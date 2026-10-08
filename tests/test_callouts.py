from datetime import datetime

from skelly.callouts import call_out
from skelly.conversation import season_note
from skelly.vision import notice


def test_notice_is_a_clean_noun_phrase():
    assert notice({"notice": "Love the blue shirt!"}) == "blue shirt"
    assert notice({"notice": "your cute dog"}) == "cute dog"
    assert notice({}) == "" and notice(None) == ""


def test_call_out_uses_what_it_noticed(monkeypatch):
    monkeypatch.setattr("random.random", lambda: 0.0)
    assert "blue shirt" in call_out([], "blue shirt")
    assert "vampire" in call_out(["Vampire"], "blue shirt")
    assert call_out([], "")  # nothing seen: a plain shout


def test_season_note_only_expects_trick_or_treaters_on_halloween():
    early = season_note(datetime(2026, 10, 8, 19))
    assert "23 days away" in early and "not trick-or-treaters" in early
    assert "Halloween night" in season_note(datetime(2026, 10, 31, 19))
    assert "349 days away" in season_note(datetime(2026, 11, 16, 19))


def test_noise_is_not_an_answer():
    from skelly.conversation import said_something

    assert said_something("Hi") and said_something("Who are you?")
    assert not said_something("Thank you.") and not said_something("...") and not said_something("um")


def test_self_started_chat_ends_when_nobody_answers(monkeypatch):
    import asyncio
    import time as _time
    from types import SimpleNamespace

    from skelly.conversation import Conversation, ConversationConfig, ConversationState

    conv = Conversation(SimpleNamespace(bus=SimpleNamespace(publish=lambda *a: None)), None, dict, None)
    conv.state = ConversationState(started_at=_time.time(), trigger="someone walked up (Front Door camera)")
    conv._last_heard = _time.monotonic() - 20  # he stopped talking 20 s ago, nobody said anything
    cfg = ConversationConfig(no_answer_s=15, idle_timeout_s=45)
    asyncio.run(asyncio.wait_for(conv._idle_watch(cfg, SimpleNamespace(speaking=False)), 3))  # ends itself

    conv.state.answered = True  # once someone has talked, the normal 45 s idle rule applies
    conv._last_heard = _time.monotonic() - 20
    try:
        asyncio.run(asyncio.wait_for(conv._idle_watch(cfg, SimpleNamespace(speaking=False)), 2))
        raise AssertionError("ended too early")
    except TimeoutError:
        pass
