# airgap-videowall — Module 2 (Media Plane)

This tarball contains **Module 2: Media Plane** (SFU + Gateway + Compositor) for the air-gapped multi-videowall platform.

Included:
- `charts/vw-sfu-janus/` — Janus WebRTC SFU (LoadBalancer via MetalLB)
- `services/gateway/` + `charts/vw-gw/` — GStreamer ingest gateway
- `services/compositor/` + `charts/vw-compositor/` — mosaic compositor
- `charts/vw-platform/` — umbrella chart (includes stubs for non-media subcharts)
- `docs/` — architecture + ports allowlist

> Runtime is air-gapped: mirror container images to `registry.local:5000` and deploy with `values-airgap.yaml`.
