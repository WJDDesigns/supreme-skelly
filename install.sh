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
  $SUDO apt-get install -y -qq bluez avahi-daemon rfkill curl \
    pipewire pipewire-pulse wireplumber libspa-0.2-bluetooth pulseaudio-utils >/dev/null
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

say "Setting up sound (mic and Skelly's speaker)"
# PipeWire runs in the login user's session. Linger starts that session at boot, and the
# WirePlumber setting keeps Bluetooth audio on with nobody logged in (headless mini PC).
AUDIO_USER="${SUDO_USER:-$(id -un)}"
[ "$AUDIO_USER" = "root" ] && AUDIO_USER="$(id -nu 1000 2>/dev/null || echo root)"
AUDIO_HOME="$(getent passwd "$AUDIO_USER" | cut -d: -f6)"
$SUDO loginctl enable-linger "$AUDIO_USER"
$SUDO install -d -o "$AUDIO_USER" "$AUDIO_HOME/.config/wireplumber/wireplumber.conf.d" \
  "$AUDIO_HOME/.config/wireplumber/bluetooth.lua.d"
$SUDO install -m 644 -o "$AUDIO_USER" deploy/audio/80-skelly-no-seat.conf \
  "$AUDIO_HOME/.config/wireplumber/wireplumber.conf.d/"
$SUDO install -m 644 -o "$AUDIO_USER" deploy/audio/80-skelly-no-seat.lua \
  "$AUDIO_HOME/.config/wireplumber/bluetooth.lua.d/"
AUDIO_UID="$(id -u "$AUDIO_USER")"
$SUDO systemctl restart "user@${AUDIO_UID}.service" || true
# docker-compose.yml reads this, so the app finds this user's sound session.
if [ -f .env ] && grep -q '^SKELLY_AUDIO_UID=' .env; then
  sed -i "s/^SKELLY_AUDIO_UID=.*/SKELLY_AUDIO_UID=${AUDIO_UID}/" .env
else
  echo "SKELLY_AUDIO_UID=${AUDIO_UID}" >> .env
fi

say "Starting Supreme Skelly"
mkdir -p data
$SUDO chmod 700 data || true  # API keys, faces and recordings live here
$SUDO docker compose up -d --build

HOST="$(hostname).local"
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
say "Done. Open one of these on your phone or computer:"
echo "    http://${HOST}"
[ -n "$IP" ] && echo "    http://${IP}"
echo
echo "The first time, it asks you to pick a password for the page."
echo "Switch Skelly on and it will connect by itself. It starts again automatically after a reboot."
