"""Records conversations as MP4: the camera picture with both sides of the chat.

Video comes straight from the camera (an RTSP stream is copied without re-encoding, so it
costs almost nothing; a USB camera's preview frames are encoded). Audio is the visitor's
mic and Skelly's voice, read from PipeWire and mixed. A JSON transcript is saved next to
each video. Old recordings are pruned after the number of days chosen in Settings.

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
    """ffmpeg filter for input 1 (mic) and input 2 (his voice), giving [a].

    "clean": a copy of his voice, plus the same copy held back by the speaker's lag, works the
    mic's volume (a sidechain compressor), so the mic is down from the moment he starts to the
    moment his speaker goes quiet. Visitors talking between his lines are kept as they were.
    """
    fmt = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    if sound == "room":
        return f"[1:a]{fmt},volume=0.9,alimiter=limit=0.8[a]"
    lag_ms = round(1000 * max(0.0, min(2.0, echo_delay + MARGIN_S)))
    return (f"[1:a]{fmt},volume=0.6[m];[2:a]{fmt},asplit=3[v][k1][k2];"
            f"[k2]adelay={lag_ms}:all=1[k2d];[k1][k2d]amix=inputs=2:duration=first:normalize=0[key];"
            "[m][key]sidechaincompress=threshold=0.003:ratio=20:attack=5:release=200:level_sc=4[md];"
            "[v]volume=0.8[vv];"
            # Mic and voice summed, then a limiter: plain mixing clipped in the first test.
            "[md][vv]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.8[a]")


def rec_dir() -> Path:
    d = data_dir() / "recordings"
    d.mkdir(parents=True, exist_ok=True)
    return d


class Recorder:
    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._feeder: asyncio.Task | None = None
        self.path: Path | None = None
        self.started = 0.0

    @property
    def recording(self) -> bool:
        return bool(self._proc and self._proc.returncode is None)

    async def start(self, *, rtsp: str | None, frames=None, mic: str | None, voice_sink: str | None,
                    sound: str = "clean", echo_delay: float = 0.5) -> Path | None:
        """Begin recording. `frames` is an async iterator of JPEGs when there is no RTSP stream.

        `echo_delay` is how long his voice takes to reach the mic through his speaker, in seconds.
        """
        if self.recording:
            return self.path
        name = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.path = rec_dir() / f"{name}.mp4"
        args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
        if rtsp:
            args += ["-rtsp_transport", "tcp", "-use_wallclock_as_timestamps", "1", "-i", rtsp]
        elif frames is not None:
            args += ["-f", "image2pipe", "-framerate", "6", "-use_wallclock_as_timestamps", "1", "-i", "pipe:0"]
        else:
            return None
        audio_inputs = 0
        for src in (mic or "default", f"{voice_sink}.monitor" if voice_sink else None):
            if src:
                args += ["-f", "pulse", "-i", src]
                audio_inputs += 1
        maps = ["-map", "0:v:0"]
        if audio_inputs == 2:
            args += ["-filter_complex", audio_filter(sound, echo_delay)]
            maps += ["-map", "[a]"]
        elif audio_inputs == 1:
            maps += ["-map", "1:a"]
        vcodec = ["-c:v", "copy"] if rtsp else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                                                "-pix_fmt", "yuv420p"]
        args += [*maps, *vcodec, "-c:a", "aac", "-b:a", "96k", "-movflags", "+frag_keyframe+empty_moov",
                 str(self.path)]
        self._proc = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.PIPE if not rtsp else asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE)
        self.started = time.time()
        if not rtsp:
            self._feeder = asyncio.create_task(self._feed(frames))
        log.info("recording to %s", self.path.name)
        return self.path

    async def _feed(self, frames) -> None:
        try:
            async for jpg in frames:
                if not self._proc or not self._proc.stdin:
                    return
                self._proc.stdin.write(jpg)
                await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass

    async def stop(self, transcript: list[dict] | None = None) -> dict | None:
        if not self._proc:
            return None
        proc, self._proc = self._proc, None
        if self._feeder:
            self._feeder.cancel()
        try:
            if proc.stdin:
                proc.stdin.close()
            if proc.returncode is None:
                # "q" isn't available without a tty; SIGINT makes ffmpeg finish the file cleanly.
                proc.send_signal(2)
            await asyncio.wait_for(proc.wait(), 10)
        except (TimeoutError, ProcessLookupError):
            proc.kill()
        err = (await proc.stderr.read()).decode(errors="replace").strip() if proc.stderr else ""
        if err:
            log.info("recorder: %s", err.splitlines()[-1])
        path = self.path
        if not path or not path.exists() or path.stat().st_size < 10_000:
            if path and path.exists():
                path.unlink()
            return None
        meta = {"started": self.started, "seconds": round(time.time() - self.started), "transcript": transcript or []}
        path.with_suffix(".json").write_text(json.dumps(meta))
        await asyncio.to_thread(finalize, path)
        await asyncio.to_thread(thumbnail, path)
        return info(path)


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
