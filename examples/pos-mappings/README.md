# POS exports without an import of their own

`scripts/pos_export_to_template.py` turns a POS's sales export into Juragan's sales template, the file the import
`pos_sales_file` reads (Tanggal, No struk, Jenis, SKU, Qty, Penjualan kotor, Diskon, Retur; one outlet per file,
the outlet's Sumber penjualan set to File POS). What the export's columns are called is in a mapping file here, one
per POS, not in the script.

```bash
python3 scripts/pos_export_to_template.py --mapping examples/pos-mappings/esb.example.json examples/pos-mappings/esb-sample-export.csv --out out
```

It prints one line per file with the totals (rows, Qty, gross, discount, returns, net): compare them with the POS's
own report before uploading. A date, a number or a SKU it cannot read stops it with the row number.

## Writing a mapping

Take the first real export of that POS, save it as CSV, and fill in:

| Key | Meaning |
| --- | --- |
| `header_row` | The line the column names are on (1 when nothing is above them) |
| `delimiter` | `,` `;` or a tab; left out, it is sniffed from the header line |
| `date_format` | How the date column is written, as Python `strptime` (`%d/%m/%Y %H:%M`, `%Y-%m-%d`) |
| `number` | `thousands` and `decimal` marks (`.` and `,` for 1.250.000,50) |
| `columns.date`, `sku`, `qty`, `gross` | Required. `sku` must hold the SKU the item has in Juragan |
| `columns.receipt`, `discount`, `branch` | Optional. With `branch`, one file per branch is written |
| `void` | `column`, the `values` that mark a voided or refunded row, and `as`: `skip` (a voided bill never was a sale) or `return` (a row of Jenis Retur with a negative Qty and the value in Retur) |
| `skip_rows_where` | Subtotal and footer lines of the report: `column` and the text it `equals` |

Lines that share date, receipt, kind and SKU are added up, because the import's key is those four. A row the POS
writes with a negative quantity is a return.

## ESB

`esb.example.json` and `esb-sample-export.csv` are synthetic: ESB (PT Esensi Solusi Buana) publishes neither an API
reference nor the layout of its export files, and no real export has been seen yet (19 Nov 2026). Only the report
names "Sales detail" and "Sales by menu" are confirmed. With the first merchant on ESB: export the sales detail of a
few days including a void and a discount, keep the file outside the repo, and replace the column names in a copy of
the example. If the mapping holds for two merchants, it becomes an import of its own in `modules/outlet_sales/imports`,
as Moka's did.

Real exports are merchant data: never commit one. Samples in this folder are made up.
