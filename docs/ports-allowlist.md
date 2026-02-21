# Ports Allow-List (Matrix)

> Baseline for firewall and NetworkPolicy allow-listing.
> Adjust to your implementation. Source/wall/stream counts are config-driven.

| From | To | Zone | Proto | Port | Purpose |
|---|---|---|---|---:|---|
| Operator VLAN | Grafana | Media Core | TCP | 3000 | Dashboards |
| Operator VLAN | Keycloak | Media Core | TCP | 8080/8443 | OIDC login |
| Operator VLAN | Vault | Media Core | TCP | 8200 | PKI admin |
| Wall Controller | mgmt-api | Display→Core | TCP | 8443 | Heartbeat, layout, tokens |
| Wall Controller | vw-config | Display→Core | TCP | 8006 | Fetch wall config |
| Source Agent | mgmt-api | Source→Core | TCP | 8443 | Register, health |
| HDMI Encoder | Gateway | Source→Core | TCP/UDP | 554/9000-9100 | RTSP/SRT ingest |
| Gateway | SFU | Core internal | UDP/TCP | 10000-20200 | Media forwarding |
| SFU | Players | Core→Display | UDP/TCP | 10000-20200 | Media to endpoints |
| mgmt-api | vw-config | Core internal | TCP | 8006 | Fetch wall/source config |
| mgmt-api | policy | Core internal | TCP | 8001 | Policy evaluation |
| mgmt-api | audit | Core internal | TCP | 8002 | Audit logging |
| compositor | policy | Core internal | TCP | 8001 | Input authorization |
| compositor | vw-config | Core internal | TCP | 8006 | Fetch bigscreen config |
| Prometheus | all services | Obs→Core | TCP | 8443/metrics | Scrape |
| Promtail | Loki | Obs internal | TCP | 3100 | Push logs |
| Grafana | Prometheus | Obs internal | TCP | 9090 | Query metrics |
| Grafana | Loki | Obs internal | TCP | 3100 | Query logs |
| Services | PostgreSQL | Core internal | TCP | 5432 | DB |
