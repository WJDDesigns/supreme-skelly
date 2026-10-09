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
MIN_FACE_PX = 16  # below this there's nothing to recognise
SHARPEN_BELOW_PX = 64  # small faces are enlarged and re-found before fingerprinting
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
            self._det = cv2.FaceDetectorYN.create(str(MODEL_DIR / DETECTOR), "", (320, 320), 0.7, 0.3, 50)
            self._rec = cv2.FaceRecognizerSF.create(str(MODEL_DIR / RECOGNIZER), "")
        return cv2

    def process(self, jpeg: bytes, ignore: list[list[float]], focus: list[float] | None = None) -> list[Seen]:
        """All faces in the frame. `focus` ([x, y, w, h]) is also searched enlarged 2.5×,
        so faces too small to find in the whole picture (people far from the camera but right
        in front of Skelly) are still found."""
        import numpy as np

        with self._lock:
            cv2 = self._load()
            img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                return []
            h, w = img.shape[:2]
            self._det.setInputSize((w, h))
            _, faces = self._det.detect(img)
            faces = list(faces) if faces is not None else []
            if focus:
                faces += self._focus_faces(cv2, img, focus, faces)
            out = []
            for f in faces:
                x, y, fw, fh = (float(v) for v in f[:4])
                if min(fw, fh) < MIN_FACE_PX:
                    continue
                box = [max(0.0, x / w), max(0.0, y / h), fw / w, fh / h]
                cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
                if any(zx <= cx <= zx + zw and zy <= cy <= zy + zh for zx, zy, zw, zh in ignore):
                    continue  # Skelly's own skull, a poster, a mask on the porch
                aligned = self._aligned(cv2, img, f) if min(fw, fh) < SHARPEN_BELOW_PX else None
                if aligned is None:
                    aligned = self._rec.alignCrop(img, f)
                emb = self._rec.feature(aligned).flatten()
                emb = emb / (np.linalg.norm(emb) or 1.0)
                ok, thumb = cv2.imencode(".jpg", cv2.resize(aligned, (96, 96)), [cv2.IMWRITE_JPEG_QUALITY, 80])
                out.append(Seen(box=box, embedding=emb.astype(float).tolist(),
                                thumb=base64.b64encode(thumb.tobytes()).decode() if ok else ""))
            return out


    def _focus_faces(self, cv2, img, focus, already) -> list:
        import numpy as np

        h, w = img.shape[:2]
        fx, fy, fw, fh = focus
        x0, y0 = int(max(0, fx * w)), int(max(0, fy * h))
        x1, y1 = int(min(w, (fx + fw) * w)), int(min(h, (fy + fh) * h))
        if x1 - x0 < 20 or y1 - y0 < 20:
            return []
        k = 2.5
        crop = cv2.resize(img[y0:y1, x0:x1], None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
        ch, cw = crop.shape[:2]
        self._det.setInputSize((cw, ch))
        _, found = self._det.detect(crop)
        self._det.setInputSize((w, h))
        out = []
        for g in found if found is not None else []:
            # YuNet rows: x, y, w, h, then five landmarks (x, y), then the score.
            g = g.copy()
            g[2:4] = g[2:4] / k
            for i in (0, 4, 6, 8, 10, 12):
                g[i] = g[i] / k + x0
                g[i + 1] = g[i + 1] / k + y0
            cx, cy = g[0] + g[2] / 2, g[1] + g[3] / 2
            if any(abs(cx - (a[0] + a[2] / 2)) < a[2] and abs(cy - (a[1] + a[3] / 2)) < a[3] for a in already):
                continue  # the whole-picture pass found this one already
            out.append(np.asarray(g, dtype=np.float32))
        return out

    def _aligned(self, cv2, img, f):
        """Enlarge the area around a small, distant face and find it again for cleaner landmarks."""
        h, w = img.shape[:2]
        x, y, fw, fh = (float(v) for v in f[:4])
        pad = max(fw, fh)
        x0, y0 = int(max(0, x - pad)), int(max(0, y - pad))
        x1, y1 = int(min(w, x + fw + pad)), int(min(h, y + fh + pad))
        scale = 96 / max(fw, fh)
        crop = cv2.resize(img[y0:y1, x0:x1], None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        ch, cw = crop.shape[:2]
        self._det.setInputSize((cw, ch))
        _, found = self._det.detect(crop)
        self._det.setInputSize((w, h))
        if found is None or not len(found):
            return None
        best = max(found, key=lambda g: g[2] * g[3])
        return self._rec.alignCrop(crop, best)


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
skelly skeleton mister mr
""".split())


# Answers to "what's your name?" that aren't names.
_NOT_ANSWERS = _NOT_NAMES | set("""
yes yeah yep yup no nope nah hi hello hey hiya what why who how where when um uh hmm oh and or
me my mine i we us our nobody nothing none thanks thank bye cool nice wow ha haha lol trick treat
mom mommy dad daddy brother sister friend he she they his her their is was it that
""".split())
_ASKED = re.compile(r"\bnames?\b|\bwho (?:are|is) (?:you|this|that)\b|\bwhat do (?:they|people) call you\b", re.I)


def asked_name(said: str) -> bool:
    """Whether Skelly's line asked who someone is ("What's your name?")."""
    return bool(_ASKED.search(said or ""))


def heard_names(text: str, asked: bool = False) -> list[str]:
    """Every name given in one go: "I'm Willow and this is Gabe" gives both.

    With `asked` (Skelly just asked their name), a bare answer counts too: kids just say
    "Willow", or "Willow and Gabe".
    """
    names: list[str] = []
    rest = text or ""
    while (name := heard_name(rest)) and name not in names:
        names.append(name)
        m = re.search(re.escape(name.split()[0]), rest, re.I)
        rest = rest[m.end():] if m else ""
    if names or not asked:
        return names
    words = re.sub(r"[^A-Za-z' \-]+", " ", text or "").split()
    if not 1 <= len(words) <= 5:
        return []  # a whole sentence, not an answer
    for w in words:
        if w.lower() in _NOT_ANSWERS or len(w) < 2:
            continue
        if not w[0].isupper() and len(words) > 1:
            continue  # speech-to-text capitalises names mid-sentence
        if (name := w.capitalize()) not in names:
            names.append(name)
    return names[:3]


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
        last = m.group(2)
        if last and last[0].isupper() and last.lower() not in _NOT_NAMES | {"and", "or", "but"}:
            name += f" {last}"
        return name
    return None
