"""Hand-written fixed asset oracle (module fixed_assets, rangka-apps#113): the expected straight-line depreciation of one
asset, run by run, and the gain or loss of a disposal.

Written from the issue's acceptance cases and the accounting rule (garis lurus: (cost - residual) / useful life per
month, rounded half up to the rupiah, the last month takes what is left so the total is exactly cost - residual), not
from the .rk files. Months are counted on the calendar here; the module counts them from days (flow
depreciation_run_fill), and months_formula() mirrors that so the self-test can prove the two agree.

    python3 tests/fixed_assets_oracle.py      # self-test: the issue's cases and the month count over 1990-2100
"""
import datetime
from decimal import Decimal, ROUND_HALF_UP


def rnd(x):
    return int(Decimal(x).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def monthly(cost, residual, life):
    return rnd(Decimal(cost - residual) / Decimal(life))


def month_index(d):
    return d.year * 12 + d.month - 1


def add_months(d, n):
    i = month_index(d) + n
    return datetime.date(i // 12, i % 12 + 1, 1)


def months_formula(a, b):
    """The module's count of months from first-of-month a to first-of-month b (flow depreciation_run_fill)."""
    return int((float((b - a).days) + 15.0) / 30.436875)


def run_line(asset, period):
    """What the run of `period` (a first of month) owes `asset`, a dict with cost, residual, life, start (first of
    month), opening (depreciation before the books) and through (last month depreciated or None, updated by apply()).
    Returns (months, amount) or None when the asset is not in the run."""
    nxt = asset["start"] if asset.get("through") is None else add_months(asset["through"], 1)
    left = asset["cost"] - asset.get("opening", 0) - asset.get("acc", 0) - asset["residual"]
    if nxt > period or left <= 0:
        return None
    months = month_index(period) - month_index(nxt) + 1
    due = monthly(asset["cost"], asset["residual"], asset["life"]) * months
    # the last month takes what is left: also when the monthly amount was rounded down and a few rupiah would remain
    # (the drift of rounding down is at most half a rupiah per month of life, a true last month at least half a month)
    return months, left if 2 * (left - due) < monthly(asset["cost"], asset["residual"], asset["life"]) else due


def apply(asset, period):
    line = run_line(asset, period)
    if line:
        asset["acc"] = asset.get("acc", 0) + line[1]
        asset["through"] = period
    return line


def book_value(asset):
    return asset["cost"] - asset.get("opening", 0) - asset.get("acc", 0)


def disposal(asset, proceeds):
    """(debit 12900, debit cash, credit 12100, signed 49300) of selling or writing off the asset; 49300 < 0 is a gain."""
    acc = asset.get("opening", 0) + asset.get("acc", 0)
    bv = asset["cost"] - acc
    return {"12900": acc, "cash": proceeds, "12100": -asset["cost"], "49300": -(proceeds - bv)}


def _self_test():
    # month count: the module's day formula equals the calendar for every pair of firsts of month 1990-2100, span <= 600
    firsts = [datetime.date(y, m, 1) for y in range(1990, 2101) for m in range(1, 13)]
    for i, a in enumerate(firsts):
        for b in firsts[i:i + 601]:
            assert months_formula(a, b) == month_index(b) - month_index(a), (a, b)
    # camera bought 1 Jan 2026, Rp 24.000.000, residual 0, 24 months, depreciation from February
    cam = {"cost": 24_000_000, "residual": 0, "life": 24, "start": datetime.date(2026, 2, 1)}
    assert run_line(cam, datetime.date(2026, 1, 1)) is None
    lines = [apply(cam, add_months(datetime.date(2026, 2, 1), k)) for k in range(25)]
    assert lines[0] == (1, 1_000_000) and all(x == (1, 1_000_000) for x in lines[:24]) and lines[24] is None
    assert book_value(cam) == 0
    # rounding: Rp 10.000.000 over 36 months: 35 x 277.778 and a last month of 277.770
    r = {"cost": 10_000_000, "residual": 0, "life": 36, "start": datetime.date(2026, 1, 1)}
    amts = [apply(r, add_months(r["start"], k))[1] for k in range(36)]
    assert amts[:35] == [277_778] * 35 and amts[35] == 277_770 and sum(amts) == 10_000_000
    # rounded down: Rp 10.000.000 over 48 months is 208.333 a month; month 48 takes 208.349, no month 49
    q = {"cost": 10_000_000, "residual": 0, "life": 48, "start": datetime.date(2026, 1, 1)}
    amts = [apply(q, add_months(q["start"], k)) for k in range(49)]
    assert [a[1] for a in amts[:47]] == [208_333] * 47 and amts[47][1] == 208_349 and amts[48] is None and book_value(q) == 0
    # catch-up: an asset from January whose first run is March takes three months at once
    c = {"cost": 12_000_000, "residual": 0, "life": 12, "start": datetime.date(2026, 1, 1)}
    assert apply(c, datetime.date(2026, 3, 1)) == (3, 3_000_000)
    # sold at month 12 for 15.000.000 with book value 12.000.000: gain 3.000.000 credited to 49300
    s = {"cost": 24_000_000, "residual": 0, "life": 24, "start": datetime.date(2026, 2, 1)}
    for k in range(12):
        apply(s, add_months(s["start"], k))
    d = disposal(s, 15_000_000)
    assert book_value(s) == 12_000_000 and d["49300"] == -3_000_000 and sum(d.values()) == 0
    # written off with book value 2.000.000: a 2.000.000 debit to 49300
    w = {"cost": 24_000_000, "residual": 0, "life": 24, "start": datetime.date(2026, 2, 1), "acc": 22_000_000}
    assert disposal(w, 0)["49300"] == 2_000_000
    print("fixed_assets_oracle: ok")


if __name__ == "__main__":
    _self_test()
