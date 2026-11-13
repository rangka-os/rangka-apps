#!/usr/bin/env python3
"""Upgrade test: a site migrated and seeded with an OLD tree of this repo gets data, then the same database is
migrated with THIS tree and checked. New fields and computes must behave on rows written before they existed.

    python3 tests/upgrade.py --site demo-jasa --check pph23 [--from origin/main]

--check <name> loads tests/upgrade_<name>.py, which defines before(api) -> state (runs on the old tree) and
after(api, state) (runs on this tree, using check/eq from tests/day_one.py). The old tree is `git archive <ref>`
into a temp dir. Needs what tests/day_one.py needs (RANGKA_OS, Postgres binaries).
"""
import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
NEW_ROOT = os.path.dirname(HERE)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # day_one binds its sibling modules through sys.modules
    spec.loader.exec_module(mod)
    return mod


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", required=True, choices=["kopi-senja", "demo-jasa", "demo-fnb", "demo-manpower"])
    ap.add_argument("--new-site", action="store_true", help="run the old tree on THIS tree's site folder (a check that needs an org or setting the old site lacks)")
    ap.add_argument("--check", required=True)
    ap.add_argument("--from", dest="ref", default="origin/main")
    a = ap.parse_args()
    sys.argv = [sys.argv[0], "--site", a.site]
    d1 = load(os.path.join(HERE, "day_one.py"), "day_one")
    chk = load(os.path.join(HERE, f"upgrade_{a.check}.py"), f"upgrade_{a.check}")
    old = tempfile.mkdtemp(prefix="rangka-apps-old-")
    arc = subprocess.run(["git", "-C", NEW_ROOT, "archive", a.ref], capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", old], input=arc, check=True)
    state_dir = tempfile.mkdtemp(prefix="rangka-upgrade-")
    d1.ROOT = old
    k = d1.Kernel(a.site, state_dir, site_dir=os.path.join(NEW_ROOT if a.new_site else old, "examples", "sites", a.site))
    k.start()
    d1.CTX["kernel"] = k   # a check may sign in to another org of the site
    try:
        state = chk.before(d1.Api(k.base))
        k.stop_serve()
        d1.ROOT = NEW_ROOT
        k.site_dir = os.path.join(NEW_ROOT, "examples", "sites", a.site)
        r = subprocess.run([k.binary, "migrate"] + k.args(), capture_output=True, text=True)
        d1.check(f"migrate {a.ref} -> this tree", r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:])
        k.serve()
        chk.after(d1.Api(k.base), state, d1)
    finally:
        k.stop()
    _, n_gap, n_fail = d1.report()
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
