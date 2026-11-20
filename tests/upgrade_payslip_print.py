"""Upgrade check for payroll 2.3.0 (employee app, rangka-os#261 slice a, audit 261a F1):

    python3 tests/upgrade.py --site demo-manpower --check payslip_print

Old tree (payroll 2.2, the print reads the worker and the profile through .Links): one monthly worker with a job title
and a bank account, a run with one submitted slip. After the upgrade the slip has no slip_* copies (the kernel does
not backfill a computed link read on a submitted document), and the print still shows the name, the employee number,
the job title and the bank line, from the linked worker and profile.
"""


def before(api):
    st, w = api.create("worker", {"worker_name": "Lama Cetak Slip", "employee_no": "OLD-PRN", "role_title": "Juru masak", "active": True})
    assert st == 201, (st, w)
    st, prof = api.create("payroll_profile", {"worker": w["name"], "ptkp_status": "tk0", "pay_basis": "monthly", "monthly_salary": 6_000_000,
                                              "nik": "9999000000000301", "bank_name": "Bank Contoh", "bank_account": "0099000123"})
    assert st == 201, (st, prof)
    st, prj = api.create("project", {"project_name": "Kontrak cetak slip", "customer": "CUST-0001", "state": "running", "billing_model": "time_material"})
    assert st == 201, (st, prj)
    st, run = api.create("payroll_run", {"project": prj["name"], "umk": 5_600_000, "jkk_rate": 0.54, "jp_wage_cap": 11_086_300,
                                         "kes_wage_cap": 12_000_000, "workdays_per_week": 6, "tax_year": 2026, "tax_month": 8,
                                         "period_from": "2026-07-21", "period_to": "2026-08-20", "billing_mode": "none"})
    assert st == 201, (st, run)
    st, sl = api.create("payslip", {"payroll_run": run["name"], "profile": prof["name"], "days": 22})
    assert st == 201, (st, sl)
    st, r = api.verb("payslip", sl["name"], "submit")
    assert st == 200, (st, r)
    return {"slip": r.get("record", r).get("name", sl["name"])}


def after(api, s, d1):
    sl = api.get("payslip", s["slip"])
    d1.eq("the old submitted slip has no slip_* copies after the upgrade",
          tuple(sl.get(f) or "" for f in ("slip_worker_name", "slip_employee_no", "slip_role_title", "slip_bank_account")), ("", "", "", ""))
    d1.check_print("the old slip printed by finance after the upgrade", api, "payslip", s["slip"], "payslip",
                   ["Lama Cetak Slip", "OLD-PRN", "Juru masak", "Ditransfer ke Bank Contoh 0099000123"])
