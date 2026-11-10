"""Harness v3: run the REAL find_matches() on the REAL edge matrix and classify
every highlighted adjacency:

  facing_ok     - final rotations put zero-sum edges face-to-face  (GOOD)
  conflict      - facing fails AND the pair's final rotations could
                  not have come from this adjacency (overwritten by another
                  neighbour's match)
  table_bad     - facing fails although rotations ARE this adjacency's combo
                  (would mean the tables/pairing are wrong)

Display convention (empirically verified): rotate(+r*90) is CCW, so the
original column facing absolute slot s is (s + r) % 4.  Slots T=0 R=1 B=2 L=3.
"""
import csv
import importlib.util
import pathlib
import types
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('p4', ROOT / 'pasyans-4.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

m._update_status = lambda: None
m._status_msg = types.SimpleNamespace(set=lambda *a: None)
m._seed_var = types.SimpleNamespace(set=lambda *a: None)

G = m.GRID_SIZE
rows = list(csv.reader(open(ROOT / 'RGTCards' / 'RGTList.csv')))
m.card_list = np.array([[int(v) for v in r[:4]] for r in rows], dtype=np.int8)
print('card_list:', m.card_list.shape, '| value range:',
      int(m.card_list.min()), '..', int(m.card_list.max()))

m.spread = np.arange(m.DECK_SIZE, dtype=np.int16).reshape(G, G)
m.rots = np.zeros((G, G), dtype=np.byte)
m.mat_rots = np.full((G, G), -5, dtype=np.byte)
m.mat_cnt = np.zeros((G, G), dtype=np.byte)
m.currs = np.zeros((G, G), dtype=np.int16)
m.matches = []
m.find_matches()

# tables are function-local inside find_matches; extract from parsed source
import ast
mp_t = {}
for node in ast.walk(ast.parse(open(ROOT / 'pasyans-4.py').read())):
    if (isinstance(node, ast.Assign) and len(node.targets) == 1
            and getattr(node.targets[0], 'id', '') in ('mp00', 'mp10', 'mp01', 'mp11')):
        mp_t[node.targets[0].id] = ast.literal_eval(node.value)
mp = {'h': (mp_t['mp00'], mp_t['mp10']), 'v': (mp_t['mp01'], mp_t['mp11'])}

facing_ok = conflict = table_bad = 0
for x in range(G):
    for y in range(G):
        for dx, dy, s_a, s_b, key in ((1, 0, 1, 3, 'h'), (0, 1, 2, 0, 'v')):
            x1, y1 = x + dx, y + dy
            if x1 >= G or y1 >= G:
                continue
            if m.mat_cnt[x, y] <= 0 or m.mat_cnt[x1, y1] <= 0:
                continue
            a = int(m.spread[x, y]); b = int(m.spread[x1, y1])
            ra = int(m.mat_rots[x, y]); rb = int(m.mat_rots[x1, y1])
            fa = int(m.card_list[a, (s_a + ra) % 4])
            fb = int(m.card_list[b, (s_b + rb) % 4])
            if fa + fb == 0:
                facing_ok += 1
                continue
            t0, t1 = mp[key]
            from_here = any(int(m.card_list[a, i]) + int(m.card_list[b, j]) == 0
                            and ra == t0[i] and rb == t1[j]
                            for i in range(4) for j in range(4))
            if from_here:
                table_bad += 1
                print(f'TABLE-BAD ({x},{y})a={a} r={ra} vs ({x1},{y1})b={b} r={rb}')
            else:
                conflict += 1
print(f'facing_ok: {facing_ok}   conflict: {conflict}   table_bad: {table_bad}')
