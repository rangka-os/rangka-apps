"""Day-one scenarios of the owner automation pack: notices of contract 0066 step 5, the owner's summaries, the
reorder point with draft purchase orders, and the payroll reminder.

Loaded by tests/day_one.py (bind() hands over its helpers) and added to the sites that have the modules:
demo-grosir (reorder, invoice notices, summaries), demo-manpower (payroll reminder), demo-fnb (negative stock, summary
over daily outlet sales), demo-kasir (summary over cashier receipts).

Clock: the date flows take `event.as_of` (YYYY-MM-DD) from a run started by hand, so every date scenario runs on a
fixed day in 2031 with documents dated around it, whatever day the test runs. Expected figures come from the records
the API returns (an oracle in Python), never from the flow's own output. The cashier summary is the exception: a
receipt is always dated today (rule paid_today), so that scenario runs the summary for tomorrow.
"""
import datetime
import time
import urllib.parse

H = {}


def bind(m):
    for k in ("ok", "fail", "gap", "check", "eq", "CTX", "Api", "denied", "secrets", "messages", "approvals_of", "kernel_today"):
        H[k] = getattr(m, k)
    globals().update(H)


# ---------- helpers

MON = "2031-03-10"   # a Monday


def day(base, n):
    return (datetime.date.fromisoformat(base) + datetime.timedelta(days=n)).isoformat()


def rp(v):
    """The short rupiah text of the flows and of business_summary.message: 1,2 miliar; 12,5 juta; 850 ribu; 900."""
    v = int(v)
    if v >= 1_000_000_000:
        return f"{v // 1_000_000_000},{v // 100_000_000 % 10} miliar"
    if v >= 1_000_000:
        return f"{v // 1_000_000},{v // 100_000 % 10} juta"
    if v >= 1_000:
        return f"{v // 1_000} ribu"
    return str(v)


def run_flow(cl, name, body=None):
    return cl.call("POST", f"/api/v1/flows/{name}/run", body or {})


def inbox(cl, kind=None, link=None):
    """The caller's notices (newest first), optionally of one kind and one link."""
    st, resp = cl.call("GET", "/api/v1/notices?limit=200")
    rows = (resp or {}).get("data", []) if st == 200 else []
    return [n for n in rows if (kind is None or n.get("kind") == kind) and (link is None or n.get("link") == link)]


def room_for_notices(api, need=25):
    """The kernel sends at most 60 notices a minute per organisation and drops the rest (contract 0066 §3). The
    scenarios before these fire many in seconds (negative stock on every import), so a scenario that counts notices
    first makes sure the current minute has room for its own, and otherwise waits for the next minute."""
    now = datetime.datetime.now(datetime.timezone.utc)
    st, resp = api.call("GET", "/api/v1/system/audit?kind=notify.&limit=200")
    used = 0
    for e in (resp or {}).get("data", []) if st == 200 else []:
        try:
            at = datetime.datetime.fromisoformat(str(e.get("at", "")).replace("Z", "+00:00")).astimezone(datetime.timezone.utc)
        except ValueError:
            continue
        used += at.strftime("%Y-%m-%dT%H:%M") == now.strftime("%Y-%m-%dT%H:%M")
    if used + need > 50:
        time.sleep(61 - now.second)


def every(api, model, where):
    """All records matching a filter (pages of 200), or None when the model is not installed or not readable."""
    out, offset = [], 0
    while True:
        st, resp = api.call("GET", f"/api/v1/r/{model}?limit=200&offset={offset}&filter=" + urllib.parse.quote(where, safe=""))
        if st != 200:
            return None
        rows = resp.get("data", [])
        out += rows
        if len(rows) < 200:
            return out
        offset += 200


def submit(cl, model, name, label):
    st, resp = cl.verb(model, name, "submit", {"force": True})
    check(label, st == 200, (st, resp))
    return ((resp or {}).get("record") or resp or {}).get("name", name) if st == 200 else None


def rec_link(model, name):
    return f"/app/#/r/{model}/{name}"


# ---------- reorder point (demo-grosir)

def scenario_reorder_point(api):
    """Items below their reorder level: head office is told once per episode, and one DRAFT purchase order per supplier
    and outlet is made. Nothing is submitted or approved by the flows."""
    room_for_notices(api)
    hq, sm = CTX.get("hq"), CTX.get("sm")
    if not (hq and sm):
        fail("reorder: members", CTX.get("login_error", "members missing"))
        return
    uom = api.list("uom", limit=1)[0]["name"]
    outlets = sorted(o["name"] for o in api.list("outlet", limit=10))
    o1, o2 = outlets[0], outlets[1]
    st, ven = api.create("vendor", {"vendor_name": "PT Uji Pemasok Otomatis"})
    if not check("reorder setup: a supplier", st == 201, (st, ven)):
        return
    V = ven["name"]
    items = {}
    for key, level, qty, vendor in [("a", 10, 50, V), ("b", 5, 0, V), ("c", 8, 0, ""), ("d", 0, 0, V)]:
        st, it = api.create("item", {"item_name": f"Barang uji batas {key.upper()}", "kind": "good", "stock_uom": uom, "rate": 20_000,
                                     "reorder_level": level, "reorder_qty": qty, "default_vendor": vendor})
        if not check(f"reorder setup: item {key} (level {level}, order {qty}, supplier {'yes' if vendor else 'none'})", st == 201, (st, it)):
            return
        items[key] = it["name"]
    A, B, C, Dn = items["a"], items["b"], items["c"], items["d"]
    st, resp = api.patch("item", A, {"reorder_level": -1})
    check("a negative reorder level -> 422", st == 422 and any("negatif" in m for m in messages(resp)), (st, resp))

    def receive(outlet, lines, label):
        st, gr = api.create("goods_receipt", {"vendor": V, "outlet": outlet, "items": [{"item": i, "qty": q, "rate": r} for i, q, r in lines]})
        if check(f"reorder setup: {label}", st == 201, (st, gr)):
            return submit(api, "goods_receipt", gr["name"], f"submit {label}")

    receive(o1, [(A, 4, 12_000), (B, 2, 7_500), (C, 3, 900), (Dn, 1, 500)], f"goods at {o1}: A 4, B 2, C 3, D 1")
    receive(o2, [(A, 20, 12_000), (B, 1, 7_500)], f"goods at {o2}: A 20, B 1")
    mine = lambda: [r for r in (every(api, "stock_reorder", "name != ''") or []) if r["item"] in items.values()]
    pos_of = lambda: [p for p in (every(api, "purchase_order", f"vendor == '{V}'") or [])]
    low0 = len(inbox(api, "app.stock.stock_low"))
    pending0 = len((api.call("GET", "/api/v1/approvals?state=pending")[1] or {}).get("data", []))

    st, out = run_flow(hq, "stock_low_alert")
    check("head office cannot run the low stock flow by hand (403: the rows are the flow's)", denied(st, out), (st, out))
    st, resp = hq.create("stock_reorder", {"balance": "SB-000001", "outlet": o1, "item": A, "open": True})
    check("head office cannot make a reorder row by hand (403)", denied(st, resp), (st, resp))
    st, out = run_flow(api, "stock_low_alert")
    check("the owner runs stock_low_alert -> 200", st == 200, (st, out))
    rows = mine()
    eq("one open row per item and outlet below its level: (o1 A, B, C) and (o2 B); A at o2 (20 >= 10) and D (level 0) have none",
       sorted((r["outlet"], r["item"], r["open"]) for r in rows), sorted([(o1, A, True), (o1, B, True), (o1, C, True), (o2, B, True)]))
    by = {(r["outlet"], r["item"]): r for r in rows}
    ra, rb, rc = by.get((o1, A), {}), by.get((o1, B), {}), by.get((o1, C), {})
    eq("row A: stock 4, level 10, order the item's reorder quantity 50, at the average cost 12.000, for the supplier",
       (ra.get("qty"), ra.get("level"), ra.get("order_qty"), ra.get("rate"), ra.get("vendor")), (4, 10, 50, 12_000, V))
    eq("row B: no reorder quantity, so up to the level (5 - 2 = 3)", (rb.get("order_qty"), rb.get("rate")), (3, 7_500))
    eq("row C: no supplier", (rc.get("vendor"), rc.get("order_qty")), ("", 5))
    lows = inbox(api, "app.stock.stock_low")
    if check("the owner got one stock_low notice for the run (not one per item)", len(lows) == low0 + 1, (low0, len(lows))):
        n = lows[0]
        eq("it is a warning, names how many items are new and links to a reorder row",
           (n["severity"], n["title"], n["link"].startswith("/app/#/r/stock_reorder/ROP-")), ("warn", "4 barang di bawah batas stok minimum", True))
        check("its text names one item with its stock and level, and the rest as a count",
              "tinggal" in n["text"] and "batas minimum" in n["text"] and "dan 3 barang lain" in n["text"], n["text"])
    check("head office got the same notice (role in the declaration)", len(inbox(hq, "app.stock.stock_low")) >= 1, inbox(hq)[:2])
    check("the sales manager did not (not a role of the notice)", len(inbox(sm, "app.stock.stock_low")) == 0, inbox(sm)[:2])
    run_flow(api, "stock_low_alert")
    eq("a second run opens no row and tells nobody again", (len(mine()), len(inbox(api, "app.stock.stock_low"))), (4, low0 + 1))

    # draft purchase orders
    draft0 = len(inbox(api, "app.procurement.reorder_draft"))
    st, out = run_flow(hq, "reorder_draft_po")
    check("head office cannot run the draft flow by hand (403: it ticks the rows)", denied(st, out), (st, out))
    eq("the refused run left no purchase order behind", len(pos_of()), 0)
    st, out = run_flow(api, "reorder_draft_po")
    check("the owner runs reorder_draft_po -> 200", st == 200, (st, out))
    pos = pos_of()
    eq("one draft per supplier and outlet (o1, o2), none submitted", sorted((p["outlet"], p["docstatus"]) for p in pos), sorted([(o1, 0), (o2, 0)]))
    p1 = next((p for p in pos if p["outlet"] == o1), None)
    p2 = next((p for p in pos if p["outlet"] == o2), None)
    if p1 and p2:
        full = api.get("purchase_order", p1["name"])
        eq("the o1 draft orders A (50 at 12.000) and B (3 at 7.500); C has no supplier and D is not watched",
           sorted((ln["item"], ln["qty"], ln["rate"]) for ln in full["items"]), sorted([(A, 50, 12_000), (B, 3, 7_500)]))
        eq("its lines took the item's unit and name (defaults of a hand-made line)",
           sorted((ln["uom"], ln["description"]) for ln in full["items"]), sorted([(uom, "Barang uji batas A"), (uom, "Barang uji batas B")]))
        eq("the o2 draft orders B up to its level (5 - 1 = 4)", [(ln["item"], ln["qty"]) for ln in api.get("purchase_order", p2["name"])["items"]], [(B, 4)])
        check("the draft says it was made automatically and must be checked", "Dibuat otomatis" in full.get("notes", ""), full.get("notes"))
        eq("no approval request was made and nothing was received", (len(approvals_of(api, "purchase_order", p1["name"])), full["received_pct"]), (0, 0))
        note = inbox(api, "app.procurement.reorder_draft", rec_link("purchase_order", p1["name"]))
        if check("the owner got one reorder_draft notice linking to the o1 draft", len(note) == 1, inbox(api, "app.procurement.reorder_draft")[:3]):
            check("it names the supplier, the number of items and that nothing is sent before a person submits",
                  note[0]["title"] == "Draft pesanan pembelian untuk PT Uji Pemasok Otomatis" and note[0]["text"].startswith("2 barang di ")
                  and "tidak dikirim ke pemasok sebelum kamu ajukan" in note[0]["text"] and note[0]["severity"] == "warn", note[0])
        check("head office, who may read purchase orders, got it too", len(inbox(hq, "app.procurement.reorder_draft", rec_link("purchase_order", p1["name"]))) == 1, "")
        check("the sales manager did not", len(inbox(sm, "app.procurement.reorder_draft")) == 0, "")
    eq("one notice per draft", len(inbox(api, "app.procurement.reorder_draft")), draft0 + 2)
    eq("the rows with a supplier are ticked drafted, the one without is not",
       sorted((r["item"], r["outlet"], r["drafted"]) for r in mine()), sorted([(A, o1, True), (B, o1, True), (C, o1, False), (B, o2, True)]))
    eq("no approval is waiting that was not waiting before",
       len((api.call("GET", "/api/v1/approvals?state=pending")[1] or {}).get("data", [])), pending0)
    run_flow(api, "reorder_draft_po")
    eq("a second run makes no second draft", len(pos_of()), 2)
    if p2:
        st, resp = api.call("DELETE", f"/api/v1/r/purchase_order/{Api.q(p2['name'])}")
        run_flow(api, "stock_low_alert")
        run_flow(api, "reorder_draft_po")
        eq(f"deleting a draft ({st}) does not bring it back while the stock stays low: one draft per episode", len(pos_of()), 1)

    # the episode ends when the stock is back, and a new fall is a new episode
    receive(o1, [(A, 10, 12_000)], f"10 more of A at {o1} (14 >= 10)")
    run_flow(api, "stock_low_alert")
    eq("A's row is closed, the others stay open", sorted((r["item"], r["outlet"], r["open"]) for r in mine()),
       sorted([(A, o1, False), (B, o1, True), (C, o1, True), (B, o2, True)]))
    eq("closing a row tells nobody", len(inbox(api, "app.stock.stock_low")), low0 + 1)
    st, adj = api.create("stock_adjustment", {"outlet": o1, "reason": "damage", "items": [{"item": A, "qty": 6}]})
    if check("6 of A damaged (8 < 10)", st == 201, (st, adj)):
        submit(api, "stock_adjustment", adj["name"], "submit the adjustment")
        run_flow(api, "stock_low_alert")
        eq("a new episode: a second row for A, open, and one more notice",
           (sorted(r["open"] for r in mine() if r["item"] == A and r["outlet"] == o1), len(inbox(api, "app.stock.stock_low"))), ([False, True], low0 + 2))
        check("the new notice is about one item", inbox(api, "app.stock.stock_low")[0]["title"] == "1 barang di bawah batas stok minimum", inbox(api, "app.stock.stock_low")[0])
        run_flow(api, "reorder_draft_po")
        new = [p for p in pos_of() if p1 is None or p["name"] != p1["name"]]
        eq("and a new draft for A alone at o1", [[(ln["item"], ln["qty"]) for ln in api.get("purchase_order", p["name"])["items"]] for p in new], [[(A, 50)]])
    st, resp = api.patch("item", B, {"reorder_level": 0})
    run_flow(api, "stock_low_alert")
    eq("an item no longer watched (level 0) has its rows closed", sorted(r["open"] for r in mine() if r["item"] == B), [False, False])
    st, kinds = api.call("GET", "/api/v1/notices/prefs")
    by_kind = {k["kind"]: k for k in (kinds or {}).get("kinds", [])}
    eq("Pengaturan › Notifikasi lists the stock and purchasing kinds; negative stock is in the bell only by default (in_app step)",
       [(k, by_kind.get(k, {}).get("label"), by_kind.get(k, {}).get("mail")) for k in ("app.stock.stock_low", "app.stock.stock_negative", "app.procurement.reorder_draft")],
       [("app.stock.stock_low", "Stok di bawah batas minimum", True), ("app.stock.stock_negative", "Stok minus", False),
        ("app.procurement.reorder_draft", "Draft pesanan pembelian stok menipis", True)])


def scenario_negative_by_other_roles(api):
    """A sales manager's delivery note that leaves a balance below zero still goes through: the negative stock flow runs
    as the writer and reads nothing a sales manager may not read (audit owner-automation-pack 1)."""
    room_for_notices(api)
    sm = CTX.get("sm")
    if not sm:
        fail("negative stock by a sales manager: members", CTX.get("login_error", "members missing"))
        return
    uom = api.list("uom", limit=1)[0]["name"]
    outlet = sorted(o["name"] for o in api.list("outlet", limit=10))[0]
    cust = api.list("customer", limit=1)[0]["name"]
    st, it = api.create("item", {"item_name": "Barang uji kirim minus", "kind": "good", "stock_uom": uom, "rate": 10_000})
    if not check("negative by a sales manager setup: an item with no stock", st == 201, (st, it)):
        return
    st, so = api.create("customer_order", {"customer": cust, "po_number": "PO-UJI-MINUS", "po_date": "2026-11-03", "items": [{"item": it["name"], "qty": 5}]})
    if not check("setup: an order of 5", st == 201, (st, so)):
        return
    son = submit(api, "customer_order", so["name"], "setup: submit the order")
    st, dn = sm.create("delivery_note", {"customer_order": son, "outlet": outlet, "items": [{"item": it["name"], "qty": 2}]})
    if not check("the sales manager drafts a delivery note of 2 from an empty shelf", st == 201, (st, dn)):
        return
    st, resp = sm.verb("delivery_note", dn["name"], "submit", {"force": True})
    check("and submits it -> 200 (a negative balance is allowed and does not refuse the writer)", st == 200, (st, resp))
    bal = api.find("stock_balance", f"outlet == '{outlet}' && item == '{it['name']}'")
    eq("the balance is -2", [b["qty"] for b in bal], [-2])
    check("the owner is told", any(n["title"] == "Stok Barang uji kirim minus minus" for n in inbox(api, "app.stock.stock_negative")), inbox(api, "app.stock.stock_negative")[:2])


# ---------- invoices due and overdue (demo-grosir)

def open_invoices(api):
    rows = every(api, "sales_invoice", "docstatus == 1 && outstanding > 0")
    return [r for r in (rows or []) if r.get("due_date")]


def scenario_invoice_notices(api):
    """invoice_due_digest on a fixed clock: due today once a day, overdue only on a morning with a new overdue invoice."""
    room_for_notices(api)
    fm, sm = CTX.get("fm"), CTX.get("sm")
    if not (fm and sm):
        fail("invoice notices: members", CTX.get("login_error", "members missing"))
        return
    cust = api.list("customer", limit=1)[0]["name"]
    item = api.list("item", limit=1)[0]["name"]
    made = {}
    for key, due, qty in [("tue", day(MON, 1), 3), ("mon", MON, 10), ("sat", day(MON, -2), 4), ("old", day(MON, -18), 7)]:
        st, inv = api.create("sales_invoice", {"customer": cust, "posting_date": day(MON, -30), "due_date": due, "items": [{"item": item, "qty": qty, "rate": 125_000}]})
        if not check(f"invoice notices setup: invoice due {due}", st == 201, (st, inv)):
            return
        made[key] = submit(api, "sales_invoice", inv["name"], f"submit the invoice due {due}")
    if not all(made.values()):
        return
    CTX["auto_invoices"] = made
    due_k, late_k = "app.invoicing.invoice_due", "app.invoicing.invoice_overdue"

    def expect(as_of):
        rows = open_invoices(api)
        due = sorted((r for r in rows if r["due_date"] == as_of), key=lambda r: r["name"])
        late = sorted((r for r in rows if r["due_date"] < as_of), key=lambda r: r["due_date"])
        back = 2 if datetime.date.fromisoformat(as_of).weekday() == 0 else 1
        fresh = [r for r in late if (datetime.date.fromisoformat(as_of) - datetime.date.fromisoformat(r["due_date"])).days <= back]
        return due, late, fresh

    def morning(as_of, label):
        d0, l0 = len(inbox(api, due_k)), len(inbox(api, late_k))
        due, late, fresh = expect(as_of)
        st, out = run_flow(api, "invoice_due_digest", {"as_of": as_of})
        check(f"{label}: the flow runs for {as_of} -> 200", st == 200, (st, out))
        d1, l1 = inbox(api, due_k), inbox(api, late_k)
        eq(f"{label}: notices sent (due today {len(due)}, overdue {len(late)}, newly overdue {len(fresh)})",
           (len(d1) - d0, len(l1) - l0), (1 if due else 0, 1 if fresh else 0))
        if due and len(d1) > d0:
            eq(f"{label}: the due notice counts the invoices, gives what is owed and links to the first",
               (d1[0]["title"], d1[0]["text"].split(". ")[0], d1[0]["link"], d1[0]["severity"]),
               (f"Tagih hari ini: {len(due)} faktur jatuh tempo", f"Sisa tagihan Rp {rp(sum(r['outstanding'] for r in due))}", rec_link("sales_invoice", due[0]["name"]), "info"))
        if fresh and len(l1) > l0:
            oldest = late[0]
            days = (datetime.date.fromisoformat(as_of) - datetime.date.fromisoformat(oldest["due_date"])).days
            eq(f"{label}: the overdue notice gives the new ones, the whole position and the oldest invoice",
               (l1[0]["title"], l1[0]["text"], l1[0]["link"], l1[0]["severity"]),
               (f"{len(fresh)} faktur baru lewat jatuh tempo",
                f"Sekarang {len(late)} faktur lewat jatuh tempo, sisa tagihan Rp {rp(sum(r['outstanding'] for r in late))}. Paling lama {oldest['name']}, telat {days} hari.",
                rec_link("sales_invoice", oldest["name"]), "warn"))
        return due, late, fresh

    due, late, fresh = morning(MON, "Monday")
    check("Monday: the invoice due today is the one made for it, and Saturday's is newly overdue (the flow rests on Sunday)",
          made["mon"] in [r["name"] for r in due] and made["sat"] in [r["name"] for r in fresh] and made["old"] not in [r["name"] for r in fresh], (due, fresh))
    d0, l0 = len(inbox(api, due_k)), len(inbox(api, late_k))
    run_flow(api, "invoice_due_digest", {"as_of": MON})
    eq("Monday again: a second run of the same day tells nobody twice", (len(inbox(api, due_k)), len(inbox(api, late_k))), (d0, l0))
    check("finance got both notices, the sales manager neither (roles of the declaration)",
          len(inbox(fm, due_k)) >= 1 and len(inbox(fm, late_k)) >= 1 and not inbox(sm, due_k) and not inbox(sm, late_k), (len(inbox(fm, due_k)), len(inbox(sm, due_k))))
    due, late, fresh = morning(day(MON, 1), "Tuesday")
    check("Tuesday: Monday's invoice is the new overdue one", [r["name"] for r in fresh] == [made["mon"]], fresh)
    # paid in full before Wednesday: it leaves both lists
    st, pay = fm.create("payment", {"direction": "receive", "customer": cust, "method": "transfer", "payment_date": day(MON, 1),
                                    "amount": api.get("sales_invoice", made["tue"])["outstanding"],
                                    "allocations": [{"sales_invoice": made["tue"], "amount": api.get("sales_invoice", made["tue"])["outstanding"]}]})
    if check("finance records Tuesday's invoice as paid", st == 201, (st, pay)):
        submit(fm, "payment", pay["name"], "submit the payment")
        CTX["auto_payment_day"] = day(MON, 1)
    due, late, fresh = morning(day(MON, 2), "Wednesday")
    check("Wednesday: nothing is due and nothing is newly overdue (Tuesday's was paid), so nothing is sent", not due and not fresh, (due, fresh))
    st, kinds = api.call("GET", "/api/v1/notices/prefs")
    by_kind = {k["kind"]: k for k in (kinds or {}).get("kinds", [])}
    eq("Pengaturan › Notifikasi lists both kinds, mail on by default (the step's channel)",
       [(by_kind.get(k, {}).get("label"), by_kind.get(k, {}).get("severity"), by_kind.get(k, {}).get("mail")) for k in (due_k, late_k)],
       [("Faktur jatuh tempo hari ini", "info", True), ("Faktur lewat jatuh tempo", "warn", True)])
    check("the old implicit kind of the flow is gone (one kind per notice now)", "app.invoicing.invoice_due_digest" not in by_kind, sorted(by_kind))


# ---------- summaries

def summary_oracle(api, start, end, as_of):
    """What a summary run on as_of for the period start..end (both included) must hold, from the documents themselves."""
    between = lambda f: f"docstatus == 1 && {f} >= '{start}' && {f} <= '{end}'"
    inv = every(api, "sales_invoice", between("posting_date"))
    pos = every(api, "pos_sale", between("date"))
    daily = every(api, "outlet_sales", between("date"))
    takings = every(api, "outlet_takings", between("date"))
    paid = every(api, "payment", between("payment_date") + " && direction == 'receive' && method != 'sales_return'")
    owed = [r for r in open_invoices(api) if r["due_date"] <= day(as_of, 6)] if inv is not None else []
    low = every(api, "stock_reorder", "open")
    s = lambda rows, f: sum(r[f] for r in rows or [])
    return {
        # a receipt counts as sales before service charge, PBJT and rounding (rangka-apps#121 audit F6); cash_in keeps it whole
        "sales_total": s(inv, "grand_total") + sum(r["grand_total"] - (r.get("service_charge") or 0) - (r.get("pbjt") or 0) - (r.get("rounding") or 0)
                                                   for r in pos or []) + s(daily, "net_total"),
        "sales_count": len(inv or []) + len(pos or []) + len(daily or []),
        "cash_in": s(paid, "amount") + s(pos, "grand_total") + s(takings, "received_total"),
        "due_count": len([r for r in owed if r["due_date"] >= as_of]),
        "due_total": sum(r["outstanding"] for r in owed if r["due_date"] >= as_of),
        "overdue_count": len([r for r in owed if r["due_date"] < as_of]),
        "overdue_total": sum(r["outstanding"] for r in owed if r["due_date"] < as_of),
        "low_stock_count": len(low or []),
        "_invoicing": inv is not None, "_stock": low is not None,
    }


def summary_message(lead, want):
    text = f"{lead}: penjualan Rp {rp(want['sales_total'])} dari {want['sales_count']} transaksi, uang masuk Rp {rp(want['cash_in'])}."
    if want["_invoicing"]:
        text += (f" Jatuh tempo 7 hari ke depan: {want['due_count']} faktur (Rp {rp(want['due_total'])})."
                 f" Lewat jatuh tempo: {want['overdue_count']} faktur (Rp {rp(want['overdue_total'])}).")
    if want["_stock"]:
        text += f" Stok di bawah batas minimum: {want['low_stock_count']} barang."
    return text


def check_summary(api, flow, period, lead, as_of, start, end, label):
    """Run one summary flow for as_of and compare the record and the notice with the oracle. Returns the record."""
    kind = f"app.users.{period}_summary"
    n0 = len(inbox(api, kind))
    want = summary_oracle(api, start, end, as_of)
    st, out = run_flow(api, flow, {"as_of": as_of})
    if not check(f"{label}: {flow} runs for {as_of} -> 200", st == 200, (st, out)):
        return None
    rows = api.find("business_summary", f"summary_key == '{period}|{end}'")
    if not check(f"{label}: one {period} summary record for the period ending {end}", len(rows) == 1, rows):
        return None
    rec = rows[0]
    eq(f"{label}: period {start}..{end}", (rec["period"], rec["date_from"], rec["date_to"]), (period, start, end))
    eq(f"{label}: the figures are those of the documents (sales {want['sales_total']}, money in {want['cash_in']})",
       {k: rec.get(k) for k in want if not k.startswith("_")}, {k: v for k, v in want.items() if not k.startswith("_")})
    eq(f"{label}: the message is the short text of those figures", rec["message"], summary_message(lead, want))
    check(f"{label}: the message fits a notice (300 characters)", len(rec["message"]) <= 300, len(rec["message"]))
    notes = inbox(api, kind)
    if check(f"{label}: the owner got one notice", len(notes) == n0 + 1, (n0, len(notes))):
        eq(f"{label}: its title, text and link are the record's",
           (notes[0]["title"], notes[0]["text"], notes[0]["link"], notes[0]["severity"]), (rec["title"], rec["message"], rec_link("business_summary", rec["name"]), "info"))
    run_flow(api, flow, {"as_of": as_of})
    eq(f"{label}: a second run for the same day makes no second record and no second notice",
       (len(api.find("business_summary", f"summary_key == '{period}|{end}'")), len(inbox(api, kind))), (1, n0 + 1))
    return rec


def scenario_summaries(api):
    """The owner's daily, weekly and monthly summary on a fixed clock (demo-grosir), and the switches."""
    room_for_notices(api)
    fm, hq = CTX.get("fm"), CTX.get("hq")
    if not (fm and hq):
        fail("summaries: members", CTX.get("login_error", "members missing"))
        return
    cust = api.list("customer", limit=1)[0]["name"]
    item = api.list("item", limit=1)[0]["name"]
    sun = day(MON, -1)
    # a sale and a payment on the Sunday, so the day has figures of its own
    st, inv = api.create("sales_invoice", {"customer": cust, "posting_date": sun, "due_date": day(MON, 5), "items": [{"item": item, "qty": 12, "rate": 110_000}]})
    name = submit(api, "sales_invoice", inv["name"], "summary setup: an invoice issued on the Sunday") if check("summary setup: invoice", st == 201, (st, inv)) else None
    if not name:
        return
    st, pay = fm.create("payment", {"direction": "receive", "customer": cust, "method": "transfer", "payment_date": sun, "amount": 600_000,
                                    "allocations": [{"sales_invoice": name, "amount": 600_000}]})
    if check("summary setup: a part payment received on the Sunday", st == 201, (st, pay)):
        submit(fm, "payment", pay["name"], "submit the payment")
    rec = check_summary(api, "summary_daily", "daily", "Kemarin", MON, sun, sun, "daily")
    if rec is None:
        return
    check("daily: the Sunday's sale and payment are in it", rec["sales_count"] >= 1 and rec["cash_in"] >= 600_000 and rec["due_count"] >= 1, rec)
    eq("daily: the title names the day", rec["title"], f"Ringkasan harian {sun}")
    check("daily: the record links to the settings", bool(rec.get("setting")), rec)
    st, resp = fm.call("GET", "/api/v1/r/business_summary?limit=5")
    check("finance cannot read the owner's summaries (403)", st == 403, (st, resp))
    st, resp = hq.call("GET", f"/api/v1/r/summary_setting/{Api.q(rec.get('setting') or 'x')}")
    check("head office cannot read the summary settings (403)", st == 403, (st, resp))
    st, out = run_flow(fm, "summary_daily", {"as_of": day(MON, 40)})
    check("finance cannot run the summary flow (403)", denied(st, out), (st, out))
    check_summary(api, "summary_weekly", "weekly", "7 hari terakhir", MON, day(MON, -7), sun, "weekly")
    first = datetime.date.fromisoformat(MON).replace(day=1).isoformat()                     # 2031-03-01
    apr1 = "2031-04-01"
    check_summary(api, "summary_monthly", "monthly", "Sebulan terakhir", apr1, first, day(apr1, -1), "monthly")
    # the switches
    settings = api.list("summary_setting", limit=5)
    if check("one settings record, made by the first run with every summary on", len(settings) == 1 and all(settings[0].get(k) for k in ("daily", "weekly", "monthly")), settings):
        sname = settings[0]["name"]
        st, resp = api.create("summary_setting", {"daily": False})
        check("a second settings record is refused (one per business)", st in (409, 422), (st, resp))
        st, resp = api.patch("summary_setting", sname, {"daily": False})
        check("the owner switches the daily summary off", st == 200, (st, resp))
        n0 = len(inbox(api, "app.users.daily_summary"))
        st, out = run_flow(api, "summary_daily", {"as_of": day(MON, 1)})
        eq("switched off: the run makes no record and tells nobody",
           (st, len(api.find("business_summary", f"summary_key == 'daily|{MON}'")), len(inbox(api, "app.users.daily_summary"))), (200, 0, n0))
        check_summary(api, "summary_weekly", "weekly", "7 hari terakhir", day(MON, 7), MON, day(MON, 6), "weekly, daily off")
        api.patch("summary_setting", sname, {"daily": True})
        check_summary(api, "summary_daily", "daily", "Kemarin", day(MON, 1), MON, MON, "daily, switched on again")
    st, kinds = api.call("GET", "/api/v1/notices/prefs")
    by_kind = {k["kind"]: k for k in (kinds or {}).get("kinds", [])}
    eq("Pengaturan › Notifikasi lists the three summaries, mail on by default",
       [(by_kind.get(f"app.users.{p}_summary", {}).get("label"), by_kind.get(f"app.users.{p}_summary", {}).get("mail")) for p in ("daily", "weekly", "monthly")],
       [("Ringkasan harian", True), ("Ringkasan mingguan", True), ("Ringkasan bulanan", True)])
    st, sysinfo = api.call("GET", "/api/v1/system/status")
    sched = {x.get("name"): x.get("cron") for x in ((((sysinfo or {}).get("capabilities") or {}).get("scheduler")) or {}).get("schedules") or []}
    eq("the scheduler holds the morning flows (site time zone)",
       {k: sched.get(k) for k in ("stock_low_alert", "reorder_draft_po", "summary_daily", "summary_weekly", "summary_monthly", "invoice_due_digest")},
       {"stock_low_alert": "30 6 * * *", "reorder_draft_po": "40 6 * * *", "summary_daily": "0 7 * * *", "summary_weekly": "5 7 * * 1",
        "summary_monthly": "10 7 1 * *", "invoice_due_digest": "0 8 * * 1-6"})


def scenario_summary_today(api):
    """Sites whose sales are cashier receipts or daily outlet sales: the daily summary run for tomorrow holds today's
    documents (a receipt is always dated today), and one for a fixed day in 2031 holds that day's."""
    room_for_notices(api)
    today = kernel_today().isoformat()
    check_summary(api, "summary_daily", "daily", "Kemarin", day(today, 1), today, today, "today's sales")
    check_summary(api, "summary_daily", "daily", "Kemarin", MON, day(MON, -1), day(MON, -1), "a day without sales")


# ---------- negative stock (demo-fnb)

def scenario_stock_negative_notice(api):
    """A balance going below zero tells the owner and head office at once, once per balance and day; outlet staff of
    another outlet are not told."""
    room_for_notices(api)
    hq, dago, braga = CTX.get("hq"), CTX.get("staff_dago"), CTX.get("staff_braga")
    if not (hq and dago and braga):
        fail("negative stock notice: members", CTX.get("login_error", "members missing"))
        return
    uom = api.list("uom", limit=1)[0]["name"]
    outlet = sorted(o["name"] for o in api.list("outlet", limit=10))[1]    # Dago
    st, it = api.create("item", {"item_name": "Bahan uji stok minus", "kind": "material", "stock_uom": uom, "rate": 1_000})
    if not check("negative stock setup: an item", st == 201, (st, it)):
        return
    kind = "app.stock.stock_negative"
    st, resp = api.call("PUT", "/api/v1/notices/prefs", {"kinds": {kind: True}})
    check("the owner switches the mail of negative stock on (Pengaturan › Notifikasi; off by default)", st == 200, (st, resp))
    st, prefs = api.call("GET", "/api/v1/notices/prefs")
    eq("and the choice is kept", [k.get("mail") for k in (prefs or {}).get("kinds", []) if k.get("kind") == kind], [True])
    n0 = len(inbox(api, kind))
    for qty, label in [(2, "2 taken out of an empty shelf"), (1, "1 more the same day")]:
        st, adj = api.create("stock_adjustment", {"outlet": outlet, "reason": "damage", "items": [{"item": it["name"], "qty": qty}]})
        if check(f"negative stock: {label}", st == 201, (st, adj)):
            submit(api, "stock_adjustment", adj["name"], "submit the adjustment")
    bal = api.find("stock_balance", f"outlet == '{outlet}' && item == '{it['name']}'")
    if not check("the balance is below zero", len(bal) == 1 and bal[0]["qty"] == -3 and bal[0]["negative"], bal):
        return
    notes = inbox(api, kind)
    if check("the owner got one notice for the balance, however many moves kept it below zero that day", len(notes) == n0 + 1, (n0, len(notes))):
        eq("it is high and names the item (no link: the flow reads only what every writer may read)",
           (notes[0]["severity"], notes[0]["title"], notes[0]["link"]), ("high", "Stok Bahan uji stok minus minus", ""))
        check("its text names the outlet and the balance", "minus (-2)" in notes[0]["text"] and " di " in notes[0]["text"], notes[0]["text"])
    check("head office got it", len([n for n in inbox(hq, kind) if n["title"] == "Stok Bahan uji stok minus minus"]) == 1, inbox(hq, kind)[:2])
    check("outlet staff are not a role of the notice", not inbox(dago, kind) and not inbox(braga, kind), (inbox(dago, kind)[:1], inbox(braga, kind)[:1]))


# ---------- payroll reminder (demo-manpower)

def scenario_payroll_reminder(api):
    """payroll_reminder on a fixed clock: ready once when the cut-off passed and the recaps are in, due every day from
    three days before the pay date with what is missing. It never makes, submits or closes anything."""
    room_for_notices(api)
    fm, pa, sm = CTX.get("fm"), CTX.get("pa"), CTX.get("sm")
    if not (fm and pa and sm):
        fail("payroll reminder: members", CTX.get("login_error", "members missing"))
        return
    import day_one_payroll
    g = day_one_payroll.gen()
    st, prj = pa.create("project", {"project_name": "Alih daya uji pengingat", "customer": g.CLIENT, "state": "running", "billing_model": "time_material"})
    if not check("payroll reminder setup: a client contract", st == 201, (st, prj)):
        return
    P = prj["name"]
    start, end, pay = "2031-01-21", "2031-02-20", "2031-02-25"
    st, run = fm.create("payroll_run", dict(g.RUN, project=P, period_from=start, period_to=end, tax_year=2031, tax_month=2, pay_date=pay))
    if not check(f"payroll reminder setup: a run {start}..{end}, paid {pay}", st == 201, (st, run)):
        return
    R = run["name"]
    link = rec_link("payroll_run", R)
    ready_k, due_k = "app.payroll.payroll_ready", "app.payroll.payroll_due"
    counts = lambda cl=None: (len(inbox(cl or api, ready_k, link)), len(inbox(cl or api, due_k, link)))
    docs0 = (len(every(api, "payslip", "name != ''")), len(every(api, "payroll_close", "name != ''")), len(every(api, "sales_invoice", "name != ''")))

    def morning(as_of):
        room_for_notices(api, 20)   # the first morning also announces the open runs of the scenarios before this one
        st, out = run_flow(api, "payroll_reminder", {"as_of": as_of})
        check(f"payroll_reminder runs for {as_of} -> 200", st == 200, (st, out))
        return counts()

    eq("ten days before the cut-off: nothing about the run", morning("2031-02-10"), (0, 0))
    eq("the day after the cut-off, four days before the pay date, no slip yet: still nothing", morning("2031-02-21"), (0, 0))
    eq("three days before the pay date: a due notice, no ready notice (the run has no slip)", morning("2031-02-22"), (0, 1))
    n = inbox(api, due_k, link)[0]
    eq("it is a warning that names the contract and the days left", (n["severity"], n["title"]), ("warn", "Gaji Alih daya uji pengingat dibayar 3 hari lagi"))
    eq("and says what is ready and what is missing",
       n["text"], f"Tanggal bayar {pay}. Siap: 0 slip gaji sudah diajukan. Belum: 0 slip masih draft, 0 rekap absen belum diajukan, "
                  "0 rekap belum jadi slip, 0 rekap absen lapangan belum diajukan. Periode gaji belum ditutup.")
    eq("a second run the same morning tells nobody twice", morning("2031-02-22"), (0, 1))
    # a recap of one worker with pay data, still a draft
    workers = sorted(fm.list("worker", limit=200), key=lambda w: w.get("employee_no", ""))
    profiles = {p["worker"] for p in fm.list("payroll_profile", limit=200) if p.get("active")}
    w1 = next((w["name"] for w in workers if w["name"] in profiles), None)
    if not check("payroll reminder setup: a worker with pay data", bool(w1), workers[:2]):
        return
    st, asg = pa.create("assignment", {"project": P, "worker": w1, "start_date": start, "end_date": end, "day_rate": 300_000})
    check("payroll reminder setup: the worker is assigned to the contract", st == 201, (st, asg))
    st, ts = pa.create("timesheet", {"project": P, "worker": w1, "assignment": (asg or {}).get("name", ""), "work_date": start, "period_to": end, "days": 22,
                                     "description": "Rekap absen uji pengingat"})
    if not check("payroll reminder setup: a draft monthly recap", st == 201, (st, ts)):
        return
    eq("two days before: the due notice of the day", morning("2031-02-23"), (0, 2))
    check("it counts the recap that is still a draft", "1 rekap absen belum diajukan" in inbox(api, due_k, link)[0]["text"], inbox(api, due_k, link)[0]["text"])
    tsn = submit(pa, "timesheet", ts["name"], "the project admin submits the recap")
    slips = fm.find("payslip", f"payroll_run == '{R}'")
    if not check("the submitted recap became one draft slip of the run (payslip_from_recap)", [s["docstatus"] for s in slips] == [0], slips):
        return
    eq("one day before: ready (cut-off passed, recaps in, a slip to check) and the due notice of the day", morning("2031-02-24"), (1, 3))
    r = inbox(api, ready_k, link)[0]
    eq("the ready notice names the contract and the slips to check", (r["severity"], r["title"]), ("info", "Periode gaji Alih daya uji pengingat siap diproses"))
    check("its text gives the period and the slips", f"Periode {start} sampai {end}" in r["text"] and "1 draft menunggu diperiksa, 0 sudah diajukan" in r["text"], r["text"])
    check("the due notice now counts the draft slip", "Belum: 1 slip masih draft, 0 rekap absen belum diajukan" in inbox(api, due_k, link)[0]["text"], inbox(api, due_k, link)[0]["text"])
    eq("finance got the same notices; the sales manager, who may not read payroll, none", (counts(fm), counts(sm)), ((1, 3), (0, 0)))
    eq("on the pay date: ready is not repeated, due says today", morning(pay), (1, 4))
    eq("the title says today", inbox(api, due_k, link)[0]["title"], "Gaji Alih daya uji pengingat dibayar hari ini")
    eq("the day after the pay date: nothing more", morning("2031-02-26"), (1, 4))
    eq("the reminder made no slip, closed no run and made no invoice",
       (len(every(api, "payslip", "name != ''")) - 1, len(every(api, "payroll_close", "name != ''")), len(every(api, "sales_invoice", "name != ''"))), docs0)
    eq("the run is still open, marked as announced, and the slip still a draft",
       (api.get("payroll_run", R)["closed"], api.get("payroll_run", R)["ready_told"], fm.find("payslip", f"payroll_run == '{R}'")[0]["docstatus"]), (False, True, 0))
    st, resp = fm.patch("payroll_run", R, {"ready_told": False})
    check("finance cannot clear the mark by hand (read-only field)", api.get("payroll_run", R)["ready_told"] is True, (st, resp))
    st, out = run_flow(sm, "payroll_reminder", {"as_of": pay})
    check("a sales manager cannot run the reminder (403)", denied(st, out), (st, out))
    # leave nothing open that the later scenarios count: the draft slip goes, the recap is cancelled
    for s in fm.find("payslip", f"payroll_run == '{R}'"):
        fm.call("DELETE", f"/api/v1/r/payslip/{Api.q(s['name'])}")
    if tsn:
        pa.verb("timesheet", tsn, "cancel")
    st, kinds = api.call("GET", "/api/v1/notices/prefs")
    by_kind = {k["kind"]: k for k in (kinds or {}).get("kinds", [])}
    eq("Pengaturan › Notifikasi lists both payroll kinds, mail on by default",
       [(by_kind.get(k, {}).get("label"), by_kind.get(k, {}).get("severity"), by_kind.get(k, {}).get("mail")) for k in (ready_k, due_k)],
       [("Periode gaji siap diproses", "info", True), ("Tanggal bayar gaji sudah dekat", "warn", True)])


SCENARIOS = {
    "demo-grosir": [scenario_reorder_point, scenario_negative_by_other_roles, scenario_invoice_notices, scenario_summaries],
    "demo-manpower": [scenario_payroll_reminder],
    "demo-fnb": [scenario_stock_negative_notice, scenario_summary_today],
    "demo-kasir": [scenario_summary_today],
}
