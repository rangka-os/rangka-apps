"""Upgrade check for warehouses apart from stores (rangka-apps#104, outlets 1.7.0, stock 1.5.0).

    python3 tests/upgrade.py --site demo-fnb --check warehouses

Old tree: the demo-fnb outlets (no location type), their balances, and a transfer still in transit. After migrate every
old outlet reads as a store (is_store backfilled by rangka migrate, rangka-os#61), balances are unchanged, the plan
counts the same outlets, and the in-transit transfer can be received. Retyping an outlet as a warehouse then frees its
seat (rangka-os contract 0057 §5) and the backfill flow gives its balances the new location type.
"""


def before(api):
    rows = api.find("stock_balance", "outlet == 'OUT-02' && qty > 1")
    if rows:
        item = rows[0]["item"]
    else:   # the seeded site has no stock yet: count some at OUT-02 first
        item = [i for i in api.list("item", limit=200) if i.get("kind") in ("material", "good")][0]["name"]
        st, c = api.create("stock_count", {"outlet": "OUT-02", "items": [{"item": item, "counted_qty": 10}]})
        assert st == 201, (st, c)
        st, r = api.verb("stock_count", c["name"], "submit")
        assert st == 200, (st, r)
    st, t = api.create("stock_transfer", {"from_outlet": "OUT-02", "to_outlet": "OUT-03", "items": [{"item": item, "qty": 1}]})
    assert st == 201, (st, t)
    st, r = api.verb("stock_transfer", t["name"], "submit")
    assert st == 200, (st, r)
    balances = {(b["outlet"], b["item"]): b.get("qty") for b in api.list("stock_balance", limit=1000)}
    st, plan = api.call("GET", "/api/v1/plan")
    return {"transfer": (r.get("record") or r)["name"], "item": item, "balances": balances,
            "outlets_used": (((plan or {}).get("usage") or {}).get("outlets") or {}).get("used")}


def after(api, s, d1):
    outs = api.list("outlet", limit=50)
    d1.check("every old outlet reads as a store after migrate", all(o.get("is_store") is True and (o.get("location_type") or "store") == "store" for o in outs),
             [(o["name"], o.get("location_type"), o.get("is_store")) for o in outs])
    now = {(b["outlet"], b["item"]): b.get("qty") for b in api.list("stock_balance", limit=1000)}
    d1.eq("balances are unchanged by the upgrade", now, s["balances"])
    st, plan = api.call("GET", "/api/v1/plan")
    d1.eq("the plan counts the same outlets until one is retyped", (((plan or {}).get("usage") or {}).get("outlets") or {}).get("used"), s["outlets_used"])
    st, rc = api.create("stock_transfer_receipt", {"stock_transfer": s["transfer"], "outlet": "OUT-03", "items": [{"item": s["item"], "qty": 1}]})
    if d1.check("the in-transit transfer can be received: draft -> 201", st == 201, (st, rc)):
        st, r = api.verb("stock_transfer_receipt", rc["name"], "submit")
        d1.check("the in-transit transfer is received -> 200", st == 200, (st, r))
    st, r = api.patch("outlet", "OUT-04", {"location_type": "warehouse"})
    if d1.check("OUT-04 retyped as a warehouse -> 200", st == 200, (st, r)):
        st, plan = api.call("GET", "/api/v1/plan")
        used = (((plan or {}).get("usage") or {}).get("outlets") or {}).get("used")
        if isinstance(s["outlets_used"], int):
            d1.eq("the retyped warehouse frees its outlet seat", used, s["outlets_used"] - 1)
        st, r = api.call("POST", "/api/v1/flows/stock_balance_location_backfill/run", {})
        d1.check("the location backfill runs -> 200", st in (200, 202), (st, r))
        rows = api.find("stock_balance", "outlet == 'OUT-04'")
        d1.check("OUT-04's balances read location_type warehouse after the backfill",
                 all(b.get("location_type") == "warehouse" for b in rows), [(b["name"], b.get("location_type")) for b in rows[:5]])
