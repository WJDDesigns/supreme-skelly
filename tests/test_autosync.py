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
