#!/usr/bin/env python3
"""wedge_demo.py -- render one 4-wedge Pasyans card and its 4 views.

Geometry PROPOSAL (unconfirmed, spec 3.3). Card ids are given in slot order
[top, right, bottom, left]; sign picks the source half, abs() picks the source.

Method: each source's half-diamond is a right-isosceles triangle congruent to a
card wedge, so a rotation+translation (no mirror, no scale) maps it into place.
The transform is solved from a 3-point vertex correspondence, and the
orientation-preserving (det>0) pairing is chosen -- so mirrors never creep in.
"""
import itertools, pathlib
import numpy as np
from PIL import Image, ImageDraw

N = 512
SS = 4                      # supersample factor
SRC_DIR = pathlib.Path('/tmp/pasyans_src2')

# ---- test sources with a visible "upright" marker --------------------------
def make_sources(nums=range(1, 5)):
    SRC_DIR.mkdir(exist_ok=True)
    for n in nums:
        p = SRC_DIR / f'src-{n:02d}.png'
        if p.exists():
            continue
        im = Image.new('RGBA', (N, N), (30, 30, 40, 255))
        d = ImageDraw.Draw(im)
        col_l = (200 + 12 * n % 55, 180 - 20 * n % 60, 60, 255)
        col_r = (60, 90 + 30 * n % 60, 200 + 15 * n % 55, 255)
        d.rectangle([0, 0, N // 2 - 1, N], fill=col_l)      # LEFT half tinted
        d.rectangle([N // 2, 0, N, N], fill=col_r)         # RIGHT half tinted
        # upright marker: an arrow pointing UP, one per half
        for cx, col in ((N // 4, col_r), (3 * N // 4, col_l)):
            d.polygon([(cx, N // 4), (cx - N // 12, N // 2), (cx + N // 12, N // 2)], fill=(20, 20, 25, 255))
            d.rectangle([cx - N // 40, N // 2, cx + N // 40, 3 * N // 4], fill=(20, 20, 25, 255))
        d.text((N // 4 - 10, 20), f'L{n}', fill=(0, 0, 0, 255))
        d.text((3 * N // 4 - 10, 20), f'R{n}', fill=(0, 0, 0, 255))
        im.save(p)

# ---- geometry --------------------------------------------------------------
S = N * SS
C = (S / 2, S / 2)
# inscribed diamond, cut at x = S/2
DIAM = {'T': (S / 2, 0), 'B': (S / 2, S), 'L': (0, S / 2), 'R': (S, S / 2)}
# card wedges: diagonal partition, apex at centre
WEDGE = {
    'top':    ((0, 0), (S, 0), C),
    'right':  ((S, 0), (S, S), C),
    'bottom': ((S, S), (0, S), C),
    'left':   ((0, 0), (0, S), C),
}
SLOT_ORDER = ('top', 'right', 'bottom', 'left')


def half_triangle(card_id):
    """Source-space triangle for a card id: apex + two cut ends."""
    if card_id < 0:    # LEFT half  tri(T, L, B)
        return [DIAM['L'], DIAM['T'], DIAM['B']]
    return [DIAM['R'], DIAM['T'], DIAM['B']]   # RIGHT half tri(T, R, B)


def solve_affine(p_src, p_dst):
    """Forward map source->card from 3 point pairs.

    Returns (inverse_coeffs, det, angle_deg, is_pure_rotation).

    NB: det == +1 alone does NOT mean rotation -- any bijection between two
    congruent triangles is an affine map with |det| == 1, and the orientation
    preserving ones include SHEARS.  Must also verify the linear part has the
    rotation form [[c,-s],[s,c]]; otherwise the wedge lands tilted and the
    engine (which only rotates in 90 deg steps) can never read it upright.
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
    return tuple(inv[:2, :].ravel()), det, ang, pure


def resolve_source(n):
    """Find the source file for source number n.

    Handles both naming schemes seen in this project:
      src-07.png      (synthetic test sources)
      07-SomeName.png (LoremIpsoCards art)
    """
    for pat in (f'src-{n:02d}*.png', f'{n:02d}*.png', f'src-{n}*.png', f'{n}-*.png'):
        hits = sorted(SRC_DIR.glob(pat))
        if hits:
            if len(hits) > 1:
                others = ', '.join(h.name for h in hits[1:])
                print(f'  ! source {n}: {len(hits)} matches, using {hits[0].name} (also: {others})')
            return hits[0]
    raise SystemExit(f'no source file found for number {n} in {SRC_DIR}')


def compose(card_ids):
    """card_ids: sequence of 4, mapped to slots top,right,bottom,left."""
    canvas = Image.new('RGBA', (S, S), (0, 0, 0, 255))
    cover = Image.new('L', (S, S), 0)
    cd = ImageDraw.Draw(cover)
    report = []
    for k, (slot, cid) in enumerate(zip(SLOT_ORDER, card_ids)):
        path = resolve_source(abs(cid))
        src = Image.open(path).convert('RGBA').resize((S, S), Image.LANCZOS)
        base = half_triangle(cid)
        wedge = list(WEDGE[slot])
        best = None
        for perm in itertools.permutations(range(3)):
            pts = [wedge[i] for i in perm]
            coeffs, det, ang, pure = solve_affine(base, pts)
            # only accept a TRUE rotation, and only one the engine can express
            if pure and min(abs(ang - round(ang / 90) * 90), 1e-6) < 1e-6:
                best = (coeffs, det, ang, pts)
                break
        if best is None:
            raise SystemExit(
                f'slot {slot!r} / id {cid:+d}: no pure 90-deg-step rotation maps this '
                f'half onto the wedge -- that pairing needs a mirror, see spec 9.2')
        coeffs, det, ang, pts = best
        placed = src.transform((S, S), Image.AFFINE, coeffs, resample=Image.BICUBIC)
        mask = Image.new('L', (S, S), 0)
        ImageDraw.Draw(mask).polygon(pts, fill=255)
        canvas.paste(placed, (0, 0), mask)
        cd.polygon([(int(x), int(y)) for x, y in pts], fill=1 << k)
        report.append((slot, cid, f'{ang:.1f}', det, f'{sum(1 for _ in ())}'))
    # coverage check: every pixel labelled exactly once
    arr = np.asarray(cover)
    total = int((arr > 0).sum())
    full = S * S
    card = canvas.resize((N, N), Image.LANCZOS)
    return card, cover.resize((N, N), Image.NEAREST), report, total, full


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--sources', default='/tmp/pasyans_src2', type=pathlib.Path,
                    help='dir of numbered source images (01-Name.png -> number 1)')
    ap.add_argument('--ids', default='-1,-2,3,4',
                    help='four card ids, mapped to slots top,right,bottom,left')
    ap.add_argument('--synthetic', action='store_true',
                    help='generate the marked test sources first (orientation arrows)')
    a = ap.parse_args()

    SRC_DIR = a.sources   # module-level scope: this rebinds the global directly
    ids = [int(x) for x in a.ids.split(',')]
    if len(ids) != 4 or any(i == 0 for i in ids):
        raise SystemExit('need exactly 4 non-zero ids')
    if a.synthetic:
        make_sources()
    card, cover, report, total, full = compose(ids)
    card.save('/tmp/wedge_card.png')
    cover.save('/tmp/wedge_coverage.png')

    print('card ids', ids, '-> slots', SLOT_ORDER)
    for slot, cid, ang, det, _ in report:
        print(f'  {slot:<7} id {cid:+3d}  src {abs(cid)}  {"LEFT " if cid<0 else "RIGHT"} half '
              f'rotated {ang:>6} deg  (det {det:.3f})')
    print(f'coverage: {total}/{full} pixels labelled -> {"EXACT TILING" if total == full else "GAPS/OVERLAP"}')

    # contact sheet: the card and the 3 rotations that reveal the other views
    views = [card] + [card.rotate(-90 * i, expand=False) for i in (1, 2, 3)]
    sheet = Image.new('RGBA', (N * 4 + 30, N + 40), (18, 18, 24, 255))
    for i, v in enumerate(views):
        sheet.paste(v, (i * (N + 10) + 5, 30))
        ImageDraw.Draw(sheet).text((i * (N + 10) + 8, 8),
                                   f'card at {90*i} deg' if i else 'as authored (0 deg)',
                                   fill=(230, 230, 230, 255))
    sheet.save('/tmp/wedge_views.png')
    print('wrote /tmp/wedge_card.png /tmp/wedge_coverage.png /tmp/wedge_views.png')
