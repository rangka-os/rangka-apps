"""Upgrade check for pay and monthly attendance (gladi G5/G7):

    python3 tests/upgrade.py --site demo-jasa --check manpower

Old tree: a worker without pay, a worker with pay, an assignment and three submitted daily timesheets (one without
pay). After the upgrade: old timesheets keep their amounts and costs (no overtime, so nothing changes), the one
without pay is flagged cost_missing and counted on the project margin dashboard, the ledger is untouched, and the
new overtime rates are 0 rather than empty. Expected numbers are written out by hand.
"""


def before(api):
    st, nopay = api.create("worker", {"worker_name": "Lama Tanpa Upah", "employee_no": "OLD-1", "day_rate": 200_000})
    assert st == 201, (st, nopay)
    st, paid = api.create("worker", {"worker_name": "Lama Berupah", "employee_no": "OLD-2", "day_rate": 200_000, "cost_day_rate": 120_000})
    assert st == 201, (st, paid)
    st, prj = api.create("project", {"project_name": "Proyek manpower lama", "customer": "CUST-0001", "state": "running"})
    assert st == 201, (st, prj)
    p = prj["name"]
    st, asg = api.create("assignment", {"project": p, "worker": paid["name"], "start_date": "2026-10-01", "end_date": "2026-10-31"})
    assert st == 201, (st, asg)
    names = []
    for w, a, d in ((nopay["name"], "", "2026-10-05"), (paid["name"], asg["name"], "2026-10-05"), (paid["name"], asg["name"], "2026-10-06")):
        body = {"project": p, "worker": w, "work_date": d, "days": 1, "description": "Shift gudang"}
        if a:
            body["assignment"] = a
        st, ts = api.create("timesheet", body)
        assert st == 201, (st, ts)
        st, r = api.verb("timesheet", ts["name"], "submit")
        assert st == 200, (st, r)
        names.append(r["name"])
    return {"project": p, "timesheets": names, "nopay": nopay["name"], "paid": paid["name"]}


def after(api, s, d1):
    ts = [api.get("timesheet", n) for n in s["timesheets"]]
    # the old tree priced a timesheet without assignment at 0 (day_rate came only from the assignment); the upgrade
    # does not reprice submitted documents
    d1.eq("old timesheets keep amount and cost (0 without assignment, 200.000 with; cost 0, 120.000, 120.000)",
          [(t["amount"], t["cost_amount"]) for t in ts], [(0, 0), (200_000, 120_000), (200_000, 120_000)])
    d1.eq("the old timesheet without pay is flagged, the paid ones are not", [t.get("cost_missing") in (True, "true") for t in ts], [True, False, False])
    d1.eq("old rows: no period, 0 overtime hours and rates", [(t.get("period_to") or "", t.get("overtime_hours"), t.get("overtime_rate"), t.get("overtime_cost_rate")) for t in ts],
          [("", 0, 0, 0)] * 3)
    d1.eq("old workers: overtime rates 0", [(api.get("worker", w).get("overtime_rate"), api.get("worker", w).get("overtime_cost_rate")) for w in (s["nopay"], s["paid"])], [(0, 0), (0, 0)])
    rows = api.find("project_ledger", "project == '%s'" % s["project"])
    d1.eq("ledger untouched by the upgrade: two labor rows of -120.000", sorted((r["kind"], r["amount"]) for r in rows), [("labor", -120_000), ("labor", -120_000)])
    ws = d1.dashboard(api, "project_margin", 365)
    d1.eq("project_margin counts the old timesheet without pay", d1.num(ws.get("timesheets_without_pay", {})), 1)
    # the owner fills the pay afterwards; the flagged timesheet is fixed by cancel + amend
    st, r = api.call("PATCH", f"/api/v1/r/worker/{d1.Api.q(s['nopay'])}", {"cost_day_rate": 130_000})
    d1.check("pay entered on the old worker", st == 200, (st, r))
    st, r = api.verb("timesheet", s["timesheets"][0], "cancel")
    d1.check("cancel the flagged timesheet", st == 200, (st, r))
    st, am = api.verb("timesheet", s["timesheets"][0], "amend")
    d1.check("amend it", st in (200, 201), (st, am))
    amn = (am or {}).get("name")
    st, r = api.call("PATCH", f"/api/v1/r/timesheet/{d1.Api.q(amn)}", {"cost_rate": 130_000}) if amn else (0, None)
    st, r = api.verb("timesheet", amn, "submit") if amn else (0, None)
    d1.check("submit the amended timesheet with the pay", st == 200, (st, r))
    rows = api.find("project_ledger", "project == '%s'" % s["project"])
    d1.eq("the margin now carries its cost: labor -370.000", sum(x["amount"] for x in rows), -370_000)
    ws = d1.dashboard(api, "project_margin", 365)
    d1.eq("no timesheet without pay left", d1.num(ws.get("timesheets_without_pay", {})), 0)
