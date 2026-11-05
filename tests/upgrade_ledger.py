"""Upgrade check for the project ledger (tests/upgrade.py --site demo-jasa --check ledger): a project invoice
submitted before project_ledger existed, cancelled after the upgrade, leaves the margin at 0, not minus."""


def before(api):
    ndt = [i for i in api.list("item") if i.get("kind") == "service"][0]["name"]
    st, prj = api.create("project", {"project_name": "Proyek sebelum upgrade", "customer": "CUST-0001"})
    st, inv = api.create("sales_invoice", {"customer": "CUST-0001", "project": prj["name"], "items": [{"item": ndt, "qty": 1, "rate": 10_000_000}]})
    st, resp = api.verb("sales_invoice", inv["name"], "submit", {"force": True})
    return {"project": prj["name"], "invoice": resp.get("name", inv["name"])}


def after(api, s, d1):
    st, resp = api.verb("sales_invoice", s["invoice"], "cancel")
    d1.check("cancel the pre-upgrade project invoice", st == 200, (st, resp))
    rows = api.find("project_ledger", "project == '%s'" % s["project"])
    d1.eq("no ledger row for a document from before the ledger: margin 0, not -10.000.000", [(r["kind"], r["amount"]) for r in rows], [])
