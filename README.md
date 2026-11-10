# PyPasyans

A Python engine for **Pasyans** — a cartomancy (fortune-telling) deck — with a
Tkinter GUI, an in-memory card compositor, and a toolchain that builds complete
card sets from numbered source images.

> **Lineage.** PyPasyans grew out of an early engine written for the *Russian
> Gypsy Fortune Telling* tradition (`pasyans-3.py`, now archived). That
> tradition is the inspiration and starting point; this project has since
> evolved into a general Pasyans deck engine with its own card-composition
> pipeline.

## Features

- **Deck engine** (`pasyans/deck.py`) — card definitions, edge matrices, and
  layout logic, extracted into a clean, tested package.
- **GUI** (`pasyans-4.py`) — Tkinter app that renders deals from pre-built
  card faces *or* composes the whole deck **in memory** from a directory of
  numbered source images (`--sources`), no pre-built assets required.
- **Wizard** (`pasyans-wizard.py`) — Tkinter launcher for the engine.
- **Composition toolchain** (`utils/`) — scripts that generate card definitions
  (`make_set.py`), compose card faces (`compose_set.py`), and export sets
  (`export_set.py`).
- **Tested** — `pytest` suite covering deck logic and session round-trips
  (12 tests, all passing on Python 3.12).

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run the GUI from a directory of numbered source images:
PYTHONPATH=utils python3 pasyans-4.py --sources LoremIpsoCards --seed 7

# Run the tests:
python3 -m pytest utils/tests -q
```

## Project layout

```
pasyans/            core package: deck model + CLI argument parsing
pasyans-4.py        main GUI engine
pasyans-wizard.py   Tkinter launcher
utils/              card-set generation / composition / export tools
utils/tests/        pytest suite (fixtures in sessions/)
docs/               design docs, spec, and archived early versions
LoremIpsoCards/     sample card set (placeholder art, CC BY-NC — see below)
BabaPetcaCardImages/  source imagery for the sample set
```

## Requirements

Python ≥ 3.12 · numpy ≥ 2.0 · Pillow ≥ 10.0 · Tkinter (stdlib).

## Licensing

- **Code** is licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE).
- **Card art and card data** (images under `LoremIpsoCards/`,
  `BabaPetcaCardImages/`, `docs/archive/LoremIpso2/`, and the card-definition
  CSVs) are licensed under [CC BY-NC 4.0](LICENSE-ASSETS), with attribution to
  **Baba Petca**, whose Pasyans deck and card definitions inspired and seed
  this project.

Both licenses permit sharing and adapting for **non-commercial** purposes with
attribution. Commercial use is not granted.

## Status

Active development. The `docs/ENTERPRISE_PROJECT.md` document records a full
repo review with verified/run status for every component.
