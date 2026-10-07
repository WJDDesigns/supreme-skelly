"""Transport to the animatronic: real BLE (bleak) or an in-memory simulator."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from . import protocol as proto
from .profiles import PROFILES, is_supported

log = logging.getLogger(__name__)


@dataclass
class Found:
    address: str
    name: str
    rssi: int | None


class Link(Protocol):
    on_notify: Callable[[bytes], None] | None
    on_disconnect: Callable[[], None] | None

    async def scan(self, timeout: float) -> list[Found]: ...
    async def connect(self, address: str) -> None: ...
    async def disconnect(self) -> None: ...
    async def write(self, data: bytes) -> None: ...
    @property
    def connected(self) -> bool: ...
    @property
    def mtu(self) -> int: ...


async def _bluez_unstick(address: str | None = None) -> None:
    """Clear BlueZ state left behind when a previous run died mid-scan or mid-connect.

    BlueZ then answers every new scan or connect with "InProgress" until the
    stale discovery is stopped or the half-open device connection is dropped.
    """
    from dbus_fast import BusType
    from dbus_fast.aio import MessageBus

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        intro = await bus.introspect("org.bluez", "/org/bluez")
        for node in intro.nodes:
            path = f"/org/bluez/{node.name}"
            obj = bus.get_proxy_object("org.bluez", path, await bus.introspect("org.bluez", path))
            try:
                await obj.get_interface("org.bluez.Adapter1").call_stop_discovery()
            except Exception:  # not discovering: fine
                pass
            if address:
                dev = f"{path}/dev_{address.replace(':', '_').upper()}"
                try:
                    dobj = bus.get_proxy_object("org.bluez", dev, await bus.introspect("org.bluez", dev))
                    await dobj.get_interface("org.bluez.Device1").call_disconnect()
                except Exception:  # adapter doesn't know this device
                    pass
    finally:
        bus.disconnect()


def _in_progress(exc: Exception) -> bool:
    return "InProgress" in str(exc)


class BleakLink:
    """BLE via bleak. Works with BlueZ on Linux (also macOS/Windows for dev)."""

    def __init__(self, adapter: str | None = None) -> None:
        self.on_notify = None
        self.on_disconnect = None
        self._client = None
        # e.g. "hci1" to use a USB dongle instead of the built-in radio; None = system default.
        self.adapter = adapter or os.environ.get("SKELLY_BT_ADAPTER") or None
        self._kw = {"adapter": self.adapter} if self.adapter else {}

    @property
    def connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    @property
    def mtu(self) -> int:
        return getattr(self._client, "mtu_size", 23) or 23

    async def scan(self, timeout: float = 6.0) -> list[Found]:
        from bleak import BleakScanner

        try:
            found = await BleakScanner.discover(timeout=timeout, return_adv=True, **self._kw)
        except Exception as exc:
            if not _in_progress(exc):
                raise
            log.info("BlueZ was stuck mid-scan; clearing it and scanning again")
            await _bluez_unstick()
            found = await BleakScanner.discover(timeout=timeout, return_adv=True, **self._kw)
        out = [
            Found(dev.address, adv.local_name or dev.name or "", adv.rssi)
            for dev, adv in found.values()
            if is_supported(adv.local_name or dev.name)
        ]
        return sorted(out, key=lambda f: -(f.rssi or -999))

    async def connect(self, address: str) -> None:
        from bleak import BleakClient

        def _gone(_client) -> None:
            if self.on_disconnect:
                self.on_disconnect()

        client = BleakClient(address, disconnected_callback=_gone, timeout=15.0, **self._kw)
        try:
            await client.connect()
        except Exception as exc:
            if not _in_progress(exc):
                raise
            log.info("BlueZ was stuck mid-connect; clearing it and connecting again")
            await _bluez_unstick(address)
            client = BleakClient(address, disconnected_callback=_gone, timeout=15.0, **self._kw)
            await client.connect()
        await client.start_notify(proto.NOTIFY_UUID, lambda _c, data: self.on_notify and self.on_notify(bytes(data)))
        self._client = client

    async def release_stale(self, address: str) -> None:
        """Drop a link a previous run left open; Skelly stops advertising while it exists."""
        try:
            await _bluez_unstick(address)
        except Exception as exc:  # no BlueZ (macOS/Windows dev) or nothing to clear
            log.debug("release_stale: %s", exc)

    async def disconnect(self) -> None:
        client, self._client = self._client, None
        if client:
            try:
                await client.disconnect()
            except Exception as exc:  # already gone
                log.debug("disconnect: %s", exc)

    async def write(self, data: bytes) -> None:
        if not self._client:
            raise ConnectionError("not connected")
        await self._client.write_gatt_char(proto.WRITE_UUID, data, response=False)


class SimulatedLink:
    """Pretend Ultra Skelly for UI work and tests (SKELLY_SIMULATE=1)."""

    def __init__(self) -> None:
        self.on_notify = None
        self.on_disconnect = None
        self.sent: list[bytes] = []
        self._connected = False
        self.files = [("Spooky Laugh.mp3", 1, 1000), ("Welcome.mp3", 2, 2000), ("Boo.mp3", 3, 3000)]
        self.upload: dict | None = None  # in-progress transfer: name, size, chunks
        self.volume = 120
        # Mimic the firmware: playback resets the live colour to the sound's saved scene.
        self.live_rgb = (255, 0, 0)
        self.file_rgb: dict[str, tuple[int, int, int]] = {}

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def mtu(self) -> int:
        return 247

    async def scan(self, timeout: float = 6.0) -> list[Found]:
        await asyncio.sleep(0.2)
        return [Found("SIM:00:00:00:00:01", p.ble_names[0], -50 - i * 7) for i, p in enumerate(PROFILES)]

    async def connect(self, address: str) -> None:
        await asyncio.sleep(0.1)
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    def _reply(self, cmd: int, payload: bytes) -> None:
        frame = bytes([0xBB, cmd]) + payload
        asyncio.get_running_loop().call_later(0.02, lambda: self.on_notify and self.on_notify(frame + b"\x00"))

    async def write(self, data: bytes) -> None:
        if not self._connected:
            raise ConnectionError("not connected")
        self.sent.append(data)
        cmd = data[1]
        C = proto.Cmd
        if cmd == C.QUERY_VERSION:
            self._reply(cmd, bytes([68]))
        elif cmd == C.QUERY_VOLUME:
            self._reply(cmd, bytes([self.volume]))
        elif cmd == C.SET_VOLUME:
            self.volume = data[2]
        elif cmd == C.QUERY_BT_NAME:
            name = b"Ultra Skelly V2 Live"
            self._reply(cmd, bytes([len(name)]) + name)
        elif cmd == C.QUERY_CAPACITY:
            self._reply(cmd, (2048).to_bytes(4, "big") + bytes([len(self.files), 1]))
        elif cmd == C.QUERY_PARAMS:
            name = b"Ultra Skelly V2 Live"
            self._reply(cmd, bytes([1, 1, 0, 0, 0, 0]) + b"1234" + bytes(9) + bytes(7) + bytes([len(name)]) + name)
        elif cmd == C.QUERY_LIVE:
            light = bytes([1, 255, 255, 0, 0, 0, 0])
            self._reply(cmd, bytes([0]) + light * 6 + bytes([1]))
        elif cmd == C.ENABLE_LIVE:
            self._reply(cmd, b"\x01")
        elif cmd == C.QUERY_FILES:
            for name, serial, cluster in self.files:
                ref = proto.FILENAME_MARKER + name.encode("utf-16le")
                payload = (serial.to_bytes(2, "big") + cluster.to_bytes(4, "big")
                           + len(self.files).to_bytes(2, "big") + (3000).to_bytes(2, "big") + bytes([255])
                           + bytes([1, 255, 255, 0, 0, 0, 0]) * 6 + bytes([1, 0, serial, len(ref)]) + ref)
                self._reply(cmd, payload)
        elif cmd == C.START_TRANSFER:
            name = data[10:-1].decode("utf-16le")
            self.upload = {"name": name, "size": int.from_bytes(data[2:6], "big"), "chunks": {}}
            self._reply(cmd, bytes([0]) + (0).to_bytes(4, "big"))
        elif cmd == C.CHUNK and self.upload is not None:
            self.upload["chunks"][int.from_bytes(data[2:4], "big")] = data[4:-1]
        elif cmd == C.END_TRANSFER and self.upload is not None:
            last = max(self.upload["chunks"], default=0)
            self._reply(cmd, bytes([0]) + last.to_bytes(2, "big"))
        elif cmd == C.CONFIRM_TRANSFER and self.upload is not None:
            up, self.upload = self.upload, None
            body = b"".join(up["chunks"][i] for i in sorted(up["chunks"]))
            ok = len(body) == up["size"]
            if ok:
                serial = max((s for _, s, _ in self.files), default=0) + 1
                self.files.append((up["name"], serial, serial * 1000))
                self.uploaded = body
            self._reply(cmd, bytes([0 if ok else 1]))
        elif cmd == C.DELETE_FILE:
            serial = int.from_bytes(data[2:4], "big")
            before = len(self.files)
            self.files = [f for f in self.files if f[1] != serial]
            self._reply(cmd, bytes([0 if len(self.files) < before else 1]))
        elif cmd == C.SET_RGB:
            rgb = (data[3], data[4], data[5])
            name_len = data[11]
            if name_len:
                self.file_rgb[data[14:12 + name_len].decode("utf-16le")] = rgb
            else:
                self.live_rgb = rgb
        elif cmd == C.PLAY_FILE:
            serial = int.from_bytes(data[2:4], "big")
            if data[4]:
                name = next((n for n, s, _ in self.files if s == serial), "")
                self.live_rgb = self.file_rgb.get(name, (255, 0, 0))
            self._reply(cmd, data[2:5] + (5).to_bytes(2, "big"))
