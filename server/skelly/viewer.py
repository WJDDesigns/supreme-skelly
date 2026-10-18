"""The Viewer page: live camera picture plus both sides of the conversation, as they happen.

One WebSocket carries everything, so it works the same on a phone as on a laptop and the
picture and the voices arrive together. Each binary message starts with one byte saying
what it is (a JPEG frame, a slice of mic sound, or a slice of what Skelly's speaker plays);
text messages are JSON status updates. Nothing runs until someone opens the page: the
picture starts with the first viewer and the recorders with the first one who presses
Listen, and all of it stops when the last one leaves.

The picture is the camera shown on the Vision page. A USB or RTSP camera that Vision is
already reading is shared (no extra work); a UniFi Protect camera gets its own smooth
stream here, since Vision only takes a snapshot from it every couple of seconds.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

from .audio_io import scale
from .vision import VisionConfig

log = logging.getLogger(__name__)

VIDEO, MIC, SPEAKER = 1, 2, 3
RATE = 24000  # every browser's Web Audio takes this rate
CHUNK = RATE * 2 * 40 // 1000  # 40 ms of 16-bit mono
FPS = 10
WIDTH = 960
QUEUE = 80  # about a second and a half of messages per viewer; a slower viewer skips some


@dataclass(eq=False)  # each viewer is its own, kept in a set
class Watcher:
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=QUEUE))
    audio: bool = False
    video: bool = True
    dropped: int = 0

    def put(self, msg: bytes | str) -> None:
        try:
            self.queue.put_nowait(msg)
        except asyncio.QueueFull:
            self.dropped += 1


def split_jpegs(buf: bytes) -> tuple[list[bytes], bytes]:
    """Whole JPEGs found in an MJPEG byte stream, and what's left over for the next read."""
    out = []
    while (end := buf.find(b"\xff\xd9")) != -1:
        start = buf.find(b"\xff\xd8")
        if 0 <= start < end:
            out.append(buf[start:end + 2])
        buf = buf[end + 2:]
    return out, buf


def ffmpeg_args(src: list[str], rotate: int = 0) -> list[str]:
    rot = {90: "transpose=1,", 180: "transpose=1,transpose=1,", 270: "transpose=2,"}.get(rotate, "")
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-fflags", "nobuffer", "-flags", "low_delay",
            *src, "-an", "-vf", f"fps={FPS},{rot}scale='min({WIDTH},iw)':-2",
            "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "7", "pipe:1"]


class Viewer:
    def __init__(self, vision, protect, vault, config_getter, mic_getter, mic_gain, speaker_getter) -> None:
        self.vision = vision
        self.protect = protect
        self.vault = vault
        self._config = config_getter  # -> settings dict for VisionConfig
        self._mic = mic_getter  # -> PipeWire source name, "" for the default mic
        self._mic_gain = mic_gain  # -> gain factor, so you hear what the AI hears
        self._speaker = speaker_getter  # async -> sink Skelly's voice plays on, or None
        self.watchers: set[Watcher] = set()
        self.status = {"video": "off", "video_note": "", "mic": False, "speaker": False}
        self._tasks: dict[str, asyncio.Task] = {}

    # -- viewers ----------------------------------------------------------------

    def join(self) -> Watcher:
        w = Watcher()
        self.watchers.add(w)
        w.put(json.dumps({"type": "status", **self.status}))
        self._sync()
        return w

    def leave(self, w: Watcher) -> None:
        self.watchers.discard(w)
        self._sync()

    def want(self, w: Watcher, listen: bool, video: bool = True) -> None:
        """What this viewer wants sent: the sound (after they press Listen) and the picture
        (not while they've gone to another page and only listen)."""
        w.audio, w.video = bool(listen), bool(video)
        self._sync()

    async def stop(self) -> None:
        self.watchers.clear()
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _sync(self) -> None:
        """Run the picture while anyone watches, and the recorders while anyone listens."""
        listening = any(w.audio for w in self.watchers)
        want = {"video": any(w.video for w in self.watchers), "mic": listening, "speaker": listening}
        for name, on in want.items():
            task = self._tasks.get(name)
            if on and (task is None or task.done()):
                runner = self._video() if name == "video" else self._audio(name)
                self._tasks[name] = asyncio.create_task(runner, name=f"skelly-viewer-{name}")
            elif not on and task is not None:
                task.cancel()
                del self._tasks[name]

    def _send(self, kind: int, payload: bytes) -> None:
        msg = bytes([kind]) + payload
        for w in self.watchers:
            if w.video if kind == VIDEO else w.audio:
                w.put(msg)

    def _set(self, **changes) -> None:
        if all(self.status.get(k) == v for k, v in changes.items()):
            return
        self.status.update(changes)
        msg = json.dumps({"type": "status", **self.status})
        for w in self.watchers:
            w.put(msg)

    # -- picture ----------------------------------------------------------------

    async def _video(self) -> None:
        backoff = 2
        try:
            while True:
                cfg = VisionConfig.from_dict(self._config())
                try:
                    if self.vision.state.running and cfg.source != "protect":
                        await self._shared(cfg)  # returns when Vision stops
                        continue
                    src = await self._source(cfg)
                    if src is None:
                        await asyncio.sleep(3)
                        continue
                    await self._ffmpeg(src, cfg.rotate)
                    backoff = 2
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.info("viewer picture stopped: %s", exc)
                    self._set(video="error", video_note=str(exc)[:200])
                    if cfg.source == "protect" and self.protect and cfg.preview_camera:
                        await self._protect_snapshots(cfg, backoff * 2)
                    else:
                        await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30)
        finally:
            self._set(video="off", video_note="")

    async def _source(self, cfg: VisionConfig) -> list[str] | None:
        if cfg.source == "protect":
            if not (self.protect and cfg.protect_host and cfg.preview_camera and self.vault.get("protect_api_key")):
                self._set(video="unset", video_note="Set up UniFi Protect on the Vision page first.")
                return None
            url = await self.protect.rtsps(cfg.preview_camera, "medium")
            if not url:
                raise ConnectionError("Protect didn't hand out a live stream for this camera")
            return ["-rtsp_transport", "tcp", "-i", url]
        if cfg.source == "rtsp":
            if not self.vault.get("rtsp_url"):
                self._set(video="unset", video_note="Add the camera's RTSP address in Settings > API keys first.")
                return None
            return ["-rtsp_transport", "tcp", "-i", self.vault.get("rtsp_url")]
        # A USB camera can only be opened once, so the Viewer never takes it away from Vision
        self._set(video="unset", video_note="Start the camera on the Vision page to see it here.")
        return None

    async def _shared(self, cfg: VisionConfig) -> None:
        """Vision already reads this camera: pass its preview frames on."""
        self._set(video="live", video_note="")
        event = self.vision._frame_event
        while self.vision.state.running:
            try:
                await asyncio.wait_for(event.wait(), 5)
            except TimeoutError:
                continue
            event.clear()
            if self.vision.frame:
                self._send(VIDEO, self.vision.frame)

    async def _ffmpeg(self, src: list[str], rotate: int) -> None:
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_args(src, rotate), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            buf = b""
            assert proc.stdout
            while chunk := await proc.stdout.read(65536):
                frames, buf = split_jpegs(buf + chunk)
                for jpg in frames:
                    self._set(video="live", video_note="")
                    self._send(VIDEO, jpg)
                if len(buf) > 4 * 1024 * 1024:
                    buf = b""
            err = (await proc.stderr.read()).decode(errors="replace").strip() if proc.stderr else ""
            raise ConnectionError(err.splitlines()[-1] if err else "The camera stream ended")
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    async def _protect_snapshots(self, cfg: VisionConfig, seconds: float) -> None:
        """While the live stream is down, keep the picture going with a snapshot a second."""
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            try:
                self._send(VIDEO, await self.protect.snapshot(cfg.preview_camera, high=False))
                self._set(video="slow", video_note="Live stream unavailable, showing a picture a second.")
            except Exception:
                pass
            await asyncio.sleep(1)

    # -- sound ------------------------------------------------------------------

    async def _audio(self, which: str) -> None:
        kind = MIC if which == "mic" else SPEAKER
        proc, device = None, ...
        try:
            while True:
                if which == "mic":
                    want, ok = self._mic() or None, True
                else:
                    sink = await self._speaker()
                    want, ok = (f"{sink}.monitor" if sink else None), bool(sink)
                if not ok:
                    await _close(proc)
                    proc, device = None, ...
                    self._set(**{which: False})
                    await asyncio.sleep(2)
                    continue
                if proc is None or want != device or proc.returncode is not None:
                    await _close(proc)
                    proc, device = await _record(want), want
                    if proc is None:
                        self._set(**{which: False})
                        await asyncio.sleep(5)
                        continue
                # Read for a couple of seconds, then check the source hasn't moved (a chat started
                # on a combined output, a different mic was picked)
                until = time.monotonic() + 2
                while time.monotonic() < until:
                    try:
                        pcm = await asyncio.wait_for(proc.stdout.readexactly(CHUNK), 1)
                    except (asyncio.IncompleteReadError, TimeoutError):
                        # The recorder stopped or stalled (mic unplugged, speaker gone): start
                        # a fresh one after a breath, never spin on a dead one
                        await _close(proc)
                        proc = None
                        self._set(**{which: False})
                        await asyncio.sleep(1)
                        break
                    self._set(**{which: True})
                    self._send(kind, scale(pcm, self._mic_gain()) if kind == MIC else pcm)
        finally:
            await _close(proc)
            self._set(**{which: False})


async def _record(device: str | None):
    args = ["parec", "--raw", "--format=s16le", f"--rate={RATE}", "--channels=1", "--latency-msec=40",
            "--client-name=Supreme Skelly viewer"]
    if device:
        args.append(f"--device={device}")
    try:
        return await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL)
    except (FileNotFoundError, OSError) as exc:
        log.info("viewer can't record %s: %r", device or "the default mic", exc)
        return None


async def _close(proc) -> None:
    if proc is None or proc.returncode is not None:
        return
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), 2)
    except TimeoutError:
        proc.kill()
