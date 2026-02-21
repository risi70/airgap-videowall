# Security

## Zone Model

- **Source Zone**: source agents, HDMI encoders (VLAN-isolated)
- **Media Core Zone** (Kubernetes): `vw-control`, `vw-media`, `vw-observability`
- **Display Zone**: wall controllers, tile players, big-screen players (VLAN-isolated)

Namespace isolation: default-deny NetworkPolicies with explicit allow-list only.

## Configuration Authority Trust Boundary

The `vw-config` service is a trust boundary — it controls what walls, sources,
and policies exist on the platform. Configuration changes are:

- **Schema-validated** against `config/schema.json` (JSONSchema Draft 2020-12)
- **Version-controlled** (semantic version in `platform.version`)
- **Signed** when delivered via offline bundles (Ed25519 signature + SHA-256 manifest)
- **Audited** — all config changes are logged to the audit chain with config hash
- **Validated** — rejected if concurrency limits are exceeded, IDs duplicate, or schema invalid

### Config delivery paths

| Path | Integrity | Authentication |
|------|-----------|---------------|
| Kubernetes ConfigMap | K8s RBAC + etcd encryption | Cluster admin |
| Helm values override | Git-signed + Helm release secret | CI/CD pipeline |
| Signed offline bundle | Ed25519 signature + SHA-256 | Bundle signing key |

### Hot reload

Config changes are detected by file watcher (5s poll on ConfigMap mount).
Services receive updated config via the vw-config API. No service restart required
for wall/source changes.

## mTLS

All service-to-service traffic uses mTLS via Vault PKI.

| From → To | Protocol | Auth |
|-----------|----------|------|
| Source Agent → mgmt-api | REST | mTLS client cert |
| Wall Controller → mgmt-api | REST | mTLS client cert |
| mgmt-api ↔ policy/audit/config | REST | mTLS |
| Prometheus → services | HTTPS scrape | mTLS (optional) |

### Certificate lifecycle
- Root CA: 10y TTL (Vault `pki/`)
- Intermediate CA: 5y TTL (Vault `pki_int/`)
- Server certs: max 90d TTL
- Client certs: max 30d TTL
- Pi endpoints: daily renewal via `vw-cert-renew.sh` (API or USB fallback)

## OIDC / Keycloak

- Operator authenticates to Keycloak `videowall` realm
- Client `vw-mgmt` issues RS256 JWT with realm roles + `clearance_tags` claim
- mgmt-api validates JWT offline (public key or JWKS)

## RBAC + ABAC (Config-Driven)

- **RBAC**: coarse-grained (admin/operator/viewer)
- **ABAC**: fine-grained, **fully driven by YAML config**:
  - Wall tags (from `walls[].tags` in config)
  - Source tags (from `sources[].tags` in config)
  - Operator tags (from `clearance_tags` JWT claim)
  - Policy rules (from `policy.rules` in config)

No policy rules are embedded in application code. All rules reference
config-defined attributes.

### Policy evaluation flow
1. Authenticate (OIDC JWT or mTLS identity)
2. Load wall + source tags from vw-config
3. Load operator tags from JWT claims
4. Evaluate policy rules from config (in order, first match wins)
5. Return allow/deny + matched rule + obligations

## PEP (Policy Enforcement Points)

- **mgmt-api**: enforces state changes, issues signed subscribe tokens
- **Compositor**: enforces input authorization (policy check before mosaic render)
- **Players**: accept only streams with valid subscribe tokens

## Audit Chain

Append-only, hash-chained audit records:
```
hash_i = SHA-256(prev_hash || canonical_json(event_i))
```

Config changes are audited with the config hash for traceability:
```json
{"action": "config.reload", "config_version": "1.1.0", "config_hash": "a1b2c3d4"}
```

Tamper detection: `GET /api/v1/audit/verify` walks the chain.

## Hardening Checklist

- Non-root containers; drop all Linux capabilities
- Read-only root FS; writable mounts explicitly declared
- No public dashboards; Grafana on operator VLAN only
- No internet egress at runtime; intra-zone flows only
- Pi endpoints: root locked, SSH key-only, iptables default-drop, BT/WiFi disabled
