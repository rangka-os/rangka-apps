"""Hand-written payroll oracle for the tests (module payroll): the expected slip, deposits and client invoice of one month.

Written from the research (reports/keputusan-riset/payroll-manpower.md in the project folder), not from the .rk files:
TER is looked up as a bracket table (the model sums increments), Pasal 17 as layers, BPJS per programme. Integer rupiah
throughout; the rounding rules are the model's documented ones (BPJS and pay lines rounded half up to the rupiah, PPh 21
truncated to the rupiah, PKP truncated to thousands).
"""
from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR

# PP 58/2023 lampiran: (upper bound of the monthly gross, rate %) per category; above the last bound 34%.
TER = {
    "A": [(5_400_000, "0"), (5_650_000, "0.25"), (5_950_000, "0.5"), (6_300_000, "0.75"), (6_750_000, "1"), (7_500_000, "1.25"),
          (8_550_000, "1.5"), (9_650_000, "1.75"), (10_050_000, "2"), (10_350_000, "2.25"), (10_700_000, "2.5"), (11_050_000, "3"),
          (11_600_000, "3.5"), (12_500_000, "4"), (13_750_000, "5"), (15_100_000, "6"), (16_950_000, "7"), (19_750_000, "8"),
          (24_150_000, "9"), (26_450_000, "10"), (28_000_000, "11"), (30_050_000, "12"), (32_400_000, "13"), (35_400_000, "14"),
          (39_100_000, "15"), (43_850_000, "16"), (47_800_000, "17"), (51_400_000, "18"), (56_300_000, "19"), (62_200_000, "20"),
          (68_600_000, "21"), (77_500_000, "22"), (89_000_000, "23"), (103_000_000, "24"), (125_000_000, "25"), (157_000_000, "26"),
          (206_000_000, "27"), (337_000_000, "28"), (454_000_000, "29"), (550_000_000, "30"), (695_000_000, "31"), (910_000_000, "32"),
          (1_400_000_000, "33")],
    "B": [(6_200_000, "0"), (6_500_000, "0.25"), (6_850_000, "0.5"), (7_300_000, "0.75"), (9_200_000, "1"), (10_750_000, "1.5"),
          (11_250_000, "2"), (11_600_000, "2.5"), (12_600_000, "3"), (13_600_000, "4"), (14_950_000, "5"), (16_400_000, "6"),
          (18_450_000, "7"), (21_850_000, "8"), (26_000_000, "9"), (27_700_000, "10"), (29_350_000, "11"), (31_450_000, "12"),
          (33_950_000, "13"), (37_100_000, "14"), (41_100_000, "15"), (45_800_000, "16"), (49_500_000, "17"), (53_800_000, "18"),
          (58_500_000, "19"), (64_000_000, "20"), (71_000_000, "21"), (80_000_000, "22"), (93_000_000, "23"), (109_000_000, "24"),
          (129_000_000, "25"), (163_000_000, "26"), (211_000_000, "27"), (374_000_000, "28"), (459_000_000, "29"), (555_000_000, "30"),
          (704_000_000, "31"), (957_000_000, "32"), (1_405_000_000, "33")],
    "C": [(6_600_000, "0"), (6_950_000, "0.25"), (7_350_000, "0.5"), (7_800_000, "0.75"), (8_850_000, "1"), (9_800_000, "1.25"),
          (10_950_000, "1.5"), (11_200_000, "1.75"), (12_050_000, "2"), (12_950_000, "3"), (14_150_000, "4"), (15_550_000, "5"),
          (17_050_000, "6"), (19_500_000, "7"), (22_700_000, "8"), (26_600_000, "9"), (28_100_000, "10"), (30_100_000, "11"),
          (32_600_000, "12"), (35_400_000, "13"), (38_900_000, "14"), (43_000_000, "15"), (47_400_000, "16"), (51_200_000, "17"),
          (55_800_000, "18"), (60_400_000, "19"), (66_700_000, "20"), (74_500_000, "21"), (83_200_000, "22"), (95_600_000, "23"),
          (110_000_000, "24"), (134_000_000, "25"), (169_000_000, "26"), (221_000_000, "27"), (390_000_000, "28"), (463_000_000, "29"),
          (561_000_000, "30"), (709_000_000, "31"), (965_000_000, "32"), (1_419_000_000, "33")],
}
CATEGORY = {"tk0": "A", "tk1": "A", "k0": "A", "tk2": "B", "tk3": "B", "k1": "B", "k2": "B", "k3": "C"}
PTKP = {"tk0": 54_000_000, "tk1": 58_500_000, "tk2": 63_000_000, "tk3": 67_500_000,
        "k0": 58_500_000, "k1": 63_000_000, "k2": 67_500_000, "k3": 72_000_000}
PASAL17 = [(60_000_000, 5), (250_000_000, 15), (500_000_000, 25), (5_000_000_000, 30), (None, 35)]


def rnd(x):
    """Half up to the rupiah (the kernel's round() for positive amounts)."""
    return int(Decimal(str(x)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def floor(x):
    return int(Decimal(str(x)).to_integral_value(rounding=ROUND_FLOOR))


def ter_rate(status, gross):
    for upper, rate in TER[CATEGORY[status]]:
        if gross <= upper:
            return Decimal(rate)
    return Decimal(34)


def pasal17(pkp):
    tax, lower = Decimal(0), 0
    for upper, rate in PASAL17:
        if pkp <= lower:
            break
        part = (pkp if upper is None else min(pkp, upper)) - lower
        tax += Decimal(part) * rate / 100
        lower = upper or lower
    return tax


def slip(p, run, ytd=None):
    """p: the worker's month (pay_basis, days, day_rate, monthly_salary, fixed_allowance, daily_allowance, overtime_hours,
    overtime_days, overtime_rate, thr_months, other_income, other_deduction, ptkp, has_tax_id, tk, jp, kes, last_month);
    run: umk, jkk, jp_cap, kes_cap, workdays; ytd: gross, pph21, pension, months of earlier submitted slips this year."""
    ytd = ytd or {"gross": 0, "pph21": 0, "pension": 0, "months": 0}
    monthly = p.get("pay_basis") == "monthly"
    days = p.get("days", 0)
    base = p["monthly_salary"] if monthly else rnd(days * p["day_rate"])
    fixed = p.get("fixed_allowance", 0)
    month_wage = (p["monthly_salary"] if monthly else p["day_rate"] * (21 if run.get("workdays", 6) == 5 else 25)) + fixed
    oh, od = p.get("overtime_hours", 0), p.get("overtime_days", 0)
    if p.get("overtime_rate", 0) > 0:
        ot = rnd(oh * p["overtime_rate"])
    else:
        first = min(od, oh)
        ot = rnd(Decimal(month_wage) / 173 * (Decimal("1.5") * Decimal(str(first)) + 2 * Decimal(str(oh - first))))
    # rest day / public holiday (PP 35/2021 Pasal 31): the hours of each day split into 2x (up to 7 or 8), 3x (the next
    # one) and 4x (the rest), given here per day and summed
    w = 8 if run.get("workdays", 6) == 5 else 7
    t = [0, 0, 0]
    for h in p.get("holiday_hours_per_day", []):
        h = Decimal(str(h))
        t[0] += min(h, w); t[1] += min(max(h - w, 0), 1); t[2] += max(h - w - 1, 0)
    hot = rnd(Decimal(month_wage) / 173 * (2 * t[0] + 3 * t[1] + 4 * t[2]))
    hdays = len(p.get("holiday_hours_per_day", []))
    allow = fixed + rnd(days * p.get("daily_allowance", 0))
    thr_m = p.get("thr_months", 0)
    thr = rnd(Decimal(month_wage) * min(thr_m, 12) / 12) if thr_m >= 1 else 0
    cash = base + ot + hot + allow + thr + p.get("other_income", 0)
    bpjs_base = max(base + fixed, run["umk"])
    jp_base, kes_base = min(bpjs_base, run["jp_cap"]), min(bpjs_base, run["kes_cap"])
    tk, jp, kes = p.get("tk", True), p.get("jp", True), p.get("kes", True)
    c = {
        "jkk": rnd(Decimal(bpjs_base) * Decimal(str(run["jkk"])) / 100) if tk else 0,
        "jkm": rnd(Decimal(bpjs_base) * Decimal("0.3") / 100) if tk else 0,
        "jht_er": rnd(Decimal(bpjs_base) * Decimal("3.7") / 100) if tk else 0,
        "jht_ee": rnd(Decimal(bpjs_base) * 2 / 100) if tk else 0,
        "jp_er": rnd(Decimal(jp_base) * 2 / 100) if tk and jp else 0,
        "jp_ee": rnd(Decimal(jp_base) / 100) if tk and jp else 0,
        "kes_er": rnd(Decimal(kes_base) * 4 / 100) if kes else 0,
        "kes_ee": rnd(Decimal(kes_base) / 100) if kes else 0,
    }
    gross = cash + c["jkk"] + c["jkm"] + c["kes_er"]
    surcharge = Decimal(1) if p.get("has_tax_id", True) else Decimal("1.2")
    status = p.get("ptkp", "tk0")
    casual = p.get("employment") == "casual" and not monthly
    if casual:   # pegawai tidak tetap paid per day: daily TER (PP 58/2023), above 2.500.000 a day 50% x Pasal 17
        worked = days + hdays     # rest days worked count as days (audit F2)
        daily = Decimal(gross) / Decimal(str(worked)) if worked else Decimal(0)
        if daily <= 450_000:
            pph = 0
        elif daily <= 2_500_000:
            pph = floor(Decimal(gross) * Decimal("0.5") / 100 * surcharge)
        else:
            pph = floor(pasal17(Decimal(gross) / 2) * surcharge)   # 50% of the gross paid, per payment (PMK 168/2023 Pasal 16(2)c)
    elif p.get("last_month"):
        year_gross = ytd["gross"] + gross
        months = ytd["months"] + 1
        job = min(floor(Decimal(year_gross) * 5 / 100), 500_000 * months)
        net = year_gross - job - (ytd["pension"] + c["jht_ee"] + c["jp_ee"])
        pkp = max(0, (net - PTKP[status]) // 1000 * 1000)
        pph = floor(pasal17(pkp) * surcharge) - ytd["pph21"]
    else:
        pph = floor(Decimal(gross) * ter_rate(status, gross) / 100 * surcharge)
    ee = c["jht_ee"] + c["jp_ee"] + c["kes_ee"]
    er = c["jkk"] + c["jkm"] + c["jht_er"] + c["jp_er"] + c["kes_er"]
    return dict(c, base=base, overtime=ot, holiday_overtime=hot, allowance=allow, thr=thr, cash_gross=cash, bpjs_base=bpjs_base, gross_taxable=gross,
                pph21=pph, net_pay=cash - ee - pph - p.get("other_deduction", 0), employer_bpjs=er, employer_cost=cash + er,
                bpjs_tk=c["jkk"] + c["jkm"] + c["jht_er"] + c["jht_ee"] + c["jp_er"] + c["jp_ee"], bpjs_kes=c["kes_er"] + c["kes_ee"],
                pension=c["jht_ee"] + c["jp_ee"])


def invoice(slips, run, ppn_rate=12, dpp=(11, 12), pph23=2):
    """Client invoice of a run: wages + employer BPJS + fee (or fee only); PPN on the whole invoice, PPh 23 on all but wages."""
    cash = sum(s["cash_gross"] for s in slips)
    er = sum(s["employer_bpjs"] for s in slips)
    cost = cash + er
    fee = rnd(Decimal(cost) * Decimal(str(run["fee_percent"])) / 100) if run.get("fee_mode", "percent") == "percent" else run["fee_per_worker"] * len(slips)
    lines = [("fee", fee, False)] if run.get("billing_mode") == "fee_only" else [("wages", cash, True), ("bpjs", er, False), ("fee", fee, False)]
    sub = sum(a for _, a, _ in lines)
    ppn = rnd(Decimal(sub) * dpp[0] / dpp[1] * ppn_rate / 100) if run.get("pkp", True) else 0
    base23 = sum(a for _, a, ex in lines if not ex)
    w = rnd(Decimal(base23) * pph23 / 100) if run.get("pph23", True) else 0
    return dict(lines=lines, subtotal=sub, ppn=ppn, grand_total=sub + ppn, pph23_base=base23, pph23=w, amount_due=sub + ppn - w, fee=fee, cost=cost)
