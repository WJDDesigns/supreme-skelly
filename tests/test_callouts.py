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
