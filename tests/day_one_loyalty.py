"""Day-one scenarios of module loyalty (rangka-apps#102) on demo-kasir, and deposits on invoices on demo-langganan.

Loaded by tests/day_one.py after day_one_pos, day_one_kitchen and day_one_promotions (bind() hands over their
helpers): `python3 tests/day_one.py --site demo-kasir` and `--site demo-langganan`. Expected figures come from
tests/loyalty_oracle.py (an independent calculation of the issue's rules). Covered: sign-up at the counter (the base
tier, a customer made, one member per number), points earned and redeemed with their cap, a void giving both back, the
member discount against a bill promo (larger applies, never both), a tier upgrade on the year's spend and the
1 January review, a deposit top-up on a shift and a sale paid partly and wholly from it, two sales racing for one
balance, the shift's expected cash and journals, a refund above Rp 1.000.000 waiting for approval, points
adjustments, expiry after inactivity, the nightly balance check, roles, the receipt and its WhatsApp print, the
dashboard; on demo-langganan a desk top-up and an invoice paid from the deposit, cancelled and refused when short.

Members: Sari (cashier, Kedai Riau), Dika (cashier, Toko Dago), Rina (head_office) come from day_one_pos; demo-langganan
uses its finance manager (fm) and the owner.
"""
import datetime
import threading

import loyalty_oracle as oracle

H = {}
DGO, RIU = "OUT-02", "OUT-03"
KSA, KHT, PULSA = "BRG-0021", "BRG-0022", "BRG-0004"   # kopi susu aren 22.000, kopi hitam 15.000, a service 3.000
BASE, SILVER = "TIER-01", "TIER-02"
SARI, DIKA = "sari.riau@contohwarung.test", "dika.dago@contohwarung.test"

_R = {"read"}
MATRIX = {
    "member_tier": {"head_office": {"read", "create", "write", "delete", "export"}, "finance_manager": _R, "sales_manager": _R, "cashier": _R, "outlet_staff": _R},
    "loyalty_account": {"head_office": {"read", "create", "write", "delete", "export", "print"}, "finance_manager": {"read", "write", "export"},
                        "sales_manager": {"read", "write"}, "cashier": {"read", "create", "write", "print"}, "outlet_staff": _R},
    # append_only (contract 0026): /meta offers no create; the flows still write rows as the roles below
    "loyalty_entry": {"head_office": {"read", "create", "export"}, "finance_manager": {"read", "export"}, "cashier": {"read", "create"}},
    "deposit_entry": {"head_office": {"read", "create", "export"}, "finance_manager": {"read", "create", "export"}, "sales_manager": {"read", "create"}, "cashier": {"read", "create"}},
    "deposit_topup": {"head_office": {"read", "create", "write", "delete", "submit", "cancel", "export", "print"},
                      "finance_manager": {"read", "create", "write", "delete", "submit", "cancel", "export", "print"},
                      "cashier": {"read", "create", "write", "delete", "submit", "cancel", "print"}},
    "deposit_refund": {"head_office": {"read", "create", "write", "delete", "submit", "cancel", "export", "print"}, "finance_manager": {"read", "export", "print"}},
    "points_adjustment": {"head_office": {"read", "create", "write", "delete", "submit", "cancel", "export"}, "finance_manager": {"read", "export"}},
    "loyalty_mismatch": {"head_office": {"read", "create", "write", "delete", "export"}},
    "loyalty_guard": {"head_office": {"create"}, "finance_manager": {"create"}, "sales_manager": {"create"}, "cashier": {"create"}},   # never stored
}
APPEND_ONLY = {"loyalty_entry", "deposit_entry"}


def bind(m):
    for k in ("ok", "fail", "check", "eq", "CTX", "denied", "dashboard", "kernel_today", "decide", "approvals_of", "page_text"):
        H[k] = getattr(m, k)
    globals().update(H)
    m.MATRIX.update(MATRIX)
    m.APPEND_ONLY.update(APPEND_ONLY)


def rec(x):
    return (x or {}).get("record", x) or {}


def rules(resp):
    return [e.get("rule") for e in (resp or {}).get("errors", [])] if isinstance(resp, dict) else []


def today():
    return kernel_today().isoformat()


def compute(cl, body):
    st, resp = cl.call("POST", "/api/v1/compute/pos_sale", body)
    resp = resp or {}
    return st, resp.get("record") or {}, [e.get("rule") for e in resp.get("errors") or []]


def shift(cl, email, outlet):
    rows = cl.find("pos_shift", f"cashier == '{email}' && outlet == '{outlet}' && state == 'open'")
    if rows:
        return rows[0]["name"]
    o = cl.get("outlet", outlet) or {}
    st, s = cl.create("pos_shift", {"outlet": outlet, "opening_cash": o.get("cash_float") or 0})
    check(f"{email} opens a shift at {outlet}", st == 201, (st, s))
    return rec(s).get("name")


def sale(cl, shift_name, lines, submit=True, **extra):
    st, s = cl.create("pos_sale", dict({"shift": shift_name, "items": lines}, **extra))
    if st != 201 or not submit:
        return st, (rec(s) if st == 201 else s)
    st, r = cl.verb("pos_sale", rec(s)["name"], "submit")
    return st, (rec(r) if st in (200, 202) else r)


def member(api, name):
    return api.get("loyalty_account", name) or {}


def ledger_sum(api, model, account, field):
    return sum(r[field] for r in api.find(model, f"account == '{account}'"))


def tier(api, name):
    t = api.get("member_tier", name) or {}
    return {k: t.get(k, 0) for k in ("member_discount_pct", "point_value", "max_redeem_pct", "earn_spend", "earn_multiplier_pct")}


def people():
    sari, dika, hq = CTX.get("sari"), CTX.get("dika"), CTX.get("hq")
    if not (sari and dika and hq):
        fail("loyalty: members signed in", CTX.get("login_error"))
        return None
    return sari, dika, hq


def signup(cl, label, name, phone, **extra):
    st, a = cl.create("loyalty_account", dict({"member_name": name, "phone": phone}, **extra))
    check(f"{label}: sign-up -> 201", st == 201, (st, a))
    return rec(a) if st == 201 else {}


def scenario_loyalty_signup(api):
    """A cashier signs a buyer up at the counter: the base tier with its figures, a customer made with the name and
    number, the member found by its number; the same number again (another spelling) is refused; balances cannot be
    typed in."""
    m = people()
    if not m:
        return
    sari, dika, hq = m
    a = signup(sari, "cashier at Riau", "Ibu Uji Member", "0812-0000-0401")
    if not a:
        return
    a = member(api, a["name"])
    eq("the base tier and its figures, 0 points, a customer linked", (a.get("tier"), a.get("tier_name"), a.get("earn_spend"), a.get("point_value"), a.get("points_balance"), bool(a.get("customer"))),
       (BASE, "Member", 10000, 100, 0, True))
    c = api.get("customer", a.get("customer")) or {}
    eq("the customer made has the member's name and number", (c.get("customer_name"), c.get("phone")), ("Ibu Uji Member", "0812-0000-0401"))
    CTX["loy_a"] = a["name"]
    st, resp = dika.create("loyalty_account", {"member_name": "Orang lain", "phone": "+62 812 0000 0401"})
    check("the same number written another way at another outlet -> 422 (phone_member, or the unique number)",
          st == 422 and ("phone_member" in rules(resp) or any(e.get("field") == "wa_number" and e.get("code") == "unique" for e in (resp or {}).get("errors", []))), (st, resp))
    st, resp = sari.create("loyalty_account", {"member_name": "Nomor salah", "phone": "12"})
    check("a number that is not one -> 422 phone_valid", st == 422 and "phone_valid" in rules(resp), (st, resp))
    st, found = dika.call("GET", "/api/v1/r/loyalty_account?q=0401")
    names = [r.get("name") for r in ((found or {}).get("data") or found or [])] if isinstance(found, (dict, list)) else []
    check("the cashier of another outlet finds the member by number", a["name"] in names, (st, found))
    sari.patch("loyalty_account", a["name"], {"points_balance": 99999, "deposit_balance": 5000000, "tier": SILVER})
    a2 = member(api, a["name"])
    eq("a cashier cannot type points, a deposit or a tier (read_only)", (a2.get("points_balance"), a2.get("deposit_balance"), a2.get("tier")), (0, 0, BASE))


def scenario_loyalty_points(api):
    """Earn on Rp 88.000 (8 points), a correction by head office, redeem 100 points (Rp 10.000 off), the 50% cap, more
    than the balance, a void that gives both back; the ledger always sums to the balance; receipt and WhatsApp print."""
    m = people()
    if not m or not CTX.get("loy_a"):
        return
    sari, dika, hq = m
    a = CTX["loy_a"]
    t = tier(api, BASE)
    s_riu = shift(sari, SARI, RIU)
    want = oracle.receipt(88000, t)
    st, r, errs = compute(sari, {"shift": s_riu, "member": a, "items": [{"item": KSA, "qty": 4}]})
    eq("compute: 4 x 22.000 earns 8 points (oracle)", (st, r.get("points_earned"), r.get("grand_total"), r.get("amount_due")), (200, want["points_earned"], 88000, 88000))
    st, s = sale(sari, s_riu, [{"item": KSA, "qty": 4}], member=a)
    if not check("the member's sale is paid -> 200", st == 200, (st, s)):
        return
    eq("8 points on the member, one earn row", (member(api, a).get("points_balance"), [(x["kind"], x["points"]) for x in api.find("loyalty_entry", f"source == '{s['name']}'")]), (8, [("earn", 8)]))
    eq("the year's spend and the last purchase", (member(api, a).get("spend_this_year"), member(api, a).get("last_activity")), (88000, today()))
    st, adj = hq.create("points_adjustment", {"account": a, "points": 1000, "reason": "Uji: poin awal dari kartu lama"})
    st2, r2 = hq.verb("points_adjustment", rec(adj).get("name", "-"), "submit")
    check("head office adds 1.000 points (no approval up to 1.000) -> 200", st == 201 and st2 == 200, (st, adj, st2, r2))
    eq("1.008 points", member(api, a).get("points_balance"), 1008)
    want = oracle.receipt(44000, t, points=100)
    st, r, errs = compute(sari, {"shift": s_riu, "member": a, "points_redeemed": 100, "items": [{"item": KSA, "qty": 2}]})
    eq("redeem 100 points on 44.000: 10.000 off, 34.000, earns 3 (oracle)", (r.get("points_discount"), r.get("grand_total"), r.get("points_earned"), r.get("points_left"), errs),
       (want["points_discount"], want["grand_total"], want["points_earned"], 1008 - 100 + want["points_earned"], []))
    st, r, errs = compute(sari, {"shift": s_riu, "member": a, "points_redeemed": 300, "items": [{"item": KSA, "qty": 2}]})
    check("300 points (30.000) on 44.000 is over the 50% cap (22.000) -> points_cap", "points_cap" in errs and oracle.receipt(44000, t, points=300)["points_ok"] is False, (r.get("points_cap"), errs))
    st, r, errs = compute(sari, {"shift": s_riu, "member": a, "points_redeemed": 5000, "items": [{"item": KSA, "qty": 20}]})
    check("more points than the member has -> points_have", "points_have" in errs, errs)
    st, r, errs = compute(sari, {"shift": s_riu, "points_redeemed": 10, "items": [{"item": KSA, "qty": 1}]})
    check("points without a member -> points_need_member", "points_need_member" in errs, errs)
    st, s2 = sale(sari, s_riu, [{"item": KSA, "qty": 2}], member=a, points_redeemed=100)
    if not check("the redeem sale is paid -> 200", st == 200, (st, s2)):
        return
    eq("1.008 - 100 + 3 = 911 points; rows redeem -100 and earn +3", (member(api, a).get("points_balance"), sorted((x["kind"], x["points"]) for x in api.find("loyalty_entry", f"source == '{s2['name']}'"))),
       (911, [("earn", 3), ("redeem", -100)]))
    st, html, _ = api.print_html("pos_sale", s2["name"])
    text = page_text(html) if st == 200 else ""
    check("the receipt shows the redemption, the points earned and left", st == 200 and "Tukar 100 poin" in text and "Poin didapat" in text and "Sisa poin" in text and "911" in text, text[-500:])
    st, html, _ = api.print_html("pos_sale", s2["name"], "pos_receipt_whatsapp")
    check("the WhatsApp print: wa.me to the member's number with the points", st == 200 and "https://wa.me/6281200000401?text=" in html and "Sisa poin kamu 911" in page_text(html), (st, html[:400]))
    st, r = sari.verb("pos_sale", s2["name"], "cancel")
    if check("void of the redeem sale -> 200", st == 200, (st, r)):
        eq("the void gives 100 back and takes the 3 earned: 1.008", member(api, a).get("points_balance"), 1008)
        eq("one reverse row of +97", [(x["kind"], x["points"]) for x in api.find("loyalty_entry", f"source == '{s2['name']}' && kind == 'reverse'")], [("reverse", 97)])
    eq("ledger sum = balance", ledger_sum(api, "loyalty_entry", a, "points"), member(api, a).get("points_balance"))


def scenario_loyalty_deposit(api):
    """A cash top-up of 100.000 on the cashier's shift, a sale paid 30.000 from the deposit and the rest in cash, two
    sales racing for the last balance, a sale paid wholly from the deposit; the shift counts the top-up in the drawer
    and the journals put the deposit in and out of 2-1400."""
    m = people()
    if not m or not CTX.get("loy_a"):
        return
    sari, dika, hq = m
    a = CTX["loy_a"]
    t = tier(api, BASE)
    s_riu = shift(sari, SARI, RIU)
    before = hq.get("pos_shift", s_riu) or {}
    st, tp = sari.create("deposit_topup", {"account": a, "amount": 100000, "method": "cash", "shift": s_riu})
    st2, r = sari.verb("deposit_topup", rec(tp).get("name", "-"), "submit")
    if not check("the cashier takes a 100.000 cash top-up on its shift -> 201, 200", st == 201 and st2 == 200, (st, tp, st2, r)):
        return
    top = rec(r)
    eq("balance 100.000, one top_up row", (member(api, a).get("deposit_balance"), [(x["kind"], x["amount"]) for x in api.find("deposit_entry", f"source == '{top['name']}'")]), (100000, [("top_up", 100000)]))
    after = hq.get("pos_shift", s_riu) or {}
    eq("the shift: top-up cash +100.000, expected cash +100.000", (after.get("topup_cash_total", 0) - before.get("topup_cash_total", 0), after.get("expected_cash", 0) - before.get("expected_cash", 0)), (100000, 100000))
    gl = {x["account"]: x["amount"] for x in hq.find("gl_entry", f"voucher_type == 'deposit_topup' && voucher == '{top['name']}' && entry == 'post'")}
    eq("journal of the top-up: 1-1150 against 2-1400", gl, {"11150": 100000, "21400": -100000})
    st, resp = sari.create("deposit_topup", {"account": a, "amount": 50000, "method": "transfer"})
    check("a cashier cannot book a transfer top-up (the desk does) -> 422", st == 422 and ("cashier_on_shift" in rules(resp) or "transfer_not_on_shift" in rules(resp)), (st, resp))

    want = oracle.receipt(44000, t, deposit=30000)
    st, s = sale(sari, s_riu, [{"item": KSA, "qty": 2}], member=a, deposit_used=30000, payments=[{"method": "cash", "amount": 20000}])
    if not check("a sale of 44.000: 30.000 from the deposit, 20.000 cash handed over -> 200", st == 200, (st, s)):
        return
    eq("amount due 14.000, change 6.000, cash 14.000, PPN on the whole 44.000 (oracle)", (s.get("amount_due"), s.get("change_amount"), s.get("cash_amount"), s.get("ppn_in_sales"), s.get("method")),
       (want["amount_due"], 6000, 14000, want["ppn_in_sales"], "cash"))
    eq("deposit 70.000 left; one spend row", (member(api, a).get("deposit_balance"), [(x["kind"], x["amount"]) for x in api.find("deposit_entry", f"source == '{s['name']}'")]), (70000, [("spend", -30000)]))
    gl = {x["account"]: x["amount"] for x in hq.find("gl_entry", f"voucher_type == 'pos_sale' && voucher == '{s['name']}' && entry == 'post'")}
    exp = oracle.sale_journal(want, (hq.get("pos_sale", s["name"]) or {}).get("cogs_total") or 0)
    eq("the sale's journal: 14.000 into 1-1150, 30.000 out of 2-1400 (oracle)", gl, exp)
    st, html, _ = api.print_html("pos_sale", s["name"])
    text = page_text(html) if st == 200 else ""
    check("the receipt shows Deposit 30.000 and Sisa deposit 70.000", "Deposit" in text and "30.000" in text and "Sisa deposit" in text and "70.000" in text, text[-500:])
    st, r, errs = compute(sari, {"shift": s_riu, "member": a, "deposit_used": 80000, "items": [{"item": KSA, "qty": 5}]})
    check("more deposit than the balance -> deposit_have", "deposit_have" in errs, errs)
    st, r, errs = compute(sari, {"shift": s_riu, "member": a, "deposit_used": 50000, "items": [{"item": KSA, "qty": 2}]})
    check("more deposit than the total -> deposit_not_over", "deposit_not_over" in errs, errs)

    # two sales at once, 40.000 each, on a balance of 70.000: one passes, the other is refused under the lock
    drafts = []
    for _ in range(2):
        st, d = sale(sari, s_riu, [{"item": KSA, "qty": 2}], submit=False, member=a, deposit_used=40000)
        if st == 201:
            drafts.append(d["name"])
    if check("two drafts spending 40.000 each", len(drafts) == 2, drafts):
        res, gate = [None, None], threading.Barrier(2)

        def go(i):
            gate.wait()
            res[i] = sari.verb("pos_sale", drafts[i], "submit")

        ts = [threading.Thread(target=go, args=(i,)) for i in range(2)]
        for x in ts:
            x.start()
        for x in ts:
            x.join(60)
        sts = sorted(r[0] for r in res if r)
        check("exactly one is paid; the other -> 422 deposit_short", sts == [200, 422] and any("deposit_short" in rules(r[1]) for r in res if r and r[0] == 422),
              [(r[0], rules(r[1])) for r in res if r])
        eq("30.000 left", member(api, a).get("deposit_balance"), 30000)
    st, s3 = sale(sari, s_riu, [{"item": KHT, "qty": 2}], member=a, deposit_used=30000)
    eq("a 30.000 sale paid wholly from the deposit: nothing due, method Deposit", (st, s3.get("amount_due"), s3.get("method"), s3.get("change_amount")), (200, 0, "deposit", 0))
    eq("balance 0, ledger sum = balance", (member(api, a).get("deposit_balance"), ledger_sum(api, "deposit_entry", a, "amount")), (0, 0))
    if st == 200:
        st, r = sari.verb("pos_sale", s3["name"], "cancel")
        eq("its void gives the 30.000 back", (st, member(api, a).get("deposit_balance")), (200, 30000))
    sh = hq.get("pos_shift", s_riu) or {}
    check("the shift keeps the deposit sales apart (deposit_total)", (sh.get("deposit_total") or 0) >= 70000, sh.get("deposit_total"))
    CTX["loy_shift_riu"] = s_riu


def scenario_loyalty_promo_tier(api):
    """Silver after Rp 1.000.000 in the year: the member moves up at once (figures copied); its 5% member discount
    against a 10% bill promo (the promo applies) and a 2% one (the member discount applies), never both; the 1 January
    review keeps Silver on last year's spend and a year later goes back to the base tier."""
    m = people()
    if not m:
        return
    sari, dika, hq = m
    b = signup(dika, "cashier at Dago", "Bapak Uji Silver", "0812-0000-0402")
    if not b:
        return
    s_dgo = shift(dika, DIKA, DGO)
    st, s = sale(dika, s_dgo, [{"item": PULSA, "qty": 400}], member=b["name"])
    check("a 1.200.000 sale -> 200", st == 200, (st, s))
    b = member(api, b["name"])
    eq("moved up to Silver with its figures; points at the base rate on this sale (120)", (b.get("tier"), b.get("member_discount_pct"), b.get("earn_multiplier_pct"), b.get("points_balance")), (SILVER, 5, 125, 120))
    CTX["loy_b"] = b["name"]
    t = tier(api, SILVER)
    if "promotion" not in ((api.call("GET", "/api/v1/meta")[1] or {}).get("models") or {}):
        return
    st, p = hq.create("promotion", {"promo_name": "Uji nota 10 persen", "kind": "bill_pct", "pct": 10, "min_spend": 0, "code_mode": "none",
                                    "valid_from": today(), "valid_until": today(), "channel": "kasir", "active": True})
    if not check("a 10% bill promo -> 201", st == 201, (st, p)):
        return
    p = rec(p)
    want = oracle.receipt(44000, t, bill_promo_offer=4400)
    st, r, errs = compute(dika, {"shift": s_dgo, "member": b["name"], "promotion": p["name"], "items": [{"item": KSA, "qty": 2}]})
    eq("Silver 5% (2.200) vs promo 10% (4.400): the promo, no member discount (oracle)", (r.get("bill_promo"), r.get("member_discount"), r.get("grand_total")), (want["bill_promo"], want["member_discount"], want["grand_total"]))
    hq.patch("promotion", p["name"], {"pct": 2})
    want = oracle.receipt(44000, t, bill_promo_offer=880)
    st, r, errs = compute(dika, {"shift": s_dgo, "member": b["name"], "promotion": p["name"], "items": [{"item": KSA, "qty": 2}]})
    eq("vs promo 2% (880): the member discount 2.200, no promo (oracle)", (r.get("bill_promo"), r.get("member_discount"), r.get("grand_total"), r.get("manual_discount_total")), (0, want["member_discount"], want["grand_total"], 0))
    st, s = sale(dika, s_dgo, [{"item": KSA, "qty": 2}], member=b["name"], promotion=p["name"])
    if check("paid with the member discount -> 200", st == 200, (st, s)):
        eq("no promo use is written when the member discount applied", api.find("promotion_ledger", f"sale == '{s['name']}'"), [])
        st, html, _ = api.print_html("pos_sale", s["name"])
        check("the receipt names the member discount and the tier", "Diskon member Silver" in page_text(html), page_text(html)[-400:])
    hq.patch("promotion", p["name"], {"active": False})
    # 1 January review (event.as_of): last year's spend 1.200.000+ keeps Silver; the next year, nothing spent, base tier
    jan = f"{kernel_today().year + 1}-01-01"
    st, run = hq.call("POST", "/api/v1/flows/loyalty_yearly/run", {"as_of": jan})
    b = member(api, CTX["loy_b"])
    eq(f"review {jan}: Silver stays, the spend rolled to last year", (st, b.get("tier"), b.get("spend_this_year"), b.get("spend_last_year") >= 1000000), (200, SILVER, 0, True))
    st, run = hq.call("POST", "/api/v1/flows/loyalty_yearly/run", {"as_of": jan})
    eq("the same day again changes nothing", (member(api, CTX["loy_b"]).get("spend_last_year") >= 1000000), True)
    st, run = hq.call("POST", "/api/v1/flows/loyalty_yearly/run", {"as_of": "2026-07-01"})
    eq("on any other day the review does nothing", member(api, CTX["loy_b"]).get("tier"), SILVER)
    jan2 = f"{kernel_today().year + 2}-01-01"
    st, run = hq.call("POST", "/api/v1/flows/loyalty_yearly/run", {"as_of": jan2})
    b = member(api, CTX["loy_b"])
    eq(f"review {jan2}: nothing spent in the year, back to the base tier and its figures", (b.get("tier"), b.get("member_discount_pct")), (BASE, 0))
    st, r = hq.patch("member_tier", BASE, {"point_value": 150})
    eq("a tier edited: its members get the new figure", member(api, CTX["loy_b"]).get("point_value"), 150)
    hq.patch("member_tier", BASE, {"point_value": 100})
    st, resp = hq.create("member_tier", {"tier_name": "Dasar kedua", "tier_code": "DUA", "min_spend": 0})
    check("a second active base tier -> 422 base_tier", st == 422 and "base_tier" in rules(resp), (st, resp))


def scenario_loyalty_refund(api):
    """A desk top-up by transfer (no shift: Bank), a refund above Rp 1.000.000 waits for approval (the owner approves),
    one under it goes through, a refund over the balance is refused."""
    m = people()
    if not m or not CTX.get("loy_b"):
        return
    sari, dika, hq = m
    b = CTX["loy_b"]
    st, tp = hq.create("deposit_topup", {"account": b, "amount": 2000000, "method": "transfer", "ref": "TRF-UJI-1"})
    st2, r = hq.verb("deposit_topup", rec(tp).get("name", "-"), "submit")
    check("head office books a 2.000.000 transfer top-up -> 200", st == 201 and st2 == 200, (st, tp, st2, r))
    gl = {x["account"]: x["amount"] for x in hq.find("gl_entry", f"voucher_type == 'deposit_topup' && voucher == '{rec(r).get('name')}' && entry == 'post'")}
    eq("journal: Bank against 2-1400", gl, {"11200": 2000000, "21400": -2000000})
    st, rf = hq.create("deposit_refund", {"account": b, "amount": 1500000, "method": "transfer", "reason": "request"})
    st2, r = hq.verb("deposit_refund", rec(rf).get("name", "-"), "submit")
    check("a 1.500.000 refund by head office waits for approval -> 202", st == 201 and st2 == 202, (st, rf, st2, r))
    eq("the balance is untouched while it waits", member(api, b).get("deposit_balance"), 2000000)
    aid = ((r or {}).get("approval") or {}).get("id")
    eq("one pending request for finance or the owner", [(x.get("policy"), x.get("approvers"), x.get("state")) for x in approvals_of(api, "deposit_refund", rec(rf).get("name", "-"))],
       [("big_refund", ["finance_manager"], "pending")])
    if aid:
        st, r = decide(api, aid)
        check("the owner approves -> 200", st == 200, (st, r))
    eq("approved: 500.000 left, a refund row", (member(api, b).get("deposit_balance"), [(x["kind"], x["amount"]) for x in api.find("deposit_entry", f"account == '{b}' && kind == 'refund'")]), (500000, [("refund", -1500000)]))
    st, rf2 = hq.create("deposit_refund", {"account": b, "amount": 600000, "method": "cash", "reason": "request"})
    check("a refund over the balance -> 422 within_balance", st == 422 and "within_balance" in rules(rf2), (st, rf2))
    st, rf3 = hq.create("deposit_refund", {"account": b, "amount": 100000, "method": "cash", "reason": "closing"})
    st2, r = hq.verb("deposit_refund", rec(rf3).get("name", "-"), "submit")
    eq("100.000 in cash under the limit: no approval, 400.000 left", (st2, member(api, b).get("deposit_balance")), (200, 400000))


def scenario_loyalty_roles(api):
    """The cashier cannot correct points, refund a deposit, edit a ledger row or set up tiers; outlet staff cannot sign up."""
    m = people()
    if not m or not CTX.get("loy_a"):
        return
    sari, dika, hq = m
    st, resp = sari.create("points_adjustment", {"account": CTX["loy_a"], "points": 50, "reason": "x"})
    check("the cashier cannot adjust points -> 403", denied(st, resp), (st, resp))
    st, resp = sari.create("deposit_refund", {"account": CTX["loy_a"], "amount": 1000, "method": "cash", "reason": "request"})
    check("the cashier cannot refund a deposit -> 403", denied(st, resp), (st, resp))
    st, resp = sari.create("member_tier", {"tier_name": "Kasir", "tier_code": "KSR"})
    check("the cashier cannot set up a tier -> 403", denied(st, resp), (st, resp))
    rows = api.find("loyalty_entry", f"account == '{CTX['loy_a']}'")
    if rows:
        st, resp = hq.patch("loyalty_entry", rows[0]["name"], {"points": 1})
        check("nobody edits a points row (append_only) -> 4xx", st in (403, 405, 409), (st, resp))
    wati = CTX.get("wati")
    if wati:
        st, resp = wati.create("loyalty_account", {"member_name": "Staf", "phone": "081200000499"})
        check("outlet staff cannot sign members up -> 403", denied(st, resp), (st, resp))


def scenario_loyalty_shift_close(api):
    """The cashier closes the shift that took the top-up: the close is blind; head office sees the top-up in the expected
    cash and the journal settles 1-1150 for the receipts and the top-up together."""
    m = people()
    if not m or not CTX.get("loy_shift_riu"):
        return
    sari, dika, hq = m
    s = hq.get("pos_shift", CTX["loy_shift_riu"]) or {}
    if s.get("state") != "open":
        return
    st, c = sari.create("pos_shift_close", {"shift": s["name"], "counted_cash": s.get("expected_cash", 0)})
    st2, r = sari.verb("pos_shift_close", rec(c).get("name", "-"), "submit")
    if not check("the shift with a top-up closes -> 201, 200", st == 201 and st2 == 200, (st, c, st2, r)):
        return
    close = hq.get("pos_shift_close", rec(r).get("name")) or {}
    eq("the close carries the top-up and no difference", (close.get("topup_cash_total"), close.get("cash_difference")), (s.get("topup_cash_total"), 0))
    gl = {x["account"]: x["amount"] for x in hq.find("gl_entry", f"voucher_type == 'pos_shift_close' && voucher == '{close['name']}' && entry == 'post'")}
    eq("journal: 1-1150 out by the receipts' cash and QRIS plus the top-ups", gl.get("11150"), -(close.get("cash_total", 0) + close.get("qris_total", 0) + close.get("topup_cash_total", 0) + close.get("topup_qris_total", 0)))


def scenario_loyalty_expiry_check(api):
    """Points expire after the tier's months without a purchase (run with event.as_of), activity keeps them; the
    nightly check finds every balance equal to its ledger; the dashboard shows members, points and deposits."""
    m = people()
    if not m or not CTX.get("loy_a"):
        return
    sari, dika, hq = m
    a = member(api, CTX["loy_a"])
    pts = a.get("points_balance", 0)
    day = (datetime.date.fromisoformat(a["last_activity"]) + datetime.timedelta(days=330)).isoformat()
    st, run = hq.call("POST", "/api/v1/flows/points_expiry/run", {"as_of": day})
    eq("11 months without a purchase: the points stay", (st, member(api, CTX["loy_a"]).get("points_balance")), (200, pts))
    y, mo, d = (int(x) for x in a["last_activity"].split("-"))
    st, run = hq.call("POST", "/api/v1/flows/points_expiry/run", {"as_of": f"{y + 1}-{mo:02d}-{min(d, 28):02d}" if (mo, d) != (2, 29) else f"{y + 1}-03-01"})
    # a run by hand may not write ledger rows (contract 0026: only events, hooks, seed and the scheduler do), so the
    # 12-month run is refused exactly where it would write the expire row: the member was selected. The 02:00
    # scheduler run writes it (system actor).
    check("12 months: the run reaches the member's expire row (by hand: 409 append_only; the scheduler writes it)",
          st == 409 and ((run or {}).get("error") or {}).get("code") == "append_only" and member(api, CTX["loy_a"]).get("points_balance") == pts, (st, run))
    st, run = hq.call("POST", "/api/v1/flows/loyalty_check/run", {})
    eq("the nightly check: every balance equals its ledger (no mismatch)", (st, api.find("loyalty_mismatch", "resolved == false")), (200, []))
    ws = dashboard(hq, "kasir_ringkas", 30)
    total_dep = sum(x.get("deposit_balance", 0) for x in api.find("loyalty_account", "active"))
    eq("dashboard: deposit held = sum of the members' balances", (ws.get("deposit_liability") or {}).get("value"), total_dep)
    check("dashboard: members and points value computed", (ws.get("members") or {}).get("value", 0) >= 4 and "points_liability" in ws, {k: ws.get(k) for k in ("members", "points_liability")})


SCENARIOS = [scenario_loyalty_signup, scenario_loyalty_points, scenario_loyalty_deposit, scenario_loyalty_promo_tier, scenario_loyalty_refund,
             scenario_loyalty_roles, scenario_loyalty_shift_close, scenario_loyalty_expiry_check]


# ---------- demo-langganan: a prepaid deposit pays invoices ----------

def scenario_loyalty_invoice_deposit(api):
    """Finance signs a client up as a member (its own customer), books a 500.000 transfer top-up, pays an invoice from
    the deposit (journal 2-1400 against piutang), cancels the payment (the balance comes back), and a payment over the
    balance is refused."""
    fm = CTX.get("fm")
    if fm is None:
        fail("loyalty: finance member signed in", CTX.get("login_error"))
        return
    st, a = api.create("loyalty_account", {"member_name": "Ibu Bunga Contoh", "phone": "+620000000202", "customer": "CUST-0002"})
    if not check("the owner signs CUST-0002 up as a member (its own customer) -> 201", st == 201, (st, a)):
        return
    a = rec(a)
    eq("linked to CUST-0002, the base tier", (member(api, a["name"]).get("customer"), member(api, a["name"]).get("tier")), ("CUST-0002", BASE))
    st, resp = api.create("loyalty_account", {"member_name": "Dua kali", "phone": "081200000999", "customer": "CUST-0002"})
    check("a second member for the same customer -> 422 customer_member", st == 422 and "customer_member" in rules(resp), (st, resp))
    st, tp = fm.create("deposit_topup", {"account": a["name"], "amount": 500000, "method": "transfer", "ref": "TRF-UJI-2"})
    st2, r = fm.verb("deposit_topup", rec(tp).get("name", "-"), "submit")
    check("finance books a 500.000 transfer top-up -> 200", st == 201 and st2 == 200, (st, tp, st2, r))
    item = sorted([i for i in api.list("item") if i.get("kind") == "service"], key=lambda i: i["name"])[0]["name"]
    st, inv = fm.create("sales_invoice", {"customer": "CUST-0002", "items": [{"item": item, "qty": 1, "rate": 200000}]})
    st2, inv2 = fm.verb("sales_invoice", rec(inv).get("name", "-"), "submit", {"force": True})
    inv = rec(inv2)
    if not check("an invoice of 200.000 for CUST-0002 -> 200", st2 == 200 and inv.get("outstanding"), (st, st2, inv2)):
        return
    due = inv["outstanding"]
    st, p = fm.create("payment", {"direction": "receive", "customer": "CUST-0002", "method": "deposit", "amount": due,
                                  "allocations": [{"sales_invoice": inv["name"], "amount": due}]})
    st2, r = fm.verb("payment", rec(p).get("name", "-"), "submit", {"force": True})
    if not check("the invoice paid from the deposit -> 201, 200", st == 201 and st2 == 200, (st, p, st2, r)):
        return
    pay = rec(r)
    eq("the invoice is paid, the deposit is down by it", ((api.get("sales_invoice", inv["name"]) or {}).get("outstanding"), member(api, a["name"]).get("deposit_balance")), (0, 500000 - due))
    gl = {x["account"]: x["amount"] for x in api.find("gl_entry", f"voucher_type == 'payment' && voucher == '{pay['name']}' && entry == 'post'")}
    eq("journal: out of 2-1400 against piutang", gl, {"21400": due, "11300": -due})
    st, r = fm.verb("payment", pay["name"], "cancel")
    eq("the payment cancelled: the deposit is back, the invoice open", (st, member(api, a["name"]).get("deposit_balance"), (api.get("sales_invoice", inv["name"]) or {}).get("outstanding")), (200, 500000, due))
    st, p = fm.create("payment", {"direction": "receive", "customer": "CUST-0002", "method": "deposit", "amount": 600000})
    st2, r = fm.verb("payment", rec(p).get("name", "-"), "submit", {"force": True})
    check("a deposit payment over the balance -> 422 deposit_short", st2 == 422 and "deposit_short" in rules(r), (st, st2, r))
    st, p = fm.create("payment", {"direction": "receive", "customer": "CUST-0003", "method": "deposit", "amount": 1000})
    st2, r = fm.verb("payment", rec(p).get("name", "-"), "submit", {"force": True})
    check("a deposit payment from a customer that is no member -> 422 not_member", st2 == 422 and "not_member" in rules(r), (st, st2, r))


SCENARIOS_LANGGANAN = [scenario_loyalty_invoice_deposit]
