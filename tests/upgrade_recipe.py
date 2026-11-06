"""Upgrade check for recipe.active_product (rangka-apps#12): recipes written before the field existed get it on
migrate (the kernel backfills new compute columns, rangka-os#61), so the unique index holds on an upgraded site too.

    python3 tests/upgrade.py --site demo-fnb --check recipe

Old tree: one product with an active recipe and an inactive one. After migrate the active one holds the product,
the inactive one holds nothing, and a second active recipe for the product is refused.
"""

KG, PORSI = "UOM-001", "UOM-006"


def before(api):
    st, mat = api.create("item", {"item_name": "Tahu upgrade (Kg)", "kind": "material", "stock_uom": KG, "sku": "UPG-TAHU"})
    assert st == 201, (st, mat)
    st, prod = api.create("item", {"item_name": "Porsi upgrade", "kind": "product", "stock_uom": PORSI, "sales_uom": PORSI, "sku": "UPG-POR", "rate": 20000})
    assert st == 201, (st, prod)
    line = [{"material": mat["name"], "qty": 0.1}]
    st, on = api.create("recipe", {"product": prod["name"], "recipe_name": "Porsi upgrade", "lines": line})
    assert st == 201, (st, on)
    st, off = api.create("recipe", {"product": prod["name"], "recipe_name": "Porsi upgrade lama", "active": False, "lines": line})
    assert st == 201, (st, off)
    return {"mat": mat["name"], "prod": prod["name"], "on": on["name"], "off": off["name"]}


def after(api, s, d1):
    on, off = api.get("recipe", s["on"]), api.get("recipe", s["off"])
    d1.eq("old active recipe gets active_product = its product on migrate", on.get("active_product"), s["prod"])
    d1.eq("old inactive recipe gets an empty active_product", off.get("active_product") or "", "")
    st, resp = api.create("recipe", {"product": s["prod"], "recipe_name": "Porsi upgrade 2", "lines": [{"material": s["mat"], "qty": 0.2}]})
    d1.check("a second active recipe for the upgraded product -> 422", st == 422, (st, resp))
    st, resp = api.call("PATCH", f"/api/v1/r/recipe/{d1.Api.q(s['off'])}", {"active": True})
    d1.check("activating the old inactive recipe while the other is active -> 422", st == 422, (st, resp))
