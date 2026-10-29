"""Kids talking to Skelly between chats get answered, and a quiet chat isn't cut off while someone talks."""
import asyncio
import time
from types import SimpleNamespace

from skelly import conversation as c
from skelly import ears
from skelly.callouts import pardon


def test_talking_is_heard_but_a_clap_is_not():
    d = ears.SpeechDetector()
    assert not any(d.feed(0.3) for _ in range(5))  # a clap or a car door: 0.1 s
    assert not any(d.feed(0.01) for _ in range(40))  # a quiet yard
    d.reset()
    # words with gaps between them: 0.1 s on, 0.06 s off
    heard = False
    for _ in range(10):
        heard = any(d.feed(0.2) for _ in range(5)) or any(d.feed(0.01) for _ in range(3)) or heard
    assert heard


def test_ears_open_only_while_people_are_about():
    now = 10_000.0
    base = dict(enabled=True, quiet=False, chatting=False, now=now, last_person_at=None, last_chat_end=None)
    assert not ears.should_listen(**base)  # nobody seen
    assert ears.should_listen(**{**base, "last_person_at": now - 30})
    assert ears.should_listen(**{**base, "last_chat_end": now - 20})  # the kids from the last chat
    assert not ears.should_listen(**{**base, "last_chat_end": now - 1})  # his last words still playing
    assert not ears.should_listen(**{**base, "last_person_at": now - ears.RECENT_S - 1})
    for off in ({"enabled": False}, {"quiet": True}, {"chatting": True}):
        assert not ears.should_listen(**{**base, "last_person_at": now - 30, **off})
    # a false alarm from the mic rests the ears, until a camera sees someone again
    paused = {**base, "last_chat_end": now - 10, "paused_until": now - 10 + ears.UNANSWERED_PAUSE_S}
    assert not ears.should_listen(**{**paused, "last_person_at": now - 30})
    assert ears.should_listen(**{**paused, "last_person_at": now - 5})


def test_pardon_lines():
    assert pardon("skelly") and pardon("santa") and pardon("nobody-we-know")


def test_old_fifteen_second_default_is_upgraded():
    assert c.ConversationConfig.from_dict({"no_answer_s": 15}).no_answer_s == 30
    assert c.ConversationConfig.from_dict({"no_answer_s": 20}).no_answer_s == 20


def _idle_conv(voice_ago: float):
    svc = SimpleNamespace(bus=SimpleNamespace(publish=lambda *a: None))
    conv = c.Conversation(svc, SimpleNamespace(get=lambda k: None), dict, None)
    conv.state.trigger, conv.state.started_at = "someone walked up", time.time()
    conv._last_heard = time.monotonic() - 40
    conv._voice_at = time.monotonic() - voice_ago
    return conv


def test_someone_talking_keeps_an_unanswered_chat_open():
    cfg = c.ConversationConfig(no_answer_s=2, idle_timeout_s=45)
    speaker = SimpleNamespace(speaking=False)

    async def ends_within(conv, s):
        try:
            await asyncio.wait_for(conv._idle_watch(cfg, speaker), s)
            return True
        except TimeoutError:
            return False

    assert asyncio.run(ends_within(_idle_conv(voice_ago=60), 1.5))  # silence: ends
    conv = _idle_conv(voice_ago=0)
    assert not asyncio.run(ends_within(conv, 1.5))  # voices at the mic: still listening


def test_app_starts_a_chat_when_someone_talks(monkeypatch):
    from fastapi.testclient import TestClient

    from skelly import api, audio_io
    from skelly.link import SimulatedLink

    class LoudMic:
        level = 0.0

        def __init__(self, *a):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        def __aiter__(self):
            return self._frames()

        async def _frames(self):
            while True:
                await asyncio.sleep(0.001)
                self.level = 0.2
                yield bytes(640)

    monkeypatch.setattr(audio_io, "Mic", LoudMic)
    monkeypatch.setattr(api, "is_quiet", lambda *a: False)
    app = api.create_app(SimulatedLink(), autoconnect=False)
    with TestClient(app) as client:
        started = []

        async def start(**kw):
            started.append(kw)

        state = client.app.state
        monkeypatch.setattr(state.conv, "start", start)
        client.portal.call(state.svc.connect, "SIM", "Ultra Skelly v2")
        state.svc.settings.vision = {**state.svc.settings.vision, "auto_converse": True}
        state.vision.last_person_at = time.time()
        for _ in range(40):
            if started:
                break
            time.sleep(0.1)
        assert started and "talked to Skelly" in started[0]["trigger"]
        assert state.vision.sightings[-1]["outcome"].startswith("Heard someone")


def test_ears_rest_after_a_greeting_nobody_answered(monkeypatch):
    """A passer-by who walked on mustn't leave the mic open for the street's chatter."""
    from fastapi.testclient import TestClient

    from skelly import api
    from skelly.link import SimulatedLink

    monkeypatch.setattr(api, "is_quiet", lambda *a: False)
    with TestClient(api.create_app(SimulatedLink(), autoconnect=False)) as client:
        state = client.app.state
        state.conv.state.trigger, state.conv.state.answered = "someone walked up (Front camera)", False
        client.portal.call(state.conv.on_ended, [{"role": "skelly", "text": "Hi!", "ts": time.time()}])
        assert state.ears_paused_until > time.time() + 60
        state.ears_paused_until = 0.0
        state.conv.state.answered = True
        client.portal.call(state.conv.on_ended, [{"role": "skelly", "text": "Hi!", "ts": time.time()}])
        assert state.ears_paused_until == 0.0
