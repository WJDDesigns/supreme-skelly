"""Playlist: plays the ticked sounds on Skelly in order, driven by his own playback events.

Like the original controller, the queue lives here rather than on the device (its
play-order command is known to corrupt custom sounds). Each sound gets a start
watchdog: if Skelly doesn't report playback starting, the play command is sent once
more, then the sound is skipped. Progression waits for Skelly's "stopped" event,
with a timeout from the length he reports in case that event goes missing.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .service import SkellyService

log = logging.getLogger(__name__)

START_TIMEOUT_S = 4.0
END_GRACE_S = 4.0  # added to the length Skelly reports before giving up on his "stopped" event
UNKNOWN_LENGTH_S = 360.0
DEFAULT_BEFORE_S = 0.0
DEFAULT_AFTER_S = 1.5
MAX_DELAY_S = 600.0


@dataclass
class PlaylistStatus:
    running: bool = False
    name: str | None = None  # sound playing (or about to)
    position: int = 0  # 1-based within this run
    total: int = 0
    waiting: float = 0.0  # seconds of delay before the next step, if pausing
    loop: bool = False


def normalize(config: dict | None) -> dict:
    """Clean a playlist config from the UI or settings file."""
    config = config or {}
    items, seen = [], set()
    for it in config.get("items", []):
        name = str(it.get("name", "")).strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())

        def secs(key: str, default: float, it=it) -> float:
            try:
                return round(min(MAX_DELAY_S, max(0.0, float(it.get(key, default)))), 1)
            except (TypeError, ValueError):
                return default

        items.append({"name": name, "on": bool(it.get("on", True)),
                      "before": secs("before", DEFAULT_BEFORE_S), "after": secs("after", DEFAULT_AFTER_S)})
    return {"items": items, "loop": bool(config.get("loop", False)), "shuffle": bool(config.get("shuffle", False))}


class Playlist:
    def __init__(self, svc: SkellyService) -> None:
        self.svc = svc
        self.status = PlaylistStatus()
        self._task: asyncio.Task | None = None
        self._skip = asyncio.Event()

    @property
    def config(self) -> dict:
        return normalize(self.svc.settings.playlist)

    def set_config(self, config: dict) -> dict:
        self.svc.settings.playlist = normalize(config)
        self.svc.settings.save()
        self.status.loop = self.svc.settings.playlist["loop"]
        self.svc.bus.publish("settings", self.svc.settings.public())
        return self.svc.settings.playlist

    def snapshot(self) -> dict:
        return asdict(self.status)

    def _publish(self, **changes) -> None:
        for k, v in changes.items():
            setattr(self.status, k, v)
        self.svc.bus.publish("playlist", self.snapshot())

    def queue(self) -> list[dict]:
        """Ticked sounds that are still on Skelly, in playlist order."""
        on_device = {f["name"].lower() for f in self.svc.state.files if f.get("name")}
        return [it for it in self.config["items"] if it["on"] and it["name"].lower() in on_device]

    async def play(self) -> dict:
        self.svc._require_connected()
        items = self.queue()
        if not items:
            raise ValueError("Tick at least one sound for the playlist first")
        await self.stop()
        self._task = asyncio.create_task(self._run(), name="skelly-playlist")
        self._publish(running=True, name=items[0]["name"], position=1, total=len(items), loop=self.config["loop"])
        return self.snapshot()

    async def stop(self) -> dict:
        task, self._task = self._task, None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return self.snapshot()

    def skip(self) -> dict:
        self._skip.set()
        return self.snapshot()

    async def _pause(self, seconds: float) -> None:
        if seconds <= 0:
            return
        self._publish(waiting=seconds)
        try:
            await asyncio.wait_for(self._skip.wait(), seconds)
        except TimeoutError:
            pass
        self._skip.clear()
        self._publish(waiting=0.0)

    async def _run(self) -> None:
        events = self.svc.bus.subscribe()
        playing_serial = None
        try:
            while True:
                items = self.queue()
                if not items:
                    break
                if self.config["shuffle"]:
                    random.shuffle(items)
                for pos, it in enumerate(items, start=1):
                    f = self.svc.file_named(it["name"])
                    if not f:
                        continue  # deleted while playing
                    self._publish(running=True, name=f["name"], position=pos, total=len(items),
                                  loop=self.config["loop"])
                    await self._pause(it["before"])
                    playing_serial = f["serial"]
                    await self._play_one(f, events)
                    playing_serial = None
                    await self._pause(it["after"])
                if not self.config["loop"]:
                    break
            log.info("Playlist finished")
        except ConnectionError:
            log.info("Playlist stopped: Skelly disconnected")
        except asyncio.CancelledError:
            if playing_serial is not None and self.svc.link.connected:
                try:
                    await self.svc.play_file(playing_serial, False)
                except ConnectionError:
                    pass
            raise
        finally:
            self.svc.bus.unsubscribe(events)
            self._skip.clear()
            self._publish(running=False, name=None, position=0, total=0, waiting=0.0)

    async def _play_one(self, f: dict, events: asyncio.Queue) -> None:
        self._skip.clear()
        _drain(events)
        started = None
        for attempt in (1, 2):
            await self.svc.play_file(f["serial"])
            started = await _next_playback(events, True, START_TIMEOUT_S)
            if started:
                break
            log.info("Playlist: %s didn't start (try %d)", f["name"], attempt)
        if not started:
            log.warning("Playlist: skipping %s, Skelly never started it", f["name"])
            return
        length = started.get("duration") or 0
        limit = length + END_GRACE_S if length else UNKNOWN_LENGTH_S
        stop = asyncio.create_task(_next_playback(events, False, limit))
        skip = asyncio.create_task(self._skip.wait())
        try:
            done, _ = await asyncio.wait({stop, skip}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stop.cancel()
            skip.cancel()
        if skip in done:
            self._skip.clear()
            await self.svc.play_file(f["serial"], False)


def _drain(q: asyncio.Queue) -> None:
    while not q.empty():
        q.get_nowait()


async def _next_playback(events: asyncio.Queue, playing: bool, timeout: float) -> dict | None:
    """Wait for Skelly's next start (or stop) event. Its serial isn't trusted: the original
    controller found it doesn't always match the sound list's numbering."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while (left := deadline - loop.time()) > 0:
        try:
            msg = await asyncio.wait_for(events.get(), left)
        except TimeoutError:
            break
        d = msg.get("data") or {}
        if msg.get("type") == "device_event" and d.get("kind") == "playback" and d.get("playing") == playing:
            return d
        if msg.get("type") == "state" and (msg.get("data") or {}).get("status") != "connected":
            raise ConnectionError("disconnected")
    return None
