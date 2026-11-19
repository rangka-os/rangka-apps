#!/usr/bin/env python3
"""Turn a sales export of a POS that has no import of its own (ESB, Kasir Pintar, Pawoon, majoo, Olsera, ...) into
Juragan's sales template, the file the import `pos_sales_file` reads (modules/outlet_sales/imports/pos_sales_file.rk:
Tanggal, No struk, Jenis, SKU, Qty, Penjualan kotor, Diskon, Retur; one outlet per file).

    python3 scripts/pos_export_to_template.py --mapping examples/pos-mappings/esb.example.json export.csv --out out/

What the columns of the export are called is not in this script: it is in a mapping file, written once from the
headers of the first real export of that POS (examples/pos-mappings/README.md). ESB publishes no API reference and no
description of its export files (rangka reports/keputusan-riset/esb-connector.md, 19 Nov 2026), so esb.example.json is
an example to edit, not ESB's format.

What it does, so that the import takes the file as it is:
- reads a CSV export (the delimiter is sniffed, or named in the mapping; a title block above the header is skipped
  with header_row), matches columns by name, and stops with the headers it found when one is missing;
- reads dates in the mapping's date_format and numbers in its number format (1.250.000,50 or 1,250,000.50);
- drops or returns voided and refunded rows as the mapping says (void.as: skip, or return: a row of Jenis Retur with a
  negative Qty, Penjualan kotor 0 and the value in Retur, as the template writes a return);
- adds up lines that share date, receipt, kind and SKU: the import's key is those four, so a second line of the same
  item on one receipt would be skipped as a duplicate instead of counted;
- writes one file per branch when the mapping names a branch column (the import takes one outlet per file);
- prints totals per file (rows, Qty, gross, discount, returns) to compare with the POS's own report before uploading.
Nothing is uploaded: the owner uploads each file for its outlet in Juragan (Sumber penjualan = File POS).

Stdlib only. Exit 1 with the row number for a date, a number or a SKU it cannot read: a wrong row must not become a sale.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
from datetime import datetime

TEMPLATE = ["Tanggal", "No struk", "Jenis", "SKU", "Qty", "Penjualan kotor", "Diskon", "Retur"]


class Problem(Exception):
    """A file or mapping the script cannot convert safely."""


def number(text: str, fmt: dict, what: str, row: int) -> float:
    """A number as the POS writes it: thousands and decimal marks from the mapping, an empty cell is 0."""
    t = (text or "").strip()
    if t in ("", "-"):
        return 0.0
    neg = t.startswith("(") and t.endswith(")")   # (1.000) is how some reports write a negative
    t = t.strip("()").replace("Rp", "").replace(" ", "")
    th, dec = fmt.get("thousands", ""), fmt.get("decimal", ".")
    if th:
        t = t.replace(th, "")
    if dec != ".":
        t = t.replace(dec, ".")
    if not re.fullmatch(r"-?\d+(\.\d+)?", t):
        raise Problem(f"row {row}: {what} is not a number: {text!r}")
    v = float(t)
    return -v if neg else v


def whole(v: float, what: str, row: int) -> int:
    """Rupiah and quantities in the template are whole numbers."""
    if abs(v - round(v)) > 1e-6:
        raise Problem(f"row {row}: {what} is not a whole number: {v}")
    return int(round(v))


def read_rows(raw: bytes, mapping: dict):
    """The export as (row number, {header: cell}) pairs, after the title block above the header."""
    text = raw.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    start = int(mapping.get("header_row", 1)) - 1
    if start < 0 or start >= len(lines):
        raise Problem(f"header_row {start + 1} is beyond the file ({len(lines)} lines)")
    delim = mapping.get("delimiter")
    if not delim:
        try:
            delim = csv.Sniffer().sniff(lines[start], delimiters=",;\t|").delimiter
        except csv.Error:
            delim = ","
    reader = csv.reader(io.StringIO("\n".join(lines[start:])), delimiter=delim)
    header = [h.strip() for h in next(reader, [])]
    out = []
    for i, cells in enumerate(reader, start=start + 2):
        if not any(c.strip() for c in cells):
            continue
        out.append((i, {h: (cells[j].strip() if j < len(cells) else "") for j, h in enumerate(header)}))
    return header, out


def convert(raw: bytes, mapping: dict) -> dict:
    """{branch: {"rows": [template rows], "totals": {...}}}; branch is "" when the mapping names no branch column."""
    cols = mapping.get("columns") or {}
    for need in ("date", "sku", "qty", "gross"):
        if not cols.get(need):
            raise Problem(f"mapping.columns.{need} is required")
    header, rows = read_rows(raw, mapping)
    void = mapping.get("void") or {}
    skip = mapping.get("skip_rows_where") or []
    used = [c for c in cols.values() if c] + ([void["column"]] if void.get("column") else []) + [s["column"] for s in skip]
    missing = sorted({c for c in used if c not in header})
    if missing:
        raise Problem(f"the export has no column {missing}; its header is {header}")
    fmt = mapping.get("number") or {}
    date_format = mapping.get("date_format") or "%d/%m/%Y"
    void_values = {str(v).strip().lower() for v in void.get("values") or []}
    void_as = void.get("as", "skip")
    if void_as not in ("skip", "return"):
        raise Problem("mapping.void.as is skip or return")
    merged, order = {}, []
    for n, r in rows:
        if any(r.get(s["column"], "").strip().lower() == str(s.get("equals", "")).strip().lower() for s in skip):
            continue   # a subtotal or footer line of the report
        is_void = bool(void.get("column")) and r[void["column"]].strip().lower() in void_values
        if is_void and void_as == "skip":
            continue   # a voided bill never was a sale
        try:
            day = datetime.strptime(r[cols["date"]].strip(), date_format).date()
        except ValueError:
            raise Problem(f"row {n}: {cols['date']} is not a date in the format {date_format}: {r[cols['date']]!r}") from None
        sku = r[cols["sku"]].strip()
        if not sku:
            raise Problem(f"row {n}: {cols['sku']} is empty; every row needs the item's SKU as it is in Juragan")
        qty = number(r[cols["qty"]], fmt, cols["qty"], n)
        gross = whole(number(r[cols["gross"]], fmt, cols["gross"], n), cols["gross"], n)
        disc = whole(number(r[cols["discount"]], fmt, cols["discount"], n), cols["discount"], n) if cols.get("discount") else 0
        receipt = r[cols["receipt"]].strip() if cols.get("receipt") else ""
        branch = r[cols["branch"]].strip() if cols.get("branch") else ""
        # a return: a voided or refunded row kept as a return, or a row the POS itself writes with a negative quantity
        ret = is_void or qty < 0
        kind = "Retur" if ret else "Jual"
        key = (branch, day, receipt, kind, sku)
        if key not in merged:
            merged[key] = {"qty": 0.0, "gross": 0, "disc": 0, "ret": 0}
            order.append(key)
        m = merged[key]
        if ret:
            m["qty"] -= abs(qty)
            m["ret"] += abs(gross) - abs(disc)
        else:
            m["qty"] += qty
            m["gross"] += gross
            m["disc"] += abs(disc)
    out = {}
    for key in order:
        branch, day, receipt, kind, sku = key
        m = merged[key]
        qty = m["qty"]
        b = out.setdefault(branch, {"rows": [], "totals": {"rows": 0, "qty": 0.0, "gross": 0, "discount": 0, "returns": 0}})
        b["rows"].append([day.strftime("%d/%m/%Y"), receipt, kind, sku, int(qty) if qty == int(qty) else qty, m["gross"], m["disc"], m["ret"]])
        t = b["totals"]
        t["rows"] += 1
        t["qty"] += qty
        t["gross"] += m["gross"]
        t["discount"] += m["disc"]
        t["returns"] += m["ret"]
    return out


def template_csv(rows) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(TEMPLATE)
    w.writerows(rows)
    return buf.getvalue().encode()


def file_name(branch: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", "-", branch).strip("-").lower()
    return f"penjualan-file-pos-{safe}.csv" if safe else "penjualan-file-pos.csv"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("export", help="the POS's sales export, CSV (save an Excel export as CSV first)")
    ap.add_argument("--mapping", required=True, help="the mapping file of that POS (examples/pos-mappings/)")
    ap.add_argument("--out", default=".", help="folder for the template files (default: here)")
    a = ap.parse_args(argv)
    try:
        mapping = json.load(open(a.mapping, encoding="utf-8"))
        result = convert(open(a.export, "rb").read(), mapping)
    except (Problem, OSError, json.JSONDecodeError) as e:
        print(f"pos_export_to_template: {e}", file=sys.stderr)
        return 1
    if not result:
        print("pos_export_to_template: no sales rows in the export", file=sys.stderr)
        return 1
    os.makedirs(a.out, exist_ok=True)
    for branch in sorted(result):
        path = os.path.join(a.out, file_name(branch))
        with open(path, "wb") as f:
            f.write(template_csv(result[branch]["rows"]))
        t = result[branch]["totals"]
        qty = int(t["qty"]) if t["qty"] == int(t["qty"]) else t["qty"]
        print(f"{path}: {branch or 'one outlet'}: {t['rows']} rows, Qty {qty}, gross {t['gross']}, discount {t['discount']}, "
              f"returns {t['returns']}, net {t['gross'] - t['discount'] - t['returns']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
