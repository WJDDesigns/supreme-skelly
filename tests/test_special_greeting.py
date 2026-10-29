"""Special greetings for people Skelly knows, and teaching him a face from a photo."""

import random
import time

from fastapi.testclient import TestClient

import skelly.api as api
from skelly.faces import FaceMemory, Seen, special_greeting
from skelly.link import SimulatedLink

A = [1.0] + [0.0] * 127
B = [0.0, 1.0] + [0.0] * 126


def test_special_greeting_lines():
    person = {"name": "Gomez Addams", "greeting": "Well hello, {name}!\n\n  Neighbor {name}, welcome!  \n"}
    lines = {special_greeting(person) for _ in range(40)}
    assert lines == {"Well hello, Gomez!", "Neighbor Gomez, welcome!"}
    assert special_greeting({"name": "Gomez", "greeting": "  "}) == ""
    assert special_greeting({"name": "Gomez"}) == ""


def test_update_and_public(tmp_path):
    mem = FaceMemory(tmp_path / "faces.json")
    p = mem.remember("Gomez Addams", Seen(box=[0, 0, 0.1, 0.1], embedding=A, thumb=""), visit=False)
    assert p["visits"] == 0 and p["last_seen"] is None  # a photo isn't a visit
    mem.update(p["id"], {"greeting": "Hi {name}!" + "x" * 900, "about": "Lives next door", "fog": True, "name": ""})
    row = FaceMemory(tmp_path / "faces.json").public()[0]
    assert row["name"] == "Gomez Addams"  # an empty name doesn't wipe it
    assert row["about"] == "Lives next door" and row["fog"] and len(row["greeting"]) == 400


def test_first_name_joins_the_saved_person_only_if_the_face_fits(tmp_path):
    mem = FaceMemory(tmp_path / "faces.json")
    gomez = mem.remember("Gomez Addams", Seen(box=[0, 0, 0.1, 0.1], embedding=A, thumb=""), visit=False)
    near = [0.6, 0.8] + [0.0] * 126  # a new angle: like him, not a copy
    same = mem.remember("Gomez", Seen(box=[0, 0, 0.1, 0.1], embedding=near, thumb=""))
    assert same is gomez and len(gomez["embeddings"]) == 2
    other = mem.remember("Gomez", Seen(box=[0, 0, 0.1, 0.1], embedding=[0.0, 0.0, 1.0] + [0.0] * 125, thumb=""))
    assert other is not gomez and other["name"] == "Gomez"


def _app(monkeypatch):
    monkeypatch.setattr(api, "is_quiet", lambda *a: False)
    return TestClient(api.create_app(SimulatedLink(), autoconnect=False))


def test_known_neighbor_gets_their_special_greeting_and_fog(monkeypatch, tmp_path):
    random.seed(1)
    c = _app(monkeypatch)
    with c:
        state = c.app.state
        started, fogged = [], []

        async def start(**kw):
            started.append(kw)

        async def auto(why, top_up=False):
            fogged.append(why)
            return True

        state.conv.start = start
        state.fog.auto = auto
        c.portal.call(state.svc.connect, "SIM", "Ultra Skelly v2")
        state.svc.settings.vision = {**state.svc.settings.vision, "auto_converse": True}
        person = {"id": "p1", "name": "Gomez Addams", "visits": 2, "embeddings": [A],
                  "greeting": "The legend himself, {name}!", "about": "Lives next door", "fog": True}
        c.portal.call(state.vision._on_known, person)
        assert started[0]["opening"] == "The legend himself, Gomez!"
        assert "Lives next door" in started[0]["context"]
        for _ in range(50):  # the fog is started alongside the hello, not before it
            if fogged:
                break
            time.sleep(0.02)
        assert fogged == ["Gomez Addams arrived"]

        # without one he gets the usual welcome back, by first name
        started.clear()
        c.portal.call(state.vision._on_known, {"id": "p2", "name": "Morticia Addams", "visits": 1, "embeddings": [B]})
        assert "Morticia" in started[0]["opening"] and "Addams" not in started[0]["opening"]


def test_add_a_face_from_a_photo(monkeypatch):
    c = _app(monkeypatch)
    with c:
        v = c.app.state.vision
        monkeypatch.setattr(v.engine, "available", lambda: True)
        faces = [Seen(box=[0, 0, 0.05, 0.05], embedding=B, thumb="small"),
                 Seen(box=[0.3, 0.3, 0.4, 0.4], embedding=A, thumb="big")]
        monkeypatch.setattr(v.engine, "process", lambda jpeg, ignore: faces)
        v.memory.people = []
        v.memory.path = v.memory.path.parent / "faces-test.json"
        r = c.post("/api/faces/photo", data={"name": "Gomez Addams"},
                   files=[("file", ("me.jpg", b"\xff\xd8\xff fake", "image/jpeg"))])
        assert r.status_code == 200, r.text
        row = r.json()[0]
        assert row["name"] == "Gomez Addams" and row["thumb"] == "big" and row["visits"] == 0
        assert v.memory.match(A)[0]["name"] == "Gomez Addams"

        r = c.put(f"/api/faces/{row['id']}", json={"greeting": "Hey {name}!", "fog": True})
        assert r.json()[0]["greeting"] == "Hey {name}!" and r.json()[0]["fog"]

        assert c.post("/api/faces/photo", data={"name": "X"},
                      files=[("file", ("a.txt", b"hello", "text/plain"))]).status_code == 400
        monkeypatch.setattr(v.engine, "process", lambda jpeg, ignore: [])
        assert c.post("/api/faces/photo", data={"name": "X"},
                      files=[("file", ("me.jpg", b"\xff\xd8\xff fake", "image/jpeg"))]).status_code == 422
