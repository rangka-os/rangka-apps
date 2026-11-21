"""Day-one scenarios of module commission (rangka-apps#110) on demo-kasir, demo-grosir and demo-karyawan.

Loaded by tests/day_one.py after day_one_pos and day_one_loyalty (bind() hands over their helpers and members):
`python3 tests/day_one.py --site demo-kasir`. Fixtures are synthetic. Covered on the cashier: an item rule beating the
"all" rule, a percent of the net line after a line discount and before the PPN a PKP price holds, a fixed amount per
item, the sale's default staff and a line's own staff, a line a rule covers without any staff (status no_staff), a void
the same day (lines cancelled), a statement that locks its lines, a void after the lock (a negative correction line that
lands in the next statement), and a cashier reading only their own commission lines.
"""
H = {}
DGO = "OUT-02"
KSA, KHT, AIR = "BRG-0021", "BRG-0022", "BRG-0001"   # kopi susu aren 22.000, kopi hitam 15.000, bottled water 4.000
SARI, DIKA = "sari.riau@contohwarung.test", "dika.dago@contohwarung.test"
FAR = "2099-12-31"

_R = {"read"}
MATRIX = {
    "commission_rule": {"head_office": {"read", "create", "write", "delete", "export"}, "finance_manager": _R, "cashier": _R, "outlet_staff": _R,
                        "sales_manager": _R},
    "commission_line": {"head_office": {"read", "create", "write", "export"}, "finance_manager": {"read", "create", "write", "export"}, "cashier": {"read", "create", "write"},
                        "outlet_staff": {"read", "create", "write"}, "sales_manager": {"read", "create", "write"}},
    "commission_statement": {"head_office": {"read", "create", "write", "export", "print"},
                             "finance_manager": {"read", "create", "write", "export", "print"}, "cashier": _R,
                             "outlet_staff": _R, "sales_manager": _R},
}


def bind(m):
    for k in ("ok", "fail", "check", "eq", "CTX", "Api"):
        H[k] = getattr(m, k)
    globals().update(H)
    H["day_one"] = m
    m.MATRIX.update(MATRIX)
    # the counter's staff picker lists the staff (a link picker reads the user model)
    m.MATRIX["user"] = dict(m.MATRIX["user"], cashier={"read"})


def rec(x):
    return (x or {}).get("record", x) or {}


def rnd(x):
    import math
    return int(math.floor(x + 0.5))


def user_name(api, email):
    rows = api.find("user", f"email == '{email}'")
    return rows[0]["name"] if rows else ""


def lines_of(api, source):
    return sorted(api.find("commission_line", f"source_name == '{source}'"), key=lambda r: (r["item"], r["name"]))


def sale(cl, shift, items, **extra):
    st, s = cl.create("pos_sale", dict({"shift": shift, "items": items, "payments": [{"method": "cash", "amount": 1_000_000}]}, **extra))
    if st != 201:
        return st, s
    st, r = cl.verb("pos_sale", rec(s)["name"], "submit")
    return st, rec(r)


def void(cl, name):
    """A cashier's void waits for head office (approval void): ask, then let Rina decide."""
    st, r = cl.verb("pos_sale", name, "cancel")
    if st != 202:
        return st, r
    return H["day_one"].decide(CTX["hq"], r["approval"]["id"])


def scenario_commission_pos(api):
    dika = CTX.get("dika")
    if not dika:
        return
    # the earlier scenarios closed Dika's shift: open a new one (a float of 100.000) unless one is open
    open_shifts = [s for s in api.find("pos_shift", f"state == 'open' && outlet == '{DGO}'")]
    if open_shifts:
        shift = open_shifts[0]["name"]
    else:
        st, s = dika.create("pos_shift", {"outlet": DGO, "opening_cash": 100_000})
        if not check("Dika opens a shift for the commission scenario -> 201", st == 201, (st, s)):
            return
        shift = rec(s)["name"]
    today = H["day_one"].kernel_today().isoformat()
    sari_u, dika_u = user_name(api, SARI), user_name(api, DIKA)
    if not check("both cashiers are staff records", bool(sari_u and dika_u), (sari_u, dika_u)):
        return
    base = {"source": "pos", "valid_from": today, "valid_until": FAR, "active": True}
    rules = {}
    for key, body in (("all", {"rule_name": "Semua 5%", "applies_to": "all", "basis": "percent", "rate": 5}),
                      ("ksa", {"rule_name": "Kopi susu 10%", "applies_to": "item", "item": KSA, "basis": "percent", "rate": 10}),
                      ("kht", {"rule_name": "Kopi hitam Rp 5.000", "applies_to": "item", "item": KHT, "basis": "per_qty", "amount_per_qty": 5000})):
        st, r = api.create("commission_rule", dict(base, **body))
        if not check(f"owner makes the rule {body['rule_name']} -> 201", st == 201, (st, r)):
            return
        rules[key] = rec(r)["name"]
    eq("the item rule outranks the category and all rules", [rec(api.get("commission_rule", rules[k]))["rank"] for k in ("all", "ksa", "kht")], [1000, 3000, 3000])
    st, bad = api.create("commission_rule", dict(base, rule_name="Salah", applies_to="all", basis="percent", rate=150))
    check("a rate above 100 percent is refused", st in (400, 422), (st, bad))

    # a line discount, then the PPN a PKP price holds: 22.000 less 10% = 19.800, PPN inside 1.962, base 17.838, 10% = 1.784
    st, s1 = sale(dika, shift, [{"item": KSA, "qty": 1, "discount_pct": 10, "staff": sari_u}])
    if not check("Dika sells one discounted coffee with Sari as the stylist -> 200", st == 200 and s1.get("docstatus") == 1, (st, s1)):
        return
    ls = lines_of(api, s1["name"])
    eq("one line: Sari, base 17.838 (net of the discount and the PPN), rule 10 percent, commission 1.784",
       [(l["employee"] == sari_u, l["base_amount"], l["rule"], l["amount"], l["status"]) for l in ls], [(True, 17838, rules["ksa"], 1784, "ok")])

    # fixed per item, the all rule and the sale's default staff: 2 x Rp 5.000; 2 bottles 8.000 at 5 percent of the net before PPN
    st, s2 = sale(dika, shift, [{"item": KHT, "qty": 2}, {"item": AIR, "qty": 2}], staff=dika_u)
    if not check("Dika sells two black coffees and two bottles, himself as the default staff -> 200", st == 200, (st, s2)):
        return
    sub, grand, ppn = s2["subtotal"], s2["grand_total"], s2["ppn_in_sales"]
    want_air = rnd(rnd(8000 * (grand - ppn) / sub) * 5 / 100)
    got = {l["item"]: (l["employee"] == dika_u, l["amount"], l["rule"]) for l in lines_of(api, s2["name"])}
    eq("fixed Rp 5.000 per item and the all rule on the water, both to the default staff", got,
       {KHT: (True, 10000, rules["kht"]), AIR: (True, want_air, rules["all"])})

    # a rule covers the line but nobody is named: counted, never silently zero
    st, s3 = sale(dika, shift, [{"item": KSA, "qty": 1}])
    ls = lines_of(api, s3["name"]) if st == 200 else []
    eq("no staff on the line or the sale: a no_staff line of 0", [(l["status"], l["amount"], l["employee"]) for l in ls], [("no_staff", 0, "")])
    st, r = void(dika, s3["name"])
    check("the same-day void goes through -> 200", st == 200, (st, r))
    eq("the voided sale's commission lines are cancelled", [l["cancelled"] for l in lines_of(api, s3["name"])], [True])

    # a statement locks the lines; a void after the lock is a correction in the next period
    st, stm = api.create("commission_statement", {"employee": sari_u, "period_from": today, "period_to": today,
                                                   "tax_year": int(today[:4]), "tax_month": int(today[5:7])})
    if not check("owner drafts Sari's statement -> 201", st == 201, (st, stm)):
        return
    stm = rec(stm)["name"]
    st, r = api.patch("commission_statement", stm, {"locked": True})
    check("owner locks it -> 200", st == 200, (st, r))
    got = rec(api.get("commission_statement", stm))
    eq("the statement holds Sari's 1.784 in one line", (got["total"], got["line_count"]), (1784, 1))
    eq("the line is locked to the statement", [l["statement"] for l in lines_of(api, s1["name"])], [stm])
    st, r = void(dika, s1["name"])
    check("Dika's void of the locked sale goes through -> 200", st == 200, (st, r))
    ls = lines_of(api, s1["name"])
    eq("the locked line is marked reversed and a correction of -1.784 is made, dated today and not in the statement",
       sorted((l["correction"], l["reversed"], l["amount"], l["statement"] == stm) for l in ls),
       [(False, True, 1784, True), (True, False, -1784, False)])
    eq("the locked statement itself does not change", rec(api.get("commission_statement", stm))["total"], 1784)
    st, nxt = api.create("commission_statement", {"employee": sari_u, "period_from": today, "period_to": today,
                                                   "tax_year": int(today[:4]), "tax_month": int(today[5:7])})
    nxt = rec(nxt)["name"]
    api.patch("commission_statement", nxt, {"locked": True})
    eq("the next statement nets the correction", rec(api.get("commission_statement", nxt))["total"], -1784)

    # staff read only their own commission
    mine = dika.list("commission_line")
    check("a cashier reads the lines of their outlet (their flows need them) but not the statements of others",
          all(r["employee"] == dika_u for r in dika.list("commission_statement")), "")


def scenario_commission_payslip(api):
    """demo-karyawan: a locked statement of Rina (monthly, finance) of the previous month fills her slip's piece_income on the
    internal run's attendance fill; the slip's gross takes it, the BPJS wage does not (and so JKK, JKM and the employer
    BPJS Kesehatan do not move), An employee reads only their own commission."""
    import datetime
    import day_one_karyawan as K
    got = K.members("fm", "bagas")
    if not got:
        return
    fm, bagas = got
    g = K.gen()
    t = H["day_one"].kernel_today()
    pt = t.replace(day=1) - datetime.timedelta(days=1)    # last day of the previous month
    pf = pt.replace(day=1)
    rina, b_u, e_u = "USR-001", "USR-004", "USR-006"
    # the lines come from the invoice flow, run by hand with a synthetic issued invoice (this site has no invoicing): 5 percent
    st, rule = api.create("commission_rule", {"rule_name": "Semua faktur 5%", "source": "invoice", "applies_to": "all", "basis": "percent",
                                              "rate": 5, "valid_from": str(pf), "valid_until": "2099-12-31", "active": True})
    if not check("owner makes a 5 percent invoice rule -> 201", st == 201, (st, rule)):
        return
    for n, (emp, amount) in enumerate(((rina, 10_000_000), (rina, 5_000_000), (b_u, 1_400_000), (e_u, 1_800_000))):
        event = {"name": f"INV-CONTOH-{n}", "salesman": emp, "kind": "regular", "posting_date": str(pf), "subtotal": amount, "discount_amount": 0,
                 "items": [{"item": "", "qty": 1, "amount": amount}]}
        st, r = api.call("POST", "/api/v1/flows/commission_on_invoice_submitted/run", {"event": event})
        if not check(f"the invoice flow runs for {emp} {amount} -> 200", st == 200, (st, r)):
            return
    eq("the lines are 5 percent of the net: 500.000, 250.000, 70.000, 90.000",
       sorted((l["employee"], l["amount"]) for l in api.find("commission_line", "status == 'ok'")),
       sorted([(rina, 500_000), (rina, 250_000), (b_u, 70_000), (e_u, 90_000)]))
    st, stm = api.create("commission_statement", {"employee": rina, "period_from": str(pf), "period_to": str(pt),
                                                   "tax_year": pt.year, "tax_month": pt.month})
    stm = rec(stm).get("name", "")
    st, r = api.patch("commission_statement", stm, {"locked": True})
    got_s = rec(api.get("commission_statement", stm))
    eq("Rina's statement holds 500.000 + 250.000 in two lines", (st, got_s.get("total"), got_s.get("line_count")), (200, 750_000, 2))
    run = dict(g.RUN, period_from=str(pf), period_to=str(pt), tax_year=pt.year, tax_month=pt.month, pay_date=str(pt))
    st, r = fm.create("payroll_run", run)
    if not check("finance opens an internal run for the previous month -> 201", st == 201, (st, r)):
        return
    R = r["name"]
    fm.patch("payroll_run", R, {"fill_from_attendance": True})
    slips = fm.find("payslip", f"payroll_run == '{R}' && employee == '{rina}'")
    if not check("the fill drafts Rina's slip", len(slips) == 1, slips):
        return
    sl = rec(fm.get("payslip", slips[0]["name"]))
    eq("piece_income is the statement's 750.000", sl["piece_income"], 750_000)
    eq("cash_gross takes it: salary + fixed allowance + commission", sl["cash_gross"], sl["monthly_salary"] + sl["fixed_allowance"] + 750_000)
    eq("the BPJS wage is the contractual wage, not the commission", sl["bpjs_base"], max(g.UMK, sl["monthly_salary"] + sl["fixed_allowance"]))
    eq("the PPh 21 base is the gross plus the employer's JKK, JKM and health share (all on the BPJS wage)",
       (sl["gross_taxable"], sl["jkk"], sl["jkm"]),
       (sl["cash_gross"] + sl["jkk"] + sl["jkm"] + sl["kes_employer"], round(sl["bpjs_base"] * 0.24 / 100 + 1e-9), round(sl["bpjs_base"] * 0.3 / 100 + 1e-9)))
    # an employee reads only their own commission
    mine = bagas.list("commission_line")
    eq("Bagas (an employee) reads only his own commission line", sorted(r["employee"] for r in mine), [b_u])
    eq("and his own statements only", [r["employee"] for r in bagas.list("commission_statement")], [])
    # the issue's case on an invoice (before PPN by nature): 10 percent of a Rp 100.000 service after a Rp 10.000 discount = 9.000
    api.create("commission_rule", {"rule_name": "Jasa 10%", "source": "invoice", "applies_to": "all", "basis": "percent", "rate": 10, "priority": 5,
                                   "valid_from": str(pf), "valid_until": "2099-12-31", "active": True})
    event = {"name": "INV-CONTOH-ACC", "salesman": e_u, "kind": "regular", "posting_date": str(t), "subtotal": 100_000, "discount_amount": 10_000,
             "items": [{"item": "", "qty": 1, "amount": 100_000}]}
    st, r = api.call("POST", "/api/v1/flows/commission_on_invoice_submitted/run", {"event": event})
    eq("10 percent of Rp 100.000 after a Rp 10.000 discount = Rp 9.000 (the higher-priority rule wins)",
       [(l["base_amount"], l["amount"]) for l in api.find("commission_line", "source_name == 'INV-CONTOH-ACC'")], [(90_000, 9_000)])


SCENARIOS = [scenario_commission_pos]

SCENARIOS_KARYAWAN = [scenario_commission_payslip]
