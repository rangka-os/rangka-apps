"""Upgrade check for the PPN base and kurs locks (tests/upgrade.py --site demo-manpower --check ppn_lever, cards
t_98c8d504 and t_aeea3903): drafts a sales_manager made before the upgrade, one in the fee-only PT with a wage line out
of the PPN base and one in USD at kurs 1,01 in the PKP PT, wait for finance when submitted after it. The approvals read
the stored draft at submit, so no data migration is needed."""
import secrets

ACCOUNTS = (("tenaga_fee", "sm.fee.upgrade@contohtenaga.test"), ("tenaga_contoh", "sm.pkp.upgrade@contohtenaga.test"))


def before(api):
    import sys
    d1 = sys.modules["day_one"]
    k = d1.CTX["kernel"]
    pw = secrets.token_urlsafe(18)   # throwaway password of a throwaway database
    cl = {}
    for org, email in ACCOUNTS:
        code, out = k.user_add(email, "sales_manager", org, "Sales upgrade", password=pw)
        cl[org] = d1.Api.login(k.base, email, pw, org=org) if code == 0 else None
        if cl[org] is None:
            raise RuntimeError(f"{email}: {out}")
    fee = cl["tenaga_fee"]
    items = {i["sku"]: i["name"] for i in fee.list("item", limit=200) if i.get("sku")}
    st, cu = fee.create("customer", {"customer_name": "PT Klien Upgrade Kunci", "address": "Jl. Contoh No. 13", "city": "Bekasi"})
    st, wage = fee.create("sales_invoice", {"customer": cu["name"], "posting_date": "2031-10-05", "due_date": "2031-10-05", "items": [
        {"item": items["TK-GAJI"], "qty": 1, "rate": 40_000_000, "pph23_exclude": True, "ppn_exclude": True},
        {"item": items["TK-FEE"], "qty": 1, "rate": 4_000_000}]})
    pkp = cl["tenaga_contoh"]
    items2 = [i["name"] for i in pkp.list("item", limit=200)]
    st, cu2 = pkp.create("customer", {"customer_name": "Client Upgrade Valas", "address": "Jl. Contoh No. 14", "city": "Jakarta"})
    st, fx = pkp.create("sales_invoice", {"customer": cu2["name"], "currency": "USD", "exchange_rate": 1.01, "posting_date": "2031-10-05", "due_date": "2031-10-05",
                                          "items": [{"item": items2[0], "qty": 1, "rate_fx": 10_000}]})
    return {"pw": pw, "wage": wage.get("name"), "wage_base": wage.get("ppn_base"), "fx": fx.get("name"), "fx_ppn": fx.get("ppn")}


def after(api, s, d1):
    k = d1.CTX["kernel"]
    d1.eq("before the upgrade: the wage draft's PPN base 4.000.000, the USD draft at kurs 1,01 PPN 1.111", (s["wage_base"], s["fx_ppn"]), (4_000_000, 1_111))
    for (org, email), key, policy in zip(ACCOUNTS, ("wage", "fx"), ("ppn_base_excludes", "fx_rate_check")):
        cl = d1.Api.login(k.base, email, s["pw"], org=org)
        if not d1.check(f"{org}: the sales_manager signs in after the upgrade", cl is not None, email):
            continue
        st, r = cl.verb("sales_invoice", s[key], "submit", {"force": True})
        pend = [a.get("policy") for a in d1.approvals_of(cl, "sales_invoice", s[key]) if a.get("state") == "pending"]
        d1.check(f"{org}: the old draft submitted by the sales_manager after the upgrade waits for finance (202, {policy})",
                 st == 202 and policy in pend, (st, r, pend))
