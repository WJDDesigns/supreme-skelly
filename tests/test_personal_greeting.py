"""Greetings written about who the camera saw: delivery drivers, groups, kids, what they're doing."""

from fastapi.testclient import TestClient

import skelly.api as api
import skelly.vision as vision_mod
from skelly.link import SimulatedLink
from skelly.vision import delivery, group, opening_line, tags, who_note

DRIVER = {"people": 1, "approaching": False, "adults": 1, "kids": 0, "delivery": "UPS",
          "doing": "carrying a parcel", "line": "Ooh, a parcel! Is it a new skull for me?",
          "description": "a man in a brown uniform carrying a box"}
FAMILY = {"people": 3, "approaching": True, "adults": 2, "kids": 1, "delivery": "", "doing": "walking a dog",
          "costumes": ["witch"], "line": "Three of you and a dog! Welcome to my yard!",
          "description": "two grown-ups and a little witch with a dog"}


def test_details_from_the_look():
    assert delivery(DRIVER) == "UPS" and delivery(FAMILY) == "" and delivery({"delivery": "none"}) == ""
    assert group(FAMILY) == "2 adults and 1 kid" and group(DRIVER) == ""
    assert tags(DRIVER) == ["Delivery: UPS", "Carrying a parcel"]
    assert tags(FAMILY) == ["2 adults, 1 kid", "Walking a dog", "Costume: witch"]
    assert "delivery driver from UPS" in who_note(DRIVER)
    assert "kids" in who_note(FAMILY) and "walking a dog" in who_note(FAMILY)
    assert opening_line(FAMILY) == FAMILY["line"] and opening_line({"line": "Hi"}) == ""


def test_no_colours_in_the_hello_at_night():
    assert opening_line({"dark": True, "line": "Love the red hat, come say hi!"}) == "Love the hat, come say hi!"


def _app(monkeypatch, verdict, **vision_cfg):
    async def fake_describe(vault, jpeg, zones=None, character="skelly"):
        assert character == "skelly"
        return dict(verdict)

    monkeypatch.setattr(vision_mod, "describe", fake_describe)
    monkeypatch.setattr(api, "describe", fake_describe)
    monkeypatch.setattr(api, "is_quiet", lambda *a: False)
    return TestClient(api.create_app(SimulatedLink(), autoconnect=False)), vision_cfg


def _visit(client, vision_cfg, verdict=None):
    started = []

    async def start(**kw):
        started.append(kw)

    state = client.app.state
    state.conv.start = start
    client.portal.call(state.svc.connect, "SIM", "Ultra Skelly v2")
    state.svc.settings.vision = {**state.svc.settings.vision, "auto_converse": True, **vision_cfg}
    client.portal.call(state.vision.maybe_visitor_from, b"\xff\xd8\xff fake", [], "Front Door camera", verdict)
    return started, state.vision.sightings[-1]


def test_delivery_driver_gets_a_quick_personal_thank_you(monkeypatch):
    c, cfg = _app(monkeypatch, DRIVER)
    with c:
        started, row = _visit(c, cfg)
    # walking back to the van counts as passing by, but drivers are greeted, never called over
    assert row["outcome"] == "Delivery driver" and row["tags"][0] == "Delivery: UPS"
    assert started[0]["opening"] == DRIVER["line"]
    assert "Don't ask them questions" in started[0]["context"]


def test_delivery_drivers_can_be_left_alone(monkeypatch):
    c, cfg = _app(monkeypatch, DRIVER, deliveries="skip")
    with c:
        started, row = _visit(c, cfg)
    assert not started and row["outcome"].startswith("Ignored: delivery driver")


def test_free_protect_check_still_looks_once_before_the_hello(monkeypatch):
    c, cfg = _app(monkeypatch, FAMILY, ai_check=False)
    with c:
        started, row = _visit(c, cfg, vision_mod.protect_verdict({"type": "smartDetectLoiterZone"}))
    assert started[0]["opening"] == FAMILY["line"]
    assert "2 adults and 1 kid" in started[0]["context"] and "witch" in started[0]["context"]
