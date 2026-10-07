"""Skelly-family BLE protocol: command builders and notification parser.

Pure functions only (no Bluetooth, no I/O) so everything here is unit-testable.

Frame layout for commands sent to the device::

    AA <cmd> <payload, zero-padded to at least 8 bytes> <crc8>

Notifications from the device start with ``BB <cmd>`` (responses) or
``FE DC`` (keepalive). Protocol facts come from the community reverse
engineering in martinecker/SkellyUltraWebController (ISC) and SkellyUltra,
plus BLE captures noted in docs/protocol.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SERVICE_UUID = "0000ae00-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "0000ae01-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000ae02-0000-1000-8000-00805f9b34fb"

MIN_PAYLOAD = 8
FILENAME_MARKER = bytes.fromhex("5C55")
MAX_FILENAME_LENGTH = 33  # longer names upload "successfully" but never appear
ALL_CHANNELS = -1


class Cmd:
    # Queries
    QUERY_PARAMS = 0xE0
    QUERY_LIVE = 0xE1
    QUERY_VOLUME = 0xE5
    QUERY_BT_NAME = 0xE6
    QUERY_VERSION = 0xEE
    QUERY_FILES = 0xD0
    QUERY_ORDER = 0xD1
    QUERY_CAPACITY = 0xD2
    # File transfer and playback
    START_TRANSFER = 0xC0
    CHUNK = 0xC1
    END_TRANSFER = 0xC2
    CONFIRM_TRANSFER = 0xC3
    CANCEL_TRANSFER = 0xC4
    RESUME_TRANSFER = 0xC5
    PLAY_FILE = 0xC6
    DELETE_FILE = 0xC7
    FACTORY_RESET = 0xC8
    SET_ORDER = 0xC9
    SET_MOVEMENT = 0xCA
    # Lights and appearance
    SET_LIGHT_MODE = 0xF2
    SET_BRIGHTNESS = 0xF3
    SET_RGB = 0xF4
    SET_SPEED = 0xF6
    SET_EYE = 0xF9
    # Device
    SET_VOLUME = 0xFA
    SET_PIN_AND_NAME = 0xFB
    MEDIA_PLAY_PAUSE = 0xFC
    ENABLE_LIVE = 0xFD


def crc8(data: bytes) -> int:
    """CRC-8/MAXIM (reflected poly 0x8C, init 0)."""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8C if crc & 1 else crc >> 1
    return crc


def build(cmd: int, payload: bytes = b"") -> bytes:
    if len(payload) < MIN_PAYLOAD:
        payload = payload + bytes(MIN_PAYLOAD - len(payload))
    frame = bytes([0xAA, cmd]) + payload
    return frame + bytes([crc8(frame)])


def _u8(name: str, value: int) -> bytes:
    if not 0 <= value <= 0xFF:
        raise ValueError(f"{name} must be 0-255, got {value}")
    return bytes([value])


def _channel(channel: int) -> bytes:
    if channel == ALL_CHANNELS:
        return b"\xff"
    if not 0 <= channel <= 5:
        raise ValueError(f"channel must be -1 (all) or 0-5, got {channel}")
    return bytes([channel])


def filename_ref(name: str, *, with_length: bool = True) -> bytes:
    """Encode a device filename reference.

    Per-file setting commands take ``len, 5C 55, utf16le(name)``; transfer
    commands take the bare marker. An empty name means "live, no file".
    """
    if not name:
        return b"\x00"
    body = FILENAME_MARKER + name.encode("utf-16le")
    if not with_length:
        return body
    if len(body) > 0xFF:
        raise ValueError("filename too long")
    return bytes([len(body)]) + body


def _target(cluster: int, filename: str) -> bytes:
    if not 0 <= cluster <= 0xFFFFFFFF:
        raise ValueError("cluster out of range")
    return cluster.to_bytes(4, "big") + filename_ref(filename)


# --- queries -----------------------------------------------------------------

def query(cmd: int) -> bytes:
    return build(cmd)


# --- live controls -------------------------------------------------------------

def movement(mask: int, cluster: int = 0, filename: str = "") -> bytes:
    """Enable movement for the body parts in ``mask`` (profile-specific bits, 255 = all)."""
    return build(Cmd.SET_MOVEMENT, _u8("mask", mask) + b"\x00" + _target(cluster, filename))


def eye(icon: int, cluster: int = 0, filename: str = "") -> bytes:
    return build(Cmd.SET_EYE, _u8("icon", icon) + b"\x00" + _target(cluster, filename))


def light_mode(channel: int, mode: int, cluster: int = 0, filename: str = "") -> bytes:
    return build(Cmd.SET_LIGHT_MODE, _channel(channel) + _u8("mode", mode) + _target(cluster, filename))


def brightness(channel: int, value: int, cluster: int = 0, filename: str = "") -> bytes:
    return build(Cmd.SET_BRIGHTNESS, _channel(channel) + _u8("brightness", value) + _target(cluster, filename))


def color(channel: int, r: int, g: int, b: int, cycle: bool = False,
          cluster: int = 0, filename: str = "") -> bytes:
    payload = _channel(channel) + _u8("r", r) + _u8("g", g) + _u8("b", b) + bytes([int(cycle)])
    return build(Cmd.SET_RGB, payload + _target(cluster, filename))


def speed(channel: int, value: int, cluster: int = 0, filename: str = "") -> bytes:
    """Raw device speed: 0 (and 255) fastest, 254 slowest. See ui_speed_to_device."""
    return build(Cmd.SET_SPEED, _channel(channel) + _u8("speed", value) + _target(cluster, filename))


def ui_speed_to_device(ui: int) -> int:
    return 254 - max(0, min(254, ui))


def device_speed_to_ui(dev: int) -> int:
    return 254 if dev == 255 else 254 - dev


def volume(value: int) -> bytes:
    return build(Cmd.SET_VOLUME, _u8("volume", value))


def enable_live_mode() -> bytes:
    return build(Cmd.ENABLE_LIVE, b"\x01")


def media_play(playing: bool) -> bytes:
    return build(Cmd.MEDIA_PLAY_PAUSE, bytes([int(playing)]))


def play_file(serial: int, play: bool = True) -> bytes:
    if not 0 <= serial <= 0xFFFF:
        raise ValueError("serial out of range")
    return build(Cmd.PLAY_FILE, serial.to_bytes(2, "big") + bytes([int(play)]))


def delete_file(serial: int, cluster: int) -> bytes:
    return build(Cmd.DELETE_FILE, serial.to_bytes(2, "big") + cluster.to_bytes(4, "big"))


def set_order(total: int, position: int, serial: int, filename: str) -> bytes:
    payload = _u8("total", total) + _u8("position", position) + serial.to_bytes(2, "big")
    return build(Cmd.SET_ORDER, payload + filename_ref(filename))


def factory_reset() -> bytes:
    """Puts the device in demo mode; user must hold Skelly's button 7 s afterwards."""
    return build(Cmd.FACTORY_RESET)


def set_pin_and_name(pin: str, name: str) -> bytes:
    if len(pin) != 4 or not pin.isdigit():
        raise ValueError("PIN must be exactly 4 digits")
    raw = name.encode("ascii", "ignore")
    if not raw:
        raise ValueError("name cannot be empty")
    if len(raw) > 0xFF:
        raise ValueError("name too long")
    return build(Cmd.SET_PIN_AND_NAME, pin.encode() + bytes(8) + bytes([len(raw)]) + raw)


# --- file transfer -------------------------------------------------------------

def start_transfer(size: int, chunk_count: int, filename: str) -> bytes:
    validate_filename(filename)
    payload = size.to_bytes(4, "big") + chunk_count.to_bytes(2, "big")
    return build(Cmd.START_TRANSFER, payload + filename_ref(filename, with_length=False))


def transfer_chunk(index: int, data: bytes) -> bytes:
    return build(Cmd.CHUNK, index.to_bytes(2, "big") + data)


def end_transfer() -> bytes:
    return build(Cmd.END_TRANSFER)


def confirm_transfer(filename: str) -> bytes:
    return build(Cmd.CONFIRM_TRANSFER, filename_ref(filename, with_length=False))


def cancel_transfer() -> bytes:
    return build(Cmd.CANCEL_TRANSFER)


def validate_filename(name: str) -> None:
    if not name:
        raise ValueError("filename cannot be empty")
    if len(name) > MAX_FILENAME_LENGTH:
        raise ValueError(f"filename must be {MAX_FILENAME_LENGTH} characters or fewer")
    if any(c in name for c in '\\/:*?"<>|'):
        raise ValueError("filename contains an invalid character")


# --- notifications -------------------------------------------------------------

@dataclass
class LightState:
    mode: int
    brightness: int
    rgb: tuple[int, int, int]
    cycle: bool
    speed: int


@dataclass
class Event:
    kind: str
    data: dict = field(default_factory=dict)


def _lights(raw: bytes, offset: int, count: int = 6) -> list[LightState]:
    out = []
    for i in range(count):
        c = raw[offset + i * 7: offset + (i + 1) * 7]
        if len(c) < 7:
            break
        out.append(LightState(c[0], c[1], (c[2], c[3], c[4]), bool(c[5]), c[6]))
    return out


def _ascii(b: bytes) -> str:
    return b.decode("ascii", "ignore").strip("\x00 ").strip()


def parse(raw: bytes) -> Event | None:
    """Turn one notification into an Event, or None if unrecognised."""
    if len(raw) < 2:
        return None
    if raw[:2] == b"\xfe\xdc":
        return Event("keepalive", {"payload": raw[2:-1].hex()})
    if raw[0] != 0xBB:
        return None
    cmd, p = raw[1], raw[2:]

    def u(a: int, b: int) -> int:
        return int.from_bytes(p[a:b], "big") if len(p) >= b else 0

    if cmd == Cmd.QUERY_VERSION:
        return Event("version", {"version": f"v{u(0, 1)}"})
    if cmd == Cmd.QUERY_VOLUME:
        return Event("volume", {"volume": u(0, 1)})
    if cmd == Cmd.QUERY_BT_NAME:
        n = u(0, 1)
        return Event("bt_name", {"name": _ascii(p[1:1 + n])})
    if cmd == Cmd.QUERY_PARAMS:
        name_len = u(26, 27)
        return Event("params", {
            "channels": list(p[0:6]),
            "pin": _ascii(p[6:10]),
            "show_mode": u(18, 19),
            "name": _ascii(p[27:27 + name_len]),
        })
    if cmd == Cmd.QUERY_LIVE:
        return Event("live_state", {
            "movement": u(0, 1),
            "lights": [vars(x) for x in _lights(p, 1)],
            "eye": u(43, 44),
        })
    if cmd == Cmd.ENABLE_LIVE:
        return Event("live_enabled", {"status": u(0, 1)})
    if cmd == Cmd.QUERY_CAPACITY:
        return Event("capacity", {"free_kb": u(0, 4), "file_count": u(4, 5), "action_mode": u(5, 6)})
    if cmd == Cmd.QUERY_ORDER:
        n = min(u(0, 1), (len(p) - 1) // 2)
        return Event("order", {"serials": [u(1 + i * 2, 3 + i * 2) for i in range(n)]})
    if cmd == Cmd.QUERY_FILES:
        # The name follows the 5C 55 marker (normally at byte 57) and runs to the CRC.
        mark = raw.find(FILENAME_MARKER, 57)
        name = raw[mark + 2:-1].decode("utf-16le", "ignore").strip("\x00 ") if mark >= 0 else ""
        return Event("file", {
            "serial": u(0, 2),
            "cluster": u(2, 6),
            "total": u(6, 8),
            "movement": u(10, 11),
            "lights": [vars(x) for x in _lights(p, 11)],
            "eye": u(53, 54),
            "db_pos": u(54, 55),
            "name": name,
        })
    if cmd == Cmd.PLAY_FILE:
        return Event("playback", {"serial": u(0, 2), "playing": bool(u(2, 3)), "duration": u(3, 5)})
    if cmd == Cmd.DELETE_FILE:
        return Event("deleted", {"ok": u(0, 1) == 0})
    if cmd == Cmd.START_TRANSFER:
        return Event("transfer_started", {"failed": u(0, 1), "written": u(1, 5)})
    if cmd == Cmd.CHUNK:
        return Event("chunk_dropped", {"dropped": u(0, 1), "index": u(1, 3)})
    if cmd == Cmd.END_TRANSFER:
        return Event("transfer_ended", {"failed": u(0, 1), "last_index": u(1, 3)})
    if cmd == Cmd.CONFIRM_TRANSFER:
        return Event("transfer_confirmed", {"failed": u(0, 1)})
    if cmd == Cmd.CANCEL_TRANSFER:
        return Event("transfer_cancelled", {"failed": u(0, 1)})
    if cmd == Cmd.RESUME_TRANSFER:
        return Event("transfer_resume", {"written": u(0, 4)})
    if cmd == Cmd.FACTORY_RESET:
        return Event("factory_reset", {"status": u(0, 1)})
    return Event("unknown", {"hex": raw.hex()})
