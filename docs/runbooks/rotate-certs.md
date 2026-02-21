# Runbook: Rotate certificates

## Goals
- Re-issue server/client certificates from Vault PKI
- Distribute to endpoints and reload services with minimal downtime

## Steps
1. Ensure Vault PKI is configured:
   - `security/vault/setup-pki.sh`

2. Rotate and distribute:
   - `cd security/vault`
   - `./rotate-certs.sh --inventory ../ansible/inventory/hosts.yml --outdir ./rotated`

3. Verify:
   - `kubectl -n vw-control get pods`
   - `curl -k --cert ... --key ... https://mgmt-api.../health`
   - Grafana: no `AuditChainBroken`, no `WallOffline`.

## Expected outputs
- New cert files in `security/vault/rotated/`
- Ansible playbooks completed successfully

## Rollback
- Re-distribute previous bundle (kept as archived directory).
- Restart services.
