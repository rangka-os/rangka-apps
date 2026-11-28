"""Day-one scenarios of the dapur piece (module kitchen) on demo-kasir: a paid receipt at the coffee kiosk becomes one
kitchen ticket per station, the kitchen works the tickets through their steps, a void and a correction reach the
kitchen, and the kitchen sees only its own outlet.

Loaded by tests/day_one.py after day_one_pos (bind() hands over its helpers): `python3 tests/day_one.py --site
demo-kasir`. Fixtures (examples/fixtures/demo-kasir): Kedai Riau has two stations, Bar (category Minuman kopi) and
Dapur (Makanan); Toko Dago has none until the scope scenario gives it one. Covered: tickets per station from the
receipt's item categories (no ticket for an unrouted category or a shop), status new -> cooking -> ready -> picked up
with a time per step, one step back clears the stamp, a jump or a void by hand refused, read-only receipt lines,
voided receipt voids its tickets, an amended receipt sends a revision, the 80 mm ticket print (no price), the kitchen
dashboard and the kitchen work screen as the kitchen's home (contract 0030), dark with a tone per new ticket, the
read-only pickup board of ready tickets (rangka-apps#131), and role kitchen limited to its outlet.

Members: Tono (kitchen, Kedai Riau), Wati (outlet_staff, Kedai Riau); Dika, Sari and Rina come from day_one_pos.
"""
import json

H = {}
DGO, RIU = "OUT-02", "OUT-03"
AIR, MI, KSA, KHT, ROTI = "BRG-0001", "BRG-0002", "BRG-0021", "BRG-0022", "BRG-0015"
BAR, DAPUR = "STD-001", "STD-002"
KOPI_CAT, MAKAN_CAT, TOKO_CAT = "KTG-001", "KTG-002", "KTG-003"

_R = {"read"}
_CFG = {"head_office": {"read", "create", "write", "delete", "print"}, "cashier": _R, "kitchen": _R, "outlet_staff": _R}
MATRIX = {
    "kitchen_station": _CFG,
    "kitchen_route": _CFG,
    "kitchen_ticket": {"head_office": {"read", "create", "write", "export", "print"}, "cashier": {"read", "create", "write", "print"},
                       "kitchen": {"read", "write", "print"}, "outlet_staff": _R},
    "kitchen_guard": {"head_office": {"create"}, "cashier": {"create"}, "kitchen": {"create"}},   # never stored
}
MEMBERS = [("tono", "kitchen", "tono.riau@contohwarung.test", "Tono Contoh", RIU),
           ("wati", "outlet_staff", "wati.riau@contohwarung.test", "Wati Contoh", RIU)]


def bind(m):
    for k in ("ok", "fail", "check", "eq", "CTX", "Api", "denied", "dashboard", "num", "chart_map", "secrets", "page_text"):
        H[k] = getattr(m, k)
    globals().update(H)
    m.MATRIX.update(MATRIX)


def rec(x):
    return (x or {}).get("record", x) or {}


def rules(resp):
    return [e.get("rule") for e in (resp or {}).get("errors", [])] if isinstance(resp, dict) else []


def setup(api, k):
    """Kitchen staff and an outlet staff member at Kedai Riau (after day_one_pos.setup_demo_kasir)."""
    org = CTX.get("org") or "warung_contoh"
    pw = secrets.token_urlsafe(18)   # throwaway password of a throwaway database
    for key, role, email, name, outlets in MEMBERS:
        code, out = k.user_add(email, role, org, name, outlets=outlets, password=pw)
        cl = Api.login(k.base, email, pw) if code == 0 else None
        if cl is None:
            fail("setup: add and sign in the kitchen members", f"{email}: " + (out if code else "login refused"))
            return
        CTX[key] = cl


def tickets(cl, sale):
    return sorted(cl.find("kitchen_ticket", f"sale == '{sale}'", children=1), key=lambda t: t["station"])


def lines(t):
    return sorted((l["item"], l["qty"], l.get("note") or "") for l in t.get("items") or [])


# kitchen 2.0 (rangka-apps#131): staff move lines; the ticket's status follows them
LINE_STEP = {"new": "new", "cooking": "in_progress", "ready": "ready", "picked_up": "done"}


def get_ticket(cl, name):
    return cl.call("GET", f"/api/v1/r/kitchen_ticket/{name}?children=1")


def set_lines(cl, name, pick, values, guard=("held", "seen", "status")):
    """What the kitchen screen writes (rangka-os contract 0077): every line as loaded, values on the picked lines, and
    _if on every line's state, so another device's change is a 409."""
    st, t = get_ticket(cl, name)
    if st != 200:
        return st, t
    rows, cond = [], {}
    for l in t.get("items") or []:
        cond[l["name"]] = {k: l.get(k) for k in guard}
        r = dict(l)
        if pick(l):
            r.update(values)
        rows.append(r)
    return cl.patch("kitchen_ticket", name, {"items": rows, "_if": {"items": cond}})


def normal(l):
    return (l.get("kind") or "normal") == "normal" and not l.get("held")


def to_status(cl, name, status):
    """Every line not held moved so the ticket reaches status (cooking, ready, picked_up); any other value is written
    as the ticket's status, which a client cannot set (computed)."""
    if status not in LINE_STEP:
        return cl.patch("kitchen_ticket", name, {"status": status})
    return set_lines(cl, name, normal, {"status": LINE_STEP[status]})


def open_shift(cl, outlet, label):
    mine = cl.find("pos_shift", f"outlet == '{outlet}' && state == 'open' && cashier == user.email")
    if mine:   # the cashier's shift of day_one_pos is still open
        return mine[0]["name"]
    st, s = cl.create("pos_shift", {"outlet": outlet, "opening_cash": 200_000})
    check(f"{label}: shift opened -> 201", st == 201, (st, s))
    return rec(s).get("name") if st == 201 else None


def pay(cl, shift, items, label, **extra):
    st, s = cl.create("pos_sale", dict({"shift": shift, "items": items}, **extra))
    if st != 201:
        check(f"{label}: draft -> 201", False, (st, s))
        return {}
    st, r = cl.verb("pos_sale", rec(s)["name"], "submit")
    check(f"{label}: paid -> 200", st == 200, (st, r))
    return rec(r) if st == 200 else {}


def scenario_kitchen_tickets(api):
    """Sari's first receipt at Kedai Riau (Meja 4: 2 kopi susu aren kurang manis, a kopi hitam, a roti) went to the
    kitchen as two tickets: Bar with the coffees, Dapur with the roti. No price on a ticket."""
    sale = CTX.get("sale_riu")
    if not sale:
        fail("kitchen: the Kedai Riau receipt of day_one_pos", "missing")
        return
    ts = tickets(api, sale["name"])
    if not check("the receipt made one ticket per station -> Bar and Dapur", [t["station"] for t in ts] == [BAR, DAPUR], ts):
        return
    bar, dapur = ts
    eq("Bar: the coffees, with the cashier's note", lines(bar), sorted([(KSA, 2, "kurang manis"), (KHT, 1, "")]))
    eq("Dapur: the roti", lines(dapur), [(ROTI, 1, "")])
    for t in ts:
        check(f"{t['station']}: new, today, Kedai Riau, card title Meja 4, receipt linked, sent_at stamped",
              t["status"] == "new" and t["outlet"] == RIU and t["heading"] == "Meja 4" and t["sale"] == sale["name"]
              and t["date"] == sale["date"] and bool(t.get("sent_at")) and not t.get("cooking_at"), t)
        check(f"{t['station']}: numbered per outlet TKD-RIU-", str(t["name"]).startswith("TKD-RIU-"), t["name"])
    eq("porsi on the Bar ticket", bar["item_count"], 3)
    check("a ticket carries no price", not any(k in bar for k in ("rate", "amount", "grand_total")) and
          not any(k in l for l in bar["items"] for k in ("rate", "amount")), bar)
    CTX["kt_bar"], CTX["kt_dapur"] = bar["name"], dapur["name"]
    # the receipt's title is its number, not its total: the kitchen finds a ticket by the receipt it came from, and the
    # total of a receipt never shows through the link (audit A4 delta F2)
    tono, total = CTX.get("tono"), int(float(sale.get("grand_total") or 0))
    if tono and total:
        eq("kitchen search by the receipt's total finds nothing (the link title is the receipt number)",
           [t["name"] for t in tono.list("kitchen_ticket", q=str(total), limit=50) if t["sale"] == sale["name"]], [])
        eq("kitchen search by the receipt number finds its tickets", sorted(t["name"] for t in tono.list("kitchen_ticket", q=sale["name"], limit=50)
                                                                          if t["sale"] == sale["name"]), sorted([bar["name"], dapur["name"]]))


def scenario_kitchen_steps(api):
    """Kitchen 2.0 (rangka-apps#131): Tono (kitchen, Kedai Riau) works the Bar ticket line by line. One line moves while
    the other waits, the ticket's status follows its lines and each status is stamped; a step back clears the stamp;
    a second tablet's stale write is refused (409); menu, quantity, note, station and table stay read-only; the
    ticket's status and voided cannot be written by hand; Sari hands it over at the pass."""
    tono, sari = CTX.get("tono"), CTX.get("sari")
    bar = CTX.get("kt_bar")
    if not (tono and sari and bar):
        return
    st, t = get_ticket(tono, bar)
    ksa = [l for l in (t or {}).get("items") or [] if l.get("item") == KSA]
    if not check("the Bar ticket's lines start Baru, not held, with the station's verbs",
                 st == 200 and ksa and all(l.get("status") == "new" and not l.get("held") and (l.get("kind") or "normal") == "normal" for l in t["items"])
                 and t.get("start_label") == "Proses" and t.get("ready_label") == "Siap", t):
        return
    st, r = set_lines(tono, bar, lambda l: l["item"] == KSA, {"status": "in_progress"})
    t = rec(r)
    check("Tono: Proses on the kopi susu aren only -> 200, the ticket is Diproses, cooking_at stamped, the kopi hitam waits",
          st == 200 and t.get("status") == "cooking" and bool(t.get("cooking_at"))
          and sorted((l["item"], l["status"]) for l in t.get("items") or []) == sorted([(KSA, "in_progress"), (KHT, "new")]), (st, r))
    cooking_at = t.get("cooking_at")
    # a second tablet still shows both lines as Baru: its write is refused, nothing changes
    rows = [dict(l, status="ready") for l in t.get("items") or []]
    st, r = tono.patch("kitchen_ticket", bar, {"items": rows, "_if": {"items": {l["name"]: {"status": "new"} for l in t.get("items") or []}}})
    check("a stale tablet's write -> 409 precondition_failed", st == 409, (st, r))
    st, r = set_lines(tono, bar, lambda l: l["item"] == KSA, {"status": "ready"})
    check("the kopi susu aren Siap while the kopi hitam is still Baru: the ticket stays Diproses", st == 200 and rec(r).get("status") == "cooking", (st, r))
    st, r = set_lines(tono, bar, normal, {"status": "ready"})
    t = rec(r)
    check("Semua siap -> 200, the ticket is Siap, ready_at stamped, cooking_at kept",
          st == 200 and t.get("status") == "ready" and bool(t.get("ready_at")) and t.get("cooking_at") == cooking_at, (st, r))
    st, r = set_lines(tono, bar, lambda l: l["item"] == KHT, {"status": "in_progress"})
    t = rec(r)
    check("Kembalikan (one line back) -> 200, the ticket is Diproses again, ready_at cleared, cooking_at kept",
          st == 200 and t.get("status") == "cooking" and not t.get("ready_at") and t.get("cooking_at") == cooking_at, (st, r))
    st, t = get_ticket(tono, bar)
    rows = [dict(l, qty=9, description="Air", note="x", item=AIR) for l in t.get("items") or []]
    st, r = tono.patch("kitchen_ticket", bar, {"items": rows, "station": DAPUR, "buyer": "Meja 9", "status": "picked_up", "voided": True})
    t = get_ticket(api, bar)[1] or {}
    check("menu, quantity, note, station and table stay as the receipt sent them; status and voided cannot be written",
          t.get("station") == BAR and t.get("buyer") == "Meja 4" and lines(t) == sorted([(KSA, 2, "kurang manis"), (KHT, 1, "")])
          and t.get("status") == "cooking" and not t.get("voided"), (st, r, t))
    st, r = set_lines(tono, bar, normal, {"status": "ready"})
    st, r = to_status(sari, bar, "picked_up")
    t = rec(r)
    check("Sari (cashier) hands it over at the pass (Serahkan) -> 200, Diambil, picked_up_at stamped",
          st == 200 and t.get("status") == "picked_up" and bool(t.get("picked_up_at")) and all(l.get("status") == "done" for l in t.get("items") or []), (st, r))
    st, r = tono.create("kitchen_ticket", {"outlet": RIU, "station": BAR, "items": [{"item": KHT, "qty": 1}]})
    check("Tono cannot write a ticket of his own -> 403", denied(st, r), (st, r))
    st, r = tono.call("GET", "/api/v1/r/pos_sale?limit=1")
    check("Tono reads no receipt (no money) -> 403", denied(st, r), (st, r))
    st, raw, ctype = tono.print_html("kitchen_ticket", CTX["kt_dapur"], "kitchen_ticket")
    text = page_text(raw)
    check("tiket dapur prints 80 mm HTML: station, table, qty x menu, no Rp",
          st == 200 and ctype.startswith("text/html") and "DAPUR" in text.upper() and "Meja 4" in text and "1x" in text
          and "Roti bakar cokelat" in text and "Rp" not in text, (st, text[:400]))


def scenario_kitchen_void_and_revision(api):
    """A voided receipt voids its open tickets; a void ticket stays void. A corrected receipt (amend without the roti)
    sends its coffees again as a revision, and no new Dapur ticket."""
    sari, tono = CTX.get("sari"), CTX.get("tono")
    if not (sari and tono):
        return
    shift = open_shift(sari, RIU, "Sari, second shift at Kedai Riau")
    if not shift:
        return
    CTX["shift_riu_kitchen"] = shift
    s1 = pay(sari, shift, [{"item": KHT, "qty": 1}, {"item": ROTI, "qty": 1}], "Meja 7: kopi hitam and roti", buyer="Meja 7")
    if not s1:
        return
    ts = tickets(api, s1["name"])
    check("Meja 7 made a Bar and a Dapur ticket", [t["station"] for t in ts] == [BAR, DAPUR], ts)
    st, r = to_status(tono, ts[1]["name"], "cooking")
    check("Tono starts the roti", st == 200, (st, r))
    st, r = sari.verb("pos_sale", s1["name"], "cancel")
    if not check("Sari voids the Meja 7 receipt -> 200", st == 200, (st, r)):
        return
    ts = tickets(api, s1["name"])
    check("both tickets are void, void_at stamped", all(t["status"] == "void" and t.get("void_at") for t in ts), ts)
    st, r = to_status(tono, ts[0]["name"], "cooking")
    check("a void ticket does not come back -> 422 ticket_void", st == 422 and "ticket_void" in rules(r), (st, r))
    st, am = sari.verb("pos_sale", s1["name"], "amend")
    am = rec(am)
    if not check("Sari amends it -> a new draft", st in (200, 201) and am.get("name"), (st, am)):
        return
    keep = [l for l in am.get("items") or [] if l.get("item") == KHT]
    st, r = sari.patch("pos_sale", am["name"], {"items": [{"item": l["item"], "qty": l["qty"]} for l in keep]})
    check("the amended draft keeps only the kopi hitam", st == 200, (st, r))
    st, r = sari.verb("pos_sale", am["name"], "submit")
    check("the corrected receipt is paid -> 200", st == 200, (st, r))
    ts = tickets(api, rec(r).get("name"))
    check("the corrected receipt sends one Bar ticket marked as a revision of the voided receipt",
          [t["station"] for t in ts] == [BAR] and ts[0]["revision_of"] == s1["name"] and lines(ts[0]) == [(KHT, 1, "")]
          and ts[0]["heading"] == "REVISI Meja 7", ts)
    st, raw, _ = api.print_html("kitchen_ticket", ts[0]["name"], "kitchen_ticket") if ts else (0, "", "")
    check("the revision ticket prints REVISI", "REVISI" in page_text(raw), page_text(raw)[:300])
    CTX["kt_revision"] = ts[0]["name"] if ts else None
    hq = CTX.get("hq")
    if hq:
        s2 = pay(hq, shift, [{"item": KSA, "qty": 1}], "Rina (head office) rings up a kopi susu aren on Sari's shift")
        ts = tickets(api, s2["name"]) if s2 else []
        check("head office's payment sends the Bar ticket too", [t["station"] for t in ts] == [BAR], ts)
    st, r = sari.create("kitchen_ticket", {"sale": s1["name"], "outlet": RIU, "station": BAR, "items": [{"item": KHT, "qty": 5}]})
    check("a ticket typed by hand is refused (its lines come only from a receipt)", st in (400, 422), (st, r))


def scenario_kitchen_served_revision(api):
    """Audit A4 L1/L2: Meja 12 (kopi susu aren, roti) is picked up, then voided and corrected (kopi susu aren kept, a kopi
    hitam added, roti returned). The kitchen makes only the kopi hitam; the coffee already served is kept as a ticket
    Diambil from the start, and a picked-up ticket of the voided receipt cannot be pulled back into the queue."""
    sari, tono = CTX.get("sari"), CTX.get("tono")
    shift = CTX.get("shift_riu_kitchen")
    if not (sari and tono and shift):
        return
    s1 = pay(sari, shift, [{"item": KSA, "qty": 1}, {"item": ROTI, "qty": 1}], "Meja 12: kopi susu aren and roti", buyer="Meja 12")
    if not s1:
        return
    ts = tickets(api, s1["name"])
    for t in ts:
        for step in ("cooking", "ready", "picked_up"):
            st, r = to_status(tono, t["name"], step)
            if st != 200:
                check(f"Meja 12 {t['station']}: {step}", False, (st, r))
    ts = tickets(api, s1["name"])
    check("Meja 12: both tickets picked up", [t["status"] for t in ts] == ["picked_up", "picked_up"], ts)
    st, r = sari.verb("pos_sale", s1["name"], "cancel")
    if not check("Sari voids Meja 12 after the food went out -> 200", st == 200, (st, r)):
        return
    ts = tickets(api, s1["name"])
    check("picked-up tickets stay picked up, marked as of a voided receipt", all(t["status"] == "picked_up" and t.get("sale_void") for t in ts), ts)
    st, r = to_status(tono, ts[0]["name"], "ready")
    check("Urungkan on a picked-up ticket of a voided receipt -> 422 sale_void", st == 422 and "sale_void" in rules(r), (st, r))
    st, am = sari.verb("pos_sale", s1["name"], "amend")
    am = rec(am)
    if not check("Sari amends Meja 12 -> a new draft", st in (200, 201) and am.get("name"), (st, am)):
        return
    st, r = sari.patch("pos_sale", am["name"], {"items": [{"item": KSA, "qty": 1}, {"item": KHT, "qty": 1}]})
    check("the corrected Meja 12: kopi susu aren kept, kopi hitam added, roti returned", st == 200, (st, r))
    st, r = sari.verb("pos_sale", am["name"], "submit")
    check("the corrected Meja 12 is paid -> 200", st == 200, (st, r))
    ts = tickets(api, rec(r).get("name"))
    eq("revision: the kitchen makes only the kopi hitam; the served kopi susu aren is kept as Diambil; no Dapur ticket",
       sorted((t["station"], t["status"], tuple(lines(t))) for t in ts),
       sorted([(BAR, "new", ((KHT, 1, ""),)), (BAR, "picked_up", ((KSA, 1, ""),))]))
    check("every revision ticket is marked REVISI", all(t["heading"] == "REVISI Meja 12" for t in ts), ts)
    # audit K1: one served coffee, a revision with two lines of that coffee: one is served, the other is cooked
    s3 = pay(sari, shift, [{"item": KSA, "qty": 1}], "Meja 15: one kopi susu aren", buyer="Meja 15")
    if not s3:
        return
    t3 = tickets(api, s3["name"])
    for step in ("cooking", "ready", "picked_up"):
        to_status(tono, t3[0]["name"], step)
    sari.verb("pos_sale", s3["name"], "cancel")
    st, am = sari.verb("pos_sale", s3["name"], "amend")
    am = rec(am)
    sari.patch("pos_sale", am["name"], {"items": [{"item": KSA, "qty": 1, "note": "panas"}, {"item": KSA, "qty": 1, "note": "dingin"}]})
    st, r = sari.verb("pos_sale", am["name"], "submit")
    eq("revision with two lines of a served item: the first line counts as served, the second is cooked",
       sorted((t["status"], tuple(lines(t))) for t in tickets(api, rec(r).get("name"))),
       sorted([("new", ((KSA, 1, "dingin"),)), ("picked_up", ((KSA, 1, "panas"),))]))


def scenario_kitchen_scope(api):
    """A shop sells without tickets; once head office gives Toko Dago a station, Dago's tickets stay out of Tono's
    reach (row scope by outlet, contract 0010), and Wati (outlet staff, Kedai Riau) reads Riau's tickets only."""
    dika, tono, wati, hq = CTX.get("dika"), CTX.get("tono"), CTX.get("wati"), CTX.get("hq")
    if not (dika and tono and wati and hq):
        return
    shift = open_shift(dika, DGO, "Dika, kitchen check at Toko Dago")
    if not shift:
        return
    s = pay(dika, shift, [{"item": MI, "qty": 2}], "Toko Dago sells mi instan")
    check("a shop without stations makes no ticket", s and tickets(api, s["name"]) == [], s)
    st, sd = hq.create("kitchen_station", {"station_name": "Dapur", "outlet": DGO})
    check("head office adds a station at Toko Dago -> 201", st == 201, (st, sd))
    st, r = hq.create("kitchen_station", {"station_name": "Dapur", "outlet": DGO})
    check("a second Dapur at Toko Dago is refused (one name per outlet)", st in (409, 422), (st, r))
    st, rt = hq.create("kitchen_route", {"station": rec(sd).get("name"), "category": TOKO_CAT})
    check("head office routes Barang toko there -> 201, the route takes the station's outlet", st == 201 and rec(rt).get("outlet") == DGO, (st, rt))
    st, r = tono.create("kitchen_route", {"station": BAR, "category": TOKO_CAT})
    check("Tono cannot change the routes -> 403", denied(st, r), (st, r))
    s = pay(dika, shift, [{"item": MI, "qty": 1}], "Toko Dago sells mi instan again")
    dts = tickets(api, s["name"]) if s else []
    if not check("now Toko Dago's receipt makes a ticket", len(dts) == 1 and dts[0]["outlet"] == DGO, dts):
        return
    dgo_t = dts[0]["name"]
    st, r = tono.call("GET", f"/api/v1/r/kitchen_ticket/{dgo_t}")
    check("Tono cannot open Toko Dago's ticket -> 403/404", st in (403, 404), (st, r))
    st, r = to_status(tono, dgo_t, "cooking")
    check("Tono cannot move Toko Dago's ticket -> 403/404", st in (403, 404), (st, r))
    for key, cl in (("Tono", tono), ("Wati", wati)):
        outs = {t["outlet"] for t in cl.find("kitchen_ticket", "true")}
        check(f"{key} lists Kedai Riau's tickets only", outs == {RIU}, outs)
    st, r = to_status(wati, CTX.get("kt_dapur"), "cooking")
    check("Wati (outlet staff) reads tickets but cannot move them -> 403", denied(st, r), (st, r))
    st, r = dika.call("GET", "/api/v1/r/kitchen_ticket/" + (CTX.get("kt_dapur") or "x"))
    check("Dika (cashier, Toko Dago) cannot open Kedai Riau's ticket -> 403/404", st in (403, 404), (st, r))


def scenario_kitchen_dashboard_and_screen(api):
    """Dapur hari ini counts the outlet's tickets for kitchen staff, every outlet for head office. The kitchen screen
    is the kitchen's home (contract 0030 §3); outlet staff are not offered it."""
    tono, wati, hq = CTX.get("tono"), CTX.get("wati"), CTX.get("hq")
    if not (tono and wati and hq):
        return
    riu = api.find("kitchen_ticket", f"outlet == '{RIU}'")
    allt = api.find("kitchen_ticket", "true")

    def count(ts, status):
        return sum(1 for t in ts if t["status"] == status)

    wt = dashboard(tono, "dapur_hari_ini")
    if wt:
        eq("Tono's dashboard: Kedai Riau only (new, cooking, ready, picked up, void)",
           [num(wt[n]) for n in ("waiting", "cooking", "ready", "done", "voided")],
           [count(riu, s) for s in ("new", "cooking", "ready", "picked_up", "void")])
        check("Tono's open list holds Kedai Riau tickets only", {r.get("outlet") for r in wt["open_list"].get("rows", [])} <= {RIU}, wt["open_list"])
    wh = dashboard(hq, "dapur_hari_ini")
    if wh:
        eq("head office's dashboard: every outlet", num(wh["waiting"]), count(allt, "new"))
    st, m = tono.call("GET", "/api/v1/meta")
    work = (m or {}).get("work") or []
    check("Tono's home is the kitchen screen (meta.work: dapur, home)", work[:1] == [{"screen": "dapur", "home": True}], work)
    sc = ((m or {}).get("screens") or {}).get("dapur") or {}
    check("the kitchen screen is a queue over kitchen_ticket", sc.get("type") == "queue" and sc.get("queue", {}).get("source", {}).get("model") == "kitchen_ticket", sc)
    st, r = tono.call("GET", "/api/v1/screens/dapur")
    check("Tono opens the kitchen screen -> 200", st == 200, (st, r))
    # rangka-apps#131: the station screen works per line (dark, a tone per new line), the pass and the guest board
    q = sc.get("queue") or {}
    check("the kitchen screen is dark, sounds on a new line, and works per line (line steps with the station's verbs, Semua siap, totals, recall)",
          sc.get("theme") == "dark" and q.get("alert") == "new" and [x.get("label_field") for x in q.get("line_steps") or []] == [None, "start_label", "ready_label"]
          and len(q.get("bulk_steps") or []) == 1 and q.get("totals", {}).get("by") == "description" and q.get("recall", {}).get("set") == {"status": "in_progress"}, sc)
    words = json.dumps(sc) + json.dumps(((m or {}).get("screens") or {}).get("penyerahan") or {})
    check("no screen says Masak or Dimasak", "Masak" not in words and "Dimasak" not in words, words[:300])
    for scr in ("penyerahan", "siap_ambil"):
        check(f"Tono is also offered {scr} (meta.work)", {"screen": scr, "home": False} in work, work)
        st, r = tono.call("GET", f"/api/v1/screens/{scr}")
        check(f"Tono opens {scr} -> 200", st == 200, (st, r))
    pas = ((m or {}).get("screens") or {}).get("penyerahan") or {}
    check("the pass joins an order's tickets (group_by sale), fires held lines (Lepas) and hands over what is ready",
          (pas.get("queue") or {}).get("group_by") == "sale" and [L0.get("set") for L0 in (pas.get("queue") or {}).get("line_steps") or []] == [{"held": False, "fired_at": "now()"}], pas)
    board = ((m or {}).get("screens") or {}).get("siap_ambil") or {}
    bq = board.get("queue") or {}
    check("the guest board is dark and read-only: orders joined by sale in two columns (Sedang disiapkan, Siap diambil)",
          board.get("theme") == "dark" and not bq.get("line_steps") and not bq.get("bulk_steps") and not bq.get("steps")
          and bq.get("group_by") == "sale" and bq.get("columns") == {"by": "board", "values": ["preparing", "ready"]}, board)
    st, m = wati.call("GET", "/api/v1/meta")
    check("Wati is not offered the kitchen screen", "dapur" not in ((m or {}).get("screens") or {}), (m or {}).get("work"))
    st, r = wati.call("GET", "/api/v1/screens/dapur")
    check("Wati opening the kitchen screen -> 404", st == 404, (st, r))
    st, m = CTX["sari"].call("GET", "/api/v1/meta")
    check("Sari (cashier) gets the kitchen screen, not as home", {"screen": "dapur", "home": False} in ((m or {}).get("work") or []), (m or {}).get("work"))


def scenario_kitchen_on_draft(api):
    """Kirim ke dapur from an open table bill (rangka-apps#122): tickets go out when the bill is sent, again only for
    new lines, a removed sent line makes a BATAL line, and payment skips what was already sent."""
    sari = CTX.get("sari")
    shift = CTX.get("shift_riu_kitchen") or (open_shift(sari, RIU, "tables") if sari else None)
    if not (sari and shift):
        return

    def L(key, item, qty):
        return {"item": item, "qty": qty, "line_key": key}

    st, b = sari.create("pos_sale", {"shift": shift, "table": "MJA-RIU-007", "guests": 2, "items": [L("k1", KSA, 1), L("k2", ROTI, 1)]})
    if not check("open bill on table B1 -> 201", st == 201, (st, b)):
        return
    name = rec(b)["name"]
    st, r = sari.patch("pos_sale", name, {"send_kitchen": True})
    ts = tickets(api, name)
    now = sari.get("pos_sale", name)
    check("Kirim ke dapur: a Bar ticket (kopi) and a Dapur ticket (roti) titled with the table; the check clears",
          st == 200 and [t["station"] for t in ts] == [BAR, DAPUR] and all(t["heading"] == "B1" for t in ts) and not now.get("send_kitchen")
          and len(now.get("sent_to_kitchen") or []) == 2, (st, r, ts, now))
    st, r = sari.patch("pos_sale", name, {"send_kitchen": True})
    eq("sending again with nothing new makes no ticket", len(tickets(api, name)), 2)
    st, r = sari.patch("pos_sale", name, {"items": [L("k1", KSA, 1), L("k2", ROTI, 1), L("k3", KHT, 1)], "send_kitchen": True})
    ts = tickets(api, name)
    check("a new line sent: one more ticket, only the new line", st == 200 and len(ts) == 3 and len([t for t in ts if t["station"] == BAR and lines(t) == [(KHT, 1, "")]]) == 1, (st, r, ts))
    st, r = sari.patch("pos_sale", name, {"items": [L("k1", KSA, 1), L("k3", KHT, 1)], "send_kitchen": True})
    ts = tickets(api, name)
    batal = [l for t in ts for l in t["items"] if l.get("kind") == "void"]
    check("a sent line removed: a Batal notice line for the roti at the Dapur station (kind void, key kept)",
          st == 200 and len(ts) == 4 and len(batal) == 1 and batal[0]["item"] == ROTI and batal[0].get("line_key") == "k2", (st, r, ts))
    tono = CTX.get("tono")
    nt = [t for t in ts if any(l.get("kind") == "void" for l in t["items"])][0]
    if tono:
        st, r = set_lines(tono, nt["name"], lambda l: True, {"status": "in_progress"})
        check("a notice line cannot be worked on -> 422 notice_line", st == 422 and "notice_line" in rules(r), (st, r))
        st, r = set_lines(tono, nt["name"], lambda l: True, {"seen": True})
        check("Oke on the notice -> 200, the notice ticket leaves the station (Diambil)", st == 200 and rec(r).get("status") == "picked_up", (st, r))
    # the kitchen knows a sent line by its key: a reused key, a raised quantity or a doubled key would hide a dish from it
    st, r = sari.patch("pos_sale", name, {"items": [L("k1", ROTI, 1), L("k3", KHT, 1)], "send_kitchen": True})
    check("a sent line's key reused for another item is refused (sent_line_kept)", st != 200 and "sent_line_kept" in str(r), (st, r))
    st, r = sari.patch("pos_sale", name, {"items": [L("k1", KSA, 2), L("k3", KHT, 1)], "send_kitchen": True})
    check("a sent line's quantity raised is refused (sent_line_kept)", st != 200 and "sent_line_kept" in str(r), (st, r))
    st, r = sari.patch("pos_sale", name, {"items": [L("k1", KSA, 1), L("k3", KHT, 1), L("k3", ROTI, 1)], "send_kitchen": True})
    check("two lines with one key are refused (line_key_unique)", st != 200 and "line_key_unique" in str(r), (st, r))
    st, kv = api.create("uom_conversion", {"item": KSA, "uom": "UOM-006", "factor": 4})
    kv = rec(kv).get("name")
    st2, r = sari.patch("pos_sale", name, {"items": [dict(L("k1", KSA, 1), packing=kv), L("k3", KHT, 1)], "send_kitchen": True})
    check("a sent line switched to a pack of 4 is refused (sent_line_kept)", st == 201 and st2 != 200 and "sent_line_kept" in str(r), (st, kv, st2, r))
    eq("no ticket came from the refused writes", len(tickets(api, name)), 4)
    st, r = sari.patch("pos_sale", name, {"items": [L("k1", KSA, 1), L("k3", KHT, 1), L("k4", ROTI, 1)]})
    st, p = sari.verb("pos_sale", name, "submit", {"payments": [{"method": "cash", "amount": 100_000}]})
    paid = rec(p).get("name") if st == 200 else name
    ts = tickets(api, paid)
    dapur = [t for t in ts if t["station"] == DAPUR and not any(l.get("kind") == "void" for l in t["items"])]
    check("payment sends only the line never sent (roti k4): 5 tickets, none for the coffees again",
          st == 200 and len(ts) == 5 and len([t for t in ts if t["station"] == BAR]) == 2 and len(dapur) == 2
          and lines(sorted(dapur, key=lambda t: t["sent_at"])[-1]) == [(ROTI, 1, "")], (st, [(t["station"], lines(t)) for t in ts]))


def scenario_kitchen_hold_and_change(api):
    """Kitchen 2.0 (rangka-apps#131): Sari sends table 7's bill with the roti held (Tahan: after the coffee). The Dapur
    line waits held: Tono cannot move it, the station still lists the ticket; Sari fires it at the pass (Lepas) and Tono
    works it. Then Sari lowers the kopi from 2 to 1 and changes its note after it was sent: the Bar gets a Diubah
    notice with what it was. A line already being made cannot be held again."""
    sari, tono = CTX.get("sari"), CTX.get("tono")
    shift = CTX.get("shift_riu_kitchen")
    if not (sari and tono and shift):
        return

    def L(key, item, qty, **kw):
        return dict({"item": item, "qty": qty, "line_key": key}, **kw)

    st, b = sari.create("pos_sale", {"shift": shift, "table": "MJA-RIU-007", "guests": 2,
                                     "items": [L("h1", KSA, 2, note="kurang manis"), L("h2", ROTI, 1, kitchen_hold=True)]})
    if not check("table 7: a bill with the roti held -> 201", st == 201, (st, b)):
        return
    name = rec(b)["name"]
    st, r = sari.patch("pos_sale", name, {"send_kitchen": True})
    check("Kirim ke dapur -> 200", st == 200, (st, r))
    ts = tickets(api, name)
    dap = [t for t in ts if t["station"] == DAPUR]
    if not check("the Dapur ticket's roti line is held; the ticket counts it (held_count 1) and stays Baru",
                 len(dap) == 1 and dap[0]["items"][0].get("held") is True and dap[0].get("held_count") == 1 and dap[0]["status"] == "new"
                 and dap[0]["items"][0].get("line_key") == "h2", ts):
        return
    dname = dap[0]["name"]
    listed = [t["name"] for t in tono.find("kitchen_ticket", "date == today() && (status == 'new' || status == 'cooking' || held_count > 0)")]
    check("the station's screen filter still lists the ticket of a held line", dname in listed, listed)
    st, r = set_lines(tono, dname, lambda l: True, {"status": "in_progress"})
    check("Tono cannot work a held line -> 422 held_line", st == 422 and "held_line" in rules(r), (st, r))
    st, r = set_lines(sari, dname, lambda l: l.get("held"), {"held": False, "fired_at": "2026-11-28T12:00:00Z"})
    check("Sari fires it at the pass (Lepas) -> 200, the line is Baru and not held", st == 200 and not rec(r)["items"][0].get("held")
          and rec(r)["items"][0].get("status") == "new", (st, r))
    st, r = set_lines(tono, dname, normal, {"status": "in_progress"})
    check("Tono now works the roti -> 200, Diproses", st == 200 and rec(r).get("status") == "cooking", (st, r))
    st, r = set_lines(sari, dname, lambda l: True, {"held": True})
    check("a line already being made cannot be held again -> 422 hold_started", st == 422 and "hold_started" in rules(r), (st, r))
    st, r = sari.patch("pos_sale", name, {"items": [L("h1", KSA, 1, note="panas"), L("h2", ROTI, 1)], "send_kitchen": True})
    ts = tickets(api, name)
    chg = [l for t in ts for l in t["items"] if l.get("kind") == "change"]
    check("the kopi lowered to 1 and its note changed after sending: a Diubah notice at the Bar with what it was",
          st == 200 and len(chg) == 1 and chg[0]["item"] == KSA and chg[0]["qty"] == 1 and "panas" in chg[0].get("note", "")
          and "tadi 2" in chg[0].get("note", "") and "kurang manis" in chg[0].get("note", "") and chg[0].get("line_key") == "h1", (st, r, chg))
    st, r = sari.patch("pos_sale", name, {"send_kitchen": True})
    eq("sending again with nothing changed makes no notice", len([l for t in tickets(api, name) for l in t["items"] if l.get("kind") == "change"]), 1)


SCENARIOS = [scenario_kitchen_tickets, scenario_kitchen_steps, scenario_kitchen_void_and_revision, scenario_kitchen_served_revision,
             scenario_kitchen_scope, scenario_kitchen_on_draft, scenario_kitchen_hold_and_change,
             scenario_kitchen_dashboard_and_screen]


# ---------------- regressions from the rk-auditor attack scenarios on kitchen 2.0 (reports/audit/2026-11-28-audit-kds-131-apps.md):
# revisions after notices and partial hand-overs, staff writes that drop or add lines or move a held line, Sajikan nanti
# through merge and split, and the printed markers of notice and held lines. They keep off the tables the self order
# scenarios use (A3, A4, A6, B2, B3): a receipt paid on a table opens its QR session.

def _summ(ts):
    return sorted((t["station"], t["status"], tuple(sorted((l["item"], l["qty"], l.get("kind") or "normal", l.get("status"), bool(l.get("held"))) for l in t.get("items") or []))) for t in ts)


def _amend_pay(sari, paid, label):
    st, r = sari.verb("pos_sale", paid, "cancel")
    check(f"{label}: void -> 200", st == 200, (st, r))
    st, am = sari.verb("pos_sale", paid, "amend")
    am = rec(am)
    st, r = sari.verb("pos_sale", am.get("name"), "submit")
    if st != 200:
        st, r = sari.verb("pos_sale", am.get("name"), "submit", {"payments": [{"method": "cash", "amount": 100_000}]})
    check(f"{label}: amended receipt paid -> 200", st == 200, (st, r))
    return rec(r).get("name")


def scenario_kitchen_audit_a1_change_notice_served(api):
    sari, tono = CTX.get("sari"), CTX.get("tono")
    shift = CTX.get("shift_riu_kitchen") or open_shift(sari, RIU, "a1")
    st, b = sari.create("pos_sale", {"shift": shift, "table": "MJA-RIU-005", "guests": 2, "items": [{"item": ROTI, "qty": 2, "line_key": "a1"}]})
    name = rec(b).get("name")
    sari.patch("pos_sale", name, {"send_kitchen": True})
    st, r = sari.patch("pos_sale", name, {"items": [{"item": ROTI, "qty": 1, "line_key": "a1"}], "send_kitchen": True})
    ts = tickets(api, name)
    notice = [t for t in ts if any(l.get("kind") == "change" for l in t["items"])]
    orig = [t for t in ts if not any(l.get("kind") == "change" for l in t["items"])]
    st1, _ = set_lines(tono, notice[0]["name"], lambda l: True, {"seen": True})
    st2, _ = to_status(tono, orig[0]["name"], "cooking")
    ts = tickets(api, name)
    st, p = sari.verb("pos_sale", name, "submit", {"payments": [{"method": "cash", "amount": 100_000}]})
    paid = rec(p).get("name")
    newname = _amend_pay(sari, paid, "A1")
    rts = tickets(api, newname)
    eq("A1: revision after a change notice was read: the roti nobody served goes to Dapur as new", [(t["status"], lines(t)) for t in rts if t["status"] == "new"], [("new", [(ROTI, 1, "")])])


def scenario_kitchen_audit_a2_partial_handover(api):
    sari, tono = CTX.get("sari"), CTX.get("tono")
    shift = CTX.get("shift_riu_kitchen") or open_shift(sari, RIU, "a2")
    s = pay(sari, shift, [{"item": KSA, "qty": 1}, {"item": KHT, "qty": 1}], "A2 Meja 21", buyer="Meja 21")
    t = tickets(api, s["name"])[0]
    to_status(tono, t["name"], "ready")
    st, r = set_lines(sari, t["name"], lambda l: l["item"] == KSA, {"status": "done"})
    newname = _amend_pay(sari, s["name"], "A2")
    rts = tickets(api, newname)
    eq("A2: revision after a partial handover: only the kopi hitam is made again", sorted((t["status"], tuple(lines(t))) for t in rts if t["status"] == "new"),
       [("new", ((KHT, 1, ""),))])


def scenario_kitchen_audit_a3_row_delete(api):
    sari, tono = CTX.get("sari"), CTX.get("tono")
    shift = CTX.get("shift_riu_kitchen") or open_shift(sari, RIU, "a3")
    s = pay(sari, shift, [{"item": KSA, "qty": 1}, {"item": KHT, "qty": 1, "kitchen_hold": True}], "A3 Meja 22", buyer="Meja 22")
    t = tickets(api, s["name"])[0]
    st, full = get_ticket(tono, t["name"])
    keep = [dict(l, status="ready") for l in full["items"] if not l.get("held")]
    st, r = tono.patch("kitchen_ticket", t["name"], {"items": keep})
    after = get_ticket(api, t["name"])[1]
    check("A3: the kitchen cannot drop a ticket line (the held kopi hitam) by leaving it out of a write",
          st != 200 and len(after.get("items") or []) == 2, (st, after.get("status"), _summ([after])))
    st, r = tono.patch("kitchen_ticket", t["name"], {"items": (after.get("items") or []) + [{"status": "new"}]})
    check("A3b: the kitchen cannot add a line", st != 200, (st, r))


def scenario_kitchen_audit_a4_held_partial_row(api):
    sari, tono = CTX.get("sari"), CTX.get("tono")
    shift = CTX.get("shift_riu_kitchen") or open_shift(sari, RIU, "a4")
    s = pay(sari, shift, [{"item": ROTI, "qty": 1, "kitchen_hold": True}], "A4 Meja 23", buyer="Meja 23")
    t = tickets(api, s["name"])[0]
    st, full = get_ticket(tono, t["name"])
    rows = [{"name": l["name"], "status": "in_progress"} for l in full["items"]]
    st, r = tono.patch("kitchen_ticket", t["name"], {"items": rows})
    after = get_ticket(api, t["name"])[1]
    check("A4: a write that leaves held out cannot move a held line (held_line)", st == 422, (st, _summ([after])))


def scenario_kitchen_audit_a5_merge_split_hold(api):
    sari = CTX.get("sari")
    shift = CTX.get("shift_riu_kitchen") or open_shift(sari, RIU, "a5")
    st, a = sari.create("pos_sale", {"shift": shift, "table": "MJA-RIU-001", "guests": 1, "items": [{"item": ROTI, "qty": 1, "line_key": "m1", "kitchen_hold": True}]})
    st2, b = sari.create("pos_sale", {"shift": shift, "table": "MJA-RIU-002", "guests": 1, "items": [{"item": KHT, "qty": 1, "line_key": "m2"}]})
    check("A5 setup", st == 201 and st2 == 201, (st, a, st2, b))
    st, r = sari.patch("pos_sale", rec(a)["name"], {"merge_into": rec(b)["name"]})
    m = sari.get("pos_sale", rec(b)["name"])
    eq("A5: merge keeps Sajikan nanti on the moved roti", [(l["item"], bool(l.get("kitchen_hold"))) for l in m.get("items") or []], [(KHT, False), (ROTI, True)])
    st, c = sari.create("pos_sale", {"shift": shift, "table": "MJA-RIU-010", "guests": 1, "items": [{"item": ROTI, "qty": 1, "line_key": "p1", "kitchen_hold": True}, {"item": KHT, "qty": 1, "line_key": "p2"}]})
    st, r = sari.patch("pos_sale", rec(c)["name"], {"split_lines": ["p1"]})
    kids = api.find("pos_sale", f"split_of == '{rec(c)['name']}'", children=1)
    eq("A5b: split keeps Sajikan nanti on the moved roti", [(l["item"], bool(l.get("kitchen_hold"))) for k in kids for l in k.get("items") or []], [(ROTI, True)])
    # the open bills leave their tables free for the scenarios after this one (self order)
    for n in [rec(a)["name"], rec(b)["name"], rec(c)["name"]] + [k["name"] for k in kids]:
        sari.call("DELETE", f"/api/v1/r/pos_sale/{sari.q(n)}")


def scenario_kitchen_audit_a6_void_notice_served(api):
    sari, tono = CTX.get("sari"), CTX.get("tono")
    shift = CTX.get("shift_riu_kitchen") or open_shift(sari, RIU, "a6")
    st, b = sari.create("pos_sale", {"shift": shift, "table": "MJA-RIU-001", "guests": 2, "items": [{"item": ROTI, "qty": 1, "line_key": "v1"}]})
    name = rec(b).get("name")
    sari.patch("pos_sale", name, {"send_kitchen": True})
    sari.patch("pos_sale", name, {"items": [{"item": KHT, "qty": 1, "line_key": "v0"}], "send_kitchen": True})
    ts = tickets(api, name)
    notice = [t for t in ts if any(l.get("kind") == "void" for l in t["items"])]
    set_lines(tono, notice[0]["name"], lambda l: True, {"seen": True})
    sari.patch("pos_sale", name, {"items": [{"item": KHT, "qty": 1, "line_key": "v0"}, {"item": ROTI, "qty": 1, "line_key": "v2"}], "send_kitchen": True})
    st, p = sari.verb("pos_sale", name, "submit", {"payments": [{"method": "cash", "amount": 100_000}]})
    paid = rec(p).get("name")
    newname = _amend_pay(sari, paid, "A6")
    rts = tickets(api, newname)
    eq("A6: revision after a Batal notice was read: the roti nobody served goes to Dapur as new",
       sorted((t["station"], tuple(lines(t))) for t in rts if t["status"] == "new" and t["station"] == DAPUR), [(DAPUR, ((ROTI, 1, ""),))])


SCENARIOS = SCENARIOS + [scenario_kitchen_audit_a1_change_notice_served, scenario_kitchen_audit_a2_partial_handover, scenario_kitchen_audit_a3_row_delete,
                         scenario_kitchen_audit_a4_held_partial_row, scenario_kitchen_audit_a5_merge_split_hold, scenario_kitchen_audit_a6_void_notice_served]


def scenario_kitchen_audit_a7_print_notice(api):
    sari, tono = CTX.get("sari"), CTX.get("tono")
    shift = CTX.get("shift_riu_kitchen") or open_shift(sari, RIU, "a7")
    st, b = sari.create("pos_sale", {"shift": shift, "table": "MJA-RIU-002", "guests": 2, "items": [{"item": ROTI, "qty": 1, "line_key": "w1"}, {"item": KHT, "qty": 2, "line_key": "w2", "kitchen_hold": True}]})
    name = rec(b).get("name")
    sari.patch("pos_sale", name, {"send_kitchen": True})
    sari.patch("pos_sale", name, {"items": [{"item": KHT, "qty": 1, "line_key": "w2", "kitchen_hold": True}], "send_kitchen": True})
    for t in tickets(api, name):
        st, raw, _ = tono.print_html("kitchen_ticket", t["name"], "kitchen_ticket")
        kinds = sorted({(l.get("kind") or "normal") + ("/held" if l.get("held") else "") for l in t["items"]})
        txt = page_text(raw)
        check(f"A7: the printed ticket of a {kinds} ticket says what it is (BATAL / DIUBAH / DITAHAN)",
              kinds == ["normal"] or any(w in txt.upper() for w in ("BATAL", "DIUBAH", "DITAHAN", "NANTI")), txt[:400])
    sari.call("DELETE", f"/api/v1/r/pos_sale/{sari.q(name)}")   # the table stays free for the scenarios after this one


SCENARIOS = SCENARIOS + [scenario_kitchen_audit_a7_print_notice]
