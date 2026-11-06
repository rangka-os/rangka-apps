#!/usr/bin/env python3
"""Synthetic demo data for CV Contoh Pangan Beku, a fictional small frozen-food factory in Cimahi (produksi).

A materials store, a production room and a cold store; dimsum made from two semi-finished items (dough and filling,
each with its own BOM: a two-level recipe), nugget with a by-product, beef meatballs; eight piece-rate workers and the
stages they are paid for. No real people, businesses or brands. Deterministic, Python stdlib only.

    python3 generate.py --seed-file demo.rk     # rewrite the seed fixtures

item.default_bom points back at the BOMs, which point at the items, so the test setup (tests/day_one_produksi.py)
links them after seeding (DEFAULT_BOM) and saves the upper-level BOMs again so their cost rolls up.
"""
import argparse

BAHAN, PRODUKSI, JADI = "OUT-01", "OUT-02", "OUT-03"
SALDO = "VND-0001"
KG, PAK, PCS, LTR = "UOM-001", "UOM-002", "UOM-003", "UOM-004"

# (name, item_name, kind, uom, standard_cost, shelf_life_days)
ITEMS = [
    ("BRG-0001", "Daging ayam fillet", "material", KG, 52000, 0), ("BRG-0002", "Udang kupas", "material", KG, 98000, 0),
    ("BRG-0003", "Tepung terigu", "material", KG, 11500, 0), ("BRG-0004", "Tepung tapioka", "material", KG, 9800, 0),
    ("BRG-0005", "Bawang putih", "material", KG, 34000, 0), ("BRG-0006", "Garam", "material", KG, 6000, 0),
    ("BRG-0007", "Minyak sayur", "material", LTR, 18000, 0), ("BRG-0008", "Kemasan plastik vakum", "material", PCS, 450, 0),
    ("BRG-0009", "Tepung roti", "material", KG, 21000, 0), ("BRG-0010", "Daging sapi", "material", KG, 128000, 0),
    ("BRG-0011", "Es batu", "material", KG, 1500, 0), ("BRG-0012", "Telur ayam", "material", KG, 28000, 0),
    ("BRG-0020", "Adonan kulit dimsum", "material", KG, 10000, 3), ("BRG-0021", "Isi dimsum ayam", "material", KG, 55000, 3),
    ("BRG-0030", "Dimsum ayam isi 20", "product", PAK, 26000, 180), ("BRG-0031", "Nugget ayam 500 g", "product", PAK, 27000, 270),
    ("BRG-0032", "Bakso sapi isi 50", "product", PAK, 58000, 180), ("BRG-0033", "Remah nugget", "product", KG, 8000, 90),
]
RATES = {"BRG-0030": 38000, "BRG-0031": 39000, "BRG-0032": 82000, "BRG-0033": 12000}

# (name, bom_name, item, output_qty, labour_per_unit, overhead_per_unit, [(item, qty)], [(byproduct, qty, share)])
BOMS = [
    ("BOM-0001", "Adonan kulit dimsum", "BRG-0020", 10, 0, 300, [("BRG-0003", 6), ("BRG-0004", 2), ("BRG-0006", 0.1), ("BRG-0007", 0.4)], []),
    ("BOM-0002", "Isi dimsum ayam", "BRG-0021", 10, 0, 500,
     [("BRG-0001", 7), ("BRG-0002", 1.5), ("BRG-0005", 0.3), ("BRG-0004", 0.8), ("BRG-0006", 0.1), ("BRG-0012", 0.5)], []),
    ("BOM-0003", "Dimsum ayam isi 20", "BRG-0030", 100, 1300, 800, [("BRG-0020", 12), ("BRG-0021", 30), ("BRG-0008", 100)], []),
    ("BOM-0004", "Nugget ayam 500 g", "BRG-0031", 100, 800, 700,
     [("BRG-0001", 35), ("BRG-0003", 5), ("BRG-0009", 8), ("BRG-0012", 4), ("BRG-0005", 0.5), ("BRG-0006", 0.3), ("BRG-0008", 100)],
     [("BRG-0033", 3, 2)]),
    ("BOM-0005", "Bakso sapi isi 50", "BRG-0032", 100, 900, 900,
     [("BRG-0010", 40), ("BRG-0004", 8), ("BRG-0005", 0.6), ("BRG-0006", 0.4), ("BRG-0011", 6), ("BRG-0008", 100)], []),
]
DEFAULT_BOM = {b[2]: b[0] for b in BOMS}

STAGES = [("TAHAP-001", "Giling dan adon", "kg", 400), ("TAHAP-002", "Cetak dimsum", "pak", 900),
          ("TAHAP-003", "Cetak nugget", "pak", 500), ("TAHAP-004", "Cetak bakso", "pak", 600), ("TAHAP-005", "Kemas", "pak", 200)]
WORKERS = ["Ani", "Budi", "Cucu", "Dadang", "Euis", "Fajar", "Giri", "Heni"]


def build():
    out = {"uom": [{"name": n, "uom_name": u} for n, u in ((KG, "Kg"), (PAK, "Pak"), (PCS, "Pcs"), (LTR, "Liter"))],
           "item_category": [{"name": "KTG-001", "category_name": "Bahan baku"}, {"name": "KTG-002", "category_name": "Setengah jadi"},
                             {"name": "KTG-003", "category_name": "Produk jadi"}],
           "outlet": [
               {"name": BAHAN, "outlet_name": "Gudang Bahan", "code": "BHN", "city": "Cimahi", "address": "Jl. Contoh Industri Kecil No. 3", "is_head_office": True, "active": True},
               {"name": PRODUKSI, "outlet_name": "Ruang Produksi", "code": "PRD", "city": "Cimahi", "address": "Jl. Contoh Industri Kecil No. 3", "active": True},
               {"name": JADI, "outlet_name": "Gudang Beku Barang Jadi", "code": "JDI", "city": "Cimahi", "address": "Jl. Contoh Industri Kecil No. 3", "active": True}],
           "user": [{"name": "USR-001", "full_name": "Sari Contoh", "role": "Pemilik"}, {"name": "USR-002", "full_name": "Tono Contoh", "role": "Mandor produksi"}],
           "vendor": [{"name": SALDO, "vendor_name": "Saldo awal", "city": "Cimahi", "pkp": False, "payment_days": 0},
                      {"name": "VND-0002", "vendor_name": "PT Contoh Unggas Segar", "city": "Bandung", "pkp": True, "payment_days": 14},
                      {"name": "VND-0003", "vendor_name": "CV Contoh Tepung Jaya", "city": "Bandung", "pkp": False, "payment_days": 30},
                      {"name": "VND-0004", "vendor_name": "UD Contoh Kemasan", "city": "Cimahi", "pkp": False, "payment_days": 7}]}
    cat = {"material": "KTG-001", "product": "KTG-003"}
    out["item"] = [dict({"name": n, "item_name": nm, "kind": k, "stock_uom": u, "standard_cost": c,
                         "category": "KTG-002" if n in ("BRG-0020", "BRG-0021") else cat[k]},
                        **({"shelf_life_days": s} if s else {}), **({"rate": RATES[n]} if n in RATES else {}))
                   for n, nm, k, u, c, s in ITEMS]
    out["bom"] = [{"name": n, "bom_name": bn, "item": it, "output_qty": oq, "labour_per_unit": lab, "overhead_per_unit": ovh, "active": True,
                   "lines": [{"item": i, "qty": q} for i, q in lines],
                   **({"byproducts": [{"item": i, "qty": q, "cost_share": s} for i, q, s in by]} if by else {})}
                  for n, bn, it, oq, lab, ovh, lines, by in BOMS]
    out["production_stage"] = [{"name": n, "stage_name": s, "unit": u, "rate": r, "active": True} for n, s, u, r in STAGES]
    out["worker"] = [{"name": f"WRK-{i + 1:04d}", "worker_name": f"{w} Contoh", "role_title": "Pekerja borongan", "active": True}
                     for i, w in enumerate(WORKERS)]
    return out


def opening_stock():
    """Opening materials in the production room (the materials store keeps the rest), for the test setup."""
    return {PRODUKSI: {"BRG-0001": 120, "BRG-0002": 20, "BRG-0003": 80, "BRG-0004": 60, "BRG-0005": 10, "BRG-0006": 8,
                       "BRG-0007": 10, "BRG-0008": 1500, "BRG-0009": 40, "BRG-0010": 90, "BRG-0011": 40, "BRG-0012": 30},
            BAHAN: {"BRG-0001": 200, "BRG-0003": 200, "BRG-0008": 5000, "BRG-0010": 100}}


def to_rk(data):
    def val(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        if isinstance(v, list):
            return "[" + ", ".join(val(x) for x in v) + "]"
        if isinstance(v, dict):
            return "{" + ", ".join(f"{k}: {val(x)}" for k, x in v.items()) + "}"
        s = str(v)
        return "'" + s.replace("'", "''") + "'" if (s.startswith("+") or ":" in s) else s
    lines = ["# Synthetic demo data for CV Contoh Pangan Beku, a fictional small frozen-food factory. No real people,",
             "# businesses or brands. Generated by generate.py (python3 generate.py --seed-file demo.rk); edit the generator."]
    for model, rows in data.items():
        lines.append(f"{model}:")
        for r in rows:
            lines.append("  - " + val(r))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-file", required=True)
    a = ap.parse_args()
    with open(a.seed_file, "w") as f:
        f.write(to_rk(build()))
