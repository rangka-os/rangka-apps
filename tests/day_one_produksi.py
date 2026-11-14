"""Day-one scenarios of demo-produksi (app produksi_pabrik): a small frozen-food factory.

Loaded by tests/day_one.py (bind() hands over its helpers): `python3 tests/day_one.py --site demo-produksi`. Fixtures are
synthetic (examples/fixtures/demo-produksi/generate.py). A batch moves stock through the declarative stock block of
contract 0021 (K-o): on a kernel without it the stock checks are gaps.
"""
import importlib.util
import os

BAHAN, PRODUKSI, JADI = "OUT-01", "OUT-02", "OUT-03"
KULIT, ISI, DIMSUM, NUGGET, REMAH = "BRG-0020", "BRG-0021", "BRG-0030", "BRG-0031", "BRG-0033"
H = {}


def bind(m):
    for k in ("ok", "fail", "gap", "check", "eq", "CTX", "Api", "denied", "dashboard", "secrets"):
        H[k] = getattr(m, k)
    globals().update(H)


def gen():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples", "fixtures", "demo-produksi", "generate.py")
    spec = importlib.util.spec_from_file_location("produksi_gen", path)
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    return g


def setup_demo_produksi(api, k):
    """Owner, production foreman (outlet staff of the production room); link items to their BOMs; opening stock."""
    st, me = api.call("GET", "/auth/me")
    org = ((me or {}).get("org") or {}).get("id") or "pangan_contoh"
    CTX.update(kernel=k, org=org)
    pw = secrets.token_urlsafe(18)   # throwaway password of a throwaway database
    for key, email, role, outlets, rec, name in [("hq", "sari@contohpangan.test", "head_office", "", "USR-001", "Sari Contoh"),
                                                 ("staff", "tono@contohpangan.test", "outlet_staff", PRODUKSI + "," + JADI, "USR-002", "Tono Contoh")]:
        code, out = k.user_add(email, role, org, name, outlets=outlets, record=rec, password=pw)
        cl = Api.login(k.base, email, pw) if code == 0 else None
        if cl is None:
            CTX["login_error"] = f"{email}: " + (out if code else "login refused")
            fail("setup: add and sign in the members", CTX["login_error"])
            return
        CTX[key] = cl
    g = gen()
    for item, bom in g.DEFAULT_BOM.items():
        st, resp = api.patch("item", item, {"default_bom": bom})
        if st != 200:
            fail(f"setup: link {item} to {bom}", (st, resp))
    for b in ("BOM-0003", "BOM-0004", "BOM-0005"):      # upper levels after the lower ones, so the cost rolls up
        api.patch("bom", b, {"notes": "Resep standar"})
    cost = {i["name"]: i["standard_cost"] for i in g.build()["item"]}
    for outlet, qty in g.opening_stock().items():
        st, gr = api.create("goods_receipt", {"vendor": g.SALDO, "outlet": outlet, "received_on": "2026-11-01",
                                              "items": [{"item": i, "qty": q, "rate": cost[i]} for i, q in qty.items()]})
        st, resp = api.verb("goods_receipt", (gr or {}).get("name", "x"), "submit")
        if st != 200:
            fail(f"setup: opening stock {outlet}", (st, resp))


def expected_unit_cost(boms, items, name, memo):
    """Independent roll-up of a BOM's standard unit cost from the fixtures (same rounding as the model)."""
    if name in memo:
        return memo[name]
    b = boms[name]
    default = {x["item"]: n for n, x in boms.items()}
    mat = 0
    for line in b["lines"]:
        unit = expected_unit_cost(boms, items, default[line["item"]], memo) if line["item"] in default else items[line["item"]]["standard_cost"]
        mat += round(line["qty"] * unit)
    share = sum(x["cost_share"] for x in b.get("byproducts", []))
    memo[name] = round(mat * (100 - share) / 100 / b["output_qty"]) + b["labour_per_unit"] + b["overhead_per_unit"]
    return memo[name]


def balance(api, outlet, item):
    rows = api.find("stock_balance", f"outlet == '{outlet}' && item == '{item}'")
    return rows[0] if rows else {"qty": 0, "value": 0}


def run_batch(api, cl, item, qty, labour=(), materials=None, outputs=None, out_outlet=JADI, date="2026-11-05", label=""):
    """SPK + batch for qty of item; returns (work order, submitted batch or None, submit answer)."""
    st, wo = api.create("work_order", {"item": item, "qty": qty, "outlet": PRODUKSI, "start_date": date})
    if not check(f"{label}: SPK draft -> 201", st == 201, (st, wo)):
        return None, None, None
    st, wo = api.verb("work_order", wo["name"], "submit")
    if not check(f"{label}: SPK released -> 200", st == 200, (st, wo)):
        return None, None, None
    body = {"work_order": wo["name"], "output_outlet": out_outlet, "production_date": date,
            "labour": [{"worker": w, "stage": s, "qty": q} for w, s, q in labour]}
    if materials is not None:
        body["materials"] = materials
    if outputs is not None:
        body["outputs"] = outputs
    st, pb = cl.create("production_batch", body)
    if not check(f"{label}: batch draft -> 201", st == 201, (st, pb)):
        return wo, None, None
    st, resp = cl.verb("production_batch", pb["name"], "submit")
    return wo, (resp if st == 200 else pb), (st, resp)


def stock_gap(ans):
    return ans and ans[0] != 200 and "no rule for production_batch" in str(ans[1])


# ---------- scenarios

def scenario_produksi_fixtures(api):
    eq("five BOMs, five stages, eight workers", (len(api.list("bom")), len(api.list("production_stage")), len(api.list("worker"))), (5, 5, 8))
    eq("dimsum is made with BOM-0003", api.get("item", DIMSUM).get("default_bom"), "BOM-0003")


def scenario_produksi_bom_cost(api):
    g = gen()
    data = g.build()
    boms = {b["name"]: b for b in data["bom"]}
    items = {i["name"]: i for i in data["item"]}
    memo = {}
    for name in boms:
        eq(f"{name} standard unit cost rolls up the levels", api.get("bom", name).get("unit_cost"), expected_unit_cost(boms, items, name, memo))
    b = api.get("bom", "BOM-0003")
    check("dimsum BOM marks dough and filling as semi-finished", [l["semi_finished"] for l in b["lines"]] == [True, True, False], b["lines"])
    st, resp = api.create("bom", {"bom_name": "Salah", "item": DIMSUM, "output_qty": 10, "lines": [{"item": DIMSUM, "qty": 1}]})
    check("a BOM using its own product is refused -> 422", st == 422, (st, resp))


def scenario_produksi_work_order(api):
    st, wo = api.create("work_order", {"item": DIMSUM, "qty": 200, "outlet": PRODUKSI, "due_date": "2026-11-20"})
    if not check("SPK for 200 pak dimsum -> 201", st == 201, (st, wo)):
        return
    wo = api.get("work_order", wo["name"])
    eq("SPK takes the product's BOM", wo["bom"], "BOM-0003")
    eq("material need = BOM x 2", [(m["item"], m["qty"]) for m in wo["materials"]], [(KULIT, 24), (ISI, 60), ("BRG-0008", 200)])
    check("dough and filling flagged as their own SPK", [m["semi_finished"] for m in wo["materials"]] == [True, True, False], wo["materials"])
    st, resp = api.patch("work_order", wo["name"], {"qty": 150})
    wo = api.get("work_order", wo["name"])
    eq("qty changed: material need refilled", [m["qty"] for m in wo["materials"]], [18, 45, 150])
    rows = wo["materials"]
    rows[2]["qty"] = 155
    api.patch("work_order", wo["name"], {"materials": rows, "size_notes": "Kemasan cadangan 5"})
    eq("a line edited by hand stays", [m["qty"] for m in api.get("work_order", wo["name"])["materials"]], [18, 45, 155])
    st, resp = api.verb("work_order", wo["name"], "submit")
    check("SPK released -> 200", st == 200, (st, resp))
    staff = CTX.get("staff")
    if staff:
        st, resp = staff.create("work_order", {"item": DIMSUM, "qty": 10, "outlet": PRODUKSI})
        check("production staff cannot issue an SPK -> 403", denied(st, resp), (st, resp))


def scenario_produksi_batch(api):
    """Two levels: dough and filling batches land in the production room, the dimsum batch consumes them at their
    actual cost; HPP = materials (stock value taken) + piece-rate wages + overhead; expiry from the shelf life."""
    hq = CTX.get("hq")
    if hq is None:
        fail("produksi: members signed in", CTX.get("login_error"))
        return
    _, kulit, ans = run_batch(api, hq, KULIT, 12, out_outlet=PRODUKSI, date="2026-11-04", label="dough")
    if stock_gap(ans):
        gap("batch moves stock", "K-o (contract 0021)", "stock.post_moves has no rule for production_batch on this kernel")
        return
    check("dough batch submitted -> 200", ans and ans[0] == 200, ans)
    _, isi, ans = run_batch(api, hq, ISI, 30, out_outlet=PRODUKSI, date="2026-11-04", label="filling")
    check("filling batch submitted -> 200", ans and ans[0] == 200, ans)
    if not (kulit and isi and kulit.get("docstatus") == 1 and isi.get("docstatus") == 1):
        return
    check("semi-finished batch: stock value in = material cost to the rupiah",
          balance(api, PRODUKSI, KULIT)["value"] == kulit["material_cost"], (balance(api, PRODUKSI, KULIT), kulit["material_cost"]))
    before = {i: balance(api, PRODUKSI, i) for i in (KULIT, ISI, "BRG-0008")}
    labour = [("WRK-0001", "TAHAP-002", 50), ("WRK-0002", "TAHAP-002", 50), ("WRK-0003", "TAHAP-005", 100)]
    wo, pb, ans = run_batch(api, hq, DIMSUM, 100, labour=labour, date="2026-11-05", label="dimsum")
    if not check("dimsum batch submitted -> 200", ans and ans[0] == 200, ans):
        return
    CTX["dimsum_batch"] = pb["name"]
    eq("batch drafted from the BOM: materials", [(m["item"], m["qty"]) for m in pb["materials"]], [(KULIT, 12), (ISI, 30), ("BRG-0008", 100)])
    taken = sum(round(before[i]["value"] * q / before[i]["qty"]) for i, q in ((KULIT, 12), (ISI, 30), ("BRG-0008", 100)))
    check("material cost = what the kernel took out (moving average)", abs(pb["material_cost"] - taken) <= 2, (pb["material_cost"], taken))
    eq("material cost = sum of the lines' cogs", pb["material_cost"], sum(m["cogs"] for m in pb["materials"]))
    eq("finished goods in the cold store at the material value", balance(api, JADI, DIMSUM)["value"], pb["material_cost"])
    eq("wages = 50 x 900 + 50 x 900 + 100 x 200", pb["labour_cost"], 110_000)
    eq("overhead = 100 x 800", pb["overhead_cost"], 80_000)
    eq("HPP = materials + wages + overhead", pb["hpp_total"], pb["material_cost"] + 110_000 + 80_000)
    eq("HPP per pack", pb["hpp_per_unit"], round(pb["hpp_total"] / 100))
    eq("expiry = production date + 180 days", pb["expiry_date"], "2027-05-04")
    eq("yield 100%", pb["yield_percent"], 100)
    sheets = api.find("wage_sheet", f"production_batch == '{pb['name']}'")
    check("a wage sheet was made for the batch", len(sheets) == 1 and sheets[0]["total"] == 110_000, sheets)
    # by-product: nugget with crumbs at 2% of the material cost
    _, nug, ans = run_batch(api, hq, NUGGET, 100, date="2026-11-05", label="nugget")
    if check("nugget batch with a by-product submitted -> 200", ans and ans[0] == 200, ans):
        eq("outputs: product 98%, crumbs 2%", [(o["item"], o["cost_share"]) for o in nug["outputs"]], [(NUGGET, 98), (REMAH, 2)])
        vin = balance(api, JADI, NUGGET)["value"] + balance(api, JADI, REMAH)["value"]
        eq("product + by-product stock value = material cost (to the rupiah)", vin, nug["material_cost"])
        check("by-product share is about 2%", abs(balance(api, JADI, REMAH)["value"] - nug["material_cost"] * 0.02) <= 1, (balance(api, JADI, REMAH), nug["material_cost"]))
        st, adj = hq.create("stock_adjustment", {"outlet": JADI, "reason": "other", "items": [{"item": REMAH, "qty": 3}]})
        st, resp = hq.verb("stock_adjustment", (adj or {}).get("name", "x"), "submit")
        st, resp = hq.verb("production_batch", nug["name"], "cancel")
        check("a batch whose by-product was used cannot be cancelled -> 422", st == 422, (st, resp))
        st, wo2 = api.create("work_order", {"item": NUGGET, "qty": 50, "outlet": PRODUKSI})
        st, wo2 = api.verb("work_order", (wo2 or {}).get("name", "x"), "submit")
        staff = CTX.get("staff")
        if staff and st == 200:
            g = gen(); bom = {b["name"]: b for b in g.build()["bom"]}["BOM-0004"]
            mats = [{"item": l["item"], "qty": round(l["qty"] / 2, 3)} for l in bom["lines"]]
            for label, outs in (("cost shares turned around", [{"item": NUGGET, "qty": 50, "cost_share": 1}, {"item": REMAH, "qty": 1.5, "cost_share": 99}]),
                                ("by-product qty far above the recipe", [{"item": NUGGET, "qty": 50, "cost_share": 98}, {"item": REMAH, "qty": 15000, "cost_share": 2}])):
                st, d = staff.create("production_batch", {"work_order": wo2["name"], "output_outlet": JADI, "materials": mats, "outputs": outs})
                st, resp = staff.verb("production_batch", (d or {}).get("name", "x"), "submit")
                pol = [a.get("policy") for a in (resp or {}).get("approvals", [])] if isinstance(resp, dict) else []
                check(f"{label}: waits for head office -> 202 (output_off_bom)", st == 202 and "output_off_bom" in pol, (st, pol))
                if d and d.get("name"):
                    if label.startswith("cost"):
                        st, resp = staff.patch("production_batch", d["name"], {"work_order": wo["name"]})
                        check("moving a draft batch to another SPK is refused -> 422", st == 422, (st, resp))
                    api.call("DELETE", f"/api/v1/r/production_batch/{Api.q(d['name'])}")
    # the dough batch's output was used by the dimsum batch: it cannot be cancelled
    st, resp = hq.verb("production_batch", kulit["name"], "cancel")
    check("a semi-finished batch whose output was used cannot be cancelled -> 422", st == 422, (st, resp))
    # cancel: stock back, wages cancelled
    st, resp = hq.verb("production_batch", pb["name"], "cancel")
    check("cancel the dimsum batch -> 200", st == 200, (st, resp))
    eq("cancel: finished goods out again", balance(api, JADI, DIMSUM)["qty"], 0)
    eq("cancel: dough back in the production room", round(balance(api, PRODUKSI, KULIT)["qty"], 3), round(before[KULIT]["qty"], 3))
    ws = api.get("wage_sheet", sheets[0]["name"]) if sheets else {}
    check("cancel: wage sheet and its lines cancelled", ws.get("cancelled") is True and all(l["cancelled"] for l in ws.get("lines", [])), ws)


def scenario_produksi_staff(api):
    staff = CTX.get("staff")
    if staff is None:
        fail("produksi: members signed in", CTX.get("login_error"))
        return
    st, wo = api.create("work_order", {"item": "BRG-0032", "qty": 50, "outlet": PRODUKSI})
    st, wo = api.verb("work_order", wo["name"], "submit")
    # the bakso recipe (BOM-0005, per 100) scaled to 48 good packs
    mats = [{"item": i, "qty": round(q * 48 / 100, 3)} for i, q in (("BRG-0010", 40), ("BRG-0004", 8), ("BRG-0005", 0.6), ("BRG-0006", 0.4), ("BRG-0011", 6), ("BRG-0008", 100))]
    st, pb = staff.create("production_batch", {"work_order": wo["name"], "output_outlet": JADI, "materials": mats,
                                               "outputs": [{"item": "BRG-0032", "qty": 48, "cost_share": 100}], "reject_qty": 2,
                                               "labour": [{"worker": "WRK-0004", "stage": "TAHAP-004", "qty": 48}]})
    if not check("production staff records a batch in the production room -> 201", st == 201, (st, pb)):
        return
    eq("yield 48 of 50 = 96%", pb["yield_percent"], 96)
    # the batch's cost is the owner's: production staff record quantities, never see the HPP (card t_09a89bcd)
    mine = staff.get("production_batch", pb["name"])
    hpp = sorted(k for k in ("material_cost", "overhead_cost", "hpp_total", "hpp_per_unit", "standard_unit_cost") if k in mine)
    hpp += sorted({"materials.cogs" for m in mine.get("materials", []) if "cogs" in m})
    check("production staff read their batch without its HPP", not hpp, hpp)
    boms = staff.list("bom", limit=20)
    bom = staff.get("bom", boms[0]["name"]) if boms else {}
    cost = sorted(k for k in ("overhead_per_unit", "material_cost", "unit_cost") if k in bom) + sorted({"lines." + k for l in bom.get("lines", []) for k in ("unit_cost", "cost") if k in l})
    check("production staff read the recipe without its costs (card t_a8bbc2bf)", bom and not cost, cost)
    check("production staff do not see the batch's overhead rate", "overhead_rate" not in mine, sorted(mine))
    st, resp = staff.call("GET", "/api/v1/aggregate/production_batch?fn=sum:hpp_total")
    check("production staff cannot sum batch HPP -> 403", st == 403, (st, resp))
    st, resp = staff.patch("production_batch", pb["name"], {"labour": [{"worker": "WRK-0004", "stage": "TAHAP-004", "qty": 48, "rate": 50_000}]})
    eq("a typed piece rate is ignored: the stage's rate counts", api.get("production_batch", pb["name"])["labour_cost"], 48 * 600)
    st, resp = staff.create("production_batch", {"work_order": wo["name"], "output_outlet": BAHAN, "materials": mats,
                                                 "outputs": [{"item": "BRG-0032", "qty": 48, "cost_share": 100}]})
    check("staff cannot put the output into an outlet that is not theirs -> 422", st == 422, (st, resp))
    st, resp = staff.create("production_batch", {"work_order": wo["name"], "output_outlet": JADI, "materials": mats,
                                                 "outputs": [{"item": "BRG-0032", "qty": 1, "cost_share": 1}, {"item": DIMSUM, "qty": 1000, "cost_share": 99}]})
    check("an output that is not the SPK's product or a by-product of its BOM is refused -> 422", st == 422, (st, resp))
    st, big = staff.create("production_batch", {"work_order": wo["name"], "output_outlet": JADI, "materials": mats,
                                                "outputs": [{"item": "BRG-0032", "qty": 500, "cost_share": 100}]})
    st, resp = staff.verb("production_batch", (big or {}).get("name", "x"), "submit")
    pol = [a.get("policy") for a in (resp or {}).get("approvals", [])] if isinstance(resp, dict) else []
    check("output far above plan waits for head office -> 202", st == 202 and "over_planned_output" in pol, (st, pol))
    odd = mats + [{"item": "BRG-0001", "qty": 1}]
    st, other = staff.create("production_batch", {"work_order": wo["name"], "output_outlet": JADI, "materials": odd,
                                                  "outputs": [{"item": "BRG-0032", "qty": 48, "cost_share": 100}]})
    st, resp = staff.verb("production_batch", (other or {}).get("name", "x"), "submit")
    pol = [a.get("policy") for a in (resp or {}).get("approvals", [])] if isinstance(resp, dict) else []
    check("a material outside the BOM waits for head office -> 202", st == 202 and "material_outside_bom" in pol, (st, pol))
    for d in (big, other):
        if d and d.get("name"):
            api.call("DELETE", f"/api/v1/r/production_batch/{Api.q(d['name'])}")
    st, resp = staff.patch("production_batch", pb["name"], {"planned_qty": 100_000})
    eq("staff cannot change the planned qty (it is the SPK's)", api.get("production_batch", pb["name"])["planned_qty"], 50)
    for label, body, policy in (
            ("wages far above the BOM standard", {"labour": [{"worker": "WRK-0004", "stage": "TAHAP-004", "qty": 5000}]}, "labour_above_standard"),
            ("materials far below the recipe", {"materials": [{"item": "BRG-0008", "qty": 1}]}, "material_off_bom")):
        st, d = staff.create("production_batch", dict({"work_order": wo["name"], "output_outlet": JADI, "materials": mats,
                                                       "outputs": [{"item": "BRG-0032", "qty": 48, "cost_share": 100}]}, **body))
        st, resp = staff.verb("production_batch", (d or {}).get("name", "x"), "submit")
        pol = [a.get("policy") for a in (resp or {}).get("approvals", [])] if isinstance(resp, dict) else []
        check(f"{label} wait for head office -> 202 ({policy})", st == 202 and policy in pol, (st, pol))
        if d and d.get("name"):
            api.call("DELETE", f"/api/v1/r/production_batch/{Api.q(d['name'])}")
    st, resp = staff.create("wage_sheet", {"work_date": "2026-11-05"})
    check("a wage sheet cannot be typed by hand -> 403/422", st in (403, 422), (st, resp))
    st, resp = staff.create("production_batch", {"work_order": wo["name"], "outlet": BAHAN, "output_outlet": JADI, "materials": mats,
                                                 "outputs": [{"item": "BRG-0032", "qty": 1, "cost_share": 100}]})
    check("staff cannot take materials out of another outlet -> 403/404/422", st in (403, 404, 422), (st, resp))
    st, resp = staff.patch("bom", "BOM-0005", {"overhead_per_unit": 0})
    check("staff cannot change a BOM -> 403", denied(st, resp), (st, resp))
    st, resp = staff.create("production_batch", {"work_order": wo["name"], "output_outlet": JADI, "materials": mats,
                                                 "outputs": [{"item": "BRG-0032", "qty": 48, "cost_share": 90}]})
    check("output shares not adding up to 100% are refused -> 422", st == 422, (st, resp))
    st, resp = staff.verb("production_batch", pb["name"], "submit")
    if stock_gap((st, resp)):
        gap("staff batch moves stock", "K-o (contract 0021)", "no declared stock on this kernel")
        return
    check("production staff submits the batch -> 200", st == 200, (st, resp))


def scenario_dashboards_produksi(api):
    ws = dashboard(api, "produksi_overview")
    for w in ("batches", "yield_avg", "hpp_total", "reject_batches", "recent_batches", "perlu_kedaluwarsa", "open_work_orders"):
        check(f"produksi_overview has {w}", w in ws, sorted(ws))
    # G10: tidak ada lagi angka yang menjumlah satuan berbeda; grafik hasil hanya produk jadi (bukan isi/adonan setengah jadi)
    check("produksi_overview has no mixed-unit good_qty total", "good_qty" not in ws, sorted(ws))
    rows = ws.get("recent_batches", {}).get("rows", [])
    check("recent batches carry the unit of their quantity (pak and kg never read as one number)",
          rows and all(r.get("uom") for r in rows) and {r["uom"] for r in rows} == {api.get("item", r["item"]).get("stock_uom") for r in rows},
          [(r.get("item"), r.get("good_qty"), r.get("uom")) for r in rows])
    out = {d.get("key") for d in ws.get("output_by_product", {}).get("data", [])}
    check("output chart shows finished products only", not out & {"BRG-0020", "BRG-0021"}, out)
    ws = dashboard(api, "produksi_upah", period=30)
    w = ws.get("wages_by_worker", {})
    keys = {d.get("key") for d in w.get("data", [])}
    check("weekly wages per worker show the piece-rate workers", "WRK-0004" in keys or not keys and CTX.get("dimsum_batch") is None, w.get("data"))
    check("a cancelled batch's wages are not counted", "WRK-0001" not in keys, w.get("data"))


SCENARIOS = [scenario_produksi_fixtures, scenario_produksi_bom_cost, scenario_produksi_work_order, scenario_produksi_batch,
             scenario_produksi_staff, scenario_dashboards_produksi]
