"""Day-one scenarios of the pesan_meja piece (module self_order, rangka-apps#119) on demo-kasir: a guest at Kedai Riau
scans the QR of table A3, orders from the menu without an account, the cashier takes the order onto A3's bill and the
kitchen gets it, the guest's page follows the kitchen, and the bill is paid at the counter.

Loaded by tests/day_one.py after day_one_pos and day_one_kitchen (their members Sari, Dika, Tono and the open shift of
Sari at Kedai Riau): `python3 tests/day_one.py --site demo-kasir --only self_order`. The guest is a client without a
session: every guest request goes to /p/<org>/pesan/<token> (rangka-os contract 0073 §9).

Covered: QR links made (and rotated) by the owner, the menu shows only sellable items with title, price and category;
an order priced from the item records whatever the request says; refusals: wrong or rotated token, a page of another
type, unknown item, qty out of range, too many lines, a note too long, a fourth open order on one table, a flood
from one table (429); the guest's status list shows only the orders its own link made; cancel within 2 minutes;
Terima puts the lines on the table's open bill (or opens one) with line keys and sends them to the stations once;
Tolak; no step back; the guest's lines cannot be edited; kitchen progress reaches the guest; payment sends nothing to
the kitchen again; a cashier of another outlet does not see the order; no role creates an order directly.
"""
H = {}
RIU, DGO = "OUT-03", "OUT-02"
KHT, ROTI, KSA = "BRG-0022", "BRG-0015", "BRG-0021"
A3, A4, B2, B3 = "MJA-RIU-003", "MJA-RIU-004", "MJA-RIU-008", "MJA-RIU-009"
BAR, DAPUR = "STD-001", "STD-002"


def bind(m):
    for k in ("ok", "fail", "check", "eq", "CTX", "Api"):
        H[k] = getattr(m, k)
    globals().update(H)
    m.MATRIX.update({
        "self_order": {"head_office": {"read", "write", "export"}, "cashier": {"read", "write"}},
        "self_order_guard": {"head_office": {"create"}, "cashier": {"create"}},
    })


def rec(x):
    return (x or {}).get("record", x) or {}


def guest():
    """A client with no session: only the portal routes answer it."""
    return Api(CTX["kernel"].base, cookie="")


def qr(api, tables):
    st, r = api.call("POST", "/api/v1/portal/pesan/qr", {"records": tables})
    rows = {x["record"]: x for x in (r or {}).get("data", [])} if st == 200 else {}
    return st, rows


def order(g, path, lines, **extra):
    return g.call("POST", path + "/order", dict({"lines": lines}, **extra))


def data(g, path, mine=()):
    return g.call("GET", path + "/data" + ("?mine=" + ",".join(mine) if mine else ""))


def scenario_self_order_guest(api):
    """The guest's side: QR, menu, an order, every refusal, the status list and a cancel."""
    sari = CTX.get("sari")
    if not sari:
        fail("self_order: cashier Sari from day_one_pos", "missing")
        return
    st, links = qr(api, [A3, B2, B3])
    if not check("owner makes QR links for A3, B2, B3 -> 200 with a QR image each",
                 st == 200 and len(links) == 3 and all(str(x.get("qr", "")).startswith("data:image/png;base64,") for x in links.values()), (st, links)):
        return
    CTX["so_links"] = {k: v["path"] for k, v in links.items()}
    eq("the QR is named by the table", links[A3]["title"], "A3")
    st, l2 = api.call("POST", "/api/v1/portal/pesan/links", {"record": A3})
    check("a second link on A3 (another phone stand) -> 201", st == 201, (st, l2))
    CTX["so_a3b"] = (l2 or {}).get("path", "")
    st, r = sari.call("POST", "/api/v1/portal/pesan/qr", {"records": [A3]})
    check("a cashier cannot make QR links (share: head_office) -> 403", st == 403, (st, r))

    g = guest()
    a3 = CTX["so_links"][A3]
    st, page, ctype = g.html(a3)
    check("the QR opens the page shell -> 200, HTML that loads the order script", st == 200 and "text/html" in ctype and "/portal/order.js" in page, (st, ctype))
    st, d = data(g, a3)
    if not check("page data -> 200, type order_page, table A3", st == 200 and d.get("page", {}).get("type") == "order_page" and d.get("anchor", {}).get("title") == "A3", (st, d)):
        return
    menu = {m["name"]: m for m in d.get("menu", [])}
    items = {r["name"]: r for r in api.find("item", "true")}
    check("the menu has the kopi and the roti, at the item's rate", KHT in menu and ROTI in menu and menu[KHT]["price"] == items[KHT]["rate"], list(menu)[:5])
    check("no material is on the menu", all(items[n].get("kind") != "material" for n in menu if n in items), [n for n in menu if items.get(n, {}).get("kind") == "material"])
    check("a menu row carries only name, title, price, group and sold_out", all(set(m) == {"name", "title", "price", "group", "sold_out"} for m in menu.values()), list(menu.values())[:1])
    check("the group is the category's title, not its code", all(not str(m["group"]).startswith("KTG-") for m in menu.values()), [m["group"] for m in menu.values()][:3])

    # an order: two kopi "kurang manis" and a roti; the request's price is ignored
    st, r = order(g, a3, [{"item": KHT, "qty": 2, "note": "kurang manis", "rate": 1}, {"item": ROTI, "qty": 1}], guest="Budi")
    o1 = (r or {}).get("order", {})
    if not check("the guest's order -> 201, Menunggu kasir", st == 201 and o1.get("status") == "new" and o1.get("label") == "Menunggu kasir" and o1.get("can_cancel"), (st, r)):
        return
    so = api.get("self_order", o1["name"])
    eq("the order sits on A3 at Kedai Riau, made by the link", (so.get("table"), so.get("outlet"), str(so.get("created_by", "")).startswith("portal:")), (A3, RIU, True))
    eq("prices come from the item, never the request", so.get("total"), 2 * items[KHT]["rate"] + items[ROTI]["rate"])
    eq("the note and the guest name are kept", ([l.get("note") for l in so.get("items", [])], so.get("guest_name")), (["kurang manis", ""], "Budi"))
    CTX["so_o1"] = o1["name"]

    # refusals
    bad = a3[:-4] + ("AAAA" if not a3.endswith("AAAA") else "BBBB")
    n0 = len(api.find("self_order", "true"))
    st, r = order(g, bad, [{"item": KHT, "qty": 1}])
    check("a wrong token -> 404 and nothing written", st == 404 and len(api.find("self_order", "true")) == n0, (st, r))
    st, r = g.call("GET", a3.replace("/pesan/", "/tagihan/") + "/data")
    check("the table token on another page -> 404", st == 404, (st, r))
    b2 = CTX["so_links"][B2]
    st, r = order(g, b2, [{"item": "BRG-9999", "qty": 1}])
    check("an item not on the menu -> 409 order_item_unavailable", st == 409 and (r or {}).get("error", {}).get("code") == "order_item_unavailable", (st, r))
    st, r = order(g, b2, [{"item": KHT, "qty": 0}])
    check("qty 0 -> 400 order_qty", st == 400 and (r or {}).get("error", {}).get("code") == "order_qty", (st, r))
    st, r = order(g, b2, [{"item": KHT, "qty": 1}])
    check("a third send from B2 is still served -> 201", st == 201, (st, r))
    st, r = order(g, b2, [{"item": KHT, "qty": 1}])
    check("a fourth send from one table within a minute -> 429", st == 429, (st, r))
    b3 = CTX["so_links"][B3]
    st, r = order(g, b3, [{"item": KHT, "qty": 1}] * 31)
    check("31 lines -> 400 order_too_long", st == 400 and (r or {}).get("error", {}).get("code") == "order_too_long", (st, r))
    st, r = order(g, b3, [{"item": KHT, "qty": 1, "note": "x" * 141}])
    check("a note over 140 characters -> 400", st == 400, (st, r))
    eq("no refused request wrote an order (only B2's good one)", len(api.find("self_order", "true")), n0 + 1)

    # cancel within 2 minutes, then the open-order cap per table
    st, r = order(g, a3, [{"item": KSA, "qty": 1}])
    o2 = (r or {}).get("order", {}).get("name", "")
    st, r = g.call("POST", f"{a3}/order/{o2}/cancel")
    check("the guest cancels a fresh order -> 200, Dibatalkan tamu", st == 200 and (r or {}).get("order", {}).get("status") == "cancelled", (st, r))
    a3b = CTX["so_a3b"]
    st, r = order(g, a3b, [{"item": KSA, "qty": 1}])
    CTX["so_o3"] = (r or {}).get("order", {}).get("name", "")
    st, r = order(g, a3b, [{"item": KHT, "qty": 1}])
    CTX["so_o4"] = (r or {}).get("order", {}).get("name", "")
    check("orders 3 and 4 on A3 (two links) -> 201", bool(CTX["so_o3"] and CTX["so_o4"]), (st, r))
    st, r = order(g, a3b, [{"item": KHT, "qty": 1}])
    check("a fourth open order on A3 -> 409 order_too_many_open", st == 409 and (r or {}).get("error", {}).get("code") == "order_too_many_open", (st, r))
    st, r = g.call("POST", f"{b3}/order/{CTX['so_o1']}/cancel")
    check("another table's link cannot cancel the order -> 404", st == 404, (st, r))
    st, d = data(g, a3, [CTX["so_o1"], CTX["so_o4"], "SO-RIU-0000-00001"])
    eq("the status list shows only this link's orders", [o["name"] for o in d.get("orders", [])], [CTX["so_o1"]])


def scenario_self_order_cashier(api):
    """The cashier's side: Terima onto A3's bill and the kitchen, Tolak, no step back, locked lines, kitchen progress,
    a second order onto the same bill, payment, and the scope of another outlet."""
    sari, dika, tono, shift = CTX.get("sari"), CTX.get("dika"), CTX.get("tono"), CTX.get("shift_riu")
    o1, o3, o4 = CTX.get("so_o1"), CTX.get("so_o3"), CTX.get("so_o4")
    if not (sari and shift and o1 and o3 and o4):
        fail("self_order: guest scenario first", "missing orders")
        return
    g = guest()
    a3 = CTX["so_links"][A3]
    st, r = sari.create("self_order", {"table": A3, "items": [{"item": KHT, "qty": 1}]})
    check("no role creates a table order directly -> 403", st == 403, (st, r))
    if dika:
        st, r = dika.patch("self_order", o1, {"status": "accepted"})
        check("a cashier of Toko Dago cannot take a Kedai Riau order", st in (403, 404), (st, r))
    st, r = sari.patch("self_order", o1, {"items": [{"item": KHT, "qty": 9}]})
    check("the guest's lines cannot be edited -> refused (locked)", st != 200 and "locked" in str(r), (st, r))

    bills_before = api.find("pos_sale", f"table == '{A3}' && docstatus == 0")
    st, r = sari.patch("self_order", o1, {"status": "accepted"})
    if not check("Sari taps Terima -> 200", st == 200, (st, r)):
        return
    so = api.get("self_order", o1)
    bill = api.get("pos_sale", so.get("sale") or "") if so.get("sale") else {}
    sh = api.get("pos_shift", bill.get("shift") or "") if bill.get("shift") else {}
    check("A3 now has an open dine-in bill on Sari's open shift at Kedai Riau", bill.get("table") == A3 and bill.get("docstatus") == 0
          and sh.get("cashier") == "sari.riau@contohwarung.test" and sh.get("state") == "open" and sh.get("outlet") == RIU and bill.get("order_type") == "dine_in", (bills_before, bill.get("shift"), sh.get("cashier")))
    keys = sorted(l.get("line_key") for l in bill.get("items", []) if str(l.get("line_key", "")).startswith(o1 + ":"))
    eq("the bill has the guest's two lines with line keys", len(keys), 2)
    note = [l.get("note") for l in bill.get("items", []) if l.get("item") == KHT]
    eq("the note reaches the bill line", note, ["kurang manis"])
    tickets = api.find("kitchen_ticket", f"sale == '{bill.get('name')}'", children=1)
    by_station = {t["station"]: t for t in tickets}
    check("one ticket at Bar (kopi) and one at Dapur (roti), titled A3 and the order",
          set(by_station) == {BAR, DAPUR} and all(t.get("buyer") == f"A3 · {o1}" and t.get("status") == "new" for t in tickets), tickets)
    sent = {s.get("key") for s in (bill.get("sent_to_kitchen") or [])}
    check("the lines are recorded as sent to the kitchen", set(keys) <= sent, (keys, sent))
    st, d = data(g, a3, [o1])
    eq("the guest sees Diterima kasir", [o.get("label") for o in d.get("orders", [])], ["Diterima kasir"])
    st, r = sari.patch("self_order", o1, {"status": "new"})
    check("no step back after Terima -> refused (bad_step)", st != 200 and "bad_step" in str(r), (st, r))

    if tono and BAR in by_station:
        st, r = tono.patch("kitchen_ticket", by_station[BAR]["name"], {"status": "cooking"})
        check("the kitchen starts the kopi -> 200", st == 200, (st, r))
        st, d = data(g, a3, [o1])
        eq("the guest sees Sedang dimasak", [o.get("label") for o in d.get("orders", [])], ["Sedang dimasak"])
        st, r = tono.patch("kitchen_ticket", by_station[BAR]["name"], {"status": "ready"})
        st, d = data(g, a3, [o1])
        st2, r2 = tono.patch("self_order", o1, {"status": "rejected"})
        check("the kitchen cannot take or turn down an order -> refused", st2 != 200, (st2, r2))
        eq("the guest sees Siap diantar", [o.get("label") for o in d.get("orders", [])], ["Siap diantar"])

    st, r = sari.patch("self_order", o3, {"status": "rejected", "reject_reason": "Kopi susu aren habis"})
    check("Sari taps Tolak on another order -> 200, Ditolak kasir", st == 200 and rec(r).get("status") == "rejected", (st, r))
    rev0 = api.get("pos_sale", bill["name"]).get("rev", 0)
    st, r = sari.patch("self_order", o4, {"status": "accepted"})
    b = api.get("pos_sale", bill["name"])
    check("a second order lands on the same open bill, rev goes up", st == 200 and api.get("self_order", o4).get("sale") == bill["name"]
          and len(b.get("items", [])) == len(bill.get("items", [])) + 1 and b.get("rev", 0) == rev0 + 1, (st, r, b.get("rev")))
    eq("A3 still has one open bill", len(api.find("pos_sale", f"table == '{A3}' && docstatus == 0 && !merged")), 1)

    n_tickets = len(api.find("kitchen_ticket", f"sale == '{bill['name']}'"))
    st, r = sari.verb("pos_sale", bill["name"], "submit")
    check("the bill is paid at the counter -> 200", st == 200, (st, r))
    paid = rec(r).get("name") or bill["name"]
    eq("payment sends nothing to the kitchen again", len(api.find("kitchen_ticket", f"sale == '{paid}'")), n_tickets)
    eq("the order points at the paid receipt", api.get("self_order", o1).get("sale"), paid)

    st, links = qr(api, [A3])
    st2, r2 = data(g, a3)
    check("a new QR sheet for A3 retires the old sticker -> 410", st == 200 and st2 == 410, (st, st2, r2))
    st3, r3 = data(g, links[A3]["path"])
    check("the new sticker opens the menu -> 200", st3 == 200, st3)


SCENARIOS = [scenario_self_order_guest, scenario_self_order_cashier]
