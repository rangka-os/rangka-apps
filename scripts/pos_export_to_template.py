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

Stdlib only. Exit 1 with the row number for a row it cannot read safely: a wrong row must not become a sale. Refused:
a row with cells missing or one too many (a cut line, a delimiter inside a name), an empty or zero quantity, an empty
amount, a number that does not follow the mapping's marks (1.5 when . groups thousands), a sale with a negative
amount, a discount above the amount, an empty branch, an unreadable date or an empty SKU, and, when void.sale_values
is set, a status that is neither a sale nor a void.
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


def number(text: str, fmt: dict, what: str, row: int, required: bool = True) -> float:
    """A number as the POS writes it, with the mapping's thousands and decimal marks. The thousands groups are checked
    (1.250.000 is read, 1.5 or 12.50.000 is refused when . is the thousands mark), so a quantity of 1,5 written as 1.5
    never becomes 15. An empty cell is refused unless the column may be empty (a discount): an empty quantity or amount
    is a broken row, not a zero."""
    t = (text or "").strip()
    if t in ("", "-"):
        if required:
            raise Problem(f"row {row}: {what} is empty")
        return 0.0
    neg = t.startswith("(") and t.endswith(")")   # (1.000) is how some reports write a negative
    t = t.strip("()").replace("Rp", "").replace(" ", "")
    if t.startswith("-"):
        neg, t = not neg, t[1:]
    th, dec = fmt.get("thousands", ""), fmt.get("decimal", ".")
    whole_part, _, frac = t.partition(dec) if dec else (t, "", "")
    if th:
        ok = re.fullmatch(r"\d{1,3}(" + re.escape(th) + r"\d{3})+|\d+", whole_part) is not None
    else:
        ok = re.fullmatch(r"\d+", whole_part) is not None
        if ok and len(frac) == 3 and dec in ".,":
            # 70.000 with . as the decimal mark: almost surely a thousands group; the mapping must say so
            raise Problem(f"row {row}: {what} {text!r} looks like a thousands group: set number.thousands in the mapping")
    if not ok or (frac and not frac.isdigit()) or (dec and t.count(dec) > 1):
        raise Problem(f"row {row}: {what} is not a number in this mapping's format: {text!r}")
    v = float((whole_part.replace(th, "") if th else whole_part) + ("." + frac if frac else ""))
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
        # a row with cells missing, or one more cell (a delimiter inside a name), would shift every column after it
        extra = cells[len(header):]
        if len(cells) < len(header) or any(c.strip() for c in extra):
            raise Problem(f"row {i}: {len(cells)} cells for {len(header)} columns; a cut row or a {delim!r} inside a value shifts the columns")
        out.append((i, {h: cells[j].strip() for j, h in enumerate(header)}))
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
    sale_values = {str(v).strip().lower() for v in void.get("sale_values") or []}
    void_as = void.get("as", "skip")
    if void_as not in ("skip", "return"):
        raise Problem("mapping.void.as is skip or return")
    merged, order = {}, []
    for n, r in rows:
        if any(r.get(s["column"], "").strip().lower() == str(s.get("equals", "")).strip().lower() for s in skip):
            continue   # a subtotal or footer line of the report
        state = r[void["column"]].strip().lower() if void.get("column") else ""
        is_void = bool(void.get("column")) and state in void_values
        if sale_values and not is_void and state not in sale_values:
            # a status the mapping does not name (blank, "Draft", "Open bill"): not taken for a sale
            raise Problem(f"row {n}: {void['column']} {r[void['column']]!r} is neither a sale ({sorted(sale_values)}) nor a void ({sorted(void_values)})")
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
        disc = whole(number(r[cols["discount"]], fmt, cols["discount"], n, required=False), cols["discount"], n) if cols.get("discount") else 0
        receipt = r[cols["receipt"]].strip() if cols.get("receipt") else ""
        branch = r[cols["branch"]].strip() if cols.get("branch") else ""
        if cols.get("branch") and not branch:
            raise Problem(f"row {n}: {cols['branch']} is empty; every row needs its branch")
        if qty == 0:
            raise Problem(f"row {n}: {cols['qty']} is 0")
        if qty > 0 and gross < 0:
            # a return is written with a negative quantity (its value may be either sign); a sale is never negative
            raise Problem(f"row {n}: a sale with a negative {cols['gross']} {gross}")
        if disc < 0 or abs(disc) > abs(gross):
            raise Problem(f"row {n}: {cols['discount']} {disc} is negative or more than {cols['gross']} {gross}")
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
