# self_order: orders from the table QR (rangka-apps#119)

v1: the guest orders from the phone, the cashier takes the order, payment stays at the counter.

- **Guest page** `portal/pesan.rk` (rangka-os contract 0073 §9, type `order_page`): the QR of a table is a portal link
  whose record is the `dining_table`. The page lists items that are not materials and active (title, price,
  category), takes a basket with notes and an optional name, and shows the status of the orders sent from that phone.
- **Cashier** work screen `pesanan_masuk` (app `pesan_meja`): Terima puts the lines on the table's open bill (or opens
  a dine-in bill on the cashier's shift) and sends them to the kitchen stations; Tolak turns the order down.
- **Kitchen**: flow `self_order_on_update` makes the station tickets itself (writes by flows start no flow) with the
  routing of `kitchen_ticket_on_draft`, buyer `<table> · <order>`, and records the lines in the bill's
  `sent_to_kitchen`, so payment never sends them twice. Flow `self_order_kitchen_progress` moves the guest's status
  to Sedang dimasak, Siap diantar, Sudah diantar.
- **QR stickers**: `POST /api/v1/portal/pesan/qr {records?}` as owner or head office makes a new link per table (the old
  ones stop working) and returns a QR image per table.

Statuses: `new` Menunggu kasir, `accepted` Diterima kasir, `cooking`, `ready`, `served` (from the kitchen), `rejected`
Ditolak kasir, `cancelled` Dibatalkan tamu (the guest only, within 2 minutes, while new).

Not in v1: pay first by QRIS (needs the payment rail of contract 0063), auto-accept, item photos, options and
modifiers, a printed QR sheet in the web app. Test: `python3 tests/day_one.py --site demo-kasir` (scenarios
`scenario_self_order_*` in `tests/day_one_self_order.py`).
