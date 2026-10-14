"""Auto sync: measure how late each speaker plays, by ear.

A short run of chirps is played on one speaker through the same kind of stream the music uses,
while Skelly's mic records. Lining the recording up with the chirps (a matched filter) gives the
delay from handing audio to PipeWire to it coming out of that speaker, Bluetooth and all.
"""

from __future__ import annotations

import asyncio
import logging

import numpy as np

from .audio_io import _stop

log = logging.getLogger(__name__)

RATE = 48000
MAX_DELAY_S = 3.5  # longer than any Bluetooth speaker we expect
MIN_SCORE = 8.0  # how far the match must stand above the background to be trusted


def chirps(rate: int = RATE) -> np.ndarray:
    """Three rising sweeps (600 Hz to 3.5 kHz), loud and easy to pick out of room noise."""
    t = np.arange(int(rate * 0.15)) / rate
    f0, f1 = 600.0, 3500.0
    sweep = np.sin(2 * np.pi * (f0 * t + (f1 - f0) / (2 * t[-1]) * t * t))
    sweep *= np.hanning(len(sweep))
    gap = np.zeros(int(rate * 0.23))  # uneven gaps so one sweep can't be mistaken for another
    gap2 = np.zeros(int(rate * 0.37))
    return (0.6 * np.concatenate([sweep, gap, sweep, gap2, sweep])).astype(np.float32)


def find_delay(recording: np.ndarray, signal: np.ndarray, rate: int = RATE) -> tuple[float, float]:
    """Where `signal` starts in `recording`, in seconds, and how sure we are (peak / noise)."""
    if len(recording) < len(signal):
        return 0.0, 0.0
    n = 1 << int(np.ceil(np.log2(len(recording) + len(signal))))
    corr = np.fft.irfft(np.fft.rfft(recording, n) * np.conj(np.fft.rfft(signal, n)), n)
    corr = np.abs(corr[: len(recording) - len(signal) + 1])
    i = int(np.argmax(corr))
    noise = float(np.median(corr)) or 1e-9
    return i / rate, float(corr[i]) / noise


async def measure(sink: str | None, source: str | None) -> float:
    """Seconds from writing audio for `sink` to hearing it on `source` (the mic)."""
    sig = chirps()
    rec = ["parec", "--raw", "--format=s16le", f"--rate={RATE}", "--channels=1", "--latency-msec=20",
           "--client-name=Supreme Skelly auto sync"]
    if source:
        rec.append(f"--device={source}")
    play = ["pacat", "--raw", "--format=s16le", f"--rate={RATE}", "--channels=2", "--latency-msec=250",
            "--volume=65536", "--client-name=Supreme Skelly auto sync"]
    if sink:
        play.append(f"--device={sink}")
    mic = await asyncio.create_subprocess_exec(*rec, stdout=asyncio.subprocess.PIPE,
                                               stderr=asyncio.subprocess.DEVNULL)
    out = None
    chunks: list[bytes] = []
    got = 0
    try:
        assert mic.stdout
        want = int(RATE * (0.6 + MAX_DELAY_S + len(sig) / RATE)) * 2
        # Let the mic settle, then start the speaker just as the music does
        while got < int(RATE * 0.4) * 2:
            chunks.append(await asyncio.wait_for(mic.stdout.read(4096), 5))
            got += len(chunks[-1])
        out = await asyncio.create_subprocess_exec(*play, stdin=asyncio.subprocess.PIPE,
                                                   stderr=asyncio.subprocess.DEVNULL)
        await asyncio.sleep(0.4)  # the music player waits this long before its clock starts
        start = got
        while True:  # catch up with what the mic recorded meanwhile
            try:
                chunk = await asyncio.wait_for(mic.stdout.read(4096), 0.02)
            except TimeoutError:
                break
            if not chunk:
                break
            chunks.append(chunk)
            got += len(chunk)
            start = got
        assert out.stdin
        pcm = (np.repeat(sig, 2) * 32767).astype("<i2").tobytes()
        out.stdin.write(pcm + bytes(int(RATE * 0.5) * 4))
        while got < start + want:
            chunk = await asyncio.wait_for(mic.stdout.read(4096), 5)
            if not chunk:
                raise ConnectionError("The microphone stopped (is it plugged in?)")
            chunks.append(chunk)
            got += len(chunk)
    finally:
        await _stop(out)
        await _stop(mic)
    data = b"".join(chunks)
    recording = np.frombuffer(data[start - start % 2:], "<i2").astype(np.float32) / 32768
    delay, score = find_delay(recording, sig)
    log.info("auto sync on %s: %.0f ms (match %.1f)", sink, delay * 1000, score)
    if score < MIN_SCORE:
        raise LookupError("Couldn't hear the test sound. Turn the speaker up, keep the room quiet, and try again")
    return delay


def settings(voice_s: float, band_s: float | None, audio_delay_s: float) -> dict:
    """Timing slider values from the measured delays (Skelly's speaker and the other speakers)."""
    out = {"sync_ms": int(round((voice_s - audio_delay_s) * 1000 / 10) * 10)}
    if band_s is not None:
        # + holds the other speakers back, - holds Skelly back: whichever is quicker waits
        out["speakers_offset_ms"] = int(round((voice_s - band_s) * 1000 / 10) * 10)
    return out
