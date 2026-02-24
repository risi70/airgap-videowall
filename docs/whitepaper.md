# Air-Gapped Multi-Videowall Platform — Technical Whitepaper

**Version:** 1.0
**Date:** February 2026

---

## Abstract

This document describes the architecture, implementation status, and remaining
work for an enterprise-grade air-gapped multi-videowall platform. The system
manages dynamic content distribution across multiple video walls in
high-security environments where internet connectivity is unavailable. It
combines YAML-driven declarative configuration, Kubernetes orchestration,
WebRTC/SRT media transport, ABAC policy enforcement, tamper-evident audit
logging, and hardened Raspberry Pi endpoints into a cohesive platform that can
be deployed, operated, and updated entirely offline.

---

## 1. Platform Overview

### 1.1 Design Goals

The platform addresses a specific class of deployment: organizations that
operate multiple video walls across physically separated rooms, each displaying
different combinations of live video sources, under strict access control and
without any connection to the public internet.

Five principles guided every architectural decision:

1. **Declarative configuration** — all wall counts, source definitions, codec
   policies, and access rules are defined in YAML. Adding a wall or source
   requires no code changes.

2. **Security-first** — VLAN zone separation, mTLS everywhere, ABAC policy
   enforcement, token-gated media access, tamper-evident audit chains, and
   CIS-aligned endpoint hardening.

3. **Offline-capable** — local container registry, signed update bundles via
   USB, Vault PKI for certificate lifecycle, and NTP/DNS served internally.

4. **Hardware-accelerated decode** — Raspberry Pi 4/5 endpoints use V4L2
   kernel-native hardware decode with DRM/KMS direct rendering. No X11, no
   proprietary blobs.

5. **Modular development** — four independent tracks (control plane, media
   plane, endpoint agents, security/observability) that can be developed and
   deployed incrementally.

### 1.2 System Topology

```
┌─────────────────────────────────────────────────────────────────┐
│ Source Zone (VLAN 10)                                           │
│  VDI WebRTC Encoders        HDMI SRT/RTSP Encoders             │
└──────────┬──────────────────────────────┬──────────────────────┘
           │                              │
┌──────────┼──────────────────────────────┼──────────────────────┐
│ Media Core Zone (Kubernetes)            │                      │
│                                         │                      │
│  vw-control namespace                   │                      │
│    mgmt-api ↔ policy ↔ audit ↔ vw-config                      │
│                                         │                      │
│  vw-media namespace                     │                      │
│    Gateway → SFU (Janus) → Compositor                          │
│                                         │                      │
│  vw-observability namespace             │                      │
│    Prometheus / Grafana / Loki                                  │
└──────────┬──────────────────────────────┬──────────────────────┘
           │                              │
┌──────────┼──────────────────────────────┼──────────────────────┐
│ Display Zone (VLAN 30)                  │                      │
│  Tile Players (Pi 4)      Big-Screen Players (Pi 5)            │
│  Wall Controller agents                                        │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Implemented Features

### 2.1 Dynamic Configuration System (vw-config)

The Configuration Authority is the central feature that enables the platform
to scale from one to many walls purely through YAML changes.

**Implemented:**

- FastAPI service loading platform config from a YAML file validated against a
  JSONSchema Draft 2020-12 schema
- Twelve REST API endpoints: config retrieval (canonical JSON, raw YAML,
  version), wall/source/policy queries, dry-run validation, force reload, and
  derived metrics
- File watcher with configurable poll interval (default 5 seconds) that
  detects changes via SHA-256 hash comparison
- Last-known-good state preservation: invalid config updates are rejected, the
  previous valid config remains active, and the error is exposed in the health
  endpoint
- Canonical JSON serialization with deterministic hashing for change detection
  and audit trail
- Derived metrics computed from config: total tiles, SFU rooms needed,
  worst-case bandwidth, concurrency headroom
- Hot reload without container restart via Kubernetes ConfigMap projection
- Append-only JSONL event log recording every config-applied and
  config-rejected event
- Helm chart with inline and external ConfigMap delivery modes
- Comprehensive test suite: 44 unit tests, 13 integration tests, 13 dynamic
  config tests (70 total)

**Schema enforcement includes:** required fields (platform.max\_concurrent\_streams,
wall classification/latency\_class, source tags.classification), classification
enums (unclassified through top\_secret), conditional endpoint requirements for
SRT/RTSP/RTP sources, and duplicate ID rejection.

### 2.2 Control Plane Services

Seven microservices compose the control plane, all implemented as FastAPI
Python applications:

**mgmt-api** (992 lines) — the orchestration hub. Full CRUD for walls,
sources, and layouts. OIDC JWT verification (RS256) with role extraction.
Token minting for SFU subscribe (HS256, TTL-limited). Policy evaluation proxy.
Bundle import with HMAC verification. Audit event emission on all mutating
operations. PostgreSQL-backed persistence.

**policy** (271 lines) — ABAC rule engine. Evaluates access decisions using
tag-set operations: source-tags-subset-of-operator-tags,
source-tags-intersect-wall-tags, explicit allow-list, and default-deny.
Loads rules from vw-config API with fallback to local file. Tag enrichment
from mgmt-api at evaluation time.

**audit** (277 lines) — tamper-evident append-only log. Hash-chained events
where each entry's hash covers the previous hash and the canonical event
content (SHA-256). Verification endpoint walks the chain and reports integrity
status.

**compositor** (325 lines) — GStreamer mosaic pipeline builder. Accepts mosaic
definitions via API, builds GStreamer pipelines for multi-source composition,
enforces per-source policy checks before rendering. SRT output to big-screen
players.

**gateway** (384 lines) — media ingest bridge. Accepts SRT, RTSP, and RTP
input sources and re-publishes them. Includes an ffprobe-based prober endpoint
for validating source connectivity and capabilities before onboarding.

**health** (177 lines) — wall and source heartbeat aggregator. Receives
periodic health reports from wall controllers and source agents, tracks
online/offline status.

**vw-config** (737 lines) — the Configuration Authority described above.

### 2.3 Endpoint Agents

Six agents handle the Display Zone and Source Zone:

**wallctl** (314 lines) — per-wall controller running on a management Pi or
the first tile. Polls mgmt-api for layout assignments, manages tile player
lifecycle, caches subscribe tokens with TTL-aware refresh, implements failover
rules (max retries, retry delay, fallback to safe-slate image), and sends
heartbeats.

**tile-player** (77 lines) — per-tile kiosk player wrapping mpv with DRM/KMS
hardware-accelerated decode. Receives stream URL and token from wallctl.

**big-player** (70 lines) — compositor output viewer for big-screen walls.
Plays SRT streams directly.

**sourcereg** (117 lines) — source registration agent. Self-registers with
mgmt-api on startup, sends periodic heartbeats with source health status.
Uses mTLS for all communication.

**vdi-encoder** (188 lines) — screen capture encoder for VDI workstations.
GStreamer pipeline capturing display output and encoding to H.264 for SRT or
RTP transport.

**\_common** (101 lines) — shared mTLS-aware HTTP client and config loader used
by all agents.

### 2.4 Kubernetes Infrastructure

The platform deploys on Kubernetes with Helm charts for every component:

- **15 Helm charts** covering all services, SFU, compositor, gateway,
  observability stack, Vault, and the umbrella platform chart
- **Namespace separation**: vw-control, vw-media, vw-observability with
  default-deny NetworkPolicies
- **Per-chart NetworkPolicies** with explicit allow-list ingress/egress rules
- **Vault HA StatefulSet** with Raft consensus (3 replicas) and PKI bootstrap
  job
- **Janus SFU** with token-authenticated VideoRoom, 2 replicas, and
  PodDisruptionBudget
- **Observability stack**: Prometheus, Grafana (with videowall dashboards),
  Loki, and Promtail
- **Air-gap values overlay** (`values-airgap.yaml`) redirecting all image
  pulls to `registry.local:5000`

### 2.5 Security Stack

**Authentication:** Keycloak OIDC (RS256 JWT) for operators. Vault PKI as sole
certificate authority. mTLS on all inter-service communication. Token-gated SFU
access (HS256, short-lived, policy-checked).

**Authorization:** Tag-based ABAC with classification levels (unclassified
through top\_secret), mission tags, and configurable rules. Policy evaluated on
every stream subscription and compositor input.

**Audit:** Hash-chained append-only log with SHA-256 integrity. All mutating
operations logged. Verification API for chain integrity checks. Signed export
for offline archival.

**Network:** Three-zone VLAN separation. Kubernetes default-deny
NetworkPolicies. Documented port allowlist. Ansible playbooks for VLAN
configuration on switches.

**Endpoints:** Alpine Linux with locked root, SSH key-only access, iptables
default-drop firewall, disabled Wi-Fi/Bluetooth via rfkill, hardware watchdog,
and log rotation for SD card endurance.

### 2.6 Offline Operations

**Container registry:** `mirror-images.sh` script pulls, saves, loads, and
retags all container images for air-gapped transfer.

**Config bundles:** `bundlectl` CLI with export, verify, import, and diff
subcommands. Ed25519 signatures, tar.zst compression, per-file SHA-256
manifests.

**Pi updates:** `vw-offline-update.sh` applies signed bundles from USB with
automatic rollback on failure.

**Certificate rotation:** `vw-cert-renew.sh` with API-first and USB-fallback
modes, 7-day renewal threshold.

**Deployment:** Ring-based rollout script (pilot wall → remaining walls →
full cluster) with automated rollback.

### 2.7 Pi Image Builder

A complete SD card image factory producing ready-to-flash Alpine Linux images
for Raspberry Pi 4 and 5. Builds one image per tile with baked-in network
config, certificates, agents, and hardware-decode player. Batch mode generates
all images for an entire wall from a YAML manifest with deterministic IP
assignment.

See `docs/pi-image-builder.md` for the full configuration reference.

### 2.8 Documentation

- Architecture overview with control flow diagrams
- Security model documentation
- Operational runbooks (8): add wall, onboard source, apply bundle, rotate
  certs, install air-gap, incident response (stream loss, wall offline),
  rollback
- Sizing guide
- Port allowlist
- Testing guide
- Deployment guide for existing clusters
- Dynamic config compliance checklist
- Architecture completeness report against 36 normative requirements

---

## 3. Maturity Assessment

### 3.1 What Works End-to-End

The following paths are fully implemented with tests:

1. **Config lifecycle**: edit YAML → vw-config validates and serves → consumers
   query API → hot reload on ConfigMap update → last-known-good on invalid
   change. Covered by 70 automated tests.

2. **Pi image build**: wall manifest YAML → batch builder → per-tile images
   with network, certs, agents, and player. Pre-build and post-build
   verification scripts.

3. **Operator authentication**: Keycloak OIDC → RS256 JWT → mgmt-api role
   extraction → ABAC policy evaluation.

4. **Policy enforcement**: operator tags + source tags + wall tags →
   set-intersection rules → allow/deny decision. Five unit tests.

5. **Audit chain**: mutating operation → hash-chained event → verification
   endpoint → integrity report.

6. **Media ingest**: SRT/RTSP/RTP source → gateway probe → ingest pipeline →
   SRT output. GStreamer pipeline generation is tested.

7. **Offline bundle**: bundlectl export → Ed25519 sign → USB transfer → import
   → verify → apply.

### 3.2 What Works at Service Level but Lacks Integration

These components are individually implemented but not yet wired together in the
data path:

1. **~~SFU room auto-creation~~** *(partially resolved)*: vw-config computes
   `sfu_rooms_needed` and the new reconciliation loop seeds wall definitions
   into the mgmt-api database.  Automatic Janus room creation from these
   records is a remaining integration step.

2. **~~Config-driven reconciliation~~** *(resolved)*: adding a wall or source
   in the YAML config now automatically creates or updates the corresponding
   database record in mgmt-api via the reconciliation loop. Compositor
   pipeline and gateway ingest creation from reconciled records are remaining
   integration steps.

3. **~~Compositor policy payload~~** *(resolved)*: the compositor now sends a
   well-formed `EvalRequest` to the policy service with `wall_id`,
   `source_id`, `operator_id`, `operator_roles`, and `operator_tags`. The
   response is checked via `data.get("allowed")`.

---

## 4. Known Gaps and Remaining Work

### 4.1 Priority 0 — Blocking Bugs (all resolved)

The following three bugs were identified during the Phase 2 compliance review
and have since been fixed in the codebase. They are listed here for
traceability.

| Issue | Location | Status |
|-------|----------|--------|
| Missing `import re` | `tools/bundlectl/bundlectl.py` | ✅ Fixed — `re`, `shutil`, `subprocess` all imported (lines 11–13) |
| Missing `import shutil` | `agents/wallctl/vw_wallctl.py` | ✅ Fixed — `shutil` imported (line 9) |
| Compositor policy payload mismatch | `services/compositor/app/policy.py` | ✅ Fixed — payload matches `EvalRequest` schema; checks `data.get("allowed")` |

Verification: `bundlectl` export/verify/diff roundtrip passes (3 Ed25519
tests + 3 HMAC-fallback tests). `wallctl` safe-slate path resolves
`shutil.which("fbi")` without error. Compositor policy sends `wall_id`,
`source_id`, `operator_id`, `operator_roles`, `operator_tags` as required.

### 4.2 Priority 1 — Architecture Gaps

**Gateway WebRTC republish (REQ-COMP-005):** The gateway outputs SRT only.
There is no `webrtcbin` pipeline to publish into Janus VideoRoom. This means
HDMI encoder sources cannot reach tile players through the SFU. Either a
WebRTC publish pipeline or a Janus Streaming Plugin SRT ingest path is needed.

**VDI encoder WebRTC mode (REQ-COMP-003):** The `--output-mode webrtc` path
ends with a placeholder `fakesink`. Actual webrtcbin pipeline with Janus
signaling is not implemented.

**~~Config reconciliation loop~~** *(resolved)*: mgmt-api now includes a
reconciliation module (`app/reconcile.py`) that polls `vw-config` for hash
changes and upserts walls and sources into the PostgreSQL database.
Config-managed rows are tagged with `config:<id>` markers to distinguish
them from manually-created records. The reconciler runs on startup and every
30 seconds (configurable via `VW_RECONCILE_INTERVAL_S`). A manual trigger is
available at `POST /api/v1/config/reconcile`. All changes emit audit events.

**Audit signed export:** The audit service has a query endpoint but no signed
JSONL export endpoint. The mgmt-api similarly lacks an export proxy.

### 4.3 Priority 2 — Hardening Gaps

**Janus replicas:** Default `replicaCount` is 2 (corrected from 1), with a
PodDisruptionBudget. However, Janus VideoRoom state is not replicated across
instances — a room exists on one pod only. Session-aware load balancing or a
shared room registry is needed for true HA.

**Compositor StatefulSet:** Currently deployed as a Deployment. GPU pipeline
state benefits from stable pod identity (StatefulSet) and explicit GPU
resource requests (`nvidia.com/gpu` or similar).

**MetalLB services:** The gateway and compositor use ClusterIP but need
LoadBalancer type for external SRT ingest from the Source Zone.

**Token subscribe audit logging:** Token mint/deny events are not yet logged
to the audit chain.

### 4.4 Priority 3 — Functional Completeness

**UI enhancements:** The web UI has most features implemented: source probe
button, audit chain verify and export buttons, and full bundle management
(export, import with ring selection, and client-side diff). The remaining gap
is a visual drag-and-drop layout editor — layouts are currently created via
a JSON textarea for `grid_config`.

**Sizing documentation:** The sizing guide has placeholder numbers. It should
reflect measured values for the W=4, N=64 reference deployment.

**Integration test coverage:** The test suite covers vw-config thoroughly but
lacks end-to-end integration tests exercising the full media path
(source → gateway → SFU → tile player) and the full auth path
(Keycloak → mgmt-api → policy → token → SFU).

---

## 5. Technology Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Services | Python 3.12, FastAPI, Pydantic | Rapid development, strong typing, async support |
| Media | GStreamer 1.x, mpv | Broad codec/protocol support, hardware decode |
| SFU | Janus Gateway | Open-source WebRTC SFU with VideoRoom plugin |
| Database | PostgreSQL 15 | Mature, air-gap friendly, no cloud dependency |
| Auth | Keycloak (OIDC), Vault (PKI) | Standards-based, offline-capable |
| Orchestration | Kubernetes, Helm 3 | Declarative deployment, rollback, scaling |
| Observability | Prometheus, Grafana, Loki | Full stack metrics, dashboards, log aggregation |
| Endpoints | Alpine Linux 3.20 (aarch64) | Minimal footprint, fast boot, low SD wear |
| Hardware | Raspberry Pi 4/5 | V4L2 hardware decode, DRM/KMS, low cost, fanless |
| Config | YAML, JSONSchema Draft 2020-12 | Human-readable, machine-validatable |
| Bundles | tar.zst, Ed25519 | Compact, cryptographically signed |
| CI/testing | pytest, bash integration tests | 70+ automated tests |

---

## 6. Deployment Models

### 6.1 Online Cluster

Helm install on an existing Kubernetes cluster. Platform config delivered via
ConfigMap. Services communicate over the cluster network. Vault provides
automated certificate rotation.

### 6.2 Air-Gapped Cluster

Same Kubernetes deployment but with:

- Local container registry (`registry.local:5000`) seeded via `mirror-images.sh`
- Config and updates delivered via signed USB bundles
- Vault PKI running inside the cluster
- NTP, DNS, and package mirrors served locally
- No egress to public internet

### 6.3 Standalone Pi Fleet

For minimal deployments without Kubernetes, Pi endpoints can operate with
pre-configured static URLs pointing to bare-metal services. Wall manifests
define the entire deployment, and offline updates maintain the fleet.

---

## 7. Repository Metrics

| Metric | Value |
|--------|-------|
| Total services | 7 (mgmt-api, policy, audit, compositor, gateway, health, vw-config) |
| Total agents | 6 (wallctl, tile-player, big-player, sourcereg, vdi-encoder, common) |
| Helm charts | 15 |
| Service code | ~3,100 lines Python |
| Agent code | ~870 lines Python |
| Pi image builder | ~1,250 lines bash |
| Tests | 70+ automated (unit + integration) |
| Documentation | ~2,500 lines across 18 documents + 8 runbooks |
| Operational scripts | 6 (mirror, rollout, rollback, cert-renew, offline-update, smoketest) |
| Ansible playbooks | 4 (VLANs, wall controllers, tile players, source agents) |

---

## 8. Roadmap

### Near Term (next sprint)

1. ~~Fix P0 blocking bugs (bundlectl imports, wallctl shutil, compositor
   policy)~~ — **Done**
2. Implement gateway WebRTC republish pipeline (SRT→Janus via webrtcbin)
3. ~~Add config reconciliation loop in mgmt-api (vw-config → DB seed)~~ — **Done**
4. Add audit signed export endpoint

### Medium Term (next quarter)

5. Implement VDI encoder WebRTC mode (webrtcbin + Janus signaling)
6. Add end-to-end media path integration tests
7. UI enhancements (drag-and-drop layouts, probe, audit verify, bundle import)
8. Janus session-aware HA (shared room registry or sticky sessions)
9. Compositor StatefulSet conversion with GPU scheduling

### Long Term

10. Multi-cluster federation (config sync across air-gapped sites)
11. Automated sizing benchmarks with published reference numbers
12. Hardware security module (HSM) integration for key storage
13. FIPS 140-2 validated cryptography option
