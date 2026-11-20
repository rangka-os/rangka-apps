"""Points and deposit arithmetic of module loyalty (rangka-apps#102), written once in Python from the issue's rules so the
day-one scenarios compare the kernel's computes against an independent calculation.

    python3 tests/loyalty_oracle.py      # runs the vectors below

The rules (modules/pos/models/pos_sale.rk, modules/loyalty):
- member discount offer = round(subtotal x tier discount %); bill promo offer as module promotions computes it;
  the larger of the two applies, never both, a tie goes to the promo; a free gift counts as the bill promo at its price.
- points discount = round(points x point value); at most round((subtotal - bill promo - member discount) x max %).
- grand total = subtotal - bill promo - member discount - points discount - manual receipt discount.
- points earned = int(grand total x multiplier % / (rupiah per point x 100)), 0 without a rate.
- amount due (cash and QRIS) = grand total - deposit used; deposit used <= grand total and <= the member's balance.
- PPN (PKP, prices include it) on the whole grand total: the deposit is a payment, not a discount.
"""
import math


def rnd(x):
    """CEL round(): half away from zero."""
    return int(math.floor(x + 0.5)) if x >= 0 else -int(math.floor(-x + 0.5))


def receipt(subtotal, tier=None, bill_promo_offer=0, gift_value=0, points=0, deposit=0, manual=0, pkp=True):
    """The member part of one receipt. tier: dict with member_discount_pct, point_value, max_redeem_pct, earn_spend,
    earn_multiplier_pct (None: no member)."""
    t = tier or {}
    member_offer = rnd(subtotal * t.get("member_discount_pct", 0) / 100) if tier else 0
    member_wins = member_offer > bill_promo_offer + gift_value
    member_discount = member_offer if member_wins else 0
    bill_promo = 0 if member_wins else bill_promo_offer
    points_discount = rnd(points * t.get("point_value", 0)) if tier else 0
    points_cap = rnd((subtotal - bill_promo - member_discount) * t.get("max_redeem_pct", 0) / 100) if tier else 0
    grand = subtotal - bill_promo - member_discount - points_discount - manual
    earn_spend = t.get("earn_spend", 0)
    earned = int(grand * t.get("earn_multiplier_pct", 100) / (earn_spend * 100)) if tier and earn_spend > 0 and grand > 0 else 0
    ppn = rnd(grand * 12 * 11 / (100 * 12 + 12 * 11)) if pkp else 0
    return {
        "member_discount": member_discount, "bill_promo": bill_promo, "points_discount": points_discount,
        "points_cap": points_cap, "points_ok": points_discount <= points_cap, "grand_total": grand,
        "points_earned": earned, "deposit_used": deposit, "amount_due": grand - deposit, "deposit_ok": deposit <= grand,
        "ppn_in_sales": ppn,
    }


def sale_journal(r, cogs=0):
    """gl_pos_sale_post lines as {account: amount} for a receipt from receipt() (PPh final 0,5% of the net)."""
    pph = rnd((r["grand_total"] - r["ppn_in_sales"]) * 5 / 1000)
    lines = {"11150": r["grand_total"] - r["deposit_used"], "41100": -(r["grand_total"] - r["ppn_in_sales"]),
             "21200": -r["ppn_in_sales"], "69100": pph, "21310": -pph, "51100": cogs, "11400": -cogs,
             "21400": r["deposit_used"]}
    return {k: v for k, v in lines.items() if v != 0}


def balances(entries):
    """Ledger rows [(kind, amount)] to a running balance; refuses (None) when a spend or refund would go below 0."""
    bal = 0
    for kind, amount in entries:
        if kind in ("spend", "refund") and bal + amount < 0:
            return None
        bal += amount
    return bal


MEMBER = {"member_discount_pct": 0, "point_value": 100, "max_redeem_pct": 50, "earn_spend": 10000, "earn_multiplier_pct": 100}
SILVER = {"member_discount_pct": 5, "point_value": 100, "max_redeem_pct": 50, "earn_spend": 10000, "earn_multiplier_pct": 125}
GOLD = {"member_discount_pct": 10, "point_value": 100, "max_redeem_pct": 50, "earn_spend": 10000, "earn_multiplier_pct": 150}

VECTORS = [
    # the issue's own: Rp 87.500 at Rp 10.000 per point earns 8; 100 points at Rp 100 take Rp 10.000 off
    (dict(subtotal=87500, tier=MEMBER), {"points_earned": 8, "grand_total": 87500}),
    (dict(subtotal=44000, tier=MEMBER, points=100), {"points_discount": 10000, "grand_total": 34000, "points_earned": 3, "points_ok": True}),
    # cap 50%: 300 points (30.000) on a 44.000 bill is over 22.000
    (dict(subtotal=44000, tier=MEMBER, points=300), {"points_cap": 22000, "points_ok": False}),
    # Silver 5% (2.200) against a 10% promo (4.400): the promo wins; against 2% (880): the member discount wins
    (dict(subtotal=44000, tier=SILVER, bill_promo_offer=4400), {"member_discount": 0, "bill_promo": 4400, "grand_total": 39600, "points_earned": 4}),
    (dict(subtotal=44000, tier=SILVER, bill_promo_offer=880), {"member_discount": 2200, "bill_promo": 0, "grand_total": 41800, "points_earned": 5}),
    # a tie goes to the promo
    (dict(subtotal=44000, tier=SILVER, bill_promo_offer=2200), {"member_discount": 0, "bill_promo": 2200}),
    # a free gift worth 4.000 against Silver 5% of 100.000 (5.000): the member discount is larger
    (dict(subtotal=100000, tier=SILVER, gift_value=4000), {"member_discount": 5000, "bill_promo": 0}),
    # Gold 10% and 150%: 200.000 -> 20.000 off, 180.000 earns 27
    (dict(subtotal=200000, tier=GOLD), {"member_discount": 20000, "grand_total": 180000, "points_earned": 27}),
    # deposit 30.000 on 44.000: 14.000 in cash; PPN on the whole 44.000
    (dict(subtotal=44000, tier=MEMBER, deposit=30000), {"amount_due": 14000, "ppn_in_sales": 4360, "deposit_ok": True}),
    (dict(subtotal=44000, tier=MEMBER, deposit=50000), {"deposit_ok": False}),
    # no member: nothing changes
    (dict(subtotal=44000), {"grand_total": 44000, "points_earned": 0, "amount_due": 44000}),
]


def main():
    bad = []
    for args, want in VECTORS:
        got = receipt(**args)
        for k, v in want.items():
            if got[k] != v:
                bad.append((args, k, got[k], v))
    j = sale_journal(receipt(44000, MEMBER, deposit=30000))
    if sum(j.values()) != 0 or j.get("21400") != 30000 or j.get("11150") != 14000:
        bad.append(("journal", j))
    if balances([("top_up", 100000), ("spend", -30000), ("spend", -40000)]) != 30000 or balances([("top_up", 50000), ("spend", -60000)]) is not None:
        bad.append(("balances",))
    for b in bad:
        print("FAIL", b)
    print(f"loyalty oracle: {len(VECTORS)} vectors, {len(bad)} failed")
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
