import asyncio
import json
import shutil

import numpy as np
import pytest

from skelly import beats, music, sources
from skelly import protocol as p
from skelly.link import SimulatedLink
from skelly.profiles import PROFILES
from skelly.service import SkellyService

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def _clicks(bpm: float, seconds: float = 20.0, rate: int = 44100) -> np.ndarray:
    n = int(rate * seconds)
    x = np.zeros((2, n), np.float32)
    per = 60 / bpm
    rng = np.random.default_rng(1)
    for k in range(int(seconds / per)):
        s = int(k * per * rate)
        size = min(2000, n - s)
        x[:, s:s + size] += np.exp(-np.arange(size) / 300) * rng.normal(size=size) * (1 if k % 4 == 0 else .5)
    return x


@pytest.mark.parametrize("bpm", [95, 128, 150])
def test_beats_land_on_the_clicks(bpm):
    a = beats.analyse(np.zeros((2, 20 * 44100), np.float32), _clicks(bpm))
    assert abs(a["bpm"] - bpm) < 3
    per = 60 / bpm
    bt = np.array(a["beats"])
    err = (bt / per - np.round(bt / per)) * per
    assert abs(err.mean()) < 0.02  # within 20 ms of each click
    assert not any(a["singing"])  # no voice at all


def test_choreography_uses_only_parts_the_prop_has():
    a = beats.analyse(np.zeros((2, 20 * 44100), np.float32), _clicks(120))
    a["singing"] = [i % 8 < 4 for i in range(len(a["beats"]))]
    for prof in PROFILES:
        d = prof.to_dict()
        parts = {m["key"] for m in d["movements"]} - {"all"}
        ev = beats.choreograph(a, d, seed="x")
        for e in ev:
            if e["kind"] == "move":
                assert set(e["parts"]) <= parts
        moves = [e for e in ev if e["kind"] == "move"]
        if parts:
            assert moves and moves[-1]["parts"] == []  # stands still at the end
        assert [e["t"] for e in ev] == sorted(e["t"] for e in ev)


def test_choreography_switches_off():
    a = beats.analyse(np.zeros((2, 10 * 44100), np.float32), _clicks(120, 10))
    assert beats.choreograph(a, PROFILES[0].to_dict(), dance=False, lights=False) == []
    only_lights = beats.choreograph(a, PROFILES[0].to_dict(), dance=False)
    assert only_lights and all(e["kind"] != "move" for e in only_lights)


def test_gate_closes_between_phrases():
    rate = 44100
    t = np.arange(rate * 2) / rate
    voice = np.sin(2 * np.pi * 220 * t).astype(np.float32) * 0.5
    voice[rate // 2:rate] = np.random.default_rng(0).normal(0, 0.005, rate // 2)  # faint bleed
    out = music._gate(voice)
    assert np.abs(out[int(rate * 0.75):int(rate * 0.95)]).max() < 0.002
    assert np.abs(out[: rate // 4]).max() > 0.4


def test_link_kinds():
    assert sources.kind("https://open.spotify.com/playlist/37i9dQZF1DX") == "spotify"
    assert sources.kind("https://open.spotify.com/intl-de/track/4uLU6hMCjMI75M1A2tKUQC?si=1") == "spotify"
    assert sources.kind("https://music.apple.com/us/album/thriller/269572838?i=269573364") == "apple"
    assert sources.kind("https://youtu.be/dQw4w9WgXcQ") == "youtube"
    assert sources.kind("https://music.youtube.com/watch?v=abc") == "youtube"
    assert sources.kind("https://soundcloud.com/x/y") == "link"
    assert sources.kind("Monster Mash") == "search"
    assert sources.Track("Monster Mash", "Bobby Pickett").query.startswith("ytsearch1:Bobby Pickett - Monster Mash")


def test_watch_link_with_a_mix_is_one_song():
    assert sources.single_video("https://www.youtube.com/watch?v=NdFEl7bg8MM&list=RDNdFEl7bg8MM")
    assert sources.single_video("https://youtu.be/dQw4w9WgXcQ?list=PL123")
    assert not sources.single_video("https://www.youtube.com/playlist?list=PL123")
    assert not sources.single_video("https://music.youtube.com/playlist?list=OLAK5uy_x")


def test_clear_unready(tmp_path):
    lib = music.Library(tmp_path / "music")
    a = lib.add(sources.Track("Ready"))
    lib.update(a["id"], status="ready")
    lib.add(sources.Track("Queued"))
    lib.update(lib.add(sources.Track("Broken"))["id"], status="failed")
    assert lib.remove_unready() == 2
    assert [s["title"] for s in lib.data["songs"]] == ["Ready"]


async def test_removing_the_song_being_prepared_frees_the_queue(tmp_path, monkeypatch):
    """A cleared song stops at once; the next one doesn't wait minutes behind it."""
    lib = music.Library(tmp_path / "music")
    prep = music.Preparer(lib, lambda: None, models=tmp_path / "models")
    started: list[str] = []

    async def slow_prepare(song):
        started.append(song["title"])
        if song["title"] == "Gone":
            await asyncio.sleep(3600)
        lib.update(song["id"], status="ready")

    monkeypatch.setattr(prep, "prepare", slow_prepare)
    gone = lib.add(sources.Track("Gone"))
    lib.add(sources.Track("Next"))
    prep.start()
    for _ in range(50):
        if started:
            break
        await asyncio.sleep(0.01)
    prep.cancel([gone["id"]])
    lib.remove(gone["id"])
    for _ in range(50):
        if lib.data["songs"][0]["status"] == "ready":
            break
        await asyncio.sleep(0.01)
    assert started == ["Gone", "Next"] and lib.data["songs"][0]["status"] == "ready"
    await prep.stop()


def test_spotify_embed_playlist():
    data = {"props": {"pageProps": {"state": {"data": {"entity": {"type": "playlist", "trackList": [
        {"title": "Thriller", "subtitle": "Michael Jackson"},
        {"title": "Ghostbusters", "subtitle": "Ray Parker Jr."}]}}}}}}
    page = f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'
    tracks = sources.parse_spotify_embed(page, "playlist")
    assert [(t.title, t.artist) for t in tracks] == [("Thriller", "Michael Jackson"),
                                                       ("Ghostbusters", "Ray Parker Jr.")]


def test_spotify_embed_track():
    data = {"props": {"pageProps": {"state": {"data": {"entity": {
        "type": "track", "name": "Monster Mash", "artists": [{"name": "Bobby Pickett"}]}}}}}}
    page = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'
    [t] = sources.parse_spotify_embed(page, "track")
    assert (t.title, t.artist, t.source) == ("Monster Mash", "Bobby Pickett", "spotify")


def test_apple_playlist_page():
    ld = {"@type": "MusicPlaylist", "track": [{"name": "Thriller", "byArtist": [{"name": "Michael Jackson"}]}]}
    page = f'<script type="application/ld+json">{json.dumps(ld)}</script>'
    [t] = sources.parse_apple_page(page)
    assert (t.title, t.artist) == ("Thriller", "Michael Jackson")


def test_library_and_playlists(tmp_path):
    lib = music.Library(tmp_path / "music")
    a = lib.add(sources.Track("One", "A"))
    b = lib.add(sources.Track("Two", "B"))
    pl = lib.save_playlist(None, " Halloween ", [a["id"], b["id"], "nope"])
    assert pl["name"] == "Halloween" and pl["songs"] == [a["id"], b["id"]]
    lib.remove(a["id"])
    again = music.Library(tmp_path / "music")
    assert [s["title"] for s in again.data["songs"]] == ["Two"]
    assert again.playlist(pl["id"])["songs"] == [b["id"]]


def test_config_is_clamped():
    cfg = music.config({"skelly_band": 900, "sync_ms": -9999, "palette": "nope", "junk": 1})
    assert cfg["skelly_band"] == 150 and cfg["sync_ms"] == -1500 and cfg["palette"] == "" and "junk" not in cfg


@needs_ffmpeg
async def test_prepare_and_sing(tmp_path, monkeypatch):
    """An uploaded song is split, analysed and sung: moves and lights go to Skelly in time."""
    lib = music.Library(tmp_path / "music")
    prep = music.Preparer(lib, lambda: None, models=tmp_path / "models")

    async def fake_split(sid, mix, folder):  # the real model is too slow for a unit test
        raw = np.fromfile(mix, np.float32).reshape(-1, 2)
        (raw * 0.3).astype(np.float32).tofile(folder / "vocals.f32")
        (raw * 0.7).astype(np.float32).tofile(folder / "band.f32")

    monkeypatch.setattr(prep, "_split", fake_split)
    src = tmp_path / "song.f32"
    _clicks(120, 8).T.astype(np.float32).tofile(src)
    wav = tmp_path / "song.wav"
    await music._run("ffmpeg", "-v", "error", "-f", "f32le", "-ar", "44100", "-ac", "2", "-i", str(src), str(wav))
    song = lib.add(sources.Track("Clicks", source="file", url="file"))
    prep._uploads[song["id"]] = wav
    await prep.prepare(lib.song(song["id"]))
    s = lib.song(song["id"])
    assert s["status"] == "ready" and abs(s["bpm"] - 120) < 3
    assert (lib.folder(s["id"]) / "voice.ogg").exists() and (lib.folder(s["id"]) / "band.ogg").exists()
    assert not (lib.folder(s["id"]) / "mix.f32").exists()

    link = SimulatedLink()
    svc = SkellyService(link, auto_reconnect=False)
    await svc.start()
    await svc.connect("SIM", "Ultra Skelly v2")
    await asyncio.sleep(0.6)
    monkeypatch.setattr(music, "AUDIO_DELAY_S", 0.0)
    live_cfg = {"gap_s": 0}
    player = music.Player(svc, lib, None, lambda: music.config(live_cfg), simulate=True)
    link.sent.clear()
    await player.play([s["id"]])
    await asyncio.sleep(3)
    moves = [d for d in link.sent if d[1] == p.Cmd.SET_MOVEMENT]
    colours = [d for d in link.sent if d[1] == p.Cmd.SET_RGB]
    assert moves and colours
    assert player.state.playing and 1.5 < player.state.position < 3.2
    # Moving the timing slider mid-song shifts the show at once (here, a full second later).
    before = player.state.position
    live_cfg["sync_ms"] = 1000
    await asyncio.sleep(1.2)
    assert player.state.position < before + 0.7
    assert lib.song(s["id"])["gain"] > 1  # measured once, remembered, and turned up (the clicks are quiet)
    await player.stop()
    assert not player.state.playing
    await svc.stop()


def test_quiet_songs_are_turned_up_without_clipping():
    """Gaps between phrases don't count towards loudness, and the limiter never passes full scale."""
    t = np.arange(8000 * 4) / 8000
    voice = 0.03 * np.sin(2 * np.pi * 220 * t) * (t % 1 < 0.5)  # half sung, half silent
    assert abs(music._active_rms(voice, 8000) - 0.03 / np.sqrt(2)) < 0.002
    loud = np.linspace(-3, 3, 1001)
    out = music._limit(loud)
    assert np.abs(out).max() <= 1.0 and np.allclose(out[np.abs(loud) <= 0.8], loud[np.abs(loud) <= 0.8])
    assert np.all(np.diff(out) >= 0)


async def test_songs_wait_while_skelly_sings(tmp_path, monkeypatch):
    """Getting songs ready pauses while one plays, so playback has the whole mini PC and doesn't skip."""
    lib = music.Library(tmp_path / "music")
    prep = music.Preparer(lib, lambda: None, models=tmp_path / "models")
    started: list[str] = []

    async def quick_prepare(song):
        started.append(song["title"])
        lib.update(song["id"], status="ready")

    monkeypatch.setattr(prep, "prepare", quick_prepare)
    prep.hold()
    lib.add(sources.Track("Later"))
    prep.start()
    await asyncio.sleep(0.1)
    assert started == []
    prep.release()
    for _ in range(50):
        if started:
            break
        await asyncio.sleep(0.01)
    assert started == ["Later"]
    await prep.stop()


def test_old_settings_start_from_the_real_song():
    old = {"voice_level": 140, "skelly_band": 30, "speakers_level": 60, "sync_ms": 120}
    new = music.upgrade_config(old)
    assert (new["voice_level"], new["skelly_band"], new["speakers_level"]) == (100, 100, 100)
    assert new["sync_ms"] == 120  # timing the user tuned is kept
    assert music.upgrade_config(music.config(new)) == music.config(new)  # only once


def test_voice_can_have_a_side_of_its_own():
    v, m = np.array([1.0, 2.0]), np.array([10.0, 20.0])
    assert music._skelly_mix(v, m, "mix").tolist() == [11, 11, 22, 22]
    assert music._skelly_mix(v, m, "left").tolist() == [1, 10, 2, 20]
    assert music._skelly_mix(v, m, "right").tolist() == [10, 1, 20, 2]
    assert music.config({"voice_side": "up"})["voice_side"] == "mix"
