import numpy as np

from skelly import autosync


def test_finds_the_chirps_in_a_noisy_recording():
    sig = autosync.chirps()
    rng = np.random.default_rng(1)
    rec = rng.normal(0, 0.05, autosync.RATE * 4).astype(np.float32)
    at = int(autosync.RATE * 1.234)
    rec[at:at + len(sig)] += 0.2 * sig  # quiet and muffled compared with the noise
    delay, score = autosync.find_delay(rec, sig)
    assert abs(delay - 1.234) < 0.002 and score > autosync.MIN_SCORE


def test_silence_is_not_a_match():
    rec = np.random.default_rng(2).normal(0, 0.05, autosync.RATE * 4).astype(np.float32)
    assert autosync.find_delay(rec, autosync.chirps())[1] < autosync.MIN_SCORE


def test_slider_values_from_the_delays():
    # The other speakers lag 900 ms behind Skelly's: hold Skelly back by that much
    assert autosync.settings(0.62, 1.52, 0.5) == {"sync_ms": 120, "speakers_offset_ms": -900}
    assert autosync.settings(0.45, None, 0.5) == {"sync_ms": -50}


def _song(seconds: float, seed: int):
    """A stand-in song at 48 kHz: sung phrases with gaps for the voice, a steady band."""
    rng = np.random.default_rng(seed)
    n = int(autosync.RATE * seconds)
    t = np.arange(n) / autosync.RATE
    phrases = (np.sin(2 * np.pi * 0.4 * t) > -0.3).astype(np.float32)  # sung, then a short gap
    voice = np.convolve(rng.normal(0, 0.3, n), np.ones(8) / 8, "same").astype(np.float32) * phrases
    band = np.convolve(rng.normal(0, 0.3, n), np.ones(3) / 3, "same").astype(np.float32)
    return voice, band


def _room(voice, band, voice_late: float, band_late: float, seed: int = 3):
    """What Skelly's mic hears: his speaker close by, the other speaker further off, an echo, and noise."""
    r = autosync.RATE
    out = np.random.default_rng(seed).normal(0, 0.02, len(voice)).astype(np.float32)
    for sig, late, gain in ((voice, voice_late, 0.5), (band, band_late, 0.2)):
        for extra, g in ((0.0, gain), (0.011, gain * 0.4)):  # and a bounce off the wall
            k = int((late + extra) * r)
            out[k:] += g * sig[: len(sig) - k]
    return out


def test_live_sync_hears_how_late_each_speaker_is():
    voice, band = _song(10, 4)
    mic = _room(voice, band, voice_late=0.62, band_late=1.37)
    live = autosync.LiveSync(None)
    live.t0 = 100.0  # the song's clock
    step = autosync.RATE // 20
    f = autosync.RATE // autosync.LIVE_RATE
    for i in range(0, len(voice), step):  # the player hands over each 50 ms as it goes
        live.played(voice[i:i + step], band[i:i + step])
        heard = mic[i:i + step]
        live.hear(heard.reshape(-1, f).mean(axis=1), live.t0 + (i + len(heard)) / autosync.RATE)
    m = live.measure()
    assert abs(m["voice"] - 0.62) < 0.004 and abs(m["band"] - 1.37) < 0.004


def test_live_sync_is_quiet_when_it_cant_hear_the_band():
    voice, band = _song(8, 5)
    mic = _room(voice, band * 0, voice_late=0.5, band_late=0.5)  # the other speaker is off
    live = autosync.LiveSync(None)
    live.t0 = 0.0
    f = autosync.RATE // autosync.LIVE_RATE
    live.played(voice, band)
    live.hear(mic.reshape(-1, f).mean(axis=1), len(mic) / autosync.RATE)
    m = live.measure()
    assert abs(m["voice"] - 0.5) < 0.004 and m["band"] is None


def test_aligner_waits_for_two_agreeing_measurements():
    a = autosync.Aligner()
    assert a.step({"voice": 0.6, "band": 1.0}) == 0  # one reading isn't enough
    assert a.step({"voice": 0.6, "band": 1.4}) == 0  # and these two disagree
    assert abs(a.step({"voice": 0.6, "band": 1.41}) + 0.805) < 1e-6  # band 0.8 s late: bring it forward
    assert not a.locked
    assert a.step({"voice": 0.6, "band": None}) == 0  # can't hear the band: change nothing
    a.step({"voice": 0.6, "band": 0.61})
    assert a.step({"voice": 0.6, "band": 0.62}) == 0 and a.locked  # close enough: leave it
