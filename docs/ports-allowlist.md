# Ports allow-list (matrix)

> This table is the baseline for firewall and NetworkPolicy allow-listing.
> Adjust to your exact implementation details.

| From | To | Zone | Direction | Proto | Port | Purpose |
|---|---|---|---|---|---:|---|
| Operator VLAN | Grafana | Media Core | Ingress | TCP | 3000 | Dashboards (restricted) |
| Operator VLAN | Keycloak | Media Core | Ingress | TCP | 8080/8443 | OIDC login/admin |
| Operator VLAN | Vault | Media Core | Ingress | TCP | 8200 | PKI issuance/admin |
| Wall Controller | mgmt-api | Display→Core | Egress | TCP | 8443 | Heartbeat, layout, token |
| Source Agent | mgmt-api | Source→Core | Egress | TCP | 8443 | Register, health |
| HDMI Encoder | Gateway | Source→Core | Egress | TCP/UDP | 554 / (SRT 9000-9100) | RTSP/SRT ingest |
| Gateway | SFU | Core internal | East/West | UDP/TCP | 10000-20000 | Media forwarding (WebRTC/RTP) |
| SFU | Players | Core→Display | Egress | UDP/TCP | 10000-20000 | Media to endpoints |
| Prometheus | all services | Obs→Core | Egress | TCP | 8443/metrics | Scrape metrics |
| Promtail | Loki | Obs internal | Egress | TCP | 3100 | Push logs |
| Grafana | Prometheus | Obs internal | Egress | TCP | 9090 | Query metrics |
| Grafana | Loki | Obs internal | Egress | TCP | 3100 | Query logs |
| Services | PostgreSQL | Core internal | Egress | TCP | 5432 | DB |
