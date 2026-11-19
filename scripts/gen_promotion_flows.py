#!/usr/bin/env python3
"""Writes modules/promotions/flows/promotion_apply*.rk: one set of steps, three triggers.

    python3 scripts/gen_promotion_flows.py

promotion_apply runs every 5 minutes; promotion_apply_on_create and promotion_apply_on_update run the same steps when
a promo is saved, so a new or changed promo reaches the counter at once. A flow has one trigger, so the steps are
written here once. The steps:
1. live of every promo: active, today within valid_from..valid_until and its days (1 Monday .. 7 Sunday), and the
   site's wall clock (clock(), rangka-os contract 0069 §3) within time_from..time_until; written only when it changed.
2. Every item that has a promo now, and every item a live item promo covers, gets the winning promo's fields: the
   live counter promo (channel kasir or both) with the highest priority, ties to the lower name; and for invoices the
   live percent promo of channel invoice or both for all outlets. An item is written only when a field changes, so a
   run that changes nothing writes nothing (no audit noise, no new catalogue data set for paired devices).
Do not edit the generated flows by hand: change this script and run it again.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLOWS = os.path.join(ROOT, "modules", "promotions", "flows")

LIVE = ("p.active && p.valid_from <= today() && p.valid_until >= today()"
        " && (p.days == '' || p.days.contains(string(days_between('2024-01-01', today()) % 7 + 1)))"
        " && (p.time_from == '' || clock() >= p.time_from && clock() < p.time_until)")


def covers(p):
    return f"({p}.scope == 'item' && {p}.item == it.name || {p}.scope == 'category' && {p}.category != '' && {p}.category == it.category)"


def winner(lst):
    better = "(q.priority > p.priority || q.priority == p.priority && q.name < p.name)"
    return f"{lst}.filter(p, {covers('p')} && {lst}.filter(q, {covers('q')} && {better}).size() == 0)"


# the item fields and the value each takes from the winner w (a list of 0 or 1 promo) and the invoice winner v
FIELDS = [
    ("promo", "w.size() > 0 ? w[0].name : ''"),
    ("promo_name", "w.size() > 0 ? w[0].promo_name : ''"),
    ("promo_kind", "w.size() == 0 ? '' : (w[0].kind == 'item_pct' ? 'pct' : (w[0].kind == 'item_off' ? 'off' : 'buy_get'))"),
    ("promo_pct", "w.size() > 0 && w[0].kind == 'item_pct' ? w[0].pct : 0"),
    ("promo_off", "w.size() > 0 && w[0].kind == 'item_off' ? w[0].amount_off : 0"),
    ("promo_buy", "w.size() > 0 && w[0].kind == 'buy_get' ? w[0].buy_qty : 0"),
    ("promo_get", "w.size() > 0 && w[0].kind == 'buy_get' ? w[0].get_qty : 0"),
    ("promo_outlet", "w.size() > 0 ? w[0].only_outlet : ''"),
    ("promo_until", "w.size() > 0 ? w[0].valid_until : null"),
    ("promo_invoice_pct", "v.size() > 0 ? v[0].pct : 0"),
]


def q(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def apply_item(ind):
    """Steps that write the winners onto the item in variable it."""
    pad = " " * ind
    changed = " || ".join(f"it.{f} != ({e})" for f, e in FIELDS)
    out = [f"{pad}- for_each: {q('[' + winner('ks') + ']')}",
           f"{pad}  as: w",
           f"{pad}  do:",
           f"{pad}    - for_each: {q('[' + winner('vs') + ']')}",
           f"{pad}      as: v",
           f"{pad}      do:",
           f"{pad}        - update: it",
           f"{pad}          if: {q(changed)}",
           f"{pad}          set:"]
    out += [f"{pad}            {f}: {q(e)}" for f, e in FIELDS]
    return out


def steps():
    out = ["steps:",
           "  # 1. live: active, in its dates, days and hours now",
           "  - find_all: promotion",
           "    as: all",
           "  - for_each: all",
           "    as: p",
           "    do:",
           "      - update: p",
           f"        if: {q('p.live != (' + LIVE + ')')}",
           "        set:",
           f"          live: {q(LIVE)}",
           "  # 2. the live item promos for the counter (ks) and for invoices (vs)",
           "  - find_all: promotion",
           "    where: \"live && !is_bill && channel != 'invoice'\"",
           "    as: ks",
           "  - find_all: promotion",
           "    where: \"live && kind == 'item_pct' && channel != 'kasir' && only_outlet == ''\"",
           "    as: vs",
           "  # items that carry a promo now (it may have ended) ...",
           "  - find_all: item",
           "    where: \"promo != '' || promo_invoice_pct != 0\"",
           "    as: had",
           "  - for_each: had",
           "    as: it",
           "    do:"]
    out += apply_item(6)
    out += ["  # ... and the items each live item promo covers",
            "  - for_each: ks + vs",
            "    as: pr",
            "    do:",
            "      - find_all: item",
            "        if: \"pr.scope == 'item'\"",
            "        where: name == pr.item",
            "        as: one",
            "      - find_all: item",
            "        if: \"pr.scope == 'category'\"",
            "        where: category == pr.category",
            "        as: cat",
            "      - for_each: \"pr.scope == 'item' ? one : cat\"",
            "        as: it",
            "        do:"]
    out += apply_item(10)
    return out


HEAD = "# yaml-language-server: $schema=https://rangka-os.github.io/rangka-os/schema/0.5/flow.json\n" \
       "# Generated by scripts/gen_promotion_flows.py (do not edit by hand). {what}\n"

VARIANTS = [
    ("promotion_apply", "Every 5 minutes: promos start and end on their dates, days and hours (happy hour precision 5 minutes).",
     "label: {id: 'Promo: perbarui yang sedang berlaku'}\nschedule: '*/5 * * * *'\n"),
    ("promotion_apply_on_create", "A new promo reaches the items at once.",
     "label: {id: 'Promo baru: pasang ke barang'}\non: promotion.created\n"),
    ("promotion_apply_on_update", "A changed promo reaches the items at once; the flow's own write of live changes nothing the second time.",
     "label: {id: 'Promo diubah: perbarui barang'}\non: promotion.updated\n"),
]


def main():
    body = "\n".join(steps()) + "\n"
    for name, what, trig in VARIANTS:
        with open(os.path.join(FLOWS, name + ".rk"), "w", encoding="utf-8") as f:
            f.write(HEAD.format(what=what) + f"name: {name}\n" + trig + body)
    print("wrote", ", ".join(v[0] for v in VARIANTS))


if __name__ == "__main__":
    main()
