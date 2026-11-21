"""Day-one scenarios of module promotions (rangka-apps#103) on demo-kasir, and its invoice default on demo-jasa.

Loaded by tests/day_one.py after day_one_pos and day_one_kitchen (bind() hands over their helpers): `python3
tests/day_one.py --site demo-kasir` and `--site demo-jasa`. Every scenario makes its own promos as head office and
switches them off at the end, so the fixtures' sample promos (switched off) and the pos scenarios' prices are left as
they were. Covered: an item promo at one outlet only (and the cashier's manual discount on top), buy 2 get 1, two
promos on one item (priority, ties), a happy hour window (clock(), rangka-os contract 0069), a bill promo with a shared
voucher code and its use limit (a void gives a use back), single-use coupons made by a batch and two sales racing for
one coupon, a free gift line (stock out, cost booked), no approval for a promo but approval for a big manual discount,
the promo ledger against every receipt and its append-only rows, roles, the customer picked or signed up at the
counter, the receipt print and the dashboard; on demo-jasa the invoice promo as a new line's default discount.

Members: Sari (cashier, Kedai Riau), Dika and Bayu (cashiers, Toko Dago), Rina (head_office), Wati (outlet_staff,
Kedai Riau) come from day_one_pos and day_one_kitchen; demo-jasa uses the owner.
"""
import datetime
import threading
import zoneinfo

H = {}
DGO, RIU = "OUT-02", "OUT-03"
AIR, MI, GULA, ROTI, KSA, KHT = "BRG-0001", "BRG-0002", "BRG-0003", "BRG-0015", "BRG-0021", "BRG-0022"
KOPI_CAT = "KTG-001"

_R = {"read"}
MATRIX = {
    "promotion": {"head_office": {"read", "create", "write", "delete", "export", "print"}, "cashier": _R, "outlet_staff": _R},
    "promo_code_batch": {"head_office": {"read", "create", "write", "delete", "submit", "export", "print"}},
    "promo_code": {"head_office": {"read", "create", "export", "print"}, "cashier": _R},
    # append_only (contract 0026): /meta never offers create; the sale's flows still write rows as the cashier
    "promotion_ledger": {"head_office": {"read", "export"}, "cashier": _R},
    "promo_guard": {"head_office": {"create"}, "cashier": {"create"}},   # never stored (a flow's refusal)
}


def bind(m):
    for k in ("ok", "fail", "check", "eq", "CTX", "denied", "dashboard", "num", "kernel_today", "check_print", "invoice_body"):
        H[k] = getattr(m, k)
    globals().update(H)
    m.MATRIX.update(MATRIX)
    # the cashier picks a customer at the counter or signs one up (module pos 1.4.0)
    m.MATRIX["customer"] = dict(m.MATRIX["customer"], cashier={"read", "create"})


def rec(x):
    return (x or {}).get("record", x) or {}


def rules(resp):
    return [e.get("rule") for e in (resp or {}).get("errors", [])] if isinstance(resp, dict) else []


def today():
    return kernel_today().isoformat()


def later(days):
    return (kernel_today() + datetime.timedelta(days=days)).isoformat()


def promo(hq, label, **fields):
    """Head office sets up a live promo (from today for 30 days); flow promotion_apply_on_create puts it on the items."""
    body = dict({"valid_from": today(), "valid_until": later(30), "channel": "kasir", "active": True}, **fields)
    st, p = hq.create("promotion", body)
    check(f"{label}: head office sets up the promo -> 201", st == 201, (st, p))
    return rec(p) if st == 201 else {}


def stop(hq, p):
    """Switch a promo off (flow promotion_apply_on_update clears the items)."""
    if p.get("name"):
        hq.patch("promotion", p["name"], {"active": False})


def apply_now(api):
    st, resp = api.call("POST", "/api/v1/flows/promotion_apply/run", {})
    check("flow promotion_apply runs by hand -> 200", st == 200, (st, resp))


def compute(cl, body):
    """The counter's dry compute (rangka-os contract 0034): (status, record, errors, warnings)."""
    st, resp = cl.call("POST", "/api/v1/compute/pos_sale", body)
    resp = resp or {}
    return st, resp.get("record") or {}, [e.get("rule") for e in resp.get("errors") or []], [w.get("rule") for w in resp.get("warnings") or []]


def shift(cl, email, outlet):
    """The cashier's open shift at outlet, opened with the outlet's float when there is none."""
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


def ledger(api, sale_name):
    return api.find("promotion_ledger", f"sale == '{sale_name}'")


def members():
    sari, dika, hq = CTX.get("sari"), CTX.get("dika"), CTX.get("hq")
    if not (sari and dika and hq):
        fail("promotions: members signed in", CTX.get("login_error"))
        return None
    return sari, dika, hq


def scenario_promo_item_pct(api):
    """An item promo for one outlet: Kopi susu aren 20% at Kedai Riau only. The sale at Riau gets it per line (the
    struck price on the counter is the line's amount), Toko Dago does not; the cashier's manual discount comes on top
    and counts as a manual discount; the paid sale writes one ledger row."""
    m = members()
    if not m:
        return
    sari, dika, hq = m
    p = promo(hq, "item promo at Riau", promo_name="Uji kopi Riau 20 persen", kind="item_pct", scope="item", item=KSA, pct=20, only_outlet=RIU)
    if not p:
        return
    eq("live once saved", (hq.get("promotion", p["name"]) or {}).get("live"), True)
    it = api.get("item", KSA) or {}
    eq("the item carries it in its one-outlet slot (promotion_apply_on_create)", (it.get("promo_o"), it.get("promo_o_kind"), it.get("promo_o_pct"), it.get("promo_o_outlet")), (p["name"], "pct", 20, RIU))
    s_riu, s_dgo = shift(sari, "sari.riau@contohwarung.test", RIU), shift(dika, "dika.dago@contohwarung.test", DGO)
    st, r, errs, _ = compute(sari, {"shift": s_riu, "items": [{"item": KSA, "qty": 2}]})
    line = (r.get("items") or [{}])[0]
    eq("Riau: 2 x 22.000, promo 8.800 on the line, total 35.200", (st, line.get("promotion"), line.get("promo_discount"), line.get("amount"), r.get("promo_total"), r.get("grand_total"), r.get("manual_discount_total")),
       (200, p["name"], 8800, 35200, 8800, 35200, 0))
    st, r, errs, _ = compute(dika, {"shift": s_dgo, "items": [{"item": KSA, "qty": 2}]})
    line = (r.get("items") or [{}])[0]
    eq("Dago: the promo of Riau does not apply", (st, line.get("promotion") or "", line.get("promo_discount"), r.get("grand_total")), (200, "", 0, 44000))
    st, r, errs, _ = compute(sari, {"shift": s_riu, "items": [{"item": KSA, "qty": 2, "discount_pct": 10}]})
    eq("a manual 10% on the promo line comes after the promo and counts as manual", (r.get("items") or [{}])[0].get("amount"), 31680)
    eq("manual discount 3.520 next to the promo 8.800", (r.get("manual_discount_total"), r.get("promo_total"), r.get("discount_total")), (3520, 8800, 12320))
    st, s = sale(sari, s_riu, [{"item": KSA, "qty": 2}])
    if check("Riau: the promo sale is paid -> 200", st == 200, (st, s)):
        CTX["promo_sale_item"] = s["name"]
        rows = ledger(api, s["name"])
        eq("one ledger row: the promo, 8.800, post", [(x["promotion"], x["amount"], x["entry"]) for x in rows], [(p["name"], 8800, "post")])
        st, html, _ = api.print_html("pos_sale", s["name"])
        check("the receipt names the promo under its line and the saving", st == 200 and "Promo Uji kopi Riau 20 persen" in html and "Hemat dari promo" in html, (st, html[:300]))
    stop(hq, p)
    eq("switched off: the item no longer carries it", (api.get("item", KSA) or {}).get("promo_o") or "", "")


def scenario_promo_buy_get(api):
    """Beli 2 gratis 1 roti at every outlet: qty 2 pays 2, 3 pays 2, 5 pays 4, 6 pays 4."""
    m = members()
    if not m:
        return
    sari, dika, hq = m
    p = promo(hq, "buy 2 get 1", promo_name="Uji beli 2 gratis 1 roti", kind="buy_get", scope="item", item=ROTI, buy_qty=2, get_qty=1)
    if not p:
        return
    s_riu = shift(sari, "sari.riau@contohwarung.test", RIU)
    got = {}
    for q in (2, 3, 5, 6):
        st, r, _, _ = compute(sari, {"shift": s_riu, "items": [{"item": ROTI, "qty": q}]})
        got[q] = r.get("grand_total")
    eq("beli 2 gratis 1: what 2, 3, 5 and 6 rolls cost", got, {2: 30000, 3: 30000, 5: 60000, 6: 60000})
    stop(hq, p)


def scenario_promo_priority(api):
    """Two live promos on one item: the higher priority wins; on a tie the lower promo number; switching the winner off
    hands the item to the other one."""
    m = members()
    if not m:
        return
    sari, dika, hq = m
    cat = promo(hq, "category promo", promo_name="Uji kopi 10 persen", kind="item_pct", scope="category", category=KOPI_CAT, pct=10, priority=1)
    one = promo(hq, "item promo", promo_name="Uji kopi hitam 30 persen", kind="item_pct", scope="item", item=KHT, pct=30, priority=5)
    if not (cat and one):
        return
    eq("kopi hitam: the item promo (priority 5) wins", ((api.get("item", KHT) or {}).get("promo"), (api.get("item", KHT) or {}).get("promo_pct")), (one["name"], 30))
    eq("kopi susu aren: the category promo", (api.get("item", KSA) or {}).get("promo"), cat["name"])
    hq.patch("promotion", one["name"], {"priority": 1})
    eq("same priority: the lower promo number wins", (api.get("item", KHT) or {}).get("promo"), min(cat["name"], one["name"]))
    stop(hq, cat)
    eq("the category promo off: kopi hitam keeps its own", (api.get("item", KHT) or {}).get("promo"), one["name"])
    eq("and kopi susu aren has none", (api.get("item", KSA) or {}).get("promo") or "", "")
    stop(hq, one)
    eq("both off: kopi hitam has none", (api.get("item", KHT) or {}).get("promo") or "", "")


def scenario_promo_happy_hour(api):
    """A happy hour window: outside it the promo is not live and the item has none; inside it, it is. The window is the
    site's wall clock (Asia/Jakarta, clock() of rangka-os contract 0069)."""
    m = members()
    if not m:
        return
    sari, dika, hq = m
    now = datetime.datetime.now(zoneinfo.ZoneInfo("Asia/Jakarta")).strftime("%H:%M")
    if now < "00:02" or now >= "23:57":
        ok("happy hour: skipped at the edge of the day (" + now + ")")
        return
    out_from, out_until = ("23:58", "23:59") if now < "23:00" else ("00:00", "00:01")
    in_from, in_until = "00:00", "23:59"
    p = promo(hq, "happy hour outside now", promo_name="Uji happy hour", kind="item_pct", scope="item", item=KHT, pct=25, time_from=out_from, time_until=out_until)
    if not p:
        return
    eq(f"outside its hours ({out_from}-{out_until}, now {now}): not live, the item has none",
       ((hq.get("promotion", p["name"]) or {}).get("live"), (api.get("item", KHT) or {}).get("promo") or ""), (False, ""))
    hq.patch("promotion", p["name"], {"time_from": in_from, "time_until": in_until})
    eq(f"inside its hours ({in_from}-{in_until}): live, the item has it", ((hq.get("promotion", p["name"]) or {}).get("live"), (api.get("item", KHT) or {}).get("promo")), (True, p["name"]))
    hq.patch("promotion", p["name"], {"days": ",".join(str(d) for d in range(1, 8) if d != kernel_today().isoweekday())})
    eq("not on today's weekday: not live", (hq.get("promotion", p["name"]) or {}).get("live"), False)
    hq.patch("promotion", p["name"], {"days": ""})
    apply_now(api)
    eq("the scheduled run keeps it live", (hq.get("promotion", p["name"]) or {}).get("live"), True)
    stop(hq, p)
    eq("off: not live", (hq.get("promotion", p["name"]) or {}).get("live"), False)


def scenario_promo_bill_code(api):
    """A bill promo with a shared voucher code and two uses: 10% capped at 5.000 from 50.000. No code, a wrong code and
    too little spend are told at the counter; the third use is refused; a void gives one use back."""
    m = members()
    if not m:
        return
    sari, dika, hq = m
    p = promo(hq, "voucher", promo_name="Uji voucher hemat", kind="bill_pct", pct=10, max_discount=5000, min_spend=50000, code_mode="shared", code="UJIHEMAT", max_uses=2)
    if not p:
        return
    s_riu = shift(sari, "sari.riau@contohwarung.test", RIU)
    four = [{"item": KHT, "qty": 4}]
    st, r, errs, _ = compute(sari, {"shift": s_riu, "items": four, "promotion": p["name"]})
    check("no code -> promo_code_needed at the counter", "promo_code_needed" in errs, errs)
    st, r, errs, _ = compute(sari, {"shift": s_riu, "items": four, "promotion": p["name"], "promo_code": "SALAH"})
    eq("a wrong code -> promo_code_shared, and nothing off on the screen", ("promo_code_shared" in errs, r.get("bill_promo")), (True, 0))
    st, r, errs, warns = compute(sari, {"shift": s_riu, "items": [{"item": KHT, "qty": 1}], "promotion": p["name"], "promo_code": "ujihemat"})
    eq("15.000 is under the minimum: a warning and nothing off", ("promo_min_spend" in warns, r.get("bill_promo"), r.get("grand_total")), (True, 0, 15000))
    st, r, errs, _ = compute(sari, {"shift": s_riu, "items": four, "promotion": p["name"], "promo_code": " ujihemat "})
    eq("60.000: 10% is 6.000, capped at 5.000 (the code in any case)", (errs, r.get("bill_promo"), r.get("grand_total"), r.get("promo_total")), ([], 5000, 55000, 5000))
    st, r, errs, _ = compute(sari, {"shift": s_riu, "items": four, "promo_code": "UJIHEMAT"})
    check("a code without its promo -> promo_code_without_promo", "promo_code_without_promo" in errs, errs)
    paid = []
    for i in range(2):
        st, s = sale(sari, s_riu, four, promotion=p["name"], promo_code="UJIHEMAT")
        if check(f"voucher use {i + 1} of 2 -> 200", st == 200, (st, s)):
            paid.append(s["name"])
    st, s = sale(sari, s_riu, four, promotion=p["name"], promo_code="UJIHEMAT")
    check("the third use -> 422 code_limit (Kuota voucher ini sudah habis)", st == 422 and "code_limit" in rules(s), (st, s))
    if paid:
        st, r = hq.verb("pos_sale", paid[0], "cancel")
        if check("head office voids one voucher sale -> 200", st == 200, (st, r)):
            eq("its ledger row is reversed", sorted((x["entry"], x["amount"]) for x in ledger(api, paid[0])), [("post", 5000), ("reverse", -5000)])
            st, s = sale(sari, s_riu, four, promotion=p["name"], promo_code="UJIHEMAT")
            check("the use given back is taken again -> 200", st == 200, (st, s))
    st, html, _ = (api.print_html("pos_sale", paid[1]) if len(paid) > 1 else (0, "", ""))
    check("the receipt shows the bill promo", st == 200 and "Promo nota Uji voucher hemat" in html, (st, html[:300]))
    stop(hq, p)
    st, r, errs, _ = compute(sari, {"shift": s_riu, "items": four, "promotion": p["name"], "promo_code": "UJIHEMAT"})
    eq("a promo switched off -> promo_valid, and nothing off on the screen", ("promo_valid" in errs, r.get("bill_promo")), (True, 0))


def scenario_promo_coupon_race(api):
    """Single-use coupons: a batch of three random codes; two sales with one coupon at the same moment, exactly one
    passes; an unknown code is refused; a void frees the coupon."""
    m = members()
    if not m:
        return
    sari, dika, hq = m
    p = promo(hq, "coupons", promo_name="Uji kupon 10 ribu", kind="bill_off", amount_off=10000, code_mode="unique")
    if not p:
        return
    st, b = hq.create("promo_code_batch", {"promotion": p["name"], "count": 3, "prefix": "UJI"})
    if not check("a batch of 3 coupons -> 201", st == 201, (st, b)):
        return
    st, r = hq.verb("promo_code_batch", rec(b)["name"], "submit")
    check("submitting the batch makes the coupons -> 200", st == 200, (st, r))
    batch = rec(r).get("name") or ""   # numbered (KPB-) when submitted
    codes = sorted(c["code"] for c in api.find("promo_code", f"batch == '{batch}'"))
    check("three codes, UJI + 8 characters without 0, O, 1, I or L, all different",
          len(codes) == 3 and len(set(codes)) == 3 and all(c.startswith("UJI") and len(c) == 11 and not set(c[3:]) & set("0O1IL") for c in codes), codes)
    if len(codes) < 2:
        return
    st, resp = hq.create("promo_code_batch", {"promotion": "PRM-0003", "count": 3})
    check("a batch for a promo without single-use coupons -> 422 unique_promo", st == 422 and "unique_promo" in rules(resp), (st, resp))
    s_riu = shift(sari, "sari.riau@contohwarung.test", RIU)
    drafts = []
    for _ in range(2):
        st, d = sale(sari, s_riu, [{"item": KHT, "qty": 2}], submit=False, promotion=p["name"], promo_code=codes[0])
        if st == 201:
            drafts.append(d["name"])
    if not check("two drafts with the same coupon", len(drafts) == 2, drafts):
        return
    res, gate = [None, None], threading.Barrier(2)

    def go(i):
        gate.wait()
        res[i] = sari.verb("pos_sale", drafts[i], "submit")

    ts = [threading.Thread(target=go, args=(i,)) for i in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    sts = sorted(r[0] for r in res if r)
    check("exactly one of the two is paid; the other -> 422 code_used", sts == [200, 422] and any("code_used" in rules(r[1]) for r in res if r and r[0] == 422), [(r[0], rules(r[1])) for r in res if r])
    won = [rec(r[1])["name"] for r in res if r and r[0] == 200]
    st, s = sale(sari, s_riu, [{"item": KHT, "qty": 2}], promotion=p["name"], promo_code="UJIXXXXXXXX")
    check("an unknown code -> 422 code_unknown", st == 422 and "code_unknown" in rules(s), (st, s))
    st, s = sale(sari, s_riu, [{"item": KHT, "qty": 2}], promotion=p["name"], promo_code=codes[1].lower())
    eq("another coupon, typed in small letters: 30.000 - 10.000", (st, (s or {}).get("grand_total")), (200, 20000))
    if won:
        st, r = hq.verb("pos_sale", won[0], "cancel")
        if check("void of the coupon sale -> 200", st == 200, (st, r)):
            st, s = sale(sari, s_riu, [{"item": KHT, "qty": 2}], promotion=p["name"], promo_code=codes[0])
            check("the freed coupon is taken again -> 200", st == 200, (st, s))
    stop(hq, p)


def scenario_promo_gift(api):
    """A free gift from 50.000: the gift line is at 0, its stock leaves the outlet and its cost is booked; a gift without
    the promo, of another item, too many or under the minimum is refused."""
    m = members()
    if not m:
        return
    sari, dika, hq = m
    p = promo(hq, "free gift", promo_name="Uji gratis air", kind="free_gift", gift_item=AIR, gift_qty=1, min_spend=50000)
    if not p:
        return
    s_dgo = shift(dika, "dika.dago@contohwarung.test", DGO)
    buy = [{"item": GULA, "qty": 3}]
    gift = {"item": AIR, "qty": 1, "is_gift": True}
    st, r, errs, _ = compute(dika, {"shift": s_dgo, "items": buy + [gift], "promotion": p["name"]})
    lines = r.get("items") or [{}, {}]
    st, r0, errs0, _ = compute(dika, {"shift": s_dgo, "items": [{"item": GULA, "qty": 2}, gift], "promotion": p["name"]})
    eq("under the minimum: the shortfall is told (36.000 of 50.000) and the rule says what is missing", (r0.get("promo_shortfall"), "gift_min_spend" in errs0), (14000, True))
    eq("the gift line is free; the gift's price is in the promo, not in the total",
       (errs, lines[1].get("amount"), r.get("gift_value"), r.get("promo_total"), r.get("grand_total")), ([], 0, 4000, 4000, 54000))
    for label, body, rule in (("a gift without the promo", {"items": buy + [gift]}, "gift_needs_promo"),
                              ("a gift of another item", {"items": buy + [dict(gift, item=MI)], "promotion": p["name"]}, "gift_item"),
                              ("two gifts where one is given", {"items": buy + [dict(gift, qty=2)], "promotion": p["name"]}, "gift_qty"),
                              ("a gift under the minimum", {"items": [{"item": GULA, "qty": 2}, gift], "promotion": p["name"]}, "gift_min_spend")):
        st, r, errs, _ = compute(dika, dict(body, shift=s_dgo))
        check(f"{label} -> {rule}", rule in errs, errs)
    before = round((api.find("stock_balance", f"outlet == '{DGO}' && item == '{AIR}'") or [{"qty": 0}])[0]["qty"], 4)
    st, s = sale(dika, s_dgo, buy + [gift], promotion=p["name"])
    if check("the gift sale is paid -> 200", st == 200, (st, s)):
        after = round((api.find("stock_balance", f"outlet == '{DGO}' && item == '{AIR}'") or [{"qty": 0}])[0]["qty"], 4)
        eq("the gift's bottle left Toko Dago's stock", round(before - after, 4), 1)
        full = api.get("pos_sale", s["name"]) or {}
        eq("its cost is booked with the sale's (3 x 15.000 + 2.500)", full.get("cogs_total"), 47500)
        eq("one ledger row for the gift: its price", [(x["promotion"], x["amount"]) for x in ledger(api, s["name"])], [(p["name"], 4000)])
        st, html, _ = api.print_html("pos_sale", s["name"])
        check("the receipt prints the gift line as Gratis", st == 200 and "Gratis" in html, (st, html[:300]))
    stop(hq, p)


def scenario_promo_no_approval(api):
    """A 50% promo set up by the owner is paid without head office; a cashier's 25% manual discount still waits
    (approval big_discount, contract 0007)."""
    m = members()
    if not m:
        return
    sari, dika, hq = m
    p = promo(hq, "half price", promo_name="Uji setengah harga", kind="item_pct", scope="item", item=KHT, pct=50)
    if not p:
        return
    s_riu = shift(sari, "sari.riau@contohwarung.test", RIU)
    st, s = sale(sari, s_riu, [{"item": KHT, "qty": 2}])
    eq("50% off by the promo: paid at once", (st, (s or {}).get("grand_total")), (200, 15000))
    st, s = sale(sari, s_riu, [{"item": ROTI, "qty": 1, "discount_pct": 25}])
    check("a 25% manual discount waits for head office -> 202", st == 202, (st, s))
    stop(hq, p)


def scenario_promo_ledger(api):
    """Every paid receipt with a promo has ledger rows that add up to its promo_total; a voided one nets to 0. Nobody
    edits or makes up a row, the owner included (append_only); the dashboard shows the promo cost."""
    if not members():
        return
    sales = api.find("pos_sale", "docstatus != 0 && promo_total > 0")
    bad = []
    for s in sales:
        rows = ledger(api, s["name"])
        net = sum(x["amount"] for x in rows)
        want = s["promo_total"] if s["docstatus"] == 1 else 0
        if net != want:
            bad.append((s["name"], s["docstatus"], net, want))
    check(f"ledger rows equal promo_total on all {len(sales)} receipts with a promo (0 when voided)", sales and not bad, bad[:5])
    rows = api.find("promotion_ledger", "entry == 'post'")
    if rows:
        st, resp = api.patch("promotion_ledger", rows[0]["name"], {"amount": 1})
        check("the owner cannot edit a ledger row -> 409 append_only", st == 409 and ((resp or {}).get("error") or {}).get("code") == "append_only", (st, resp))
    st, resp = api.create("promotion_ledger", {"promotion": "PRM-0001", "amount": 1, "entry": "post", "entry_key": "x|y|post"})
    check("nobody makes up a ledger row -> 409 append_only", st == 409, (st, resp))
    hq = CTX.get("hq")
    ws = dashboard(hq, "kasir_ringkas", 30) if hq else {}
    total = sum(x["amount"] for x in api.find("promotion_ledger", f"date >= '{later(-29)}'"))
    eq("dashboard: promo cost of the period", num(ws.get("promo_cost") or {}), total)


def scenario_promo_roles(api):
    """Promos are head office's: the cashier cannot set one up or make coupons and does not see the ledger's money;
    outlet staff only read. The cashier signs a customer up at the counter and sells to them; the receipt names them."""
    m = members()
    if not m:
        return
    sari, dika, hq = m
    st, resp = sari.create("promotion", {"promo_name": "Kasir iseng", "kind": "item_pct", "scope": "item", "item": KHT, "pct": 90, "valid_from": today(), "valid_until": later(1)})
    check("the cashier cannot set up a promo -> 403", denied(st, resp), (st, resp))
    st, resp = sari.create("promo_code_batch", {"promotion": "PRM-0005", "count": 5})
    check("the cashier cannot make coupons -> 403", denied(st, resp), (st, resp))
    rows = sari.find("promotion_ledger", "entry == 'post'")
    check("the cashier reads ledger rows without their money", rows and all("amount" not in x for x in rows), rows[:1])
    wati = CTX.get("wati")
    if wati:
        check("outlet staff read promos", len(wati.find("promotion", "name != ''")) >= 5)
        st, resp = wati.patch("promotion", "PRM-0001", {"active": True})
        check("outlet staff cannot switch a promo on -> 403", denied(st, resp), (st, resp))
    st, c = sari.create("customer", {"customer_name": "Pelanggan Contoh Kasir", "phone": "081200000001", "credit_limit": 9_000_000})
    if check("the cashier signs a customer up -> 201", st == 201, (st, c)):
        c = rec(c)
        eq("credit and money stay hidden from the cashier", sorted(k for k in ("credit_limit", "outstanding") if k in c), [])
        check("a cashier cannot set a credit limit (read-only for it: the default stays)", (api.get("customer", c["name"]) or {}).get("credit_limit") != 9_000_000)
        s_riu = shift(sari, "sari.riau@contohwarung.test", RIU)
        st, s = sale(sari, s_riu, [{"item": KHT, "qty": 1}], customer=c["name"])
        if check("a sale to that customer -> 200", st == 200, (st, s)):
            st, html, _ = api.print_html("pos_sale", s["name"])
            check("the receipt names the customer", st == 200 and "Pelanggan Contoh Kasir" in html, (st, html[:300]))


def scenario_promo_counter_screen(api):
    """The cashier screen names the promo slots (rangka-os contract 0069): checkout customer, bill promo (live bill promos
    only) and code; the line's amount and the gift switch; the summary with the promo."""
    sari = CTX.get("sari")
    if not sari:
        return
    st, sc = sari.call("GET", "/api/v1/screens/kasir")
    doc = ((sc or {}).get("counter") or (sc or {}).get("screen", {}).get("counter") or {}).get("document") or {}
    # module loyalty (rangka-apps#102) adds the member, points and deposit to the same slots; the counter's total is
    # amount_due (the total less the deposit part)
    eq("checkout slot", doc.get("checkout"), ["order_type", "customer", {"field": "member", "filter": "active"}, {"field": "promotion", "filter": "is_bill && live"}, "promo_code", "points_redeemed", "deposit_used"])
    eq("line amount and flag", ((doc.get("line") or {}).get("amount"), (doc.get("line") or {}).get("flag")), ("amount", "is_gift"))
    eq("summary", doc.get("summary"), ["gross_total", "promo_total", "member_discount", "points_discount", "manual_discount_total", "promo_shortfall", "service_charge", "pbjt", "rounding", "deposit_used", "amount_due"])


def scenario_promo_audit_fixes(api):
    """Audit #103 F1-F7: a one-outlet promo does not hide the all-outlet one elsewhere; rupiah off and buy X get Y count
    stock units of a pack; a gift is never a pack; the cashier reads no voucher or coupon code; a deleted promo leaves
    the items at once; the sale's flow checks a voucher's code and counts its uses per promo; big_discount measures
    the manual discount against the price after promos."""
    m = members()
    if not m:
        return
    sari, dika, hq = m
    s_riu, s_dgo = shift(sari, "sari.riau@contohwarung.test", RIU), shift(dika, "dika.dago@contohwarung.test", DGO)
    # F2: Riau 20% (priority 10) next to all outlets 10% (priority 1)
    riu = promo(hq, "F2 Riau promo", promo_name="Uji F2 Riau", kind="item_pct", scope="item", item=KSA, pct=20, only_outlet=RIU, priority=10)
    alln = promo(hq, "F2 all-outlet promo", promo_name="Uji F2 semua", kind="item_pct", scope="item", item=KSA, pct=10, priority=1)
    if riu and alln:
        got = {}
        for cl, sh, o in ((sari, s_riu, RIU), (dika, s_dgo, DGO)):
            st, r, _, _ = compute(cl, {"shift": sh, "items": [{"item": KSA, "qty": 2}]})
            got[o] = ((r.get("items") or [{}])[0].get("promotion"), r.get("grand_total"))
        eq("Riau takes its 20%, Dago the all-outlet 10% (not nothing)", got, {RIU: (riu["name"], 35200), DGO: (alln["name"], 39600)})
        st, resp = hq.call("DELETE", f"/api/v1/r/promotion/{riu['name']}")
        check("F5: an unused promo can be deleted -> 2xx", st in (200, 204), (st, resp))
        it = api.get("item", KSA) or {}
        eq("F5: its outlet slot is cleared in the same delete; the all-outlet one stays", (it.get("promo_o") or "", it.get("promo")), ("", alln["name"]))
        st, s = sale(sari, s_riu, [{"item": KSA, "qty": 1}])
        eq("F5: a sale right after the delete is paid with the remaining promo", (st, (s or {}).get("grand_total")), (200, 19800))
        stop(hq, alln)
    # F4: rupiah off per bottle and buy 2 get 1 on packs (Toko Dago sells water by the dus of 24 and mi by the pak of 5)
    off = promo(hq, "F4 rupiah off per unit", promo_name="Uji F4 air", kind="item_off", scope="item", item=AIR, amount_off=500)
    if off:
        st, r, _, _ = compute(dika, {"shift": s_dgo, "items": [{"item": AIR, "qty": 1, "packing": "KONV-0001"}]})
        eq("Rp 500 off a bottle on a dus of 24 is 12.000 off (84.000 -> 72.000)", ((r.get("items") or [{}])[0].get("promo_discount"), r.get("grand_total")), (12000, 72000))
        stop(hq, off)
    bg = promo(hq, "F4 buy 2 get 1 per unit", promo_name="Uji F4 mi", kind="buy_get", scope="item", item=MI, buy_qty=2, get_qty=1)
    if bg:
        st, r, _, _ = compute(dika, {"shift": s_dgo, "items": [{"item": MI, "qty": 1, "packing": "KONV-0003"}]})
        eq("buy 2 get 1 on a pak of 5: 1 mi free at its own price (16.500 - 3.500)", ((r.get("items") or [{}])[0].get("promo_discount"), r.get("grand_total")), (3500, 13000))
        stop(hq, bg)
    # F1: a gift is never a pack
    gp = promo(hq, "F1 gift", promo_name="Uji F1 hadiah", kind="free_gift", gift_item=AIR, gift_qty=1, min_spend=10000)
    if gp:
        st, r, errs, _ = compute(dika, {"shift": s_dgo, "items": [{"item": GULA, "qty": 1}, {"item": AIR, "qty": 1, "packing": "KONV-0001", "is_gift": True}], "promotion": gp["name"]})
        check("a gift line of a dus of 24 -> gift_no_packing and gift_qty", {"gift_no_packing", "gift_qty"} <= set(errs), errs)
        st, s = sale(dika, s_dgo, [{"item": GULA, "qty": 1}, {"item": AIR, "qty": 1, "packing": "KONV-0001", "is_gift": True}], promotion=gp["name"])
        check("such a sale is refused -> 422", st == 422, (st, s))
        stop(hq, gp)
    # F3: codes are the buyer's, not the cashier's
    v = promo(hq, "F3 voucher", promo_name="Uji F3 voucher", kind="bill_off", amount_off=1000, code_mode="shared", code="UJIRAHASIA", max_uses=1)
    if v:
        mine = sari.get("promotion", v["name"]) or {}
        check("the cashier does not read the voucher code", "code" not in mine or not mine.get("code"), mine.get("code"))
        rows = sari.find("promo_code", "name != ''")
        check("the cashier lists no coupon code", all(not x.get("code") for x in rows), [x.get("code") for x in rows][:3])
        # F6: the flow checks the code and counts uses per promo, so a wrong code cannot dodge max_uses
        st, s = sale(sari, s_riu, [{"item": KHT, "qty": 1}], promotion=v["name"], promo_code="UJIRAHASIA")
        check("the voucher's one use -> 200", st == 200, (st, s))
        st, s = sale(sari, s_riu, [{"item": KHT, "qty": 1}], promotion=v["name"], promo_code="UJIRAHASIA")
        check("its second use -> 422 code_limit", st == 422 and "code_limit" in rules(s), (st, s))
        stop(hq, v)
    # F7: the manual discount is measured against the price after promos
    half = promo(hq, "F7 half price", promo_name="Uji F7", kind="item_pct", scope="item", item=ROTI, pct=50)
    if half:
        st, s = sale(sari, s_riu, [{"item": ROTI, "qty": 4, "discount_pct": 25}])
        check("25% manual on a 30.000 promo price waits for head office -> 202", st == 202, (st, s))
        stop(hq, half)


def scenario_promo_invoice_default(api):
    """demo-jasa: a percent promo of channel invoice becomes the default discount of a new invoice line; a draft made
    before keeps its lines; a counter-only promo never reaches invoices."""
    item = "BRG-0001"
    st, before = api.create("sales_invoice", invoice_body("CUST-0001", item))
    if not check("a draft invoice before the promo -> 201", st == 201, (st, before)):
        return
    p = promo(api, "invoice promo", promo_name="Uji diskon faktur", kind="item_pct", scope="item", item=item, pct=15, channel="invoice")
    k = promo(api, "counter-only promo", promo_name="Uji kasir saja", kind="item_pct", scope="item", item=item, pct=40, priority=9, channel="kasir")
    if not (p and k):
        return
    it = api.get("item", item) or {}
    eq("the item: invoice 15%, the counter promo stays the counter's", (it.get("promo_invoice_pct"), it.get("promo")), (15, k["name"]))
    body = invoice_body("CUST-0001", item)
    for l in body.get("items") or []:
        l.pop("discount", None)
    st, inv = api.create("sales_invoice", body)
    eq("a new line takes 15% as its discount", (st, [l.get("discount") for l in rec(inv).get("items") or []]), (201, [15] * len(body.get("items") or [])))
    old = rec(before)
    st, r = api.patch("sales_invoice", old["name"], {"items": old.get("items")})
    eq("the earlier draft keeps its lines' discount", [l.get("discount") for l in rec(r).get("items") or []], [l.get("discount") for l in old.get("items") or []])
    stop(api, p)
    stop(api, k)
    eq("both off: no invoice promo on the item", (api.get("item", item) or {}).get("promo_invoice_pct") or 0, 0)


SCENARIOS = [scenario_promo_item_pct, scenario_promo_buy_get, scenario_promo_priority, scenario_promo_happy_hour, scenario_promo_bill_code,
             scenario_promo_coupon_race, scenario_promo_gift, scenario_promo_no_approval, scenario_promo_ledger, scenario_promo_roles,
             scenario_promo_counter_screen, scenario_promo_audit_fixes]
SCENARIOS_JASA = [scenario_promo_invoice_default]
