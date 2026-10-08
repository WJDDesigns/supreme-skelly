from fastapi.testclient import TestClient

from skelly.api import create_app
from skelly.link import SimulatedLink
from skelly.settings import Settings


def test_choose_adapter_saves_and_switches(tmp_path):
    link = SimulatedLink()
    settings = Settings.load(tmp_path / "settings.json")
    with TestClient(create_app(link, settings, autoconnect=False)) as c:
        radios = c.get("/api/adapters").json()
        assert [r["in_use"] for r in radios] == [True, False]

        usb = radios[1]["address"]
        radios = c.post("/api/adapter", json={"address": usb}).json()
        assert [r["in_use"] for r in radios] == [False, True]
        assert radios[1]["selected"]
        assert Settings.load(tmp_path / "settings.json").bt_adapter == usb

        assert c.post("/api/adapter", json={"address": "00:11:22:33:44:55"}).status_code == 400


def test_saved_adapter_is_used_at_startup(tmp_path):
    settings = Settings.load(tmp_path / "settings.json")
    settings.bt_adapter = "AA:AA:AA:AA:AA:02"
    link = SimulatedLink()
    with TestClient(create_app(link, settings, autoconnect=False)):
        assert link.adapter_in_use == "hci1"
