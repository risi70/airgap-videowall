#!/bin/sh
# SPDX-License-Identifier: EUPL-1.2
# ──────────────────────────────────────────────────────────────────────────────
# vw-smoketest.sh — On-device validation for Pi videowall endpoints
#
# Run after first boot to verify the image is correctly configured.
# Returns exit code 0 if all checks pass, non-zero otherwise.
#
# Usage: sudo /opt/videowall/bin/vw-smoketest.sh
# ──────────────────────────────────────────────────────────────────────────────
set -eu

PASS=0
FAIL=0
WARN=0

check() {
  desc="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "  ✓ PASS: $desc"
    PASS=$((PASS + 1))
  else
    echo "  ✗ FAIL: $desc"
    FAIL=$((FAIL + 1))
  fi
}

warn_check() {
  desc="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "  ✓ PASS: $desc"
    PASS=$((PASS + 1))
  else
    echo "  ⚠ WARN: $desc"
    WARN=$((WARN + 1))
  fi
}

echo "═══════════════════════════════════════════"
echo "  Videowall Pi Endpoint Smoke Test"
echo "  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "═══════════════════════════════════════════"

echo ""
echo "── A) Platform / Architecture ──"
check "Architecture is aarch64" test "$(uname -m)" = "aarch64"
check "Kernel running" test -f /proc/version
check "/boot/config.txt exists" test -f /boot/config.txt

echo ""
echo "── B) Video Pipeline / KMS ──"
check "KMS overlay enabled (vc4-kms-v3d)" grep -q "vc4-kms-v3d" /boot/config.txt
check "gpu_mem >= 256" sh -c "grep -q 'gpu_mem=256' /boot/config.txt || grep -q 'gpu_mem=512' /boot/config.txt"
check "DRM device exists" test -e /dev/dri/card0 -o -e /dev/dri/card1
check "mpv installed" command -v mpv
check "gst-launch-1.0 installed" command -v gst-launch-1.0
check "GStreamer v4l2 plugin" gst-inspect-1.0 v4l2dec
warn_check "GStreamer webrtcbin plugin" gst-inspect-1.0 webrtcbin
warn_check "GStreamer srt plugin" gst-inspect-1.0 srtsrc
check "ffprobe installed" command -v ffprobe

echo ""
echo "── C) Services ──"
check "vw-player service exists" test -f /etc/init.d/vw-player
check "vw-player enabled" rc-update show default | grep -q vw-player
check "vw-wallagent service exists" test -f /etc/init.d/vw-wallagent
check "vw-wallagent enabled" rc-update show default | grep -q vw-wallagent
check "vw-watchdog service exists" test -f /etc/init.d/vw-watchdog
check "chrony running" rc-service chronyd status
check "NetworkManager running" rc-service NetworkManager status
check "Player env file exists" test -f /opt/videowall/config/player.env
check "vw-player user exists" id vw-player

echo ""
echo "── D) Security / Hardening ──"
check "Root password locked" grep -q 'root:!' /etc/shadow -o grep -q 'root:*' /etc/shadow
check "SSH: PasswordAuth disabled" grep -q "PasswordAuthentication no" /etc/ssh/sshd_config.d/50-videowall.conf
check "SSH: PermitRootLogin no" grep -q "PermitRootLogin no" /etc/ssh/sshd_config.d/50-videowall.conf
check "Bluetooth disabled" grep -q "disable-bt" /boot/config.txt
warn_check "Wi-Fi kernel module unloaded" sh -c "! lsmod | grep -q brcmfmac"
check "Firewall rules loaded" iptables -L INPUT -n | grep -q DROP
check "sysctl: ip_forward=0" sysctl -n net.ipv4.ip_forward | grep -q 0
check "sysctl: rp_filter=1" sysctl -n net.ipv4.conf.all.rp_filter | grep -q 1
check "Journald config exists" test -f /etc/conf.d/vw-journald

echo ""
echo "── E) Offline Updates & Certs ──"
check "vw-offline-update.sh exists" test -x /opt/videowall/bin/vw-offline-update.sh
check "vw-cert-renew.sh exists" test -x /opt/videowall/bin/vw-cert-renew.sh
check "CA cert present" test -f /opt/videowall/certs/ca.crt
warn_check "Client cert present" test -f /opt/videowall/certs/client.crt
warn_check "Client key present" test -f /opt/videowall/certs/client.key
check "Cert renew cron exists" test -f /etc/periodic/daily/vw-cert-renew
check "Data partition writable" touch /var/lib/videowall/.writetest && rm /var/lib/videowall/.writetest

echo ""
echo "── F) Build Metadata ──"
warn_check "Build manifest exists" test -f /opt/videowall/.build-metadata.json
if [ -f /opt/videowall/.build-metadata.json ]; then
  echo "  Build info: $(cat /opt/videowall/.build-metadata.json | python3 -c 'import sys,json; d=json.load(sys.stdin); print(f"tile={d.get(\"tile_id\",\"?\")} built={d.get(\"build_date\",\"?\")}")' 2>/dev/null || echo "  (unreadable)")"
fi

echo ""
echo "═══════════════════════════════════════════"
echo "  Results: ${PASS} PASS, ${FAIL} FAIL, ${WARN} WARN"
echo "═══════════════════════════════════════════"

if [ "$FAIL" -gt 0 ]; then
  echo "  STATUS: FAILED — $FAIL critical checks failed"
  exit 1
else
  echo "  STATUS: PASSED"
  exit 0
fi
