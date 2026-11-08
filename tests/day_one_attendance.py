"""Day-one scenarios of field attendance on demo-manpower (A2, plans/layar-kerja.md §4.1; app jasa_proyek, module projects).

Loaded by tests/day_one.py (bind() hands over its helpers and adds the permission rows): `python3 tests/day_one.py --site
demo-manpower --only attendance`. Workers clock in and out at the client's factory (work_site LOK-001) with a position and a
photo; the kernel decides inside/outside the radius from lat/lng; a far, late or overtime log waits for the site supervisor
(contract 0007), who sees only their own site (row scope work_site from the member's outlets); a worker sees only their own
logs (row scope worker). The project admin closes the period: the submitted logs become monthly recap timesheets, and
payroll makes the payslips from them as from an imported recap. Dates follow today (Asia/Jakarta), as the kernel does.
"""
import datetime

H = {}
PW = {}

SITE, SITE2 = "LOK-001", "LOK-002"
SITE_POINT = {SITE: (-6.3105, 107.2950, 150), SITE2: (-6.4050, 107.4560, 200)}   # examples/fixtures/demo-manpower
M_PER_LAT = 110574.0

_R, _RP = {"read"}, {"read", "print"}
_ALL = {"read", "create", "write", "delete", "submit", "cancel", "amend", "export", "print"}
ATTENDANCE_MATRIX = {
    "work_site": {"project_admin": {"read", "create", "write", "delete", "export", "print"}, "finance_manager": _R, "site_supervisor": _R},
    "attendance_log": {"field_worker": {"read", "create", "write", "submit"}, "site_supervisor": _RP, "project_admin": _ALL,
                       "finance_manager": {"read", "export"}},
    "attendance_close": {"project_admin": _ALL, "finance_manager": _R, "site_supervisor": _R, "field_worker": _R},
    "attendance_guard": {"field_worker": {"create"}, "project_admin": {"create"}},   # never stored
}


def bind(m):
    for k in ("ok", "fail", "check", "eq", "CTX", "Api", "messages", "dashboard", "num", "kernel_today", "denied", "secrets", "approvals_of", "decide"):
        H[k] = getattr(m, k)
    globals().update(H)
    m.MATRIX.update(ATTENDANCE_MATRIX)
    m.MATRIX["assignment"] = dict(m.MATRIX["assignment"], field_worker=_R, site_supervisor=_R)


def refused(st, resp, text):
    return st in (409, 422) and any(text in x for x in messages(resp))


def rec(resp):
    return ((resp or {}).get("record") or resp or {}) if isinstance(resp, dict) else {}


def near(site, north_m=0.0, east_m=0.0):
    """A position `north_m` and `east_m` metres from the site's point (same flat-earth formula as the model)."""
    import math
    lat, lng, _ = SITE_POINT[site]
    return round(lat + north_m / M_PER_LAT, 7), round(lng + east_m / (111320.0 * math.cos(math.radians(lat))), 7)


def photo(cl, name):
    """Upload a photo to the log (contract 0011); returns the attachment id."""
    jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\x00" * 64 + b"\xff\xd9"
    st, resp = cl.upload(f"/api/v1/r/attendance_log/{Api.q(name)}/attachments", {}, "selfie.jpg", jpeg)
    return (resp or {}).get("id", "") if st == 201 else ""


def clock_in(cl, worker, assignment, day, north=30.0, east=20.0, **extra):
    lat, lng = near(SITE_POINT_OF[assignment], north, east)
    body = {"worker": worker, "assignment": assignment, "work_date": str(day), "clock_in": f"{day}T07:58:00+07:00",
            "in_lat": lat, "in_lng": lng, "in_accuracy_m": 12, "in_distance_m": round((north ** 2 + east ** 2) ** 0.5)}
    body.update(extra)
    return cl.create("attendance_log", body)


def clock_out(cl, name, day, north=10.0, east=-15.0, **extra):
    lat, lng = near(SITE_POINT_OF[CTX["att_asg_of"][name]], north, east)
    body = {"clock_out": f"{day}T17:02:00+07:00", "out_lat": lat, "out_lng": lng, "out_accuracy_m": 9,
            "out_distance_m": round((north ** 2 + east ** 2) ** 0.5)}
    body.update(extra)
    return cl.patch("attendance_log", name, body)


SITE_POINT_OF = {}


def setup_attendance(api):
    """Three new workers (not paid elsewhere in the month scenarios), their project and assignments, payroll profiles, and the
    members: two field workers, a supervisor per site. Returns False when a step failed."""
    fm, pa, k = CTX.get("fm"), CTX.get("pa"), CTX.get("kernel")
    if not (fm and pa and k):
        fail("attendance: members", CTX.get("login_error", "members missing"))
        return False
    t = kernel_today()
    CTX["att_today"] = t
    workers = {}
    for no, nm in (("MP-101", "Yudi Contoh"), ("MP-102", "Zahra Contoh"), ("MP-103", "Aldi Contoh")):
        st, w = fm.create("worker", {"worker_name": nm, "employee_no": no, "role_title": "Operator gudang", "day_rate": 300_000,
                                     "cost_day_rate": 220_000, "overtime_rate": 40_000, "overtime_cost_rate": 30_000, "active": True})
        if not check(f"finance adds worker {no} for field attendance", st == 201, (st, w)):
            return False
        workers[no] = w["name"]
        st, p = fm.create("payroll_profile", {"worker": w["name"], "nik": f"999900000001{no[-3:]}0", "has_tax_id": True, "ptkp_status": "tk0",
                                              "pay_basis": "daily", "daily_allowance": 0, "bpjs_tk": True, "bpjs_jp": True, "bpjs_kes": True,
                                              "bank_name": "Bank Contoh", "bank_account": f"00001{no[-3:]}", "active": True})
        check(f"finance adds the payroll profile of {no}", st == 201, (st, p))
    CTX["att_workers"] = workers
    st, prj = pa.create("project", {"project_name": "Alih daya gudang dan pabrik (absen lapangan)", "customer": "CUST-0001", "state": "running",
                                    "billing_model": "time_material"})
    if not check("project admin opens the field project", st == 201, (st, prj)):
        return False
    P = prj["name"]
    CTX["att_project"] = P
    asg = {}
    for no, site in (("MP-101", SITE), ("MP-102", SITE), ("MP-103", SITE2)):
        st, a = pa.create("assignment", {"project": P, "worker": workers[no], "work_site": site, "start_date": str(t - datetime.timedelta(days=40)),
                                         "end_date": str(t + datetime.timedelta(days=60))})
        if not check(f"project admin assigns {no} to {site}", st == 201 and a.get("work_site") == site, (st, a)):
            return False
        asg[no] = a["name"]
        SITE_POINT_OF[a["name"]] = site
    CTX["att_asg"] = asg
    CTX["att_asg_of"] = {}
    pw = secrets.token_urlsafe(18)   # throwaway password of a throwaway database
    for key, email, role, recname, name, outlets in [
            ("w1", "yudi@contohtenaga.test", "field_worker", workers["MP-101"], "Yudi Contoh", ""),
            ("w2", "zahra@contohtenaga.test", "field_worker", workers["MP-102"], "Zahra Contoh", ""),
            ("sv1", "sinta@contohtenaga.test", "site_supervisor", "USR-003", "Sinta Contoh", SITE),
            ("sv2", "taufik@contohtenaga.test", "site_supervisor", "USR-004", "Taufik Contoh", SITE2)]:
        code, out = k.user_add(email, role, CTX["org"], name, outlets=outlets, record=recname, password=pw)
        cl = Api.login(k.base, email, pw) if code == 0 else None
        if not check(f"add and sign in {role} {email}", cl is not None, out):
            return False
        CTX["att_" + key] = cl
    return True


def scenario_attendance_clock(api):
    """Clock in and out inside the radius submits at once; the kernel measures the distance itself; a worker sees and writes
    only their own logs; one log per worker per day; pay rates of the assignment stay hidden from the worker."""
    if not setup_attendance(api):
        return
    t = CTX["att_today"]
    w1, w2, sv1 = CTX["att_w1"], CTX["att_w2"], CTX["att_sv1"]
    W, A = CTX["att_workers"], CTX["att_asg"]
    # The worker reads their own assignment with the site's point (for the phone's message), never the rates.
    mine = w1.list("assignment")
    eq("the worker lists only their own assignment", [a["worker"] for a in mine], [W["MP-101"]])
    a1 = mine[0] if mine else {}
    check("the worker's assignment shows the site point and radius but no rate or pay",
          a1.get("site_radius_m") == 150 and abs((a1.get("site_lat") or 0) - SITE_POINT[SITE][0]) < 1e-9
          and not any(f in a1 for f in ("day_rate", "cost_rate", "overtime_rate", "overtime_cost_rate", "hour_rate")), a1)
    st, resp = w1.call("GET", "/api/v1/r/work_site")
    check("the worker cannot list the firm's client sites (403)", denied(st, resp), (st, resp))
    st, resp = w1.call("GET", f"/api/v1/r/worker/{W['MP-101']}")
    check("the worker cannot read worker records, which carry pay (403)", st == 403, (st, resp))
    # Clock-in 36 m from the point: inside.
    st, log = clock_in(w1, W["MP-101"], A["MP-101"], t, north=30, east=20)
    if not check("worker Yudi clocks in 36 m from the factory point", st == 201, (st, log)):
        return
    L1 = log["name"]
    CTX["att_asg_of"][L1] = A["MP-101"]
    check("the kernel puts the clock-in inside the 150 m radius and measures about 36 m",
          log.get("in_inside") is True and log.get("outside_site") is False and 33 <= (log.get("in_site_m") or 0) <= 39
          and log.get("project") == CTX["att_project"] and log.get("work_site") == SITE and log.get("worker_name") == "Yudi Contoh", log)
    st, resp = clock_in(w1, W["MP-101"], A["MP-101"], t)
    check("a second clock-in the same day is refused (one log per worker per day)", refused(st, resp, "sudah punya absen"), (st, resp))
    st, resp = clock_in(w1, W["MP-102"], A["MP-102"], t)
    check("a worker cannot clock in for another worker (403)", denied(st, resp), (st, resp))
    st, resp = w1.verb("attendance_log", L1, "submit")
    check("a log without clock-out cannot be submitted", refused(st, resp, "jam keluar"), (st, resp))
    st, resp = clock_out(w1, L1, t)
    check("worker Yudi clocks out at the factory", st == 200 and rec(resp).get("out_inside") is True, (st, resp))
    st, resp = w1.verb("attendance_log", L1, "submit")
    check("inside the radius the log submits at once (no approval)", st == 200 and rec(resp).get("docstatus") == 1, (st, resp))
    CTX["att_L1"] = rec(resp).get("name", L1)
    st, resp = w1.verb("attendance_log", CTX["att_L1"], "cancel")
    check("the worker cannot cancel a submitted day (403)", denied(st, resp), (st, resp))
    # The client sends a distance of 5 m but stands 400 m away: the kernel's own measure decides.
    st, log2 = clock_in(w2, W["MP-102"], A["MP-102"], t, north=400, east=0, in_distance_m=5)
    check("a far clock-in without a reason is refused", refused(st, log2, "alasan"), (st, log2))
    st, log2 = clock_in(w2, W["MP-102"], A["MP-102"], t, north=400, east=0, in_distance_m=5, reason="Antar barang ke gudang depan dulu")
    if not check("worker Zahra clocks in 400 m away (phone claims 5 m) with a reason", st == 201, (st, log2)):
        return
    L2 = log2["name"]
    CTX["att_asg_of"][L2] = A["MP-102"]
    check("the kernel ignores the phone's 5 m: outside the radius, about 384-400 m",
          log2.get("in_inside") is False and log2.get("outside_site") is True and 380 <= (log2.get("in_site_m") or 0) <= 420, log2)
    eq("worker Yudi lists only his own logs", sorted({x["worker"] for x in w1.list("attendance_log")}), [W["MP-101"]])
    st, resp = w1.call("GET", f"/api/v1/r/attendance_log/{Api.q(L2)}")
    check("worker Yudi cannot open Zahra's log (403)", denied(st, resp), (st, resp))
    st, resp = w1.patch("attendance_log", L2, {"reason": "ubah orang lain"})
    check("worker Yudi cannot change Zahra's log (403)", denied(st, resp), (st, resp))
    st, resp = clock_out(w2, L2, t)
    check("Zahra clocks out at the factory", st == 200, (st, resp))
    st, resp = w2.verb("attendance_log", L2, "submit")
    check("a log outside the radius needs a clock-in photo", refused(st, resp, "foto"), (st, resp))
    pid = photo(w2, L2)
    check("Zahra uploads her clock-in photo as an attachment of the log (contract 0011)", pid.startswith("att_"), pid)
    st, resp = w2.patch("attendance_log", L2, {"in_photo": "foto.jpg"})
    check("a photo field holds an attachment id, not a file name", refused(st, resp, "lampiran"), (st, resp))
    st, resp = w2.patch("attendance_log", L2, {"in_photo": pid})
    check("the photo is set on the log", st == 200, (st, resp))
    st, resp = w2.verb("attendance_log", L2, "submit")
    pend = [a for a in (resp or {}).get("approvals", []) if a.get("state") == "pending"] if isinstance(resp, dict) else []
    check("the far log waits for the site supervisor (202, policy outside_site)", st == 202 and [a.get("policy") for a in pend] == ["outside_site"], (st, resp))
    CTX["att_L2"] = L2
    CTX["att_L2_req"] = pend[0]["id"] if pend else ""
    st, resp = sv1.create("attendance_log", {"worker": W["MP-102"], "assignment": A["MP-102"], "work_date": str(t)})
    check("a supervisor cannot write logs (403)", denied(st, resp), (st, resp))


def scenario_attendance_supervisor(api):
    """The supervisor of the factory sees only the factory's logs and decides the held ones; the warehouse's supervisor sees
    and decides nothing of the factory; a late log and an overtime log wait too; a rejected log stays a draft."""
    sv1, sv2, w1, w2 = CTX.get("att_sv1"), CTX.get("att_sv2"), CTX.get("att_w1"), CTX.get("att_w2")
    L2, req = CTX.get("att_L2"), CTX.get("att_L2_req")
    if not (sv1 and L2 and req):
        fail("attendance supervisor: clock scenario", "no held log")
        return
    t = CTX["att_today"]
    W, A = CTX["att_workers"], CTX["att_asg"]
    eq("the factory supervisor lists the factory's logs only", sorted({x["work_site"] for x in sv1.list("attendance_log")}), [SITE])
    eq("the factory supervisor reads only the factory site", [s["name"] for s in sv1.list("work_site")], [SITE])
    eq("the warehouse supervisor lists no factory log", [x["name"] for x in sv2.list("attendance_log") if x.get("work_site") == SITE], [])
    st, resp = sv2.call("GET", f"/api/v1/r/attendance_log/{Api.q(L2)}")
    check("the warehouse supervisor cannot open a factory log (403)", denied(st, resp), (st, resp))
    st, resp = decide(sv2, req)
    check("the warehouse supervisor cannot approve a factory log (403)", st == 403, (st, resp))
    st, resp = decide(w1, req)
    check("a worker cannot approve (403)", st == 403, (st, resp))
    pend = [a for a in approvals_of(sv1, "attendance_log", L2) if a.get("state") == "pending"]
    eq("the factory supervisor sees the held log in Persetujuan", [a.get("id") for a in pend], [req])
    lg = sv1.get("attendance_log", L2)
    check("the supervisor reads the reason, the measured distance and the photo id", lg.get("reason") and (lg.get("in_site_m") or 0) > 150 and lg.get("in_photo", "").startswith("att_"), lg)
    st, resp = decide(sv1, req)
    check("the factory supervisor approves: the log is submitted as Zahra", st == 200, (st, resp))
    sub = sv1.find("attendance_log", f"worker == '{W['MP-102']}' && work_date == '{t}'")
    check("Zahra's day is now counted (docstatus 1)", [x["docstatus"] for x in sub] == [1], sub)
    # Yesterday, inside, with 2 h overtime: held for overtime; approved.
    y = t - datetime.timedelta(days=1)
    st, lo = clock_in(w2, W["MP-102"], A["MP-102"], y, overtime_hours=2, reason="Lembur bongkar muat kontainer")
    if not check("Zahra logs yesterday with 2 h overtime", st == 201, (st, lo)):
        return
    CTX["att_asg_of"][lo["name"]] = A["MP-102"]
    clock_out(w2, lo["name"], y)
    st, resp = w2.verb("attendance_log", lo["name"], "submit")
    pend = [a for a in (resp or {}).get("approvals", []) if a.get("state") == "pending"] if isinstance(resp, dict) else []
    check("overtime waits for the supervisor (policy overtime)", st == 202 and [a.get("policy") for a in pend] == ["overtime"], (st, resp))
    st, resp = decide(sv1, pend[0]["id"]) if pend else (0, None)
    check("the supervisor approves the overtime", st == 200, (st, resp))
    # Yudi yesterday, inside: one day late is fine, submits at once.
    st, ly = clock_in(w1, W["MP-101"], A["MP-101"], y)
    CTX["att_asg_of"][(ly or {}).get("name", "")] = A["MP-101"]
    clock_out(w1, (ly or {}).get("name", "x"), y)
    st, resp = w1.verb("attendance_log", (ly or {}).get("name", "x"), "submit")
    check("a log of yesterday inside the radius submits at once", st == 200, (st, resp))
    # Yudi three days ago: entered late, held; the supervisor rejects; the log stays a draft and is not counted.
    d3 = t - datetime.timedelta(days=3)
    st, l3 = clock_in(w1, W["MP-101"], A["MP-101"], d3)
    CTX["att_asg_of"][(l3 or {}).get("name", "")] = A["MP-101"]
    clock_out(w1, (l3 or {}).get("name", "x"), d3)
    st, resp = w1.verb("attendance_log", (l3 or {}).get("name", "x"), "submit")
    pend = [a for a in (resp or {}).get("approvals", []) if a.get("state") == "pending"] if isinstance(resp, dict) else []
    check("a log entered three days late waits (policy late_entry)", st == 202 and [a.get("policy") for a in pend] == ["late_entry"], (st, resp))
    st, resp = decide(sv1, pend[0]["id"], "reject", "Tidak ada di lokasi hari itu") if pend else (0, None)
    check("the supervisor rejects the late log", st == 200, (st, resp))
    eq("the rejected log stays a draft", sv1.get("attendance_log", (l3 or {}).get("name", "x")).get("docstatus"), 0)
    CTX["att_L3"] = (l3 or {}).get("name", "")
    st, resp = clock_in(w1, W["MP-101"], A["MP-101"], t + datetime.timedelta(days=1))
    check("a clock-in for tomorrow is refused", refused(st, resp, "belum datang"), (st, resp))
    ws = dashboard(w1, "absen_saya", 31)
    eq("absen_saya: Yudi has 2 days present and 1 waiting (the rejected draft)", (num(ws.get("days_present", {})), num(ws.get("waiting", {}))), (2, 1))
    st, resp = w1.call("GET", "/api/v1/dashboards/rekap_absen")
    check("a worker cannot open the supervisor's board (403)", st == 403, (st, resp))
    ws = dashboard(sv1, "rekap_absen", 31)
    eq("rekap_absen for the factory supervisor: 4 days present (2 Yudi, 2 Zahra), 2 logs today",
       (num(ws.get("days_present", {})), num(ws.get("in_today", {}))), (4, 2))
    ws = dashboard(sv2, "rekap_absen", 31)
    eq("rekap_absen for the warehouse supervisor counts none of the factory's days", num(ws.get("days_present", {})), 0)


def scenario_attendance_close(api):
    """The project admin closes the period: one draft recap timesheet per worker with the counted days and overtime; payroll
    makes the slips from them; days of a closed period cannot be added or cancelled; the close cannot be cancelled while
    its recaps live; overlapping closes are refused."""
    pa, fm, w1, sv1 = CTX.get("pa"), CTX.get("fm"), CTX.get("att_w1"), CTX.get("att_sv1")
    P = CTX.get("att_project")
    if not (pa and P and CTX.get("att_L1")):
        fail("attendance close: earlier scenarios", "missing")
        return
    t = CTX["att_today"]
    W, A = CTX["att_workers"], CTX["att_asg"]
    pf, pt = t - datetime.timedelta(days=3), t
    # A payroll run of the project whose period holds the recap's start (period_from) takes the slips.
    rt = pf + datetime.timedelta(days=27)
    st, run = fm.create("payroll_run", {"project": P, "period_from": str(pf), "period_to": str(rt), "tax_year": rt.year, "tax_month": rt.month,
                                        "pay_date": str(rt), "umk": 5_600_000, "jkk_rate": 0.54, "jp_wage_cap": 11_086_300, "kes_wage_cap": 12_000_000,
                                        "workdays_per_week": 6, "billing_mode": "wages_plus_fee", "fee_mode": "percent", "fee_percent": 10,
                                        "wage_item": "BRG-0001", "bpjs_item": "BRG-0002", "fee_item": "BRG-0003", "invoice_date": str(rt), "pph23_applies": True})
    if not check("finance opens the payroll run of the field project", st == 201, (st, run)):
        return
    st, resp = pa.create("attendance_close", {"project": P, "period_from": str(pf), "period_to": str(t + datetime.timedelta(days=1))})
    st2, resp2 = pa.verb("attendance_close", rec(resp).get("name", "x"), "submit") if st == 201 else (st, resp)
    check("a period that has not ended cannot be closed", refused(st2, resp2, "belum selesai"), (st2, resp2))
    if st == 201:
        pa.call("DELETE", f"/api/v1/r/attendance_close/{Api.q(resp['name'])}")
    st, c = pa.create("attendance_close", {"project": P, "period_from": str(pf), "period_to": str(pt)})
    if not check("project admin opens the field attendance close", st == 201, (st, c)):
        return
    st, resp = pa.verb("attendance_close", c["name"], "submit")
    if not check("project admin submits the close", st == 200, (st, resp)):
        return
    C = rec(resp).get("name", c["name"])
    ts = pa.find("timesheet", f"attendance_close == '{C}'")
    got = sorted((x["worker"], x["days"], x["overtime_hours"], x["work_date"], x["period_to"], x["docstatus"]) for x in ts)
    eq("one draft recap per worker with logs: Yudi 2 days, Zahra 2 days + 2 h; Aldi (no logs) none",
       got, sorted([(W["MP-101"], 2, 0, str(pf), str(pt), 0), (W["MP-102"], 2, 2, str(pf), str(pt), 0)]))
    by_w = {x["worker"]: x for x in ts}
    eq("the recap takes the assignment's pay (cost 2 x 220.000; Zahra + 2 x 30.000 overtime)",
       ((by_w.get(W["MP-101"]) or {}).get("cost_amount"), (by_w.get(W["MP-102"]) or {}).get("cost_amount")), (440_000, 500_000))
    st, resp = pa.verb("attendance_close", C, "cancel")
    check("the close cannot be cancelled while its recaps live", refused(st, resp, "timesheet"), (st, resp))
    bad = [(x["name"], s, r) for x in ts for s, r in [pa.verb("timesheet", x["name"], "submit")] if s != 200]
    check("project admin submits the two recaps", not bad, bad)
    slips = fm.find("payslip", f"payroll_run == '{run['name']}'")
    eq("payroll made one draft slip per recap with its days and overtime",
       sorted((s["worker"], s["days"], s["overtime_hours"]) for s in slips), sorted([(W["MP-101"], 2, 0), (W["MP-102"], 2, 2)]))
    s2 = next((s for s in slips if s["worker"] == W["MP-102"]), {})
    eq("Zahra's slip pays 2 days x 220.000 + 2 h x 30.000", (s2.get("base_pay"), s2.get("overtime_pay")), (440_000, 60_000))
    # Days of the closed period are frozen.
    st, resp = pa.verb("attendance_log", CTX["att_L1"], "cancel")
    check("a counted day of a closed period cannot be cancelled", refused(st, resp, "rekap"), (st, resp))
    L3 = CTX.get("att_L3")
    pa.patch("attendance_log", L3, {"reason": "Diajukan ulang setelah rekap"})
    st, resp = w1.verb("attendance_log", L3, "submit")
    pend = [a for a in (resp or {}).get("approvals", []) if a.get("state") == "pending"] if isinstance(resp, dict) else []
    st2, resp2 = decide(sv1, pend[0]["id"]) if pend else (0, None)
    check("a late day of a closed period cannot be added, even approved", st == 202 and refused(st2, resp2, "rekap"), (st, resp, st2, resp2))
    st, o = pa.create("attendance_close", {"project": P, "period_from": str(pf + datetime.timedelta(days=1)), "period_to": str(pt)})
    st2, resp2 = pa.verb("attendance_close", o.get("name", "x"), "submit") if st == 201 else (st, o)
    check("an overlapping close of the same project is refused", refused(st2, resp2, "tumpang tindih"), (st2, resp2))
    st, resp = w1.create("attendance_close", {"project": P, "period_from": str(pf), "period_to": str(pt)})
    check("a worker cannot close attendance (403)", denied(st, resp), (st, resp))


SCENARIOS = [scenario_attendance_clock, scenario_attendance_supervisor, scenario_attendance_close]
