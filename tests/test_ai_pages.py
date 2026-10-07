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
