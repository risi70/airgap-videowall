# Pi Image Builder — Configuration Reference

This document covers every configurable parameter of the two SD card image
builder scripts, the wall manifest format, the on-image file layout, and the
embedded operational scripts. For build/flash/first-boot instructions see
`tools/pi-image/README.md`.

---

## 1. Architecture

The builder produces a complete Alpine Linux aarch64 image per tile or screen.
Each image is self-contained: OS, media stack, videowall agents, mTLS
certificates, network config, and a hardware-decode kiosk player are all
baked in at build time. At runtime the Pi is a stateless decoder that fetches
its layout assignment from the control plane.

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Build Host (x86_64)                               │
│                                                                     │
│  vw-build-wall-images.sh ── reads wall manifest YAML                │
│       │                                                             │
│       ├── for each tile (row × col):                                │
│       │     vw-build-pi-image.sh --tile-id tile-R-C ...             │
│       │         │                                                   │
│       │         ├── 1. Create blank image (truncate + parted)       │
│       │         ├── 2. Format partitions (vfat boot + ext4 root)    │
│       │         ├── 3. Bootstrap Alpine minirootfs (qemu chroot)    │
│       │         ├── 4. Install packages (GStreamer, mpv, Python)    │
│       │         ├── 5. Write boot config (config.txt, cmdline.txt)  │
│       │         ├── 6. Configure kernel modules + DRM/KMS           │
│       │         ├── 7. Configure network (static IP, VLAN, NM)     │
│       │         ├── 8. Install mTLS certs (Vault PKI)              │
│       │         ├── 9. Apply security hardening                     │
│       │         ├── 10. Install videowall agents + player           │
│       │         ├── 11. Write env files (player.env, tile.env)     │
│       │         ├── 12. Install kiosk player wrapper                │
│       │         ├── 13. Create OpenRC services                      │
│       │         ├── 14. Install operational scripts                 │
│       │         └── 15. Write build metadata JSON                   │
│       │                                                             │
│       └── Output: images/<hostname>.img per tile                    │
└─────────────────────────────────────────────────────────────────────┘
```

Two scripts serve different use cases:

| Script | Purpose | Input |
|--------|---------|-------|
| `vw-build-pi-image.sh` | Build a single tile/screen image | CLI flags |
| `vw-build-wall-images.sh` | Build all images for an entire wall | YAML manifest |

---

## 2. Single-Image Builder (`vw-build-pi-image.sh`)

### 2.1 CLI Flags

Every flag has a default; only `--tile-id` is strictly required.

| Flag | Default | Description |
|------|---------|-------------|
| `--tile-id` | *(required)* | Unique tile identifier, e.g. `tile-0-0`. Format: `tile-{row}-{col}` |
| `--wall-id` | `${VW_WALL_ID:-1}` | Wall this tile belongs to |
| `--mgmt-api-url` | `https://vw-mgmt-api:8000` | Control plane mgmt-api URL |
| `--sfu-url` | `https://janus:8088` | Janus SFU HTTP URL |
| `--room-id` | `${VW_ROOM_ID:-0}` | Janus VideoRoom room ID (allocated by SFU controller) |
| `--ca-cert` | *(empty)* | Path to Vault CA certificate (PEM) |
| `--client-cert` | *(empty)* | Path to mTLS client certificate (PEM) |
| `--client-key` | *(empty)* | Path to mTLS client private key (PEM) |
| `--hostname` | `vw-player` | Linux hostname for the Pi |
| `--ip` | *(empty → DHCP)* | Static IP in CIDR notation, e.g. `10.30.1.10/24` |
| `--gateway` | *(empty)* | Default gateway |
| `--dns` | `1.1.1.1` | DNS server |
| `--ntp` | *(empty)* | NTP server for Chrony |
| `--wifi-ssid` | *(empty)* | Wi-Fi SSID (disabled by default in air-gap) |
| `--wifi-psk` | *(empty)* | Wi-Fi passphrase |
| `--ssh-pubkey` | *(empty)* | Path to SSH public key for remote access |
| `--output` | `vw-player.img` | Output image file path |
| `--image-size` | `2G` | Raw image size (must fit boot + rootfs) |
| `--player-mode` | `tile` | `tile` for SFU-driven tiles, `big` for compositor SRT |
| `--stream-token` | *(empty)* | Pre-loaded subscribe token (optional) |
| `--stream-url` | *(empty)* | Override: play this URL directly instead of API-driven |
| `--hwdec` | `v4l2m2m` | Hardware decode method: `v4l2m2m` (Pi 4), `v4l2request` (Pi 5), `auto` |
| `--display` | `0` | DRM display index |
| `--vlan-id` | *(empty)* | Display Zone VLAN ID (creates sub-interface `eth0.{id}`) |
| `--pi-model` | `4` | Raspberry Pi model: `4` or `5` |
| `--extra-packages` | *(empty)* | Comma-separated list of additional Alpine packages |
| `--safe-slate` | *(empty)* | Path to PNG shown when no stream is available |

### 2.2 Environment Variable Overrides

These host-side environment variables override the corresponding defaults
when the script reads them at build time:

| Variable | Overrides |
|----------|-----------|
| `VW_WALL_ID` | `--wall-id` default |
| `VW_ROOM_ID` | `--room-id` default |
| `VW_SOURCE_ID` | `source_id` in the on-image smoketest |

### 2.3 Build Prerequisites

The build host must be x86_64 Linux (Debian/Ubuntu recommended) with:

```
qemu-user-static    binfmt-support     parted
dosfstools          e2fsprogs          wget
python3-yaml        losetup (util-linux)
```

Verify with `make pi-verify` or `bash tools/pi-image/verify.sh`.

### 2.4 Image Partition Layout

| Partition | Filesystem | Label | Size | Contents |
|-----------|-----------|-------|------|----------|
| `/dev/loopXp1` | vfat (FAT32) | `BOOT` | 256 MB | Kernel, DTBs, overlays, `config.txt`, `cmdline.txt` |
| `/dev/loopXp2` | ext4 | `VWROOT` | Remainder | Alpine rootfs, agents, certs, config |

The root partition auto-expands to fill the SD card on first boot.

---

## 3. Wall Manifest Format

The batch builder (`vw-build-wall-images.sh`) reads a YAML manifest that
describes the entire wall and iterates over every tile in the grid.

### 3.1 Schema

```yaml
# ── Identity ──
wall_id: 1                          # Numeric wall ID
wall_name: "ops-room"               # Human-readable name (used in hostname)

# ── Grid ──
grid:
  rows: 6                           # Tile rows
  cols: 4                           # Tile columns
# Total images generated: rows × cols

# ── Hardware ──
pi_model: 4                         # 4 or 5
hwdec: v4l2m2m                      # v4l2m2m | v4l2request | auto
image_size: 2G                      # Per-image size

# ── Control Plane ──
mgmt_api_url: "https://vw-mgmt-api.videowall.svc:8000"
sfu_url: "https://janus.videowall.svc:8088"
room_id: 1234                       # Required — allocated by SFU controller

# ── Network ──
network:
  vlan_id: 30                       # Display Zone VLAN (empty = no VLAN)
  gateway: "10.30.1.1"
  dns: "10.30.1.1"
  ntp: "10.30.1.1"
  subnet: "10.30.1"                 # Tiles get subnet.{base_offset + index}
  base_offset: 10                   # First tile IP: subnet.10

# ── Certificates ──
certs:
  ca_cert: "/path/to/ca.crt"
  client_cert: "/path/to/client.crt"
  client_key: "/path/to/client.key"

# ── Access ──
ssh_pubkey: "~/.ssh/id_ed25519.pub"
safe_slate: ""                      # Path to PNG or empty

# ── Per-Tile Overrides ──
overrides:
  "tile-5-3":                       # Override a specific tile
    player_mode: big                # Switch from tile to big-screen mode
    stream_url: "srt://compositor.videowall.svc:9000"
```

### 3.2 IP Address Assignment

Each tile receives a deterministic IP address computed from the manifest:

```
IP = {network.subnet}.{network.base_offset + tile_index}
```

Where `tile_index` counts from 0 in row-major order (row 0 col 0 = index 0,
row 0 col 1 = index 1, and so on). For a 6×4 wall with `subnet: 10.30.1` and
`base_offset: 10`, tile IPs are `10.30.1.10` through `10.30.1.33`.

### 3.3 Hostname Convention

Each tile hostname follows the pattern:

```
vw-{wall_name}-tile-{row}-{col}
```

For example: `vw-ops-room-tile-0-0`, `vw-ops-room-tile-5-3`.

### 3.4 Per-Tile Overrides

The `overrides` map lets you customize individual tiles without creating
separate manifests. Common use cases:

- Switching a corner tile to `big` player mode for compositor output
- Setting a direct `stream_url` for SRT bypass
- Any flag supported by `vw-build-pi-image.sh`

### 3.5 Example Manifests

The repo includes two examples:

| File | Wall Type | Grid | Tiles |
|------|-----------|------|-------|
| `examples/wall-1-ops-room.yaml` | Tile wall | 6×4 | 24 Pi 4 images |
| `examples/wall-3-exec-room.yaml` | Big-screen | 1×2 | 2 Pi 5 images |

---

## 4. On-Image File Layout

After build, each image contains the following application files:

```
/opt/videowall/
├── agents/
│   ├── tile-player/
│   │   ├── vw_tile_player.py       # Python tile player agent
│   │   └── vw-kiosk-play.sh        # GStreamer/mpv kiosk wrapper
│   ├── big-player/
│   │   └── vw_big_player.py        # Big-screen player agent
│   ├── _common/
│   │   ├── vw_cfg.py               # Config loader
│   │   └── vw_http.py              # mTLS HTTP client
│   └── vw_wallctl.py               # Wall controller agent
├── bin/
│   ├── vw-cert-renew.sh            # Certificate rotation (daily cron)
│   ├── vw-offline-update.sh        # Signed USB bundle updater
│   └── vw-smoketest.sh             # On-device validation
├── certs/
│   ├── ca.crt                      # Vault CA certificate
│   ├── client.crt                  # mTLS client certificate
│   └── client.key                  # mTLS client private key
├── config/
│   └── player.env                  # Runtime environment (see §5)
├── logs/                           # Rotated logs (50 MB cap, 14-day retention)
├── slate/                          # Safe-slate image (optional PNG)
└── .build-metadata.json            # Build provenance (see §6)

/etc/videowall/tiles/
└── {tile-id}.env                   # Per-tile env (token, SFU, room, display)
```

---

## 5. Runtime Environment Files

### 5.1 player.env

Written to `/opt/videowall/config/player.env` (mode 0600):

| Variable | Source | Description |
|----------|--------|-------------|
| `VW_TILE_ID` | `--tile-id` | This tile's unique ID |
| `VW_WALL_ID` | `--wall-id` | Parent wall ID |
| `VW_MGMT_API_URL` | `--mgmt-api-url` | Control plane API |
| `VW_SFU_URL` | `--sfu-url` | Janus SFU HTTP endpoint |
| `VW_ROOM_ID` | `--room-id` | Janus VideoRoom ID |
| `VW_TOKEN` | `--stream-token` | Pre-loaded subscribe token (if any) |
| `VW_DISPLAY` | `--display` | DRM display index |
| `VW_HWDEC` | `--hwdec` | Hardware decode method |
| `VW_PLAYER_MODE` | `--player-mode` | `tile` or `big` |
| `VW_STREAM_URL` | `--stream-url` | Direct URL override (bypasses API) |
| `VW_CA_CERT` | *(fixed)* | `/opt/videowall/certs/ca.crt` |
| `VW_CLIENT_CERT` | *(fixed)* | `/opt/videowall/certs/client.crt` |
| `VW_CLIENT_KEY` | *(fixed)* | `/opt/videowall/certs/client.key` |

### 5.2 Per-Tile Env

Written to `/etc/videowall/tiles/{tile-id}.env`:

| Variable | Description |
|----------|-------------|
| `VW_TOKEN` | Subscribe token |
| `VW_SFU_URL` | Janus SFU endpoint |
| `VW_ROOM_ID` | VideoRoom ID |
| `VW_DISPLAY` | DRM display index |

---

## 6. Build Metadata

Every image contains `/opt/videowall/.build-metadata.json` for audit and
reproducibility:

```json
{
  "tile_id": "tile-0-0",
  "wall_id": "1",
  "hostname": "vw-ops-room-tile-0-0",
  "pi_model": "4",
  "alpine_version": "3.20",
  "hwdec": "v4l2m2m",
  "player_mode": "tile",
  "build_date": "2026-02-23T10:30:00Z",
  "build_host": "builder.local",
  "image_size": "2G",
  "builder_version": "1.1.0"
}
```

---

## 7. Security Hardening

The builder applies the following hardening measures to every image:

| Control | Implementation |
|---------|---------------|
| Root password locked | `passwd -l root` |
| SSH key-only auth | `PasswordAuthentication no`, `PermitRootLogin prohibit-password` |
| Firewall (iptables) | Default `INPUT DROP` / `FORWARD DROP` / `OUTPUT ACCEPT` |
| Allowed inbound | SSH (22/tcp), ICMP, established connections |
| Wi-Fi disabled | `rfkill block wifi` + `rfkill block bluetooth` |
| Hardware watchdog | BCM2835, 15-second timeout, auto-reboot on hang |
| Log rotation | 50 MB cap, 14-day retention (SD card endurance) |
| Minimal packages | Alpine minirootfs — no compiler, no desktop, no unnecessary services |

---

## 8. OpenRC Services

Three application services are registered in the `default` runlevel:

| Service | Script | Description |
|---------|--------|-------------|
| `vw-player` | `/etc/init.d/vw-player` | Kiosk player (mpv with DRM/KMS, hardware decode) |
| `vw-wallagent` | `/etc/init.d/vw-wallagent` | Wall controller agent (heartbeat, layout polling) |
| `vw-watchdog` | `/etc/init.d/vw-watchdog` | BCM2835 hardware watchdog feeder |

Boot order: `sysinit` → `boot` (modules, networking, hostname) → `default`
(chrony, sshd, NetworkManager, vw-player, vw-wallagent, vw-watchdog).

---

## 9. Operational Scripts

Three shell scripts are installed in `/opt/videowall/bin/`:

### 9.1 vw-cert-renew.sh

Runs daily via cron. Two modes:

1. **API mode**: POST to `mgmt-api/api/v1/certs/issue` with mTLS client cert.
   If the current cert expires within 7 days, requests a new one from Vault PKI.
2. **Offline mode**: Scans `/media/usb/vw-certs/` for `ca.crt`, `client.crt`,
   `client.key`. Copies if newer than installed certs.

After renewal, restarts `vw-player` and `vw-wallagent`.

### 9.2 vw-offline-update.sh

Applies signed update bundles from USB media:

1. Scans for `vw-update-*.tar.zst` on removable media
2. Verifies Ed25519 signature (`.sig` file)
3. Verifies SHA-256 checksum (`.sha256` file)
4. Checks per-file checksums from embedded `manifest.json`
5. Creates rollback snapshot to `/var/lib/videowall/rollback/`
6. Applies atomically (agents, config, certs)
7. Restarts services
8. On failure: automatic rollback to pre-update state

### 9.3 vw-smoketest.sh

Post-boot validation. Checks:

- Architecture is `aarch64`
- DRM/KMS devices present (`/dev/dri/card0`)
- HDMI connected
- Network interface up with correct IP
- Control plane reachable (mgmt-api, SFU)
- Services running (vw-player, vw-wallagent)
- Firewall rules active
- Certificates valid and not expired
- Hardware decode functional

Run: `sudo /opt/videowall/bin/vw-smoketest.sh`

---

## 10. Pre-Build Verification

The `verify.sh` script checks build script compliance before and after builds:

```bash
# Pre-build: verify the script recipe
bash tools/pi-image/verify.sh

# Post-build: verify a built image
sudo bash tools/pi-image/verify.sh --image vw-tile-0-0.img
```

Pre-build checks include: Alpine aarch64 architecture, `vc4-kms-v3d` DRM
overlay, GPU memory allocation, GStreamer plugin packages, mpv hardware decode
flags, iptables hardening, watchdog config, SSH hardening, and NTP config.

---

## 11. Relationship to Platform Config

The Pi image builder operates independently of the YAML platform configuration
served by `vw-config`. The relationship is:

- `vw-config` defines walls, sources, and SFU room assignments
- The SFU controller (mgmt-api) creates Janus rooms based on derived metrics
- The `room_id` in the wall manifest should match the room allocated for this wall
- At runtime, `vw-wallagent` on the Pi polls `mgmt-api` for layout assignments
- `mgmt-api` queries `vw-config` for wall definitions and source availability

In production, query `GET vw-config:8006/api/v1/derived` to determine the
number of SFU rooms needed, then assign room IDs accordingly in wall manifests.
