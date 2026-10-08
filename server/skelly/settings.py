"""Persistent settings, stored as JSON in the data directory."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

log = logging.getLogger(__name__)


def data_dir() -> Path:
    default = Path.home() / ".local" / "share" / "supreme-skelly"
    return Path(os.environ.get("SKELLY_DATA_DIR", default))


@dataclass
class Settings:
    auto_connect: bool = True  # find and connect to Skelly on startup and after drops
    auto_live_mode: bool = False  # turn on Live Mode (Bluetooth speaker) once connected
    keep_look: bool = False  # re-apply the chosen lights/eyes whenever the device resets them
    look: dict | None = None  # {"lights": {light_key: {...}}, "eye": int | None}
    last_address: str | None = None
    last_name: str | None = None
    live_speaker: str | None = None  # MAC of Skelly's Live Mode (Classic) speaker once paired
    conversation: dict = field(default_factory=dict)  # ConversationConfig fields
    vision: dict = field(default_factory=dict)  # VisionConfig fields
    # Sound in and out: {"mic": PipeWire source or "", "skelly": play on Skelly's Live speaker,
    # "extra": [more PipeWire sinks, e.g. outdoor Bluetooth speakers]}
    audio: dict = field(default_factory=lambda: {"mic": "", "skelly": True, "extra": []})
    bt_adapter: str | None = None  # MAC of the Bluetooth radio to use; None = SKELLY_BT_ADAPTER or the default
    # {"items": [{"name", "on", "before", "after"}], "loop": bool, "shuffle": bool}; see playlist.normalize
    playlist: dict = field(default_factory=dict)
    # Per-sound Live Performance, by sound name: {"moves", "eye", "color", "mode", "speed", "brightness", "cycle"}
    performances: dict = field(default_factory=dict)
    timezone: str = ""  # IANA name, e.g. "America/New_York"; empty = the mini PC's own clock zone
    setup_done: bool = False  # the first-run welcome has been finished or skipped
    auto_update: bool = True  # install new releases by itself (the host's skelly-update timer reads this)
    wallpaper: str = "classic"  # a built-in from web/wallpapers, "classic" (no picture) or "custom" (uploaded)
    wallpaper_dim: int = 55  # how much the wallpaper is darkened behind the cards, in percent
    ui_transparency: int = 0  # how see-through the cards and bars are, in percent (0 = solid)

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        path = path or data_dir() / "settings.json"
        s = cls()
        s._path = path
        try:
            raw = json.loads(path.read_text())
            known = {f.name for f in fields(cls)}
            for k, v in raw.items():
                if k in known:
                    setattr(s, k, v)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            log.warning("Ignoring unreadable settings file %s: %s", path, exc)
        return s

    def save(self) -> None:
        path: Path | None = getattr(self, "_path", None)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(self), indent=2))
            tmp.replace(path)  # atomic, so a power cut can't leave a half-written file
        except OSError as exc:
            log.warning("Couldn't save settings: %s", exc)

    def public(self) -> dict:
        return asdict(self)
