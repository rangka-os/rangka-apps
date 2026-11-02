#!/usr/bin/env python3
"""Check the experimental sidecars: modules/<m>/experimental/<model>.rk.

A sidecar holds keys that the kernel does not read yet: {model, approvals, naming, fields, defaults}.
Rules:
  - `model` equals the file stem and names a model in the same module (modules/<m>/models/<model>.rk)
  - every `fields[].name` is new to that model
  - every `defaults` key is a field of the model (or one the sidecar adds)
  - every approval's `approvers` role exists in some roles/ folder (modules, apps) or is the built-in `owner`

Exits 1 and prints one line per problem; prints a summary when clean.
"""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BUILTIN_ROLES = {"owner"}


def load(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def role_names():
    names = set(BUILTIN_ROLES)
    for kind in ("modules", "apps"):
        for p in sorted((ROOT / kind).glob("*/roles/*.rk")):
            names.add(load(p).get("name", p.stem))
    return names


def main():
    roles = role_names()
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

        added = set()
        for f in data.get("fields", []) or []:
            name = f.get("name")
            if name in existing:
                err(f"field {name!r} already exists on the model")
            if name in added:
                err(f"field {name!r} listed twice")
            added.add(name)

        for key in (data.get("defaults") or {}):
            if key not in existing and key not in added:
                err(f"default {key!r} is not a field of the model")

        for ap in data.get("approvals", []) or []:
            for role in ap.get("approvers", []) or []:
                if role not in roles:
                    err(f"approval {ap.get('name')!r}: role {role!r} not found in any roles/ folder")

    for e in errors:
        print(e)
    print(f"{checked} sidecar(s) checked, {len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
