"""Restart buttons for the parts of the mini PC Skelly depends on.

Everything goes through the host's systemd and logind over the system D-Bus the app
already uses for Bluetooth, so no extra access is needed: audio is PipeWire in the
"skelly" user's session (restarting user@1000 brings it back up, including when nobody is
logged in), Bluetooth is bluetooth.service, and a reboot is logind's Reboot.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time

log = logging.getLogger(__name__)

AUDIO_UNIT = os.environ.get("SKELLY_AUDIO_UNIT", "user@1000.service")


async def _systemd(member: str, sig: str, body: list, *, dest="org.freedesktop.systemd1",
                   path="/org/freedesktop/systemd1", iface="org.freedesktop.systemd1.Manager"):
    from dbus_fast import BusType, Message, MessageType
    from dbus_fast.aio import MessageBus

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        reply = await bus.call(Message(destination=dest, path=path, interface=iface, member=member,
                                       signature=sig, body=body))
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f"{reply.error_name}: {reply.body[0] if reply.body else ''}")
        return reply.body
    finally:
        bus.disconnect()


async def restart_unit(unit: str) -> None:
    await _systemd("RestartUnit", "ss", [unit, "replace"])


async def restart_audio() -> None:
    await restart_unit(AUDIO_UNIT)
    await asyncio.sleep(4)  # PipeWire, its pulse socket and WirePlumber come back up


async def restart_bluetooth() -> None:
    await restart_unit("bluetooth.service")
    await asyncio.sleep(4)


async def reboot() -> None:
    await _systemd("Reboot", "b", [False], dest="org.freedesktop.login1", path="/org/freedesktop/login1",
                   iface="org.freedesktop.login1.Manager")


def restart_app_soon(delay: float = 0.5) -> None:
    """Exit after replying; Docker's restart policy starts the app again in a few seconds."""
    loop = asyncio.get_running_loop()
    loop.call_later(delay, os._exit, 0)


def status() -> dict:
    try:
        up = float(open("/proc/uptime").read().split()[0])
    except OSError:
        up = 0.0
    try:
        load = os.getloadavg()[0]
    except OSError:
        load = 0.0
    disk = shutil.disk_usage(os.environ.get("SKELLY_DATA_DIR", "/"))
    mem = {}
    try:
        for line in open("/proc/meminfo"):
            k, v = line.split(":", 1)
            mem[k] = int(v.split()[0]) * 1024
    except OSError:
        pass
    return {"uptime_s": int(up), "load": round(load, 2), "cpus": os.cpu_count(),
            "disk_free": disk.free, "disk_total": disk.total,
            "mem_free": mem.get("MemAvailable"), "mem_total": mem.get("MemTotal"), "time": time.time()}
