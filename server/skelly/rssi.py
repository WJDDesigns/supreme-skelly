"""Live signal strength of an open BLE link, read from the Linux kernel.

A connected Skelly stops advertising, so BlueZ's D-Bus RSSI goes stale. The
kernel's Bluetooth management socket can still report the link's RSSI
("Get Connection Information"). That command needs CAP_NET_ADMIN, which
docker-compose.yml grants; without it this quietly returns None.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import select
import socket
import struct

log = logging.getLogger(__name__)

AF_BLUETOOTH = 31
BTPROTO_HCI = 1
HCI_DEV_NONE = 0xFFFF
HCI_CHANNEL_CONTROL = 3
MGMT_OP_GET_CONN_INFO = 0x0031
MGMT_EV_CMD_COMPLETE = 0x0001
MGMT_EV_CMD_STATUS = 0x0002
ADDR_TYPES = (1, 2)  # LE public, LE random
RSSI_INVALID = 127


class _SockaddrHci(ctypes.Structure):
    _fields_ = [("family", ctypes.c_ushort), ("dev", ctypes.c_ushort), ("channel", ctypes.c_ushort)]


_libc = None
_warned = False


def _open() -> socket.socket:
    global _libc
    if _libc is None:
        _libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    s = socket.socket(AF_BLUETOOTH, socket.SOCK_RAW | getattr(socket, "SOCK_CLOEXEC", 0), BTPROTO_HCI)
    addr = _SockaddrHci(AF_BLUETOOTH, HCI_DEV_NONE, HCI_CHANNEL_CONTROL)
    if _libc.bind(s.fileno(), ctypes.byref(addr), ctypes.sizeof(addr)) != 0:
        err = ctypes.get_errno()
        s.close()
        raise OSError(err, "bind to the Bluetooth management channel failed")
    return s


def _ask(s: socket.socket, index: int, address: str, addr_type: int, timeout: float) -> int | None:
    bdaddr = bytes(int(b, 16) for b in reversed(address.split(":")))
    params = bdaddr + bytes([addr_type])
    s.send(struct.pack("<HHH", MGMT_OP_GET_CONN_INFO, index, len(params)) + params)
    while True:
        ready, _, _ = select.select([s], [], [], timeout)
        if not ready:
            return None
        data = s.recv(512)
        if len(data) < 9:
            continue
        event, ev_index, _ = struct.unpack_from("<HHH", data)
        opcode, status = struct.unpack_from("<HB", data, 6)
        if ev_index != index or opcode != MGMT_OP_GET_CONN_INFO:
            continue  # someone else's reply on the shared channel
        if event == MGMT_EV_CMD_STATUS or status != 0:
            return None
        if event == MGMT_EV_CMD_COMPLETE and len(data) >= 9 + 8 and data[9:15] == bdaddr:
            rssi = struct.unpack_from("<b", data, 16)[0]
            return None if rssi == RSSI_INVALID else rssi


def read_rssi(address: str, index: int = 0, timeout: float = 1.0) -> int | None:
    """dBm of the open link to `address` on controller hci<index>, or None if unknown. Blocking."""
    global _warned
    try:
        s = _open()
    except (OSError, AttributeError) as exc:  # no Bluetooth sockets on macOS/Windows
        if not _warned:
            log.info("Live Bluetooth signal strength unavailable: %s", exc)
            _warned = True
        return None
    try:
        for addr_type in ADDR_TYPES:
            rssi = _ask(s, index, address, addr_type, timeout)
            if rssi is not None:
                return rssi
        return None
    except OSError as exc:
        if not _warned:
            log.info("Live Bluetooth signal strength unavailable: %s", exc)
            _warned = True
        return None
    finally:
        s.close()
