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

**Fresh mini PC, hands-free:** build a USB installer that wipes the box and sets
up Ubuntu Server, Bluetooth, Docker and Supreme Skelly with no questions asked:

```bash
cd deploy/usb && ./make-usb-image.sh     # needs xorriso (brew install xorriso)
```

Flash `supreme-skelly-installer.iso` to a USB stick (e.g. balenaEtcher), boot the
mini PC from it (F7 on Beelink), and a few minutes after it reboots open
`http://skelly.local`. Remote access: `ssh skelly@skelly.local`
(password `skelly`; change it with `passwd`).

**Already running Linux** (Ubuntu Server 24.04 or Debian 12). One command
sets everything up, including Bluetooth and Docker:

```bash
git clone https://github.com/WJDDesigns/supreme-skelly && cd supreme-skelly
./install.sh
```

Then open `http://<mini-pc-name>.local` on your phone. Switch Skelly on and
it connects by itself; after a reboot everything comes back on its own.

**Windows:** install [Python](https://www.python.org/downloads/) (tick "Add
python.exe to PATH"), then double-click `start-windows.bat`. It sets itself up the
first time and opens the UI at `http://localhost:8420`. (Docker Desktop on
Windows can't reach the PC's Bluetooth radio, so Windows runs it natively.)

**Try it without a prop:** `SKELLY_SIMULATE=1 supreme-skelly` (or set
`SKELLY_SIMULATE=1` in `docker-compose.yml`).

| Setting | Default | |
|---|---|---|
| `SKELLY_PORT` | `8420` | Web UI / API port |
| `SKELLY_EXTRA_PORTS` | (none; `80` in docker-compose) | More ports serving the same UI, e.g. 80 for a plain `http://skelly.local` |
| `SKELLY_SIMULATE` | `0` | `1` = fake device for testing |
| `SKELLY_AUTOCONNECT` | `1` | Find and connect to Skelly automatically |
| `SKELLY_DATA_DIR` | `~/.local/share/supreme-skelly` | Where settings are kept (`/data` in Docker) |
| `SKELLY_LOG_LEVEL` | `INFO` | |

## Status

| Area | State |
|---|---|
| Plug and play: one-command install, auto-connect, starts on boot | ✅ |
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
