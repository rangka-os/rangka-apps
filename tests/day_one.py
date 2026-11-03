#!/usr/bin/env python3
"""Day-one integration test against a live kernel (Python stdlib only).

    python3 tests/day_one.py [--site kopi-senja|demo-jasa|demo-fnb] [--strict]

Starts a throwaway Postgres (rangka-os/kernel/scripts/pgtemp.sh), runs `rangka migrate`, `seed` and
`serve --dev-open` on a free port, waits for /api/v1/system/status, runs the scenarios of the site, and stops everything.
Each check is ok(name), fail(name, detail) or gap(name, owner, reason). A gap is a feature whose kernel stream has not
merged: a hook that returns 501 or is recorded as hook.skipped, or an endpoint that answers 404 not_found.
Exit 1 on any fail, and on any gap with --strict. The last line reads `N ok · M gaps · K failed`.
"""
import argparse
import atexit
import json
import os
import secrets
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


class Kernel:
    """A temp Postgres plus `rangka serve` for one site."""

    def __init__(self, site, state_dir):
        self.site = site
        self.os_dir, self.binary, self.connectors = lib_env()
        self.pgtemp = os.path.join(self.os_dir, "kernel", "scripts", "pgtemp.sh")
        self.env = dict(os.environ, PGTEMP_STATE=os.path.join(state_dir, "pgtemp.state"))
        self.proc = None
        self.log = open(os.path.join(state_dir, "serve.log"), "w+")
        self.logpath = self.log.name
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.db = None

    def args(self):
        site_dir = os.path.join(ROOT, "examples", "sites", self.site)
        return ["--apps", os.path.join(ROOT, "apps"), "--site", site_dir, "--connectors", self.connectors, "--db", self.db]

    def start(self):
        r = subprocess.run(["bash", self.pgtemp, "start"], env=self.env, capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit("pgtemp start failed: " + r.stderr)
        self.db = r.stdout.strip().split("\n")[-1]
        for verb, extra in (("migrate", []), ("seed", ["--fixtures", os.path.join(ROOT, "examples", "fixtures", self.site)])):
            r = subprocess.run([self.binary, verb] + self.args() + extra, capture_output=True, text=True)
            if r.returncode != 0:
                sys.exit(f"rangka {verb} failed:\n{r.stdout}\n{r.stderr}")
        self.proc = subprocess.Popen([self.binary, "serve"] + self.args() + ["--dev-open", "--addr", f"127.0.0.1:{self.port}"],
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
    def login(cls, base, email, password):
        """A client signed in as `email` (POST /auth/login), or None when the login is refused."""
        req = urllib.request.Request(base + "/auth/login", data=json.dumps({"email": email, "password": password}).encode(),
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
    check("fixture invoices valid with new fields", bool(existing) and all(r.get("kind") == "regular" and r.get("ppn_applies") in (True, "true") for r in existing),
          f"first: {existing[0] if existing else None}")

    # PKP: PPN on
    st, inv = api.create("sales_invoice", invoice_body(customer, item))
    check("create invoice (PKP)", st == 201, inv)
    if st != 201:
        return
    eq("PKP subtotal", inv["subtotal"], 10_000_000)
    eq("PKP discount 0", inv["discount_amount"], 0)
    eq("PKP dpp_base", inv["dpp_base"], 10_000_000)
    eq("PKP ppn 1.100.000", inv["ppn"], 1_100_000)
    eq("PKP grand_total 11.100.000", inv["grand_total"], 11_100_000)
    eq("PKP amount_due = grand_total", inv["amount_due"], 11_100_000)
    eq("PKP outstanding", inv["outstanding"], 11_100_000)

    # non-PKP: PPN off
    st, inv = api.create("sales_invoice", invoice_body(customer, item, ppn_applies=False))
    eq("non-PKP ppn 0", inv.get("ppn"), 0)
    eq("non-PKP grand_total 10.000.000", inv.get("grand_total"), 10_000_000)

    # discount
    st, inv = api.create("sales_invoice", invoice_body(customer, item, discount_amount=1_000_000))
    eq("discount 1.000.000 -> dpp_base 9.000.000", inv.get("dpp_base"), 9_000_000)
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
    st, resp = api.create("sales_invoice", invoice_body(customer, item, dp_invoice=dp["name"], dp_deduction=1))
    check("DP not submitted yet -> deduction refused", st == 422, (st, resp))
    st, resp = api.verb("sales_invoice", dp["name"], "submit", {"force": True})
    check("submit DP invoice", st == 200, resp)
    dp_no = resp.get("name", dp["name"])  # submit renames DRAFT-… to the numbered name
    st, reg = api.create("sales_invoice", invoice_body(customer, item, dp_invoice=dp_no, dp_deduction=3_000_000))
    check("create regular invoice with DP deduction", st == 201, reg)
    if st == 201:
        eq("DP deducted: dpp_base 7.000.000", reg["dpp_base"], 7_000_000)
        eq("DP deducted: ppn 770.000", reg["ppn"], 770_000)
    st, resp = api.create("sales_invoice", invoice_body(customer, item, dp_invoice=dp_no, dp_deduction=4_000_000))
    check("DP over-deduction 4.000.000 vs 3.000.000 -> rule error",
          st == 422 and any("Potongan uang muka melebihi" in m for m in messages(resp)), (st, resp))
    st, resp = api.create("sales_invoice", invoice_body(customer, item, dp_deduction=1_000_000))
    check("deduction without a DP invoice -> rule error", st == 422 and any("faktur uang muka" in m.lower() for m in messages(resp)), (st, resp))

    # retention invoice must point at a submitted invoice
    st, resp = api.create("sales_invoice", invoice_body(customer, item, kind="retention"))
    check("retention invoice without retention_of -> rule error", st == 422, (st, resp))
    st, draft = api.create("sales_invoice", invoice_body(customer, item))
    st, resp = api.create("sales_invoice", invoice_body(customer, item, kind="retention", retention_of=draft["name"]))
    check("retention_of a draft -> rule error", st == 422 and any("retensi" in m.lower() for m in messages(resp)), (st, resp))
    st, resp = api.create("sales_invoice", invoice_body(customer, item, kind="retention", retention_of=dp_no))
    check("retention_of a submitted invoice accepted", st == 201, (st, resp))

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
        gap("ledger.post_receivable adds amount_due to customer.outstanding", "kernel (S5 ledger)", "hook.skipped in the audit log")
    else:
        eq("ledger.post_receivable adds 11.100.000 to outstanding", after - before, 11_100_000)
    st, resp = api.verb("sales_invoice", submitted, "cancel")
    check("cancel invoice", st == 200, resp)
    final = api.get("customer", customer)["outstanding"]
    if skipped(api, "invoicing.reverse_receivable", f"sales_invoice/{submitted}"):
        gap("invoicing.reverse_receivable subtracts amount_due on cancel", "kernel (S8 hooks)", "hook.skipped in the audit log")
    else:
        eq("cancel restores customer.outstanding", final, before)

# ---------- scenarios: project services (demo-jasa)

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
    st, resp = api.create("quotation", {"customer": customer, "subject": "x", "discount_amount": 99_999_999_999, "items": [{"item": ndt}]})
    check("discount above subtotal rejected", st == 422 and any("Diskon" in m for m in messages(resp)), (st, resp))
    st, resp = api.create("quotation", {"customer": customer, "subject": "x", "ppn_applies": False, "items": [{"item": ndt, "qty": 2, "rate": 1_000_000}]})
    eq("quotation without PPN: grand_total = dpp_base", (resp.get("ppn"), resp.get("grand_total")), (0, 2_000_000))

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
            gap("quotation.mark_outcome", "kernel (S8c hooks)", "501 hook_not_implemented")
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
        gap("orders.from_quotation creates a draft customer_order", "kernel (S8c hooks; needs mark_outcome first)", "mark_outcome is 501, so no quotation can reach accepted")

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
        gap("quotation.revise creates a draft with revision + 1", "kernel (S8c hooks)", "501 hook_not_implemented")
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
        gap("invoicing.from_order creates a draft sales_invoice", "kernel (S8c hooks)", "501 hook_not_implemented")
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
    gap("certification.expired stays current after save", "kernel (computed-on-read or a scheduled recompute)", "computes run at save; the flag goes stale once expires_on passes")


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
    inv2 = submitted_invoice(3_900_000, ppn_applies=False)
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
        gap("payments.allocate pays both invoices", "kernel (S8c hooks)", "hook.skipped in the audit log")
        gap("payments.unallocate restores on cancel", "kernel (S8c hooks)", "hook.skipped in the audit log")
        gap("payments.allocate refuses the second payment (409 over_allocation)", "kernel (S8c hooks)", "hook.skipped in the audit log")
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
            gap("client-supplied pph23_applies=false on a row is ignored", "kernel (S1, read_only on child-row fields)",
                "kernel stores the client value, so the row rule passes; payments.allocate must refuse with 409 pph23_already_deducted from the stored invoice")
            st2, r2 = api.verb("payment", resp["name"], "submit")
            bypass = r2.get("name", resp["name"]) if st2 == 200 else resp["name"]
            if st2 == 200 and skipped(api, "payments.allocate", f"payment/{bypass}"):
                gap("payments.allocate refuses withheld on a pph23 invoice (409 pph23_already_deducted)", "kernel (S8c hooks)", "hook.skipped in the audit log")
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
        eq("withholding dpp defaults to invoice dpp_base", bp["dpp"], 10_000_000)
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
            gap("cash + withheld clears a non-pph23 invoice", "kernel (S8c hooks)", "hook.skipped in the audit log")

    # USD payment: conversion happens once at the header (amount = round(amount_fx x exchange_rate)); rows are IDR
    inv5 = submitted_invoice(10_000_000)
    st, usd = api.create("payment", {"direction": "receive", "customer": customer, "currency": "USD", "exchange_rate": 16000, "amount_fx": 1000, "amount": 16_000_000})
    if st != 201 or "amount_fx" not in usd:
        gap("USD 1.000 at 16.000 gives payment.amount 16.000.000", "kernel (S8a money)", f"money field amount_fx not served ({st})")
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
    st, resp = api.create("payment", {"direction": "receive", "customer": customer, "currency": "USD", "exchange_rate": 1, "amount_fx": "0.5"})
    check("USD 0.5 at rate 1 -> amount 1 (half away from zero)", st == 201 and resp.get("amount") == 1, (st, resp))
    st, resp = api.create("payment", {"direction": "receive", "customer": customer, "currency": "USD", "exchange_rate": 1, "amount_fx": "0.5", "amount": 1})
    check("USD 0.5 at rate 1 with amount 1 passes the rule", st == 201, (st, resp))

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

    # SOA print on customer
    st, _ = api.call("GET", f"/api/v1/r/customer/{Api.q(customer)}/print?format=statement")
    if st == 404:
        gap("statement print on customer", "kernel (S3 print)", "404: print endpoint or format not served yet")
    else:
        check("statement print answers 200", st == 200, st)


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
        gap("min_rows 1: a purchase request without lines is refused", "kernel (S1)", "201: min_rows is not enforced on create")
    else:
        check("PR without lines -> 422", st == 422, (st, resp))
    st, resp = api.verb("purchase_request", pr["name"], "submit")
    check("submit purchase request", st == 200, resp)
    prn = resp.get("name", pr["name"])

    # make_po: vendor comes from the call body
    st, resp = api.action("purchase_request", prn, "make_po", {})
    if st == 501:
        gap("procurement.make_po creates the draft PO from the request", "kernel (S8c hooks)", "501 hook_not_implemented")
        gap("procurement.make_po without vendor -> 422 vendor_required", "kernel (S8c hooks)", "501 hook_not_implemented")
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
    st, po = api.create("purchase_order", {"vendor": vendor, "purchase_request": prn, "outlet": outlet, "ppn_applies": True,
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
    st, p2 = api.create("purchase_order", {"vendor": vendor, "items": po_lines})
    eq("PO without PPN: ppn 0, grand_total 2.050.000", (p2.get("ppn"), p2.get("grand_total")), (0, 2_050_000))
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
    st, big = api.create("purchase_order", {"vendor": vendor, "items": [{"item": liter_item, "qty": 100, "rate": 85_000}]})
    eq("big PO grand_total 8.500.000", big.get("grand_total"), 8_500_000)
    st, resp = api.verb("purchase_order", big["name"], "submit")
    # contract 0007: the dev-open caller is the owner, so the request is created and self-approved (200); a caller without an
    # approver role would get 202 with the request pending. Either way the policy must have matched and left a request.
    check("submit big purchase order", st in (200, 202), (st, resp))
    bign = resp.get("name", big["name"]) if st == 200 else big["name"]
    reqs = [a for a in approvals_of(api, "purchase_order", bign) if a.get("policy") == "po_approval"]
    check("po_approval: a PO above 5.000.000 asks purchasing_manager at submit (request recorded)",
          len(reqs) == 1 and reqs[0].get("approvers") == ["purchasing_manager"] and reqs[0].get("state") in ("approved", "pending"), reqs)
    check("po_approval: the PO under the limit made no request", not approvals_of(api, "purchase_order", pon), approvals_of(api, "purchase_order", pon))

    # foreign-currency PO: the IDR header comes from the converted subtotal
    st, upo = api.create("purchase_order", {"vendor": vendor, "currency": "USD", "exchange_rate": 16000, "ppn_applies": True,
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
        gap("procurement.receive on a PO without outlet -> 422 outlet_required", "kernel (S8c hooks)", "501 hook_not_implemented")
    else:
        check("receive on a PO without outlet -> 422 outlet_required 'Outlet tujuan wajib diisi di PO sebelum barang diterima'",
              st == 422 and resp.get("error", {}).get("code") == "outlet_required" and "Outlet tujuan wajib diisi" in resp["error"]["message"], (st, resp))

    # receive action: draft goods receipt with the remainder per line
    st, resp = api.action("purchase_order", pon, "receive", {})
    if st == 501:
        gap("procurement.receive creates the draft goods receipt", "kernel (S8c hooks)", "501 hook_not_implemented")
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
        gap("stock.post_moves posts the receipt and updates PO received_qty/received_pct", "stock module (not installed on this site)", "no stock_move/stock_balance models; the hook is a no-op (stock is optional for procurement; demo-fnb covers it)")
        st, resp = api.verb("goods_receipt", grn, "cancel")
        check("cancel goods receipt", st == 200, (st, resp))
        gap("stock.reverse_moves reverses the receipt", "stock module (not installed on this site)", "no stock models; the hook is a no-op (demo-fnb covers it)")
    else:
        pod = api.get("purchase_order", pon)
        eq("PO received_pct 100 after the receipt", pod["received_pct"], 100)
        eq("PO received_qty per line [10, 2]", [i["received_qty"] for i in pod["items"]], [10, 2])
        st, resp = api.action("purchase_order", pon, "receive", {})
        check("receive on a fully received PO is gated: 409", st == 409, (st, resp))

    # vendor bill: amount_due = grand_total - pph23_amount
    bill_body = {"vendor": vendor, "purchase_order": pon, "goods_receipt": grn, "bill_no": "INV-CBU-0001", "bill_date": "2026-11-10",
                 "ppn_applies": True, "pph23_amount": 20_000,
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
        gap("procurement.post_payable adds amount_due to vendor.outstanding", "kernel (S8c hooks)", "hook.skipped in the audit log")
        gap("procurement.reverse_payable subtracts on cancel", "kernel (S8c hooks)", "hook.skipped in the audit log")
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
        gap("payments.allocate pays the vendor bill (direction pay)", "kernel (S8c hooks)", "hook.skipped in the audit log")
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

    # PO print
    st, _ = api.call("GET", f"/api/v1/r/purchase_order/{Api.q(pon)}/print?format=purchase_order")
    if st == 404:
        gap("purchase_order print", "kernel (S3 print)", "404: print endpoint or format not served yet")
    else:
        check("purchase_order print answers 200", st == 200, st)


# ---------- scenarios: multi-branch F&B (demo-fnb): stock and outlet sales

KG, PORSI = "UOM-001", "UOM-006"
HO, DAGO, BRAGA = "OUT-01", "OUT-02", "OUT-03"
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


def submit_doc(api, model, name, label):
    st, resp = api.verb(model, name, "submit")
    check(label, st == 200, (st, resp))
    return resp.get("name", name) if st == 200 else None


def receive(api, outlet, lines, label, vendor="VND-0001"):
    """Create and submit a goods receipt; returns its numbered name."""
    st, gr = api.create("goods_receipt", {"vendor": vendor, "outlet": outlet, "items": lines})
    if not check(f"create {label}", st == 201, gr):
        return None
    return submit_doc(api, "goods_receipt", gr["name"], f"submit {label}")


def scenario_fnb_fixtures(api):
    outlets = api.list("outlet")
    check("fixtures: 3 outlets, one head office", len(outlets) == 3 and [o["name"] for o in outlets if o.get("is_head_office") in (True, "true")] == [HO], outlets)
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

    # transfer 30 kg Dago -> Braga
    st, tr = api.create("stock_transfer", {"from_outlet": DAGO, "to_outlet": BRAGA, "items": [{"item": m, "qty": 30}]})
    check("create transfer 30 kg", st == 201, tr)
    trn = submit_doc(api, "stock_transfer", tr["name"], "submit transfer 30 kg")
    check_balance("transfer 30 kg: from-outlet 120 kg at 13.000", api, DAGO, m, 120, 1_560_000, 13_000)
    check_balance("transfer 30 kg: to-outlet 30 kg at 13.000", api, BRAGA, m, 30, 390_000, 13_000)
    mv = moves_of(api, trn)
    check("transfer makes transfer_out -30 / -390.000 and transfer_in +30 / +390.000",
          sorted((x["reason"], x["qty"], x["value"], x["outlet"]) for x in mv) == [("transfer_in", 30, 390_000, BRAGA), ("transfer_out", -30, -390_000, DAGO)], mv)

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
        gap("stock_negative_alert notifies head_office when a move turns negative", "kernel (record events -> flows)",
            "no notify.in_app audit entry: the kernel does not fire `on: stock_move.created` flows (S8c finding)")
    else:
        ok("stock_negative_alert notified head_office on its own")
    st, resp = api.call("POST", "/api/v1/flows/stock_negative_alert/run", {"event": mv[0]} if mv else {})
    alerts = [e for e in api.audit("notify.in_app") if e.get("target") == "flow/stock_negative_alert"]
    check("stock_negative_alert, run with the move as its event, notifies 'Stok <item> di <outlet> minus (-10)'",
          st == 200 and any(f"Stok {m} di {neg} minus (-10)" in e.get("summary", "") for e in alerts), (st, resp, alerts[-1:]))
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
        gap("outlet_staff row scope check (list own outlet, 403 on another)", "harness", f"could not sign in as a non-owner: {CTX.get('login_error')}")
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
    st, resp = dago.create("outlet_sales", {"outlet": BRAGA, "date": "2026-11-05", "lines": [{"item": "BRG-0007", "qty": 1}]})
    check("outlet_staff (Dago) creates outlet_sales for Braga -> 403", st == 403, (st, resp))


def read_sample(name):
    with open(os.path.join(FIXTURES, name), "rb") as f:
        return f.read()


def preview(cl, imp, filename, content, outlet):
    return cl.upload(f"/api/v1/imports/{imp}", {"outlet": outlet}, filename, content)


def scenario_outlet_sales(api):
    hq, dago = CTX.get("hq") or api, CTX.get("staff_dago")
    st, meta = api.call("GET", "/api/v1/meta")
    imps = (meta or {}).get("imports", {})
    check("meta lists the imports moka_item_details and daily_form on outlet_sales",
          st == 200 and all(imps.get(n, {}).get("model") == "outlet_sales" and imps[n].get("per") == "outlet" for n in ("moka_item_details", "daily_form")), imps)
    if not imps:
        gap("Moka import: upload, preview, commit, duplicates", "kernel (S7 imports)", "no imports in /meta: the imports stream has not merged into this kernel")
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
    check("upload the Moka sample: preview 6 rows, no errors, not a duplicate", st == 200 and pv["rows"] == 6 and pv["errors"] == [] and pv["skipped"] == 0 and pv["duplicate"] is False, (st, pv))
    if st != 200:
        return
    prev = pv["preview"]
    check("preview: one outlet_sales record for 02/11/2026 with 6 lines, source moka, outlet Dago",
          len(prev) == 1 and len(prev[0]["lines"]) == 6 and prev[0]["date"] == "2026-11-02" and prev[0]["source"] == "moka" and prev[0]["outlet"] == DAGO, prev)
    st, c = hq.call("POST", f"/api/v1/imports/moka_item_details/{pv['id']}/commit")
    check("commit creates the draft outlet_sales", st == 200 and len(c.get("records", [])) == 1, (st, c))
    if st != 200:
        return
    sales = api.get("outlet_sales", c["records"][0])
    check("imported record is a draft (docstatus 0, no stock moved yet)", sales["docstatus"] == 0 and not moves_of(api, sales["name"]), sales)
    eq("imported totals: gross 259.000, discount 16.000, refund 15.000", (sales["gross_total"], sales["discount_total"], sales["refund_total"]), (259_000, 16_000, 15_000))
    eq("imported net_total 228.000 (= Moka's Net Sales)", sales["net_total"], 228_000)
    eq("imported qty_total 13", sales["qty_total"], 13)
    eq("imported lines match items by sku", [l["item"] for l in sales["lines"]], ["BRG-0007", "BRG-0008", "BRG-0009", "BRG-0008", "BRG-0007", "BRG-0010"])

    # the same file again
    st, pv2 = preview(hq, "moka_item_details", "moka-sample.csv", moka, DAGO)
    check("the same file again: duplicate true (skipped rows 6)", st == 200 and pv2["duplicate"] is True and pv2["skipped"] == 6, (st, pv2))
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
    # the same Moka file for another outlet is not a duplicate (scope is per outlet)
    st, pvb = preview(hq, "moka_item_details", "moka-sample.csv", moka, BRAGA)
    check("the same file for another outlet is not a duplicate", st == 200 and pvb["duplicate"] is False and pvb["skipped"] == 0 and pvb["errors"] == [], (st, pvb))

    # submit: stock moves through the recipes, cogs, margin
    sn = submit_doc(api, "outlet_sales", sales["name"], "submit the imported sales record")
    s2 = api.get("outlet_sales", sn)
    eq("recipes explode: line cogs [3.396, 2.280, 7.740, 4.560, 1.698, 15.680]", [l["cogs"] for l in s2["lines"]], [3396, 2280, 7740, 4560, 1698, 15680])
    eq("cogs_total 35.354", s2["cogs_total"], 35_354)
    eq("margin = net 228.000 - cogs 35.354 = 192.646", s2["margin"], 192_646)
    mv = moves_of(api, sn)
    check("moves: one per line and material (6 lines -> 22 moves), all reason sale, none negative",
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
    st, df = api.create("outlet_sales", {"outlet": DAGO, "date": "2026-11-04", "lines": [
        {"item": "BRG-0007", "qty": 40, "gross": 600_000}, {"item": "BRG-0008", "qty": 25, "gross": 450_000, "discount": 15_000}]})
    check("daily form created via REST with 2 lines", st == 201, (st, df))
    if st != 201:
        return
    eq("daily form: source form by default, net_total 1.035.000 (600.000 + 450.000 - 15.000)", (df["source"], df["net_total"]), ("form", 1_035_000))
    eq("daily form: net per line [600.000, 435.000]", [l["net"] for l in df["lines"]], [600_000, 435_000])
    dfn = submit_doc(api, "outlet_sales", df["name"], "submit the daily form")
    dd = api.get("outlet_sales", dfn)
    eq("daily form cogs: 40 ORI + 25 PDS through the recipes", dd["cogs_total"], 40 * 1698 + 25 * 2280)
    st, pvf = preview(hq, "daily_form", "daily-form-sample.csv", read_sample("daily-form-sample.csv"), DAGO)
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
    st, again = preview(hq, "daily_form", "daily-form-sample.csv", read_sample("daily-form-sample.csv"), DAGO)
    check("the daily form file again: duplicate true", st == 200 and again["duplicate"] is True, (st, again))
    st, resp = api.create("outlet_sales", {"outlet": DAGO, "date": "2026-11-04", "lines": [{"item": "BRG-0007", "qty": 1, "discount": 99_999_999}]})
    check("discount above gross -> 422 'Diskon tidak boleh melebihi penjualan kotor'", st == 422 and any("Diskon tidak boleh" in x for x in messages(resp)), (st, resp))
    st, resp = api.create("outlet_sales", {"date": "2026-11-04", "lines": [{"item": "BRG-0007", "qty": 1}]})
    check("outlet_sales without an outlet -> 422", st == 422, (st, resp))
    st, resp = api.create("outlet_sales", {"outlet": DAGO, "date": "2026-11-04", "lines": []})
    if st == 201:
        gap("min_rows 1: an outlet_sales without lines is refused", "kernel (S1)", "201: min_rows is not enforced on create")
    else:
        check("outlet_sales without lines -> 422", st == 422, (st, resp))


def setup_demo_fnb(api, k):
    """Members for the access checks: head office, and outlet staff of Dago and Braga (rangka user add, contract 0010 §7)."""
    st, me = api.call("GET", "/auth/me")
    org = ((me or {}).get("org") or {}).get("id") or (me or {}).get("user", {}).get("org") or "kopi-senja"
    CTX.update(kernel=k, org=org)
    pw = secrets.token_urlsafe(18)   # a throwaway password for this run's throwaway database
    people = [("hq", "rina@tahucontoh.test", "head_office", "", "USR-001", "Rina Contoh"),
              ("staff_dago", "yusuf.dago@tahucontoh.test", "outlet_staff", DAGO, "USR-002", "Yusuf Contoh"),
              ("staff_braga", "sinta.braga@tahucontoh.test", "outlet_staff", BRAGA, "USR-003", "Sinta Contoh")]
    for key, email, role, outlets, rec, name in people:
        code, out = k.user_add(email, role, org, name, outlets=outlets, record=rec, password=pw)
        if code != 0:
            CTX["login_error"] = f"rangka user add {email} failed: {out}"
            return
        cl = Api.login(k.base, email, pw)
        if cl is None:
            CTX["login_error"] = f"login as {email} refused"
            return
        CTX[key] = cl


SCENARIOS = {
    "kopi-senja": [scenario_invoicing],
    "demo-jasa": [scenario_quotation, scenario_orders, scenario_workforce, scenario_projects, scenario_payments, scenario_procurement],
    "demo-fnb": [scenario_fnb_fixtures, scenario_stock, scenario_stock_access, scenario_outlet_sales],
    # Task 10 completes demo-jasa and demo-fnb; Task 11 extends both with the day-one flows.
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", default="kopi-senja", choices=["kopi-senja", "demo-jasa", "demo-fnb"])
    ap.add_argument("--strict", action="store_true", help="exit 1 on gaps too")
    a = ap.parse_args()
    if a.site not in SCENARIOS:
        sys.exit(f"no scenarios for site {a.site} yet")
    state = tempfile.mkdtemp(prefix="day-one-")
    k = Kernel(a.site, state)
    atexit.register(k.stop)
    k.start()
    api = Api(k.base)
    if a.site == "demo-fnb":
        setup_demo_fnb(api, k)
    for sc in SCENARIOS[a.site]:
        try:
            sc(api)
        except Exception as e:  # a crashed scenario is a failure, not a silent pass
            tb = e.__traceback__
            while tb.tb_next:
                tb = tb.tb_next
            fail(sc.__name__, f"{type(e).__name__}: {e} (line {tb.tb_lineno})")
    k.stop()
    _, n_gap, n_fail = report()
    sys.exit(1 if n_fail or (a.strict and n_gap) else 0)


if __name__ == "__main__":
    main()
