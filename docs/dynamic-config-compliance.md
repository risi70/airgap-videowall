# Dynamic Configuration — Compliance Checklist & Risk Assessment

## Compliance Checklist

| # | Requirement | Status | Evidence |
|---|------------|--------|----------|
| 1 | No hardcoded wall counts | ✅ PASS | Walls defined in `config/examples/platform-config.yaml`; vw-config loads dynamically |
| 2 | No hardcoded source counts | ✅ PASS | Sources defined in YAML; gateway spawns workers per config |
| 3 | No hardcoded SFU room definitions | ✅ PASS | `defaultRoomId: 0` in Janus values; rooms auto-created from config |
| 4 | No static concurrency limits in code | ✅ PASS | `platform.max_concurrent_streams` in YAML; guardrail enforced by vw-config |
| 5 | Schema-validated configuration | ✅ PASS | `config/schema.json` (JSONSchema Draft 2020-12) |
| 6 | Duplicate ID rejection | ✅ PASS | `load_config()` checks for duplicate wall/source IDs |
| 7 | Concurrency guardrail | ✅ PASS | Config rejected if endpoints > max_concurrent_streams |
| 8 | Dry-run simulation | ✅ PASS | `POST /api/v1/config/dry-run` returns metrics without applying |
| 9 | Hot reload without restart | ✅ PASS | File watcher (5s poll) on ConfigMap mount |
| 10 | ConfigMap delivery | ✅ PASS | `charts/vw-config/templates/configmap.yaml` |
| 11 | Signed bundle delivery | ✅ PASS | bundlectl export/import → ConfigMap update |
| 12 | Helm override support | ✅ PASS | `values.yaml` contains inline `platformConfig` |
| 13 | Audit trail for config changes | ✅ PASS | Config hash + version logged on reload |
| 14 | Derived metrics computed | ✅ PASS | `DerivedMetrics.compute()` — tiles, rooms, bandwidth, headroom |
| 15 | Policy rules config-driven | ✅ PASS | `policy.rules` in YAML; no embedded rules in code |
| 16 | Tag-driven ABAC | ✅ PASS | `walls[].tags`, `sources[].tags` drive policy evaluation |
| 17 | Integration test: 1→4 walls | ✅ PASS | `tests/test_dynamic_config.py` — 13 tests, all pass |
| 18 | Backwards compatible | ✅ PASS | Existing DB-driven walls/sources still work; config is additive |
| 19 | Air-gap compatible | ✅ PASS | No cloud dependencies; config via ConfigMap or USB bundle |
| 20 | Documentation updated | ✅ PASS | architecture.md, security.md, operations.md, sizing.md, ports-allowlist.md |

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **Config file corruption** | Low | High — no config = no walls | Validated on load; last-known-good persisted; rollback via git |
| **Concurrent config edits** | Medium | Medium — race condition | ConfigMap is atomic; vw-config uses file hash to detect changes |
| **Schema evolution** | Medium | Low — new fields ignored | JSONSchema `additionalProperties: false` catches typos; migration via versioned schemas |
| **Excessive wall count** | Low | Medium — resource exhaustion | `max_concurrent_streams` guardrail rejects invalid configs |
| **Config reload during stream** | Medium | Low — brief disruption | Hot reload does not interrupt existing streams; only new assignments use new config |
| **Stale config on Pi endpoints** | Medium | Low — plays old assignment | Wall controller polls on interval; restart picks up latest |
| **Bundle replay attack** | Low | Medium — old config applied | Config version is monotonic; audit chain links config hash |
| **ConfigMap size limit** | Low | Low — 1MB K8s limit | Typical config <50KB; very large deployments split sources into separate ConfigMaps |

## Migration Path (Backwards Compatibility)

Existing deployments can adopt the dynamic config model incrementally:

1. **Phase 1**: Deploy `vw-config` alongside existing services
   - Existing DB-driven walls/sources continue to work
   - vw-config serves config API in parallel
2. **Phase 2**: Services read walls/sources from vw-config API instead of DB
   - mgmt-api: `GET /api/v1/walls` proxied from vw-config
   - policy: rules loaded from vw-config instead of local `policy.yaml`
3. **Phase 3**: Remove static wall/source DB seeds
   - Config YAML is the single source of truth
   - DB stores only runtime state (layouts, audit events, tokens)

No breaking changes at any phase. Services that don't support the config API
continue to work with their existing configuration.
