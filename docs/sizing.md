# Sizing (baseline assumptions)
Assumptions:
- W=4 walls (2×24-tile 1080p; 2×2-screen 4K)
- Sources=28 (20 VDI + 8 HDMI encoders)
- N=64 max concurrent streams
- Two latency classes: interactive (sub-second) and broadcast-grade (seconds)
- Typical codecs: H.264 baseline/high; optional H.265 for 4K where supported

## Sizing table (rule-of-thumb)

| Component | CPU | RAM | Network | Storage | Notes |
|---|---:|---:|---:|---:|---|
| vw-mgmt-api (HA x2) | 2 vCPU each | 2–4 GB | <200 Mbps | negligible | AuthZ, orchestration, token issuance |
| vw-policy (HA x2) | 1–2 vCPU each | 1–2 GB | <100 Mbps | negligible | ABAC evaluation, cache hot paths |
| vw-audit (HA x2) | 1–2 vCPU each | 2–4 GB | <100 Mbps | 50–200 GB | audit chain + retention/export |
| PostgreSQL 15 (HA) | 4–8 vCPU | 16–32 GB | <200 Mbps | 200–500 GB | depends on audit retention + config history |
| Janus SFU | 8–24 vCPU | 8–16 GB | 2–10 Gbps | negligible | dominated by number of forwarded streams |
| Gateway (GStreamer) | 8–32 vCPU | 8–16 GB | 2–10 Gbps | negligible | ingest fan-in + transcoding (avoid if possible) |
| Compositor | 16–64 vCPU or GPU | 16–64 GB | 2–10 Gbps | negligible | mosaic rendering cost driver |
| Prometheus | 2–4 vCPU | 4–8 GB | <200 Mbps | 50–200 GB | retention 7d baseline |
| Grafana | 1 vCPU | 1–2 GB | low | low | operator access only |
| Loki | 2–8 vCPU | 4–16 GB | <200 Mbps | 100–500 GB | depends on log volume/retention |
| Promtail | ~0.1 vCPU/node | ~100 MB/node | low | low | daemonset |

## Dominant cost drivers
1. **SFU egress**: total forwarded bitrate = sum(stream bitrate × subscribers).
2. **Compositor**: pixel throughput (tiles × resolution × fps) dominates.
3. **Gateway transcoding**: avoid if possible; prefer pass-through.

## Raspberry Pi 4/5 feasibility (decoder)
Constraints:
- Pi 4: limited 4K decode depending on profile; RAM 2–8 GB.
- Pi 5: improved CPU/GPU; still constrained IO and thermal headroom.

### Test plan (decoder)
1. Hardware: Pi 4 (8 GB) and Pi 5 (8 GB), official PSU, heatsink/fan.
2. OS: minimal Linux with KMS/DRM; mpv with hwdec enabled.
3. Streams:
   - 1080p30 H.264 6–8 Mbps
   - 1080p60 H.264 8–12 Mbps
   - 4K30 H.265 15–25 Mbps (if supported end-to-end)
4. Measure:
   - CPU/GPU utilization, dropped frames
   - end-to-end latency (glass-to-glass)
   - stability over 8h
5. Success criteria:
   - <1% dropped frames
   - steady-state temperature below throttling
   - latency class met for intended use

## TBS HDMI encoder integration checklist
- Put encoders into **Source Zone VLAN**; deny lateral movement.
- Firewall allow-list: encoder → gateway only (RTSP/SRT ports).
- Disable unused services (web UI, telnet/ssh) or restrict to operator VLAN.
- Unique credentials per encoder; store in Vault.
- Validate RTSP/SRT URLs and rotate credentials.
