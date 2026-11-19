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
ok, msg = refused(SAMPLE, {k: v for k, v in MAP.items() if k != "skip_rows_where"}, "row 14")
check("without skip_rows_where the footer is not taken for a sale: refused", ok, msg)
ok, msg = refused(SAMPLE, dict(MAP, columns={k: v for k, v in MAP["columns"].items() if k != "gross"}), "columns.gross")
check("a mapping without a required column: refused", ok, msg)
ok, msg = refused(SAMPLE, dict(MAP, void=dict(MAP["void"], **{"as": "ignore"})), "skip or return")
check("an unknown void.as: refused", ok, msg)

# rows the audit (2026-11-19, H2) showed becoming wrong sales: each is refused with its row number
ROW = b"17/11/2026 19:15;B-0003;Dago;MNU-007;Sate ayam;1;30.000;0;Paid"
for name, bad, words in [
    ("a cut row (Discount and Status missing): a voided bill must not become a sale",
     b"17/11/2026 12:40;B-0002;Dago;MNU-007;Sate ayam;3;90.000", ("row 8", "cells")),
    ("a ; inside the menu name shifts the columns", b"17/11/2026 19:15;B-0003;Dago;MNU-007;Sate;Combo;1;30.000;0;Paid", ("row 8", "cells")),
    ("an empty quantity", b"17/11/2026 19:15;B-0003;Dago;MNU-007;Sate ayam;;30.000;0;Paid", ("row 8", "Qty", "empty")),
    ("an empty amount", b"17/11/2026 19:15;B-0003;Dago;MNU-007;Sate ayam;1;;0;Paid", ("row 8", "Subtotal", "empty")),
    ("a quantity of 1.5 when . groups thousands (not 15)", b"17/11/2026 19:15;B-0003;Dago;MNU-007;Sate ayam;1.5;30.000;0;Paid", ("row 8", "Qty", "format")),
    ("badly grouped thousands", b"17/11/2026 19:15;B-0003;Dago;MNU-007;Sate ayam;1;30.00.000;0;Paid", ("row 8", "Subtotal", "format")),
    ("a zero quantity", b"17/11/2026 19:15;B-0003;Dago;MNU-007;Sate ayam;0;30.000;0;Paid", ("row 8", "is 0")),
    ("a sale with a negative amount", b"17/11/2026 19:15;B-0003;Dago;MNU-007;Sate ayam;1;-30.000;0;Paid", ("row 8", "negative")),
    ("a discount above the amount", b"17/11/2026 19:15;B-0003;Dago;MNU-007;Sate ayam;1;10.000;50.000;Paid", ("row 8", "Discount")),
    ("an empty branch", b"17/11/2026 19:15;B-0003;;MNU-007;Sate ayam;1;30.000;0;Paid", ("row 8", "Branch", "empty")),
    ("a status that is neither a sale nor a void (blank)", b"17/11/2026 19:15;B-0003;Dago;MNU-007;Sate ayam;1;30.000;0;", ("row 8", "Status")),
    ("a status that is neither a sale nor a void (Open bill)", b"17/11/2026 19:15;B-0003;Dago;MNU-007;Sate ayam;1;30.000;0;Open bill", ("row 8", "Status")),
]:
    assert ROW in SAMPLE
    ok, msg = refused(SAMPLE.replace(ROW, bad), MAP, *words)
    check(f"refused: {name}", ok, msg)
ok, msg = refused(b"Tanggal,SKU,Qty,Nilai\n17/11/2026,A,1,70.000\n", {"columns": {"date": "Tanggal", "sku": "SKU", "qty": "Qty", "gross": "Nilai"}},
                  "row 2", "thousands")
check("refused: 70.000 with . as the decimal mark and no thousands mark (not Rp 70)", ok, msg)
m = copy.deepcopy(MAP)
del m["void"]["sale_values"]
check("without sale_values an unknown status is read as a sale (the mapping's choice)",
      any(r[1] == "B-0003" for r in p.convert(SAMPLE.replace(ROW, ROW.replace(b";Paid", b";Open bill")), m)["Dago"]["rows"]), "")
plain_ret = b"Date,Receipt,SKU,Qty,Gross\n2026-11-17,R9,SKU-1,-2,-50000\n"
r9 = p.convert(plain_ret, {"date_format": "%Y-%m-%d", "columns": {"date": "Date", "receipt": "Receipt", "sku": "SKU", "qty": "Qty", "gross": "Gross"}})
check("a return written with both quantity and amount negative is read as a return of 50.000",
      r9[""]["rows"] == [["17/11/2026", "R9", "Retur", "SKU-1", -2, 0, 0, 50000]], r9)

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
