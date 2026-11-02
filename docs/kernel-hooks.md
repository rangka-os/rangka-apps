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

### `invoicing.from_order` (action `make_invoice` on `customer_order`, added with orders)
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

### Not checkable in a rule (hook or kernel work)
- A DP invoice deducted by two regular invoices: each rule checks `dp_deduction <= dp_invoice.dpp_base` alone. The
  submit hook must refuse when the sum of `dp_deduction` over submitted invoices naming the same `dp_invoice` exceeds its `dpp_base`
  (`409 dp_over_deducted`). Likewise one `retention_of` may be billed once (`409 retention_billed`).
