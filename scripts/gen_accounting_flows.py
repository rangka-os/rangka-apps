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
    ("1-1300", "Piutang usaha"), ("1-1320", "Piutang retensi"), ("1-1330", "Piutang karyawan"), ("1-1340", "Piutang dana gaji klien"),
    ("1-1400", "Persediaan"), ("1-1410", "Persediaan dalam perjalanan"), ("1-1420", "Barang dalam proses"),
    ("1-1500", "PPN masukan"), ("1-1510", "PPh 23 dibayar dimuka"),
    ("1-2100", "Aset tetap"), ("1-2900", "Akumulasi penyusutan"),
    ("2-1100", "Utang usaha"), ("2-1150", "Utang belum ditagih"), ("2-1200", "PPN keluaran"),
    ("2-1300", "Utang PPh 23"), ("2-1310", "Utang PPh final"), ("2-1320", "Utang PPh 21"), ("2-1400", "Uang muka pelanggan"),
    ("2-1500", "Utang upah"), ("2-1600", "Utang BPJS"), ("2-1900", "Utang lain-lain"), ("2-2100", "Utang bank"),
    ("3-1100", "Modal disetor"), ("3-1900", "Ekuitas saldo awal"),
    ("4-1100", "Pendapatan usaha"), ("4-1200", "Retur penjualan"), ("4-9100", "Pendapatan lain-lain"),
    ("4-9200", "Pendapatan denda dan ganti rugi sewa"),
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
# What a vendor bill pays for (vendor_bill.posting, defaulted from its links; old bills without it follow the same
# order): a project's cost, goods already received into stock (only where the stock module is installed), else the
# expense account chosen.
BILL_KIND = ("(event.posting != null ? event.posting : (event.project != '' ? 'project'"
             " : ((event.goods_receipt != '' || event.purchase_order != '') && app_installed('stock') ? 'stock' : 'expense')))")
BILL_DEBIT = ("(" + BILL_KIND + ") == 'project' ? '51400' : ((" + BILL_KIND + ") == 'stock' ? '21150'"
              " : (event.expense_account != null ? event.expense_account : '61900'))")
# A business that is not PKP cannot credit input PPN: it is part of the cost (goods: HPP, as the receipt valued the
# stock without it).
BILL_PPN = "tax.pkp ? '11500' : ((" + BILL_KIND + ") == 'stock' ? '51100' : (" + BILL_DEBIT + "))"
# Potong retur: the credit note covers the return value plus its PPN; only the value is retur penjualan, the PPN
# reduces PPN keluaran, in the proportion of what this payment credits.
RETURN_VALUE = "round(event.amount * 1.0 * ret.return_value / ret.return_total)"
# A project expense on a vendor bill moves that bill's cost (its debit line 1, plus line 2 when that is the same
# account: PPN of a business that is not PKP) into the project, always at its full amount; the live expenses on one
# bill may not move more than the bill's cost (gl_entry rule bill_cost_left on BILL_LEFT, computed from the lines of the
# other expenses on the bill, whose reversals give their part back), so the result never depends on their order.
BILL_COST = "bl.amount + (bl2 != null && bl2.account == bl.account ? bl2.amount : 0)"
BILL_LEFT = "(" + BILL_COST + ") + prior.map(r, r.amount).sum() - event.amount"
MOVED = "event.vendor_bill != '' && bl != null ? event.amount : 0"
SALES_RETURN = "event.direction == 'receive' && event.method == 'sales_return' && ret != null && ret.return_total > 0"
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
        extra=["  - find: sales_return", "    if: event.method == 'sales_return' && event.sales_return != ''",
               "    where: name == event.sales_return", "    as: ret"],
        # Receive: cash or bank (Potong retur: retur penjualan, its PPN off PPN keluaran) and the PPh 23 the customer
        # withheld, against piutang. Pay: utang usaha against cash or bank.
        lines=[("event.direction == 'receive' ? (" + CASH + ") : '21100'",
                "event.direction == 'receive' ? (" + SALES_RETURN + " ? " + RETURN_VALUE + " : event.amount) : event.amount + event.withheld_total"),
               ("'21200'", SALES_RETURN + " ? event.amount - " + RETURN_VALUE + " : 0"),
               ("event.direction == 'receive' ? '11510' : '21300'",
                "event.direction == 'receive' ? event.withheld_total : 0 - event.withheld_total"),
               ("event.direction == 'receive' ? '11300' : (" + CASH + ")",
                "0 - event.amount - event.withheld_total")]),
    "goods_receipt": dict(
        label="Jurnal terima barang", date="event.received_on", outlet="event.outlet", party="event.vendor",
        cond="app_installed('stock')",
        # Stock in at the receipt value; payable not yet billed, or opening equity for the "Saldo awal" vendor.
        extra=["  - find: vendor", "    where: name == event.vendor", "    as: v"],
        lines=[("'11400'", "event.total_value"),
               ("v != null && v.vendor_name == 'Saldo awal' ? '31900' : '21150'", "0 - event.total_value")]),
    "vendor_bill": dict(
        label="Jurnal tagihan pemasok", date="event.bill_date", project="event.project", party="event.vendor",
        # Goods (received before): utang belum ditagih; a project's cost; else the expense account chosen.
        lines=[(BILL_DEBIT, "event.dpp"),
               (BILL_PPN, "event.ppn"),
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
    "pos_sale": dict(
        label="Jurnal transaksi kasir", date="event.date", outlet="event.outlet",
        # module pos: one receipt, booked like a day of outlet_sales. Cash and QRIS both wait in Kas outlet belum
        # disetor until the takings (outlet_takings) move them to Kas or Bank; revenue net of PPN, final PPh, HPP.
        lines=[("'11150'", "event.grand_total"),
               ("'41100'", "0 - (event.grand_total - event.ppn_in_sales)"),
               ("'21200'", "0 - event.ppn_in_sales"),
               ("'69100'", "event.pph_final"),
               ("'21310'", "0 - event.pph_final"),
               ("'51100'", "event.cogs_total"),
               ("'11400'", "0 - event.cogs_total")]),
    "pos_shift_close": dict(
        label="Jurnal tutup sesi kasir", date="event.close_date", outlet="event.outlet",
        # Closing the shift settles what its receipts held in Kas outlet belum disetor: the cash counted (less the float,
        # which stays in the drawer) goes to Kas, the QRIS to Bank (settled by the provider; a QRIS MDR is not taken
        # here), and the counted difference is Selisih kas (more cash a gain, less a loss). Outlets on the cashier take
        # no outlet_takings (rule there), so the difference is booked once (audit kasir H1).
        lines=[("'11100'", "event.counted_cash - event.opening_cash"),
               ("'11200'", "event.qris_total"),
               ("'11150'", "0 - (event.cash_total + event.qris_total)"),
               ("'61300'", "0 - event.cash_difference")]),
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
        # Paid from cash. When it points at a vendor bill (always one without a project), the project_ledger counts the
        # expense at its full amount, so the cost moves into the project: out of the account the bill debited (without a
        # project tag), never more than what is left of the bill's cost (BILL_LEFT, refused above it). A bill from before
        # the accounting module has no journal: the whole expense counts as paid in cash.
        extra=["  - find: gl_entry", "    if: event.vendor_bill != ''",
               "    where: voucher_type == 'vendor_bill' && voucher == event.vendor_bill && entry == 'post' && line == '1'",
               "    as: bl",
               "  - find: gl_entry", "    if: event.vendor_bill != ''",
               "    where: voucher_type == 'vendor_bill' && voucher == event.vendor_bill && entry == 'post' && line == '2'",
               "    as: bl2",
               "  - find_all: gl_entry", "    if: event.vendor_bill != ''",
               "    where: against == event.vendor_bill && voucher_type == 'project_expense'",
               "    as: prior"],
        lines=[("'51400'", "event.amount"),
               ("bl != null ? bl.account : '61900'", "0 - (" + MOVED + ")", {"project": "''", "against": "event.vendor_bill", "bill_left": BILL_LEFT}),
               ("'11100'", "0 - (event.amount - (" + MOVED + "))")]),
    "delivery_note": dict(
        label="Jurnal surat jalan", date="event.delivery_date", outlet="event.outlet", party="event.customer",
        lines=[("'51100'", "event.cogs_total"), ("'11400'", "0 - event.cogs_total")]),
    "sales_return": dict(
        label="Jurnal retur penjualan (stok)", date=MOVE_DATE, outlet="event.outlet", party="event.customer", extra=MOVES,
        lines=[("'11400'", NET), ("'51100'", "0 - " + NET)]),
    "outlet_return": dict(
        label="Jurnal retur ke stok", date=MOVE_DATE, outlet="event.outlet", extra=MOVES,
        # Goods a cashier refund put back on the shelf (contract 0022): the cost the sale's journal took out of stock
        # into HPP comes back, at the value of the return's stock moves (the sale's cost), so the books keep Persediaan
        # equal to the stock value and HPP counts a unit sold again only once (audit stock-returns r2 F1).
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
    "payroll_close": dict(
        label="Jurnal gaji periode", date="event.period_to", project="event.project", party="event.customer",
        # The attendance (timesheet) already booked its cost to Upah langsung against Utang upah. Closing the payroll
        # adds the rest of the employer's cost (extra_cost: BPJS of the employer, allowances, THR; negative in fee_only,
        # where the client funds the wages and the attendance cost leaves the project), turns the accrued wages into
        # net pay owed, and books PPh 21, BPJS of both sides and deductions (kasbon) repaid. In fee_only the client owes
        # the whole employer cost. Balances because employer cost = cash gross + employer BPJS and net pay = cash gross -
        # employee BPJS - PPh 21 - other deductions (plans/akuntansi-dasar.md, payroll session 7 Nov).
        lines=[("'51300'", "event.extra_cost"),
               ("'21500'", "event.total_timesheet_cost - event.total_net_pay"),
               ("'21320'", "0 - event.total_pph21"),
               ("'21600'", "0 - (event.total_employer_bpjs + event.total_employee_bpjs)"),
               ("'11330'", "0 - event.total_other_deduction"),
               ("'11340'", "event.billing_mode == 'fee_only' ? event.total_employer_cost : 0")]),
    "rental_booking": dict(
        label="Jurnal deposit sewa", date="event.booking_date", party="event.customer",
        # The deposit received when the booking is made is the customer's money held (uang muka pelanggan), not revenue.
        lines=[("event.deposit_method == 'cash' ? '11100' : '11200'", "event.deposit"),
               ("'21400'", "0 - event.deposit")]),
    "rental_return": dict(
        label="Jurnal pengembalian deposit sewa", date="event.return_date", party="event.customer",
        # The deposit held comes out: the late fee and damage it covers are other income, the rest is paid back.
        # What it does not cover is a draft invoice (flow rental_return_on_submit), journaled when it is submitted.
        lines=[("'21400'", "event.deposit"),
               ("'49200'", "0 - (event.covered_late + event.covered_damage)"),
               ("event.refund_method == 'cash' ? '11100' : '11200'", "0 - event.deposit_refund")]),
    "opening_balance": dict(
        label="Jurnal saldo awal", date="event.opening_date",
        lines=[("'11100'", "event.cash"), ("'11200'", "event.bank"), ("'11300'", "event.receivable"),
               ("'11400'", "event.inventory"),
               ("'12100'", "event.fixed_assets"), ("'12900'", "0 - event.accumulated_depreciation"),
               ("'21100'", "0 - event.payable"), ("'22100'", "0 - event.bank_loan"),
               ("'21900'", "0 - event.other_liabilities"), ("'31100'", "0 - event.paid_in_capital"),
               ("'31900'", "0 - (event.cash + event.bank + event.receivable + event.inventory + event.fixed_assets"
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
    for i, ln in enumerate(p["lines"], 1):
        acct, amt = ln[0], ln[1]
        own = ln[2] if len(ln) > 2 else {}       # per-line overrides of outlet/project/party ('' = none)
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
        for f in ("outlet", "project", "party", "against", "bill_left"):
            v = own.get(f, p.get(f))
            if v:
                out.append(f"      {f}: {q(v)}")
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
                f"      against: {r}.against",
                f"      lock_date: {q(LOCK_DATE)}"]
    return "\n".join(out) + "\n"


def account_options():
    return "\n".join(f"      - {{value: '{c.replace('-', '')}', label: {{id: '{c} {n}'}}}}" for c, n in ACCOUNTS)


def check_accounts():
    """Every account code written in this file is in ACCOUNTS (a typo such as 15400 for 5-1400 would post to a code
    gl_entry refuses only at submit time)."""
    import re
    known = {c.replace("-", "") for c, _ in ACCOUNTS}
    used = set(re.findall(r"'(\d{5})'", open(__file__).read()))
    unknown = sorted(used - known)
    if unknown:
        raise SystemExit(f"unknown account codes: {unknown}")


def main():
    check_accounts()
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
