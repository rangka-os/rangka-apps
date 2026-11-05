"""Upgrade check for the Moka import key (rangka-apps#7): Time joined the key, so the rows imported before the upgrade
have hashes the new key never matches, and an overlapping export re-imports them. The import cannot be made to match
the old hashes from .rk, so a record that repeats a day imported under the old key is held at submit until head office
has removed the repeated lines.

    python3 tests/upgrade.py --site demo-fnb --check moka_key --from origin/fix/moka-item-details-headers

Old tree: day 04-11-2026 at Dago, two rows (2 x tahu original 15.000 = 30.000, 3 x tahu isi 20.000 = 60.000), imported
and submitted: net 90.000. New tree: the overlapping export has the same two rows and one new (1 x tahu pedas 18.000).
"""
import gc
import secrets

DAGO = "OUT-02"
HEAD = ("Outlet,Receipt Number,Date,Time,Category,Brand,Items,Variant,SKU,Quantity,Modifier Applied,Discount Applied,"
        "Gross Sales,Discounts,Refunds,Net Sales,Gratuity,Tax,Sales Type,Collected By,Served By,Customer,Payment Method,"
        "Event Type,Reason of Refund\n")
R1 = "Outlet Dago,DGO-0101,04-11-2026,08:10:00,Tahu,Unbranded,Tahu goreng original,Porsi,TH-ORI,2,,,30000.0,0.0,0.0,30000.0,0.0,0.0,Dine In,Kasir 1,Kasir 1,,Cash,Payment,\n"
R2 = "Outlet Dago,DGO-0102,04-11-2026,09:20:00,Tahu,Unbranded,Tahu isi sayur,Porsi,TH-ISI,3,,,60000.0,0.0,0.0,60000.0,0.0,0.0,Take Away,Kasir 1,Kasir 1,,QRIS,Payment,\n"
R3 = "Outlet Dago,DGO-0103,04-11-2026,10:30:00,Tahu,Unbranded,Tahu goreng pedas,Porsi,TH-PDS,1,,,18000.0,0.0,0.0,18000.0,0.0,0.0,Dine In,Kasir 1,Kasir 1,,Cash,Payment,\n"


def _kernel():
    return next(o for o in gc.get_objects() if type(o).__name__ == "Kernel" and getattr(o, "db", None))   # upgrade.py's


def _preview(cl, name, content):
    return cl.upload("/api/v1/imports/moka_item_details", {"outlet": DAGO}, name, content.encode())


def _import(cl, name, content):
    st, pv = _preview(cl, name, content)
    assert st == 200 and pv["errors"] == [], (st, pv)
    st, c = cl.call("POST", f"/api/v1/imports/moka_item_details/{pv['id']}/commit")
    assert st == 200, (st, c)
    return pv, c["records"]


def before(api):
    k, pw = _kernel(), secrets.token_urlsafe(18)   # a throwaway password for a throwaway database
    st, me = api.call("GET", "/auth/me")
    org = ((me or {}).get("org") or {}).get("id") or (me or {}).get("user", {}).get("org")
    code, out = k.user_add("rina@tahucontoh.test", "head_office", org, "Rina Contoh", record="USR-001", password=pw)
    assert code == 0, out
    hq = type(api).login(k.base, "rina@tahucontoh.test", pw)
    pv, recs = _import(hq, "moka-04-11-a.csv", HEAD + R1 + R2)
    assert pv["rows"] == 2 and len(recs) == 1, pv
    st, resp = api.verb("outlet_sales", recs[0], "submit")
    assert st == 200, (st, resp)
    return {"old": resp["name"], "pw": pw}


def _net_0411(api):
    return sum(r["net_total"] for r in api.find("outlet_sales", f"outlet == '{DAGO}' && date == '2026-11-04' && docstatus == 1"))


def after(api, s, d1):
    check, eq = d1.check, d1.eq
    hq = d1.Api.login(_kernel().base, "rina@tahucontoh.test", s["pw"])
    if not check("head office signs in after the upgrade", hq is not None):
        return
    eq("old record: net 90.000", api.get("outlet_sales", s["old"])["net_total"], 90_000)
    st, pv = _preview(hq, "moka-04-11-b.csv", HEAD + R1 + R2 + R3)
    check("overlapping export after the upgrade previews (the old rows are not recognised by the new key)", st == 200 and pv["errors"] == [], (st, pv))
    st, c = hq.call("POST", f"/api/v1/imports/moka_item_details/{pv['id']}/commit")
    if not check("commit creates one draft", st == 200 and len(c.get("records", [])) == 1, (st, c)):
        return
    new = api.get("outlet_sales", c["records"][0])
    eq("the draft carries the three rows (90.000 again + 18.000)", (len(new["lines"]), new["net_total"]), (3, 108_000))
    eq("it is marked as overlapping a day imported under the old key", new.get("legacy_key_overlap"), True)
    st, resp = hq.verb("outlet_sales", new["name"], "submit")
    check("submitting it as is -> 422 (the day would count 198.000)", st == 422, (st, resp))
    eq("day 04-11 still nets 90.000", _net_0411(api), 90_000)
    keep = [l for l in new["lines"] if l["item"] == "BRG-0008"]
    st, resp = hq.patch("outlet_sales", new["name"], {"lines": keep, "legacy_key_checked": True})
    check("head office removes the two repeated lines and confirms the check", st == 200, (st, resp))
    st, resp = hq.verb("outlet_sales", new["name"], "submit")
    check("then it submits", st == 200, (st, resp))
    eq("day 04-11 nets 108.000 (90.000 + 18.000), not 198.000", _net_0411(api), 108_000)
    # a day first imported after the upgrade is not marked
    st, pv = _preview(hq, "moka-05-11.csv", HEAD + R3.replace("04-11-2026", "05-11-2026"))
    st, c = hq.call("POST", f"/api/v1/imports/moka_item_details/{pv['id']}/commit")
    if check("a new day imports", st == 200 and len(c.get("records", [])) == 1, (st, c)):
        rec = api.get("outlet_sales", c["records"][0])
        eq("a day with no old-key import is not marked", bool(rec.get("legacy_key_overlap")), False)
        st, resp = hq.verb("outlet_sales", rec["name"], "submit")
        check("and submits without a check", st == 200, (st, resp))
