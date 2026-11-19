# What the owner gets without asking (owner automation pack)

Scheduled flows and notices that take daily checking off the owner. Everything here is `.rk`: scheduled flows
(`schedule:`), record events (`on:`), and the notices of rangka-os contract 0066 (`notices:` in `module.rk`, the
`notify` step's `notice`, `title`, `record`, `outlet`, `id`). Nothing here submits, approves, pays or sends a document
to a customer or supplier: every flow reads, writes its own small records, and tells people.

Tests: `tests/day_one_automation.py` (in `tests/day_one.py` for demo-grosir, demo-manpower, demo-fnb, demo-kasir).
Mails as a kernel without a host sends them: `python3 tests/automation_mail.py --out <dir>`.

## Channels

A notice goes to the kernel inbox (the bell) of everyone it names, and from there:

- on Rangka Cloud the host mails it by its own preferences (contract 0066 §3);
- on a kernel without a host and `notify.email` `driver: smtp`, the kernel mails it (§4.2), one plain-text mail per
  person: title, text, the link, and the line to Pengaturan › Notifikasi.

Every step says `channel: email`, which only sets the kind's mail default to on. A person switches the mail of a kind
off in Pengaturan › Notifikasi (`rk_notice_prefs`), or on the host's page when the host manages mail.

The texts are written for any short-message channel: plain text, at most 300 characters, no formatting, amounts
shortened (`12,5 juta`, `850 ribu`, rounded down), the details behind the link. A WhatsApp channel can carry the same
title and text without a change to a flow.

## Notices

| Kind | Module, flow | When | To | Severity |
| --- | --- | --- | --- | --- |
| `kernel.approval.requested` | kernel (contract 0066 §5) | a document or flow waits for approval | the policy's approvers and the owner, never the requester | warn |
| `app.invoicing.invoice_due` | invoicing, `invoice_due_digest` | 08:00 Monday to Saturday, when submitted invoices with money owed are due that day | owner, finance_manager | info |
| `app.invoicing.invoice_overdue` | invoicing, `invoice_due_digest` | the same run, only when an invoice became overdue since the last run | owner, finance_manager | warn |
| `app.stock.stock_negative` | stock, `stock_negative_alert` | a stock move leaves a balance below zero; once per balance and day | owner, head_office (people limited to other outlets are left out) | high |
| `app.stock.stock_low` | stock, `stock_low_alert` | 06:30, when a balance newly fell below its item's reorder level | owner, head_office, purchasing_manager (those who may read the reorder row) | warn |
| `app.procurement.reorder_draft` | procurement, `reorder_draft_po` | 06:40, one per draft purchase order made | owner, purchasing_manager, head_office (those who may read purchase orders) | warn |
| `app.payroll.payroll_ready` | payroll, `payroll_reminder` | 07:15, once per run: cut-off over, recaps submitted, slips to check | owner, finance_manager | info |
| `app.payroll.payroll_due` | payroll, `payroll_reminder` | 07:15, each day from three days before the pay date while the run is open | owner, finance_manager | warn |
| `app.users.daily_summary`, `weekly_summary`, `monthly_summary` | users, `summary_daily`, `summary_weekly`, `summary_monthly` | 07:00 daily, 07:05 Monday, 07:10 on the 1st | owner | info |

Times are in the site's `scheduler.timezone`. A site that wants another hour overrides the flow's `schedule:` in its
own files.

## Summaries (module `users`)

`users` is the one module every installation has, so the summaries live there and read the sales, money and stock
modules only when they are installed.

- One `business_summary` record per period (`summary_key` = period and last day, unique): sales (submitted invoices,
  cashier receipts, daily outlet sales), money in (customer payments, cashier receipts, daily takings), invoices due
  within 7 days and overdue, items below their reorder level. The record holds the exact figures; `message` is the
  short text the notice carries.
- The notice links to the record. The record links to the one `summary_setting` record, where the owner unticks the
  daily, weekly or monthly summary. Only the owner reads either model (`permissions: []`).
- A second run for the same period makes and tells nothing.
- Not in the summary yet: approvals waiting and "Mulai di sini" progress. A flow cannot read either (they are kernel
  tables, not models); the owner gets each approval request as its own notice.

## Reorder point (modules `catalogue`, `stock`, `procurement`)

- `item.reorder_level` (0 = not watched), `item.reorder_qty` (0 = order up to the level), `item.default_vendor`.
- `stock_low_alert` keeps one open `stock_reorder` row per balance below its level and closes it when the stock is back.
  Head office is told once per episode, not every morning.
- `reorder_draft_po` puts the open rows that have a supplier on one **draft** purchase order per supplier and outlet
  (quantity from the row, price = the outlet's average cost when the row opened) and ticks them `drafted`. One episode
  gives one draft: deleting the draft does not bring it back until the stock recovered and fell again. A person submits
  the draft, where the site's approval policy applies as for any purchase order.

## Payroll reminder (module `payroll`)

`payroll_reminder` reads each open `payroll_run`, its slips, the monthly recaps of its period and the field attendance
closes still in draft. Its one write is `payroll_run.ready_told`. It makes no slip, submits nothing and closes nothing.

## The day a flow runs for

The date flows (`invoice_due_digest`, `summary_*`, `payroll_reminder`) use today, or `event.as_of` (`YYYY-MM-DD`) when
someone who may run the flow starts it by hand: `POST /api/v1/flows/<name>/run` with `{"as_of": "2031-03-10"}`. That
is how the tests fix the clock, and how an owner gets the summary of a day the scheduler missed.

## Known limits

- A `channel: email` step on a kernel without a host and with SMTP queues two mails: the older `notify_email` job
  (flow label as subject) and the notice mail. Kernel follow-up; Rangka Cloud is not affected.
- The kernel sends at most 60 notices a minute per organisation and drops the rest (contract 0066 §3). A large import
  that leaves hundreds of balances below zero tells about the first 60.
- Dates in texts are `YYYY-MM-DD`; CEL has no date or money formatting.
- Cashier refunds (`pos_refund`) are not taken off the summary's sales and money in.
- On a Monday the owner gets the daily and the weekly summary, on the 1st also the monthly one (07:00, 07:05, 07:10).
  Each can be switched off in the summary settings.
