from datetime import datetime

from skelly.conversation import ConversationConfig, is_quiet


def at(h, m=0):
    return datetime(2026, 10, 31, h, m)


def test_quiet_overnight_window():
    cfg = ConversationConfig(quiet_from="22:00", quiet_to="16:00")
    assert is_quiet(cfg, at(22)) and is_quiet(cfg, at(2)) and is_quiet(cfg, at(15, 59))
    assert not is_quiet(cfg, at(16)) and not is_quiet(cfg, at(21, 59))


def test_quiet_same_day_window_and_off_switch():
    cfg = ConversationConfig(quiet_from="01:00", quiet_to="06:30")
    assert is_quiet(cfg, at(3)) and not is_quiet(cfg, at(7))
    assert not is_quiet(ConversationConfig(quiet_hours=False), at(23))
