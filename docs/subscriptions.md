# Subscriptions (module `subscriptions`, piece `langganan_jasa`)

For businesses that bill the same client every period: a digital agency's retainers, an RT/RW net, a reseller of a
POS app or another licence, maintenance contracts. Site: `examples/sites/demo-langganan` (composed `langganan_jasa` +
`faktur_piutang` + `pembukuan`, a fictional RT/RW net); scenarios: `tests/day_one_subscriptions.py`. Kernel: contract
0031 of rangka-os (`add_months`, `days_between`, the `for_each` flow step, flows on `<model>.deleted`).

## Flow of a month
1. **Paket** (`subscription_plan`): monthly price, the invoice line item, the default cycle, due days and dunning ladder.
2. **Area** (`service_area`) and its **penagih** (a user with role `collector`).
3. **Langganan** (`subscription`): a client on a plan with its own cycle (monthly, every 3 months, yearly, or N months
   paid in advance), price and discount, start date and anchor:
   - *Tiap tanggal mulai*: periods start on the start date (a retainer from the 15th bills on the 15th);
   - *Tiap tanggal 1*: periods start on the 1st; a start on another day first bills the days up to the next 1st,
     pro-rated by days (start 17 Aug: 15/31 of a month for 17-31 Aug).
   Period k starts at `add_months(anchor, months billed)`, never from the previous period, so a 31st anchor does not
   drift (31 Jan, 28 Feb, 31 Mar). Price, discount and cycle can change at any time and apply from the next period; the
   start date, anchor and customer lock at the first bill. An end date stops billing for periods that start on or after
   it; the period it falls in is billed up to the day before, pro-rated by days; it cannot fall before what is billed
   (`billed_through`).
4. **Tambahan** (`subscription_extra`): a line added once (an installation fee, an extra job this month) or every period
   from a date until an optional date (a static IP, a second access point). Locked once billed except its until date and
   active flag.
5. **Tagihan langganan** (`subscription_bill`), every day at 00:05 by flow `subscription_billing` (the kernel scheduler,
   `scheduler.timezone` of system.rk; the owner can also run it from the flow list): each subscription whose next bill
   date has come gets one period billed per run, as a bill and a **draft sales invoice** dated the period's first day,
   due `due_days` after, one line for the plan and one per extra, notes tagged `{langganan:<subscription>} [<bill>]`.
   Finance checks and submits the drafts (a draft can be edited, but the only live invoice of a bill is not deleted); from there payments, reminders, aging, PPN, Coretax and bank matching are the
   money rail's (`faktur_piutang`), the journal `accounting`'s.
6. **Status layanan**, every day at 02:00 by flow `subscription_dunning`, and at once when a client's payment is
   submitted (`subscription_status_on_payment`): from the oldest submitted invoice still unpaid, *Telat* when past due,
   *Ditangguhkan* (isolir) at `suspend_after_days`, *Berhenti* when the end date has come; `stop_after_days` > 0 sets the
   end date itself. Each change is a row of `subscription_status_log` (the isolir list); the owner is told of each
   suspension. Paying lifts a suspension at once.
7. **Uang tertagih penagih** (`field_collection`): cash a collector took in the field, on a subscription of their own
   area (a collector reads no invoices). **Setoran penagih** (`collector_deposit`): finance counts the cash; it must equal
   the collections not yet deposited; each becomes a draft cash payment (notes `Setoran <deposit> [<collection>]`)
   allocated to the subscription's oldest unpaid invoices, after the drafts already waiting, and counts on the
   subscription as `deposit_waiting` until finance submits it (Kas, invoices paid).
8. **Rekap bulan** (`subscription_month`), refreshed every night: invoiced and paid of the month's subscription
   invoices, the collection rate, running subscriptions and MRR.

## Dashboards
- `langganan_ringkas` (finance): MRR, running subscriptions, billed this month, collection rate, drafts waiting, due in
  3 days, suspended, cash with collectors, arrears 1-30 / 31-60 / 61-90 / over 90 days, rate per month, subscriptions per
  status, arrears per area, the isolir list and the overdue invoices.
- `penagihan_saya` (collector, row-scoped): what the area still owes, cash in hand, the visit list (longest late first).

## Rules that guard money
- **One bill per period.** `period_key` = subscription | period start is unique; every field of a bill is read-only, no
  role creates one, so only the flow bills (the scheduler, or the owner running it).
- **One invoice per bill.** `invoice_subscription_once`: an invoice naming a bill is submitted only to the bill's
  customer, for exactly its total before PPN, with its subscription tag, and once; an invoice with a subscription tag but
  no bill is refused; the notes of a tagged invoice cannot be edited (`invoice_subscription_tag_lock`).
- **Collections within what is owed.** The collections of a subscription not yet deposited stay within its unpaid
  amount less the deposits waiting (checked when saved, under the read lock of contract 0025; not again at the deposit,
  so cash in hand always reaches Kas, a surplus becomes the client's credit). A deposited collection cannot change or
  be deleted; the payment it made cannot be deleted or cancelled, and its amount, client, method and notes stay.
- **Arrears of a stopped client.** A stopped subscription that still owes, or has a deposit waiting, is refreshed by the
  dunning and payment flows like a live one.
- **Exact deposit.** A deposit whose cash differs from the collections' total is refused, not booked with a shortage.

## Known limits
- The deposit's oldest-first window assumes the waiting drafts keep their allocations; if finance moves one, the rail
  refuses an allocation over what an invoice owes when the next is submitted.
- An invoice with a down payment (uang muka) is refused by the guard (its dpp is less than the bill); subscriptions do
  not take down payments.

## Not yet (follow-up cards)
- Submitting many drafts at once (bulk submit in the kernel UI or a flow `submit` step): today finance submits each.
- Sending reminders on WhatsApp (connector), auto-debit (Xendit Subscriptions), isolir on the router (rangka-edge, D6).
- Per-org schedules (kernel): a Juragan tenant is one org on its own kernel, so the default org's schedule is enough.
- Credit notes and refunds for a period stopped early.
