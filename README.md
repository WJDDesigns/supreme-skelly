# Supreme Skelly

A fast, stable controller for Home Depot's Skelly-family Bluetooth animatronics
(Ultra Skelly v2, Animated Skelly, 12ft Skelly, Lethal Lily, Ultra Santa).

It runs as a small background service with a web UI, so a mini PC can sit
headless next to the prop while you control everything from a phone, tablet or
laptop browser.

## Why it's built this way

The Windows controller this replaces runs the UI, Bluetooth, Live AI audio and
camera detection in one Python process, so they fight each other and the app
stutters on small machines. Here:

- one asyncio service owns the Bluetooth link, with a single paced write queue
  and automatic reconnect;
- the UI is a static web page that talks to `/api` and a live WebSocket feed;
- heavy features (Live AI, Vision) will run as separate worker processes so
  they can never freeze controls or audio.

## Run it

**Recommended: a Linux mini PC with Docker** (Ubuntu Server 24.04 or Debian 12).

```bash
sudo apt install bluez docker.io docker-compose-v2
git clone https://github.com/WJDDesigns/supreme-skelly && cd supreme-skelly
docker compose up -d
```

Then open `http://<mini-pc-ip>:8420`.

Docker Desktop on Windows can't reach the PC's Bluetooth radio, so on Windows
run it natively instead:

```bash
pip install .
supreme-skelly
```

**Try it without a prop:** `SKELLY_SIMULATE=1 supreme-skelly` (or set
`SKELLY_SIMULATE=1` in `docker-compose.yml`).

| Setting | Default | |
|---|---|---|
| `SKELLY_PORT` | `8420` | Web UI / API port |
| `SKELLY_SIMULATE` | `0` | `1` = fake device for testing |
| `SKELLY_LOG_LEVEL` | `INFO` | |

## Status

| Area | State |
|---|---|
| Scan, connect, auto-reconnect, device info | ✅ |
| Movement, eyes, lights, volume, Live Mode switch | ✅ |
| Sound list and playback | ✅ |
| Sound upload, playlist, per-sound performances | Next |
| ElevenLabs Live AI, voice wake, voice effects | Planned |
| Vision (people, pose, gestures, OCR) | Planned |
| Relays, activity monitor, diagnostics | Planned |

The full parity checklist lives in [docs/feature-parity.md](docs/feature-parity.md).

## Develop

```bash
pip install -e ".[dev]"
pytest
ruff check .
SKELLY_SIMULATE=1 python -m skelly   # from repo root with PYTHONPATH=server, or after pip install -e
```

## Credits

Protocol knowledge builds on the community reverse engineering in
[martinecker/SkellyUltraWebController](https://github.com/martinecker/SkellyUltraWebController)
(ISC) and [martinecker/SkellyUltra](https://github.com/martinecker/SkellyUltra).
See [docs/protocol.md](docs/protocol.md).

Not affiliated with Home Depot or the makers of the original controller app.
Use at your own risk.
