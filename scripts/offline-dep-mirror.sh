#!/usr/bin/env bash
set -euo pipefail

# Mirror Python wheels/sdists for offline install
# Usage:
#   ./offline-dep-mirror.sh /path/to/repo
REPO="${1:-.}"
OUT="${OUT:-./offline-pypi}"
mkdir -p "$OUT"

echo "[pip] downloading requirements for agents/tools..."
find "$REPO" -name requirements.txt -print0 | while IFS= read -r -d '' req; do
  echo "  - $req"
  python3 -m pip download -r "$req" -d "$OUT"
done

# Mirror UI dependencies (optional)
if [ -f "$REPO/ui/package.json" ]; then
  echo "[npm] packing UI"
  mkdir -p ./offline-npm
  (cd "$REPO/ui" && npm pack --pack-destination ../offline-npm)
fi

echo "Done. Python artifacts in $OUT"
