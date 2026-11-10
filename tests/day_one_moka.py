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


def bind(m):
    for k in ("ok", "fail", "check", "eq", "CTX", "denied", "kernel_today"):
        H[k] = getattr(m, k)
    globals().update(H)
    # head office reads the Moka staging records and maps unmapped lines (write while not posted); nobody else sees them
    m.MATRIX["moka_transaction"] = {"head_office": {"read", "export"}}
    m.MATRIX["moka_sale_line"] = {"head_office": {"read", "write", "export"}}


def event(tid, day, checkouts, outlet_id=70002, outlet_name="Tahu Contoh - Braga", refunded=False, hour="11:42:10"):
    """One transaction_created event, as the moka connector emits it (emit keys of moka/connector.rk)."""
    total = sum(c["total_price"] - c["discount_amount"] for c in checkouts)
    return {"transaction_id": tid, "outlet_id": outlet_id, "outlet_name": outlet_name, "receipt_no": f"BRG-{tid % 100000:06d}",
            "created_at": f"{day}T{hour}+07:00", "is_refunded": refunded, "checkouts": checkouts, "payment_type": "cash",
            "total_collected": total}


def line(name, sku, qty, price, discount=0, refunded=0, variant=""):
    return {"item_id": 0, "item_name": name, "item_variant_name": variant, "sku": sku, "quantity": qty, "item_price": price,
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
    st, resp = api.patch("outlet", BRAGA, {"moka_outlet_id": "70002"})
    check("Braga gets its Moka outlet id", st == 200, (st, resp))

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

    # an item Juragan does not know: unmapped, the day waits
    ev3 = event(91000003, d1, [line("Es jeruk", "MK-JRK", 1, 8000)], hour="12:10:00")
    run(api, "moka_transaction_staged", ev3)
    tx3 = api.find("moka_transaction", "transaction_id == '91000003'")
    um = api.find("moka_sale_line", f"transaction == '{tx3[0]['name']}'") if tx3 else []
    check("unknown item: the line is unmapped", len(um) == 1 and um[0]["status"] == "unmapped" and um[0]["item"] in ("", None), um)

    # an outlet Juragan does not know: unmapped without outlet
    run(api, "moka_transaction_staged", event(91000004, d1, [line("Tahu goreng original", "TH-ORI", 1, 15000)], outlet_id=88888, outlet_name="Outlet lain"))
    tx4 = api.find("moka_transaction", "transaction_id == '91000004'")
    check("unknown outlet: transaction kept without outlet, line unmapped", len(tx4) == 1 and tx4[0]["outlet"] in ("", None)
          and all(r["status"] == "unmapped" for r in api.find("moka_sale_line", f"transaction == '{tx4[0]['name']}'")), tx4)

    # a sale of today and one two days ago
    run(api, "moka_transaction_staged", event(91000005, today.isoformat(), [line("Tahu goreng pedas", "TH-PDS", 1, 18000)]))
    run(api, "moka_transaction_staged", event(91000006, d2, [line("Tahu goreng pedas", "TH-PDS", 3, 18000)]))
    # three days ago: one known item and one unknown (no variant or refund keys in the checkout: they are optional)
    ev8 = event(91000008, d3, [line("Tahu goreng original", "TH-ORI", 1, 15000), line("Kerupuk", "MK-KRP", 2, 5000)])
    for c in ev8["checkouts"]:
        c.pop("item_variant_name"), c.pop("refunded_quantity"), c.pop("discount_amount")
    st, out = run(api, "moka_transaction_staged", ev8)
    tx8 = api.find("moka_transaction", "transaction_id == '91000008'")
    l8 = api.find("moka_sale_line", f"transaction == '{tx8[0]['name']}'") if tx8 else []
    check("checkout without variant, refund and discount keys: recorded, the day held by its unknown item",
          st == 200 and sorted((r["status"], r["day_held"]) for r in l8) == [("staged", True), ("unmapped", False)], (st, out, l8))

    # people: head office cannot type a Moka transaction or change a staged line
    st, resp = hq.create("moka_transaction", {"transaction_id": "x1"})
    check("head office cannot create a moka_transaction -> 403", denied(st, resp), (st, resp))
    if rows:
        st, resp = hq.patch("moka_sale_line", rows[0]["name"], {"item": ""})
        check("a staged line keeps its item (rule staged_has_outlet_and_item)", st == 422 and "staged_has_outlet_and_item" in str(resp), (st, resp))

    # the recap: the oldest past day per outlet that is not held (d3 waits for its mapping, so d2), today never
    st, out = run(api, "moka_daily_sales")
    check("recap runs", st == 200, (st, out))
    eq("recap: the held day three days ago is not taken", len(api.find("outlet_sales", f"outlet == '{BRAGA}' && date == '{d3}' && key_version == 3")), 0)
    s2 = api.find("outlet_sales", f"outlet == '{BRAGA}' && date == '{d2}' && source == 'moka' && key_version == 3")
    check("recap: two days ago becomes one draft for Braga, net Rp 54.000", len(s2) == 1 and s2[0]["docstatus"] == 0 and s2[0]["net_total"] == 54000, s2)
    um8 = [r for r in l8 if r["status"] == "unmapped"]
    if um8:
        st, resp = hq.patch("moka_sale_line", um8[0]["name"], {"item": ORI})
        check("head office maps the day's last unknown item: the day is free", st == 200 and not any(
            r["day_held"] for r in api.find("moka_sale_line", f"transaction == '{tx8[0]['name']}'")), (st, resp))
        run(api, "moka_daily_sales")
        s3 = api.find("outlet_sales", f"outlet == '{BRAGA}' && date == '{d3}' && key_version == 3")
        check("recap: three days ago after the mapping, net Rp 25.000", len(s3) == 1 and s3[0]["net_total"] == 25000, s3)
    eq("recap: today is not taken", len(api.find("outlet_sales", f"outlet == '{BRAGA}' && date == '{today.isoformat()}' && source == 'moka' && key_version == 3")), 0)

    # yesterday waits for the unmapped line; head office maps it
    st, out = run(api, "moka_daily_sales")
    eq("yesterday is held while a line is unmapped", len(api.find("outlet_sales", f"outlet == '{BRAGA}' && date == '{d1}' && key_version == 3")), 0)
    if um:
        st, resp = hq.patch("moka_sale_line", um[0]["name"], {"item": PDS})
        check("head office maps the unknown item", st == 200, (st, resp))
        eq("the mapped line is staged", api.get("moka_sale_line", um[0]["name"])["status"], "staged")
    st, out = run(api, "moka_daily_sales")
    s1 = api.find("outlet_sales", f"outlet == '{BRAGA}' && date == '{d1}' && source == 'moka' && key_version == 3")
    # 30000 + (36000 - 3000) + 15000 - 15000 + 8000 (mapped Es jeruk at Moka's price) = 71000
    check("recap: yesterday becomes one draft, net Rp 71.000, not flagged", len(s1) == 1 and s1[0]["net_total"] == 71000 and not s1[0].get("legacy_key_overlap"), s1)
    if s1:
        lines = api.find("moka_sale_line", f"sales == '{s1[0]['name']}'")
        check("its five lines are posted", len(lines) == 5 and all(r["status"] == "posted" for r in lines), lines)
        if lines:
            st, resp = hq.patch("moka_sale_line", lines[0]["name"], {"item": PDS})
            check("head office cannot change a posted Moka line -> 403", denied(st, resp), (st, resp))
        st, resp = hq.verb("outlet_sales", s1[0]["name"], "submit")
        check("head office submits the Moka day: stock out through the recipes", st == 200 and (resp or {}).get("cogs_total", 0) > 0, (st, resp))

    # a line that arrives after the day was recapped: a second record, flagged and held at submit
    run(api, "moka_transaction_staged", event(91000007, d1, [line("Tahu goreng original", "TH-ORI", 1, 15000)], hour="21:59:00"))
    run(api, "moka_daily_sales")
    late = api.find("outlet_sales", f"outlet == '{BRAGA}' && date == '{d1}' && key_version == 3 && docstatus == 0")
    check("late line: a second draft for the day, flagged", len(late) == 1 and late[0]["legacy_key_overlap"] is True, late)
    if late:
        st, resp = hq.verb("outlet_sales", late[0]["name"], "submit")
        check("the flagged draft cannot be submitted before head office checks it", st in (409, 422) and "legacy_key_reviewed" in str(resp), (st, resp))


SCENARIOS = [scenario_moka_api]
