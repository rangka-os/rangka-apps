"""Day-one scenarios of demo-grosir (app grosir_distribusi): a sub-distributor with three salesmen.

Loaded by tests/day_one.py (bind() hands over its helpers), so `python3 tests/day_one.py --site demo-grosir` runs them.
The fixtures are synthetic (examples/fixtures/demo-grosir/generate.py): CUST-0001..0003 are Asep's (wholesale, retail,
agent), CUST-0004 is Ujang's, CUST-0005 Wawan's. Stock documents (delivery note, sales return) need the declarative
stock block of contract 0021 (K-o); on a kernel without it those checks are gaps.
"""

GUDANG, KANVAS, RUSAK = "OUT-01", "OUT-02", "OUT-03"
ASEP, UJANG, WAWAN = "USR-011", "USR-012", "USR-013"
MI, TEH, MINYAK = "BRG-0001", "BRG-0002", "BRG-0003"   # rate / rate_wholesale / rate_agent in the fixtures

H = {}


def bind(m):
    """Take the helpers of tests/day_one.py (ok, fail, gap, check, eq, CTX, Api, denied, ...)."""
    for k in ("ok", "fail", "gap", "check", "eq", "CTX", "Api", "denied", "approvals_of", "dashboard", "secrets", "messages", "qris_payload", "check_print", "installed"):
        H[k] = getattr(m, k)
    globals().update(H)


SALESMEN = [("asep", "asep@contohniaga.test", ASEP, "Asep Contoh"),
            ("ujang", "ujang@contohniaga.test", UJANG, "Ujang Contoh"),
            ("wawan", "wawan@contohniaga.test", WAWAN, "Wawan Contoh")]


def setup_demo_grosir(api, k):
    """Members: sales manager, finance, warehouse (head office) and the three salesmen (role field_sales)."""
    st, me = api.call("GET", "/auth/me")
    org = ((me or {}).get("org") or {}).get("id") or "niaga_contoh"
    CTX.update(kernel=k, org=org)
    pw = secrets.token_urlsafe(18)   # throwaway password of a throwaway database
    people = [("sm", "rudi@contohniaga.test", "sales_manager", "USR-001", "Rudi Contoh"),
              ("fm", "lina@contohniaga.test", "finance_manager", "USR-002", "Lina Contoh"),
              ("hq", "hendra@contohniaga.test", "head_office", "USR-003", "Hendra Contoh")]
    people += [(key, email, "field_sales", rec, name) for key, email, rec, name in SALESMEN]
    for key, email, role, rec, name in people:
        code, out = k.user_add(email, role, org, name, record=rec, password=pw)
        cl = Api.login(k.base, email, pw) if code == 0 else None
        if cl is None:
            CTX["login_error"] = f"{email}: " + (out if code else "login refused")
            fail("setup: add and sign in the members", CTX["login_error"])
            return
        CTX[key] = cl
    CTX["member_pw"] = pw
    opening_stock(api)


def opening_stock(api):
    """Opening stock of the warehouse and the van: one goods receipt each from 'Saldo awal' at the cost price."""
    import importlib.util, os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples", "fixtures", "demo-grosir", "generate.py")
    spec = importlib.util.spec_from_file_location("grosir_gen", path)
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    cost = {i["name"]: i["standard_cost"] for i in gen.build()["item"]}
    for outlet, qty in gen.opening_stock().items():
        st, gr = api.create("goods_receipt", {"vendor": gen.SALDO, "outlet": outlet, "received_on": "2026-11-01",
                                              "items": [{"item": i, "qty": q, "rate": cost[i]} for i, q in qty.items()]})
        if st != 201:
            fail(f"setup: opening stock {outlet}", (st, gr))
            continue
        st, resp = api.verb("goods_receipt", gr["name"], "submit")
        if st != 200:
            fail(f"setup: submit opening stock {outlet}", (st, resp))


def _need(*keys):
    missing = [k for k in keys if CTX.get(k) is None]
    if missing:
        fail("grosir: members signed in", CTX.get("login_error") or missing)
        return None
    return [CTX[k] for k in keys]


def order_body(customer, lines, **extra):
    body = {"customer": customer, "po_number": extra.pop("po_number", "SO-UJI"), "po_date": extra.pop("po_date", "2026-11-03"),
            "items": [{"item": i, "qty": q} for i, q in lines]}
    body.update(extra)
    return body


def rates(rec):
    return [(r["item"], r.get("price_tier"), r["rate"]) for r in rec.get("items", [])]


def approve_all(cl, model, name):
    """Approve every pending request of a record as `cl`; returns the last answer."""
    last = None
    for a in approvals_of(cl, model, name):
        if a.get("state") == "pending":
            last = cl.call("POST", f"/api/v1/approvals/{a['id']}/approve", {})
    return last


def overdue_invoice(api, customer, amount, posting="2026-09-01", due="2026-09-15", salesman=ASEP):
    st, inv = api.create("sales_invoice", {"customer": customer, "posting_date": posting, "due_date": due,
                                            "items": [{"item": MI, "qty": 1, "rate": amount}]})
    if st != 201:
        fail("setup: overdue invoice", (st, inv))
        return None
    st, sub = api.verb("sales_invoice", inv["name"], "submit")
    if st != 200:
        fail("setup: submit overdue invoice", (st, sub))
        return None
    return sub


# ---------- scenarios

def scenario_grosir_fixtures(api):
    users = {u["name"] for u in api.list("user", limit=50)}
    check("three salesmen in the fixtures", {ASEP, UJANG, WAWAN} <= users, users)
    custs = api.list("customer", limit=200)
    by = {}
    for c in custs:
        by.setdefault(c.get("salesman"), []).append(c["name"])
    check("every salesman has customers", all(len(by.get(s, [])) >= 5 for s in (ASEP, UJANG, WAWAN)), {k: len(v) for k, v in by.items()})
    tiers = {c.get("price_tier") for c in custs}
    check("customers use all three price tiers", tiers >= {"retail", "wholesale", "agent"}, tiers)
    mi = api.get("item", MI)
    check("item has three prices (eceran > grosir > agen)", mi["rate"] > mi["rate_wholesale"] > mi["rate_agent"] > 0,
          (mi.get("rate"), mi.get("rate_wholesale"), mi.get("rate_agent")))
    outlets = {o["name"] for o in api.list("outlet")}
    check("warehouse, canvas van and damaged-goods room", {GUDANG, KANVAS, RUSAK} <= outlets, outlets)


def scenario_grosir_tier_price(api):
    mi, teh = api.get("item", MI), api.get("item", TEH)
    want = {"CUST-0001": mi["rate_wholesale"], "CUST-0002": mi["rate"], "CUST-0003": mi["rate_agent"]}
    for cust, rate in want.items():
        st, so = api.create("customer_order", order_body(cust, [(MI, 10)], po_number="TIER-" + cust))
        if not check(f"order for {cust} created", st == 201, (st, so)):
            continue
        so = api.get("customer_order", so["name"])
        eq(f"{cust}: line priced at its tier ({so.get('price_tier')})", rates(so), [(MI, so.get("price_tier"), rate)])
        eq(f"{cust}: order amount follows the tier price", so["subtotal"], 10 * rate)
    # a price typed by hand stays; a line added later gets the tier price
    st, so = api.create("customer_order", order_body("CUST-0001", [(MI, 5)], po_number="TIER-EDIT"))
    so = api.get("customer_order", so["name"])
    rows = so["items"]
    rows[0]["rate"] = mi["rate_wholesale"] - 500
    rows.append({"item": TEH, "qty": 2})
    st, resp = api.patch("customer_order", so["name"], {"items": rows})
    check("edit order (special price + new line) -> 200", st == 200, (st, resp))
    so = api.get("customer_order", so["name"])
    eq("special price kept, new line at the wholesale price", rates(so),
       [(MI, "wholesale", mi["rate_wholesale"] - 500), (TEH, "wholesale", teh["rate_wholesale"])])
    # changing the customer re-prices the lines to the new tier
    st, resp = api.patch("customer_order", so["name"], {"customer": "CUST-0002"})
    so = api.get("customer_order", so["name"])
    check("another customer: order tier follows the customer", so.get("price_tier") == "retail", so.get("price_tier"))
    eq("lines re-priced at retail after the customer change", [r[2] for r in rates(so)], [mi["rate"], teh["rate"]])


def scenario_grosir_salesman_scope(api):
    got = _need("asep", "ujang")
    if not got:
        return
    asep, ujang = got
    mine = {c["name"] for c in asep.list("customer", limit=200)}
    check("Asep sees only his own customers", mine and all(api.get("customer", c).get("salesman") == ASEP for c in mine), sorted(mine)[:5])
    check("Asep does not see Ujang's customer", "CUST-0004" not in mine, sorted(mine)[:5])
    st, so = asep.create("customer_order", order_body("CUST-0001", [(MI, 3)], po_number="ASEP-1"))
    if check("Asep creates an order for his customer -> 201", st == 201, (st, so)):
        eq("the order is Asep's", so.get("salesman"), ASEP)
        CTX["asep_order"] = so["name"]
    st, resp = asep.create("customer_order", order_body("CUST-0004", [(MI, 3)], po_number="ASEP-2"))
    check("Asep cannot take an order for Ujang's customer -> 403/404/422", st in (403, 404, 422), (st, resp))
    theirs = {o["name"] for o in ujang.list("customer_order", limit=200)}
    check("Ujang does not see Asep's order", CTX.get("asep_order") not in theirs, sorted(theirs))
    st, resp = asep.call("GET", "/api/v1/r/vendor_bill")
    check("a salesman cannot read supplier bills -> 403", denied(st, resp), (st, resp))


def scenario_grosir_credit_hold(api):
    got = _need("asep", "sm", "fm")
    if not got:
        return
    asep, sm, fm = got
    # clean customer, within limit: submits at once
    st, so = asep.create("customer_order", order_body("CUST-0002", [(MI, 2)], po_number="KREDIT-OK"))
    st, resp = asep.verb("customer_order", so["name"], "submit")
    check("order of a customer with nothing overdue submits at once -> 200", st == 200 and resp.get("docstatus") == 1, (st, resp))
    # an overdue invoice holds the next order
    inv = overdue_invoice(fm, "CUST-0001", 1_500_000)
    cust = api.get("customer", "CUST-0001")
    eq("customer shows its oldest open due date", cust.get("oldest_open_due"), "2026-09-15")
    st, so = asep.create("customer_order", order_body("CUST-0001", [(MI, 2)], po_number="KREDIT-TAHAN"))
    st, resp = asep.verb("customer_order", so["name"], "submit")
    pol = [a.get("policy") for a in (resp or {}).get("approvals", [])] if isinstance(resp, dict) else []
    check("order of a customer with an overdue invoice is held -> 202 (policy overdue)", st == 202 and "overdue" in pol, (st, pol, resp))
    check("held order stays a draft", api.get("customer_order", so["name"]).get("docstatus") == 0)
    st, resp = asep.call("POST", f"/api/v1/approvals/{(resp or {}).get('approval', {}).get('id', 'x')}/approve", {})
    check("the salesman cannot release his own held order -> 403", st == 403, (st, resp))
    r = approve_all(sm, "customer_order", so["name"])
    rec = ((r or (0, {}))[1] or {}).get("record") or {}
    check("sales manager approves -> the order is submitted", r and r[0] == 200 and rec.get("docstatus") == 1, r)
    # paying the overdue invoice clears the hold
    if inv:
        st, pay = fm.create("payment", {"direction": "receive", "customer": "CUST-0001", "method": "transfer", "amount": inv["outstanding"],
                                        "allocations": [{"sales_invoice": inv["name"], "amount": inv["outstanding"]}]})
        st, resp = fm.verb("payment", pay["name"], "submit")
        check("payment of the overdue invoice -> 200", st == 200, (st, resp))
        eq("oldest open due cleared after payment", api.get("customer", "CUST-0001").get("oldest_open_due"), "")
        st, so = asep.create("customer_order", order_body("CUST-0001", [(MI, 2)], po_number="KREDIT-LEPAS"))
        st, resp = asep.verb("customer_order", so["name"], "submit")
        check("after payment the next order submits at once -> 200", st == 200, (st, resp))
    # a salesman's price below the tier price waits for the sales manager
    st, so = asep.create("customer_order", order_body("CUST-0002", [(MI, 3)], po_number="HARGA-1"))
    rows = asep.get("customer_order", so["name"])["items"]
    rows[0]["rate"] = 1
    asep.patch("customer_order", so["name"], {"items": rows})
    st, resp = asep.verb("customer_order", so["name"], "submit")
    pol = [a.get("policy") for a in (resp or {}).get("approvals", [])] if isinstance(resp, dict) else []
    check("a salesman's price below the tier price is held -> 202 (policy below_tier_price)", st == 202 and "below_tier_price" in pol, (st, pol))
    # over the credit limit
    lim = api.get("customer", "CUST-0003")
    big = int(lim["credit_limit"] / api.get("item", MI)["rate_agent"]) + 5
    st, so = asep.create("customer_order", order_body("CUST-0003", [(MI, big)], po_number="KREDIT-LIMIT"))
    st, resp = asep.verb("customer_order", so["name"], "submit")
    pol = [a.get("policy") for a in (resp or {}).get("approvals", [])] if isinstance(resp, dict) else []
    check("order above the credit limit is held -> 202 (policy over_credit_limit)", st == 202 and "over_credit_limit" in pol, (st, pol))
    # a manual hold by finance
    st, resp = fm.patch("customer", "CUST-0005", {"credit_hold": True, "credit_hold_reason": "Uji tahan manual"})
    check("finance puts a customer on hold -> 200", st == 200, (st, resp))
    wawan = CTX.get("wawan")
    if wawan:
        st, so = wawan.create("customer_order", order_body("CUST-0005", [(MI, 1)], po_number="KREDIT-MANUAL"))
        st, resp = wawan.verb("customer_order", so["name"], "submit")
        pol = [a.get("policy") for a in (resp or {}).get("approvals", [])] if isinstance(resp, dict) else []
        check("order of a customer on hold is held -> 202 (policy credit_hold)", st == 202 and "credit_hold" in pol, (st, pol))
    st, resp = asep.patch("customer", "CUST-0001", {"credit_hold": False})
    check("a salesman cannot change a customer's credit -> 403", denied(st, resp), (st, resp))
    fm.patch("customer", "CUST-0005", {"credit_hold": False, "credit_hold_reason": ""})


def scenario_grosir_giro(api):
    got = _need("fm", "asep")
    if not got:
        return
    fm, asep = got
    st, inv = fm.create("sales_invoice", {"customer": "CUST-0002", "posting_date": "2026-11-02", "due_date": "2026-11-16",
                                          "items": [{"item": MI, "qty": 10, "rate": 100_000}]})
    st, inv = fm.verb("sales_invoice", inv["name"], "submit")
    if not check("invoice for the giro -> 200", st == 200, (st, inv)):
        return
    due = inv["outstanding"]
    st, pay = fm.create("payment", {"direction": "receive", "customer": "CUST-0002", "method": "giro", "reference": "BG-778812",
                                    "giro_bank": "Bank Contoh", "giro_due_date": "2026-11-20", "amount": due,
                                    "allocations": [{"sales_invoice": inv["name"], "amount": due}]})
    if not check("giro payment draft -> 201", st == 201, (st, pay)):
        return
    st, resp = fm.create("payment", {"direction": "receive", "customer": "CUST-0002", "method": "giro", "amount": 1_000})
    check("a giro payment without number and effective date is refused -> 422", st == 422, (st, resp))
    st, pay = fm.verb("payment", pay["name"], "submit")
    check("giro payment submitted -> 200", st == 200, (st, pay))
    eq("invoice paid by the giro", api.get("sales_invoice", inv["name"]).get("outstanding"), 0)
    giros = api.find("giro", f"payment == '{pay['name']}'")
    if not check("a giro register line was made for the payment", len(giros) == 1, giros):
        return
    g = giros[0]
    eq("giro: number, bank, effective date, amount, status", (g["giro_no"], g["bank"], g["effective_date"], g["amount"], g["status"]),
       ("BG-778812", "Bank Contoh", "2026-11-20", due, "received"))
    eq("giro carries the customer's salesman", g.get("salesman"), api.get("customer", "CUST-0002").get("salesman"))
    st, resp = fm.patch("giro", g["name"], {"status": "bounced"})
    check("marking a giro bounced while its payment stands is refused -> 422", st == 422, (st, resp))
    st, resp = fm.patch("giro", g["name"], {"status": "deposited", "deposited_on": "2026-11-20"})
    check("giro deposited for clearing -> 200", st == 200, (st, resp))
    # the bank returns it: finance cancels the payment, the invoice opens again
    st, resp = fm.verb("payment", pay["name"], "cancel")
    check("bounced giro: cancel the payment -> 200", st == 200, (st, resp))
    eq("invoice open again after the bounce", api.get("sales_invoice", inv["name"]).get("outstanding"), due)
    g = api.get("giro", g["name"])
    eq("giro marked bounced by the cancel", g.get("status"), "bounced")
    c = api.get("customer", "CUST-0002")
    check("customer put on hold after the bounce", c.get("credit_hold") is True and "BG-778812" in (c.get("credit_hold_reason") or ""), (c.get("credit_hold"), c.get("credit_hold_reason")))
    st, so = asep.create("customer_order", order_body("CUST-0002", [(MI, 1)], po_number="GIRO-TOLAK"))
    st, resp = asep.verb("customer_order", so["name"], "submit")
    check("next order of that customer is held -> 202", st == 202, (st, resp))
    fm.patch("customer", "CUST-0002", {"credit_hold": False, "credit_hold_reason": ""})
    st, resp = asep.call("GET", "/api/v1/r/giro")
    check("a salesman sees giro of his own customers only", st == 200 and all(x.get("salesman") == ASEP for x in resp.get("data", [])), (st, resp))


def scenario_grosir_ar_by_salesman(api):
    got = _need("fm", "sm", "ujang", "hq")
    if not got:
        return
    fm, sm, ujang, hq = got
    st, so = ujang.create("customer_order", order_body("CUST-0004", [(MI, 4), (TEH, 2)], po_number="AR-UJANG"))
    st, so = ujang.verb("customer_order", so["name"], "submit")
    if not check("Ujang's order submitted", st == 200, (st, so)):
        return
    st, inv = sm.action("customer_order", so["name"], "make_invoice")
    name = ((inv or {}).get("open") or {}).get("name")
    eq("invoice drafted before any delivery bills nothing", len((api.get("sales_invoice", name) if name else {}).get("items", [1])), 0)
    if name:
        sm.call("DELETE", f"/api/v1/r/sales_invoice/{Api.q(name)}")
    st, dn = hq.create("delivery_note", {"customer_order": so["name"], "outlet": GUDANG, "items": [{"item": MI, "qty": 3}]})
    st, resp = hq.verb("delivery_note", (dn or {}).get("name", "x"), "submit")
    if st != 200 and "no rule for delivery_note" in str(resp):
        gap("invoice follows the delivered quantity", "K-o (contract 0021)", "delivery notes do not post on this kernel")
        return
    check("partial delivery of Ujang's order -> 200", st == 200, (st, resp))
    st, inv = sm.action("customer_order", so["name"], "make_invoice")
    name = ((inv or {}).get("open") or {}).get("name")
    check("make_invoice from Ujang's order -> 200", st == 200 and name, (st, inv))
    rec = api.get("sales_invoice", name) if name else {}
    eq("invoice from the order carries the salesman", rec.get("salesman"), UJANG)
    eq("invoice bills what was delivered, at the tier price", [(i["item"], i["qty"], i["rate"]) for i in rec.get("items", [])], [(MI, 3, api.get("item", MI)["rate_wholesale"])])
    if rec:
        rows = rec["items"]
        rows[0]["qty"] = 4
        sm.patch("sales_invoice", name, {"items": rows})
        st, resp = sm.verb("sales_invoice", name, "submit")
        check("invoicing more than delivered is refused at submit -> 422", st == 422, (st, resp))
        rows[0]["qty"] = 3
        sm.patch("sales_invoice", name, {"items": rows})
    if rec:
        fm.verb("sales_invoice", name, "submit")
    ws = dashboard(sm, "grosir_ar")
    w = ws.get("ar_by_salesman", {})
    keys = {d.get("key") for d in w.get("data", [])}
    check("AR by salesman shows Ujang", UJANG in keys, w.get("data"))
    mine = ujang.list("sales_invoice", limit=200)
    check("Ujang sees only his invoices", mine and all(i.get("salesman") == UJANG for i in mine), [i.get("salesman") for i in mine][:5])


def scenario_dashboards_grosir(api):
    ws = dashboard(api, "grosir_overview")
    for w in ("held_orders", "overdue_ar", "ar_by_salesman", "giro_due", "sales_by_salesman"):
        check(f"grosir_overview has {w}", w in ws, sorted(ws))


def scenario_grosir_delivery(api):
    """Surat jalan: partial delivery from the warehouse takes stock out at the moving average (needs K-o)."""
    hq = CTX.get("hq")
    if hq is None:
        fail("grosir: members signed in", CTX.get("login_error"))
        return
    st, so = api.create("customer_order", order_body("CUST-0001", [(MI, 10), (TEH, 4)], po_number="SJ-1"))
    st, so = api.verb("customer_order", so["name"], "submit")
    before = {b["item"]: b for b in api.find("stock_balance", f"outlet == '{GUDANG}'")}
    st, dn = hq.create("delivery_note", {"customer_order": so["name"], "outlet": GUDANG, "delivery_date": "2026-11-04",
                                         "items": [{"item": MI, "qty": 6}, {"item": TEH, "qty": 4}]})
    if not check("delivery note draft from the order -> 201", st == 201, (st, dn)):
        return
    eq("delivery note takes the customer and salesman of the order", (dn.get("customer"), dn.get("salesman")), ("CUST-0001", ASEP))
    st, resp = hq.verb("delivery_note", dn["name"], "submit")
    if st != 200 and "no rule for delivery_note" in str(resp):
        gap("delivery note moves stock", "K-o (contract 0021)", "stock.post_moves has no rule for delivery_note on this kernel")
        return
    if not check("delivery note submitted -> 200", st == 200, (st, resp)):
        return
    dn = resp
    after = {b["item"]: b for b in api.find("stock_balance", f"outlet == '{GUDANG}'")}
    eq("warehouse stock of MI down by 6", round(before[MI]["qty"] - after[MI]["qty"], 3), 6)
    dn = api.get("delivery_note", dn["name"])
    check("delivery note records its cost of goods", dn.get("cogs_total", 0) > 0, dn.get("cogs_total"))
    so = api.get("customer_order", so["name"])
    ful = api.get("order_fulfilment", so.get("fulfilment") or "x") or {}
    eq("order fulfilment shows ordered and delivered", [(i["item"], i["ordered_qty"], i["delivered_qty"]) for i in ful.get("items", [])], [(MI, 10, 6), (TEH, 4, 4)])
    st, resp = hq.create("delivery_note", {"customer_order": so["name"], "outlet": GUDANG, "delivery_date": "2026-11-05",
                                           "items": [{"item": MI, "qty": 5}]})
    check("delivering more than ordered is refused -> 422", st == 422, (st, resp))
    CTX["delivery_note"] = dn["name"]
    sm = CTX.get("sm")
    if sm:
        st, resp = sm.verb("customer_order", so["name"], "cancel")
        check("an order with a live delivery note cannot be cancelled -> 422", st == 422 and "surat jalan" in str(resp), (st, resp))
    # a draft delivery note pointed at another order is refused (its customer and fulfilment are the first order's)
    st, other = api.create("customer_order", order_body("CUST-0002", [(MI, 3)], po_number="SJ-GANTI"))
    st, other = api.verb("customer_order", other["name"], "submit")
    st, d2 = hq.create("delivery_note", {"customer_order": so["name"], "outlet": GUDANG, "items": [{"item": MI, "qty": 1}]})
    st, resp = hq.patch("delivery_note", (d2 or {}).get("name", "x"), {"customer_order": other["name"]})
    check("changing the order of a draft delivery note is refused -> 422", st == 422, (st, resp))
    if d2 and d2.get("name"):
        hq.call("DELETE", f"/api/v1/r/delivery_note/{Api.q(d2['name'])}")


def scenario_grosir_credit_at_delivery(api):
    """Orders a salesman splits under the limit are counted where goods leave: the delivery note is held."""
    got = _need("asep", "hq", "sm")
    if not got:
        return
    asep, hq, sm = got
    cust = api.get("customer", "CUST-0003")
    rate = api.get("item", TEH)["rate_agent"]
    qty = int(cust["credit_limit"] / rate / 1.11 * 0.6)     # each order is 60% of the limit (with PPN)
    orders = []
    for n in range(2):
        st, so = asep.create("customer_order", order_body("CUST-0003", [(TEH, qty)], po_number=f"PECAH-{n}"))
        st, so = asep.verb("customer_order", so["name"], "submit")
        if st == 200:
            orders.append(so)
    check("two split orders of a salesman each pass on their own -> 200", len(orders) == 2, orders)
    notes = []
    for so in orders:
        st, dn = hq.create("delivery_note", {"customer_order": so["name"], "outlet": GUDANG, "items": [{"item": TEH, "qty": qty}]})
        st, resp = hq.verb("delivery_note", (dn or {}).get("name", "x"), "submit")
        notes.append((st, resp))
    check("first delivery within the limit -> 200", notes and notes[0][0] == 200, notes[:1])
    pol = [a.get("policy") for a in (notes[1][1] or {}).get("approvals", [])] if len(notes) > 1 and isinstance(notes[1][1], dict) else []
    check("second delivery over the limit is held at the door -> 202 (over_credit_limit)", len(notes) > 1 and notes[1][0] == 202 and "over_credit_limit" in pol, (notes[1:] or [None])[0])
    eq("customer shows goods delivered and not yet invoiced", api.get("customer", "CUST-0003").get("uninvoiced_deliveries"),
       (notes[0][1] or {}).get("delivery_total") if notes and notes[0][0] == 200 else None)


def scenario_grosir_return(api):
    """Retur jual: good goods back to the warehouse at the cost they left at, damaged to the damaged-goods room (needs K-o)."""
    dn = CTX.get("delivery_note")
    hq = CTX.get("hq")
    if not dn or hq is None:
        gap("sales return puts stock back", "K-o (contract 0021)", "no delivery note posted stock on this kernel")
        return
    note = api.get("delivery_note", dn)
    unit = next(i for i in note["items"] if i["item"] == MI)["cogs"] / 6
    before = {b["outlet"] + b["item"]: b["qty"] for b in api.find("stock_balance", f"item == '{MI}'")}
    st, ret = hq.create("sales_return", {"delivery_note": dn, "return_date": "2026-11-06", "outlet": GUDANG, "damaged_outlet": RUSAK,
                                         "items": [{"item": MI, "qty": 2, "condition": "good"}, {"item": MI, "qty": 1, "condition": "damaged"}]})
    if not check("sales return draft -> 201", st == 201, (st, ret)):
        return
    st, resp = hq.verb("sales_return", ret["name"], "submit")
    if not check("sales return submitted -> 200", st == 200, (st, resp)):
        return
    ret = resp
    after = {b["outlet"] + b["item"]: b["qty"] for b in api.find("stock_balance", f"item == '{MI}'")}
    eq("2 good back in the warehouse", round(after[GUDANG + MI] - before.get(GUDANG + MI, 0), 3), 2)
    eq("1 damaged in the damaged-goods room", round(after.get(RUSAK + MI, 0) - before.get(RUSAK + MI, 0), 3), 1)
    moves = api.find("stock_move", f"voucher == '{ret['name']}'")
    check("returned at the cost it left at", moves and all(abs(m["unit_cost"] - round(unit)) <= 1 for m in moves), [(m["unit_cost"], unit) for m in moves])
    st, resp = hq.create("sales_return", {"delivery_note": dn, "return_date": "2026-11-06", "outlet": GUDANG,
                                          "items": [{"item": MI, "qty": 9, "condition": "good"}]})
    check("returning more than was delivered is refused -> 422", st == 422, (st, resp))
    st, resp = hq.verb("delivery_note", dn, "cancel")
    check("a delivery note with a live return cannot be cancelled -> 422", st == 422 and "retur" in str(resp), (st, resp))
    # the return is credited to the customer's invoice by a payment "Potong retur", once
    fm, sm = CTX.get("fm"), CTX.get("sm")
    if fm is None or sm is None:
        return
    st, inv = sm.action("customer_order", note["customer_order"], "make_invoice")
    inv_name = ((inv or {}).get("open") or {}).get("name")
    st, inv = sm.verb("sales_invoice", inv_name or "x", "submit")
    if not check("invoice for the delivered goods -> 200", st == 200, (st, inv)):
        return
    value = ret["return_total"]
    check("return value at the order price with PPN", value > 0 and ret["return_value"] == 3 * next(i for i in api.get("customer_order", note["customer_order"])["items"] if i["item"] == MI)["rate"], (ret["return_value"], value))
    body = {"direction": "receive", "customer": ret["customer"], "method": "sales_return", "sales_return": ret["name"], "amount": value,
            "allocations": [{"sales_invoice": inv["name"], "amount": value}]}
    st, p1 = fm.create("payment", body)
    st, p1 = fm.verb("payment", (p1 or {}).get("name", "x"), "submit")
    check("payment 'Potong retur' credits the return -> 200", st == 200, (st, p1))
    eq("invoice reduced by the return", api.get("sales_invoice", inv["name"])["outstanding"], inv["outstanding"] - value)
    st, p2 = fm.create("payment", dict(body, amount=1_000, allocations=[{"sales_invoice": inv["name"], "amount": 1_000}]))
    check("a second credit draft of the same return (within the invoice) -> 201", st == 201, (st, p2))
    st, resp = fm.verb("payment", (p2 or {}).get("name", "x"), "submit")
    check("the same return credited a second time is refused -> 422", st == 422, (st, resp))
    eq("invoice reduced only once", api.get("sales_invoice", inv["name"])["outstanding"], inv["outstanding"] - value)
    eq("the return shows what was credited", (api.find("return_credit", f"sales_return == '{ret['name']}'") or [{}])[0].get("credited"), value)
    st, resp = hq.verb("sales_return", ret["name"], "cancel")
    check("a credited return cannot be cancelled -> 422", st == 422, (st, resp))
    # a delivery note whose goods are invoiced cannot be cancelled either (the earlier SJ-1 note has 6 of 10 delivered)
    st, resp = fm.verb("payment", p1["name"], "cancel")
    st, resp = hq.verb("sales_return", ret["name"], "cancel")
    check("after the credit is cancelled the return can be cancelled -> 200", st == 200, (st, resp))
    eq("cancelled credit gives the return back", (api.find("return_credit", f"sales_return == '{ret['name']}'") or [{}])[0].get("credited"), 0)
    st, resp = hq.verb("delivery_note", dn, "cancel")
    check("a delivery note whose goods are invoiced cannot be cancelled -> 422", st == 422 and "ditagih" in str(resp), (st, resp))


def _race(calls):
    """Submit several drafts at the same moment (threads behind a barrier); returns [(status, body)] in call order."""
    import threading
    out, bar = [None] * len(calls), threading.Barrier(len(calls))
    def go(i, cl, model, name):
        bar.wait()
        out[i] = cl.verb(model, name, "submit")
    th = [threading.Thread(target=go, args=(i,) + c) for i, c in enumerate(calls)]
    for t in th:
        t.start()
    for t in th:
        t.join()
    return out


def scenario_grosir_credit_race(api):
    """t_561e44ec (audit of contract 0025, N1): the credit approvals on customer_order and delivery_note are evaluated
    before any flow lock, so two documents of one customer submitted at the same moment each saw the other's room. Each
    fits under the limit on its own and both together do not: at most one may go through without an over_credit_limit
    approval; the other is refused (and is held for approval when submitted again)."""
    got = _need("asep", "sm", "fm", "hq")
    if not got:
        return
    asep, sm, fm, hq = got
    tier = api.get("customer", "CUST-0003")
    def customer(label):
        st, c = fm.create("customer", {"customer_name": label, "salesman": ASEP, "price_tier": tier.get("price_tier"), "credit_limit": 1_000_000_000})
        check(f"customer {label} for the race", st == 201, (st, c))
        return c.get("name")
    def unapproved(model, results):
        ok = [b.get("name") for st, b in results if st == 200]
        return [n for n in ok if not any(a.get("policy") == "over_credit_limit" for a in approvals_of(api, model, n))]
    # orders by the sales manager (staff orders keep customer.open_orders current)
    for rnd in range(3):
        c = customer(f"Toko Balapan Pesanan {rnd}")
        drafts = []
        for n in range(2):
            st, so = sm.create("customer_order", order_body(c, [(MI, 10)], po_number=f"BALAP-{rnd}-{n}"))
            drafts.append(so.get("name") if st == 201 else None)
        if None in drafts:
            fail("race: two order drafts", drafts)
            return
        each = api.get("customer_order", drafts[0])["estimated_total"]
        fm.patch("customer", c, {"credit_limit": int(each * 1.5)})
        res = _race([(sm, "customer_order", d) for d in drafts])
        check(f"round {rnd}: two orders of 1x room each at once: never 500", all(st != 500 for st, _ in res), res)
        eq(f"round {rnd}: at most one of the two orders goes through without a credit approval", len(unapproved("customer_order", res)) <= 1, True)
        for (st, b), d in zip(res, drafts):
            if st == 422:
                check(f"round {rnd}: the refused order says the credit limit was just used", any("batas kredit" in m.lower() for m in messages(b)), b)
                st2, b2 = sm.verb("customer_order", d, "submit")
                pol = [a.get("policy") for a in approvals_of(api, "customer_order", (b2 or {}).get("name", d))]
                check(f"round {rnd}: submitted again it goes through only with the over_credit_limit approval", st2 in (200, 202) and "over_credit_limit" in pol, (st2, b2, pol))
    # deliveries at the door by head office (a salesman's orders are counted there)
    for rnd in range(3):
        c = customer(f"Toko Balapan Kirim {rnd}")
        orders = []
        for n in range(2):
            st, so = asep.create("customer_order", order_body(c, [(TEH, 5)], po_number=f"KIRIM-{rnd}-{n}"))
            st, so = asep.verb("customer_order", (so or {}).get("name", "x"), "submit")
            orders.append(so.get("name") if st == 200 else None)
        notes = []
        for so in orders:
            st, dn = hq.create("delivery_note", {"customer_order": so, "outlet": GUDANG, "items": [{"item": TEH, "qty": 5}]}) if so else (0, {})
            notes.append(dn.get("name") if st == 201 else None)
        if None in notes:
            fail("race: two delivery drafts", (orders, notes))
            return
        each = api.get("delivery_note", notes[0])["delivery_total"]
        fm.patch("customer", c, {"credit_limit": int(each * 1.5)})
        res = _race([(hq, "delivery_note", d) for d in notes])
        check(f"round {rnd}: two deliveries at once: never 500", all(st != 500 for st, _ in res), res)
        eq(f"round {rnd}: at most one of the two deliveries goes through without a credit approval", len(unapproved("delivery_note", res)) <= 1, True)
        for (st, b), d in zip(res, notes):
            if st == 422:
                check(f"round {rnd}: the refused delivery says the credit limit was just used", any("batas kredit" in m.lower() for m in messages(b)), b)
                st2, b2 = hq.verb("delivery_note", d, "submit")
                check(f"round {rnd}: submitted again the delivery is held for approval -> 202", st2 == 202, (st2, b2))


def scenario_grosir_invoice_prints(api):
    """A salesman prints his customer's invoice (A4, nota 80 mm, WhatsApp) with the cara bayar copy of that invoice only;
    the nota carries retention down to the net bill; a cancelled invoice no longer tells the customer to pay (audit pr-desain-e)."""
    got = _need("fm", "asep", "ujang")
    if not got:
        return
    fm, asep, ujang = got
    acct = None
    if not api.find("bank_account", "default_claim == 'default'"):
        st, acct = fm.create("bank_account", {"bank": "BCA", "account_no": "4440000004", "holder": "CV Contoh Niaga", "is_default": True,
                                               "qris_payload": qris_payload("CV CONTOH NIAGA", "ID1020000000401")})
        check("finance adds the default rekening + QRIS -> 201", st == 201, (st, acct))
    rate = api.get("item", MI)["rate"]
    # salesman is given: the field's default comes from the order or the customer, and a direct invoice has no order
    st, inv = fm.create("sales_invoice", {"customer": "CUST-0001", "salesman": ASEP, "posting_date": "2026-11-06", "due_date": "2026-11-20",
                                          "retention_percent": 5, "items": [{"item": MI, "qty": 10, "rate": rate}]})
    if not check("an invoice for Asep's customer with 5% retention -> 201", st == 201, (st, inv)):
        return
    name = inv["name"]
    st, resp = fm.verb("sales_invoice", name, "submit")
    if not check("the invoice is submitted -> 200", st == 200, (st, resp)):
        return
    name = (resp or {}).get("name") or name   # the invoice number is given at submit
    rec = api.get("sales_invoice", name)
    eq("the invoice is Asep's", rec.get("salesman"), ASEP)
    pis = api.find("payment_instruction", f"sales_invoice == '{name}'")
    eq("its cara bayar copy carries the salesman", (pis or [{}])[0].get("salesman"), ASEP)
    rp = lambda v: "Rp " + f"{int(v):,}".replace(",", ".")
    for fmt in ("", "sales_invoice_nota", "sales_invoice_whatsapp"):
        check_print(f"Asep prints {fmt or 'the A4 invoice'}", asep, "sales_invoice", name, fmt, [rp(rec["amount_due"]), name])
    check_print("the nota shows retention and the net bill", api, "sales_invoice", name, "sales_invoice_nota",
                ["Retensi 5% ditahan", "Tagihan bersih " + rp(rec["amount_due"]), "isi " + rp(rec["outstanding"])])
    theirs = {p["name"] for p in ujang.list("payment_instruction", limit=200)}
    check("Ujang does not see the cara bayar of Asep's invoice", not theirs & {p["name"] for p in pis}, sorted(theirs))
    st, resp = fm.verb("sales_invoice", name, "cancel")
    if check("the invoice is cancelled -> 200", st == 200, (st, resp)):
        check_print("a cancelled invoice", api, "sales_invoice", name, "", ["Batal"], ["Cara bayar", "Isi nominal", "Scan QRIS"])
        check_print("a cancelled nota", api, "sales_invoice", name, "sales_invoice_nota", ["BATAL"], ["Scan QRIS", "Berita transfer"])
    if acct and acct.get("name"):   # leave the site without a default account, as the other scenarios expect
        st, resp = fm.patch("bank_account", acct["name"], {"is_default": False})
        check("the test rekening is no longer the default -> 200", st == 200, (st, resp))


# Element order of the DJP sample "Sample Faktur PK Template v.1.4.xml" (pajak.go.id Coretax templates).
CORETAX_LINE = ["Opt", "Code", "Name", "Unit", "Price", "Qty", "TotalDiscount", "TaxBase", "OtherTaxBase", "VATRate", "VAT", "STLGRate", "STLG"]


def scenario_grosir_order_marks(api):
    """The tier-pricing flows rewrite an order's lines: the wages marks of a line stay (audit lanjutan 2 F2)."""
    a, b = [i["name"] for i in api.list("item", limit=2)]
    st, so = api.create("customer_order", {"customer": "CUST-0001", "po_number": "PO-MARK-1", "po_date": "2026-11-10",
                                           "items": [{"item": a, "qty": 1, "pph23_exclude": True, "ppn_exclude": True}, {"item": b, "qty": 2}]})
    if check("grosir: order with a marked line", st == 201, (st, so)):
        got = [(bool(x.get("pph23_exclude")), bool(x.get("ppn_exclude"))) for x in api.get("customer_order", so["name"])["items"]]
        eq("grosir: the tier pricing keeps the line marks", got, [(True, True), (False, False)])
        api.call("DELETE", f"/api/v1/r/customer_order/{Api.q(so['name'])}")


def scenario_grosir_coretax(api):
    """A PKP distributor reports its sales of the month to Coretax: every submitted invoice with PPN, goods (Opt A),
    the units' Coretax codes, the shops' NIK as buyer document; and the books carry the same PPN keluaran (21200) and
    PPN masukan (11500) as the invoices and the PKP suppliers' bills."""
    import xml.etree.ElementTree as ET
    from collections import defaultdict
    from decimal import Decimal
    invs = [i for i in api.find("sales_invoice", "docstatus == 1 && ppn > 0") if i.get("kind") != "retention"]
    if not check("grosir PKP: the scenarios submitted invoices with PPN", invs, invs):
        return
    by_month = defaultdict(list)
    for i in invs:
        by_month[i["posting_date"][:7]].append(i)
    seller = "9" + "0" * 13 + "55"   # a made-up NPWP
    uoms = {u["name"]: u.get("coretax_unit") for u in api.list("uom", limit=200)}
    for month, rows in sorted(by_month.items()):
        y, m = month.split("-")
        st, tp = api.create("tax_period", {"year": y, "month": m, "seller_tin": seller, "seller_nitku": seller + "000000"})
        if not check(f"grosir PKP: tax period {month}", st == 201, (st, tp)):
            continue
        st, body, ct = api.html(f"/api/v1/r/tax_period/{Api.q(tp['name'])}/print?format=coretax_faktur")
        if not check(f"grosir PKP: faktur XML of {month} downloads (no invoice refused)", st == 200, (st, body[:600])):
            continue
        root = ET.fromstring(body.encode())
        got = {t.findtext("RefDesc").split(";")[0]: t for t in root.findall("ListOfTaxInvoice/TaxInvoice")}
        eq(f"grosir PKP {month}: every invoice with PPN is in the file", sorted(got), sorted(i["name"] for i in rows))
        for i in rows:
            t, doc = got.get(i["name"]), api.get("sales_invoice", i["name"])
            if t is None:
                continue
            lines = t.findall("ListOfGoodService/GoodService")
            check(f"{i['name']}: code 04, buyer by NIK (National ID, 16 zeros as TIN)",
                  (t.findtext("TrxCode"), t.findtext("BuyerDocument"), t.findtext("BuyerTin"), t.findtext("BuyerDocumentNumber")) ==
                  ("04", "National ID", "0" * 16, doc.get("buyer_doc_no")) and len(doc.get("buyer_doc_no") or "") == 16, (t.findtext("TrxCode"), t.findtext("BuyerDocument")))
            check(f"{i['name']}: lines in the DJP order, goods (Opt A), the unit's Coretax code",
                  all([c.tag for c in g] == CORETAX_LINE and g.findtext("Opt") == "A" for g in lines)
                  and [g.findtext("Unit") for g in lines] == [uoms.get(x.get("uom")) or "UM.0033" for x in doc["items"]], [(g.findtext("Opt"), g.findtext("Unit")) for g in lines])
            eq(f"{i['name']}: the lines add up to the invoice DPP", sum(Decimal(g.findtext("TaxBase")) for g in lines), Decimal(doc["dpp"]))
            check(f"{i['name']}: faktur PPN within Rp1 of the booked PPN", abs(sum(Decimal(g.findtext("VAT")) for g in lines) - Decimal(doc["ppn"])) <= 1,
                  (sum(Decimal(g.findtext("VAT")) for g in lines), doc["ppn"]))
    if not installed(api, "gl_entry"):
        return
    def gl(account, voucher_type):
        total, offset = 0, 0
        while True:
            page = api.list("gl_entry", filter=f"account == '{account}' && voucher_type == '{voucher_type}'", limit=500, offset=offset)
            total += sum(r["amount"] for r in page)
            if len(page) < 500:
                return total
            offset += 500
    eq("books: PPN keluaran (21200) = the PPN of the submitted invoices", -gl("21200", "sales_invoice"), sum(i["ppn"] for i in invs))
    bills = api.find("vendor_bill", "docstatus == 1")
    eq("books: PPN masukan (11500) = the PPN of the submitted bills of PKP suppliers", gl("11500", "vendor_bill"), sum(b.get("ppn") or 0 for b in bills))


def scenario_grosir_staff_home(api):
    """Card t_19f909b9: staff do not land on the owner's money dashboards. A warehouse member (outlet_staff, outlet
    GUDANG) and a salesman each get their own home dashboard (contract 0030 §3: the first dashboard whose roles names one
    of their roles), whose widgets all compute for them; grosir_overview and grosir_ar answer 403 to both and are not in
    their /meta, while the sales manager still opens them."""
    got = _need("asep", "sm")
    if not got:
        return
    asep, sm = got
    k, org = CTX.get("kernel"), CTX.get("org")
    email = "gudang@contohniaga.test"
    code, out = k.user_add(email, "outlet_staff", org, "Gudang Contoh", outlets=GUDANG, password=CTX["member_pw"])
    staff = Api.login(k.base, email, CTX["member_pw"]) if code == 0 else None
    if not check("a warehouse member (outlet_staff, outlet GUDANG) signs in", staff is not None, out):
        return
    for who, cl, home, other in (("warehouse member", staff, "gudang_harian", "salesman_harian"), ("salesman", asep, "salesman_harian", "gudang_harian")):
        st, m = cl.call("GET", "/api/v1/meta")
        dashes = set((m or {}).get("dashboards") or {})
        check(f"{who}: /meta offers {home}, not the owner's money dashboards", home in dashes and not dashes & {"grosir_overview", "grosir_ar"}, sorted(dashes))
        check(f"{who}: /meta does not offer {other}", other not in dashes, sorted(dashes))
        for d in ("grosir_overview", "grosir_ar"):
            st, resp = cl.call("GET", f"/api/v1/dashboards/{d}")
            check(f"{who}: {d} -> 403", st == 403, (st, resp))
        dashboard(cl, home)   # 200 and every widget computed: no widget over a model the role may not read
    for d in ("grosir_overview", "grosir_ar"):
        st, resp = sm.call("GET", f"/api/v1/dashboards/{d}")
        check(f"the sales manager still opens {d}", st == 200, (st, resp))
    ws = dashboard(asep, "salesman_harian")
    st, mine = asep.call("GET", "/api/v1/r/sales_invoice?limit=500")
    own = sum(float(i.get("outstanding") or 0) for i in (mine or {}).get("data", []) if i.get("docstatus") == 1)
    check("the salesman's AR on his home is his customers' only", abs(float(ws.get("my_ar", {}).get("value") or 0) - own) < 1, (ws.get("my_ar"), own))


SCENARIOS = [scenario_grosir_fixtures, scenario_grosir_tier_price, scenario_grosir_salesman_scope, scenario_grosir_credit_hold,
             scenario_grosir_giro, scenario_grosir_ar_by_salesman, scenario_grosir_delivery, scenario_grosir_credit_at_delivery, scenario_grosir_credit_race, scenario_grosir_return,
             scenario_grosir_invoice_prints, scenario_dashboards_grosir, scenario_grosir_staff_home, scenario_grosir_order_marks, scenario_grosir_coretax]
