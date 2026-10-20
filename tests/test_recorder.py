import os
import shutil
import subprocess
import time

import numpy as np
import pytest

from skelly import recorder


def test_names_are_checked_and_old_ones_pruned(tmp_path, monkeypatch):
    monkeypatch.setenv("SKELLY_DATA_DIR", str(tmp_path))
    d = recorder.rec_dir()
    old, new = d / "2026-09-01_10-00-00.mp4", d / "2026-10-07_10-00-00.mp4"
    for p in (old, new):
        p.write_bytes(b"x" * 20000)
    os.utime(old, (time.time() - 40 * 86400,) * 2)
    assert recorder.recording_path("../settings.json") is None
    assert recorder.recording_path(new.name) == new
    assert recorder.prune(30) == 1
    assert [r["name"] for r in recorder.recordings()] == [new.name]
    assert recorder.delete(new.name) and recorder.recordings() == []


def test_recordings_say_how_much_the_visitor_talked(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("SKELLY_DATA_DIR", str(tmp_path))
    d = recorder.rec_dir()
    chats = {
        "2026-10-20_18-00-00": [("skelly", "Boo! Who goes there?")],
        "2026-10-20_18-05-00": [("skelly", "Hello!"), ("user", "thank you"), ("user", "hi skelly")],
        "2026-10-20_18-10-00": [("skelly", "Hello!"), ("user", "Hi!"), ("skelly", "Nice hat"),
                                ("user", "It's a witch hat"), ("user", "um")],
    }
    for name, lines in chats.items():
        (d / f"{name}.mp4").write_bytes(b"x" * 20000)
        (d / f"{name}.json").write_text(json.dumps({"started": 0, "seconds": 9, "transcript": [
            {"role": r, "text": t} for r, t in lines]}))
    got = {r["name"][:-4]: (r["kind"], r["replies"], r["preview"]) for r in recorder.recordings()}
    assert got == {
        "2026-10-20_18-00-00": ("ignored", 0, ""),
        "2026-10-20_18-05-00": ("brief", 1, "hi skelly"),
        "2026-10-20_18-10-00": ("chat", 2, "Hi!"),
    }


def test_clean_sound_turns_the_mic_down_for_his_voice_and_its_echo():
    f = recorder.audio_filter("clean", 0.4)
    assert "sidechaincompress" in f and "adelay=650:all=1" in f  # measured lag plus the margin
    assert f.endswith("[a]") and "[1:a]" in f and "[2:a]" in f
    assert "adelay=2000:" in recorder.audio_filter("clean", 9)  # a wild measurement is capped
    assert recorder.audio_filter("anything else", 0.4) == f  # unknown settings fall back to clean


def test_room_sound_keeps_only_the_mic():
    f = recorder.audio_filter("room", 0.4)
    assert "[1:a]" in f and "[2:a]" not in f and "sidechaincompress" not in f


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_clean_sound_hears_him_once(tmp_path):
    """His voice on input 2; the mic hears it 0.4 s later, then a visitor speaks alone."""
    rate = 16000

    def tone(hz, start, end, amp):
        t = np.arange(rate * 4) / rate
        return amp * np.sin(2 * np.pi * hz * t) * ((t >= start) & (t < end))

    voice = tone(1000, 0.0, 1.0, 0.3)
    mic = tone(1000, 0.4, 1.4, 0.15) + tone(300, 2.5, 3.5, 0.2)
    for name, x in (("voice", voice), ("mic", mic)):
        (tmp_path / f"{name}.raw").write_bytes(x.astype(np.float32).tobytes())
    raw = ["-f", "f32le", "-ar", str(rate), "-ac", "1", "-i"]
    out = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=d=4",
                          *raw, str(tmp_path / "mic.raw"), *raw, str(tmp_path / "voice.raw"),
                          "-filter_complex", recorder.audio_filter("clean", 0.4), "-map", "[a]",
                          "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"], capture_output=True, check=True).stdout
    y = np.frombuffer(out, np.float32)

    def level(a, b):
        return float(np.sqrt(np.mean(y[int(a * rate):int(b * rate)] ** 2)))

    assert level(1.1, 1.35) < 0.005  # the echo after he stopped is gone
    assert level(0.3, 0.9) > 0.1  # his voice is there
    assert level(2.7, 3.3) > 0.05  # and so is the visitor


def test_thumbnail_made_once_and_deleted_with_the_video(tmp_path, monkeypatch):
    import shutil
    import subprocess

    import pytest

    if not shutil.which("ffmpeg"):
        pytest.skip("needs ffmpeg")
    monkeypatch.setenv("SKELLY_DATA_DIR", str(tmp_path))
    d = recorder.rec_dir()
    short, longer = d / "2026-10-20_18-00-00.mp4", d / "2026-10-20_18-05-00.mp4"
    for p, secs in ((short, 1), (longer, 3)):  # recorded the way the recorder does: fragmented MP4
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size=640x360:rate=6:d={secs}",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+frag_keyframe+empty_moov",
                        str(p)], check=True)
    for p in (short, longer):
        thumb = recorder.thumbnail(p)
        assert thumb == p.with_suffix(".jpg") and thumb.read_bytes()[:2] == b"\xff\xd8"
    mtime = longer.with_suffix(".jpg").stat().st_mtime_ns
    assert recorder.thumbnail(longer).stat().st_mtime_ns == mtime  # not made again
    assert recorder.delete(longer.name) and not longer.with_suffix(".jpg").exists()
