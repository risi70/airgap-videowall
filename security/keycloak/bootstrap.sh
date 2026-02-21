#!/usr/bin/env bash
set -euo pipefail

# Bootstrap Keycloak realm for Videowall.
# Supports:
#  - kcadm.sh (preferred) if present in container/host
#  - curl-based fallback (admin REST)
#
# Usage:
#   ./bootstrap.sh \
#     --url http://keycloak.vw-control.svc:8080 \
#     --admin-user admin --admin-pass admin \
#     --realm-file videowall-realm.json

URL=""
ADMIN_USER=""
ADMIN_PASS=""
REALM_FILE="videowall-realm.json"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url) URL="$2"; shift 2;;
    --admin-user) ADMIN_USER="$2"; shift 2;;
    --admin-pass) ADMIN_PASS="$2"; shift 2;;
    --realm-file) REALM_FILE="$2"; shift 2;;
    -h|--help)
      sed -n '1,120p' "$0"; exit 0;;
    *) echo "Unknown arg: $1" >&2; exit 2;;
  esac
done

if [[ -z "$URL" || -z "$ADMIN_USER" || -z "$ADMIN_PASS" ]]; then
  echo "Missing required args. Use --help." >&2
  exit 2
fi

if [[ ! -f "$REALM_FILE" ]]; then
  echo "Realm file not found: $REALM_FILE" >&2
  exit 2
fi

echo "[*] Waiting for Keycloak at $URL ..."
for i in {1..60}; do
  if curl -fsS "$URL/health/ready" >/dev/null 2>&1 || curl -fsS "$URL/realms/master" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

if command -v kcadm.sh >/dev/null 2>&1; then
  echo "[*] Using kcadm.sh"
  kcadm.sh config credentials --server "$URL" --realm master --user "$ADMIN_USER" --password "$ADMIN_PASS"
  # Import realm; if exists, update via partial import.
  if kcadm.sh get realms/videowall >/dev/null 2>&1; then
    echo "[*] Realm exists; performing partial import (overwrite)."
    kcadm.sh create realms/videowall/partialImport -f "$REALM_FILE" -s ifResourceExists=OVERWRITE -s action=OVERWRITE
  else
    echo "[*] Creating realm from export."
    kcadm.sh create realms -f "$REALM_FILE"
  fi
  echo "[+] Keycloak bootstrap complete."
  exit 0
fi

echo "[*] kcadm.sh not found; using curl fallback"
TOKEN=$(curl -fsS \
  -d "username=${ADMIN_USER}" \
  -d "password=${ADMIN_PASS}" \
  -d "grant_type=password" \
  -d "client_id=admin-cli" \
  "$URL/realms/master/protocol/openid-connect/token" | python3 -c 'import sys, json; print(json.load(sys.stdin)["access_token"])')

# Create or update realm.
if curl -fsS -H "Authorization: Bearer $TOKEN" "$URL/admin/realms/videowall" >/dev/null 2>&1; then
  echo "[*] Realm exists; deleting and recreating (lab-safe)."
  curl -fsS -X DELETE -H "Authorization: Bearer $TOKEN" "$URL/admin/realms/videowall" >/dev/null
fi

curl -fsS -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --data-binary "@${REALM_FILE}" \
  "$URL/admin/realms" >/dev/null

echo "[+] Keycloak bootstrap complete (curl)."
