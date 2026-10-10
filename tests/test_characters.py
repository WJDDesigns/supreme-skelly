from datetime import datetime

from skelly.callouts import LINES, call_out, greeting
from skelly.conversation import DEFAULT_PROMPT, ConversationConfig, for_character, season_note
from skelly.profiles import LETHAL_LILY, SKELLY_12FT, ULTRA_SANTA, ULTRA_SKELLY_V2


def test_untouched_personality_follows_the_model():
    cfg = for_character(ConversationConfig(), ULTRA_SANTA)
    assert cfg.prompt.startswith("You are Santa Claus") and "six-and-a-half-foot" in cfg.prompt
    assert "Ho ho ho" in cfg.first_message
    lily = for_character(ConversationConfig(prompt=cfg.prompt, first_message=cfg.first_message), LETHAL_LILY)
    assert "Lethal Lily" in lily.prompt and "dearie" in lily.first_message
    assert "twelve-foot" in for_character(ConversationConfig(), SKELLY_12FT).prompt
    assert for_character(ConversationConfig(), ULTRA_SKELLY_V2).prompt == DEFAULT_PROMPT


def test_an_edited_personality_is_kept():
    cfg = for_character(ConversationConfig(prompt="You are Bob the friendly ghost.", first_message="Boo!"),
                        ULTRA_SANTA)
    assert cfg.prompt == "You are Bob the friendly ghost." and cfg.first_message == "Boo!"


def test_each_character_has_its_own_lines(monkeypatch):
    for who in ("lily", "santa"):
        assert greeting(character=who) in LINES[who]["greetings"]
        assert "Morticia" in greeting("Morticia", character=who)
        assert call_out([], "", who) in LINES[who]["plain"]
    monkeypatch.setattr("random.random", lambda: 0.0)
    assert "vampire" in call_out(["Vampire"], "", "santa")
    assert not any("skeleton" in line.lower() for group in LINES["santa"].values() for line in group)


def test_santa_counts_down_to_christmas():
    assert "Christmas is 76 days away" in season_note(datetime(2026, 10, 10, 19), character="santa")
    assert "Christmas Eve" in season_note(datetime(2026, 12, 24, 19), character="santa")
    assert "Halloween" in season_note(datetime(2026, 10, 10, 19))
