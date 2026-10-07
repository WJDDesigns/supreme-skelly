"""Pair and connect Skelly's Live Mode speaker (Bluetooth Classic, A2DP) through BlueZ.

Live Mode makes the prop show up as a second, Classic Bluetooth device ("<name>(Live)"
or "<name> Live") that takes legacy PIN pairing with the PIN the prop reports over BLE.
Once paired and connected, PipeWire on the host exposes it as an audio sink, which is
where conversation audio is played.
"""

import asyncio
import logging

log = logging.getLogger(__name__)

AGENT_PATH = "/skelly/agent"


def _pin_agent(pin: str):
    """A BlueZ pairing agent that answers with the prop's PIN and says yes to everything else.

    Built on demand so dbus_fast (Linux only) isn't needed just to import this module.
    """
    from dbus_fast.service import ServiceInterface, method

    class _PinAgent(ServiceInterface):
        """Answers BlueZ's pairing questions: the prop's PIN, and yes to everything else."""

        def __init__(self, pin: str) -> None:
            super().__init__("org.bluez.Agent1")
            self.pin = pin

        @method()
        def Release(self): ...

        @method()
        def RequestPinCode(self, device: "o") -> "s":  # noqa: F821
            return self.pin

        @method()
        def DisplayPinCode(self, device: "o", pincode: "s"): ...  # noqa: F821

        @method()
        def RequestPasskey(self, device: "o") -> "u":  # noqa: F821
            return int(self.pin) if self.pin.isdigit() else 0

        @method()
        def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"): ...  # noqa: F821

        @method()
        def RequestConfirmation(self, device: "o", passkey: "u"): ...  # noqa: F821

        @method()
        def RequestAuthorization(self, device: "o"): ...  # noqa: F821

        @method()
        def AuthorizeService(self, device: "o", uuid: "s"): ...  # noqa: F821

        @method()
        def Cancel(self): ...

    return _PinAgent(pin)


async def _call(bus, path: str, iface: str, member: str, sig: str = "", body=None, timeout=None):
    from dbus_fast import Message, MessageType

    reply = await bus.call(Message(destination="org.bluez", path=path, interface=iface, member=member,
                                   signature=sig, body=body or []))
    if reply.message_type == MessageType.ERROR:
        raise RuntimeError(f"{reply.error_name}: {reply.body[0] if reply.body else ''}")
    return reply.body


async def _props(bus, path: str, iface: str) -> dict:
    (props,) = await _call(bus, path, "org.freedesktop.DBus.Properties", "GetAll", "s", [iface])
    return {k: v.value for k, v in props.items()}


async def _find(bus, adapter: str, names: tuple[str, ...]) -> str | None:
    (objs,) = await _call(bus, "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")
    wanted = {n.casefold() for n in names}
    for path, ifaces in objs.items():
        dev = ifaces.get("org.bluez.Device1")
        if dev and path.startswith(f"/org/bluez/{adapter}/"):
            name = getattr(dev.get("Name") or dev.get("Alias"), "value", "") or ""
            if name.casefold() in wanted:
                return path
    return None


async def connect_speaker(adapter: str, names: tuple[str, ...], pin: str, *, scan_s: float = 12.0) -> dict:
    """Find, pair (if needed), trust and connect the Live speaker. Returns its state."""
    from dbus_fast import BusType
    from dbus_fast.aio import MessageBus

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    agent = _pin_agent(pin or "1234")
    bus.export(AGENT_PATH, agent)
    from dbus_fast import Variant

    try:
        await _call(bus, "/org/bluez", "org.bluez.AgentManager1", "RegisterAgent", "os",
                    [AGENT_PATH, "KeyboardDisplay"])
        await _call(bus, "/org/bluez", "org.bluez.AgentManager1", "RequestDefaultAgent", "o", [AGENT_PATH])
        apath = f"/org/bluez/{adapter}"

        dev = await _find(bus, adapter, names)
        if not dev:
            await _call(bus, apath, "org.bluez.Adapter1", "SetDiscoveryFilter", "a{sv}",
                        [{"Transport": Variant("s", "bredr")}])
            await _call(bus, apath, "org.bluez.Adapter1", "StartDiscovery")
            try:
                for _ in range(int(scan_s * 2)):
                    await asyncio.sleep(0.5)
                    dev = await _find(bus, adapter, names)
                    if dev:
                        break
            finally:
                try:
                    await _call(bus, apath, "org.bluez.Adapter1", "StopDiscovery")
                except RuntimeError:
                    pass
        if not dev:
            raise LookupError("Skelly's speaker isn't showing up. Is Live Mode on?")

        props = await _props(bus, dev, "org.bluez.Device1")
        if not props.get("Paired"):
            log.info("pairing Live speaker %s", props.get("Address"))
            await _call(bus, dev, "org.bluez.Device1", "Pair")
        await _call(bus, dev, "org.freedesktop.DBus.Properties", "Set", "ssv",
                    ["org.bluez.Device1", "Trusted", Variant("b", True)])
        if not props.get("Connected") or not props.get("Paired"):
            await _call(bus, dev, "org.bluez.Device1", "Connect")
        props = await _props(bus, dev, "org.bluez.Device1")
        return {"address": props.get("Address"), "name": props.get("Name"),
                "paired": props.get("Paired"), "connected": props.get("Connected")}
    finally:
        try:
            await _call(bus, "/org/bluez", "org.bluez.AgentManager1", "UnregisterAgent", "o", [AGENT_PATH])
        except RuntimeError:
            pass
        bus.disconnect()


# -- extra Bluetooth speakers (outdoor speakers and the like) -------------------------

A2DP_SINK = "0000110b-0000-1000-8000-00805f9b34fb"


def _is_audio_out(dev: dict) -> bool:
    uuids = getattr(dev.get("UUIDs"), "value", None) or []
    cls = getattr(dev.get("Class"), "value", 0) or 0
    return A2DP_SINK in uuids or (cls >> 8) & 0x1F == 0x04  # major class "Audio/Video"


def _describe(path: str, dev: dict) -> dict:
    val = lambda k, d=None: getattr(dev.get(k), "value", d)  # noqa: E731
    return {"address": val("Address"), "name": val("Name") or val("Alias") or val("Address"),
            "paired": bool(val("Paired", False)), "connected": bool(val("Connected", False)),
            "rssi": val("RSSI"), "sink": f"bluez_output.{(val('Address') or '').replace(':', '_')}.1"}


async def _devices(bus, adapter: str) -> list[dict]:
    (objs,) = await _call(bus, "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")
    return [_describe(p, i["org.bluez.Device1"]) for p, i in objs.items()
            if "org.bluez.Device1" in i and p.startswith(f"/org/bluez/{adapter}/")
            and _is_audio_out(i["org.bluez.Device1"])]


async def scan_speakers(adapter: str, seconds: float = 10.0) -> list[dict]:
    """Bluetooth speakers in range (put them in pairing mode first) plus ones already paired."""
    from dbus_fast import BusType, Variant
    from dbus_fast.aio import MessageBus

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    apath = f"/org/bluez/{adapter}"
    try:
        await _call(bus, apath, "org.bluez.Adapter1", "SetDiscoveryFilter", "a{sv}",
                    [{"Transport": Variant("s", "bredr")}])
        await _call(bus, apath, "org.bluez.Adapter1", "StartDiscovery")
        try:
            await asyncio.sleep(seconds)
        finally:
            try:
                await _call(bus, apath, "org.bluez.Adapter1", "StopDiscovery")
            except RuntimeError:
                pass
        found = await _devices(bus, adapter)
    finally:
        bus.disconnect()
    return sorted(found, key=lambda d: (not d["paired"], -(d["rssi"] or -999), d["name"].lower()))


async def connect_address(adapter: str, address: str, pin: str = "0000") -> dict:
    """Pair (if needed), trust and connect a speaker by its address."""
    from dbus_fast import BusType, Variant
    from dbus_fast.aio import MessageBus

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    bus.export(AGENT_PATH, _pin_agent(pin))
    dev = f"/org/bluez/{adapter}/dev_{address.upper().replace(':', '_')}"
    try:
        await _call(bus, "/org/bluez", "org.bluez.AgentManager1", "RegisterAgent", "os",
                    [AGENT_PATH, "NoInputNoOutput"])
        await _call(bus, "/org/bluez", "org.bluez.AgentManager1", "RequestDefaultAgent", "o", [AGENT_PATH])
        props = await _props(bus, dev, "org.bluez.Device1")
        if not props.get("Paired"):
            await _call(bus, dev, "org.bluez.Device1", "Pair")
        await _call(bus, dev, "org.freedesktop.DBus.Properties", "Set", "ssv",
                    ["org.bluez.Device1", "Trusted", Variant("b", True)])
        if not props.get("Connected"):
            await _call(bus, dev, "org.bluez.Device1", "Connect")
        props = await _props(bus, dev, "org.bluez.Device1")
        return {"address": props.get("Address"), "name": props.get("Name"), "connected": props.get("Connected")}
    finally:
        try:
            await _call(bus, "/org/bluez", "org.bluez.AgentManager1", "UnregisterAgent", "o", [AGENT_PATH])
        except RuntimeError:
            pass
        bus.disconnect()


async def forget(adapter: str, address: str) -> None:
    from dbus_fast import BusType
    from dbus_fast.aio import MessageBus

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        await _call(bus, f"/org/bluez/{adapter}", "org.bluez.Adapter1", "RemoveDevice", "o",
                    [f"/org/bluez/{adapter}/dev_{address.upper().replace(':', '_')}"])
    finally:
        bus.disconnect()
