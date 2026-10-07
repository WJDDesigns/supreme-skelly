"""API keys and other secrets, kept in one file on the mini PC that only root can read.

Values are written by the person through the Settings page and are never sent back
in full: the UI only ever sees whether a secret is set and its last four characters.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .settings import data_dir

log = logging.getLogger(__name__)

# What the Settings page offers, in display order. Anything else is refused.
SECRETS: dict[str, dict] = {
    "elevenlabs_api_key": {"label": "ElevenLabs API key",
                           "used_for": "ElevenLabs conversations, voices and speech-to-text"},
    "openai_api_key": {"label": "OpenAI API key",
                       "used_for": "OpenAI Realtime conversations, voices and speech-to-text"},
    "anthropic_api_key": {"label": "Anthropic API key", "used_for": "Claude conversations and scene descriptions"},
    "rtsp_url": {"label": "Camera stream address (RTSP)",
                 "used_for": "Vision, e.g. a UniFi Protect camera's RTSPS link"},
}


class Vault:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "vault.json"
        self._data: dict[str, str] = {}
        try:
            self._data = {k: v for k, v in json.loads(self.path.read_text()).items() if k in SECRETS and v}
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            log.warning("Ignoring unreadable vault %s: %s", self.path, exc)

    def get(self, name: str) -> str | None:
        return self._data.get(name) or None

    def set(self, name: str, value: str | None) -> None:
        if name not in SECRETS:
            raise ValueError(f"Unknown secret '{name}'")
        value = (value or "").strip()
        if value:
            self._data[name] = value
        else:
            self._data.pop(name, None)
        self._save()

    def public(self) -> list[dict]:
        out = []
        for name, meta in SECRETS.items():
            v = self._data.get(name)
            hint = (f"…{v[-4:]}" if len(v) > 8 else "set") if v else None
            out.append({"name": name, **meta, "set": bool(v), "hint": hint})
        return out

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self._data, f)
        tmp.replace(self.path)
