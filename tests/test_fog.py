import time

import httpx
import pytest
from fastapi.testclient import TestClient

from skelly import fog
from skelly.api import create_app
from skelly.link import SimulatedLink
from skelly.settings import Settings
from skelly.vault import Vault


class Bus:
    def __init__(self):
        self.events = []

    def publish(self, kind, data=None):
        self.events.append((kind, data))


class Svc:
    def __init__(self):
        self.bus = Bus()


@pytest.fixture
def requests(monkeypatch):
    """Every request a relay would get, answered OK."""
    seen = []
    real = httpx.AsyncClient

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(fog.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(fog, "TAP_S", 0.01)
    return seen


def make_fog(allowed=True, **cfg):
    conf = {"enabled": True, "host": "10.0.0.9", "burst_s": 0.5, **cfg}
    return fog.Fog(Svc(), lambda: conf, lambda: allowed)


def test_detail_drops_when_the_picture_goes_flat():
    w, h = 8, 6
    sharp = bytes((0 if (x + y) % 2 else 200) for y in range(h) for x in range(w))
    flat = bytes([120] * (w * h))
    zone = [0, 0, 1, 1]
    clear = fog.detail(sharp, w, h, zone)
    assert clear > 100
    assert fog.detail(flat, w, h, zone) == 0
    assert fog.fog_level(clear, clear) == 0
    assert fog.fog_level(0, clear) == 100
    assert fog.fog_level(clear / 2, clear) == 50
    assert fog.fog_level(clear, 0) is None  # not calibrated yet
    assert fog.detail(sharp, w, h, []) is None


def test_config_is_kept_in_safe_bounds():
    cfg = fog.FogConfig.from_dict({"burst_s": 999, "cooldown_s": 0, "kind": "nope", "mode": "x", "zone": [2, -1, 0.5]})
    assert cfg.burst_s == fog.MAX_BURST_S and cfg.cooldown_s == 10
    assert cfg.kind == "shelly" and cfg.mode == "hold" and cfg.zone == []


async def test_shelly_hold_closes_then_opens_with_a_safety_timer(requests):
    f = make_fog(kind="shelly", channel=1)
    await f.puff()
    on, off = requests
    assert on.url.path == "/rpc/Switch.Set" and on.url.params["on"] == "true"
    assert on.url.params["id"] == "1" and float(on.url.params["toggle_after"]) == 0.5
    assert off.url.params["on"] == "false"
    assert not f.state.fogging and f.state.last_why == "Fog button"


async def test_tasmota_switches_itself_off(requests):
    await make_fog(kind="tasmota").puff()
    assert requests[0].url.params["cmnd"] == "Backlog Power1 On; Delay 5; Power1 Off"
    assert requests[-1].url.params["cmnd"] == "Power1 Off"


async def test_tap_mode_taps_start_then_stop(requests):
    await make_fog(kind="shelly1", mode="tap", channel=0, stop_channel=1).puff()
    paths = [(r.url.path, r.url.params["turn"]) for r in requests]
    assert paths == [("/relay/0", "on"), ("/relay/0", "off"), ("/relay/1", "on"), ("/relay/1", "off")]


async def test_unreachable_relay_reports_plainly(monkeypatch):
    real = httpx.AsyncClient

    def refuse(req):
        raise httpx.ConnectError("refused", request=req)

    monkeypatch.setattr(fog.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(refuse), **kw))
    monkeypatch.setattr(fog.asyncio, "sleep", _no_sleep)
    f = make_fog()
    with pytest.raises(ValueError, match="Couldn't reach the relay"):
        await f.puff()
    assert not f.state.fogging and "Couldn't reach" in f.state.error


async def _no_sleep(_s):
    return None


async def test_auto_fog_rests_and_respects_quiet_hours(requests):
    f = make_fog(cooldown_s=60)
    assert await f.auto("visitor")
    assert not await f.auto("visitor again")  # resting
    f.state.last_at = time.time() - 120
    assert await f.auto("later visitor")
    assert not await make_fog(allowed=False).auto("visitor")
    assert not await make_fog(enabled=False).auto("visitor")


async def test_hourly_limit(requests):
    f = make_fog(cooldown_s=10, max_per_hour=2)
    for _ in range(2):
        assert await f.auto("v")
        f.state.last_at = 0
    assert not await f.auto("v")


async def test_disabled_fog_button_explains():
    with pytest.raises(ValueError, match="Turn the fog machine on"):
        await make_fog(enabled=False).puff()


def make_app(tmp_path):
    settings = Settings.load(tmp_path / "settings.json")
    return TestClient(create_app(SimulatedLink(), settings, autoconnect=False, vault=Vault(tmp_path / "vault.json")))


def test_fog_api(tmp_path):
    with make_app(tmp_path) as c:
        r = c.get("/api/fog").json()
        assert r["config"]["enabled"] is False and "shelly" in r["kinds"]
        assert c.post("/api/fog/puff", json={}).status_code == 400
        c.app.state.fog._detail = 12.5
        cfg = c.put("/api/fog/config", json={"enabled": True, "zone": [0.1, 0.1, 0.3, 0.3]}).json()["config"]
        assert cfg["enabled"] and cfg["zone"] == [0.1, 0.1, 0.3, 0.3]
        c.app.state.fog._zone, c.app.state.fog._detail = cfg["zone"], 12.5
        assert c.post("/api/fog/calibrate").json()["config"]["clear_detail"] == 12.5
        # Calibration can't be set by hand, and moving the spot clears it.
        assert c.put("/api/fog/config", json={"clear_detail": 1}).json()["config"]["clear_detail"] == 12.5
        assert c.put("/api/fog/config", json={"zone": [0, 0, 0.5, 0.5]}).json()["config"]["clear_detail"] == 0
