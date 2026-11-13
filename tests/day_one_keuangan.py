"""demo-kepingan-keuangan: a PKP shop that runs only the money and tax pieces (faktur_piutang, pajak, mutasi_bank,
pembelian_pemasok, pembukuan). One month of invoices, supplier bills and payments; the month's Coretax files (faktur
keluaran and BPPU); then the books (tests/day_one_accounting.py) check the profit and loss and balance sheet.

Loaded by tests/day_one.py (bind() hands over its helpers): `python3 tests/day_one.py --site demo-kepingan-keuangan`.
"""
import datetime
import xml.etree.ElementTree as ET
from decimal import Decimal

H = {}
SITE = "demo-kepingan-keuangan"
MEMBERS = [("fm", "finance_manager"), ("hq", "head_office")]


def bind(m):
    for k in ("ok", "fail", "gap", "check", "eq", "CTX", "Api", "secrets"):
        H[k] = getattr(m, k)
    globals().update(H)
    m.SITE_ORGS[SITE] = [("tahu_contoh", SITE)]
    # the shared matrix check runs for finance; head office's payment and goods-receipt grants belong to outlet sites
    # (as on demo-sewa), here it buys from suppliers and downloads the tax files
    m.SITE_MEMBERS[SITE] = [MEMBERS[0]]


def setup_demo_keuangan(api, k):
    """Finance (invoices, bills, tax files) and head office, signed in."""
    pw = secrets.token_urlsafe(18)   # throwaway password of a throwaway database
    for key, role in MEMBERS:
        email = f"{key}@stokcontoh.test"
        code, out = k.user_add(email, role, "tahu_contoh", f"{role} contoh", password=pw)
        cl = Api.login(k.base, email, pw) if code == 0 else None
        if cl is None:
            CTX["login_error"] = f"{email}: " + (out if code else "login refused")
            fail("setup: add and sign in the members", CTX["login_error"])
            return
        CTX[key] = cl


def submit(cl, model, body, label):
    st, d = cl.create(model, body)
    if not check(f"{label}: create", st == 201, (st, d)):
        return None
    st, resp = cl.verb(model, d["name"], "submit", {"force": True})
    if not check(f"{label}: submit", st == 200, (st, resp)):
        return None
    return cl.get(model, (resp or {}).get("name", d["name"]))


def scenario_keuangan_month(api):
    """Three invoices (catering service with PPh 23 withheld by the client, boxed meals with a discount, a monthly
    subscription), two PKP supplier bills entered by head office (food, cleaning with PPh 23 the shop withholds), a customer payment and a
    supplier payment, all dated in this month."""
    fm, hq = CTX.get("fm"), CTX.get("hq")
    if not (fm and hq):
        fail("keuangan: members", CTX.get("login_error", "members missing"))
        return
    today = datetime.date.today()
    d = lambda n: today.replace(day=min(n, 28)).isoformat()
    inv = [
        submit(fm, "sales_invoice", {"customer": "CUST-0001", "posting_date": d(3), "due_date": d(17), "pph23_applies": True,
                                     "items": [{"item": "BRG-0002", "qty": 2, "rate": 2_500_000}]}, "invoice: catering for an office (PPh 23 by the client)"),
        submit(fm, "sales_invoice", {"customer": "CUST-0002", "posting_date": d(5), "due_date": d(12), "discount_amount": 100_000,
                                     "items": [{"item": "BRG-0001", "qty": 120, "rate": 35_000}]}, "invoice: boxed meals with a discount"),
        submit(fm, "sales_invoice", {"customer": "CUST-0003", "posting_date": d(8), "due_date": d(28),
                                     "items": [{"item": "BRG-0003", "qty": 1, "rate": 4_500_000}]}, "invoice: monthly lunch subscription"),
    ]
    bills = [
        submit(hq, "vendor_bill", {"vendor": "VND-0002", "bill_no": "TAG/PGN/001", "bill_date": d(4), "due_date": d(28),
                                   "items": [{"item": "BRG-0004", "qty": 40, "rate": 60_000}]}, "bill: food supplies (PKP supplier)"),
        submit(hq, "vendor_bill", {"vendor": "VND-0003", "bill_no": "TAG/BRS/001", "bill_date": d(6), "due_date": d(20), "pph23_amount": 30_000,
                                   "items": [{"item": "BRG-0005", "qty": 1, "rate": 1_500_000}]}, "bill: kitchen cleaning (PPh 23 2% withheld)"),
    ]
    if not all(inv) or not all(bills):
        return
    eq("invoices: PPN 11/12 x 12% of each DPP", [i["ppn"] for i in inv], [round(i["dpp"] * 11 / 12 * 12 / 100) for i in inv])
    eq("the cleaning bill: object 24-104-36 from the supplier, base = the bill's DPP", (bills[1].get("pph23_object_code"), bills[1].get("pph23_base")),
       ("24-104-36", 1_500_000))
    p = inv[0]
    st, pay = fm.create("payment", {"direction": "receive", "customer": "CUST-0001", "method": "transfer", "amount": p["outstanding"],
                                    "allocations": [{"sales_invoice": p["name"], "amount": p["outstanding"]}]})
    st2, resp = fm.verb("payment", (pay or {}).get("name", ""), "submit") if st == 201 else (st, pay)
    check("the office pays its invoice (net of PPh 23)", st == 201 and st2 == 200, (st, pay, st2, resp))
    b = bills[0]
    st, pay = fm.create("payment", {"direction": "pay", "vendor": "VND-0002", "method": "transfer", "amount": b["outstanding"],
                                    "allocations": [{"vendor_bill": b["name"], "amount": b["outstanding"]}]})
    st2, resp = fm.verb("payment", (pay or {}).get("name", ""), "submit") if st == 201 else (st, pay)
    check("the shop pays the food supplier", st == 201 and st2 == 200, (st, pay, st2, resp))
    CTX["keuangan"] = {"invoices": inv, "bills": bills, "month": today.strftime("%Y-%m")}


def scenario_keuangan_coretax(api):
    """The month's Coretax files from the pajak app: every invoice in the faktur keluaran, the cleaning bill in the
    BPPU, each refused document named instead of a wrong file."""
    fm, data = CTX.get("fm"), CTX.get("keuangan")
    if not (fm and data):
        fail("keuangan coretax", "the month scenario did not run")
        return
    y, m = data["month"].split("-")
    seller = "9" + "0" * 13 + "66"   # a made-up NPWP
    st, tp = fm.create("tax_period", {"year": y, "month": m, "seller_tin": seller, "seller_nitku": seller + "000000"})
    if not check("finance opens the tax period of this month", st == 201, (st, tp)):
        return
    url = f"/api/v1/r/tax_period/{Api.q(tp['name'])}/print?format="
    st, body, _ = fm.html(url + "coretax_faktur")
    if not check("faktur keluaran XML downloads", st == 200, (st, body[:500])):
        return
    tis = ET.fromstring(body.encode()).findall("ListOfTaxInvoice/TaxInvoice")
    eq("every invoice of the month is in the faktur file", sorted(t.findtext("RefDesc").split(";")[0] for t in tis), sorted(i["name"] for i in data["invoices"]))
    for t in tis:
        i = next(x for x in data["invoices"] if t.findtext("RefDesc").startswith(x["name"]))
        lines = t.findall("ListOfGoodService/GoodService")
        eq(f"{i['name']}: lines add up to the DPP, PPN within Rp1", (sum(Decimal(g.findtext("TaxBase")) for g in lines),
           abs(sum(Decimal(g.findtext("VAT")) for g in lines) - Decimal(i["ppn"])) <= 1), (Decimal(i["dpp"]), True))
    opts = {t.findtext("RefDesc").split(";")[0]: [(g.findtext("Opt"), g.findtext("Unit")) for g in t.findall("ListOfGoodService/GoodService")] for t in tis}
    eq("goods are Opt A in Dus (UM.0022), services Opt B in Kegiatan/Bulan", [opts[i["name"]] for i in data["invoices"]],
       [[("B", "UM.0030")], [("A", "UM.0022")], [("B", "UM.0024")]])
    st, body, _ = fm.html(url + "coretax_bupot")
    if not check("BPPU XML downloads", st == 200, (st, body[:500])):
        return
    bpus = ET.fromstring(body.encode()).findall("ListOfBpu/Bpu")
    eq("one slip: the cleaning bill, NIK recipient, 24-104-36, base 1.500.000 at 2%",
       [(x.findtext("DocumentNumber"), x.findtext("CounterpartTin"), x.findtext("TaxObjectCode"), x.findtext("TaxBase"), x.findtext("Rate")) for x in bpus],
       [("TAG/BRS/001", "9000000000000301", "24-104-36", "1500000", "2")])
    hq = CTX.get("hq")
    if hq:
        st, _, _ = hq.html(url + "coretax_faktur")
        check("head office downloads the faktur file too", st == 200, st)


SCENARIOS = [scenario_keuangan_month, scenario_keuangan_coretax]
