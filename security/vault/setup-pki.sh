#!/usr/bin/env bash
set -euo pipefail

# Vault PKI bootstrap for Videowall.
# Requires VAULT_ADDR and VAULT_TOKEN env vars.

: "${VAULT_ADDR:?Must set VAULT_ADDR}"
: "${VAULT_TOKEN:?Must set VAULT_TOKEN}"

ROOT_TTL="87600h"      # 10y
INT_TTL="43800h"       # 5y
SERVER_MAX_TTL="2160h" # 90d
CLIENT_MAX_TTL="720h"  # 30d

echo "[*] Enabling PKI at pki/ (root)"
vault secrets enable -path=pki pki >/dev/null 2>&1 || true
vault secrets tune -max-lease-ttl="$ROOT_TTL" pki

echo "[*] Generating root CA"
vault write -field=certificate pki/root/generate/internal \
  common_name="Videowall Root CA" \
  ttl="$ROOT_TTL" > pki-root-ca.pem

vault write pki/config/urls \
  issuing_certificates="$VAULT_ADDR/v1/pki/ca" \
  crl_distribution_points="$VAULT_ADDR/v1/pki/crl"

echo "[*] Enabling intermediate PKI at pki_int/"
vault secrets enable -path=pki_int pki >/dev/null 2>&1 || true
vault secrets tune -max-lease-ttl="$INT_TTL" pki_int

echo "[*] Generating intermediate CSR"
vault write -field=csr pki_int/intermediate/generate/internal \
  common_name="Videowall Intermediate CA" \
  ttl="$INT_TTL" > pki-int.csr

echo "[*] Signing intermediate with root"
vault write -field=certificate pki/root/sign-intermediate \
  csr=@pki-int.csr \
  format=pem_bundle \
  ttl="$INT_TTL" > pki-int.pem

echo "[*] Setting signed intermediate"
vault write pki_int/intermediate/set-signed certificate=@pki-int.pem

echo "[*] Creating roles"
vault write pki_int/roles/server-cert \
  allowed_domains="videowall.local" \
  allow_subdomains=true \
  allow_bare_domains=true \
  enforce_hostnames=true \
  max_ttl="$SERVER_MAX_TTL"

vault write pki_int/roles/client-cert \
  allow_any_name=true \
  enforce_hostnames=false \
  max_ttl="$CLIENT_MAX_TTL"

echo "[*] Policies"
cat > cert-issuer.hcl <<'HCL'
path "pki_int/issue/server-cert" { capabilities = ["create", "update"] }
path "pki_int/issue/client-cert" { capabilities = ["create", "update"] }
HCL

cat > cert-reader.hcl <<'HCL'
path "pki/ca" { capabilities = ["read"] }
path "pki_int/ca" { capabilities = ["read"] }
path "pki_int/cert/ca_chain" { capabilities = ["read"] }
HCL

vault policy write cert-issuer cert-issuer.hcl
vault policy write cert-reader cert-reader.hcl

echo "[+] Vault PKI setup complete."
echo "    - Root CA: pki-root-ca.pem"
echo "    - Intermediate bundle: pki-int.pem"
