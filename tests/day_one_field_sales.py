"""Day-one scenarios of field_sales (rangka-apps#112) on demo-grosir: the salesman's GPS visit check-in (sales_visit), the
van sale (canvas_sale: stock out of the vehicle location, draft invoice and cash payment, credit approvals, cancel
guard) and the van's end-of-day cash settlement (canvas_settlement).

Loaded by tests/day_one.py (bind() hands over its helpers). The customers are made here, as the sales manager, so the
scenarios do not depend on what the other grosir scenarios did to the fixture customers: every one is Asep's, wholesale,
with a shop point in Bandung and the default 100 m radius.
"""
import day_one_grosir as G

H = {}


def bind(m):
    for k in ("ok", "fail", "gap", "check", "eq", "CTX", "Api", "denied", "approvals_of", "kernel_today"):
        H[k] = getattr(m, k)
    globals().update(H)


POINT = {"lat": -6.9147, "lng": 107.6098}        # synthetic shop point
S = {}                                            # state shared by the scenarios, in order


def _need(*keys):
    return G._need(*keys)


def _customer(sm, name, limit=25_000_000, point=True, salesman=G.ASEP):
    body = {"customer_name": name, "salesman": salesman, "price_tier": "wholesale", "credit_limit": limit, "route_day": "daily"}
    if point:
        body["location"] = dict(POINT)
    st, c = sm.create("customer", body)
    if st != 201:
        fail(f"setup: customer {name}", (st, c))
        return None
    return c["name"]


def _van_qty(api, item, outlet=G.KANVAS):
    return round(sum(b["qty"] for b in api.find("stock_balance", f"outlet == '{outlet}' && item == '{item}'")), 3)


def _drafts(api):
    """Run the scheduled flow canvas_sale_drafts by hand (the scheduler runs it every five minutes, as the system)."""
    st, out = api.call("POST", "/api/v1/flows/canvas_sale_drafts/run", {"event": {}})
    check("the drafts flow runs -> 200", st == 200, (st, out))


def _pending(cl, model, name):
    return [a for a in approvals_of(cl, model, name) if a.get("state") == "pending"]


def scenario_field_sales_visit_inside_radius(api):
    """A check-in at the shop's point is inside the radius: it submits at once and has no approval."""
    got = _need("asep", "sm")
    if not got:
        return
    asep, sm = got
    S["c1"] = _customer(sm, "Toko Kanvas Satu")
    S["c2"] = _customer(sm, "Toko Kanvas Dua")
    S["c3"] = _customer(sm, "Toko Kanvas Tanpa Titik", point=False)
    if not (S["c1"] and S["c2"] and S["c3"]):
        return
    cust = api.get("customer", S["c1"])
    eq("a new customer has the default 100 m visit radius", cust.get("visit_radius_m"), 100)
    check("customer.route_today is true for a daily route", cust.get("route_today") is True, cust.get("route_today"))
    near = {"lat": POINT["lat"] + 0.0002, "lng": POINT["lng"]}     # about 22 m north
    st, v = asep.create("sales_visit", {"customer": S["c1"], "pos": near})
    if not check("salesman creates a visit at his own customer -> 201", st == 201, (st, v)):
        return
    st, resp = asep.verb("sales_visit", v["name"], "submit")
    check("a visit inside the radius submits at once -> 200", st == 200 and resp.get("docstatus") == 1, (st, resp))
    v = api.get("sales_visit", resp["name"])
    eq("visit is not outside the radius", bool(v.get("outside_radius")), False)
    check("distance is measured by the kernel (about 22 m)", 15 <= v.get("distance_m", -1) <= 30, v.get("distance_m"))
    eq("the visit day is today in the org time zone (written by the flow)", v.get("visit_day"), str(kernel_today()))
    eq("the visit belongs to the salesman", v.get("salesman"), G.ASEP)
    eq("an inside visit has no approval request", _pending(asep, "sales_visit", v["name"]), [])
    S["visit1"] = v["name"]


def scenario_field_sales_visit_outside_radius_needs_approval(api):
    """A check-in far from the shop (or at a customer with no point) waits for head office or the outlet supervisor."""
    got = _need("asep", "hq", "sm")
    if not got or not S.get("c2"):
        return
    asep, hq, sm = got
    far = {"lat": POINT["lat"] + 0.01, "lng": POINT["lng"]}         # about 1.1 km north
    st, v = asep.create("sales_visit", {"customer": S["c2"], "pos": far})
    if not check("outside visit is created as a draft -> 201", st == 201, (st, v)):
        return
    rec = api.get("sales_visit", v["name"])
    eq("the visit is marked outside the radius", bool(rec.get("outside_radius")), True)
    check("distance is about 1.1 km", 1000 <= rec.get("distance_m", 0) <= 1250, rec.get("distance_m"))
    st, resp = asep.verb("sales_visit", v["name"], "submit")
    pol = [a.get("policy") for a in (resp or {}).get("approvals", [])] if isinstance(resp, dict) else []
    check("outside visit is held -> 202 (policy outside_radius)", st == 202 and "outside_radius" in pol, (st, pol, resp))
    eq("the held visit is still a draft", api.get("sales_visit", v["name"]).get("docstatus"), 0)
    ap = (resp or {}).get("approval", {}).get("id", "x")
    st, resp = asep.call("POST", f"/api/v1/approvals/{ap}/approve", {})
    check("the salesman cannot approve his own visit -> 403", st == 403, (st, resp))
    r = G.approve_all(hq, "sales_visit", v["name"])
    rec = ((r or (0, {}))[1] or {}).get("record") or {}
    check("head office approves -> the visit is submitted", bool(r) and r[0] == 200 and rec.get("docstatus") == 1, r)
    # a customer with no point yet counts as far: unknown is never inside
    st, v2 = asep.create("sales_visit", {"customer": S["c3"], "pos": POINT})
    rec2 = api.get("sales_visit", (v2 or {}).get("name", "x")) or {}
    eq("a customer with no shop point is outside (needs approval)", bool(rec2.get("outside_radius")), True)
    # a visit to another salesman's customer is refused
    st, resp = asep.create("sales_visit", {"customer": "CUST-0004", "pos": POINT})
    check("a visit to another salesman's customer is refused", st in (403, 404, 422), (st, resp))


def scenario_field_sales_visit_duplicate_same_day_rejected(api):
    """One visit per customer, salesman and day: the second check-in is refused and leaves no record."""
    got = _need("asep")
    if not got or not S.get("visit1"):
        return
    asep = got[0]
    before = len(api.find("sales_visit", f"customer == '{S['c1']}'"))
    st, v = asep.create("sales_visit", {"customer": S["c1"], "pos": POINT})
    check("a second visit of the same customer on the same day is refused", st in (409, 422), (st, v))
    eq("the refused check-in left no record", len(api.find("sales_visit", f"customer == '{S['c1']}'")), before)
    # a cancelled visit frees the day only through an amendment (amended_from is part of the key)
    st, c = api.verb("sales_visit", S["visit1"], "cancel")
    check("head office or the owner cancels the first visit -> 200", st == 200, (st, c))
    st, v = asep.create("sales_visit", {"customer": S["c1"], "pos": POINT})
    check("after the cancel the same salesman cannot simply start over (key still held) -> refused", st in (409, 422), (st, v))
    st, am = api.verb("sales_visit", S["visit1"], "amend")
    check("the cancelled visit is amended into a new draft -> 200/201", st in (200, 201), (st, am))


def scenario_field_sales_canvas_sale_moves_stock_out_of_van_only(api):
    """Submitting a canvas sale takes the goods out of the van and nowhere else; the invoice drafted from it moves nothing."""
    got = _need("asep", "fm")
    if not got or not S.get("c1"):
        return
    asep, fm = got
    van0 = {i: _van_qty(api, i) for i in (G.MI, G.TEH)}
    wh0 = {i: _van_qty(api, i, G.GUDANG) for i in (G.MI, G.TEH)}
    moves0 = len(api.find("stock_move", "true"))
    rates = {i: api.get("item", i) for i in (G.MI, G.TEH)}
    st, cs = asep.create("canvas_sale", {"customer": S["c1"], "outlet": G.KANVAS, "cash_received": 20_000,
                                         "items": [{"item": G.MI, "qty": 2}, {"item": G.TEH, "qty": 1}]})
    if not check("salesman drafts a canvas sale from the van -> 201", st == 201, (st, cs)):
        return
    cs = api.get("canvas_sale", cs["name"])
    got_rates = {r["item"]: r["rate"] for r in cs["items"]}
    eq("the tier price is set on save (wholesale: rate_wholesale, else the base rate)", got_rates[G.TEH],
       rates[G.TEH]["rate_wholesale"] or rates[G.TEH]["rate"])
    st, resp = asep.verb("canvas_sale", cs["name"], "submit")
    if not check("a sale inside the credit limit submits at once -> 200", st == 200 and resp.get("docstatus") == 1, (st, resp)):
        return
    cs = api.get("canvas_sale", resp["name"])
    eq("van stock of MI down by 2", round(van0[G.MI] - _van_qty(api, G.MI), 3), 2)
    eq("van stock of TEH down by 1", round(van0[G.TEH] - _van_qty(api, G.TEH), 3), 1)
    eq("warehouse stock is untouched", {i: _van_qty(api, i, G.GUDANG) for i in (G.MI, G.TEH)}, wh0)
    moves = api.find("stock_move", f"voucher == '{cs['name']}'")
    eq("the sale wrote one stock move per line, all out of the van", sorted((m["outlet"], m["item"]) for m in moves), sorted([(G.KANVAS, G.MI), (G.KANVAS, G.TEH)]))
    check("the sale records its cost of goods for staff", api.get("canvas_sale", cs["name"]).get("cogs_total", 0) > 0)
    eq("no invoice exists before the drafts flow has run", api.find("sales_invoice", f"notes.contains('[{cs['name']}]')"), [])
    _drafts(api)
    cs = api.get("canvas_sale", cs["name"])
    invs = api.find("sales_invoice", f"notes.contains('[{cs['name']}]')")
    inv = api.get("sales_invoice", invs[0]["name"]) if invs else {}
    check("a draft invoice was made for the customer with the sale's lines", inv.get("docstatus") == 0 and inv.get("customer") == S["c1"]
          and sorted((r["item"], r["qty"], r["rate"]) for r in inv.get("items", [])) == sorted((r["item"], r["qty"], r["rate"]) for r in cs["items"]), inv)
    eq("the invoice total equals the sale total", inv.get("grand_total"), cs["grand_total"])
    pays = api.find("payment", f"notes.contains('[{cs['name']}]')")
    pay = api.get("payment", pays[0]["name"]) if pays else {}
    check("a draft cash payment of the cash taken was made", pay.get("docstatus") == 0 and pay.get("amount") == 20_000 and pay.get("method") == "cash", pay)
    # finance submits the invoice and the payment: the stock stays as it was (an invoice never moves stock)
    st, resp = fm.verb("sales_invoice", inv["name"], "submit")
    check("finance submits the invoice -> 200", st == 200, (st, resp))
    inv = dict(inv, name=(resp or {}).get("name", inv["name"]))
    eq("submitting the invoice moves no stock", (_van_qty(api, G.MI), _van_qty(api, G.TEH)), (van0[G.MI] - 2, van0[G.TEH] - 1))
    eq("only the sale's moves exist (invoice added none)", len(api.find("stock_move", "true")) - moves0, 2)
    S["sale1"], S["inv1"], S["pay1"] = cs["name"], inv["name"], pay["name"]
    # not from a warehouse
    st, resp = asep.create("canvas_sale", {"customer": S["c1"], "outlet": G.GUDANG, "items": [{"item": G.MI, "qty": 1}]})
    check("a canvas sale from a warehouse is refused -> 422", st == 422, (st, resp))
    # not to another salesman's customer
    st, resp = asep.create("canvas_sale", {"customer": "CUST-0004", "outlet": G.KANVAS, "items": [{"item": G.MI, "qty": 1}]})
    check("a canvas sale to another salesman's customer is refused", st in (403, 404, 422), (st, resp))


def scenario_field_sales_canvas_sale_over_credit_limit_waits(api):
    """What the customer leaves unpaid counts against the limit: over it, the sale waits for the sales manager; cash is never held."""
    got = _need("asep", "sm")
    if not got:
        return
    asep, sm = got
    tight = _customer(sm, "Toko Kanvas Batas Kecil", limit=1_000)
    if not tight:
        return
    S["tight"] = tight
    van0 = _van_qty(api, G.MI)
    st, cs = asep.create("canvas_sale", {"customer": tight, "outlet": G.KANVAS, "items": [{"item": G.MI, "qty": 1}]})
    if not check("draft sale over the limit -> 201", st == 201, (st, cs)):
        return
    st, resp = asep.verb("canvas_sale", cs["name"], "submit")
    pol = [a.get("policy") for a in (resp or {}).get("approvals", [])] if isinstance(resp, dict) else []
    check("the sale over the credit limit is held -> 202 (policy over_credit_limit)", st == 202 and "over_credit_limit" in pol, (st, pol, resp))
    eq("held sale stays a draft", api.get("canvas_sale", cs["name"]).get("docstatus"), 0)
    eq("held sale has not moved the van's stock", _van_qty(api, G.MI), van0)
    ap = (resp or {}).get("approval", {}).get("id", "x")
    st, resp = asep.call("POST", f"/api/v1/approvals/{ap}/approve", {})
    check("the salesman cannot release his own held sale -> 403", st == 403, (st, resp))
    r = G.approve_all(sm, "canvas_sale", cs["name"])
    rec = ((r or (0, {}))[1] or {}).get("record") or {}
    check("sales manager approves -> the sale is submitted", bool(r) and r[0] == 200 and rec.get("docstatus") == 1, r)
    eq("after the approval the van's stock is down by 1", round(van0 - _van_qty(api, G.MI), 3), 1)
    S["sale_tight"] = rec.get("name", cs["name"])
    # the same customer paying in cash is not held
    total = api.get("item", G.MI)["rate_wholesale"] or api.get("item", G.MI)["rate"]
    st, cs2 = asep.create("canvas_sale", {"customer": tight, "outlet": G.KANVAS, "items": [{"item": G.MI, "qty": 1}]})
    cs2 = api.get("canvas_sale", cs2["name"])
    asep.patch("canvas_sale", cs2["name"], {"cash_received": cs2["grand_total"]})
    st, resp = asep.verb("canvas_sale", cs2["name"], "submit")
    check("a sale paid in full in cash is never held -> 200", st == 200 and resp.get("docstatus") == 1, (st, resp))
    S["sale_cash"] = (resp or {}).get("name", cs2["name"])


def scenario_field_sales_cancel_after_invoice_paid_refused(api):
    """Cancel is refused once finance submitted the invoice, most of all once it is paid; an untouched sale cancels and the stock returns."""
    got = _need("asep", "sm", "fm")
    if not got or not S.get("sale1"):
        return
    asep, sm, fm = got
    st, resp = sm.verb("canvas_sale", S["sale1"], "cancel")
    check("cancel with a submitted (unpaid) invoice is refused -> 422", st == 422 and "diajukan" in str(resp), (st, resp))
    inv = api.get("sales_invoice", S["inv1"])
    st, pay = fm.create("payment", {"direction": "receive", "customer": S["c1"], "method": "cash", "amount": 20_000,
                                    "allocations": [{"sales_invoice": inv["name"], "amount": 20_000}]})
    if not check("finance records the cash against the invoice -> 201", st == 201, (st, pay)):
        return
    st, resp = fm.verb("payment", pay["name"], "submit")
    check("finance submits the payment -> 200", st == 200, (st, resp))
    eq("the invoice is partly paid", api.get("sales_invoice", inv["name"]).get("paid_amount"), 20_000)
    van = _van_qty(api, G.MI)
    st, resp = sm.verb("canvas_sale", S["sale1"], "cancel")
    check("cancel after the invoice was paid is refused -> 422", st == 422 and "dibayar" in str(resp), (st, resp))
    eq("the refused cancel left the sale submitted", api.get("canvas_sale", S["sale1"]).get("docstatus"), 1)
    eq("and the van's stock where it was", _van_qty(api, G.MI), van)
    st, resp = asep.verb("canvas_sale", S["sale1"], "cancel")
    check("the salesman cannot cancel a canvas sale -> 403", denied(st, resp), (st, resp))
    # a sale cancelled before the drafts flow ran gets no invoice and no payment
    st, early = asep.create("canvas_sale", {"customer": S["c2"], "outlet": G.KANVAS, "cash_received": 500, "items": [{"item": G.MI, "qty": 1}]})
    st, resp = asep.verb("canvas_sale", early["name"], "submit")
    early_name = (resp or {}).get("name", early["name"])
    van = _van_qty(api, G.MI)
    st, resp = sm.verb("canvas_sale", early_name, "cancel")
    check("a sale cancelled before its drafts exist cancels -> 200", st == 200, (st, resp))
    eq("the van's stock returns", round(_van_qty(api, G.MI) - van, 3), 1)
    n_inv = len(api.find("sales_invoice", f"notes.contains('[{early_name}]')"))
    _drafts(api)
    eq("a cancelled sale gets no draft invoice from the flow", len(api.find("sales_invoice", f"notes.contains('[{early_name}]')")), n_inv)
    # a sale finance never touched cancels after its drafts exist: the stock returns and the drafts are marked
    st, cs = asep.create("canvas_sale", {"customer": S["c2"], "outlet": G.KANVAS, "cash_received": 1_000, "items": [{"item": G.MI, "qty": 1}]})
    st, resp = asep.verb("canvas_sale", cs["name"], "submit")
    _drafts(api)
    cs = api.get("canvas_sale", (resp or {}).get("name", cs["name"]))
    van = _van_qty(api, G.MI)
    st, resp = sm.verb("canvas_sale", cs["name"], "cancel")
    check("a sale with only draft invoice and payment cancels -> 200", st == 200, (st, resp))
    eq("the van's stock returns", round(_van_qty(api, G.MI) - van, 3), 1)
    tag = f"notes.contains('[{cs['name']}]')"
    check("the draft invoice is marked to delete", all("hapus draf" in (r.get("notes") or "") for r in api.find("sales_invoice", tag)) and api.find("sales_invoice", tag))
    check("the draft payment is marked to delete", all("hapus draf" in (r.get("notes") or "") for r in api.find("payment", tag)) and api.find("payment", tag))


def scenario_field_sales_settlement_shortage_recorded(api):
    """The van's cash handed in is counted against the day's cash sales; a shortage is recorded, explained, and closes the day."""
    got = _need("asep", "fm", "sm")
    if not got or not S.get("sale1"):
        return
    asep, fm, sm = got
    today = str(kernel_today())
    sales = api.find("canvas_sale", f"docstatus == 1 && salesman == '{G.ASEP}' && outlet == '{G.KANVAS}' && sale_date == '{today}'")
    expected = sum(s["cash_received"] for s in sales)
    check("the day has cash sales to settle", expected > 0, expected)
    st, stl = fm.create("canvas_settlement", {"salesman": G.ASEP, "outlet": G.KANVAS, "settle_date": today, "cash_counted": expected - 5_000})
    if not check("finance drafts the settlement -> 201", st == 201, (st, stl)):
        return
    stl = api.get("canvas_settlement", stl["name"])
    eq("the draft expects the day's cash sales", stl.get("cash_expected"), expected)
    eq("the sales total is the day's sales (with PPN)", stl.get("sales_total"), sum(s["grand_total"] for s in sales))
    eq("the shortage is 5,000", (stl.get("difference"), stl.get("shortage"), stl.get("overage")), (-5_000, 5_000, 0))
    st, resp = fm.verb("canvas_settlement", stl["name"], "submit")
    check("a difference without a note is refused -> 422", st == 422, (st, resp))
    fm.patch("canvas_settlement", stl["name"], {"notes": "Kurang 5.000: uang kembalian pelanggan belum dicatat"})
    st, resp = fm.verb("canvas_settlement", stl["name"], "submit")
    check("with the note the settlement submits -> 200 (a shortage is recorded, not refused)", st == 200 and resp.get("docstatus") == 1, (st, resp))
    stl = api.get("canvas_settlement", (resp or {}).get("name", stl["name"]))
    eq("the submitted settlement keeps the shortage", (stl.get("cash_expected"), stl.get("cash_counted"), stl.get("shortage")), (expected, expected - 5_000, 5_000))
    st, dup = fm.create("canvas_settlement", {"salesman": G.ASEP, "outlet": G.KANVAS, "settle_date": today, "cash_counted": expected})
    check("a second settlement of the same van, salesman and day is refused", st in (409, 422), (st, dup))
    # the day is closed
    st, cs = asep.create("canvas_sale", {"customer": S["c2"], "outlet": G.KANVAS, "items": [{"item": G.MI, "qty": 1}]})
    van = _van_qty(api, G.MI)
    st, resp = asep.verb("canvas_sale", (cs or {}).get("name", "x"), "submit")
    check("a canvas sale after the day was settled is refused -> 422", st == 422 and "disetor" in str(resp), (st, resp))
    eq("and the van's stock did not move", _van_qty(api, G.MI), van)
    st, resp = sm.verb("canvas_sale", S["sale_cash"], "cancel")
    check("a cancel of a settled day's sale is refused -> 422", st == 422 and "disetor" in str(resp), (st, resp))
    # a staff member who counts an overage is recorded the same way (new van day: yesterday has no sales, so expected is 0)
    # field_sales reads its own settlement but cannot create one
    st, resp = asep.create("canvas_settlement", {"salesman": G.ASEP, "outlet": G.KANVAS, "cash_counted": 1})
    check("a salesman cannot settle his own van -> 403", denied(st, resp), (st, resp))


def scenario_field_sales_forged_work_order_and_stale_date_refused(api):
    """A salesman can create a work order but it never decides the invoice; a sale cannot be dated far in the past."""
    got = _need("asep", "sm")
    if not got or not S.get("c2"):
        return
    asep, sm = got
    st, cs = asep.create("canvas_sale", {"customer": S["c2"], "outlet": G.KANVAS, "items": [{"item": G.MI, "qty": 1}]})
    if not check("salesman drafts a plain credit sale -> 201", st == 201, (st, cs)):
        return
    st, resp = asep.verb("canvas_sale", cs["name"], "submit")
    if not check("it submits -> 200", st == 200, (st, resp)):
        return
    name = (resp or {}).get("name", cs["name"])
    st, job = asep.create("canvas_sale_job", {"sale": name, "customer": S["c2"], "salesman": G.ASEP, "sale_date": "2020-01-01", "cash_received": 0,
                                              "items": [{"item": G.MI, "qty": 1, "rate": 1}]})
    check("the salesman can create a work order -> 201 (that is why it is verified)", st == 201, (st, job))
    _drafts(api)
    invs = api.find("sales_invoice", f"po_number == '{name}'")
    eq("exactly one invoice for the sale", len(invs), 1)
    inv = api.get("sales_invoice", invs[0]["name"]) if invs else {}
    eq("the invoice is the real sale's total, not the forged one", inv.get("grand_total"), api.get("canvas_sale", name).get("grand_total"))
    eq("the invoice is dated the sale's day, not the forged date", inv.get("posting_date"), api.get("canvas_sale", name).get("sale_date"))
    check("the customer's uninvoiced canvas credit counts the draft invoice (the credit limit sees it)", (api.get("customer", S["c2"]).get("uninvoiced_canvas") or 0) >= inv.get("grand_total", 1), api.get("customer", S["c2"]).get("uninvoiced_canvas"))
    # same totals, different lines: still ignored
    st, cs3 = asep.create("canvas_sale", {"customer": S["c2"], "outlet": G.KANVAS, "items": [{"item": G.MI, "qty": 3}]})
    st, r3 = asep.verb("canvas_sale", (cs3 or {}).get("name", "x"), "submit")
    n3 = (r3 or {}).get("name", "x")
    sale3 = api.get("canvas_sale", n3)
    if sale3:
        asep.create("canvas_sale_job", {"sale": n3, "customer": S["c2"], "salesman": G.ASEP, "sale_date": sale3["sale_date"], "cash_received": 0,
                                        "items": [{"item": G.MI, "qty": 1, "rate": sale3["subtotal"]}, {"item": G.TEH, "qty": 2, "rate": 0}]})
        _drafts(api)
        i3 = api.find("sales_invoice", f"po_number == '{n3}'")
        lines = sorted((r["item"], r["qty"]) for r in api.get("sales_invoice", i3[0]["name"]).get("items", [])) if i3 else []
        eq("a forged work order with the same totals but other lines does not change the invoice lines", lines, [(G.MI, 3)])
    st, resp = asep.create("canvas_sale", {"customer": S["c2"], "outlet": G.KANVAS, "sale_date": "2020-01-01", "items": [{"item": G.MI, "qty": 1}]})
    check("a sale dated years ago is refused -> 422", st == 422, (st, resp))


SCENARIOS = [scenario_field_sales_visit_inside_radius, scenario_field_sales_visit_outside_radius_needs_approval,
             scenario_field_sales_visit_duplicate_same_day_rejected, scenario_field_sales_canvas_sale_moves_stock_out_of_van_only,
             scenario_field_sales_canvas_sale_over_credit_limit_waits, scenario_field_sales_forged_work_order_and_stale_date_refused,
             scenario_field_sales_cancel_after_invoice_paid_refused,
             scenario_field_sales_settlement_shortage_recorded]
