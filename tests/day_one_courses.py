"""Day-one scenarios of demo-kursus (piece kelas_kursus + module courses): a course school's month.

Loaded by tests/day_one.py (bind() hands over its helpers and registers the site): `python3 tests/day_one.py --site
demo-kursus`. Fixtures are synthetic (examples/fixtures/demo-kursus). Covered: quota (a full batch refuses, two
enrollments at the same moment cannot both take the last seat), charges (one charge per period, instalments add up to
the total, one invoice per charge for exactly its amount), attendance, the dashboard and the permissions of the course
admin, the instructor (own batches only, no money) and finance. The books are checked by scenario_accounting after.
"""
import threading

H = {}
PW = {}
INS_OWN, INS_OTHER = "ANG-2026-001", "ANG-2026-002"   # Maya (USR-002) teaches 001 and 003; Nanda (USR-003) 002 and 004

_R, _RP = {"read"}, {"read", "print"}
_RCWEP = {"read", "create", "write", "export", "print"}
COURSE_MATRIX = {
    "course_program": {"course_admin": _RCWEP, "finance_manager": _RCWEP, "instructor": _R},
    "course_batch": {"course_admin": _RCWEP, "finance_manager": {"read", "export", "print"}, "instructor": _RP},
    "student": {"course_admin": _RCWEP, "finance_manager": _RCWEP, "instructor": _R},
    "enrollment": {"course_admin": _RCWEP, "finance_manager": {"read", "write", "export", "print"}, "instructor": _R},
    "course_session": {"course_admin": _RCWEP | {"delete"}, "instructor": {"read", "create", "write", "print"}, "finance_manager": {"read", "export"}},
    "course_charge": {"course_admin": _RCWEP, "finance_manager": _RCWEP | {"submit", "cancel", "amend"}, "sales_manager": _R},
    "course_guard": {"course_admin": {"create"}, "finance_manager": {"create"}, "sales_manager": {"create"}},   # never stored
}


def bind(m):
    """Take the helpers of tests/day_one.py and register demo-kursus (orgs, members, permission rows)."""
    for k in ("ok", "fail", "check", "eq", "CTX", "Api", "messages", "rows_all", "dashboard", "num", "kernel_today", "denied", "secrets"):
        H[k] = getattr(m, k)
    globals().update(H)
    m.SITE_ORGS["demo-kursus"] = [("kursus_contoh", "demo-kursus")]
    m.SITE_MEMBERS["demo-kursus"] = [("ca", "course_admin"), ("ins", "instructor"), ("fm", "finance_manager")]
    m.MATRIX.update(COURSE_MATRIX)
    m.MATRIX["customer"] = dict(m.MATRIX["customer"], course_admin={"read", "create", "write"})
    m.MATRIX["item"] = dict(m.MATRIX["item"], course_admin=_R)
    m.MATRIX["user"] = dict(m.MATRIX["user"], course_admin=_R)


def refused(st, resp, text):
    return st in (409, 422) and any(text in x for x in messages(resp))


def submitted_name(resp, fallback):
    """A draft gets its final number on submit."""
    return ((resp or {}).get("record") or resp or {}).get("name", fallback)


def setup_demo_kursus(api):
    """The course admin, two instructors and finance, signed in (records USR-001..004 of the fixtures)."""
    k = CTX["kernel"]
    st, me = api.call("GET", "/auth/me")
    CTX.update(org=((me or {}).get("org") or {}).get("id") or "kursus_contoh")
    pw = secrets.token_urlsafe(18)   # throwaway password of a throwaway database
    for key, email, role, rec, name in [("ca", "lina@kursuscontoh.test", "course_admin", "USR-001", "Lina Contoh"),
                                        ("ins", "maya@kursuscontoh.test", "instructor", "USR-002", "Maya Contoh"),
                                        ("ins2", "nanda@kursuscontoh.test", "instructor", "USR-003", "Nanda Contoh"),
                                        ("fm", "oki@kursuscontoh.test", "finance_manager", "USR-004", "Oki Contoh")]:
        code, out = k.user_add(email, role, CTX["org"], name, record=rec, password=pw)
        cl = Api.login(k.base, email, pw) if code == 0 else None
        if cl is None:
            CTX["login_error"] = f"{email}: " + (out if code else "login refused")
            fail("setup: add and sign in the members", CTX["login_error"])
            return
        CTX[key] = cl


def seats(api, batch):
    return (api.get("course_batch", batch) or {}).get("seats_taken")


def taken(api, batch):
    return len(rows_all(api, "enrollment", f"batch == '{batch}' && (status == 'registered' || status == 'active')"))


def scenario_courses_fixtures(api):
    eq("fixtures: 4 programs, 4 batches, 10 students, 9 enrollments, 3 sessions",
       [len(rows_all(api, m)) for m in ("course_program", "course_batch", "student", "enrollment", "course_session")], [4, 4, 10, 9, 3])
    bad = {b["name"]: (b["seats_taken"], taken(api, b["name"])) for b in rows_all(api, "course_batch") if b["seats_taken"] != taken(api, b["name"])}
    check("fixtures: each batch's seats taken = its registered and active enrollments", not bad, bad)
    s = api.get("course_session", "PTM-2026-00002")
    eq("a session counts present and not present from its attendance", (s.get("present_count"), s.get("absent_count")), (2, 1))


def scenario_courses_quota(api):
    ca = CTX.get("ca")
    if ca is None:
        return
    # a person on their own gets a customer to bill; an employee is billed to the company
    st, me = ca.create("student", {"student_name": "Kurnia Contoh", "kind": "individual", "phone": "+620000000111", "email": "kurnia@contoh.test"})
    if not check("course admin adds a student on their own (201)", st == 201, (st, me)):
        return
    me = api.get("student", me["name"])
    payer = api.get("customer", me.get("bill_to") or "-") or {}
    check("a student on their own gets a customer with their name to bill", payer.get("customer_name") == "Kurnia Contoh", (me, payer))
    st, emp = ca.create("student", {"student_name": "Lukman Contoh", "kind": "employee", "company": "CUST-0001"})
    check("an employee is billed to the client company", st == 201 and api.get("student", emp["name"]).get("bill_to") == "CUST-0001", (st, emp))
    st, resp = ca.create("student", {"student_name": "Tanpa Perusahaan", "kind": "employee"})
    check("an employee without a company -> 422", refused(st, resp, "Pilih perusahaan"), (st, resp))

    # Excel batch: 4 seats, 3 taken -> the 4th fits, the 5th is refused
    full = "ANG-2026-003"
    st, e4 = ca.create("enrollment", {"student": "PST-0009", "batch": full})
    check("the 4th enrollment of a 4-seat batch -> 201, price and terms from the program", st == 201 and e4.get("price") == 2_500_000 and e4.get("billing_plan") == "full", (st, e4))
    eq("seats taken 4 of 4", seats(api, full), 4)
    st, resp = ca.create("enrollment", {"student": emp["name"], "batch": full})
    check("a 5th enrollment of a full batch -> 422 'Kuota angkatan ini sudah penuh'", refused(st, resp, "Kuota angkatan ini sudah penuh"), (st, resp))
    eq("and nothing was written (4 seats, 4 enrollments)", (seats(api, full), len(rows_all(api, "enrollment", f"batch == '{full}'"))), (4, 4))
    # a student who leaves frees the seat; set back while full -> refused
    st, resp = ca.patch("enrollment", "DFT-2026-00006", {"status": "withdrawn"})
    check("Keluar without a date -> 422", refused(st, resp, "tanggal keluar"), (st, resp))
    st, resp = ca.patch("enrollment", "DFT-2026-00006", {"status": "withdrawn", "left_on": "2026-11-10"})
    check("a student set to Keluar -> 200, seat freed (3 of 4)", st == 200 and seats(api, full) == 3, (st, resp, seats(api, full)))
    st, e5 = ca.create("enrollment", {"student": emp["name"], "batch": full})
    check("the freed seat takes a new enrollment -> 201", st == 201 and seats(api, full) == 4, (st, e5))
    st, resp = ca.patch("enrollment", "DFT-2026-00006", {"status": "registered"})
    check("setting the leaver back to Daftar while the batch is full -> 422", refused(st, resp, "Kuota angkatan ini sudah penuh"), (st, resp))
    st, resp = ca.patch("course_batch", full, {"quota": 3})
    check("lowering the quota below the seats taken -> 422", refused(st, resp, "Kuota angkatan ini sudah penuh"), (st, resp))
    st, resp = ca.patch("enrollment", "DFT-2026-00007", {"batch": "ANG-2026-004"})
    check("moving an enrollment to another batch -> 422 'tidak bisa diganti'", refused(st, resp, "tidak bisa diganti"), (st, resp))
    st, resp = ca.create("enrollment", {"student": "PST-0001", "batch": INS_OWN})
    check("the same student twice in one batch -> refused", st in (409, 422), (st, resp))
    eq("seats of the English batch unchanged (3)", seats(api, INS_OWN), 3)


def scenario_courses_race(api):
    """Two enrollments for the last seat at the same moment: exactly one is made (flow read lock, contract 0025)."""
    ca = CTX.get("ca")
    if ca is None:
        return
    for rnd in range(3):
        st, b = ca.create("course_batch", {"batch_name": f"Kelas uji rebutan {rnd + 1}", "program": "PRG-004", "start_date": "2026-11-09",
                                           "quota": 1, "delivery": "online", "class_link": "https://meet.kursuscontoh.test/uji"})
        if not check(f"race {rnd + 1}: a 1-seat batch (201)", st == 201, (st, b)):
            return
        studs = []
        for i in range(2):
            st, s = ca.create("student", {"student_name": f"Rebutan {rnd + 1}{i}", "kind": "employee", "company": "CUST-0001"})
            studs.append(s["name"])
        out = [None, None]
        gate = threading.Barrier(2)

        def go(i):
            gate.wait()
            out[i] = ca.create("enrollment", {"student": studs[i], "batch": b["name"]})

        ts = [threading.Thread(target=go, args=(i,)) for i in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        codes = sorted(o[0] for o in out)
        loser = next((o for o in out if o[0] != 201), None)
        check(f"race {rnd + 1}: one 201 and one refusal 'Kuota angkatan ini sudah penuh'", codes[0] == 201 and loser is not None and refused(loser[0], loser[1], "Kuota angkatan ini sudah penuh"), out)
        eq(f"race {rnd + 1}: one enrollment in the batch, seats taken 1", (taken(api, b["name"]), seats(api, b["name"])), (1, 1))


def charge(cl, enrollment, **extra):
    st, c = cl.create("course_charge", dict({"enrollment": enrollment, "invoice_date": "2026-11-07"}, **extra))
    return st, c


def invoice_of(api, charge_name, status=None):
    rows = rows_all(api, "sales_invoice", f"notes.contains('[{charge_name}]')" + (f" && docstatus == {status}" if status is not None else ""))
    return rows[0] if rows else None


def bill(fm, api, enrollment, label, **extra):
    """Charge made by the course admin, submitted by finance; returns (charge name, draft invoice) or (None, None)."""
    st, c = charge(CTX["ca"], enrollment, **extra)
    if not check(f"{label}: course admin makes the charge (201)", st == 201, (st, c)):
        return None, None
    st, resp = fm.verb("course_charge", c["name"], "submit")
    if not check(f"{label}: finance submits it", st == 200, (st, resp)):
        return None, None
    name = submitted_name(resp, c["name"])
    inv = invoice_of(api, name, 0)
    check(f"{label}: a draft invoice carries [{name}]", inv is not None, name)
    return name, inv


def scenario_courses_billing(api):
    ca, fm = CTX.get("ca"), CTX.get("fm")
    if ca is None or fm is None:
        return
    enr = "DFT-2026-00002"   # 1.800.000 - 100.000, 3 instalments
    e = api.get("enrollment", enr)
    eq("enrollment total = price - discount (1.700.000), 3 instalments", (e["total"], e["installments"]), (1_700_000, 3))
    st, c = charge(ca, enr, installment_no=1)
    st2, resp = ca.verb("course_charge", c["name"], "submit") if st == 201 else (st, c)
    check("course admin may not submit a charge (it makes the invoice) -> 403", denied(st2, resp), (st2, resp))
    st, dup = charge(ca, enr, installment_no=1)
    check("a second charge for instalment 1, even as a draft -> refused (one charge per period)", st in (409, 422), (st, dup))
    st, resp = charge(ca, enr, installment_no=4)
    check("instalment 4 of 3 -> 422", refused(st, resp, "Cicilan ke harus"), (st, resp))
    amounts, invoices = [], []
    st, resp = fm.verb("course_charge", c["name"], "submit")
    if check("finance submits instalment 1", st == 200, (st, resp)):
        n1 = submitted_name(resp, c["name"])
        inv = invoice_of(api, n1, 0)
        if check("instalment 1: draft invoice to the payer with [charge number], no PPN, ground not_taxable (card t_3106eb6c)", inv and inv["customer"] == "CUST-0003" and inv["tax_rate"] == 0 and inv.get("tax_rate_reason") == "not_taxable", inv):
            invoices.append(inv["name"])
        amounts.append(api.get("course_charge", n1)["amount"])
    st, resp = ca.patch("enrollment", enr, {"discount": 200_000})
    check("after a submitted charge the discount is locked -> 422", refused(st, resp, "tidak bisa diubah lagi"), (st, resp))
    for k in (2, 3):
        n, inv = bill(fm, api, enr, f"instalment {k}", installment_no=k)
        if n:
            amounts.append(api.get("course_charge", n)["amount"])
            if inv:
                invoices.append(inv["name"])
    eq("instalments 566.667 + 566.667 + 566.666", amounts, [566_667, 566_667, 566_666])
    eq("the 3 instalments add up to the total (1.700.000)", sum(amounts), 1_700_000)
    eq("enrollment: 3 charges submitted", api.get("enrollment", enr)["billed_count"], 3)
    done = []
    for name in invoices:
        st, resp = fm.verb("sales_invoice", name, "submit")
        if check(f"finance submits invoice of an instalment", st == 200, (st, resp)):
            done.append(submitted_name(resp, name))
    totals = [api.get("sales_invoice", n)["grand_total"] for n in done]
    eq("the instalment invoices add up to the total", sum(totals), 1_700_000)

    # SPP: one charge per month
    spp = "DFT-2026-00004"
    oct_, inv_oct = bill(fm, api, spp, "SPP 2026-10", month="2026-10")
    st, resp = charge(ca, spp, month="2026-10")
    check("SPP 2026-10 a second time -> refused (one charge per month)", st in (409, 422), (st, resp))
    for m, text in (("2027-03", "di antara bulan mulai"), ("2026-13", "TTTT-BB"), ("2026-09", "di antara bulan mulai")):
        st, resp = charge(ca, spp, month=m)
        check(f"SPP {m} -> 422 '{text}'", refused(st, resp, text), (st, resp))
    if not oct_ or not inv_oct:
        return
    eq("SPP invoice = the monthly fee (450.000)", api.get("sales_invoice", inv_oct["name"])["dpp"], 450_000)
    st, resp = fm.patch("sales_invoice", inv_oct["name"], {"notes": "catatan diganti"})
    check("editing the charge number out of the invoice notes -> 422", refused(st, resp, "tidak bisa diubah"), (st, resp))
    st, resp = fm.patch("sales_invoice", inv_oct["name"], {"items": [{"item": "BRG-0002", "qty": 1, "rate": 400_000}]})
    if check("finance changes the draft invoice to 400.000 (200)", st == 200, (st, resp)):
        st, resp = fm.verb("sales_invoice", inv_oct["name"], "submit")
        check("an invoice that is not the charge's amount -> refused", refused(st, resp, "jumlahnya beda"), (st, resp))
        fm.patch("sales_invoice", inv_oct["name"], {"items": [{"item": "BRG-0002", "qty": 1, "rate": 450_000}]})
    st, resp = fm.verb("sales_invoice", inv_oct["name"], "submit")
    if not check("the SPP invoice at 450.000 submits", st == 200, (st, resp)):
        return
    real = submitted_name(resp, inv_oct["name"])
    # a second invoice with the same charge number: refused at submit
    st, twin = fm.create("sales_invoice", {"customer": "CUST-0005", "notes": f"Salinan [{oct_}]", "tax_rate": 0, "tax_rate_reason": "not_taxable", "items": [{"item": "BRG-0002", "qty": 1, "rate": 450_000}]})
    if check("a copy invoice carrying the same charge number is drafted (201)", st == 201, (st, twin)):
        st, resp = fm.verb("sales_invoice", twin["name"], "submit")
        check("submitting it -> refused (the month is invoiced already)", refused(st, resp, "sudah punya faktur diajukan"), (st, resp))
    # cancel: refused while invoiced; after the invoice is cancelled the charge can go, and its amended copy bills again
    st, resp = fm.verb("course_charge", oct_, "cancel")
    check("cancelling an invoiced charge -> refused 'batalkan fakturnya dulu'", refused(st, resp, "batalkan fakturnya dulu"), (st, resp))
    st, resp = fm.verb("sales_invoice", real, "cancel")
    check("finance cancels the SPP invoice", st == 200, (st, resp))
    st, resp = fm.verb("course_charge", oct_, "cancel")
    check("then the charge cancels", st == 200, (st, resp))
    eq("enrollment: 0 charges submitted again", api.get("enrollment", spp)["billed_count"], 0)
    st, am = fm.verb("course_charge", oct_, "amend")
    if check("finance amends the cancelled charge (one copy of the month)", st in (200, 201), (st, am)):
        am_name = ((am or {}).get("record") or am or {}).get("name")
        st, resp = fm.verb("course_charge", am_name, "submit")
        if check("the amended charge submits", st == 200, (st, resp)):
            n2 = submitted_name(resp, am_name)
            inv2 = invoice_of(api, n2, 0)
            if check("its draft invoice", inv2 is not None, n2):
                st, resp = fm.verb("sales_invoice", inv2["name"], "submit")
                check("and that invoice submits", st == 200, (st, resp))
    live = [i for i in rows_all(api, "sales_invoice", "docstatus == 1 && customer == 'CUST-0005'")]
    eq("SPP 2026-10 of Raka: exactly one live invoice", len(live), 1)

    # full payment of the online course, then the dashboard
    n, inv = bill(fm, api, "DFT-2026-00009", "online course paid in full")
    if inv:
        eq("full charge invoice = 1.200.000", api.get("sales_invoice", inv["name"])["dpp"], 1_200_000)
        fm.verb("sales_invoice", inv["name"], "submit")


def scenario_courses_attendance(api):
    ins, ins2, ca = CTX.get("ins"), CTX.get("ins2"), CTX.get("ca")
    if ins is None:
        return
    import datetime
    today = kernel_today()
    day = today.isoformat() if datetime.date(2026, 11, 2) <= today <= datetime.date(2027, 1, 29) else "2026-11-09"
    st, s = ins.create("course_session", {"batch": INS_OWN, "session_no": 3, "session_date": day, "start_time": "19:00", "end_time": "20:30", "topic": "Kata kerja"})
    if not check("the instructor opens session 3 of her own batch (201)", st == 201, (st, s)):
        return
    s = api.get("course_session", s["name"])
    lines = s.get("attendance") or []
    eq("attendance filled with the batch's registered and active students, all present",
       sorted((a["enrollment"], a["status"]) for a in lines), [("DFT-2026-00001", "present"), ("DFT-2026-00002", "present"), ("DFT-2026-00003", "present")])
    rows = [dict(enrollment=a["enrollment"], status=("absent" if a["enrollment"] == "DFT-2026-00002" else a["status"])) for a in lines]
    st, resp = ins.patch("course_session", s["name"], {"attendance": rows})
    check("the instructor marks one student absent (200): 2 present, 1 not", st == 200 and api.get("course_session", s["name"])["present_count"] == 2, (st, resp))
    st, resp = ins.patch("course_session", s["name"], {"attendance": rows + [{"enrollment": "DFT-2026-00004", "status": "present"}]})
    check("a student of another batch in the attendance -> refused", st in (403, 404, 422), (st, resp))
    st, resp = ins.create("course_session", {"batch": INS_OWN, "session_no": 3, "session_date": day})
    check("session 3 of the batch a second time -> refused", st in (409, 422), (st, resp))
    # row scope: own batches only
    st, resp = ins.call("GET", f"/api/v1/r/course_batch/{INS_OTHER}")
    check("the instructor cannot open another teacher's batch (403/404)", st in (403, 404), (st, resp))
    seen = sorted(b["name"] for b in ins.list("course_batch", limit=200))
    eq("the instructor's batch list: her own two", seen, ["ANG-2026-001", "ANG-2026-003"])
    st, resp = ins.create("course_session", {"batch": INS_OTHER, "session_no": 9, "session_date": "2026-11-10"})
    check("the instructor cannot open a session of another teacher's batch -> refused", st in (403, 404, 422), (st, resp))
    enr = ins.get("enrollment", "DFT-2026-00001") or {}
    check("the instructor reads her students' enrollments without the money fields", enr.get("student") == "PST-0001" and "total" not in enr and "price" not in enr, enr)
    stu, prg = ins.get("student", "PST-0004") or {}, ins.get("course_program", "PRG-002") or {}
    check("the instructor reads student names without their phone, email or guardian (audit L2)",
          stu.get("student_name") == "Raka Contoh" and not ({"phone", "email", "guardian_name", "bill_to"} & set(stu)), stu)
    check("the instructor reads programs without their price (audit L2)", prg.get("program_name") and not ({"price", "monthly_fee"} & set(prg)), prg)
    for model in ("course_charge", "sales_invoice"):
        st, resp = ins.call("GET", f"/api/v1/r/{model}?limit=1")
        check(f"the instructor may not list {model} -> 403", denied(st, resp), (st, resp))
    st, resp = ins.patch("enrollment", "DFT-2026-00001", {"status": "completed"})
    check("the instructor may not change an enrollment -> 403", denied(st, resp), (st, resp))
    if ins2:
        eq("the other instructor sees only his batches", sorted(b["name"] for b in ins2.list("course_batch", limit=200)), ["ANG-2026-002", "ANG-2026-004"])
    if ca:
        st, resp = ca.call("DELETE", "/api/v1/r/enrollment/DFT-2026-00003")
        check("the course admin may not delete an enrollment (set it to Keluar) -> 403", denied(st, resp), (st, resp))


def scenario_courses_dashboard(api):
    ws = dashboard(api, "kursus_ringkas")
    if not ws:
        return
    eq("dashboard: active students = registered and active enrollments", num(ws.get("active_students", {})),
       len(rows_all(api, "enrollment", "status == 'registered' || status == 'active'")))
    today = kernel_today()
    import datetime
    week = [s for s in rows_all(api, "course_session") if today <= datetime.date.fromisoformat(s["session_date"][:10]) < today + datetime.timedelta(days=7)]
    eq("dashboard: classes this week = sessions in the next 7 days", num(ws.get("sessions_this_week", {})), len(week))
    over = sum(i["outstanding"] for i in rows_all(api, "sales_invoice", "docstatus == 1 && outstanding > 0")
               if datetime.date.fromisoformat(i["due_date"][:10]) < today)
    eq("dashboard: arrears = course invoices past due", num(ws.get("overdue_total", {})), round(over))
    for key in ("ins",):
        cl = CTX.get(key)
        if cl:
            st, resp = cl.call("GET", "/api/v1/dashboards/kursus_ringkas")
            check("the instructor does not open the money dashboard -> 403", st == 403, (st, resp))


def submit(cl, model, name):
    st, resp = cl.verb(model, name, "submit")
    return st, resp, submitted_name(resp, name) if st == 200 else name


def amend(cl, name):
    st, am = cl.verb("course_charge", name, "amend")
    return ((am or {}).get("record") or am or {}).get("name") if st in (200, 201) else None


def scenario_courses_audit(api):
    """Audit 2026-11-07: an amended copy cannot bill a billed period (H1), a plan change after cancelling cannot bill
    twice (H1), a fee change racing a charge submit cannot break the instalments (M3), one invoice names one charge (L1)."""
    ca, fm = CTX.get("ca"), CTX.get("fm")
    if ca is None or fm is None:
        return
    # S1: SPP of Sari: November billed; December cancelled, amended and moved to November -> refused
    sari = "DFT-2026-00005"
    nov, _ = bill(fm, api, sari, "S1 SPP 2026-11", month="2026-11")
    dec, dec_inv = bill(fm, api, sari, "S1 SPP 2026-12", month="2026-12")
    if not (nov and dec and dec_inv):
        return
    st, resp = fm.verb("course_charge", dec, "cancel")
    check("S1: December's charge cancels (its invoice is a draft)", st == 200, (st, resp))
    am = amend(fm, dec)
    if not check("S1: finance amends it", am, dec):
        return
    st, resp = fm.patch("course_charge", am, {"month": "2026-11"})
    st, resp, _ = submit(fm, "course_charge", am) if st == 200 else (st, resp, am)
    check("S1: the amended copy moved to November -> refused 'sudah ditagih'", refused(st, resp, "sudah ditagih"), (st, resp))
    fm.patch("course_charge", am, {"month": "2026-12"})
    st, resp, am_name = submit(fm, "course_charge", am)
    if check("S1: back on December it submits", st == 200, (st, resp)):
        new_inv = invoice_of(api, am_name, 0)
        old = api.get("sales_invoice", dec_inv["name"]) or {}
        check("S1: a new draft invoice for the copy; the old draft says it was replaced", new_inv is not None and "diganti" in old.get("notes", ""), (new_inv, old.get("notes")))
        st, resp = fm.verb("sales_invoice", dec_inv["name"], "submit")
        check("S1: the replaced draft cannot be submitted", refused(st, resp, "bukan untuk tagihan"), (st, resp))
    eq("S1: Sari has one submitted charge per month (11, 12)", sorted(c["period"] for c in rows_all(api, "course_charge", f"enrollment == '{sari}' && docstatus == 1")), ["2026-11", "2026-12"])
    # S2: Ani's instalments 1 and 2 cancelled, plan switched to full: only one full charge can be submitted
    ani = "DFT-2026-00001"
    c1, _ = bill(fm, api, ani, "S2 instalment 1", installment_no=1)
    c2, _ = bill(fm, api, ani, "S2 instalment 2", installment_no=2)
    if not (c1 and c2):
        return
    for c in (c1, c2):
        fm.verb("course_charge", c, "cancel")
    st, resp = ca.patch("enrollment", ani, {"billing_plan": "full"})
    check("S2: with both instalments cancelled the plan can change to full (200)", st == 200, (st, resp))
    a1, a2 = amend(fm, c1), amend(fm, c2)
    st1, r1, _ = submit(fm, "course_charge", a1)
    st2, r2, _ = submit(fm, "course_charge", a2)
    check("S2: the first amended copy bills the full price", st1 == 200, (st1, r1))
    check("S2: the second amended copy, now also full -> refused", refused(st2, r2, "sudah ditagih"), (st2, r2))
    st, c3 = charge(ca, ani)
    if st == 201:
        st, resp, _ = submit(fm, "course_charge", c3["name"])
        check("S2: a new full charge -> refused", refused(st, resp, "sudah ditagih"), (st, resp))
    live = rows_all(api, "course_charge", f"enrollment == '{ani}' && docstatus == 1")
    eq("S2: Ani is billed once, 1.800.000", [c["amount"] for c in live], [1_800_000])
    # L1: one invoice naming two charges
    st, inv = fm.create("sales_invoice", {"customer": "CUST-0006", "notes": f"Gabungan [{nov}] [{am_name}]", "tax_rate": 0, "tax_rate_reason": "not_taxable", "items": [{"item": "BRG-0002", "qty": 1, "rate": 450_000}]})
    if check("L1: an invoice naming two charges is drafted (201)", st == 201, (st, inv)):
        st, resp = fm.verb("sales_invoice", inv["name"], "submit")
        check("L1: submitting it -> refused", refused(st, resp, "bukan untuk tagihan"), (st, resp))
    # M3: a discount racing the submit of instalment 1, five times
    for rnd in range(5):
        st, stu = ca.create("student", {"student_name": f"Balapan {rnd + 1}", "kind": "employee", "company": "CUST-0001"})
        st, en = ca.create("enrollment", {"student": stu["name"], "batch": INS_OWN})
        st, ch = charge(ca, en["name"], installment_no=1)
        if st != 201:
            fail(f"M3 race {rnd + 1}: setup", (st, ch))
            return
        out = [None, None]
        gate = threading.Barrier(2)

        def go(i):
            gate.wait()
            out[i] = fm.verb("course_charge", ch["name"], "submit") if i == 0 else ca.patch("enrollment", en["name"], {"discount": 300_000})

        ts = [threading.Thread(target=go, args=(i,)) for i in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        e = api.get("enrollment", en["name"])
        live = rows_all(api, "course_charge", f"enrollment == '{en['name']}' && docstatus == 1")
        want = round(e["total"] / 3)
        good = not live or (len(live) == 1 and live[0]["amount"] == want and e["locked_total"] == e["total"])
        check(f"M3 race {rnd + 1}: submit {out[0][0]} / discount {out[1][0]}: instalment 1 = total / 3 of the enrollment as it stands", good,
              (out, e["total"], e.get("locked_total"), [c["amount"] for c in live]))
        ca.patch("enrollment", en["name"], {"status": "withdrawn", "left_on": "2026-11-07"})   # out of the attendance scenario's batch list


def scenario_courses_roll(api):
    """The instructor's class roll (screens/absen_kelas.rk, contract 0030 roll): her home; today's classes open by
    themselves every morning (flow session_of_the_day, every student present); saving the marks ticks Absen sudah dicek
    (flow session_attendance_checked) so the meeting leaves the roll; the course admin's saves tick nothing."""
    ins, ca = CTX.get("ins"), CTX.get("ca")
    if ins is None:
        return
    import datetime
    st, m = ins.call("GET", "/api/v1/meta")
    work = (m or {}).get("work") or []
    check("Maya (instructor) lands on the class roll (meta.work: absen_kelas, home)", work[:1] == [{"screen": "absen_kelas", "home": True}], work)
    sc = ((m or {}).get("screens") or {}).get("absen_kelas") or {}
    check("absen_kelas is a roll over course_session attendance, no submit", sc.get("type") == "roll" and sc.get("roll", {}).get("rows", {}).get("table") == "attendance"
          and sc.get("roll", {}).get("submit") is False, sc)
    if ca:
        st, r = ca.call("GET", "/api/v1/screens/absen_kelas")
        check("the course admin is not offered the roll (404)", st == 404, (st, r))
    # The morning flow: one session today per running batch with a class on today's weekday.
    today = kernel_today()
    wd = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][today.weekday()]
    want = sorted(b["name"] for b in rows_all(api, "course_batch") if b["start_date"] <= today.isoformat()
                  and (not b.get("end_date") or b["end_date"] >= today.isoformat()) and wd in (b.get("class_days") or "").split())
    before = {x["name"] for x in rows_all(api, "course_session")}
    st, out = api.call("POST", "/api/v1/flows/session_of_the_day/run", {})
    check("flow session_of_the_day runs (200)", st == 200, (st, out))
    made = [x for x in rows_all(api, "course_session") if x["name"] not in before]
    already = sorted({x["batch"] for x in rows_all(api, "course_session") if x["session_date"][:10] == today.isoformat() and x["name"] in before})
    eq(f"today ({wd}): one new session per batch with a class today and no session yet", sorted(x["batch"] for x in made), [b for b in want if b not in already])
    for x in made:
        full = api.get("course_session", x["name"])
        enr = sorted(e["name"] for e in rows_all(api, "enrollment", f"batch == '{x['batch']}'") if e["status"] in ("registered", "active"))
        eq(f"{x['batch']}: today's session lists its students, all present, numbered after the last",
           (sorted((a["enrollment"], a["status"]) for a in full.get("attendance") or []), full["session_no"] > 0, full.get("attendance_taken")),
           ([(e, "present") for e in enr], True, False))
    st, out = api.call("POST", "/api/v1/flows/session_of_the_day/run", {})
    eq("a second run the same day opens nothing new", len(rows_all(api, "course_session")), len(before) + len(made))
    # Maya's roll: a session of her own batch today, as the screen sees and saves it.
    st, s = ins.create("course_session", {"batch": INS_OWN, "session_no": 41, "session_date": today.isoformat(), "topic": "Ulangan"})
    if not check("Maya opens a session of her batch for today (201)", st == 201, (st, s)):
        return
    name = s["name"]
    find = sc.get("roll", {}).get("document", {}).get("find", "false")
    offered = [x["name"] for x in ins.find("course_session", find)]
    check("the roll offers it (her own, up to today, not checked)", name in offered, offered)
    full = ins.get("course_session", name)
    lines = full.get("attendance") or []
    check("each line carries the student's name for the roll", lines and all(a.get("student_name") for a in lines), lines)
    marks = [dict(enrollment=a["enrollment"], status=("sick" if i == 0 else a["status"]), note=("demam" if i == 0 else "")) for i, a in enumerate(lines)]
    st, r = ins.patch("course_session", name, {"attendance": marks})
    after = api.get("course_session", name)
    check("Maya saves the roll: one sick with a note, the rest present; Absen sudah dicek ticks itself",
          st == 200 and after.get("attendance_taken") is True and after.get("present_count") == len(lines) - 1, (st, r, after.get("attendance_taken")))
    check("the checked session leaves the roll", name not in [x["name"] for x in ins.find("course_session", find)], name)
    st, r = ins.patch("course_session", name, {"attendance_taken": False})
    check("Maya can untick it again (the flow leaves an untick alone)", st == 200 and api.get("course_session", name).get("attendance_taken") is False, (st, r))
    st, fut = ins.create("course_session", {"batch": INS_OWN, "session_no": 42, "session_date": (today + datetime.timedelta(days=3)).isoformat()})
    if st == 201:
        ins.patch("course_session", fut["name"], {"attendance": [dict(enrollment=a["enrollment"], status="present") for a in lines]})
        check("a future session's save does not tick it", api.get("course_session", fut["name"]).get("attendance_taken") is False, fut["name"])
    if ca:
        st, s2 = ca.create("course_session", {"batch": INS_OWN, "session_no": 43, "session_date": today.isoformat()})
        if st == 201:
            l2 = api.get("course_session", s2["name"]).get("attendance") or []
            ca.patch("course_session", s2["name"], {"attendance": [dict(enrollment=a["enrollment"], status="absent") for a in l2]})
            check("the course admin's save ticks nothing", api.get("course_session", s2["name"]).get("attendance_taken") is False, s2["name"])


SCENARIOS = [setup_demo_kursus, scenario_courses_fixtures, scenario_courses_quota, scenario_courses_race, scenario_courses_billing,
             scenario_courses_audit, scenario_courses_attendance, scenario_courses_dashboard, scenario_courses_roll]
