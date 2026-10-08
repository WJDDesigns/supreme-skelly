import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from skelly.api import create_app
from skelly.auth import Auth
from skelly.link import SimulatedLink
from skelly.settings import Settings


def _client(tmp_path):
    auth = Auth(tmp_path / "auth.json")
    app = create_app(SimulatedLink(), Settings.load(tmp_path / "s.json"), autoconnect=False, auth=auth)
    return TestClient(app), auth


def test_open_until_a_password_is_set(tmp_path):
    c, _ = _client(tmp_path)
    with c:
        assert c.get("/api/state").status_code == 200
        assert c.get("/api/auth/status").json() == {"password": False, "signed_in": True, "setup_done": False}


def test_password_locks_the_api_and_websocket(tmp_path):
    c, auth = _client(tmp_path)
    with c:
        assert c.put("/api/auth/password", json={"new": "abc"}).status_code == 400  # too short
        r = c.put("/api/auth/password", json={"new": "boneyard"})
        assert r.status_code == 200 and auth.enabled
        assert c.get("/api/state").status_code == 200  # the browser that set it stays signed in

        c.cookies.clear()
        assert c.get("/api/state").status_code == 401
        assert c.get("/api/health").status_code == 200
        with pytest.raises(WebSocketDisconnect) as refused:
            with c.websocket_connect("/api/events"):
                pass
        assert refused.value.code == 4401

        assert c.post("/api/auth/login", json={"password": "nope"}).status_code == 401
        assert c.post("/api/auth/login", json={"password": "boneyard"}).status_code == 200
        assert c.get("/api/state").status_code == 200

        # Changing it needs the current one; an empty new password turns it off.
        assert c.put("/api/auth/password", json={"current": "wrong", "new": ""}).status_code == 401
        assert c.put("/api/auth/password", json={"current": "boneyard", "new": ""}).status_code == 200
        assert not auth.enabled


def test_deleting_the_file_turns_the_password_off(tmp_path):
    c, auth = _client(tmp_path)
    with c:
        c.put("/api/auth/password", json={"new": "boneyard"})
        c.cookies.clear()
        assert c.get("/api/state").status_code == 401
        (tmp_path / "auth.json").unlink()
        assert c.get("/api/state").status_code == 200


def test_other_websites_cant_change_things(tmp_path):
    c, _ = _client(tmp_path)
    with c:
        evil = {"Origin": "http://evil.example"}
        assert c.post("/api/conversation/stop", headers=evil).status_code == 403
        assert c.get("/api/state", headers=evil).status_code == 200  # reads are harmless: they can't see the reply
        assert c.post("/api/auth/logout", headers={"Origin": "http://testserver"}).status_code == 200


def test_time_zone_setting(tmp_path):
    c, _ = _client(tmp_path)
    with c:
        assert c.patch("/api/settings", json={"timezone": "Mars/Olympus"}).status_code == 400
        r = c.patch("/api/settings", json={"timezone": "America/Chicago", "setup_done": True})
        assert r.status_code == 200 and r.json()["timezone"] == "America/Chicago"
        from skelly import clock
        assert str(clock.zone()) == "America/Chicago"
        clock.set_zone(None)
