#!/usr/bin/env bash
set -euo pipefail

RELEASE="${RELEASE:-videowall}"
CHART_DIR="${CHART_DIR:-./charts/videowall}"
NS_STAGING="${NS_STAGING:-vw-staging}"
NS_PROD_CTRL="${NS_PROD_CTRL:-vw-control}"

# Ring 0: staging namespace rollout
ring0() {
  helm upgrade --install "$RELEASE" "$CHART_DIR" -f values-staging.yaml --namespace "$NS_STAGING" --create-namespace
}

# Ring 1: pilot wall layout activate (API call; adapt endpoint/mtls)
ring1() {
  local wall_id="${PILOT_WALL_ID:-wall-1}"
  local api="${MGMT_API_URL:-https://vw-mgmt-api.local}"
  local ca="${CA_CERT:-/etc/videowall/pki/ca.crt}"
  local crt="${CLIENT_CERT:-/etc/videowall/pki/operator.crt}"
  local key="${CLIENT_KEY:-/etc/videowall/pki/operator.key}"
  local layout_id="${LAYOUT_ID:-pilot-layout}"
  curl --fail --cacert "$ca" --cert "$crt" --key "$key" \
    -X POST "$api/api/v1/walls/$wall_id/layouts/$layout_id/activate"
}

# Ring 2: production rollout
ring2() {
  helm upgrade --install "$RELEASE" "$CHART_DIR" -f values-production.yaml --namespace "$NS_PROD_CTRL" --create-namespace
}

case "${1:-}" in
  ring0) ring0 ;;
  ring1) ring1 ;;
  ring2) ring2 ;;
  *) echo "Usage: $0 {ring0|ring1|ring2}" ; exit 2 ;;
esac
