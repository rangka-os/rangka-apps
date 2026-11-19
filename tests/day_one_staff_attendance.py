"""Day-one scenarios of outlet staff attendance on demo-fnb (module attendance, app piece absen_karyawan, rangka-apps#100).

Loaded by tests/day_one.py (bind() hands over its helpers and adds the permission rows): `python3 tests/day_one.py --site
demo-fnb --only staff_attendance`. Staff clock in and out at their outlet with a position and a selfie; the kernel decides
inside or outside the outlet radius (distance_m, contract 0030 §4). A far, position-less, late-entered or overtime log
waits for the outlet supervisor of that outlet (row scope outlet) or head office (contract 0007). Staff read only their
own logs (permission if:). Lateness is measured against the outlet's default hours in its own time zone. Head office
closes a period: one summary per active employee. Dates follow today (Asia/Jakarta), as the kernel does.
"""
import datetime
import math

H = {}

HO, DAGO, BRAGA, CIMAHI = "OUT-01", "OUT-02", "OUT-03", "OUT-04"
POINT = {HO: (-6.9147, 107.6098), DAGO: (-6.8850, 107.6136), BRAGA: (-6.9175, 107.6091), CIMAHI: (-6.8722, 107.5425)}   # fixtures demo-fnb

_R, _RP = {"read"}, {"read", "print"}
ATTENDANCE_MATRIX = {
    # outlet_staff (Yusuf): own logs only (if:), no cancel or delete; head office all but delete
    "staff_attendance": {"outlet_staff": {"read", "create", "write", "submit"},
                         "head_office": {"read", "create", "write", "submit", "cancel", "amend", "export", "print"}},
    "staff_attendance_guard": {"outlet_staff": {"create"}, "head_office": {"create"}},   # never stored
    "attendance_period": {"head_office": {"read", "create", "write", "delete", "submit", "cancel", "amend", "export", "print"}, "outlet_staff": _R},
    "attendance_summary": {"head_office": {"read", "create", "write", "export", "print"}, "outlet_staff": _R},
}


def bind(m):
    for k in ("ok", "fail", "check", "eq", "CTX", "Api", "messages", "dashboard", "num", "kernel_today", "denied", "secrets", "approvals_of", "decide"):
        H[k] = getattr(m, k)
    globals().update(H)
    m.MATRIX.update(ATTENDANCE_MATRIX)
    prev = m.SITE_SETUP.get("demo-fnb")

    def chained(api, k):
        if prev:
            prev(api, k)
        setup(api, k)
    m.SITE_SETUP["demo-fnb"] = chained


def refused(st, resp, text):
    return st in (409, 422) and any(text in x for x in messages(resp))


def rec(resp):
    return ((resp or {}).get("record") or resp or {}) if isinstance(resp, dict) else {}


def pos(outlet, north_m=0.0, east_m=0.0, acc=10):
    """A geo value (contract 0030 §4) `north_m` and `east_m` metres from the outlet's point."""
    lat, lng = POINT[outlet]
    return {"lat": round(lat + north_m / 110574.0, 7), "lng": round(lng + east_m / (111320.0 * math.cos(math.radians(lat))), 7),
            "accuracy_m": acc, "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}


def photo(cl, name):
    """Upload a selfie to the log (contract 0011); returns the attachment id."""
    jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\x00" * 64 + b"\xff\xd9"
    st, resp = cl.upload(f"/api/v1/r/staff_attendance/{Api.q(name)}/attachments", {}, "selfie.jpg", jpeg)
    return (resp or {}).get("id", "") if st == 201 else ""


def me(cl):
    """The member's employee record (user.name, contract 0056) as /meta reports it."""
    st, m = cl.call("GET", "/api/v1/meta")
    return ((m or {}).get("user") or {}).get("name", "")


MEMBERS = [   # key, email, role, outlets, name
    ("sa_cashier", "ayu.dago@tahucontoh.test", "cashier", DAGO, "Ayu Contoh"),
    ("sa_kitchen", "dimas.dago@tahucontoh.test", "employee", DAGO, "Dimas Contoh"),
    ("sa_sup_dago", "bayu.dago@tahucontoh.test", "outlet_supervisor", DAGO, "Bayu Contoh"),
    ("sa_sup_braga", "citra.braga@tahucontoh.test", "outlet_supervisor", BRAGA, "Citra Contoh"),
]


def setup(api, k):
    pw = secrets.token_urlsafe(18)   # throwaway password of a throwaway database
    for key, email, role, outlets, name in MEMBERS:
        code, out = k.user_add(email, role, CTX.get("org") or "tahu_contoh", name, outlets=outlets, password=pw)
        cl = Api.login(k.base, email, pw) if code == 0 else None
        if cl is None:
            CTX["sa_error"] = f"{email}: " + (out if code else "login refused")
            return
        CTX[key] = cl
        CTX[key + "_rec"] = me(cl)


def members(*keys):
    got = [CTX.get(k) for k in keys]
    if not all(got):
        fail("staff attendance: members signed in", CTX.get("sa_error") or CTX.get("login_error") or [k for k in keys if not CTX.get(k)])
        return None
    return got


def local(day, hhmm, offset="+07:00"):
    return f"{day}T{hhmm}:00{offset}"


def full_day(cl, day, outlet, north=40.0, clock_in="08:05", clock_out="17:02", offset="+07:00", **extra):
    """Create a log, attach the selfie, set clock-in; then clock-out with a new photo. Returns (status, name)."""
    body = {"outlet": outlet, "work_date": str(day), "in_pos": pos(outlet, north, 0, 12)}
    body.update(extra)
    st, lg = cl.create("staff_attendance", body)
    if st != 201:
        return st, lg
    name = lg["name"]
    st, resp = cl.patch("staff_attendance", name, {"in_photo": photo(cl, name), "clock_in": local(day, clock_in, offset)})
    if st != 200:
        return st, resp
    st, resp = cl.patch("staff_attendance", name, {"clock_out": local(day, clock_out, offset), "out_pos": pos(outlet, north / 2, 0, 9), "out_photo": photo(cl, name)})
    return (st, name) if st == 200 else (st, resp)


def scenario_staff_attendance_clock(api):
    """Ayu (cashier, Dago) clocks in 40 m from the outlet with a selfie: inside, submitted at clock-out without anyone
    deciding. A second clock-in the same day is refused; she cannot clock in for someone else or at Braga; Yusuf (outlet
    staff, Dago) cannot read her log."""
    got = members("sa_cashier", "staff_dago", "hq")
    if not got:
        return
    ayu, yusuf, hq = got
    t = kernel_today()
    CTX["sa_today"] = t
    ayu_rec = CTX["sa_cashier_rec"]
    check("Ayu has an employee record (contract 0056)", ayu_rec.startswith("USR-"), ayu_rec)
    st, lg = ayu.create("staff_attendance", {"in_pos": pos(DAGO, 40, 0)})
    if not check("Ayu's clock-in creates a log (employee and outlet from her membership)", st == 201, (st, lg)):
        return
    eq("the log's employee and outlet default to Ayu and Dago", (lg.get("employee"), lg.get("outlet"), lg.get("work_date")), (ayu_rec, DAGO, str(t)))
    check("40 m from Dago is inside the 100 m radius", lg.get("in_inside") is True and 35 <= int(lg.get("in_site_m") or 0) <= 45, lg)
    name = lg["name"]
    st, resp = ayu.verb("staff_attendance", name, "submit")
    check("a log without a clock-in time cannot be submitted", refused(st, resp, "jam masuk"), (st, resp))
    pid = photo(ayu, name)
    st, resp = ayu.patch("staff_attendance", name, {"in_photo": pid, "clock_in": local(t, "08:05")})
    check("the selfie and the clock-in time are set together", st == 200 and rec(resp).get("clock_in"), (st, resp))
    st, resp = ayu.patch("staff_attendance", name, {"clock_in": local(t, "07:00")})
    check("a clock-in time cannot be moved (set_once)", st in (403, 409, 422), (st, resp))
    st, resp = ayu.patch("staff_attendance", name, {"clock_out": local(t, "17:02"), "out_pos": pos(DAGO, 20, 0), "out_photo": pid})
    check("the clock-in selfie cannot be the clock-out photo", refused(st, resp, "Foto keluar"), (st, resp))
    st, resp = ayu.patch("staff_attendance", name, {"clock_out": local(t, "17:02"), "out_pos": pos(DAGO, 20, 0), "out_photo": photo(ayu, name)})
    check("Ayu clocks out inside", st == 200 and rec(resp).get("outside_site") is False, (st, resp))
    eq("worked minutes 08:05 to 17:02", rec(resp).get("worked_min"), 537)
    st, resp = ayu.verb("staff_attendance", name, "submit")
    check("inside the radius the submit goes straight through (200, docstatus 1)", st == 200 and rec(resp).get("docstatus") == 1, (st, resp))
    name = rec(resp).get("name", name)   # a draft is numbered at submit
    CTX["sa_ayu_today"] = name
    # a second clock-in the same day
    st, resp = ayu.create("staff_attendance", {"in_pos": pos(DAGO, 10, 0), "clock_in": local(t, "12:00")})
    check("a second clock-in the same day is refused (one log per person per day)", refused(st, resp, "sudah punya absen"), (st, resp))
    # someone else, another outlet
    st, resp = ayu.create("staff_attendance", {"employee": "USR-002", "in_pos": pos(DAGO, 10, 0)})
    check("Ayu cannot make a log for Yusuf (403 or rule employee_self)", denied(st, resp) or refused(st, resp, "diri sendiri"), (st, resp))
    st, resp = ayu.create("staff_attendance", {"outlet": BRAGA, "work_date": str(t - datetime.timedelta(days=1)), "in_pos": pos(BRAGA, 10, 0)})
    check("Ayu cannot clock in at Braga, not her outlet", st in (403, 422), (st, resp))
    # privacy: colleagues of the same outlet do not read each other's logs (selfies, positions)
    eq("Ayu lists only her own logs", sorted({x["employee"] for x in ayu.list("staff_attendance")}), [ayu_rec])
    st, resp = yusuf.call("GET", f"/api/v1/r/staff_attendance/{Api.q(name)}")
    check("Yusuf (outlet staff at the same outlet) cannot open Ayu's log", st in (403, 404), (st, resp))
    st, resp = yusuf.call("GET", f"/api/v1/r/staff_attendance/{Api.q(name)}/attachments")
    check("Yusuf cannot list the selfies of Ayu's log", st in (403, 404), (st, resp))
    check("head office reads Ayu's log with its selfie", (hq.get("staff_attendance", name) or {}).get("in_photo", "").startswith("att_"), name)


def scenario_staff_attendance_held(api):
    """Dimas (employee, Dago kitchen) clocks in 600 m away: a reason is needed, the submit waits for the Dago supervisor.
    The Braga supervisor neither sees nor decides it; the Dago supervisor approves. Yusuf without a position: held too."""
    got = members("sa_kitchen", "sa_sup_dago", "sa_sup_braga", "staff_dago", "sa_cashier")
    if not got:
        return
    dimas, bayu, citra, yusuf, ayu = got
    t = CTX.get("sa_today") or kernel_today()
    st, resp = dimas.create("staff_attendance", {"in_pos": pos(DAGO, 600, 0)})
    check("600 m away without a reason is refused (422)", refused(st, resp, "tulis alasannya"), (st, resp))
    st, name = full_day(dimas, t, DAGO, north=600, reason="Belanja bahan ke pasar dulu")
    if not check("Dimas clocks in 600 m away with a reason, and out", st == 200, (st, name)):
        return
    lg = dimas.get("staff_attendance", name)
    check("the log is outside the radius", lg.get("outside_site") is True and int(lg.get("in_site_m") or 0) > 550, lg)
    st, resp = dimas.verb("staff_attendance", name, "submit")
    pend = [a for a in (resp or {}).get("approvals", []) if a.get("state") == "pending"] if isinstance(resp, dict) else []
    if not check("the far log waits for approval (202, policy outside_site)", st == 202 and [a.get("policy") for a in pend] == ["outside_site"], (st, resp)):
        return
    req = pend[0]["id"]
    eq("the Braga supervisor lists no Dago log", [x["name"] for x in citra.list("staff_attendance") if x.get("outlet") == DAGO], [])
    st, resp = citra.call("GET", f"/api/v1/r/staff_attendance/{Api.q(name)}")
    check("the Braga supervisor cannot open the Dago log", st in (403, 404), (st, resp))
    st, resp = decide(citra, req)
    check("the Braga supervisor cannot approve the Dago log (403)", st == 403, (st, resp))
    st, resp = decide(ayu, req)
    check("a cashier cannot approve (403)", st == 403, (st, resp))
    pend = [a for a in approvals_of(bayu, "staff_attendance", name) if a.get("state") == "pending"]
    eq("the Dago supervisor sees the held log", [a.get("id") for a in pend], [req])
    lg = bayu.get("staff_attendance", name)
    check("the Dago supervisor reads the reason, the distance and the selfie", lg.get("reason") and int(lg.get("in_site_m") or 0) > 550 and lg.get("in_photo", "").startswith("att_"), lg)
    st, resp = decide(bayu, req)
    check("the Dago supervisor approves: the log is submitted as Dimas", st == 200 and rec(resp).get("docstatus") == 1 and rec(resp).get("employee") == CTX["sa_kitchen_rec"], (st, resp))
    st, resp = bayu.create("staff_attendance", {"employee": CTX["sa_kitchen_rec"], "in_pos": pos(DAGO, 5, 0)})
    check("a supervisor cannot write a log for someone else", denied(st, resp) or refused(st, resp, "diri sendiri"), (st, resp))
    # no position: the phone refused location
    st, resp = yusuf.create("staff_attendance", {"work_date": str(t)})
    check("no position and no reason is refused", refused(st, resp, "lokasi tidak terbaca"), (st, resp))
    st, lg = yusuf.create("staff_attendance", {"work_date": str(t), "reason": "GPS HP tidak mau menyala"})
    check("no position with a reason: saved, outside, distance empty (not 0 m)", st == 201 and lg.get("in_located") is False and lg.get("outside_site") is True
          and lg.get("in_site_m") in (None, ""), (st, lg))
    if st == 201:
        n = lg["name"]
        yusuf.patch("staff_attendance", n, {"in_photo": photo(yusuf, n), "clock_in": local(t, "08:00")})
        yusuf.patch("staff_attendance", n, {"clock_out": local(t, "17:00"), "out_photo": photo(yusuf, n)})
        st, resp = yusuf.verb("staff_attendance", n, "submit")
        check("the position-less log is held too (202)", st == 202, (st, resp))


def scenario_staff_attendance_late(api):
    """Lateness against the outlet's default hours: Dago 08:00 WIB with 10 minutes grace; 08:25+07:00 and the same instant
    in UTC are 15 minutes late. Cimahi in WITA: 08:25+08:00 is 15, 08:25+07:00 (09:25 WITA) is 75. Head office makes the
    logs (manual source, no selfie): its own employee record on past days."""
    got = members("hq", "sa_cashier")
    if not got:
        return
    hq, ayu = got
    t = CTX.get("sa_today") or kernel_today()
    rina = me(hq)
    if not check("Rina (head office) has an employee record", rina.startswith("USR-"), rina):
        return
    st, resp = api.patch("outlet", CIMAHI, {"time_zone": "wita", "work_start": "08:00", "work_end": "17:00", "late_grace_min": 10})
    check("the owner sets Cimahi to WITA, 08:00 to 17:00", st == 200, (st, resp))
    st, resp = api.patch("outlet", CIMAHI, {"work_start": "8 pagi"})
    check("hours must be HH:MM", refused(st, resp, "JJ:MM"), (st, resp))
    st, resp = api.patch("outlet", CIMAHI, {"radius_m": 5})
    check("a radius below 20 m is refused", refused(st, resp, "Radius absen"), (st, resp))
    cases = [(DAGO, "08:25:00+07:00", 15, 3), (DAGO, "01:25:00Z", 15, 4), (CIMAHI, "08:25:00+08:00", 15, 5), (CIMAHI, "08:25:00+07:00", 75, 6),
             (DAGO, "08:09:00+07:00", 0, 7)]
    for outlet, at, want, back in cases:
        d = t - datetime.timedelta(days=back)
        st, lg = hq.create("staff_attendance", {"employee": rina, "outlet": outlet, "work_date": str(d), "source": "manual",
                                                 "clock_in": f"{d}T{at}", "reason": "Koreksi absen manual oleh kantor pusat"})
        if check(f"head office logs {outlet} clock-in {at} manually", st == 201, (st, lg)):
            eq(f"late minutes at {outlet} for {at}", lg.get("late_min"), want)
            if back == 3:
                st, resp = hq.patch("staff_attendance", lg["name"], {"clock_out": f"{d}T16:30:00+07:00"})
                eq("early leave 16:30 against 17:00", (st, rec(resp).get("early_min")), (200, 30))
                st, resp = hq.verb("staff_attendance", lg["name"], "submit")
                # outside (no position) is held for outlet_supervisor or head_office: head office is itself an approver,
                # so its own correction counts at once (contract 0007), with the outside flag and the reason kept
                check("a manual log by head office is counted at once, still marked outside with its reason",
                      st == 200 and rec(resp).get("docstatus") == 1 and rec(resp).get("outside_site") is True and rec(resp).get("source") == "manual", (st, resp))
    st, resp = ayu.create("staff_attendance", {"source": "manual", "work_date": str(t - datetime.timedelta(days=9)), "in_pos": pos(DAGO, 5, 0)})
    check("staff cannot mark their own log manual", refused(st, resp, "manual"), (st, resp))
    d = t - datetime.timedelta(days=10)
    st, resp = hq.create("staff_attendance", {"employee": rina, "outlet": DAGO, "work_date": str(d), "source": "manual",
                                               "clock_in": f"{t - datetime.timedelta(days=11)}T23:00:00+07:00", "reason": "Koreksi absen manual"})
    check("a clock-in on another day than the work date is refused", refused(st, resp, "tanggal absen"), (st, resp))


def scenario_staff_attendance_period(api):
    """Head office closes yesterday: one summary per active employee who is not exempt, from the submitted logs; an
    overlapping period is refused; a log inside cannot be cancelled while the period stands; cancelling the period marks
    its summaries cancelled. Ayu reads only her own summary; the Braga supervisor none of Dago."""
    got = members("hq", "sa_cashier", "sa_sup_braga", "sa_sup_dago")
    if not got:
        return
    hq, ayu, citra, bayu = got
    t = CTX.get("sa_today") or kernel_today()
    y = t - datetime.timedelta(days=1)
    st, name = full_day(ayu, y, DAGO, clock_in="08:20", clock_out="17:00")
    if not check("Ayu clocks a full day yesterday at Dago (08:20, 10 minutes late)", st == 200, (st, name)):
        return
    st, resp = ayu.verb("staff_attendance", name, "submit")
    check("yesterday inside: submitted at once", st == 200 and rec(resp).get("docstatus") == 1 and rec(resp).get("late_min") == 10, (st, resp))
    name = rec(resp).get("name", name)   # a draft is numbered at submit
    st, resp = api.patch("user", "USR-001", {"attendance_exempt": True})
    check("the owner marks Rina as not required to clock in", st == 200, (st, resp))
    st, p = hq.create("attendance_period", {"period_from": str(y), "period_to": str(t)})
    st2, resp = hq.verb("attendance_period", p.get("name", "x"), "submit") if st == 201 else (st, p)
    check("a period that has not ended cannot be submitted", refused(st2, resp, "belum selesai"), (st2, resp))
    hq.patch("attendance_period", p.get("name", "x"), {"period_to": str(y)})
    st, resp = hq.verb("attendance_period", p.get("name", "x"), "submit")
    if not check("head office submits the period of yesterday (whole business)", st == 200, (st, resp)):
        return
    P = rec(resp).get("name") or p["name"]
    sums = hq.find("attendance_summary", f"attendance_period == '{P}'")
    by = {s["employee"]: s for s in sums}
    a = by.get(CTX["sa_cashier_rec"]) or {}
    eq("Ayu's summary: 1 day, late once, 10 minutes, at Dago", (a.get("days_present"), a.get("late_count"), a.get("late_min_total"), a.get("outlet")), (1, 1, 10, DAGO))
    check("Rina (exempt) has no summary; other active staff have one with 0 days", "USR-001" not in by and (by.get("USR-002") or {}).get("days_present") == 0, sorted(by))
    st, resp = hq.verb("staff_attendance", name, "cancel")
    check("a log inside a submitted period cannot be cancelled", refused(st, resp, "sudah masuk rekap"), (st, resp))
    st, p2 = hq.create("attendance_period", {"period_from": str(y - datetime.timedelta(days=3)), "period_to": str(y), "only_outlet": DAGO})
    st, resp = hq.verb("attendance_period", p2.get("name", "x"), "submit") if st == 201 else (st, p2)
    check("an overlapping period for Dago is refused", refused(st, resp, "tumpang tindih"), (st, resp))
    eq("Ayu reads only her own summary", [s["employee"] for s in ayu.list("attendance_summary")], [CTX["sa_cashier_rec"]])
    eq("the Braga supervisor reads no Dago summary", [s["name"] for s in citra.list("attendance_summary") if s.get("outlet") == DAGO], [])
    check("the Dago supervisor reads Ayu's summary", any(s["employee"] == CTX["sa_cashier_rec"] for s in bayu.list("attendance_summary")), "")
    st, resp = hq.verb("attendance_period", P, "cancel")
    check("head office cancels the period", st == 200, (st, resp))
    eq("its summaries are marked cancelled", sorted({s["cancelled"] for s in hq.find("attendance_summary", f"attendance_period == '{P}'")}), [True])
    api.patch("user", "USR-001", {"attendance_exempt": False})


def scenario_staff_attendance_screen(api):
    """The clock screen absen_outlet and the dashboards: the kitchen employee lands on the clock; the cashier is offered
    only Dago; head office sees today's board, the Braga supervisor only Braga; Ayu's own board counts only her days."""
    got = members("sa_kitchen", "sa_cashier", "hq", "sa_sup_braga")
    if not got:
        return
    dimas, ayu, hq, citra = got
    st, m = dimas.call("GET", "/api/v1/meta")
    work = (m or {}).get("work") or []
    check("the employee's home is the clock screen", work[:1] == [{"screen": "absen_outlet", "home": True}], work)
    sc = ((m or {}).get("screens") or {}).get("absen_outlet") or {}
    check("absen_outlet is a clock over staff_attendance at the outlet", sc.get("type") == "clock" and (sc.get("clock") or {}).get("site", {}).get("model") == "outlet", sc)
    site = (sc.get("clock") or {}).get("site") or {}
    rows = ayu.list("outlet", filter=site.get("filter", ""))
    eq("the cashier is offered only her outlet", [r["name"] for r in rows], [DAGO])
    eq("the site slot reads the outlet's point and radius", (round(float((rows[0].get("point") or {}).get("lat", 0)), 4) if rows else None, rows[0].get("radius_m") if rows else None), (POINT[DAGO][0], 100))
    st, r = ayu.call("GET", "/api/v1/screens/absen_outlet")
    check("the cashier opens the clock screen", st == 200, (st, r))
    ws = dashboard(hq, "absen_hari_ini")
    if ws:
        check("absen_hari_ini: head office sees today's clock-ins of Dago", num(ws["present"]) >= 2, ws["present"])
    ws = dashboard(citra, "absen_hari_ini")
    if ws:
        eq("absen_hari_ini: the Braga supervisor sees none of Dago's", num(ws["present"]), 0)
    ws = dashboard(ayu, "absen_karyawan_saya", 31)
    if ws:
        mine = [x for x in ayu.list("staff_attendance") if x.get("docstatus") == 1]
        eq("absen_karyawan_saya counts Ayu's own submitted days only", num(ws["days_present"]), len(mine))
    ws = dashboard(hq, "absen_karyawan_saya", 31)
    if ws:
        rina = me(hq)
        own = [x for x in hq.find("staff_attendance", f"employee == '{rina}' && docstatus == 1")]
        eq("absen_karyawan_saya for head office counts only Rina's own days, not every log she may read", num(ws["days_present"]), len(own))


SCENARIOS = {"demo-fnb": [scenario_staff_attendance_clock, scenario_staff_attendance_held, scenario_staff_attendance_late,
                          scenario_staff_attendance_period, scenario_staff_attendance_screen]}
