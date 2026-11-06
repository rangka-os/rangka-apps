#!/usr/bin/env python3
"""Writes modules/accounting/flows/gl_*.rk and the account options of modules/accounting/models/gl_entry.rk.

    python3 scripts/gen_accounting_flows.py

One table (POSTINGS) says which lines a submitted document writes into gl_entry: an account and a signed amount
(+ debit, - credit) per line, each a CEL expression over the submitted record (`event`). Every document gets two
flows: gl_<model>_post on submit (one create step per line, skipped when the amount is 0) and gl_<model>_reverse on
cancel (the opposite of each post line the document wrote, on the same date). A flow has no loop, so the reverse flow
holds REVERSE_SLOTS create steps over the post lines it found. The lines of one document always add up to 0: the
amounts below are written so that they do (the test tests/day_one_accounting.py checks every voucher).
Do not edit the generated flows by hand: change this table and run the script again.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLOWS = os.path.join(ROOT, "modules", "accounting", "flows")
MODEL = os.path.join(ROOT, "modules", "accounting", "models", "gl_entry.rk")

# Bagan akun (SAK EMKM, codes in the style of Jurnal.id and Kledo). The stored value is the code without its dash
# (option values are [a-z0-9_]), 1-1100 -> '11100'. Reports sum by prefix: 1 aset (111/112 kas dan bank), 2
# liabilitas, 3 ekuitas, 4 pendapatan, 5 beban pokok, 61 beban operasional, 69 beban pajak.
ACCOUNTS = [
    ("1-1100", "Kas"), ("1-1150", "Kas outlet belum disetor"), ("1-1200", "Bank"),
    ("1-1300", "Piutang usaha"), ("1-1320", "Piutang retensi"), ("1-1330", "Piutang karyawan"),
    ("1-1400", "Persediaan"), ("1-1410", "Persediaan dalam perjalanan"), ("1-1420", "Barang dalam proses"),
    ("1-1500", "PPN masukan"), ("1-1510", "PPh 23 dibayar dimuka"),
    ("1-2100", "Aset tetap"), ("1-2900", "Akumulasi penyusutan"),
    ("2-1100", "Utang usaha"), ("2-1150", "Utang belum ditagih"), ("2-1200", "PPN keluaran"),
    ("2-1300", "Utang PPh 23"), ("2-1310", "Utang PPh final"), ("2-1320", "Utang PPh 21"), ("2-1400", "Uang muka pelanggan"),
    ("2-1500", "Utang upah"), ("2-1600", "Utang BPJS"), ("2-1900", "Utang lain-lain"), ("2-2100", "Utang bank"),
    ("3-1100", "Modal disetor"), ("3-1900", "Ekuitas saldo awal"),
    ("4-1100", "Pendapatan usaha"), ("4-1200", "Retur penjualan"), ("4-9100", "Pendapatan lain-lain"),
    ("5-1100", "Harga pokok penjualan"), ("5-1200", "Selisih dan susut persediaan"), ("5-1300", "Upah langsung"),
    ("5-1400", "Biaya proyek"),
    ("6-1100", "Beban gaji kantor"), ("6-1200", "Biaya admin pembayaran"), ("6-1300", "Selisih kas"),
    ("6-1400", "Beban sewa"), ("6-1500", "Beban listrik, air, internet"), ("6-1600", "Beban transport dan kirim"),
    ("6-1700", "Beban pemasaran"), ("6-1800", "Beban perlengkapan dan perawatan"), ("6-1900", "Beban operasional lain"),
    ("6-9100", "Beban pajak"),
]

LOCK = [
    "  - find_all: period_close",
    "    where: docstatus == 1",
    "    sort: -closed_until",
    "    limit: 1",
    "    as: locks",
]
LOCK_DATE = "locks.size() > 0 ? locks[0].closed_until : null"

# Sum of the stock moves the document wrote (+ in, - out): the value the kernel took at the moving average.
MOVES = [
    "  - find_all: stock_move",
    "    where: voucher_type == event.model && voucher == event.name",
    "    as: moves",
]
NET = "moves.map(m, m.value).sum()"
MOVE_DATE = "moves.size() > 0 ? moves[0].posting_date : today()"

CASH = "event.method == 'cash' ? '11100' : (event.method == 'sales_return' ? '41200' : '11200')"
BILL_DEBIT = ("(event.posting == 'stock' || (event.posting == null && (event.goods_receipt != '' || event.purchase_order != ''))) ? '21150'"
              " : ((event.posting == 'project' || (event.posting == null && event.project != '')) ? '51400'"
              " : (event.expense_account != null ? event.expense_account : '61900'))")
CASH_TAKEN = "event.lines.filter(l, l.method == 'cash').map(l, l.received).sum()"

# model: label, date, outlet, project, party, extra steps (before the lock), if, lines [(account, amount)]
POSTINGS = {
    "sales_invoice": dict(
        label="Jurnal faktur penjualan", date="event.posting_date", outlet="event.outlet", project="event.project",
        party="event.customer",
        # Piutang (net bill), retensi and PPh 23 held by the customer, down payment taken back; against revenue
        # (a DP invoice: uang muka pelanggan, a retention invoice: piutang retensi) and PPN.
        lines=[("'11300'", "event.amount_due"),
               ("'11320'", "event.retention_amount"),
               ("'11510'", "event.pph23_amount"),
               ("'21400'", "event.down_payment"),
               ("event.kind == 'down_payment' ? '21400' : (event.kind == 'retention' ? '11320' : '41100')",
                "0 - (event.subtotal - event.discount_amount)"),
               ("'21200'", "0 - event.ppn")]),
    "payment": dict(
        label="Jurnal pembayaran", date="event.payment_date", party="event.direction == 'receive' ? event.customer : event.vendor",
        # Receive: cash or bank (Potong retur: retur penjualan) and the PPh 23 the customer withheld, against piutang.
        # Pay: utang usaha against cash or bank.
        lines=[("event.direction == 'receive' ? (" + CASH + ") : '21100'",
                "event.direction == 'receive' ? event.amount : event.amount + event.withheld_total"),
               ("event.direction == 'receive' ? '11510' : '21300'",
                "event.direction == 'receive' ? event.withheld_total : 0 - event.withheld_total"),
               ("event.direction == 'receive' ? '11300' : (" + CASH + ")",
                "0 - event.amount - event.withheld_total")]),
    "goods_receipt": dict(
        label="Jurnal terima barang", date="event.received_on", outlet="event.outlet", party="event.vendor",
        # Stock in at the receipt value; payable not yet billed, or opening equity for the "Saldo awal" vendor.
        extra=["  - find: vendor", "    where: name == event.vendor", "    as: v"],
        lines=[("'11400'", "event.total_value"),
               ("v != null && v.vendor_name == 'Saldo awal' ? '31900' : '21150'", "0 - event.total_value")]),
    "vendor_bill": dict(
        label="Jurnal tagihan pemasok", date="event.bill_date", project="event.project", party="event.vendor",
        # Goods (received before): utang belum ditagih; a project's cost; else the expense account chosen.
        lines=[(BILL_DEBIT, "event.dpp"),
               ("'11500'", "event.ppn"),
               ("'21100'", "0 - event.amount_due"),
               ("'21300'", "0 - event.pph23_amount")]),
    "outlet_sales": dict(
        label="Jurnal penjualan outlet", date="event.date", outlet="event.outlet",
        # Takings held at the outlet, revenue net of PPN, final PPh as tax expense, HPP out of stock.
        lines=[("'11150'", "event.net_total"),
               ("'41100'", "0 - (event.net_total - event.ppn_in_sales)"),
               ("'21200'", "0 - event.ppn_in_sales"),
               ("'69100'", "event.pph_final"),
               ("'21310'", "0 - event.pph_final"),
               ("'51100'", "event.cogs_total"),
               ("'11400'", "0 - event.cogs_total")]),
    "outlet_takings": dict(
        label="Jurnal setoran outlet", date="event.date", outlet="event.outlet",
        # Cash and non-cash received, the payment fees, against the takings held; the difference is selisih kas.
        lines=[("'11100'", CASH_TAKEN),
               ("'11200'", "event.received_total - (" + CASH_TAKEN + ")"),
               ("'61200'", "event.fee_total"),
               ("'11150'", "0 - event.expected_total"),
               ("'61300'", "0 - event.difference_total")]),
    "timesheet": dict(
        label="Jurnal upah proyek", date="event.work_date", project="event.project",
        lines=[("'51300'", "event.cost_amount"), ("'21500'", "0 - event.cost_amount")]),
    "project_expense": dict(
        label="Jurnal biaya proyek", date="event.expense_date", project="event.project", party="event.vendor",
        # A cost on a vendor bill is journaled by the bill; one without is paid from cash.
        cond="event.vendor_bill == null",
        lines=[("'51400'", "event.amount"), ("'11100'", "0 - event.amount")]),
    "delivery_note": dict(
        label="Jurnal surat jalan", date="event.delivery_date", outlet="event.outlet", party="event.customer",
        lines=[("'51100'", "event.cogs_total"), ("'11400'", "0 - event.cogs_total")]),
    "sales_return": dict(
        label="Jurnal retur penjualan (stok)", date=MOVE_DATE, outlet="event.outlet", party="event.customer", extra=MOVES,
        lines=[("'11400'", NET), ("'51100'", "0 - " + NET)]),
    "stock_adjustment": dict(
        label="Jurnal penyesuaian stok", date=MOVE_DATE, outlet="event.outlet", extra=MOVES,
        lines=[("'11400'", NET), ("event.reason == 'consignment_return' ? '21150' : '51200'", "0 - " + NET)]),
    "stock_count": dict(
        label="Jurnal opname", date=MOVE_DATE, outlet="event.outlet", extra=MOVES,
        lines=[("'11400'", NET), ("'51200'", "0 - " + NET)]),
    "stock_transfer": dict(
        label="Jurnal transfer keluar", date=MOVE_DATE, outlet="event.from_outlet", extra=MOVES,
        lines=[("'11400'", NET), ("'11410'", "0 - " + NET)]),
    "stock_transfer_receipt": dict(
        label="Jurnal transfer masuk", date=MOVE_DATE, outlet="event.outlet", extra=MOVES,
        lines=[("'11400'", NET), ("'11410'", "0 - " + NET)]),
    "production_batch": dict(
        label="Jurnal produksi", date="event.production_date", outlet="event.outlet", extra=MOVES,
        # Outputs take in what the materials took out (contract 0021 consumed), so stock nets to 0 unless nothing
        # came out. Piece-rate wages are labour owed.
        lines=[("'11400'", NET), ("'51200'", "0 - " + NET),
               ("'51300'", "event.labour_cost"), ("'21500'", "0 - event.labour_cost")]),
    "opening_balance": dict(
        label="Jurnal saldo awal", date="event.opening_date",
        lines=[("'11100'", "event.cash"), ("'11200'", "event.bank"), ("'11300'", "event.receivable"),
               ("'12100'", "event.fixed_assets"), ("'12900'", "0 - event.accumulated_depreciation"),
               ("'21100'", "0 - event.payable"), ("'22100'", "0 - event.bank_loan"),
               ("'21900'", "0 - event.other_liabilities"), ("'31100'", "0 - event.paid_in_capital"),
               ("'31900'", "0 - (event.cash + event.bank + event.receivable + event.fixed_assets"
                            " - event.accumulated_depreciation - event.payable - event.bank_loan"
                            " - event.other_liabilities - event.paid_in_capital)")]),
}
REVERSE_SLOTS = max(len(p["lines"]) for p in POSTINGS.values())


def q(s):
    """A YAML double-quoted scalar."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def post_flow(model, p):
    out = ["# yaml-language-server: $schema=https://rangka-os.github.io/rangka-os/schema/0.5/flow.json",
           "# Generated by scripts/gen_accounting_flows.py; edit POSTINGS there, not this file.",
           f"name: gl_{model}_post",
           f"label: {{id: {p['label']}}}",
           f"on: {model}.submitted"]
    if p.get("cond"):
        out.append(f"if: {q(p['cond'])}")
    out.append("steps:")
    out += p.get("extra", [])
    out += LOCK
    for i, (acct, amt) in enumerate(p["lines"], 1):
        out += ["  - create: gl_entry",
                f"    if: {q('(' + amt + ') != 0')}",
                "    set:",
                f"      posting_date: {q(p['date'])}",
                f"      account: {q(acct)}",
                f"      amount: {q(amt)}",
                "      voucher_type: event.model",
                "      voucher: event.name",
                "      entry: \"'post'\"",
                f"      line: \"'{i}'\""]
        for f in ("outlet", "project", "party"):
            if p.get(f):
                out.append(f"      {f}: {q(p[f])}")
        out.append(f"      lock_date: {q(LOCK_DATE)}")
    return "\n".join(out) + "\n"


def reverse_flow(model, p):
    out = ["# yaml-language-server: $schema=https://rangka-os.github.io/rangka-os/schema/0.5/flow.json",
           "# Generated by scripts/gen_accounting_flows.py; edit POSTINGS there, not this file.",
           "# The opposite of every line the document posted, on the post's own date, so a cancelled document nets to 0",
           "# in its period. A document submitted before the accounting module was installed posted nothing and is not",
           "# reversed. Amending posts the new document again.",
           f"name: gl_{model}_reverse",
           f"label: {{id: {p['label']} dibatalkan}}",
           f"on: {model}.cancelled",
           "steps:",
           "  - find_all: gl_entry",
           "    where: voucher_type == event.model && voucher == event.name && entry == 'post'",
           "    sort: line",
           "    as: posts"]
    out += LOCK
    for i in range(REVERSE_SLOTS):
        r = f"posts[{i}]"
        out += ["  - create: gl_entry",
                f"    if: posts.size() > {i}",
                "    set:",
                f"      posting_date: {r}.posting_date",
                f"      account: {r}.account",
                f"      amount: 0 - {r}.amount",
                f"      voucher_type: {r}.voucher_type",
                f"      voucher: {r}.voucher",
                "      entry: \"'reverse'\"",
                f"      line: {r}.line",
                f"      reverses: {r}.name",
                f"      outlet: {r}.outlet",
                f"      project: {r}.project",
                f"      party: {r}.party",
                f"      lock_date: {q(LOCK_DATE)}"]
    return "\n".join(out) + "\n"


def account_options():
    return "\n".join(f"      - {{value: '{c.replace('-', '')}', label: {{id: '{c} {n}'}}}}" for c, n in ACCOUNTS)


def main():
    os.makedirs(FLOWS, exist_ok=True)
    for f in os.listdir(FLOWS):
        if f.startswith("gl_") and f.endswith(".rk"):
            os.remove(os.path.join(FLOWS, f))
    for model, p in POSTINGS.items():
        with open(os.path.join(FLOWS, f"gl_{model}_post.rk"), "w") as fh:
            fh.write(post_flow(model, p))
        with open(os.path.join(FLOWS, f"gl_{model}_reverse.rk"), "w") as fh:
            fh.write(reverse_flow(model, p))
    src = open(MODEL).read()
    start, end = "    # accounts:begin\n", "    # accounts:end\n"
    a, b = src.index(start) + len(start), src.index(end)
    open(MODEL, "w").write(src[:a] + account_options() + "\n" + src[b:])
    print(f"{2 * len(POSTINGS)} flows, {len(ACCOUNTS)} accounts")


if __name__ == "__main__":
    main()
