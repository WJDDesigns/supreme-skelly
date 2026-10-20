import os
import time

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
