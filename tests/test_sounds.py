import asyncio
import io
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from skelly import audio, service
from skelly.api import create_app
from skelly.link import SimulatedLink
from skelly.service import SkellyService


def _wav(seconds=0.5, rate=22050, channels=2, amp=(500, 8000)) -> bytes:
    t = np.arange(int(seconds * rate)) / rate
    tone = np.sin(2 * np.pi * 440 * t)
    data = np.stack([tone * a for a in amp[:channels]], axis=1).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())
    return buf.getvalue()


def _mp3_header(mp3: bytes) -> tuple[int, int, int]:
    i = next(i for i in range(len(mp3) - 1) if mp3[i] == 0xFF and mp3[i + 1] & 0xE0 == 0xE0)
    h = int.from_bytes(mp3[i:i + 4], "big")
    version, layer = (h >> 19) & 3, (h >> 17) & 3
    bitrate = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320][(h >> 12) & 15]
    rate = [44100, 48000, 32000][(h >> 10) & 3]
    mono = ((h >> 6) & 3) == 3
    assert version == 3 and layer == 1  # MPEG-1 Layer III
    return rate, bitrate, int(mono)


def test_process_keeps_loudest_channel_resamples_and_limits():
    rate = 22050
    t = np.arange(rate) / rate
    quiet, loud = np.sin(2 * np.pi * 300 * t) * 100, np.sin(2 * np.pi * 300 * t) * 20000
    pcm = audio.process(np.stack([quiet, loud], axis=1).astype(np.float32), rate)
    wake = int(audio.SAMPLE_RATE * audio.WAKE_TONE_MS / 1000)
    assert abs(len(pcm) - (wake + audio.SAMPLE_RATE)) <= 1
    body = pcm[wake:].astype(float)
    assert np.abs(body).max() <= audio.PEAK_CEILING + 1
    assert np.sqrt(np.mean(body ** 2)) > 10000  # loud channel kept and normalised up
    assert np.abs(pcm[:wake]).max() <= audio.WAKE_TONE_AMPLITUDE + 1


def test_process_rejects_long_and_empty():
    with pytest.raises(audio.AudioError):
        audio.process(np.zeros((0, 1), np.float32), 44100)
    with pytest.raises(audio.AudioError):
        audio.process(np.zeros((int(301 * 1000), 1), np.float32), 1000)


def test_prepare_makes_64k_mono_mp3():
    mp3, seconds = audio.prepare(_wav())
    assert _mp3_header(mp3) == (44100, 64, 1)
    assert 0.9 < seconds < 1.0  # 0.5 s of sound + 0.45 s wake tone
    with pytest.raises(audio.AudioError):
        audio.prepare(b"definitely not audio")


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(service, "CHUNK_GAP_S", 0)
    monkeypatch.setattr(service, "VERIFY_WAITS_S", (0.05,))
    monkeypatch.setattr(service, "DELETE_SETTLE_S", 0)
    monkeypatch.setattr(service, "ORDER_GAP_S", 0)


async def test_upload_then_delete(fast):
    link = SimulatedLink()
    svc = SkellyService(link, auto_reconnect=False)
    await svc.start()
    await svc.connect("SIM", "Animated Skelly")
    await asyncio.sleep(0.6)
    events = svc.bus.subscribe()
    mp3 = bytes(range(256)) * 9 + b"tail"  # 2308 bytes -> 10 chunks of 239
    result = await svc.upload_sound(mp3, "Evil Laugh")
    assert result["name"] == "Evil Laugh.mp3" and result["ready"]
    assert link.uploaded == mp3
    assert any(f["name"] == "Evil Laugh.mp3" for f in svc.state.files)
    stages = []
    while not events.empty():
        m = events.get_nowait()
        if m["type"] == "upload":
            stages.append(m["data"]["stage"])
    assert stages[0] == "starting" and stages[-1] == "done"

    with pytest.raises(ValueError):
        await svc.upload_sound(mp3, "Evil Laugh.mp3")  # duplicate name

    serial = result["serial"]
    files = await svc.delete_sound(serial)
    assert all(f["serial"] != serial for f in files)
    await svc.stop()


def test_upload_api_needs_connection_and_audio(tmp_path):
    with TestClient(create_app(SimulatedLink(), autoconnect=False)) as c:
        r = c.post("/api/sounds/upload", files={"file": ("boo.wav", _wav(), "audio/wav")})
        assert r.status_code == 409
        c.post("/api/connect", json={"address": "SIM", "name": "Animated Skelly"})
        r = c.post("/api/sounds/upload", files={"file": ("x.txt", b"nope", "text/plain")})
        assert r.status_code == 400
        r = c.post("/api/sounds/upload", files={"file": ("Big Boo!.wav", _wav(), "audio/wav")})
        assert r.status_code == 202 and r.json()["name"] == "Big_Boo.mp3"
