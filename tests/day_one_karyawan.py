"""Day-one scenarios of demo-karyawan (app karyawan_gaji with absen_karyawan, rangka-apps#101 slice b): a bakery paying
its own staff from their phone attendance.

Loaded by tests/day_one.py (bind() hands over its helpers and registers the site): `python3 tests/day_one.py --site
demo-karyawan`. Fixtures are synthetic (examples/fixtures/demo-karyawan/generate.py). Dates follow today (Asia/Jakarta):
staff clock a full day yesterday, head office submits the attendance period of yesterday, finance pays the month of
yesterday in an internal payroll run. Every expected amount comes from tests/payroll_oracle.py.

Members: Rina (finance_manager, does not clock in), Dodi (head_office, kitchen), Ayu (cashier, shop), Bagas and Eko
(employee, kitchen bakers paid per day), Citra (outlet_supervisor, shop). Fajar (outlet_staff, shop) never signs in.
"""
import csv
import datetime
import importlib.util
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import payroll_oracle as oracle  # noqa: E402

H = {}
SITE = "demo-karyawan"
KITCHEN, SHOP = "OUT-01", "OUT-02"
POINT = {KITCHEN: (-7.7829, 110.3671), SHOP: (-7.7600, 110.3800)}   # fixtures demo-karyawan
# key, email, role, outlets, record, name
MEMBERS = [("fm", "rina@contohroti.test", "finance_manager", KITCHEN, "USR-001", "Rina Contoh"),
           ("hq", "dodi@contohroti.test", "head_office", KITCHEN, "USR-002", "Dodi Contoh"),
           ("ayu", "ayu@contohroti.test", "cashier", SHOP, "USR-003", "Ayu Contoh"),
           ("bagas", "bagas@contohroti.test", "employee", KITCHEN, "USR-004", "Bagas Contoh"),
           ("citra", "citra@contohroti.test", "outlet_supervisor", SHOP, "USR-005", "Citra Contoh"),
           ("eko", "eko@contohroti.test", "employee", KITCHEN, "USR-006", "Eko Contoh")]
TRANSFER_COLUMNS = "bank_account,account_holder,bank_name,net_pay,transfer_note"


def bind(m):
    for k in ("ok", "fail", "check", "eq", "CTX", "Api", "denied", "secrets", "messages", "dashboard", "num", "decide", "kernel_today",
              "check_print"):
        H[k] = getattr(m, k)
    globals().update(H)
    m.SITE_ORGS[SITE] = [("roti_contoh", SITE)]
    m.SITE_MEMBERS[SITE] = [("fm", "finance_manager"), ("hq", "head_office"), ("ayu", "cashier")]
    m.SITE_SETUP[SITE] = setup
    # a cashier clocks in like outlet staff (docs/permissions.md: one column for cashier, outlet_staff and employee)
    for model in ("staff_attendance", "staff_attendance_guard", "attendance_period", "attendance_summary"):
        row = m.MATRIX.get(model)
        if row and "outlet_staff" in row and "cashier" not in row:
            row["cashier"] = set(row["outlet_staff"])


def gen():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples", "fixtures", SITE, "generate.py")
    spec = importlib.util.spec_from_file_location("karyawan_gen", path)
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    return g


def rec(resp):
    return ((resp or {}).get("record") or resp or {}) if isinstance(resp, dict) else {}


def setup(api, k):
    st, me = api.call("GET", "/auth/me")
    org = ((me or {}).get("org") or {}).get("id") or "roti_contoh"
    CTX.update(kernel=k, org=org)
    pw = secrets.token_urlsafe(18)   # throwaway password of a throwaway database
    for key, email, role, outlets, record, name in MEMBERS:
        code, out = k.user_add(email, role, org, name, outlets=outlets, record=record, password=pw)
        cl = Api.login(k.base, email, pw) if code == 0 else None
        if cl is None:
            CTX["login_error"] = f"{email}: " + (out if code else "login refused")
            fail("setup: add and sign in the members", CTX["login_error"])
            return
        CTX[key] = cl


def members(*keys):
    got = [CTX.get(k) for k in keys]
    if not all(got):
        fail("demo-karyawan: members signed in", CTX.get("login_error") or [k for k in keys if not CTX.get(k)])
        return None
    return got


def pos(outlet, north_m=0.0, acc=10):
    lat, lng = POINT[outlet]
    return {"lat": round(lat + north_m / 110574.0, 7), "lng": round(lng, 7), "accuracy_m": acc,
            "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}


def photo(cl, name):
    jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\x00" * 64 + b"\xff\xd9"
    st, resp = cl.upload(f"/api/v1/r/staff_attendance/{Api.q(name)}/attachments", {}, "selfie.jpg", jpeg)
    return (resp or {}).get("id", "") if st == 201 else ""


def full_day(cl, day, outlet, clock_in, clock_out, **extra):
    """Clock in and out at the outlet with selfies, then submit. Returns (status of submit, response, log name)."""
    body = dict({"outlet": outlet, "work_date": str(day), "in_pos": pos(outlet, 30, 12)}, **extra)
    st, lg = cl.create("staff_attendance", body)
    if st != 201:
        return st, lg, ""
    n = lg["name"]
    cl.patch("staff_attendance", n, {"in_photo": photo(cl, n), "clock_in": f"{day}T{clock_in}:00+07:00"})
    st, resp = cl.patch("staff_attendance", n, {"clock_out": f"{day}T{clock_out}:00+07:00", "out_pos": pos(outlet, 15, 9), "out_photo": photo(cl, n)})
    if st != 200:
        return st, resp, n
    st, resp = cl.verb("staff_attendance", n, "submit")
    return st, resp, rec(resp).get("name", n) if st == 200 else n


def oracle_run():
    g = gen()
    return {"umk": g.UMK, "jkk": g.RUN["jkk_rate"], "jp_cap": g.RUN["jp_wage_cap"], "kes_cap": g.RUN["kes_wage_cap"], "workdays": g.RUN["workdays_per_week"]}


def profile_input(g, user, slip):
    """The oracle's input for one fixture employee and the attendance the slip carries."""
    s = next(x for x in g.STAFF if x[0] == user)
    return {"pay_basis": s[7], "monthly_salary": s[8], "fixed_allowance": s[9], "day_rate": s[10], "daily_allowance": s[11],
            "overtime_rate": s[12], "employment": s[13], "ptkp": s[14], "jp": s[7] == "monthly",
            "days": slip.get("days", 0), "overtime_hours": slip.get("overtime_hours", 0), "overtime_days": slip.get("overtime_days", 0),
            "late_min": slip.get("late_min_total", 0)}


SLIP_FIELDS = ["base_pay", "overtime_pay", "allowance_pay", "cash_gross", "bpjs_base", "jkk", "jkm", "jht_employer", "jht_employee",
               "jp_employer", "jp_employee", "kes_employer", "kes_employee", "gross_taxable", "pph21", "net_pay", "employer_cost"]
ORACLE_KEY = {"base_pay": "base", "overtime_pay": "overtime", "allowance_pay": "allowance", "jht_employer": "jht_er", "jht_employee": "jht_ee",
              "jp_employer": "jp_er", "jp_employee": "jp_ee", "kes_employer": "kes_er", "kes_employee": "kes_ee"}


def scenario_karyawan_month(api):
    """One month of a bakery's own staff. Staff clock a day yesterday (Bagas late with 2 approved overtime hours); the
    morning flow waits while the attendance period is a draft; head office submits it; finance ticks "Isi slip dari absen"
    on the internal run: a slip per employee with a summary plus Rina (monthly, exempt), days, lateness and overtime from
    the summaries, the tick cleared and the note written; ticking again and the morning flow add nothing. Every amount
    equals the oracle; the slips submit (Fajar without an account warns), the run closes, and the transfer list holds
    every slip with an account and keeps the leading zeros."""
    got = members("fm", "hq", "ayu", "bagas", "citra", "eko")
    if not got:
        return
    fm, hq, ayu, bagas, citra, eko = got
    g = gen()
    t = kernel_today()
    y = t - datetime.timedelta(days=1)
    # staff clock yesterday: Ayu at the shop, Citra at the shop, Bagas 06:12 at the kitchen with 2 overtime hours, Eko on time
    st, resp, ayu_log = full_day(ayu, y, SHOP, "08:00", "17:00")
    check("Ayu (cashier) clocks a full day at the shop: submitted", st == 200 and rec(resp).get("docstatus") == 1, (st, resp))
    st, resp, _ = full_day(citra, y, SHOP, "07:55", "17:05")
    check("Citra (shop supervisor) clocks a full day: submitted", st == 200, (st, resp))
    st, resp, _ = full_day(eko, y, KITCHEN, "05:58", "14:00")
    check("Eko (baker, daily) clocks a full day at the kitchen: submitted", st == 200, (st, resp))
    st, resp, bagas_log = full_day(bagas, y, KITCHEN, "06:12", "16:00", overtime_hours=2, reason="Pesanan roti ulang tahun")
    pend = [a for a in (resp or {}).get("approvals", []) if a.get("state") == "pending"] if isinstance(resp, dict) else []
    if check("Bagas clocks in 06:12 with 2 overtime hours: waits for approval (202)", st == 202 and pend, (st, resp)):
        st, resp = decide(hq, pend[0]["id"])
        check("head office approves Bagas's overtime: the log is submitted", st == 200 and rec(resp).get("docstatus") == 1, (st, resp))
    logs = {x["employee"]: x for x in hq.find("staff_attendance", f"docstatus == 1 && work_date == '{y}'")}
    eq("four submitted logs yesterday (Ayu, Citra, Eko, Bagas)", sorted(logs), ["USR-003", "USR-004", "USR-005", "USR-006"])
    check("Bagas is late and has 2 overtime hours", (logs.get("USR-004") or {}).get("late_min", 0) > 0 and (logs.get("USR-004") or {}).get("overtime_hours") == 2,
          logs.get("USR-004"))

    # finance opens the internal run for the month of yesterday
    pf, pt = y.replace(day=1), y   # the month of yesterday up to yesterday: the period is over today
    run_body = dict(g.RUN, period_from=str(pf), period_to=str(pt), tax_year=pt.year, tax_month=pt.month, pay_date=str(pt))
    st, run = fm.create("payroll_run", run_body)
    if not check(f"finance opens the internal run {pf}..{pt}", st == 201 and run.get("run_kind") == "internal", (st, run)):
        return
    R = run["name"]
    CTX["karyawan_run"] = R
    st, per = hq.create("attendance_period", {"period_from": str(y), "period_to": str(y)})
    check("head office drafts the attendance period of yesterday", st == 201, (st, per))
    P = (per or {}).get("name", "x")
    asof = str(t)
    st, resp = api.call("POST", "/api/v1/flows/payslip_from_attendance/run", {"event": {"as_of": asof}})
    eq("the morning flow waits while the attendance period is a draft: no slip", (st, len(fm.find("payslip", f"payroll_run == '{R}'"))), (200, 0))
    st, resp = hq.verb("attendance_period", P, "submit")
    if not check("head office submits the attendance period", st == 200, (st, resp)):
        return
    P = rec(resp).get("name") or P
    sums = {s["employee"]: s for s in hq.find("attendance_summary", f"attendance_period == '{P}'")}
    check("summaries for the four who clocked in (Rina is exempt)", {"USR-003", "USR-004", "USR-005", "USR-006"} <= set(sums) and "USR-001" not in sums, sorted(sums))

    # Dodi (head office) may not tick the fill: the run is finance's
    st, resp = hq.patch("payroll_run", R, {"fill_from_attendance": True})
    check("head office cannot write the payroll run (403)", denied(st, resp), (st, resp))
    st, resp = fm.patch("payroll_run", R, {"fill_from_attendance": True})
    if not check("finance ticks Isi slip dari absen and saves", st == 200, (st, resp)):
        return
    r = fm.get("payroll_run", R)
    slips = {s["employee"]: s for s in fm.find("payslip", f"payroll_run == '{R}'")}
    users_with_slip = sorted(slips)
    # who gets one: an employee with a summary in the period, or a monthly worker without one (a run of every outlet)
    want = sorted(set(u for u in sums if not sums[u].get("cancelled")) | {s[0] for s in g.STAFF if s[7] == "monthly"})
    eq("one draft slip per employee with a summary, plus monthly staff without one", users_with_slip, want)
    check("the tick is cleared and the note says how many", r.get("fill_from_attendance") is False and str(r.get("fill_note", "")).startswith(f"{len(want)} slip baru"),
          (r.get("fill_from_attendance"), r.get("fill_note")))
    check("all slips are drafts", all(s.get("docstatus") == 0 for s in slips.values()), [s.get("docstatus") for s in slips.values()])
    for u in ("USR-003", "USR-004", "USR-006"):
        s, sm = slips.get(u) or {}, sums.get(u) or {}
        eq(f"{u}: days, minutes late and overtime hours come from the summary, and the slip links it",
           (s.get("days"), s.get("late_min_total"), s.get("overtime_hours"), s.get("attendance_summary"), s.get("unpaid_days")),
           (sm.get("days_present"), sm.get("late_min_total"), sm.get("overtime_hours"), sm.get("name"), 0))
    eq("Rina (monthly, exempt) gets a slip without attendance: 0 days, no summary",
       ((slips.get("USR-001") or {}).get("days"), (slips.get("USR-001") or {}).get("attendance_summary") or ""), (0, ""))
    st, resp = fm.patch("payroll_run", R, {"fill_from_attendance": True})
    eq("ticking again adds no slip", (st, len(fm.find("payslip", f"payroll_run == '{R}'")), str(fm.get("payroll_run", R).get("fill_note", "")).split(" ")[0]),
       (200, len(want), "0"))
    st, resp = api.call("POST", "/api/v1/flows/payslip_from_attendance/run", {"event": {"as_of": asof}})
    eq("the morning flow (period over, attendance submitted) adds no slip either", (st, len(fm.find("payslip", f"payroll_run == '{R}'"))), (200, len(want)))

    # every amount equals the oracle; then submit (Fajar has no account number: a warning, not a refusal)
    o = oracle_run()
    bad = {}
    for u, s in slips.items():
        w = oracle.slip(profile_input(g, u, s), o)
        diff = {f: (s.get(f), w[ORACLE_KEY.get(f, f)]) for f in SLIP_FIELDS if s.get(f) != w[ORACLE_KEY.get(f, f)]}
        if diff:
            bad[u] = diff
    check(f"all {len(slips)} slips: every amount matches the oracle (BPJS, PPh 21 TER, daily TER for the bakers)", not bad, bad)
    b = slips.get("USR-004") or {}
    # Bagas: 1 day x 120.000 + 15.000 allowance + 2 h x 20.000 weighted overtime = 175.000 cash; casual, daily TER 0% under 450.000
    eq("by hand: Bagas earns 120.000 + 15.000 + 40.000 overtime = 175.000, daily TER 0%", (b.get("base_pay"), b.get("allowance_pay"), b.get("overtime_pay"), b.get("cash_gross"), b.get("pph21")),
       (120_000, 15_000, 40_000, 175_000, 0))
    f = slips.get("USR-007") or {}
    eq("Fajar's slip has no account: left out of the transfer", (f.get("bank_account") or "", f.get("account_holder")), ("", "Fajar Contoh"))
    eq("Ayu's slip carries her account (leading zeros kept), holder and note",
       tuple((slips.get("USR-003") or {}).get(x) for x in ("bank_name", "bank_account", "account_holder", "transfer_note")),
       ("Bank Contoh", "0011000003", "Ayu Contoh", f"Gaji {pt.year}-{pt.month:02d}"))
    st, resp = fm.verb("payslip", f.get("name", "x"), "submit")
    warned = [w.get("rule") for w in (resp or {}).get("warnings", [])] if isinstance(resp, dict) else []
    check("Fajar's slip first answers with warnings (no account number, under the UMK) and needs a confirm",
          st == 422 and "bank_account_warning" in warned and "umk_warning" in warned, (st, resp))
    bad, named = [], {}
    for u, s in slips.items():
        st, resp = fm.verb("payslip", s["name"], "submit", {"force": True})
        named[u] = rec(resp).get("name", s["name"])
        if st != 200:
            bad.append((u, st, resp))
    check(f"finance submits all {len(slips)} slips (confirming the warnings)", not bad, bad)
    check_print("a slip filled from attendance", fm, "payslip", named.get("USR-004", "x"), "payslip", ["Bagas Contoh"])
    st, c = fm.create("payroll_close", {"payroll_run": R})
    st, resp = fm.verb("payroll_close", (c or {}).get("name", "x"), "submit") if st == 201 else (st, c)
    check("finance closes the run", st == 200 and fm.get("payroll_run", R).get("closed") is True, (st, resp))
    r = fm.get("payroll_run", R)
    subs = fm.find("payslip", f"payroll_run == '{R}' && docstatus == 1")
    eq("the run's net total is the sum of its slips", r.get("total_net_pay"), sum(s["net_pay"] for s in subs))

    # bank_transfer_csv: the list export of the run's submitted slips with an account (contract 0024)
    flt = f"payroll_run == '{R}' && docstatus == 1 && bank_account != '' && net_pay > 0"
    url = f"/api/v1/export/r/payslip?filter={Api.q(flt)}&columns={TRANSFER_COLUMNS}&sort=account_holder"
    st, body, ctype = fm.html(url)
    if check("finance downloads the transfer list as CSV", st == 200 and ctype.startswith("text/csv"), (st, ctype, body[:200])):
        rows = list(csv.reader(io.StringIO(body.lstrip("﻿"))))
        head, data = rows[0], [x for x in rows[1:] if x]
        eq("the file has the transfer columns in order", head, ["No. rekening", "Nama pemilik rekening", "Bank", "Gaji bersih", "Berita transfer"])
        with_acct = [s for s in subs if s.get("bank_account") and s["net_pay"] > 0]
        eq("one row per slip with an account (Fajar left out), sorted by name",
           [x[1] for x in data], sorted(s["account_holder"] for s in with_acct))
        eq("the amounts are plain numbers that sum to the net pay of those slips", sum(int(x[3]) for x in data), sum(s["net_pay"] for s in with_acct))
        check("account numbers keep their leading zeros", all(x[0].startswith("00110000") for x in data), [x[0] for x in data])
        CTX["karyawan_csv"] = body
    st, body, ctype = fm.html(url + "&format=xlsx")
    check("the same list as an Excel workbook (account numbers stay text)", st == 200 and "spreadsheetml" in ctype, (st, ctype))
    st, body, ctype = hq.html(url)
    check("head office may not download payslips (no export grant) -> 403", st == 403, (st, ctype))
    ws = dashboard(fm, "transfer_gaji", 92)
    if ws:
        eq("transfer_gaji: total = net pay of submitted slips with an account in the period", num(ws["total"]),
           sum(s["net_pay"] for s in fm.find("payslip", "docstatus == 1 && bank_account != '' && net_pay > 0") if s.get("pay_date", "") >= str(t - datetime.timedelta(days=92))))
    ws = dashboard(fm, "gaji_ringkas", 92)
    if ws:
        eq("gaji_ringkas: no draft slip left, one submitted slip without an account", (num(ws["draft_slips"]), num(ws["no_account"])), (0, 1))
    st, resp = hq.call("GET", "/api/v1/dashboards/gaji_ringkas")
    check("head office does not get the payroll dashboard", st in (403, 404), (st, resp))


def scenario_karyawan_outlet_run(api):
    """A run of one outlet reads only that outlet's summaries and makes no slip for staff without one there; without
    the attendance module the fill would make slips only for monthly staff (checked by tests/day_one_payroll.py's site
    without it). Slips here stay drafts and are deleted."""
    got = members("fm")
    if not got:
        return
    fm, = got
    g = gen()
    t = kernel_today()
    y = t - datetime.timedelta(days=1)
    nm = (y.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)   # the month after: the first run holds this tax month
    body = dict(g.RUN, outlet=KITCHEN, period_from=str(y), period_to=str(y), tax_year=nm.year, tax_month=nm.month, pay_date=str(nm))
    st, run = fm.create("payroll_run", body)
    if not check("finance opens a kitchen-only run of yesterday paid next month", st == 201, (st, run)):
        return
    R = run["name"]
    st, resp = fm.patch("payroll_run", R, {"fill_from_attendance": True})
    slips = fm.find("payslip", f"payroll_run == '{R}'")
    eq("only the kitchen staff with a summary there (Bagas, Eko); no monthly fallback in an outlet run",
       (st, sorted(s["employee"] for s in slips)), (200, ["USR-004", "USR-006"]))
    for s in slips:
        fm.call("DELETE", f"/api/v1/r/payslip/{Api.q(s['name'])}")
    eq("the draft slips are deleted again", len(fm.find("payslip", f"payroll_run == '{R}'")), 0)


SCENARIOS = [scenario_karyawan_month, scenario_karyawan_outlet_run]
