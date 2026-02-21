#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# vw-build-wall-images.sh — Generate SD card images for every tile in a wall
#
# Reads a YAML manifest describing the wall layout and generates one .img
# per tile (or big-screen), each with the correct tile-id, IP, hostname,
# certificates, and SFU/room configuration baked in.
#
# Usage:
#   sudo ./vw-build-wall-images.sh --manifest wall-1.yaml --output-dir ./images/
#
# Manifest format (wall-1.yaml):
#
#   wall_id: 1
#   wall_name: "ops-room"
#   grid: { rows: 6, cols: 4 }
#   pi_model: 4
#   hwdec: v4l2m2m
#   image_size: 2G
#
#   mgmt_api_url: "https://vw-mgmt-api.videowall.svc:8000"
#   sfu_url: "https://janus.videowall.svc:8088"
#   room_id: 1234
#
#   network:
#     vlan_id: 30
#     gateway: "10.30.1.1"
#     dns: "10.30.1.1"
#     ntp: "10.30.1.1"
#     subnet: "10.30.1"     # tiles get .10, .11, .12, ... (base + index)
#     base_offset: 10
#
#   certs:
#     ca_cert: "/path/to/ca.crt"
#     client_cert: "/path/to/client.crt"
#     client_key: "/path/to/client.key"
#
#   ssh_pubkey: "~/.ssh/id_ed25519.pub"
#   safe_slate: "/path/to/slate.png"
#
#   # Optional: override specific tiles
#   overrides:
#     "tile-0-3":
#       stream_url: "srt://compositor:9000"
#       player_mode: big
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_SCRIPT="${SCRIPT_DIR}/vw-build-pi-image.sh"

MANIFEST=""
OUTPUT_DIR="./images"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --manifest)    MANIFEST="$2"; shift 2 ;;
    --output-dir)  OUTPUT_DIR="$2"; shift 2 ;;
    --help|-h)
      echo "Usage: sudo $0 --manifest wall.yaml --output-dir ./images/"
      exit 0
      ;;
    *) echo "Unknown: $1"; exit 1 ;;
  esac
done

if [[ -z "$MANIFEST" || ! -f "$MANIFEST" ]]; then
  echo "ERROR: --manifest is required and must exist"
  exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: must run as root"
  exit 1
fi

if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 required for YAML parsing"
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

# Parse YAML with Python
read_yaml() {
  python3 -c "
import yaml, sys, json
with open('$MANIFEST') as f:
    d = yaml.safe_load(f)
print(json.dumps(d))
"
}

CONF="$(read_yaml)"

# Extract fields
val()  { echo "$CONF" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('$1','$2'))"; }
nval() { echo "$CONF" | python3 -c "import sys,json; d=json.load(sys.stdin); v=d; [v:=v.get(k,{}) for k in '$1'.split('.')]; print(v if not isinstance(v,dict) else '$2')"; }

WALL_ID="$(val wall_id 1)"
WALL_NAME="$(val wall_name wall)"
ROWS="$(echo "$CONF" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('grid',{}).get('rows',2))")"
COLS="$(echo "$CONF" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('grid',{}).get('cols',2))")"
PI_MODEL="$(val pi_model 4)"
HWDEC="$(val hwdec v4l2m2m)"
IMAGE_SIZE="$(val image_size 2G)"
MGMT_URL="$(val mgmt_api_url https://vw-mgmt-api:8000)"
SFU_URL="$(val sfu_url https://janus:8088)"
ROOM_ID="$(val room_id 1234)"

VLAN_ID="$(echo "$CONF" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('network',{}).get('vlan_id',''))")"
GW="$(echo "$CONF" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('network',{}).get('gateway',''))")"
DNS="$(echo "$CONF" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('network',{}).get('dns',''))")"
NTP="$(echo "$CONF" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('network',{}).get('ntp',''))")"
SUBNET="$(echo "$CONF" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('network',{}).get('subnet','10.30.1'))")"
BASE_OFFSET="$(echo "$CONF" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('network',{}).get('base_offset',10))")"

CA="$(echo "$CONF" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('certs',{}).get('ca_cert',''))")"
CERT="$(echo "$CONF" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('certs',{}).get('client_cert',''))")"
KEY="$(echo "$CONF" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('certs',{}).get('client_key',''))")"
SSH_KEY="$(val ssh_pubkey '')"
SLATE="$(val safe_slate '')"

TOTAL=$((ROWS * COLS))
echo "═══════════════════════════════════════════════════════════"
echo "  Building ${TOTAL} images for wall '${WALL_NAME}' (${ROWS}×${COLS})"
echo "  Pi model: ${PI_MODEL}  |  Output: ${OUTPUT_DIR}/"
echo "═══════════════════════════════════════════════════════════"

IDX=0
for row in $(seq 0 $((ROWS - 1))); do
  for col in $(seq 0 $((COLS - 1))); do
    TILE_ID="tile-${row}-${col}"
    IP_LAST=$((BASE_OFFSET + IDX))
    IP="${SUBNET}.${IP_LAST}/24"
    HNAME="vw-${WALL_NAME}-${TILE_ID}"
    IMG="${OUTPUT_DIR}/${HNAME}.img"

    # Check for per-tile overrides
    TILE_PLAYER_MODE="tile"
    TILE_STREAM_URL=""
    OVERRIDE="$(echo "$CONF" | python3 -c "
import sys, json
d = json.load(sys.stdin)
o = d.get('overrides', {}).get('${TILE_ID}', {})
print(json.dumps(o))
" 2>/dev/null)"

    if [[ "$OVERRIDE" != "{}" ]]; then
      TILE_PLAYER_MODE="$(echo "$OVERRIDE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('player_mode','tile'))")"
      TILE_STREAM_URL="$(echo "$OVERRIDE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('stream_url',''))")"
    fi

    echo ""
    echo "── [${IDX}/${TOTAL}] ${TILE_ID} → ${HNAME} (${IP}) ──"

    EXTRA_ARGS=""
    [[ -n "$TILE_STREAM_URL" ]] && EXTRA_ARGS="$EXTRA_ARGS --stream-url $TILE_STREAM_URL"
    [[ -n "$CA" ]]   && EXTRA_ARGS="$EXTRA_ARGS --ca-cert $CA"
    [[ -n "$CERT" ]] && EXTRA_ARGS="$EXTRA_ARGS --client-cert $CERT"
    [[ -n "$KEY" ]]  && EXTRA_ARGS="$EXTRA_ARGS --client-key $KEY"
    [[ -n "$SSH_KEY" ]] && EXTRA_ARGS="$EXTRA_ARGS --ssh-pubkey $SSH_KEY"
    [[ -n "$SLATE" ]]   && EXTRA_ARGS="$EXTRA_ARGS --safe-slate $SLATE"
    [[ -n "$VLAN_ID" ]] && EXTRA_ARGS="$EXTRA_ARGS --vlan-id $VLAN_ID"
    [[ -n "$NTP" ]]     && EXTRA_ARGS="$EXTRA_ARGS --ntp $NTP"

    "$BUILD_SCRIPT" \
      --tile-id "$TILE_ID" \
      --wall-id "$WALL_ID" \
      --mgmt-api-url "$MGMT_URL" \
      --sfu-url "$SFU_URL" \
      --room-id "$ROOM_ID" \
      --hostname "$HNAME" \
      --ip "$IP" \
      --gateway "$GW" \
      --dns "$DNS" \
      --output "$IMG" \
      --image-size "$IMAGE_SIZE" \
      --player-mode "$TILE_PLAYER_MODE" \
      --hwdec "$HWDEC" \
      --display "0" \
      --pi-model "$PI_MODEL" \
      $EXTRA_ARGS

    IDX=$((IDX + 1))
  done
done

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  All ${TOTAL} images built in ${OUTPUT_DIR}/"
echo ""
ls -lh "${OUTPUT_DIR}"/*.img
echo ""
echo "  Write to SD cards:"
echo "    for img in ${OUTPUT_DIR}/*.img; do"
echo "      sudo dd if=\$img of=/dev/sdX bs=4M status=progress conv=fsync"
echo "    done"
echo "═══════════════════════════════════════════════════════════"
