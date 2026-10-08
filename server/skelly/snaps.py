"""Camera pictures saved with each line of a conversation, for looking back at what happened.

Each transcript line gets the camera's current preview frame (already a small JPEG, so no
extra decoding or AI). Lines that share the same frame share one file. Pictures are kept
for a week, and never more than MAX_FILES of them.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from pathlib import Path

from .settings import data_dir

log = logging.getLogger(__name__)

KEEP_S = 7 * 86400
MAX_FILES = 3000
_NAME = re.compile(r"^\d{13}-[0-9a-f]{8}\.jpg$")


class Snaps:
    def __init__(self, root: Path | None = None):
        self._root = root
        self._last: tuple[str, str] | None = None  # (hash, name) of the last saved frame
        self._saves = 0

    @property
    def root(self) -> Path:
        d = self._root or data_dir() / "snaps"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save(self, jpeg: bytes | None) -> str | None:
        """Store a frame and return its name, or None when there's no picture."""
        if not jpeg:
            return None
        digest = hashlib.sha1(jpeg).hexdigest()[:8]
        if self._last and self._last[0] == digest and (self.root / self._last[1]).exists():
            return self._last[1]
        name = f"{int(time.time() * 1000)}-{digest}.jpg"
        try:
            (self.root / name).write_bytes(jpeg)
        except OSError as exc:
            log.warning("Couldn't save a conversation picture: %s", exc)
            return None
        self._last = (digest, name)
        self._saves += 1
        if self._saves % 50 == 1:
            self.prune()
        return name

    def path(self, name: str) -> Path | None:
        if not _NAME.match(name):
            return None
        p = self.root / name
        return p if p.is_file() else None

    def prune(self) -> int:
        files = sorted(self.root.glob("*.jpg"))
        cutoff = (time.time() - KEEP_S) * 1000
        old = [f for f in files if _NAME.match(f.name) and int(f.name[:13]) < cutoff]
        extra = files[: max(0, len(files) - MAX_FILES)]
        gone = 0
        for f in {*old, *extra}:
            try:
                f.unlink()
                gone += 1
            except OSError:
                pass
        return gone
