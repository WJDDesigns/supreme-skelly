import asyncio
import random

from skelly import conversation as c
from skelly.conversation import ConversationConfig


class _Speaker:
    """Talks for the first `talk_frames` mic frames, then stops."""

    def __init__(self, talk_frames):
        self.left = talk_frames

    @property
    def echoing(self):
        return self.left > 0

    speaking = echoing


class _Mic:
    def __init__(self, levels, speaker):
        self.levels, self.speaker, self.level = levels, speaker, 0.0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for lv in self.levels:
            self.level = lv
            yield b"\x01\x00" * 160
            self.speaker.left -= 1


def _frames(monkeypatch, levels, talk_frames, **cfg):
    spk = _Speaker(talk_frames)
    monkeypatch.setattr(c, "Mic", lambda *a, **k: _Mic(levels, spk))
    clock = iter(range(10**6))
    monkeypatch.setattr(c.time, "monotonic", lambda: next(clock) * 0.02)

    class Bus:
        def publish(self, *a):
            pass

    conv = c.Conversation.__new__(c.Conversation)
    conv.svc = type("S", (), {"bus": Bus()})()
    conv.state = c.ConversationState()

    async def collect():
        return [f async for f in conv._mic_frames(ConversationConfig.from_dict(cfg), 8000, spk)]

    return asyncio.run(collect())


SILENT = bytes(320)


def test_his_own_voice_never_gets_through(monkeypatch):
    random.seed(1)
    # Syllables: loud bursts and gaps, starting loud straight away, as a sentence does.
    echo = [random.choice([0.02, 0.15, 0.3, 0.45]) for _ in range(250)]
    frames = _frames(monkeypatch, echo, talk_frames=250)
    assert all(f == SILENT for f in frames)


def test_a_visitor_louder_than_him_cuts_in(monkeypatch):
    echo = [0.1, 0.2] * 50 + [0.9] * 10
    frames = _frames(monkeypatch, echo, talk_frames=110)
    assert frames[-1] != SILENT


def test_no_interruptions_keeps_the_mic_muted(monkeypatch):
    frames = _frames(monkeypatch, [0.1] * 50 + [0.9] * 20, talk_frames=70, allow_interrupt=False)
    assert all(f == SILENT for f in frames)


def test_mic_opens_when_he_has_finished(monkeypatch):
    frames = _frames(monkeypatch, [0.3] * 20 + [0.1] * 5, talk_frames=20)
    assert frames[-1] != SILENT and frames[0] == SILENT


def test_a_loud_word_after_a_pause_is_still_him(monkeypatch):
    echo = [0.4] * 40 + [0.01] * 100 + [0.45] * 30  # talks, pauses 2 s, says something loud
    frames = _frames(monkeypatch, echo, talk_frames=170)
    assert all(f == SILENT for f in frames)


def test_resume_prompt_carries_what_he_had_left_to_say():
    p = c.resume_prompt("the spiders are friendly, mostly.")
    assert p.startswith(c.RESUME_MARK) and "As I was saying" in p and "spiders are friendly" in p
