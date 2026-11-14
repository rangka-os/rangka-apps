---
name: rangka-app-sewa-alat
description: "Rangka Sewa Alat (app sewa_alat): rental of equipment or vehicles with a schedule without double booking, handover out and back per unit, deposit, late fee, rent invoices. Use when an owner rents things out (alat pesta, kamera, mobil, motor) and for the first day after it is built (first-day.json)."
metadata: {rangka: {version: 0.1.0, app: sewa_alat, requires: [rangka-compose, rangka-changesets]}}
---

# Rangka Sewa Alat

Read `rangka-compose` first. This draft only adds the first day.

## First day (first-day.json)

`first-day.json` next to this file is read by Rangka Cloud after the build (proposal "First day with Raka", draft for rangka-os):

- `steps`: 3-5 steps in the order a rental business starts. A step is done when the kernel has at least `done.min`
  records of `done.model`; a step without `done` is ticked by the owner.
- `seed`: what Raka may prepare from the interview. Only values the owner said, each with the owner's quote (engine
  `/v1/seed` drops the rest). Money fields are marked `money` and shown separately; nothing is written before the
  owner agrees.
- `rename`: model labels that follow the owner's own word for what they rent (`{Word}` capitalised, `{word}` lower),
  proposed as one low-risk change set of label overlays.
- `menu`: one sentence per menu item, said by Raka when the owner first opens it.

Say these in the owner's words. Never invent units, plates, rates or fees the owner did not say.
