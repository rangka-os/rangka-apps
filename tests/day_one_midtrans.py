"""Day-one scenario: Midtrans payment notifications (connector midtrans, trigger payment_notified) through the flows
payment_from_midtrans and payment_refund_from_midtrans, on the sites that bill with invoices (demo-jasa, and demo-fnb
for a catering order of the F&B shop).

Loaded by tests/day_one.py. Events are shaped as the connector emits them from a notification the kernel has already
verified (rangka-os contract 0002, body-field signatures; the signed path itself is tested in rangka-os
TestBodySignedWebhook and rangka-connectors tests/check_midtrans_mock.py) and go through POST /api/v1/flows/<name>/run.
Midtrans signs the order id, the status code and the gross amount only, so gateway_ref is the order id, external_id the
invoice number in it and paid_amount the gross amount as Midtrans writes it, a text with two decimals.
Covered: a QRIS payment drafts one payment for the invoice (gateway midtrans, method qris), the repeat, Midtrans's
retries and a resend with other unsigned fields record nothing more, a second attempt (<invoice>~2) pays the same
invoice under its own id, pending, expire and zero notifications are skipped or told, bank_transfer and echannel are
va, an e-wallet is ewallet, a cancelled or unknown invoice records nothing and tells the owner, an overpayment leaves
the rest unallocated, six parallel notifications with one order id give one payment, and a refund tells the owner.
"""
import threading

H = {}


def bind(m):
    for k in ("ok", "fail", "check", "eq", "installed", "invoice_body", "services"):
        H[k] = getattr(m, k)
    globals().update(H)


def scenario_midtrans_callbacks(api):
    if not installed(api, "gateway_ref_claim"):
        fail("midtrans callbacks", "payments module not installed on this site")
        return
    customers = api.list("customer")
    if not customers:
        st, c = api.create("customer", {"customer_name": "Kantor Contoh (katering)"})
        customers = [c]
    customer = customers[0]["name"]
    item = (services(api) or api.list("item"))[0]["name"]

    def submitted(rate):
        st, inv = api.create("sales_invoice", invoice_body(customer, item, rate=rate, tax_rate=0, tax_rate_reason="not_taxable"))
        st, resp = api.verb("sales_invoice", inv.get("name", ""), "submit", {"force": True})
        check(f"midtrans: submit invoice {rate}", st == 200, (st, resp))
        return resp.get("name", inv.get("name")) if isinstance(resp, dict) else inv.get("name")

    def notified(**event):
        paid = event.get("paid")
        event.setdefault("status", "settlement" if paid else "pending")
        event.setdefault("status_code", "200" if paid else "201")
        event.setdefault("refunded", False)
        event.setdefault("channel", "qris")
        event.setdefault("transaction_id", "tx-" + str(event.get("gateway_ref", "")))
        event.setdefault("refund_amount", None)
        if "gateway_ref" in event:
            event.setdefault("external_id", event["gateway_ref"])
        st, out = api.call("POST", "/api/v1/flows/payment_from_midtrans/run", {"event": event})
        return st, out or {}

    def payments_of(ref):
        return api.find("payment", f"gateway_ref == '{ref}' && docstatus != 2")

    inv = submitted(750_000)
    st, out = notified(gateway_ref=inv, paid_amount="750000.00", paid=True, paid_at="2026-11-19 10:12:40")
    pays = payments_of(inv)
    check("Midtrans QRIS paid: one draft payment for the invoice, gateway midtrans, method qris, the whole amount allocated",
          st == 200 and len(pays) == 1 and pays[0]["docstatus"] == 0 and pays[0]["gateway"] == "midtrans" and pays[0]["method"] == "qris"
          and pays[0]["reference"] == inv and pays[0]["amount"] == 750_000 and pays[0]["allocated_total"] == 750_000, (st, out, pays))
    check("Midtrans QRIS paid: the order id is claimed", len(api.find("gateway_ref_claim", f"gateway_ref == '{inv}'")) == 1, "")
    for _ in range(5):   # Midtrans retries after 2, 10, 30, 90 and 210 minutes
        notified(gateway_ref=inv, paid_amount="750000.00", paid=True, paid_at="2026-11-19 10:12:40")
    eq("the same notification five more times: still one payment", len(payments_of(inv)), 1)
    # the unsigned fields of a genuine notification changed and resent: the signed order id is the key, nothing new
    notified(gateway_ref=inv, paid_amount="750000.00", paid=True, transaction_id="tx-lain", status="capture", channel="credit_card", paid_at="2026-12-01 00:00:00")
    eq("a resend with another transaction id, status text and channel: still one payment", len(payments_of(inv)), 1)
    if pays:
        st, resp = api.verb("payment", pays[0]["name"], "submit")
        check("finance submits the drafted Midtrans payment", st == 200, (st, resp))
        eq("the invoice is settled", api.get("sales_invoice", inv)["outstanding"], 0)
        notified(gateway_ref=inv, paid_amount="750000.00", paid=True)
        eq("a notification after submit: still one payment", len(payments_of(inv)), 1)

    inv2 = submitted(400_000)
    st, out = notified(gateway_ref=inv2, paid_amount="400000.00", paid=False)
    check("pending notification: skipped, no payment", out.get("skipped") is True and not payments_of(inv2), out)
    st, out = notified(gateway_ref=inv2, paid_amount="400000.00", paid=False, status="expire", status_code="407")
    check("expire notification: skipped, no payment", out.get("skipped") is True and not payments_of(inv2), out)
    st, out = notified(gateway_ref=inv2, paid_amount="400000.00", paid=None, status="settlement", status_code="200")
    check("a notification whose paid could not be worked out (null): skipped, no payment", out.get("skipped") is True and not payments_of(inv2), out)
    # the first page expired: the second attempt is its own Midtrans transaction, <invoice>~2, for the same invoice
    st, out = notified(gateway_ref=inv2 + "~2", external_id=inv2, paid_amount="400000.00", paid=True, channel="bank_transfer")
    p2 = payments_of(inv2 + "~2")
    check("second attempt (<invoice>~2): one payment for the invoice, method va, keyed on the attempt's order id",
          st == 200 and len(p2) == 1 and p2[0]["reference"] == inv2 and p2[0]["method"] == "va" and p2[0]["allocated_total"] == 400_000
          and not payments_of(inv2), (st, out, p2))

    told = lambda out: any("tanpa order id atau jumlah" in n.get("text", "") for n in out.get("notifications", []))
    inv3 = submitted(120_000)
    st, out = notified(gateway_ref=inv3, paid_amount="0.00", paid=True)
    check("a settlement of 0.00: no payment, the owner is told (not dropped silently)", st == 200 and not payments_of(inv3) and told(out), (st, out))
    st, out = notified(external_id=inv3, paid_amount="120000.00", paid=True)
    check("a paid notification without gateway_ref: no payment, the owner is told", st == 200 and told(out), (st, out))
    before = len(api.find("payment", "gateway == 'midtrans'"))
    st, out = notified(gateway_ref="", external_id=inv3, paid_amount="120000.00", paid=True)
    check("a paid notification with an empty gateway_ref: no payment, the owner is told", st == 200 and told(out)
          and not out.get("created") and len(api.find("payment", "gateway == 'midtrans'")) == before, (st, out))
    # one flow run per order id (the flow's idempotency key): each case below is its own attempt of the invoice
    st, out = notified(gateway_ref=inv3 + "~2", external_id=inv3, paid_amount="abc", paid=True)
    check("an unreadable amount: no payment, the owner is told", st == 200 and told(out) and not payments_of(inv3 + "~2"), (st, out))
    st, out = notified(gateway_ref=inv3 + "~3", external_id=inv3, paid_amount="-120000.00", paid=True)
    check("a negative amount: no payment, the owner is told", st == 200 and told(out) and not payments_of(inv3 + "~3"), (st, out))
    st, out = notified(gateway_ref=inv3 + "~4", external_id=inv3, paid_amount="120000.00", paid=False, refunded=True, status="refund", refund_amount="120000.00")
    check("a refund notification (paid false from the connector): skipped by the payment flow", out.get("skipped") is True and not payments_of(inv3 + "~4"), out)
    st, out = notified(gateway_ref=inv3 + "~5", external_id=inv3, paid_amount="120000.00", paid=True, channel="echannel")
    p3 = payments_of(inv3 + "~5")
    check("the amount text with two decimals is read as rupiah; echannel (Mandiri bill) is va",
          len(p3) == 1 and p3[0]["amount"] == 120_000 and p3[0]["allocated_total"] == 120_000 and p3[0]["method"] == "va" and p3[0]["reference"] == inv3, (st, out, p3))
    st, out = notified(gateway_ref=inv3, paid_amount="120000.00", paid=True)
    check("an order id whose notification was already handled (the 0.00 one) does not run the flow again", out.get("skipped") is True and not payments_of(inv3), out)

    inv4 = submitted(300_000)
    st, out = notified(gateway_ref=inv4, paid_amount="500000.00", paid=True, channel="gopay")
    p4 = payments_of(inv4)
    check("GoPay: method ewallet; paid 500.000 on 300.000 open, 200.000 unallocated",
          len(p4) == 1 and p4[0]["method"] == "ewallet" and p4[0]["allocated_total"] == 300_000 and p4[0]["unallocated"] == 200_000, (out, p4))
    st, out = notified(gateway_ref=inv4 + "~2", external_id=inv4, paid_amount="50000.00", paid=True, channel="credit_card", status="capture")
    p5 = payments_of(inv4 + "~2")
    check("another payment type is other; nothing allocated beyond what is open after the draft", len(p5) == 1 and p5[0]["method"] == "other" and p5[0]["allocated_total"] == 0, p5)

    inv5 = submitted(310_000)
    api.verb("sales_invoice", inv5, "cancel")
    st, out = notified(gateway_ref=inv5, paid_amount="310000.00", paid=True)
    check("paid for a cancelled invoice: no payment, the owner is told",
          not payments_of(inv5) and any(inv5 in n.get("text", "") and "faktur tidak ada" in n.get("text", "") for n in out.get("notifications", [])), out)
    st, out = notified(gateway_ref="INV-TIDAK-ADA", paid_amount="99000.00", paid=True)
    check("paid for an unknown invoice number: no payment, the owner is told",
          not payments_of("INV-TIDAK-ADA") and any("INV-TIDAK-ADA" in n.get("text", "") for n in out.get("notifications", [])), out)
    st, out = notified(gateway_ref=inv5 + "~10", paid_amount="310000.00", paid=True)
    check("an attempt beyond 9 keeps its ending, so no invoice matches: no payment, the owner is told",
          not payments_of(inv5 + "~10") and any("faktur tidak ada" in n.get("text", "") for n in out.get("notifications", [])), out)

    inv6 = submitted(640_000)
    results = []

    def fire():
        results.append(notified(gateway_ref=inv6, paid_amount="640000.00", paid=True)[0])
    threads = [threading.Thread(target=fire) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    eq("six parallel notifications with one order id: exactly one payment", len(payments_of(inv6)), 1)
    eq("six parallel notifications: one claim, naming that payment",
       [c["payment"] for c in api.find("gateway_ref_claim", f"gateway_ref == '{inv6}'")], [p["name"] for p in payments_of(inv6)])

    # refunds tell the owner and book nothing
    def refund(**event):
        event.setdefault("external_id", event.get("gateway_ref"))
        st, out = api.call("POST", "/api/v1/flows/payment_refund_from_midtrans/run", {"event": event})
        return st, out or {}

    count = len(api.find("payment", "gateway == 'midtrans'"))
    st, out = refund(gateway_ref=inv6, paid_amount="640000.00", paid=False, refunded=True, status="partial_refund", status_code="200", refund_amount="140000.00")
    check("partial refund: the owner is told the invoice, the order id and the amount the notification names",
          st == 200 and any(inv6 in n.get("text", "") and "140000.00" in n.get("text", "") and "dashboard Midtrans" in n.get("text", "") for n in out.get("notifications", [])), (st, out))
    st, out = refund(gateway_ref=inv6, paid_amount="640000.00", paid=False, refunded=True, status="refund", status_code="200", refund_amount=None)
    check("refund without an amount: the owner is sent to the dashboard for it",
          st == 200 and any(inv6 in n.get("text", "") and "Cek jumlahnya" in n.get("text", "") for n in out.get("notifications", [])), (st, out))
    st, out = refund(gateway_ref=inv6, paid_amount="640000.00", paid=True, refunded=False, status="settlement", status_code="200")
    check("a settlement is not a refund: the refund flow skips it", out.get("skipped") is True, out)
    st, out = refund(gateway_ref="", paid_amount="640000.00", paid=False, refunded=True, status="refund", status_code="200")
    check("a refund without an order id: skipped", out.get("skipped") is True, out)
    eq("refund notifications record no payment", len(api.find("payment", "gateway == 'midtrans'")), count)


SCENARIOS = [scenario_midtrans_callbacks]
