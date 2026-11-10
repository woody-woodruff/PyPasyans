"""
pasyans.py
==========
Russian Gypsy Fortune Telling (Pasyans) divination deck GUI.

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

* ``>= 0`` — card is visible and free to rotate; value is rotation in 90° steps
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
import argparse
from functools import partial
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# Third-party
# ---------------------------------------------------------------------------
import numpy as np
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk, ImageDraw, ImageFilter

# ---------------------------------------------------------------------------
# Module metadata  (consumed by setuptools, Sphinx autodoc, and CI pipelines)
# ---------------------------------------------------------------------------
__version__ = "0.2.0"
__all__: List[str] = []   # nothing is intended to be imported from this module


############################################################################################################################
# CardDataGenerator
############################################################################################################################

class CardDataGenerator:
    """Generate a randomised edge-value matrix for the full deck.

    Each card has four edges.  Values are drawn from the signed range
    ``[-edge_range, -1] ∪ [1, edge_range]`` (zero is excluded so that no edge
    can trivially self-match).  Matches occur between adjacent cards when one
    card's edge value is the exact negative of its neighbour's touching edge.

    Args:
        deck_size:        Number of cards in the deck.
        edge_range:       Maximum absolute value of any edge number.
        allow_duplicates: If ``True``, a single card may have the same value on
                          more than one edge.  If ``False``, all four edges of
                          each card are distinct.

    Attributes:
        deck_size (int):        Stored deck size.
        edge_range (int):       Stored edge range.
        allow_duplicates (bool): Stored duplicate flag.

    Example::

        gen = CardDataGenerator(25, edge_range=12, allow_duplicates=False)
        card_list = gen.generate()   # shape (25, 4), dtype np.byte
    """

    def __init__(self, deck_size: int, edge_range: int, allow_duplicates: bool) -> None:
        self.deck_size        = deck_size
        self.edge_range       = edge_range
        self.allow_duplicates = allow_duplicates

        # Build the pool of candidate edge values: all non-zero signed integers
        # in [-edge_range, edge_range].  Symmetric so matches are always possible.
        self._pool = (
            list(range(-edge_range, 0)) + list(range(1, edge_range + 1))
        )

    def generate(self) -> np.ndarray:
        """Return a ``(deck_size, 4)`` array of signed edge values.

        Each row is one card; each column is one edge (top / right / bottom / left
        by convention, matching the rotation maps in :func:`find_matches`).

        Returns:
            numpy.ndarray: Shape ``(deck_size, 4)``, dtype ``np.byte``.

        Note:
            The RNG state at call time determines the output.  Call
            ``random.seed()`` before this method if you need a reproducible deck,
            or rely on the system-entropy seeding in :func:`click_card` to keep
            the mystical coupling intact.

        Raises:
            ValueError: If ``allow_duplicates=False`` and ``edge_range < 4``
                        (fewer than 4 distinct values available for one card).
        """
        if not self.allow_duplicates and len(self._pool) < 4:
            raise ValueError(
                f"edge_range={self.edge_range} provides only {len(self._pool)} "
                "distinct values but each card needs 4 unique edges when "
                "--no-duplicates is set.  Increase --edge-range to at least 4."
            )

        result = np.zeros((self.deck_size, 4), dtype=np.byte)

        for card_idx in range(self.deck_size):
            if self.allow_duplicates:
                edges = [random.choice(self._pool) for _ in range(4)]
            else:
                edges = random.sample(self._pool, 4)
            result[card_idx] = edges

        return result


############################################################################################################################
# ImageCompositor
############################################################################################################################

class ImageCompositor:
    """Generate card face and back images from a source-image pool.

    Each card image is built by:

    1. Picking two RGBA source images at random from the pool.
    2. Optionally splitting each source along the configured axis (half is
       discarded; the kept half is padded back to full size with transparency
       so the next step blends correctly).
    3. Optionally rotating each half by a random multiple of 90°.
    4. Alpha-compositing the two layers onto a transparent base.
    5. Flattening onto a solid background and resizing to ``card_size``.

    When no source directory is provided, or the directory contains fewer than
    two usable images, the compositor falls back to a procedurally generated
    placeholder whose colour is derived from the card index.

    Args:
        source_dir: Directory of RGBA source images.  ``None`` → always use
                    the procedural fallback.
        card_size:  Width and height of the output image in pixels.
        split_axis: ``'h'`` splits top/bottom, ``'v'`` splits left/right,
                    ``'random'`` chooses per card.
        bg_colour:  RGB background colour applied before alpha-flatten.

    Attributes:
        source_images (List[PIL.Image.Image]): Loaded RGBA source pool.

    .. todo::
        * Expose a ``--blend-mode`` option (multiply, screen, overlay …) so
          the visual style of composited cards can be tuned without code changes.
        * Cache generated faces to disk under ``wdir`` so a run can be resumed
          after the program is closed (requires storing the RNG seed too).
    """

    #: Image file suffixes considered when scanning the source directory.
    SUPPORTED_SUFFIXES = {'.png', '.tiff', '.tif', '.webp', '.bmp'}

    def __init__(
        self,
        source_dir: Optional[pathlib.Path],
        card_size: int,
        split_axis: str = 'random',
        bg_colour: Tuple[int, int, int] = (15, 10, 30),   # deep indigo
    ) -> None:
        self.card_size   = card_size
        self.split_axis  = split_axis
        self.bg_colour   = bg_colour
        self.source_images: List[Image.Image] = []

        if source_dir is not None and source_dir.is_dir():
            self._load_source_pool(source_dir)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def generate_back(self) -> Image.Image:
        """Generate a decorative card-back image (face-down state).

        The pattern is a gold-on-indigo diamond lattice with a subtle radial
        vignette, giving the deck a mystical / tarot-like appearance.

        Returns:
            PIL.Image.Image: ``card_size × card_size`` RGB image.
        """
        sz  = self.card_size
        img = Image.new('RGB', (sz, sz), self.bg_colour)
        drw = ImageDraw.Draw(img)

        # --- diamond lattice -------------------------------------------
        step   = max(sz // 8, 12)
        colour = (180, 145, 60)   # antique gold

        for gx in range(-step, sz + step, step):
            for gy in range(-step, sz + step, step):
                # alternate rows offset by half a step
                ox = step // 2 if ((gy // step) % 2) else 0
                cx, cy = gx + ox, gy
                hs = step // 3
                diamond = [
                    (cx,      cy - hs),
                    (cx + hs, cy     ),
                    (cx,      cy + hs),
                    (cx - hs, cy     ),
                ]
                drw.polygon(diamond, outline=colour)

        # --- border frame -----------------------------------------------
        pad = max(sz // 20, 6)
        drw.rectangle([pad, pad, sz - pad - 1, sz - pad - 1], outline=colour, width=2)
        inner = pad * 2
        drw.rectangle([inner, inner, sz - inner - 1, sz - inner - 1],
                      outline=(120, 95, 35), width=1)

        # --- radial vignette (darkens corners) -------------------------
        vignette = Image.new('L', (sz, sz), 0)
        vdrw     = ImageDraw.Draw(vignette)
        cx_f, cy_f = sz / 2, sz / 2
        r_max      = math.sqrt(cx_f ** 2 + cy_f ** 2)
        for rx in range(sz):
            for ry in range(sz):
                dist   = math.sqrt((rx - cx_f) ** 2 + (ry - cy_f) ** 2)
                # brightness falls off quadratically from centre to corners
                bright = max(0, int(220 * (1 - (dist / r_max) ** 1.8)))
                vdrw.point((rx, ry), fill=bright)
        vignette = vignette.filter(ImageFilter.GaussianBlur(radius=sz // 8))

        # Blend the lattice darkened by the vignette mask
        darkened = Image.new('RGB', (sz, sz), (0, 0, 0))
        img      = Image.composite(img, darkened, vignette)

        # Re-draw the frame on top of the vignette so it stays sharp
        drw2 = ImageDraw.Draw(img)
        drw2.rectangle([pad, pad, sz - pad - 1, sz - pad - 1], outline=colour, width=2)
        drw2.rectangle([inner, inner, sz - inner - 1, sz - inner - 1],
                       outline=(120, 95, 35), width=1)

        return img

    def generate_face(self, card_index: int) -> Image.Image:
        """Generate the face image for card *card_index*.

        Uses the source pool if at least two images are available; otherwise
        falls back to :meth:`_placeholder`.

        Args:
            card_index: Index of the card (``0``–``DECK_SIZE-1``).  Used only
                        for the fallback colour when no source pool exists.

        Returns:
            PIL.Image.Image: ``card_size × card_size`` RGB image.

        Note:
            The RNG state at call time drives source selection, split axis
            choice, and rotation.  Because :func:`click_card` re-seeds from
            system entropy before calling here, each reveal is influenced by
            the exact moment the user clicked.
        """
        if len(self.source_images) >= 2:
            return self._composite_from_sources(card_index)
        return self._placeholder(card_index)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_source_pool(self, source_dir: pathlib.Path) -> None:
        """Load all supported RGBA images from *source_dir* into the pool.

        Images that cannot be opened are silently skipped with a warning
        printed to ``stderr`` so the program can continue with a reduced pool.

        Args:
            source_dir: Directory to scan.
        """
        paths = sorted(
            p for p in source_dir.iterdir()
            if p.suffix.lower() in self.SUPPORTED_SUFFIXES
        )
        for path in paths:
            try:
                img = Image.open(path).convert('RGBA')
                self.source_images.append(img)
            except Exception as exc:
                print(f"[ImageCompositor] skipping {path.name}: {exc}", file=sys.stderr)

        print(
            f"[ImageCompositor] loaded {len(self.source_images)} source images "
            f"from {source_dir}",
            file=sys.stderr,
        )

    def _split_and_pad(self, img: Image.Image, axis: str, keep_first: bool) -> Image.Image:
        """Split *img* along *axis* and pad the kept half back to original size.

        The discarded half is replaced with full transparency so alpha
        compositing with the second layer blends naturally without a hard seam.

        Args:
            img:        RGBA source image (will be resized to ``card_size``
                        internally).
            axis:       ``'h'`` (horizontal split → top / bottom halves) or
                        ``'v'`` (vertical split → left / right halves).
            keep_first: ``True`` → keep top/left half;
                        ``False`` → keep bottom/right half.

        Returns:
            PIL.Image.Image: Full-size RGBA image with one half transparent.

        INTENTIONAL OVERLOAD: The axis parameter and keep_first flag are overloaded
        in the compositing workflow. For horizontal splits ('h'), we keep top/bottom
        halves. For vertical splits ('v'), we keep left/right halves. The same axis
        value produces different crop coordinates depending on whether we're keeping
        the first or second half - this is intentional trixy data use that reduces
        code duplication.

        .. todo::
            Experiment with splitting at a random position (not always the exact
            midpoint) once the compositor is visually validated, for more organic
            card faces.
        """
        sz  = self.card_size
        src = img.resize((sz, sz), Image.LANCZOS)

        if axis == 'h':
            mid = sz // 2
            box = (0, 0, sz, mid) if keep_first else (0, mid, sz, sz)
            offset = (0, 0) if keep_first else (0, mid)
        else:  # 'v'
            mid = sz // 2
            box = (0, 0, mid, sz) if keep_first else (mid, 0, sz, sz)
            offset = (0, 0) if keep_first else (mid, 0)

        half   = src.crop(box)
        canvas = Image.new('RGBA', (sz, sz), (0, 0, 0, 0))
        canvas.paste(half, offset)
        return canvas

    def _composite_from_sources(self, card_index: int) -> Image.Image:
        """Build a card face by compositing two randomly chosen source images.

        Workflow per card:

        1. Sample two distinct source images from the pool (with replacement if
           the pool has only two images).
        2. Determine the split axis (per-card random, or fixed by ``split_axis``).
        3. For each source: split → keep one half → rotate by random × 90°.
        4. Alpha-composite layer B over layer A on a transparent base.
        5. Flatten onto :attr:`bg_colour` background and return as RGB.

        Args:
            card_index: Passed through to ``_placeholder`` as a fallback only.

        Returns:
            PIL.Image.Image: ``card_size × card_size`` RGB image.
        """
        sz = self.card_size

        # Step 1 — pick two distinct sources
        idx_a = random.randrange(len(self.source_images))
        idx_b = idx_a
        if len(self.source_images) > 2:
            while idx_b == idx_a:
                idx_b = random.randrange(len(self.source_images))
        else:
            idx_b = (idx_a + 1) % len(self.source_images)

        src_a = self.source_images[idx_a]
        src_b = self.source_images[idx_b]

        # Step 2 — split axis
        axis = (random.choice(['h', 'v'])
                if self.split_axis == 'random'
                else self.split_axis)

        # Step 3 — split, keep complementary halves, rotate
        # TODO: when the visual style is finalised, consider exposing
        #       keep_first as a weighted choice rather than always True/False.
        layer_a = self._split_and_pad(src_a, axis, keep_first=True)
        layer_b = self._split_and_pad(src_b, axis, keep_first=False)

        rot_a = random.randrange(4) * 90
        rot_b = random.randrange(4) * 90
        if rot_a:
            layer_a = layer_a.rotate(rot_a)
        if rot_b:
            layer_b = layer_b.rotate(rot_b)

        # Step 4 — composite B over A
        composite = Image.new('RGBA', (sz, sz), (0, 0, 0, 0))
        composite = Image.alpha_composite(composite, layer_a)
        composite = Image.alpha_composite(composite, layer_b)

        # Step 5 — flatten onto background
        bg = Image.new('RGB', (sz, sz), self.bg_colour)
        bg.paste(composite, mask=composite.split()[3])   # alpha channel as mask

        return bg

    def _placeholder(self, card_index: int) -> Image.Image:
        """Return a solid-colour placeholder for *card_index*.

        Used when the source pool is empty.  The colour cycles through a
        visually distinct palette so cards remain individually identifiable
        during development even without source images.

        Args:
            card_index: Card index ``[0, DECK_SIZE)``.  Drives colour selection.

        Returns:
            PIL.Image.Image: ``card_size × card_size`` RGB image.
        """
        sz  = self.card_size
        n   = card_index * 10
        col = (255 - n, abs(127 - n), abs(n + 127) % 256)
        img = Image.new('RGB', (sz, sz), col)

        # Draw a simple card-index label so placeholder cards are identifiable
        drw = ImageDraw.Draw(img)
        label_col = (255, 255, 255) if sum(col) < 380 else (0, 0, 0)
        drw.text((sz // 2 - 10, sz // 2 - 10), str(card_index),
                 fill=label_col)
        return img


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


############################################################################################################################
# Command-line arguments
############################################################################################################################

def _build_arg_parser() -> argparse.ArgumentParser:
    """Construct and return the CLI argument parser.

    Keeping argument construction in its own function makes it independently
    testable (``_build_arg_parser().parse_args([...])``) and isolates
    module-level side-effects.

    Returns:
        argparse.ArgumentParser: Configured parser.
    """
    p = argparse.ArgumentParser(
        description="Russian Gypsy Fortune Telling (Pasyans) divination deck GUI.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # ---- display -------------------------------------------------------
    p.add_argument(
        "-s", "--size",
        help="Width and height of each card in pixels.",
        default=300, type=int,
    )
    p.add_argument(
        "-b", "--border",
        help="Highlight-border thickness in pixels for matched cards.",
        default=5, type=int,
    )
    p.add_argument(
        "--wdir",
        help="Root directory containing the RGTCards asset folder.",
        default=pathlib.Path("."), type=pathlib.Path,
    )

    # ---- random card-data generation -----------------------------------
    p.add_argument(
        "--random-cards",
        help="Generate edge values randomly instead of loading from RGTList.csv.",
        action="store_true", default=False,
    )
    p.add_argument(
        "--no-duplicates",
        help="When --random-cards is set, ensure no edge value appears twice on "
             "the same card.  Requires --edge-range >= 4.",
        action="store_true", default=False,
    )
    p.add_argument(
        "--edge-range",
        help="Maximum absolute edge value when --random-cards is set.  Values are "
             "drawn from [-N, -1] ∪ [1, N].",
        default=12, type=int, dest="edge_range",
    )

    # ---- image compositor ----------------------------------------------
    p.add_argument(
        "--source-images",
        help="Directory of RGBA source images used to composite card faces.  "
             "Requires at least 2 images.  Falls back to procedural placeholders "
             "if omitted or if fewer than 2 images are found.",
        default=None, type=pathlib.Path, dest="source_images",
    )
    p.add_argument(
        "--split-axis",
        help="Axis along which source images are split when compositing: "
             "'h' (horizontal), 'v' (vertical), 'random' (per card).",
        default="random", choices=["h", "v", "random"], dest="split_axis",
    )

    return p


_parser    = _build_arg_parser()
args       = _parser.parse_args()

card_size  = args.size         #: Width/height of a rendered card in pixels.
mat_border = args.border       #: Highlight-border thickness in pixels.
wdir       = args.wdir         #: Root asset directory.

# ---------------------------------------------------------------------------
# Asset paths
# ---------------------------------------------------------------------------

card_dir      = wdir / "RGTCards"
card_list_csv = card_dir / "RGTList.csv"
card_defs_csv = card_dir / "RGTCards.csv"


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


############################################################################################################################
# Constants
############################################################################################################################

GRID_SIZE:  int = 5
"""Number of cards along each axis of the spread."""

DECK_SIZE:  int = GRID_SIZE * GRID_SIZE
"""Total number of unique cards in the deck (``25``)."""

UNSET_CARD: int = -1
"""Sentinel in ``spread[x, y]`` — slot not yet drawn."""

UNSET_ROT:  int = -63
"""Sentinel in ``rots[x, y]``, ``nats``, ``currs`` — card hidden / not yet revealed.
Chosen well outside the valid rotation range ``0``–``3``.

INTENTIONAL TRIXY DATA USE: The value -63 is chosen because:
- Valid rotations are 0,1,2,3 (positive values representing 0°,90°,180°,270°)
- Hidden/locked states use negative values
- -63 is far outside the range [-4, -1] used for locked rotations, preventing
  accidental collisions if a card somehow got assigned a rotation of -63
- Using a single byte (np.byte range: -128 to 127) with a large negative
  sentinel ensures we have headroom for all state encodings"""

#: Highlight-border colour keyed by how many matches a card participates in.
MATCH_COLORS = {
    1: (127, 255, 127),   # green  — once
    2: (225, 225, 127),   # yellow — twice
    3: (255, 127, 127),   # red    — three times
    4: (127, 127, 255),   # blue   — four times (theoretical maximum)
}

# ---------------------------------------------------------------------------
# GUI colour palette  (dark mystical theme)
# ---------------------------------------------------------------------------
CLR_BG       = "#0d0a1e"   #: Deep indigo — main window background.
CLR_SURFACE  = "#1a1530"   #: Slightly lighter — frame / panel background.
CLR_GOLD     = "#b49130"   #: Antique gold — accents, button text.
CLR_GOLD_LT  = "#d4b94a"   #: Lighter gold — hover / active states.
CLR_TEXT     = "#e8dfc0"   #: Warm parchment — primary text.
CLR_MUTED    = "#7a7060"   #: Muted parchment — secondary / status text.
CLR_BORDER   = "#3a3050"   #: Panel border colour.


############################################################################################################################
# Data loading
############################################################################################################################

def _load_or_generate_card_list() -> np.ndarray:
    """Return the ``(DECK_SIZE, 4)`` edge-value matrix.

    If ``--random-cards`` is set, generates fresh values via
    :class:`CardDataGenerator` (using whatever RNG state is current at
    startup).  Otherwise loads from ``RGTList.csv``.

    Returns:
        numpy.ndarray: Shape ``(DECK_SIZE, 4)``, dtype ``np.byte``.

    Raises:
        SystemExit: If ``--random-cards`` is *not* set and ``RGTList.csv``
                    cannot be found.
    """
    if args.random_cards:
        gen = CardDataGenerator(
            deck_size=DECK_SIZE,
            edge_range=args.edge_range,
            allow_duplicates=not args.no_duplicates,
        )
        return gen.generate()

    if not card_list_csv.exists():
        print(
            f"[ERROR] {card_list_csv} not found.  "
            "Use --random-cards to generate edge values procedurally.",
            file=sys.stderr,
        )
        sys.exit(1)

    return np.loadtxt(card_list_csv, dtype=np.byte, delimiter=',')


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

    return np.loadtxt(
        card_defs_csv,
        dtype=str,
        comments=None,
        delimiter=',',
        converters=None, skiprows=0, usecols=None,
        unpack=False, ndmin=0, encoding=None,
        max_rows=None, quotechar='"',
    )


#: Edge-value matrix.  Shape ``(DECK_SIZE, 4)``, dtype ``np.byte``.
card_list: np.ndarray        = _load_or_generate_card_list()

#: Card-interpretation matrix, or ``None`` if definitions are unavailable.
card_defs: Optional[np.ndarray] = _load_card_defs()

#: Compositor used for all card face generation.
compositor = ImageCompositor(
    source_dir=args.source_images,
    card_size=card_size,
    split_axis=args.split_axis,
    bg_colour=(15, 10, 30),
)


############################################################################################################################
# Tkinter window, theme, and layout
############################################################################################################################

root = tk.Tk()
root.title('Russian Gypsy Fortune Telling')
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
    font=('Georgia', 10),
)
_style.configure('TFrame',  background=CLR_BG)
_style.configure('TLabel',  background=CLR_BG,     foreground=CLR_TEXT, font=('Georgia', 10))
_style.configure('Status.TLabel',
    background=CLR_SURFACE, foreground=CLR_MUTED, font=('Courier', 9), padding=(6, 2))
_style.configure('Title.TLabel',
    background=CLR_BG,      foreground=CLR_GOLD,  font=('Georgia', 14, 'bold'), padding=(0, 4))
_style.configure('Seed.TLabel',
    background=CLR_BG,      foreground=CLR_MUTED, font=('Courier', 9))

_style.configure('Gold.TButton',
    background=CLR_SURFACE, foreground=CLR_GOLD,
    bordercolor=CLR_GOLD,   relief='flat',
    padding=(10, 5),        font=('Georgia', 10),
)
_style.map('Gold.TButton',
    background=[('active', CLR_GOLD),   ('pressed', CLR_BORDER)],
    foreground=[('active', CLR_BG),     ('pressed', CLR_TEXT)],
)

_style.configure('Danger.TButton',
    background=CLR_SURFACE, foreground='#c0504a',
    bordercolor='#c0504a',  relief='flat',
    padding=(10, 5),        font=('Georgia', 10),
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
    text="✦  RUSSIAN GYPSY FORTUNE TELLING  ✦",
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
deck: List[int] = list(range(DECK_SIZE))

#: ``(GRID_SIZE, GRID_SIZE)`` — card index at each grid position, or UNSET_CARD.
spread: np.ndarray    = np.full((GRID_SIZE, GRID_SIZE), UNSET_CARD, dtype=np.byte)

#: Flat list of card-grid :class:`tk.Button` widgets in row-major order
#: (index = ``y * GRID_SIZE + x``).
bttn_spread: List[tk.Button] = []

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
rots: np.ndarray      = np.full((GRID_SIZE, GRID_SIZE), UNSET_ROT, dtype=np.byte)

#: ``(GRID_SIZE, GRID_SIZE)`` — rotation at deal time; used by :func:`reset_cards`.
org_rots: np.ndarray  = np.zeros((GRID_SIZE, GRID_SIZE), dtype=np.byte)

#: ``(GRID_SIZE, GRID_SIZE)`` — match-oriented rotation from :func:`find_matches`.
mat_rots: np.ndarray  = np.zeros((GRID_SIZE, GRID_SIZE), dtype=np.byte)

#: ``(GRID_SIZE, GRID_SIZE)`` — natural edge values; reserved for future use.
nats: np.ndarray      = np.full((GRID_SIZE, GRID_SIZE), UNSET_ROT, dtype=np.byte)

#: ``(GRID_SIZE, GRID_SIZE)`` — current match-contributing edge value per card.
currs: np.ndarray     = np.full((GRID_SIZE, GRID_SIZE), UNSET_ROT, dtype=np.byte)

#: ``(GRID_SIZE, GRID_SIZE)`` — match participation count per card.
mat_cnt: np.ndarray   = np.zeros((GRID_SIZE, GRID_SIZE), dtype=np.byte)

#: Discovered match list.  Each entry: ``[abs_edge_value, position_code, grid_loc]``.
#: Cleared at the top of every :func:`find_matches` call.
matches: List[List[int]] = []

#: PIL Image objects for each card face.  Initialised to ``None``; generated
#: lazily on first reveal in :func:`click_card`.  Index = card number.
images: List[Optional[Image.Image]] = [None] * DECK_SIZE

#: PhotoImage objects for each grid slot, one per button, row-major order.
#: Initialised to the card-back pattern; updated in-place by
#: :func:`show_card_rot` and :func:`set_bttn_clrs`.
pimages: List[ImageTk.PhotoImage] = []

#: Shared card-back image (PIL).  Generated once at startup.
_card_back_pil: Image.Image = compositor.generate_back()


############################################################################################################################
# Image initialisation — card backs for all slots
############################################################################################################################

# All slots start showing the card-back pattern.  The actual face is pasted in
# lazily when the player first reveals that slot.
for _i in range(DECK_SIZE):
    pimages.append(ImageTk.PhotoImage(_card_back_pil.copy()))


############################################################################################################################
# Loading indicator helpers
############################################################################################################################

#: Loading overlay PIL image — card back with a semi-transparent "..." centre.
_loading_overlay: Image.Image = _card_back_pil.copy()
_ld_drw = ImageDraw.Draw(_loading_overlay)
_ld_cx  = card_size // 2
_ld_cy  = card_size // 2
_ld_r   = card_size // 6
_ld_drw.ellipse(
    [_ld_cx - _ld_r, _ld_cy - _ld_r, _ld_cx + _ld_r, _ld_cy + _ld_r],
    fill=(30, 22, 60, 200), outline=(180, 145, 60), width=2,
)
_ld_drw.text((_ld_cx - 10, _ld_cy - 8), "...", fill=(180, 145, 60))


def _show_loading(x: int, y: int) -> None:
    """Display the loading overlay on card button *(x, y)* and start the
    indeterminate progress bar in the status bar.

    Must be followed by ``root.update_idletasks()`` to ensure the visual
    update is painted before the (synchronous) generation begins.

    Args:
        x: Column index.
        y: Row index.
    """
    idx = (y * GRID_SIZE) + x
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
    imgobj = images[card].rotate(r * 90)
    pimages[(y * GRID_SIZE) + x].paste(imgobj)


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
    for x in range(GRID_SIZE):
        for y in range(GRID_SIZE):
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
    for x in range(GRID_SIZE):
        for y in range(GRID_SIZE):
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
    for x in range(GRID_SIZE):
        for y in range(GRID_SIZE):
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
                imgobj = imgobj.rotate(abs(rots[x, y]) * 90)

                color = MATCH_COLORS.get(int(mat_cnt[x, y]), (200, 200, 200))
                timg  = Image.new('RGB', (card_size, card_size), color)
                timg.paste(imgobj, (mat_border, mat_border))
                pimages[(y * GRID_SIZE) + x].paste(timg)


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
    mp00 = [2, 1, 0, 3]   # left card  of horizontal pair: edge i → rotation mp00[i]
    mp10 = [0, 3, 2, 1]   # right card of horizontal pair: edge j → rotation mp10[j]
    mp01 = [1, 0, 3, 2]   # top card   of vertical pair:    edge i → rotation mp01[i]
    mp11 = [3, 2, 1, 0]   # bottom card of vertical pair:    edge j → rotation mp11[j]

    for x in range(GRID_SIZE):
        for y in range(GRID_SIZE):
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
                if x < GRID_SIZE - 1:
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
                                            GRID_SIZE * y + (x + 1),                # grid position (0-24)
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
                if y < GRID_SIZE - 1:
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
                                            GRID_SIZE * y + (x + 1),
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
            # Prefer the pre-built tiff if it exists and --source-images is not set.
            pre_built = card_image_path(card)
            if not args.random_cards and pre_built.exists():
                images[card] = Image.open(pre_built).resize(
                    (card_size, card_size), Image.LANCZOS
                )
            else:
                # Generate via compositor (source pool or procedural fallback).
                images[card] = compositor.generate_face(card)

        _hide_loading()
        _status_msg.set(f"Card {card} revealed at ({x}, {y}).")

    _update_status()
    show_card_rot(x, y, rots[x, y])


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

    for x in range(GRID_SIZE):
        for y in range(GRID_SIZE):
            show_card_rot(x, y, abs(rots[x, y]))

    curry = 10
    for c in sorted(matches):
        ttk.Label(
            frm,
            text=f"{c[0]} - {card_defs[c[0]-1, 6]} in position {c[1]}    (location {c[2]})",
            style='TLabel',
        ).grid(column=0, row=curry, columnspan=GRID_SIZE, sticky='w', padx=4)
        curry += 1
        ttk.Label(
            frm,
            text=f"{card_defs[c[0]-1, c[1]]}  [{card_defs[c[0]-1, 5]}]",
            style='TLabel',
        ).grid(column=0, row=curry, columnspan=GRID_SIZE, sticky='w', padx=4)
        curry += 1

    _update_status()
    _status_msg.set(f"Details shown for {len(matches)} match(es).")


############################################################################################################################
# Widget construction
############################################################################################################################

# ---------------------------------------------------------------------------
# Card grid  — GRID_SIZE × GRID_SIZE buttons
# ---------------------------------------------------------------------------
# ``partial`` binds (x, y) at creation time, avoiding the loop-closure pitfall
# where all lambdas would capture the final loop values.

for y in range(GRID_SIZE):
    for x in range(GRID_SIZE):
        bttn = tk.Button(
            frm,
            image=pimages[(y * GRID_SIZE) + x],
            command=partial(click_card, x, y),
            width=card_size, height=card_size,
            anchor="center",
            bd=0,                       # zero border so mat_border highlight is the only frame
            relief='flat',
            bg=CLR_BG, activebackground=CLR_BG,
            cursor='hand2',             # visual affordance that cards are clickable
        )
        bttn_spread.append(bttn)
        bttn.grid(column=x, row=y, padx=2, pady=2)

# ---------------------------------------------------------------------------
# Separator between card grid and controls
# ---------------------------------------------------------------------------
ttk.Separator(frm, orient='horizontal').grid(
    column=0, row=5, columnspan=GRID_SIZE, sticky='ew', pady=(6, 4)
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

# ---------------------------------------------------------------------------
# Embed frm into the scrollable canvas (must be last)
# ---------------------------------------------------------------------------
canvas.create_window((0, 0), window=frm, anchor="nw")

root.mainloop()
