# Tiket dapur (module `kitchen` 2.0, piece `dapur`)

Kitchen tickets for an F&B outlet that sells through the cashier (module `pos`). Plan: rangka workspace
`plans/layar-kerja.md` §4.1 A4 and `plans/kitchen-display-131.md`; kernel contracts 0030 (work screens, `queue`) and
0077 (queue rows: line and bulk steps, groups, totals, recall, `_if` on table rows). Issue rangka-apps#131.

## Setup per outlet
- **Stasiun dapur** (`kitchen_station`): a place that gets its own tickets, per outlet (Bar, Dapur panas, Kemas). One
  name per outlet. An outlet without an active station makes no ticket, so a shop on the same site is untouched. Each
  station has its own verbs (`start_label`, default "Proses"; `ready_label`, default "Siap"), never "Masak", which is
  wrong for a bar, a dessert counter or packing.
- **Kategori ke stasiun** (`kitchen_route`): which item category a station prepares. One station per category and
  outlet. Items of a category without a route make no ticket (bottled drinks handed over by the cashier).

## Per item, not per ticket
Each ticket line (`kitchen_ticket_item`) has its own state: Baru (`new`) -> Diproses (`in_progress`) -> Siap
(`ready`) -> Diambil (`done`). The ticket's status is computed from its lines: Siap when every line not held is ready,
Diambil when every line is handed over and every notice is read, Batal only when the receipt is voided (`voided`).

- **Sajikan nanti (hold and fire).** The cashier marks a line "Sajikan nanti" (`pos_sale_item.kitchen_hold`, pos
  1.12): it reaches its station greyed (`held`) and waits until the pass (or the cashier) taps "Lepas"; then it is new
  at its station, with a sound. Not "Tahan", which parks the whole bill at the counter.
- **Notices.** A line removed from an open table bill after it reached the station arrives as a Batal line (`kind:
  void`); a lowered quantity or a changed note arrives as Diubah (`kind: change`, with what it was). The station taps
  "Oke" (`seen`).

## Flow
| When | Flow | What |
|---|---|---|
| receipt paid (`pos_sale` submitted) | `kitchen_ticket_on_sale` | one `kitchen_ticket` per active station with the receipt's lines routed there (item, description, qty, unit, note, line key, held; no price). Runs inside the payment: a failure rolls the payment back |
| "Kirim ke dapur" on an open table bill | `kitchen_ticket_on_draft` | new lines go to their stations; removed or changed lines the kitchen already got go as Batal or Diubah notices |
| receipt voided (`pos_sale` cancelled) | `kitchen_ticket_on_void` | its tickets not fully handed over become Batal; a handed-over ticket is marked `sale_void` |
| receipt corrected (amend, then paid) | `kitchen_ticket_on_sale` | new tickets with `revision_of` = the voided receipt; lines already handed over (per line, notices never count) are not made again; the print shows REVISI |
| a staff write | `kitchen_ticket_on_update` | refuses what a screen never does: changing a voided ticket, removing a line, moving a held line (even in the write that fires it), moving a notice, holding a line already started; stamps `cooking_at`, `ready_at`, `picked_up_at` |

Receipt, outlet, station, table and the lines' menu, quantity, note and key are read-only; staff write only the lines'
`status`, `held`, `seen` and time stamps. Every screen write is a conditional PATCH (`_if` on every line, contract
0077 §1), so two tablets never overwrite each other.

## Roles (row scope by `outlet`, contract 0010)
- `kitchen` (Dapur, `desk: false`): reads and moves the lines of their outlets; reads no receipt and no money. Home:
  work screen `dapur`.
- `cashier`: the flow makes the tickets as the cashier; runs the pass (`penyerahan`) on a small outlet.
- `outlet_staff`: reads the tickets and dashboard `dapur_hari_ini`.
- `head_office`: everything except delete; sets up stations and routes.

## Screens and print
- `screens/dapur.rk` (station): rows of today's open tickets, dark theme. The device picks its station once
  ("Semua", Bar, Dapur; remembered). Tap a row: the station's start verb, then its ready verb; "Semua siap" on a card;
  "Urungkan" for 5 s; header counts per state; "Total item" (all-day counts, open at TV width); "Baru selesai" to
  bring back a line marked ready by mistake. Offline taps wait in the device outbox and are sent when online.
- `screens/penyerahan.rk` (pass): one card per order across stations, each line with its station and state; "Lepas"
  fires a held line, "Serahkan yang siap" hands over what is ready (drinks can go before the food).
- `screens/siap_ambil.rk` (guest board, TV): "Sedang disiapkan" and "Siap diambil", the table or buyer, else the
  receipt's last three digits.
- Print `kitchen_ticket` (80 mm): station, table, receipt, time in, qty x menu with note; per line BATAL, DIUBAH or
  NANTI; REVISI or BATAL for the ticket. Printed from the browser; automatic printing waits for rangka-edge.
- Dashboard `dapur_hari_ini`: today's tickets per status and station, the oldest open first.

## Known gaps
- Firing by prep time so items of an order finish together (out of scope in contract 0077).
- New orders while the outlet's internet is down need a local hub (rangka-edge); taps made offline are queued.
- No automatic print when a ticket arrives (needs rangka-edge or a print queue in the kernel).
- Tickets made before the 2.0 upgrade keep their stored status until their next write; screens show today's tickets
  only, so deploy outside service hours.
- Modifiers are the line's free-text note plus the self order's option text.
