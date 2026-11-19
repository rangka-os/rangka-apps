"""Day-one scenarios of the janji_temu piece (module appointments, rangka-apps#118) on demo-salon: a salon books
services per staff member and a cafe corner takes table reservations, with a deposit, the day's work screen, billing at
the cashier and a WhatsApp reminder the day before.

Loaded by tests/day_one.py (bind() registers the site): `python3 tests/day_one.py --site demo-salon`. Fixtures are
synthetic (examples/fixtures/demo-salon). Covered: catalogue prices on services; clash per staff member and per
resource (touching ends allowed), opening hours, time off, two submits at the same moment (contract 0025 lock); the
deposit as a draft invoice by an invoicing role only, its tag lock and its once/amount check; staff moving the day's
slot one step at a time; the cashier's "Buat tagihan" drafting the pos_sale at catalogue price with the submitted
deposit taken off (deposit invoice + receipt == catalogue price), paying it, and the deposit invoice then locked;
billing with an unsubmitted deposit (full price, deposit invoice refused afterwards); a deleted draft bill puts the
slot back; cancel only while booked and the deposit not invoiced; no-show deposit forfeited by default or flagged
for refund by the owner's setting; table reservations (seats, clash, never billed from the screen); the reminder
through module whatsapp_booking (mock host connector, once, skipped without a number); the work screens and the
permission matrix.

Members: Maya (head_office), Nina (cashier, Salon Dago), Oki and Putri (service_staff), Rudi (sales_manager).
"""
import datetime
import json
import subprocess
import threading

H = {}
SITE, ORG = "demo-salon", "salon_contoh"
SALON, KAFE = "OUT-01", "OUT-02"
POTONG, CREAM, FACIAL, WARNA = "LYN-001", "LYN-002", "LYN-003", "LYN-004"
KURSI1, KURSI2, RUANG, MEJA1, MEJA2 = "TMP-001", "TMP-002", "TMP-003", "TMP-004", "TMP-005"
AYU, BIMA, CITRA, DARMA = "CUST-0001", "CUST-0002", "CUST-0003", "CUST-0004"
OKI, PUTRI = "USR-003", "USR-004"

_R = {"read"}
_MASTER = {"head_office": {"read", "create", "write", "delete", "export", "print"}, "sales_manager": {"read", "create", "write", "delete", "export", "print"},
           "finance_manager": _R, "cashier": _R, "service_staff": _R}
_ALL = {"read", "create", "write", "delete", "submit", "cancel", "amend", "export", "print"}
MATRIX = {
    "booking_resource": _MASTER,
    "booking_service": _MASTER,
    "resource_time_off": {"head_office": {"read", "create", "write", "delete", "export"}, "sales_manager": {"read", "create", "write", "delete", "export"},
                          "finance_manager": _R, "cashier": _R, "service_staff": _R},
    "appointment_setting": {"head_office": {"read", "create", "write"}, "sales_manager": _R, "finance_manager": _R, "cashier": _R, "service_staff": _R},
    "appointment": {"head_office": _ALL, "sales_manager": _ALL, "finance_manager": {"read", "export", "print"},
                    "cashier": _ALL - {"export"}, "service_staff": _R},
    "appointment_slot": {"head_office": {"read", "create", "write", "export"}, "sales_manager": {"read", "create", "write", "export"},
                         "finance_manager": {"read", "write", "export"}, "cashier": {"read", "create", "write"}, "service_staff": {"read", "write"}},
    "appointment_guard": {r: {"create"} for r in ("head_office", "sales_manager", "finance_manager", "cashier", "service_staff")},   # never stored
}
MEMBERS = [("maya", "head_office", "maya@contohsalon.test", "Maya Contoh", "", "USR-001"),
           ("nina", "cashier", "nina@contohsalon.test", "Nina Contoh", SALON, "USR-002"),
           ("oki", "service_staff", "oki@contohsalon.test", "Oki Contoh", SALON, OKI),
           ("putri", "service_staff", "putri@contohsalon.test", "Putri Contoh", SALON, PUTRI),
           ("rudi", "sales_manager", "rudi@contohsalon.test", "Rudi Contoh", "", "USR-005")]


def site_env(m):
    """The kernel of this site serves the host connector rangka_whatsapp (the mock: nothing leaves the machine)."""
    _, binary, _ = m.lib_env()
    key = subprocess.run([binary, "keygen"], capture_output=True, text=True).stdout.strip()
    return {"RANGKA_HOST_CONNECTORS": "rangka_whatsapp", "RANGKA_MASTER_KEY": key}


def bind(m):
    for k in ("ok", "fail", "check", "eq", "CTX", "Api", "denied", "secrets", "kernel_today", "decide"):
        H[k] = getattr(m, k)
    globals().update(H)
    m.SITE_ORGS[SITE] = [(ORG, SITE)]
    m.SITE_ENV[SITE] = lambda: site_env(m)
    m.SITE_SETUP[SITE] = setup
    # head office is left out of the shared matrix check: its money bodies (vendor payments, purchases) need suppliers
    # a salon fixture does not have; its rights on the appointment models are checked by the scenarios
    m.SITE_MEMBERS[SITE] = [("nina", "cashier"), ("oki", "service_staff"), ("rudi", "sales_manager")]
    m.MATRIX.update(MATRIX)
    # module whatsapp (no other site with the matrix check installs it): its own permission lists
    m.MATRIX.setdefault("whatsapp_message", {"head_office": {"read", "create", "write", "export"}, "cashier": {"read", "create"}, "kitchen": {"read", "create"},
                                              "finance_manager": {"read", "create", "write", "export"}, "sales_manager": {"read", "create", "write", "export"},
                                              "sales_rep": {"read", "create", "write"}})
    m.MATRIX.setdefault("whatsapp_opt_out", {"head_office": {"read", "create", "export"}, "sales_manager": {"read", "create"}, "finance_manager": {"read", "create"},
                                              "sales_rep": {"read", "create"}, "purchasing_manager": {"read", "create"}, "project_admin": {"read", "create"},
                                              "course_admin": {"read", "create"}, "cashier": _R, "kitchen": _R})


def rec(x):
    return (x or {}).get("record", x) or {}


def rules(resp):
    return [e.get("rule") for e in (resp or {}).get("errors", [])] if isinstance(resp, dict) else []


def setup(api, k):
    pw = secrets.token_urlsafe(18)   # throwaway password of a throwaway database
    CTX.update(kernel=k, org=ORG)
    for key, role, email, name, outlets, record in MEMBERS:
        code, out = k.user_add(email, role, ORG, name, outlets=outlets, record=record, password=pw)
        cl = Api.login(k.base, email, pw) if code == 0 else None
        if cl is None:
            CTX["login_error"] = f"{email}: " + (out if code else "login refused")
            fail("appointments setup: add and sign in the members", CTX["login_error"])
            return
        CTX[key] = cl
    CTX["owner"] = Api.login(k.base, k.owner_email, k.owner_pw)


# ---------- helpers

def today():
    return kernel_today().isoformat()


def tomorrow():
    return (kernel_today() + datetime.timedelta(days=1)).isoformat()


def book(cl, customer, start, lines=(), staff="", resource="", day=None, submit=True, **extra):
    """Create (and submit) an appointment; returns (status, record or error body) of the last call."""
    body = dict({"customer": customer, "date": day or today(), "start_time": start, "staff": staff, "resource": resource,
                 "outlet": SALON, "lines": [{"service": s} for s in lines]}, **extra)
    st, a = cl.create("appointment", body)
    if st != 201 or not submit:
        return st, (rec(a) if st == 201 else a)
    st, r = cl.verb("appointment", rec(a)["name"], "submit")
    return st, (rec(r) if st == 200 else dict(r or {}, _draft=rec(a)["name"]))


def slot_of(api, appt):
    rows = api.find("appointment_slot", f"appointment == '{appt}'")
    return rows[0] if len(rows) == 1 else {}


def move(cl, slot, state):
    return cl.patch("appointment_slot", slot["name"], {"state": state})


def deposit_invoice(api, appt):
    return [i for i in api.list("sales_invoice", limit=500) if f"({appt})" in (i.get("notes") or "")]


def open_shift(cl):
    rows = cl.find("pos_shift", "state == 'open' && cashier == user.email")
    if rows:
        return rows[0]["name"]
    st, s = cl.create("pos_shift", {"outlet": SALON, "opening_cash": 200_000})
    check("Nina opens a shift at Salon Dago -> 201", st == 201, (st, s))
    return rec(s).get("name") if st == 201 else None


# ---------- scenarios

def scenario_appt_masters(api):
    """Services take their price from the catalogue; resources keep opening hours in JJ:MM."""
    rudi = CTX.get("rudi")
    if not rudi:
        return
    s = api.get("booking_service", POTONG)
    eq("a service's price is the catalogue item's rate", s.get("price"), 75_000)
    st, r = rudi.create("booking_resource", {"title": "Kursi uji", "kind": "station", "hours": [{"weekday": "mon", "open_time": "9:00", "close_time": "17:00"}]})
    check("hours not written JJ:MM -> 422 hours_format", st == 422 and "hours_format" in rules(r), (st, r))
    st, r = CTX["oki"].create("booking_service", {"title": "Uji", "item": "BRG-0001", "duration_min": 30})
    check("a service staff member cannot add services -> 403", denied(st, r), (st, r))


def scenario_appt_salon_day(api):
    """Ayu books a cut and a creambath with Oki on chair 1 and pays a deposit; clashes, hours and time off are refused;
    Oki marks her arrived and done; Nina bills it at the counter with the deposit taken off and takes the payment."""
    rudi, nina, oki, putri, maya = (CTX.get(k) for k in ("rudi", "nina", "oki", "putri", "maya"))
    if not (rudi and nina and oki and putri and maya):
        return
    st, a = book(rudi, AYU, "10:00", [POTONG, CREAM], staff=OKI, resource=KURSI1, deposit=50_000)
    if not check("Rudi books Ayu 10:00 cut + creambath with Oki on chair 1, deposit 50.000 -> 200", st == 200, (st, a)):
        return
    eq("duration is the services' minutes and the end is computed", (a.get("duration_min"), a.get("end_time")), (105, "11:45"))
    eq("catalogue total of the services", a.get("service_total"), 195_000)
    CTX["ayu_appt"] = a["name"]
    s = slot_of(api, a["name"])
    check("the submit wrote the slot: booked, deposit draft, invoice number", s.get("state") == "booked" and s.get("deposit_state") == "draft" and s.get("deposit_invoice"), s)
    invs = deposit_invoice(api, a["name"])
    check("one draft deposit invoice of 50.000 tagged with the appointment", len(invs) == 1 and invs[0].get("docstatus") == 0 and invs[0].get("grand_total") == 50_000, invs)

    # clashes, under the flow read lock: the same staff member, the same chair, touching ends allowed
    st, r = book(rudi, BIMA, "11:00", [POTONG], staff=OKI, resource=KURSI2)
    check("Oki already has Ayu at 11:00 -> 422 staff_busy", st == 422 and "staff_busy" in rules(r), (st, r))
    st, r = book(rudi, BIMA, "11:30", [POTONG], staff=PUTRI, resource=KURSI1)
    check("chair 1 is taken until 11:45 -> 422 resource_busy", st == 422 and "resource_busy" in rules(r), (st, r))
    st, r = book(rudi, BIMA, "11:45", [POTONG], staff=OKI, resource=KURSI1)
    check("Bima at 11:45 with Oki on chair 1 (right after Ayu) -> 200", st == 200, (st, r))
    st, r = book(rudi, BIMA, "19:30", [POTONG], staff=PUTRI, resource=KURSI1)
    check("ends 20:15, chair 1 closes 20:00 -> 422 closed", st == 422 and "closed" in rules(r), (st, r))
    st, svc = maya.create("booking_service", {"title": "Smoothing lama", "item": "BRG-0004", "duration_min": 90, "active": False})
    st, r = book(rudi, BIMA, "17:00", [svc.get("record", svc).get("name", "")], staff=PUTRI, resource=KURSI2)
    check("an inactive service -> 422 services_active", st == 422 and "services_active" in rules(r), (st, r))
    st, r = book(rudi, BIMA, "17:00", [POTONG], staff=PUTRI)
    check("a cut needs a chair: booked without one -> 422 resource_needed", st == 422 and "resource_needed" in rules(r), (st, r))
    st, off = maya.create("resource_time_off", {"staff": PUTRI, "date": today(), "from_time": "13:00", "to_time": "15:00", "reason": "Izin"})
    check("Maya records Putri away 13:00-15:00 -> 201", st == 201, (st, off))
    st, r = book(rudi, BIMA, "14:00", [POTONG], staff=PUTRI, resource=KURSI2)
    check("Putri is away at 14:00 -> 422 time_off", st == 422 and "time_off" in rules(r), (st, r))
    st, r = book(nina, CITRA, "17:00", [POTONG], staff=PUTRI, resource=KURSI2, deposit=20_000)
    check("Nina (cashier) cannot take a deposit: it is an invoice -> 422 deposit_by_invoicing_role", st == 422 and "deposit_by_invoicing_role" in rules(r), (st, r))
    st, r = book(nina, BIMA, "15:00", [POTONG], staff=OKI, resource=KURSI2)
    check("Nina (front desk) books Bima 15:00 with Oki without a deposit -> 200", st == 200, (st, r))
    sl = slot_of(api, r.get("name", "")) if st == 200 else {}
    eq("the slot card names the staff member and the chair", (sl.get("staff_name"), sl.get("resource_title")), ("Oki Contoh", "Kursi 2"))
    eq("the slot card: time on its own line, then the customer, and the service", (sl.get("time_range"), sl.get("customer_line"), sl.get("services")),
       ("15:00-15:45", "Bima Contoh", "Potong rambut"))
    ayu = slot_of(api, a["name"])
    eq("Ayu's slot keeps 105 minutes (10:00-11:45) and both services", (ayu.get("end_min") - ayu.get("start_min"), ayu.get("services")), (105, "Potong rambut +1 layanan"))
    st, r = book(rudi, CITRA, "17:00", [POTONG], staff=PUTRI, resource=KURSI2, deposit=100_000)
    check("a deposit above the services' total -> 422 deposit_range", st == 422 and "deposit_range" in rules(r), (st, r))

    # two submits at the same moment for the same staff member and time: exactly one passes (contract 0025)
    drafts = []
    for cust, res in ((CITRA, KURSI2), (DARMA, RUANG)):
        st, d = book(rudi, cust, "16:00", [FACIAL] if res == RUANG else [POTONG], staff=PUTRI, resource=res, submit=False)
        drafts.append(d.get("name"))
    out = {}

    def go(n):
        out[n] = rudi.verb("appointment", n, "submit")[0]
    ts = [threading.Thread(target=go, args=(n,)) for n in drafts if n]
    [t.start() for t in ts]
    [t.join() for t in ts]
    check("two appointments for Putri at 16:00 submitted at once: one 200, one 422", sorted(out.values()) == [200, 422], out)

    # the deposit invoice: amount and once, notes locked
    st, r = rudi.create("sales_invoice", {"customer": AYU, "notes": f"Uang muka lain ({a['name']})", "items": [{"item": "BRG-0001", "qty": 1, "rate": 10_000}]})
    if check("a second invoice tagged with the appointment (10.000) is saved as a draft", st == 201, (st, r)):
        st, r2 = rudi.verb("sales_invoice", rec(r)["name"], "submit")
        check("its submit is refused: amount is not the deposit -> 422 invoice_stale", st == 422 and "invoice_stale" in rules(r2), (st, r2))
        rudi.call("DELETE", f"/api/v1/r/sales_invoice/{rec(r)['name']}")
    inv = invs[0]["name"]
    st, r = rudi.patch("sales_invoice", inv, {"notes": "Uang muka"})
    check("the deposit invoice's notes cannot be edited -> 422 invoice_tag", st == 422 and "invoice_tag" in rules(r), (st, r))
    st, r = rudi.verb("sales_invoice", inv, "submit")
    check("Rudi submits the deposit invoice -> 200", st == 200, (st, r))
    s = slot_of(api, a["name"])
    check("the slot records the deposit as invoiced, under the invoice's final number",
          s.get("deposit_state") == "invoiced" and s.get("deposit_invoice") == rec(r).get("name") and not s["deposit_invoice"].startswith("DRAFT-"), (s, rec(r).get("name")))
    inv = s.get("deposit_invoice")
    st, r = rudi.verb("appointment", a["name"], "cancel")
    check("cancel while the deposit is invoiced -> 422 deposit_invoiced", st == 422 and "deposit_invoiced" in rules(r), (st, r))

    # the day's work
    s = slot_of(api, a["name"])
    st, r = move(oki, s, "done")
    check("Oki jumps booked -> done -> 422 bad_step", st == 422 and "bad_step" in rules(r), (st, r))
    st, r = move(oki, s, "arrived")
    check("Oki marks Ayu arrived -> 200", st == 200, (st, r))
    st, r = move(oki, s, "done")
    check("Oki marks the service done -> 200", st == 200, (st, r))
    s = slot_of(api, a["name"])
    check("arrival and finish are stamped", s.get("arrived_at") and s.get("done_at") and s.get("state") == "done", s)
    st, r = move(oki, s, "billed")
    check("Oki cannot bill (no cashier shift, no sale rights)", st >= 400 and slot_of(api, a["name"]).get("state") == "done", (st, r))
    st, r = move(nina, s, "billed")
    check("Nina bills before opening a shift -> 422 no_shift", st == 422 and "no_shift" in rules(r), (st, r))
    shift = open_shift(nina)
    if not shift:
        return
    st, r = move(nina, s, "billed")
    if not check("Nina taps Buat tagihan -> 200", st == 200, (st, r)):
        return
    s = slot_of(api, a["name"])
    sale = nina.get("pos_sale", s.get("sale")) if s.get("sale") else {}
    check("a draft receipt on Nina's shift with both services at catalogue price",
          sale.get("docstatus") == 0 and sale.get("shift") == shift and sorted((l["item"], l["rate"]) for l in sale.get("items") or []) == [("BRG-0001", 75_000), ("BRG-0002", 120_000)], sale)
    eq("the deposit comes off the bill: receipt total 145.000, deposit applied 50.000",
       (sale.get("discount_amount"), sale.get("grand_total"), s.get("deposit_applied")), (50_000, 145_000, 50_000))
    check("the buyer carries the appointment tag", f"({a['name']})" in (sale.get("buyer") or ""), sale.get("buyer"))
    st, r = nina.patch("pos_sale", sale["name"], {"buyer": "Ayu"})
    check("the bill's buyer tag cannot be edited -> 422 sale_tag", st == 422 and "sale_tag" in rules(r), (st, r))
    st, r = nina.patch("pos_sale", sale["name"], {"payments": [{"method": "cash", "amount": 150_000}]})
    check("Nina records 150.000 cash -> 200", st == 200, (st, r))
    st, r = nina.verb("pos_sale", sale["name"], "submit")
    # the deposit is taken off as the receipt discount, so above 20% of the bill it waits for head office
    # (pos_sale approval big_discount; a deposit tender in module pos is the follow-up that removes this step)
    check("deposit above 20% of the bill: the payment waits for head office -> 202 big_discount",
          st == 202 and (r or {}).get("approval", {}).get("policy") == "big_discount", (st, r))
    if st == 202:
        st, r = decide(maya, r["approval"]["id"])
        check("Maya approves; the receipt is paid -> 200", st == 200, (st, r))
    s = slot_of(api, a["name"])
    eq("the slot is paid", s.get("state"), "paid")
    paid = nina.find("pos_sale", f"buyer == '{sale['buyer']}' && docstatus == 1")
    paid = paid[0] if paid else {}
    inv_now = api.get("sales_invoice", inv)
    eq("deposit invoice + receipt == the services at catalogue price, once", inv_now.get("grand_total") + paid.get("grand_total"), 195_000)
    st, r = rudi.verb("sales_invoice", inv, "cancel")
    check("cancelling the deposit invoice after the bill took it off -> 422 deposit_used", st == 422 and "deposit_used" in rules(r), (st, r))
    CTX["ayu_slot"] = s

    # audit 2026-11-20 F1/F2: no hand-made bill, no amended copy of a voided bill, no edited deposit discount
    st, r = nina.create("pos_sale", {"shift": shift, "buyer": f"Ayu Contoh ({a['name']})", "items": [{"item": "BRG-0001", "qty": 1}]})
    check("a hand-made sale with an appointment tag -> 422 sale_tag", st == 422 and "sale_tag" in rules(r), (st, r))
    receipt = s.get("sale")
    st, r = nina.verb("pos_sale", receipt, "cancel")
    if st == 202:
        st, r = decide(maya, r["approval"]["id"])
    check("Nina voids Ayu's bill (head office approves) -> 200", st == 200, (st, r))
    s = slot_of(api, a["name"])
    eq("the voided bill puts the slot back to done, nothing deducted", (s.get("state"), s.get("sale"), s.get("deposit_applied")), ("done", "", 0))
    st, am = nina.verb("pos_sale", receipt, "amend")
    am = rec(am) if st in (200, 201) else {}
    if check("Nina amends the voided bill -> a draft copy with the deposit discount", am.get("docstatus") == 0 and am.get("discount_amount") == 50_000, (st, am)):
        st, r = nina.verb("pos_sale", am["name"], "submit")
        if st == 202:   # its discount waits for head office first (big_discount); the approval runs the submit
            st, r = decide(maya, r["approval"]["id"])
        check("paying the amended copy is refused -> 422 sale_stale (the slot is not billed by it)", st == 422 and "sale_stale" in rules(r), (st, r))
        check("the amended copy stays a draft", nina.get("pos_sale", am["name"]).get("docstatus") == 0, "")
        nina.call("DELETE", f"/api/v1/r/pos_sale/{am['name']}")
    st, r = move(nina, s, "billed")
    check("Nina bills it again from Pesanan terbuka -> 200", st == 200, (st, r))
    s = slot_of(api, a["name"])
    bill = s.get("sale")
    st, r = nina.patch("pos_sale", bill, {"discount_amount": 0, "payments": [{"method": "cash", "amount": 195_000}]})
    check("Nina removes the deposit discount on the draft -> 200 (a draft)", st == 200, (st, r))
    st, r = nina.verb("pos_sale", bill, "submit")
    check("paying it without the deposit the bill took off -> 422 sale_stale", st == 422 and "sale_stale" in rules(r), (st, r))
    nina.patch("pos_sale", bill, {"discount_amount": 50_000, "payments": [{"method": "cash", "amount": 150_000}]})
    st, r = nina.verb("pos_sale", bill, "submit")
    if st == 202:
        st, r = decide(maya, r["approval"]["id"])
    check("with the deposit discount back it is paid -> 200", st == 200, (st, r))
    s = slot_of(api, a["name"])
    live = nina.find("pos_sale", f"buyer == 'Ayu Contoh ({a['name']})' && docstatus == 1")
    check("one live receipt of 145.000 and the slot paid with 50.000 deducted",
          len(live) == 1 and live[0].get("grand_total") == 145_000 and s.get("state") == "paid" and s.get("deposit_applied") == 50_000, (live, s))


def scenario_appt_bill_without_deposit(api):
    """Bima's 11:45 cut is billed while its deposit invoice is still a draft: full price, and that invoice can no
    longer be submitted. A deleted draft bill puts the slot back to done."""
    rudi, nina, oki = (CTX.get(k) for k in ("rudi", "nina", "oki"))
    if not (rudi and nina and oki):
        return
    st, a = book(rudi, BIMA, "12:30", [POTONG], staff=OKI, resource=KURSI2, deposit=25_000)
    if not check("Rudi books Bima 12:30 with a 25.000 deposit -> 200", st == 200, (st, a)):
        return
    s = slot_of(api, a["name"])
    move(oki, s, "arrived")
    move(oki, s, "done")
    st, r = move(nina, s, "billed")
    check("Nina bills it -> 200", st == 200, (st, r))
    s = slot_of(api, a["name"])
    sale = nina.get("pos_sale", s.get("sale")) if s.get("sale") else {}
    eq("deposit invoice still a draft: full catalogue price, nothing taken off", (sale.get("grand_total"), s.get("deposit_applied")), (75_000, 0))
    inv = s.get("deposit_invoice")
    st, r = rudi.verb("sales_invoice", inv, "submit")
    check("that deposit invoice can no longer be submitted (the bill charged in full) -> 422 invoice_stale", st == 422 and "invoice_stale" in rules(r), (st, r))
    st, r = nina.call("DELETE", f"/api/v1/r/pos_sale/{sale['name']}")
    check("Nina deletes the draft bill -> 2xx", st in (200, 204), (st, r))
    s = slot_of(api, a["name"])
    check("the slot is back to done with no sale", s.get("state") == "done" and not s.get("sale"), s)


def scenario_appt_cancel_no_show(api):
    """Cancel only while booked; a cancelled time is free again. No-show: the deposit is kept by default, or flagged
    for refund (with a notice) when the owner's setting says so."""
    rudi, maya, putri = (CTX.get(k) for k in ("rudi", "maya", "putri"))
    if not (rudi and maya and putri):
        return
    st, a = book(rudi, CITRA, "18:00", [POTONG], staff=PUTRI, resource=KURSI2)
    if not check("Citra 18:00 with Putri -> 200", st == 200, (st, a)):
        return
    st, r = rudi.verb("appointment", a["name"], "cancel")
    check("cancel a booked appointment -> 200", st == 200, (st, r))
    eq("its slot is cancelled", slot_of(api, a["name"]).get("state"), "cancelled")
    st, r = book(rudi, BIMA, "18:00", [POTONG], staff=PUTRI, resource=KURSI2)
    check("the same time is free again -> 200", st == 200, (st, r))
    s = slot_of(api, r.get("name", ""))
    move(putri, s, "arrived")
    st, r2 = rudi.verb("appointment", r["name"], "cancel")
    check("cancel after the customer arrived -> 422 not_booked", st == 422 and "not_booked" in rules(r2), (st, r2))

    def no_show(start, label):
        st, a = book(rudi, CITRA, start, [CREAM], staff=OKI, resource=KURSI1, deposit=40_000)
        if not check(f"{label}: Citra {start} with a 40.000 deposit -> 200", st == 200, (st, a)):
            return {}
        s = slot_of(api, a["name"])
        rudi.verb("sales_invoice", s["deposit_invoice"], "submit")
        st, r = move(putri, slot_of(api, a["name"]), "no_show")
        check(f"{label}: Putri marks the no-show -> 200", st == 200, (st, r))
        return slot_of(api, a["name"])
    s = no_show("19:00", "default")
    eq("without a setting the deposit is forfeited", s.get("deposit_action"), "forfeited")
    st, cfg = maya.create("appointment_setting", {"no_show_deposit": "refund"})
    check("Maya sets no-show deposits to be refunded -> 201", st == 201, (st, cfg))
    s = no_show("16:00", "refund setting")
    eq("with the setting the deposit is flagged for refund", s.get("deposit_action"), "refund_due")
    st, r = rudi.verb("sales_invoice", s["deposit_invoice"], "cancel")
    check("Rudi cancels the deposit invoice (the refund) -> 200, the slot says refunded",
          st == 200 and slot_of(api, s["appointment"]).get("deposit_action") == "refunded", (st, r, slot_of(api, s["appointment"])))
    st, a2 = book(rudi, CITRA, "19:00", [POTONG], staff=PUTRI, resource=KURSI2, deposit=30_000)
    if check("late deposit: Citra 19:00 with Putri, 30.000 -> 200", st == 200, (st, a2)):
        s2 = slot_of(api, a2["name"])
        move(putri, s2, "no_show")
        st, r = rudi.verb("sales_invoice", s2["deposit_invoice"], "submit")
        check("the deposit invoiced after the no-show -> 200, flagged for refund (owner's setting)",
              st == 200 and slot_of(api, a2["name"]).get("deposit_action") == "refund_due", (st, r, slot_of(api, a2["name"])))
    s = slot_of(api, s["appointment"])
    st, r = move(putri, s, "booked")
    check("Urungkan the no-show -> 200; a refund already made stays recorded", st == 200 and slot_of(api, s["appointment"]).get("deposit_action") == "refunded", (st, r))


def scenario_appt_table(api):
    """The cafe corner: a table for a party, seats checked, clash per table, never billed from the screen."""
    rudi, nina, maya = CTX.get("rudi"), CTX.get("nina"), CTX.get("maya")
    if not (rudi and nina and maya):
        return
    base = {"kind": "table", "outlet": KAFE}
    st, r = book(rudi, DARMA, "19:00", resource=MEJA1, party_size=6, **base)
    check("6 guests at a 4-seat table -> 422 party_fits", st == 422 and "party_fits" in rules(r), (st, r))
    st, r = book(rudi, DARMA, "19:00", [POTONG], resource=MEJA1, party_size=4, **base)
    check("a table reservation with services -> 422 table_without_lines", st == 422 and "table_without_lines" in rules(r), (st, r))
    st, r = book(rudi, DARMA, "19:00", resource=KURSI1, party_size=2, **base)
    check("a chair is not a table -> 422 resource_kind_matches", st == 422 and "resource_kind_matches" in rules(r), (st, r))
    st, a = book(rudi, DARMA, "19:00", resource=MEJA1, party_size=4, **base)
    if not check("the Darma family, 4 guests, table 1 at 19:00 -> 200", st == 200, (st, a)):
        return
    eq("held for 90 minutes", a.get("end_time"), "20:30")
    eq("the table card says how many guests", slot_of(api, a["name"]).get("customer_line"), "Keluarga Contoh Darma · 4 tamu")
    st, r = book(rudi, AYU, "20:00", resource=MEJA1, party_size=2, **base)
    check("table 1 at 20:00 -> 422 resource_busy", st == 422 and "resource_busy" in rules(r), (st, r))
    st, r = book(rudi, AYU, "20:30", resource=MEJA1, party_size=2, **base)
    check("table 1 at 20:30 -> 200", st == 200, (st, r))
    s = slot_of(api, a["name"])
    st, r = move(nina, s, "arrived")
    check("Nina (cashier of Salon Dago) cannot move a Kafe Dago slot (outlet scope) -> 403", denied(st, r), (st, r))
    move(maya, s, "arrived")
    move(maya, slot_of(api, a["name"]), "done")
    st, r = move(maya, slot_of(api, a["name"]), "billed")
    check("a table reservation is not billed from the screen -> 422 bad_step", st == 422 and "bad_step" in rules(r), (st, r))


def scenario_appt_reminder(api):
    """With whatsapp_booking switched on, tomorrow's appointments are reminded once with pengingat_janji; a customer
    without a number is skipped with the reason. The host connector is the mock: nothing reaches Meta."""
    rudi, owner = CTX.get("rudi"), CTX.get("owner")
    if not (rudi and owner):
        return
    st, tok = owner.call("POST", "/api/v1/tokens", {"name": "rangka-cloud", "scopes": ["secrets:host"]})
    if not check("the owner mints a secrets:host token -> 201", st == 201 and (tok or {}).get("token"), (st, tok)):
        return
    host = Api(api.base)
    host.headers = lambda extra=None, t=tok["token"]: dict(extra or {}, Authorization="Bearer " + t)
    for field, value in (("api_host", "cloud.rangka.example"), ("service_token", "dummy-" + secrets.token_hex(16)), ("webhook_key", "test-" + secrets.token_hex(24))):
        host.call("PUT", f"/api/v1/connectors/rangka_whatsapp/auth/{field}", {"value": value})
    st, a = book(rudi, AYU, "10:00", [POTONG], staff=OKI, resource=KURSI1, day=tomorrow())
    check("Ayu books tomorrow 10:00 -> 200", st == 200, (st, a))
    st, c = book(rudi, CITRA, "11:00", [POTONG], staff=OKI, resource=KURSI1, day=tomorrow())
    if not check("Citra (no phone) books tomorrow 11:00 -> 200", st == 200, (st, c)) or not a.get("name"):
        return
    for _ in range(2):
        st, out = api.call("POST", "/api/v1/flows/appointment_reminder_tomorrow/run", {"event": {}})
    check("the afternoon run (twice) -> 200", st == 200, (st, out))

    def calls_for(key):
        st, resp = api.call("GET", "/api/v1/system/jobs?kind=call&limit=500")
        rows = (resp or {}).get("data", []) if st == 200 else []
        res = []
        for j in rows:
            args = j.get("args") or j.get("payload") or {}
            if isinstance(args, str):
                args = json.loads(args)
            inp = args.get("input") or {}
            if args.get("connector") == "rangka_whatsapp" and args.get("action") == "send_template" and inp.get("idempotency_key") == key:
                res.append(inp)
        return res
    cs = calls_for(a["name"] + ":appointment:1")
    comps = (cs[0].get("components") if cs else None) or []
    if isinstance(comps, str):
        comps = json.loads(comps)
    texts = [p.get("text") for p in (comps[0].get("parameters") if comps else [])]
    t = kernel_today() + datetime.timedelta(days=1)
    when = f"{t.day} {['Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni', 'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember'][t.month - 1]} {t.year} jam 10:00"
    check("one pengingat_janji to Ayu: her name, the service, the chair, tomorrow and the time",
          len(cs) == 1 and cs[0].get("template") == "pengingat_janji" and texts == ["Ayu Contoh", "Potong rambut", "Kursi 1", when], (cs, texts))
    m = api.find("whatsapp_message", f"key == '{c['name']}:appointment:1'")
    check("Citra has no number: one skipped row (no_number), no call", len(m) == 1 and m[0].get("status") == "skipped" and m[0].get("error") == "no_number"
          and not calls_for(c["name"] + ":appointment:1"), m)


def scenario_appt_screens(api):
    """Staff land on Jadwal hari ini; the cashier also gets Pesanan terbuka; both are queues over appointment_slot."""
    oki, nina = CTX.get("oki"), CTX.get("nina")
    if not (oki and nina):
        return
    st, m = oki.call("GET", "/api/v1/meta")
    work = (m or {}).get("work") or []
    check("Oki's home is Jadwal hari ini", work[:1] == [{"screen": "jadwal_hari_ini", "home": True}], work)
    check("Oki is not offered Pesanan terbuka", "pesanan_terbuka" not in ((m or {}).get("screens") or {}), work)
    st, m = nina.call("GET", "/api/v1/meta")
    sc = ((m or {}).get("screens") or {}).get("pesanan_terbuka") or {}
    check("Nina gets Pesanan terbuka, a queue over appointment_slot", sc.get("type") == "queue" and sc.get("queue", {}).get("source", {}).get("model") == "appointment_slot", sc)
    st, r = nina.call("GET", "/api/v1/screens/pesanan_terbuka")
    check("Nina opens Pesanan terbuka -> 200", st == 200, (st, r))


SCENARIOS = [scenario_appt_masters, scenario_appt_salon_day, scenario_appt_bill_without_deposit, scenario_appt_cancel_no_show,
             scenario_appt_table, scenario_appt_reminder, scenario_appt_screens]
