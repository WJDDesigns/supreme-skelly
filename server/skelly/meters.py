"""Live mic and speaker levels for the UI, measured only while someone is looking.

The page asks for meters and keeps renewing the request; with nobody watching for a few
seconds the recorders stop. The speaker level is read from the output's PipeWire monitor,
so it shows what is actually being played (Skelly's voice, sounds, the test chime).
"""

from __future__ import annotations

import asyncio
import logging
import math
import time

from .audio_io import rms

log = logging.getLogger(__name__)

RATE = 8000
CHUNK = RATE * 2 // 10  # 100 ms of 16-bit mono


def db(level: float) -> float:
    return round(20 * math.log10(level), 1) if level > 1e-5 else -100.0


class Meters:
    def __init__(self, bus, mic_getter, sink_getter) -> None:
        self.bus = bus
        self._mic = mic_getter
        self._sink = sink_getter
        self._until = 0.0
        self._task: asyncio.Task | None = None
        self.levels = {"mic": 0.0, "speaker": 0.0}

    def watch(self, seconds: float = 12.0) -> None:
        self._until = time.monotonic() + seconds
        if not self._task or self._task.done():
            self._task = asyncio.create_task(self._run(), name="skelly-meters")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        mic = spk = None
        mic_src = spk_src = None
        try:
            while time.monotonic() < self._until:
                want_mic = self._mic() or None
                want_spk = await self._sink()
                want_spk = f"{want_spk}.monitor" if want_spk else None
                if mic is None or want_mic != mic_src or mic.returncode is not None:
                    await _close(mic)
                    mic, mic_src = await _record(want_mic), want_mic
                if want_spk != spk_src or (spk is not None and spk.returncode is not None):
                    await _close(spk)
                    spk, spk_src = (await _record(want_spk) if want_spk else None), want_spk
                for _ in range(10):  # re-check the sources once a second
                    m = await _level(mic)
                    s = await _level(spk) if spk else 0.0
                    self.levels = {"mic": m, "speaker": s}
                    self.bus.publish("meters", {"mic": round(m, 4), "speaker": round(s, 4),
                                                "mic_db": db(m), "speaker_db": db(s)})
        finally:
            await _close(mic)
            await _close(spk)
            self.levels = {"mic": 0.0, "speaker": 0.0}
            self.bus.publish("meters", {"mic": 0, "speaker": 0, "mic_db": -100, "speaker_db": -100})


async def _record(source: str | None):
    args = ["parec", "--raw", "--format=s16le", f"--rate={RATE}", "--channels=1", "--latency-msec=50",
            "--client-name=Supreme Skelly meter"]
    if source:
        args.append(f"--device={source}")
    try:
        return await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL)
    except FileNotFoundError:
        return None


async def _level(proc) -> float:
    if proc is None or proc.stdout is None:
        await asyncio.sleep(0.1)
        return 0.0
    try:
        pcm = await asyncio.wait_for(proc.stdout.readexactly(CHUNK), 0.5)
    except (asyncio.IncompleteReadError, TimeoutError):
        return 0.0
    return rms(pcm)


async def _close(proc) -> None:
    if proc is None or proc.returncode is not None:
        return
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), 2)
    except TimeoutError:
        proc.kill()
