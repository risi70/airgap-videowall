# Raspberry Pi SD Card Image Builder

Build ready-to-flash SD card images for videowall decoder Pis. Each image is a complete, hardened Alpine Linux system with the videowall player and wall agent pre-configured for a specific tile/screen.

> **Licence:** EUPL-1.2

## Design Summary

| Aspect | Decision | Rationale |
|--------|----------|-----------|
| **Base OS** | Alpine Linux 3.20 aarch64 | Minimal footprint (~80 MB installed), musl libc, OpenRC, fast boot |
| **Init system** | OpenRC | Alpine native; no systemd overhead on embedded target |
| **Video output** | DRM/KMS via `vc4-kms-v3d` | No X11 required; mpv uses `--gpu-context=drm` directly |
| **Hardware decode** | `v4l2m2m` (Pi 4) / `v4l2request` (Pi 5) | Kernel-native V4L2 decode; no proprietary blobs |
| **Player** | mpv with low-latency profile | `--cache=no --demuxer-readahead-secs=0.5 --video-latency-hacks=yes` |
| **Media stack** | GStreamer 1.x (base, good, bad, ugly, libav, SRT) + ffmpeg | Covers WebRTC, RTSP, SRT, H.264, H.265 |
| **Networking** | NetworkManager, VLAN, static IP | Reliable for headless embedded; VLAN-aware |
| **Security** | Root locked, SSH key-only, iptables default-drop, BT/WiFi disabled, rfkill | CIS-aligned hardening |
| **Cert management** | Vault PKI certs baked in + daily renewal cron | API-based renewal with USB fallback |
| **Offline updates** | `vw-offline-update.sh` — USB bundle with Ed25519 signature | Atomic apply with automatic rollback |
| **Observability** | OpenRC logging + log rotation (14d, 50MB cap) | SD card write endurance protection |
| **Watchdog** | BCM2835 hardware watchdog (15s timeout) | Auto-reboot on hang |

## Build

### Prerequisites (build host)

```bash
# Debian/Ubuntu x86_64
sudo apt install qemu-user-static binfmt-support parted \
  dosfstools e2fsprogs wget python3-yaml

# Verify binfmt is active
ls /proc/sys/fs/binfmt_misc/qemu-aarch64
```

### Pre-build verification

```bash
make pi-verify
# or:
bash tools/pi-image/verify.sh
```

### Single tile

```bash
sudo ./tools/pi-image/vw-build-pi-image.sh \
  --tile-id tile-0-0 \
  --wall-id 1 \
  --mgmt-api-url https://vw-mgmt-api.videowall.svc:8000 \
  --sfu-url https://janus.videowall.svc:8088 \
  --room-id 1234 \
  --ca-cert /path/to/ca.crt \
  --client-cert /path/to/client.crt \
  --client-key /path/to/client.key \
  --hostname vw-tile-0-0 \
  --ip 10.30.1.10/24 \
  --gateway 10.30.1.1 \
  --dns 10.30.1.1 \
  --ntp 10.30.1.1 \
  --ssh-pubkey ~/.ssh/id_ed25519.pub \
  --vlan-id 30 \
  --pi-model 4 \
  --output vw-tile-0-0.img

# Post-build verification
sudo bash tools/pi-image/verify.sh --image vw-tile-0-0.img
```

### Entire wall (batch)

```bash
sudo ./tools/pi-image/vw-build-wall-images.sh \
  --manifest tools/pi-image/examples/wall-1-ops-room.yaml \
  --output-dir ./images/
```

## Flash

```bash
sudo dd if=vw-tile-0-0.img of=/dev/sdX bs=4M status=progress conv=fsync
sync

# Or with compression:
zstd vw-tile-0-0.img -o vw-tile-0-0.img.zst
zstd -d vw-tile-0-0.img.zst --stdout | sudo dd of=/dev/sdX bs=4M status=progress
```

## First Boot

1. Insert SD card into Pi, connect HDMI + Ethernet
2. Pi boots → root partition auto-expands to fill card
3. NetworkManager brings up eth0 (static IP or DHCP)
4. Chrony syncs time from local NTP server
5. `vw-wallagent` starts → connects to mgmt-api, fetches layout assignment
6. `vw-player` starts → fetches subscribe token → plays stream via mpv (DRM/KMS)

### On-device validation

SSH in and run the smoke test:

```bash
ssh vw-player@10.30.1.10
sudo /opt/videowall/bin/vw-smoketest.sh
```

Expected output: all checks ✓ PASS (some ⚠ WARN are acceptable for optional features).

## Offline Updates

### Prepare update bundle (on build host)

```bash
# Create update bundle
tools/bundlectl/bundlectl.py export \
  --config-dir /path/to/updated-config \
  --key /path/to/ed25519-private-key \
  --output vw-update-2026-02-21.tar.zst

# Generate checksum
sha256sum vw-update-2026-02-21.tar.zst > vw-update-2026-02-21.sha256

# Copy to USB drive along with .sig file
cp vw-update-*.tar.zst vw-update-*.sha256 vw-update-*.tar.zst.sig /media/usb/
```

### Apply on Pi

```bash
# Insert USB drive (auto-mounted to /media/usb)
sudo /opt/videowall/bin/vw-offline-update.sh

# Or specify path directly:
sudo /opt/videowall/bin/vw-offline-update.sh /media/usb/vw-update-2026-02-21.tar.zst
```

The update script:
1. Scans USB for `vw-update-*.tar.zst`
2. Verifies Ed25519 signature (if `.sig` present)
3. Verifies SHA-256 checksum (if `.sha256` present)
4. Verifies per-file checksums from `manifest.json`
5. Creates rollback snapshot of current state
6. Applies atomically (agents + config + certs)
7. Restarts services
8. On failure: automatic rollback to previous state

### Manual rollback

```bash
# List available rollback snapshots
ls -la /var/lib/videowall/rollback/

# Restore manually
sudo tar xzf /var/lib/videowall/rollback/rollback-<timestamp>.tar.gz -C /
sudo rc-service vw-player restart
sudo rc-service vw-wallagent restart
```

## Certificate Rotation

Certificates are renewed automatically via daily cron (`/etc/periodic/daily/vw-cert-renew`).

Two modes:
1. **API mode**: requests new cert from mgmt-api `/api/v1/certs/issue` (if reachable)
2. **USB mode**: imports certs from `/media/usb/vw-certs/` (ca.crt, client.crt, client.key)

Manual trigger:

```bash
sudo /opt/videowall/bin/vw-cert-renew.sh
```

## Troubleshooting

| Symptom | Check | Fix |
|---------|-------|-----|
| No video output | `cat /boot/config.txt \| grep kms` | Ensure `vc4-kms-v3d` is set |
| Garbled display | Check HDMI cable, `dmesg \| grep drm` | Try `hdmi_safe=1` in config.txt |
| Player won't start | `rc-service vw-player status`, check logs | `cat /opt/videowall/logs/player-error.log` |
| Can't reach mgmt-api | `curl -k https://vw-mgmt-api:8000/` | Check VLAN, IP, firewall rules |
| Token fetch fails | Check `/opt/videowall/config/player.env` | Verify VW_MGMT_API_URL and certs |
| High CPU (SW decode) | `top`, check mpv output | Verify `--hwdec=v4l2m2m` in env |
| SD card full | `df -h` | Check log rotation: `/etc/periodic/daily/vw-logrotate` |
| Clock wrong | `date`, `chronyc tracking` | Verify NTP server in `/etc/chrony/chrony.conf` |
| Cert expired | `openssl x509 -enddate -in /opt/videowall/certs/client.crt` | Run `vw-cert-renew.sh` or import from USB |
| Update fails | Check `/var/lib/videowall/rollback/` | Restore rollback snapshot |

## Hardware Test Checklist

Run these commands on the Pi after first boot:

```bash
# 1. Architecture
uname -m                          # → aarch64

# 2. DRM/KMS working
ls /dev/dri/                      # → card0, card1, renderD128
cat /sys/class/drm/card1-HDMI-A-1/status  # → connected

# 3. Hardware decode
gst-launch-1.0 videotestsrc num-buffers=100 ! v4l2h264enc ! fakesink
mpv --hwdec=v4l2m2m --vo=gpu --gpu-context=drm /dev/video0  # (test src)

# 4. Network
ip addr show eth0                 # Check IP
curl -k https://vw-mgmt-api:8000/api/v1/walls  # Check connectivity

# 5. Services
rc-status default                 # All services started
rc-service vw-player status       # Running

# 6. Security
iptables -L -n                    # Default DROP, allow rules present
rfkill list                       # wifi: blocked, bluetooth: blocked

# 7. Smoke test
sudo /opt/videowall/bin/vw-smoketest.sh
```
