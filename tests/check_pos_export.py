#!/usr/bin/env python3
"""Check scripts/pos_export_to_template.py on the synthetic ESB sample and on the cases a real export brings: voids
dropped or kept as returns, lines of one item on one receipt added up, a file per branch, Indonesian and English
number formats, footer lines, and a refusal with the row number for a date, a number or a SKU it cannot read.
Stdlib only, no kernel (tests/day_one.py imports a converted file on demo-ritel).

    python3 tests/check_pos_export.py
"""
import copy
import csv
import io
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import pos_export_to_template as p  # noqa: E402

MAP = json.load(open(os.path.join(ROOT, "examples", "pos-mappings", "esb.example.json"), encoding="utf-8"))
SAMPLE = open(os.path.join(ROOT, "examples", "pos-mappings", "esb-sample-export.csv"), "rb").read()
fails = []


def check(name, cond, detail=""):
    print(("ok    " if cond else "FAIL  ") + name + ("" if cond else f"  {detail}"))
    if not cond:
        fails.append(name)


def refused(raw, mapping, *words):
    try:
        p.convert(raw, mapping)
    except p.Problem as e:
        return all(w in str(e) for w in words), str(e)
    return False, "converted"


out = p.convert(SAMPLE, MAP)
check("one file per branch", sorted(out) == ["Cibiru", "Dago"], sorted(out))
dago, cibiru = out["Dago"], out["Cibiru"]
check("Dago: two lines of one item on one receipt are one row (3 pcs, 105.000, discount 5.000)",
      ["17/11/2026", "B-0001", "Jual", "MNU-001", 3, 105000, 5000, 0] in dago["rows"], dago["rows"])
check("voided and cancelled bills are dropped (void.as skip)", not any(r[1] in ("B-0002", "C-0002") for b in out.values() for r in b["rows"]), "")
check("the footer line and the empty line are skipped", sum(len(b["rows"]) for b in out.values()) == 6, [b["rows"] for b in out.values()])
check("numbers written 1.250.000 are read as rupiah", ["18/11/2026", "C-0003", "Jual", "MNU-020", 1, 1250000, 125000, 0] in cibiru["rows"], cibiru["rows"])
# the report's own footer counts the voids: 1.771.000 gross, of which 150.000 voided
check("totals: gross 1.621.000 (footer 1.771.000 less the two voids), discount 138.000, Qty 21",
      dago["totals"]["gross"] + cibiru["totals"]["gross"] == 1_621_000 and dago["totals"]["discount"] + cibiru["totals"]["discount"] == 138_000
      and dago["totals"]["qty"] + cibiru["totals"]["qty"] == 21, (dago["totals"], cibiru["totals"]))
rows = list(csv.reader(io.StringIO(p.template_csv(dago["rows"]).decode())))
check("the file is Juragan's template: its header, dates dd/mm/yyyy", rows[0] == p.TEMPLATE and rows[1][0] == "17/11/2026", rows[:2])
keys = [tuple(r[:4]) for r in rows[1:]]
check("no two rows share the import's key (Tanggal, No struk, Jenis, SKU)", len(keys) == len(set(keys)), keys)

# voids kept as returns: Jenis Retur, a negative Qty, Penjualan kotor 0, the value in Retur
m = copy.deepcopy(MAP)
m["void"]["as"] = "return"
ret = p.convert(SAMPLE, m)
check("void.as return: the voided bill is a Retur row of -3 with 90.000 in Retur",
      ["17/11/2026", "B-0002", "Retur", "MNU-007", -3, 0, 0, 90000] in ret["Dago"]["rows"], ret["Dago"]["rows"])
check("void.as return: net sales are the same as with skip only when the sale itself is in the file (here it is not: net is lower by the voids)",
      ret["Dago"]["totals"]["returns"] == 90_000 and ret["Cibiru"]["totals"]["returns"] == 60_000, (ret["Dago"]["totals"], ret["Cibiru"]["totals"]))

# no branch column: one file; an English number format; a negative quantity is a return; the header on line 1
plain = (b"Date,Receipt,SKU,Qty,Gross,Disc\n"
         b"2026-11-17,R1,SKU-1,2,\"1,250,000.00\",0\n"
         b"2026-11-17,R1,SKU-2,1,15000,1500\n"
         b"2026-11-17,R2,SKU-1,-1,\"625,000.00\",0\n")
pm = {"date_format": "%Y-%m-%d", "number": {"thousands": ",", "decimal": "."},
      "columns": {"date": "Date", "receipt": "Receipt", "sku": "SKU", "qty": "Qty", "gross": "Gross", "discount": "Disc"}}
one = p.convert(plain, pm)
check("no branch column: one file, the delimiter sniffed, 1,250,000.00 read as 1250000",
      list(one) == [""] and ["17/11/2026", "R1", "Jual", "SKU-1", 2, 1250000, 0, 0] in one[""]["rows"], one)
check("a row with a negative quantity is a return with its value in Retur",
      ["17/11/2026", "R2", "Retur", "SKU-1", -1, 0, 0, 625000] in one[""]["rows"], one[""]["rows"])
check("the file name of an outlet without a branch", p.file_name("") == "penjualan-file-pos.csv" and p.file_name("Dago / Atas") == "penjualan-file-pos-dago-atas.csv", "")

# refusals: a wrong row must not become a sale
ok, msg = refused(SAMPLE, dict(MAP, columns=dict(MAP["columns"], sku="Kode Menu")), "no column", "Kode Menu", "Menu Code")
check("a column the export does not have: refused, with the header it found", ok, msg)
ok, msg = refused(SAMPLE.replace(b"18/11/2026 09:30", b"2026-11-18"), MAP, "row 10", "not a date")
check("a date in another format: refused with the row number", ok, msg)
ok, msg = refused(SAMPLE.replace(b";10;80.000;8.000", b";sepuluh;80.000;8.000"), MAP, "row 10", "Qty", "not a number")
check("a quantity that is not a number: refused with the row number", ok, msg)
ok, msg = refused(SAMPLE.replace(b";MNU-020;", b";;"), MAP, "row 12", "empty")
check("a row without a SKU: refused with the row number", ok, msg)
ok, msg = refused(SAMPLE.replace(b"80.000;8.000", b"80.000,50;8.000"), MAP, "row 10", "whole number")
check("an amount with cents: refused (rupiah are whole)", ok, msg)
ok, msg = refused(SAMPLE, {k: v for k, v in MAP.items() if k != "skip_rows_where"}, "not a date", "TOTAL")
check("without skip_rows_where the footer is not taken for a sale: refused", ok, msg)
ok, msg = refused(SAMPLE, dict(MAP, columns={k: v for k, v in MAP["columns"].items() if k != "gross"}), "columns.gross")
check("a mapping without a required column: refused", ok, msg)
ok, msg = refused(SAMPLE, dict(MAP, void=dict(MAP["void"], **{"as": "ignore"})), "skip or return")
check("an unknown void.as: refused", ok, msg)

# the command: files written, totals printed, exit codes
with tempfile.TemporaryDirectory() as d:
    r = subprocess.run([sys.executable, p.__file__, "--mapping", os.path.join(ROOT, "examples", "pos-mappings", "esb.example.json"),
                        os.path.join(ROOT, "examples", "pos-mappings", "esb-sample-export.csv"), "--out", d], capture_output=True, text=True, timeout=30)
    check("the command writes one template per branch and prints their totals",
          r.returncode == 0 and sorted(os.listdir(d)) == ["penjualan-file-pos-cibiru.csv", "penjualan-file-pos-dago.csv"]
          and "net 218000" in r.stdout and "net 1265000" in r.stdout, (r.returncode, r.stdout, r.stderr))
    bad = os.path.join(d, "bad.csv")
    open(bad, "wb").write(SAMPLE.replace(b";MNU-020;", b";;"))
    r = subprocess.run([sys.executable, p.__file__, "--mapping", os.path.join(ROOT, "examples", "pos-mappings", "esb.example.json"), bad, "--out", os.path.join(d, "x")],
                       capture_output=True, text=True, timeout=30)
    check("a file it cannot read: exit 1, the reason on stderr, nothing written", r.returncode == 1 and "row 12" in r.stderr and not os.path.exists(os.path.join(d, "x")), (r.returncode, r.stderr))

print(f"pos export: {len(fails)} failed")
sys.exit(1 if fails else 0)
