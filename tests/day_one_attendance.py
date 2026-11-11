"""Day-one scenarios of field attendance on demo-manpower (A2, plans/layar-kerja.md §4.1; app jasa_proyek, module projects).

Loaded by tests/day_one.py (bind() hands over its helpers and adds the permission rows): `python3 tests/day_one.py --site
demo-manpower --only attendance`. Workers clock in and out at the client's factory (work_site LOK-001) with a position and a
photo; the kernel decides inside/outside the radius from the geo position (distance_m, contract 0030 §4); a far, late or overtime log waits for the site supervisor
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


def pos(site, north_m=0.0, east_m=0.0, acc=10):
    """A geo value (contract 0030 §4) `north_m` and `east_m` metres from the site's point."""
    lat, lng = near(site, north_m, east_m)
    return {"lat": lat, "lng": lng, "accuracy_m": acc, "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}


def clock_in(cl, worker, assignment, day, north=30.0, east=20.0, **extra):
    body = {"worker": worker, "assignment": assignment, "work_date": str(day), "clock_in": f"{day}T07:58:00+07:00",
            "in_pos": pos(SITE_POINT_OF[assignment], north, east, 12)}
    body.update(extra)
    return cl.create("attendance_log", body)


def clock_out(cl, name, day, north=10.0, east=-15.0, with_photo=True, **extra):
    """Clock-out; with_photo also uploads the clock-in photo first (every log needs one to be submitted)."""
    body = {"clock_out": f"{day}T17:02:00+07:00", "out_pos": pos(SITE_POINT_OF[CTX["att_asg_of"][name]], north, east, 9)}
    if with_photo:
        body["in_photo"] = photo(cl, name)
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
    check("the kernel puts the clock-in inside the 150 m radius and measures 36 m",
          log.get("in_inside") is True and log.get("outside_site") is False and log.get("in_site_m") == 36
          and log.get("project") == CTX["att_project"] and log.get("work_site") == SITE and log.get("worker_name") == "Yudi Contoh", log)
    st, resp = clock_in(w1, W["MP-101"], A["MP-101"], t)
    check("a second clock-in the same day is refused (one log per worker per day)", refused(st, resp, "sudah punya absen"), (st, resp))
    st, resp = clock_in(w1, W["MP-102"], A["MP-102"], t)
    check("a worker cannot clock in for another worker (403)", denied(st, resp), (st, resp))
    st, resp = w1.verb("attendance_log", L1, "submit")
    check("a log without clock-out cannot be submitted", refused(st, resp, "jam keluar"), (st, resp))
    st, resp = clock_out(w1, L1, t, with_photo=False)
    check("worker Yudi clocks out at the factory", st == 200 and rec(resp).get("out_inside") is True, (st, resp))
    st, resp = w1.verb("attendance_log", L1, "submit")
    check("a log inside the radius without a clock-in photo cannot be submitted (audit H2)", refused(st, resp, "foto"), (st, resp))
    p1 = photo(w1, L1)
    CTX["att_L1_photo"] = p1
    st, resp = w1.patch("attendance_log", L1, {"in_photo": p1})
    check("Yudi adds his clock-in photo", st == 200, (st, resp))
    st, resp = w1.patch("attendance_log", L1, {"clock_in": f"{t}T07:30:00+07:00"})
    check("the clock-in time cannot be moved once set (set_once, 422)", refused(st, resp, "tidak bisa diubah"), (st, resp))
    st, resp = w1.patch("attendance_log", L1, {"in_pos": pos(SITE, 0, 0)})
    check("the clock-in position cannot be moved once set (set_once)", refused(st, resp, "tidak bisa diubah"), (st, resp))
    st, resp = w1.patch("attendance_log", L1, {"in_photo": photo(w1, L1)})
    check("the clock-in photo cannot be swapped once set (set_once)", refused(st, resp, "tidak bisa diubah"), (st, resp))
    st, resp = w1.verb("attendance_log", L1, "submit")
    check("inside the radius the log submits at once (no approval)", st == 200 and rec(resp).get("docstatus") == 1, (st, resp))
    CTX["att_L1"] = rec(resp).get("name", L1)
    st, resp = w1.verb("attendance_log", CTX["att_L1"], "cancel")
    check("the worker cannot cancel a submitted day (403)", denied(st, resp), (st, resp))
    # The phone only sends its position; it stands 400 m away and the kernel's own measure decides.
    st, log2 = clock_in(w2, W["MP-102"], A["MP-102"], t, north=400, east=0)
    check("a far clock-in without a reason is refused", refused(st, log2, "alasan"), (st, log2))
    st, log2 = clock_in(w2, W["MP-102"], A["MP-102"], t, north=400, east=0, reason="Antar barang ke gudang depan dulu")
    if not check("worker Zahra clocks in 400 m away with a reason", st == 201, (st, log2)):
        return
    L2 = log2["name"]
    CTX["att_asg_of"][L2] = A["MP-102"]
    check("the kernel measures 400 m: outside the radius",
          log2.get("in_inside") is False and log2.get("outside_site") is True and 395 <= (log2.get("in_site_m") or 0) <= 405, log2)
    eq("worker Yudi lists only his own logs", sorted({x["worker"] for x in w1.list("attendance_log")}), [W["MP-101"]])
    st, resp = w1.call("GET", f"/api/v1/r/attendance_log/{Api.q(L2)}")
    check("worker Yudi cannot open Zahra's log (403)", denied(st, resp), (st, resp))
    st, resp = w1.patch("attendance_log", L2, {"reason": "ubah orang lain"})
    check("worker Yudi cannot change Zahra's log (403)", denied(st, resp), (st, resp))
    st, resp = clock_out(w2, L2, t, with_photo=False)
    check("Zahra clocks out at the factory", st == 200, (st, resp))
    st, resp = w2.verb("attendance_log", L2, "submit")
    check("a log outside the radius needs a clock-in photo", refused(st, resp, "foto"), (st, resp))
    pid = photo(w2, L2)
    check("Zahra uploads her clock-in photo as an attachment of the log (contract 0011)", pid.startswith("att_"), pid)
    st, resp = w2.patch("attendance_log", L2, {"in_photo": "foto.jpg"})
    check("a photo field holds an attachment id, not a file name", refused(st, resp, "Lampiran"), (st, resp))
    st, resp = w2.patch("attendance_log", L2, {"in_photo": CTX.get("att_L1_photo", "att_x")})
    check("another log's photo is refused: a photo must be an attachment of this log (WS6)", refused(st, resp, "bukan milik catatan ini"), (st, resp))
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
    st, c = pa.create("attendance_close", {"project": P, "period_from": str(pf), "period_to": str(pt), "notes": "Klien minta rekap per shift"})
    if not check("project admin opens the field attendance close", st == 201, (st, c)):
        return
    for who, cl in (("worker", w1), ("supervisor", sv1)):
        st, got = cl.call("GET", f"/api/v1/r/attendance_close/{Api.q(c['name'])}")
        check(f"the {who} reads the close (its guards need it) without its notes (audit L7)", st == 200 and "notes" not in rec(got) and rec(got).get("project") == P, (st, got))
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


def owner_log(api, worker, asg, day, ot=0):
    """A log at the factory made and submitted by the owner (who approves its own late entry), with photo."""
    p = pos(SITE, 20, 10)
    body = {"worker": worker, "assignment": asg, "work_date": str(day), "clock_in": f"{day}T08:00:00+07:00", "in_pos": p,
            "clock_out": f"{day}T17:00:00+07:00", "out_pos": p}
    if ot:
        body.update(overtime_hours=ot, reason="Lembur stok opname")
    st, lg = api.create("attendance_log", body)
    if st != 201:
        return st, lg
    api.patch("attendance_log", lg["name"], {"in_photo": photo(api, lg["name"])})
    return api.verb("attendance_log", lg["name"], "submit")


def close(api, project, pf, pt):
    st, c = api.create("attendance_close", {"project": project, "period_from": str(pf), "period_to": str(pt)})
    if st != 201:
        return st, c, ""
    st, resp = api.verb("attendance_close", c["name"], "submit")
    return st, resp, rec(resp).get("name", c["name"]) if st == 200 else c["name"]


def scenario_attendance_close_edges(api):
    """Audit 8 Nov: two assignments of one worker are never paid at one rate (refused, then closed per assignment); a
    cancelled close can be amended; a recap never stacks on another timesheet of the same days (both directions); an
    hourly assignment still gets a per-day recap."""
    t = CTX.get("att_today") or kernel_today()
    D = lambda n: t - datetime.timedelta(days=n)   # noqa: E731
    ws = {}
    for no, nm in (("MP-111", "Bima Contoh"), ("MP-112", "Citra Contoh"), ("MP-113", "Dewa Contoh")):
        st, w = api.create("worker", {"worker_name": nm, "employee_no": no, "role_title": "Operator", "day_rate": 300_000, "cost_day_rate": 200_000,
                                      "overtime_rate": 40_000, "overtime_cost_rate": 30_000, "hour_rate": 50_000, "cost_hour_rate": 30_000})
        if not check(f"owner adds worker {no}", st == 201, (st, w)):
            return
        ws[no] = w["name"]
    st, prj = api.create("project", {"project_name": "Proyek uji rekap absen", "customer": "CUST-0001", "state": "running"})
    if not check("owner opens the project for the close edges", st == 201, (st, prj)):
        return
    P = prj["name"]
    asg = {}
    for key, no, start, end, extra in (("a1", "MP-111", D(10), D(6), {"day_rate": 300_000, "cost_rate": 200_000}),
                                       ("a2", "MP-111", D(5), t + datetime.timedelta(days=30), {"day_rate": 400_000, "cost_rate": 260_000}),
                                       ("c", "MP-112", D(10), t + datetime.timedelta(days=30), {}),
                                       ("h", "MP-113", D(10), t + datetime.timedelta(days=30), {"rate_basis": "hour", "day_rate": 350_000})):
        st, a = api.create("assignment", dict({"project": P, "worker": ws[no], "work_site": SITE, "start_date": str(start), "end_date": str(end)}, **extra))
        if not check(f"assignment {key} of {no}", st == 201, (st, a)):
            return
        asg[key] = a["name"]
    for w, a, d in ((ws["MP-111"], asg["a1"], D(7)), (ws["MP-111"], asg["a2"], D(4)), (ws["MP-111"], asg["a2"], D(3)), (ws["MP-113"], asg["h"], D(7))):
        st, resp = owner_log(api, w, a, d)
        check(f"owner logs {w} on {d}", st == 200, (st, resp))
    st, resp, _ = close(api, P, D(8), D(1))
    check("a close over two assignments of one worker is refused (one slip, one rate)", refused(st, resp, "dua penugasan"), (st, resp))
    st, resp, C1 = close(api, P, D(8), D(6))
    check("closing at the end of the first assignment works", st == 200, (st, resp))
    st, resp, C2 = close(api, P, D(5), D(1))
    check("the next close takes the second assignment", st == 200, (st, resp))
    got = sorted((x["assignment"], x["days"], x["cost_amount"], x["amount"]) for x in api.find("timesheet", f"project == '{P}' && worker == '{ws['MP-111']}'"))
    eq("each assignment is paid and billed at its own rate (1 x 200.000/300.000; 2 x 260.000/400.000)",
       got, sorted([(asg["a1"], 1, 200_000, 300_000), (asg["a2"], 2, 520_000, 800_000)]))
    h = api.find("timesheet", f"project == '{P}' && worker == '{ws['MP-113']}'")
    eq("an hourly assignment gets a per-day recap: the worker's day pay, the assignment's day rate (audit r2, r3: 200.000 pay, 350.000 billed)",
       [(x["rate_basis"], x["days"], x["attendance_close"], x["cost_amount"], x["amount"]) for x in h], [("day", 1, C1, 200_000, 350_000)])
    # Reopen the second close: delete its draft recap, cancel, amend, submit again.
    for x in api.find("timesheet", f"attendance_close == '{C2}'"):
        api.call("DELETE", f"/api/v1/r/timesheet/{Api.q(x['name'])}")
    st, resp = api.verb("attendance_close", C2, "cancel")
    check("with its recap deleted the close cancels", st == 200, (st, resp))
    st, am = api.verb("attendance_close", C2, "amend")
    amn = rec(am).get("name")
    st2, resp2 = api.verb("attendance_close", amn, "submit") if st in (200, 201) and amn else (st, am)
    check("the cancelled close is amended and submitted again (no unique key in the way)", st2 == 200, (st, am, st2, resp2))
    C2b = rec(resp2).get("name", amn)
    eq("the amended close made the recap again", [(x["days"], x["attendance_close"]) for x in api.find("timesheet", f"project == '{P}' && worker == '{ws['MP-111']}' && assignment == '{asg['a2']}'")], [(2, C2b)])
    # A daily timesheet of a day already in a recap is refused.
    st, ts = api.create("timesheet", {"project": P, "worker": ws["MP-111"], "assignment": asg["a2"], "work_date": str(D(3)), "days": 1, "description": "Shift dobel"})
    st2, resp2 = api.verb("timesheet", ts.get("name", "x"), "submit") if st == 201 else (st, ts)
    check("a daily timesheet on a day already in a field recap cannot be submitted", refused(st2, resp2, "timesheet lain"), (st2, resp2))
    # The other way: a daily timesheet first, then a close over it.
    st, ts = api.create("timesheet", {"project": P, "worker": ws["MP-112"], "assignment": asg["c"], "work_date": str(D(9)), "days": 1, "description": "Shift harian"})
    st2, resp2 = api.verb("timesheet", ts.get("name", "x"), "submit") if st == 201 else (st, ts)
    check("Citra has a submitted daily timesheet on day -9", st2 == 200, (st2, resp2))
    st, resp = owner_log(api, ws["MP-112"], asg["c"], D(9))
    check("and a log the same day", st == 200, (st, resp))
    st, resp, _ = close(api, P, D(10), D(9))
    check("a close over a day that already has a daily timesheet is refused", refused(st, resp, "timesheet lain"), (st, resp))
    st, resp = api.verb("attendance_close", C1, "cancel")
    check("a close whose recaps live cannot be cancelled", refused(st, resp, "timesheet"), (st, resp))
    # A recap typed over both closes for Dewa, whose days are in the older close only (audit r2 R2-2).
    st, ts = api.create("timesheet", {"project": P, "worker": ws["MP-113"], "work_date": str(D(8)), "period_to": str(D(1)), "days": 1, "description": "Rekap impor"})
    st2, resp2 = api.verb("timesheet", ts.get("name", "x"), "submit") if st == 201 else (st, ts)
    check("a recap over days already in an older close is refused too", refused(st2, resp2, "timesheet lain"), (st2, resp2))
    # One photo, one day (audit r2 R2-3).
    used = next((x for x in api.find("attendance_log", f"worker == '{ws['MP-112']}' && docstatus == 1")), {})
    p5 = pos(SITE, 5, 5)
    st, resp = api.create("attendance_log", {"worker": ws["MP-112"], "assignment": asg["c"], "work_date": str(t), "clock_in": f"{t}T08:00:00+07:00",
                                             "in_pos": p5, "clock_out": f"{t}T17:00:00+07:00", "out_pos": p5, "in_photo": used.get("in_photo", "")})
    check("a clock-in photo already used by another log is refused (not an attachment of the new log)", bool(used.get("in_photo")) and st == 422 and any(e.get("code") == "attachment" for e in (resp or {}).get("errors", [])), (used, st, resp))
    st, lg = api.create("attendance_log", {"worker": ws["MP-112"], "assignment": asg["c"], "work_date": str(t), "clock_in": f"{t}T08:00:00+07:00",
                                           "in_pos": p5, "clock_out": f"{t}T17:00:00+07:00", "out_pos": p5})
    # The clock-out photo counts too (audit r3 R3-2): another log's photo as this log's clock-out photo, and this log's
    # clock-out photo as a later log's clock-in photo.
    lgn = lg.get("name", "x")
    st, resp = api.patch("attendance_log", lgn, {"in_photo": photo(api, lgn), "out_photo": used.get("in_photo", "")})
    check("another log's clock-in photo as the clock-out photo is refused", refused(st, resp, "bukan milik catatan ini"), (st, resp))
    st, resp = api.patch("attendance_log", lgn, {"in_photo": photo(api, lgn)})
    own_out = photo(api, lgn)
    st, resp = api.patch("attendance_log", lgn, {"out_photo": own_out})
    st2, resp2 = api.verb("attendance_log", lgn, "submit") if st == 200 else (st, resp)
    check("with its own clock-out photo the log submits", st2 == 200, (st, st2, resp2))
    # Clock-in on the paid day (audit L9): another day is refused; 06:00 WIB (stored 23:00Z the day before) is that day.
    base = {"worker": ws["MP-113"], "assignment": asg["h"], "work_date": str(t), "in_pos": p5,
            "clock_out": f"{t}T17:00:00+07:00", "out_pos": p5}
    st, resp = api.create("attendance_log", dict(base, clock_in=f"{D(2)}T08:00:00+07:00"))
    check("a clock-in two days before the log's date is refused", refused(st, resp, "tanggal absen"), (st, resp))
    st, resp = api.create("attendance_log", dict(base, clock_in=f"{t}T17:30:00Z"))
    check("a UTC clock-in past 24:00 WIB of the log's date is refused", refused(st, resp, "tanggal absen"), (st, resp))
    st, lgu = api.create("attendance_log", dict(base, clock_in=f"{t - datetime.timedelta(days=1)}T23:00:00.000+00:00"))
    check("a UTC clock-in written +00:00 with milliseconds (06:00 WIB) is accepted (audit F3)", st == 201, (st, lgu))
    if st == 201:
        api.call("DELETE", f"/api/v1/r/attendance_log/{Api.q(lgu['name'])}")
    st, lg3 = api.create("attendance_log", dict(base, clock_in=f"{t}T06:00:00+07:00"))
    check("a clock-in at 06:00 WIB on the log's date is accepted", st == 201, (st, lg3))
    if st == 201:
        st, resp = api.patch("attendance_log", lg3["name"], {"in_photo": own_out})
        check("an earlier log's clock-out photo as the clock-in photo is refused (not this log's attachment)", refused(st, resp, "bukan milik catatan ini"), (st, resp))
        st, resp = api.patch("attendance_log", lg3["name"], {"in_photo": photo(api, lg3["name"])})
        check("the stored early clock-in (UTC, the day before) still passes on the next save", st == 200, (st, resp))


def scenario_attendance_screen(api):
    """The clock work screen (screens/absen.rk, contract 0030 §2 clock) as the web app drives it: the worker lands on it,
    its record set fills their worker, the site is today's assignment with a geo point; clock-in creates the log with the
    position, attaches the photo, then sets the photo and the time together; clock-out sets the out side and submits.
    The supervisor and the project admin are not offered it."""
    w1, sv1, pa = CTX.get("att_w1"), CTX.get("att_sv1"), CTX.get("pa")
    if not (w1 and sv1):
        fail("attendance screen: members", "no members")
        return
    W, A = CTX["att_workers"], CTX["att_asg"]
    st, m = w1.call("GET", "/api/v1/meta")
    work = (m or {}).get("work") or []
    check("the field worker's home is the clock screen (meta.work: absen, home)", work[:1] == [{"screen": "absen", "home": True}], work)
    sc = ((m or {}).get("screens") or {}).get("absen") or {}
    check("absen is a clock over attendance_log, site = assignment", sc.get("type") == "clock" and (sc.get("clock") or {}).get("site", {}).get("model") == "assignment", sc)
    st, r = w1.call("GET", "/api/v1/screens/absen")
    check("the screen's record set fills the worker's own record (user.name)", st == 200 and ((r or {}).get("sets") or {}).get("record", {}).get("worker") == W["MP-101"], (st, r))
    site = (sc.get("clock") or {}).get("site") or {}
    rows = w1.list("assignment")
    today_rows = [a for a in rows if a.get("start_date", "") <= str(CTX["att_today"]) <= a.get("end_date", "") and a.get("work_site")]
    a1 = today_rows[0] if today_rows else {}
    sp = a1.get(site.get("location", "")) or {}
    check("the site slot reads the assignment's site name, point and radius", a1.get("site_name") and abs(float(sp.get("lat", 0)) - SITE_POINT[SITE][0]) < 1e-9
          and a1.get(site.get("radius_m", "")) == 150, a1)
    for who, cl in (("the site supervisor", sv1), ("the project admin", pa)):
        st, r = cl.call("GET", "/api/v1/screens/absen")
        check(f"{who} is not offered the clock screen (404)", st == 404, (st, r))
    # Aldi (MP-103, warehouse LOK-002) clocks in through the screen: create without time, attach, then photo + time. His
    # assignment is in a project of its own, so the field project's close (next scenario) counts the same days as before.
    t = CTX["att_today"]
    st, prj = pa.create("project", {"project_name": "Gudang layar absen", "customer": "CUST-0001", "state": "running", "billing_model": "time_material"})
    st, a3 = pa.create("assignment", {"project": prj.get("name", "x"), "worker": W["MP-103"], "work_site": SITE2,
                                      "start_date": str(t), "end_date": str(t + datetime.timedelta(days=30))}) if st == 201 else (st, prj)
    if not check("project admin assigns Aldi to the warehouse in a project of its own", st == 201, (st, a3)):
        return
    k, pw = CTX.get("kernel"), secrets.token_urlsafe(18)
    code, out = k.user_add("aldi@contohtenaga.test", "field_worker", CTX["org"], "Aldi Contoh", record=W["MP-103"], password=pw)
    w3 = Api.login(k.base, "aldi@contohtenaga.test", pw) if code == 0 else None
    if not check("add and sign in field worker aldi@contohtenaga.test", w3 is not None, out):
        return
    st, r = w3.call("GET", "/api/v1/screens/absen")
    body = dict(((r or {}).get("sets") or {}).get("record") or {})
    body.update({"assignment": a3["name"], "in_pos": pos(SITE2, 15, 10)})
    st, lg = w3.create("attendance_log", body)
    if not check("the screen's clock-in creates the log with the position and no time yet", st == 201 and lg.get("in_inside") is True and not lg.get("clock_in"), (st, lg)):
        return
    name = lg["name"]
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    st, resp = w3.patch("attendance_log", name, {"in_photo": photo(w3, name), "clock_in": now})
    check("then the photo and the clock-in time are set together", st == 200 and rec(resp).get("clock_in"), (st, resp))
    st, resp = w3.verb("attendance_log", name, "submit")
    check("the open log cannot be submitted before the clock-out", refused(st, resp, "jam keluar"), (st, resp))
    out_now = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=5)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    st, resp = w3.patch("attendance_log", name, {"clock_out": out_now, "out_pos": pos(SITE2, 600, 0), "out_photo": photo(w3, name)})
    check("a clock-out 600 m away needs no reason (the screen asks only at clock-in) but marks the log outside",
          st == 200 and rec(resp).get("out_inside") is False and rec(resp).get("outside_site") is True, (st, resp))
    st, resp = w3.verb("attendance_log", name, "submit")
    pend = [a for a in (resp or {}).get("approvals", []) if a.get("state") == "pending"] if isinstance(resp, dict) else []
    check("so the screen's submit waits for the site supervisor (202, outside_site)", st == 202 and [a.get("policy") for a in pend] == ["outside_site"], (st, resp))
    # Without a position: saved, outside, and the reason is required.
    st, resp = w3.create("attendance_log", dict(body, in_pos=None))
    check("a clock-in without a position needs a reason (counts as outside)", refused(st, resp, "alasan"), (st, resp))


SCENARIOS = [scenario_attendance_clock, scenario_attendance_supervisor, scenario_attendance_screen, scenario_attendance_close, scenario_attendance_close_edges]
