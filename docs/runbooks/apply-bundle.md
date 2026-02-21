# Runbook: Apply an update bundle

## Context
Bundles are signed, content-addressed tarballs staged ring-by-ring to reduce blast radius.

## Steps
1. Transfer bundle into Media Core Zone (approved media).
2. Verify signature:
   - `make bundle-verify BUNDLE=path/to/bundle.tar`
   - Expected: `signature OK`, `manifest OK`
3. Stage ring 0:
   - Apply to a single canary wall + subset of sources.
4. Verify:
   - Grafana dashboards stable, no elevated packet loss, no stream loss.
5. Stage ring 1 then ring 2.
6. Record in audit log:
   - Bundle hash, operator ID, change ticket.

## Rollback
- `make rollback`
- `helm rollback` affected releases
- Restore cached layouts from bundle export
