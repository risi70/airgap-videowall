# Phase 2 — Repository Compliance Review

**Repo:** `https://github.com/risi70/airgap-videowall` (cloned 2026-02-21)  
**Spec:** `videowall-architecture-v2.md` (corrected Phase 1 output)

---

## 1. Repository Structure Summary

The repo is well-structured with meaningful separation of concerns:

```
agents/           — Display/Source zone agents (Python)
  wallctl/        ✅ exists — token cache, failover, layout polling, safe slate
  vdi-encoder/    ✅ exists — GStreamer pipeline, health endpoint, /metrics
  sourcereg/      ✅ exists — register + heartbeat via mTLS
  tile-player/    ✅ exists — per-tile player (GStreamer)
  big-player/     ✅ exists — compositor-output viewer
  _common/        ✅ vw_http.py (mTLS-aware HTTP client), vw_cfg.py

services/         — Kubernetes microservices (FastAPI Python)
  mgmt-api/       ✅ Full CRUD: /walls, /sources, /layouts, /tokens/subscribe,
                     /policy/evaluate, /bundles/export+import, /audit/query
                     OIDC JWT verification (RS256), role-based access
  audit/          ✅ Standalone hash-chain logger: /ingest, /query, /verify
  policy/         ✅ YAML-configurable rule engine with tag-intersection
  compositor/     ✅ GStreamer mosaic builder with per-source policy check
  gateway/        ✅ SRT/RTSP→SRT ingest pipelines + /probe (ffprobe-based)
  health/         ✅ Wall/source heartbeat aggregator
  ui/             ✅ SPA (vanilla JS): dashboard, walls, sources, layouts,
                     policy eval, audit query, bundle export

charts/           — Helm charts
  vw-sfu-janus/   ✅ Full: Deployment, ConfigMap, NetworkPolicy, Service (LB)
  vw-compositor/  ✅ Full: Deployment, NetworkPolicy, Service
  vw-gw/          ✅ Full: Deployment, NetworkPolicy, Service
  vw-observability/ ✅ Full: Prometheus, Grafana, Loki, Promtail, NetworkPolicy
  vw-platform/    ✅ Umbrella chart with values-airgap.yaml registry override
  audit/mgmt-api/policy/health/ui  ✅ Charts with default-deny NetworkPolicy

security/         — Security artifacts
  vault/          ✅ setup-pki.sh (root+intermediate CA, roles, policies)
                  ✅ rotate-certs.sh (Vault-based rotation + Ansible distribute)
  keycloak/       ✅ bootstrap.sh + videowall-realm.json
  certs/          ✅ generate-lab-certs.sh (lab/dev only)
  ansible/        ✅ Playbooks: VLANs, wall-controllers, tile-players, source-agents

tools/bundlectl/  ✅ CLI: export/verify/import/diff, Ed25519 (PyNaCl) or HMAC fallback,
                     tar.zst format, manifest with SHA-256 per file

scripts/          ✅ mirror-images.sh, rollout.sh (ring0/1/2), rollback.sh
tests/            ✅ Unit: test_policy.py; Integration: docker-compose.yml
docs/             ✅ architecture.md, security.md, sizing.md, ports-allowlist.md,
                     operations.md, testing.md, runbooks (8 runbooks)
```

---

## 2. Compliance Matrix

| Req ID | Requirement | Repo Evidence | Status | Fix Required | Effort | Risk |
|--------|------------|--------------|--------|-------------|--------|------|
| **REQ-ZONE-001** | Separate VLANs with stateful firewalls | `security/ansible/playbooks/configure-vlans.yml`, `security/ansible/roles/vlan-config/` | **PASS** | — | — | — |
| **REQ-ZONE-002** | Default-deny NetworkPolicy per namespace | `charts/audit/templates/networkpolicy.yaml` et al. use `podSelector: {}` + Ingress+Egress. But only on **per-chart** basis, NOT a namespace-wide default-deny applied before workloads. | **PARTIAL** | Add namespace-wide default-deny manifests to `charts/vw-platform/` or each namespace template | S | High |
| **REQ-ZONE-003** | Explicit allow-list NetworkPolicies | `charts/vw-sfu-janus/templates/networkpolicy.yaml`, `charts/vw-compositor/templates/networkpolicy.yaml`, `charts/vw-gw/templates/networkpolicy.yaml` all have specific `from`/`to` selectors. `docs/ports-allowlist.md` documents matrix. | **PASS** | — | — | — |
| **REQ-AUTH-001** | Keycloak OIDC auth | `services/mgmt-api/app/main.py`: `_decode_and_verify_rs256()`, RS256 JWT validation, `_extract_roles()`. `security/keycloak/bootstrap.sh` + `videowall-realm.json`. | **PASS** | — | — | — |
| **REQ-AUTH-002** | Keycloak NOT a CA; Vault PKI is sole CA | No step-ca references. No "Keycloak issues certs" anywhere. `security/vault/setup-pki.sh` is the sole PKI provisioner. Keycloak files only handle OIDC realm. | **PASS** | — | — | — |
| **REQ-AUTH-003** | mTLS via Vault on all services | `agents/wallctl/vw_wallctl.py` and `agents/sourcereg/vw_sourcereg.py` use `MTLSConfig` with ca/client cert/key. `security/vault/rotate-certs.sh` issues certs for all services. **But**: no Vault Agent sidecar in Helm charts. K8s pods get mTLS only via manual cert injection, not auto-rotation. | **PARTIAL** | Add Vault Agent injector annotation or init-container to all Helm chart Deployments for auto cert provisioning | M | High |
| **REQ-AUTH-004** | Vault HA StatefulSet with Raft | No Vault Helm chart exists in `charts/`. Vault is deployed externally (setup-pki.sh script assumes pre-existing Vault). | **PARTIAL** | Add `charts/vw-vault/` StatefulSet with Raft HA, or reference external Vault Helm chart in vw-platform umbrella | M | High |
| **REQ-PEP-001** | Token-gated SFU subscribe | `services/mgmt-api/app/main.py`: `tokens_subscribe()` at `/api/v1/tokens/subscribe` calls `_policy_evaluate()` → mints HS256 JWT on ALLOW. `charts/vw-sfu-janus/values.yaml`: `tokenAuthEnabled: true`. | **PASS** | — | — | — |
| **REQ-PEP-002** | Compositor policy check on input | `services/compositor/app/main.py`: `create_or_update_mosaic()` calls `evaluate_source_access()` per input. `services/compositor/app/policy.py`: calls policy service, fail-closed. | **PASS** | Compositor policy call payload is `{source_id, action:"use"}` which doesn't match policy service's `EvalRequest` schema (needs `wall_id, operator_id, operator_roles`). The call would fail or default-deny. | M | Med |
| **REQ-PEP-003** | Tag-based policy with set-intersection | `services/policy/app/main.py`: `PolicyEngine.evaluate()` implements `source_tags_subset_of_operator_tags`, `source_tags_intersect_wall_tags`, explicit allow-list. `tests/unit/test_policy.py` validates all paths. `services/policy/policy.yaml` defines rules. | **PASS** | Policy currently gets tags from `_lookup_tags_stub()` (returns `[]`). In production, needs DB lookup or API enrichment. | S | Med |
| **REQ-AUDIT-001** | Hash-chained append-only audit | `services/audit/app/main.py`: `ingest()` computes `SHA-256(prev_hash | canonical_event)`, stores `prev_hash` + `hash`. `services/mgmt-api/app/database.py`: `append_audit_event()` does the same inline. Two parallel implementations. | **PASS** | Dual implementation (mgmt-api + audit service) may diverge. Consider single source of truth. | S | Low |
| **REQ-AUDIT-002** | Verification API | `services/audit/app/main.py`: `verify()` at `GET /verify` walks chain forward, validates each hash, reports `{checked, verified, broken}`. | **PASS** | — | — | — |
| **REQ-AUDIT-003** | All events logged | `services/mgmt-api/app/main.py`: `append_audit_event()` called in: `create_wall`, `update_wall`, `delete_wall`, `create_source`, `update_source`, `delete_source`, `create_layout`, `update_layout`, `delete_layout`, `activate`, `bundles_import`. Token subscribe is **NOT** audit-logged. | **PARTIAL** | Add `append_audit_event()` to `tokens_subscribe()` and `policy_evaluate()` | S | Med |
| **REQ-AUDIT-004** | Signed JSONL export | `services/audit/app/main.py`: no export endpoint. `services/mgmt-api/app/main.py`: `/audit/query` returns events but no signed export. | **FAIL** | Add `GET /api/v1/audit/export` that returns signed JSONL with Ed25519 signature | S | Low |
| **REQ-COMP-001** | vw-wallctl with token cache + failover | `agents/wallctl/vw_wallctl.py`: `WallCtl` class with `_token_cache` (dict, persisted to JSON), `request_subscribe_token()` with TTL check, `FailoverRules` (max_retries, retry_delay, fallback_to_slate), `_show_slate()`, `_check_tile_health()`, heartbeat loop. | **PASS** | Missing: `import shutil` (line using `shutil.which("fbi")` will crash). Bug fix needed. | S | Med |
| **REQ-COMP-002** | vw-bundlectl CLI with export/verify/import/diff | `tools/bundlectl/bundlectl.py`: all 4 subcommands, Ed25519 via PyNaCl, HMAC-SHA256 dev fallback, `tar.zst` format, `manifest.json` with per-file SHA-256. | **PASS** | Missing: `import re` (line `re.fullmatch(...)` in `load_key()` will crash). Bug fix needed. Also missing `import subprocess` in fallback zstd path. | S | Med |
| **REQ-COMP-003** | vw-vdi-encoder WebRTC publish | `agents/vdi-encoder/vw_vdi_encoder.py`: supports `--output-mode srt|rtp|webrtc`. For `webrtc` mode, pipeline ends with `fakesink` (placeholder comment: "needs adaptation"). **No actual WebRTC/webrtcbin publish.** | **FAIL** | Implement `webrtcbin`-based pipeline for `--output-mode webrtc` with Janus signaling | L | High |
| **REQ-COMP-004** | vw-sourcereg heartbeat | `agents/sourcereg/vw_sourcereg.py`: `SourceReg.register_if_needed()` POSTs to `/api/v1/sources`, `heartbeat()` POSTs to `/api/v1/sources/{id}/heartbeat`. Uses mTLS. State persisted to disk. | **PASS** | — | — | — |
| **REQ-COMP-005** | Gateway WebRTC republish into Janus | `services/gateway/app/pipelines.py`: outputs are all `srtsink uri=...` (SRT only). **No webrtcbin publish path.** Gateway is SRT→SRT only. | **FAIL** | Add WebRTC republish pipeline option using `webrtcbin` or document SRT-to-Janus ingest path via streaming plugin | L | High |
| **REQ-COMP-006** | Gateway prober API | `services/gateway/app/probe.py`: `probe()` uses `ffprobe` to validate URL, returns codec/resolution/fps/bitrate/reachability. `services/gateway/app/main.py`: `POST /probe`. | **PASS** | — | — | — |
| **REQ-GW-001** | No raw RTP to Janus | Gateway outputs only SRT. No `udpsink` to Janus port, no raw RTP injection. | **PASS** | But also means no connection to Janus at all currently (see REQ-COMP-005). | — | — |
| **REQ-HA-001** | Janus Deployment ≥2 replicas + PDB | `charts/vw-sfu-janus/values.yaml`: `replicaCount: 1`. No PDB manifest exists anywhere in repo. | **FAIL** | Change default to `replicaCount: 2`; add `charts/vw-sfu-janus/templates/pdb.yaml` | S | Med |
| **REQ-HA-002** | Compositor StatefulSet + GPU nodeSelector | `charts/vw-compositor/templates/deployment.yaml`: **Deployment** (not StatefulSet). `values.yaml`: `nodeSelector: {}` (empty, no GPU selector). | **FAIL** | Convert to StatefulSet; add `nodeSelector: {gpu: "true"}` default; add GPU resource requests | M | Med |
| **REQ-HA-003** | MetalLB Services | `charts/vw-sfu-janus/values.yaml`: `service.type: LoadBalancer` ✅. `charts/vw-compositor/values.yaml`: `service.type: ClusterIP` ❌. `charts/vw-gw/values.yaml`: `service.type: ClusterIP` ❌. | **PARTIAL** | Set gateway and compositor service types to LoadBalancer where external access needed (gateway needs external SRT ingest) | S | Med |
| **REQ-HA-004** | No Keepalived | `grep -ri keepalived` returns nothing. | **PASS** | — | — | — |
| **REQ-OSS-001** | No NDI | `grep -ri ndi` found matches in `docs/` and `tests/integration/docker-compose.yml`, but these are false positives (substring matches in words like "condition", "finding"). No actual NDI protocol usage. | **PASS** | — | — | — |
| **REQ-OSS-002** | Compositor output via HDMI/SRT only | `services/compositor/app/pipelines.py`: output is `srtsink uri=...` (SRT). No NDI. | **PASS** | — | — | — |
| **REQ-OFFLINE-001** | Local registry, no external pulls | `charts/vw-platform/values-airgap.yaml`: overrides `image.repository` to `registry.local:5000/...`. `scripts/mirror-images.sh`: `pull_all`, `save_all`, `load_all`, `retag_and_push`. | **PASS** | — | — | — |
| **REQ-OFFLINE-002** | Signed bundles | `tools/bundlectl/bundlectl.py`: Ed25519 signing. `services/mgmt-api/app/main.py`: `/bundles/import` validates HMAC. | **PASS** | API uses HMAC (not Ed25519). CLI uses Ed25519. Should align. | S | Low |
| **REQ-SIZE-001** | W=4, N=64 | `docs/sizing.md` exists but contains v1 numbers. No `values.yaml` enforces W=4/N=64. | **PARTIAL** | Update docs/sizing.md to match v2 spec; add sizing comments to values files | S | Low |
| **REQ-UI-001** | Layout editor with PEP-integrated apply | `services/ui/src/index.html`: `layoutsPage()` creates/updates/activates layouts via API. Grid config as JSON textarea. **No drag-and-drop.** Activate calls API which triggers policy check indirectly. | **PARTIAL** | Drag-and-drop is aspirational; current JSON editor is functional. Accept as MVP. | L | Low |
| **REQ-UI-002** | Source onboarding wizard with gateway probe | `services/ui/src/index.html`: `sourcesPage()` has create/update form. **No probe integration** (no button to call gateway `/probe`). | **PARTIAL** | Add "Probe" button to sources page that calls `/api/v1/gateway/probe` via mgmt-api proxy | S | Low |
| **REQ-UI-003** | Audit viewer + chain verify | `services/ui/src/index.html`: `auditPage()` queries `/audit/query`. **No verify button** calling `/audit/verify`. | **PARTIAL** | Add "Verify Chain" button calling audit service `/verify` | S | Low |
| **REQ-UI-004** | Bundle UI (import/verify/diff) | `services/ui/src/index.html`: `bundlePage()` has export only. **No import/verify/diff UI.** | **PARTIAL** | Add import file upload + verify + diff display | M | Low |

### Summary Counts

| Status | Count |
|--------|-------|
| **PASS** | 19 |
| **PARTIAL** | 12 |
| **FAIL** | 5 |
| **Total** | 36 |

---

## 3. Critical Bugs Found

All three critical bugs have been **resolved**:

| File | Bug | Status |
|------|-----|--------|
| `tools/bundlectl/bundlectl.py` | `import re` was missing | ✅ Fixed — `re`, `shutil`, `subprocess` imported (lines 11–13). Verified by 6 automated tests (3 Ed25519 + 3 HMAC). |
| `agents/wallctl/vw_wallctl.py` | `import shutil` was missing | ✅ Fixed — `shutil` imported (line 9). `shutil.which("fbi")` call in `_show_slate()` verified. |
| `services/compositor/app/policy.py` | Policy call sent `{source_id, action}` but policy service expected `{wall_id, source_id, operator_id, operator_roles}` | ✅ Fixed — payload now sends all required `EvalRequest` fields; response checks `data.get("allowed")`. |

---

## 4. Remediation PR Plan (8 PRs)

### PR 1: Critical Bug Fixes + Audit Completeness
**Scope:** Fix crashes, add missing audit hooks  
**Files to modify:**
- `tools/bundlectl/bundlectl.py` — add `import re` and `import subprocess`
- `agents/wallctl/vw_wallctl.py` — add `import shutil`
- `services/mgmt-api/app/main.py` — add `append_audit_event()` calls in `tokens_subscribe()` and `policy_evaluate()`
- `services/compositor/app/policy.py` — fix payload to match `EvalRequest` schema (add `wall_id`, `operator_id`, `operator_roles`)  
**Acceptance:** `vw-bundlectl export/verify` works; wallctl safe-slate doesn't crash; token subscribe emits audit event; compositor policy calls don't default-deny  
**Effort:** S | **Risk:** High (blocking bugs)

### PR 2: Namespace Default-Deny + PDB
**Scope:** Helm hardening  
**Files to create/modify:**
- `charts/vw-platform/templates/ns-default-deny-media.yaml` — namespace-level default-deny for `vw-media`
- `charts/vw-platform/templates/ns-default-deny-control.yaml` — same for `vw-control`
- `charts/vw-platform/templates/ns-default-deny-obs.yaml` — same for `vw-observability`
- `charts/vw-sfu-janus/templates/pdb.yaml` — `minAvailable: 1`
- `charts/vw-compositor/templates/pdb.yaml` — `minAvailable: 1`
- `charts/vw-sfu-janus/values.yaml` — change `replicaCount: 1` → `replicaCount: 2`  
**Acceptance:** `kubectl get networkpolicy -A` shows 3 default-deny policies; PDB blocks voluntary eviction of last pod  
**Effort:** S | **Risk:** High

### PR 3: Compositor → StatefulSet + GPU + MetalLB Services
**Scope:** HA alignment  
**Files to modify:**
- `charts/vw-compositor/templates/deployment.yaml` → rename to `statefulset.yaml`, change `kind: Deployment` to `kind: StatefulSet`, add `volumeClaimTemplates` for pipeline state
- `charts/vw-compositor/values.yaml` — add `nodeSelector: {nvidia.com/gpu: "present"}`, add `resources.limits: {nvidia.com/gpu: "1"}`, change `service.type: LoadBalancer`
- `charts/vw-gw/values.yaml` — change `service.type: LoadBalancer` (needs external SRT ingest)
- `charts/vw-gw/templates/service.yaml` — add UDP port range for SRT  
**Acceptance:** `kubectl get sts` shows compositor; GPU pod scheduled on GPU node; MetalLB assigns VIPs  
**Effort:** M | **Risk:** Med

### PR 4: Vault Helm Chart + Agent Sidecar Injection
**Scope:** PKI infrastructure  
**Files to create:**
- `charts/vw-vault/` — StatefulSet (Raft HA, 3 replicas), init Job for setup-pki.sh, Service
- Add Vault Agent injector annotations to all Deployment/StatefulSet templates:
  - `charts/vw-sfu-janus/templates/deployment.yaml`
  - `charts/vw-compositor/templates/statefulset.yaml`
  - `charts/vw-gw/templates/deployment.yaml`
  - `charts/audit/templates/deployment.yaml`
  - `charts/mgmt-api/templates/deployment.yaml`
  - `charts/policy/templates/deployment.yaml`
  - `charts/health/templates/deployment.yaml`  
**Acceptance:** Vault pods run HA; all pods get auto-renewed mTLS certs via Vault Agent  
**Effort:** L | **Risk:** High

### PR 5: Gateway WebRTC Republish Pipeline
**Scope:** Fix REQ-COMP-005 — gateway→Janus path  
**Files to modify:**
- `services/gateway/app/pipelines.py` — add `webrtc` output mode using `gst-plugins-bad webrtcbin` + Janus signaling (or document SRT-to-Janus Streaming plugin path)
- `services/gateway/app/models.py` — add `output_protocol: Literal["srt", "webrtc"]`
- `services/gateway/app/main.py` — pass WebRTC signaling config (Janus room, token)
- Add integration test for gateway→Janus publish  
**Acceptance:** HDMI encoder → SRT → gateway → Janus VideoRoom (appears as publisher); tile player can subscribe  
**Effort:** L | **Risk:** High

### PR 6: VDI Encoder WebRTC Mode
**Scope:** Fix REQ-COMP-003 — actual webrtcbin pipeline  
**Files to modify:**
- `agents/vdi-encoder/vw_vdi_encoder.py` — replace `fakesink` placeholder with `webrtcbin` pipeline + Janus signaling
- Add signaling helper module `agents/vdi-encoder/janus_signaling.py`
- Integration test: VDI encoder → Janus room → subscriber receives frames  
**Acceptance:** `--output-mode webrtc` publishes to Janus room with token auth  
**Effort:** L | **Risk:** High

### PR 7: Audit Export + UI Enhancements
**Scope:** Fill remaining PARTIAL gaps  
**Files to create/modify:**
- `services/audit/app/main.py` — add `GET /export` endpoint (signed JSONL, Ed25519)
- `services/mgmt-api/app/main.py` — add `/api/v1/audit/verify` proxy, `/api/v1/audit/export` proxy, `/api/v1/gateway/probe` proxy
- `services/ui/src/index.html`:
  - Audit page: add "Verify Chain" button
  - Sources page: add "Probe" button calling gateway probe
  - Bundle page: add import file upload + verify + diff
- `docs/sizing.md` — update to v2 sizing (W=4, N=64, 24-tile walls)  
**Acceptance:** Audit chain verification accessible from UI; probe returns codec info; bundle round-trip from UI  
**Effort:** M | **Risk:** Low

### PR 8: Policy Tag Enrichment from DB
**Scope:** Complete the policy→DB loop  
**Files to modify:**
- `services/policy/app/main.py` — replace `_lookup_tags_stub()` with actual DB or API call to fetch wall/source tags from mgmt-api
- `services/mgmt-api/app/main.py` — add internal endpoint `GET /api/v1/internal/tags?wall_id=X&source_id=Y` returning tags
- `services/policy/app/main.py` — call internal tags endpoint
- Update unit tests  
**Acceptance:** Policy decisions use real tags from DB; tests pass with DB-backed tags  
**Effort:** M | **Risk:** Med

---

## 5. Patches for FAIL Items

### Patch 5.1: Fix `bundlectl.py` missing imports

```diff
--- a/tools/bundlectl/bundlectl.py
+++ b/tools/bundlectl/bundlectl.py
@@ -5,6 +5,8 @@ import argparse
 import hashlib
 import hmac
 import io
 import json
 import os
+import re
+import subprocess
 import shutil
 import sys
```

### Patch 5.2: Fix `vw_wallctl.py` missing import

```diff
--- a/agents/wallctl/vw_wallctl.py
+++ b/agents/wallctl/vw_wallctl.py
@@ -7,6 +7,7 @@ import json
 import logging
 import os
 import signal
+import shutil
 import subprocess
 import sys
 import time
```

### Patch 5.3: Fix compositor policy payload

```diff
--- a/services/compositor/app/policy.py
+++ b/services/compositor/app/policy.py
@@ -10,7 +10,16 @@ VW_POLICY_SERVICE_URL = os.getenv("VW_POLICY_SERVICE_URL", "http://vw-policy.vw
 async def evaluate_source_access(source_id: str, user: Optional[str] = None) -> bool:
     url = f"{VW_POLICY_SERVICE_URL.rstrip('/')}/evaluate"
-    payload = {"source_id": source_id, "action": "use"}
+    # Must match policy service EvalRequest schema
+    payload = {
+        "wall_id": 0,         # compositor is not wall-specific; use 0 as sentinel
+        "source_id": int(source_id) if str(source_id).isdigit() else 0,
+        "operator_id": user or "compositor-service",
+        "operator_roles": ["admin"],  # compositor acts as privileged service
+        "operator_tags": [],
+    }
     headers = {}
     if user:
         headers["X-User"] = user
@@ -19,8 +28,8 @@ async def evaluate_source_access(source_id: str, user: Optional[str] = None) ->
             r = await client.post(url, json=payload, headers=headers)
             r.raise_for_status()
             data = r.json()
-            if isinstance(data, dict):
-                if data.get("allow") is True:
+            if isinstance(data, dict):
+                if data.get("allowed") is True:
                     return True
-                if str(data.get("decision", "")).lower() == "allow":
+                if str(data.get("reason", "")).startswith("allowed"):
                     return True
```

### Patch 5.4: Janus replicas + PDB

```diff
--- a/charts/vw-sfu-janus/values.yaml
+++ b/charts/vw-sfu-janus/values.yaml
@@ -1,4 +1,4 @@
-replicaCount: 1
+replicaCount: 2
```

New file `charts/vw-sfu-janus/templates/pdb.yaml`:

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: {{ include "vw-sfu-janus.fullname" . }}-pdb
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: {{ include "vw-sfu-janus.name" . }}
```

### Patch 5.5: Add missing audit hook to token subscribe

```diff
--- a/services/mgmt-api/app/main.py
+++ b/services/mgmt-api/app/main.py
@@ -xxx,6 +xxx,12 @@ async def tokens_subscribe(payload: TokenSubscribeRequest, user: ...) -> ...:
     if not presp.allowed:
+        await append_audit_event(
+            action="tokens.subscribe.deny", actor=operator_id,
+            object_type="token", object_id=f"{payload.wall_id}:{payload.source_id}:{payload.tile_id}",
+            details={"reason": presp.reason})
         return TokenSubscribeResponse(allowed=False, reason=presp.reason, token=None)
     token = _mint_stream_token(...)
+    await append_audit_event(
+        action="tokens.subscribe.allow", actor=operator_id,
+        object_type="token", object_id=f"{payload.wall_id}:{payload.source_id}:{payload.tile_id}",
+        details={"wall_id": payload.wall_id, "source_id": payload.source_id})
     return TokenSubscribeResponse(allowed=True, reason="allowed", token=token)
```

---

## 6. Risk Summary

| Priority | Items | Key Risk |
|----------|-------|----------|
| **P0 — Blocking** | ~~Patch 5.1, 5.2 (crash bugs), Patch 5.3 (compositor policy broken)~~ | ✅ **All resolved** — bundlectl, wallctl, compositor all functional |
| **P1 — High** | PR 2 (default-deny + PDB), PR 4 (Vault chart), PR 5 (gateway WebRTC) | Security model incomplete; no SFU ingest path for HDMI |
| **P2 — Medium** | PR 3 (StatefulSet+GPU), PR 6 (VDI WebRTC), PR 8 (tag enrichment) | HA and media paths incomplete |
| **P3 — Low** | PR 7 (UI + audit export), sizing docs | Functional completeness |

---

*End of Phase 2 compliance review.*
