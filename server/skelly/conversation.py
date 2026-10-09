"""Live AI conversations through Skelly: the USB mic in, Skelly's Live speaker out.

Three interchangeable brains, picked on the Conversation page:

* ElevenLabs Conversational AI: one WebSocket does listening, thinking and the voice.
* OpenAI Realtime: the same idea with OpenAI's speech-to-speech model.
* Claude: a pipeline. The mic is cut into utterances, transcribed (ElevenLabs or OpenAI),
  answered by Claude with streaming text, and each sentence is spoken (ElevenLabs or
  OpenAI voices) while the next one is still being written.

While Skelly talks his head, arms and torso move at random, and by default the mic is
ignored so he doesn't hear himself and interrupt his own sentences.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import random
import re
import time
import wave
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass, field

import httpx

from . import clock, usage
from .audio_io import ECHO_DELAY_S, FRAME_MS, Mic, Speaker, rms

log = logging.getLogger(__name__)

PROVIDERS = ("elevenlabs", "openai", "claude")

DEFAULT_PROMPT = (
    "You are Skelly, a friendly, funny six-foot Halloween skeleton standing in the front yard. "
    "Visitors talk to you out loud. Keep every reply short: one or two spoken sentences. "
    "Be spooky but family friendly, make bone puns sparingly, and ask visitors questions back."
)


# How much Skelly says per reply, from the "Talk amount" slider (1..5).
TALK_RULES = {
    1: "HARD LIMIT: answer in at most 12 words. One short sentence, then stop and let them talk.",
    2: "HARD LIMIT: answer in at most 20 words. One or two short sentences, then stop and let them talk.",
    3: "HARD LIMIT: answer in at most 35 words, then let them talk.",
    4: "Keep answers under 60 words.",
    5: "Be as chatty and theatrical as you like.",
}
# Token caps for the agent / model: a backstop a bit above the word limit so sentences can finish.
MAX_TOKENS = {1: 45, 2: 70, 3: 110, 4: 170, 5: 400}


def talk_rule(amount: int) -> str:
    return TALK_RULES.get(max(1, min(5, int(amount or 2))), TALK_RULES[2])


# Added to every Claude prompt: whatever the personality says, the reply is read aloud.
SPOKEN_RULES = (
    "Your words are spoken aloud by a speaker inside a skeleton, to someone standing in front of you. "
    "Never write stage directions, actions in asterisks, emoji, lists or formatting: only the words you say."
)


def speakable(text: str) -> str:
    """Strip what shouldn't be read out: *actions*, (asides), markdown and emoji."""
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)  # **bold** keeps its words
    text = re.sub(r"\*[^*]{1,200}\*", " ", text).replace("*", " ")  # *rattles* is an action
    text = re.sub(r"[_#`>~]|\[|\]", " ", text)
    text = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class ConversationConfig:
    provider: str = "elevenlabs"
    prompt: str = DEFAULT_PROMPT
    first_message: str = "Well hello there! Come closer, I don't bite... much."
    vary_greeting: bool = True  # open each chat with a different line instead of first_message
    # ElevenLabs Conversational AI
    elevenlabs_agent_id: str = ""
    # OpenAI Realtime
    openai_model: str = "gpt-realtime"
    openai_voice: str = "ash"
    # Claude pipeline
    claude_model: str = "claude-haiku-4-5-20251001"
    stt: str = "elevenlabs"  # elevenlabs | openai
    tts: str = "elevenlabs"  # elevenlabs | openai
    elevenlabs_voice_id: str = "JBFqnCBsd6RMkjVDRZzb"  # "George"; any voice from your library works
    openai_tts_voice: str = "onyx"
    # Behaviour
    move_while_talking: bool = True
    ignore_mic_while_talking: bool = True  # always on now: his own voice is never sent to the AI
    allow_interrupt: bool = True  # a visitor speaking clearly over Skelly cuts him off
    interrupt_sensitivity: int = 50  # 0..100: how easily speech counts as interrupting
    talk_amount: int = 2  # 1..5: how much he says per reply
    # Quiet hours: no visitor-triggered conversations between these local times (the Start button still works)
    quiet_hours: bool = True
    quiet_from: str = "22:00"
    quiet_to: str = "16:00"
    idle_timeout_s: int = 45  # end the conversation after this long with nobody talking
    # A chat Skelly started on his own (someone seen on camera) ends this long after he stops
    # talking if nobody has answered yet: whoever set it off walked on, or it was a false alarm.
    no_answer_s: int = 15
    max_minutes: int = 5  # hard stop, so a stuck or self-talking chat can't burn credits all night
    record: bool = False  # save each conversation as a video with sound
    keep_days: int = 30  # delete recordings older than this
    mic: str = ""  # PipeWire source; empty = default
    mic_gain: int = 100  # percent, from Settings > Sound
    out_gain: int = 100  # percent cap on his voice, from Settings > Sound
    speaker: str = ""  # PipeWire sink; empty = Skelly's Live speaker when paired, else default

    @classmethod
    def from_dict(cls, d: dict | None) -> ConversationConfig:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (d or {}).items() if k in known})


@dataclass
class ConversationState:
    state: str = "idle"  # idle | connecting | listening | thinking | speaking | error
    provider: str | None = None
    level: float = 0.0
    error: str | None = None
    started_at: float | None = None
    transcript: list[dict] = field(default_factory=list)
    trigger: str | None = None  # why it started, e.g. "Front Door camera saw someone walk up"
    answered: bool = False  # a visitor has said something real


# Interrupting Skelly: the mic must beat his own echo, after a moment to learn how loud it is.
INTERRUPT_MIN = 0.03
INTERRUPT_FRAMES = 8  # 160 ms of a louder voice, so a clatter or an echo blip doesn't count
ECHO_LEARN_S = 0.6  # each time he starts talking, listen to his echo before allowing interruptions
ECHO_HOLD_S = 0.4  # his echo level halves this fast once he goes quieter


# Cut off by a noise rather than a person: after this long with nothing said, he carries on.
RESUME_AFTER_S = 3.0
RESUME_MARK = "(Nobody said anything"


def call_over_prompt(line: str) -> str:
    """Hidden nudge: someone is walking past while nobody is talking to Skelly; he calls out."""
    return (f"{RESUME_MARK} to you; someone new is walking past at a distance.) Call out to them to come "
            f"over and chat, in your own words, something like: \"{line}\" One short line only.")


def resume_prompt(unsaid: str) -> str:
    return (f"{RESUME_MARK}; that was just a noise.) Carry on where you were cut off. Start with a quick "
            "\"As I was saying...\" or a playful variation, then finish your point in your own words. "
            f"What you hadn't said yet: \"{unsaid[:400]}\"")


def _minutes(hhmm: str) -> int:
    h, _, m = (hhmm or "0:0").partition(":")
    return (int(h) % 24) * 60 + int(m or 0) % 60


def is_quiet(cfg: ConversationConfig, now=None) -> bool:
    """Whether it's inside quiet hours (local time at Skelly's house), a window that may cross midnight."""
    if not cfg.quiet_hours:
        return False
    now = now or clock.now()
    t, start, end = now.hour * 60 + now.minute, _minutes(cfg.quiet_from), _minutes(cfg.quiet_to)
    if start == end:
        return False
    return start <= t < end if start < end else t >= start or t < end


def season_note(now=None) -> str:
    """What time of year it is, so Skelly doesn't treat every neighbour out walking as a trick-or-treater."""
    now = now or clock.now()
    day = f"Today is {now:%A, %B} {now.day}."
    if now.month == 10 and now.day == 31:
        return f"{day} It's Halloween night: expect trick-or-treaters in costumes."
    halloween = now.replace(year=now.year + (now.month > 10), month=10, day=31).date()
    days = (halloween - now.date()).days
    return (f"{day} Halloween is {days} days away, so the people passing by are neighbours out for a walk, "
            "a jog or with their dog, not trick-or-treaters. Don't ask about candy, trick-or-treating or "
            "costumes unless they're actually dressed up; compliment something you can see instead, like "
            "their shirt, their dog or what they're doing.")


# What speech-to-text often "hears" in wind, traffic or a quiet yard.
NOT_SPEECH = {"you", "thank you", "thanks", "thank you for watching", "bye", "uh", "um", "hmm", "mm",
              "oh", "ah", "huh", "okay", "ok"}


def said_something(text: str) -> bool:
    """Whether a visitor transcript is real talk rather than noise the speech-to-text made words of."""
    words = re.sub(r"[^\w' ]+", " ", text.lower()).split()
    return bool(words) and " ".join(words) not in NOT_SPEECH and sum(len(w) for w in words) >= 2


class MissingKey(ValueError):
    pass


class OutOfCredits(RuntimeError):
    """ElevenLabs refused because this month's credits are used up."""


OUT_OF_CREDITS = ("ElevenLabs is out of credits, so Skelly can't talk or hear through it until they renew "
                  "or you top up. Add an OpenAI key in Settings > API keys and he switches to OpenAI's "
                  "cheaper voice and hearing by himself meanwhile.")
QUOTA_WORDS = ("quota", "credits", "insufficient")
CREDITS_RETRY_S = 3600  # after running out, try ElevenLabs again this often


def _is_quota(text: str) -> bool:
    return any(w in (text or "").lower() for w in QUOTA_WORDS)


class Conversation:
    """Runs one provider at a time and reports what's happening on the event bus."""

    def __init__(self, svc, vault, settings_getter: Callable[[], dict], sink_for_skelly):
        self.svc = svc
        self.vault = vault
        self._config = settings_getter
        self._sink_for_skelly = sink_for_skelly
        self.state = ConversationState()
        self._task: asyncio.Task | None = None
        self._last_heard = 0.0
        self.scene_context: Callable[[], str] | None = None  # background about the yard and display
        self._context: list[str] = []
        self.output_sink: str | None = None  # where his voice is playing, for the speaker meter
        self.on_user_text: Callable[[str], None] | None = None  # e.g. listening for names
        self.on_started = None  # async (cfg, sink) once his voice has somewhere to go
        self.on_ended = None  # async (transcript) when the conversation finishes
        self.snap: Callable[[], str | None] | None = None  # saves the camera picture for a line
        self._override_ok = False
        self._opening: str | None = None
        self._nudges: asyncio.Queue | None = None
        self.save_picture: Callable[[bytes], str | None] | None = None  # stores a JPEG, returns its name
        self._echo_gain = 1.0  # mic level per unit of played level; follows his real echo while he talks
        self._echo_delay = ECHO_DELAY_S  # measured live from how the mic tracks what was played
        self._eleven_out_at: float | None = None  # when ElevenLabs last said it's out of credits

    # -- public ---------------------------------------------------------------

    @property
    def running(self) -> bool:
        return bool(self._task and not self._task.done())

    def snapshot(self) -> dict:
        return asdict(self.state)

    async def start(self, context: str | None = None, opening: str | None = None, trigger: str | None = None,
                    picture: bytes | None = None) -> dict:
        """Begin a conversation. `opening` replaces the first thing he says (e.g. calling someone over).

        `trigger` says what set it off when Skelly starts on his own; such a chat ends quickly if
        nobody answers. None means someone pressed Start.
        """
        if self.running:
            if context:
                self._context.append(context)
            return self.snapshot()
        cfg = ConversationConfig.from_dict(self._config())
        if self._eleven_out_at and time.monotonic() - self._eleven_out_at < CREDITS_RETRY_S:
            cfg = self._without_elevenlabs(cfg) or cfg
        self._check_keys(cfg)
        scene = ""
        if self.scene_context:
            try:
                scene = self.scene_context()
            except Exception as exc:
                log.info("no scene context: %r", exc)
        self._context = [c for c in (season_note(), scene, context) if c]
        if not opening and cfg.vary_greeting:
            from .callouts import greeting
            opening = greeting()
        self._opening = opening
        if opening:
            cfg.first_message = opening
        self.state = ConversationState(state="connecting", provider=cfg.provider, started_at=time.time(),
                                       trigger=trigger)
        if trigger:
            log.info("conversation started on its own: %s", trigger)
            note = {"role": "note", "text": f"Started because: {trigger}", "ts": time.time()}
            try:  # the picture that set it off, which may be from a different camera than the preview
                if picture and self.save_picture and (name := self.save_picture(picture)):
                    note["snap"] = name
            except Exception:
                log.exception("Trigger picture failed")
            self.state.transcript.append(note)
        self._publish()
        self._task = asyncio.create_task(self._run(cfg), name="skelly-conversation")
        return self.snapshot()

    async def stop(self) -> dict:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        if self.state.state != "error":
            self.state.state = "idle"
        self.state.level = 0.0
        self._publish()
        return self.snapshot()

    @property
    def quiet_for(self) -> float:
        """Seconds since anyone spoke to Skelly (or since he finished talking)."""
        return time.monotonic() - self._last_heard

    def call_over(self, line: str) -> bool:
        """Make Skelly call out to a passer-by now, if the AI supports being nudged mid-chat.

        Only when nobody has talked to him in this conversation: never in the middle of a chat.
        """
        if not self.running or self._nudges is None:
            return False
        if any(t.get("role") == "user" for t in self.state.transcript):
            return False
        self._nudges.put_nowait(call_over_prompt(line))
        return True

    def add_context(self, text: str) -> None:
        """Something worth knowing mid-conversation (e.g. what the camera sees)."""
        self._context.append(text)

    # -- plumbing ---------------------------------------------------------------

    def _without_elevenlabs(self, cfg: ConversationConfig) -> ConversationConfig | None:
        """The same chat on OpenAI's voice and hearing (Claude still thinks), if the keys are there."""
        uses_eleven = cfg.provider == "elevenlabs" or (
            cfg.provider == "claude" and "elevenlabs" in (cfg.tts, cfg.stt))
        if not uses_eleven or not self.vault.get("openai_api_key"):
            return None
        if cfg.provider == "elevenlabs" and not self.vault.get("anthropic_api_key"):
            return None
        alt = ConversationConfig.from_dict(asdict(cfg))
        alt.provider, alt.tts, alt.stt = "claude", "openai", "openai"
        alt.elevenlabs_agent_id = ""  # no agent voice lookups against the empty account
        return alt

    def _check_keys(self, cfg: ConversationConfig) -> None:
        if cfg.provider not in PROVIDERS:
            raise ValueError(f"Unknown AI '{cfg.provider}'")
        need = {
            "elevenlabs": ["elevenlabs_api_key"] if not cfg.elevenlabs_agent_id else [],
            "openai": ["openai_api_key"],
            "claude": ["anthropic_api_key", f"{cfg.stt}_api_key", f"{cfg.tts}_api_key"],
        }[cfg.provider]
        if cfg.provider == "elevenlabs" and not cfg.elevenlabs_agent_id:
            raise MissingKey("Add your ElevenLabs agent ID on the Conversation page first.")
        missing = [n for n in dict.fromkeys(need) if not self.vault.get(n)]
        if missing:
            names = ", ".join(n.replace("_api_key", "").replace("openai", "OpenAI").replace("elevenlabs", "ElevenLabs")
                              .replace("anthropic", "Anthropic") for n in missing)
            raise MissingKey(f"Add your {names} API key in Settings > API keys first.")

    def _publish(self) -> None:
        self.svc.bus.publish("conversation", self.snapshot())

    def _set(self, state: str) -> None:
        if self.state.state != state:
            self.state.state = state
            self._publish()

    def _say(self, role: str, text: str) -> None:
        text = (text or "").strip()
        if role == "skelly":  # ElevenLabs v3 performance cues like [excited] aren't words
            text = re.sub(r"\s*\[[a-z][a-z ,'-]{0,30}\]\s*", " ", text, flags=re.I).strip()
        if not text:
            return
        entry = {"role": role, "text": text, "ts": time.time()}
        if self.snap:
            try:
                if name := self.snap():
                    entry["snap"] = name
            except Exception:  # a picture is a nice-to-have; never break the chat for it
                log.exception("Conversation picture failed")
        self.state.transcript = (self.state.transcript + [entry])[-60:]
        self.svc.bus.publish("transcript", entry)
        if role == "user" and said_something(text):  # not a cough or a mis-heard car going by
            self._last_heard = time.monotonic()
            self.state.answered = True
            if self.on_user_text:
                try:
                    self.on_user_text(text)
                except Exception as exc:
                    log.info("on_user_text failed: %r", exc)

    def _take_context(self) -> str:
        ctx, self._context = " ".join(self._context), []
        return ctx

    async def _run(self, cfg: ConversationConfig) -> None:
        speaker: Speaker | None = None
        mover: asyncio.Task | None = None
        self._last_heard = time.monotonic()
        try:
            sink = cfg.speaker or await self._sink_for_skelly()
            self.output_sink = sink
            if self.on_started:
                try:
                    await self.on_started(cfg, sink)
                except Exception as exc:
                    log.warning("conversation start hook failed: %r", exc)
            speaker = Speaker(sink, cfg.out_gain / 100)
            mover = asyncio.create_task(self._body(cfg, speaker))
            runner = {"elevenlabs": self._elevenlabs, "openai": self._openai, "claude": self._claude}[cfg.provider]
            try:
                await runner(cfg, speaker)
            except OutOfCredits:
                self._eleven_out_at = time.monotonic()
                alt = self._without_elevenlabs(cfg)
                if not alt:
                    raise
                log.warning("ElevenLabs is out of credits; carrying on with OpenAI's voice and hearing")
                self.state.provider = alt.provider
                self._last_heard = time.monotonic()
                await self._claude(alt, speaker)
            else:
                if "elevenlabs" in (cfg.provider, cfg.tts, cfg.stt):
                    self._eleven_out_at = None
        except asyncio.CancelledError:
            raise
        except OutOfCredits:
            log.warning("ElevenLabs is out of credits and there's no OpenAI key to fall back on")
            self.state.error = OUT_OF_CREDITS
            self._set("error")
        except MissingKey as exc:
            self.state.error = str(exc)
            self._set("error")
        except Exception as exc:
            log.exception("conversation failed")
            self.state.error = _friendly(exc)
            self._set("error")
        else:
            self._set("idle")
        finally:
            self._nudges = None
            if self.state.started_at:  # conversation minutes are what the voice services bill
                usage.add(f"conversation:{cfg.provider}", seconds=time.time() - self.state.started_at)
            if mover:
                mover.cancel()
            if speaker:
                await speaker.close()
            await self._still()
            if self.on_ended:
                try:
                    await self.on_ended(list(self.state.transcript))
                except Exception as exc:
                    log.warning("conversation end hook failed: %r", exc)

    async def _idle_watch(self, cfg: ConversationConfig, speaker: Speaker) -> None:
        """Ends the conversation once nobody has spoken for a while."""
        while True:
            await asyncio.sleep(1)
            if self.state.started_at and time.time() - self.state.started_at > cfg.max_minutes * 60:
                log.info("conversation hit the %s-minute limit; ending", cfg.max_minutes)
                return
            if speaker.speaking:
                self._last_heard = time.monotonic()
            elif (self.state.trigger and not self.state.answered
                  and time.monotonic() - self._last_heard > cfg.no_answer_s):
                log.info("nobody answered within %ss (%s); ending", cfg.no_answer_s, self.state.trigger)
                return
            elif time.monotonic() - self._last_heard > cfg.idle_timeout_s:
                log.info("conversation idle for %ss; ending", cfg.idle_timeout_s)
                return

    async def _mic_frames(self, cfg: ConversationConfig, rate: int, speaker: Speaker) -> AsyncIterator[bytes]:
        """Mic chunks, with Skelly's own voice blanked out if asked, and the level published.

        While he talks (and for a moment after, while Bluetooth is still playing the tail of his
        sentence) the mic is muted. With interruptions on, it opens only for someone clearly
        louder than his own voice at the mic: his echo level is learned from the first moments
        of each sentence and followed as it rises and falls, before anything may get through.
        """
        last_pub = 0.0
        echo = 0.0  # loudest recent level of Skelly's own voice at the mic
        peak = 0.0  # loudest he's been this time he's talking
        loud = 0
        gate_until = 0.0
        talk_started = 0.0
        hearing_self = False
        hist: deque = deque(maxlen=400)  # (time, mic level) for the last 8 s, to measure the echo delay
        # 0 = only shouting gets through, 100 = talking a bit louder than him does
        factor = 3.0 - 1.6 * (max(0, min(100, cfg.interrupt_sensitivity)) / 100)
        frame_s = FRAME_MS / 1000
        decay = 0.5 ** (frame_s / ECHO_HOLD_S)
        async with Mic(rate, cfg.mic or None, cfg.mic_gain / 100) as mic:
            async for pcm in mic:
                now = time.monotonic()
                level = mic.level
                was_hearing, hearing_self = hearing_self, speaker.echoing
                if not hearing_self:
                    if was_hearing:
                        log.info("Skelly finished; mic open (his peak %.3f, mic now %.3f)", peak, level)
                    deaf, echo, peak, loud, talk_started = False, 0.0, 0.0, 0, 0.0
                elif not cfg.allow_interrupt:
                    deaf = True
                    peak = max(peak, level)
                elif hasattr(speaker, "level_at"):
                    # Predict his echo from what was played one Bluetooth delay ago, scaled by how
                    # loud it comes back. Both are measured live: the delay from how the mic's
                    # loudness tracks what was played, the loudness as an upper envelope that
                    # jumps up at once (so his echo can never be mistaken for a visitor while it
                    # catches up) and eases down slowly. Between his words the prediction drops,
                    # so a visitor talking then gets through.
                    gate_open = now < gate_until
                    talk_started = talk_started or now
                    hist.append((now, level))
                    if len(hist) % 50 == 0:
                        self._echo_delay = _estimate_delay(hist, speaker, self._echo_delay)
                    ref = speaker.level_at(now - self._echo_delay, window=0.12)
                    if ref > 0.03 and not gate_open:
                        ratio = level / ref
                        self._echo_gain = max(self._echo_gain * 0.998, min(ratio, 4.0))
                    expected = self._echo_gain * ref
                    settled = now - talk_started > ECHO_LEARN_S
                    if settled and level > max(INTERRUPT_MIN, expected * factor + 0.015):
                        loud += 1
                        if loud >= INTERRUPT_FRAMES:
                            if not gate_open:
                                log.info("mic opened over Skelly: level %.3f, expected echo %.3f "
                                         "(gain %.2f, delay %.2fs)", level, expected, self._echo_gain,
                                         self._echo_delay)
                            gate_until = now + 0.8
                            gate_open = True
                    else:
                        loud = 0
                    peak = max(peak, level)
                    deaf = not gate_open
                else:
                    talk_started = talk_started or now
                    gate_open = now < gate_until
                    settled = now - talk_started > ECHO_LEARN_S
                    # After a pause his echo estimate has fallen, but his next word will be as loud
                    # as his loudest so far: never let the bar drop far below that.
                    if settled and level > max(INTERRUPT_MIN, max(echo, peak * 0.8) * factor):
                        loud += 1
                        if loud >= INTERRUPT_FRAMES:
                            if not gate_open:
                                log.info("mic opened over Skelly: level %.3f, his echo %.3f, his peak %.3f",
                                         level, echo, peak)
                            gate_until = now + 0.8
                            gate_open = True
                    else:
                        loud = 0
                    if not gate_open and not loud:  # follow his echo: jump up with it, fall back slowly
                        echo = max(level, echo * decay)
                        peak = max(peak, level)
                    deaf = not gate_open
                self.state.level = 0.0 if deaf else level
                if now - last_pub > 0.15:
                    last_pub = now
                    self.svc.bus.publish("conversation_level", {"level": round(self.state.level, 3)})
                yield bytes(len(pcm)) if deaf else pcm

    async def _body(self, cfg: ConversationConfig, speaker: Speaker) -> None:
        """Random head/arm/torso movement while Skelly is speaking."""
        if not cfg.move_while_talking:
            return
        moving = False
        parts = [m["key"] for m in self.svc.profile.to_dict().get("movements", []) if m["key"] != "all"]
        while True:
            await asyncio.sleep(random.uniform(1.2, 3.0))
            if not self.svc.link.connected or not parts:
                continue
            try:
                if speaker.speaking:
                    pick = random.sample(parts, k=random.randint(1, len(parts)))
                    await self.svc.set_movement(pick)
                    moving = True
                elif moving:
                    await self.svc.set_movement([])
                    moving = False
            except Exception as exc:  # movement is decoration; never end the chat over it
                log.debug("movement failed: %r", exc)

    async def _still(self) -> None:
        try:
            if self.svc.link.connected:
                await self.svc.set_movement([])
        except Exception:
            pass

    # -- ElevenLabs Conversational AI ------------------------------------------

    async def _elevenlabs(self, cfg: ConversationConfig, speaker: Speaker) -> None:
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed

        key = self.vault.get("elevenlabs_api_key")
        url = f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={cfg.elevenlabs_agent_id}"
        if key:  # private agents need a signed URL; public ones work either way
            async with httpx.AsyncClient(timeout=15) as http:
                r = await http.get("https://api.elevenlabs.io/v1/convai/conversation/get-signed-url",
                                   params={"agent_id": cfg.elevenlabs_agent_id}, headers={"xi-api-key": key})
                _raise_for(r, "ElevenLabs")
                url = r.json()["signed_url"]
        self._override_ok = bool(key) and await _allow_prompt_override(key, cfg.elevenlabs_agent_id)
        if key:
            await _cap_agent_tokens(key, cfg.elevenlabs_agent_id, MAX_TOKENS.get(cfg.talk_amount, 110))
        async with connect(url, max_size=None, open_timeout=15) as ws:
            # The agent's own prompt, first message and voice apply (they're set when the agent is
            # created here, or edited in ElevenLabs). Overriding them per call is refused unless
            # the agent explicitly allows it, so nothing is overridden.
            init = {"type": "conversation_initiation_client_data"}
            rule = talk_rule(cfg.talk_amount)
            if self._override_ok and cfg.prompt:
                # The rule goes first and last: one line in the middle of a long personality
                # prompt gets ignored.
                init["conversation_config_override"] = {"agent": {"prompt": {
                    "prompt": f"{rule}\n\n{cfg.prompt}\n\n{rule}"}}}
                if self._opening:
                    init["conversation_config_override"]["agent"]["first_message"] = self._opening
            await ws.send(json.dumps(init))
            if not self._override_ok:  # second best: tell it as context
                await ws.send(json.dumps({"type": "contextual_update", "text": f"Speaking style: {rule}"}))
            in_rate = out_rate = 16000
            self._set("listening")
            last_said = ""  # what Skelly was saying, for picking up after a false interruption
            cut_off: dict = {}  # {"at": time, "text": what he didn't get to say}

            async def resume_watch():
                """Cut off but nobody followed up: carry on with "as I was saying"."""
                while True:
                    await asyncio.sleep(0.25)
                    if cut_off and time.monotonic() - cut_off["at"] > RESUME_AFTER_S:
                        text = cut_off.get("text", "")
                        cut_off.clear()
                        if not text or speaker.speaking:
                            continue
                        log.info("interrupted with nothing said; resuming")
                        await ws.send(json.dumps({"type": "user_message", "text": resume_prompt(text)}))

            self._nudges = asyncio.Queue()

            async def send_nudges():
                while True:
                    text = await self._nudges.get()
                    if not speaker.speaking:
                        log.info("nudging the agent: %s", text[:80])
                        await ws.send(json.dumps({"type": "user_message", "text": text}))

            async def send_mic():
                async for pcm in self._mic_frames(cfg, in_rate, speaker):
                    if ctx := self._take_context():
                        await ws.send(json.dumps({"type": "contextual_update", "text": ctx}))
                    await ws.send(json.dumps({"user_audio_chunk": base64.b64encode(pcm).decode()}))

            async def receive():
                nonlocal out_rate, last_said
                async for raw in ws:
                    msg = json.loads(raw)
                    kind = msg.get("type")
                    if kind == "conversation_initiation_metadata":
                        meta = msg.get("conversation_initiation_metadata_event", {})
                        out_rate = _pcm_rate(meta.get("agent_output_audio_format"), 16000)
                    elif kind == "audio":
                        pcm = base64.b64decode(msg["audio_event"]["audio_base_64"])
                        self._set("speaking")
                        await speaker.play(pcm, out_rate)
                    elif kind == "agent_response":
                        last_said = msg["agent_response_event"]["agent_response"]
                        self._say("skelly", last_said)
                        # A reminder before every next turn; the rule fades as the chat grows.
                        await ws.send(json.dumps({"type": "contextual_update", "text": f"Reminder: {rule}"}))
                    elif kind == "agent_response_correction":
                        ev = msg.get("agent_response_correction_event", {})
                        said = ev.get("corrected_agent_response") or ""
                        whole = ev.get("original_agent_response") or last_said
                        if cut_off:  # what he didn't get to say
                            cut_off["text"] = whole[len(said):].strip() if whole.startswith(said) else whole
                    elif kind == "user_transcript":
                        heard = msg["user_transcription_event"]["user_transcript"]
                        if heard.startswith(RESUME_MARK):
                            continue  # our own nudge, not the visitor
                        if len(heard.strip(" .…-")) >= 2:
                            cut_off.clear()  # they did say something; the agent answers that
                        self._say("user", heard)
                        self._set("thinking")
                    elif kind == "interruption":
                        await speaker.interrupt()
                        self._set("listening")
                        cut_off.update(at=time.monotonic(), text=cut_off.get("text") or last_said)
                    elif kind == "ping":
                        ev = msg.get("ping_event", {})
                        await ws.send(json.dumps({"type": "pong", "event_id": ev.get("event_id")}))
                    if kind != "audio" and not speaker.speaking and self.state.state == "speaking":
                        self._set("listening")

            try:
                await _first_done(send_mic(), receive(), resume_watch(), send_nudges(),
                                  self._idle_watch(cfg, speaker), self._speaking_watch(speaker))
            except ConnectionClosed as exc:
                reason = exc.rcvd.reason if exc.rcvd else ""
                if _is_quota(reason):
                    raise OutOfCredits(f"ElevenLabs: {reason}") from exc
                raise RuntimeError(f"ElevenLabs ended the chat: {reason or exc}") from exc
            if _is_quota(ws.close_reason or ""):  # a clean close can still be "you're out of credits"
                raise OutOfCredits(f"ElevenLabs: {ws.close_reason}")
            if ws.close_code not in (None, 1000):
                log.warning("ElevenLabs closed the chat (%s): %s", ws.close_code, ws.close_reason)

    # -- OpenAI Realtime --------------------------------------------------------

    async def _openai(self, cfg: ConversationConfig, speaker: Speaker) -> None:
        from websockets.asyncio.client import connect

        rate = 24000
        url = f"wss://api.openai.com/v1/realtime?model={cfg.openai_model}"
        headers = {"Authorization": f"Bearer {self.vault.get('openai_api_key')}"}
        async with connect(url, additional_headers=headers, max_size=None, open_timeout=15) as ws:
            await ws.send(json.dumps({"type": "session.update", "session": {
                "type": "realtime",
                "instructions": (f"{talk_rule(cfg.talk_amount)}\n\n{cfg.prompt}\n\n"
                                 f"{talk_rule(cfg.talk_amount)}"),
                "audio": {
                    "input": {"format": {"type": "audio/pcm", "rate": rate},
                              "turn_detection": {"type": "server_vad"},
                              "transcription": {"model": "gpt-4o-mini-transcribe"}},
                    "output": {"format": {"type": "audio/pcm", "rate": rate}, "voice": cfg.openai_voice},
                },
            }}))
            if cfg.first_message:
                await ws.send(json.dumps({"type": "response.create", "response": {
                    "instructions": f"Greet the visitor by saying: {cfg.first_message}"}}))
            self._set("listening")

            async def send_mic():
                async for pcm in self._mic_frames(cfg, rate, speaker):
                    if ctx := self._take_context():
                        await ws.send(json.dumps({"type": "conversation.item.create", "item": {
                            "type": "message", "role": "system",
                            "content": [{"type": "input_text", "text": ctx}]}}))
                    await ws.send(json.dumps({"type": "input_audio_buffer.append",
                                              "audio": base64.b64encode(pcm).decode()}))

            async def receive():
                async for raw in ws:
                    msg = json.loads(raw)
                    kind = msg.get("type", "")
                    if kind in ("response.output_audio.delta", "response.audio.delta"):
                        self._set("speaking")
                        await speaker.play(base64.b64decode(msg["delta"]), rate)
                    elif kind in ("response.output_audio_transcript.done", "response.audio_transcript.done"):
                        self._say("skelly", msg.get("transcript", ""))
                    elif kind == "conversation.item.input_audio_transcription.completed":
                        self._say("user", msg.get("transcript", ""))
                    elif kind == "input_audio_buffer.speech_started":
                        # Echo never reaches OpenAI (the mic is blanked or gated), so speech is a person.
                        if cfg.allow_interrupt or not speaker.speaking:
                            await speaker.interrupt()
                        self._last_heard = time.monotonic()
                    elif kind == "input_audio_buffer.speech_stopped":
                        self._set("thinking")
                    elif kind == "error":
                        err = msg.get("error", {})
                        raise RuntimeError(f"OpenAI: {err.get('message') or err}")

            await _first_done(send_mic(), receive(), self._idle_watch(cfg, speaker), self._speaking_watch(speaker))

    async def _speaking_watch(self, speaker: Speaker) -> None:
        """Flip back to "listening" once buffered speech has actually finished playing."""
        while True:
            await asyncio.sleep(0.1)
            if self.state.state == "speaking" and not speaker.speaking:
                self._set("listening")

    # -- Claude pipeline ------------------------------------------------------------

    async def _claude(self, cfg: ConversationConfig, speaker: Speaker) -> None:
        rate = 16000
        history: list[dict] = []
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=60)) as http:
            if cfg.first_message:
                self._set("speaking")
                self._say("skelly", cfg.first_message)
                history += [{"role": "user", "content": "(A visitor has walked up.)"},
                            {"role": "assistant", "content": cfg.first_message}]
                await self._speak(http, cfg, speaker, cfg.first_message)
            self._set("listening")

            async def turns():
                async for utterance in self._utterances(cfg, rate, speaker):
                    self._set("thinking")
                    text = await self._transcribe(http, cfg, utterance, rate)
                    if not text:
                        self._set("listening")
                        continue
                    self._say("user", text)
                    ctx = self._take_context()
                    history.append({"role": "user", "content": f"{text}\n\n(Context: {ctx})" if ctx else text})
                    reply = await self._answer(http, cfg, speaker, history)
                    history.append({"role": "assistant", "content": reply or "..."})
                    del history[:-24]
                    await speaker.wait_done()
                    self._set("listening")

            await _first_done(turns(), self._idle_watch(cfg, speaker))

    async def _utterances(self, cfg: ConversationConfig, rate: int, speaker: Speaker) -> AsyncIterator[bytes]:
        """Energy-based voice activity detection: yields one buffer per thing someone said."""
        floor, voiced, quiet = 0.004, 0, 0
        buf = bytearray()
        start_frames, end_frames = 3, 700 // FRAME_MS
        min_bytes = rate * 2 * 300 // 1000
        async for pcm in self._mic_frames(cfg, rate, speaker):
            level = rms(pcm)
            if not buf:
                floor = 0.95 * floor + 0.05 * min(level, 0.05)  # learn the background noise
            loud = level > max(0.012, floor * 3)
            if loud:
                voiced += 1
                quiet = 0
            else:
                quiet += 1
                voiced = 0 if not buf else voiced
            if buf or voiced >= start_frames:
                buf += pcm
                if quiet >= end_frames:
                    if len(buf) >= min_bytes:
                        self._last_heard = time.monotonic()
                        yield bytes(buf)
                    buf.clear()
                    voiced = quiet = 0
                elif len(buf) > rate * 2 * 20:  # 20 s monologue: hand it over anyway
                    yield bytes(buf)
                    buf.clear()

    async def _transcribe(self, http: httpx.AsyncClient, cfg: ConversationConfig, pcm: bytes, rate: int) -> str:
        wav = _wav(pcm, rate)
        if cfg.stt == "openai":
            r = await http.post("https://api.openai.com/v1/audio/transcriptions",
                                headers={"Authorization": f"Bearer {self.vault.get('openai_api_key')}"},
                                data={"model": "gpt-4o-mini-transcribe"},
                                files={"file": ("speech.wav", wav, "audio/wav")})
            _raise_for(r, "OpenAI speech-to-text")
            return r.json().get("text", "").strip()
        r = await http.post("https://api.elevenlabs.io/v1/speech-to-text",
                            headers={"xi-api-key": self.vault.get("elevenlabs_api_key")},
                            data={"model_id": "scribe_v1"}, files={"file": ("speech.wav", wav, "audio/wav")})
        _raise_for(r, "ElevenLabs speech-to-text")
        return r.json().get("text", "").strip()

    async def _answer(self, http: httpx.AsyncClient, cfg: ConversationConfig, speaker: Speaker,
                      history: list[dict]) -> str:
        """Streams Claude's reply and speaks it sentence by sentence as it arrives."""
        body = {"model": cfg.claude_model, "max_tokens": MAX_TOKENS.get(cfg.talk_amount, 200),
                "system": (f"{talk_rule(cfg.talk_amount)}\n\n{cfg.prompt}\n\n{SPOKEN_RULES} "
                           f"{talk_rule(cfg.talk_amount)}"),
                "messages": history, "stream": True}
        headers = {"x-api-key": self.vault.get("anthropic_api_key"), "anthropic-version": "2023-06-01",
                   "content-type": "application/json"}
        full, pending = "", ""
        tok_in = tok_out = 0
        speaking = asyncio.Queue()

        async def voice():
            while (sentence := await speaking.get()) is not None:
                self._set("speaking")
                await self._speak(http, cfg, speaker, sentence)

        voicer = asyncio.create_task(voice())
        try:
            async with http.stream("POST", "https://api.anthropic.com/v1/messages", json=body, headers=headers) as r:
                if r.status_code >= 400:
                    await r.aread()
                    _raise_for(r, "Claude")
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    ev = json.loads(line[5:].strip() or "{}")
                    if ev.get("type") == "message_start":
                        tok_in = (ev.get("message", {}).get("usage") or {}).get("input_tokens", 0)
                    elif ev.get("type") == "message_delta":
                        tok_out = (ev.get("usage") or {}).get("output_tokens", 0)
                    if ev.get("type") == "content_block_delta" and ev["delta"].get("type") == "text_delta":
                        chunk = ev["delta"]["text"]
                        full += chunk
                        pending += chunk
                        *done, pending = re.split(r"(?<=[.!?])\s+", pending)
                        for sentence in done:
                            await speaking.put(sentence)
            if pending.strip():
                await speaking.put(pending)
            await speaking.put(None)
            await voicer
        finally:
            voicer.cancel()
            usage.add("chat", model=cfg.claude_model, tokens_in=tok_in, tokens_out=tok_out)
        self._say("skelly", speakable(full))
        return full

    async def _agent_voice(self, cfg: ConversationConfig) -> dict:
        if not cfg.elevenlabs_agent_id:
            return {}
        cached = getattr(self, "_agent_voice_cache", None)
        if cached and cached[0] == cfg.elevenlabs_agent_id and time.monotonic() - cached[1] < 300:
            return cached[2]
        from .voices import agent_voice

        try:
            av = await agent_voice(self.vault.get("elevenlabs_api_key"), cfg.elevenlabs_agent_id)
        except Exception as exc:
            log.info("agent voice lookup failed: %r", exc)
            av = {}
        self._agent_voice_cache = (cfg.elevenlabs_agent_id, time.monotonic(), av)
        return av

    async def _speak(self, http: httpx.AsyncClient, cfg: ConversationConfig, speaker: Speaker, text: str) -> None:
        text = speakable(text)
        if not text:
            return
        if cfg.tts == "openai":
            rate = 24000
            req = http.stream("POST", "https://api.openai.com/v1/audio/speech",
                              headers={"Authorization": f"Bearer {self.vault.get('openai_api_key')}"},
                              json={"model": "gpt-4o-mini-tts", "voice": cfg.openai_tts_voice, "input": text,
                                    "response_format": "pcm", "instructions": "Speak like a playful, spooky skeleton."})
            name = "OpenAI voice"
        else:
            rate = 16000
            # With an agent picked, speak exactly like it (same voice, model and tuning).
            av = await self._agent_voice(cfg)
            body = {"text": text, "model_id": av.get("model_id") or "eleven_flash_v2_5"}
            if av.get("settings"):
                body["voice_settings"] = av["settings"]
            req = http.stream("POST", "https://api.elevenlabs.io/v1/text-to-speech/"
                              f"{av.get('voice_id') or cfg.elevenlabs_voice_id}/stream",
                              params={"output_format": "pcm_16000"},
                              headers={"xi-api-key": self.vault.get("elevenlabs_api_key")}, json=body)
            name = "ElevenLabs voice"
        async with req as r:
            if r.status_code >= 400:
                await r.aread()
                _raise_for(r, name)
            carry = b""
            async for chunk in r.aiter_bytes():
                chunk = carry + chunk
                cut = len(chunk) - len(chunk) % 2
                carry = chunk[cut:]
                await speaker.play(chunk[:cut], rate)


# -- ElevenLabs account helpers ------------------------------------------------------

_override_checked: dict[str, bool] = {}
_tokens_set: dict[str, int] = {}


async def _cap_agent_tokens(key: str, agent_id: str, tokens: int) -> None:
    """Match the agent's reply length cap to the Talk amount slider (a backstop for the prompt rule)."""
    if _tokens_set.get(agent_id) == tokens:
        return
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.patch(f"https://api.elevenlabs.io/v1/convai/agents/{agent_id}", headers={"xi-api-key": key},
                                 json={"conversation_config": {"agent": {"prompt": {"max_tokens": tokens}}}})
            _raise_for(r, "ElevenLabs")
        _tokens_set[agent_id] = tokens
    except Exception as exc:
        log.info("couldn't set the agent's reply cap: %r", exc)


async def _allow_prompt_override(key: str, agent_id: str) -> bool:
    """Let this agent take a per-conversation prompt (for the Talk amount slider). Done once."""
    if agent_id in _override_checked:
        return _override_checked[agent_id]
    ok = False
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.get(f"https://api.elevenlabs.io/v1/convai/agents/{agent_id}", headers={"xi-api-key": key})
            _raise_for(r, "ElevenLabs")
            o = ((r.json().get("platform_settings") or {}).get("overrides") or {})
            agent_o = (o.get("conversation_config_override") or {}).get("agent") or {}
            ok = bool((agent_o.get("prompt") or {}).get("prompt")) and bool(agent_o.get("first_message"))
            if not ok:  # per-conversation prompt (Talk amount) and opening line (calling people over)
                r = await http.patch(f"https://api.elevenlabs.io/v1/convai/agents/{agent_id}",
                                     headers={"xi-api-key": key}, json={"platform_settings": {"overrides": {
                                         "conversation_config_override": {"agent": {
                                             "prompt": {"prompt": True}, "first_message": True}}}}})
                _raise_for(r, "ElevenLabs")
                ok = True
    except Exception as exc:
        log.info("prompt override not available: %r", exc)
    _override_checked[agent_id] = ok
    return ok


async def elevenlabs_agents(key: str) -> list[dict]:
    """The Conversational AI agents in the account, newest first."""
    out, cursor = [], None
    async with httpx.AsyncClient(timeout=15) as http:
        for _ in range(10):
            r = await http.get("https://api.elevenlabs.io/v1/convai/agents", headers={"xi-api-key": key},
                               params={"page_size": 100, **({"cursor": cursor} if cursor else {})})
            _raise_for(r, "ElevenLabs")
            data = r.json()
            out += [{"id": a["agent_id"], "name": a.get("name") or a["agent_id"]} for a in data.get("agents", [])]
            cursor = data.get("next_cursor")
            if not data.get("has_more") or not cursor:
                break
    return out


async def elevenlabs_voices(key: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.get("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": key})
        _raise_for(r, "ElevenLabs")
    voices = [{"id": v["voice_id"], "name": v.get("name") or v["voice_id"],
               "category": v.get("category") or ""} for v in r.json().get("voices", [])]
    return sorted(voices, key=lambda v: (v["category"] == "premade", v["name"].lower()))


async def elevenlabs_create_agent(key: str, cfg: ConversationConfig) -> dict:
    """Makes a "Skelly" agent with this page's personality, first line and voice."""
    body = {"name": "Skelly", "conversation_config": {
        "agent": {"prompt": {"prompt": cfg.prompt}, "first_message": cfg.first_message, "language": "en"},
        "tts": {"voice_id": cfg.elevenlabs_voice_id}}}
    async with httpx.AsyncClient(timeout=30) as http:
        r = await http.post("https://api.elevenlabs.io/v1/convai/agents/create", headers={"xi-api-key": key},
                            json=body)
        _raise_for(r, "ElevenLabs")
    return {"id": r.json()["agent_id"], "name": "Skelly"}


async def elevenlabs_agent(key: str, agent_id: str) -> dict:
    """The agent's prompt, first message and voice, as this page uses them."""
    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.get(f"https://api.elevenlabs.io/v1/convai/agents/{agent_id}", headers={"xi-api-key": key})
        _raise_for(r, "ElevenLabs")
    conf = r.json().get("conversation_config", {})
    agent = conf.get("agent", {})
    return {"id": agent_id, "name": r.json().get("name"), "prompt": (agent.get("prompt") or {}).get("prompt", ""),
            "first_message": agent.get("first_message", ""), "voice_id": (conf.get("tts") or {}).get("voice_id")}


async def elevenlabs_update_agent(key: str, agent_id: str, *, prompt: str | None = None,
                                  first_message: str | None = None) -> None:
    """Writes the page's personality and first line back to the agent.

    Only the changed fields are sent: ElevenLabs merges them and keeps the agent's model,
    tools and knowledge base. (Echoing the whole prompt block back is refused, because it
    carries both "tools" and "tool_ids".)
    """
    agent: dict = {}
    if prompt is not None:
        agent["prompt"] = {"prompt": prompt}
    if first_message is not None:
        agent["first_message"] = first_message
    if not agent:
        return
    async with httpx.AsyncClient(timeout=20) as http:
        r = await http.patch(f"https://api.elevenlabs.io/v1/convai/agents/{agent_id}", headers={"xi-api-key": key},
                             json={"conversation_config": {"agent": agent}})
        _raise_for(r, "ElevenLabs")


# -- helpers ------------------------------------------------------------------------


def _estimate_delay(hist, speaker, current: float) -> float:
    """The lag (0.2-1.2 s) at which the mic's loudness best follows what was played."""
    pts = list(hist)[-250:]  # last 5 s
    if len(pts) < 100:
        return current
    mic = [lv for _, lv in pts]
    m_mean = sum(mic) / len(mic)
    best, best_score = current, 0.0
    for i in range(10, 61, 2):  # 0.2 .. 1.2 s in 40 ms steps
        lag = i * 0.02
        ref = [speaker.level_at(t - lag, window=0.02) for t, _ in pts]
        r_mean = sum(ref) / len(ref)
        num = sum((a - m_mean) * (b - r_mean) for a, b in zip(mic, ref, strict=True))
        den = (sum((a - m_mean) ** 2 for a in mic) * sum((b - r_mean) ** 2 for b in ref)) ** 0.5
        score = num / den if den else 0.0
        if score > best_score:
            best, best_score = lag, score
    if best_score < 0.4:  # not enough of him playing to tell; keep what we had
        return current
    return round(0.7 * current + 0.3 * best, 3)


async def _first_done(*coros) -> None:
    """Run side by side; when any one finishes (or fails), stop the rest."""
    tasks = [asyncio.ensure_future(c) for c in coros]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            t.result()
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _pcm_rate(fmt: str | None, default: int) -> int:
    m = re.match(r"pcm_(\d+)", fmt or "")
    return int(m.group(1)) if m else default


def _wav(pcm: bytes, rate: int) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()


def _raise_for(r: httpx.Response, who: str) -> None:
    if r.status_code < 400:
        return
    try:
        detail = r.json()
        detail = detail.get("detail") or detail.get("error") or detail
        if isinstance(detail, dict):
            detail = detail.get("message") or detail.get("status") or json.dumps(detail)
    except ValueError:
        detail = r.text[:200]
    if "ElevenLabs" in who and (r.status_code in (401, 402, 429) and _is_quota(str(detail))):
        raise OutOfCredits(f"{who}: {detail}")
    if r.status_code in (401, 403):
        raise RuntimeError(f"{who} rejected the API key ({r.status_code}). Check it in Settings > API keys.")
    raise RuntimeError(f"{who} error {r.status_code}: {detail}")


def _friendly(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    if isinstance(exc, OutOfCredits):
        return OUT_OF_CREDITS
    if "401" in text or "403" in text:
        return "The AI service rejected the API key. Check it in Settings > API keys."
    if isinstance(exc, (OSError, httpx.ConnectError)) and "pa" not in text:
        return f"Couldn't reach the AI service: {text}"
    return text
