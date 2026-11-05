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
    return {"p": p, "ms": ms, "inv": r.get("name", i["name"]), "ndt": ndt}


def after(api, s, d1):
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
