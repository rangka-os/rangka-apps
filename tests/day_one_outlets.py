"""Day-one scenarios of the outlet comparison table and the busy-hours heatmap (rangka-apps#115, rangka-os contract 0075).

Loaded by tests/day_one.py (bind() hands over its helpers). demo-ritel imports the Moka file a second time as receipts
by hour (import moka_hours) and reads dashboard banding_outlet_ritel; demo-kasir and demo-fnb read their own variants.
"""

H = {}


def bind(m):
    """Take the helpers of tests/day_one.py."""
    for k in ("ok", "fail", "gap", "check", "eq", "CTX", "Api", "dashboard", "num", "kernel_today", "rows_all", "import_commit", "ritel_gen", "CBR"):
        H[k] = getattr(m, k)
    globals().update(H)
    m.MATRIX["outlet_hour"] = {"head_office": {"read", "create", "import", "export"}}


def moka_receipts(g, today, since):
    """{(date, receipt no): [net, hour of its first row]} of the generated Moka file for days on and after since (a refund
    row joins its receipt), read back from the CSV."""
    import csv
    import datetime as dt
    import io
    out = {}
    for r in csv.DictReader(io.StringIO(g.moka_csv(today).decode())):
        d = dt.datetime.strptime(r["Date"], "%d-%m-%Y").date().isoformat()
        if d >= since:
            k = (d, r["Receipt Number"])
            cur = out.setdefault(k, [0, int(r["Time"][:2])])
            cur[0] += int(float(r["Net Sales"]))
    return {k: v[0] for k, v in out.items()}, {k: v[1] for k, v in out.items()}


def scenario_outlet_compare_numbers(api):
    """outlet_compare_numbers: the table's numbers equal the sum of the sales per outlet, to the rupiah."""
    import datetime
    g, today = ritel_gen(), CTX.get("today") or kernel_today()
    hq = CTX.get("hq") or api
    moka = g.moka_csv(today)
    pv, c = import_commit(hq, "moka_hours", "moka-item-details-cibiru.csv", moka, CBR, "Moka Cibiru receipts by hour")
    if not c:
        return
    since = (today - datetime.timedelta(days=30)).isoformat()
    want, _hours = moka_receipts(g, today, since)
    rows = [r for r in rows_all(api, "outlet_hour") if r["date"] >= since and r["outlet"] == CBR]
    eq("outlet_hour: one record per receipt of the file (30 days)", len(rows), len(want))
    eq("outlet_hour amounts sum to the file's Net Sales (30 days)", sum(r["amount"] for r in rows), sum(want.values()))
    ws = dashboard(api, "banding_outlet_ritel", 30)
    t = ws.get("compare")
    if not t:
        return
    by = {r["key"]: r for r in t["rows"]}
    row = by.get(CBR)
    if not check("banding_outlet_ritel: a row for Cibiru", row is not None, list(by)):
        return
    eq("table sales = sum of the Moka receipts of Cibiru", row["values"]["sales"], sum(want.values()))
    eq("table receipts = distinct receipts of Cibiru", row["values"]["receipts"], len(want))
    avg = sum(want.values()) / len(want)
    check("table average per receipt = sales / receipts (rounded)", abs(row["values"]["average"] - avg) < 1, (row["values"]["average"], avg))
    eq("table total sales = sum of the rows", t["total"]["values"]["sales"], sum(r["values"]["sales"] for r in t["rows"]))
    check("compare: previous_period gives every row a prev and a delta map", t.get("compare") is True and all("prev" in r and "delta" in r for r in t["rows"]), t["rows"][:1])
    eq("table columns", [col["name"] for col in t["columns"]], ["sales", "receipts", "average"])
    eq("sales column is shown as currency", t["columns"][0]["format"], "currency")


def scenario_peak_hours_heatmap(api):
    """peak_hours_heatmap: the fixtures' lunch and evening peaks (12 and 19) are the two busiest hours."""
    import datetime
    g, today = ritel_gen(), CTX.get("today") or kernel_today()
    since = (today - datetime.timedelta(days=30)).isoformat()
    ws = dashboard(api, "banding_outlet_ritel", 30)
    h = ws.get("busy_hours")
    if not h:
        return
    eq("heatmap has 24 hour columns 0 to 23", [c["key"] for c in h["cols"]], [str(i) for i in range(24)])
    check("heatmap rows are weekdays", 1 <= len(h["rows"]) <= 7 and all(r["label"] in ("Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu") for r in h["rows"]), h["rows"])
    per_hour = [sum((row[j] or 0) for row in h["counts"]) for j in range(24)]
    want = {}
    for hr in moka_receipts(g, today, since)[1].values():
        want[hr] = want.get(hr, 0) + 1
    eq("heatmap receipts per hour = the file's receipts per hour", per_hour, [want.get(i, 0) for i in range(24)])
    top2 = sorted(range(24), key=lambda i: -per_hour[i])[:2]
    eq("the two busiest hours are 12 and 19", sorted(top2), [12, 19])
    check("night hours stay empty cells (null), not zero", all(h["values"][i][3] is None for i in range(len(h["rows"]))), [r[3] for r in h["values"]])
    check("max is the busiest cell", h["max"] == max(v for row in h["values"] for v in row if v is not None), h["max"])
    # weekday of a day: a Saturday receipt lands in the Saturday row (the site timezone, not UTC)
    rows = rows_all(api, "outlet_hour", f"date >= '{since}'")
    sat = [r for r in rows if datetime.date.fromisoformat(r["date"]).isoweekday() == 6]
    row_sat = next((i for i, r in enumerate(h["rows"]) if r["label"] == "Sabtu"), None)
    if sat and row_sat is not None:
        eq("Saturday row counts the Saturday receipts", sum(h["counts"][row_sat]), len(sat))


def scenario_banding_fnb(api):
    """The F&B comparison dashboard answers with no Moka hours yet: empty table and an empty heatmap, no error."""
    ws = dashboard(api, "banding_outlet_fnb", 30)
    if ws:
        check("banding_outlet_fnb: empty table and heatmap", ws["compare"]["rows"] == [] and not any(v is not None for row in ws["busy_hours"]["values"] for v in row), ws)


def scenario_banding_kasir(api):
    """The cashier's comparison reads pos_sale (its own hour and weekday): sums equal the paid receipts."""
    today = CTX.get("today") or kernel_today()
    sales = [s for s in rows_all(api, "pos_sale", "docstatus == 1")]
    ws = dashboard(api, "banding_outlet_kasir", 30)
    t, h = ws.get("compare"), ws.get("busy_hours")
    if not t or not sales:
        check("demo-kasir has paid receipts to compare", bool(sales), len(sales))
        return
    by = {}
    for s in sales:
        by.setdefault(s["outlet"], []).append(s)
    for outlet, ss in by.items():
        row = next((r for r in t["rows"] if r["key"] == outlet), None)
        check(f"banding_outlet_kasir: a row for {outlet}", row is not None, [r["key"] for r in t["rows"]])
        if row:
            eq(f"{outlet}: sales = sum of grand_total", row["values"]["sales"], sum(s["grand_total"] for s in ss))
            eq(f"{outlet}: receipts = number of paid receipts", row["values"]["receipts"], len(ss))
    eq("kasir heatmap counts every paid receipt once", sum(c for row in h["counts"] for c in row), len(sales))
    bad = [(s["name"], s.get("sold_at"), s.get("hour"), s.get("weekday")) for s in sales if s.get("hour") is None or not s.get("weekday")]
    check("kasir receipts carry hour and weekday", not bad, bad[:3])


SCENARIOS_RITEL = [scenario_outlet_compare_numbers, scenario_peak_hours_heatmap]
SCENARIOS_FNB = [scenario_banding_fnb]
SCENARIOS_KASIR = [scenario_banding_kasir]
