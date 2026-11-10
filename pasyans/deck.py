"""
pasyans.deck -- Pasyans divination-deck engine.

Extracted verbatim from pasyans-4.py during the P2 module split.
Contains the deck constants plus the three composition classes
(CardDataGenerator, ImageCompositor, WedgeCompositor).
"""
import sys
import pathlib
import math
import random
from typing import List, Optional, Tuple
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

# ``compose_set`` lives in utils/ next to the package root.
_UTILS_DIR = pathlib.Path(__file__).resolve().parent.parent / "utils"
if str(_UTILS_DIR) not in sys.path:
    sys.path.insert(0, str(_UTILS_DIR))




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

    def __init__(self, deck_size: int, edge_range: int, allow_duplicates: bool,
                 match_once: bool = False) -> None:
        self.deck_size        = deck_size
        self.edge_range       = edge_range
        self.allow_duplicates = allow_duplicates
        # When True, cards are built so any two cards share at most one edge
        # magnitude, so a pair can match at most once (best-effort when the
        # magnitude pool is too small to satisfy every pair).
        self.match_once       = match_once

        # Build the pool of candidate edge values: all non-zero signed integers
        # in [-edge_range, edge_range].  Symmetric so matches are always possible.
        self._pool = (
            list(range(-edge_range, 0)) + list(range(1, edge_range + 1))
        )

    def generate(self) -> np.ndarray:
        """Return a ``(deck_size, 4)`` array of signed edge values.

        Each row is one card; each column is one edge (top / left / bottom / right
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

        if self.match_once:
            return self._generate_match_once()

        result = np.zeros((self.deck_size, 4), dtype=np.byte)

        for card_idx in range(self.deck_size):
            if self.allow_duplicates:
                edges = [random.choice(self._pool) for _ in range(4)]
            else:
                edges = random.sample(self._pool, 4)
            result[card_idx] = edges

        return result

    def _generate_match_once(self) -> np.ndarray:
        """Build a deck where any two cards share at most one edge magnitude.

        Two cards can only match on a magnitude they both carry (one signed +,
        the other -).  If every pair shares at most one magnitude, a pair can
        match at most once.  Each card uses four distinct magnitudes (one sign
        each), so a single card never carries both members of a +/- pair.

        The constraint is satisfied greedily.  When the magnitude pool is too
        small to honour it for every pair (few magnitudes, many cards), the
        least-overlapping candidate found is used instead, so a full deck is
        always produced ("complete as best we can").

        Returns:
            numpy int8 array of shape (deck_size, 4), columns [T, L, B, R].
        """
        mags = list(range(1, self.edge_range + 1))
        result = np.zeros((self.deck_size, 4), dtype=np.byte)
        used: list[set[int]] = []
        tries = 200   # attempts per card to find a <=1-overlap candidate

        for card_idx in range(self.deck_size):
            best: set[int] | None = None
            best_overlap = None
            for _ in range(tries):
                cand = set(random.sample(mags, 4))
                overlap = max((len(cand & c) for c in used), default=0)
                if overlap <= 1:
                    best = cand
                    break
                if best_overlap is None or overlap < best_overlap:
                    best, best_overlap = cand, overlap
            # best is always set: either an acceptable candidate or the
            # least-overlapping one seen (and with cards empty the first
            # candidate has overlap 0).
            used.append(best)
            edges = [random.choice((1, -1)) * m for m in best]
            random.shuffle(edges)
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
class WedgeCompositor:
    """In-memory four-wedge compositor.

    Replaces BOTH pre-built card faces (``RGCard-<i>.tiff``) and the
    ``RGTList.csv`` edge matrix: given a directory of numbered source images it
    builds the whole deck in memory at startup.

    Card-id convention (see pasyans-card-spec.md):
        ``-n`` -> LEFT  half of source image ``n``
        ``+n`` -> RIGHT half of source image ``n``
    Each face is four such wedges, placed in counter-clockwise slots
    top/left/bottom/right (plan id k lands at CCW slot k, the engine's
    convention — see the scan sheet's [T,L,B,R] wedge order).

    Deck discipline, enforced at construction and fatal if violated:
      * every wedge is used exactly once across the 25 faces;
      * no face carries both halves of the same source image.
    With 50 numbered sources that is exactly 100 wedges for 100 wedge slots, so
    a full deck consumes the pool precisely.

    Geometry lives in compose_set.py (single source of truth) -- this class only
    adapts it to the engine's ``generate_face`` / ``generate_back`` interface.
    """

    def __init__(self, source_dir, card_size: int, seed=None,
                 bg_colour=(15, 10, 30)) -> None:
        import compose_set as _cs

        self._cs        = _cs
        self.card_size  = card_size
        self.bg_colour  = bg_colour
        self.pool       = {}
        self.plan       = None
        self._back      = None

        #: kept only for parity with ImageCompositor's attributes
        self.source_images: List[pathlib.Path] = []

        if source_dir is None:
            print('[WARN] WedgeCompositor with no --source-images: '
                  'faces will be procedural placeholders.', file=sys.stderr)
            return

        try:
            self.pool = _cs.load_pool(pathlib.Path(source_dir))
        except _cs.DeckError as exc:
            print(f'[ERROR] {exc}', file=sys.stderr)
            raise SystemExit(1)
        if len(self.pool) < 4:
            print(f'[ERROR] need >= 4 numbered sources, found {len(self.pool)} '
                  f'in {source_dir}', file=sys.stderr)
            raise SystemExit(1)

        rng = random.Random(seed)
        try:
            self.plan = _cs.sample_set(rng, sorted(self.pool), DECK_SIZE)
        except _cs.DeckError as exc:
            print(f'[ERROR] cannot build a wedge deck: {exc}', file=sys.stderr)
            raise SystemExit(1)

        problems = _cs.validate_plan(self.plan)
        if problems:
            for p in problems:
                print(f'[ERROR] {p}', file=sys.stderr)
            raise SystemExit(1)

        self.source_images = [self.pool[n].path for n in sorted(self.pool)]
        print(f'Wedge deck built in memory: {len(self.plan)} faces from '
              f'{len(self.pool)} sources, every wedge used once.')

    # -- engine interface ----------------------------------------------------
    def card_ids(self, card_index: int) -> Optional[List[int]]:
        """The four signed card ids behind face *card_index* (debug/inspection)."""
        if not self.plan:
            return None
        return list(self.plan[card_index % len(self.plan)])

    def generate_face(self, card_index: int) -> Image.Image:
        """Compose face *card_index* on the fly (RGB, sized for the board)."""
        if not self.plan:
            return Image.new('RGB', (self.card_size, self.card_size), self.bg_colour[:3])
        ids  = self.plan[card_index % len(self.plan)]
        face = self._cs.compose_card(ids, self.pool).convert('RGB')
        if face.size != (self.card_size, self.card_size):
            face = face.resize((self.card_size, self.card_size), Image.LANCZOS)
        return face

    def generate_back(self) -> Image.Image:
        """Card back -- delegated to ImageCompositor with an empty source pool."""
        if self._back is None:
            self._back = ImageCompositor(None, self.card_size, 'random', self.bg_colour)
        return self._back.generate_back()
############################################################################################################################
# Constants
############################################################################################################################

GRID_SIZE:  int = 5
DECK_SIZE:  int = GRID_SIZE * GRID_SIZE
UNSET_CARD: int = -1
UNSET_ROT:  int = -63
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
