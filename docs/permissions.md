# Permissions

Every top-level model in `modules/` declares `permissions:` (contract 0005 §4); child models (`*_item`, `*_line`,
`payment_allocation`, `contact_person`, `certification`, `price_list_item`) use their parent's. `owner` is implicit
and may do everything. A model without `permissions:` would be open to every member, so none is left without.

All roles live in the `users` module, which every module requires, so a permission never names a role the site lacks.
An app lists the roles it uses in `roles:` (`crm`: sales_rep, sales_manager; `jasa_proyek`: sales_manager,
finance_manager, purchasing_manager, project_admin; `fnb_cabang`: head_office, outlet_staff).

Letters: r read, c create, w write, s submit, x cancel, a amend, d delete, e export, p print, i import.
**all** = r c w d s x a e p (no import unless written). `outlet_staff` has a row scope
(`[outlet, from_outlet, to_outlet]` from `user.outlets`, contract 0010 §5): its grants apply to its own outlets only.

| Model | sales_manager | finance_manager | purchasing_manager | project_admin | head_office | outlet_staff | sales_rep |
| --- | --- | --- | --- | --- | --- | --- | --- |
| customer (with contact_person) | r c w p | r c w p | r c w p | r c w p | r c w p | r | r c w (`crm.convert_lead` makes customers) |
| item, outlet | r c w p | r c w p | r c w p | r c w p | r c w p | r | r |
| vendor, uom, uom_conversion, price_list, tax_code, currency, exchange_rate, worker | r c w p | r c w p | r c w p | r c w p | r c w p | r | |
| user | r | r | r | r | r | r | r |
| quotation, customer_order | all | r p | | r c w s (submit held by `manager_approval`) | | | |
| project, assignment, timesheet, acceptance | r | r | | all | | | |
| sales_invoice | all | all | | | | | r |
| withholding | r p | all | | | | | |
| invoice_reminder | r c w d e | r c w d e | | | | | r c |
| payment | all | all | | | r c w s p if `direction == 'pay'` (vendor payments only) | | |
| gateway_ref_claim (written by the payment flows) | r c w e | r c w e | | | r c w | | |
| purchase_request | | | all | r c w s | all | r c w s | |
| purchase_order | | r p | all | | all | | |
| vendor_bill | | r s p | all | | all | | |
| goods_receipt | | | all | | all | r c w s | |
| stock_move, stock_balance (written by hooks only) | | | | | r e | r | |
| stock_transfer | | | | | all | r | |
| stock_transfer_receipt | | | | | all | r c w s x | |
| stock_count, stock_adjustment | | | | | all | r c w s | |
| stock_adjustment_value (rows written by flows only: every field read_only; old adjustments by flow `adjustment_value_backfill`) | | | | | r c e | r c | |
| recipe | | | | | r c w d e | r | |
| outlet_sales | | | | | all + i | r c w s while `source != 'moka'`, else r | |
| lead, deal, activity | all | r | r | r | r | r | r c w on own records (`owner == user.name`) |

Approvals (contract 0007) decide on top of this: `quotation` submit → `sales_manager`; `sales_invoice` above
Rp 100.000.000 → `finance_manager`; `purchase_order` approval is a site's choice (`custom/purchase_order.rk`): demo-jasa asks `finance_manager` above
Rp 5.000.000 (not a role that raises POs, so the maker never approves), demo-fnb asks `purchasing_manager` (in practice the
owner) above Rp 5.000.000, and also holds a vendor bill above that amount unless it sits on an approved, live PO of at
least its value (a draft, held or cancelled PO counts as none), and an outgoing payment above that amount
(a PO, bill or payment in another currency than IDR always waits, whatever kurs the maker typed, and a bill does so
even on an approved PO)
(`custom/vendor_bill.rk`, `custom/payment.rk`), so the PO approval cannot be skipped by billing or paying directly.
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
