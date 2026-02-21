#!/usr/bin/env bash
set -euo pipefail

# Rotate certificates for all components, distribute via Ansible, reload services.
#
# Assumptions:
# - Vault PKI is configured (setup-pki.sh already executed).
# - VAULT_ADDR and VAULT_TOKEN set (token has cert-issuer).
# - Ansible inventory and playbooks exist under ../ansible.
#
# Usage:
#   ./rotate-certs.sh --inventory ../ansible/inventory/hosts.yml

INV="../ansible/inventory/hosts.yml"
OUTDIR="./rotated"
DOMAIN="videowall.local"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --inventory) INV="$2"; shift 2;;
    --outdir) OUTDIR="$2"; shift 2;;
    --domain) DOMAIN="$2"; shift 2;;
    -h|--help) sed -n '1,160p' "$0"; exit 0;;
    *) echo "Unknown arg: $1" >&2; exit 2;;
  esac
done

: "${VAULT_ADDR:?Must set VAULT_ADDR}"
: "${VAULT_TOKEN:?Must set VAULT_TOKEN}"

mkdir -p "$OUTDIR"

issue_server () {
  local cn="$1"
  local sans="$2"
  vault write -format=json pki_int/issue/server-cert \
    common_name="$cn" \
    alt_names="$sans" \
    ttl="2160h" > "$OUTDIR/${cn}.json"
  python3 - <<PY
import json, pathlib
p = pathlib.Path("$OUTDIR/${cn}.json")
d = json.loads(p.read_text())
pathlib.Path("$OUTDIR/${cn}.crt").write_text(d["data"]["certificate"]+"\n")
pathlib.Path("$OUTDIR/${cn}.key").write_text(d["data"]["private_key"]+"\n")
pathlib.Path("$OUTDIR/${cn}.ca").write_text(d["data"]["issuing_ca"]+"\n")
PY
}

echo "[*] Issuing server certs"
issue_server "mgmt-api.${DOMAIN}" "mgmt-api.${DOMAIN},mgmt-api"
issue_server "sfu.${DOMAIN}" "sfu.${DOMAIN},sfu"
issue_server "gateway.${DOMAIN}" "gateway.${DOMAIN},gateway"
issue_server "compositor.${DOMAIN}" "compositor.${DOMAIN},compositor"
issue_server "keycloak.${DOMAIN}" "keycloak.${DOMAIN},keycloak"
issue_server "vault.${DOMAIN}" "vault.${DOMAIN},vault"

echo "[*] Distributing and reloading via Ansible"
ansible-playbook -i "$INV" ../ansible/playbooks/deploy-wall-controllers.yml --extra-vars "cert_src=$OUTDIR"
ansible-playbook -i "$INV" ../ansible/playbooks/deploy-tile-players.yml --extra-vars "cert_src=$OUTDIR"
ansible-playbook -i "$INV" ../ansible/playbooks/deploy-source-agents.yml --extra-vars "cert_src=$OUTDIR"

echo "[+] Rotation complete. Verify mTLS and Prometheus targets."
