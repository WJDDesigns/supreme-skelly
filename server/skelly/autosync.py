"""Auto sync: measure how late each speaker plays, by ear.

A short run of chirps is played on one speaker through the same kind of stream the music uses,
while Skelly's mic records. Lining the recording up with the chirps (a matched filter) gives the
delay from handing audio to PipeWire to it coming out of that speaker, Bluetooth and all.
"""

from __future__ import annotations

import asyncio
import logging
import time

import numpy as np

from .audio_io import MUSIC_BUFFER_MS, _stop

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
    play = ["pacat", "--raw", "--format=s16le", f"--rate={RATE}", "--channels=2", f"--latency-msec={MUSIC_BUFFER_MS}",
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


# -- keeping in sync while a song plays --------------------------------------------------------

LIVE_RATE = 8000  # plenty to time a speaker to the millisecond, and cheap to work with
WINDOW_S = 4.0  # how much of the mic each measurement listens to
LIVE_MIN_SCORE = 10.0  # chance peaks in four seconds of noise reach about 7


def _decimate(x: np.ndarray, factor: int) -> np.ndarray:
    n = len(x) // factor * factor
    return x[:n].reshape(-1, factor).mean(axis=1).astype(np.float32)


def find_lag(mic: np.ndarray, ref: np.ndarray, max_lag: int) -> tuple[int, float, np.ndarray]:
    """How many samples `mic` hears `ref` late, given `ref` starts `max_lag` samples before `mic`.

    A phase-only (GCC-PHAT) correlation: every frequency counts the same, so a booming bass or a
    loud singer can't blur the peak, and room echo doesn't pull it off. Returns the lag, how far
    the peak stands above the rest, and the whole curve (to look for a second speaker in it).
    """
    n = 1 << int(np.ceil(np.log2(len(mic) + len(ref))))
    cross = np.conj(np.fft.rfft(mic, n)) * np.fft.rfft(ref, n)
    corr = np.fft.irfft(cross / (np.abs(cross) + 1e-9), n)[: max_lag + 1]
    curve = np.abs(corr[::-1])  # index = lag: ref[i + max_lag - lag] lines up with mic[i]
    i = int(np.argmax(curve))
    spread = float(np.std(curve)) or 1e-9
    return i, float(curve[i] - np.median(curve)) / spread, curve


class LiveSync:
    """Listens with Skelly's mic while a song plays and works out how late each speaker is heard.

    The player hands over what it sends to Skelly (the voice) and to the other speakers (the band),
    stamped with the song's clock. Lining each up with what the mic hears says when that part of the
    song really comes out of each speaker, whatever the Bluetooth links are doing right now.
    """

    def __init__(self, source: str | None, rate: int = RATE, max_delay_s: float = MAX_DELAY_S) -> None:
        self.source = source
        self.factor = rate // LIVE_RATE
        self.max_lag = int(max_delay_s * LIVE_RATE)
        self.t0: float | None = None  # when song time 0 was handed over (the song's clock)
        self._song: dict[str, list[np.ndarray]] = {"voice": [], "band": []}
        self._song_base = 0  # song sample (at LIVE_RATE) the kept parts start at
        self._mic: list[np.ndarray] = []
        self._mic_kept = 0
        self._mic_end: float | None = None  # wall time of the newest mic sample
        self._proc: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task | None = None

    async def start(self, t0: float) -> None:
        self.t0 = t0
        args = ["parec", "--raw", "--format=s16le", f"--rate={LIVE_RATE}", "--channels=1",
                "--latency-msec=20", "--client-name=Supreme Skelly live sync"]
        if self.source:
            args.append(f"--device={self.source}")
        self._proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                          stderr=asyncio.subprocess.DEVNULL)
        self._task = asyncio.create_task(self._listen(), name="live-sync mic")

    async def _listen(self) -> None:
        assert self._proc and self._proc.stdout
        while True:
            raw = await self._proc.stdout.read(LIVE_RATE // 50 * 2)
            if not raw:
                return
            self.hear(np.frombuffer(raw[: len(raw) // 2 * 2], "<i2").astype(np.float32) / 32768,
                      time.monotonic())

    def hear(self, x: np.ndarray, now: float) -> None:
        """Mic samples, the last of them heard at `now`."""
        self._mic.append(x)
        self._mic_kept += len(x)
        self._mic_end = now
        while self._mic_kept - len(self._mic[0]) >= LIVE_RATE * WINDOW_S:
            self._mic_kept -= len(self._mic.pop(0))

    def played(self, voice: np.ndarray, band: np.ndarray) -> None:
        """The next stretch of the song, as sent to Skelly (voice) and to the other speakers (band)."""
        self._song["voice"].append(_decimate(voice, self.factor))
        self._song["band"].append(_decimate(band, self.factor))
        # Forget what's too old to be heard any more
        keep = int(LIVE_RATE * (WINDOW_S + 2)) + self.max_lag
        parts = self._song["voice"]
        while len(parts) > 1 and sum(len(p) for p in parts) - len(parts[0]) > keep:
            self._song_base += len(parts[0])
            self._song["voice"].pop(0)
            self._song["band"].pop(0)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await _stop(self._proc)
        self._proc = None

    def snapshot(self) -> dict | None:
        """What measure() works on, taken in one go (the player keeps adding while it works)."""
        if self.t0 is None or self._mic_end is None or self._mic_kept < LIVE_RATE * WINDOW_S:
            return None
        return {"mic": list(self._mic), "mic_end": self._mic_end, "t0": self.t0, "base": self._song_base,
                "voice": list(self._song["voice"]), "band": list(self._song["band"]), "max_lag": self.max_lag}

    def measure(self, voice_everywhere: bool = False) -> dict | None:
        snap = self.snapshot()
        return measure_live(snap, voice_everywhere) if snap else None


def measure_live(snap: dict, voice_everywhere: bool = False) -> dict:
    """Seconds after its song time that Skelly's voice and the band are heard (see LiveSync).
    Each is None when the mic can't pick it out clearly, the voice when nobody's singing, say.

    `voice_everywhere`: the other speakers play the voice too, so the voice is heard twice;
    Skelly's is the one that isn't where the band is.
    """
    max_lag = snap["max_lag"]
    mic = np.concatenate(snap["mic"])[-int(LIVE_RATE * WINDOW_S):]
    start = snap["mic_end"] - len(mic) / LIVE_RATE
    # The song sample that would be heard at the mic's first sample with the longest delay
    first = int(round((start - snap["t0"]) * LIVE_RATE)) - max_lag - snap["base"]
    out: dict = {}
    for name in ("band", "voice"):
        ref = _span(snap[name], first, len(mic) + max_lag)
        if float(np.sqrt((ref ** 2).mean())) < 1e-3:  # that part is silent here
            out[name] = None
            continue
        lag, score, curve = find_lag(mic, ref, max_lag)
        if name == "voice" and voice_everywhere and out.get("band") is not None:
            b = int(out["band"] * LIVE_RATE)
            curve = curve.copy()
            curve[max(0, b - LIVE_RATE // 25): b + LIVE_RATE // 25] = 0
            lag = int(np.argmax(curve))
            score = float(curve[lag] - np.median(curve)) / (float(np.std(curve)) or 1e-9)
        out[name] = lag / LIVE_RATE if score >= LIVE_MIN_SCORE else None
        out[f"{name}_score"] = round(score, 1)
    return out


def _span(parts: list[np.ndarray], first: int, length: int) -> np.ndarray:
    """Kept song samples first..first+length (zeros where there are none)."""
    whole = np.concatenate(parts) if parts else np.zeros(0, np.float32)
    out = np.zeros(length, np.float32)
    lo, hi = max(0, first), min(len(whole), first + length)
    if hi > lo:
        out[lo - first: hi - first] = whole[lo:hi]
    return out


class Aligner:
    """Turns live measurements into corrections for the other speakers, carefully.

    Two measurements in a row have to agree before anything changes, and small differences
    (under the time it takes sound to cross a room) are left alone, so a noisy room or a quiet
    moment in the song can't make the band wander.
    """

    TOLERANCE_S = 0.03
    AGREE_S = 0.03

    def __init__(self) -> None:
        self._last: float | None = None
        self.locked = False  # the band and Skelly are heard together
        self.gap_s: float | None = None  # the latest agreed band-minus-voice difference

    def step(self, m: dict | None) -> float:
        """Seconds to hold the other speakers back by (negative: bring them forward), usually 0."""
        if not m or m.get("voice") is None or m.get("band") is None:
            return 0.0
        gap = m["band"] - m["voice"]  # the band is heard this long after Skelly's voice
        if self._last is None or abs(gap - self._last) > self.AGREE_S:
            self._last = gap
            return 0.0
        gap, self._last = (gap + self._last) / 2, None
        self.gap_s = gap
        self.locked = abs(gap) <= self.TOLERANCE_S
        return 0.0 if self.locked else -gap
