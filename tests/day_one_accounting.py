"""Accounting (module accounting) on every site that installs it, after the site's own scenarios have run.

Loaded by tests/day_one.py (bind() hands over its helpers). The books are checked against the documents, not against
themselves: the profit and loss of the busiest month and the stock, receivable and payable balances are recomputed
here from the submitted documents (the manual calculation), then compared with the Laporan keuangan the flows fill.
Then: every voucher adds up to 0, a cancelled document has one reversal per line, the balance sheet balances, journal
lines cannot be written by hand (contract 0026), Tutup buku refuses dates it closed, and an opening balance comes in
through the import opening_balance_sheet.
"""
import calendar
import datetime
from collections import defaultdict

H = {}

STOCK_VOUCHERS = ("goods_receipt", "outlet_sales", "stock_adjustment", "stock_count", "stock_transfer",
                  "stock_transfer_receipt", "delivery_note", "sales_return", "production_batch")


def bind(m):
    for k in ("ok", "fail", "gap", "check", "eq", "CTX", "Api", "installed"):
        H[k] = getattr(m, k)
    globals().update(H)


def rows(api, model, where=""):
    """Every record of `model` the caller reads (paged), optionally filtered by a CEL `where`."""
    out, offset = [], 0
    while True:
        q = {"limit": 500, "offset": offset}
        if where:
            q["filter"] = where
        page = api.list(model, **q)
        out += page
        if len(page) < 500:
            return out
        offset += 500


def has(api, model):
    return installed(api, model)


def docs(api, model, where="docstatus == 1"):
    return rows(api, model, where) if has(api, model) else []


def month_of(rows_):
    """(first, last) day of the month with the most journal lines."""
    per = defaultdict(int)
    for r in rows_:
        per[r["posting_date"][:7]] += 1
    ym = max(per, key=per.get)
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{ym}-01", f"{ym}-{calendar.monthrange(y, m)[1]:02d}"


def expected_pl(api, d0, d1):
    """The month's profit and loss from the documents (what an accountant would add up by hand)."""
    inm = lambda d: d0 <= (d or "") <= d1
    e = defaultdict(int)
    for s in docs(api, "outlet_sales"):
        if inm(s["date"]):
            e["revenue"] += s["net_total"] - s["ppn_in_sales"]
            e["cogs"] += s["cogs_total"]
            e["tax_expense"] += s["pph_final"]
    for i in docs(api, "sales_invoice"):
        if inm(i["posting_date"]) and i["kind"] == "regular":
            e["revenue"] += i["subtotal"] - i["discount_amount"]
    for p in docs(api, "payment"):
        if inm(p["payment_date"]) and p["direction"] == "receive" and p["method"] == "sales_return":
            e["sales_returns"] += p["amount"]
    for d in docs(api, "delivery_note"):
        if inm(d["delivery_date"]):
            e["cogs"] += d["cogs_total"]
    moves = rows(api, "stock_move") if has(api, "stock_move") else []
    live = {(t, x["name"]) for t in STOCK_VOUCHERS for x in docs(api, t)}
    adj_reason = {a["name"]: a["reason"] for a in docs(api, "stock_adjustment")}
    for mv in moves:
        key = (mv["voucher_type"], mv["voucher"])
        if key not in live or not inm(mv["posting_date"]):
            continue
        if mv["voucher_type"] == "sales_return":
            e["cogs"] -= mv["value"]
        elif mv["voucher_type"] in ("stock_count", "production_batch") or (
                mv["voucher_type"] == "stock_adjustment" and adj_reason.get(mv["voucher"]) != "consignment_return"):
            e["stock_loss"] -= mv["value"]
    for t in docs(api, "timesheet"):
        if inm(t["work_date"]):
            e["direct_labour"] += t["cost_amount"] or 0
    for b in docs(api, "production_batch"):
        if inm(b["production_date"]):
            e["direct_labour"] += b["labour_cost"] or 0
    for x in docs(api, "project_expense"):
        if inm(x["expense_date"]) and not x.get("vendor_bill"):
            e["project_costs"] += x["amount"]
    for b in docs(api, "vendor_bill"):
        if not inm(b["bill_date"]):
            continue
        kind = bill_kind(b)
        if kind == "project":
            e["project_costs"] += b["dpp"]
        elif kind == "expense":
            e["operating_expenses"] += b["dpp"]
    for t in docs(api, "outlet_takings"):
        if inm(t["date"]):
            e["operating_expenses"] += t["fee_total"] - t["difference_total"]
    e["net_revenue"] = e["revenue"] - e["sales_returns"]
    e["net_profit"] = (e["net_revenue"] - e["cogs"] - e["stock_loss"] - e["direct_labour"] - e["project_costs"]
                       - e["operating_expenses"] - e["tax_expense"])
    return e


def bill_kind(b):
    p = b.get("posting") or ""
    if p:
        return p
    if b.get("goods_receipt") or b.get("purchase_order"):
        return "stock"
    return "project" if b.get("project") else "expense"


def expected_balances(api, d1):
    """Stock, receivable and payable balances at d1 from the documents."""
    upto = lambda d: (d or "") <= d1
    live = {(t, x["name"]) for t in STOCK_VOUCHERS for x in docs(api, t)}
    adj_reason = {a["name"]: a["reason"] for a in docs(api, "stock_adjustment")}
    inv, consign_back = 0, 0
    for mv in (rows(api, "stock_move") if has(api, "stock_move") else []):
        if (mv["voucher_type"], mv["voucher"]) in live and upto(mv["posting_date"]):
            inv += mv["value"]
            if mv["voucher_type"] == "stock_adjustment" and adj_reason.get(mv["voucher"]) == "consignment_return":
                consign_back += mv["value"]
    recv = 0
    for i in docs(api, "sales_invoice"):
        if upto(i["posting_date"]):
            recv += i["amount_due"] + i["retention_amount"]
            if i["kind"] == "retention":
                recv -= i["subtotal"] - i["discount_amount"]
    pay = 0
    opening_vendors = {v["name"] for v in docs(api, "vendor", "vendor_name == 'Saldo awal'")}
    for g in docs(api, "goods_receipt"):
        if upto(g["received_on"]) and g["vendor"] not in opening_vendors:
            pay += g["total_value"]
    pay += consign_back   # consigned goods sent back clear the unbilled payable (their moves are negative)
    for b in docs(api, "vendor_bill"):
        if upto(b["bill_date"]):
            pay += b["amount_due"] - (b["dpp"] if bill_kind(b) == "stock" else 0)
    for p in docs(api, "payment"):
        if not upto(p["payment_date"]):
            continue
        if p["direction"] == "receive":
            recv -= p["amount"] + p["withheld_total"]
        else:
            pay -= p["amount"] + p["withheld_total"]
    return {"inventory": inv, "receivables": recv, "trade_payables": pay}


def report(api, d0, d1, **extra):
    st, r = api.create("financial_report", dict(period_from=d0, period_to=d1, **extra))
    return st, (r or {}).get("record", r) or {}


def scenario_accounting(api):
    """Module accounting: journals from the documents, Laporan keuangan against a manual calculation, Tutup buku,
    saldo awal, append-only lines."""
    if not has(api, "gl_entry"):
        fail("accounting: module installed (gl_entry)", "gl_entry is not a model of this site")
        return
    gl = rows(api, "gl_entry")
    check("accounting: documents of the scenarios wrote journal lines", len(gl) > 0, len(gl))
    if not gl:
        return
    # every voucher (document and post/reverse) adds up to 0, and has at least two lines
    per = defaultdict(list)
    for r in gl:
        per[(r["voucher_type"], r["voucher"], r["entry"])].append(r)
    unbalanced = [(k, sum(x["amount"] for x in v)) for k, v in per.items() if sum(x["amount"] for x in v) != 0 or len(v) < 2]
    check(f"accounting: each of the {len(per)} vouchers balances (debit = kredit)", not unbalanced, unbalanced[:5])
    # a cancelled document: one reverse line per post line, on the same date, the voucher nets to 0
    bad = []
    for (t, v, e), lines in per.items():
        if e != "reverse":
            continue
        post = per.get((t, v, "post"), [])
        if sorted((x["account"], x["amount"], x["posting_date"]) for x in post) != sorted((x["account"], -x["amount"], x["posting_date"]) for x in lines):
            bad.append((t, v))
    rev = [k for k in per if k[2] == "reverse"]
    check(f"accounting: {len(rev)} cancelled documents reversed line by line on their own date", not bad, bad[:5])
    still = [(t, v) for (t, v, e) in per if e == "post" and (t, v, "reverse") not in per
             and (api.get(t, v) or {}).get("docstatus") == 2]
    check("accounting: no cancelled document keeps its journal", not still, still[:5])

    d0, d1 = month_of(gl)
    st, rep = report(api, d0, d1)
    if not check(f"accounting: Laporan keuangan {d0}..{d1} created", st in (200, 201) and rep.get("computed_at"), (st, rep)):
        return
    exp = expected_pl(api, d0, d1)
    for f in ("revenue", "sales_returns", "net_revenue", "cogs", "stock_loss", "direct_labour", "project_costs",
              "operating_expenses", "tax_expense", "net_profit"):
        eq(f"accounting: {f} {d0[:7]} = manual calculation from the documents", rep.get(f), exp[f])
    check(f"accounting: the month has revenue ({rep.get('net_revenue')})", (rep.get("net_revenue") or 0) > 0, rep.get("net_revenue"))
    eq("accounting: balance sheet balances (aset = liabilitas + ekuitas)", (rep.get("balanced"), rep.get("difference")), (True, 0))
    bal = expected_balances(api, d1)
    for f in ("inventory", "receivables", "trade_payables"):
        eq(f"accounting: {f} at {d1} = documents", rep.get(f), bal[f])
    eq("accounting: current profit of the whole business = net profit (no outlet filter)", rep.get("current_profit"), rep.get("net_profit"))
    eq("accounting: closing cash = opening + in - out", rep.get("cash_closing"), rep.get("cash_opening", 0) + rep.get("cash_in", 0) - rep.get("cash_out", 0))
    print(f"accounting {CTX.get('site')} {d0[:7]}: pendapatan {rep.get('net_revenue'):,} HPP {rep.get('cogs'):,} laba bersih {rep.get('net_profit'):,} "
          f"aset {rep.get('total_assets'):,} liabilitas {rep.get('total_liabilities'):,} ekuitas {rep.get('total_equity'):,}")

    # profit and loss of one outlet: its outlet sales only
    sales = [s for s in docs(api, "outlet_sales") if d0 <= s["date"] <= d1]
    if sales:
        per_outlet = defaultdict(int)
        for s in sales:
            per_outlet[s["outlet"]] += s["net_total"] - s["ppn_in_sales"]
        outlet = max(per_outlet, key=per_outlet.get)
        inv_outlet = sum(i["subtotal"] - i["discount_amount"] for i in docs(api, "sales_invoice")
                         if d0 <= i["posting_date"] <= d1 and i["kind"] == "regular" and i.get("outlet") == outlet)
        st, ro = report(api, d0, d1, outlet=outlet)
        eq(f"accounting: revenue of outlet {outlet} = its sales", ro.get("revenue"), per_outlet[outlet] + inv_outlet)
        eq("accounting: the balance sheet stays the whole business with an outlet filter", ro.get("total_assets"), rep.get("total_assets"))

    # saving the report again recomputes it (after new documents)
    st, again = api.patch("financial_report", rep["name"], {"period_to": d1})
    eq("accounting: saving a report again recomputes the same numbers", ((again or {}).get("record", again) or {}).get("net_profit"), rep.get("net_profit"))

    # append-only: not even the owner writes a journal line by hand
    st, resp = api.create("gl_entry", {"posting_date": d1, "account": "11100", "amount": 1000, "voucher_type": "manual",
                                       "voucher": "X", "entry": "post", "line": "1"})
    eq("accounting: a journal line cannot be created by hand (409 append_only)", (st, (resp or {}).get("error", {}).get("code")), (409, "append_only"))
    st, resp = api.patch("gl_entry", gl[0]["name"], {"amount": 1})
    eq("accounting: a journal line cannot be edited", st, 409)
    st, resp = api.call("DELETE", f"/api/v1/r/gl_entry/{Api.q(gl[0]['name'])}")
    eq("accounting: a journal line cannot be deleted", st, 409)

    scenario_period_close_and_opening(api, gl)


def scenario_period_close_and_opening(api, gl):
    """Tutup buku locks the dates it closes; Saldo awal posts the opening balance sheet (import)."""
    first = min(r["posting_date"] for r in gl)
    day = (datetime.date.fromisoformat(first) - datetime.timedelta(days=40)).isoformat()   # before any document
    later = (datetime.date.fromisoformat(day) + datetime.timedelta(days=1)).isoformat()
    st, ob = api.create("opening_balance", {"opening_date": day, "cash": 5_000_000})
    ob = (ob or {}).get("record", ob) or {}
    st, s1 = api.verb("opening_balance", ob.get("name", "x"), "submit")
    check("accounting: a Saldo awal before any document submits", st == 200, (st, s1))
    obn = ((s1 or {}).get("record", s1) or {}).get("name", ob.get("name"))
    lines = rows(api, "gl_entry", f"voucher == '{obn}'")
    eq("accounting: Saldo awal kas 5 juta: Kas debit, Ekuitas saldo awal kredit",
       sorted((l["account"], l["amount"]) for l in lines), [("11100", 5_000_000), ("31900", -5_000_000)])
    st, pc = api.create("period_close", {"closed_until": day})
    pc = (pc or {}).get("record", pc) or {}
    st, s = api.verb("period_close", pc.get("name", "x"), "submit")
    check(f"accounting: Tutup buku sampai {day} submitted", st == 200, (st, s))
    pcn = ((s or {}).get("record", s) or {}).get("name", pc.get("name"))
    st, c = api.verb("opening_balance", obn, "cancel")
    msg = str(c)
    check("accounting: cancelling a document of a closed period is refused", st != 200 and "ditutup" in msg, (st, msg[:300]))
    st, ob2 = api.create("opening_balance", {"opening_date": day, "bank": 1_000})
    ob2 = (ob2 or {}).get("record", ob2) or {}
    st, s2 = api.verb("opening_balance", ob2.get("name", "x"), "submit")
    check("accounting: submitting a document dated in the closed period is refused", st != 200 and "ditutup" in str(s2), (st, str(s2)[:300]))
    st, ob3 = api.create("opening_balance", {"opening_date": later, "bank": 1_000})
    ob3 = (ob3 or {}).get("record", ob3) or {}
    st, s3 = api.verb("opening_balance", ob3.get("name", "x"), "submit")
    check("accounting: a document dated after the closed date still submits", st == 200, (st, str(s3)[:300]))
    st, c = api.verb("period_close", pcn, "cancel")
    check("accounting: cancelling Tutup buku opens the period", st == 200, (st, c))
    st, c = api.verb("opening_balance", obn, "cancel")
    check("accounting: after opening, the old document cancels", st == 200, (st, str(c)[:300]))
    lines = rows(api, "gl_entry", f"voucher == '{obn}'")
    eq("accounting: its reversal nets the voucher to 0 on the original date",
       (sum(l["amount"] for l in lines), {l["posting_date"] for l in lines}, len(lines)), (0, {day}, 4))
    ob3n = ((s3 or {}).get("record", s3) or {}).get("name", ob3.get("name"))
    api.verb("opening_balance", ob3n, "cancel")

    # import: Neraca awal from a spreadsheet arrives as a draft Saldo awal
    csv = ("Tanggal,Kas,Bank,Piutang,Aset tetap,Akumulasi penyusutan,Utang usaha,Utang bank,Utang lain,Modal\n"
           f"{day},2000000,15000000,0,40000000,10000000,3000000,20000000,0,20000000\n")
    st, pv = api.upload("/api/v1/imports/opening_balance_sheet", {}, "neraca-awal.csv", csv.encode())
    if not check("accounting: Neraca awal import preview without errors", st == 200 and pv.get("errors") == [], (st, pv)):
        return
    st, cm = api.call("POST", f"/api/v1/imports/opening_balance_sheet/{pv['id']}/commit")
    recs = (cm or {}).get("records", [])
    check("accounting: Neraca awal import commits one draft Saldo awal", st == 200 and len(recs) == 1, (st, cm))
    if not recs:
        return
    name = recs[0] if isinstance(recs[0], str) else recs[0].get("name")
    draft = api.get("opening_balance", name)
    eq("accounting: imported Saldo awal: equity difference = assets - liabilities - modal", draft.get("opening_equity"),
       2_000_000 + 15_000_000 + 40_000_000 - 10_000_000 - 3_000_000 - 20_000_000 - 20_000_000)
    st, s = api.verb("opening_balance", name, "submit")
    check("accounting: imported Saldo awal submits", st == 200, (st, str(s)[:300]))
    last = max(r["posting_date"] for r in rows(api, "gl_entry"))
    st, rep = report(api, day, last)
    eq("accounting: with the opening balance the balance sheet still balances", (rep.get("balanced"), rep.get("difference")), (True, 0))
    eq("accounting: fixed assets net of depreciation in the balance sheet", rep.get("fixed_assets_net"), 30_000_000)


SCENARIOS = [scenario_accounting]
