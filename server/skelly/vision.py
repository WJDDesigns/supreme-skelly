"""Camera for Skelly: a USB webcam or any RTSP/RTSPS stream (UniFi Protect included).

One ffmpeg process reads the camera and writes two things at once: small JPEGs for the
live preview, and a tiny grey thumbnail used to measure motion. When motion holds for a
moment, it counts as someone walking up; optionally an AI looks at the frame to confirm
it's a person and describe them, and a conversation can start on its own.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

THUMB_W, THUMB_H = 80, 45


@dataclass
class VisionConfig:
    source: str = "usb"  # usb | rtsp
    usb_device: str = "/dev/video0"
    fps: int = 6
    rotate: int = 0  # 0 | 90 | 180 | 270
    sensitivity: int = 50  # 0..100, higher triggers on smaller movement
    start_on_boot: bool = False
    ai_check: bool = True  # ask an AI whether the motion is a person, and what they look like
    auto_converse: bool = False  # start a conversation when a visitor is confirmed
    cooldown_s: int = 90  # minimum gap between visitor events
    # Areas to ignore, per camera ("rtsp" or the USB device path): [[x, y, w, h], ...] as
    # fractions of the picture. Skelly himself goes here so his moving doesn't count.
    zones: dict = field(default_factory=dict)

    @property
    def camera_key(self) -> str:
        return "rtsp" if self.source == "rtsp" else self.usb_device

    @property
    def active_zones(self) -> list[list[float]]:
        return [z for z in self.zones.get(self.camera_key, []) if len(z) == 4]

    @classmethod
    def from_dict(cls, d: dict | None) -> VisionConfig:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in (d or {}).items() if k in known})


@dataclass
class VisionState:
    running: bool = False
    error: str | None = None
    motion: float = 0.0
    visitor: bool = False
    last_visitor_at: float | None = None
    description: str | None = None
    fps: float = 0.0


def list_cameras() -> list[dict]:
    out = []
    for dev in sorted(Path("/sys/class/video4linux").glob("video*")):
        try:
            name = (dev / "name").read_text().strip()
            index = int((dev / "index").read_text().strip() or 0)
        except OSError:
            continue
        if index == 0:  # each webcam also exposes metadata-only nodes; index 0 is the picture
            out.append({"device": f"/dev/{dev.name}", "label": name})
    return out


class Vision:
    def __init__(self, svc, vault, config_getter, on_visitor) -> None:
        self.svc = svc
        self.vault = vault
        self._config = config_getter
        self._on_visitor = on_visitor
        self.state = VisionState()
        self.frame: bytes | None = None
        self._frame_event = asyncio.Event()
        self._task: asyncio.Task | None = None

    # -- control ----------------------------------------------------------------

    def snapshot(self) -> dict:
        return asdict(self.state)

    async def start(self) -> dict:
        if self._task and not self._task.done():
            return self.snapshot()
        cfg = VisionConfig.from_dict(self._config())
        if cfg.source == "rtsp" and not self.vault.get("rtsp_url"):
            raise ValueError("Add the camera's RTSP address in Settings > API keys first.")
        self.state = VisionState(running=True)
        self._publish()
        self._task = asyncio.create_task(self._run(cfg), name="skelly-vision")
        return self.snapshot()

    async def stop(self) -> dict:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self.state.running = False
        self.state.motion = 0.0
        self._publish()
        return self.snapshot()

    async def frames(self):
        """Latest JPEG frames as they arrive (for the MJPEG preview stream)."""
        while True:
            self._frame_event.clear()
            await self._frame_event.wait()
            if self.frame:
                yield self.frame

    def _publish(self) -> None:
        self.svc.bus.publish("vision", self.snapshot())

    # -- capture ----------------------------------------------------------------

    def _ffmpeg_args(self, cfg: VisionConfig, thumb_fd: int) -> list[str]:
        if cfg.source == "rtsp":
            src = ["-rtsp_transport", "tcp", "-i", self.vault.get("rtsp_url")]
        else:
            src = ["-f", "v4l2", "-i", cfg.usb_device]
        rot = {90: "transpose=1,", 180: "transpose=1,transpose=1,", 270: "transpose=2,"}.get(cfg.rotate, "")
        fps = max(1, min(cfg.fps, 15))
        return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-fflags", "nobuffer", *src,
                "-filter_complex", f"[0:v]fps={fps},{rot}split=2[a][b];[a]scale=640:-2[preview];"
                                   f"[b]scale={THUMB_W}:{THUMB_H},format=gray[thumb]",
                "-map", "[preview]", "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "7", "pipe:1",
                "-map", "[thumb]", "-f", "rawvideo", f"pipe:{thumb_fd}"]

    async def _run(self, cfg: VisionConfig) -> None:
        backoff = 2
        while True:
            try:
                await self._capture(cfg)
                backoff = 2
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.state.error = str(exc)
                self._publish()
                log.info("camera stopped: %s; retrying in %ss", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    async def _capture(self, cfg: VisionConfig) -> None:
        rfd, wfd = os.pipe()
        proc = await asyncio.create_subprocess_exec(
            *self._ffmpeg_args(cfg, wfd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            pass_fds=(wfd,))
        os.close(wfd)
        loop = asyncio.get_running_loop()
        thumbs = asyncio.StreamReader()
        transport, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(thumbs), os.fdopen(rfd, "rb"))
        jpeg_task = asyncio.create_task(self._read_jpegs(proc.stdout))
        motion_task = asyncio.create_task(self._read_motion(cfg, thumbs))
        try:
            done, _ = await asyncio.wait([jpeg_task, motion_task, asyncio.create_task(proc.wait())],
                                         return_when=asyncio.FIRST_COMPLETED)
            err = (await proc.stderr.read()).decode(errors="replace").strip() if proc.stderr else ""
            for t in done:
                if t.exception():
                    raise t.exception()
            raise ConnectionError(err.splitlines()[-1] if err else "The camera stream ended")
        finally:
            jpeg_task.cancel()
            motion_task.cancel()
            transport.close()
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    async def _read_jpegs(self, stream: asyncio.StreamReader) -> None:
        buf = b""
        count, since = 0, time.monotonic()
        while chunk := await stream.read(65536):
            buf += chunk
            while (end := buf.find(b"\xff\xd9")) != -1:
                start = buf.find(b"\xff\xd8")
                if 0 <= start < end:
                    self.frame = buf[start:end + 2]
                    self._frame_event.set()
                    if self.state.error:
                        self.state.error = None
                        self._publish()
                    count += 1
                buf = buf[end + 2:]
            if (now := time.monotonic()) - since > 5:
                self.state.fps = round(count / (now - since), 1)
                count, since = 0, now

    async def _read_motion(self, cfg: VisionConfig, stream: asyncio.StreamReader) -> None:
        size = THUMB_W * THUMB_H
        keep = _mask(cfg.active_zones)
        counted = max(1, len(keep))
        prev: bytes | None = None
        busy_frames, last_pub = 0, 0.0
        # Sensitivity 0..100 maps to the share of the picture that must change.
        threshold = 0.12 - (cfg.sensitivity / 100) * 0.105
        while True:
            thumb = await stream.readexactly(size)
            if prev is not None:
                changed = sum(1 for i in keep if abs(thumb[i] - prev[i]) > 24) / counted
                self.state.motion = round(changed, 3)
                busy_frames = busy_frames + 1 if changed > threshold else 0
                if busy_frames >= max(2, cfg.fps // 2):  # half a second of movement
                    busy_frames = 0
                    asyncio.create_task(self._maybe_visitor(cfg))
                now = time.monotonic()
                if now - last_pub > 0.5:
                    last_pub = now
                    self.svc.bus.publish("vision_motion", {"motion": self.state.motion, "fps": self.state.fps})
            prev = thumb

    async def _maybe_visitor(self, cfg: VisionConfig) -> None:
        now = time.time()
        if self.state.last_visitor_at and now - self.state.last_visitor_at < cfg.cooldown_s:
            return
        self.state.last_visitor_at = now  # claim the slot before the (slow) AI check
        description = None
        if cfg.ai_check and self.frame:
            try:
                verdict = await describe(self.vault, self.frame, cfg.active_zones)
            except Exception as exc:
                log.info("visitor check failed: %r", exc)
                verdict = None
            if verdict is not None:
                if not verdict.get("people"):
                    self.state.last_visitor_at = None  # just leaves blowing about; stay ready
                    return
                description = verdict.get("description")
        self.state.visitor = True
        self.state.description = description
        self._publish()
        await self._on_visitor(description, cfg)
        await asyncio.sleep(10)
        self.state.visitor = False
        self._publish()


def _mask(zones: list[list[float]]) -> list[int]:
    """Thumbnail pixels outside every ignored zone."""
    out = []
    for i in range(THUMB_W * THUMB_H):
        x, y = (i % THUMB_W + 0.5) / THUMB_W, (i // THUMB_W + 0.5) / THUMB_H
        if not any(zx <= x <= zx + zw and zy <= y <= zy + zh for zx, zy, zw, zh in zones):
            out.append(i)
    return out


async def blackout(jpeg: bytes, zones: list[list[float]]) -> bytes:
    """Paint ignored zones black so an AI looking at the frame can't count what's in them."""
    if not zones:
        return jpeg
    boxes = ",".join(f"drawbox=x=iw*{x:.4f}:y=ih*{y:.4f}:w=iw*{w:.4f}:h=ih*{h:.4f}:color=black:t=fill"
                     for x, y, w, h in zones)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "image2pipe", "-i", "pipe:0", "-vf", boxes,
        "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "4", "pipe:1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await proc.communicate(jpeg)
    return out or jpeg


SEE_PROMPT = (
    "You are the eyes of a talking Halloween skeleton. Look at this camera frame. Skeletons, statues, "
    "inflatables and other Halloween decorations are props, not people; black areas are hidden on purpose. "
    'Reply with JSON only: {"people": <number of real people>, "description": "<one short sentence about '
    'them a skeleton could joke about: costumes, clothes colours, pets, what they hold>"}.'
)

FIND_PROMPT = (
    "This camera frame shows a life-size Halloween skeleton decoration (an animatronic prop). "
    "Give the box around the whole skeleton, including head, arms and feet, as fractions of the image width "
    'and height. Reply with JSON only: {"found": true, "x": <left>, "y": <top>, "w": <width>, "h": <height>} '
    'or {"found": false} if there is no skeleton.'
)


async def find_skelly(vault, jpeg: bytes) -> list[float] | None:
    """Where Skelly stands in the picture, padded a little, as [x, y, w, h] fractions."""
    r = await _ask_vision(vault, jpeg, FIND_PROMPT)
    if not r or not r.get("found"):
        return None
    x, y, w, h = (float(r[k]) for k in ("x", "y", "w", "h"))
    if max(x, y, w, h) > 1.5:  # some models answer in percent
        x, y, w, h = x / 100, y / 100, w / 100, h / 100
    pad_w, pad_h = w * 0.15, h * 0.08
    x, y = max(0.0, x - pad_w), max(0.0, y - pad_h)
    return [round(x, 4), round(y, 4), round(min(1 - x, w + 2 * pad_w), 4), round(min(1 - y, h + 2 * pad_h), 4)]


async def describe(vault, jpeg: bytes, zones: list[list[float]] | None = None) -> dict | None:
    """Ask Claude (or OpenAI) whether people are in the frame and what they look like."""
    return await _ask_vision(vault, await blackout(jpeg, zones or []), SEE_PROMPT)


async def _ask_vision(vault, jpeg: bytes, prompt: str) -> dict | None:
    b64 = base64.b64encode(jpeg).decode()
    async with httpx.AsyncClient(timeout=20) as http:
        if key := vault.get("anthropic_api_key"):
            r = await http.post("https://api.anthropic.com/v1/messages", headers={
                "x-api-key": key, "anthropic-version": "2023-06-01"}, json={
                "model": "claude-haiku-4-5-20251001", "max_tokens": 150, "messages": [{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": prompt}]}]})
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", []))
        elif key := vault.get("openai_api_key"):
            r = await http.post("https://api.openai.com/v1/chat/completions", headers={
                "Authorization": f"Bearer {key}"}, json={
                "model": "gpt-4o-mini", "max_tokens": 150, "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": prompt}]}]})
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
        else:
            return None
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1]) if start != -1 else None
