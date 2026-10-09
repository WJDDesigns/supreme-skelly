"""Fog machine: a Wi-Fi relay "presses" the fog button on the machine's remote.

The relay sits across the button on the fog machine's battery keyfob (or its wired remote,
if that one is low voltage), so the machine stays powered and its heater stays warm. Skelly
talks to the relay on the local network only: Shelly, Sonoff (LAN mode), Tasmota, ESPHome,
or any pair of web links.

Fog comes from three places: the Fog button on the page, a visitor walking up, and a
"fog is thin" check that watches a spot on the camera. Fog blurs and greys out what's
behind it, so the fine detail in that spot (compared with how it looks with no fog)
says how thick the fog is.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import asdict, dataclass, field

import httpx

log = logging.getLogger(__name__)

MAX_BURST_S = 25  # these machines run dry of heat after about 25 s of fog anyway
TAP_S = 0.5  # how long a "tap" holds the remote's button

KINDS = {
    "shelly": "Shelly (Plus, Pro, Gen3/4)",
    "shelly1": "Shelly Gen 1",
    "sonoff": "Sonoff (LAN / DIY mode)",
    "tasmota": "Tasmota",
    "esphome": "ESPHome",
    "url": "Web links",
}


@dataclass
class FogConfig:
    enabled: bool = False
    kind: str = "shelly"
    host: str = ""  # relay's IP address or name on the network
    channel: int = 0  # which relay output (Shelly/Tasmota count from 0 here; ESPHome uses `entity`)
    entity: str = "relay"  # ESPHome switch id
    on_url: str = ""  # "url" kind: opened to press the button
    off_url: str = ""  # "url" kind: opened to let go (optional)
    # hold: the relay stays closed for the whole burst, like holding the remote's button down.
    # tap: a short tap starts the fog; after the burst, a tap on stop_channel stops it
    # (-1 = no stop tap, the machine times out by itself).
    mode: str = "hold"
    stop_channel: int = -1
    burst_s: float = 5.0
    cooldown_s: int = 60  # rest between automatic puffs
    max_per_hour: int = 20  # automatic puffs per hour, so a busy night can't empty the tank
    on_visitor: bool = True  # puff when someone walks up or Skelly calls them over
    top_up: bool = False  # puff when the camera says the fog has thinned out
    thin_below: int = 30  # "thin" means the fog meter reads below this (0..100)
    camera: str = ""  # camera the fog meter watches: "" = the Vision page's camera, else a Protect camera id
    zone: list = field(default_factory=list)  # [x, y, w, h] fractions of the picture to watch
    clear_detail: float = 0.0  # detail in the zone with no fog at all (set by "Calibrate")

    @classmethod
    def from_dict(cls, d: dict | None) -> FogConfig:
        known = set(cls.__dataclass_fields__)
        cfg = cls(**{k: v for k, v in (d or {}).items() if k in known})
        cfg.burst_s = max(0.5, min(float(cfg.burst_s), MAX_BURST_S))
        cfg.cooldown_s = max(10, int(cfg.cooldown_s))
        cfg.max_per_hour = max(1, min(int(cfg.max_per_hour), 120))
        cfg.thin_below = max(1, min(int(cfg.thin_below), 99))
        if cfg.kind not in KINDS:
            cfg.kind = "shelly"
        if cfg.mode not in ("hold", "tap"):
            cfg.mode = "hold"
        try:
            cfg.zone = [round(min(max(float(v), 0.0), 1.0), 4) for v in cfg.zone] if len(cfg.zone) == 4 else []
        except (TypeError, ValueError):
            cfg.zone = []
        return cfg


@dataclass
class FogState:
    fogging: bool = False
    last_at: float | None = None
    last_why: str = ""
    error: str | None = None
    level: int | None = None  # fog meter 0..100 from the camera, None when it can't tell
    puffs_last_hour: int = 0


class Relay:
    """Closes and opens one relay output over the local network."""

    def __init__(self, cfg: FogConfig) -> None:
        self.cfg = cfg

    def _base(self) -> str:
        host = self.cfg.host.strip().rstrip("/")
        if not host and self.cfg.kind != "url":
            raise ValueError("Enter the relay's IP address first.")
        return host if host.startswith("http") else f"http://{host}"

    async def set(self, on: bool, channel: int | None = None, auto_off: float | None = None) -> None:
        """Switch an output. `auto_off` asks relays that can to switch themselves off after that
        many seconds, so the fog stops even if the mini PC goes away mid-burst."""
        c = self.cfg
        ch = c.channel if channel is None else channel
        async with httpx.AsyncClient(timeout=5) as http:
            if c.kind == "shelly":
                params = {"id": ch, "on": "true" if on else "false"}
                if on and auto_off:
                    params["toggle_after"] = round(auto_off, 1)
                r = await http.get(f"{self._base()}/rpc/Switch.Set", params=params)
            elif c.kind == "shelly1":
                params = {"turn": "on" if on else "off"}
                if on and auto_off:
                    params["timer"] = round(auto_off, 1)
                r = await http.get(f"{self._base()}/relay/{ch}", params=params)
            elif c.kind == "tasmota":
                cmd = f"Power{ch + 1} {'On' if on else 'Off'}"
                if on and auto_off:  # Delay counts tenths of a second, run on the device itself
                    cmd = f"Backlog Power{ch + 1} On; Delay {max(1, round(auto_off * 10))}; Power{ch + 1} Off"
                r = await http.get(f"{self._base()}/cm", params={"cmnd": cmd})
            elif c.kind == "sonoff":
                base = self._base()
                if ":" not in base.split("//", 1)[1]:
                    base += ":8081"
                r = await http.post(f"{base}/zeroconf/switch",
                                    json={"deviceid": "", "data": {"switch": "on" if on else "off"}})
            elif c.kind == "esphome":
                r = await http.post(f"{self._base()}/switch/{c.entity.strip() or 'relay'}/"
                                    f"{'turn_on' if on else 'turn_off'}")
            else:
                url = c.on_url if on else c.off_url
                if not url:
                    if on:
                        raise ValueError("Enter the web link that starts the fog first.")
                    return
                r = await http.get(url)
            r.raise_for_status()

    async def check(self) -> str:
        """Ask the relay something harmless, to prove it's reachable. Returns what it said."""
        c = self.cfg
        async with httpx.AsyncClient(timeout=5) as http:
            if c.kind == "shelly":
                r = await http.get(f"{self._base()}/rpc/Switch.GetStatus", params={"id": c.channel})
            elif c.kind == "shelly1":
                r = await http.get(f"{self._base()}/relay/{c.channel}")
            elif c.kind == "tasmota":
                r = await http.get(f"{self._base()}/cm", params={"cmnd": f"Power{c.channel + 1}"})
            elif c.kind == "sonoff":
                base = self._base()
                if ":" not in base.split("//", 1)[1]:
                    base += ":8081"
                r = await http.post(f"{base}/zeroconf/info", json={"deviceid": "", "data": {}})
            elif c.kind == "esphome":
                r = await http.get(f"{self._base()}/switch/{c.entity.strip() or 'relay'}")
            else:
                return "Web links can't be checked without pressing the button. Use Test fog."
            r.raise_for_status()
            return r.text[:200]


def detail(thumb: bytes, width: int, height: int, zone: list) -> float | None:
    """How much fine detail is in `zone` of a grey thumbnail: the average step between
    neighbouring pixels. Fog smooths these steps out."""
    if len(zone) != 4:
        return None
    x, y, w, h = zone
    x0, y0 = int(x * width), int(y * height)
    x1, y1 = min(width, int((x + w) * width + 0.999)), min(height, int((y + h) * height + 0.999))
    if x1 - x0 < 3 or y1 - y0 < 3:
        return None
    total, n = 0, 0
    for row in range(y0, y1 - 1):
        i = row * width
        for col in range(x0, x1 - 1):
            p = thumb[i + col]
            total += abs(p - thumb[i + col + 1]) + abs(p - thumb[i + width + col])
            n += 2
    return total / n if n else None


def fog_level(now_detail: float | None, clear_detail: float) -> int | None:
    """Fog meter 0..100: 0 = as sharp as with no fog, 100 = nothing left to see."""
    if now_detail is None or clear_detail <= 0:
        return None
    return round(max(0.0, min(1.0, 1 - now_detail / clear_detail)) * 100)


class Fog:
    def __init__(self, svc, config_getter, allowed=lambda: True) -> None:
        self.svc = svc
        self._config = config_getter  # -> dict
        self._allowed = allowed  # automatic fog only when Skelly is on and it's not quiet hours
        self.state = FogState()
        self._lock = asyncio.Lock()
        self._auto: deque = deque(maxlen=200)  # times of automatic puffs
        self._detail: float | None = None  # smoothed detail in the zone
        self._thin_since: float | None = None
        self._last_thumb = 0.0
        self._zone: list = []
        self.frame: bytes | None = None  # latest picture from the fog camera, when it isn't the Vision camera
        self.viewed_at = 0.0  # when the Fog page last showed the fog camera

    @property
    def cfg(self) -> FogConfig:
        return FogConfig.from_dict(self._config())

    def snapshot(self) -> dict:
        now = time.time()
        self.state.puffs_last_hour = sum(1 for t in self._auto if now - t < 3600)
        return asdict(self.state)

    def _publish(self) -> None:
        self.svc.bus.publish("fog", self.snapshot())

    async def puff(self, seconds: float | None = None, why: str = "Fog button") -> dict:
        """Make fog now. Waits for the burst to finish."""
        cfg = self.cfg
        if not cfg.enabled:
            raise ValueError("Turn the fog machine on in the Fog card first.")
        if self._lock.locked():
            raise ValueError("Already making fog.")
        burst = max(0.5, min(float(seconds or cfg.burst_s), MAX_BURST_S))
        relay = Relay(cfg)
        async with self._lock:
            self.state.fogging, self.state.last_at, self.state.last_why = True, time.time(), why
            self.state.error = None
            self._publish()
            log.info("fog for %.1f s (%s)", burst, why)
            try:
                if cfg.mode == "tap":
                    await self._tap(relay, cfg.channel)
                    await asyncio.sleep(burst)
                    if cfg.stop_channel >= 0:
                        await self._tap(relay, cfg.stop_channel)
                else:
                    try:
                        await relay.set(True, auto_off=burst)
                        await asyncio.sleep(burst)
                    finally:
                        await self._off(relay, cfg.channel)
            except (httpx.HTTPError, ValueError) as exc:
                self.state.error = _explain(exc)
                log.info("fog failed: %s", self.state.error)
                raise ValueError(self.state.error) from exc
            finally:
                self.state.fogging = False
                self._publish()
        return self.snapshot()

    async def stop(self) -> dict:
        """Let go of the button right away."""
        cfg = self.cfg
        relay = Relay(cfg)
        try:
            if cfg.mode == "tap" and cfg.stop_channel >= 0:
                await self._tap(relay, cfg.stop_channel)
            else:
                await relay.set(False)
        except (httpx.HTTPError, ValueError) as exc:
            raise ValueError(_explain(exc)) from exc
        return self.snapshot()

    async def check(self) -> str:
        try:
            return await Relay(self.cfg).check()
        except (httpx.HTTPError, ValueError) as exc:
            raise ValueError(_explain(exc)) from exc

    @staticmethod
    async def _tap(relay: Relay, channel: int) -> None:
        try:
            await relay.set(True, channel, auto_off=TAP_S)
            await asyncio.sleep(TAP_S)
        finally:
            await Fog._off(relay, channel)

    @staticmethod
    async def _off(relay: Relay, channel: int) -> None:
        for attempt in range(3):  # the button must not stay held down
            try:
                await relay.set(False, channel)
                return
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("couldn't let go of the fog button (try %d): %s", attempt + 1, exc)
                await asyncio.sleep(0.5)

    # -- automatic fog ------------------------------------------------------------

    def _can_auto(self, cfg: FogConfig) -> str | None:
        """Why an automatic puff can't happen right now, or None if it can."""
        now = time.time()
        if not cfg.enabled:
            return "off"
        if not self._allowed():
            return "Skelly is off or it's quiet hours"
        if self._lock.locked():
            return "already fogging"
        if self.state.last_at and now - self.state.last_at < cfg.cooldown_s:
            return "resting"
        if sum(1 for t in self._auto if now - t < 3600) >= cfg.max_per_hour:
            return "hourly limit reached"
        return None

    async def auto(self, why: str) -> bool:
        cfg = self.cfg
        if (reason := self._can_auto(cfg)) is not None:
            log.debug("no automatic fog (%s): %s", why, reason)
            return False
        self._auto.append(time.time())
        try:
            await self.puff(why=why)
        except ValueError:
            return False
        return True

    async def visitor(self, why: str = "Someone walked up") -> None:
        if self.cfg.on_visitor:
            await self.auto(why)

    def thumb(self, thumb: bytes, width: int, height: int, camera: str = "") -> None:
        """A new grey thumbnail from `camera` ("" = the Vision camera): update the fog meter and
        top up when it's thin. Thumbnails from other cameras than the fog camera are ignored."""
        cfg = self.cfg
        if camera != cfg.camera:
            return
        now = time.monotonic()
        if now - self._last_thumb < 1:  # once a second is plenty
            return
        self._last_thumb = now
        if cfg.zone != self._zone:  # a new spot: start the average afresh
            self._zone, self._detail = cfg.zone, None
        d = detail(thumb, width, height, cfg.zone)
        if d is None:
            self._detail = None
            self.state.level = None
            return
        # Smooth over ~10 s so someone walking through the zone doesn't swing the meter.
        self._detail = d if self._detail is None else self._detail * 0.9 + d * 0.1
        level = fog_level(self._detail, cfg.clear_detail)
        if level != self.state.level:
            self.state.level = level
            self.svc.bus.publish("fog_level", {"level": level})
        if not (cfg.top_up and level is not None and level < cfg.thin_below and not self.state.fogging):
            self._thin_since = None
            return
        self._thin_since = self._thin_since or now
        if now - self._thin_since >= 15:  # thin for a while, not just a gust
            self._thin_since = None
            asyncio.get_running_loop().create_task(self.auto(f"Fog thinned out (meter {level})"))

    def calibrate(self) -> float:
        """The fog zone's detail right now, to save as the "no fog" reference."""
        if self._detail is None:
            raise ValueError("Start the camera and draw the fog area first.")
        return round(self._detail, 3)


def _explain(exc: Exception) -> str:
    if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout):
        return "Couldn't reach the relay. Check its IP address and that it's on the same Wi-Fi."
    if isinstance(exc, httpx.TimeoutException):
        return "The relay didn't answer in time."
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 401:
            return "The relay wants a password. Turn off its login, or use a web link with the password in it."
        if code == 404:
            return "The relay doesn't know that output. Check the relay type and output number."
        return f"The relay said no ({code})."
    return str(exc) or exc.__class__.__name__
