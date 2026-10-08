"""Turn any audio file into the MP3 the Skelly firmware plays reliably.

Matches the original controller: loudest single channel, 44.1 kHz, DC removed,
loudness-normalised, soft-clipped, a quiet "wake tone" in front so the speaker
amp is awake before the sound starts, then 64 kbps CBR mono MP3.
Decoding uses ffmpeg, so anything ffmpeg reads (mp3, wav, m4a, ogg, flac...) works.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import numpy as np

SAMPLE_RATE = 44100
BITRATE_KBPS = 64
MAX_SECONDS = 300.0
TARGET_RMS = 14400.0     # about -7 dBFS
PEAK_CEILING = 31100.0   # about -0.4 dBFS
MAX_GAIN = 10.0
WAKE_TONE_MS = 450
WAKE_TONE_AMPLITUDE = 900.0


class AudioError(ValueError):
    pass


def decode(data: bytes) -> tuple[np.ndarray, int]:
    """Decode any audio file to float32 samples shaped (frames, channels) in int16 range."""
    if not shutil.which("ffmpeg"):
        raise AudioError("ffmpeg isn't installed, so audio can't be converted.")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=channels,sample_rate", "-of", "json", "-i", "pipe:0"],
        input=data, capture_output=True, timeout=60,
    )
    try:
        stream = json.loads(probe.stdout)["streams"][0]
        channels, rate = int(stream["channels"]), int(stream["sample_rate"])
    except (ValueError, KeyError, IndexError):
        raise AudioError("That doesn't look like an audio file.") from None
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"],
        input=data, capture_output=True, timeout=180,
    )
    if out.returncode != 0 or not out.stdout:
        raise AudioError("Couldn't decode that audio file.")
    pcm = np.frombuffer(out.stdout, dtype="<i2").astype(np.float32)
    frames = len(pcm) // channels
    return pcm[: frames * channels].reshape(frames, channels), rate


def wake_tone(rate: int = SAMPLE_RATE, ms: int = WAKE_TONE_MS) -> np.ndarray:
    """170 Hz with a slow wobble, faded in and out; too quiet to notice, enough to wake the amp."""
    n = int(rate * ms / 1000)
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    t = np.arange(n) / rate
    freq = 170 + 18 * np.sin(2 * np.pi * 5 * t)
    tone = WAKE_TONE_AMPLITUDE * np.sin(2 * np.pi * np.cumsum(freq) / rate)
    fade = max(1, int(0.05 * rate))
    env = np.ones(n)
    ramp = np.linspace(0, 1, min(fade, n))
    env[: len(ramp)] = ramp
    env[n - len(ramp):] = np.minimum(env[n - len(ramp):], ramp[::-1])
    return (tone * env).astype(np.float32)


def process(samples: np.ndarray, rate: int, *, normalize: bool = True, wake_ms: int = WAKE_TONE_MS) -> np.ndarray:
    """Mono 44.1 kHz int16 samples ready to encode."""
    if samples.ndim == 1:
        samples = samples[:, None]
    if samples.shape[0] == 0:
        raise AudioError("That audio file is empty.")
    if samples.shape[0] / rate > MAX_SECONDS:
        raise AudioError(f"Sounds can be at most {int(MAX_SECONDS // 60)} minutes long.")
    # Keep the loudest channel rather than averaging, so a one-sided stereo file stays loud.
    rms = np.sqrt(np.mean(samples.astype(np.float64) ** 2, axis=0))
    x = samples[:, int(np.argmax(rms))].astype(np.float64)
    if rate != SAMPLE_RATE:
        n_out = max(1, round(len(x) * SAMPLE_RATE / rate))
        x = np.interp(np.linspace(0, len(x) - 1, n_out), np.arange(len(x)), x)
    x -= x.mean()
    if normalize:
        r, peak = np.sqrt(np.mean(x ** 2)), np.max(np.abs(x))
        if r > 1 and peak > 1:
            x *= min(MAX_GAIN, TARGET_RMS / r, PEAK_CEILING / peak)
    x = PEAK_CEILING * np.tanh(x / PEAK_CEILING)
    x = np.concatenate([wake_tone(SAMPLE_RATE, wake_ms), x])
    return np.clip(np.round(x), -32768, 32767).astype("<i2")


def encode_mp3(pcm: np.ndarray) -> bytes:
    import lameenc

    enc = lameenc.Encoder()
    enc.set_bit_rate(BITRATE_KBPS)
    enc.set_in_sample_rate(SAMPLE_RATE)
    enc.set_out_sample_rate(SAMPLE_RATE)
    enc.set_channels(1)
    enc.set_quality(2)
    return bytes(enc.encode(pcm.tobytes()) + enc.flush())


def prepare(data: bytes, *, normalize: bool = True, wake_ms: int = WAKE_TONE_MS) -> tuple[bytes, float]:
    """Any audio file in, (Skelly-ready MP3 bytes, duration in seconds) out."""
    samples, rate = decode(data)
    pcm = process(samples, rate, normalize=normalize, wake_ms=wake_ms)
    return encode_mp3(pcm), len(pcm) / SAMPLE_RATE
