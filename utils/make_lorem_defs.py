#!/usr/bin/env python3
"""
make_lorem_defs.py -- extract LoremIpsoCards/RGTCards_Lorem.csv into the format
the engine actually loads, so pasyans-3.py / pasyans-4.py can use the Lorem deck.

Why this file exists
--------------------
The Lorem sheet and the engine's ``RGTCards.csv`` are NOT the same shape:

  source  LoremIpsoCards/RGTCards_Lorem.csv
          51 rows x 16 fields, WITH a header row
          cols: Image Name, ID, -, -, Image Meaning, 1, 2, 3, 4, Duration,
                BG Colour, FG Colour, Related Images, Archetype, Category, -

  target  <wdir>/RGTCards/RGTCards.csv
          50 rows x 7 fields, NO header, every field double-quoted
          cols: 0 name, 1..4 the four position interpretations, 5 short title,
                6 card name

The engine loads it as::

    np.loadtxt(path, dtype=str, comments=None, delimiter=',', quotechar='"')

and then indexes ``card_defs[n-1, pos_code]`` with pos_code in 1..4, plus
``card_defs[n-1, 5]`` and ``card_defs[n-1, 6]``. So 7 columns is a hard floor --
fewer is an IndexError at match time, not a graceful miss.

Content gap you must know about
-------------------------------
Only ONE of the 50 source rows supplies all four position meanings. The other
49 give a single "Image Meaning" line and leave cols 1-4 empty. This script does
NOT invent fortune-telling text: it carries the real Image Meaning through and
marks the missing positions as placeholders in a report, so the author can fill
them in. Rows that still need work are listed by ID on stderr.

Usage
-----
    python3 make_lorem_defs.py                       # -> /tmp/pasyans_lorem
    python3 make_lorem_defs.py --wdir ./LoremDeck
    python3 pasyans-4.py --wdir /tmp/pasyans_lorem   # engine uses the Lorem text

The shipped PyPasyans/RGTCards/RGTCards.csv is never touched: output always
goes to <wdir>/RGTCards/RGTCards.csv, which is how the engine selects a deck.
"""
from __future__ import annotations

import argparse
import csv
import pathlib
import sys

import numpy as np

SRC_DEFAULT = pathlib.Path(__file__).resolve().parent.parent / 'LoremIpsoCards' / 'RGTCards_Lorem.csv'

# source column indexes (0-based, per the header row)
C_NAME, C_ID, C_MEANING = 0, 1, 4
C_POS = (5, 6, 7, 8)          # the four position meanings
C_DURATION, C_ARCHETYPE, C_CATEGORY = 9, 13, 14

N_COLS = 7                    # target column count -- engine indexes up to 6
PLACEHOLDER = '(meaning to be written -- position {pos} of "{name}")'


def read_source(path: pathlib.Path) -> list[dict]:
    """Parse the 16-column Lorem sheet into records, keyed by numeric ID."""
    if not path.is_file():
        raise SystemExit(f'[ERROR] source sheet not found: {path}')
    with path.open(newline='', encoding='utf-8') as fh:
        rows = list(csv.reader(fh))
    if not rows:
        raise SystemExit(f'[ERROR] {path} is empty')

    header = rows[0]
    # refuse silently if the sheet's columns ever move -- the indexes above are
    # positional, so a reordered sheet would otherwise mix meanings into suits
    expect = {'Image Name', 'ID', 'Image Meaning'}
    got = set(header)
    if not expect <= got:
        raise SystemExit(f'[ERROR] unexpected header in {path}: {header[:6]}... '
                         f'(missing {sorted(expect - got)})')

    recs, problems = [], []
    for line_no, r in enumerate(rows[1:], start=2):
        if not any(f.strip() for f in r):
            continue
        if len(r) < 9:
            problems.append(f'line {line_no}: only {len(r)} fields, skipped')
            continue
        try:
            rid = int(r[C_ID].strip())
        except ValueError:
            problems.append(f'line {line_no}: ID not an integer ({r[C_ID]!r}), skipped')
            continue
        recs.append({
            'id': rid,
            'name': r[C_NAME].strip(),
            'meaning': r[C_MEANING].strip(),
            'pos': [r[i].strip() for i in C_POS],
            'duration': r[C_DURATION].strip() if len(r) > C_DURATION else '',
            'archetype': r[C_ARCHETYPE].strip() if len(r) > C_ARCHETYPE else '',
            'category': r[C_CATEGORY].strip() if len(r) > C_CATEGORY else '',
        })
    for p in problems:
        print(f'  ! {p}', file=sys.stderr)
    return recs


def build_rows(recs: list[dict]) -> tuple[list[list[str]], list[int]]:
    """Records -> 50x7 target rows. Returns (rows, ids_still_needing_text)."""
    rows, need = [], []
    for rec in sorted(recs, key=lambda x: x['id']):
        pos = list(rec['pos'])
        missing = [i for i, v in enumerate(pos) if not v]
        if missing:
            need.append(rec['id'])
            # the real Image Meaning carries through; only the per-position
            # gradations are placeholders. Never fabricate an interpretation.
            for i in missing:
                pos[i] = rec['meaning'] or PLACEHOLDER.format(pos=i + 1, name=rec['name'])
        # col 5 = short title, col 6 = card name. Archetype is empty across the
        # whole sheet, so Category is the only grouping available for col 6.
        short = rec['duration'] or rec['category'] or 'Middle'
        name = rec['archetype'] or rec['category'] or 'Unknown'
        rows.append([rec['name'], pos[0], pos[1], pos[2], pos[3], short, name])
    return rows, need


def write_target(rows: list[list[str]], out: pathlib.Path) -> None:
    """Write exactly like the shipped file: all fields quoted, LF endings."""
    if len(rows) != 50:
        raise SystemExit(f'[ERROR] built {len(rows)} rows, expected 50')
    # NB mkdir on the PARENT. Calling mkdir on the file path itself creates a
    # directory named RGTCards.csv and the open() below then dies with
    # IsADirectoryError.
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_ALL, lineterminator='\n')
        for r in rows:
            if len(r) != N_COLS:
                raise SystemExit(f'[ERROR] row has {len(r)} fields, need {N_COLS}')
            w.writerow(r)


def verify(out: pathlib.Path) -> list[str]:
    """Re-read with the ENGINE's exact loader call -- the real acceptance test."""
    errs = []
    try:
        cd = np.loadtxt(out, dtype=str, comments=None, delimiter=',', quotechar='"')
    except Exception as exc:                              # noqa: BLE001
        return [f'engine loader rejected the file: {exc}']
    if cd.ndim != 2:
        return [f'loader gave {cd.ndim}D, engine expects 2D']
    if cd.shape[1] < N_COLS:
        errs.append(f'{cd.shape[1]} columns; engine indexes column 6 -> IndexError')
    if cd.shape[0] < 25:
        errs.append(f'{cd.shape[0]} rows; the deck needs at least 25')
    for n in (1, 25):                                     # the range the engine uses
        for pos in range(1, 7):
            if not str(cd[n - 1, pos]).strip():
                errs.append(f'card {n} column {pos} is blank (engine would show nothing)')
    return errs


def main() -> None:
    ap = argparse.ArgumentParser(
        description='extract the Lorem sheet into the engine\'s RGTCards.csv format')
    ap.add_argument('--source', default=SRC_DEFAULT, type=pathlib.Path,
                    help='the 16-column Lorem sheet')
    ap.add_argument('--wdir', default=pathlib.Path('/tmp/pasyans_lorem'), type=pathlib.Path,
                    help='working dir to emit into; writes <wdir>/RGTCards/RGTCards.csv')
    ap.add_argument('--strict', action='store_true',
                    help='refuse to write while any row still lacks real position text')
    args = ap.parse_args()

    recs = read_source(args.source)
    print(f'source: {args.source} -> {len(recs)} records '
          f'(IDs {min(r["id"] for r in recs)}..{max(r["id"] for r in recs)})')

    rows, need = build_rows(recs)

    if args.strict and need:
        raise SystemExit(f'[ERROR] --strict: {len(need)} row(s) lack all four position '
                         f'meanings: {need}\nWrite them in {args.source} and re-run.')

    out = args.wdir / 'RGTCards' / 'RGTCards.csv'
    write_target(rows, out)

    errs = verify(out)
    for e in errs:
        print(f'  ! {e}', file=sys.stderr)

    print(f'wrote {out}  ({len(rows)} rows x {N_COLS} cols, all fields quoted)')
    if need:
        print(f'NOTE: {len(need)} of {len(rows)} rows had missing per-position text; the '
              f'single Image Meaning was carried into those positions.\n'
              f'      rows needing authored text: {need}')
    if errs:
        raise SystemExit(f'[ERROR] output failed the engine-loader check ({len(errs)} problem(s))')
    print('engine-loader check: OK')
    print(f'run it:  python3 pasyans-4.py --wdir {args.wdir}')


if __name__ == '__main__':
    main()
