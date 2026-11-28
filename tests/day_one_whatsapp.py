"""Day-one scenario: the WhatsApp service from Rangka Cloud (modules whatsapp, whatsapp_receipt, whatsapp_reminder,
whatsapp_order_ready, whatsapp_booking; host connector rangka_whatsapp) on site demo-whatsapp.

Loaded by tests/day_one.py, which starts the kernel with RANGKA_HOST_CONNECTORS=rangka_whatsapp and a throwaway
RANGKA_MASTER_KEY (SITE_ENV below). Nothing is sent anywhere: the connector's host is a placeholder, so the kernel's
queued calls stay in its job queue, where the scenarios read what would have gone to Rangka Cloud. Rangka Cloud's
side (opt-out list, kill switch, approvals, sending once) is tested in rangka-cloud; the relay's events reach the
kernel here the way Rangka Cloud sends them: signed with the business's own webhook key, which the host writes with a
secrets:host token.

Covered: the host connector is installable only with RANGKA_HOST_CONNECTORS, and its keys only by the host (the owner
gets 409); a paid sale with the buyer's yes queues one receipt and a replay queues nothing more; no tick or a bad
number sends nothing; a customer marked "Tidak mau dikirimi WA" goes on the list, Rangka Cloud is told, and the next
message to that number is skipped; the relay's signed events move a message forward only and a forged one is refused;
a customer's BERHENTI marks the customer; the kitchen's Siap sends one order-ready message; the morning reminder sends
one message per overdue step with the bank account and an Indonesian date, records it on the invoice, and skips a
customer without a number; tomorrow's rental booking gets one reminder.
"""
import datetime
import hashlib
import hmac
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

from day_one_kitchen import to_status  # kitchen 2.0: staff move lines, the ticket's status follows (rangka-apps#131)

H = {}
SITE = "demo-whatsapp"
ORG = "warung_contoh"
RIU = "OUT-03"
KOPI, GELAS, ROTI, KHT, PULSA = "BRG-0011", "BRG-0014", "BRG-0015", "BRG-0022", "BRG-0004"
DEWI, BUDI, CITRA = "081200000001", "081200000002", "081200000003"   # synthetic numbers only
# One phone rule in the kernel's CEL and in Rangka Cloud's NormalizePhone (rangka-cloud TestPhoneRuleVectors): without
# a plus, 0 or 8 first is Indonesian and 00 starts an international number; with a plus the country code is as
# written; a trunk 0 after 62 is dropped.
PHONE_VECTORS = {
    "081234567890": "6281234567890", "812-3456-7890": "6281234567890", "8123 4567 890": "6281234567890",
    "+62 0812 3456 7890": "6281234567890", "620812 3456 7890": "6281234567890", "62 812 3456 7890": "6281234567890",
    "0062 812 3456 7890": "6281234567890", "+81 3 1234 5678": "81312345678", "0081 3 1234 5678": "81312345678",
    "+1 415 555 0100": "14155550100", "(021) 555-0101": "62215550101", "0812": "", "08x2": "",
}

MEMBERS = [("sari", "cashier", "sari.wa@contohwarung.test", "Sari Contoh", RIU),
           ("tono", "kitchen", "tono.wa@contohwarung.test", "Tono Contoh", RIU),
           ("hq", "head_office", "rina.wa@contohwarung.test", "Rina Contoh", "")]


def site_env(m):
    """The kernel of this site serves the host connector and keeps connector values in the database."""
    os_dir, binary, _ = m.lib_env()
    key = subprocess.run([binary, "keygen"], capture_output=True, text=True).stdout.strip()
    return {"RANGKA_HOST_CONNECTORS": "rangka_whatsapp", "RANGKA_MASTER_KEY": key}


def bind(m):
    for k in ("ok", "fail", "check", "eq", "CTX", "Api", "kernel_today", "invoice_body", "secrets"):
        H[k] = getattr(m, k)
    globals().update(H)
    m.SITE_ORGS[SITE] = [(ORG, "demo-kasir")]
    m.SITE_ENV[SITE] = lambda: site_env(m)
    m.SITE_SETUP[SITE] = setup


def rec(x):
    return (x or {}).get("record", x) or {}


def setup(api, k):
    pw = secrets.token_urlsafe(18)   # throwaway password of a throwaway database
    CTX.update(kernel=k, org=ORG)
    for key, role, email, name, outlets in MEMBERS:
        code, out = k.user_add(email, role, ORG, name, outlets=outlets, password=pw)
        cl = Api.login(k.base, email, pw) if code == 0 else None
        if cl is None:
            fail("whatsapp setup: add and sign in the members", f"{email}: " + (out if code else "login refused"))
            return
        CTX[key] = cl
    CTX["owner"] = Api.login(k.base, k.owner_email, k.owner_pw)


# ---------- helpers

def messages(api, where="true"):
    return api.find("whatsapp_message", where)


def message(api, key):
    ms = messages(api, f"key == '{key}'")
    return ms[0] if len(ms) == 1 else ({"_count": len(ms)} if ms else None)


def calls(api, action="send_template"):
    """The kernel's queued calls to rangka_whatsapp.<action>, newest first: what would have gone to Rangka Cloud."""
    st, resp = api.call("GET", "/api/v1/system/jobs?kind=call&limit=500")
    rows = (resp or {}).get("data", []) if st == 200 else []
    out = []
    for j in rows:
        args = j.get("args") or j.get("payload") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        if args.get("connector") == "rangka_whatsapp" and args.get("action") == action:
            out.append(args.get("input") or {})
    return out


def calls_for(api, key):
    return [c for c in calls(api) if c.get("idempotency_key") == key]


def params(call):
    comps = call.get("components") or []
    if isinstance(comps, str):
        comps = json.loads(comps)
    return [p.get("text") for p in (comps[0].get("parameters") if comps else [])]


def hook(k, trigger, event, key):
    """POST one event to the kernel's hook route the way Rangka Cloud forwards it: signed over the raw body."""
    body = json.dumps(event, separators=(",", ":")).encode()
    sig = "sha256=" + hmac.new(key.encode(), body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(f"{k.base}/hooks/{ORG}/rangka_whatsapp/{trigger}", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "X-Rangka-Signature": sig})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def wait_for(fn, timeout=15):
    """Hook events run their flows on the kernel's queue: wait until fn() is truthy."""
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.3)
    return fn()


def pay(cl, shift, lines, **extra):
    st, s = cl.create("pos_sale", dict({"shift": shift, "items": lines}, **extra))
    if st != 201:
        return st, s
    st, r = cl.verb("pos_sale", rec(s)["name"], "submit")
    return st, (rec(r) if st == 200 else r)


# ---------- scenarios

def scenario_wa_host_connector(api):
    """rangka_whatsapp is a host connector this kernel serves; the owner cannot set its keys, the host can."""
    st, cat = api.call("GET", "/api/v1/catalogue")
    conns = {c.get("name"): c for c in (cat or {}).get("connectors", [])} if st == 200 else {}
    c = conns.get("rangka_whatsapp") or {}
    check("catalogue: rangka_whatsapp is provided by the host and installed", c.get("provided_by") == "host" and c.get("state") != "host_unavailable", c)
    owner = CTX.get("owner")
    if not owner:
        fail("whatsapp: owner signs in", "login refused")
        return
    st, r = owner.call("PUT", "/api/v1/connectors/rangka_whatsapp/auth/service_token", {"value": "dummy-test-token"})
    check("the owner cannot set the host connector's token -> 409 secret_host_provided",
          st == 409 and (r or {}).get("error", {}).get("code") == "secret_host_provided", (st, r))
    st, tok = owner.call("POST", "/api/v1/tokens", {"name": "rangka-cloud", "scopes": ["secrets:host"]})
    if not check("the owner mints a secrets:host token (as Rangka Cloud does at provisioning) -> 201", st == 201 and (tok or {}).get("token"), (st, tok)):
        return
    host = Api(api.base)
    host.headers = lambda extra=None, t=tok["token"]: dict(extra or {}, Authorization="Bearer " + t)
    CTX["wa_webhook_key"] = "test-" + secrets.token_hex(24)   # the business's own key (Rangka Cloud derives it)
    for field, value in (("api_host", "cloud.rangka.example"), ("service_token", "dummy-" + secrets.token_hex(16)), ("webhook_key", CTX["wa_webhook_key"])):
        st, r = host.call("PUT", f"/api/v1/connectors/rangka_whatsapp/auth/{field}", {"value": value})
        check(f"the host writes {field} -> 2xx", st in (200, 201, 204), (st, r))
    st, a = owner.call("GET", "/api/v1/connectors/rangka_whatsapp/auth")
    fields = {f.get("field"): f for f in (a or {}).get("fields", [])} if st == 200 else {}
    check("every field is set by the host (source host), no hint of the value",
          fields and all(f.get("set") and f.get("source") == "host" and not f.get("hint") for f in fields.values()), (st, a))


def scenario_wa_receipt(api):
    """Sari rings up a sale at Kedai Riau; the buyer gives a number and says yes: one receipt, whatever runs again."""
    hq, sari = CTX.get("hq"), CTX.get("sari")
    if not (hq and sari):
        return
    st, c = hq.create("stock_count", {"outlet": RIU, "items": [{"item": KOPI, "counted_qty": 2}, {"item": GELAS, "counted_qty": 100}, {"item": ROTI, "counted_qty": 20}]})
    if check("opening count at Kedai Riau -> 201", st == 201, (st, c)):
        hq.verb("stock_count", rec(c)["name"], "submit")
    st, s = sari.create("pos_shift", {"outlet": RIU, "opening_cash": 100_000})
    if not check("Sari opens a shift -> 201", st == 201, (st, s)):
        return
    shift = rec(s)["name"]
    CTX["wa_shift"] = shift
    lines = [{"item": KHT, "qty": 2}, {"item": ROTI, "qty": 1}]

    st, r = pay(sari, shift, lines, buyer="Dewi", wa_phone="0812", wa_receipt=True)
    check("a tick with a number that is not one -> 422 wa_receipt_number",
          st == 422 and any(e.get("rule") == "wa_receipt_number" for e in (r or {}).get("errors", [])), (st, r))

    st, sale = pay(sari, shift, lines, buyer="Dewi", wa_phone="0812-0000-0001", wa_receipt=True,
                   payments=[{"method": "qris", "amount": 45_000}])
    if not check("Dewi's sale paid by QRIS -> 200", st == 200, (st, sale)):
        return
    CTX["wa_sale"] = sale
    key = sale["name"] + ":receipt:1"
    m = message(api, key)
    check("one message row for the receipt: queued, struk_pembayaran, last four digits only, Rp 45.000",
          m and m.get("status") == "queued" and m.get("template") == "struk_pembayaran" and m.get("message_type") == "receipt"
          and m.get("number_tail") == "…0001" and m.get("amount_text") == "Rp 45.000" and "0812" not in json.dumps(m), m)
    cs = calls_for(api, key)
    if check("one call queued to Rangka Cloud for that key", len(cs) == 1, cs):
        p = params(cs[0])
        check("the call: +62 number, type receipt, the six parameters",
              cs[0].get("to") == "+6281200000001" and cs[0].get("type") == "receipt" and cs[0].get("template") == "struk_pembayaran"
              and p == ["Dewi", sale["name"], "Kedai Contoh Riau", "2 jenis barang", "Rp 45.000", "QRIS"], (cs[0], p))
    st, out = api.call("POST", "/api/v1/flows/whatsapp_receipt_on_sale/run", {"event": sale})
    check("the same sale's event again: nothing more queued, still one row",
          len(calls_for(api, key)) == 1 and (message(api, key) or {}).get("_count") is None, (st, out))

    st, plain = pay(sari, shift, [{"item": ROTI, "qty": 1}], buyer="Meja 2", wa_phone="081200000009")
    check("a sale without the tick sends nothing", st == 200 and not messages(api, f"document == '{plain.get('name')}'"), (st, plain))
    CTX["wa_plain_sale"] = plain


def scenario_wa_opt_out(api):
    """Staff mark Budi "Tidak mau dikirimi WA": the number goes on the list and to Rangka Cloud; then nothing goes to it."""
    hq, sari = CTX.get("hq"), CTX.get("sari")
    if not (hq and sari and CTX.get("wa_shift")):
        return
    st, c = hq.create("customer", {"customer_name": "Budi Contoh", "phone": "+62 812-0000-0002"})
    if not check("customer Budi -> 201", st == 201, (st, c)):
        return
    budi = rec(c)
    check("Budi's WhatsApp number in one form", budi.get("wa_number") == "6281200000002", budi)
    st, r = hq.patch("customer", budi["name"], {"wa_opt_out": True})
    check("Rina ticks Tidak mau dikirimi WA -> 200", st == 200, (st, r))
    outs = api.find("whatsapp_opt_out", "number == '6281200000002'")
    check("the number is on the list once, from staff, linked to Budi",
          len(outs) == 1 and outs[0]["source"] == "staff" and outs[0]["customer"] == budi["name"], outs)
    told = [c for c in calls(api, "set_opt_out") if c.get("phone") == "+6281200000002"]
    eq("Rangka Cloud is told once (set_opt_out queued)", len(told), 1)
    hq.patch("customer", budi["name"], {"city": "Bandung"})
    eq("saving Budi again tells Rangka Cloud nothing more", len([c for c in calls(api, "set_opt_out") if c.get("phone") == "+6281200000002"]), 1)
    hq.patch("customer", budi["name"], {"wa_opt_out": False})
    eq("unticking does not take the number off the list", len(api.find("whatsapp_opt_out", "number == '6281200000002'")), 1)

    st, sale = pay(sari, CTX["wa_shift"], [{"item": ROTI, "qty": 1}], buyer="Budi", wa_phone=BUDI, wa_receipt=True)
    key = (sale or {}).get("name", "") + ":receipt:1"
    m = message(api, key)
    check("a receipt to a number on the list: the row says Tidak dikirim and why, nothing queued",
          st == 200 and m and m.get("status") == "skipped" and m.get("reason") == "Pelanggan minta tidak dikirimi WA" and not calls_for(api, key), (st, m))


def scenario_wa_status_events(api):
    """The relay's events arrive signed with the business's key: the row moves forward only; a forged event is refused."""
    k, sale = CTX.get("kernel"), CTX.get("wa_sale")
    wkey = CTX.get("wa_webhook_key")
    if not (k and sale and wkey):
        fail("whatsapp status events", "needs the receipt and the host's webhook key")
        return
    key = sale["name"] + ":receipt:1"
    ev = lambda st, err="": {"message_id": "wam_test0001", "idempotency_key": key, "type": "receipt", "status": st,
                             "at": "2026-11-19T03:04:05Z", "error": err}
    eq("a forged event (another key) -> 401", hook(k, "message_status", ev("failed", "service_stopped"), "dummy-other-key"), 401)
    eq("Rangka Cloud's 'delivered' -> 200", hook(k, "message_status", ev("delivered"), wkey), 200)
    got = wait_for(lambda: (message(api, key) or {}).get("status") == "delivered" and message(api, key))
    check("the row says Sampai, with the relay's message id", got and got.get("message_id") == "wam_test0001", got)
    hook(k, "message_status", ev("read"), wkey)
    wait_for(lambda: (message(api, key) or {}).get("status") == "read")
    hook(k, "message_status", ev("delivered"), wkey)
    hook(k, "message_status", ev("failed", "meta:131026"), wkey)
    time.sleep(2)
    eq("a late delivered or failed after Dibaca changes nothing", (message(api, key) or {}).get("status"), "read")
    st, sf = pay(CTX["sari"], CTX["wa_shift"], [{"item": ROTI, "qty": 1}], buyer="Eka", wa_phone="081200000006", wa_receipt=True)
    keyf = (sf or {}).get("name", "") + ":receipt:1"
    hook(k, "message_status", {**ev("sent"), "idempotency_key": keyf, "message_id": "wam_test0002"}, wkey)
    wait_for(lambda: (message(api, keyf) or {}).get("status") == "sent")
    hook(k, "message_status", {**ev("failed", "meta:131026"), "idempotency_key": keyf, "message_id": "wam_test0002"}, wkey)
    got = wait_for(lambda: (message(api, keyf) or {}).get("status") == "failed" and message(api, keyf))
    check("Meta's failed after sent: Gagal, the number cannot receive WhatsApp (audit finding 7)",
          got and got.get("reason") == "Nomor tidak bisa menerima pesan WhatsApp", message(api, keyf))

    # A refusal of the relay (no message id): the row says Gagal and why.
    plain = CTX.get("wa_plain_sale") or {}
    st, s2 = pay(CTX["sari"], CTX["wa_shift"], [{"item": ROTI, "qty": 1}], buyer="Citra", wa_phone=CITRA, wa_receipt=True)
    key2 = (s2 or {}).get("name", "") + ":receipt:1"
    hook(k, "message_status", {"message_id": "", "idempotency_key": key2, "type": "receipt", "status": "failed",
                               "at": "2026-11-19T03:05:00Z", "error": "service_stopped"}, wkey)
    got = wait_for(lambda: (message(api, key2) or {}).get("status") == "failed" and message(api, key2))
    check("Rangka Cloud stopped the service: Gagal, 'Layanan WhatsApp sedang dihentikan'",
          got and got.get("reason") == "Layanan WhatsApp sedang dihentikan", got)
    hook(k, "message_status", {"message_id": "x", "idempotency_key": plain.get("name", "x") + ":receipt:9", "status": "read",
                               "type": "receipt", "at": "2026-11-19T03:05:00Z", "error": ""}, wkey)
    time.sleep(1)
    check("a status for a key nobody asked for makes no row", not messages(api, f"key == '{plain.get('name', 'x')}:receipt:9'"), "")


def scenario_wa_berhenti(api):
    """Citra replied BERHENTI: Rangka Cloud forwards opted_out; she goes on the list and her customer record is ticked."""
    k, hq, wkey = CTX.get("kernel"), CTX.get("hq"), CTX.get("wa_webhook_key")
    if not (k and hq and wkey):
        return
    st, c = hq.create("customer", {"customer_name": "Citra Contoh", "phone": "0812 0000 0003"})
    citra = rec(c)
    before = len(calls(api, "set_opt_out"))
    eq("opted_out from Rangka Cloud -> 200", hook(k, "opted_out", {"phone": "+6281200000003", "at": "2026-11-19T03:06:00Z", "source": "keyword"}, wkey), 200)
    outs = wait_for(lambda: api.find("whatsapp_opt_out", "number == '6281200000003'"))
    check("the number is on the list, from the customer", outs and outs[0]["source"] == "customer", outs)
    got = wait_for(lambda: api.get("customer", citra.get("name", "")).get("wa_opt_out"))
    check("Citra's record is ticked Tidak mau dikirimi WA", bool(got), api.get("customer", citra.get("name", "")))
    eq("the kernel does not report the opt-out back to Rangka Cloud", len(calls(api, "set_opt_out")), before)


def scenario_wa_order_ready(api):
    """Tono marks Dewi's coffee Siap: Dewi gets pesanan_siap once, even when the ticket goes back and forth."""
    tono, sale = CTX.get("tono"), CTX.get("wa_sale")
    if not (tono and sale):
        return
    ts = tono.find("kitchen_ticket", f"sale == '{sale['name']}'")
    if not check("Dewi's sale made kitchen tickets", bool(ts), ts):
        return
    t = ts[0]["name"]
    for status in ("cooking", "ready", "cooking", "ready"):
        st, r = to_status(tono, t, status)
        check(f"Tono: ticket -> {status}", st == 200, (st, r))
    key = t + ":order_ready:1"
    m = message(api, key)
    check("one order-ready row, queued", m and m.get("status") == "queued" and m.get("template") == "pesanan_siap", m)
    cs = calls_for(api, key)
    check("one call: pesanan_siap to Dewi with the receipt number and the outlet",
          len(cs) == 1 and params(cs[0]) == ["Dewi", sale["name"], "Kedai Contoh Riau"] and cs[0].get("type") == "order_ready", cs)
    ps = api.find("whatsapp_pickup", f"sale == '{sale['name']}'")
    check("the kitchen reads who to tell from whatsapp_pickup, never the receipt",
          len(ps) == 1 and ps[0]["buyer"] == "Dewi" and "grand_total" not in ps[0], ps)
    plain = CTX.get("wa_plain_sale") or {}
    for pt in tono.find("kitchen_ticket", f"sale == '{plain.get('name', 'x')}'"):
        to_status(tono, pt["name"], "cooking")
        to_status(tono, pt["name"], "ready")
        check("a ticket whose buyer did not agree sends nothing", not message(api, pt["name"] + ":order_ready:1"), pt["name"])


def scenario_wa_reminder(api):
    """The morning run reminds overdue customers once per step, with the bank account and an Indonesian date."""
    hq = CTX.get("hq")
    owner = CTX.get("owner") or api
    today = kernel_today()
    st, c = hq.create("customer", {"customer_name": "Katering Contoh", "phone": "081200000004", "payment_days": 0})
    cust = rec(c)
    st, c2 = hq.create("customer", {"customer_name": "Tanpa Nomor", "payment_days": 0})
    nophone = rec(c2)
    due = (today - datetime.timedelta(days=8)).isoformat()
    months = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
    due_id = f"{int(due[8:])} {months[int(due[5:7]) - 1]} {due[:4]}"
    invs = []
    for cu in (cust, nophone):
        st, inv = owner.create("sales_invoice", invoice_body(cu.get("name"), PULSA, rate=1_234_567, posting_date=due, due_date=due,
                                                              tax_rate=0, tax_rate_reason="not_taxable"))
        st, r = owner.verb("sales_invoice", rec(inv).get("name", ""), "submit", {"force": True})
        check(f"overdue invoice for {cu.get('customer_name')} submitted", st == 200, (st, r))
        invs.append(rec(r) if st == 200 else rec(inv))
    st, out = api.call("POST", "/api/v1/flows/whatsapp_invoice_overdue/run", {"event": {}})
    check("the morning run -> 200", st == 200, (st, out))
    key = invs[0]["name"] + ":reminder:2"
    m = message(api, key)
    check("8 days late: step 2, queued, Rp 1.234.567", m and m.get("status") == "queued" and m.get("amount_text") == "Rp 1.234.567"
          and m.get("customer") == cust.get("name"), m)
    cs = calls_for(api, key)
    if check("one call for the step", len(cs) == 1, cs):
        p = params(cs[0])
        check("parameters: name, invoice, amount, days late, Indonesian due date, bank account",
              p[:5] == ["Katering Contoh", invs[0]["name"], "Rp 1.234.567", "8", due_id] and len(p) == 6 and p[5], p)
    rems = api.find("invoice_reminder", f"sales_invoice == '{invs[0]['name']}'")
    check("the reminder is on the invoice too (channel WhatsApp, pengingat_jatuh_tempo)",
          len(rems) == 1 and rems[0]["channel"] == "whatsapp" and rems[0]["template"] == "pengingat_jatuh_tempo", rems)
    m2 = message(api, invs[1]["name"] + ":reminder:2")
    check("a customer without a number: Tidak dikirim, 'Nomor WhatsApp belum diisi'",
          m2 and m2.get("status") == "skipped" and m2.get("error") == "no_number" and not calls_for(api, invs[1]["name"] + ":reminder:2"), m2)
    api.call("POST", "/api/v1/flows/whatsapp_invoice_overdue/run", {"event": {}})
    check("the run again (or a missed day caught up) sends no step twice",
          len(calls_for(api, key)) == 1 and len(api.find("invoice_reminder", f"sales_invoice == '{invs[0]['name']}'")) == 1, "")


def scenario_wa_booking(api):
    """A rental that starts tomorrow: the customer is reminded once."""
    hq, owner = CTX.get("hq"), CTX.get("owner") or api
    today = kernel_today()
    st, c = hq.create("customer", {"customer_name": "Penyewa Contoh", "phone": "081200000005"})
    cust = rec(c)
    st, it = owner.create("rental_item", {"item": PULSA, "title": "Tenda 3x3", "units": 2, "daily_rate": 150_000})
    if not check("rental item -> 201", st == 201, (st, it)):
        return
    st, b = owner.create("rental_booking", {"customer": cust.get("name"), "rental_item": rec(it)["name"], "qty": 1,
                                            "start_date": (today + datetime.timedelta(days=1)).isoformat(), "days": 2})
    if not check("booking for tomorrow -> 201", st == 201, (st, b)):
        return
    st, r = owner.verb("rental_booking", rec(b)["name"], "submit", {"force": True})
    check("booking submitted", st == 200, (st, r))
    st, out = api.call("POST", "/api/v1/flows/whatsapp_booking_tomorrow/run", {"event": {}})
    check("the afternoon run -> 200", st == 200, (st, out))
    CTX["wa_booking_run"] = out
    api.call("POST", "/api/v1/flows/whatsapp_booking_tomorrow/run", {"event": {}})
    key = rec(r).get("name", rec(b)["name"]) + ":appointment:1"   # the number it got at submit
    t = today + datetime.timedelta(days=1)
    tomorrow_id = f"{t.day} {['Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni', 'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember'][t.month - 1]} {t.year}"
    cs = calls_for(api, key)
    check("one reminder: pengingat_janji with the item and tomorrow's date",
          len(cs) == 1 and params(cs[0])[0] == "Penyewa Contoh" and params(cs[0])[1] == "mulai sewa " + api.get("rental_item", rec(it)["name"]).get("title", "")
          and params(cs[0])[3] == tomorrow_id and cs[0].get("type") == "appointment",
          (cs, rec(r).get("start_date"), str(CTX.get("wa_booking_run"))[:600]))


def scenario_wa_phone_rule(api):
    """customer.wa_number and pos_sale.wa_number follow the same rule as Rangka Cloud (audit findings 5 and 6)."""
    hq = CTX.get("hq")
    if not hq:
        return
    bad = {}
    for i, (raw, want) in enumerate(PHONE_VECTORS.items()):
        st, c = hq.create("customer", {"customer_name": f"Vektor {i}", "phone": raw})
        got = rec(c).get("wa_number") if st == 201 else ("HTTP", st)
        if got != want:
            bad[raw] = (got, want)
    check("customer.wa_number: every vector as Rangka Cloud normalises it", not bad, bad)


def scenario_wa_visibility(api):
    """The kitchen and the cashier see no amounts and no full numbers (audit finding 3); the kitchen reads no receipt."""
    tono, sari, sale = CTX.get("tono"), CTX.get("sari"), CTX.get("wa_sale")
    if not (tono and sari and sale):
        return
    st, r = tono.call("GET", f"/api/v1/r/pos_sale/{Api.q(sale['name'])}")
    check("the kitchen still may not read a receipt -> 403", st == 403, (st, r))
    for who, cl in (("kitchen", tono), ("cashier", sari)):
        rows = cl.list("whatsapp_message", limit=200)
        check(f"{who}: whatsapp_message rows carry no amount", rows and not any("amount" in m or "amount_text" in m for m in rows), rows[:2])
        outs = cl.list("whatsapp_opt_out", limit=200)
        check(f"{who}: the opt-out list shows no number", not any("number" in o for o in outs), outs[:2])
        ps = cl.list("whatsapp_pickup", limit=200)
        check(f"{who}: pickup rows show no number", ps and not any("number" in p for p in ps), ps[:2])
    hq = CTX.get("hq")
    if hq:
        check("head office still sees amounts", any(m.get("amount_text") for m in hq.list("whatsapp_message", limit=200)), "")


def scenario_wa_no_phone_in_logs(api):
    """No message row or audit entry carries a full phone number."""
    rows = json.dumps(messages(api))
    check("message rows keep only the last four digits", "81200000001" not in rows and "81200000002" not in rows, "")


SCENARIOS = [scenario_wa_host_connector, scenario_wa_receipt, scenario_wa_opt_out, scenario_wa_status_events,
             scenario_wa_berhenti, scenario_wa_order_ready, scenario_wa_reminder, scenario_wa_booking, scenario_wa_phone_rule,
             scenario_wa_visibility, scenario_wa_no_phone_in_logs]
