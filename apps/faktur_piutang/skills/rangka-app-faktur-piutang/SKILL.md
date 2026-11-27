---
name: rangka-app-faktur-piutang
description: "Rangka Faktur dan Piutang (app faktur_piutang): sales invoices, payments received and reminders. Use when an owner asks for approval or a spending limit (\"PO besar harus aku setujui\", \"batasi pengeluaran staf\", \"diskon besar tanya aku dulu\") and during onboarding of this app, to offer the approval presets."
metadata: {rangka: {version: 1.0.0, app: faktur_piutang, requires: [rangka-compose, rangka-changesets]}}
---

# Rangka approval presets for this app

Read `rangka-compose` first. This skill only covers the approval presets (issue rangka-apps#105). The engine is the
document approval of contract 0007: a submit that matches a policy is held (202), the approver sees it in Persetujuan
and the document proceeds when approved; a rejected one stays a draft.

## Rules

- **Opt-in.** Never switch a preset on by default. Ask the owner (onboarding question) and only an answer `ya` installs it.
- **Roles only.** No per-person limits. The approver of the spending and big discount presets is the owner only (`approvers: [owner]`): finance can submit these documents, and an approver who submits approves their own request.
- **The limit is the owner's call.** The files use Rp 5.000.000 (purchasing, payouts) and 20% (discount) as examples.
  Ask "mulai dari berapa?" and change the number in the `when:` expression of the copy, never in the preset file.
- Money or tax questions beyond a threshold are not part of a preset: say so and offer the nearest preset.

## Presets for this app

### Persetujuan diskon besar

- What the owner gets: a sales invoice with a discount above 20% waits for finance.
- Say it like this: "Mau aku pasangkan Persetujuan diskon besar? Mulai dari berapa?"
- Files: `docs/presets/approvals/sales_invoice_discount.rk`. Read each with `rangka_skill_file`, replace the limit if the owner named another, and write it as a
  change set to custom/sales_invoice.rk (a copy of the file, named after its model). Each file starts with a plain-words header comment.
- After install, tell the owner the request shows in Persetujuan and only the owner approves it.

## Checks before you propose the change set

- `custom/<model>.rk` does not exist yet for that model (one preset per model; if it does, merge the `approvals:` entry into it).
- The approver role is not the role that normally submits that document: an approver who submits approves their own request.
- Run the site validation of the change set; do not apply it when it reports an error.
