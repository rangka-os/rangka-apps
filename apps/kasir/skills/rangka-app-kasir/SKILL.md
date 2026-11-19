---
name: rangka-app-kasir
description: "Rangka Kasir (app kasir): the browser cashier for shops and F&B outlets, with shifts per outlet, numbered receipts, cash and QRIS, stock and recipes taken out automatically. Use when an owner asks for a cashier or POS (\"mau pakai kasir\", \"tambahin POS\", \"jualan langsung di outlet\"), to explain what it shares with apps they already run, for what to set up after it is added, and in the studio interview to decide whether a counter business gets the cashier on its map."
metadata: {rangka: {version: 1.0.0, app: kasir, requires: [rangka-compose, rangka-changesets]}}
---

# Rangka Kasir

Read `rangka-compose` first: it covers the catalogue check and the `config.rk` change. This skill adds what is
specific to the cashier.

## What it is

- Module `pos` plus the shared modules `users`, `contacts`, `catalogue`, `outlets`, `stock`.
- A cashier opens a **shift** at an outlet with the float in the drawer, rings up **sales** (items or menu, any unit of
  the item, line and receipt discounts), takes **cash** (with change), **QRIS** or both, and closes the shift by
  counting the drawer. The difference is booked as *selisih kas*; the cashier does not see the expected amount, head
  office does (dashboard *Ringkasan kasir*).
- Each sale gets a receipt number per outlet and takes its goods out of that outlet's stock, a menu item through its
  recipe. With `accounting` installed it is journalled.
- A sale can be voided or corrected only on its own day, while its shift is open.
- Roles: `cashier` (sees only the cashier screen of their own outlets; it is their home) and `head_office`.

## What it is not (say so when asked)

- **Online only.** It runs in the browser and needs the internet. Offline selling, receipt printers and cash drawers
  come later with Rangka Edge.
- **QRIS is recorded, not processed.** The cashier records a QRIS payment and its reference from the notification;
  Kasir does not show a dynamic QR or confirm the payment itself.
- **Not GoFood, GrabFood or ShopeeFood.** Orders from those apps are not rung up here; see `rangka-compose`, "When
  nothing fits".

## In the studio interview: when the cashier goes on the map

For Raka's consultant interview (`rangka-consult`, contract 0042). The deciding fact is the tools the owner uses
today and whether Juragan replaces them:

- A business that serves buyers at a counter (kedai, kafe, restoran, toko) and has **no cashier app yet**, or chose
  **ganti penuh**, gets a piece `{kind: catalogue, ref: kasir}` at the payment stage. Tell the owner the cashier
  screen runs on a tablet or a phone:

  > Untuk meja kasir aku pasang Kasir. Layarnya jalan di tablet atau HP, jadi tidak perlu mesin kasir khusus.

- If the owner keeps their current cashier (**pendamping**, for example Moka or another POS), do not place kasir. Their
  sales come in through the imports from the cashier they already use (check which imports exist with
  `rangka_catalogue` / `rangka_meta` before naming one).
- If you do not know yet, ask what they use at the counter today before placing anything.

## Explaining it to an owner who already runs an app

Use the catalogue's `shares` and `adds` for `kasir`. On a site running Rangka F&B Cabang it shares items, outlets,
stock and users and adds only the cashier. Say it in their words:

> Kasir pakai barang, outlet dan stok yang sama dengan sistem kamu sekarang, jadi tiap penjualan langsung
> mengurangi stok outlet itu. Yang baru: layar kasir, sesi kas, dan struk.

## After the change set is applied: what the owner sets up

You cannot do these yourself: they are records, set by a person. Tell the owner, one step at a time.

1. **Which outlets use the cashier.** On a site that also records outlet sales (module `outlet_sales`, part of F&B
   Cabang and Ritel Cabang), each outlet sells through one source only. For each outlet that will use Kasir, the
   owner opens the outlet (menu *Barang dan harga* → *Outlet*) and sets *Sumber penjualan* to *Kasir Juragan*. Until
   then a sale at that outlet is refused. Outlets that keep the daily form or a POS file stay as they are.
2. **Who the cashiers are.** Each cashier is a user with role `cashier` and their outlets. The owner adds them in the
   app's member settings (or asks Juragan). Ask for names and outlets; never ask for passwords.
3. **Float (optional).** *Modal kas kasir* on the outlet: the cash a shift opens with. Empty means the cashier types
   it when opening the shift.
4. **Menu items need prices and, for F&B, recipes,** so stock goes out correctly. If items have no recipe yet, say
   that sales still work but only the item itself leaves stock.

The app's own onboarding asks the first two: "Outlet mana yang mau pakai kasir Juragan?" and "Siapa saja kasirnya,
dan di outlet mana?". Use the same questions.

## Where it is once applied

- Cashiers land on the cashier screen (`#/w/kasir`) when they sign in.
- Head office: menu *Kasir* (sales, shifts, shift closes) and the *Kerja* group for the cashier screen.

Point the owner there only after `rangka_changeset_status` says the change set is applied (`rangka-changesets`).

## Promo (module `promotions`, added next to the cashier)

When the owner asks for discounts, happy hour, "beli 2 gratis 1", a voucher code or coupons, the module
`promotions` adds them to the cashier (the change set names it in `config.modules`). What exists:

- **Item promos, applied by themselves:** percent off, rupiah off per item, or buy X get Y of the same item, for one
  item or one category, at one outlet or all, between two dates, on some days and between two hours (happy hour;
  it starts and ends within 5 minutes). If two promos hit one item, the higher *Prioritas* wins.
- **Bill promos, picked at the counter before paying:** percent off the bill with a cap, rupiah off the bill, or a
  free product, each with a minimum spend. Optionally with one shared voucher code (and a use limit) or with
  single-use coupons made in a batch (*Kiriman kupon*).
- **What it costs:** *Pemakaian promo* lists every use; the cashier dashboard shows the promo cost of the period.

Say clearly: promos are set up by the owner or head office, never by the cashier and never by you. You may explain
the kinds, suggest one in words ("Mau coba diskon 10% jam 14.00 sampai 16.00?") and open an empty *Promo* form for the
owner; you never save, switch on or change a promo, and you never decide a price or a discount. A promo the owner set
up does not wait for head office's approval; a cashier's own discount above 20% still does. A coupon is checked when
the sale is sent, and the cashier never sees voucher or coupon codes (the buyer brings them), so on a device that is
offline a voucher or coupon sale is checked once it is back online.
