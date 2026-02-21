# Raspberry Pi SD Card Image Builder

Build ready-to-flash SD card images for videowall decoder Pis. Each image is a complete, minimal Alpine Linux system with the videowall player pre-configured for a specific tile/screen.

## How it works

```
┌──────────────┐    ┌─────────────────────────┐    ┌──────────────┐
│ Alpine Linux │    │  vw-build-pi-image.sh   │    │  tile-0-0.img│
│ minirootfs   │───▶│  + GStreamer/mpv         │───▶│  (dd-ready)  │
│ (aarch64)    │    │  + vw-player + certs     │    │              │
└──────────────┘    │  + network + hardening   │    └──────┬───────┘
                    └─────────────────────────┘           │
                                                    dd if=... of=/dev/sdX
                                                          │
                                                    ┌─────▼───────┐
                                                    │  Pi boots   │
                                                    │  → kiosk    │
                                                    │  → plays    │
                                                    └─────────────┘
```

1. Creates a 2 GB raw disk image with boot (FAT32) + root (ext4) partitions
2. Downloads Alpine Linux aarch64 minirootfs (~6 MB)
3. Installs via `chroot` + `qemu-user-static`: kernel, GStreamer, mpv, Python, NetworkManager, chrony, SSH
4. Configures: Pi boot firmware, hardware decode, static IP / VLAN, mTLS certs, iptables firewall, sysctl hardening
5. Installs the videowall player service (auto-starts on boot, fetches stream token from mgmt-api, plays via mpv with hw decode)
6. On first boot, the root partition auto-expands to fill the SD card

## Prerequisites (build host)

```bash
# Debian/Ubuntu
sudo apt install qemu-user-static binfmt-support parted \
  dosfstools e2fsprogs wget python3-yaml

# Or Alpine
sudo apk add qemu-aarch64 parted dosfstools e2fsprogs wget py3-yaml
```

## Single tile image

```bash
sudo ./vw-build-pi-image.sh \
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
```

Then flash:

```bash
sudo dd if=vw-tile-0-0.img of=/dev/sdX bs=4M status=progress conv=fsync
```

## Whole wall (batch)

Define a YAML manifest (see `examples/`), then:

```bash
sudo ./vw-build-wall-images.sh \
  --manifest examples/wall-1-ops-room.yaml \
  --output-dir ./images/
```

This generates 24 images (one per tile in a 6×4 grid), each with unique hostname, IP, and tile ID.

## Parameters

| Flag | Default | Description |
|------|---------|-------------|
| `--tile-id` | _(required)_ | Tile identifier (e.g. `tile-0-0`) |
| `--wall-id` | `1` | Wall ID in mgmt-api |
| `--mgmt-api-url` | `https://vw-mgmt-api:8000` | Mgmt API URL for token fetch |
| `--sfu-url` | `https://janus:8088` | Janus SFU URL |
| `--room-id` | `1234` | Janus VideoRoom ID |
| `--ca-cert` | | mTLS CA certificate |
| `--client-cert` | | mTLS client certificate |
| `--client-key` | | mTLS client key |
| `--hostname` | `vw-player` | System hostname |
| `--ip` | _(DHCP)_ | Static IP with CIDR (e.g. `10.30.1.10/24`) |
| `--gateway` | | Default gateway |
| `--dns` | `1.1.1.1` | DNS server |
| `--ntp` | | NTP server (for air-gapped chrony) |
| `--ssh-pubkey` | | SSH public key for remote access |
| `--vlan-id` | | Display Zone VLAN ID |
| `--pi-model` | `4` | `4` or `5` (selects kernel + hwdec) |
| `--hwdec` | `v4l2m2m` | mpv hardware decode (`v4l2m2m`, `v4l2request`, `auto`) |
| `--image-size` | `2G` | Raw image size (expands on first boot) |
| `--player-mode` | `tile` | `tile` (WebRTC via SFU) or `big` (SRT from compositor) |
| `--stream-url` | | Direct stream URL (bypasses API token fetch) |
| `--safe-slate` | | PNG shown when no stream is available |
| `--display` | `0` | Display/screen number |
| `--extra-packages` | | Additional Alpine packages (comma-separated) |

## What's in the image

| Component | Details |
|-----------|---------|
| **Base OS** | Alpine Linux 3.20 aarch64 (~50 MB installed) |
| **Kernel** | `linux-rpi4` (Pi 4) or `linux-rpi` (Pi 5) |
| **Player** | mpv with `--hwdec=v4l2m2m`, `--gpu-context=drm`, low-latency profile |
| **Media stack** | GStreamer 1.x (base, good, bad, ugly), ffmpeg |
| **Network** | NetworkManager, static IP or DHCP, VLAN support |
| **Security** | iptables firewall, sysctl hardening, SSH key-only, root locked |
| **Time sync** | chrony (pointed at local NTP server) |
| **Monitoring** | Hardware watchdog (BCM2835), auto-restart on crash |
| **Boot** | Auto-expand rootfs, auto-start player service |
| **Certificates** | mTLS certs baked in at `/opt/videowall/certs/` |

## Offline / air-gapped build

To build without internet access, pre-download the Alpine rootfs and place it alongside the script:

```bash
# On internet-connected machine
wget -O alpine-rootfs.tar.gz \
  https://dl-cdn.alpinelinux.org/alpine/v3.20/releases/aarch64/alpine-minirootfs-3.20.0-aarch64.tar.gz

# Also pre-populate an APK cache (or use apk fetch)
# Then transfer both to the air-gapped build host
```

The script checks for a local `alpine-rootfs.tar.gz` before downloading.
