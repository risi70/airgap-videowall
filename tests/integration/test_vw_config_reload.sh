#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
# Integration test: vw-config hot-reload via filesystem + API
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PASS=0; FAIL=0; PID=0
TMPDIR=$(mktemp -d)
trap 'kill $PID 2>/dev/null; rm -rf "$TMPDIR"' EXIT

ok()   { echo "  ✓ PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  ✗ FAIL: $1 ($2)"; FAIL=$((FAIL + 1)); }

# Assert JSON field. Usage: json_assert "$json" "expression" "description"
json_assert() {
    local json="$1" expr="$2" desc="$3"
    if python3 -c "import json,sys; d=json.loads(sys.argv[1]); assert $expr" "$json" 2>/dev/null; then
        ok "$desc"
    else
        fail "$desc" "json=$json"
    fi
}

json_field() {
    python3 -c "import json,sys; print(json.loads(sys.argv[1])$2)" "$1" 2>/dev/null
}

PORT=18006
CFG="$TMPDIR/config.yaml"
EVENT_LOG="$TMPDIR/events.jsonl"
BASE="http://127.0.0.1:${PORT}"

echo "═══════════════════════════════════════════"
echo "  vw-config Integration Test"
echo "═══════════════════════════════════════════"

# ── Prepare ───────────────────────────────────────────────────────────────
cat > "$CFG" << 'YAML'
platform:
  version: "1.0.0"
  max_concurrent_streams: 64
walls:
  - id: wall-x
    type: tiles
    classification: unclassified
    latency_class: interactive
    grid: { rows: 3, cols: 3 }
sources:
  - id: src-x
    type: webrtc
    tags: { classification: unclassified }
YAML

CFG_B=$(cat << 'YAML'
platform:
  version: "2.0.0"
  max_concurrent_streams: 128
walls:
  - id: wall-x
    type: tiles
    classification: unclassified
    latency_class: interactive
    grid: { rows: 6, cols: 4 }
  - id: wall-y
    type: bigscreen
    classification: unclassified
    latency_class: broadcast
    screens: 2
sources:
  - id: src-x
    type: webrtc
    tags: { classification: unclassified }
  - id: src-y
    type: srt
    endpoint: "srt://10.0.0.1:9000"
    tags: { classification: unclassified }
YAML
)

# ── Start service ─────────────────────────────────────────────────────────
export VW_CONFIG_PATH="$CFG"
export VW_CONFIG_POLL_INTERVAL=999
export VW_CONFIG_EVENT_LOG="$EVENT_LOG"
export PYTHONPATH="$REPO_ROOT/services/vw-config"

cd "$REPO_ROOT"
python3 -m uvicorn app.main:app \
    --host 127.0.0.1 --port "$PORT" --log-level warning \
    --app-dir services/vw-config &
PID=$!
sleep 2

echo ""
echo "── Step 1: Health ──"
R=$(curl -sf "$BASE/healthz" || echo '{}')
json_assert "$R" "d['status']=='ok'" "healthz status=ok"
json_assert "$R" "'active_hash' in d" "healthz has active_hash"

echo ""
echo "── Step 2: Initial config ──"
V=$(curl -sf "$BASE/api/v1/config/version" || echo '{}')
H1=$(json_field "$V" "['config_hash']")
json_assert "$V" "d['version']=='1.0.0'" "version=1.0.0"
echo "  Initial hash: ${H1:0:16}..."

echo ""
echo "── Step 3: Swap config → reload ──"
echo "$CFG_B" > "$CFG"
RL=$(curl -sf -X POST "$BASE/api/v1/config/reload" || echo '{}')
json_assert "$RL" "d.get('reloaded')==True" "reload returns reloaded=true"

V2=$(curl -sf "$BASE/api/v1/config/version" || echo '{}')
H2=$(json_field "$V2" "['config_hash']")
json_assert "$V2" "d['version']=='2.0.0'" "version now 2.0.0"
if [ "$H1" != "$H2" ]; then ok "hash changed"; else fail "hash changed" "$H1==$H2"; fi
echo "  New hash: ${H2:0:16}..."

echo ""
echo "── Step 4: Invalid config → last-known-good ──"
echo "broken {{{" > "$CFG"
curl -sf -X POST "$BASE/api/v1/config/reload" >/dev/null 2>&1 || true

V3=$(curl -sf "$BASE/api/v1/config/version" || echo '{}')
H3=$(json_field "$V3" "['config_hash']")
if [ "$H2" = "$H3" ]; then ok "hash unchanged (last-known-good)"; else fail "hash unchanged" "$H2!=$H3"; fi

HLT=$(curl -sf "$BASE/healthz" || echo '{}')
json_assert "$HLT" "'last_error' in d" "healthz exposes last_error"

echo ""
echo "── Step 5: Restore valid → recovery ──"
echo "$CFG_B" > "$CFG"
curl -sf -X POST "$BASE/api/v1/config/reload" >/dev/null

V4=$(curl -sf "$BASE/api/v1/config/version" || echo '{}')
H4=$(json_field "$V4" "['config_hash']")
if [ "$H2" = "$H4" ]; then ok "hash matches after recovery"; else fail "hash matches" "$H2!=$H4"; fi

HLT2=$(curl -sf "$BASE/healthz" || echo '{}')
json_assert "$HLT2" "'last_error' not in d" "last_error cleared after recovery"

echo ""
echo "── Step 6: Dry-run ──"
DR=$(curl -sf -X POST -d "$CFG_B" "$BASE/api/v1/config/dry-run" || echo '{}')
json_assert "$DR" "d['valid']==True" "dry-run valid=true"

DR2=$(curl -s -X POST -d "platform: {version: nope}" "$BASE/api/v1/config/dry-run" || echo '{}')
json_assert "$DR2" "d['valid']==False" "dry-run invalid returns valid=false"

echo ""
echo "── Step 7: Event log ──"
if [ -f "$EVENT_LOG" ] && [ "$(wc -l < "$EVENT_LOG")" -ge 2 ]; then
    ok "event log has ≥2 entries ($(wc -l < "$EVENT_LOG"))"
else
    fail "event log entries" "file missing or <2 entries"
fi

echo ""
echo "═══════════════════════════════════════════"
echo "  Results: ${PASS} PASS, ${FAIL} FAIL"
echo "═══════════════════════════════════════════"
exit "$FAIL"
