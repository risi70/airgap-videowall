#!/usr/bin/env bash
set -euo pipefail

REGISTRY_LOCAL="${REGISTRY_LOCAL:-registry.local:5000}"

# External images (pin versions in your Helm charts / values files)
EXTERNAL_IMAGES=(
  "docker.io/postgres:15"
  "quay.io/keycloak/keycloak:latest"
  "hashicorp/vault:latest"
  "prom/prometheus:latest"
  "grafana/grafana:latest"
  "grafana/loki:latest"
  "grafana/promtail:latest"
  "metallb/controller:latest"
  "metallb/speaker:latest"
  "docker.io/nginx:stable"
  "docker.io/janus-gateway/janus-gateway:latest"
)

# Platform images (built in your CI and pushed to local registry)
PLATFORM_IMAGES=(
  "${REGISTRY_LOCAL}/vw-mgmt-api:latest"
  "${REGISTRY_LOCAL}/vw-ui:latest"
  "${REGISTRY_LOCAL}/vw-health:latest"
  "${REGISTRY_LOCAL}/vw-sfu-gateway:latest"
  "${REGISTRY_LOCAL}/vw-compositor:latest"
)

pull_all() {
  for img in "${EXTERNAL_IMAGES[@]}" "${PLATFORM_IMAGES[@]}"; do
    echo "[pull] $img"
    docker pull "$img"
  done
}

save_all() {
  local out="${1:-images.tar.zst}"
  echo "[save] -> $out"
  docker save "${EXTERNAL_IMAGES[@]}" "${PLATFORM_IMAGES[@]}" | zstd -10 -T0 -o "$out"
}

load_all() {
  local in="${1:-images.tar.zst}"
  echo "[load] <- $in"
  zstd -d -c "$in" | docker load
}

retag_and_push() {
  # Retag external images into the air-gapped registry
  for img in "${EXTERNAL_IMAGES[@]}"; do
    local name tag
    name="$(echo "$img" | sed 's#^.*/##')"
    tag="${REGISTRY_LOCAL}/ext/${name}"
    echo "[retag] $img -> $tag"
    docker tag "$img" "$tag"
    docker push "$tag"
  done
}

case "${1:-}" in
  pull_all) pull_all ;;
  save_all) save_all "${2:-images.tar.zst}" ;;
  load_all) load_all "${2:-images.tar.zst}" ;;
  retag_and_push) retag_and_push ;;
  *)
    echo "Usage: $0 {pull_all|save_all OUT|load_all IN|retag_and_push}"
    exit 2
  ;;
esac
