"""HTTP routes for music mode (see music.py)."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from . import beats, music, sources

log = logging.getLogger(__name__)

MAX_SONG_BYTES = 80 * 1024 * 1024


class AddBody(BaseModel):
    text: str
    playlist: str | None = None  # also put the new songs on this playlist


class PlaylistBody(BaseModel):
    id: str | None = None
    name: str
    songs: list[str] = []


class PlayBody(BaseModel):
    songs: list[str] | None = None
    playlist: str | None = None
    start: int = 0
    shuffle: bool = False
    loop: bool = False


class SkipBody(BaseModel):
    step: int = 1


class PauseBody(BaseModel):
    paused: bool


def register(app: FastAPI) -> None:
    def lib() -> music.Library:
        return app.state.library

    def changed() -> dict:
        data = lib().public()
        app.state.svc.bus.publish("music_library", data)
        return data

    def cfg() -> dict:
        return music.config(app.state.svc.settings.music)

    @app.get("/api/music")
    async def music_state():
        return {"library": lib().public(), "player": app.state.music.snapshot(), "config": cfg(),
                "palettes": sorted(beats.PALETTES)}

    @app.put("/api/music/config")
    async def music_config(body: dict):
        svc = app.state.svc
        svc.settings.music = music.config({**svc.settings.music, **body})
        svc.settings.save()
        return svc.settings.music

    def add_to_playlist(playlist_id: str | None, ids: list[str]) -> None:
        if not playlist_id:
            return
        try:
            p = lib().playlist(playlist_id)
        except KeyError:
            return
        lib().save_playlist(p["id"], p["name"], [*p["songs"], *ids])

    @app.post("/api/music/add")
    async def music_add(body: AddBody):
        """A YouTube, Spotify or Apple Music link (song, album or playlist), or a song's name."""
        try:
            tracks = await sources.resolve(body.text)
        except (ValueError, LookupError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            log.info("couldn't read %s: %r", body.text, exc)
            raise HTTPException(502, f"Couldn't read that link: {music._friendly(exc)}") from exc
        try:
            added = [lib().add(t) for t in tracks]
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        add_to_playlist(body.playlist, [s["id"] for s in added])
        app.state.preparer.kick()
        changed()
        return {"added": added}

    @app.post("/api/music/upload")
    async def music_upload(file: Annotated[UploadFile, File()], playlist: Annotated[str, Form()] = ""):
        data = await file.read(MAX_SONG_BYTES + 1)
        if len(data) > MAX_SONG_BYTES:
            raise HTTPException(413, "That file is too big (80 MB max)")
        stem = Path(file.filename or "song").stem
        artist, _, title = stem.partition(" - ") if " - " in stem else ("", "", stem)
        try:
            song = lib().add(sources.Track(title=title.strip() or "Song", artist=artist.strip(), source="file",
                                           url="file"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        ext = re.sub(r"[^a-z0-9]", "", Path(file.filename or "").suffix.lower())[:5] or "bin"
        folder = lib().folder(song["id"])
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"source.{ext}"
        path.write_bytes(data)
        app.state.preparer.add_file(song, path)
        add_to_playlist(playlist or None, [song["id"]])
        changed()
        return song

    @app.post("/api/music/songs/{song_id}/retry")
    async def music_retry(song_id: str):
        try:
            lib().update(song_id, status="waiting", progress=0, error=None)
        except KeyError as exc:
            raise HTTPException(404, "No such song") from exc
        app.state.preparer.kick()
        return changed()

    @app.delete("/api/music/songs/{song_id}")
    async def music_remove(song_id: str):
        if app.state.music.state.song == song_id:
            await app.state.music.stop()
        lib().remove(song_id)
        return changed()

    @app.put("/api/music/playlists")
    async def music_playlist_save(body: PlaylistBody):
        try:
            p = lib().save_playlist(body.id, body.name, body.songs)
        except KeyError as exc:
            raise HTTPException(404, "No such playlist") from exc
        changed()
        return p

    @app.delete("/api/music/playlists/{playlist_id}")
    async def music_playlist_delete(playlist_id: str):
        lib().delete_playlist(playlist_id)
        return changed()

    @app.post("/api/music/play")
    async def music_play(body: PlayBody):
        svc = app.state.svc
        if not svc.link.connected and not app.state.music.simulate:
            raise HTTPException(409, "Connect to Skelly first, so he can sing")
        songs = body.songs
        if body.playlist:
            try:
                songs = lib().playlist(body.playlist)["songs"]
            except KeyError as exc:
                raise HTTPException(404, "No such playlist") from exc
        if songs is None:
            songs = [s["id"] for s in lib().data["songs"]]
        await app.state.playlist.stop()  # his own sounds and a song at once would talk over each other
        try:
            return await app.state.music.play(songs, body.start, body.playlist, body.shuffle, body.loop)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/music/stop")
    async def music_stop():
        return await app.state.music.stop()

    @app.post("/api/music/skip")
    async def music_skip(body: SkipBody):
        return app.state.music.skip(body.step)

    @app.post("/api/music/pause")
    async def music_pause(body: PauseBody):
        return app.state.music.pause(body.paused)
