"""Face memory: Skelly recognises people he's met, entirely on the mini PC.

OpenCV's YuNet finds faces (many per frame, so crowds are fine) and SFace turns each one
into a 128-number fingerprint. Fingerprints of people who told Skelly their name are kept
in faces.json next to the settings, with a small thumbnail so you can see who's who.
Nothing about faces leaves the machine.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .settings import data_dir

log = logging.getLogger(__name__)

MODEL_DIR = Path(os.environ.get("SKELLY_MODEL_DIR", "/app/models"))
DETECTOR = "face_detection_yunet_2023mar.onnx"
RECOGNIZER = "face_recognition_sface_2021dec.onnx"
MATCH = 0.40  # SFace cosine similarity; OpenCV suggests 0.363, a bit stricter avoids mix-ups
MIN_FACE_PX = 36  # smaller faces are too blurry to remember reliably
MAX_SAMPLES = 12  # fingerprints kept per person (different angles and light)


@dataclass
class Seen:
    """One face in the current frame."""

    box: list[float]  # x, y, w, h as fractions of the frame
    embedding: list[float]
    thumb: str  # base64 JPEG, ~96 px
    person_id: str | None = None
    name: str | None = None
    similarity: float = 0.0
    ts: float = field(default_factory=time.time)

    @property
    def area(self) -> float:
        return self.box[2] * self.box[3]

    def public(self, index: int) -> dict:
        return {"index": index, "box": [round(v, 4) for v in self.box], "name": self.name,
                "person_id": self.person_id, "similarity": round(self.similarity, 2)}


class FaceEngine:
    """Detect and fingerprint faces in a frame. Models load on first use."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._det = self._rec = None

    def available(self) -> bool:
        try:
            import cv2  # noqa: F401
        except ImportError:
            return False
        return (MODEL_DIR / DETECTOR).exists() and (MODEL_DIR / RECOGNIZER).exists()

    def _load(self):
        import cv2

        if self._det is None:
            self._det = cv2.FaceDetectorYN.create(str(MODEL_DIR / DETECTOR), "", (320, 320), 0.8, 0.3, 50)
            self._rec = cv2.FaceRecognizerSF.create(str(MODEL_DIR / RECOGNIZER), "")
        return cv2

    def process(self, jpeg: bytes, ignore: list[list[float]]) -> list[Seen]:
        import numpy as np

        with self._lock:
            cv2 = self._load()
            img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                return []
            h, w = img.shape[:2]
            self._det.setInputSize((w, h))
            _, faces = self._det.detect(img)
            out = []
            for f in faces if faces is not None else []:
                x, y, fw, fh = (float(v) for v in f[:4])
                if min(fw, fh) < MIN_FACE_PX:
                    continue
                box = [max(0.0, x / w), max(0.0, y / h), fw / w, fh / h]
                cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
                if any(zx <= cx <= zx + zw and zy <= cy <= zy + zh for zx, zy, zw, zh in ignore):
                    continue  # Skelly's own skull, a poster, a mask on the porch
                aligned = self._rec.alignCrop(img, f)
                emb = self._rec.feature(aligned).flatten()
                emb = emb / (np.linalg.norm(emb) or 1.0)
                ok, thumb = cv2.imencode(".jpg", cv2.resize(aligned, (96, 96)), [cv2.IMWRITE_JPEG_QUALITY, 80])
                out.append(Seen(box=box, embedding=emb.astype(float).tolist(),
                                thumb=base64.b64encode(thumb.tobytes()).decode() if ok else ""))
            return out


class FaceMemory:
    """People Skelly knows, by name, with a few fingerprints each."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "faces.json"
        self.people: list[dict] = []
        try:
            self.people = json.loads(self.path.read_text()).get("people", [])
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            log.warning("Ignoring unreadable face memory %s: %s", self.path, exc)

    def public(self) -> list[dict]:
        return [{"id": p["id"], "name": p["name"], "thumb": p.get("thumb", ""), "samples": len(p["embeddings"]),
                 "first_seen": p.get("first_seen"), "last_seen": p.get("last_seen"), "visits": p.get("visits", 0)}
                for p in sorted(self.people, key=lambda p: -(p.get("last_seen") or 0))]

    def match(self, embedding: list[float]) -> tuple[dict | None, float]:
        best, best_sim = None, 0.0
        for p in self.people:
            for e in p["embeddings"]:
                sim = sum(a * b for a, b in zip(embedding, e, strict=False))
                if sim > best_sim:
                    best, best_sim = p, sim
        return (best, best_sim) if best_sim >= MATCH else (None, best_sim)

    def remember(self, name: str, seen: Seen) -> dict:
        """Add this face under a name: to the existing person of that name, or a new one."""
        name = name.strip()[:40]
        person = next((p for p in self.people if p["name"].casefold() == name.casefold()), None)
        now = time.time()
        if person is None:
            person = {"id": uuid.uuid4().hex[:10], "name": name, "embeddings": [], "thumb": seen.thumb,
                      "first_seen": now, "last_seen": now, "visits": 1}
            self.people.append(person)
        self.add_sample(person, seen)
        return person

    def add_sample(self, person: dict, seen: Seen) -> None:
        """Keep a varied set of fingerprints: skip near-duplicates, drop the oldest when full."""
        if any(sum(a * b for a, b in zip(seen.embedding, e, strict=False)) > 0.92 for e in person["embeddings"]):
            return
        person["embeddings"] = (person["embeddings"] + [seen.embedding])[-MAX_SAMPLES:]
        if seen.thumb and seen.area >= 0:
            person["thumb"] = person.get("thumb") or seen.thumb
        self.save()

    def seen_now(self, person: dict) -> bool:
        """Mark a sighting; True if this counts as a new visit (not seen for 10 minutes)."""
        now = time.time()
        new_visit = now - (person.get("last_seen") or 0) > 600
        person["last_seen"] = now
        if new_visit:
            person["visits"] = person.get("visits", 0) + 1
            self.save()
        return new_visit

    def rename(self, person_id: str, name: str) -> None:
        for p in self.people:
            if p["id"] == person_id:
                p["name"] = name.strip()[:40]
        self.save()

    def forget(self, person_id: str | None = None) -> None:
        self.people = [p for p in self.people if person_id and p["id"] != person_id]
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"people": self.people}, f)
        tmp.replace(self.path)


# -- hearing a name -------------------------------------------------------------------

_INTRO = re.compile(
    r"\b(?:my name(?:'s| is)|i'm|i am|im|call me|they call me|it's|this is|name's)\s+"
    r"([A-Za-z][A-Za-z'\-]{1,20})(?:\s+([A-Z][a-z'\-]{1,20}))?", re.I)
_NOT_NAMES = set("""
a an the here just so not very really good fine okay ok sorry happy hungry scared tired cold hot
going gonna looking trying from with your you a skeleton ghost witch kid boy girl new back fine
great doing well alright sure ready done excited spooky dressed wearing over about only still
skelly skeleton bones mister mr
""".split())


def heard_name(text: str) -> str | None:
    """A name someone gave for themselves ("I'm Sarah", "my name is Jo Smith"), or None."""
    for m in _INTRO.finditer(text or ""):
        first = m.group(1)
        if first.lower() in _NOT_NAMES:
            continue
        intro = m.group(0).lower()
        if intro.startswith(("i'm", "i am", "im", "it's", "this is")) and not first[0].isupper():
            continue  # "i'm going" etc. Speech-to-text capitalises real names
        name = first.capitalize()
        if m.group(2) and m.group(2).lower() not in _NOT_NAMES:
            name += f" {m.group(2)}"
        return name
    return None
