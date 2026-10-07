"""Microphone in, Skelly's speaker out, through the host's PipeWire (PulseAudio protocol).

The container reaches PipeWire over its pulse socket (PULSE_SERVER) and uses the stock
`parec` / `pacat` / `pactl` tools, so there are no native audio libraries to build.
Audio is 16-bit mono PCM throughout; each stream picks the sample rate its AI wants.
"""

from __future__ import annotations

import array
import asyncio
import json
import logging
import math
import os
import time

log = logging.getLogger(__name__)

FRAME_MS = 20


def rms(pcm: bytes) -> float:
    """Loudness of a 16-bit mono chunk, 0..1."""
    if len(pcm) < 2:
        return 0.0
    a = array.array("h", pcm[: len(pcm) - len(pcm) % 2])
    return math.sqrt(sum(x * x for x in a) / len(a)) / 32768.0


async def _pactl_json(*args: str) -> list[dict]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "pactl", "-f", "json", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), 5)
    except (FileNotFoundError, TimeoutError) as exc:
        log.info("pactl unavailable: %r", exc)
        return []
    if proc.returncode:
        log.info("pactl %s failed: %s", " ".join(args), err.decode(errors="replace").strip())
        return []
    try:
        return json.loads(out or b"[]")
    except ValueError:
        return []


async def list_devices() -> dict:
    """Speakers and microphones PipeWire knows about, for the Settings pickers."""
    sinks = await _pactl_json("list", "sinks")
    sources = await _pactl_json("list", "sources")
    return {
        "speakers": [{"name": s["name"], "label": s.get("description") or s["name"],
                      "bluetooth": s["name"].startswith("bluez_")} for s in sinks],
        "mics": [{"name": s["name"], "label": s.get("description") or s["name"]}
                 for s in sources if ".monitor" not in s["name"]],
    }


def skelly_sink_name(mac: str | None) -> str | None:
    return f"bluez_output.{mac.replace(':', '_').upper()}.1" if mac else None


class Mic:
    """Streams 20 ms chunks of 16-bit mono PCM from a PipeWire source."""

    def __init__(self, rate: int, source: str | None = None) -> None:
        self.rate = rate
        self.source = source
        self.level = 0.0
        self._proc: asyncio.subprocess.Process | None = None

    async def __aenter__(self) -> Mic:
        args = ["parec", "--raw", "--format=s16le", f"--rate={self.rate}", "--channels=1",
                f"--latency-msec={FRAME_MS}", "--client-name=Supreme Skelly"]
        if self.source:
            args.append(f"--device={self.source}")
        self._proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        return self

    async def __aexit__(self, *exc) -> None:
        await _stop(self._proc)

    def __aiter__(self):
        return self._chunks()

    async def _chunks(self):
        size = self.rate * 2 * FRAME_MS // 1000
        assert self._proc and self._proc.stdout
        while True:
            try:
                pcm = await self._proc.stdout.readexactly(size)
            except asyncio.IncompleteReadError:
                raise ConnectionError("The microphone stopped (is it plugged in?)") from None
            self.level = rms(pcm)
            yield pcm


class Speaker:
    """Plays 16-bit mono PCM on a PipeWire sink and knows roughly when it's still talking."""

    def __init__(self, sink: str | None = None) -> None:
        self.sink = sink
        self.rate = 0
        self._proc: asyncio.subprocess.Process | None = None
        self._busy_until = 0.0

    @property
    def speaking(self) -> bool:
        return time.monotonic() < self._busy_until

    async def _open(self, rate: int) -> None:
        await _stop(self._proc)
        args = ["pacat", "--raw", "--format=s16le", f"--rate={rate}", "--channels=1",
                "--latency-msec=80", "--client-name=Supreme Skelly"]
        if self.sink:
            args.append(f"--device={self.sink}")
        self._proc = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        self.rate = rate

    async def play(self, pcm: bytes, rate: int) -> None:
        if not pcm:
            return
        if rate != self.rate or not self._proc or self._proc.returncode is not None:
            await self._open(rate)
        assert self._proc and self._proc.stdin
        self._proc.stdin.write(pcm)
        try:
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            self._proc = None
            raise ConnectionError("Skelly's speaker went away") from None
        now = time.monotonic()
        self._busy_until = max(self._busy_until, now) + len(pcm) / (2 * rate)

    async def wait_done(self) -> None:
        while self.speaking:
            await asyncio.sleep(0.05)

    async def interrupt(self) -> None:
        """Stop talking now (the person spoke over Skelly): drop whatever is still buffered."""
        await _stop(self._proc)
        self._proc = None
        self.rate = 0
        self._busy_until = 0.0

    async def close(self) -> None:
        await self.interrupt()


async def _stop(proc: asyncio.subprocess.Process | None) -> None:
    if not proc or proc.returncode is not None:
        return
    try:
        if proc.stdin:
            proc.stdin.close()
        proc.terminate()
        await asyncio.wait_for(proc.wait(), 2)
    except (ProcessLookupError, TimeoutError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def pulse_available() -> bool:
    server = os.environ.get("PULSE_SERVER", "")
    return not server.startswith("unix:") or os.path.exists(server[5:])


COMBINED = "skelly_all_speakers"


async def _pactl(*args: str) -> str:
    proc = await asyncio.create_subprocess_exec("pactl", *args, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)
    out, err = await asyncio.wait_for(proc.communicate(), 10)
    if proc.returncode:
        raise RuntimeError(err.decode(errors="replace").strip() or f"pactl {args[0]} failed")
    return out.decode()


async def output_for(sinks: list[str]) -> str | None:
    """One sink to play on: the only one, or a combined sink that plays on all of them at once.

    PipeWire's combine-sink keeps the speakers in step and resamples for each one.
    """
    sinks = list(dict.fromkeys(s for s in sinks if s))
    if not sinks:
        return None
    if len(sinks) == 1:
        return sinks[0]
    modules = await _pactl_json("list", "modules")
    for m in modules:
        if m.get("name") == "module-combine-sink" and f"sink_name={COMBINED}" in (m.get("argument") or ""):
            if f"slaves={','.join(sinks)}" in m["argument"]:
                return COMBINED
            await _pactl("unload-module", str(m["index"]))
    await _pactl("load-module", "module-combine-sink", f"sink_name={COMBINED}", f"slaves={','.join(sinks)}",
                 "sink_properties=device.description='Skelly+speakers'")
    for _ in range(20):
        if any(d["name"] == COMBINED for d in (await list_devices())["speakers"]):
            break
        await asyncio.sleep(0.25)
    return COMBINED


async def set_volume(sink: str, percent: int) -> None:
    await _pactl("set-sink-volume", sink, f"{max(0, min(150, int(percent)))}%")


async def volumes() -> dict[str, int]:
    out = {}
    for s in await _pactl_json("list", "sinks"):
        vols = [int(str(c.get("value_percent", "0")).rstrip("%") or 0) for c in (s.get("volume") or {}).values()]
        if vols:
            out[s["name"]] = max(vols)
    return out


def chime(rate: int = 16000) -> bytes:
    """A short two-note "ta-da" for testing speakers."""
    out = array.array("h")
    for freq, secs in ((660, 0.18), (880, 0.32)):
        n = int(rate * secs)
        for i in range(n):
            env = min(1.0, i / 400, (n - i) / 1600)
            out.append(int(9000 * env * math.sin(2 * math.pi * freq * i / rate)))
    return out.tobytes()
