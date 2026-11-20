"""Day-one scenario: orders customers write on WhatsApp (module whatsapp_order) on site demo-whatsapp.

Runs after tests/day_one_whatsapp.py on the same kernel (its members, the open shift of Sari at Kedai Contoh Riau and
the business's webhook key that the host wrote). Each event reaches the kernel the way Rangka Cloud forwards it:
message_received signed with the business's key, with the lines Rangka Cloud's parser read from the text (rangka-cloud
internal/whatsapp/orderparse; the same texts are in its corpus, so the lines below are what it gives). Nothing is sent:
the reply stays in the kernel's job queue, where the scenarios read what would go to Rangka Cloud's relay.

Covered: "pesan 2 kopi susu, 1 roti" makes one draft priced from the item records (exact and partial names), the same
webhook twice makes nothing more, a second message within 30 minutes is added to the same draft and a chat line is no
order; number words, a typo (spelling skeleton), an unknown item (kept as a flagged line) and a drink in two sizes (a
choice, confirm refused until made) on a known customer; one-tap confirm creates the customer, the cashier's draft
sale and one reply (pesanan_diterima, items and the sale's total, never free text), cannot be undone, and paying the
sale takes the stock out like any sale; an opted-out number gets no reply; messages more than 30 minutes apart are two
drafts; no open shift refuses the confirm and leaves nothing behind; questions and greetings make no draft; the
kitchen cannot read orders; item names normalise the way Rangka Cloud's parser writes names.
"""
import datetime
import json
import time

H = {}
RIU, DGO = "OUT-03", "OUT-02"
KHT, ROTI = "BRG-0022", "BRG-0015"
RANI, DEWI, BUDI, EKA, FAJAR = "+6281200000011", "+6281200000001", "+6281200000002", "+6281200000012", "+6281200000013"


def bind(m):
    for k in ("ok", "fail", "check", "eq", "CTX", "Api"):
        H[k] = getattr(m, k)
    globals().update(H)


def wa():
    import day_one_whatsapp
    return day_one_whatsapp


def rec(x):
    return (x or {}).get("record", x) or {}


def L(qty, name, text, note="", given=True, unit=""):
    """One line as Rangka Cloud's parser writes it."""
    return {"qty": qty, "qty_given": given, "unit": unit, "name": name, "note": note, "text": text}


def iso(minutes=0):
    t = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


SEQ = [0]


def send(text, frm, lines, signal=True, notes=None, name="", at=None, mid=None):
    """POST message_received to the kernel, signed; returns (status, message id)."""
    SEQ[0] += 1
    mid = mid or f"wamid.WAO{int(time.time())}{SEQ[0]:04d}"
    ev = {"message_id": mid, "from": frm, "text": text, "kind": "text", "profile_name": name, "at": at or iso(),
          "lines": lines, "notes": notes or [], "order_signal": signal}
    CTX.setdefault("wao_events", {})[mid] = ev
    return wa().hook(CTX["kernel"], "message_received", ev, CTX["wa_webhook_key"]), mid


def inbound(api, mid):
    rows = api.find("whatsapp_inbound", f"message_id == '{mid}'")
    return rows[0] if len(rows) == 1 else ({"_count": len(rows)} if rows else None)


def wait_inbound(api, mid):
    return wa().wait_for(lambda: inbound(api, mid))


def order(api, name):
    return api.get("whatsapp_order", name) or {}


def orders_of(api, number):
    return api.find("whatsapp_order", f"wa_number == '{number.replace('+', '')}'", sort="first_message_at")


def lines_of(o):
    return [(l.get("qty"), l.get("item"), l.get("match"), l.get("note") or "") for l in o.get("lines") or []]


# ---------- scenarios

def scenario_wao_setup(api):
    """Head office adds the drinks the scenes order (one in two sizes), counts their stock and ticks Kedai Riau as the
    store that takes WhatsApp orders; Dewi is a customer already."""
    hq = CTX.get("hq")
    if not (hq and CTX.get("wa_webhook_key") and CTX.get("wa_shift")):
        fail("whatsapp order setup", "needs the WhatsApp scenarios first (members, shift, webhook key)")
        return
    made = {}
    for key, name, rate in (("susu", "Kopi Susu", 18000), ("reg", "Es Kopi Susu (Regular)", 20000), ("lrg", "Es Kopi Susu (Large)", 26000)):
        st, r = hq.create("item", {"item_name": name, "kind": "good", "stock_uom": "UOM-001", "rate": rate, "category": "KTG-001"})
        if not check(f"head office adds {name} -> 201", st == 201, (st, r)):
            return
        made[key] = rec(r)["name"]
    CTX["wao_items"] = made
    st, c = hq.create("stock_count", {"outlet": RIU, "items": [{"item": made["susu"], "counted_qty": 50}, {"item": made["reg"], "counted_qty": 20},
                                                               {"item": made["lrg"], "counted_qty": 20}]})
    if check("opening count of the drinks at Kedai Riau -> 201", st == 201, (st, c)):
        hq.verb("stock_count", rec(c)["name"], "submit")
    st, r = hq.patch("outlet", RIU, {"wa_orders": True})
    check("Kedai Riau takes WhatsApp orders (Terima pesanan WhatsApp) -> 200", st == 200 and rec(r).get("wa_orders") is True, (st, r))
    st, r = hq.create("customer", {"customer_name": "Dewi Contoh", "phone": "0812 0000 0001"})
    if check("customer Dewi -> 201", st == 201, (st, r)):
        CTX["wao_dewi"] = rec(r)["name"]


def scenario_wao_draft(api):
    """Rani, a new number, writes "pesan 2 kopi susu, 1 roti": one draft, priced from the records; the same webhook
    again changes nothing; "tambah 1 kopi hitam lagi ya" two minutes later joins the same draft; "makasih kak" is no order."""
    if not CTX.get("wao_items"):
        return
    st, mid = send("pesan 2 kopi susu, 1 roti", RANI, [L(2, "kopi susu", "pesan 2 kopi susu"), L(1, "roti", "1 roti")], name="Rani")
    eq("message_received from Rangka Cloud -> 200", st, 200)
    m = wait_inbound(api, mid)
    check("the message is kept once and started an order", m and m.get("result") == "order_new" and m.get("lines_read") == 2
          and m.get("lines_matched") == 2 and m.get("outlet") == RIU, m)
    if not m or not m.get("order"):
        return
    o = order(api, m["order"])
    CTX["wao_rani"] = o["name"]
    check("draft: Baru, Siap dikonfirmasi, Rani as written in WhatsApp, no customer yet, Kedai Riau",
          o.get("state") == "new" and o.get("stage") == "ready" and o.get("customer_name") == "Rani" and not o.get("customer")
          and o.get("outlet") == RIU and o.get("phone") == RANI, o)
    susu = CTX["wao_items"]["susu"]
    eq("lines: 2 Kopi Susu (same name), 1 Roti bakar cokelat (part of the name)", lines_of(o),
       [(2, susu, "exact", ""), (1, ROTI, "partial", "")])
    check("priced from the item records: 2 x 18.000 + 15.000 = 51.000",
          [l.get("rate") for l in o["lines"]] == [18000, 15000] and o.get("total") == 51000, o.get("lines"))
    check("nothing is ordered or sent before a person confirms: no sale, no reply, no customer",
          not o.get("sale") and not wa().calls_for(api, o["name"] + ":order_reply:1")
          and not api.find("customer", "wa_number == '6281200000011'"), o)

    st = wa().hook(CTX["kernel"], "message_received", CTX["wao_events"][mid], CTX["wa_webhook_key"])
    time.sleep(2)
    o2 = order(api, o["name"])
    check("the same webhook twice -> 200, still one message, one draft, two lines",
          st == 200 and (inbound(api, mid) or {}).get("_count") is None and len(orders_of(api, RANI)) == 1 and len(o2.get("lines") or []) == 2,
          (st, inbound(api, mid), o2.get("lines")))

    st, mid2 = send("tambah 1 kopi hitam lagi ya", RANI, [L(1, "kopi hitam", "tambah 1 kopi hitam lagi ya")], name="Rani", at=iso(2))
    m2 = wait_inbound(api, mid2)
    o3 = order(api, o["name"])
    check("a second message within 30 minutes joins the same draft: three lines, two messages, 66.000",
          m2 and m2.get("result") == "order_added" and m2.get("order") == o["name"] and len(orders_of(api, RANI)) == 1
          and lines_of(o3)[2] == (1, KHT, "exact", "") and o3.get("messages") == 2 and o3.get("total") == 66000, (m2, o3))
    st, mid3 = send("makasih kak", RANI, [], signal=False, name="Rani", at=iso(3))
    m3 = wait_inbound(api, mid3)
    check("'makasih kak' is no order: kept as a message, the draft unchanged",
          m3 and m3.get("result") == "not_order" and not m3.get("order") and len(order(api, o["name"]).get("lines") or []) == 3, m3)


def scenario_wao_harder(api):
    """Dewi (a known customer) writes number words, a typo, an item the shop does not sell and a drink in two sizes."""
    items = CTX.get("wao_items")
    if not items:
        return
    text = "dua es kopi susu, kopi suus 1, nasi goreng 2, roti bakar coklat 1 less sugar"
    st, mid = send(text, DEWI, [L(2, "es kopi susu", "dua es kopi susu"), L(1, "kopi suus", "kopi suus 1"),
                                L(2, "nasi goreng", "nasi goreng 2"), L(1, "roti bakar coklat", "roti bakar coklat 1 less sugar", note="less sugar")],
                   notes=["ambil jam 3"], name="dewi wa")
    m = wait_inbound(api, mid)
    if not check("Dewi's message started an order", m and m.get("result") == "order_new", m):
        return
    o = order(api, m["order"])
    CTX["wao_dewi_order"] = o["name"]
    check("a known number: linked to customer Dewi, with her name from the records (not the WhatsApp profile)",
          o.get("customer") == CTX.get("wao_dewi") and o.get("customer_name") == "Dewi Contoh", o)
    ls = o.get("lines") or []
    check("'dua es kopi susu': two sizes -> a choice, no item guessed",
          ls and ls[0].get("match") == "ambiguous" and not ls[0].get("item") and ls[0].get("qty") == 2
          and "Es Kopi Susu (Regular)" in ls[0].get("choices", "") and "Es Kopi Susu (Large)" in ls[0].get("choices", "")
          and ls[0].get("flag", "").startswith("Pilih salah satu"), ls[:1])
    check("'kopi suus': a typo read from the spelling -> Kopi Susu, flagged",
          len(ls) > 1 and ls[1].get("item") == items["susu"] and ls[1].get("match") == "typo" and ls[1].get("flag", "").startswith("Dibaca dari"), ls[1:2])
    check("'nasi goreng': not sold here -> kept as a flagged line without item or price",
          len(ls) > 2 and not ls[2].get("item") and ls[2].get("match") == "unknown" and ls[2].get("amount") == 0
          and "nasi goreng" in ls[2].get("flag", ""), ls[2:3])
    check("'roti bakar coklat' (spelt without e) -> Roti bakar cokelat, the note kept",
          len(ls) > 3 and ls[3].get("item") == ROTI and ls[3].get("match") == "typo" and ls[3].get("note") == "less sugar", ls[3:4])
    check("the card: Perlu dicek, '1 baris perlu dipilih, 1 baris tidak dikenali', the pick-up note kept",
          o.get("stage") == "check" and o.get("check_note") == "1 baris perlu dipilih, 1 baris tidak dikenali" and o.get("notes") == "ambil jam 3", o)

    sari = CTX["sari"]
    st, r = sari.patch("whatsapp_order", o["name"], {"state": "confirmed"})
    check("confirming with a choice open -> 422 confirm_choices_made, nothing made",
          st == 422 and any(e.get("rule") == "confirm_choices_made" for e in (r or {}).get("errors", []))
          and not sari.find("pos_sale", f"buyer == 'Dewi Contoh ({o['name']})'"), (st, r))
    rows = [dict(l) for l in ls]
    rows[0]["item"] = items["lrg"]
    st, r = sari.patch("whatsapp_order", o["name"], {"lines": rows})
    o = order(api, o["name"])
    check("Sari picks Large: no choice left, the unknown line stays as a note",
          st == 200 and o.get("open_choices") == 0 and o.get("check_note") == "1 baris tidak dikenali" and o.get("total") == 2 * 26000 + 18000 + 15000, (st, r, o))
    st, r = sari.patch("whatsapp_order", o["name"], {"state": "confirmed", "_if": {"state": "new"}})
    o = order(api, o["name"])
    check("one tap confirms -> 200: Dewi stays the customer (no new one), a draft sale of three lines (the unknown line left out)",
          st == 200 and o.get("state") == "confirmed" and o.get("customer") == CTX.get("wao_dewi") and o.get("sale")
          and len(api.find("customer", "wa_number == '6281200000001'")) == 1, (st, r, o))
    sale = api.get("pos_sale", o.get("sale") or "x") or {}
    check("the sale: 2 Es Kopi Susu (Large), 1 Kopi Susu, 1 Roti bakar cokelat; 85.000 from the records",
          [(l.get("item"), l.get("qty")) for l in sale.get("items") or []] == [(items["lrg"], 2), (items["susu"], 1), (ROTI, 1)]
          and sale.get("grand_total") == 85000 and sale.get("docstatus") == 0, sale)
    cs = wa().calls_for(api, o["name"] + ":order_reply:1")
    p = wa().params(cs[0]) if len(cs) == 1 else []
    check("the reply: the items and the sale's total",
          p == ["Dewi Contoh", o["name"], "Kedai Contoh Riau", "2 Es Kopi Susu (Large), 1 Kopi Susu, 1 Roti bakar cokelat", "Rp 85.000"], (cs, p))


def scenario_wao_confirm(api):
    """Sari taps Konfirmasi on Rani's card: the customer, the draft sale on her shift, one reply; no undo; paying the
    sale takes the stock out like any sale."""
    name, sari = CTX.get("wao_rani"), CTX.get("sari")
    if not (name and sari):
        return
    items = CTX["wao_items"]
    st, r = sari.patch("whatsapp_order", name, {"state": "confirmed", "_if": {"state": "new", "stage": "ready"}})
    o = order(api, name)
    if not check("Konfirmasi (the work screen's conditional PATCH) -> 200", st == 200 and o.get("state") == "confirmed", (st, r)):
        return
    cust = api.find("customer", "wa_number == '6281200000011'")
    check("a new customer Rani with her WhatsApp number, linked on the order",
          len(cust) == 1 and cust[0].get("customer_name") == "Rani" and o.get("customer") == cust[0]["name"], (cust, o))
    sale = api.get("pos_sale", o.get("sale") or "x") or {}
    check("a draft sale on Sari's open shift at Kedai Riau, buyer 'Rani (WAO-…)', the customer, WhatsApp receipt on",
          sale.get("docstatus") == 0 and sale.get("shift") == CTX["wa_shift"] and sale.get("outlet") == RIU
          and sale.get("buyer") == f"Rani ({name})" and sale.get("customer") == o.get("customer")
          and sale.get("wa_number") == "6281200000011" and sale.get("wa_receipt") is True, sale)
    check("the sale's lines are the order's, priced by the sale from the item: 66.000",
          [(l.get("item"), l.get("qty")) for l in sale.get("items") or []] == [(items["susu"], 2), (ROTI, 1), (KHT, 1)]
          and sale.get("grand_total") == 66000, sale.get("items"))
    st, today = api.call("GET", "/api/v1/r/whatsapp_order?filter=" + __import__("urllib.parse").parse.quote(
        "state == 'confirmed' && confirmed_date == today()") + "&limit=200")
    check("confirmed today in the site's date (confirmed_date, for lists and reports)",
          st == 200 and name in [r.get("name") for r in (today or {}).get("data", [])], (st, today))
    key = name + ":order_reply:1"
    m = wa().message(api, key)
    check("one reply row: Balasan pesanan WA, pesanan_diterima, queued, last four digits only, Rp 66.000",
          m and m.get("message_type") == "order_reply" and m.get("template") == "pesanan_diterima" and m.get("status") == "queued"
          and m.get("number_tail") == "…0011" and m.get("amount_text") == "Rp 66.000" and o.get("reply") == m.get("name")
          and o.get("reply_total") == 66000, (m, o))
    cs = wa().calls_for(api, key)
    p = wa().params(cs[0]) if len(cs) == 1 else []
    check("one call to Rangka Cloud: a template (never free text), to Rani's number, the items and the total",
          len(cs) == 1 and cs[0].get("to") == RANI and cs[0].get("template") == "pesanan_diterima" and cs[0].get("type") == "order_reply"
          and p == ["Rani", name, "Kedai Contoh Riau", "2 Kopi Susu, 1 Roti bakar cokelat, 1 Kopi hitam", "Rp 66.000"], (cs, p))
    st, r = sari.patch("whatsapp_order", name, {"state": "new"})
    check("Urungkan after confirm -> 422 (bad_step): a confirmed order stays, nothing more sent",
          st == 422 and order(api, name).get("state") == "confirmed" and len(wa().calls_for(api, key)) == 1, (st, r))

    def bal(item):
        rows = api.find("stock_balance", f"outlet == '{RIU}' && item == '{item}'")
        return rows[0].get("qty") if rows else None
    before = bal(items["susu"])
    st, paid = sari.verb("pos_sale", sale["name"], "submit")
    paid = rec(paid)
    check("Sari takes the payment at the counter -> 200, a receipt number", st == 200 and paid.get("name", "").startswith("STRK-RIU-"), (st, paid))
    check("stock of Kopi Susu at Kedai Riau down by 2, like any sale", before is not None and bal(items["susu"]) == before - 2, (before, bal(items["susu"])))
    check("the WhatsApp receipt follows the paid sale (module whatsapp_receipt)",
          bool(wa().calls_for(api, paid.get("name", "x") + ":receipt:1")), paid.get("name"))
    st, mid = send("pesan 1 kopi hitam", RANI, [L(1, "kopi hitam", "pesan 1 kopi hitam")], name="Rani", at=iso(4))
    m = wait_inbound(api, mid)
    check("Rani's next order after the confirm is a new draft", m and m.get("result") == "order_new" and m.get("order") != name
          and order(api, m["order"]).get("customer") == o.get("customer"), m)


def scenario_wao_opted_out(api):
    """Budi asked not to get WhatsApp messages: his order is confirmed, the reply is not sent (Tidak dikirim)."""
    sari = CTX.get("sari")
    if not (sari and CTX.get("wao_items")):
        return
    st, mid = send("pesan 1 kopi hitam", BUDI, [L(1, "kopi hitam", "pesan 1 kopi hitam")], name="Budi")
    m = wait_inbound(api, mid)
    if not check("Budi's order is a draft", m and m.get("result") == "order_new", m):
        return
    st, r = sari.patch("whatsapp_order", m["order"], {"state": "confirmed"})
    key = m["order"] + ":order_reply:1"
    row = wa().message(api, key)
    check("confirmed; the reply row says Tidak dikirim, Pelanggan minta tidak dikirimi WA, and nothing is queued",
          st == 200 and row and row.get("status") == "skipped" and row.get("reason") == "Pelanggan minta tidak dikirimi WA"
          and not wa().calls_for(api, key), (st, row))


def scenario_wao_window(api):
    """Eka writes twice 40 minutes apart: two drafts. A third message a minute after the second joins the second."""
    if not CTX.get("wao_items"):
        return
    st, a = send("pesan 1 kopi hitam", EKA, [L(1, "kopi hitam", "pesan 1 kopi hitam")], name="Eka", at=iso(-40))
    st, b = send("pesan 1 roti", EKA, [L(1, "roti", "pesan 1 roti")], name="Eka", at=iso(0))
    ma, mb = wait_inbound(api, a), wait_inbound(api, b)
    check("40 minutes apart: two drafts", ma and mb and ma.get("result") == "order_new" and mb.get("result") == "order_new"
          and ma.get("order") != mb.get("order") and len(orders_of(api, EKA)) == 2, (ma, mb))
    st, c = send("1 kopi susu", EKA, [L(1, "kopi susu", "1 kopi susu")], name="Eka", at=iso(1))
    mc = wait_inbound(api, c)
    check("a minute after the second: added to the second draft, not the first",
          mc and mc.get("result") == "order_added" and mc.get("order") == (mb or {}).get("order")
          and len(order(api, (ma or {}).get("order", "x")).get("lines") or []) == 1, mc)


def scenario_wao_no_shift(api):
    """Fajar's order is for Toko Dago, where no shift is open: head office's confirm is refused and leaves nothing."""
    hq = CTX.get("hq")
    if not (hq and CTX.get("wao_items")):
        return
    st, mid = send("pesan 2 kopi hitam", FAJAR, [L(2, "kopi hitam", "pesan 2 kopi hitam")], name="Fajar")
    m = wait_inbound(api, mid)
    if not (m and m.get("order")):
        fail("Fajar's draft", m)
        return
    st, r = hq.patch("whatsapp_order", m["order"], {"outlet": DGO})
    check("head office moves it to Toko Dago -> 200", st == 200, (st, r))
    st, r = hq.patch("whatsapp_order", m["order"], {"state": "confirmed"})
    check("confirm with no open shift at Toko Dago -> 422 no_shift", st == 422 and any(e.get("rule") == "no_shift" for e in (r or {}).get("errors", [])), (st, r))
    o = order(api, m["order"])
    check("nothing kept: the order is still new, no customer for Fajar, no reply",
          o.get("state") == "new" and not api.find("customer", "wa_number == '6281200000013'")
          and not wa().calls_for(api, m["order"] + ":order_reply:1"), o)


def scenario_wao_not_orders(api):
    """A question, a greeting and an item named without any order word make no draft."""
    if not CTX.get("wao_items"):
        return
    for text, lines, signal in (("halo kak, buka jam berapa?", [], False), ("kopi susu ada?", [], False),
                                ("kopi susu", [L(1, "kopi susu", "kopi susu", given=False)], False)):
        st, mid = send(text, "+6281200000014", lines, signal=signal, name="Gita")
        m = wait_inbound(api, mid)
        check(f"'{text}': kept as a message, no order", m and m.get("result") == "not_order" and not m.get("order"), m)
    eq("no draft for that number", len(orders_of(api, "+6281200000014")), 0)


def scenario_wao_visibility(api):
    """The kitchen does not read WhatsApp orders; the cashier reads her outlet's; the order screen is for cashier and head office."""
    tono, sari = CTX.get("tono"), CTX.get("sari")
    if not (tono and sari and CTX.get("wao_rani")):
        return
    st, _ = tono.call("GET", "/api/v1/r/whatsapp_order")
    eq("kitchen: no access to WhatsApp orders -> 403", st, 403)
    rows = sari.find("whatsapp_order", "true")
    check("Sari sees the orders of Kedai Riau", rows and all(r.get("outlet") == RIU for r in rows), [r.get("outlet") for r in rows])
    st, meta = sari.call("GET", "/api/v1/meta")
    screens = (meta or {}).get("screens") or {}
    names = list(screens) if isinstance(screens, dict) else [s.get("name") for s in screens]
    check("the work screen Pesanan dari WhatsApp is on Sari's list", "pesanan_wa" in names, names)


# The same form Rangka Cloud's orderparse.Normalize gives (rangka-cloud TestNormalizeVectors): an item named with
# brackets or punctuation is matched by the name a customer writes. Checked through the intake, one line per item.
NORMALIZE_VECTORS = {"Es Kopi Susu (Large)": "es kopi susu large", "Teh Tarik, Panas": "teh tarik panas",
                     "Nasi Goreng/Spesial": "nasi goreng spesial", "Kue: Bolu Pandan": "kue bolu pandan",
                     "Pisang Goreng 'Madu'": "pisang goreng madu"}


def scenario_wao_normalize(api):
    hq = CTX.get("hq")
    if not (hq and CTX.get("wao_items")):
        return
    want = {}
    for name, norm in NORMALIZE_VECTORS.items():
        found = api.find("item", f"item_name == \"{name}\"")
        if found:
            want[norm] = found[0]["name"]
            continue
        st, r = hq.create("item", {"item_name": name, "kind": "good", "stock_uom": "UOM-001", "rate": 10000})
        want[norm] = rec(r).get("name")
    st, mid = send("pesan", "+6281200000015", [L(1, n, n) for n in want], name="Hadi")
    m = wait_inbound(api, mid)
    o = order(api, (m or {}).get("order") or "x")
    got = {l.get("text"): (l.get("item"), l.get("match")) for l in o.get("lines") or []}
    check("each name as Rangka Cloud writes it matches its item exactly",
          all(got.get(n) == (want[n], "exact") for n in want), (got, want))


def scenario_wao_reference_lines(api):
    """The seam for catalogue carts (A1b): a line that names a product by reference (sku) is matched by the item's SKU
    only, a price the line carries is kept to compare and flagged when it differs; the price is the item's."""
    sari = CTX.get("sari")
    if not (sari and CTX.get("wao_items")):
        return
    lines = [{"qty": 2, "sku": "KSA", "price": 20000, "text": "Kopi susu aren x2"}, {"qty": 1, "sku": "TIDAK-ADA", "price": 5000, "text": "Produk lama x1"}]
    st, mid = send("", "+6281200000016", lines, name="Indra")
    m = wait_inbound(api, mid)
    o = order(api, (m or {}).get("order") or "x")
    ls = o.get("lines") or []
    check("a source recorded on the draft and the message (whatsapp_text today)",
          o.get("source") == "whatsapp_text" and (m or {}).get("source") == "whatsapp_text" and (m or {}).get("kind") == "text", (o, m))
    check("SKU KSA -> Kopi susu aren at its own price 22.000; the line's 20.000 kept and flagged",
          ls and ls[0].get("item") == "BRG-0021" and ls[0].get("match") == "exact" and ls[0].get("rate") == 22000
          and ls[0].get("quoted_rate") == 20000 and "beda dengan harga barang" in ls[0].get("flag", ""), ls[:1])
    check("an unknown SKU is never matched by name: unknown, flagged",
          len(ls) > 1 and not ls[1].get("item") and ls[1].get("match") == "unknown", ls[1:2])
    check("the card says Perlu dicek with the price difference",
          o.get("stage") == "check" and o.get("check_note") == "1 baris tidak dikenali, 1 baris beda harga" and o.get("total") == 44000, o)


SCENARIOS = [scenario_wao_setup, scenario_wao_draft, scenario_wao_harder, scenario_wao_confirm, scenario_wao_opted_out,
             scenario_wao_window, scenario_wao_no_shift, scenario_wao_not_orders, scenario_wao_normalize, scenario_wao_reference_lines, scenario_wao_visibility]
