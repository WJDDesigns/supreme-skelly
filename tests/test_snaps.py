import time

from skelly.snaps import KEEP_S, Snaps


def test_same_frame_shares_one_file(tmp_path):
    s = Snaps(tmp_path)
    a = s.save(b"\xff\xd8one")
    assert s.save(b"\xff\xd8one") == a
    b = s.save(b"\xff\xd8two")
    assert b != a and len(list(tmp_path.glob("*.jpg"))) == 2
    assert s.save(None) is None


def test_path_rejects_bad_names(tmp_path):
    s = Snaps(tmp_path)
    name = s.save(b"x")
    assert s.path(name) == tmp_path / name
    assert s.path("../auth.json") is None
    assert s.path("0000000000000-deadbeef.jpg") is None


def test_prune_drops_old_pictures(tmp_path):
    s = Snaps(tmp_path)
    old = tmp_path / f"{int((time.time() - KEEP_S - 60) * 1000)}-00000000.jpg"
    old.write_bytes(b"x")
    keep = s.save(b"new")
    s.prune()
    assert not old.exists() and (tmp_path / keep).exists()
