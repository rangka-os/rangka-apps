# rangka-apps (spike)

Metadata for the Rangka framework spike, plus the tools that read it.

## File format: `.rk`

Every metadata file is a `.rk` file: YAML with a few strict rules, so it means the same thing in every tool.

```yaml
# yaml-language-server: $schema=https://rangka.dev/schema/0.4/model.json
name: deal
label: {id: Peluang, en: Deal}
fields:
  - name: stage
    type: select
    default: "'prospect'"        # a CEL expression, so the string is quoted inside
    options:
      - {value: prospect, label: {id: Prospek}, probability: 10}
  - name: weighted_value
    type: currency
    compute: round(value * probability / 100)
```

Rules (checked by `tools/rkfile.py`, used by every tool):

- YAML 1.2 types only: `true`/`false`, `null`, numbers. Dates stay text.
- Values that older YAML tools read differently must be quoted: `yes`, `no`, `on`, `off`, `y`, `n`,
  numbers with a leading zero (phone numbers, NPWP, item codes like `"0812..."`), `1_000`, times like `"08:30"`.
  Keys are always text, so `on: save` is fine.
- No duplicate keys, no tabs, no anchors/aliases (`&`, `*`), no merge keys (`<<`). One document per file.
- Errors point at `file:line:col`.

Accepted extensions: `.rk` (preferred), `.rk.yaml`, `.rk.yml`. Legacy `.json` still loads during migration;
`tools/rk_convert.py <dir>` converts it and refuses any file that would not read back identically.
The JSON Schemas in `schema/0.4` validate `.rk` files unchanged. `.vscode/settings.json` maps `*.rk` to YAML and
attaches the schemas, so VS Code (with the Red Hat YAML extension) gives highlighting, completion and errors.

## Layout

- `apps/core`, `apps/sales`, `apps/crm` — `app.rk` manifests and `models/*.rk`
- `apps/crm/roles/*.rk`, `apps/crm/flows/whatsapp_to_lead.rk` — roles and the WhatsApp → lead flow
- `apps/crm/dashboards/crm_overview.rk`, `apps/sales/dashboards/sales_overview.rk` — dashboards (see below)
- `templates/crm/template.rk` — the Rangka CRM template
- Connectors live in the rangka-connectors repo
- `sites/kopi-senja/` — an example installation: system.rk, features.rk, config.rk, themes/, layouts/
- Schemas, validator and tools live in rangka-os (`schema/0.4`, `tools/`)
- `fixtures/*.rk` — demo data for Kopi Senja

## Dashboards

A dashboard is a `.rk` file in `apps/<app>/dashboards/`, listed in `app.rk` under `dashboards:` (the first one is the app's home).
Widgets are computed from records when the page opens; nothing is stored.

```yaml
widgets:
  - name: pipeline            # number: one aggregated value
    type: number
    model: deal
    aggregate: sum            # count | sum | avg | min | max
    field: value
    filter: stage != 'won' && stage != 'lost'   # CEL, same dialect as rules
    compact: true             # Rp 51,1 jt
    span: 3                   # of 12 columns
  - name: by_stage            # chart: values grouped by a field
    type: chart
    chart: funnel             # bar | line | donut | funnel
    model: deal
    group_by: stage           # select, link, check, date (+ grain: day|week|month) or data
    aggregate: sum
    field: value
    span: 6
  - name: closing_soon        # list: first rows of a filtered, sorted list
    type: list
    model: deal
    sort: expected_close      # -field for descending
    limit: 5
    columns: [deal_name, customer, stage, expected_close, value]
    span: 12
```

`period: <date field>` makes a widget follow the dashboard's 7/30/90-day switch and compare with the period before;
`good_when: down` flips the colour of the change for figures where less is better. Widgets on a model from an optional
app need `if: app_installed('<app>')`. The validator checks every model, field, type, grain and filter expression.

## Tools (Python 3, `pip install pyyaml jsonschema`)

- `python3 ../rangka-os/tools/rangka_validate.py apps --site sites/kopi-senja --connectors ../rangka-connectors` — syntax, shape and meaning checks, one line per problem with `file:line:col`
- `tools/rangka_docs.py apps crm out.json` — generate the API reference model
- `tools/build_proto.py . meta.js` — bundle metadata (and the `.rk` sources) for the browser prototype
- `tools/rk_convert.py .` — convert `.json` metadata to `.rk`

Spec: Rangka Framework Spec (Claude Docs). Domain `rangka.dev` in schema URLs is a placeholder.
