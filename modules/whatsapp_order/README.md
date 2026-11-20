# whatsapp_order: orders customers write on WhatsApp

A customer writes to the business's WhatsApp number ("pesan 2 kopi susu, 1 roti"). The order becomes a draft, priced
from the business's own item records. Staff confirm it with one tap on the work screen **Pesanan dari WhatsApp**
(`pesanan_wa`, app `pesanan_whatsapp`). Confirming creates the customer when needed and a draft `pos_sale` on the
cashier's open shift, then replies to the customer with the items and the total (template `pesanan_diterima`).
Nothing becomes an order without a person's confirm. The rules are in `module.rk` and the comments of each flow.

## Two steps, one path

1. **Message into lines.** For a text, Rangka Cloud does this (`rangka-cloud internal/whatsapp/orderparse`):
   deterministic, no catalogue, no LLM. It gives `lines: [{qty, name, note, text, …}]`, `notes` and `order_signal`
   on the connector trigger `rangka_whatsapp.message_received`.
2. **Lines into a priced draft.** Flow `whatsapp_order_intake` does this for any line:
   - a line with `sku` is matched by `item.sku` only;
   - a line with `name` is matched by the same name, part of the name, or the spelling skeleton. One candidate fills
     the line, several make a choice for staff, none leaves an unknown line flagged as a note;
   - a `price` on a line is kept as `quoted_rate` and only compared. The charged price is always the item's rate, and
     a difference puts the order in "Perlu dicek".

   The draft records its `source` (`whatsapp_text` today). Each message is kept once in `whatsapp_inbound`, with its
   raw text, its Meta message type (`kind`) and the lines read from it, for audit and for a later shared inbox
   (rangka-apps#130).

Names stay neutral (order, line, item, customer), so the same path fits a reseller re-ordering stock or a course
registration, not only a café.

## Next: A1b (interactive messages and catalogue carts)

Not built here. How it plugs in:

- **Catalogue cart** (Meta webhook message type `order`: `product_retailer_id`, `quantity`, `item_price`). Rangka
  Cloud's intake maps each product to a line `{qty: quantity, sku: product_retailer_id, price: item_price, text}` and
  sends `source: whatsapp_cart` and `order_signal: true`. The connector `rangka_whatsapp` adds `source` to its
  `message_received` emit. The order then follows the same path: SKU match, price from the item, mismatch flagged,
  one-tap confirm, the same reply. It needs catalogue sync to Meta (item SKU = retailer id). That is a separate piece
  of work and costs money once real Meta is used.
- **Reply buttons and list messages** (`interactive` replies). The intake turns a picked row into one line with
  `sku`, or into a staff note. A button that confirms an order still only records the customer's yes; a person
  confirms the order.
- **WhatsApp Flows forms** (for example a course registration). Same idea: the form's answer becomes lines or notes
  on a draft, never a confirmed document by itself.
- Sending interactive messages needs a new relay action in Rangka Cloud. Templates stay the only path outside the
  24-hour window.
