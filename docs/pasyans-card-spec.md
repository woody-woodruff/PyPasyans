# Pasyans Card Composer — Pipeline Spec (draft v0.1)

Status: **draft for review**. Geometry below is my reconstruction of your description and
needs your confirmation — see §9 "Open questions". Everything else is implementation-agnostic.

---

## 1. What the thing is (one paragraph for the dev)

A **Pasyans card** is a square output image built from **four triangular wedges**, each wedge
taken from a **different source image**. Every source image is authored on a **diamond canvas**:
a square artboard rotated 45° (vertices up/down/left/right). A diamond canvas splits down its
vertical axis into a **left half** and a **right half**, each of which is a right-isosceles
triangle. The composer takes one such half from four different sources and lays the four
triangles onto one square canvas, edge-to-edge, so that the four flat cut edges land on the
four outer edges of the card and the three... four triangle apexes meet at the card centre.

Because each wedge carries a differently-oriented source, the finished card is a **multi-view
illusion**: it reads as one picture upright, and as the other three as you rotate it by 90°,
180°, 270°. (Prior art / terminology to show the dev: "multi-view optical illusions",
`dangeng.github.io/visual_anagrams` — Visual Anagrams, which formalises exactly this family of
rotation/flip-based multi-view images.)

## 2. Vocabulary (use these names in the code)

| Term | Meaning |
|---|---|
| **card** | one final square output image |
| **source** / **tile source** | one input image, authored on a diamond canvas |
| **diamond canvas** | the artboard: square rotated 45°, diagonals vertical+horizontal |
| **half** | left or right half of a diamond canvas — a right-isosceles triangle |
| **side** | `L` or `R`, which half of the source is used |
| **slot** | target position on the card: `top`, `right`, `bottom`, `left` |
| **wedge** | a triangle placed in a slot |
| **seam** | a boundary between two wedges (on the card's diagonals) |
| **view** | one of the 4 orientations the card resolves to |

Prefer `side`/`slot` over "left/right" everywhere in logs and filenames — the collision is a
guaranteed source of bugs.

## 3. Geometry (the part to get right before writing code)

Conventions: **image coordinates**, origin top-left, `x` right, `y` down, units = pixels.
Card side `N`. Centre `C = (N/2, N/2)`.

### 3.1 The source canvas
A diamond canvas has the **same bounding box as the card** (`N × N`) and is the *inscribed*
diamond:

```
T = (N/2, 0)      top vertex
R = (N,   N/2)    right vertex
B = (N/2, N)      bottom vertex
L = (0,   N/2)    left vertex
```

Diamond area = `N²/2`. Cut line = vertical `x = N/2`.

```
  left half = tri(T, L, B)      right half = tri(T, R, B)
  area each = N²/4              (4 × N²/4 = N² = exactly the card)
```

```
      T                      T
     /\                      /\
    /  \                    /  \
   / L  \   +              /    R \
   \    /                  \      /
    \  /                    \    /
     \/                      \/
      B                      B
```

The four halves are **congruent** to the four wedges of the card, so they tile it with no
scaling and no gaps. That is the whole trick — worth a comment in the code.

### 3.2 The card's four slots
The card is partitioned by its two diagonals (the corner-to-corner lines, *not* the
midlines):

```
slot top    = tri((0,0), (N,0), C)
slot right  = tri((N,0), (N,N), C)
slot bottom = tri((N,N), (0,N), C)
slot left   = tri((0,0), (0,N), C)
```

```
   *-----------*
    \ \  top  / /
     \ \     / /
      \ \   / /
  left \ \ / / right
        \ X /
        / / \ \
       / /   \ \
      / /     \ \
     / / bottom\ \
   *-----------*
```

### 3.3 Placement transform (source half → slot)
Each slot is reached by a **rotation by a multiple of 90°** of the source half, plus a fixed
translation — no scaling, no mirroring needed if you standardise on one handedness. Derived
example for the `left` slot from a `L`-side half: rotate the half 180° about `(N/4, N/2)`;
the cut edge maps from `x = N/2` to `x = 0` and the apex from `L` to `C`. ✔

The full per-slot rotation table is **TBD — this is open question #1 in §9**, because the
correct rotation is also what decides which view reads upright. Do not let the dev "eyeball"
this table; make it a data table with an assertion that the four wedges cover the card
exactly (see §6, coverage check).

## 4. Per-source content orientation (this is the spec's real payload)

For the illusion to work, the source is **not** authored upright. Each source is pre-rotated so
that after the slot transform it lands upright in its view. If slot rotations are
`top: 0°, right: 90°, bottom: 180°, left: 270°` then the source authoring rotation is the
inverse. Two options, pick one and document it:

- **A — Artist pre-rotates.** Source files are delivered already rotated. Composer only crops
  and places. Simplest code; most fragile (a wrong file silently breaks the illusion).
- **B — Composer rotates.** Sources delivered upright; composer applies
  `source.rotation = -slot.rotation` before cropping. Recommended: the source of truth is the
  slot table, and the art directory stays human-readable.

Also decide **content safe area**: the corners of the source's bounding box never appear in the
output (only the diamond's interior does, and only one half of that). Tell the artist which
fraction of their canvas is guaranteed visible — for a diamond-inscribed half it is `1/4` of the
bounding box, so any detail placed outside the triangle is silently discarded.

## 5. Pipeline (7 steps)

1. **Load & validate** each source: assert exact `N × N`, assert no alpha or assert it is
   premultiplied — pick one policy.
2. **Rotate** to the slot's required orientation (if option B).
3. **Crop** the half: `L` → `x < N/2`, `R` → `x > N/2`, intersected with the diamond mask.
   Both crops are `N/2 × N` rectangles plus a triangle mask.
4. **Transform** into slot (rotate + translate).
5. **Composite** the four wedges onto a blank `N × N` canvas in slot order.
6. **Seam pass** (see §7).
7. **Write** output + a sidecar manifest (§8).

Use `Pillow` (`Image.rotate` with `resample=Image.BICUBIC`, `Image.ImageDraw.polygon` for the
mask, `Image.composite`/`paste` with mask for placement). If the card is generated from a larger
working resolution, downsample **once** at the end (`LANCZOS`) — never per wedge.

## 6. Correctness checks (assert these in CI, they are cheap)

- **Coverage check.** Paint each wedge onto an `N × N` mask image with a distinct label value
  (1, 2, 4, 8). Assert `mask == 15` for every pixel. This single assertion catches ~every
  geometry bug: gaps (`<15`), overlaps (`>15` from additive drawing), off-by-one edge leaks.
- **Area check.** Each wedge label count must equal `round(N²/4)`.
- **Rotation smoke test.** For a test card, render 4 sources that are solid-coloured with a
  small upright marker (an arrow or an "F"). Rotate the rendered card by 90° and assert that
  exactly the intended wedge's marker is upright. This is the only test that protects the
  illusion itself.
- **Idempotence.** Same inputs + same manifest ⇒ byte-identical output (pin resampler, no
  timestamps in output metadata).

## 7. Seam policy (must be decided, not defaulted)

Rotating and masking a triangle gives you hard diagonal edges; two independent sources meeting
along a diagonal will look *seamed* unless you handle it. Options, in increasing effort:

1. **Hard cut.** Do nothing. Honest, graphic, cheap. Fine for the vintage-pasyans look.
2. **Oversize & feather.** Extend each wedge ~2–4 px past its diagonal and cross-fade with a
   feathered mask. Kills the aliasing staircase without blending content.
3. **Content-aware blend.** `cv2.seamlessClone` / Poisson blending per wedge. Best looking,
   changes colours, and is the hardest to keep deterministic.

Also: mask polygons must be rasterised at the **final** output size (or supersampled), and
antialiasing on the diagonal edges is a visible-quality issue at card size — a 63×88 px card
will show staircases immediately. Recommended default: render at 4× and downsample once.

## 8. Data model / manifest

One manifest per card, checked into the repo next to the art. Example:

```json
{
  "card_id": "pasyans_07",
  "output": { "path": "out/pasyans_07.png", "size": 1024, "render_scale": 4 },
  "slots": {
    "top":    { "source": "src/owl.png",       "side": "L" },
    "right":  { "source": "src/key.png",       "side": "R" },
    "bottom": { "source": "src/lantern.png",   "side": "L" },
    "left":   { "source": "src/moth.png",      "side": "R" }
  },
  "seam": { "mode": "feather", "feather_px": 3 }
}
```

Notes for the dev:
- `side` + `slot` is the whole job description; keep the composer a pure function of the manifest.
- Validate that a source is not reused in the same card unless explicitly allowed (duplicated
  content is usually an authoring mistake, but it *is* a legitimate technique — so make it a
  warning, not an error).
- CLI shape: `python -m pasyans build manifests/pasyans_07.json` plus
  `python -m pasyans preview manifests/` for a contact sheet, and `python -m pasyans rotate out/x.png --view 2`
  for previewing view 2 without rotating the file.

## 9. Open questions — I need your answers before this is buildable

1. **Slot rotation table.** Which slot does the viewer see upright at 0°, and does the sequence
   run clockwise or counter-clockwise as the card is turned? This fixes both the placement
   rotation and the source pre-rotation.
2. **Reflected or rotated?** Is the card a pure **rotation** illusion (4 views, `C4` symmetry) or
   does it also read in a mirror (`D4`, 8 views)? Mirroring halves changes which source halves
   are usable and whether `side` even matters. Your "left **or** right halves" phrasing hints
   that mirrors are in play.
3. **Where do top/bottom wedges come from?** Geometrically a diamond gives a `L`/`R` pair and a
   `T`/`B` pair. You said four *left-or-right* halves, which is consistent if the `top`/`bottom`
   sources are authored on canvases pre-rotated 90° (so their "left half" *is* a top wedge after
   placement). Confirm that's the authoring intent, or whether top/bottom sources are simply
   cropped top/bottom and the wording was loose.
4. **The wasted half.** Half of every source is discarded (or reused elsewhere). Are the discarded
   halves reused for a sibling card — i.e. is the unit of authoring a *diamond* yielding 2
   wedges, meaning 2 diamonds feed 1 card, or 4 diamonds feed 1 card? This changes the asset
   count and the whole art-direction workflow.
5. **Output targets.** Final pixel size(s), DPI, print vs screen, colour profile (sRGB vs CMYK for
   print), bleed requirement. Print bleed interacts with §7 hard cuts: if the card is trimmed to
   a square the diagonals reaching the edge must be bleed-safe.
6. **Inputs.** Are sources flat rasters, or layered (PSD/Figma exports)? If layered, the composer
   needs an export step and a named-layer contract (`art`, `frame`, `caption`) before it can crop.

## 10. Suggested build order

1. Geometry module + coverage/area tests, no images involved (pure polygon math).
2. Non-destructive "four solid colours + arrow" test card → confirm the illusion visually.
3. Manifest schema + loader + validation.
4. Real compositing with hard cuts.
5. Seam modes behind a flag.
6. Contact-sheet preview tool (the artists will want this before you want it).

---

# Addendum — Card numbering & integration with `PyPasyans/pasyans-3.py`

## 11. Signed card numbering, -50 … 50 (0 excluded)

**Card ID `c ∈ [-50,-1] ∪ [1,50]`. `sign(c)` selects the source half; `|c|` is the card number.**

| ID | meaning |
|---|---|
| `+n` | card built from the **right** half of source `n` |
| `-n` | card built from the **left** half of source `n` |

So source `n` yields exactly **two** cards (`-n`, `+n`) — one per half — and the ±50 range
means a pool of 50 diamond canvases → 100 cards. This matches the codebase's existing
half-based compositor (`_split_and_pad(img, axis, keep_first)`), where `keep_first` maps
1:1 onto the sign: `left = (c < 0)`, `keep_first = (c < 0)`.

### 11.1 Filename encoding (do not use a bare leading minus)

`RGCard--7.png` is ugly and a leading `-` gets eaten by arg parsers. Recommend either:

```
RGCard-m07.png   # -7        RGCard-p07.png   # +7
RGCard_n07.png                  RGCard_p07.png
```

Provide the two helpers and forbid string surgery anywhere else:

```python
def card_id_to_stem(c: int) -> str: ...   # -7 -> "RGCard-m07"
def stem_to_card_id(s: str) -> int: ...   # "RGCard-m07" -> -7
```

Zero-pad to 2 digits so lexical sort == numeric sort within a sign group.

### 11.2 ⚠ Sign collision with existing state encoding — keep these separate

`pasyans-3.py` **already uses sign for a different meaning** in the per-card byte `rots`
(`np.byte`, `UNSET_ROT = -63`, locked rotation 0 stored as `-4`, `abs(value)` = display
rotation). Two different signed integers will exist in the system at once:

| Integer | Domain | Sign means |
|---|---|---|
| **card id** (new) | ±1…±50 | which source half |
| **`rots[y,x]`** (existing) | -63, -4…-1, 0…3 | hidden/locked vs free |

Rule for the dev: **card id sign is an asset/manifest concern only.** It must never be stored
in `rots`, `nats`, or `currs`, and never passed through the existing `abs()` normalisation.
The board keeps addressing cards by *index* `0…24`; the card id is a property of the asset
that index happens to hold. Keep an explicit `card_ids: np.ndarray[int]` alongside, not
merged into `rots`.

### 11.3 Deck sizing is now a sampling problem

Assets exist for 100 ids; the board is 5×5 = 25. A game draws 25 of the 100. Two policy
questions for you:

1. **Are `-n` and `+n` allowed in the same game?** They are two halves of one diamond, so
   they'll look like siblings. Probably want a flag (`--no-sibling-pairs`).
2. **Do ids still carry edge values?** Currently `RGTCards.csv` / `--random-cards` assign the
   edge numbers that produce `sum == 0` matches. Decide whether card **id** has any
   relationship to its edge values (e.g. id ±7 always carries ±7 somewhere) or whether the
   two numbering systems are fully independent. Independence is much simpler; a deliberate
   coupling could make the divination reading legible, which may be the whole point.

## 12. Rotation: already solved — do not bake it into the art

Good news, this closes open questions #1 and #2 from §9. The engine rotates cards **at display
time in 90° steps** (`show_card_rot(x, y, r)`, `r ∈ 0…3` = 0°/90°/180°/270°), and the match
logic derives a match-oriented rotation (`nats`) from the edge values. Therefore:

- The composer emits each card in **one canonical orientation** (the upright/left-half
  reading, say). No pre-rotated art, no per-view files.
- All four views come free from the existing viewer.
- **Requirement this creates on the art:** since the engine is the only thing applying
  rotation, multi-view legibility depends entirely on the source art being authored
  rotation-aware. The compositor stays dumb; add a `--view n` preview flag that just calls
  the existing rotation for artists to check their work.

Two things to confirm with the artist side:

- Is the illusion **pure rotation** (`C4`, 4 views) or also **mirrored** (`D4`, 8 views)?
  Existing `show_card_rot` only rotates — a mirror would need a new code path.
- Does the left/right split axis stay **vertical** for all cards? `split_axis` currently
  accepts `'random'`; a random axis per card is incompatible with a stable ±sign-means-half
  contract, since the sign must name the same geometric half every time. **Recommend
  pinning `split_axis='vertical'` in the Pasyans-card path.**

## 13. What is genuinely new vs. already in the codebase

| Concern | Status |
|---|---|
| Split source into halves, pad kept half with transparency, alpha-composite | **exists** (`_split_and_pad`, `_composite_from_sources`) |
| Seam avoidance via transparency rather than hard cut | **exists** |
| 90° display rotation, locked/match rotation | **exists** |
| Source pool discovery from a directory | **exists** (`_load_source_pool`) |
| Signed ±50 id space + filename encoding | **new** |
| 4-triangle diamond wedge arrangement (this is the actual card design) | **new** — current compositor layers 2 halves on a full square, it does not yet do the 4-wedge diagonal partition |
| Diamond-canvas authoring contract (safe area = ¼ of bbox) | **new** |
| Manifest / id → source mapping | **new** |

The single largest piece of work is the last-but-two row: the current generator *layers* two
halves over a square; the Pasyans design *partitions* a square into four triangular wedges.
Same vocabulary, different geometry — worth telling the dev explicitly so they don't try to
extend `_split_and_pad` and end up with an alpha stack instead of a partition.

## 14. Random set generation — contract (implemented in `PyPasyans/make_set.py`)

A **set** = the 25 card faces for one 5×5 board, plus the edge-value table, plus a manifest.

### 14.1 The two-key rule (most important thing in this section)

| Key | Range | Owned by | Used for |
|---|---|---|---|
| **board index** | `0…24` | `pasyans-3.py` | addressing a card on the board, loading `RGCard-<i>.tiff` |
| **card id** | `±1…±50` | `make_set.py` + art dir only | which source and which half |

`make_set.py` writes `RGCard-<board_index>.tiff` (the filename the engine already expects) and
records `board_index → card_id` in `set.json`. The engine never learns about card ids. This is
deliberate: `pasyans-3.py`'s per-card `rots` byte already overloads sign to mean
hidden/locked/rotation, so a signed card id must never enter that array.

⚠ **Three independent signed-numbering systems now exist.** Card id (`±50`, = half), edge value
(`±edge_range`, = match semantics), and `rots` (`-63`/`-4…-1`/`0…3`, = state). Same integer type,
unrelated meanings. Any bug that mixes them will be silent. Recommend the dev prefix variable
names: `card_id`, `edge_val`, `rot_byte`.

### 14.2 Sampling rules

1. The id space is built from **sources that actually loaded**, not from the theoretical ±50
   range. A set must never reference a missing file.
2. A source file's **number comes from its filename** (`src-07.png` → 7); unnumbered files are
   skipped with a warning, since they cannot be addressed by card id.
3. **Siblings** = `-n` and `+n`, the two halves of one diamond. Default is **sibling-free**: a set
   of 25 uses 25 distinct source numbers, one side each. `--allow-siblings` relaxes this to draw
   from the full 2×N id space.
4. Determinism via `--seed`; same seed ⇒ byte-identical faces and manifest (verified).

### 14.3 Interface

```
python make_set.py --sources <dir> --out <wdir>/RGTCards --seed N \
                   --size 300 --edge-range 12 [--allow-siblings] [--allow-duplicates] [--dry-run]
```

`--dry-run` prints the sampled 25 rows (index, id, stem, source, 4 edges) and writes nothing —
use it to eyeball a set before spending time on art.

### 14.4 Still stubbed in `make_set.py` (by design)

- `compose_face()` reproduces the **existing 2-layer half stack**, not the 4-wedge diamond
  partition. Plumbing was validated first; the wedge body is a drop-in swap once the slot
  rotation table (spec §3.3) is confirmed. It carries a `TODO(unconfirmed geometry)` marker.
- The `--allow-siblings` auto-fallback in `main()` exists for small pools; with fewer than 25
  numbered sources a sibling-free set is impossible and the run says so rather than emitting a
  short deck.
- Back-card art, vignette and the interpretation CSV path are untouched — `make_set.py` only
  writes faces + manifest.
