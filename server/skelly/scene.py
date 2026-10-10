"""Photos of Skelly's yard and Halloween display, so he can talk about his surroundings.

Each photo is shrunk to a modest JPEG, described once by the vision AI (Claude, else OpenAI),
and the descriptions plus any note the owner adds are handed to every conversation as
background. Everything stays in the data folder on the mini PC.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path

from .settings import data_dir

log = logging.getLogger(__name__)

MAX_PHOTOS = 8
MAX_SIDE = 1280

DESCRIBE_PROMPT = (
    "This photo shows the yard and holiday display around a talking animatronic figure. "
    "Describe what's in it so the figure can mention it to visitors: decorations, props, lights, "
    "the house and yard, anything fun or festive. Be concrete (colours, where things are). "
    'Reply with JSON only: {"description": "two to four sentences"}'
)


def shrink(raw: bytes) -> bytes:
    """Any common image (JPEG, PNG, WebP) to a JPEG no larger than MAX_SIDE on its long side."""
    import cv2
    import numpy as np

    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("That file isn't a photo Skelly can read. Use a JPEG or PNG.")
    h, w = img.shape[:2]
    scale = MAX_SIDE / max(h, w)
    if scale < 1:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise ValueError("Couldn't process that photo")
    return buf.tobytes()


class Scene:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or data_dir() / "scene"
        self.index = self.root / "scene.json"

    def load(self) -> list[dict]:
        try:
            return json.loads(self.index.read_text())
        except (OSError, ValueError):
            return []

    def _save(self, items: list[dict]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.index.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, indent=1))
        tmp.replace(self.index)

    def path(self, photo_id: str) -> Path:
        return self.root / f"{Path(photo_id).name}.jpg"

    def add(self, raw: bytes) -> dict:
        items = self.load()
        if len(items) >= MAX_PHOTOS:
            raise ValueError(f"Up to {MAX_PHOTOS} photos. Remove one first.")
        jpeg = shrink(raw)
        item = {"id": uuid.uuid4().hex[:12], "note": "", "description": "", "added": time.time()}
        self.root.mkdir(parents=True, exist_ok=True)
        self.path(item["id"]).write_bytes(jpeg)
        self._save([*items, item])
        return item

    def update(self, photo_id: str, **fields) -> dict:
        items = self.load()
        for it in items:
            if it["id"] == photo_id:
                it.update({k: str(v)[:600] for k, v in fields.items() if k in ("note", "description")})
                self._save(items)
                return it
        raise KeyError(photo_id)

    def remove(self, photo_id: str) -> None:
        self._save([it for it in self.load() if it["id"] != photo_id])
        self.path(photo_id).unlink(missing_ok=True)

    def context(self) -> str:
        """What Skelly knows about his surroundings, for the start of a conversation."""
        parts = [" ".join(p for p in (it.get("note", "").strip(), it.get("description", "").strip()) if p)
                 for it in self.load()]
        parts = [p for p in parts if p]
        if not parts:
            return ""
        return ("About your surroundings (from photos of the yard and display; mention things when it fits, "
                "don't list them): " + " | ".join(parts))


async def describe_photo(vault, jpeg: bytes) -> str:
    from .vision import _ask_vision

    verdict = await _ask_vision(vault, jpeg, DESCRIBE_PROMPT, precise=True)
    return str((verdict or {}).get("description", "")).strip()
