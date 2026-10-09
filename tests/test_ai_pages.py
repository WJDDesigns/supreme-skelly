import json

from fastapi.testclient import TestClient

from skelly.api import create_app
from skelly.link import SimulatedLink
from skelly.settings import Settings
from skelly.vault import Vault


def make(tmp_path):
    settings = Settings.load(tmp_path / "settings.json")
    vault = Vault(tmp_path / "vault.json")
    return TestClient(create_app(SimulatedLink(), settings, autoconnect=False, vault=vault)), tmp_path


def test_vault_never_returns_the_secret(tmp_path):
    c, d = make(tmp_path)
    with c:
        secret = "sk-test-1234567890abcd"
        rows = c.put("/api/vault/openai_api_key", json={"value": secret}).json()
        row = next(r for r in rows if r["name"] == "openai_api_key")
        assert row["set"] and row["hint"] == "…abcd"
        assert secret not in json.dumps(c.get("/api/vault").json())
        assert secret not in json.dumps(c.get("/api/state").json())
        assert (d / "vault.json").stat().st_mode & 0o777 == 0o600
        assert c.put("/api/vault/nope", json={"value": "x"}).status_code == 400
        rows = c.delete("/api/vault/openai_api_key").json()
        assert not next(r for r in rows if r["name"] == "openai_api_key")["set"]


def test_conversation_needs_keys_and_saves_config(tmp_path):
    c, _ = make(tmp_path)
    with c:
        r = c.post("/api/conversation/start")
        assert r.status_code == 400 and "agent ID" in r.json()["detail"]
        cfg = c.put("/api/conversation/config", json={"provider": "claude", "stt": "openai", "bogus": 1}).json()
        assert cfg["provider"] == "claude" and cfg["stt"] == "openai" and "bogus" not in cfg
        r = c.post("/api/conversation/start")
        assert r.status_code == 400 and "Anthropic" in r.json()["detail"]
        assert c.get("/api/conversation").json()["config"]["provider"] == "claude"


def test_vision_rtsp_needs_address(tmp_path):
    c, _ = make(tmp_path)
    with c:
        c.put("/api/vision/config", json={"source": "rtsp"})
        r = c.post("/api/vision/start")
        assert r.status_code == 400 and "RTSP" in r.json()["detail"]
        assert c.get("/api/vision").json()["state"]["running"] is False


def test_elevenlabs_pickers(tmp_path, monkeypatch):
    import skelly.api as api_mod

    c, _ = make(tmp_path)
    with c:
        assert c.get("/api/elevenlabs/agents").status_code == 400  # no key yet
        c.put("/api/vault/elevenlabs_api_key", json={"value": "xi-test-key-123456"})

        async def agents(key):
            assert key == "xi-test-key-123456"
            return [{"id": "agent_1", "name": "Skelly"}]

        async def create(key, cfg):
            return {"id": "agent_new", "name": "Skelly"}

        monkeypatch.setattr(api_mod, "elevenlabs_agents", agents)
        monkeypatch.setattr(api_mod, "elevenlabs_create_agent", create)
        assert c.get("/api/elevenlabs/agents").json() == [{"id": "agent_1", "name": "Skelly"}]
        assert c.post("/api/elevenlabs/agents").json()["id"] == "agent_new"
        assert c.get("/api/conversation").json()["config"]["elevenlabs_agent_id"] == "agent_new"


def test_elevenlabs_prompt_sync(tmp_path, monkeypatch):
    import skelly.api as api_mod

    c, _ = make(tmp_path)
    pushed = {}

    async def agent(key, agent_id):
        return {"id": agent_id, "name": "Spooky", "prompt": "From ElevenLabs", "first_message": "Boo!",
                "voice_id": "v1"}

    async def update(key, agent_id, *, prompt=None, first_message=None):
        pushed.update(agent_id=agent_id, prompt=prompt, first_message=first_message)

    monkeypatch.setattr(api_mod, "elevenlabs_agent", agent)
    monkeypatch.setattr(api_mod, "elevenlabs_update_agent", update)
    with c:
        c.put("/api/vault/elevenlabs_api_key", json={"value": "sk_test_key_123456"})
        r = c.put("/api/conversation/config", json={"provider": "elevenlabs", "elevenlabs_agent_id": "a1"}).json()
        assert (r["prompt"], r["first_message"], r["elevenlabs_voice_id"]) == ("From ElevenLabs", "Boo!", "v1")
        assert "Loaded Spooky" in r["sync"]

        r = c.put("/api/conversation/config", json={"prompt": "Edited here"}).json()
        assert r["sync"] == "Saved to ElevenLabs"
        assert pushed == {"agent_id": "a1", "prompt": "Edited here", "first_message": "Boo!"}


def test_ignored_zones_are_per_camera_and_masked(tmp_path):
    from skelly.vision import THUMB_H, THUMB_W, _mask

    c, _ = make(tmp_path)
    with c:
        c.put("/api/vision/config", json={"source": "rtsp"})
        cfg = c.put("/api/vision/zones", json={"zones": [[0.0, 0.2, 0.25, 0.6], [0.5, 0.5, 0, 0.1]]}).json()
        assert cfg["zones"] == {"rtsp": [[0.0, 0.2, 0.25, 0.6]]}  # empty box dropped
        cfg = c.put("/api/vision/config", json={"source": "usb"}).json()
        assert c.put("/api/vision/zones", json={"zones": []}).json()["zones"]["rtsp"] == [[0.0, 0.2, 0.25, 0.6]]
    keep = _mask([[0.0, 0.0, 0.5, 1.0]])
    assert len(keep) == THUMB_W * THUMB_H // 2 and all(i % THUMB_W >= THUMB_W // 2 for i in keep)


def test_wallpaper_pick_upload_and_remove(tmp_path):
    c, _ = make(tmp_path)
    with c:
        assert c.get("/api/settings").json()["wallpaper"] == "classic"
        st = c.patch("/api/settings", json={"wallpaper": "graveyard", "wallpaper_dim": 40}).json()
        assert st["wallpaper"] == "graveyard" and st["wallpaper_dim"] == 40
        assert c.patch("/api/settings", json={"wallpaper": "../etc"}).status_code == 422
        assert c.patch("/api/settings", json={"ui_transparency": 50}).json()["ui_transparency"] == 50
        assert c.patch("/api/settings", json={"ui_transparency": 95}).status_code == 422
        assert c.get("/static/wallpapers/graveyard.svg").status_code == 200
        bad = c.post("/api/wallpaper", files={"file": ("x.txt", b"hello", "text/plain")})
        assert bad.status_code == 400
        png = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
        assert c.post("/api/wallpaper", files={"file": ("me.png", png, "image/png")}).json()["wallpaper"] == "custom"
        got = c.get("/api/wallpaper/custom")
        assert got.status_code == 200 and got.headers["content-type"] == "image/png" and got.content == png
        assert c.delete("/api/wallpaper/custom").json()["wallpaper"] == "classic"
        assert c.get("/api/wallpaper/custom").status_code == 404


def test_sightings_log_what_skelly_did(tmp_path, monkeypatch):
    import skelly.vision as vision_mod

    verdicts = iter([{"people": 1, "approaching": False, "description": "a man in a blue shirt walking a dog"},
                     {"people": 0, "approaching": False}])

    async def fake_describe(vault, jpeg, zones=None):
        return next(verdicts)

    monkeypatch.setattr(vision_mod, "describe", fake_describe)
    c, _ = make(tmp_path)
    with c:
        app = c.app
        called = []

        async def passerby(verdict, cfg):
            called.append(verdict)

        app.state.vision._on_passerby = passerby
        c.portal.call(app.state.vision.maybe_visitor_from, b"\xff\xd8\xff fake", [], "Front Door camera")
        app.state.vision.state.last_visitor_at -= 100  # past the short gap after a passer-by
        c.portal.call(app.state.vision.maybe_visitor_from, b"\xff\xd8\xff other", [], "Garage Hoop camera")
        rows = c.get("/api/sightings").json()
        assert [r["camera"] for r in rows] == ["Garage Hoop camera", "Front Door camera"]
        assert rows[1]["outcome"].startswith("Called them over") and called
        assert rows[0]["outcome"] == "Ignored: nobody in the picture"
        assert rows[1]["snap"] and c.get(f"/api/snaps/{rows[1]['snap']}").status_code == 200


def test_cyclists_riding_past_are_not_called_over(tmp_path, monkeypatch):
    import skelly.vision as vision_mod

    async def fake_describe(vault, jpeg, zones=None):
        return {"people": 1, "bikes": 1, "approaching": False, "notice": "blue bike"}

    monkeypatch.setattr(vision_mod, "describe", fake_describe)
    c, _ = make(tmp_path)
    with c:
        called = []

        async def passerby(verdict, cfg):
            called.append(verdict)

        c.app.state.vision._on_passerby = passerby
        c.portal.call(c.app.state.vision.maybe_visitor_from, b"\xff\xd8\xff bike", [], "Garage Mailbox camera")
        assert not called
        assert c.get("/api/sightings").json()[0]["outcome"].startswith("Ignored: riding past on a bike")
