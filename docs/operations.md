# Operations (Day-2)

## Backups
- PostgreSQL:
  - nightly `pg_dump` + weekly base backup (WAL as applicable)
- Vault:
  - periodic snapshot (or raft snapshot)
- Bundle exports:
  - export layout + policy + manifests into signed bundles

## Restore
1. Restore PostgreSQL into a new instance.
2. Restore Vault snapshot (unseal + verify).
3. Re-deploy platform via Helm/ArgoCD.
4. Import bundle exports and validate audit chain.

## Monitoring / alerting
- Prometheus scrapes:
  - mgmt-api, policy, audit, health, SFU, gateway, compositor
- Loki:
  - cluster and service logs (Promtail DS)
- Grafana dashboards:
  - videowall-overview
  - videowall-alerts

## Common tasks
- Certificate rotation: `docs/runbooks/rotate-certs.md`
- Apply bundle: `docs/runbooks/apply-bundle.md`
- Incident response: `docs/runbooks/incident-*.md`
