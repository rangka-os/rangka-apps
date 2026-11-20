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
           ("eko", "eko@contohroti.test", "employee", KITCHEN, "USR-006", "Eko Contoh"),
           ("gita", "gita@contohroti.test", "employee", KITCHEN, "USR-008", "Gita Contoh")]
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
    equals the oracle; the slips submit (Fajar, under the UMK, after the warning), the run closes, and the transfer list holds
    every slip with an account and keeps the leading zeros."""
    got = members("fm", "hq", "ayu", "bagas", "citra", "eko")
    if not got:
        return
    fm, hq, ayu, bagas, citra, eko = got
    g = gen()
    t = kernel_today()
    y = t - datetime.timedelta(days=1)
    # staff clock yesterday: Ayu at the shop, Citra at the shop, Bagas 06:12 at the kitchen with 2 overtime hours, Eko on time
    st, resp, ayu_log = full_day(ayu, y, SHOP, "08:00", "18:00", overtime_hours=1, reason="Stok opname akhir bulan")
    pend = [a for a in (resp or {}).get("approvals", []) if a.get("state") == "pending"] if isinstance(resp, dict) else []
    if check("Ayu (cashier) clocks a full day with 1 overtime hour: waits for the shop supervisor (202)", st == 202 and pend, (st, resp)):
        st, resp = decide(citra, pend[0]["id"])
        check("Citra approves Ayu's overtime: the log is submitted", st == 200 and rec(resp).get("docstatus") == 1, (st, resp))
    st, resp, _ = full_day(citra, y, SHOP, "07:55", "17:05")
    check("Citra (shop supervisor) clocks a full day: submitted", st == 200, (st, resp))
    st, resp, _ = full_day(eko, y, KITCHEN, "05:58", "14:00")
    check("Eko (baker, daily) clocks a full day at the kitchen: submitted", st == 200, (st, resp))
    eko_days = 1
    if y.day > 1:   # Eko also worked the day before, entered late: held until head office approves (a second day in the month)
        st, resp, _ = full_day(eko, y - datetime.timedelta(days=1), KITCHEN, "06:00", "14:00", reason="Lupa absen kemarin")
        pend = [a for a in (resp or {}).get("approvals", []) if a.get("state") == "pending"] if isinstance(resp, dict) else []
        if check("Eko enters the day before late: held (202)", st == 202 and pend, (st, resp)):
            st, resp = decide(hq, pend[0]["id"])
            eko_days += 1 if check("head office approves Eko's day before", st == 200 and rec(resp).get("docstatus") == 1, (st, resp)) else 0
    CTX["eko_days"] = eko_days
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
    asof = str(t)

    def morning():
        st, resp = api.call("POST", "/api/v1/flows/payslip_from_attendance/run", {"event": {"as_of": asof}})
        return st, {s_["employee"]: s_ for s_ in fm.find("payslip", f"payroll_run == '{R}'")}

    # audit 101b F1: the morning after the period, before head office has even drafted the attendance period
    st, early = morning()
    eq("the morning flow fills nothing while no attendance of the period is submitted", (st, len(early), fm.get("payroll_run", R).get("auto_filled")), (200, 0, False))
    # finance ticks too early: monthly staff get drafts without attendance, daily staff none yet
    st, resp = fm.patch("payroll_run", R, {"fill_from_attendance": True})
    early = {s_["employee"]: s_ for s_ in fm.find("payslip", f"payroll_run == '{R}'")}
    eq("an early tick drafts the monthly staff only, with 0 days and no summary",
       (st, sorted(early), sorted({(x.get("days"), x.get("attendance_summary") or "", x.get("from_fill")) for x in early.values()})),
       (200, sorted(x[0] for x in g.STAFF if x[7] == "monthly"), [(0, "", True)]))
    st, per = hq.create("attendance_period", {"period_from": str(pf), "period_to": str(pt)})
    check("head office drafts the attendance period of the month", st == 201, (st, per))
    P = (per or {}).get("name", "x")
    st, before = morning()
    eq("the morning flow waits while the attendance period is a draft", (st, len(before), fm.get("payroll_run", R).get("auto_filled")), (200, len(early), False))
    st, resp = hq.verb("attendance_period", P, "submit")
    if not check("head office submits the attendance period", st == 200, (st, resp)):
        return
    P = rec(resp).get("name") or P
    CTX["karyawan_period"] = (P, str(pf), str(pt))
    sums = {s_["employee"]: s_ for s_ in hq.find("attendance_summary", f"attendance_period == '{P}'")}
    check("summaries for those who clocked in (Rina is exempt)", {"USR-003", "USR-004", "USR-005", "USR-006"} <= set(sums) and "USR-001" not in sums, sorted(sums))
    # Dodi (head office) may not tick the fill: the run is finance's
    st, resp = hq.patch("payroll_run", R, {"fill_from_attendance": True})
    check("head office cannot write the payroll run (403)", denied(st, resp), (st, resp))
    st, slips = morning()
    r = fm.get("payroll_run", R)
    basis = {x[0]: x[7] for x in g.STAFF}
    # who gets one: an employee with a summary in the period (daily: at least one day), or a monthly worker without one
    want = sorted(set(u for u, sm in sums.items() if not sm.get("cancelled") and (basis.get(u) == "monthly" or sm.get("days_present", 0) > 0))
                  | {u for u, b in basis.items() if b == "monthly"})
    eq("the morning after the attendance is complete: one draft per employee with a summary, plus monthly staff without one", sorted(slips), want)
    refreshed = sum(1 for u in early if u in sums)
    check("the early monthly drafts are refreshed from the summaries, the daily ones made, once (auto_filled), and the note says so",
          r.get("auto_filled") is True and str(r.get("fill_note", "")).startswith(f"{len(want) - len(early)} slip baru, {refreshed} slip diperbarui"), r.get("fill_note"))
    check("Gita (daily, signed in, never came) has a 0-day summary and no slip: no negative net pay to refuse the fill",
          (sums.get("USR-008") or {}).get("days_present") == 0 and "USR-008" not in slips, (sums.get("USR-008"), sorted(slips)))
    check("all slips are drafts", all(s_.get("docstatus") == 0 for s_ in slips.values()), [s_.get("docstatus") for s_ in slips.values()])
    for u in ("USR-003", "USR-004", "USR-006"):
        s_, sm = slips.get(u) or {}, sums.get(u) or {}
        eq(f"{u}: days, minutes late and overtime hours come from the summary, and the slip links it",
           (s_.get("days"), s_.get("late_min_total"), s_.get("overtime_hours"), s_.get("attendance_summary"), s_.get("unpaid_days")),
           (sm.get("days_present"), sm.get("late_min_total"), sm.get("overtime_hours"), sm.get("name"), 0))
    eq("Ayu's early draft now carries her approved overtime hour", (slips.get("USR-003") or {}).get("overtime_hours"), 1)
    eq("Rina (monthly, exempt) keeps a slip without attendance: 0 days, no summary",
       ((slips.get("USR-001") or {}).get("days"), (slips.get("USR-001") or {}).get("attendance_summary") or ""), (0, ""))
    st, resp = fm.patch("payroll_run", R, {"fill_from_attendance": True})
    eq("ticking again adds and refreshes nothing", (st, len(fm.find("payslip", f"payroll_run == '{R}'")), str(fm.get("payroll_run", R).get("fill_note", "")).split(", ")[:2]),
       (200, len(want), ["0 slip baru", "0 slip diperbarui"]))
    # audit 101b F3: a draft finance deletes is not made again by the next morning (the run is filled once)
    rina = (slips.get("USR-001") or {}).get("name", "x")
    st, resp = fm.call("DELETE", f"/api/v1/r/payslip/{Api.q(rina)}")
    st2, again = morning()
    eq("a deleted draft does not come back the next morning", (st in (200, 204), "USR-001" in again), (True, False))
    st, resp = fm.patch("payroll_run", R, {"fill_from_attendance": True})
    slips = {s_["employee"]: s_ for s_ in fm.find("payslip", f"payroll_run == '{R}'")}
    eq("ticking makes Rina's draft again (finance asked for it)", (st, sorted(slips)), (200, want))

    # every amount equals the oracle; then submit (warnings confirmed with force, as finance does in the web app)
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
    # The BPJS base is the wage, never below the UMK (docs/payroll.md; the consultant item for daily workers of a few days
    # is open in the project's NEEDS-ZEIN.md): 2.900.000 x (JHT 2% + Kesehatan 1%) = 87.000 off a 1-day slip; no JP for
    # casual staff, no late deduction at the default rate 0, no kasbon.
    e = slips.get("USR-006") or {}
    eq("by hand: Bagas's net 175.000 - JHT 58.000 - Kesehatan 29.000 = 88.000 (late 7 minutes cost nothing at rate 0)",
       (b.get("bpjs_base"), b.get("jht_employee"), b.get("jp_employee"), b.get("kes_employee"), b.get("late_deduction"), b.get("other_deduction"), b.get("net_pay")),
       (2_900_000, 58_000, 0, 29_000, 0, 0, 88_000))
    n = CTX.get("eko_days", 1)
    eq(f"by hand: Eko's net {n} x (110.000 + 15.000) - 87.000", (e.get("days"), e.get("cash_gross"), e.get("employee_bpjs"), e.get("net_pay")),
       (n, n * 125_000, 87_000, n * 125_000 - 87_000))
    f = slips.get("USR-007") or {}
    eq("Fajar's slip has no account: left out of the transfer", (f.get("bank_account") or "", f.get("account_holder")), ("", "Fajar Contoh"))
    eq("Ayu's slip carries her account (leading zeros kept), holder and note",
       tuple((slips.get("USR-003") or {}).get(x) for x in ("bank_name", "bank_account", "account_holder", "transfer_note")),
       ("Bank Contoh", "0011000003", "Ayu Contoh", f"Gaji {pt.year}-{pt.month:02d}"))
    st, resp = fm.verb("payslip", f.get("name", "x"), "submit")
    warned = [w.get("rule") for w in (resp or {}).get("warnings", [])] if isinstance(resp, dict) else []
    check("Fajar's slip (under the UMK) first answers with the UMK warning and needs a confirm",
          st == 422 and warned == ["umk_warning"], (st, resp))
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
    """A kitchen-only run saved with the tick at create (audit 101b F7) reads only the kitchen's summaries and makes no
    slip for staff without one there (no monthly fallback in an outlet run). Then head office cancels the attendance
    period (audit 101b F2): a draft filled from it cannot be submitted; a corrected period submitted and the tick again
    refresh the drafts the fill made. The drafts are deleted at the end (the main run already paid these days)."""
    got = members("fm", "hq")
    if not got or not CTX.get("karyawan_period"):
        fail("outlet run: members and the month's attendance period", CTX.get("login_error") or "no period")
        return
    fm, hq = got
    P, pf, pt = CTX["karyawan_period"]
    g = gen()
    y = datetime.date.fromisoformat(pt)
    nm = (y.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)   # the month after: the first run holds this tax month
    body = dict(g.RUN, outlet=KITCHEN, period_from=pf, period_to=pt, tax_year=nm.year, tax_month=nm.month, pay_date=str(nm), fill_from_attendance=True)
    st, run = fm.create("payroll_run", body)
    if not check("finance opens a kitchen-only run of the month, paid next month, with the tick set at create", st == 201, (st, run)):
        return
    R = run["name"]
    slips = fm.find("payslip", f"payroll_run == '{R}'")
    r = fm.get("payroll_run", R)
    eq("filled at create: only the kitchen staff with days there (Bagas, Eko); no monthly fallback in an outlet run; tick cleared",
       (sorted(s_["employee"] for s_ in slips), r.get("fill_from_attendance")), (["USR-004", "USR-006"], False))
    st, resp = hq.verb("attendance_period", P, "cancel")
    if not check("head office cancels the month's attendance period", st == 200, (st, resp)):
        return
    bagas = next((s_ for s_ in slips if s_["employee"] == "USR-004"), {})
    st, resp = fm.verb("payslip", bagas.get("name", "x"), "submit", {"force": True})
    rules = [e.get("rule") for e in (resp or {}).get("errors", [])] if isinstance(resp, dict) else []
    check("a draft filled from a cancelled attendance period cannot be submitted", st == 422 and ("attendance_still_valid" in rules or "attendance_changed" in rules), (st, resp))
    st, per = hq.create("attendance_period", {"period_from": pf, "period_to": pt})
    st, resp = hq.verb("attendance_period", (per or {}).get("name", "x"), "submit") if st == 201 else (st, per)
    if not check("head office submits the corrected attendance period", st == 200, (st, resp)):
        return
    P2 = rec(resp).get("name") or per["name"]
    new_sums = {s_["employee"]: s_["name"] for s_ in hq.find("attendance_summary", f"attendance_period == '{P2}'")}
    st, resp = fm.patch("payroll_run", R, {"fill_from_attendance": True})
    slips = fm.find("payslip", f"payroll_run == '{R}'")
    eq("the tick refreshes both drafts from the corrected summaries", (st, sorted((s_["employee"], s_.get("attendance_summary")) for s_ in slips),
                                                                       str(fm.get("payroll_run", R).get("fill_note", "")).split(", ")[:2]),
       (200, sorted((u, new_sums.get(u)) for u in ("USR-004", "USR-006")), ["0 slip baru", "2 slip diperbarui"]))
    if pf < pt:
        multi_period(api, fm, hq, R, P2, pf, pt)
    for s_ in fm.find("payslip", f"payroll_run == '{R}'"):
        fm.call("DELETE", f"/api/v1/r/payslip/{Api.q(s_['name'])}")
    eq("the draft slips are deleted again", len(fm.find("payslip", f"payroll_run == '{R}'")), 0)


def multi_period(api, fm, hq, R, P2, pf, pt):
    """Audit 101b RF2 and RF1: the month's attendance in two periods (up to the day before, and yesterday). The morning
    flow fills the kitchen run once (Eko: two days, from two summaries). Head office cancels the first period: Eko's draft
    still links the valid second summary, but its snapshot no longer adds up, so the submit is refused; the next morning
    the flow, although the run is already auto_filled, refreshes it to the remaining day."""
    y = datetime.date.fromisoformat(pt)
    d1 = str(y - datetime.timedelta(days=1))
    st, resp = hq.verb("attendance_period", P2, "cancel")
    check("head office cancels the corrected period again", st == 200, (st, resp))
    periods = []
    for a, b in ((pf, d1), (pt, pt)):
        st, per = hq.create("attendance_period", {"period_from": a, "period_to": b})
        st, resp = hq.verb("attendance_period", (per or {}).get("name", "x"), "submit") if st == 201 else (st, per)
        periods.append(rec(resp).get("name") or (per or {}).get("name", "x"))
        check(f"head office submits the attendance period {a}..{b}", st == 200, (st, resp))
    asof = str(kernel_today())
    st, resp = api.call("POST", "/api/v1/flows/payslip_from_attendance/run", {"event": {"as_of": asof}})
    eko = next((s_ for s_ in fm.find("payslip", f"payroll_run == '{R}'") if s_["employee"] == "USR-006"), {})
    eq("the morning flow refreshes Eko's draft from both periods (two days) and marks the run filled",
       (st, eko.get("days"), eko.get("att_days"), fm.get("payroll_run", R).get("auto_filled")), (200, CTX.get("eko_days", 1), CTX.get("eko_days", 1), True))
    st, resp = hq.verb("attendance_period", periods[0], "cancel")
    check("head office cancels the first period (Eko's day before)", st == 200, (st, resp))
    st, resp = fm.verb("payslip", eko.get("name", "x"), "submit", {"force": True})
    rules = [e.get("rule") for e in (resp or {}).get("errors", [])] if isinstance(resp, dict) else []
    check("the draft still links a valid summary but no longer adds up: the submit is refused (attendance_changed)", st == 422 and "attendance_changed" in rules, (st, resp))
    st, resp = api.call("POST", "/api/v1/flows/payslip_from_attendance/run", {"event": {"as_of": asof}})
    eko = fm.get("payslip", eko.get("name", "x"))
    eq("the next morning (run already auto_filled) refreshes the draft to the remaining day, and the note says so",
       (st, eko.get("days"), eko.get("att_days"), str(fm.get("payroll_run", R).get("fill_note", "")).split(", ")[:2]),
       (200, CTX.get("eko_days", 1) - 1, CTX.get("eko_days", 1) - 1, ["0 slip baru", "1 slip diperbarui"]))


def month_run(fm, g, base, k):
    """An internal run of all outlets for the k-th month after `base` (a first of month), opened by finance."""
    m = base
    for _ in range(k):
        m = (m + datetime.timedelta(days=32)).replace(day=1)
    last = (m + datetime.timedelta(days=32)).replace(day=1) - datetime.timedelta(days=1)
    st, run = fm.create("payroll_run", dict(g.RUN, period_from=str(m), period_to=str(last), tax_year=m.year, tax_month=m.month, pay_date=str(last)))
    return (run or {}).get("name", "") if st == 201 else ""


def pending(resp):
    return [a for a in (resp or {}).get("approvals", []) if a.get("state") == "pending"] if isinstance(resp, dict) else []


def receipt(cl, name):
    jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\x00" * 64 + b"\xff\xd9"
    st, resp = cl.upload(f"/api/v1/r/expense_claim/{Api.q(name)}/attachments", {}, "struk.jpg", jpeg)
    return (resp or {}).get("id", "") if st == 201 else ""


def rules_of(resp):
    return [e.get("rule") for e in (resp or {}).get("errors", [])] if isinstance(resp, dict) else []


def scenario_karyawan_kasbon_claims(api):
    """rangka-apps#117. Kasbon: Citra (shop supervisor) asks for Rp 1.500.000 interest-free, Rp 500.000 a month, from her
    phone; she cannot submit it or name someone else's worker; finance picks her worker and submits it; a second kasbon
    while it is open is refused; three monthly slips deduct 500.000 each by themselves until it is repaid; cancelling the
    third slip gives 500.000 back; a fourth month deducts nothing.
    Claims: Ayu (cashier, shop) claims Rp 75.000 parking with salary: over the category limit and without a receipt are
    refused; the claim waits for the shop supervisor; Ayu cannot approve her own, Citra cannot approve a kitchen claim,
    Citra's own claim goes to head office. Ayu's next slip carries the 75.000 outside the taxable gross: gross, PPh 21 and
    BPJS equal the oracle and the same slip without it, net pay + 75.000. A claim approved after the draft was made
    refuses the submit until the slip is corrected; the slip then marks the claims paid; cancelling it gives them back.
    A cash payment pays a claim once; a paid claim cannot be cancelled."""
    got = members("fm", "hq", "ayu", "bagas", "citra", "eko")
    if not got or not CTX.get("karyawan_period"):
        fail("kasbon and claims: members and the month's run", CTX.get("login_error") or "no period")
        return
    fm, hq, ayu, bagas, citra, eko = got
    g = gen()
    base = datetime.date.fromisoformat(CTX["karyawan_period"][1]).replace(day=1)
    worker = {w["employee"]: w["name"] for w in fm.find("worker", "employee != ''")}
    profile = {}
    for p in fm.find("payroll_profile", "active"):
        e = next((u for u, w in worker.items() if w == p["worker"]), "")
        if e:
            profile[e] = p["name"]

    # --- kasbon request (screen ajukan_kasbon: a draft cash_advance)
    st, k = citra.create("cash_advance", {"amount": 1_500_000, "installment": 500_000, "notes": "Biaya sekolah anak"})
    if not check("Citra asks for a kasbon of 1.500.000, 500.000 a month, from her phone: a draft without a worker",
                 st == 201 and k.get("docstatus") == 0 and not k.get("worker") and k.get("employee") == "USR-005" and k.get("installments") == 3, (st, k)):
        return
    K = k["name"]
    st, resp = citra.verb("cash_advance", K, "submit")
    check("Citra cannot submit her own kasbon (finance pays it out) -> 403", denied(st, resp), (st, resp))
    st, resp = bagas.call("GET", f"/api/v1/r/cash_advance/{Api.q(K)}")
    check("Bagas cannot read Citra's kasbon", st in (403, 404), (st, resp))
    st, resp = bagas.create("cash_advance", {"worker": worker.get("USR-005", "x"), "amount": 100_000, "installment": 100_000})
    check("Bagas cannot request a kasbon for Citra's worker (refused by rule or by the worker link he cannot read)", st in (403, 422), (st, resp))
    st, resp = citra.patch("cash_advance", K, {"installment": 2_000_000})
    check("an installment above the amount is refused", st == 422 and "installment_range" in rules_of(resp), (st, resp))
    st, resp = fm.verb("cash_advance", K, "submit")
    check("finance cannot submit a request before picking the worker", st == 422 and "worker_on_submit" in rules_of(resp), (st, resp))
    st, resp = fm.patch("cash_advance", K, {"worker": worker.get("USR-005", "x")})
    check("finance picks Citra's worker", st == 200 and rec(resp).get("employee") == "USR-005", (st, resp))
    st, resp = citra.patch("cash_advance", K, {"amount": 5_000_000})
    check("once finance has picked the worker, Citra cannot raise her request (audit F1) -> 403", denied(st, resp), (st, resp))
    st, fk = fm.create("cash_advance", {"worker": worker.get("USR-006", "x"), "amount": 100_000, "installment": 100_000})
    st, resp = eko.patch("cash_advance", (fk or {}).get("name", "x"), {"amount": 5_000_000})
    check("Eko reads a kasbon finance drafted for him but cannot change it -> 403", denied(st, resp), (st, resp))
    fm.call("DELETE", f"/api/v1/r/cash_advance/{Api.q((fk or {}).get('name', 'x'))}")
    st, resp = fm.verb("cash_advance", K, "submit")
    if not check("finance submits the kasbon (pays it out)", st == 200 and rec(resp).get("amount") == 1_500_000, (st, resp)):
        return
    K = rec(resp).get("name") or K
    bal = (fm.find("cash_advance_balance", f"cash_advance == '{K}'") or [{}])[0]
    eq("its balance: 1.500.000 open, 500.000 a month, nothing repaid",
       (bal.get("amount"), bal.get("installment"), bal.get("repaid_total"), bal.get("remaining"), bal.get("state")), (1_500_000, 500_000, 0, 1_500_000, "open"))
    eq("Citra reads her own kasbon balance, Ayu reads none", (len(citra.find("cash_advance_balance", "true")), len(ayu.find("cash_advance_balance", "true"))), (1, 0))
    st, k2 = fm.create("cash_advance", {"worker": worker.get("USR-005", "x"), "amount": 100_000})
    st, resp = fm.verb("cash_advance", k2.get("name", "x"), "submit") if st == 201 else (st, k2)
    check("a second kasbon while the first is open is refused (one open kasbon per worker)", st == 422 and "advance_open" in rules_of(resp), (st, resp))
    fm.call("DELETE", f"/api/v1/r/cash_advance/{Api.q(k2.get('name', 'x'))}")

    runs = [month_run(fm, g, base, i) for i in (2, 3, 4, 5)]
    if not check("finance opens four monthly internal runs after the paid month", all(runs), runs):
        return

    def slip(run, who):
        st, sl = fm.create("payslip", {"payroll_run": run, "profile": profile.get(who, "x")})
        return st, (sl or {})

    def submit(name):
        st, resp = fm.verb("payslip", name, "submit", {"force": True})
        return st, resp, rec(resp).get("name", name)

    deducted = []
    for i in range(3):
        st, sl = slip(runs[i], "USR-005")
        deducted.append((st, sl.get("cash_advance"), sl.get("other_deduction")))
        st, resp, n = submit(sl.get("name", "x"))
        check(f"Citra's slip of month {i + 1} submits", st == 200, (st, resp))
        if i == 2:
            third = n
    eq("three slips each fill the kasbon and its 500.000 installment by themselves", deducted, [(201, K, 500_000)] * 3)
    bal = fm.get("cash_advance_balance", bal.get("name", "x"))
    eq("after three slips the kasbon is repaid", (bal.get("repaid_total"), bal.get("remaining"), bal.get("state")), (1_500_000, 0, "repaid"))
    st, resp = fm.verb("payslip", third, "cancel")
    bal = fm.get("cash_advance_balance", bal.get("name", "x"))
    eq("cancelling the third slip gives its 500.000 back", (st, bal.get("repaid_total"), bal.get("remaining"), bal.get("state")), (200, 1_000_000, 500_000, "open"))
    st, sl = slip(runs[2], "USR-005")
    st2, resp, _ = submit(sl.get("name", "x"))
    bal = fm.get("cash_advance_balance", bal.get("name", "x"))
    eq("a new slip of that month deducts the 500.000 again and repays it", (sl.get("other_deduction"), st2, bal.get("state")), (500_000, 200, "repaid"))
    st, sl = slip(runs[3], "USR-005")
    eq("a fourth month deducts nothing", (st, sl.get("cash_advance") or "", sl.get("other_deduction")), (201, "", 0))
    fm.call("DELETE", f"/api/v1/r/payslip/{Api.q(sl.get('name', 'x'))}")
    st, resp = fm.verb("cash_advance", K, "cancel")
    check("a kasbon repaid by submitted slips cannot be cancelled", st == 422 and "advance_repaid" in rules_of(resp), (st, resp))

    # --- expense claims
    cats = {}
    for name, acct, needs, cap in (("Parkir", "61600", True, 100_000), ("ATK", "61800", False, 0)):
        st, c = hq.create("expense_category", {"category_name": name, "expense_account": acct, "needs_receipt": needs, "max_per_claim": cap})
        cats[name] = (c or {}).get("name", "x")
        check(f"head office sets the claim category {name}", st == 201, (st, c))
    st, resp = bagas.create("expense_claim", {"category": cats["ATK"], "amount": 10_000, "description": "Plastik", "pay_with": "cash", "outlet": SHOP})
    check("Bagas (kitchen) cannot put his claim on the shop, whose supervisor would approve it (audit F2)", st == 422 and "own_outlet" in rules_of(resp), (st, resp))
    st, resp = ayu.create("expense_claim", {"category": cats["Parkir"], "amount": 150_000, "description": "Parkir antar kue", "pay_with": "payroll"})
    check("a parking claim over the category's limit is refused", st == 422 and "within_limit" in rules_of(resp), (st, resp))
    st, c = ayu.create("expense_claim", {"category": cats["Parkir"], "amount": 75_000, "description": "Parkir antar kue", "pay_with": "payroll"})
    if not check("Ayu drafts a 75.000 parking claim paid with salary (outlet defaults to the shop)", st == 201 and c.get("employee") == "USR-003" and c.get("outlet") == SHOP, (st, c)):
        return
    C1 = c["name"]
    st, resp = ayu.verb("expense_claim", C1, "submit")
    check("without the receipt photo the claim is refused", st == 422 and "receipt_needed" in rules_of(resp), (st, resp))
    ayu.patch("expense_claim", C1, {"receipt": receipt(ayu, C1)})
    st, resp = ayu.verb("expense_claim", C1, "submit")
    pend = pending(resp)
    if not check("with the photo it waits for the shop supervisor or head office (202)", st == 202 and pend and set(pend[0].get("approvers", [])) == {"outlet_supervisor", "head_office"}, (st, resp)):
        return
    st, resp = decide(ayu, pend[0]["id"])
    check("Ayu cannot approve her own claim -> 403", st == 403, (st, resp))
    st, e = eko.create("expense_claim", {"category": cats["ATK"], "amount": 20_000, "description": "Plastik roti", "pay_with": "cash"})
    st, resp = eko.verb("expense_claim", (e or {}).get("name", "x"), "submit")
    pe = pending(resp)
    if check("Eko (kitchen) claims 20.000 supplies in cash without a receipt (not needed): waits (202)", st == 202 and pe, (st, resp)):
        st, resp = decide(citra, pe[0]["id"])
        check("Citra, the shop supervisor, cannot approve a kitchen claim -> 403", st == 403, (st, resp))
        st, resp = decide(hq, pe[0]["id"])
        check("head office approves Eko's claim", st == 200, (st, resp))
    E1 = rec(resp).get("name") or (e or {}).get("name", "x")
    st, cc = citra.create("expense_claim", {"category": cats["ATK"], "amount": 30_000, "description": "Spidol label", "pay_with": "cash"})
    st, resp = citra.verb("expense_claim", (cc or {}).get("name", "x"), "submit")
    pc = pending(resp)
    if check("Citra's own claim waits for head office only (202)", st == 202 and pc and pc[0].get("approvers") == ["head_office"], (st, resp)):
        st, resp = decide(citra, pc[0]["id"])
        check("Citra cannot approve her own claim -> 403", st == 403, (st, resp))
        st, resp = decide(hq, pc[0]["id"])
        check("head office approves it", st == 200, (st, resp))
    st, resp = decide(citra, pend[0]["id"])
    if not check("Citra approves Ayu's parking claim: submitted", st == 200 and rec(resp).get("docstatus") == 1, (st, resp)):
        return
    C1 = rec(resp).get("name") or C1
    due = {x["claim"]: x for x in fm.find("claim_settlement", "true")}
    eq("approved claims wait to be paid, each once", sorted((x["employee"], x["amount"], x["pay_with"], x["state"]) for x in due.values()),
       sorted([("USR-003", 75_000, "payroll", "due"), ("USR-006", 20_000, "cash", "due"), ("USR-005", 30_000, "cash", "due")]))
    eq("Ayu sees only her own claims", sorted(x["employee"] for x in ayu.find("expense_claim", "true")), ["USR-003"])
    st, resp = ayu.create("claim_payment", {"claim": C1})
    check("Ayu cannot pay a claim -> 403", denied(st, resp), (st, resp))

    # Ayu's next slip carries the claim, untaxed
    R = runs[0]
    st, sl = slip(R, "USR-003")
    S = sl.get("name", "x")
    eq("Ayu's slip of the next month carries the 75.000 claim as reimbursement", (st, sl.get("reimbursement")), (201, 75_000))
    fm.patch("payslip", S, {"reimbursement": 0})
    without = fm.get("payslip", S)
    fm.patch("payslip", S, {"reimbursement": 75_000})
    withr = fm.get("payslip", S)
    eq("the reimbursement changes nothing but the net pay: gross, PPh 21 and BPJS equal the slip without it, net + 75.000",
       tuple(withr.get(f) for f in ("cash_gross", "gross_taxable", "pph21", "employee_bpjs", "employer_cost", "net_pay")),
       tuple(without.get(f) for f in ("cash_gross", "gross_taxable", "pph21", "employee_bpjs", "employer_cost")) + (without.get("net_pay", 0) + 75_000,))
    w = oracle.slip(profile_input(g, "USR-003", withr), oracle_run())
    diff = {f: (withr.get(f), w[ORACLE_KEY.get(f, f)]) for f in SLIP_FIELDS if f != "net_pay" and withr.get(f) != w[ORACLE_KEY.get(f, f)]}
    check("the oracle: every amount of the slip equals the slip without a claim, and net = oracle net + 75.000",
          not diff and withr.get("net_pay") == w["net_pay"] + 75_000, (diff, withr.get("net_pay"), w["net_pay"]))
    st, c2 = ayu.create("expense_claim", {"category": cats["Parkir"], "amount": 25_000, "description": "Parkir belanja tepung", "pay_with": "payroll"})
    C2 = (c2 or {}).get("name", "x")
    ayu.patch("expense_claim", C2, {"receipt": receipt(ayu, C2)})
    st, resp = ayu.verb("expense_claim", C2, "submit")
    p2 = pending(resp)
    st, resp = decide(citra, p2[0]["id"]) if p2 else (st, resp)
    C2 = rec(resp).get("name") or C2
    check("Ayu's second parking claim (25.000) is approved after the slip was drafted", st == 200, (st, resp))
    st, resp, _ = submit(S)
    check("the slip no longer matches the claims due: refused until corrected", st == 422 and "reimbursement_changed" in rules_of(resp), (st, resp))
    fm.patch("payslip", S, {"reimbursement": 100_000})
    st, resp, S = submit(S)
    if not check("finance corrects the reimbursement to 100.000: the slip submits", st == 200 and rec(resp).get("net_pay") == withr.get("net_pay", 0) + 25_000, (st, resp)):
        return
    paid = {x["claim"]: x for x in fm.find("claim_settlement", "employee == 'USR-003'")}
    eq("both of Ayu's claims are paid by the slip", sorted((x["amount"], x["paid"], x["paid_by"]) for x in paid.values()), [(25_000, True, S), (75_000, True, S)])
    eq("the run's totals hold the reimbursement inside the net pay", fm.get("payroll_run", R).get("total_reimbursement"), 100_000)
    st, resp = fm.verb("payslip", S, "cancel")
    paid = {x["claim"]: x for x in fm.find("claim_settlement", "employee == 'USR-003'")}
    eq("cancelling the slip gives both claims back", (st, sorted((x["paid"], x["paid_by"] or "") for x in paid.values())), (200, [(False, ""), (False, "")]))
    # finance pays the 25.000 in cash instead; once only
    st, pay = fm.create("claim_payment", {"claim": C2, "method": "cash"})
    st, resp = fm.verb("claim_payment", (pay or {}).get("name", "x"), "submit") if st == 201 else (st, pay)
    P1 = rec(resp).get("name", "x")
    check("finance pays Ayu's 25.000 claim in cash", st == 200 and rec(resp).get("amount") == 25_000, (st, resp))
    st, pay2 = fm.create("claim_payment", {"claim": C2, "method": "bank"})
    st, resp = fm.verb("claim_payment", (pay2 or {}).get("name", "x"), "submit") if st == 201 else (st, pay2)
    check("a second payment of the same claim is refused", st == 422 and "claim_paid" in rules_of(resp), (st, resp))
    fm.call("DELETE", f"/api/v1/r/claim_payment/{Api.q((pay2 or {}).get('name', 'x'))}")
    st, resp = hq.verb("expense_claim", C2, "cancel")
    check("a paid claim cannot be cancelled", st == 422 and "claim_paid" in rules_of(resp), (st, resp))
    st, sl = slip(R, "USR-003")
    st2, resp, S2 = submit(sl.get("name", "x"))
    eq("a new slip of the month carries only the claim still due (75.000) and pays it", (sl.get("reimbursement"), st2), (75_000, 200))
    st, pe2 = fm.create("claim_payment", {"claim": E1, "method": "cash"})
    st, resp = fm.verb("claim_payment", (pe2 or {}).get("name", "x"), "submit") if st == 201 else (st, pe2)
    check("finance pays Eko's claim in cash", st == 200, (st, resp))
    st, resp = fm.verb("claim_payment", P1, "cancel")
    st2, resp2 = hq.verb("expense_claim", C2, "cancel")
    eq("cancelling the cash payment gives the claim back, which head office may then cancel",
       (st, st2, (fm.find("claim_settlement", f"claim == '{C2}'") or [{}])[0].get("state")), (200, 200, "cancelled"))
    ws = dashboard(ayu, "klaim_kasbon_saya", 92)
    if ws:
        eq("klaim_kasbon_saya for Ayu: no claim waiting, nothing due, no kasbon", (num(ws["claims_waiting"]), num(ws["claims_due"]), num(ws["kasbon_left"])), (0, 0, 0))
    ws = dashboard(citra, "klaim_kasbon_saya", 92)
    if ws:
        eq("klaim_kasbon_saya for Citra: her 30.000 claim due in cash, her kasbon repaid", (num(ws["claims_due"]), num(ws["kasbon_left"])), (30_000, 0))
    ws = dashboard(fm, "gaji_ringkas", 92)
    if ws:
        eq("gaji_ringkas: claims due 30.000 (Citra's), no kasbon left", (num(ws["claims_due"]), num(ws["kasbon_open"])), (30_000, 0))
    st, cl = fm.create("payroll_close", {"payroll_run": R})
    st, resp = fm.verb("payroll_close", (cl or {}).get("name", "x"), "submit") if st == 201 else (st, cl)
    check("finance closes the month that paid Ayu's claim", st == 200, (st, resp))
    CL = rec(resp).get("name", "x")
    st, resp = fm.call("GET", "/api/v1/meta")
    if "gl_entry" in ((resp or {}).get("models") or {}):   # pembukuan installed: the journals of #117
        def lines(v):
            return sorted((x["account"], x["amount"]) for x in fm.find("gl_entry", f"voucher == '{v}' && entry == 'post'"))
        eq("journal of Ayu's approved parking claim: Dr Beban transport 75.000 / Cr Utang reimburse karyawan", lines(C1), [("21510", -75_000), ("61600", 75_000)])
        eq("journal of Eko's supplies claim: Dr Beban perlengkapan / Cr Utang reimburse karyawan", lines(E1), [("21510", -20_000), ("61800", 20_000)])
        pays = [x["name"] for x in fm.find("claim_payment", f"claim == '{E1}' && docstatus == 1")]
        eq("journal of the cash payment: Dr Utang reimburse karyawan / Cr Kas", lines(pays[0] if pays else "x"), [("11100", -20_000), ("21510", 20_000)])
        eq("the payroll close debits Utang reimburse karyawan with the claims the slips paid", [a_ for a_ in lines(CL) if a_[0] == "21510"], [("21510", 75_000)])
        left = sum(x["amount"] for x in fm.find("gl_entry", "account == '21510'"))
        eq("Utang reimburse karyawan = the claims still due (Citra's 30.000)", left, -30_000)
        eq("journal of the kasbon: Dr Piutang karyawan / Cr Kas", lines(K), [("11100", -1_500_000), ("11330", 1_500_000)])
    CTX["karyawan_claims"] = {"slip": S2, "claims": [C1, C2, E1], "kasbon": K}


SCENARIOS = [scenario_karyawan_month, scenario_karyawan_outlet_run, scenario_karyawan_kasbon_claims]
