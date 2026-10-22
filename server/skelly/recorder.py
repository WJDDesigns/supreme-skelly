"""Records conversations as MP4: the camera picture with both sides of the chat.

Video comes straight from the camera (an RTSP stream is copied without re-encoding, so it
costs almost nothing; a USB camera's preview frames are encoded). Audio is the visitor's
mic and Skelly's voice, read from PipeWire and mixed. A JSON transcript is saved next to
each video. Old recordings are pruned after the number of days chosen in Settings.

Picture and sound are recorded by two separate ffmpeg processes and joined when the chat
ends. In one process, ffmpeg (7+) holds back an input that runs ahead of the others; with
the camera timed by arrival, that throttled the camera read until recordings came out in
slow motion (8 s of picture spread over 76 s, the video track ending long before the sound).

The mic stands next to Skelly, so it hears his speaker too, a moment after his voice was
sent (Bluetooth lag). Mixed with his voice taken straight from the sound card, that made
him sound doubled. In "clean" sound (the default) the mic is turned right down while he
talks and for the lag after, so his voice is heard once, crisp; "room" keeps only the mic.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from .conversation import said_something
from .settings import data_dir

log = logging.getLogger(__name__)


SOUNDS = ("clean", "room")
MARGIN_S = 0.25  # extra time the mic stays down after his voice, on top of the measured lag


def audio_filter(sound: str, echo_delay: float) -> str:
    """ffmpeg filter for input 0 (mic) and input 1 (his voice), giving [a].

    "clean": a copy of his voice, plus the same copy held back by the speaker's lag, works the
    mic's volume (a sidechain compressor), so the mic is down from the moment he starts to the
    moment his speaker goes quiet. Visitors talking between his lines are kept as they were.
    """
    fmt = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    if sound == "room":
        return f"[0:a]{fmt},volume=0.9,alimiter=limit=0.8[a]"
    lag_ms = round(1000 * max(0.0, min(2.0, echo_delay + MARGIN_S)))
    return (f"[0:a]{fmt},volume=0.6[m];[1:a]{fmt},asplit=3[v][k1][k2];"
            f"[k2]adelay={lag_ms}:all=1[k2d];[k1][k2d]amix=inputs=2:duration=first:normalize=0[key];"
            "[m][key]sidechaincompress=threshold=0.003:ratio=20:attack=5:release=200:level_sc=4[md];"
            "[v]volume=0.8[vv];"
            # Mic and voice summed, then a limiter: plain mixing clipped in the first test.
            "[md][vv]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.8[a]")


def rec_dir() -> Path:
    d = data_dir() / "recordings"
    d.mkdir(parents=True, exist_ok=True)
    return d


class _Part:
    """One ffmpeg process writing one track, and when its first moment happened."""

    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self.proc = proc
        self.t0: float | None = None  # wall time of the track's 0 s
        self._watch = asyncio.create_task(self._progress())

    async def _progress(self) -> None:
        """ffmpeg's -progress says how far the track has got; now minus that is when it began.

        Delays only ever make that later, so the earliest estimate is the best one.
        """
        out = 0
        try:
            while line := await self.proc.stdout.readline():
                key, _, value = line.decode(errors="replace").strip().partition("=")
                if key == "out_time_us" and value.lstrip("-").isdigit():
                    out = int(value)
                elif key == "progress" and out > 0:
                    t0 = time.time() - out / 1e6
                    self.t0 = t0 if self.t0 is None else min(self.t0, t0)
        except (OSError, ValueError):
            pass

    async def stop(self) -> str:
        proc = self.proc
        try:
            if proc.stdin:
                proc.stdin.close()
            if proc.returncode is None:
                # "q" isn't available without a tty; SIGINT makes ffmpeg finish the file cleanly.
                proc.send_signal(2)
            await asyncio.wait_for(proc.wait(), 10)
        except (TimeoutError, ProcessLookupError):
            proc.kill()
            await proc.wait()
        try:
            await asyncio.wait_for(self._watch, 2)
        except TimeoutError:
            self._watch.cancel()
        return (await proc.stderr.read()).decode(errors="replace").strip() if proc.stderr else ""


async def _spawn(args: list[str], stdin=False) -> _Part:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1", "-stats_period", "0.2",
        "-y", *args, stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    return _Part(proc)


def video_part(path: Path) -> Path:
    return path.with_suffix(".video")


def audio_part(path: Path) -> Path:
    return path.with_suffix(".audio")


class Recorder:
    def __init__(self) -> None:
        self._video: _Part | None = None
        self._audio: _Part | None = None
        self._feeder: asyncio.Task | None = None
        self.path: Path | None = None
        self.started = 0.0

    @property
    def recording(self) -> bool:
        return bool(self._video and self._video.proc.returncode is None)

    async def start(self, *, rtsp: str | None, frames=None, mic: str | None, voice_sink: str | None,
                    sound: str = "clean", echo_delay: float = 0.5) -> Path | None:
        """Begin recording. `frames` is an async iterator of JPEGs when there is no RTSP stream.

        `echo_delay` is how long his voice takes to reach the mic through his speaker, in seconds.
        """
        if self.recording:
            return self.path
        if rtsp:
            # The camera's own timestamps: frames that arrive in bunches over Wi-Fi still play evenly.
            vin = ["-rtsp_transport", "tcp", "-i", rtsp]
            vcodec = ["-c:v", "copy"]
        elif frames is not None:
            vin = ["-f", "image2pipe", "-framerate", "6", "-use_wallclock_as_timestamps", "1", "-i", "pipe:0"]
            vcodec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p"]
        else:
            return None
        name = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.path = rec_dir() / f"{name}.mp4"
        # Fragmented, so a crash or power cut still leaves a playable picture.
        self._video = await _spawn([*vin, "-map", "0:v:0", *vcodec, "-an", "-movflags", "+frag_keyframe+empty_moov",
                                    "-f", "mp4", str(video_part(self.path))], stdin=not rtsp)
        ain: list[str] = []
        for src in (mic or "default", f"{voice_sink}.monitor" if voice_sink else None):
            if src:
                ain += ["-f", "pulse", "-i", src]
        amap = ["-filter_complex", audio_filter(sound, echo_delay), "-map", "[a]"] if len(ain) > 4 else ["-map", "0:a"]
        self._audio = await _spawn([*ain, *amap, "-c:a", "aac", "-b:a", "96k", "-f", "adts",
                                    str(audio_part(self.path))])
        self.started = time.time()
        if not rtsp:
            self._feeder = asyncio.create_task(self._feed(frames))
        log.info("recording to %s", self.path.name)
        return self.path

    async def _feed(self, frames) -> None:
        try:
            async for jpg in frames:
                if not self._video or not self._video.proc.stdin:
                    return
                self._video.proc.stdin.write(jpg)
                await self._video.proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass

    async def stop(self, transcript: list[dict] | None = None) -> dict | None:
        if not self._video:
            return None
        video, self._video = self._video, None
        audio, self._audio = self._audio, None
        if self._feeder:
            self._feeder.cancel()
            self._feeder = None
        errs = await asyncio.gather(video.stop(), audio.stop() if audio else asyncio.sleep(0, ""))
        for err in errs:
            if err:
                log.info("recorder: %s", err.splitlines()[-1])
        path = self.path
        if not path:
            return None
        lag = (audio.t0 - video.t0) if audio and audio.t0 is not None and video.t0 is not None else 0.0
        if not await asyncio.to_thread(join, path, lag):
            return None
        meta = {"started": self.started, "seconds": round(time.time() - self.started), "transcript": transcript or [],
                "faststart": True}
        path.with_suffix(".json").write_text(json.dumps(meta))
        await asyncio.to_thread(thumbnail, path)
        return info(path)


def join(path: Path, lag: float) -> bool:
    """Put the picture and the sound together in one MP4, index at the front, nothing re-encoded.

    `lag` is how many seconds after the picture the sound began (negative: it began before,
    usually, as the camera takes a moment to connect), so the two line up.
    """
    video, audio = video_part(path), audio_part(path)
    try:
        if not video.exists() or video.stat().st_size < 10_000:
            return False
        args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video)]
        has_audio = audio.exists() and audio.stat().st_size > 0
        if has_audio:
            lag = max(-30.0, min(30.0, lag))
            shift = ["-ss", f"{-lag:.3f}"] if lag < 0 else ["-itsoffset", f"{lag:.3f}"]
            args += [*shift, "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0"]
        args += ["-c", "copy", "-movflags", "+faststart", "-f", "mp4", str(path)]
        try:
            done = subprocess.run(args, capture_output=True, timeout=300, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.info("couldn't join %s: %r", path.name, exc)
            return False
        if done.returncode or not path.exists() or path.stat().st_size < 10_000:
            log.info("couldn't join %s: %s", path.name, done.stderr.decode(errors="replace")[-300:])
            path.unlink(missing_ok=True)
            return False
        return True
    finally:
        video.unlink(missing_ok=True)
        audio.unlink(missing_ok=True)


def info(path: Path) -> dict:
    meta = {}
    try:
        meta = json.loads(path.with_suffix(".json").read_text())
    except (OSError, ValueError):
        pass
    t = meta.get("transcript") or []
    heard = [x["text"] for x in t if x.get("role") == "user" and said_something(x.get("text", ""))]
    return {"name": path.name, "size": path.stat().st_size, "started": meta.get("started", path.stat().st_mtime),
            "seconds": meta.get("seconds"), "lines": len(t), "replies": len(heard), "kind": kind(len(heard)),
            "preview": (heard[0] if heard else "")[:120]}


CHAT_REPLIES = 2  # visitor spoke at least this often: a real back-and-forth, not one "hi"


def kind(replies: int) -> str:
    """How much the visitor joined in: "chat", "brief" (said one thing) or "ignored" (said nothing)."""
    return "chat" if replies >= CHAT_REPLIES else "brief" if replies else "ignored"


_finalizing = threading.Lock()


def finalize(path: Path) -> None:
    """Rewrite a finished recording as a plain MP4 with its index at the front.

    While recording, the MP4 is written in fragments so a crash or power cut still leaves a
    playable file. Safari (iPhone, iPad, Mac) stalls a few seconds into a fragmented file
    though, so once it's finished it is copied, without re-encoding, into a normal MP4.
    Recordings made before this are done the first time they're played.
    """
    meta_path = path.with_suffix(".json")
    with _finalizing:
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, ValueError):
            meta = {}
        if meta.get("faststart") or not path.exists():
            return
        part = path.with_suffix(".part")
        try:
            done = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path), "-map", "0",
                                   "-c", "copy", "-movflags", "+faststart", "-f", "mp4", str(part)],
                                  capture_output=True, timeout=300, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.info("couldn't tidy %s for playback: %r", path.name, exc)
            part.unlink(missing_ok=True)
            return
        if done.returncode or not part.exists() or part.stat().st_size < path.stat().st_size // 2:
            log.info("couldn't tidy %s for playback: %s", path.name, done.stderr.decode(errors="replace")[-300:])
            part.unlink(missing_ok=True)
            return
        st = path.stat()
        os.utime(part, (st.st_atime, st.st_mtime))  # its age decides when it's pruned
        os.replace(part, path)
        meta_path.write_text(json.dumps({**meta, "faststart": True}))


THUMB_AT_S = 2  # a moment in, once the visitor is in view and the picture has settled


def thumbnail(path: Path) -> Path | None:
    """A small picture from the recording for the list, made once and kept beside the video."""
    thumb = path.with_suffix(".jpg")
    if thumb.exists():
        return thumb
    for at in (THUMB_AT_S, 0):  # a recording shorter than THUMB_AT_S has no frame there
        try:
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(at), "-i", str(path),
                            "-frames:v", "1", "-vf", "scale=320:-2", "-q:v", "5", str(thumb)],
                           capture_output=True, timeout=20, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.info("thumbnail for %s failed: %r", path.name, exc)
            return None
        if thumb.exists() and thumb.stat().st_size:
            return thumb
    thumb.unlink(missing_ok=True)
    return None


def recordings() -> list[dict]:
    return sorted((info(p) for p in rec_dir().glob("*.mp4")), key=lambda r: -r["started"])


SAFE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.mp4$")


def recording_path(name: str) -> Path | None:
    if not SAFE.match(name):
        return None
    p = rec_dir() / name
    return p if p.exists() else None


def delete(name: str) -> bool:
    p = recording_path(name)
    if not p:
        return False
    p.unlink()
    p.with_suffix(".json").unlink(missing_ok=True)
    p.with_suffix(".jpg").unlink(missing_ok=True)
    p.with_suffix(".part").unlink(missing_ok=True)
    return True


def recover() -> int:
    """Join the pieces of recordings cut off by a crash or restart, so they show up in the list."""
    joined = 0
    for video in rec_dir().glob("*.video"):
        path, began = video.with_suffix(".mp4"), video.stat().st_mtime
        if SAFE.match(path.name) and not path.exists() and join(path, 0.0):
            meta_path = path.with_suffix(".json")
            if not meta_path.exists():
                meta_path.write_text(json.dumps({"started": began, "transcript": [], "faststart": True}))
            joined += 1
        video.unlink(missing_ok=True)
        video.with_suffix(".audio").unlink(missing_ok=True)
    return joined


def prune(keep_days: int) -> int:
    """Delete recordings older than keep_days, and the oldest ones if the disk is getting full."""
    removed = 0
    cutoff = time.time() - keep_days * 86400
    items = sorted(rec_dir().glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    for p in items:
        too_old = keep_days > 0 and p.stat().st_mtime < cutoff
        disk_full = shutil.disk_usage(p.parent).free < 2 * 1024**3
        if too_old or disk_full:
            delete(p.name)
            removed += 1
    return removed
