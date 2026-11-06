#!/usr/bin/env python3
"""Synthetic demo data for Toko Contoh, a fictional two-branch grocery shop (toko kelontong / minimarket kecil).

No real people, businesses, brands or barcodes: product names are generic, barcodes use the GS1 in-store range 200-299.
Deterministic (fixed random seed), Python stdlib only.

    python3 generate.py --seed-file demo.rk          # rewrite the seed fixtures (outlets, items, vendors, users)
    python3 generate.py --out DIR [--end 2026-11-04] # write the POS exports and templates for a manual demo

tests/day_one.py --site demo-ritel imports this module and builds the same files for the kernel's today, so the
dashboards' 30-day period and the slow-mover list always have data.
"""
import argparse
import csv
import io
import datetime as dt
import os
import random

ORG_CODE = "TKC"
CIBIRU, CILEUNYI = "OUT-01", "OUT-02"
MOKA_NAME = "Toko Contoh - Cibiru"
DAYS = 40            # days of sales history ending the day before `end`
SALDO, PENITIP = "VND-0001", ("VND-0006", "VND-0007", "VND-0008")

UOMS = [("UOM-001", "Pcs"), ("UOM-002", "Kg"), ("UOM-003", "Liter"), ("UOM-004", "Dus"), ("UOM-005", "Pak")]
PCS, KG, LTR = "UOM-001", "UOM-002", "UOM-003"

CATEGORIES = ["Sembako", "Mi dan makanan instan", "Minuman", "Susu dan olahan", "Makanan ringan", "Bumbu dapur",
              "Perawatan diri", "Kebersihan rumah", "Alat tulis", "Bayi dan anak", "Kue dan jajanan titipan"]

# (category index, product, variant group or "", [(variant label, cost per stock unit)], stock uom, units per dus or 0)
PRODUCTS = [
    (0, "Beras medium", "BERAS", [("5 kg", 62000), ("10 kg", 121000)], PCS, 0),
    (0, "Beras premium", "BERAS-P", [("5 kg", 71000)], PCS, 0),
    (0, "Gula pasir", "", [("1 kg", 16500)], PCS, 20),
    (0, "Minyak goreng kemasan", "MINYAK", [("1 liter", 17500), ("2 liter", 34000)], PCS, 12),
    (0, "Telur ayam", "", [("per kg", 27000)], KG, 0),
    (0, "Tepung terigu", "TERIGU", [("500 g", 6200), ("1 kg", 11800)], PCS, 20),
    (0, "Garam beryodium", "", [("250 g", 2300)], PCS, 40),
    (0, "Kecap manis", "KECAP", [("sachet", 900), ("botol 135 ml", 6800), ("botol 520 ml", 21500)], PCS, 24),
    (1, "Mi goreng instan", "MI-GRG", [("original", 2750), ("pedas", 2800), ("rendang", 2850)], PCS, 40),
    (1, "Mi kuah instan", "MI-KUAH", [("ayam bawang", 2600), ("soto", 2600), ("kari", 2650), ("baso", 2650)], PCS, 40),
    (1, "Mi cup", "MI-CUP", [("ayam", 4900), ("sapi", 4900)], PCS, 24),
    (1, "Bihun instan", "", [("ayam", 2900)], PCS, 30),
    (1, "Sarden kaleng", "SARDEN", [("kecil", 8900), ("besar", 19800)], PCS, 24),
    (1, "Kornet kaleng", "", [("198 g", 21500)], PCS, 24),
    (2, "Air mineral", "AIR", [("330 ml", 1700), ("600 ml", 2400), ("1,5 liter", 4800)], PCS, 24),
    (2, "Air mineral galon isi ulang", "", [("19 liter", 17000)], PCS, 0),
    (2, "Teh dalam botol", "TEH-BTL", [("melati", 3300), ("apel", 3400), ("tanpa gula", 3500)], PCS, 24),
    (2, "Teh kotak", "TEH-KTK", [("200 ml", 2700)], PCS, 24),
    (2, "Kopi saset", "KOPI", [("hitam", 1150), ("susu", 1350), ("moka", 1350), ("gula aren", 1450)], PCS, 120),
    (2, "Minuman isotonik", "ISO", [("350 ml", 5100), ("500 ml", 6300)], PCS, 24),
    (2, "Minuman soda", "SODA", [("kaleng jeruk", 5200), ("kaleng cola", 5200), ("botol 1,5 liter", 12500)], PCS, 24),
    (2, "Sirup", "SIRUP", [("cocopandan", 15800), ("jeruk", 15800)], PCS, 12),
    (2, "Teh celup", "", [("isi 25", 5600)], PCS, 48),
    (3, "Susu UHT", "UHT", [("cokelat 200 ml", 5000), ("stroberi 200 ml", 5000), ("plain 1 liter", 17800)], PCS, 24),
    (3, "Susu kental manis", "SKM", [("saset", 1350), ("kaleng", 11200)], PCS, 48),
    (3, "Susu bubuk", "SUSU-BBK", [("400 g", 42000)], PCS, 12),
    (3, "Yoghurt botol", "", [("stroberi", 6800)], PCS, 24),
    (4, "Keripik kentang", "KRP-KTG", [("original kecil", 4600), ("balado kecil", 4600), ("original besar", 11400)], PCS, 30),
    (4, "Biskuit kelapa", "", [("250 g", 7900)], PCS, 24),
    (4, "Wafer cokelat", "WAFER", [("kecil", 1800), ("besar", 8200)], PCS, 48),
    (4, "Biskuit krim", "BISK-KRIM", [("vanila", 2200), ("cokelat", 2200), ("stroberi", 2200)], PCS, 48),
    (4, "Kacang kulit", "", [("150 g", 8400)], PCS, 24),
    (4, "Permen mint", "", [("isi 50", 7800)], PCS, 24),
    (4, "Cokelat batang", "COKLAT", [("kecil", 4300), ("besar", 13900)], PCS, 48),
    (4, "Roti tawar", "", [("kupas", 13500)], PCS, 0),
    (5, "Kaldu bubuk", "KALDU", [("ayam saset", 450), ("sapi saset", 450), ("ayam 100 g", 5900)], PCS, 120),
    (5, "Saus sambal", "SAMBAL", [("saset", 400), ("botol 135 ml", 6900)], PCS, 24),
    (5, "Bumbu instan", "BUMBU", [("nasi goreng", 2300), ("rendang", 4100), ("opor", 3700)], PCS, 60),
    (5, "Merica bubuk", "", [("saset", 900)], PCS, 120),
    (6, "Sabun mandi batang", "SABUN-BTG", [("lavender", 3300), ("lemon", 3300), ("susu", 3400)], PCS, 72),
    (6, "Sabun mandi cair", "SABUN-CR", [("isi ulang 450 ml", 18200)], PCS, 24),
    (6, "Sampo", "SAMPO", [("saset", 850), ("botol 170 ml", 17200)], PCS, 24),
    (6, "Pasta gigi", "PASTA", [("75 g", 7800), ("190 g", 14600)], PCS, 36),
    (6, "Sikat gigi", "", [("dewasa", 4400)], PCS, 72),
    (6, "Pembalut", "PEMBALUT", [("siang isi 10", 9300), ("malam isi 8", 11800)], PCS, 24),
    (6, "Deodoran", "", [("roll on", 14200)], PCS, 24),
    (7, "Deterjen bubuk", "DETERJEN", [("saset", 900), ("800 g", 19800)], PCS, 24),
    (7, "Sabun cuci piring", "SCP", [("saset", 450), ("isi ulang 750 ml", 13200)], PCS, 24),
    (7, "Pewangi pakaian", "PEWANGI", [("saset", 450), ("isi ulang 900 ml", 13800)], PCS, 24),
    (7, "Pembersih lantai", "", [("isi ulang 800 ml", 11800)], PCS, 12),
    (7, "Obat nyamuk bakar", "", [("isi 10", 5300)], PCS, 60),
    (7, "Tisu", "TISU", [("saku", 2100), ("isi 250 lembar", 11200)], PCS, 48),
    (7, "Kantong plastik", "", [("pak isi 50", 7200)], PCS, 0),
    (8, "Buku tulis", "BUKU", [("38 lembar", 3100), ("58 lembar", 4400)], PCS, 40),
    (8, "Pulpen", "", [("hitam", 1900)], PCS, 144),
    (8, "Pensil", "", [("2B", 1700)], PCS, 144),
    (8, "Lakban", "", [("bening", 8100)], PCS, 72),
    (8, "Baterai", "BATERAI", [("AA isi 2", 7300), ("AAA isi 2", 7300)], PCS, 24),
    (9, "Popok bayi", "POPOK", [("M isi 10", 22800), ("L isi 10", 24800)], PCS, 8),
    (9, "Minyak telon", "", [("60 ml", 16200)], PCS, 24),
    (9, "Bubur bayi instan", "", [("saset", 3100)], PCS, 48),
]

EXTRA = [  # (name, cost per stock unit, category index)
    ("Gula merah 500 g", 9800, 0), ("Gula pasir 500 g", 8400, 0), ("Minyak goreng curah per liter", 15800, 0), ("Bawang merah per kg", 38000, 0),
    ("Bawang putih per kg", 33000, 0), ("Kacang hijau 500 g", 13200, 0), ("Kacang tanah 500 g", 15400, 0), ("Tepung beras 500 g", 6900, 0),
    ("Tepung tapioka 500 g", 6400, 0), ("Santan instan 65 ml", 3100, 0), ("Ikan teri kering 100 g", 9800, 0), ("Mentega 200 g", 7900, 0),
    ("Mi telur kering 200 g", 5200, 1), ("Makaroni 250 g", 6100, 1), ("Bubur instan ayam", 4200, 1), ("Nugget ayam beku 500 g", 31000, 1),
    ("Sosis ayam beku 500 g", 27500, 1), ("Bakso sapi beku 500 g", 33500, 1), ("Abon sapi 100 g", 19800, 1), ("Ikan kaleng tuna", 13400, 1),
    ("Air mineral 1 liter", 3600, 2), ("Teh dalam botol 1 liter", 6400, 2), ("Kopi bubuk 165 g", 13500, 2), ("Kopi susu botol 220 ml", 6900, 2),
    ("Minuman jeli 150 ml", 2900, 2), ("Minuman sari kacang hijau", 3100, 2), ("Minuman energi saset", 1100, 2), ("Cokelat bubuk saset", 1600, 2),
    ("Jus jeruk kotak 250 ml", 4600, 2), ("Susu kedelai botol", 4900, 2), ("Es krim cup cokelat", 4300, 3), ("Es krim stik vanila", 3100, 3),
    ("Keju lembaran isi 10", 19800, 3), ("Susu UHT anak 115 ml", 2900, 3), ("Krimer kental manis saset", 1100, 3), ("Susu pasteurisasi 250 ml", 7400, 3),
    ("Kerupuk udang mentah 250 g", 9800, 4), ("Kue kering kaleng 400 g", 39500, 4), ("Snack jagung bakar", 1800, 4), ("Snack kentang balado", 1900, 4),
    ("Rumput laut panggang", 4600, 4), ("Kacang atom 200 g", 9900, 4), ("Pilus pedas 100 g", 4800, 4), ("Roti sobek cokelat", 9500, 4),
    ("Roti isi kelapa", 3200, 4), ("Agar-agar bubuk", 2900, 5), ("Penyedap rasa 100 g", 4600, 5), ("Ketumbar bubuk saset", 900, 5),
    ("Kunyit bubuk saset", 900, 5), ("Cuka masak 150 ml", 3800, 5), ("Saus tomat botol 340 ml", 9800, 5), ("Terasi udang 50 g", 3400, 5),
    ("Bumbu pecel 200 g", 9200, 5), ("Minyak kayu putih 60 ml", 19500, 6), ("Bedak tabur 100 g", 9800, 6), ("Cotton bud isi 100", 5200, 6),
    ("Krim wajah saset", 2100, 6), ("Sabun cuci muka 100 g", 17900, 6), ("Minyak rambut 100 ml", 11200, 6), ("Pisau cukur sekali pakai", 3900, 6),
    ("Plester luka isi 10", 3100, 6), ("Masker kain", 4400, 6), ("Pemutih pakaian 500 ml", 7600, 7), ("Pembersih kaca isi ulang", 9200, 7),
    ("Kapur barus isi 6", 4100, 7), ("Spons cuci piring", 2300, 7), ("Sapu ijuk", 18500, 7), ("Kain pel", 15500, 7),
    ("Korek api gas", 2600, 7), ("Lilin putih isi 5", 6200, 7), ("Kantong sampah besar", 9800, 7), ("Sabun colek 450 g", 5900, 7),
    ("Penghapus pensil", 1400, 8), ("Penggaris 30 cm", 2600, 8), ("Spidol permanen", 6900, 8), ("Map plastik", 1900, 8),
    ("Amplop putih isi 10", 3900, 8), ("Lem kertas 35 ml", 3200, 8), ("Tisu basah bayi isi 50", 9900, 9), ("Sabun bayi 100 g", 6100, 9),
    ("Bedak bayi 100 g", 8800, 9), ("Dot bayi", 15800, 9),
]

# Slow movers: sold only in the first days of the window (last sale more than 30 days ago), and never-sold items.
SLOW = {"TKC-0040", "TKC-0063", "TKC-0071", "TKC-0098", "TKC-0117", "TKC-0129", "TKC-0150", "TKC-0163", "TKC-0177"}
NEVER = {"TKC-0058", "TKC-0110", "TKC-0145", "TKC-0186"}

CONSIGNED = [  # (name, consignor, consignment cost, selling price)
    ("Kue lapis (titipan)", PENITIP[0], 2000, 3000), ("Bolu kukus (titipan)", PENITIP[0], 1800, 2500),
    ("Risoles (titipan)", PENITIP[0], 2200, 3000), ("Keripik singkong pedas (titipan)", PENITIP[1], 7000, 10000),
    ("Keripik pisang (titipan)", PENITIP[1], 8000, 11000), ("Makaroni bantat (titipan)", PENITIP[1], 4500, 6500),
    ("Rempeyek kacang (titipan)", PENITIP[2], 9000, 12500), ("Kerupuk kulit (titipan)", PENITIP[2], 6000, 8500),
]


def ean13(n):
    """A 13-digit code in the GS1 restricted range 200-299 (in-store use) with a valid check digit."""
    body = f"20{n:010d}"
    s = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(body))
    return body + str((10 - s % 10) % 10)


def items():
    """The 200 items: dicts with the fields of the seed file plus `_weight` (popularity) and `_dus`."""
    rng = random.Random(7)
    out, n = [], 0
    for cat, product, group, variants, uom, dus in PRODUCTS:
        for label, cost in variants:
            n += 1
            margin = rng.choice([0.08, 0.10, 0.12, 0.15, 0.18, 0.22, 0.25])
            rate = int(round(cost * (1 + margin) / 100.0)) * 100 or 100
            it = {"name": f"BRG-{n:04d}", "item_name": f"{product} {label}", "sku": f"{ORG_CODE}-{n:04d}", "barcode": ean13(n),
                  "kind": "good", "stock_uom": uom, "category": f"KTG-{cat + 1:03d}", "rate": rate, "standard_cost": cost,
                  "_weight": rng.paretovariate(1.3), "_dus": dus}
            if group and len(variants) > 1:
                it["variant_group"], it["variant_label"] = group, label
            out.append(it)
    # single goods without variants, so the shop has 192 own SKU plus 8 consigned
    for name, cost, cat in EXTRA:
        n += 1
        rate = int(round(cost * rng.choice([1.10, 1.12, 1.15, 1.20]) / 100.0)) * 100
        out.append({"name": f"BRG-{n:04d}", "item_name": name, "sku": f"{ORG_CODE}-{n:04d}", "barcode": ean13(n), "kind": "good",
                    "stock_uom": LTR if "per liter" in name else (KG if "per kg" in name else PCS), "category": f"KTG-{cat + 1:03d}",
                    "rate": rate, "standard_cost": cost, "_weight": rng.paretovariate(1.3), "_dus": 24 if "per " not in name else 0})
    for name, consignor, cost, rate in CONSIGNED:
        n += 1
        out.append({"name": f"BRG-{n:04d}", "item_name": name, "sku": f"{ORG_CODE}-{n:04d}", "kind": "good", "stock_uom": PCS,
                    "category": "KTG-011", "rate": rate, "standard_cost": cost, "consignor": consignor, "consignment_cost": cost,
                    "_weight": 3.0, "_dus": 0})
    return out


def own(it):
    return not it.get("consignor")


# ---------- seed file

def yval(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if any(c in s for c in ":#,{}[]'\"") or s.strip() != s or s in ("true", "false", "null") or s[:1].isdigit():
        return "'" + s.replace("'", "''") + "'"
    return s


def flow(d):
    return "{" + ", ".join(f"{k}: {yval(v)}" for k, v in d.items() if not k.startswith("_")) + "}"


def seed_text():
    its = items()
    lines = ["# Synthetic demo data for Toko Contoh, a fictional two-branch grocery shop. No real people, businesses, brands or",
             "# barcodes. Generated by generate.py (python3 generate.py --seed-file demo.rk); edit the generator, not this file.",
             "uom:"]
    lines += [f"  - {flow({'name': n, 'uom_name': u})}" for n, u in UOMS]
    lines.append("item_category:")
    lines += [f"  - {flow({'name': f'KTG-{i + 1:03d}', 'category_name': c})}" for i, c in enumerate(CATEGORIES)]
    lines.append("outlet:")
    lines.append("  - " + flow({"name": CIBIRU, "outlet_name": "Toko Contoh Cibiru", "code": "CBR", "city": "Bandung",
                               "address": "Jl. Contoh Cibiru No. 10", "is_head_office": True, "moka_name": MOKA_NAME, "active": True}))
    lines.append("  - " + flow({"name": CILEUNYI, "outlet_name": "Toko Contoh Cileunyi", "code": "CLY", "city": "Kabupaten Bandung",
                               "address": "Jl. Contoh Cileunyi No. 22", "sales_source": "file", "active": True}))
    lines.append("vendor:")
    vendors = [(SALDO, "Saldo awal", "Bandung", False, 0),
               ("VND-0002", "CV Contoh Distribusi Sembako", "Bandung", False, 14),
               ("VND-0003", "PT Contoh Minuman Nusantara", "Bandung", True, 30),
               ("VND-0004", "UD Contoh Grosir Makanan Ringan", "Cimahi", False, 7),
               ("VND-0005", "PT Contoh Kebutuhan Rumah", "Bandung", True, 30),
               (PENITIP[0], "Bu Sari Kue Contoh", "Bandung", False, 0),
               (PENITIP[1], "UMKM Keripik Contoh", "Sumedang", False, 0),
               (PENITIP[2], "Dapur Rempeyek Contoh", "Bandung", False, 0)]
    for name, vn, city, pkp, days in vendors:
        lines.append("  - " + flow({"name": name, "vendor_name": vn, "city": city, "pkp": pkp, "payment_days": days}))
    lines.append("item:")
    lines += [f"  - {flow(it)}" for it in its]
    lines.append("uom_conversion:")
    k = 0
    for it in its:
        if it["_dus"]:
            k += 1
            lines.append(f"  - {flow({'name': f'KONV-{k:04d}', 'item': it['name'], 'uom': 'UOM-004', 'factor': it['_dus']})}")
    lines.append("user:")
    lines += ["  - {name: USR-001, full_name: Dewi Contoh, role: head_office, email: dewi@tokocontoh.test}",
              "  - {name: USR-002, full_name: Lina Contoh, role: outlet_staff, email: lina.cibiru@tokocontoh.test}",
              "  - {name: USR-003, full_name: Agus Contoh, role: outlet_staff, email: agus.cileunyi@tokocontoh.test}"]
    return "\n".join(lines) + "\n"


# ---------- POS exports and templates (dates relative to `end`)

def ddmmyyyy(d, sep="-"):
    return d.strftime(f"%d{sep}%m{sep}%Y")


def csv_bytes(header, rows):
    def cell(v):
        s = str(v)
        return '"' + s.replace('"', '""') + '"' if any(c in s for c in ',"\n') else s
    return ("\n".join([",".join(header)] + [",".join(cell(v) for v in r) for r in rows]) + "\n").encode()


def window(end):
    """The sales days: DAYS days ending the day before `end` (a date)."""
    return [end - dt.timedelta(days=DAYS - i) for i in range(DAYS)]


def daily_sales(end, outlet):
    """{date: [(item, qty)]} for one outlet: popular items most days, slow items only in the first 8 days."""
    rng = random.Random(11 if outlet == CIBIRU else 23)
    its = [it for it in items() if it["sku"] not in NEVER]
    if outlet == CILEUNYI:
        its = [it for it in its if own(it)]           # the consignors deliver to Cibiru only
    days = window(end)
    out = {}
    for di, d in enumerate(days):
        busy = 1.25 if d.weekday() >= 5 else 1.0
        rows = []
        for it in its:
            if it["sku"] in SLOW and di >= 8:
                continue
            lam = min(it["_weight"], 12) * 0.9 * busy * (1.0 if outlet == CIBIRU else 0.7)
            if it["sku"] in SLOW:
                lam = 0.6
            q = sum(1 for _ in range(int(lam * 3)) if rng.random() < 1 / 3)
            if it["sku"] in SLOW and di == 0:
                q = max(q, 1)
            if q:
                rows.append((it, q))
        out[d] = rows
    return out


def opening_qty(end, outlet):
    """Opening stock per item: what the window sells plus 3-10 days of cover, so most balances stay positive."""
    sold = {}
    for rows in daily_sales(end, outlet).values():
        for it, q in rows:
            sold[it["sku"]] = sold.get(it["sku"], 0) + q
    rng = random.Random(5 if outlet == CIBIRU else 6)
    qty = {}
    for it in items():
        if not own(it):
            continue
        base = sold.get(it["sku"], 0)
        qty[it["sku"]] = base + max(3, int(base / DAYS * rng.randint(3, 10)))
    # one bestseller at Cileunyi runs short on purpose: the dashboard's negative-stock list has a row
    short = max((it for it in items() if own(it)), key=lambda it: it["_weight"])["sku"]
    if outlet == CILEUNYI and short in qty:
        qty[short] = max(1, sold.get(short, 0) - 5)
    return qty


def opening_stock_csv(end, outlet):
    d = end - dt.timedelta(days=DAYS + 1)
    by_sku = {it["sku"]: it for it in items()}
    rows = [(ddmmyyyy(d, "/"), sku, q, by_sku[sku]["standard_cost"]) for sku, q in sorted(opening_qty(end, outlet).items())]
    return csv_bytes(["Tanggal", "SKU", "Qty", "Harga pokok"], rows)


MOKA_HEADER = ["Outlet", "Receipt Number", "Date", "Time", "Category", "Brand", "Items", "Variant", "SKU", "Quantity",
               "Modifier Applied", "Discount Applied", "Gross Sales", "Discounts", "Refunds", "Net Sales", "Gratuity", "Tax",
               "Sales Type", "Collected By", "Served By", "Customer", "Payment Method", "Event Type", "Reason of Refund"]


def moka_csv(end):
    """Moka Item Details export of Cibiru for the whole window (one row per item per receipt, 3 items per receipt)."""
    rng = random.Random(31)
    cats = {f"KTG-{i + 1:03d}": c for i, c in enumerate(CATEGORIES)}
    rows, receipt = [], 0
    for d, sold in daily_sales(end, CIBIRU).items():
        lines = [(it, 1) for it, q in sold for _ in range(q)]
        rng.shuffle(lines)
        merged = []
        for j in range(0, len(lines), 3):
            chunk = {}
            for it, q in lines[j:j + 3]:
                chunk[it["sku"]] = (it, chunk.get(it["sku"], (it, 0))[1] + q)
            merged.append(list(chunk.values()))
        for basket in merged:
            receipt += 1
            no = f"CBR-{receipt:06d}"
            t = f"{rng.randint(7, 21):02d}:{rng.randint(0, 59):02d}:00"
            pay = rng.choice(["Cash", "Cash", "Cash", "QRIS", "QRIS", "Debit BCA"])
            for it, q in basket:
                gross = it["rate"] * q
                disc = (gross // 10 // 100) * 100 if rng.random() < 0.04 else 0
                rows.append([MOKA_NAME, no, ddmmyyyy(d), t, cats[it["category"]], "", it["item_name"], "", it["sku"], q, "", "Promo" if disc else "",
                             f"{gross}.0", f"{disc}.0", "0.0", f"{gross - disc}.0", "0.0", "0.0", "Take Away", "Kasir 1", "Kasir 1", "", pay, "Payment", ""])
        if d.day % 9 == 0 and merged:   # an occasional refund of the first item of the day's first receipt
            it, q = merged[0][0]
            rows.append([MOKA_NAME, f"CBR-{receipt - len(merged) + 1:06d}", ddmmyyyy(d), "21:30:00", cats[it["category"]], "",
                         f"Full Refund for: {it['item_name']}", "", it["sku"], -1, "", "", "0.0", "0.0", f"{it['rate']}.0", f"-{it['rate']}.0",
                         "0.0", "0.0", "Take Away", "Kasir 1", "Kasir 1", "", "Cash", "Refund", "Barang rusak"])
    return csv_bytes(MOKA_HEADER, rows)


def pos_file_csv(end):
    """Juragan's POS template for Cileunyi: one row per item per day (the POS only exports daily totals), a few returns."""
    rows = []
    for d, sold in daily_sales(end, CILEUNYI).items():
        for it, q in sold:
            rows.append([ddmmyyyy(d, "/"), "", "Jual", it["sku"], q, it["rate"] * q, 0, 0])
        if d.day % 7 == 0 and sold:
            it = sold[0][0]
            rows.append([ddmmyyyy(d, "/"), "", "Retur", it["sku"], -1, 0, 0, it["rate"]])
    return csv_bytes(["Tanggal", "No struk", "Jenis", "SKU", "Qty", "Penjualan kotor", "Diskon", "Retur"], rows)


def sales_net(end, outlet):
    """{date: net sales} as the POS reports it (gross - discounts - refunds), parsed back from the generated file."""
    out = {}
    if outlet == CIBIRU:
        for r in csv.DictReader(io.StringIO(moka_csv(end).decode())):
            d = dt.datetime.strptime(r["Date"], "%d-%m-%Y").date()
            out[d] = out.get(d, 0) + int(float(r["Net Sales"]))
    else:
        for r in csv.DictReader(io.StringIO(pos_file_csv(end).decode())):
            d = dt.datetime.strptime(r["Tanggal"], "%d/%m/%Y").date()
            out[d] = out.get(d, 0) + int(r["Penjualan kotor"]) - int(r["Diskon"]) - int(r["Retur"])
    return out


def takings_csv(end, outlet=CIBIRU, days=7):
    """Daily takings for the last `days` sales days: cash and QRIS per day; one day the cash deposit is Rp 50.000 short."""
    net = sales_net(end, outlet)
    rows = []
    for d in window(end)[-days:]:
        total = net.get(d, 0)
        qris = (total * 35 // 100) // 100 * 100
        cash = total - qris
        fee = qris * 3 // 1000          # MDR 0,3%
        short = 50_000 if d == window(end)[-3] else 0
        rows.append([ddmmyyyy(d, "/"), "Tunai", cash, cash - short, 0, f"SETOR TUNAI {ddmmyyyy(d, '')}"])
        rows.append([ddmmyyyy(d, "/"), "QRIS", qris, qris - fee, fee, f"QRIS SETTLEMENT {ddmmyyyy(d, '')}"])
    return csv_bytes(["Tanggal", "Metode", "Menurut POS", "Disetor", "Potongan", "Referensi"], rows)


NEW_ITEMS = [  # a new product line added through the item list import
    ("TKC-0201", "Madu murni 250 ml", "Sembako", "", "", "Pcs", 38500, 32000), ("TKC-0202", "Kurma 500 g", "Sembako", "", "", "Pcs", 36000, 29500),
    ("TKC-0203", "Kaos kaki anak S", "Bayi dan anak", "KAOSKAKI", "S", "Pcs", 9500, 6500),
    ("TKC-0204", "Kaos kaki anak M", "Bayi dan anak", "KAOSKAKI", "M", "Pcs", 9500, 6500),
    ("TKC-0205", "Kaos kaki anak L", "Bayi dan anak", "KAOSKAKI", "L", "Pcs", 10000, 6900),
    ("TKC-0206", "Dodol garut (titipan)", "Kue dan jajanan titipan", "", "", "Pcs", 15000, 11000),
]


def item_master_csv():
    rows = []
    for i, (sku, name, cat, grp, var, uom, rate, cost) in enumerate(NEW_ITEMS):
        tit = "titipan" in name
        rows.append([sku, ean13(201 + i) if not tit else "", name, cat, grp, var, uom, rate, cost,
                     "Dapur Rempeyek Contoh" if tit else "", cost if tit else ""])
    return csv_bytes(["SKU", "Barcode", "Nama barang", "Kategori", "Grup varian", "Varian", "Satuan", "Harga jual", "Harga pokok",
                      "Penitip", "Harga setor"], rows)


def loyverse_csv(day):
    """A small Loyverse 'Receipts by item' file (guessed headers, see modules/outlet_sales/imports/loyverse_receipts.rk)."""
    d = ddmmyyyy(day, "/")
    a, b = items()[0], items()[1]
    hdr = ["Date", "Receipt number", "Receipt type", "Category", "SKU", "Item", "Variant", "Modifiers applied", "Quantity",
           "Gross sales", "Discounts", "Net sales", "Cost of goods", "Gross profit", "Taxes", "Dining option", "POS", "Store",
           "Cashier name", "Customer name", "Customer contacts", "Comment", "Status"]
    rows = [[d, "1-1001", "Sale", "Sembako", a["sku"], a["item_name"], "", "", 3, a["rate"] * 3, 0, a["rate"] * 3, 0, 0, 0, "", "POS 1", "Toko Contoh Uji", "Kasir", "", "", "", "Closed"],
            [d, "1-1001", "Sale", "Sembako", b["sku"], b["item_name"], "", "", 5, b["rate"] * 5, 500, b["rate"] * 5 - 500, 0, 0, 0, "", "POS 1", "Toko Contoh Uji", "Kasir", "", "", "", "Closed"],
            [d, "1-1002", "Refund", "Sembako", a["sku"], a["item_name"], "", "", 1, a["rate"], 0, a["rate"], 0, 0, 0, "", "POS 1", "Toko Contoh Uji", "Kasir", "", "", "", "Closed"]]
    return csv_bytes(hdr, rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed-file", help="write the seed fixtures to this file")
    ap.add_argument("--out", help="write the CSV files to this directory")
    ap.add_argument("--end", default=dt.date.today().isoformat(), help="first day after the sales window (default today)")
    a = ap.parse_args()
    if a.seed_file:
        with open(a.seed_file, "w") as f:
            f.write(seed_text())
    if a.out:
        end = dt.date.fromisoformat(a.end)
        os.makedirs(a.out, exist_ok=True)
        files = {"stok-awal-cibiru.csv": opening_stock_csv(end, CIBIRU), "stok-awal-cileunyi.csv": opening_stock_csv(end, CILEUNYI),
                 "moka-item-details-cibiru.csv": moka_csv(end), "penjualan-file-pos-cileunyi.csv": pos_file_csv(end),
                 "setoran-cibiru.csv": takings_csv(end), "daftar-barang-baru.csv": item_master_csv()}
        for name, data in files.items():
            with open(os.path.join(a.out, name), "wb") as f:
                f.write(data)
        print(f"{len(files)} files in {a.out}")


if __name__ == "__main__":
    main()
