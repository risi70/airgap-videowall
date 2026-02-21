# Ports allowlist / matrix (airgap-videowall)

> Complete this table with site-specific CIDRs and external boundary firewall rules.
> Kubernetes **NetworkPolicies** provided by the charts implement the *in-cluster* portion.

| Zone | Component | Port(s) | Proto | Direction | Peer / Notes |
|---|---|---:|---|---|---|
| Source Zone | VDI sources (e.g., RTSP endpoints) | 554 | TCP | Ingress to Source | From `vw-gw` / `vw-compositor` egress |
| Source Zone | HDMI-to-IP encoders (SRT) | 1024-65535 (as configured) | UDP | Ingress to Source | From `vw-gw` / `vw-compositor` |
| Media Core (vw-media) | **vw-gw** (gateway API) | 8004 | TCP | Ingress to `vw-gw` | From `vw-mgmt-api` |
| Media Core (vw-media) | **vw-gw** egress | 554, 80, 443, SRT ports | TCP/UDP | Egress from `vw-gw` | To Source Zone IP ranges |
| Media Core (vw-media) | **vw-compositor** (API) | 8005 | TCP | Ingress | From `vw-mgmt-api` |
| Media Core (vw-media) | **vw-compositor** → policy service | 8002 | TCP | Egress | To `vw-policy` |
| Media Core (vw-media) | **vw-sfu-janus** HTTP | 8088 | TCP | Ingress | From `vw-mgmt-api` and Display Zone clients |
| Media Core (vw-media) | **vw-sfu-janus** WebSocket | 8188 | TCP | Ingress | From Display Zone clients |
| Media Core (vw-media) | **vw-sfu-janus** RTP | 20000-20200 | UDP | Ingress/Egress | RTP/RTCP between Janus and clients (no STUN/TURN; LAN ICE Lite) |
| Control Plane (vw-control) | Keycloak | 8080/8443 | TCP | Ingress | Operators / internal services |
| Control Plane (vw-control) | Vault | 8200 | TCP | Ingress | Internal services (mTLS cert issuance) |
| Control Plane (vw-control) | PostgreSQL | 5432 | TCP | Ingress | From control/media services that persist data |
| Observability (vw-obs) | Prometheus | 9090 | TCP | Ingress | Operators / Grafana |
| Observability (vw-obs) | Grafana | 3000 | TCP | Ingress | Operators |
| Display Zone | Tile player / decoder | (varies) | TCP/UDP | Egress | To Janus 8088/8188 and RTP range |

## Notes

- Use **default deny** namespace policies (recommended) and then per-app allowlists as shipped.
- For air-gapped systems, ensure no egress to Internet at the node/firewall level; NetworkPolicy alone is not a perimeter control.
