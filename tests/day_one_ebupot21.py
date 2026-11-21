"""Day-one scenario of the PPh 21 Coretax exports (rangka-apps#128), run on demo-manpower after the payroll scenarios.

Four synthetic workers over one tax year: a permanent K/1 worker for all twelve months (1721-A1 in December), a permanent
worker who leaves in June (partial year A1), a permanent worker with a NIK and no NPWP (the NIK is the tax number), and a
casual daily worker paid in March (BP21, never an A1). Every amount is compared with tests/payroll_oracle.py (`slip` and
`annual`), written from the regulations and not from the .rk files. The files are the Coretax XML imports (BPMP, BP21,
BPA1) parsed back, plus the employee's own view of the A1.
"""
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import payroll_oracle as oracle  # noqa: E402
import day_one_payroll as dp  # noqa: E402

YEAR = 2031


def bind(m):
    dp.bind(m)
    globals().update(dp.H)


def xml_of(fm, period, fmt):
    st, body, ct = fm.html(f"/api/v1/r/pph21_period/{dp.Api.q(period['name'])}/print?format={fmt}")
    return st, body


def scenario_ebupot21(api):
    """One tax year of four workers -> the monthly BPMP, the casual BP21, the A1 of the last tax month, the employee's own A1."""
    fm, pa = CTX.get("fm"), CTX.get("pa")
    if not (fm and pa):
        fail("ebupot21: members", "missing")
        return
    g = dp.gen()
    st, prj = pa.create("project", {"project_name": "Alih daya ebupot", "customer": g.CLIENT, "state": "running", "billing_model": "time_material"})
    if not check("ebupot21: contract", st == 201, (st, prj)):
        return
    people = {}
    spec = [("full", "MP-921", "k1", "9999000000009921", "", "monthly", "permanent"),
            ("leaver", "MP-922", "tk0", "9999000000009922", "", "monthly", "permanent"),
            ("nonpwp", "MP-923", "tk0", "9999000000009923", "", "monthly", "permanent"),
            ("casual", "MP-924", "tk0", "9999000000009924", "", "daily", "casual")]
    for key, no, ptkp, nik, npwp, basis, emp in spec:
        st, w = fm.create("worker", {"worker_name": f"Pajak {no}", "employee_no": no, "cost_day_rate": 600_000 if key == "casual" else 400_000, "active": True})
        body = {"worker": (w or {}).get("name", ""), "ptkp_status": ptkp, "pay_basis": basis, "employment": emp, "nik": nik}
        if basis == "monthly":
            body.update(monthly_salary=9_000_000 if key == "full" else 8_000_000, fixed_allowance=1_000_000 if key == "full" else 0)
        st2, prof = fm.create("payroll_profile", body)
        if not check(f"ebupot21: {key} worker and profile", st == 201 and st2 == 201, (st, w, st2, prof)):
            return
        people[key] = {"worker": w, "profile": prof, "ptkp": ptkp, "no": no}
    # a worker without any tax number: the exports must refuse its month and say so
    st, w = fm.create("worker", {"worker_name": "Pajak MP-925", "employee_no": "MP-925", "cost_day_rate": 400_000, "active": True})
    st2, prof = fm.create("payroll_profile", {"worker": w["name"], "ptkp_status": "tk0", "pay_basis": "monthly", "monthly_salary": 8_000_000, "has_tax_id": False})
    people["noid"] = {"profile": prof, "worker": w}

    slips = {k: [] for k in people}      # oracle slips per worker in month order
    bad, last, closes = [], {}, {}
    for m in range(1, 13):
        f = f"{YEAR - 1 if m == 1 else YEAR}-{12 if m == 1 else m - 1:02d}-21"
        t = f"{YEAR}-{m:02d}-20"
        st, run = fm.create("payroll_run", dict(g.RUN, project=prj["name"], period_from=f, period_to=t, tax_month=m, tax_year=YEAR, billing_mode="none", pay_date=f"{YEAR}-{m:02d}-25"))
        if st != 201:
            bad.append((m, "run", st, run))
            break
        who = ["full"] + (["leaver"] if m <= 6 else []) + (["nonpwp"] if m == 3 else []) + (["casual"] if m == 3 else []) + (["noid"] if m == 4 else [])
        for key in who:
            body = {"payroll_run": run["name"], "profile": people[key]["profile"]["name"], "days": 22}
            if key == "leaver" and m == 6:
                body["last_month"] = True
            if key == "nonpwp":
                body["has_tax_id"] = True
            st, s = fm.create("payslip", body)
            if st == 201:
                st, resp = fm.verb("payslip", s["name"], "submit")
                s = (resp or {}).get("record", resp) if st == 200 else s
            if st not in (200, 201):
                bad.append((m, key, st, s))
                continue
            basis = people[key]["profile"].get("pay_basis")
            p = {"pay_basis": basis, "days": 22, "ptkp": people[key]["ptkp"] if key in people and "ptkp" in people[key] else "tk0",
                 "last_month": m == 12 and key == "full" or (key == "leaver" and m == 6)}
            if basis == "monthly":
                p.update(monthly_salary=9_000_000 if key == "full" else 8_000_000, fixed_allowance=1_000_000 if key == "full" else 0)
            else:
                p.update(day_rate=600_000, employment="casual")
            if key == "noid":
                p["has_tax_id"] = False
            ytd = None
            prev = slips[key]
            if p["last_month"]:
                ytd = {"gross": sum(x["gross_taxable"] for x in prev), "pph21": sum(x["pph21"] for x in prev), "pension": sum(x["pension"] for x in prev), "months": len(prev)}
            want = oracle.slip(p, dp.oracle_run(run), ytd)
            if s.get("pph21") != want["pph21"]:
                bad.append((m, key, "pph21", s.get("pph21"), want["pph21"]))
            slips[key].append(want)
            last[(key, m)] = s
        for _ in range(1):
            st, resp, closes[m] = dp.close_run(fm, run["name"])
            if st != 200:
                bad.append((m, "close", st, resp))
    if not check("ebupot21: a tax year of slips matches the hand calculation", not bad, bad[:4]):
        return

    def period(m, **over):
        st, p = fm.create("pph21_period", dict({"year": YEAR, "month": m, "seller_tin": "0000000000000001", "seller_nitku": "0000000000000001000000",
                                                "withholding_date": f"{YEAR}-{m:02d}-28"}, **over))
        return p if st == 201 else None

    # March: BPMP with the full-year worker and the NIK-only worker; the casual worker is a BP21, not in the BPMP
    p3 = period(3)
    if not check("ebupot21: period March made", p3 is not None, p3):
        return
    st, body = xml_of(fm, p3, "coretax_bpmp")
    if check("ebupot21: BPMP March downloads", st == 200, (st, body[:300])):
        root = ET.fromstring(body.encode())
        rows = root.findall("ListOfMmPayroll/MmPayroll")
        tins = sorted(r.findtext("CounterpartTin") for r in rows)
        eq("BPMP March: the three permanent workers, NIK as the tax number, the casual worker left out", tins, ["9999000000009921", "9999000000009922", "9999000000009923"])
        full = [r for r in rows if r.findtext("CounterpartTin") == "9999000000009921"][0]
        eq("BPMP: status, object code, tax period", (full.findtext("StatusTaxExemption"), full.findtext("TaxObjectCode"), full.findtext("TaxPeriodMonth"), full.findtext("TaxPeriodYear")),
           ("K/1", "21-100-01", "3", str(YEAR)))
        want = slips["full"][2]
        rate, gross = float(full.findtext("Rate")), int(full.findtext("Gross"))
        check("BPMP: Gross is the slip's bruto and Gross x Rate is its PPh 21 (to the rupiah)", gross == want["gross_taxable"] and abs(gross * rate / 100 - want["pph21"]) < 1, (gross, rate, want["pph21"]))
        eq("BPMP: withholding date and NITKU", (full.findtext("WithholdingDate"), full.findtext("IDPlaceOfBusinessActivity")), (f"{YEAR}-03-28", "0000000000000001000000"))
    st, body = xml_of(fm, p3, "coretax_bp21")
    if check("ebupot21: BP21 March downloads", st == 200, (st, body[:300])):
        rows = ET.fromstring(body.encode()).findall("ListOfBp21/Bp21")
        want = slips["casual"][0]
        daily = want["gross_taxable"] / 22
        check("BP21: the casual worker only, with the slip's bruto", len(rows) == 1 and rows[0].findtext("CounterpartTin") == "9999000000009924"
              and int(rows[0].findtext("Gross")) == want["gross_taxable"], body[:600])
        if rows:
            eq("BP21: 600.000 a day is the 0,5% band, code 21-100-24", (rows[0].findtext("TaxObjectCode"), float(rows[0].findtext("Rate")), daily > 450_000), ("21-100-24", 0.5, True))
            check("BP21: Gross x Rate is the slip's PPh 21", abs(int(rows[0].findtext("Gross")) * float(rows[0].findtext("Rate")) / 100 - want["pph21"]) < 1, want["pph21"])
    # April: a worker without NIK and NPWP blocks the export with a message
    p4 = period(4)
    st, body = xml_of(fm, p4, "coretax_bpmp")
    check("ebupot21: a month with a slip without NIK or NPWP is refused, naming the problem", st >= 400 and "NIK" in body and "SLIP-" in body, (st, body[:300]))
    # June: the leaver's A1, partial year
    p6 = period(6)
    st, body = xml_of(fm, p6, "coretax_bpa1")
    ann = fm.find("pph21_annual", "tax_year == %d" % YEAR)
    rows = {a["worker_name"]: a for a in ann}
    if check("ebupot21: one annual summary per closed period of work (leaver June, full year December)", set(rows) == {"Pajak MP-921", "Pajak MP-922"}, list(rows)):
        for key, name, status in (("leaver", "Pajak MP-922", "tk0"), ("full", "Pajak MP-921", "k1")):
            want = oracle.annual(slips[key], status)
            a = rows[name]
            bad = {k: (a.get(k), v) for k, v in want.items() if a.get(k) != v}
            check(f"ebupot21: A1 numbers of {key} match the hand calculation", not bad, bad)
        eq("the leaver is a partial year, January to June", (rows["Pajak MP-922"]["withholding_status"], rows["Pajak MP-922"]["month_start"], rows["Pajak MP-922"]["month_end"]), ("partial_year", 1, 6))
        eq("the full-year worker is January to December", (rows["Pajak MP-921"]["withholding_status"], rows["Pajak MP-921"]["month_start"], rows["Pajak MP-921"]["month_end"]), ("full_year", 1, 12))
    if check("ebupot21: BPA1 June downloads", st == 200, (st, body[:300])):
        a1 = ET.fromstring(body.encode()).findall("ListOfA1/A1")
        eq("BPA1 June: the leaver only, PartialYear, months 1 to 6", [(x.findtext("CounterpartTin"), x.findtext("StatusOfWithholding"), x.findtext("TaxPeriodMonthStart"), x.findtext("TaxPeriodMonthEnd")) for x in a1],
           [("9999000000009922", "PartialYear", "1", "6")])
        want = oracle.annual(slips["leaver"], "tk0")
        if a1:
            eq("BPA1: salary, other benefit, employer insurance, pension fee are the slips' sums",
               (int(a1[0].findtext("SalaryPensionJhtTht")), int(a1[0].findtext("OtherBenefit")), int(a1[0].findtext("InsurancePaidByEmployer")), int(a1[0].findtext("PensionContributionJhtThtFee"))),
               (want["gross_salary"], want["other_benefit"], want["insurance_employer"], want["pension_contribution"]))
    # December: the full-year A1 and no BPMP row for the full-year worker
    p12 = period(12)
    st, body = xml_of(fm, p12, "coretax_bpa1")
    if check("ebupot21: BPA1 December downloads", st == 200, (st, body[:300])):
        a1 = ET.fromstring(body.encode()).findall("ListOfA1/A1")
        eq("BPA1 December: the full-year worker, FullYear", [(x.findtext("CounterpartTin"), x.findtext("StatusOfWithholding")) for x in a1], [("9999000000009921", "FullYear")])
    st, body = xml_of(fm, p12, "coretax_bpmp")
    check("ebupot21: BPMP December carries no row for a last-month slip", st == 200 and "<MmPayroll>" not in body, (st, body[:200]))
    # the A1 PDF page of the full-year worker
    a = rows.get("Pajak MP-921")
    if a:
        want = oracle.annual(slips["full"], "k1")
        check_print("A1 print of the full-year worker", fm, "pph21_annual", a["name"], "bukti_potong_a1",
                    ["Pajak MP-921", f"Rp {want['year_pph21']:,}".replace(",", "."), f"Rp {want['year_gross']:,}".replace(",", ".")])
    # cancel the full-year last slip: the A1 disappears from the export (voided)
    ls = last[("full", 12)]
    st, _ = fm.verb("payroll_close", closes[12], "cancel")
    st, _ = fm.verb("payslip", ls["name"], "cancel")
    if check("ebupot21: cancelling the December slip works", st == 200, (st, _)):
        a2 = fm.get("pph21_annual", a["name"]) if a else {}
        eq("the annual summary of the cancelled slip is void", a2.get("voided"), True)
        st, body = xml_of(fm, p12, "coretax_bpa1")
        check("ebupot21: a void summary is not in the BPA1 file", st == 200 and "<A1>" not in body, (st, body[:200]))


def scenario_ebupot21_overlap(api):
    """Audit R1-F1 and R2-F1: a last_month tick in February and again in March. The March summary covers January to March and
    voids the February one; cancelling March brings the February summary back."""
    fm, pa = CTX.get("fm"), CTX.get("pa")
    if not (fm and pa):
        fail("ebupot21 overlap: members", "missing")
        return
    g = dp.gen()
    st, prj = pa.create("project", {"project_name": "Alih daya ebupot dua", "customer": g.CLIENT, "state": "running", "billing_model": "time_material"})
    st1, w = fm.create("worker", {"worker_name": "Pajak MP-931", "employee_no": "MP-931", "cost_day_rate": 400_000, "active": True})
    st2, prof = fm.create("payroll_profile", {"worker": (w or {}).get("name", ""), "ptkp_status": "tk0", "pay_basis": "monthly", "monthly_salary": 8_000_000, "nik": "9999000000009931"})
    if not check("ebupot21 overlap: contract, worker, profile", st == 201 and st1 == 201 and st2 == 201, (st, st1, st2)):
        return
    slips, closes = {}, {}
    for m in (1, 2, 3):
        f = f"2033-{m - 1:02d}-21" if m > 1 else "2032-12-21"
        st, run = fm.create("payroll_run", dict(g.RUN, project=prj["name"], period_from=f, period_to=f"2033-{m:02d}-20", tax_month=m, tax_year=2033, billing_mode="none", pay_date=f"2033-{m:02d}-25"))
        st, s_ = fm.create("payslip", {"payroll_run": run["name"], "profile": prof["name"], "days": 22, "last_month": m >= 2})
        st, resp = fm.verb("payslip", s_["name"], "submit")
        if not check(f"ebupot21 overlap: month {m} slip submitted", st == 200, (st, resp)):
            return
        slips[m] = ((resp or {}).get("record", resp) or {}).get("name", s_["name"])
        st, resp, closes[m] = dp.close_run(fm, run["name"])
    rows = {r["month_end"]: r for r in fm.find("pph21_annual", "worker == '%s' && tax_year == 2033" % w["name"])}
    eq("both last-month slips made a summary, only the March one (January to March) is live", (sorted(rows), rows[2]["voided"], rows[3]["voided"], rows[3]["month_start"]), ([2, 3], True, False, 1))
    fm.verb("payroll_close", closes[3], "cancel")
    st, _ = fm.verb("payslip", slips[3], "cancel")
    check("ebupot21 overlap: cancelling the March slip works", st == 200, (st, _))
    rows = {r["month_end"]: r for r in fm.find("pph21_annual", "worker == '%s' && tax_year == 2033" % w["name"])}
    eq("after the cancel the February summary is live again and the March one is void", (rows[2]["voided"], rows[3]["voided"]), (False, True))


SCENARIOS = [scenario_ebupot21, scenario_ebupot21_overlap]
