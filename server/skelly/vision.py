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
    source: str = "usb"  # usb | rtsp | protect (snapshots from UniFi Protect, no live stream)
    usb_device: str = "/dev/video0"
    fps: int = 6
    rotate: int = 0  # 0 | 90 | 180 | 270
    sensitivity: int = 50  # 0..100, higher triggers on smaller movement
    start_on_boot: bool = False
    ai_check: bool = True  # ask an AI whether the motion is a person, and what they look like
    auto_converse: bool = False  # start a conversation when a visitor is confirmed
    cooldown_s: int = 90  # minimum gap between visitor events
    faces: bool = False  # recognise faces and remember people who say their name
    call_over: bool = True  # call out to people walking past to come and chat
    # UniFi Protect: use its person/face detections on these cameras (sharper faces, no extra load)
    protect: bool = False
    protect_host: str = ""
    protect_cameras: list = field(default_factory=list)
    protect_preview: str = ""  # Protect camera shown on the page when the source is "protect"
    # What shouldn't set Skelly off, checked by the AI (see IGNORABLE).
    ignore: list = field(default_factory=lambda: ["vehicles", "weather", "passers"])
    # Areas to ignore, per camera ("rtsp" or the USB device path): [[x, y, w, h], ...] as
    # fractions of the picture. Skelly himself goes here so his moving doesn't count.
    zones: dict = field(default_factory=dict)

    @property
    def camera_key(self) -> str:
        if self.source == "protect":
            return f"protect:{self.preview_camera}"
        return "rtsp" if self.source == "rtsp" else self.usb_device

    @property
    def preview_camera(self) -> str:
        return self.protect_preview or (self.protect_cameras[0] if self.protect_cameras else "")

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
    costumes: list = field(default_factory=list)  # costumes spotted on the last visitor check
    fps: float = 0.0
    faces: list = field(default_factory=list)  # faces in view right now (Seen.public)


# Things the AI can tell apart when motion is spotted; any of them can be ignored.
IGNORABLE = {
    "vehicles": "Cars & trucks",
    "passers": "People walking past",
    "animals": "Animals & pets",
    "bikes": "Bikes & scooters",
    "weather": "Shadows, light & weather",
}


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
    def __init__(self, svc, vault, config_getter, on_visitor, on_known=None) -> None:
        from .faces import FaceEngine, FaceMemory

        self.svc = svc
        self.vault = vault
        self._config = config_getter
        self._on_visitor = on_visitor
        self._on_known = on_known
        self._on_passerby = None  # set by the app: async (verdict, cfg) for people walking past
        self.engine = FaceEngine()
        self.memory = FaceMemory()
        self.seen: list = []  # Seen objects in the latest face frame
        self.protect = None  # set by the app: the UniFi Protect bridge, for the "protect" source
        self.full_frame: bytes | None = None  # full-resolution snapshot (Protect source)
        self._recent: list = []  # unknown faces from the last few seconds, for naming
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
        if cfg.source == "protect" and not (self.protect and cfg.protect_host and cfg.preview_camera
                                            and self.vault.get("protect_api_key")):
            raise ValueError("Set up UniFi Protect below (key, console address and a camera) first.")
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

    def _ffmpeg_args(self, cfg: VisionConfig, thumb_fd: int, face_fd: int | None = None) -> list[str]:
        if cfg.source == "rtsp":
            src = ["-rtsp_transport", "tcp", "-i", self.vault.get("rtsp_url")]
        else:
            src = ["-f", "v4l2", "-i", cfg.usb_device]
        rot = {90: "transpose=1,", 180: "transpose=1,transpose=1,", 270: "transpose=2,"}.get(cfg.rotate, "")
        fps = max(1, min(cfg.fps, 15))
        graph = (f"[0:v]fps={fps},{rot}split={3 if face_fd else 2}[a][b]{'[c]' if face_fd else ''};"
                 f"[a]scale=640:-2[preview];[b]scale={THUMB_W}:{THUMB_H},format=gray[thumb]")
        outs = ["-map", "[preview]", "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "7", "pipe:1",
                "-map", "[thumb]", "-f", "rawvideo", f"pipe:{thumb_fd}"]
        if face_fd:
            # Faces need detail: the full picture (up to 1920 wide) twice a second.
            graph += ";[c]fps=2,scale='min(1920,iw)':-2[faces]"
            outs += ["-map", "[faces]", "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "3", f"pipe:{face_fd}"]
        return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-fflags", "nobuffer", *src,
                "-filter_complex", graph, *outs]

    async def _snapshots(self, cfg: VisionConfig) -> None:
        """Protect source: a fresh full-resolution snapshot every 2 s instead of decoding video.

        Visitors, faces and costumes come from Protect's own detections (see protect.py).
        """
        import cv2
        import numpy as np

        count, since = 0, time.monotonic()
        while True:
            jpeg = await self.protect.snapshot(cfg.preview_camera)
            img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_REDUCED_COLOR_2)
            if img is not None and img.shape[1] > 960:  # keep the page light: preview at ~960 px
                img = cv2.resize(img, (960, int(img.shape[0] * 960 / img.shape[1])))
            ok, small = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80]) if img is not None else (False, None)
            self.frame = small.tobytes() if ok else jpeg
            self.full_frame = jpeg
            self._frame_event.set()
            count += 1
            if self.state.error:
                self.state.error = None
                self._publish()
            if (now := time.monotonic()) - since > 10:
                self.state.fps = round(count / (now - since), 1)
                count, since = 0, now
            await asyncio.sleep(2)

    async def _run(self, cfg: VisionConfig) -> None:
        backoff = 2
        while True:
            try:
                if cfg.source == "protect":
                    await self._snapshots(cfg)
                    continue
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
        use_faces = cfg.faces and self.engine.available()
        frfd, fwfd = os.pipe() if use_faces else (None, None)
        proc = await asyncio.create_subprocess_exec(
            *self._ffmpeg_args(cfg, wfd, fwfd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            pass_fds=tuple(fd for fd in (wfd, fwfd) if fd is not None))
        os.close(wfd)
        loop = asyncio.get_running_loop()
        thumbs = asyncio.StreamReader()
        transport, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(thumbs), os.fdopen(rfd, "rb"))
        tasks = [asyncio.create_task(self._read_jpegs(proc.stdout)),
                 asyncio.create_task(self._read_motion(cfg, thumbs))]
        face_transport = None
        if use_faces:
            os.close(fwfd)
            face_stream = asyncio.StreamReader(limit=8 * 1024 * 1024)
            face_transport, _ = await loop.connect_read_pipe(
                lambda: asyncio.StreamReaderProtocol(face_stream), os.fdopen(frfd, "rb"))
            tasks.append(asyncio.create_task(self._read_faces(cfg, face_stream)))
        try:
            done, _ = await asyncio.wait([*tasks, asyncio.create_task(proc.wait())],
                                         return_when=asyncio.FIRST_COMPLETED)
            err = (await proc.stderr.read()).decode(errors="replace").strip() if proc.stderr else ""
            for t in done:
                if t.exception():
                    raise t.exception()
            raise ConnectionError(err.splitlines()[-1] if err else "The camera stream ended")
        finally:
            for t in tasks:
                t.cancel()
            transport.close()
            if face_transport:
                face_transport.close()
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

    async def _read_faces(self, cfg: VisionConfig, stream: asyncio.StreamReader) -> None:
        """Find and recognise faces in the sharp frames; announce returning people."""
        buf = b""
        busy = False
        while chunk := await stream.read(262144):
            buf += chunk
            frame = None
            while (end := buf.find(b"\xff\xd9")) != -1:
                start = buf.find(b"\xff\xd8")
                if 0 <= start < end:
                    frame = buf[start:end + 2]
                buf = buf[end + 2:]
            if frame is None or busy:
                continue  # still working on the last one: skip rather than fall behind
            busy = True
            try:
                seen = await asyncio.to_thread(self.engine.process, frame, cfg.active_zones, _focus(cfg))
                self._recognise(seen)
            except Exception as exc:
                log.warning("face check failed: %r", exc)
            finally:
                busy = False

    def _recognise(self, seen: list) -> None:
        now = time.time()
        for s in seen:
            person, sim = self.memory.match(s.embedding)
            s.similarity = sim
            if person:
                s.person_id, s.name = person["id"], person["name"]
                if sim < 0.6:  # a less-certain match is a new angle worth keeping
                    self.memory.add_sample(person, s)
                if self.memory.seen_now(person) and self._on_known:
                    asyncio.get_running_loop().create_task(self._on_known(person))
        self.seen = seen
        self._recent = [r for r in self._recent if now - r.ts < 12] + [s for s in seen if not s.person_id]
        faces = [s.public(i) for i, s in enumerate(seen)]
        if faces != self.state.faces:
            self.state.faces = faces
            self.svc.bus.publish("faces", {"faces": faces})

    def learn_name(self, name: str, index: int | None = None) -> dict | None:
        """Save a face under a name: the one picked, else the nearest unknown face just seen.

        "Nearest" is the biggest face in the last few seconds, which in a crowd is usually
        the person standing right in front of Skelly doing the talking.
        """
        if index is not None:
            pick = self.seen[index] if 0 <= index < len(self.seen) else None
        else:
            now = time.time()
            pool = [s for s in self._recent if now - s.ts < 10] or [s for s in self.seen if not s.person_id]
            pick = max(pool, key=lambda s: s.area, default=None)
        if pick is None:
            return None
        person = self.memory.remember(name, pick)
        self.memory.seen_now(person)
        self._recent = [r for r in self._recent if r is not pick]
        self.svc.bus.publish("face_learned", {"id": person["id"], "name": person["name"], "thumb": pick.thumb})
        self._recognise(self.seen)
        return person

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
                # Most of the picture changing at once is a cloud, headlights or the camera's
                # night switch, not someone walking up.
                lighting = changed > 0.6
                counts = changed > threshold and not (lighting and "weather" in cfg.ignore)
                busy_frames = busy_frames + 1 if counts else 0
                if busy_frames >= max(2, cfg.fps // 2):  # half a second of movement
                    busy_frames = 0
                    asyncio.create_task(self._maybe_visitor(cfg))
                now = time.monotonic()
                if now - last_pub > 0.5:
                    last_pub = now
                    self.svc.bus.publish("vision_motion", {"motion": self.state.motion, "fps": self.state.fps})
            prev = thumb

    def recognise_external(self, seen: list) -> None:
        """Faces from another camera (a Protect snapshot): match them and offer unknowns for naming.

        They don't replace the preview's face boxes, since they're from a different picture.
        """
        now = time.time()
        for s in seen:
            person, sim = self.memory.match(s.embedding)
            s.similarity = sim
            if person:
                s.person_id, s.name = person["id"], person["name"]
                if sim < 0.6:
                    self.memory.add_sample(person, s)
                if self.memory.seen_now(person) and self._on_known:
                    asyncio.get_running_loop().create_task(self._on_known(person))
        self._recent = [r for r in self._recent if now - r.ts < 12] + [s for s in seen if not s.person_id]
        if seen:
            self.svc.bus.publish("protect_faces", {"faces": [{"name": s.name, "thumb": s.thumb} for s in seen]})

    async def maybe_visitor_from(self, jpeg: bytes, zones: list) -> None:
        """A person spotted by another camera (Protect) counts as walking up, checked like motion."""
        cfg = VisionConfig.from_dict(self._config())
        keep = self.frame
        self.frame = jpeg  # describe() and costume checks look at this picture
        try:
            await self._maybe_visitor(VisionConfig.from_dict({**vars(cfg), "zones": {cfg.camera_key: zones}}))
        finally:
            if keep is not None:
                self.frame = keep

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
                passing = int(verdict.get("people") or 0) and verdict.get("approaching") is False
                if passing and cfg.call_over and self._on_passerby:
                    self.state.costumes = costume_names(verdict)
                    await self._on_passerby(verdict, cfg)
                    return
                if not worth_a_visit(verdict, cfg.ignore):
                    if int(verdict.get("people") or 0) and cfg.call_over and self._on_passerby:
                        # Walking past rather than coming up: Skelly calls them over.
                        self.state.costumes = costume_names(verdict)
                        await self._on_passerby(verdict, cfg)
                        return
                    self.state.last_visitor_at = None  # a car, leaves blowing about...: stay ready
                    return
                description = verdict.get("description")
                self.state.costumes = costume_names(verdict)
                costumes = self.state.costumes
                if costumes and description and not any(c.lower() in description.lower() for c in costumes):
                    description = f"{description} Costumes: {', '.join(costumes)}."
        self.state.visitor = True
        self.state.description = description
        self._publish()
        await self._on_visitor(description, cfg)
        await asyncio.sleep(10)
        self.state.visitor = False
        self._publish()


def _focus(cfg: VisionConfig) -> list[float] | None:
    """Where visitors talking to Skelly stand: around his ignored area, wider and a bit lower."""
    zones = cfg.active_zones
    if not zones:
        return None
    x, y, w, h = zones[0]
    fx, fy = max(0.0, x - 2.5 * w), max(0.0, y - 0.15 * h)
    return [fx, fy, min(1.0, x + 3.5 * w) - fx, min(1.0, y + 1.3 * h) - fy]


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
    "Reply with JSON only: "
    '{"people": <number of real people>, "approaching": <true if any of them is coming towards the camera or '
    'standing near it, false if they are only walking or driving past>, "animals": <number>, "vehicles": '
    '<number of moving or arriving cars/trucks>, "bikes": <number of bikes or scooters>, '
    '"costumes": [<for each person in a Halloween costume, a short name for it, e.g. "vampire", "witch", '
    '"Spider-Man", "princess", "zombie", "inflatable dinosaur"; empty if nobody is dressed up>], '
    '"description": "<one short sentence about the people (or animals) a skeleton could joke about: '
    'costumes, clothes colours, pets, what they hold>"}.'
)


def costume_names(verdict: dict | None) -> list[str]:
    raw = (verdict or {}).get("costumes") or []
    out = []
    for c in raw if isinstance(raw, list) else [raw]:
        name = str(c.get("costume") if isinstance(c, dict) else c).strip()[:40]
        if name and name.lower() not in {x.lower() for x in out}:
            out.append(name)
    return out[:8]


def worth_a_visit(verdict: dict, ignore: list[str]) -> bool:
    """Whether what the AI saw should set Skelly off, given the ignore chips."""
    people = int(verdict.get("people") or 0)
    if people and ("passers" not in ignore or verdict.get("approaching", True)):
        return True
    if int(verdict.get("animals") or 0) and "animals" not in ignore:
        return True
    if int(verdict.get("vehicles") or 0) and "vehicles" not in ignore:
        return True
    if int(verdict.get("bikes") or 0) and "bikes" not in ignore:
        return True
    return not people and "weather" not in ignore and not any(
        int(verdict.get(k) or 0) for k in ("animals", "vehicles", "bikes"))

FIND_PROMPT = (
    "This image is {w}x{h} pixels. It shows a life-size Halloween skeleton decoration (an animatronic prop). "
    "Give pixel coordinates of: the top of its skull, the bottom of its lowest foot, leg or stand, its leftmost "
    "point and its rightmost point (arms included). "
    'Reply with JSON only: {{"found": true, "top": y, "bottom": y, "left": x, "right": x}} or {{"found": false}}.'
)


def jpeg_size(jpeg: bytes) -> tuple[int, int]:
    """Width and height from a JPEG's start-of-frame marker."""
    i = 2
    while i + 9 < len(jpeg):
        if jpeg[i] != 0xFF:
            i += 1
            continue
        marker, length = jpeg[i + 1], int.from_bytes(jpeg[i + 2:i + 4], "big")
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            return int.from_bytes(jpeg[i + 7:i + 9], "big"), int.from_bytes(jpeg[i + 5:i + 7], "big")
        i += 2 + length
    return 640, 360


async def find_skelly(vault, jpeg: bytes) -> list[float] | None:
    """Where Skelly stands in the picture, padded a little, as [x, y, w, h] fractions.

    Asks for his extreme points in pixels with a stronger model: in testing, asking for a
    fractional box (or using Haiku) reliably cut off his legs.
    """
    w, h = jpeg_size(jpeg)
    r = await _ask_vision(vault, jpeg, FIND_PROMPT.format(w=w, h=h), precise=True)
    if not r or not r.get("found"):
        return None
    left, right = sorted((float(r["left"]) / w, float(r["right"]) / w))
    top, bottom = sorted((float(r["top"]) / h, float(r["bottom"]) / h))
    pad_x, pad_y = (right - left) * 0.2, (bottom - top) * 0.08
    x0, y0 = max(0.0, left - pad_x), max(0.0, top - pad_y)
    x1, y1 = min(1.0, right + pad_x), min(1.0, bottom + pad_y)
    return [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)]


async def describe(vault, jpeg: bytes, zones: list[list[float]] | None = None) -> dict | None:
    """Ask Claude (or OpenAI) whether people are in the frame and what they look like."""
    return await _ask_vision(vault, await blackout(jpeg, zones or []), SEE_PROMPT)


async def _ask_vision(vault, jpeg: bytes, prompt: str, *, precise: bool = False) -> dict | None:
    b64 = base64.b64encode(jpeg).decode()
    async with httpx.AsyncClient(timeout=60 if precise else 20) as http:
        if key := vault.get("anthropic_api_key"):
            r = await http.post("https://api.anthropic.com/v1/messages", headers={
                "x-api-key": key, "anthropic-version": "2023-06-01"}, json={
                "model": "claude-sonnet-5-5" if precise else "claude-haiku-4-5-20251001",
                "max_tokens": 1000 if precise else 150, "messages": [{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": prompt}]}]})
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
        elif key := vault.get("openai_api_key"):
            r = await http.post("https://api.openai.com/v1/chat/completions", headers={
                "Authorization": f"Bearer {key}"}, json={
                "model": "gpt-4o" if precise else "gpt-4o-mini", "max_tokens": 150,
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": prompt}]}]})
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
        else:
            return None
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1]) if start != -1 else None
