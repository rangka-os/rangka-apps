"""Day-one scenario: a sales export of a POS without an import of its own (ESB is the example) goes through
scripts/pos_export_to_template.py and comes in with the import pos_sales_file, on demo-ritel.

Loaded by tests/day_one.py. The export is synthetic, in the layout of examples/pos-mappings/esb.example.json (not ESB's
real layout, which is not published: rangka reports/keputusan-riset/esb-connector.md), with the SKUs of the demo's
items. Covered: the converted file previews without errors and commits as one draft per day, a voided bill is not in
it, two lines of one item on one receipt arrive as one line with both quantities, the drafts' net sales equal the
totals the converter printed, and the converted file a second time is a duplicate.
"""
import datetime
import json
import os
import sys

H = {}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bind(m):
    for k in ("ok", "fail", "check", "eq", "CTX", "import_commit", "ritel_gen", "kernel_today"):
        H[k] = getattr(m, k)
    globals().update(H)


def scenario_pos_export(api):
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import pos_export_to_template as conv
    mapping = json.load(open(os.path.join(ROOT, "examples", "pos-mappings", "esb.example.json"), encoding="utf-8"))
    g, today = ritel_gen(), CTX.get("today") or kernel_today()
    hq = CTX.get("hq") or api
    st, o = api.create("outlet", {"outlet_name": "Toko Contoh Uji ESB", "code": "UJI-ESB", "sales_source": "file"})
    if not check("create an outlet whose sales come from a POS file", st == 201, (st, o)):
        return
    a, b, c = g.items()[0], g.items()[1], g.items()[2]
    d1, d2 = today - datetime.timedelta(days=2), today - datetime.timedelta(days=1)

    def rp(n):   # 1.250.000, as the example mapping reads numbers
        return f"{n:,}".replace(",", ".")

    def row(day, hhmm, bill, it, qty, disc=0, status="Paid"):
        return f"{day.strftime('%d/%m/%Y')} {hhmm};{bill};Uji ESB;{it['sku']};{it['item_name']};{qty};{rp(it['rate'] * qty)};{rp(disc)};{status}"

    lines = ["Sales Detail Report (synthetic)", f"Period: {d1.strftime('%d/%m/%Y')} - {d2.strftime('%d/%m/%Y')}",
             "Sales Date;Bill Number;Branch;Menu Code;Menu Name;Qty;Subtotal;Discount;Status",
             row(d1, "10:02", "E-0001", a, 2), row(d1, "10:02", "E-0001", b, 5, disc=500), row(d1, "10:02", "E-0001", a, 1),
             row(d1, "12:40", "E-0002", c, 4, status="Void"),
             row(d2, "09:30", "E-0003", c, 3), row(d2, "13:20", "E-0004", a, 1, status="Cancelled"),
             "", "TOTAL;;;;;16;0;500;"]
    try:
        out = conv.convert(("\n".join(lines) + "\n").encode(), mapping)
    except conv.Problem as e:
        fail("the synthetic export converts", str(e))
        return
    if not check("the export converts to one file for its one branch", list(out) == ["Uji ESB"], list(out)):
        return
    t = out["Uji ESB"]["totals"]
    net = a["rate"] * 3 + b["rate"] * 5 - 500 + c["rate"] * 3
    eq("the converter's totals: 3 rows, the voided and cancelled bills left out", (t["rows"], t["gross"] - t["discount"] - t["returns"]), (3, net))
    content = conv.template_csv(out["Uji ESB"]["rows"])
    pv, com = import_commit(hq, "pos_sales_file", conv.file_name("Uji ESB"), content, o["name"], "converted POS export (ESB example)")
    if not com:
        return
    eq("converted POS export: one draft per day (2)", len(com["records"]), 2)
    sales = sorted((api.get("outlet_sales", n) for n in com["records"]), key=lambda s: s["date"])
    eq("day 1: source file, two lines, the item sold on two lines of one receipt arrives once with both quantities",
       (sales[0]["source"], sorted(l["qty"] for l in sales[0]["lines"])), ("file", [3, 5]))
    eq("day 2: one line; the cancelled bill is not in it", [l["qty"] for l in sales[1]["lines"]], [3])
    eq("net sales of the drafts = the converter's net", sum(s["net_total"] for s in sales), net)
    st, pv = hq.upload("/api/v1/imports/pos_sales_file", {"outlet": o["name"]}, "lagi.csv", content)
    check("the converted file again: duplicate", st == 200 and pv["duplicate"] is True, (st, pv.get("duplicate")))


SCENARIOS = [scenario_pos_export]
