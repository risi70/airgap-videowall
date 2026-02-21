#!/bin/sh
# SPDX-License-Identifier: EUPL-1.2
# ──────────────────────────────────────────────────────────────────────────────
# vw-cert-renew.sh — Certificate enrollment and rotation for Pi endpoints
#
# Two modes:
#   1. API mode: request new cert from Vault PKI via mgmt-api proxy (if network reachable)
#   2. Offline mode: import pre-generated certs from /media/usb or update bundle
#
# Called by: cron (daily) + first-boot
# Stores certs in /opt/videowall/certs/ (ca.crt, client.crt, client.key)
# Reloads dependent services on renewal.
# ──────────────────────────────────────────────────────────────────────────────
set -eu

CERT_DIR="/opt/videowall/certs"
ENV_FILE="/opt/videowall/config/player.env"
LOG_TAG="vw-cert-renew"
RENEWAL_THRESHOLD_DAYS=7

log() { echo "[$(date +%H:%M:%S)] $*" | logger -t "$LOG_TAG"; echo "[$(date +%H:%M:%S)] $*"; }

# Load config
[ -f "$ENV_FILE" ] && . "$ENV_FILE"

mkdir -p "$CERT_DIR"

# ── Check if renewal is needed ────────────────────────────────────────────
needs_renewal() {
  cert="$1"
  if [ ! -f "$cert" ]; then
    return 0  # No cert = needs enrollment
  fi

  # Check expiry
  expiry=$(openssl x509 -enddate -noout -in "$cert" 2>/dev/null | cut -d= -f2)
  if [ -z "$expiry" ]; then
    return 0  # Can't parse = needs renewal
  fi

  expiry_epoch=$(date -d "$expiry" +%s 2>/dev/null || date -D "%b %d %H:%M:%S %Y %Z" -d "$expiry" +%s 2>/dev/null || echo 0)
  now_epoch=$(date +%s)
  threshold=$((RENEWAL_THRESHOLD_DAYS * 86400))
  remaining=$((expiry_epoch - now_epoch))

  if [ "$remaining" -lt "$threshold" ]; then
    log "Certificate expires in $((remaining / 86400)) days (threshold: ${RENEWAL_THRESHOLD_DAYS}d) — renewal needed"
    return 0
  fi

  return 1  # Still valid
}

# ── Mode 1: API-based renewal via mgmt-api ────────────────────────────────
renew_via_api() {
  API_URL="${VW_MGMT_API_URL:-}"
  if [ -z "$API_URL" ]; then
    log "No mgmt-api URL configured; skipping API renewal"
    return 1
  fi

  HOSTNAME=$(hostname)

  CURL_OPTS="--connect-timeout 5 --max-time 30"
  if [ -f "${CERT_DIR}/ca.crt" ]; then
    CURL_OPTS="${CURL_OPTS} --cacert ${CERT_DIR}/ca.crt"
  fi
  # Use existing client cert for mTLS auth (if available)
  if [ -f "${CERT_DIR}/client.crt" ] && [ -f "${CERT_DIR}/client.key" ]; then
    CURL_OPTS="${CURL_OPTS} --cert ${CERT_DIR}/client.crt --key ${CERT_DIR}/client.key"
  fi

  log "Requesting certificate renewal from ${API_URL}..."

  # Generate CSR
  CSR_FILE="${CERT_DIR}/client.csr"
  KEY_FILE="${CERT_DIR}/client.key.new"

  openssl req -new -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
    -nodes -keyout "$KEY_FILE" -out "$CSR_FILE" \
    -subj "/CN=${HOSTNAME}/O=videowall-display" 2>/dev/null || {
    log "WARN: Failed to generate CSR"
    rm -f "$CSR_FILE" "$KEY_FILE"
    return 1
  }

  CSR_PEM=$(cat "$CSR_FILE")

  RESP=$(curl -sf $CURL_OPTS \
    -X POST "${API_URL}/api/v1/certs/issue" \
    -H "Content-Type: application/json" \
    -d "{\"csr\": \"$(echo "$CSR_PEM" | sed ':a;N;$!ba;s/\n/\\n/g')\", \"hostname\": \"${HOSTNAME}\", \"ttl\": \"720h\"}" \
    2>/dev/null) || {
    log "WARN: API renewal request failed (mgmt-api may not support /certs/issue)"
    rm -f "$CSR_FILE" "$KEY_FILE"
    return 1
  }

  # Extract cert from response
  NEW_CERT=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('certificate',''))" 2>/dev/null)

  if [ -z "$NEW_CERT" ]; then
    log "WARN: No certificate in API response"
    rm -f "$CSR_FILE" "$KEY_FILE"
    return 1
  fi

  # Atomic swap
  echo "$NEW_CERT" > "${CERT_DIR}/client.crt.new"
  mv "${CERT_DIR}/client.key.new" "${CERT_DIR}/client.key"
  mv "${CERT_DIR}/client.crt.new" "${CERT_DIR}/client.crt"
  rm -f "$CSR_FILE"

  chmod 640 "${CERT_DIR}/client.key"
  chown root:vw-player "${CERT_DIR}/client.key"
  chmod 644 "${CERT_DIR}/client.crt"

  log "Certificate renewed via API"
  return 0
}

# ── Mode 2: Import from USB/bundle ────────────────────────────────────────
renew_from_media() {
  for mp in /media/usb /media/usb0 /mnt/usb; do
    CERT_BUNDLE="${mp}/vw-certs"
    if [ -d "$CERT_BUNDLE" ]; then
      log "Found certificates on removable media: $CERT_BUNDLE"

      for f in ca.crt client.crt client.key; do
        if [ -f "${CERT_BUNDLE}/${f}" ]; then
          cp "${CERT_BUNDLE}/${f}" "${CERT_DIR}/${f}"
          log "  Imported: ${f}"
        fi
      done

      chmod 640 "${CERT_DIR}/client.key" 2>/dev/null || true
      chown root:vw-player "${CERT_DIR}/client.key" 2>/dev/null || true
      chmod 644 "${CERT_DIR}/client.crt" "${CERT_DIR}/ca.crt" 2>/dev/null || true

      log "Certificates imported from removable media"
      return 0
    fi
  done

  log "No certificates found on removable media"
  return 1
}

# ── Reload services ───────────────────────────────────────────────────────
reload_services() {
  log "Reloading services after cert renewal..."
  rc-service vw-player restart 2>/dev/null || true
  rc-service vw-wallagent restart 2>/dev/null || true
}

# ── Main ──────────────────────────────────────────────────────────────────
if ! needs_renewal "${CERT_DIR}/client.crt"; then
  log "Certificate still valid; no renewal needed"
  exit 0
fi

log "Certificate renewal needed"

# Try API first, then USB
if renew_via_api; then
  reload_services
  exit 0
fi

if renew_from_media; then
  reload_services
  exit 0
fi

log "WARN: Certificate renewal failed (both API and USB). Will retry on next run."
exit 1
