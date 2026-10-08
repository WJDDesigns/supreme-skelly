#!/usr/bin/env bash
# Install or update Supreme Skelly with one line:
#   curl -fsSL https://raw.githubusercontent.com/WJDDesigns/supreme-skelly/main/get.sh | bash
# Downloads the app to /opt/supreme-skelly (or $SKELLY_DIR) and runs install.sh.
# Run the same line again later to update; your settings, keys and faces are kept.
set -euo pipefail

REPO="${SKELLY_REPO:-https://github.com/WJDDesigns/supreme-skelly.git}"
DIR="${SKELLY_DIR:-/opt/supreme-skelly}"
SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"

if ! command -v git >/dev/null; then
  $SUDO apt-get update -qq && $SUDO apt-get install -y -qq git >/dev/null
fi

if [ -d "$DIR/.git" ]; then
  echo "==> Updating Supreme Skelly in $DIR"
  # Settings, keys and faces are in data/ and .env, which git leaves alone.
  git -C "$DIR" fetch --depth 1 origin main
  git -C "$DIR" checkout -q -B main FETCH_HEAD
  git -C "$DIR" reset -q --hard FETCH_HEAD
else
  echo "==> Downloading Supreme Skelly to $DIR"
  $SUDO mkdir -p "$DIR"
  $SUDO chown "$(id -u):$(id -g)" "$DIR"
  git clone --depth 1 "$REPO" "$DIR"
fi

exec "$DIR/install.sh"
