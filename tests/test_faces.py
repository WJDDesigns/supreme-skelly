from skelly.faces import FaceMemory, Seen, heard_name


def test_heard_name():
    assert heard_name("Hi Skelly, my name is sarah") == "Sarah"
    assert heard_name("I'm Jo Smith and this is my dog") == "Jo Smith"
    assert heard_name("Hey! I'm Marcus.") == "Marcus"
    assert heard_name("call me Bones") == "Bones"
    assert heard_name("I'm going to get candy") is None
    assert heard_name("i'm scared") is None
    assert heard_name("I am Hungry") is None
    assert heard_name("nice costume") is None


def test_memory_matches_and_forgets(tmp_path):
    mem = FaceMemory(tmp_path / "faces.json")
    a = [1.0] + [0.0] * 127
    b = [0.0, 1.0] + [0.0] * 126
    mem.remember("Sarah", Seen(box=[0, 0, 0.1, 0.1], embedding=a, thumb=""))
    assert mem.match(a)[0]["name"] == "Sarah"
    assert mem.match(b)[0] is None
    assert FaceMemory(tmp_path / "faces.json").public()[0]["name"] == "Sarah"  # saved
    assert (tmp_path / "faces.json").stat().st_mode & 0o777 == 0o600
    mem.forget(None)
    assert FaceMemory(tmp_path / "faces.json").people == []


def test_ignore_chips():
    from skelly.vision import worth_a_visit

    default = ["vehicles", "weather", "passers"]
    assert worth_a_visit({"people": 1, "approaching": True}, default)
    assert not worth_a_visit({"people": 1, "approaching": False}, default)
    assert worth_a_visit({"people": 1, "approaching": False}, ["vehicles"])
    assert not worth_a_visit({"vehicles": 1}, default)
    assert worth_a_visit({"vehicles": 1}, [])
    assert worth_a_visit({"animals": 1}, default)
    assert not worth_a_visit({"animals": 1}, ["animals"])
    assert not worth_a_visit({}, default)  # nothing identifiable: shadows/weather


def test_costume_names():
    from skelly.vision import costume_names

    assert costume_names({"costumes": ["Vampire", "witch", "vampire", ""]}) == ["Vampire", "witch"]
    assert costume_names({"costumes": [{"costume": "Spider-Man"}]}) == ["Spider-Man"]
    assert costume_names({}) == [] and costume_names(None) == []


def test_greeting_skelly_is_not_a_name():
    from skelly.faces import heard_name

    assert heard_name("Hey, it's Skelly.") is None
    assert heard_name("Hi Skelly, my name is Morticia.") == "Morticia"


def test_call_out_lines_vary_and_use_costumes():
    from skelly.callouts import call_out

    lines = {call_out([]) for _ in range(60)}
    assert len(lines) > 5
    assert any("vampire" in call_out(["Vampire"]) for _ in range(20))
