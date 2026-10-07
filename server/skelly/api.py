"""HTTP + WebSocket API, and the static web UI."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .link import BleakLink, Link, SimulatedLink
from .profiles import PROFILES
from .service import SkellyService

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


def make_link() -> Link:
    return SimulatedLink() if os.environ.get("SKELLY_SIMULATE") == "1" else BleakLink()


def create_app(link: Link | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = SkellyService(link or make_link())
        await svc.start()
        app.state.svc = svc
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
