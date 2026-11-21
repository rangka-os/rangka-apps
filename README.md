# Rangka Apps

**Ready-to-use business apps for [Rangka OS](https://github.com/rangka-os/rangka-os)**, written entirely as `.rk`
metadata: models, rules, dashboards, flows and roles. No application code — the Rangka kernel turns these files into a
database, an API and screens.

> **Status: v0 (November 2026).** Twenty-one modules, six apps (`crm`, `fnb_cabang`, `jasa_proyek`, `ritel_cabang`, `grosir_distribusi`, `produksi_pabrik`) and six example sites, laid out per
> [contract 0001](https://github.com/rangka-os/rangka-os/blob/main/docs/contracts/0001-modules-and-apps.md).
> All files pass both validators with 0 errors.

Part of the Rangka family: [rangka-os](https://github.com/rangka-os/rangka-os) (kernel, schemas, Console) ·
**rangka-apps** · [rangka-connectors](https://github.com/rangka-os/rangka-connectors) ·
[rangka-ai](https://github.com/rangka-os/rangka-ai)

## What's inside

A **module** holds models, rules, flows and roles. An **app** is what a customer installs: it picks modules and adds
the menu, dashboards, home and onboarding. Apps define no models.

Every module requires `users`, which holds all roles (`sales_manager`, `sales_rep`, `finance_manager`, `purchasing_manager`,
`project_admin`, `head_office`, `outlet_staff`), so a permission never names a role a site lacks. Who may do what is in
[docs/permissions.md](docs/permissions.md).

| Module | Requires (besides `users`) | Models |
| --- | --- | --- |
| `users` | none (optional: outlets, invoicing, payments, outlet_sales, pos, stock) | `user`; every role; the owner's summaries: `business_summary`, `summary_setting`, flows `summary_daily`, `summary_weekly`, `summary_monthly` ([docs/automation.md](docs/automation.md)) |
| `contacts` | none | `customer`, `vendor` (`pkp`), `contact_person`; imports `customer_list` and `vendor_list` |
| `catalogue` | contacts | `item` (barcode, category, variants as items sharing `variant_group`, consignor and consignment cost), `item_category`, `uom`, `uom_conversion`, `price_list` (+ `price_list_item`); import `item_master`; import `item_list` (opening item list, units matched by name) |
| `outlets` | none | `outlet` |
| `invoicing` | contacts, catalogue (optional: outlets, tax_id, currency, orders, projects) | `sales_invoice` (+ `sales_invoice_item`): PPN from config, PPh 23 on the lines not marked `pph23_exclude` (wages and reimbursed costs can stay out; a down payment's base is deducted), down payment, retention (no PPN again, bounded by the origin), header discount; print format; finance approval above Rp 100.000.000; foreign-currency `*_fx` totals; numbering series `invoice`; `invoice_reminder` (who was reminded, when, how); flow `invoice_due_digest` (morning list of due invoices); print *Pesan tagihan WA* (reminder text and a `wa.me` link with the text filled in); `bank_account` (rekening and static QRIS of the business: account number digits only, QRIS text checked as EMVCo with CRC, static, rupiah, no fixed amount, no fee (55=02/03, 56, 57), NMID and merchant name read from it; one active default per org); `payment_instruction` (copy of the default account and the payment reference = invoice number, made by flow `payment_instruction_on_submit` when an invoice is submitted, so changing the account never changes an old invoice) |
| `tax_id` | none | `tax_code` |
| `currency` | none | `currency` (named by ISO code), `exchange_rate` |
| `quotation` | contacts, catalogue (optional: tax_id, currency) | `quotation` (+ `quotation_item`): line discounts only (no header discount), revisions, outcome, manager approval, numbering series `quotation`, foreign-currency `*_fx` totals, print *Surat penawaran harga* |
| `orders` | contacts, catalogue (optional: quotation) | `customer_order` (+ `customer_order_item`): customer PO, action `make_invoice`; refuses a foreign quotation |
| `workforce` | none | `worker` (selling rate apart from pay: `cost_day_rate`, overtime rates), `certification` (expiry flag); import `worker_list` (pay columns optional) |
| `projects` | contacts, workforce (optional: orders) | `project`, `assignment`, `timesheet` (Absensi: one day, or a monthly recap with `period_to` and overtime hours; `cost_missing` when no pay), `acceptance` (berita acara); import `attendance_recap` (one row per worker per project per cut-off period); field attendance: `work_site` (point and radius), `attendance_log` (clock in/out with position and photo, held for the `site_supervisor` when outside the radius, late or with overtime), `attendance_close` (period's logs to recap timesheets); roles `field_worker`, `site_supervisor` |
| `attendance` | outlets | Outlet staff attendance: `staff_attendance` (clock in/out from the phone at an outlet with position and selfie, geofence per outlet, lateness and early leave against the outlet hours in its own zone, held for `outlet_supervisor` or head office when outside, late-entered or with overtime), `attendance_period` and `attendance_summary` (monthly summary per employee), screen `absen_outlet` (clock), flow `attendance_missing_digest`; adds point, radius, zone and hours to `outlet`, `attendance_exempt` to `user`; see `docs/attendance.md` |
| `payroll` | users, workforce (optional: projects, invoicing, contacts, catalogue, outlets, attendance) | 2.0: `payroll_profile`, `payroll_run` (`run_kind` client: one client contract, margin row and client invoice at the close; internal: own staff, no project or invoice, optional outlet), `payslip` (BPJS, PPh 21 TER and Pasal 17; unpaid days and lateness deductions), `payroll_close`, `cash_advance`; imports `payroll_profile_list` and the `Email karyawan` column of `worker_list`; flow `payroll_reminder`; see `docs/payroll.md` |
| `payments` | contacts, invoicing, procurement (optional: currency) | `payment` (+ `payment_allocation`), `withholding` (PPh 23 certificate); foreign-currency `amount_fx`; methods QRIS, VA, e-wallet and `gateway_ref` held by one live payment at a time (`gateway_ref_claim`, released on cancel so the payment can be amended); hooks `payments.allocate` and `payments.unallocate`; flows `payment_from_xendit_va`, `_qr`, `_invoice` (ready, not active until the kernel runs connectors: a completed Xendit callback drafts one payment allocated to the invoice named by `external_id`, never twice for one gateway id; a cancelled or unknown invoice records nothing and tells the owner), `payment_from_doku` (DOKU Checkout notification, signature checked by the kernel: SUCCESS drafts one payment, gateway `doku`, method from the channel), `payment_from_midtrans` (Midtrans notification, `signature_key` checked by the kernel: status code 200 with settlement or an accepted capture drafts one payment, gateway `midtrans`, keyed on the signed order id, method from the payment type) and `payment_refund_from_doku`, `payment_refund_from_midtrans` (a refund tells the owner; nothing is booked); print *Statement* |
| `bank_statement` | contacts, invoicing, payments | `bank_transaction` (one statement line; money in positive): import `bank_statement_csv` (Tanggal, Urutan, Keterangan, Masuk, Keluar, Saldo, Rekening; duplicates by file and by row, the key is Rekening, Tanggal, Urutan, Keterangan, Masuk, Keluar, Saldo, and Saldo may be empty); flow `bank_transaction_suggest` (invoice number in the note, compared without case, spaces, dashes, slashes and dots, when the line is no more than that invoice still owes; else exactly one open invoice of that amount); confirming a line drafts a payment into `draft_payment` (flow `bank_transaction_confirm`), submitting a payment that names the line (`payment.bank_transaction`) sets `payment` and marks the line matched, cancelling it unmatched; only those flows set Cocok. `bank_transaction_claim` allows one live payment per line (unique, checked on submit), whatever path made the payment. A statement line never settles an invoice by itself |
| `procurement` | contacts, catalogue (optional: outlets, currency, stock) | `purchase_request`, `purchase_order`, `goods_receipt`, `vendor_bill` (+ line models): no approval in the module (a site adds its own threshold in `custom/purchase_order.rk`, contract 0007 §1), foreign-currency `*_fx` totals, print *Pesanan pembelian*, actions `make_po` and `receive`; import `opening_stock` (valued opening stock per outlet as a goods receipt from "Saldo awal"); flow `reorder_draft_po` (one draft purchase order per supplier and outlet for items below their reorder level, never submitted by the flow) |
| `stock` | contacts, catalogue, outlets | `recipe` (+ line), `stock_move`, `stock_balance`, `stock_transfer` (two steps), `stock_transfer_receipt`, `stock_count`, `stock_adjustment` (+ line models): moving-average cost, recipes, negative stock allowed and flagged; flows `stock_negative_alert` (notice to owner and head office, in the bell) and `stock_low_alert` (`stock_reorder`: one open row per balance below `item.reorder_level`, head office told once per episode) |
| `outlet_sales` | outlets, catalogue, stock | `outlet_sales` (+ line): daily sales per outlet, submit posts stock through the recipes (an item without one takes itself out), one sales source per outlet, PPN-in-sales and 0,5% final PPh per record; `outlet_takings` (+ line): daily takings per payment method against the POS; imports `moka_item_details`, `daily_form`, `pos_sales_file` (Juragan's template for any POS; `scripts/pos_export_to_template.py` turns the export of a POS without an import of its own, such as ESB, into it from a mapping file in `examples/pos-mappings`), `loyverse_receipts` (guessed headers), `takings_file`; Moka API (connector `moka`, poll per outlet): `moka_transaction` + `moka_sale_line` staged per transaction (outlet by `outlet.moka_outlet_id` or Moka name, item by SKU or name, unmapped lines mapped by head office), flow `moka_daily_sales` hourly makes one draft `outlet_sales` per outlet and past day |
| `consignment` | contacts, catalogue, outlets, stock, procurement | `consignment_settlement` (+ item): one live settlement per consignor and month (all outlets); flow `consignment_bill` keeps one `vendor_bill` per consignor and month (draft on submit, refilled from an amended settlement), `consignment_bill_cancelled` refuses to cancel a settlement whose bill is submitted. Goods come in through `goods_receipt`, go back through `stock_adjustment` reason `consignment_return` |
| `pipeline` | contacts (optional: invoicing) | `lead`, `deal`, `activity`, flows `whatsapp_to_lead` and `morning_follow_up` |
| `distribution` | contacts, catalogue, orders, invoicing, payments, procurement | `giro` (register of giro received); switches on salesman, route, price tier and credit fields of `customer`, `item`, `customer_order`, `sales_invoice`, `payment`; approvals on `customer_order` (credit hold, overdue, over the limit); flows for tier pricing, the customer's credit figures and giro; role `field_sales` |
| `delivery` | contacts, catalogue, outlets, stock, orders, invoicing | `delivery_note` and `sales_return` (+ lines; declared `stock:` blocks of contract 0021), `order_fulfilment` (ordered, delivered, returned, invoiced per order); invoices bill what was delivered; payment method "Potong retur" (`sales_return`), which a site that installs delivery adds in `custom/payment.rk` (see `examples/sites/demo-grosir`) |
| `production` | catalogue, outlets, stock, workforce (optional: orders) | `bom` (multi-level, rolled-up standard cost, by-products), `work_order` (SPK), `production_batch` (expiry, yield, actual HPP; declared `stock:` with `cost: {consumed}`), `production_stage` (piece rates), `wage_sheet` |
| `subscriptions` | contacts, catalogue, invoicing, payments, procurement | `subscription_plan`, `service_area`, `subscription` (own cycle: monthly, 3 months, yearly or N months prepaid; billed from the start date or on the 1st with a pro-rated first month; price and cycle change from the next period; status Aktif, Telat, Ditangguhkan, Berhenti computed from the oldest unpaid invoice), `subscription_extra` (once or every period), `subscription_bill` (one per period, made only by the scheduled flow `subscription_billing` with a draft sales invoice), `subscription_status_log`, `subscription_month` (collection rate, MRR), `field_collection` and `collector_deposit` (a collector's cash into draft cash payments, oldest invoices first); role `collector` (row scope). See [docs/subscriptions.md](docs/subscriptions.md) |
| `promotions` | catalogue (optional: outlets, pos, invoicing) | `promotion` (item promos: percent, rupiah off per unit, buy X get Y, by item or category; bill promos: percent with a cap, rupiah off, free gift, minimum spend; dates, days, hours, one outlet or all, priority; shared voucher code with a use limit or single-use coupons), `promo_code_batch` (makes coupons with `random_code()`), `promo_code`, `promotion_ledger` (append-only promo cost per sale, voucher and coupon uses), `promo_guard`; flows `promotion_apply` (every 5 minutes and on save or delete: live promos onto `item.promo*` for all outlets and `item.promo_o*` for one outlet), `promotion_on_sale` (window, code, voucher limit per promo, coupon use and gift checked again under the ledger lock, also for device sales; ledger rows), `promotion_on_sale_cancel`; with `pos` a sale takes item promos by itself and a bill promo at the counter's checkout (rangka-os contract 0069), with `invoicing` a percent promo is the default line discount; `scripts/gen_promotion_flows.py` writes the apply flows |
| `loyalty` | users, contacts (optional: outlets, pos, payments, accounting) | `member_tier` (Member, Silver, Gold: points per rupiah, multiplier, point value, redeem cap, member discount, expiry months; one base tier at Rp 0), `loyalty_account` (a member, one per WhatsApp number per org, linked to its customer; points, deposit, year's spend, tier figures, all flow-written), `loyalty_entry` and `deposit_entry` (append-only ledgers), `deposit_topup` (counter shift or desk), `deposit_refund` (approval above Rp 1.000.000), `points_adjustment`, `loyalty_mismatch`, `loyalty_guard`; flows `loyalty_on_sale` (member, points and deposit checked again under the member's lock, ledgers, tier up), `loyalty_on_sale_cancel`, `deposit_on_payment` (an invoice paid from the deposit), `points_expiry` (02:00, inactivity), `loyalty_yearly` (1 January review), `loyalty_check` (03:00, balances against the ledgers); with `pos` the counter's checkout takes a member, points and a deposit part (rangka-os contract 0069) and prints `pos_receipt_whatsapp` (wa.me link); accounting books the deposit as 2-1400 Uang muka pelanggan, points stay off the books |
| `fixed_assets` | users, accounting (optional: procurement, rental, outlets, projects) | `fixed_asset` (register document by source: vendor bill, old asset brought in, bought without a bill), `asset_book` (live register row written by flows: accumulated depreciation, month depreciated, state), `depreciation_run` (+ `depreciation_line`; straight-line per month with residual value, catch-up of missed months, last month takes the remainder), `asset_disposal` (sold or written off, gain or loss on one signed 4-9300), `asset_category` (useful life, residual percent, PMK 72/2023 fiscal group), `fixed_asset_setting` (capitalisation threshold, default Rp 1.000.000), `asset_bill_line` (the cost each bill line put in 1-2100, written by the bill's flow only), `asset_guard`; flows `fixed_assets_setup`, `depreciation_run_monthly` (drafts last month's run on the 1st, a person submits), `asset_from_vendor_bill` (a bill posted as Aset tetap drafts one asset per line); import `asset_list`, print `fixed_asset_register`; journals 6-1950 Beban penyusutan and 4-9300 in `scripts/gen_accounting_flows.py` |
| `whatsapp_order` | contacts, catalogue, whatsapp (optional: outlets, pos, whatsapp_receipt) | `whatsapp_order` (+ `whatsapp_order_line`): an order a customer wrote on WhatsApp, as a draft priced from the items (Rangka Cloud reads the text, flow `whatsapp_order_intake` matches the lines in CEL: same name, part of the name, spelling skeleton; several candidates are a choice, none an unknown line kept as a note), confirmed by a person (work screen `pesanan_wa`; flow `whatsapp_order_on_update`: the customer, a draft `pos_sale` on the cashier's open shift, the reply `pesanan_diterima`); `whatsapp_inbound` (each message once by Meta's id), `whatsapp_order_guard` |

| App | Modules | Also |
| --- | --- | --- |
| `crm` | contacts, catalogue, pipeline, invoicing, outlets, users | Menu, dashboards `crm_overview` and `sales_overview`, process `lead_to_cash`, WhatsApp connector, home, onboarding |
| `fnb_cabang` | contacts, catalogue, outlets, users, stock, outlet_sales, procurement, payments, invoicing (no menu entry) | Multi-branch F&B back office: menu groups Penjualan outlet, Stok, Pembelian, Data induk; dashboards `fnb_overview` (home: net sales today and over the period, margin, net by outlet and by day, the "Stok minus" list for head office) and `stock_recap` (stock value by outlet, moves by reason, monthly sales and cost per outlet and in total); roles `head_office` and `outlet_staff`; onboarding (outlets, Moka outlets, raw-material unit) |
| `ritel_cabang` | contacts, catalogue, outlets, users, stock, outlet_sales, procurement, payments, consignment, invoicing (no menu entry), bank_statement (menu Bank) | Retail back office for a shop that keeps its POS (Jalur A): menu groups Penjualan toko, Stok, Pembelian, Titipan, Data induk; dashboards `ritel_overview` (home: net sales today and over the period, gross margin and net sales per branch, returns per day, short takings, negative stock), `ritel_stock` (stock value own vs consigned, best sellers, slow movers from `stock_balance.last_sale_date`, consigned goods sold) and `ritel_tax` (monthly turnover, 0,5% final PPh, PPN Digunggung); roles `head_office` and `outlet_staff`; onboarding (branches, Moka branches, other POS, PKP) |
| `grosir_distribusi` | contacts, catalogue, outlets, users, stock, orders, invoicing, payments, procurement, distribution, delivery, bank_statement | Sub-distributor back office: menu groups Penjualan, Pengiriman, Gudang, Pembelian, Data induk; dashboards `grosir_overview` (home: sales, AR, overdue AR, orders waiting, sales and AR per salesman, giro not yet cleared, late customers) and `grosir_ar`; roles `sales_manager`, `finance_manager`, `head_office`, `field_sales`; site `demo-grosir` (three salesmen) |
| `produksi_pabrik` | contacts, catalogue, outlets, users, stock, workforce, production, procurement, payments, invoicing (no menu entry) | Small-factory back office: menu groups Produksi, Resep dan tarif, Stok, Pembelian, Data induk; dashboards `produksi_overview` (home: batches, good output, HPP, reject, HPP per product, recent batches against the standard, expiring batches, open SPK) and `produksi_upah` (weekly piece-rate wages per worker); roles `head_office`, `outlet_staff`; site `demo-produksi` (frozen food) |
| `langganan_jasa` | users, contacts, catalogue, invoicing, payments, procurement, subscriptions, bank_statement (menu Bank) | Subscription businesses (agency retainers, RT/RW net, licence resellers): menu groups Langganan, Penagihan lapangan, Pengaturan langganan; dashboards `langganan_ringkas` (home: MRR, billed this month, collection rate, arrears by age, isolir list) and `penagihan_saya` (collector); roles `finance_manager`, `collector`; site `demo-langganan` (RT/RW net) |
| `jasa_proyek` | contacts, catalogue, users, outlets, tax_id, currency, invoicing, quotation, orders, workforce, projects, payments, bank_statement, procurement | Project-services quote-to-cash: menu groups Penjualan, Proyek, Tenaga kerja, Keuangan, Pembelian, Data induk; process `quote_to_cash`; dashboards `jasa_overview` (home: open, won and lost quotations, invoices issued, outstanding AR, overdue invoices, active projects) and `ar_aging` (outstanding by age bucket, by customer, overdue list); roles `sales_manager`, `finance_manager`, `purchasing_manager`, `project_admin`; onboarding (PKP, business line, number of PTs) |
| `pesanan_whatsapp` | users, contacts, catalogue, outlets, pos, whatsapp, whatsapp_order | On top of kasir: work screen Pesanan dari WhatsApp (cashier, head office), menu Pesanan WhatsApp; needs connector `rangka_whatsapp` |

```
modules/<m>/module.rk               manifest: name, version, title, requires, optional, hooks
modules/<m>/models|roles|flows/     the module's metadata (roles only in modules/users/roles/)
modules/<m>/print/                  print formats (contract 0008; rendered by the kernel)
modules/<m>/imports/                spreadsheet imports (kind import, contract 0012); sample files in examples/fixtures/imports/
apps/<a>/app.rk, dashboards/        what a customer installs: modules, menu, dashboards, connectors, roles, home, onboarding
apps/<a>/processes/                 named stages for the process canvas (kind process, contract 0014; found by folder, not listed in app.rk)
examples/sites/<site>/              example installations: system.rk, features.rk, config.rk (+ letterhead.rk, letterheads/<org>.rk, themes/, layouts/)
examples/fixtures/<dir>/            their demo data (`demo-jasa` seeds the first PT, `demo-jasa-pelatihan` the second; see below)
scripts/                            validate.sh (both validators over every site), lib.sh (finds rangka-os and the kernel binary)
tests/day_one.py                    integration test against a live kernel (`python3 tests/day_one.py --site kopi-senja|demo-jasa|demo-fnb|demo-ritel`)
docs/kernel-hooks.md                what each hook the modules declare does in the kernel, and the table of kernel gaps
docs/permissions.md                 the permission matrix every model declares
```

The kernel and both validators read `apps/` and the `modules/` folder next to it, so there is nothing to build. A
site's `config.apps` lists apps only; the kernel installs the modules each app names, and their `requires`.

### PPN

No rate is written in a module. `tax` in `config.rk` (`ppn_rate`, `dpp_ratio`, `pkp`; an org's `orgs.<id>.tax` is deep-merged
over the site's) reaches the models as the CEL global `tax` (`ppn_rate`, `dpp_num`, `dpp_den`, `pkp`). Every taxed document
(`quotation`, `sales_invoice`, `purchase_order`, `vendor_bill`) stores `tax_rate`, `dpp_num` and `dpp_den` at creation (no `dpp_ratio` text field: `string()` is not in the CEL function set of either validator; prints join `dpp_num`/`dpp_den`), so a
submitted document keeps its rate when the config changes later. Sales side `tax_rate` is `tax.pkp ? tax.ppn_rate : 0`; purchase side
`vendor.pkp ? tax.ppn_rate : 0` (`vendor.pkp`). `ppn = round(dpp * dpp_num / dpp_den * tax_rate / 100)`; `tax_rate` 0 means no PPN.

### The demo sites

- `kopi-senja`: the CRM demo (one org).
- `demo-kasir`: the cashier demo. Restaurant charges (rangka-apps#121): outlet `OUT-05` (Restoran Contoh Braga) charges 5% service
  on dine-in orders and 10% PBJT (a regional tax outside the price, so PPN is 0) and rounds the bill to the nearest Rp 100,
  so Rp 100.000 dine-in is Rp 115.500; `OUT-06` (Kafe Contoh Setiabudi) has none. Service books to 4-1300, PBJT to 2-1330,
  rounding to 4-9400. A bill can be split between people (`parties`, one `party` per payment; "Bayar terpisah" on the counter).
- `demo-jasa`: project services under one owner with **two fictional PTs** (`orgs:` in `config.rk`, contract 0010):
  `pt_contoh_inspeksi` (code `CIN`, PKP, numbers like `INV/CIN/26/0001`) and `cv_contoh_pelatihan` (code `CPL`, non-PKP,
  numbers like `KW-CPL-2611-001`). Each has its own `letterheads/<org>.rk`; `letterhead.rk` is the fallback. Seed each org
  with its own fixtures: `rangka seed --org pt_contoh_inspeksi --fixtures examples/fixtures/demo-jasa` and
  `rangka seed --org cv_contoh_pelatihan --fixtures examples/fixtures/demo-jasa-pelatihan`, then serve with `--org pt_contoh_inspeksi`
  (the owner switches orgs with `POST /auth/org`).
- `demo-fnb`: Tahu Contoh, one org (`tahu_contoh`, code `TCN`) with three outlets (head office, Dago, Braga), a letterhead
  and PO numbers like `PO/TCN/2611/001`. Outlet staff are members with `--outlets` (`tests/day_one.py` adds them with `rangka user add`).
- `demo-ritel`: Toko Contoh, a grocery shop with two branches (org `toko_contoh`, code `TKC`, non-PKP): Cibiru imports Moka and is the
  head office, Cileunyi sends Juragan's POS file template. 200 items (192 own with barcodes, 8 consigned from three home businesses),
  11 categories, carton packings, four suppliers. `examples/fixtures/demo-ritel/generate.py` writes the seed file and, for any end date,
  40 days of POS exports, opening stock, daily takings and a new item list (`python3 generate.py --out /tmp/ritel`); the test builds them
  for today, so the 30-day dashboards and the slow-mover list have data. `python3 tests/day_one.py --site demo-ritel --keep` leaves the
  server running with that month of data for a walkthrough.

`python3 tests/day_one.py --site demo-jasa|demo-fnb|demo-ritel|kopi-senja` runs the integration test; `--only <text>` runs the
scenarios whose function name contains the text.

## Try it

Clone next to rangka-os and rangka-connectors, then run the kernel on it with a Postgres 16
database (`$URL`):

```bash
git clone git@github.com:rangka-os/rangka-os.git
git clone git@github.com:rangka-os/rangka-apps.git
git clone git@github.com:rangka-os/rangka-connectors.git
cd rangka-apps
K=../rangka-os/kernel/bin/rangka     # make -C ../rangka-os/kernel build
A="--apps apps --site examples/sites/kopi-senja --db $URL"
$K migrate $A && $K seed $A --fixtures examples/fixtures/kopi-senja && $K serve $A --addr 127.0.0.1:8080 --dev-open
```

Validate after any change (Python 3 with `pyyaml` and `jsonschema` for the reference validator, plus the Go binary;
`RANGKA_OS` points at the rangka-os checkout, found next to this repo by default; `PYTHON` picks the interpreter that has those packages):

```bash
scripts/validate.sh                      # both validators, every site in examples/sites; prints "0 errors"
scripts/validate.sh examples/sites/kopi-senja
```

Each problem prints as `file:line:col  level  code  path  message`, e.g.
`pipeline/models/deal.rk:15:9  error  unknown_model  fields/1/to  "custommer" … did you mean "customer"?`.

## Tests

`tests/day_one.py` starts a throwaway Postgres, migrates, seeds and serves one site on a free port, then walks its day-one scenarios over
the REST API (documents, hooks, approvals, prints, dashboards, roles, imports). It needs the same `RANGKA_OS` and Python as the validators.

```bash
RANGKA_OS=../rangka-os PYTHON=python3 scripts/validate.sh
python3 tests/day_one.py --site kopi-senja      # or demo-jasa, demo-fnb, demo-ritel; add --only <text> for one scenario, --strict to fail on gaps
```

Each check prints `OK`, `FAIL` or `GAP`; the last line reads `N ok · M gaps · K failed`. A `GAP` is a genuine kernel limitation no merged stream covers
(named with its owning stream in [docs/kernel-hooks.md](docs/kernel-hooks.md#kernel-gaps)); it does not fail the run.
Last run on rangka-os main 9c206b4: `kopi-senja` 70 ok, `demo-jasa` 595 ok · 3 gaps, `demo-fnb` 332 ok · 2 gaps, 0 failed.

## Kernel version

The modules need rangka-os `main` at 9c206b4 or later, which has merged S2 approvals, S3 prints, S7 imports, S8a money, S8c business hooks
([#15](https://github.com/rangka-os/rangka-os/pull/15)), S8d `org` and `tax` CEL globals ([#20](https://github.com/rangka-os/rangka-os/pull/20))
and S8e two-step stock transfers with `dpp`/`down_payment` ledger reads ([#24](https://github.com/rangka-os/rangka-os/pull/24)). Pending kernel
keys no longer live in sidecars: every model is one file. `bank_account` (invoicing) needs the CEL text methods `emvcoValid()`, `emvcoTag()`
and `replace()` (rangka-os#67, main at faa7262 or later): on an older kernel those rules cannot be evaluated and do not block, so an invalid QRIS
would be accepted. Hook behaviour: [docs/kernel-hooks.md](docs/kernel-hooks.md).
CI (`.github/workflows/day-one.yml`) runs `scripts/validate.sh` and the three `day_one` sites against rangka-os `main` on every push and pull request.

## Data

Everything here is fictional: Kopi Senja, PT Contoh Inspeksi, CV Contoh Pelatihan, Tahu Contoh and Toko Contoh are made up, and the fixtures hold no real customer data.
Do not commit real customer, NPWP, bank or personal data to this repo.

## Writing an app

Files are `.rk`: strict YAML whose first line names its schema, so VS Code (Red Hat YAML extension) completes and
checks as you type — `.vscode/settings.json` in this repo sets that up.

```yaml
# yaml-language-server: $schema=https://rangka-os.github.io/rangka-os/schema/0.5/dashboard.json
name: crm_overview
label: {id: Ringkasan CRM, en: CRM overview}
cache: 5m
widgets:
  - name: pipeline
    type: number
    model: deal
    aggregate: sum
    field: value
    filter: stage != 'won' && stage != 'lost'   # CEL
    compact: true                               # Rp 51,1 jt
    span: 3
```

Rules worth knowing:

- **Quote what YAML could misread**: `"yes"`, `"no"`, `"on"`, phone numbers and codes with a leading zero, times like
  `"08:30"`, and `'${ENV}'` inside `{ }`. Unquoted, the validator refuses them.
- **Dependencies point one way**: a unit may link to its own models and to units it `requires`; models from an
  `optional` unit need `if: app_installed('<name>')`. `contacts` never links to `pipeline`.
- **Names are forever**: model and field names are `snake_case` and never renamed after release; companies add their
  own fields as `x_…` in a site's `custom/<model>.rk` overlay, never in these files.
- **Labels are Bahasa Indonesia first** (`label: {id: …, en: …}`); money is rupiah without decimals.
- **Backend needs are one line**: `realtime: true` on a model, `schedule:` on a flow, `queue:` on an action, `cache:` on
  a dashboard. The site's `system.rk` decides how they run.

The full format, CEL dialect and lifecycle are in
[rangka-os/docs/contracts.md](https://github.com/rangka-os/rangka-os/blob/main/docs/contracts.md) and the schemas in
[rangka-os/schema/0.5](https://github.com/rangka-os/rangka-os/tree/main/schema/0.5).

## Roadmap

POS (cashier, shifts, offline sales, QRIS), a general ledger (journals; receivables and payables are tracked on the parties today),
warehouses beyond outlets, HR basics; industry packs for F&B, clinics and services on top of the core apps.

## Contributing

New apps and fixes are welcome. A pull request must keep `rangka_validate.py` at 0 errors and include fixtures that
exercise new models. Write UI labels in Bahasa Indonesia (English optional) and keep money and stock logic in rules
and hooks, never in flows driven by an LLM.

## Licence

Elastic License 2.0 (ELv2), see `LICENSE`. Copyright 2026 Saga Tekno Studio. The code is public and free to
self-host, change and use in your own business. You may not offer it to others as a hosted or managed service;
partners who want to host Rangka for clients need a partner agreement with Saga. Rangka Cloud is the only
closed part. Contributions need a contributor agreement (to be published).
