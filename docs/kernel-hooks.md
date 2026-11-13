# Kernel hooks

Modules declare cross-record logic as **named hooks**. A hook is a Go function in the rangka-os kernel
(`kernel/internal/engine/hooks.go`); a module only names it (`on_submit`, `on_cancel`, an action's `hook`, an
automation's `do`) and lists its own hooks in `module.rk` `hooks:`. This file fixes what each hook must do, so
the kernel implements it exactly and the modules can rely on it.

Every hook named below is built in the kernel: S8c ([rangka-os#15](https://github.com/rangka-os/rangka-os/pull/15), merged) for the hooks, S8e ([rangka-os#24](https://github.com/rangka-os/rangka-os/pull/24), merged) for two-step transfers and for reading `dpp`/`down_payment` in the ledger hooks. The `org` and `tax` CEL globals the modules rely on come from S8d ([rangka-os#20](https://github.com/rangka-os/rangka-os/pull/20), merged); all three are on rangka-os main 9c206b4, with S3 (prints render). The code is `kernel/internal/engine/{ledger,conversions,stock,transfers}.go`. The kernel runs a hook inside the caller's transaction. A hook that is **not implemented** is handled like this:

- `on_submit` / `on_cancel`: the kernel records `hook.skipped` in the audit log (target `<model>/<name>`) and carries on,
  so the document is submitted without the side effect;
- an action: the call returns `501 hook_not_implemented`;
- an automation: `hook.skipped` in the audit log.

On a kernel older than main 9c206b4 `tests/day_one.py` reports each of these as a failure: the streams are merged, so a skipped hook is a regression. All hooks run in the document's transaction: an error
rolls the whole submit/cancel back. A refusal inside a hook is always `409` with the code named below, except the three argument checks that answer `422` (`po_required`, `vendor_required`, `outlet_required`). Money is whole rupiah and no hook consults an LLM.

## Foreign-currency documents

Hooks read only the IDR fields (`amount_due`, `grand_total`, `outstanding`, `paid_amount`, allocation `amount`), never `*_fx`. For a
document whose `currency` is not IDR, the model sets the IDR `subtotal` to the IDR twin of `subtotal_fx` (contract 0013); `dpp`,
PPN (assessed in rupiah), `grand_total`, retention, PPh 23 and `amount_due` stay IDR computes from it. Lines are entered with `rate_fx`; the
IDR line `amount` (qty x rate) cannot see the parent's currency, so a foreign header ignores it. The `*_fx` header totals are
informative. A foreign payment's IDR `amount` is `round(amount_fx x exchange_rate)`, so allocation rows stay IDR.

Choice for adjustments (review item 6): `discount_amount`, `down_payment` (sales_invoice) and `pph23_amount` (vendor_bill) are entered in
**rupiah only**, also on a foreign document; their foreign twins were removed, and `dpp_fx` / `amount_due_fx` derive from them as
`IDR / exchange_rate`. Every money document (`quotation`, `sales_invoice`, `purchase_order`, `vendor_bill`, `payment`) refuses a foreign
`currency` with `exchange_rate <= 1` (rule `foreign_needs_rate`). Prints of `quotation`, `sales_invoice` and `purchase_order` show `rate_fx`,
`amount_fx` and the totals in the document currency, the kurs, then the rupiah totals.

The conversion hooks (`orders.from_quotation`, `invoicing.from_order`) do not carry `currency`/`exchange_rate` (kernel gap, S8c
follow-up). `customer_order` has no currency, so it refuses a foreign quotation (rule `quotation_in_rupiah`); a foreign invoice is
created directly. A CEL rule cannot tell that a draft was made by a hook, so this is the only guard.

## invoicing

### `ledger.post_receivable` (core hook, `sales_invoice.on_submit`)
- **Trigger:** submit of a `sales_invoice` (kinds `regular`, `down_payment`, `retention`).
- **Reads:** the invoice (`customer`, `amount_due`, `docstatus`), the customer's `outstanding`.
- **Writes:** `customer.outstanding += amount_due`. The invoice's own `paid_amount` stays 0 until payments allocate.
- **Also writes:** when the invoice names a `customer_order`, `customer_order.invoiced_amount += subtotal` (reversed on cancel).
- **Refuses (409):** `dp_over_deducted` when the `down_payment` of the submitted invoices naming the same `dp_invoice` (this one included) adds up to more than that invoice's `dpp`;
  `retention_billed` when another submitted invoice already has the same `retention_of`. Both lock the referenced invoice first, so two concurrent submits cannot both pass.
  The credit-limit rule is a warning that the user confirms with `force`.
- **Idempotency / reversal:** runs once per submit (a submitted document does not submit again). The reversal is
  `invoicing.reverse_receivable`; an amended document posts again as a new invoice.

### `invoicing.reverse_receivable` (`sales_invoice.on_cancel`)
- **Trigger:** cancel of a submitted `sales_invoice`.
- **Reads:** the invoice (`customer`, `amount_due`, `paid_amount`).
- **Writes:** `customer.outstanding -= (amount_due - paid_amount)`, i.e. only what is still open; payment allocations have
  already reduced `outstanding` by the paid part.
- **Refuses:** `409 has_payments` when `paid_amount > 0` (cancel the payment first); `409 has_dependents` when another submitted invoice still
  names this one as `dp_invoice` or `retention_of` (cancel that one first).
- **Also writes:** `customer_order.invoiced_amount -= subtotal` when the invoice names an order.
- **Idempotency:** cancel happens once per document. The hook is the exact inverse of `ledger.post_receivable`, so
  submit then cancel leaves `customer.outstanding` unchanged.

### `invoicing.from_order` (action `make_invoice` on `customer_order`; declared by the orders module, needs invoicing)
- **Trigger:** the action on a submitted `customer_order` (`if: docstatus == 1`).
- **Reads:** the order and its lines; every submitted, not cancelled `sales_invoice` with `customer_order = <order>`
  and its lines.
- **Writes:** a **draft** `sales_invoice` (`customer`, `customer_order`, `po_number`, `kind: regular`) with one line per
  order line whose quantity is not yet fully invoiced: `qty = order qty - qty already invoiced` (billed quantities go first
  to order lines with the same item and line marks, then to the item's other lines whatever their marks, filling the
  order's lines in order, so a mark changed on a draft invoice never bills a line twice), `rate`, `uom` and `description` from the order line, and the line
  marks `pph23_exclude` and `ppn_exclude` (the workers' wages line) when both line models have them.
  `orders.from_quotation` carries the same marks from the quotation lines to the order lines (rangka-os
  fix/conversions-ppn-exclude). An order made before the marks existed has none: mark its wages line on the draft
  invoice before submitting (a PT with `tax.ppn_fee_only` would otherwise charge PPN on the wages). Returns `{open: {model: sales_invoice, name}}`. Updates `customer_order.invoiced_amount`
  on submit of the invoice, not here.
- **Refuses:** `409 nothing_to_invoice` when every line is fully invoiced; `409 order_not_submitted` when the order is not submitted (past the action gate).
- **Idempotency:** the hook reuses an existing draft for the order (same `customer_order`, `docstatus 0`) and returns it instead of creating another
  (checked under an advisory lock, so two concurrent calls make one draft). A new draft only exists once the previous one is submitted or deleted.
- **Declared in:** `modules/orders/module.rk` `hooks:` (the action lives on `customer_order`); the semantics sit here because
  the hook writes invoices.

### Not checkable in a rule (done by the hook)
- A DP invoice deducted by two regular invoices: each rule checks `down_payment <= dp_invoice.dpp` alone, so `ledger.post_receivable` adds up the
  deductions (`409 dp_over_deducted`). Likewise one `retention_of` may be billed once (`409 retention_billed`).
- The hook reads the fields `down_payment` and `dpp` (it falls back to the old names `dp_deduction`/`dpp_base` only on a catalogue that has no `down_payment`/`dpp`).

## payments

Both hooks run in the payment's submit/cancel transaction. They work per allocation row, in `idx` order, on the
`sales_invoice` (direction `receive`) or the `vendor_bill` (direction `pay`).

### `payments.allocate` (`payment.on_submit`)
- **Reads:** the payment (`direction`, `customer`/`vendor`, `amount`, `currency`/`exchange_rate` (`amount_fx` is the source of truth for a foreign payment: `amount` is its IDR value)) and its allocation rows (`sales_invoice`, `amount`, `withheld`).
- **Locks:** each target `for update`, then reloads its **stored** `outstanding` (the row's `outstanding` is only an entry-time snapshot).
- **Refuses:** a row without its invoice/bill link is a `422` validation error (`allocations.<i>.<link>` required); `409`: `pph23_already_deducted` (`withheld > 0` and the stored invoice has `pph23_applies` true; the hook re-reads the invoice and never trusts the row's `pph23_applies` snapshot); `negative_allocation` (a row with `amount <= 0` or `withheld < 0`, checked before any lock); `not_submitted` (target docstatus is not 1);
  `wrong_party` (target belongs to another customer/vendor than the payment);
  `over_allocation`, message "Alokasi melebihi sisa tagihan <name>", when `amount + withheld > stored outstanding` (checked row by row, so two rows
  on the same invoice are caught as the running outstanding drops); `over_payment` when the rows' `amount` add up to more than `payment.amount` (also checked before any lock). Targets are locked in name order, so two payments over the same invoices cannot deadlock.
- **Writes:** per row `target.paid_amount += amount + withheld` (so `outstanding` recomputes and `status`/paid state follows);
  `customer.outstanding -= amount + withheld` (`vendor.outstanding` for pay); `applied_idr = amount` on the allocation row.
- **Currency:** the hook converts nothing. Allocation rows (`amount`, `withheld`, `applied_idr`) and the live `payment.amount` are always IDR.
  For a foreign payment the conversion happened once, at the header: `amount_fx` (money, source of truth) with `exchange_rate` gives
  `amount = round(amount_fx x exchange_rate)` (half away from zero, contract 0013's `_idr` twin). Receivables are tracked in IDR.
- **PPh 23:** an invoice with `pph23_applies` already nets the tax in `amount_due`, so cash `amount_due` settles it and the bukti potong is a
  `withholding` record. A row's `withheld` is only for an invoice where `pph23_applies` is false but the customer withheld anyway; the model rule
  `no_double_pph23` ("PPh 23 sudah dipotong di faktur ini") refuses `withheld > 0` on a PPh 23 invoice.
- **Idempotency:** once per submit.

### `payments.unallocate` (`payment.on_cancel`)
- The exact inverse: for each row `target.paid_amount -= applied_idr + withheld` and `party.outstanding += applied_idr + withheld`. It reads the stored `applied_idr`, never recomputes from a rate.
- **Refuses:** nothing beyond the same missing-link `422`. Cancelling a payment is always allowed; an invoice cancelled meanwhile is refused by `invoicing.reverse_receivable` (`has_payments`), so the order of cancellation is payment first.

### Not checkable in a rule
- The rules compare each row's entry-time `outstanding` snapshot. Two payments drafted against the same invoice, or two rows on the same invoice, each pass their rule alone; only this hook catches them (`over_allocation` on the one submitted second).
- `withholding` (bukti potong) is a record only: it does not move money. The deduction itself is the allocation row's `withheld`.
- `vendor_bill` allocation (direction `pay`) is live: `payment_allocation.vendor_bill` is a model field, the rule `allocation_has_target` makes a `receive` row name only a `sales_invoice` and a `pay` row only a `vendor_bill`, and the row's `outstanding` default reads the bill's. The payments module therefore `requires` procurement (the link needs the `vendor_bill` model).

### Print helper `aging` (see Kernel gaps)
Contract 0008's `aging` helper buckets `grand_total - paid_amount`. For an invoice with PPh 23 or retention that is more than the amount
the customer still owes, which is `outstanding` (`amount_due - paid_amount`). The statement format therefore sums its buckets in the template from `outstanding`.
Listed in the Kernel gaps table.

## procurement

Flow: `purchase_request` -> `purchase_order` -> `goods_receipt` -> `vendor_bill` -> `payment` (direction `pay`). The money fields are IDR; the
`currency`/`exchange_rate`/`*_fx` money twins are `money` fields on `purchase_order`, `vendor_bill` and their lines (contract 0013).

### `procurement.post_payable` (`vendor_bill.on_submit`)
- **Reads:** the bill (`vendor`, `amount_due`).
- **Writes:** `vendor.outstanding += amount_due`. `paid_amount` stays 0 until `payments.allocate` runs for a `pay` payment.
- **Refuses:** nothing beyond the model's rules (a repeated `bill_no` for the same vendor is not checked; see below).
- **Idempotency:** once per submit.

### `procurement.reverse_payable` (`vendor_bill.on_cancel`)
- **Refuses:** `409 has_payments` when `paid_amount > 0` (cancel the payment first), as `invoicing.reverse_receivable` does.
- **Writes:** `vendor.outstanding -= (amount_due - paid_amount)`, the exact inverse of `post_payable`.

### `procurement.make_po` (action `make_po` on `purchase_request`; gate `docstatus == 1 && state == 'open'`)
- **Args (call body):** `{vendor}`. **The hook leaves the vendor to the user, but `purchase_order.vendor` is required,** so a draft cannot exist
  without it: the action takes the vendor from the call body, like `orders.from_quotation` takes the PO number, and answers
  `422 vendor_required` when it is missing or names no vendor. The hook never picks a vendor.
- **Reads:** the request and its lines.
- **Writes:** a **draft** `purchase_order` with `vendor` (from the args), `purchase_request`, `outlet`, and one line per request line (`item`, `qty`,
  `uom`, `packing`, `rate = est_rate`); sets the request's `state = ordered` (only when a new draft is made; a reused draft leaves `state` alone). Returns `{open: {model: purchase_order, name}}`.
- **Order of checks:** `vendor_required` first, then the reuse of a draft.
- **Idempotency:** reuses an existing **draft** PO with `purchase_request = <request>` and returns it; once the state is `ordered` the gate answers
  `409 action_not_allowed`. Cancelling the PO does not reopen the request (the user sets it back by amending the request).
- **Refuses:** `422 vendor_required`; `409 not_submitted` when the request is not submitted (past the gate). The hook itself does not test `state`; the gate does.

### `procurement.receive` (action `receive` on `purchase_order`; gate `docstatus == 1 && received_pct < 100`)
- **Reads:** the PO and its lines (`qty`, `received_qty`).
- **Writes:** a **draft** `goods_receipt` with `vendor`, `outlet`, `purchase_order` and, per line whose `received_qty < stock_qty`, the not-yet-received
  remainder: `qty = qty - received_qty`, both in the PO line's own unit (`received_qty` is kept in that unit), with the line's `uom`, `packing` and `rate` as ordered.
  Returns `{open: {model: goods_receipt, name}}`. `received_qty` and `received_pct` are written on goods_receipt **submit** by `stock.post_moves`, not here.
- **Idempotency:** reuses an existing draft goods receipt for the PO.
- **Refuses:** `422 outlet_required`, "Outlet tujuan wajib diisi di PO sebelum barang diterima", when the PO has no `outlet` (`purchase_order.outlet` is optional, `goods_receipt.outlet` is required, and the hook copies it); `409 nothing_to_receive` when every line is fully received; `409 not_submitted` when the PO is not submitted (past the gate). `outlet_required` is checked before a draft is reused.

### `stock.post_moves` / `stock.reverse_moves` on `goods_receipt`
- Declared by the model (`on_submit: [stock.post_moves]`, `on_cancel: [stock.reverse_moves]`); the semantics of the
  moves are in the stock section below. For a goods receipt the hook also adds each line's `stock_qty` to the PO line's `received_qty` and sets the PO's
  `received_pct = 100 x sum(min(received_qty, qty)) / sum(qty)` rounded to two decimals (so an over-delivery caps at 100). Receipt lines go to the PO lines of the same item in order, converted through `stock_qty` into the PO line's unit. Reversal subtracts the same.
  Without the stock module installed (no `stock_move` model) the hook returns a message and writes nothing, so the receipt is only a document.
- `stock.reverse_moves` is not a core hook, so `modules/procurement/module.rk` lists it in `hooks:`.

### Not checkable in a rule
- A `packing` must belong to the line's `item` (`uom_conversion.item == item`): CEL does not evaluate link lookups inside `map`, so no model rule can check it. **Not built:** no hook checks it (`packing_mismatch` does not exist in the kernel); a mismatched packing is only caught when someone reads the stock quantities.
- A vendor's `bill_no` should be unique per vendor; **Not built:** `procurement.post_payable` does not check it (`duplicate_bill` does not exist in the kernel).
- A receipt larger than the PO's remainder: `stock.post_moves` accepts it (over-delivery happens) but caps `received_pct` at 100.

## stock

### `stock.post_moves` (core hook, `on_submit` of `goods_receipt`, `stock_transfer`, `stock_transfer_receipt`, `stock_count`, `stock_adjustment`, `outlet_sales`) and `stock.reverse_moves` (`on_cancel`)
- **Models it writes:** `stock_move` (one per document line and item affected) and `stock_balance` (one per outlet and item, created on the first move, row-locked).
  Both models are `read_only` for every role: `permissions:` give `head_office` and `outlet_staff` only `read`, so only owner (and the hooks, which are not
  scoped or permission-checked, contract 0010 §5) can create, change or delete them. Quantities are in the item's `stock_uom`: transfer, adjustment and receipt lines
  go through their computed `stock_qty`; sales, recipe and count lines are already in stock units.
- **Per move:** `posting_date` (the document's date), `outlet`, `item`, signed `qty`, `unit_cost`, signed `value`, `balance_qty`, `balance_value`, `avg_cost`,
  `negative` (`balance_qty < 0` after the move), `voucher_type`, `voucher` (the document's final name), `reason`.
- **Moving average and rounding:** `stock_balance.value` is the exact sum of the move values and `avg_cost = round(value / qty)` while `qty > 0` (kept otherwise).
  A receipt move's value is the line `amount` (so a package of 3 kg at Rp 100.000 is worth Rp 100.000, not 99.999). An outgoing move is `round(qty x avg)`, or
  `round(qty x item.standard_cost)` when there is no average yet. Example: 100 kg at 12.000, then 50 kg at 15.000 gives 150 kg, Rp 1.950.000, avg 13.000.
- **Per document:**
  - `goods_receipt`: `+stock_qty` into `outlet`, reason `receipt`; adds `stock_qty` to the PO line's `received_qty` (in the PO line's own unit) and sets `received_pct`.
  - `stock_transfer` (two steps, because `stock_transfer_receipt` is installed): submit takes the goods out of `from_outlet` at its average (`transfer_out`), writes `unit_cost` and
    `received_qty = 0` on each line and sets `state = in_transit`; nothing enters `to_outlet` yet. Cancel refuses `409 has_receipts` while a receipt is submitted.
  - `stock_transfer_receipt` (submitted by the receiving outlet, `outlet` = the transfer's `to_outlet`): brings the goods in at the transfer's unit cost (`transfer_in`), adds to the lines' `received_qty`
    and moves `state` to `partly_received`, then `received`. The receipt that takes an item's whole remainder gets the exact remaining value, so the in-values add up to the out-value to the rupiah. Receipt quantities are in the transfer's stock unit and are checked per item (lines of the same item add up).
    Refuses 409 `wrong_outlet` (receipt outlet is not the transfer's `to_outlet`), `not_in_transit` (transfer not submitted, already `received`, or an old one-step transfer) and `over_receipt`
    (an item's qty is more than sent minus received, or the item is not on the transfer). Cancel reverses the moves exactly and takes `received_qty` back. Without the receipt model the transfer is one step again.
  - `stock_count`: reads the balance, writes `system_qty` and `difference = counted_qty - system_qty` onto the line, and posts the difference (`count`; a zero difference posts nothing).
  - `stock_adjustment`: `-stock_qty` at the average; reason `waste` gives move reason `waste`, anything else `adjustment`.
  - `outlet_sales`: per line, the active `recipe` of the item (the most recently modified if several), each `recipe_line` consumes `line.qty x recipe_line.qty / yield_qty`
    of its material (reason `sale`); with no recipe a good, material or product consumes itself and a service is skipped. The line `cogs` is the sum of the values consumed and the
    header `cogs_total` the sum of the lines; `margin` is a compute. A line with `qty <= 0` (a refund from a POS file) posts nothing, so returned goods stay out of stock.
- **`stock_balance.last_sale_date`:** every `sale` move out of a balance sets it to the move's `posting_date` when that is later than the stored date (an earlier
  sale leaves it); sales whose document was cancelled do not count (a cancel derives the date again from the moves left), and `rangka migrate` backfills
  it from existing sale moves when the column is added. Written only when the model has the field, like `last_move`; needs rangka-os#59. The retail
  dashboard lists slow movers with `qty > 0 && last_sale_date < today() - duration('30d')`.
- **Never refuses for lack of stock:** a balance may go below zero. That is the negative-stock exception: the move and the balance carry `negative = true`, and head office
  reads them from a "Stok minus" list (`stock_balance` filtered on `negative`). The alert flow `stock_negative_alert` (`on: stock_move.created`) runs in the writer's transaction
  when the move is created (rangka-os #34); with the move as its `event` it notifies `head_office` with "Stok <item_name> di <outlet_name> minus (<balance_qty>)" (two `find` steps look the names up; the plain record names stand in when a record is not found).
- **Refuses:** `409 duplicate_sales` when another submitted `outlet_sales` has the same `outlet`, `date` and `source = form` (serialised with an advisory lock). A Moka import is protected by the import's key check instead (contract 0012). Transfers and receipts: `has_receipts`, `wrong_outlet`, `not_in_transit`, `over_receipt` (above). Nothing else refuses.
- **Reversal:** one `reversal` move per original move, opposite `qty` and `value`, at the original unit cost, dated the day of the cancel; the average is recomputed as
  `round(value / qty)` so cancelling a receipt restores value and average exactly. Line `cogs` and count `system_qty` are kept as history; a receipt's cancel takes its quantities off the PO.

### Declared stock documents (`stock:` block, rangka-os contract 0021)
`delivery_note`, `sales_return` (module delivery) and `production_batch` (module production) have no Go rule: `stock.post_moves` reads
their `stock:` block (needs rangka-os with contract 0021; on an older kernel submit answers "no rule for <model>").
- `delivery_note`: out of `outlet` (warehouse or canvas van) at the average, reason `delivery`; line `cogs` and header `cogs_total`.
- `sales_return`: good lines into `outlet`, damaged lines into `damaged_outlet`, reason `return_in`, each at the unit cost the delivery
  note took the item out at (`cost: {source: delivery_note}`).
- `production_batch`: `materials` out of `outlet` (`production_out`, line `cogs`, header `material_cost`), then `outputs` into
  `output_outlet` (`production_in`) at the value just taken out, split by `cost_share`, the last line taking the remainder (`cost: {consumed}`).
  Wages and overhead are in `hpp_total`, not in the stock value.
- **One document per chain moves stock:** the delivery note moves it, the invoice never does (`sales_invoice` has no stock rule).
  Quantities across the chain are kept in `order_fulfilment` by flows (a submitted order cannot be written): delivery notes, returns and
  invoices check against it in rules. Kernel conversions with returned qty (K-p, contract 0022) will replace it.
- Reversal is the generic `stock.reverse_moves`.

## quotation

### `quotation.revise` (action `new_revision` on `quotation`)
- **Trigger:** the action on a submitted **or cancelled** quotation whose outcome is open (`if: (docstatus == 1 || docstatus == 2) && (outcome == 'open' || outcome == '')`).
  An empty `outcome` means open: a generic amend zeroes the read-only field, and such a quotation must not get stuck.
  A cancelled one is allowed because the cancel can be held for approval (contract 0007): the hook then returns
  `{held: true, approvals, message}` and changes nothing else; once the cancel is approved the quotation is cancelled
  and the same action, run again, makes the revision.
- **Reads:** the quotation and its lines.
- **Writes:** for a submitted quotation, cancels it and creates its amendment (cancel + amend, `amended_from` = the old one) as a
  **draft** with the same header and lines, `revision = old revision + 1` and `outcome = open` (`lost_reason` cleared); for a
  cancelled one, only the amendment. The kernel's amend zeroes every `read_only` field (`revision`, `outcome`, `lost_reason` are read-only), so the hook sets
  `revision = old + 1` and `outcome = 'open'` explicitly on the new draft. The model's rule `draft_outcome_open` also
  accepts an empty `outcome` on a draft, so an amended draft saves even before the hook's values land.
  Returns `{open: {model: quotation, name}}`. The user edits the draft and submits it, which asks for the manager's
  approval again.
- **Refuses:** the action `if` gate answers first: `409 action_not_allowed` for an accepted or lost quotation (make a new one) and
  for a draft one. `409 already_revised` ("Penawaran X sudah direvisi menjadi Y") once a **submitted** amendment of the cancelled
  quotation exists. The hook-level codes only apply when the hook is reached past the gate (a direct call from another hook or the queue):
  `409 not_open` only for `outcome` `accepted` or `lost` (`open` and `''` pass), and `409 not_submitted` for a draft.
- **Idempotency:** a second call on a cancelled quotation whose amendment is still a draft answers `200` with that draft
  (`open` points at it, no second draft is made). Once the amendment is submitted the call is refused with `already_revised`.
  Linked draft orders keep pointing at the cancelled quotation until they are edited; submitting an order whose quotation is
  cancelled is not blocked here.

### `quotation.mark_outcome` (action `mark_outcome` on `quotation`)
- **Why a hook:** a submitted document cannot be edited (`PATCH` answers `409`, verified in `tests/day_one.py`), but a sent
  quotation is won or lost after submit. This hook is the one controlled way to write on a submitted quotation.
- **Trigger:** the action on a submitted quotation whose outcome is open (`''` counts as open, as above). Args: `{outcome: accepted|lost, lost_reason}` (the kernel also takes `open`, which sets it back to open and clears `lost_reason`).
- **Writes:** sets `outcome` and `lost_reason` and nothing else; every other field is left as submitted, the snapshot hash
  of the approval is not touched. Audit entry `quotation.outcome`.
- **Refuses:** `409 bad_outcome` when `outcome` is not `open`, `accepted` or `lost`; `409 lost_reason_required` when `lost` comes without `lost_reason`;
  outside the gate (`409 action_not_allowed` on an accepted or lost quotation), the hook itself answers `409 not_open`; `409 not_submitted` for a draft.

## orders

### `orders.from_quotation` (action `make_order` on `quotation`; declared in `modules/quotation/module.rk`, implemented with orders)
- **Trigger:** the action on a submitted quotation with `outcome == 'accepted'` (gate: `if: docstatus == 1 && outcome == 'accepted'`, so
  any other outcome, including `''`, answers `409 action_not_allowed` first). Args from the call body (same pattern as
  `mark_outcome`): `{po_number, po_date}`, the customer's PO.
- **Reads:** the quotation, its lines, existing `customer_order` records with `quotation = <this>`.
- **Writes:** a **draft** `customer_order` with `customer`, `quotation` and one line per quotation line (`item`, `description`,
  `qty`, `uom`, `rate` net of the line's `discount` percent, because the order line has no discount field), `po_number` and `po_date` from the args (both are required on the order, so a draft cannot exist
  without them). Returns `{open: {model: customer_order, name}}`.
- **Order of checks:** the `po_number`/`po_date` args check (`422 po_required`) runs first, before looking for a draft to reuse,
  so a call without a PO is refused even when a draft exists.
- **Idempotency:** reuses an existing **draft** `customer_order` for the quotation and returns it; creates a new one only when
  none exists (when reusing, `po_number`/`po_date` of the draft are left as they are). A submitted order for the quotation does not block a new draft (partial orders are allowed).
- **Refuses:** `422 po_required` when `po_number` or `po_date` is missing (the hook never invents a PO number);
  `409 not_submitted` and `409 not_accepted` only past the gate (as with the hook codes above). When the orders module is absent the hook returns a message and writes nothing (no error).

## workforce

### Computed `certification.expired` (no hook)
`expired` is a compute (`expires_on != '' && expires_on < today()`). The kernel recomputes computes that read `today()`/`now()` on
every read (get, list before its filter, dashboards; rangka-os #42), so `expired` turns true once `expires_on` passes without a save.
The stored column keeps the value of the last save. `tests/day_one.py` moves a stored date into the past in SQL and reads it back.

## Kernel gaps

What the modules need that the kernel does not do yet. "Harness gap" is the check in `tests/day_one.py` that reports it as a `GAP` line
(the owner it prints uses the same stream names); `none` means the harness does not assert it.

| Gap | Owning stream | Effect on the modules | Harness gap |
| --- | --- | --- | --- |
| `string()` is missing from the validator CEL | S5/engine | no `dpp_ratio` text field; documents store `dpp_num` and `dpp_den`, prints join them | none |
| Amend zeroes `read_only` fields | engine | `dpp_num`/`dpp_den` stay editable so an amended draft keeps its rate; `revision`/`outcome` are reset by `quotation.revise` | none (the amend case is checked in the quotation scenarios) |
| Print helper `dppfactor` reads `dpp_ratio`, not the document's `dpp_num`/`dpp_den` | S3 | formats join `dpp_num`/`dpp_den` themselves | none |
| Print helper `aging` buckets `grand_total - paid_amount`, not `outstanding` | S3 | the statement sums its buckets in the template from `outstanding` | none |
| `avg_cost` is whole rupiah per stock unit (`currency`) | S8e follow-up (stock) | a stock unit of gram would round cheap materials to the rupiah (Rp 12,4/g becomes Rp 12, 3% off); `fnb_cabang` onboarding offers kg, liter and pcs only, grams and sacks are `uom_conversion` packings (`balance_value` stays the exact sum of move values) | none |
| Conversion hooks drop `currency` and `exchange_rate` (`orders.from_quotation`, `invoicing.from_order`) | S8c follow-up | an order cannot be made from a foreign quotation (rule `quotation_in_rupiah`, 422); foreign invoices are created directly | none (the 422 is checked in `scenario_fx_jasa`) |
| Prints get `.Links` for top-level link fields only, not for child rows | S3 | line `description` defaults to `item.item_name`, so prints show names; the line unit prints as its `uom` code (e.g. `UOM-004`) until child-row links reach templates | none |
| `stock_balance.last_sale_date` is written only by a kernel with rangka-os#59 | stock (K-o follow-up) | on an older kernel the slow-mover widgets of `ritel_stock` stay empty (they need a date, never-sold items are not listed) | `GAP` in `scenario_ritel_sales` and `scenario_dashboards_ritel` when no balance has the date |
| No stock document moves goods back in on a sale return (`qty <= 0` posts nothing) | K-o / K-p (draft contracts 0021, 0022) | retail returns lower net sales but not stock; `ritel_overview` shows returns per day so the count corrects them | none |
| No hook fills `consignment_settlement` qty sold from the sale moves | K-o | head office types qty sold from the "Titipan terjual" chart; the test checks the chart's numbers equal the POS sales | none |
| No file export for Coretax (Digunggung XML) | B4 + X6 `coretax_xml` | `ritel_tax` shows the monthly numbers only | none |
| Imports see only `row` in CEL (no `org`, no `tax`) | S7 | import rules cannot read the PPN rate or the org; the Moka and daily-form imports carry no tax logic | none (S7 is merged: a kernel without imports makes `Moka import: upload, preview, commit, duplicates` fail) |

No row above is a `GAP` line any more: record-event flows (rangka-os #34), `read_only` on child rows, `min_rows` on save and
date-dependent computes (rangka-os #42) and the `dpp_ratio` text (rangka-os #37) are closed and checked as `OK`/`FAIL`. `stock.post_moves` / `stock.reverse_moves` on `demo-jasa` (the `stock` module is optional for
`procurement` and not installed there) are checked as no-ops (`OK`); `demo-fnb` covers the moves. A setup failure, or a hook or endpoint of
a stream merged on rangka-os main 9c206b4 (S2 approvals, S3 prints, S7 imports, S8a money, S8c/S8d/S8e hooks) that answers `501`,
`hook.skipped` or `404`, is a `FAIL`, not a gap.
