# Enterprise Project Document — PyPasyans

**Project:** PyPasyans — Pasyans divination deck — GUI engine and card-composition toolchain (early engine: pasyans-3.py, inspired by Russian Gypsy Fortune Telling)
**Document date:** 2026-10-27 · **Reviewed by:** automated full-repo review (all Python files read or structure-extracted; commands run on this machine)
**Status legend:** ✅ verified by execution · 🔎 read in review · ⚠️ known issue

---

## 1. Executive Summary

This repository builds and plays a **Pasyans** divination deck: a 5×5 board of 25
cards laid face-down; the player reveals each card in a random orientation, and
adjacent cards whose touching edge values sum to zero form a *match*, highlighted
with a coloured border. Match interpretations come from a per-card CSV or are
generated dynamically. 🔎

Two generations of the engine coexist:

| Engine | Role | Deck source |
|---|---|---|
| `pasyans-3.py` (1547 ln) | previous generation | pre-baked assets: `RGTCards/RGCard-<i>.tiff` + `RGTList.csv` edge matrix |
| `pasyans-4.py` (1927 ln) | **current** | same, **plus** can build the whole deck **in memory** from a directory of numbered source images (`--sources`), no pre-baked assets needed |

A `utils/` toolchain composes cards from source art (four triangular "wedges"
per card), exports the asset tree the engine loads, and self-tests the geometry.

**Verified working:** `pasyans-4.py --self-test` passes **9/9** with
`PYTHONPATH=utils` ✅. Without it, the engine crashes on import of `compose_set` ⚠️ (§7.1).

---

## 2. Repository Layout

```
PyPasyans/
├── pasyans-4.py            current engine (tkinter GUI + in-memory wedge deck)   🔎
├── pasyans-3.py            previous engine (pre-baked assets only)                🔎
├── ScratchPad.py           59-line scratch script                                 🔎
├── launnchCards.sh         launcher — BROKEN: runs missing RussianTarot.py        ⚠️
├── utils/                  card-composition toolchain                             🔎
│   ├── compose_set.py          core library (409 ln): sample_set, wedge geometry
│   ├── export_set.py           emits engine asset tree (RGCard-<i>.tiff, RGTList.csv, RGTCards.csv)
│   ├── make_set.py             DRAFT v0.1 set generator (pre-compose_set)
│   ├── make_lorem_defs.py      builds Lorem Ipsum card-definition CSV
│   ├── wedge_demo.py           renders one 4-wedge card + 4 views (geometry proposal, spec §3.3)
│   └── test_wedge_compositor.py standalone test — broken cwd assumptions          ⚠️
├── docs/
│   ├── pasyans-card-spec.md    card geometry/format spec (394 ln)
│   ├── RUNNING.md              run guide (CLI, IDLE, Python Launcher, BBEdit); facts checked 2026-10-26
│   ├── pasyans-card-spec.md / Notes-Table 1.csv   (Notes CSV is empty — headers only) ⚠️
│   └── Baba Petca-Baba Petca's Pasyans Cards.csv  card-definition sheet; columns present, data mostly unfilled
├── RGTCards/               27 files: RGCard-<i>.tiff faces (engine needs 25 + RGTList.csv)
├── LoremIpsoCards/         numbered art sources ("01-SunsEye.png" …) — engine convention: filename starts with source number
├── LoremIpso2/             24 files: RGTCards.csv + deepimg-*.png art
├── BabaPetcaCardImages/    56 deepimg-*.png art sources
├── .spyproject/            Spyder IDE project config
└── __pycache__/            note: contains "pasyans-4.cpython-312.py" (odd name — should be .pyc) ⚠️
```

**No git repository, no venv, no requirements.txt / pyproject.toml / setup.py.** ⚠️

---

## 3. Domain Model & Conventions

- **Signed card id:** `-n` = LEFT half of source `n`, `+n` = RIGHT half. Sources are 1..50. 🔎
- **Wedge faces:** each card square is split by its diagonals into four wedges,
  slots ordered **top, right, bottom, left**. Every wedge is used exactly once per
  deck (100 wedge slots / 100 source-halves), and no face carries both halves of
  one source. ✅ (enforced by `--self-test`)
- **Edge matrix:** `RGTList.csv`, 25×4 signed `np.byte` (int8) — edges per card in
  slot order. Matches are adjacent edges summing to **0**; a match must be
  *completable by rotation* (board self-test reported 20 of 80 adjacencies). ✅
- **Interpretations:** `RGTCards.csv`, 25×7 quoted strings; the engine indexes
  column 6. ✅ (shape checked by self-test)
- **Board:** `GRID_SIZE = 5`, 25 cards, `card_size` from `-s/--size` (default 300 px). 🔎

---

## 4. Engine: pasyans-4.py (current)

Structure (line numbers from this review): 🔎

| Component | Line | Purpose |
|---|---|---|
| `CardDataGenerator` | 138 | edge values / card list generation |
| `ImageCompositor` | 217 | composes card faces from halves (v3-era path) |
| `WedgeCompositor` | 523 | **new in v4** — 4-wedge in-memory composition; delegates sampling to `utils/compose_set.py` |
| `ScrollableFrame` | 623 | ttk board container |
| `_build_arg_parser` | 659 | CLI (independently testable by design) |
| `card_image_path` / `_load_or_generate_card_list` / `_load_card_defs` | 810/879/912 | asset resolution: pre-baked vs in-memory |
| `find_matches` / `match_cards` / `click_card` / `show_card_rot` / `reset_cards` … | 1428–1808 | game loop, reveal, match highlighting |

**CLI (verified flags):** `-s/--size` (default 300), `--seed`, `--dry-run`,
`--self-test`, `--dump-cards <dir>` (writes faces + `sheet.png`),
`--sources` (alias of `--source-images`; either implies `--random-cards`, so
`RGTList.csv` is never read). ✅

**Note:** the GUI is built at **module level** — there is no
`if __name__ == "__main__":` guard, so importing the file constructs the app.
This is why the standalone test extracts source text instead of importing. 🔎

---

## 5. Toolchain: utils/

- **`compose_set.py`** — the core library: `sample_set(rng, pool, n)` picks a legal
  25-card set (signed ids, wedge discipline), geometry helpers, plan validation. 🔎
- **`export_set.py`** — composes a set **and emits the exact asset tree**
  `pasyans-3.py` loads into a working dir: `RGCard-<i>.tiff` (i = 0..24),
  `RGTList.csv` (25×4 signed int8, REQUIRED), `RGTCards.csv` (25×7 quoted). 🔎
- **`make_set.py`** — DRAFT v0.1, predates `compose_set`; kept for reference. 🔎
- **`wedge_demo.py`** — geometry proof-of-concept (rotation+translation only, no
  mirror/scale); marked *unconfirmed* against spec §3.3. 🔎
- **`make_lorem_defs.py`** — generates the Lorem Ipsum definitions CSV. 🔎
- **`test_wedge_compositor.py`** — standalone test that regex-extracts
  `WedgeCompositor` source from `pasyans-4.py` (a workaround for the missing
  `__main__` guard). ⚠️ Broken: it reads `pasyans-4.py` from cwd (needs repo root)
  but expects sources at `../LoremIpsoCards` (needs `utils/` as cwd) — it cannot
  pass from either location as written.

---

## 6. Environment & Dependencies

Verified on this machine (2026-10-27): ✅

| Item | Value |
|---|---|
| Python | 3.12.3 — `/usr/local/bin/python3` (framework build; matches IDLE per RUNNING.md) |
| numpy | 2.5.4 |
| Pillow | 10.3.0 |
| tkinter | available (system Tcl/Tk) |
| IDE | Spyder (`.spyproject/` present) |

Runtime deps are therefore: **numpy, Pillow, tkinter** — undeclared anywhere ⚠️.

---

## 7. Known Issues & Risks

1. **`compose_set` import failure (highest priority).** `pasyans-4.py` (line 547)
   and `pasyans-3.py` (lines 547, 975) do `import compose_set as _cs` with **no
   `sys.path` handling anywhere in the repo**. From the repo root,
   `--self-test`/`--dry-run` crash with `ModuleNotFoundError: compose_set`. ✅
   Workaround: run with `PYTHONPATH=utils`. Proper fix: add a `sys.path.insert`
   for `utils/` next to the script, or promote `compose_set` to a real package.
2. **`test_wedge_compositor.py` cannot pass from any cwd** (contradictory path
   assumptions, §5). ✅
3. **`launnchCards.sh` is dead** — launches `python3 RussianTarot.py -s=200 -b=15 &`;
   no `RussianTarot.py` exists (stale name for the engine) and `-b` is not a
   current flag. ✅
4. **No version control.** No `.git`; no history/rollback safety. ✅
5. **No dependency manifest or venv.** Environment reproducibility relies on
   RUNNING.md prose. ✅
6. **Module-level GUI construction** in both engines — untestable by import,
   hence the regex-extraction test hack. 🔎
7. **Asset/data gaps:** `docs/Notes-Table 1.csv` is empty (headers only); the
   Baba Petca definitions CSV has its column scheme but meanings/colours/
   archetypes mostly unfilled; `RGTCards/` holds 27 files — reconcile against the
   25 faces + `RGTList.csv` the engine expects. 🔎
8. **`__pycache__/pasyans-4.cpython-312.py`** — a `.py`-named cache artifact;
   likely a stray copy, safe to delete. 🔎
9. **Security note (accepted for this walled-garden setting):** the engine reads
   arbitrary image/CSV paths from CLI args and `subprocess` is imported; in a
   shared/untrusted context, path inputs and CSV contents would need validation.
   Fine here; flagged for the record.

---

## 8. Verified Runbook

```bash
cd PyPasyans

# Self-test the wedge engine (currently REQUIRES PYTHONPATH):
PYTHONPATH=utils python3 pasyans-4.py --sources LoremIpsoCards --seed 7 --self-test
# → "self-test: 9/9 passed"  ✅

# Build a deck and print it without opening a window:
PYTHONPATH=utils python3 pasyans-4.py --sources LoremIpsoCards --seed 7 --dry-run

# Dump composed faces + sheet.png to a directory:
PYTHONPATH=utils python3 pasyans-4.py --sources LoremIpsoCards --seed 7 --dump-cards /tmp/cards

# Play (tkinter window):
PYTHONPATH=utils python3 pasyans-4.py --sources LoremIpsoCards --seed 7 -s 300
```

---

## 9. Suggested Roadmap (not started — for planning only)

1. Fix the `compose_set` import (sys.path shim or package-ify `utils/`); re-run
   self-test without `PYTHONPATH`.
2. Repair or retire `test_wedge_compositor.py`; make it cwd-independent
   (`pathlib.Path(__file__)`-relative paths).
3. Add `if __name__ == "__main__": main()` guards to both engines.
4. `git init` + `.gitignore` (`__pycache__/`, `.DS_Store`, dumps) + commit.
5. Add `requirements.txt` (numpy, Pillow) or a `pyproject.toml`.
6. Fix or delete `launnchCards.sh`.
7. Fill the Baba Petca definitions CSV / remove the empty Notes CSV.
8. Longer-term: fold v3-only paths into v4, or document v3 as frozen legacy.

---

## 10. Review Method & Coverage

- Read in full: `docs/RUNNING.md`, `ScratchPad.py`, `launnchCards.sh`, all CSV
  headers, directory inventories.
- Structure-extracted + key sections read: `pasyans-4.py`, `pasyans-3.py`,
  all six `utils/*.py` modules, `docs/pasyans-card-spec.md` headings.
- Executed: dependency check, `--self-test` (fail without `PYTHONPATH`, 9/9 pass
  with it), standalone test from both cwds, `--dry-run`.
- Not executed: the interactive GUI window (headless review), `--dump-cards`,
  `export_set.py` end-to-end.
