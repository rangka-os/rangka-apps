"""Upgrade check for the project ledger (tests/upgrade.py --site demo-jasa --check ledger --from <tree without the
ledger>): documents submitted before project_ledger existed, cancelled after the upgrade, leave the margin at 0, and
no reversal row can be written by hand for them."""


def before(api):
    ndt = [i for i in api.list("item") if i.get("kind") == "service"][0]["name"]
    st, prj = api.create("project", {"project_name": "Proyek sebelum upgrade", "customer": "CUST-0001"})
    st, inv = api.create("sales_invoice", {"customer": "CUST-0001", "project": prj["name"], "items": [{"item": ndt, "qty": 1, "rate": 10_000_000}]})
    st, resp = api.verb("sales_invoice", inv["name"], "submit", {"force": True})
    return {"project": prj["name"], "invoice": resp.get("name", inv["name"])}


def after(api, s, d1):
    st, resp = api.verb("sales_invoice", s["invoice"], "cancel")
    d1.check("cancel the pre-upgrade project invoice", st == 200, (st, resp))
    rows = lambda: api.find("project_ledger", "project == '%s'" % s["project"])
    d1.eq("no ledger row for a document from before the ledger: margin 0, not -10.000.000", [(r["kind"], r["amount"]) for r in rows()], [])
    base = {"project": s["project"], "posting_date": "2026-11-20", "kind": "revenue", "source_model": "sales_invoice",
            "source_name": s["invoice"], "sales_invoice": s["invoice"], "amount": -10_000_000}
    for extra in ({}, {"entry": "reverse"}, {"entry": "post"}):
        st, resp = api.create("project_ledger", dict(base, **extra))
        d1.check(f"a hand-written -10.000.000 row for the old cancelled invoice {extra or ''} -> 422", st == 422, (st, resp))
    d1.eq("margin still 0", sum(r["amount"] for r in rows()), 0)
