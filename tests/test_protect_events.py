"""Protect events only cost an AI look for people, once per event, and not too often per camera."""
import asyncio
from types import SimpleNamespace

from skelly import protect as p


def _bridge():
    cfg = SimpleNamespace(protect_cameras=["cam1", "cam2"])
    seen = []

    async def on_person(name, jpeg, ev):
        seen.append(ev.get("id"))

    b = p.Protect(SimpleNamespace(bus=SimpleNamespace(publish=lambda *a: None)), None, lambda: cfg, on_person)
    b._log = lambda msg: None

    async def snap(cam):
        return b"jpeg"

    b.snapshot = snap
    b.snapshot_state = lambda: {}
    return b, seen


def test_only_people_once_per_event_and_spaced(monkeypatch):
    b, seen = _bridge()
    clock = [1000.0]
    monkeypatch.setattr(p.time, "monotonic", lambda: clock[0])

    def ev(i, cam="cam1", types=("person",)):
        return {"item": {"id": i, "type": "smartDetectZone", "device": cam, "smartDetectTypes": list(types)}}

    async def go():
        await b._handle(ev("car", types=("vehicle",)))  # a parked car: no AI look
        await b._handle(ev("a"))
        clock[0] += 3
        await b._handle(ev("c"))  # same walk, new event, too soon
        await b._handle(ev("b", cam="cam2"))  # another camera is fine
        clock[0] += 20
        await b._handle(ev("a"))  # a late update to an event already looked at
        await b._handle(ev("d"))

    asyncio.run(go())
    assert seen == ["a", "b", "d"]


def test_welcome_back_by_name():
    from skelly.callouts import greeting

    assert "Morticia" in greeting("Morticia")


def test_protect_verdict_is_free_and_sensible():
    from skelly.vision import protect_verdict, worth_a_visit

    walk = protect_verdict({"type": "smartDetectZone", "smartDetectTypes": ["person"]})
    assert walk["people"] == 1 and walk["approaching"] is False and walk["notice"] == ""
    assert protect_verdict({"type": "smartDetectLoiterZone", "smartDetectTypes": ["person"]})["approaching"]
    assert protect_verdict({"type": "smartDetectZone", "smartDetectTypes": ["person"]}, faces=2)["approaching"]
    assert worth_a_visit(walk, [])


def test_keep_awake_starts_and_stops_silence(monkeypatch):
    from skelly import audio_io

    started = []

    class Proc:
        returncode = None
        stdin = None

        def terminate(self):
            self.returncode = 0

        async def wait(self):
            return 0

    async def fake_exec(*args, **kw):
        started.append(next(a for a in args if a.startswith("--device")))
        return Proc()

    monkeypatch.setattr(audio_io.asyncio, "create_subprocess_exec", fake_exec)
    k = audio_io.KeepAwake()

    async def go():
        await k.sync({"bluez_output.AA"})
        await k.sync({"bluez_output.AA"})  # already running: nothing new
        assert k.sinks == {"bluez_output.AA"}
        await k.sync(set())
        assert k.sinks == set()

    asyncio.run(go())
    assert started == ["--device=bluez_output.AA"]
