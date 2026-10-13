"""Find the beat in a song and turn it into a dance and a light show.

Analysis happens once, when a song is added (after the voice and the band are split):
the band gives the beat and how hard the song is going at each moment, the voice says
when Skelly is singing. Choreography is worked out when the song starts, from that
analysis and whatever animatronic is connected, so it always uses the parts he has.

Plain numpy, no extra libraries: an onset envelope (spectral flux), tempo from its
autocorrelation, and beat positions by dynamic programming (Ellis, 2007).
"""

from __future__ import annotations

import random

import numpy as np

SR = 22050  # analysis rate (the song is halved from 44.1 kHz)
HOP = 512
NFFT = 2048
FPS = SR / HOP  # onset frames per second (~43)
BANDS = 40
LAG = NFFT // 2 + HOP  # samples from a frame's start to where its onsets land (measured on clicks)
MIN_BPM, MAX_BPM = 60.0, 200.0

PALETTES = {
    "halloween": [(255, 70, 0), (150, 0, 255), (40, 255, 40), (255, 140, 0)],
    "blood": [(255, 0, 0), (255, 60, 0), (180, 0, 30), (255, 120, 60)],
    "ghost": [(0, 200, 255), (200, 255, 255), (40, 80, 255), (120, 0, 255)],
    "party": [(255, 0, 180), (0, 255, 220), (255, 230, 0), (60, 255, 60), (90, 60, 255)],
}


def _mono22(stereo: np.ndarray) -> np.ndarray:
    """[2, n] at 44.1 kHz -> mono at 22.05 kHz (pairwise average; plenty for beats)."""
    mono = stereo.mean(axis=0)
    mono = mono[: len(mono) // 2 * 2]
    return mono.reshape(-1, 2).mean(axis=1)


def _frames_rms(x: np.ndarray) -> np.ndarray:
    n = len(x) // HOP
    return np.sqrt((x[: n * HOP].reshape(n, HOP) ** 2).mean(axis=1) + 1e-12)


def onset_envelope(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Spectral flux per frame, and the same for the low end only (kick drum, for bar starts)."""
    if len(x) < NFFT:
        x = np.pad(x, (0, NFFT - len(x)))
    n = 1 + (len(x) - NFFT) // HOP
    win = np.hanning(NFFT).astype(np.float32)
    flux = np.zeros(n, np.float32)
    low = np.zeros(n, np.float32)
    # ~40 bands evenly spaced in pitch (like the ear), so cymbals' many high bins don't drown out the kick.
    freqs = np.fft.rfftfreq(NFFT, 1 / SR)
    edges = np.geomspace(30, SR / 2, BANDS + 1)
    band_of = np.clip(np.searchsorted(edges, freqs) - 1, 0, BANDS - 1)
    counts = np.maximum(np.bincount(band_of, minlength=BANDS), 1)
    low_bands = int(np.searchsorted(edges, 150))
    prev = None
    for start in range(0, n, 512):  # in blocks to keep memory small
        idx = (np.arange(start, min(n, start + 512))[:, None] * HOP + np.arange(NFFT)[None, :])
        spec = np.abs(np.fft.rfft(x[idx] * win, axis=1))
        bands = np.zeros((len(spec), BANDS), np.float32)
        np.add.at(bands.T, band_of, spec.T)
        mag = np.log1p(10 * bands / counts)
        if prev is None:
            prev = mag[:1]
        both = np.vstack([prev, mag])
        d = np.maximum(0, np.diff(both, axis=0))
        flux[start:start + len(mag)] = d.sum(axis=1)
        low[start:start + len(mag)] = d[:, :low_bands].sum(axis=1)
        prev = mag[-1:]
    # Take away the slowly moving average so loud passages don't swamp the peaks.
    k = int(FPS)
    smooth = np.convolve(flux, np.ones(k) / k, mode="same")
    env = np.maximum(0, flux - smooth)
    sd = env.std() or 1.0
    return env / sd, low


def tempo(env: np.ndarray) -> float:
    """Beats per minute: the strongest repeat in the onset envelope, nudged towards ~115 (dance tempo)."""
    env = env - env.mean()
    ac = np.correlate(env, env, mode="full")[len(env) - 1:]
    lags = np.arange(len(ac), dtype=float)
    lo, hi = int(FPS * 60 / MAX_BPM), int(FPS * 60 / MIN_BPM)
    if hi >= len(ac) or lo < 1:
        return 120.0
    bpm = 60 * FPS / lags[lo:hi]
    prior = np.exp(-0.5 * (np.log2(bpm / 115.0) / 0.6) ** 2)
    best = lo + int(np.argmax(ac[lo:hi] * prior))
    return float(60 * FPS / best)


def track_beats(env: np.ndarray, bpm: float, tightness: float = 100.0) -> np.ndarray:
    """Beat times in seconds, by dynamic programming over the onset envelope."""
    period = 60 * FPS / bpm
    n = len(env)
    score = env.astype(float).copy()
    back = np.full(n, -1)
    lo, hi = int(round(period / 2)), int(round(2 * period))
    offsets = np.arange(lo, hi + 1)
    cost = -tightness * np.log(offsets / period) ** 2
    for t in range(lo, n):
        prev = t - offsets
        ok = prev >= 0
        if not ok.any():
            continue
        cand = score[prev[ok]] + cost[ok]
        i = int(np.argmax(cand))
        if cand[i] > 0:
            score[t] += cand[i]
            back[t] = prev[ok][i]
    # Last beat: the best-scoring frame within the final beat period.
    tail = max(0, n - int(period) - 1)
    t = tail + int(np.argmax(score[tail:])) if n else 0
    beats = []
    while t >= 0:
        beats.append(t)
        t = back[t]
    # A frame's flux peaks when the hit is near the middle of its window, so time it there.
    return (np.array(beats[::-1], dtype=float) * HOP + LAG) / SR


def analyse(vocals: np.ndarray, band: np.ndarray) -> dict:
    """Everything the dance and lights need, from the split song ([2, n] float32 each at 44.1 kHz)."""
    band22, voice22 = _mono22(band), _mono22(vocals)
    duration = len(band22) / SR
    env, low = onset_envelope(band22)  # the band keeps the beat; singing rhythm would blur it
    bpm = tempo(env)
    beats = track_beats(env, bpm)
    if len(beats) < 4:  # no clear beat (spoken word, ambient): a steady pulse at the guessed tempo
        beats = np.arange(0, duration, 60 / bpm)
    band_rms, voice_rms = _frames_rms(band22), _frames_rms(voice22)
    edges = np.append(beats, duration)
    per = lambda arr, a, b: float(arr[int(a * FPS):max(int(a * FPS) + 1, int(b * FPS))].mean()) if len(arr) else 0.0  # noqa: E731
    energy = np.array([per(band_rms, a, b) for a, b in zip(edges[:-1], edges[1:], strict=True)])
    voice = np.array([per(voice_rms, a, b) for a, b in zip(edges[:-1], edges[1:], strict=True)])
    ref = np.percentile(energy, 95) or 1.0
    energy = np.clip(energy / ref, 0, 1)
    vref = np.percentile(voice, 95)
    # A song with no real singing (an instrumental) keeps every beat "not singing".
    singing = (voice > max(0.12 * vref, 0.004)) if vref > 0.01 else np.zeros(len(voice), bool)
    # Bar starts: the beat phase (of four) where the kick drum lands hardest.
    frames = np.clip(((beats * SR - LAG) / HOP).round().astype(int), 0, len(low) - 1)
    lows = [low[frames[p::4]].sum() for p in range(4)]
    return {
        "bpm": round(bpm, 1), "duration": round(duration, 3),
        "beats": [round(float(b), 3) for b in beats],
        "downbeat": int(np.argmax(lows)),
        "energy": [round(float(e), 3) for e in energy],
        "singing": [bool(s) for s in singing],
    }


def _levels(energy: list[float]) -> list[int]:
    """How hard to dance on each beat, 0 (still) to 3 (everything), from energy smoothed over two bars."""
    e = np.array(energy, float)
    if not len(e):
        return []
    smooth = np.convolve(e, np.ones(8) / 8, mode="same")
    return [0 if s < 0.3 else 1 if s < 0.55 else 2 if s < 0.78 else 3 for s in smooth]


def choreograph(analysis: dict, profile: dict, *, dance: bool = True, lights: bool = True,
                palette: str | None = None, seed: str = "") -> list[dict]:
    """Timed commands for one song: ``{"t": seconds, "kind": "move"|"color"|"bright"|"eye"|"mode", ...}``.

    Only changes are listed, so Skelly isn't sent the same command twice in a row.
    """
    rng = random.Random(seed)
    beats = analysis.get("beats", [])
    energy = analysis.get("energy", [])
    singing = analysis.get("singing", [])
    down = analysis.get("downbeat", 0)
    levels = _levels(energy)
    events: list[dict] = []

    parts = [m["key"] for m in profile.get("movements", []) if m["key"] != "all"]
    lead = "head" if "head" in parts else (parts[0] if parts else None)
    others = [p for p in parts if p != lead]
    if dance and lead:
        last: tuple | None = None
        for i, t in enumerate(beats):
            lv, sing = levels[i], singing[i] if i < len(singing) else False
            pos = (i - down) % 4
            half = (i - down) // 2
            if lv == 0:
                want = {lead} if sing else set()
            elif lv == 1:
                want = {lead} | ({others[half % len(others)]} if others and pos >= 2 and not sing else set())
            elif lv == 2:
                combos = [{lead}, {lead, *others[:1]}, {lead, *others[1:2]}, set(others[:2]) or {lead}]
                want = combos[half % len(combos)]
            else:
                want = {lead, *others}
                if others and pos % 2:  # pulse on the off-beats: drop one part for a beat
                    want = want - {others[(i // 2) % len(others)]}
            if sing:
                want = want | {lead}
            key = tuple(sorted(want))
            if key != last:
                events.append({"t": t, "kind": "move", "parts": list(key)})
                last = key
        if last:
            events.append({"t": analysis.get("duration", beats[-1] if beats else 0), "kind": "move", "parts": []})

    light_keys = [li["key"] for li in profile.get("lights", [])]
    if lights and light_keys:
        colours = PALETTES.get(palette or "") or PALETTES[rng.choice(sorted(PALETTES))]
        static = next((m["value"] for m in profile.get("light_modes", []) if m["label"] == "Static"), None)
        if static is not None:
            events.append({"t": 0.0, "kind": "mode", "light": "all", "value": static})
        flames = next((e["value"] for e in profile.get("eyes", []) if e["label"] == "Flames"), None)
        ci, last_bright, hot = 0, None, False
        for i, t in enumerate(beats):
            lv, pos, e = levels[i], (i - down) % 4, energy[i] if i < len(energy) else 0.0
            change = pos == 0 if lv <= 1 else (pos % 2 == 0 if lv == 2 else True)
            if change:
                ci = (ci + 1) % len(colours)
                if lv == 3 and len(light_keys) > 1:  # split the colours across the lights at full tilt
                    for j, key in enumerate(light_keys):
                        events.append({"t": t, "kind": "color", "light": key,
                                       "rgb": list(colours[(ci + j) % len(colours)])})
                else:
                    events.append({"t": t, "kind": "color", "light": "all", "rgb": list(colours[ci])})
            bright = int(40 + 215 * e) if (e > 0.05 or (i < len(singing) and singing[i])) else 30
            if last_bright is None or abs(bright - last_bright) >= 30:
                events.append({"t": t, "kind": "bright", "light": "all", "value": bright})
                last_bright = bright
            if flames is not None and (lv == 3) != hot:
                hot = lv == 3
                events.append({"t": t, "kind": "eye", "value": flames if hot else None})
    events.sort(key=lambda ev: ev["t"])
    return events
