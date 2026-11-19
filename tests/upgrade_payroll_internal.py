"""Upgrade check for payroll 2.0 (internal runs, rangka-apps#101 slice a):

    python3 tests/upgrade.py --site demo-manpower --check payroll_internal

Old tree (payroll 1.x, every run is a client contract): one monthly worker on one project, a run closed with its client
invoice drafted (wages + BPJS + 10% fee) and a second run still open with a submitted slip. After the upgrade:
- both runs are client runs with the same run key, the closed run keeps its totals, the slips keep every amount and
  read 0 for the new fields (unpaid days, lateness), the draft invoice and the margin row are untouched;
- the open run with an old submitted slip still saves (pay date) and still refuses a rate change, and its close books
  the margin row as before;
- cancelling the old close (made before run_kind existed) still reverses its margin row and reopens the run.
"""


def before(api):
    st, w = api.create("worker", {"worker_name": "Lama Gaji Bulanan", "employee_no": "OLD-P2", "active": True})
    assert st == 201, (st, w)
    st, prof = api.create("payroll_profile", {"worker": w["name"], "ptkp_status": "tk0", "pay_basis": "monthly", "monthly_salary": 7_000_000,
                                              "fixed_allowance": 500_000, "nik": "9999000000000201"})
    assert st == 201, (st, prof)
    st, prj = api.create("project", {"project_name": "Kontrak gaji lama", "customer": "CUST-0001", "state": "running", "billing_model": "time_material"})
    assert st == 201, (st, prj)
    p = prj["name"]
    run = {"umk": 5_600_000, "jkk_rate": 0.54, "jp_wage_cap": 11_086_300, "kes_wage_cap": 12_000_000, "workdays_per_week": 6, "tax_year": 2026}
    st, ra = api.create("payroll_run", dict(run, project=p, period_from="2026-07-21", period_to="2026-08-20", tax_month=8, billing_mode="wages_plus_fee",
                                            fee_mode="percent", fee_percent=10, wage_item="BRG-0001", bpjs_item="BRG-0002", fee_item="BRG-0003",
                                            invoice_date="2026-08-26", pph23_applies=True))
    assert st == 201, (st, ra)
    st, sa = api.create("payslip", {"payroll_run": ra["name"], "profile": prof["name"], "days": 22})
    assert st == 201, (st, sa)
    st, r = api.verb("payslip", sa["name"], "submit")
    assert st == 200, (st, r)
    sa = r.get("record", r)
    st, c = api.create("payroll_close", {"payroll_run": ra["name"]})
    assert st == 201, (st, c)
    st, r = api.verb("payroll_close", c["name"], "submit")
    assert st == 200, (st, r)
    close = r.get("record", r).get("name", c["name"])
    inv = api.find("sales_invoice", f"project == '{p}' && docstatus == 0")
    assert len(inv) == 1, inv
    st, rb = api.create("payroll_run", dict(run, project=p, period_from="2026-08-21", period_to="2026-09-20", tax_month=9, billing_mode="none"))
    assert st == 201, (st, rb)
    st, sb = api.create("payslip", {"payroll_run": rb["name"], "profile": prof["name"], "days": 22})
    assert st == 201, (st, sb)
    st, r = api.verb("payslip", sb["name"], "submit")
    assert st == 200, (st, r)
    sb = r.get("record", r)
    ra = api.get("payroll_run", ra["name"])
    keep = ("base_pay", "cash_gross", "bpjs_base", "gross_taxable", "pph21", "net_pay", "employer_cost")
    return {"project": p, "run_a": ra["name"], "run_b": rb["name"], "close": close, "invoice": inv[0]["name"], "grand_total": inv[0]["grand_total"],
            "slip_a": sa["name"], "slip_b": sb["name"], "slip_a_amounts": [sa.get(f) for f in keep], "slip_b_amounts": [sb.get(f) for f in keep],
            "keep": keep, "run_a_totals": [ra.get(f) for f in ("headcount", "total_net_pay", "total_employer_cost", "total_pph21")],
            "run_a_key": ra.get("run_key"), "extra": ra.get("extra_cost")}


def after(api, s, d1):
    ra, rb = api.get("payroll_run", s["run_a"]), api.get("payroll_run", s["run_b"])
    d1.eq("every old run becomes a client run", (ra.get("run_kind"), rb.get("run_kind")), ("client", "client"))
    d1.eq("the run key of an old run does not change (project:period_from)", (ra.get("run_key"), rb.get("run_key")),
          (s["run_a_key"], f"{s['project']}:2026-08-21"))
    d1.eq("the closed run keeps its totals and stays closed",
          ([ra.get(f) for f in ("headcount", "total_net_pay", "total_employer_cost", "total_pph21")], ra.get("closed")), (s["run_a_totals"], True))
    for key, name in (("slip_a", s["slip_a"]), ("slip_b", s["slip_b"])):
        sl = api.get("payslip", name)
        d1.eq(f"old slip {name} keeps every amount", [sl.get(f) for f in s["keep"]], s[key + "_amounts"])
        d1.eq(f"old slip {name} reads 0 for the new fields", tuple(sl.get(f) for f in ("unpaid_days", "unpaid_deduction", "late_min_total", "late_deduction")),
              (0, 0, 0, 0))
    inv = api.get("sales_invoice", s["invoice"])
    d1.eq("the old draft client invoice is untouched", (inv.get("docstatus"), inv.get("grand_total")), (0, s["grand_total"]))
    rows = api.find("project_ledger", f"project == '{s['project']}' && kind == 'payroll'")
    d1.eq("the old margin row is untouched", sorted(r["amount"] for r in rows), [0 - s["extra"]])
    # the open run with an old submitted slip: the locks still hold, and nothing else is refused
    st, r = api.call("PATCH", f"/api/v1/r/payroll_run/{d1.Api.q(s['run_b'])}", {"pay_date": "2026-09-25"})
    d1.check("the open old run still saves (pay date)", st == 200, (st, r))
    st, r = api.call("PATCH", f"/api/v1/r/payroll_run/{d1.Api.q(s['run_b'])}", {"jkk_rate": 1.27})
    d1.check("its rates stay locked by the old submitted slip", st == 422, (st, r))
    st, r = api.call("PATCH", f"/api/v1/r/payroll_run/{d1.Api.q(s['run_b'])}", {"run_kind": "internal", "project": ""})
    d1.check("an old run cannot be turned into an internal run once it has a submitted slip", st == 422, (st, r))
    st, c = api.create("payroll_close", {"payroll_run": s["run_b"]})
    st, r = api.verb("payroll_close", c["name"], "submit") if st == 201 else (st, c)
    d1.check("the open old run closes after the upgrade", st == 200, (st, r))
    rows = api.find("project_ledger", f"project == '{s['project']}' && kind == 'payroll'")
    d1.eq("its close books its margin row as before (client run)", len(rows), 2)
    d1.eq("billing none: still one client invoice", len(api.find("sales_invoice", f"project == '{s['project']}'")), 1)
    # the close made before run_kind existed: cancelling it must still reverse the margin row
    st, r = api.verb("payroll_close", s["close"], "cancel")
    d1.check("the old close cancels (its invoice is only a draft)", st == 200, (st, r))
    rows = api.find("project_ledger", f"project == '{s['project']}' && kind == 'payroll' && payroll_close == '{s['close']}'")
    d1.eq("the old close's margin row is reversed (post + reverse net to 0)", (len(rows), sum(r["amount"] for r in rows)), (2, 0))
    d1.eq("the old run is open again", api.get("payroll_run", s["run_a"]).get("closed"), False)
    # a run made after the upgrade on this site (projects installed) is a client run by default
    st, rn = api.create("payroll_run", {"project": s["project"], "period_from": "2026-09-21", "period_to": "2026-10-20", "tax_year": 2026, "tax_month": 10,
                                        "umk": 5_600_000, "billing_mode": "none"})
    d1.eq("a new run on a site with projects defaults to a client run", (st, (rn or {}).get("run_kind")), (201, "client"))
