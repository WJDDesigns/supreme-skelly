#!/usr/bin/env bash
# One-command setup for a Linux mini PC (Ubuntu/Debian).
#   ./install.sh
# Installs Bluetooth + Docker if needed, then starts Supreme Skelly so it
# runs on every boot and connects to your animatronic by itself.
set -euo pipefail

cd "$(dirname "$0")"
SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"

say() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }

if [ "$(uname -s)" != "Linux" ]; then
  echo "This installer is for Linux. On Windows, run: pip install . && supreme-skelly" >&2
  exit 1
fi

if command -v apt-get >/dev/null; then
  say "Installing Bluetooth and network discovery"
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq bluez avahi-daemon rfkill curl >/dev/null
else
  echo "Couldn't find apt-get. Install bluez and Docker yourself, then run: docker compose up -d --build" >&2
  exit 1
fi

if ! command -v docker >/dev/null; then
  say "Installing Docker"
  curl -fsSL https://get.docker.com | $SUDO sh
fi

say "Turning on Bluetooth"
$SUDO rfkill unblock bluetooth || true
$SUDO systemctl enable --now bluetooth avahi-daemon docker >/dev/null

say "Starting Supreme Skelly"
mkdir -p data
$SUDO docker compose up -d --build

HOST="$(hostname).local"
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
say "Done. Open one of these on your phone or computer:"
echo "    http://${HOST}"
[ -n "$IP" ] && echo "    http://${IP}"
echo
echo "Switch Skelly on and it will connect by itself. It starts again automatically after a reboot."
