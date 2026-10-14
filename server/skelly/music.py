"""Music mode: Skelly sings and dances to songs, with lights that follow the music.

Skelly's jaw is moved by his own firmware from whatever plays through his Live speaker, so
the trick to "singing" is what he's given to play. Each song is split once, when it's added,
into the singer's voice and the band (see separate.py). While it plays:

* Skelly's speaker gets the voice (plus as much of the band as the owner likes), so his
  mouth moves with the words and closes when the singer stops.
* Any other speakers chosen in Settings > Sound get the band (and optionally the voice),
  lined up with Skelly by a timing offset because each Bluetooth speaker lags differently.
* Dance moves and lights are worked out from the song's beat (beats.py) and sent over BLE
  timed to what's heard, not to what's been written, since Bluetooth audio runs ~0.3 s late.

Songs are kept on the mini PC as small Opus files: data/music/<id>/{voice,band}.ogg.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import shutil
import signal
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from . import beats, sources
from . import protocol as proto
from .settings import data_dir

log = logging.getLogger(__name__)

RATE = 48000
CHUNK_S = 0.05
# From handing audio to PipeWire to hearing it from Skelly: our 100 ms pacat buffer plus his
# Bluetooth (A2DP) delay. The owner fine-tunes the rest with the "Moves timing" slider.
AUDIO_DELAY_S = float(os.environ.get("SKELLY_MUSIC_DELAY_S", "0.5"))
BLE_LEAD_S = 0.04  # a BLE command takes about this long to reach Skelly and act
LATE_S = 0.3  # a light or move this late is skipped rather than sent out of time
MAX_SONGS = 300

DEFAULTS = {
    "dance": True,  # move to the beat
    "lights": True,  # lights follow the music
    "palette": "",  # beats.PALETTES key, "" = a different one per song
    "skelly_band": 100,  # % of the band on Skelly's speaker when it's the only one (100 = the song as recorded)
    "speakers": True,  # also play on the extra speakers from Settings > Sound
    "speakers_voice": False,  # the extra speakers get the voice too, not just the band
    "voice_level": 100,  # % volume of the voice on Skelly
    "speakers_level": 100,  # % volume of the extra speakers
    "sync_ms": 0,  # moves and lights earlier (-) or later (+) than the estimated audio delay
    "speakers_offset_ms": 0,  # extra speakers later (+) or earlier (-) than Skelly
    "gap_s": 2.0,  # pause between songs
    # How the voice and music share Skelly's speaker: "mix" = both on both sides; "left"/"right" = the
    # voice alone on that side and the music on the other, for a jaw that listens to one side only.
    "voice_side": "mix",
}
VOICE_SIDES = ("mix", "left", "right")
LEVELS = ("voice_level", "skelly_band", "speakers_level")
MIX_VERSION = 2  # v2: levels start at the song's own voice/music balance


def upgrade_config(raw: dict) -> dict:
    """Saved settings from before v2 had the music turned down under the voice: start from the real song."""
    if raw and raw.get("mix_version") != MIX_VERSION:
        raw = {**raw, **{k: DEFAULTS[k] for k in LEVELS}, "mix_version": MIX_VERSION}
    return raw


def config(raw: dict | None) -> dict:
    cfg = {**DEFAULTS, **{k: v for k, v in (raw or {}).items() if k in DEFAULTS}, "mix_version": MIX_VERSION}
    for k in ("skelly_band", "voice_level", "speakers_level"):
        cfg[k] = int(max(0, min(150, float(cfg[k]))))
    for k in ("sync_ms", "speakers_offset_ms"):
        cfg[k] = int(max(-1500, min(1500, float(cfg[k]))))
    cfg["gap_s"] = max(0.0, min(30.0, float(cfg["gap_s"])))
    if cfg["voice_side"] not in VOICE_SIDES:
        cfg["voice_side"] = "mix"
    if cfg["palette"] not in beats.PALETTES:
        cfg["palette"] = ""
    return cfg


# -- the song library ---------------------------------------------------------------------

class Library:
    """Songs and playlists, kept in data/music/library.json."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or data_dir() / "music"
        self.index = self.root / "library.json"
        self.data = self._load()

    def _load(self) -> dict:
        try:
            data = json.loads(self.index.read_text())
        except (OSError, ValueError):
            data = {}
        data.setdefault("songs", [])
        data.setdefault("playlists", [])
        return data

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.index.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1))
        tmp.replace(self.index)

    def folder(self, song_id: str) -> Path:
        return self.root / Path(song_id).name

    def song(self, song_id: str) -> dict:
        for s in self.data["songs"]:
            if s["id"] == song_id:
                return s
        raise KeyError("No such song")

    def add(self, track: sources.Track) -> dict:
        if len(self.data["songs"]) >= MAX_SONGS:
            raise ValueError(f"The library holds up to {MAX_SONGS} songs. Remove some first.")
        song = {"id": uuid.uuid4().hex[:10], "title": track.title, "artist": track.artist, "source": track.source,
                "query": track.query, "status": "waiting", "progress": 0, "error": None, "added": time.time(),
                "duration": None, "bpm": None}
        self.data["songs"].append(song)
        self.save()
        return song

    def update(self, song_id: str, **fields) -> dict:
        s = self.song(song_id)
        s.update(fields)
        self.save()
        return s

    def remove(self, song_id: str) -> None:
        self.data["songs"] = [s for s in self.data["songs"] if s["id"] != song_id]
        for p in self.data["playlists"]:
            p["songs"] = [i for i in p["songs"] if i != song_id]
        self.save()
        shutil.rmtree(self.folder(song_id), ignore_errors=True)

    def remove_unready(self) -> int:
        """Drop every song that isn't ready (still queued, being prepared, or failed)."""
        gone = [s["id"] for s in self.data["songs"] if s["status"] != "ready"]
        for sid in gone:
            self.remove(sid)
        return len(gone)

    def playlist(self, playlist_id: str) -> dict:
        for p in self.data["playlists"]:
            if p["id"] == playlist_id:
                return p
        raise KeyError("No such playlist")

    def save_playlist(self, playlist_id: str | None, name: str, songs: list[str]) -> dict:
        name = name.strip()[:60] or "My playlist"
        known = {s["id"] for s in self.data["songs"]}
        songs = [i for i in songs if i in known]
        if playlist_id:
            p = self.playlist(playlist_id)
            p.update(name=name, songs=songs)
        else:
            p = {"id": uuid.uuid4().hex[:10], "name": name, "songs": songs}
            self.data["playlists"].append(p)
        self.save()
        return p

    def delete_playlist(self, playlist_id: str) -> None:
        self.data["playlists"] = [p for p in self.data["playlists"] if p["id"] != playlist_id]
        self.save()

    def public(self) -> dict:
        return {"songs": self.data["songs"], "playlists": self.data["playlists"]}


# -- getting songs ready --------------------------------------------------------------------

def _gate(voice: np.ndarray, rate: int = 44100) -> np.ndarray:
    """Silence the faint bleed left between sung phrases, so Skelly's jaw closes when the singer stops.

    A soft gate on 10 ms blocks: full voice above the threshold, fading quickly to nothing below it.
    """
    block = rate // 100
    n = len(voice) // block * block
    if not n:
        return voice
    env = np.sqrt((voice[:n].reshape(-1, block) ** 2).mean(axis=1))
    loud = np.percentile(env, 95)
    if loud <= 1e-5:
        return voice * 0
    gain = np.clip((env / loud - 0.06) / 0.06, 0, 1)  # 0 below 6 % of loud singing, full by 12 %
    for i in range(1, len(gain)):  # quick to open, ~80 ms to close
        gain[i] = max(gain[i], gain[i - 1] * 0.88)
    smooth = np.repeat(gain, block)
    out = voice.copy()
    out[:n] *= smooth
    out[n:] *= gain[-1]
    return out


def _level(mix: np.ndarray) -> float:
    """Gain that brings a song to an even loudness without clipping its peaks."""
    rms = float(np.sqrt((mix ** 2).mean()) or 1e-6)
    peak = float(np.abs(mix).max() or 1e-6)
    return min(0.12 / rms, 0.99 / peak, 4.0)


MIX_TARGET = 0.25  # RMS of the whole song (voice + band) when played: loud, like his conversation voice
MAX_GAIN = 10.0


def _active_rms(x: np.ndarray, rate: int) -> float:
    """Loudness of the parts that aren't silent (the gaps between sung phrases would drag it down)."""
    block = rate // 20
    n = len(x) // block * block
    if not n:
        return 0.0
    env = np.sqrt((x[:n].reshape(-1, block) ** 2).mean(axis=1))
    loud = env[env > 0.15 * np.percentile(env, 95)]
    return float(np.sqrt((loud ** 2).mean())) if len(loud) else 0.0


async def _gain(folder: Path) -> float:
    """How much to turn the song up when playing. One gain for the voice and the band alike, so
    they keep the balance they have in the real song; the sliders adjust from there."""
    parts = []
    for name in ("voice", "band"):
        raw = await _run("ffmpeg", "-v", "error", "-i", str(folder / f"{name}.ogg"), "-f", "f32le", "-ac", "1",
                         "-ar", "8000", "-")
        parts.append(np.frombuffer(raw[: len(raw) // 4 * 4], np.float32))
    n = min(len(p) for p in parts)
    rms = _active_rms(parts[0][:n] + parts[1][:n], 8000)
    return round(min(MAX_GAIN, MIX_TARGET / rms), 2) if rms > 1e-4 else 1.0


def _skelly_mix(voice: np.ndarray, music: np.ndarray, side: str) -> np.ndarray:
    """Interleaved stereo for Skelly's speaker: voice and music together, or each on its own side."""
    if side == "left":
        left, right = voice, music
    elif side == "right":
        left, right = music, voice
    else:
        left = right = voice + music
    return np.stack([left, right], axis=1).reshape(-1)


def _limit(x: np.ndarray) -> np.ndarray:
    """Soft limiter: untouched below 0.8, rounded off above it, never past full scale."""
    a = np.abs(x)
    over = a > 0.8
    if not over.any():
        return x
    y = x.copy()
    y[over] = np.sign(x[over]) * (0.8 + 0.2 * np.tanh((a[over] - 0.8) / 0.2))
    return y


async def _run(*args: str, stdin: bytes | None = None) -> bytes:
    proc = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.PIPE if stdin is not None else None,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await proc.communicate(stdin)
    except asyncio.CancelledError:
        proc.kill()
        raise
    if proc.returncode:
        raise RuntimeError(err.decode(errors="replace").strip().splitlines()[-1] if err else f"{args[0]} failed")
    return out


async def _encode(pcm: np.ndarray, channels: int, path: Path, kbps: int) -> None:
    """float32 samples at 44.1 kHz (interleaved) to an Opus file."""
    await _run("ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", "44100", "-ac", str(channels), "-i", "-",
               "-c:a", "libopus", "-b:a", f"{kbps}k", str(path), stdin=pcm.astype(np.float32).tobytes())


class Preparer:
    """Fetches, splits and analyses added songs one at a time, in the background."""

    def __init__(self, library: Library, publish: Callable[[], None], models: Path | None = None) -> None:
        self.lib = library
        self.publish = publish
        self.models = models or Path(os.environ.get("SKELLY_MODELS_DIR", "/app/models"))
        if not os.access(self.models.parent if not self.models.exists() else self.models, os.W_OK):
            self.models = data_dir() / "models"
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._uploads: dict[str, Path] = {}
        self._current: tuple[str, asyncio.Task] | None = None  # the song being prepared
        self._go = asyncio.Event()  # cleared while Skelly sings, so playback gets the whole mini PC
        self._go.set()
        self._split_proc: asyncio.subprocess.Process | None = None

    def start(self) -> None:
        # Anything left half-done by a restart starts again.
        for s in self.lib.data["songs"]:
            if s["status"] not in ("ready", "failed"):
                s.update(status="waiting", progress=0)
        self._task = asyncio.create_task(self._loop(), name="music-prepare")
        self._wake.set()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    def kick(self) -> None:
        self._wake.set()

    def hold(self) -> None:
        """Pause getting songs ready while one plays: the split hogs the CPU and made playback skip."""
        self._go.clear()
        self._signal(signal.SIGSTOP)

    def release(self) -> None:
        self._go.set()
        self._signal(signal.SIGCONT)

    def _signal(self, sig: int) -> None:
        proc = self._split_proc
        if proc and proc.returncode is None:
            try:
                proc.send_signal(sig)
            except ProcessLookupError:
                pass

    def cancel(self, song_ids) -> None:
        """Stop work on removed songs at once, so the next song doesn't wait behind one that's gone."""
        if self._current and self._current[0] in set(song_ids):
            self._current[1].cancel()

    def add_file(self, song: dict, path: Path) -> None:
        self._uploads[song["id"]] = path
        self.kick()

    def _set(self, song_id: str, **fields) -> None:
        try:
            self.lib.update(song_id, **fields)
        except KeyError:
            return  # removed while being prepared
        self.publish()

    async def _loop(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            while True:
                await self._go.wait()
                nxt = next((s for s in self.lib.data["songs"] if s["status"] == "waiting"), None)
                if not nxt:
                    break
                job = asyncio.create_task(self.prepare(nxt))
                self._current = (nxt["id"], job)
                try:
                    await asyncio.wait({job})
                except asyncio.CancelledError:
                    job.cancel()
                    raise
                finally:
                    self._current = None
                if job.cancelled():  # the song was removed: tidy up anything it left behind
                    shutil.rmtree(self.lib.folder(nxt["id"]), ignore_errors=True)
                elif exc := job.exception():
                    log.warning("song %s failed: %r", nxt.get("title"), exc)
                    self._set(nxt["id"], status="failed", error=_friendly(exc))

    async def prepare(self, song: dict) -> None:
        sid = song["id"]
        folder = self.lib.folder(sid)
        folder.mkdir(parents=True, exist_ok=True)
        if sid in self._uploads:
            src = self._uploads.pop(sid)
        elif (existing := sorted(folder.glob("source.*"))):
            src = existing[0]
        else:
            self._set(sid, status="fetching", progress=0, error=None)
            track = sources.Track(title=song["title"], artist=song.get("artist", ""), source=song.get("source", ""),
                                  url=song["query"] if not song["query"].startswith("ytsearch") else None)
            info = await sources.ytdlp("download", json.dumps(asdict(track)), str(folder))
            src = Path(info["path"])
            self._set(sid, title=info["title"] or song["title"], artist=info["artist"] or song.get("artist", ""),
                      url=info.get("url"))

        await self._go.wait()
        self._set(sid, status="splitting", progress=0)
        mix_raw = await _run("ffmpeg", "-v", "error", "-i", str(src), "-t", str(sources.MAX_SECONDS),
                             "-f", "f32le", "-ac", "2", "-ar", "44100", "-")
        mix = np.frombuffer(mix_raw, np.float32).reshape(-1, 2)
        if len(mix) < 44100 * 5:
            raise ValueError("That song is too short (under 5 seconds)")
        mix = mix * _level(mix)
        work = folder / "mix.f32"
        mix.astype(np.float32).tofile(work)
        await self._split(sid, work, folder)

        await self._go.wait()
        self._set(sid, status="analysing", progress=100)
        voice = np.fromfile(folder / "vocals.f32", np.float32).reshape(-1, 2)
        band = np.fromfile(folder / "band.f32", np.float32).reshape(-1, 2)
        analysis = await asyncio.to_thread(beats.analyse, voice.T, band.T)
        (folder / "analysis.json").write_text(json.dumps(analysis))
        mono_voice = await asyncio.to_thread(_gate, voice.mean(axis=1))
        await _encode(mono_voice, 1, folder / "voice.ogg", 96)
        await _encode(band, 2, folder / "band.ogg", 160)
        for f in ("mix.f32", "vocals.f32", "band.f32"):
            (folder / f).unlink(missing_ok=True)
        src.unlink(missing_ok=True)
        self._set(sid, status="ready", progress=100, duration=analysis["duration"], bpm=analysis["bpm"],
                  singing=round(sum(analysis["singing"]) / max(1, len(analysis["singing"])), 2))

    async def _split(self, sid: str, mix: Path, folder: Path) -> None:
        """Voice/band split in a child process at low priority, so the rest of the app stays snappy."""
        server = str(Path(__file__).resolve().parent.parent)
        env = {**os.environ, "PYTHONPATH": server + os.pathsep + os.environ.get("PYTHONPATH", "")}
        proc = await asyncio.create_subprocess_exec(
            "nice", "-n", "10", sys.executable, "-m", "skelly.separate", str(mix), str(folder), str(self.models),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
        assert proc.stdout
        self._split_proc = proc
        if not self._go.is_set():
            self._signal(signal.SIGSTOP)
        try:
            async for line in proc.stdout:
                text = line.decode(errors="replace").strip()
                if text.startswith("progress "):
                    self._set(sid, progress=int(text.split()[1]))
            err = await proc.stderr.read() if proc.stderr else b""
            if await proc.wait():
                raise RuntimeError(err.decode(errors="replace").strip().splitlines()[-1] if err
                                   else "the voice/band split failed")
        except asyncio.CancelledError:
            proc.kill()
            raise
        finally:
            self._split_proc = None


def _friendly(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    if "Sign in to confirm" in text or "bot" in text.lower():
        return "YouTube asked for a sign-in. Try again later, or upload the song as a file."
    if "Video unavailable" in text or "not available" in text:
        return "That video isn't available here. Try another link or the song's name."
    if "longer than 12 minutes" in text:
        return "That's longer than 12 minutes. Try the single version."
    return text[:200]


# -- playing ---------------------------------------------------------------------------------

@dataclass
class PlayerState:
    playing: bool = False
    paused: bool = False
    song: str | None = None  # song id
    title: str = ""
    artist: str = ""
    position: float = 0.0  # seconds into the song, as heard
    duration: float = 0.0
    playlist: str | None = None
    queue: list[str] = field(default_factory=list)
    index: int = 0
    loop: bool = False
    shuffle: bool = False
    error: str | None = None


class _Out:
    """One pacat stream of 16-bit PCM at 48 kHz, or nothing (no speaker / simulation)."""

    def __init__(self, sink: str | None, channels: int, enabled: bool) -> None:
        self.sink, self.channels, self.enabled = sink, channels, enabled
        self.proc: asyncio.subprocess.Process | None = None

    async def open(self) -> None:
        if not self.enabled:
            return
        args = ["pacat", "--raw", "--format=s16le", f"--rate={RATE}", f"--channels={self.channels}",
                "--latency-msec=250", "--volume=65536", "--client-name=Supreme Skelly music"]
        if self.sink:
            args.append(f"--device={self.sink}")
        self.proc = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.PIPE,
                                                         stderr=asyncio.subprocess.DEVNULL)

    async def write(self, samples: np.ndarray) -> None:
        if not self.proc or not self.proc.stdin:
            return
        pcm = (np.clip(_limit(samples), -1, 1) * 32767).astype("<i2").tobytes()
        self.proc.stdin.write(pcm)
        try:
            await self.proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            self.proc = None
            raise ConnectionError("A speaker went away during the song") from None

    async def close(self) -> None:
        from .audio_io import _stop

        await _stop(self.proc)
        self.proc = None


async def _decoder(path: Path, channels: int) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", str(channels), "-ar", str(RATE), "-",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)


class Player:
    """Plays a queue of ready songs: voice on Skelly, band on the other speakers, moves and lights in time."""

    def __init__(self, svc, library: Library, outputs: Callable[[bool], Awaitable[tuple[str | None, str | None]]],
                 cfg: Callable[[], dict], simulate: bool = False,
                 busy: Callable[[bool], None] | None = None) -> None:
        self.svc = svc
        self.busy = busy or (lambda _: None)  # told when singing starts and stops
        self.lib = library
        self.outputs = outputs  # (want extra speakers) -> (Skelly's sink, extra speakers' sink)
        self.cfg = cfg
        self.simulate = simulate
        self.state = PlayerState()
        self._task: asyncio.Task | None = None
        self._skip = asyncio.Event()
        self._resume = asyncio.Event()
        self._resume.set()
        self._jump: int | None = None

    @property
    def playing(self) -> bool:
        return self.state.playing

    def snapshot(self) -> dict:
        return asdict(self.state)

    def _publish(self, **changes) -> None:
        for k, v in changes.items():
            setattr(self.state, k, v)
        self.svc.bus.publish("music", self.snapshot())

    async def play(self, songs: list[str], start: int = 0, playlist: str | None = None,
                   shuffle: bool = False, loop: bool = False) -> dict:
        ready = {s["id"] for s in self.lib.data["songs"] if s["status"] == "ready"}
        queue = [i for i in songs if i in ready]
        if not queue:
            raise ValueError("None of those songs are ready yet")
        first = songs[start] if 0 <= start < len(songs) and songs[start] in ready else queue[0]
        if shuffle:
            rest = [i for i in queue if i != first]
            random.shuffle(rest)
            queue = [first, *rest]
        await self.stop()
        self._publish(playing=True, paused=False, queue=queue, index=queue.index(first), playlist=playlist,
                      shuffle=shuffle, loop=loop, error=None)
        self._task = asyncio.create_task(self._run(), name="music-player")
        return self.snapshot()

    async def stop(self) -> dict:
        task, self._task = self._task, None
        self._resume.set()
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self.state.playing:
            self._publish(playing=False, paused=False, position=0.0)
        return self.snapshot()

    def skip(self, step: int = 1) -> dict:
        self._jump = step
        self._skip.set()
        self._resume.set()
        return self.snapshot()

    def pause(self, paused: bool) -> dict:
        if not self.state.playing:
            return self.snapshot()
        if paused:
            self._resume.clear()
        else:
            self._resume.set()
        self._publish(paused=paused)
        return self.snapshot()

    async def _run(self) -> None:
        self.busy(True)
        try:
            while self.state.playing:
                q = self.state.queue
                if not 0 <= self.state.index < len(q):
                    if not self.state.loop:
                        break
                    self.state.index = 0
                sid = q[self.state.index]
                try:
                    song = self.lib.song(sid)
                except KeyError:
                    song = None
                if song and song["status"] == "ready":
                    self._skip.clear()
                    self._jump = None
                    await self._play_song(song)
                    if self._jump is None and self.cfg()["gap_s"] and self.state.index + 1 < len(q):
                        try:
                            await asyncio.wait_for(self._skip.wait(), self.cfg()["gap_s"])
                        except TimeoutError:
                            pass
                step = self._jump if self._jump is not None else 1
                self.state.index = max(0, self.state.index + step)
        except ConnectionError as exc:
            self._publish(error=str(exc))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("music stopped")
            self._publish(error=_friendly(exc))
        finally:
            self._publish(playing=False, paused=False, position=0.0)
            self.busy(False)

    async def _play_song(self, song: dict) -> None:
        cfg = self.cfg()
        folder = self.lib.folder(song["id"])
        analysis = json.loads((folder / "analysis.json").read_text())
        gain = song.get("gain")
        if not gain:  # worked out once per song, then remembered
            gain = await _gain(folder)
            try:
                self.lib.update(song["id"], gain=gain)
            except KeyError:
                pass
        profile = self.svc.profile.to_dict()
        events = beats.choreograph(analysis, profile, dance=cfg["dance"], lights=cfg["lights"],
                                   palette=cfg["palette"] or None, seed=song["id"])
        self._publish(song=song["id"], title=song["title"], artist=song.get("artist", ""), position=0.0,
                      duration=analysis["duration"], error=None)
        log.info("singing %s (%d moves/lights)", song["title"], len(events))

        skelly_sink, band_sink = (None, None) if self.simulate else await self.outputs(cfg["speakers"])
        voice_out = _Out(skelly_sink, 2, not self.simulate)
        band_out = _Out(band_sink, 2, not self.simulate and band_sink is not None)
        voice_dec = await _decoder(folder / "voice.ogg", 1)
        band_dec = await _decoder(folder / "band.ogg", 2)
        show: asyncio.Task | None = None
        try:
            await voice_out.open()
            await band_out.open()
            n = int(RATE * CHUNK_S)
            # Line the other speakers up with Skelly by starting one stream a little late.
            offset = cfg["speakers_offset_ms"] / 1000
            if offset > 0:
                await band_out.write(np.zeros(int(RATE * offset) * 2, np.float32))
            elif offset < 0:
                await voice_out.write(np.zeros(int(RATE * -offset) * 2, np.float32))
            await asyncio.sleep(0.4)  # give PipeWire a moment to hook the streams up before timing starts
            lead = AUDIO_DELAY_S + cfg["sync_ms"] / 1000 + max(0.0, -offset)
            t0 = time.monotonic()
            written = 0.0
            clock = {"t0": t0, "lead": lead}
            show = asyncio.create_task(self._show(events, clock), name="music-show")
            last_pub = 0.0
            live = {"sync_ms": cfg["sync_ms"], "offset_ms": cfg["speakers_offset_ms"]}
            last_cfg = 0.0
            while True:
                if time.monotonic() - last_cfg > 0.25:  # sliders moved mid-song apply straight away
                    last_cfg = time.monotonic()
                    cfg = self.cfg()
                    gain_v = cfg["voice_level"] / 100 * gain
                    # With other speakers playing the band, Skelly's speaker keeps to the voice (cleanest mouth).
                    gain_b_skelly = 0.0 if band_out.enabled else cfg["skelly_band"] / 100 * gain
                    gain_spk = cfg["speakers_level"] / 100 * gain
                    clock["lead"] += (cfg["sync_ms"] - live["sync_ms"]) / 1000
                    live["sync_ms"] = cfg["sync_ms"]
                    # A new "other speakers" offset: hold back whichever stream should now be later,
                    # a little at a time so neither speaker runs dry. Holding back the voice is
                    # picked up by the clock below like any other delay.
                    step = max(-100, min(100, cfg["speakers_offset_ms"] - live["offset_ms"]))
                    if band_out.enabled and step:
                        live["offset_ms"] += step
                        if step > 0:
                            await band_out.write(np.zeros(int(RATE * step / 1000) * 2, np.float32))
                        else:
                            await voice_out.write(np.zeros(int(RATE * -step / 1000) * 2, np.float32))
                if not self._resume.is_set():
                    await self._halt_body()
                    await self._resume.wait()
                    clock["t0"] = time.monotonic() - written  # the speakers ran dry, so pick up from here
                if self._skip.is_set():
                    break
                v_raw = await voice_dec.stdout.read(n * 4)
                b_raw = await band_dec.stdout.read(n * 2 * 4)
                if not v_raw and not b_raw:
                    break
                v = np.frombuffer(v_raw[: len(v_raw) // 4 * 4], np.float32)
                b = np.frombuffer(b_raw[: len(b_raw) // 8 * 8], np.float32).reshape(-1, 2)
                m = max(len(v), len(b))
                v = np.pad(v, (0, m - len(v)))
                b = np.pad(b, ((0, m - len(b)), (0, 0)))
                await voice_out.write(_skelly_mix(v * gain_v, b.mean(axis=1) * gain_b_skelly, cfg["voice_side"]))
                if band_out.enabled:
                    spk = b + (v[:, None] if cfg["speakers_voice"] else 0)
                    await band_out.write((spk * gain_spk).reshape(-1))
                written += m / RATE
                now = time.monotonic()
                if self.simulate:  # no speaker to pace us: keep real time ourselves
                    ahead = written - (now - clock["t0"]) - 0.1
                    if ahead > 0:
                        await asyncio.sleep(ahead)
                    now = time.monotonic()
                behind = (now - clock["t0"]) - written
                if behind > 0.02:  # the speaker ran dry (we were late), so everything heard slipped
                    clock["t0"] += behind
                heard = now - clock["t0"] - clock["lead"]
                if now - last_pub > 0.5:
                    last_pub = now
                    self._publish(position=round(max(0.0, heard), 1))
            # Let the end of the song play out before the next one (or stillness).
            if not self._skip.is_set():
                await asyncio.sleep(max(0.0, clock["t0"] + written + clock["lead"] - time.monotonic()))
        finally:
            if show:
                show.cancel()
            for proc in (voice_dec, band_dec):
                if proc.returncode is None:
                    proc.kill()
            await voice_out.close()
            await band_out.close()
            await self._halt_body(restore=True)

    async def _halt_body(self, restore: bool = False) -> None:
        """Stop moving; after a song, put his own lights and eyes back."""
        if not self.svc.link.connected:
            return
        try:
            await self.svc.set_movement([])
            if restore:
                await self.svc.apply_look()
        except Exception as exc:
            log.info("couldn't settle Skelly after the song: %r", exc)

    async def _show(self, events: list[dict], clock: dict) -> None:
        """Send each move and light change so it lands as its beat is heard."""
        svc = self.svc
        look_eye = (svc.look or {}).get("eye")
        for i, ev in enumerate(events):
            while True:  # re-check as we go: pauses and dropouts move the clock
                await self._resume.wait()
                wait = clock["t0"] + clock["lead"] + ev["t"] - BLE_LEAD_S - time.monotonic()
                if wait <= 0:
                    break
                await asyncio.sleep(min(wait, 0.25))
            heard = time.monotonic() - clock["t0"] - clock["lead"] + BLE_LEAD_S
            if heard - ev["t"] > LATE_S and any(
                    e["kind"] == ev["kind"] and e.get("light") == ev.get("light") and e["t"] <= heard
                    for e in events[i + 1:]):
                continue  # running behind: a newer change of the same kind is already due
            if not svc.link.connected:
                continue
            try:
                await self._send(ev, look_eye)
            except (ConnectionError, ValueError) as exc:
                log.debug("show step failed: %r", exc)

    async def _send(self, ev: dict, look_eye: int | None) -> None:
        svc = self.svc
        kind = ev["kind"]
        if kind == "move":
            await svc.send(proto.movement(svc.movement_mask(ev["parts"])))
            return
        if kind == "eye":
            value = ev["value"] if ev["value"] is not None else look_eye
            if value:
                await svc.send(proto.eye(value))
            return
        ch = svc._channel(ev.get("light"))
        if kind == "color":
            await svc.send(proto.color(ch, *ev["rgb"]))
        elif kind == "bright":
            await svc.send(proto.brightness(ch, ev["value"]))
        elif kind == "mode":
            await svc.send(proto.light_mode(ch, ev["value"]))
