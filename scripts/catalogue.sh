#!/usr/bin/env bash
# Build build/catalogue/<name>, one symlink per module (modules/<name>) and per app (apps/<name>).
# The kernel and the validators read a flat directory of units; this folder layout is interim (contract 0001).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=$ROOT/build/catalogue
rm -rf "$OUT"
mkdir -p "$OUT"
for kind in modules apps; do
  [ -d "$ROOT/$kind" ] || continue
  for dir in "$ROOT/$kind"/*/; do
    [ -d "$dir" ] || continue
    name=$(basename "$dir")
    if [ -e "$OUT/$name" ]; then
      echo "catalogue: name '$name' exists as both a module and an app" >&2
      exit 1
    fi
    ln -s "../../$kind/$name" "$OUT/$name"
  done
done
echo "catalogue: $(find "$OUT" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' ') units in build/catalogue"
