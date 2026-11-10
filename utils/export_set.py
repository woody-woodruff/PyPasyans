#!/usr/bin/env python3
"""
export_set.py -- compose a 25-card set AND emit the asset tree the Spyder
engine (pasyans-3.py) loads, into its own working directory.

Emits, under <wdir>/RGTCards/:

    RGCard-<i>.tiff   25 composed card faces, board index i = 0..24
    RGTList.csv       edge-value matrix, 25 x 4, signed, np.byte  (REQUIRED by engine)
    RGTCards.csv      interpretations, 25 x 7, quoted strings      (optional but needed
                      for the "Show Details" text)
    set.json          manifest: board index -> card ids -> source art

Run it with:   python pasyans-3.py --wdir <wdir>

Engine contract this file is written against (verified against pasyans-3.py):
  * card_list  = np.loadtxt(RGTList.csv,  dtype=np.byte, delimiter=',')      -> (25, 4)
  * card_defs  = np.loadtxt(RGTCards.csv, dtype=str, delimiter=',', quotechar='"',
                            comments=None)                                   -> (25, >=7)
  * text lookup uses card_defs[n-1, pos_code] with pos_code in 1..4, plus
    card_defs[n-1, 5] (short title) and card_defs[n-1, 6] (card name).
    => fewer than 7 columns is an IndexError, not a graceful miss. Guarded below.

Card-id convention (unchanged): -n = LEFT half of source n, +n = RIGHT half.
Wedge discipline: every wedge used once per set; no card holds both halves of
one image. Enforced by compose_set.sample_set() and re-proven by validate_plan().
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import random
import re
from typing import Dict, List

import numpy as np

import compose_set as cs

DECK = 25
GRID = 5
N_DEF_COLS = 7          # cols 0..6; engine indexes up to 6


# --------------------------------------------------------------- edge values
def adjacencies() -> List[tuple]:
    """(a, side_a, b, side_b) for every touching pair in the 5x5 grid."""
    out = []
    for r in range(GRID):
        for c in range(GRID):
            i = r * GRID + c
            if c + 1 < GRID:
                out.append((i, 'right', i + 1, 'left'))
            if r + 1 < GRID:
                out.append((i, 'bottom', i + GRID, 'top'))
    return out


def build_edges(rng: random.Random, edge_range: int, n_pairs: int = 14) -> np.ndarray:
    """Edge matrix (25, 4), signed ints in [-edge_range,-1] U [1,edge_range].

    A match is two touching edges that sum to zero, so a fully random matrix
    would often yield a board with very few matches.  Here `n_pairs` adjacencies
    are made complementary on purpose, so every set is actually playable.
    Column order must match the engine's position convention: top, right,
    bottom, left (see compose_set.SLOTS).
    """
    pool = [v for v in range(-edge_range, edge_range + 1) if v != 0]
    edges = np.array([[rng.choice(pool) for _ in cs.SLOTS] for _ in range(DECK)],
                     dtype=np.int8)

    col = {name: k for k, name in enumerate(cs.SLOTS)}
    adj = adjacencies()
    for (a, sa, b, sb) in rng.sample(adj, min(n_pairs, len(adj))):
        v = int(edges[a, col[sa]])
        edges[b, col[sb]] = np.int8(-v)      # complementary -> guaranteed match
    return edges


# ------------------------------------------------------------- interpretations
NAMES = {
    'best':   ('Fulfilment', 'A wish granted', 'A clear gain', 'A lasting joy'),
    'good':   ('A steady gain', 'Help arrives', 'A door opens', 'Quiet progress'),
    'warn':   ('Delay, then a settling', 'A small loss', 'Words best left unsaid',
               'Effort without return'),
    'bad':    ('A disappointment', 'A plan comes apart', 'A loss remembered',
               'A friendship strained'),
    'when':   ('Immediate to short', 'Middle', 'Slow to long', 'Within the year'),
    'who':    ('Cavalier', 'Clover', 'News', 'Ship', 'Ring', 'Coin', 'Letter', 'Coffin'),
}


def card_title(art_name: str, rng: random.Random) -> str:
    return art_name if art_name else f'Card {rng.randrange(1000):03d}'


def build_defs(plan: List[List[int]], art_names: Dict[int, str],
               rng: random.Random) -> List[List[str]]:
    """25 x 7 rows, same column layout as the shipped RGTCards.csv.

    Col 0 name, cols 1..4 the four position interpretations (best..worst),
    col 5 short timing, col 6 the card name shown in the details panel.
    """
    rows = []
    for ids in plan:
        # name the card after its TOP wedge art, the primary view
        title = card_title(art_names.get(abs(ids[0]), ''), rng)
        r = rng
        rows.append([
            title,
            r.choice(NAMES['best']),
            r.choice(NAMES['good']),
            r.choice(NAMES['warn']),
            r.choice(NAMES['bad']),
            r.choice(NAMES['when']),
            r.choice(NAMES['who']),
        ])
    return rows


# ------------------------------------------------------------------ writers
def write_csv(path: pathlib.Path, rows: List[List[str]]) -> None:
    """Quote every field: the engine parses with quotechar='"'."""
    with path.open('w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_ALL, lineterminator='\n')
        for r in rows:
            w.writerow(r)


def verify(wdir: pathlib.Path) -> List[str]:
    """Re-read the emitted files using the ENGINE's exact loadtxt calls."""
    errs: List[str] = []
    d = wdir / 'RGTCards'

    try:
        cl = np.loadtxt(d / 'RGTList.csv', dtype=np.byte, delimiter=',')
        if cl.shape != (DECK, 4):
            errs.append(f'RGTList.csv shape {cl.shape}, engine expects ({DECK}, 4)')
    except Exception as e:                                  # noqa: BLE001
        errs.append(f'RGTList.csv failed engine load: {e}')
        cl = None

    try:
        cd = np.loadtxt(d / 'RGTCards.csv', dtype=str, comments=None,
                        delimiter=',', quotechar='"')
        if cd.ndim != 2 or cd.shape[0] != DECK:
            errs.append(f'RGTCards.csv shape {cd.shape}, expects ({DECK}, N)')
        elif cd.shape[1] < N_DEF_COLS:
            # engine does card_defs[n-1, 6] -> IndexError, not a graceful miss
            errs.append(f'RGTCards.csv has {cd.shape[1]} cols, engine needs >= {N_DEF_COLS}')
    except Exception as e:                                  # noqa: BLE001
        errs.append(f'RGTCards.csv failed engine load: {e}')
        cd = None

    missing = [i for i in range(DECK) if not (d / f'RGCard-{i}.tiff').exists()]
    if missing:
        errs.append(f'missing faces for board indices {missing}')

    if cl is not None:
        n_match = sum(1 for (a, sa, b, sb) in adjacencies()
                      if int(cl[a, cs.SLOTS.index(sa)]) + int(cl[b, cs.SLOTS.index(sb)]) == 0)
        print(f'  playable check: {n_match} complementary adjacency pair(s)')
        if n_match == 0:
            errs.append('no matchable pairs in edge matrix -- board would be dead')
    return errs


# --------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description='compose a set and emit a Spyder-ready asset tree')
    ap.add_argument('--sources', default=pathlib.Path(__file__).resolve().parent.parent / 'LoremIpsoCards', type=pathlib.Path)
    ap.add_argument('--wdir', default=pathlib.Path('/tmp/pasyans_set'), type=pathlib.Path,
                    help='working dir to emit into; run with: python pasyans-3.py --wdir <wdir>')
    ap.add_argument('--seed', default=None, type=int)
    ap.add_argument('--edge-range', default=12, type=int, dest='edge_range')
    ap.add_argument('--match-pairs', default=14, type=int, dest='match_pairs',
                    help='adjacencies forced to be complementary (playable matches)')
    ap.add_argument('--face-size', default=440, type=int, dest='face_size',
                    help='rendered face size in px; engine resizes to --size')
    args = ap.parse_args()

    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(1, 2**31)
    rng = random.Random(seed)

    pool = cs.load_pool(args.sources)
    if len(pool) < 4:
        raise SystemExit(f'need >= 4 numbered sources, found {len(pool)} in {args.sources}')

    # art display names, e.g. '07-SomeName.png' -> 'SomeName'
    art_names = {n: (re.sub(r'^\d+[-_ ]*', '', s.path.stem) or '') for n, s in pool.items()}

    plan = cs.sample_set(rng, sorted(pool), DECK)
    problems = cs.validate_plan(plan)
    if problems:
        for p in problems:
            print(f'  ! {p}')
        raise SystemExit(f'plan failed validation with {len(problems)} problem(s)')
    print(f'plan validated ({seed}): {len(plan)} cards, every wedge once, '
          f'no card holds both halves of one image')

    card_dir = args.wdir / 'RGTCards'
    card_dir.mkdir(parents=True, exist_ok=True)

    # ---- faces: named by BOARD INDEX, which is what the engine loads --------
    cs.CARD_PX = args.face_size          # rendered size; engine resizes anyway
    for i, ids in enumerate(plan):
        face = cs.compose_card(ids, pool).convert('RGB')
        face.save(card_dir / f'RGCard-{i}.tiff')
    print(f'  wrote {DECK} faces -> {card_dir}/RGCard-0..{DECK-1}.tiff')

    # ---- the two text files (this set's own copies; shipped ones untouched) --
    edges = build_edges(rng, args.edge_range, args.match_pairs)
    np.savetxt(card_dir / 'RGTList.csv', edges, fmt='%d', delimiter=',')

    defs = build_defs(plan, art_names, rng)
    write_csv(card_dir / 'RGTCards.csv', defs)
    print(f'  wrote RGTList.csv {edges.shape} and RGTCards.csv '
          f'{len(defs)}x{len(defs[0])} (this set\'s own text)')

    # ---- manifest -----------------------------------------------------------
    manifest = {
        'seed': seed,
        'sources_dir': str(args.sources),
        'slot_order': list(cs.SLOTS),
        'id_convention': 'negative = LEFT half of source, positive = RIGHT half',
        'edge_range': args.edge_range,
        'match_pairs_forced': args.match_pairs,
        'note': 'board index is the engine key; card ids are art-only and must '
                'never be stored in the engine rots byte.',
        'cards': [
            {'board_index': i, 'card_ids': ids,
             'art': [art_names.get(abs(c), '') for c in ids]}
            for i, ids in enumerate(plan)
        ],
    }
    (card_dir / 'set.json').write_text(json.dumps(manifest, indent=2))

    print('verifying against the engine loaders:')
    errs = verify(args.wdir)
    for e in errs:
        print(f'  ! {e}')
    if errs:
        raise SystemExit(f'export FAILED verification with {len(errs)} problem(s)')
    print(f'  OK -- run:  python pasyans-3.py --wdir {args.wdir}')


if __name__ == '__main__':
    main()
