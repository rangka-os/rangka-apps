"""Day-one scenarios of demo-sewa (piece sewa_alat, module rental): a small camera and party-equipment rental.

Loaded by tests/day_one.py (bind() hands over its helpers and registers the site): `python3 tests/day_one.py --site
demo-sewa`. Fixtures are synthetic (examples/fixtures/demo-sewa/demo.rk). Dates are relative to today, so the
dashboard checks (late, going out today) hold whenever the test runs.

Members: Admin sewa (sales_manager) books, records equipment out and back; Keuangan (finance_manager) sets rates and
corrects returns; Kantor pusat (head_office) reads; Staf gudang (rental_staff) hands equipment over per unit (checklist,
photo, signature, BAST) without touching money. The owner (seeded) can do everything.
"""
import datetime
import os
import shutil
import tempfile
import threading
import zoneinfo

H = {}
LENS, CAMERA, LAMP, TENT, CHAIR, SOUND = "ALT-0002", "ALT-0001", "ALT-0003", "ALT-0004", "ALT-0005", "ALT-0006"
BUDI, WO, FILM, ARISAN = "CUST-0001", "CUST-0002", "CUST-0003", "CUST-0004"
TODAY = datetime.datetime.now(zoneinfo.ZoneInfo("Asia/Jakarta")).date()   # the kernel's today() (site timezone)


def d(n):
    return (TODAY + datetime.timedelta(days=n)).isoformat()


_R, _RP = {"read", "export"}, {"read", "export", "print"}
_DOC = {"read", "create", "write", "delete", "submit", "cancel", "amend", "export", "print"}
_STAFF = {"read", "create", "write", "delete", "export", "print"}
MATRIX = {
    "rental_item": {"finance_manager": {"read", "create", "write", "export", "print"}, "sales_manager": _R, "head_office": _R, "rental_staff": {"read"}},
    "rental_booking": {"sales_manager": _DOC, "finance_manager": _RP | {"cancel"}, "head_office": _RP, "rental_staff": {"read"}},
    "rental_checkout": {"sales_manager": _DOC, "finance_manager": _RP, "head_office": _RP, "rental_staff": _STAFF | {"submit"}},
    # warehouse staff fill the return checklist; its submit pays the deposit back, so it stays the admin's
    "rental_return": {"sales_manager": _DOC - {"cancel", "amend"}, "finance_manager": _RP | {"cancel", "amend"}, "head_office": _RP, "rental_staff": _STAFF},
    # written only by the flows of whoever submits or cancels (its fields are read_only)
    "rental_schedule": {"sales_manager": {"read", "create", "write", "export"}, "finance_manager": {"read", "write", "export"}, "head_office": _R, "rental_staff": {"read", "write"}},
    "rental_unit": {"finance_manager": {"read", "create", "write", "export", "print"}, "sales_manager": {"read", "create", "write", "export"},
                    "head_office": _R, "rental_staff": {"read", "write", "export"}},
    "rental_guard": {"sales_manager": {"create"}, "finance_manager": {"create"}, "rental_staff": {"create"}},   # never stored (a flow's refusal)
}
MEMBERS = [("sm", "sales_manager"), ("fm", "finance_manager"), ("hq", "head_office"), ("gd", "rental_staff")]


def bind(m):
    for k in ("ok", "fail", "gap", "check", "eq", "CTX", "Api", "Kernel", "ROOT", "denied", "dashboard", "num", "chart_map", "secrets", "messages"):
        H[k] = getattr(m, k)
    globals().update(H)
    m.SITE_ORGS["demo-sewa"] = [("sewa_contoh", "demo-sewa")]
    # the shared matrix check (scenario_permissions) runs for the two counter roles; head office here only reads the
    # rental (its payment and goods-receipt grants belong to outlet sites) and is checked in scenario_rental_roles
    m.SITE_MEMBERS["demo-sewa"] = [MEMBERS[0], MEMBERS[1], MEMBERS[3]]
    m.MATRIX.update(MATRIX)


def rec(x):
    return (x or {}).get("record", x) or {}


def rules(resp):
    return [e.get("rule") for e in (resp or {}).get("errors", [])] if isinstance(resp, dict) else []


def setup_demo_sewa(api, k):
    st, me = api.call("GET", "/auth/me")
    org = ((me or {}).get("org") or {}).get("id") or "sewa_contoh"
    CTX.update(kernel=k, org=org)
    pw = secrets.token_urlsafe(18)   # throwaway password of a throwaway database
    for (key, role), (email, rec_, name) in zip(MEMBERS, [("dimas@contohsewa.test", "USR-002", "Dimas Contoh"),
                                                          ("wulan@contohsewa.test", "USR-003", "Wulan Contoh"),
                                                          ("rina.hq@contohsewa.test", "", "Rina Contoh"),
                                                          ("agus.gudang@contohsewa.test", "USR-004", "Agus Contoh")]):
        code, out = k.user_add(email, role, org, name, record=rec_, password=pw)
        cl = Api.login(k.base, email, pw) if code == 0 else None
        if cl is None:
            CTX["login_error"] = f"{email}: " + (out if code else "login refused")
            fail("setup: add and sign in the members", CTX["login_error"])
            return
        CTX[key] = cl


def booking(cl, item, qty, start, days, customer=BUDI, method="cash", **extra):
    """A draft booking; returns its record or {} (a check fails)."""
    body = dict({"customer": customer, "rental_item": item, "qty": qty, "start_date": start, "days": days, "deposit_method": method}, **extra)
    st, b = cl.create("rental_booking", body)
    if not check(f"booking {item} x{qty} {start} +{days}d: draft -> 201", st == 201, (st, b)):
        return {}
    return rec(b)


def submit(cl, model, name):
    st, resp = cl.verb(model, name, "submit")
    return st, rec(resp) if st == 200 else resp


def book(cl, item, qty, start, days, label, **kw):
    """Draft and submit; returns the submitted booking or {}."""
    b = booking(cl, item, qty, start, days, **kw)
    if not b:
        return {}
    st, r = submit(cl, "rental_booking", b["name"])
    check(f"{label}: submitted -> 200", st == 200 and r.get("docstatus") == 1, (st, r))
    return r if st == 200 else {}


def refused(cl, item, qty, start, days, label, reason="over_capacity", **kw):
    b = booking(cl, item, qty, start, days, **kw)
    if not b:
        return
    st, r = submit(cl, "rental_booking", b["name"])
    check(f"{label}: refused 422 ({reason})", st == 422 and reason in rules(r), (st, r))
    check(f"{label}: stays a draft with no schedule", cl.get("rental_booking", b["name"]).get("docstatus") == 0
          and not cl.find("rental_schedule", f"booking == '{b['name']}'"), b["name"])


def schedule(api, name):
    rows = api.find("rental_schedule", f"booking == '{name}'")
    return rows[0] if rows else {}


def gl(api, model, name, entry="post"):
    return {r["account"]: r["amount"] for r in api.find("gl_entry", f"voucher_type == '{model}' && voucher == '{name}' && entry == '{entry}'")}


def checkout(cl, b, date):
    st, c = cl.create("rental_checkout", {"booking": b, "out_date": date})
    if not check(f"checkout {b}: draft -> 201", st == 201, (st, c)):
        return 0, {}
    return submit(cl, "rental_checkout", rec(c)["name"])


def give_back(cl, b, date, damage=0, method="cash"):
    st, r = cl.create("rental_return", {"booking": b, "return_date": date, "damage_fee": damage, "refund_method": method})
    if not check(f"return {b}: draft -> 201", st == 201, (st, r)):
        return 0, {}
    return submit(cl, "rental_return", rec(r)["name"])


# ---------- scenarios

def scenario_rental_fixtures(api):
    items = {r["name"]: r for r in api.list("rental_item", limit=50)}
    eq("six rental items, the lens has one unit, chairs a hundred", (len(items), items.get(LENS, {}).get("units"), items.get(CHAIR, {}).get("units")), (6, 1, 100))
    eq("a rental item takes its name from the catalogue item", items.get(CAMERA, {}).get("title"), "Kamera mirrorless contoh A")


def scenario_rental_booking_conflict(api):
    """One lens: an overlapping booking is refused, the return day is free for the next one. Two cameras: the busiest
    day counts, not the sum of every overlapping booking."""
    sm = CTX.get("sm")
    if not sm:
        fail("rental members signed in", CTX.get("login_error"))
        return
    a = book(sm, LENS, 1, d(10), 3, "lens A (days 10-13)")
    if not a:
        return
    eq("booking A: return date = start + days, rent = 1 x 150.000 x 3, deposit 750.000", (a["end_date"], a["rent_amount"], a["deposit"]), (d(13), 450_000, 750_000))
    s = schedule(api, a["name"])
    eq("booking A: schedule booked for days 10-13", (s.get("state"), s.get("start_date"), s.get("end_date"), s.get("qty")), ("booked", d(10), d(13), 1))
    inv = api.find("sales_invoice", f"docstatus == 0 && notes.contains('({a['name']})')")
    eq("booking A: one draft rent invoice of 450.000 for the customer", [(i["customer"], i["subtotal"]) for i in inv], [(BUDI, 450_000)])
    eq("booking A: deposit journal 1-1100 Kas +750.000, 2-1400 Uang muka pelanggan -750.000", gl(api, "rental_booking", a["name"]), {"11100": 750_000, "21400": -750_000})
    refused(sm, LENS, 1, d(12), 2, "lens B (days 12-14) overlaps A")
    refused(sm, LENS, 1, d(9), 2, "lens B2 (days 9-11) overlaps the start of A")
    book(sm, LENS, 1, d(13), 2, "lens C (days 13-15) starts on A's return day")
    book(sm, LENS, 1, d(7), 3, "lens D (days 7-10) ends on A's start day", customer=FILM)
    st, b = sm.create("rental_booking", {"customer": BUDI, "rental_item": LENS, "qty": 2, "start_date": d(40), "days": 1})
    check("two lenses when the shop has one: refused on save (within_units)", st == 422 and "within_units" in rules(b), (st, b))
    # two cameras: E [20,22) and F [23,25) never overlap each other, so G [21,24) needs at most 2 on any day
    book(sm, CAMERA, 1, d(20), 2, "camera E (days 20-22)")
    book(sm, CAMERA, 1, d(23), 2, "camera F (days 23-25)")
    book(sm, CAMERA, 1, d(21), 3, "camera G (days 21-24): busiest day has 2 of 2")
    refused(sm, CAMERA, 1, d(21), 1, "camera H (day 21): E and G already out that day")
    book(sm, CAMERA, 1, d(22), 1, "camera I (day 22): E is back, only G out")


def scenario_rental_race(api):
    """Contract 0025: two bookings for the last unit submitted at the same moment never both pass."""
    sm, owner = CTX.get("sm"), api
    if not sm:
        return
    for rnd in range(3):
        start = 60 + rnd * 10
        if not book(sm, SOUND, 1, d(start), 3, f"race {rnd}: first sound system (days {start}-{start + 3})"):
            continue
        drafts = [booking(sm, SOUND, 1, d(start + 1), 2, customer=WO), booking(owner, SOUND, 1, d(start + 2), 2, customer=ARISAN)]
        if not all(drafts):
            continue
        res, gate = [None, None], threading.Barrier(2)

        def go(i, cl):
            gate.wait()
            res[i] = submit(cl, "rental_booking", drafts[i]["name"])

        ts = [threading.Thread(target=go, args=(0, sm)), threading.Thread(target=go, args=(1, owner))]
        for t in ts:
            t.start()
        for t in ts:
            t.join(60)
        codes = sorted(r[0] for r in res if r)
        losers = [r[1] for r in res if r and r[0] != 200]
        check(f"race {rnd}: exactly one of two concurrent bookings for the last unit passes (200 + 422)",
              codes == [200, 422] and all("over_capacity" in rules(x) for x in losers), res)
        live = api.find("rental_schedule", f"rental_item == '{SOUND}' && state == 'booked' && start_date >= '{d(start)}' && start_date <= '{d(start + 3)}'")
        eq(f"race {rnd}: the schedule holds 2 sound systems for the window, never 3", sum(r["qty"] for r in live), 2)


def scenario_rental_out_and_back(api):
    """Out and back with actual dates: late fee per unit per day and damage kept from the deposit, the rest paid back;
    what the deposit does not cover is a draft invoice."""
    sm = CTX.get("sm")
    if not sm:
        return
    # lamps: 2 units, back 3 days late with 100.000 damage; deposit 2 x 300.000 by transfer
    b = book(sm, LAMP, 2, d(-5), 2, "lamps x2 (days -5 to -3)", customer=FILM, method="transfer")
    if not b:
        return
    eq("lamps: deposit journal 1-1200 Bank +600.000 against 2-1400", gl(api, "rental_booking", b["name"]), {"11200": 600_000, "21400": -600_000})
    st, r = give_back(sm, b["name"], d(0))
    check("return before the equipment went out: refused (not_out)", st == 422 and "not_out" in rules(r), (st, r))
    st, c = checkout(sm, b["name"], d(-5))
    check("lamps go out on day -5 -> 200", st == 200, (st, c))
    eq("lamps: schedule out with the actual date", (schedule(api, b["name"]).get("state"), schedule(api, b["name"]).get("out_date")), ("out", d(-5)))
    st, c2 = checkout(sm, b["name"], d(-5))
    check("a second checkout of the same booking: refused (not_booked)", st == 422 and "not_booked" in rules(c2), (st, c2))
    ws = dashboard(api, "sewa_ringkas")
    late_rows = api.find("rental_schedule", f"state == 'out' && end_date < '{d(0)}'")
    if ws:
        eq("dashboard: Telat kembali counts what is still out after its return date", num(ws["late"]), len(late_rows))
        check("dashboard: the late list shows the lamps", any(x.get("booking") == b["name"] for x in ws["late_list"].get("rows", ws["late_list"].get("data", []))), ws["late_list"])
    st, ret = give_back(sm, b["name"], d(0), damage=100_000)
    if not check("lamps back on day 0 with 100.000 damage -> 200", st == 200, (st, ret)):
        return
    eq("lamps: 3 days late x 2 units x 50.000 = 300.000, damage 100.000, deposit back 200.000, nothing more to bill",
       (ret["late_days"], ret["late_fee"], ret["covered_late"], ret["covered_damage"], ret["deposit_refund"], ret["extra_due"]),
       (3, 300_000, 300_000, 100_000, 200_000, 0))
    eq("lamps: return journal 2-1400 +600.000, 4-9200 denda -400.000, 1-1100 Kas -200.000",
       gl(api, "rental_return", ret["name"]), {"21400": 600_000, "49200": -400_000, "11100": -200_000})
    s = schedule(api, b["name"])
    eq("lamps: schedule returned on day 0", (s.get("state"), s.get("returned_date"), s.get("rental_return")), ("returned", d(0), ret["name"]))
    st, r2 = give_back(sm, b["name"], d(0))
    check("a second return of the same booking: refused (not_out)", st == 422 and "not_out" in rules(r2), (st, r2))

    # tent: 1 unit, 5 days late x 200.000 = 1.000.000 against a 500.000 deposit: 500.000 billed on a draft invoice
    t = book(sm, TENT, 1, d(-6), 1, "tent (day -6 to -5)", customer=ARISAN)
    if not t:
        return
    checkout(sm, t["name"], d(-6))
    st, ret = give_back(sm, t["name"], d(0))
    if not check("tent back on day 0 -> 200", st == 200, (st, ret)):
        return
    eq("tent: late 5 days = 1.000.000, deposit 500.000 kept, nothing paid back, 500.000 still owed",
       (ret["late_days"], ret["late_fee"], ret["deposit_refund"], ret["extra_due"]), (5, 1_000_000, 0, 500_000))
    eq("tent: return journal 2-1400 +500.000 against 4-9200 -500.000 (no cash line)", gl(api, "rental_return", ret["name"]), {"21400": 500_000, "49200": -500_000})
    extra = api.find("sales_invoice", f"notes.contains('({ret['name']})')")
    eq("tent: a draft invoice of 500.000 for what the deposit did not cover", [(i["customer"], i["subtotal"], i["docstatus"]) for i in extra], [(ARISAN, 500_000, 0)])
    CTX["rental_extra_invoice"] = (ret["name"], extra[0]["name"] if extra else None)

    # chairs: 50 back on time, the whole deposit returns
    c = book(sm, CHAIR, 50, d(-2), 2, "chairs x50 (days -2 to 0)", customer=WO)
    if c:
        checkout(sm, c["name"], d(-2))
        st, ret = give_back(sm, c["name"], d(0))
        eq("chairs back on time: no fee, deposit 500.000 back in full", (st, ret.get("late_days"), ret.get("deposit_refund")), (200, 0, 500_000))
        eq("chairs: return journal 2-1400 +500.000 against Kas -500.000", gl(api, "rental_return", ret.get("name", "x")), {"21400": 500_000, "11100": -500_000})

    # the rent invoices are submitted and the lamps' paid: revenue and receivable come from invoicing as usual
    for inv in api.find("sales_invoice", "docstatus == 0 && notes.startsWith('Sewa ')")[:4]:
        st, resp = sm.verb("sales_invoice", inv["name"], "submit", {"force": True})
        check(f"rent invoice {inv['name']} submitted by Admin sewa", st == 200, (st, resp))


def scenario_rental_cancel(api):
    """A booking is cancelled only before the equipment goes out; the units free up and the deposit is paid back."""
    sm = CTX.get("sm")
    if not sm:
        return
    a = book(sm, LENS, 1, d(100), 2, "lens J (days 100-102)")
    if not a:
        return
    refused(sm, LENS, 1, d(101), 1, "lens K (day 101) while J is booked")
    st, resp = sm.verb("rental_booking", a["name"], "cancel")
    check("Admin sewa cancels lens J before it goes out -> 200", st == 200, (st, resp))
    eq("lens J: schedule cancelled", schedule(api, a["name"]).get("state"), "cancelled")
    eq("lens J: deposit journal reversed (paid back)", gl(api, "rental_booking", a["name"], "reverse"), {"11100": -750_000, "21400": 750_000})
    book(sm, LENS, 1, d(101), 1, "lens K (day 101) after J was cancelled")
    o = book(sm, CHAIR, 10, d(-1), 3, "chairs x10 (out now)")
    if o:
        checkout(sm, o["name"], d(-1))
        st, resp = sm.verb("rental_booking", o["name"], "cancel")
        check("cancelling a booking whose equipment is out: refused (in_use)", st == 422 and "in_use" in rules(resp), (st, resp))
    p = book(sm, CHAIR, 5, d(120), 1, "chairs x5 (invoice submitted)")
    if p:
        inv = api.find("sales_invoice", f"docstatus == 0 && notes.contains('({p['name']})')")
        if inv:
            sm.verb("sales_invoice", inv[0]["name"], "submit", {"force": True})
        st, resp = sm.verb("rental_booking", p["name"], "cancel")
        check("cancelling a booking whose rent invoice is submitted: refused (invoice_submitted)", st == 422 and "invoice_submitted" in rules(resp), (st, resp))


def scenario_rental_still_out(api):
    """A late unit is still out: the next booking of the only lens cannot go out until it is back; a return cannot be
    dated before the checkout."""
    sm = CTX.get("sm")
    if not sm:
        return
    l1 = book(sm, LENS, 1, d(-3), 1, "lens L1 (day -3 to -2)")
    l2 = book(sm, LENS, 1, d(-2), 2, "lens L2 (day -2 to 0), booked while L1 has not gone out")
    if not (l1 and l2):
        return
    st, c = checkout(sm, l1["name"], d(-2))
    check("lens L1 goes out on day -2 (one day after its start) -> 200", st == 200, (st, c))
    st, c = checkout(sm, l2["name"], d(-2))
    check("lens L2 cannot go out while L1 is still out (still_out)", st == 422 and "still_out" in rules(c), (st, c))
    st, r = give_back(sm, l1["name"], d(-3))
    check("lens L1 cannot come back before it went out (before_out)", st == 422 and "before_out" in rules(r), (st, r))
    st, r = give_back(sm, l1["name"], d(0))
    check("lens L1 back on day 0 (2 days late) -> 200", st == 200 and r.get("late_days") == 2, (st, r))
    st, c = checkout(sm, l2["name"], d(0))
    check("then lens L2 goes out -> 200", st == 200, (st, c))


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64   # sniffed as image/png (contract 0011 sniffs the bytes)
KAM1, KAM2, SND1, SND2 = "UNT-00001", "UNT-00002", "UNT-00003", "UNT-00004"


def attach(cl, model, name, filename):
    """Upload a small PNG to a record (contract 0011); returns the attachment id or ''."""
    st, a = cl.upload(f"/api/v1/r/{model}/{Api.q(name)}/attachments", {}, filename, PNG)
    check(f"{model} {name}: {filename} attached -> 201", st == 201 and (a or {}).get("id", "").startswith("att_"), (st, a))
    return (a or {}).get("id", "") if st == 201 else ""


def unit(api, name):
    return api.get("rental_unit", name) or {}


def handover_out(cl, b, rows, date=None, **extra):
    """A checkout draft with a checklist; returns (status, record or error body)."""
    body = dict({"booking": b, "out_date": date or d(0), "units": rows}, **extra)
    st, c = cl.create("rental_checkout", body)
    return st, rec(c) if st == 201 else c


def scenario_rental_handover(api):
    """Plan layar-kerja A6: cameras and sound systems go out and come back per unit, with a condition checklist, a
    photo and the customer's signature as attachments, and a printed BAST. Warehouse staff hand over; the checklist
    fees of the return are kept from the deposit with the late fee; a broken or lost unit cannot go out again."""
    sm, fm, gd = CTX.get("sm"), CTX.get("fm"), CTX.get("gd")
    if not (sm and fm and gd):
        fail("rental members signed in", CTX.get("login_error"))
        return
    units = {r["name"]: r for r in api.list("rental_unit", limit=50)}
    eq("fixtures: four units with codes, in the warehouse, two cameras and two sound systems",
       sorted((r["code"], r["rental_item"], r["state"], r["condition"]) for r in units.values()),
       [("KAM-A-01", CAMERA, "in", "lengkap"), ("KAM-A-02", CAMERA, "in", "lengkap"), ("SND-01", SOUND, "in", "lengkap"), ("SND-02", SOUND, "in", "lengkap")])
    st, resp = gd.create("rental_booking", {"customer": BUDI, "rental_item": CAMERA, "qty": 1, "start_date": d(200), "days": 1})
    check("Staf gudang may not book -> 403", denied(st, resp), (st, resp))

    # two cameras for days -3 to -1, deposit 2 x 1.000.000 in cash
    b = book(sm, CAMERA, 2, d(-3), 2, "cameras x2 (days -3 to -1)", customer=FILM)
    if not b:
        return
    st, r = handover_out(gd, b["name"], [])
    check("checkout of a tracked item without a checklist: refused (units_required)", st == 422 and "units_required" in rules(r), (st, r))
    st, r = handover_out(gd, b["name"], [{"unit": KAM1}])
    check("checklist with 1 unit for a booking of 2: refused (units_match_qty)", st == 422 and "units_match_qty" in rules(r), (st, r))
    st, r = handover_out(gd, b["name"], [{"unit": KAM1}, {"unit": SND1}])
    check("a sound system on a camera checklist: refused (units_of_item)", st == 422 and "units_of_item" in rules(r), (st, r))
    st, r = handover_out(gd, b["name"], [{"unit": KAM1}, {"unit": KAM1}])
    check("the same unit twice: refused (units_unique)", st == 422 and "units_unique" in rules(r), (st, r))
    st, r = handover_out(gd, b["name"], [{"unit": KAM1}, {"unit": KAM2, "condition": "rusak"}])
    check("a unit marked broken at checkout cannot go out (unit_fit)", st == 422 and "unit_fit" in rules(r), (st, r))
    st, c = handover_out(gd, b["name"], [{"unit": KAM1, "notes": "baterai 2, tas"}, {"unit": KAM2, "condition": "lecet", "notes": "lecet sisi kiri"}],
                         d(-3), handed_by="Agus Contoh")
    if not check("Staf gudang drafts the camera checkout with a two-unit checklist -> 201", st == 201, (st, c)):
        return
    eq("checklist rows carry the unit code and serial number", [(x.get("unit_code"), x.get("serial_no")) for x in c.get("units", [])],
       [("KAM-A-01", "CTH-KA-0001"), ("KAM-A-02", "CTH-KA-0002")])
    st, r = submit(gd, "rental_checkout", c["name"])
    check("submit without the customer's name: refused (signed_by_required)", st == 422 and "signed_by_required" in rules(r), (st, r))
    st, r = gd.patch("rental_checkout", c["name"], {"photo": "foto-kamera.jpg"})
    check("a photo that is not an attachment id: refused (photo_is_attachment)", st == 422 and "photo_is_attachment" in rules(r), (st, r))
    photo, sig = attach(gd, "rental_checkout", c["name"], "kamera-keluar.png"), attach(gd, "rental_checkout", c["name"], "ttd-penyewa.png")
    st, r = gd.patch("rental_checkout", c["name"], {"photo": photo, "signature": sig})
    check("a signature without the signer's name: refused (signature_needs_name)", st == 422 and "signature_needs_name" in rules(r), (st, r))
    st, r = gd.patch("rental_checkout", c["name"], {"photo": photo, "signature": sig, "signed_by": "Sari Contoh (Komunitas Film)"})
    check("photo, signature and signer saved -> 200", st == 200, (st, r))
    st, out = submit(gd, "rental_checkout", c["name"])
    if not check("Staf gudang submits the camera checkout -> 200", st == 200 and out.get("docstatus") == 1, (st, out)):
        return
    st, atts = gd.call("GET", f"/api/v1/r/rental_checkout/{Api.q(out['name'])}/attachments")
    eq("photo and signature follow the checkout to its number (attachments of the record)",
       sorted(a["id"] for a in (atts or {}).get("data", [])), sorted([photo, sig]))
    eq("checkout keeps the attachment ids in photo and signature", (out.get("photo"), out.get("signature")), (photo, sig))
    u1, u2 = unit(api, KAM1), unit(api, KAM2)
    eq("both cameras are out with this booking; KAM-A-02 noted scratched",
       [(u.get("state"), u.get("booking"), u.get("condition"), u.get("last_checkout")) for u in (u1, u2)],
       [("out", b["name"], "lengkap", out["name"]), ("out", b["name"], "lecet", out["name"])])
    eq("schedule out with the checkout", (schedule(api, b["name"]).get("state"), schedule(api, b["name"]).get("checkout")), ("out", out["name"]))
    st, html, ctype = gd.print_html("rental_checkout", out["name"])
    check("BAST keluar prints for Staf gudang -> 200 html", st == 200 and "text/html" in ctype, (st, ctype))
    for want in ("Berita acara serah terima alat", out["name"], "KAM-A-01", "CTH-KA-0002", "Lecet", "lecet sisi kiri", "Sari Contoh (Komunitas Film)", "Agus Contoh", sig):
        check(f"BAST keluar shows {want!r}", want in html, want)

    # sound systems: one out on a short booking, the next booking of the same unit cannot take it while it is out
    s1 = book(sm, SOUND, 1, d(-2), 1, "sound S1 (day -2 to -1)", customer=WO)
    s2 = book(sm, SOUND, 1, d(-1), 1, "sound S2 (day -1 to 0)", customer=ARISAN)
    if not (s1 and s2):
        return
    st, c1 = handover_out(gd, s1["name"], [{"unit": SND1}], d(-2), signed_by="Pak Contoh WO")
    st, o1 = submit(gd, "rental_checkout", c1.get("name", "x"))
    check("sound S1 goes out with SND-01 -> 200", st == 200, (st, o1))
    st, c2 = handover_out(gd, s2["name"], [{"unit": SND1}], d(-1), signed_by="Ibu Contoh")
    st, r = submit(gd, "rental_checkout", c2.get("name", "x"))
    check("sound S2 cannot take SND-01 while it is out with S1 (unit_busy)", st == 422 and "unit_busy" in rules(r), (st, r))
    st, r = gd.patch("rental_checkout", c2.get("name", "x"), {"units": [{"unit": SND2}]})
    st, o2 = submit(gd, "rental_checkout", c2.get("name", "x"))
    check("sound S2 goes out with SND-02 -> 200", st == 200, (st, o2))

    # cameras back on day 0 (1 day late): KAM-A-02 broken, 250.000 repair on the checklist
    st, rt = gd.create("rental_return", {"booking": b["name"], "return_date": d(0), "units": [{"unit": KAM1}]})
    check("return checklist with 1 of 2 units: refused (units_match_qty)", st == 422 and "units_match_qty" in rules(rt), (st, rt))
    st, rt = gd.create("rental_return", {"booking": b["name"], "return_date": d(0), "received_by": "Agus Contoh",
                                         "units": [{"unit": KAM1}, {"unit": KAM2, "condition": "rusak", "fee": 250_000, "notes": "layar retak"}]})
    if not check("Staf gudang drafts the camera return with the checklist -> 201", st == 201, (st, rt)):
        return
    rt = rec(rt)
    eq("return rows show the condition at checkout and now", [(x.get("unit_code"), x.get("out_condition"), x.get("condition"), x.get("fee")) for x in rt.get("units", [])],
       [("KAM-A-01", "lengkap", "lengkap", 0), ("KAM-A-02", "lecet", "rusak", 250_000)])
    st, r = submit(gd, "rental_return", rt["name"])
    check("Staf gudang may not submit a return (it pays the deposit back) -> 403", denied(st, r), (st, r))
    st, r = submit(sm, "rental_return", rt["name"])
    check("submit without the customer's name: refused (signed_by_required)", st == 422 and "signed_by_required" in rules(r), (st, r))
    sig2 = attach(gd, "rental_return", rt["name"], "ttd-kembali.png")
    gd.patch("rental_return", rt["name"], {"signed_by": "Sari Contoh (Komunitas Film)", "signature": sig2})
    st, ret = submit(sm, "rental_return", rt["name"])
    if not check("Admin sewa submits the camera return -> 200", st == 200, (st, ret)):
        return
    eq("cameras: 1 day late x 2 x 150.000 = 300.000, checklist damage 250.000, deposit 2.000.000, 1.450.000 back",
       (ret["late_fee"], ret["unit_damage"], ret["total_damage"], ret["covered_late"], ret["covered_damage"], ret["deposit_refund"], ret["extra_due"]),
       (300_000, 250_000, 250_000, 300_000, 250_000, 1_450_000, 0))
    eq("cameras: return journal 2-1400 +2.000.000, 4-9200 -550.000, Kas -1.450.000",
       gl(api, "rental_return", ret["name"]), {"21400": 2_000_000, "49200": -550_000, "11100": -1_450_000})
    eq("both cameras back in the warehouse, KAM-A-02 recorded broken",
       [(unit(api, n).get("state"), unit(api, n).get("booking"), unit(api, n).get("condition"), unit(api, n).get("last_return")) for n in (KAM1, KAM2)],
       [("in", "", "lengkap", ret["name"]), ("in", "", "rusak", ret["name"])])
    st, html, _ = sm.print_html("rental_return", ret["name"])
    check("BAST kembali prints -> 200", st == 200, st)
    for want in ("Alat kembali " + ret["name"], "KAM-A-02", "Rusak", "layar retak", "Rp 250.000", "Rp 300.000", "Rp 1.450.000", "Telat 1 hari"):
        check(f"BAST kembali shows {want!r}", want in html, want)

    # the broken camera cannot go out until it is repaired; KAM-A-01 goes out on the next booking
    nb = book(sm, CAMERA, 1, d(1), 1, "camera N (day 1)", customer=BUDI)
    if not nb:
        return
    st, cn = handover_out(gd, nb["name"], [{"unit": KAM2}], d(0), signed_by="Budi Contoh")
    st, r = submit(gd, "rental_checkout", cn.get("name", "x"))
    check("the camera recorded broken cannot go out (unit_busy)", st == 422 and "unit_busy" in rules(r), (st, r))
    gd.patch("rental_checkout", cn.get("name", "x"), {"units": [{"unit": KAM1}]})
    st, on = submit(gd, "rental_checkout", cn.get("name", "x"))
    check("camera N goes out with KAM-A-01 -> 200", st == 200, (st, on))
    st, r = fm.verb("rental_return", ret["name"], "cancel")
    check("the camera return cannot be cancelled while KAM-A-01 is out again (unit_moved)", st == 422 and "unit_moved" in rules(r), (st, r))
    st, r = gd.verb("rental_checkout", on.get("name", "x"), "cancel")
    check("Staf gudang may not cancel a checkout -> 403", denied(st, r), (st, r))
    st, r = sm.verb("rental_checkout", on.get("name", "x"), "cancel")
    check("Admin sewa cancels camera N's checkout -> 200", st == 200, (st, r))
    eq("KAM-A-01 back in the warehouse, schedule of N booked again", (unit(api, KAM1).get("state"), schedule(api, nb["name"]).get("state")), ("in", "booked"))
    st, r = fm.verb("rental_return", ret["name"], "cancel")
    check("Keuangan cancels the camera return -> 200", st == 200, (st, r))
    eq("cancelled return: both cameras out again with the booking, KAM-A-02 back to its checkout condition (lecet)",
       [(unit(api, n).get("state"), unit(api, n).get("booking"), unit(api, n).get("condition")) for n in (KAM1, KAM2)],
       [("out", b["name"], "lengkap"), ("out", b["name"], "lecet")])
    eq("cancelled return: journal reversed", gl(api, "rental_return", ret["name"], "reverse"), {"21400": -2_000_000, "49200": 550_000, "11100": 1_450_000})
    st, am = fm.verb("rental_return", ret["name"], "amend")
    am = rec(am)
    if check("Keuangan amends the return -> 200/201", st in (200, 201) and am.get("name"), (st, am)):
        eq("the amended return keeps the checklist", [(x.get("unit"), x.get("condition"), x.get("fee")) for x in am.get("units", [])],
           [(KAM1, "lengkap", 0), (KAM2, "rusak", 250_000)])
        st, r2 = submit(sm, "rental_return", am["name"])
        check("Admin sewa submits the amended return -> 200", st == 200 and r2.get("deposit_refund") == 1_450_000, (st, r2))
    st, r = fm.patch("rental_unit", KAM2, {"condition": "lengkap", "notes": "layar diganti di servis"})
    check("Keuangan marks KAM-A-02 repaired -> 200", st == 200 and rec(r).get("condition") == "lengkap", (st, r))
    st, r = gd.patch("rental_unit", KAM2, {"state": "out"})
    eq("a hand edit of a unit's position is ignored (read_only)", unit(api, KAM2).get("state"), "in")
    st, r = sm.create("rental_unit", {"rental_item": CAMERA, "code": "KAM-A-99", "state": "out", "booking": b["name"]})
    eq("a new unit cannot be created as out with a booking (read_only)", (st, rec(r).get("state"), rec(r).get("booking")), (201, "in", ""))
    if st == 201:
        api.call("DELETE", f"/api/v1/r/rental_unit/{rec(r)['name']}")
    st, rt = gd.create("rental_return", {"booking": nb["name"], "return_date": d(1), "signed_by": "Budi Contoh"})
    st, r = submit(sm, "rental_return", rec(rt).get("name", "x"))
    check("a return of a booking that has not gone out is refused (not_out)", st == 422 and "not_out" in rules(r), (st, r))
    if rec(rt).get("name"):
        sm.call("DELETE", f"/api/v1/r/rental_return/{Api.q(rec(rt)['name'])}")

    # sound S1 back with SND-01 lost: 3.000.000 on the checklist, more than the deposit, the rest on a draft invoice
    st, r1 = gd.create("rental_return", {"booking": s1["name"], "return_date": d(0), "signed_by": "Pak Contoh WO",
                                         "units": [{"unit": SND2}]})
    st, r = submit(sm, "rental_return", rec(r1).get("name", "x"))
    check("S1 cannot return SND-02, which is out with S2 (unit_not_out)", st == 422 and "unit_not_out" in rules(r), (st, r))
    gd.patch("rental_return", rec(r1).get("name", "x"), {"units": [{"unit": SND1, "condition": "hilang", "fee": 3_000_000, "notes": "tidak kembali"}]})
    st, sr = submit(sm, "rental_return", rec(r1).get("name", "x"))
    if check("S1 returns with SND-01 recorded lost -> 200", st == 200, (st, sr)):
        eq("S1: late 1 day 200.000 + lost 3.000.000 against 1.000.000 deposit: nothing back, 2.200.000 billed",
           (sr["covered_late"], sr["covered_damage"], sr["deposit_refund"], sr["extra_due"]), (200_000, 800_000, 0, 2_200_000))
        inv = api.find("sales_invoice", f"notes.contains('({sr['name']})')")
        eq("S1: a draft invoice of 2.200.000 for the loss", [(i["customer"], i["subtotal"], i["docstatus"]) for i in inv], [(WO, 2_200_000, 0)])
        eq("SND-01 back on the books as lost", (unit(api, SND1).get("state"), unit(api, SND1).get("condition")), ("in", "hilang"))

    # untracked items keep going out by count (lens, lamps, tent, chairs; earlier scenarios)
    ws = dashboard(gd, "alat_hari_ini")
    if ws:
        rows = api.list("rental_schedule", limit=500)
        eq("alat_hari_ini: Siapkan keluar = booked and due", num(ws["to_go_out"]), sum(1 for r in rows if r["state"] == "booked" and r["start_date"] <= d(0)))
        eq("alat_hari_ini: Harus kembali hari ini", num(ws["due_back"]), sum(1 for r in rows if r["state"] == "out" and r["end_date"] == d(0)))
        eq("alat_hari_ini: Telat kembali", num(ws["late"]), sum(1 for r in rows if r["state"] == "out" and r["end_date"] < d(0)))
        eq("alat_hari_ini: units broken or lost = SND-01", num(ws["units_to_check"]), 1)
        out_rows = ws["units_out"].get("rows", ws["units_out"].get("data", []))
        eq("alat_hari_ini: units out = the cameras (amended return keeps them in) and SND-02",
           sorted(x.get("code") for x in out_rows), ["SND-02"])
    st, resp = CTX["hq"].call("GET", "/api/v1/dashboards/alat_hari_ini")
    check("Kantor pusat opens alat_hari_ini -> 200", st == 200, (st, resp))


def scenario_rental_invoice_guards(api):
    """Audit finding 1 and 2: a rental invoice is submitted once, only for a live document, and its notes are locked."""
    sm = CTX.get("sm")
    if not sm:
        return
    b = book(sm, CHAIR, 3, d(130), 1, "chairs x3 (draft rent invoice left by a cancel)")
    if not b:
        return
    draft = api.find("sales_invoice", f"docstatus == 0 && notes.contains('({b['name']})')")
    if not check("chairs x3: one draft rent invoice", len(draft) == 1, draft):
        return
    st, resp = sm.patch("sales_invoice", draft[0]["name"], {"notes": "Sewa kursi"})
    check("the notes of a rent invoice cannot be edited (invoice_tag)", st == 422 and "invoice_tag" in rules(resp), (st, resp))
    st, resp = sm.verb("rental_booking", b["name"], "cancel")
    check("chairs x3: booking cancelled while its invoice is a draft -> 200", st == 200, (st, resp))
    st, resp = sm.verb("sales_invoice", draft[0]["name"], "submit", {"force": True})
    check("the left-over draft of a cancelled booking cannot be submitted (invoice_stale)", st == 422 and "invoice_stale" in rules(resp), (st, resp))
    st, am = sm.verb("rental_booking", b["name"], "amend")
    am = rec(am)
    if check("chairs x3: the booking is amended (new dates come this way) -> 200/201", st in (200, 201) and am.get("name"), (st, am)):
        st, sb = submit(sm, "rental_booking", am["name"])
        check("the amended booking is submitted", st == 200, (st, sb))
        am = sb if st == 200 else am   # a draft gets its number on submit
        new = api.find("sales_invoice", f"docstatus == 0 && notes.contains('({am['name']})')")
        if check("the amended booking has its own draft invoice", len(new) == 1, new):
            st, resp = sm.verb("sales_invoice", new[0]["name"], "submit", {"force": True})
            check("its invoice is submitted -> 200", st == 200, (st, resp))
            st, twin = sm.create("sales_invoice", {"customer": BUDI, "notes": f"Sewa kursi ({am['name']})", "items": [{"item": "BRG-0005", "qty": 3, "rate": 5_000}]})
            st, resp = sm.verb("sales_invoice", rec(twin).get("name", "x"), "submit", {"force": True})
            check("a second invoice with the same booking number is refused (invoice_stale)", st == 422 and "invoice_stale" in rules(resp), (st, resp))


def scenario_rental_pkp(api):
    """Audit finding 3: a PKP business keeps nothing from the deposit (the fee carries PPN): the deposit goes back in
    full and the whole fee is a draft invoice with PPN. Runs on a throwaway copy of the site with pkp: true."""
    state, k = None, None
    try:
        state = tempfile.mkdtemp(prefix="day-one-sewa-pkp-")
        site_dir = os.path.join(state, "site")
        shutil.copytree(os.path.join(ROOT, "examples", "sites", "demo-sewa"), site_dir)
        cfg = os.path.join(site_dir, "config.rk")
        text = open(cfg).read()
        assert "      tax: {pkp: false}" in text
        open(cfg, "w").write(text.replace("      tax: {pkp: false}", "      tax: {pkp: true}").replace("pkp: false}", "pkp: true}"))
        k = Kernel("demo-sewa", state, site_dir=site_dir)
        k.start()
        own = Api(k.base)
        b = book(own, LAMP, 1, d(-4), 1, "PKP: lamp x1 (day -4 to -3)")
        if not b:
            return
        checkout(own, b["name"], d(-4))
        st, ret = give_back(own, b["name"], d(0), damage=20_000)
        if not check("PKP: lamp back 3 days late -> 200", st == 200, (st, ret)):
            return
        eq("PKP: nothing kept from the deposit, 300.000 back, fee 3 x 50.000 + 20.000 = 170.000 billed",
           (ret["deposit_kept"], ret["covered_late"], ret["covered_damage"], ret["deposit_refund"], ret["extra_due"]), (False, 0, 0, 300_000, 170_000))
        eq("PKP: return journal 2-1400 +300.000 against Kas -300.000 (no 4-9200)", gl(own, "rental_return", ret["name"]), {"21400": 300_000, "11100": -300_000})
        inv = own.find("sales_invoice", f"notes.contains('({ret['name']})')")
        eq("PKP: the fee invoice carries PPN 12% of DPP 11/12", [(i["subtotal"], i["tax_rate"], i["ppn"]) for i in inv], [(170_000, 12, round(170_000 * 11 / 12 * 12 / 100))])
    finally:
        if k is not None:
            k.stop()
        if state is not None:
            shutil.rmtree(state, ignore_errors=True)


def scenario_rental_roles(api):
    """Per role: Admin sewa runs the counter but cannot change rates or undo a return; Keuangan sets rates and
    corrects returns but does not book; Kantor pusat reads."""
    sm, fm, hq = CTX.get("sm"), CTX.get("fm"), CTX.get("hq")
    if not (sm and fm and hq):
        fail("rental members signed in", CTX.get("login_error"))
        return
    st, resp = sm.patch("rental_item", LENS, {"daily_rate": 1})
    check("Admin sewa may not change a rate -> 403", denied(st, resp), (st, resp))
    st, resp = fm.patch("rental_item", LENS, {"notes": "Tarif diperiksa keuangan"})
    check("Keuangan updates a rental item -> 200", st == 200, (st, resp))
    for cl, who in ((fm, "Keuangan"), (hq, "Kantor pusat")):
        st, resp = cl.create("rental_booking", {"customer": BUDI, "rental_item": CHAIR, "qty": 1, "start_date": d(150), "days": 1})
        check(f"{who} may not book -> 403", denied(st, resp), (st, resp))
    st, mm = hq.call("GET", "/api/v1/meta")
    wrong = []
    for n, row in MATRIX.items():
        can = ((mm or {}).get("models", {}).get(n) or {}).get("can", {})
        got = {x for x in ("read", "create", "write", "delete", "submit", "cancel", "amend", "export", "import", "print") if can.get(x)}
        if got != row.get("head_office", set()):
            wrong.append(f"{n}: got {sorted(got)} want {sorted(row.get('head_office', set()))}")
    check("Kantor pusat: /meta can matches the matrix on the rental models", not wrong, wrong)
    st, resp = hq.call("GET", "/api/v1/dashboards/sewa_ringkas")
    check("Kantor pusat opens the dashboard -> 200", st == 200, (st, resp))
    st, resp = sm.call("GET", "/api/v1/r/gl_entry?limit=200&filter=" + Api.q("voucher_type == 'timesheet'"))
    check("Admin sewa reads no journal lines outside its own documents", st in (200, 403) and not (resp or {}).get("data"), (st, resp))
    # a schedule's state is the flows' to write: a hand patch leaves it as it is
    rows = api.find("rental_schedule", "state == 'booked'")
    if rows:
        sm.patch("rental_schedule", rows[0]["name"], {"state": "returned"})
        eq("a hand edit of a schedule's state is ignored (read_only)", api.get("rental_schedule", rows[0]["name"]).get("state"), "booked")
    draft = booking(sm, CHAIR, 1, d(160), 1)
    if draft:
        st, resp = sm.create("rental_schedule", {"booking": draft["name"], "rental_item": CHAIR, "qty": 100, "start_date": d(160), "end_date": d(161)})
        check("a schedule cannot be made by hand for a draft booking (it would hold units) -> 422", st == 422, (st, resp))
    # undo a return: Admin sewa may not, Keuangan may; the tent's extra invoice must be cancelled first once submitted
    ret, inv = CTX.get("rental_extra_invoice", (None, None))
    if not ret:
        return
    st, resp = sm.verb("rental_return", ret, "cancel")
    check("Admin sewa may not cancel a return -> 403", denied(st, resp), (st, resp))
    if inv:
        st, resp = fm.verb("sales_invoice", inv, "submit", {"force": True})
        check("Keuangan submits the tent's extra invoice", st == 200, (st, resp))
        inv = rec(resp).get("name", inv)   # a draft gets its number on submit
        st, resp = fm.verb("rental_return", ret, "cancel")
        check("cancelling a return whose extra invoice is submitted: refused (invoice_submitted)", st == 422 and "invoice_submitted" in rules(resp), (st, resp))
        st, resp = fm.verb("sales_invoice", inv, "cancel")
        check("Keuangan cancels the extra invoice first", st == 200, (st, resp))
    st, resp = fm.verb("rental_return", ret, "cancel")
    check("Keuangan cancels the tent's return -> 200", st == 200, (st, resp))
    b = api.get("rental_return", ret).get("booking")
    eq("tent: schedule back to out, its return journal reversed", (schedule(api, b).get("state"), gl(api, "rental_return", ret, "reverse")),
       ("out", {"21400": -500_000, "49200": 500_000}))


def scenario_rental_dashboard(api):
    ws = dashboard(api, "sewa_ringkas")
    if not ws:
        return
    rows = api.list("rental_schedule", limit=500)
    eq("dashboard: Telat kembali = schedules out past their return date", num(ws["late"]), sum(1 for r in rows if r["state"] == "out" and r["end_date"] < d(0)))
    eq("dashboard: Jadwal keluar hari ini = booked and due out", num(ws["going_out_today"]), sum(1 for r in rows if r["state"] == "booked" and r["start_date"] <= d(0)))
    eq("dashboard: Deposit dipegang = deposits of bookings not yet back", num(ws["deposit_held"]), sum(r["deposit"] for r in rows if r["state"] in ("booked", "out")))
    eq("dashboard: units out per item", chart_map(ws["out_by_item"]),
       {k: v for k, v in _sum_by(rows, lambda r: r["state"] == "out").items()})


def _sum_by(rows, cond):
    out = {}
    for r in rows:
        if cond(r):
            out[r["rental_item"]] = out.get(r["rental_item"], 0) + r["qty"]
    return out


SCENARIOS = [scenario_rental_fixtures, scenario_rental_booking_conflict, scenario_rental_race, scenario_rental_out_and_back,
             scenario_rental_cancel, scenario_rental_still_out, scenario_rental_handover, scenario_rental_invoice_guards, scenario_rental_pkp, scenario_rental_roles, scenario_rental_dashboard]
