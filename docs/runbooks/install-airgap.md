# Runbook: Fresh installation (air-gapped)

## Preconditions
1. Kubernetes cluster is up (masters/workers) with CNI installed.
2. Offline container registry reachable as `registry.local:5000`.
3. Operator workstation has:
   - `kubectl`, `helm`, `ansible`, `vault`, `jq`, `openssl`
   - access to the **operator VLAN** only
4. Time sync: NTP reachable from all zones.

## Procedure

1. **Mirror container images**
   1. On a connected staging environment, mirror required images.
   2. Transfer as OCI archives into the air-gapped registry host.
   3. Push to `registry.local:5000/...`.
   **Expected output:** `curl -fsS http://registry.local:5000/v2/_catalog` shows required repos.

2. **Prepare lab certificates (if no Vault PKI yet)**
   1. `cd security/certs && ./generate-lab-certs.sh --domain videowall.local`
   2. Copy `ca-chain.pem` to operator host trust store (optional).
   **Expected output:** `security/certs/certs/lab/ca-chain.pem` exists.

3. **Install Vault**
   1. Deploy Vault (out of scope for Module 4 charting) or use your existing Vault.
   2. Initialize/unseal Vault.
   3. Set `VAULT_ADDR` and `VAULT_TOKEN`.
   **Expected output:** `vault status` is `sealed=false`.

4. **Initialize Vault PKI**
   1. `cd security/vault`
   2. `./setup-pki.sh`
   **Expected output:** `pki-root-ca.pem`, `pki-int.pem` created locally; Vault has `pki/` and `pki_int/`.

5. **Install Keycloak**
   1. Deploy Keycloak into `vw-control` (out of scope here).
   2. Ensure admin login works.
   **Expected output:** Keycloak `/health/ready` returns OK.

6. **Bootstrap Keycloak realm**
   1. `cd security/keycloak`
   2. `./bootstrap.sh --url http://keycloak.vw-control.svc:8080 --admin-user admin --admin-pass <pwd>`
   **Expected output:** realm `videowall` exists; user `vw-admin` created (password reset required).

7. **Deploy observability**
   1. `helm upgrade --install vw-obs charts/vw-observability -n vw-obs --create-namespace`
   2. `kubectl -n vw-obs get pods`
   **Expected output:** Prometheus, Grafana, Loki, Promtail running.

8. **Deploy endpoints (controllers/players/agents)**
   1. `cd security/ansible`
   2. `ansible-playbook playbooks/deploy-wall-controllers.yml`
   3. `ansible-playbook playbooks/deploy-tile-players.yml`
   4. `ansible-playbook playbooks/deploy-source-agents.yml`
   **Expected output:** systemd services active, endpoints report health.

9. **Verification**
   1. Open Grafana (operator VLAN) and load Videowall dashboards.
   2. Run integration tests: `make test-integration`
   **Expected output:** smoke tests pass; no critical alerts.

## Rollback / recovery
- Helm: `helm rollback <release> <revision>`
- Endpoints: `systemctl stop vw-wallctl|vw-tile-player|vw-source-agent`
- Certificates: revert to previous cert bundle from archive and re-distribute.
