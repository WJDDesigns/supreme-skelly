"""Records conversations as MP4: the camera picture with both sides of the chat.

Video comes straight from the camera (an RTSP stream is copied without re-encoding, so it
costs almost nothing; a USB camera's preview frames are encoded). Audio is the visitor's
mic and Skelly's voice, read from PipeWire and mixed. A JSON transcript is saved next to
each video. Old recordings are pruned after the number of days chosen in Settings.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

from .settings import data_dir

log = logging.getLogger(__name__)


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

    async def start(self, *, rtsp: str | None, frames=None, mic: str | None, voice_sink: str | None) -> Path | None:
        """Begin recording. `frames` is an async iterator of JPEGs when there is no RTSP stream."""
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
            args += ["-filter_complex", "[1:a][2:a]amix=inputs=2:duration=longest:normalize=0[a]"]
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
        return info(path)


def info(path: Path) -> dict:
    meta = {}
    try:
        meta = json.loads(path.with_suffix(".json").read_text())
    except (OSError, ValueError):
        pass
    t = meta.get("transcript") or []
    return {"name": path.name, "size": path.stat().st_size, "started": meta.get("started", path.stat().st_mtime),
            "seconds": meta.get("seconds"), "lines": len(t),
            "preview": next((x["text"] for x in t if x.get("role") == "user"), "")[:120]}


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
