"""Day-one scenario: DOKU payment notifications (connector doku, trigger payment_notified) through flow
payment_from_doku, on the sites that bill with invoices (demo-jasa, and demo-fnb for a catering order of the F&B shop).

Loaded by tests/day_one.py. Events are shaped as the connector emits them from a notification the kernel has already
verified (rangka-os contract 0002, canonical-line signatures; the signed path itself is tested in rangka-os
TestSignedConnector and rangka-connectors tests/check_doku_mock.py) and go through POST /api/v1/flows/<name>/run.
Covered: a QRIS payment drafts one payment for the invoice (gateway doku, method qris), the repeat and DOKU's retries
record nothing more, FAILED and zero notifications are skipped, a VA channel is va, a cancelled or unknown invoice
records nothing and tells the owner, an overpayment leaves the rest unallocated, and six parallel notifications with
one gateway id give one payment.
"""
import threading

H = {}


def bind(m):
    for k in ("ok", "fail", "check", "eq", "installed", "invoice_body", "services"):
        H[k] = getattr(m, k)
    globals().update(H)


def scenario_doku_callbacks(api):
    if not installed(api, "gateway_ref_claim"):
        fail("doku callbacks", "payments module not installed on this site")
        return
    customers = api.list("customer")
    if not customers:
        st, c = api.create("customer", {"customer_name": "Kantor Contoh (katering)"})
        customers = [c]
    customer = customers[0]["name"]
    item = (services(api) or api.list("item"))[0]["name"]

    def submitted(rate):
        st, inv = api.create("sales_invoice", invoice_body(customer, item, rate=rate, tax_rate=0))
        st, resp = api.verb("sales_invoice", inv.get("name", ""), "submit", {"force": True})
        check(f"doku: submit invoice {rate}", st == 200, (st, resp))
        return resp.get("name", inv.get("name")) if isinstance(resp, dict) else inv.get("name")

    def notified(**event):
        event.setdefault("status", "SUCCESS" if event.get("paid") else "FAILED")
        event.setdefault("channel", "QRIS")
        event.setdefault("service", "QRIS")
        st, out = api.call("POST", "/api/v1/flows/payment_from_doku/run", {"event": event})
        return st, out or {}

    def payments_of(ref):
        return api.find("payment", f"gateway_ref == '{ref}' && docstatus != 2")

    inv = submitted(750_000)
    st, out = notified(gateway_ref="dk-0001", external_id=inv, paid_amount=750_000, paid=True, paid_at="2026-11-11T05:12:40Z")
    pays = payments_of("dk-0001")
    check("DOKU QRIS paid: one draft payment for the invoice, gateway doku, method qris, the whole amount allocated",
          st == 200 and len(pays) == 1 and pays[0]["docstatus"] == 0 and pays[0]["gateway"] == "doku" and pays[0]["method"] == "qris"
          and pays[0]["reference"] == inv and pays[0]["allocated_total"] == 750_000, (st, out, pays))
    check("DOKU QRIS paid: the gateway id is claimed", len(api.find("gateway_ref_claim", "gateway_ref == 'dk-0001'")) == 1, "")
    for _ in range(3):   # DOKU retries at +30 min, +6 h, +12 h
        notified(gateway_ref="dk-0001", external_id=inv, paid_amount=750_000, paid=True)
    eq("the same notification three more times: still one payment", len(payments_of("dk-0001")), 1)
    if pays:
        st, resp = api.verb("payment", pays[0]["name"], "submit")
        check("finance submits the drafted DOKU payment", st == 200, (st, resp))
        eq("the invoice is settled", api.get("sales_invoice", inv)["outstanding"], 0)
        notified(gateway_ref="dk-0001", external_id=inv, paid_amount=750_000, paid=True)
        eq("a notification after submit: still one payment", len(payments_of("dk-0001")), 1)

    inv2 = submitted(400_000)
    st, out = notified(gateway_ref="dk-0002", external_id=inv2, paid_amount=400_000, paid=False)
    check("FAILED notification: skipped, no payment", out.get("skipped") is True and not payments_of("dk-0002"), out)
    told = lambda out: any("tanpa ID transaksi atau jumlah" in n.get("text", "") for n in out.get("notifications", []))
    st, out = notified(gateway_ref="dk-0003", external_id=inv2, paid_amount=0, paid=True)
    check("a SUCCESS of 0: no payment, the owner is told (not dropped silently)", st == 200 and not payments_of("dk-0003") and told(out), (st, out))
    st, out = notified(external_id=inv2, paid_amount=400_000, paid=True)
    check("a paid notification without gateway_ref: no payment, the owner is told", st == 200 and told(out), (st, out))
    before = len(api.find("payment", "gateway == 'doku'"))
    st, out = notified(gateway_ref="", external_id=inv2, paid_amount=400_000, paid=True)
    check("a paid notification with an empty gateway_ref: no payment, the owner is told", st == 200 and told(out)
          and not out.get("created") and len(api.find("payment", "gateway == 'doku'")) == before, (st, out))
    st, out = notified(gateway_ref="dk-0003b", external_id=inv2, paid_amount="abc", paid=True)
    check("an unreadable amount: no payment, the owner is told", st == 200 and told(out) and not payments_of("dk-0003b"), (st, out))
    st, out = notified(gateway_ref="dk-refund-0001", external_id=inv, paid_amount=750_000, paid=False, status="SUCCESS", type="REFUND")
    check("a REFUND notification (paid false from the connector): skipped, no payment", out.get("skipped") is True and not payments_of("dk-refund-0001"), out)
    inv2b = submitted(120_000)
    st, out = notified(gateway_ref="dk-str-0009", external_id=inv2b, paid_amount="120000", paid=True)
    p9 = payments_of("dk-str-0009")
    check("an amount sent as a string of digits is read", len(p9) == 1 and p9[0]["amount"] == 120_000 and p9[0]["allocated_total"] == 120_000, (st, out, p9))
    st, out = notified(gateway_ref="dk-0004", external_id=inv2, paid_amount=500_000, paid=True, channel="VIRTUAL_ACCOUNT_BCA", service="VIRTUAL_ACCOUNT")
    p4 = payments_of("dk-0004")
    check("VA channel: method va; paid 500.000 on 400.000 open, 100.000 unallocated",
          len(p4) == 1 and p4[0]["method"] == "va" and p4[0]["allocated_total"] == 400_000 and p4[0]["unallocated"] == 100_000, (out, p4))
    st, out = notified(gateway_ref="dk-0005", external_id=inv2, paid_amount=50_000, paid=True, channel="CREDIT_CARD", service="CREDIT_CARD")
    p5 = payments_of("dk-0005")
    check("another channel is other, nothing allocated beyond what is open", len(p5) == 1 and p5[0]["method"] == "other" and p5[0]["allocated_total"] == 0, p5)

    inv3 = submitted(300_000)
    api.verb("sales_invoice", inv3, "cancel")
    st, out = notified(gateway_ref="dk-0006", external_id=inv3, paid_amount=300_000, paid=True)
    check("paid for a cancelled invoice: no payment, the owner is told",
          not payments_of("dk-0006") and any("dk-0006" in n.get("text", "") for n in out.get("notifications", [])), out)
    st, out = notified(gateway_ref="dk-0007", external_id="INV-TIDAK-ADA", paid_amount=99_000, paid=True)
    check("paid for an unknown invoice number: no payment, the owner is told",
          not payments_of("dk-0007") and any("INV-TIDAK-ADA" in n.get("text", "") for n in out.get("notifications", [])), out)

    inv4 = submitted(640_000)
    results = []

    def fire():
        results.append(notified(gateway_ref="dk-race-0008", external_id=inv4, paid_amount=640_000, paid=True)[0])
    threads = [threading.Thread(target=fire) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    eq("six parallel notifications with one gateway id: exactly one payment", len(payments_of("dk-race-0008")), 1)
    eq("six parallel notifications: one claim, naming that payment",
       [c["payment"] for c in api.find("gateway_ref_claim", "gateway_ref == 'dk-race-0008'")], [p["name"] for p in payments_of("dk-race-0008")])


SCENARIOS = [scenario_doku_callbacks]
