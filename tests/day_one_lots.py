"""Day-one scenarios of lots, expiry, FEFO and serial numbers in the stock modules (rangka-os#260 slice b, kernel contract
0070): catalogue item.tracking, the registers stock_lot / stock_lot_balance / serial_no, goods receipt with lot numbers
and serials, a delivery note that picks earliest expiry first and skips expired lots, a sales return, a transfer that
carries its lots, a count per lot, an expired-stock adjustment, the expiry-soon digest, and a production batch that
consumes a material lot and makes a lot numbered after the batch.

Loaded by tests/day_one.py (bind() hands over its helpers). Items are created here, so the fixtures' items keep their
untracked history; on demo-produksi two fixture items switch tracking on after the factory scenarios ran (their stock
from before sits in the no-lot bucket, contract 0070 §2).
"""
import datetime

H = {}


_R = {"read"}
MATRIX = {
    # registers written by the stock hooks only (contract 0070 §2); head office may write a lot's notes
    "stock_lot": {"head_office": {"read", "write", "export", "print"}, "purchasing_manager": {"read", "export"}, "sales_manager": _R,
                  "field_sales": _R, "outlet_staff": _R, "warehouse_staff": _R, "cashier": _R},
    "stock_lot_balance": {"head_office": {"read", "export"}, "purchasing_manager": {"read", "export"}, "sales_manager": _R,
                          "outlet_staff": _R, "warehouse_staff": _R, "cashier": _R},
    "serial_no": {"head_office": {"read", "export"}, "purchasing_manager": {"read", "export"}, "sales_manager": _R,
                  "outlet_staff": _R, "warehouse_staff": _R, "cashier": _R},
    # the expiry-soon list, written by flow lot_expiry_digest only
    "stock_expiry": {"head_office": {"read", "export"}, "purchasing_manager": {"read", "export"}, "sales_manager": _R,
                     "outlet_staff": _R, "warehouse_staff": _R},
}


def bind(m):
    for k in ("ok", "fail", "gap", "check", "eq", "CTX", "Api", "denied", "kernel_today"):
        H[k] = getattr(m, k)
    globals().update(H)
    m.MATRIX.update(MATRIX)


def rec(x):
    return (x or {}).get("record", x) or {}


def code(resp):
    return ((resp or {}).get("error") or {}).get("code") or ""


def day(n):
    return (kernel_today() + datetime.timedelta(days=n)).isoformat()


def bal(api, outlet, item):
    rows = api.find("stock_balance", f"outlet == '{outlet}' && item == '{item}'")
    return (rows[0].get("qty") or 0) if rows else 0


def lot_name(api, item, no):
    rows = api.find("stock_lot", f"item == '{item}' && lot_no == '{no}'")
    return rows[0]["name"] if rows else ""


def lots(api, outlet, item):
    """{lot number or '' (the no-lot bucket): qty} of an outlet and item."""
    names = {r["name"]: r["lot_no"] for r in api.find("stock_lot", f"item == '{item}'")}
    return {names.get(r.get("lot") or "", ""): r.get("qty") or 0
            for r in api.find("stock_lot_balance", f"outlet == '{outlet}' && item == '{item}'")}


def serial_state(api, item, sn):
    rows = api.find("serial_no", f"item == '{item}' && serial == '{sn}'")
    return (rows[0].get("state"), rows[0].get("outlet")) if rows else (None, None)


def submit(cl, model, name):
    st, r = cl.verb(model, name, "submit")
    return st, r


def invariant(api, outlets, item, label):
    """Contract 0070 §2: the lot rows of an outlet and item always add up to its stock_balance.qty."""
    for o in outlets:
        got = round(sum(lots(api, o, item).values()), 6)
        eq(f"{label}: lots at {o} add up to the stock balance", got, round(bal(api, o, item), 6))


# ---------- demo-grosir: receipt, FEFO delivery, return, transfer, count, adjustment, digest, serials

GUDANG, KANVAS, RUSAK = "OUT-01", "OUT-02", "OUT-03"


def make_items(api):
    base = api.get("item", "BRG-0001")
    uom = base.get("stock_uom")
    st, plain = api.create("item", {"item_name": "Barang uji tanpa lot", "kind": "good", "stock_uom": uom, "rate": 1000})
    if check("lots: an untracked item -> 201", st == 201, (st, plain)):
        eq("a new item is not tracked by default", rec(plain).get("tracking"), "none")
    st, obat = api.create("item", {"item_name": "Sirup batuk 60 ml (uji lot)", "kind": "good", "stock_uom": uom, "rate": 15000,
                                   "rate_wholesale": 14000, "rate_agent": 13500, "tracking": "lot_expiry"})
    if not check("lots: a lot item with expiry -> 201", st == 201, (st, obat)):
        return None, None
    eq("expiry warning defaults to 30 days", rec(obat).get("expiry_warning_days"), 30)
    st, hp = api.create("item", {"item_name": "Ponsel uji (nomor seri)", "kind": "good", "stock_uom": uom, "rate": 1_500_000,
                                 "rate_wholesale": 1_450_000, "rate_agent": 1_400_000, "tracking": "serial"})
    if not check("lots: a serial item -> 201", st == 201, (st, hp)):
        return rec(obat)["name"], None
    return rec(obat)["name"], rec(hp)["name"]


def receipt(cl, vendor, outlet, lines, when=None):
    st, gr = cl.create("goods_receipt", {"vendor": vendor, "outlet": outlet, "received_on": when or day(0), "items": lines})
    if st != 201:
        return st, gr, None
    st, r = submit(cl, "goods_receipt", rec(gr)["name"])
    return st, r, rec(gr)["name"]


def scenario_lots_receipt_grosir(api):
    obat, hp = make_items(api)
    if not (obat and hp):
        return
    CTX.update(lot_obat=obat, lot_hp=hp)
    vendor = (api.list("vendor", limit=5) or [{}])[0].get("name")
    lines = [{"item": obat, "qty": 10, "rate": 8000, "lot_no": "SB-A", "expiry_date": day(10)},
             {"item": obat, "qty": 3, "rate": 8000, "lot_no": "SB-C", "expiry_date": day(20)},
             {"item": obat, "qty": 20, "rate": 9000, "lot_no": "SB-B", "expiry_date": day(120), "made_date": day(-30)},
             {"item": obat, "qty": 4, "rate": 8000, "lot_no": "SB-OLD", "expiry_date": day(-5)}]
    st, r, gr = receipt(api, vendor, GUDANG, lines)
    if not check("goods receipt with four lots -> 200", st == 200, (st, r)):
        return
    gr = rec(r).get("name") or gr
    CTX["lot_vendor"] = vendor
    eq("four lots registered for the item", sorted(x["lot_no"] for x in api.find("stock_lot", f"item == '{obat}'")), ["SB-A", "SB-B", "SB-C", "SB-OLD"])
    lb = api.find("stock_lot", f"item == '{obat}' && lot_no == 'SB-B'")[0]
    eq("the lot keeps its expiry and made date and its first document", (lb.get("expiry_date"), lb.get("made_date"), lb.get("first_voucher")),
       (day(120), day(-30), gr))
    eq("lot balances at the warehouse", lots(api, GUDANG, obat), {"SB-A": 10, "SB-C": 3, "SB-B": 20, "SB-OLD": 4})
    eq("stock balance = all lots", bal(api, GUDANG, obat), 37)
    lbr = api.find("stock_lot_balance", f"outlet == '{GUDANG}' && item == '{obat}' && lot == '{lot_name(api, obat, 'SB-A')}'")
    eq("the lot balance carries the lot's expiry (expiry-soon list sorts on it)", (lbr or [{}])[0].get("expiry_date"), day(10))
    moves = api.find("stock_move", f"voucher == '{gr}'")
    eq("one stock move per line, each with its lot", sorted((m.get("lot") or "") != "" for m in moves), [True] * 4)
    # refusals at the door
    st, g2 = api.create("goods_receipt", {"vendor": vendor, "outlet": GUDANG, "received_on": day(0), "items": [{"item": obat, "qty": 1, "rate": 8000}]})
    if check("a draft without a lot number saves -> 201", st == 201, (st, g2)):
        st, r = submit(api, "goods_receipt", rec(g2)["name"])
        check("... but cannot be submitted for a lot item with expiry -> 422 lot_no_given", st == 422 and "lot_no_given" in str(r), (st, r))
        api.call("DELETE", f"/api/v1/r/goods_receipt/{Api.q(rec(g2)['name'])}")
    st, r, g3 = receipt(api, vendor, GUDANG, [{"item": obat, "qty": 1, "rate": 8000, "lot_no": "SB-A", "expiry_date": day(11)}])
    check("a known lot with another expiry -> 409 lot_expiry_mismatch", st == 409 and code(r) == "lot_expiry_mismatch", (st, r))
    st, r, g4 = receipt(api, vendor, GUDANG, [{"item": obat, "qty": 1, "rate": 8000, "lot_no": "SB-NEW"}])
    check("a new lot without expiry -> 409 expiry_required", st == 409 and code(r) == "expiry_required", (st, r))
    for g in (g3, g4):
        if g:
            api.call("DELETE", f"/api/v1/r/goods_receipt/{Api.q(g)}")
    # serials
    st, r, _ = receipt(api, vendor, GUDANG, [{"item": hp, "qty": 2, "rate": 1_200_000, "serial_nos": "IMEI-001\nIMEI-002"}])
    check("goods receipt of two phones by serial -> 200", st == 200, (st, r))
    eq("IMEI-001 is in stock at the warehouse", serial_state(api, hp, "IMEI-001"), ("in_stock", GUDANG))
    st, r, g5 = receipt(api, vendor, GUDANG, [{"item": hp, "qty": 1, "rate": 1_200_000, "serial_nos": "IMEI-001"}])
    check("the same serial again -> 409 serial_in_stock", st == 409 and code(r) == "serial_in_stock", (st, r))
    st, r, g6 = receipt(api, vendor, GUDANG, [{"item": hp, "qty": 2, "rate": 1_200_000, "serial_nos": "IMEI-003"}])
    check("two phones with one serial -> 409 serial_count_mismatch", st == 409 and code(r) == "serial_count_mismatch", (st, r))
    for g in (g5, g6):
        if g:
            api.call("DELETE", f"/api/v1/r/goods_receipt/{Api.q(g)}")
    st, r = api.create("goods_receipt", {"vendor": vendor, "outlet": GUDANG, "received_on": day(0),
                                         "items": [{"item": hp, "qty": 1, "rate": 1_200_000, "packing": "x"}]})
    check("a serial item in a packing is refused (serial_whole_units) -> 422", st == 422 and "serial_whole_units" in str(r), (st, r))
    invariant(api, [GUDANG], obat, "after the receipts")


def scenario_lots_delivery_grosir(api):
    obat, hp, hq = CTX.get("lot_obat"), CTX.get("lot_hp"), CTX.get("hq")
    if not (obat and hp and hq):
        fail("lots: items and members ready", "missing")
        return
    # a customer of its own: the fixture customers carry overdue invoices from the scenarios before (a held delivery)
    base = api.get("customer", "CUST-0001")
    st, cu = api.create("customer", {"customer_name": "Apotek Uji Lot", "salesman": base.get("salesman"), "price_tier": base.get("price_tier") or ""})
    if not check("a customer for the lot delivery -> 201", st == 201, (st, cu)):
        return
    st, so = api.create("customer_order", {"customer": rec(cu)["name"], "po_number": "SO-LOT", "po_date": day(0),
                                           "items": [{"item": obat, "qty": 15}, {"item": hp, "qty": 1}]})
    if not check("order for 15 bottles and a phone -> 201", st == 201, (st, so)):
        return
    st, so = api.verb("customer_order", rec(so)["name"], "submit")
    if not check("order submitted -> 200", st == 200, (st, so)):
        return
    so = rec(so)
    st, dn = hq.create("delivery_note", {"customer_order": so["name"], "outlet": GUDANG, "delivery_date": day(0),
                                         "items": [{"item": obat, "qty": 12}, {"item": hp, "qty": 1, "serial_nos": "IMEI-009"}]})
    if not check("delivery note draft -> 201", st == 201, (st, dn)):
        return
    st, r = submit(hq, "delivery_note", rec(dn)["name"])
    check("a serial that is not in the warehouse -> 409 serial_not_here", st == 409 and code(r) == "serial_not_here", (st, r))
    items = rec(dn)["items"]
    items[1]["serial_nos"] = "IMEI-001"
    st, r = hq.patch("delivery_note", rec(dn)["name"], {"items": items})
    check("the right serial is typed -> 200", st == 200, (st, r))
    st, r = submit(hq, "delivery_note", rec(dn)["name"])
    if not check("delivery note submitted -> 200", st == 200, (st, r)):
        return
    name = rec(r).get("name") or rec(dn)["name"]
    CTX["lot_dn"] = name
    eq("FEFO: SB-A (10) then SB-C (2) leave; the expired SB-OLD is skipped", lots(api, GUDANG, obat), {"SB-A": 0, "SB-C": 1, "SB-B": 20, "SB-OLD": 4})
    mv = [m for m in api.find("stock_move", f"voucher == '{name}' && item == '{obat}'")]
    eq("one move per lot taken", sorted((m.get("qty"), m.get("lot") == lot_name(api, obat, "SB-A")) for m in mv), [(-10, True), (-2, False)])
    dnr = api.get("delivery_note", name)
    line = [i for i in dnr.get("items", []) if i["item"] == obat][0]
    eq("the line's cost = the value of its lot moves, to the rupiah", line.get("cogs"), -sum(m.get("value") or 0 for m in mv))
    eq("IMEI-001 is sold", serial_state(api, hp, "IMEI-001")[0], "sold")
    # the customer returns one bottle and the phone; the bottle went out of two lots, so it comes back to the no-lot bucket
    st, ret = hq.create("sales_return", {"delivery_note": name, "return_date": day(0), "outlet": GUDANG, "damaged_outlet": RUSAK,
                                         "items": [{"item": obat, "qty": 1, "condition": "good"},
                                                   {"item": hp, "qty": 1, "condition": "good", "serial_nos": "IMEI-001"}]})
    if check("sales return draft -> 201", st == 201, (st, ret)):
        st, r = submit(hq, "sales_return", rec(ret)["name"])
        if check("sales return submitted -> 200", st == 200, (st, r)):
            eq("the returned bottle sits in the no-lot bucket", lots(api, GUDANG, obat).get(""), 1)
            eq("IMEI-001 is back in stock", serial_state(api, hp, "IMEI-001"), ("in_stock", GUDANG))
            st, r = hq.verb("sales_return", rec(r).get("name") or rec(ret)["name"], "cancel")
            if check("the return is cancelled -> 200", st == 200, (st, r)):
                eq("... the bucket is empty again", lots(api, GUDANG, obat).get("", 0), 0)
                eq("... and IMEI-001 is sold again", serial_state(api, hp, "IMEI-001")[0], "sold")
    invariant(api, [GUDANG, RUSAK], obat, "after delivery and return")


def scenario_lots_transfer_count_grosir(api):
    obat = CTX.get("lot_obat")
    if not obat:
        return
    st, tr = api.create("stock_transfer", {"from_outlet": GUDANG, "to_outlet": KANVAS, "transfer_date": day(0), "items": [{"item": obat, "qty": 5}]})
    if not check("transfer of 5 bottles to the van -> 201", st == 201, (st, tr)):
        return
    st, r = submit(api, "stock_transfer", rec(tr)["name"])
    if not check("transfer sent -> 200", st == 200, (st, r)):
        return
    tname = rec(r).get("name") or rec(tr)["name"]
    eq("FEFO on the transfer: SB-C (1) then SB-B (4) leave", {k: v for k, v in lots(api, GUDANG, obat).items() if k}, {"SB-A": 0, "SB-C": 0, "SB-B": 16, "SB-OLD": 4})
    st, rc = api.create("stock_transfer_receipt", {"stock_transfer": tname, "outlet": KANVAS, "items": [{"item": obat, "qty": 5}]})
    if check("the van receives -> 201", st == 201, (st, rc)):
        st, r = submit(api, "stock_transfer_receipt", rec(rc)["name"])
        check("receipt submitted -> 200", st == 200, (st, r))
    eq("the same lots arrive in the van", lots(api, KANVAS, obat), {"SB-C": 1, "SB-B": 4})
    # count one lot: 15 of SB-B on the shelf, the system says 16
    st, c = api.create("stock_count", {"outlet": GUDANG, "count_date": day(0), "items": [{"item": obat, "counted_qty": 15, "lot_no": "SB-B"},
                                                                                         {"item": obat, "counted_qty": 1, "lot_no": "SB-B"}]})
    check("a lot counted on two lines -> 422 lot_once", st == 422 and "lot_once" in str(c), (st, c))
    st, c = api.create("stock_count", {"outlet": GUDANG, "count_date": day(0), "items": [{"item": obat, "counted_qty": 15, "lot_no": "SB-B"}]})
    if check("a count of lot SB-B -> 201", st == 201, (st, c)):
        st, r = submit(api, "stock_count", rec(c)["name"])
        if check("count submitted -> 200", st == 200, (st, r)):
            line = (api.get("stock_count", rec(r).get("name") or rec(c)["name"]).get("items") or [{}])[0]
            eq("the count reads the lot's quantity", (line.get("system_qty"), line.get("difference")), (16, -1))
            eq("only SB-B changes", lots(api, GUDANG, obat).get("SB-B"), 15)
            eq("... the expired lot is untouched", lots(api, GUDANG, obat).get("SB-OLD"), 4)
    invariant(api, [GUDANG, KANVAS], obat, "after transfer and count")


def scenario_lots_expiry_grosir(api):
    obat = CTX.get("lot_obat")
    if not obat:
        return
    st, run = api.call("POST", "/api/v1/flows/lot_expiry_digest/run", {})
    if not check("expiry digest runs -> 200", st == 200, (st, run)):
        return
    rows = {(r["outlet"], r["lot_no"]): r for r in api.find("stock_expiry", f"item == '{obat}'")}
    eq("the expiry-soon list: SB-OLD in the warehouse (expired), SB-C in the van (20 days)", sorted(rows), sorted([(KANVAS, "SB-C"), (GUDANG, "SB-OLD")]))
    r = rows.get((GUDANG, "SB-OLD"), {})
    eq("SB-OLD: expired 5 days ago, 4 left, open", (r.get("state"), r.get("days_left"), r.get("qty"), r.get("open")), ("expired", -5, 4, True))
    r = rows.get((KANVAS, "SB-C"), {})
    eq("SB-C: soon, 20 days left", (r.get("state"), r.get("days_left")), ("soon", 20))
    check("SB-B (120 days) and the used-up SB-A are not listed", not any(k[1] in ("SB-A", "SB-B") for k in rows), sorted(rows))
    # the expired bottles leave through an adjustment with reason expired: the kernel takes expired lots first
    st, adj = api.create("stock_adjustment", {"outlet": GUDANG, "reason": "expired", "adjustment_date": day(0), "items": [{"item": obat, "qty": 4}]})
    if check("expired adjustment of 4 bottles -> 201", st == 201, (st, adj)):
        st, r = submit(api, "stock_adjustment", rec(adj)["name"])
        if check("adjustment submitted -> 200", st == 200, (st, r)):
            eq("the expired lot leaves first", (lots(api, GUDANG, obat).get("SB-OLD"), lots(api, GUDANG, obat).get("SB-B")), (0, 15))
    st, run = api.call("POST", "/api/v1/flows/lot_expiry_digest/run", {})
    check("expiry digest runs again -> 200", st == 200, (st, run))
    rows = {(r["outlet"], r["lot_no"]): r for r in api.find("stock_expiry", f"item == '{obat}'")}
    eq("SB-OLD is closed once its stock is gone; SB-C stays open; nothing new", {k: v.get("open") for k, v in rows.items()},
       {(GUDANG, "SB-OLD"): False, (KANVAS, "SB-C"): True})
    invariant(api, [GUDANG, KANVAS, RUSAK], obat, "after the expired adjustment")


def scenario_lots_access_grosir(api):
    """The registers are written by the stock hooks only: head office reads them, nobody creates one."""
    hq, asep = CTX.get("hq"), CTX.get("asep")
    if not hq:
        return
    obat = CTX.get("lot_obat")
    st, r = hq.create("stock_lot", {"item": obat, "lot_no": "TANGAN"})
    check("head office cannot create a lot by hand -> 403", denied(st, r), (st, r))
    st, r = hq.create("serial_no", {"item": CTX.get("lot_hp"), "serial": "TANGAN"})
    check("... nor a serial -> 403", denied(st, r), (st, r))
    if asep:
        rows = asep.list("stock_lot", limit=5)
        check("a salesman reads lots (a printed surat jalan names them)", isinstance(rows, list) and len(rows) > 0, rows)


# ---------- demo-produksi: a batch consumes a material lot and makes a lot with its production code

PRODUKSI, JADI = "OUT-02", "OUT-03"
DIMSUM, PLASTIK = "BRG-0030", "BRG-0008"


def scenario_lots_production(api):
    hq = CTX.get("hq")
    if hq is None:
        fail("lots: produksi members signed in", CTX.get("login_error"))
        return
    before_bucket = bal(api, PRODUKSI, PLASTIK)
    st, r = api.patch("item", PLASTIK, {"tracking": "lot"})
    check("the packaging material is tracked per lot -> 200", st == 200, (st, r))
    st, r = api.patch("item", DIMSUM, {"tracking": "lot_expiry", "expiry_warning_days": 14})
    check("dimsum is tracked per lot with expiry -> 200", st == 200, (st, r))
    vendor = (api.list("vendor", limit=5) or [{}])[0].get("name")
    st, gr = api.create("goods_receipt", {"vendor": vendor, "outlet": PRODUKSI, "received_on": day(0),
                                          "items": [{"item": PLASTIK, "qty": 300, "rate": 150, "lot_no": "PLS-77"}]})
    if check("a receipt of packaging lot PLS-77 -> 201", st == 201, (st, gr)):
        st, r = submit(api, "goods_receipt", rec(gr)["name"])
        check("... submitted -> 200", st == 200, (st, r))
    eq("the stock from before is the no-lot bucket", lots(api, PRODUKSI, PLASTIK), {"": before_bucket, "PLS-77": 300})
    st, wo = api.create("work_order", {"item": DIMSUM, "qty": 50, "outlet": PRODUKSI, "start_date": day(0)})
    if not check("SPK for 50 packs -> 201", st == 201, (st, wo)):
        return
    st, wo = api.verb("work_order", rec(wo)["name"], "submit")
    st, pb = hq.create("production_batch", {"work_order": rec(wo)["name"], "output_outlet": JADI, "production_date": day(0), "production_code": "DS-LOT-01"})
    if not check("batch draft from the BOM -> 201", st == 201, (st, pb)):
        return
    pb = rec(pb)
    out = [o for o in pb.get("outputs", []) if o["item"] == DIMSUM]
    check("the output line carries the production code as lot and the batch's expiry",
          out and out[0].get("lot_no") == "DS-LOT-01" and out[0].get("expiry_date") == pb.get("expiry_date") != "", (out, pb.get("expiry_date")))
    st, r = hq.verb("production_batch", pb["name"], "submit")
    if not check("batch submitted -> 200", st == 200, (st, r)):
        return
    pbname = rec(r).get("name") or pb["name"]
    used = sum(m["qty"] for m in pb.get("materials", []) if m["item"] == PLASTIK)
    eq("the packaging comes out of lot PLS-77 (FEFO: real lots before the bucket)", lots(api, PRODUKSI, PLASTIK), {"": before_bucket, "PLS-77": 300 - used})
    made = api.find("stock_lot", f"item == '{DIMSUM}' && lot_no == 'DS-LOT-01'")
    if check("a dimsum lot with the production code", len(made) == 1, made):
        eq("... with the batch's expiry and production date", (made[0].get("expiry_date"), made[0].get("made_date")), (pb.get("expiry_date"), day(0)))
        good = sum(o["qty"] for o in pb.get("outputs", []) if o["item"] == DIMSUM)
        eq("... and the packs in the cold store", lots(api, JADI, DIMSUM).get("DS-LOT-01"), good)
    invariant(api, [PRODUKSI], PLASTIK, "after the batch")
    invariant(api, [JADI], DIMSUM, "after the batch")
    eq("... first brought in by the batch", made[0].get("first_voucher") if made else None, pbname)
    st, r = hq.verb("production_batch", pbname, "cancel")
    if check("the batch is cancelled -> 200", st == 200, (st, r)):
        eq("the packaging lot is whole again", lots(api, PRODUKSI, PLASTIK).get("PLS-77"), 300)
        eq("the dimsum lot is empty again", lots(api, JADI, DIMSUM).get("DS-LOT-01"), 0)
    invariant(api, [JADI], DIMSUM, "after the cancel")
    st, d2 = hq.create("production_batch", {"work_order": rec(wo)["name"], "output_outlet": JADI, "production_date": day(0)})
    if check("a second draft batch without a production code -> 201", st == 201, (st, d2)):
        o2 = [o for o in rec(d2).get("outputs", []) if o["item"] == DIMSUM]
        eq("... takes the production date as its lot number", o2[0].get("lot_no") if o2 else None, day(0))
        hq.call("DELETE", f"/api/v1/r/production_batch/{Api.q(rec(d2)['name'])}")


SCENARIOS = {
    "demo-grosir": [scenario_lots_receipt_grosir, scenario_lots_delivery_grosir, scenario_lots_transfer_count_grosir,
                    scenario_lots_expiry_grosir, scenario_lots_access_grosir],
    "demo-produksi": [scenario_lots_production],
}
