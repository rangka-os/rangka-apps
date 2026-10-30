# Rangka Apps

**Ready-to-use business apps for [Rangka OS](https://github.com/rangka-os/rangka-os)**, written entirely as `.rk`
metadata: models, rules, dashboards, flows and roles. No application code — the Rangka kernel turns these files into a
database, an API and screens.

> **Status: v0 (October 2026).** Three apps, one template and one example site. All files pass validation with 0
> errors.

Part of the Rangka family: [rangka-os](https://github.com/rangka-os/rangka-os) (kernel, schemas, Console) ·
**rangka-apps** · [rangka-connectors](https://github.com/rangka-os/rangka-connectors) ·
[rangka-ai](https://github.com/rangka-os/rangka-ai)

## What's inside

| App | Depends on | Models | Also |
| --- | --- | --- | --- |
| `core` | — | `customer`, `item`, `outlet`, `user` | Shared master data every other app links to |
| `sales` | core | `sales_invoice` (document: draft → submitted → cancelled/amended), `sales_invoice_item` | PPN 12% × DPP 11/12, payment status (draft, submitted, partly paid, paid, overdue), dashboard `sales_overview` |
| `crm` | core, sales (optional) | `lead`, `deal`, `activity` | Kanban pipeline with win probability per stage, "convert lead" action, won deal → draft invoice, dashboard `crm_overview`, flows `whatsapp_to_lead` and `morning_follow_up`, roles `sales_rep` and `sales_manager` |

| Folder | What |
| --- | --- |
| `apps/<app>/app.rk` | Manifest: version, dependencies, menu, dashboards, hooks |
| `apps/<app>/models/*.rk` | Models: fields, views (list, form, kanban), rules, actions, permissions |
| `apps/<app>/dashboards/*.rk` | Number, chart (bar, line, donut, funnel) and list widgets computed from records |
| `apps/<app>/flows/*.rk` | Event- or schedule-driven steps (find, create, update, notify, call a connector) |
| `apps/<app>/roles/*.rk` | Roles shipped with the app |
| `templates/crm/template.rk` | **Rangka CRM** template: apps, onboarding questions, starting configuration |
| `sites/kopi-senja/` | An example installation ("Kopi Senja", a coffee shop): `system.rk`, `features.rk` (plans), `config.rk`, `themes/`, `layouts/` |
| `fixtures/*.rk` | Demo data for Kopi Senja: 4 customers, 4 items, 2 outlets, 17 invoices, 7 deals, 4 leads, 4 activities |

## Try it

Clone next to rangka-os and rangka-connectors, then run the kernel's demo, which loads this repo:

```bash
git clone git@github.com:rangka-os/rangka-os.git
git clone git@github.com:rangka-os/rangka-apps.git
git clone git@github.com:rangka-os/rangka-connectors.git
cd rangka-os/kernel && make run-demo   # http://localhost:8080/prototype/ · /console/ (ceo@saga.co.id / rangka-demo)
```

Validate after any change (Python 3 with `pyyaml` and `jsonschema`, or the Go binary):

```bash
python3 ../rangka-os/tools/rangka_validate.py apps --site sites/kopi-senja --connectors ../rangka-connectors
../rangka-os/kernel/bin/rangka validate --apps apps --site sites/kopi-senja --connectors ../rangka-connectors
```

Each problem prints as `file:line:col  level  code  path  message`, e.g.
`crm/models/deal.rk:15:9  error  unknown_model  fields/1/to  "custommer" … did you mean "customer"?`.

## Writing an app

Files are `.rk`: strict YAML whose first line names its schema, so VS Code (Red Hat YAML extension) completes and
checks as you type — `.vscode/settings.json` in this repo sets that up.

```yaml
# yaml-language-server: $schema=https://rangka-os.github.io/rangka-os/schema/0.4/dashboard.json
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
- **Dependencies point one way**: an app may link to its own models and to apps it `requires`; models from an
  `optional` app need `if: app_installed('<app>')`. `core` never links to `crm`.
- **Names are forever**: model and field names are `snake_case` and never renamed after release; companies add their
  own fields as `x_…` in a site's `custom/<model>.rk` overlay, never in these files.
- **Labels are Bahasa Indonesia first** (`label: {id: …, en: …}`); money is rupiah without decimals.
- **Backend needs are one line**: `realtime: true` on a model, `schedule:` on a flow, `queue:` on an action, `cache:` on
  a dashboard. The site's `system.rk` decides how they run.

The full format, CEL dialect and lifecycle are in
[rangka-os/docs/contracts.md](https://github.com/rangka-os/rangka-os/blob/main/docs/contracts.md) and the schemas in
[rangka-os/schema/0.4](https://github.com/rangka-os/rangka-os/tree/main/schema/0.4).

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
