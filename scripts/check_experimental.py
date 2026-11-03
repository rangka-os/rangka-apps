#!/usr/bin/env python3
"""Check the experimental sidecars: modules/<m>/experimental/<model>.rk.

A sidecar holds what the kernel cannot read yet. Today that is one thing: the `ppn_applies` default taken from the
org's `pkp` (CEL has no `org` variable until stream S5). Keys: {model, stream, defaults}.
Rules:
  - `model` equals the file stem and names a model in the same module (modules/<m>/models/<model>.rk)
  - `stream` is a non-empty list of known kernel streams (S1 ... S10)
  - every `defaults` key is a field of the model

Exits 1 and prints one line per problem; prints a summary when clean.
"""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
KNOWN_STREAMS = {"S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8a", "S8", "S9", "S10"}


def load(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def main():
    errors = []
    checked = 0
    for side in sorted((ROOT / "modules").glob("*/experimental/*.rk")):
        checked += 1
        rel = side.relative_to(ROOT)
        module = side.parent.parent
        data = load(side)

        def err(msg):
            errors.append(f"{rel}: {msg}")

        if data.get("model") != side.stem:
            err(f"model is {data.get('model')!r}, expected {side.stem!r}")
        model_file = module / "models" / f"{side.stem}.rk"
        if not model_file.exists():
            err(f"model {side.stem!r} not found in {module.relative_to(ROOT)}/models")
            continue
        existing = {f["name"] for f in load(model_file).get("fields", [])}

        streams = data.get("stream")
        if not isinstance(streams, list) or not streams:
            err("stream must be a non-empty list")
        else:
            for st in streams:
                if st not in KNOWN_STREAMS:
                    err(f"unknown stream {st!r}")

        for key in (data.get("defaults") or {}):
            if key not in existing:
                err(f"default {key!r} is not a field of the model")

    for e in errors:
        print(e)
    print(f"{checked} sidecar(s) checked, {len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
