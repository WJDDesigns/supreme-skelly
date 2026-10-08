from fastapi.testclient import TestClient

from skelly import updates
from skelly.api import create_app
from skelly.auth import Auth
from skelly.link import SimulatedLink
from skelly.settings import Settings


def test_version_compare():
    assert updates.newer("0.3.0", "0.2.0") and updates.newer("v0.10.0", "0.9.9")
    assert not updates.newer("0.2.0", "0.2.0") and not updates.newer("0.1.0", "0.2.0")
    assert not updates.newer(None, "0.2.0") and not updates.newer("0.3.0", "dev")
    assert updates.parse("v1.2.3") == (1, 2, 3) and updates.parse("v1.2.3-rc1") is None


def test_running_version_comes_from_the_version_file():
    assert updates.parse(updates.current())


def test_version_endpoint_and_auto_update_setting(tmp_path, monkeypatch):
    async def fake_latest(force=False):
        return "99.0.0"

    monkeypatch.setattr(updates, "latest", fake_latest)
    app = create_app(SimulatedLink(), Settings.load(tmp_path / "s.json"), autoconnect=False,
                     auth=Auth(tmp_path / "auth.json"))
    with TestClient(app) as c:
        v = c.get("/api/version").json()
        assert v["latest"] == "99.0.0" and v["update_available"] and v["auto_update"] is True
        assert c.patch("/api/settings", json={"auto_update": False}).json()["auto_update"] is False
        assert c.get("/api/version").json()["auto_update"] is False
