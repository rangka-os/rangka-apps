"""Day-one scenarios of module leave on demo-karyawan (rangka-apps#114): leave and permits with balances.

Loaded by tests/day_one.py (bind() hands over its helpers; the scenarios are appended to the demo-karyawan list, after the
payroll ones): `python3 tests/day_one.py --site demo-karyawan`. The leave dates sit in March of last year and in a month
four months back, away from the attendance periods the payroll scenarios submit. Every person and number is synthetic.

Members: Ayu (cashier, shop), Citra (outlet supervisor, shop), Bagas and Eko (employee, kitchen), Gita (employee, kitchen,
less than 12 months of service), Dodi (head office), Rina (finance). Scenarios:
  leave_request_within_balance, leave_public_holiday_not_counted, leave_over_balance_rejected, leave_cancel_returns_days,
  leave_concurrent_requests_cannot_overdraw, leave_unpaid_deducts_on_slip, leave_employee_sees_only_own,
  leave_no_balance_and_doctor_note.
"""
import calendar
import datetime
import json
import re
import threading

H = {}
S = {}   # state shared by the scenarios, in order


def bind(m):
    for k in ("ok", "fail", "check", "eq", "CTX", "kernel_today", "check_print"):
        H[k] = getattr(m, k)
    globals().update(H)


def members(*keys):
    got = [CTX.get(k) for k in keys]
    if not all(got):
        fail("demo-karyawan leave: members signed in", CTX.get("login_error") or [k for k in keys if not CTX.get(k)])
        return None
    return got


def rec(resp):
    return ((resp or {}).get("record") or resp or {}) if isinstance(resp, dict) else {}


def said(resp, needle):
    return needle.lower() in json.dumps(resp, ensure_ascii=False).lower()


def monday_on_or_after(d):
    return d + datetime.timedelta(days=(7 - d.weekday()) % 7)


def week(n, start):
    """Monday of week n (0 based) from `start`, and the dates of its Friday."""
    mon = start + datetime.timedelta(days=7 * n)
    return mon, mon + datetime.timedelta(days=4)


def ltype(hq, code):
    rows = hq.find("leave_type", f"code == '{code}'")
    return rows[0]["name"] if rows else ""


def balance(hq, user, year):
    rows = hq.find("leave_balance", f"employee == '{user}' && year == {year}")
    return rows[0] if rows else {}


def request(cl, user, typ, d1, d2, **extra):
    return cl.create("leave_request", dict({"employee": user, "leave_type": typ, "date_from": str(d1), "date_to": str(d2)}, **extra))


def setup_state(hq):
    """Last year's March (the leave dates) and the grant day inside it."""
    if "Y" in S:
        return
    S["Y"] = kernel_today().year - 1
    S["start"] = monday_on_or_after(datetime.date(S["Y"], 3, 3))


def grant(api, as_of):
    return api.call("POST", "/api/v1/flows/leave_grant_daily/run", {"event": {"as_of": str(as_of)}})


def scenario_leave_request_within_balance(api):
    """The daily grant flow makes the five standard types (the site has them), gives the five employees with more than 12
    months of service 12 days for the year once (a second run adds none), a request within the balance is a draft the
    employee cannot approve, and the shop supervisor's approval spends its days from the balance."""
    got = members("hq", "ayu", "citra")
    if not got:
        return
    hq, ayu, citra = got
    setup_state(hq)
    Y, start = S["Y"], S["start"]
    st, _ = grant(api, f"{Y}-12-31")
    eq("the grant flow runs", st, 200)
    types = hq.find("leave_type", "name != ''")
    eq("the five standard leave types", sorted(t["code"] for t in types), ["annual", "joint", "maternity", "sick", "unpaid"])
    bals = hq.find("leave_balance", f"year == {Y}")
    eq("five employees get 12 annual days for last year", sorted((b["employee"], b["granted"], b["used"]) for b in bals),
       [("USR-002", 12, 0), ("USR-003", 12, 0), ("USR-004", 12, 0), ("USR-005", 12, 0), ("USR-006", 12, 0)])
    grant(api, f"{Y}-12-31")
    eq("a second grant run adds no second balance", len(hq.find("leave_balance", f"year == {Y}")), 5)
    S["annual"] = ltype(hq, "annual")
    mon, fri = week(0, start)
    st, r1 = request(ayu, "USR-003", S["annual"], mon, fri, reason="Pulang kampung")
    r1 = rec(r1)
    if not check("Ayu requests annual leave for one working week: a draft of 5 days waiting for approval",
                 st == 201 and r1.get("days") == 5 and r1.get("docstatus") == 0 and not r1.get("rejected") and r1.get("outlet") == "OUT-02", (st, r1)):
        return
    eq("the draft notes the balance left (12) and moves nothing", (r1.get("balance_left"), balance(hq, "USR-003", Y).get("used")), (12, 0))
    S["ayu_w1"] = r1["name"]
    st, resp = ayu.verb("leave_request", r1["name"], "submit")
    check("Ayu cannot approve her own request", st == 403, (st, resp))
    st, resp = citra.verb("leave_request", r1["name"], "submit")
    check("Citra (shop supervisor) approves it", st == 200 and rec(resp).get("docstatus") == 1, (st, resp))
    S["ayu_w1"] = rec(resp).get("name", r1["name"])   # a submitted request gets its number
    b = balance(hq, "USR-003", Y)
    eq("the approval spent 5 days: used 5, remaining 7", (b.get("used"), b.get("remaining")), (5, 7))


def scenario_leave_public_holiday_not_counted(api):
    """A public holiday on a weekday is not a day of leave; a cuti bersama day also takes one day off every annual balance
    (the default of the annual type) once; a request over weekend days only has no days and is refused."""
    got = members("hq", "ayu", "citra")
    if not got or "ayu_w1" not in S:
        return
    hq, ayu, citra = got
    Y, start = S["Y"], S["start"]
    mon, fri = week(1, start)
    wed, fri_day = mon + datetime.timedelta(days=2), fri
    st, h1 = hq.create("public_holiday", {"holiday_date": str(wed), "holiday_name": "Hari libur contoh"})
    check("head office adds a public holiday on the Wednesday", st == 201, (st, h1))
    st, h2 = hq.create("public_holiday", {"holiday_date": str(fri_day), "holiday_name": "Cuti bersama contoh", "joint": True})
    check("head office adds a cuti bersama on the Friday", st == 201, (st, h2))
    st, dup = hq.create("public_holiday", {"holiday_date": str(wed), "holiday_name": "Dobel"})
    check("a second holiday on the same day is refused", st in (409, 422), (st, dup))
    joint = sorted((b["employee"], b["joint_used"], b["remaining"]) for b in hq.find("leave_balance", f"year == {Y}"))
    eq("the cuti bersama took one day off each annual balance (Ayu 12 - 5 - 1 = 6)", joint,
       [("USR-002", 1, 11), ("USR-003", 1, 6), ("USR-004", 1, 11), ("USR-005", 1, 11), ("USR-006", 1, 11)])
    st, r2 = request(ayu, "USR-003", S["annual"], mon, fri)
    r2 = rec(r2)
    check("Monday to Friday with a holiday on Wednesday and a cuti bersama on Friday is 3 days (2 holidays not counted)",
          st == 201 and r2.get("days") == 3 and r2.get("holiday_days") == 2 and r2.get("workdays") == 5, (st, r2))
    sat = mon + datetime.timedelta(days=5)
    st, bad = request(ayu, "USR-003", S["annual"], sat, sat + datetime.timedelta(days=1))
    check("only a weekend is refused (no day of leave)", st == 422 and said(bad, "akhir pekan"), (st, bad))
    mat = ltype(hq, "maternity")
    st, mm = request(ayu, "USR-003", mat, mon, mon + datetime.timedelta(days=6))
    check("a type counting calendar days counts weekends and holidays (7 days) and has no balance", st == 201 and rec(mm).get("days") == 7, (st, mm))
    if st == 201:
        ayu.call("DELETE", f"/api/v1/r/leave_request/{rec(mm)['name']}")
    if st != 201 or not r2:
        return
    S["ayu_w2"] = r2["name"]
    st, resp = citra.verb("leave_request", r2["name"], "submit")
    check("Citra approves the 3 days", st == 200, (st, resp))
    S["ayu_w2"] = rec(resp).get("name", r2["name"])
    b = balance(hq, "USR-003", Y)
    eq("used 5 + 3 = 8, joint 1, remaining 3", (b.get("used"), b.get("joint_used"), b.get("remaining")), (8, 1, 3))
    unp = ltype(hq, "unpaid")
    st, o = request(ayu, "USR-003", unp, mon, mon)
    if st == 201:
        st2, resp = citra.verb("leave_request", rec(o)["name"], "submit")
        check("leave that overlaps approved leave is refused at approval", st2 == 422 and said(resp, "sudah punya cuti"), (st2, resp))
        ayu.call("DELETE", f"/api/v1/r/leave_request/{rec(o)['name']}")
    else:
        fail("Ayu drafts an overlapping permit", (st, o))


def scenario_leave_over_balance_rejected(api):
    """A request for more days than the balance has is refused when it is saved, with a message the employee can act on; the
    approval reads the live balance again, and a request using the last days empties it."""
    got = members("hq", "ayu", "citra")
    if not got or "ayu_w2" not in S:
        return
    hq, ayu, citra = got
    Y, start = S["Y"], S["start"]
    mon, fri = week(2, start)
    st, bad = request(ayu, "USR-003", S["annual"], mon, fri)
    check("5 days against 3 left is refused when saved", st == 422 and said(bad, "jatah cuti tidak cukup"), (st, bad))
    st, ok3 = request(ayu, "USR-003", S["annual"], mon, mon + datetime.timedelta(days=2))
    ok3 = rec(ok3)
    if not check("3 days against 3 left is accepted", st == 201 and ok3.get("days") == 3 and ok3.get("balance_left") == 3, (st, ok3)):
        return
    st, resp = citra.verb("leave_request", ok3["name"], "submit")
    check("the approval spends the last 3 days", st == 200, (st, resp))
    S["ayu_w3"] = rec(resp).get("name", ok3["name"])
    b = balance(hq, "USR-003", Y)
    eq("remaining 0", (b.get("used"), b.get("remaining")), (11, 0))
    tue = mon + datetime.timedelta(days=7)
    st, bad = request(ayu, "USR-003", S["annual"], tue, tue)
    check("one more day with nothing left is refused", st == 422 and said(bad, "jatah cuti tidak cukup"), (st, bad))


def scenario_leave_cancel_returns_days(api):
    """Cancelling approved leave returns its days to the balance (by the approver, never the employee); the freed days can
    be requested again."""
    got = members("hq", "ayu", "citra")
    if not got or "ayu_w1" not in S or "ayu_w3" not in S:
        return
    hq, ayu, citra = got
    Y, start = S["Y"], S["start"]
    st, resp = ayu.verb("leave_request", S["ayu_w1"], "cancel")
    check("Ayu cannot cancel approved leave herself", st == 403, (st, resp))
    st, resp = citra.verb("leave_request", S["ayu_w1"], "cancel")
    check("Citra cancels the approved week", st == 200 and rec(resp).get("docstatus") == 2, (st, resp))
    b = balance(hq, "USR-003", Y)
    eq("its 5 days came back: used 6, remaining 5", (b.get("used"), b.get("remaining")), (6, 5))
    tue = week(2, start)[0] + datetime.timedelta(days=7)
    st, again = request(ayu, "USR-003", S["annual"], tue, tue)
    check("a day can be requested again", st == 201, (st, again))
    if st == 201:
        ayu.call("DELETE", f"/api/v1/r/leave_request/{rec(again)['name']}")


def scenario_leave_concurrent_requests_cannot_overdraw(api):
    """Eko has 11 days (12 less the cuti bersama) and two drafts of 7 days each, both fine on their own. Head office
    approves both at the same moment: the flow read lock of contract 0025 serialises the approvals, exactly one succeeds and
    the balance is never overdrawn."""
    got = members("hq", "eko")
    if not got or "annual" not in S:
        return
    hq, eko = got
    Y, start = S["Y"], S["start"]
    names = []
    for n in (4, 6):
        mon, _ = week(n, start)
        st, r = request(eko, "USR-006", S["annual"], mon, mon + datetime.timedelta(days=8))   # Monday to the next Tuesday: 7 days
        r = rec(r)
        if not check(f"Eko drafts 7 days from week {n}", st == 201 and r.get("days") == 7, (st, r)):
            return
        names.append(r["name"])
    results = []

    def go(n):
        results.append(hq.verb("leave_request", n, "submit"))

    th = [threading.Thread(target=go, args=(n,)) for n in names]
    for t in th:
        t.start()
    for t in th:
        t.join()
    codes = sorted(st for st, _ in results)
    check("exactly one of the two approvals went through, the other was refused", codes[0] == 200 and codes[1] != 200, results)
    loser = [resp for st, resp in results if st != 200]
    check("the refused one says the balance is not enough", bool(loser) and said(loser[0], "jatah cuti tidak cukup"), loser)
    b = balance(hq, "USR-006", Y)
    eq("used 7, remaining 4: never overdrawn", (b.get("used"), b.get("remaining")), (7, 4))


def month_back(today, n):
    y, mth = today.year, today.month - n
    while mth < 1:
        y, mth = y - 1, mth + 12
    return datetime.date(y, mth, 1)


def scenario_leave_unpaid_deducts_on_slip(api):
    """Approved unpaid leave reaches the payslip: Ayu (3.000.000 a month) takes 2 unpaid days and her slip is cut by
    salary / 25 per day; Bagas (120.000 a day) takes 1 unpaid day and 1 paid annual day and is paid for the paid day, the
    unpaid day shown with its basis and never taken off twice. Leave is decided before the attendance period; after the
    period is submitted its days can no longer be approved or cancelled."""
    got = members("fm", "hq", "ayu", "bagas", "citra")
    if not got:
        return
    fm, hq, ayu, bagas, citra = got
    t = kernel_today()
    st, _ = grant(api, t)   # this year's balances (the month four months back may be in it)
    eq("this year's grant runs", st, 200)
    pf = month_back(t, 4)
    pt = pf.replace(day=calendar.monthrange(pf.year, pf.month)[1])
    mon = monday_on_or_after(pf)
    unp, annual = ltype(hq, "unpaid"), ltype(hq, "annual")
    st, a = request(ayu, "USR-003", unp, mon + datetime.timedelta(days=1), mon + datetime.timedelta(days=2), reason="Urusan keluarga")
    a = rec(a)
    if not check("Ayu requests 2 unpaid days", st == 201 and a.get("days") == 2 and a.get("is_unpaid") is True, (st, a)):
        return
    st, resp = citra.verb("leave_request", a["name"], "submit")
    check("Citra approves the unpaid days (no balance moves)", st == 200, (st, resp))
    a_name = rec(resp).get("name", a["name"])
    st, b1 = request(bagas, "USR-004", unp, mon + datetime.timedelta(days=3), mon + datetime.timedelta(days=3))
    st2, b2 = request(bagas, "USR-004", annual, mon + datetime.timedelta(days=4), mon + datetime.timedelta(days=4))
    if not check("Bagas requests 1 unpaid day and 1 annual day", st == 201 and st2 == 201, (st, b1, st2, b2)):
        return
    for n in (rec(b1)["name"], rec(b2)["name"]):
        st, resp = hq.verb("leave_request", n, "submit")
        check("head office approves Bagas's request", st == 200, (st, resp))
    st, per = hq.create("attendance_period", {"period_from": str(pf), "period_to": str(pt)})
    if not check(f"head office drafts the attendance period {pf}..{pt}", st == 201, (st, per)):
        return
    st, resp = hq.verb("attendance_period", per["name"], "submit")
    if not check("head office submits it: the leave is in the summaries", st == 200, (st, resp)):
        return
    per_name = rec(resp).get("name", per["name"])
    sums = {x["employee"]: x for x in hq.find("attendance_summary", f"attendance_period == '{per_name}'")}
    eq("Ayu's summary: 2 unpaid leave days, no paid leave", (sums.get("USR-003", {}).get("leave_days"), sums.get("USR-003", {}).get("unpaid_leave_days")), (0, 2))
    eq("Bagas's summary: 1 paid leave day, 1 unpaid leave day", (sums.get("USR-004", {}).get("leave_days"), sums.get("USR-004", {}).get("unpaid_leave_days")), (1, 1))
    eq("Citra's summary has no leave", (sums.get("USR-005", {}).get("leave_days"), sums.get("USR-005", {}).get("unpaid_leave_days")), (0, 0))
    st, resp = citra.verb("leave_request", a_name, "cancel")
    check("cancelling leave inside a summarised period is refused", st == 422 and said(resp, "sudah direkap"), (st, resp))
    st, late = request(ayu, "USR-003", unp, mon + datetime.timedelta(days=7), mon + datetime.timedelta(days=7))
    if st == 201:
        st2, resp = citra.verb("leave_request", rec(late)["name"], "submit")
        check("approving leave inside a summarised period is refused", st2 == 422 and said(resp, "sudah direkap"), (st2, resp))
        ayu.call("DELETE", f"/api/v1/r/leave_request/{rec(late)['name']}")
    # finance opens the run and ticks the fill
    g = H_gen()
    run_body = dict(g.RUN, period_from=str(pf), period_to=str(pt), tax_year=pt.year, tax_month=pt.month, pay_date=str(pt))
    st, run = fm.create("payroll_run", run_body)
    if not check(f"finance opens the internal run {pf}..{pt}", st == 201, (st, run)):
        return
    st, resp = fm.patch("payroll_run", run["name"], {"fill_from_attendance": True})
    check("finance ticks the fill", st == 200, (st, resp))
    slips = {s["employee"]: s for s in fm.find("payslip", f"payroll_run == '{run['name']}'")}
    ayu_s, bagas_s, citra_s = slips.get("USR-003", {}), slips.get("USR-004", {}), slips.get("USR-005", {})
    eq("Ayu's slip: 2 unpaid days, deduction 3.000.000 x 2 / 25 = 240.000, base pay 2.760.000",
       (ayu_s.get("unpaid_days"), ayu_s.get("unpaid_deduction"), ayu_s.get("base_pay")), (2, 240000, 2760000))
    check("Ayu's slip states the basis", bool(re.search(r"Gaji pokok bulanan / 25 x 2\D*hari", ayu_s.get("unpaid_basis", ""))), ayu_s.get("unpaid_basis"))
    eq("Bagas's slip: paid for the paid leave day only (1 x 120.000), the unpaid day listed as 120.000 not paid",
       (bagas_s.get("days"), bagas_s.get("unpaid_days"), bagas_s.get("unpaid_deduction"), bagas_s.get("base_pay")), (1, 1, 120000, 120000))
    check("Bagas's slip states the daily basis", bagas_s.get("unpaid_basis", "").startswith("Upah harian x"), bagas_s.get("unpaid_basis"))
    eq("Citra's slip has no unpaid days and no basis", (citra_s.get("unpaid_days"), citra_s.get("unpaid_deduction"), citra_s.get("unpaid_basis")), (0, 0, ""))
    if ayu_s.get("name"):
        check_print("Ayu's draft payslip", fm, "payslip", ayu_s["name"], "", ["Tidak dibayar 2 hari", "Gaji pokok bulanan / 25"])


def H_gen():
    import day_one_karyawan
    return day_one_karyawan.gen()


def scenario_leave_employee_sees_only_own(api):
    """An employee reads only their own requests and balances; a supervisor reads the requests of their outlet only; head
    office reads all."""
    got = members("hq", "ayu", "bagas", "citra")
    if not got:
        return
    hq, ayu, bagas, citra = got
    mine = bagas.find("leave_request", "name != ''")
    check("Bagas sees his requests and only his", bool(mine) and all(x["employee"] == "USR-004" for x in mine), [(x["name"], x["employee"]) for x in mine])
    st, resp = bagas.call("GET", f"/api/v1/r/leave_request/{S.get('ayu_w2', 'x')}")
    check("Bagas cannot open Ayu's request", st in (403, 404), (st, resp))
    bb = bagas.find("leave_balance", "name != ''")
    check("Bagas sees his own balances only", bool(bb) and all(x["employee"] == "USR-004" for x in bb), [(x["name"], x["employee"]) for x in bb])
    cr = citra.find("leave_request", "name != ''")
    check("Citra (shop) sees the shop's requests (Ayu's) and none of the kitchen's",
          bool(cr) and all(x["outlet"] == "OUT-02" for x in cr) and any(x["employee"] == "USR-003" for x in cr), [(x["employee"], x["outlet"]) for x in cr])
    allr = hq.find("leave_request", "name != ''")
    check("head office sees both outlets", {x["outlet"] for x in allr} >= {"OUT-01", "OUT-02"}, sorted({x["outlet"] for x in allr}))
    st, resp = bagas.create("leave_request", {"employee": "USR-003", "leave_type": ltype(hq, "unpaid"), "date_from": str(S["start"]), "date_to": str(S["start"])})
    check("Bagas cannot request leave for Ayu", st in (403, 422), (st, resp))


def scenario_leave_no_balance_and_doctor_note(api):
    """Less than 12 months of service has no annual balance (UU Ketenagakerjaan), so annual leave is refused with a message;
    sick leave needs the doctor's note attached before it is approved."""
    got = members("hq", "gita")
    if not got or "annual" not in S:
        return
    hq, gita = got
    t = kernel_today()
    st, prof = hq.create("leave_profile", {"employee": "USR-008", "outlet": "OUT-01", "join_date": str(t - datetime.timedelta(days=100))})
    check("head office gives Gita (100 days of service) a leave profile", st == 201, (st, prof))
    grant(api, t)
    eq("Gita has no annual balance this year", len(hq.find("leave_balance", f"employee == 'USR-008' && year == {t.year}")), 0)
    mon = monday_on_or_after(datetime.date(t.year, 1, 5))
    st, bad = request(gita, "USR-008", S["annual"], mon, mon)
    check("annual leave for Gita is refused: no balance yet", st == 422 and said(bad, "belum ada jatah cuti"), (st, bad))
    sick = ltype(hq, "sick")
    st, s1 = request(gita, "USR-008", sick, S["start"] + datetime.timedelta(days=56), S["start"] + datetime.timedelta(days=57))
    s1 = rec(s1)
    if not check("Gita requests 2 sick days (no balance needed)", st == 201 and s1.get("days") == 2, (st, s1)):
        return
    st, resp = hq.verb("leave_request", s1["name"], "submit")
    check("approving sick leave without the doctor's note is refused", st == 422 and said(resp, "surat dokter"), (st, resp))
    jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\x00" * 64 + b"\xff\xd9"
    st, up = gita.upload(f"/api/v1/r/leave_request/{s1['name']}/attachments", {}, "surat-dokter.jpg", jpeg)
    att = (up or {}).get("id", "") if st == 201 else ""
    if check("Gita attaches the doctor's note", bool(att), (st, up)):
        st, resp = gita.patch("leave_request", s1["name"], {"attach": att})
        check("the note is saved on the request", st == 200, (st, resp))
        st, resp = hq.verb("leave_request", s1["name"], "submit")
        check("with the note, head office approves", st == 200 and rec(resp).get("docstatus") == 1, (st, resp))


SCENARIOS = [scenario_leave_request_within_balance, scenario_leave_public_holiday_not_counted, scenario_leave_over_balance_rejected,
             scenario_leave_cancel_returns_days, scenario_leave_concurrent_requests_cannot_overdraw, scenario_leave_unpaid_deducts_on_slip,
             scenario_leave_employee_sees_only_own, scenario_leave_no_balance_and_doctor_note]
