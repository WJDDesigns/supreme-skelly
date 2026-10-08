#!/usr/bin/env bash
# Installs the newest Supreme Skelly release (a vX.Y.Z tag on GitHub) if this box is behind.
# Run by skelly-update.timer every 15 minutes (skipped when "Update automatically" is off in
# Settings) and by skelly-update-now.service when someone taps "Update now" (--now).
set -euo pipefail
DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$DIR"
git() { command git -c safe.directory="$DIR" "$@"; }
log() { mkdir -p data; echo "$(date -Is) $*" | tee -a data/update.log; }

if [ "${1:-}" != "--now" ] && grep -q '"auto_update": false' data/settings.json 2>/dev/null; then
  exit 0
fi

LATEST="$(git ls-remote --tags --refs origin 'v*' | sed 's#.*refs/tags/##' \
  | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1 || true)"
CURRENT="v$(cat VERSION 2>/dev/null || echo 0.0.0)"
if [ -z "$LATEST" ] || [ "$LATEST" = "$CURRENT" ] \
   || [ "$(printf '%s\n%s\n' "$LATEST" "$CURRENT" | sort -V | tail -1)" != "$LATEST" ]; then
  [ "${1:-}" = "--now" ] && log "Already on the newest release ($CURRENT)"
  exit 0
fi

log "Updating $CURRENT -> $LATEST"
git fetch -q --depth 1 origin tag "$LATEST"
git checkout -q -f "$LATEST"
docker compose up -d --build >>data/update.log 2>&1
log "Now on $LATEST"
