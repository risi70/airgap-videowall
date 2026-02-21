#!/usr/bin/env bash
set -euo pipefail

# Offline lab certificate generator for airgap-videowall.
# - Creates an ED25519 keypair for bundle signing.
# - Creates a self-signed RSA4096 CA (10y) and issues server/client certificates.
#
# Output directory: security/certs/certs/lab/
#
# Usage:
#   ./generate-lab-certs.sh [--domain videowall.local]

DOMAIN="videowall.local"
OUTDIR="$(cd "$(dirname "$0")" && pwd)/certs/lab"
mkdir -p "$OUTDIR"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2;;
    -h|--help) sed -n '1,200p' "$0"; exit 0;;
    *) echo "Unknown arg: $1" >&2; exit 2;;
  esac
done

cd "$OUTDIR"

echo "[*] Generating ED25519 bundle signing keypair"
openssl genpkey -algorithm ED25519 -out bundle-signing-key.pem
openssl pkey -in bundle-signing-key.pem -pubout -out bundle-signing-pub.pem

echo "[*] Generating lab CA (RSA 4096, 10y)"
openssl genrsa -out lab-ca.key 4096
openssl req -x509 -new -nodes -key lab-ca.key -sha256 -days 3650 \
  -subj "/C=CH/O=Videowall/L=Airgap/CN=Videowall Lab CA" \
  -out lab-ca.crt

cat lab-ca.crt > ca-chain.pem

make_openssl_cnf () {
  local cn="$1"
  local sans="$2"
  cat > openssl.cnf <<EOF
[ req ]
default_bits       = 2048
prompt             = no
default_md         = sha256
req_extensions     = req_ext
distinguished_name = dn

[ dn ]
C  = CH
O  = Videowall
OU = Lab
CN = ${cn}

[ req_ext ]
subjectAltName = ${sans}

[ v3_ext ]
subjectAltName = ${sans}
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth, clientAuth
EOF
}

issue_cert () {
  local name="$1"
  local cn="$2"
  local sans="$3"
  local is_client="$4"

  echo "[*] Issuing ${name} (CN=${cn})"
  local san_line=""
  # sans provided as comma list. convert to DNS entries
  IFS=',' read -ra A <<< "$sans"
  local i=0
  for s in "${A[@]}"; do
    s="$(echo "$s" | xargs)"
    if [[ $i -eq 0 ]]; then
      san_line="DNS:${s}"
    else
      san_line="${san_line},DNS:${s}"
    fi
    i=$((i+1))
  done

  make_openssl_cnf "$cn" "$san_line"

  openssl genrsa -out "${name}.key" 2048
  openssl req -new -key "${name}.key" -out "${name}.csr" -config openssl.cnf

  # Extensions differ slightly for client vs server; we allow both for simplicity.
  openssl x509 -req -in "${name}.csr" -CA lab-ca.crt -CAkey lab-ca.key -CAcreateserial \
    -out "${name}.crt" -days 825 -sha256 -extfile openssl.cnf -extensions v3_ext

  rm -f openssl.cnf "${name}.csr"
}

# Servers
issue_cert "mgmt-api" "mgmt-api.${DOMAIN}" "mgmt-api.${DOMAIN},mgmt-api" "false"
issue_cert "sfu" "sfu.${DOMAIN}" "sfu.${DOMAIN},sfu" "false"
issue_cert "gateway" "gateway.${DOMAIN}" "gateway.${DOMAIN},gateway" "false"
issue_cert "compositor" "compositor.${DOMAIN}" "compositor.${DOMAIN},compositor" "false"
issue_cert "keycloak" "keycloak.${DOMAIN}" "keycloak.${DOMAIN},keycloak" "false"
issue_cert "vault" "vault.${DOMAIN}" "vault.${DOMAIN},vault" "false"

# Clients: wall controllers and source agents
for i in $(seq -w 1 4); do
  issue_cert "wall-controller-${i}" "wall-controller-${i}.${DOMAIN}" "wall-controller-${i}.${DOMAIN},wall-controller-${i}" "true"
done

for i in $(seq -w 1 28); do
  issue_cert "source-agent-${i}" "source-agent-${i}.${DOMAIN}" "source-agent-${i}.${DOMAIN},source-agent-${i}" "true"
done

echo "[+] Lab certs generated in: $OUTDIR"
echo "    Trust chain: ca-chain.pem"
