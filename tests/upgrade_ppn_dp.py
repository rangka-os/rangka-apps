"""Upgrade check for the down-payment part of the PPN base (tests/upgrade.py --site demo-manpower --new-site
--check ppn_dp): in the PT with tax.ppn_fee_only, a DP invoice submitted before ppn_base existed (taxed on its whole
DPP) is deducted with that DPP from the PPN base of a settlement made after the upgrade, so its PPN is not charged
again (audit C1-3 N1). The old tree runs on this tree's site folder: the fee-only PT is new."""


def before(api):
    import sys
    d1 = sys.modules["day_one"]
    k = d1.CTX["kernel"]
    fee = d1.Api.login(k.base, k.owner_email, k.owner_pw, org="tenaga_fee")
    items = {i["sku"]: i["name"] for i in fee.list("item", limit=200) if i.get("sku")}
    wages, fee_item = items["TK-GAJI"], items["TK-FEE"]
    st, cu = fee.create("customer", {"customer_name": "PT Klien Upgrade DP", "address": "Jl. Contoh No. 12", "city": "Bekasi"})
    st, dp = fee.create("sales_invoice", {"customer": cu["name"], "kind": "down_payment", "posting_date": "2031-10-05", "due_date": "2031-10-05",
                                          "items": [{"item": fee_item, "qty": 1, "rate": 55_000_000}]})
    st, resp = fee.verb("sales_invoice", dp["name"], "submit", {"force": True})
    dp = fee.get("sales_invoice", resp.get("name", dp["name"]))
    return {"dp": dp["name"], "dp_dpp": dp["dpp"], "dp_ppn": dp["ppn"], "customer": cu["name"], "wages": wages, "fee": fee_item}


def after(api, s, d1):
    k = d1.CTX["kernel"]
    fee = d1.Api.login(k.base, k.owner_email, k.owner_pw, org="tenaga_fee")
    d1.eq("the old DP invoice: whole DPP taxed (55.000.000, PPN 6.050.000)", (s["dp_dpp"], s["dp_ppn"]), (55_000_000, 6_050_000))
    st, fin = fee.create("sales_invoice", {"customer": s["customer"], "posting_date": "2031-10-25", "due_date": "2031-10-25", "dp_invoice": s["dp"], "down_payment": 55_000_000,
                                           "items": [{"item": s["wages"], "qty": 1, "rate": 100_000_000, "pph23_exclude": True, "ppn_exclude": True},
                                                     {"item": s["fee"], "qty": 1, "rate": 10_000_000}]})
    if not d1.check("settlement after the upgrade, deducting the old DP", st == 201, (st, fin)):
        return
    d1.eq("the old DP's whole DPP is its PPN base: settlement PPN base 10.000.000 - 55.000.000 -> 0, PPN 0 (not charged twice)",
          (fin.get("ppn_dp_base"), fin.get("ppn_base"), fin.get("ppn")), (55_000_000, 0, 0))
