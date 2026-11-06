"""Upgrade check for billing milestones (tests/upgrade.py --site demo-jasa --check milestones --from d1d100a): milestones
made before contract_snapshot and milestone_claim existed keep working: the next report makes the next one ready, it
can be billed, and an old invoice can be cancelled and amended."""


def before(api):
    ndt = [i for i in api.list("item") if i.get("kind") == "service"][0]["name"]
    st, p = api.create("project", {"project_name": "Lama", "customer": "CUST-0001", "billing_model": "milestone", "contract_value": 100_000_000})
    p = p["name"]
    ms = [api.create("billing_milestone", {"project": p, "seq": i, "title": f"T{i}", "basis": "percent", "percent": 50, "trigger": "acceptance"})[1]["name"] for i in (1, 2)]
    st, b = api.create("acceptance", {"project": p, "accepted_on": "2026-11-20", "accepted_by": "PIC", "scope": "x"})
    api.verb("acceptance", b["name"], "submit")
    st, i = api.create("sales_invoice", {"billing_milestone": ms[0], "items": [{"item": ndt, "qty": 1, "rate": 50_000_000}]})
    st, r = api.verb("sales_invoice", i["name"], "submit", {"force": True})
    st, q = api.create("project", {"project_name": "Lama borongan", "customer": "CUST-0001", "contract_value": 100_000_000})
    st, qi = api.create("sales_invoice", {"customer": "CUST-0001", "project": q["name"], "items": [{"item": ndt, "qty": 1, "rate": 100_000_000}]})
    api.verb("sales_invoice", qi["name"], "submit", {"force": True})
    # a retainer project with November billed before the upgrade (no month key then), and a milestone of another kind
    st, rp = api.create("project", {"project_name": "Lama retainer", "customer": "CUST-0001", "billing_model": "retainer", "contract_value": 15_000_000})
    nov = {"project": rp["name"], "kind": "retainer", "basis": "amount", "amount_fixed": 15_000_000, "trigger": "date", "due_date": "2026-11-30", "period_from": "2026-11-01", "period_to": "2026-11-30"}
    st, rm = api.create("billing_milestone", dict(nov, seq=1, title="Nov"))
    st, ri = api.create("sales_invoice", {"billing_milestone": rm["name"], "items": [{"item": ndt, "qty": 1, "rate": 15_000_000}]})
    api.verb("sales_invoice", ri["name"], "submit", {"force": True})
    st, om = api.create("billing_milestone", dict(nov, seq=2, title="Lain", kind="milestone"))
    return {"p": p, "ms": ms, "inv": r.get("name", i["name"]), "ndt": ndt, "q": q["name"], "rp": rp["name"], "nov": nov, "om": om.get("name", "") if st == 201 else ""}


def after(api, s, d1):
    # L1: a project billed before the upgrade keeps its contract frozen
    st, r = api.call("PATCH", f"/api/v1/r/project/{d1.Api.q(s['p'])}", {"contract_value": 200_000_000})
    d1.check("L1: raising the contract of a project billed before the upgrade -> 422", st == 422, (st, r))
    # L2: invoices from before the upgrade count against the contract
    st, i = api.create("sales_invoice", {"customer": "CUST-0001", "project": s["q"], "items": [{"item": s["ndt"], "qty": 1, "rate": 100_000_000}]})
    st2, r2 = api.verb("sales_invoice", i["name"], "submit", {"force": True}) if st == 201 else (st, i)
    d1.check("L2: a project billed 100.000.000 before the upgrade takes no further 100.000.000 on a 100.000.000 contract -> 422", st2 == 422, (st2, r2))
    st, b = api.create("acceptance", {"project": s["p"], "accepted_on": "2026-11-21", "accepted_by": "PIC", "scope": "y"})
    st, r = api.verb("acceptance", b["name"], "submit")
    d1.check("old schedule: the next report submits and makes milestone 2 ready", st == 200 and api.get("billing_milestone", s["ms"][1])["state"] == "ready", (st, r))
    d1.eq("old percent milestone keeps its amount (50.000.000)", api.get("billing_milestone", s["ms"][1])["amount"], 50_000_000)
    st, i = api.create("sales_invoice", {"billing_milestone": s["ms"][1], "items": [{"item": s["ndt"], "qty": 1, "rate": 50_000_000}]})
    st2, r2 = api.verb("sales_invoice", i["name"], "submit", {"force": True}) if st == 201 else (st, i)
    d1.check("old milestone 2 is billed after the upgrade", st2 == 200, (st2, r2))
    st, r = api.verb("sales_invoice", s["inv"], "cancel")
    d1.check("an invoice from before the upgrade can be cancelled", st == 200, (st, r))
    st, r = api.verb("sales_invoice", s["inv"], "amend")
    d1.check("and amended", st in (200, 201), (st, r))
    # R2 legacy: November billed before the upgrade is not billed again through a second November milestone
    st, m = api.create("billing_milestone", dict(s["nov"], seq=3, title="Nov lagi"))
    st2, r2 = (None, None)
    if st == 201:
        st, i = api.create("sales_invoice", {"billing_milestone": m["name"], "items": [{"item": s["ndt"], "qty": 1, "rate": 15_000_000}]})
        st2, r2 = api.verb("sales_invoice", i["name"], "submit", {"force": True}) if st == 201 else (st, i)
    d1.check("R2 legacy: November billed before the upgrade, a second November milestone (422) or its invoice (422) is refused",
             st == 422 or st2 == 422, (st, st2, r2))
    billed = sum(i["dpp"] for i in api.find("sales_invoice", "project == '%s' && docstatus == 1" % s["rp"]))
    d1.eq("R2 legacy: retainer project billed 15.000.000 for November", billed, 15_000_000)
    # R1 legacy: a non-retainer milestone made on a retainer project before the upgrade cannot be invoiced
    if s["om"]:
        st, i = api.create("sales_invoice", {"billing_milestone": s["om"], "items": [{"item": s["ndt"], "qty": 1, "rate": 15_000_000}]})
        st2 = api.verb("sales_invoice", i["name"], "submit", {"force": True})[0] if st == 201 else st
        d1.check("R1 legacy: invoicing an old non-retainer milestone of a retainer project -> 422", st2 == 422, (st, st2))
