"""A chat that ends on its own never leaves the visitor's last words (often a goodbye) unanswered."""
import asyncio
from types import SimpleNamespace

from skelly import conversation as c


def _conv(keys, transcript):
    vault = SimpleNamespace(get=keys.get)
    svc = SimpleNamespace(bus=SimpleNamespace(publish=lambda *a: None), set_movement=None)
    conv = c.Conversation(svc, vault, dict, None)
    conv.state.transcript = [{"role": r, "text": t, "ts": 0} for r, t in transcript]
    return conv


def _sign_off(conv, cfg, monkeypatch):
    spoken = []

    async def speak(http, voice, speaker, text):
        spoken.append((voice.tts, text))

    monkeypatch.setattr(conv, "_speak", speak)
    asyncio.run(conv._sign_off(cfg, SimpleNamespace(echoing=False)))
    return spoken


def test_unanswered_goodbye_gets_an_answer(monkeypatch):
    conv = _conv({"elevenlabs_api_key": "e"}, [("skelly", "Boo!"), ("user", "Bye Skelly, I love you")])
    spoken = _sign_off(conv, c.ConversationConfig(provider="elevenlabs", elevenlabs_agent_id="ag"), monkeypatch)
    assert spoken == [("elevenlabs", c.goodbye_line("Bye Skelly, I love you"))]
    assert "love you too" in spoken[0][1]
    assert conv.state.transcript[-1]["role"] == "skelly"


def test_answered_or_silent_chats_add_nothing(monkeypatch):
    cfg = c.ConversationConfig(provider="elevenlabs", elevenlabs_agent_id="ag")
    keys = {"elevenlabs_api_key": "e"}
    assert _sign_off(_conv(keys, [("user", "Bye!"), ("skelly", "Bye bye!")]), cfg, monkeypatch) == []
    assert _sign_off(_conv(keys, [("skelly", "Psst! Over here!")]), cfg, monkeypatch) == []
    assert _sign_off(_conv(keys, [("skelly", "Hi!"), ("user", "um")]), cfg, monkeypatch) == []


def test_goodbye_uses_a_voice_that_works(monkeypatch):
    said = [("skelly", "Hi!"), ("user", "Gotta go, see ya")]
    openai = _sign_off(_conv({"openai_api_key": "o"}, said), c.ConversationConfig(provider="openai"), monkeypatch)
    assert openai and openai[0][0] == "openai"
    conv = _conv({"elevenlabs_api_key": "e", "openai_api_key": "o"}, said)
    conv._eleven_out_at = 1.0  # ElevenLabs ran out of credits mid-chat
    assert _sign_off(conv, c.ConversationConfig(provider="claude"), monkeypatch)[0][0] == "openai"
    assert _sign_off(_conv({}, said), c.ConversationConfig(provider="elevenlabs"), monkeypatch) == []


def test_stock_goodbyes():
    assert "love you too" in c.goodbye_line("i luv you skelly")
    assert c.goodbye_line("bye bye").startswith("Bye bye")
    assert "nice talking" in c.goodbye_line("what's your name")
