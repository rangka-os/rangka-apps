"""Day-one scenarios of a paired cashier device on demo-kasir (rangka-os contract 0058, rangka-os#262 slice b).

Loaded by tests/day_one.py after day_one_pos (its members and helpers): `python3 tests/day_one.py --site demo-kasir`.
A browser is paired to Toko Dago the way #/pasang does it (owner's pairing code, POST /auth/device/pair, the rk_device
cookie) and sends what the counter sends: screen.write events to POST /api/v1/edge/events. Covered:
  - device_sale_named: the shift and the receipt carry the device's numbers (SESI-DGO-<code>-YYMM-0001,
    STRK-DGO-<code>-YYMM-00001), the money the device computed is stored as sent (no edge_price_drift when it is the
    kernel's), sold_at is the press time (occurred_at) and the shift's totals grow; /edge/config gives the patterns
  - device_price_drift: a sale computed at the old price after head office changed it is kept at the device's
    total (sales win) with an edge_price_drift exception
  - device_number_gap: a skipped number is accepted with a device_number_gap exception; a wrong pattern is refused
  - device_offline_after_close: a sale paid offline on a shift head office closed meanwhile is accepted (rule shift_open
    becomes rule_overridden, flow pos_sale_on_submit does not refuse it); the closed shift keeps its counted totals
  - device_second_shift: a shift a device opened offline is accepted though the cashier has another open one
  - device_not_a_store: a device paired at the warehouse cannot open a shift nor sell on a store's shift
    (rangka-apps#104, guard not_a_store)
  - browser numbering unchanged: a sale made in the browser keeps STRK-DGO-YYMM-n without a device code
"""
import datetime
import http.cookiejar
import json
import urllib.request

H = {}
DGO, HQ_OUT = "OUT-02", "OUT-01"
AIR, MI = "BRG-0001", "BRG-0002"
PIN = "2468"
GUDANG = ("gudang", "cashier", "kasir.gudang@contohwarung.test", "Kasir Gudang Contoh", HQ_OUT)
# the cashier at the paired device of Toko Dago (a member of her own, so the PIN does not touch day_one_pos's members)
DEWI = ("dewi", "cashier", "dewi.dago@contohwarung.test", "Dewi Contoh", DGO)


def bind(m):
    for k in ("ok", "fail", "check", "eq", "CTX", "Api", "kernel_today", "secrets"):
        H[k] = getattr(m, k)
    globals().update(H)


class Device:
    """A paired browser: its rk_device cookie and the events it sends."""

    def __init__(self, base):
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.seq = 0
        self.info = {}

    def call(self, method, path, body=None, edge=False):
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if edge:
            h["Rangka-Edge-Sync"] = "v1"
        req = urllib.request.Request(self.base + path, method=method, headers=h,
                                     data=None if body is None else json.dumps(body).encode())
        try:
            with self.op.open(req, timeout=30) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"raw": raw.decode(errors="replace")[:200]}

    def send(self, payload, actor, at=None, key=None):
        """One screen.write event; returns its result {status, code, message, ref}."""
        self.seq += 1
        at = at or datetime.datetime.now(datetime.timezone.utc)
        ev = {"key": key or "k-" + secrets.token_hex(8), "kind": "screen.write", "seq": self.seq,
              "occurred_at": at.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z", "actor": str(actor), "payload": payload}
        st, r = self.call("POST", "/api/v1/edge/events", {"events": [ev]}, edge=True)
        res = ((r or {}).get("results") or [{}])[0] if st == 200 else {"status": "http_" + str(st), "message": str(r)}
        return res


def pair(owner, outlet, name):
    st, c = owner.call("POST", "/api/v1/devices/codes", {"outlet": outlet})
    check(f"pairing code for {outlet} -> 201", st == 201, (st, c))
    d = Device(owner.base)
    st, info = d.call("POST", "/auth/device/pair", {"code": (c or {}).get("code", ""), "name": name})
    check(f"device paired at {outlet} -> 201", st == 201 and (info or {}).get("code"), (st, info))
    d.info = info or {}
    return d


def staff_id(dev, name):
    st, staff = dev.call("GET", "/auth/device/staff")
    for s in (staff or []) if st == 200 else []:
        if s.get("name") == name:
            return s["id"]
    fail(f"{name} on the device's staff list", (st, staff))
    return 0


def set_pin(email, pw, org):
    cl = Api.login(CTX["kernel"].base, email, pw, org)
    if cl is None:
        fail(f"sign in {email} to set a PIN", "login refused")
        return
    st, r = cl.call("PUT", "/auth/pin", {"pin": PIN})
    check(f"{email} sets a cashier PIN -> 204", st == 204, (st, r))


def money_of(cl, body):
    """The kernel's numbers for an unsaved sale (POST /compute, contract 0034): what engine.js computes on the device
    (the shared parity fixture keeps the two equal)."""
    st, r = cl.call("POST", "/api/v1/compute/pos_sale", body)
    check("compute the sale like the device -> 200", st == 200, (st, r))
    return dict(body, **{k: v for k, v in ((r or {}).get("record") or {}).items() if k not in ("name", "docstatus")})


def exceptions(owner, kind):
    st, r = owner.call("GET", "/api/v1/exceptions?kind=" + kind)
    return (r or {}).get("items") or (r or {}).get("data") or [] if st == 200 else []


def yymm(d=None):
    return (d or kernel_today()).strftime("%y%m")


def scenario_device_sale_named(api):
    owner, k = api, CTX["kernel"]
    pw = secrets.token_urlsafe(18)
    # Dewi (Toko Dago) sets a PIN so the device lists her (contract 0058 §2)
    CTX["device_pw"] = pw
    key, role, email, name, outlet = DEWI
    code, out = k.user_add(email, role, CTX["org"], name, outlets=outlet, password=pw)
    check("a cashier member for the device", code == 0, out)
    set_pin(email, pw, CTX["org"])
    dev = pair(owner, DGO, "Kasir depan")
    CTX["dev"] = dev
    dcode = dev.info.get("code", "")
    st, cfg = dev.call("GET", "/api/v1/edge/config", edge=True)
    naming = (cfg or {}).get("naming") or {}
    eq("/edge/config names the device patterns", {k: naming.get(k) for k in ("pos_sale", "pos_shift")},
       {"pos_sale": "STRK-{outlet.code}-{device.code}-{YY}{MM}-{#####}", "pos_shift": "SESI-{outlet.code}-{device.code}-{YY}{MM}-{####}"})
    actor = staff_id(dev, name)
    CTX["dev_actor"] = actor
    # the outlet's own float when head office set one (flow pos_shift_on_create, guard float_of_outlet)
    flt = (api.get("outlet", DGO) or {}).get("cash_float") or 300000
    CTX["dev_float"] = flt
    ym = yymm()
    shift = f"SESI-DGO-{dcode}-{ym}-0001"
    r = dev.send({"screen": "kasir", "model": "pos_shift", "op": "create", "name": shift, "body": {"outlet": DGO, "opening_cash": flt}}, actor)
    check("device shift accepted with its own number", r.get("status") == "accepted" and str(r.get("ref", "")).endswith(shift), r)
    CTX["dev_shift"] = shift
    cashier = Api.login(k.base, DEWI[2], pw, CTX["org"])
    body = money_of(cashier, {"shift": shift, "outlet": DGO, "items": [{"item": AIR, "qty": 3}, {"item": MI, "qty": 2}], "payments": [{"method": "cash", "amount": 50000}]})
    pressed = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=7)
    name = f"STRK-DGO-{dcode}-{ym}-00001"
    r = dev.send({"screen": "kasir", "model": "pos_sale", "op": "create", "submit": True, "name": name, "body": body}, actor, at=pressed)
    check("device sale accepted", r.get("status") == "accepted", r)
    s = api.get("pos_sale", name) or {}
    eq("the receipt keeps the device's number, paid", (s.get("name"), s.get("docstatus")), (name, 1))
    eq("its totals are the device's", (s.get("grand_total"), s.get("change_amount")), (body["grand_total"], body["change_amount"]))
    check("sold_at is when the button was pressed (occurred_at), not when it synced",
          str(s.get("sold_at", "")).startswith(pressed.strftime("%Y-%m-%dT%H:%M")), (s.get("sold_at"), pressed.isoformat()))
    eq("no edge_price_drift for a sale computed like the kernel", [e for e in exceptions(owner, "edge_price_drift") if name in str(e.get("ref"))], [])
    sh = api.get("pos_shift", shift) or {}
    eq("the device shift's totals grow", (sh.get("sales_count"), sh.get("net_total")), (1, body["grand_total"]))
    # a sale in the browser (head office, no device) keeps the outlet's series without a device code
    names = [x.get("name", "") for x in api.find("pos_shift", f"outlet == '{DGO}'")]
    if not [n for n in names if n.startswith(f"SESI-DGO-{ym}-")]:
        st, h = api.create("pos_shift", {"outlet": DGO, "opening_cash": flt})
        names.append(((h or {}).get("record", h) or {}).get("name", ""))
    check("a browser shift keeps SESI-DGO-YYMM-n", any(n.startswith(f"SESI-DGO-{ym}-") for n in names), names)


def scenario_device_price_drift(api):
    dev, actor, shift = CTX.get("dev"), CTX.get("dev_actor"), CTX.get("dev_shift")
    if not dev:
        fail("device_price_drift", "no paired device (device_sale_named failed)")
        return
    k, dcode = CTX["kernel"], dev.info.get("code", "")
    cashier = Api.login(k.base, DEWI[2], CTX["device_pw"], CTX["org"])
    body = money_of(cashier, {"shift": shift, "outlet": DGO, "items": [{"item": AIR, "qty": 2}]})
    old = (api.get("item", AIR) or {}).get("rate")
    st, _ = api.patch("item", AIR, {"rate": old + 1000})   # head office raises the price; the device still has the old one
    check("head office raises the price -> 200", st == 200, st)
    name = f"STRK-DGO-{dcode}-{yymm()}-00002"
    r = dev.send({"screen": "kasir", "model": "pos_sale", "op": "create", "submit": True, "name": name, "body": body}, actor)
    check("the old-price sale is accepted (sales win)", r.get("status") == "accepted", r)
    s = api.get("pos_sale", name) or {}
    eq("the device's total is kept", s.get("grand_total"), body["grand_total"])
    drift = [e for e in exceptions(api, "edge_price_drift") if name in str(e.get("ref"))]
    check("edge_price_drift for head office", len(drift) >= 1, drift)
    api.patch("item", AIR, {"rate": old})


def scenario_device_number_gap(api):
    dev, actor, shift = CTX.get("dev"), CTX.get("dev_actor"), CTX.get("dev_shift")
    if not dev:
        fail("device_number_gap", "no paired device")
        return
    k, dcode = CTX["kernel"], dev.info.get("code", "")
    cashier = Api.login(k.base, DEWI[2], CTX["device_pw"], CTX["org"])
    body = money_of(cashier, {"shift": shift, "outlet": DGO, "items": [{"item": MI, "qty": 1}]})
    name = f"STRK-DGO-{dcode}-{yymm()}-00005"
    r = dev.send({"screen": "kasir", "model": "pos_sale", "op": "create", "submit": True, "name": name, "body": body}, actor)
    check("a skipped number is accepted", r.get("status") == "accepted", r)
    gap = [e for e in exceptions(api, "device_number_gap") if name in str(e.get("ref"))]
    check("device_number_gap for head office", len(gap) == 1, gap)
    r = dev.send({"screen": "kasir", "model": "pos_sale", "op": "create", "submit": True, "name": f"STRK-DGO-{yymm()}-00006", "body": body}, actor)
    eq("a name without the device code is refused", (r.get("status"), r.get("code")), ("rejected", "device_name_invalid"))


def scenario_device_offline_after_close(api):
    dev, actor = CTX.get("dev"), CTX.get("dev_actor")
    if not dev:
        fail("offline_after_close", "no paired device")
        return
    k, dcode, ym, shift = CTX["kernel"], dev.info.get("code", ""), yymm(), CTX["dev_shift"]
    t0 = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=2)
    cashier = Api.login(k.base, DEWI[2], CTX["device_pw"], CTX["org"])
    # the sale the device paid offline two minutes ago, still in its queue
    body = money_of(cashier, {"shift": shift, "outlet": DGO, "items": [{"item": AIR, "qty": 1}]})
    # head office closes the device's shift meanwhile, counting the drawer
    st, c = api.create("pos_shift_close", {"shift": shift, "counted_cash": CTX.get("dev_float", 300000)})
    c = (c or {}).get("record", c) or {}
    st2, _ = api.verb("pos_shift_close", c.get("name", ""), "submit") if st == 201 else (st, None)
    check("head office closes the device shift", st == 201 and st2 == 200, (st, st2, c))
    before = api.get("pos_shift", shift) or {}
    name = f"STRK-DGO-{dcode}-{ym}-00006"
    r = dev.send({"screen": "kasir", "model": "pos_sale", "op": "create", "submit": True, "name": name, "body": body}, actor, at=t0)
    check("a sale paid offline before the close arrived is accepted", r.get("status") == "accepted", r)
    over = [e for e in exceptions(api, "rule_overridden") if (e.get("detail") or {}).get("rule") == "shift_open"]
    check("rule_overridden shift_open for head office", len(over) >= 1, over)
    after = api.get("pos_shift", shift) or {}
    eq("the closed shift keeps its counted totals", (after.get("state"), after.get("net_total")), ("closed", before.get("net_total")))


def scenario_device_second_shift(api):
    """A cashier with a shift open elsewhere (here: in the browser) still gets the shift her device opened offline:
    refusing it would refuse every receipt queued on it (contract 0058 §6)."""
    dev, actor = CTX.get("dev"), CTX.get("dev_actor")
    if not dev:
        fail("device_second_shift", "no paired device")
        return
    k, dcode, ym, flt = CTX["kernel"], dev.info.get("code", ""), yymm(), CTX.get("dev_float", 300000)
    cashier = Api.login(k.base, DEWI[2], CTX["device_pw"], CTX["org"])
    st, b = cashier.create("pos_shift", {"outlet": DGO, "opening_cash": flt})
    check("Dewi opens a shift in the browser", st == 201, (st, b))
    shift = f"SESI-DGO-{dcode}-{ym}-0002"
    r = dev.send({"screen": "kasir", "model": "pos_shift", "op": "create", "name": shift, "body": {"outlet": DGO, "opening_cash": flt}}, actor)
    check("the shift her device opened offline is accepted too", r.get("status") == "accepted", r)
    body = money_of(cashier, {"shift": shift, "outlet": DGO, "items": [{"item": MI, "qty": 2}]})
    r = dev.send({"screen": "kasir", "model": "pos_sale", "op": "create", "submit": True, "name": f"STRK-DGO-{dcode}-{ym}-00007", "body": body}, actor)
    check("and the receipt queued on it", r.get("status") == "accepted", r)
    # a second shift opened live in the browser is still refused
    st, b2 = cashier.create("pos_shift", {"outlet": DGO, "opening_cash": flt})
    check("a second browser shift is still refused (shift_already_open)", st == 422, (st, b2))


def scenario_device_not_a_store(api):
    k = CTX["kernel"]
    pw = secrets.token_urlsafe(18)
    key, role, email, name, outlet = GUDANG
    code, out = k.user_add(email, role, CTX["org"], name, outlets=outlet, password=pw)
    check("a cashier member at the warehouse", code == 0, out)
    set_pin(email, pw, CTX["org"])
    dev = pair(api, HQ_OUT, "Perangkat gudang")
    actor = staff_id(dev, name)
    shift = f"SESI-HO-{dev.info.get('code', '')}-{yymm()}-0001"
    r = dev.send({"screen": "kasir", "model": "pos_shift", "op": "create", "name": shift, "body": {"outlet": HQ_OUT, "opening_cash": 0}}, actor)
    check("a device at the warehouse cannot open a shift (only a store sells)",
          r.get("status") == "rejected" and "Toko" in str(r.get("message")), r)
    # nor sell on a store's shift head office opened (no device): the sale's own rules are exceptions for a device
    # event, the flow's guard is not
    open_ho = [x for x in api.find("pos_shift", f"outlet == '{DGO}' && state == 'open'") if x.get("name", "").startswith(f"SESI-DGO-{yymm()}-")]
    if not open_ho:
        st, h = api.create("pos_shift", {"outlet": DGO, "opening_cash": CTX.get("dev_float", 300000)})
        open_ho = [(h or {}).get("record", h) or {}]
    r = dev.send({"screen": "kasir", "model": "pos_sale", "op": "create", "submit": True, "name": f"STRK-HO-{dev.info.get('code', '')}-{yymm()}-00001",
                  "body": {"shift": open_ho[0].get("name"), "outlet": HQ_OUT, "items": [{"item": AIR, "qty": 1}]}}, actor)
    check("a device at the warehouse cannot sell on a store's shift", r.get("status") == "rejected" and "Toko" in str(r.get("message")), r)


SCENARIOS = [scenario_device_sale_named, scenario_device_price_drift, scenario_device_number_gap, scenario_device_offline_after_close,
             scenario_device_second_shift, scenario_device_not_a_store]
