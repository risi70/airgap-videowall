# Sizing

> **Dynamic sizing — all values derived from `config/examples/platform-config.yaml`**
>
> Wall counts, tile counts, source counts, and concurrency limits are
> **declaratively configured via YAML**. The sizing model scales dynamically
> based on the current configuration. There are no hardcoded assumptions.

## Configuration-Driven Sizing

| Parameter | Configured In | Example Value | Impact |
|-----------|--------------|---------------|--------|
| Wall count (W) | `walls[]` array | 4 | SFU rooms, compositor pipelines |
| Tiles per wall | `walls[].grid.rows × cols` | 24 (6×4) | SFU egress, endpoint count |
| Big-screens | `walls[].type: bigscreen` | 2 × dual-4K | Compositor GPU load |
| Source count (N) | `sources[]` array | 28 (20 VDI + 8 HDMI) | Gateway ingest workers |
| Max concurrent | `platform.max_concurrent_streams` | 64 | Cluster-wide stream cap |
| Codec policy | `platform.codec_policy` | tiles: h264, mosaics: hevc | Decode/encode CPU/GPU |

### Derived metrics (computed by vw-config)

These are recalculated automatically when the YAML config changes:

- **total_tiles** = Σ(rows × cols) for all tile-walls
- **total_screens** = Σ(screens) for all bigscreen-walls
- **sfu_rooms_needed** = count of tile-walls
- **mosaic_pipelines_needed** = count of bigscreen-walls
- **estimated_bandwidth_gbps** = (tiles × 6Mbps + screens × 15Mbps + source ingress) / 1000
- **concurrency_headroom** = max_concurrent_streams − total_endpoints

Use `POST /api/v1/config/dry-run` to simulate before applying.

## Component Sizing Table

> Scale CPU/RAM proportionally to the configured wall + source counts.

| Component | Base (W≤2) | Reference (W=4, N=64) | Scale Factor |
|-----------|-----------|----------------------|-------------|
| vw-config | 0.1 vCPU, 128 MB | 0.1 vCPU, 128 MB | Constant |
| vw-mgmt-api (×2) | 1 vCPU, 2 GB each | 2 vCPU, 4 GB each | +1 vCPU per 2 walls |
| vw-policy (×2) | 1 vCPU, 1 GB each | 2 vCPU, 2 GB each | +0.5 vCPU per 10 sources |
| vw-audit (×2) | 1 vCPU, 2 GB each | 2 vCPU, 4 GB each | Storage grows with retention |
| PostgreSQL (HA) | 4 vCPU, 16 GB | 8 vCPU, 32 GB | +50 GB storage per wall-year |
| Janus SFU (×2) | 4 vCPU, 4 GB each | 12 vCPU, 8 GB each | Linear with tile count |
| Gateway | 4 vCPU, 4 GB | 16 vCPU, 8 GB | Linear with source count |
| Compositor (GPU) | 1 GPU, 8 GB | 1 GPU, 32 GB | +1 GPU per 2 bigscreen walls |
| Prometheus | 2 vCPU, 4 GB | 4 vCPU, 8 GB | +1 GB per 50 endpoints |

## Dominant Cost Drivers

1. **SFU egress**: total_tiles × stream_bitrate × subscriber_fanout
2. **Compositor GPU**: pixel throughput (tiles × resolution × fps) per bigscreen wall
3. **Gateway transcoding**: avoid if possible; prefer codec-compatible pass-through

## Bandwidth Estimate Formula

```
Total ≈ (tile_walls × tiles_per_wall × 6 Mbps)
      + (bigscreen_walls × screens × 15 Mbps)
      + Σ(source.bitrate_kbps) / 1000
```

## Raspberry Pi 4/5 Decoder Sizing

| Stream Type | Pi 4 (v4l2m2m) | Pi 5 (v4l2request) |
|------------|----------------|-------------------|
| 1080p30 H.264 6–8 Mbps | ✅ <30% CPU | ✅ <15% CPU |
| 1080p60 H.264 8–12 Mbps | ⚠ 40–60% CPU | ✅ <25% CPU |
| 4K30 H.265 15–25 Mbps | ❌ Not reliable | ⚠ 50–70% CPU |

### Test plan (decoder)
1. Hardware: Pi 4 (8 GB) + Pi 5 (8 GB), official PSU, heatsink/fan
2. OS: Alpine Linux aarch64, KMS/DRM, mpv with hwdec
3. Measure: CPU/GPU util, dropped frames, e2e latency, 8h stability
4. Success: <1% drops, below thermal throttle, latency class met
