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
