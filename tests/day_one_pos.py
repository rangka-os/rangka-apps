"""Day-one scenarios of demo-kasir (piece kasir + module pos): a shop and a coffee kiosk ringing up a day in Juragan.

Loaded by tests/day_one.py (bind() hands over its helpers and registers the site): `python3 tests/day_one.py --site
demo-kasir`. Fixtures are synthetic (examples/fixtures/demo-kasir). Covered: a shift opened with a float and closed
with the counted drawer (selisih kas journalled), sales in any unit of an item with line and receipt discounts, paid
in cash with change, QRIS or split, receipts numbered per outlet (kernel contract 0009 §2a), goods and recipe
materials out of the outlet's stock (contract 0021 declared moves), a void and a partial return on the same day,
the cashier role limited to its own outlet, two sales at the same moment on one shift, the printed receipt, the
dashboard against the shifts and the journal, and Jalur A next to Jalur B (an outlet takes one sales source). The
books are checked by scenario_accounting after.

Members: Dika (cashier, Toko Dago), Sari (cashier, Kedai Riau), Bayu (cashier, Toko Dago, second register) and Rina
(head_office). The owner (seeded) can do everything.
"""
import math
import threading

H = {}
HQ_OUT, DGO, RIU, CMH = "OUT-01", "OUT-02", "OUT-03", "OUT-04"
AIR, MI, GULA, PULSA = "BRG-0001", "BRG-0002", "BRG-0003", "BRG-0004"
KOPI, SUSU, AREN, GELAS, ROTI = "BRG-0011", "BRG-0012", "BRG-0013", "BRG-0014", "BRG-0015"
KSA, KHT = "BRG-0021", "BRG-0022"
DUS_AIR, DUS_MI, PAK_MI = "KONV-0001", "KONV-0002", "KONV-0003"
COST = {AIR: 2500, MI: 2600, GULA: 15000, KOPI: 120000, SUSU: 20000, AREN: 40000, GELAS: 600, ROTI: 6000}
RECIPE = {KSA: {KOPI: 0.018, SUSU: 0.15, AREN: 0.025, GELAS: 1}, KHT: {KOPI: 0.015, GELAS: 1}}

_R, _RP = {"read"}, {"read", "print"}
_ALL = {"read", "create", "write", "delete", "submit", "cancel", "amend", "export", "print"}
MATRIX = {
    "pos_shift": {"head_office": {"read", "create", "write", "export", "print"}, "cashier": {"read", "create", "write", "print"}},
    "pos_shift_close": {"head_office": _ALL - {"amend"}, "cashier": {"read", "create", "write", "delete", "submit", "print"}},
    "pos_sale": {"head_office": _ALL, "cashier": _ALL - {"export"}, "outlet_staff": _R},
    "pos_guard": {"head_office": {"create"}, "cashier": {"create"}, "outlet_staff": {"create"}},   # never stored (a flow's refusal)
}
# what the cashier reads besides its own models (role scope: its outlets only)
CASHIER_READS = ("item", "item_category", "uom", "uom_conversion", "outlet", "stock_balance", "gl_entry", "period_close", "outlet_sales", "outlet_takings")
MEMBERS = [("dika", "cashier", "dika.dago@contohwarung.test", "Dika Contoh", DGO),
           ("sari", "cashier", "sari.riau@contohwarung.test", "Sari Contoh", RIU),
           ("bayu", "cashier", "bayu.dago@contohwarung.test", "Bayu Contoh", DGO),
           ("hq", "head_office", "rina@contohwarung.test", "Rina Contoh", "")]


def bind(m):
    for k in ("ok", "fail", "check", "eq", "CTX", "Api", "denied", "dashboard", "num", "chart_map", "secrets", "messages",
              "kernel_today", "page_text", "decide", "approvals_of"):
        H[k] = getattr(m, k)
    globals().update(H)
    H["day_one"] = m    # Kernel, Api and SITE_ORGS for the kasir-only site (scenario_pos_kasir_only_site)
    m.SITE_ORGS["demo-kasir"] = [("warung_contoh", "demo-kasir")]
    # the shared matrix check runs for a cashier (its 403s on every other money model); head office is checked here
    m.SITE_MEMBERS["demo-kasir"] = [("dika", "cashier")]
    m.MATRIX.update(MATRIX)
    for model in CASHIER_READS:
        m.MATRIX[model] = dict(m.MATRIX[model], cashier=_R)


def rec(x):
    return (x or {}).get("record", x) or {}


def rules(resp):
    return [e.get("rule") for e in (resp or {}).get("errors", [])] if isinstance(resp, dict) else []


def rnd(x):
    """CEL round(): half away from zero (amounts here are positive)."""
    return int(math.floor(x + 0.5)) if x >= 0 else -int(math.floor(-x + 0.5))


def yymm():
    return kernel_today().strftime("%y%m")


def ppn_of(total):
    return rnd(total * 12 * 11 / (100 * 12 + 12 * 11))


def gl(api, model, name, entry="post"):
    out = {}
    for r in api.find("gl_entry", f"voucher_type == '{model}' && voucher == '{name}' && entry == '{entry}'"):
        out[r["account"]] = out.get(r["account"], 0) + r["amount"]
    return out


def bal(api, outlet, item):
    rows = api.find("stock_balance", f"outlet == '{outlet}' && item == '{item}'")
    return round(rows[0]["qty"], 4) if rows else 0


def setup_demo_kasir(api, k):
    st, me = api.call("GET", "/auth/me")
    org = ((me or {}).get("org") or {}).get("id") or "warung_contoh"
    CTX.update(kernel=k, org=org)
    pw = secrets.token_urlsafe(18)   # throwaway password of a throwaway database
    for key, role, email, name, outlets in MEMBERS:
        code, out = k.user_add(email, role, org, name, outlets=outlets, password=pw)
        cl = Api.login(k.base, email, pw) if code == 0 else None
        if cl is None:
            CTX["login_error"] = f"{email}: " + (out if code else "login refused")
            fail("setup: add and sign in the members", CTX["login_error"])
            return
        CTX[key] = cl


def open_shift(cl, outlet, cash, label):
    st, s = cl.create("pos_shift", {"outlet": outlet, "opening_cash": cash})
    check(f"{label}: shift opened -> 201", st == 201, (st, s))
    return rec(s) if st == 201 else {}


def sale(cl, shift, lines, label, submit=True, **extra):
    """A draft sale (and its payment when submit); returns (status, record or error body)."""
    st, s = cl.create("pos_sale", dict({"shift": shift, "items": lines}, **extra))
    if st != 201:
        return st, s
    s = rec(s)
    if not submit:
        return st, s
    st, r = cl.verb("pos_sale", s["name"], "submit")
    return st, (rec(r) if st == 200 else r)


def void(cl, name, approve=True):
    """A cashier's void waits for head office (approval void, contract 0007): ask, then let Rina decide. Returns the
    (status, body) of the cancel as it finally ran, or of the request when approve is False."""
    st, r = cl.verb("pos_sale", name, "cancel")
    if st != 202 or not approve:
        return st, r
    return H["decide"](CTX["hq"], r["approval"]["id"])


def shift_of(api, name):
    return api.get("pos_shift", name) or {}


KASIR_ONLY_FIXTURES = """# Synthetic: the smallest site of the kasir piece alone, one outlet, one service and one stocked good.
uom:
  - {name: UOM-001, uom_name: Pcs}
outlet:
  - {name: OUT-01, outlet_name: Kantor Contoh, code: HO, city: Bandung, is_head_office: true, active: true}
  - {name: OUT-02, outlet_name: Laundry Contoh, code: LDR, city: Depok, sales_source: kasir, active: true}
item:
  - {name: BRG-0001, item_name: Cuci kiloan, kind: service, stock_uom: UOM-001, rate: 8000}
  - {name: BRG-0002, item_name: Deterjen sachet, kind: good, stock_uom: UOM-001, rate: 3000, standard_cost: 1800}
"""


def scenario_pos_kasir_only_site(api):
    """A site of the kasir piece alone, as Cloud's studio builds one (apps: [kasir], no outlet_sales): a cash receipt
    submits. pos_sale_on_submit read `forms` of a find_all its `if: app_installed('outlet_sales')` had skipped, and every
    receipt failed with 500 "undeclared reference to forms" (host smoke 13 Nov, kartu t_e5c261bb)."""
    import os
    import shutil
    import tempfile
    d1 = H["day_one"]
    state, k = None, None
    try:
        state = tempfile.mkdtemp(prefix="day-one-kasir-only-")
        site_dir = os.path.join(state, "site")
        shutil.copytree(os.path.join(d1.ROOT, "examples", "sites", "demo-kasir"), site_dir)
        for name in ("config.rk", "features.rk"):
            path = os.path.join(site_dir, name)
            text = open(path).read()
            assert "apps: [kasir, penjualan_harian, stok_outlet, dapur, pembukuan]" in text, name
            open(path, "w").write(text.replace("apps: [kasir, penjualan_harian, stok_outlet, dapur, pembukuan]", "apps: [kasir]"))
        fixtures = os.path.join(state, "fixtures")
        os.makedirs(fixtures)
        open(os.path.join(fixtures, "demo.rk"), "w").write(KASIR_ONLY_FIXTURES)
        d1.SITE_ORGS["demo-kasir-only"] = [("warung_contoh", fixtures)]   # an absolute path: os.path.join keeps it
        k = d1.Kernel("demo-kasir-only", state, site_dir=site_dir)
        k.start()
        owner = d1.Api(k.base)
        st, resp = owner.call("GET", "/api/v1/r/outlet_sales")
        check("kasir-only site: outlet_sales is not installed (its records answer 404)", st == 404, (st, resp))
        s = open_shift(owner, "OUT-02", 100_000, "kasir-only site")
        if not s:
            return
        st, r = sale(owner, s["name"], [{"item": "BRG-0001", "qty": 3}], "", payments=[{"method": "cash", "amount": 30_000}])
        check("kasir-only site: a cash receipt submits -> 200 (no 'undeclared reference to forms')", st == 200, (st, r))
        got = shift_of(owner, s["name"])
        eq("kasir-only site: the shift counts the receipt (1 sale, net 24.000, cash 24.000)",
           (got.get("sales_count"), got.get("net_total"), got.get("cash_total")), (1, 24_000, 24_000))
        # a stocked good (audit #74 I1): the opening count, then the sale takes it out of the outlet at standard cost
        st, c = owner.create("stock_count", {"outlet": "OUT-02", "items": [{"item": "BRG-0002", "counted_qty": 10}]})
        if check("kasir-only site: opening count of the good -> 201", st == 201, (st, c)):
            st, r = owner.verb("stock_count", rec(c)["name"], "submit")
            check("kasir-only site: opening count submitted", st == 200, (st, r))
        eq("kasir-only site: the outlet holds 10 sachets", bal(owner, "OUT-02", "BRG-0002"), 10)
        st, r = sale(owner, s["name"], [{"item": "BRG-0002", "qty": 2}], "", payments=[{"method": "cash", "amount": 10_000}])
        check("kasir-only site: a receipt of a stocked good submits -> 200", st == 200, (st, r))
        eq("kasir-only site: the sale takes 2 out of the outlet's stock", bal(owner, "OUT-02", "BRG-0002"), 8)
        eq("kasir-only site: the head office's stock of the good is untouched", bal(owner, "OUT-01", "BRG-0002"), 0)
        got = shift_of(owner, s["name"])
        eq("kasir-only site: the shift counts both receipts and the good's cost (2 sales, net 30.000, cogs 3.600)",
           (got.get("sales_count"), got.get("net_total"), got.get("cogs_total")), (2, 30_000, 3_600))
    finally:
        if k is not None:
            k.stop()
        if state is not None:
            shutil.rmtree(state, ignore_errors=True)


def scenario_pos_day_source_kept_after_removal(api):
    """Audit rangka-apps#74 L1: an outlet's daily form submitted today, the outlet moved to the cashier, then the daily
    sales app removed from the site: the cashier's receipt for that day is still refused (outlets/outlet_day keeps
    the day's source; the old guard could not see a daily form of an app no longer installed)."""
    import os
    import shutil
    import subprocess
    import tempfile
    d1 = H["day_one"]
    state, k = None, None
    try:
        state = tempfile.mkdtemp(prefix="day-one-day-source-")
        site_dir = os.path.join(state, "site")
        shutil.copytree(os.path.join(d1.ROOT, "examples", "sites", "demo-kasir"), site_dir)
        k = d1.Kernel("demo-kasir", state, site_dir=site_dir)
        k.start()
        owner = d1.Api(k.base)
        today = kernel_today().isoformat()
        st, f = owner.create("outlet_sales", {"outlet": CMH, "date": today, "lines": [{"item": GULA, "qty": 2, "gross": 36_000}]})
        if not check("day source: Cimahi's daily form -> 201", st == 201, (st, f)):
            return
        st, r = owner.verb("outlet_sales", rec(f)["name"], "submit")
        check("day source: Cimahi's daily form submitted", st == 200, (st, r))
        day = owner.find("outlet_day", f"outlet == '{CMH}' && date == '{today}'")
        eq("day source: the day is marked 'harian' with the form", [(x.get("source"), x.get("document")) for x in day], [("harian", rec(r).get("name") or rec(f)["name"])])
        # nobody writes the marker by hand (audit #77 F1/F2): a new one is refused, a change is dropped
        st, resp = owner.create("outlet_day", {"outlet": CMH, "date": today, "source": "", "day_key": "x"})
        check("day source: a marker cannot be made through the API (read_only fields)", st in (400, 403, 422), (st, resp))
        if day:
            owner.patch("outlet_day", day[0]["name"], {"source": "", "day_key": "y"})
            again = owner.find("outlet_day", f"outlet == '{CMH}' && date == '{today}'")
            eq("day source: an edit through the API leaves the marker as it was", [(x.get("source"), x.get("day_key")) for x in again],
               [("harian", day[0].get("day_key"))])
        owner.patch("outlet", CMH, {"sales_source": "kasir"})
        # the owner removes the daily sales app (penjualan_harian) the same day
        k.stop_serve()
        for name in ("config.rk", "features.rk"):
            path = os.path.join(site_dir, name)
            text = open(path).read()
            assert "apps: [kasir, penjualan_harian, stok_outlet, dapur, pembukuan]" in text, name
            open(path, "w").write(text.replace("apps: [kasir, penjualan_harian, stok_outlet, dapur, pembukuan]", "apps: [kasir, stok_outlet, dapur, pembukuan]"))
        r = subprocess.run([k.binary, "migrate"] + k.args(), capture_output=True, text=True)
        check("day source: migrate without penjualan_harian", r.returncode == 0, r.stderr[-400:])
        k.serve()
        st, resp = owner.call("GET", "/api/v1/r/outlet_sales")
        check("day source: outlet_sales is gone from the site (404)", st == 404, st)
        s = open_shift(owner, CMH, 0, "day source: Cimahi")
        if not s:
            return
        st, resp = sale(owner, s["name"], [{"item": PULSA, "qty": 1}], "", payments=[{"method": "cash", "amount": 5_000}])
        check("day source: a receipt for that day is still refused -> 422 daily_sales_same_day", st == 422 and "daily_sales_same_day" in rules(resp), (st, resp))
    finally:
        if k is not None:
            k.stop()
        if state is not None:
            shutil.rmtree(state, ignore_errors=True)


def scenario_pos_day_source_backfill(api):
    """Audit rangka-apps#77 F3: days submitted before outlets/outlet_day existed get their marker from the backfill
    flows (outlet_day_backfill_kasir in pos, outlet_day_backfill_harian in outlet_sales), with the right source, and a
    second run adds nothing."""
    import os
    import shutil
    import subprocess
    import tempfile
    d1 = H["day_one"]
    state, k = None, None
    try:
        state = tempfile.mkdtemp(prefix="day-one-day-backfill-")
        site_dir = os.path.join(state, "site")
        shutil.copytree(os.path.join(d1.ROOT, "examples", "sites", "demo-kasir"), site_dir)
        k = d1.Kernel("demo-kasir", state, site_dir=site_dir)
        k.start()
        owner = d1.Api(k.base)
        today = kernel_today().isoformat()
        s = open_shift(owner, DGO, 0, "backfill: Dago")
        st, r = sale(owner, s.get("name"), [{"item": PULSA, "qty": 1}], "", payments=[{"method": "cash", "amount": 5_000}]) if s else (0, None)
        check("backfill: a receipt at Toko Dago -> 200", st == 200, (st, r))
        st, r = sale(owner, s.get("name"), [{"item": PULSA, "qty": 1}], "", payments=[{"method": "cash", "amount": 5_000}]) if s else (0, None)
        check("backfill: a second receipt at Toko Dago the same day -> 200 (one batch, one marker)", st == 200, (st, r))
        st, f = owner.create("outlet_sales", {"outlet": CMH, "date": today, "lines": [{"item": GULA, "qty": 1, "gross": 18_000}]})
        if check("backfill: Cimahi's daily form -> 201", st == 201, (st, f)):
            st, sub = owner.verb("outlet_sales", rec(f)["name"], "submit")
            check("backfill: Cimahi's daily form submitted", st == 200, (st, sub))
        # as before outlet_day existed: no marker at all
        psql = os.path.join(os.environ.get("PGBIN", ""), "psql")
        if not os.access(psql, os.X_OK):
            psql = shutil.which("psql") or "psql"
        out = subprocess.run([psql, k.db, "-X", "-v", "ON_ERROR_STOP=1", "-c", "delete from r_outlet_day; delete from r_outlet_day_backfill"], capture_output=True, text=True)
        check("backfill: markers removed (the state before outlet_day)", out.returncode == 0, out.stderr[-300:])
        eq("backfill: no marker left", len(owner.find("outlet_day", "true")), 0)
        for flow in ("outlet_day_backfill_kasir", "outlet_day_backfill_harian"):
            st, res = owner.call("POST", f"/api/v1/flows/{flow}/run", {})
            check(f"backfill: {flow} runs -> 200", st == 200, (st, res))
        got = sorted((x["outlet"], x["date"], x["source"]) for x in owner.find("outlet_day", "true"))
        eq("backfill: Dago marked kasir, Cimahi marked harian", got, sorted([(DGO, today, "kasir"), (CMH, today, "harian")]))
        for flow in ("outlet_day_backfill_kasir", "outlet_day_backfill_harian"):
            owner.call("POST", f"/api/v1/flows/{flow}/run", {})
        eq("backfill: a second run adds nothing", len(owner.find("outlet_day", "true")), 2)
        eq("backfill: each source is recorded done", sorted((x["key"], x.get("done")) for x in owner.find("outlet_day_backfill", "true")),
           [("harian", True), ("kasir", True)])
        # audit rangka-apps#79 F1: once done, a run reads only its done row, not the receipts or daily records
        for flow, models in (("outlet_day_backfill_kasir", ("pos_sale",)), ("outlet_day_backfill_harian", ("outlet_sales", "outlet_takings"))):
            st, res = owner.call("POST", f"/api/v1/flows/{flow}/run", {})
            steps = (res or {}).get("steps") or []
            read = [x for x in steps if any(f"find_all {m} " in x for m in models)]
            check(f"backfill: {flow} after done reads no document -> 200, skipped", st == 200 and not read and any("skipped" in x for x in steps), (st, steps))
    finally:
        if k is not None:
            k.stop()
        if state is not None:
            shutil.rmtree(state, ignore_errors=True)


def scenario_pos_opening_stock(api):
    """Head office counts the opening stock of both outlets (stock_count: in at the items' standard cost)."""
    hq = CTX.get("hq")
    if not hq:
        fail("demo-kasir members signed in", CTX.get("login_error"))
        return
    for outlet, items in ((DGO, {AIR: 200, MI: 200, GULA: 20}), (RIU, {KOPI: 2, SUSU: 10, AREN: 2, GELAS: 200, ROTI: 20})):
        st, c = hq.create("stock_count", {"outlet": outlet, "items": [{"item": i, "counted_qty": q} for i, q in items.items()]})
        if check(f"opening count {outlet}: draft -> 201", st == 201, (st, c)):
            st, r = hq.verb("stock_count", rec(c)["name"], "submit")
            check(f"opening count {outlet}: submitted", st == 200, (st, r))
    eq("Toko Dago holds 200 bottles of water", bal(api, DGO, AIR), 200)
    eq("Kedai Riau holds 2 kg of coffee", bal(api, RIU, KOPI), 2)


def scenario_pos_shift(api):
    """A cashier opens one shift per outlet with a float; a second one, another outlet's or a changed float is refused."""
    dika, sari = CTX.get("dika"), CTX.get("sari")
    if not (dika and sari):
        return
    s = open_shift(dika, DGO, 200_000, "Dika at Toko Dago")
    if not s:
        return
    CTX["shift_dgo"] = s["name"]
    eq("the shift is numbered per outlet when it opens; it carries the cashier, today and state open", (s["name"], s.get("cashier"), s.get("opening_date"), s.get("state")),
       (f"SESI-DGO-{yymm()}-0001", "dika.dago@contohwarung.test", kernel_today().isoformat(), "open"))
    st, resp = dika.create("pos_shift", {"outlet": DGO, "opening_cash": 50_000})
    check("a second open shift of the same cashier at the same outlet -> 422 shift_already_open", st == 422 and "shift_already_open" in rules(resp), (st, resp))
    st, resp = dika.create("pos_shift", {"outlet": RIU, "opening_cash": 50_000})
    check("Dika cannot open a shift at Kedai Riau (not their outlet) -> 403", denied(st, resp), (st, resp))
    st, resp = dika.patch("pos_shift", s["name"], {"opening_cash": 900_000})
    check("the float cannot be raised once the shift is open -> 422 shift_changed", st == 422 and "shift_changed" in rules(resp), (st, resp))
    st, resp = dika.patch("pos_shift", s["name"], {"sales_count": 99, "net_total": 1, "state": "closed"})
    eq("the totals and the state of a shift are the flows' to write (read_only)",
       tuple(shift_of(api, s["name"]).get(f) for f in ("sales_count", "net_total", "state")), (0, 0, "open"))
    r = open_shift(sari, RIU, 150_000, "Sari at Kedai Riau")
    CTX["shift_riu"] = r.get("name")
    st, resp = api.create("outlet", {"outlet_name": "Pop-up Salah", "code": "Pop up"})
    check("an outlet code takes capitals, digits and '-' only -> 422 code_shape", st == 422 and "code_shape" in rules(resp), (st, resp))
    # an outlet without a code cannot number receipts
    st, o = api.create("outlet", {"outlet_name": "Pop-up Contoh", "sales_source": "kasir"})
    if check("owner adds an outlet without a code", st == 201, (st, o)):
        st, resp = api.create("pos_shift", {"outlet": rec(o)["name"], "opening_cash": 0})
        check("a shift at an outlet without a code -> 409 numbering (its number carries the code)", st == 409 and (resp or {}).get("error", {}).get("code") == "numbering", (st, resp))
        api.patch("outlet", rec(o)["name"], {"active": False})


def scenario_pos_retail_sale(api):
    """Toko Dago: bottles, a dus (its own price), a pak of noodles, a line discount and a receipt discount, cash with
    change. The receipt is numbered per outlet, stock leaves in the stock unit, the journal splits PPN out."""
    dika, shift = CTX.get("dika"), CTX.get("shift_dgo")
    if not (dika and shift):
        return
    lines = [{"item": AIR, "qty": 2}, {"item": AIR, "qty": 1, "packing": DUS_AIR}, {"item": MI, "qty": 1, "packing": PAK_MI},
             {"item": MI, "qty": 1, "packing": DUS_MI}, {"item": GULA, "qty": 1, "discount_pct": 10}]
    st, draft = sale(dika, shift, lines, "", submit=False, discount_amount=1_000, payments=[{"method": "cash", "amount": 500_000}])
    if not check("Dika drafts a sale -> 201", st == 201, (st, draft)):
        return
    gross = 8_000 + 84_000 + 16_500 + 140_000 + 18_000    # dus of noodles: no own price, 40 x 3.500
    sub = gross - 1_800
    eq("prices per unit: item, the dus's own price, the pak's own price, item x factor; line discount 10%",
       [(l["rate"], l["stock_qty"], l["amount"]) for l in draft["items"]],
       [(4000, 2, 8000), (84000, 24, 84000), (16500, 5, 16500), (140000, 40, 140000), (18000, 1, 16200)])
    total = sub - 1_000
    eq("totals: subtotal, discounts, total, cash, change", (draft["gross_total"], draft["subtotal"], draft["discount_total"], draft["grand_total"],
                                                           draft["cash_amount"], draft["qris_amount"], draft["change_amount"]),
       (gross, sub, 2_800, total, total, 0, 500_000 - total))
    # a cashier sees the receipt's prices, never its cost or margin (card t_09a89bcd; pos_shift already hides its COGS)
    cost = sorted(k for k in ("cogs_total", "margin") if k in draft) + sorted({"items.cogs" for l in draft["items"] if "cogs" in l})
    check("a cashier's receipt carries no cost or margin", not cost, cost)
    # a cashier gives discounts, never a price
    st, resp = dika.patch("pos_sale", draft["name"], {"items": [dict(l, rate=1) for l in lines]})
    eq("a typed price is ignored (rate is computed from the item)", [l["rate"] for l in rec(api.get("pos_sale", draft["name"]))["items"]][:1], [4000])
    st, r = dika.verb("pos_sale", draft["name"], "submit")
    r = rec(r)
    if not check("Dika takes the payment (submit) -> 200", st == 200 and r.get("docstatus") == 1, (st, r)):
        return
    CTX["sale1"] = r
    eq("the receipt is numbered per outlet: STRK-DGO-YYMM-00001", r["name"], f"STRK-DGO-{yymm()}-00001")
    eq("stock left Toko Dago in the stock unit (2 + 24 bottles, 5 + 40 packs, 1 sugar)",
       (bal(api, DGO, AIR), bal(api, DGO, MI), bal(api, DGO, GULA)), (174, 155, 19))
    cogs = 26 * 2500 + 45 * 2600 + 15000
    own = rec(api.get("pos_sale", r["name"]))   # the owner's view: the cashier does not see cost or margin
    eq("cogs: goods out at their average cost", (own["cogs_total"], own["margin"]), (cogs, total - cogs))
    ppn = ppn_of(total)
    pph = rnd((total - ppn) * 5 / 1000)
    eq("PPN inside the PKP price and the final PPh", (r["ppn_in_sales"], r["pph_final"]), (ppn, pph))
    eq("journal: takings held, revenue net of PPN, PPN, final PPh, HPP out of stock", gl(api, "pos_sale", r["name"]),
       {"11150": total, "41100": -(total - ppn), "21200": -ppn, "69100": pph, "21310": -pph, "51100": cogs, "11400": -cogs})
    s = shift_of(api, shift)
    eq("the shift counts the receipt (count, net, cash)", (s["sales_count"], s["net_total"], s["cash_total"], s["qris_total"], s["expected_cash"]),
       (1, total, total, 0, 200_000 + total))
    # QRIS, then split
    st, q = sale(dika, shift, [{"item": AIR, "qty": 3}], "", payments=[{"method": "qris", "amount": 12_000, "ref": "QR-0001"}])
    if check("QRIS sale -> 200, numbered 00002", st == 200 and q.get("name") == f"STRK-DGO-{yymm()}-00002", (st, q)):
        CTX["sale_qris"] = q
        eq("QRIS: no cash, no change", (q["method"], q["cash_amount"], q["qris_amount"], q["change_amount"]), ("qris", 0, 12_000, 0))
    st, sp = sale(dika, shift, [{"item": GULA, "qty": 2}], "", payments=[{"method": "qris", "amount": 16_000, "ref": "QR-0002"}, {"method": "cash", "amount": 50_000}])
    if check("split sale -> 200, numbered 00003", st == 200 and sp.get("name") == f"STRK-DGO-{yymm()}-00003", (st, sp)):
        CTX["sale_split"] = sp
        eq("split: 16.000 QRIS, 50.000 cash handed over, 20.000 kept, change 30.000",
           (sp["method"], sp["qris_amount"], sp["cash_received"], sp["cash_amount"], sp["change_amount"]), ("split", 16_000, 50_000, 20_000, 30_000))
    st, big = dika.create("pos_sale", {"shift": shift, "items": [{"item": GULA, "qty": 1, "discount_pct": 50}]})
    st, resp = dika.verb("pos_sale", rec(big)["name"], "submit") if st == 201 else (st, big)
    if check("a 50% discount waits for head office -> 202", st == 202 and resp.get("approval", {}).get("approvers") == ["head_office"], (st, resp)):
        st, resp = decide(CTX["hq"], resp["approval"]["id"])
        check("Rina approves the discount: the receipt is paid", st == 200, (st, resp))
        CTX["sale_big"] = rec(resp).get("record", rec(resp))
    st, sv = sale(dika, shift, [{"item": PULSA, "qty": 1}], "")
    if check("a service sale (no stock) -> 200", st == 200, (st, sv)):
        eq("a service moves no stock", api.find("stock_move", f"voucher == '{sv['name']}'"), [])
    s = shift_of(api, shift)
    eq("shift after five receipts", (s["sales_count"], s["cash_total"], s["qris_total"]), (5, total + 20_000 + 9_000 + 3_000, 12_000 + 16_000))


def scenario_pos_fnb_sale(api):
    """Kedai Riau: menu items take their recipe's materials out; a good without a recipe goes out as itself. The
    receipts of this outlet count from 00001 on their own."""
    sari, shift = CTX.get("sari"), CTX.get("shift_riu")
    if not (sari and shift):
        return
    st, r = sale(sari, shift, [{"item": KSA, "qty": 2, "note": "kurang manis"}, {"item": KHT, "qty": 1}, {"item": ROTI, "qty": 1}], "", buyer="Meja 4",
                 payments=[{"method": "cash", "amount": 100_000}])
    if not check("Sari sells two kopi susu aren, a kopi hitam and a roti -> 200", st == 200, (st, r)):
        return
    CTX["sale_riu"] = r
    eq("Kedai Riau numbers its own receipts: STRK-RIU-YYMM-00001", r["name"], f"STRK-RIU-{yymm()}-00001")
    used = {KOPI: 2 * 0.018 + 0.015, SUSU: 0.3, AREN: 0.05, GELAS: 3}
    eq("materials out through the recipes, roti as itself",
       {i: bal(api, RIU, i) for i in (KOPI, SUSU, AREN, GELAS, ROTI)},
       {KOPI: round(2 - used[KOPI], 4), SUSU: 9.7, AREN: 1.95, GELAS: 197, ROTI: 19})
    # per line, per material: round(qty x average)
    cogs = (rnd(0.036 * 120000) + rnd(0.3 * 20000) + rnd(0.05 * 40000) + rnd(2 * 600)) + (rnd(0.015 * 120000) + rnd(600)) + 6000
    eq("cogs of the receipt = the recipe materials and the roti at their average", rec(api.get("pos_sale", r["name"]))["cogs_total"], cogs)
    eq("change", r["change_amount"], 100_000 - (2 * 22_000 + 15_000 + 15_000))


def scenario_pos_guards(api):
    """Rules of a sale: one shift per cashier, the outlet's own, no negative or oversized discounts, enough cash; the
    cashier role sees its own outlet only."""
    dika, sari, bayu, hq = CTX.get("dika"), CTX.get("sari"), CTX.get("bayu"), CTX.get("hq")
    shift = CTX.get("shift_dgo")
    if not (dika and sari and bayu and hq and shift):
        return
    st, resp = sale(sari, shift, [{"item": AIR, "qty": 1}], "", submit=False)
    check("Sari cannot sell on a Toko Dago shift (outlet out of scope, or not her shift) -> 403/422", denied(st, resp) or (st == 422 and "own_shift" in rules(resp)), (st, resp))
    st, resp = sale(bayu, shift, [{"item": AIR, "qty": 1}], "", submit=False)
    check("Bayu (same outlet) cannot ring up on Dika's shift -> 422 own_shift", st == 422 and "own_shift" in rules(resp), (st, resp))
    for label, body, rule in (
            ("qty 0", {"items": [{"item": AIR, "qty": 0}]}, "qty_positive"),
            ("line discount 120%", {"items": [{"item": AIR, "qty": 1, "discount_pct": 120}]}, "line_discount_range"),
            ("receipt discount above the subtotal", {"items": [{"item": AIR, "qty": 1}], "discount_amount": 5_000}, "receipt_discount_range"),
            ("a unit of another item", {"items": [{"item": GULA, "qty": 1, "packing": DUS_AIR}]}, "unit_of_item"),
            ("QRIS above the total (change only from cash)", {"items": [{"item": AIR, "qty": 1}], "payments": [{"method": "qris", "amount": 5_000}]}, "qris_not_over"),
            ("cash handed over below the total", {"items": [{"item": GULA, "qty": 1}], "payments": [{"method": "cash", "amount": 10_000}]}, "paid_enough"),
            ("a payment of 0", {"items": [{"item": AIR, "qty": 1}], "payments": [{"method": "cash", "amount": 0}, {"method": "cash", "amount": 4_000}]}, "payment_positive"),
            ("an item without a price", {"items": [{"item": KOPI, "qty": 1}]}, "priced")):
        st, resp = dika.create("pos_sale", dict({"shift": shift}, **body))
        check(f"refused: {label} -> 422 {rule}", st == 422 and rule in rules(resp), (st, resp))
    # cashier screens: own outlet's records only, no other money documents, no journal of other documents
    st, resp = dika.call("GET", "/api/v1/r/pos_sale?limit=200")
    outlets = {r["outlet"] for r in (resp or {}).get("data", [])}
    check("Dika lists only Toko Dago receipts", st == 200 and outlets == {DGO}, (st, outlets, (resp or {}).get("data", [])[:2], resp if st != 200 else ""))
    st, resp = sari.call("GET", "/api/v1/r/stock_balance?limit=200")
    check("Sari reads the stock of Kedai Riau only", st == 200 and {r["outlet"] for r in resp.get("data", [])} == {RIU}, (st, resp))
    for model in ("stock_move", "financial_report", "stock_count"):
        st, resp = dika.call("GET", f"/api/v1/r/{model}?limit=1")
        check(f"a cashier cannot list {model} -> 403", denied(st, resp), (st, resp))
    st, resp = dika.call("GET", "/api/v1/dashboards/kasir_ringkas")
    check("a cashier does not open the owner's dashboard", st in (403, 404), (st, resp))
    st, resp = dika.call("GET", "/api/v1/r/gl_entry?limit=200")
    check("a cashier reads only the journal lines of its own outlet's receipts (not a close's cash difference)",
          st == 200 and {r["voucher_type"] for r in resp.get("data", [])} == {"pos_sale"} and {r.get("outlet") for r in resp.get("data", [])} <= {DGO}, (st, resp))
    # the amounts of those lines (the receipt's COGS and revenue split) are not the cashier's to see (card t_8074a56a)
    leaked = sorted({k for r in resp.get("data", []) for k in ("amount", "debit", "credit", "bill_left") if k in r}) if st == 200 else []
    check("a cashier's journal lines carry no amounts", st == 200 and resp.get("data") and not leaked, leaked)
    st, resp = dika.call("GET", "/api/v1/aggregate/gl_entry?fn=sum:amount")
    check("a cashier cannot sum journal amounts -> 403", st == 403, (st, resp))
    st, resp = dika.patch("item", AIR, {"rate": 1})
    check("a cashier cannot change a price on the item -> 403", denied(st, resp), (st, resp))
    # Jalur A and Jalur B: one sales source per outlet, and never both on one outlet and day
    today = kernel_today().isoformat()
    st, resp = hq.create("outlet_sales", {"outlet": DGO, "date": today, "lines": [{"item": AIR, "qty": 1}]})
    check("a daily form for a cashier outlet -> 422 source_matches_outlet", st == 422 and "source_matches_outlet" in rules(resp), (st, resp))
    hq.patch("outlet", DGO, {"sales_source": "form"})
    st, tk = hq.create("outlet_takings", {"outlet": DGO, "date": today, "lines": [{"method": "cash", "expected": 1_000, "received": 1_000}]})
    st, resp = hq.verb("outlet_takings", rec(tk)["name"], "submit") if st == 201 else (st, tk)
    check("a setoran for a day the outlet rang up in the cashier (even after switching its source) -> 422 cashier_takings",
          st == 422 and "cashier_takings" in rules(resp), (st, resp))
    st, f = hq.create("outlet_sales", {"outlet": DGO, "date": today, "lines": [{"item": AIR, "qty": 1, "gross": 4_000}]})
    if check("head office switches Toko Dago to the daily form and drafts one for today", st == 201, (st, f)):
        st, resp = hq.verb("outlet_sales", rec(f)["name"], "submit")
        check("a daily form for a day the outlet already rang up in the cashier -> 422 cashier_same_day", st == 422 and "cashier_same_day" in rules(resp), (st, resp))
        hq.call("DELETE", f"/api/v1/r/outlet_sales/{Api.q(rec(f)['name'])}")
    hq.patch("outlet", DGO, {"sales_source": "kasir"})
    # module pos installed: a new outlet sells through the cashier unless told otherwise (a build that adds kasir next
    # to an outlet_sales app gets outlets the cashier can ring up in), an outlet on Moka keeps no source
    st, o = hq.create("outlet", {"outlet_name": "Kios Contoh Sore", "code": "SRE"})
    if check("a new outlet without a sales source (Kios Sore)", st == 201, (st, o)):
        eq("its sales source defaults to the cashier", rec(o).get("sales_source"), "kasir")
        hq.patch("outlet", rec(o)["name"], {"active": False})
    st, o = hq.create("outlet", {"outlet_name": "Kios Contoh Moka", "code": "MKA", "moka_name": "Kios Contoh Moka"})
    if check("a new outlet on Moka (Kios Moka)", st == 201, (st, o)):
        eq("an outlet on Moka gets no default sales source", rec(o).get("sales_source") or "", "")
        hq.patch("outlet", rec(o)["name"], {"active": False})
    # a cashier outlet that has not rung up yet today: a setoran first, then the receipts, would book the cash twice
    st, o = hq.create("outlet", {"outlet_name": "Kios Contoh Pagi", "code": "PGI", "sales_source": "kasir"})
    if check("a new cashier outlet (Kios Pagi)", st == 201, (st, o)):
        st, tk = hq.create("outlet_takings", {"outlet": rec(o)["name"], "date": today, "lines": [{"method": "cash", "expected": 5_000, "received": 5_000}]})
        st, resp = hq.verb("outlet_takings", rec(tk)["name"], "submit") if st == 201 else (st, tk)
        check("a setoran for a cashier outlet with no daily sales that day -> 422 cashier_takings (even before its first receipt)",
              st == 422 and "cashier_takings" in rules(resp), (st, resp))
        # the other order: a setoran booked while the outlet was on the daily form, then the cashier the same day
        pgi = rec(o)["name"]
        hq.patch("outlet", pgi, {"sales_source": "form"})
        st, tk = hq.create("outlet_takings", {"outlet": pgi, "date": today, "lines": [{"method": "cash", "expected": 5_000, "received": 5_000}]})
        st, resp = hq.verb("outlet_takings", rec(tk)["name"], "submit") if st == 201 else (st, tk)
        check("Kios Pagi on the daily form takes a setoran", st == 200, (st, resp))
        hq.patch("outlet", pgi, {"sales_source": "kasir"})
        st, sh = hq.create("pos_shift", {"outlet": pgi, "opening_cash": 0})
        if check("head office opens a shift at Kios Pagi", st == 201, (st, sh)):
            st, resp = sale(hq, rec(sh)["name"], [{"item": AIR, "qty": 1}], "")
            check("a receipt on a day that already has a setoran -> 422 daily_sales_same_day", st == 422 and "daily_sales_same_day" in rules(resp), (st, resp))
            CTX["shift_pgi"] = rec(sh)["name"]
    st, sh = hq.create("pos_shift", {"outlet": CMH, "opening_cash": 0})
    if check("head office opens a shift at Cimahi (still on the daily form)", st == 201, (st, sh)):
        st, resp = sale(hq, rec(sh)["name"], [{"item": AIR, "qty": 1}], "", submit=False)
        check("a sale at an outlet on the daily form -> 422 outlet_uses_cashier", st == 422 and "outlet_uses_cashier" in rules(resp), (st, resp))
        CTX["shift_cmh"] = rec(sh)["name"]


def scenario_pos_daily_form_next_door(api):
    """Cimahi stays on Jalur A: its daily form is outlet_sales, untouched by the cashier."""
    hq = CTX.get("hq")
    if not hq:
        return
    st, f = hq.create("outlet_sales", {"outlet": CMH, "date": kernel_today().isoformat(), "lines": [{"item": GULA, "qty": 2, "gross": 36_000}]})
    if check("Cimahi's daily form -> 201", st == 201, (st, f)):
        st, r = hq.verb("outlet_sales", rec(f)["name"], "submit")
        check("Cimahi's daily form submitted", st == 200, (st, r))
        CTX["form_cmh"] = rec(r)
    # Cimahi switched to the cashier the same day: its receipts for today are refused (the form already counted it)
    sh = CTX.get("shift_cmh")
    if sh and CTX.get("form_cmh"):
        hq.patch("outlet", CMH, {"sales_source": "kasir"})
        st, resp = sale(hq, sh, [{"item": GULA, "qty": 1}], "")
        check("a receipt on a day the outlet already has a daily form -> 422 daily_sales_same_day", st == 422 and "daily_sales_same_day" in rules(resp), (st, resp))
        if isinstance(resp, dict) and resp.get("errors") is None and st != 200:
            pass
        hq.patch("outlet", CMH, {"sales_source": "form"})


def scenario_pos_void_and_return(api):
    """Same day, shift open: a QRIS receipt voided brings its goods back and leaves the shift's QRIS; a partial return
    is a void plus an amended receipt without the returned item."""
    dika, shift = CTX.get("dika"), CTX.get("shift_dgo")
    q, s1 = CTX.get("sale_qris"), CTX.get("sale1")
    if not (dika and shift and q and s1):
        return
    bayu = CTX.get("bayu")
    if bayu:
        st, resp = void(bayu, q["name"])
        check("Bayu (same outlet, other drawer) cannot void Dika's receipt -> 422 void_not_own", st == 422 and "void_not_own" in rules(resp), (st, resp))
    before = shift_of(api, shift)
    air_before = bal(api, DGO, AIR)
    st, resp = void(dika, q["name"])
    check("Dika voids the QRIS receipt -> 200", st == 200, (st, resp))
    eq("its three bottles are back", bal(api, DGO, AIR), air_before + 3)
    after = shift_of(api, shift)
    eq("the shift: one receipt less, QRIS down, one void counted",
       (after["sales_count"], after["qris_total"], after["void_count"], after["void_total"]),
       (before["sales_count"] - 1, before["qris_total"] - 12_000, 1, 12_000))
    eq("the void's journal is reversed line by line", gl(api, "pos_sale", q["name"], "reverse"), {k: -v for k, v in gl(api, "pos_sale", q["name"]).items()})
    # the buyer of receipt 1 brings the sugar back: void, then amend without it
    st, resp = void(dika, s1["name"])
    if not check("Dika voids receipt 1 for a return -> 200", st == 200, (st, resp)):
        return
    st, am = dika.verb("pos_sale", s1["name"], "amend")
    am = rec(am)
    if not check("Dika amends it -> a new draft", st in (200, 201) and am.get("docstatus") == 0, (st, am)):
        return
    keep = [{"item": l["item"], "qty": l["qty"], "packing": l.get("packing") or "", "discount_pct": l["discount_pct"]} for l in s1["items"] if l["item"] != GULA]
    st, resp = dika.patch("pos_sale", am["name"], {"items": keep, "payments": []})
    check("the amended draft keeps everything but the sugar", st == 200, (st, resp))
    gula = bal(api, DGO, GULA)
    st, r = dika.verb("pos_sale", am["name"], "submit")
    r = rec(r)
    if check("the amended receipt is paid -> 200, numbered -1", st == 200 and r.get("name") == s1["name"] + "-1", (st, r)):
        CTX["sale1b"] = r
        eq("the sugar stays in stock; the rest left again", bal(api, DGO, GULA), gula)
        eq("the amended total is the old one minus the sugar line", r["grand_total"], s1["grand_total"] - 16_200)
    s = shift_of(api, shift)
    sales = api.find("pos_sale", f"shift == '{shift}' && docstatus == 1")
    eq("shift totals = the live receipts of the shift", (s["sales_count"], s["net_total"], s["cash_total"], s["qris_total"]),
       (len(sales), sum(x["grand_total"] for x in sales), sum(x["cash_amount"] for x in sales), sum(x["qris_amount"] for x in sales)))


def scenario_pos_race(api):
    """Two receipts paid at the same moment on one shift are both counted (the flows add one after the other)."""
    sari, shift = CTX.get("sari"), CTX.get("shift_riu")
    if not (sari and shift):
        return
    drafts = []
    for _ in range(4):
        st, d = sale(sari, shift, [{"item": KHT, "qty": 1}], "", submit=False)
        if st == 201:
            drafts.append(d["name"])
    if not check("four drafts for the race", len(drafts) == 4, drafts):
        return
    res, gate = [None] * 4, threading.Barrier(4)

    def go(i):
        gate.wait()
        res[i] = sari.verb("pos_sale", drafts[i], "submit")

    ts = [threading.Thread(target=go, args=(i,)) for i in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    check("four receipts paid at the same moment all pass", all(r and r[0] == 200 for r in res), [r and r[0] for r in res])
    names = sorted(rec(r[1]).get("name", "") for r in res if r and r[0] == 200)
    eq("they get four different consecutive numbers", names, [f"STRK-RIU-{yymm()}-{n:05d}" for n in range(2, 6)])
    s = shift_of(api, shift)
    sales = api.find("pos_sale", f"shift == '{shift}' && docstatus == 1")
    eq("the shift counts every one of them", (s["sales_count"], s["net_total"]), (len(sales), sum(x["grand_total"] for x in sales)))


def close(cl, shift, counted, note=""):
    st, c = cl.create("pos_shift_close", {"shift": shift, "counted_cash": counted, "closing_note": note})
    if st != 201:
        return st, c
    return cl.verb("pos_shift_close", rec(c)["name"], "submit")


def scenario_pos_close(api):
    """Closing (Tutup sesi), blind: the cashier counts without seeing what should be there; the difference is
    journalled as selisih kas and shown to head office. The shift closes and takes no sale and no void after; a close
    reads the shift again at submit; head office reopens by cancelling a close."""
    dika, hq, shift = CTX.get("dika"), CTX.get("hq"), CTX.get("shift_dgo")
    if not (dika and hq and shift):
        return
    mine = dika.get("pos_shift", shift) or {}
    eq("blind count: the cashier sees no money total of the shift",
       sorted(k for k in ("expected_cash", "cash_total", "cash_difference", "net_total", "gross_total", "qris_total", "void_total", "cogs_total") if k in mine), [])
    # the float is head office's to set per outlet; setting it does not stop a shift that is open
    hq.patch("outlet", DGO, {"cash_float": 150_000})
    st, r = sale(dika, shift, [{"item": AIR, "qty": 1}], "")
    check("a float set on the outlet while the shift is open: the shift sells on", st == 200, (st, r))
    st, resp = bayu.create("pos_shift", {"outlet": DGO, "opening_cash": 900_000}) if (bayu := CTX.get("bayu")) else (0, {})
    check("a new shift with a float other than the outlet's -> 422 float_of_outlet", st == 422 and "float_of_outlet" in rules(resp), (st, resp))
    hq.patch("outlet", DGO, {"cash_float": 200_000})
    st, late = sale(dika, shift, [{"item": AIR, "qty": 1}], "", submit=False)   # a draft left open on the shift
    s = shift_of(api, shift)
    st, c = close(dika, shift, s["expected_cash"] - 2_000, "Hitung akhir hari")
    c = rec(c)
    if not check("Dika closes the shift -> 200", st == 200 and c.get("docstatus") == 1, (st, c)):
        return
    eq("the close is numbered per outlet", c["name"], f"TUTUP-DGO-{yymm()}-0001")
    eq("the close's answer to the cashier has no expected cash or difference", sorted(k for k in ("expected_cash", "cash_difference") if k in c), [])
    closed = api.get("pos_shift_close", c["name"])
    eq("head office sees the shift's figures and a difference of -2.000", (closed["net_total"], closed["expected_cash"], closed["cash_difference"]),
       (s["net_total"], s["expected_cash"], -2_000))
    s = shift_of(api, shift)
    eq("the shift is closed with the counted cash", (s["state"], s["closed_by"], s["cash_difference"]), ("closed", c["name"], -2_000))
    eq("journal of the close: cash counted less float to Kas, QRIS to Bank, Kas outlet cleared, Selisih kas 2.000",
       gl(api, "pos_shift_close", c["name"]),
       {"11100": closed["counted_cash"] - closed["opening_cash"], "11200": closed["qris_total"], "11150": -(closed["cash_total"] + closed["qris_total"]), "61300": 2_000})
    left = sum(r["amount"] for r in api.find("gl_entry", f"account == '11150' && outlet == '{DGO}'"))
    eq("Kas outlet belum disetor of Toko Dago is 0 once its only shift closed", left, 0)
    if late.get("name"):
        st, resp = dika.verb("pos_sale", late["name"], "submit")
        check("a draft paid after its shift closed -> 422 shift_open", st == 422 and "shift_open" in rules(resp), (st, resp))
        dika.call("DELETE", f"/api/v1/r/pos_sale/{Api.q(late['name'])}")
    sp = CTX.get("sale_split")
    if sp:
        st, resp = void(dika, sp["name"])
        check("a receipt of a closed shift cannot be voided -> 422 void_shift_closed", st == 422 and "void_shift_closed" in rules(resp), (st, resp))
    st, resp = close(dika, shift, 0)
    check("a closed shift cannot be closed again -> 422 shift_open", st == 422 and "shift_open" in rules(resp), (st, resp))
    st, resp = dika.verb("pos_shift_close", c["name"], "cancel")
    check("a cashier cannot reopen a shift (cancel its close) -> 403", denied(st, resp), (st, resp))
    st, resp = hq.verb("pos_shift_close", c["name"], "cancel")
    if not check("head office cancels the close (a miscount) -> 200", st == 200, (st, resp)):
        return
    s = shift_of(api, shift)
    eq("the shift is open again, counted figures cleared", (s["state"], s["closed_by"], s["cash_difference"]), ("open", "", 0))
    eq("the close's journal is reversed", gl(api, "pos_shift_close", c["name"], "reverse"), {k: -v for k, v in gl(api, "pos_shift_close", c["name"]).items()})
    # Dika saves a new close with the right count, then sells once more before submitting: the close reads the shift
    # again at submit, so the 4.000 of that sale is short in the drawer count
    st, c2 = dika.create("pos_shift_close", {"shift": shift, "counted_cash": s["expected_cash"]})
    st2, r = sale(dika, shift, [{"item": AIR, "qty": 1}], "")
    if check("a close drafted, then one more sale", st == 201 and st2 == 200, (st, c2, st2, r)):
        st, resp = dika.verb("pos_shift_close", rec(c2)["name"], "submit")
        if check("the close submits -> 200", st == 200, (st, resp)):
            again = api.get("pos_shift_close", rec(resp)["name"])
            eq("it counted the last sale: numbered 0002, expected up by 4.000, difference -4.000",
               (again["name"], again["expected_cash"], again["cash_difference"]), (f"TUTUP-DGO-{yymm()}-0002", s["expected_cash"] + 4_000, -4_000))
    # the next shift of the same cashier
    s2 = open_shift(dika, DGO, 200_000, "Dika's next shift")
    if s2:
        eq("the next shift is 0002 of the outlet", s2["name"], f"SESI-DGO-{yymm()}-0002")
        st, r = sale(dika, s2["name"], [{"item": AIR, "qty": 1}], "")
        check("the next shift sells on: receipt number continues for the outlet", st == 200 and r.get("name", "").startswith(f"STRK-DGO-{yymm()}-"), (st, r))


def scenario_pos_close_race(api):
    """Contract 0025: a close submitted at the same moment as sales on the shift. Each sale is either counted before
    the close (and in its figures) or refused after it; the close's expected cash is the shift's."""
    sari, shift = CTX.get("sari"), CTX.get("shift_riu")
    if not (sari and shift):
        return
    drafts = []
    for _ in range(3):
        st, d = sale(sari, shift, [{"item": ROTI, "qty": 1}], "", submit=False)
        if st == 201:
            drafts.append(d["name"])
    st, c = sari.create("pos_shift_close", {"shift": shift, "counted_cash": 0, "closing_note": "Uji tutup bersamaan"})
    if not check("three sale drafts and a close draft for the race", len(drafts) == 3 and st == 201, (drafts, st, c)):
        return
    jobs = [("pos_sale", n) for n in drafts] + [("pos_shift_close", rec(c)["name"])]
    res, gate = [None] * 4, threading.Barrier(4)

    def go(i):
        gate.wait()
        res[i] = sari.verb(jobs[i][0], jobs[i][1], "submit")

    ts = [threading.Thread(target=go, args=(i,)) for i in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    codes = [r and r[0] for r in res]
    check("every submit of the race answers 200 or 422", all(x in (200, 422) for x in codes), res)
    s = shift_of(api, shift)
    sales = api.find("pos_sale", f"shift == '{shift}' && docstatus == 1")
    eq("after the race the shift's totals are its live receipts", (s["sales_count"], s["net_total"], s["cash_total"]),
       (len(sales), sum(x["grand_total"] for x in sales), sum(x["cash_amount"] for x in sales)))
    if codes[3] == 200:
        cl = api.get("pos_shift_close", rec(res[3][1])["name"])
        eq("the close took the shift's figures at the moment it closed", (s["state"], cl["expected_cash"], cl["net_total"]),
           ("closed", s["opening_cash"] + s["cash_total"], s["net_total"]))
        late = [r for r in res[:3] if r and r[0] == 422]
        check("a sale that lost the race to the close was refused as closed", all({"shift_open", "shift_closed"} & set(rules(r[1])) for r in late), late)
    else:
        check("the close lost only to a sale (close_stale), never silently", "close_stale" in rules(res[3][1]), res[3])


def scenario_pos_print(api):
    """The receipt prints from the browser on 80 mm paper."""
    r = CTX.get("sale_riu")
    if not r:
        return
    st, raw, ctype = api.print_html("pos_sale", r["name"], "pos_receipt")
    if not check("struk: print answers 200 text/html", st == 200 and ctype.startswith("text/html"), (st, ctype, raw[:200])):
        return
    text = page_text(raw)
    for needle in (r["name"], "CV Contoh Warung Rasa", "Kedai Contoh Riau", "Meja 4", "Kopi susu aren", "Tunai", "Kembalian", "LUNAS", "Termasuk PPN"):
        check(f"struk shows {needle!r}", needle in text, text[:600])
    q = CTX.get("sale_qris")
    if q:
        st, raw, _ = api.print_html("pos_sale", q["name"], "pos_receipt")
        check("a voided receipt prints BATAL", "BATAL" in page_text(raw), page_text(raw)[:400])


def scenario_pos_dashboard(api):
    """kasir_ringkas against the receipts; per outlet and day, the receipts, the shifts and the journal agree."""
    ws = dashboard(api, "kasir_ringkas")
    if not ws:
        return
    sales = api.list("pos_sale", filter="docstatus == 1", limit=500)
    voids = api.list("pos_sale", filter="docstatus == 2", limit=500)
    eq("dashboard: Penjualan kasir = paid receipts", num(ws["net_total"]), sum(s["grand_total"] for s in sales))
    eq("dashboard: Struk and Struk dibatalkan", (num(ws["receipts"]), num(ws["voids"])), (len(sales), len(voids)))
    per = {}
    for s in sales:
        per[s["outlet"]] = per.get(s["outlet"], 0) + s["grand_total"]
    eq("dashboard: per outlet = receipts per outlet", chart_map(ws["net_by_outlet"]), per)
    shifts = api.list("pos_shift", limit=500)
    by_shift = {}
    for sh in shifts:
        by_shift[sh["outlet"]] = by_shift.get(sh["outlet"], 0) + sh["net_total"]
    eq("daily recap per outlet: the shifts' net = the receipts", {k: v for k, v in by_shift.items() if v}, per)
    led = {}
    for r in api.find("gl_entry", "voucher_type == 'pos_sale' && account == '11150'"):
        led[r["outlet"]] = led.get(r["outlet"], 0) + r["amount"]
    eq("daily recap per outlet: Kas outlet belum disetor from receipts (posts minus voids) = the receipts", {k: v for k, v in led.items() if v}, per)
    eq("dashboard: Selisih kas = submitted closes = closed shifts", num(ws["cash_difference"]),
       sum(c["cash_difference"] for c in api.list("pos_shift_close", filter="docstatus == 1", limit=500)))
    eq("dashboard: Sesi kasir terbuka", num(ws["open_shifts"]), sum(1 for sh in shifts if sh["state"] == "open"))
    # the Jalur A dashboard next door stays on outlet_sales: Cimahi's form only
    wp = dashboard(api, "penjualan_ringkas")
    f = CTX.get("form_cmh")
    if wp and f:
        eq("penjualan_ringkas counts the daily forms (Cimahi), not the cashier", chart_map(wp["net_by_outlet"]), {CMH: f["net_total"]})
        eq("penjualan_ringkas shows the cashier's sales in their own widget", num(wp["pos_net_total"]), sum(s["grand_total"] for s in sales))


def scenario_pos_head_office(api):
    """Head office: /meta matches the matrix on the cashier models; it reads every outlet and voids on any shift."""
    hq = CTX.get("hq")
    if not hq:
        return
    st, mm = hq.call("GET", "/api/v1/meta")
    wrong = []
    for n, row in MATRIX.items():
        can = ((mm or {}).get("models", {}).get(n) or {}).get("can", {})
        got = {x for x in ("read", "create", "write", "delete", "submit", "cancel", "amend", "export", "import", "print") if can.get(x)}
        if got != row.get("head_office", set()):
            wrong.append(f"{n}: got {sorted(got)} want {sorted(row.get('head_office', set()))}")
    check("Kantor pusat: /meta can matches the matrix on the cashier models", not wrong, wrong)
    st, resp = hq.call("GET", "/api/v1/r/pos_sale?limit=200")
    check("Kantor pusat lists receipts of both outlets", st == 200 and {r["outlet"] for r in resp.get("data", [])} >= {DGO, RIU}, (st, resp))
    st, resp = hq.call("GET", "/api/v1/dashboards/kasir_ringkas")
    check("Kantor pusat opens kasir_ringkas -> 200", st == 200, (st, resp))
    pgi = CTX.get("shift_pgi")
    if pgi:
        st, resp = close(hq, pgi, 0)
        check("head office closes the empty Kios Pagi shift", st == 200, (st, resp))
    cmh = CTX.get("shift_cmh")
    if cmh:
        st, resp = close(hq, cmh, 0)
        check("head office closes the empty Cimahi shift", st == 200, (st, resp))


def scenario_pos_catalogue_hides_inactive(api):
    """An item switched off (active false: it cannot be deleted once sold) leaves the cashier's catalogue; the screen's
    own catalogue filter, read from /meta as the cashier's web app does, decides (rangka-os card t_542e70e1)."""
    dika = CTX.get("dika")
    if not dika:
        return
    st, it = api.create("item", {"item_name": "Teh kotak lama contoh", "kind": "good", "stock_uom": "UOM-001", "sku": "TEH-LAMA", "rate": 5000, "active": False})
    if not check("an item switched off -> 201", st == 201 and it, (st, it)):
        return
    st, mm = dika.call("GET", "/api/v1/meta")
    flt = (((((mm or {}).get("screens") or {}).get("kasir") or {}).get("counter") or {}).get("catalogue") or {}).get("filter")
    if not check("Dika's /meta carries the kasir catalogue filter", st == 200 and flt, (st, flt)):
        return
    names = [r["name"] for r in dika.find("item", flt)]
    check("the cashier's catalogue leaves out the item switched off, keeps the others",
          it["name"] not in names and AIR in names and MI in names, (flt, it["name"], names))


SCENARIOS = [scenario_pos_opening_stock, scenario_pos_shift, scenario_pos_retail_sale, scenario_pos_fnb_sale, scenario_pos_guards,
             scenario_pos_daily_form_next_door, scenario_pos_void_and_return, scenario_pos_race, scenario_pos_close, scenario_pos_close_race, scenario_pos_print,
             scenario_pos_dashboard, scenario_pos_head_office, scenario_pos_catalogue_hides_inactive, scenario_pos_kasir_only_site, scenario_pos_day_source_kept_after_removal, scenario_pos_day_source_backfill]
