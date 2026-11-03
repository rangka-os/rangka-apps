# Kernel hooks

Modules declare cross-record logic as **named hooks**. A hook is a Go function in the rangka-os kernel
(`kernel/internal/engine/hooks.go`); a module only names it (`on_submit`, `on_cancel`, an action's `hook`, an
automation's `do`) and lists its own hooks in `app.rk` `hooks:`. This file fixes what each hook must do, so
the kernel implements it exactly and the modules can rely on it.

Today's kernel runs a hook inside the caller's transaction. A hook that is **not implemented** is handled like this:

- `on_submit` / `on_cancel`: the kernel records `hook.skipped` in the audit log (target `<model>/<name>`) and carries on,
  so the document is submitted without the side effect;
- an action: the call returns `501 hook_not_implemented`;
- an automation: `hook.skipped` in the audit log.

`tests/day_one.py` reports each of these as a gap, not a failure. All hooks run in the document's transaction: an error
rolls the whole submit/cancel back. Money is whole rupiah and no hook consults an LLM.

## invoicing

### `ledger.post_receivable` (core hook, `sales_invoice.on_submit`)
- **Trigger:** submit of a `sales_invoice` (kinds `regular`, `down_payment`, `retention`).
- **Reads:** the invoice (`customer`, `amount_due`, `docstatus`), the customer's `outstanding`.
- **Writes:** `customer.outstanding += amount_due`. The invoice's own `paid_amount` stays 0 until payments allocate.
- **Refuses:** nothing beyond the model's rules. The credit-limit rule is a warning that the user confirms with `force`.
- **Idempotency / reversal:** runs once per submit (a submitted document does not submit again). The reversal is
  `invoicing.reverse_receivable`; an amended document posts again as a new invoice.

### `invoicing.reverse_receivable` (`sales_invoice.on_cancel`)
- **Trigger:** cancel of a submitted `sales_invoice`.
- **Reads:** the invoice (`customer`, `amount_due`, `paid_amount`).
- **Writes:** `customer.outstanding -= (amount_due - paid_amount)`, i.e. only what is still open; payment allocations have
  already reduced `outstanding` by the paid part.
- **Refuses:** `409 has_payments` when `paid_amount > 0` (cancel the payment first), and when another submitted invoice still
  names this one as `dp_invoice` or `retention_of` (cancel that one first).
- **Idempotency:** cancel happens once per document. The hook is the exact inverse of `ledger.post_receivable`, so
  submit then cancel leaves `customer.outstanding` unchanged.

### `invoicing.from_order` (action `make_invoice` on `customer_order`; declared by the orders module, needs invoicing)
- **Trigger:** the action on a submitted `customer_order` (`if: docstatus == 1`).
- **Reads:** the order and its lines; every submitted, not cancelled `sales_invoice` with `customer_order = <order>`
  and its lines.
- **Writes:** a **draft** `sales_invoice` (`customer`, `customer_order`, `po_number`, `kind: regular`) with one line per
  order line whose quantity is not yet fully invoiced: `qty = order qty - qty already invoiced` for the same item,
  `rate` and `uom` from the order line. Returns `{open: {model: sales_invoice, name}}`. Updates `customer_order.invoiced_amount`
  on submit of the invoice, not here.
- **Refuses:** `409 nothing_to_invoice` when every line is fully invoiced; `409 order_not_submitted` otherwise.
- **Idempotency:** each call creates a new draft containing only the remainder, so calling it twice before the first
  draft is submitted would produce two drafts for the same remainder: the hook reuses an existing draft for the order
  (same `customer_order`, `docstatus 0`) and returns it instead of creating another.
- **Declared in:** `modules/orders/app.rk` `hooks:` (the action lives on `customer_order`); the semantics sit here because
  the hook writes invoices.

### Not checkable in a rule (hook or kernel work)
- A DP invoice deducted by two regular invoices: each rule checks `dp_deduction <= dp_invoice.dpp_base` alone. The
  submit hook must refuse when the sum of `dp_deduction` over submitted invoices naming the same `dp_invoice` exceeds its `dpp_base`
  (`409 dp_over_deducted`). Likewise one `retention_of` may be billed once (`409 retention_billed`).

## payments

Both hooks run in the payment's submit/cancel transaction. They work per allocation row, in `idx` order, on the
`sales_invoice` (direction `receive`) or the `vendor_bill` (direction `pay`).

### `payments.allocate` (`payment.on_submit`)
- **Reads:** the payment (`direction`, `customer`/`vendor`, `amount`, `currency`/`exchange_rate` when the money sidecar is live) and its allocation rows (`sales_invoice`, `amount`, `withheld`).
- **Locks:** each target `for update`, then reloads its **stored** `outstanding` (the row's `outstanding` is only an entry-time snapshot).
- **Refuses (409):** `pph23_already_deducted` (`withheld > 0` and the stored invoice has `pph23_applies` true; the hook re-reads the invoice and never trusts the row's `pph23_applies` snapshot); `negative_allocation` (a row with `amount <= 0` or `withheld < 0`); `not_submitted` (target docstatus is not 1);
  `wrong_party` (target belongs to another customer/vendor than the payment);
  `over_allocation`, message "Alokasi melebihi sisa tagihan <name>", when `amount + withheld > stored outstanding` (checked row by row, so two rows
  on the same invoice are caught as the running outstanding drops); `over_payment` when the rows' `amount` add up to more than `payment.amount`.
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
- **Refuses:** nothing. Cancelling a payment is always allowed; an invoice cancelled meanwhile is refused by `invoicing.reverse_receivable` (`has_payments`), so the order of cancellation is payment first.

### Not checkable in a rule
- The rules compare each row's entry-time `outstanding` snapshot. Two payments drafted against the same invoice, or two rows on the same invoice, each pass their rule alone; only this hook catches them (`over_allocation` on the one submitted second).
- `withholding` (bukti potong) is a record only: it does not move money. The deduction itself is the allocation row's `withheld`.
- `vendor_bill` allocation (direction `pay`) is live: `payment_allocation.vendor_bill` is a model field, the rule `allocation_has_target` makes a `receive` row name only a `sales_invoice` and a `pay` row only a `vendor_bill`, and the row's `outstanding` default reads the bill's. A site that installs payments must also install procurement, because the link needs the `vendor_bill` model.

### Kernel gap for S3 (print helper `aging`)
Contract 0008's `aging` helper buckets `grand_total - paid_amount`. For an invoice with PPh 23 or retention that is more than the amount
the customer still owes, which is `outstanding` (`amount_due - paid_amount`). The statement format therefore sums its buckets in the template from `outstanding`.
S3 should make `aging` use `outstanding` when the model has that field.

## procurement

Flow: `purchase_request` -> `purchase_order` -> `goods_receipt` -> `vendor_bill` -> `payment` (direction `pay`). The money fields are IDR; the
`currency`/`exchange_rate`/`*_fx` money twins sit in the sidecars `modules/procurement/experimental/` (S8a).

### `procurement.post_payable` (`vendor_bill.on_submit`)
- **Reads:** the bill (`vendor`, `amount_due`).
- **Writes:** `vendor.outstanding += amount_due`. `paid_amount` stays 0 until `payments.allocate` runs for a `pay` payment.
- **Refuses:** nothing beyond the model's rules (`bill_no` and a repeated `bill_no` for the same vendor are not checked here; see below).
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
  `uom`, `packing`, `rate = est_rate`); sets the request's `state = ordered`. Returns `{open: {model: purchase_order, name}}`.
- **Order of checks:** `vendor_required` first, then the reuse of a draft.
- **Idempotency:** reuses an existing **draft** PO with `purchase_request = <request>` and returns it; once the state is `ordered` the gate answers
  `409 action_not_allowed`. Cancelling the PO does not reopen the request (the user sets it back by amending the request).
- **Refuses:** `422 vendor_required`; `409 not_open` past the gate.

### `procurement.receive` (action `receive` on `purchase_order`; gate `docstatus == 1 && received_pct < 100`)
- **Reads:** the PO and its lines (`qty`, `received_qty`).
- **Writes:** a **draft** `goods_receipt` with `vendor`, `outlet`, `purchase_order` and, per line whose `received_qty < stock_qty`, the not-yet-received
  remainder: `qty` is the remainder in the line's own unit (`(stock_qty - received_qty) / factor` when `packing` is set), `uom`, `packing`, `rate` as ordered.
  Returns `{open: {model: goods_receipt, name}}`. `received_qty` and `received_pct` are written on goods_receipt **submit** by `stock.post_moves`, not here.
- **Idempotency:** reuses an existing draft goods receipt for the PO.
- **Refuses:** `409 nothing_to_receive` when every line is fully received; `409 order_not_submitted` past the gate.

### `stock.post_moves` / `stock.reverse_moves` on `goods_receipt`
- Declared by the model (`on_submit: [stock.post_moves]`, `on_cancel: [stock.reverse_moves]`) and implemented with the stock module (Task 8); the semantics of the
  moves are in the stock section. For a goods receipt the hook also adds each line's `stock_qty` to the PO line's `received_qty` and sets the PO's
  `received_pct = round(sum(received_qty) / sum(stock_qty) * 100)` (capped at 100). Reversal subtracts the same.
  Until the stock module exists the kernel records `hook.skipped` and the receipt is only a document.
- `stock.reverse_moves` is not a core hook, so `procurement/app.rk` lists it in `hooks:`.

### Not checkable in a rule
- A `packing` must belong to the line's `item` (`uom_conversion.item == item`): CEL does not evaluate link lookups inside `map`, so no model rule can check it. Suggested hardening, not in the S8c plan: the hooks that read the line (`stock.post_moves`, `procurement.receive`) refuse `422 packing_mismatch`.
- A vendor's `bill_no` should be unique per vendor; suggested hardening, not in the S8c plan: `procurement.post_payable` refuses `409 duplicate_bill` when another submitted bill of the same vendor has it.
- A receipt larger than the PO's remainder: `stock.post_moves` accepts it (over-delivery happens) but caps `received_pct` at 100.

## quotation

### `quotation.revise` (action `new_revision` on `quotation`)
- **Trigger:** the action on a submitted quotation whose outcome is open (`if: docstatus == 1 && (outcome == 'open' || outcome == '')`).
  An empty `outcome` means open: a generic amend zeroes the read-only field, and such a quotation must not get stuck.
- **Reads:** the quotation and its lines.
- **Writes:** cancels the submitted quotation and creates its amendment (cancel + amend, `amended_from` = the old one) as a
  **draft** with the same header and lines, `revision = old revision + 1` and `outcome = open` (`lost_reason` cleared).
  The kernel's amend zeroes every `read_only` field (`revision`, `outcome`, `lost_reason` are read-only), so the hook sets
  `revision = old + 1` and `outcome = 'open'` explicitly on the new draft. The model's rule `draft_outcome_open` also
  accepts an empty `outcome` on a draft, so an amended draft saves even before the hook's values land.
  Returns `{open: {model: quotation, name}}`. The user edits the draft and submits it, which asks for the manager's
  approval again.
- **Refuses:** the action `if` gate answers first: `409 action_not_allowed` for an accepted or lost quotation (make a new one) and
  for a cancelled or draft one. The hook-level codes `409 not_open` and `409 already_cancelled` only apply when the hook is
  reached past the gate (a direct call from another hook or the queue); keep them as a second line of defence.
- **Idempotency:** the old document is cancelled, so a second call finds it not submitted and is refused. Linked draft orders
  keep pointing at the cancelled quotation until they are edited; submitting an order whose quotation is cancelled is not blocked here.

### `quotation.mark_outcome` (action `mark_outcome` on `quotation`)
- **Why a hook:** a submitted document cannot be edited (`PATCH` answers `409`, verified in `tests/day_one.py`), but a sent
  quotation is won or lost after submit. This hook is the one controlled way to write on a submitted quotation.
- **Trigger:** the action on a submitted quotation whose outcome is open (`''` counts as open, as above). Args: `{outcome: accepted|lost, lost_reason}`.
- **Writes:** sets `outcome` and `lost_reason` and nothing else; every other field is left as submitted, the snapshot hash
  of the approval is not touched. Audit entry `quotation.outcome`.
- **Refuses:** `422` when `outcome` is not `accepted` or `lost`, or when `lost` comes without `lost_reason`;
  outside the gate (`409 action_not_allowed` on an accepted or lost quotation), the hook itself answers `409 not_open`.

## orders

### `orders.from_quotation` (action `make_order` on `quotation`; declared in `modules/quotation/app.rk`, implemented with orders)
- **Trigger:** the action on a submitted quotation with `outcome == 'accepted'` (gate: `if: docstatus == 1 && outcome == 'accepted'`, so
  any other outcome, including `''`, answers `409 action_not_allowed` first). Args from the call body (same pattern as
  `mark_outcome`): `{po_number, po_date}`, the customer's PO.
- **Reads:** the quotation, its lines, existing `customer_order` records with `quotation = <this>`.
- **Writes:** a **draft** `customer_order` with `customer`, `quotation` and one line per quotation line (`item`, `description`,
  `qty`, `uom`, `rate`), `po_number` and `po_date` from the args (both are required on the order, so a draft cannot exist
  without them). Returns `{open: {model: customer_order, name}}`.
- **Order of checks:** the `po_number`/`po_date` args check (`422 po_required`) runs first, before looking for a draft to reuse,
  so a call without a PO is refused even when a draft exists.
- **Idempotency:** reuses an existing **draft** `customer_order` for the quotation and returns it; creates a new one only when
  none exists (when reusing, `po_number`/`po_date` of the draft are left as they are). A submitted order for the quotation does not block a new draft (partial orders are allowed).
- **Refuses:** `422 po_required` when `po_number` or `po_date` is missing (the hook never invents a PO number);
  `409 not_accepted` only past the gate (as with the hook codes above); `409 orders_not_installed` when the orders module is absent.

## workforce

### Computed `certification.expired` goes stale (kernel gap, no hook)
`expired` is a compute (`expires_on != '' && expires_on < today()`) and computes are evaluated at save, so a certification
saved while valid stays `expired = false` after `expires_on` passes. The kernel needs either computed-on-read for
date-dependent computes or a scheduled recompute (a flow with `schedule:` that touches rows crossing their date). Until then
only a save of the worker refreshes it; `tests/day_one.py` asserts the value right after creation.
