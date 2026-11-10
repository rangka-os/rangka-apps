# Tiket dapur (module `kitchen`, piece `dapur`)

Kitchen tickets for an F&B outlet that sells through the cashier (module `pos`). Plan: rangka workspace
`plans/layar-kerja.md` §4.1 A4; kernel contract 0030 (work screens, `queue`).

## Setup per outlet
- **Stasiun dapur** (`kitchen_station`): a place that gets its own tickets, per outlet (Bar, Dapur panas). One name
  per outlet. An outlet without an active station makes no ticket, so a shop on the same site is untouched.
- **Kategori ke stasiun** (`kitchen_route`): which item category a station cooks. One station per category and outlet.
  Items of a category without a route make no ticket (bottled drinks handed over by the cashier).

## Flow
| When | Flow | What |
|---|---|---|
| receipt paid (`pos_sale` submitted) | `kitchen_ticket_on_sale` | one `kitchen_ticket` per active station of the outlet with the receipt's lines routed there (item, description, qty, unit, note; no price). Runs inside the payment: a failure rolls the payment back |
| receipt voided (`pos_sale` cancelled) | `kitchen_ticket_on_void` | its tickets not picked up yet become Batal with `void_at` |
| receipt corrected (amend, then paid) | `kitchen_ticket_on_sale` | new tickets with `revision_of` = the voided receipt; the print shows REVISI |
| ticket status changed | `kitchen_ticket_on_update` | one step forward or back only (Baru, Dimasak, Siap, Diambil); stamps `cooking_at`, `ready_at`, `picked_up_at`; a step back clears the stamp of the step undone; Batal only from a void, and a void ticket stays void |

Receipt, outlet, station, table and lines of a ticket are read-only; staff change only `status`.

## Roles (row scope by `outlet`, contract 0010)
- `kitchen` (Dapur): reads and moves the tickets of their outlets; reads no receipt and no money. Home: work screen `dapur`.
- `cashier`: the flow makes the tickets as the cashier; marks a ready ticket Diambil.
- `outlet_staff`: reads the tickets (widgets on dashboard `outlet_hari_ini` of piece stok_outlet).
- `head_office`: everything except delete; sets up stations and routes.

## Screen and print
- `screens/dapur.rk`: `queue` over today's open tickets, columns Baru, Dimasak, Siap, steps Masak, Siap, Diambil,
  refresh 5 s. Bound in app `dapur` as home of `kitchen`, and offered to `cashier` and `head_office`. The renderer
  is kernel WS4; until it is on rangka-os `main` the screen shows the kernel's notice and the tickets are worked from
  the list or kanban of Tiket dapur.
- Print `kitchen_ticket` (80 mm): station, table, receipt, time in, qty x menu with note, REVISI or BATAL. Printed
  from the browser; automatic printing to a station printer waits for rangka-edge (contract 0003).
- Dashboard `dapur_hari_ini`: today's tickets per status and station, the oldest open first.

## Known gaps
- No automatic print when a ticket arrives (needs rangka-edge or a print queue in the kernel).
- One status per ticket, not per line (a station cooks a ticket as one).
- Modifiers are the line's free-text note (the cashier has no modifier model yet).
