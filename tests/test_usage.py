from datetime import datetime

from skelly import clock, usage


def test_usage_adds_up_per_day(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "_path", tmp_path / "usage.json")
    night = datetime(2026, 10, 8, 2, 0, tzinfo=clock.zone())
    usage.add("vision", model="claude-haiku-4-5-20251001", tokens_in=1000, tokens_out=100, now=night)
    usage.add("vision", model="claude-haiku-4-5-20251001", tokens_in=1000, tokens_out=100, now=night)
    usage.add("conversation:elevenlabs", seconds=90, now=night)
    [day] = usage.summary()
    assert day["day"] == "2026-10-08"
    assert day["items"]["vision"]["calls"] == 2 and day["items"]["vision"]["tokens_in"] == 2000
    assert day["items"]["conversation:elevenlabs"]["seconds"] == 90
    assert day["usd"] == round(2 * (1000 * 1.0 + 100 * 5.0) / 1e6, 3)
