"""Day-one scenarios of module fixed_assets (rangka-apps#113): fixed assets, monthly straight-line depreciation, disposals.

Loaded by tests/day_one.py (bind() hands over its helpers): `python3 tests/day_one.py --site demo-sewa --only fixed`.
Every amount is checked against the hand-written oracle tests/fixed_assets_oracle.py, not against the .rk files.

demo-sewa (not PKP, pieces sewa_alat + pembukuan): seven assets on a fixed timeline from October 2023 to October 2026,
one depreciation run per month (37 runs, the last one drafted by the scheduled flow when its month is last month):
a rental camera (the issue's case: 24 months, nothing in the month it was bought, Habis disusutkan after 24 runs), a
Rp 10.000.000 asset over 36 months (rounding: the last month takes the remainder), an old asset brought in mid-life,
an asset whose first run catches up five months, a camera sold at month 12 with a gain, an asset written off with a
loss, and one cancelled before any run. Guards: threshold, closed period, disposal before the last run, cancelling a
run that is not the latest, a run for a month already done, a stale draft, cancelling a depreciated asset, one asset
per rental unit. Then the register print, the opening asset import and the balance sheet.
demo-kepingan-keuangan (PKP) and demo-jasa's second PT (not PKP): a vendor bill posted as "Aset tetap" drafts one asset
per line (cost before PPN when PKP, with its share of PPN when not), and the bill cannot be cancelled once an asset
from it is submitted.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixed_assets_oracle as O  # noqa: E402

H = {}
_DOC = {"read", "create", "write", "delete", "submit", "cancel", "amend", "export", "print"}
MATRIX = {
    "fixed_asset": {"finance_manager": _DOC | {"import"}, "head_office": _DOC | {"import"}, "purchasing_manager": {"read", "create"}, "rental_staff": {"read"}},
    "asset_book": {"finance_manager": {"read", "create", "write", "export", "print"}, "head_office": {"read", "create", "write", "export", "print"}, "rental_staff": {"read"}},
    "depreciation_run": {"finance_manager": _DOC - {"amend"}, "head_office": _DOC - {"amend"}},
    "asset_disposal": {"finance_manager": _DOC, "head_office": _DOC},
    "asset_category": {"finance_manager": {"read", "create", "write", "delete", "export"}, "head_office": {"read", "create", "write", "delete", "export"},
                       "purchasing_manager": {"read"}, "rental_staff": {"read"}},
    "fixed_asset_setting": {"finance_manager": {"read", "create", "write"}, "head_office": {"read", "create", "write"}, "purchasing_manager": {"read"}},
    "asset_guard": {"finance_manager": {"create"}, "head_office": {"create"}, "purchasing_manager": {"create"}},   # never stored
}


def bind(m):
    for k in ("ok", "fail", "gap", "check", "eq", "CTX", "Api", "ROOT", "denied"):
        H[k] = getattr(m, k)
    globals().update(H)
    m.MATRIX.update(MATRIX)


def rec(x):
    return (x or {}).get("record", x) or {}


def rules(resp):
    return [e.get("rule") for e in (resp or {}).get("errors", [])] if isinstance(resp, dict) else []


def submit(cl, model, name):
    st, resp = cl.verb(model, name, "submit")
    return st, rec(resp) if st == 200 else resp


def cancel(cl, model, name):
    st, resp = cl.verb(model, name, "cancel")
    return st, rec(resp) if st == 200 else resp


def gl(cl, model, name, entry="post"):
    out = {}
    for r in cl.find("gl_entry", f"voucher_type == '{model}' && voucher == '{name}' && entry == '{entry}'"):
        out[r["account"]] = out.get(r["account"], 0) + r["amount"]
    return out


def book(cl, asset):
    rows = cl.find("asset_book", f"asset == '{asset}'")
    return rows[0] if rows else {}


def iso(d):
    return d.isoformat()


def run_flow(cl, name):
    return cl.call("POST", f"/api/v1/flows/{name}/run", {})


def setup(cl):
    """Settings and default categories (flow fixed_assets_setup, run by hand as the owner would from the flow list)."""
    st, out = run_flow(cl, "fixed_assets_setup")
    check("fixed assets: setup flow runs -> 200", st == 200, (st, out))
    cats = {c["category_name"]: c for c in cl.find("asset_category", "true")}
    cfg = cl.find("fixed_asset_setting", "true")
    return cats, cfg


def new_asset(cl, label, **body):
    st, a = cl.create("fixed_asset", body)
    if not check(f"{label}: draft -> 201", st == 201, (st, a)):
        return {}
    return rec(a)


def add_asset(cl, label, **body):
    a = new_asset(cl, label, **body)
    if not a:
        return {}
    st, r = submit(cl, "fixed_asset", a["name"])
    check(f"{label}: submitted -> 200", st == 200 and r.get("docstatus") == 1, (st, r))
    return r if st == 200 else {}


# ---------- demo-sewa

S = {}   # the assets of the timeline: key -> {"doc": record, "o": oracle dict}


def scenario_fixed_assets_setup(api):
    """Settings (Rp 1.000.000 threshold) and the six default categories; the flow never makes them twice."""
    cats, cfg = setup(api)
    eq("fixed assets: one setting, threshold Rp 1.000.000", [c.get("capitalisation_threshold") for c in cfg], [1_000_000])
    eq("fixed assets: six default categories", sorted(cats), sorted(["Kendaraan", "Peralatan kantor", "Komputer dan HP", "Mesin", "Alat sewa", "Bangunan"]))
    eq("fixed assets: Alat sewa 48 months, kelompok 1", (cats.get("Alat sewa", {}).get("useful_life_months"), cats.get("Alat sewa", {}).get("fiscal_group")), (48, "kelompok_1"))
    run_flow(api, "fixed_assets_setup")
    eq("fixed assets: a second setup run changes nothing", (len(api.find("asset_category", "true")), len(api.find("fixed_asset_setting", "true"))), (6, 1))
    S["cats"] = cats


def _asset(key, cl, label, o, **body):
    a = add_asset(cl, label, **body)
    if a:
        S[key] = {"doc": a, "o": o}
    return a


def scenario_fixed_assets_register(api):
    """Seven assets: purchase journals by source, the register row, the rental unit link and the threshold."""
    fm = CTX.get("fm") or api
    cat = S.get("cats", {}).get("Alat sewa", {}).get("name")
    units = api.find("rental_unit", "true")
    unit = units[0]["name"] if units else ""
    D = datetime.date
    # the issue's camera: bought 1 Oct 2023 (the "January" of the issue), depreciated from November, 24 months
    a = _asset("cam", fm, "camera Rp 24.000.000, 24 months, rental unit", {"cost": 24_000_000, "residual": 0, "life": 24, "start": D(2023, 11, 1)},
               asset_name="Kamera Contoh A", category=cat, acquisition_date="2023-10-01", cost=24_000_000, useful_life_months=24,
               residual_value=0, source="other", paid_from="bank", rental_unit=unit, serial_no="SN-CAM-A")
    if a:
        eq("camera: depreciation starts the month after it was bought", a.get("depreciation_start"), "2023-11-01")
        eq("camera: Rp 1.000.000 a month", a.get("monthly_depreciation"), 1_000_000)
        eq("camera: purchase journal Aset tetap against Bank", gl(api, "fixed_asset", a["name"]), {"12100": 24_000_000, "11200": -24_000_000})
        b = book(api, a["name"])
        eq("camera: register row open, book value = cost", (b.get("state"), b.get("book_value"), b.get("accumulated")), ("active", 24_000_000, 0))
        if unit:
            eq("camera: the rental unit points at its asset", (api.get("rental_unit", unit) or {}).get("asset"), a["name"])
            x = new_asset(fm, "a second asset on the same rental unit", asset_name="Kamera kembar", category=cat, acquisition_date="2023-10-01",
                          cost=5_000_000, useful_life_months=24, source="other", rental_unit=unit)
            if x:
                st, r = submit(fm, "fixed_asset", x["name"])
                check("one asset per rental unit: refused 422 (unit_taken)", st == 422 and "unit_taken" in rules(r), (st, r))
                fm.call("DELETE", f"/api/v1/r/fixed_asset/{Api.q(x['name'])}")
    _asset("round", fm, "Rp 10.000.000 over 36 months", {"cost": 10_000_000, "residual": 0, "life": 36, "start": D(2023, 11, 1)},
           asset_name="Proyektor Contoh", category=cat, acquisition_date="2023-10-20", cost=10_000_000, useful_life_months=36, residual_value=0,
           source="other", paid_from="cash")
    o = _asset("old", fm, "old asset brought in at 50% depreciated", {"cost": 12_000_000, "residual": 0, "life": 24, "start": D(2023, 10, 1), "opening": 6_000_000},
               asset_name="Tenda Contoh lama", category=cat, acquisition_date="2021-10-01", book_date="2023-09-30", cost=12_000_000,
               useful_life_months=24, residual_value=0, source="opening", opening_accumulated=6_000_000)
    if o:
        eq("old asset: opening journal: cost, its depreciation, Ekuitas saldo awal", gl(api, "fixed_asset", o["name"]),
           {"12100": 12_000_000, "12900": -6_000_000, "31900": -6_000_000})
        eq("old asset: book value 6.000.000 at the opening date", book(api, o["name"]).get("book_value"), 6_000_000)
    _asset("late", fm, "asset from May 2023, first run in October (catch-up)", {"cost": 12_000_000, "residual": 0, "life": 12, "start": D(2023, 6, 1)},
           asset_name="Genset Contoh", category=cat, acquisition_date="2023-05-10", cost=12_000_000, useful_life_months=12, residual_value=0,
           source="other", paid_from="bank")
    _asset("sold", fm, "camera to be sold at month 12", {"cost": 24_000_000, "residual": 0, "life": 24, "start": D(2023, 11, 1)},
           asset_name="Kamera Contoh B", category=cat, acquisition_date="2023-10-01", cost=24_000_000, useful_life_months=24, residual_value=0,
           source="other", paid_from="bank")
    _asset("lost", fm, "lighting kit to be written off", {"cost": 4_000_000, "residual": 0, "life": 4, "start": D(2023, 11, 1)},
           asset_name="Lampu Contoh", category=cat, acquisition_date="2023-10-05", cost=4_000_000, useful_life_months=4, residual_value=0,
           source="other", paid_from="cash")
    # a residual value: (cost - residual) / life, never depreciated below the residual
    _asset("resid", fm, "car with a 20% residual", {"cost": 150_000_000, "residual": 30_000_000, "life": 96, "start": D(2023, 11, 1)},
           asset_name="Mobil box Contoh", category=S.get("cats", {}).get("Kendaraan", {}).get("name"), acquisition_date="2023-10-25",
           cost=150_000_000, useful_life_months=96, residual_value=30_000_000, source="other", paid_from="bank")
    # below the capitalisation threshold: an expense, not an asset
    x = new_asset(fm, "a Rp 500.000 kettle", asset_name="Ketel", category=cat, acquisition_date="2023-10-01", cost=500_000,
                  useful_life_months=12, source="other")
    if x:
        st, r = submit(fm, "fixed_asset", x["name"])
        check("below the capitalisation threshold: refused 422 (below_threshold)", st == 422 and "below_threshold" in rules(r), (st, r))
        fm.call("DELETE", f"/api/v1/r/fixed_asset/{Api.q(x['name'])}")
    # cancelled before any run: its purchase journal is reversed, its register row closed
    c = add_asset(fm, "asset entered by mistake", asset_name="Salah input", category=cat, acquisition_date="2023-10-02", cost=3_000_000,
                  useful_life_months=24, source="other", paid_from="cash")
    if c:
        st, r = cancel(fm, "fixed_asset", c["name"])
        check("an asset with no run cancels -> 200", st == 200, (st, r))
        eq("cancelled asset: journal reversed, register row Batal", (gl(api, "fixed_asset", c["name"], "reverse"), book(api, c["name"]).get("state")),
           ({"12100": -3_000_000, "11100": 3_000_000}, "cancelled"))
    # a rental staff member sees the asset and its unit, not what it cost (contract 0027)
    gd = CTX.get("gd")
    if gd and a:
        r = gd.get("asset_book", book(api, a["name"]).get("name", "x")) or {}
        check("rental staff read the register row without cost or book value", r.get("asset_name") == "Kamera Contoh A" and "cost" not in r and "book_value" not in r, r)
        st, resp = gd.create("fixed_asset", {"asset_name": "x", "acquisition_date": "2023-10-01", "cost": 2_000_000, "source": "other"})
        check("rental staff cannot create an asset", denied(st, resp), (st, resp))


def _lines(run):
    return {l["asset"]: (l.get("months"), l["amount"]) for l in run.get("lines", [])}


def _expect(period):
    out = {}
    for v in S.values():
        if isinstance(v, dict) and "o" in v and not v["o"].get("gone"):
            ln = O.run_line(v["o"], period)
            if ln:
                out[v["doc"]["name"]] = ln
    return out


def _apply(period):
    for v in S.values():
        if isinstance(v, dict) and "o" in v and not v["o"].get("gone"):
            O.apply(v["o"], period)


def _run(cl, period, label, draft=None):
    """Draft (or take the scheduled draft), compare its lines with the oracle, submit; returns the submitted run."""
    if draft is None:
        st, r = cl.create("depreciation_run", {"period": iso(period)})
        if not check(f"{label}: draft -> 201", st == 201, (st, r)):
            return {}
        draft = rec(r)
    run = cl.get("depreciation_run", draft["name"]) or {}
    exp = _expect(period)
    if _lines(run) != exp:
        fail(f"{label}: lines = oracle", (_lines(run), exp))
        return {}
    st, s = submit(cl, "depreciation_run", draft["name"])
    if not check(f"{label}: submitted -> 200", st == 200, (st, s)):
        return {}
    _apply(period)
    j = gl(cl, "depreciation_run", draft["name"])
    tot = sum(a for _, a in exp.values())
    if j and j != {"61950": tot, "12900": -tot}:
        fail(f"{label}: journal 6-1950 against 1-2900", j)
    return s


def scenario_fixed_assets_depreciation(api):
    """37 monthly runs, October 2023 to October 2026, each listing exactly what the oracle owes each asset."""
    fm = CTX.get("fm") or api
    if not S.get("cam"):
        fail("depreciation: the assets of the timeline", "not created")
        return
    D = datetime.date
    first = D(2023, 10, 1)
    today = datetime.date.today()
    last_month = O.add_months(D(today.year, today.month, 1), -1)
    end = max(D(2026, 10, 1), last_month)
    runs, period, n = {}, first, 0
    while period <= end:
        n += 1
        if period == D(2023, 10, 1):
            run = fm.get("depreciation_run", rec(fm.create("depreciation_run", {"period": iso(period)})[1]).get("name", "x")) or {}
            eq("October 2023: the camera bought that month is not in the run; the late asset catches up five months",
               (S["cam"]["doc"]["name"] in _lines(run), _lines(run).get(S["late"]["doc"]["name"])), (False, (5, 5_000_000)))
            runs[period] = _run(fm, period, "run 2023-10", draft=run)
            # the same month again: refused at submit
            st, r = fm.create("depreciation_run", {"period": iso(period)})
            st, s = submit(fm, "depreciation_run", rec(r).get("name", "x"))
            check("a second run for a month already submitted: refused 422 (run_exists)", st == 422 and "run_exists" in rules(s), (st, s))
            fm.call("DELETE", f"/api/v1/r/depreciation_run/{Api.q(rec(r).get('name', 'x'))}")
        elif period == last_month:
            # the scheduled flow drafts last month's run and tells finance; a person submits it
            st, out = run_flow(api, "depreciation_run_monthly")
            drafts = fm.find("depreciation_run", f"period == '{iso(period)}' && docstatus == 0")
            exp = _expect(period)
            check(f"scheduled flow: draft for {iso(period)[:7]} when an asset is due ({len(exp)} due)", st == 200 and (len(drafts) == 1) == bool(exp), (st, out, drafts))
            if drafts:
                st, inbox = fm.call("GET", "/api/v1/notices?limit=200")
                kinds = [n.get("kind") for n in (inbox or {}).get("data", [])] if st == 200 else []
                check("scheduled flow: finance is told the draft is ready (notice depreciation_ready)", any(str(k).endswith("depreciation_ready") for k in kinds), (st, kinds[:10]))
                runs[period] = _run(fm, period, f"run {iso(period)[:7]} (scheduled draft)", draft=drafts[0])
                run_flow(api, "depreciation_run_monthly")
                eq("scheduled flow: a second call makes no second draft", len(fm.find("depreciation_run", f"period == '{iso(period)}' && docstatus != 2")), 1)
        else:
            runs[period] = _run(fm, period, f"run {iso(period)[:7]}")
        if period == D(2023, 12, 1) and S.get("lost"):
            _write_off(api, fm)
        if period == D(2024, 10, 1) and S.get("sold"):
            _sell(api, fm)
        if period == D(2023, 11, 1):
            # cancelling a depreciated asset is refused
            st, r = cancel(fm, "fixed_asset", S["cam"]["doc"]["name"])
            check("an asset with depreciation cannot be cancelled: 422 (asset_in_use)", st == 422 and "asset_in_use" in rules(r), (st, r))
        if period == D(2023, 12, 1):
            st, r = cancel(fm, "depreciation_run", runs[D(2023, 11, 1)].get("name", "x"))
            check("only the latest run cancels: November after December refused 422 (run_not_latest)", st == 422 and "run_not_latest" in rules(r), (st, r))
        period = O.add_months(period, 1)
    S["runs"] = runs
    cam, rnd = book(api, S["cam"]["doc"]["name"]), book(api, S["round"]["doc"]["name"])
    eq("camera: after 24 runs book value 0, Habis disusutkan", (cam.get("book_value"), cam.get("state"), cam.get("depreciated_through")), (0, "fully_depreciated", "2025-10-01"))
    eq("Rp 10.000.000 / 36: accumulated = cost exactly", rnd.get("accumulated"), 10_000_000)
    ln = [l for l in (runs.get(D(2026, 10, 1)) or {}).get("lines", []) if l["asset"] == S["round"]["doc"]["name"]]
    eq("Rp 10.000.000 / 36: the 36th month is 277.770", [l["amount"] for l in ln], [277_770])
    eq("old asset: fully depreciated after 12 runs", (book(api, S["old"]["doc"]["name"]).get("state"), book(api, S["old"]["doc"]["name"]).get("book_value")), ("fully_depreciated", 0))
    car = book(api, S["resid"]["doc"]["name"])
    eq("car: depreciated to the oracle, never below its residual", (car.get("accumulated"), car.get("book_value") >= 30_000_000), (S["resid"]["o"]["acc"], True))


def _write_off(api, fm):
    """Lighting kit written off after two months: book value 2.000.000 is a debit on 4-9300."""
    b = book(api, S["lost"]["doc"]["name"])
    st, d = fm.create("asset_disposal", {"book": b["name"], "disposal_date": "2023-12-15", "kind": "written_off"})
    check("a disposal dated before the last run (15 Dec, run of December): refused 422 (after_last_run)", st == 422 and "after_last_run" in rules(d), (st, d))
    st, d = fm.create("asset_disposal", {"book": b["name"], "disposal_date": "2023-12-31", "kind": "written_off"})
    st, r = submit(fm, "asset_disposal", rec(d).get("name", "x"))
    if not check("written off on 31 Dec: submitted -> 200", st == 200, (st, r)):
        return
    exp = O.disposal(S["lost"]["o"], 0)
    eq("written off with book value 2.000.000: 4-9300 debit 2.000.000", gl(api, "asset_disposal", r["name"]),
       {"12900": exp["12900"], "12100": exp["12100"], "49300": exp["49300"]})
    eq("written-off asset: register row Dihapus", book(api, S["lost"]["doc"]["name"]).get("state"), "written_off")
    st, again = fm.create("asset_disposal", {"book": b["name"], "disposal_date": "2024-01-31", "kind": "written_off"})
    check("an asset already written off cannot be disposed of again (asset_in_use)", st == 422 and "asset_in_use" in rules(again), (st, again))
    S["lost"]["o"]["gone"] = True
    S["lost_disposal"] = r["name"]


def _sell(api, fm):
    """Camera B sold at month 12 for 15.000.000 with book value 12.000.000: gain 3.000.000 credited to 4-9300."""
    b = book(api, S["sold"]["doc"]["name"])
    eq("camera B: book value 12.000.000 after 12 runs", b.get("book_value"), 12_000_000)
    st, d = fm.create("asset_disposal", {"book": b["name"], "disposal_date": "2024-10-31", "kind": "sold", "proceeds": 15_000_000,
                                         "received_in": "bank", "buyer": "Pembeli Contoh"})
    st, r = submit(fm, "asset_disposal", rec(d).get("name", "x"))
    if not check("camera B sold on 31 Oct 2024: submitted -> 200", st == 200, (st, r)):
        return
    eq("sale: gain_loss 3.000.000", r.get("gain_loss"), 3_000_000)
    eq("sale journal: depreciation and cost out, bank in, gain credited", gl(api, "asset_disposal", r["name"]),
       {"12900": 12_000_000, "11200": 15_000_000, "12100": -24_000_000, "49300": -3_000_000})
    eq("sold asset: register row Dijual", book(api, S["sold"]["doc"]["name"]).get("state"), "sold")
    oct_run = fm.find("depreciation_run", "period == '2024-10-01' && docstatus == 1")
    if oct_run:
        st, x = cancel(fm, "depreciation_run", oct_run[0]["name"])
        check("the run of a camera sold since cannot be cancelled first: 422 (run_disposed)", st == 422 and "run_disposed" in rules(x), (st, x))
    S["sold"]["o"]["gone"] = True
    st, rep = api.create("financial_report", {"period_from": "2024-10-01", "period_to": "2024-10-31"})
    rep = rec(rep)
    check("October 2024 report: the gain is other income and the balance sheet balances",
          rep.get("balanced") is True and rep.get("other_income", 0) >= 3_000_000 and rep.get("tax_expense") == 0, rep)


def scenario_fixed_assets_cancel_and_lock(api):
    """The latest run cancels (assets and journal back), a closed period refuses it, the disposal cancels too."""
    fm = CTX.get("fm") or api
    runs = S.get("runs") or {}
    if not runs:
        return
    period = max(p for p, r in runs.items() if r)
    run = runs[period]
    # a stale draft: an amount changed by hand is refused at submit
    nxt = O.add_months(period, 1)
    st, dr = fm.create("depreciation_run", {"period": iso(nxt)})
    dr = fm.get("depreciation_run", rec(dr).get("name", "x")) or {}
    if dr.get("lines"):
        lines = [dict(l) for l in dr["lines"]]
        lines[0]["amount"] += 1
        fm.patch("depreciation_run", dr["name"], {"lines": lines})
        st, r = submit(fm, "depreciation_run", dr["name"])
        check("a run whose amount was changed by hand: refused 422 (run_stale)", st == 422 and "run_stale" in rules(r), (st, r))
    if dr:
        fm.call("DELETE", f"/api/v1/r/depreciation_run/{Api.q(dr['name'])}")
    # Tutup buku up to the run's month: its cancel would post into a closed period
    st, pc = api.create("period_close", {"closed_until": run["posting_date"]})
    st, pcs = api.verb("period_close", rec(pc).get("name", "x"), "submit")
    if check("Tutup buku up to the last run's month -> 200", st == 200, (st, pcs)):
        st, r = cancel(fm, "depreciation_run", run["name"])
        check("cancelling a run in a closed period: refused 422 (period_open)", st == 422 and "period_open" in rules(r), (st, r))
        st, r = api.verb("period_close", rec(pcs).get("name", rec(pc).get("name", "x")), "cancel")
        check("Tutup buku cancelled again -> 200", st == 200, (st, r))
    before = {k: book(api, v["doc"]["name"]) for k, v in S.items() if isinstance(v, dict) and "doc" in v}
    st, r = cancel(fm, "depreciation_run", run["name"])
    if check(f"the latest run ({iso(period)[:7]}) cancels -> 200", st == 200, (st, r)):
        rev = gl(api, "depreciation_run", run["name"], "reverse")
        eq("cancelled run: journal reversed line by line", rev, {a: -x for a, x in gl(api, "depreciation_run", run["name"]).items()})
        for line in run.get("lines", []):
            k = [k for k, v in before.items() if v.get("asset") == line["asset"]][0]
            after = book(api, line["asset"])
            eq(f"cancelled run gives back {line['asset']}: accumulated, month and state",
               (after.get("accumulated"), after.get("depreciated_through"), after.get("state")),
               (before[k]["accumulated"] - line["amount"], line.get("prev_through") or "", "active"))
        # and the same month again
        st, again = fm.create("depreciation_run", {"period": iso(period)})
        again = fm.get("depreciation_run", rec(again).get("name", "x")) or {}
        eq("the month made again lists the same lines", _lines(again), _lines(run))
        st, s = submit(fm, "depreciation_run", again.get("name", "x"))
        check("and submits -> 200", st == 200, (st, s))
    # a run that holds an asset disposed of later cannot be cancelled before the disposal
    if S.get("lost_disposal"):
        st, r = cancel(fm, "asset_disposal", S["lost_disposal"])
        check("the write-off cancels -> 200, the asset is in use again", st == 200 and book(api, S["lost"]["doc"]["name"]).get("state") == "active", (st, r))
        eq("cancelled write-off: journal reversed", gl(api, "asset_disposal", S["lost_disposal"], "reverse").get("49300"), -2_000_000)


def scenario_fixed_assets_register_print(api):
    """Daftar aset tetap prints from a run; the balance sheet's fixed assets = the register's book values."""
    runs = [r for r in api.find("depreciation_run", "docstatus == 1")]
    if not runs:
        fail("register print: a submitted run", "none")
        return
    last = max(runs, key=lambda r: r["period"])
    st, html, _ = api.print_html("depreciation_run", last["name"], "fixed_asset_register")
    check("Daftar aset tetap prints (assets, book values, this month's lines)", st == 200 and "Daftar aset tetap" in html and "Proyektor Contoh" in html, st)
    books = [b for b in api.find("asset_book", "state in ['active', 'fully_depreciated']")]
    st, rep = api.create("financial_report", {"period_from": "2026-10-01", "period_to": "2026-10-31"})
    rep = rec(rep)
    eq("balance sheet: fixed assets net = sum of the register's book values", rep.get("fixed_assets_net"), sum(b["book_value"] for b in books))
    eq("balance sheet balances", (rep.get("balanced"), rep.get("difference")), (True, 0))


def scenario_fixed_assets_import(api):
    """Daftar aset lama (examples/fixtures/imports/asset-list.csv): three draft opening assets."""
    path = os.path.join(ROOT, "examples", "fixtures", "imports", "asset-list.csv")
    content = open(path, "rb").read()
    st, pv = api.upload("/api/v1/imports/asset_list", {}, "asset-list.csv", content)
    if not check("asset list import: preview 3 rows, no errors", st in (200, 201) and pv.get("rows") == 3 and not pv.get("errors"), (st, pv)):
        return
    st, done = api.call("POST", f"/api/v1/imports/asset_list/{pv['id']}/commit", {})
    names = (done or {}).get("records", [])
    check("asset list import: commit -> 3 drafts", st == 200 and len(names) == 3, (st, done))
    if names:
        a = api.get("fixed_asset", names[0]) or {}
        eq("imported asset: Aset lama, category matched, depreciation since", (a.get("source"), bool(a.get("category")), a.get("opening_accumulated")),
           ("opening", True, 9_000_000))
        eq("imported asset: starts the month after the opening date", a.get("depreciation_start"), "2026-01-01")


SCENARIOS_SEWA = [scenario_fixed_assets_setup, scenario_fixed_assets_register, scenario_fixed_assets_depreciation,
                  scenario_fixed_assets_cancel_and_lock, scenario_fixed_assets_register_print, scenario_fixed_assets_import]


# ---------- vendor bills (demo-kepingan-keuangan PKP, demo-jasa's second PT not PKP)

def _bill_assets(cl, label, pkp):
    setup(cl)
    vendors = [v for v in cl.find("vendor", "true") if v.get("vendor_name") != "Saldo awal"]
    items = cl.find("item", "true")
    if not check(f"{label}: a vendor and an item", vendors and items, (len(vendors), len(items))):
        return
    v = ([x for x in vendors if x.get("pkp")] or [{}])[0]
    if not v:   # a PKP supplier, so the bill carries PPN (a cost of the asset when the business is not PKP)
        st, nv = cl.create("vendor", {"vendor_name": "Toko Contoh Elektronik", "pkp": True})
        v = rec(nv)
        if not check(f"{label}: a PKP supplier", st == 201, (st, nv)):
            return
    it = items[0]["name"]
    day = datetime.date.today().replace(day=3).isoformat()
    body = {"vendor": v["name"], "bill_no": f"AST/{label[:3].upper()}/1", "bill_date": day, "posting": "asset",
            "items": [{"item": it, "description": "Laptop kasir", "qty": 1, "rate": 7_000_001},
                      {"item": it, "description": "Printer label", "qty": 1, "rate": 3_333_333},
                      {"item": it, "description": "Rak besi", "qty": 2, "rate": 1_250_000}]}
    st, b = cl.create("vendor_bill", body)
    if not check(f"{label}: bill posted as Aset tetap: draft -> 201", st == 201, (st, b)):
        return
    st, sb = cl.verb("vendor_bill", rec(b)["name"], "submit", {"force": True})
    sb = rec(sb) if st == 200 else sb
    if not check(f"{label}: bill submitted -> 200", st == 200, (st, sb)):
        return
    bill = cl.get("vendor_bill", sb.get("name", rec(b)["name"]))
    j = gl(cl, "vendor_bill", bill["name"])
    cost_total = bill["dpp"] + (0 if pkp else bill["ppn"])
    eq(f"{label}: the bill debits Aset tetap (DPP{'' if pkp else ' + PPN'})", j.get("12100"), cost_total)
    drafts = sorted(cl.find("fixed_asset", f"vendor_bill == '{bill['name']}'"), key=lambda a: a["bill_line"])
    eq(f"{label}: one draft asset per bill line", [(a["asset_name"], a["docstatus"]) for a in drafts],
       [("Laptop kasir", 0), ("Printer label", 0), ("Rak besi", 0)])
    eq(f"{label}: asset costs add up to the bill's Aset tetap debit", sum(a["cost"] for a in drafts), cost_total)
    if pkp:
        eq(f"{label}: PKP: each asset costs its line before PPN", [a["cost"] for a in drafts], [7_000_001, 3_333_333, 2_500_000])
        eq(f"{label}: PKP: the PPN goes to PPN masukan", j.get("11500"), bill["ppn"])
    else:
        check(f"{label}: not PKP: the bill carries PPN ({bill['ppn']}) and each asset takes its share",
              bill["ppn"] > 0 and all(a["cost"] > ln for a, ln in zip(drafts, [7_000_001, 3_333_333, 2_500_000])), [a["cost"] for a in drafts])
    cat = (cl.find("asset_category", "category_name == 'Komputer dan HP'") or [{}])[0].get("name")
    a0 = drafts[0] if drafts else {}
    st, r = cl.patch("fixed_asset", a0.get("name", "x"), {"category": cat, "useful_life_months": 48, "cost": a0.get("cost", 0) + 1})
    st, r = submit(cl, "fixed_asset", a0.get("name", "x"))
    check(f"{label}: an asset whose cost differs from its bill line: refused 422 (bill_source)", st == 422 and "bill_source" in rules(r), (st, r))
    cl.patch("fixed_asset", a0.get("name", "x"), {"cost": a0.get("cost", 0)})
    st, r = submit(cl, "fixed_asset", a0.get("name", "x"))
    if check(f"{label}: finance completes and submits the laptop -> 200", st == 200, (st, r)):
        eq(f"{label}: an asset from a bill posts no journal of its own", gl(cl, "fixed_asset", r["name"]), {})
        eq(f"{label}: register row at the bill line's cost", book(cl, r["name"]).get("book_value"), a0["cost"])
    st, r = cancel(cl, "vendor_bill", bill["name"])
    check(f"{label}: the bill cannot be cancelled while its asset is submitted (bill_has_asset)", st == 422 and "bill_has_asset" in rules(r), (st, r))
    # a line below the threshold refuses the whole bill
    st, b2 = cl.create("vendor_bill", dict(body, bill_no=f"AST/{label[:3].upper()}/2", items=[{"item": it, "description": "Mouse", "qty": 1, "rate": 150_000}]))
    st, r = cl.verb("vendor_bill", rec(b2).get("name", "x"), "submit", {"force": True})
    check(f"{label}: a bill line below the capitalisation threshold: refused 422 (below_threshold)", st == 422 and "below_threshold" in rules(r), (st, r))
    cl.call("DELETE", f"/api/v1/r/vendor_bill/{Api.q(rec(b2).get('name', 'x'))}")


def scenario_fixed_assets_from_bill(api):
    site = CTX.get("site")
    if site == "demo-kepingan-keuangan":
        _bill_assets(api, "keuangan PKP", True)
    elif site == "demo-jasa":
        k = CTX["kernel"]
        org2 = Api.login(k.base, k.owner_email, k.owner_pw, org="cv_contoh_pelatihan")
        if check("asset bill: owner signs in to the non-PKP PT", org2 is not None, "login refused"):
            _bill_assets(org2, "jasa non-PKP", False)
