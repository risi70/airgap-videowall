# airgap-videowall — Module 4 (Security, Observability, Ops & Documentation)

This package contains the complete deliverables for **Module 4**:
- Keycloak bootstrap
- Vault PKI setup + rotation helper
- Lab certificate generation
- Ansible roles + playbooks
- Observability Helm chart (Prometheus + Grafana + Loki/Promtail)
- Runbooks + platform documentation
- Sizing + test plans
- Integration test docker-compose
- Root Makefile targets (for module-4 concerns)

> Runtime is **air-gapped**: all images must be mirrored to `registry.local:5000` and all artifacts transferred via approved media.

## Quick start (lab)
```bash
make certs-init
make vault-init
make keycloak-init
make deploy
make test-integration
```

## Layout
- `security/keycloak/` — realm export + bootstrap
- `security/vault/` — PKI setup + rotate helper
- `security/certs/` — lab cert generator (offline)
- `security/ansible/` — Ansible inventory + roles + playbooks
- `charts/vw-observability/` — Helm chart
- `docs/` — architecture/security/operations/sizing/testing/ports allowlist + runbooks
- `tests/integration/` — docker-compose + smoke tests
