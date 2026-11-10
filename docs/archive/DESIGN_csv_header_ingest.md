# Design: Header-driven CSV ingestion for card definitions

Status: **design / open questions** — no code changed yet.
Scope: `pasyans-4.py` `_find_card_defs_csv()` + `_load_card_defs()`, and the
structure they hand to the "Show Details" UI.

## 1. What the example actually contains

`LoremIpsoCards/RGTCards_Lorem.csv` — 16 columns, 50 data rows, WITH a header row:

| col | header            | populated | engine currently maps it to |
|-----|-------------------|-----------|------------------------------|
| 0   | Image Name        | 50/50     | `name` (col 6)               |
| 1   | ID                | 50/50     | (dropped)                    |
| 2   | Description       | 50/50     | **(dropped)**                |
| 3   | Nature            | 50/50     | **(dropped)**                |
| 4   | Image Meaning     | 50/50     | `title` (col 0)              |
| 5   | `1`               | 1/50      | interp pos 1 (col 1)         |
| 6   | `2`               | 1/50      | interp pos 2 (col 2)         |
| 7   | `3`               | 1/50      | interp pos 3 (col 3)         |
| 8   | `4`               | 1/50      | interp pos 4 (col 4)         |
| 9   | Duration          | 0/50      | short title (col 5)          |
| 10  | BG Colour         | 0/50      | (dropped)                    |
| 11  | FG Colour         | 0/50      | (dropped)                    |
| 12  | Related Images    | 0/50      | (dropped)                    |
| 13  | Archetype         | 0/50      | (dropped)                    |
| 14  | Category          | 50/50     | **(dropped)**                |
| 15  | *(empty header)*  | 0/50      | trailing comma               |

## 2. Root cause of "name imports, everything else misses"

`_load_card_defs()` (pasyans-4.py ~361) remaps an art-sheet **by fixed position**:

```python
rows = [[_r[4], _r[5], _r[6], _r[7], _r[8], _r[9], _r[0]] for _r in rows]
#        title  i1    i2    i3    i4    dur   name
```

Those indices were correct for the *original* art-sheet schema, where cols 5–8
held the four interpretations and col 9 held duration. In the Lorem sheet:

- cols 5–8 are literally named `1..4` and are **empty for 49 of 50 cards** → interps blank
- col 9 `Duration` is **empty for all 50** → short title blank
- the rich columns (Description, Nature, Category) are **not in the 7-col layout at all** → silently dropped
- `Image Meaning` is being used as `title`, which may or may not be intended

So it's not a parsing failure — it's a **positional schema mismatch**. The fix is
to map **by header name**, not by position.

## 3. Second finding: deck-size truncation

`DECK_SIZE = 25` (5×5, `pasyans/deck.py:506`). The Lorem sheet has **50** data
rows. `_load_card_defs` does `rows[:DECK_SIZE]`, so cards 26–50 are dropped
today, independent of the header issue. Need a decision (see Q3).

## 4. Proposed header-detection heuristic

Replace the hardcoded `'Image Name' in rows[0] and 'ID' in rows[0]` check with a
**content-driven** one, per the brief:
**Corrected for Pasyans (images = cards × 2).** Each card is built from 2
source images, so `expected_data_lines == image_count / 2`. The original
"populated > images" never fires (cards < 2·cards always). The signal is the
*extra* line a header adds on top of the expected data lines:

```python
images    = count_images(directory)            # *.png *.jpg *.jpeg *.tiff ...
populated = count_populated_csv_lines(path)    # includes the header if present
has_header = populated > (images / 2)          # one extra line == header row
```

Verified against the example: `LoremIpsoCards` has 52 images → expects 26 data
lines; the sheet has 1 header + 50 rows = 51 populated lines, and `51 > 26` →
header detected. (Note the deck here is 50 cards, not 25 — see §3.)

**OR'd with the precise name check** (your Q2 answer):

```python
has_header = ('Image Name' in rows[0] and 'ID' in rows[0]) or (populated > images / 2)
```
  it would falsely strip the first data row.
- The current name-based check (`Image Name`+`ID`) is more *precise* when those
  headers exist. Suggestion: **OR the two** — header if (name-based check passes)
  OR (populated > images). Cheap, and keeps the precise path.

## 5. Proposed ingestion model

Two layers, so we don't break the existing numpy consumer:

**Layer A — name→index map from the header.** Build `{header: col_index}`. Resolve
the engine's known fields by *name* with a small alias table:

| engine field  | accepted header names (case-insensitive)                  |
|---------------|-----------------------------------------------------------|
| name          | `Image Name`, `Name`, `Card Name`                         |
| title         | `Image Meaning`, `Title`, `Meaning`                       |
| interp 1..4   | `1`,`2`,`3`,`4`, `Interp 1`.., `Meaning 1`..              |
| short title   | `Duration`, `Short`, `Short Title`                        |
| id            | `ID`                                                      |

**Layer B — capture everything else.** Every header not consumed by Layer A is
kept as an extra field (Description, Nature, Category, …) rather than dropped.

## 6. Resolved decisions

- **Q1 Structure → Hybrid (C).** numpy `(N, 7)` stays the hot path for the
  Details UI (unchanged indexing). A parallel `list[dict[str, str]]` of extras
  rides alongside, keyed by header name.
- **Q2 Header detection → OR'd.** `(name-based check) or (populated > images/2)`.
- **Q3 Deck size → Pasyans layout.** Each card is composed from **2** source
  images, so `image_count == card_count * 2`. The heuristic uses `images/2` as
  the expected data-line count (see §4). NOTE: the Lorem deck is 50 cards, which
  still exceeds `DECK_SIZE=25`; truncation is a separate open item (§3).
- **Q4 Title vs Meaning → `Image Meaning` is the title.** `Description` is the
  image-generation prompt, so it is an **extra**, not the title.
- **Q5 Interpretations → confirmed.** Headers `1..4` are the four interpretation
  slots, keyed to image position; blank is acceptable.
- **Q6 Where → build a reusable util** (`utils/`), used by both the engine and
  `make_lorem_defs.py` so the bridge script can retire.

## 7. Extra fields: where they map, what to add

Engine's 7 columns (fixed, numpy): `Title, Interpretation 1..4, Short Title, Name`.
Everything else in a source sheet becomes an **extra** (captured in the hybrid
dict, not the numpy array). Mapping for the Lorem sheet:

| source header    | maps to            | notes                                  |
|------------------|--------------------|----------------------------------------|
| Image Name       | numpy `Name`       |                                        |
| ID               | numpy row index    | drives `card_defs[id-1]`               |
| Image Meaning    | numpy `Title`      | Q4: title                              |
| 1 / 2 / 3 / 4    | numpy `Interp 1..4`| Q5: by image position                  |
| Duration         | numpy `Short Title`|                                        |
| Description      | **extra**          | Q4: image-gen prompt                   |
| Nature           | **extra**          | e.g. "clarity, vision" — tags          |
| BG Colour        | **extra**          | compositor hint (unused today)         |
| FG Colour        | **extra**          | compositor hint (unused today)         |
| Related Images   | **extra**          | cross-links                            |
| Archetype        | **extra**          | grouping                               |
| Category         | **extra**          | grouping (50/50 populated)             |
| *(empty header)* | dropped            | trailing comma                         |

**What to maybe add** (engine fields not present in the Lorem sheet but the UI
can use): a stable `ID` is already there; consider `Tags` (from Nature) and
`Category` surfaced in the Details panel. `BG/FG Colour` are compositor inputs —
wired only if/when the compositor reads them.

## 8. Done this pass / next

- **Done:** added a code-derived header row to `RGTCards/RGTCards.csv`
  (`Title, Interpretation 1..4, Short Title, Name`), backing up the original to
  `RGTCards/RGTCards.csv.bak`.
- **Next:** build `utils/` loader util (header-aware, hybrid output), then wire
  the engine + retire `make_lorem_defs.py`.
