#!/usr/bin/env python3
"""utils/tests/test_deck.py -- unittest suite for the pasyans deck engine.

Replaces the old utils/test_wedge_compositor.py regex-extraction hack with
real imports (possible since the P2 split + __main__ guard).

Run from anywhere:
    python3 -m unittest discover -s tests -v
"""
import pathlib
import random
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / 'utils'):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import compose_set as cs                      # noqa: E402
from pasyans.deck import (                    # noqa: E402
    DECK_SIZE, GRID_SIZE, UNSET_CARD, UNSET_ROT,
    CardDataGenerator, WedgeCompositor,
)
from pasyans.cli import _build_arg_parser     # noqa: E402

SOURCES = ROOT / 'LoremIpsoCards'


class TestConstants(unittest.TestCase):
    def test_deck_geometry(self):
        self.assertEqual(DECK_SIZE, GRID_SIZE * GRID_SIZE)

    def test_unset_sentinels(self):
        self.assertEqual((UNSET_CARD, UNSET_ROT), (-1, -63))


class TestArgParser(unittest.TestCase):
    def test_sources_alias(self):
        # --sources is an alias of --source-images.  The implied
        # --random-cards is applied at runtime in main(), not by the parser.
        args = _build_arg_parser().parse_args(['--sources', 'x'])
        self.assertEqual(str(args.source_images), 'x')
        self.assertFalse(args.random_cards)

    def test_defaults(self):
        args = _build_arg_parser().parse_args([])
        self.assertEqual(args.size, 300)
        self.assertIsNone(args.seed)


class TestCardDataGenerator(unittest.TestCase):
    def test_edge_matrix_shape_dtype(self):
        gen = CardDataGenerator(DECK_SIZE, edge_range=12, allow_duplicates=False)
        m = gen.generate()
        self.assertEqual(m.shape, (DECK_SIZE, 4))
        self.assertEqual(str(m.dtype), 'int8')


@unittest.skipUnless(SOURCES.is_dir(), f"no art sources at {SOURCES}")
class TestWedgeCompositor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.comp = WedgeCompositor(SOURCES, card_size=120, seed=7)

    def test_plan_shape(self):
        self.assertEqual(len(self.comp.plan), DECK_SIZE)
        self.assertTrue(all(len(card) == 4 for card in self.comp.plan))

    def test_wedge_discipline(self):
        ids = [i for card in self.comp.plan for i in card]
        self.assertEqual(len(ids), 4 * DECK_SIZE)
        self.assertEqual(len(set(ids)), len(ids), 'every wedge used exactly once')
        for card in self.comp.plan:
            srcs = [abs(i) for i in card]
            self.assertEqual(len(set(srcs)), 4,
                             f'card carries two halves of one source: {card}')

    def test_validate_plan_clean(self):
        self.assertEqual(cs.validate_plan(self.comp.plan), [])

    def test_generate_face(self):
        img = self.comp.generate_face(0)
        self.assertEqual(img.size, (120, 120))
        self.assertEqual(self.comp.card_ids(0), list(self.comp.plan[0]))

    def test_determinism_same_seed(self):
        other = WedgeCompositor(SOURCES, card_size=120, seed=7)
        self.assertEqual(other.plan, self.comp.plan)


class TestMatchRotationTables(unittest.TestCase):
    """Match-display rotation tables inside find_matches must match the
    engine's display convention: show_card_rot uses Image.rotate(r * 90), a
    COUNTER-clockwise turn, and slots are numbered counter-clockwise to match
    the scan sheet's native wedge order [T,L,B,R] (T=0, L=1, B=2, R=3), so
    edge column i sits at slot i at rot 0 and rotation r moves slot s to
    (s + r) % 4.  Each table gives the CCW rotation bringing a matching edge
    to face its partner: left card -> right (3), right card -> left (1),
    top card -> down (2), bottom card -> up (0).

    The tables are function-local, so they are read from the parsed source.
    """

    FACING = {'mp00': 3, 'mp10': 1, 'mp01': 2, 'mp11': 0}

    def test_tables_face_partner(self):
        import ast
        src = (ROOT / 'pasyans-4.py').read_text()
        tables = {}
        for node in ast.walk(ast.parse(src)):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and getattr(node.targets[0], 'id', '') in self.FACING):
                tables[node.targets[0].id] = ast.literal_eval(node.value)
        self.assertEqual(set(tables), set(self.FACING), 'all four tables found')
        for name, table in tables.items():
            facing = self.FACING[name]
            self.assertEqual(len(table), 4, f'{name} must have 4 entries')
            for edge, r in enumerate(table):
                self.assertEqual(
                    (edge + r) % 4, facing,
                    f'{name}[{edge}] = {r} puts the edge at slot '
                    f'{(edge + r) % 4}, expected {facing}')


HAND_SESSION = ROOT / 'sessions' / 'Deal-01-Hand.json'


class TestScanColumnOrder(unittest.TestCase):
    """The scan sheet lists each card's wedges right-to-left [T,L,B,R], which
    IS the engine's native convention: CCW slot numbering (T=0, L=1, B=2, R=3)
    with ``column i = slot i`` at rot 0, matching the CCW turn of
    ``Image.rotate(r * 90)``.  The hand-arranged ground-truth session pins
    this end to end: with the raw scan order, every matchable adjacency sits
    at exactly the rotation the mp-tables require (locked negatives display
    as abs(rot)).
    """

    def test_hand_session_matches_tables(self):
        import ast
        import json
        src = (ROOT / 'pasyans-4.py').read_text()
        tables = {}
        for node in ast.walk(ast.parse(src)):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and getattr(node.targets[0], 'id', '')
                    in TestMatchRotationTables.FACING):
                tables[node.targets[0].id] = ast.literal_eval(node.value)
        deal = json.loads(HAND_SESSION.read_text())
        # Raw scan order — the engine's native CCW convention, no reordering.
        cl = deal['card_list']

        def display_rot(r):
            return 0 if r == -4 else (abs(r) if r < 0 else r)

        g = {(c['x'], c['y']): c for c in deal['board']}
        checked = 0
        for (x, y), c in sorted(g.items()):
            for dx, dy, t in ((1, 0, 'H'), (0, 1, 'V')):
                n = g.get((x + dx, y + dy))
                if n is None:
                    continue
                pairs = [(i, j) for i in range(4) for j in range(4)
                         if cl[c['card']][i] + cl[n['card']][j] == 0]
                if not pairs:
                    continue
                i, j = pairs[0]
                if t == 'H':
                    need = (tables['mp00'][i], tables['mp10'][j])
                else:
                    need = (tables['mp01'][i], tables['mp11'][j])
                have = (display_rot(c['rot']), display_rot(n['rot']))
                self.assertEqual(
                    have, need,
                    f'{t} pair ({x},{y})/({x + dx},{y + dy}) columns '
                    f'({i},{j}): stored {have}, tables require {need} '
                    f'- scan-column remap or mp-tables drifted')
                checked += 1
        self.assertEqual(checked, 6, 'expected 6 matchable adjacencies')


if __name__ == '__main__':
    unittest.main(verbosity=2)
