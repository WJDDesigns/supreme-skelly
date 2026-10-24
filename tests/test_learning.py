"""Skelly learns from his chats: a log of every chat, a nightly review, tips in every conversation."""


import httpx
from fastapi.testclient import TestClient

import skelly.api as api
from skelly.learning import Learning, outcome
from skelly.link import SimulatedLink
from skelly.vault import Vault

CHAT = [{"role": "note", "text": "Started because: someone walked up", "ts": 1},
        {"role": "skelly", "text": "A witch AND her dog? Hi all!", "ts": 100},
        {"role": "user", "text": "Hi Skelly! I'm Morticia", "ts": 104},
        {"role": "skelly", "text": "Want to hear a riddle?", "ts": 106},
        {"role": "user", "text": "Yes!", "ts": 130}]


def test_outcome_counts_answers_and_time():
    assert outcome(CHAT) == {"answers": 2, "turns": 4, "seconds": 30}


def test_log_and_context(tmp_path):
    lr = Learning(tmp_path)
    row = lr.log_chat(CHAT, "someone walked up", ["2 adults, 1 kid"])
    assert row["answers"] == 2 and len(row["lines"]) == 4 and lr.chats()[0]["seen"] == ["2 adults, 1 kid"]
    assert lr.log_chat([{"role": "note", "text": "x"}])["lines"] == [] and len(lr.chats()) == 1
    assert lr.context() == ""
    lr.add("Ask kids what they're dressed as")
    assert "dressed as" in lr.context()
    tip = lr.load()["tips"][0]
    lr.update(tip["id"], "Ask kids for a riddle")
    assert "riddle" in lr.context()
    lr.remove(tip["id"])
    assert lr.context() == ""


def test_nightly_review_keeps_owner_tips(tmp_path, monkeypatch):
    lr = Learning(tmp_path)
    lr.log_chat(CHAT, "someone walked up")
    lr.add("Never mention the neighbours")
    assert lr.due(hour=4) and not lr.due(hour=1) and not lr.due(hour=20)
    sent = {}

    async def fake_post(self, url, headers=None, json=None):
        sent["prompt"] = json["messages"][0]["content"]
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "model": "claude-sonnet-5-5", "usage": {"input_tokens": 10, "output_tokens": 5},
            "content": [{"type": "text",
                         "text": '{"tips": ["Riddles keep kids talking", "Never mention the neighbours"]}'}]})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    vault = Vault(tmp_path / "vault.json")
    vault.set("anthropic_api_key", "sk-test")
    import asyncio

    data = asyncio.run(lr.review(vault))
    assert [(t["text"], t["by"]) for t in data["tips"]] == [
        ("Never mention the neighbours", "you"), ("Riddles keep kids talking", "skelly")]
    assert "riddle" in sent["prompt"] and "answered 2 times" in sent["prompt"]
    assert not lr.due(hour=4)  # reviewed just now, and nothing new since
    assert lr.public()["chats_waiting"] == 0


def test_chats_are_logged_and_tips_reach_the_conversation():
    with TestClient(api.create_app(SimulatedLink(), autoconnect=False)) as c:
        state = c.app.state
        state.chat_seen = ["Delivery: UPS"]
        c.portal.call(state.conv.on_ended, CHAT)
        r = c.get("/api/learning").json()
        assert r["chats_logged"] == 1 and r["chats_waiting"] == 1
        assert state.learning.chats()[0]["seen"] == ["Delivery: UPS"] and state.chat_seen == []
        r = c.post("/api/learning/tips", json={"text": "Ask what they're dressed as"}).json()
        assert r["tips"][0]["by"] == "you"
        assert "dressed as" in state.conv.scene_context()
        assert c.post("/api/learning/review").status_code == 400  # no API key yet
