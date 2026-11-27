"""Day-one scenarios of warehouses apart from stores (rangka-apps#104): typed outlet rows, the store -> warehouse stock
request that the warehouse turns into a transfer, the warehouse role, the location rules on documents, the plan's
outlet seats counting stores only (rangka-os contract 0057 §5) and the reorder drafts that skip supplied stores.

Loaded by tests/day_one.py (bind() hands over its helpers). Sites and members:
- demo-kasir: OUT-01 is the central warehouse, the three stores name it as supply_from. Gani (warehouse_staff, OUT-01)
  and Wati (outlet_staff, Kedai Riau OUT-03, added by day_one_staff) are the people of the request.
- demo-grosir: GDG warehouse, KNV canvas van, RSK damaged-goods room.
- demo-produksi: BHN and JDI warehouses, PRD production room.
"""
import subprocess

H = {}


def bind(m):
    for k in ("ok", "fail", "check", "eq", "CTX", "Api", "denied", "dashboard", "num", "secrets"):
        H[k] = getattr(m, k)
    globals().update(H)


def rec(x):
    return (x or {}).get("record", x) or {}


def code(resp):
    return ((resp or {}).get("error") or {}).get("code")


def bal(api, outlet, item):
    rows = api.find("stock_balance", f"outlet == '{outlet}' && item == '{item}'")
    return (rows[0].get("qty") or 0) if rows else 0


def setup_kasir(api, k):
    """Gani, warehouse staff of the central warehouse (OUT-01)."""
    if CTX.get("gani"):
        return
    pw = secrets.token_urlsafe(18)   # throwaway password of a throwaway database
    email = "gani.gudang@contohwarung.test"
    rc, out = k.user_add(email, "warehouse_staff", CTX.get("org") or "warung_contoh", "Gani Contoh", outlets="OUT-01", password=pw)
    cl = Api.login(k.base, email, pw) if rc == 0 else None
    if cl is None:
        fail("warehouses: Gani (warehouse_staff) signed in", out if rc else "login refused")
        return
    CTX["gani"] = cl


def scenario_warehouse_types_kasir(api):
    """The typed fixture: OUT-01 is a warehouse, the stores are supplied by it, balances carry the location type."""
    outs = {o["name"]: o for o in api.list("outlet", limit=50)}
    eq("demo-kasir: OUT-01 is a warehouse", outs.get("OUT-01", {}).get("location_type"), "warehouse")
    check("demo-kasir: every store is supplied by OUT-01",
          all(outs.get(n, {}).get("supply_from") == "OUT-01" and outs[n].get("location_type") == "store" for n in ("OUT-02", "OUT-03", "OUT-04")),
          {n: (o.get("location_type"), o.get("supply_from")) for n, o in outs.items()})
    sp = (CTX.get("kernel").sold_plan or {}) if CTX.get("kernel") else {}
    used = ((sp.get("usage") or {}).get("outlets") or {}).get("used")
    if sp:
        eq("demo-kasir plan as sold: outlet seats count the 5 stores, not the warehouse (0057 §5)", used, 5)
    # outlet rules: a warehouse cannot be a supplying warehouse of itself, a store cannot supply a store, a warehouse has no sales source
    st, r = api.patch("outlet", "OUT-02", {"supply_from": "OUT-03"})
    check("a store as a supplying warehouse is refused (supply_is_warehouse) -> 422", st == 422 and "supply_is_warehouse" in str(r), (st, r))
    st, r = api.patch("outlet", "OUT-01", {"parent_location": "OUT-01"})
    check("a location as its own parent is refused (not_own_parent) -> 422", st == 422 and "not_own_parent" in str(r), (st, r))
    st, r = api.create("outlet", {"outlet_name": "Gudang uji Moka", "location_type": "warehouse", "moka_name": "Gudang - Moka"})
    check("a warehouse with a Moka name is refused (store_fields_only) -> 422", st == 422 and "store_fields_only" in str(r), (st, r))


def scenario_stock_request_kasir(api):
    """Request to receipt: Wati (Riau) asks the warehouse for 20 bottles, Gani counts the warehouse's stock, picks
    Proses, sends 15 of them, Wati receives; the request follows open -> sent -> received."""
    gani, wati, hq = CTX.get("gani"), CTX.get("wati"), CTX.get("hq")
    if not (gani and wati and hq):
        fail("warehouses: Gani, Wati and Rina signed in", CTX.get("login_error") or "missing")
        return
    item = "BRG-0001"
    # the warehouse's stock: Gani counts 60 bottles at OUT-01 (his count posts its GL lines as him)
    st, c = gani.create("stock_count", {"outlet": "OUT-01", "items": [{"item": item, "counted_qty": 60}]})
    if check("Gani counts the warehouse -> 201", st == 201, (st, c)):
        st, r = gani.verb("stock_count", rec(c)["name"], "submit")
        check("Gani submits the count -> 200", st == 200, (st, r))
    wh0, riu0 = bal(api, "OUT-01", item), bal(api, "OUT-03", item)
    # Wati asks; the warehouse comes from Riau's supply_from
    st, q = wati.create("stock_request", {"outlet": "OUT-03", "needed_by": "2030-01-01", "items": [{"item": item, "qty": 20}]})
    if not check("Wati asks the warehouse for 20 bottles -> 201", st == 201, (st, q)):
        return
    req = rec(q)["name"]
    q = api.get("stock_request", req)
    eq("the request goes to Riau's supplying warehouse", q.get("supply_outlet"), "OUT-01")
    eq("the request is open", q.get("state"), "open")
    eq("the line notes Riau's stock at the time", (q.get("items") or [{}])[0].get("on_hand"), riu0)
    # Wati cannot decide for the warehouse, nor ask in another store's name
    wati.patch("stock_request", req, {"decision": "accept"})
    eq("Wati's Proses is not taken (read_only_for outlet_staff)", api.get("stock_request", req).get("decision") or "", "")
    eq("... and no transfer is drafted", len(api.find("stock_transfer", f"stock_request == '{req}'")), 0)
    st, r = wati.create("stock_request", {"outlet": "OUT-02", "items": [{"item": item, "qty": 1}]})
    check("Wati cannot ask in Toko Dago's name -> 403", denied(st, r), (st, r))
    st, r = wati.create("stock_request", {"outlet": "OUT-03", "supply_outlet": "OUT-02", "items": [{"item": item, "qty": 1}]})
    check("a store as the warehouse of a request is refused (supply_is_warehouse) -> 422", st == 422 and "supply_is_warehouse" in str(r), (st, r))
    # Gani sees it on his home and picks Proses: a draft transfer from the warehouse to Riau
    w = dashboard(gani, "outlet_hari_ini")
    if w:
        check("Gani's home lists the request", req in {x.get("name") for x in (w.get("request_list") or {}).get("rows") or []}, w.get("request_list"))
    st, r = gani.patch("stock_request", req, {"decision": "accept"})
    check("Gani picks Proses -> 200", st == 200, (st, r))
    drafts = api.find("stock_transfer", f"stock_request == '{req}' && docstatus == 0")
    if not check("one draft transfer of the request", len(drafts) == 1, drafts):
        return
    gani.patch("stock_request", req, {"notes": "Disiapkan"})
    eq("saving the request again drafts nothing more", len(api.find("stock_transfer", f"stock_request == '{req}'")), 1)
    t = api.get("stock_transfer", drafts[0]["name"])
    check("the draft goes OUT-01 -> OUT-03 with the requested line",
          t.get("from_outlet") == "OUT-01" and t.get("to_outlet") == "OUT-03" and (t.get("items") or [{}])[0].get("qty") == 20, t)
    eq("the transfer's route reads warehouse>store", t.get("route"), "warehouse>store")
    # the warehouse sends 15, not 20
    items = t.get("items") or []
    items[0]["qty"] = 15
    st, r = gani.patch("stock_transfer", t["name"], {"items": items})
    check("Gani sends 15 instead of 20 -> 200", st == 200, (st, r))
    st, r = gani.verb("stock_transfer", t["name"], "submit")
    if not check("Gani submits the transfer -> 200", st == 200, (st, r)):
        return
    tname = rec(r).get("name") or t["name"]
    q = api.get("stock_request", req)
    check("the request is sent and names the transfer", q.get("state") == "sent" and q.get("stock_transfer") == tname, q)
    eq("the warehouse holds 15 fewer", bal(api, "OUT-01", item), wh0 - 15)
    st, r = gani.patch("stock_request", req, {"decision": "reject", "reject_reason": "Berubah pikiran"})
    check("a sent request cannot be turned to Tolak (decided_once) -> 422", st == 422 and "decided_once" in str(r), (st, r))
    gl = api.find("gl_entry", f"voucher_type == 'stock_transfer' && voucher_no == '{tname}'")
    check("the GL lines of Gani's transfer balance", abs(sum(float(g.get("amount") or 0) for g in gl)) < 0.01, gl[:4])
    # Wati receives
    st, rc = wati.create("stock_transfer_receipt", {"stock_transfer": tname, "outlet": "OUT-03", "items": [{"item": item, "qty": 15}]})
    if check("Wati drafts the receipt -> 201", st == 201, (st, rc)):
        st, r = wati.verb("stock_transfer_receipt", rec(rc)["name"], "submit")
        check("Wati receives -> 200", st == 200, (st, r))
    eq("the request is received", api.get("stock_request", req).get("state"), "received")
    eq("Riau holds 15 more", bal(api, "OUT-03", item), riu0 + 15)
    # reject: a second request, refused with a reason
    st, q2 = wati.create("stock_request", {"outlet": "OUT-03", "items": [{"item": item, "qty": 500}]})
    if check("Wati asks for 500 -> 201", st == 201, (st, q2)):
        n2 = rec(q2)["name"]
        st, r = gani.patch("stock_request", n2, {"decision": "reject"})
        check("Tolak without a reason is refused (reject_needs_reason) -> 422", st == 422 and "reject_needs_reason" in str(r), (st, r))
        st, r = gani.patch("stock_request", n2, {"decision": "reject", "reject_reason": "Stok gudang tidak cukup"})
        check("Gani rejects with a reason -> 200", st == 200, (st, r))
        eq("the request is rejected", api.get("stock_request", n2).get("state"), "rejected")
        eq("a rejected request drafts no transfer", len(api.find("stock_transfer", f"stock_request == '{n2}'")), 0)
    # Proses then Tolak: the draft stays behind but cannot be sent, and the request stays rejected
    st, q4 = wati.create("stock_request", {"outlet": "OUT-03", "items": [{"item": item, "qty": 3}]})
    if check("Wati asks for 3 -> 201", st == 201, (st, q4)):
        n4 = rec(q4)["name"]
        gani.patch("stock_request", n4, {"decision": "accept"})
        d4 = api.find("stock_transfer", f"stock_request == '{n4}' && docstatus == 0")
        st, r = gani.patch("stock_request", n4, {"decision": "reject", "reject_reason": "Barang rusak di gudang"})
        check("Gani turns Proses to Tolak before sending -> 200", st == 200, (st, r))
        if check("the earlier draft is there", len(d4) == 1, d4):
            st, r = gani.verb("stock_transfer", d4[0]["name"], "submit")
            check("the draft of a rejected request cannot be sent (request_still_accepted) -> 422",
                  st == 422 and "request_still_accepted" in str(r), (st, r))
        eq("the request stays rejected", api.get("stock_request", n4).get("state"), "rejected")
    # cancel path: a third request sent then cancelled is open again and waits for a new decision
    st, q3 = wati.create("stock_request", {"outlet": "OUT-03", "items": [{"item": item, "qty": 2}]})
    if check("Wati asks for 2 -> 201", st == 201, (st, q3)):
        n3 = rec(q3)["name"]
        gani.patch("stock_request", n3, {"decision": "accept"})
        d3 = api.find("stock_transfer", f"stock_request == '{n3}' && docstatus == 0")
        if check("the third request is drafted", len(d3) == 1, d3):
            st, r = gani.verb("stock_transfer", d3[0]["name"], "submit")
            if check("its transfer is sent -> 200", st == 200, (st, r)):
                st, r = gani.verb("stock_transfer", rec(r)["name"], "cancel")
                check("Gani cancels it before it is received -> 200", st == 200, (st, r))
                q = api.get("stock_request", n3)
                check("the request is open again, without a decision or transfer",
                      q.get("state") == "open" and not q.get("decision") and not q.get("stock_transfer"), q)


def scenario_warehouse_role_kasir(api):
    """Gani (warehouse_staff): costs stay hidden, he cannot book daily sales, and a warehouse takes no daily sales."""
    gani = CTX.get("gani")
    if not gani:
        return
    rows = gani.find("stock_balance", "outlet == 'OUT-01'")
    check("Gani reads the warehouse's balances without their value", bool(rows) and all("value" not in r and "avg_cost" not in r for r in rows), rows[:2])
    gl = gani.find("gl_entry", "voucher_type == 'stock_transfer'")
    check("Gani reads stock transfer GL lines without amounts",
          bool(gl) and all(not ({"amount", "debit", "credit", "bill_left"} & set(g)) for g in gl), gl[:2])
    its = gani.list("item", limit=20)
    check("Gani reads items without their cost", bool(its) and all(not ({"standard_cost", "consignment_cost"} & set(i)) for i in its), its[:1])
    vs = gani.list("vendor", limit=20)
    check("Gani reads vendors without terms or what is owed", all(not ({"payment_days", "outstanding"} & set(v)) for v in vs), vs[:1])
    st, r = gani.create("outlet_sales", {"outlet": "OUT-04", "source": "form", "items": []})
    check("Gani cannot book a daily sales form -> 403", st == 403, (st, r))
    st, r = api.create("outlet_sales", {"outlet": "OUT-01", "source": "form", "items": [{"item": "BRG-0001", "qty": 1, "rate": 4000}]})
    check("a daily sales form at the warehouse is refused (outlet_is_store) -> 422", st == 422 and "outlet_is_store" in str(r), (st, r))


def scenario_plan_seats_kasir(api):
    """Plan Mulai (1 outlet) set the way an operator does: a new warehouse is still taken, a new store is refused
    (plan_limit), and the warehouse stays writable while the stores past the limit are read-only. The test plan is
    put back after."""
    k = CTX.get("kernel")
    if not k:
        return

    def plan_set(name):
        r = subprocess.run([k.binary, "plan", "set", name, "--by", "day_one", "--note", "uji gudang"] + k.args(), capture_output=True, text=True)
        return r.returncode == 0, r.stdout + r.stderr

    done, out = plan_set("mulai")
    if not check("plan set mulai (1 outlet)", done, out):
        return
    try:
        st, r = api.create("outlet", {"outlet_name": "Gudang Beku Uji", "location_type": "warehouse"})
        check("on Mulai with 3 stores, a new warehouse is taken -> 201", st == 201, (st, r))
        st, r = api.create("outlet", {"outlet_name": "Toko Uji Antapani"})
        check("on Mulai, a new store is refused (plan_limit) -> 403", st == 403 and code(r) == "plan_limit", (st, r))
        st, rep = api.call("GET", "/api/v1/plan")
        ro = ((rep or {}).get("read_only") or {}).get("outlets") or []
        used = (((rep or {}).get("usage") or {}).get("outlets") or {}).get("used") or 0
        stores = [o["name"] for o in api.list("outlet", limit=100) if o.get("is_store")]
        check("read-only past the limit: the newest stores only, never a warehouse",
              not any(x.endswith("/OUT-01") or x.split("/")[-1] not in stores for x in ro) and used == len(stores) and len(ro) == used - 1,
              (ro, stores, (rep or {}).get("usage", {}).get("outlets")))
        st, r = api.patch("outlet", "OUT-01", {"address": "Jl. Contoh Gudang No. 1"})
        check("the warehouse stays writable on Mulai -> 200", st == 200, (st, r))
    finally:
        done, out = plan_set(k.TEST_PLAN)
        check("back on the test plan", done, out)


def scenario_reorder_skips_supplied_stores(api):
    """procurement/reorder_draft_po: a store with a supplying warehouse gets no supplier draft; the warehouse does."""
    st, _ = api.call("GET", "/api/v1/r/purchase_order?limit=1")
    if st != 200:
        ok("reorder: procurement not installed on this site, nothing to draft")
        return
    rows = api.find("stock_reorder", "open && drafted")
    outs = {o["name"]: o for o in api.list("outlet", limit=50)}
    check("no open reorder row of a supplied store is on a supplier draft",
          all(not (outs.get(r.get("outlet"), {}).get("supply_from")) for r in rows), rows[:5])


def scenario_warehouse_types_grosir(api):
    outs = {o["name"]: o for o in api.list("outlet", limit=50)}
    eq("demo-grosir: GDG warehouse, KNV canvas van, RSK quarantine",
       [outs.get(n, {}).get("location_type") for n in ("OUT-01", "OUT-02", "OUT-03")], ["warehouse", "vehicle", "quarantine"])
    loads = api.find("stock_transfer", "route == 'warehouse>vehicle'")
    alln = api.find("stock_transfer", "from_outlet == 'OUT-01' && to_outlet == 'OUT-02'")
    check("the canvas loads (GDG -> KNV) carry route warehouse>vehicle", {t["name"] for t in alln} <= {t["name"] for t in loads}, (alln, loads))
    # a delivery note cannot ship from the damaged-goods room
    cust = (api.list("customer", limit=1) or [{}])[0].get("name")
    item = (api.find("stock_balance", "outlet == 'OUT-01' && qty > 0") or [{}])[0].get("item")
    st, r = api.create("delivery_note", {"customer": cust, "outlet": "OUT-03", "items": [{"item": item, "qty": 1}]})
    check("a delivery note from the damaged-goods room is refused (ships_from_stock) -> 422", st == 422 and "ships_from_stock" in str(r), (st, r))
    bal_rows = api.find("stock_balance", "outlet == 'OUT-01'")
    check("balances of the warehouse carry location_type warehouse", bool(bal_rows) and all(b.get("location_type") == "warehouse" for b in bal_rows), bal_rows[:2])


def scenario_damaged_warning_grosir(api):
    """sales_return.damaged_to_quarantine is a warning: damaged goods into a non-quarantine location still save."""
    dn = (api.find("delivery_note", "docstatus == 1") or [{}])[0]
    if not dn.get("name"):
        ok("damaged_to_quarantine: no submitted delivery note on this run")
        return
    dn = api.get("delivery_note", dn["name"])
    line = (dn.get("items") or [{}])[0]
    st, r = api.create("sales_return", {"delivery_note": dn["name"], "customer": dn.get("customer"), "outlet": dn.get("outlet"),
                                        "damaged_outlet": "OUT-01", "items": [{"item": line.get("item"), "qty": 1, "condition": "damaged"}]})
    warns = [w.get("rule") or w.get("name") for w in ((r or {}).get("warnings") or [])]
    check("damaged goods into the warehouse: saved with the damaged_to_quarantine warning", st == 201 and "damaged_to_quarantine" in str(warns or r), (st, r))
    if st == 201:
        api.call("DELETE", f"/api/v1/r/sales_return/{rec(r)['name']}")


def scenario_warehouse_types_produksi(api):
    outs = {o["name"]: o for o in api.list("outlet", limit=50)}
    eq("demo-produksi: BHN warehouse, PRD production room, JDI warehouse",
       [outs.get(n, {}).get("location_type") for n in ("OUT-01", "OUT-02", "OUT-03")], ["warehouse", "production", "warehouse"])
    sp = (CTX.get("kernel").sold_plan or {}) if CTX.get("kernel") else {}
    if sp:
        eq("demo-produksi plan as sold: no store takes an outlet seat", ((sp.get("usage") or {}).get("outlets") or {}).get("used"), 0)



def scenario_pos_store_only_kasir(api):
    """Only a store runs the cashier: a warehouse takes no plan seat, so a shift or sale there would be a free seat."""
    st, r = api.create("pos_shift", {"outlet": "OUT-01", "opening_cash": 0})
    check("a cashier shift at the warehouse is refused (shift_at_store) -> 422", st == 422 and "shift_at_store" in str(r), (st, r))
    st, o = api.create("outlet", {"outlet_name": "Toko Uji Kasir", "code": "UJK", "city": "Bandung"})
    if not check("a test store -> 201", st == 201, (st, o)):
        return
    out = rec(o)["name"]
    st, sh = api.create("pos_shift", {"outlet": out, "opening_cash": 0})
    if not check("a cashier shift at a store opens -> 201", st == 201, (st, sh)):
        return
    shift = rec(sh)["name"]
    line = [{"item": "BRG-0001", "qty": 1}]
    st, r = api.create("pos_sale", {"shift": shift, "items": line})
    check("a sale at the store is drafted -> 201", st == 201, (st, r))
    st, r = api.patch("outlet", out, {"location_type": "warehouse", "sales_source": "", "moka_name": ""})
    if check("the store is retyped to a warehouse -> 200", st == 200, (st, r)):
        st, r = api.create("pos_sale", {"shift": shift, "items": line})
        check("a sale on its open shift is now refused (sale_at_store) -> 422", st == 422 and "sale_at_store" in str(r), (st, r))
        st, r = api.patch("outlet", out, {"location_type": "vehicle"})
        st, r = api.create("pos_shift", {"outlet": out, "opening_cash": 0})
        check("a vehicle cannot open a cashier shift either -> 422", st == 422 and "shift_at_store" in str(r), (st, r))
    api.patch("outlet", out, {"active": False})

SCENARIOS = {
    "demo-kasir": [scenario_warehouse_types_kasir, scenario_stock_request_kasir, scenario_warehouse_role_kasir, scenario_pos_store_only_kasir, scenario_plan_seats_kasir, scenario_reorder_skips_supplied_stores],
    "demo-grosir": [scenario_warehouse_types_grosir, scenario_damaged_warning_grosir],
    "demo-produksi": [scenario_warehouse_types_produksi],
}
