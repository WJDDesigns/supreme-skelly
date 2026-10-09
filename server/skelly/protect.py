"""UniFi Protect bridge: Protect's own person/face detections drive Skelly's eyes.

Protect already watches every camera for people and faces on the NVR, so Skelly listens to
its official Integration API event stream instead of analysing more video itself. When a
person or face shows up on one of the chosen cameras, a full-resolution snapshot is
fetched and run through face memory: those faces are many times sharper than the ones in
the live preview stream. Costs the mini PC almost nothing per extra camera.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from collections import deque

import httpx

from .settings import data_dir

log = logging.getLogger(__name__)

PERSON_TYPES = {"person", "face"}
PERSON_EVENTS = {"smartDetectZone", "smartDetectLine", "smartDetectLoiterZone", "faceGroupDetected"}


def _ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # Protect consoles use a self-signed certificate
    return ctx


class Protect:
    def __init__(self, svc, vault, config_getter, on_person) -> None:
        self.svc = svc
        self.vault = vault
        self._config = config_getter  # -> VisionConfig
        self._on_person = on_person  # async (camera_name, jpeg, event) -> None
        self._task: asyncio.Task | None = None
        self.connected = False
        self.error: str | None = None
        self.recent: deque = deque(maxlen=20)  # last events, for the page and for debugging
        self._last_shot: dict[str, float] = {}
        self._names: dict[str, str] = {}

    @property
    def host(self) -> str:
        return (self._config().protect_host or "").strip()

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=f"https://{self.host}/proxy/protect/integration/v1",
                                 headers={"X-API-KEY": self.vault.get("protect_api_key") or ""},
                                 verify=False, timeout=20)

    async def cameras(self) -> list[dict]:
        async with self._client() as c:
            r = await c.get("/cameras")
            r.raise_for_status()
        cams = [{"id": x["id"], "name": x.get("name") or x["id"], "online": x.get("state") == "CONNECTED",
                 "faces": "face" in ((x.get("smartDetectSettings") or {}).get("objectTypes") or [])}
                for x in r.json()]
        self._names = {c["id"]: c["name"] for c in cams}
        return sorted(cams, key=lambda c: c["name"].lower())

    async def snapshot(self, camera_id: str, high: bool = True) -> bytes:
        async with self._client() as c:
            r = await c.get(f"/cameras/{camera_id}/snapshot", params={"highQuality": "true" if high else "false"})
            r.raise_for_status()
            return r.content

    async def rtsps(self, camera_id: str, quality: str = "high") -> str | None:
        """The camera's RTSPS address (Protect hands these out per quality)."""
        async with self._client() as c:
            r = await c.get(f"/cameras/{camera_id}/rtsps-stream")
            if r.status_code == 200 and (url := r.json().get(quality)):
                return url
            r = await c.post(f"/cameras/{camera_id}/rtsps-stream", json={"qualities": [quality]})
            return r.json().get(quality) if r.status_code < 300 else None

    def start(self) -> None:
        if not self._task or self._task.done():
            self._task = asyncio.create_task(self._run(), name="skelly-protect")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self.connected = False

    def snapshot_state(self) -> dict:
        return {"connected": self.connected, "error": self.error, "recent": list(self.recent)[-8:]}

    async def _run(self) -> None:
        from websockets.asyncio.client import connect

        backoff = 2
        while True:
            cfg = self._config()
            if not (cfg.protect and self.host and self.vault.get("protect_api_key")):
                self.connected = False
                await asyncio.sleep(5)
                continue
            try:
                if not self._names:
                    await self.cameras()
                url = f"wss://{self.host}/proxy/protect/integration/v1/subscribe/events"
                async with connect(url, additional_headers={"X-API-KEY": self.vault.get("protect_api_key")},
                                   ssl=_ctx(), open_timeout=15, ping_interval=30) as ws:
                    self.connected, self.error, backoff = True, None, 2
                    log.info("listening to UniFi Protect events on %s", self.host)
                    async for raw in ws:
                        try:
                            await self._handle(json.loads(raw))
                        except Exception as exc:
                            log.info("protect event not handled: %r", exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected = False
                self.error = str(exc)[:200]
                log.info("Protect connection dropped: %s; retrying in %ss", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _handle(self, msg: dict) -> None:
        ev = msg.get("item") if isinstance(msg.get("item"), dict) else msg
        kind = ev.get("type") or ""
        camera = ev.get("device") or ev.get("deviceId") or ev.get("camera") or ""
        types = set(ev.get("smartDetectTypes") or [])
        self._log(msg)
        cfg = self._config()
        if camera not in (cfg.protect_cameras or []):
            return
        if kind not in PERSON_EVENTS and not (types & PERSON_TYPES):
            return
        now = time.monotonic()
        if now - self._last_shot.get(camera, 0) < 2.5:  # Protect sends add + several updates per event
            return
        self._last_shot[camera] = now
        jpeg = await self.snapshot(camera)
        name = self._names.get(camera, camera)
        self.recent.append({"camera": name, "type": kind, "objects": sorted(types), "ts": time.time()})
        self.svc.bus.publish("protect", self.snapshot_state())
        await self._on_person(name, jpeg, ev)

    def _log(self, msg: dict) -> None:
        """Keep the last raw events on disk, to see what each Protect version sends."""
        try:
            p = data_dir() / "protect-events.jsonl"
            if p.exists() and p.stat().st_size > 512 * 1024:
                p.write_text("")
            with p.open("a") as f:
                f.write(json.dumps(msg)[:4000] + "\n")
        except OSError:
            pass
