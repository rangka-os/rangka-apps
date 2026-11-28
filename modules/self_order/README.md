# self_order: orders from the table QR (rangka-apps#119)

Guests order from their phones; payment stays at the counter (pay first by QRIS waits for the payment rail of rangka-os
contract 0063).

- **Guest page** `portal/pesan.rk` (rangka-os contract 0073 §9, `order_page`): the QR of a table is a portal link whose
  record is the `dining_table`. The menu lists items ticked "Tampil di menu QR meja" (`item.qr_menu`), with their option
  groups (contract 0048 §1: Level pedas, Es, Tambahan). The guest picks options, marks a dish "Antar nanti", sees the
  model's estimate, sends a round, and follows the table: the table's open bill, every phone's rounds, and buttons
  Panggil pelayan, Minta tagihan, Minta diantar.
- **Table session** = the table's open bill. A round on a closed table waits for the cashier ("Dari HP tamu",
  Terima opens the table); a round on an open table goes straight to the bill and the kitchen (flow
  `self_order_on_create`). A paid bill closes the session: the QR takes no round until a person opens it again. An
  outlet without an open cashier shift refuses QR orders.
- **Prices** always come from `item.rate` plus `item_option.price_delta`: on the bill, `pos_sale.options` rows by
  `line_key` are folded into each line's rate (`pos_sale_item.option_delta`), so promotions, PPN, PBJT, receipts and the
  journal see one price per line.
- **Kitchen**: writes by flows start no flow, so the flows make the station tickets themselves with the routing of
  `kitchen_ticket_on_draft` (buyer `<table> · <order>`) and record the lines in `sent_to_kitchen`. Held lines wait until
  the cashier handles "Minta diantar" (or taps "Kirim ke dapur" on the bill). `self_order_kitchen_progress` moves the
  guest's round to Sedang disiapkan, Siap diantar, Sudah diantar.
- **QR stickers**: `POST /api/v1/portal/pesan/qr {records?}` (owner, head office) makes a new link per table and
  revokes the old ones; the answer carries a QR image per table.

Statuses: `new` Menunggu kasir, `accepted` Diterima, `cooking`, `ready`, `served` (from the kitchen), `done` (a request
handled), `rejected` Ditolak kasir, `cancelled` Dibatalkan tamu (the guest only, within 2 minutes, while new).

Not in this version (follow-ups): pay first by QRIS (needs the payment rail of contract 0063), a printed QR sheet page in
the web app (the API returns the images), the cashier's own option dialog on the counter ("Pilih varian", contract 0048
P5 web), Tolak as a second button on the queue card (the renderer shows one step; turn a round down from its form),
per-item kitchen status (KDS #131), an outlet switch to confirm every round, a counter mode for outlets without tables
(order number, pay first), a "QR · N HP" mark on the table map, a check that the cashier device is online (the open
shift stands in for it), options taking stock or cost, an audit trail when a cashier removes an option row, menus per
outlet. Test: `python3 tests/day_one.py --site demo-kasir` (`tests/day_one_self_order.py`).
