# Permissions

Every top-level model in `modules/` declares `permissions:` (contract 0005 §4); child models (`*_item`, `*_line`,
`payment_allocation`, `contact_person`, `certification`, `price_list_item`) use their parent's. `owner` is implicit
and may do everything. A model without `permissions:` would be open to every member, so none is left without.

All roles live in the `users` module, which every module requires, so a permission never names a role the site lacks.
An app lists the roles it uses in `roles:` (`crm`: sales_rep, sales_manager; `jasa_proyek`: sales_manager,
finance_manager, purchasing_manager, project_admin; `fnb_cabang`, `ritel_cabang` and `produksi_pabrik`: head_office, outlet_staff;
`grosir_distribusi`: sales_manager, finance_manager, head_office, field_sales, outlet_supervisor; `kelas_kursus`: course_admin, instructor
(its role file is in the `courses` module), finance_manager; `kasir`: cashier, head_office; `langganan_jasa`:
finance_manager, collector (its role file is in the `subscriptions` module); `jasa_proyek` also field_worker and
site_supervisor, whose role files are in the `projects` module).

Letters: r read, c create, w write, s submit, x cancel, a amend, d delete, e export, p print, i import.
**all** = r c w d s x a e p (no import unless written). `outlet_staff` has a row scope
(`[outlet, from_outlet, to_outlet]` from `user.outlets`, contract 0010 §5): its grants apply to its own outlets only.

**Member grants (rangka-os contract 0074).** Next to their one role, every member with an employee record holds the
pseudo-role `member`. A model grant to `member` must read `user.name` (the person's own records) and gives at most
r c w d s p. The tables below list role grants; these member grants come on top of them, for every role:

| Model | member (own records only) |
| --- | --- |
| payslip | r p, once released: submitted and its run closed or its pay date reached (`employee == user.name`) |
| staff_attendance, attendance_summary | r (`employee == user.name`) |
| cash_advance | r (`requested_by == user.name \|\| employee == user.name`) |
| cash_advance_balance, expense_claim, claim_settlement | r (`employee == user.name`) |
| shift_assignment | r, published days only (`employee == user.name && published`) |
| shift_swap | r (`requested_by == user.name`) |

Clocking in, claiming and asking for a kasbon stay with the roles that have them (cashier, outlet_staff, employee,
outlet_supervisor): their flows create guard records only those roles are granted.

| Model | sales_manager | finance_manager | purchasing_manager | project_admin | head_office | outlet_staff | sales_rep |
| --- | --- | --- | --- | --- | --- | --- | --- |
| customer (with contact_person) | r c w p | r c w p | r c w p | r c w p | r c w p | r | r c w (`crm.convert_lead` makes customers) |
| item | r c w p | r c w p | r c w p | r c w p | r c w p i (`item_master` import) | r | r |
| outlet | r c w p | r c w p | r c w p | r c w p | r c w p | r | r |
| item_category | r c w p | r c w p | r c w p | r c w p | r c w d p | r | r |
| vendor, uom, uom_conversion, price_list, tax_code, currency, exchange_rate | r c w p | r c w p | r c w p | r c w p | r c w p | r | |
| worker (carries pay, `hidden_for` enforced since contract 0027; still no access for sales and purchasing) | | r c w p | | r c w p | r c w p | r | |
| user | r | r | r | r | r | r | r |
| quotation, customer_order | all | r p | | r c w s (submit held by `manager_approval`) | | | |
| project (finance and sales write only for the invoice flows that keep `billed_amount`; contract terms freeze once billed) | r w | r w | | all | | | |
| acceptance | r | r | | all | | | |
| assignment (cost rates; field_worker r own, site_supervisor r own site, rates hidden from both; cost rates carry the worker's `hidden_for`) | | r | | all | | | |
| timesheet (pay and cost; `cost_rate`, `overtime_cost_rate`, `cost_amount` carry the worker's `hidden_for`) | | r | | all + i (`attendance_recap` import) | | | |
| billing_milestone (state written by flows; no delete; finance and sales write only through the invoice flows, an amount edit by them is refused) | r w | r w e | | r c w e i | | | |
| project_expense (sales only `reimbursable` rows, billed on to the client; purchasing only rows linking a `vendor_bill`, for its cancel guard) | r | all but d | r | all | | | |
| project_guard (never stored; a flow's refusal) | | c | c | | | | |
| project_ledger (`append_only`, contract 0026: rows written by flows only, nobody edits or deletes one; create is what those flows need; sales only `kind == 'revenue'`, purchasing only `kind == 'vendor'`) | r c | r c e | r c | r c e | | | |
| sales_invoice (head office: the Coretax faktur file) | all | all | | r | r e p | | r |
| credit_guard (never stored; a flow's refusal when a submit at the same moment used the customer's credit limit) | c | c | | c | c | | |
| withholding | r p | all | | | | | |
| invoice_reminder | r c w d e | r c w d e | | | | | r c |
| bank_account (rekening and static QRIS; only finance and head office change where customers pay, only finance deletes; field_sales: r) | r | r c w d e | | r | r c w e | | r |
| payment_instruction (copy made by `payment_instruction_on_submit` as the submitter; never written or deleted; not field_sales, whose row scope `salesman` it lacks) | r c e | r c e | | r | r | | r |
| bank_transaction | r c w e i | r c w d e i | | | r c w e i | | |
| payment | all | all | | | r c w s p if `direction == 'pay'` (vendor payments only) | | |
| gateway_ref_claim (written by the payment flows) | r c w e | r c w e | | | r c w | | |
| bank_transaction_claim (written by the bank_statement flows) | r c w e | r c w e | | | r c w | | |
| purchase_request | | | all | r c w s | all | r c w s | |
| purchase_order | | r p | all | | all | | |
| vendor_bill (finance exports it for the Coretax BPPU file) | | r s e p | all | | all | | |
| tax_period (Coretax downloads: an export of the period's invoices and bills) | | r c w d e p | | | r c w d e p | | |
| goods_receipt | | | all | | all + i (`opening_stock` import) | r c w s | |
| stock_move, stock_balance (written by hooks only; sales_manager reads the moves of delivery notes and sales returns, for the accounting flow of a return) | r (stock_move only) | | | | r e | r | |
| stock_transfer | | | | | all | r | |
| stock_reorder (written by flows `stock_low_alert` and `reorder_draft_po` only; docs/automation.md) | | | r e | | r e | | |
| business_summary, summary_setting (the owner's summaries and their switches: `permissions: []`, no role) | | | | | | | |
| stock_transfer_receipt | | | | | all | r c w s x | |
| stock_count, stock_adjustment | | | | | all | r c w s | |
| stock_adjustment_value (`append_only`, contract 0026: rows written by flows only, nobody edits or deletes one; old adjustments by the schedule of flow `adjustment_value_backfill`) | | | | | r c e | r c | |
| gl_entry (journal lines written by the accounting flows of whoever submits or cancels; append_only, so no one creates, edits or deletes one by hand; sales: invoice, payment, delivery and return lines; purchasing: bill and receipt lines; project admin: timesheet and project expense lines, and lines 1-2 of vendor bills (the cost a project expense on that bill moves out of); outlet staff: outlet sales, takings, receipt and stock document lines) | r c | r c e | r c | r c | r c e | r c | |
| period_close (Tutup buku; the posting flows read the lock as whoever submits) | r | all | r | r | all | r | |
| opening_balance (Saldo awal) | | all + i | | | all + i | | |
| financial_report (Laporan keuangan; numbers filled by flow) | | r c w d e p | | | r c w d e p | | |
| recipe | | | | | r c w d e | r | |
| outlet_sales | | | | | all + i | r c w s while `source == 'form'`, else r (records from Moka, a POS file or Loyverse are head office's imports) | |
| outlet_takings | | | | | all + i | r c w s | |
| consignment_settlement | | | | | all except d (a deleted cancelled settlement would free its month) | | |
| lead, deal, activity | all | r | r | r | r | r | r c w on own records (`owner == user.name`) |

### Distributor and factory (grosir_distribusi, produksi_pabrik)

`field_sales` (Salesman) has a row scope `salesman` from `[user.name]` (its own user record): it sees only the customers,
orders, invoices, payments, giro and delivery notes whose `salesman` is itself. On models without that field it reads
masters (`item`, `item_category`, `uom`, `outlet`, `user`: r). `field_sales` has `desk: false` (rangka-apps#112): it opens the work shell
on the `kunjungan` route screen (the van sale `pesanan_lapangan` is one tap away), with its lists under "Lainnya".

| Model | sales_manager | finance_manager | head_office | outlet_staff | field_sales |
| --- | --- | --- | --- | --- | --- |
| customer | as above | as above | as above | r | r (credit fields are finance's) |
| customer_order | all | r p | r p | | r c w s p (own customers; rule `customer_of_salesman`) |
| sales_invoice, payment | all | all | as above | | r p |
| bank_account | r | r c w d e | r c w e | | r |
| giro (lines made by the flow `giro_from_payment`; bounced only by cancelling the payment) | r c w e p | r c w e p | | | r |
| delivery_note, sales_return | all | r p | all | | r p |
| sales_visit (GPS check-in, rangka-apps#112; outside the shop radius waits for head office or `outlet_supervisor`; one per customer, salesman and day) | r w x e | r e | r w x e | | r c w s p (own customers; rule `own_customer`) |
| canvas_sale (van sale: stock out of the `vehicle` outlet; draft invoice and cash payment by the scheduled flow `canvas_sale_drafts` (it runs as the system: a salesman cannot create invoices); held over the credit limit; cancel is for the sales and finance managers) | r c w s x a e p | r x e p | r c w s e p | | r c w s p (own customers) |
| canvas_settlement (cash handed in against the van's day; a difference is recorded, never refused; closes the day) | r c w s x a e p | r c w s x a e p | r c w s e p | | r |
| canvas_guard (never stored; a flow's refusal) | c | c | c | | c |
| order_fulfilment (every field read_only; written by the delivery flows of whoever submits) | r c w e | r c w e | r c w e | | r c w |
| return_credit (credited value of a sales return; opened by the return's flow, written by the payment flows) | r c w e | r c w e | r c w | | |
| delivery_guard (never stored; a flow's refusal of a cancel or submit) | c | c | c | | |
| bom | | | r c w d e p | r p | |
| production_stage | | | r c w d e | r | |
| work_order | | | all | r p | |
| production_batch | | | all | r c w s p (own outlet) | |
| wage_sheet (made by the flow of whoever submits a batch; only head office reads wages) | | | r c w e p | c | |

`customer_order` approvals (module distribution): `credit_hold`, `overdue` and `over_credit_limit` → `sales_manager`
or `finance_manager`; a salesman never releases his own held order.

Approvals (contract 0007) decide on top of this: `quotation` submit → `sales_manager`; `sales_invoice` above
Rp 100.000.000 → `finance_manager`; `purchase_order` approval is a site's choice (`custom/purchase_order.rk`): demo-jasa asks `finance_manager` above
Rp 5.000.000 (not a role that raises POs, so the maker never approves), demo-fnb asks `purchasing_manager` (in practice the
owner) above Rp 5.000.000, and also holds a vendor bill above that amount unless it sits on an approved, live PO of at
least its value (a draft, held or cancelled PO counts as none), and an outgoing payment above that amount
(a PO, bill or payment in another currency than IDR always waits, whatever kurs the maker typed, and a bill does so
even on an approved PO)
(`custom/vendor_bill.rk`, `custom/payment.rk`), so the PO approval cannot be skipped by billing or paying directly.
In a PKP org a foreign-currency `vendor_bill` with PPN waits for `finance_manager` on submit (`fx_rate_check`, like the
`sales_invoice` one): its kurs sets the input PPN credited in the SPT, which must follow the KMK kurs.
An approver who submits self-approves, so a quotation by a `project_admin` and a big invoice by a `sales_manager` are held;
both hold: a PO is never approved by its maker.

Actions (`make_order`, `make_invoice`, `receive`, `make_po`, `new_revision`, `mark_outcome`) need `write` on their
model; `new_revision` also needs `cancel` (the hook cancels the quotation), so a `project_admin` cannot revise.
Hooks write the documents they make (drafts, moves, balances, outstanding) without permission checks.

Overlays (`custom/<model>.rk`) cannot change permissions (schema 0.5 overlay has no `permissions` key), so a pilot that
needs a different matrix changes these files.

`tests/day_one.py` holds the same table as `MATRIX` and checks, for each member of each site, that `/api/v1/meta`
reports exactly these grants on every model, and that each money model answers one allowed and one forbidden call per
role (2xx / 403 `permission_denied`).

### Payroll (module payroll, in jasa_proyek)

Pay and tax data belong to finance. The project admin reads them, and creates draft payslips, because the payslip of an
attendance recap is made inside the recap's submit as that person; only finance submits a payslip or closes a run.
Sales and purchasing have no access.

| Model | sales_manager | finance_manager | purchasing_manager | project_admin |
| --- | --- | --- | --- | --- |
| payroll_profile (NIK, PTKP, BPJS, pay basis) | | r c w e p i | | r |
| payroll_run (not submittable: payslips link to it; `closed`, locks and totals written by flows; no delete) | | r c w e p | | r |
| payslip | | all | | r c (draft from the recap's flow) |
| payroll_close (Tutup periode gaji: freezes totals, margin row, client invoice) | | all | | r |
| cash_advance (kasbon: Dr Piutang karyawan / Cr Kas or Bank; repaid through payslip other_deduction) | | all | | r |
| payroll_guard (never stored; a flow's refusal, also inside a recap cancel and an invoice save) | c | c | | c |

Payroll 2.0 internal runs (own staff, no project) use the same grants: finance makes, submits and closes them; the
project admin's read and draft-create grants matter only for client runs.

### Field attendance (module projects, in jasa_proyek)

Two roles with their files in the `projects` module, since their row scopes name its fields: `field_worker` (Pekerja
lapangan) with scope `employee` from `[user.name]` (the member's employee record, a `users.user` record since rangka-os
contract 0056; the owner links the worker record to it in `worker.employee`, and assignments and logs copy it), and
`site_supervisor` (Pengawas lokasi, mandor or danru) with scope `supervisor` from `[user.name]` (the sites whose
`work_site.supervisor` names their employee record, and the assignments and logs of those sites, which copy it; contract
0056 keeps a member's outlets to outlet records). A worker clocks in and out on their own logs and submits
them; a log outside the radius, without position, entered more than a day late or with overtime is held for the site
supervisor (approvals `outside_site`, `late_entry`, `overtime`, contract 0007); every log needs a clock-in photo, who must be able to read the log, so only
the supervisor of that site (or the owner) decides it. Neither role reads worker records (pay); the log carries the
worker's name, and the worker reads their site's point through their assignment, not the list of client sites. The rates
of an assignment are `hidden_for: [field_worker, site_supervisor]`. The project admin closes a period (attendance_close),
whose submit makes the draft recap timesheets; the workers' and the admin's submits run the guards, so both create
attendance_guard. `MATRIX` rows live in `tests/day_one_attendance.py`.

| Model | field_worker | site_supervisor | project_admin | finance_manager |
| --- | --- | --- | --- | --- |
| work_site (point, radius) | | r (own sites) | r c w d e p | r |
| attendance_log (no delete or cancel by the worker; a held submit is decided in Persetujuan) | r c w s (own) | r p (own sites) | all | r e |
| attendance_close (Rekap absen lapangan: makes the recap timesheets) | r (the guards read it) | r | all | r |
| attendance_guard (never stored; a flow's refusal) | c | | c | |

### Outlet staff attendance (module attendance, piece absen_karyawan)

Two roles in the `users` module: `outlet_supervisor` (Kepala outlet), row scope `outlet` from `user.outlets`, reads and
decides the held logs of their outlets (and leave, reimbursement and shifts later); `employee` (Karyawan), `desk: false`,
no scope, for staff who only clock in. Staff grants carry `if: employee == user.name`, which also filters reads: a
member sees only their own logs and summaries, never a colleague's selfie or position. `user.phone`, `email` and
`account_id` are hidden for both roles. Details in `docs/attendance.md`; `MATRIX` rows live in
`tests/day_one_staff_attendance.py`.

| Model | cashier, outlet_staff, employee | outlet_supervisor | head_office |
| --- | --- | --- | --- |
| staff_attendance (staff: no delete or cancel; a held submit is decided in Persetujuan; employee, outlet and day set once) | r c w s (own) | r p (own outlets); c w s (own, held for head office) | all but amend (d: stuck drafts) |
| attendance_period (Rekap absen karyawan; notes hidden from staff) | r (the guards read it) | r | all |
| attendance_summary (written by the period flows only; finance_manager r, for payroll_run_fill) | r (own) | r (own outlets) | r c w e p |
| staff_attendance_guard (never stored; a flow's refusal) | c | c | c |
| outlet | r (cashier, outlet_staff as before; employee: own outlets only) | r p | as before |
| user (phone, email hidden) | | r | as before |

### Courses (module courses, piece kelas_kursus)

Two roles: `course_admin` (Admin kursus, in the `users` module) runs programs, batches, students, enrollments, sessions
and drafts charges; `instructor` (Pengajar, in the `courses` module, since its row scope names the courses field
`instructor`) sees only the batches, sessions and enrollments that name their user record (`rangka user add --record`)
and takes attendance; the money fields of an enrollment and a student's payer are `hidden_for: [instructor]` (contract
0027). Submitting a charge makes the draft invoice, so it is finance's. The course admin also reads items, reads users,
and reads, creates and writes customers (a student on their own gets a customer to bill; an employee is billed to the
company). Seats and billed counts are written by flows. `MATRIX` rows live in `tests/day_one_courses.py`.

| Model | course_admin | instructor | finance_manager | sales_manager |
| --- | --- | --- | --- | --- |
| course_program | r c w e p | r | r c w e p | |
| course_batch (`seats_taken` written by the enrollment flows) | r c w e p | r p (own) | r e p | |
| student (`bill_to` hidden from the instructor) | r c w e p | r | r c w e p | |
| enrollment (no delete: set Keluar; finance writes only through the charge flows that lock the terms) | r c w e p | r (own, no money fields) | r w e p | |
| course_session (attendance) | r c w d e p | r c w p (own) | r e | |
| course_charge (no delete for anyone but the owner: a cancelled charge's key must stay) | r c w e p | | all but d | r (the invoice flows read it as the submitter) |
| course_guard (never stored; a flow's refusal) | c | | c | c |

### Subscriptions (module subscriptions, piece langganan_jasa)

`finance_manager` runs plans, areas, subscriptions and extras, submits the draft invoices and counts deposits.
`collector` (Penagih, in the `subscriptions` module, since its row scope names the field `collector`) sees only the areas,
subscriptions, status rows and collections of their own user record (`rangka user add --record`), records the cash they
collect, and reads no invoice. Bills, the month recap and every status fact are written by flows (read-only fields);
the billing flow runs as the scheduler or the owner. `sales_manager` gets what the invoice and payment flows read and
write as whoever submits. `MATRIX` rows live in `tests/day_one_subscriptions.py`.

| Model | finance_manager | collector | sales_manager |
| --- | --- | --- | --- |
| subscription_plan | r c w e p | r | |
| service_area | r c w e p | r (own) | |
| subscription (status computed; billing and dunning facts read-only; no delete: set an end date) | r c w e p | r (own) | r w (the invoice and payment flows) |
| subscription_extra (no delete; locked once billed) | r c w e p | | |
| subscription_bill (made only by flow subscription_billing) | r e p | | r (the invoice flow) |
| subscription_status_log (rows by the dunning flows) | r c e p | r (own) | r c |
| subscription_month | r e p | | |
| field_collection (locked once deposited) | r c w d e p | r c w d (own) | r (the payment flows) |
| collector_deposit (not cancelled) | r c w s e p | r | |
| subscription_guard (never stored; a flow's refusal) | c | c | c |
### Cashier (module pos, piece kasir)

One new role, `cashier` (Kasir, in the `users` module because catalogue, outlets, stock and accounting grant it reads),
with the row scope `outlet` from `user.outlets` (contract 0010 §5): a cashier sees and writes the shifts, closes and
receipts of their own outlets only, and reads the items, units, prices, outlets and the stock balance there. A sale's
shift must be the cashier's own (rule `own_shift`, by sign-in email); head office and the owner may ring up on any.
The totals and state of a shift are written by the flows of whoever pays, voids or closes (read_only fields), so the
cashier writes pos_shift; the float and outlet are fixed once open (flow `pos_shift_on_update`). Reopening a closed
shift (cancelling its Tutup sesi) is head office's. The journal flows run as the cashier, who therefore creates the
gl_entry lines of `pos_sale` and `pos_shift_close`, reads only those of `pos_sale` (the close's lines show the cash
difference; the count is blind, every money total of a shift is `hidden_for: [cashier]`), and reads period_close. A
sale reads the outlet's daily sales records (`outlet_sales`, read for the cashier) and a daily sales record reads the
outlet's receipts (`pos_sale`, read for outlet staff), so one outlet and day never has both. A discount above 20% of
a receipt waits for head office (approval `big_discount`). `MATRIX` rows live in `tests/day_one_pos.py`.

| Model | cashier | head_office |
| --- | --- | --- |
| pos_shift (not submittable: numbered when it opens; totals and state written by flows) | r c w p (own outlet) | r c w e p |
| pos_sale (void = cancel, same day, shift open, own shift; partial return = cancel + amend; outlet_staff: r) | all but e (own outlet) | all |
| pos_shift_close (cancel = reopen the shift) | r c w d s p (own outlet) | all but a |
| pos_guard (never stored; a flow's refusal; outlet_staff: c) | c | c |
| item, item_category (the counter's category tabs), uom, uom_conversion, outlet, stock_balance, period_close, outlet_sales, outlet_takings | r | (as above) |
| gl_entry | r (lines of `pos_sale`; `pos_shift_close` lines created, not read) | (as above) |
| customer (pick or sign up at the counter's checkout; money, credit and tax identity hidden, credit and terms read-only) | r c | (as before) |

### Promotions (module promotions, next to the kasir piece)

Promos are the owner's and head office's: they set them up, switch them off and make coupon batches; the cashier only
picks a live bill promo at the counter and types its code. Item promos apply by themselves from the item's promo
fields (written by flow `promotion_apply`, which runs as the system on its schedule and as head office when a promo is
saved). The promo flows of a sale run as the cashier, so the cashier reads promotions and coupons and creates
`promotion_ledger` rows; the ledger is append-only (contract 0026: no create, edit or delete by hand, the owner
included), its money is `hidden_for: [cashier]`, and it carries `sale_outlet` instead of `outlet` so a shared voucher's
uses are counted across outlets. `promotion` names its outlet `only_outlet` for the same reason (the cashier's row
scope would hide a promo of every outlet). A promo set up by the owner never waits for approval; `big_discount` looks at
the cashier's manual discounts only (`manual_discount_total`). `MATRIX` rows live in `tests/day_one_promotions.py`.

| Model | cashier | outlet_staff | head_office |
| --- | --- | --- | --- |
| promotion (a used promo cannot be deleted: its ledger rows link it; the voucher `code` is hidden from cashier and outlet staff) | r | r | r c w d e p |
| promo_code_batch (submit makes the coupons) | none | none | r c w d s e p |
| promo_code (made by the batch, every field read-only; `code` hidden from the cashier, so unissued coupons cannot be listed) | r | none | r c e p |
| promotion_ledger (append-only; written by the sale's flows) | r c (amount hidden) | none | r e |
| promo_guard (never stored; a flow's refusal) | c | none | c |


### Warehouses and stock requests (modules outlets 1.7 and stock 1.5, rangka-apps#104)

An `outlet` row is any stock location; `location_type` (store, warehouse, vehicle, quarantine, production) tells them
apart, and only stores take a plan outlet seat (`limit_scope: {outlets: is_store}`, rangka-os contract 0057 §5). A store
asks its warehouse for goods with `stock_request`; the warehouse sets `decision` (Proses or Tolak) and flow
`stock_request_decided` drafts the transfer. Only a store runs the cashier: `pos_shift.shift_at_store` and
`pos_sale.sale_at_store` (pos 1.6.0) refuse every other location type, vehicles too, since none of them takes a seat. `decision` and `reject_reason` are `read_only_for: [outlet_staff, cashier]`.

`warehouse_staff` (Staf gudang stok, role file in the `users` module, offered by `stok_outlet`, `grosir_distribusi` and
`produksi_pabrik`) has **no row scope** in v1: contract 0010 §5 lets a scoped member write only when every filled scope
field is theirs, so a member scoped to the warehouse could not submit the warehouse to store transfer a request asks
for. Costs stay hidden from it as from `outlet_staff` (`hidden_for`), and its dashboards narrow to its outlets by filter.
The flows of its documents run as it, so it also reads `item`, `uom`, `uom_conversion`, `item_category`, `vendor`,
`user`, `currency`, `exchange_rate`, `tax_code`, `outlet` and `period_close`, and creates `gl_entry` lines of
`goods_receipt`, `stock_adjustment`, `stock_count`, `stock_transfer` and `stock_transfer_receipt`.

| Model | outlet_staff | warehouse_staff | head_office |
| --- | --- | --- | --- |
| stock_request (state written by flows only) | r c w p (own outlet as requester) | r w p | r c w d e p |
| stock_transfer | r | r c w d s x p | all |
| stock_transfer_receipt | r c w s x | r c w s x | all |
| stock_count, stock_adjustment | r c w s | r c w s | all |
| stock_adjustment_value | r c | r c | r c e |
| stock_balance, stock_move | r | r (values hidden) | r e |
| goods_receipt | r c w s | r c w s | all |

### Members, points and deposit (module loyalty, next to the kasir piece)

A member (`loyalty_account`) is signed up by a cashier at the counter or by head office at the desk; its tier figures,
points, deposit, the year's spend and the tier are `read_only` and written only by the loyalty flows, which run as
whoever pays the sale, takes the top-up or submits the payment. That is why the cashier (and finance or the sales
manager for invoice payments from the deposit) holds `write` on `loyalty_account`: with every balance read-only, a
client write can only change the name and number (the customer link and the active switch are `read_only_for` the
cashier, the finance and the sales manager). The ledgers `loyalty_entry`, `deposit_entry` and `member_spend` are
append-only (contract 0026); every run of a sale's loyalty flows writes a `member_spend` row, so a flow started again by
hand is refused as a whole. A deposit refund and a
points correction are head office's; above Rp 1.000.000 or 1.000 points they wait for finance or the owner (contract
0007: head office does not hold `finance_manager`, so it never approves its own). Tiers are head office's. The member
model has no `outlet` field (`joined_outlet`), so a member is found at every outlet; the ledgers carry `outlet`, so a
cashier lists only its outlet's rows. `MATRIX` rows live in `tests/day_one_loyalty.py`.

| Model | cashier | outlet_staff | head_office | finance_manager |
| --- | --- | --- | --- | --- |
| member_tier | r | r | r c w d e | r |
| loyalty_account (balances read-only; customer and active read-only for everyone but head office; no delete: switched off instead) | r c w p | r | r c w e p | r w e |
| member_spend (append-only; one row per paid or voided member receipt) | r c | none | r c e | r e |
| loyalty_entry (append-only) | r c | none | r c e | r e |
| deposit_entry (append-only) | r c | none | r c e | r c e |
| deposit_topup (cashier: cash or QRIS on its own open shift; the desk: transfer or cash) | r c w d s x p | none | r c w d s x e p | r c w d s x e p |
| deposit_refund (approval above Rp 1.000.000) | none | none | r c w d s x e p | r e p (approves) |
| points_adjustment (approval above 1.000 points) | none | none | r c w d s x e | r e (approves) |
| loyalty_mismatch (written by the nightly check) | none | none | r c w d e | none |
| loyalty_guard (never stored; a flow's refusal) | c | none | c | c |

### Fixed assets (module fixed_assets, rangka-apps#113)

Assets and their depreciation are finance's and head office's. The purchasing manager's bill posted as "Aset tetap"
drafts the assets (flow `asset_from_vendor_bill` runs as them), so they create drafts and read the threshold; finance
completes and submits them. Warehouse staff of a rental business see which unit is which asset, never what it cost
(`hidden_for: [rental_staff]`, contract 0027). The register row (`asset_book`) and the rental unit's `asset` are written
only by this module's flows (their fields are read_only); head office writes `rental_unit` for that reason. `MATRIX`
rows live in `tests/day_one_fixed_assets.py`.

| Model | finance_manager | head_office | purchasing_manager | rental_staff |
| --- | --- | --- | --- | --- |
| fixed_asset (submit journals the purchase by source) | r c w d s x a e p i | r c w d s x a e p i | r c | r (cost, residual, depreciation hidden) |
| asset_book (written by flows only) | r c w e p | r c w e p | none | r (money hidden) |
| depreciation_run (only the latest cancels) | r c w d s x e p | r c w d s x e p | none | none |
| asset_disposal | r c w d s x a e p | r c w d s x a e p | none | none |
| asset_category | r c w d e | r c w d e | r | r |
| fixed_asset_setting | r c w | r c w | r | none |
| asset_bill_line (written by the bill's flow only; every field read-only) | r c | r c | r c | none |
| asset_guard (never stored; a flow's refusal) | c | c | c | none |

### Appointments (module appointments, piece janji_temu)

rangka-apps#118 v1. The front desk (cashier, head office, sales manager) books; only the owner and sales_manager (who
both book and make invoices) may submit an appointment with a deposit, because the deposit is a draft
`sales_invoice`. `appointment_slot` is written by the submit's flow as the submitter (hence create), and its fields are
read-only except `state`, which flow `appointment_slot_on_update` lets move one step at a time; paid and cancelled are
set by flows only. Billing runs as the cashier (it drafts the `pos_sale` on the cashier's own open shift); a `pos_sale` with an
appointment tag made by hand or by amending a voided bill is refused, and a bill is paid only with the deposit
discount the flow drafted. The cashier's
outlet scope covers the slot's `outlet`, so a cashier sees only the slots of their outlets. `service_staff` has no row
scope and no money. `MATRIX` rows live in `tests/day_one_appointments.py`.

| Model | cashier | service_staff | head_office | sales_manager | finance_manager |
| --- | --- | --- | --- | --- | --- |
| booking_resource, booking_service | r | r | r c w d e p | r c w d e p | r |
| resource_time_off | r | r | r c w d e | r c w d e | r |
| appointment_setting | r | r | r c w | r | r |
| appointment | r c w d s x a p | r | all | all | r e p |
| appointment_slot (only `state` writable; moves checked by a flow) | r c w | r w | r c w e | r c w e | r w e |
| appointment_guard (never stored; a flow's refusal) | c | c | c | c | c |

### Lots, expiry and serial numbers (modules catalogue 1.6, stock 1.6, rangka-os#260)

rangka-os contract 0070. `item.tracking` (none, lot, lot_expiry, serial) and `expiry_warning_days` are item fields, so
whoever writes items sets them. The registers `stock_lot`, `stock_lot_balance` and `serial_no` are written by the stock
hooks only: no role creates, writes or deletes a row (head office may write `stock_lot.notes`). `stock_lot` is also read
by the roles that pick a lot on a document line (`sales_manager` on delivery notes, `field_sales` who prints them).
`stock_expiry` is the expiry-soon list, written by flow `lot_expiry_digest` only. The line fields (`lot`, `lot_no`,
`expiry_date`, `made_date`, `serial_nos`) follow their document's permissions. `serial_no` is on the menu of
grosir_distribusi and produksi_pabrik only: cashier files (`outlet_sales`, `outlet_return`) do not move serials yet. `stock_move.lot` and `serial_nos` are
read-only like every move field.

| Model | head_office | purchasing_manager | sales_manager | field_sales | outlet_staff | warehouse_staff | cashier |
| --- | --- | --- | --- | --- | --- | --- | --- |
| stock_lot | r w e p (`notes` only) | r e | r | r | r | r | r |
| stock_lot_balance | r e | r e | r | | r | r | r |
| serial_no | r e | r e | r | | r | r | r |
| stock_expiry (written by flow `lot_expiry_digest` only) | r e | r e | r | | r | r | |
| stock_guard (never stored; flow `item_tracking_lock`'s refusal; also finance_manager and project_admin: c) | c | c | c | | | | |

`item.tracking` changes are checked by flow `item_tracking_lock` (inside the item's save): leaving the lot modes while a
real lot holds stock, leaving serial while a serial is in stock or in transit, and switching to serial while the item has
stock are refused. The flow reads the registers as the person saving the item, so a role that cannot read
`stock_balance` cannot switch an item to serial (head office and the owner can). Stock counts stay whole-item in v1:
a count line has no lot number (a no-lot line on a lot item would count only the no-lot bucket).
