#!/usr/bin/env bash
set -euo pipefail

RELEASE="${RELEASE:-videowall}"
NS="${NS:-vw-control}"

rollback_helm() {
  local revision="${1:-}"
  if [ -z "$revision" ]; then
    echo "Usage: $0 helm <revision>"
    exit 2
  fi
  helm rollback "$RELEASE" "$revision" --namespace "$NS"
}

restore_layout_cache_note() {
  cat <<'EOF'
Layout rollback (endpoint-side):
  - wallctl already caches last-known-good layout at:
      /var/lib/vw-wallctl/last-known-good-layout.json
  - If a bad layout is activated, re-activate a previous layout via API,
    or replace the cache file and restart vw-wallctl:
      systemctl restart vw-wallctl
EOF
}

case "${1:-}" in
  helm) rollback_helm "${2:-}" ;;
  layout-note) restore_layout_cache_note ;;
  *) echo "Usage: $0 {helm <revision>|layout-note}" ; exit 2 ;;
esac
