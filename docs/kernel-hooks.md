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

## quotation

### `quotation.revise` (action `new_revision` on `quotation`)
- **Trigger:** the action on a submitted quotation with `outcome == 'open'` (`if: docstatus == 1 && outcome == 'open'`).
- **Reads:** the quotation and its lines.
- **Writes:** cancels the submitted quotation and creates its amendment (cancel + amend, `amended_from` = the old one) as a
  **draft** with the same header and lines, `revision = old revision + 1` and `outcome = open` (`lost_reason` cleared).
  Returns `{open: {model: quotation, name}}`. The user edits the draft and submits it, which asks for the manager's
  approval again.
- **Refuses:** `409 not_open` when `outcome != 'open'` (an accepted or lost quotation is not revised; make a new one);
  `409 already_cancelled` when the document is cancelled.
- **Idempotency:** the old document is cancelled, so a second call finds it not submitted and is refused. Linked draft orders
  keep pointing at the cancelled quotation until they are edited; submitting an order whose quotation is cancelled is not blocked here.

### `quotation.mark_outcome` (action `mark_outcome` on `quotation`)
- **Why a hook:** a submitted document cannot be edited (`PATCH` answers `409`, verified in `tests/day_one.py`), but a sent
  quotation is won or lost after submit. This hook is the one controlled way to write on a submitted quotation.
- **Trigger:** the action on a submitted quotation with `outcome == 'open'`. Args: `{outcome: accepted|lost, lost_reason}`.
- **Writes:** sets `outcome` and `lost_reason` and nothing else; every other field is left as submitted, the snapshot hash
  of the approval is not touched. Audit entry `quotation.outcome`.
- **Refuses:** `422` when `outcome` is not `accepted` or `lost`, or when `lost` comes without `lost_reason`;
  `409 not_open` when the outcome is already set.

## orders

### `orders.from_quotation` (action `make_order` on `quotation`; declared in `modules/quotation/app.rk`, implemented with orders)
- **Trigger:** the action on a submitted quotation with `outcome == 'accepted'`. Args from the call body (same pattern as
  `mark_outcome`): `{po_number, po_date}`, the customer's PO.
- **Reads:** the quotation, its lines, existing `customer_order` records with `quotation = <this>`.
- **Writes:** a **draft** `customer_order` with `customer`, `quotation` and one line per quotation line (`item`, `description`,
  `qty`, `uom`, `rate`), `po_number` and `po_date` from the args (both are required on the order, so a draft cannot exist
  without them). Returns `{open: {model: customer_order, name}}`.
- **Idempotency:** reuses an existing **draft** `customer_order` for the quotation and returns it; creates a new one only when
  none exists (when reusing, `po_number`/`po_date` of the draft are left as they are). A submitted order for the quotation does not block a new draft (partial orders are allowed).
- **Refuses:** `422 po_required` when `po_number` or `po_date` is missing (the hook never invents a PO number);
  `409 not_accepted` when `outcome != 'accepted'`; `409 orders_not_installed` when the orders module is absent.

## workforce

### Computed `certification.expired` goes stale (kernel gap, no hook)
`expired` is a compute (`expires_on != '' && expires_on < today()`) and computes are evaluated at save, so a certification
saved while valid stays `expired = false` after `expires_on` passes. The kernel needs either computed-on-read for
date-dependent computes or a scheduled recompute (a flow with `schedule:` that touches rows crossing their date). Until then
only a save of the worker refreshes it; `tests/day_one.py` asserts the value right after creation.
