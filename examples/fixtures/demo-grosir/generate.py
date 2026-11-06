#!/usr/bin/env python3
"""Synthetic demo data for CV Contoh Niaga Distribusi, a fictional FMCG sub-distributor in Bandung (grosir).

One warehouse, one canvas van, a damaged-goods room; three salesmen, each with a route and about fifteen shops and
stalls priced at one of three tiers (eceran, grosir, agen); 60 products sold per carton or pack. No real people,
businesses, brands or barcodes. Deterministic (fixed random seed), Python stdlib only.

    python3 generate.py --seed-file demo.rk     # rewrite the seed fixtures

tests/day_one_grosir.py relies on the fixed records at the top (BRG-0001..0003, CUST-0001..0005, USR-011..013).
"""
import argparse
import random

GUDANG, KANVAS, RUSAK = "OUT-01", "OUT-02", "OUT-03"
SALDO = "VND-0001"
DUS, PAK, KARTON, BAL = "UOM-001", "UOM-002", "UOM-003", "UOM-004"
UOMS = [(DUS, "Dus"), (PAK, "Pak"), (KARTON, "Karton"), (BAL, "Bal")]
CATEGORIES = ["Mi instan", "Minuman", "Minyak dan sembako", "Kopi dan teh", "Makanan ringan", "Sabun dan deterjen", "Susu"]

# (category, product, uom, cost per uom). Prices: eceran = cost x 1,12; grosir = cost x 1,08; agen = cost x 1,05.
PRODUCTS = [
    (0, "Mi goreng instan isi 40", DUS, 104000), (1, "Teh dalam botol 350 ml isi 24", KARTON, 72000),
    (2, "Minyak goreng 2 liter isi 6", KARTON, 198000),
    (0, "Mi kuah ayam bawang isi 40", DUS, 98000), (0, "Mi kuah soto isi 40", DUS, 98000), (0, "Mi cup ayam isi 24", DUS, 112000),
    (0, "Bihun instan isi 30", DUS, 81000), (0, "Mi goreng pedas isi 40", DUS, 106000), (0, "Mi telur keriting isi 20", DUS, 76000),
    (1, "Air mineral 600 ml isi 24", KARTON, 52000), (1, "Air mineral 1,5 liter isi 12", KARTON, 54000),
    (1, "Teh kotak 200 ml isi 24", KARTON, 61000), (1, "Minuman isotonik 500 ml isi 24", KARTON, 146000),
    (1, "Minuman soda kaleng isi 24", KARTON, 118000), (1, "Sirup cocopandan isi 12", KARTON, 180000),
    (1, "Minuman jeli isi 24", KARTON, 58000), (1, "Kopi botol 220 ml isi 24", KARTON, 128000),
    (2, "Minyak goreng 1 liter isi 12", KARTON, 205000), (2, "Gula pasir 1 kg isi 20", BAL, 318000),
    (2, "Tepung terigu 1 kg isi 20", BAL, 228000), (2, "Garam 250 g isi 40", BAL, 88000), (2, "Kecap manis 520 ml isi 12", KARTON, 252000),
    (2, "Saus sambal 135 ml isi 24", KARTON, 158000), (2, "Kaldu ayam saset isi 120", PAK, 52000), (2, "Sarden kaleng 155 g isi 24", KARTON, 196000),
    (2, "Beras 5 kg isi 4", BAL, 248000), (2, "Bumbu nasi goreng isi 60", PAK, 132000),
    (3, "Kopi saset hitam isi 120", PAK, 132000), (3, "Kopi saset susu isi 120", PAK, 156000), (3, "Kopi gula aren isi 120", PAK, 168000),
    (3, "Teh celup isi 48 kotak", KARTON, 262000), (3, "Teh melati 40 g isi 40", BAL, 148000), (3, "Kopi bubuk 165 g isi 24", KARTON, 286000),
    (4, "Keripik kentang 68 g isi 30", DUS, 132000), (4, "Biskuit krim isi 48", DUS, 101000), (4, "Wafer cokelat isi 48", DUS, 84000),
    (4, "Kacang kulit 150 g isi 24", DUS, 196000), (4, "Permen mint isi 24", DUS, 182000), (4, "Biskuit kelapa isi 24", DUS, 184000),
    (4, "Cokelat batang isi 48", DUS, 198000), (4, "Kerupuk udang isi 40", BAL, 92000), (4, "Makaroni pedas isi 40", BAL, 86000),
    (5, "Sabun batang isi 72", KARTON, 228000), (5, "Deterjen saset isi 120", PAK, 104000), (5, "Deterjen 800 g isi 12", KARTON, 228000),
    (5, "Sabun cuci piring 800 ml isi 12", KARTON, 156000), (5, "Pembersih lantai 800 ml isi 12", KARTON, 142000), (5, "Sampo saset isi 120", PAK, 98000),
    (5, "Pasta gigi 190 g isi 36", KARTON, 498000), (5, "Pewangi pakaian 800 ml isi 12", KARTON, 168000), (5, "Pemutih 1 liter isi 12", KARTON, 96000),
    (6, "Susu UHT cokelat 200 ml isi 24", KARTON, 116000), (6, "Susu kental manis kaleng isi 48", KARTON, 512000),
    (6, "Susu kental manis saset isi 48", PAK, 62000), (6, "Susu bubuk 400 g isi 12", KARTON, 486000), (6, "Yoghurt botol isi 24", KARTON, 154000),
    (6, "Susu UHT 1 liter isi 12", KARTON, 208000), (6, "Susu kedelai 200 ml isi 24", KARTON, 92000), (6, "Krimer kental isi 48", KARTON, 412000),
    (6, "Susu cokelat bubuk saset isi 120", PAK, 148000),
]

SALESMEN = [("USR-011", "Asep Contoh", "mon_thu"), ("USR-012", "Ujang Contoh", "tue_fri"), ("USR-013", "Wawan Contoh", "wed_sat")]
AREAS = {"USR-011": ["Cimahi", "Padalarang", "Batujajar"], "USR-012": ["Soreang", "Banjaran", "Katapang"],
         "USR-013": ["Ujungberung", "Cibiru", "Rancaekek"]}
KINDS = ["Toko", "Warung", "Toko Grosir", "Minimarket", "Kios", "Agen"]
WORDS = ["Makmur", "Berkah", "Jaya", "Sentosa", "Abadi", "Lancar", "Rejeki", "Mulya", "Sejahtera", "Barokah", "Mandiri",
         "Harapan", "Sumber", "Cahaya", "Amanah", "Mekar", "Subur", "Bersama"]
# The first five customers are fixed: the tests use them.
FIXED = [("Toko Contoh Makmur", "USR-011", "wholesale", "Cimahi", 30_000_000, 14),
         ("Warung Contoh Berkah", "USR-011", "retail", "Cimahi", 5_000_000, 7),
         ("Agen Contoh Jaya", "USR-011", "agent", "Padalarang", 15_000_000, 21),
         ("Toko Grosir Contoh Sentosa", "USR-012", "wholesale", "Soreang", 30_000_000, 14),
         ("Minimarket Contoh Abadi", "USR-013", "wholesale", "Cibiru", 25_000_000, 14)]


def price(cost, factor):
    return int(round(cost * factor / 100.0)) * 100


def build():
    rnd = random.Random(20261106)
    out = {"uom": [{"name": n, "uom_name": u} for n, u in UOMS],
           "item_category": [{"name": f"KTG-{i + 1:03d}", "category_name": c} for i, c in enumerate(CATEGORIES)],
           "outlet": [
               {"name": GUDANG, "outlet_name": "Gudang Utama", "code": "GDG", "city": "Bandung", "address": "Jl. Contoh Industri No. 8", "is_head_office": True, "active": True},
               {"name": KANVAS, "outlet_name": "Mobil Kanvas 1", "code": "KNV", "city": "Bandung", "address": "Kendaraan D 0000 CN", "active": True},
               {"name": RUSAK, "outlet_name": "Ruang Barang Rusak", "code": "RSK", "city": "Bandung", "address": "Jl. Contoh Industri No. 8 (belakang)", "active": True}],
           "user": [{"name": "USR-001", "full_name": "Rudi Contoh", "role": "Manajer penjualan"},
                    {"name": "USR-002", "full_name": "Lina Contoh", "role": "Keuangan"},
                    {"name": "USR-003", "full_name": "Hendra Contoh", "role": "Kepala gudang"}] +
                   [{"name": n, "full_name": f, "role": "Salesman"} for n, f, _ in SALESMEN],
           "vendor": [{"name": SALDO, "vendor_name": "Saldo awal", "city": "Bandung", "pkp": False, "payment_days": 0},
                      {"name": "VND-0002", "vendor_name": "PT Contoh Prinsipal Pangan", "city": "Jakarta", "pkp": True, "payment_days": 30},
                      {"name": "VND-0003", "vendor_name": "PT Contoh Minuman Segar", "city": "Bekasi", "pkp": True, "payment_days": 30},
                      {"name": "VND-0004", "vendor_name": "PT Contoh Rumah Bersih", "city": "Tangerang", "pkp": True, "payment_days": 45}]}
    items = []
    for i, (cat, name, uom, cost) in enumerate(PRODUCTS):
        items.append({"name": f"BRG-{i + 1:04d}", "item_name": name, "sku": f"CND-{i + 1:04d}", "category": f"KTG-{cat + 1:03d}",
                      "kind": "good", "stock_uom": uom, "standard_cost": cost,
                      "rate": price(cost, 1.12), "rate_wholesale": price(cost, 1.08), "rate_agent": price(cost, 1.05)})
    out["item"] = items
    custs = []
    for name, sm, tier, city, limit, days in FIXED:
        custs.append((name, sm, tier, city, limit, days))
    used = {c[0] for c in custs}
    for sm, _, _ in SALESMEN:
        while sum(1 for c in custs if c[1] == sm) < 14:
            kind, word = rnd.choice(KINDS), rnd.choice(WORDS)
            name = f"{kind} Contoh {word}"
            if name in used:
                name = f"{name} {rnd.randint(2, 9)}"
            if name in used:
                continue
            used.add(name)
            tier = "agent" if kind == "Agen" else ("wholesale" if kind in ("Toko Grosir", "Minimarket") else rnd.choice(["retail", "retail", "wholesale"]))
            limit = {"agent": 15_000_000, "wholesale": 25_000_000, "retail": 5_000_000}[tier]
            custs.append((name, sm, tier, rnd.choice(AREAS[sm]), limit, rnd.choice([7, 14, 14, 21, 30])))
    route = {n: r for n, _, r in SALESMEN}
    out["customer"] = [{"name": f"CUST-{i + 1:04d}", "customer_name": n, "salesman": sm, "price_tier": tier, "city": city,
                        "address": f"Jl. Contoh {city} No. {(i * 7) % 90 + 1}", "route_day": route[sm],
                        "credit_limit": limit, "payment_days": days, "phone": f"+62 812 0000 {i + 1:04d}"}
                       for i, (n, sm, tier, city, limit, days) in enumerate(custs)]
    return out


def opening_stock():
    """Opening quantities per product in the warehouse and the canvas van (used by the test setup)."""
    rnd = random.Random(7)
    return {GUDANG: {f"BRG-{i + 1:04d}": rnd.randint(40, 160) for i in range(len(PRODUCTS))},
            KANVAS: {f"BRG-{i + 1:04d}": rnd.randint(5, 20) for i in range(12)}}


def to_rk(data):
    def val(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, int):
            return str(v)
        s = str(v)
        if s.startswith("+") or ":" in s or s in ("yes", "no", "on", "off"):
            return "'" + s.replace("'", "''") + "'"
        return s
    lines = ["# Synthetic demo data for CV Contoh Niaga Distribusi, a fictional FMCG sub-distributor. No real people,",
             "# businesses, brands or barcodes. Generated by generate.py (python3 generate.py --seed-file demo.rk); edit the generator."]
    for model, rows in data.items():
        lines.append(f"{model}:")
        for r in rows:
            lines.append("  - {" + ", ".join(f"{k}: {val(v)}" for k, v in r.items()) + "}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-file", required=True)
    a = ap.parse_args()
    with open(a.seed_file, "w") as f:
        f.write(to_rk(build()))
