"""Voice test: say a line in different voices through Skelly, to compare them by ear.

ElevenLabs Flash is the fast voice conversations use; ElevenLabs v3 is the expressive
model (better pauses and tone, slower to start). Kokoro is a free voice that runs on the
mini PC; its model (~120 MB) downloads the first time it's used. Kokoro reads "..." like a
comma, so lines are split there and real silence is put in.
"""

from __future__ import annotations

import array
import asyncio
import logging
import re
import threading
import time
import urllib.request

import httpx

from .settings import data_dir

log = logging.getLogger(__name__)

KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_FILES = ("kokoro-v1.0.int8.onnx", "voices-v1.0.bin")

OPTIONS = [
    {"id": "agent", "label": "Your ElevenLabs agent", "note": "Exactly what the agent sounds like in conversations"},
    {"id": "eleven_flash", "label": "ElevenLabs Flash", "note": "What conversations use: fast"},
    {"id": "eleven_v3", "label": "ElevenLabs v3", "note": "Most expressive; slower to start"},
    {"id": "kokoro:bm_lewis", "label": "Kokoro: Lewis", "note": "Free, on the mini PC"},
    {"id": "kokoro:bm_george", "label": "Kokoro: George", "note": "Free, on the mini PC"},
    {"id": "kokoro:bm_fable", "label": "Kokoro: Fable", "note": "Free, on the mini PC"},
]

_kokoro = None
_kokoro_lock = threading.Lock()


def _kokoro_engine():
    global _kokoro
    with _kokoro_lock:
        if _kokoro is None:
            from kokoro_onnx import Kokoro

            d = data_dir() / "models"
            d.mkdir(parents=True, exist_ok=True)
            for f in KOKORO_FILES:
                if not (d / f).exists():
                    log.info("downloading %s", f)
                    tmp = d / f"{f}.part"
                    urllib.request.urlretrieve(KOKORO_URL + f, tmp)
                    tmp.replace(d / f)
            _kokoro = Kokoro(str(d / KOKORO_FILES[0]), str(d / KOKORO_FILES[1]))
        return _kokoro


def _kokoro_say(text: str, voice: str, speed: float) -> tuple[bytes, int]:
    k = _kokoro_engine()
    out = array.array("h")
    rate = 24000
    for i, part in enumerate(p.strip() for p in re.split(r"\.{3}|…", text)):
        if i:
            out.extend([0] * int(rate * 0.45))  # the dramatic pause
        if part:
            samples, rate = k.create(part, voice=voice, speed=speed, lang="en-gb")
            out.extend(int(max(-1.0, min(1.0, float(x))) * 32767) for x in samples)
    return out.tobytes(), rate


async def _eleven(key: str, voice_id: str, model: str, text: str,
                  settings: dict | None = None) -> tuple[bytes, int]:
    body = {"text": text, "model_id": model}
    if settings:
        body["voice_settings"] = settings
    async with httpx.AsyncClient(timeout=60) as http:
        r = await http.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                            params={"output_format": "pcm_16000"}, headers={"xi-api-key": key}, json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:200]}")
        return r.content, 16000


async def agent_voice(key: str, agent_id: str) -> dict:
    """The voice, model and tuning an ElevenLabs agent speaks with."""
    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.get(f"https://api.elevenlabs.io/v1/convai/agents/{agent_id}", headers={"xi-api-key": key})
        if r.status_code >= 400:
            raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:200]}")
    tts = r.json().get("conversation_config", {}).get("tts", {})
    settings = {k: tts[k] for k in ("stability", "similarity_boost", "speed") if tts.get(k) is not None}
    return {"voice_id": tts.get("voice_id"), "model_id": tts.get("model_id") or "eleven_flash_v2_5",
            "settings": settings}


async def synth(option: str, text: str, *, eleven_key: str | None, eleven_voice: str,
                speed: float = 1.15, agent_id: str | None = None) -> tuple[bytes, int, float]:
    """Returns PCM, its rate, and how long it took to make (seconds)."""
    t = time.monotonic()
    if option == "agent":
        if not (eleven_key and agent_id):
            raise ValueError("Pick your ElevenLabs agent on the Conversation page first.")
        av = await agent_voice(eleven_key, agent_id)
        pcm, rate = await _eleven(eleven_key, av["voice_id"] or eleven_voice, av["model_id"], text, av["settings"])
    elif option.startswith("kokoro:"):
        pcm, rate = await asyncio.to_thread(_kokoro_say, text, option.split(":", 1)[1], speed)
    elif option in ("eleven_flash", "eleven_v3"):
        if not eleven_key:
            raise ValueError("Add your ElevenLabs API key in Settings > API keys first.")
        model = "eleven_flash_v2_5" if option == "eleven_flash" else "eleven_v3"
        pcm, rate = await _eleven(eleven_key, eleven_voice, model, text)
    else:
        raise ValueError("Unknown voice")
    return pcm, rate, time.monotonic() - t


def kokoro_ready() -> bool:
    return all((data_dir() / "models" / f).exists() for f in KOKORO_FILES)

