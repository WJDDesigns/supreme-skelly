"""Which version is running, whether a newer release exists, and installing it.

Releases are git tags like v0.3.0 on GitHub. The mini PC's skelly-update timer (installed by
install.sh) checks for a newer one every 15 minutes and installs it when automatic updates are
on; "Update now" starts the same job straight away through systemd.
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

import httpx

from . import system

log = logging.getLogger(__name__)

REPO = os.environ.get("SKELLY_UPDATE_REPO", "WJDDesigns/supreme-skelly")
UNIT = "skelly-update-now.service"
CHECK_EVERY_S = 3600
_cache: dict = {"at": 0.0, "latest": None, "error": None}


def _version_file() -> Path:
    here = Path(__file__).resolve()
    for p in (Path(os.environ.get("SKELLY_VERSION_FILE", "/app/VERSION")), here.parents[2] / "VERSION"):
        if p.is_file():
            return p
    return here.parents[2] / "VERSION"


def current() -> str:
    try:
        return _version_file().read_text().strip() or "dev"
    except OSError:
        return "dev"


def parse(v: str | None) -> tuple[int, ...] | None:
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", (v or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def newer(latest: str | None, running: str) -> bool:
    a, b = parse(latest), parse(running)
    return bool(a and b and a > b)


async def latest(force: bool = False) -> str | None:
    """Newest release tag on GitHub, checked at most hourly."""
    if not force and time.time() - _cache["at"] < CHECK_EVERY_S:
        return _cache["latest"]
    _cache["at"] = time.time()
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            r = await http.get(f"https://api.github.com/repos/{REPO}/tags", params={"per_page": 100})
            r.raise_for_status()
        tags = [t["name"] for t in r.json() if parse(t.get("name"))]
        _cache["latest"] = max(tags, key=parse).lstrip("v") if tags else None
        _cache["error"] = None
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        _cache["error"] = "Couldn't reach GitHub to check for updates"
        log.info("update check failed: %s", exc)
    return _cache["latest"]


async def status(force: bool = False) -> dict:
    running = current()
    newest = await latest(force)
    return {"version": running, "latest": newest, "update_available": newer(newest, running),
            "error": _cache["error"], "repo": REPO}


async def install_now() -> None:
    """Start the host's update job; it rebuilds and restarts this app if there's a newer release."""
    await system._systemd("StartUnit", "ss", [UNIT, "replace"])
