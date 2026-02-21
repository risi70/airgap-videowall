# Security

## Zone model and namespaces
- **Source Zone**: source agents and HDMI encoders.
- **Media Core Zone** (Kubernetes): `vw-control`, `vw-media`, `vw-obs`.
- **Display Zone**: wall controllers, tile players, big screen players.

Namespace isolation:
- Default deny with NetworkPolicies.
- Explicit allow only:
  - Prometheus scrape from `vw-obs` to targets
  - Grafana ingress from operator VLAN (or bastion)
  - mgmt-api to policy/audit/postgres
  - gateway/SFU/compositor internal flows

## mTLS
### Who talks to what
- Source Agent → mgmt-api: mTLS REST
- Wall Controller → mgmt-api: mTLS REST
- mgmt-api ↔ policy/audit: mTLS REST
- Prometheus → services: HTTPS scrape (optionally mTLS)

### Cert lifecycle
- Vault root CA (10y) → intermediate (5y) → server/client certs.
- Rotation:
  - server certs max TTL 90d
  - client certs max TTL 30d
- Distribution is performed via Ansible; services reload via systemd/K8s rollout.

## OIDC flow
- Operator authenticates to Keycloak `videowall` realm.
- Client `vw-mgmt` issues JWT with:
  - realm roles (admin/operator/viewer)
  - `clearance_tags` claim via protocol mapper
- mgmt-api validates JWT signature + issuer + audience and extracts claims.

## RBAC / ABAC
- RBAC provides coarse rights: admin/operator/viewer.
- ABAC constrains by tags:
  - user `clearance_tags`
  - source tags (e.g., `classification:confidential`, `origin:vdi`)
  - wall tags (e.g., `room:ops`, `audience:exec`)
Policy evaluation flow:
1. Authenticate (OIDC or mTLS identity).
2. Determine subject roles and attributes.
3. Evaluate policy predicates.
4. Return allow/deny + obligations (e.g., max fps, allowed codecs).

## PEP (Policy Enforcement Points)
- mgmt-api enforces all state changes and issues signed subscribe tokens.
- Compositor enforces input authorization before rendering mosaic.
- Players only accept streams with valid subscribe tokens.

## Audit
- Append-only audit records hashed into a chain:
  - record_i_hash = H(record_i || record_{i-1}_hash)
- Tamper detection via periodic `verify`:
  - Alert `AuditChainBroken` if verification fails.
- Retention: export bundles + DB backups, WORM storage where required.

## Hardening checklist
- Non-root containers; drop Linux capabilities.
- Read-only root FS; writable mounts explicitly declared.
- No public dashboards; Grafana only on operator VLAN.
- No internet egress at runtime; only whitelisted intra-zone flows.
