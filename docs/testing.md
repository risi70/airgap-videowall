# Testing

## Unit tests (summary)
- Policy engine:
  - role checks, tag taxonomy, deny-by-default
  - obligations and token claims
- Bundle tooling (bundlectl):
  - manifest generation, signing, verification
- Audit chain:
  - append, verify, tamper detection

## Integration test (docker-compose)
Location: `tests/integration/`
- Brings up: postgres + vw-mgmt-api + vw-policy + vw-audit + vw-health
- Runs curl-based smoke tests:
  - create wall
  - create source
  - evaluate policy allow/deny
  - issue subscribe token
  - append audit record
  - verify audit chain

Run:
```bash
make test-integration
```

## Load test plan
- SFU:
  - synthetic publishers up to N=64
  - verify packet loss and session stability
- Gateway:
  - ingest throughput per encoder class
  - verify CPU saturation thresholds
- Compositor:
  - frame time under full mosaic load
  - verify 30fps target: frame time <= 33ms

## Security checks
- Helm lint:
  - `make lint`
- Offline image scan:
  - `make security-scan` (Trivy DB mirrored; no internet at runtime)
- Config audit:
  - detect plaintext secrets in repo
  - ensure NetworkPolicies deny-by-default

## Pi test plan
See `docs/sizing.md` for detailed steps and success criteria.
