"""Day-one scenarios of work shifts on demo-fnb (module shifts, rangka-apps#120 T1 to T5, menu in app piece absen_karyawan).

Loaded by tests/day_one.py after day_one_staff_attendance (its members and helpers): `python3 tests/day_one.py --site
demo-fnb --only shifts`. Head office and the Dago supervisor set shift patterns, the supervisor publishes a weekly roster
grid of Dago (submit), Eko (kitchen, employee role) reads only his own published days, a night shift ends the next day,
a clock-in at 22:20 for the 22:00 shift is 10 minutes late (Dago grace 10) and the clock-out at 06:05 the next morning
is accepted, the hourly sync marks shifts late or absent, and a same-day swap is approved by the Dago supervisor (not
by Braga's). Dates follow today (Asia/Jakarta), as the kernel does.
"""
import datetime

H = {}

HO, DAGO, BRAGA = "OUT-01", "OUT-02", "OUT-03"   # fixtures demo-fnb

_R = {"read"}
_STAFF = {"cashier": _R, "outlet_staff": _R, "employee": _R}
SHIFTS_MATRIX = {
    "shift_template": {"head_office": {"read", "create", "write", "delete", "export", "print"},
                       "outlet_supervisor": {"read", "create", "write"}, **_STAFF},
    "shift_assignment": {"head_office": {"read", "create", "write", "delete", "export", "print"},
                         "outlet_supervisor": {"read", "create", "write", "delete", "print"}, **_STAFF},
    "shift_roster": {"head_office": {"read", "create", "write", "delete", "submit", "cancel", "amend", "export", "print"},
                     "outlet_supervisor": {"read", "create", "write", "delete", "submit", "cancel", "amend", "print"}},
    "shift_swap": {"head_office": {"read", "create", "write", "delete", "submit", "cancel", "export", "print"},
                   "outlet_supervisor": {"read", "create", "write", "delete", "submit", "cancel", "print"},
                   **{r: {"read", "create", "write", "delete"} for r in ("cashier", "outlet_staff", "employee")}},
    "shift_guard": {"head_office": {"create"}, "outlet_supervisor": {"create"}},   # never stored
}

MEMBERS = [("sh_eko", "eko.dago@tahucontoh.test", "employee", DAGO, "Eko Contoh")]


def bind(m):
    for k in ("ok", "fail", "check", "eq", "CTX", "Api", "messages", "dashboard", "num", "kernel_today", "denied", "secrets"):
        H[k] = getattr(m, k)
    globals().update(H)
    m.MATRIX.update(SHIFTS_MATRIX)
    prev = m.SITE_SETUP.get("demo-fnb")

    def chained(api, k):
        if prev:
            prev(api, k)
        setup(api, k)
    m.SITE_SETUP["demo-fnb"] = chained


def setup(api, k):
    pw = secrets.token_urlsafe(18)   # throwaway password of a throwaway database
    for key, email, role, outlets, name in MEMBERS:
        code, out = k.user_add(email, role, CTX.get("org") or "tahu_contoh", name, outlets=outlets, password=pw)
        cl = Api.login(k.base, email, pw) if code == 0 else None
        if cl is None:
            CTX["sh_error"] = f"{email}: " + (out if code else "login refused")
            return
        CTX[key] = cl
        st, mt = cl.call("GET", "/api/v1/meta")
        CTX[key + "_rec"] = ((mt or {}).get("user") or {}).get("name", "")


def members(*keys):
    got = [CTX.get(k) for k in keys]
    if not all(got):
        fail("shifts: members signed in", CTX.get("sh_error") or CTX.get("sa_error") or [k for k in keys if not CTX.get(k)])
        return None
    return got


def refused(st, resp, text):
    return st in (409, 422) and any(text in x for x in messages(resp))


def rec(resp):
    return ((resp or {}).get("record") or resp or {}) if isinstance(resp, dict) else {}


def wib(v):
    """A stored datetime (the kernel answers in UTC) as local Jakarta time, YYYY-MM-DDTHH:MM."""
    if not v:
        return ""
    d = datetime.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return d.astimezone(datetime.timezone(datetime.timedelta(hours=7))).strftime("%Y-%m-%dT%H:%M")


def monday(d):
    return d - datetime.timedelta(days=d.weekday())


def week_line(emp, days):
    """A roster line: days is 7 template names or '' (day off), Monday first."""
    return dict(employee=emp, **dict(zip(("mon", "tue", "wed", "thu", "fri", "sat", "sun"), days)))


def day_of(cl, emp, d):
    rows = cl.find("shift_assignment", f"employee == '{emp}' && work_date == '{d}'")
    return rows[0] if rows else {}


def submit(cl, model, name):
    st, resp = cl.verb(model, name, "submit")
    return st, resp, rec(resp).get("name") or name


def scenario_shifts_templates(api):
    """Head office makes Pagi 07:00 to 15:00 for Dago and Malam 22:00 to 06:00 for every outlet; the Dago supervisor adds
    Siang for Dago, not for Braga; the Braga supervisor reads every pattern but cannot change Dago's."""
    got = members("hq", "sa_sup_dago", "sa_sup_braga", "sh_eko")
    if not got:
        return
    hq, bayu, citra, eko = got
    st, resp = hq.create("shift_template", {"shift_name": "Pagi", "for_outlet": DAGO, "start": "7:00", "end": "15:00"})
    check("a pattern time must be HH:MM", refused(st, resp, "JJ:MM"), (st, resp))
    st, pagi = hq.create("shift_template", {"shift_name": "Pagi", "for_outlet": DAGO, "start": "07:00", "end": "15:00", "break_min": 60})
    if not check("head office makes Pagi for Dago", st == 201, (st, pagi)):
        return
    eq("Pagi: 7 working hours after a 60 minute break, same day", (pagi.get("hours"), pagi.get("crosses_midnight")), (7.0, False))
    st, malam = hq.create("shift_template", {"shift_name": "Malam", "start": "22:00", "end": "06:00"})
    if not check("head office makes Malam for every outlet", st == 201, (st, malam)):
        return
    eq("Malam crosses midnight, 8 hours", (malam.get("crosses_midnight"), malam.get("hours")), (True, 8.0))
    st, siang = bayu.create("shift_template", {"shift_name": "Siang", "for_outlet": DAGO, "start": "11:00", "end": "19:00"})
    check("the Dago supervisor makes Siang for Dago", st == 201, (st, siang))
    st, resp = bayu.create("shift_template", {"shift_name": "Siang", "for_outlet": BRAGA, "start": "11:00", "end": "19:00"})
    check("the Dago supervisor cannot make a pattern for Braga", denied(st, resp), (st, resp))
    st, resp = bayu.create("shift_template", {"shift_name": "Umum", "start": "09:00", "end": "17:00"})
    check("the Dago supervisor cannot make a pattern for every outlet", denied(st, resp), (st, resp))
    names = {t["name"] for t in citra.list("shift_template")}
    check("the Braga supervisor reads every pattern (Malam for all, Dago's too)", {pagi["name"], malam["name"]} <= names, names)
    st, resp = citra.patch("shift_template", pagi["name"], {"start": "06:00"})
    check("the Braga supervisor cannot change Dago's Pagi", denied(st, resp), (st, resp))
    st, resp = eko.create("shift_template", {"shift_name": "X", "start": "01:00", "end": "02:00"})
    check("an employee cannot make a pattern", denied(st, resp), (st, resp))
    CTX.update(sh_pagi=pagi["name"], sh_malam=malam["name"], sh_siang=(siang or {}).get("name", ""))


def scenario_shifts_roster(api):
    """The Dago supervisor publishes next week for Eko and Dimas: 14 assignments, Malam ends the next day, an empty cell
    is a day off. Eko reads only his own published days; the Braga supervisor none. A second roster of the same week is
    refused; re-publishing after an amend changes Wednesday only; head office cannot put Eko on Braga the same day.
    This week's roster writes no day before today."""
    got = members("hq", "sa_sup_dago", "sa_sup_braga", "sh_eko")
    if not got or not CTX.get("sh_malam"):
        return
    hq, bayu, citra, eko = got
    t = kernel_today()
    P, M = CTX["sh_pagi"], CTX["sh_malam"]
    eko_rec, dimas_rec = CTX["sh_eko_rec"], CTX["sa_kitchen_rec"]
    nxt = monday(t) + datetime.timedelta(days=7)
    CTX["sh_next"] = nxt
    st, resp = bayu.create("shift_roster", {"outlet": DAGO, "week_start": str(nxt + datetime.timedelta(days=1)), "lines": [week_line(eko_rec, [P] * 7)]})
    check("a roster week starts on Monday", refused(st, resp, "Senin"), (st, resp))
    st, resp = bayu.create("shift_roster", {"outlet": DAGO, "week_start": str(nxt), "lines": [week_line(eko_rec, [P] * 7), week_line(eko_rec, [M] * 7)]})
    check("one line per employee", refused(st, resp, "Satu karyawan"), (st, resp))
    st, r = bayu.create("shift_roster", {"outlet": DAGO, "week_start": str(nxt), "lines": [
        week_line(eko_rec, [P, P, M, "", M, P, ""]), week_line(dimas_rec, [M, M, P, P, "", "", P])]})
    if not check("the Dago supervisor fills next week's grid", st == 201, (st, r)):
        return
    st, resp, R = submit(bayu, "shift_roster", r["name"])
    if not check("submitting publishes the week", st == 200 and rec(resp).get("docstatus") == 1, (st, resp)):
        return
    rows = hq.find("shift_assignment", f"roster == '{R}'")
    eq("14 assignments, all published", (len(rows), all(x["published"] for x in rows)), (14, True))
    wed = day_of(hq, eko_rec, nxt + datetime.timedelta(days=2))
    eq("Eko's Wednesday is Malam 22:00 to 06:00 the next day (Dago, WIB)",
       (wed.get("shift_name"), wib(wed.get("start_at")), wib(wed.get("end_at"))),
       ("Malam", f"{nxt + datetime.timedelta(days=2)}T22:00", f"{nxt + datetime.timedelta(days=3)}T06:00"))
    thu = day_of(hq, eko_rec, nxt + datetime.timedelta(days=3))
    eq("an empty cell is a day off", (thu.get("day_off"), thu.get("shift_name"), thu.get("start_at") or ""), (True, "Libur", ""))
    eq("Eko reads only his own 7 days", sorted({x["employee"] for x in eko.list("shift_assignment")}), [eko_rec])
    eq("the Braga supervisor reads none of Dago's", [x["name"] for x in citra.find("shift_assignment", f"roster == '{R}'")], [])
    st, resp = citra.call("GET", f"/api/v1/r/shift_roster/{Api.q(R)}")
    check("the Braga supervisor cannot open Dago's roster", st in (403, 404), (st, resp))
    st, r2 = bayu.create("shift_roster", {"outlet": DAGO, "week_start": str(nxt), "lines": [week_line(eko_rec, [P] * 7)]})
    st, resp, _ = submit(bayu, "shift_roster", r2.get("name", "x")) if st == 201 else (st, r2, "")
    check("a second roster of the same week is refused", refused(st, resp, "sudah punya jadwal mingguan"), (st, resp))
    # re-publish: cancel, amend, change Wednesday, submit
    st, resp = bayu.verb("shift_roster", R, "cancel")
    check("the supervisor cancels the published week", st == 200, (st, resp))
    eq("cancelling takes the future days off Eko's schedule", len(eko.list("shift_assignment")), 0)
    st, am = bayu.verb("shift_roster", R, "amend")
    A = rec(am).get("name", "")
    if not check("the supervisor amends the week", st in (200, 201) and A, (st, am)):
        return
    lines = rec(am).get("lines") or []
    for ln in lines:
        if ln.get("employee") == eko_rec:
            ln["wed"] = P
    st, resp = bayu.patch("shift_roster", A, {"lines": lines})
    st, resp, A = submit(bayu, "shift_roster", A) if st == 200 else (st, resp, A)
    check("the amended week is published", st == 200, (st, resp))
    wed2 = day_of(hq, eko_rec, nxt + datetime.timedelta(days=2))
    eq("Wednesday is now Pagi, the same assignment, published by the amendment", (wed2.get("name"), wed2.get("shift_name"), wed2.get("roster"), wed2.get("published")),
       (wed.get("name"), "Pagi", A, True))
    eq("the other days are unchanged", day_of(hq, eko_rec, nxt + datetime.timedelta(days=4)).get("shift_name"), "Malam")
    eq("Eko sees his 7 days again", len(eko.list("shift_assignment")), 7)
    # another outlet, same day
    st, rb = hq.create("shift_roster", {"outlet": BRAGA, "week_start": str(nxt), "lines": [week_line(eko_rec, [P, "", "", "", "", "", ""])]})
    st, resp, _ = submit(hq, "shift_roster", rb.get("name", "x")) if st == 201 else (st, rb, "")
    check("Eko working at Dago on Monday cannot be put on Braga that day", refused(st, resp, "outlet lain"), (st, resp))
    # this week: past days are never written
    cur = monday(t)
    st, rc = bayu.create("shift_roster", {"outlet": DAGO, "week_start": str(cur), "lines": [week_line(eko_rec, [M] * 7)]})
    st, resp, C = submit(bayu, "shift_roster", rc.get("name", "x")) if st == 201 else (st, rc, "")
    if check("the supervisor publishes this week (Malam every day)", st == 200, (st, resp)):
        days = sorted(x["work_date"] for x in hq.find("shift_assignment", f"roster == '{C}'"))
        eq("only today and later days are written", days, [str(cur + datetime.timedelta(days=i)) for i in range(7) if cur + datetime.timedelta(days=i) >= t])
    CTX["sh_roster_now"] = C


def scenario_shifts_attendance(api):
    """Eko's shift today is Malam 22:00 (this week's roster). He clocks in at 22:20: the log links to the shift, the
    schedule is 22:00 and he is 10 minutes late (Dago grace 10); the clock-out at 06:05 the next morning is accepted and
    the work date stays today. The hourly sync marks the shift late; a published shift yesterday without a clock-in is
    absent; a day off is not."""
    import day_one_staff_attendance as SA
    got = members("hq", "sh_eko")
    if not got or not CTX.get("sh_roster_now"):
        return
    hq, eko = got
    t = kernel_today()
    eko_rec = CTX["sh_eko_rec"]
    shift = day_of(hq, eko_rec, t)
    if not check("Eko has a published Malam today", shift.get("shift_name") == "Malam" and shift.get("published"), shift):
        return
    st, lg = eko.create("staff_attendance", {"in_pos": SA.pos(DAGO, 30, 0)})
    if not check("Eko clocks in at Dago", st == 201, (st, lg)):
        return
    name = lg["name"]
    eq("the clock-in links to today's shift", lg.get("work_shift"), shift["name"]) if lg.get("work_shift") else \
        eq("the clock-in links to today's shift", (eko.get("staff_attendance", name) or {}).get("work_shift"), shift["name"])
    st, resp = eko.patch("staff_attendance", name, {"in_photo": SA.photo(eko, name), "clock_in": SA.local(t, "22:20")})
    r = rec(resp)
    eq("scheduled 22:00 from the shift, 10 minutes late", (st, wib(r.get("sched_start")), r.get("late_min")), (200, f"{t}T22:00", 10))
    st, resp = eko.patch("staff_attendance", name, {"clock_out": SA.local(t + datetime.timedelta(days=1), "06:05"), "out_pos": SA.pos(DAGO, 20, 0), "out_photo": SA.photo(eko, name)})
    r = rec(resp)
    eq("clock-out 06:05 the next morning: no early leave, 465 minutes worked, work date today", (st, r.get("early_min"), r.get("worked_min"), r.get("work_date")), (200, 0, 465, str(t)))
    st, resp = eko.verb("staff_attendance", name, "submit")
    check("inside the radius: submitted at once", st == 200 and rec(resp).get("docstatus") == 1, (st, resp))
    log_name = rec(resp).get("name") or name   # a draft is numbered at submit
    # yesterday: a published shift without a clock-in; the day before: a day off
    y, y2 = t - datetime.timedelta(days=1), t - datetime.timedelta(days=2)
    st, a1 = hq.create("shift_assignment", {"employee": eko_rec, "outlet": DAGO, "work_date": str(y), "shift_template": CTX["sh_pagi"], "published": True})
    st2, a2 = hq.create("shift_assignment", {"employee": eko_rec, "outlet": DAGO, "work_date": str(y2), "day_off": True, "published": True})
    check("head office adds yesterday's Pagi and a day off by hand", st == 201 and st2 == 201, (st, a1, st2, a2))
    st, resp = hq.create("shift_assignment", {"employee": eko_rec, "outlet": DAGO, "work_date": str(y), "shift_template": CTX["sh_malam"]})
    check("a second assignment the same day is refused (one per employee per day)", st in (409, 422), (st, resp))
    st, resp = hq.create("shift_assignment", {"employee": eko_rec, "outlet": DAGO, "work_date": str(t - datetime.timedelta(days=3)), "shift_template": CTX["sh_pagi"], "day_off": True})
    check("a shift and a day off together are refused", refused(st, resp, "salah satu"), (st, resp))
    st, resp = api.call("POST", "/api/v1/flows/shift_attendance_sync/run", {"event": {}})
    check("the owner runs the hourly sync", st == 200, (st, resp))
    got = {d: day_of(hq, eko_rec, d) for d in (t, y, y2)}
    eq("today late 10 with the log linked; yesterday absent; the day off still planned",
       [(got[t].get("outcome"), got[t].get("late_min"), got[t].get("attendance")), got[y].get("outcome"), got[y2].get("outcome")],
       [("late", 10, log_name), "absent", "planned"])
    st, resp = hq.patch("shift_assignment", got[y]["name"], {"outcome": "present"})
    eq("nobody writes the outcome by hand", (hq.get("shift_assignment", got[y]["name"]) or {}).get("outcome"), "absent")


def scenario_shifts_swap(api):
    """Eko asks to swap next Monday (Pagi) with Dimas (Malam). He cannot pick Dimas's shift or submit; the Braga
    supervisor cannot see it; the Dago supervisor picks Dimas's Monday and submits: Eko works Malam, Dimas Pagi.
    Cancelling swaps them back."""
    got = members("hq", "sa_sup_dago", "sa_sup_braga", "sh_eko")
    if not got or not CTX.get("sh_next"):
        return
    hq, bayu, citra, eko = got
    mon = CTX["sh_next"]
    eko_rec, dimas_rec = CTX["sh_eko_rec"], CTX["sa_kitchen_rec"]
    mine, theirs = day_of(hq, eko_rec, mon), day_of(hq, dimas_rec, mon)
    if not check("Eko has Pagi and Dimas Malam next Monday", (mine.get("shift_name"), theirs.get("shift_name")) == ("Pagi", "Malam"), (mine, theirs)):
        return
    st, sw = eko.create("shift_swap", {"my_shift": theirs["name"], "with_note": "Dimas"})
    check("Eko cannot ask on Dimas's shift", st in (403, 404, 422), (st, sw))
    st, sw = eko.create("shift_swap", {"my_shift": mine["name"], "with_note": "Dimas", "reason": "Ada acara keluarga pagi", "their_shift": theirs["name"]})
    if not check("Eko asks to swap his Monday", st == 201, (st, sw)):
        return
    S = sw["name"]
    eq("Eko cannot fill the colleague's shift (left for the supervisor)", sw.get("their_shift") or "", "")
    st, resp = eko.verb("shift_swap", S, "submit")
    check("Eko cannot approve his own swap", denied(st, resp), (st, resp))
    st, resp = citra.call("GET", f"/api/v1/r/shift_swap/{Api.q(S)}")
    check("the Braga supervisor cannot see the Dago swap", st in (403, 404), (st, resp))
    st, resp = bayu.patch("shift_swap", S, {"their_shift": theirs["name"]})
    st, resp, S = submit(bayu, "shift_swap", S) if st == 200 else (st, resp, S)
    if not check("the Dago supervisor picks Dimas's Monday and approves", st == 200 and rec(resp).get("docstatus") == 1, (st, resp)):
        return
    eq("Eko now works Malam, Dimas Pagi", (day_of(hq, eko_rec, mon).get("shift_name"), day_of(hq, dimas_rec, mon).get("shift_name"), day_of(hq, eko_rec, mon).get("swap")), ("Malam", "Pagi", S))
    st, resp = bayu.verb("shift_swap", S, "cancel")
    check("the supervisor cancels the swap", st == 200, (st, resp))
    eq("the shifts are back", (day_of(hq, eko_rec, mon).get("shift_name"), day_of(hq, dimas_rec, mon).get("shift_name")), ("Pagi", "Malam"))


def scenario_shifts_dashboards(api):
    """jadwal_saya shows Eko his own days; jadwal_outlet shows head office today's Dago shifts, the Braga supervisor none."""
    got = members("hq", "sa_sup_braga", "sh_eko")
    if not got:
        return
    hq, citra, eko = got
    ws = dashboard(eko, "jadwal_saya")
    if ws:
        mine = [x for x in eko.list("shift_assignment") if x["work_date"] > str(kernel_today())]
        check("jadwal_saya: Eko's today and next days", len(ws["today"].get("rows") or ws["today"].get("data") or []) == 1 and len(ws["next"].get("rows") or ws["next"].get("data") or []) == min(len(mine), 14), ws)
    ws = dashboard(hq, "jadwal_outlet")
    if ws:
        check("jadwal_outlet: head office sees Eko on today's board", num(ws["scheduled"]) >= 1 and num(ws["present"]) >= 1, (ws["scheduled"], ws["present"]))
    ws = dashboard(citra, "jadwal_outlet")
    if ws:
        eq("jadwal_outlet: the Braga supervisor sees no Dago shift", num(ws["scheduled"]), 0)


def scenario_shifts_pos_link(api):
    """demo-kasir (T3): a cash session opened by a cashier with a published shift today links to it; a cashier without a
    shift still opens a session (selling never stops), marked "Tanpa jadwal". Two new cashiers at Toko Dago, so the
    sessions of the pos scenarios are not touched."""
    k, hq = CTX.get("kernel"), CTX.get("hq")
    if not (k and hq):
        fail("shifts pos: kernel and head office signed in", CTX.get("login_error"))
        return
    dgo = "OUT-02"
    pw = secrets.token_urlsafe(18)   # throwaway password of a throwaway database
    cls = {}
    for key, email, name in (("wati", "wati.dago@contohwarung.test", "Wati Contoh"), ("joko", "joko.dago@contohwarung.test", "Joko Contoh")):
        code, out = k.user_add(email, "cashier", CTX.get("org") or "warung_contoh", name, outlets=dgo, password=pw)
        cls[key] = Api.login(k.base, email, pw) if code == 0 else None
        if not check(f"shifts pos: {name} signs in as cashier", cls[key] is not None, out):
            return
    wati = cls["wati"]
    st, mt = wati.call("GET", "/api/v1/meta")
    wati_rec = ((mt or {}).get("user") or {}).get("name", "")
    st, tpl = hq.create("shift_template", {"shift_name": "Pagi", "for_outlet": dgo, "start": "07:00", "end": "15:00"})
    st2, a = hq.create("shift_assignment", {"employee": wati_rec, "outlet": dgo, "work_date": str(kernel_today()),
                                            "shift_template": rec(tpl).get("name", ""), "published": True})
    if not check("head office publishes Wati's Pagi today at Toko Dago", st == 201 and st2 == 201, (st, tpl, st2, a)):
        return
    flt = (hq.get("outlet", dgo) or {}).get("cash_float") or 0   # an earlier pos scenario may have set the outlet's float
    st, s = wati.create("pos_shift", {"outlet": dgo, "opening_cash": flt})
    if check("Wati opens a cash session", st == 201, (st, s)):
        got = wati.get("pos_shift", s["name"]) or {}
        eq("the session links to her shift and names her employee record", (got.get("work_shift"), got.get("employee"), got.get("no_schedule")),
           (a["name"], wati_rec, False))
    st, s = cls["joko"].create("pos_shift", {"outlet": dgo, "opening_cash": flt})
    check("Joko without a shift still opens a session, marked Tanpa jadwal", st == 201 and rec(s).get("no_schedule") is True and not rec(s).get("work_shift"), (st, s))


SCENARIOS = {"demo-fnb": [scenario_shifts_templates, scenario_shifts_roster, scenario_shifts_attendance, scenario_shifts_swap, scenario_shifts_dashboards],
             "demo-kasir": [scenario_shifts_pos_link]}
