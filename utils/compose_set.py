#!/usr/bin/env python3
"""
compose_set.py -- render a random set of 25 four-wedge Pasyans cards.

Each card is built from FOUR different source images.  A card id is signed:
    -n  -> the LEFT  half of source n
    +n  -> the RIGHT half of source n
and the four ids of a card fill slots in counter-clockwise order
top, left, bottom, right (the engine's native convention, matching the scan
sheet's [T,L,B,R] wedge order).

Geometry: the card square is partitioned by its two diagonals into four
right-isosceles wedges.  A half of an inscribed diamond canvas is a congruent
triangle, so each wedge is reached by a pure rotation + translation of its
source half -- no scaling, no mirroring.  The transform is solved from a
3-point vertex correspondence; only orientation-preserving 90-deg-stepped
rotations are accepted (det == +1 alone is not enough, it also admits shears).

    python utils/compose_set.py --out /tmp/set --seed 7        # sources default to <project>/LoremIpsoCards
"""
from __future__ import annotations

import argparse
import itertools
import json
import pathlib
import random
import re
import sys
from dataclasses import dataclass, asdict
from typing import Dict, List

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ----------------------------------------------------------------- tunables
CARD_PX = 440          # rendered card size in px
SS = 2                 # supersample factor; CARD_PX*SS == 1024 == native art
DECK_SIZE = 25
ID_LIMIT = 50
# CCW slot order: plan id k lands at counter-clockwise slot k (T=0, L=1, B=2,
# R=3) — the engine's native convention, matching the scan sheet's [T,L,B,R].
SLOTS = ('top', 'left', 'bottom', 'right')
LABEL_BAR = 0.11       # fraction of card height used by the number bar

S = CARD_PX * SS
CEN = (S / 2, S / 2)
DIAM = {'T': (S / 2, 0), 'B': (S / 2, S), 'L': (0, S / 2), 'R': (S, S / 2)}
WEDGE = {
    'top':    ((0, 0), (S, 0), CEN),
    'right':  ((S, 0), (S, S), CEN),
    'bottom': ((S, S), (0, S), CEN),
    'left':   ((0, 0), (0, S), CEN),
}


# ------------------------------------------------------------ source pool
@dataclass
class Source:
    number: int
    path: pathlib.Path


class DeckError(ValueError):
    """Unbuildable deck: not enough sources, impossible constraints, or a plan
    that breaks the wedge disciplines.

    Library code raises this, NOT SystemExit -- a library must not terminate the
    host process.  The CLI in main() turns it into an exit; pasyans-4.py catches
    it and reports it in the engine's own way.
    """


def load_pool(src_dir: pathlib.Path) -> Dict[int, Source]:
    """number -> source, keyed by the source NUMBER.

    Accepted filename conventions (first run of digits wins):
        '07-SomeName.png'   '07_SomeName.png'   'src-07.png'   '7.png'
    Anything with no digits, or a number outside 1..ID_LIMIT, is skipped with a
    warning on stderr.  Duplicate numbers keep the first (filename sort order)
    and warn -- e.g. '15-A', '15-B', '15-C' are variants, not three sources.
    """
    pool: Dict[int, Source] = {}
    if not src_dir.is_dir():
        raise DeckError(f'source directory does not exist: {src_dir}')
    for p in sorted(src_dir.glob('*.png')):
        m = re.search(r'(\d+)', p.stem)
        if not m:
            print(f'  ! {p.name}: no number in filename, skipped', file=sys.stderr)
            continue
        n = int(m.group(1))
        if not 1 <= n <= ID_LIMIT:
            print(f'  ! {p.name}: number {n} outside 1..{ID_LIMIT}, skipped',
                  file=sys.stderr)
            continue
        if n in pool:
            print(f'  ! source {n}: keeping {pool[n].path.name}, ignoring {p.name}',
                  file=sys.stderr)
            continue
        pool[n] = Source(n, p)
    return pool


def source_img(src: Source) -> Image.Image:
    """Load at working resolution and stamp the source number at centre
    bottom (cached by path).

    The stamp is applied BEFORE the image is split into wedges.  The number
    straddles the vertical T-B cut line (see ``half_triangle``), so the left
    half of the digits rides with the left-half wedge (-n) and the right half
    with the right-half wedge (+n); when the two halves meet on the board and
    rotate into a match the number reassembles, making the match easy to
    read.
    """
    if not hasattr(source_img, '_cache'):
        source_img._cache = {}
    key = str(src.path)
    if key not in source_img._cache:
        img = Image.open(src.path).convert('RGBA').resize((S, S), Image.LANCZOS)
        source_img._cache[key] = stamp_source_number(img, src.number)
    return source_img._cache[key]


def stamp_source_number(img: Image.Image, n: int) -> Image.Image:
    """Draw the source number top and bottom, centred on the wedge cut line.

    Called by ``source_img`` after loading and before the wedge split.  The
    two copies sit on the vertical T-B cut line (``x = S/2``) — one towards
    the top corner of the canvas, one towards the bottom — so the split
    divides each copy into a left half (rides with wedge ``-n``) and a right
    half (rides with wedge ``+n``): the labelling number is top/bottom split
    exactly when the wedges are split, and the two halves reassemble at the
    shared cut.  Both copies are inset so the digits stay completely inside
    the diamond canvas frame.
    """
    d = ImageDraw.Draw(img)
    font = _font(max(10, S // 16))
    pad, fill, stroke = max(1, S // 200), (240, 230, 180, 255), (12, 10, 22, 255)
    for fy in (0.28, 0.72):
        x, y = S / 2.0, S * fy
        try:
            d.text((x, y), str(n), font=font, anchor='mm',
                   fill=fill, stroke_width=pad, stroke_fill=stroke)
        except (ValueError, AttributeError):   # bitmap fallback font
            d.text((x, y), str(n), font=font, fill=fill)
    return img


# --------------------------------------------------------------- geometry
def half_triangle(card_id: int):
    """Source-space triangle for an id: [apex, cut_end_1, cut_end_2]."""
    if card_id < 0:
        return [DIAM['L'], DIAM['T'], DIAM['B']]   # LEFT  half tri(T, L, B)
    return [DIAM['R'], DIAM['T'], DIAM['B']]       # RIGHT half tri(T, R, B)


def solve_rotation(p_src, p_dst):
    """Affine rotation+translation mapping p_src -> p_dst.

    Returns (inverse_coeffs, angle_deg, is_pure_90_step_rotation).
    NB det == +1 is NOT sufficient: any bijection between congruent triangles is
    affine with |det| == 1 and the orientation-preserving ones include shears.
    """
    A = np.zeros((6, 6)); b = np.zeros(6)
    for i, (p, q) in enumerate(zip(p_src, p_dst)):
        A[2 * i], b[2 * i] = [p[0], p[1], 1, 0, 0, 0], q[0]
        A[2 * i + 1], b[2 * i + 1] = [0, 0, 0, p[0], p[1], 1], q[1]
    m = np.linalg.solve(A, b).reshape(2, 3)
    lin = m[:2, :2]
    fwd = np.eye(3); fwd[:2, :] = m
    inv = np.linalg.inv(fwd)
    det = float(np.linalg.det(lin))
    ang = float(np.degrees(np.arctan2(lin[1, 0], lin[0, 0]))) % 360
    pure = (abs(lin[0, 0] - lin[1, 1]) < 1e-6 and abs(lin[0, 1] + lin[1, 0]) < 1e-6
            and abs(det - 1.0) < 1e-6)
    ok90 = pure and abs(ang - round(ang / 90) * 90) < 1e-6
    return tuple(inv[:2, :].ravel()), ang, ok90


def place_mask(slot: str) -> Image.Image:
    m = Image.new('L', (S, S), 0)
    ImageDraw.Draw(m).polygon([tuple(map(int, p)) for p in WEDGE[slot]], fill=255)
    return m


# --------------------------------------------------------------- sampling
def wedge_universe(available: List[int]) -> List[int]:
    """Every wedge that exists: -n and +n for each source number.

    50 sources -> 100 wedges, and 25 cards x 4 wedges = 100 slots, so a full set
    can use every wedge EXACTLY ONCE.  Each image then appears twice per set:
    its left half on one card, its right half on another.
    """
    return [w for n in sorted(available) for w in (-n, n)]


def sample_set(rng: random.Random, available: List[int], n: int = DECK_SIZE,
               allow_siblings: bool = False, tries: int = 5000) -> List[List[int]]:
    """Deal wedges so each wedge appears once and only once.

    Constraint (unless allow_siblings): a card never holds both -n and +n, so
    the four wedges of a card always come from four different images.

    Method: shuffle the wedge list, cut into cards, then repair sibling
    collisions by random swaps.  A plain shuffle does not guarantee the
    constraint, so the repair loop is what actually enforces it.
    """
    need = n * 4
    universe = wedge_universe(available)
    if need > len(universe):
        raise DeckError(f'{need} wedge slots but only {len(universe)} wedges exist '
                        f'({len(available)} images x 2 sides)')

    if not allow_siblings:
        # Capacity bound for the sibling-free discipline.  Each card needs 4
        # DISTINCT images, and each image can contribute at most 2 wedges (its
        # left and right half) because every wedge is used once.  So 4*n slots
        # need at least 2*n distinct images.  State it up front: without this
        # the repair loop just spins and blames the seed, which is misleading --
        # the arithmetic is impossible, not unlucky.
        min_images = 2 * n
        if len(available) < min_images:
            raise DeckError(
                f'a sibling-free set of {n} cards needs >= {min_images} distinct images, '
                f'have {len(available)}. Either add images, render fewer cards, or pass '
                f'allow_siblings=True.')

    wedges = rng.sample(universe, need) if need != len(universe) else list(universe)
    rng.shuffle(wedges)
    cards = [wedges[i * 4:(i + 1) * 4] for i in range(n)]

    if allow_siblings:
        return cards

    def clash(c):
        nums = [abs(w) for w in c]
        return len(set(nums)) != len(nums)

    for _ in range(tries):
        bad = [i for i, c in enumerate(cards) if clash(c)]
        if not bad:
            return cards
        ci = rng.choice(bad)
        cj = rng.randrange(n)
        if cj == ci:
            continue
        a, b = rng.randrange(4), rng.randrange(4)
        cards[ci][a], cards[cj][b] = cards[cj][b], cards[ci][a]

    raise DeckError('could not resolve sibling collisions -- try another --seed')


def validate_plan(plan: List[List[int]], strict: bool = True) -> List[str]:
    """Boundary guard: check a plan satisfies the wedge disciplines.

    Lives here, separate from sample_set(), because a plan can also arrive from
    a hand-edited set.json or a future --plan file.  sample_set() guarantees the
    disciplines by construction; this proves them for plans it did not build.

    Returns a list of human-readable problems (empty == plan is good).
    """
    problems: List[str] = []

    seen: Dict[int, int] = {}
    for i, ids in enumerate(plan):
        if len(ids) != 4:
            problems.append(f'card {i}: has {len(ids)} wedges, expected 4')
        if 0 in ids:
            problems.append(f'card {i}: contains id 0, which is not a valid wedge')
        for cid in ids:
            seen[cid] = seen.get(cid, 0) + 1

    for cid, n in sorted(seen.items()):
        if n > 1:
            problems.append(f'wedge {cid:+d} used {n} times, expected exactly 1')

    for i, ids in enumerate(plan):
        nums = [abs(w) for w in ids]
        if len(set(nums)) != len(nums):
            dup = sorted({n for n in nums if nums.count(n) > 1})
            problems.append(f'card {i}: both halves of image(s) {dup} on one card')

    if strict:
        # NB collect the offending ID, not the card index -- collecting `i` here
        # makes the message point at a card instead of at the bad id.
        bad = sorted({abs(n) for ids in plan for n in ids
                      if not 1 <= abs(n) <= ID_LIMIT})
        if bad:
            problems.append(f'ids outside 1..{ID_LIMIT}: {bad}')

    return problems


# ------------------------------------------------------------- composition
def compose_card(ids: List[int], pool: Dict[int, Source]) -> Image.Image:
    canvas = Image.new('RGBA', (S, S), (0, 0, 0, 255))
    for slot, cid in zip(SLOTS, ids):
        src = source_img(pool[abs(cid)])
        base = half_triangle(cid)
        wedge = list(WEDGE[slot])
        for perm in itertools.permutations(range(3)):
            pts = [wedge[i] for i in perm]
            coeffs, ang, ok = solve_rotation(base, pts)
            if ok:
                break
        else:
            raise RuntimeError(f'slot {slot} id {cid}: no pure 90-deg rotation maps half->wedge')
        placed = src.transform((S, S), Image.AFFINE, coeffs, resample=Image.BICUBIC)
        canvas.paste(placed, (0, 0), place_mask(slot))
    return canvas.resize((CARD_PX, CARD_PX), Image.LANCZOS)


# ------------------------------------------------------------- numbering
def _font(px: int) -> ImageFont.FreeTypeFont:
    for path in ('/System/Library/Fonts/Menlo.ttc',
                 '/System/Library/Fonts/Supplemental/Arial.ttf',
                 '/System/Library/Fonts/Helvetica.ttc'):
        p = pathlib.Path(path)
        if p.exists():
            try:
                return ImageFont.truetype(str(p), px)
            except OSError:
                continue
    return ImageFont.load_default()


def label_card(card: Image.Image, ids: List[int]) -> Image.Image:
    """Draw the four source numbers along the bottom, in slot order T L B R.

    '-12' means left half of image 12, '+12' means right half.  A small slot
    letter sits under each number so the card reads without a legend.
    """
    out = card.convert('RGB')
    d = ImageDraw.Draw(out)
    w, h = out.size
    bar_h = int(h * LABEL_BAR)
    top = h - bar_h
    d.rectangle([0, top, w, h], fill=(12, 10, 22))
    d.line([0, top, w, top], fill=(150, 130, 80), width=2)

    num_font = _font(int(bar_h * 0.52))
    tag_font = _font(int(bar_h * 0.26))
    cell = w / 4.0
    for i, (slot, cid) in enumerate(zip(SLOTS, ids)):
        # real minus sign (U+2212) so -n reads as "left half of n", not a hyphen
        text = f'\u2212{abs(cid)}' if cid < 0 else f'+{cid}'
        cx = cell * (i + 0.5)
        bb = d.textbbox((0, 0), text, font=num_font)
        d.text((cx - (bb[2] - bb[0]) / 2, top + bar_h * 0.10), text,
               fill=(240, 232, 210), font=num_font)
        tag = slot[0].upper()
        bb2 = d.textbbox((0, 0), tag, font=tag_font)
        d.text((cx - (bb2[2] - bb2[0]) / 2, top + bar_h * 0.66), tag,
               fill=(170, 150, 95), font=tag_font)
    return out


# ------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description='render a random 25-card Pasyans set')
    ap.add_argument('--sources', default=pathlib.Path(__file__).resolve().parent.parent / 'LoremIpsoCards', type=pathlib.Path)
    ap.add_argument('--out', default=pathlib.Path('/tmp/set'), type=pathlib.Path)
    ap.add_argument('--seed', default=None, type=int)
    ap.add_argument('--cards', default=DECK_SIZE, type=int)
    ap.add_argument('--gap', default=10, type=int, help='contact-sheet gap in px')
    args = ap.parse_args()

    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(1, 2**31)
    rng = random.Random(seed)

    # DeckError is a library error; the CLI is the only place it becomes an exit.
    try:
        pool = load_pool(args.sources)
    except DeckError as exc:
        raise SystemExit(f'[ERROR] {exc}')
    if len(pool) < 4:
        raise SystemExit(f'need at least 4 numbered sources, found {len(pool)} in {args.sources}')
    print(f'pool: {len(pool)} numbered sources from {args.sources}')

    try:
        plan = sample_set(rng, sorted(pool), args.cards)
    except DeckError as exc:
        raise SystemExit(f'[ERROR] {exc}')

    # Prove the disciplines on the plan we are about to render.  sample_set()
    # guarantees them by construction, so this should be silent -- it is here for
    # plans that arrive some other way (hand-edited set.json, future --plan file).
    problems = validate_plan(plan)
    if problems:
        for p in problems:
            print(f'  ! {p}')
        raise SystemExit(f'plan failed validation with {len(problems)} problem(s); nothing rendered')
    print('plan validated: every wedge used once, no card holds both halves of one image')

    args.out.mkdir(parents=True, exist_ok=True)
    cards = []
    for i, ids in enumerate(plan):
        card = label_card(compose_card(ids, pool), ids)
        card.save(args.out / f'set-{i:02d}.png')
        cards.append(card)
        print(f'  card {i:02d}  T{ids[0]:+d} R{ids[1]:+d} B{ids[2]:+d} L{ids[3]:+d}')

    # contact sheet
    cols = 5
    rows = (len(cards) + cols - 1) // cols
    g = args.gap
    sheet = Image.new('RGB', (cols * CARD_PX + (cols + 1) * g,
                              rows * CARD_PX + (rows + 1) * g), (24, 20, 34))
    for i, c in enumerate(cards):
        r, cc = divmod(i, cols)
        sheet.paste(c, (g + cc * (CARD_PX + g), g + r * (CARD_PX + g)))
    sheet.save(args.out / 'sheet.png')

    # usage accounting -- this is where "each wedge once and only once" is proven
    wedge_use, img_use = {}, {}
    for ids in plan:
        for cid in ids:
            wedge_use[cid] = wedge_use.get(cid, 0) + 1
            img_use[abs(cid)] = img_use.get(abs(cid), 0) + 1
    repeats = {k: v for k, v in wedge_use.items() if v > 1}
    siblings = [i for i, ids in enumerate(plan) if len({abs(w) for w in ids}) != 4]
    print(f'\nverification: {len(wedge_use)} distinct wedges used, '
          f'{len(repeats)} repeated, {len(siblings)} cards holding both halves of one image')
    manifest = {
        'seed': seed,
        'sources_dir': str(args.sources),
        'card_px': CARD_PX,
        'supersample': SS,
        'slot_order': list(SLOTS),
        'id_convention': 'negative = LEFT half of source, positive = RIGHT half',
        'wedge_discipline': 'each wedge (signed id) used exactly once per set',
        'cards': [{'index': i, 'ids': ids} for i, ids in enumerate(plan)],
        'image_usage': {str(k): v for k, v in sorted(img_use.items())},
        'wedge_usage': {str(k): v for k, v in sorted(wedge_use.items())},
        'images_used': len(img_use),
        'max_image_usage': max(img_use.values()),
        'slots_total': sum(len(ids) for ids in plan),
    }
    (args.out / 'set.json').write_text(json.dumps(manifest, indent=2))

    print(f'\nwrote {len(cards)} cards + sheet.png + set.json to {args.out}  (seed={seed})')
    print(f'images used: {manifest["images_used"]} of {len(pool)} '
          f'(max {manifest["max_image_usage"]} appearances each) '
          f'across {manifest["slots_total"]} wedge slots')


if __name__ == '__main__':
    main()
