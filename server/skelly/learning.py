"""Skelly learns from his own chats: what got visitors talking, and what fell flat.

Every chat is logged (its words, who the camera saw, and how it went: did they answer, how
long they stayed). Once a night Claude reads the chats since the last review and rewrites a
short list of tips, which every new conversation gets as background. The owner can edit,
add or delete tips on the Conversation page. Everything stays in the data folder on the mini PC.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path

import httpx

from . import usage
from .settings import data_dir

log = logging.getLogger(__name__)

KEEP_DAYS = 30  # chats older than this are dropped from the log
MAX_TIPS = 10
REVIEW_EVERY_S = 20 * 3600  # at most about once a night
REVIEW_HOUR = 3  # local hour the nightly review runs at (or later in the morning, if the box was off)
MAX_CHATS = 60  # newest chats sent to one review

REVIEW_PROMPT = """You coach {persona}, an animatronic Halloween character in a front yard who chats \
with visitors out loud. Below are its recent conversations, each with who the camera saw and how it \
went (how many times the visitor answered, how long it lasted). Long chats where visitors kept \
answering went well; chats nobody answered, or that ended after one reply, went badly. Asking a \
question the visitor had just answered, or asking the same question again, also went badly.

Current tips:
{tips}

Recent chats:
{chats}

Rewrite the tips list: keep tips that still hold, drop ones the chats show are wrong, and add new \
ones the chats teach. Each tip is one short, concrete instruction for future chats (an opener that \
worked, a question kids love, something to stop doing), at most 25 words. Never put visitors' names, \
addresses or other personal details in a tip. Tips are followed in every reply, so never write one \
that makes every reply end on a question or repeats a question. At most {max_tips} tips, best first. Reply with JSON \
only: {{"tips": ["...", "..."]}}"""


def outcome(transcript: list[dict]) -> dict:
    """How a chat went, from its transcript."""
    said = [t for t in transcript if t.get("role") in ("user", "skelly")]
    answers = sum(1 for t in said if t["role"] == "user")
    times = [t.get("ts") for t in said if t.get("ts")]
    return {"answers": answers, "turns": len(said), "seconds": round(times[-1] - times[0]) if len(times) > 1 else 0}


class Learning:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or data_dir()
        self.log_path = self.root / "chats.jsonl"
        self.tips_path = self.root / "lessons.json"

    # -- the chat log ------------------------------------------------------------------

    def log_chat(self, transcript: list[dict], trigger: str | None = None, seen: list[str] | None = None) -> dict:
        lines = [{"role": t["role"], "text": str(t.get("text", ""))[:400], "ts": t.get("ts")}
                 for t in transcript if t.get("role") in ("user", "skelly") and t.get("text")]
        row = {"ts": time.time(), "trigger": trigger or "someone pressed Start", "seen": seen or [],
               **outcome(transcript), "lines": lines}
        if not lines:
            return row
        self.root.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a") as f:
            f.write(json.dumps(row) + "\n")
        return row

    def chats(self, since: float = 0.0) -> list[dict]:
        cutoff = max(since, time.time() - KEEP_DAYS * 86400)
        out = []
        try:
            for line in self.log_path.read_text().splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("ts", 0) > cutoff:
                    out.append(row)
        except OSError:
            pass
        return out

    def prune(self) -> None:
        keep = self.chats()
        try:
            tmp = self.log_path.with_suffix(".tmp")
            tmp.write_text("".join(json.dumps(r) + "\n" for r in keep))
            tmp.replace(self.log_path)
        except OSError as exc:
            log.info("couldn't prune the chat log: %r", exc)

    # -- tips ----------------------------------------------------------------------------

    def load(self) -> dict:
        try:
            data = json.loads(self.tips_path.read_text())
        except (OSError, ValueError):
            data = {}
        return {"tips": data.get("tips", []), "last_review": data.get("last_review"),
                "reviewed_through": data.get("reviewed_through", 0.0), "error": data.get("error")}

    def _save(self, data: dict) -> dict:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.tips_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        tmp.replace(self.tips_path)
        return data

    def public(self) -> dict:
        data = self.load()
        since = data["reviewed_through"] or 0.0
        return {**data, "chats_logged": len(self.chats()), "chats_waiting": len(self.chats(since))}

    def add(self, text: str) -> dict:
        data = self.load()
        text = text.strip()[:200]
        if not text:
            raise ValueError("Write a tip first")
        if len(data["tips"]) >= MAX_TIPS * 2:
            raise ValueError("That's a lot of tips. Delete one first.")
        data["tips"].append({"id": uuid.uuid4().hex[:10], "text": text, "by": "you", "added": time.time()})
        return self._save(data)

    def update(self, tip_id: str, text: str) -> dict:
        data = self.load()
        for t in data["tips"]:
            if t["id"] == tip_id:
                t["text"], t["by"] = text.strip()[:200], "you"
                return self._save(data)
        raise KeyError(tip_id)

    def remove(self, tip_id: str) -> dict:
        data = self.load()
        data["tips"] = [t for t in data["tips"] if t["id"] != tip_id]
        return self._save(data)

    def context(self) -> str:
        """The tips, for the start of every conversation."""
        tips = [t["text"] for t in self.load()["tips"] if t.get("text")]
        if not tips:
            return ""
        return ("Tips learned from your past chats with visitors (follow them when they fit; a tip about asking "
                "a question never means asking one they've already answered): ") + " | ".join(tips)

    # -- the review ----------------------------------------------------------------------

    def due(self, hour: int, now: float | None = None) -> bool:
        """Time for the nightly review: past REVIEW_HOUR, not reviewed lately, and new chats to read."""
        data, now = self.load(), now or time.time()
        if data["last_review"] and now - data["last_review"] < REVIEW_EVERY_S:
            return False
        return REVIEW_HOUR <= hour < 12 and bool(self.chats(data["reviewed_through"] or 0.0))

    async def review(self, vault, persona: str = "Skelly, a friendly, funny talking skeleton") -> dict:
        """Read the chats since the last review and rewrite the tips. Tips the owner wrote are kept."""
        data = self.load()
        chats = self.chats(data["reviewed_through"] or 0.0)[-MAX_CHATS:]
        if not chats:
            raise ValueError("No new chats to learn from yet.")
        key = vault.get("anthropic_api_key")
        if not key:
            raise ValueError("Add an Anthropic API key in Settings > API keys first.")
        mine = [t for t in data["tips"] if t.get("by") == "you"]
        prompt = REVIEW_PROMPT.format(
            persona=persona, max_tips=MAX_TIPS,
            tips="\n".join(f"- {t['text']}" for t in data["tips"]) or "(none yet)",
            chats="\n\n".join(_chat_text(c) for c in chats))
        async with httpx.AsyncClient(timeout=90) as http:
            r = await http.post("https://api.anthropic.com/v1/messages", headers={
                "x-api-key": key, "anthropic-version": "2023-06-01"}, json={
                "model": "claude-sonnet-5-5", "max_tokens": 1200,
                "messages": [{"role": "user", "content": prompt}]})
            r.raise_for_status()
            body = r.json()
        u = body.get("usage") or {}
        usage.add("learning", model=body.get("model", ""), tokens_in=u.get("input_tokens", 0),
                  tokens_out=u.get("output_tokens", 0))
        text = "".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text")
        start, end = text.find("{"), text.rfind("}")
        new = json.loads(text[start:end + 1]).get("tips", []) if start != -1 else []
        mine_text = {t["text"].lower() for t in mine}
        learned = [{"id": uuid.uuid4().hex[:10], "text": str(t).strip()[:200], "by": "skelly", "added": time.time()}
                   for t in new if str(t).strip() and str(t).strip().lower() not in mine_text][:MAX_TIPS]
        self.prune()
        return self._save({"tips": mine + learned, "last_review": time.time(),
                           "reviewed_through": max(c["ts"] for c in chats), "error": None})

    def failed(self, error: str) -> None:
        data = self.load()
        self._save({**data, "last_review": time.time(), "error": error[:300]})


def _chat_text(chat: dict) -> str:
    head = (f"[{chat.get('trigger')}; seen: {', '.join(chat.get('seen') or []) or 'nothing noted'}; "
            f"visitor answered {chat.get('answers', 0)} times; {chat.get('seconds', 0)} s]")
    lines = [f"{'Visitor' if t['role'] == 'user' else 'Character'}: {t['text']}" for t in chat.get("lines", [])[:30]]
    return "\n".join([head, *lines])
