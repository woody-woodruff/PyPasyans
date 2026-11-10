# Debug Plan — Rotation of On-the-Fly Generated Cards

**Symptom (as reported):** With the pre-generated set in `./RGTCards`, the
"Matches" button (`match_cards()`) correctly orients matched cards. Cards
composed on-the-fly (compositor path — `--random-cards` / `--source-images`)
are **not** oriented correctly when the match/highlight flow runs
("Matechs" — see Terminology note).

**Date of review:** files as of Oct 29 tree; `pasyans-4.py` + `utils/compose_set.py`.

---

## Terminology (resolved)

"Matechs" confirmed as a typo for **Matches** (`match_cards()`). The failing
scenario is the Matches flow **while the compositor is active** (on-the-fly
card images).

---

## Code map (verified line refs, pasyans-4.py)

- `show_card_rot(x, y, r)` — ~484: rotates base image `images[card]` by
  `Image.rotate(r * 90)` (**counter-clockwise**) and pastes into `pimages`
  in place. Single rotation application point for normal display.
- `set_bttn_clrs()` — ~546: for cells with `mat_cnt[x,y] > 0`:
  - locks `rots[x,y]` to `mat_rots[x,y]` via **sign encoding**
    (positive = free, negative = locked; locked-rot-0 stored as `-4`),
  - shrinks to `(inner × inner)`, rotates `images[card]` by
    `abs(int(rots[x,y])) * 90`, composites the `MATCH_COLORS` border,
    pastes into `pimages`. **Does not call `show_card_rot`.**
  - Existing `.. todo::` in its docstring: *"When the compositor is active,
    verify that the composite seam stays visually centred after the
    (inner × inner) shrink."* — the author already suspected this path.
- `find_matches()` — ~587+: builds `mat_cnt` / `mat_rots` from edge tables
  `mp00/mp10/mp01/mp11`. Convention comment (~650): slots numbered CCW to
  match the scan sheet's wedge order `[T,L,B,R]`: T=0, L=1, B=2, R=3; edge
  column *i* sits at slot *i* at rot 0; `show_card_rot` turns CCW.
- `click_card(x, y)` — ~740: lazy on-the-fly generation on first reveal;
  draws from deck, picks deal rotation `r1`, ends with
  `show_card_rot(x, y, rots[x, y])` (~826).
- Compositor: `utils/compose_set.py` — `solve_rotation()` (~150) returns
  `(inverse_coeffs, angle_deg, is_pure_90_step_rotation)`;
  `compose_card()` (~288) places wedges into a card image.
- Pre-gen loader: `card_image_path()` (~201) → `RGTCards/RGCard-N.tiff`.

---

## Hypotheses (ordered by likelihood)

**H1 — Base-image orientation mismatch (most likely).**
The match flow rotates `images[card]` (the *base* image) assuming it is in
canonical rot-0 orientation. Pre-gen TIFFs are stored that way. If
`compose_card()` bakes wedges in a different reference orientation (e.g.
`angle_deg` from `solve_rotation` follows a CW convention, or the source
scan sheet wedge order is applied without the CCW remap), then the same
`mat_rots` value lands the card's edges one/two slots off — *looks exactly
like "not orienting correctly" while the math is fine.*

**H2 — Double rotation on the generated path.**
If on-the-fly generation bakes the deal rotation `r1` into the stored base
image (instead of keeping `images[card]` canonical and applying `r1` only
at display via `show_card_rot`), then `set_bttn_clrs()`'s
`rotate(abs(rots)*90)` rotates an already-rotated image → wrong final
orientation. Pre-gen cards can't double-rotate because their base is always
canonical.

**H3 — Non-square generated images + rotate-after-shrink.**
`set_bttn_clrs()` shrinks to `(inner × inner)` then rotates. If generated
cards are not exactly square before the shrink (source pool images vary in
aspect), PIL `resize` squashes rather than crops, and a 90°/270° rotate
swaps the distortion axis → card appears rotated/mis-centred. The existing
`todo` about the seam centring points at this.

**H4 — Highlight overwritten by later lazy generation.**
`match_cards()` re-seeds the RNG and skips cells whose image isn't
generated yet. If a matched card is revealed/generated *after* a Matches
click (or regenerated on a second click), the fresh `show_card_rot` paint
overwrites the match-oriented, bordered paint. Timing/ordering bug rather
than geometry.

**H5 — Sign-encoding edge case on generated path.**
Locked rot 0 stored as `-4`; `abs()` → 4 → `rotate(360)` = identity (fine
for pre-gen). Check `mat_rots` dtype (int8) and that generated-path code
never stores raw `-0`-style values or values outside `[-4, 3]`. Low
likelihood but cheap to rule out.

---

## Step 0 — Deterministic debug mode (DONE)

`RGTCards/CardList.csv` (copy of `RGTList.csv`) now pins the edge matrix.
`_load_or_generate_card_list()` checks `card_dir/CardList.csv` **first**,
before the `--random-cards` branch, so the compositor path can be exercised
with identical card values every run. Verified headlessly: override returns
RGTList values with `--random-cards` set; renaming the file restores fresh
random generation. Delete/rename `CardList.csv` to return to normal
behaviour.

Note: pre-existing test failure `utils/tests/test_deck.py::
TestScanColumnOrder::test_hand_session_matches_tables` — missing
`sessions/Deal-01-Hand.json` data file; unrelated to this change
(11 passed, 1 failed before and after).

## Card-list data convention (baked in — stay aware)

Per the partner: in the card list each row starts with the card's
**lowest-|wedge| at position 0 ("to the left"), then continues CCW around the
card**. This is an artifact of how the original cards were scanned — meant
only to simplify debugging. Any column order is valid as long as it matches
the visual card, and it is **distinct from the first-reveal rotation**
(`org_rots`/`rots`, which are independent RNG draws).

Verified against data: all 25 rows of `RGTList.csv` satisfy
`abs(col0) == min(abs(row))`; col0 signs run in ± pairs
(`-+-+-+-+-+-++++-+-+---+--`), reflecting the scan sheet's pair ordering.
`CardDataGenerator.generate()` does **not** enforce the convention (no
in-row sort), and nothing in `find_matches`/`set_bttn_clrs` depends on it —
so it is currently data-only.

⚠ **Convention discrepancy — possible root-cause lead.** Three sources
disagree about which wedge column 0 is:

1. `find_matches` comment: native order `[T,L,B,R]`, "column i sits at slot
   i" with T=0 → col 0 = **top**.
2. Partner (above): col 0 = **left**, then CCW → `[L,B,R,T]`.
3. `utils/_scratch_match_harness.py`: "empirically verified" col 0 faces
   **south** at rot 0.

These are the same CCW cycle with different starting points — i.e. they
disagree by whole quarter-turns about the index-0 column. If the data truly
starts at left, the code's `column i = slot i (T=0)` mapping is off by one
slot, and the pre-built path's load-time `.rotate(-90)` may be *accidentally
absorbing* the offset while the compositor (baked upright, "no correction")
exposes it — which matches the reported symptom exactly.

**Diagnostic:** for one card, compare the visual wedge positions of the
pre-built TIFF (after the -90 leveling) against the compositor face at
rot 0, against the CSV row. If they differ by a quarter-turn, the fix is to
align the compositor's wedge placement (or the slot-index convention), not
the match math.

## Diagnostic steps

1. **Repro both modes, record everything.**
   Run pre-gen and `--random-cards` (and `--source-images` if that's the
   failing mode). For one matched card, log: `spread[x,y]`, `mat_cnt`,
   `mat_rots[x,y]`, final `rots[x,y]`, and the rotation actually applied.
2. **Dump base images.** Add a debug hook (or scratch script reusing
   `utils/_scratch_match_harness.py`) that saves `images[card]` for a
   generated card to disk next to the corresponding pre-gen TIFF. Compare
   orientation of a known edge value at rot 0. **This single comparison
   discriminates H1/H2 from H3–H5.**
3. **Unit-test the compositor's angle convention.** In
   `utils/compose_set.py`, for each pure 90° step, assert that
   `solve_rotation`'s `angle_deg` and PIL `Image.rotate` agree on
   direction (CCW) and that wedge placement matches the T=0,L=1,B=2,R=3 CCW
   convention in `find_matches()`'s comment. Extend `tests/test_deck.py` /
   `utils/tests/` with an edge-position test: compose a card, apply
   `mat_rots` rotation, assert each edge value lands in the expected slot —
   run identically for a pre-gen TIFF and a composed card.
4. **Check squareness.** Log `images[card].size` for generated vs pre-gen
   before the `(inner × inner)` shrink in `set_bttn_clrs()`.
5. **Check ordering.** After clicking Matches, reveal a matched-but-never-
   clicked cell (if reachable) and see whether the highlight/orientation is
   lost → confirms H4.

## Fix strategy (pending diagnosis)

- H1: normalize `compose_card()` output to canonical rot-0 (adjust
  `solve_rotation`/placement by the CCW remap) so all downstream rotation
  math is untouched.
- H2: store canonical rot-0 base images on the generated path; apply `r1`
  only in `show_card_rot`.
- H3: crop (not squash) to square before shrink, or rotate *before* resize.
- H4: in the lazy-generation branch of `click_card`, if `mat_cnt[x,y] > 0`,
  re-run the `set_bttn_clrs` paint for that cell after generating.
- H5: clamp/validate `mat_rots` writes; add an assertion.

## Regression checks

- `pytest` (there is a `.pytest_cache`; `tests/test_deck.py` pins the
  rotation convention).
- Manual: same seed not reproducible by design (RNG re-seeds on user
  timing — "mystical connection"), so verify by *invariants* (edge lands in
  slot) rather than golden screenshots.

## Caveats / notes

- "Matechs" not found in code — confirm which build/button before fixing.
- RNG re-seeding on every click/Matches makes runs non-reproducible by
  design; instrument with logged state, not replay.
- Security: nothing flagged in this path; standard note — the walled-garden
  assumption here means no untrusted-image parsing hardening on the
  compositor input pool (PIL decompression-bomb style concerns are ignored
  by choice).

## Resolution (implemented)

- **CardList.csv override** (`_load_or_generate_card_list`): when
  `RGTCards/CardList.csv` exists its 25x4 matrix pins the edge values in
  every mode (pre-built, `--random-cards`, `--sources`), so on-the-fly
  debugging runs with fixed, known values.
- **Edge id == wedge id** (`main`): when the compositor builds a deck plan,
  the edge matrix is derived from that plan
  (`card_list = np.array(compositor.plan, dtype=np.byte)`).  The plan places
  signed wedge id k at CCW slot k and each wedge carries its source number
  baked into the art, so the numbers visible on a card match its edge
  values by construction.  Matching is unaffected: values match when they
  sum to zero, and each source n contributes exactly one -n and one +n
  wedge.  The plan is seed-deterministic; the CSV override still pins the
  pre-built path.
- **Corner labels** (`utils/compose_set.py::stamp_source_number`): the
  source number is stamped twice, centred on the vertical T-B cut line —
  one copy towards the top corner of the canvas, one towards the bottom —
  so the wedge split divides each copy into a left half (rides with wedge
  -n) and a right half (wedge +n), and the digits stay completely inside
  the diamond canvas frame.
- **Card-defs from the art folder** (`_find_defs_csv` + `_load_card_defs`):
  with `--sources`, the definitions CSV is resolved from the source-images
  directory — canonical engine format, or an art sheet (header naming
  'Image Name'/'ID').  Art sheets are remapped into the engine layout
  `[title, interp1..4, duration, name]` keyed by ID-1 and sized to the
  largest ID present (50 sources -> 50 rows; this fixed the Details-button
  IndexError on edge ids above 25).
- **Debug text field** (`_debug_dump` + the `tk.Text` widget in `main`):
  clicking a card appends its slot, card index, edge values, rots/org/mat,
  image source (prebuilt/compositor), and the compositor plan entry
  (`wedges=[...]`) naming the numbers visible on the card.

Verification: `python3 -m pytest -q` -> 11 passed; the single failure
(`test_hand_session_matches_tables`) is caused by `sessions/Deal-01-*.json`
being deleted in the working tree, not by these changes.
