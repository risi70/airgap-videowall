# Deploying into an Existing Kubernetes Cluster

This guide covers deploying airgap-videowall alongside your existing Keycloak, Vault, and PostgreSQL infrastructure.

---

## Prerequisites

| Component | Requirement |
|-----------|------------|
| Kubernetes | 1.26+ with RBAC enabled |
| Helm | 3.x |
| Vault | HashiCorp Vault with Kubernetes auth + PKI engine |
| Vault Agent Injector | Deployed in cluster (handles mTLS cert injection) |
| Keycloak | Running instance with admin access |
| PostgreSQL | 15+ with a database provisioned for videowall |
| MetalLB / LB | LoadBalancer support (for Janus, gateway, compositor) |
| GPU node | At least one node with nvidia.com/gpu (for compositor) |

---

## Step 1: Prepare PostgreSQL

Create a database and user for the videowall application:

```sql
CREATE DATABASE videowall;
CREATE USER vw_app WITH PASSWORD '<strong-password>';
GRANT ALL PRIVILEGES ON DATABASE videowall TO vw_app;
```

Create a Kubernetes Secret for the password:

```bash
kubectl create secret generic videowall-db-credentials \
  --from-literal=password='<strong-password>' \
  -n videowall
```

> The application auto-creates its schema on first startup.

---

## Step 2: Configure Keycloak

**Option A — Import the realm file:**

```bash
cd security/keycloak
bash bootstrap.sh \
  --url https://keycloak.example.com \
  --admin-user admin \
  --admin-pass '<admin-password>' \
  --realm-file videowall-realm.json
```

**Option B — Manual setup in your existing realm:**

1. Create client `vw` (confidential, RS256 signing)
2. Create realm roles: `admin`, `operator`, `viewer`
3. Add a protocol mapper named `clearance_tags`:
   - Type: User Attribute
   - User Attribute: `clearance_tags`
   - Token Claim Name: `clearance_tags`
   - Claim JSON Type: String (multivalued)
4. Note the RS256 public key from Realm Settings → Keys → RS256 → Public key

---

## Step 3: Configure Vault PKI

Run the PKI setup script against your existing Vault:

```bash
export VAULT_ADDR="https://vault.example.com:8200"
export VAULT_TOKEN="<privileged-token>"

cd security/vault
bash setup-pki.sh
```

This creates:
- Root CA (`pki/`) with 10-year TTL
- Intermediate CA (`pki_int/`) with 5-year TTL
- Server cert role (`pki_int/roles/server-cert`) — 90-day max TTL
- Client cert role (`pki_int/roles/client-cert`) — 30-day max TTL

Then create Kubernetes auth roles for each service:

```bash
# Enable Kubernetes auth (if not already)
vault auth enable kubernetes
vault write auth/kubernetes/config \
  kubernetes_host="https://<k8s-api-server>:6443"

# Create a role for each videowall service
for svc in sfu gateway compositor mgmt-api policy audit health ui; do
  vault write "auth/kubernetes/role/videowall-${svc}" \
    bound_service_account_names="*" \
    bound_service_account_namespaces="videowall" \
    policies="videowall-pki" \
    ttl="1h"
done

# Create the PKI policy
vault policy write videowall-pki - << 'EOF'
path "pki_int/issue/server-cert" {
  capabilities = ["create", "update"]
}
path "pki_int/issue/client-cert" {
  capabilities = ["create", "update"]
}
EOF
```

> Ensure the [Vault Agent Injector](https://developer.hashicorp.com/vault/docs/platform/k8s/injector) is deployed in your cluster. If not:
> ```bash
> helm install vault hashicorp/vault \
>   --set "injector.enabled=true" \
>   --set "server.enabled=false" \
>   -n vault-system --create-namespace
> ```

---

## Step 4: Create Namespace and Deploy

```bash
kubectl create namespace videowall

# Deploy using the existing-cluster values
helm upgrade --install videowall charts/vw-platform \
  -f charts/vw-platform/values-existing-cluster.yaml \
  -n videowall
```

For per-service deployment (if not using the umbrella chart):

```bash
# Deploy each service individually
for chart in mgmt-api policy audit health ui; do
  helm upgrade --install "vw-${chart}" "charts/${chart}" \
    --set env.VW_DB_DSN="postgresql://vw_app:secret@pg.infra.svc:5432/videowall" \
    --set env.VW_OIDC_ISSUER="https://keycloak.example.com/realms/videowall" \
    --set vault.role="videowall-${chart}" \
    --set vault.pkiPath="pki_int/issue/server-cert" \
    -n videowall
done

# Media services
helm upgrade --install vw-janus charts/vw-sfu-janus \
  --set controlPlaneNamespace=videowall \
  --set vault.role=videowall-sfu \
  -n videowall

helm upgrade --install vw-gateway charts/vw-gw \
  --set controlPlaneNamespace=videowall \
  --set vault.role=videowall-gateway \
  -n videowall

helm upgrade --install vw-compositor charts/vw-compositor \
  --set controlPlaneNamespace=videowall \
  --set vault.role=videowall-compositor \
  --set env.VW_POLICY_SERVICE_URL="http://vw-policy.videowall.svc:8001" \
  -n videowall
```

---

## Step 5: Verify

```bash
# Check all pods are running
kubectl get pods -n videowall

# Check Vault Agent sidecars injected
kubectl get pods -n videowall -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{range .spec.containers[*]}{.name}{","}{end}{"\n"}{end}'

# Test mgmt-api health
kubectl port-forward svc/vw-mgmt-api 8000:8000 -n videowall &
curl -s http://localhost:8000/api/v1/walls | head

# Test audit chain
curl -s http://localhost:8000/api/v1/audit/verify
```

---

## Configuration Reference

### Environment Variables (mgmt-api)

All settings use the `VW_` prefix and can be set via `--set env.VAR=value` in Helm:

| Variable | Default | Description |
|----------|---------|-------------|
| `VW_DB_DSN` | `postgresql://vw:vw@postgres:5432/vw` | PostgreSQL connection string |
| `VW_POLICY_URL` | `http://vw-policy:8001` | Policy service URL |
| `VW_AUDIT_URL` | `http://vw-audit:8002` | Audit service URL |
| `VW_HEALTH_URL` | `http://vw-health:8003` | Health service URL |
| `VW_OIDC_ISSUER` | _(empty)_ | Keycloak issuer URL |
| `VW_OIDC_AUDIENCE` | _(empty)_ | Expected JWT audience |
| `VW_OIDC_CLIENT_ID` | `vw` | Keycloak client ID |
| `VW_OIDC_PUBLIC_KEY_PEM` | _(empty)_ | RS256 public key for JWT verification |
| `VW_OIDC_JWKS_PATH` | _(empty)_ | Path to offline JWKS JSON |
| `VW_STREAM_TOKEN_SECRET` | `change-me` | HS256 secret for stream tokens |
| `VW_STREAM_TOKEN_TTL_SECONDS` | `300` | Stream token lifetime |
| `VW_BUNDLE_HMAC_SECRET` | _(empty)_ | HMAC secret for bundle import verification |

### Environment Variables (compositor)

| Variable | Default | Description |
|----------|---------|-------------|
| `VW_POLICY_SERVICE_URL` | `http://vw-policy.vw-control.svc.cluster.local:8002` | Policy service URL |

### Environment Variables (policy)

| Variable | Default | Description |
|----------|---------|-------------|
| `VW_MGMT_API_URL` | `http://vw-mgmt-api:8000` | Mgmt API URL (for tag enrichment) |

### Environment Variables (agents)

| Variable | Default | Description |
|----------|---------|-------------|
| `VW_MGMT_API_URL` | `https://vw-mgmt-api:8000` | Mgmt API URL |
| `VW_HEALTH_URL` | `https://vw-health:8003` | Health service URL |

### Vault Configuration

| Helm Value | Default | Description |
|------------|---------|-------------|
| `vault.enabled` | `true` | Enable/disable Vault Agent injection |
| `vault.role` | _(chart name)_ | Vault Kubernetes auth role name |
| `vault.pkiPath` | `pki_int/issue/server-cert` | Vault PKI issue path |

### Namespace Configuration (umbrella chart)

| Helm Value | Default | Description |
|------------|---------|-------------|
| `namespaces.control.name` | `vw-control` | Control plane namespace |
| `namespaces.media.name` | `vw-media` | Media plane namespace |
| `namespaces.obs.name` | `vw-observability` | Observability namespace |
| `namespaces.*.defaultDenyEnabled` | `true` | Deploy default-deny NetworkPolicy |

---

## Common Scenarios

### Single namespace deployment

Deploy everything into one namespace (e.g. `videowall`):

```yaml
# values-single-ns.yaml
namespaces:
  control: { name: "videowall", defaultDenyEnabled: false }
  media:   { name: "videowall", defaultDenyEnabled: false }
  obs:     { name: "videowall", defaultDenyEnabled: false }
```

Cross-namespace NetworkPolicy selectors won't apply when everything is co-located — services find each other by service name directly.

### Disable Vault injection

If your cluster doesn't use Vault (e.g. you manage TLS certs externally via cert-manager):

```yaml
vault:
  deploy: false
vw-sfu-janus:
  vault: { enabled: false }
vw-gw:
  vault: { enabled: false }
vw-compositor:
  vault: { enabled: false }
# Also set vault.enabled: false on each per-service chart
```

### Disable GPU requirement

If you don't have GPU nodes (compositor will use CPU rendering):

```yaml
vw-compositor:
  nodeSelector: {}
  resources:
    requests:
      cpu: "4"
      memory: "8Gi"
```
