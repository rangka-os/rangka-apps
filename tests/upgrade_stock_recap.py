"""Upgrade check for stock_adjustment_value (rangka-apps#14): adjustments submitted before the model existed get their
value rows from flow adjustment_value_backfill, and nobody can fill those empty slots by hand.

    python3 tests/upgrade.py --site demo-fnb --check stock_recap

Old tree: 10 kg tahu mentah received at Dago at 12.000, then four adjustments of BRG-0001 at Dago:
A damage 2 kg (10 days ago), B expired 1 kg (5 days ago), C other 3 kg (3 days ago, cancelled), D waste 1 kg (2 days ago).
Expected values are written out by hand (kg x 12.000), not computed from the rows the widget reads.
"""
import datetime
import gc
import time

DAGO = "OUT-02"
TODAY = datetime.date.today()


def _day(n):
    return (TODAY - datetime.timedelta(days=n)).isoformat()


def _adjust(api, reason, kg, days):
    st, adj = api.create("stock_adjustment", {"outlet": DAGO, "reason": reason, "adjustment_date": _day(days),
                                              "items": [{"item": "BRG-0001", "qty": kg}]})
    assert st == 201, (st, adj)
    st, resp = api.verb("stock_adjustment", adj["name"], "submit")
    assert st == 200, (st, resp)
    return resp["name"]


def before(api):
    st, gr = api.create("goods_receipt", {"vendor": "VND-0001", "outlet": DAGO, "items": [{"item": "BRG-0001", "qty": 10, "rate": 12000}]})
    assert st == 201, (st, gr)
    st, resp = api.verb("goods_receipt", gr["name"], "submit")
    assert st == 200, (st, resp)
    s = {"A": _adjust(api, "damage", 2, 10), "B": _adjust(api, "expired", 1, 5), "C": _adjust(api, "other", 3, 3), "D": _adjust(api, "waste", 1, 2)}
    st, resp = api.verb("stock_adjustment", s["C"], "cancel")
    assert st == 200, (st, resp)
    return s


def _rows(api):
    return sorted((r["stock_adjustment"], r["entry"], r["posting_date"], r["reason"], r["value"]) for r in api.list("stock_adjustment_value"))


def _waste(d1, api, period):
    return d1.chart_map(d1.dashboard(api, "stock_recap", period).get("waste_by_outlet", {})).get(DAGO, 0)


def after(api, s, d1):
    check, eq = d1.check, d1.eq
    k = next(o for o in gc.get_objects() if isinstance(o, d1.Kernel) and o.db)   # the kernel upgrade.py started
    d1.setup_demo_fnb(api, k)
    hq, dago = d1.CTX.get("hq"), d1.CTX.get("staff_dago")
    if not (hq and dago):
        return
    eq("after migrate: the four old adjustments have no value rows yet", _rows(api), [])

    # the empty slots of old adjustments cannot be filled by hand (audit round 2 of #14, U1 and U4)
    st, resp = dago.create("stock_adjustment_value", {"stock_adjustment": s["A"], "entry": "post", "outlet": DAGO,
                                                      "posting_date": _day(10), "reason": "damage", "value": -99_000_000})
    check("outlet staff cannot write a post row for an old adjustment (-99.000.000) -> 409 append_only", st == 409, (st, resp))
    st, resp = hq.create("stock_adjustment_value", {"stock_adjustment": s["C"], "entry": "reverse", "outlet": DAGO,
                                                    "posting_date": _day(3), "reason": "other", "value": 77_000_000})
    check("head office cannot write a reverse row for an old cancelled adjustment (77.000.000) -> 409 append_only", st == 409, (st, resp))
    st, resp = api.create("stock_adjustment_value", {"stock_adjustment": s["B"], "entry": "post", "outlet": DAGO,
                                                     "posting_date": _day(5), "reason": "expired", "value": -1})
    check("even the owner cannot write a value row through the API -> 409 append_only", st == 409, (st, resp))
    eq("no row was made by hand", _rows(api), [])

    # append_only (contract 0026): a flow run by hand may not post ledger rows, the owner's included; the schedule
    # (every 5 minutes) does it
    st, resp = api.call("POST", "/api/v1/flows/adjustment_value_backfill/run", {})
    check("the owner cannot run the backfill by hand -> 409 append_only", st == 409, (st, resp))
    eq("the refused run wrote nothing", _rows(api), [])
    want = sorted([(s["A"], "post", _day(10), "damage", -24000), (s["B"], "post", _day(5), "expired", -12000),
                   (s["D"], "post", _day(2), "waste", -12000)])
    deadline = time.time() + 330
    while _rows(api) != want and time.time() < deadline:
        time.sleep(10)
    eq("backfill by its schedule: one post row per live old adjustment, on its own date (2 kg, 1 kg, 1 kg x 12.000); none for cancelled C", _rows(api), want)
    time.sleep(15)
    eq("nothing more is added while waiting", _rows(api), want)
    eq("waste_by_outlet Dago over 30 days after backfill = -48.000", _waste(d1, api, 30), -48000)
    eq("waste_by_outlet Dago over 7 days after backfill = -24.000 (B and D)", _waste(d1, api, 7), -24000)

    st, resp = hq.verb("stock_adjustment", s["D"], "cancel")
    check("head office cancels old adjustment D after the upgrade", st == 200, (st, resp))
    eq("its reverse row is +12.000 on D's own date", [r for r in _rows(api) if r[1] == "reverse"], [(s["D"], "reverse", _day(2), "waste", 12000)])
    eq("waste_by_outlet Dago over 30 days after cancelling D = -36.000", _waste(d1, api, 30), -36000)
    eq("waste_by_outlet Dago over 7 days after cancelling D = -12.000", _waste(d1, api, 7), -12000)
