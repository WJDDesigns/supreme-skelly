import cv2
import numpy as np
from fastapi.testclient import TestClient

from skelly.api import create_app
from skelly.link import SimulatedLink
from skelly.scene import MAX_SIDE, Scene
from skelly.settings import Settings
from skelly.vault import Vault


def make(tmp_path):
    app = create_app(SimulatedLink(), Settings.load(tmp_path / "settings.json"), autoconnect=False,
                     vault=Vault(tmp_path / "vault.json"))
    return TestClient(app), tmp_path


def png(w=3000, h=2000) -> bytes:
    img = np.zeros((h, w, 3), np.uint8)
    img[:, :, 2] = 255
    return cv2.imencode(".png", img)[1].tobytes()


def test_scene_shrinks_photos_and_builds_context(tmp_path):
    sc = Scene(tmp_path)
    item = sc.add(png())
    jpeg = cv2.imdecode(np.frombuffer(sc.path(item["id"]).read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    assert max(jpeg.shape[:2]) == MAX_SIDE
    assert sc.context() == ""
    sc.update(item["id"], description="A glowing pumpkin path.", note="The witch cackles.")
    ctx = sc.context()
    assert "glowing pumpkin" in ctx and "witch cackles" in ctx
    sc.remove(item["id"])
    assert sc.load() == [] and not sc.path(item["id"]).exists()


def test_scene_api_without_ai_keys(tmp_path):
    c, _ = make(tmp_path)
    with c:
        r = c.post("/api/scene", files={"file": ("yard.png", png(400, 300), "image/png")})
        assert r.status_code == 200 and "warning" in r.json()
        pid = r.json()["id"]
        assert c.get(f"/api/scene/{pid}.jpg").headers["content-type"] == "image/jpeg"
        assert c.put(f"/api/scene/{pid}", json={"note": "Fog machine by the door"}).json()["note"]
        assert c.post("/api/scene", files={"file": ("x.txt", b"hello", "text/plain")}).status_code == 400
        assert c.delete(f"/api/scene/{pid}").json() == []
