#!/usr/bin/env python3
"""Synthetic demo data for PT Contoh Tenaga Mandiri, a fictional outsourcing firm in Karawang (pilot A shape).

Twenty operators placed at one client factory, paid monthly from the attendance recap (cut-off 21 Sep to 20 Oct 2026,
paid in October): fourteen daily operators, one paid under the UMK, one without a tax number, one whose overtime is
paid by PP 35/2021 (no weighted rate), two monthly supervisors and one team leader above the JP and BPJS Kesehatan wage
caps. Names, NIK and every number are made up. Deterministic, Python stdlib only.

    python3 generate.py --seed-file demo.rk     # rewrite the seed fixtures

MONTH is the October recap (days present, overtime hours) the tests and the demo import; UMK and the run settings are
the fictional site's.
"""
import argparse

CLIENT = "CUST-0001"
WAGES, BPJS, FEE = "BRG-0001", "BRG-0002", "BRG-0003"
UMK = 5_600_000          # fictional UMK of the client's site
RUN = {"period_from": "2026-09-21", "period_to": "2026-10-20", "tax_year": 2026, "tax_month": 10, "pay_date": "2026-10-25",
       "umk": UMK, "jkk_rate": 0.54, "jp_wage_cap": 11_086_300, "kes_wage_cap": 12_000_000, "workdays_per_week": 6,
       "billing_mode": "wages_plus_fee", "fee_mode": "percent", "fee_percent": 10, "wage_item": WAGES, "bpjs_item": BPJS,
       "fee_item": FEE, "invoice_date": "2026-10-26", "pph23_applies": True}

# (employee_no, name, role, sell/day, pay/day, sell OT/h, pay OT/h, ptkp, basis, monthly salary, fixed allowance,
#  allowance per day, has tax id, JP)
NAMES = ["Ade", "Bayu", "Cahya", "Dimas", "Eka", "Fitri", "Galih", "Hana", "Irfan", "Joko", "Kiki", "Lestari", "Maman",
         "Nining", "Opik", "Puput", "Rudi", "Sinta", "Taufik", "Wulan"]
PTKP = ["tk0", "k0", "k1", "k2", "tk1", "k3", "tk2", "k0", "tk0", "k1", "k2", "tk0", "k0", "tk3", "tk0", "k1", "tk0", "k1", "k3", "k2"]
WORKERS = []
for i, n in enumerate(NAMES):
    no = f"MP-{i + 1:03d}"
    if i < 14:
        row = (no, n, "Operator produksi", 310_000, 235_000, 45_000, 34_000, PTKP[i], "daily", 0, 0, 25_000, True, True)
    elif i == 14:   # paid under the UMK: 200.000 x 25 = 5.000.000 < 5.600.000
        row = (no, n, "Helper", 280_000, 200_000, 40_000, 30_000, PTKP[i], "daily", 0, 0, 25_000, True, True)
    elif i == 15:   # no NIK/NPWP on file: PPh 21 x 120%
        row = (no, n, "Operator produksi", 310_000, 235_000, 45_000, 34_000, PTKP[i], "daily", 0, 0, 25_000, False, True)
    elif i == 16:   # overtime by PP 35/2021: no weighted pay rate, the slip gets the days with overtime
        row = (no, n, "Operator mesin", 320_000, 240_000, 45_000, 0, PTKP[i], "daily", 0, 0, 25_000, True, True)
    elif i < 19:    # monthly supervisors
        row = (no, n, "Supervisor shift", 450_000, 340_000, 0, 0, PTKP[i], "monthly", 7_500_000, 1_000_000, 0, True, True)
    else:           # team leader above the JP (11.086.300) and BPJS Kesehatan (12.000.000) caps
        row = (no, n, "Team leader", 700_000, 560_000, 0, 0, PTKP[i], "monthly", 12_500_000, 1_500_000, 0, True, True)
    WORKERS.append(row)

# October recap: employee_no -> (days present, overtime hours)
MONTH = {w[0]: (22 + (i % 5), [0, 6, 10, 14, 20][i % 5]) for i, w in enumerate(WORKERS)}
MONTH["MP-017"] = (25, 12)
MONTH["MP-018"], MONTH["MP-019"], MONTH["MP-020"] = (24, 0), (25, 0), (26, 0)
# Field attendance (A2): the client's factory and its warehouse, fictional points in Karawang and Cikampek (decimal
# degrees), with the radius a worker must clock in within. The site supervisor of each is a member whose `outlets` list
# holds the site code (tests/day_one_attendance.py).
SITES = [{"name": "LOK-001", "site_name": "Pabrik PT Contoh Garmen Sejahtera", "customer": CLIENT, "address": "Kawasan Industri Contoh Blok A1, Karawang",
          "lat": -6.3105, "lng": 107.2950, "radius_m": 150, "active": True},
         {"name": "LOK-002", "site_name": "Gudang PT Contoh Garmen Sejahtera", "customer": CLIENT, "address": "Jalan Contoh Raya 12, Cikampek",
          "lat": -6.4050, "lng": 107.4560, "radius_m": 200, "active": True}]
# Edits finance makes on the draft slips before submitting them.
EDITS = {"MP-001": {"other_deduction": 300_000}, "MP-017": {"overtime_days": 5}, "MP-019": {"thr_tenure_months": 7}}


def profile_input(w):
    """The oracle's input for one worker (tests/payroll_oracle.py), from the fixtures and the October recap."""
    no, _, _, _, pay, _, ot_pay, ptkp, basis, monthly, fixed, daily, tax_id, jp = w
    days, oh = MONTH[no]
    p = {"pay_basis": basis, "days": days, "day_rate": pay, "monthly_salary": monthly, "fixed_allowance": fixed,
         "daily_allowance": daily, "overtime_hours": oh, "overtime_rate": ot_pay, "ptkp": ptkp, "has_tax_id": tax_id, "jp": jp}
    e = EDITS.get(no, {})
    p.update(overtime_days=e.get("overtime_days", 0), thr_months=e.get("thr_tenure_months", 0), other_deduction=e.get("other_deduction", 0))
    return p


def recap_csv(project):
    head = "Proyek,No. pegawai,Nama,Dari tanggal,Sampai tanggal,Hari hadir,Jam lembur\n"
    return head + "".join(f"{project},{w[0]},{w[1]} Contoh,21/09/2026,20/10/2026,{MONTH[w[0]][0]},{MONTH[w[0]][1] or ''}\n" for w in WORKERS)


def build():
    out = {"uom": [{"name": "UOM-001", "uom_name": "Bulan"}, {"name": "UOM-002", "uom_name": "Paket"}],
           "customer": [{"name": CLIENT, "customer_name": "PT Contoh Garmen Sejahtera", "city": "Karawang", "payment_days": 14,
                         "credit_limit": 1_000_000_000}],
           "item": [{"name": WAGES, "item_name": "Gaji tenaga kerja alih daya", "kind": "service", "stock_uom": "UOM-001", "sales_uom": "UOM-001", "sku": "TK-GAJI"},
                    {"name": BPJS, "item_name": "Iuran BPJS bagian perusahaan", "kind": "service", "stock_uom": "UOM-001", "sales_uom": "UOM-001", "sku": "TK-BPJS"},
                    {"name": FEE, "item_name": "Management fee tenaga kerja", "kind": "service", "stock_uom": "UOM-002", "sales_uom": "UOM-002", "sku": "TK-FEE"}],
           "outlet": [{"name": "OUT-01", "outlet_name": "Kantor Karawang", "code": "KRW", "city": "Karawang", "is_head_office": True, "active": True}],
           "user": [{"name": "USR-001", "full_name": "Rina Contoh", "role": "finance_manager", "email": "rina@contohtenaga.test"},
                    {"name": "USR-002", "full_name": "Asep Contoh", "role": "project_admin", "email": "asep@contohtenaga.test"},
                    {"name": "USR-003", "full_name": "Sinta Contoh", "role": "site_supervisor", "email": "sinta@contohtenaga.test"},
                    {"name": "USR-004", "full_name": "Taufik Contoh", "role": "site_supervisor", "email": "taufik@contohtenaga.test"}]}
    out["worker"] = [{"name": f"WRK-{i + 1:04d}", "worker_name": f"{w[1]} Contoh", "employee_no": w[0], "role_title": w[2], "day_rate": w[3],
                      "cost_day_rate": w[4], "overtime_rate": w[5], "overtime_cost_rate": w[6], "active": True} for i, w in enumerate(WORKERS)]
    out["payroll_profile"] = [{"name": f"PGJ-{i + 1:04d}", "worker": f"WRK-{i + 1:04d}", "nik": f"9999{i + 1:012d}", "has_tax_id": w[12],
                               "ptkp_status": w[7], "pay_basis": w[8], "monthly_salary": w[9], "fixed_allowance": w[10], "daily_allowance": w[11],
                               "bpjs_tk": True, "bpjs_jp": w[13], "bpjs_kes": True, "bank_name": "Bank Contoh", "bank_account": f"000{i + 1:07d}", "active": True}
                              for i, w in enumerate(WORKERS)]
    out["work_site"] = SITES
    return out


def to_rk(data):
    def val(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        if isinstance(v, dict):
            return "{" + ", ".join(f"{k}: {val(x)}" for k, x in v.items()) + "}"
        s = str(v)
        return "'" + s.replace("'", "''") + "'" if (s.startswith("+") or ":" in s or "," in s or s.isdigit()) else s
    lines = ["# Synthetic demo data for PT Contoh Tenaga Mandiri, a fictional outsourcing firm. No real people, businesses",
             "# or brands; NIK are fake. Generated by generate.py (python3 generate.py --seed-file demo.rk); edit the generator."]
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
