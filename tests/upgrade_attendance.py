"""Upgrade check for field attendance (A2): `python3 tests/upgrade.py --site demo-jasa --check attendance`

Old tree: a worker, a project, an assignment and a submitted daily timesheet. After the upgrade: the old assignment has
no work site (its site point reads 0), the old timesheet keeps its amount and has no attendance close; the owner then
gives the assignment a work site, a log clocked in at the site submits at once and a close makes the recap timesheet.
"""
import datetime


def before(api):
    st, w = api.create("worker", {"worker_name": "Lama Lapangan", "employee_no": "OLD-A2", "day_rate": 250_000, "cost_day_rate": 180_000})
    assert st == 201, (st, w)
    st, prj = api.create("project", {"project_name": "Proyek absen lama", "customer": "CUST-0001", "state": "running"})
    assert st == 201, (st, prj)
    t = datetime.date.today()
    st, asg = api.create("assignment", {"project": prj["name"], "worker": w["name"], "start_date": str(t - datetime.timedelta(days=30)),
                                        "end_date": str(t + datetime.timedelta(days=30))})
    assert st == 201, (st, asg)
    st, ts = api.create("timesheet", {"project": prj["name"], "worker": w["name"], "assignment": asg["name"], "work_date": str(t - datetime.timedelta(days=20)),
                                      "days": 1, "description": "Shift lama"})
    assert st == 201, (st, ts)
    st, r = api.verb("timesheet", ts["name"], "submit")
    assert st == 200, (st, r)
    return {"worker": w["name"], "project": prj["name"], "assignment": asg["name"], "timesheet": r["name"]}


def after(api, s, d1):
    a = api.get("assignment", s["assignment"])
    d1.eq("old assignment: no work site, site point 0", (a.get("work_site") or "", a.get("site_lat"), a.get("site_radius_m")), ("", 0, 0))
    ts = api.get("timesheet", s["timesheet"])
    d1.eq("old timesheet keeps its amount and cost, no attendance close", (ts.get("amount"), ts.get("cost_amount"), ts.get("attendance_close") or ""), (250_000, 180_000, ""))
    st, site = api.create("work_site", {"site_name": "Lokasi uji upgrade", "lat": -6.2, "lng": 106.8, "radius_m": 100})
    d1.check("a work site can be added after the upgrade", st == 201, (st, site))
    st, a = api.call("PATCH", f"/api/v1/r/assignment/{d1.Api.q(s['assignment'])}", {"work_site": site.get("name")})
    d1.check("the old assignment gets the work site and its point", st == 200 and a.get("site_radius_m") == 100 and a.get("site_lat") == -6.2, (st, a))
    t = d1.kernel_today()
    st, lg = api.create("attendance_log", {"worker": s["worker"], "assignment": s["assignment"], "work_date": str(t), "clock_in": f"{t}T08:00:00+07:00",
                                           "in_lat": -6.2, "in_lng": 106.8, "clock_out": f"{t}T17:00:00+07:00", "out_lat": -6.2, "out_lng": 106.8})
    d1.check("a log at the site of the old assignment is inside", st == 201 and lg.get("outside_site") is False, (st, lg))
    import secrets
    st, att = api.upload(f"/api/v1/r/attendance_log/{d1.Api.q(lg.get('name', 'x'))}/attachments", {}, "selfie.jpg", b"\xff\xd8\xff\xe0" + secrets.token_bytes(32) + b"\xff\xd9")
    api.call("PATCH", f"/api/v1/r/attendance_log/{d1.Api.q(lg.get('name', 'x'))}", {"in_photo": (att or {}).get("id", "")})
    st, r = api.verb("attendance_log", lg.get("name", "x"), "submit")
    d1.check("and submits at once", st == 200, (st, r))
    st, c = api.create("attendance_close", {"project": s["project"], "period_from": str(t), "period_to": str(t)})
    st, r = api.verb("attendance_close", c.get("name", "x"), "submit") if st == 201 else (st, c)
    d1.check("a close of today submits", st == 200, (st, r))
    rows = api.find("timesheet", "attendance_close == '%s'" % (r or {}).get("name", "x"))
    d1.eq("the close made one draft recap of 1 day for the old worker", [(x["worker"], x["days"], x["docstatus"]) for x in rows], [(s["worker"], 1, 0)])
