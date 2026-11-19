"""Day-one scenario: "Tautan tagihan", the customer link of rangka-os contract 0073 (module invoicing, page
portal/tagihan.rk), on demo-jasa.

Loaded by tests/day_one.py. The owner submits an invoice, makes a customer link, and a browser without credentials
opens it: the page shows the invoice, what is left to pay and the transfer instructions (rekening, payment reference,
static QRIS) copied onto the invoice when it was submitted, and nothing the page does not list. A changed token is
404, an expired or revoked link is 410 with "Tautan sudah kedaluwarsa". The kernel side is tested in rangka-os
TestPortalLinkFlow; this checks the invoicing declaration against the real invoice model.
"""
import json
import urllib.error
import urllib.request

H = {}


def bind(m):
    for k in ("ok", "fail", "check", "eq", "installed", "invoice_body", "services", "sql", "qris_payload", "CTX"):
        H[k] = getattr(m, k)
    globals().update(H)


def customer_get(base, path):
    """GET as a customer: no cookie, no credentials. Returns (status, json or None, text, headers)."""
    req = urllib.request.Request(base + path)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", "replace")
            status, headers = r.status, r.headers
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        status, headers = e.code, e.headers
    try:
        body = json.loads(raw)
    except ValueError:
        body = None
    return status, body, raw, headers


def scenario_portal_invoice_link(api):
    if not installed(api, "payment_instruction"):
        fail("portal: invoice link", "invoicing payment_instruction not installed on this site")
        return
    st, pages = api.call("GET", "/api/v1/portal")
    check("portal: the owner may share the invoicing page tagihan", st == 200 and any(p.get("name") == "tagihan" for p in pages.get("data", [])), (st, pages))

    # a default rekening with a static QRIS, so a submitted invoice gets its payment instruction
    accounts = api.find("bank_account", "is_default == true && active == true")
    if not accounts:
        st, acct = api.create("bank_account", {"bank": "BCA", "account_no": "1230000064", "holder": "PT Contoh Jasa Teknik", "is_default": True,
                                               "qris_payload": qris_payload("PT CONTOH JASA", "ID1020000000264"),
                                               "instructions": "Tulis nomor faktur di berita transfer."})
        check("portal: default rekening with static QRIS", st == 201, (st, acct))
        accounts = [acct] if st == 201 else []
    customer = api.list("customer")[0]
    item = (services(api) or api.list("item"))[0]["name"]
    st, inv = api.create("sales_invoice", invoice_body(customer["name"], item, rate=2_500_000, tax_rate=0, tax_rate_reason="not_taxable",
                                                       notes="Terima kasih atas kepercayaannya."))
    name = inv.get("name", "")
    st, resp = api.call("POST", "/api/v1/portal/tagihan/links", {"record": name})
    check("portal: a draft invoice is not shared (409 portal_not_submitted)",
          st == 409 and (resp or {}).get("error", {}).get("code") == "portal_not_submitted", (st, resp))
    st, resp = api.verb("sales_invoice", name, "submit", {"force": True})
    check("portal: submit the invoice", st == 200, (st, resp))
    inv = api.get("sales_invoice", name)

    # the owner shares a link
    st, link = api.call("POST", "/api/v1/portal/tagihan/links", {"record": name})
    check("portal: the owner makes a customer link (201, /p/<org>/tagihan/<token>)",
          st == 201 and "/p/" in link.get("url", "") and "/tagihan/" in link.get("path", ""), (st, link))
    if st != 201:
        return
    base, path = api.base, link["path"]
    token = path.rsplit("/", 1)[-1]

    # the customer opens it, without signing in
    st, _, html, headers = customer_get(base, path)
    check("portal: the link opens without login (200 page, no-referrer, no-store)",
          st == 200 and "/portal/portal.js" in html and headers.get("Referrer-Policy") == "no-referrer" and headers.get("Cache-Control") == "no-store",
          (st, dict(headers)))
    st, d, raw, _ = customer_get(base, path + "/data")
    check("portal: the page data loads", st == 200 and isinstance(d, dict), (st, raw[:300]))
    if not isinstance(d, dict):
        return
    doc = d.get("doc", {})
    eq("portal: the page names the invoice", doc.get("name"), name)
    eq("portal: the invoice is open", doc.get("state"), "open")
    eq("portal: the headline amount is the invoice's outstanding", (doc.get("amount") or {}).get("value"), inv["outstanding"])
    check("portal: the customer's name is shown, not its code", doc.get("customer") and doc.get("customer") != customer["name"], doc)
    eq("portal: the due date is the invoice's", (doc.get("due") or {}).get("value"), inv.get("due_date"))
    pay = d.get("payment") or {}
    got = {f["name"]: f["value"] for f in pay.get("fields", [])}
    acct = accounts[0] if accounts else {}
    check("portal: transfer instructions: bank, rekening, holder and the invoice number as reference",
          got.get("account_no") == acct.get("account_no") and got.get("bank") == acct.get("bank") and got.get("reference") == name and got.get("holder"), got)
    check("portal: the static QRIS is shown as an image", str((pay.get("qris") or {}).get("image", "")).startswith("data:image/png;base64,"), pay.get("qris"))
    lines = (d.get("lines") or {}).get("rows", [])
    check("portal: the invoice lines are listed", len(lines) == len(inv.get("items", [])) and lines and "amount" in lines[0], d.get("lines"))
    shown = {f["name"] for f in d.get("fields", [])}
    check("portal: only the declared fields reach the customer",
          shown <= {"posting_date", "due_date", "po_number", "grand_total", "paid_amount", "outstanding", "notes"}
          and not any(k in raw for k in ("salesman", "pph23_rate", "coretax", "buyer_tin", "credit_warned", token)), (shown, raw[:400]))
    st, _, ptxt, _ = customer_get(base, path + "/print")
    check("portal: the full invoice opens as a printable page", st == 200 and name in ptxt, (st, ptxt[:200]))

    # misses
    other = token[:-1] + ("B" if token[-1] == "A" else "A")
    st, d2, _, _ = customer_get(base, path.rsplit("/", 1)[0] + "/" + other + "/data")
    check("portal: one character changed in the token is 404", st == 404 and (d2 or {}).get("error", {}).get("code") == "portal_link_unknown", (st, d2))
    out = sql(f"update rk_portal_links set expires_at = now() - interval '1 minute' where id = '{link['id']}'")
    check("portal: (setup) the link's 30 days pass", out.startswith("UPDATE 1"), out)
    st, d3, _, _ = customer_get(base, path + "/data")
    check("portal: an expired link says 'Tautan sudah kedaluwarsa, minta tautan baru' (410)",
          st == 410 and "kedaluwarsa" in (d3 or {}).get("error", {}).get("message", ""), (st, d3))
    st, link2 = api.call("POST", "/api/v1/portal/tagihan/links", {"record": name, "days": 7})
    check("portal: a new link for 7 days", st == 201 and link2.get("expires_at"), (st, link2))
    st, resp = api.call("POST", "/api/v1/portal/tagihan/links/revoke", {"record": name, "id": link2.get("id")})
    check("portal: the owner revokes the new link", st == 200 and resp.get("revoked") == 1, (st, resp))
    st, _, _, _ = customer_get(base, link2.get("path", "") + "/data")
    eq("portal: a revoked link is 410 like an expired one", st, 410)
    st, links = api.call("GET", f"/api/v1/portal/tagihan/links?record={api.q(name)}")
    rows = (links or {}).get("data", [])
    check("portal: the owner sees both links, their views, no token",
          len(rows) == 2 and any(r.get("views", 0) >= 2 for r in rows) and token not in json.dumps(rows), rows)
    st, audit = api.call("GET", "/api/v1/system/audit?kind=portal.")
    entries = (audit or {}).get("data", audit if isinstance(audit, list) else [])
    check("portal: views and link changes are audited without the token",
          any(e.get("kind") == "portal.view" for e in entries) and any(e.get("kind") == "portal.link.revoke" for e in entries)
          and token not in json.dumps(entries), (st, str(audit)[:300]))


SCENARIOS = [scenario_portal_invoice_link]
