"""Day-one scenarios of the dapur piece (module kitchen) on demo-kasir: a paid receipt at the coffee kiosk becomes one
kitchen ticket per station, the kitchen works the tickets through their steps, a void and a correction reach the
kitchen, and the kitchen sees only its own outlet.

Loaded by tests/day_one.py after day_one_pos (bind() hands over its helpers): `python3 tests/day_one.py --site
demo-kasir`. Fixtures (examples/fixtures/demo-kasir): Kedai Riau has two stations, Bar (category Minuman kopi) and
Dapur (Makanan); Toko Dago has none until the scope scenario gives it one. Covered: tickets per station from the
receipt's item categories (no ticket for an unrouted category or a shop), status new -> cooking -> ready -> picked up
with a time per step, one step back clears the stamp, a jump or a void by hand refused, read-only receipt lines,
voided receipt voids its tickets, an amended receipt sends a revision, the 80 mm ticket print (no price), the kitchen
dashboard and the kitchen work screen as the kitchen's home (contract 0030), and role kitchen limited to its outlet.

Members: Tono (kitchen, Kedai Riau), Wati (outlet_staff, Kedai Riau); Dika, Sari and Rina come from day_one_pos.
"""

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


def scenario_kitchen_steps(api):
    """Tono (kitchen, Kedai Riau) cooks the Bar ticket: one step at a time, each stamped; a step back clears the stamp;
    a jump, a void by hand and a change of lines are refused; Sari marks it picked up."""
    tono, sari = CTX.get("tono"), CTX.get("sari")
    bar = CTX.get("kt_bar")
    if not (tono and sari and bar):
        return
    st, r = tono.patch("kitchen_ticket", bar, {"status": "ready"})
    check("Tono: new -> ready in one go is refused -> 422 one_step", st == 422 and "one_step" in rules(r), (st, r))
    st, r = tono.patch("kitchen_ticket", bar, {"status": "void"})
    check("Tono cannot void a ticket by hand -> 422 void_by_receipt", st == 422 and "void_by_receipt" in rules(r), (st, r))
    st, r = tono.patch("kitchen_ticket", bar, {"status": "cooking"})
    t = rec(r)
    check("Tono: Masak -> 200, cooking_at stamped", st == 200 and t.get("status") == "cooking" and bool(t.get("cooking_at")), (st, r))
    cooking_at = t.get("cooking_at")
    st, r = tono.patch("kitchen_ticket", bar, {"status": "ready"})
    t = rec(r)
    check("Tono: Siap -> 200, ready_at stamped, cooking_at kept", st == 200 and bool(t.get("ready_at")) and t.get("cooking_at") == cooking_at, (st, r))
    st, r = tono.patch("kitchen_ticket", bar, {"status": "cooking"})
    t = rec(r)
    check("Urungkan (ready -> cooking) -> 200, ready_at cleared, cooking_at kept",
          st == 200 and t.get("status") == "cooking" and not t.get("ready_at") and t.get("cooking_at") == cooking_at, (st, r))
    st, r = tono.patch("kitchen_ticket", bar, {"status": "ready", "station": DAPUR, "buyer": "Meja 9", "items": [{"item": AIR, "qty": 9}]})
    t = api.call("GET", f"/api/v1/r/kitchen_ticket/{bar}?children=1")[1] or {}
    check("receipt, station, table and lines stay as the receipt sent them (read-only)",
          t.get("station") == BAR and t.get("buyer") == "Meja 4" and lines(t) == sorted([(KSA, 2, "kurang manis"), (KHT, 1, "")]), (st, r, t))
    check("the status of that write went through -> ready", t.get("status") == "ready", t)
    st, r = sari.patch("kitchen_ticket", bar, {"status": "picked_up"})
    t = rec(r)
    check("Sari (cashier) marks it Diambil -> 200, picked_up_at stamped", st == 200 and t.get("status") == "picked_up" and bool(t.get("picked_up_at")), (st, r))
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
    st, r = tono.patch("kitchen_ticket", ts[1]["name"], {"status": "cooking"})
    check("Tono starts the roti", st == 200, (st, r))
    st, r = sari.verb("pos_sale", s1["name"], "cancel")
    if not check("Sari voids the Meja 7 receipt -> 200", st == 200, (st, r)):
        return
    ts = tickets(api, s1["name"])
    check("both tickets are void, void_at stamped", all(t["status"] == "void" and t.get("void_at") for t in ts), ts)
    st, r = tono.patch("kitchen_ticket", ts[0]["name"], {"status": "cooking"})
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
          [t["station"] for t in ts] == [BAR] and ts[0]["revision_of"] == s1["name"] and lines(ts[0]) == [(KHT, 1, "")], ts)
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
    st, r = tono.patch("kitchen_ticket", dgo_t, {"status": "cooking"})
    check("Tono cannot move Toko Dago's ticket -> 403/404", st in (403, 404), (st, r))
    for key, cl in (("Tono", tono), ("Wati", wati)):
        outs = {t["outlet"] for t in cl.find("kitchen_ticket", "true")}
        check(f"{key} lists Kedai Riau's tickets only", outs == {RIU}, outs)
    st, r = wati.patch("kitchen_ticket", CTX.get("kt_dapur"), {"status": "cooking"})
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
    st, r = wati.call("GET", "/api/v1/dashboards/dapur_hari_ini")
    check("Wati (outlet staff) is not offered Dapur hari ini -> 403", st == 403, (st, r))
    st, m = tono.call("GET", "/api/v1/meta")
    work = (m or {}).get("work") or []
    check("Tono's home is the kitchen screen (meta.work: dapur, home)", work[:1] == [{"screen": "dapur", "home": True}], work)
    sc = ((m or {}).get("screens") or {}).get("dapur") or {}
    check("the kitchen screen is a queue over kitchen_ticket", sc.get("type") == "queue" and sc.get("queue", {}).get("source", {}).get("model") == "kitchen_ticket", sc)
    st, r = tono.call("GET", "/api/v1/screens/dapur")
    check("Tono opens the kitchen screen -> 200", st == 200, (st, r))
    st, m = wati.call("GET", "/api/v1/meta")
    check("Wati is not offered the kitchen screen", "dapur" not in ((m or {}).get("screens") or {}), (m or {}).get("work"))
    st, r = wati.call("GET", "/api/v1/screens/dapur")
    check("Wati opening the kitchen screen -> 404", st == 404, (st, r))
    st, m = CTX["sari"].call("GET", "/api/v1/meta")
    check("Sari (cashier) gets the kitchen screen, not as home", {"screen": "dapur", "home": False} in ((m or {}).get("work") or []), (m or {}).get("work"))


SCENARIOS = [scenario_kitchen_tickets, scenario_kitchen_steps, scenario_kitchen_void_and_revision, scenario_kitchen_scope,
             scenario_kitchen_dashboard_and_screen]
