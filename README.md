# Rangka Apps

**Ready-to-use business apps for [Rangka OS](https://github.com/rangka-os/rangka-os)**, written entirely as `.rk`
metadata: models, rules, dashboards, flows and roles. No application code — the Rangka kernel turns these files into a
database, an API and screens.

> **Status: v0 (November 2026).** Sixteen modules, three apps (`crm`, `fnb_cabang`, `jasa_proyek`) and three example sites, laid out per
> [contract 0001](https://github.com/rangka-os/rangka-os/blob/main/docs/contracts/0001-modules-and-apps.md).
> All files pass both validators with 0 errors.

Part of the Rangka family: [rangka-os](https://github.com/rangka-os/rangka-os) (kernel, schemas, Console) ·
**rangka-apps** · [rangka-connectors](https://github.com/rangka-os/rangka-connectors) ·
[rangka-ai](https://github.com/rangka-os/rangka-ai)

## What's inside

A **module** holds models, rules, flows and roles. An **app** is what a customer installs: it picks modules and adds
the menu, dashboards, home and onboarding. Apps define no models.

| Module | Requires | Models |
| --- | --- | --- |
| `contacts` | none | `customer`, `vendor`, `contact_person` |
| `catalogue` | contacts | `item`, `uom`, `uom_conversion`, `price_list` (+ `price_list_item`) |
| `outlets` | none | `outlet` |
| `users` | none | `user` |
| `invoicing` | contacts, catalogue (optional: outlets, tax_id, currency) | `sales_invoice` (+ `sales_invoice_item`): PPN on/off, PPh 23, down payment, retention, discount; print format; finance approval above Rp 100.000.000; foreign-currency `*_fx` money fields; numbering series `invoice`; sidecar for the org-PKP default |
| `tax_id` | none | `tax_code` |
| `currency` | none | `currency`, `exchange_rate` |
| `quotation` | contacts, catalogue (optional: tax_id, currency) | `quotation` (+ `quotation_item`): revisions, outcome, manager approval, numbering series `quotation`, foreign-currency `*_fx` fields (sidecar for the org-PKP default), print *Surat penawaran harga*; role `sales_manager` |
| `orders` | contacts, catalogue (optional: quotation) | `customer_order` (+ `customer_order_item`): customer PO, action `make_invoice` |
| `workforce` | none | `worker`, `certification` (expiry flag) |
| `projects` | contacts, users, workforce (optional: orders) | `project`, `assignment`, `timesheet`, `acceptance` (berita acara); role `project_admin` |
| `payments` | contacts, invoicing (optional: procurement, currency) | `payment` (+ `payment_allocation`), `withholding` (PPh 23 certificate); foreign-currency `amount_fx`; hooks `payments.allocate` and `payments.unallocate`; print *Statement* |
| `procurement` | contacts, catalogue (optional: outlets, users, currency, stock) | `purchase_request`, `purchase_order`, `goods_receipt`, `vendor_bill` (+ line models): approval above Rp 5.000.000, foreign-currency `*_fx` fields, print *Pesanan pembelian*, actions `make_po` and `receive`; role `purchasing_manager` |
| `stock` | catalogue, outlets (optional: users) | `recipe` (+ line), `stock_move`, `stock_balance`, `stock_transfer` (two steps), `stock_transfer_receipt`, `stock_count`, `stock_adjustment` (+ line models): moving-average cost, recipes, negative stock allowed and flagged; roles `head_office` and `outlet_staff` (row scope by outlet); flow `stock_negative_alert` |
| `outlet_sales` | outlets, catalogue, stock | `outlet_sales` (+ line): daily sales per outlet, submit posts stock through the recipes; imports `moka_item_details` and `daily_form` |
| `pipeline` | contacts, users | `lead`, `deal`, `activity`, flows `whatsapp_to_lead` and `morning_follow_up`, roles `sales_rep` and `sales_manager` |

| App | Modules | Also |
| --- | --- | --- |
| `crm` | contacts, catalogue, pipeline, invoicing, outlets, users | Menu, dashboards `crm_overview` and `sales_overview`, WhatsApp connector, home, onboarding |
| `fnb_cabang` | contacts, catalogue, outlets, users, stock, outlet_sales, procurement, payments, invoicing (no menu entry) | Multi-branch F&B back office: menu groups Penjualan outlet, Stok, Pembelian, Data induk; dashboards `fnb_overview` (home: net sales today and over the period, margin, net by outlet and by day, the "Stok minus" list for head office) and `stock_recap` (stock value by outlet, moves by reason, monthly sales and cost per outlet and in total); roles `head_office` and `outlet_staff`; onboarding (outlets, Moka outlets, raw-material unit) |
| `jasa_proyek` | contacts, catalogue, users, outlets, tax_id, currency, invoicing, quotation, orders, workforce, projects, payments, procurement | Project-services quote-to-cash: menu groups Penjualan, Proyek, Tenaga kerja, Keuangan, Pembelian, Data induk; dashboards `jasa_overview` (home: open, won and lost quotations, invoices issued, outstanding AR, overdue invoices, active projects) and `ar_aging` (outstanding by age bucket, by customer, overdue list); roles `sales_manager`, `finance_manager`, `purchasing_manager`, `project_admin`; onboarding (PKP, business line, number of PTs) |

```
modules/<m>/module.rk               manifest: name, version, title, requires, optional, hooks
modules/<m>/models|roles|flows/     the module's metadata
modules/<m>/print/                  print formats (inert until the kernel's print stream, S3, merges)
modules/<m>/imports/                spreadsheet imports (kind import, contract 0012)
apps/<a>/app.rk, dashboards/        what a customer installs: modules, menu, dashboards, connectors, roles, home, onboarding
examples/sites/<site>/              example installations: system.rk, features.rk, config.rk (+ letterhead.rk, letterheads/<org>.rk, themes/, layouts/)
examples/fixtures/<dir>/            their demo data (`demo-jasa` seeds the first PT, `demo-jasa-pelatihan` the second; see below)
scripts/                            validate.sh (both validators over every site), lib.sh (finds rangka-os and the kernel binary)
tests/day_one.py                    integration test against a live kernel (`python3 tests/day_one.py --site kopi-senja|demo-jasa|demo-fnb`)
docs/kernel-hooks.md                what each hook the modules declare does in the kernel, and the table of kernel gaps
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
- `demo-jasa`: project services under one owner with **two fictional PTs** (`orgs:` in `config.rk`, contract 0010):
  `pt_contoh_inspeksi` (code `CIN`, PKP, numbers like `INV/CIN/26/0001`) and `cv_contoh_pelatihan` (code `CPL`, non-PKP,
  numbers like `KW-CPL-2611-001`). Each has its own `letterheads/<org>.rk`; `letterhead.rk` is the fallback. Seed each org
  with its own fixtures: `rangka seed --org pt_contoh_inspeksi --fixtures examples/fixtures/demo-jasa` and
  `rangka seed --org cv_contoh_pelatihan --fixtures examples/fixtures/demo-jasa-pelatihan`, then serve with `--org pt_contoh_inspeksi`
  (the owner switches orgs with `POST /auth/org`).
- `demo-fnb`: Tahu Contoh, one org (`tahu_contoh`, code `TCN`) with three outlets (head office, Dago, Braga), a letterhead
  and PO numbers like `PO/TCN/2611/001`. Outlet staff are members with `--outlets` (`tests/day_one.py` adds them with `rangka user add`).

`python3 tests/day_one.py --site demo-jasa|demo-fnb|kopi-senja` runs the integration test; `--only <text>` runs the
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
python3 tests/day_one.py --site kopi-senja      # or demo-jasa, demo-fnb; add --only <text> for one scenario, --strict to fail on gaps
```

Each check prints `OK`, `FAIL` or `GAP`; the last line reads `N ok · M gaps · K failed`. A `GAP` is something the kernel does not do yet
(named with its owning stream in [docs/kernel-hooks.md](docs/kernel-hooks.md#kernel-gaps)); it does not fail the run.
Last run on rangka-os with S8c, S8d and S8e: `kopi-senja` 40 ok, `demo-jasa` 487 ok · 5 gaps, `demo-fnb` 296 ok · 2 gaps, 0 failed.

## Kernel version

The modules need rangka-os `main` with these merged: S8c business hooks
([#15](https://github.com/rangka-os/rangka-os/pull/15), merged), S8d `org` and `tax` CEL globals
([#20](https://github.com/rangka-os/rangka-os/pull/20), open) and S8e two-step stock transfers with `dpp`/`down_payment` ledger reads
([#24](https://github.com/rangka-os/rangka-os/pull/24), open). Without S8d the PPN rules cannot read `tax`; without S8e a transfer
moves stock in one step and the DP/retention checks read the old field names. Hook behaviour: [docs/kernel-hooks.md](docs/kernel-hooks.md).

## Data

Everything here is fictional: Kopi Senja, PT Contoh Inspeksi, CV Contoh Pelatihan and Tahu Contoh are made up, and the fixtures hold no real customer data.
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

Not chosen yet; no `LICENSE` file is committed, so all rights are reserved until one is. The intent is a licence that
lets partners build and sell templates on top of these apps.
