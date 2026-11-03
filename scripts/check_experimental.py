#!/usr/bin/env python3
"""Check the experimental sidecars: modules/<m>/experimental/<model>.rk.

A sidecar holds keys that the kernel does not read yet: {model, approvals, naming, fields, defaults}.
Rules:
  - `model` equals the file stem and names a model in the same module (modules/<m>/models/<model>.rk)
  - every `fields[].name` is new to that model
  - every `defaults` key is a field of the model (or one the sidecar adds)
  - every approval's `approvers` role exists in some roles/ folder (modules, apps) or is the built-in `owner`;
    approval names are unique, `on` is set, `approvers` is a non-empty list
  - `stream` (when present) is a list of known kernel streams (S2, S3, S4, S5, S7, S8a ...)
  - `naming` (when present) is {series: <name>, pattern: <string with one {#..} counter>}
  - every added field has a `name` and a `type`

Exits 1 and prints one line per problem; prints a summary when clean.
"""
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BUILTIN_ROLES = {"owner"}
KNOWN_STREAMS = {"S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8a", "S8-task7", "S9", "S10"}


class StrictLoader(yaml.SafeLoader):
    """YAML where only true/false are booleans: `on:` (an approval's trigger) must stay the string "on"."""


StrictLoader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:bool"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
StrictLoader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$"), list("tf"))


def load(path):
    with open(path, encoding="utf-8") as f:
        return yaml.load(f, Loader=StrictLoader) or {}


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

        streams = data.get("stream")
        if streams is not None:
            if not isinstance(streams, list) or not streams:
                err("stream must be a non-empty list")
            else:
                for st in streams:
                    if st not in KNOWN_STREAMS:
                        err(f"unknown stream {st!r}")

        naming = data.get("naming")
        if naming is not None:
            if not isinstance(naming, dict) or not isinstance(naming.get("series"), str):
                err("naming must be {series: <name>, pattern: <pattern>}")
            else:
                pattern = naming.get("pattern")
                if pattern is not None and len(re.findall(r"\{#{1,9}\}", str(pattern))) != 1:
                    err(f"naming pattern {pattern!r} needs exactly one {{#...}} counter")

        added = set()
        for f in data.get("fields", []) or []:
            if not isinstance(f, dict) or not f.get("name") or not f.get("type"):
                err(f"field entry needs name and type: {f!r}")
                continue
            name = f.get("name")
            if name in existing:
                err(f"field {name!r} already exists on the model")
            if name in added:
                err(f"field {name!r} listed twice")
            added.add(name)

        for key in (data.get("defaults") or {}):
            if key not in existing and key not in added:
                err(f"default {key!r} is not a field of the model")

        seen_ap = set()
        for ap in data.get("approvals", []) or []:
            if ap.get("name") in seen_ap:
                err(f"approval {ap.get('name')!r} listed twice")
            seen_ap.add(ap.get("name"))
            if not ap.get("on"):
                err(f"approval {ap.get('name')!r}: missing `on`")
            if not ap.get("approvers"):
                err(f"approval {ap.get('name')!r}: `approvers` must list at least one role")
            for role in ap.get("approvers", []) or []:
                if role not in roles:
                    err(f"approval {ap.get('name')!r}: role {role!r} not found in any roles/ folder")

    for e in errors:
        print(e)
    print(f"{checked} sidecar(s) checked, {len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
