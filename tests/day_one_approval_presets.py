"""Day-one scenarios of the approval presets (docs/presets/approvals, rangka-apps#105), for demo-jasa and demo-fnb.

Loaded by tests/day_one.py (bind() hands over its helpers). demo-jasa installs the PO threshold (finance_manager) and
the big discount preset on sales invoices; demo-fnb installs the owner only variants of the PO, payout and bill presets.
A held document is a draft (202); the approver decides; an approved one proceeds, a rejected one stays a draft.
"""

H = {}


def bind(m):
    """Take the helpers of tests/day_one.py."""
    for k in ("ok", "fail", "gap", "check", "eq", "CTX", "Api", "approvals_of", "decide", "invoice_body", "services"):
        H[k] = getattr(m, k)
    globals().update(H)


def policies(api, model):
    st, meta = api.call("GET", "/api/v1/meta")
    return [p.get("name") for p in (((meta or {}).get("models") or {}).get(model) or {}).get("approvals", [])]


def scenario_discount_preset_jasa(api):
    sm, fm = CTX.get("sm"), CTX.get("fm")
    if not (sm and fm):
        fail("discount preset (jasa): members", CTX.get("login_error", "no sales_manager or finance_manager"))
        return
    check("meta: sales_invoice carries big_discount_approval from the preset", "big_discount_approval" in policies(api, "sales_invoice"), policies(api, "sales_invoice"))
    check("meta: purchase_order carries po_approval from the preset", "po_approval" in policies(api, "purchase_order"), policies(api, "purchase_order"))
    item = services(api)[0]["name"]
    # a fresh customer with a high credit limit, so the credit_limit warning of other scenarios does not interfere
    st, cust = fm.create("customer", {"customer_name": "Klien Preset Diskon", "credit_limit": 1_000_000_000})
    if not check("finance_manager creates a customer with a high credit limit", st == 201, (st, cust)):
        return
    customer = cust["name"]

    def draft(label, **extra):
        body = invoice_body(customer, item, rate=1_000_000, qty=10, **extra)
        if "line_discount" in extra:
            body["items"][0]["discount"] = body.pop("line_discount")
        st, inv = sm.create("sales_invoice", body)
        check(f"sales_manager drafts the invoice: {label}", st == 201, (st, inv))
        return inv.get("name")

    # exactly 20% on the line: at the limit, no request
    at = draft("a 20% line discount", line_discount=20)
    st, resp = sm.verb("sales_invoice", at, "submit")
    check("a 20% line discount submits without an approval request", st == 200 and not approvals_of(api, "sales_invoice", at), (st, resp))

    # 25% on the line: held for finance_manager, the maker cannot approve, the owner can
    big = draft("a 25% line discount", line_discount=25)
    st, resp = sm.verb("sales_invoice", big, "submit")
    held = st == 202 and resp["approval"]["state"] == "pending" and resp["approval"]["policy"] == "big_discount_approval" \
        and resp["approval"]["approvers"] == ["finance_manager"]
    check("a 25% line discount is held (202, big_discount_approval pending for finance_manager)", held, (st, resp))
    if held:
        eq("the held invoice is still a draft", sm.get("sales_invoice", big)["docstatus"], 0)
        st2, r2 = decide(sm, resp["approval"]["id"])
        check("the maker (sales_manager) cannot approve its own request -> 403", st2 == 403, (st2, r2))
        st2, r2 = decide(api, resp["approval"]["id"])
        check("the owner approves: the invoice is submitted", st2 == 200 and r2.get("record", {}).get("docstatus") == 1, (st2, r2))

    # a header discount of 30% of the subtotal: held; finance_manager rejects, it stays a draft, a smaller one goes through
    hdr = draft("a header discount of 3.000.000 on 10.000.000", discount_amount=3_000_000)
    st, resp = sm.verb("sales_invoice", hdr, "submit")
    held = st == 202 and resp["approval"]["policy"] == "big_discount_approval"
    check("a header discount above 20% is held (202)", held, (st, resp))
    if held:
        st2, r2 = decide(fm, resp["approval"]["id"], "reject", "Diskon terlalu besar")
        check("finance_manager rejects the request", st2 == 200, (st2, r2))
        eq("the rejected invoice stays a draft", sm.get("sales_invoice", hdr)["docstatus"], 0)
        st3, r3 = sm.call("PATCH", f"/api/v1/r/sales_invoice/{Api.q(hdr)}", {"discount_amount": 1_000_000})
        check("the maker lowers the discount to 10%", st3 == 200, (st3, r3))
        st3, r3 = sm.verb("sales_invoice", hdr, "submit")
        check("the lowered invoice submits without a new request", st3 == 200, (st3, r3))

    # an invoice without any discount is never held by the preset
    plain = draft("no discount")
    st, resp = sm.verb("sales_invoice", plain, "submit")
    check("an invoice without a discount submits without a request", st == 200 and not approvals_of(api, "sales_invoice", plain), (st, resp))


def scenario_spending_presets_fnb(api):
    hq = CTX.get("hq")
    if hq is None:
        fail("spending presets (fnb): head office member", CTX.get("login_error", "no head office member"))
        return
    for model, name in (("purchase_order", "po_approval"), ("payment", "payout_approval"), ("vendor_bill", "bill_without_po_approval")):
        check(f"meta: {model} carries {name} from the owner only preset", name in policies(api, model), policies(api, model))
    st, big = hq.create("purchase_order", {"vendor": "VND-0001", "items": [{"item": "BRG-0001", "qty": 100, "rate": 85_000}]})
    if not check("head office drafts a PO of 8.500.000", st == 201, (st, big)):
        return
    st, resp = hq.verb("purchase_order", big["name"], "submit")
    held = st == 202 and resp["approval"]["policy"] == "po_approval" and resp["approval"]["approvers"] == ["purchasing_manager"]
    check("the PO is held for the owner (202, po_approval, role purchasing_manager)", held, (st, resp))
    if held:
        st2, r2 = decide(hq, resp["approval"]["id"])
        check("head office (the maker) cannot approve it -> 403", st2 == 403, (st2, r2))
        st2, r2 = decide(api, resp["approval"]["id"], "reject", "Belum perlu")
        check("the owner rejects it", st2 == 200, (st2, r2))
        eq("the rejected PO stays a draft", hq.get("purchase_order", big["name"])["docstatus"], 0)
    st, small = hq.create("purchase_order", {"vendor": "VND-0001", "items": [{"item": "BRG-0001", "qty": 10, "rate": 50_000}]})
    if st == 201:
        st, resp = hq.verb("purchase_order", small["name"], "submit")
        check("a PO of 500.000 submits without a request", st == 200 and not approvals_of(api, "purchase_order", small["name"]), (st, resp))


SCENARIOS_JASA = [scenario_discount_preset_jasa]
SCENARIOS_FNB = [scenario_spending_presets_fnb]
