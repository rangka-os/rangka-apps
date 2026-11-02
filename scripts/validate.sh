#!/usr/bin/env bash
# Validate build/catalogue with the Python reference validator and the Go kernel for each example site,
# then check the experimental sidecars. Usage: scripts/validate.sh [site-dir ...]
# Prints "0 errors" and exits 0 only when everything is clean.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/lib.sh"
cd "$ROOT"

"$HERE/catalogue.sh" || exit 1

if [ "$#" -gt 0 ]; then sites=("$@"); else sites=(examples/sites/*/); fi
PYTHON=${PYTHON:-python3}
status=0
for site in "${sites[@]}"; do
  site=${site%/}
  echo "== $site: python"
  "$PYTHON" "$RANGKA_OS/tools/rangka_validate.py" build/catalogue --site "$site" --connectors "$CONNECTORS" || status=1
  echo "== $site: go"
  "$RANGKA_BIN" validate --apps build/catalogue --site "$site" --connectors "$CONNECTORS" || status=1
done
echo "== sidecars"
"$PYTHON" "$HERE/check_experimental.py" || status=1

if [ "$status" -eq 0 ]; then echo "0 errors"; else echo "validation failed" >&2; fi
exit "$status"
