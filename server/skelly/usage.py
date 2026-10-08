"""What Skelly spends on paid AI services, counted per day (Indiana time) and kept on the mini PC.

Every call to Claude or OpenAI and every conversation minute is tallied here, so the Settings page
can show what each night cost and anything running away is easy to spot. ElevenLabs credits are
read straight from the account, since its agent bills by conversation time on its side.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from .settings import data_dir

log = logging.getLogger(__name__)

TZ = ZoneInfo("America/Indiana/Indianapolis")
KEEP_DAYS = 30

# Rough list prices in US$ per million tokens (input, output), for an estimate only.
PRICES = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet": (3.0, 15.0),
    "gpt-4o-mini": (0.15, 0.6),
    "gpt-4o": (2.5, 10.0),
}

_lock = threading.Lock()
_path: Path | None = None


def _file() -> Path:
    return _path or data_dir() / "usage.json"


def _load() -> dict:
    try:
        return json.loads(_file().read_text())
    except (OSError, ValueError):
        return {}


def _save(days: dict) -> None:
    keep = dict(sorted(days.items())[-KEEP_DAYS:])
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(keep, indent=1))
    tmp.replace(f)


def today(now: datetime | None = None) -> str:
    return (now or datetime.now(TZ)).astimezone(TZ).strftime("%Y-%m-%d")


def _price(model: str) -> tuple[float, float]:
    for prefix, p in PRICES.items():
        if model.startswith(prefix):
            return p
    return (3.0, 15.0)


def add(what: str, *, model: str = "", tokens_in: int = 0, tokens_out: int = 0, seconds: float = 0.0,
        now: datetime | None = None) -> None:
    """Count one paid call. `what` is e.g. "vision", "chat", "conversation:elevenlabs"."""
    try:
        with _lock:
            days = _load()
            day = days.setdefault(today(now), {})
            row = day.setdefault(what, {"calls": 0, "tokens_in": 0, "tokens_out": 0, "seconds": 0.0, "usd": 0.0})
            row["calls"] += 1
            row["tokens_in"] += int(tokens_in)
            row["tokens_out"] += int(tokens_out)
            row["seconds"] = round(row["seconds"] + seconds, 1)
            if model:
                pin, pout = _price(model)
                row["usd"] = round(row["usd"] + (tokens_in * pin + tokens_out * pout) / 1e6, 4)
            _save(days)
    except Exception as exc:  # counting must never break Skelly
        log.info("usage not recorded: %r", exc)


def summary(days: int = 7) -> list[dict]:
    """Newest first: [{"day", "items": {what: row}, "usd"}]."""
    data = _load()
    out = []
    for day in sorted(data, reverse=True)[:days]:
        items = data[day]
        out.append({"day": day, "items": items, "usd": round(sum(r.get("usd", 0) for r in items.values()), 3)})
    return out


async def elevenlabs_credits(key: str) -> dict | None:
    """Credits used and left this billing period, from the ElevenLabs account itself."""
    if not key:
        return None
    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.get("https://api.elevenlabs.io/v1/user/subscription", headers={"xi-api-key": key})
        r.raise_for_status()
        s = r.json()
    return {"used": s.get("character_count"), "limit": s.get("character_limit"),
            "resets": s.get("next_character_count_reset_unix"), "tier": s.get("tier")}
