# airgap-videowall

An **air-gapped, open-source multi-videowall platform** for high-security enclaves. Ingests heterogeneous video sources (VDI soft-encoders, HDMI-to-IP hardware encoders), distributes them via WebRTC SFU and GStreamer compositor, and renders them on configurable videowalls with RBAC-enforced source entitlements.

> **Runtime is fully air-gapped:** no Internet connectivity. All images are mirrored to a local registry, all updates transferred via signed bundles on approved media.

---

## Key Capabilities

- **4 independent videowalls** — 2× 24-tile 1080p (6×4 grid) + 2× dual-4K bigscreen — selecting from 28 sources
- **Dual media path** — sub-500 ms interactive latency via WebRTC SFU; 2–6 s broadcast-grade via SRT/compositor
- **Token-gated subscriptions** — policy-authorized SFU subscribe tokens (HS256 JWT, 5-min TTL) and compositor input checks
- **Tamper-evident audit** — hash-chained append-only log with verification API and signed JSONL export
- **mTLS everywhere** — HashiCorp Vault PKI (root → intermediate → short-lived certs); Keycloak OIDC for operators
- **Kubernetes-native** — MetalLB LoadBalancer, NetworkPolicy default-deny, PodDisruptionBudgets, GPU scheduling
- **Offline operations** — `vw-bundlectl` for Ed25519-signed configuration bundles; staged ring-based rollout

---

## Architecture

The platform follows a **Combined C+D** topology: Janus SFU for per-tile distribution + GStreamer compositor for large-screen mosaics, unified under a single control plane.

```
┌─────────────────────┐    ┌──────────────────────────────────────────┐    ┌──────────────────┐
│    Source Zone       │    │        Media Core Zone (Kubernetes)      │    │   Display Zone   │
│    VLAN 10           │    │                                          │    │   VLAN 30        │
│                      │    │  ┌─────────────┐  ┌──────────────────┐  │    │                  │
│  vw-vdi-encoder ─────┼──▶ │  │  Janus SFU  │  │  vw-mgmt-api    │  │    │  vw-wallctl      │
│  (20 VDI VMs)        │    │  │  (×2, PDB)  │  │  (OIDC+RBAC)    │◀─┼────│  (token cache,   │
│                      │    │  └──────┬──────┘  ├──────────────────┤  │    │   failover FSM)  │
│  HDMI Encoders ──────┼──▶ │  ┌─────┴──────┐  │  vw-policy       │  │    │       │          │
│  (8 SRT/RTSP)        │    │  │  vw-gateway │  │  (tag ABAC)      │  │    │  tile-player     │
│                      │    │  │  (WebRTC    │  ├──────────────────┤  │    │  (×48 per-tile)  │
│  vw-sourcereg ───────┼──▶ │  │  republish) │  │  vw-audit        │  │    │                  │
│  (register+heartbeat)│    │  └─────────────┘  │  (hash chain)    │  │    │  big-player      │
│                      │    │  ┌─────────────┐  ├──────────────────┤  │    │  (4K compositor   │
│                      │    │  │ Compositor  │  │  Vault PKI (×3)  │  │    │   output)        │
│                      │    │  │ (StatefulSet│  │  Keycloak OIDC   │  │    │                  │
│                      │    │  │  GPU, HEVC) │  │  PostgreSQL      │  │    │                  │
│                      │    │  └─────────────┘  └──────────────────┘  │    │                  │
└─────────────────────┘    └──────────────────────────────────────────┘    └──────────────────┘
```

**Full architecture specification:** [`docs/architecture-v2.md`](docs/architecture-v2.md)

---

## Repository Layout

```
agents/                     Display & Source zone agents (Python)
├── wallctl/                Wall controller — layout polling, token cache, failover FSM, safe-slate
├── vdi-encoder/            GStreamer screen capture → WebRTC/SRT publish + /healthz + /metrics
├── sourcereg/              Source registration + heartbeat via mTLS
├── tile-player/            Per-tile kiosk player (GStreamer/mpv)
├── big-player/             Compositor output viewer (4K)
└── _common/                Shared mTLS HTTP client, config helpers

services/                   Kubernetes microservices (FastAPI)
├── mgmt-api/               Central API — walls/sources/layouts CRUD, token mint, policy proxy,
│                           audit query/verify/export, gateway probe proxy, bundle import/export
├── audit/                  Standalone hash-chain logger — /ingest, /query, /verify, /export
├── policy/                 YAML-configurable ABAC engine with DB-backed tag enrichment
├── compositor/             GStreamer mosaic builder with per-source policy enforcement
├── gateway/                SRT/RTSP/RTP ingest with WebRTC republish to Janus + /probe (ffprobe)
├── health/                 Wall + source heartbeat aggregator
└── ui/                     SPA — dashboard, walls, sources, layouts, policy eval, audit, bundles

charts/                     Helm charts
├── vw-sfu-janus/           Janus SFU (Deployment ×2, PDB, ConfigMap, NetworkPolicy, LoadBalancer)
├── vw-compositor/          Compositor (StatefulSet, GPU nodeSelector, PDB, NetworkPolicy, LoadBalancer)
├── vw-gw/                  Gateway (Deployment, NetworkPolicy, LoadBalancer with SRT ports)
├── vw-vault/               Vault HA (StatefulSet ×3, Raft, RBAC, PKI bootstrap Job, NetworkPolicy)
├── vw-observability/       Prometheus + Grafana + Loki + Promtail (dashboards, alerts)
├── vw-platform/            Umbrella chart (default-deny NetworkPolicies, values-airgap.yaml)
├── audit/                  Audit service chart
├── mgmt-api/               Mgmt API chart
├── policy/                 Policy service chart
├── health/                 Health service chart
└── ui/                     UI chart

security/
├── vault/                  setup-pki.sh (root+intermediate CA, roles) + rotate-certs.sh
├── keycloak/               bootstrap.sh + videowall-realm.json
├── certs/                  Lab certificate generator (dev only)
└── ansible/                Playbooks: VLANs, wall-controllers, tile-players, source-agents
                            Roles: common, vlan-config, wall-controller, tile-player, source-agent

tools/
└── bundlectl/              Offline bundle CLI — export|verify|import|diff, Ed25519, tar.zst

scripts/
├── mirror-images.sh        Pull/save/load/retag for air-gapped image transfer
├── rollout.sh              Ring 0 (staging) → Ring 1 (pilot) → Ring 2 (production)
├── rollback.sh             Helm rollback helper
└── offline-dep-mirror.sh   Dependency mirror for pip/npm

tests/
├── unit/                   Policy engine tests (5 tests: admin bypass, tag matching, deny)
└── integration/            docker-compose smoke tests (postgres + all control-plane services)

docs/                       Architecture, security, operations, compliance, runbooks
```

---

## Quick Start (Lab)

### Prerequisites

- Docker + docker-compose (for integration tests)
- Kubernetes cluster (k3s/RKE2) with MetalLB (for full deployment)
- `gst-launch-1.0` with GStreamer plugins (bad, good, ugly) for media pipelines
- Python 3.11+ with pip
- Helm 3

### 1. Generate lab certificates

```bash
make certs-init
# or:
cd security/certs && bash generate-lab-certs.sh
```

### 2. Bootstrap Vault PKI

```bash
make vault-init
# or:
export VAULT_ADDR=http://localhost:8200
export VAULT_TOKEN=<root-token>
cd security/vault && bash setup-pki.sh
```

### 3. Bootstrap Keycloak

```bash
make keycloak-init
# or:
cd security/keycloak && bash bootstrap.sh \
  --url http://localhost:8080 \
  --admin-user admin --admin-pass admin \
  --realm-file videowall-realm.json
```

### 4. Deploy to Kubernetes

**Option A — Fresh air-gapped cluster:**

```bash
# Pull and save images
cd scripts && bash mirror-images.sh pull_all
bash mirror-images.sh save_all images.tar.zst

# Load and retag on air-gapped network
bash mirror-images.sh load_all images.tar.zst
bash mirror-images.sh retag_and_push

# Deploy
helm upgrade --install videowall charts/vw-platform \
  -f charts/vw-platform/values-airgap.yaml \
  --namespace vw-control --create-namespace
```

**Option B — Existing cluster with Vault + Keycloak + PostgreSQL:**

```bash
# Copy and customize the example values
cp charts/vw-platform/values-existing-cluster.yaml my-values.yaml
# Edit my-values.yaml: set your Vault address, Keycloak issuer, DB DSN, etc.

helm upgrade --install videowall charts/vw-platform \
  -f my-values.yaml \
  --namespace videowall --create-namespace
```

See **[`docs/deploy-existing-cluster.md`](docs/deploy-existing-cluster.md)** for step-by-step instructions including Vault role setup, Keycloak realm import, and per-service deployment.

### 5. Run tests

```bash
# Unit tests
make test-unit
# or: cd tests && python -m pytest unit/

# Integration tests
make test-integration
# or: cd tests/integration && bash run-tests.sh
```

---

## Air-Gap Operations

### Offline bundle workflow

```bash
# Export current config into a signed bundle
vw-bundlectl export \
  --config-dir /etc/videowall \
  --key /path/to/ed25519-private-key \
  --output bundle-2026-02-21.tar.zst

# Transfer bundle to air-gapped network via approved media

# Verify bundle integrity
vw-bundlectl verify \
  --bundle bundle-2026-02-21.tar.zst \
  --pubkey /path/to/ed25519-public-key

# Diff against current config
vw-bundlectl diff \
  --bundle bundle-2026-02-21.tar.zst \
  --config-dir /etc/videowall

# Import and stage by ring
vw-bundlectl import \
  --bundle bundle-2026-02-21.tar.zst \
  --pubkey /path/to/ed25519-public-key \
  --ring 0   # 0=staging, 1=pilot, 2=production
```

### Staged rollout

```bash
scripts/rollout.sh ring0   # Deploy to staging namespace
scripts/rollout.sh ring1   # Activate pilot wall layout
scripts/rollout.sh ring2   # Production rollout
scripts/rollback.sh        # Helm rollback on failure
```

---

## Security Model

| Layer | Mechanism | Implementation |
|-------|-----------|----------------|
| **Network** | VLAN segmentation + k8s NetworkPolicy default-deny | Ansible VLAN config + 3 namespace-level deny-all + 10 per-chart allow policies |
| **Identity (services)** | mTLS via Vault PKI | Vault Agent sidecar on all pods; `MTLSConfig` in agents |
| **Identity (operators)** | OIDC via Keycloak | RS256 JWT with roles + clearance tags |
| **Authorization** | Tag-based ABAC + RBAC | `vw-policy` engine: source tags ⊆ operator tags, wall∩source tags, explicit allow-list |
| **Media access** | Token-gated SFU subscribe | `POST /tokens/subscribe` → policy check → scoped HS256 JWT (5-min TTL) |
| **Compositor** | Per-input policy check | `evaluate_source_access()` calls policy service; fail-closed |
| **Audit** | Hash-chained append-only log | `SHA-256(prev_hash ‖ canonical_event)`; `/verify` API; signed export |
| **Supply chain** | Ed25519-signed bundles | `vw-bundlectl` with per-file SHA-256 manifest |

---

## Architecture Compliance

The project has been verified against 36 normative requirements from the v2 architecture specification:

| Category | Requirements | Status |
|----------|-------------|--------|
| Zone Security | 3 | ✅ 3/3 PASS |
| Authentication & PKI | 4 | ✅ 4/4 PASS |
| Policy Enforcement | 3 | ✅ 3/3 PASS |
| Audit | 4 | ✅ 4/4 PASS |
| Components | 6 | ✅ 6/6 PASS |
| Gateway | 1 | ✅ 1/1 PASS |
| High Availability | 4 | ✅ 4/4 PASS |
| Open Source | 2 | ✅ 2/2 PASS |
| Offline Operations | 3 | ✅ 3/3 PASS |
| Sizing | 2 | ✅ 2/2 PASS |
| User Interface | 4 | ✅ 4/4 PASS |
| **Total** | **36** | **✅ 36/36 PASS** |

Full verification details: [`docs/architecture-completeness-report.md`](docs/architecture-completeness-report.md)

---

## Documentation

| Document | Description |
|----------|-------------|
| [`docs/architecture-v2.md`](docs/architecture-v2.md) | Full reference architecture specification (v2.0) |
| [`docs/deploy-existing-cluster.md`](docs/deploy-existing-cluster.md) | Deploy into existing K8s with Vault + Keycloak + PostgreSQL |
| [`docs/architecture.md`](docs/architecture.md) | Architecture overview with Mermaid diagrams |
| [`docs/security.md`](docs/security.md) | Zone model, mTLS, OIDC, RBAC/ABAC, PEP, hardening |
| [`docs/sizing.md`](docs/sizing.md) | Reference sizing, Pi decoder test plan, encoder checklist |
| [`docs/operations.md`](docs/operations.md) | Day-2 operations: backups, restore, monitoring |
| [`docs/testing.md`](docs/testing.md) | Unit, integration, load, and security test plans |
| [`docs/ports-allowlist.md`](docs/ports-allowlist.md) | Firewall and NetworkPolicy port matrix |
| [`docs/architecture-completeness-report.md`](docs/architecture-completeness-report.md) | 36/36 requirement verification report |
| [`docs/phase2-compliance-report.md`](docs/phase2-compliance-report.md) | Compliance audit with remediation history |

### Runbooks

| Runbook | Description |
|---------|-------------|
| [`install-airgap.md`](docs/runbooks/install-airgap.md) | Air-gapped cluster bootstrap |
| [`add-wall.md`](docs/runbooks/add-wall.md) | Add a new videowall |
| [`onboard-source.md`](docs/runbooks/onboard-source.md) | Onboard a video source |
| [`rotate-certs.md`](docs/runbooks/rotate-certs.md) | Certificate rotation procedure |
| [`apply-bundle.md`](docs/runbooks/apply-bundle.md) | Apply a signed config bundle |
| [`rollback.md`](docs/runbooks/rollback.md) | Rollback a failed deployment |
| [`incident-stream-loss.md`](docs/runbooks/incident-stream-loss.md) | Troubleshoot stream loss |
| [`incident-wall-offline.md`](docs/runbooks/incident-wall-offline.md) | Troubleshoot wall offline |

---

## License

EUPL-1.2 — see [LICENSE](LICENSE)
