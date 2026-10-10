"""HTTP + WebSocket API, and the static web UI."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import audio, audio_io, protect, recorder, scene, snaps, speaker, system, updates, usage, voices
from . import protocol as proto
from .auth import COOKIE, SESSION_DAYS, Auth
from .conversation import (
    Conversation,
    ConversationConfig,
    MissingKey,
    elevenlabs_agent,
    elevenlabs_agents,
    elevenlabs_create_agent,
    elevenlabs_update_agent,
    elevenlabs_voices,
    for_character,
    is_quiet,
)
from .fog import KINDS as FOG_KINDS
from .fog import Fog, FogConfig
from .link import BleakLink, Link, SimulatedLink
from .meters import Meters
from .playlist import Playlist
from .profiles import PROFILES
from .service import SkellyService
from .settings import Settings, data_dir
from .vault import Vault
from .vision import (
    IGNORABLE,
    Vision,
    VisionConfig,
    costume_names,
    describe,
    find_skelly,
    list_cameras,
    notice,
    protect_verdict,
)

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


class PerformanceBody(BaseModel):
    moves: list[str] = []
    eye: int | None = Field(None, ge=0, le=255)
    color: str | None = Field(None, pattern=r"^#?[0-9a-fA-F]{6}$")
    mode: int | None = Field(None, ge=0, le=255)
    brightness: int | None = Field(None, ge=0, le=255)
    speed: int | None = Field(None, ge=0, le=254)
    cycle: bool = False


class PlaylistItem(BaseModel):
    name: str
    on: bool = True
    before: float = Field(0.0, ge=0, le=600)
    after: float = Field(1.5, ge=0, le=600)


class PlaylistBody(BaseModel):
    items: list[PlaylistItem]
    loop: bool = False
    shuffle: bool = False


class KeepLookBody(BaseModel):
    keep: bool


class AdapterBody(BaseModel):
    address: str


class AudioBody(BaseModel):
    mic: str | None = None
    mic_gain: int | None = Field(None, ge=0, le=300)
    out_gain: int | None = Field(None, ge=0, le=150)
    skelly: bool | None = None
    extra: list[str] | None = None
    keep_awake: bool | None = None


class AddressBody(BaseModel):
    address: str


class VolumeSinkBody(BaseModel):
    sink: str
    volume: int = Field(ge=0, le=150)


class ZonesBody(BaseModel):
    zones: list[list[float]]


class SecretBody(BaseModel):
    value: str


class LoginBody(BaseModel):
    password: str = Field(max_length=200)


class PasswordBody(BaseModel):
    current: str = Field("", max_length=200)
    new: str = Field("", max_length=200)


class SettingsBody(BaseModel):
    auto_connect: bool | None = None
    auto_live_mode: bool | None = None
    timezone: str | None = Field(None, max_length=64)
    setup_done: bool | None = None
    auto_update: bool | None = None
    wallpaper: str | None = Field(None, pattern=r"^[a-z0-9-]{1,32}$")
    wallpaper_dim: int | None = Field(None, ge=0, le=90)
    ui_transparency: int | None = Field(None, ge=0, le=80)


WALLPAPER_MAX = 15 * 1024 * 1024


def _image_type(head: bytes) -> str | None:
    """The picture's media type from its first bytes, or None when it isn't a picture we show."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


# Reachable without signing in: the health check and what the login form needs.
OPEN_PATHS = {"/api/health", "/api/auth/status", "/api/auth/login", "/api/auth/logout"}


def _cookie(headers: dict[str, str], name: str) -> str | None:
    for part in headers.get("cookie", "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return None


class Guard:
    """Locks /api behind the UI password (when one is set) and refuses other websites' requests.

    A page on some other site can't read our replies, but the browser would still send its
    POSTs and open the live feed, so anything that changes something or opens a WebSocket must
    come from this page's own origin.
    """

    def __init__(self, app, auth: Auth) -> None:
        self.app, self.auth = app, auth

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        changing = scope["type"] == "websocket" or scope.get("method") not in ("GET", "HEAD", "OPTIONS")
        origin = headers.get("origin")
        if changing and origin and origin != "null" and origin.split("://", 1)[-1] != headers.get("host"):
            return await self._deny(scope, receive, send, 403, "Requests from other websites aren't allowed")
        if scope["path"] not in OPEN_PATHS and not self.auth.valid_session(_cookie(headers, COOKIE)):
            return await self._deny(scope, receive, send, 401, "Sign in first")
        return await self.app(scope, receive, send)

    @staticmethod
    async def _deny(scope, receive, send, status: int, msg: str):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4000 + status})
            return
        await JSONResponse({"detail": msg}, status_code=status)(scope, receive, send)


def make_link() -> Link:
    return SimulatedLink() if os.environ.get("SKELLY_SIMULATE") == "1" else BleakLink()


def create_app(
    link: Link | None = None, settings: Settings | None = None, *, autoconnect: bool | None = None,
    vault: Vault | None = None, auth: Auth | None = None,
) -> FastAPI:
    auth = auth or Auth()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = SkellyService(link or make_link(), settings=settings or Settings.load())
        await svc.start()
        if autoconnect if autoconnect is not None else os.environ.get("SKELLY_AUTOCONNECT", "1") == "1":
            svc.start_autoconnect()
        app.state.svc = svc
        app.state.playlist = Playlist(svc)
        app.state.tasks = set()
        app.state.vault = vault or Vault()
        app.state.conv = Conversation(
            svc, app.state.vault, lambda: {**svc.settings.conversation, "mic": svc.settings.audio.get("mic", ""),
                                           "mic_gain": svc.settings.audio.get("mic_gain", 100),
                                           "out_gain": svc.settings.audio.get("out_gain", 100)},
            resolve_output)
        app.state.vision = Vision(svc, app.state.vault, lambda: svc.settings.vision, on_visitor, on_known)
        app.state.conv.on_user_text = on_user_text
        app.state.snaps = snaps.Snaps()
        app.state.vision.snaps = app.state.snaps
        app.state.fog = Fog(svc, lambda: svc.settings.fog, lambda: not quiet())
        app.state.vision.on_thumb = app.state.fog.thumb
        fog_cam_task = asyncio.create_task(fog_camera_watch())
        app.state.conv.save_picture = app.state.snaps.save
        app.state.conv.snap = lambda: app.state.snaps.save(
            app.state.vision.frame if app.state.vision.state.running else None)
        app.state.scene = scene.Scene()
        app.state.conv.scene_context = app.state.scene.context
        app.state.recorder = recorder.Recorder()
        app.state.costumes_mentioned = set()
        app.state.last_live_callout = 0.0
        costume_task = asyncio.create_task(costume_watch())
        app.state.keep_awake = audio_io.KeepAwake()
        awake_task = asyncio.create_task(keep_awake_watch())
        quiet_task = asyncio.create_task(quiet_watch())
        app.state.pending_name = None
        name_task = asyncio.create_task(pending_name_watch())
        app.state.conv.on_started = start_recording
        app.state.conv.on_ended = stop_recording
        app.state.protect = protect.Protect(svc, app.state.vault,
                                            lambda: VisionConfig.from_dict(svc.settings.vision), on_protect_person)
        app.state.protect.start()
        app.state.vision.protect = app.state.protect
        app.state.vision._on_passerby = on_passerby
        app.state.meters = Meters(svc.bus, lambda: svc.settings.audio.get("mic", ""), meter_sink,
                                  lambda: svc.settings.audio.get("mic_gain", 100) / 100)
        if VisionConfig.from_dict(svc.settings.vision).start_on_boot:
            try:
                await app.state.vision.start()
            except ValueError as exc:
                log.info("camera not started: %s", exc)
        yield
        costume_task.cancel()
        awake_task.cancel()
        await app.state.keep_awake.stop()
        fog_cam_task.cancel()
        name_task.cancel()
        quiet_task.cancel()
        await app.state.conv.stop()
        await app.state.recorder.stop()
        await app.state.vision.stop()
        if app.state.fog.state.fogging:
            try:
                await app.state.fog.stop()
            except ValueError:
                pass
        await app.state.protect.stop()
        await app.state.meters.stop()
        await app.state.playlist.stop()
        await svc.stop()

    app = FastAPI(title="Supreme Skelly", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(Guard, auth=auth)
    app.state.auth = auth

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

    def full_snapshot() -> dict:
        return {**svc().snapshot(), "conversation": app.state.conv.snapshot(), "vision": app.state.vision.snapshot(),
                "playlist": app.state.playlist.snapshot(), "fog": app.state.fog.snapshot()}

    # -- Skelly's Live speaker ---------------------------------------------------

    heal_lock = asyncio.Lock()

    async def heal_audio(why: str) -> None:
        """Restart the host's sound session once, instead of asking for a reboot."""
        async with heal_lock:
            if await audio_io.sound_server_ok() and why.startswith("the sound service"):
                return  # another request already brought it back
            log.warning("restarting the sound service: %s", why)
            try:
                await system.restart_audio()
            except Exception as exc:
                log.warning("couldn't restart the sound service: %s", exc)

    async def skelly_sink() -> str | None:
        """Make sure Live Mode is on and its speaker is paired and connected; return its sink."""
        s = svc()
        if os.environ.get("SKELLY_SIMULATE") == "1":
            return None
        if not s.link.connected:
            raise ConnectionError("Connect to Skelly first, so he can talk")
        if not s.state.live_mode:
            await s.enable_live_mode()
            await asyncio.sleep(3)
        if not await audio_io.sound_server_ok():
            await heal_audio("the sound service didn't answer")
            if not await audio_io.sound_server_ok():
                raise RuntimeError(audio_io.NO_SOUND_SERVER)
        names = tuple(dict.fromkeys([*s.profile.live_audio_names, *([s.state.bt_name] if s.state.bt_name else [])]))
        adapter = getattr(s.link, "adapter_in_use", None) or "hci0"
        try:
            info = await speaker.connect_speaker(adapter, names, s.state.pin or "1234")
        except speaker.NoAudioProfile:
            await heal_audio("Bluetooth had no audio profile for Skelly's speaker")
            info = await speaker.connect_speaker(adapter, names, s.state.pin or "1234")
        if info.get("address") and s.settings.live_speaker != info["address"]:
            s.settings.live_speaker = info["address"]
            s.settings.save()
        sink = audio_io.skelly_sink_name(info.get("address"))
        for _ in range(20):  # PipeWire takes a moment to publish the new speaker
            if any(d["name"] == sink for d in (await audio_io.list_devices())["speakers"]):
                break
            await asyncio.sleep(0.5)
        return sink

    def adapter_name() -> str:
        return getattr(svc().link, "adapter_in_use", None) or "hci0"

    async def resolve_output() -> str | None:
        """Where Skelly's voice plays: his Live speaker and/or extra speakers, combined if several."""
        audio = svc().settings.audio
        sinks: list[str] = []
        if audio.get("skelly", True):
            sinks.append(await skelly_sink())
        if os.environ.get("SKELLY_SIMULATE") == "1":
            return None
        have = {d["name"] for d in (await audio_io.list_devices())["speakers"]}
        for sink in audio.get("extra", []):
            if sink not in have and sink.startswith("bluez_output."):
                mac = sink.split(".")[1].replace("_", ":")
                try:
                    try:
                        await speaker.connect_address(adapter_name(), mac)
                    except speaker.NoAudioProfile:
                        await heal_audio(f"Bluetooth had no audio profile for {mac}")
                        await speaker.connect_address(adapter_name(), mac)
                    await asyncio.sleep(2)
                except Exception as exc:
                    log.info("extra speaker %s not connected: %s", mac, exc)
            sinks.append(sink)
        have = {d["name"] for d in (await audio_io.list_devices())["speakers"]}
        live = [s for s in sinks if s and s in have]
        if not live:
            raise ConnectionError("None of the chosen speakers are connected")
        # PipeWire gives every newly connected Bluetooth speaker 40% (-24 dB), which made Skelly
        # nearly silent. Put each one at its saved volume, full by default, every time.
        saved = audio.get("volumes", {})
        current = await audio_io.volumes()
        for sink in live:
            want = int(saved.get(sink, 100))
            if current.get(sink) != want:
                try:
                    await audio_io.set_volume(sink, want)
                except RuntimeError as exc:
                    log.info("volume for %s not set: %s", sink, exc)
        return await audio_io.output_for(live)

    async def meter_sink() -> str | None:
        """The output to meter: the conversation's if one is running, else Skelly's/combined if awake."""
        conv = app.state.conv
        if conv.running and conv.output_sink:
            return conv.output_sink
        have = {d["name"] for d in (await audio_io.list_devices())["speakers"]}
        for name in (audio_io.last_sink, audio_io.COMBINED, audio_io.skelly_sink_name(svc().settings.live_speaker)):
            if name and name in have:
                return name
        return None

    @app.post("/api/audio/meters")
    async def audio_meters():
        """Keep live mic/speaker levels coming on the event stream for the next 12 seconds."""
        app.state.meters.watch()
        return app.state.meters.levels

    @app.get("/api/audio")
    async def audio_state():
        return {"devices": await audio_io.list_devices(), "volumes": await audio_io.volumes(),
                "config": svc().settings.audio,
                "skelly_sink": audio_io.skelly_sink_name(svc().settings.live_speaker)}

    @app.put("/api/audio/config")
    async def audio_config(body: AudioBody):
        svc().settings.audio = {**svc().settings.audio, **body.model_dump(exclude_none=True)}
        svc().settings.save()
        return svc().settings.audio

    @app.post("/api/audio/scan")
    async def audio_scan():
        try:
            return await speaker.scan_speakers(adapter_name())
        except Exception as exc:
            raise HTTPException(502, f"Bluetooth scan failed: {exc}") from exc

    @app.post("/api/audio/pair")
    async def audio_pair(body: AddressBody):
        try:
            try:
                info = await speaker.connect_address(adapter_name(), body.address)
            except speaker.NoAudioProfile:
                await heal_audio(f"Bluetooth had no audio profile for {body.address}")
                info = await speaker.connect_address(adapter_name(), body.address)
        except Exception as exc:
            raise HTTPException(502, f"Couldn't pair: {exc}. Is the speaker in pairing mode?") from exc
        sink = audio_io.skelly_sink_name(info["address"])
        audio = svc().settings.audio
        if sink not in audio.get("extra", []):
            svc().settings.audio = {**audio, "extra": [*audio.get("extra", []), sink]}
            svc().settings.save()
        return {**info, "sink": sink}

    @app.post("/api/audio/forget")
    async def audio_forget(body: AddressBody):
        sink = audio_io.skelly_sink_name(body.address)
        audio = svc().settings.audio
        svc().settings.audio = {**audio, "extra": [s for s in audio.get("extra", []) if s != sink]}
        svc().settings.save()
        try:
            await speaker.forget(adapter_name(), body.address)
        except Exception as exc:
            log.info("forget %s: %s", body.address, exc)
        return svc().settings.audio

    @app.post("/api/audio/volume")
    async def audio_volume(body: VolumeSinkBody):
        try:
            await audio_io.set_volume(body.sink, body.volume)
        except RuntimeError as exc:
            raise HTTPException(400, str(exc)) from exc
        audio = svc().settings.audio
        svc().settings.audio = {**audio, "volumes": {**audio.get("volumes", {}), body.sink: body.volume}}
        svc().settings.save()
        return {"ok": True}

    @app.post("/api/audio/test")
    async def audio_test():
        try:
            sink = await resolve_output()
        except (ConnectionError, LookupError, RuntimeError) as exc:
            raise HTTPException(409, str(exc)) from exc
        out = audio_io.Speaker(sink, svc().settings.audio.get("out_gain", 100) / 100)
        audio_io.last_sink = sink
        await asyncio.sleep(1.2)  # let the speaker meter latch on before the chime starts
        await out.play(audio_io.chime(), 16000)
        await out.wait_done()
        await out.close()
        return {"sink": sink}

    async def keep_awake_watch() -> None:
        """Silence to the Bluetooth speakers so they don't chime each time Skelly starts talking."""
        while True:
            try:
                sinks: set[str] = set()
                if svc().settings.audio.get("keep_awake", True) and os.environ.get("SKELLY_SIMULATE") != "1":
                    have = {d["name"] for d in (await audio_io.list_devices())["speakers"]}
                    sinks = {s for s in have if s.startswith("bluez_output.")}
                await app.state.keep_awake.sync(sinks)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.info("keep-awake check failed: %r", exc)
            await asyncio.sleep(15)

    def quiet() -> bool:
        """Quiet hours (Settings), or Skelly switched off: visitors and passers-by don't start conversations.

        Home Assistant powers Skelly down on a schedule; a chat then would talk to nobody but still cost credits.
        """
        if not svc().link.connected:
            return True
        return is_quiet(ConversationConfig.from_dict(svc().settings.conversation))

    async def quiet_watch() -> None:
        """At the start of quiet hours, end any conversation still running."""
        was = False
        while True:
            await asyncio.sleep(30)
            try:
                now = quiet()
                if now and not was and app.state.conv.running:
                    log.info("quiet hours started; ending the conversation")
                    await app.state.conv.stop()
                was = now
            except Exception as exc:
                log.info("quiet hours check failed: %r", exc)

    def costume_hint(costumes: list[str]) -> str:
        return (f" They're dressed as: {', '.join(costumes)}. Mention their costume in a fun, spooky way."
                if costumes else "")

    def later(coro) -> None:
        """Run alongside, without holding up the caller (fog takes a few seconds)."""
        task = asyncio.create_task(coro)
        app.state.tasks.add(task)
        task.add_done_callback(app.state.tasks.discard)

    async def on_passerby(verdict: dict, cfg: VisionConfig) -> None:
        """Someone walking past: Skelly calls them over, a different way each time."""
        from .callouts import call_out

        if quiet():
            return
        later(app.state.fog.visitor("Calling someone over"))

        costumes, noticed = costume_names(verdict), notice(verdict)
        line = call_out(costumes, noticed, svc().profile.character)
        svc().bus.publish("calling_over", {"line": line, "costumes": costumes})
        conv = app.state.conv
        if conv.running:
            conv.add_context(f"Someone else is walking past at a distance. Call them over to join: {line}")
            return
        ctx = ("You just called out to someone walking past to come over and chat. When they come over, "
               "welcome them warmly." + costume_hint(costumes)
               + (f" You noticed their {noticed}; mention it in a friendly way." if noticed else "")
               + (" It's getting dark, so don't guess the colour of anything they're wearing."
                  if verdict.get("dark") else ""))
        try:
            await conv.start(context=ctx, opening=line,
                             trigger=f"called over someone walking past ({app.state.vision.source})",
                             picture=app.state.vision.frame)
        except (MissingKey, ValueError) as exc:
            log.info("call-over not started: %s", exc)

    async def on_visitor(description: str | None, cfg: VisionConfig) -> None:
        costumes = list(app.state.vision.state.costumes)
        svc().bus.publish("visitor", {"description": description, "costumes": costumes,
                                      "ts": __import__("time").time()})
        app.state.costumes_mentioned = set(c.lower() for c in costumes)
        later(app.state.fog.visitor())
        if cfg.auto_converse and not quiet():
            ctx = (f"Someone just walked up. What the camera sees: {description}{costume_hint(costumes)}"
                   if description else None)
            try:
                await app.state.conv.start(context=ctx, trigger=f"someone walked up ({app.state.vision.source})",
                                           picture=app.state.vision.frame)
            except (MissingKey, ValueError) as exc:
                log.info("visitor conversation not started: %s", exc)

    # -- UniFi Protect -------------------------------------------------------------

    async def call_over_live(jpeg: bytes, zones: list, source: str = "a camera") -> None:
        """A person shows up while Skelly is in a conversation nobody is having: call them over."""
        now = time.monotonic()
        conv = app.state.conv
        if now - app.state.last_live_callout < 20 or conv.quiet_for < 8 or quiet():
            app.state.vision.sighting("Skipped: Skelly is mid-conversation", jpeg=jpeg, source=source)
            return
        app.state.last_live_callout = now
        try:
            verdict = await describe(app.state.vault, jpeg, zones)
        except Exception as exc:
            log.info("call-over check failed: %r", exc)
            return
        if not verdict or not int(verdict.get("people") or 0):
            app.state.vision.sighting("Mid-chat: nobody new in the picture", verdict, jpeg=jpeg, source=source)
            return
        from .callouts import call_out

        line = call_out(costume_names(verdict), notice(verdict), svc().profile.character)
        if conv.call_over(line):
            svc().bus.publish("calling_over", {"line": line, "costumes": costume_names(verdict)})
            app.state.vision.sighting("Mid-chat: called them over too", verdict, jpeg=jpeg, source=source)
        else:
            app.state.vision.sighting("Mid-chat: couldn't call them over right now", verdict, jpeg=jpeg, source=source)

    async def on_protect_person(camera: str, jpeg: bytes, event: dict) -> None:
        cfg = VisionConfig.from_dict(svc().settings.vision)
        cam_id = event.get("device") or event.get("deviceId") or ""
        zones = [z for z in cfg.zones.get(f"protect:{cam_id}", []) if len(z) == 4]
        vision = app.state.vision
        seen = []
        if cfg.faces and vision.engine.available():
            seen = await asyncio.to_thread(vision.engine.process, jpeg, zones)
            vision.recognise_external(seen)
        if quiet() and not app.state.conv.running:
            why = "Skelly is switched off" if not svc().link.connected else "quiet hours"
            vision.sighting(f"Skipped: {why}", jpeg=jpeg, source=f"{camera} camera")
        elif cfg.ai_check:  # a paid AI look at the picture
            if app.state.conv.running and cfg.call_over:
                await call_over_live(jpeg, zones, f"{camera} camera")  # mid-chat: invite them in
            else:
                await vision.maybe_visitor_from(jpeg, zones, f"{camera} camera")
        elif not app.state.conv.running:  # free: go by what UniFi itself detected
            await vision.maybe_visitor_from(jpeg, zones, f"{camera} camera", protect_verdict(event, len(seen)))
        else:
            vision.sighting("Skipped: mid-conversation", jpeg=jpeg, source=f"{camera} camera")

    @app.get("/api/sightings")
    async def sightings():
        """What the cameras saw lately and what Skelly did about each, newest first."""
        return list(reversed(app.state.vision.sightings))

    @app.get("/api/usage")
    async def usage_state():
        """What the paid AI services cost per day, and ElevenLabs credits left this period."""
        credits, err = None, None
        try:
            credits = await usage.elevenlabs_credits(app.state.vault.get("elevenlabs_api_key") or "")
        except Exception as exc:
            err = f"Couldn't read ElevenLabs credits: {exc}"
        return {"days": usage.summary(7), "elevenlabs": credits, "elevenlabs_error": err}

    @app.get("/api/protect")
    async def protect_state():
        p = app.state.protect
        cams, err = [], None
        if app.state.vault.get("protect_api_key") and p.host:
            try:
                cams = await p.cameras()
            except Exception as exc:
                err = f"Couldn't reach Protect: {exc}"
        return {**p.snapshot_state(), "cameras": cams, "camera_error": err,
                "key_set": bool(app.state.vault.get("protect_api_key"))}

    @app.post("/api/protect/find-skelly")
    async def protect_find_skelly():
        """Find Skelly in each chosen Protect camera's picture and ignore him there."""
        cfg = VisionConfig.from_dict(svc().settings.vision)
        found = {}
        for cam in cfg.protect_cameras:
            try:
                jpeg = await app.state.protect.snapshot(cam)
                box = await find_skelly(app.state.vault, jpeg)
            except Exception as exc:
                log.info("find skelly on %s failed: %r", cam, exc)
                continue
            if box:
                found[f"protect:{cam}"] = [box]
        if found:
            cfg.zones = {**cfg.zones, **found}
            svc().settings.vision = vars(cfg)
            svc().settings.save()
        return {"found": len(found), "of": len(cfg.protect_cameras)}

    # -- faces ---------------------------------------------------------------

    async def costume_watch() -> None:
        """While a conversation runs, look again every 20 s for costumes Skelly hasn't mentioned yet."""
        while True:
            await asyncio.sleep(20)
            conv, vision = app.state.conv, app.state.vision
            if not (conv.running and vision.state.running and vision.frame):
                if not conv.running:
                    app.state.costumes_mentioned = set()
                continue
            try:
                cfg = VisionConfig.from_dict(svc().settings.vision)
                frame, zones = vision.frame, cfg.active_zones
                if cfg.protect and cfg.preview_camera and app.state.protect.connected:
                    # A fresh full-resolution picture beats the preview for spotting costumes.
                    frame = await app.state.protect.snapshot(cfg.preview_camera)
                    zones = [z for z in cfg.zones.get(f"protect:{cfg.preview_camera}", []) if len(z) == 4]
                verdict = await describe(app.state.vault, frame, zones)
            except Exception as exc:
                log.info("costume check failed: %r", exc)
                continue
            new = [c for c in costume_names(verdict) if c.lower() not in app.state.costumes_mentioned]
            if new:
                app.state.costumes_mentioned |= {c.lower() for c in new}
                vision.state.costumes = costume_names(verdict)
                vision._publish()
                conv.add_context(f"New costume spotted: {', '.join(new)}. Work it into the conversation.")

    def known_here() -> list[str]:
        return sorted({f.name for f in app.state.vision.seen if f.name})

    async def on_known(person: dict) -> None:
        """Someone Skelly has met before just showed up."""
        name = person["name"]
        svc().bus.publish("known_visitor", {"id": person["id"], "name": name, "visits": person.get("visits")})
        conv = app.state.conv
        if conv.running:
            conv.add_context(f"{name} just joined; you've met them before ({person.get('visits', 1)} visits).")
        elif VisionConfig.from_dict(svc().settings.vision).auto_converse and not quiet():
            try:
                from .callouts import greeting

                await conv.start(context=f"Your friend {name} just walked up; you've met before.",
                                 opening=greeting(name, svc().profile.character), trigger=f"recognised {name}'s face")
            except (MissingKey, ValueError) as exc:
                log.info("greeting %s not started: %s", name, exc)

    def on_user_text(text: str) -> None:
        """Visitor said something: if they gave their name, remember their face."""
        from .faces import asked_name, heard_names

        if not VisionConfig.from_dict(svc().settings.vision).faces or not app.state.vision.state.running:
            return
        said = [t for t in app.state.conv.state.transcript if t.get("role") == "skelly"]
        names = heard_names(text, asked=bool(said) and asked_name(said[-1]["text"]))
        if names:
            log.info("heard names: %s", ", ".join(names))
        missed = [n for n in names if not try_learn(n)]
        for name in missed[:1]:
            # No face close enough yet: keep the name for a bit and save it once one shows up.
            app.state.pending_name = (name, time.monotonic())
            svc().bus.publish("face_pending", {"name": name})
            app.state.conv.add_context(f"(You heard their name is {name}, but you can't see their face clearly "
                                       "yet. Ask them to step closer and look right at you so you'll remember them.)")

    def try_learn(name: str) -> bool:
        person = app.state.vision.learn_name(name)
        if person:
            app.state.pending_name = None
            app.state.conv.add_context(f"(You can now recognise {name} by their face next time.)")
            log.info("learned the face of %s", name)
        return bool(person)

    async def pending_name_watch() -> None:
        """A name heard with no face in view: keep trying for 30 s while they step closer."""
        while True:
            await asyncio.sleep(1)
            pending = app.state.pending_name
            if not pending:
                continue
            name, since = pending
            if time.monotonic() - since > 30:
                app.state.pending_name = None
                log.info("never saw a face for %s", name)
                svc().bus.publish("face_missed", {"name": name})
            elif app.state.vision.state.running:
                try_learn(name)

    @app.get("/api/faces")
    async def faces():
        v = app.state.vision
        return {"available": v.engine.available(), "people": v.memory.public(), "seen": v.state.faces}

    @app.post("/api/faces/name")
    async def faces_name(body: dict):
        name = str(body.get("name", "")).strip()
        if not name:
            raise HTTPException(400, "Give a name")
        index = body.get("index")
        person = app.state.vision.learn_name(name, int(index) if index is not None else None)
        if not person:
            raise HTTPException(404, "No face to name right now")
        return app.state.vision.memory.public()

    @app.put("/api/faces/{person_id}")
    async def faces_rename(person_id: str, body: dict):
        app.state.vision.memory.rename(person_id, str(body.get("name", "")))
        return app.state.vision.memory.public()

    @app.delete("/api/faces/{person_id}")
    async def faces_forget(person_id: str):
        app.state.vision.memory.forget(person_id)
        return app.state.vision.memory.public()

    @app.delete("/api/faces")
    async def faces_forget_all():
        app.state.vision.memory.forget(None)
        return []

    # -- recordings --------------------------------------------------------------

    async def start_recording(cfg, sink: str | None) -> None:
        if not cfg.record:
            return
        vcfg = VisionConfig.from_dict(svc().settings.vision)
        rtsp = app.state.vault.get("rtsp_url") if vcfg.source == "rtsp" else None
        if vcfg.source == "protect" and vcfg.preview_camera:
            try:  # record the camera's own stream, opened only for this conversation
                rtsp = await app.state.protect.rtsps(vcfg.preview_camera, "high")
            except Exception as exc:
                log.info("no Protect stream to record: %r", exc)
        frames = app.state.vision.frames() if not rtsp and app.state.vision.state.running else None
        await asyncio.to_thread(recorder.prune, cfg.keep_days)
        path = await app.state.recorder.start(rtsp=rtsp, frames=frames, mic=cfg.mic or None, voice_sink=sink)
        if path:
            svc().bus.publish("recording", {"recording": True, "name": path.name})

    async def stop_recording(transcript: list[dict]) -> None:
        if app.state.recorder.recording:
            saved = await app.state.recorder.stop(transcript)
            svc().bus.publish("recording", {"recording": False, "saved": saved})

    @app.get("/api/recordings")
    async def recordings_list():
        return await asyncio.to_thread(recorder.recordings)

    @app.get("/api/recordings/{name}")
    async def recording_file(name: str, download: bool = False):
        path = recorder.recording_path(name)
        if not path:
            raise HTTPException(404, "No such recording")
        return FileResponse(path, media_type="video/mp4", filename=name if download else None)

    @app.get("/api/recordings/{name}/transcript")
    async def recording_transcript(name: str):
        path = recorder.recording_path(name)
        if not path:
            raise HTTPException(404, "No such recording")
        try:
            return __import__("json").loads(path.with_suffix(".json").read_text()).get("transcript", [])
        except (OSError, ValueError):
            return []

    @app.get("/api/snaps/{name}")
    async def snap_file(name: str):
        path = app.state.snaps.path(name)
        if not path:
            raise HTTPException(404, "No such picture")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=604800"})

    @app.delete("/api/recordings/{name}")
    async def recording_delete(name: str):
        if not recorder.delete(name):
            raise HTTPException(404, "No such recording")
        return await asyncio.to_thread(recorder.recordings)

    # -- system ------------------------------------------------------------------

    @app.get("/api/system")
    async def system_status():
        return system.status()

    @app.get("/api/version")
    async def version(check: bool = False):
        return {**await updates.status(force=check), "auto_update": svc().settings.auto_update}

    @app.post("/api/update")
    async def update_now():
        try:
            await updates.install_now()
        except Exception as exc:
            raise HTTPException(502, "Couldn't start the update. Run get.sh on the mini PC once to set up updates.") \
                from exc
        return {"started": True}

    @app.post("/api/system/restart/{part}")
    async def system_restart(part: str):
        """Restart audio, Bluetooth, the camera or the app, or reboot the mini PC."""
        s = svc()
        try:
            if part == "audio":
                await app.state.conv.stop()
                await system.restart_audio()
            elif part == "bluetooth":
                # The app's Bluetooth library keeps stale state across a bluetoothd restart and
                # stops finding Skelly, so the app restarts too and connects afresh.
                await app.state.conv.stop()
                await app.state.recorder.stop()
                await s.link.disconnect()
                await system.restart_bluetooth()
                system.restart_app_soon()
            elif part == "camera":
                await app.state.vision.stop()
                await app.state.vision.start()
            elif part == "app":
                system.restart_app_soon()
            elif part == "reboot":
                await app.state.conv.stop()
                await app.state.recorder.stop()
                asyncio.get_running_loop().call_later(0.5, lambda: asyncio.ensure_future(system.reboot()))
            else:
                raise HTTPException(404, "Unknown part")
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(502, f"Restart failed: {exc}") from exc
        return {"ok": True, "part": part}

    # -- voice test ----------------------------------------------------------------

    @app.get("/api/voices")
    async def voice_options():
        return {"options": voices.OPTIONS, "kokoro_ready": voices.kokoro_ready()}

    @app.post("/api/voices/test")
    async def voice_test(body: dict):
        """Say a line in one voice through whatever speakers Skelly is using."""
        text = str(body.get("text") or "").strip()[:400] or "Well hello there! Come closer, I don't bite... much."
        cfg = ConversationConfig.from_dict(svc().settings.conversation)
        try:
            pcm, rate, took = await voices.synth(str(body.get("voice")), text,
                                                 eleven_key=app.state.vault.get("elevenlabs_api_key"),
                                                 eleven_voice=cfg.elevenlabs_voice_id,
                                                 speed=float(body.get("speed") or 1.15),
                                                 agent_id=cfg.elevenlabs_agent_id or None)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502, f"Couldn't make that voice: {exc}") from exc
        try:
            sink = await resolve_output()
        except (ConnectionError, LookupError, RuntimeError) as exc:
            raise HTTPException(409, str(exc)) from exc
        out = audio_io.Speaker(sink, svc().settings.audio.get("out_gain", 100) / 100)
        await out.play(pcm, rate)
        await out.wait_done()
        await out.close()
        return {"made_in_s": round(took, 2), "seconds": round(len(pcm) / 2 / rate, 1)}

    @app.post("/api/speaker/connect")
    async def connect_live_speaker():
        try:
            sink = await skelly_sink()
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(502, f"Pairing failed: {exc}") from exc
        return {"sink": sink, "address": svc().settings.live_speaker}

    @app.get("/api/audio/devices")
    async def audio_devices():
        return await audio_io.list_devices()

    # -- API key vault -----------------------------------------------------------

    @app.get("/api/vault")
    async def vault_list():
        return app.state.vault.public()

    @app.put("/api/vault/{name}")
    async def vault_set(name: str, body: SecretBody):
        try:
            app.state.vault.set(name, body.value)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return app.state.vault.public()

    @app.delete("/api/vault/{name}")
    async def vault_delete(name: str):
        try:
            app.state.vault.set(name, None)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return app.state.vault.public()

    # -- conversation ----------------------------------------------------------

    @app.get("/api/conversation")
    async def conversation_state():
        cfg = for_character(ConversationConfig.from_dict(svc().settings.conversation), svc().profile)
        return {"state": app.state.conv.snapshot(), "config": vars(cfg)}

    @app.put("/api/conversation/config")
    async def conversation_config(body: dict):
        """Saves the page; with an ElevenLabs agent picked, its prompt and first line stay in sync.

        Picking a different agent pulls its prompt, first line and voice into the page; editing
        the personality or first line while on ElevenLabs writes them back to the agent.
        """
        old = ConversationConfig.from_dict(svc().settings.conversation)
        cfg = ConversationConfig.from_dict({**svc().settings.conversation, **body})
        sync = None
        key = app.state.vault.get("elevenlabs_api_key")
        if key and cfg.elevenlabs_agent_id and cfg.provider == "elevenlabs":
            try:
                if cfg.elevenlabs_agent_id != old.elevenlabs_agent_id or old.provider != "elevenlabs":
                    agent = await elevenlabs_agent(key, cfg.elevenlabs_agent_id)
                    cfg.prompt = agent["prompt"] or cfg.prompt
                    cfg.first_message = agent["first_message"]
                    cfg.elevenlabs_voice_id = agent["voice_id"] or cfg.elevenlabs_voice_id
                    sync = f"Loaded {agent['name'] or 'the agent'}'s prompt from ElevenLabs"
                elif (cfg.prompt, cfg.first_message) != (old.prompt, old.first_message):
                    await elevenlabs_update_agent(key, cfg.elevenlabs_agent_id, prompt=cfg.prompt,
                                                  first_message=cfg.first_message)
                    sync = "Saved to ElevenLabs"
            except RuntimeError as exc:
                sync = f"Couldn't sync with ElevenLabs: {exc}"
        svc().settings.conversation = vars(cfg)
        svc().settings.save()
        return {**vars(cfg), "sync": sync}

    @app.post("/api/elevenlabs/pull")
    async def eleven_pull():
        """Fetch the picked agent's current prompt, first line and voice into the page."""
        cfg = ConversationConfig.from_dict(svc().settings.conversation)
        if not cfg.elevenlabs_agent_id:
            raise HTTPException(400, "Pick an ElevenLabs agent first")
        agent = await eleven(elevenlabs_agent(eleven_key(), cfg.elevenlabs_agent_id))
        cfg.prompt = agent["prompt"] or cfg.prompt
        cfg.first_message = agent["first_message"]
        cfg.elevenlabs_voice_id = agent["voice_id"] or cfg.elevenlabs_voice_id
        svc().settings.conversation = vars(cfg)
        svc().settings.save()
        return vars(cfg)

    def eleven_key() -> str:
        key = app.state.vault.get("elevenlabs_api_key")
        if not key:
            raise HTTPException(400, "Add your ElevenLabs API key in Settings > API keys first.")
        return key

    async def eleven(coro):
        try:
            return await coro
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.get("/api/elevenlabs/agents")
    async def eleven_agents():
        return await eleven(elevenlabs_agents(eleven_key()))

    @app.get("/api/elevenlabs/voices")
    async def eleven_voices():
        return await eleven(elevenlabs_voices(eleven_key()))

    @app.post("/api/elevenlabs/agents")
    async def eleven_create_agent():
        cfg = for_character(ConversationConfig.from_dict(svc().settings.conversation), svc().profile)
        agent = await eleven(elevenlabs_create_agent(eleven_key(), cfg))
        svc().settings.conversation = {**vars(cfg), "elevenlabs_agent_id": agent["id"]}
        svc().settings.save()
        return agent

    @app.post("/api/conversation/start")
    async def conversation_start():
        here = known_here()
        v = app.state.vision.state
        recent = v.last_visitor_at and __import__("time").time() - v.last_visitor_at < 120
        parts = [f"People you recognise standing here: {', '.join(here)}." if here else "",
                 costume_hint(v.costumes).strip() if recent else ""]
        app.state.costumes_mentioned = {c.lower() for c in v.costumes} if recent else set()
        try:
            return await app.state.conv.start(context=" ".join(p for p in parts if p) or None)
        except ValueError as exc:  # MissingKey included
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/conversation/stop")
    async def conversation_stop():
        return await app.state.conv.stop()

    # -- vision ------------------------------------------------------------------

    @app.get("/api/vision")
    async def vision_state():
        return {"state": app.state.vision.snapshot(), "cameras": list_cameras(), "ignorable": IGNORABLE,
                "config": {**vars(VisionConfig()), **svc().settings.vision}}

    @app.put("/api/vision/config")
    async def vision_config(body: dict):
        cfg = VisionConfig.from_dict({**svc().settings.vision, **body})
        svc().settings.vision = vars(cfg)
        svc().settings.save()
        if app.state.vision.state.running:  # pick up the new source/settings straight away
            await app.state.vision.stop()
            await app.state.vision.start()
        return vars(cfg)

    @app.post("/api/vision/start")
    async def vision_start():
        try:
            return await app.state.vision.start()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/vision/stop")
    async def vision_stop():
        return await app.state.vision.stop()

    @app.get("/api/vision/snapshot.jpg")
    async def vision_snapshot():
        if not app.state.vision.frame:
            raise HTTPException(404, "No picture yet")
        return Response(app.state.vision.frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/vision/stream")
    async def vision_stream():
        async def body():
            async for jpg in app.state.vision.frames():
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
        return StreamingResponse(body(), media_type="multipart/x-mixed-replace; boundary=frame",
                                 headers={"Cache-Control": "no-store"})

    async def save_zones(zones: list[list[float]]) -> dict:
        cfg = VisionConfig.from_dict(svc().settings.vision)
        clean = [[round(min(max(v, 0.0), 1.0), 4) for v in z] for z in zones if len(z) == 4 and z[2] > 0 and z[3] > 0]
        cfg.zones = {**cfg.zones, cfg.camera_key: clean}
        svc().settings.vision = vars(cfg)
        svc().settings.save()
        if app.state.vision.state.running:  # the motion mask is built when capture starts
            await app.state.vision.stop()
            await app.state.vision.start()
        return vars(cfg)

    @app.put("/api/vision/zones")
    async def vision_zones(body: ZonesBody):
        return await save_zones(body.zones)

    @app.post("/api/vision/find-skelly")
    async def vision_find_skelly():
        if not app.state.vision.frame:
            raise HTTPException(409, "Start the camera first")
        try:
            box = await find_skelly(app.state.vault, app.state.vision.frame)
        except Exception as exc:
            raise HTTPException(502, f"The AI couldn't look: {exc}") from exc
        if box is None:
            raise HTTPException(404, "Couldn't spot Skelly in this camera's picture. Draw the area by hand instead.")
        cfg = VisionConfig.from_dict(svc().settings.vision)
        return await save_zones([*cfg.active_zones, box])

    @app.post("/api/vision/describe")
    async def vision_describe():
        if not app.state.vision.frame:
            raise HTTPException(409, "Start the camera first")
        cfg = VisionConfig.from_dict(svc().settings.vision)
        try:
            verdict = await describe(app.state.vault, app.state.vision.frame, cfg.active_zones)
        except Exception as exc:
            raise HTTPException(502, f"The AI couldn't look: {exc}") from exc
        if verdict is None:
            raise HTTPException(400, "Add an Anthropic or OpenAI API key in Settings > API keys first.")
        return verdict

    # -- fog machine -----------------------------------------------------------------

    async def fog_camera_watch() -> None:
        """When the fog meter watches a Protect camera of its own, fetch a picture every few seconds.

        Only while the fog meter is needed: topping up is on, or the Fog page is open.
        """
        import cv2
        import numpy as np

        from .vision import THUMB_H, THUMB_W

        fog = app.state.fog
        while True:
            await asyncio.sleep(3)
            cfg = fog.cfg
            if not cfg.camera or not (cfg.top_up and cfg.enabled or time.monotonic() - fog.viewed_at < 30):
                continue
            if not (app.state.vault.get("protect_api_key") and app.state.protect.host):
                continue
            try:
                jpeg = await app.state.protect.snapshot(cfg.camera, high=False)
                img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
                if img is None:
                    continue
                fog.frame = jpeg
                grey = cv2.resize(img, (THUMB_W, THUMB_H), interpolation=cv2.INTER_AREA)
                fog.thumb(grey.tobytes(), THUMB_W, THUMB_H, cfg.camera)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.info("fog camera picture failed: %r", exc)
                await asyncio.sleep(10)

    def fog_view() -> dict:
        return {"state": app.state.fog.snapshot(), "config": vars(app.state.fog.cfg), "kinds": FOG_KINDS}

    @app.get("/api/fog")
    async def fog_state():
        return fog_view()

    @app.put("/api/fog/config")
    async def fog_config(body: dict):
        body.pop("clear_detail", None)  # only Calibrate sets this
        old = app.state.fog.cfg
        cfg = FogConfig.from_dict({**svc().settings.fog, **body})
        if cfg.camera != old.camera:
            fog = app.state.fog
            fog.frame, fog._detail, fog.state.level = None, None, None
            if "zone" not in body:
                cfg.zone = []  # a different camera sees a different picture
        if cfg.zone != old.zone or cfg.camera != old.camera:
            cfg.clear_detail = 0.0  # a new spot needs a new "no fog" reference
        svc().settings.fog = vars(cfg)
        svc().settings.save()
        return fog_view()

    @app.get("/api/fog/snapshot.jpg")
    async def fog_snapshot():
        """The fog camera's latest picture, for drawing the fog area."""
        fog = app.state.fog
        fog.viewed_at = time.monotonic()
        jpeg = fog.frame if fog.cfg.camera else app.state.vision.frame
        if fog.cfg.camera and not jpeg:
            try:
                jpeg = fog.frame = await app.state.protect.snapshot(fog.cfg.camera, high=False)
            except Exception:
                jpeg = None
        if not jpeg:
            raise HTTPException(404, "No picture yet")
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/api/fog/puff")
    async def fog_puff(body: dict | None = None):
        seconds = (body or {}).get("seconds")
        return await guarded(app.state.fog.puff(float(seconds) if seconds else None))

    @app.post("/api/fog/stop")
    async def fog_stop():
        return await guarded(app.state.fog.stop())

    @app.post("/api/fog/check")
    async def fog_check():
        return {"reply": await guarded(app.state.fog.check())}

    @app.post("/api/fog/calibrate")
    async def fog_calibrate():
        try:
            clear = app.state.fog.calibrate()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        svc().settings.fog = {**vars(app.state.fog.cfg), "clear_detail": clear}
        svc().settings.save()
        return fog_view()

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    # -- UI password ---------------------------------------------------------------

    def signed_in(request: Request) -> bool:
        return auth.valid_session(request.cookies.get(COOKIE))

    def with_session(resp: Response) -> Response:
        resp.set_cookie(COOKIE, auth.new_session(), max_age=SESSION_DAYS * 86400, httponly=True, samesite="strict")
        return resp

    @app.get("/api/auth/status")
    async def auth_status(request: Request):
        return {"password": auth.enabled, "signed_in": signed_in(request), "setup_done": svc().settings.setup_done}

    @app.post("/api/auth/login")
    async def login(body: LoginBody, request: Request):
        who = request.client.host if request.client else "?"
        if auth.locked_out(who):
            raise HTTPException(429, "Too many wrong tries. Wait five minutes and try again.")
        if not auth.check(body.password):
            auth.failed(who)
            await asyncio.sleep(1)
            raise HTTPException(401, "That password isn't right")
        return with_session(JSONResponse({"ok": True})) if auth.enabled else {"ok": True}

    @app.post("/api/auth/logout")
    async def logout():
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE)
        return resp

    @app.put("/api/auth/password")
    async def set_password(body: PasswordBody, request: Request):
        if auth.enabled and not auth.check(body.current):
            await asyncio.sleep(1)
            raise HTTPException(401, "The current password isn't right")
        try:
            auth.set_password(body.new)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        resp = JSONResponse({"password": auth.enabled})
        return with_session(resp) if auth.enabled else resp

    @app.get("/api/profiles")
    async def profiles():
        return [p.to_dict() for p in PROFILES]

    @app.get("/api/state")
    async def state():
        return full_snapshot()

    @app.get("/api/settings")
    async def get_settings():
        return svc().settings.public()

    @app.patch("/api/settings")
    async def patch_settings(body: SettingsBody):
        try:
            return svc().update_settings(**body.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    # -- wallpaper behind the page ----------------------------------------------

    def wallpaper_path() -> Path:
        return data_dir() / "wallpaper.img"

    @app.post("/api/wallpaper")
    async def wallpaper_upload(file: Annotated[UploadFile, File()]):
        """Use your own picture as the page background. Kept on the mini PC only."""
        raw = await file.read(WALLPAPER_MAX + 1)
        if len(raw) > WALLPAPER_MAX:
            raise HTTPException(413, "That picture is too big (15 MB max)")
        if not _image_type(raw):
            raise HTTPException(400, "That isn't a picture Skelly can show (use JPG, PNG, WebP or GIF)")
        path = wallpaper_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        await asyncio.to_thread(tmp.write_bytes, raw)
        tmp.replace(path)
        return svc().update_settings(wallpaper="custom")

    @app.get("/api/wallpaper/custom")
    async def wallpaper_custom():
        path = wallpaper_path()
        if not path.exists():
            raise HTTPException(404, "No picture uploaded")
        return FileResponse(path, media_type=_image_type(path.read_bytes()[:16]) or "application/octet-stream",
                            headers={"Cache-Control": "no-cache"})

    @app.delete("/api/wallpaper/custom")
    async def wallpaper_remove():
        wallpaper_path().unlink(missing_ok=True)
        st = svc().settings
        return svc().update_settings(wallpaper="classic") if st.wallpaper == "custom" else st.public()

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

    @app.put("/api/files/{serial}/performance")
    async def set_performance(serial: int, body: PerformanceBody):
        perf = body.model_dump()
        if perf["color"]:
            perf["color"] = "#" + perf["color"].lstrip("#").lower()
        return await guarded(svc().set_performance(serial, perf))

    @app.delete("/api/files/{serial}/performance")
    async def clear_performance(serial: int):
        try:
            svc().clear_performance(serial)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True}

    @app.get("/api/playlist")
    async def playlist_state():
        pl = app.state.playlist
        return {"config": pl.config, "status": pl.snapshot()}

    @app.put("/api/playlist")
    async def playlist_config(body: PlaylistBody):
        return app.state.playlist.set_config(body.model_dump())

    @app.post("/api/playlist/play")
    async def playlist_play():
        return await guarded(app.state.playlist.play())

    @app.post("/api/playlist/stop")
    async def playlist_stop():
        return await app.state.playlist.stop()

    @app.post("/api/playlist/skip")
    async def playlist_skip():
        return app.state.playlist.skip()

    # -- the yard and display, from photos ------------------------------------

    @app.get("/api/scene")
    async def scene_list():
        return app.state.scene.load()

    @app.post("/api/scene")
    async def scene_add(file: Annotated[UploadFile, File()]):
        """Add a photo of the yard or display; the AI describes it for Skelly."""
        raw = await file.read(25 * 1024 * 1024 + 1)
        if len(raw) > 25 * 1024 * 1024:
            raise HTTPException(413, "That photo is too big (25 MB max)")
        try:
            item = await asyncio.to_thread(app.state.scene.add, raw)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return await scene_describe(item["id"])

    @app.post("/api/scene/{photo_id}/describe")
    async def scene_describe(photo_id: str):
        sc = app.state.scene
        if not sc.path(photo_id).exists():
            raise HTTPException(404, "No such photo")
        try:
            text = await scene.describe_photo(app.state.vault, sc.path(photo_id).read_bytes())
        except Exception as exc:
            log.info("describing a scene photo failed: %r", exc)
            text = ""
        item = sc.update(photo_id, description=text) if text else next(
            (it for it in sc.load() if it["id"] == photo_id), {})
        if not text:
            item = {**item, "warning": "Couldn't describe it automatically (needs a Claude or OpenAI key)."
                                       " Write what's in it yourself."}
        return item

    @app.put("/api/scene/{photo_id}")
    async def scene_edit(photo_id: str, body: dict):
        try:
            return app.state.scene.update(photo_id, **body)
        except KeyError as exc:
            raise HTTPException(404, "No such photo") from exc

    @app.delete("/api/scene/{photo_id}")
    async def scene_remove(photo_id: str):
        app.state.scene.remove(photo_id)
        return app.state.scene.load()

    @app.get("/api/scene/{photo_id}.jpg")
    async def scene_photo(photo_id: str):
        path = app.state.scene.path(photo_id)
        if not path.exists():
            raise HTTPException(404, "No such photo")
        return FileResponse(path, media_type="image/jpeg")

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
            await ws.send_json({"type": "snapshot", "data": full_snapshot()})
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
