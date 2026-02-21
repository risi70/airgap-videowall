# Runbook: Rollback procedure

## Steps
1. Identify the last known-good release revision:
   - `helm history <release> -n <ns>`
2. Roll back:
   - `helm rollback <release> <rev> -n <ns>`
3. Restore layout cache (if needed):
   - `kubectl -n vw-control apply -f backups/layout-cache.yaml`
4. Verify:
   - Wall heartbeat OK, streams recovered, audit chain OK

## Escalation
If rollback does not restore service:
- isolate faulty component via NetworkPolicy
- move all walls to static fallback layouts
