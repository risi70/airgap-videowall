#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
# ──────────────────────────────────────────────────────────────────────────────
# verify.sh — Build-time verification for Pi image builder
#
# Asserts the build script and output image meet all compliance requirements.
# Run before or after vw-build-pi-image.sh to catch misconfigurations early.
#
# Usage:
#   ./verify.sh                          # Verify the build script (pre-build)
#   ./verify.sh --image /path/to.img     # Verify a built image (post-build)
# ──────────────────────────────────────────────────────────────────────────────
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_SCRIPT="${SCRIPT_DIR}/vw-build-pi-image.sh"
PASS=0; FAIL=0

check() {
  desc="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "  ✓ $desc"; PASS=$((PASS + 1))
  else
    echo "  ✗ $desc"; FAIL=$((FAIL + 1))
  fi
}

echo "═══════════════════════════════════════════"
echo "  Pi Image Builder Verification"
echo "═══════════════════════════════════════════"

# ── Pre-build: verify build script recipe ─────────────────────────────────
echo ""
echo "── Build Script Compliance ──"

check "Build script exists" test -f "$BUILD_SCRIPT"
check "Build script executable" test -x "$BUILD_SCRIPT"
check "Shell syntax valid" bash -n "$BUILD_SCRIPT"

# A) Architecture
check "Alpine ARCH=aarch64" grep -q 'ALPINE_ARCH="aarch64"' "$BUILD_SCRIPT"

# B) Video pipeline — KMS
check "config.txt: vc4-kms-v3d" grep -q 'vc4-kms-v3d' "$BUILD_SCRIPT"
check "config.txt: gpu_mem=256" grep -q 'gpu_mem=256' "$BUILD_SCRIPT"
check "mpv --gpu-context=drm" grep -q 'gpu-context=drm' "$BUILD_SCRIPT"
check "mpv --hwdec configured" grep -q 'hwdec=' "$BUILD_SCRIPT"

# B) Video pipeline — GStreamer plugins
check "Package: gstreamer" grep -q 'gstreamer ' "$BUILD_SCRIPT"
check "Package: gst-plugins-base" grep -q 'gst-plugins-base' "$BUILD_SCRIPT"
check "Package: gst-plugins-good" grep -q 'gst-plugins-good' "$BUILD_SCRIPT"
check "Package: gst-plugins-bad" grep -q 'gst-plugins-bad' "$BUILD_SCRIPT"
check "Package: mpv" grep -q '  mpv' "$BUILD_SCRIPT"
check "Package: ffmpeg" grep -q '  ffmpeg' "$BUILD_SCRIPT"
check "Package: mesa-dri" grep -q 'mesa-dri' "$BUILD_SCRIPT"

# C) Runtime model
check "OpenRC vw-player service" grep -q 'etc/init.d/vw-player' "$BUILD_SCRIPT"
check "OpenRC vw-wallagent service" grep -q 'etc/init.d/vw-wallagent' "$BUILD_SCRIPT"
check "OpenRC vw-watchdog service" grep -q 'etc/init.d/vw-watchdog' "$BUILD_SCRIPT"
check "Services enabled at boot (rc-update)" grep -q 'rc-update add vw-player' "$BUILD_SCRIPT"

# D) Security / Hardening
check "Root password locked" grep -q 'passwd -l root' "$BUILD_SCRIPT"
check "SSH: PasswordAuthentication no" grep -q 'PasswordAuthentication no' "$BUILD_SCRIPT"
check "SSH: PermitRootLogin no" grep -q 'PermitRootLogin no' "$BUILD_SCRIPT"
check "Bluetooth disabled (dtoverlay=disable-bt)" grep -q 'disable-bt' "$BUILD_SCRIPT"
check "Wi-Fi rfkill block" grep -q 'rfkill' "$BUILD_SCRIPT"
check "Firewall (iptables)" grep -q 'iptables -P INPUT DROP' "$BUILD_SCRIPT"
check "sysctl hardening" grep -q 'rp_filter = 1' "$BUILD_SCRIPT"
check "Journald log limits" grep -q 'SystemMaxUse\|journald' "$BUILD_SCRIPT"

# E) Offline updates & certs
check "Offline update script installed" grep -q 'vw-offline-update' "$BUILD_SCRIPT"
check "Cert renewal script installed" grep -q 'vw-cert-renew' "$BUILD_SCRIPT"
check "Cert renewal cron" grep -q 'periodic/daily/vw-cert-renew' "$BUILD_SCRIPT"
check "Chrony NTP configured" grep -q 'chrony' "$BUILD_SCRIPT"
check "Build metadata recorded" grep -q 'build-metadata' "$BUILD_SCRIPT"

# F) Smoke test included
check "Smoke test script installed" grep -q 'vw-smoketest' "$BUILD_SCRIPT"

# ── Post-build: verify image (if provided) ────────────────────────────────
IMAGE=""
if [[ "${1:-}" == "--image" && -n "${2:-}" ]]; then
  IMAGE="$2"
fi

if [[ -n "$IMAGE" && -f "$IMAGE" ]]; then
  echo ""
  echo "── Image Verification: $IMAGE ──"

  # Mount and inspect
  TMPDIR="$(mktemp -d)"
  LOOP="$(losetup --find --show --partscan "$IMAGE")"
  trap "umount ${TMPDIR}/boot 2>/dev/null; umount ${TMPDIR} 2>/dev/null; losetup -d $LOOP 2>/dev/null; rm -rf $TMPDIR" EXIT

  mount "${LOOP}p2" "$TMPDIR"
  mount "${LOOP}p1" "${TMPDIR}/boot"

  check "Image: aarch64 rootfs" file "${TMPDIR}/bin/busybox" | grep -q "aarch64\|ARM aarch64"
  check "Image: config.txt has KMS" grep -q 'vc4-kms-v3d' "${TMPDIR}/boot/config.txt"
  check "Image: vw-player service" test -f "${TMPDIR}/etc/init.d/vw-player"
  check "Image: vw-wallagent service" test -f "${TMPDIR}/etc/init.d/vw-wallagent"
  check "Image: SSH hardened" grep -q 'PasswordAuthentication no' "${TMPDIR}/etc/ssh/sshd_config.d/50-videowall.conf"
  check "Image: root locked" grep -q 'root:[!*]' "${TMPDIR}/etc/shadow"
  check "Image: offline update tool" test -x "${TMPDIR}/opt/videowall/bin/vw-offline-update.sh"
  check "Image: cert renew tool" test -x "${TMPDIR}/opt/videowall/bin/vw-cert-renew.sh"
  check "Image: smoke test tool" test -x "${TMPDIR}/opt/videowall/bin/vw-smoketest.sh"
  check "Image: build metadata" test -f "${TMPDIR}/opt/videowall/.build-metadata.json"
  check "Image: CA cert" test -f "${TMPDIR}/opt/videowall/certs/ca.crt"
  check "Image: player config" test -f "${TMPDIR}/opt/videowall/config/player.env"
  check "Image: BT disabled" grep -q 'disable-bt' "${TMPDIR}/boot/config.txt"
fi

echo ""
echo "═══════════════════════════════════════════"
echo "  Results: ${PASS} PASS, ${FAIL} FAIL"
echo "═══════════════════════════════════════════"

if [[ $FAIL -gt 0 ]]; then
  echo "  VERIFICATION FAILED"
  exit 1
fi
echo "  ALL CHECKS PASSED"
