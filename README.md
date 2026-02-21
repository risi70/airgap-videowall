# Module 3 — Endpoint Agents & Offline Bundle System

This bundle contains non-Kubernetes endpoint agents (Source Zone + Display Zone) and offline update tooling.

## Layout

- agents/wallctl/        Wall controller agent (systemd service)
- agents/tile-player/   Tile player wrapper (systemd template unit per tile)
- agents/big-player/    Big screen player wrapper
- agents/vdi-encoder/   VDI capture+encode (GStreamer) with /healthz and /metrics
- agents/sourcereg/     Source registration + heartbeat
- tools/bundlectl/      Offline signed bundle CLI (tar.zst + ed25519)
- scripts/              Image mirroring + offline deps + rollout/rollback

## Install layout (suggested)

- /opt/videowall/agents/...          (code)
- /etc/videowall/<agent>/config.yaml (config)
- /etc/videowall/pki/*               (mTLS)
- /var/lib/<agent>                   (state)

## Notes

- All HTTP calls assume **mTLS** to mgmt/health endpoints.
- Tile player/big player commands are placeholders; adapt stream URL templates to your SFU/gateway wiring.
