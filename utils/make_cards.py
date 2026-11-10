#!/usr/bin/env python3
"""
make_cards.py -- render a set of Pasyans cards from a CardList.csv + source images.

This is the *forward* half of the set round-trip:

    CardList.csv  +  source images   --[make_cards.py]-->   RGCard-<i>.tiff

``compose_set.py`` renders a *random* set (it samples a plan).  This util is
plan-driven: it reads an existing ``CardList.csv`` -- the signed edge/wedge
matrix the engine already uses -- and composes exactly those cards, so a deal
plan can be turned back into card art deterministically.

CardList.csv format
-------------------
25 rows x 4 signed ints, CR-only line endings, no header.  Each row is one
card's four wedges in the engine's native CCW order [T, L, B, R]; a value is a
source id where ``-n`` is the LEFT half of source n and ``+n`` is the RIGHT half
(identical to RGTList.csv -- the edge matrix doubles as the wedge plan).

Reuses the geometry from ``compose_set.py`` (load_pool / compose_card /
label_card / validate_plan) rather than reimplementing the wedge math.

Usage
-----
    python make_cards.py --cardlist ../RGTCards/CardList.csv \
                         --sources ../LoremIpsoCards \
                         --out /tmp/cards
"""
from __future__ import annotations

import argparse
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import compose_set as cs  # noqa: E402


def load_cardlist(path: pathlib.Path) -> list[list[int]]:
    """Parse a CardList.csv into a list of 4-int wedge plans.

    Tolerates CR-only, LF, or CRLF line endings (the shipped file is CR-only).
    Each non-empty row must have exactly 4 signed integers.
    """
    text = path.read_text()
    plan: list[list[int]] = []
    for line_no, line in enumerate(text.replace('\r', '\n').split('\n'), 1):
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(',') if p.strip() != '']
        if len(parts) != 4:
            raise ValueError(f'{path.name}:{line_no}: expected 4 fields, got {len(parts)}: {line!r}')
        try:
            plan.append([int(p) for p in parts])
        except ValueError as exc:
            raise ValueError(f'{path.name}:{line_no}: non-integer field: {exc}') from exc
    return plan


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='render cards from a CardList.csv plan')
    ap.add_argument('--cardlist', default=_HERE.parent / 'RGTCards' / 'CardList.csv',
                    type=pathlib.Path, help='signed edge/wedge matrix (25x4)')
    ap.add_argument('--sources', default=_HERE.parent / 'LoremIpsoCards',
                    type=pathlib.Path, help='directory of numbered source images')
    ap.add_argument('--out', default=pathlib.Path('/tmp/cards'),
                    type=pathlib.Path, help='output dir for RGCard-<i>.tiff')
    ap.add_argument('--no-label', action='store_true',
                    help='skip the source-number label bar')
    args = ap.parse_args(argv)

    plan = load_cardlist(args.cardlist)
    print(f'[make_cards] plan: {len(plan)} cards from {args.cardlist}')

    pool = cs.load_pool(args.sources)
    print(f'[make_cards] pool: {len(pool)} sources from {args.sources}')

    # Surface plan problems (missing sources, sibling halves, ...) without
    # aborting -- the engine tolerates partial pools by skipping wedges.
    for warn in cs.validate_plan(plan):
        print(f'  [plan] {warn}', file=sys.stderr)

    args.out.mkdir(parents=True, exist_ok=True)
    written = 0
    for i, ids in enumerate(plan):
        try:
            card = cs.compose_card(ids, pool)
        except KeyError as exc:
            print(f'  card {i:02d}: missing source {exc}, skipped', file=sys.stderr)
            continue
        if not args.no_label:
            card = cs.label_card(card, ids)
        card.save(args.out / f'RGCard-{i}.tiff')
        written += 1
        print(f'  card {i:02d}  T{ids[0]:+d} L{ids[1]:+d} B{ids[2]:+d} R{ids[3]:+d}')

    print(f'[make_cards] wrote {written} cards to {args.out}')
    return 0 if written == len(plan) else 1


if __name__ == '__main__':
    raise SystemExit(main())
