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
