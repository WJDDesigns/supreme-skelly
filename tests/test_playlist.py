import asyncio
import time

from fastapi.testclient import TestClient

from skelly import playlist as playlist_mod
from skelly import protocol as p
from skelly import service as service_mod
from skelly.api import create_app
from skelly.link import SimulatedLink
from skelly.playlist import Playlist, normalize
from skelly.service import SkellyService
from skelly.settings import Settings


async def _connected(play_seconds=0.2):
    link = SimulatedLink()
    link.play_seconds = play_seconds
    svc = SkellyService(link, auto_reconnect=False)
    await svc.start()
    await svc.connect("SIM", "Ultra Skelly v2")
    await asyncio.sleep(0.6)
    return svc, link


def _plays(link):
    return [int.from_bytes(d[2:4], "big") for d in link.sent if d[1] == p.Cmd.PLAY_FILE and d[4]]


def _wait_for_files(c):
    for _ in range(50):
        if c.get("/api/files").json():
            return
        time.sleep(0.05)
    raise AssertionError("sound list never loaded")


def test_normalize_cleans_items():
    cfg = normalize({"items": [{"name": "Boo.mp3", "after": "2"}, {"name": "boo.mp3"}, {"name": " "},
                               {"name": "Welcome.mp3", "on": False, "before": -3, "after": 9999}], "loop": 1})
    assert cfg == {"items": [{"name": "Boo.mp3", "on": True, "before": 0.0, "after": 2.0},
                             {"name": "Welcome.mp3", "on": False, "before": 0.0, "after": 600.0}],
                   "loop": True, "shuffle": False}


async def test_plays_ticked_sounds_in_order_driven_by_device_events():
    svc, link = await _connected()
    pl = Playlist(svc)
    pl.set_config({"items": [{"name": "Boo.mp3", "after": 0}, {"name": "Spooky Laugh.mp3", "on": False},
                             {"name": "Welcome.mp3", "after": 0}]})
    await pl.play()
    await asyncio.sleep(0.15)
    assert pl.status.running and pl.status.name == "Boo.mp3" and pl.status.total == 2
    await asyncio.sleep(1.0)
    assert _plays(link) == [3, 2]
    assert not pl.status.running
    await svc.stop()


async def test_start_watchdog_retries_then_skips(monkeypatch):
    monkeypatch.setattr(playlist_mod, "START_TIMEOUT_S", 0.15)
    svc, link = await _connected()
    real_write = link.write

    async def deaf_to_boo(data):
        if data[1] == p.Cmd.PLAY_FILE and int.from_bytes(data[2:4], "big") == 3:
            link.sent.append(data)  # Skelly "misses" this one
            return
        await real_write(data)

    link.write = deaf_to_boo
    pl = Playlist(svc)
    pl.set_config({"items": [{"name": "Boo.mp3", "after": 0}, {"name": "Welcome.mp3", "after": 0}]})
    await pl.play()
    await asyncio.sleep(1.2)
    assert _plays(link) == [3, 3, 2]
    assert not pl.status.running
    await svc.stop()


async def test_stop_halts_the_sound():
    svc, link = await _connected(play_seconds=5)
    pl = Playlist(svc)
    pl.set_config({"items": [{"name": "Boo.mp3"}], "loop": True})
    await pl.play()
    await asyncio.sleep(0.2)
    await pl.stop()
    await asyncio.sleep(0.1)
    assert link.sent[-1] == p.play_file(3, False)
    assert not pl.status.running
    await svc.stop()


async def test_skip_moves_on():
    svc, link = await _connected(play_seconds=5)
    pl = Playlist(svc)
    pl.set_config({"items": [{"name": "Boo.mp3", "after": 0}, {"name": "Welcome.mp3", "after": 0}]})
    await pl.play()
    await asyncio.sleep(0.2)
    pl.skip()
    await asyncio.sleep(0.3)
    assert _plays(link) == [3, 2] and pl.status.name == "Welcome.mp3"
    await pl.stop()
    await svc.stop()


async def test_performance_is_stored_on_the_sound_and_applied_when_it_plays(monkeypatch):
    monkeypatch.setattr(service_mod, "REAPPLY_DELAY_S", 0.05)
    svc, link = await _connected()
    perf = {"moves": ["head"], "eye": 4, "color": "#00ff00", "mode": 1, "brightness": 200, "speed": None,
            "cycle": False}
    await svc.set_performance(2, perf)
    await asyncio.sleep(0.3)
    # Stored in the sound's scene on Skelly, for when he plays it on his own...
    assert link.file_rgb["Welcome.mp3"] == (0, 255, 0)
    assert p.movement(svc.movement_mask(["head"]), 2000, "Welcome.mp3") in link.sent
    assert svc.settings.performances["Welcome.mp3"] == perf
    # ...and sent live when it starts, over whatever the firmware resets to.
    link.file_rgb.clear()
    await svc.play_file(2)
    await asyncio.sleep(0.4)
    assert link.live_rgb == (0, 255, 0)
    await svc.stop()


async def test_kept_look_comes_back_after_a_performance(monkeypatch):
    monkeypatch.setattr(service_mod, "REAPPLY_DELAY_S", 0.05)
    svc, link = await _connected()
    await svc.set_light("all", rgb=(0, 0, 255))
    svc.set_keep_look(True)
    await svc.set_performance(2, {"moves": [], "color": "#ff00ff"})
    await svc.play_file(2)
    await asyncio.sleep(0.15)
    assert link.live_rgb == (255, 0, 255)
    await asyncio.sleep(0.5)  # sound ends
    assert link.live_rgb == (0, 0, 255)
    await svc.stop()


def test_api_playlist_and_performance(tmp_path):
    link = SimulatedLink()
    link.play_seconds = 0.2
    with TestClient(create_app(link, Settings.load(tmp_path / "s.json"), autoconnect=False)) as c:
        assert c.post("/api/playlist/play").status_code == 409  # not connected
        c.post("/api/connect", json={"address": "SIM", "name": "Ultra Skelly v2"})
        _wait_for_files(c)
        cfg = c.put("/api/playlist", json={"items": [{"name": "Boo.mp3", "after": 0}], "loop": False}).json()
        assert cfg["items"][0]["name"] == "Boo.mp3"
        assert c.get("/api/playlist").json()["config"] == cfg
        assert c.post("/api/playlist/play").json()["running"] in (True, False)
        r = c.put("/api/files/3/performance", json={"moves": ["head"], "color": "FF0000"})
        assert r.status_code == 200 and r.json()["color"] == "#ff0000"
        assert c.put("/api/files/3/performance", json={"moves": ["tail"]}).status_code == 400
        assert c.delete("/api/files/3/performance").json() == {"ok": True}
        c.post("/api/playlist/stop")
    s = Settings.load(tmp_path / "s.json")
    assert s.playlist["items"][0]["name"] == "Boo.mp3" and s.performances == {}


def test_empty_playlist_is_refused(tmp_path):
    with TestClient(create_app(SimulatedLink(), Settings.load(tmp_path / "s.json"), autoconnect=False)) as c:
        c.post("/api/connect", json={"address": "SIM", "name": "Ultra Skelly v2"})
        _wait_for_files(c)
        r = c.post("/api/playlist/play")
        assert r.status_code == 400 and "Tick" in r.json()["detail"]
