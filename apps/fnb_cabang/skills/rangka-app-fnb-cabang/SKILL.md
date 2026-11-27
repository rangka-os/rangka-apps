---
name: rangka-app-fnb-cabang
description: "Rangka F&B Cabang (app fnb_cabang): outlets, stock, recipes, purchasing and daily sales for a multi-outlet food business. Use when an owner asks for approval or a spending limit (\"PO besar harus aku setujui\", \"batasi pengeluaran staf\", \"diskon besar tanya aku dulu\") and during onboarding of this app, to offer the approval presets."
metadata: {rangka: {version: 1.0.0, app: fnb_cabang, requires: [rangka-compose, rangka-changesets]}}
---

# Rangka approval presets for this app

Read `rangka-compose` first. This skill only covers the approval presets (issue rangka-apps#105). The engine is the
document approval of contract 0007: a submit that matches a policy is held (202), the approver sees it in Persetujuan
and the document proceeds when approved; a rejected one stays a draft.

## Rules

- **Opt-in.** Never switch a preset on by default. Ask the owner (onboarding question) and only an answer `ya` installs it.
- **Roles only.** No per-person limits. The approver here is the owner (this app has no finance role, so the `_owner_only` files are used: the approver role is one nobody holds and the owner may always approve).
- **The limit is the owner's call.** The files use Rp 5.000.000 (purchasing, payouts) and 20% (discount) as examples.
  Ask "mulai dari berapa?" and change the number in the `when:` expression of the copy, never in the preset file.
- Money or tax questions beyond a threshold are not part of a preset: say so and offer the nearest preset.

## Presets for this app

### Persetujuan PO di atas batas

- What the owner gets: a purchase order above Rp 5.000.000 (or in a foreign currency) waits for approval before it can be submitted; a smaller one goes straight through.
- Say it like this: "Mau aku pasangkan Persetujuan PO di atas batas? Mulai dari berapa?"
- Files: `docs/presets/approvals/purchase_order_threshold_owner_only.rk`. Read each with `rangka_skill_file`, replace the limit if the owner named another, and write it as a
  change set to custom/purchase_order.rk (a copy of the file, named after its model). Each file starts with a plain-words header comment.
- After install, tell the owner who will see the request: the owner (this app has no finance role, so the `_owner_only` files are used: the approver role is one nobody holds and the owner may always approve), in Persetujuan.

### Persetujuan pengeluaran

- What the owner gets: an outgoing payment above Rp 5.000.000, and a supplier bill above it that has no approved PO, wait for approval; incoming payments are not affected.
- Say it like this: "Mau aku pasangkan Persetujuan pengeluaran? Mulai dari berapa?"
- Files: `docs/presets/approvals/payment_payout.rk` and `docs/presets/approvals/vendor_bill_threshold.rk`. Read each with `rangka_skill_file`, replace the limit if the owner named another, and write it as a
  change set to custom/payment.rk and custom/vendor_bill.rk (a copy of the file, named after its model). Each file starts with a plain-words header comment.
- After install, tell the owner who will see the request: the owner (this app has no finance role, so the `_owner_only` files are used: the approver role is one nobody holds and the owner may always approve), in Persetujuan.

## Checks before you propose the change set

- `custom/<model>.rk` does not exist yet for that model (one preset per model; if it does, merge the `approvals:` entry into it).
- The approver role is not the role that normally submits that document: an approver who submits approves their own request.
- Run the site validation of the change set; do not apply it when it reports an error.
