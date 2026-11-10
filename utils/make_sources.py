#!/usr/bin/env python3
"""
make_sources.py -- reconstruct source images from a set of cards + CardList.csv.

This is the *reverse* half of the set round-trip:

    RGCard-<i>.tiff  +  CardList.csv   --[make_sources.py]-->   source images

``make_cards.py`` composes cards from sources; this util inverts that.  Each
source n contributes two halves to the deck -- its LEFT half (id ``-n``) and
RIGHT half (id ``+n``) -- each placed into exactly one wedge of one card.  We
find those two wedges, inverse-warp each back into source space, mask it to the
source half-triangle, and stitch the two halves into the source diamond.

It reuses ``compose_set``'s geometry (half_triangle / WEDGE / solve_rotation /
place_mask) so the inverse matches the forward transform exactly.

LOSSY BY NATURE: the forward path resamples (BICUBIC warp + LANCZOS downscale to
CARD_PX).  Reconstructing upscales back, so the result is a close approximation
of the original diamond, not a pixel-exact copy.  The four square corners of the
original source are never used by any wedge and cannot be recovered -- only the
diamond (the two triangles) is reconstructed.

Usage
-----
    python make_sources.py --cards /tmp/cards --cardlist ../RGTCards/CardList.csv \
                           --out /tmp/sources_recon
"""
from __future__ import annotations

import argparse
import itertools
import pathlib
import sys

import numpy as np
from PIL import Image, ImageDraw

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import compose_set as cs  # noqa: E402
from make_cards import load_cardlist  # noqa: E402

SLOTS = cs.SLOTS           # ('top', 'left', 'bottom', 'right')
S = cs.S                   # supersampled square size


def forward_coeffs(cid: int, slot: str):
    """Recompute the exact PIL AFFINE coeffs compose_card used for (cid, slot).

    These map card-space -> source-space (PIL samples the source with them).
    """
    base = cs.half_triangle(cid)
    wedge = list(cs.WEDGE[slot])
    for perm in itertools.permutations(range(3)):
        pts = [wedge[i] for i in perm]
        coeffs, _ang, ok = cs.solve_rotation(base, pts)
        if ok:
            return coeffs
    raise RuntimeError(f'slot {slot} id {cid}: no pure 90-deg rotation maps half->wedge')


def invert_coeffs(coeffs):
    """Invert an affine (a,b,c,d,e,f) so it maps the opposite direction."""
    a, b, c, d, e, f = coeffs
    fwd = np.eye(3)
    fwd[0] = [a, b, c]
    fwd[1] = [d, e, f]
    inv = np.linalg.inv(fwd)
    return tuple(inv[:2, :].ravel())


def half_mask(cid: int) -> Image.Image:
    """Source-space mask for the half-triangle this cid occupies."""
    m = Image.new('L', (S, S), 0)
    ImageDraw.Draw(m).polygon([tuple(map(int, p)) for p in cs.half_triangle(cid)], fill=255)
    return m


def locate_halves(plan: list[list[int]]) -> dict[int, dict[int, tuple[int, str]]]:
    """source n -> {sign: (card_index, slot)}.  sign is -1 (left) or +1 (right)."""
    loc: dict[int, dict[int, tuple[int, str]]] = {}
    for card_i, ids in enumerate(plan):
        for k, cid in enumerate(ids):
            n, sign = abs(cid), (1 if cid > 0 else -1)
            loc.setdefault(n, {})[sign] = (card_i, SLOTS[k])
    return loc


def reconstruct_source(n: int, loc: dict[int, tuple[int, str]],
                       cards: dict[int, Image.Image]) -> Image.Image:
    """Stitch a source n's left/right halves back into its diamond (S x S)."""
    canvas = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    for sign, (card_i, slot) in loc.items():
        cid = sign * n
        card = cards[card_i].resize((S, S), Image.LANCZOS)
        coeffs = invert_coeffs(forward_coeffs(cid, slot))
        warped = card.transform((S, S), Image.AFFINE, coeffs, resample=Image.BICUBIC)
        canvas.paste(warped, (0, 0), half_mask(cid))
    return canvas


def load_cards(cards_dir: pathlib.Path) -> dict[int, Image.Image]:
    out: dict[int, Image.Image] = {}
    for p in sorted(cards_dir.glob('RGCard-*.tiff')):
        idx = int(p.stem.split('-')[1])
        out[idx] = Image.open(p).convert('RGBA')
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='reconstruct source images from cards + CardList.csv')
    ap.add_argument('--cards', required=True, type=pathlib.Path,
                    help='directory of RGCard-<i>.tiff')
    ap.add_argument('--cardlist', default=_HERE.parent / 'RGTCards' / 'CardList.csv',
                    type=pathlib.Path, help='signed edge/wedge matrix (25x4)')
    ap.add_argument('--out', default=pathlib.Path('/tmp/sources_recon'),
                    type=pathlib.Path, help='output dir for reconstructed sources')
    args = ap.parse_args(argv)

    plan = load_cardlist(args.cardlist)
    cards = load_cards(args.cards)
    print(f'[make_sources] {len(plan)} plan rows, {len(cards)} card images')

    loc = locate_halves(plan)
    missing = [n for n, halves in loc.items() if len(halves) < 2]
    if missing:
        print(f'  [warn] {len(missing)} sources have only one half present in the deck: '
              f'{sorted(missing)[:10]}{"..." if len(missing) > 10 else ""}', file=sys.stderr)

    args.out.mkdir(parents=True, exist_ok=True)
    for n in sorted(loc):
        src = reconstruct_source(n, loc[n], cards)
        src.save(args.out / f'{n}-reconstructed.png')
    print(f'[make_sources] reconstructed {len(loc)} source diamonds to {args.out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
