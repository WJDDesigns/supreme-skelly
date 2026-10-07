"""Device service: owns the link, the device state and the event stream.

Everything runs on one asyncio loop. Writes go through a single paced queue,
so the UI, playlists and (later) Live AI can never interleave or flood the
device. If the link drops it is reconnected with backoff.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from . import protocol as proto
from .link import Found, Link
from .profiles import UNKNOWN, Profile, by_ble_name
from .settings import Settings

log = logging.getLogger(__name__)

WRITE_GAP_S = 0.04  # device drops back-to-back write-without-response packets
REPLY_TIMEOUT_S = 3.0
AUTOCONNECT_INTERVAL_S = 10.0
AUTOCONNECT_SCAN_S = 8.0
# The firmware resets lights/eyes to the sound's stored scene when playback
# starts, so a locked look is re-applied shortly after each START.
REAPPLY_DELAY_S = 0.3


class EventBus:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, kind: str, data: Any = None) -> None:
        msg = {"type": kind, "data": data, "ts": time.time()}
        for q in list(self._subs):
            if q.full():  # slow client: drop its oldest message rather than block the device
                q.get_nowait()
            q.put_nowait(msg)


@dataclass
class DeviceState:
    status: str = "disconnected"  # disconnected | scanning | connecting | connected | reconnecting
    address: str | None = None
    name: str | None = None
    profile: str = UNKNOWN.key
    version: str | None = None
    volume: int | None = None
    bt_name: str | None = None
    pin: str | None = None
    free_kb: int | None = None
    live_mode: bool = False
    live: dict | None = None
    files: list[dict] = field(default_factory=list)
    playing: int | None = None
    error: str | None = None


class SkellyService:
    def __init__(self, link: Link, bus: EventBus | None = None, *, auto_reconnect: bool = True,
                 settings: Settings | None = None) -> None:
        self.link = link
        self.bus = bus or EventBus()
        self.settings = settings or Settings()
        self._autoconnector: asyncio.Task | None = None
        self._user_disconnected = False
        self._reapply: asyncio.Task | None = None
        self.look: dict = self.settings.look or {"lights": {}, "eye": None}
        self.state = DeviceState()
        self.profile: Profile = UNKNOWN
        self.auto_reconnect = auto_reconnect
        self._queue: asyncio.Queue[tuple[bytes, asyncio.Future | None]] = asyncio.Queue()
        self._waiters: dict[str, list[asyncio.Future]] = {}
        self._writer: asyncio.Task | None = None
        self._reconnector: asyncio.Task | None = None
        self._wanted: str | None = None  # address the user asked for
        self._files_buf: dict[int, dict] = {}
        self._files_done: asyncio.Event | None = None
        link.on_notify = self._on_notify
        link.on_disconnect = self._on_link_lost

    # -- lifecycle --------------------------------------------------------------

    async def start(self) -> None:
        self._writer = asyncio.create_task(self._write_loop(), name="skelly-writer")

    def start_autoconnect(self) -> None:
        if not self._autoconnector or self._autoconnector.done():
            self._autoconnector = asyncio.create_task(self._autoconnect_loop(), name="skelly-autoconnect")

    async def stop(self) -> None:
        self._wanted = None
        for t in (self._writer, self._reconnector, self._autoconnector):
            if t:
                t.cancel()
        await self.link.disconnect()

    def snapshot(self) -> dict:
        return {"device": asdict(self.state), "profile": self.profile.to_dict(),
                "settings": self.settings.public(), "look": self.look}

    def update_settings(self, **changes: Any) -> dict:
        for k, v in changes.items():
            setattr(self.settings, k, v)
        self.settings.save()
        if changes.get("auto_connect"):
            self._user_disconnected = False
        self.bus.publish("settings", self.settings.public())
        return self.settings.public()

    def _set(self, **changes: Any) -> None:
        for k, v in changes.items():
            setattr(self.state, k, v)
        self.bus.publish("state", asdict(self.state))

    # -- connection -------------------------------------------------------------

    async def scan(self, timeout: float = 6.0) -> list[Found]:
        prev = self.state.status
        self._set(status="scanning", error=None)
        try:
            return await self.link.scan(timeout)
        finally:
            self._set(status=prev if prev != "scanning" else "disconnected")

    async def connect(self, address: str, name: str | None = None) -> None:
        self._wanted = address
        self._user_disconnected = False
        await self._connect_once(address, name)

    async def _autoconnect_loop(self) -> None:
        """Plug and play: keep looking for the last (or any) supported prop until connected."""
        while True:
            idle = self.state.status == "disconnected" and not self.link.connected
            if idle and self.settings.auto_connect and not self._user_disconnected:
                try:
                    found = await self.scan(AUTOCONNECT_SCAN_S)
                    pick = next((f for f in found if f.address == self.settings.last_address), None)
                    pick = pick or (found[0] if found else None)
                    if pick and not self._user_disconnected and self.state.status == "disconnected":
                        log.info("auto-connecting to %s (%s)", pick.name, pick.address)
                        await self.connect(pick.address, pick.name)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.info("auto-connect attempt failed: %s", exc)
            await asyncio.sleep(AUTOCONNECT_INTERVAL_S)

    async def _connect_once(self, address: str, name: str | None) -> None:
        self._set(status="connecting", address=address, error=None)
        try:
            await self.link.connect(address)
        except Exception as exc:
            self._set(status="disconnected", error=f"Couldn't connect: {exc}")
            raise
        self.profile = by_ble_name(name or self.state.name)
        self._set(status="connected", name=name or self.state.name, profile=self.profile.key)
        self.bus.publish("profile", self.profile.to_dict())
        if (self.settings.last_address, self.settings.last_name) != (address, self.state.name):
            self.settings.last_address, self.settings.last_name = address, self.state.name
            self.settings.save()
        asyncio.create_task(self._after_connect())

    async def _after_connect(self) -> None:
        await self.refresh_all()
        if self.settings.keep_look:
            await self._apply_look_safely()
        if self.settings.auto_live_mode and not self.state.live_mode and self.link.connected:
            try:
                await self.enable_live_mode()
            except (TimeoutError, ConnectionError) as exc:
                log.info("auto Live Mode failed: %r", exc)

    async def disconnect(self) -> None:
        self._wanted = None
        self._user_disconnected = True  # don't auto-connect straight back
        if self._reconnector:
            self._reconnector.cancel()
        await self.link.disconnect()
        self._set(status="disconnected", live_mode=False)

    def _on_link_lost(self) -> None:
        if self.state.status == "disconnected":
            return
        log.warning("link lost")
        self._fail_waiters(ConnectionError("disconnected"))
        if self._wanted and self.auto_reconnect:
            self._set(status="reconnecting", live_mode=False)
            if not self._reconnector or self._reconnector.done():
                self._reconnector = asyncio.create_task(self._reconnect_loop())
        else:
            self._set(status="disconnected", live_mode=False)

    async def _reconnect_loop(self) -> None:
        delay = 1.0
        while self._wanted and not self.link.connected:
            await asyncio.sleep(delay)
            try:
                await self._connect_once(self._wanted, self.state.name)
                return
            except Exception:
                self._set(status="reconnecting")
                delay = min(delay * 2, 30.0)

    # -- writes and replies -----------------------------------------------------

    async def _write_loop(self) -> None:
        while True:
            data, done = await self._queue.get()
            try:
                await self.link.write(data)
                if done and not done.done():
                    done.set_result(None)
            except Exception as exc:
                if done and not done.done():
                    done.set_exception(exc)
            await asyncio.sleep(WRITE_GAP_S)

    def _require_connected(self) -> None:
        if not self.link.connected:
            raise ConnectionError("Not connected to a device")

    async def send(self, data: bytes) -> None:
        self._require_connected()
        done = asyncio.get_running_loop().create_future()
        await self._queue.put((data, done))
        await done

    async def request(self, data: bytes, reply: str, timeout: float = REPLY_TIMEOUT_S) -> dict:
        fut = asyncio.get_running_loop().create_future()
        self._waiters.setdefault(reply, []).append(fut)
        try:
            await self.send(data)
            return await asyncio.wait_for(fut, timeout)
        finally:
            lst = self._waiters.get(reply, [])
            if fut in lst:
                lst.remove(fut)

    def _fail_waiters(self, exc: Exception) -> None:
        for lst in self._waiters.values():
            for f in lst:
                if not f.done():
                    f.set_exception(exc)
        self._waiters.clear()

    def _on_notify(self, raw: bytes) -> None:
        ev = proto.parse(raw)
        if not ev or ev.kind == "keepalive":
            return
        self._apply(ev)
        for f in self._waiters.pop(ev.kind, []):
            if not f.done():
                f.set_result(ev.data)
        self.bus.publish("device_event", {"kind": ev.kind, **ev.data})

    def _apply(self, ev: proto.Event) -> None:
        d = ev.data
        if ev.kind == "version":
            self._set(version=d["version"])
        elif ev.kind == "volume":
            self._set(volume=d["volume"])
        elif ev.kind == "bt_name":
            self._set(bt_name=d["name"])
        elif ev.kind == "params":
            self._set(pin=d["pin"], bt_name=d["name"] or self.state.bt_name)
        elif ev.kind == "capacity":
            self._set(free_kb=d["free_kb"])
        elif ev.kind == "live_state":
            self._set(live=d)
        elif ev.kind == "live_enabled":
            self._set(live_mode=bool(d["status"]))
        elif ev.kind == "playback":
            self._set(playing=d["serial"] if d["playing"] else None)
            if d["playing"] and self.settings.keep_look:
                self._schedule_reapply()
        elif ev.kind == "file":
            self._files_buf[d["serial"]] = d
            if self._files_done and len(self._files_buf) >= d["total"]:
                self._files_done.set()

    # -- queries ----------------------------------------------------------------

    async def refresh_all(self) -> None:
        C = proto.Cmd
        for cmd, reply in ((C.QUERY_VERSION, "version"), (C.QUERY_PARAMS, "params"),
                           (C.QUERY_VOLUME, "volume"), (C.QUERY_CAPACITY, "capacity"),
                           (C.QUERY_LIVE, "live_state")):
            try:
                await self.request(proto.query(cmd), reply)
            except (TimeoutError, ConnectionError) as exc:
                log.info("query %02X got no reply: %r", cmd, exc)
        try:
            await self.refresh_files()
        except (TimeoutError, ConnectionError) as exc:
            log.info("file list failed: %r", exc)

    async def refresh_files(self, timeout: float = 6.0) -> list[dict]:
        self._files_buf = {}
        self._files_done = asyncio.Event()
        try:
            await self.send(proto.query(proto.Cmd.QUERY_FILES))
            try:
                await asyncio.wait_for(self._files_done.wait(), timeout)
            except TimeoutError:
                if not self._files_buf:
                    raise
        finally:
            self._files_done = None
        files = sorted(self._files_buf.values(), key=lambda f: f["serial"])
        self._set(files=files)
        return files

    # -- controls ---------------------------------------------------------------

    def movement_mask(self, keys: list[str]) -> int:
        bits = {m.key: m.bit for m in self.profile.movements}
        unknown = [k for k in keys if k not in bits]
        if unknown:
            raise ValueError(f"{self.profile.name} has no movement {', '.join(unknown)}")
        mask = 0
        for k in keys:
            mask |= bits[k]
        return mask & 0xFF

    async def set_movement(self, keys: list[str]) -> None:
        self._require_connected()
        await self.send(proto.movement(self.movement_mask(keys)))

    async def set_eye(self, value: int) -> None:
        await self.send(proto.eye(value))
        self.look["eye"] = value
        self._look_changed()

    def _channel(self, light: str | None) -> int:
        if light in (None, "all"):
            return proto.ALL_CHANNELS
        for li in self.profile.lights:
            if li.key == light:
                return li.channel
        raise ValueError(f"{self.profile.name} has no light '{light}'")

    async def set_light(self, light: str | None = None, *, mode: int | None = None,
                        brightness: int | None = None, rgb: tuple[int, int, int] | None = None,
                        cycle: bool = False, speed: int | None = None) -> None:
        self._require_connected()
        ch = self._channel(light)
        if mode is not None:
            await self.send(proto.light_mode(ch, mode))
        if brightness is not None:
            await self.send(proto.brightness(ch, brightness))
        if rgb is not None:
            await self.send(proto.color(ch, *rgb, cycle=cycle))
        if speed is not None:
            await self.send(proto.speed(ch, proto.ui_speed_to_device(speed)))
        key = light if light not in (None, "all") else "all"
        lights = self.look["lights"]
        if key == "all":
            # "All" overrides any per-light choice; keep only what was just set.
            prev = lights.get("all", {})
            lights.clear()
            lights["all"] = prev
        entry = lights.setdefault(key, {})
        for k, v in (("mode", mode), ("brightness", brightness), ("speed", speed)):
            if v is not None:
                entry[k] = v
        if rgb is not None:
            entry["rgb"] = list(rgb)
            entry["cycle"] = cycle
        self._look_changed()

    # -- keep this look ---------------------------------------------------------

    def _look_changed(self) -> None:
        if self.settings.keep_look:
            self.settings.look = self.look
            self.settings.save()
        self.bus.publish("look", self.look)

    def set_keep_look(self, keep: bool) -> dict:
        self.settings.keep_look = keep
        self.settings.look = self.look if keep else None
        self.settings.save()
        self.bus.publish("settings", self.settings.public())
        return self.settings.public()

    def _schedule_reapply(self) -> None:
        if self._reapply and not self._reapply.done():
            self._reapply.cancel()

        async def later() -> None:
            await asyncio.sleep(REAPPLY_DELAY_S)
            await self._apply_look_safely()

        self._reapply = asyncio.create_task(later())

    async def _apply_look_safely(self) -> None:
        try:
            await self.apply_look()
        except (ConnectionError, ValueError) as exc:
            log.info("couldn't re-apply look: %s", exc)

    async def apply_look(self, cluster: int = 0, filename: str = "") -> None:
        """Send the stored look live, or into one sound's saved scene when ``filename`` is given."""
        for key, e in self.look.get("lights", {}).items():
            if key == "all":
                # Per-sound scenes are stored per channel, so expand "all" there.
                chans = [li.channel for li in self.profile.lights] if filename else [proto.ALL_CHANNELS]
            else:
                chans = [self._channel(key)]
            for ch in chans:
                if "mode" in e:
                    await self.send(proto.light_mode(ch, e["mode"], cluster, filename))
                if "brightness" in e:
                    await self.send(proto.brightness(ch, e["brightness"], cluster, filename))
                if "rgb" in e:
                    await self.send(proto.color(ch, *e["rgb"], cycle=e.get("cycle", False),
                                                cluster=cluster, filename=filename))
                if "speed" in e:
                    await self.send(proto.speed(ch, proto.ui_speed_to_device(e["speed"]), cluster, filename))
        if self.look.get("eye") and self.profile.eyes:
            await self.send(proto.eye(self.look["eye"], cluster, filename))

    async def save_look_to_sounds(self) -> int:
        """Write the current look into every sound on the device, so it sticks even
        when Skelly plays sounds on his own (motion sensor, no controller running)."""
        self._require_connected()
        files = self.state.files or await self.refresh_files()
        for f in files:
            if f.get("name"):
                await self.apply_look(f["cluster"], f["name"])
        return sum(1 for f in files if f.get("name"))

    async def set_volume(self, value: int) -> None:
        await self.send(proto.volume(value))
        self._set(volume=value)

    async def enable_live_mode(self) -> None:
        try:
            await self.request(proto.enable_live_mode(), "live_enabled")
        except TimeoutError:
            self._set(live_mode=True)  # some firmware never acks; the speaker still appears

    async def play_file(self, serial: int, play: bool = True) -> None:
        await self.send(proto.play_file(serial, play))
