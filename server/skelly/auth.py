"""An optional password for the web UI.

With no password set (the default until first-run setup), anyone on the home network can open
the page. Once one is set, every /api call and the live feed need a signed session cookie, which
the login form hands out. Forgot it? Delete auth.json in the data folder and restart.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path

from .settings import data_dir

log = logging.getLogger(__name__)

COOKIE = "skelly_session"
SESSION_DAYS = 90
MIN_LENGTH = 6
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def _hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT).hex()


class Auth:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "auth.json"
        self._data: dict = {}
        self._mtime: float | None = None
        self._fails: dict[str, list[float]] = {}
        self._reload()

    def _reload(self) -> None:
        """Re-read the file if it changed, so deleting it on the box turns the password off."""
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            self._data, self._mtime = {}, None
            return
        if mtime == self._mtime:
            return
        try:
            self._data = json.loads(self.path.read_text())
        except (OSError, ValueError) as exc:
            log.warning("Ignoring unreadable %s: %s", self.path, exc)
            self._data = {}
        self._mtime = mtime

    @property
    def enabled(self) -> bool:
        self._reload()
        return bool(self._data.get("hash"))

    def check(self, password: str) -> bool:
        self._reload()
        if not self.enabled:
            return True
        salt = bytes.fromhex(self._data["salt"])
        return hmac.compare_digest(_hash(password or "", salt), self._data["hash"])

    def set_password(self, password: str | None) -> None:
        """Set a new password (signing everyone out), or turn the password off with an empty one."""
        password = password or ""
        if password and len(password) < MIN_LENGTH:
            raise ValueError(f"Use at least {MIN_LENGTH} characters")
        if password:
            salt = os.urandom(16)
            data = {"salt": salt.hex(), "hash": _hash(password, salt), "secret": secrets.token_hex(32)}
        else:
            data = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        tmp.replace(self.path)
        self._mtime = None
        self._reload()

    # -- sessions ---------------------------------------------------------------

    def _sign(self, issued: str) -> str:
        return hmac.new(bytes.fromhex(self._data["secret"]), issued.encode(), hashlib.sha256).hexdigest()

    def new_session(self) -> str:
        issued = str(int(time.time()))
        return f"{issued}.{self._sign(issued)}"

    def valid_session(self, token: str | None) -> bool:
        if not self.enabled:
            return True
        issued, _, sig = (token or "").partition(".")
        if not issued.isdigit() or not hmac.compare_digest(sig, self._sign(issued)):
            return False
        return time.time() - int(issued) < SESSION_DAYS * 86400

    # -- slowing down guessing ---------------------------------------------------

    def locked_out(self, who: str) -> bool:
        recent = [t for t in self._fails.get(who, []) if time.time() - t < 300]
        self._fails[who] = recent
        return len(recent) >= 10

    def failed(self, who: str) -> None:
        self._fails.setdefault(who, []).append(time.time())
