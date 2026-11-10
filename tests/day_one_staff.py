"""Day-one scenarios of the staff dashboards (plans/layar-kerja.md §4.1 A5): each staff role lands on its own dashboard
(kernel contract 0030 §3, web app homeHash) and sees only its own records (row scope, contract 0010).

Loaded by tests/day_one.py (bind() hands over its helpers). Sites and members:
- demo-kasir: Wati (outlet_staff, Kedai Riau) on outlet_hari_ini (piece stok_outlet; the dapur piece adds its kitchen tickets);
- demo-fnb: Yusuf (outlet_staff, Dago) on outlet_harian (piece fnb_cabang, Jalur A daily sales);
- demo-kursus: Maya and Nanda (instructor) on kelas_saya;
- demo-langganan: Dodi (collector) on penagihan_saya (shipped with the subscriptions piece).
- demo-sewa: the warehouse (rental_staff) on alat_hari_ini (shipped with the rental handover, A6).
"""
import datetime

H = {}
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def bind(m):
    for k in ("ok", "fail", "check", "eq", "CTX", "Api", "denied", "dashboard", "num", "kernel_today", "secrets"):
        H[k] = getattr(m, k)
    globals().update(H)


def rec(x):
    return (x or {}).get("record", x) or {}


def home(meta):
    """The landing page of the web app for this /meta (rangka-os web/app/shell.js homeHash, contract 0030 §3)."""
    for w in meta.get("work") or []:
        if w.get("home") and (meta.get("screens") or {}).get(w["screen"]):
            return "#/w/" + w["screen"]
    described = meta.get("dashboards")
    mine = [r for r in ((meta.get("user") or {}).get("roles") or []) if r != "owner"]
    open_ = [d for a in meta.get("apps") or [] for d in (a.get("dashboards") or []) if described is None or d in described]
    for d in open_:
        if set(mine) & set((described or {}).get(d, {}).get("roles") or []):
            return "#/d/" + d
    if open_:
        return "#/d/" + open_[0]
    return None


def setup_kasir(api, k):
    """Wati, outlet staff at Kedai Riau (after day_one_pos.setup_demo_kasir); day_one_kitchen may have added her."""
    if CTX.get("wati"):
        return
    pw = secrets.token_urlsafe(18)   # throwaway password of a throwaway database
    email = "wati.riau@contohwarung.test"
    code, out = k.user_add(email, "outlet_staff", CTX.get("org") or "warung_contoh", "Wati Contoh", outlets="OUT-03", password=pw)
    cl = Api.login(k.base, email, pw) if code == 0 else None
    if cl is None:
        CTX["login_error"] = f"{email}: " + (out if code else "login refused")
        return
    CTX["wati"] = cl


def lands(cl, who, want):
    st, m = cl.call("GET", "/api/v1/meta")
    got = home(m or {})
    check(f"{who} lands on {want}", got == want, (got, (m or {}).get("user"), sorted(((m or {}).get("dashboards") or {}).keys())))
    return m or {}


def rows(w):
    return (w or {}).get("rows") or []


def scenario_staff_outlet_kasir(api):
    """Wati (outlet staff, Kedai Riau): a transfer from the warehouse to Riau waits for her, a waste note of today, the
    nothing of Toko Dago."""
    wati, hq = CTX.get("wati"), CTX.get("hq")
    if not (wati and hq):
        fail("staff: Wati and Rina signed in", CTX.get("login_error") or "missing")
        return
    lands(wati, "Wati (outlet_staff)", "#/d/outlet_hari_ini")
    lands(api, "the owner", "#/d/kasir_ringkas")
    lands(hq, "Rina (head office)", "#/d/kasir_ringkas")
    # head office sends roti from Kedai Riau's stock... to Toko Dago (Dago's, not Wati's), and coffee cups to Riau
    sent = {}
    for frm, to, item, qty in (("OUT-03", "OUT-02", "BRG-0015", 2), ("OUT-02", "OUT-03", "BRG-0001", 6)):
        st, t = hq.create("stock_transfer", {"from_outlet": frm, "to_outlet": to, "items": [{"item": item, "qty": qty}]})
        if check(f"head office drafts a transfer {frm} -> {to} -> 201", st == 201, (st, t)):
            st, r = hq.verb("stock_transfer", rec(t)["name"], "submit")
            check(f"transfer {frm} -> {to} sent -> 200", st == 200, (st, r))
            sent[to] = rec(r).get("name")
    st, a = wati.create("stock_adjustment", {"outlet": "OUT-03", "reason": "waste", "items": [{"item": "BRG-0015", "qty": 1}], "notes": "Roti gosong"})
    if check("Wati notes a burnt roti as waste -> 201", st == 201, (st, a)):
        st, r = wati.verb("stock_adjustment", rec(a)["name"], "submit")
        check("Wati submits the waste -> 200", st == 200, (st, r))
    ws = dashboard(wati, "outlet_hari_ini")
    if not ws:
        return
    to_riu = api.find("stock_transfer", "docstatus == 1 && state != 'received' && to_outlet == 'OUT-03'")
    eq("Wati: transfers to receive = Kedai Riau's only", num(ws["to_receive"]), len(to_riu))
    check("Wati's receive list holds the transfer to Riau, not the one to Dago",
          sent.get("OUT-03") in {r.get("name") for r in rows(ws["receive_list"])} and sent.get("OUT-02") not in {r.get("name") for r in rows(ws["receive_list"])}, rows(ws["receive_list"]))
    today = kernel_today().isoformat()
    waste = api.find("stock_adjustment", f"adjustment_date == '{today}' && reason == 'waste' && outlet == 'OUT-03'")
    eq("Wati: waste notes today at Riau", num(ws["waste_today"]), len(waste))
    wh = dashboard(hq, "outlet_hari_ini")
    if wh:
        allt = api.find("stock_transfer", "docstatus == 1 && state != 'received'")
        eq("head office (no outlets): transfers to receive at every outlet", num(wh["to_receive"]), len(allt))
    if "tickets_open" in ws:   # the dapur piece is installed on demo-kasir
        open_riu = api.find("kitchen_ticket", f"outlet == 'OUT-03' && date == '{today}' && status != 'picked_up' && status != 'void'")
        eq("Wati: open kitchen tickets of Riau today", num(ws["tickets_open"]), len(open_riu))
        check("Wati's ticket list: Riau only", {r.get("outlet") for r in rows(ws["tickets_list"])} <= {"OUT-03"}, rows(ws["tickets_list"]))
        if CTX.get("tono"):
            lands(CTX["tono"], "Tono (kitchen)", "#/w/dapur")
    else:
        fail("demo-kasir has the dapur piece: kitchen widgets on outlet_hari_ini", sorted(ws))
    # the goods arrive: Wati receives Riau's, head office Dago's (the books count goods in transit at no outlet)
    for to, item, qty, cl in (("OUT-03", "BRG-0001", 6, wati), ("OUT-02", "BRG-0015", 2, hq)):
        if not sent.get(to):
            continue
        st, rc = cl.create("stock_transfer_receipt", {"stock_transfer": sent[to], "outlet": to, "items": [{"item": item, "qty": qty}]})
        if check(f"transfer to {to} received: draft -> 201", st == 201, (st, rc)):
            st, r = cl.verb("stock_transfer_receipt", rec(rc)["name"], "submit")
            check(f"transfer to {to} received -> 200", st == 200, (st, r))
    ws = dashboard(wati, "outlet_hari_ini")
    eq("received: nothing left for Wati to receive", num(ws.get("to_receive", {})), len(api.find("stock_transfer", "docstatus == 1 && state != 'received' && to_outlet == 'OUT-03'")))
    st, r = CTX["dika"].call("GET", "/api/v1/dashboards/outlet_hari_ini")
    check("Dika (cashier) is not offered Outlet hari ini -> 403", st == 403, (st, r))
    st, r = wati.call("GET", "/api/v1/dashboards/kasir_ringkas")
    check("Wati cannot open the cashier summary (money) -> 403", st == 403, (st, r))


def scenario_staff_outlet_fnb(api):
    """Yusuf (outlet staff, Dago, Jalur A) lands on Outlet hari ini of the F&B piece: Dago's transfers and daily sales."""
    staff, hq = CTX.get("staff_dago"), CTX.get("hq")
    if not (staff and hq):
        return
    lands(staff, "Yusuf (outlet_staff, Dago)", "#/d/outlet_harian")
    lands(api, "the owner", "#/d/fnb_overview")
    ws = dashboard(staff, "outlet_harian")
    if not ws:
        return
    dago = [t for t in api.find("stock_transfer", "docstatus == 1 && state != 'received'") if t.get("to_outlet") == "OUT-02"]
    eq("Yusuf: transfers to receive at Dago only", num(ws["to_receive"]), len(dago))
    check("Yusuf's receive list: to Dago only", {r.get("to_outlet") for r in rows(ws["receive_list"])} <= {"OUT-02"}, rows(ws["receive_list"]))
    drafts = [s for s in api.find("outlet_sales", "docstatus == 0") if s.get("outlet") == "OUT-02"]
    eq("Yusuf: daily sales drafts of Dago only", num(ws["sales_draft"]), len(drafts))
    check("no ticket widgets without the dapur piece", "tickets_open" not in ws, sorted(ws))


def scenario_staff_instructor(api):
    """Maya (instructor) lands on Kelas saya: her batches scheduled today, her meetings whose attendance she has not
    checked, her students; never Nanda's. Ticking Absen sudah dicek takes a meeting off the list."""
    ins, ins2, ca = CTX.get("ins"), CTX.get("ins2"), CTX.get("ca")
    if not (ins and ins2):
        return
    lands(ins, "Maya (instructor)", "#/d/kelas_saya")
    lands(ins2, "Nanda (instructor)", "#/d/kelas_saya")
    if ca:
        lands(ca, "the course admin", "#/d/kursus_ringkas")
    today = kernel_today()
    wd = WEEKDAYS[today.weekday()]
    for who, cl, user in (("Maya", ins, "USR-002"), ("Nanda", ins2, "USR-003")):
        ws = dashboard(cl, "kelas_saya")
        if not ws:
            continue
        batches = [api.call("GET", f"/api/v1/r/course_batch/{b['name']}?children=1")[1] for b in api.find("course_batch", f"instructor == '{user}'")]
        want = sorted(b["name"] for b in batches if b["start_date"] <= today.isoformat() and (not b.get("end_date") or b["end_date"] >= today.isoformat())
                      and wd in {s["weekday"] for s in b.get("schedule") or []})
        eq(f"{who}: classes today ({wd}) = own active batches with a slot today", sorted(r.get("name") for r in rows(ws["classes_today"])), want)
        open_ = [s for s in api.find("course_session", f"instructor == '{user}'") if s["session_date"] <= today.isoformat() and not s.get("attendance_taken")]
        eq(f"{who}: meetings with attendance not checked", num(ws["attendance_open"]), len(open_))
        mine = [e for e in api.find("enrollment", f"instructor == '{user}'") if e["status"] in ("registered", "active")]
        eq(f"{who}: active students = own batches' registered and active", num(ws["my_students"]), len(mine))
        check(f"{who}: the student list holds no fee", not any(k in r for r in rows(ws["students"]) for k in ("total", "price", "monthly_fee")), rows(ws["students"])[:2])
    maya_open = [r.get("name") for r in rows(dashboard(ins, "kelas_saya").get("attendance_list"))]
    if maya_open:
        st, r = ins.patch("course_session", maya_open[0], {"attendance_taken": True})
        check("Maya ticks Absen sudah dicek -> 200", st == 200, (st, r))
        after = [r.get("name") for r in rows(dashboard(ins, "kelas_saya").get("attendance_list"))]
        check("the meeting leaves Maya's list", maya_open[0] not in after, after)
    else:
        fail("Maya has a meeting to check (day_one_courses opened session 3)", "none")
    st, r = ins.call("GET", "/api/v1/dashboards/kursus_ringkas")
    check("Maya cannot open the course summary (money) -> 403", st == 403, (st, r))
    if ca:
        st, r = ca.call("GET", "/api/v1/dashboards/kelas_saya")
        check("the course admin is not offered Kelas saya -> 403", st == 403, (st, r))


def scenario_staff_collector(api):
    """Dodi (collector) lands on Penagihan saya, not on the finance summary he may not open."""
    col, fm = CTX.get("col1"), CTX.get("fm")
    if not (col and fm):
        return
    lands(col, "Dodi (collector)", "#/d/penagihan_saya")
    lands(fm, "finance", "#/d/langganan_ringkas")
    st, r = col.call("GET", "/api/v1/dashboards/langganan_ringkas")
    check("Dodi cannot open the subscriptions summary -> 403", st == 403, (st, r))


def scenario_staff_rental(api):
    """The rental warehouse (rental_staff) lands on Alat hari ini (rental handover, A6); the owner keeps Ringkasan sewa."""
    gd = CTX.get("gd")
    if not gd:
        return
    lands(gd, "the warehouse (rental_staff)", "#/d/alat_hari_ini")
    lands(api, "the owner", "#/d/sewa_ringkas")


SCENARIOS = {
    "demo-kasir": [scenario_staff_outlet_kasir],
    "demo-fnb": [scenario_staff_outlet_fnb],
    "demo-kursus": [scenario_staff_instructor],
    "demo-langganan": [scenario_staff_collector],
    "demo-sewa": [scenario_staff_rental],
}
