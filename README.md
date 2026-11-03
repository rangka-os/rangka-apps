# Rangka Apps

**Ready-to-use business apps for [Rangka OS](https://github.com/rangka-os/rangka-os)**, written entirely as `.rk`
metadata: models, rules, dashboards, flows and roles. No application code — the Rangka kernel turns these files into a
database, an API and screens.

> **Status: v0 (November 2026).** Fourteen modules, two apps and two example sites, laid out per
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
| `pipeline` | contacts, users | `lead`, `deal`, `activity`, flows `whatsapp_to_lead` and `morning_follow_up`, roles `sales_rep` and `sales_manager` |

| App | Modules | Also |
| --- | --- | --- |
| `crm` | contacts, catalogue, pipeline, invoicing, outlets, users | Menu, dashboards `crm_overview` and `sales_overview`, WhatsApp connector, home, onboarding |
| `jasa_proyek` | contacts, catalogue, users, outlets, tax_id, currency, invoicing, quotation, orders, workforce, projects, payments, procurement | Interim: a flat menu of the models so far (Task 10 completes it) |

```
modules/<m>/module.rk               manifest: name, version, title, requires, optional, hooks
modules/<m>/models|roles|flows/     the module's metadata
modules/<m>/print/                  print formats (inert until the kernel's print stream, S3, merges)
modules/<m>/experimental/           sidecars: only what still waits for a kernel stream (see below)
apps/<a>/app.rk, dashboards/        what a customer installs: modules, menu, dashboards, connectors, roles, home, onboarding
examples/sites/<site>/              example installations: system.rk, features.rk, config.rk (+ themes/, layouts/)
examples/fixtures/<site>/           their demo data
scripts/                            validate.sh, check_experimental.py
tests/day_one.py                    integration test against a live kernel (`python3 tests/day_one.py --site kopi-senja|demo-jasa`)
docs/kernel-hooks.md                what each hook the modules declare must do
```

The kernel and both validators read `apps/` and the `modules/` folder next to it, so there is nothing to build. A
site's `config.apps` lists apps only; the kernel installs the modules each app names, and their `requires`.

A sidecar `modules/<m>/experimental/<model>.rk` holds keys the kernel cannot read yet. Today there are two: the
`ppn_applies` default from the org's `pkp` on `sales_invoice` and `quotation` (CEL has no `org` variable until stream S5).

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
`RANGKA_OS` points at the rangka-os checkout, found next to this repo by default):

```bash
scripts/validate.sh                      # both validators, every site in examples/sites; prints "0 errors"
scripts/validate.sh examples/sites/kopi-senja
```

Each problem prints as `file:line:col  level  code  path  message`, e.g.
`pipeline/models/deal.rk:15:9  error  unknown_model  fields/1/to  "custommer" … did you mean "customer"?`.

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

POS (cashier, shifts, offline sales, QRIS), Stock (warehouses, transfers, stock moves), Ledger (journals, receivables),
Payments, HR basics; industry packs for F&B, clinics and services on top of the core apps.

## Contributing

New apps and fixes are welcome. A pull request must keep `rangka_validate.py` at 0 errors and include fixtures that
exercise new models. Write UI labels in Bahasa Indonesia (English optional) and keep money and stock logic in rules
and hooks, never in flows driven by an LLM.

## Licence

Not chosen yet; no `LICENSE` file is committed, so all rights are reserved until one is. The intent is a licence that
lets partners build and sell templates on top of these apps.
