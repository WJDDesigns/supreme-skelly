"""HTTP + WebSocket API, and the static web UI."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import audio
from . import protocol as proto
from .link import BleakLink, Link, SimulatedLink
from .profiles import PROFILES
from .service import SkellyService
from .settings import Settings

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
log = logging.getLogger(__name__)
WEB_DIR = Path(os.environ.get("SKELLY_WEB_DIR", Path(__file__).resolve().parents[2] / "web"))


class ConnectBody(BaseModel):
    address: str
    name: str | None = None


class MovementBody(BaseModel):
    parts: list[str]


class EyeBody(BaseModel):
    eye: int = Field(ge=0, le=255)


class LightBody(BaseModel):
    light: str | None = None  # profile light key, or None/"all"
    mode: int | None = Field(None, ge=0, le=255)
    brightness: int | None = Field(None, ge=0, le=255)
    color: str | None = Field(None, pattern=r"^#?[0-9a-fA-F]{6}$")
    cycle: bool = False
    speed: int | None = Field(None, ge=0, le=254)


class VolumeBody(BaseModel):
    volume: int = Field(ge=0, le=255)


class PlayBody(BaseModel):
    play: bool = True


class KeepLookBody(BaseModel):
    keep: bool


class AdapterBody(BaseModel):
    address: str


class SettingsBody(BaseModel):
    auto_connect: bool | None = None
    auto_live_mode: bool | None = None


def make_link() -> Link:
    return SimulatedLink() if os.environ.get("SKELLY_SIMULATE") == "1" else BleakLink()


def create_app(
    link: Link | None = None, settings: Settings | None = None, *, autoconnect: bool | None = None
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = SkellyService(link or make_link(), settings=settings or Settings.load())
        await svc.start()
        if autoconnect if autoconnect is not None else os.environ.get("SKELLY_AUTOCONNECT", "1") == "1":
            svc.start_autoconnect()
        app.state.svc = svc
        app.state.tasks = set()
        yield
        await svc.stop()

    app = FastAPI(title="Supreme Skelly", lifespan=lifespan)

    def svc() -> SkellyService:
        return app.state.svc

    async def guarded(coro):
        try:
            return await coro
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except ConnectionError as exc:
            raise HTTPException(409, str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(504, "The device didn't answer in time") from exc

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    @app.get("/api/profiles")
    async def profiles():
        return [p.to_dict() for p in PROFILES]

    @app.get("/api/state")
    async def state():
        return svc().snapshot()

    @app.get("/api/settings")
    async def get_settings():
        return svc().settings.public()

    @app.patch("/api/settings")
    async def patch_settings(body: SettingsBody):
        return svc().update_settings(**body.model_dump(exclude_none=True))

    @app.get("/api/adapters")
    async def adapters():
        return await guarded(svc().adapters())

    @app.post("/api/adapter")
    async def choose_adapter(body: AdapterBody):
        return await guarded(svc().choose_adapter(body.address))

    @app.get("/api/look")
    async def get_look():
        return {"look": svc().look, "keep": svc().settings.keep_look}

    @app.post("/api/look/keep")
    async def keep_look(body: KeepLookBody):
        out = svc().set_keep_look(body.keep)
        if body.keep and svc().link.connected:
            await guarded(svc().apply_look())
        return out

    @app.post("/api/look/save-to-sounds")
    async def save_look_to_sounds():
        return {"sounds": await guarded(svc().save_look_to_sounds())}

    @app.post("/api/scan")
    async def scan(timeout: float = 6.0):
        found = await guarded(svc().scan(min(max(timeout, 1.0), 20.0)))
        return [vars(f) for f in found]

    @app.post("/api/connect")
    async def connect(body: ConnectBody):
        await guarded(svc().connect(body.address, body.name))
        return svc().snapshot()

    @app.post("/api/disconnect")
    async def disconnect():
        await svc().disconnect()
        return svc().snapshot()

    @app.post("/api/movement")
    async def movement(body: MovementBody):
        await guarded(svc().set_movement(body.parts))
        return {"ok": True}

    @app.post("/api/eye")
    async def eye(body: EyeBody):
        await guarded(svc().set_eye(body.eye))
        return {"ok": True}

    @app.post("/api/light")
    async def light(body: LightBody):
        rgb = None
        if body.color:
            h = body.color.lstrip("#")
            rgb = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        await guarded(svc().set_light(body.light, mode=body.mode, brightness=body.brightness,
                                      rgb=rgb, cycle=body.cycle, speed=body.speed))
        return {"ok": True}

    @app.post("/api/volume")
    async def volume(body: VolumeBody):
        await guarded(svc().set_volume(body.volume))
        return {"ok": True}

    @app.post("/api/live-mode")
    async def live_mode():
        await guarded(svc().enable_live_mode())
        return {"ok": True}

    @app.get("/api/files")
    async def files():
        return svc().state.files

    @app.post("/api/files/refresh")
    async def refresh_files():
        return await guarded(svc().refresh_files())

    @app.post("/api/files/{serial}/play")
    async def play(serial: int, body: PlayBody | None = None):
        await guarded(svc().play_file(serial, body.play if body else True))
        return {"ok": True}

    @app.delete("/api/files/{serial}")
    async def delete_file(serial: int):
        return await guarded(svc().delete_sound(serial))

    @app.post("/api/sounds/upload", status_code=202)
    async def upload_sound(file: Annotated[UploadFile, File()], name: Annotated[str, Form()] = "",
                           normalize: Annotated[bool, Form()] = True):
        """Convert any audio file to Skelly's format, then send it in the background.

        Progress arrives on the event stream as ``upload`` messages.
        """
        s = svc()
        if not s.link.connected:
            raise HTTPException(409, "Connect to Skelly first")
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "That file is too big (50 MB max)")
        title = name.strip() or Path(file.filename or "").stem
        device_name = proto.sanitize_upload_name(title)
        if any(f["name"].lower() == device_name.lower() for f in s.state.files):
            raise HTTPException(400, f'Skelly already has a sound called "{device_name}"')
        wake_ms = audio.WAKE_TONE_MS if s.profile.wake_tone_ms is None else s.profile.wake_tone_ms
        try:
            mp3, seconds = await asyncio.to_thread(audio.prepare, data, normalize=normalize, wake_ms=wake_ms)
        except audio.AudioError as exc:
            raise HTTPException(400, str(exc)) from exc

        async def run() -> None:
            try:
                await s.upload_sound(mp3, device_name)
            except Exception as exc:  # reported to the UI through the "upload" event
                log.warning("upload of %s failed: %s", device_name, exc)

        task = asyncio.create_task(run(), name=f"upload-{device_name}")
        app.state.tasks.add(task)
        task.add_done_callback(app.state.tasks.discard)
        return {"name": device_name, "seconds": round(seconds, 1), "bytes": len(mp3)}

    @app.websocket("/api/events")
    async def events(ws: WebSocket):
        await ws.accept()
        bus = svc().bus
        q = bus.subscribe()
        try:
            await ws.send_json({"type": "snapshot", "data": svc().snapshot()})
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), 25)
                except TimeoutError:
                    msg = {"type": "ping"}
                await ws.send_json(msg)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            bus.unsubscribe(q)

    if WEB_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

        @app.get("/")
        async def index():
            return FileResponse(WEB_DIR / "index.html")

    return app


app = create_app()
