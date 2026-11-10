#!/usr/bin/env python3
"""
make_set.py — generate a random Pasyans card SET from a pool of source images.

DRAFT v0.1 — for review before wiring into pasyans-3.py.

A "set" is the 25 card faces (5x5 board) plus the edge-value table that the
Spyder engine consumes.  This script:

  1. samples 25 signed card ids from [-50,-1] U [1,50]
       sign(c) < 0 -> LEFT  half of source |c|
       sign(c) > 0 -> RIGHT half of source |c|
  2. assigns each card 4 edge values (same rules as CardDataGenerator)
  3. composites each card face from its source half
  4. writes RGCard-<i>.tiff for board index i = 0..24   <-- what the engine loads
  5. writes set.json, the id <-> index <-> source manifest

The engine addresses cards by BOARD INDEX (0..24), never by card id.  Card id
lives only in this manifest and in the art filename.  (Do not let card id sign
leak into the engine's `rots` byte -- that byte already means something else.)

Usage
-----
    python make_set.py --sources ./sources --out ./PyPasyans/RGTCards \
                       --seed 20260329 --edge-range 12

    # then run the game against the freshly written set
    python pasyans.py --wdir ./PyPasyans

Note: pasyans-3.py cannot be imported as-is (dash in module name).  Either
duplicate the compositor here (done below, deliberately small) or add
`ln -s pasyans-3.py pasyans.py` and import from it.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

import numpy as np
from PIL import Image

DECK_SIZE  = 25          # 5x5 board -- matches DECK_SIZE in pasyans-3.py
ID_LIMIT   = 50          # card ids are +/- 1 .. +/- 50
EDGE_ORDER = ('top', 'right', 'bottom', 'left')


# --------------------------------------------------------------------------
# id / filename encoding
# --------------------------------------------------------------------------
def card_id_to_stem(c: int) -> str:
    """-7 -> 'RGCard-m07', 7 -> 'RGCard-p07'.  No bare leading minus."""
    if c == 0:
        raise ValueError("card id 0 is not a valid id")
    return f"RGCard-{'m' if c < 0 else 'p'}{abs(c):02d}"


def stem_to_card_id(stem: str) -> int:
    """Inverse of card_id_to_stem."""
    body = stem.replace('RGCard-', '')
    sign, digits = body[0], body[1:]
    return -int(digits) if sign == 'm' else int(digits)


def side_of(c: int) -> str:
    return 'L' if c < 0 else 'R'


# --------------------------------------------------------------------------
# set sampling
# --------------------------------------------------------------------------
def sample_card_ids(rng: random.Random,
                    n: int,
                    available: List[int],
                    no_siblings: bool = True) -> List[int]:
    """Draw n signed card ids from the source numbers that actually exist.

    The id space is built from ``available`` (the numbers present on disk), NOT
    from the theoretical 1..ID_LIMIT range -- a set must never reference a source
    that was not loaded.

    Sibling = -n and +n, the two halves of one source diamond.  With
    no_siblings=True at most one of a sibling pair may appear in a set.
    """
    if not available:
        raise ValueError('no sources available to sample from')

    # every available number contributes two candidate ids, -n and +n
    ids = [c for num in available for c in (-num, num)]

    if not no_siblings:
        if n > len(ids):
            raise ValueError(f'asked for {n} cards but only {len(ids)} ids available')
        return rng.sample(ids, n)

    if n > len(available):
        raise ValueError(
            f'sibling-free set of {n} needs {n} distinct sources, only {len(available)} available'
        )
    # pick n distinct source numbers, then a side for each
    return [rng.choice([-num, num]) for num in rng.sample(sorted(available), n)]


def sample_edges(rng: random.Random,
                 edge_range: int,
                 allow_duplicates: bool = False) -> List[int]:
    """4 edge values, drawn from [-N,-1] U [1,N].

    Mirrors CardDataGenerator's rule set.  Edge values are a SEPARATE numbering
    system from card ids -- both use signed integers, do not conflate them.
    """
    pool = [i for i in range(-edge_range, edge_range + 1) if i != 0]
    if allow_duplicates:
        return [rng.choice(pool) for _ in EDGE_ORDER]
    return rng.sample(pool, 4)


# --------------------------------------------------------------------------
# compositor (kept deliberately small; see pasyans-3.py CardArtGenerator for
# the full-quality version with vignette/back art)
# --------------------------------------------------------------------------
def half_of(img: Image.Image, side: str, axis: str = 'vertical') -> Image.Image:
    """Return the left or right half of a source, padded back to full size.

    The discarded half becomes fully transparent so layers alpha-composite
    without a hard seam -- same convention as _split_and_pad in pasyans-3.py.
    """
    w, h = img.size
    box = (0, 0, w // 2, h) if side == 'L' else (w // 2, 0, w, h)
    kept = img.crop(box)
    out = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    out.paste(kept, (0, 0) if side == 'L' else (w // 2, 0))
    return out


def compose_face(sources: Dict[int, Image.Image],
                 card_id: int,
                 size: int,
                 bg: tuple = (15, 10, 30, 255)) -> Image.Image:
    """Composite one card face from the source named by abs(card_id).

    TODO(unconfirmed geometry): the Pasyans design wants FOUR triangular wedges
    (left/right/top/bottom halves of four different diamond canvases) partition-
    ing the square.  This v0.1 reproduces the existing 2-layer half stack so the
    plumbing can be validated first.  Swap the body for a 4-wedge partition once
    the slot rotation table in pasyans-card-spec.md §3.3 is confirmed.
    """
    src = sources[abs(card_id)].img.convert('RGBA').resize((size, size), Image.LANCZOS)
    face = Image.new('RGBA', (size, size), bg)
    face.alpha_composite(half_of(src, side_of(card_id)))
    return face


# --------------------------------------------------------------------------
# writer
# --------------------------------------------------------------------------
@dataclass
class Source:
    """One numbered source image: where it came from and its pixels."""
    number: int
    path: pathlib.Path
    img: Image.Image


@dataclass
class Slot:
    board_index: int
    card_id: int
    side: str
    source: str
    stem: str
    edges: Dict[str, int]


def build_set(sources: Dict[int, Image.Image],
              rng: random.Random,
              edge_range: int,
              size: int,
              no_siblings: bool,
              allow_duplicates: bool) -> List[Slot]:
    ids = sample_card_ids(rng, DECK_SIZE, sorted(sources), no_siblings=no_siblings)
    slots = []
    for i, cid in enumerate(ids):
        src_path = sources[abs(cid)].path
        slots.append(Slot(
            board_index=i,
            card_id=cid,
            side=side_of(cid),
            source=str(src_path),
            stem=card_id_to_stem(cid),
            edges=dict(zip(EDGE_ORDER, sample_edges(rng, edge_range, allow_duplicates))),
        ))
    return slots


def load_source_pool(source_dir: pathlib.Path) -> Dict[int, Image.Image]:
    """Map source NUMBER -> image.

    Numbering contract: a source file is named by the diamond number it
    represents, e.g. ``src-07.png`` or ``07.png`` -> number 7.  Both signs
    (-7 and +7) draw from the same file.  Files whose name yields no number
    are skipped with a warning, because an unnumberable source cannot be
    addressed by card id.
    """
    import re
    pool: Dict[int, Image.Image] = {}
    for path in sorted(source_dir.glob('*')):
        if path.suffix.lower() not in ('.png', '.tiff', '.tif', '.jpg', '.jpeg'):
            continue
        m = re.search(r'(\d+)', path.stem)
        if not m:
            print(f'  ! skipping {path.name}: no number in filename')
            continue
        n = int(m.group(1))
        if not 1 <= n <= ID_LIMIT:
            print(f'  ! skipping {path.name}: number {n} outside 1..{ID_LIMIT}')
            continue
        if n in pool:
            print(f'  ! duplicate source number {n}: keeping {pool[n].path.name}, ignoring {path.name}')
            continue
        pool[n] = Source(number=n, path=path, img=Image.open(path))
    return pool


def write_set(slots: List[Slot],
              faces: List[Image.Image],
              out_dir: pathlib.Path,
              seed: int,
              edge_range: int) -> None:
    """Write RGCard-<i>.tiff (what the engine loads) + set.json (the manifest)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for slot, face in zip(slots, faces):
        # engine expects RGCard-{index}.tiff for index 0..24
        face.save(out_dir / f'RGCard-{slot.board_index}.tiff')

    manifest = {
        'seed': seed,
        'deck_size': DECK_SIZE,
        'edge_range': edge_range,
        'id_limit': ID_LIMIT,
        'note': 'board_index is the engine key; card_id is art-only. '
                'Edge values are a separate signed system from card ids.',
        'slots': [asdict(s) for s in slots],
    }
    (out_dir / 'set.json').write_text(json.dumps(manifest, indent=2))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    p.add_argument('--sources', required=True, type=pathlib.Path,
                   help='Directory of numbered source images (src-07.png -> number 7).')
    p.add_argument('--out', default=pathlib.Path('RGTCards'), type=pathlib.Path,
                   help='Output asset dir (the engine reads <wdir>/RGTCards).')
    p.add_argument('--size', default=300, type=int, help='Card face size in px.')
    p.add_argument('--seed', default=None, type=int,
                   help='Determinism. Omit for a fresh set each run.')
    p.add_argument('--edge-range', default=12, type=int, dest='edge_range')
    p.add_argument('--allow-duplicates', action='store_true', dest='allow_duplicates')
    p.add_argument('--allow-siblings', action='store_true', dest='allow_siblings',
                   help='Let -n and +n (two halves of one diamond) appear together.')
    p.add_argument('--dry-run', action='store_true',
                   help='Print the sampled set without writing any images.')
    args = p.parse_args()

    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(1, 2**31)
    rng = random.Random(seed)

    sources = load_source_pool(args.sources)
    if len(sources) < DECK_SIZE and not args.allow_siblings:
        print(f'  ! only {len(sources)} numbered sources; a sibling-free set of '
              f'{DECK_SIZE} needs {DECK_SIZE}. Re-running with siblings allowed.')
        args.allow_siblings = True
    if not sources:
        raise SystemExit(f'no numbered sources found in {args.sources}')

    slots = build_set(sources, rng, args.edge_range, args.size,
                      no_siblings=not args.allow_siblings,
                      allow_duplicates=args.allow_duplicates)

    for s in slots:
        e = s.edges
        print(f'  idx {s.board_index:2d}  id {s.card_id:+3d}  {s.stem}  '
              f'src={pathlib.Path(s.source).name:<14} '
              f'edges T{e["top"]:+d} R{e["right"]:+d} B{e["bottom"]:+d} L{e["left"]:+d}')

    if args.dry_run:
        print(f'\ndry run, nothing written (seed={seed})')
        return

    faces = [compose_face(sources, s.card_id, args.size) for s in slots]
    write_set(slots, faces, args.out, seed, args.edge_range)
    print(f'\nwrote {len(faces)} faces + set.json to {args.out}  (seed={seed})')
    print('run:  python pasyans-3.py --wdir .')


if __name__ == '__main__':
    main()
