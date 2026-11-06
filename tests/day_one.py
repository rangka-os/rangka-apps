#!/usr/bin/env python3
"""Day-one integration test against a live kernel (Python stdlib only).

    python3 tests/day_one.py [--site kopi-senja|demo-jasa|demo-fnb|demo-ritel|demo-grosir|demo-produksi] [--strict] [--keep]
    DAY_ONE_PORT=9201 python3 tests/day_one.py --site demo-grosir --keep     # a demo on a fixed port

Starts a throwaway Postgres (rangka-os/kernel/scripts/pgtemp.sh), runs `rangka migrate`, `seed` and
`serve --dev-open` on a free port, waits for /api/v1/system/status, runs the scenarios of the site, and stops everything.
Each check is ok(name), fail(name, detail) or gap(name, owner, reason). A gap is a genuine kernel limitation that no merged stream
covers, named with its owner (docs/kernel-hooks.md, Kernel gaps). A setup failure, or a hook or endpoint of a stream merged on rangka-os
main (S2 approvals, S3 prints, S7 imports, S8a money, S8c/S8d/S8e hooks) answering 501, hook.skipped or 404, is a fail.
Exit 1 on any fail, and on any gap with --strict. The last line reads `N ok · M gaps · K failed`.
"""
import argparse
import atexit
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = []  # (status, name, detail)


# ---------- results

def ok(name):
    RESULTS.append(("ok", name, ""))


def fail(name, detail):
    RESULTS.append(("fail", name, str(detail)))


def gap(name, owner, reason):
    RESULTS.append(("gap", name, f"{owner}: {reason}"))


def check(name, cond, detail=""):
    """ok when cond is true, else fail with detail."""
    if cond:
        ok(name)
    else:
        fail(name, detail)
    return bool(cond)


def report():
    width = max([len(n) for _, n, _ in RESULTS] + [10])
    for st, name, detail in RESULTS:
        print(f"{st.upper():5} {name.ljust(width)}  {detail}".rstrip())
    n_ok = sum(1 for r in RESULTS if r[0] == "ok")
    n_gap = sum(1 for r in RESULTS if r[0] == "gap")
    n_fail = sum(1 for r in RESULTS if r[0] == "fail")
    print(f"{n_ok} ok · {n_gap} gaps · {n_fail} failed")
    return n_ok, n_gap, n_fail


# ---------- environment

def lib_env():
    """RANGKA_OS and RANGKA_BIN as scripts/lib.sh finds them (builds the kernel when missing)."""
    out = subprocess.run(["bash", "-c", '. "$1/scripts/lib.sh" && echo "$RANGKA_OS" && echo "$RANGKA_BIN" && echo "$CONNECTORS"', "x", ROOT],
                         capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit("lib.sh: " + out.stderr.strip())
    os_dir, binary, connectors = out.stdout.strip().split("\n")
    return os_dir, binary, connectors


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# Orgs of each site and the fixtures directory (examples/fixtures/<dir>) seeded into each; the first org is the one the
# server serves in dev-open mode. demo-jasa has two PTs (contract 0010); kopi-senja keeps the default org (the site name).
SITE_ORGS = {
    "kopi-senja": [(None, "kopi-senja")],
    "demo-jasa": [("pt_contoh_inspeksi", "demo-jasa"), ("cv_contoh_pelatihan", "demo-jasa-pelatihan")],
    "demo-fnb": [("tahu_contoh", "demo-fnb")],
    "demo-ritel": [("toko_contoh", "demo-ritel")],
    "demo-grosir": [("niaga_contoh", "demo-grosir")],
    "demo-produksi": [("pangan_contoh", "demo-produksi")],
}


class Kernel:
    """A temp Postgres plus `rangka serve` for one site."""

    def __init__(self, site, state_dir, site_dir=None):
        self.site = site
        self.site_dir = site_dir or os.path.join(ROOT, "examples", "sites", site)
        self.os_dir, self.binary, self.connectors = lib_env()
        self.pgtemp = os.path.join(self.os_dir, "kernel", "scripts", "pgtemp.sh")
        self.env = dict(os.environ, PGTEMP_STATE=os.path.join(state_dir, "pgtemp.state"))
        self.proc = None
        self.log = open(os.path.join(state_dir, "serve.log"), "w+")
        self.logpath = self.log.name
        self.port = int(os.environ.get("DAY_ONE_PORT") or 0) or free_port()   # a fixed port for a --keep demo
        self.base = f"http://127.0.0.1:{self.port}"
        self.db = None
        self.owner_email = "owner@contoh.test"      # the seeded owner of every org; a throwaway account of a throwaway database
        self.owner_pw = secrets.token_urlsafe(18)
        self.orgs = SITE_ORGS[site]

    def args(self):
        return ["--apps", os.path.join(ROOT, "apps"), "--site", self.site_dir, "--connectors", self.connectors, "--db", self.db]

    def start(self):
        r = subprocess.run(["bash", self.pgtemp, "start"], env=self.env, capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit("pgtemp start failed: " + r.stderr)
        self.db = r.stdout.strip().split("\n")[-1]
        steps = [("migrate", [])]
        for org, fixtures in self.orgs:     # `seed --org` fills one org with its own fixtures
            steps.append(("seed", ["--fixtures", os.path.join(ROOT, "examples", "fixtures", fixtures),
                                   "--email", self.owner_email, "--password", self.owner_pw] + (["--org", org] if org else [])))
        for verb, extra in steps:
            r = subprocess.run([self.binary, verb] + self.args() + extra, capture_output=True, text=True)
            if r.returncode != 0:
                sys.exit(f"rangka {verb} failed:\n{r.stdout}\n{r.stderr}")
        self.serve()

    def serve(self):
        first = self.orgs[0][0]
        web = os.path.join(self.os_dir, "web")    # the app's screens, for a --keep demo in a browser
        self.proc = subprocess.Popen([self.binary, "serve"] + self.args() + (["--org", first] if first else []) + (["--web", web] if os.path.isdir(web) else [])
                                     + ["--dev-open", "--addr", f"127.0.0.1:{self.port}"],
                                     stdout=self.log, stderr=subprocess.STDOUT)
        for _ in range(100):
            if self.proc.poll() is not None:
                self.log.seek(0)
                sys.exit("serve exited:\n" + self.log.read())
            try:
                urllib.request.urlopen(self.base + "/api/v1/system/status", timeout=1).read()
                return
            except Exception:
                time.sleep(0.2)
        sys.exit("serve did not become ready")

    def stop_serve(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(10)

    def user_add(self, email, role, org, name, outlets="", record="", password=""):
        """`rangka user add`: a member of `org` with `role` (and the outlets of contract 0010). Returns (exit code, output)."""
        cmd = [self.binary, "user", "add", email, "--org", org, "--role", role, "--name", name, "--db", self.db]
        if outlets:
            cmd += ["--outlets", outlets]
        if record:
            cmd += ["--record", record]
        r = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ, RANGKA_USER_PASSWORD=password))
        return r.returncode, (r.stdout + r.stderr).strip()

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.db:
            subprocess.run(["bash", self.pgtemp, "stop"], env=self.env, capture_output=True)
            self.db = None


# ---------- REST client

class Api:
    def __init__(self, base, cookie=""):
        self.base = base
        self.cookie = cookie  # "rk_session=..." of a signed-in person; empty in dev-open mode (the owner)

    @classmethod
    def login(cls, base, email, password, org=None):
        """A client signed in as `email` (POST /auth/login) in `org` (default: the user's first membership), or None when refused."""
        body = {"email": email, "password": password}
        if org:
            body["org"] = org
        req = urllib.request.Request(base + "/auth/login", data=json.dumps(body).encode(),
                                     method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                for c in r.headers.get_all("Set-Cookie") or []:
                    if c.startswith("rk_session="):
                        return cls(base, c.split(";")[0])
        except urllib.error.HTTPError:
            pass
        return None

    def headers(self, extra=None):
        h = dict(extra or {})
        if self.cookie:
            h["Cookie"] = self.cookie
        return h

    def upload(self, path, fields, filename, content):
        """POST multipart/form-data with form `fields` and the file part `file`."""
        boundary = "----rk" + secrets.token_hex(8)
        parts = []
        for k, v in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: text/csv\r\n\r\n'.encode()
                     + content + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        req = urllib.request.Request(self.base + path, data=b"".join(parts), method="POST",
                                     headers=self.headers({"Content-Type": f"multipart/form-data; boundary={boundary}"}))
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"error": {"code": "non_json", "message": raw.decode(errors="replace")[:200]}}

    def call(self, method, path, body=None):
        data = None if body is None and method == "GET" else json.dumps(body or {}).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=self.headers({"Content-Type": "application/json"}))
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"error": {"code": "non_json", "message": raw.decode(errors="replace")[:200]}}

    def html(self, path):
        """GET a text page (print): (status, body text, content type)."""
        req = urllib.request.Request(self.base + path, headers=self.headers())
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read().decode("utf-8", "replace"), r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace"), e.headers.get("Content-Type", "")

    def print_html(self, model, name, fmt=""):
        """The HTML print of a record: GET /api/v1/r/{model}/{name}/print?as=html[&format=...]."""
        return self.html(f"/api/v1/r/{model}/{self.q(name)}/print?as=html" + (f"&format={fmt}" if fmt else ""))

    @staticmethod
    def q(name):
        """A record name as one path segment: numbers like INV/PTA/26/0012 contain slashes."""
        return urllib.parse.quote(str(name), safe="")

    def patch(self, model, name, body):
        return self.call("PATCH", f"/api/v1/r/{model}/{self.q(name)}", body)

    def create(self, model, body):
        return self.call("POST", f"/api/v1/r/{model}", body)

    def get(self, model, name):
        return self.call("GET", f"/api/v1/r/{model}/{self.q(name)}")[1]

    def verb(self, model, name, verb, body=None):
        return self.call("POST", f"/api/v1/r/{model}/{self.q(name)}/{verb}", body or {})

    def action(self, model, name, action, body=None):
        return self.call("POST", f"/api/v1/r/{model}/{self.q(name)}/actions/{action}", body or {})

    def list(self, model, **q):
        qs = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in q.items())
        return self.call("GET", f"/api/v1/r/{model}" + ("?" + qs if qs else ""))[1]["data"]

    def find(self, model, where, **q):
        """Records of `model` matching a CEL filter, e.g. find("stock_balance", "outlet == 'OUT-02'") (up to 200)."""
        return self.list(model, filter=where, limit=200, **q)

    def audit(self, kind):
        return self.call("GET", f"/api/v1/system/audit?kind={kind}&limit=200")[1]["data"]


def approvals_of(api, model, name):
    """The approval requests recorded for one record (contract 0007)."""
    st, resp = api.call("GET", f"/api/v1/approvals?model={model}&name={Api.q(name)}")
    return (resp or {}).get("data", []) if st == 200 else []


def installed(api, model):
    """True when the catalogue installed on the site defines `model` (GET /meta lists models by name)."""
    st, meta = api.call("GET", "/api/v1/meta")
    return st == 200 and model in (meta or {}).get("models", {})


def messages(resp):
    """Rule and field messages of a 422 body, or the error message of any other error."""
    if not isinstance(resp, dict):
        return []
    if "errors" in resp:
        return [e.get("message", "") for e in resp["errors"]]
    return [resp.get("error", {}).get("message", "")]


def skipped(api, hook, target):
    """True when the audit log says `hook` was skipped for target (not implemented in this kernel)."""
    return any(hook in e.get("summary", "") and e.get("target") == target for e in api.audit("hook.skipped"))


# ---------- scenarios: invoice arithmetic (every site that has invoicing)

def invoice_body(customer, item, rate=10_000_000, qty=1, **extra):
    body = {"customer": customer, "items": [{"item": item, "qty": qty, "rate": rate}]}
    body.update(extra)
    return body


def eq(name, got, want):
    check(name, got == want, f"got {got!r}, want {want!r}")


def services(api):
    """The fixture's service items in name order (stocked materials share the list on sites with procurement)."""
    return sorted([i for i in api.list("item") if i.get("kind") == "service"], key=lambda i: i["name"])


def scenario_invoicing(api):
    customer = api.list("customer")[0]["name"]
    item = api.list("item")[0]["name"]

    # kopi-senja fixtures: existing invoices still read with the new defaults
    existing = api.list("sales_invoice")
    check("fixture invoices valid with new fields", bool(existing) and all(r.get("kind") == "regular" and r.get("tax_rate") == 12 for r in existing),
          f"first: {existing[0] if existing else None}")

    # PKP: PPN on
    st, inv = api.create("sales_invoice", invoice_body(customer, item))
    check("create invoice (PKP)", st == 201, inv)
    if st != 201:
        return
    eq("PKP subtotal", inv["subtotal"], 10_000_000)
    eq("PKP discount 0", inv["discount_amount"], 0)
    eq("PKP dpp", inv["dpp"], 10_000_000)
    eq("PKP tax_rate 12 with DPP 11/12 stored on the document", (inv["tax_rate"], inv["dpp_num"], inv["dpp_den"]), (12, 11, 12))
    eq("PKP ppn 1.100.000", inv["ppn"], 1_100_000)
    eq("PKP grand_total 11.100.000", inv["grand_total"], 11_100_000)
    eq("PKP amount_due = grand_total", inv["amount_due"], 11_100_000)
    eq("PKP outstanding", inv["outstanding"], 11_100_000)

    # non-PKP: PPN off
    st, inv = api.create("sales_invoice", invoice_body(customer, item, tax_rate=0))
    eq("non-PKP ppn 0", inv.get("ppn"), 0)
    eq("non-PKP grand_total 10.000.000", inv.get("grand_total"), 10_000_000)

    # discount
    st, inv = api.create("sales_invoice", invoice_body(customer, item, discount_amount=1_000_000))
    eq("discount 1.000.000 -> dpp 9.000.000", inv.get("dpp"), 9_000_000)
    eq("discount -> ppn 990.000", inv.get("ppn"), 990_000)
    st, resp = api.create("sales_invoice", invoice_body(customer, item, discount_amount=10_000_001))
    check("discount above subtotal rejected", st == 422 and any("Diskon" in m for m in messages(resp)), (st, resp))

    # PPh 23
    st, inv = api.create("sales_invoice", invoice_body(customer, item, pph23_applies=True))
    eq("PPh 23 amount 200.000", inv.get("pph23_amount"), 200_000)
    eq("PPh 23 amount_due 10.900.000", inv.get("amount_due"), 10_900_000)

    # retention 5%
    st, inv = api.create("sales_invoice", invoice_body(customer, item, retention_percent=5))
    eq("retention 5% -> 500.000", inv.get("retention_amount"), 500_000)
    eq("retention amount_due 10.600.000", inv.get("amount_due"), 10_600_000)
    st, resp = api.create("sales_invoice", invoice_body(customer, item, retention_percent=25))
    check("retention above 20% rejected", st == 422, (st, resp))
    for label, line in (("qty 0", {"qty": 0}), ("negative rate", {"rate": -1}), ("line discount above 100%", {"discount": 101}), ("negative line discount", {"discount": -5})):
        st, resp = api.create("sales_invoice", {"customer": customer, "items": [dict({"item": item, "qty": 1, "rate": 1_000_000}, **line)]})
        check(f"invoice line bounds: {label} -> 422", st == 422, (st, resp))

    # odd amounts round to whole rupiah
    st, inv = api.create("sales_invoice", invoice_body(customer, item, rate=1_234_567))
    eq("ppn rounds to whole rupiah: 1.234.567 -> 135.802", inv.get("ppn"), 135_802)

    # line uom default: sales_uom when set, else stock_uom (CEL: link compared with '')
    st, u1 = api.create("uom", {"uom_name": "Satuan uji A"})
    st, u2 = api.create("uom", {"uom_name": "Satuan uji B"})
    st, plain = api.create("item", {"item_name": "Barang uji tanpa satuan jual", "stock_uom": u1["name"], "kind": "good", "rate": 1000})
    st, sold = api.create("item", {"item_name": "Barang uji satuan jual", "stock_uom": u1["name"], "sales_uom": u2["name"], "kind": "good", "rate": 1000})
    st, inv = api.create("sales_invoice", {"customer": customer, "items": [{"item": plain["name"]}, {"item": sold["name"]}]})
    rows = inv.get("items", []) if st == 201 else []
    check("line uom defaults to stock_uom without sales_uom", len(rows) == 2 and rows[0].get("uom") == u1["name"], rows)
    check("line uom defaults to sales_uom when set", len(rows) == 2 and rows[1].get("uom") == u2["name"], rows)
    check("line rate defaults to item rate", len(rows) == 2 and rows[0].get("rate") == 1000, rows)

    # down payment, then a regular invoice deducting it
    st, dp = api.create("sales_invoice", invoice_body(customer, item, rate=3_000_000, kind="down_payment"))
    check("create DP invoice", st == 201, dp)
    if st != 201:
        return
    st, resp = api.create("sales_invoice", invoice_body(customer, item, dp_invoice=dp["name"], down_payment=1))
    check("DP not submitted yet -> deduction refused", st == 422, (st, resp))
    st, resp = api.verb("sales_invoice", dp["name"], "submit", {"force": True})
    check("submit DP invoice", st == 200, resp)
    dp_no = resp.get("name", dp["name"])  # submit renames DRAFT-… to the numbered name
    st, reg = api.create("sales_invoice", invoice_body(customer, item, dp_invoice=dp_no, down_payment=3_000_000))
    check("create regular invoice with DP deduction", st == 201, reg)
    if st == 201:
        eq("DP deducted: dpp 7.000.000", reg["dpp"], 7_000_000)
        eq("DP deducted: ppn 770.000", reg["ppn"], 770_000)
    st, resp = api.create("sales_invoice", invoice_body(customer, item, dp_invoice=dp_no, down_payment=4_000_000))
    check("DP over-deduction 4.000.000 vs 3.000.000 -> rule error",
          st == 422 and any("Potongan uang muka melebihi" in m for m in messages(resp)), (st, resp))
    st, resp = api.create("sales_invoice", invoice_body(customer, item, down_payment=1_000_000))
    check("deduction without a DP invoice -> rule error", st == 422 and any("faktur uang muka" in m.lower() for m in messages(resp)), (st, resp))

    # retention invoice must point at a submitted invoice
    st, resp = api.create("sales_invoice", invoice_body(customer, item, kind="retention"))
    check("retention invoice without retention_of -> rule error", st == 422, (st, resp))
    st, draft = api.create("sales_invoice", invoice_body(customer, item))
    st, resp = api.create("sales_invoice", invoice_body(customer, item, kind="retention", retention_of=draft["name"]))
    check("retention_of a draft -> rule error", st == 422 and any("retensi" in m.lower() for m in messages(resp)), (st, resp))
    # review item 3: a retention invoice releases what the origin retained, without PPN again
    st, base = api.create("sales_invoice", invoice_body(customer, item, retention_percent=5))
    st, resp = api.verb("sales_invoice", base["name"], "submit", {"force": True})
    origin = resp.get("name", base["name"])
    eq("origin invoice retains 5% of 10.000.000", api.get("sales_invoice", origin)["retention_amount"], 500_000)
    st, ret = api.create("sales_invoice", invoice_body(customer, item, rate=500_000, kind="retention", retention_of=origin))
    check("retention invoice for the retained 500.000 accepted, tax_rate defaults to 0 and no PPN",
          st == 201 and (ret["tax_rate"], ret["ppn"], ret["grand_total"]) == (0, 0, 500_000), (st, ret))
    st, resp = api.create("sales_invoice", invoice_body(customer, item, rate=500_000, kind="retention", retention_of=origin, tax_rate=12))
    check("retention invoice charging PPN again (tax_rate 12) -> 422", st == 422 and any("retensi" in m.lower() for m in messages(resp)), (st, resp))
    st, resp = api.create("sales_invoice", invoice_body(customer, item, rate=50_000_000, kind="retention", retention_of=origin))
    check("retention invoice of 100x the retained amount -> 422", st == 422 and any("melebihi retensi" in m for m in messages(resp)), (st, resp))
    st, resp = api.create("sales_invoice", invoice_body(customer, item, rate=1_000, kind="retention", retention_of=dp_no))
    check("retention invoice against an invoice that retained nothing -> 422", st == 422, (st, resp))

    # receivable hooks (core ledger hook and invoicing.reverse_receivable)
    before = api.get("customer", customer)["outstanding"]
    st, resp = api.create("sales_invoice", invoice_body(customer, item))
    check("create invoice for hooks", st == 201, resp)
    name = resp["name"]
    st, resp = api.verb("sales_invoice", name, "submit", {"force": True})
    check("submit invoice", st == 200, resp)
    submitted = resp.get("name", name)
    after = api.get("customer", customer)["outstanding"]
    if skipped(api, "ledger.post_receivable", f"sales_invoice/{submitted}"):
        fail("ledger.post_receivable adds amount_due to customer.outstanding", "kernel (S8c hooks) is merged on main: hook.skipped in the audit log")
    else:
        eq("ledger.post_receivable adds 11.100.000 to outstanding", after - before, 11_100_000)
    st, resp = api.verb("sales_invoice", submitted, "cancel")
    check("cancel invoice", st == 200, resp)
    final = api.get("customer", customer)["outstanding"]
    if skipped(api, "invoicing.reverse_receivable", f"sales_invoice/{submitted}"):
        fail("invoicing.reverse_receivable subtracts amount_due on cancel", "kernel (S8c hooks) is merged on main: hook.skipped in the audit log")
    else:
        eq("cancel restores customer.outstanding", final, before)

# ---------- scenarios: project services (demo-jasa)

def scenario_masters_jasa(api):
    """Spec A1: customers with PIC contacts, vendors, and a price list of services per business unit (one price list per PT)."""
    cust = api.get("customer", "CUST-0001")
    prim = [c for c in cust["contacts"] if c.get("is_primary") in (True, "true")]
    check("customer has a primary PIC contact", len(prim) == 1 and prim[0]["person_name"] == "Bambang Contoh", cust["contacts"])
    eq("fixtures: 2 customers, 2 vendors, 6 items, 3 workers each with a certification",
       (len(api.list("customer")), len(api.list("vendor")), len(api.list("item")), len(api.list("worker", children=1))), (2, 2, 6, 3))
    check("fixtures: every worker has a certification", all(w.get("certifications") for w in api.list("worker", children=1)), "")
    eq("fixtures: currencies IDR and USD, tax codes PPN and PPh 23", (sorted(c["code"] for c in api.list("currency")), sorted(t["kind"] for t in api.list("tax_code"))), (["IDR", "USD"], ["pph23", "ppn"]))
    eq("fixtures: users with the four owner-led roles", sorted(u["role"] for u in api.list("user")), ["finance_manager", "project_admin", "purchasing_manager", "sales_manager"])
    st, pl = api.create("price_list", {"list_name": "Harga inspeksi 2026", "customer": "CUST-0001", "valid_from": "2026-01-01", "valid_until": "2026-12-31",
                                        "items": [{"item": "BRG-0001", "uom": "UOM-004", "rate": 1_400_000}, {"item": "BRG-0002", "uom": "UOM-001", "rate": 80_000}]})
    check("create a price list with 2 services for a customer (PT Contoh Inspeksi)", st == 201 and len(pl["items"]) == 2, (st, pl))
    org2 = Api.login(CTX["kernel"].base, CTX["kernel"].owner_email, CTX["kernel"].owner_pw, org="cv_contoh_pelatihan")
    if org2 is None:
        fail("sign in to CV Contoh Pelatihan", "login refused")
        return
    st, pl2 = org2.create("price_list", {"list_name": "Harga pelatihan 2026", "items": [{"item": "BRG-0001", "uom": "UOM-001", "rate": 9_500_000}]})
    check("create a price list in CV Contoh Pelatihan", st == 201, (st, pl2))
    eq("each PT sees only its own price list", ([p["list_name"] for p in api.list("price_list")], [p["list_name"] for p in org2.list("price_list")]),
       (["Harga inspeksi 2026"], ["Harga pelatihan 2026"]))


def scenario_quotation(api):
    customer = api.list("customer")[0]["name"]
    items = services(api)
    ndt, ut = items[0]["name"], items[1]["name"]

    st, q = api.create("quotation", {"customer": customer, "subject": "Inspeksi NDT shutdown 2026", "rfq_ref": "RFQ-001",
                                     "items": [{"item": ndt, "qty": 10, "rate": 1_500_000}, {"item": ut, "qty": 100, "rate": 85_000, "discount": 10}]})
    check("create quotation with 2 lines", st == 201, q)
    if st != 201:
        return
    eq("quotation line 1 amount 15.000.000", q["items"][0]["amount"], 15_000_000)
    eq("quotation line 2 amount (10% off) 7.650.000", q["items"][1]["amount"], 7_650_000)
    eq("quotation subtotal 22.650.000", q["subtotal"], 22_650_000)
    eq("quotation ppn 2.491.500", q["ppn"], 2_491_500)
    eq("quotation grand_total 25.141.500", q["grand_total"], 25_141_500)
    eq("quotation revision 0", q["revision"], 0)
    eq("quotation outcome open", q["outcome"], "open")
    check("quotation valid_until defaults 30 days after quotation_date", bool(q.get("valid_until")) and q["valid_until"] > q["quotation_date"], (q.get("quotation_date"), q.get("valid_until")))
    check("line uom defaults to the item's sales_uom", q["items"][0]["uom"] == items[0].get("sales_uom"), q["items"][0])

    st, resp = api.create("quotation", {"customer": customer, "subject": "x", "valid_until": "2000-01-01", "items": [{"item": ndt}]})
    check("valid_until before quotation_date rejected", st == 422, (st, resp))
    st, resp = api.create("quotation", {"customer": customer, "subject": "x", "discount_amount": 1_000_000, "items": [{"item": ndt, "qty": 1, "rate": 2_000_000}]})
    check("quotation has no header discount: a discount_amount in the body is refused or ignored (dpp = subtotal)",
          st >= 400 or (st == 201 and "discount_amount" not in resp and resp.get("dpp") == resp.get("subtotal") == 2_000_000), (st, resp))
    for label, line in (("qty 0", {"qty": 0}), ("negative rate", {"rate": -1}), ("line discount above 100%", {"discount": 101}), ("negative line discount", {"discount": -5})):
        st, resp = api.create("quotation", {"customer": customer, "subject": "x", "items": [dict({"item": ndt, "qty": 1, "rate": 1_000_000}, **line)]})
        check(f"quotation line bounds: {label} -> 422", st == 422, (st, resp))
    st, resp = api.create("quotation", {"customer": customer, "subject": "x", "tax_rate": 0, "items": [{"item": ndt, "qty": 2, "rate": 1_000_000}]})
    eq("quotation without PPN: grand_total = dpp", (resp.get("ppn"), resp.get("grand_total")), (0, 2_000_000))

    # submit (contract 0007: the owner caller self-approves, so the document submits at once and the request is recorded)
    st, resp = api.verb("quotation", q["name"], "submit")
    check("submit quotation", st == 200, resp)
    name = resp.get("name", q["name"])
    reqs = [a for a in approvals_of(api, "quotation", name) if a.get("policy") == "manager_approval"]
    check("quotation manager_approval: submit left a request for sales_manager (owner self-approved)",
          len(reqs) == 1 and reqs[0].get("approvers") == ["sales_manager"] and reqs[0].get("state") == "approved", reqs)
    sub = api.get("quotation", name)
    check("submitted quotation is numbered QUO-<year>-nnnnn", sub["name"].startswith("QUO-"), sub["name"])
    eq("submitted quotation revision 0", sub["revision"], 0)

    # can `outcome` be set on a submitted document? Recorded in the task report.
    st, resp = api.patch("quotation", name, {"outcome": "accepted"})
    if st == 200:
        eq("PATCH sets outcome on a submitted quotation", api.get("quotation", name)["outcome"], "accepted")
        ok("outcome editable after submit (PATCH 200)")
        accepted_by_patch = True
    else:
        check("PATCH on a submitted quotation is refused with 409", st == 409, (st, resp))
        accepted_by_patch = False
        st, resp = api.action("quotation", name, "mark_outcome", {"outcome": "accepted"})
        if st == 501:
            fail("quotation.mark_outcome", "kernel (S8c hooks) is merged on main: 501 hook_not_implemented")
        else:
            check("action mark_outcome sets outcome", st == 200 and api.get("quotation", name)["outcome"] == "accepted", (st, resp))

    # make_order takes {po_number, po_date} from the call body; it is shown only for an accepted quotation
    st, resp = api.action("quotation", name, "make_order", {"po_number": "PO-CJT-0900", "po_date": "2026-11-02"})
    if accepted_by_patch or (st != 409 and st != 501):
        check("orders.from_quotation creates a draft customer_order", st == 200 and resp.get("open", {}).get("model") == "customer_order", (st, resp))
        if st == 200:
            order = api.get("customer_order", resp["open"]["name"])
            check("order from quotation is a draft with quotation, PO and 2 lines",
                  order["docstatus"] == 0 and order["quotation"] == name and order["po_number"] == "PO-CJT-0900" and len(order["items"]) == 2, order)
            st2, resp2 = api.action("quotation", name, "make_order", {"po_number": "PO-CJT-0900", "po_date": "2026-11-02"})
            check("orders.from_quotation reuses the existing draft", st2 == 200 and resp2["open"]["name"] == resp["open"]["name"], (st2, resp2))
            st2, resp2 = api.action("quotation", name, "make_order", {})
            check("orders.from_quotation without PO -> 422 po_required", st2 == 422 and resp2["error"]["code"] == "po_required", (st2, resp2))
    else:
        check("make_order refused while outcome is open", st == 409 and resp["error"]["code"] == "action_not_allowed", (st, resp))
        fail("orders.from_quotation creates a draft customer_order", "kernel (S8c hooks) is merged on main: mark_outcome answered 501, so no quotation reached accepted")

    # a generic amend zeroes read-only fields, so the amended draft has outcome '' which counts as open (not stuck)
    def amended_submitted(subject):
        st, qx = api.create("quotation", {"customer": customer, "subject": subject, "items": [{"item": ndt, "qty": 1, "rate": 1_500_000}]})
        st, resp = api.verb("quotation", qx["name"], "submit")
        nx = resp.get("name", qx["name"])
        st, resp = api.verb("quotation", nx, "cancel")
        check("cancel quotation before generic amend", st == 200, resp)
        st, am = api.verb("quotation", nx, "amend")
        check("generic amend creates a draft", st in (200, 201), (st, am))
        if st not in (200, 201):
            return None
        am_name = am.get("name") or am.get("data", {}).get("name")
        st, resp = api.verb("quotation", am_name, "submit")
        check("amended draft (outcome empty) submits", st == 200, (st, resp))
        return resp.get("name", am_name)

    n4 = amended_submitted("Untuk amend A")
    if n4:
        st, resp = api.action("quotation", n4, "new_revision")
        check("empty outcome is open: new_revision passes the gate", st != 409, (st, resp))
    n5 = amended_submitted("Untuk amend B")
    if n5:
        st, resp = api.action("quotation", n5, "mark_outcome", {"outcome": "lost", "lost_reason": "uji"})
        check("empty outcome is open: mark_outcome passes the gate", st != 409, (st, resp))

    # revision flow on a separate submitted quotation whose outcome is still open: Rev.00 -> Rev.01
    st, q2 = api.create("quotation", {"customer": customer, "subject": "Untuk revisi", "items": [{"item": ndt, "qty": 3, "rate": 1_500_000}, {"item": ut, "qty": 5, "rate": 85_000}]})
    check("create quotation to revise", st == 201, q2)
    if st != 201:
        return
    st, resp = api.verb("quotation", q2["name"], "submit")
    check("submit quotation to revise", st == 200, resp)
    old = resp.get("name", q2["name"])
    st, resp = api.action("quotation", old, "new_revision")
    if st == 501:
        fail("quotation.revise creates a draft with revision + 1", "kernel (S8c hooks) is merged on main: 501 hook_not_implemented")
        return
    check("quotation.revise answers 200 with the new draft", st == 200 and resp.get("open", {}).get("model") == "quotation", (st, resp))
    if st != 200:
        return
    new = api.get("quotation", resp["open"]["name"])
    eq("revision draft has docstatus 0", new["docstatus"], 0)
    eq("revision draft has revision 1", new["revision"], 1)
    eq("revision draft has outcome open", new["outcome"], "open")
    check("revision draft keeps the same lines", [(r["item"], r["qty"], r["rate"]) for r in new["items"]] == [(r["item"], r["qty"], r["rate"]) for r in q2["items"]], new["items"])
    eq("old quotation is cancelled (docstatus 2)", api.get("quotation", old)["docstatus"], 2)
    # the gate also admits the cancelled original (a held cancel is approved later): the second call reuses the draft,
    # and once the amendment is submitted the original is refused with already_revised
    draft = resp["open"]["name"]
    st, resp = api.action("quotation", old, "new_revision")
    check("revise on the cancelled original reuses the open draft", st == 200 and resp.get("open", {}).get("name") == draft, (st, resp))
    st, resp = api.verb("quotation", draft, "submit")
    check("submit the revision draft", st == 200, (st, resp))
    st, resp = api.action("quotation", old, "new_revision")
    check("revise on the cancelled original after its amendment is submitted -> 409 already_revised",
          st == 409 and resp["error"]["code"] == "already_revised", (st, resp))


def scenario_line_discount_chain(api):
    """Review item 1: a quotation discounts per line only; the order and the invoice made from it carry the same DPP and total."""
    customer = api.list("customer")[0]["name"]
    ndt = services(api)[0]["name"]
    st, q = api.create("quotation", {"customer": customer, "subject": "Diskon baris 10%", "items": [{"item": ndt, "qty": 10, "rate": 1_000_000, "discount": 10}]})
    check("quotation with a 10% line discount: dpp 9.000.000, total 9.990.000", st == 201 and (q["dpp"], q["grand_total"]) == (9_000_000, 9_990_000), (st, q))
    if st != 201:
        return
    st, resp = api.verb("quotation", q["name"], "submit")
    qn = resp.get("name", q["name"])
    st, resp = api.action("quotation", qn, "mark_outcome", {"outcome": "accepted"})
    check("discount chain: quotation accepted", st == 200, (st, resp))
    st, resp = api.action("quotation", qn, "make_order", {"po_number": "PO-DISKON-01", "po_date": "2026-11-03"})
    check("discount chain: make_order opens a draft order", st == 200 and resp.get("open", {}).get("model") == "customer_order", (st, resp))
    if st != 200:
        return
    order = api.get("customer_order", resp["open"]["name"])
    eq("discount chain: order subtotal = quotation dpp (net line rate 900.000)", (order["subtotal"], order["items"][0]["rate"]), (9_000_000, 900_000))
    st, resp = api.verb("customer_order", order["name"], "submit")
    on = resp.get("name", order["name"])
    st, resp = api.action("customer_order", on, "make_invoice")
    check("discount chain: make_invoice opens a draft invoice", st == 200 and resp.get("open", {}).get("model") == "sales_invoice", (st, resp))
    if st != 200:
        return
    inv = api.get("sales_invoice", resp["open"]["name"])
    eq("discount chain: invoice dpp and grand_total equal the quotation's", (inv["dpp"], inv["grand_total"]), (q["dpp"], q["grand_total"]))


def scenario_cross_links(api):
    """Review item 5: an invoice points only at documents of its own customer; a vendor bill and a goods receipt only at their vendor's PO."""
    ndt = services(api)[0]["name"]
    a, b = "CUST-0001", "CUST-0002"

    def refused(label, model, body, word):
        st, resp = api.create(model, body)
        check(f"{label} -> 422", st == 422 and any(word in m for m in messages(resp)), (st, resp))

    def submitted(body):
        st, d = api.create("sales_invoice", body)
        st, resp = api.verb("sales_invoice", d["name"], "submit", {"force": True})
        return resp.get("name", d["name"])
    dp_b = submitted(invoice_body(b, ndt, rate=3_000_000, kind="down_payment"))
    refused("invoice of CUST-0001 deducting the DP of CUST-0002", "sales_invoice", invoice_body(a, ndt, dp_invoice=dp_b, down_payment=3_000_000), "Faktur uang muka harus milik")
    ret_b = submitted(invoice_body(b, ndt, retention_percent=5))
    refused("retention invoice of CUST-0001 for an invoice of CUST-0002", "sales_invoice", invoice_body(a, ndt, rate=500_000, kind="retention", retention_of=ret_b), "Faktur asal retensi harus milik")
    st, so = api.create("customer_order", {"customer": b, "po_number": "PO-LAIN", "po_date": "2026-11-03", "items": [{"item": ndt}]})
    refused("invoice of CUST-0001 on an order of CUST-0002", "sales_invoice", invoice_body(a, ndt, customer_order=so["name"]), "Pesanan pelanggan harus milik")
    st, pj = api.create("project", {"project_name": "Proyek pelanggan lain", "customer": b, "site": "Serang"})
    refused("invoice of CUST-0001 on a project of CUST-0002", "sales_invoice", invoice_body(a, ndt, project=pj["name"]), "Proyek harus milik")
    st, ac = api.create("acceptance", {"project": pj["name"], "accepted_on": "2026-11-03", "accepted_by": "Pak Contoh", "scope": "Inspeksi tahap 1", "amount": 1_000_000})
    check("setup: an acceptance on the project of CUST-0002", st == 201, (st, ac))
    if st == 201:
        refused("invoice of CUST-0001 on an acceptance of a CUST-0002 project", "sales_invoice", invoice_body(a, ndt, acceptance=ac["name"]), "Berita acara harus")
        st, ok_inv = api.create("sales_invoice", invoice_body(b, ndt, project=pj["name"], acceptance=ac["name"], customer_order=so["name"]))
        check("invoice of CUST-0002 on its own order, project and acceptance accepted", st == 201, (st, ok_inv))
    # purchase side
    st, po = api.create("purchase_order", {"vendor": "VND-0001", "outlet": "OUT-01", "items": [{"item": "BRG-0004", "qty": 1, "rate": 100_000}]})
    refused("vendor_bill of VND-0002 against a PO of VND-0001", "vendor_bill",
            {"vendor": "VND-0002", "purchase_order": po["name"], "bill_no": "X-1", "bill_date": "2026-11-03", "tax_rate": 0, "items": [{"item": "BRG-0004", "qty": 1, "rate": 100_000}]}, "pemasok yang sama")
    refused("goods_receipt of VND-0002 against a PO of VND-0001", "goods_receipt",
            {"vendor": "VND-0002", "purchase_order": po["name"], "outlet": "OUT-01", "items": [{"item": "BRG-0004", "qty": 1, "rate": 100_000}]}, "pemasok yang sama")
    st, out2 = api.create("outlet", {"outlet_name": "Gudang uji lain", "code": "UJ2"})
    refused("goods_receipt at another outlet than the PO's", "goods_receipt",
            {"vendor": "VND-0001", "purchase_order": po["name"], "outlet": out2.get("name"), "items": [{"item": "BRG-0004", "qty": 1, "rate": 100_000}]}, "outlet tujuan")
    st, gr = api.create("goods_receipt", {"vendor": "VND-0001", "outlet": "OUT-01", "items": [{"item": "BRG-0004", "qty": 1, "rate": 100_000}]})
    refused("vendor_bill of VND-0002 on a goods receipt of VND-0001", "vendor_bill",
            {"vendor": "VND-0002", "goods_receipt": gr["name"], "bill_no": "X-2", "bill_date": "2026-11-03", "tax_rate": 0, "items": [{"item": "BRG-0004", "qty": 1, "rate": 100_000}]}, "pemasok yang sama")


def scenario_fx_jasa(api):
    """Review item 6: a foreign document needs its rate; adjustments are entered in rupiah and the foreign twins follow; prints show both."""
    ndt = services(api)[0]["name"]
    c = "CUST-0001"
    usd = {"currency": "USD"}
    bodies = {"quotation": dict(usd, customer=c, subject="Valas", items=[{"item": ndt, "qty": 1, "rate_fx": 1000}]),
              "sales_invoice": dict(usd, customer=c, items=[{"item": ndt, "qty": 1, "rate_fx": 1000}]),
              "purchase_order": dict(usd, vendor="VND-0001", items=[{"item": "BRG-0004", "qty": 1, "rate": 0, "rate_fx": 1000}]),
              "vendor_bill": dict(usd, vendor="VND-0001", bill_no="FX-1", bill_date="2026-11-03", items=[{"item": "BRG-0004", "qty": 1, "rate": 0, "rate_fx": 1000}]),
              "payment": dict(usd, direction="receive", customer=c, amount_fx=1000)}
    for model, body in bodies.items():
        st, resp = api.create(model, body)
        check(f"USD {model} with exchange_rate left at the default 1 -> 422 (kurs wajib)", st == 422 and any("kursnya" in m for m in messages(resp)), (st, resp))
    st, inv = api.create("sales_invoice", dict(bodies["sales_invoice"], exchange_rate=16000, discount_amount=1_600_000, discount_fx=100))
    check("USD invoice at 16.000 with a rupiah discount of 1.600.000", st == 201, (st, inv))
    if st == 201:
        eq("USD invoice: discount entered in rupiah gives dpp 14.400.000 and ppn 1.584.000", (inv["dpp"], inv["ppn"]), (14_400_000, 1_584_000))
        check("USD invoice: dpp_fx follows the rupiah discount (1.000 - 100 = 900); there is no discount_fx field",
              float(inv["dpp_fx"]) == 900 and "discount_fx" not in inv and "down_payment_fx" not in inv, inv)
        st, resp = api.verb("sales_invoice", inv["name"], "submit", {"force": True})
        n = resp.get("name", inv["name"])
        check_print("USD invoice print shows USD lines, USD totals, the kurs and the rupiah equivalent", api, "sales_invoice", n, "",
                    ["USD 1.000,00", "USD 900,00", "Kurs: 1 USD = Rp 16.000", "Rp 14.400.000", "Rp 15.984.000"], ["Rp 1.500.000"])
    st, vb = api.create("vendor_bill", dict(bodies["vendor_bill"], exchange_rate=16000, pph23_amount=160_000))
    check("USD vendor_bill with PPh 23 of 160.000 entered in rupiah: amount_due_fx = grand_total_fx - 10", st == 201 and "pph23_amount_fx" not in vb
          and abs(float(vb["amount_due_fx"]) - (float(vb["grand_total_fx"]) - 10)) < 0.005, (st, vb))
    st, q = api.create("quotation", dict(bodies["quotation"], exchange_rate=16000))
    st, resp = api.verb("quotation", q["name"], "submit")
    qn = resp.get("name", q["name"])
    check_print("USD quotation print shows USD lines and the kurs", api, "quotation", qn, "", ["USD 1.000,00", "Kurs: 1 USD = Rp 16.000", "Rp 16.000.000"])
    st, resp = api.create("customer_order", {"customer": c, "quotation": qn, "po_number": "PO-FX", "po_date": "2026-11-03", "items": [{"item": ndt}]})
    check("order from a USD quotation -> 422 (conversions drop the currency: kernel gap S8c)", st == 422 and any("valas" in m for m in messages(resp)), (st, resp))
    st, po = api.create("purchase_order", dict(bodies["purchase_order"], exchange_rate=16000))
    st, resp = api.verb("purchase_order", po["name"], "submit")
    check_print("USD purchase order print shows USD lines and the kurs", api, "purchase_order", resp.get("name", po["name"]), "", ["USD 1.000,00", "Kurs: 1 USD = Rp 16.000"])


def scenario_print_descriptions(api):
    """Review item 7: line descriptions default to the item name, so prints show names, not item codes."""
    site = CTX["kernel"].site
    if site == "demo-jasa":
        item, name = "BRG-0001", "Inspeksi NDT (per orang-hari)"
        st, q = api.create("quotation", {"customer": "CUST-0001", "subject": "Uraian", "items": [{"item": item, "qty": 1, "rate": 1_000_000}]})
        eq("quotation line description defaults to the item name", q["items"][0]["description"], name)
        st, inv = api.create("sales_invoice", invoice_body("CUST-0001", item))
        eq("sales_invoice line description defaults to the item name", inv["items"][0]["description"], name)
        st, resp = api.verb("sales_invoice", inv["name"], "submit", {"force": True})
        check_print("invoice print shows the item name, not its code", api, "sales_invoice", resp.get("name", inv["name"]), "", [name], ["BRG-0001"])
        check_print("quotation print shows the item name, not its code", api, "quotation", q["name"], "", [name], ["BRG-0001"])
        vendor, pitem = "VND-0001", "BRG-0004"
    else:
        vendor, pitem = "VND-0002", "BRG-0001"
    pname = api.get("item", pitem)["item_name"]
    st, po = api.create("purchase_order", dict({"vendor": vendor, "items": [{"item": pitem, "qty": 1, "rate": 1_000}]}, **({"outlet": DAGO} if site == "demo-fnb" else {})))
    eq("purchase_order line description defaults to the item name", po["items"][0]["description"], pname)
    check_print("purchase order print shows the item name, not its code", api, "purchase_order", po["name"], "", [pname], [pitem])


def scenario_orders(api):
    customer, other = api.list("customer")[0]["name"], api.list("customer")[1]["name"]
    ndt = services(api)[0]["name"]
    st, q = api.create("quotation", {"customer": customer, "subject": "Untuk pesanan", "items": [{"item": ndt, "qty": 4, "rate": 1_500_000}]})
    st, o = api.create("customer_order", {"customer": customer, "quotation": q["name"], "po_number": "PO-CJT-0001", "po_date": "2026-11-02",
                                          "site": "Cilegon", "items": [{"item": ndt, "qty": 4, "rate": 1_500_000}, {"item": ndt, "qty": 1, "rate": 500_000}]})
    check("create customer_order from a quotation", st == 201, o)
    if st != 201:
        return
    eq("order subtotal 6.500.000", o["subtotal"], 6_500_000)
    eq("order line amount round(qty*rate)", o["items"][0]["amount"], 6_000_000)
    eq("order invoiced_amount 0", o["invoiced_amount"], 0)
    st, resp = api.create("customer_order", {"customer": other, "quotation": q["name"], "po_number": "PO-X", "po_date": "2026-11-02", "items": [{"item": ndt}]})
    check("order quotation of another customer rejected", st == 422 and any("Penawaran" in m for m in messages(resp)), (st, resp))
    st, resp = api.create("customer_order", {"customer": customer, "po_date": "2026-11-02", "items": [{"item": ndt}]})
    check("order without po_number rejected", st == 422, (st, resp))
    st, resp = api.create("customer_order", {"customer": customer, "po_number": "PO-Y", "po_date": "2026-11-02", "start_date": "2026-12-01", "end_date": "2026-11-01", "items": [{"item": ndt}]})
    check("order end before start rejected", st == 422, (st, resp))

    st, resp = api.verb("customer_order", o["name"], "submit")
    check("submit customer_order", st == 200, resp)
    name = resp.get("name", o["name"])

    st, resp = api.action("customer_order", name, "make_invoice")
    if st == 501:
        fail("invoicing.from_order creates a draft sales_invoice", "kernel (S8c hooks) is merged on main: 501 hook_not_implemented")
    else:
        check("invoicing.from_order creates a draft invoice", st == 200, (st, resp))

    # the invoice links moved out of the sidecar: customer_order, po_number default
    st, inv = api.create("sales_invoice", {"customer": customer, "customer_order": name, "items": [{"item": ndt, "qty": 1, "rate": 1_000_000}]})
    check("sales_invoice accepts customer_order", st == 201, inv)
    if st == 201:
        eq("sales_invoice po_number defaults from customer_order", inv.get("po_number"), "PO-CJT-0001")


def scenario_workforce(api):
    st, w = api.create("worker", {"worker_name": "Citra Uji", "day_rate": 1_500_000, "role_title": "Inspektor NDT Level II",
                                  "certifications": [{"cert_name": "Kedaluwarsa kemarin", "expires_on": yesterday()},
                                                     {"cert_name": "Masih berlaku", "expires_on": "2099-01-01"},
                                                     {"cert_name": "Tanpa kedaluwarsa"}]})
    check("create worker with certifications", st == 201, w)
    if st != 201:
        return
    certs = w["certifications"]
    eq("certification expired yesterday -> expired true", certs[0].get("expired") in (True, "true"), True)
    eq("certification valid in 2099 -> expired false", certs[1].get("expired") in (True, "true"), False)
    eq("certification without date -> expired false", certs[2].get("expired") in (True, "true"), False)
    fixtures = {x["worker_name"]: x for x in api.list("worker")}
    check("fixture has a worker with an expired certification", any(c.get("expired") in (True, "true") for c in api.get("worker", fixtures["Budi Contoh"]["name"])["certifications"]), fixtures.get("Budi Contoh"))
    # let a valid certificate's date pass without a save: the stored `expired` stays false, a read must recompute it
    out = sql(f"update r_certification set expires_on = current_date - 1 where parent = '{w['name']}' and cert_name = 'Masih berlaku'")
    if not check("certification date moved into the past behind the kernel's back", out == "UPDATE 1", out):
        return
    certs = api.get("worker", w["name"])["certifications"]
    eq("certification.expired stays current after save (computed on read)", certs[1].get("expired") in (True, "true"), True)


def sql(stmt):
    """Runs one statement on the kernel's database with psql ($PGBIN/psql, else psql on PATH); returns the command tag or the error."""
    psql = os.path.join(os.environ.get("PGBIN", ""), "psql")
    if not os.access(psql, os.X_OK):
        psql = shutil.which("psql") or "psql"
    r = subprocess.run([psql, CTX["kernel"].db, "-X", "-v", "ON_ERROR_STOP=1", "-c", stmt], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else r.stderr.strip()


def yesterday():
    import datetime
    return (datetime.date.today() - datetime.timedelta(days=1)).isoformat()


def scenario_projects(api):
    customer = api.list("customer")[0]["name"]
    worker = next(w for w in api.list("worker") if w["worker_name"] == "Andi Contoh")["name"]
    st, p = api.create("project", {"project_name": "Inspeksi shutdown Cilegon", "customer": customer, "site": "Cilegon"})
    check("create project", st == 201, p)
    if st != 201:
        return
    eq("project state defaults to planned", p["state"], "planned")
    eq("project number PRJ-<year>-nnnn", p["name"].startswith("PRJ-"), True)
    eq("project billing_model defaults to fixed, contract_value 0", (p["billing_model"], p["contract_value"]), ("fixed", 0))
    st, eng = api.create("project", {"project_name": "Pendampingan pajak 2026", "customer": customer, "billing_model": "milestone",
                                     "contract_kind": "spk", "contract_no": "SPK/001/XI/2026", "contract_date": "2026-11-02", "contract_value": 100_000_000})
    check("create an engagement: milestone billing, SPK, contract 100.000.000", st == 201 and eng["contract_value"] == 100_000_000 and eng["billing_model"] == "milestone", (st, eng))
    st, resp = api.create("project", {"project_name": "x", "customer": customer, "contract_value": -1})
    check("negative contract value -> 422 'Nilai kontrak tidak boleh negatif'", st == 422 and any("Nilai kontrak" in m for m in messages(resp)), (st, resp))

    st, a = api.create("assignment", {"project": p["name"], "worker": worker, "start_date": "2026-11-10", "end_date": "2026-11-20"})
    check("create assignment", st == 201, a)
    if st == 201:
        eq("assignment day_rate defaults from the worker", a["day_rate"], 1_500_000)
        eq("assignment site defaults from the project", a["site"], "Cilegon")
    st, resp = api.create("assignment", {"project": p["name"], "worker": worker, "start_date": "2026-11-20", "end_date": "2026-11-10"})
    check("assignment end before start -> 422", st == 422 and any("selesai" in m.lower() for m in messages(resp)), (st, resp))

    st, t = api.create("timesheet", {"project": p["name"], "worker": worker, "assignment": a["name"], "work_date": "2026-11-10", "days": 1, "description": "Inspeksi UT pipa"})
    check("create timesheet", st == 201, t)
    if st == 201:
        eq("timesheet day_rate defaults from the assignment", t["day_rate"], 1_500_000)
        eq("timesheet 1 day x 1.500.000 = 1.500.000", t["amount"], 1_500_000)
        st, resp = api.verb("timesheet", t["name"], "submit")
        check("submit timesheet", st == 200, resp)
    st, resp = api.create("timesheet", {"project": p["name"], "worker": worker, "work_date": "2026-11-10", "days": 2, "description": "x"})
    check("timesheet 2 days rejected", st == 422, (st, resp))
    st, resp = api.create("timesheet", {"project": p["name"], "worker": worker, "work_date": "2026-11-10", "days": 0, "description": "x"})
    check("timesheet 0 days rejected", st == 422, (st, resp))
    st, half = api.create("timesheet", {"project": p["name"], "worker": worker, "work_date": "2026-11-11", "days": 1.5, "day_rate": 1_000_001, "description": "lembur"})
    eq("timesheet 1,5 days rounds to whole rupiah", half.get("amount"), 1_500_002)

    # hourly time entry (consultants) and the internal cost behind margin
    st, cw = api.create("worker", {"worker_name": "Konsultan Contoh", "day_rate": 3_000_000, "hour_rate": 400_000, "cost_day_rate": 1_000_000, "cost_hour_rate": 150_000})
    check("create a consultant: 400.000/hour, costs 150.000/hour", st == 201, (st, cw))
    st, ha = api.create("assignment", {"project": p["name"], "worker": cw["name"], "start_date": "2026-11-10", "end_date": "2026-11-30", "rate_basis": "hour"})
    if check("assignment billed per hour", st == 201, (st, ha)):
        eq("hour_rate and cost_rate default from the worker (cost per hour on an hourly assignment)", (ha["hour_rate"], ha["cost_rate"]), (400_000, 150_000))
        st, ht = api.create("timesheet", {"project": p["name"], "worker": cw["name"], "assignment": ha["name"], "work_date": "2026-11-12", "hours": 20, "description": "Review SPT"})
        if check("hourly timesheet 20 hours created", st == 201, (st, ht)):
            eq("20 h x 400.000 = 8.000.000 billed, x 150.000 = 3.000.000 cost, rate_basis from the assignment",
               (ht["rate_basis"], ht["amount"], ht["cost_amount"], ht["billable_amount"]), ("hour", 8_000_000, 3_000_000, 8_000_000))
        st, resp = api.create("timesheet", {"project": p["name"], "worker": cw["name"], "assignment": ha["name"], "work_date": "2026-11-13", "hours": 25, "description": "x"})
        check("25 hours in a day -> 422 'Jam kerja'", st == 422 and any("Jam kerja" in m for m in messages(resp)), (st, resp))
        st, nb = api.create("timesheet", {"project": p["name"], "worker": cw["name"], "assignment": ha["name"], "work_date": "2026-11-14", "hours": 2, "billable": False, "description": "Internal"})
        eq("a non-billable hour still costs: billable_amount 0, cost 300.000", (nb.get("billable_amount"), nb.get("cost_amount")), (0, 300_000))
    st, na = api.create("timesheet", {"project": p["name"], "worker": cw["name"], "rate_basis": "hour", "work_date": "2026-11-15", "hours": 3, "description": "Tanpa penugasan"})
    eq("an hourly timesheet without assignment takes the worker's rates: 3 h = 1.200.000, cost 450.000", (na.get("amount"), na.get("cost_amount")), (1_200_000, 450_000))
    st, resp = api.create("timesheet", {"project": p["name"], "worker": worker, "rate_basis": "hour", "work_date": "2026-11-15", "hours": 3, "description": "x"})
    check("a billable hourly timesheet with no hour rate anywhere -> 422 'butuh tarif per jam'", st == 422 and any("tarif per jam" in m for m in messages(resp)), (st, resp))
    eq("a daily timesheet keeps rate_basis day and no cost without a cost rate", (half.get("rate_basis"), half.get("cost_amount")), ("day", 0))

    st, b = api.create("acceptance", {"project": p["name"], "accepted_on": "2026-11-21", "accepted_by": "Bambang Contoh", "scope": "Inspeksi tahap 1", "amount": 15_000_000})
    check("create acceptance", st == 201, b)
    if st == 201:
        st, resp = api.verb("acceptance", b["name"], "submit")
        check("submit acceptance", st == 200 and str(resp.get("name", "")).startswith("BA-"), resp)
        st, inv = api.create("sales_invoice", {"customer": customer, "project": p["name"], "acceptance": resp.get("name", b["name"]),
                                               "items": [{"item": services(api)[0]["name"], "qty": 10, "rate": 1_500_000}]})
        check("sales_invoice accepts project and acceptance", st == 201, inv)
    st, resp = api.create("acceptance", {"project": p["name"], "accepted_on": "2026-11-21", "scope": "x"})
    check("acceptance without accepted_by rejected", st == 422, (st, resp))


def scenario_payments(api):
    item = services(api)[0]["name"]
    st, cust = api.create("customer", {"customer_name": "PT Contoh Bayar"})
    check("create customer for payments", st == 201, cust)
    customer = cust["name"]

    def submitted_invoice(rate, **extra):
        st, inv = api.create("sales_invoice", invoice_body(customer, item, rate=rate, **extra))
        check(f"create invoice {rate}", st == 201, inv)
        st, resp = api.verb("sales_invoice", inv["name"], "submit", {"force": True})
        check(f"submit invoice {rate}", st == 200, resp)
        return resp.get("name", inv["name"])

    inv1 = submitted_invoice(10_000_000)
    inv2 = submitted_invoice(3_900_000, tax_rate=0)
    eq("invoice 1 outstanding 11.100.000", api.get("sales_invoice", inv1)["outstanding"], 11_100_000)
    eq("invoice 2 outstanding 3.900.000", api.get("sales_invoice", inv2)["outstanding"], 3_900_000)
    cust_before = api.get("customer", customer)["outstanding"]
    hooked = cust_before == 15_000_000   # ledger.post_receivable is implemented

    def pay(amount, rows, **extra):
        body = {"direction": "receive", "customer": customer, "amount": amount,
                "allocations": [{"sales_invoice": i, "amount": a, **({"withheld": w} if w else {})} for i, a, w in rows]}
        body.update(extra)
        return api.create("payment", body)

    # rules
    st, resp = pay(15_000_000, [(inv1, 12_000_000, 0)])
    check("allocation 12.000.000 vs outstanding 11.100.000 -> 422 'Alokasi melebihi sisa tagihan'",
          st == 422 and any("Alokasi melebihi sisa tagihan" in m for m in messages(resp)), (st, resp))
    st, resp = pay(10_000_000, [(inv1, 6_000_000, 0), (inv2, 3_900_000, 0)])
    check("allocated_total 9.900.000 <= amount ok", st == 201, (st, resp))
    st, resp = pay(9_000_000, [(inv1, 6_000_000, 0), (inv2, 3_900_000, 0)])
    check("allocated_total above amount -> 422 'Alokasi melebihi jumlah pembayaran'",
          st == 422 and any("Alokasi melebihi jumlah pembayaran" in m for m in messages(resp)), (st, resp))
    st, resp = api.create("payment", {"direction": "receive", "amount": 1000})
    check("receive without customer -> 422", st == 422, (st, resp))
    st, resp = api.create("payment", {"direction": "pay", "amount": 1000})
    check("pay without vendor -> 422", st == 422, (st, resp))
    st, resp = api.create("payment", {"direction": "receive", "customer": customer, "amount": 0})
    check("amount 0 -> 422", st == 422, (st, resp))
    st, resp = api.create("payment", {"direction": "receive", "customer": customer, "amount": 1000, "allocations": [{"amount": 1000}]})
    check("allocation row without invoice -> 422", st == 422, (st, resp))
    st, resp = pay(20_000_000, [(inv1, -1, 0)])
    check("negative allocation amount -> 422", st == 422 and any("lebih dari 0" in m or "negatif" in m for m in messages(resp)), (st, resp))
    st, resp = pay(20_000_000, [(inv1, 0, 0)])
    check("allocation row amount 0 -> 422 'Jumlah alokasi harus lebih dari 0'", st == 422 and any("Jumlah alokasi harus lebih dari 0" in m for m in messages(resp)), (st, resp))
    st, resp = pay(20_000_000, [(inv1, 1_000_000, -5)])
    check("negative withheld -> 422 'tidak boleh negatif'", st == 422 and any("tidak boleh negatif" in m for m in messages(resp)), (st, resp))
    st, resp = pay(20_000_000, [(inv1, 11_000_000, 200_000)])
    check("amount alone fits (11.000.000) but amount + withheld 11.200.000 > 11.100.000 -> 422 'Alokasi melebihi sisa tagihan'",
          st == 422 and any("Alokasi melebihi sisa tagihan" in m for m in messages(resp)), (st, resp))

    # one payment of 15.000.000 across both invoices: submit passes the rules
    st, p = pay(15_000_000, [(inv1, 11_100_000, 0), (inv2, 3_900_000, 0)])
    check("create payment 15.000.000 allocated 11.100.000 + 3.900.000", st == 201, (st, p))
    if st != 201:
        return
    eq("payment allocated_total 15.000.000", p["allocated_total"], 15_000_000)
    eq("payment unallocated 0", p["unallocated"], 0)
    check("allocation row snapshots outstanding", p["allocations"][0]["outstanding"] == 11_100_000, p["allocations"])
    # a second draft against the same invoice, created before either is submitted: each passes its rules alone
    st_pb, pb = pay(1_000_000, [(inv1, 1_000_000, 0)])
    check("draft second payment against the same invoice passes its rules", st_pb == 201, (st_pb, pb))
    st, resp = api.verb("payment", p["name"], "submit")
    check("submit payment passes the rules", st == 200, (st, resp))
    pname = resp.get("name", p["name"])
    if skipped(api, "payments.allocate", f"payment/{pname}"):
        fail("payments.allocate pays both invoices", "kernel (S8c hooks) is merged on main: hook.skipped in the audit log")
        fail("payments.unallocate restores on cancel", "kernel (S8c hooks) is merged on main: hook.skipped in the audit log")
        fail("payments.allocate refuses the second payment (409 over_allocation)", "kernel (S8c hooks) is merged on main: hook.skipped in the audit log")
        api.verb("payment", pname, "cancel")
    else:
        i1, i2 = api.get("sales_invoice", inv1), api.get("sales_invoice", inv2)
        eq("invoice 1 paid_amount 11.100.000", i1["paid_amount"], 11_100_000)
        eq("invoice 1 outstanding 0", i1["outstanding"], 0)
        eq("invoice 2 outstanding 0", i2["outstanding"], 0)
        check("both invoices status paid", i1.get("status") == "paid" and i2.get("status") == "paid", (i1.get("status"), i2.get("status")))
        rows = api.get("payment", pname)["allocations"]
        eq("applied_idr written on the rows", [r["applied_idr"] for r in rows], [11_100_000, 3_900_000])
        if hooked:
            eq("customer outstanding dropped by 15.000.000", api.get("customer", customer)["outstanding"], cust_before - 15_000_000)
        if st_pb == 201:
            st, resp = api.verb("payment", pb["name"], "submit")
            check("second payment on the same invoice: 409 over_allocation 'Alokasi melebihi sisa tagihan'",
                  st == 409 and resp["error"]["code"] == "over_allocation" and "Alokasi melebihi sisa tagihan" in resp["error"]["message"], (st, resp))
        st, resp = api.verb("sales_invoice", inv1, "cancel")
        check("invoice with payments cannot be cancelled (has_payments)", st == 409, (st, resp))
        st, resp = api.verb("payment", pname, "cancel")
        check("cancel payment", st == 200, resp)
        i1 = api.get("sales_invoice", inv1)
        eq("cancel restores invoice 1 paid_amount 0", i1["paid_amount"], 0)
        eq("cancel restores invoice 1 outstanding", i1["outstanding"], 11_100_000)
        eq("cancel restores invoice 2 outstanding", api.get("sales_invoice", inv2)["outstanding"], 3_900_000)
        if hooked:
            eq("cancel restores customer outstanding", api.get("customer", customer)["outstanding"], cust_before)

    # PPh 23 invoice: amount_due already nets the tax, so cash 10.900.000 clears it and withheld must stay 0
    inv3 = submitted_invoice(10_000_000, pph23_applies=True)
    i3 = api.get("sales_invoice", inv3)
    eq("pph23 invoice amount_due 10.900.000", i3["amount_due"], 10_900_000)
    st, resp = pay(10_900_000, [(inv3, 10_700_000, 200_000)])
    check("withheld on a pph23 invoice -> 422 'PPh 23 sudah dipotong di faktur ini'",
          st == 422 and any("PPh 23 sudah dipotong" in m for m in messages(resp)), (st, resp))
    st, resp = api.create("payment", {"direction": "receive", "customer": customer, "amount": 10_900_000,
                                      "allocations": [{"sales_invoice": inv3, "amount": 10_700_000, "withheld": 200_000, "pph23_applies": False}]})
    if st == 422:
        check("snapshot honoured: client pph23_applies=false does not bypass -> 422 'PPh 23 sudah dipotong'",
              any("PPh 23 sudah dipotong" in m for m in messages(resp)), (st, resp))
    elif st == 201:
        row = resp["allocations"][0]
        if "pph23_applies" not in row:
            fail("row serves pph23_applies", row)
        elif row["pph23_applies"] in (True, "true"):
            fail("rule no_double_pph23 did not fire", f"201 with stored pph23_applies true and withheld {row.get('withheld')}")
        else:
            fail("client-supplied pph23_applies=false on a row is ignored (read_only on child rows)",
                 "kernel stored the client value, so the row rule passed; checking that payments.allocate still refuses")
            st2, r2 = api.verb("payment", resp["name"], "submit")
            bypass = r2.get("name", resp["name"]) if st2 == 200 else resp["name"]
            if st2 == 200 and skipped(api, "payments.allocate", f"payment/{bypass}"):
                fail("payments.allocate refuses withheld on a pph23 invoice (409 pph23_already_deducted)", "kernel (S8c hooks) is merged on main: hook.skipped in the audit log")
            else:
                check("hook refuses the bypassed row: 409 pph23_already_deducted", st2 == 409 and r2["error"]["code"] == "pph23_already_deducted", (st2, r2))
            if st2 == 200:
                api.verb("payment", bypass, "cancel")
    else:
        fail("client pph23_applies=false row", (st, resp))
    st, p2 = pay(10_900_000, [(inv3, 10_900_000, 0)])
    check("payment of 10.900.000 cash on the pph23 invoice", st == 201, (st, p2))
    if st == 201:
        st, resp = api.verb("payment", p2["name"], "submit")
        check("submit payment on the pph23 invoice", st == 200, resp)
        p2 = {"name": resp.get("name", p2["name"])}
        if st == 200 and not skipped(api, "payments.allocate", f"payment/{resp.get('name', p2['name'])}"):
            eq("pph23 invoice cleared by cash only: outstanding 0", api.get("sales_invoice", inv3)["outstanding"], 0)
    st, bp = api.create("withholding", {"customer": customer, "sales_invoice": inv3, "cert_no": "BP-UJI-001", "cert_date": "2026-11-05",
                                        "payment": p2["name"] if isinstance(p2, dict) and p2.get("name") else ""})
    check("create withholding for the invoice", st == 201, bp)
    if st == 201:
        eq("withholding dpp defaults to the invoice's PPh 23 base (= dpp when every line is ticked)", bp["dpp"], 10_000_000)
        eq("withholding amount = round(dpp x 2 / 100) = 200.000", bp["amount"], 200_000)
        st, resp = api.verb("withholding", bp["name"], "submit")
        check("submit withholding", st == 200 and str(resp.get("name", "")).startswith("BP-"), resp)

    # non-PPh-23 invoice where the customer withheld anyway: cash + withheld clears it
    inv4 = submitted_invoice(10_000_000)
    st, p3 = pay(10_900_000, [(inv4, 10_900_000, 200_000)])
    check("cash 10.900.000 + withheld 200.000 on a non-pph23 invoice of 11.100.000", st == 201, (st, p3))
    if st == 201:
        eq("withheld_total 200.000", p3["withheld_total"], 200_000)
        st, resp = api.verb("payment", p3["name"], "submit")
        check("submit payment with withheld", st == 200, resp)
        if st == 200 and not skipped(api, "payments.allocate", f"payment/{resp.get('name', p3['name'])}"):
            eq("cash + withheld clears the invoice: outstanding 0", api.get("sales_invoice", inv4)["outstanding"], 0)
        elif st == 200:
            fail("cash + withheld clears a non-pph23 invoice", "kernel (S8c hooks) is merged on main: hook.skipped in the audit log")

    # USD payment: conversion happens once at the header (amount = round(amount_fx x exchange_rate)); rows are IDR
    inv5 = submitted_invoice(10_000_000)
    st, usd = api.create("payment", {"direction": "receive", "customer": customer, "currency": "USD", "exchange_rate": 16000, "amount_fx": 1000, "amount": 16_000_000})
    if st != 201 or "amount_fx" not in usd:
        fail("USD 1.000 at 16.000 gives payment.amount 16.000.000", f"kernel (S8a money) is merged on main: money field amount_fx not served ({st})")
    else:
        eq("USD 1.000 at 16.000 -> amount 16.000.000", usd["amount"], 16_000_000)
        st, resp = api.create("payment", {"direction": "receive", "customer": customer, "currency": "USD", "exchange_rate": 16000, "amount_fx": 1000,
                                          "allocations": [{"sales_invoice": inv5, "amount": 16_000_000}]})
        check("USD payment allocating 16.000.000 to an IDR invoice of 11.100.000 -> 422", st == 422 and any("Alokasi melebihi sisa tagihan" in m for m in messages(resp)), (st, resp))
        st, resp = api.create("payment", {"direction": "receive", "customer": customer, "currency": "USD", "exchange_rate": 16000, "amount_fx": 1000,
                                          "allocations": [{"sales_invoice": inv5, "amount": 11_100_000}]})
        check("USD payment allocating 11.100.000 works", st == 201, (st, resp))

    # payment.amount follows amount_fx x exchange_rate (half away from zero); a stated amount must agree
    st, resp = api.create("payment", {"direction": "receive", "customer": customer, "currency": "USD", "exchange_rate": 16000, "amount_fx": 1000, "amount": 15_000_000})
    check("USD payment whose amount disagrees with amount_fx x rate -> 422 'Jumlah rupiah harus sama'",
          st == 422 and any("Jumlah rupiah harus sama" in m for m in messages(resp)), (st, resp))
    st, resp = api.create("payment", {"direction": "receive", "customer": customer, "currency": "USD", "exchange_rate": 3, "amount_fx": "0.5"})
    check("USD 0.5 at rate 3 -> amount 2 (1,5 half away from zero)", st == 201 and resp.get("amount") == 2, (st, resp))
    st, resp = api.create("payment", {"direction": "receive", "customer": customer, "currency": "USD", "exchange_rate": 3, "amount_fx": "0.5", "amount": 2})
    check("USD 0.5 at rate 3 with amount 2 passes the rule", st == 201, (st, resp))

    # foreign-currency invoice: the IDR header comes from the converted subtotal (rupiah PPN), then a USD payment clears it
    usd_body = {"customer": customer, "currency": "USD", "exchange_rate": 16000,
                "items": [{"item": item, "qty": 1, "rate_fx": 1000}]}
    st, ui = api.create("sales_invoice", usd_body)
    check("create USD sales_invoice (1.000 USD at 16.000)", st == 201, (st, ui))
    if st == 201:
        eq("USD invoice subtotal 16.000.000", ui["subtotal"], 16_000_000)
        eq("USD invoice ppn 1.760.000", ui["ppn"], 1_760_000)
        eq("USD invoice grand_total 17.760.000", ui["grand_total"], 17_760_000)
        eq("USD invoice amount_due 17.760.000", ui["amount_due"], 17_760_000)
        eq("USD invoice outstanding 17.760.000", ui["outstanding"], 17_760_000)
        st, resp = api.verb("sales_invoice", ui["name"], "submit", {"force": True})
        check("submit USD invoice", st == 200, resp)
        uin = resp.get("name", ui["name"])
        st, pu = api.create("payment", {"direction": "receive", "customer": customer, "currency": "USD", "exchange_rate": 16000, "amount_fx": 1110,
                                        "allocations": [{"sales_invoice": uin, "amount": 17_760_000}]})
        check("USD payment 1.110 at 16.000 (17.760.000) allocated to the USD invoice", st == 201 and pu.get("amount") == 17_760_000, (st, pu))
        if st == 201:
            st, resp = api.verb("payment", pu["name"], "submit")
            check("submit USD payment", st == 200, (st, resp))
            after = api.get("sales_invoice", uin)
            eq("USD invoice cleared: outstanding 0", after["outstanding"], 0)
            check("USD invoice status paid", after.get("status") == "paid", after.get("status"))
    other = next(c["name"] for c in api.list("customer") if c["name"] != customer)
    st, resp = api.create("withholding", {"customer": other, "sales_invoice": inv3, "cert_no": "BP-X", "cert_date": "2026-11-05"})
    check("withholding of another customer's invoice -> 422", st == 422 and any("bukan milik" in m for m in messages(resp)), (st, resp))


def scenario_procurement(api):
    vendor = "VND-0001"
    liter_item, mag_item, conv = "BRG-0004", "BRG-0005", "KONV-0001"   # BRG-0005 is stocked in Kg; 1 Karung = 50 Kg
    outlet = api.list("outlet")[0]["name"]
    lines = [{"item": liter_item, "qty": 10}, {"item": mag_item, "qty": 2, "packing": conv}]

    # purchase request with 2 lines
    st, pr = api.create("purchase_request", {"needed_by": "2026-12-01", "outlet": outlet, "purpose": "Bahan uji shutdown", "items": lines})
    check("create purchase request with 2 lines", st == 201, pr)
    if st != 201:
        return
    eq("PR line est_rate defaults to the item's standard_cost", [i["est_rate"] for i in pr["items"]], [85_000, 12_000])
    eq("PR line uom [Liter, Karung]", [i["uom"] for i in pr["items"]], ["UOM-009", "UOM-008"])
    eq("PR line amounts", [i["amount"] for i in pr["items"]], [850_000, 24_000])
    eq("PR estimated_total 874.000", pr["estimated_total"], 874_000)
    eq("PR state open", pr["state"], "open")
    st, resp = api.create("purchase_request", {"needed_by": "2026-12-01", "items": [{"item": liter_item, "qty": 0}]})
    check("PR line qty 0 -> 422 'Jumlah barang harus lebih dari 0'", st == 422 and any("Jumlah barang harus lebih dari 0" in m for m in messages(resp)), (st, resp))
    st, resp = api.create("purchase_request", {"needed_by": "2026-12-01", "items": []})
    if st == 201:
        fail("min_rows 1: a purchase request without lines is refused", "201: the kernel saved it")
    else:
        check("PR without lines -> 422", st == 422, (st, resp))
    st, resp = api.verb("purchase_request", pr["name"], "submit")
    check("submit purchase request", st == 200, resp)
    prn = resp.get("name", pr["name"])

    # make_po: vendor comes from the call body
    st, resp = api.action("purchase_request", prn, "make_po", {})
    if st == 501:
        fail("procurement.make_po creates the draft PO from the request", "kernel (S8c hooks) is merged on main: 501 hook_not_implemented")
        fail("procurement.make_po without vendor -> 422 vendor_required", "kernel (S8c hooks) is merged on main: 501 hook_not_implemented")
    else:
        check("make_po without vendor -> 422 vendor_required", st == 422 and resp.get("error", {}).get("code") == "vendor_required", (st, resp))
        st, resp = api.action("purchase_request", prn, "make_po", {"vendor": vendor})
        check("make_po with vendor opens a draft purchase_order", st == 200 and resp.get("open", {}).get("model") == "purchase_order", (st, resp))
        if st == 200:
            d = api.get("purchase_order", resp["open"]["name"])
            check("draft PO: vendor, request, docstatus 0", d["vendor"] == vendor and d["purchase_request"] == prn and d["docstatus"] == 0, d)
            eq("draft PO lines (item, qty, packing, rate = est_rate)", [(i["item"], i["qty"], i["packing"], i["rate"]) for i in d["items"]],
               [(liter_item, 10, "", 85_000), (mag_item, 2, conv, 12_000)])
            eq("draft PO line 2 stock_qty 100", d["items"][1]["stock_qty"], 100)
            eq("request state ordered", api.get("purchase_request", prn)["state"], "ordered")
            st, again = api.action("purchase_request", prn, "make_po", {"vendor": vendor})
            check("make_po again is gated (state ordered): 409 action_not_allowed", st == 409, (st, again))
            api.call("DELETE", f"/api/v1/r/purchase_order/{Api.q(d['name'])}")

    # purchase order (manual create from the request's lines): line 2 is 2 Karung of 50 Kg
    po_lines = [{"item": liter_item, "qty": 10, "rate": 85_000}, {"item": mag_item, "qty": 2, "packing": conv, "rate": 600_000}]
    st, po = api.create("purchase_order", {"vendor": vendor, "purchase_request": prn, "outlet": outlet,
                                           "payment_terms": "Tempo 30 hari", "items": po_lines})
    check("create purchase order", st == 201, po)
    if st != 201:
        return
    eq("PO line 1 stock_qty = qty (no packing)", po["items"][0]["stock_qty"], 10)
    eq("PO line 2 (2 Karung, factor 50) stock_qty 100", po["items"][1]["stock_qty"], 100)
    eq("PO line amounts", [i["amount"] for i in po["items"]], [850_000, 1_200_000])
    eq("PO subtotal 2.050.000", po["subtotal"], 2_050_000)
    eq("PO ppn 225.500", po["ppn"], 225_500)
    eq("PO grand_total 2.275.500", po["grand_total"], 2_275_500)
    eq("PO line uom: stock_uom without packing, the packing's uom (Karung) with it; stock_qty stays 100", [i["uom"] for i in po["items"]], ["UOM-009", "UOM-008"])
    eq("PO received_pct 0", po["received_pct"], 0)
    eq("PO from a PKP vendor: tax_rate 12, DPP 11/12 stored on the document", (po["tax_rate"], po["dpp_num"], po["dpp_den"], po["dpp"]), (12, 11, 12, 2_050_000))
    st, p2 = api.create("purchase_order", {"vendor": "VND-0002", "items": po_lines})
    eq("PO from a non-PKP vendor: tax_rate 0, ppn 0, grand_total 2.050.000", (p2.get("tax_rate"), p2.get("ppn"), p2.get("grand_total")), (0, 0, 2_050_000))
    st, resp = api.create("purchase_order", {"items": po_lines})
    check("PO without vendor -> 422", st == 422, (st, resp))
    st, resp = api.create("purchase_order", {"vendor": vendor, "order_date": "2026-11-10", "delivery_date": "2026-11-01", "items": po_lines})
    check("delivery before order -> 422 'Tanggal kirim tidak boleh sebelum tanggal pesanan'", st == 422 and any("Tanggal kirim" in m for m in messages(resp)), (st, resp))
    st, resp = api.create("purchase_order", {"vendor": vendor, "items": [{"item": liter_item, "qty": 1, "rate": -1}]})
    check("negative rate -> 422 'Harga tidak boleh negatif'", st == 422 and any("Harga tidak boleh negatif" in m for m in messages(resp)), (st, resp))
    st, resp = api.create("purchase_order", {"vendor": vendor, "items": [{"item": liter_item, "qty": 0, "rate": 1}]})
    check("qty 0 -> 422 'Jumlah barang harus lebih dari 0'", st == 422 and any("Jumlah barang harus lebih dari 0" in m for m in messages(resp)), (st, resp))

    st, resp = api.verb("purchase_order", po["name"], "submit")
    check("submit purchase order (2.275.500 is under the approval limit)", st == 200, (st, resp))
    pon = resp.get("name", po["name"])
    st, big = api.create("purchase_order", {"vendor": "VND-0002", "items": [{"item": liter_item, "qty": 100, "rate": 85_000}]})
    eq("big PO grand_total 8.500.000", big.get("grand_total"), 8_500_000)
    st, resp = api.verb("purchase_order", big["name"], "submit")
    # contract 0007: the dev-open caller is the owner, so the request is created and self-approved (200); a caller without an
    # approver role would get 202 with the request pending. Either way the policy must have matched and left a request.
    check("submit big purchase order", st in (200, 202), (st, resp))
    bign = resp.get("name", big["name"]) if st == 200 else big["name"]
    reqs = [a for a in approvals_of(api, "purchase_order", bign) if a.get("policy") == "po_approval"]
    check("po_approval: a PO above 5.000.000 asks finance_manager at submit (request recorded)",
          len(reqs) == 1 and reqs[0].get("approvers") == ["finance_manager"] and reqs[0].get("state") in ("approved", "pending"), reqs)
    check("po_approval: the PO under the limit made no request", not approvals_of(api, "purchase_order", pon), approvals_of(api, "purchase_order", pon))

    # foreign-currency PO: the IDR header comes from the converted subtotal
    st, upo = api.create("purchase_order", {"vendor": vendor, "currency": "USD", "exchange_rate": 16000,
                                            "items": [{"item": liter_item, "qty": 1, "rate_fx": 1000}]})
    check("create USD purchase_order (1.000 USD at 16.000)", st == 201, (st, upo))
    if st == 201:
        eq("USD PO subtotal 16.000.000", upo["subtotal"], 16_000_000)
        eq("USD PO ppn 1.760.000", upo["ppn"], 1_760_000)
        eq("USD PO grand_total 17.760.000", upo["grand_total"], 17_760_000)
        st, resp = api.verb("purchase_order", upo["name"], "submit")
        check("submit USD purchase order (above the approval limit: request recorded, owner self-approves)", st in (200, 202), (st, resp))

    # receive refuses a PO without an outlet (goods_receipt.outlet is required)
    st, noout = api.create("purchase_order", {"vendor": vendor, "items": po_lines})
    st, sub = api.verb("purchase_order", noout["name"], "submit")
    st, resp = api.action("purchase_order", sub.get("name", noout["name"]), "receive", {})
    if st == 501:
        fail("procurement.receive on a PO without outlet -> 422 outlet_required", "kernel (S8c hooks) is merged on main: 501 hook_not_implemented")
    else:
        check("receive on a PO without outlet -> 422 outlet_required 'Outlet tujuan wajib diisi di PO sebelum barang diterima'",
              st == 422 and resp.get("error", {}).get("code") == "outlet_required" and "Outlet tujuan wajib diisi" in resp["error"]["message"], (st, resp))

    # receive action: draft goods receipt with the remainder per line
    st, resp = api.action("purchase_order", pon, "receive", {})
    if st == 501:
        fail("procurement.receive creates the draft goods receipt", "kernel (S8c hooks) is merged on main: 501 hook_not_implemented")
    else:
        check("receive opens a draft goods_receipt", st == 200 and resp.get("open", {}).get("model") == "goods_receipt", (st, resp))
        if st == 200:
            d = api.get("goods_receipt", resp["open"]["name"])
            check("draft GR: vendor, outlet, purchase_order", d["vendor"] == vendor and d["outlet"] == outlet and d["purchase_order"] == pon, d)
            eq("draft GR lines: remainder per line", [(i["item"], i["qty"], i["packing"], i["rate"], i["stock_qty"]) for i in d["items"]],
               [(liter_item, 10, "", 85_000, 10), (mag_item, 2, conv, 600_000, 100)])
            api.call("DELETE", f"/api/v1/r/goods_receipt/{Api.q(d['name'])}")

    # goods receipt: 12.000 per Kg
    st, gr = api.create("goods_receipt", {"purchase_order": pon, "vendor": vendor, "outlet": outlet,
                                          "items": [{"item": liter_item, "qty": 10, "rate": 85_000}, {"item": mag_item, "qty": 2, "packing": conv, "rate": 600_000}]})
    check("create goods receipt", st == 201, gr)
    if st != 201:
        return
    eq("GR line uom [Liter, Karung]", [i["uom"] for i in gr["items"]], ["UOM-009", "UOM-008"])
    eq("GR stock_qty [10, 100]", [i["stock_qty"] for i in gr["items"]], [10, 100])
    eq("GR unit_cost per stock unit [85.000, 12.000]", [i["unit_cost"] for i in gr["items"]], [85_000, 12_000])
    eq("GR total_value 2.050.000", gr["total_value"], 2_050_000)
    st, resp = api.create("goods_receipt", {"purchase_order": pon, "vendor": vendor, "items": [{"item": liter_item, "qty": 1, "rate": 1}]})
    check("GR without outlet -> 422", st == 422, (st, resp))
    st, resp = api.verb("goods_receipt", gr["name"], "submit")
    check("submit goods receipt", st == 200, (st, resp))
    grn = resp.get("name", gr["name"])
    if not installed(api, "stock_move"):
        # S8c's stock.post_moves answers with a message and writes nothing while the stock module is not installed
        # stock is optional for procurement and not installed here (demo-fnb covers the moves): the hooks are no-ops
        eq("stock.post_moves is a no-op without the stock module: the receipt submits, PO received_pct stays 0", api.get("purchase_order", pon)["received_pct"], 0)
        st, resp = api.verb("goods_receipt", grn, "cancel")
        check("stock.reverse_moves is a no-op without the stock module: the receipt cancels", st == 200, (st, resp))
    else:
        pod = api.get("purchase_order", pon)
        eq("PO received_pct 100 after the receipt", pod["received_pct"], 100)
        eq("PO received_qty per line [10, 2]", [i["received_qty"] for i in pod["items"]], [10, 2])
        st, resp = api.action("purchase_order", pon, "receive", {})
        check("receive on a fully received PO is gated: 409", st == 409, (st, resp))

    # vendor bill: amount_due = grand_total - pph23_amount
    bill_body = {"vendor": vendor, "purchase_order": pon, "goods_receipt": grn, "bill_no": "INV-CBU-0001", "bill_date": "2026-11-10",
                 "pph23_amount": 20_000,
                 "items": [{"item": liter_item, "qty": 10, "rate": 85_000}, {"item": mag_item, "qty": 2, "rate": 600_000}]}
    vendor_before = api.get("vendor", vendor)["outstanding"]
    st, vb = api.create("vendor_bill", bill_body)
    check("create vendor bill", st == 201, vb)
    if st != 201:
        return
    eq("bill subtotal 2.050.000", vb["subtotal"], 2_050_000)
    eq("bill ppn 225.500", vb["ppn"], 225_500)
    eq("bill grand_total 2.275.500", vb["grand_total"], 2_275_500)
    eq("bill amount_due = grand_total - pph23_amount = 2.255.500", vb["amount_due"], 2_255_500)
    eq("bill outstanding = amount_due - paid_amount", vb["outstanding"], 2_255_500)
    check("bill due_date defaults to bill_date + vendor payment_days (30)", vb["due_date"] == "2026-12-10", vb.get("due_date"))
    st, resp = api.create("vendor_bill", dict(bill_body, pph23_amount=3_000_000))
    check("pph23 above total -> 422 'PPh 23 tidak boleh negatif atau melebihi total'", st == 422 and any("PPh 23 tidak boleh" in m for m in messages(resp)), (st, resp))
    st, resp = api.create("vendor_bill", dict(bill_body, due_date="2026-11-01"))
    check("due before bill date -> 422 'Jatuh tempo tidak boleh sebelum tanggal faktur'", st == 422 and any("Jatuh tempo" in m for m in messages(resp)), (st, resp))
    st, resp = api.create("vendor_bill", {k: v for k, v in bill_body.items() if k != "bill_no"})
    check("bill without bill_no -> 422", st == 422, (st, resp))
    st, resp = api.verb("vendor_bill", vb["name"], "submit")
    check("submit vendor bill", st == 200, (st, resp))
    vbn = resp.get("name", vb["name"])
    posted = api.get("vendor", vendor)["outstanding"]
    if skipped(api, "procurement.post_payable", f"vendor_bill/{vbn}"):
        fail("procurement.post_payable adds amount_due to vendor.outstanding", "kernel (S8c hooks) is merged on main: hook.skipped in the audit log")
        fail("procurement.reverse_payable subtracts on cancel", "kernel (S8c hooks) is merged on main: hook.skipped in the audit log")
        hooked = False
    else:
        eq("vendor outstanding += amount_due (2.255.500)", posted, vendor_before + 2_255_500)
        hooked = True

    # payments to the bill (direction pay) and the allocation-target rule
    def pay(amount, rows, **extra):
        body = {"direction": "pay", "vendor": vendor, "amount": amount, "allocations": rows}
        body.update(extra)
        return api.create("payment", body)

    st, resp = pay(3_000_000, [{"vendor_bill": vbn, "amount": 2_300_000}])
    check("allocation 2.300.000 vs bill outstanding 2.255.500 -> 422 'Alokasi melebihi sisa tagihan'",
          st == 422 and any("Alokasi melebihi sisa tagihan" in m for m in messages(resp)), (st, resp))
    cust = api.list("customer")[0]["name"]
    st, inv = api.create("sales_invoice", invoice_body(cust, services(api)[0]["name"]))
    check("create a sales invoice to misuse", st == 201, inv)
    st, resp = pay(1_000_000, [{"sales_invoice": inv["name"], "amount": 1_000_000}])
    check("direction pay allocated to a sales_invoice -> 422 'Pilih faktur penjualan (terima) atau tagihan pemasok (bayar)'",
          st == 422 and any("tagihan pemasok (bayar)" in m for m in messages(resp)), (st, resp))
    st, resp = api.create("payment", {"direction": "receive", "customer": cust, "amount": 1_000_000, "allocations": [{"vendor_bill": vbn, "amount": 1_000_000}]})
    check("direction receive allocated to a vendor_bill -> 422", st == 422 and any("tagihan pemasok (bayar)" in m for m in messages(resp)), (st, resp))
    st, resp = pay(2_000_000, [{"sales_invoice": inv["name"], "vendor_bill": vbn, "amount": 1_000_000}])
    check("row naming both a sales_invoice and a vendor_bill -> 422", st == 422, (st, resp))
    st, resp = pay(1_000_000, [{"amount": 1_000_000}])
    check("pay row without target -> 422", st == 422, (st, resp))

    st, p = pay(2_255_500, [{"vendor_bill": vbn, "amount": 2_255_500}])
    check("create payment (pay) allocated to the vendor bill", st == 201, (st, p))
    if st != 201:
        return
    eq("allocation row snapshots the bill's outstanding (default uses vendor_bill)", p["allocations"][0]["outstanding"], 2_255_500)
    eq("payment unallocated 0", p["unallocated"], 0)
    st, resp = api.verb("payment", p["name"], "submit")
    check("submit payment to vendor", st == 200, (st, resp))
    pn = resp.get("name", p["name"])
    if skipped(api, "payments.allocate", f"payment/{pn}"):
        fail("payments.allocate pays the vendor bill (direction pay)", "kernel (S8c hooks) is merged on main: hook.skipped in the audit log")
        api.verb("payment", pn, "cancel")
    else:
        b = api.get("vendor_bill", vbn)
        eq("bill paid_amount 2.255.500", b["paid_amount"], 2_255_500)
        eq("bill outstanding 0", b["outstanding"], 0)
        if hooked:
            eq("vendor outstanding back to start", api.get("vendor", vendor)["outstanding"], vendor_before)
        st, resp = api.verb("vendor_bill", vbn, "cancel")
        check("bill with payments cannot be cancelled (has_payments)", st == 409, (st, resp))
        st, resp = api.verb("payment", pn, "cancel")
        check("cancel payment", st == 200, (st, resp))
        eq("cancel restores bill outstanding", api.get("vendor_bill", vbn)["outstanding"], 2_255_500)
    st, resp = api.verb("vendor_bill", vbn, "cancel")
    check("cancel vendor bill", st == 200, (st, resp))
    if hooked:
        eq("cancel restores vendor outstanding", api.get("vendor", vendor)["outstanding"], vendor_before)


# ---------- scenarios: roles and approvals on demo-jasa (non-owner members, contract 0007)

# ---------- scenario: the money rail (Tahap 1): remind, get paid by transfer or gateway, match the bank statement

def statement_csv(rows, seq=True):
    """A bank_statement_csv file: rows of (date dd/mm/yyyy, note, credit, debit, balance). Urutan is the number of
    the line within its day, as the owner copies the bank's export (seq=False leaves the column out)."""
    out = ["Rekening,Tanggal,Urutan,Keterangan,Masuk,Keluar,Saldo" if seq else "Rekening,Tanggal,Keterangan,Masuk,Keluar,Saldo"]
    per_day = {}
    for d, note, cr, db, bal in rows:
        per_day[d] = per_day.get(d, 0) + 1
        out.append(f'1110000001,{d},{per_day[d]},"{note}",{cr},{db},{bal}' if seq else f'1110000001,{d},"{note}",{cr},{db},{bal}')
    return ("\n".join(out) + "\n").encode()


def scenario_xendit_callbacks(api):
    """Rel uang U13/U17 (ready, not active): synthetic Xendit callbacks, as the xendit connector's triggers emit them,
    run through payment_from_xendit_* with POST /flows/<name>/run (the kernel has no connector runtime yet). A paid
    callback drafts one payment for the invoice; repeats, non-payments, cancelled or unknown invoices record nothing."""
    if not installed(api, "gateway_ref_claim"):
        fail("xendit callbacks", "payments module not installed on this site")
        return
    customer = api.list("customer")[0]["name"]
    item = (services(api) or api.list("item"))[0]["name"]

    def submitted(rate):
        st, inv = api.create("sales_invoice", invoice_body(customer, item, rate=rate, tax_rate=0))
        st, resp = api.verb("sales_invoice", inv.get("name", ""), "submit", {"force": True})
        check(f"xendit: submit invoice {rate}", st == 200, (st, resp))
        return resp.get("name", inv.get("name")) if isinstance(resp, dict) else inv.get("name")

    def callback(flow, **event):
        st, out = api.call("POST", f"/api/v1/flows/payment_from_xendit_{flow}/run", {"event": event})
        return st, out or {}

    def payments_of(ref):
        return api.find("payment", f"gateway_ref == '{ref}' && docstatus != 2")

    inv = submitted(1_250_000)
    st, out = callback("va", gateway_ref="va-pay-0001", external_id=inv, paid_amount=1_250_000, paid=True, method="va",
                       paid_at="2026-11-06T10:00:00Z", bank_code="BCA", account_number="1234567890")
    pays = payments_of("va-pay-0001")
    check("VA paid: one draft payment for the invoice, gateway xendit, the whole amount allocated", st == 200 and len(pays) == 1
          and pays[0]["docstatus"] == 0 and pays[0]["gateway"] == "xendit" and pays[0]["method"] == "va" and pays[0]["reference"] == inv, (st, out, pays))
    if not pays:
        return
    pay = api.get("payment", pays[0]["name"])
    eq("VA paid: allocation to the invoice", [(a["sales_invoice"], a["amount"]) for a in pay.get("allocations", [])], [(inv, 1_250_000)])
    check("VA paid: the gateway id is claimed", len(api.find("gateway_ref_claim", "gateway_ref == 'va-pay-0001'")) == 1, "")
    st, out = callback("va", gateway_ref="va-pay-0001", external_id=inv, paid_amount=1_250_000, paid=True, method="va")
    eq("the same VA callback again: no second payment", len(payments_of("va-pay-0001")), 1)
    st, resp = api.verb("payment", pay["name"], "submit")
    check("finance submits the drafted payment", st == 200, (st, resp))
    pay_name = resp.get("name", pay["name"]) if isinstance(resp, dict) else pay["name"]   # the draft is renamed on submit
    eq("the invoice is settled", api.get("sales_invoice", inv)["outstanding"], 0)
    st, out = callback("va", gateway_ref="va-pay-0001", external_id=inv, paid_amount=1_250_000, paid=True, method="va")
    eq("the callback after submit: still one payment", len(payments_of("va-pay-0001")), 1)

    # non-payments: a failed QR, an expired link, a zero amount
    inv2 = submitted(800_000)
    st, out = callback("qr", gateway_ref="qr-0002", external_id=inv2, paid_amount=800_000, paid=False, method="qris", status="FAILED")
    check("QR callback that did not succeed: skipped, no payment", out.get("skipped") is True and not payments_of("qr-0002"), out)
    st, out = callback("invoice", gateway_ref="inv-0003", external_id=inv2, paid_amount=0, paid=False, method="other", status="EXPIRED")
    check("expired payment link: skipped, no payment", out.get("skipped") is True and not payments_of("inv-0003"), out)
    st, out = callback("qr", gateway_ref="qr-0004", external_id=inv2, paid_amount=0, paid=True, method="qris")
    check("a paid callback of 0: skipped", out.get("skipped") is True and not payments_of("qr-0004"), out)

    # overpaid and split: the allocation never exceeds what is still open after earlier drafts
    st, out = callback("qr", gateway_ref="qr-0005", external_id=inv2, paid_amount=500_000, paid=True, method="qris")
    st, out = callback("invoice", gateway_ref="inv-0006", external_id=inv2, paid_amount=500_000, paid=True, method="ewallet")
    a5 = api.get("payment", (payments_of("qr-0005") or [{"name": ""}])[0]["name"])
    a6 = api.get("payment", (payments_of("inv-0006") or [{"name": ""}])[0]["name"])
    eq("two link/QR payments on one invoice: 500.000 then the 300.000 still open", (a5.get("allocated_total"), a6.get("allocated_total"), a6.get("unallocated")), (500_000, 300_000, 200_000))

    # a cancelled invoice and an unknown number: nothing recorded, the owner is told
    inv3 = submitted(450_000)
    api.verb("sales_invoice", inv3, "cancel")
    st, out = callback("va", gateway_ref="va-0007", external_id=inv3, paid_amount=450_000, paid=True, method="va")
    check("paid callback for a cancelled invoice: no payment, the owner is notified",
          not payments_of("va-0007") and any("va-0007" in n.get("text", "") for n in out.get("notifications", [])), out)
    st, out = callback("va", gateway_ref="va-0008", external_id="INV-TIDAK-ADA", paid_amount=99_000, paid=True, method="va")
    check("paid callback for an unknown invoice number: no payment, the owner is notified",
          not payments_of("va-0008") and any("INV-TIDAK-ADA" in n.get("text", "") for n in out.get("notifications", [])), out)

    # malformed callbacks: no gateway id is skipped (not a 500); a method outside payment's options is recorded as other
    st, out = callback("va", external_id=inv2, paid_amount=10_000, paid=True, method="va")
    check("a callback without gateway_ref: skipped, no error", st == 200 and out.get("skipped") is True, (st, out))
    inv4 = submitted(275_000)
    st, out = callback("invoice", gateway_ref="inv-0009", external_id=inv4, paid_amount=275_000, paid=True, method="CREDIT_CARD")
    p9 = payments_of("inv-0009")
    check("a method outside the options is recorded as 'other', the money is not lost", st == 200 and len(p9) == 1 and p9[0]["method"] == "other", (st, out, p9))

    # the same callback six times in parallel: one payment, the other runs are refused by the claim's unique index
    import threading
    inv5 = submitted(640_000)
    results = []
    def fire():
        results.append(callback("qr", gateway_ref="qr-race-0010", external_id=inv5, paid_amount=640_000, paid=True, method="qris")[0])
    threads = [threading.Thread(target=fire) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    eq("six parallel callbacks with one gateway id: exactly one payment", len(payments_of("qr-race-0010")), 1)
    check("six parallel callbacks: one run succeeded, the others were refused or found it", results.count(200) >= 1, results)
    eq("six parallel callbacks: one claim, naming that payment",
       [c["payment"] for c in api.find("gateway_ref_claim", "gateway_ref == 'qr-race-0010'")], [p["name"] for p in payments_of("qr-race-0010")])

    # a draft deleted by finance leaves its claim behind: the callback again, six times in parallel, still gives one
    # payment (the old claim is released and a new one inserted, so the unique index refuses the racing runs)
    inv6 = submitted(330_000)
    callback("va", gateway_ref="va-del-0011", external_id=inv6, paid_amount=330_000, paid=True, method="va")
    first = payments_of("va-del-0011")
    st, resp = api.call("DELETE", f"/api/v1/r/payment/{api.q(first[0]['name'])}") if first else (0, None)
    check("finance deletes the drafted Xendit payment", st in (200, 204), (st, resp))
    results = []
    def fire_again():
        results.append(callback("va", gateway_ref="va-del-0011", external_id=inv6, paid_amount=330_000, paid=True, method="va")[0])
    threads = [threading.Thread(target=fire_again) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    eq("after a deleted draft, six parallel callbacks: exactly one new payment", len(payments_of("va-del-0011")), 1)
    eq("after a deleted draft: one live claim naming the new payment, the old one released",
       ([c["payment"] for c in api.find("gateway_ref_claim", "gateway_ref == 'va-del-0011'")], len(api.find("gateway_ref_claim", "released_ref == 'va-del-0011'"))),
       ([p["name"] for p in payments_of("va-del-0011")], 1))

    # a cancelled payment gives its gateway id back: the callback can be recorded again
    st, resp = api.verb("payment", pay_name, "cancel")
    check("cancel the Xendit payment", st == 200, (st, resp))
    st, out = callback("va", gateway_ref="va-pay-0001", external_id=inv, paid_amount=1_250_000, paid=True, method="va")
    again = payments_of("va-pay-0001")
    check("after the cancel the same callback drafts a new payment and takes over the claim",
          len(again) == 1 and again[0]["name"] != pay_name
          and api.find("gateway_ref_claim", "gateway_ref == 'va-pay-0001'")[0]["payment"] == again[0]["name"], (again, out))


def qris_payload(name, nmid, city="BANDUNG", poi="11", amount=None, fee=""):
    """A synthetic QRIS (EMVCo merchant-presented) payload with a correct CRC-16/CCITT-FALSE: no real merchant."""
    def tlv(tag, value):
        return f"{tag}{len(value):02d}{value}"
    def crc(text):
        c = 0xFFFF
        for b in text.encode():
            c ^= b << 8
            for _ in range(8):
                c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
        return f"{c:04X}"
    p = (tlv("00", "01") + tlv("01", poi)
         + tlv("26", tlv("00", "ID.CO.QRIS.WWW") + tlv("01", "936000000000000001") + tlv("02", "000000000000001") + tlv("03", "UMI"))
         + tlv("51", tlv("00", "ID.CO.QRIS.WWW") + tlv("02", nmid) + tlv("03", "UMI"))
         + tlv("52", "5812") + tlv("53", "360") + (tlv("54", amount) if amount else "") + fee + tlv("58", "ID") + tlv("59", name) + tlv("60", city) + "6304")
    return p + crc(p)


def scenario_payment_instruction(api):
    """G2 gladi (U1/U2 rel uang): the business's rekening and static QRIS, copied into each invoice when it is submitted,
    so an old invoice keeps the account it was billed with; transfer notes that carry the invoice number loosely still match."""
    if not installed(api, "bank_account"):
        fail("payment instruction", "bank_account not installed on this site")
        return
    site = CTX["kernel"].site
    customer = api.list("customer")[0]["name"]
    item = (services(api) or api.list("item"))[0]["name"]
    q1 = qris_payload("PT CONTOH TAGIH", "ID1020000000101")
    good = {"bank": "BCA", "account_no": "1110000001", "holder": "PT Contoh Tagih", "is_default": True, "qris_payload": q1,
            "instructions": "Kirim bukti transfer ke WhatsApp kami."}

    # validation: rekening digits only, QRIS frame + CRC, static, rupiah, no fixed amount
    for label, patch in (("account number with spaces", {"account_no": "111 000 0001"}), ("account number with dots", {"account_no": "111.000.0001"}),
                         ("account number with letters", {"account_no": "BCA1110000001"}), ("account number too short", {"account_no": "1234"}),
                         ("QRIS with a wrong CRC", {"qris_payload": q1[:-4] + ("0000" if q1[-4:] != "0000" else "1111")}),
                         ("QRIS edited by hand (CRC no longer fits)", {"qris_payload": q1.replace("PT CONTOH TAGIH", "PT CONTOH TAGIX")}),
                         ("QRIS cut short", {"qris_payload": q1[:-1]}),
                         ("dynamic QRIS (one-off)", {"qris_payload": qris_payload("PT CONTOH TAGIH", "ID1020000000101", poi="12")}),
                         ("QRIS with a fixed amount", {"qris_payload": qris_payload("PT CONTOH TAGIH", "ID1020000000101", amount="50000")}),
                         ("QRIS with a leading space", {"qris_payload": " " + q1}),
                         # a fixed or percentage fee on top of the bill: the customer pays more than the invoice
                         ("QRIS with a fixed fee (55=02, 56)", {"qris_payload": qris_payload("PT CONTOH TAGIH", "ID1020000000101", fee="550202" + "56041000")}),
                         ("QRIS with a percentage fee (55=03, 57)", {"qris_payload": qris_payload("PT CONTOH TAGIH", "ID1020000000101", fee="550203" + "57032.5")}),
                         ("QRIS with a fee amount but no indicator (56)", {"qris_payload": qris_payload("PT CONTOH TAGIH", "ID1020000000101", fee="56041000")}),
                         ("QRIS whose merchant name is blank", {"qris_payload": qris_payload("   ", "ID1020000000101")}),
                         ("QRIS whose NMID is blank", {"qris_payload": qris_payload("PT CONTOH TAGIH", "  ")})):
        st, resp = api.create("bank_account", dict(good, **patch))
        check(f"bank_account refuses {label}", st == 422 and any(e.get("code") == "rule" for e in (resp or {}).get("errors", [])), (st, resp))
        if st == 201:
            api.call("DELETE", f"/api/v1/r/bank_account/{api.q(resp['name'])}")
    st, acct = api.create("bank_account", good)
    check("bank_account: valid rekening + static QRIS saved as the default", st == 201, (st, acct))
    if st != 201:
        return
    eq("NMID and merchant name are read from the QRIS", (acct.get("qris_nmid"), acct.get("qris_merchant_name")), ("ID1020000000101", "PT CONTOH TAGIH"))
    st, resp = api.create("bank_account", dict(good, account_no="2220000002", qris_payload=""))
    check("a second default account in the same org is refused", st in (409, 422), (st, resp))
    st, resp = api.create("bank_account", dict(good, account_no="2220000002", qris_payload="", active=False))
    check("a default account must be active", st == 422, (st, resp))
    st, spare = api.create("bank_account", dict(good, bank="Mandiri", account_no="1230009876543", is_default=False, qris_payload=""))
    check("a second, non-default account without QRIS is fine", st == 201, (st, spare))

    # roles: only finance (and head office) change where customers pay
    sm, fm = CTX.get("sm"), CTX.get("fm")
    if sm:
        st, resp = sm.create("bank_account", dict(good, account_no="3330000003", is_default=False))
        check("sales_manager cannot add a bank account", st == 403, (st, resp))
        st, resp = sm.patch("bank_account", acct["name"], {"account_no": "9999999999"})
        check("sales_manager cannot change the rekening", st == 403, (st, resp))
    if fm:
        st, resp = fm.patch("bank_account", spare["name"], {"instructions": "Rekening cadangan."})
        check("finance_manager edits a bank account", st == 200, (st, resp))

    # submit: the invoice gets a copy of the default account, with its number as payment reference
    def submitted(rate, as_api=None, **extra):
        cl = as_api or api
        st, inv = cl.create("sales_invoice", invoice_body(customer, item, rate=rate, tax_rate=0, **extra))
        check(f"payment instruction: create invoice {rate}", st == 201, (st, inv))
        st, resp = cl.verb("sales_invoice", inv.get("name", ""), "submit", {"force": True})
        check(f"payment instruction: submit invoice {rate}", st == 200, (st, resp))
        return resp.get("name", inv.get("name")) if isinstance(resp, dict) else inv.get("name")

    inv1 = submitted(1_111_000)
    pis = api.find("payment_instruction", f"sales_invoice == '{inv1}'")
    check("submitting copies the default account into one payment_instruction", len(pis) == 1, pis)
    if not pis:
        return
    pi = pis[0]
    eq("the copy holds rekening, QRIS, instruction, reference = invoice number, amount due",
       (pi["bank"], pi["account_no"], pi["holder"], pi["qris_payload"], pi["qris_nmid"], pi["qris_merchant_name"], pi["instructions"], pi["reference"], pi["amount"]),
       ("BCA", "1110000001", "PT Contoh Tagih", q1, "ID1020000000101", "PT CONTOH TAGIH", good["instructions"], inv1, api.get("sales_invoice", inv1)["amount_due"]))
    if sm:
        inv_sm = submitted(1_212_000, as_api=sm)
        check("an invoice submitted by the sales_manager gets its copy too (the flow runs as the submitter)",
              len(api.find("payment_instruction", f"sales_invoice == '{inv_sm}'")) == 1, inv_sm)

    # the account changes: the old invoice keeps what it billed with, a new one takes the new account
    q2 = qris_payload("PT CONTOH TAGIH BARU", "ID1020000000102", city="JAKARTA")
    st, resp = api.patch("bank_account", acct["name"], {"account_no": "4440000004", "qris_payload": q2})
    check("change the default account's number and QRIS", st == 200, (st, resp))
    pi_old = api.get("payment_instruction", pi["name"])
    eq("the earlier invoice's copy is unchanged", (pi_old["account_no"], pi_old["qris_payload"], pi_old["qris_nmid"]), ("1110000001", q1, "ID1020000000101"))
    inv2 = submitted(2_222_000)
    pi2 = (api.find("payment_instruction", f"sales_invoice == '{inv2}'") or [{}])[0]
    eq("an invoice submitted after the change carries the new account", (pi2.get("account_no"), pi2.get("qris_nmid")), ("4440000004", "ID1020000000102"))

    # the copy cannot be written, deleted, or made up by hand
    st, resp = api.patch("payment_instruction", pi["name"], {"account_no": "5550000005"})
    check("the copied account number is read-only", st in (200, 403, 422) and api.get("payment_instruction", pi["name"])["account_no"] == "1110000001", (st, resp))
    st, resp = api.create("payment_instruction", {"sales_invoice": inv1, "bank_account": spare["name"]})
    check("a second payment instruction for the same invoice is refused", st in (409, 422), (st, resp))
    st, dr = api.create("sales_invoice", invoice_body(customer, item, rate=3_000))
    st, resp = api.create("payment_instruction", {"sales_invoice": dr["name"], "bank_account": acct["name"]})
    check("no payment instruction for a draft invoice", st == 422, (st, resp))

    # no default account: the invoice submits without a copy (the print falls back to the letterhead)
    st, resp = api.patch("bank_account", acct["name"], {"is_default": False})
    check("unset the default account", st == 200, (st, resp))
    inv3 = submitted(3_333_000)
    eq("without a default account no copy is made", len(api.find("payment_instruction", f"sales_invoice == '{inv3}'")), 0)
    st, resp = api.create("payment_instruction", {"sales_invoice": inv3, "bank_account": spare["name"], "account_no": "6660000006", "bank": "BANK PALSU"})
    check("a hand-made copy with a made-up rekening is refused (the copied fields are read-only and must equal the account)",
          st in (403, 422) and not api.find("payment_instruction", f"sales_invoice == '{inv3}' && account_no == '6660000006'"), (st, resp))
    st, resp = api.patch("bank_account", acct["name"], {"is_default": True})
    check("set the default account again", st == 200, (st, resp))

    # cancel + amend: the amended invoice is a new submission with its own copy
    st, resp = api.verb("sales_invoice", inv2, "cancel")
    check("cancel an invoice with a payment instruction", st == 200, (st, resp))
    st, am = api.verb("sales_invoice", inv2, "amend")
    check("amend it", st in (200, 201), (st, am))
    st, resp = api.verb("sales_invoice", am.get("name", ""), "submit", {"force": True})
    am_name = resp.get("name", "") if isinstance(resp, dict) else ""
    check("the amended invoice submits and gets its own copy", st == 200 and len(api.find("payment_instruction", f"sales_invoice == '{am_name}'")) == 1, (st, resp))

    # a second org has its own default account (the default is per org)
    if site == "demo-jasa":
        org2 = Api.login(CTX["kernel"].base, CTX["kernel"].owner_email, CTX["kernel"].owner_pw, org="cv_contoh_pelatihan")
        st, a2 = org2.create("bank_account", dict(good, holder="CV Contoh Pelatihan", account_no="7770000007",
                                                  qris_payload=qris_payload("CV CONTOH PELATIHAN", "ID1020000000201")))
        check("the second PT saves its own default account", st == 201, (st, a2))
        eq("each PT sees only its own accounts", (sorted(a["account_no"] for a in api.list("bank_account")), [a["account_no"] for a in org2.list("bank_account")]),
           (["1230009876543", "4440000004"], ["7770000007"]))

    # mutasi: the invoice number written loosely in the transfer note still finds the invoice
    if not installed(api, "bank_transaction"):
        return
    inv4 = submitted(4_444_123)
    inv5 = submitted(4_444_123)            # same amount: only the number can tell them apart
    inv6 = submitted(4_444_123)
    # the series may be INV-2026-00012 or INV/CIN/26/0005: each note differs from the number as stored
    loose4 = "TRSF E-BANKING CR " + re.sub(r"[-/]", " ", inv4).lower() + " BUDI"
    loose5 = "QR TRF " + re.sub(r"[-/]", "", inv5)
    loose6 = "TRF " + re.sub(r"[-/]", ".", inv6) + " LUNAS"
    check("the notes do not contain the invoice numbers as stored", all(n not in t for n, t in ((inv4, loose4), (inv5, loose5), (inv6, loose6))), (loose4, loose5, loose6))
    rows = [("06/11/2026", loose4, 4_444_123, 0, 50_000_000), ("06/11/2026", loose5, 4_444_123, 0, 54_444_123),
            ("06/11/2026", loose6, 4_444_123, 0, 58_888_246),
            ("06/11/2026", "TRSF CR 4444123 TANPA NOMOR", 4_444_123, 0, 63_332_369)]
    st, pv = api.upload("/api/v1/imports/bank_statement_csv", {}, "mutasi-referensi.csv", statement_csv(rows))
    check("statement with loosely written references: preview without errors", st == 200 and pv.get("errors") == [], (st, pv))
    if st != 200:
        return
    st, c = api.call("POST", f"/api/v1/imports/bank_statement_csv/{pv['id']}/commit")
    check("statement commit", st == 200 and len(c.get("records", [])) == 4, (st, c))
    lines = {l["description"]: l for l in api.find("bank_transaction", "date == '2026-11-06' && amount == 4444123")}
    eq("lower case with spaces instead of dashes: suggested that invoice", (lines.get(loose4, {}).get("status"), lines.get(loose4, {}).get("suggested_invoice")), ("suggested", inv4))
    eq("without separators: suggested that invoice", (lines.get(loose5, {}).get("status"), lines.get(loose5, {}).get("suggested_invoice")), ("suggested", inv5))
    l6 = lines.get(rows[2][1], {})
    eq("dots instead of separators: suggested that invoice", (l6.get("status"), l6.get("suggested_invoice")), ("suggested", inv6))
    l7 = lines.get(rows[3][1], {})
    check("no number and three invoices of that amount: no suggestion", l7.get("status") == "unmatched" and not l7.get("suggested_invoice"), l7)

    # the number decides only when the money fits it: a note whose number is a prefix of what the customer typed
    # ("...0004 2" read as 00042) must not pull a line worth more than that invoice still owes
    inv8 = submitted(5_000_001)
    inv9 = submitted(5_000_002)
    note8 = "BAYAR " + inv8 + " 2"            # compacts to <inv8 number>2: contains inv8's number
    st, pv = api.upload("/api/v1/imports/bank_statement_csv", {}, "mutasi-nominal.csv",
                        statement_csv([("21/11/2026", note8, 5_000_002, 0, 70_000_000)]))
    st, c = api.call("POST", f"/api/v1/imports/bank_statement_csv/{pv['id']}/commit") if st == 200 else (st, {})
    l8 = (api.find("bank_transaction", "date == '2026-11-21' && amount == 5000002") or [{}])[0]
    eq("a number in the note whose invoice owes less than the line: the invoice of that exact amount instead",
       (l8.get("status"), l8.get("suggested_invoice")), ("suggested", inv9))
    st, pv = api.upload("/api/v1/imports/bank_statement_csv", {}, "mutasi-sebagian.csv",
                        statement_csv([("22/11/2026", "CICIL " + inv8, 1_000_000, 0, 71_000_000)]))
    st, c = api.call("POST", f"/api/v1/imports/bank_statement_csv/{pv['id']}/commit") if st == 200 else (st, {})
    l9 = (api.find("bank_transaction", "date == '2026-11-22' && amount == 1000000") or [{}])[0]
    eq("a part payment naming the invoice still suggests it", (l9.get("status"), l9.get("suggested_invoice")), ("suggested", inv8))


def scenario_money_rail(api):
    if not installed(api, "bank_transaction"):
        fail("money rail", "bank_statement module not installed on this site")
        return
    item = services(api)[0]["name"]
    st, cust = api.create("customer", {"customer_name": "PT Contoh Lunas", "phone": "081200001111"})
    check("money rail: create customer with a WhatsApp number", st == 201, cust)
    customer = cust["name"]

    def submitted_invoice(rate, **extra):
        st, inv = api.create("sales_invoice", invoice_body(customer, item, rate=rate, tax_rate=0, **extra))
        check(f"money rail: create invoice {rate}", st == 201, inv)
        st, resp = api.verb("sales_invoice", inv["name"], "submit", {"force": True})
        check(f"money rail: submit invoice {rate}", st == 200, resp)
        return resp.get("name", inv["name"])

    past = "2026-01-05"
    inv_a = submitted_invoice(2_750_000, posting_date="2026-01-01", due_date=past)   # paid by transfer, number in the note
    inv_b = submitted_invoice(1_234_000)                                               # paid by transfer, amount only
    inv_c = submitted_invoice(990_000)                                                 # paid through the gateway
    inv_d = submitted_invoice(500_000)
    inv_e = submitted_invoice(500_000)                                                 # same amount as inv_d: no suggestion

    # 1. tagih: the WhatsApp reminder print and the reminder log
    st, page, _ = api.print_html("sales_invoice", inv_a, "sales_invoice_whatsapp")
    check("WhatsApp print: wa.me link to 62812..., text filled in", st == 200 and "https://wa.me/6281200001111?text=" in page and "Halo%20PT%20Contoh%20Lunas" in page, (st, page[:400]))
    check("WhatsApp print of an overdue invoice says how late and asks for the number in the note",
          "lewat" in page and "hari dari jatuh tempo" in page and "berita transfer" in page, page[:600])
    st, rem = api.create("invoice_reminder", {"sales_invoice": inv_a})
    check("invoice_reminder: customer, overdue template and outstanding default from the invoice",
          st == 201 and rem["customer"] == customer and rem["template"] == "pengingat_jatuh_tempo" and rem["outstanding"] == 2_750_000, (st, rem))
    st, draft = api.create("sales_invoice", invoice_body(customer, item, rate=1000))
    st, resp = api.create("invoice_reminder", {"sales_invoice": draft["name"]})
    check("invoice_reminder refuses a draft invoice", st == 422, (st, resp))
    st, run = api.call("POST", "/api/v1/flows/invoice_due_digest/run", {})
    texts = [n.get("text", "") for n in (run or {}).get("notifications", [])]
    check("invoice_due_digest lists the overdue invoice to the owner", st == 200 and any("faktur jatuh tempo" in t for t in texts), (st, run))

    # 2. bayar lewat gateway (a Xendit callback turned into a payment): gateway_ref is unique
    body = {"direction": "receive", "customer": customer, "amount": 990_000, "method": "va", "gateway": "xendit",
            "gateway_ref": "va-paid-test-0001", "reference": inv_c, "allocations": [{"sales_invoice": inv_c, "amount": 990_000}]}
    st, gp = api.create("payment", body)
    check("gateway payment created with gateway_ref", st == 201, (st, gp))
    st, resp = api.verb("payment", gp["name"], "submit")
    check("gateway payment submitted", st == 200, (st, resp))
    gp_name = resp.get("name", gp["name"])
    eq("gateway payment settles its invoice (outstanding 0)", api.get("sales_invoice", inv_c)["outstanding"], 0)
    st, resp = api.create("payment", body)
    check("the same callback again (same gateway_ref) is refused", st in (409, 422), (st, resp))
    st, resp = api.create("payment", dict(body, gateway_ref="", amount=1))
    check("a gateway payment without gateway_ref is refused", st == 422, (st, resp))

    # a gateway payment allocated to the wrong invoice is cancelled and amended under the same gateway id
    st, resp = api.verb("payment", gp_name, "cancel")
    check("cancel the gateway payment", st == 200, (st, resp))
    eq("cancelled gateway payment reopens its invoice", api.get("sales_invoice", inv_c)["outstanding"], 990_000)
    st, am = api.verb("payment", gp_name, "amend")
    check("amend a cancelled gateway payment: a draft with the same gateway_ref (no 500)",
          st in (200, 201) and am.get("gateway_ref") == body["gateway_ref"], (st, am))
    st, resp = api.verb("payment", am.get("name", ""), "submit")
    check("the amended gateway payment submits", st == 200, (st, resp))
    am_name = resp.get("name", "") if isinstance(resp, dict) else ""
    eq("amended gateway payment settles the invoice again", api.get("sales_invoice", inv_c)["outstanding"], 0)
    st, resp = api.create("payment", body)
    check("after the amend the same callback is still refused", st in (409, 422), (st, resp))
    # cancel again, then the callback is recorded afresh as a new payment (no amend)
    st, resp = api.verb("payment", am_name, "cancel")
    check("cancel the amended gateway payment", st == 200, (st, resp))
    st, again = api.create("payment", body)
    check("a cancelled gateway id can be recorded again as a new payment", st == 201, (st, again))
    st, resp = api.verb("payment", again.get("name", ""), "submit")
    check("the re-recorded gateway payment submits", st == 200, (st, resp))
    eq("re-recorded gateway payment settles the invoice", api.get("sales_invoice", inv_c)["outstanding"], 0)
    # a draft with a gateway id that is deleted does not keep the id
    body2 = dict(body, gateway_ref="va-draft-deleted-0002", allocations=[])
    st, d = api.create("payment", body2)
    check("draft gateway payment created", st == 201, (st, d))
    st, resp = api.call("DELETE", f"/api/v1/r/payment/{api.q(d.get('name', ''))}")
    check("draft gateway payment deleted", st in (200, 204), (st, resp))
    st, d2 = api.create("payment", body2)
    check("the gateway id of a deleted draft can be used again", st == 201, (st, d2))
    st, resp = api.create("payment", body2)
    check("but not twice", st in (409, 422), (st, resp))

    # the WhatsApp print never bills an invoice that is not open
    inv_x = submitted_invoice(700_000, posting_date="2026-01-01", due_date=past)
    st, resp = api.verb("sales_invoice", inv_x, "cancel")
    check("cancel an overdue invoice", st == 200, (st, resp))
    st, page, _ = api.print_html("sales_invoice", inv_x, "sales_invoice_whatsapp")
    check("WhatsApp print of a cancelled invoice: no reminder, no amount, no wa.me link",
          st == 200 and "wa.me" not in page and "mengingatkan" not in page and "700.000" not in page and "dibatalkan" in page, (st, page[:800]))
    st, dr = api.create("sales_invoice", invoice_body(customer, item, rate=650_000))
    st, page, _ = api.print_html("sales_invoice", dr["name"], "sales_invoice_whatsapp")
    check("WhatsApp print of a draft invoice: no bill, no wa.me link",
          st == 200 and "wa.me" not in page and "650.000" not in page and "belum diajukan" in page, (st, page[:800]))

    # 3. mutasi: import the statement
    rows = [("06/11/2026", f"TRSF E-BANKING CR {inv_a} PT CONTOH LUNAS", 2_750_000, 0, 12_750_000),
            ("06/11/2026", "TRSF E-BANKING CR PT CONTOH LUNAS", 1_234_000, 0, 13_984_000),
            ("06/11/2026", "TRSF E-BANKING CR PT CONTOH", 500_000, 0, 14_484_000),
            ("06/11/2026", "BIAYA ADM", 0, 15_000, 14_469_000)]
    csv = statement_csv(rows)
    st, pv = api.upload("/api/v1/imports/bank_statement_csv", {}, "mutasi.csv", csv)
    check("statement preview: 4 rows, no errors", st == 200 and pv["rows"] == 4 and pv["errors"] == [], (st, pv))
    if st != 200:
        return
    st, c = api.call("POST", f"/api/v1/imports/bank_statement_csv/{pv['id']}/commit")
    check("statement commit creates 4 lines", st == 200 and len(c.get("records", [])) == 4, (st, c))
    st, pv2 = api.upload("/api/v1/imports/bank_statement_csv", {}, "mutasi.csv", csv)
    check("the same statement again: duplicate", st == 200 and pv2["duplicate"] is True, (st, pv2))
    more = statement_csv(rows + [("07/11/2026", "SETORAN TUNAI", 100_000, 0, 14_569_000)])
    st, pv3 = api.upload("/api/v1/imports/bank_statement_csv", {}, "mutasi-2.csv", more)
    st, c3 = api.call("POST", f"/api/v1/imports/bank_statement_csv/{pv3['id']}/commit")
    check("an overlapping statement adds only the new line", st == 200 and len(c3.get("records", [])) == 1, (st, c3))
    lines = {l["description"]: l for l in api.find("bank_transaction", "date >= '2026-11-06'")}
    eq("5 statement lines", len(lines), 5)
    la = lines[rows[0][1]]
    lb = lines[rows[1][1]]
    ld = lines[rows[2][1]]
    lf = lines["BIAYA ADM"]
    eq("money out is negative", lf["amount"], -15_000)
    check("line with the invoice number in the note: suggested that invoice",
          la["status"] == "suggested" and la["suggested_invoice"] == inv_a, la)
    check("line with a unique amount: suggested the invoice of that amount",
          lb["status"] == "suggested" and lb["suggested_invoice"] == inv_b, lb)
    check("line whose amount fits two invoices: no suggestion", ld["status"] == "unmatched" and not ld["suggested_invoice"], ld)
    eq("money out: no suggestion", lf["status"], "unmatched")

    # two equal transfers on one day, statement without Saldo: Urutan keeps them apart, so neither is lost
    same = ("06/12/2026", "TRSF CR SAMA", 500_000, 0, "")
    st, pv4 = api.upload("/api/v1/imports/bank_statement_csv", {}, "pagi.csv", statement_csv([same]))
    st, c4 = api.call("POST", f"/api/v1/imports/bank_statement_csv/{pv4['id']}/commit")
    check("morning export without Saldo: 1 line", st == 200 and len(c4.get("records", [])) == 1, (st, c4))
    st, pv5 = api.upload("/api/v1/imports/bank_statement_csv", {}, "sore.csv", statement_csv([same, same]))
    check("evening export with a second equal transfer: no error", st == 200 and pv5["errors"] == [], (st, pv5))
    st, c5 = api.call("POST", f"/api/v1/imports/bank_statement_csv/{pv5['id']}/commit")
    check("evening export adds the second equal transfer", st == 200 and len(c5.get("records", [])) == 1, (st, c5))
    eq("both equal transfers are kept", len(api.find("bank_transaction", "description == 'TRSF CR SAMA'")), 2)
    # a part of a day pasted and numbered from 1 again, with Saldo: no equal transfer is dropped
    q = ("06/12/2026", "QRIS CR SAMA", 50_000, 0)
    st, pv7 = api.upload("/api/v1/imports/bank_statement_csv", {}, "qris-pagi.csv", statement_csv([q + (1_050_000,), q + (1_100_000,)]))
    api.call("POST", f"/api/v1/imports/bank_statement_csv/{pv7['id']}/commit")
    st, pv8 = api.upload("/api/v1/imports/bank_statement_csv", {}, "qris-siang.csv", statement_csv([q + (1_100_000,), q + (1_150_000,), q + (1_200_000,)]))
    st, c8 = api.call("POST", f"/api/v1/imports/bank_statement_csv/{pv8['id']}/commit")
    check("partial paste renumbered from 1, Saldo filled: the two new equal transfers are added, none dropped",
          len(api.find("bank_transaction", "description == 'QRIS CR SAMA'")) >= 4, (st, c8))
    st, pv6 = api.upload("/api/v1/imports/bank_statement_csv", {}, "tanpa-urutan.csv", statement_csv([("07/12/2026", "TRSF CR LAIN", 1_000, 0, "")], seq=False))
    check("a statement without the Urutan column is refused, not deduplicated silently",
          st == 200 and any(e.get("column") == "Urutan" for e in pv6.get("errors", [])), (st, pv6))

    # 4. cocok -> pembayaran -> lunas
    st, resp = api.patch("bank_transaction", la["name"], {"status": "confirmed"})
    check("confirm the suggested line", st == 200, (st, resp))
    la = api.get("bank_transaction", la["name"])
    check("confirming drafts a payment linked to the line (not yet the matched payment)", bool(la.get("draft_payment")) and not la.get("payment"), la)
    p = api.get("payment", la["draft_payment"])
    check("drafted payment: customer, amount, reference = line, one allocation to the invoice",
          p["docstatus"] == 0 and p["customer"] == customer and p["amount"] == 2_750_000 and p["reference"] == la["name"]
          and [(r["sales_invoice"], r["amount"]) for r in p["allocations"]] == [(inv_a, 2_750_000)], p)
    st, resp = api.verb("payment", p["name"], "submit")
    check("submit the drafted payment", st == 200, (st, resp))
    pname = resp.get("name", p["name"])
    inv = api.get("sales_invoice", inv_a)
    check("invoice A lunas: outstanding 0, status paid", inv["outstanding"] == 0 and inv["status"] == "paid", inv)
    la = api.get("bank_transaction", la["name"])
    check("line matched to the submitted payment", la["status"] == "matched" and la["payment"] == pname, la)
    st, resp = api.patch("bank_transaction", la["name"], {"status": "unmatched"})
    check("a matched line cannot be set back by hand", st == 422, (st, resp))

    # another payment that only names the line in its reference does not take the line over
    st, other = api.create("payment", {"direction": "receive", "customer": customer, "amount": 100_000, "reference": la["name"],
                                       "allocations": [{"sales_invoice": inv_d, "amount": 100_000}]})
    st, resp = api.verb("payment", other.get("name", ""), "submit")
    check("other payment with reference = the line submitted", st == 200, (st, resp))
    other_name = resp.get("name", "")
    la = api.get("bank_transaction", la["name"])
    check("the line stays matched to its own payment", la["status"] == "matched" and la["payment"] == pname, la)
    st, resp = api.verb("payment", other_name, "cancel")
    la = api.get("bank_transaction", la["name"])
    check("cancelling the other payment leaves the line matched", st == 200 and la["status"] == "matched" and la["payment"] == pname, (st, la))
    st, page, _ = api.print_html("sales_invoice", inv_a, "sales_invoice_whatsapp")
    check("WhatsApp print of a paid invoice thanks the customer", st == 200 and "sudah lunas" in page, page[:400])

    # the owner picks the invoice for the ambiguous line
    st, resp = api.patch("bank_transaction", ld["name"], {"status": "confirmed"})
    check("confirm without an invoice -> 422", st == 422, (st, resp))
    st, resp = api.patch("bank_transaction", ld["name"], {"suggested_invoice": inv_e, "status": "confirmed"})
    ld = api.get("bank_transaction", ld["name"])
    check("owner picks invoice E and confirms", st == 200 and bool(ld.get("draft_payment")), (st, ld))
    st, resp = api.patch("bank_transaction", ld["name"], {"status": "matched"})
    check("a line cannot be set to Cocok by hand while its payment is a draft", st == 422, (st, resp))
    # the drafted payment is deleted: the line is not stuck, confirming again drafts a new one
    st, resp = api.call("DELETE", f"/api/v1/r/payment/{api.q(ld['draft_payment'])}")
    check("delete the drafted payment", st in (200, 204), (st, resp))
    st, resp = api.patch("bank_transaction", ld["name"], {"status": "suggested"})
    ld2 = api.get("bank_transaction", ld["name"])
    check("after the draft is deleted the line can go back", st == 200 and ld2["status"] == "suggested", (st, ld2))
    st, resp = api.patch("bank_transaction", ld["name"], {"status": "confirmed"})
    ld2 = api.get("bank_transaction", ld["name"])
    check("confirming again drafts a new payment", st == 200 and ld2.get("draft_payment") not in ("", None, ld["draft_payment"]), (st, ld2))
    st, resp = api.verb("payment", ld2.get("draft_payment", ""), "submit")
    ld2 = api.get("bank_transaction", ld["name"])
    check("submitting it matches the line", st == 200 and ld2["status"] == "matched" and ld2["payment"] == resp.get("name"), (st, ld2))
    st, resp = api.patch("bank_transaction", lf["name"], {"suggested_invoice": inv_d, "status": "confirmed"})
    check("money out cannot be confirmed against a sales invoice", st == 422, (st, resp))

    # cancel the payment: the line goes back to unmatched, the invoice is owed again
    st, resp = api.verb("payment", pname, "cancel")
    check("cancel the payment of line A", st == 200, (st, resp))
    la = api.get("bank_transaction", la["name"])
    check("line A back to unmatched, payment cleared", la["status"] == "unmatched" and not la.get("payment"), la)
    eq("invoice A owed again", api.get("sales_invoice", inv_a)["outstanding"], 2_750_000)
    # the cancelled payment is amended and submitted: the line follows it (amended_from), so it is not confirmed again
    st, am = api.verb("payment", pname, "amend")
    st, resp = api.verb("payment", am.get("name", ""), "submit")
    la = api.get("bank_transaction", la["name"])
    check("the amended payment of line A matches line A again", st == 200 and la["status"] == "matched" and la["payment"] == resp.get("name"), (st, la))
    eq("invoice A paid once", api.get("sales_invoice", inv_a)["outstanding"], 0)

    # going back and forth never leaves a second draft: one line, one payment
    inv_o, inv_o2 = submitted_invoice(1_000_001), submitted_invoice(1_000_002)
    st, lo = api.create("bank_transaction", {"account": "1110000001", "date": "2026-11-08", "seq": 1, "description": "TRSF CR BOLAK BALIK", "credit": 400_000})
    check("statement line for the back-and-forth case", st == 201, (st, lo))
    api.patch("bank_transaction", lo["name"], {"suggested_invoice": inv_o, "status": "confirmed"})
    d1 = api.get("bank_transaction", lo["name"]).get("draft_payment")
    api.patch("bank_transaction", lo["name"], {"status": "suggested"})
    api.patch("bank_transaction", lo["name"], {"suggested_invoice": inv_o2, "status": "confirmed"})
    lo = api.get("bank_transaction", lo["name"])
    drafts = api.find("payment", f"reference == '{lo['name']}' && docstatus == 0")
    check("confirm, back, confirm again with another invoice: the same single draft", bool(d1) and lo["draft_payment"] == d1 and len(drafts) == 1, (d1, lo, drafts))
    p1 = api.get("payment", d1)
    check("the draft follows the invoice chosen last", [r["sales_invoice"] for r in p1.get("allocations", [])] == [inv_o2], p1)
    api.patch("bank_transaction", lo["name"], {"status": "ignored"})
    st, resp = api.verb("payment", d1, "submit")
    lo = api.get("bank_transaction", lo["name"])
    check("its draft submitted while the line is set aside still matches the line (no second payment possible)",
          st == 200 and lo["status"] == "matched" and lo["payment"] == resp.get("name"), (st, lo))
    eq("invoices: the first untouched, the last paid 400.000", (api.get("sales_invoice", inv_o)["outstanding"], api.get("sales_invoice", inv_o2)["outstanding"]), (1_000_001, 600_002))

    # The invariant, whatever the path: one statement line, at most one live payment (bank_transaction_claim).
    def one_live(label, line_name, invoices, amount):
        live = api.find("payment", f"bank_transaction == '{line_name}' && docstatus == 1")
        paid = sum(base - api.get("sales_invoice", i)["outstanding"] for i, base in invoices)
        ln = api.get("bank_transaction", line_name)
        check(f"{label}: one live payment for the line, the transfer paid once, the line matched to it",
              len(live) == 1 and paid == amount and ln["status"] == "matched" and ln["payment"] == live[0]["name"], (live, paid, ln))

    def new_line(desc, seq, amount=300_000):
        st, ln = api.create("bank_transaction", {"account": "1110000001", "date": "2026-11-09", "seq": seq, "description": desc, "credit": amount})
        return ln["name"]

    def confirm(line_name, invoice):
        api.patch("bank_transaction", line_name, {"suggested_invoice": invoice, "status": "confirmed"})
        return api.get("bank_transaction", line_name)["draft_payment"]

    def submit(name):
        st, r = api.verb("payment", name, "submit")
        return st, (r.get("name", "") if isinstance(r, dict) else "")

    # N1: paid to A, cancelled, confirmed to B, the cancelled payment amended; both submitted: the second is refused
    ia, ib = submitted_invoice(1_000_003), submitted_invoice(1_000_004)
    ln = new_line("TRSF CR N1", 1)
    st, p0 = submit(confirm(ln, ia))
    st, resp = api.verb("payment", p0, "cancel")
    check("N1: cancel the payment of the line", st == 200 and api.get("bank_transaction", ln)["status"] == "unmatched", (st, resp))
    d2 = confirm(ln, ib)
    st, am = api.verb("payment", p0, "amend")
    check("N1: the amend keeps the line", am.get("bank_transaction") == ln, am)
    st1, _ = submit(am.get("name", ""))
    st2, _ = submit(d2)
    check("N1: of the amend and the new draft, one submits and the other is refused (422)", sorted((st1, st2)) == [200, 422], (st1, st2))
    one_live("N1", ln, [(ia, 1_000_003), (ib, 1_000_004)], 300_000)

    # H2 / H3: a chain (paid, cancelled, confirmed again, paid, cancelled), then both cancelled payments amended,
    # or one amended and the line confirmed again
    for label, amend_both in (("H2", True), ("H3", False)):
        ia, ib = submitted_invoice(1_000_010 + amend_both), submitted_invoice(1_000_020 + amend_both)
        ln = new_line(f"TRSF CR {label}", 2 + amend_both)
        st, p0 = submit(confirm(ln, ia))
        api.verb("payment", p0, "cancel")
        st, p2 = submit(confirm(ln, ib))
        api.verb("payment", p2, "cancel")
        st, a0 = api.verb("payment", p0, "amend")
        st1, _ = submit(a0.get("name", ""))
        if amend_both:
            st, a2 = api.verb("payment", p2, "amend")
            st2, _ = submit(a2.get("name", ""))
        else:   # the line is matched by the amend already: confirming it again is refused at the line
            st2, _ = api.patch("bank_transaction", ln, {"suggested_invoice": ib, "status": "confirmed"})
            if st2 == 200:
                st2, _ = submit(api.get("bank_transaction", ln)["draft_payment"])
        check(f"{label}: the first submit goes through, the second is refused", (st1, st2) == (200, 422), (st1, st2))
        one_live(label, ln, [(ia, 1_000_010 + amend_both), (ib, 1_000_020 + amend_both)], 300_000)

    # r4: paid to A, cancelled, confirmed to B (draft D2), the cancelled payment amended, confirmed to C
    ia, ib, ic = submitted_invoice(1_000_031), submitted_invoice(1_000_032), submitted_invoice(1_000_033)
    ln = new_line("TRSF CR R4", 4)
    st, p0 = submit(confirm(ln, ia))
    api.verb("payment", p0, "cancel")
    d2 = confirm(ln, ib)
    st, am = api.verb("payment", p0, "amend")
    dc = confirm(ln, ic)
    sts = [submit(n)[0] for n in {d2, am.get("name", ""), dc}]
    check("r4: of all drafts of the line exactly one submits", sts.count(200) == 1 and all(x in (200, 409, 422) for x in sts), sts)
    one_live("r4", ln, [(ia, 1_000_031), (ib, 1_000_032), (ic, 1_000_033)], 300_000)

    # two drafts of one line submitted at the same time: the unique claim lets exactly one through
    import threading
    ia, ib = submitted_invoice(1_000_041), submitted_invoice(1_000_042)
    ln = new_line("TRSF CR SERENTAK", 5)
    drafts = []
    for inv_ in (ia, ib):
        st, dp = api.create("payment", {"direction": "receive", "customer": customer, "amount": 300_000, "reference": ln, "bank_transaction": ln,
                                        "allocations": [{"sales_invoice": inv_, "amount": 300_000}]})
        drafts.append(dp.get("name", ""))
    api.patch("bank_transaction", ln, {"suggested_invoice": ia, "status": "confirmed"})   # reuses one of them
    results = []
    threads = [threading.Thread(target=lambda n=n: results.append(submit(n)[0])) for n in drafts]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("concurrent submits of two drafts of one line: one 200, one refused", sorted(results)[0] == 200 and sorted(results)[1] in (409, 422, 500) and len(results) == 2, results)
    check("concurrent submits: never 500", 500 not in results, results)
    one_live("concurrent", ln, [(ia, 1_000_041), (ib, 1_000_042)], 300_000)

    # a payment that names a matched line is refused, so the line cannot be taken over through bank_transaction either
    st, intr = api.create("payment", {"direction": "receive", "customer": customer, "amount": 1_000, "bank_transaction": ln,
                                      "allocations": [{"sales_invoice": inv_d, "amount": 1_000}]})
    st, resp = api.verb("payment", intr.get("name", ""), "submit")
    check("a second payment naming a matched line is refused (422)", st == 422, (st, resp))

    # only the line's own draft (or the amend of its payment) may claim it, and the link cannot be edited
    ia = submitted_invoice(1_000_051)
    ln = new_line("TRSF CR A1", 6, 400_000)
    st, fp = api.create("payment", {"direction": "receive", "customer": customer, "amount": 1, "bank_transaction": ln,
                                    "allocations": [{"sales_invoice": ia, "amount": 1}]})
    st, resp = api.verb("payment", fp.get("name", ""), "submit")
    check("A1: a payment that is not the line's draft cannot claim the line", st == 422 and api.get("bank_transaction", ln)["status"] != "matched", (st, resp))
    d1 = confirm(ln, ia)
    st, resp = api.patch("payment", d1, {"bank_transaction": ""})
    check("A2: the line of a draft from the statement cannot be cleared", st == 422, (st, resp))
    ln2 = new_line("TRSF CR A3", 7, 250_000)
    st, resp = api.patch("payment", d1, {"bank_transaction": ln2})
    check("A3: nor moved to another line", st == 422, (st, resp))
    st, resp = api.patch("payment", d1, {"amount": 1, "allocations": [{"sales_invoice": ia, "amount": 1}]})
    st, resp = api.verb("payment", d1, "submit")
    check("N4: the line's draft with an amount other than the transfer is refused", st == 422, (st, resp))
    st, resp = api.patch("payment", d1, {"amount": 400_000, "allocations": [{"sales_invoice": ia, "amount": 400_000}]})
    st, resp = api.verb("payment", d1, "submit")
    check("A2/A3/N4: the untouched draft submits and matches", st == 200 and api.get("bank_transaction", ln)["status"] == "matched", (st, resp))
    vend = api.list("vendor")
    st, pp = api.create("payment", {"direction": "pay", "vendor": vend[0]["name"], "amount": 10, "bank_transaction": ln2})
    st, resp = api.verb("payment", pp.get("name", ""), "submit")
    claims = api.find("bank_transaction_claim", f"bank_transaction == '{ln2}'")
    check("A4: a vendor payment never claims a statement line", not claims, (st, resp, claims))
    ib = submitted_invoice(1_000_052)
    st, resp = api.patch("bank_transaction", ln2, {"suggested_invoice": ib, "status": "confirmed"})
    st, resp = api.verb("payment", api.get("bank_transaction", ln2)["draft_payment"], "submit")
    check("A4: nor keeps the line from being confirmed and paid", st == 200 and api.get("bank_transaction", ln2)["status"] == "matched", (st, resp))
    st, resp = CTX["fm"].call("DELETE", f"/api/v1/r/bank_transaction/{api.q(ln)}") if CTX.get("fm") else (403, None)
    check("S9: a matched statement line cannot be deleted", st in (403, 409, 422) and api.get("bank_transaction", ln) is not None, (st, resp))

def setup_demo_jasa(api, k):
    """One member per jasa role in PT Contoh Inspeksi (rangka user add), signed in: the owner self-approves, so only these can show a held approval."""
    CTX.update(kernel=k, org=k.orgs[0][0])
    pw = secrets.token_urlsafe(18)   # throwaway password of a throwaway database
    people = [("sm", "dewi@contohjasa.test", "sales_manager", "USR-001", "Dewi Contoh"),
              ("fm", "hendra@contohjasa.test", "finance_manager", "USR-002", "Hendra Contoh"),
              ("pm", "lina@contohjasa.test", "purchasing_manager", "USR-003", "Lina Contoh"),
              ("pa", "agus@contohjasa.test", "project_admin", "USR-004", "Agus Contoh")]
    for key, email, role, rec, name in people:
        code, out = k.user_add(email, role, CTX["org"], name, record=rec, password=pw)
        cl = Api.login(k.base, email, pw, org=CTX["org"]) if code == 0 else None
        if cl is None:
            CTX["login_error"] = f"{email}: " + (out if code else "login refused")
            fail("setup: add and sign in the members", CTX["login_error"])
            return
        CTX[key] = cl


def decide(cl, approval_id, verb="approve", reason=""):
    return cl.call("POST", f"/api/v1/approvals/{approval_id}/{verb}", {"reason": reason} if verb == "reject" else {})


def scenario_billing_milestones(api):
    """Tahap 2 J2: a consultancy's billing schedule. Milestones by percent of the contract, one by date and two after an
    acceptance report; the report makes the next one ready; an invoice that bills it marks it invoiced; cancel reopens it."""
    pa, fm = CTX.get("pa"), CTX.get("fm")
    if pa is None or fm is None:
        fail("billing milestones: members", CTX.get("login_error", "no project_admin or finance_manager"))
        return
    customer, ndt = "CUST-0001", services(api)[0]["name"]
    st, prj = pa.create("project", {"project_name": "Pendampingan pajak tahunan", "customer": customer, "billing_model": "milestone",
                                    "contract_kind": "spk", "contract_no": "SPK/007/XI/2026", "contract_value": 100_000_000})
    if not check("project_admin creates a 100.000.000 milestone engagement", st == 201, (st, prj)):
        return
    p = prj["name"]
    ms = {}
    for seq, title, kind, pct, trig, due in ((1, "DP 30%", "down_payment", 30, "date", "2026-11-10"), (2, "Termin 2 setelah laporan", "milestone", 40, "acceptance", ""),
                                             (3, "Pelunasan setelah BA akhir", "milestone", 30, "acceptance", "")):
        body = {"project": p, "seq": seq, "title": title, "kind": kind, "basis": "percent", "percent": pct, "trigger": trig}
        if due:
            body["due_date"] = due
        st, m = pa.create("billing_milestone", body)
        check(f"milestone {seq} '{title}' created", st == 201, (st, m))
        ms[seq] = m
    eq("milestone amounts from the contract value: 30.000.000, 40.000.000, 30.000.000", [ms[i].get("amount") for i in (1, 2, 3)], [30_000_000, 40_000_000, 30_000_000])
    eq("milestones start planned, customer from the project", [(ms[i].get("state"), ms[i].get("customer")) for i in (1, 2, 3)], [("planned", customer)] * 3)
    st, resp = pa.create("billing_milestone", {"project": p, "title": "x", "basis": "percent", "percent": 120, "trigger": "acceptance"})
    check("percent 120 -> 422 'Persen termin'", st == 422 and any("Persen termin" in m for m in messages(resp)), (st, resp))
    st, resp = pa.create("billing_milestone", {"project": p, "title": "x", "basis": "amount", "amount_fixed": 1_000_000, "trigger": "date"})
    check("a date milestone without due date -> 422", st == 422 and any("jatuh tempo" in m for m in messages(resp)), (st, resp))
    st, resp = pa.call("PATCH", f"/api/v1/r/billing_milestone/{Api.q(ms[2]['name'])}", {"state": "invoiced"})
    eq("state is read_only for people: a PATCH leaves it planned", api.get("billing_milestone", ms[2]["name"])["state"], "planned")

    # the acceptance report makes the next acceptance milestone (lowest seq) ready
    st, ba = pa.create("acceptance", {"project": p, "accepted_on": "2026-11-20", "accepted_by": "Bu Rina (klien)", "scope": "Laporan pendahuluan"})
    ban = submit_doc(pa, "acceptance", ba["name"], "project_admin submits the acceptance report") if st == 201 else None
    m2, m3 = api.get("billing_milestone", ms[2]["name"]), api.get("billing_milestone", ms[3]["name"])
    eq("after the report: milestone 2 ready with the report, milestone 3 still planned", (m2["state"], m2["acceptance"], m3["state"]), ("ready", ban, "planned"))

    # finance bills it: customer, project and report default from the milestone
    body = {"billing_milestone": ms[2]["name"], "items": [{"item": ndt, "qty": 1, "rate": 40_000_000, "description": "Termin 2: laporan pendahuluan"}]}
    st, inv = fm.create("sales_invoice", body)
    if not check("finance creates the invoice from the milestone", st == 201, (st, inv)):
        return
    eq("invoice defaults from the milestone: customer, project, acceptance", (inv["customer"], inv["project"], inv["acceptance"]), (customer, p, ban))
    invn = submit_doc(fm, "sales_invoice", inv["name"], "finance submits the milestone invoice", force=True)
    m2 = api.get("billing_milestone", ms[2]["name"])
    eq("submitted invoice marks milestone 2 invoiced and links it", (m2["state"], m2["sales_invoice"]), ("invoiced", invn))
    st, resp = fm.create("sales_invoice", body)
    check("a second invoice for the invoiced milestone -> 422 (one invoice per milestone)", st == 422 and any("sudah ditagih" in m or "Faktur untuk termin ini" in m for m in messages(resp)), (st, resp))
    st, other = pa.create("project", {"project_name": "Proyek lain", "customer": customer})
    st, resp = fm.create("sales_invoice", dict(body, billing_milestone=ms[3]["name"], project=other["name"]))
    check("a milestone of another project -> 422 'Termin harus milik proyek yang sama'", st == 422 and any("proyek yang sama" in m for m in messages(resp)), (st, resp))
    st, resp = fm.verb("sales_invoice", invn, "cancel")
    check("finance cancels the milestone invoice", st == 200, (st, resp))
    m2 = api.get("billing_milestone", ms[2]["name"])
    eq("cancel reopens milestone 2: ready, no invoice", (m2["state"], m2["sales_invoice"]), ("ready", ""))
    st, resp = fm.create("sales_invoice", dict(body, items=[{"item": ndt, "qty": 1, "rate": 45_000_000}]))
    check("an invoice above the milestone (45.000.000 > 40.000.000) -> 422 'melebihi nilai termin'", st == 422 and any("melebihi nilai termin" in m for m in messages(resp)), (st, resp))
    st, resp = fm.create("sales_invoice", dict(body, customer="CUST-0002"))
    check("another customer than the milestone's -> 422", st == 422, (st, resp))
    # the report is cancelled and amended: the amended report takes over milestone 2, milestone 3 stays planned
    st, resp = pa.verb("acceptance", ban, "cancel")
    check("project admin cancels the acceptance report", st == 200, (st, resp))
    m2 = api.get("billing_milestone", ms[2]["name"])
    eq("cancelled report gives milestone 2 back: planned (it keeps the cancelled report, for the amendment)", (m2["state"], m2["acceptance"]), ("planned", ban))
    st, am = pa.verb("acceptance", ban, "amend")
    amn = (am or {}).get("name") or ((am or {}).get("open") or {}).get("name")
    if check("amend the report", st in (200, 201) and amn, (st, am)):
        ban2 = submit_doc(pa, "acceptance", amn, "submit the amended report")
        m2, m3 = api.get("billing_milestone", ms[2]["name"]), api.get("billing_milestone", ms[3]["name"])
        eq("amended report: milestone 2 ready again with it, milestone 3 still planned", (m2["state"], m2["acceptance"], m3["state"]), ("ready", ban2, "planned"))
        # the audit case: milestone 2 is invoiced, then its report is cancelled and amended -> milestone 3 must not move
        st, am2 = fm.verb("sales_invoice", invn, "amend")
        am2n = (am2 or {}).get("name") or ((am2 or {}).get("open") or {}).get("name")
        st, resp = fm.create("sales_invoice", body)
        check("billing milestone 2 again with a new invoice (not an amendment of the cancelled one) -> 422", st == 422, (st, resp))
        inv2n = submit_doc(fm, "sales_invoice", am2n, "finance bills milestone 2 again by amending the cancelled invoice", force=True) if am2n else None
        st, resp = pa.verb("acceptance", ban2, "cancel")
        m2 = api.get("billing_milestone", ms[2]["name"])
        eq("cancelling the report of an invoiced milestone keeps it invoiced", (m2["state"], m2["sales_invoice"]), ("invoiced", inv2n))
        st, am = pa.verb("acceptance", ban2, "amend")
        amn = (am or {}).get("name") or ((am or {}).get("open") or {}).get("name")
        ban3 = submit_doc(pa, "acceptance", amn, "submit the second amendment") if amn else None
        m2, m3 = api.get("billing_milestone", ms[2]["name"]), api.get("billing_milestone", ms[3]["name"])
        eq("second amendment takes over invoiced milestone 2; milestone 3 stays planned", (m2["state"], m2["acceptance"], m3["state"]), ("invoiced", ban3, "planned"))

    # the schedule never exceeds the contract: 30+40+30 = 100% is full, a 4th milestone or a lower contract is refused
    st, resp = pa.create("billing_milestone", {"project": p, "seq": 4, "title": "Tambahan", "basis": "percent", "percent": 30, "trigger": "acceptance"})
    check("a 4th milestone of 30% (130% of the contract) -> 422", st == 422, (st, resp))
    st, resp = pa.create("billing_milestone", {"project": p, "seq": 5, "title": "Besar", "basis": "amount", "amount_fixed": 500_000_000, "trigger": "acceptance"})
    check("a 500.000.000 milestone on a 100.000.000 contract -> 422", st == 422, (st, resp))
    st, resp = pa.call("PATCH", f"/api/v1/r/project/{Api.q(p)}", {"contract_value": 50_000_000})
    check("lowering the contract below its schedule (100.000.000) -> 422", st == 422, (st, resp))
    st, resp = pa.call("DELETE", f"/api/v1/r/billing_milestone/{Api.q(ms[2]['name'])}")
    check("a milestone cannot be deleted by project admin (403)", st == 403, (st, resp))

    # two amendments of one cancelled report: only one takes over; the second moves nothing forward
    st, ba7 = pa.create("acceptance", {"project": p, "accepted_on": "2026-11-25", "accepted_by": "Bu Rina (klien)", "scope": "Laporan akhir"})
    ba7n = submit_doc(pa, "acceptance", ba7["name"], "a report for the final milestone") if st == 201 else None
    m3 = api.get("billing_milestone", ms[3]["name"])
    eq("the new report makes milestone 3 ready", (m3["state"], m3["acceptance"]), ("ready", ba7n))
    pa.verb("acceptance", ba7n, "cancel")
    amends = []
    for _ in range(2):
        st, am = pa.verb("acceptance", ba7n, "amend")
        amends.append((am or {}).get("name") or ((am or {}).get("open") or {}).get("name"))
    for n in amends:
        if n:
            pa.verb("acceptance", n, "submit")
    m3 = api.get("billing_milestone", ms[3]["name"])
    others = [m for m in api.find("billing_milestone", "project == '%s'" % p) if m["state"] == "ready" and m["name"] != ms[3]["name"]]
    eq("two amendments: milestone 3 ready once, no other milestone moved", (m3["state"], [m["name"] for m in others if m["name"] != ms[2]["name"]]), ("ready", []))

    # a cancelled report and a cancelled invoice: the milestone goes back to planned, not ready, and cannot be billed
    st, inv3 = fm.create("sales_invoice", {"billing_milestone": ms[3]["name"], "items": [{"item": ndt, "qty": 1, "rate": 30_000_000}]})
    inv3n = submit_doc(fm, "sales_invoice", inv3["name"], "finance bills milestone 3", force=True) if st == 201 else None
    live = api.get("billing_milestone", ms[3]["name"])["acceptance"]
    pa.verb("acceptance", live, "cancel")
    if inv3n:
        fm.verb("sales_invoice", inv3n, "cancel")
    m3 = api.get("billing_milestone", ms[3]["name"])
    eq("report and invoice cancelled: milestone 3 planned (its report is not live)", m3["state"], "planned")
    st, resp = fm.create("sales_invoice", {"billing_milestone": ms[3]["name"], "items": [{"item": ndt, "qty": 1, "rate": 30_000_000}]})
    check("billing an acceptance milestone that is not ready -> 422", st == 422 and any("siap ditagih" in m for m in messages(resp)), (st, resp))

    # monthly retainers from a sheet (a retainer engagement of its own: 3 x 15.000.000)
    st, rp = pa.create("project", {"project_name": "Retainer pajak bulanan", "customer": customer, "billing_model": "retainer", "contract_value": 45_000_000})
    p = rp["name"]
    sheet = ("Proyek,Urutan,Judul,Jenis,Nominal,Jatuh tempo,Periode dari,Periode sampai\n"
             f"{p},11,Retainer November,retainer,15000000,30/11/2026,01/11/2026,30/11/2026\n"
             f"{p},12,Retainer Desember,retainer,15000000,31/12/2026,01/12/2026,31/12/2026\n"
             f"{p},13,Retainer Januari,retainer,15000000,31/01/2027,01/01/2027,31/01/2027\n").encode()
    st, pv = pa.upload("/api/v1/imports/billing_milestone", {}, "retainer.csv", sheet)
    if check("retainer sheet: preview 3 rows, no errors", st == 200 and pv.get("rows") == 3 and pv.get("errors") == [], (st, pv)):
        st, c = pa.call("POST", f"/api/v1/imports/billing_milestone/{pv['id']}/commit")
        check("retainer sheet committed: 3 milestones", st == 200 and len(c.get("records", [])) == 3, (st, c))
        rs = api.find("billing_milestone", "project == '%s' && kind == 'retainer'" % p)
        eq("retainers: 3 x 15.000.000, planned, due 30/11, 31/12, 31/01", sorted((r["due_date"], r["amount"], r["state"]) for r in rs),
           [("2026-11-30", 15_000_000, "planned"), ("2026-12-31", 15_000_000, "planned"), ("2027-01-31", 15_000_000, "planned")])


def scenario_project_costs(api):
    """Tahap 2 J4: project expenses (re-billed with markup, receipt required) and the project carried from purchase
    request to purchase order (make_po) to vendor bill."""
    customer, ndt = "CUST-0001", services(api)[0]["name"]
    st, prj = api.create("project", {"project_name": "Audit internal 2026", "customer": customer, "contract_value": 50_000_000})
    if not check("project for the cost checks", st == 201, (st, prj)):
        return
    p = prj["name"]
    body = {"project": p, "category": "lodging", "description": "Hotel tim audit 2 malam", "amount": 2_000_000, "reimbursable": True, "markup_percent": 10}
    st, resp = api.create("project_expense", body)
    check("a re-billed expense without its receipt -> 422 'wajib punya bukti'", st == 422 and any("wajib punya bukti" in m for m in messages(resp)), (st, resp))
    st, ex = api.create("project_expense", dict(body, receipt="nota-hotel.pdf"))
    if check("re-billed expense with its receipt created", st == 201, (st, ex)):
        eq("2.000.000 with 10% markup is billed 2.200.000", ex["rebill_amount"], 2_200_000)
        submit_doc(api, "project_expense", ex["name"], "submit the expense")
    st, own = api.create("project_expense", {"project": p, "category": "travel", "description": "Bensin", "amount": 300_000})
    eq("an expense not billed on: rebill_amount 0", own.get("rebill_amount"), 0)
    # the re-billed line stays out of PPh 23 (pph23_exclude)
    st, inv = api.create("sales_invoice", {"customer": customer, "project": p, "pph23_applies": True, "items": [
        {"item": ndt, "qty": 1, "rate": 20_000_000, "description": "Jasa audit tahap 1"},
        {"item": ndt, "qty": 1, "rate": 2_200_000, "description": "Penggantian biaya hotel", "pph23_exclude": True}]})
    if check("invoice with the fee and the re-billed hotel", st == 201, (st, inv)):
        eq("PPh 23 base excludes the re-billed 2.200.000: base 20.000.000, PPh 23 400.000", (inv["pph23_dpp"], inv["pph23_amount"]), (20_000_000, 400_000))
        st, upd = api.call("PATCH", f"/api/v1/r/project_expense/{Api.q(own['name'])}", {"rebill_invoice": inv["name"]})
        check("an expense links the invoice of its own project", st == 200, (st, upd))
    st, other = api.create("project", {"project_name": "Proyek lain biaya", "customer": customer})
    if inv.get("name"):
        st, resp = api.create("project_expense", {"project": other["name"], "description": "x", "amount": 1, "rebill_invoice": inv["name"]})
        check("an invoice of another project -> 422", st == 422 and any("proyek yang sama" in m for m in messages(resp)), (st, resp))
    # subcontract: the project travels from the request to the PO (make_po) to the vendor bill
    st, pr = api.create("purchase_request", {"needed_by": "2026-12-01", "project": p, "purpose": "Auditor lepas", "items": [{"item": "BRG-0004", "qty": 1}]})
    if not check("purchase request for the project", st == 201, (st, pr)):
        return
    prn = submit_doc(api, "purchase_request", pr["name"], "submit the project's purchase request")
    st, resp = api.action("purchase_request", prn, "make_po", {"vendor": "VND-0001"})
    if check("make_po from the project's request", st == 200, (st, resp)):
        po = api.get("purchase_order", resp["open"]["name"])
        eq("the PO made by make_po carries the project", po["project"], p)
        api.call("PATCH", f"/api/v1/r/purchase_order/{Api.q(po['name'])}", {"items": [dict(po["items"][0], rate=25_000_000)]})
        pon = submit_doc(api, "purchase_order", po["name"], "submit the project's PO")
        st, vb = api.create("vendor_bill", {"vendor": "VND-0001", "purchase_order": pon, "bill_no": "INV-SUB-01", "bill_date": "2026-11-20",
                                            "items": [{"item": "BRG-0004", "qty": 1, "rate": 25_000_000}]})
        if check("vendor bill from the PO", st == 201, (st, vb)):
            eq("the vendor bill carries the project from the PO", vb["project"], p)
    st, resp = api.create("purchase_request", {"needed_by": "2026-12-01", "project": p, "items": [{"item": "BRG-0004", "qty": 1}]})
    api.call("PATCH", f"/api/v1/r/project/{Api.q(p)}", {"state": "closed"})
    st, resp = api.create("purchase_request", {"needed_by": "2026-12-01", "project": p, "items": [{"item": "BRG-0004", "qty": 1}]})
    check("a closed project takes no new purchase -> 422", st == 422 and any("sudah ditutup" in m for m in messages(resp)), (st, resp))
def scenario_milestone_money(api):
    """Tahap 2 J2, audit round 3: whatever is done to the schedule, the invoices billed against a project's milestones never
    exceed its contract, a milestone has one live invoice, and a milestone that is ready or billed cannot be changed."""
    import threading
    pa, fm = CTX.get("pa"), CTX.get("fm")
    if pa is None or fm is None:
        fail("milestone money: members", CTX.get("login_error", "members missing"))
        return
    customer, ndt = "CUST-0001", services(api)[0]["name"]
    def project(label, cv=100_000_000, model="milestone"):
        st, p = pa.create("project", {"project_name": label, "customer": customer, "billing_model": model, "contract_value": cv})
        return p["name"]
    def ms(p, seq, pct=None, amount=None, trig="acceptance", **kw):
        b = dict({"project": p, "seq": seq, "title": f"T{seq}", "trigger": trig}, **kw)
        b.update({"basis": "percent", "percent": pct} if pct is not None else {"basis": "amount", "amount_fixed": amount})
        return pa.create("billing_milestone", b)
    def ba(p):
        st, b = pa.create("acceptance", {"project": p, "accepted_on": "2026-11-20", "accepted_by": "PIC", "scope": "x"})
        return pa.verb("acceptance", b["name"], "submit")[1].get("name")
    def invoice(m, amt):
        st, inv = fm.create("sales_invoice", {"billing_milestone": m, "items": [{"item": ndt, "qty": 1, "rate": amt}]})
        if st != 201:
            return st
        return fm.verb("sales_invoice", inv["name"], "submit", {"force": True})[0]
    def billed(p):
        return sum(i["dpp"] for i in api.find("sales_invoice", "project == '%s' && docstatus == 1" % p))
    def bill_all(p):
        for m in sorted(api.find("billing_milestone", "project == '%s' && state == 'ready'" % p), key=lambda m: m["seq"]):
            invoice(m["name"], m["amount"])
    # N1: raising the contract does not move the amounts of existing percent milestones (flows re-save them)
    p = project("Uang N1")
    m1 = ms(p, 1, 50)[1]; ms(p, 2, 50)
    pa.call("PATCH", f"/api/v1/r/project/{Api.q(p)}", {"contract_value": 200_000_000})
    ms(p, 3, 50)
    for _ in range(3):
        ba(p)
    eq("N1: a 50% milestone made on a 100.000.000 contract stays 50.000.000 after the contract is raised and its report is in",
       api.get("billing_milestone", m1["name"])["amount"], 50_000_000)
    bill_all(p)
    check("N1: billed on the project <= its contract", billed(p) <= api.get("project", p)["contract_value"], (billed(p), api.get("project", p)["contract_value"]))
    # N1b: a percent milestone needs a contract value
    p = project("Uang N1b", cv=0)
    st, resp = ms(p, 1, 30)
    check("N1b: a percent milestone on a project without contract value -> 422", st == 422, (st, resp))
    # N2: a billed milestone cannot be shrunk to make room
    p = project("Uang N2")
    m1 = ms(p, 1, 40)[1]; ms(p, 2, 60); ba(p)
    invoice(m1["name"], 40_000_000)
    st, resp = pa.call("PATCH", f"/api/v1/r/billing_milestone/{Api.q(m1['name'])}", {"percent": 10})
    check("N2: shrinking a billed milestone -> 422", st == 422, (st, resp))
    ms(p, 3, 30); ba(p); ba(p); bill_all(p)
    check("N2: billed on the project <= its contract", billed(p) <= 100_000_000, billed(p))
    # N3: a billed milestone cannot be moved to another project
    p = project("Uang N3"); pz = project("Uang N3 tujuan", cv=0)
    m1 = ms(p, 1, 60)[1]; ms(p, 2, 40); ba(p)
    invoice(m1["name"], 60_000_000)
    st, resp = pa.call("PATCH", f"/api/v1/r/billing_milestone/{Api.q(m1['name'])}", {"project": pz})
    check("N3: moving a billed milestone to another project -> 422", st == 422, (st, resp))
    ms(p, 3, 60); ba(p); ba(p); bill_all(p)
    check("N3: billed on the project <= its contract", billed(p) <= 100_000_000, billed(p))
    # a ready milestone is locked too
    p = project("Uang siap")
    m1 = ms(p, 1, 50)[1]; ba(p)
    st, resp = pa.call("PATCH", f"/api/v1/r/billing_milestone/{Api.q(m1['name'])}", {"percent": 80})
    check("a ready milestone cannot be changed -> 422", st == 422, (st, resp))
    # two invoices for one milestone made and submitted in parallel: exactly one goes through
    out = []
    def mk():
        st, inv = fm.create("sales_invoice", {"billing_milestone": m1["name"], "items": [{"item": ndt, "qty": 1, "rate": 50_000_000}]})
        out.append(fm.verb("sales_invoice", inv["name"], "submit", {"force": True})[0] if st == 201 else st)
    th = [threading.Thread(target=mk) for _ in range(2)]
    [t.start() for t in th]; [t.join() for t in th]
    eq("two invoices for one milestone in parallel: one 200, one refused", sorted(x == 200 for x in out), [False, True])
    check("one milestone, one live invoice: billed 50.000.000", billed(p) == 50_000_000, billed(p))
    # commercial terms freeze once a project has a schedule or invoices (audit round 4)
    p = project("Uang V2")
    m1 = ms(p, 1, 60)[1]; ba(p); invoice(m1["name"], 60_000_000)
    st, resp = pa.call("PATCH", f"/api/v1/r/project/{Api.q(p)}", {"contract_value": 0})
    check("V2: a billed project's contract cannot go to 0 -> 422", st == 422, (st, resp))
    st, resp = pa.call("PATCH", f"/api/v1/r/project/{Api.q(p)}", {"billing_model": "retainer"})
    check("V2b: a billed project cannot switch to retainer -> 422", st == 422, (st, resp))
    st, resp = pa.call("PATCH", f"/api/v1/r/project/{Api.q(p)}", {"contract_value": 200_000_000})
    check("a billed project's contract cannot be raised either (frozen) -> 422", st == 422, (st, resp))
    # every project invoice counts against the contract, with or without a milestone
    p = project("Uang tanpa termin", cv=10_000_000)
    st, i1 = fm.create("sales_invoice", {"customer": customer, "project": p, "items": [{"item": ndt, "qty": 1, "rate": 8_000_000}]})
    st1 = fm.verb("sales_invoice", i1["name"], "submit", {"force": True})[0] if st == 201 else st
    st, i2 = fm.create("sales_invoice", {"customer": customer, "project": p, "items": [{"item": ndt, "qty": 1, "rate": 5_000_000}]})
    st2, r2 = fm.verb("sales_invoice", i2["name"], "submit", {"force": True}) if st == 201 else (st, i2)
    check("project invoices without milestones: 8.000.000 submits, a further 5.000.000 on a 10.000.000 contract -> 422",
          st1 == 200 and st2 == 422, (st1, st2, r2))
    st, resp = fm.verb("sales_invoice", i1["name"] if st1 != 200 else api.find("sales_invoice", "project == '%s' && docstatus == 1" % p)[0]["name"], "cancel")
    st2b = fm.verb("sales_invoice", i2["name"], "submit", {"force": True})[0]
    check("after cancelling the 8.000.000 invoice, the 5.000.000 one fits", st == 200 and st2b == 200, (st, st2b))
    # an amendment re-pointed at another milestone claims that milestone like any invoice
    p = project("Uang amend")
    ma = ms(p, 1, 50, trig="date", due_date="2026-11-01")[1]; mb = ms(p, 2, 50, trig="date", due_date="2026-11-02")[1]
    st, ia = fm.create("sales_invoice", {"billing_milestone": ma["name"], "items": [{"item": ndt, "qty": 1, "rate": 50_000_000}]})
    st, ib = fm.create("sales_invoice", {"billing_milestone": mb["name"], "items": [{"item": ndt, "qty": 1, "rate": 50_000_000}]})
    ibn = fm.verb("sales_invoice", ib["name"], "submit", {"force": True})[1].get("name")
    fm.verb("sales_invoice", ibn, "cancel")
    st, am = fm.verb("sales_invoice", ibn, "amend")
    amn = (am or {}).get("name") or ((am or {}).get("open") or {}).get("name")
    st, resp = fm.call("PATCH", f"/api/v1/r/sales_invoice/{Api.q(amn)}", {"billing_milestone": ma["name"]}) if amn else (0, None)
    check("an amendment re-pointed at a milestone that already has an invoice -> 422", st == 422, (st, resp))
    # retainer: the contract value is the monthly fee; every month may bill it, no month more
    p = project("Uang retainer", cv=15_000_000, model="retainer")
    res = [ms(p, 10 + i, amount=15_000_000, trig="date", due_date=f"2026-1{i}-28", kind="retainer", period_from=f"2026-1{i}-01", period_to=f"2026-1{i}-28")[0] for i in (0, 1)]
    st, resp = ms(p, 20, amount=20_000_000, trig="date", due_date="2027-01-28", kind="retainer", period_from="2027-01-01", period_to="2027-01-31")
    check("retainer: months of 15.000.000 on a 15.000.000 monthly fee are accepted, a 20.000.000 month -> 422", res == [201, 201] and st == 422, (res, st, resp))
    st, ri = fm.create("sales_invoice", {"customer": customer, "project": p, "items": [{"item": ndt, "qty": 1, "rate": 10_000_000}]})
    check("retainer: an invoice without a monthly milestone -> 422", st == 422 or fm.verb("sales_invoice", ri["name"], "submit", {"force": True})[0] == 422, (st, ri))
    st, resp = ms(p, 30, amount=15_000_000, trig="date", due_date="2027-02-27", kind="retainer", period_from="2027-02-01", period_to="2027-02-28")
    st2, resp2 = ms(p, 31, amount=15_000_000, trig="date", due_date="2027-02-28", kind="retainer", period_from="2027-02-01", period_to="2027-02-28")
    check("retainer: one milestone per month (a second for February -> 422)", st == 201 and st2 == 422, (st, st2, resp2))
    st, resp = ms(p, 32, amount=15_000_000, trig="date", due_date="2026-12-28", kind="retainer", period_from="2026-12-15", period_to="2027-01-14")
    check("retainer: a month starts on the 1st (period_from 15 Dec -> 422)", st == 422, (st, resp))
    # R1 (self-audit 6 Nov): a retainer project bills only through monthly milestones; other kinds have no month key
    p = project("Uang R1", cv=15_000_000, model="retainer")
    res = [ms(p, 1 + i, amount=15_000_000, trig="date", due_date="2026-11-30", kind=kd, **({"period_from": "2026-11-01", "period_to": "2026-11-30"} if kd == "time_material" else {}))[0]
           for i, kd in enumerate(("milestone", "time_material", "down_payment"))]
    check("R1: a milestone, time-material or DP milestone on a retainer project -> 422 each", res == [422, 422, 422], res)
    eq("R1: nothing billed on the retainer project", billed(p), 0)
    # R2/R3: a billed November retainer cannot be re-kinded or moved to free November, and November is billed once
    nov = {"trig": "date", "due_date": "2026-11-30", "kind": "retainer", "period_from": "2026-11-01", "period_to": "2026-11-30"}
    p = project("Uang R2", cv=15_000_000, model="retainer")
    st, m = ms(p, 1, amount=15_000_000, **nov)
    i1 = invoice(m["name"], 15_000_000) if st == 201 else st
    rk = pa.call("PATCH", f"/api/v1/r/billing_milestone/{Api.q(m['name'])}", {"kind": "milestone"})[0] if st == 201 else st
    mv = pa.call("PATCH", f"/api/v1/r/billing_milestone/{Api.q(m['name'])}", {"period_from": "2027-05-01", "period_to": "2027-05-31"})[0] if st == 201 else st
    check("R2/R3: November billed, then re-kinding (-> 422) or moving its period (-> 422) the billed milestone is refused", (i1, rk, mv) == (200, 422, 422), (i1, rk, mv))
    eq("R2/R3: the billed November milestone keeps its month", (api.get("billing_milestone", m["name"]) or {}).get("period_from") if st == 201 else st, "2026-11-01")
    st2, _ = ms(p, 2, amount=15_000_000, **nov)
    eq("R2/R3: retainer project billed 15.000.000 for November, a second November milestone -> 422", (billed(p), st2), (15_000_000, 422))
    # a retention release belongs to the project of the invoice it releases
    pr1 = project("Retensi asal", cv=0); pr2 = project("Retensi lain", cv=100_000_000)
    st, src = fm.create("sales_invoice", {"customer": customer, "project": pr1, "retention_percent": 10, "items": [{"item": ndt, "qty": 1, "rate": 20_000_000}]})
    srcn = fm.verb("sales_invoice", src["name"], "submit", {"force": True})[1].get("name") if st == 201 else None
    st, resp = fm.create("sales_invoice", {"customer": customer, "project": pr2, "kind": "retention", "retention_of": srcn, "items": [{"item": ndt, "qty": 1, "rate": 2_000_000}]})
    check("a retention release on another project than its invoice's -> 422", st == 422, (st, resp))


def scenario_project_margin(api):
    """Tahap 2 J5: project_ledger rows written by flows inside each submit and cancel; their sum is the margin.
    Each document is submitted by the role that does it, so the flows' create permission is exercised."""
    pa, fm, pm = CTX.get("pa"), CTX.get("fm"), CTX.get("pm")
    if None in (pa, fm, pm):
        fail("project margin: members", CTX.get("login_error", "members missing"))
        return
    customer, ndt = "CUST-0001", services(api)[0]["name"]
    st, prj = pa.create("project", {"project_name": "Konsultasi transfer pricing", "customer": customer, "state": "running", "contract_value": 60_000_000})
    if not check("project for the margin checks", st == 201, (st, prj)):
        return
    p = prj["name"]
    def rows():
        return api.find("project_ledger", "project == '%s'" % p)
    def margin():
        return sum(r["amount"] for r in rows())
    st, inv = fm.create("sales_invoice", {"customer": customer, "project": p, "items": [{"item": ndt, "qty": 1, "rate": 40_000_000}]})
    invn = submit_doc(fm, "sales_invoice", inv["name"], "finance submits a 40.000.000 project invoice", force=True) if st == 201 else None
    st, cw = api.create("worker", {"worker_name": "Konsultan TP", "hour_rate": 500_000, "cost_hour_rate": 150_000})
    st, asg = pa.create("assignment", {"project": p, "worker": cw["name"], "start_date": "2026-11-01", "end_date": "2026-11-30", "rate_basis": "hour"})
    st, ts = pa.create("timesheet", {"project": p, "worker": cw["name"], "assignment": asg["name"], "work_date": "2026-11-05", "hours": 20, "description": "Analisis"})
    tsn = submit_doc(pa, "timesheet", ts["name"], "project admin submits 20 hours (cost 3.000.000)") if st == 201 else None
    st, ex = pa.create("project_expense", {"project": p, "category": "travel", "description": "Tiket", "amount": 2_000_000})
    submit_doc(pa, "project_expense", ex["name"], "project admin submits a 2.000.000 expense") if st == 201 else None
    st, vb = pm.create("vendor_bill", {"vendor": "VND-0001", "project": p, "bill_no": "SUB-TP-1", "bill_date": "2026-11-20", "items": [{"item": "BRG-0004", "qty": 1, "rate": 25_000_000}]})
    vbn = submit_doc(pm, "vendor_bill", vb["name"], "purchasing submits a 25.000.000 subcontract bill") if st == 201 else None
    got = sorted((r["kind"], r["amount"]) for r in rows())
    eq("ledger rows: revenue +40.000.000, labor -3.000.000, expense -2.000.000, vendor -25.000.000", got,
       sorted([("revenue", 40_000_000), ("labor", -3_000_000), ("expense", -2_000_000), ("vendor", -25_000_000)]))
    eq("margin 10.000.000", margin(), 10_000_000)
    if vbn:
        st, resp = pm.verb("vendor_bill", vbn, "cancel")
        check("purchasing cancels the subcontract bill", st == 200, (st, resp))
        eq("cancel writes the opposite row: margin 35.000.000", margin(), 35_000_000)
    st, ret = fm.create("sales_invoice", {"customer": customer, "project": p, "kind": "retention", "retention_of": invn, "items": [{"item": ndt, "qty": 1, "rate": 0}]})
    eq("margin unchanged by anything not submitted", margin(), 35_000_000)
    st, resp = pa.call("PATCH", f"/api/v1/r/project_ledger/{Api.q(rows()[0]['name'])}", {"amount": 1})
    check("people cannot edit a ledger row (no write)", st == 403, (st, resp))
    # no row can be made up by hand, even by a role that holds create (flows run as that role)
    for who, cl in (("project_admin", pa), ("finance_manager", fm), ("sales_manager", CTX.get("sm"))):
        st, resp = cl.create("project_ledger", {"project": p, "posting_date": "2026-11-20", "kind": "revenue", "source_model": "manual", "source_name": "manual", "amount": 100_000_000})
        check(f"{who} cannot make up a revenue row -> 422", st == 422, (st, resp))
    if invn:
        st, resp = fm.create("project_ledger", {"project": p, "posting_date": "2026-11-20", "kind": "revenue", "source_model": "sales_invoice", "source_name": invn, "sales_invoice": invn, "amount": 40_000_000})
        check("a second copy of the invoice's own row -> 422 (entry_key unique)", st == 422, (st, resp))
    # an expense cannot point at a vendor bill of no project (its cost would reach no ledger)
    st, nb = pm.create("vendor_bill", {"vendor": "VND-0001", "bill_no": "NOPRJ-1", "bill_date": "2026-11-20", "items": [{"item": "BRG-0004", "qty": 1, "rate": 1_000_000}]})
    # an expense counts at its own full amount whatever its linked bill does (audit round 3): it may link a bill
    # recorded without the project (that bill reaches no ledger), a draft or a smaller one, never a bill of this project
    before_m = margin()
    st, draft_bill = pm.create("vendor_bill", {"vendor": "VND-0001", "bill_no": "DRAFT-BILL", "bill_date": "2026-11-20", "items": [{"item": "BRG-0004", "qty": 1, "rate": 8_000_000}]})
    st, resp = pa.create("project_expense", {"project": p, "description": "Ke tagihan draft", "amount": 8_000_000, "vendor_bill": draft_bill.get("name", "")})
    check("D1: an expense cannot link a draft bill (it could gain the project later, and its name changes at submit) -> 422", st == 422, (st, resp))
    sb = submit_doc(pm, "vendor_bill", draft_bill["name"], "submit the project-less bill") if draft_bill.get("name") else None
    st, e1 = pa.create("project_expense", {"project": p, "description": "Ke tagihan tanpa proyek", "amount": 8_000_000, "vendor_bill": sb or ""})
    e1n = submit_doc(pa, "project_expense", e1["name"], "expense of 8.000.000 linked to a submitted bill without project") if st == 201 else None
    if sb:
        st, resp = pm.verb("vendor_bill", sb, "cancel")
        check("D2: a bill linked by a live expense cannot be cancelled (it could come back with the project) -> 422", st == 422, (st, resp))
    nbn = submit_doc(pm, "vendor_bill", nb["name"], "submit the small bill without project") if nb.get("name") else ""
    st, e2 = pa.create("project_expense", {"project": p, "description": "Ke tagihan kecil", "amount": 9_000_000, "vendor_bill": nbn or ""})
    submit_doc(pa, "project_expense", e2["name"], "expense of 9.000.000 linked to a 1.000.000 bill without project") if st == 201 else None
    eq("both expenses lower the margin by their full amount (17.000.000)", before_m - margin(), 17_000_000)
    st, vbp = pm.create("vendor_bill", {"vendor": "VND-0001", "project": p, "bill_no": "PRJ-BILL", "bill_date": "2026-11-20", "items": [{"item": "BRG-0004", "qty": 1, "rate": 1_000_000}]})
    st, resp = pa.create("project_expense", {"project": p, "description": "x", "amount": 1_000_000, "vendor_bill": vbp.get("name", "")})
    check("an expense linking a bill of this project (counted on its own) -> 422", st == 422, (st, resp))
    if e1n:
        pa.verb("project_expense", e1n, "cancel")
        eq("cancelling the 8.000.000 expense gives it back", before_m - margin(), 9_000_000)
        if sb:
            st, resp = pm.verb("vendor_bill", sb, "cancel")
            check("once the expense is cancelled, its bill can be cancelled", st == 200, (st, resp))
    # a project invoice with DPP 0 (fully discounted) submits and cancels: its post and reverse rows have distinct keys
    st, z = fm.create("sales_invoice", {"customer": customer, "project": p, "discount_amount": 1_000_000, "items": [{"item": ndt, "qty": 1, "rate": 1_000_000}]})
    zn = submit_doc(fm, "sales_invoice", z["name"], "finance submits a DPP 0 project invoice", force=True) if st == 201 else None
    if zn:
        st, resp = fm.verb("sales_invoice", zn, "cancel")
        check("the DPP 0 project invoice can be cancelled (no entry_key clash)", st == 200, (st, resp))
    # a reversal row needs the row it reverses: none for a document that never had one, and only one per row
    st, ex = pa.create("project_expense", {"project": p, "category": "other", "description": "Batal", "amount": 500_000})
    exn = submit_doc(pa, "project_expense", ex["name"], "submit an expense to cancel") if st == 201 else None
    if exn:
        pa.verb("project_expense", exn, "cancel")
        post = [r for r in rows() if r["source_name"] == exn and r["entry"] == "post"]
        st, resp = pa.create("project_ledger", {"project": p, "posting_date": ex["expense_date"], "kind": "expense", "entry": "reverse", "source_model": "project_expense",
                                                "source_name": exn, "project_expense": exn, "reverses": post[0]["name"] if post else "", "amount": 500_000})
        check("a second reversal of the same row -> 422", st == 422, (st, resp))
    allr = rows()
    posts = {r["name"]: r for r in allr if r["entry"] == "post"}
    revs = [r for r in allr if r["entry"] == "reverse"]
    check("every reversal points at the post row of the same document, with the opposite amount",
          all(r["reverses"] in posts and posts[r["reverses"]]["source_name"] == r["source_name"] and r["amount"] == -posts[r["reverses"]]["amount"] for r in revs), revs)
    eq("no document has two reversals; each cancelled one here has one",
       sorted(r["source_name"] for r in revs), sorted({r["source_name"] for r in revs}))
    eq("four reversals: the subcontract bill, the DPP 0 invoice, the 500.000 expense and the 8.000.000 expense", len(revs), 4)
    ws = dashboard(api, "project_margin", 365)
    eq("project_margin margin_by_project shows the project's margin (35.000.000 - the 9.000.000 expense = 26.000.000)", chart_map(ws.get("margin_by_project", {})).get(p), 26_000_000)


def scenario_manpower_recap(api):
    """Gladi 6 Nov G5 + G7: pay (cost) is imported with the worker list, kept apart from the selling rate, and reaches
    the project margin; attendance arrives as one monthly recap row per worker per project (days present, overtime
    hours) instead of one timesheet per person per day. A timesheet with no pay is flagged, never silently 0 cost."""
    pa = CTX.get("pa")
    if pa is None:
        fail("manpower recap: members", CTX.get("login_error", "members missing"))
        return
    # G5: the worker list carries pay next to the selling rate; the pay columns are optional (old files still import)
    wl = ("Nama,Nomor karyawan,Jabatan,WhatsApp,Tarif harian,Upah harian,Tarif lembur per jam,Upah lembur per jam\n"
          "Operator Satu,MP-001,Operator forklift,+620000000031,225000,150000,30000,20000\n"
          "Operator Dua,MP-002,Operator forklift,+620000000032,225000,,,\n").encode()
    pv, c = import_commit(api, "worker_list", "tenaga-dengan-upah.csv", wl, label="worker list with pay (2 workers)")
    ws = {w["employee_no"]: w for w in api.list("worker", limit=500) if w.get("employee_no", "").startswith("MP-")}
    eq("worker list: pay apart from the selling rate (MP-001 jual 225.000, upah 150.000, lembur 30.000/20.000)",
       tuple((ws.get("MP-001") or {}).get(f) for f in ("day_rate", "cost_day_rate", "overtime_rate", "overtime_cost_rate")), (225_000, 150_000, 30_000, 20_000))
    eq("worker list: an empty pay cell stays 0 (MP-002)", (ws.get("MP-002") or {}).get("cost_day_rate"), 0)
    old = b"Nama,Nomor karyawan,Jabatan,WhatsApp,Tarif harian\nOperator Lama,MP-003,Helper,+620000000033,200000\n"
    import_commit(api, "worker_list", "tenaga-template-lama.csv", old, label="worker list in the old template (no pay columns)")
    if c is None or "MP-001" not in ws:
        return
    st, prj = pa.create("project", {"project_name": "Outsourcing gudang Agro", "customer": "CUST-0001", "state": "running", "contract_value": 120_000_000})
    if not check("manpower project", st == 201, (st, prj)):
        return
    p = prj["name"]
    st, asg = pa.create("assignment", {"project": p, "worker": ws["MP-001"]["name"], "start_date": "2026-10-21", "end_date": "2027-10-20", "day_rate": 250_000})
    check("assignment of MP-001 at the client rate 250.000 (pay and overtime from the worker)", st == 201 and (asg.get("cost_rate"), asg.get("overtime_rate"), asg.get("overtime_cost_rate")) == (150_000, 30_000, 20_000), (st, asg))
    # G7: one recap row per worker per project per cut-off period (21 Oct to 20 Nov)
    head = "Proyek,No. pegawai,Nama,Dari tanggal,Sampai tanggal,Hari hadir,Jam lembur\n"
    recap = (head + f"{p},MP-001,Operator Satu,21/10/2026,20/11/2026,22,10\n" + f"{p},MP-002,Operator Dua,21/10/2026,20/11/2026,20,\n").encode()
    pv, c = import_commit(pa, "attendance_recap", "rekap-absensi-okt-nov.csv", recap, label="monthly attendance recap (2 workers)")
    if c is None:
        return
    tss = {pa.get("timesheet", n)["worker"]: pa.get("timesheet", n) for n in c["records"]}
    t1, t2 = tss.get(ws["MP-001"]["name"], {}), tss.get(ws["MP-002"]["name"], {})
    eq("recap row -> one draft timesheet for the period", (t1.get("work_date"), t1.get("period_to"), t1.get("days"), t1.get("overtime_hours"), t1.get("docstatus")), ("2026-10-21", "2026-11-20", 22, 10, 0))
    eq("the recap finds the worker's assignment on the project (client rate 250.000)", (t1.get("assignment"), t1.get("day_rate")), (asg.get("name"), 250_000))
    eq("MP-001: 22 x 250.000 + 10 h x 30.000 = 5.800.000; cost 22 x 150.000 + 10 h x 20.000 = 3.500.000", (t1.get("amount"), t1.get("cost_amount"), t1.get("cost_missing")), (5_800_000, 3_500_000, False))
    eq("MP-002 without assignment: worker rate 20 x 225.000 = 4.500.000, no pay -> flagged, not a silent 0", (t2.get("amount"), t2.get("cost_amount"), t2.get("cost_missing")), (4_500_000, 0, True))
    st, dup = pa.upload("/api/v1/imports/attendance_recap", {}, "rekap-absensi-okt-nov-lagi.csv", recap)
    check("the same recap again -> duplicate, nothing created twice", st == 200 and dup.get("duplicate") is True, (st, dup))
    bad = (head + f"{p},MP-001,Operator Satu,21/11/2026,20/12/2026,35,\n"      # 35 days in a 30-day period
                + f"{p},MP-001,Operator Satu,01/12/2026,15/01/2027,20,\n"      # a period longer than 31 days
                + f"{p},MP-002,Operator Dua,21/11/2026,20/12/2026,0,\n").encode()  # nothing worked
    st, pv = pa.upload("/api/v1/imports/attendance_recap", {}, "rekap-salah.csv", bad)
    eq("a recap with impossible rows: every bad row is reported (rows 2-4)", sorted({e["row"] for e in (pv or {}).get("errors", [])}), [2, 3, 4])
    st, cm = pa.call("POST", f"/api/v1/imports/attendance_recap/{pv['id']}/commit") if st == 200 else (st, pv)
    check("... and it cannot be committed: 422 import_errors", st == 422, (st, cm))
    st, pv = pa.upload("/api/v1/imports/attendance_recap", {}, "rekap-pegawai-asing.csv", (head + f"{p},MP-999,Tidak Ada,21/11/2026,20/12/2026,20,\n").encode())
    check("an unknown employee number is reported on its row", st == 200 and [e["row"] for e in pv.get("errors", [])] == [2], (st, pv))
    t1n, t2n = (submit_doc(pa, "timesheet", t["name"], f"project admin submits the recap timesheet of {t.get('worker')}") for t in (t1, t2))
    rows = lambda: api.find("project_ledger", "project == '%s'" % p)
    eq("ledger: only the paid worker's cost -3.500.000 (MP-002 has none to post)", sorted((r["kind"], r["amount"]) for r in rows()), [("labor", -3_500_000)])
    d = dashboard(api, "project_margin", 365)
    flagged = len(api.find("timesheet", "docstatus == 1 && cost_missing"))
    eq("project_margin: submitted timesheets without pay are counted, so the margin reads as incomplete", num(d.get("timesheets_without_pay", {})), flagged)
    check("... and MP-002's recap is one of them", flagged >= 1, flagged)
    nopay = len([w for w in api.list("worker", limit=500) if w["active"] in (True, "true") and not w.get("cost_day_rate") and not w.get("cost_hour_rate")])
    eq("project_margin: active workers without pay", num(d.get("workers_without_pay", {})), nopay)
    st, resp = pa.verb("timesheet", t1n, "cancel") if t1n else (0, "not submitted")
    check("a recap timesheet can be cancelled", st == 200, (st, resp))
    eq("cancel reverses its cost: labor rows sum 0", sum(r["amount"] for r in rows()), 0)
    # audit 6 Nov: no negative money, no silent default days, no fraction past the period, overtime within the period
    neg = (b"Nama,Nomor karyawan,Jabatan,WhatsApp,Tarif harian,Upah harian,Tarif lembur per jam,Upah lembur per jam\n"
           b"Upah Minus,MP-NEG,Helper,+620000000039,225000,-150000,,\n")
    st, pv = api.upload("/api/v1/imports/worker_list", {}, "upah-minus.csv", neg)
    check("worker list: a negative pay is an error on its row, not a cost that raises the margin", st == 200 and [e["row"] for e in pv.get("errors", [])] == [2], (st, pv))
    for model, body in (("worker", {"worker_name": "Minus", "day_rate": 1, "overtime_cost_rate": -1}),
                        ("assignment", {"project": p, "worker": ws["MP-002"]["name"], "start_date": "2026-10-21", "end_date": "2026-10-22", "overtime_rate": -1}),
                        ("timesheet", {"project": p, "worker": ws["MP-002"]["name"], "work_date": "2026-11-10", "days": 1, "cost_rate": -1, "description": "x"})):
        st, resp = pa.create(model, body) if model != "worker" else api.create(model, body)
        check(f"{model}: a negative rate -> 422", st == 422, (st, resp))
    edge = (head + f"{p},MP-002,Operator Dua,21/12/2026,20/01/2027,,\n"         # Hari hadir empty
                 + f"{p},MP-002,Operator Dua,22/12/2026,22/12/2026,1.9,\n"      # 1,9 days in a 1-day period
                 + f"{p},MP-002,Operator Dua,23/12/2026,23/12/2026,1,30\n").encode()  # 30 h overtime in one day
    st, pv = pa.upload("/api/v1/imports/attendance_recap", {}, "rekap-tepi.csv", edge)
    eq("recap edges: empty days, a fraction past the period, overtime longer than the period are each refused (rows 2-4)",
       sorted({e["row"] for e in (pv or {}).get("errors", [])}), [2, 3, 4])
    st, ok15 = pa.create("timesheet", {"project": p, "worker": ws["MP-002"]["name"], "work_date": "2026-12-24", "period_to": "2026-12-25", "days": 1.5, "description": "x"})
    check("1,5 days in a 2-day recap period is fine", st == 201, (st, ok15))
    st, hasg = pa.create("assignment", {"project": p, "worker": ws["MP-003"]["name"] if "MP-003" in ws else ws["MP-002"]["name"], "start_date": "2027-01-01", "end_date": "2027-01-31", "rate_basis": "hour", "hour_rate": 50_000, "day_rate": 300_000})
    if check("an hourly assignment", st == 201, (st, hasg)):
        st, ht = pa.create("timesheet", {"project": p, "worker": hasg["worker"], "work_date": "2027-01-01", "period_to": "2027-01-20", "days": 15, "description": "Rekap"})
        check("a recap for an hourly assignment stays per day at the assignment's day rate (15 x 300.000)", st == 201 and (ht.get("rate_basis"), ht.get("amount"), ht.get("assignment")) == ("day", 4_500_000, hasg["name"]), (st, ht))
    # a daily timesheet keeps its 1,5-day limit; a recap period may not run backwards
    st, resp = pa.create("timesheet", {"project": p, "worker": ws["MP-001"]["name"], "work_date": "2026-11-10", "days": 2, "description": "x"})
    check("a daily timesheet of 2 days is still refused", st == 422, (st, resp))
    st, resp = pa.create("timesheet", {"project": p, "worker": ws["MP-001"]["name"], "work_date": "2026-11-10", "period_to": "2026-11-01", "days": 5, "description": "x"})
    check("a recap whose end is before its start -> 422", st == 422, (st, resp))


def scenario_cost_visibility(api):
    """Audit re-pin L1 (t_9c6fc6d5): pay and labour cost are read only by owner, finance and project admin. hidden_for is
    not enforced by the kernel, so the models carrying pay (worker, assignment, timesheet) are not readable by sales or
    purchasing, and the project ledger shows sales only revenue rows and purchasing only vendor rows (row-level `if`),
    which is what their submit and cancel flows need. No CSV export of the ledger for sales."""
    sm, pm, fm, pa = (CTX.get(x) for x in ("sm", "pm", "fm", "pa"))
    if None in (sm, pm, fm, pa):
        fail("cost visibility: members", CTX.get("login_error", "members missing"))
        return
    eq("sales_manager: worker, assignment and timesheet (pay, cost rates) -> 403",
       [sm.call("GET", f"/api/v1/r/{m}?limit=1")[0] for m in ("worker", "assignment", "timesheet")], [403, 403, 403])
    eq("purchasing_manager: worker (pay) -> 403", pm.call("GET", "/api/v1/r/worker?limit=1")[0], 403)
    for who, cl in (("finance_manager", fm), ("project_admin", pa)):
        st, r = cl.call("GET", "/api/v1/r/worker?limit=50")
        check(f"{who} still reads the workers' pay", st == 200 and all("cost_day_rate" in w for w in r.get("data", [])), (st, r if st != 200 else len(r.get("data", []))))
    # one project with a revenue, a labour and a vendor row, each written by the role that does the work
    ndt = services(api)[0]["name"]
    st, prj = pa.create("project", {"project_name": "Visibilitas biaya", "customer": "CUST-0001", "state": "running"})
    if not check("project for the visibility checks", st == 201, (st, prj)):
        return
    p = prj["name"]
    st, w = api.create("worker", {"worker_name": "Upah Rahasia", "day_rate": 300_000, "cost_day_rate": 200_000})
    st, ts = pa.create("timesheet", {"project": p, "worker": w["name"], "work_date": "2026-11-05", "days": 1, "description": "Shift"})
    submit_doc(pa, "timesheet", ts["name"], "project admin submits a timesheet (labour row -200.000)") if st == 201 else None
    st, vb = pm.create("vendor_bill", {"vendor": "VND-0001", "project": p, "bill_no": "VIS-1", "bill_date": "2026-11-20", "items": [{"item": "BRG-0004", "qty": 1, "rate": 1_000_000}]})
    submit_doc(pm, "vendor_bill", vb["name"], "purchasing submits a project vendor bill (vendor row)") if st == 201 else None
    st, inv = sm.create("sales_invoice", {"customer": "CUST-0001", "project": p, "items": [{"item": ndt, "qty": 1, "rate": 5_000_000}]})
    invn = submit_doc(sm, "sales_invoice", inv["name"], "sales_manager submits a project invoice (revenue row by its flow)", force=True) if st == 201 else None
    flt = "&filter=" + urllib.parse.quote("project == '%s'" % p)
    eq("owner sees the three kinds", sorted({r["kind"] for r in api.find("project_ledger", "project == '%s'" % p)}), ["labor", "revenue", "vendor"])
    for who, cl, want in (("sales_manager", sm, ["revenue"]), ("purchasing_manager", pm, ["vendor"])):
        st, rows = cl.call("GET", "/api/v1/r/project_ledger?limit=500" + flt)
        eq(f"{who} reads only {want[0]} rows of the project ledger", sorted({r["kind"] for r in rows.get("data", [])}) if st == 200 else st, want)
    st, r = sm.call("GET", "/api/v1/r/project_ledger?limit=500&filter=" + urllib.parse.quote("kind == 'labor'"))
    check("sales_manager: a filter for labour rows finds nothing", st != 200 or not r.get("data"), (st, r))
    req = urllib.request.Request(sm.base + "/api/v1/export/r/project_ledger", headers=sm.headers())
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            est = resp.status
    except urllib.error.HTTPError as e:
        est = e.code
    eq("sales_manager cannot export the project ledger", est, 403)
    st, d = sm.call("GET", "/api/v1/dashboards/project_margin")
    shown = sorted(w["name"] for w in d.get("widgets", []) if not w.get("hidden")) if st == 200 else st
    # roles: [finance_manager, project_admin]: a kernel that enforces dashboard roles (contract 0027, alpha.9) answers
    # 403; an older kernel opens it and the widget `if:` leaves only the contract backlog
    check("project_margin for sales_manager: closed (403) or only the contract backlog shows (no margin made of revenue rows, no pay)", shown in (403, ["backlog_total"]), shown)
    fmd = dashboard(fm, "project_margin", 365)
    check("project_margin for finance: margin widgets show", not fmd.get("margin_total", {}).get("hidden") and "margin_total" in fmd, list(fmd))
    st, d = sm.call("GET", "/api/v1/dashboards/jasa_overview")
    ws = {w["name"]: w for w in d.get("widgets", [])} if st == 200 else {}
    eq("jasa_overview for sales_manager: no widget errors", {n: w["error"] for n, w in ws.items() if "error" in w}, {})
    eq("jasa_overview for sales_manager: the worker widgets are hidden", [bool(ws.get(n, {}).get("hidden")) for n in ("active_workers", "workers_certifications")], [True, True])
    st, d = pa.call("GET", "/api/v1/dashboards/jasa_overview")
    ws = {w["name"]: w for w in d.get("widgets", [])} if st == 200 else {}
    eq("jasa_overview for project_admin: the worker widgets show", [bool(ws.get(n, {}).get("hidden")) for n in ("active_workers", "workers_certifications")], [False, False])
    # the flows still work for sales: the invoice cancelled by sales_manager reverses its revenue row
    if invn:
        st, resp = sm.verb("sales_invoice", invn, "cancel")
        check("sales_manager cancels it (the reversing flow finds the revenue row)", st == 200, (st, resp))
    rev = [r["amount"] for r in api.find("project_ledger", "project == '%s' && kind == 'revenue'" % p)]
    eq("its revenue rows: +5.000.000 and -5.000.000", sorted(rev), [-5_000_000, 5_000_000])
    prj = {"name": p}
    st, resp = sm.create("project_ledger", {"project": prj["name"], "posting_date": "2026-11-20", "kind": "labor", "source_model": "manual", "source_name": "manual", "amount": 1})
    check("sales_manager cannot write a labour row -> 403/422", st in (403, 422), (st, resp))


def scenario_roles_jasa(api):
    sm, fm, pm, pa = (CTX.get(x) for x in ("sm", "fm", "pm", "pa"))
    if not all((sm, fm, pm, pa)):
        fail("members per role on demo-jasa", f"setup failed: could not sign in as non-owners: {CTX.get('login_error')}")
        return
    ndt = services(api)[0]["name"]
    customer = "CUST-0001"
    st, me = pa.call("GET", "/auth/me")
    eq("project_admin member signs in with its role", [r for r in ((me or {}).get("org") or {}).get("roles", [])], ["project_admin"])

    # A2: a quotation by a non-approver is held (202) until a sales_manager approves
    def quote(subject):
        st, q = pa.create("quotation", {"customer": customer, "subject": subject, "items": [{"item": ndt, "qty": 2, "rate": 1_000_000}]})
        check(f"project_admin creates quotation {subject!r}", st == 201, (st, q))
        return q["name"]
    q1 = quote("Ditahan untuk persetujuan")
    st, resp = pa.verb("quotation", q1, "submit")
    check("project_admin submits a quotation: held, 202 with a pending manager_approval for sales_manager",
          st == 202 and resp["approval"]["state"] == "pending" and resp["approval"]["policy"] == "manager_approval" and resp["approval"]["approvers"] == ["sales_manager"], (st, resp))
    if st != 202:
        return
    aid = resp["approval"]["id"]
    eq("held quotation is still a draft", api.get("quotation", q1)["docstatus"], 0)
    st, resp = decide(pa, aid)
    check("project_admin cannot approve its own request -> 403 permission_denied", st == 403 and resp["error"]["code"] == "permission_denied", (st, resp))
    st, resp = decide(fm, aid)
    check("finance_manager cannot approve a quotation -> 403", st == 403, (st, resp))
    st, resp = decide(sm, aid)
    check("sales_manager approves: the quotation is submitted", st == 200 and resp.get("record", {}).get("docstatus") == 1, (st, resp))
    sub1 = resp.get("record", {}).get("name", q1)
    check("approved quotation is numbered QUO-... and was created by the project_admin", sub1.startswith("QUO-") and api.get("quotation", sub1)["created_by"] == "agus@contohjasa.test",
          api.get("quotation", sub1).get("created_by"))
    q2 = quote("Ditolak manajer")
    st, resp = pa.verb("quotation", q2, "submit")
    st, resp = decide(sm, resp["approval"]["id"], "reject", "Harga belum sesuai")
    check("sales_manager rejects: the request is rejected and the quotation stays a draft", st == 200 and resp.get("state", resp.get("approval", {}).get("state")) == "rejected" and api.get("quotation", q2)["docstatus"] == 0, (st, resp))

    # B-side of A5: a big invoice (> 100.000.000) by a sales_manager (who may invoice, docs/permissions.md) is held for finance_manager;
    # project_admin may not invoice at all
    st, resp = pa.create("sales_invoice", invoice_body(customer, ndt, rate=100_000_000))
    check("project_admin may not create a sales_invoice -> 403 permission_denied", st == 403 and resp["error"]["code"] == "permission_denied", (st, resp))
    st, inv = sm.create("sales_invoice", invoice_body(customer, ndt, rate=100_000_000))
    check("sales_manager creates a big invoice (111.000.000)", st == 201 and inv["grand_total"] == 111_000_000, (st, inv))
    if st == 201:
        st, resp = sm.verb("sales_invoice", inv["name"], "submit", {"force": True})
        check("big invoice by a sales_manager: held, 202, policy big_invoice for finance_manager",
              st == 202 and resp["approval"]["policy"] == "big_invoice" and resp["approval"]["approvers"] == ["finance_manager"], (st, resp))
        big = None
        if st == 202:
            st2, r2 = decide(sm, resp["approval"]["id"])
            check("sales_manager cannot approve the big invoice -> 403", st2 == 403, (st2, r2))
            st2, r2 = decide(fm, resp["approval"]["id"])
            check("finance_manager approves the big invoice: submitted", st2 == 200 and r2.get("record", {}).get("docstatus") == 1, (st2, r2))
            big = r2.get("record", {}).get("name")
        if big:
            # finance_manager's main job: record the customer's payment against it
            st3, pay = fm.create("payment", {"direction": "receive", "customer": customer, "amount": 11_100_000, "allocations": [{"sales_invoice": big, "amount": 11_100_000}]})
            check("finance_manager creates a payment against the invoice", st3 == 201, (st3, pay))
            if st3 == 201:
                st3, resp3 = fm.verb("payment", pay["name"], "submit")
                check("finance_manager submits the payment", st3 == 200, (st3, resp3))
                eq("the invoice's outstanding fell by the payment (111.000.000 - 11.100.000)", api.get("sales_invoice", big)["outstanding"], 99_900_000)
    st, inv = fm.create("sales_invoice", invoice_body(customer, ndt, rate=100_000_000))
    st, resp = fm.verb("sales_invoice", inv["name"], "submit", {"force": True})
    reqs = [x for x in approvals_of(api, "sales_invoice", resp.get("name", inv["name"])) if x.get("policy") == "big_invoice"]
    check("finance_manager submits its own big invoice: self-approved, request recorded approved", st == 200 and len(reqs) == 1 and reqs[0]["state"] == "approved", (st, resp, reqs))

    # new_revision cancels the quotation first, and project_admin has no cancel (docs/permissions.md): refused. The site overlay
    # (custom/quotation.rk) makes the cancel need a sales_manager, who self-approves, so the request is recorded approved.
    st, resp = pa.action("quotation", sub1, "new_revision")
    check("project_admin may not revise a quotation (new_revision cancels) -> 403 permission_denied on cancel",
          st == 403 and resp["error"]["code"] == "permission_denied" and resp["error"].get("action") == "cancel" and api.get("quotation", sub1)["docstatus"] == 1, (st, resp))
    st, resp = sm.action("quotation", sub1, "new_revision")
    check("sales_manager revises the submitted quotation: a draft opens", st == 200 and resp.get("open", {}).get("model") == "quotation", (st, resp))
    cancels = [a for a in approvals_of(api, "quotation", sub1) if a.get("verb") == "cancel"]
    check("the revise recorded the overlay's cancel_approval, self-approved by the sales_manager",
          len(cancels) == 1 and cancels[0]["approvers"] == ["sales_manager"] and cancels[0]["state"] == "approved" and api.get("quotation", sub1)["docstatus"] == 2, cancels)
    if st == 200:
        d = api.get("quotation", resp["open"]["name"])
        eq("the revision is a draft with revision 1, outcome open, amended from the original", (d["docstatus"], d["revision"], d["outcome"], d.get("amended_from")), (0, 1, "open", sub1))
        st, resp = pa.verb("quotation", d["name"], "submit")
        check("the project_admin submits the revision: held again for the manager (a new submit approval)", st == 202 and resp["approval"]["policy"] == "manager_approval", (st, resp))

    # sales_manager's main job: own quotation submits at once (self-approval), then record the outcome
    st, q = sm.create("quotation", {"customer": customer, "subject": "Oleh manajer", "items": [{"item": ndt, "qty": 1, "rate": 1_500_000}]})
    st, resp = sm.verb("quotation", q["name"], "submit")
    check("sales_manager submits its own quotation: self-approved, 200", st == 200, (st, resp))
    qn = resp.get("name", q["name"])
    st, resp = sm.action("quotation", qn, "mark_outcome", {"outcome": "accepted"})
    check("sales_manager records the outcome accepted", st == 200 and api.get("quotation", qn)["outcome"] == "accepted", (st, resp))

    # project_admin's main job: project, assignment and timesheet
    worker = next(w for w in api.list("worker") if w["worker_name"] == "Andi Contoh")["name"]
    st, p = pa.create("project", {"project_name": "Proyek oleh admin", "customer": customer, "site": "Cilegon"})
    check("project_admin creates a project", st == 201, (st, p))
    if st == 201:
        st, a = pa.create("assignment", {"project": p["name"], "worker": worker, "start_date": "2026-11-10", "end_date": "2026-11-20"})
        check("project_admin creates an assignment", st == 201, (st, a))
        st, t = pa.create("timesheet", {"project": p["name"], "worker": worker, "assignment": a["name"], "work_date": "2026-11-10", "days": 1, "description": "Inspeksi"})
        check("project_admin creates a timesheet of 1.500.000", st == 201 and t["amount"] == 1_500_000, (st, t))
        if st == 201:
            st, resp = pa.verb("timesheet", t["name"], "submit")
            check("project_admin submits the timesheet", st == 200, (st, resp))

    # purchasing_manager raises POs; a PO above 5.000.000 waits for finance_manager (demo-jasa custom/purchase_order.rk), so
    # the maker never approves; under the limit no request is made
    st, resp = pa.create("purchase_order", {"vendor": "VND-0002", "order_date": "2026-11-03", "items": [{"item": "BRG-0004", "qty": 1, "rate": 85_000}]})
    check("project_admin may not create a purchase order -> 403 permission_denied", st == 403 and resp["error"]["code"] == "permission_denied", (st, resp))
    def po(qty):
        st, o = pm.create("purchase_order", {"vendor": "VND-0002", "order_date": "2026-11-03", "items": [{"item": "BRG-0004", "qty": qty, "rate": 85_000}]})
        check(f"purchasing_manager creates a purchase order of {qty} x 85.000", st == 201, (st, o))
        return o["name"], o["grand_total"]
    big_po, total = po(100)
    eq("the big PO is 8.500.000 (above 5.000.000)", total, 8_500_000)
    st, resp = pm.verb("purchase_order", big_po, "submit")
    check("purchasing_manager submits a big PO: held, 202, po_approval pending for finance_manager",
          st == 202 and resp["approval"]["state"] == "pending" and resp["approval"]["approvers"] == ["finance_manager"], (st, resp))
    if st == 202:
        st2, r2 = decide(pm, resp["approval"]["id"])
        check("the maker (purchasing_manager) cannot approve its PO -> 403", st2 == 403, (st2, r2))
        st2, r2 = decide(fm, resp["approval"]["id"])
        check("finance_manager approves: the PO is submitted and numbered PO/CIN/...", st2 == 200 and r2.get("record", {}).get("name", "").startswith("PO/CIN/"), (st2, r2))
    st, fxpo = pm.create("purchase_order", {"vendor": "VND-0002", "currency": "USD", "exchange_rate": 1.01,
                                            "items": [{"item": "BRG-0004", "qty": 1, "rate": 0, "rate_fx": 100_000}]})
    if check("purchasing_manager drafts a USD 100.000 PO at kurs 1,01", st == 201, (st, fxpo)):
        st, resp = pm.verb("purchase_order", fxpo["name"], "submit")
        check("a USD PO waits for finance_manager whatever its rupiah value (202)", st == 202 and resp["approval"]["approvers"] == ["finance_manager"], (st, resp))
    small_po, total = po(10)
    st, resp = pm.verb("purchase_order", small_po, "submit")
    check("PO under the limit (850.000) is submitted without an approval request", st == 200 and not approvals_of(api, "purchase_order", resp.get("name", small_po)), (st, resp))
    # project_admin's purchasing job is the request: create and submit, no PO
    st, pr = pa.create("purchase_request", {"needed_by": "2026-11-30", "purpose": "Bahan uji", "items": [{"item": "BRG-0004", "qty": 2, "est_rate": 85_000}]})
    check("project_admin creates a purchase request", st == 201, (st, pr))
    if st == 201:
        st, resp = pa.verb("purchase_request", pr["name"], "submit")
        check("project_admin submits the purchase request", st == 200, (st, resp))


# ---------- scenarios: the permission matrix (docs/permissions.md), every site

# Executable copy of docs/permissions.md: model -> role -> granted actions (owner is implicit and unrestricted).
# Models outside this table must not exist on a site (the check below fails on one), so a new model needs a row here
# and in the doc.
_ALL = {"read", "create", "write", "delete", "submit", "cancel", "amend", "export", "print"}
_R, _RP, _RCWP, _RCWS = {"read"}, {"read", "print"}, {"read", "create", "write", "print"}, {"read", "create", "write", "submit"}
_JASA = ("sales_manager", "finance_manager", "purchasing_manager", "project_admin")
_MASTER = dict({r: _RCWP for r in _JASA + ("head_office",)}, outlet_staff=_R)
_PIPE = dict({r: _R for r in ("finance_manager", "purchasing_manager", "project_admin", "head_office", "outlet_staff")},
             sales_manager=_ALL, sales_rep={"read", "create", "write"})          # sales_rep: own records (if: owner == user.name)
_STOCK_DOC = {"head_office": _ALL, "outlet_staff": _RCWS}
MATRIX = {
    **{m: _MASTER for m in ("vendor", "uom_conversion", "price_list", "tax_code", "currency", "exchange_rate")},
    # worker carries pay (hidden_for is not enforced): not readable by sales or purchasing (audit L1)
    "worker": {r: v for r, v in _MASTER.items() if r not in ("sales_manager", "purchasing_manager")},
    "uom": dict(_MASTER, field_sales=_R),
    # field_sales (distribution): a salesman reads his own customers (role scope salesman) and cannot change their credit
    "customer": dict(_MASTER, sales_rep={"read", "create", "write"}, field_sales=_R),     # crm.convert_lead makes customers for sales reps
    "item": dict(_MASTER, sales_rep=_R, field_sales=_R, head_office=_RCWP | {"import"}), "outlet": dict(_MASTER, sales_rep=_R, field_sales=_R),
    "item_category": dict(_MASTER, sales_rep=_R, field_sales=_R, head_office=_RCWP | {"delete"}),
    "user": {r: _R for r in _JASA + ("head_office", "outlet_staff", "sales_rep", "field_sales")},
    "quotation": {"sales_manager": _ALL, "project_admin": _RCWS, "finance_manager": _RP},
    "customer_order": {"sales_manager": _ALL, "project_admin": _RCWS, "finance_manager": _RP, "head_office": _RP,
                       "field_sales": {"read", "create", "write", "submit", "print"}},
    # delivery (grosir): warehouse and sales manager deliver and take returns; order_fulfilment is written by the flows
    # of whoever submits (all its fields are read_only), so those roles may create and write it
    **{m: {"head_office": _ALL, "sales_manager": _ALL, "finance_manager": _RP, "field_sales": _RP} for m in ("delivery_note", "sales_return")},
    "return_credit": {"finance_manager": {"read", "create", "write", "export"}, "sales_manager": {"read", "create", "write", "export"}, "head_office": {"read", "create", "write"}},
    "delivery_guard": {"head_office": {"create"}, "sales_manager": {"create"}, "finance_manager": {"create"}},   # never stored (a flow's refusal)
    "order_fulfilment": {"sales_manager": {"read", "create", "write", "export"}, "finance_manager": {"read", "create", "write", "export"},
                         "head_office": {"read", "create", "write", "export"}, "field_sales": {"read", "create", "write"}},
    # production (produksi): head office plans (BOM, SPK), the production room's staff record batches at their outlet;
    # a batch's wage sheet is made by the flow of whoever submits it, but only head office reads wages
    **{m: {"head_office": {"read", "create", "write", "delete", "export", "print"}, "outlet_staff": _RP} for m in ("bom",)},
    "production_stage": {"head_office": {"read", "create", "write", "delete", "export"}, "outlet_staff": _R},
    "work_order": {"head_office": _ALL, "outlet_staff": _RP},
    "production_batch": {"head_office": _ALL, "outlet_staff": {"read", "create", "write", "submit", "print"}},
    "production_guard": {"head_office": {"create"}},   # never stored (a flow's refusal of a batch cancel)
    "wage_sheet": {"head_office": {"read", "create", "write", "export", "print"}, "outlet_staff": {"create"}},
    "giro": {"finance_manager": {"read", "create", "write", "export", "print"}, "sales_manager": {"read", "create", "write", "export", "print"},
             "field_sales": _R},
    "acceptance": {"project_admin": _ALL, "sales_manager": _R, "finance_manager": _R},
    "assignment": {"project_admin": _ALL, "finance_manager": _R},                     # cost rates: not for sales (audit L1)
    "timesheet": {"project_admin": _ALL | {"import"}, "finance_manager": _R},
    "project": {"project_admin": _ALL, "sales_manager": {"read", "write"}, "finance_manager": {"read", "write"}},   # write for the billing flows
    "project_ledger": {"project_admin": {"read", "create", "export"}, "finance_manager": {"read", "create", "export"},
                       "sales_manager": {"read", "create"}, "purchasing_manager": {"read", "create"}},   # rows: revenue / vendor only
    "project_expense": {"project_admin": _ALL, "finance_manager": _ALL - {"delete"}, "sales_manager": _R, "purchasing_manager": _R},
    "project_guard": {"purchasing_manager": {"create"}, "finance_manager": {"create"}},
    "sales_invoice": {"finance_manager": _ALL, "sales_manager": _ALL, "sales_rep": _R, "project_admin": _R, "field_sales": _RP},
    "billing_milestone": {"project_admin": {"read", "create", "write", "export", "import"},
                          "finance_manager": {"read", "write", "export"}, "sales_manager": {"read", "write"}},
    "withholding": {"finance_manager": _ALL, "sales_manager": _RP},
    "bank_account": {"finance_manager": {"read", "create", "write", "delete", "export"}, "head_office": {"read", "create", "write", "delete", "export"},
                     "sales_manager": _R, "sales_rep": _R, "field_sales": _R, "project_admin": _R},
    "payment_instruction": {"finance_manager": {"read", "create", "export"}, "sales_manager": {"read", "create", "export"},
                            "head_office": _R, "sales_rep": _R, "project_admin": _R},
    "gateway_ref_claim": {"finance_manager": {"read", "create", "write", "export"}, "sales_manager": {"read", "create", "write", "export"},
                          "head_office": {"read", "create", "write"}},
    "invoice_reminder": {"finance_manager": {"read", "create", "write", "delete", "export"}, "sales_manager": {"read", "create", "write", "delete", "export"},
                         "sales_rep": {"read", "create"}},
    "bank_transaction_claim": {"finance_manager": {"read", "create", "write", "export"}, "sales_manager": {"read", "create", "write", "export"},
                               "head_office": {"read", "create", "write"}},
    "bank_transaction": {"finance_manager": {"read", "create", "write", "delete", "export", "import"}, "sales_manager": {"read", "create", "write", "export", "import"},
                         "head_office": {"read", "create", "write", "export", "import"}},
    "payment": {"finance_manager": _ALL, "sales_manager": _ALL, "head_office": {"read", "create", "write", "submit", "print"},
                "field_sales": _RP},   # head_office: if direction == 'pay'
    "purchase_request": {"purchasing_manager": _ALL, "project_admin": _RCWS, "head_office": _ALL, "outlet_staff": _RCWS},
    "purchase_order": {"purchasing_manager": _ALL, "finance_manager": _RP, "head_office": _ALL},
    "vendor_bill": {"purchasing_manager": _ALL, "finance_manager": {"read", "submit", "print"}, "head_office": _ALL},
    "goods_receipt": {"purchasing_manager": _ALL, "head_office": _ALL | {"import"}, "outlet_staff": _RCWS},
    "lead": _PIPE, "deal": _PIPE, "activity": _PIPE,
    "recipe": {"head_office": {"read", "create", "write", "delete", "export"}, "outlet_staff": _R},
    "stock_move": {"head_office": {"read", "export"}, "outlet_staff": _R},
    "stock_balance": {"head_office": {"read", "export"}, "outlet_staff": _R},
    "stock_transfer": {"head_office": _ALL, "outlet_staff": _R},
    "stock_transfer_receipt": {"head_office": _ALL, "outlet_staff": _RCWS | {"cancel"}},
    "stock_count": _STOCK_DOC, "stock_adjustment": _STOCK_DOC,
    "stock_adjustment_value": {"head_office": {"read", "create", "export"}, "outlet_staff": {"read", "create"}},
    "outlet_sales": {"head_office": _ALL | {"import"}, "outlet_staff": _RCWS},
    "outlet_takings": {"head_office": _ALL | {"import"}, "outlet_staff": _RCWS},
    "consignment_settlement": {"head_office": _ALL - {"delete"}},
}
_CHECKED = ("read", "create", "write", "delete", "submit", "cancel", "amend", "export", "import", "print")


def granted(role, model):
    return MATRIX.get(model, {}).get(role, set())


def setup_kopi_senja(api, k):
    """Members for the matrix check on the CRM site: a sales_manager and a sales_rep."""
    CTX.update(kernel=k, org="kopi-senja")
    pw = secrets.token_urlsafe(18)   # throwaway password of a throwaway database
    # the sales_rep is the fixture's USR-002, owner of LEAD-00031 (user.name in the own-records grant)
    for key, email, role, name, rec in (("sm", "manajer@kopisenja.test", "sales_manager", "Sari Contoh", "USR-003"), ("sr", "sales@kopisenja.test", "sales_rep", "Rina Wulandari", "USR-002")):
        code, out = k.user_add(email, role, CTX["org"], name, record=rec, password=pw)
        cl = Api.login(k.base, email, pw) if code == 0 else None
        if cl is None:
            CTX["login_error"] = f"{email}: " + (out if code else "login refused")
            fail("setup: add and sign in the members", CTX["login_error"])
            return
        CTX[key] = cl


# The members each site's setup signs in, with their role.
SITE_MEMBERS = {
    "kopi-senja": [("sm", "sales_manager"), ("sr", "sales_rep")],
    "demo-jasa": [("sm", "sales_manager"), ("fm", "finance_manager"), ("pm", "purchasing_manager"), ("pa", "project_admin")],
    "demo-fnb": [("hq", "head_office"), ("staff_dago", "outlet_staff")],
    "demo-ritel": [("hq", "head_office"), ("staff_cly", "outlet_staff")],
    "demo-grosir": [("sm", "sales_manager"), ("fm", "finance_manager"), ("hq", "head_office"), ("asep", "field_sales")],
    "demo-produksi": [("hq", "head_office"), ("staff", "outlet_staff")],
}


def money_bodies(api, site):
    """A valid create body per money model of the site (fixture names), for the allowed/forbidden create calls."""
    item = {"demo-fnb": "BRG-0001", "demo-jasa": "BRG-0001"}.get(site) or api.list("item")[0]["name"]
    customer = "CUST-0001" if site in ("demo-jasa", "demo-grosir") else (api.list("customer") or [{"name": ""}])[0]["name"]
    vendor = "VND-0002" if site != "kopi-senja" else ""
    outlet = {"outlet": DAGO} if site in ("demo-fnb", "demo-ritel", "demo-produksi") else {}
    b = {
        "quotation": {"customer": customer, "subject": "Uji izin", "items": [{"item": item, "qty": 1, "rate": 1_000_000}]},
        "customer_order": {"customer": customer, "po_number": "PO-IZIN", "po_date": "2026-11-03", "items": [{"item": item, "qty": 1, "rate": 1_000_000}]},
        "sales_invoice": invoice_body(customer, item),
        "payment": ({"direction": "pay", "vendor": vendor, "amount": 1_000} if site in ("demo-fnb", "demo-ritel", "demo-grosir", "demo-produksi") else {"direction": "receive", "customer": customer, "amount": 1_000}),
        "purchase_request": dict({"needed_by": "2026-11-30", "items": [{"item": item, "qty": 1, "est_rate": 1_000}]}, **outlet),
        "purchase_order": dict({"vendor": vendor, "items": [{"item": item, "qty": 1, "rate": 1_000}]}, **outlet),
        "vendor_bill": {"vendor": vendor, "bill_no": "IZIN-1", "bill_date": "2026-11-03", "items": [{"item": item, "qty": 1, "rate": 1_000}]},
        "goods_receipt": {"vendor": vendor, "outlet": DAGO if site in ("demo-fnb", "demo-ritel", "demo-produksi") else (api.list("outlet") or [{"name": ""}])[0]["name"], "items": [{"item": item, "qty": 1, "rate": 1_000}]},
        "outlet_sales": ({"outlet": DAGO, "date": "2026-11-04", "source": "moka", "moka_outlet": "Tahu Contoh - Dago", "lines": [{"item": "BRG-0007", "qty": 1}]} if site != "demo-ritel"
                         else {"outlet": CLY, "date": "2026-11-04", "source": "file", "lines": [{"item": "BRG-0001", "qty": 1}]}),
        "outlet_takings": {"outlet": CLY, "date": "2026-11-04", "lines": [{"method": "cash", "expected": 1_000, "received": 1_000}]},
        "consignment_settlement": {"consignor": "VND-0006", "month": "2026-01", "items": [{"item": "BRG-0193", "qty_received": 1, "qty_sold": 1}]},
    }
    return {m: v for m, v in b.items() if installed(api, m)}


def denied(st, resp):
    return st == 403 and isinstance(resp, dict) and resp.get("error", {}).get("code") == "permission_denied"


def scenario_crm_roles(api):
    """crm on kopi-senja: a sales_rep converts its own lead (crm.convert_lead makes the customer), a sales_manager wins the deal
    (crm.mark_won makes the draft invoice) and submits the invoice."""
    sm, sr = CTX.get("sm"), CTX.get("sr")
    if not (sm and sr):
        fail("crm members signed in", CTX.get("login_error"))
        return
    st, resp = sr.action("lead", "LEAD-00031", "convert")
    check("sales_rep converts its own lead (crm.convert_lead) -> 200", st == 200, (st, resp))
    if st != 200:
        return
    lead = api.get("lead", "LEAD-00031")
    cust, deal = lead.get("converted_customer"), lead.get("converted_deal")
    check("the conversion created a customer and a deal", bool(cust) and bool(deal), lead)
    st, c = sr.call("GET", f"/api/v1/r/customer/{Api.q(cust)}")
    check("sales_rep opens the customer the conversion created", st == 200 and c.get("customer_name") == "Kedai Kopi Andi", (st, c))
    st, d = sr.call("GET", f"/api/v1/r/deal/{Api.q(deal)}")
    check("sales_rep opens the deal (its own)", st == 200 and d.get("customer") == cust, (st, d))
    st, resp = sr.create("customer", {"customer_name": "Pelanggan dari sales"})
    check("sales_rep creates a customer", st == 201, (st, resp))
    st, resp = sr.call("GET", "/api/v1/r/item?limit=1")
    check("sales_rep reads items", st == 200, (st, resp))
    st, resp = sr.create("sales_invoice", invoice_body(cust, api.list("item")[0]["name"]))
    check("sales_rep may not create a sales_invoice -> 403", denied(st, resp), (st, resp))
    st, resp = sm.patch("deal", deal, {"stage": "won", "value": 2_000_000})
    check("sales_manager marks the deal won", st == 200, (st, resp))
    invs = [i for i in api.list("sales_invoice", limit=200) if str(i.get("notes", "")).startswith("Dari peluang " + deal)]
    check("crm.mark_won made one draft invoice for the deal's customer", len(invs) == 1 and invs[0]["customer"] == cust and invs[0]["docstatus"] == 0, invs)
    if len(invs) != 1:
        return
    st, resp = sm.patch("sales_invoice", invs[0]["name"], {"items": [{"item": api.list("item")[0]["name"], "qty": 1, "rate": 2_000_000}]})
    check("sales_manager fills the draft invoice", st == 200, (st, resp))
    st, resp = sm.verb("sales_invoice", invs[0]["name"], "submit", {"force": True})
    check("sales_manager submits the invoice", st == 200 and resp.get("docstatus") == 1, (st, resp))


def scenario_dashboards_crm(api):
    """crm sales_overview: the receivable widget and the unpaid list read `outstanding` (part-paid invoices count only their rest)."""
    ws = dashboard(api, "sales_overview", 3650)
    if not ws:
        return
    open_ = [i for i in api.find("sales_invoice", "docstatus == 1 && outstanding > 0")]
    check("setup: kopi-senja has open submitted invoices", bool(open_), open_)
    eq("sales_overview receivable = sum of outstanding of submitted invoices", num(ws["receivable"]), sum(i["outstanding"] for i in open_))
    rows = ws["unpaid"].get("rows") or ws["unpaid"].get("data") or []
    check("sales_overview unpaid list shows outstanding", bool(rows) and all("outstanding" in r for r in rows), ws["unpaid"])


def scenario_permissions(api):
    """Review item 2: /meta `can` of every model equals the matrix for each member, and each money model answers one allowed and one
    forbidden call per role with 2xx / 403 permission_denied."""
    site = CTX["kernel"].site
    members = [(key, role, CTX.get(key)) for key, role in SITE_MEMBERS[site]]
    if not all(cl for _, _, cl in members):
        fail("permission matrix: members signed in", CTX.get("login_error"))
        return
    st, meta = api.call("GET", "/api/v1/meta")
    models = (meta or {}).get("models", {})
    top = sorted(n for n, m in models.items() if not m.get("is_child"))
    missing = [n for n in top if n not in MATRIX]
    check("every top-level model of the site has a row in the permission matrix", not missing, missing)
    for key, role, cl in members:
        st, mm = cl.call("GET", "/api/v1/meta")
        wrong = []
        for n in top:
            can = ((mm or {}).get("models", {}).get(n) or {}).get("can", {})
            got = {a for a in _CHECKED if can.get(a)}
            want = granted(role, n) & set(_CHECKED)
            if got != want:
                wrong.append(f"{n}: got {sorted(got)} want {sorted(want)}")
        check(f"{role}: /meta can matches the matrix on all {len(top)} models", not wrong, wrong[:6])
    for model, body in money_bodies(api, site).items():
        for key, role, cl in members:
            g = granted(role, model)
            if "create" in g:
                st, resp = cl.create(model, body)
                if model == "outlet_sales" and role == "outlet_staff" and site == "demo-ritel":
                    # Cileunyi takes Juragan's POS file: a source file record is head office's import, so staff get 403
                    # (audit #7 S1 for file and loyverse), and a daily form there is refused by the sales-source rule
                    check(f"{role} may not create {model} with source file -> 403 permission_denied", denied(st, resp), (st, resp))
                    st, resp = cl.create(model, {"outlet": CLY, "date": "2026-11-04", "lines": [{"item": "BRG-0001", "qty": 1}]})
                    rules = [e.get("rule") for e in (resp or {}).get("errors", [])] if isinstance(resp, dict) else []
                    check(f"{role} may create {model} (permission passes; the POS-file outlet refuses the form by rule)", st == 422 and rules == ["source_matches_outlet"], (st, resp))
                    continue
                if model == "outlet_sales" and role == "outlet_staff":
                    # Dago uses Moka (one source per outlet) and Moka records are head office's, so the role is allowed
                    # to create but the sales-source rule refuses the daily form here: 422 from the rule, not 403
                    st, resp = cl.create(model, {"outlet": DAGO, "date": "2026-11-04", "lines": [{"item": "BRG-0007", "qty": 1}]})
                    rules = [e.get("rule") for e in (resp or {}).get("errors", [])] if isinstance(resp, dict) else []
                    check(f"{role} may create {model} (permission passes; the Moka outlet refuses the form by rule)", st == 422 and "form_only_for_non_moka_outlets" in rules, (st, resp))
                    continue
                check(f"{role} may create {model} -> 201", st == 201, (st, resp))
            elif "read" in g:
                st, resp = cl.call("GET", f"/api/v1/r/{model}?limit=1")
                check(f"{role} may list {model} -> 200", st == 200, (st, resp))
            if "read" not in g:
                st, resp = cl.call("GET", f"/api/v1/r/{model}?limit=1")
                check(f"{role} may not list {model} -> 403 permission_denied", denied(st, resp), (st, resp))
            elif "create" not in g:
                st, resp = cl.create(model, body)
                check(f"{role} may not create {model} -> 403 permission_denied", denied(st, resp), (st, resp))
            elif "delete" not in g:
                # a second consignment settlement needs another month (one live settlement per consignor and month)
                st, d = api.create(model, dict(body, month="2026-02") if model == "consignment_settlement" else body)
                st, resp = cl.call("DELETE", f"/api/v1/r/{model}/{Api.q(d['name'])}") if st == 201 else (st, d)
                check(f"{role} may not delete a draft {model} -> 403 permission_denied", denied(st, resp), (st, resp))
    if site == "demo-fnb":
        # the reviewer's probes: outlet staff on money documents and masters
        dago = CTX["staff_dago"]
        body = money_bodies(api, site)["vendor_bill"]
        st, resp = dago.create("vendor_bill", body)
        check("outlet_staff cannot create a vendor_bill -> 403", denied(st, resp), (st, resp))
        st, vb = api.create("vendor_bill", body)
        st, resp = dago.verb("vendor_bill", vb["name"], "submit")
        check("outlet_staff cannot submit a vendor_bill -> 403", denied(st, resp), (st, resp))
        st, resp = dago.create("payment", {"direction": "pay", "vendor": "VND-0002", "amount": 1_000_000, "allocations": [{"vendor_bill": vb["name"], "amount": 1_000_000}]})
        check("outlet_staff cannot create a vendor payment -> 403", denied(st, resp), (st, resp))
        st, p = api.create("payment", {"direction": "pay", "vendor": "VND-0002", "amount": 1_000})
        st, resp = dago.verb("payment", p["name"], "submit")
        check("outlet_staff cannot submit a payment -> 403", denied(st, resp), (st, resp))
        st, resp = dago.patch("item", "BRG-0001", {"standard_cost": 1})
        check("outlet_staff cannot change item.standard_cost -> 403", denied(st, resp), (st, resp))
        st, resp = dago.patch("uom_conversion", "KONV-0001", {"factor": 1})
        check("outlet_staff cannot change uom_conversion.factor -> 403", denied(st, resp), (st, resp))
        st, resp = dago.call("GET", "/api/v1/r/payment")
        check("outlet_staff cannot list payments -> 403", denied(st, resp), (st, resp))
        hq = CTX["hq"]
        st, cust = api.create("customer", {"customer_name": "Pelanggan uji izin"})
        st, resp = hq.create("payment", {"direction": "receive", "customer": cust.get("name"), "amount": 1_000})
        check("head_office cannot create a receive payment (grant only if direction == 'pay') -> 403", denied(st, resp), (st, resp))
        st, p = hq.create("payment", {"direction": "pay", "vendor": "VND-0002", "amount": 1_000})
        check("head_office creates a vendor payment", st == 201, (st, p))
        if st == 201:
            st, resp = hq.verb("payment", p["name"], "submit")
            check("head_office submits the vendor payment", st == 200, (st, resp))
            st, resp = hq.verb("payment", resp.get("name", p["name"]), "cancel")
            check("head_office cannot cancel a payment (r+cw+s+p) -> 403", denied(st, resp), (st, resp))


# ---------- scenarios: print formats (contract 0008), both PTs of demo-jasa

import html as htmllib
import re


def page_text(raw):
    """Visible text of a print page: tags dropped, entities decoded, whitespace collapsed (so `Rp 11.100.000` can be matched)."""
    body = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    return re.sub(r"\s+", " ", htmllib.unescape(body)).replace("\xa0", " ")


def check_print(label, api, model, name, fmt, must, must_not=()):
    """GET the HTML print and assert status 200, HTML content type and the visible strings in `must` / not in `must_not`."""
    st, raw, ctype = api.print_html(model, name, fmt)
    if not check(f"{label}: print answers 200 text/html", st == 200 and ctype.startswith("text/html"), (st, ctype, raw[:200])):
        return ""
    text = page_text(raw)
    for needle in must:
        check(f"{label}: shows {needle!r}", needle in text, text[:600])
    for needle in must_not:
        check(f"{label}: does not show {needle!r}", needle not in text, needle)
    return text


def aging_cells(text):
    """The five aging amounts of the statement (belum jatuh tempo, 1-30, 31-60, 61-90, > 90) as ints."""
    m = re.search(r"> 90 hari ((?:-?Rp [\d.]+ ?){5})", text)
    return [int(x.replace("Rp ", "").replace(".", "")) for x in re.findall(r"-?Rp [\d.]+", m.group(1))] if m else None


def scenario_prints_jasa(api):
    import datetime
    org2 = Api.login(CTX["kernel"].base, CTX["kernel"].owner_email, CTX["kernel"].owner_pw, org="cv_contoh_pelatihan")
    customer, ndt = "CUST-0001", services(api)[0]["name"]

    # quotation (PKP org): PPN row from the document's own tax figures
    st, q = api.create("quotation", {"customer": customer, "subject": "Inspeksi uji cetak", "items": [{"item": ndt, "qty": 10, "rate": 1_000_000}]})
    check("print: create quotation", st == 201, q)
    st, resp = api.verb("quotation", q["name"], "submit")
    check("print: submit quotation", st == 200, resp)
    qn = resp.get("name", q["name"])
    check_print("quotation (PT Contoh Inspeksi)", api, "quotation", qn, "quotation",
                [qn, "Surat Penawaran Harga", "Rp 10.000.000", "Rp 1.100.000", "Rp 11.100.000", "sebelas juta seratus ribu rupiah", "PPN 12%", "PT Contoh Inspeksi"])

    # sales_invoice, PKP org: 10.000.000 + PPN 1.100.000
    st, inv = api.create("sales_invoice", invoice_body(customer, ndt))
    check("print: create PKP invoice", st == 201, inv)
    st, resp = api.verb("sales_invoice", inv["name"], "submit", {"force": True})
    check("print: submit PKP invoice", st == 200, resp)
    invn = resp.get("name", inv["name"])
    check("PKP invoice number follows INV/{ORG}/{YY}/{####} with the org code", re.fullmatch(r"INV/CIN/\d\d/\d{4}", invn) is not None, invn)
    check_print("sales_invoice (PKP org)", api, "sales_invoice", invn, "",
                [invn, "Rp 10.000.000", "PPN 12%", "Rp 1.100.000", "Rp 11.100.000", "sebelas juta seratus ribu rupiah", "PT Contoh Inspeksi", "00.000.000.0-000.001", "e-Faktur"])
    # the rate and the DPP come from the document: an 11% invoice prints 11%, not a fixed 12%
    st, inv11 = api.create("sales_invoice", invoice_body(customer, ndt, tax_rate=11))
    check("print: create 11% invoice", st == 201, inv11)
    eq("11% invoice ppn = round(10.000.000 x 11/12 x 11%)", inv11.get("ppn"), 1_008_333)
    st, resp = api.verb("sales_invoice", inv11["name"], "submit", {"force": True})
    n11 = resp.get("name", inv11["name"])
    t11 = check_print("sales_invoice with tax_rate 11 (rate from the document)", api, "sales_invoice", n11, "",
                      ["PPN 11%", "Rp 1.008.333", "Rp 11.008.333"], must_not=["PPN 12%"])
    # a DP deduction lowers the DPP the invoice prints
    st, dpi = api.create("sales_invoice", invoice_body(customer, ndt, rate=3_000_000, kind="down_payment"))
    st, resp = api.verb("sales_invoice", dpi["name"], "submit", {"force": True})
    dpn = resp.get("name", dpi["name"])
    st, reg = api.create("sales_invoice", invoice_body(customer, ndt, dp_invoice=dpn, down_payment=3_000_000))
    check("print: create invoice with DP deduction", st == 201, reg)
    st, resp = api.verb("sales_invoice", reg["name"], "submit", {"force": True})
    rn = resp.get("name", reg["name"])
    check_print("sales_invoice with DP deduction (DPP 7.000.000)", api, "sales_invoice", rn, "",
               ["Uang muka", "Rp 3.000.000", "Rp 7.000.000", "Rp 770.000", "Rp 7.770.000", "tujuh juta tujuh ratus tujuh puluh ribu rupiah"])

    # purchase_order (the PO's own ppn and totals)
    st, po = api.create("purchase_order", {"vendor": "VND-0001", "order_date": "2026-11-03", "payment_terms": "Net 30",
                                           "items": [{"item": "BRG-0004", "qty": 20, "rate": 85_000}, {"item": "BRG-0005", "qty": 100, "rate": 12_000}]})
    check("print: create purchase_order", st == 201, po)
    if st == 201:
        st, resp = api.verb("purchase_order", po["name"], "submit")
        check("print: submit purchase_order", st == 200, resp)
        pon = resp.get("name", po["name"])
        check("PO number follows PO/{ORG}/{YY}{MM}/{###}", re.fullmatch(r"PO/CIN/\d{4}/\d{3}", pon) is not None, pon)
        total = api.get("purchase_order", pon)["grand_total"]
        check_print("purchase_order", api, "purchase_order", pon, "",
                    [pon, "Pesanan pembelian", "PT Contoh Bahan Uji", "Rp 2.900.000", "Rp 319.000", "Rp 3.219.000", "tiga juta dua ratus sembilan belas ribu rupiah"])
        eq("PO grand_total 3.219.000", total, 3_219_000)

    # statement of account: three open invoices, one per aging bucket (belum jatuh tempo, 1-30, 31-60)
    st, cust = api.create("customer", {"customer_name": "PT Contoh Piutang", "address": "Jl. Contoh Piutang 1", "payment_days": 30})
    check("print: create SOA customer", st == 201, cust)
    today = datetime.date.today()
    open_ids = {}
    for tag, days_past_due in (("current", -5), ("d30", 10), ("d60", 45)):
        due = today - datetime.timedelta(days=days_past_due)
        posted = due - datetime.timedelta(days=30)
        st, i = api.create("sales_invoice", invoice_body(cust["name"], ndt, posting_date=posted.isoformat(), due_date=due.isoformat()))
        check(f"SOA: create invoice {tag}", st == 201, i)
        st, resp = api.verb("sales_invoice", i["name"], "submit", {"force": True})
        check(f"SOA: submit invoice {tag}", st == 200, resp)
        open_ids[tag] = resp.get("name", i["name"])
    # settle the current one so the list shows open invoices only
    st, pay = api.create("payment", {"direction": "receive", "customer": cust["name"], "amount": 11_100_000, "allocations": [{"sales_invoice": open_ids["current"], "amount": 11_100_000}]})
    st, resp = api.verb("payment", pay["name"], "submit")
    check("SOA: submit the payment of the current invoice", st == 200, (st, resp))
    paid = api.get("sales_invoice", open_ids["current"])["outstanding"] == 0
    if not paid:
        fail("SOA: payments.allocate settles the invoice", "kernel (S8c hooks) is merged on main: hook did not run; the statement lists the invoice as open")
    soa = check_print("statement (customer)", api, "customer", cust["name"], "statement",
                      ["PT Contoh Piutang", open_ids["d30"], open_ids["d60"], "Umur piutang", "Total sisa tagihan"])
    cells = aging_cells(soa)
    if paid:
        eq("SOA aging buckets [not due, 1-30, 31-60, 61-90, >90] after the payment", cells, [0, 11_100_000, 11_100_000, 0, 0])
        check("SOA lists open invoices only (the paid one is not shown)", open_ids["current"] not in soa, open_ids["current"])
        check("SOA total 22.200.000", "Total sisa tagihan Rp 22.200.000" in soa, soa[-500:])
    else:
        eq("SOA aging buckets [not due, 1-30, 31-60, 61-90, >90]", cells, [11_100_000, 11_100_000, 11_100_000, 0, 0])

    # non-PKP org (CV Contoh Pelatihan): its own code, numbering and letterhead, and no PPN row
    if org2 is None:
        fail("sign in as the owner of CV Contoh Pelatihan", "login refused")
        return
    st, me = org2.call("GET", "/auth/me")
    eq("owner belongs to both PTs", sorted(o["id"] for o in (me or {}).get("orgs", [])), ["cv_contoh_pelatihan", "pt_contoh_inspeksi"])
    eq("current org is cv_contoh_pelatihan with code CPL", ((me or {}).get("org") or {}).get("code"), "CPL")
    eq("CV org sees its own customer only", [c["customer_name"] for c in org2.list("customer")], ["PT Contoh Peserta Mandiri"])
    st, _ = org2.call("GET", "/api/v1/r/sales_invoice/" + Api.q(invn))
    check("an invoice of PT Contoh Inspeksi is 404 in CV Contoh Pelatihan (records never cross orgs)", st == 404, st)
    item2 = org2.list("item")[0]["name"]
    st, inv2 = org2.create("sales_invoice", invoice_body("CUST-0001", item2))
    check("print: create non-PKP invoice", st == 201, inv2)
    eq("non-PKP invoice grand_total 10.000.000", inv2.get("grand_total"), 10_000_000)
    st, resp = org2.verb("sales_invoice", inv2["name"], "submit", {"force": True})
    check("print: submit non-PKP invoice", st == 200, resp)
    inv2n = resp.get("name", inv2["name"])
    check("non-PKP invoice number follows KW-{ORG}-{YY}{MM}-{###}", re.fullmatch(r"KW-CPL-\d{4}-\d{3}", inv2n) is not None, inv2n)
    check_print("sales_invoice (non-PKP org)", org2, "sales_invoice", inv2n, "",
                [inv2n, "Rp 10.000.000", "sepuluh juta rupiah", "CV Contoh Pelatihan", "00.000.000.0-000.002"],
                must_not=["PPN", "e-Faktur", "Rp 11.100.000", "PT Contoh Inspeksi"])
    st, q2 = org2.create("quotation", {"customer": "CUST-0001", "subject": "Pelatihan K3", "items": [{"item": item2, "qty": 1, "rate": 10_000_000}]})
    st, resp = org2.verb("quotation", q2["name"], "submit")
    q2n = resp.get("name", q2["name"])
    check_print("quotation (non-PKP org)", org2, "quotation", q2n, "quotation", [q2n, "Rp 10.000.000", "sepuluh juta rupiah", "CV Contoh Pelatihan"], must_not=["PPN"])


# ---------- scenarios: PPN from config and org, stored per document (task 10c)

LETTERHEADS = {   # org -> (legal name, address line, bank account) of examples/sites/*/letterheads/<org>.rk
    "pt_contoh_inspeksi": ("PT Contoh Inspeksi", "Jl. Contoh Industri No. 1", "111 000 0001"),
    "cv_contoh_pelatihan": ("CV Contoh Pelatihan", "Jl. Contoh Pendidikan No. 5", "222 000 0002"),
    "tahu_contoh": ("PT Tahu Contoh Nusantara", "Jl. Contoh Dago No. 1", "333 000 0003"),
}


def check_org_letterhead(api, org, vendor, customer, item):
    """A PO and an invoice of one PT print that PT's own name, address and bank, and no other PT's (a listed PT never uses the site letterhead)."""
    name, addr, bank = LETTERHEADS[org]
    others = [v for k, v in LETTERHEADS.items() if k != org]
    must_not = [o[0] for o in others] + [o[2] for o in others] + ["Contoh Jasa Teknik"]
    for model, body in (("purchase_order", {"vendor": vendor, "items": [{"item": item, "qty": 1, "rate": 100_000}]}),
                        ("sales_invoice", invoice_body(customer, item, rate=100_000))):
        st, doc = api.create(model, body)
        check(f"{org}: create {model} for the letterhead check", st == 201, (st, doc))
        if st != 201:
            continue
        st, resp = api.verb(model, doc["name"], "submit", {"force": True})
        check(f"{org}: submit {model}", st == 200, (st, resp))
        n = resp.get("name", doc["name"])
        must = [name, addr] + ([bank] if model == "sales_invoice" else [])
        check_print(f"{org} {model}: its own letterhead", api, model, n, "", must, must_not=must_not)


def scenario_tax_jasa(api):
    """PKP org vs non-PKP org vs PKP vendor: the rate, DPP ratio and PPN are stored on each document."""
    org2 = Api.login(CTX["kernel"].base, CTX["kernel"].owner_email, CTX["kernel"].owner_pw, org="cv_contoh_pelatihan")
    customer, ndt = "CUST-0001", services(api)[0]["name"]
    # PKP org (PT Contoh Inspeksi): tax.pkp true
    st, inv = api.create("sales_invoice", invoice_body(customer, ndt))
    check("PKP org invoice created", st == 201, inv)
    eq("PKP org invoice: tax_rate 12, dpp 11/12, ppn 1.100.000", (inv.get("tax_rate"), inv.get("dpp_num"), inv.get("dpp_den"), inv.get("ppn")), (12, 11, 12, 1_100_000))
    st, q = api.create("quotation", {"customer": customer, "subject": "Pajak", "items": [{"item": ndt, "qty": 1, "rate": 10_000_000}]})
    eq("PKP org quotation: tax_rate 12, ppn 1.100.000", (q.get("tax_rate"), q.get("ppn")), (12, 1_100_000))
    st, resp = api.verb("sales_invoice", inv["name"], "submit", {"force": True})
    check("submit the PKP invoice", st == 200, resp)
    stored = api.get("sales_invoice", resp.get("name", inv["name"]))
    eq("a submitted invoice keeps its stored tax_rate, DPP ratio and ppn", (stored["tax_rate"], stored["dpp_num"], stored["dpp_den"], stored["ppn"]), (12, 11, 12, 1_100_000))
    # manpower: wages on their own line are not PPh 23 base, the management fee is (pph23_exclude per line)
    st, mp = api.create("sales_invoice", {"customer": customer, "pph23_applies": True, "items": [
        {"item": ndt, "qty": 1, "rate": 10_000_000, "description": "Gaji tenaga kerja", "pph23_exclude": True},
        {"item": ndt, "qty": 1, "rate": 1_000_000, "description": "Management fee"}]})
    check("manpower invoice (wages 10.000.000 untaxed for PPh 23, fee 1.000.000) created", st == 201, (st, mp))
    if st == 201:
        eq("manpower invoice: dpp 11.000.000, ppn 1.210.000, grand_total 12.210.000", (mp["dpp"], mp["ppn"], mp["grand_total"]), (11_000_000, 1_210_000, 12_210_000))
        eq("manpower invoice: PPh 23 base 1.000.000 (the fee), PPh 23 20.000, amount_due 12.190.000", (mp["pph23_dpp"], mp["pph23_amount"], mp["amount_due"]), (1_000_000, 20_000, 12_190_000))
        eq("lines default to pph23_exclude false; the wage line keeps true", [l["pph23_exclude"] in (True, "true") for l in mp["items"]], [True, False])
    st, dsc = api.create("sales_invoice", {"customer": customer, "pph23_applies": True, "discount_amount": 1_100_000, "items": [
        {"item": ndt, "qty": 1, "rate": 10_000_000, "pph23_exclude": True}, {"item": ndt, "qty": 1, "rate": 1_000_000}]})
    if st == 201:
        eq("header discount spreads pro rata: dpp 9.900.000, PPh 23 base 900.000, PPh 23 18.000", (dsc["dpp"], dsc["pph23_dpp"], dsc["pph23_amount"]), (9_900_000, 900_000, 18_000))
    else:
        fail("discounted manpower invoice created", (st, dsc))
    eq("an invoice with no line excluded: PPh 23 base = dpp", inv.get("pph23_dpp"), inv.get("dpp"))
    # down payment: the DP invoice splits wages and fee too; the final invoice deducts the DP's base, so the contract's
    # total PPh 23 base is the fee (11.000.000 contract: wages 10.000.000, fee 1.000.000; DP 30% = 3.300.000)
    st, dp = api.create("sales_invoice", {"customer": customer, "kind": "down_payment", "pph23_applies": True, "items": [
        {"item": ndt, "qty": 1, "rate": 3_000_000, "description": "DP 30% gaji", "pph23_exclude": True},
        {"item": ndt, "qty": 1, "rate": 300_000, "description": "DP 30% fee"}]})
    dpn = submit_doc(api, "sales_invoice", dp["name"], "submit the DP invoice (wages and fee split)") if st == 201 else None
    if dpn:
        eq("DP invoice PPh 23 base = its fee line 300.000", api.get("sales_invoice", dpn)["pph23_dpp"], 300_000)
        st, fin = api.create("sales_invoice", {"customer": customer, "pph23_applies": True, "dp_invoice": dpn, "down_payment": 3_300_000, "items": [
            {"item": ndt, "qty": 1, "rate": 10_000_000, "description": "Gaji", "pph23_exclude": True},
            {"item": ndt, "qty": 1, "rate": 1_000_000, "description": "Fee"}]})
        if check("final invoice deducting the DP", st == 201, (st, fin)):
            eq("final invoice base = fee 1.000.000 - DP base 300.000 = 700.000 (contract total = fee)", (fin["pph23_dpp"], fin["pph23_amount"]), (700_000, 14_000))
        # a DP left all-base, then wages excluded on the final: base clamps to 0 and the save warns
        st, dp2 = api.create("sales_invoice", {"customer": customer, "kind": "down_payment", "pph23_applies": True, "items": [{"item": ndt, "qty": 1, "rate": 3_000_000}]})
        dp2n = submit_doc(api, "sales_invoice", dp2["name"], "submit an all-base DP invoice") if st == 201 else None
        if dp2n:
            st, w = api.create("sales_invoice", {"customer": customer, "pph23_applies": True, "dp_invoice": dp2n, "down_payment": 3_000_000, "items": [
                {"item": ndt, "qty": 1, "rate": 10_000_000, "pph23_exclude": True}, {"item": ndt, "qty": 1, "rate": 1_000_000}]})
            check("DP base above the final's line base: saved with a warning 'Dasar PPh 23 di faktur uang muka lebih besar'",
                  st == 201 and any("faktur uang muka lebih besar" in str(x) for x in (w.get("warnings") or w.get("_warnings") or [])), (st, {k: w.get(k) for k in ("pph23_dpp", "warnings", "_warnings")}))
    if mp.get("name"):
        check_print("manpower invoice print names the PPh 23 base", api, "sales_invoice", mp["name"], "", ["PPh 23 dipotong pelanggan (dasar Rp 1.000.000)", "Rp 20.000", "Rp 12.190.000"])
    st, fx = api.create("sales_invoice", {"customer": customer, "currency": "USD", "exchange_rate": 16000, "pph23_applies": True, "items": [
        {"item": ndt, "qty": 1, "rate_fx": 900, "pph23_exclude": True}, {"item": ndt, "qty": 1, "rate_fx": 100}]})
    if st == 201:
        eq("USD invoice 900 untaxed + 100 fee at 16.000: PPh 23 base 1.600.000, PPh 23 32.000, PPh 23 (valas) 2",
           (fx["pph23_dpp"], fx["pph23_amount"], float(fx["pph23_amount_fx"])), (1_600_000, 32_000, 2.0))
    else:
        fail("USD manpower invoice created", (st, fx))
    # a USD DP (150 wages excluded + 50 fee at 16.000 = 3.200.000, base 800.000) deducted in full by a rupiah final
    # (wages 10.000.000 excluded + fee 2.000.000): base 2.000.000 - 800.000 = 1.200.000, PPh 23 24.000
    st, dpx = api.create("sales_invoice", {"customer": customer, "kind": "down_payment", "currency": "USD", "exchange_rate": 16000, "pph23_applies": True, "items": [
        {"item": ndt, "qty": 1, "rate_fx": 150, "pph23_exclude": True}, {"item": ndt, "qty": 1, "rate_fx": 50}]})
    dpxn = submit_doc(api, "sales_invoice", dpx["name"], "submit a USD DP invoice") if st == 201 else None
    if dpxn:
        st, fin = api.create("sales_invoice", {"customer": customer, "pph23_applies": True, "dp_invoice": dpxn, "down_payment": 3_200_000, "items": [
            {"item": ndt, "qty": 1, "rate": 10_000_000, "pph23_exclude": True}, {"item": ndt, "qty": 1, "rate": 2_000_000}]})
        eq("rupiah final deducting a USD DP: DP base 800.000, base 1.200.000, PPh 23 24.000",
           (fin.get("pph23_dp_base"), fin.get("pph23_dpp"), fin.get("pph23_amount")), (800_000, 1_200_000, 24_000))
    # large foreign amounts (a Decimal product above 10^16 is stored as 0 by the kernel): a USD DP 10.000 x 15.000 =
    # 150.000.000 deducted by a final of 500.000.000: base 350.000.000, PPh 23 7.000.000; a KRW invoice 40.000.000 x 11 =
    # 440.000.000: PPh 23 8.800.000, in won 800.000
    st, dpb = api.create("sales_invoice", {"customer": customer, "kind": "down_payment", "currency": "USD", "exchange_rate": 15000, "pph23_applies": True,
                                           "items": [{"item": ndt, "qty": 1, "rate_fx": 10_000}]})
    dpbn = submit_doc(api, "sales_invoice", dpb["name"], "submit a USD DP invoice of 150.000.000") if st == 201 else None
    if dpbn:
        st, fin = api.create("sales_invoice", {"customer": customer, "pph23_applies": True, "dp_invoice": dpbn, "down_payment": 150_000_000,
                                              "items": [{"item": ndt, "qty": 1, "rate": 500_000_000}]})
        eq("final of 500.000.000 deducting a USD DP of 150.000.000: DP base 150.000.000, base 350.000.000, PPh 23 7.000.000",
           (fin.get("pph23_dp_base"), fin.get("pph23_dpp"), fin.get("pph23_amount")), (150_000_000, 350_000_000, 7_000_000))
    st, krw = api.create("sales_invoice", {"customer": customer, "currency": "KRW", "exchange_rate": 11, "pph23_applies": True,
                                           "items": [{"item": ndt, "qty": 1, "rate_fx": 40_000_000}]})
    eq("KRW invoice 40.000.000 x 11: PPh 23 base 440.000.000, PPh 23 8.800.000, PPh 23 (valas) 800.000",
       (krw.get("pph23_dpp"), krw.get("pph23_amount"), float(krw.get("pph23_amount_fx") or 0)), (440_000_000, 8_800_000, 800_000.0))
    # a down payment invoice's own amount is its dpp / grand_total
    st, dp = api.create("sales_invoice", invoice_body(customer, ndt, rate=3_000_000, kind="down_payment"))
    eq("down_payment invoice: dpp 3.000.000, ppn 330.000, grand_total 3.330.000", (dp.get("dpp"), dp.get("ppn"), dp.get("grand_total")), (3_000_000, 330_000, 3_330_000))
    # purchase side: the vendor's pkp decides
    st, vb = api.create("vendor_bill", {"vendor": "VND-0001", "bill_no": "T-1", "bill_date": "2026-11-10", "items": [{"item": "BRG-0004", "qty": 10, "rate": 100_000}]})
    eq("vendor_bill from a PKP vendor: tax_rate 12, ppn 110.000", (vb.get("tax_rate"), vb.get("dpp"), vb.get("ppn")), (12, 1_000_000, 110_000))
    st, vb = api.create("vendor_bill", {"vendor": "VND-0002", "bill_no": "T-2", "bill_date": "2026-11-10", "items": [{"item": "BRG-0004", "qty": 10, "rate": 100_000}]})
    eq("vendor_bill from a non-PKP vendor: tax_rate 0, ppn 0", (vb.get("tax_rate"), vb.get("ppn")), (0, 0))
    st, po = api.create("purchase_order", {"vendor": "VND-0001", "tax_rate": 5, "items": [{"item": "BRG-0004", "qty": 10, "rate": 100_000}]})
    st, vb = api.create("vendor_bill", {"vendor": "VND-0001", "purchase_order": po["name"], "bill_no": "T-4", "bill_date": "2026-11-10", "items": [{"item": "BRG-0004", "qty": 10, "rate": 100_000}]})
    eq("vendor_bill linked to a purchase_order takes its tax_rate and DPP factor", (vb.get("tax_rate"), vb.get("dpp_num"), vb.get("dpp_den")), (5, po.get("dpp_num"), po.get("dpp_den")))
    # bounds: the DPP factor and the rate cannot be set to nonsense on any taxed document
    bad = {"quotation": {"customer": customer, "subject": "x", "items": [{"item": ndt, "qty": 1, "rate": 1_000_000}]},
           "sales_invoice": invoice_body(customer, ndt),
           "purchase_order": {"vendor": "VND-0001", "items": [{"item": "BRG-0004", "qty": 1, "rate": 100_000}]},
           "vendor_bill": {"vendor": "VND-0001", "bill_no": "T-5", "bill_date": "2026-11-10", "items": [{"item": "BRG-0004", "qty": 1, "rate": 100_000}]}}
    for model, body in bad.items():
        for label, extra in (("dpp_den 0", {"dpp_den": 0}), ("dpp_num above dpp_den", {"dpp_num": 13}), ("tax_rate 120", {"tax_rate": 120}), ("tax_rate -1", {"tax_rate": -1})):
            st, resp = api.create(model, dict(body, **extra))
            check(f"{model} with {label} -> 422 'Tarif PPN atau faktor DPP tidak valid'", st == 422 and any("faktor DPP tidak valid" in m for m in messages(resp)), (st, resp))
    # ledger.post_receivable guard (reads down_payment and dpp): two deductions of 2.000.000 from a 3.000.000 DP
    st, dp = api.create("sales_invoice", invoice_body(customer, ndt, rate=3_000_000, kind="down_payment"))
    st, resp = api.verb("sales_invoice", dp["name"], "submit", {"force": True})
    dpn = resp.get("name", dp["name"])
    names = []
    for i in (1, 2):
        st, reg = api.create("sales_invoice", invoice_body(customer, ndt, dp_invoice=dpn, down_payment=2_000_000))
        check(f"DP guard: create regular invoice {i} deducting 2.000.000", st == 201, (st, reg))
        names.append(reg.get("name"))
    st, resp = api.verb("sales_invoice", names[0], "submit", {"force": True})
    check("DP guard: the first deduction of 2.000.000 submits", st == 200, (st, resp))
    st, resp = api.verb("sales_invoice", names[1], "submit", {"force": True})
    check("DP guard: the second deduction (4.000.000 in total over a 3.000.000 DP) -> 409 dp_over_deducted",
          st == 409 and resp["error"]["code"] == "dp_over_deducted", (st, resp))
    # non-PKP org (CV Contoh Pelatihan): tax.pkp false in its orgs entry
    if org2 is None:
        fail("sign in as the owner of CV Contoh Pelatihan", "login refused")
        return
    item2 = org2.list("item")[0]["name"]
    st, i2 = org2.create("sales_invoice", invoice_body("CUST-0001", item2))
    eq("non-PKP org invoice: tax_rate 0, ppn 0, grand_total = dpp", (i2.get("tax_rate"), i2.get("ppn"), i2.get("grand_total")), (0, 0, 10_000_000))
    eq("non-PKP org invoice still stores the DPP ratio 11/12", (i2.get("dpp_num"), i2.get("dpp_den")), (11, 12))
    st, q2 = org2.create("quotation", {"customer": "CUST-0001", "subject": "Pajak", "items": [{"item": item2, "qty": 1, "rate": 10_000_000}]})
    eq("non-PKP org quotation: tax_rate 0, ppn 0", (q2.get("tax_rate"), q2.get("ppn")), (0, 0))
    st, vb2 = org2.create("vendor_bill", {"vendor": "VND-0001", "bill_no": "T-3", "bill_date": "2026-11-10", "items": [{"item": item2, "qty": 1, "rate": 100_000}]})
    eq("non-PKP org, non-PKP vendor: vendor_bill tax_rate 0", (vb2.get("tax_rate"), vb2.get("ppn")), (0, 0))
    # review item 4: a non-PKP seller cannot charge PPN, and a non-PKP vendor cannot be charged it
    for model, body in (("sales_invoice", invoice_body("CUST-0001", item2, tax_rate=12)),
                        ("quotation", {"customer": "CUST-0001", "subject": "Pajak", "tax_rate": 12, "items": [{"item": item2, "qty": 1, "rate": 1_000_000}]})):
        st, resp = org2.create(model, body)
        check(f"non-PKP org {model} with tax_rate 12 -> 422", st == 422 and any("bukan PKP" in m for m in messages(resp)), (st, resp))
    st, resp = api.create("purchase_order", {"vendor": "VND-0002", "tax_rate": 12, "items": [{"item": "BRG-0004", "qty": 1, "rate": 100_000}]})
    check("purchase_order from a non-PKP vendor with tax_rate 12 -> 422", st == 422 and any("bukan PKP" in m for m in messages(resp)), (st, resp))
    st, resp = api.create("vendor_bill", {"vendor": "VND-0002", "tax_rate": 12, "bill_no": "T-6", "bill_date": "2026-11-10", "items": [{"item": "BRG-0004", "qty": 1, "rate": 100_000}]})
    check("vendor_bill from a non-PKP vendor (no PO) with tax_rate 12 -> 422", st == 422 and any("bukan PKP" in m for m in messages(resp)), (st, resp))
    st, resp = api.create("vendor_bill", {"vendor": "VND-0001", "bill_no": "T-8", "bill_date": "2026-11-10", "items": [{"item": "BRG-0004", "qty": 1, "rate": -100_000}]})
    check("vendor_bill with a negative line rate -> 422", st == 422 and any("Harga tidak boleh negatif" in m for m in messages(resp)), (st, resp))
    st, po0 = api.create("purchase_order", {"vendor": "VND-0001", "tax_rate": 0, "items": [{"item": "BRG-0004", "qty": 1, "rate": 100_000}]})
    st, resp = api.create("vendor_bill", {"vendor": "VND-0001", "purchase_order": po0["name"], "tax_rate": 12, "bill_no": "T-7", "bill_date": "2026-11-10", "items": [{"item": "BRG-0004", "qty": 1, "rate": 100_000}]})
    check("vendor_bill with PPN against a PO without PPN -> 422", st == 422 and any("bukan PKP" in m for m in messages(resp)), (st, resp))
    check_org_letterhead(api, "pt_contoh_inspeksi", "VND-0001", customer, ndt)
    check_org_letterhead(org2, "cv_contoh_pelatihan", "VND-0001", "CUST-0001", item2)
    ppn_rate_11_variant()


def ppn_rate_11_variant():
    """A site whose PKP org sets tax ppn_rate 11: documents get 11, ppn = round(10.000.000 x 11/12 x 11/100) = 1.008.333, the print shows 11%,
    and after the config goes back to 12 the submitted invoice keeps 11 while a new one gets 12."""
    import shutil
    state, k = None, None
    try:
        state = tempfile.mkdtemp(prefix="day-one-ppn11-")
        site_dir = os.path.join(state, "site")
        shutil.copytree(os.path.join(ROOT, "examples", "sites", "demo-jasa"), site_dir)
        cfg = os.path.join(site_dir, "config.rk")
        text = open(cfg).read()
        assert "      tax: {pkp: true}" in text
        open(cfg, "w").write(text.replace("      tax: {pkp: true}", "      tax: {pkp: true, ppn_rate: 11}"))
        k = Kernel("demo-jasa", state, site_dir=site_dir)
        k.start()
        api = Api(k.base)
        customer, ndt = "CUST-0001", services(api)[0]["name"]
        st, inv = api.create("sales_invoice", invoice_body(customer, ndt))
        check("ppn_rate 11 site: create invoice", st == 201, inv)
        eq("ppn_rate 11: tax_rate 11, DPP still 11/12, ppn 1.008.333", (inv.get("tax_rate"), inv.get("dpp_num"), inv.get("dpp_den"), inv.get("ppn")), (11, 11, 12, 1_008_333))
        st, resp = api.verb("sales_invoice", inv["name"], "submit", {"force": True})
        check("ppn_rate 11 site: submit invoice", st == 200, resp)
        n11 = resp.get("name", inv["name"])
        check_print("ppn_rate 11 invoice", api, "sales_invoice", n11, "", ["PPN 11%", "Rp 1.008.333", "Rp 11.008.333", "11/12"], must_not=["PPN 12%"])
        st, po = api.create("purchase_order", {"vendor": "VND-0001", "items": [{"item": ndt, "qty": 1, "rate": 10_000_000}]})
        eq("ppn_rate 11: a PO from a PKP vendor gets 11 too", (po.get("tax_rate"), po.get("ppn")), (11, 1_008_333))
        # the config changes back to 12: stored documents are not recomputed
        k.stop_serve()
        open(cfg, "w").write(text)
        k.serve()
        kept = api.get("sales_invoice", n11)
        eq("config back to 12: the submitted invoice keeps tax_rate 11, its DPP factor 11/12 and ppn 1.008.333", (kept["tax_rate"], kept["dpp_num"], kept["dpp_den"], kept["ppn"]), (11, 11, 12, 1_008_333))
        st, inv12 = api.create("sales_invoice", invoice_body(customer, ndt))
        eq("config back to 12: a new invoice gets 12", (inv12.get("tax_rate"), inv12.get("ppn")), (12, 1_100_000))
    finally:
        if k is not None:
            k.stop()
        if state is not None:
            shutil.rmtree(state, ignore_errors=True)


# ---------- scenarios: dashboards (computed by the kernel, compared with figures computed here from the records)

def kernel_today():
    import datetime
    import zoneinfo
    return datetime.datetime.now(zoneinfo.ZoneInfo("Asia/Jakarta")).date()


def dashboard(api, name, period=0):
    """A dashboard as {widget name: widget}; every widget must have computed (no `error`)."""
    st, resp = api.call("GET", f"/api/v1/dashboards/{name}" + (f"?period={period}" if period else ""))
    if not check(f"dashboard {name} answers 200", st == 200, (st, resp)):
        return {}
    ws = {w["name"]: w for w in resp["widgets"]}
    bad = {n: w["error"] for n, w in ws.items() if "error" in w}
    check(f"dashboard {name}: all {len(ws)} widgets computed", not bad, bad)
    return ws


def num(w):
    return round(w.get("value") or 0)


def chart_map(w):
    return {p["key"]: round(p["value"]) for p in w.get("data", [])}


# days past due_date -> the bucket it belongs to, at every edge of the buckets (not due, 1-30, 31-60, 61-90, over 90)
AGING_EDGES = [(0, "not_due", 1_000_000), (30, "past_1_30", 2_000_000), (31, "past_31_60", 3_000_000),
               (60, "past_31_60", 4_000_000), (61, "past_61_90", 5_000_000), (91, "past_over_90", 6_000_000)]


def scenario_dashboards_jasa(api):
    today = kernel_today()
    import datetime
    buckets = ["not_due", "past_1_30", "past_31_60", "past_61_90", "past_over_90"]

    def bucket_sums():
        out = dict.fromkeys(buckets, 0)
        for i in api.find("sales_invoice", "docstatus == 1"):
            if i["outstanding"] > 0:
                d = (today - datetime.date.fromisoformat(i["due_date"])).days
                out["not_due" if d <= 0 else "past_1_30" if d <= 30 else "past_31_60" if d <= 60 else "past_61_90" if d <= 90 else "past_over_90"] += i["outstanding"]
        return out

    # boundary data: one non-PKP invoice due exactly 0, 30, 31, 60, 61 and 91 days ago (today from the kernel's time zone, for data and expectations)
    before = bucket_sums()
    st, bc = api.create("customer", {"customer_name": "PT Contoh Batas Umur"})
    check("aging edges: create the customer", st == 201, bc)
    added = dict.fromkeys(buckets, 0)
    for days, bucket, amount in AGING_EDGES:
        due = today - datetime.timedelta(days=days)
        st, i = api.create("sales_invoice", invoice_body(bc["name"], services(api)[0]["name"], rate=amount, tax_rate=0,
                                                          posting_date=(due - datetime.timedelta(days=30)).isoformat(), due_date=due.isoformat()))
        st2, resp = api.verb("sales_invoice", i["name"], "submit", {"force": True}) if st == 201 else (st, i)
        check(f"aging edges: invoice due {days} days ago submitted", st == 201 and st2 == 200, (st, st2, resp))
        added[bucket] += amount
    ws = dashboard(api, "ar_aging")
    eq("ar_aging at the edges (0, 30, 31, 60, 61, 91 days): not due 1M | 1-30 2M | 31-60 3M+4M | 61-90 5M | over 90 6M (added on top of the others)",
       {b: num(ws[b]) for b in buckets}, {b: before[b] + added[b] for b in buckets})
    eq("ar_aging edge expectations as written out", added, {"not_due": 1_000_000, "past_1_30": 2_000_000, "past_31_60": 7_000_000, "past_61_90": 5_000_000, "past_over_90": 6_000_000})
    invs = [i for i in api.find("sales_invoice", "docstatus == 1") if i["outstanding"] > 0]
    want = {"not_due": 0, "past_1_30": 0, "past_31_60": 0, "past_61_90": 0, "past_over_90": 0}
    for i in invs:
        days = (today - datetime.date.fromisoformat(i["due_date"])).days
        key = "not_due" if days <= 0 else "past_1_30" if days <= 30 else "past_31_60" if days <= 60 else "past_61_90" if days <= 90 else "past_over_90"
        want[key] += i["outstanding"]
    ws = dashboard(api, "ar_aging")
    got = {k: num(ws[k]) for k in want if k in ws}
    eq("ar_aging buckets (today() and duration() in the dashboard filter) match the invoices", got, want)
    check("ar_aging has overdue and past-due amounts in at least two buckets (the SOA scenario made them)", sum(1 for v in want.values() if v) >= 2, want)
    eq("ar_aging total_outstanding = sum of outstanding", num(ws["total_outstanding"]), sum(i["outstanding"] for i in invs))
    by_customer = {}
    for i in invs:
        by_customer[i["customer"]] = by_customer.get(i["customer"], 0) + i["outstanding"]
    top10 = dict(sorted(by_customer.items(), key=lambda kv: -kv[1])[:10])   # the widget sorts by -value and limits to 10
    eq("ar_aging outstanding by customer (chart) = the top 10 customers' outstanding, from the invoices", chart_map(ws["outstanding_by_customer"]), top10)
    overdue = [i for i in invs if i["due_date"] < today.isoformat()]
    eq("ar_aging overdue list holds the overdue invoices", ws["overdue_invoices"]["total"], len(overdue))
    check("ar_aging overdue list is sorted by due_date", [r["due_date"] for r in ws["overdue_invoices"]["rows"]] == sorted(r["due_date"] for r in ws["overdue_invoices"]["rows"]), ws["overdue_invoices"]["rows"][:3])

    ws = dashboard(api, "jasa_overview", 30)
    eq("jasa_overview outstanding_ar", num(ws["outstanding_ar"]), sum(i["outstanding"] for i in invs))
    eq("jasa_overview overdue_count", ws["overdue_count"]["value"], len(overdue))
    since = (today - datetime.timedelta(days=30)).isoformat()
    eq("jasa_overview invoices_issued counts the period only", num(ws["invoices_issued"]),
       sum(i["grand_total"] for i in api.find("sales_invoice", "docstatus == 1") if i["posting_date"] >= since))
    quotes = api.find("quotation", "docstatus == 1")
    eq("jasa_overview open_quotations", ws["open_quotations"]["value"], len([q for q in quotes if q["outcome"] == "open"]))
    msl = api.list("billing_milestone", limit=500)
    eq("jasa_overview ready_to_bill = sum of ready milestones", num(ws["ready_to_bill"]), sum(m["amount"] for m in msl if m["state"] == "ready"))
    due = [m for m in msl if m["state"] == "ready" or (m["state"] == "planned" and m["trigger"] == "date" and m["due_date"] and m["due_date"] <= today.isoformat())]
    eq("jasa_overview milestones_due lists ready and date-due milestones", ws["milestones_due"]["total"], len(due))
    eq("jasa_overview quotations_won (period)", ws["quotations_won"]["value"], len([q for q in quotes if q["outcome"] == "accepted" and q["quotation_date"] >= since]))
    eq("jasa_overview quotations_lost (period)", ws["quotations_lost"]["value"], len([q for q in quotes if q["outcome"] == "lost" and q["quotation_date"] >= since]))
    eq("jasa_overview active_projects", ws["active_projects"]["value"], len([p for p in api.list("project") if p["state"] in ("planned", "running")]))
    eq("jasa_overview workers list (certification expiry: workers, not certificates)", ws["workers_certifications"]["total"], len([w for w in api.list("worker") if w["active"] in (True, "true")]))


def scenario_dashboards_fnb(api):
    import datetime
    today = kernel_today()
    sales = api.find("outlet_sales", "docstatus == 1")
    check("setup: submitted outlet_sales exist for the dashboards", len(sales) >= 2, len(sales))
    since30 = (today - datetime.timedelta(days=30)).isoformat()
    since90 = (today - datetime.timedelta(days=90)).isoformat()
    negs = api.find("stock_balance", "negative == true")

    ws = dashboard(api, "fnb_overview", 30)
    eq("fnb_overview net_period", num(ws["net_period"]), sum(x["net_total"] for x in sales if x["date"] >= since30))
    eq("fnb_overview margin_period = net - cogs", num(ws["margin_period"]), sum(x["net_total"] - x["cogs_total"] for x in sales if x["date"] >= since30))
    eq("fnb_overview net_today", num(ws["net_today"]), sum(x["net_total"] for x in sales if x["date"] == today.isoformat()))
    by_outlet = {}
    for x in sales:
        if x["date"] >= since30:
            by_outlet[x["outlet"]] = by_outlet.get(x["outlet"], 0) + x["net_total"]
    eq("fnb_overview net by outlet (chart)", chart_map(ws["net_by_outlet"]), by_outlet)
    by_day = {}
    for x in sales:
        if x["date"] >= since30:
            by_day[x["date"]] = by_day.get(x["date"], 0) + x["net_total"]
    eq("fnb_overview net by day (chart, grain day)", chart_map(ws["net_by_day"]), by_day)
    eq("fnb_overview 'Stok minus' list = balances below zero", (ws["perlu_stok_minus"]["total"], sorted(r["name"] for r in ws["perlu_stok_minus"]["rows"])), (len(negs), sorted(r["name"] for r in negs)))
    check("fnb_overview 'Stok minus' list is not empty (the stock scenario made a negative balance)", len(negs) >= 1, negs)
    eq("fnb_overview negative_count", ws["negative_count"]["value"], len(negs))

    # adjustments of every reason count in waste_by_outlet, in the adjustment's own period; a cancelled one nets to
    # zero there even when it is cancelled weeks later. The expected value comes from the stock balance, not from the
    # rows the widget reads.
    before = balance(api, DAGO, "BRG-0001")["value"]
    st, adj = api.create("stock_adjustment", {"outlet": DAGO, "reason": "expired", "items": [{"item": "BRG-0001", "qty": 2}]})
    if check("setup: expired adjustment of 2 kg at Dago today", st == 201, (st, adj)):
        submit_doc(api, "stock_adjustment", adj["name"], "submit the expired adjustment")
    expired_value = balance(api, DAGO, "BRG-0001")["value"] - before
    check("the expired adjustment took value out of Dago's stock", expired_value < 0, expired_value)
    if st == 201:
        st2, resp = api.create("stock_adjustment_value", {"stock_adjustment": adj["name"], "entry": "post", "outlet": DAGO,
                                                          "posting_date": adj["adjustment_date"], "value": -1})
        check("a second value row for the same adjustment cannot be made by hand -> 422", st2 == 422, (st2, resp))
    for days in (100, 20):
        d = (today - datetime.timedelta(days=days)).isoformat()
        st, old = api.create("stock_adjustment", {"outlet": DAGO, "reason": "damage", "adjustment_date": d, "items": [{"item": "BRG-0001", "qty": 1}]})
        on = submit_doc(api, "stock_adjustment", old["name"], f"submit a damage adjustment dated {days} days ago") if st == 201 else None
        if on:
            st, resp = api.verb("stock_adjustment", on, "cancel")
            check(f"cancel it today ({days} days later)", st == 200, (st, resp))
    for period in (7, 30, 90):
        ws_p = dashboard(api, "stock_recap", period)
        eq(f"waste_by_outlet Dago over {period} days = the expired adjustment only ({expired_value}); the back-dated cancelled ones net to 0",
           chart_map(ws_p.get("waste_by_outlet", {})).get(DAGO, 0), expired_value)
    ws = dashboard(api, "stock_recap", 90)
    bal = api.list("stock_balance")
    val = {}
    for b in bal:
        val[b["outlet"]] = val.get(b["outlet"], 0) + b["value"]
    eq("stock_recap stock value by outlet", chart_map(ws["stock_value_by_outlet"]), val)
    eq("stock_recap stock_value_total", num(ws["stock_value_total"]), sum(b["value"] for b in bal))
    moves = [m for m in api.find("stock_move", "posting_date >= '%s'" % since90)]
    reasons = {}
    for m in moves:
        reasons[m["reason"]] = reasons.get(m["reason"], 0) + 1
    got = {p["key"]: p["count"] for p in ws["moves_by_reason"]["data"]}
    eq("stock_recap moves by reason", got, reasons)
    nm, cm = {}, {}
    for x in sales:
        nm[x["date"][:7]] = nm.get(x["date"][:7], 0) + x["net_total"]
        cm[x["date"][:7]] = cm.get(x["date"][:7], 0) + x["cogs_total"]
    eq("stock_recap net sales by month (total, all outlets)", chart_map(ws["net_by_month"]), nm)
    eq("stock_recap cogs by month", chart_map(ws["cogs_by_month"]), cm)
    eq("stock_recap net sales by outlet (period)", chart_map(ws["net_by_outlet"]),
       {o: v for o, v in ((o, sum(x["net_total"] for x in sales if x["outlet"] == o and x["date"] >= since90)) for o in {x["outlet"] for x in sales}) if v})
    eq("stock_recap negative balances", ws["negative_balances"]["total"], len(negs))


def scenario_prints_fnb(api):
    """Purchase order print on demo-fnb: the site letterhead and the PO number series with the org code."""
    st, po = api.create("purchase_order", {"vendor": "VND-0001", "outlet": DAGO, "order_date": "2026-11-03", "items": [{"item": "BRG-0001", "qty": 10, "rate": 12_000}]})
    check("print: create purchase_order for Dago", st == 201, po)
    if st != 201:
        return
    st, resp = api.verb("purchase_order", po["name"], "submit")
    check("print: submit purchase_order", st == 200, resp)
    pon = resp.get("name", po["name"])
    check("PO number follows PO/{ORG}/{YY}{MM}/{###} with the org code TCN", re.fullmatch(r"PO/TCN/\d{4}/\d{3}", pon) is not None, pon)
    check_print("purchase_order (Tahu Contoh)", api, "purchase_order", pon, "",
                [pon, "Pesanan pembelian", "PT Tahu Contoh Nusantara", "halo@tahucontoh.test", "CV Contoh Tahu Segar", "Outlet Dago",
                 "Rp 120.000", "seratus dua puluh ribu rupiah"], must_not=["PPN"])
    # a PKP vendor: PPN 12% (DPP 11/12) is on the document and on the print; the org itself is non-PKP
    st, po2 = api.create("purchase_order", {"vendor": "VND-0002", "outlet": DAGO, "order_date": "2026-11-03", "items": [{"item": "BRG-0001", "qty": 10, "rate": 12_000}]})
    eq("fnb PO from a PKP vendor: tax_rate 12, dpp 120.000, ppn 13.200", (po2.get("tax_rate"), po2.get("dpp"), po2.get("ppn")), (12, 120_000, 13_200))
    eq("fnb PO from a non-PKP vendor: tax_rate 0", api.get("purchase_order", pon)["tax_rate"], 0)
    st, resp = api.verb("purchase_order", po2["name"], "submit")
    check_print("purchase_order from a PKP vendor", api, "purchase_order", resp.get("name", po2["name"]), "", ["PPN 12%", "Rp 13.200", "Rp 133.200", "11/12"])
    st, cust = api.create("customer", {"customer_name": "Warung Contoh Pelanggan", "address": "Jl. Contoh Pelanggan 1", "payment_days": 7})
    st, items = 200, [i for i in api.list("item") if i.get("kind") == "material"]
    check_org_letterhead(api, "tahu_contoh", "VND-0001", cust["name"], items[0]["name"])
    st, i0 = api.create("sales_invoice", invoice_body(cust["name"], items[0]["name"]))
    eq("fnb (non-PKP org) invoice: tax_rate 0, ppn 0", (i0.get("tax_rate"), i0.get("ppn")), (0, 0))


# ---------- scenarios: multi-branch F&B (demo-fnb): stock and outlet sales

KG, PORSI = "UOM-001", "UOM-006"
HO, DAGO, BRAGA, CIMAHI = "OUT-01", "OUT-02", "OUT-03", "OUT-04"   # Dago and Braga use Moka, Cimahi the daily form
FIXTURES = os.path.join(ROOT, "examples", "fixtures", "demo-fnb")
CTX = {}   # kernel, org and the signed-in clients, set by main() for demo-fnb


def eqf(name, got, want):
    """Float equality for quantities (0.1 kg steps)."""
    check(name, isinstance(got, (int, float)) and abs(got - want) < 1e-9, f"got {got!r}, want {want!r}")


def new_outlet(api, label):
    st, o = api.create("outlet", {"outlet_name": label, "code": label.upper().replace(" ", "-")[:16]})
    check(f"create outlet {label}", st == 201, o)
    return o["name"]


def balance(api, outlet, item):
    rows = api.find("stock_balance", f"outlet == '{outlet}' && item == '{item}'")
    return rows[0] if rows else None


def check_balance(name, api, outlet, item, qty, value, avg):
    b = balance(api, outlet, item)
    got = (b["qty"], b["value"], b["avg_cost"]) if b else None
    check(name, b is not None and abs(b["qty"] - qty) < 1e-9 and b["value"] == value and b["avg_cost"] == avg, f"got (qty, value, avg) {got}, want {(qty, value, avg)}")
    return b


def moves_of(api, voucher):
    return sorted(api.find("stock_move", f"voucher == '{voucher}'"), key=lambda m: m["name"])


def submit_doc(api, model, name, label, force=False):
    # force acknowledges warn rules (the customer's credit limit, filled by earlier scenarios)
    st, resp = api.verb(model, name, "submit", {"force": True} if force else None)
    check(label, st == 200, (st, resp))
    return resp.get("name", name) if st == 200 else None


def receive(api, outlet, lines, label, vendor="VND-0001"):
    """Create and submit a goods receipt; returns its numbered name."""
    st, gr = api.create("goods_receipt", {"vendor": vendor, "outlet": outlet, "items": lines})
    if not check(f"create {label}", st == 201, gr):
        return None
    return submit_doc(api, "goods_receipt", gr["name"], f"submit {label}")


def scenario_fnb_fixtures(api):
    st, meta = api.call("GET", "/api/v1/meta")
    models = (meta or {}).get("models") or {}
    pf = {f["name"]: f for f in (models.get("purchase_order") or {}).get("fields", [])}.get("project") or {}
    check("F&B has no projects module: the PO's Proyek field is hidden (depends_on app_installed('projects'))",
          "project" not in models and pf.get("depends_on") == "app_installed('projects')", pf)
    outlets = api.list("outlet")
    check("fixtures: 4 outlets, one head office", len(outlets) == 4 and [o["name"] for o in outlets if o.get("is_head_office") in (True, "true")] == [HO], outlets)
    eq("fixtures: uoms Kg, Gram, Karung, Liter, Pcs, Porsi", sorted(u["uom_name"] for u in api.list("uom")), ["Gram", "Karung", "Kg", "Liter", "Pcs", "Porsi"])
    items = api.list("item")
    eq("fixtures: 6 materials and 4 products", (len([i for i in items if i["kind"] == "material"]), len([i for i in items if i["kind"] == "product"])), (6, 4))
    recipes = api.list("recipe", children=1)
    eq("fixtures: one active recipe per product (4)", sorted(r["product"] for r in recipes if r["active"] in (True, "true")), ["BRG-0007", "BRG-0008", "BRG-0009", "BRG-0010"])
    eq("fixtures: recipe line counts [4, 4, 3, 3]", [len(r["lines"]) for r in sorted(recipes, key=lambda r: r["name"])], [4, 4, 3, 3])
    conv = api.get("uom_conversion", "KONV-0001")
    eq("fixtures: Karung -> Kg factor 50 (tepung bumbu)", (conv["uom"], conv["factor"], api.get("item", conv["item"])["stock_uom"]), ("UOM-003", 50, "UOM-001"))
    eq("fixtures: 2 vendors", len(api.list("vendor")), 2)
    eq("fixtures: users with roles head_office and outlet_staff", sorted(u["role"] for u in api.list("user")), ["head_office", "outlet_staff", "outlet_staff"])


def scenario_stock(api):
    # Materials and products of its own, so the brief's numbers are exact whatever the fixtures hold.
    st, mat = api.create("item", {"item_name": "Tahu uji (Kg)", "kind": "material", "stock_uom": KG, "sku": "UJI-TAHU"})
    st, prod = api.create("item", {"item_name": "Porsi uji", "kind": "product", "stock_uom": PORSI, "sales_uom": PORSI, "sku": "UJI-POR", "rate": 20000})
    st, rec = api.create("recipe", {"product": prod["name"], "recipe_name": "Porsi uji", "yield_qty": 1, "lines": [{"material": mat["name"], "qty": 0.1}]})
    check("create recipe: 0.1 kg per portion", st == 201, rec)
    m, p = mat["name"], prod["name"]

    # recipe and document rules
    st, resp = api.create("recipe", {"product": m, "lines": [{"material": m, "qty": 1}]})
    check("recipe for a material -> 422 'Resep hanya untuk barang berjenis produk jadi'", st == 422 and any("produk jadi" in x for x in messages(resp)), (st, resp))
    st, resp = api.create("recipe", {"product": p, "yield_qty": 0, "lines": [{"material": m, "qty": 1}]})
    check("recipe with yield 0 -> 422", st == 422, (st, resp))
    # one active recipe per product (active_product is unique, empty when inactive)
    st, resp = api.create("recipe", {"product": p, "recipe_name": "Porsi uji 2", "lines": [{"material": m, "qty": 0.5}]})
    check("a second active recipe for the same product -> 422", st == 422, (st, resp))
    st, off = api.create("recipe", {"product": p, "recipe_name": "Porsi uji 2", "active": False, "lines": [{"material": m, "qty": 0.5}]})
    check("the same recipe inactive is accepted", st == 201, (st, off))
    if st == 201:
        st, resp = api.call("PATCH", f"/api/v1/r/recipe/{Api.q(off['name'])}", {"active": True})
        check("activating it while another is active -> 422", st == 422, (st, resp))
    st, resp = api.create("stock_transfer", {"from_outlet": DAGO, "to_outlet": DAGO, "items": [{"item": m, "qty": 1}]})
    check("transfer to the same outlet -> 422 'Outlet asal dan tujuan tidak boleh sama'", st == 422 and any("tidak boleh sama" in x for x in messages(resp)), (st, resp))
    st, resp = api.create("stock_adjustment", {"outlet": DAGO, "reason": "waste", "items": [{"item": m, "qty": 0}]})
    check("adjustment qty 0 -> 422 'Jumlah barang harus lebih dari 0'", st == 422 and any("lebih dari 0" in x for x in messages(resp)), (st, resp))
    st, resp = api.create("stock_count", {"outlet": DAGO, "items": [{"item": m, "counted_qty": -1}]})
    check("count below 0 -> 422", st == 422, (st, resp))

    # receipt 100 kg at 12.000 -> avg 12.000
    g1 = receive(api, DAGO, [{"item": m, "qty": 100, "rate": 12000}], "receipt 100 kg at 12.000")
    check_balance("receipt 100 kg at 12.000: balance 100 kg, value 1.200.000, avg 12.000", api, DAGO, m, 100, 1_200_000, 12_000)
    mv = moves_of(api, g1)
    check("receipt makes one move (receipt, +100, value 1.200.000, not negative)",
          len(mv) == 1 and mv[0]["reason"] == "receipt" and mv[0]["qty"] == 100 and mv[0]["value"] == 1_200_000 and not mv[0]["negative"]
          and mv[0]["voucher_type"] == "goods_receipt" and mv[0]["outlet"] == DAGO and mv[0]["item"] == m, mv)

    # receipt 50 kg at 15.000 -> avg 13.000, value 1.950.000
    receive(api, DAGO, [{"item": m, "qty": 50, "rate": 15000}], "receipt 50 kg at 15.000")
    check_balance("receipt 50 kg at 15.000: 150 kg, value 1.950.000, avg 13.000", api, DAGO, m, 150, 1_950_000, 13_000)

    # transfer 30 kg Dago -> Braga in two steps: head office sends, Braga's staff receive (20 kg, then 10 kg)
    has_staff = CTX.get("staff_braga") is not None and CTX.get("staff_dago") is not None
    braga = CTX.get("staff_braga") or api   # without a session the receipts are made by owner so the numbers below still hold; staff-only checks are skipped
    dago_staff = CTX.get("staff_dago")
    if not has_staff:
        fail("outlet_staff receive transfers and are refused elsewhere (staff sessions)", f"setup failed: could not sign in as a non-owner: {CTX.get('login_error')}")
    leftovers = []   # draft receipts that stay after refusals; removed at the end of the block
    st, tr = api.create("stock_transfer", {"from_outlet": DAGO, "to_outlet": BRAGA, "items": [{"item": m, "qty": 30}]})
    check("create transfer 30 kg", st == 201, tr)
    trn = submit_doc(api, "stock_transfer", tr["name"], "submit transfer 30 kg")
    check_balance("transfer 30 kg sent: from-outlet 120 kg at 13.000", api, DAGO, m, 120, 1_560_000, 13_000)
    check("transfer 30 kg sent: to-outlet holds nothing yet (goods in transit)", balance(api, BRAGA, m) is None, balance(api, BRAGA, m))
    mv = moves_of(api, trn)
    check("sending makes one move: transfer_out -30 / -390.000 at Dago", [(x["reason"], x["qty"], x["value"], x["outlet"]) for x in mv] == [("transfer_out", -30, -390_000, DAGO)], mv)
    t = api.get("stock_transfer", trn)
    check("transfer state in_transit, line unit_cost 13.000, received_qty 0",
          t["state"] == "in_transit" and t["items"][0]["unit_cost"] == 13_000 and t["items"][0]["received_qty"] == 0, t)
    if has_staff:
        st, resp = braga.call("GET", f"/api/v1/r/stock_transfer/{Api.q(trn)}")
        check("Braga's staff read the incoming transfer (to_outlet is in scope)", st == 200, (st, resp))
    st, r0 = braga.create("stock_transfer_receipt", {"stock_transfer": trn, "outlet": BRAGA, "items": [{"item": m, "qty": 31}]})
    check("Braga's staff create a receipt of 31 kg", st == 201, (st, r0))
    if st == 201:
        leftovers.append(r0["name"])
        st, resp = braga.verb("stock_transfer_receipt", r0["name"], "submit")
        check("receiving 31 of 30 kg -> 409 over_receipt", st == 409 and resp["error"]["code"] == "over_receipt", (st, resp))
    # receipt by the sending outlet's staff: outlet Dago is not the transfer's to_outlet
    if has_staff:
        st, r1 = dago_staff.create("stock_transfer_receipt", {"stock_transfer": trn, "outlet": DAGO, "items": [{"item": m, "qty": 5}]})
        check("Dago's staff create a receipt with outlet Dago (their own outlet)", st == 201, (st, r1))
        if st == 201:
            leftovers.append(r1["name"])
            st, resp = dago_staff.verb("stock_transfer_receipt", r1["name"], "submit")
            check("receipt by the sending outlet -> 409 wrong_outlet", st == 409 and resp["error"]["code"] == "wrong_outlet", (st, resp))
        st, resp = dago_staff.create("stock_transfer_receipt", {"stock_transfer": trn, "outlet": BRAGA, "items": [{"item": m, "qty": 5}]})
        check("Dago's staff create a receipt with outlet Braga -> 403 (row scope)", st == 403 and resp["error"]["code"] == "permission_denied", (st, resp))
    st, r2 = braga.create("stock_transfer_receipt", {"stock_transfer": trn, "outlet": BRAGA, "items": [{"item": m, "qty": 20}]})
    check("create receipt of 20 kg", st == 201, (st, r2))
    rn1 = submit_doc(braga, "stock_transfer_receipt", r2["name"], "Braga's staff submit the receipt of 20 kg")
    check_balance("after 20 kg received: Braga 20 kg / 260.000 at 13.000", api, BRAGA, m, 20, 260_000, 13_000)
    t = api.get("stock_transfer", trn)
    check("state partly_received, received_qty 20", t["state"] == "partly_received" and t["items"][0]["received_qty"] == 20, t)
    st, r3 = braga.create("stock_transfer_receipt", {"stock_transfer": trn, "outlet": BRAGA, "items": [{"item": m, "qty": 10}]})
    check("create receipt of 10 kg", st == 201, (st, r3))
    rn2 = submit_doc(braga, "stock_transfer_receipt", r3["name"], "Braga's staff submit the receipt of 10 kg")
    check_balance("after 10 kg more: Braga 30 kg / 390.000 at 13.000 (the brief's numbers hold after the receipt)", api, BRAGA, m, 30, 390_000, 13_000)
    ins = [x for n in (rn1, rn2) for x in moves_of(api, n)]
    check("receipts make transfer_in moves at Braga whose values add up to the out-value (390.000)",
          sorted((x["reason"], x["qty"], x["outlet"]) for x in ins) == [("transfer_in", 10, BRAGA), ("transfer_in", 20, BRAGA)] and sum(x["value"] for x in ins) == 390_000, ins)
    t = api.get("stock_transfer", trn)
    check("state received, received_qty 30", t["state"] == "received" and t["items"][0]["received_qty"] == 30, t)
    st, r4 = braga.create("stock_transfer_receipt", {"stock_transfer": trn, "outlet": BRAGA, "items": [{"item": m, "qty": 1}]})
    if st == 201:
        leftovers.append(r4["name"])
        st, resp = braga.verb("stock_transfer_receipt", r4["name"], "submit")
        check("a receipt after full receipt -> 409 not_in_transit", st == 409 and resp["error"]["code"] == "not_in_transit", (st, resp))
    st, resp = api.verb("stock_transfer", trn, "cancel")
    check("cancelling a transfer that has receipts -> 409 has_receipts", st == 409 and resp["error"]["code"] == "has_receipts", (st, resp))

    if has_staff:
        # outlet_staff may only read transfers: one negative per verb (the draft belongs to head office, the cancel is of a submitted transfer)
        st, resp = braga.patch("stock_transfer", trn, {"notes": "diubah staf"})
        check("Braga's staff PATCH a stock_transfer -> 403 permission_denied", st == 403 and resp["error"]["code"] == "permission_denied", (st, resp))
        st, hd = api.create("stock_transfer", {"from_outlet": DAGO, "to_outlet": BRAGA, "items": [{"item": m, "qty": 1}]})
        if check("setup: head office drafts another transfer", st == 201, (st, hd)):
            st, resp = braga.verb("stock_transfer", hd["name"], "submit")
            check("Braga's staff submit head office's draft transfer -> 403 permission_denied", st == 403 and resp["error"]["code"] == "permission_denied", (st, resp))
            api.call("DELETE", f"/api/v1/r/stock_transfer/{Api.q(hd['name'])}")
        st, resp = braga.verb("stock_transfer", trn, "cancel")
        check("Braga's staff cancel a transfer -> 403 permission_denied", st == 403 and resp["error"]["code"] == "permission_denied", (st, resp))
        # staff of another outlet against Braga's receipts: read, submit and cancel
        st, resp = dago_staff.call("GET", f"/api/v1/r/stock_transfer_receipt/{Api.q(rn1)}")
        check("Dago's staff read Braga's receipt -> 403 permission_denied", st == 403 and resp["error"]["code"] == "permission_denied", (st, resp))
        st, bd = braga.create("stock_transfer_receipt", {"stock_transfer": trn, "outlet": BRAGA, "items": [{"item": m, "qty": 1}]})
        if check("setup: Braga's staff draft a receipt", st == 201, (st, bd)):
            leftovers.append(bd["name"])
            st, resp = dago_staff.verb("stock_transfer_receipt", bd["name"], "submit")
            check("Dago's staff submit Braga's draft receipt -> 403 permission_denied", st == 403 and resp["error"]["code"] == "permission_denied", (st, resp))
        st, resp = dago_staff.verb("stock_transfer_receipt", rn2, "cancel")
        check("Dago's staff cancel Braga's receipt -> 403 permission_denied", st == 403 and resp["error"]["code"] == "permission_denied", (st, resp))
        check_balance("those refusals left Braga at 30 kg / 390.000", api, BRAGA, m, 30, 390_000, 13_000)
        # positive: the receiving outlet's staff cancel their own receipt, an exact reverse; they then receive the 10 kg again
        st, resp = braga.verb("stock_transfer_receipt", rn2, "cancel")
        check("Braga's staff cancel their own receipt of 10 kg", st == 200, (st, resp))
        check_balance("exact reverse: Braga back to 20 kg / 260.000 at 13.000", api, BRAGA, m, 20, 260_000, 13_000)
        rv = [x for x in moves_of(api, rn2) if x["reason"] == "reversal"]
        check("the cancel wrote one reversal move (-10 kg, -130.000) at Braga", len(rv) == 1 and rv[0]["qty"] == -10 and rv[0]["value"] == -130_000 and rv[0]["outlet"] == BRAGA, rv)
        t = api.get("stock_transfer", trn)
        check("state partly_received again, received_qty 20", t["state"] == "partly_received" and t["items"][0]["received_qty"] == 20, t)
        st, r5 = braga.create("stock_transfer_receipt", {"stock_transfer": trn, "outlet": BRAGA, "items": [{"item": m, "qty": 10}]})
        if st == 201:
            submit_doc(braga, "stock_transfer_receipt", r5["name"], "Braga's staff receive the 10 kg again")
        check_balance("Braga back to 30 kg / 390.000 at 13.000", api, BRAGA, m, 30, 390_000, 13_000)
    for name in leftovers:
        st, resp = api.call("DELETE", f"/api/v1/r/stock_transfer_receipt/{Api.q(name)}")
        check(f"leftover draft receipt {name} removed", st in (200, 204), (st, resp))

    # receipts and transfers cancel exactly, on outlets of their own
    src, dst = new_outlet(api, "Outlet uji kirim"), new_outlet(api, "Outlet uji terima")
    receive(api, src, [{"item": m, "qty": 100, "rate": 12000}], "receipt 100 kg at 12.000 (sender)")
    st, tc = api.create("stock_transfer", {"from_outlet": src, "to_outlet": dst, "items": [{"item": m, "qty": 40}]})
    tcn = submit_doc(api, "stock_transfer", tc["name"], "submit transfer 40 kg (cancel test)") if st == 201 else None
    check_balance("sender holds 60 kg / 720.000 at 12.000", api, src, m, 60, 720_000, 12_000)
    ra = rb = None
    for qty in (25, 15):
        st, rc = api.create("stock_transfer_receipt", {"stock_transfer": tcn, "outlet": dst, "items": [{"item": m, "qty": qty}]})
        name = submit_doc(api, "stock_transfer_receipt", rc["name"], f"submit receipt of {qty} kg") if st == 201 else None
        ra, rb = (name, rb) if ra is None else (ra, name)
    check_balance("receiver holds 40 kg / 480.000 at 12.000", api, dst, m, 40, 480_000, 12_000)
    st, resp = api.verb("stock_transfer_receipt", rb, "cancel")
    check("cancel the second receipt", st == 200, (st, resp))
    check_balance("receipt cancel is an exact reverse: receiver 25 kg / 300.000", api, dst, m, 25, 300_000, 12_000)
    rv = [x for x in moves_of(api, rb) if x["reason"] == "reversal"]
    check("the cancel wrote one reversal move (-15 kg, -180.000) at the receiver", len(rv) == 1 and rv[0]["qty"] == -15 and rv[0]["value"] == -180_000 and rv[0]["outlet"] == dst, rv)
    t = api.get("stock_transfer", tcn)
    check("state back to partly_received, received_qty 25", t["state"] == "partly_received" and t["items"][0]["received_qty"] == 25, t)
    st, resp = api.verb("stock_transfer_receipt", ra, "cancel")
    check("cancel the first receipt", st == 200, (st, resp))
    check_balance("receiver back to 0 kg / 0", api, dst, m, 0, 0, 12_000)
    t = api.get("stock_transfer", tcn)
    check("state back to in_transit", t["state"] == "in_transit" and t["items"][0]["received_qty"] == 0, t)
    st, resp = api.verb("stock_transfer", tcn, "cancel")
    check("with no receipt left the transfer cancels", st == 200, (st, resp))
    check_balance("sender restored to 100 kg / 1.200.000 at 12.000", api, src, m, 100, 1_200_000, 12_000)

    # value conservation with inexact values: the receipts' in-values add up to the transfer's out-value to the rupiah
    def split_transfer(label, src_lots, qty, parts):
        o1, o2 = new_outlet(api, f"{label} kirim"), new_outlet(api, f"{label} terima")
        for q, rate in src_lots:
            receive(api, o1, [{"item": m, "qty": q, "rate": rate}], f"{label}: receipt {q} kg at {rate}")
        st, tx = api.create("stock_transfer", {"from_outlet": o1, "to_outlet": o2, "items": [{"item": m, "qty": qty}]})
        txn = submit_doc(api, "stock_transfer", tx["name"], f"{label}: send {qty} kg") if st == 201 else None
        out = -sum(x["value"] for x in moves_of(api, txn) if x["reason"] == "transfer_out")
        rn = []
        for q in parts:
            st, rc = api.create("stock_transfer_receipt", {"stock_transfer": txn, "outlet": o2, "items": [{"item": m, "qty": q}]})
            rn.append(submit_doc(api, "stock_transfer_receipt", rc["name"], f"{label}: receive {q} kg") if st == 201 else None)
        ins = [x for n in rn for x in moves_of(api, n) if x["reason"] == "transfer_in"]
        check(f"{label}: sum of the receipts' values == the out-value ({out}) to the rupiah", out > 0 and sum(x["value"] for x in ins) == out, (out, [(x["qty"], x["value"]) for x in ins]))
        check(f"{label}: no receipt move has a positive qty with a negative value", not any(x["qty"] > 0 and x["value"] < 0 for x in ins), ins)
        check_balance(f"{label}: receiver holds the whole value", api, o2, m, qty, out, round(out / qty))
        eq(f"{label}: state received", api.get("stock_transfer", txn)["state"], "received")

    # 100 kg at 10.000 and 1 kg at 10.001 -> value 1.010.001, avg 10.000 (a fractional average, rounded)
    split_transfer("PIK 7 kg", [(100, 10000), (1, 10001)], 7, [3, 4])
    # a quantity that does not divide: 1,5 kg at avg 3 is worth 4,5, in three receipts of 0,5 kg that round to 2 each on their own
    split_transfer("PIK 1,5 kg", [(100, 3)], 1.5, [0.5, 0.5, 0.5])

    # sale of 300 portions on an outlet holding 20 kg (recipe 0.1 kg per portion)
    neg = new_outlet(api, "Outlet uji minus")
    receive(api, neg, [{"item": m, "qty": 20, "rate": 13000}], "receipt 20 kg at 13.000")
    check_balance("outlet holds 20 kg at 13.000", api, neg, m, 20, 260_000, 13_000)
    st, sale = api.create("outlet_sales", {"outlet": neg, "date": "2026-11-02", "lines": [{"item": p, "qty": 300}]})
    check("create sale of 300 portions (gross defaults to qty x item rate = 6.000.000)", st == 201 and sale["gross_total"] == 6_000_000, (st, sale))
    st, resp = api.verb("outlet_sales", sale["name"], "submit")
    check("sale that pushes stock below zero submits (never blocked)", st == 200, (st, resp))
    sn = resp.get("name", sale["name"]) if st == 200 else sale["name"]
    s2 = api.get("outlet_sales", sn)
    eq("sale line cogs 390.000 (30 kg x 13.000)", [l["cogs"] for l in s2["lines"]], [390_000])
    eq("sale cogs_total 390.000", s2["cogs_total"], 390_000)
    eq("sale margin = net_total - cogs_total = 5.610.000", s2["margin"], 5_610_000)
    b = check_balance("balance -10 kg (value -130.000, avg stays 13.000)", api, neg, m, -10, -130_000, 13_000)
    check("stock_balance.negative is true", bool(b and b["negative"] is True), b)
    mv = [x for x in moves_of(api, sn)]
    check("sale move: reason sale, qty -30, value -390.000, balance_qty -10, negative true",
          len(mv) == 1 and mv[0]["reason"] == "sale" and mv[0]["qty"] == -30 and mv[0]["value"] == -390_000 and mv[0]["balance_qty"] == -10 and mv[0]["negative"] is True, mv)
    listed = api.find("stock_balance", "negative == true")
    check("head office list 'Stok minus' (filter negative == true) holds the balance", any(r["outlet"] == neg and r["item"] == m for r in listed), listed)
    check("a positive balance is not in the 'Stok minus' list", not any(r["outlet"] == DAGO and r["item"] == m for r in listed), listed)
    # the alert flow: runs when handed the move, but nothing hands it over yet
    alerts = [e for e in api.audit("notify.in_app") if e.get("target") == "flow/stock_negative_alert"]
    if not alerts:
        fail("stock_negative_alert notifies head_office when a move turns negative",
             "no notify.in_app audit entry: the kernel did not run the `on: stock_move.created` flow")
    else:
        ok("stock_negative_alert notified head_office on its own")
    st, resp = api.call("POST", "/api/v1/flows/stock_negative_alert/run", {"event": mv[0]} if mv else {})
    alerts = [e for e in api.audit("notify.in_app") if e.get("target") == "flow/stock_negative_alert"]
    item_label, outlet_label = api.get("item", m)["item_name"], api.get("outlet", neg)["outlet_name"]
    check("stock_negative_alert, run with the move as its event, names the item and the outlet (find steps), not their ids",
          st == 200 and any(f"Stok {item_label} di {outlet_label} minus (-10)" in e.get("summary", "") for e in alerts), (st, resp, alerts[-1:]))
    st, resp = api.call("POST", "/api/v1/flows/stock_negative_alert/run", {"event": dict(mv[0], item="BRG-9999", outlet="OUT-99")} if mv else {})
    alerts = [e for e in api.audit("notify.in_app") if e.get("target") == "flow/stock_negative_alert"]
    check("stock_negative_alert falls back to the plain names when the records are not found",
          st == 200 and any("Stok BRG-9999 di OUT-99 minus (-10)" in e.get("summary", "") for e in alerts), (st, resp, alerts[-1:]))
    st, resp = api.call("POST", "/api/v1/flows/stock_negative_alert/run", {"event": dict(mv[0], negative=False)} if mv else {})
    check("stock_negative_alert is silent for a move that is not negative", len([e for e in api.audit("notify.in_app") if e.get("target") == "flow/stock_negative_alert"]) == len(alerts), resp)
    # cancel restores
    st, resp = api.verb("outlet_sales", sn, "cancel")
    check("cancel the sale", st == 200, (st, resp))
    check_balance("cancelling the sale restores 20 kg / 260.000 / 13.000", api, neg, m, 20, 260_000, 13_000)

    # count: counted 18 on system 20 -> -2
    cnt = new_outlet(api, "Outlet uji opname")
    receive(api, cnt, [{"item": m, "qty": 20, "rate": 13000}], "receipt 20 kg for the count")
    st, sc = api.create("stock_count", {"outlet": cnt, "items": [{"item": m, "counted_qty": 18}]})
    check("create stock count (18 counted)", st == 201, sc)
    scn = submit_doc(api, "stock_count", sc["name"], "submit stock count")
    d = api.get("stock_count", scn)
    eq("count line system_qty 20 and difference -2 are written back", [(l["system_qty"], l["difference"]) for l in d["items"]], [(20, -2)])
    mv = moves_of(api, scn)
    check("count move: reason count, qty -2, value -26.000", len(mv) == 1 and mv[0]["reason"] == "count" and mv[0]["qty"] == -2 and mv[0]["value"] == -26_000, mv)
    check_balance("balance after the count: 18 kg, value 234.000", api, cnt, m, 18, 234_000, 13_000)

    # waste 1 kg
    st, adj = api.create("stock_adjustment", {"outlet": cnt, "reason": "waste", "items": [{"item": m, "qty": 1}]})
    check("create waste adjustment 1 kg", st == 201, adj)
    adn = submit_doc(api, "stock_adjustment", adj["name"], "submit waste adjustment")
    mv = moves_of(api, adn)
    check("waste move: reason waste, qty -1, value -13.000", len(mv) == 1 and mv[0]["reason"] == "waste" and mv[0]["qty"] == -1 and mv[0]["value"] == -13_000, mv)
    check_balance("balance after waste: 17 kg, value 221.000", api, cnt, m, 17, 221_000, 13_000)

    # PO 2 Karung x 50 -> +100 kg through the receive action
    pkg = new_outlet(api, "Outlet uji kemasan")
    conv, tepung = "KONV-0001", "BRG-0006"
    st, po = api.create("purchase_order", {"vendor": "VND-0001", "outlet": pkg, "items": [{"item": tepung, "qty": 2, "packing": conv, "rate": 600_000}]})
    check("create PO: 2 Karung at 600.000", st == 201 and po["items"][0]["stock_qty"] == 100, (st, po))
    pon = submit_doc(api, "purchase_order", po["name"], "submit PO")
    st, resp = api.action("purchase_order", pon, "receive", {})
    check("receive opens a draft goods receipt", st == 200 and resp.get("open", {}).get("model") == "goods_receipt", (st, resp))
    if st == 200:
        grn = submit_doc(api, "goods_receipt", resp["open"]["name"], "submit goods receipt of the PO")
        check_balance("PO 2 Karung x 50 -> +100 kg at 12.000 (value 1.200.000)", api, pkg, tepung, 100, 1_200_000, 12_000)
        pod = api.get("purchase_order", pon)
        check("PO received_qty 2 Karung, received_pct 100", pod["items"][0]["received_qty"] == 2 and pod["received_pct"] == 100, pod)
        st, resp = api.verb("goods_receipt", grn, "cancel")
        check("cancel the PO's goods receipt", st == 200, (st, resp))
        check_balance("cancelling it takes the 100 kg off again", api, pkg, tepung, 0, 0, 12_000)
        eq("and takes received_qty off the PO", api.get("purchase_order", pon)["items"][0]["received_qty"], 0)

    # cancelling a receipt restores value and avg exactly
    can = new_outlet(api, "Outlet uji batal")
    receive(api, can, [{"item": m, "qty": 100, "rate": 12000}], "receipt 1 (100 kg at 12.000)")
    g2 = receive(api, can, [{"item": m, "qty": 50, "rate": 15000}], "receipt 2 (50 kg at 15.000)")
    check_balance("before the cancel: 150 kg / 1.950.000 / avg 13.000", api, can, m, 150, 1_950_000, 13_000)
    st, resp = api.verb("goods_receipt", g2, "cancel")
    check("cancel receipt 2", st == 200, (st, resp))
    check_balance("cancelling receipt 2 restores 100 kg / 1.200.000 / avg 12.000 exactly", api, can, m, 100, 1_200_000, 12_000)
    rv = [x for x in moves_of(api, g2) if x["reason"] == "reversal"]
    check("the cancel wrote one reversal move (-50 kg, -750.000)", len(rv) == 1 and rv[0]["qty"] == -50 and rv[0]["value"] == -750_000, rv)


def scenario_stock_access(api):
    """Permissions: stock_move and stock_balance are written only by hooks; outlet_staff sees only its outlets (contract 0010)."""
    org, k = CTX["org"], CTX["kernel"]
    hq, dago = CTX.get("hq"), CTX.get("staff_dago")
    m = api.find("item", "sku == 'UJI-TAHU'")[0]["name"]
    all_bal = api.find("stock_balance", "item == '%s'" % m)
    check("setup: the stock scenario left balances at several outlets", len({b["outlet"] for b in all_bal}) >= 3, all_bal)
    if hq is None or dago is None:
        fail("outlet_staff row scope check (list own outlet, 403 on another)", f"setup failed: could not sign in as a non-owner: {CTX.get('login_error')}")
        return
    move = api.find("stock_move", "item == '%s'" % m)[0]
    bal_dago = balance(api, DAGO, m)
    bal_braga = balance(api, BRAGA, m)

    # outlet_staff at Dago
    mine = dago.find("stock_balance", "item == '%s'" % m)
    check("outlet_staff (Dago) lists only Dago's stock_balance", bool(mine) and all(r["outlet"] == DAGO for r in mine), mine)
    eq("outlet_staff (Dago) lists all rows of its own outlet", len(mine), len([b for b in all_bal if b["outlet"] == DAGO]))
    st, resp = dago.call("GET", f"/api/v1/r/stock_balance/{Api.q(bal_braga['name'])}")
    check("outlet_staff (Dago) reads Braga's stock_balance -> 403 permission_denied", st == 403 and resp["error"]["code"] == "permission_denied", (st, resp))
    st, resp = dago.call("GET", f"/api/v1/r/stock_balance/{Api.q(bal_dago['name'])}")
    check("outlet_staff (Dago) reads Dago's stock_balance -> 200", st == 200, (st, resp))
    st, resp = dago.call("GET", f"/api/v1/r/outlet/{BRAGA}")
    check("outlet_staff (Dago) reads the outlet record of Braga -> 403", st == 403, (st, resp))
    eq("outlet_staff (Dago) lists only its own outlet", [o["name"] for o in dago.list("outlet")], [DAGO])
    moves = dago.find("stock_move", "item == '%s'" % m)
    check("outlet_staff (Dago) lists only Dago's stock_move", bool(moves) and all(x["outlet"] == DAGO for x in moves), moves)
    # nobody but owner writes moves or balances
    for who, cl in (("outlet_staff", dago), ("head_office", hq)):
        st, resp = cl.create("stock_move", {"outlet": DAGO, "item": m, "qty": 5})
        check(f"{who} cannot create a stock_move -> 403", st == 403 and resp["error"]["code"] == "permission_denied", (st, resp))
        st, resp = cl.create("stock_balance", {"outlet": DAGO, "item": m, "qty": 5})
        check(f"{who} cannot create a stock_balance -> 403", st == 403, (st, resp))
        st, resp = cl.patch("stock_balance", bal_dago["name"], {"qty": 1})
        check(f"{who} cannot change a stock_balance -> 403", st == 403, (st, resp))
        st, resp = cl.call("DELETE", f"/api/v1/r/stock_move/{Api.q(move['name'])}")
        check(f"{who} cannot delete a stock_move -> 403", st == 403, (st, resp))
    # head office sees everything, and the negative exception
    seen = {b["outlet"] for b in hq.find("stock_balance", "item == '%s'" % m)}
    check("head_office lists the stock_balance of every outlet", {DAGO, BRAGA} <= seen and len(seen) == len({b["outlet"] for b in all_bal}), seen)

    # an outlet_staff document: the hook writes moves although the role may not create them
    st, sc = dago.create("stock_count", {"outlet": DAGO, "items": [{"item": m, "counted_qty": 118}]})
    check("outlet_staff (Dago) creates a stock_count for Dago", st == 201, (st, sc))
    if st == 201:
        scn = submit_doc(dago, "stock_count", sc["name"], "outlet_staff submits its stock_count")
        mv = moves_of(api, scn)
        check("the stock hook wrote the count move for the staff's submit (-2 kg)", len(mv) == 1 and mv[0]["qty"] == -2 and mv[0]["reason"] == "count", mv)
    st, resp = dago.create("stock_count", {"outlet": BRAGA, "items": [{"item": m, "counted_qty": 1}]})
    check("outlet_staff (Dago) creates a stock_count for Braga -> 403", st == 403, (st, resp))
    # Cimahi takes the daily form, so no sales rule fails first (the kernel checks rules before row scope)
    st, resp = dago.create("outlet_sales", {"outlet": CIMAHI, "date": "2026-11-05", "lines": [{"item": "BRG-0007", "qty": 1}]})
    check("outlet_staff (Dago) creates outlet_sales for Cimahi -> 403", st == 403, (st, resp))

    # transfers (contract 0010 §5): the scope lists [outlet, from_outlet, to_outlet], so outlets read transfers in and out of their outlet; only head
    # office creates them (outlet_staff has read only), and outlets receive goods with a stock_transfer_receipt of their own outlet
    st, resp = dago.create("stock_transfer", {"from_outlet": BRAGA, "to_outlet": DAGO, "items": [{"item": m, "qty": 1}]})
    check("outlet_staff (Dago) creates a stock_transfer from Braga (to Dago) -> 403 permission_denied",
          st == 403 and resp["error"]["code"] == "permission_denied", (st, resp))
    st, resp = dago.create("stock_transfer", {"from_outlet": BRAGA, "to_outlet": HO, "items": [{"item": m, "qty": 1}]})
    check("outlet_staff (Dago) creates a stock_transfer from Braga to head office (neither side is Dago) -> 403", st == 403, (st, resp))
    st, resp = dago.create("stock_transfer", {"from_outlet": DAGO, "to_outlet": BRAGA, "items": [{"item": m, "qty": 1}]})
    check("outlet_staff (Dago) creates a stock_transfer from Dago to Braga -> 403 permission_denied (outlet_staff has read only on transfers)",
          st == 403 and resp["error"]["code"] == "permission_denied", (st, resp))
    st, braga_sales = api.create("outlet_sales", {"outlet": BRAGA, "date": "2026-11-04", "source": "moka", "moka_outlet": "Tahu Contoh - Braga", "lines": [{"item": "BRG-0007", "qty": 1}]})
    check("setup: owner creates a draft outlet_sales for Braga", st == 201, (st, braga_sales))
    if st == 201:
        st, resp = dago.call("GET", f"/api/v1/r/outlet_sales/{Api.q(braga_sales['name'])}")
        check("outlet_staff (Dago) reads Braga's outlet_sales -> 403 permission_denied", st == 403 and resp["error"]["code"] == "permission_denied", (st, resp))
        eq("outlet_staff (Dago) lists no outlet_sales of Braga", [x for x in dago.list("outlet_sales") if x["outlet"] == BRAGA], [])


def read_sample(name):
    with open(os.path.join(FIXTURES, name), "rb") as f:
        return f.read()


def preview(cl, imp, filename, content, outlet):
    return cl.upload(f"/api/v1/imports/{imp}", {"outlet": outlet}, filename, content)


def scenario_payment_methods(api):
    """Gladi 6 Nov (audit re-pin alpha.7, L2): "Potong retur" (method sales_return) only credits a sales return of the
    delivery module, so only a site that installs it offers the option (demo-grosir, through custom/payment.rk). Elsewhere
    the form must not offer a choice that is always refused."""
    st, meta = api.call("GET", "/api/v1/meta")
    fields = (((meta or {}).get("models") or {}).get("payment") or {}).get("fields") or []
    if not fields:
        check(f"{CTX.get('site')}: no payments module, no payment form", True)
        return
    method = next((f for f in fields if f.get("name") == "method"), {})
    values = [o.get("value") for o in method.get("options") or []]
    check("payment.method still offers transfer, cash, giro, qris", all(v in values for v in ("transfer", "cash", "giro", "qris")), values)
    if CTX.get("site") == "demo-grosir":
        check("demo-grosir: payment.method offers Potong retur (sales_return) from the site overlay", "sales_return" in values, values)
        return
    check(f"{CTX.get('site')}: payment.method does not offer Potong retur (sales_return) without the delivery module", "sales_return" not in values, values)
    st, cust = api.call("GET", "/api/v1/r/customer?limit=1")
    rows = (cust or {}).get("data") or []
    if rows:
        st, resp = api.call("POST", "/api/v1/r/payment", {"direction": "receive", "customer": rows[0]["name"], "method": "sales_return", "amount": 1000})
        check("a payment with method sales_return is refused (422)", st == 422, (st, resp))


def scenario_credit_limit_warning(api):
    """Audit ulang test operator 6 Nov, finding 2: several submits pass force=True for the credit limit, so prove the
    warn rule itself: an invoice past the customer's limit is held with needs_force and only credit_limit, stays a draft,
    and goes through when the warning is acknowledged."""
    fm = CTX.get("fm") or api
    customer, ndt = "CUST-0001", services(api)[0]["name"]
    c = api.get("customer", customer)
    limit, owed = c.get("credit_limit") or 0, c.get("outstanding") or 0
    if not check("CUST-0001 has a credit limit for the warning check", limit > 0, c):
        return
    over = max(limit - owed, 0) + 1_000_000
    st, inv = fm.create("sales_invoice", {"customer": customer, "items": [{"item": ndt, "qty": 1, "rate": over, "description": "Uji batas kredit"}]})
    if not check("invoice past the credit limit created as a draft", st == 201, (st, inv)):
        return
    st, resp = fm.verb("sales_invoice", inv["name"], "submit")
    warns = [w.get("rule") for w in (resp or {}).get("warnings", [])]
    check("submit without force -> 422 needs_force, warnings only credit_limit, no errors",
          st == 422 and resp.get("needs_force") is True and warns == ["credit_limit"] and not resp.get("errors"), (st, resp))
    eq("the held invoice is still a draft", api.get("sales_invoice", inv["name"]).get("docstatus"), 0)
    st, resp = fm.verb("sales_invoice", inv["name"], "submit", {"force": True})
    check("submit with force acknowledges the warning -> 200", st == 200, (st, resp))
    if st == 200:
        fm.verb("sales_invoice", resp.get("name", inv["name"]), "cancel")


def scenario_po_approval_fnb(api):
    """demo-fnb's custom/purchase_order.rk: a head office PO above Rp 5.000.000 waits for the owner (contract 0007 §1)."""
    hq = CTX.get("hq")
    if hq is None:
        fail("po_approval (fnb): head office member", CTX.get("login_error", "no head office member"))
        return
    st, meta = api.call("GET", "/api/v1/meta")
    pols = [p.get("name") for p in (((meta or {}).get("models") or {}).get("purchase_order") or {}).get("approvals", [])]
    check("meta: purchase_order carries po_approval from the site overlay", "po_approval" in pols, pols)
    st, edge = hq.create("purchase_order", {"vendor": "VND-0001", "outlet": DAGO, "items": [{"item": "BRG-0001", "qty": 100, "rate": 50_000}]})
    st, resp = hq.verb("purchase_order", edge["name"], "submit")
    check("a PO of exactly 5.000.000 by head office submits without approval", st == 200 and not approvals_of(api, "purchase_order", resp.get("name", edge["name"])), (st, resp))
    st, big = hq.create("purchase_order", {"vendor": "VND-0001", "outlet": DAGO, "items": [{"item": "BRG-0001", "qty": 100, "rate": 85_000}]})
    check("head office creates a PO of 8.500.000", st == 201 and big["grand_total"] == 8_500_000, (st, big))
    if st != 201:
        return
    st, resp = hq.verb("purchase_order", big["name"], "submit")
    check("head office submits it: held, 202, po_approval pending for purchasing_manager",
          st == 202 and resp["approval"]["policy"] == "po_approval" and resp["approval"]["state"] == "pending" and resp["approval"]["approvers"] == ["purchasing_manager"], (st, resp))
    if st != 202:
        return
    eq("the held PO is still a draft", api.get("purchase_order", big["name"])["docstatus"], 0)
    st2, r2 = decide(hq, resp["approval"]["id"])
    check("head office cannot approve its own PO -> 403", st2 == 403, (st2, r2))
    st2, r2 = decide(api, resp["approval"]["id"])
    check("the owner approves: the PO is submitted and numbered PO/...", st2 == 200 and r2.get("record", {}).get("docstatus") == 1 and r2["record"]["name"].startswith("PO/"), (st2, r2))
    approved_po = r2.get("record", {}).get("name", "") if st2 == 200 else ""
    # a bill skips the owner only on an approved (submitted, live) PO and within its value; audit round 2 of #10
    def bill_on(po_name, bill_no, rate):
        st, vb = hq.create("vendor_bill", {"vendor": "VND-0001", "bill_no": bill_no, "bill_date": "2026-11-05", "outlet": DAGO,
                                           "purchase_order": po_name, "items": [{"item": "BRG-0001", "qty": 100, "rate": rate}]})
        if not check(f"head office records bill {bill_no} on {po_name}", st == 201, (st, vb)):
            return None, None
        return hq.verb("vendor_bill", vb["name"], "submit")
    st, small = hq.create("purchase_order", {"vendor": "VND-0001", "outlet": DAGO, "items": [{"item": "BRG-0001", "qty": 1, "rate": 10_000}]})
    st, resp = hq.verb("purchase_order", small["name"], "submit")
    small_po = resp.get("name", "") if st == 200 else ""
    check("a PO of 10.000 is submitted without approval", st == 200 and small_po.startswith("PO/"), (st, resp))
    st, resp = bill_on(small_po, "A1-50JT", 500_000)
    check("a 50.000.000 bill on a 10.000 PO is held for the owner (202, bill_without_po_approval)",
          st == 202 and resp["approval"]["policy"] == "bill_without_po_approval", (st, resp))
    st, held = hq.create("purchase_order", {"vendor": "VND-0001", "outlet": DAGO, "items": [{"item": "BRG-0001", "qty": 100, "rate": 510_000}]})
    st, resp = hq.verb("purchase_order", held["name"], "submit")
    check("a PO of 51.000.000 waits for the owner (202)", st == 202, (st, resp))
    st, resp = bill_on(held["name"], "A2-51JT", 510_000)
    check("a 51.000.000 bill on a PO still waiting for approval is held (202)",
          st == 202 and resp["approval"]["policy"] == "bill_without_po_approval", (st, resp))
    st, resp = hq.verb("purchase_order", small_po, "cancel")
    check("head office cancels the 10.000 PO", st == 200, (st, resp))
    st, resp = bill_on(small_po, "A3-50JT", 500_000)
    check("a 50.000.000 bill on a cancelled PO is held (202)",
          st == 202 and resp["approval"]["policy"] == "bill_without_po_approval", (st, resp))
    if approved_po:
        st, resp = bill_on(approved_po, "A4-8JT5", 85_000)
        check("an 8.500.000 bill on the approved 8.500.000 PO is not asked again (200, no request)",
              st == 200 and resp.get("approval") is None, (st, resp))
    # a foreign-currency document waits whatever its rupiah value: the kurs is typed by the maker, so USD 100.000 at
    # 1,01 (Rp 101.000) would otherwise pass every threshold (audit round 3 of #10, B4)
    fx = {"currency": "USD", "exchange_rate": 1.01}
    st, d = hq.create("purchase_order", dict(fx, vendor="VND-0001", outlet=DAGO, items=[{"item": "BRG-0001", "qty": 1, "rate": 0, "rate_fx": 100_000}]))
    if check("head office drafts a USD 100.000 PO at kurs 1,01 (Rp 101.000)", st == 201 and d["grand_total"] == 101_000, (st, d)):
        st, resp = hq.verb("purchase_order", d["name"], "submit")
        check("the USD PO waits for the owner (202, po_approval)", st == 202 and resp["approval"]["policy"] == "po_approval", (st, resp))
    st, d = hq.create("vendor_bill", dict(fx, vendor="VND-0001", bill_no="FX-101", bill_date="2026-11-05", outlet=DAGO,
                                          items=[{"item": "BRG-0001", "qty": 1, "rate": 0, "rate_fx": 100_000}]))
    if check("head office records a USD 100.000 bill at kurs 1,01 without a PO", st == 201, (st, d)):
        st, resp = hq.verb("vendor_bill", d["name"], "submit")
        check("the USD bill waits for the owner (202, bill_without_po_approval)", st == 202 and resp["approval"]["policy"] == "bill_without_po_approval", (st, resp))
    # nor through a PO link: a bill in another currency than the base (IDR) always waits (audit round 4 of #10, D1)
    st, d = hq.create("purchase_order", {"vendor": "VND-0001", "outlet": DAGO, "items": [{"item": "BRG-0001", "qty": 1, "rate": 200_000}]})
    st, resp = hq.verb("purchase_order", d["name"], "submit")
    po200 = resp.get("name", "") if st == 200 else ""
    check("a PO of 200.000 is submitted without approval", st == 200 and po200.startswith("PO/"), (st, resp))
    st, d = hq.create("vendor_bill", dict(fx, vendor="VND-0001", bill_no="FX-D1", bill_date="2026-11-05", outlet=DAGO, purchase_order=po200,
                                          items=[{"item": "BRG-0001", "qty": 1, "rate": 0, "rate_fx": 100_000}]))
    if check("head office records a USD 100.000 bill at kurs 1,01 (Rp 101.000) on the 200.000 PO", st == 201 and d["grand_total"] == 101_000, (st, d)):
        st, resp = hq.verb("vendor_bill", d["name"], "submit")
        check("the USD bill on an approved PO still waits for the owner (202, bill_without_po_approval)",
              st == 202 and resp["approval"]["policy"] == "bill_without_po_approval", (st, resp))
    st, d = hq.create("payment", dict(fx, direction="pay", vendor="VND-0001", amount_fx=100_000))
    if check("head office drafts a USD 100.000 outgoing payment at kurs 1,01", st == 201 and d["amount"] == 101_000, (st, d)):
        st, resp = hq.verb("payment", d["name"], "submit")
        check("the USD payment waits for the owner (202, payout_approval)", st == 202 and resp["approval"]["policy"] == "payout_approval", (st, resp))
    # the PO approval cannot be skipped by billing and paying directly
    st, vb = hq.create("vendor_bill", {"vendor": "VND-0001", "bill_no": "TANPA-PO-1", "bill_date": "2026-11-05", "outlet": DAGO,
                                       "items": [{"item": "BRG-0001", "qty": 100, "rate": 85_000}]})
    if check("head office records an 8.500.000 bill without a PO", st == 201, (st, vb)):
        st, resp = hq.verb("vendor_bill", vb["name"], "submit")
        check("the bill without a PO is held for the owner (202, bill_without_po_approval)", st == 202 and resp["approval"]["policy"] == "bill_without_po_approval", (st, resp))
    st, pay = hq.create("payment", {"direction": "pay", "vendor": "VND-0001", "amount": 50_000_000})
    if check("head office drafts a 50.000.000 outgoing payment", st == 201, (st, pay)):
        st, resp = hq.verb("payment", pay["name"], "submit")
        check("the outgoing payment is held for the owner (202, payout_approval)", st == 202 and resp["approval"]["policy"] == "payout_approval", (st, resp))


def scenario_outlet_sales(api):
    hq, dago = CTX.get("hq") or api, CTX.get("staff_dago")
    st, meta = api.call("GET", "/api/v1/meta")
    imps = (meta or {}).get("imports", {})
    check("meta lists the imports moka_item_details and daily_form on outlet_sales",
          st == 200 and all(imps.get(n, {}).get("model") == "outlet_sales" and imps[n].get("per") == "outlet" for n in ("moka_item_details", "daily_form")), imps)
    if not imps:
        fail("Moka import: upload, preview, commit, duplicates", "kernel (S7 imports) is merged on main: no imports in /meta")
        return

    # stock to sell from: Dago receives the fixture materials
    lines = [{"item": "BRG-0001", "qty": 100, "rate": 12000}, {"item": "BRG-0002", "qty": 50, "rate": 18000}, {"item": "BRG-0003", "qty": 5, "rate": 9000},
             {"item": "BRG-0004", "qty": 5, "rate": 60000}, {"item": "BRG-0005", "qty": 100, "rate": 800}, {"item": "BRG-0006", "qty": 20, "rate": 12000}]
    receive(api, DAGO, lines, "opening receipt of the fixture materials at Dago")

    moka = read_sample("moka-sample.csv")
    # a staff member may not import (import permission is head office's)
    if dago is not None:
        st, resp = preview(dago, "moka_item_details", "moka-sample.csv", moka, DAGO)
        check("outlet_staff may not import: 403 permission_denied", st == 403, (st, resp))
    # an unknown SKU is reported per row, in Bahasa Indonesia
    bad = moka.replace(b"TH-ISI", b"TH-XX")
    st, resp = preview(hq, "moka_item_details", "bad.csv", bad, DAGO)
    check("unknown SKU: preview reports 'Item dengan sku \"TH-XX\" tidak ditemukan' on row 4",
          st == 200 and any(e["row"] == 4 and "TH-XX" in e["message"] for e in resp["errors"]), (st, resp))
    if st == 200:
        st, c = hq.call("POST", f"/api/v1/imports/moka_item_details/{resp['id']}/commit")
        check("a preview with errors cannot be committed: 422 import_errors", st == 422 and c["error"]["code"] == "import_errors", (st, c))

    # upload -> preview
    st, pv = preview(hq, "moka_item_details", "moka-sample.csv", moka, DAGO)
    check("upload the Moka sample: preview 7 rows (one a refund), no errors, not a duplicate", st == 200 and pv["rows"] == 7 and pv["errors"] == [] and pv["skipped"] == 0 and pv["duplicate"] is False, (st, pv))
    if st != 200:
        return
    prev = pv["preview"]
    check("preview: one outlet_sales record for 02/11/2026 with 7 lines, source moka, outlet Dago",
          len(prev) == 1 and len(prev[0]["lines"]) == 7 and prev[0]["date"] == "2026-11-02" and prev[0]["source"] == "moka" and prev[0]["outlet"] == DAGO, prev)
    st, c = hq.call("POST", f"/api/v1/imports/moka_item_details/{pv['id']}/commit")
    check("commit creates the draft outlet_sales", st == 200 and len(c.get("records", [])) == 1, (st, c))
    if st != 200:
        return
    sales = api.get("outlet_sales", c["records"][0])
    check("imported record is a draft (docstatus 0, no stock moved yet)", sales["docstatus"] == 0 and not moves_of(api, sales["name"]), sales)
    eq("imported totals: gross 259.000, discount 16.000, refund 15.000", (sales["gross_total"], sales["discount_total"], sales["refund_total"]), (259_000, 16_000, 15_000))
    eq("imported net_total 228.000 (= Moka's Net Sales)", sales["net_total"], 228_000)
    eq("imported qty_total 12 (the refund line counts -1)", sales["qty_total"], 12)
    eq("moka_outlet keeps the file's Outlet column", sales["moka_outlet"], "Tahu Contoh - Dago")
    eq("refund line kept as Moka wrote it: qty -1, gross 0, refund 15.000, net -15.000",
       [(l["qty"], l["gross"], l["refund"], l["net"]) for l in sales["lines"] if l["refund"]], [(-1, 0, 15_000, -15_000)])
    eq("imported lines match items by sku", [l["item"] for l in sales["lines"]], ["BRG-0007", "BRG-0008", "BRG-0009", "BRG-0008", "BRG-0007", "BRG-0007", "BRG-0010"])

    # the same file again
    st, pv2 = preview(hq, "moka_item_details", "moka-sample.csv", moka, DAGO)
    check("the same file again: duplicate true (skipped rows 7)", st == 200 and pv2["duplicate"] is True and pv2["skipped"] == 7, (st, pv2))
    st, c2 = hq.call("POST", f"/api/v1/imports/moka_item_details/{pv2['id']}/commit")
    check("committing the duplicate -> 409 already_imported", st == 409 and c2["error"]["code"] == "already_imported", (st, c2))
    eq("still one imported record", len(api.find("outlet_sales", "source == 'moka' && outlet == '%s'" % DAGO)), 1)

    # an overlapping export: only the new rows
    st, pv3 = preview(hq, "moka_item_details", "moka-sample-overlap.csv", read_sample("moka-sample-overlap.csv"), DAGO)
    check("overlapping file (1 old row, 3 new): skipped 1, 2 records previewed (02/11 and 03/11), not a duplicate",
          st == 200 and pv3["rows"] == 4 and pv3["skipped"] == 1 and pv3["duplicate"] is False and sorted(r["date"] for r in pv3["preview"]) == ["2026-11-02", "2026-11-03"], (st, pv3))
    st, c3 = hq.call("POST", f"/api/v1/imports/moka_item_details/{pv3['id']}/commit")
    check("commit of the overlapping file creates only the new rows", st == 200 and len(c3.get("records", [])) == 2, (st, c3))
    if st == 200:
        got = sorted((r["date"], len(r["lines"])) for r in (api.get("outlet_sales", n) for n in c3["records"]))
        eq("new records: 02/11 with 2 lines, 03/11 with 1 line", got, [("2026-11-02", 2), ("2026-11-03", 1)])
    # the Outlet column must be the chosen outlet's moka_name: Dago's file uploaded for Braga is refused
    wrong = "beda dengan Nama outlet di Moka"
    st, pvw = preview(hq, "moka_item_details", "moka-sample.csv", moka, BRAGA)
    check("Dago's Moka file uploaded for Braga: preview error 'Kolom Outlet ... beda dengan Nama outlet di Moka'",
          st == 200 and pvw["duplicate"] is False and any(wrong in e["message"] for e in pvw["errors"]), (st, pvw))
    if st == 200:
        st, c = hq.call("POST", f"/api/v1/imports/moka_item_details/{pvw['id']}/commit")
        check("the wrong-outlet file cannot be committed: 422 import_errors", st == 422 and c["error"]["code"] == "import_errors", (st, c))
    # an All Outlets export (Dago and Braga rows) for Dago: the Braga rows are refused, nothing is committed
    braga = read_sample("moka-sample-braga.csv")
    allout = moka + braga.split(b"\n", 1)[1]
    st, pva = preview(hq, "moka_item_details", "all-outlets.csv", allout, DAGO)
    check("All Outlets file for Dago: Dago's 7 rows skipped (already imported), Braga's 7 rows grouped apart and refused",
          st == 200 and pva["skipped"] == 7 and len(pva["preview"]) == 1 and any(wrong in e["message"] for e in pva["errors"]), (st, pva))
    if st == 200:
        st, c = hq.call("POST", f"/api/v1/imports/moka_item_details/{pva['id']}/commit")
        check("the All Outlets file cannot be committed for Dago: 422 import_errors", st == 422 and c["error"]["code"] == "import_errors", (st, c))
    # an outlet without moka_name (head office) refuses any Moka file
    st, pvh = preview(hq, "moka_item_details", "moka-sample.csv", moka, "OUT-01")
    check("outlet without Nama outlet di Moka (head office): the Moka file is refused", st == 200 and any(wrong in e["message"] for e in pvh["errors"]), (st, pvh))
    # Braga's own export: no errors (used below for the negative-stock checks)
    st, pvb = preview(hq, "moka_item_details", "moka-sample-braga.csv", braga, BRAGA)
    check("Braga's own Moka file for Braga: no errors, not a duplicate", st == 200 and pvb["duplicate"] is False and pvb["skipped"] == 0 and pvb["errors"] == [], (st, pvb))

    # submit: stock moves through the recipes, cogs, margin
    sn = submit_doc(api, "outlet_sales", sales["name"], "submit the imported sales record")
    s2 = api.get("outlet_sales", sn)
    eq("recipes explode: line cogs [3.396, 2.280, 7.740, 4.560, 1.698, 0, 15.680] (no cogs on the refund line)", [l["cogs"] or 0 for l in s2["lines"]], [3396, 2280, 7740, 4560, 1698, 0, 15680])
    eq("cogs_total 35.354", s2["cogs_total"], 35_354)
    eq("margin = net 228.000 - cogs 35.354 = 192.646", s2["margin"], 192_646)
    mv = moves_of(api, sn)
    check("moves: one per sold line and material (6 sold lines -> 22 moves; the refund line posts none: materials stay used), all reason sale, none negative",
          len(mv) == 22 and all(x["reason"] == "sale" and not x["negative"] for x in mv), [(x["item"], x["qty"]) for x in mv])
    b = balance(api, DAGO, "BRG-0001")
    eqf("tahu mentah at Dago: 100 - 1,85 = 98,15 kg", b["qty"], 98.15)
    eq("tahu mentah value = 1.200.000 - 22.200 (1,85 kg at 12.000)", b["value"], 1_177_800)
    b = balance(api, DAGO, "BRG-0005")
    eqf("kemasan: 100 - 4 = 96 pcs (BWP has the box in its recipe)", b["qty"], 96)
    st, resp = api.verb("outlet_sales", sn, "cancel")
    check("cancel the sales record", st == 200, (st, resp))
    eqf("cancel restores tahu mentah to 100 kg", balance(api, DAGO, "BRG-0001")["qty"], 100)

    # Review Focus: sales that push stock below zero still import and submit
    st, c = hq.call("POST", f"/api/v1/imports/moka_item_details/{pvb['id']}/commit")
    check("Braga (no stock at all): the Moka import commits", st == 200 and len(c.get("records", [])) == 1, (st, c))
    if st == 200:
        bsn = submit_doc(api, "outlet_sales", c["records"][0], "Braga's sales record submits although it holds nothing")
        s3 = api.get("outlet_sales", bsn)
        eq("negative sale: cogs falls back to the items' standard_cost (same 35.354)", s3["cogs_total"], 35_354)
        b = balance(api, BRAGA, "BRG-0001")
        eqf("tahu mentah at Braga: balance -1,85 kg", b["qty"], -1.85)
        check("stock_balance.negative is true", b["negative"] is True, b)
        negs = hq.find("stock_balance", "negative == true && outlet == '%s'" % BRAGA)
        check("head office lists all 6 negative balances of Braga ('Stok minus')", sorted(r["item"] for r in negs) == [f"BRG-000{i}" for i in (1, 2, 3, 4, 5, 6)], negs)
        check("negative moves are flagged", all(x["negative"] is True for x in moves_of(api, bsn)), moves_of(api, bsn)[:3])
        # fnb dashboard "Stok minus" list: Task 10 (a dashboard widget)

    # the daily form: created by REST with 2 lines, then imported
    st, df = api.create("outlet_sales", {"outlet": CIMAHI, "date": "2026-11-04", "lines": [
        {"item": "BRG-0007", "qty": 40, "gross": 600_000}, {"item": "BRG-0008", "qty": 25, "gross": 450_000, "discount": 15_000}]})
    check("daily form created via REST with 2 lines", st == 201, (st, df))
    if st != 201:
        return
    eq("daily form: source form by default, net_total 1.035.000 (600.000 + 450.000 - 15.000)", (df["source"], df["net_total"]), ("form", 1_035_000))
    eq("daily form: net per line [600.000, 435.000]", [l["net"] for l in df["lines"]], [600_000, 435_000])
    dfn = submit_doc(api, "outlet_sales", df["name"], "submit the daily form")
    dd = api.get("outlet_sales", dfn)
    eq("daily form cogs: 40 ORI + 25 PDS through the recipes", dd["cogs_total"], 40 * 1698 + 25 * 2280)
    st, pvf = preview(hq, "daily_form", "daily-form-sample.csv", read_sample("daily-form-sample.csv"), CIMAHI)
    check("daily_form import: preview 3 rows, one record, no errors", st == 200 and pvf["rows"] == 3 and pvf["errors"] == [] and len(pvf["preview"]) == 1, (st, pvf))
    if st == 200:
        st, cf = hq.call("POST", f"/api/v1/imports/daily_form/{pvf['id']}/commit")
        check("daily_form commit creates a draft (source form)", st == 200 and len(cf.get("records", [])) == 1, (st, cf))
        if st == 200:
            rec = api.get("outlet_sales", cf["records"][0])
            eq("imported form: gross 1.290.000, discount 15.000, net 1.275.000", (rec["gross_total"], rec["discount_total"], rec["net_total"]), (1_290_000, 15_000, 1_275_000))
            st, resp = api.verb("outlet_sales", rec["name"], "submit")
            check("a second submitted daily form for the same outlet and day -> 409 duplicate_sales",
                  st == 409 and resp["error"]["code"] == "duplicate_sales", (st, resp))
    st, again = preview(hq, "daily_form", "daily-form-sample.csv", read_sample("daily-form-sample.csv"), CIMAHI)
    check("the daily form file again: duplicate true", st == 200 and again["duplicate"] is True, (st, again))
    # a corrected sheet: cancel the submitted form and drop the draft, then the corrected file imports and submits
    st, resp = api.verb("outlet_sales", dfn, "cancel")
    check("cancel the submitted daily form of Cimahi 04/11", st == 200, (st, resp))
    for d in api.find("outlet_sales", "outlet == '%s' && source == 'form' && docstatus == 0" % CIMAHI):
        api.call("DELETE", f"/api/v1/r/outlet_sales/{Api.q(d['name'])}")
    fixed = read_sample("daily-form-sample.csv").replace(b"TH-ORI,40,600000", b"TH-ORI,42,630000") + b"04/11/2026,TH-ISI,1,20000,0\n"
    st, pvc = preview(hq, "daily_form", "koreksi.csv", fixed, CIMAHI)
    check("corrected sheet (a qty changed, TH-ISI on two rows): previewed, not a duplicate, nothing skipped, no errors",
          st == 200 and pvc["duplicate"] is False and pvc["skipped"] == 0 and pvc["errors"] == [] and len(pvc["preview"]) == 1 and len(pvc["preview"][0]["lines"]) == 4, (st, pvc))
    if st == 200:
        st, cc = hq.call("POST", f"/api/v1/imports/daily_form/{pvc['id']}/commit")
        if check("corrected sheet committed", st == 200 and len(cc.get("records", [])) == 1, (st, cc)):
            cn = submit_doc(api, "outlet_sales", cc["records"][0], "submit the corrected daily form")
            eq("corrected form: gross 1.340.000 (630.000 + 450.000 + 240.000 + 20.000)", api.get("outlet_sales", cn)["gross_total"], 1_340_000)
    st, resp = api.create("outlet_sales", {"outlet": CIMAHI, "date": "2026-11-04", "lines": [{"item": "BRG-0007", "qty": 1, "discount": 99_999_999}]})
    check("discount above gross -> 422 'Diskon tidak boleh melebihi penjualan kotor'", st == 422 and any("Diskon tidak boleh" in x for x in messages(resp)), (st, resp))
    # a Moka outlet takes no daily form, by REST or by import (one sales source per outlet)
    st, resp = api.create("outlet_sales", {"outlet": DAGO, "date": "2026-11-05", "lines": [{"item": "BRG-0007", "qty": 1}]})
    check("a daily form for Dago (a Moka outlet) -> 422 'Outlet ini memakai Moka'", st == 422 and any("Outlet ini memakai Moka" in x for x in messages(resp)), (st, resp))
    st, pvd = preview(hq, "daily_form", "daily-form-sample.csv", read_sample("daily-form-sample.csv"), DAGO)
    check("the daily_form import for Dago: preview error 'Outlet ini memakai Moka'", st == 200 and any("Outlet ini memakai Moka" in e["message"] for e in pvd["errors"]), (st, pvd))
    st, resp = api.create("outlet_sales", {"date": "2026-11-04", "lines": [{"item": "BRG-0007", "qty": 1}]})
    check("outlet_sales without an outlet -> 422", st == 422, (st, resp))
    st, resp = api.create("outlet_sales", {"outlet": CIMAHI, "date": "2026-11-04", "lines": [{"item": "BRG-0007", "qty": -1, "gross": 0}]})
    check("a negative qty without a refund -> 422 'hanya boleh negatif di baris retur'", st == 422 and any("hanya boleh negatif di baris retur" in x for x in messages(resp)), (st, resp))
    st, resp = api.create("outlet_sales", {"outlet": CIMAHI, "date": "2026-11-04", "lines": [{"item": "BRG-0007", "qty": -5, "gross": 0, "refund": 1}]})
    check("a negative qty with a refund on a daily form (not Moka) -> 422", st == 422 and any("hanya boleh negatif di baris retur" in x for x in messages(resp)), (st, resp))
    # real refund shapes: a discounted line refunded (Discounts negative) and two partial refunds of one item on one
    # receipt at different times; the record's totals equal the file's (Net Sales sum 0 + 30.000 - 30.000)
    st, pvr = preview(hq, "moka_item_details", "moka-sample-refunds.csv", read_sample("moka-sample-refunds.csv"), DAGO)
    check("refund shapes: preview 5 rows, no errors (two partial refunds are not 'Baris ganda')", st == 200 and pvr["rows"] == 5 and pvr["errors"] == [], (st, pvr))
    if st == 200 and pvr["errors"] == []:
        st, cr = hq.call("POST", f"/api/v1/imports/moka_item_details/{pvr['id']}/commit")
        if check("refund shapes committed", st == 200 and len(cr.get("records", [])) == 1, (st, cr)):
            r = api.get("outlet_sales", cr["records"][0])
            eq("refund shapes: gross 90.000, discount 0, refund 90.000, net 0 (= Moka Net Sales)", (r["gross_total"], r["discount_total"], r["refund_total"], r["net_total"]), (90_000, 0, 90_000, 0))
            eq("imported record keeps source moka", r["source"], "moka")
    # source moka is the importer's (head office): outlet staff cannot type it to book a negative net (audit round 2
    # of #7, S1 and S1b)
    dago = CTX.get("staff_dago")
    if dago:
        st, resp = dago.create("outlet_sales", {"outlet": DAGO, "date": "2026-11-04", "source": "moka",
                                                "lines": [{"item": "BRG-0007", "qty": -5, "gross": 0, "refund": 500_000}]})
        check("outlet staff creating a source moka record with net -500.000 -> 403", st == 403, (st, resp))
        # Dago is a Moka outlet here (one source per outlet), so its staff cannot create a daily form record at all; the
        # "turn a form record into source moka" probe of #7 has no record to start from
        st, resp = dago.create("outlet_sales", {"outlet": DAGO, "date": "2026-11-04", "lines": [{"item": "BRG-0007", "qty": 1}]})
        check("outlet staff creating a daily form record at a Moka outlet -> 422 by the sales-source rule", st == 422, (st, resp))
        moka_rec = next(iter(api.find("outlet_sales", "source == 'moka' && outlet == '%s'" % DAGO)), None)
        if moka_rec:
            st, resp = dago.call("GET", f"/api/v1/r/outlet_sales/{Api.q(moka_rec['name'])}")
            check("outlet staff still reads its outlet's Moka records", st == 200, (st, resp))
    # Audit #7 S1b, restored (audit ulang test operator 6 Nov, finding 1): staff of a daily-form outlet turn their own
    # form record into source moka with a refund. Only the outlet_staff grant `if: source == 'form'`, read on the value
    # after the PATCH, stops it (source_matches_outlet allows moka at any outlet), so this probe guards that grant.
    cim = CTX.get("staff_cimahi")
    if not cim:
        fail("outlet staff of Cimahi signed in (form -> moka probe)", CTX.get("login_error", "no staff_cimahi"))
    else:
        st, own = cim.create("outlet_sales", {"outlet": CIMAHI, "date": "2026-11-06", "lines": [{"item": "BRG-0007", "qty": 1}]})
        if check("Cimahi staff creates a daily form record (201, source form)", st == 201 and own.get("source") == "form", (st, own)):
            st, resp = cim.patch("outlet_sales", own["name"], {"source": "moka", "lines": [{"item": "BRG-0007", "qty": -5, "gross": 0, "refund": 900_000}]})
            check("Cimahi staff turning it into source moka with net -900.000 -> 403", st == 403, (st, resp))
            st, resp = cim.patch("outlet_sales", own["name"], {"source": "moka"})
            check("Cimahi staff changing only source to moka -> 403", st == 403, (st, resp))
            kept = api.get("outlet_sales", own["name"])
            eq("the form record keeps source form and net 15.000 (1 x 15.000)", (kept["source"], kept["net_total"]), ("form", 15_000))
            api.call("DELETE", f"/api/v1/r/outlet_sales/{Api.q(own['name'])}")
    st, resp = api.create("outlet_sales", {"outlet": CIMAHI, "date": "2026-11-04", "lines": []})
    if st == 201:
        fail("min_rows 1: an outlet_sales without lines is refused", "201: the kernel saved it")
    else:
        check("outlet_sales without lines -> 422", st == 422, (st, resp))


# ---------- scenarios: multi-branch retail, Jalur A (demo-ritel): POS files in, stock per outlet, purchasing, consignment

RITEL = os.path.join(ROOT, "examples", "fixtures", "demo-ritel")
CBR, CLY = "OUT-01", "OUT-02"     # Cibiru imports Moka (and is the head office), Cileunyi Juragan's POS file template


def ritel_gen():
    """examples/fixtures/demo-ritel/generate.py as a module (the fixtures and the POS files of this run)."""
    import importlib.util
    if "gen" not in CTX:
        spec = importlib.util.spec_from_file_location("ritel_generate", os.path.join(RITEL, "generate.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        CTX["gen"] = mod
    return CTX["gen"]


def rows_all(api, model, where="", **q):
    """Every record of `model` matching `where`, paged by 200."""
    out, off = [], 0
    while True:
        page = api.list(model, limit=200, offset=off, **(dict(filter=where) if where else {}), **q)
        out += page
        if len(page) < 200:
            return out
        off += 200


def import_commit(cl, imp, filename, content, outlet=None, label=""):
    """Upload -> preview -> commit; returns (preview, commit response) or (preview, None) when the preview has errors."""
    fields = {"outlet": outlet} if outlet else {}
    st, pv = cl.upload(f"/api/v1/imports/{imp}", fields, filename, content)
    if not check(f"{label or imp}: preview without errors", st == 200 and not pv.get("errors"), (st, (pv or {}).get("errors", pv) if isinstance(pv, dict) else pv)):
        return pv, None
    st, c = cl.call("POST", f"/api/v1/imports/{imp}/{pv['id']}/commit")
    check(f"{label or imp}: commit", st == 200, (st, c))
    return pv, (c if st == 200 else None)


def submit_all(api, model, names, label):
    bad = []
    for n in names:
        st, resp = api.verb(model, n, "submit")
        if st != 200:
            bad.append((n, st, resp))
    check(f"{label}: all {len(names)} submitted", not bad, bad[:3])


def scenario_ritel_fixtures(api):
    g = ritel_gen()
    outlets = {o["name"]: o for o in api.list("outlet")}
    eq("fixtures: 2 outlets, Cibiru (Moka, head office) and Cileunyi (POS file)",
       sorted((n, bool(o.get("is_head_office")), o.get("moka_name") or "", o.get("sales_source") or "") for n, o in outlets.items()),
       [(CBR, True, g.MOKA_NAME, ""), (CLY, False, "", "file")])
    its = rows_all(api, "item")
    eq("fixtures: 200 items, 8 of them consigned", (len(its), len([i for i in its if i.get("consignor")])), (200, 8))
    barcodes = [i["barcode"] for i in its if i.get("barcode")]
    check("fixtures: 192 barcodes, all distinct, EAN-13 in the in-store range 200-299",
          len(barcodes) == 192 and len(set(barcodes)) == 192 and all(len(b) == 13 and b[:1] == "2" for b in barcodes), len(barcodes))
    eq("fixtures: 11 item categories", len(api.list("item_category")), 11)
    groups = {}
    for i in its:
        if i.get("variant_group"):
            groups.setdefault(i["variant_group"], []).append(i["variant_label"])
    check("fixtures: variants are items sharing variant_group (MI-KUAH has 4)", sorted(groups.get("MI-KUAH", [])) == ["ayam bawang", "baso", "kari", "soto"], groups.get("MI-KUAH"))
    st, resp = api.create("item", {"item_name": "Barcode kembar", "stock_uom": "UOM-001", "barcode": barcodes[0]})
    check("a second item with the same barcode -> 409/422 (barcode is unique)", st in (409, 422), (st, resp))


def scenario_ritel_item_master(api):
    g = ritel_gen()
    hq = CTX.get("hq") or api
    pv, c = import_commit(hq, "item_master", "daftar-barang-baru.csv", g.item_master_csv(), label="item list import (6 new items)")
    if not c:
        return
    eq("item list: 6 items created", len(c["records"]), 6)
    by = {i["sku"]: i for i in rows_all(api, "item", "sku.startsWith('TKC-02')")}
    kaos = sorted((by[s]["variant_group"], by[s]["variant_label"]) for s in ("TKC-0203", "TKC-0204", "TKC-0205"))
    eq("item list: three variants of KAOSKAKI (S, M, L)", kaos, [("KAOSKAKI", "L"), ("KAOSKAKI", "M"), ("KAOSKAKI", "S")])
    eq("item list: category matched by name, uom by name", (by["TKC-0201"]["category"], by["TKC-0201"]["stock_uom"]), ("KTG-001", "UOM-001"))
    eq("item list: the consigned row gets consignor and consignment_cost", (by["TKC-0206"].get("consignor"), by["TKC-0206"].get("consignment_cost")), ("VND-0008", 11000))
    eq("item list: an empty Penitip leaves the item the shop's own", by["TKC-0201"].get("consignor") or "", "")
    st, pv2 = hq.upload("/api/v1/imports/item_master", {}, "daftar-barang-baru.csv", g.item_master_csv())
    check("the same item list again: duplicate", st == 200 and pv2["duplicate"] is True, (st, pv2))
    bad = (b"SKU,Barcode,Nama barang,Kategori,Grup varian,Varian,Satuan,Harga jual,Harga pokok,Penitip,Harga setor\n"
           b"TKC-0299,,Barang uji,Kategori salah,,,Pcs,1000,800,,\n")
    st, pv3 = hq.upload("/api/v1/imports/item_master", {}, "salah.csv", bad)
    check("an unknown category is reported per row", st == 200 and any("Kategori salah" in e["message"] for e in pv3["errors"]), (st, pv3))


def scenario_ritel_opening_stock(api):
    g, today = ritel_gen(), kernel_today()
    CTX["today"] = today
    hq = CTX.get("hq") or api
    by = {i["sku"]: i for i in rows_all(api, "item")}
    for outlet, label in ((CBR, "Cibiru"), (CLY, "Cileunyi")):
        csv = g.opening_stock_csv(today, outlet)
        pv, c = import_commit(hq, "opening_stock", f"stok-awal-{label.lower()}.csv", csv, outlet, f"opening stock {label}")
        if not c:
            continue
        eq(f"opening stock {label}: one goods receipt from 'Saldo awal'", (len(c["records"]), pv["preview"][0]["vendor"]), (1, "VND-0001"))
        gr = api.get("goods_receipt", c["records"][0])
        want = sum(q * by[s]["standard_cost"] for s, q in g.opening_qty(today, outlet).items())
        eq(f"opening stock {label}: value = sum qty x cost price to the rupiah", gr["total_value"], want)
        name = submit_doc(api, "goods_receipt", gr["name"], f"submit opening stock {label}")
        vals = sum(b["value"] for b in rows_all(api, "stock_balance", f"outlet == '{outlet}'"))
        eq(f"opening stock {label}: stock value of the outlet = receipt value", vals, want)
        CTX.setdefault("opening", {})[outlet] = name
        st, pv2 = hq.upload("/api/v1/imports/opening_stock", {"outlet": outlet}, "lagi.csv", csv)
        check(f"opening stock {label} uploaded again: duplicate", st == 200 and pv2["duplicate"] is True, (st, pv2.get("duplicate")))
    eq("'Saldo awal' gets no payable (no vendor_bill)", api.get("vendor", "VND-0001").get("outstanding") or 0, 0)
    sku = next(iter(g.opening_qty(today, CBR)))
    item = by[sku]["name"]
    b = balance(api, CBR, item)
    eq(f"opening stock: the average cost of {sku} starts at its cost price", b["avg_cost"] if b else None, by[sku]["standard_cost"])


def scenario_ritel_consignment_in(api):
    """The consignors deliver to Cibiru before the sales window: goods receipts without PO, at the consignment cost."""
    g, today = ritel_gen(), CTX.get("today") or kernel_today()
    import datetime
    sold = {}
    for rows in g.daily_sales(today, CBR).values():
        for it, q in rows:
            if it.get("consignor"):
                sold[it["sku"]] = sold.get(it["sku"], 0) + q
    CTX["consigned_sold"] = sold
    by = {i["sku"]: i for i in rows_all(api, "item")}
    day = (today - datetime.timedelta(days=g.DAYS + 1)).isoformat()
    for v in g.PENITIP:
        lines = [{"item": i["name"], "qty": sold.get(i["sku"], 0) + 12, "rate": i["consignment_cost"]} for i in by.values() if i.get("consignor") == v and i["sku"] in sold]
        st, gr = api.create("goods_receipt", {"vendor": v, "outlet": CBR, "received_on": day, "notes": "Barang titipan", "items": lines})
        if check(f"consignment in from {v}: goods receipt without PO", st == 201, (st, gr)):
            submit_doc(api, "goods_receipt", gr["name"], f"submit consignment receipt {v}")
        eq(f"consignment in from {v}: no payable", api.get("vendor", v).get("outstanding") or 0, 0)


def scenario_ritel_sales(api):
    g, today = ritel_gen(), CTX.get("today") or kernel_today()
    import datetime
    hq, staff = CTX.get("hq") or api, CTX.get("staff_cly")
    moka, posf = g.moka_csv(today), g.pos_file_csv(today)
    # wrong outlet / wrong source are refused before anything lands
    st, pv = hq.upload("/api/v1/imports/moka_item_details", {"outlet": CLY}, "moka.csv", moka)
    check("a Moka file uploaded for Cileunyi (no moka_name) is refused per row", st == 200 and pv["errors"], (st, (pv or {}).get("errors", [])[:1]))
    st, pv = hq.upload("/api/v1/imports/pos_sales_file", {"outlet": CBR}, "pos.csv", posf)
    check("a POS file uploaded for Cibiru (a Moka outlet) is refused: one source per outlet",
          st == 200 and any("Sumber penjualan" in e["message"] for e in pv["errors"]), (st, (pv or {}).get("errors", [])[:1]))
    if staff is not None:
        st, pv = staff.upload("/api/v1/imports/pos_sales_file", {"outlet": CLY}, "pos.csv", posf)
        check("outlet_staff may not import sales -> 403", st == 403, st)
    t0 = time.time()
    pv, c = import_commit(hq, "moka_item_details", "moka-item-details-cibiru.csv", moka, CBR, f"Moka Cibiru, {g.DAYS} days")
    CTX["timing_moka"] = (pv.get("rows"), round(time.time() - t0, 1))
    if c:
        eq(f"Moka Cibiru: one draft per day ({g.DAYS})", len(c["records"]), g.DAYS)
        t0 = time.time()
        submit_all(api, "outlet_sales", c["records"], "Moka Cibiru")
        CTX["timing_moka_submit"] = round(time.time() - t0, 1)
    pv, c = import_commit(hq, "pos_sales_file", "penjualan-file-pos-cileunyi.csv", posf, CLY, f"POS file Cileunyi, {g.DAYS} days")
    if c:
        eq(f"POS file Cileunyi: one draft per day ({g.DAYS})", len(c["records"]), g.DAYS)
        submit_all(api, "outlet_sales", c["records"], "POS file Cileunyi")
    st, pv = hq.upload("/api/v1/imports/pos_sales_file", {"outlet": CLY}, "lagi.csv", posf)
    check("the POS file again: duplicate", st == 200 and pv["duplicate"] is True, (st, pv.get("duplicate")))
    # a re-export of the last day whose totals changed (qty +2 on every row) skips the rows already imported: no double count
    lines = posf.decode().splitlines()
    last = lines[-1].split(",")[0]
    redo = [lines[0]] + [",".join(r[:4] + [str(int(r[4]) + 2)] + r[5:]) for r in (l.split(",") for l in lines[1:]) if r[0] == last and r[2] == "Jual"]
    st, pv = hq.upload("/api/v1/imports/pos_sales_file", {"outlet": CLY}, "ulang.csv", ("\n".join(redo) + "\n").encode())
    check("a re-export of a day with changed totals: every row skipped (key without Qty), nothing to add", st == 200 and pv["skipped"] == len(redo) - 1 and pv["duplicate"] is True, (st, pv.get("skipped"), pv.get("duplicate")))
    sales = rows_all(api, "outlet_sales", "docstatus == 1")
    for outlet, label in ((CBR, "Cibiru"), (CLY, "Cileunyi")):
        want = {d.isoformat(): v for d, v in g.sales_net(today, outlet).items()}
        got = {}
        for s in sales:
            if s["outlet"] == outlet:
                got[s["date"]] = got.get(s["date"], 0) + s["net_total"]
        bad = {d: (got.get(d), want[d]) for d in want if got.get(d) != want[d]}
        check(f"{label}: net sales per day = the POS report to the rupiah, all {len(want)} days", not bad, list(bad.items())[:3])
    refunds = [s for s in sales if s["refund_total"]]
    check("refund lines lower net sales (refund_total > 0 on some days)", refunds, len(refunds))
    # stock: opening - sold, refunds do not come back
    by = {i["sku"]: i for i in rows_all(api, "item")}
    sku = max((it for it in g.items() if g.own(it)), key=lambda it: it["_weight"])["sku"]
    sold = sum(q for rows in g.daily_sales(today, CBR).values() for it, q in rows if it["sku"] == sku)
    b = balance(api, CBR, by[sku]["name"])
    eq(f"Cibiru stock of the best seller {sku} = opening - sold", b["qty"] if b else None, g.opening_qty(today, CBR)[sku] - sold)
    # last sale dates: slow items were last sold more than 30 days ago, never-sold items have none
    cutoff = (today - datetime.timedelta(days=30)).isoformat()
    bals = rows_all(api, "stock_balance", f"outlet == '{CBR}'")
    sku_of = {i["name"]: i["sku"] for i in by.values()}
    dated = [b for b in bals if b.get("last_sale_date")]
    if not dated:
        CTX["no_last_sale"] = True
        gap("stock_balance.last_sale_date written on sale moves", "rangka-os#59", "kernel without the field writer: slow movers stay empty")
        return
    late = {sku_of[b["item"]] for b in dated if b["last_sale_date"][:10] < cutoff}
    check("stock_balance.last_sale_date (kernel): the slow items' last sale is over 30 days ago", g.SLOW <= late, sorted(g.SLOW - late))
    never = [b for b in bals if sku_of.get(b["item"]) in g.NEVER]
    check("never-sold items keep an empty last_sale_date", never and all(not b.get("last_sale_date") for b in never), never[:1])


def scenario_ritel_loyverse(api):
    """A third outlet on Loyverse: its export (guessed headers) imports with the refund as a negative line."""
    g, today = ritel_gen(), CTX.get("today") or kernel_today()
    hq = CTX.get("hq") or api
    st, o = api.create("outlet", {"outlet_name": "Toko Contoh Uji Loyverse", "code": "UJI-LOY", "sales_source": "loyverse"})
    if not check("create a Loyverse outlet", st == 201, (st, o)):
        return
    pv, c = import_commit(hq, "loyverse_receipts", "loyverse.csv", g.loyverse_csv(today), o["name"], "Loyverse receipts by item")
    if not c:
        return
    s = api.get("outlet_sales", c["records"][0])
    a, b = g.items()[0], g.items()[1]
    eq("Loyverse: source loyverse, 3 lines, the refund as qty -1", (s["source"], len(s["lines"]), sorted(l["qty"] for l in s["lines"])), ("loyverse", 3, [-1, 3, 5]))
    eq("Loyverse: net sales = sales - discount - refund", s["net_total"], a["rate"] * 3 + b["rate"] * 5 - 500 - a["rate"])
    st, pv = hq.upload("/api/v1/imports/pos_sales_file", {"outlet": o["name"]}, "pos.csv", g.pos_file_csv(today))
    check("a POS template file for the Loyverse outlet is refused (one source per outlet)", st == 200 and pv["errors"], st)


def scenario_ritel_purchase(api):
    """PO in cartons to a supplier for Cileunyi, partial receipt, bill, payment."""
    hq = CTX.get("hq") or api
    by = {i["sku"]: i for i in rows_all(api, "item")}
    item = by["TKC-0014"]   # Mi goreng instan original (40 per carton)
    conv = rows_all(api, "uom_conversion", f"item == '{item['name']}'")
    if not check("the item has a carton packing", conv, item["name"]):
        return
    before = balance(api, CLY, item["name"])
    st, po = hq.create("purchase_order", {"vendor": "VND-0002", "outlet": CLY, "items": [{"item": item["name"], "qty": 5, "packing": conv[0]["name"], "rate": 108_000}]})
    if not check("head office orders 5 cartons for Cileunyi", st == 201 and po["grand_total"] == 540_000, (st, po)):
        return
    pon = submit_doc(hq, "purchase_order", po["name"], "submit the PO")
    st, r = hq.action("purchase_order", pon, "receive")
    check("receive: a draft goods receipt", st == 200 and r.get("open", {}).get("model") == "goods_receipt", (st, r))
    gr = api.get("goods_receipt", r["open"]["name"])
    gr["items"][0]["qty"] = 3
    st, gr2 = hq.patch("goods_receipt", gr["name"], {"items": gr["items"]})
    grn = submit_doc(hq, "goods_receipt", gr["name"], "submit a partial receipt (3 of 5 cartons)")
    after = balance(api, CLY, item["name"])
    eqf("Cileunyi stock +120 pcs (3 cartons x 40)", (after["qty"] if after else 0) - (before["qty"] if before else 0), 120)
    eq("PO received_pct 60", api.get("purchase_order", pon)["received_pct"], 60)
    st, vb = hq.create("vendor_bill", {"vendor": "VND-0002", "purchase_order": pon, "goods_receipt": grn, "bill_no": "INV-CDS-0912", "bill_date": (CTX.get("today") or kernel_today()).isoformat(),
                                       "items": [{"item": item["name"], "qty": 3, "uom": "UOM-004", "rate": 108_000}]})
    if not check("bill for the 3 cartons", st == 201 and vb["amount_due"] == 324_000, (st, vb)):
        return
    vbn = submit_doc(hq, "vendor_bill", vb["name"], "submit the bill")
    eq("payable to the supplier 324.000", api.get("vendor", "VND-0002")["outstanding"], 324_000)
    st, p = hq.create("payment", {"direction": "pay", "vendor": "VND-0002", "amount": 324_000, "allocations": [{"vendor_bill": vbn, "amount": 324_000}]})
    if check("pay the bill", st == 201, (st, p)):
        submit_doc(hq, "payment", p["name"], "submit the payment")
    eq("payable back to 0 after the payment", api.get("vendor", "VND-0002")["outstanding"], 0)


def scenario_ritel_transfer_count(api):
    hq, staff = CTX.get("hq") or api, CTX.get("staff_cly")
    by = {i["sku"]: i for i in rows_all(api, "item")}
    item = by["TKC-0034"]["name"]
    b0c, b0l = balance(api, CBR, item), balance(api, CLY, item)
    st, t = hq.create("stock_transfer", {"from_outlet": CBR, "to_outlet": CLY, "items": [{"item": item, "qty": 24}]})
    tn = submit_doc(hq, "stock_transfer", t["name"], "transfer 24 pcs Cibiru -> Cileunyi") if st == 201 else None
    if not tn:
        return
    eq("in transit: Cibiru -24, Cileunyi unchanged", (round(balance(api, CBR, item)["qty"] - b0c["qty"], 6), round(balance(api, CLY, item)["qty"] - b0l["qty"], 6)), (-24, 0))
    cl = staff or hq
    st, rc = cl.create("stock_transfer_receipt", {"stock_transfer": tn, "outlet": CLY, "items": [{"item": item, "qty": 24}]})
    if check("Cileunyi staff records the arrival", st == 201, (st, rc)):
        submit_doc(cl, "stock_transfer_receipt", rc["name"], "submit the transfer receipt")
    eq("after the receipt: Cileunyi +24", round(balance(api, CLY, item)["qty"] - b0l["qty"], 6), 24)
    sys_qty = balance(api, CLY, item)["qty"]
    st, sc = cl.create("stock_count", {"outlet": CLY, "items": [{"item": item, "counted_qty": sys_qty - 2}]})
    if check("monthly count at Cileunyi (2 short)", st == 201, (st, sc)):
        scn = submit_doc(cl, "stock_count", sc["name"], "submit the count")
        line = api.get("stock_count", scn)["items"][0]
        eq("count: difference -2 posted", (line["system_qty"], line["difference"]), (sys_qty, -2))


def scenario_ritel_settlement(api):
    """Month-end with consignor VND-0006: what sold (read from the sale moves) becomes a draft vendor_bill; the rest goes back."""
    g, today = ritel_gen(), CTX.get("today") or kernel_today()
    import datetime
    hq = CTX.get("hq") or api
    v = g.PENITIP[0]
    by = {i["name"]: i for i in rows_all(api, "item", f"consignor == '{v}'")}
    sold = {}
    for m in rows_all(api, "stock_move", f"voucher_type == 'outlet_sales' && outlet == '{CBR}'"):
        if m["item"] in by:
            sold[m["item"]] = sold.get(m["item"], 0) + m["sold_qty"]
    want_sold = {i["name"]: CTX.get("consigned_sold", {}).get(i["sku"], 0) for i in by.values()}
    eq("consigned qty sold (stock_move.sold_qty, as the chart sums it) = the POS sales", {k: round(q, 6) for k, q in sold.items() if q}, {k: q for k, q in want_sold.items() if q})
    month = (today - datetime.timedelta(days=1)).isoformat()[:7]
    lines = [{"item": n, "qty_received": want_sold[n] + 12, "qty_sold": sold.get(n, 0), "qty_returned": 12} for n in by if want_sold.get(n)]
    st, ks = hq.create("consignment_settlement", {"consignor": v, "month": month, "items": lines})
    if not check("settlement for Bu Sari: draft", st == 201, (st, ks)):
        return
    total = sum(round(sold.get(n, 0) * by[n]["consignment_cost"]) for n in by)
    eq("settlement: rate defaults to the item's consignment cost, total = sold x rate, nothing left on the shelf",
       (ks["total"], [l["qty_left"] for l in ks["items"]]), (total, [0] * len(lines)))
    st, bad = hq.patch("consignment_settlement", ks["name"], {"items": [dict(lines[0], qty_returned=999)]})
    check("sold + returned above what came in -> 422", st == 422, (st, bad))
    st, bad = hq.patch("consignment_settlement", ks["name"], {"items": [dict(lines[0], rate=0)]})
    check("a sold line without a consignment price -> 422", st == 422, (st, bad))
    st, bad = hq.patch("consignment_settlement", ks["name"], {"month": "2026-13"})
    check("month must read YYYY-MM -> 422", st == 422, (st, bad))
    st, resp = hq.verb("consignment_settlement", ks["name"], "submit")
    check("submit the settlement", st == 200, (st, resp))
    ksn = resp.get("name", ks["name"]) if st == 200 else ks["name"]
    st, dup = hq.create("consignment_settlement", {"consignor": v, "month": month, "items": lines})
    check("a second settlement for the same consignor and month is refused (one live settlement per month)", st in (409, 422), (st, dup))
    bills = rows_all(api, "vendor_bill", f"vendor == '{v}'")
    check("flow consignment_bill: a draft vendor_bill, bill_no TTP/<month>, amount = settlement total",
          len(bills) == 1 and bills[0]["docstatus"] == 0 and bills[0]["bill_no"] == "TTP/" + month and bills[0]["amount_due"] == total, bills)
    if not bills:
        return
    bn = submit_doc(hq, "vendor_bill", bills[0]["name"], "submit the consignor's bill")
    eq("payable to the consignor = settlement total", api.get("vendor", v)["outstanding"], total)
    st, resp = hq.verb("consignment_settlement", ksn, "cancel")
    check("a settlement whose bill is submitted cannot be cancelled", st >= 400 and api.get("consignment_settlement", ksn)["docstatus"] == 1, (st, resp))
    eq("payable unchanged after the refused cancel", api.get("vendor", v)["outstanding"], total)
    # correction: cancel the bill, then the settlement, amend it with one unit less of the first item
    st, resp = hq.verb("vendor_bill", bn, "cancel")
    check("cancel the bill first", st == 200, (st, resp))
    st, resp = hq.verb("consignment_settlement", ksn, "cancel")
    check("then the settlement cancels", st == 200, (st, resp))
    st, am = hq.verb("consignment_settlement", ksn, "amend")
    check("amend the cancelled settlement", st in (200, 201), (st, am))
    amn = (am.get("record") or am).get("name") if isinstance(am, dict) else None
    total2 = total - by[lines[0]["item"]]["consignment_cost"]
    fix = [dict(lines[0], qty_sold=lines[0]["qty_sold"] - 1, qty_returned=13)] + lines[1:]
    st, resp = hq.patch("consignment_settlement", amn, {"items": fix})
    check("the amended copy takes the corrected lines", st == 200, (st, resp))
    submit_doc(hq, "consignment_settlement", amn, "submit the amended settlement")
    live = rows_all(api, "vendor_bill", f"vendor == '{v}' && docstatus != 2")
    check("one live bill for the month, from the corrected settlement", len(live) == 1 and live[0]["amount_due"] == total2 and live[0]["docstatus"] == 0, [(b["name"], b["docstatus"], b["amount_due"]) for b in live])
    if live:
        submit_doc(hq, "vendor_bill", live[0]["name"], "submit the corrected bill")
    eq("payable to the consignor = corrected total", api.get("vendor", v)["outstanding"], total2)
    st, resp = hq.call("DELETE", f"/api/v1/r/consignment_settlement/{Api.q(ksn)}")
    check("head office cannot delete the cancelled settlement (its month stays taken) -> 403", denied(st, resp), (st, resp))
    st, dup = hq.create("consignment_settlement", {"consignor": v, "month": month, "items": lines})
    check("a new settlement for the month is still refused while the amended one is live", st in (409, 422), (st, dup))
    st, resp = api.call("DELETE", f"/api/v1/r/consignment_settlement/{Api.q(ksn)}")
    if st in (200, 204):
        gap("owner deletes a cancelled settlement with an amended copy", "rangka-os#59", "kernel without has_amendments: the month's key is freed")
    else:
        check("not even the owner can delete a cancelled settlement that has an amended copy -> 409 has_amendments (rangka-os#59)",
              st == 409 and (resp or {}).get("error", {}).get("code") == "has_amendments", (st, resp))
    st, adj = hq.create("stock_adjustment", {"outlet": CBR, "reason": "consignment_return", "items": [{"item": n, "qty": 12} for n in by if want_sold.get(n)]})
    if check("unsold goods back to the consignor: stock_adjustment reason consignment_return", st == 201, (st, adj)):
        submit_doc(hq, "stock_adjustment", adj["name"], "submit the return")
        left = [balance(api, CBR, n)["qty"] for n in by if want_sold.get(n)]
        check("consigned stock of Bu Sari back to 0", all(abs(q) < 1e-9 for q in left), left)


def scenario_ritel_takings(api):
    g, today = ritel_gen(), CTX.get("today") or kernel_today()
    hq = CTX.get("hq") or api
    pv, c = import_commit(hq, "takings_file", "setoran-cibiru.csv", g.takings_csv(today), CBR, "daily takings Cibiru (7 days)")
    if not c:
        return
    submit_all(api, "outlet_takings", c["records"], "takings")
    rows = rows_all(api, "outlet_takings", f"outlet == '{CBR}' && docstatus == 1")
    short = [r for r in rows if r["short"]]
    eq("takings: one day short, by Rp 50.000 (cash)", [(r["difference_total"]) for r in short], [-50_000])
    net = {d.isoformat(): v for d, v in g.sales_net(today, CBR).items()}
    eq("takings: what the POS expected = net sales of that day", {r["date"]: r["expected_total"] for r in rows}, {r["date"]: net[r["date"]] for r in rows})
    eq("takings: QRIS MDR 0,3% recorded as fee, not as shortfall", all(r["difference_total"] in (0, -50_000) for r in rows) and all(r["fee_total"] > 0 for r in rows), True)


def scenario_dashboards_ritel(api):
    g, today = ritel_gen(), CTX.get("today") or kernel_today()
    import datetime
    since30 = (today - datetime.timedelta(days=30)).isoformat()
    sales = rows_all(api, "outlet_sales", "docstatus == 1")
    ws = dashboard(api, "ritel_overview", 30)
    in30 = [s for s in sales if s["date"] >= since30]
    if ws:
        eq("ritel_overview net_period (30 days)", num(ws["net_period"]), sum(s["net_total"] for s in in30))
        eq("ritel_overview margin_period", num(ws["margin_period"]), sum(s["margin"] for s in in30))
        eq("ritel_overview net by outlet", chart_map(ws["net_by_outlet"]), {o: sum(s["net_total"] for s in in30 if s["outlet"] == o) for o in {s["outlet"] for s in in30}})
        eq("ritel_overview takings_short = -50.000", num(ws["takings_short"]), -50_000)
        check("ritel_overview lists the Cileunyi bestseller as negative stock", ws["perlu_stok_minus"]["total"] >= 1, ws["perlu_stok_minus"].get("total"))
    ws = dashboard(api, "ritel_stock", 30)
    if ws:
        bal = rows_all(api, "stock_balance")
        eq("ritel_stock stock_value_total", num(ws["stock_value_total"]), sum(b["value"] for b in bal))
        cons = {i["name"] for i in rows_all(api, "item") if i.get("consignor")}
        eq("ritel_stock consigned stock value (link-of-link filter item.consignor)", num(ws["stock_value_consigned"]), sum(b["value"] for b in bal if b["item"] in cons))
        cutoff = (today - datetime.timedelta(days=30)).isoformat()
        slow = [b for b in bal if b["qty"] > 0 and b.get("last_sale_date") and b["last_sale_date"][:10] < cutoff]
        eq("ritel_stock slow movers (qty > 0, last sale over 30 days ago)", (num(ws["slow_count"]), ws["slow_movers"]["total"]), (len(slow), len(slow)))
        if CTX.get("no_last_sale"):
            gap("ritel_stock slow movers listed", "rangka-os#59", "no last_sale_date on this kernel: the list is empty, not faked")
        else:
            check("ritel_stock: at least the 9 generated slow items per outlet are listed", len(slow) >= len(g.SLOW), len(slow))
        top = ws["best_sellers_qty"]["data"][0] if ws["best_sellers_qty"].get("data") else {}
        qty = {}
        for o in (CBR, CLY):
            for d, rows in g.daily_sales(today, o).items():
                if d.isoformat() >= since30:
                    for it, q in rows:
                        qty[it["name"]] = qty.get(it["name"], 0) + q
        most = max(qty.values())
        check("ritel_stock best seller by qty (30 days) = an item the POS files sold most (positive sold_qty)",
              round(top.get("value", 0)) == most and qty.get(top.get("key")) == most, (top, most))
        check("ritel_stock best sellers are positive and sorted high to low", [p["value"] for p in ws["best_sellers_qty"]["data"]] == sorted((p["value"] for p in ws["best_sellers_qty"]["data"]), reverse=True)
              and all(p["value"] > 0 for p in ws["best_sellers_qty"]["data"]), ws["best_sellers_qty"]["data"][:3])
        check("ritel_stock consigned sold chart has data", ws["consigned_sold"].get("data"), ws["consigned_sold"])
        if not CTX.get("no_last_sale"):
            never = {i["name"] for i in rows_all(api, "item") if i["sku"] in g.NEVER}
            listed = {r["item"] for r in ws["never_sold"].get("rows", [])}
            check("ritel_stock never-sold list holds the generated never-sold items", never <= listed, (sorted(never - listed), ws["never_sold"].get("total")))
    ws = dashboard(api, "ritel_tax", 30)
    if ws:
        eq("ritel_tax turnover (30 days)", num(ws["turnover"]), sum(s["net_total"] for s in in30))
        eq("ritel_tax PPh final 0,5% = sum of round(net x 0,5%) per record (non-PKP org)", num(ws["pph_final_total"]), sum(((s["net_total"] - s["ppn_in_sales"]) * 5 + 500) // 1000 for s in in30))   # kernel round() is half up
        eq("ritel_tax PPN Digunggung 0 for a non-PKP org", num(ws["ppn_total"]), 0)


def scenario_ritel_access(api):
    staff = CTX.get("staff_cly")
    if staff is None:
        fail("ritel access: outlet staff signed in", CTX.get("login_error"))
        return
    mine = staff.list("outlet_sales", limit=200)
    check("Cileunyi staff sees only Cileunyi sales", mine and all(s["outlet"] == CLY for s in mine), {s["outlet"] for s in mine})
    st, resp = staff.call("GET", "/api/v1/r/consignment_settlement")
    check("outlet_staff cannot see consignment settlements -> 403", denied(st, resp), (st, resp))
    st, resp = staff.create("outlet_takings", {"outlet": CBR, "date": "2026-11-01", "lines": [{"method": "cash", "expected": 1}]})
    check("Cileunyi staff cannot record Cibiru's takings -> 403/404", st in (403, 404, 422), (st, resp))
    # audit #7 S1 for the POS sources ritel_cabang adds: a refund line (net < 0) is allowed only on an imported record,
    # so staff cannot type source file or loyverse at their own outlet, nor turn a record they can write into one
    for src in ("file", "loyverse"):
        st, resp = staff.create("outlet_sales", {"outlet": CLY, "date": "2026-11-04", "source": src,
                                                 "lines": [{"item": "BRG-0001", "qty": -5, "gross": 0, "refund": 500_000}]})
        check(f"outlet staff creating a source {src} record with net -500.000 -> 403", denied(st, resp), (st, resp))
    imported = next(iter(api.find("outlet_sales", "source == 'file' && outlet == '%s' && docstatus == 0" % CLY)), None)
    if imported is None:
        st, imported = api.create("outlet_sales", {"outlet": CLY, "date": "2026-11-05", "source": "file", "lines": [{"item": "BRG-0001", "qty": 1}]})
    if imported and imported.get("name"):
        st, resp = staff.patch("outlet_sales", imported["name"], {"lines": [{"item": "BRG-0001", "qty": -5, "gross": 0, "refund": 900_000}]})
        check("outlet staff editing a source file draft into net -900.000 -> 403", denied(st, resp), (st, resp))
        st, resp = staff.verb("outlet_sales", imported["name"], "submit")
        check("outlet staff submitting a source file draft -> 403", denied(st, resp), (st, resp))
        if api.get("outlet_sales", imported["name"]).get("date") == "2026-11-05":
            api.call("DELETE", f"/api/v1/r/outlet_sales/{Api.q(imported['name'])}")


def setup_demo_ritel(api, k):
    st, me = api.call("GET", "/auth/me")
    org = ((me or {}).get("org") or {}).get("id") or "toko_contoh"
    CTX.update(kernel=k, org=org)
    pw = secrets.token_urlsafe(18)   # throwaway password of a throwaway database
    people = [("hq", "dewi@tokocontoh.test", "head_office", "", "USR-001", "Dewi Contoh"),
              ("staff_cly", "agus.cileunyi@tokocontoh.test", "outlet_staff", CLY, "USR-003", "Agus Contoh")]
    for key, email, role, outlets, rec, name in people:
        code, out = k.user_add(email, role, org, name, outlets=outlets, record=rec, password=pw)
        cl = Api.login(k.base, email, pw) if code == 0 else None
        if cl is None:
            CTX["login_error"] = f"{email}: " + (out if code else "login refused")
            fail("setup: add and sign in the members", CTX["login_error"])
            return
        CTX[key] = cl
    CTX["member_pw"] = pw



def setup_demo_fnb(api, k):
    """Members for the access checks: head office, and outlet staff of Dago and Braga (rangka user add, contract 0010 §7)."""
    st, me = api.call("GET", "/auth/me")
    org = ((me or {}).get("org") or {}).get("id") or (me or {}).get("user", {}).get("org") or "kopi-senja"
    CTX.update(kernel=k, org=org)
    pw = secrets.token_urlsafe(18)   # a throwaway password for this run's throwaway database
    people = [("hq", "rina@tahucontoh.test", "head_office", "", "USR-001", "Rina Contoh"),
              ("staff_dago", "yusuf.dago@tahucontoh.test", "outlet_staff", DAGO, "USR-002", "Yusuf Contoh"),
              ("staff_braga", "sinta.braga@tahucontoh.test", "outlet_staff", BRAGA, "USR-003", "Sinta Contoh"),
              # Cimahi keeps the daily form: its staff can make a form record, so the "form -> moka" probe has one
              ("staff_cimahi", "dewi.cimahi@tahucontoh.test", "outlet_staff", CIMAHI, "", "Dewi Contoh")]
    for key, email, role, outlets, rec, name in people:
        code, out = k.user_add(email, role, org, name, outlets=outlets, record=rec, password=pw)
        if code != 0:
            CTX["login_error"] = f"rangka user add {email} failed: {out}"
            fail("setup: add and sign in the members", CTX["login_error"])
            return
        cl = Api.login(k.base, email, pw)
        if cl is None:
            CTX["login_error"] = f"login as {email} refused"
            fail("setup: add and sign in the members", CTX["login_error"])
            return
        CTX[key] = cl


# Scenarios of the newer sites live in their own files (tests/day_one_<app>.py) and take this file's helpers.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import day_one_grosir  # noqa: E402
import day_one_produksi  # noqa: E402
day_one_grosir.bind(sys.modules[__name__])
day_one_produksi.bind(sys.modules[__name__])

SCENARIOS = {
    "kopi-senja": [scenario_invoicing, scenario_dashboards_crm, scenario_permissions, scenario_crm_roles, scenario_payment_methods],
    "demo-jasa": [scenario_masters_jasa, scenario_quotation, scenario_line_discount_chain, scenario_cross_links, scenario_fx_jasa, scenario_print_descriptions, scenario_orders, scenario_workforce, scenario_projects, scenario_payments, scenario_procurement,
                  scenario_prints_jasa, scenario_tax_jasa, scenario_roles_jasa, scenario_billing_milestones, scenario_project_costs, scenario_project_margin, scenario_manpower_recap, scenario_cost_visibility, scenario_milestone_money, scenario_permissions, scenario_dashboards_jasa, scenario_money_rail, scenario_payment_methods, scenario_credit_limit_warning, scenario_payment_instruction, scenario_xendit_callbacks],
    "demo-fnb": [scenario_fnb_fixtures, scenario_stock, scenario_stock_access, scenario_po_approval_fnb, scenario_outlet_sales, scenario_dashboards_fnb, scenario_prints_fnb, scenario_print_descriptions, scenario_permissions, scenario_payment_methods],
    "demo-ritel": [scenario_ritel_fixtures, scenario_ritel_item_master, scenario_ritel_opening_stock, scenario_ritel_consignment_in, scenario_ritel_sales, scenario_ritel_loyverse,
                   scenario_ritel_purchase, scenario_ritel_transfer_count, scenario_ritel_settlement, scenario_ritel_takings, scenario_dashboards_ritel, scenario_ritel_access,
                   scenario_permissions, scenario_payment_methods],
    "demo-grosir": day_one_grosir.SCENARIOS + [scenario_payment_instruction, scenario_permissions, scenario_payment_methods],
    "demo-produksi": day_one_produksi.SCENARIOS + [scenario_permissions, scenario_payment_methods],
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", default="kopi-senja", choices=sorted(SITE_ORGS))
    ap.add_argument("--strict", action="store_true", help="exit 1 on gaps too")
    ap.add_argument("--keep", action="store_true", help="after the scenarios, keep the server and its data running for a demo (Ctrl-C stops it)")
    ap.add_argument("--only", default="", help="run only the scenarios whose function name contains this text (debugging)")
    a = ap.parse_args()
    if a.site not in SCENARIOS:
        sys.exit(f"no scenarios for site {a.site} yet")
    state = tempfile.mkdtemp(prefix="day-one-")
    k = Kernel(a.site, state)
    atexit.register(k.stop)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))   # a stopped --keep demo still removes its database (atexit)
    k.start()
    api = Api(k.base)
    CTX["kernel"] = k
    CTX["site"] = a.site
    if a.site == "demo-fnb":
        setup_demo_fnb(api, k)
    if a.site == "demo-jasa":
        setup_demo_jasa(api, k)
    if a.site == "kopi-senja":
        setup_kopi_senja(api, k)
    if a.site == "demo-ritel":
        setup_demo_ritel(api, k)
    if a.site == "demo-grosir":
        day_one_grosir.setup_demo_grosir(api, k)
    if a.site == "demo-produksi":
        day_one_produksi.setup_demo_produksi(api, k)
    for sc in SCENARIOS[a.site]:
        if a.only and a.only not in sc.__name__:
            continue
        try:
            sc(api)
        except Exception as e:  # a crashed scenario is a failure, not a silent pass
            tb = e.__traceback__
            while tb.tb_next:
                tb = tb.tb_next
            fail(sc.__name__, f"{type(e).__name__}: {e} (line {tb.tb_lineno})")
    if a.site == "demo-ritel" and CTX.get("timing_moka"):
        rows, secs = CTX["timing_moka"]
        print(f"timing: Moka import {rows} rows in {secs}s (upload, preview, commit), submit {CTX.get('timing_moka_submit')}s")
    if a.keep:
        report()
        print(f"\ndemo running: {k.base}/  (dev-open signs you in as the owner)")
        print(f"database: {k.db}\nCtrl-C stops the server and removes the database.")
        try:
            k.proc.wait()
        except KeyboardInterrupt:
            pass
    k.stop()
    _, n_gap, n_fail = report()
    sys.exit(1 if n_fail or (a.strict and n_gap) else 0)


if __name__ == "__main__":
    main()
