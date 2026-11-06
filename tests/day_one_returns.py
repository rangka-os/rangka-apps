"""Day-one scenarios of cashier refunds that go back into stock (Retur ke stok, contract 0022), for demo-ritel and demo-fnb.

Loaded by tests/day_one.py (bind() hands over its helpers). A submitted outlet_sales with refund lines (qty < 0) gets a
Retur di kasir (pos_refund); a Retur ke stok (outlet_return) brings refunded goods back, each line through the item's
recipe (an item sold as is comes back as itself, a cancelled menu order brings back its materials) at the cost the sale
took them out. The scenarios run last in their site, after the sales scenarios made the refunds.
"""

H = {}
EPS = 0.0005


def bind(m):
    """Take the helpers of tests/day_one.py."""
    for k in ("ok", "fail", "gap", "check", "eq", "CTX", "Api", "messages", "balance", "moves_of", "rows_all"):
        H[k] = getattr(m, k)
    globals().update(H)


def refused(st, resp, text):
    return st in (409, 422) and any(text in m for m in messages(resp))


def source_unit_cost(api, sales, item):
    """round(sum |value| / sum |qty|) of the sale's own moves of item (contract 0021 decision 4), or None."""
    mv = [m for m in moves_of(api, sales) if m["item"] == item and m["reason"] != "reversal"]
    q = sum(abs(m["qty"]) for m in mv)
    if q <= EPS:
        return None
    v = sum(abs(m["value"]) for m in mv)
    return int(v / q + 0.5)


def qty_of(bal):
    return bal["qty"] if bal else 0


def returned(api, refund, item):
    r = api.get("pos_refund", refund)
    return sum(b["returned_qty"] for b in r["items"] if b["item"] == item), sum(b["refunded_qty"] for b in r["items"] if b["item"] == item)


def scenario_ritel_returns(api):
    CBR, CLY = "OUT-01", "OUT-02"
    hq, staff = CTX.get("hq") or api, CTX.get("staff_cly")
    sales = rows_all(api, "outlet_sales", "docstatus == 1")
    refunds = rows_all(api, "pos_refund")
    eq("every submitted sale with refunds has one Retur di kasir", sorted(r["sales"] for r in refunds), sorted(s["name"] for s in sales if s["refund_total"]))
    cly = [r for r in refunds if r["outlet"] == CLY and r["open_qty"] > EPS]
    if not check("Cileunyi has refunds waiting to come back", cly, len(cly)) or staff is None:
        return
    rf = api.get("pos_refund", cly[0]["name"])
    row = rf["items"][0]
    item, refunded = row["item"], returned(api, rf["name"], row["item"])[1]
    eq("Retur di kasir: refunded qty from the sale's refund lines, nothing returned yet", returned(api, rf["name"], item), (0, refunded))
    # too much, an item that was not refunded, an outlet that is not the staff's own
    st, resp = staff.create("outlet_return", {"refund": rf["name"], "items": [{"item": item, "qty": refunded + 1}]})
    check("returning more than was refunded -> 422 'melebihi'", refused(st, resp, "melebihi"), (st, resp))
    other = next((i["name"] for i in rows_all(api, "item", "kind == 'good'") if i["name"] not in {b["item"] for b in rf["items"]}), None)
    if other:
        st, resp = staff.create("outlet_return", {"refund": rf["name"], "items": [{"item": other, "qty": 1}]})
        check("an item that was not refunded -> 422", refused(st, resp, "tidak diretur di kasir"), (st, resp))
    cbr = next((r for r in refunds if r["outlet"] == CBR), None)
    if cbr:
        st, resp = staff.create("outlet_return", {"refund": cbr["name"], "items": [{"item": api.get("pos_refund", cbr["name"])["items"][0]["item"], "qty": 1}]})
        check("Cileunyi staff returning a Cibiru refund -> refused (403 or 404, outlet scope)", st in (403, 404), (st, resp))
    # one unit back on the shelf, by the outlet's own staff, at the cost the sale took it out
    before = qty_of(balance(api, CLY, item))
    st, ret = staff.create("outlet_return", {"refund": rf["name"], "items": [{"item": item, "qty": 1}]})
    if not check("Cileunyi staff makes a Retur ke stok (201; outlet and sale from the refund)", st == 201 and ret.get("outlet") == CLY and ret.get("sales") == rf["sales"], (st, ret)):
        return
    st, resp = staff.verb("outlet_return", ret["name"], "submit")
    if not check("Cileunyi staff submits it", st == 200, (st, resp)):
        return
    ret["name"] = resp.get("name", ret["name"])   # a draft gets its final number on submit
    mv = moves_of(api, ret["name"])
    unit = source_unit_cost(api, rf["sales"], item)
    check("one move in: the item itself, qty 1, reason return_in", len(mv) == 1 and mv[0]["item"] == item and abs(mv[0]["qty"] - 1) < EPS and mv[0]["reason"] == "return_in", mv)
    if unit is not None and mv:
        eq("at the unit cost the sale took it out (cost source)", mv[0]["unit_cost"], unit)
    eqv = qty_of(balance(api, CLY, item))
    check("Cileunyi stock of the item +1", abs(eqv - before - 1) < EPS, (before, eqv))
    eq("Retur di kasir: 1 returned", returned(api, rf["name"], item)[0], 1)
    # the rest, then one more: the second is refused at submit and moves nothing
    rest = refunded - 1
    if rest > EPS:
        st, r2 = staff.create("outlet_return", {"refund": rf["name"], "items": [{"item": item, "qty": rest}]})
        st2, r3 = staff.create("outlet_return", {"refund": rf["name"], "items": [{"item": item, "qty": 1}]})
        if check("two drafts, each within what is open", st == 201 and st2 == 201, (st, r2, st2, r3)):
            st, resp = staff.verb("outlet_return", r2["name"], "submit")
            check("the first submits", st == 200, (st, resp))
            r2["name"] = (resp or {}).get("name", r2["name"])
            mid = qty_of(balance(api, CLY, item))
            st, resp = staff.verb("outlet_return", r3["name"], "submit")
            check("the second would bring back more than was refunded -> refused", st in (409, 422) and "melebihi" in " ".join(messages(resp)), (st, resp))
            check("and moved nothing", abs(qty_of(balance(api, CLY, item)) - mid) < EPS, (mid, balance(api, CLY, item)))
            staff.call("DELETE", f"/api/v1/r/outlet_return/{Api.q(r3['name'])}")
            st, resp = hq.verb("outlet_return", r2["name"], "cancel")
            check("head office cancels the rest", st == 200, (st, resp))
    # the sale cannot be cancelled while its goods are back in stock; the return can, and its stock leaves again
    st, resp = hq.verb("outlet_sales", rf["sales"], "cancel")
    check("cancelling the sale of a live return -> refused 'retur ke stok yang masih aktif'", refused(st, resp, "retur ke stok yang masih aktif"), (st, resp))
    st, resp = staff.verb("outlet_return", ret["name"], "cancel")
    check("outlet staff cannot cancel a return (head office does)", st == 403, (st, resp))
    st, resp = hq.verb("outlet_return", ret["name"], "cancel")
    check("head office cancels the return", st == 200, (st, resp))
    check("Cileunyi stock back to before the return", abs(qty_of(balance(api, CLY, item)) - before) < EPS, (before, balance(api, CLY, item)))
    eq("Retur di kasir: nothing returned again", returned(api, rf["name"], item)[0], 0)


def scenario_fnb_returns(api):
    DAGO, ISI = "OUT-02", "BRG-0009"
    hq, dago, braga = CTX.get("hq") or api, CTX.get("staff_dago"), CTX.get("staff_braga")
    # the Moka refund sample of scenario_outlet_sales (a full refund of 3 Tahu isi sayur, "Cancelled Order") is a draft
    rec = next((s for s in rows_all(api, "outlet_sales", f"outlet == '{DAGO}' && source == 'moka' && docstatus == 0") if s["refund_total"] == 90_000), None)
    if not check("the Moka refund sample is waiting as a draft", rec, rec):
        return
    st, resp = api.verb("outlet_sales", rec["name"], "submit")
    if not check("submit the refund sample", st == 200, (st, resp)):
        return
    rec["name"] = resp.get("name", rec["name"])   # a draft gets its final number on submit
    rf = next(iter(rows_all(api, "pos_refund", f"sales == '{rec['name']}'")), None)
    if not check("it gets its Retur di kasir", rf, rf):
        return
    eq("Tahu isi sayur: 3 refunded", returned(api, rf["name"], ISI), (0, 3))
    recipe = next((r for r in rows_all(api, "recipe", f"product == '{ISI}'") if r.get("active")), None)
    if not check("Tahu isi sayur has an active recipe", recipe, recipe):
        return
    recipe = api.get("recipe", recipe["name"])
    y = recipe.get("yield_qty") or 1
    want = {}
    for l in recipe["lines"]:
        want[l["material"]] = want.get(l["material"], 0) + 3 * l["qty"] / y
    if braga:
        st, resp = braga.create("outlet_return", {"refund": rf["name"], "items": [{"item": ISI, "qty": 3}]})
        check("Braga staff returning a Dago refund -> refused (403 or 404, outlet scope)", st in (403, 404), (st, resp))
    cl = dago or hq
    before = {m: qty_of(balance(api, DAGO, m)) for m in want}
    menu_before = qty_of(balance(api, DAGO, ISI))
    st, ret = cl.create("outlet_return", {"refund": rf["name"], "items": [{"item": ISI, "qty": 3}], "notes": "Pesanan batal, belum dimasak"})
    if not check("Dago staff makes a Retur ke stok for the cancelled order", st == 201, (st, ret)):
        return
    st, resp = cl.verb("outlet_return", ret["name"], "submit")
    if not check("and submits it", st == 200, (st, resp)):
        return
    ret["name"] = resp.get("name", ret["name"])
    mv = moves_of(api, ret["name"])
    eq("the materials of the recipe come back, not the menu item", sorted(m["item"] for m in mv), sorted(want))
    for m in mv:
        check(f"{m['item']}: {round(want.get(m['item'], 0), 4)} back (3 portions x recipe)", abs(m["qty"] - want.get(m["item"], 0)) < 0.001 and m["reason"] == "return_in", m)
        unit = source_unit_cost(api, rec["name"], m["item"])
        if unit is not None:
            eq(f"{m['item']}: at the unit cost the sale took it out", m["unit_cost"], unit)
        after = qty_of(balance(api, DAGO, m["item"]))
        check(f"{m['item']}: Dago stock up by that", abs(after - before[m["item"]] - m["qty"]) < 0.001, (before[m["item"]], after))
    check("the menu item gets no stock", abs(qty_of(balance(api, DAGO, ISI)) - menu_before) < EPS, balance(api, DAGO, ISI))
    eq("Retur di kasir: all 3 back, nothing open for Tahu isi sayur", returned(api, rf["name"], ISI), (3, 3))
    st, resp = cl.create("outlet_return", {"refund": rf["name"], "items": [{"item": ISI, "qty": 1}]})
    check("a fourth portion -> 422 'melebihi'", refused(st, resp, "melebihi"), (st, resp))


SCENARIOS_RITEL = [scenario_ritel_returns]
SCENARIOS_FNB = [scenario_fnb_returns]
