#!/usr/bin/env bash
# Build the worker Lambda layer: pip-installs worker runtime deps into
# workers/layer/python (matching the `python/` layout Lambda merges into
# /opt/python). Run in CI before `cdk deploy`.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAYER="$ROOT/workers/layer"

rm -rf "$LAYER/python"
mkdir -p "$LAYER/python"

python -m pip install --quiet --no-cache-dir \
  --requirement "$ROOT/workers/requirements.txt" \
  --target "$LAYER/python"

# Drop the placeholder kept in git so the layer path exists pre-CI.
rm -f "$LAYER/python/_keep.py"

echo "layer built at $LAYER/python"
du -sh "$LAYER/python"