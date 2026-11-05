"""Upgrade check for the per-line PPh 23 base (tests/upgrade.py --site demo-jasa --check pph23): invoices written
before pph23_exclude and pph23_dpp existed keep their PPh 23 after migrate."""


def before(api):
    ndt = [i for i in api.list("item") if i.get("kind") == "service"][0]["name"]
    body = {"customer": "CUST-0001", "pph23_applies": True, "items": [{"item": ndt, "qty": 1, "rate": 10_000_000}]}
    st, sub = api.create("sales_invoice", body)
    st, resp = api.verb("sales_invoice", sub["name"], "submit", {"force": True})
    st, draft = api.create("sales_invoice", body)
    return {"submitted": resp.get("name", sub["name"]), "draft": draft["name"]}


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
