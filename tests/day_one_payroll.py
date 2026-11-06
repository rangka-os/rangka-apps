"""Day-one scenarios of demo-manpower (app jasa_proyek + module payroll): one month of an outsourcing firm.

Loaded by tests/day_one.py (bind() hands over its helpers): `python3 tests/day_one.py --site demo-manpower`. Fixtures are
synthetic (examples/fixtures/demo-manpower/generate.py). Every expected amount comes from tests/payroll_oracle.py, which
recomputes PPh 21 TER, BPJS, Pasal 17 and the client invoice from the research, independently of the .rk files.
"""
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import payroll_oracle as oracle  # noqa: E402

H = {}
SLIP_FIELDS = ["base_pay", "overtime_pay", "allowance_pay", "thr_pay", "cash_gross", "bpjs_base", "jkk", "jkm", "jht_employer",
               "jht_employee", "jp_employer", "jp_employee", "kes_employer", "kes_employee", "gross_taxable", "pph21", "net_pay",
               "employer_cost"]
ORACLE_KEY = {"base_pay": "base", "overtime_pay": "overtime", "allowance_pay": "allowance", "thr_pay": "thr",
              "jht_employer": "jht_er", "jht_employee": "jht_ee", "jp_employer": "jp_er", "jp_employee": "jp_ee",
              "kes_employer": "kes_er", "kes_employee": "kes_ee"}


def bind(m):
    for k in ("ok", "fail", "gap", "check", "eq", "CTX", "Api", "denied", "secrets", "import_commit", "submit_all"):
        H[k] = getattr(m, k)
    globals().update(H)


def gen():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples", "fixtures", "demo-manpower", "generate.py")
    spec = importlib.util.spec_from_file_location("manpower_gen", path)
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    return g


def setup_demo_manpower(api, k):
    """Finance (payroll) and the project admin (recaps), signed in; a sales manager to prove pay stays hidden."""
    st, me = api.call("GET", "/auth/me")
    CTX.update(kernel=k, org=((me or {}).get("org") or {}).get("id") or "tenaga_contoh")
    pw = secrets.token_urlsafe(18)   # throwaway password of a throwaway database
    for key, email, role, rec, name in [("fm", "rina@contohtenaga.test", "finance_manager", "USR-001", "Rina Contoh"),
                                        ("pa", "asep@contohtenaga.test", "project_admin", "USR-002", "Asep Contoh"),
                                        ("sm", "dodi@contohtenaga.test", "sales_manager", "", "Dodi Contoh")]:
        code, out = k.user_add(email, role, CTX["org"], name, record=rec, password=pw)
        cl = Api.login(k.base, email, pw) if code == 0 else None
        if cl is None:
            CTX["login_error"] = f"{email}: " + (out if code else "login refused")
            fail("setup: add and sign in the members", CTX["login_error"])
            return
        CTX[key] = cl


def oracle_run(run):
    return {"umk": run["umk"], "jkk": run["jkk_rate"], "jp_cap": run["jp_wage_cap"], "kes_cap": run["kes_wage_cap"], "workdays": run["workdays_per_week"]}


def compare_slip(label, slip, want):
    bad = {f: (slip.get(f), want[ORACLE_KEY.get(f, f)]) for f in SLIP_FIELDS if slip.get(f) != want[ORACLE_KEY.get(f, f)]}
    return check(f"{label}: every amount matches the hand calculation", not bad, bad)


def new_run(fm, project, **over):
    g = gen()
    body = dict(g.RUN, project=project, **over)
    st, run = fm.create("payroll_run", body)
    check(f"finance opens payroll run {body['period_from']}..{body['period_to']} ({body['billing_mode']})", st == 201, (st, run))
    return run if st == 201 else None


def close_run(fm, run_name):
    """Tutup periode gaji: a payroll_close of the run, submitted. Returns (status, response, close name)."""
    st, c = fm.create("payroll_close", {"payroll_run": run_name})
    if st != 201:
        return st, c, ""
    st, resp = fm.verb("payroll_close", c["name"], "submit")
    return st, resp, ((resp or {}).get("record", resp) or {}).get("name", c["name"]) if st == 200 else c["name"]


def scenario_payroll_month(api):
    """Twenty workers, one month: recap import -> 20 draft slips -> edits -> submit -> run totals, BPJS and PPh 21 deposits,
    extra employer cost on the project margin, and the client invoice wages + BPJS + 10% fee with PPN and PPh 23."""
    fm, pa, sm = CTX.get("fm"), CTX.get("pa"), CTX.get("sm")
    if not (fm and pa and sm):
        fail("payroll month: members", CTX.get("login_error", "members missing"))
        return
    g = gen()
    st, prj = pa.create("project", {"project_name": "Alih daya operator Garmen", "customer": g.CLIENT, "state": "running", "billing_model": "time_material"})
    if not check("project admin opens the client contract (per day, no contract cap)", st == 201, (st, prj)):
        return
    p = prj["name"]
    CTX["payroll_project"] = p
    run = new_run(fm, p)
    if run is None:
        return
    R = run["name"]
    eq("the run takes the client from the project", run.get("customer"), g.CLIENT)
    st, _ = pa.create("payroll_run", dict(g.RUN, project=p))
    check("the project admin cannot open a payroll run (403)", denied(st, _), (st, _))
    # The attendance recap, as the fingerprint machine exports it, imported and submitted by the project admin.
    pv, c = import_commit(pa, "attendance_recap", "rekap-absensi-okt.csv", g.recap_csv(p).encode(), label="recap of 20 workers (21 Sep - 20 Oct)")
    if c is None:
        return
    eq("one draft timesheet per worker", len(c["records"]), 20)
    submit_all(pa, "timesheet", c["records"], "project admin submits the 20 recaps")
    slips = fm.find("payslip", f"payroll_run == '{R}'")
    eq("each submitted recap became one draft payslip of the run", (len(slips), sorted({s["docstatus"] for s in slips})), (20, [0]))
    workers = {w["name"]: w for w in fm.list("worker", limit=200)}
    by_no = {workers[s["worker"]]["employee_no"]: s for s in slips if s.get("worker") in workers}
    eq("the slips carry days and overtime from the recap (MP-002: 23 days, 6 h)",
       ((by_no.get("MP-002") or {}).get("days"), (by_no.get("MP-002") or {}).get("overtime_hours")), (g.MONTH["MP-002"][0], g.MONTH["MP-002"][1]))
    eq("the slips carry the recap's labour cost (MP-002: 23 x 235.000 + 6 x 34.000)", (by_no.get("MP-002") or {}).get("timesheet_cost"), 23 * 235_000 + 6 * 34_000)
    st, resp = pa.verb("payslip", (by_no.get("MP-002") or {}).get("name", "x"), "submit")
    check("the project admin cannot submit a payslip (403)", denied(st, resp), (st, resp))
    st, resp = sm.call("GET", f"/api/v1/r/payslip?limit=5")
    check("a sales manager cannot read payslips (403)", st == 403, (st, resp))
    for no, body in g.EDITS.items():
        st, resp = fm.patch("payslip", by_no[no]["name"], body)
        check(f"finance edits the draft slip of {no} ({', '.join(body)})", st == 200, (st, resp))
    bad = []
    for no, s in list(by_no.items()):
        st, resp = fm.verb("payslip", s["name"], "submit", {"force": True} if no == "MP-015" else None)
        if st != 200:
            bad.append((no, st, resp))
        else:
            by_no[no] = resp.get("record", resp)
    check("finance submits the 20 payslips (MP-015 confirms the under-UMK warning)", not bad, bad[:3])
    r = oracle_run(fm.get("payroll_run", R))
    want = {}
    for w in g.WORKERS:
        want[w[0]] = oracle.slip(g.profile_input(w), r)
        s = fm.get("payslip", by_no[w[0]]["name"])
        by_no[w[0]] = s if isinstance(s, dict) and "error" not in s else by_no[w[0]]
        compare_slip(f"slip {w[0]} ({w[8]}, {w[7]})", s, want[w[0]])
    s15, s16, s17, s20 = by_no["MP-015"], by_no["MP-016"], by_no["MP-017"], by_no["MP-020"]
    check("MP-015 is paid under the UMK: flagged, and its BPJS wage is raised to the UMK", s15.get("below_umk") is True and s15.get("bpjs_base") == g.UMK, s15)
    eq("MP-016 has no NIK/NPWP: PPh 21 is 120% of the TER amount", s16.get("pph21"), oracle.floor(oracle.Decimal(s16["gross_taxable"]) * oracle.ter_rate("k1", s16["gross_taxable"]) / 100 * oracle.Decimal("1.2")))
    eq("MP-017 overtime by PP 35/2021 (5 first hours x 1,5 + 7 x 2 of 6.000.000 / 173)", s17.get("overtime_pay"), oracle.rnd(oracle.Decimal(6_000_000) / 173 * (oracle.Decimal("7.5") + 14)))
    eq("MP-020 JP stops at the JP cap and BPJS Kesehatan at 12.000.000",
       (s20.get("jp_employer"), s20.get("jp_employee"), s20.get("kes_employer"), s20.get("kes_employee")), (221_726, 110_863, 480_000, 120_000))
    eq("MP-019 THR 7/12 of a month (8.500.000) in this month's gross", by_no["MP-019"].get("thr_pay"), oracle.rnd(oracle.Decimal(8_500_000) * 7 / 12))
    ws = list(want.values())
    tot = {k: sum(x[k] for x in ws) for k in ("cash_gross", "employer_bpjs", "pph21", "net_pay", "employer_cost", "bpjs_tk", "bpjs_kes")}
    run = fm.get("payroll_run", R)
    eq("run totals: 20 workers, wages, employer BPJS, PPh 21, net pay, employer cost",
       (run.get("headcount"), run.get("total_cash_gross"), run.get("total_employer_bpjs"), run.get("total_pph21"), run.get("total_net_pay"), run.get("total_employer_cost")),
       (20, tot["cash_gross"], tot["employer_bpjs"], tot["pph21"], tot["net_pay"], tot["employer_cost"]))
    eq("deposits of the month: BPJS Ketenagakerjaan, BPJS Kesehatan, PPh 21", (run.get("deposit_bpjs_tk"), run.get("deposit_bpjs_kes"), run.get("total_pph21")),
       (tot["bpjs_tk"], tot["bpjs_kes"], tot["pph21"]))
    print(f"payroll: gaji {tot['cash_gross']:,} bersih {tot['net_pay']:,} PPh21 {tot['pph21']:,} BPJS TK {tot['bpjs_tk']:,} Kes {tot['bpjs_kes']:,} biaya {tot['employer_cost']:,}")
    st, resp, C = close_run(fm, R)
    if not check("finance closes the payroll run (Tutup periode gaji submitted)", st == 200, (st, resp)):
        return
    check("the run is closed and the close froze its totals", fm.get("payroll_run", R).get("closed") is True and fm.get("payroll_close", C).get("total_pph21") == tot["pph21"], fm.get("payroll_close", C))
    inv = fm.find("sales_invoice", f"project == '{p}'")
    if not check("the run made one draft client invoice", len(inv) == 1 and inv[0]["docstatus"] == 0, inv):
        return
    want_inv = oracle.invoice(ws, {"fee_mode": "percent", "fee_percent": 10})
    i = fm.get("sales_invoice", inv[0]["name"])
    eq("invoice lines: wages (outside PPh 23), employer BPJS, 10% fee",
       [(x["item"], x["rate"], bool(x.get("pph23_exclude"))) for x in i["items"]],
       [(g.WAGES, tot["cash_gross"], True), (g.BPJS, tot["employer_bpjs"], False), (g.FEE, want_inv["fee"], False)])
    st, resp = fm.verb("sales_invoice", i["name"], "submit")
    check("finance submits the client invoice", st == 200, (st, resp))
    i = (resp or {}).get("record", resp) if st == 200 else i
    eq("invoice: subtotal, PPN 11/12 x 12% on the whole invoice, PPh 23 2% on BPJS + fee, net due",
       (i.get("dpp"), i.get("ppn"), i.get("pph23_dpp"), i.get("pph23_amount"), i.get("amount_due")),
       (want_inv["subtotal"], want_inv["ppn"], want_inv["pph23_base"], want_inv["pph23"], want_inv["amount_due"]))
    print(f"invoice: dpp {want_inv['subtotal']:,} ppn {want_inv['ppn']:,} pph23 {want_inv['pph23']:,} due {want_inv['amount_due']:,} fee {want_inv['fee']:,}")
    rows = fm.find("project_ledger", f"project == '{p}'")
    margin = sum(x["amount"] for x in rows)
    eq("project margin = invoice DPP - full employer cost (recap rows + payroll row)", margin, want_inv["subtotal"] - tot["employer_cost"])
    eq("one payroll ledger row with the cost the recaps did not book", [x["amount"] for x in rows if x["kind"] == "payroll"],
       [-(tot["employer_cost"] - sum(s["timesheet_cost"] for s in by_no.values()))])
    st, resp = fm.verb("payslip", s15["name"], "cancel")
    check("a slip of a closed run cannot be cancelled", st >= 400, (st, resp))
    st, resp = fm.verb("payroll_close", C, "cancel")
    check("the close cannot be cancelled while its client invoice is submitted", st >= 400, (st, resp))
    st, resp = fm.verb("sales_invoice", i["name"], "cancel")
    st2, resp2 = fm.verb("payroll_close", C, "cancel")
    check("after the invoice is cancelled the close cancels too and the run is open again", st == 200 and st2 == 200 and fm.get("payroll_run", R).get("closed") is False, (st, resp, st2, resp2))
    rows = fm.find("project_ledger", f"project == '{p}' && kind == 'payroll'")
    eq("the payroll row is reversed on the run's date", sorted(x["amount"] for x in rows), sorted([-(tot["employer_cost"] - sum(s["timesheet_cost"] for s in by_no.values())), tot["employer_cost"] - sum(s["timesheet_cost"] for s in by_no.values())]))


def scenario_payroll_rules(api):
    """Refusals: a second slip for a worker in a run, a slip into a submitted run, an empty run, a run without items."""
    fm = CTX.get("fm")
    p = CTX.get("payroll_project")
    if not (fm and p):
        fail("payroll rules: setup", "no project")
        return
    run = new_run(fm, p, period_from="2026-10-21", period_to="2026-11-20", tax_month=11, wage_item="", bpjs_item="", fee_item="")
    if run is None:
        return
    st, resp, _ = close_run(fm, run["name"])
    check("an empty run cannot be closed", st == 422 and "slip" in str(resp).lower(), (st, resp))
    st, s1 = fm.create("payslip", {"payroll_run": run["name"], "profile": "PGJ-0018"})
    check("finance makes a slip by hand for a monthly supervisor", st == 201 and s1.get("base_pay") == 7_500_000, (st, s1))
    st, s2 = fm.create("payslip", {"payroll_run": run["name"], "profile": "PGJ-0018"})
    check("a second slip for the same worker in the run is refused", st >= 400, (st, s2))
    st, resp = fm.verb("payslip", s1.get("name", "x"), "submit")
    check("the hand-made slip submits", st == 200, (st, resp))
    st, resp, _ = close_run(fm, run["name"])
    check("a run billing wages + fee needs its three invoice items", st == 422, (st, resp))
    st, resp = fm.patch("payroll_run", run["name"], {"jkk_rate": 2.5})
    check("JKK above 1,74% is refused", st == 422, (st, resp))


def scenario_payroll_fee_only(api):
    """Fee only, flat per worker: the invoice has one fee line, all of it in the PPh 23 base."""
    fm = CTX.get("fm")
    p = CTX.get("payroll_project")
    if not (fm and p):
        fail("payroll fee only: setup", "no project")
        return
    run = new_run(fm, p, period_from="2026-11-21", period_to="2026-12-20", tax_month=12, billing_mode="fee_only", fee_mode="flat", fee_per_worker=150_000)
    if run is None:
        return
    names = []
    for prof in ("PGJ-0018", "PGJ-0019"):
        st, s = fm.create("payslip", {"payroll_run": run["name"], "profile": prof})
        if check(f"slip {prof}", st == 201, (st, s)):
            names.append(s["name"])
    submit_all(fm, "payslip", names, "fee-only slips")
    st, resp, C = close_run(fm, run["name"])
    if not check("fee-only run closes", st == 200, (st, resp)):
        return
    inv = [x for x in fm.find("sales_invoice", f"project == '{p}' && docstatus == 0")]
    inv = [fm.get("sales_invoice", x["name"]) for x in inv]
    inv = [x for x in inv if f"({C})" in (x.get("notes") or "")]
    want = oracle.invoice([{"cash_gross": 0, "employer_bpjs": 0}] * 2, {"billing_mode": "fee_only", "fee_mode": "flat", "fee_per_worker": 150_000})
    check("fee only: one line of 2 x 150.000, PPN on it, PPh 23 on all of it",
          len(inv) == 1 and [(x["item"], x["rate"]) for x in inv[0]["items"]] == [("BRG-0003", 300_000)]
          and (inv[0].get("ppn"), inv[0].get("pph23_amount")) == (want["ppn"], want["pph23"]), inv)


def scenario_payroll_december(api):
    """A full tax year of one monthly worker (K/1, 9.000.000 + 1.000.000, a 2.000.000 bonus in November): January to
    November withhold TER, December recomputes the year with Pasal 17 and withholds the rest."""
    fm, pa = CTX.get("fm"), CTX.get("pa")
    if not (fm and pa):
        fail("payroll december: members", "missing")
        return
    g = gen()
    st, w = fm.create("worker", {"worker_name": "Yuni Contoh", "employee_no": "MP-901", "cost_day_rate": 400_000, "active": True})
    st2, prof = fm.create("payroll_profile", {"worker": (w or {}).get("name", ""), "ptkp_status": "k1", "pay_basis": "monthly", "monthly_salary": 9_000_000,
                                              "fixed_allowance": 1_000_000, "nik": "9999000000009901"})
    if not check("a monthly worker with a payroll profile (K/1, 9.000.000 + 1.000.000)", st == 201 and st2 == 201, (st, w, st2, prof)):
        return
    st, prj = pa.create("project", {"project_name": "Alih daya admin gudang", "customer": g.CLIENT, "state": "running", "billing_model": "time_material"})
    if not check("second contract", st == 201, (st, prj)):
        return
    p, ytd = prj["name"], {"gross": 0, "pph21": 0, "pension": 0, "months": 0}
    base = {"pay_basis": "monthly", "monthly_salary": 9_000_000, "fixed_allowance": 1_000_000, "ptkp": "k1", "days": 22}
    bad = []
    for m in range(1, 13):
        f = f"{2025 if m == 1 else 2026}-{12 if m == 1 else m - 1:02d}-21"
        t = f"2026-{m:02d}-20"
        extra = {"other_income": 2_000_000} if m == 11 else {}
        st, run = fm.create("payroll_run", dict(g.RUN, project=p, period_from=f, period_to=t, tax_month=m, billing_mode="none"))
        if st != 201:
            bad.append((m, "run", st, run))
            break
        st, s = fm.create("payslip", dict({"payroll_run": run["name"], "profile": prof["name"], "days": 22}, **extra))
        if st != 201:
            bad.append((m, "slip", st, s))
            break
        st, resp = fm.verb("payslip", s["name"], "submit")
        s = (resp or {}).get("record", resp) if st == 200 else s
        want = oracle.slip(dict(base, last_month=(m == 12), **extra), oracle_run(run), ytd)
        if (s.get("ytd_gross"), s.get("ytd_pph21"), s.get("ytd_months")) != (ytd["gross"], ytd["pph21"], ytd["months"]):
            bad.append((m, "ytd", (s.get("ytd_gross"), s.get("ytd_pph21"), s.get("ytd_months")), ytd))
        if m in (1, 11, 12):
            compare_slip(f"month {m} ({'Pasal 17 year' if m == 12 else 'TER'})", s, want)
        elif s.get("pph21") != want["pph21"]:
            bad.append((m, "pph21", s.get("pph21"), want["pph21"]))
        ytd = {"gross": ytd["gross"] + want["gross_taxable"], "pph21": ytd["pph21"] + want["pph21"], "pension": ytd["pension"] + want["pension"], "months": ytd["months"] + 1}
        st, resp, _ = close_run(fm, run["name"])
        if st != 200:
            bad.append((m, "run close", st, resp))
            break
    check("twelve months: year to date read from the earlier slips, TER January to November", not bad, bad[:3])
    eq("December withholds the year's Pasal 17 tax (5% of PKP 55.208.000 = 2.760.400) minus January to November (1.947.120)",
       ytd["pph21"], 2_760_400)
    print(f"december: year gross {ytd['gross']:,} pph21 year {ytd['pph21']:,}")


SCENARIOS = [scenario_payroll_month, scenario_payroll_rules, scenario_payroll_fee_only, scenario_payroll_december]
