"""Free, on-device hearing and voice for the Claude conversation mode.

* Hearing: faster-whisper (OpenAI's Whisper, int8 on the CPU). "base.en" turns a few
  seconds of speech into text in well under a second on the N100.
* Voice: Piper, a small neural text-to-speech engine. Skelly's "depth" is done by playing
  the voice back slower than it was made, which lowers the pitch like a slowed-down record.

Models are downloaded once, on first use (or baked into the Docker image), into the data
directory so they survive updates. Nothing leaves the mini PC.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import tarfile
import threading
import urllib.request
from pathlib import Path

from .settings import data_dir

log = logging.getLogger(__name__)

WHISPER_MODEL = os.environ.get("SKELLY_WHISPER_MODEL", "base.en")
HF = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en"
GH = "https://github.com/rhasspy/piper/releases/download/v0.0.2"

# key: (label, Hugging Face path under en/, GitHub v0.0.2 tarball fallback or None, speaker name or None)
VOICES = {
    "obadiah": ("Obadiah: gloomy, posh British man", "en_GB/semaine/medium/en_GB-semaine-medium", None, "obadiah"),
    "alan": ("Alan: British man", "en_GB/alan/medium/en_GB-alan-medium", "voice-en-gb-alan-low.tar.gz", None),
    "northern": ("Northern English man", "en_GB/northern_english_male/medium/en_GB-northern_english_male-medium",
                 None, None),
    "spike": ("Spike: grumpy British man", "en_GB/semaine/medium/en_GB-semaine-medium", None, "spike"),
    "ryan": ("Ryan: deep American man", "en_US/ryan/medium/en_US-ryan-medium", "voice-en-us-ryan-medium.tar.gz", None),
    "joe": ("Joe: American man", "en_US/joe/medium/en_US-joe-medium", None, None),
    "lessac": ("Lessac: American woman", "en_US/lessac/medium/en_US-lessac-medium", "voice-en-us-lessac-medium.tar.gz",
               None),
}
DEFAULT_VOICE = "alan"


def models_dir() -> Path:
    return Path(os.environ.get("SKELLY_MODELS_DIR", data_dir() / "models"))


def baked_dir() -> Path:
    return Path(os.environ.get("SKELLY_BAKED_MODELS", "/app/models"))


def voices() -> list[dict]:
    return [{"id": k, "name": v[0]} for k, v in VOICES.items()]


# -- downloads ------------------------------------------------------------------------

def _fetch(url: str, timeout: float = 120) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 - fixed https URLs above
        return r.read()


def voice_path(key: str) -> Path:
    """The voice's .onnx file, downloading it the first time. Blocking."""
    if key not in VOICES:
        key = DEFAULT_VOICE
    _, hf, gh, _ = VOICES[key]
    name = hf.rsplit("/", 1)[1]
    for d in (models_dir() / "piper", baked_dir() / "piper"):
        if (d / f"{name}.onnx").exists() and (d / f"{name}.onnx.json").exists():
            return d / f"{name}.onnx"
    dest = models_dir() / "piper"
    dest.mkdir(parents=True, exist_ok=True)
    try:
        model, config = _fetch(f"{HF}/{hf}.onnx"), _fetch(f"{HF}/{hf}.onnx.json")
    except OSError as exc:
        if not gh:
            raise RuntimeError(f"Couldn't download the {key} voice: {exc}") from exc
        log.info("Hugging Face unreachable (%s); trying the GitHub copy of %s", exc, key)
        try:
            with tarfile.open(fileobj=io.BytesIO(_fetch(f"{GH}/{gh}")), mode="r:gz") as tar:
                files = {Path(m.name).name: m for m in tar.getmembers() if m.isfile()}
                onnx = next(n for n in files if n.endswith(".onnx"))
                model = tar.extractfile(files[onnx]).read()
                config = tar.extractfile(files[onnx + ".json"]).read()
        except (OSError, StopIteration, KeyError, tarfile.TarError) as exc2:
            raise RuntimeError(f"Couldn't download the {key} voice: {exc2}") from exc2
    json.loads(config)  # a broken download shouldn't be saved as a voice
    tmp = dest / f"{name}.onnx.part"
    tmp.write_bytes(model)
    (dest / f"{name}.onnx.json").write_bytes(config)
    tmp.replace(dest / f"{name}.onnx")
    log.info("Downloaded the %s voice (%.0f MB)", key, len(model) / 1e6)
    return dest / f"{name}.onnx"


# -- engines --------------------------------------------------------------------------

_lock = threading.Lock()
_voices: dict[str, object] = {}
_whisper = None


def _voice(key: str):
    path = str(voice_path(key))
    with _lock:
        if path not in _voices:
            from piper import PiperVoice

            _voices[path] = PiperVoice.load(path)
        return _voices[path]


def _stt():
    global _whisper
    with _lock:
        if _whisper is None:
            from faster_whisper import WhisperModel

            root = models_dir() / "whisper"
            baked = baked_dir() / "whisper"
            _whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8",
                                    cpu_threads=min(4, os.cpu_count() or 4),
                                    download_root=str(baked if baked.is_dir() and any(baked.iterdir()) else root))
        return _whisper


def synthesize(text: str, voice: str = DEFAULT_VOICE, depth: int = 100) -> tuple[bytes, int]:
    """16-bit mono PCM and the rate to play it at. ``depth`` below 100 plays it slower and
    deeper (85 = 15% lower). Blocking."""
    from piper.config import SynthesisConfig

    voice = voice if voice in VOICES else DEFAULT_VOICE
    v = _voice(voice)
    # A touch slower than Piper's default reads as more deliberate (and gives a skeleton time to clack).
    speaker = VOICES[voice][3]
    sid = (getattr(v.config, "speaker_id_map", None) or {}).get(speaker) if speaker else None
    chunks = list(v.synthesize(text, SynthesisConfig(speaker_id=sid, length_scale=1.1)))
    pcm = b"".join(c.audio_int16_bytes for c in chunks)
    rate = chunks[0].sample_rate if chunks else 22050
    return pcm, max(8000, int(rate * max(60, min(120, depth)) / 100))


def transcribe(pcm: bytes, rate: int = 16000) -> str:
    """Text of one utterance of 16-bit mono PCM at 16 kHz. Blocking."""
    import numpy as np

    if rate != 16000:
        raise ValueError("Whisper wants 16 kHz audio")
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    segments, _ = _stt().transcribe(audio, language="en", beam_size=1, vad_filter=False,
                                    condition_on_previous_text=False)
    text = " ".join(s.text.strip() for s in segments).strip()
    # Whisper "hears" these in silence and noise.
    return "" if text.lower().strip(" .!?") in {"", "you", "thank you", "thanks for watching", "bye"} else text


async def warm_up(stt: bool, tts: bool, voice: str = DEFAULT_VOICE) -> None:
    """Load (and on first use download) the models before the first visitor speaks."""
    if stt:
        await asyncio.to_thread(_stt)
    if tts:
        await asyncio.to_thread(_voice, voice if voice in VOICES else DEFAULT_VOICE)
