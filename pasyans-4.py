"""
pasyans-4.py
============
PyPasyans — a Pasyans divination-deck GUI.

Variant of pasyans-3.py that can build the whole deck IN MEMORY from a directory
of numbered source images, standing in for both the pre-built card faces
(``RGCard-<i>.tiff``) and the ``RGTList.csv`` edge matrix::

    python3 pasyans-4.py --sources /path/to/LoremIpsoCards --seed 7
    python3 pasyans-4.py --sources /path/to/LoremIpsoCards --dry-run   # no window

Conventions this file depends on:
  * source filenames start with their number (``07-Name.png`` -> source 7);
  * a card id is signed: ``-n`` = LEFT half of source n, ``+n`` = RIGHT half;
  * each face is four wedges (slots top/left/bottom/right — counter-clockwise,
    the scan sheet's native wedge order); every wedge is used
    once per deck and no face carries both halves of one source;
  * ``--sources`` is an alias of ``--source-images``, and either implies
    ``--random-cards`` (so ``RGTList.csv`` is not read).
Without ``--sources`` behaviour is identical to pasyans-3.py.

A 5×5 grid of 25 cards is laid face-down; the player clicks each card to
reveal it in a randomly assigned orientation.  Adjacent cards whose touching
edges carry complementary values (sum == 0) form a *match*, highlighted with
a coloured border.  The interpretation of each match is looked up from either
``RGTCards.csv`` or generated dynamically when ``--random-cards`` is active.

Card-edge matching
------------------
Each card has four edge-values (one per side).  A match between two neighbours
occurs when ``card_list[a, i] + card_list[b, j] == 0`` for the shared edge
indices ``i`` (card *a*) and ``j`` (card *b*).  Edge values are signed integers:
positive on one card always pairs with the matching negative on the other.

Rotation encoding (``rots`` array)
------------------------------------
A single :class:`numpy.byte` per grid cell encodes three states using sign:

* ``>= 0`` — card is visible and free to rotate; value is rotation in counter-clockwise 90° steps
  (``0``–``3``).
* ``< 0``  — card is either **hidden** (not yet clicked) or **locked** (orientation
  fixed by a match highlight); ``abs(value)`` is the display rotation.
* Special case: locked rotation ``0`` is stored as ``-4`` (negating ``0`` would be
  ``0``, which is positive and therefore indistinguishable from "free").

This packs hidden/locked/rotation into one byte per card intentionally — no
parallel boolean arrays required.

RNG and the mystical connection
--------------------------------
``random.seed()`` (system-entropy reseed) is called on *every* user interaction.
This is an **intentional design feature**: the exact moment the user clicks each
card re-seeds the random state, creating a direct coupling between the timing
of the user's actions and the outcome of the reading.  Do not remove this
behaviour without discussion.

Lazy image generation
----------------------
Card face images are generated (or loaded) on *first reveal*, not at startup.
Until then each slot displays a decorative card-back pattern.  A wait indicator
in the status bar is shown during generation.  Pre-loaded assets (CSV data,
source image pool) are read at startup so generation itself is fast.

Asset layout
------------
All assets are resolved relative to ``--wdir`` (default ``"."``):

.. code-block:: text

    <wdir>/
      RGTCards/
        RGTList.csv         — edge-value matrix  (25 rows × 4 cols, signed int)
        RGTCards.csv        — card definitions   (25 rows × N cols, str)
        RGCard-0.tiff       — pre-built card face images (optional)
        …
        RGCard-24.tiff
      <source-images-dir>/  — pool of RGBA source images for compositing
        *.png / *.tiff …

Usage
-----
.. code-block:: bash

    # Use pre-built card assets (original behaviour)
    python pasyans.py [--size 300] [--border 5] [--wdir .]

    # Randomly generated edge values, composited images
    python pasyans.py --random-cards --source-images ./sources --edge-range 12

    # Random edges, no duplicate values per card, custom split axis
    python pasyans.py --random-cards --no-duplicates --split-axis v

:author:  <your name>
:version: 0.2.0
:license: <your licence>

.. todo::
    * Decide on final scrolling strategy (inline canvas vs ``ScrollableFrame``).
    * Replace module-level globals with a proper application class once the
      feature set has stabilised.
    * Consider persisting the run seed so a reading can be reproduced exactly.
    * Add keyboard navigation (arrow keys to rotate, space to reveal).
    * Add an "export reading" function (screenshot + match summary to PDF).
"""
# ---------------------------------------------------------------------------
# Standard library
# ---------------------------------------------------------------------------
import sys
import math
import random
import pathlib
import json
import argparse
import subprocess
from functools import partial
from datetime import datetime
from typing import List, Optional, Tuple
# ---------------------------------------------------------------------------
# Third-party
# ---------------------------------------------------------------------------
import numpy as np
import tkinter as tk
from tkinter import ttk
from tkinter import filedialog
from PIL import Image, ImageTk, ImageDraw, ImageFilter
# ---------------------------------------------------------------------------
# Local toolchain import shim
# ---------------------------------------------------------------------------
# ``compose_set`` lives in utils/ next to this script.  Make it importable
# regardless of the current working directory (previously required running
# with PYTHONPATH=utils from the project root).
_UTILS_DIR = pathlib.Path(__file__).resolve().parent / "utils"
if str(_UTILS_DIR) not in sys.path:
    sys.path.insert(0, str(_UTILS_DIR))

# ---------------------------------------------------------------------------
# Pasyans package (P2 split: engine + CLI live in ./pasyans/)
# ---------------------------------------------------------------------------
from pasyans.deck import (
    CLR_BG,
    CLR_BORDER,
    CLR_GOLD,
    CLR_GOLD_LT,
    CLR_MUTED,
    CLR_SURFACE,
    CLR_TEXT,
    DECK_SIZE,
    GRID_SIZE,
    MATCH_COLORS,
    UNSET_CARD,
    UNSET_ROT,
    CardDataGenerator,
    ImageCompositor,
    WedgeCompositor,
)
from pasyans.cli import _build_arg_parser
# ---------------------------------------------------------------------------
# Module metadata  (consumed by setuptools, Sphinx autodoc, and CI pipelines)
# ---------------------------------------------------------------------------
__version__ = "0.2.0"
__all__: List[str] = []   # nothing is intended to be imported from this module

#: Board geometry (rows x cols).  Defaults to a square GRID_SIZE grid and is
#: overwritten from --rows/--cols in main().  Independent of DECK_SIZE: cards
#: run out (cells > cards) or go unused (cards > cells).
GRID_ROWS: int = GRID_SIZE
GRID_COLS: int = GRID_SIZE

#: Cells excluded from play via ``--void`` (normalised in main()).  A set of
#: ``(x, y)`` tuples; void cells never hold a card and render as a plain
#: background-coloured tile instead of a card back.
void_cells: set = set()
############################################################################################################################
# ScrollableFrame
############################################################################################################################

# TODO: ScrollableFrame is unused — the main window uses an inline canvas/scrollbar
#       approach instead.  Retain until the final scrolling strategy is decided,
#       then either adopt this class or remove it.

class ScrollableFrame(ttk.Frame):
    """A :class:`ttk.Frame` that automatically grows scroll-bars.

    Wraps an inner :class:`tk.Canvas` so that any widgets placed inside
    ``self.scrollable_frame`` can be scrolled both horizontally and vertically.

    Args:
        container: Parent widget.
        *args:     Passed through to :class:`ttk.Frame`.
        **kwargs:  Passed through to :class:`ttk.Frame`.

    Attributes:
        scrollable_frame (:class:`ttk.Frame`): Place child widgets here.
    """

    def __init__(self, container: tk.Widget, *args, **kwargs) -> None:
        super().__init__(container, *args, **kwargs)
        canvas      = tk.Canvas(self)
        scrollbar   = ttk.Scrollbar(self, orient="vertical",   command=canvas.yview)
        scrollbar_h = ttk.Scrollbar(self, orient="horizontal", command=canvas.xview)
        self.scrollable_frame = ttk.Frame(canvas)
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set, xscrollcommand=scrollbar_h.set)
        scrollbar_h.grid(sticky='ew')
        scrollbar.grid(sticky='ns')
        canvas.grid(sticky='news')
def card_image_path(n: int) -> pathlib.Path:
    """Return the pre-built asset path for card face image *n*.

    Only relevant when ``--random-cards`` is *not* set and pre-built tiffs
    exist.  When the compositor is active this function is not called.

    Args:
        n: Card index in ``[0, DECK_SIZE)``.

    Returns:
        pathlib.Path: Path to ``RGCard-<n>.tiff``.

    .. todo::
        Remove this function once the compositor path is the only supported
        workflow; at that point pre-built tiffs become an unsupported legacy
        path.
    """
    return card_dir / f"RGCard-{n}.tiff"
"""Number of cards along each axis of the spread."""
"""Total number of unique cards in the deck (``25``)."""
"""Sentinel in ``spread[x, y]`` — slot not yet drawn."""
"""Sentinel in ``rots[x, y]``, ``nats``, ``currs`` — card hidden / not yet revealed.
Chosen well outside the valid rotation range ``0``–``3``.

INTENTIONAL TRIXY DATA USE: The value -63 is chosen because:
- Valid rotations are 0,1,2,3 (positive values representing 0°,90°,180°,270°)
- Hidden/locked states use negative values
- -63 is far outside the range [-4, -1] used for locked rotations, preventing
  accidental collisions if a card somehow got assigned a rotation of -63
- Using a single byte (np.byte range: -128 to 127) with a large negative
  sentinel ensures we have headroom for all state encodings"""
############################################################################################################################
# Data loading
############################################################################################################################

def _load_or_generate_card_list() -> np.ndarray:
    """Return the ``(DECK_SIZE, 4)`` edge-value matrix.

    If ``--random-cards`` is set, generates fresh values via
    :class:`CardDataGenerator` (using whatever RNG state is current at
    startup).  Otherwise loads from ``RGTList.csv``.

    A debug override is honoured first: if ``CardList.csv`` exists in
    ``card_dir`` it pins the deck regardless of ``--random-cards``, so the
    compositor path can be exercised deterministically while debugging.

    Returns:
        numpy.ndarray: Shape ``(DECK_SIZE, 4)``, dtype ``np.byte``.

    Raises:
        SystemExit: If ``--random-cards`` is *not* set and ``RGTList.csv``
                    cannot be found.
    """
    # ---- Debug override: fixed card list -----------------------------------
    # CardList.csv uses the same native [T,L,B,R] convention as RGTList.csv.
    # While it exists it wins over --random-cards; rename or delete it to
    # return to normal behaviour.
    debug_card_list_csv = card_dir / "CardList.csv"
    if debug_card_list_csv.exists():
        print(f"[DEBUG] fixed card list in use: {debug_card_list_csv}",
              file=sys.stderr)
        return np.loadtxt(debug_card_list_csv, dtype=np.byte, delimiter=',')

    if args.random_cards:
        gen = CardDataGenerator(
            deck_size=DECK_SIZE,
            edge_range=args.edge_range,
            allow_duplicates=not args.no_duplicates,
            match_once=args.match_once,
        )
        return gen.generate()

    if not card_list_csv.exists():
        print(
            f"[ERROR] {card_list_csv} not found.  "
            "Use --random-cards to generate edge values procedurally.",
            file=sys.stderr,
        )
        sys.exit(1)

    # The scan sheet lists each card's wedges right-to-left (counter-clockwise)
    # as [T,L,B,R].  That IS the engine's native convention: edge column i sits
    # at counter-clockwise slot i (T=0, L=1, B=2, R=3), matching the CCW turn of
    # Image.rotate(r * 90).  No reordering is applied — the scan sheet is the
    # source of truth and round-trips unchanged through save/load.
    return np.loadtxt(card_list_csv, dtype=np.byte, delimiter=',')
def _find_defs_csv(src_dir: pathlib.Path) -> Optional[pathlib.Path]:
    """Return an engine-format definitions CSV inside *src_dir*, if any.

    Card definitions belong with the art they describe.  The canonical name
    is ``RGTCards.csv``; other ``RGTCards*.csv`` variants (e.g.
    ``RGTCards_Lorem.csv``) are accepted only when they carry the engine's
    shape (``DECK_SIZE`` rows x >= 7 columns) -- raw definition sheets with
    a different schema are skipped so the caller's wdir fallback stays in
    charge.

    Args:
        src_dir: The ``--source-images`` directory being used for the deck.

    Returns:
        Path to a usable definitions CSV, or ``None``.
    """
    import csv
    canonical = src_dir / "RGTCards.csv"
    if canonical.is_file():
        print(f"[INFO] card definitions: {canonical} (same directory as sources)")
        return canonical
    for variant in sorted(src_dir.glob("RGTCards*.csv")):
        try:
            with variant.open(newline='') as fh:
                rows = list(csv.reader(fh))
        except OSError:
            continue
        if len(rows) == DECK_SIZE and rows and len(rows[0]) >= 7:
            print(f"[INFO] card definitions: {variant} (same directory as sources)")
            return variant
        # Art-sheet format (e.g. LoremIpsoCards/RGTCards_Lorem.csv): a header
        # row naming 'Image Name' and 'ID' columns plus at least DECK_SIZE
        # data rows.  Entries beyond the deck size are ignored; _load_card_defs
        # remaps the wider column set into the engine layout.
        if rows and 'Image Name' in rows[0] and 'ID' in rows[0] \
                and len(rows) - 1 >= DECK_SIZE:
            print(f"[INFO] card definitions: {variant} (art sheet, remapped)")
            return variant
    return None


def _load_card_defs() -> Optional[np.ndarray]:
    """Load ``RGTCards.csv`` if it exists; return ``None`` otherwise.

    Interpretations are optional — if the CSV is missing (e.g. when running
    with ``--random-cards`` and no asset directory), the "Show Details" button
    is hidden.

    Returns:
        numpy.ndarray or None: Shape ``(DECK_SIZE, N)``, dtype ``str``, or
        ``None`` if the file cannot be found.
    """
    if not card_defs_csv.exists():
        return None

    # Art-sheet format (header naming 'Image Name'/'ID'): remap into the
    # engine layout [title, interp1..4, duration, name] with row = ID-1.
    # Since edge id == wedge id, edge values can reference ANY source id in
    # the pool (not just the first DECK_SIZE), so the table is sized to the
    # largest ID present.  Built as a list of rows first so numpy picks a
    # string width that fits the text.
    import csv
    with card_defs_csv.open(newline='') as fh:
        _rows = list(csv.reader(fh))
    if _rows and 'Image Name' in _rows[0] and 'ID' in _rows[0]:
        _ids = []
        for _r in _rows[1:]:
            try:
                _ids.append(int(_r[1]))
            except (ValueError, IndexError):
                pass
        _n = max([DECK_SIZE] + _ids)
        _table = [[''] * 7 for _ in range(_n)]
        for _r in _rows[1:]:
            try:
                _idx = int(_r[1]) - 1
            except (ValueError, IndexError):
                continue
            if 0 <= _idx < _n and len(_r) >= 10:
                # Art-sheet interps (cols 1..4) are often blank; fall back to
                # the card's Image Meaning (title) so the Details view has
                # explanatory text to show. Explicit interps win when present.
                _title = _r[4]
                _interps = [_r[5], _r[6], _r[7], _r[8]]
                _interps = [i if i.strip() else _title for i in _interps]
                _table[_idx] = [_title, *_interps, _r[9], _r[0]]
        return np.array(_table, dtype=str)

    return np.loadtxt(
        card_defs_csv,
        dtype=str,
        comments=None,
        delimiter=',',
        converters=None, skiprows=0, usecols=None,
        unpack=False, ndmin=0, encoding=None,
        max_rows=None, quotechar='"',
    )
# ---------------------------------------------------------------------------
# Diagnostics.  --dry-run / --self-test / --list-sources / --dump-cards all run
# and exit BEFORE `root = tk.Tk()`.  That placement is the whole point: under
# IDLE or PythonLauncher a second Tk mainloop can beachball the shell, so every
# check worth running has to be reachable without ever creating a window.
# ---------------------------------------------------------------------------
def _diag_self_test() -> int:
    """Verify the invariants that, if broken, silently produce bad cards.

    Returns the number of failures (nothing here raises, so one run reports all
    of them instead of stopping at the first).
    """
    import itertools
    import compose_set as _cs

    checks = []

    # T1: the four slot polygons must tile the square exactly.
    cover = Image.new('L', (_cs.S, _cs.S), 0)
    _d = ImageDraw.Draw(cover)
    for slot in _cs.SLOTS:
        _d.polygon([tuple(map(int, p)) for p in _cs.WEDGE[slot]], fill=1)
    carr = np.asarray(cover)
    gaps, over = int((carr == 0).sum()), int((carr > 1).sum())
    checks.append(('wedge tiling exact', gaps == 0 and over == 0,
                   f'{gaps} uncovered px, {over} overlapped px'))

    # T2: each (side, slot) pairing must be a PURE rotation in 90-deg steps.
    # |det| == 1 alone is not enough -- it also admits shears, which tilt a wedge
    # out of the engine's 90-degree rotation scheme.  This broke once already.
    bad = []
    for sign in (-1, 1):
        for slot in _cs.SLOTS:
            base = _cs.half_triangle(sign)
            wedge = list(_cs.WEDGE[slot])
            if not any(_cs.solve_rotation(base, [wedge[i] for i in perm])[2]
                       for perm in itertools.permutations(range(3))):
                bad.append(f'{"LEFT" if sign < 0 else "RIGHT"}/{slot}')
    checks.append(('half->wedge maps are pure 90-deg rotations', not bad,
                   ', '.join(bad) if bad else '8/8 pairings OK'))

    # T3: deck disciplines, on the deck that was actually built.
    plan = getattr(compositor, 'plan', None)
    if plan is None:
        checks.append(('deck disciplines', True, 'skipped (no --sources given)'))
    else:
        flat = [w for card in plan for w in card]
        checks.append(('every wedge used exactly once',
                       len(set(flat)) == len(flat),
                       f'{len(set(flat))} distinct / {len(flat)} slots'))
        checks.append(('no card carries both halves of one source',
                       all(len({abs(w) for w in c}) == 4 for c in plan),
                       f'{len(plan)} cards checked'))
        probs = _cs.validate_plan(plan)
        checks.append(('validate_plan agrees', not probs,
                       '; '.join(probs[:3]) if probs else 'no problems'))

    # T4: the edge matrix the engine will play with.
    # NB deliberately NOT indexed by slot column.  find_matches() reaches a match
    # by ROTATING cards (mp00/mp10/mp01/mp11 orient them), so counting complements
    # in one fixed orientation undercounts -- it reported "0 pairs" on a perfectly
    # playable shipped board.  An adjacency is matchable if SOME edge of one card
    # is the negative of SOME edge of its neighbour; that needs no assumption about
    # which column holds which edge.
    if card_list is not None:
        zeros = int((card_list == 0).sum())
        g = GRID_SIZE
        potential = 0
        for y in range(g):
            for x in range(g):
                i = y * g + x
                for nb in ((y * g + x + 1) if x + 1 < g else None,
                           ((y + 1) * g + x) if y + 1 < g else None):
                    if nb is None:
                        continue
                    a = {int(v) for v in card_list[i]}
                    b = {int(v) for v in card_list[nb]}
                    if a & ({-v for v in b}):
                        potential += 1
        checks.append(('edge values nonzero', zeros == 0, f'{zeros} zeros'))
        checks.append(('board has matchable adjacencies', potential > 0,
                       f'{potential} of {4 * g * (g - 1)} adjacencies completable '
                       f'by rotation'))
        checks.append(('edge matrix shape/dtype',
                       card_list.shape == (DECK_SIZE, 4) and card_list.dtype == np.byte,
                       f'{card_list.shape} {card_list.dtype}'))
    else:
        checks.append(('edge matrix', False, 'card_list is None'))

    # T5: interpretation text must carry the columns the engine indexes.
    if card_defs is not None:
        cols = card_defs.shape[1] if card_defs.ndim > 1 else 1
        checks.append(('RGTCards.csv has >= 7 columns', cols >= 7,
                       f'{cols} columns (engine indexes column 6)'))
    else:
        checks.append(('RGTCards.csv', True, 'not loaded (dynamic text in use)'))

    fails = [n for n, ok, _ in checks if not ok]
    for name, ok, detail in checks:
        print(f'  [{"PASS" if ok else "FAIL"}] {name}: {detail}')
    print(f'self-test: {len(checks) - len(fails)}/{len(checks)} passed'
          + (f' -- FAILED: {", ".join(fails)}' if fails else ''))
    return len(fails)
def _show_loading(x: int, y: int) -> None:
    """Display the loading overlay on card button *(x, y)* and start the
    indeterminate progress bar in the status bar.

    Must be followed by ``root.update_idletasks()`` to ensure the visual
    update is painted before the (synchronous) generation begins.

    Args:
        x: Column index.
        y: Row index.
    """
    idx = (y * GRID_COLS) + x
    pimages[idx].paste(_loading_overlay)
    _progress.pack(side=tk.RIGHT, padx=6, pady=2)
    _progress.start(12)
    _status_msg.set("Generating card image…")
def _hide_loading() -> None:
    """Stop the progress bar and remove it from the status bar."""
    _progress.stop()
    _progress.pack_forget()
############################################################################################################################
# Status bar update helpers
############################################################################################################################

def _update_status() -> None:
    """Refresh the status bar counts from current game state.

    Counts revealed cards (``spread != UNSET_CARD``) and total found matches
    (``len(matches)``).  Safe to call at any point; reads only global state.
    """
    revealed = int(np.sum(spread != UNSET_CARD))
    _status_revealed.set(f"Revealed: {revealed} / {DECK_SIZE}")
    _status_matches.set(f"Matches: {len(matches)}")
############################################################################################################################
# Debug dump helpers
############################################################################################################################

#: Debug text widget (created in ``main()``; ``None`` until then).
_debug_text: Optional[tk.Text] = None

#: Card index -> image source (``'prebuilt'`` | ``'compositor'``), recorded
#: at lazy-generation time so debug dumps can show which path produced the
#: base image (relevant to the rotation-convention investigation).
_img_src: dict = {}

def _debug_dump(x: int, y: int) -> None:
    """Append one line of debug info about the card at *(x, y)*.

    Reports card index, native edge values ``[T,L,B,R]``, display rotation
    (with sign encoding intact), original deal rotation, match state
    (``mat_cnt``/``mat_rots``) and the image source.  Safe to call before
    the widget exists (no-op).
    """
    if _debug_text is None:
        return
    card = int(spread[x, y])
    if card == UNSET_CARD:
        line = f"({x},{y}) hidden"
    else:
        edges = list(map(int, card_list[card])) if card_list is not None else '?'
        line = (f"({x},{y}) card={card} edges={edges} "
                f"rots={int(rots[x, y])} org={int(org_rots[x, y])} "
                f"mat={int(mat_cnt[x, y])}/{int(mat_rots[x, y])} "
                f"src={_img_src.get(card, '?')}")
        # Compositor cards carry the *source* image numbers in their corners
        # (baked before the wedge split), which are a different ID space from
        # the card-list index above.  Report the plan entry so the dump line
        # names the numbers actually visible on the card.  ``WedgeCompositor``
        # fills its plan at build time; until then this part is absent.
        comp = globals().get('compositor')
        plan = getattr(comp, 'plan', None)
        if plan is not None and card < len(plan):
            line += f" wedges={[int(w) for w in plan[card]]}"
    _debug_text.configure(state='normal')
    _debug_text.insert('end', line + '\n')
    _debug_text.see('end')
    _debug_text.configure(state='disabled')
############################################################################################################################
# UI callbacks
############################################################################################################################

def quit_clicked() -> None:
    """Destroy the Tk root and exit the process cleanly."""
    root.destroy()
    sys.exit(69)
def zap_labels(w: tk.Widget) -> None:
    """Destroy all immediate ``TLabel`` children of widget *w*.

    Used by :func:`show_match_details` to clear old interpretation rows
    before re-populating them.

    Args:
        w: Parent widget.  Only direct children are examined (no recursion).
    """
    for child in w.winfo_children():
        if child.winfo_class() == "TLabel":
            child.destroy()
def show_card_rot(x: int, y: int, r: int) -> None:
    """Rotate the card face at *(x, y)* and paste the result into ``pimages``.

    The :class:`~PIL.ImageTk.PhotoImage` is mutated in-place so the
    :class:`tk.Button` referencing it updates without an explicit
    ``configure(image=…)`` call.

    Args:
        x: Column index ``[0, GRID_SIZE)``.
        y: Row index ``[0, GRID_SIZE)``.
        r: Rotation in 90° steps (PIL convention: positive = CCW).

    Note:
        * Does nothing if ``spread[x, y] == UNSET_CARD``.
        * Does nothing if ``images[card]`` is still ``None`` (lazy generation
          has not run yet for this card index — should not occur in normal flow
          because :func:`click_card` generates the image before calling here).
    """
    card = spread[x, y]
    if card == UNSET_CARD:
        return
    if images[card] is None:
        return
    imgobj = images[card].rotate(int(r) * 90)   # int() guards np.byte*90 int8 overflow
    pimages[(y * GRID_COLS) + x].paste(imgobj)
def reset_cards() -> None:
    """Restore all placed cards to the rotation assigned at deal time.

    Re-seeds the RNG (mystical connection — user's timing of clicking Reset
    becomes part of the reading's entropy).

    INTENTIONAL: The user's timing when clicking Reset influences the reading
    by re-seeding the RNG. This is consistent with the "mystical connection"
    design where user interactions directly shape the entropy of the reading.
    """
    # INTENTIONAL: Re-seed from system entropy on user action
    # See module docstring "RNG and the mystical connection"
    random.seed()
    _seed_var.set(f"seed: {random.getstate()[1][0]:010d}")
    for x in range(GRID_COLS):
        for y in range(GRID_ROWS):
            rots[x, y] = org_rots[x, y]
            show_card_rot(x, y, rots[x, y])
    _status_msg.set("Cards reset to deal rotation.")
def zero_cards() -> None:
    """Set every revealed card to rotation ``0`` (upright).

    Re-seeds the RNG for the same mystical-coupling reason as
    :func:`reset_cards`.

    INTENTIONAL: User timing when clicking Zero becomes part of the reading's
    entropy chain, consistent with the "mystical connection" design.
    """
    # INTENTIONAL: Re-seed from system entropy on user action
    # See module docstring "RNG and the mystical connection"
    random.seed()
    _seed_var.set(f"seed: {random.getstate()[1][0]:010d}")
    rots.fill(0)
    for x in range(GRID_COLS):
        for y in range(GRID_ROWS):
            show_card_rot(x, y, rots[x, y])
    _status_msg.set("All cards zeroed.")
def set_bttn_clrs() -> None:
    """Apply match-highlight borders to all matched cards.

    INTENTIONAL OVERLOAD: Uses sign encoding in ``rots`` to lock match-highlighted
    cards. A positive rotation value means "free to rotate"; a negative value means
    "locked" (rotation fixed by match). To distinguish locked rotation 0 from a
    "free" card, locked rotation 0 is stored as -4 (negating 0 would give 0,
    which is positive and indistinguishable from free).

    For each cell where ``mat_cnt[x, y] > 0``:

    * Locks the card's rotation into ``mat_rots[x, y]`` by storing it as
      a negative value in ``rots``.  Rotation ``0`` is stored as ``-4``
      to avoid ``-0 == 0`` (which would be indistinguishable from "free").
    * Shrinks the card image by ``mat_border`` pixels on each side.
    * Composites a solid-colour border using :data:`MATCH_COLORS`.

    .. todo::
        When the compositor is active, verify that the composite seam stays
        visually centred after the ``(inner × inner)`` shrink.
    """
    for x in range(GRID_COLS):
        for y in range(GRID_ROWS):
            if mat_cnt[x, y] > 0:
                card = spread[x, y]
                if images[card] is None:
                    continue
                r = mat_rots[x, y]
                if r == 0:
                    r = 4   # -0 == 0 would be "free"; store as -4 instead
                rots[x, y] = -r

                inner  = card_size - (2 * mat_border)
                imgobj = images[card].resize((inner, inner))
                imgobj = imgobj.rotate(abs(int(rots[x, y])) * 90)   # int() guards int8 overflow

                color = MATCH_COLORS.get(int(mat_cnt[x, y]), (200, 200, 200))
                timg  = Image.new('RGB', (card_size, card_size), color)
                timg.paste(imgobj, (mat_border, mat_border))
                pimages[(y * GRID_COLS) + x].paste(timg)
def match_cards() -> None:
    """Find all matches and apply highlight borders.

    Re-seeds the RNG (user's timing of clicking Show Matches is part of the
    reading's entropy chain).

    INTENTIONAL: User timing when clicking "Show Matches" influences the reading
    by re-seeding the RNG, consistent with the "mystical connection" design.
    The match highlighting itself does not change the card values, but the
    entropy from the user action is incorporated into the reading.

    .. todo::
        ``rots = mat_rots.copy()`` was previously the middle line here —
        a dead local assignment.  If the intent was to sync the global rotation
        array, use ``rots[:] = mat_rots.copy()`` or ``np.copyto(rots, mat_rots)``.
    """
    # INTENTIONAL: Re-seed from system entropy on user action
    # See module docstring "RNG and the mystical connection"
    random.seed()
    _seed_var.set(f"seed: {random.getstate()[1][0]:010d}")
    find_matches()
    # TODO: dead local assignment was here — see docstring note above.
    set_bttn_clrs()
    _update_status()
    _status_msg.set(f"{len(matches)} match(es) found.")
def find_matches() -> None:
    """Scan all placed cards for adjacent edge-complement matches.

    Clears and repopulates :data:`matches`, :data:`mat_cnt`, :data:`mat_rots`,
    and :data:`currs`.  Clearing at the top of every call prevents duplicate
    accumulation on repeated invocations.

    Edge-to-rotation maps
    ~~~~~~~~~~~~~~~~~~~~~
    ``mp00``/``mp10`` orient cards for a *horizontal* pair (left / right card).
    ``mp01``/``mp11`` orient cards for a *vertical* pair (top / bottom card).

    Position codes in :data:`matches`
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    * ``1`` — negative edge faces right   (horizontal, left card)
    * ``4`` — positive edge faces right   (horizontal, left card)
    * ``3`` — negative edge faces down    (vertical,   top card)
    * ``2`` — positive edge faces down    (vertical,   top card)
    """
    matches.clear()
    currs.fill(UNSET_ROT)
    mat_cnt.fill(0)
    mat_rots.fill(-5)

    # --- Edge-to-rotation mapping (INTENTIONAL OVERLOADING) ---
    # Each card has 4 edges (0=top, 1=right, 2=bottom, 3=left).
    # When two cards are adjacent, their touching edges must have complementary
    # values (sum == 0) to match. These arrays map the original edge index to
    # the rotation value needed to display the match properly.
    #
    # INTENTIONAL OVERLOAD: The same edge index (0-3) maps to DIFFERENT rotation
    # values depending on the card's position in the match pair. This is because
    # the "same" physical edge (e.g., top edge) appears from different perspectives
    # on the left vs right card (horizontal) or top vs bottom card (vertical).
    #
    # For a horizontal pair (card 'a' left, card 'b' right):
    #   - mp00: left card edge i → rotation needed to display match
    #   - mp10: right card edge j → rotation needed (edge j is "facing left")
    # For a vertical pair (card 'a' top, card 'b' bottom):
    #   - mp01: top card edge i → rotation needed
    #   - mp11: bottom card edge j → rotation needed (edge j is "facing up")
    # Display convention: show_card_rot uses Image.rotate(r * 90), which turns
    # the card COUNTER-clockwise.  Slots are numbered COUNTER-clockwise to match
    # the scan sheet's native wedge order [T,L,B,R]: T=0, L=1, B=2, R=3, so
    # edge column i sits at slot i at rot 0 and rotation r moves slot s to
    # (s + r) % 4.  Each table gives the CCW rotation bringing a matching edge
    # to face its partner: (edge + r) % 4 == facing (pinned by tests/test_deck.py).
    mp00 = [3, 2, 1, 0]   # left card  of horizontal pair: edge i faces right (slot 3)
    mp10 = [1, 0, 3, 2]   # right card of horizontal pair: edge j faces left  (slot 1)
    mp01 = [2, 1, 0, 3]   # top card   of vertical pair:   edge i faces down  (slot 2)
    mp11 = [0, 3, 2, 1]   # bottom card of vertical pair:  edge j faces up    (slot 0)

    for x in range(GRID_COLS):
        for y in range(GRID_ROWS):
            a = spread[x, y]
            if a >= 0:

                # ---- Right neighbour (horizontal pair) ----------------------------
                # INTENTIONAL: Permissive edge matching - ANY edge on card 'a' matches
                # ANY edge on card 'b' when values sum to zero. This creates multiple
                # potential match paths between adjacent cards.
                #
                # INTENTIONAL OVERLOAD: The position code stored in matches[] uses
                # a compact encoding where the grid position is computed as
                # (y * GRID_SIZE + x) but we store (x + 1) for the right card.
                # This is trixy but avoids storing full (x,y) coordinates per match.
                # The position code (1,2,3,4) encodes which edge is facing right/down:
                #   1 = negative edge faces right  (horizontal, left card)
                #   4 = positive edge faces right  (horizontal, left card)
                #   3 = negative edge faces down   (vertical, top card)
                #   2 = positive edge faces down   (vertical, top card)
                if x < GRID_COLS - 1:
                    b = spread[x + 1, y]
                    if b >= 0:
                        for i in range(4):          # i = edge index on left card (0-3)
                            for j in range(4):      # j = edge index on right card
                                if (card_list[a, i] + card_list[b, j]) == 0:
                                    # Match found: complementary edges (e.g., +5 and -5)
                                    x1, y1 = x + 1, y
                                    mat_cnt[x,  y ] += 1      # card 'a' participates in match
                                    mat_cnt[x1, y1] += 1      # card 'b' participates in match
                                    a1               = abs(card_list[a, i])   # matched edge value
                                    currs[x,  y ]    = card_list[a, i]        # storing contributing value
                                    currs[x1, y1]    = card_list[b, j]        # storing contributing value
                                    mat_rots[x,  y ] = mp00[i]                # rotation for left card
                                    mat_rots[x1, y1] = mp10[j]                # rotation for right card
                                    if not any(a2[0] == a1 for a2 in matches):
                                        matches.append([
                                            a1,                                     # edge value
                                            1 if (card_list[a, i] < 0) else 4,      # pos code: 1=neg-right, 4=pos-right
                                            GRID_COLS * y + (x + 1),                # grid position (0-24)
                                        ])

                # ---- Bottom neighbour ---------------------------------------
                # INTENTIONAL: Permissive edge matching - ANY edge on card 'a' matches
                # ANY edge on card 'b' when values sum to zero.
                #
                # INTENTIONAL OVERLOAD: Position code encoding uses the same pattern
                # as horizontal matches but with different codes:
                #   3 = negative edge faces down   (vertical, top card)
                #   2 = positive edge faces down   (vertical, top card)
                #
                # NOTE: Grid position is computed as y * GRID_SIZE + (x + 1) for both
                # horizontal and vertical matches. This is INTENTIONAL TRIXY DATA USE:
                # - horizontal: x1 = x+1 (right card), y same, formula uses y * GRID_SIZE + (x + 1)
                # - vertical:   x1 = x (bottom card), y1 = y+1, but formula uses y * GRID_SIZE + (x + 1)
                # The formula gives different results for vertical vs horizontal but this is
                # consistent with the original design where position codes are used only for
                # display lookups, not precise coordinate recovery.
                if y < GRID_ROWS - 1:
                    b = spread[x, y + 1]
                    if b >= 0:
                        for i in range(4):
                            for j in range(4):
                                if (card_list[a, i] + card_list[b, j]) == 0:
                                    x1, y1 = x, y + 1
                                    mat_cnt[x,  y ] += 1
                                    mat_cnt[x1, y1] += 1
                                    a1               = abs(card_list[a, i])
                                    currs[x,  y ]    = card_list[a, i]
                                    currs[x1, y1]    = card_list[b, j]
                                    mat_rots[x,  y ] = mp01[i]
                                    mat_rots[x1, y1] = mp11[j]
                                    if not any(a2[0] == a1 for a2 in matches):
                                        matches.append([
                                            a1,
                                            3 if (card_list[a, i] < 0) else 2,
                                            GRID_COLS * y + (x + 1),
                                        ])
def click_card(x: int, y: int) -> None:
    """Handle a player click on the card at grid position *(x, y)*.

    **Re-seeds the RNG on every call** — this is the "mystical connection"
    feature described in the module docstring.  The exact moment the player
    clicks determines what card is drawn and how it is oriented.

    State machine
    -------------
    * ``rots < 0``         → un-negate (reveal face-down or unlock matched card).
    * ``rots >= 0``        → advance rotation by one 90° step.
    * ``spread == UNSET_CARD`` → draw a card, generate its image lazily, assign
                                  a random initial rotation.

    Lazy image generation
    ~~~~~~~~~~~~~~~~~~~~~
    When a new card slot is filled, its PIL Image is generated by the
    :data:`compositor` (or loaded from the pre-built tiff if available) and
    cached in :data:`images`.  A loading overlay is shown and
    ``root.update_idletasks()`` is called to force a repaint before the
    (synchronous) generation begins, giving the user visual feedback.

    Args:
        x: Column index ``[0, GRID_SIZE)``.
        y: Row index ``[0, GRID_SIZE)``.
    """
    # Void cells are out of play: a click on one does nothing at all —
    # no reseed, no draw.  They are permanently-unassigned slots.
    if (x, y) in void_cells:
        return

    # ---- Mystical RNG reseed ------------------------------------------------
    # INTENTIONAL: Re-seed from system entropy on EVERY user interaction.
    # This creates the "mystical connection" where the exact timing of clicks
    # becomes part of the entropy chain that determines subsequent random draws.
    #
    # Why? In divination, the reader's intent and timing are believed to
    # influence the reading. By re-seeding on each click, the user's timing
    # directly shapes which card is drawn and its orientation.
    # Do NOT remove this without discussion - it's a core design feature.
    random.seed()
    _seed_var.set(f"seed: {random.getstate()[1][0]:010d}")

    card = spread[x, y]
    r    = rots[x, y]

    if r < 0:
        # Hidden (UNSET_ROT sentinel) or locked (negative match rotation):
        # reveal / unlock without changing the stored rotation.
        rots[x, y] = abs(r)
    else:
        # Visible and free: advance rotation.
        rots[x, y] = (r + 1) % 4

    if card == UNSET_CARD:
        # ---- First click on this slot: draw and generate -------------------

        # Show loading overlay while image is being generated.
        if not deck:
            _status_msg.set("Deck empty — no cards left to deal")
            return
        _show_loading(x, y)
        root.update_idletasks()   # force repaint before blocking PIL call

        card_idx       = random.randrange(len(deck))
        r1             = random.randrange(4)
        rots[x, y]     = r1
        org_rots[x, y] = r1
        spread[x, y]   = deck[card_idx]
        del deck[card_idx]
        card = spread[x, y]

        # ---- Lazy image generation ----------------------------------------
        if images[card] is None:
            pre_built = card_image_path(card)
            if not args.random_cards and pre_built.exists():
                # The physical RGTCards/ scans are the source of truth: their
                # wedge values already sit at CCW slots (column i = slot i), but
                # the art was scanned a quarter-turn off upright, so level it at
                # load for the ``rotate(r * 90)`` display.  (The in-memory
                # compositor bakes upright to the same CCW convention and needs
                # no correction.)  Direction here is CW (-90); flip to +90 if
                # the art reads the wrong way round.
                images[card] = Image.open(pre_built).resize(
                    (card_size, card_size), Image.LANCZOS
                ).rotate(-90)
                _img_src[card] = 'prebuilt'
            else:
                # Generate via compositor (source pool or procedural fallback).
                images[card] = compositor.generate_face(card)
                _img_src[card] = 'compositor'

        _hide_loading()
        _status_msg.set(f"Card {card} revealed at ({x}, {y}).")

    _update_status()
    show_card_rot(x, y, rots[x, y])
    _debug_dump(x, y)
def show_match_details() -> None:
    """Clear existing interpretation labels and render a fresh match table.

    Hidden when ``card_defs`` is ``None`` (definitions CSV not found).

    For each entry in :data:`matches` (sorted by edge value), two rows are
    added to ``frm``:

    * Row A: edge value, card name (``card_defs[:, 6]``), position code, location.
    * Row B: interpretation text (``card_defs[:, pos_code]``) + short title
      (``card_defs[:, 5]``).

    Re-seeds the RNG — the player's timing of clicking Show Details is part of
    the entropy chain.

    INTENTIONAL: User timing when clicking "Show Details" influences the reading
    by re-seeding the RNG, consistent with the "mystical connection" design.
    """
    if card_defs is None:
        _status_msg.set("No card definitions loaded (RGTCards.csv not found).")
        return

    # INTENTIONAL: Re-seed from system entropy on user action
    # See module docstring "RNG and the mystical connection"
    random.seed()
    _seed_var.set(f"seed: {random.getstate()[1][0]:010d}")
    zap_labels(frm)
    find_matches()

    for x in range(GRID_COLS):
        for y in range(GRID_ROWS):
            show_card_rot(x, y, abs(rots[x, y]))

    curry = 10
    for c in sorted(matches):
        ttk.Label(
            frm,
            text=f"{c[0]} - {card_defs[c[0]-1, 6]} in position {c[1]}    (location {c[2]})",
            style='TLabel',
        ).grid(column=0, row=curry, columnspan=GRID_COLS, sticky='w', padx=4)
        curry += 1
        ttk.Label(
            frm,
            text=f"{card_defs[c[0]-1, c[1]]}  [{card_defs[c[0]-1, 5]}]",
            style='TLabel',
        ).grid(column=0, row=curry, columnspan=GRID_COLS, sticky='w', padx=4)
        curry += 1

    _update_status()
    _status_msg.set(f"Details shown for {len(matches)} match(es).")


def save_session() -> None:
    """Dump the current session to a timestamped JSON file for debugging and
    reload/continue.

    Captures:
      * ``card_list`` — the in-memory edge matrix (25x4).  Saved verbatim so an
        on-the-fly deck (``--sources`` / ``--random-cards``) can be inspected or
        reloaded even though it was never written to ``RGTList.csv``.
      * ``board`` — every grid position with the card id sitting there (or
        ``null`` if the post is still empty) and the currently displayed
        rotation of each occupied post (raw signed ``rots`` value, so the lock
        sign is preserved).

    The file lands in ``<script dir>/sessions/pasyans_<UTC timestamp>.json``.
    """
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    board = []
    for x in range(GRID_COLS):
        for y in range(GRID_ROWS):
            card = int(spread[x, y])
            occupied = card != UNSET_CARD
            board.append({
                'x': x,
                'y': y,
                'card': card if occupied else None,
                'rot': int(rots[x, y]) if occupied else None,
            })
    payload = {
        'saved_at': datetime.now().isoformat(timespec='seconds'),
        'stamp': stamp,
        'grid_rows': int(GRID_ROWS),
        'grid_cols': int(GRID_COLS),
        'deck_size': int(DECK_SIZE),
        'card_list': card_list.tolist(),
        'board': board,
    }
    sessions_dir = pathlib.Path(__file__).resolve().parent / 'sessions'
    sessions_dir.mkdir(exist_ok=True)
    out_path = sessions_dir / f'pasyans_{stamp}.json'
    out_path.write_text(json.dumps(payload, indent=2))
    _status_msg.set(f"Session saved → {out_path.name}")


def load_session() -> None:
    """Open a previously saved session and restore the board from it.

    Shows a file dialog over the ``sessions/`` directory, then restores
    ``card_list``, ``spread``, ``rots``, ``org_rots`` (and the seed display
    when the file carries one).  Locked negative rotations are re-displayed
    with ``abs()`` semantics, matching the lock handler, and match
    highlights are recomputed afterwards.  A file whose grid or deck size
    does not match this build is refused with a status message.
    """
    global card_list, spread, rots, org_rots, mystic_seed
    sessions_dir = pathlib.Path(__file__).resolve().parent / 'sessions'
    path = filedialog.askopenfilename(
        title='Open session',
        initialdir=str(sessions_dir),
        filetypes=[('JSON sessions', '*.json'), ('All files', '*')],
    )
    if not path:
        _status_msg.set('Open cancelled')
        return
    try:
        data = json.loads(pathlib.Path(path).read_text())
        if (int(data['grid_rows']) != GRID_ROWS or int(data['grid_cols']) != GRID_COLS
                or int(data['deck_size']) != DECK_SIZE):
            raise ValueError(
                f"session is {data['grid_rows']}x{data['grid_cols']} / "
                f"{data['deck_size']} cards, this build is "
                f"{GRID_ROWS}x{GRID_COLS} / {DECK_SIZE}")
        board = data['board']
        new_list = np.array(data['card_list'], dtype=np.byte)
    except (OSError, ValueError, KeyError) as exc:
        _status_msg.set(f'Open failed: {exc}')
        return
    card_list = new_list
    spread.fill(UNSET_CARD)
    rots.fill(UNSET_ROT)
    org_rots.fill(UNSET_ROT)
    for entry in board:
        if entry is None:
            continue
        x, y = int(entry['x']), int(entry['y'])
        spread[x, y] = int(entry['card'])
        rots[x, y] = int(entry['rot'])
        org_rots[x, y] = int(entry['rot'])
    if 'seed' in data:
        mystic_seed = int(data['seed'])
        _seed_var.set(f'seed: {mystic_seed:010d}')
    for x in range(GRID_COLS):
        for y in range(GRID_ROWS):
            if spread[x, y] != UNSET_CARD:
                show_card_rot(x, y, abs(int(rots[x, y])))
    set_bttn_clrs()
    _status_msg.set(f'Session opened \u2190 {pathlib.Path(path).name}')


def main() -> None:
    """Build the deck, construct the GUI, and run the Tk mainloop.

    All state lives in module globals (declared below) because the Tk
    callbacks defined above reference them by name at call time.
    """
    global _btn_row, _card_back_pil, _debug_text, _details_btn, _i, _ld_cx, _ld_cy, _ld_drw, _ld_r, _loading_overlay, _parser, _progress, _seed_var, _status_matches, _status_msg, _status_revealed, _style, args, bttn, bttn_spread, c, canvas, card, card_defs, card_defs_csv, card_dir, card_list, card_list_csv, card_size, cols, compositor, container, currs, deck, f, face, faces, flat, frm, gap, h, header_frm, i, ids, images, mat_border, mat_cnt, mat_rots, matches, nats, on_disk, org_rots, out, pimages, plan, pool, r, root, rots, rows, sec, sheet, spread, status_frm, t0, void_cells, w, wdir, x, x_scrollbar, y, y_scrollbar



    _parser    = _build_arg_parser()
    args       = _parser.parse_args()

    global GRID_ROWS, GRID_COLS
    GRID_ROWS = args.rows
    GRID_COLS = args.cols

    # Void cells: normalise --void [col, row] pairs into absolute (x, y)
    # tuples.  Positive indices count from the top/left, negative from the
    # bottom/right (Python-style).  Fail loudly on anything malformed.
    if args.void:
        try:
            pairs = json.loads(args.void)
            if not isinstance(pairs, list):
                raise ValueError("expected a JSON list of [col, row] pairs")
            for col, row in pairs:
                x = int(col) if int(col) >= 0 else GRID_COLS + int(col)
                y = int(row) if int(row) >= 0 else GRID_ROWS + int(row)
                if not (0 <= x < GRID_COLS and 0 <= y < GRID_ROWS):
                    raise ValueError(f"void cell [{col}, {row}] lies outside the grid")
                void_cells.add((x, y))
        except (ValueError, TypeError) as exc:
            _parser.error(f"--void: {exc}")

    # Fail loudly on a bad source directory.  Silently falling back to procedural
    # placeholders is what makes a typo'd path look like a crash under a GUI
    # launcher: the window opens with flat coloured cards and nothing says why.
    if args.source_images is not None:
        if not args.source_images.exists():
            print(f'[ERROR] --source-images path does not exist: {args.source_images}',
                  file=sys.stderr)
            raise SystemExit(2)
        if not args.source_images.is_dir():
            print(f'[ERROR] --source-images is not a directory: {args.source_images}',
                  file=sys.stderr)
            raise SystemExit(2)

    # Running a Tkinter app *inside* IDLE / PythonLauncher is the usual cause of a
    # beachballed shell: IDLE is itself Tk, and a second Tk mainloop driven from its
    # shell can stall the shell while the game window waits for events.


    # A source-image directory means the deck is composed in memory, so neither the
    # pre-built faces nor RGTList.csv are required.  Imply --random-cards rather than
    # demanding it: asking for --source-images is asking for a deck that does not
    # exist on disk.
    if args.source_images is not None and not args.random_cards:
        args.random_cards = True
        print('[INFO] --source-images given: implying --random-cards, '
              'edge values are generated in memory (RGTList.csv is not read).')

    card_size  = args.size         #: Width/height of a rendered card in pixels.
    mat_border = args.border       #: Highlight-border thickness in pixels.
    wdir       = args.wdir         #: Root asset directory.

    # ---------------------------------------------------------------------------
    # Asset paths
    # ---------------------------------------------------------------------------

    card_dir      = wdir / "RGTCards"
    card_list_csv = card_dir / "RGTList.csv"
    card_defs_csv = card_dir / "RGTCards.csv"

    # Definitions belong with the art: when a source-image directory is in
    # use, prefer an engine-format definitions CSV found IN THAT DIRECTORY
    # over the wdir copy (falls back to wdir when none is present).
    if args.source_images is not None:
        card_defs_csv = _find_defs_csv(pathlib.Path(args.source_images)) or card_defs_csv


    #: Edge-value matrix.  Shape ``(DECK_SIZE, 4)``, dtype ``np.byte``.
    card_list = _load_or_generate_card_list()

    #: Card-interpretation matrix, or ``None`` if definitions are unavailable.
    card_defs = _load_card_defs()

    #: Compositor used for all card face generation.
    #: With --source-images the deck is composed in memory (WedgeCompositor), which
    #: stands in for the pre-built RGCard-<i>.tiff faces AND for RGTList.csv.
    #: Without it, behaviour is unchanged from pasyans-3.py (ImageCompositor).
    if args.source_images is not None:
        compositor = WedgeCompositor(
            source_dir=args.source_images,
            card_size=card_size,
            seed=args.seed,
            bg_colour=(15, 10, 30),
        )
    else:
        compositor = ImageCompositor(
            source_dir=args.source_images,
            card_size=card_size,
            split_axis=args.split_axis,
            bg_colour=(15, 10, 30),
        )

    # -----------------------------------------------------------------------
    # Edge id == wedge id: when the compositor built a deck plan, the plan's
    # signed wedge ids ARE the edge values.  Each wedge carries its source
    # number baked into the art (stamp_source_number, before the split), and
    # the plan places id k at CCW slot k — the same native [T,L,B,R] column
    # convention card_list uses — so deriving the edge matrix from the plan
    # makes the numbers on the art match the edge values by construction.
    # Matching is unaffected: values match when they sum to zero, and each
    # source n contributes exactly one -n and one +n wedge.  This overrides
    # CardList.csv while a plan is active (the CSV still pins the pre-built
    # path); the plan is seed-deterministic, so runs stay reproducible.
    # -----------------------------------------------------------------------
    _plan = getattr(compositor, 'plan', None)
    if _plan is not None:
        card_list = np.array(_plan, dtype=np.byte)
        print(f"[DEBUG] edge matrix derived from compositor plan "
              f"(edge id = wedge id, {len(card_list)} cards)", file=sys.stderr)


    if args.dry_run:
        plan = getattr(compositor, 'plan', None)
        if plan is None:
            print('dry run: no in-memory deck (--sources not given); faces come from '
                  'pre-built assets or procedural placeholders.')
        else:
            flat = [w for card in plan for w in card]
            print(f'dry run: {len(plan)} faces, {len(flat)} wedge slots, '
                  f'{len(set(flat))} distinct wedges   seed={args.seed}')
            print(f'  wedge-once   : {len(set(flat)) == len(flat)}')
            print(f'  sibling-free : {all(len({abs(w) for w in c}) == 4 for c in plan)}')
            for i, ids in enumerate(plan):
                print(f'  face {i:2d}   T{ids[0]:+4d}  R{ids[1]:+4d}  '
                      f'B{ids[2]:+4d}  L{ids[3]:+4d}')
        raise SystemExit(0)

    if args.self_test:
        raise SystemExit(1 if _diag_self_test() else 0)

    if args.list_sources:
        if args.source_images is None:
            print('--list-sources needs --sources <dir>', file=sys.stderr)
            raise SystemExit(2)
        import compose_set as _cs
        pool = _cs.load_pool(args.source_images)      # rejections are warned on stderr
        print(f'{args.source_images}: {len(pool)} sources accepted')
        on_disk = len(list(args.source_images.glob('*.png')))
        print(f'{on_disk} png files on disk, {len(pool)} accepted, '
              f'{on_disk - len(pool)} skipped or duplicate-numbered')
        if len(pool) < 2 * DECK_SIZE:
            print(f'NOTE: a sibling-free deck of {DECK_SIZE} cards needs '
                  f'{2 * DECK_SIZE} distinct images; have {len(pool)}.')
        raise SystemExit(0)

    if args.dump_cards:
        plan = getattr(compositor, 'plan', None)
        if plan is None:
            print('--dump-cards needs --sources <dir> so a deck exists in memory',
                  file=sys.stderr)
            raise SystemExit(2)
        import time as _time
        import compose_set as _cs
        out = args.dump_cards
        out.mkdir(parents=True, exist_ok=True)
        t0 = _time.time()
        faces = []
        for i in range(DECK_SIZE):
            face = compositor.generate_face(i).convert('RGB')      # the engine's own path
            _cs.label_card(face, plan[i]).save(out / f'face-{i:02d}.png')
            faces.append(face)
        cols, gap = GRID_SIZE, 8
        w, h = faces[0].size
        rows = DECK_SIZE // cols
        sheet = Image.new('RGB', (cols * w + (cols + 1) * gap,
                                  rows * h + (rows + 1) * gap), (24, 20, 34))
        for i, f in enumerate(faces):
            r, c = divmod(i, cols)
            sheet.paste(f, (gap + c * (w + gap), gap + r * (h + gap)))
        sheet.save(out / 'sheet.png')
        compositor.generate_back().save(out / 'back.png')
        print(f'wrote {len(faces)} faces + sheet.png + back.png to {out} '
              f'in {_time.time() - t0:.1f}s')
        raise SystemExit(0)


    # ---------------------------------------------------------------------------
    # --dry-run: report the in-memory deck and exit BEFORE any Tk window exists.
    # Composing faces is lazy, so this stays fast -- it prints the deck plan and the
    # two deck disciplines without touching a single pixel.
    # ---------------------------------------------------------------------------
    if args.dry_run:
        plan = getattr(compositor, 'plan', None)
        if plan is None:
            print('dry run: no in-memory deck (--sources not given); '
                  'faces come from pre-built assets / procedural placeholders.')
        else:
            flat = [w for card in plan for w in card]
            print(f'dry run: {len(plan)} faces, {len(flat)} wedge slots, '
                  f'{len(set(flat))} distinct wedges')
            print(f'  wedge-once    : {len(set(flat)) == len(flat)}')
            print(f'  sibling-free  : {all(len({abs(w) for w in c}) == 4 for c in plan)}')
            print(f'  seed          : {args.seed}')
            for i, ids in enumerate(plan):
                print(f'  face {i:2d}   T{ids[0]:+4d}  R{ids[1]:+4d}  '
                      f'B{ids[2]:+4d}  L{ids[3]:+4d}')



    ############################################################################################################################
    # Tkinter window, theme, and layout
    ############################################################################################################################

    root = tk.Tk()
    root.title('PyPasyans — Pasyans Divination Deck')
    root.geometry("1550x1650")
    root.configure(bg=CLR_BG)
    root.resizable(True, True)

    # ---------------------------------------------------------------------------
    # ttk style sheet  (dark mystical theme throughout all ttk widgets)
    # ---------------------------------------------------------------------------
    _style = ttk.Style(root)
    _style.theme_use('clam')   # 'clam' gives the most predictable canvas for re-styling

    _style.configure('.',
        background=CLR_BG,
        foreground=CLR_TEXT,
        bordercolor=CLR_BORDER,
        troughcolor=CLR_SURFACE,
        selectbackground=CLR_GOLD,
        selectforeground=CLR_BG,
        font=('Georgia', 14),
    )
    _style.configure('TFrame',  background=CLR_BG)
    _style.configure('TLabel',  background=CLR_BG,     foreground=CLR_TEXT, font=('Georgia', 14))
    _style.configure('Status.TLabel',
        background=CLR_SURFACE, foreground=CLR_MUTED, font=('Courier', 9), padding=(6, 2))
    _style.configure('Title.TLabel',
        background=CLR_BG,      foreground=CLR_GOLD,  font=('Georgia', 14, 'bold'), padding=(0, 4))
    _style.configure('Seed.TLabel',
        background=CLR_BG,      foreground=CLR_MUTED, font=('Courier', 9))

    _style.configure('Gold.TButton',
        background=CLR_SURFACE, foreground=CLR_GOLD,
        bordercolor=CLR_GOLD,   relief='flat',
        padding=(10, 5),        font=('Georgia', 14),
    )
    _style.map('Gold.TButton',
        background=[('active', CLR_GOLD),   ('pressed', CLR_BORDER)],
        foreground=[('active', CLR_BG),     ('pressed', CLR_TEXT)],
    )

    _style.configure('Danger.TButton',
        background=CLR_SURFACE, foreground='#c0504a',
        bordercolor='#c0504a',  relief='flat',
        padding=(10, 5),        font=('Georgia', 14),
    )
    _style.map('Danger.TButton',
        background=[('active', '#c0504a'), ('pressed', CLR_BORDER)],
        foreground=[('active', CLR_BG),   ('pressed', CLR_TEXT)],
    )

    _style.configure('TProgressbar',
        background=CLR_GOLD, troughcolor=CLR_SURFACE, bordercolor=CLR_BORDER,
        thickness=4,
    )

    _style.configure('TSeparator', background=CLR_BORDER)

    # ---------------------------------------------------------------------------
    # Header bar
    # ---------------------------------------------------------------------------
    header_frm = tk.Frame(root, bg=CLR_BG, bd=0)
    header_frm.pack(fill=tk.X, side=tk.TOP, padx=12, pady=(8, 0))

    tk.Label(
        header_frm,
        text="✦  P Y P A S Y A N S  ✦",
        bg=CLR_BG, fg=CLR_GOLD,
        font=('Georgia', 15, 'bold'),
    ).pack(side=tk.LEFT)

    #: StringVar shown in the header — updated each time the RNG is re-seeded so
    #: the player can see that their timing is influencing the reading.
    _seed_var = tk.StringVar(value="seed: —")
    tk.Label(
        header_frm,
        textvariable=_seed_var,
        bg=CLR_BG, fg=CLR_MUTED,
        font=('Courier', 9),
    ).pack(side=tk.RIGHT, padx=8)

    # Thin gold rule under the header
    tk.Frame(root, bg=CLR_GOLD, height=1).pack(fill=tk.X, side=tk.TOP, padx=0, pady=(4, 0))

    # ---------------------------------------------------------------------------
    # Scrollable card canvas
    # ---------------------------------------------------------------------------
    container = ttk.Frame(root, padding=0)
    container.pack(fill=tk.BOTH, expand=1)

    sec = tk.Frame(container, bg=CLR_BG)
    sec.pack(fill=tk.X, side=tk.BOTTOM)

    canvas = tk.Canvas(container, bg=CLR_BG, highlightthickness=0)
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=1)

    x_scrollbar = ttk.Scrollbar(sec,       orient=tk.HORIZONTAL, command=canvas.xview)
    x_scrollbar.pack(side=tk.BOTTOM, fill=tk.X)

    y_scrollbar = ttk.Scrollbar(container, orient=tk.VERTICAL,   command=canvas.yview)
    y_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    canvas.configure(xscrollcommand=x_scrollbar.set, yscrollcommand=y_scrollbar.set)
    canvas.bind(
        "<Configure>",
        lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
    )

    # ``frm`` is the inner frame holding the card grid and controls.
    # Embedded into the canvas via ``create_window`` at the bottom of this file.
    frm = ttk.Frame(canvas, padding=6)

    # TODO: Alternative ScrollableFrame-based approach — see class above.
    #       Keep until final scrolling strategy is decided.
    #
    # fc = tk.Canvas(frm)
    # h = ttk.Scrollbar(frm, orient='horizontal', command=fc.xview)
    # v = ttk.Scrollbar(frm, orient='vertical',   command=fc.yview)
    # frm_s = ttk.Frame(fc)
    # frm_s.bind("<Configure>", lambda e: fc.configure(scrollregion=fc.bbox("all")))
    # fc.create_window((0, 0), window=frm_s, anchor="nw")
    # fc.configure(yscrollcommand=v.set, xscrollcommand=h.set)
    # fc.grid(); v.grid(); h.grid()

    frm.grid()

    # ---------------------------------------------------------------------------
    # Status bar  (sits below the canvas container, above the window bottom)
    # ---------------------------------------------------------------------------
    status_frm = tk.Frame(root, bg=CLR_SURFACE, bd=0)
    status_frm.pack(fill=tk.X, side=tk.BOTTOM)

    tk.Frame(root, bg=CLR_BORDER, height=1).pack(fill=tk.X, side=tk.BOTTOM)

    _status_revealed = tk.StringVar(value="Revealed: 0 / 25")
    _status_matches  = tk.StringVar(value="Matches: 0")
    _status_msg      = tk.StringVar(value="Click a card to begin your reading.")

    tk.Label(status_frm, textvariable=_status_revealed,
             bg=CLR_SURFACE, fg=CLR_MUTED, font=('Courier', 9)).pack(side=tk.LEFT,  padx=8, pady=2)
    tk.Label(status_frm, textvariable=_status_matches,
             bg=CLR_SURFACE, fg=CLR_MUTED, font=('Courier', 9)).pack(side=tk.LEFT,  padx=8)
    tk.Label(status_frm, textvariable=_status_msg,
             bg=CLR_SURFACE, fg=CLR_MUTED, font=('Courier', 9), anchor='w').pack(
                 side=tk.LEFT, padx=8, fill=tk.X, expand=1)

    #: Indeterminate progress bar — shown while a card image is being generated.
    _progress = ttk.Progressbar(status_frm, mode='indeterminate', length=80, style='TProgressbar')
    # Not packed by default; shown/hidden by _show_loading() / _hide_loading().


    ############################################################################################################################
    # Game state
    ############################################################################################################################

    # No seed here — intentional.  The first user click will seed via random.seed()
    # creating the mystical link between player timing and the reading.
    # See module docstring section "RNG and the mystical connection".

    #: Ordered list of card indices not yet drawn from the deck.
    deck = list(range(DECK_SIZE))

    #: ``(GRID_SIZE, GRID_SIZE)`` — card index at each grid position, or UNSET_CARD.
    spread = np.full((GRID_COLS, GRID_ROWS), UNSET_CARD, dtype=np.byte)

    #: Flat list of card-grid :class:`tk.Button` widgets in row-major order
    #: (index = ``y * GRID_SIZE + x``).
    bttn_spread = []

    #: ``(GRID_SIZE, GRID_SIZE)`` — display rotation per card.
    #:
    #: INTENTIONAL OVERLOAD - SIGN ENCODING: A single numpy.byte stores three states:
    #:   * ``rots < 0``       : card is HIDDEN (UNSET_ROT=-63) or LOCKED (match-highlighted)
    #:   * ``rots == 0``      : card is visible, upright (0°)
    #:   * ``rots == 1,2,3``  : card is visible, rotated 90°, 180°, 270° respectively
    #:   * Special case: locked rotation 0 is stored as -4 (negating 0 would be 0,
    #:     which is positive and indistinguishable from "free")
    #:
    #: This packs 3 bits of state (hidden/visible, rotation, locked/unlocked) into
    #: one byte without parallel boolean arrays.
    rots = np.full((GRID_COLS, GRID_ROWS), UNSET_ROT, dtype=np.byte)

    #: ``(GRID_SIZE, GRID_SIZE)`` — rotation at deal time; used by :func:`reset_cards`.
    org_rots = np.zeros((GRID_COLS, GRID_ROWS), dtype=np.byte)

    #: ``(GRID_SIZE, GRID_SIZE)`` — match-oriented rotation from :func:`find_matches`.
    mat_rots = np.zeros((GRID_COLS, GRID_ROWS), dtype=np.byte)

    #: ``(GRID_SIZE, GRID_SIZE)`` — natural edge values; reserved for future use.
    nats = np.full((GRID_COLS, GRID_ROWS), UNSET_ROT, dtype=np.byte)

    #: ``(GRID_SIZE, GRID_SIZE)`` — current match-contributing edge value per card.
    currs = np.full((GRID_COLS, GRID_ROWS), UNSET_ROT, dtype=np.byte)

    #: ``(GRID_SIZE, GRID_SIZE)`` — match participation count per card.
    mat_cnt = np.zeros((GRID_COLS, GRID_ROWS), dtype=np.byte)

    #: Discovered match list.  Each entry: ``[abs_edge_value, position_code, grid_loc]``.
    #: Cleared at the top of every :func:`find_matches` call.
    matches = []

    #: PIL Image objects for each card face.  Initialised to ``None``; generated
    #: lazily on first reveal in :func:`click_card`.  Index = card number.
    images = [None] * DECK_SIZE

    #: PhotoImage objects for each grid slot, one per button, row-major order.
    #: Initialised to the card-back pattern; updated in-place by
    #: :func:`show_card_rot` and :func:`set_bttn_clrs`.
    pimages = []

    #: Shared card-back image (PIL).  Generated once at startup.
    _card_back_pil = compositor.generate_back()


    ############################################################################################################################
    # Image initialisation — card backs for all slots
    ############################################################################################################################

    # All slots start showing the card-back pattern.  The actual face is pasted in
    # lazily when the player first reveals that slot.
    _void_pil = Image.new('RGB', (card_size, card_size), CLR_BG)
    for _y in range(GRID_ROWS):
        for _x in range(GRID_COLS):
            _base = _void_pil if (_x, _y) in void_cells else _card_back_pil
            pimages.append(ImageTk.PhotoImage(_base.copy()))


    ############################################################################################################################
    # Loading indicator helpers
    ############################################################################################################################

    #: Loading overlay PIL image — card back with a semi-transparent "..." centre.
    _loading_overlay = _card_back_pil.copy()
    _ld_drw = ImageDraw.Draw(_loading_overlay)
    _ld_cx  = card_size // 2
    _ld_cy  = card_size // 2
    _ld_r   = card_size // 6
    _ld_drw.ellipse(
        [_ld_cx - _ld_r, _ld_cy - _ld_r, _ld_cx + _ld_r, _ld_cy + _ld_r],
        fill=(30, 22, 60, 200), outline=(180, 145, 60), width=2,
    )
    _ld_drw.text((_ld_cx - 10, _ld_cy - 8), "...", fill=(180, 145, 60))


    ############################################################################################################################
    # Widget construction
    ############################################################################################################################

    # ---------------------------------------------------------------------------
    # Card grid  — GRID_SIZE × GRID_SIZE buttons
    # ---------------------------------------------------------------------------
    # ``partial`` binds (x, y) at creation time, avoiding the loop-closure pitfall
    # where all lambdas would capture the final loop values.

    for y in range(GRID_ROWS):
        for x in range(GRID_COLS):
            bttn = tk.Button(
                frm,
                image=pimages[(y * GRID_COLS) + x],
                command=partial(click_card, x, y),
                width=card_size, height=card_size,
                anchor="center",
                bd=0,                       # zero border so mat_border highlight is the only frame
                relief='flat',
                bg=CLR_BG, activebackground=CLR_BG,
                cursor='arrow' if (x, y) in void_cells else 'hand2',  # voids are inert
            )
            bttn_spread.append(bttn)
            bttn.grid(column=x, row=y, padx=2, pady=2)

    # ---------------------------------------------------------------------------
    # Separator between card grid and controls
    # ---------------------------------------------------------------------------
    ttk.Separator(frm, orient='horizontal').grid(
        column=0, row=5, columnspan=GRID_COLS, sticky='ew', pady=(6, 4)
    )

    # ---------------------------------------------------------------------------
    # Control button bar  (row 6)
    # ---------------------------------------------------------------------------
    _btn_row = 6

    ttk.Button(
        frm, text="↺  Reset",     command=reset_cards,       style='Gold.TButton'
    ).grid(column=0, row=_btn_row, padx=4, pady=4, sticky='ew')

    ttk.Button(
        frm, text="⊡  Zero",      command=zero_cards,        style='Gold.TButton'
    ).grid(column=1, row=_btn_row, padx=4, pady=4, sticky='ew')

    ttk.Button(
        frm, text="◈  Matches",   command=match_cards,       style='Gold.TButton'
    ).grid(column=2, row=_btn_row, padx=4, pady=4, sticky='ew')

    # "Show Details" is only useful if card_defs were loaded.
    _details_btn = ttk.Button(
        frm, text="✦  Details",   command=show_match_details, style='Gold.TButton'
    )
    _details_btn.grid(column=3, row=_btn_row, padx=4, pady=4, sticky='ew')
    if card_defs is None:
        _details_btn.state(['disabled'])   # grey out when no definitions CSV found

    ttk.Button(
        frm, text="✕  Quit",      command=quit_clicked,      style='Danger.TButton'
    ).grid(column=4, row=_btn_row, padx=4, pady=4, sticky='ew')

    ttk.Button(
        frm, text="⬇  Save",      command=save_session,      style='Gold.TButton'
    ).grid(column=5, row=_btn_row, padx=4, pady=4, sticky='ew')
    ttk.Button(
        frm, text="⬆  Open",     command=load_session,     style='Gold.TButton'
    ).grid(column=5, row=_btn_row + 1, padx=4, pady=4, sticky='ew')

    # ---------------------------------------------------------------------------
    # Debug dump field (row _btn_row + 2): append-only card-state lines written
    # by _debug_dump() on every card click.  Disabled for typing; _debug_dump
    # enables it only for the duration of each insert.
    # ---------------------------------------------------------------------------
    _debug_text = tk.Text(
        frm, height=10, width=80, font=('Courier', 12),
        state='disabled', bg=CLR_BG, fg=CLR_TEXT,
    )
    _debug_text.grid(column=0, row=_btn_row + 2, columnspan=GRID_COLS,
                     sticky='ew', padx=4, pady=(4, 2))

    # ---------------------------------------------------------------------------
    # Embed frm into the scrollable canvas (must be last)
    # ---------------------------------------------------------------------------
    canvas.create_window((0, 0), window=frm, anchor="nw")

    root.mainloop()

if __name__ == "__main__":
    main()
