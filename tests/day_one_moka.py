"""Day-one scenario of demo-fnb: Moka sales through the API (connector moka, poll per outlet) into outlet_sales.

Loaded by tests/day_one.py. The kernel polls Moka per outlet (rangka-connectors moka, trigger transaction_created) and
starts flow moka_transaction_staged once per transaction; here the same events, shaped as the connector emits them from
its synthetic fixtures (rangka-connectors moka/fixtures), go through POST /api/v1/flows/<name>/run. No Moka account.
Covered: outlet by Moka id or by Moka name, item by SKU or by name, a refunded quantity as its own line, a repeated
transaction recorded once, unmapped lines held and mapped by head office, the hourly recap (one draft per outlet and
past day, today left alone), a late line on a recapped day flagged and held at submit, the draft submitted with stock
out through the recipes, and the permissions (head office reads, maps unmapped lines only, cannot type a transaction).
"""
import datetime

H = {}
BRAGA, DAGO = "OUT-03", "OUT-02"
ORI, PDS = "BRG-0007", "BRG-0008"
PORSI_UOM = "UOM-006"


def bind(m):
    for k in ("ok", "fail", "check", "eq", "CTX", "denied", "kernel_today"):
        H[k] = getattr(m, k)
    globals().update(H)
    # head office reads the Moka staging records and maps unmapped lines (write while not posted); nobody else sees them
    m.MATRIX["moka_transaction"] = {"head_office": {"read", "export"}}
    m.MATRIX["moka_sale_line"] = {"head_office": {"read", "write", "export"}}


def event(tid, day, checkouts, outlet_id=70002, outlet_name="Tahu Contoh - Braga", refunded=False, hour="11:42:10", refunded_at=None):
    """One transaction_created event, as the moka connector emits it (emit keys of moka/connector.rk)."""
    total = sum(c["total_price"] - c["discount_amount"] for c in checkouts)
    return {"transaction_id": tid, "outlet_id": outlet_id, "outlet_name": outlet_name, "receipt_no": f"BRG-{tid % 100000:06d}" if isinstance(tid, int) else f"BRG-{str(tid)[-6:]}",
            "created_at": f"{day}T{hour}+07:00", "is_refunded": refunded, "checkouts": checkouts, "payment_type": "cash",
            "total_collected": total, "updated_at": refunded_at or f"{day}T{hour}+07:00", "refunded_date": refunded_at}


CHECKOUT = [66000000]


def line(name, sku, qty, price, discount=0, refunded=0, variant="", cid=None):
    """One checkout; every checkout gets its own Moka id (cid to repeat one in a later event of the same transaction)."""
    if cid is None:
        CHECKOUT[0] += 1
        cid = CHECKOUT[0]
    return {"id": cid, "item_id": 0, "item_name": name, "item_variant_name": variant, "sku": sku, "quantity": qty, "item_price": price,
            "total_price": qty * price, "discount_amount": discount, "refunded_quantity": refunded}


def run(api, flow, ev=None):
    st, out = api.call("POST", f"/api/v1/flows/{flow}/run", {"event": ev} if ev is not None else {})
    return st, out or {}


def scenario_moka_api(api):
    hq = CTX.get("hq")
    if hq is None:
        fail("moka api", "head office client missing")
        return
    today = kernel_today()
    d1 = (today - datetime.timedelta(days=1)).isoformat()
    d2 = (today - datetime.timedelta(days=2)).isoformat()
    d3 = (today - datetime.timedelta(days=3)).isoformat()
    d4 = (today - datetime.timedelta(days=4)).isoformat()
    st, resp = api.patch("outlet", BRAGA, {"moka_outlet_id": "70002"})
    check("Braga gets its Moka outlet id", st == 200, (st, resp))

    # the gate (audit sisa-1111 R1, moka-guard H1/M1): until an outlet's moka_api_from is set its API lines are held
    # (the export counts the day, the recap leaves them); setting the date stages the held lines from it on
    d25 = (today - datetime.timedelta(days=25)).isoformat()
    old_day = (today - datetime.timedelta(days=40)).isoformat()
    st, out = run(api, "moka_transaction_staged", event(91000000, d25, [line("Tahu goreng original", "TH-ORI", 1, 15000)]))
    tx0 = api.find("moka_transaction", "transaction_id == '91000000'")
    l0 = api.find("moka_sale_line", f"transaction == '{tx0[0]['name']}'") if tx0 else []
    check("gate closed: Braga's line is held (the export counts the day)", st == 200 and [r["status"] for r in l0] == ["held"], (st, out, l0))
    st, out = run(api, "moka_transaction_staged", event(91000046, d25, [line("Es contoh", "MK-ESC", 1, 7000), line("Tahu goreng original", "TH-ORI", 0, 15000)]))
    check("outlet still on the export: no notice about unknown items or qty 0 (audit moka-guard D3)", st == 200
          and not any("belum dikenali" in n.get("text", "") or "qty 0" in n.get("text", "") for n in out.get("notifications", [])), (st, out))
    sold40 = line("Tahu goreng original", "TH-ORI", 2, 15000)
    run(api, "moka_transaction_staged", event(91000040, old_day, [sold40]))
    run(api, "moka_daily_sales")
    eq("held lines are not recapped", [r["status"] for r in api.find("moka_sale_line", "status == 'held'")], ["held", "held"])
    api_from = (today - datetime.timedelta(days=30)).isoformat()
    for o in (BRAGA, DAGO):
        st, resp = api.patch("outlet", o, {"moka_api_from": api_from})
        check(f"{o} counts Moka API sales from {api_from}", st == 200, (st, resp))
    l46 = api.find("moka_sale_line", "description == 'Es contoh'")
    eq("the unknown item polled on the export is unmapped and dated after the gate (head office told on opening)", [(r["status"], r["date"]) for r in l46], [("unmapped", d25)])
    st, out = run(api, "moka_api_opened", api.get("outlet", BRAGA))
    check("opening the gate tells head office about the unmapped lines from that day (audit held-release A2)", st == 200
          and any("belum dipetakan" in n.get("text", "") for n in out.get("notifications", [])), (st, out.get("notifications")))
    l0 = api.find("moka_sale_line", f"transaction == '{tx0[0]['name']}'") if tx0 else []
    eq("gate opened: the line polled before it is staged now", [r["status"] for r in l0], ["staged"])
    tx40 = api.find("moka_transaction", "transaction_id == '91000040'")
    eq("a sale of a day before the gate stays held", [r["status"] for r in api.find("moka_sale_line", f"transaction == '{tx40[0]['name']}'")] if tx40 else None, ["held"])
    # that sale refunded after the switch: the refund is booked by the API on its own day
    st, out = run(api, "moka_transaction_staged", event(91000040, old_day, [dict(sold40, refunded_quantity=1)], refunded=True, refunded_at=f"{d25}T10:00:00+07:00"))
    r40 = [r for r in api.find("moka_sale_line", f"transaction == '{tx40[0]['name']}'") if r["qty"] < 0] if tx40 else []
    eq("refund after the switch of a sale before it: staged on the refund day", [(r["qty"], r["refund"], r["date"], r["status"]) for r in r40], [(-1, 15000, d25, "staged")])

    # a transaction without id is not booked (the kernel does not dedupe an empty key); head office is told
    for tid in ("", None):
        ev = event(tid, d1, [line("Tahu goreng original", "TH-ORI", 1, 15000)])
        st, out = run(api, "moka_transaction_staged", ev)
        check(f"transaction_id {tid!r}: the staging flow does not run", st == 200 and out.get("skipped"), (st, out))
        st, out = run(api, "moka_transaction_refused", ev)
        check(f"transaction_id {tid!r}: head office told", st == 200 and any("tanpa ID atau jumlah/harga" in n.get("text", "") for n in out.get("notifications", [])), (st, out))
    eq("no moka_transaction without id", len(api.find("moka_transaction", "transaction_id == ''")), 0)
    st, out = run(api, "moka_transaction_refused", event(91000041, d1, [line("Tahu goreng original", "TH-ORI", 1, 15000)]))
    check("a transaction with id: the notice flow does not run", st == 200 and out.get("skipped"), (st, out))
    # a checkout without quantity (or total_price): not booked as a guess, head office told (audit moka-guard L1)
    nq = line("Tahu goreng original", "TH-ORI", 1, 15000)
    nq["quantity"] = None
    evq = event(91000044, d1, [nq])
    st, out = run(api, "moka_transaction_staged", evq)
    check("quantity null: the staging flow does not run", st == 200 and out.get("skipped"), (st, out))
    st, out = run(api, "moka_transaction_refused", evq)
    check("quantity null: head office told", st == 200 and any("jumlah/harga" in n.get("text", "") for n in out.get("notifications", [])), (st, out))

    # refunded_quantity null in a checkout (Moka's spec does not list it): read as 0, the sale is booked (audit R2)
    d20 = (today - datetime.timedelta(days=20)).isoformat()
    evn = event(91000042, d20, [line("Tahu goreng original", "TH-ORI", 1, 15000, refunded=None)])
    st, out = run(api, "moka_transaction_staged", evn)
    txn = api.find("moka_transaction", "transaction_id == '91000042'")
    ln = api.find("moka_sale_line", f"transaction == '{txn[0]['name']}'") if txn else []
    check("refunded_quantity null: the sale is booked, no refund line", st == 200 and [(r["qty"], r["status"]) for r in ln] == [(1, "staged")], (st, out, ln))
    st, out = run(api, "moka_transaction_staged", dict(evn, is_refunded=True))
    check("refunded_quantity null on a known transaction: runs, nothing added", st == 200
          and len(api.find("moka_sale_line", f"transaction == '{txn[0]['name']}'")) == 1 if txn else False, (st, out))

    # a sale (by id, two items by SKU, one with a discount)
    ev1 = event(91000001, d1, [line("Tahu goreng original", "TH-ORI", 2, 15000), line("Tahu goreng pedas", "TH-PDS", 2, 18000, discount=3000)])
    st, out = run(api, "moka_transaction_staged", ev1)
    check("Moka transaction runs the staging flow", st == 200 and not out.get("skipped"), (st, out))
    tx = api.find("moka_transaction", "transaction_id == '91000001'")
    check("one moka_transaction, Braga by its Moka id, day from created_at", len(tx) == 1 and tx[0]["outlet"] == BRAGA and tx[0]["date"] == d1, tx)
    rows = api.find("moka_sale_line", f"transaction == '{tx[0]['name']}'") if tx else []
    eq("two staged lines with the items found by SKU", sorted((r["item"], r["qty"], r["gross"], r["discount"], r["status"]) for r in rows),
       [(ORI, 2, 30000, 0, "staged"), (PDS, 2, 36000, 3000, "staged")])

    # the same transaction again (a repeated poll): nothing new
    st, out = run(api, "moka_transaction_staged", ev1)
    eq("the same Moka transaction again: still one transaction", len(api.find("moka_transaction", "transaction_id == '91000001'")), 1)
    eq("the same Moka transaction again: still two lines", len(api.find("moka_sale_line", f"transaction == '{tx[0]['name']}'")) if tx else 0, 2)

    # a refunded sale, outlet by Moka name only (no id match), item by name (no SKU)
    ev2 = event(91000002, d1, [line("Tahu goreng original", "", 1, 15000, refunded=1)], outlet_id=99999, refunded=True, hour="19:05:44")
    st, out = run(api, "moka_transaction_staged", ev2)
    tx2 = api.find("moka_transaction", "transaction_id == '91000002'")
    check("refunded sale: outlet found by Moka name", len(tx2) == 1 and tx2[0]["outlet"] == BRAGA and tx2[0]["refunded"] is True, (st, out, tx2))
    rows2 = api.find("moka_sale_line", f"transaction == '{tx2[0]['name']}'") if tx2 else []
    eq("refunded sale: a sale line and a refund line (qty < 0, gross 0, refund = net)", sorted((r["item"], r["qty"], r["gross"], r["refund"]) for r in rows2),
       [(ORI, -1, 0, 15000), (ORI, 1, 15000, 0)])

    # an item Juragan does not know: unmapped (head office is told)
    ev3 = event(91000003, d1, [line("Es jeruk", "MK-JRK", 1, 8000)], hour="12:10:00")
    run(api, "moka_transaction_staged", ev3)
    tx3 = api.find("moka_transaction", "transaction_id == '91000003'")
    um = api.find("moka_sale_line", f"transaction == '{tx3[0]['name']}'") if tx3 else []
    check("unknown item: the line is unmapped", len(um) == 1 and um[0]["status"] == "unmapped" and um[0]["item"] in ("", None), um)

    # a line of an unknown outlet mapped by head office to Braga: held before the gate, staged from it on
    run(api, "moka_transaction_staged", event(91000043, old_day, [line("Tahu goreng original", "TH-ORI", 1, 15000)], outlet_id=88889, outlet_name="Outlet lama"))
    run(api, "moka_transaction_staged", event(91000045, d25, [line("Tahu goreng original", "TH-ORI", 1, 15000)], outlet_id=88889, outlet_name="Outlet lama"))
    for tid, want in ((91000043, "held"), (91000045, "staged")):
        t = api.find("moka_transaction", f"transaction_id == '{tid}'")
        ls = api.find("moka_sale_line", f"transaction == '{t[0]['name']}'") if t else []
        if ls:
            st, resp = hq.patch("moka_sale_line", ls[0]["name"], {"outlet": BRAGA})
            check(f"mapping a line of {ls[0]['date']} to Braga (API from {api_from}): {want}", st == 200 and api.get("moka_sale_line", ls[0]["name"])["status"] == want, (st, resp))
        else:
            fail("gate mapping", ("no unmapped line", tid))

    # D1: a held line moved to an outlet already on the API for its day is staged by the hourly release
    t43 = api.find("moka_transaction", "transaction_id == '91000043'")
    l43 = api.find("moka_sale_line", f"transaction == '{t43[0]['name']}'") if t43 else []
    if l43:
        api.patch("outlet", DAGO, {"moka_api_from": old_day})
        st, resp = hq.patch("moka_sale_line", l43[0]["name"], {"outlet": DAGO})
        check("a held line moved to Dago (API from its day): still held until the release", st == 200 and api.get("moka_sale_line", l43[0]["name"])["status"] == "held", (st, resp))
        st, out = run(api, "moka_held_release")
        check("hourly release: the line is staged", st == 200 and api.get("moka_sale_line", l43[0]["name"])["status"] == "staged", (st, out))
    # D2: a held line whose item head office emptied does not block opening the gate: it goes back to unmapped
    l40 = [r for r in api.find("moka_sale_line", f"transaction == '{tx40[0]['name']}'") if r["qty"] > 0] if tx40 else []
    if l40:
        st, resp = hq.patch("moka_sale_line", l40[0]["name"], {"item": ""})
        check("head office empties the item of a held line", st == 200, (st, resp))
        st, resp = api.patch("outlet", BRAGA, {"moka_api_from": old_day})
        check("opening Braga from that day is not refused; the line is unmapped", st == 200 and api.get("moka_sale_line", l40[0]["name"])["status"] == "unmapped", (st, resp))
        api.patch("outlet", BRAGA, {"moka_api_from": api_from})
        api.patch("outlet", DAGO, {"moka_api_from": api_from})
    else:
        fail("held release", ("no held line", tx40))

    # an outlet Juragan does not know: unmapped without outlet
    run(api, "moka_transaction_staged", event(91000004, d1, [line("Tahu goreng original", "TH-ORI", 1, 15000)], outlet_id=88888, outlet_name="Outlet lain"))
    tx4 = api.find("moka_transaction", "transaction_id == '91000004'")
    check("unknown outlet: transaction kept without outlet, line unmapped", len(tx4) == 1 and tx4[0]["outlet"] in ("", None)
          and all(r["status"] == "unmapped" for r in api.find("moka_sale_line", f"transaction == '{tx4[0]['name']}'")), tx4)

    # a sale of today, one two days ago, and three days ago one known and one unknown item (no variant, refund or
    # discount keys in that checkout: they are optional)
    run(api, "moka_transaction_staged", event(91000005, today.isoformat(), [line("Tahu goreng pedas", "TH-PDS", 1, 18000)]))
    run(api, "moka_transaction_staged", event(91000006, d2, [line("Tahu goreng pedas", "TH-PDS", 3, 18000)]))
    ev8 = event(91000008, d3, [line("Tahu goreng original", "TH-ORI", 1, 15000), line("Kerupuk", "MK-KRP", 2, 5000)])
    for c in ev8["checkouts"]:
        c.pop("item_variant_name"), c.pop("refunded_quantity"), c.pop("discount_amount")
    st, out = run(api, "moka_transaction_staged", ev8)
    tx8 = api.find("moka_transaction", "transaction_id == '91000008'")
    l8 = api.find("moka_sale_line", f"transaction == '{tx8[0]['name']}'") if tx8 else []
    check("checkout without variant, refund and discount keys: recorded, the unknown item unmapped",
          st == 200 and sorted(r["status"] for r in l8) == ["staged", "unmapped"], (st, out, l8))

    # data the recap would refuse never becomes a line: a free item refunded (refund 0), a discount above the price, a
    # quantity of 0; the recap of other outlets and days goes on
    ev10 = event(91000010, d2, [line("Tahu goreng original", "TH-ORI", 1, 15000, discount=15000, refunded=1),
                                line("Tahu goreng pedas", "TH-PDS", 1, 18000, discount=20000), line("Tahu goreng pedas", "TH-PDS", 0, 18000)])
    st, out = run(api, "moka_transaction_staged", ev10)
    tx10 = api.find("moka_transaction", "transaction_id == '91000010'")
    l10 = api.find("moka_sale_line", f"transaction == '{tx10[0]['name']}'") if tx10 else []
    eq("free item refunded: no refund line; discount capped at the price; qty 0 skipped (head office told)",
       (sorted((r["qty"], r["gross"], r["discount"], r["refund"]) for r in l10), any("qty 0" in n.get("text", "") for n in out.get("notifications", []))),
       ([(1, 15000, 15000, 0), (1, 18000, 18000, 0)], True))

    # people: head office cannot type a Moka transaction, and a staged line keeps an item
    st, resp = hq.create("moka_transaction", {"transaction_id": "x1"})
    check("head office cannot create a moka_transaction -> 403", denied(st, resp), (st, resp))
    if rows:
        st, resp = hq.patch("moka_sale_line", rows[0]["name"], {"item": ""})
        check("a staged line keeps its item (rule staged_has_outlet_and_item)", st == 422 and "staged_has_outlet_and_item" in str(resp), (st, resp))
        st, resp = hq.patch("moka_sale_line", rows[0]["name"], {"outlet": "OUT-01"})
        check("a staged line cannot move to an outlet without Moka (rule staged_outlet_uses_moka)", st == 422 and "staged_outlet_uses_moka" in str(resp), (st, resp))

    def api_sales(day):
        return api.find("outlet_sales", f"outlet == '{BRAGA}' && date == '{day}' && source == 'moka' && key_version == 3 && docstatus != 2")

    # the recap: every past day with staged lines, one record per outlet and day; unmapped lines wait, hold nothing back
    st, out = run(api, "moka_daily_sales")
    check("recap runs", st == 200, (st, out))
    s3, s2, s1 = api_sales(d3), api_sales(d2), api_sales(d1)
    check("recap: three days ago, its known item only: net Rp 15.000", len(s3) == 1 and s3[0]["net_total"] == 15000, s3)
    check("recap: two days ago, net Rp 54.000", len(s2) == 1 and s2[0]["net_total"] == 54000 and s2[0]["docstatus"] == 0, s2)
    # 30000 + (36000 - 3000) + 15000 - 15000 (refund); Es jeruk is still unmapped
    check("recap: yesterday without the unmapped line, net Rp 63.000, not flagged", len(s1) == 1 and s1[0]["net_total"] == 63000
          and not s1[0].get("legacy_key_overlap"), s1)
    eq("recap: today is not taken", len(api_sales(today.isoformat())), 0)
    st, out = run(api, "moka_daily_sales")
    eq("recap again: nothing new", (len(api_sales(d1)), len(api_sales(d2)), len(api_sales(d3))), (1, 1, 1))
    if s1:
        posted = api.find("moka_sale_line", f"sales == '{s1[0]['name']}'")
        check("its four lines are posted", len(posted) == 4 and all(r["status"] == "posted" for r in posted), posted)
        if posted:
            st, resp = hq.patch("moka_sale_line", posted[0]["name"], {"item": PDS})
            check("head office cannot change a posted Moka line -> 403", denied(st, resp), (st, resp))
        st, resp = hq.verb("outlet_sales", s1[0]["name"], "submit")
        check("head office submits the Moka day: stock out through the recipes", st == 200 and (resp or {}).get("cogs_total", 0) > 0, (st, resp))

    # head office maps the unknown items: they go into the next recap as their own records, not flagged
    for u, item in ((um, PDS), ([r for r in l8 if r["status"] == "unmapped"], ORI)):
        if u:
            st, resp = hq.patch("moka_sale_line", u[0]["name"], {"item": item})
            check("head office maps an unknown item, the line is staged", st == 200 and api.get("moka_sale_line", u[0]["name"])["status"] == "staged", (st, resp))
    run(api, "moka_daily_sales")
    eq("mapped lines: a second record for yesterday (Rp 8.000) and for three days ago (Rp 10.000), neither flagged",
       (sorted(x["net_total"] for x in api_sales(d1)), sorted(x["net_total"] for x in api_sales(d3)),
        any(x.get("legacy_key_overlap") for x in api_sales(d1) + api_sales(d3))), ([8000, 63000], [10000, 15000], False))

    # a late transaction of a recapped day: its own record, not flagged
    run(api, "moka_transaction_staged", event(91000007, d1, [line("Tahu goreng original", "TH-ORI", 1, 15000)], hour="21:59:00"))
    run(api, "moka_daily_sales")
    eq("late transaction: another record for yesterday, not flagged", sorted((x["net_total"], bool(x.get("legacy_key_overlap"))) for x in api_sales(d1)),
       [(8000, False), (15000, False), (63000, False)])

    # an export of the same day already imported (key_version 2): the API record is flagged and held at submit
    st, filed = api.create("outlet_sales", {"outlet": BRAGA, "date": d4, "source": "moka", "moka_outlet": "Tahu Contoh - Braga", "key_version": 2,
                                            "lines": [{"item": ORI, "qty": 1, "gross": 15000}]})
    check("an export record for four days ago", st == 201, (st, filed))
    run(api, "moka_transaction_staged", event(91000009, d4, [line("Tahu goreng original", "TH-ORI", 1, 15000)]))
    run(api, "moka_daily_sales")
    s4 = api_sales(d4)
    check("API day with an export already there: flagged", len(s4) == 1 and s4[0]["legacy_key_overlap"] is True, s4)
    if s4:
        st, resp = hq.verb("outlet_sales", s4[0]["name"], "submit")
        check("the flagged draft cannot be submitted before head office checks it", st in (409, 422) and "legacy_key_reviewed" in str(resp), (st, resp))


def scenario_moka_late_refund(api):
    """Audit moka H3: a refund Moka reports after the transaction was polled."""
    today = kernel_today()
    d = [(today - datetime.timedelta(days=k)).isoformat() for k in range(0, 12)]

    def lines_of(tid):
        tx = api.find("moka_transaction", f"transaction_id == '{tid}'")
        return tx, (api.find("moka_sale_line", f"transaction == '{tx[0]['name']}'") if tx else [])

    def sales(outlet, day):
        return api.find("outlet_sales", f"outlet == '{outlet}' && date == '{day}' && source == 'moka' && key_version == 3 && docstatus != 2")

    # a sale polled before its refund: two of ORI, one of PDS with a discount
    ori, pds = line("Tahu goreng original", "TH-ORI", 2, 15000), line("Tahu goreng pedas", "TH-PDS", 1, 18000, discount=2000)
    run(api, "moka_transaction_staged", event(91000021, d[8], [ori, pds]))
    run(api, "moka_daily_sales")
    eq("sale before its refund: recapped once, net Rp 46.000", [x["net_total"] for x in sales(BRAGA, d[8])], [46000])

    # Moka changes the transaction: one ORI refunded on d[7] (the connector polls changed transactions again)
    later = lambda r_ori, r_pds, at: event(91000021, d[8], [dict(ori, refunded_quantity=r_ori), dict(pds, refunded_quantity=r_pds)],
                                           refunded=True, refunded_at=at)
    st, out = run(api, "moka_transaction_staged", later(1, 0, f"{d[7]}T09:15:30+07:00"))
    tx, ls = lines_of(91000021)
    check("late refund: the known transaction runs without error", st == 200, (st, out))
    eq("late refund: one refund line of the refunded quantity, dated the refund day, staged, same checkout",
       sorted((r["qty"], r["refund"], r["date"], r["status"], r["checkout"]) for r in ls if r["qty"] < 0),
       [(-1, 15000, d[7], "staged", str(ori["id"]))])
    check("late refund: the transaction is marked refunded, head office told", tx and tx[0]["refunded"] is True
          and any("retur susulan" in n.get("text", "") for n in out.get("notifications", [])), (tx, out.get("notifications")))
    # the same change again (a repeated poll, or Moka touching the transaction): nothing new
    run(api, "moka_transaction_staged", later(1, 0, f"{d[7]}T09:15:30+07:00"))
    eq("the same change again: still one refund line", len([r for r in lines_of(91000021)[1] if r["qty"] < 0]), 1)
    # the second ORI and the PDS refunded later: only the new quantity, PDS net of its discount, never above the quantity
    run(api, "moka_transaction_staged", later(5, 1, f"{d[6]}T10:00:00+07:00"))
    eq("second refund: only what is new (ORI 1 more, PDS 1 at net Rp 16.000), quantity capped at what was sold",
       sorted((r["description"], r["qty"], r["refund"], r["date"]) for r in lines_of(91000021)[1] if r["qty"] < 0),
       sorted([("Retur Tahu goreng original", -1, 15000, d[7]), ("Retur Tahu goreng original", -1, 15000, d[6]),
               ("Retur Tahu goreng pedas", -1, 16000, d[6])]))
    run(api, "moka_daily_sales")
    eq("refunds go into the recap of the refund days (net minus the refund)",
       ([x["net_total"] for x in sales(BRAGA, d[7])], [x["net_total"] for x in sales(BRAGA, d[6])]), ([-15000], [-31000]))
    eq("the sale day keeps its one record", [x["net_total"] for x in sales(BRAGA, d[8])], [46000])

    # checkouts as in Moka's spec: a uuid, no id; two lines of one item refunded later, a refund time in UTC
    u1, u2 = dict(line("Tahu goreng original", "TH-ORI", 2, 15000), uuid="c0ffee00-0000-4000-8000-0000000000a1"), \
        dict(line("Tahu goreng original", "TH-ORI", 1, 15000), uuid="c0ffee00-0000-4000-8000-0000000000a2")
    for c in (u1, u2):
        c.pop("id")
    run(api, "moka_transaction_staged", event("4a3f6c1e-uuid-0026", d[5], [u1, u2]))
    run(api, "moka_transaction_staged", event("4a3f6c1e-uuid-0026", d[5], [dict(u1, refunded_quantity=2), dict(u2, refunded_quantity=1)],
                                              refunded=True, refunded_at=f"{d[5]}T18:30:00Z"))
    eq("uuid checkouts, two lines of one item: each refund booked once (3 portions, Rp 45.000), a Z time taken as WIB (next day)",
       sorted((r["qty"], r["refund"], r["date"]) for r in lines_of("4a3f6c1e-uuid-0026")[1] if r["qty"] < 0), [(-2, 30000, d[4]), (-1, 15000, d[4])])

    # a checkout with neither uuid nor id: a later refund is not guessed by name, head office is told
    n1 = line("Tahu goreng pedas", "TH-PDS", 1, 18000)
    n1.pop("id")
    run(api, "moka_transaction_staged", event(91000027, d[5], [n1]))
    st, out = run(api, "moka_transaction_staged", event(91000027, d[5], [dict(n1, refunded_quantity=1)], refunded=True, refunded_at=f"{d[4]}T10:00:00+07:00"))
    check("checkout without uuid or id: no refund line guessed, head office told to book it",
          not [r for r in lines_of(91000027)[1] if r["qty"] < 0] and any("tanpa ID baris" in n.get("text", "") for n in out.get("notifications", [])),
          (lines_of(91000027)[1], out.get("notifications")))

    # a refund of a line still unmapped: mapped together with its sale line
    hq = CTX.get("hq")
    k = line("Es teh contoh", "MK-TEH", 2, 5000)
    run(api, "moka_transaction_staged", event(91000028, d[5], [k]))
    run(api, "moka_transaction_staged", event(91000028, d[5], [dict(k, refunded_quantity=1)], refunded=True, refunded_at=f"{d[4]}T11:00:00+07:00"))
    l28 = lines_of(91000028)[1]
    sale28 = [r for r in l28 if r["qty"] > 0]
    if hq and sale28:
        st, resp = hq.patch("moka_sale_line", sale28[0]["name"], {"item": PDS})
        l28 = lines_of(91000028)[1]
    eq("mapping the sale line maps its unmapped refund too (same item, staged)", sorted((r["qty"], r["item"], r["status"]) for r in l28),
       [(-1, PDS, "staged"), (2, PDS, "staged")])


def scenario_moka_recap_resilience(api):
    """Audit moka N3/L6 (a deleted item or an outlet without Moka stops nothing) and M6 (scans of moka_sale_line)."""
    hq = CTX.get("hq")
    today = kernel_today()
    d = [(today - datetime.timedelta(days=k)).isoformat() for k in range(0, 12)]

    def lines_of(tid):
        tx = api.find("moka_transaction", f"transaction_id == '{tid}'")
        return tx, (api.find("moka_sale_line", f"transaction == '{tx[0]['name']}'") if tx else [])

    def sales(outlet, day):
        return api.find("outlet_sales", f"outlet == '{outlet}' && date == '{day}' && source == 'moka' && key_version == 3 && docstatus != 2")

    # a staged line whose item the owner deleted, and an outlet that stopped using Moka: the recap of the other outlet
    # and days goes on, those lines go back to unmapped, head office is told
    st, it = api.create("item", {"item_name": "Tahu bakso contoh", "sku": "TH-BKS", "kind": "good", "stock_uom": PORSI_UOM, "rate": 20000})
    bks = (it or {}).get("name")
    check("an item to delete later", st == 201 and bks, (st, it))
    run(api, "moka_transaction_staged", event(91000022, d[10], [line("Tahu bakso contoh", "TH-BKS", 1, 20000), line("Tahu goreng original", "TH-ORI", 1, 15000)]))
    run(api, "moka_transaction_staged", event(91000023, d[9], [line("Tahu goreng original", "TH-ORI", 3, 15000)], outlet_id=70001, outlet_name="Tahu Contoh - Dago"))
    l23 = lines_of(91000023)[1]
    check("Dago takes Moka lines by its Moka name", len(l23) == 1 and l23[0]["outlet"] == DAGO and l23[0]["status"] == "staged", l23)
    st, resp = api.call("DELETE", f"/api/v1/r/item/{bks}") if bks else (0, None)
    # a kernel that refuses to delete a record a live record still links (rangka-os card t_5a50f661) answers 409
    # linked: the item stays and the recap runs as usual; an older kernel deletes it and the recap must survive that
    refused = st == 409 and ((resp or {}).get("error") or {}).get("code") == "linked"
    check("the owner deletes the item while its Moka line is staged: refused (409 linked) or allowed (older kernel)",
          refused or st in (200, 204), (st, resp))
    st, out = run(api, "moka_daily_sales")
    check("recap after the delete attempt: runs", st == 200, (st, out))
    eq("recap after the delete attempt: Dago, a later day, recapped too", [x["net_total"] for x in sales(DAGO, d[9])], [45000])
    if refused:
        eq("recap with the item kept: the day is recapped with it", [x["net_total"] for x in sales(BRAGA, d[10])], [35000])
    else:
        eq("recap with a deleted item: the day is recapped without it", [x["net_total"] for x in sales(BRAGA, d[10])], [15000])
        gone = [r for r in lines_of(91000022)[1] if r["description"] == "Tahu bakso contoh"]
        check("the line of the deleted item is unmapped again, item emptied, head office told",
              len(gone) == 1 and gone[0]["status"] == "unmapped" and gone[0]["item"] in ("", None)
              and any("Belum dipetakan" in n.get("text", "") for n in out.get("notifications", [])), (gone, out.get("notifications")))
        if gone:
            st, resp = hq.patch("moka_sale_line", gone[0]["name"], {"item": PDS})
            run(api, "moka_daily_sales")
            eq("mapped again: its own record for that day", sorted(x["net_total"] for x in sales(BRAGA, d[10])), [15000, 20000])

    # Dago stops using Moka (its Moka name emptied) with a staged line: back to unmapped without outlet, nothing stops
    run(api, "moka_transaction_staged", event(91000024, d[11], [line("Tahu goreng original", "TH-ORI", 1, 15000)], outlet_id=70001, outlet_name="Tahu Contoh - Dago"))
    run(api, "moka_transaction_staged", event(91000025, d[11], [line("Tahu goreng pedas", "TH-PDS", 1, 18000)]))
    o = api.get("outlet", DAGO)
    st, resp = api.patch("outlet", DAGO, {"moka_name": ""})
    check("Dago's Moka name emptied", st == 200, (st, resp))
    run(api, "moka_daily_sales")
    l24 = lines_of(91000024)[1]
    check("a line of an outlet without Moka: unmapped, outlet emptied, no source-moka record there",
          len(l24) == 1 and l24[0]["status"] == "unmapped" and l24[0]["outlet"] in ("", None) and not sales(DAGO, d[11]), (l24, sales(DAGO, d[11])))
    eq("Braga, same day: recapped", [x["net_total"] for x in sales(BRAGA, d[11])], [18000])
    api.patch("outlet", DAGO, {"moka_name": o.get("moka_name") or "Tahu Contoh - Dago"})

    # cost: a recap with nothing to do reads moka_sale_line once (the kernel reads a whole model per find)
    st, out = run(api, "moka_daily_sales")
    scans = [x for x in out.get("steps", []) if "moka_sale_line" in x and "find" in x]
    check("an idle recap reads moka_sale_line once", st == 200 and len(scans) == 1, (st, scans, out.get("steps", [])[:5]))


SCENARIOS = [scenario_moka_api, scenario_moka_late_refund, scenario_moka_recap_resilience]
