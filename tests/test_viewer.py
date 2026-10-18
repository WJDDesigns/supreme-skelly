import asyncio
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from skelly import viewer
from skelly.api import create_app
from skelly.link import SimulatedLink
from skelly.viewer import MIC, SPEAKER, VIDEO, Viewer, ffmpeg_args, split_jpegs


def test_split_jpegs_keeps_partial_frame_for_next_read():
    a, b = b"\xff\xd8one\xff\xd9", b"\xff\xd8two\xff\xd9"
    frames, rest = split_jpegs(b"junk" + a + b + b"\xff\xd8thr")
    assert frames == [a, b] and rest == b"\xff\xd8thr"
    frames, rest = split_jpegs(rest + b"ee\xff\xd9")
    assert frames == [b"\xff\xd8three\xff\xd9"] and rest == b""


def test_ffmpeg_args_rotate_and_no_sound():
    args = ffmpeg_args(["-i", "rtsps://cam"], rotate=90)
    assert "-an" in args and any("transpose=1" in a for a in args) and args[-1] == "pipe:1"


class FakeVision:
    def __init__(self):
        self.state = SimpleNamespace(running=True)
        self.frame = None
        self._frame_event = asyncio.Event()

    def show(self, jpg):
        self.frame = jpg
        self._frame_event.set()


def _viewer(vision, source="usb"):
    async def no_speaker():
        return None
    return Viewer(vision, None, SimpleNamespace(get=lambda k: None), lambda: {"source": source},
                  lambda: "", lambda: 1.0, no_speaker)


def _drain(w):
    out = []
    while not w.queue.empty():
        out.append(w.queue.get_nowait())
    return out


async def test_shares_visions_frames_and_stops_when_nobody_watches():
    vision = FakeVision()
    v = _viewer(vision)
    w = v.join()
    assert json.loads(_drain(w)[0])["type"] == "status"
    await asyncio.sleep(0.05)
    vision.show(b"\xff\xd8pic\xff\xd9")
    await asyncio.sleep(0.05)
    msgs = _drain(w)
    assert bytes([VIDEO]) + b"\xff\xd8pic\xff\xd9" in msgs
    assert v.status["video"] == "live"
    assert "mic" not in v._tasks  # no recording until someone presses Listen
    v.leave(w)
    assert not v._tasks
    await v.stop()


async def test_sound_only_goes_to_listeners_and_picture_only_to_watchers(monkeypatch):
    async def no_record(device):
        return None
    monkeypatch.setattr(viewer, "_record", no_record)
    v = _viewer(FakeVision())
    a, b = v.join(), v.join()
    v.want(a, listen=True, video=False)
    assert {"mic", "speaker", "video"} <= set(v._tasks)
    _drain(a), _drain(b)
    v._send(MIC, b"\x00\x01")
    v._send(SPEAKER, b"\x00\x02")
    v._send(VIDEO, b"jpg")
    assert _drain(a) == [bytes([MIC]) + b"\x00\x01", bytes([SPEAKER]) + b"\x00\x02"]
    assert _drain(b) == [bytes([VIDEO]) + b"jpg"]
    v.want(a, listen=False)
    assert "mic" not in v._tasks and "speaker" not in v._tasks
    await v.stop()


def test_slow_viewer_skips_instead_of_piling_up():
    w = viewer.Watcher()
    for _ in range(viewer.QUEUE + 5):
        w.put(b"x")
    assert w.queue.qsize() == viewer.QUEUE and w.dropped == 5


def test_websocket_sends_status(monkeypatch):
    monkeypatch.setenv("SKELLY_SIMULATE", "1")
    with TestClient(create_app(SimulatedLink(), autoconnect=False)) as client:
        with client.websocket_connect("/api/viewer") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "status" and "video" in msg
            ws.send_text(json.dumps({"listen": False, "video": True}))


async def test_dead_recorder_never_freezes_the_app(monkeypatch):
    opened = []

    async def dead_record(device):
        out = asyncio.StreamReader()
        out.feed_eof()  # parec quit at once (mic unplugged), but hasn't been reaped yet

        async def wait():
            return 0
        proc = SimpleNamespace(stdout=out, returncode=None, terminate=lambda: None, kill=lambda: None, wait=wait)
        opened.append(proc)
        return proc
    monkeypatch.setattr(viewer, "_record", dead_record)
    v = _viewer(FakeVision())
    w = v.join()
    v.want(w, listen=True)
    ticks = 0
    for _ in range(20):  # the event loop must keep running while the recorder is retried
        await asyncio.sleep(0.01)
        ticks += 1
    assert ticks == 20 and v.status["mic"] is False and len(opened) >= 1
    await v.stop()
