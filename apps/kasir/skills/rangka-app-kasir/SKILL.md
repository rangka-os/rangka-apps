---
name: rangka-app-kasir
description: "Rangka Kasir (app kasir): the browser cashier for shops and F&B outlets, with shifts per outlet, numbered receipts, cash and QRIS, stock and recipes taken out automatically. Use when an owner asks for a cashier or POS (\"mau pakai kasir\", \"tambahin POS\", \"jualan langsung di outlet\"), to explain what it shares with apps they already run, and for what to set up after it is added."
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
