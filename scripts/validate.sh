#!/usr/bin/env bash
# Validate apps/ and modules/ with the Python reference validator and the Go kernel for each example site,
# Both validators find modules/ next to apps/. Usage: scripts/validate.sh [site-dir ...]
# Prints "0 errors" and exits 0 only when everything is clean.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/lib.sh"
cd "$ROOT"

if [ "$#" -gt 0 ]; then sites=("$@"); else sites=(examples/sites/*/); fi
PYTHON=${PYTHON:-python3}
status=0
for site in "${sites[@]}"; do
  site=${site%/}
  echo "== $site: python"
  "$PYTHON" "$RANGKA_OS/tools/rangka_validate.py" apps --site "$site" --connectors "$CONNECTORS" || status=1
  echo "== $site: go"
  "$RANGKA_BIN" validate --apps apps --site "$site" --connectors "$CONNECTORS" || status=1
done

# Approval presets (docs/presets/approvals/*.rk): each one is copied, as onboarding does, to custom/<model>.rk of a
# scratch copy of demo-jasa and validated by both validators, so a broken preset fails here and not at an owner's site.
# Skipped when site arguments were given.
if [ "$#" -eq 0 ] && [ -d docs/presets/approvals ]; then
  for preset in docs/presets/approvals/*.rk; do
    model=$(sed -n 's/^model: *//p' "$preset" | head -1)
    if [ -z "$model" ]; then echo "== preset $preset: no model line" >&2; status=1; continue; fi
    rm -rf "$ROOT/.validate-presets"
    mkdir -p "$ROOT/.validate-presets"
    cp -RL examples/sites/demo-jasa "$ROOT/.validate-presets/site"
    cp "$preset" ".validate-presets/site/custom/$model.rk"
    echo "== preset $preset: python"
    "$PYTHON" "$RANGKA_OS/tools/rangka_validate.py" apps --site .validate-presets/site --connectors "$CONNECTORS" || status=1
    echo "== preset $preset: go"
    "$RANGKA_BIN" validate --apps apps --site .validate-presets/site --connectors "$CONNECTORS" || status=1
  done
  rm -rf "$ROOT/.validate-presets"
fi

if [ "$status" -eq 0 ]; then echo "0 errors"; else echo "validation failed" >&2; fi
exit "$status"
