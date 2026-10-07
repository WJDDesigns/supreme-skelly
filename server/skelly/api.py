"""HTTP + WebSocket API, and the static web UI."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import audio, audio_io, recorder, speaker, system
from . import protocol as proto
from .conversation import (
    Conversation,
    ConversationConfig,
    MissingKey,
    elevenlabs_agent,
    elevenlabs_agents,
    elevenlabs_create_agent,
    elevenlabs_update_agent,
    elevenlabs_voices,
)
from .link import BleakLink, Link, SimulatedLink
from .meters import Meters
from .playlist import Playlist
from .profiles import PROFILES
from .service import SkellyService
from .settings import Settings
from .vault import Vault
from .vision import IGNORABLE, Vision, VisionConfig, costume_names, describe, find_skelly, list_cameras

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


class AddressBody(BaseModel):
    address: str


class VolumeSinkBody(BaseModel):
    sink: str
    volume: int = Field(ge=0, le=150)


class ZonesBody(BaseModel):
    zones: list[list[float]]


class SecretBody(BaseModel):
    value: str


class SettingsBody(BaseModel):
    auto_connect: bool | None = None
    auto_live_mode: bool | None = None


def make_link() -> Link:
    return SimulatedLink() if os.environ.get("SKELLY_SIMULATE") == "1" else BleakLink()


def create_app(
    link: Link | None = None, settings: Settings | None = None, *, autoconnect: bool | None = None,
    vault: Vault | None = None,
) -> FastAPI:
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
        app.state.recorder = recorder.Recorder()
        app.state.costumes_mentioned = set()
        costume_task = asyncio.create_task(costume_watch())
        app.state.conv.on_started = start_recording
        app.state.conv.on_ended = stop_recording
        app.state.meters = Meters(svc.bus, lambda: svc.settings.audio.get("mic", ""), meter_sink,
                                  lambda: svc.settings.audio.get("mic_gain", 100) / 100)
        if VisionConfig.from_dict(svc.settings.vision).start_on_boot:
            try:
                await app.state.vision.start()
            except ValueError as exc:
                log.info("camera not started: %s", exc)
        yield
        costume_task.cancel()
        await app.state.conv.stop()
        await app.state.recorder.stop()
        await app.state.vision.stop()
        await app.state.meters.stop()
        await app.state.playlist.stop()
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

    def full_snapshot() -> dict:
        return {**svc().snapshot(), "conversation": app.state.conv.snapshot(), "vision": app.state.vision.snapshot(),
                "playlist": app.state.playlist.snapshot()}

    # -- Skelly's Live speaker ---------------------------------------------------

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
            raise RuntimeError(audio_io.NO_SOUND_SERVER)
        names = tuple(dict.fromkeys([*s.profile.live_audio_names, *([s.state.bt_name] if s.state.bt_name else [])]))
        adapter = getattr(s.link, "adapter_in_use", None) or "hci0"
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
                    await speaker.connect_address(adapter_name(), mac)
                    await asyncio.sleep(2)
                except Exception as exc:
                    log.info("extra speaker %s not connected: %s", mac, exc)
            sinks.append(sink)
        have = {d["name"] for d in (await audio_io.list_devices())["speakers"]}
        live = [s for s in sinks if s and s in have]
        if not live:
            raise ConnectionError("None of the chosen speakers are connected")
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

    def costume_hint(costumes: list[str]) -> str:
        return (f" They're dressed as: {', '.join(costumes)}. Mention their costume in a fun, spooky way."
                if costumes else "")

    async def on_visitor(description: str | None, cfg: VisionConfig) -> None:
        costumes = list(app.state.vision.state.costumes)
        svc().bus.publish("visitor", {"description": description, "costumes": costumes,
                                      "ts": __import__("time").time()})
        app.state.costumes_mentioned = set(c.lower() for c in costumes)
        if cfg.auto_converse:
            ctx = (f"Someone just walked up. What the camera sees: {description}{costume_hint(costumes)}"
                   if description else None)
            try:
                await app.state.conv.start(context=ctx)
            except (MissingKey, ValueError) as exc:
                log.info("visitor conversation not started: %s", exc)

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
                verdict = await describe(app.state.vault, vision.frame, cfg.active_zones)
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
        elif VisionConfig.from_dict(svc().settings.vision).auto_converse:
            try:
                await conv.start(context=f"Your friend {name} just walked up; you've met before. Greet them by name.")
            except (MissingKey, ValueError) as exc:
                log.info("greeting %s not started: %s", name, exc)

    def on_user_text(text: str) -> None:
        """Visitor said something: if they gave their name, remember their face."""
        from .faces import heard_name

        if not VisionConfig.from_dict(svc().settings.vision).faces or not app.state.vision.state.running:
            return
        name = heard_name(text)
        if not name:
            return
        person = app.state.vision.learn_name(name)
        if person:
            app.state.conv.add_context(f"(You can now recognise {name} by their face next time.)")
            log.info("learned the face of %s", name)

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

    @app.delete("/api/recordings/{name}")
    async def recording_delete(name: str):
        if not recorder.delete(name):
            raise HTTPException(404, "No such recording")
        return await asyncio.to_thread(recorder.recordings)

    # -- system ------------------------------------------------------------------

    @app.get("/api/system")
    async def system_status():
        return system.status()

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
        return {"state": app.state.conv.snapshot(),
                "config": {**vars(ConversationConfig()), **svc().settings.conversation}}

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
        cfg = ConversationConfig.from_dict(svc().settings.conversation)
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

    @app.get("/api/health")
    async def health():
        return {"ok": True}

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
