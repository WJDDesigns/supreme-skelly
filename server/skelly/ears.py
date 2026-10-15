"""Skelly's ears between conversations: someone talking to him starts a chat.

A chat usually starts when a camera sees someone walk up. But kids who were already standing
there when the last chat gave up, or who walked up while Skelly was resting between chats,
could talk to him all they liked and nothing was listening. While people have been around
recently, the mic stays open between chats, and a voice that keeps going for a moment (not a
clap or a car door) starts a new one.
"""

from __future__ import annotations

from collections import deque

FRAME_S = 0.02  # the Mic class hands over 20 ms chunks
VOICE_LEVEL = 0.05  # mic level (after gain) of someone talking to him; a quiet yard reads ~0.01
VOICED_S = 0.4  # this much voice...
WINDOW_S = 1.5  # ...within this long counts as talking (words have gaps between them)
RECENT_S = 120  # listen this long after a camera last saw someone, or a chat ended
AFTER_CHAT_S = 3.0  # let the tail of his last words leave the speaker before listening
# A chat the ears started that nobody answered: stay shut until a camera sees someone again
# (as long as RECENT_S, so the end of that chat can't reopen them and loop on a noisy street).
UNANSWERED_PAUSE_S = RECENT_S


class SpeechDetector:
    """Says when the mic has heard someone talking: enough loud frames inside a short window."""

    def __init__(self, level: float = VOICE_LEVEL, voiced_s: float = VOICED_S, window_s: float = WINDOW_S) -> None:
        self.level = level
        self.need = max(1, round(voiced_s / FRAME_S))
        self.frames: deque[bool] = deque(maxlen=max(self.need, round(window_s / FRAME_S)))

    def reset(self) -> None:
        self.frames.clear()

    def feed(self, level: float) -> bool:
        self.frames.append(level >= self.level)
        return sum(self.frames) >= self.need


def should_listen(*, enabled: bool, quiet: bool, chatting: bool, now: float, last_person_at: float | None,
                  last_chat_end: float | None, paused_until: float = 0.0) -> bool:
    """Whether the ears should be open right now (all times from time.time())."""
    if not enabled or quiet or chatting:
        return False
    if now < paused_until and not (last_person_at and last_person_at > paused_until - UNANSWERED_PAUSE_S):
        return False  # resting after a false alarm, and no camera has seen anyone since
    if last_chat_end and now - last_chat_end < AFTER_CHAT_S:
        return False
    recent = [t for t in (last_person_at, last_chat_end) if t]
    return bool(recent) and now - max(recent) < RECENT_S
