"""Upgrade check for the per-line PPh 23 base (tests/upgrade.py --site demo-jasa --check pph23): invoices written
before pph23_exclude and pph23_dpp existed keep their PPh 23 after migrate, and a down payment invoiced before the
upgrade is still deducted from the base of a final invoice made or edited after it."""


def before(api):
    ndt = [i for i in api.list("item") if i.get("kind") == "service"][0]["name"]
    body = {"customer": "CUST-0001", "pph23_applies": True, "items": [{"item": ndt, "qty": 1, "rate": 10_000_000}]}
    st, sub = api.create("sales_invoice", body)
    st, resp = api.verb("sales_invoice", sub["name"], "submit", {"force": True})
    st, draft = api.create("sales_invoice", body)
    st, dp = api.create("sales_invoice", {"customer": "CUST-0001", "kind": "down_payment", "pph23_applies": True, "items": [{"item": ndt, "qty": 1, "rate": 3_000_000}]})
    st, dpr = api.verb("sales_invoice", dp["name"], "submit", {"force": True})
    dpn = dpr.get("name", dp["name"])
    st, fin = api.create("sales_invoice", dict(body, dp_invoice=dpn, down_payment=3_000_000))
    # a USD down payment (200 x 15.000 = 3.000.000) invoiced before the upgrade: its stored PPh 23 base is empty too
    st, dpx = api.create("sales_invoice", {"customer": "CUST-0001", "kind": "down_payment", "currency": "USD", "exchange_rate": 15000, "pph23_applies": True,
                                           "items": [{"item": ndt, "qty": 1, "rate_fx": 200}]})
    st, dpxr = api.verb("sales_invoice", dpx["name"], "submit", {"force": True})
    return {"submitted": resp.get("name", sub["name"]), "draft": draft["name"], "dp": dpn, "final_draft": fin["name"], "ndt": ndt,
            "dp_fx": dpxr.get("name", dpx["name"])}


def after(api, s, d1):
    old = api.get("sales_invoice", s["submitted"])
    d1.eq("old submitted invoice keeps PPh 23 200.000 and amount_due 10.900.000", (old["pph23_amount"], old["amount_due"]), (200_000, 10_900_000))
    st, wh = api.create("withholding", {"customer": "CUST-0001", "sales_invoice": s["submitted"], "cert_no": "BP-UPG-1", "cert_date": "2026-11-05"})
    d1.check("withholding on the old invoice defaults to its dpp: 10.000.000, 200.000", st == 201 and (wh["dpp"], wh["amount"]) == (10_000_000, 200_000), (st, wh))
    st, raw, ctype = api.print_html("sales_invoice", s["submitted"], "")
    d1.check("old invoice print shows PPh 23 without 'dasar Rp 0'", st == 200 and "dasar Rp 0" not in raw and "Rp 200.000" in raw, raw[-600:] if isinstance(raw, str) else raw)
    st, upd = api.call("PATCH", f"/api/v1/r/sales_invoice/{d1.Api.q(s['draft'])}", {"po_number": "PO-UPG"})
    d1.check("old draft edited after migrate keeps PPh 23: base 10.000.000, PPh 23 200.000",
             st == 200 and (upd.get("pph23_dpp"), upd.get("pph23_amount")) == (10_000_000, 200_000), (st, upd))
    # a down payment invoiced before the upgrade (no stored base) is deducted like a new one
    st, fin = api.create("sales_invoice", {"customer": "CUST-0001", "pph23_applies": True, "dp_invoice": s["dp"], "down_payment": 3_000_000,
                                          "items": [{"item": s["ndt"], "qty": 1, "rate": 10_000_000}]})
    d1.check("new final invoice deducting an old DP: base 7.000.000, PPh 23 140.000 (as before the upgrade)",
             st == 201 and (fin.get("pph23_dpp"), fin.get("pph23_amount")) == (7_000_000, 140_000), (st, fin))
    st, upd = api.call("PATCH", f"/api/v1/r/sales_invoice/{d1.Api.q(s['final_draft'])}", {"po_number": "PO-UPG-2"})
    d1.check("old final draft deducting an old DP, edited after migrate: base 7.000.000, PPh 23 140.000",
             st == 200 and (upd.get("pph23_dpp"), upd.get("pph23_amount")) == (7_000_000, 140_000), (st, upd))
    # the same for a foreign-currency DP from before the upgrade: 10.000.000 - 3.000.000 = 7.000.000, 2% = 140.000
    dpx = api.get("sales_invoice", s["dp_fx"])
    d1.eq("old USD DP invoice: dpp 3.000.000", dpx.get("dpp"), 3_000_000)
    st, fin = api.create("sales_invoice", {"customer": "CUST-0001", "pph23_applies": True, "dp_invoice": s["dp_fx"], "down_payment": 3_000_000,
                                          "items": [{"item": s["ndt"], "qty": 1, "rate": 10_000_000}]})
    d1.check("new final invoice deducting an old USD DP: base 7.000.000, PPh 23 140.000 (as before the upgrade)",
             st == 201 and (fin.get("pph23_dpp"), fin.get("pph23_amount")) == (7_000_000, 140_000), (st, fin))
