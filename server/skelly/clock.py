"""Local time at Skelly's house, for quiet hours and the daily usage tally.

The time zone comes from Settings (filled in from the browser on first run). Until it's set,
the mini PC's own clock zone is used.
"""

from __future__ import annotations

from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_zone: tzinfo | None = None


def valid(name: str | None) -> bool:
    try:
        return bool(name) and ZoneInfo(name) is not None
    except (ZoneInfoNotFoundError, ValueError):
        return False


def set_zone(name: str | None) -> None:
    global _zone
    _zone = ZoneInfo(name) if valid(name) else None


def zone() -> tzinfo:
    return _zone or datetime.now().astimezone().tzinfo


def now() -> datetime:
    return datetime.now(zone())
