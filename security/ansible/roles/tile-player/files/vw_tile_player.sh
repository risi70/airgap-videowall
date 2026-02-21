#!/usr/bin/env bash
set -euo pipefail

# Minimal tile player wrapper.
# In the full platform, the player receives a signed subscribe token and
# a playback URL (e.g., WebRTC, SRT, RTP) from mgmt-api/wall-controller.

URL_FILE="/opt/videowall/player/stream-url.txt"
FALLBACK_URL="${1:-}"

# Screen hardening
xset -dpms || true
xset s off || true
xset s noblank || true
unclutter -idle 0.1 -root &

while true; do
  URL=""
  if [[ -f "$URL_FILE" ]]; then
    URL="$(cat "$URL_FILE" | tr -d '\n')"
  fi
  if [[ -z "$URL" ]]; then
    URL="$FALLBACK_URL"
  fi
  if [[ -z "$URL" ]]; then
    echo "[tile-player] No URL; sleeping..." >&2
    sleep 5
    continue
  fi

  echo "[tile-player] Playing: $URL"
  mpv --no-terminal --fullscreen --keep-open=yes --really-quiet "$URL" || true
  sleep 1
done
