#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
# ──────────────────────────────────────────────────────────────────────────────
# vw-build-pi-image.sh — Build a minimal SD-card image for videowall decoders
#
# Starts from Alpine Linux aarch64 minirootfs, adds GStreamer/mpv decode stack,
# vw-tile-player + vw-wallctl agents, mTLS certs, and kiosk autologin.
# Output is a raw .img file ready to write with dd.
#
# Usage:
#   sudo ./vw-build-pi-image.sh \
#     --tile-id tile-0-0 \
#     --wall-id 1 \
#     --mgmt-api-url https://vw-mgmt-api.videowall.svc:8000 \
#     --sfu-url https://janus.videowall.svc:8088 \
#     --room-id 1234 \
#     --ca-cert /path/to/ca.crt \
#     --client-cert /path/to/client.crt \
#     --client-key /path/to/client.key \
#     --hostname vw-tile-0-0 \
#     --ip 10.30.1.10/24 \
#     --gateway 10.30.1.1 \
#     --dns 10.30.1.1 \
#     --ntp 10.30.1.1 \
#     --wifi-ssid "" \
#     --wifi-psk "" \
#     --ssh-pubkey ~/.ssh/id_ed25519.pub \
#     --output vw-tile-0-0.img \
#     --image-size 2G \
#     --player-mode tile \
#     --stream-token "preloaded-token-or-empty" \
#     --hwdec v4l2m2m \
#     --display 0 \
#     --vlan-id 30 \
#     --pi-model 4
#
# Requires: qemu-user-static (binfmt), losetup, mkfs.vfat, mkfs.ext4,
#           parted, wget, chroot
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail
trap 'cleanup' EXIT

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# ──────────────────────────────────────────────────────────────────────────────
# Defaults
# ──────────────────────────────────────────────────────────────────────────────
TILE_ID=""
WALL_ID="1"
MGMT_API_URL="https://vw-mgmt-api:8000"
SFU_URL="https://janus:8088"
ROOM_ID="1234"
CA_CERT=""
CLIENT_CERT=""
CLIENT_KEY=""
HOSTNAME="vw-player"
STATIC_IP=""
GATEWAY=""
DNS="1.1.1.1"
NTP=""
WIFI_SSID=""
WIFI_PSK=""
SSH_PUBKEY=""
OUTPUT="vw-player.img"
IMAGE_SIZE="2G"
PLAYER_MODE="tile"          # tile | big
STREAM_TOKEN=""
HWDEC="v4l2m2m"            # v4l2m2m (Pi4) | v4l2request (Pi5) | auto
DISPLAY_NUM="0"
VLAN_ID=""
PI_MODEL="4"                # 4 | 5
ALPINE_VERSION="3.20"
ALPINE_ARCH="aarch64"
BOOT_SIZE_MB=256
EXTRA_PACKAGES=""           # comma-separated
STREAM_URL_OVERRIDE=""      # override: play this URL directly instead of API-driven
SAFE_SLATE_IMAGE=""         # path to a PNG shown when no stream (optional)

LOOP_DEV=""
MNT_ROOT=""
MNT_BOOT=""

# ──────────────────────────────────────────────────────────────────────────────
# Parse arguments
# ──────────────────────────────────────────────────────────────────────────────
usage() {
  grep '^#' "$0" | sed 's/^# \?//' | head -40
  echo ""
  echo "All --flags are shown above. Required: --tile-id, --mgmt-api-url, --sfu-url"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tile-id)        TILE_ID="$2"; shift 2 ;;
    --wall-id)        WALL_ID="$2"; shift 2 ;;
    --mgmt-api-url)   MGMT_API_URL="$2"; shift 2 ;;
    --sfu-url)        SFU_URL="$2"; shift 2 ;;
    --room-id)        ROOM_ID="$2"; shift 2 ;;
    --ca-cert)        CA_CERT="$2"; shift 2 ;;
    --client-cert)    CLIENT_CERT="$2"; shift 2 ;;
    --client-key)     CLIENT_KEY="$2"; shift 2 ;;
    --hostname)       HOSTNAME="$2"; shift 2 ;;
    --ip)             STATIC_IP="$2"; shift 2 ;;
    --gateway)        GATEWAY="$2"; shift 2 ;;
    --dns)            DNS="$2"; shift 2 ;;
    --ntp)            NTP="$2"; shift 2 ;;
    --wifi-ssid)      WIFI_SSID="$2"; shift 2 ;;
    --wifi-psk)       WIFI_PSK="$2"; shift 2 ;;
    --ssh-pubkey)     SSH_PUBKEY="$2"; shift 2 ;;
    --output)         OUTPUT="$2"; shift 2 ;;
    --image-size)     IMAGE_SIZE="$2"; shift 2 ;;
    --player-mode)    PLAYER_MODE="$2"; shift 2 ;;
    --stream-token)   STREAM_TOKEN="$2"; shift 2 ;;
    --stream-url)     STREAM_URL_OVERRIDE="$2"; shift 2 ;;
    --hwdec)          HWDEC="$2"; shift 2 ;;
    --display)        DISPLAY_NUM="$2"; shift 2 ;;
    --vlan-id)        VLAN_ID="$2"; shift 2 ;;
    --pi-model)       PI_MODEL="$2"; shift 2 ;;
    --extra-packages) EXTRA_PACKAGES="$2"; shift 2 ;;
    --safe-slate)     SAFE_SLATE_IMAGE="$2"; shift 2 ;;
    --help|-h)        usage ;;
    *) echo "Unknown option: $1"; usage ;;
  esac
done

if [[ -z "$TILE_ID" ]]; then
  echo "ERROR: --tile-id is required"
  usage
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: this script must run as root (needs losetup, mount, chroot)"
  exit 1
fi

# ──────────────────────────────────────────────────────────────────────────────
# Derived
# ──────────────────────────────────────────────────────────────────────────────
ALPINE_MIRROR="https://dl-cdn.alpinelinux.org/alpine"
ALPINE_ROOTFS_URL="${ALPINE_MIRROR}/v${ALPINE_VERSION}/releases/${ALPINE_ARCH}/alpine-minirootfs-${ALPINE_VERSION}.0-${ALPINE_ARCH}.tar.gz"
# Pi firmware
RPI_FIRMWARE_TAG="1.20240529"

WORKDIR="$(mktemp -d /tmp/vw-pi-image.XXXXXX)"
MNT_ROOT="${WORKDIR}/rootfs"
MNT_BOOT="${WORKDIR}/boot"
ROOTFS_TAR="${WORKDIR}/alpine-rootfs.tar.gz"

log() { echo "[$(date +%H:%M:%S)] $*"; }

cleanup() {
  log "Cleaning up..."
  # Unmount in reverse order
  umount "${MNT_ROOT}/proc" 2>/dev/null || true
  umount "${MNT_ROOT}/sys" 2>/dev/null || true
  umount "${MNT_ROOT}/dev" 2>/dev/null || true
  umount "${MNT_BOOT}" 2>/dev/null || true
  umount "${MNT_ROOT}" 2>/dev/null || true
  [[ -n "$LOOP_DEV" ]] && losetup -d "$LOOP_DEV" 2>/dev/null || true
  rm -rf "${WORKDIR}"
}

# ──────────────────────────────────────────────────────────────────────────────
# 1. Create blank image + partition table
# ──────────────────────────────────────────────────────────────────────────────
log "Creating ${IMAGE_SIZE} image: ${OUTPUT}"
truncate -s "$IMAGE_SIZE" "$OUTPUT"

log "Partitioning (boot=FAT32 ${BOOT_SIZE_MB}MB + root=ext4)"
parted -s "$OUTPUT" \
  mklabel msdos \
  mkpart primary fat32 1MiB "${BOOT_SIZE_MB}MiB" \
  set 1 boot on \
  mkpart primary ext4 "${BOOT_SIZE_MB}MiB" 100%

LOOP_DEV="$(losetup --find --show --partscan "$OUTPUT")"
PART_BOOT="${LOOP_DEV}p1"
PART_ROOT="${LOOP_DEV}p2"

log "Formatting: ${PART_BOOT} (vfat), ${PART_ROOT} (ext4)"
mkfs.vfat -F 32 -n BOOT "$PART_BOOT"
mkfs.ext4 -L VWROOT -q "$PART_ROOT"

mkdir -p "$MNT_ROOT" "$MNT_BOOT"
mount "$PART_ROOT" "$MNT_ROOT"
mkdir -p "${MNT_ROOT}/boot"
mount "$PART_BOOT" "${MNT_ROOT}/boot"

# ──────────────────────────────────────────────────────────────────────────────
# 2. Download and extract Alpine rootfs
# ──────────────────────────────────────────────────────────────────────────────
log "Downloading Alpine minirootfs (${ALPINE_VERSION} ${ALPINE_ARCH})..."
if [[ -f "${SCRIPT_DIR}/alpine-rootfs.tar.gz" ]]; then
  cp "${SCRIPT_DIR}/alpine-rootfs.tar.gz" "$ROOTFS_TAR"
  log "  (using cached copy)"
else
  wget -q -O "$ROOTFS_TAR" "$ALPINE_ROOTFS_URL"
fi
tar xzf "$ROOTFS_TAR" -C "$MNT_ROOT"

# ──────────────────────────────────────────────────────────────────────────────
# 3. Download Raspberry Pi firmware + kernel
# ──────────────────────────────────────────────────────────────────────────────
log "Downloading Raspberry Pi firmware..."
FIRMWARE_DIR="${WORKDIR}/firmware"
mkdir -p "$FIRMWARE_DIR"

# Minimal boot files needed for Pi 4/5
BOOT_FILES="bootcode.bin fixup4.dat start4.elf bcm2711-rpi-4-b.dtb"
if [[ "$PI_MODEL" == "5" ]]; then
  BOOT_FILES="$BOOT_FILES bcm2712-rpi-5-b.dtb"
fi

for f in $BOOT_FILES; do
  wget -q -O "${MNT_ROOT}/boot/${f}" \
    "https://github.com/raspberrypi/firmware/raw/${RPI_FIRMWARE_TAG}/boot/${f}" 2>/dev/null || \
    log "  WARN: could not fetch ${f} — will rely on apk linux-rpi4 package"
done

# ──────────────────────────────────────────────────────────────────────────────
# 4. Setup chroot (qemu-user-static for cross-arch)
# ──────────────────────────────────────────────────────────────────────────────
log "Setting up chroot..."

# Copy qemu static if we're cross-compiling
if [[ "$(uname -m)" != "aarch64" ]]; then
  QEMU_BIN="/usr/bin/qemu-aarch64-static"
  if [[ ! -f "$QEMU_BIN" ]]; then
    log "ERROR: qemu-aarch64-static not found. Install qemu-user-static."
    exit 1
  fi
  cp "$QEMU_BIN" "${MNT_ROOT}/usr/bin/"
fi

# Mount virtual filesystems
mount -t proc /proc "${MNT_ROOT}/proc"
mount --rbind /sys "${MNT_ROOT}/sys"
mount --rbind /dev "${MNT_ROOT}/dev"

# Configure DNS in chroot
cp /etc/resolv.conf "${MNT_ROOT}/etc/resolv.conf"

# Configure Alpine repos
cat > "${MNT_ROOT}/etc/apk/repositories" << EOF
${ALPINE_MIRROR}/v${ALPINE_VERSION}/main
${ALPINE_MIRROR}/v${ALPINE_VERSION}/community
EOF

# ──────────────────────────────────────────────────────────────────────────────
# 5. Install packages inside chroot
# ──────────────────────────────────────────────────────────────────────────────
log "Installing packages (kernel, GStreamer, mpv, Python, hardening)..."

KERNEL_PKG="linux-rpi4"
[[ "$PI_MODEL" == "5" ]] && KERNEL_PKG="linux-rpi"

# Core packages for media decode + kiosk
APK_PACKAGES="
  ${KERNEL_PKG}
  linux-firmware-brcm
  raspberrypi-bootloader
  mpv
  gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad
  gst-plugins-ugly gstreamer-tools
  gst-libav
  libsrt srt-tools gst-plugins-bad-srt
  ffmpeg
  mesa-dri-gallium mesa-egl mesa-gbm libdrm
  v4l-utils
  libva libva-utils
  python3 py3-requests py3-yaml py3-pip
  curl jq zstd
  openssl ca-certificates
  openssh-server
  chrony
  dbus eudev
  networkmanager
  sudo
  iptables ip6tables
  htop lsof
  util-linux e2fsprogs dosfstools sfdisk
  watchdog
  busybox-openrc
  rfkill
"

# Optional extra packages
if [[ -n "$EXTRA_PACKAGES" ]]; then
  APK_PACKAGES="$APK_PACKAGES ${EXTRA_PACKAGES//,/ }"
fi

chroot "$MNT_ROOT" /sbin/apk update
chroot "$MNT_ROOT" /sbin/apk add --no-cache $APK_PACKAGES

# ──────────────────────────────────────────────────────────────────────────────
# 6. Pi boot configuration (config.txt + cmdline.txt)
# ──────────────────────────────────────────────────────────────────────────────
log "Writing boot configuration..."

cat > "${MNT_ROOT}/boot/config.txt" << EOF
# Raspberry Pi ${PI_MODEL} — Videowall Decoder
disable_overscan=1
dtoverlay=vc4-kms-v3d
max_framebuffers=2

# GPU memory (256MB for smooth 1080p decode)
gpu_mem=256

# Force HDMI output
hdmi_force_hotplug=1
hdmi_group=1
hdmi_mode=16

# Pi 4: enable 4K output on hdmi0
[pi4]
hdmi_enable_4kp60=0
arm_boost=1

# Pi 5
[pi5]
arm_boost=1

[all]
# Disable Bluetooth and Wi-Fi (air-gapped; reduces attack surface)
dtoverlay=disable-bt
dtoverlay=disable-wifi

# Enable hardware watchdog
dtparam=watchdog=on

# Console on serial (for debug)
enable_uart=1
EOF

# Root partition UUID
ROOT_UUID="$(blkid -s UUID -o value "$PART_ROOT")"
cat > "${MNT_ROOT}/boot/cmdline.txt" << EOF
console=serial0,115200 console=tty1 root=UUID=${ROOT_UUID} rootfstype=ext4 elevator=deadline rootwait quiet
EOF

# fstab
BOOT_UUID="$(blkid -s UUID -o value "$PART_BOOT")"
cat > "${MNT_ROOT}/etc/fstab" << EOF
UUID=${ROOT_UUID}  /      ext4  defaults,noatime  0 1
UUID=${BOOT_UUID}  /boot  vfat  defaults          0 2
tmpfs              /tmp   tmpfs defaults,nodev,nosuid,size=128M 0 0
EOF

# ──────────────────────────────────────────────────────────────────────────────
# 7. Network configuration
# ──────────────────────────────────────────────────────────────────────────────
log "Configuring network (hostname: ${HOSTNAME})..."
echo "$HOSTNAME" > "${MNT_ROOT}/etc/hostname"
cat > "${MNT_ROOT}/etc/hosts" << EOF
127.0.0.1   localhost
127.0.1.1   ${HOSTNAME}
::1         localhost
EOF

# NetworkManager connection for Ethernet
ETH_IFACE="eth0"
[[ -n "$VLAN_ID" ]] && ETH_IFACE="eth0.${VLAN_ID}"

NM_DIR="${MNT_ROOT}/etc/NetworkManager/system-connections"
mkdir -p "$NM_DIR"

if [[ -n "$STATIC_IP" ]]; then
  cat > "${NM_DIR}/videowall-eth.nmconnection" << EOF
[connection]
id=videowall-eth
type=ethernet
interface-name=eth0
autoconnect=true

[ipv4]
method=manual
addresses=${STATIC_IP}
gateway=${GATEWAY}
dns=${DNS}

[ipv6]
method=disabled
EOF

  # VLAN sub-interface
  if [[ -n "$VLAN_ID" ]]; then
    cat > "${NM_DIR}/videowall-vlan.nmconnection" << EOF
[connection]
id=videowall-vlan${VLAN_ID}
type=vlan
autoconnect=true

[vlan]
parent=eth0
id=${VLAN_ID}

[ipv4]
method=manual
addresses=${STATIC_IP}
gateway=${GATEWAY}
dns=${DNS}

[ipv6]
method=disabled
EOF
  fi
else
  cat > "${NM_DIR}/videowall-eth.nmconnection" << EOF
[connection]
id=videowall-eth
type=ethernet
interface-name=eth0
autoconnect=true

[ipv4]
method=auto

[ipv6]
method=disabled
EOF
fi

chmod 600 "${NM_DIR}"/*.nmconnection

# Wi-Fi (optional, disabled by default in air-gapped deployments)
if [[ -n "$WIFI_SSID" && -n "$WIFI_PSK" ]]; then
  cat > "${NM_DIR}/videowall-wifi.nmconnection" << EOF
[connection]
id=videowall-wifi
type=wifi
autoconnect=false

[wifi]
ssid=${WIFI_SSID}
mode=infrastructure

[wifi-security]
key-mgmt=wpa-psk
psk=${WIFI_PSK}

[ipv4]
method=auto

[ipv6]
method=disabled
EOF
  chmod 600 "${NM_DIR}/videowall-wifi.nmconnection"
fi

# ──────────────────────────────────────────────────────────────────────────────
# 8. NTP (chrony — local server for air-gap)
# ──────────────────────────────────────────────────────────────────────────────
if [[ -n "$NTP" ]]; then
  log "Configuring chrony (NTP server: ${NTP})..."
  cat > "${MNT_ROOT}/etc/chrony/chrony.conf" << EOF
server ${NTP} iburst prefer
driftfile /var/lib/chrony/drift
makestep 1 3
rtcsync
EOF
fi

# ──────────────────────────────────────────────────────────────────────────────
# 9. System hardening
# ──────────────────────────────────────────────────────────────────────────────
log "Applying system hardening..."

# Create vw-player user (unprivileged, video + audio groups)
chroot "$MNT_ROOT" /usr/sbin/addgroup -S vw-player 2>/dev/null || true
chroot "$MNT_ROOT" /usr/sbin/adduser -S -G vw-player -h /home/vw-player \
  -s /bin/sh -D vw-player 2>/dev/null || true
chroot "$MNT_ROOT" /usr/sbin/addgroup vw-player video 2>/dev/null || true
chroot "$MNT_ROOT" /usr/sbin/addgroup vw-player audio 2>/dev/null || true
chroot "$MNT_ROOT" /usr/sbin/addgroup vw-player input 2>/dev/null || true
chroot "$MNT_ROOT" /usr/sbin/addgroup vw-player render 2>/dev/null || true

# Lock root password
chroot "$MNT_ROOT" /usr/bin/passwd -l root

# SSH: key-only auth
mkdir -p "${MNT_ROOT}/etc/ssh/sshd_config.d"
cat > "${MNT_ROOT}/etc/ssh/sshd_config.d/50-videowall.conf" << 'EOF'
PermitRootLogin no
PasswordAuthentication no
ChallengeResponseAuthentication no
UsePAM no
X11Forwarding no
AllowUsers vw-player
MaxAuthTries 3
LoginGraceTime 30
ClientAliveInterval 300
ClientAliveCountMax 2
EOF

if [[ -n "$SSH_PUBKEY" && -f "$SSH_PUBKEY" ]]; then
  mkdir -p "${MNT_ROOT}/home/vw-player/.ssh"
  cp "$SSH_PUBKEY" "${MNT_ROOT}/home/vw-player/.ssh/authorized_keys"
  chmod 700 "${MNT_ROOT}/home/vw-player/.ssh"
  chmod 600 "${MNT_ROOT}/home/vw-player/.ssh/authorized_keys"
  chroot "$MNT_ROOT" /usr/bin/chown -R vw-player:vw-player /home/vw-player/.ssh
fi

# Firewall: allow only SSH + mgmt-api + SFU + NTP, drop everything else
cat > "${MNT_ROOT}/etc/local.d/10-firewall.start" << 'FWEOF'
#!/bin/sh
iptables -F
iptables -P INPUT DROP
iptables -P FORWARD DROP
iptables -P OUTPUT ACCEPT

# Loopback
iptables -A INPUT -i lo -j ACCEPT

# Established connections
iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# SSH (from management VLAN)
iptables -A INPUT -p tcp --dport 22 -j ACCEPT

# ICMP
iptables -A INPUT -p icmp -j ACCEPT

# DHCP client
iptables -A INPUT -p udp --sport 67 --dport 68 -j ACCEPT

# WebRTC media (UDP high ports for SRTP)
iptables -A INPUT -p udp --dport 20000:20200 -j ACCEPT

# SRT ingest (if needed)
iptables -A INPUT -p udp --dport 9000:9100 -j ACCEPT

# Drop everything else (logged)
iptables -A INPUT -j LOG --log-prefix "vw-fw-drop: " --log-level 7
iptables -A INPUT -j DROP
FWEOF
chmod +x "${MNT_ROOT}/etc/local.d/10-firewall.start"

# Sysctl hardening
cat > "${MNT_ROOT}/etc/sysctl.d/99-videowall.conf" << 'EOF'
net.ipv4.ip_forward = 0
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv6.conf.all.disable_ipv6 = 1
kernel.sysrq = 0
fs.protected_hardlinks = 1
fs.protected_symlinks = 1
EOF

# Disable Wi-Fi and Bluetooth via rfkill (defense in depth beyond dtoverlay)
cat > "${MNT_ROOT}/etc/local.d/05-rfkill.start" << 'RFEOF'
#!/bin/sh
rfkill block wifi 2>/dev/null || true
rfkill block bluetooth 2>/dev/null || true
RFEOF
chmod +x "${MNT_ROOT}/etc/local.d/05-rfkill.start"

# Journald configuration (log limits for SD card longevity)
mkdir -p "${MNT_ROOT}/etc/conf.d"
cat > "${MNT_ROOT}/etc/conf.d/vw-journald" << 'EOF'
# Journald-equivalent log limits (OpenRC uses syslog/logrotate)
# Keep logs small — SD card has limited write endurance
VW_LOG_MAX_SIZE_MB=50
VW_LOG_RETENTION_DAYS=14
EOF

# Logrotate for player logs
mkdir -p "${MNT_ROOT}/etc/periodic/daily"
cat > "${MNT_ROOT}/etc/periodic/daily/vw-logrotate" << 'LOGEOF'
#!/bin/sh
# Rotate videowall logs
LOG_DIR="/opt/videowall/logs"
MAX_SIZE=10485760  # 10MB
KEEP=5

for logfile in "$LOG_DIR"/*.log; do
  [ -f "$logfile" ] || continue
  size=$(stat -c%s "$logfile" 2>/dev/null || echo 0)
  if [ "$size" -gt "$MAX_SIZE" ]; then
    # Rotate
    i=$KEEP
    while [ "$i" -gt 0 ]; do
      prev=$((i - 1))
      [ -f "${logfile}.${prev}" ] && mv "${logfile}.${prev}" "${logfile}.${i}"
      i=$((i - 1))
    done
    mv "$logfile" "${logfile}.0"
    touch "$logfile"
    chown vw-player:vw-player "$logfile"
  fi
done

# Clean old logs
find "$LOG_DIR" -name '*.log.*' -mtime +14 -delete 2>/dev/null || true
LOGEOF
chmod +x "${MNT_ROOT}/etc/periodic/daily/vw-logrotate"

# Disable screen blanking at kernel level
cat >> "${MNT_ROOT}/etc/sysctl.d/99-videowall.conf" << 'EOF'
kernel.consoleblank = 0
EOF

# ──────────────────────────────────────────────────────────────────────────────
# 10. Install videowall application
# ──────────────────────────────────────────────────────────────────────────────
log "Installing videowall player + agents..."

VW_DIR="${MNT_ROOT}/opt/videowall"
mkdir -p "${VW_DIR}/agents/tile-player" \
         "${VW_DIR}/agents/big-player" \
         "${VW_DIR}/agents/_common" \
         "${VW_DIR}/certs" \
         "${VW_DIR}/config" \
         "${VW_DIR}/logs" \
         "${VW_DIR}/slate"

# Copy player code from repo
cp "${REPO_ROOT}/agents/tile-player/vw_tile_player.py" "${VW_DIR}/agents/tile-player/"
cp "${REPO_ROOT}/agents/big-player/vw_big_player.py" "${VW_DIR}/agents/big-player/"
cp "${REPO_ROOT}/agents/_common/vw_cfg.py" "${VW_DIR}/agents/_common/"
cp "${REPO_ROOT}/agents/_common/vw_http.py" "${VW_DIR}/agents/_common/"
cp "${REPO_ROOT}/agents/wallctl/vw_wallctl.py" "${VW_DIR}/agents/" 2>/dev/null || true

# Install operational tooling from rootfs overlay
ROOTFS_OVERLAY="${SCRIPT_DIR}/rootfs"
if [ -d "$ROOTFS_OVERLAY" ]; then
  log "Installing operational tooling (offline-update, cert-renew, smoketest)..."
  mkdir -p "${VW_DIR}/bin"
  cp "${ROOTFS_OVERLAY}/opt/videowall/bin/vw-offline-update.sh" "${VW_DIR}/bin/"
  cp "${ROOTFS_OVERLAY}/opt/videowall/bin/vw-cert-renew.sh" "${VW_DIR}/bin/"
  cp "${ROOTFS_OVERLAY}/opt/videowall/bin/vw-smoketest.sh" "${VW_DIR}/bin/"
  chmod +x "${VW_DIR}/bin/"*.sh
fi

# Install cert renewal cron job (daily)
mkdir -p "${MNT_ROOT}/etc/periodic/daily"
cat > "${MNT_ROOT}/etc/periodic/daily/vw-cert-renew" << 'CRONEOF'
#!/bin/sh
exec /opt/videowall/bin/vw-cert-renew.sh
CRONEOF
chmod +x "${MNT_ROOT}/etc/periodic/daily/vw-cert-renew"

# Create data directory (writable state: logs, rollback, update staging)
mkdir -p "${MNT_ROOT}/var/lib/videowall/rollback"

# Copy mTLS certificates
if [[ -n "$CA_CERT" && -f "$CA_CERT" ]]; then
  cp "$CA_CERT" "${VW_DIR}/certs/ca.crt"
fi
if [[ -n "$CLIENT_CERT" && -f "$CLIENT_CERT" ]]; then
  cp "$CLIENT_CERT" "${VW_DIR}/certs/client.crt"
fi
if [[ -n "$CLIENT_KEY" && -f "$CLIENT_KEY" ]]; then
  cp "$CLIENT_KEY" "${VW_DIR}/certs/client.key"
  chmod 600 "${VW_DIR}/certs/client.key"
fi

# Safe slate image
if [[ -n "$SAFE_SLATE_IMAGE" && -f "$SAFE_SLATE_IMAGE" ]]; then
  cp "$SAFE_SLATE_IMAGE" "${VW_DIR}/slate/safe-slate.png"
fi

# ──────────────────────────────────────────────────────────────────────────────
# 11. Application configuration
# ──────────────────────────────────────────────────────────────────────────────
log "Writing application configuration..."

cat > "${VW_DIR}/config/player.env" << EOF
# Videowall Tile Player Configuration
# Generated by vw-build-pi-image.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)

VW_TILE_ID=${TILE_ID}
VW_WALL_ID=${WALL_ID}
VW_MGMT_API_URL=${MGMT_API_URL}
VW_SFU_URL=${SFU_URL}
VW_ROOM_ID=${ROOM_ID}
VW_TOKEN=${STREAM_TOKEN}
VW_DISPLAY=${DISPLAY_NUM}
VW_HWDEC=${HWDEC}
VW_PLAYER_MODE=${PLAYER_MODE}
VW_STREAM_URL=${STREAM_URL_OVERRIDE}

# mTLS
VW_CA_CERT=/opt/videowall/certs/ca.crt
VW_CLIENT_CERT=/opt/videowall/certs/client.crt
VW_CLIENT_KEY=/opt/videowall/certs/client.key
EOF
chmod 600 "${VW_DIR}/config/player.env"

# Build metadata (reproducibility + audit trail)
cat > "${VW_DIR}/.build-metadata.json" << METAEOF
{
  "tile_id": "${TILE_ID}",
  "wall_id": "${WALL_ID}",
  "hostname": "${HOSTNAME}",
  "pi_model": "${PI_MODEL}",
  "alpine_version": "${ALPINE_VERSION}",
  "hwdec": "${HWDEC}",
  "player_mode": "${PLAYER_MODE}",
  "build_date": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "build_host": "$(hostname 2>/dev/null || echo unknown)",
  "image_size": "${IMAGE_SIZE}",
  "builder_version": "1.1.0"
}
METAEOF

# Per-tile env (for template systemd unit)
mkdir -p "${MNT_ROOT}/etc/videowall/tiles"
cat > "${MNT_ROOT}/etc/videowall/tiles/${TILE_ID}.env" << EOF
VW_TOKEN=${STREAM_TOKEN}
VW_SFU_URL=${SFU_URL}
VW_ROOM_ID=${ROOM_ID}
VW_DISPLAY=${DISPLAY_NUM}
EOF

# ──────────────────────────────────────────────────────────────────────────────
# 12. Player wrapper script (GStreamer + mpv with hardware decode)
# ──────────────────────────────────────────────────────────────────────────────
log "Installing kiosk player wrapper..."

cat > "${VW_DIR}/agents/tile-player/vw-kiosk-play.sh" << 'PLAYEREOF'
#!/bin/sh
# ──────────────────────────────────────────────────────────────────────────────
# vw-kiosk-play.sh — Hardware-accelerated kiosk player for Raspberry Pi
#
# Reads configuration from /opt/videowall/config/player.env and starts
# mpv in fullscreen kiosk mode with hardware decode.
#
# Supports two flows:
#   1. API-driven: polls mgmt-api for stream URL + subscribe token
#   2. Direct: plays a static stream URL (VW_STREAM_URL set)
# ──────────────────────────────────────────────────────────────────────────────
set -eu

ENV_FILE="/opt/videowall/config/player.env"
[ -f "$ENV_FILE" ] && . "$ENV_FILE"

HWDEC="${VW_HWDEC:-v4l2m2m}"
DISPLAY_OPT=""
[ -n "${VW_DISPLAY:-}" ] && DISPLAY_OPT="--screen=${VW_DISPLAY}"

# DRM/KMS mode — no X11 display server needed
# Screen blanking is disabled via kernel (consoleblank=0 in sysctl)

STREAM_URL="${VW_STREAM_URL:-}"

# ── API-driven token + URL fetch ──────────────────────────────────────────
fetch_stream_url() {
  # Call mgmt-api to get a subscribe token, then derive the playback URL
  if [ -z "${VW_MGMT_API_URL:-}" ] || [ -z "${VW_TILE_ID:-}" ]; then
    return 1
  fi

  CURL_OPTS=""
  if [ -f "${VW_CA_CERT:-}" ]; then
    CURL_OPTS="--cacert ${VW_CA_CERT}"
  fi
  if [ -f "${VW_CLIENT_CERT:-}" ] && [ -f "${VW_CLIENT_KEY:-}" ]; then
    CURL_OPTS="${CURL_OPTS} --cert ${VW_CLIENT_CERT} --key ${VW_CLIENT_KEY}"
  fi

  RESP=$(curl -sf $CURL_OPTS \
    -X POST "${VW_MGMT_API_URL}/api/v1/tokens/subscribe" \
    -H "Content-Type: application/json" \
    -d "{\"wall_id\": ${VW_WALL_ID:-1}, \"source_id\": 1, \"tile_id\": \"${VW_TILE_ID}\"}" \
    2>/dev/null) || return 1

  TOKEN=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('token',''))" 2>/dev/null)
  ALLOWED=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('allowed',False))" 2>/dev/null)

  if [ "$ALLOWED" != "True" ] || [ -z "$TOKEN" ]; then
    return 1
  fi

  # Build playback URL for Janus WebRTC via HTTP API
  # (mpv can play WebRTC via its webrtc:// support, or we use SFU HTTP streaming)
  echo "${VW_SFU_URL}/janus/${VW_ROOM_ID}?token=${TOKEN}"
}

# ── Main loop ─────────────────────────────────────────────────────────────
RESTART_COUNT=0
MAX_RESTARTS=1000
SLATE="/opt/videowall/slate/safe-slate.png"

while true; do
  # Resolve stream URL
  if [ -z "$STREAM_URL" ]; then
    STREAM_URL=$(fetch_stream_url) || STREAM_URL=""
  fi

  if [ -z "$STREAM_URL" ]; then
    echo "[vw-player] No stream URL available; showing slate..." >&2
    if [ -f "$SLATE" ]; then
      # Display slate via mpv (single frame, DRM output)
      mpv --no-terminal --really-quiet --gpu-context=drm --vo=gpu \
        --image-display-duration=5 --loop-file=no "$SLATE" 2>/dev/null || true
    else
      sleep 5
    fi
    # Reset for next API poll
    STREAM_URL="${VW_STREAM_URL:-}"
    continue
  fi

  echo "[vw-player] Playing: $STREAM_URL (hwdec=${HWDEC})"

  mpv \
    --no-terminal \
    --fullscreen \
    --really-quiet \
    --keep-open=no \
    --hwdec="${HWDEC}" \
    --gpu-context=drm \
    --drm-connector=HDMI-A-1 \
    --vo=gpu \
    --profile=low-latency \
    --cache=no \
    --demuxer-max-bytes=512KiB \
    --demuxer-readahead-secs=0.5 \
    --video-latency-hacks=yes \
    --untimed \
    ${DISPLAY_OPT} \
    "$STREAM_URL" || true

  RESTART_COUNT=$((RESTART_COUNT + 1))
  if [ "$RESTART_COUNT" -ge "$MAX_RESTARTS" ]; then
    echo "[vw-player] Max restarts ($MAX_RESTARTS) exceeded" >&2
    exit 1
  fi

  DELAY=$((RESTART_COUNT < 6 ? RESTART_COUNT * 2 : 10))
  echo "[vw-player] Restarting in ${DELAY}s (restart ${RESTART_COUNT})" >&2
  sleep "$DELAY"

  # Re-fetch URL on restart (token may have expired)
  STREAM_URL="${VW_STREAM_URL:-}"
done
PLAYEREOF
chmod +x "${VW_DIR}/agents/tile-player/vw-kiosk-play.sh"

# ──────────────────────────────────────────────────────────────────────────────
# 13. Systemd services
# ──────────────────────────────────────────────────────────────────────────────
log "Installing systemd services..."

cat > "${MNT_ROOT}/etc/init.d/vw-player" << 'INITEOF'
#!/sbin/openrc-run
# OpenRC init script for vw-player

name="Videowall Player"
description="Videowall tile/big-screen player kiosk"
command="/opt/videowall/agents/tile-player/vw-kiosk-play.sh"
command_user="vw-player:vw-player"
command_background=true
pidfile="/run/${RC_SVCNAME}.pid"
output_log="/opt/videowall/logs/player.log"
error_log="/opt/videowall/logs/player-error.log"

depend() {
    need net
    after NetworkManager chronyd
}

start_pre() {
    # Ensure log directory is writable
    checkpath -d -m 0755 -o vw-player:vw-player /opt/videowall/logs
}
INITEOF
chmod +x "${MNT_ROOT}/etc/init.d/vw-player"

# Wall agent service (pulls config from control plane, manages tile assignments)
cat > "${MNT_ROOT}/etc/init.d/vw-wallagent" << 'WAEOF'
#!/sbin/openrc-run
# OpenRC init script for vw-wallagent (wall controller agent)

name="Videowall Wall Agent"
description="Pulls layout config from mgmt-api and manages tile player"
command="/usr/bin/python3"
command_args="/opt/videowall/agents/vw_wallctl.py --config /opt/videowall/config/player.env"
command_user="vw-player:vw-player"
command_background=true
pidfile="/run/${RC_SVCNAME}.pid"
output_log="/opt/videowall/logs/wallagent.log"
error_log="/opt/videowall/logs/wallagent-error.log"

respawn_delay=5
respawn_max=0

depend() {
    need net
    after NetworkManager chronyd
    before vw-player
}

start_pre() {
    checkpath -d -m 0755 -o vw-player:vw-player /opt/videowall/logs
}
WAEOF
chmod +x "${MNT_ROOT}/etc/init.d/vw-wallagent"

# Also install a hardware watchdog service
cat > "${MNT_ROOT}/etc/init.d/vw-watchdog" << 'WDEOF'
#!/sbin/openrc-run

name="Hardware Watchdog"
description="BCM2835 hardware watchdog"
command="/sbin/watchdog"
command_args="-T 15 -t 5 /dev/watchdog"
command_background=true
pidfile="/run/watchdog.pid"

depend() {
    need dev
}
WDEOF
chmod +x "${MNT_ROOT}/etc/init.d/vw-watchdog"

# ──────────────────────────────────────────────────────────────────────────────
# 14. Enable services at boot
# ──────────────────────────────────────────────────────────────────────────────
log "Enabling boot services..."

chroot "$MNT_ROOT" /sbin/rc-update add devfs sysinit 2>/dev/null || true
chroot "$MNT_ROOT" /sbin/rc-update add dmesg sysinit 2>/dev/null || true
chroot "$MNT_ROOT" /sbin/rc-update add mdev sysinit 2>/dev/null || true
chroot "$MNT_ROOT" /sbin/rc-update add hwdrivers sysinit 2>/dev/null || true

chroot "$MNT_ROOT" /sbin/rc-update add modules boot 2>/dev/null || true
chroot "$MNT_ROOT" /sbin/rc-update add sysctl boot 2>/dev/null || true
chroot "$MNT_ROOT" /sbin/rc-update add hostname boot 2>/dev/null || true
chroot "$MNT_ROOT" /sbin/rc-update add bootmisc boot 2>/dev/null || true
chroot "$MNT_ROOT" /sbin/rc-update add networking boot 2>/dev/null || true
chroot "$MNT_ROOT" /sbin/rc-update add seedrng boot 2>/dev/null || true

chroot "$MNT_ROOT" /sbin/rc-update add sshd default 2>/dev/null || true
chroot "$MNT_ROOT" /sbin/rc-update add chronyd default 2>/dev/null || true
chroot "$MNT_ROOT" /sbin/rc-update add NetworkManager default 2>/dev/null || true
chroot "$MNT_ROOT" /sbin/rc-update add vw-wallagent default 2>/dev/null || true
chroot "$MNT_ROOT" /sbin/rc-update add vw-player default 2>/dev/null || true
chroot "$MNT_ROOT" /sbin/rc-update add vw-watchdog default 2>/dev/null || true
chroot "$MNT_ROOT" /sbin/rc-update add local default 2>/dev/null || true

chroot "$MNT_ROOT" /sbin/rc-update add mount-ro shutdown 2>/dev/null || true
chroot "$MNT_ROOT" /sbin/rc-update add killprocs shutdown 2>/dev/null || true
chroot "$MNT_ROOT" /sbin/rc-update add savecache shutdown 2>/dev/null || true

# ──────────────────────────────────────────────────────────────────────────────
# 15. Set file ownership
# ──────────────────────────────────────────────────────────────────────────────
log "Setting ownership..."
chroot "$MNT_ROOT" /bin/chown -R vw-player:vw-player /opt/videowall/logs
chroot "$MNT_ROOT" /bin/chown -R root:root /opt/videowall/agents
chroot "$MNT_ROOT" /bin/chown -R root:vw-player /opt/videowall/certs
chroot "$MNT_ROOT" /bin/chmod 750 /opt/videowall/certs
chroot "$MNT_ROOT" /bin/chown -R root:vw-player /opt/videowall/config

# ──────────────────────────────────────────────────────────────────────────────
# 16. First-boot expansion script (optional: grow root to fill SD card)
# ──────────────────────────────────────────────────────────────────────────────
cat > "${MNT_ROOT}/etc/local.d/01-expand-rootfs.start" << 'EXPANDEOF'
#!/bin/sh
# One-shot: expand root partition to fill the SD card on first boot.
FLAG="/opt/videowall/.rootfs-expanded"
if [ -f "$FLAG" ]; then exit 0; fi

ROOT_DEV=$(findmnt -n -o SOURCE /)
DISK_DEV=$(echo "$ROOT_DEV" | sed 's/p\?[0-9]*$//')
PART_NUM=$(echo "$ROOT_DEV" | grep -o '[0-9]*$')

# Grow partition
echo ", +" | sfdisk --no-reread -N "$PART_NUM" "$DISK_DEV" 2>/dev/null || true
partprobe "$DISK_DEV" 2>/dev/null || true
resize2fs "$ROOT_DEV" 2>/dev/null || true

touch "$FLAG"
echo "[vw-firstboot] Root filesystem expanded" | logger -t vw-firstboot
EXPANDEOF
chmod +x "${MNT_ROOT}/etc/local.d/01-expand-rootfs.start"

# ──────────────────────────────────────────────────────────────────────────────
# 17. Cleanup chroot
# ──────────────────────────────────────────────────────────────────────────────
log "Cleaning up chroot..."
rm -f "${MNT_ROOT}/etc/resolv.conf"
rm -f "${MNT_ROOT}/usr/bin/qemu-aarch64-static" 2>/dev/null || true

# Clear APK cache
rm -rf "${MNT_ROOT}/var/cache/apk/"*

# ──────────────────────────────────────────────────────────────────────────────
# 18. Unmount and finalize
# ──────────────────────────────────────────────────────────────────────────────
log "Unmounting..."
umount "${MNT_ROOT}/proc" 2>/dev/null || true
umount -l "${MNT_ROOT}/sys" 2>/dev/null || true
umount -l "${MNT_ROOT}/dev" 2>/dev/null || true
umount "${MNT_ROOT}/boot"
umount "${MNT_ROOT}"
losetup -d "$LOOP_DEV"
LOOP_DEV=""

# Compress (optional)
IMG_SIZE=$(stat -c%s "$OUTPUT")
IMG_SIZE_MB=$((IMG_SIZE / 1048576))

log "──────────────────────────────────────────────────────────────"
log "SUCCESS: ${OUTPUT} (${IMG_SIZE_MB} MB)"
log ""
log "  Tile ID:    ${TILE_ID}"
log "  Wall ID:    ${WALL_ID}"
log "  Hostname:   ${HOSTNAME}"
log "  IP:         ${STATIC_IP:-DHCP}"
log "  SFU URL:    ${SFU_URL}"
log "  API URL:    ${MGMT_API_URL}"
log "  Pi model:   ${PI_MODEL}"
log "  HW decode:  ${HWDEC}"
log ""
log "Write to SD card:"
log "  sudo dd if=${OUTPUT} of=/dev/sdX bs=4M status=progress conv=fsync"
log ""
log "Or compress first:"
log "  zstd ${OUTPUT} -o ${OUTPUT}.zst"
log "  zstd -d ${OUTPUT}.zst --stdout | sudo dd of=/dev/sdX bs=4M status=progress"
log "──────────────────────────────────────────────────────────────"
