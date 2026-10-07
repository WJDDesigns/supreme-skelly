import asyncio
import socket
import struct

from skelly import rssi as rssi_mod
from skelly.link import SimulatedLink
from skelly.service import SkellyService

ADDR = "AA:BB:CC:DD:EE:FF"


def _reply(ours: socket.socket, addr_type_ok: int, rssi: int) -> None:
    """Answer GET_CONN_INFO like the kernel: status for the wrong address type, data for the right one."""
    while True:
        try:
            req = ours.recv(64)
        except OSError:
            return
        if not req:
            return
        opcode, index, _ = struct.unpack_from("<HHH", req)
        bdaddr, addr_type = req[6:12], req[12]
        if addr_type != addr_type_ok:
            ours.send(struct.pack("<HHHHB", 2, index, 3, opcode, 2))  # Not Connected
        else:
            params = struct.pack("<HB", opcode, 0) + bdaddr + bytes([addr_type]) + struct.pack("<bbb", rssi, 4, 9)
            ours.send(struct.pack("<HHH", 1, index, len(params)) + params)
        if addr_type == addr_type_ok:
            return


def test_conn_info_reply_is_parsed():
    import threading

    for addr_type, expected in ((1, -61), (2, -77)):
        app, kernel = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        t = threading.Thread(target=_reply, args=(kernel, addr_type, expected))
        t.start()
        got = None
        for at in rssi_mod.ADDR_TYPES:
            got = rssi_mod._ask(app, 0, ADDR, at, 1.0)
            if got is not None:
                break
        t.join()
        app.close()
        kernel.close()
        assert got == expected


def test_read_rssi_never_raises():
    # CI runners have no Bluetooth (or no permission): the answer is simply "unknown".
    got = rssi_mod.read_rssi(ADDR, 0, timeout=0.2)
    assert got is None or isinstance(got, int)


def test_service_reports_signal_while_connected(monkeypatch):
    monkeypatch.setattr("skelly.service.SIGNAL_INTERVAL_S", 0.05)

    async def main():
        link = SimulatedLink()
        svc = SkellyService(link, auto_reconnect=False)
        await svc.start()
        await svc.connect("SIM", "Animated Skelly")
        for _ in range(40):
            if svc.state.rssi is not None:
                break
            await asyncio.sleep(0.05)
        assert svc.state.rssi is not None and svc.state.rssi < 0
        await svc.disconnect()
        for _ in range(40):
            if svc.state.rssi is None:
                break
            await asyncio.sleep(0.05)
        assert svc.state.rssi is None
        await svc.stop()

    asyncio.run(main())
