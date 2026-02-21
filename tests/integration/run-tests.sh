#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-https://localhost:8443}"
CURL_OPTS="${CURL_OPTS:--k -sS}"

echo "[*] Waiting for mgmt-api..."
for i in {1..60}; do
  if curl $CURL_OPTS "$BASE_URL/health" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo "[*] Smoke: create wall"
curl $CURL_OPTS -X POST "$BASE_URL/api/v1/walls" \
  -H "Content-Type: application/json" \
  -d '{"wall_id":"wall-01","type":"tiles-1080p","tiles":24}' | jq .

echo "[*] Smoke: create source"
curl $CURL_OPTS -X POST "$BASE_URL/api/v1/sources" \
  -H "Content-Type: application/json" \
  -d '{"source_id":"src-01","type":"vdi","tags":["lab"]}' | jq .

echo "[*] Smoke: policy eval (allow example)"
curl $CURL_OPTS -X POST "$BASE_URL/api/v1/policy/evaluate" \
  -H "Content-Type: application/json" \
  -d '{"subject":{"roles":["operator"],"clearance_tags":["lab"]},"object":{"tags":["lab"]},"action":"subscribe"}' | jq .

echo "[*] Smoke: audit verify"
curl $CURL_OPTS "$BASE_URL/api/v1/audit/verify" | jq .

echo "[+] Integration smoke tests complete."
