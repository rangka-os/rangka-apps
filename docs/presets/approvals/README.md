# Approval presets

Opt-in approval policies an owner can switch on. Each file is a normal site overlay (`custom/<model>.rk`). The Raka
skill reads them with `rangka_skill_file` and onboarding copies the chosen one to the site's `custom/` folder under the
model's name (for example `purchase_order_threshold.rk` becomes `custom/purchase_order.rk`). No kernel schema change.

| Owner-facing name | Files | Model | Default limit |
| --- | --- | --- | --- |
| Persetujuan PO di atas batas | `purchase_order_threshold.rk` | purchase_order | Rp 5.000.000 |
| Persetujuan pengeluaran | `payment_payout.rk`, `vendor_bill_without_po.rk` | payment, vendor_bill | Rp 5.000.000 |
| Persetujuan diskon besar | `sales_invoice_discount.rk`, `quotation_discount.rk` | sales_invoice, quotation | 20% |

Variants: the plain file is approved by `finance_manager` (or the owner). The `_owner_only` file uses
`purchasing_manager`, a role no staff member holds on a site without a purchasing team, so only the owner approves.
One preset per model: two files for the same model would both want `custom/<model>.rk`.

Rules: roles only (no per-person limits); a limit is a number in the `when:` expression the owner can change;
`scripts/validate.sh` validates every file here on a scratch copy of a demo site, so a broken preset fails CI.
