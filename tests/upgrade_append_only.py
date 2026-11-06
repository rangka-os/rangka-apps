"""Upgrade check for append_only ledgers (contract 0026, t_68bc8060):

    python3 tests/upgrade.py --site demo-jasa --check append_only --from origin/main

Rows posted before project_ledger became append_only stay as they were, nobody can change them after the upgrade (on
the old tree the owner could), and the flows keep posting: a new invoice gets its row and cancelling an old one writes
its reversal.
"""


def before(api):
    ndt = [i for i in api.list("item") if i.get("kind") == "service"][0]["name"]
    st, prj = api.create("project", {"project_name": "Proyek buku tetap", "customer": "CUST-0001"})
    assert st == 201, (st, prj)
    names = []
    for rate in (10_000_000, 4_000_000):
        st, inv = api.create("sales_invoice", {"customer": "CUST-0001", "project": prj["name"], "items": [{"item": ndt, "qty": 1, "rate": rate}]})
        assert st == 201, (st, inv)
        st, resp = api.verb("sales_invoice", inv["name"], "submit", {"force": True})
        assert st == 200, (st, resp)
        names.append(resp["name"])
    rows = api.find("project_ledger", "project == '%s'" % prj["name"])
    assert sorted(r["amount"] for r in rows) == [4_000_000, 10_000_000], rows
    # the hole this change closes: on the old tree the owner may edit a posted row
    st, resp = api.call("PATCH", "/api/v1/r/project_ledger/" + rows[0]["name"], {"posting_date": rows[0]["posting_date"]})
    return {"project": prj["name"], "invoices": names, "rows": sorted((r["name"], r["amount"]) for r in rows), "ndt": ndt, "old_patch": st}


def after(api, s, d1):
    check, eq = d1.check, d1.eq
    rows = lambda: api.find("project_ledger", "project == '%s'" % s["project"])
    eq("before the upgrade the owner could still edit a posted row (the gap)", s["old_patch"], 200)
    eq("after migrate: the posted rows are unchanged", sorted((r["name"], r["amount"]) for r in rows()), s["rows"])
    name = s["rows"][0][0]
    st, resp = api.call("PATCH", "/api/v1/r/project_ledger/" + name, {"amount": 1})
    check("after the upgrade the owner cannot edit a posted row -> 409 append_only", st == 409 and ((resp or {}).get("error") or {}).get("code") == "append_only", (st, resp))
    st, resp = api.call("DELETE", "/api/v1/r/project_ledger/" + name)
    check("nor delete it -> 409 append_only", st == 409, (st, resp))
    st, resp = api.verb("sales_invoice", s["invoices"][0], "cancel")
    check("cancelling an invoice posted before the upgrade -> 200", st == 200, (st, resp))
    st, inv = api.create("sales_invoice", {"customer": "CUST-0001", "project": s["project"], "items": [{"item": s["ndt"], "qty": 1, "rate": 1_000_000}]})
    st, resp = api.verb("sales_invoice", inv["name"], "submit", {"force": True})
    check("a new project invoice after the upgrade -> 200", st == 200, (st, resp))
    eq("the flows still post: the old 10.000.000 is reversed and the new 1.000.000 posted",
       sorted(r["amount"] for r in rows()), [-10_000_000, 1_000_000, 4_000_000, 10_000_000])
    eq("margin = 4.000.000 + 1.000.000", sum(r["amount"] for r in rows()), 5_000_000)
