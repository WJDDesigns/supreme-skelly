#!/usr/bin/env bash
# Install or update Supreme Skelly with one line:
#   curl -fsSL https://raw.githubusercontent.com/WJDDesigns/supreme-skelly-public/main/get.sh | bash
# Downloads the app to /opt/supreme-skelly (or $SKELLY_DIR) and runs install.sh.
# Run the same line again later to update; your settings, keys and faces are kept.
set -euo pipefail

REPO="${SKELLY_REPO:-https://github.com/WJDDesigns/supreme-skelly-public.git}"
DIR="${SKELLY_DIR:-/opt/supreme-skelly}"
SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"

if ! command -v git >/dev/null; then
  $SUDO apt-get update -qq && $SUDO apt-get install -y -qq git >/dev/null
fi

if [ -d "$DIR/.git" ]; then
  echo "==> Updating Supreme Skelly in $DIR"
  git -C "$DIR" pull --ff-only
else
  echo "==> Downloading Supreme Skelly to $DIR"
  $SUDO mkdir -p "$DIR"
  $SUDO chown "$(id -u):$(id -g)" "$DIR"
  git clone --depth 1 "$REPO" "$DIR"
fi

exec "$DIR/install.sh"
