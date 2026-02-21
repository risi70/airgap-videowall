#!/bin/sh
# SPDX-License-Identifier: EUPL-1.2
# ──────────────────────────────────────────────────────────────────────────────
# vw-offline-update.sh — Apply signed update bundles from removable media
#
# Designed for air-gapped Raspberry Pi endpoints. Scans USB/removable media
# for update bundles, verifies signature + checksums, and applies atomically
# with automatic rollback on failure.
#
# Usage (manual):
#   sudo vw-offline-update /media/usb/vw-update-2026-02-21.tar.zst
#
# Usage (auto-detect on USB insert):
#   Triggered by udev rule → scans /media/usb/vw-update-*.tar.zst
# ──────────────────────────────────────────────────────────────────────────────
set -eu

VW_DIR="/opt/videowall"
VW_DATA="/var/lib/videowall"
UPDATE_STAGING="${VW_DATA}/update-staging"
ROLLBACK_DIR="${VW_DATA}/rollback"
PUBKEY="${VW_DIR}/certs/update-signing.pub"
LOG_TAG="vw-update"

log()  { echo "[$(date +%H:%M:%S)] $*" | logger -t "$LOG_TAG"; echo "[$(date +%H:%M:%S)] $*"; }
die()  { log "FATAL: $*"; exit 1; }

# ── Locate bundle ─────────────────────────────────────────────────────────
BUNDLE=""
if [ -n "${1:-}" ] && [ -f "$1" ]; then
  BUNDLE="$1"
else
  # Auto-scan USB mount points
  for mp in /media/usb /media/usb0 /media/usb1 /mnt/usb; do
    if [ -d "$mp" ]; then
      found=$(find "$mp" -maxdepth 1 -name 'vw-update-*.tar.zst' -o -name 'vw-update-*.tar.gz' 2>/dev/null | sort -r | head -1)
      if [ -n "$found" ]; then
        BUNDLE="$found"
        break
      fi
    fi
  done
fi

if [ -z "$BUNDLE" ]; then
  die "No update bundle found. Provide path or insert USB with vw-update-*.tar.zst"
fi

log "Found bundle: $BUNDLE"

# ── Verify signature ──────────────────────────────────────────────────────
BUNDLE_DIR="$(dirname "$BUNDLE")"
SIG_FILE="${BUNDLE}.sig"

if [ ! -f "$SIG_FILE" ]; then
  # Try .sig alongside bundle
  SIG_FILE="${BUNDLE_DIR}/$(basename "$BUNDLE").sig"
fi

if [ -f "$SIG_FILE" ] && [ -f "$PUBKEY" ]; then
  log "Verifying Ed25519 signature..."
  if command -v openssl >/dev/null 2>&1; then
    openssl pkeyutl -verify -pubin -inkey "$PUBKEY" \
      -sigfile "$SIG_FILE" -rawin -in "$BUNDLE" 2>/dev/null || \
      die "Signature verification FAILED"
    log "Signature OK"
  else
    log "WARN: openssl not available; skipping signature verification"
  fi
elif [ -f "$PUBKEY" ]; then
  log "WARN: No .sig file found; skipping signature verification"
else
  log "WARN: No public key at ${PUBKEY}; skipping signature verification"
fi

# ── Verify checksum (SHA-256 manifest inside bundle) ──────────────────────
MANIFEST_FILE="${BUNDLE_DIR}/$(basename "$BUNDLE" | sed 's/\.tar\.\(zst\|gz\)$//').sha256"
if [ -f "$MANIFEST_FILE" ]; then
  log "Verifying SHA-256 checksum..."
  EXPECTED=$(awk '{print $1}' "$MANIFEST_FILE")
  ACTUAL=$(sha256sum "$BUNDLE" | awk '{print $1}')
  if [ "$EXPECTED" != "$ACTUAL" ]; then
    die "Checksum mismatch: expected=$EXPECTED actual=$ACTUAL"
  fi
  log "Checksum OK"
fi

# ── Stage update ──────────────────────────────────────────────────────────
log "Staging update..."
rm -rf "$UPDATE_STAGING"
mkdir -p "$UPDATE_STAGING"

case "$BUNDLE" in
  *.tar.zst)
    if command -v zstd >/dev/null 2>&1; then
      zstd -d "$BUNDLE" --stdout | tar xf - -C "$UPDATE_STAGING"
    else
      die "zstd not available; cannot decompress bundle"
    fi
    ;;
  *.tar.gz)
    tar xzf "$BUNDLE" -C "$UPDATE_STAGING"
    ;;
  *)
    die "Unknown bundle format: $BUNDLE"
    ;;
esac

# ── Validate staged content ──────────────────────────────────────────────
if [ ! -f "${UPDATE_STAGING}/manifest.json" ]; then
  die "Bundle missing manifest.json"
fi

# Verify per-file checksums from manifest
log "Verifying file checksums in bundle..."
python3 -c "
import json, hashlib, os, sys
staging = '${UPDATE_STAGING}'
with open(os.path.join(staging, 'manifest.json')) as f:
    manifest = json.load(f)
errors = 0
for entry in manifest.get('files', []):
    path = os.path.join(staging, entry['path'])
    if not os.path.exists(path):
        print(f'  MISSING: {entry[\"path\"]}', file=sys.stderr)
        errors += 1
        continue
    with open(path, 'rb') as fh:
        actual = hashlib.sha256(fh.read()).hexdigest()
    if actual != entry.get('sha256', ''):
        print(f'  CORRUPT: {entry[\"path\"]} expected={entry[\"sha256\"]} got={actual}', file=sys.stderr)
        errors += 1
sys.exit(errors)
" || die "Bundle content verification failed"
log "All files verified"

# ── Create rollback snapshot ──────────────────────────────────────────────
log "Creating rollback snapshot..."
mkdir -p "$ROLLBACK_DIR"
ROLLBACK_TS="$(date +%Y%m%d-%H%M%S)"
ROLLBACK_TAR="${ROLLBACK_DIR}/rollback-${ROLLBACK_TS}.tar.gz"

# Snapshot current agents + config
tar czf "$ROLLBACK_TAR" \
  -C / \
  opt/videowall/agents \
  opt/videowall/config \
  etc/videowall \
  2>/dev/null || log "WARN: partial rollback snapshot"

log "Rollback saved: $ROLLBACK_TAR"

# ── Apply update ──────────────────────────────────────────────────────────
log "Applying update..."
APPLY_FAILED=0

# Copy agents
if [ -d "${UPDATE_STAGING}/agents" ]; then
  cp -a "${UPDATE_STAGING}/agents/"* "${VW_DIR}/agents/" || APPLY_FAILED=1
fi

# Copy config
if [ -d "${UPDATE_STAGING}/config" ]; then
  cp -a "${UPDATE_STAGING}/config/"* "${VW_DIR}/config/" || APPLY_FAILED=1
fi

# Copy certs (if included in bundle)
if [ -d "${UPDATE_STAGING}/certs" ]; then
  cp -a "${UPDATE_STAGING}/certs/"* "${VW_DIR}/certs/" || APPLY_FAILED=1
fi

# Run post-update hook if present
if [ -x "${UPDATE_STAGING}/post-update.sh" ]; then
  log "Running post-update hook..."
  "${UPDATE_STAGING}/post-update.sh" || APPLY_FAILED=1
fi

# ── Rollback on failure ──────────────────────────────────────────────────
if [ "$APPLY_FAILED" -ne 0 ]; then
  log "ERROR: Update failed, rolling back..."
  tar xzf "$ROLLBACK_TAR" -C /
  die "Update rolled back to previous state"
fi

# ── Restart services ──────────────────────────────────────────────────────
log "Restarting services..."
rc-service vw-player restart 2>/dev/null || true
rc-service vw-wallagent restart 2>/dev/null || true

# ── Cleanup ───────────────────────────────────────────────────────────────
rm -rf "$UPDATE_STAGING"

# Keep only last 3 rollback snapshots
ls -t "${ROLLBACK_DIR}"/rollback-*.tar.gz 2>/dev/null | tail -n +4 | xargs rm -f 2>/dev/null || true

log "Update applied successfully from: $(basename "$BUNDLE")"
log "Rollback available at: $ROLLBACK_TAR"
