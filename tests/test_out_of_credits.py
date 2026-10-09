"""ElevenLabs running out of credits: a clear message, and OpenAI's voice and hearing take over."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest

from skelly import conversation as c


def _conv(keys):
    vault = SimpleNamespace(get=keys.get)
    svc = SimpleNamespace(bus=SimpleNamespace(publish=lambda *a: None), set_movement=None)
    return c.Conversation(svc, vault, dict, None)


def test_quota_error_is_out_of_credits_not_a_bad_key():
    r = httpx.Response(401, json={"detail": {"status": "quota_exceeded",
                                             "message": "This request exceeds your quota of 30000."}})
    with pytest.raises(c.OutOfCredits):
        c._raise_for(r, "ElevenLabs voice")
    assert "out of credits" in c._friendly(c.OutOfCredits("x"))
    with pytest.raises(RuntimeError, match="rejected the API key"):
        c._raise_for(httpx.Response(401, json={"detail": {"status": "invalid_api_key"}}), "ElevenLabs voice")


def test_fallback_needs_an_openai_key():
    cfg = c.ConversationConfig(provider="claude", tts="elevenlabs", stt="elevenlabs", first_message="Psst!")
    assert _conv({"anthropic_api_key": "a"})._without_elevenlabs(cfg) is None
    alt = _conv({"anthropic_api_key": "a", "openai_api_key": "o"})._without_elevenlabs(cfg)
    assert (alt.provider, alt.tts, alt.stt, alt.first_message) == ("claude", "openai", "openai", "Psst!")
    agent = c.ConversationConfig(provider="elevenlabs", elevenlabs_agent_id="ag")
    assert _conv({"openai_api_key": "o"})._without_elevenlabs(agent) is None  # Claude still has to think
    assert _conv({"anthropic_api_key": "a", "openai_api_key": "o"})._without_elevenlabs(agent).provider == "claude"


def _run(conv, cfg, monkeypatch):
    used = []

    async def broke(cfg, speaker):
        used.append(("eleven", cfg.tts))
        raise c.OutOfCredits("ElevenLabs voice: quota exceeded")

    async def claude(cfg, speaker):
        used.append(("claude", cfg.tts))
        if cfg.tts == "elevenlabs":
            raise c.OutOfCredits("ElevenLabs voice: quota exceeded")

    monkeypatch.setattr(conv, "_elevenlabs", broke)
    monkeypatch.setattr(conv, "_claude", claude)
    monkeypatch.setattr(conv, "_body", lambda *a: asyncio.sleep(0))
    monkeypatch.setattr(conv, "_still", lambda: asyncio.sleep(0))
    monkeypatch.setattr(c, "Speaker", lambda *a: SimpleNamespace(close=lambda: asyncio.sleep(0)))
    cfg.speaker = "sink"
    asyncio.run(conv._run(cfg))
    return used


def test_out_of_credits_switches_to_openai(monkeypatch):
    conv = _conv({"anthropic_api_key": "a", "openai_api_key": "o"})
    used = _run(conv, c.ConversationConfig(provider="claude"), monkeypatch)
    assert used == [("claude", "elevenlabs"), ("claude", "openai")]
    assert conv.state.state != "error" and conv._eleven_out_at


def test_out_of_credits_without_openai_says_so(monkeypatch):
    conv = _conv({"elevenlabs_api_key": "e"})
    used = _run(conv, c.ConversationConfig(provider="elevenlabs", elevenlabs_agent_id="ag"), monkeypatch)
    assert used == [("eleven", "elevenlabs")]
    assert conv.state.state == "error" and conv.state.error == c.OUT_OF_CREDITS
