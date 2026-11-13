"""Upgrade check for the PPN base (tests/upgrade.py --site demo-jasa --check ppn_base): an invoice submitted before
sales_invoice.ppn_base existed keeps its PPN after migrate, and its Coretax faktur still carries the whole DPP as
TaxBase (audit C1-3 H1: the kernel leaves computes that read `tax` empty on frozen documents); a draft made before the
upgrade gets ppn_base = DPP on its next save (tax.ppn_fee_only is off on this site)."""
import xml.etree.ElementTree as ET
from decimal import Decimal

BUYER_TAX_ID = "9" + "0" * 13 + "88"   # a made-up NPWP
SELLER_TAX_ID = "9" + "0" * 13 + "89"


def before(api):
    svc = [i for i in api.list("item") if i.get("kind") == "service"][0]["name"]
    st, cu = api.create("customer", {"customer_name": "PT Contoh Upgrade PPN", "address": "Jl. Contoh No. 11", "city": "Bandung", "npwp": BUYER_TAX_ID})
    body = {"customer": cu["name"], "posting_date": "2031-09-10", "due_date": "2031-09-10", "discount_amount": 500_000,
            "items": [{"item": svc, "qty": 2, "rate": 7_000_000}, {"item": svc, "qty": 1, "rate": 3_333_333}]}
    st, inv = api.create("sales_invoice", body)
    st, resp = api.verb("sales_invoice", inv["name"], "submit", {"force": True})
    st, draft = api.create("sales_invoice", dict(body, posting_date="2031-09-20", due_date="2031-09-20"))
    old = api.get("sales_invoice", resp.get("name", inv["name"]))
    return {"submitted": old["name"], "dpp": old["dpp"], "ppn": old["ppn"], "draft": draft["name"]}


def after(api, s, d1):
    old = api.get("sales_invoice", s["submitted"])
    d1.eq("old submitted invoice keeps its DPP and PPN after migrate", (old["dpp"], old["ppn"]), (s["dpp"], s["ppn"]))
    st, tp = api.create("tax_period", {"year": "2031", "month": "09", "seller_tin": SELLER_TAX_ID, "seller_nitku": SELLER_TAX_ID + "000000"})
    if not d1.check("tax period September 2031", st == 201, (st, tp)):
        return
    st, body, _ = api.html(f"/api/v1/r/tax_period/{d1.Api.q(tp['name'])}/print?format=coretax_faktur")
    if not d1.check("faktur XML downloads after the upgrade", st == 200, (st, body[:400])):
        return
    t = [x for x in ET.fromstring(body.encode()).findall("ListOfTaxInvoice/TaxInvoice") if x.findtext("RefDesc").startswith(s["submitted"])]
    if not d1.check("the old invoice is in the file", len(t) == 1, body[:400]):
        return
    lines = t[0].findall("ListOfGoodService/GoodService")
    d1.eq("the old invoice's lines carry its whole DPP (not 0)", sum(Decimal(g.findtext("TaxBase")) for g in lines), Decimal(s["dpp"]))
    vat = sum(Decimal(g.findtext("VAT")) for g in lines)
    d1.check("the old invoice's faktur PPN is its PPN (within Rp1), not 0", abs(vat - Decimal(s["ppn"])) <= 1, (vat, s["ppn"]))
    d1.check("no wages note on an invoice taxed on its whole DPP", "PMK 83" not in t[0].findtext("RefDesc"), t[0].findtext("RefDesc"))
    st, dr = api.patch("sales_invoice", s["draft"], {"notes": "setelah upgrade"})
    d1.eq("a draft from before the upgrade gets ppn_base = DPP on its next save, PPN unchanged",
          (st, dr.get("ppn_base") == dr.get("dpp"), dr.get("ppn")), (200, True, s["ppn"]))
