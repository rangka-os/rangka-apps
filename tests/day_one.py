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
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
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
        return ["--apps", os.path.join(ROOT, "build", "catalogue"), "--site", site_dir, "--connectors", self.connectors, "--db", self.db]

    def start(self):
        subprocess.run([os.path.join(ROOT, "scripts", "catalogue.sh")], check=True, capture_output=True)
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
    def __init__(self, base):
        self.base = base

    def call(self, method, path, body=None):
        data = None if body is None and method == "GET" else json.dumps(body or {}).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={"Content-Type": "application/json"})
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

    def patch(self, model, name, body):
        return self.call("PATCH", f"/api/v1/r/{model}/{name}", body)

    def create(self, model, body):
        return self.call("POST", f"/api/v1/r/{model}", body)

    def get(self, model, name):
        return self.call("GET", f"/api/v1/r/{model}/{name}")[1]

    def verb(self, model, name, verb, body=None):
        return self.call("POST", f"/api/v1/r/{model}/{name}/{verb}", body or {})

    def action(self, model, name, action, body=None):
        return self.call("POST", f"/api/v1/r/{model}/{name}/actions/{action}", body or {})

    def list(self, model, **q):
        qs = "&".join(f"{k}={v}" for k, v in q.items())
        return self.call("GET", f"/api/v1/r/{model}" + ("?" + qs if qs else ""))[1]["data"]

    def audit(self, kind):
        return self.call("GET", f"/api/v1/system/audit?kind={kind}&limit=200")[1]["data"]


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
    items = api.list("item")
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

    # submit (the approval policy is a sidecar until S2: the document submits straight away)
    st, resp = api.verb("quotation", q["name"], "submit")
    check("submit quotation", st == 200, resp)
    name = resp.get("name", q["name"])
    if resp.get("approval"):
        gap("quotation manager_approval", "kernel (S2 approvals)", "submit returned a pending approval")
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
    st, q3 = api.create("quotation", {"customer": customer, "subject": "Untuk amend", "items": [{"item": ndt, "qty": 1, "rate": 1_500_000}]})
    st, resp = api.verb("quotation", q3["name"], "submit")
    n3 = resp.get("name", q3["name"])
    st, resp = api.verb("quotation", n3, "cancel")
    check("cancel quotation before generic amend", st == 200, resp)
    st, am = api.verb("quotation", n3, "amend")
    check("generic amend creates a draft", st in (200, 201), (st, am))
    if st in (200, 201):
        am_name = am.get("name") or am.get("data", {}).get("name")
        st, resp = api.verb("quotation", am_name, "submit")
        check("amended draft (outcome empty) submits", st == 200, (st, resp))
        n4 = resp.get("name", am_name)
        st, resp = api.action("quotation", n4, "new_revision")
        check("empty outcome is open: new_revision passes the gate", st != 409, (st, resp))
        st, resp = api.action("quotation", n4, "mark_outcome", {"outcome": "lost", "lost_reason": "uji"})
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
    st, resp = api.action("quotation", old, "new_revision")
    check("revising the cancelled quotation again is refused (action_not_allowed)", st == 409 and resp["error"]["code"] == "action_not_allowed", (st, resp))


def scenario_orders(api):
    customer, other = api.list("customer")[0]["name"], api.list("customer")[1]["name"]
    ndt = api.list("item")[0]["name"]
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
                                               "items": [{"item": api.list("item")[0]["name"], "qty": 10, "rate": 1_500_000}]})
        check("sales_invoice accepts project and acceptance", st == 201, inv)
    st, resp = api.create("acceptance", {"project": p["name"], "accepted_on": "2026-11-21", "scope": "x"})
    check("acceptance without accepted_by rejected", st == 422, (st, resp))


def scenario_payments(api):
    item = api.list("item")[0]["name"]
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
    if st == 201:
        row = resp["allocations"][0]
        stored = row.get("pph23_applies") in (True, "true")
        if stored:
            ok("client-supplied pph23_applies=false is ignored: row stores true")
        else:
            gap("client-supplied pph23_applies=false on a row is ignored", "kernel (read_only on child-row fields)",
                "kernel stores the client value, so the row rule passes; payments.allocate must refuse with 409 pph23_already_deducted from the stored invoice")
            st2, r2 = api.verb("payment", resp["name"], "submit")
            if skipped(api, "payments.allocate", f"payment/{r2.get('name', resp['name'])}") if st2 == 200 else False:
                gap("payments.allocate refuses withheld on a pph23 invoice (409 pph23_already_deducted)", "kernel (S8c hooks)", "hook.skipped in the audit log")
            else:
                check("hook refuses the bypassed row: 409 pph23_already_deducted", st2 == 409 and r2["error"]["code"] == "pph23_already_deducted", (st2, r2))
                if st2 == 200:
                    api.verb("payment", r2["name"], "cancel")
    else:
        check("client-supplied pph23_applies=false on a row does not bypass the rule -> 422 'PPh 23 sudah dipotong'",
              st == 422 and any("PPh 23 sudah dipotong" in m for m in messages(resp)), (st, resp))
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
    other = next(c["name"] for c in api.list("customer") if c["name"] != customer)
    st, resp = api.create("withholding", {"customer": other, "sales_invoice": inv3, "cert_no": "BP-X", "cert_date": "2026-11-05"})
    check("withholding of another customer's invoice -> 422", st == 422 and any("bukan milik" in m for m in messages(resp)), (st, resp))

    # SOA print on customer
    st, _ = api.call("GET", f"/api/v1/r/customer/{customer}/print?format=statement")
    if st == 404:
        gap("statement print on customer", "kernel (S3 print)", "404: print endpoint or format not served yet")
    else:
        check("statement print answers 200", st == 200, st)


SCENARIOS = {
    "kopi-senja": [scenario_invoicing],
    "demo-jasa": [scenario_quotation, scenario_orders, scenario_workforce, scenario_projects, scenario_payments],
    # Task 10 adds "demo-fnb" here and completes demo-jasa; Task 11 extends both with the day-one flows.
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
