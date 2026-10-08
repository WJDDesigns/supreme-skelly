import asyncio

from fastapi.testclient import TestClient

from skelly import profiles
from skelly import protocol as p
from skelly.api import create_app
from skelly.link import SimulatedLink
from skelly.service import SkellyService


async def _connected_service(name="Ultra Skelly v2"):
    link = SimulatedLink()
    svc = SkellyService(link, auto_reconnect=False)
    await svc.start()
    await svc.connect("SIM", name)
    await asyncio.sleep(0.6)  # let refresh_all finish
    return svc, link


async def test_connect_loads_device_info_and_files():
    svc, _ = await _connected_service()
    s = svc.state
    assert s.status == "connected" and s.profile == "ultra_skelly_v2"
    assert s.version == "v68" and s.volume == 120 and s.pin == "1234"
    assert [f["name"] for f in s.files] == ["Spooky Laugh.mp3", "Welcome.mp3", "Boo.mp3"]
    await svc.stop()


async def test_movement_mask_uses_profile_bits():
    svc, link = await _connected_service("Lethal Lily")
    await svc.set_movement(["head", "eyes"])
    assert link.sent[-1] == p.movement(0x30)
    try:
        await svc.set_movement(["torso"])
    except ValueError as exc:
        assert "Lethal Lily" in str(exc)
    else:
        raise AssertionError("expected ValueError")
    await svc.stop()


async def test_link_loss_reports_reconnecting():
    svc, link = await _connected_service()
    svc.auto_reconnect = True
    await link.disconnect()
    link.on_disconnect()
    assert svc.state.status == "reconnecting"
    await asyncio.sleep(1.3)
    assert svc.state.status == "connected"
    await svc.stop()


def test_profile_lookup():
    assert profiles.by_ble_name(" ultra  skelly v2 ").key == "ultra_skelly_v2"
    assert profiles.by_ble_name("Random Speaker").key == "unknown"
    assert profiles.by_key("ultra_santa").wake_tone_ms == 125


def test_api_end_to_end():
    with TestClient(create_app(SimulatedLink(), autoconnect=False)) as c:
        found = c.post("/api/scan", params={"timeout": 1}).json()
        assert found[0]["name"] == "Ultra Skelly v2"
        assert c.post("/api/movement", json={"parts": ["head"]}).status_code == 409
        c.post("/api/connect", json={"address": found[0]["address"], "name": found[0]["name"]})
        assert c.get("/api/state").json()["device"]["status"] == "connected"
        r = c.post("/api/light", json={"light": "head", "color": "#00ff00", "brightness": 200})
        assert r.json() == {"ok": True}
        assert c.post("/api/light", json={"light": "lantern", "mode": 1}).status_code == 400
        assert c.post("/api/eye", json={"eye": 3}).status_code == 200
        assert c.post("/api/volume", json={"volume": 300}).status_code == 422
        with c.websocket_connect("/api/events") as ws:
            assert ws.receive_json()["type"] == "snapshot"


async def test_autoconnect_finds_device_and_remembers_it(tmp_path, monkeypatch):
    from skelly import service as service_mod
    from skelly.settings import Settings

    monkeypatch.setattr(service_mod, "AUTOCONNECT_INTERVAL_S", 0.05)
    monkeypatch.setattr(service_mod, "AUTOCONNECT_SCAN_S", 0.05)
    settings = Settings.load(tmp_path / "settings.json")
    settings.auto_live_mode = True
    svc = SkellyService(SimulatedLink(), settings=settings)
    await svc.start()
    svc.start_autoconnect()
    await asyncio.sleep(1.2)
    assert svc.state.status == "connected"
    assert svc.state.live_mode is True
    assert Settings.load(tmp_path / "settings.json").last_name == "Ultra Skelly v2"

    # A manual disconnect must not be undone by auto-connect.
    await svc.disconnect()
    await asyncio.sleep(0.5)
    assert svc.state.status == "disconnected"
    await svc.stop()


def test_settings_api(tmp_path):
    from skelly.settings import Settings

    with TestClient(create_app(SimulatedLink(), Settings.load(tmp_path / "s.json"), autoconnect=False)) as c:
        assert c.get("/api/settings").json()["auto_connect"] is True
        assert c.patch("/api/settings", json={"auto_live_mode": True}).json()["auto_live_mode"] is True
    assert Settings.load(tmp_path / "s.json").auto_live_mode is True


async def test_kept_look_survives_playback_reset(monkeypatch):
    from skelly import service as service_mod

    monkeypatch.setattr(service_mod, "REAPPLY_DELAY_S", 0.05)
    svc, link = await _connected_service()
    await svc.set_light("all", rgb=(0, 255, 0))
    assert link.live_rgb == (0, 255, 0)

    # Without the lock, playing a sound resets Skelly to the sound's own colour.
    await svc.play_file(1)
    await asyncio.sleep(0.3)
    assert link.live_rgb == (255, 0, 0)

    svc.set_keep_look(True)
    await svc.apply_look()
    await svc.play_file(2)
    await asyncio.sleep(0.3)
    assert link.live_rgb == (0, 255, 0)
    assert svc.settings.look["lights"]["all"]["rgb"] == [0, 255, 0]
    await svc.stop()


async def test_save_look_to_sounds_writes_every_file():
    svc, link = await _connected_service()
    await svc.set_light("all", rgb=(0, 255, 0), brightness=255)
    assert await svc.save_look_to_sounds() == 3
    await asyncio.sleep(0.5)
    assert link.file_rgb == {"Spooky Laugh.mp3": (0, 255, 0), "Welcome.mp3": (0, 255, 0), "Boo.mp3": (0, 255, 0)}
    # Now even device-triggered playback stays green, with no lock needed.
    await svc.play_file(3)
    await asyncio.sleep(0.2)
    assert link.live_rgb == (0, 255, 0)
    await svc.stop()
