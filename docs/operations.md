# Operations (Day-2)

## Dynamic Configuration Model

All wall counts, source counts, codec policies, and ABAC rules are declaratively
configured via YAML. Changes do not require code modifications or redeployments.

### Configuration lifecycle

```
Author YAML → Validate (dry-run) → Sign bundle → Transport → Apply → Audit
```

1. **Author**: edit `platform-config.yaml` (or Helm `values.yaml`)
2. **Validate**: `POST /api/v1/config/dry-run` with the YAML body
3. **Sign**: `bundlectl export --config ... --key ...` (for air-gap transfer)
4. **Transport**: USB drive / removable media to air-gapped enclave
5. **Apply**: `bundlectl import` → updates ConfigMap → vw-config file watcher detects change
6. **Audit**: config change logged with version + hash to audit chain

### Hot reload flow

```
ConfigMap updated → file watcher (5s poll) → vw-config validates →
  → emits new config to API consumers → services fetch updated walls/sources
```

Services using the config API:
- **mgmt-api**: reads wall + source definitions (no static DB seed)
- **policy**: evaluates ABAC rules from config-defined tags
- **SFU controller**: creates/deletes Janus rooms per tile-wall
- **compositor**: instantiates mosaic pipelines per bigscreen-wall
- **gateway**: spawns ingest workers per YAML-defined source
- **wall controller**: fetches tile assignments for its wall

### Change management procedure

1. Create a branch with the proposed config change
2. Run dry-run validation: `curl -X POST https://vw-config:8006/api/v1/config/dry-run -d @new-config.yaml`
3. Review derived metrics (endpoint count, concurrency, bandwidth estimate)
4. Approve and merge
5. For air-gap: sign and export bundle
6. Apply via ConfigMap update or bundle import
7. Verify: `GET /api/v1/config/version` returns new version + hash
8. Audit trail: `GET /api/v1/audit/export` includes config.reload event

## Backups

- **PostgreSQL**: nightly `pg_dump` + weekly base backup
- **Vault**: periodic raft snapshot
- **Platform config**: version-controlled YAML (git is the source of truth)
- **Bundle exports**: signed bundles for disaster recovery

## Restore

1. Restore PostgreSQL into a new instance
2. Restore Vault snapshot (unseal + verify)
3. Re-deploy platform via Helm with config YAML
4. Import bundle exports and validate audit chain

## Monitoring / Alerting

- Prometheus scrapes: mgmt-api, policy, audit, health, config, SFU, gateway, compositor
- Loki: cluster and service logs
- Grafana dashboards: videowall-overview, videowall-alerts

### Config-specific alerts
- `ConfigReloadFailed` — vw-config could not parse/validate updated YAML
- `ConcurrencyNearLimit` — endpoint count > 80% of max_concurrent_streams
- `ConfigVersionStale` — config version unchanged for >30 days

## Common Tasks

| Task | Command |
|------|---------|
| View current config | `curl https://vw-config:8006/api/v1/config` |
| Validate config change | `curl -X POST .../config/dry-run -d @new.yaml` |
| Force reload | `curl -X POST .../config/reload` |
| Check config version | `curl .../config/version` |
| Rotate certificates | `docs/runbooks/rotate-certs.md` |
| Apply offline bundle | `docs/runbooks/apply-bundle.md` |
| Scale walls | Edit `walls[]` in config YAML, apply |
| Add sources | Edit `sources[]` in config YAML, apply |
