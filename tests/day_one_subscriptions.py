"""Day-one scenarios of demo-langganan (piece langganan_jasa + module subscriptions): a small RT/RW net's month.

Loaded by tests/day_one.py (bind() hands over its helpers and registers the site): `python3 tests/day_one.py --site
demo-langganan`. Fixtures are synthetic (examples/fixtures/demo-langganan). Dates are real: the expected bills come
from an oracle written here (calendar months from the anchor, the end-of-month clamp of contract 0031, the pro-rated
first month), so the test holds on any day it runs. Covered: periods and amounts of every cycle (monthly on the 1st
with a pro-rated start, 6 months prepaid, quarterly, yearly from the 31st, stopped), extras (once, every period, locked
once billed), one bill per period whatever runs the flow, the invoice guard (one invoice per bill, its customer and
amount, tags locked), the dunning ladder and its log, a payment lifting a suspension, the month recap and collection
rate, collectors (own areas only, no invoices, a cap on what they collect), deposits (exact cash, oldest invoices
first after the drafts already waiting, drafts kept), the dashboards and the permission matrix. The books are checked
by scenario_accounting after.
"""
import datetime

H = {}
SITE = "demo-langganan"
SUBS = ["LGN-00001", "LGN-00002", "LGN-00003", "LGN-00004", "LGN-00005", "LGN-00006", "LGN-00007"]

_R = {"read"}
_RCWEP = {"read", "create", "write", "export", "print"}
MATRIX = {
    "subscription_plan": {"finance_manager": _RCWEP, "collector": _R},
    "service_area": {"finance_manager": _RCWEP, "collector": _R},
    "subscription": {"finance_manager": _RCWEP, "collector": _R, "sales_manager": {"read", "write"}},
    "subscription_extra": {"finance_manager": _RCWEP},
    "subscription_bill": {"finance_manager": {"read", "export", "print"}, "sales_manager": _R},
    "subscription_status_log": {"finance_manager": {"read", "create", "export", "print"}, "sales_manager": {"read", "create"}, "collector": _R},
    "subscription_month": {"finance_manager": {"read", "export", "print"}},
    "field_collection": {"collector": {"read", "create", "write", "delete"}, "finance_manager": _RCWEP | {"delete"}, "sales_manager": _R},
    "collector_deposit": {"finance_manager": {"read", "create", "write", "submit", "export", "print"}, "collector": _R},
    "subscription_guard": {"finance_manager": {"create"}, "sales_manager": {"create"}, "collector": {"create"}},   # never stored
}


def bind(m):
    for k in ("ok", "fail", "check", "eq", "CTX", "Api", "messages", "rows_all", "dashboard", "kernel_today", "denied", "secrets"):
        H[k] = getattr(m, k)
    globals().update(H)
    m.SITE_ORGS[SITE] = [("contoh_net", SITE)]
    m.SITE_MEMBERS[SITE] = [("fm", "finance_manager"), ("col1", "collector")]
    m.MATRIX.update(MATRIX)


def refused(st, resp, text):
    return st in (409, 422) and any(text in x for x in messages(resp))


def rec(x):
    return (x or {}).get("record", x) or {}


# ---------- the oracle: calendar months from the anchor (contract 0031)

def d(s):
    return datetime.date.fromisoformat(s)


def add_months(day, n):
    """add_months of the kernel: n calendar months later, the day clamped to the end of the target month."""
    y, m = divmod(day.month - 1 + n, 12)
    y, m = day.year + y, m + 1
    nxt = datetime.date(y + (m == 12), m % 12 + 1, 1)
    return datetime.date(y, m, min(day.day, (nxt - datetime.timedelta(days=1)).day))


def js_round(x):
    return int(x + 0.5) if x >= 0 else -int(-x + 0.5)


def expected_bills(s, today, billed_before=0):
    """Every period of subscription s whose first day is on or before today: [(kind, start, end, base amount)]."""
    start, months = d(s["start_date"]), s["cycle_months"]
    net = js_round(s["monthly_price"] * (100 - s["discount_percent"]) / 100)
    first = start.replace(day=1)
    anchor = start if s["anchor"] == "start" or start.day == 1 else add_months(first, 1)
    end = d(s["end_date"]) if s.get("end_date") else None
    out = []

    def cut(kind, ps, nxt, amount):
        """A period [ps, nxt) that the end date cuts is billed up to the day before it, pro-rated by days."""
        if end is not None and end < nxt:
            amount = js_round(amount * (end - ps).days / (nxt - ps).days)
            nxt = end
        out.append((kind, ps, nxt - datetime.timedelta(days=1), amount))
    if anchor != start and start <= today and (end is None or start < end):
        cut("prorata", start, anchor, js_round(net * (anchor - start).days / (anchor - add_months(anchor, -1)).days))
    k = billed_before
    while True:
        ps = add_months(anchor, k)
        if ps > today or (end is not None and ps >= end):
            return out
        cut("regular", ps, add_months(anchor, k + months), net * months)
        k += months


# ---------- setup

def setup_demo_langganan(api):
    """Finance and two collectors, signed in (user records USR-001..003 of the fixtures)."""
    k = CTX["kernel"]
    st, me = api.call("GET", "/auth/me")
    CTX.update(org=((me or {}).get("org") or {}).get("id") or "contoh_net")
    pw = secrets.token_urlsafe(18)   # throwaway password of a throwaway database
    for key, email, role, recname, name in [("fm", "rani@contohnet.test", "finance_manager", "USR-001", "Rani Contoh"),
                                            ("col1", "dodi@contohnet.test", "collector", "USR-002", "Dodi Contoh"),
                                            ("col2", "eka@contohnet.test", "collector", "USR-003", "Eka Contoh")]:
        code, out = k.user_add(email, role, CTX["org"], name, record=recname, password=pw)
        cl = Api.login(k.base, email, pw) if code == 0 else None
        if cl is None:
            CTX["login_error"] = f"{email}: " + (out if code else "login refused")
            fail("setup: add and sign in the members", CTX["login_error"])
            return
        CTX[key] = cl


def run_flow(api, name, label):
    st, out = api.call("POST", f"/api/v1/flows/{name}/run", {})
    check(f"{label}: flow {name} runs (200)", st == 200, (st, out))
    return out or {}


def bill_all(api, label="billing"):
    """Run subscription_billing until it bills nothing (one period per subscription per run)."""
    made = []
    for _ in range(60):
        out = run_flow(api, "subscription_billing", label) if not made else api.call("POST", "/api/v1/flows/subscription_billing/run", {})[1] or {}
        new = [c for c in out.get("created", []) if c.startswith("subscription_bill/")]
        if not new:
            return made
        made += new
    fail(f"{label}: billing caught up within 60 runs", len(made))
    return made


def bill_invoice(api, bill):
    """The live invoice of a bill (found by its tag: a draft is renamed when submitted)."""
    x = rows_all(api, "sales_invoice", f"docstatus != 2 && notes.contains('[{bill}]')")
    return x[0]["name"] if x else "-"


def collection_payment(api, col):
    """The payment a deposited collection made (its number in [] in the notes)."""
    x = rows_all(api, "payment", f"notes.contains('[{col}]')")
    return api.get("payment", x[0]["name"]) if x else {}


def sub_invoices(api, sub, where=""):
    return rows_all(api, "sales_invoice", f"notes.contains('{{langganan:{sub}}}')" + (f" && {where}" if where else ""))


# ---------- scenarios

def scenario_subs_fixtures(api):
    eq("fixtures: 3 plans, 3 areas, 7 subscriptions, 2 extras, no bill",
       [len(rows_all(api, m)) for m in ("subscription_plan", "service_area", "subscription", "subscription_extra", "subscription_bill")], [3, 3, 7, 2, 0])
    s1 = api.get("subscription", "LGN-00001")
    eq("LGN-00001 (start 17 Aug, billed on the 1st): anchor 1 Sep, first bill pro-rated 17-31 Aug = 15/31 x 150.000",
       (s1["anchor_date"], s1["next_is_prorata"], s1["next_bill_date"], s1["next_period_end"], s1["next_amount"], s1["collector"]),
       ("2026-09-01", True, "2026-08-17", "2026-08-31", 72581, "USR-002"))
    s4 = api.get("subscription", "LGN-00004")
    eq("LGN-00004 prepaid 6 months, 5% off: 237.500 a month, first bill 1 Sep - 28 Feb = 1.425.000",
       (s4["cycle_months"], s4["monthly_net"], s4["next_period_end"], s4["next_amount"]), (6, 237500, "2027-02-28", 1425000))
    s6 = api.get("subscription", "LGN-00006")
    eq("LGN-00006 yearly from 31 Oct, 10% off: period ends 30 Oct 2027, 16.200.000",
       (s6["cycle_months"], s6["next_period_end"], s6["next_amount"]), (12, "2027-10-30", 16200000))
    s7 = api.get("subscription", "LGN-00007")
    eq("LGN-00007 left before Rangka: next bill 1 Oct, on its end date", (s7["next_bill_date"], s7["end_date"]), ("2026-10-01", "2026-10-01"))


def scenario_subs_schedules(api):
    """The kernel scheduler holds the two schedules, in the site's time zone (system.rk scheduler.timezone)."""
    st, sysinfo = api.call("GET", "/api/v1/system/status")
    sc = (((sysinfo or {}).get("capabilities") or {}).get("scheduler")) or {}
    got = {x.get("name"): x.get("cron") for x in sc.get("schedules") or []}
    eq("scheduler: billing at 00:05 and dunning at 02:00, Asia/Jakarta",
       (st, sc.get("timezone"), got.get("subscription_billing"), got.get("subscription_dunning")), (200, "Asia/Jakarta", "5 0 * * *", "0 2 * * *"))


def scenario_subs_billing(api):
    fm, col1 = CTX.get("fm"), CTX.get("col1")
    if fm is None:
        return
    today = kernel_today()
    subs = {s["name"]: s for s in rows_all(api, "subscription")}
    want = {n: expected_bills(s, today, 0 if n != "LGN-00007" else 4) for n, s in subs.items()}
    # only the owner (and the scheduler) bills: finance has no create on bills, a collector runs no flow (row scope)
    st, resp = fm.call("POST", "/api/v1/flows/subscription_billing/run", {})
    check("finance cannot run the billing flow (403: bills are the scheduler's)", denied(st, resp), (st, resp))
    st, resp = col1.call("POST", "/api/v1/flows/subscription_billing/run", {})
    check("a collector cannot run a flow (403, row scope)", denied(st, resp), (st, resp))
    eq("and nothing was billed", len(rows_all(api, "subscription_bill")), 0)

    made = bill_all(api)
    eq("billing catches up: one bill per period whose first day has come", len(made), sum(len(v) for v in want.values()))
    eq("a second run bills nothing", [c for c in (api.call("POST", "/api/v1/flows/subscription_billing/run", {})[1] or {}).get("created", [])], [])
    bills = rows_all(api, "subscription_bill")
    got = {n: sorted((b["kind"], b["period_start"], b["period_end"], b["base_amount"]) for b in bills if b["subscription"] == n) for n in subs}
    for n in SUBS:
        w = sorted((k, a.isoformat(), b.isoformat(), amt) for k, a, b, amt in want[n])
        eq(f"{n} ({subs[n]['cycle']}, anchor {subs[n]['anchor']}): bills = the oracle's periods and amounts", got[n], w)
    eq("LGN-00007 (stopped on 1 Oct, billed through Sep before Rangka) gets no bill", got["LGN-00007"], [])

    # every bill has exactly one draft invoice, tagged, dated the period's start, due due_days after, lines = total
    bad = []
    for b in bills:
        invs = rows_all(api, "sales_invoice", f"notes.contains('[{b['name']}]')")
        s = subs[b["subscription"]]
        inv = api.get("sales_invoice", invs[0]["name"]) if len(invs) == 1 else {}
        due = (d(b["period_start"]) + datetime.timedelta(days=s["due_days"])).isoformat()
        if not (len(invs) == 1 and inv["docstatus"] == 0 and inv["customer"] == s["customer"] and inv["posting_date"] == b["period_start"]
                and inv["due_date"] == due == b["due_date"] and "{langganan:%s}" % s["name"] in inv["notes"] and inv["subtotal"] == b["total"]):
            bad.append((b["name"], len(invs), {k: inv.get(k) for k in ("customer", "posting_date", "due_date", "subtotal", "notes")}, b["total"], due))
    check(f"each of the {len(bills)} bills has one draft invoice: its customer, dated the period's start, due due_days after, subtotal = bill total", not bad, bad[:3])

    # extras: once on the first bill that covers it, every period on each
    b3 = sorted((b for b in bills if b["subscription"] == "LGN-00003"), key=lambda b: b["period_start"])
    eq("LGN-00003: the installation fee (once) is on its pro-rated first bill only",
       [(b["kind"], b["extras_total"]) for b in b3], [("prorata", 200000)] + [("regular", 0)] * (len(b3) - 1))
    eq("the one-off extra is marked with that bill", api.get("subscription_extra", "TMB-00002")["billed_bill"], b3[0]["name"])
    b5 = [b for b in bills if b["subscription"] == "LGN-00005"]
    check("LGN-00005: the static IP (every period) is on each quarterly bill", b5 and all(b["extras_total"] == 100000 for b in b5), [(b["period_start"], b["extras_total"]) for b in b5])
    inv5 = api.get("sales_invoice", bill_invoice(api, b5[0]["name"])) if b5 else {}
    eq("its invoice: a plan line and an extra line", sorted((i["item"], i["rate"]) for i in inv5.get("items", [])), [("BRG-0003", 4500000), ("BRG-0004", 100000)])

    # the period ledger and the locked terms
    s1 = api.get("subscription", "LGN-00001")
    n_reg = len([x for x in want["LGN-00001"] if x[0] == "regular"])
    eq("LGN-00001 moved on: pro-rata billed, months billed, start and anchor locked", (s1["prorata_billed"], s1["billed_months"], s1["locked_start"], s1["locked_anchor"]),
       (True, n_reg, "2026-08-17", "month_start"))
    st, resp = fm.patch("subscription", "LGN-00001", {"start_date": "2026-08-20"})
    check("a billed subscription's start date cannot change (422)", refused(st, resp, "tidak bisa diubah lagi"), (st, resp))
    st, resp = fm.patch("subscription", "LGN-00001", {"customer": "CUST-0003"})
    check("nor its customer (422; audit #6)", refused(st, resp, "tidak bisa diubah lagi"), (st, resp))
    one = [b for b in bills if b["subscription"] == "LGN-00006"][0]
    st, resp = fm.call("DELETE", f"/api/v1/r/sales_invoice/{Api.q(bill_invoice(api, one['name']))}")
    check("the only draft invoice of a bill cannot be deleted (409/422; audit #7)", refused(st, resp, "satu-satunya faktur"), (st, resp))
    st, resp = fm.patch("subscription", "LGN-00001", {"end_date": "2026-09-15"})
    check("an end date inside a billed period is refused (422)", refused(st, resp, "sudah ditagih"), (st, resp))
    st, resp = fm.patch("subscription", "LGN-00001", {"billed_months": 0, "prorata_billed": False})
    check("billed months are not set by hand (read-only, ignored)", api.get("subscription", "LGN-00001")["billed_months"] == n_reg, (st, resp))
    # a bill is never made by hand: finance has no create, and the owner cannot fill its read-only fields
    st, resp = fm.create("subscription_bill", {"subscription": "LGN-00002", "period_start": "2030-01-01"})
    check("finance cannot create a bill (403)", denied(st, resp), (st, resp))
    st, resp = api.create("subscription_bill", {"subscription": "LGN-00002", "customer": "CUST-0002", "period_start": "2030-01-01", "period_end": "2030-01-31", "base_amount": 1})
    check("the owner cannot make a bill by hand either (its fields are the flow's: 422)", st == 422, (st, resp))
    # an extra a bill took is locked; its until date may still change
    st, resp = fm.patch("subscription_extra", "TMB-00001", {"amount": 150000})
    check("a billed extra's amount cannot change (422)", refused(st, resp, "sudah ditagih"), (st, resp))
    st, resp = fm.patch("subscription_extra", "TMB-00001", {"until_date": "2027-12-31"})
    check("its until date can (200)", st == 200, (st, resp))
    CTX["bills"] = {b["name"]: b for b in bills}


def scenario_subs_new_and_change(api):
    """A subscription made today bills today; a price change applies from the next bill; a cycle change keeps the anchor."""
    fm = CTX.get("fm")
    if fm is None:
        return
    today = kernel_today()
    st, s = fm.create("subscription", {"customer": "CUST-0007", "plan": "PKT-002", "area": "ARE-002", "start_date": today.isoformat(), "service_id": "gani.baru"})
    if not check("finance adds a subscription starting today (201), cycle and price from the plan", st == 201 and s.get("cycle") == "monthly" and s.get("monthly_price") == 250000, (st, s)):
        return
    CTX["new_sub"] = s["name"]
    st, x = fm.create("subscription_extra", {"subscription": s["name"], "description": "Router tambahan", "amount": 50000, "repeat": "every_period", "from_date": today.isoformat()})
    check("an extra every period from today (201), item from the plan", st == 201 and x.get("item") == "BRG-0002", (st, x))
    made = bill_all(api, "new subscription")
    b = api.get("subscription_bill", made[0].split("/", 1)[1]) if len(made) == 1 else {}
    eq("one bill for it today: 250.000 + 50.000", (len(made), b.get("subscription"), b.get("period_start"), b.get("base_amount"), b.get("extras_total")),
       (1, s["name"], today.isoformat(), 250000, 50000))
    CTX["new_bill"] = b.get("name")
    st, r = fm.patch("subscription", s["name"], {"monthly_price": 275000, "cycle": "quarterly"})
    r = api.get("subscription", s["name"])
    eq("price and cycle change apply from the next period, counted from the same anchor",
       (st, r["next_bill_date"], r["next_amount"], r["next_period_end"]),
       (200, add_months(today, 1).isoformat(), 825000, (add_months(today, 4) - datetime.timedelta(days=1)).isoformat()))
    fm.patch("subscription", s["name"], {"monthly_price": 250000, "cycle": "monthly"})


def scenario_subs_end_dates(api):
    """A client who stops mid-period (audit #1): the period is billed up to the day before the end date, pro-rated,
    and the run goes on for everyone (one transaction for all subscriptions)."""
    fm = CTX.get("fm")
    if fm is None:
        return
    today = kernel_today()
    s_start = today - datetime.timedelta(days=40)
    cases = [   # started 40 days ago on its own date, stops 5 days into its second month; a pro-rated start cut short
        {"customer": "CUST-0005", "plan": "PKT-001", "area": "ARE-003", "start_date": s_start.isoformat(), "anchor": "start",
         "end_date": (add_months(s_start, 1) + datetime.timedelta(days=5)).isoformat()},
        {"customer": "CUST-0006", "plan": "PKT-001", "area": "ARE-003", "start_date": (today - datetime.timedelta(days=3)).isoformat(), "anchor": "month_start",
         "end_date": (today - datetime.timedelta(days=1)).isoformat()},
    ]
    made = []
    for body in cases:
        st, x = fm.create("subscription", body)
        if not check(f"finance adds a subscription {body['start_date']} to {body['end_date']} (201)", st == 201, (st, x)):
            return
        made.append(x["name"])
    st, x = fm.create("subscription_extra", {"subscription": made[0], "description": "IP statis", "item": "BRG-0004", "amount": 100000,
                                             "repeat": "every_period", "from_date": s_start.isoformat()})
    check("an extra of 100.000 every period on the first (201)", st == 201, (st, x))
    bill_all(api, "end dates")
    for n in made:
        s = api.get("subscription", n)
        want = sorted((k, a.isoformat(), b.isoformat(), amt) for k, a, b, amt in expected_bills(s, today))
        got = sorted((b["kind"], b["period_start"], b["period_end"], b["base_amount"]) for b in rows_all(api, "subscription_bill", f"subscription == '{n}'"))
        eq(f"{n}: the period the end date cuts is billed to the day before it, pro-rated", got, want)
        eq(f"{n}: billed through its end date, nothing after", (s["billed_through"], s["next_bill_date"] >= s["end_date"]), (s["end_date"], True))
    st, resp = fm.patch("subscription", made[0], {"end_date": (d(cases[0]["end_date"]) - datetime.timedelta(days=1)).isoformat()})
    check("an end date before what is already billed is refused (422)", refused(st, resp, "sudah ditagih"), (st, resp))
    # the client stays after all (audit #12): the rest of the cut period is billed, pro-rated, then the cycle goes on
    bt = d(cases[0]["end_date"])
    boundary = add_months(s_start, 2)
    fm.patch("subscription", made[0], {"monthly_price": 10000})   # the price falls before the rest is billed (audit #15)
    st, resp = fm.patch("subscription", made[0], {"end_date": None})
    check("finance takes the end date away (200)", st == 200 and not api.get("subscription", made[0]).get("end_date"), (st, resp))
    new = bill_all(api, "remainder")
    rb = [api.get("subscription_bill", x.split("/", 1)[1]) for x in new]
    rb = [b for b in rb if b["subscription"] == made[0]]
    want = [("remainder", bt.isoformat(), (boundary - datetime.timedelta(days=1)).isoformat(), js_round(150000 * (boundary - bt).days / (boundary - add_months(s_start, 1)).days))]
    if boundary <= today:
        want.append(("regular", boundary.isoformat(), (add_months(s_start, 3) - datetime.timedelta(days=1)).isoformat(), 10000))
    eq("the rest of that period is billed next, pro-rated against the full period", sorted((b["kind"], b["period_start"], b["period_end"], b["base_amount"]) for b in rb), sorted(want))
    allb = rows_all(api, "subscription_bill", f"subscription == '{made[0]}'")
    month2 = [b for b in allb if add_months(s_start, 1).isoformat() <= b["period_start"] < boundary.isoformat()]   # the price fell to 10.000 between its pieces
    eq("the extra every period: 100.000 on the full first month", [b["extras_total"] for b in allb if b["period_start"] == s_start.isoformat()], [100000])
    check("and the cut second month and its rest add up to one extra (audit #14)", abs(sum(b["extras_total"] for b in month2) - 100000) <= 1,
          [(b["kind"], b["period_start"], b["extras_total"]) for b in month2])
    fm.patch("subscription", made[0], {"monthly_price": 150000})
    fm.patch("subscription", made[0], {"end_date": (boundary if boundary > today else add_months(s_start, 3)).isoformat()})
    CTX["ended"] = made


def scenario_subs_invoice_guard(api):
    """Finance submits the drafts; the invoice of a bill goes to its customer, for its total, once, with its tags."""
    fm = CTX.get("fm")
    if fm is None or not CTX.get("new_bill"):
        return
    nb = api.get("subscription_bill", CTX["new_bill"])
    draft = bill_invoice(api, nb["name"])
    st, resp = fm.patch("sales_invoice", draft, {"notes": "Langganan tanpa penanda"})
    check("the notes of a subscription invoice cannot be edited (tags locked, 409/422)", refused(st, resp, "Catatan faktur langganan"), (st, resp))
    inv = api.get("sales_invoice", draft)
    items = [dict(i) for i in inv["items"]]
    items[0]["rate"] = items[0]["rate"] + 1000
    st, resp = fm.patch("sales_invoice", draft, {"items": [{k: i[k] for k in ("item", "description", "qty", "rate")} for i in items]})
    check("finance may edit the draft's lines (200)", st == 200, (st, resp))
    st, resp = fm.verb("sales_invoice", draft, "submit")
    check("but an invoice whose total differs from its bill is refused on submit (422)", refused(st, resp, "tidak cocok dengan tagihan langganannya"), (st, resp))
    items[0]["rate"] = items[0]["rate"] - 1000
    fm.patch("sales_invoice", draft, {"items": [{k: i[k] for k in ("item", "description", "qty", "rate")} for i in items]})
    st, resp = fm.verb("sales_invoice", draft, "submit", {"force": True})
    check("set back to the bill's total it submits (200)", st == 200, (st, resp))
    # a second invoice of the same bill, or a tag without a bill
    body = {"customer": nb["customer"], "notes": inv["notes"], "items": [{k: i[k] for k in ("item", "description", "qty", "rate")} for i in inv["items"]]}
    st, twin = fm.create("sales_invoice", body)
    st2, resp = fm.verb("sales_invoice", (twin or {}).get("name", "-"), "submit", {"force": True})
    check("a second invoice carrying the same bill is refused on submit (422)", st == 201 and refused(st2, resp, "tidak cocok dengan tagihan langganannya"), (st, st2, resp))
    st, fake = fm.create("sales_invoice", dict(body, notes="Langganan palsu {langganan:LGN-00002}"))
    st2, resp = fm.verb("sales_invoice", (fake or {}).get("name", "-"), "submit", {"force": True})
    check("an invoice with a subscription tag but no bill is refused (422)", st == 201 and refused(st2, resp, "tidak cocok dengan tagihan langganannya"), (st, st2, resp))
    for x in (twin, fake):
        if x and x.get("name"):
            fm.call("DELETE", f"/api/v1/r/sales_invoice/{Api.q(x['name'])}")
    # finance submits every other draft subscription invoice
    drafts = rows_all(api, "sales_invoice", "docstatus == 0 && notes.contains('{langganan:')")
    bad = []
    for x in drafts:
        st, resp = fm.verb("sales_invoice", x["name"], "submit", {"force": True})
        if st != 200:
            bad.append((x["name"], st, messages(resp)))
    check(f"finance submits the other {len(drafts)} draft subscription invoices", not bad, bad[:3])
    open_ = sub_invoices(api, "LGN-00002", "docstatus == 1 && outstanding > 0")
    eq("submitting refreshes what a subscription still owes (LGN-00002)", api.get("subscription", "LGN-00002")["unpaid_amount"], sum(i["outstanding"] for i in open_))


def expected_status(s, invoices, today):
    open_ = sorted((i for i in invoices if i["docstatus"] == 1 and i["outstanding"] > 0), key=lambda i: i["due_date"])
    oldest = d(open_[0]["due_date"]) if open_ else None
    late = (today - oldest).days if oldest and oldest < today else 0
    end = s.get("end_date") or ""
    if not end and s["stop_after_days"] > 0 and late >= s["stop_after_days"]:
        billed_end = add_months(d(s["anchor_date"]), s["billed_months"])
        end = max(today, billed_end).isoformat()
    if end and d(end) <= today:
        return "stopped", end
    return ("suspended" if late >= s["suspend_after_days"] else "late" if late > 0 else "active"), end


def scenario_subs_dunning(api):
    fm = CTX.get("fm")
    if fm is None:
        return
    today = kernel_today()
    before = {s["name"]: s for s in rows_all(api, "subscription")}
    want = {n: expected_status(s, sub_invoices(api, n), today) for n, s in before.items()}
    run_flow(api, "subscription_dunning", "dunning")
    after = {s["name"]: s for s in rows_all(api, "subscription")}
    bad = {n: ((after[n]["status"], after[n].get("end_date") or ""), want[n]) for n in after if (after[n]["status"], after[n].get("end_date") or "") != want[n]}
    check("dunning: every subscription's status (and an automatic end date) follows its oldest unpaid invoice", not bad, bad)
    eq("LGN-00007 (end date passed) is stopped", after["LGN-00007"]["status"], "stopped")
    for n, s in after.items():
        open_ = [i for i in sub_invoices(api, n) if i["docstatus"] == 1 and i["outstanding"] > 0]
        if s["unpaid_amount"] != sum(i["outstanding"] for i in open_) or s["overdue_amount"] != sum(i["outstanding"] for i in open_ if d(i["due_date"]) < today):
            fail(f"{n}: unpaid and overdue amounts = its submitted invoices still open", (s["unpaid_amount"], s["overdue_amount"]))
    logs = rows_all(api, "subscription_status_log")
    changed = sorted(n for n in after if after[n]["status"] != before[n]["status"])
    eq("one status log row per change", sorted(x["subscription"] for x in logs), changed)
    check("each row says from and to", all(x["from_status"] == before[x["subscription"]]["status"] and x["to_status"] == after[x["subscription"]]["status"] for x in logs), logs[:2])
    st, out = api.call("POST", "/api/v1/flows/subscription_dunning/run", {})
    eq("a second run the same day changes no status", len(rows_all(api, "subscription_status_log")), len(logs))
    CTX["suspended_before_pay"] = after["LGN-00001"]["status"]

    # the month recap: invoiced and paid of this month's subscription invoices
    m = today.isoformat()[:7]
    rows = rows_all(api, "subscription_month", f"month == '{m}'")
    invs = [i for i in rows_all(api, "sales_invoice", "docstatus == 1 && notes.contains('{langganan:')") if i["posting_date"][:7] == m]
    billed = sum(i["amount_due"] for i in invs)
    paid = sum(i["amount_due"] - i["outstanding"] for i in invs)
    live = [s for s in after.values() if s["status"] != "stopped"]
    eq(f"month recap {m}: invoiced, paid, count, running subscriptions, MRR",
       [(r["billed"], r["collected"], r["invoices"], r["active_count"], r["mrr"]) for r in rows],
       [(billed, paid, len(invs), len(live), sum(s["monthly_net"] for s in live))])


def scenario_subs_payment_lifts(api):
    """A payment of everything LGN-00001 owes makes it active at once, without waiting for the night run."""
    fm = CTX.get("fm")
    if fm is None:
        return
    open_ = [i for i in sub_invoices(api, "LGN-00001") if i["docstatus"] == 1 and i["outstanding"] > 0]
    total = sum(i["outstanding"] for i in open_)
    st, p = fm.create("payment", {"direction": "receive", "customer": "CUST-0001", "method": "transfer", "amount": total,
                                  "allocations": [{"sales_invoice": i["name"], "amount": i["outstanding"]} for i in open_]})
    st2, resp = fm.verb("payment", (p or {}).get("name", "-"), "submit", {"force": True})
    check("finance records and submits LGN-00001's payment (201, 200)", st == 201 and st2 == 200, (st, p, st2, resp))
    s = api.get("subscription", "LGN-00001")
    eq("LGN-00001 is active again at once, owes nothing", (s["status"], s["unpaid_amount"], s["overdue_since"]), ("active", 0, ""))
    if CTX.get("suspended_before_pay") not in (None, "active"):
        logs = rows_all(api, "subscription_status_log", "subscription == 'LGN-00001'")
        check("and the change is logged", any(x["to_status"] == "active" for x in logs), logs)


def scenario_subs_stopped_arrears(api):
    """A stopped subscription that still owes stays refreshed (audit #3): paying it at the office clears it."""
    fm = CTX.get("fm")
    ended = [n for n in CTX.get("ended", []) if api.get("subscription", n)["status"] == "stopped"]
    if fm is None or not ended:
        return
    n = ended[0]
    s = api.get("subscription", n)
    open_ = [i for i in sub_invoices(api, n) if i["docstatus"] == 1 and i["outstanding"] > 0]
    check(f"{n} is stopped and still owes", s["unpaid_amount"] > 0 and s["unpaid_amount"] == sum(i["outstanding"] for i in open_), (s["status"], s["unpaid_amount"]))
    st, p = fm.create("payment", {"direction": "receive", "customer": s["customer"], "method": "transfer", "amount": s["unpaid_amount"],
                                  "allocations": [{"sales_invoice": i["name"], "amount": i["outstanding"]} for i in open_]})
    st2, resp = fm.verb("payment", (p or {}).get("name", "-"), "submit", {"force": True})
    eq(f"{n}: paid at the office, its arrears go to 0 at once", (st, st2, api.get("subscription", n)["unpaid_amount"]), (201, 200, 0))


def scenario_subs_collectors(api):
    fm, col1, col2 = CTX.get("fm"), CTX.get("col1"), CTX.get("col2")
    if col1 is None or col2 is None:
        return
    eq("collector Dodi sees the subscriptions of his area only", sorted(x["name"] for x in col1.list("subscription", limit=200)), ["LGN-00001", "LGN-00002"])
    eq("collector Eka sees hers", sorted(x["name"] for x in col2.list("subscription", limit=200)), sorted(["LGN-00003", "LGN-00004", "LGN-00007"] + ([CTX["new_sub"]] if CTX.get("new_sub") else [])))
    st, resp = col1.call("GET", "/api/v1/r/sales_invoice")
    check("a collector reads no invoices (403)", denied(st, resp), (st, resp))
    s2 = api.get("subscription", "LGN-00002")
    owed = s2["unpaid_amount"]
    if not check("LGN-00002 owes something to collect", owed >= 300000, owed):
        return
    st, resp = col1.create("field_collection", {"subscription": "LGN-00003", "amount": 1000})
    check("a collection on another area's subscription is refused", st in (403, 422), (st, resp))
    st, resp = col1.create("field_collection", {"subscription": "LGN-00002", "amount": owed + 1})
    check("more than the subscription owes is refused (422)", refused(st, resp, "melebihi yang belum dibayar"), (st, resp))
    st, c1 = col1.create("field_collection", {"subscription": "LGN-00002", "amount": 300000})
    check("Dodi records 300.000 collected from LGN-00002 (201), as himself", st == 201 and rec(c1).get("collector") == "USR-002", (st, c1))
    st, resp = col1.create("field_collection", {"subscription": "LGN-00002", "amount": owed - 300000 + 1})
    check("a second collection beyond what is left is refused (the cap counts collections not yet deposited)", refused(st, resp, "melebihi yang belum dibayar"), (st, resp))
    eq("finance sees Dodi's collection; Eka does not", (len(fm.list("field_collection")), len(col2.list("field_collection"))), (1, 0))

    # the deposit: exact cash, then one draft cash payment per collection, oldest invoices first
    st, dep = fm.create("collector_deposit", {"collector": "USR-002", "cash_counted": 250000})
    st2, resp = fm.verb("collector_deposit", (dep or {}).get("name", "-"), "submit")
    check("a deposit whose cash is not the collections' total is refused (422)", st == 201 and refused(st2, resp, "tidak sama dengan total"), (st, dep, st2, resp))
    fm.patch("collector_deposit", dep["name"], {"cash_counted": 300000})
    st, resp = fm.verb("collector_deposit", dep["name"], "submit")
    check("with 300.000 counted it submits (200)", st == 200, (st, resp))
    dep1 = (resp or {}).get("name", dep["name"])
    c1 = api.get("field_collection", rec(c1)["name"])
    pay = collection_payment(api, c1["name"])
    invs = sorted((i for i in sub_invoices(api, "LGN-00002") if i["docstatus"] == 1 and i["outstanding"] > 0), key=lambda i: (i["due_date"], i["name"]))
    want, left = [], 300000
    for i in invs:
        a = min(left, i["outstanding"])
        if a > 0:
            want.append((i["name"], a))
        left -= a
    eq("the collection is deposited into a draft cash payment of the client, dated the collection",
       (c1.get("deposit"), pay.get("docstatus"), pay.get("method"), pay.get("customer"), pay.get("amount"), pay.get("payment_date")),
       (dep1, 0, "cash", "CUST-0002", 300000, c1["collected_on"]))
    eq("allocated to LGN-00002's oldest invoices first", sorted((a["sales_invoice"], a["amount"]) for a in pay.get("allocations", [])), sorted(want))
    st, resp = col1.patch("field_collection", c1["name"], {"amount": 1000})
    check("a deposited collection cannot change (409/422)", refused(st, resp, "sudah disetor"), (st, resp))
    st, resp = col1.call("DELETE", f"/api/v1/r/field_collection/{Api.q(c1['name'])}")
    check("nor be deleted", refused(st, resp, "sudah disetor"), (st, resp))
    st, resp = fm.patch("payment", pay["name"], {"method": "transfer"})
    check("the deposit's draft payment keeps its method, amount and client (422)", refused(st, resp, "dari setoran penagih"), (st, resp))
    st, resp = fm.call("DELETE", f"/api/v1/r/payment/{Api.q(pay['name'])}")
    check("and is not deleted", refused(st, resp, "dari setoran penagih"), (st, resp))
    eq("the deposit waits on the subscription until finance submits it", api.get("subscription", "LGN-00002")["deposit_waiting"], 300000)
    st, resp = col1.create("field_collection", {"subscription": "LGN-00002", "amount": owed - 300000 + 1})
    check("so what was deposited cannot be collected again meanwhile (422; audit #4)", refused(st, resp, "melebihi yang belum dibayar"), (st, resp))

    # two more collections of the same subscription before finance submits the first payment, in one deposit: each is
    # allocated after the drafts already waiting (the first deposit's, then the one made just before it)
    st, c2 = col1.create("field_collection", {"subscription": "LGN-00002", "amount": 60000})
    st3, c3 = col1.create("field_collection", {"subscription": "LGN-00002", "amount": 40000})
    check("Dodi collects 60.000 and 40.000 more from LGN-00002 (201, 201)", st == 201 and st3 == 201, (st, c2, st3, c3))
    st, d2 = fm.create("collector_deposit", {"collector": "USR-002", "cash_counted": 100000})
    st2, resp = fm.verb("collector_deposit", (d2 or {}).get("name", "-"), "submit")
    check("one deposit of both submits (200)", st == 201 and st2 == 200, (st, d2, st2, resp))

    def window(lo, hi):
        out, pos = [], 0
        for i in invs:
            a = max(0, min(pos + i["outstanding"], hi) - max(pos, lo))
            if a > 0:
                out.append((i["name"], a))
            pos += i["outstanding"]
        return sorted(out)
    pays = {}
    for c, lo, hi in ((c2, 300000, 360000), (c3, 360000, 400000)):
        p = collection_payment(api, rec(c)["name"])
        pays[p.get("name")] = p
        eq(f"collection of {hi - lo} takes the invoices after what the drafts before it cover", sorted((a["sales_invoice"], a["amount"]) for a in p.get("allocations", [])), window(lo, hi))
    pay2 = list(pays.values())
    for p in [pay] + pay2:
        st, resp = fm.verb("payment", p["name"], "submit", {"force": True})
        check(f"finance submits the deposit's payment {p['name']} (200)", st == 200, (st, resp))
    pay1 = collection_payment(api, c1["name"])
    st, resp = fm.verb("payment", pay1.get("name", "-"), "cancel")
    check("a submitted deposit payment cannot be cancelled (422; audit #2)", refused(st, resp, "dari setoran penagih"), (st, resp))
    s2b = api.get("subscription", "LGN-00002")
    eq("no deposit waits any more", s2b["deposit_waiting"], 0)
    eq("LGN-00002 owes 400.000 less", s2b["unpaid_amount"], owed - 400000)
    eq("nothing left in the collector's hands", sum(x["amount"] for x in rows_all(api, "field_collection", "deposit == ''")), 0)

    # an area that changes collector hands its subscriptions over
    st, resp = fm.patch("service_area", "ARE-002", {"collector": "USR-002"})
    check("finance gives Eka's area to Dodi (200)", st == 200, (st, resp))
    eq("Dodi now sees that area's subscriptions too", "LGN-00003" in [x["name"] for x in col1.list("subscription", limit=200)], True)
    eq("Eka sees none", len(col2.list("subscription", limit=200)), 0)
    fm.patch("service_area", "ARE-002", {"collector": "USR-003"})


def scenario_subs_dashboards(api):
    today = kernel_today()
    ws = dashboard(api, "langganan_ringkas")
    subs = rows_all(api, "subscription")
    live = [s for s in subs if s["status"] != "stopped"]
    eq("MRR = monthly value of the subscriptions not stopped", ws.get("mrr", {}).get("value"), sum(s["monthly_net"] for s in live))
    eq("running subscriptions", ws.get("running", {}).get("value"), len(live))
    invs = rows_all(api, "sales_invoice", "docstatus == 1 && notes.contains('{langganan:')")
    month_start = today.replace(day=1).isoformat()
    eq("billed this month", ws.get("billed_this_month", {}).get("value"), sum(i["amount_due"] for i in invs if i["posting_date"] >= month_start))
    eq("suspended", ws.get("suspended", {}).get("value"), len([s for s in subs if s["status"] == "suspended"]))
    age = lambda i: (today - d(i["due_date"])).days
    open_ = [i for i in invs if i["outstanding"] > 0]
    for w, lo, hi in (("overdue_1_30", 1, 30), ("overdue_31_60", 31, 60), ("overdue_61_90", 61, 90), ("overdue_over_90", 91, 10 ** 6)):
        eq(f"aging {w}", ws.get(w, {}).get("value"), sum(i["outstanding"] for i in open_ if lo <= age(i) <= hi))
    check("collection rate widget computed", "collection_rate" in ws, sorted(ws))
    col1 = CTX.get("col1")
    if col1:
        st, resp = col1.call("GET", "/api/v1/dashboards/penagihan_saya")
        mine = {w["name"]: w for w in (resp or {}).get("widgets", [])}
        eq("a collector's dashboard counts only the subscriptions of his areas",
           (st, mine.get("to_collect", {}).get("value")), (200, sum(s["unpaid_amount"] for s in subs if s.get("collector") == "USR-002" and s["unpaid_amount"] > 0)))
        st, resp = col1.call("GET", "/api/v1/dashboards/langganan_ringkas")
        check("and cannot open the owner's overview (403)", st == 403, (st, resp))


SCENARIOS = [setup_demo_langganan, scenario_subs_fixtures, scenario_subs_schedules, scenario_subs_billing, scenario_subs_new_and_change, scenario_subs_end_dates, scenario_subs_invoice_guard,
             scenario_subs_dunning, scenario_subs_payment_lifts, scenario_subs_stopped_arrears, scenario_subs_collectors, scenario_subs_dashboards]
