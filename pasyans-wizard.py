"""pasyans-wizard.py -- graphical launcher for pasyans-4.py.

A Tkinter front-end that builds the ``pasyans-4.py`` command line from form
controls (dropdowns first, prompted text as a validated fallback) and launches
the engine as a **child process**.

Why a child process and not ``runpy`` (as ``pasyans-4-sources.py`` does): the
engine opens its own Tk ``mainloop``.  Running a second Tk mainloop inside this
wizard's own mainloop is precisely the beachball problem ``--idle-safe`` exists
to dodge, so the wizard hands off via ``subprocess`` instead.

NOTE: deliberately NO shebang line -- PythonLauncher quotes a shebang like
``#!/usr/bin/env python3`` as a single path token and fails.  With no shebang it
uses its default interpreter, same as pasyans-4.py.  Run from a shell with
``python3 pasyans-wizard.py``.

Directory dropdowns
-------------------
* ``--sources`` candidates are CWD sub-directories that carry a ``RGTCards*.csv``
  art/definitions sheet (e.g. ``LoremIpsoCards/RGTCards_Lorem.csv``).  This is
  the "directories with a RGTCards...csv" filter.
* ``--wdir`` candidates are CWD sub-directories (plus the CWD itself) that
  contain ``RGTCards/RGTList.csv`` -- the edge matrix the engine hard-requires
  (it exits 1 without it unless ``--random-cards``/``--sources`` stand in).

Every option is validated before launch; the assembled command is previewed so
you can see exactly what will run.
"""

from __future__ import annotations

import csv
import pathlib
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable, Optional

# The engine this wizard drives, resolved next to this file (same convention as
# pasyans-4-sources.py).
_HERE = pathlib.Path(__file__).resolve().parent
ENGINE = _HERE / "pasyans-4.py"

# Sensible dropdown presets; every numeric option also offers "Other..." which
# prompts for and validates a free value.
SIZE_PRESETS = [150, 200, 300, 400, 600, 800]
BORDER_PRESETS = [0, 2, 3, 5, 8, 10]
EDGE_RANGE_PRESETS = [4, 6, 8, 10, 12, 16, 20]
# Board dimensions are independent of the deck: cards run out (cells > cards)
# or go unused (cards > cells).  Default 5x5 matches the engine.
GRID_DIM_PRESETS = [3, 4, 5, 6, 7, 8]
SPLIT_AXES = ["random", "h", "v"]

# Run modes are mutually exclusive in the engine (they are checked in precedence
# order and each exits before the game window opens), so the wizard exposes them
# as a single radio group rather than stackable checkboxes.
MODES = ["play", "dry-run", "self-test", "list-sources", "dump-cards"]

_OTHER = "Other\u2026"  # "Other…" sentinel used in numeric comboboxes


# ---------------------------------------------------------------------------
# Directory discovery (pure, testable)
# ---------------------------------------------------------------------------
def find_source_dirs(root: pathlib.Path) -> list[str]:
    """Sub-directories of *root* that carry a ``RGTCards*.csv`` definitions sheet.

    These are valid ``--sources``/``--source-images`` candidates: a directory of
    numbered card art that also ships its own ``RGTCards*.csv``.

    Args:
        root: Directory to scan (non-recursive, top level only).

    Returns:
        Sorted relative paths (as strings) of matching sub-directories.
    """
    out: list[str] = []
    if not root.is_dir():
        return out
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith((".", "__")):
            continue
        if list(child.glob("RGTCards*.csv")):
            out.append(child.name)
    return out


def find_wdir_roots(root: pathlib.Path) -> list[str]:
    """Directories usable as ``--wdir``: those containing ``RGTCards/RGTList.csv``.

    The engine reads ``<wdir>/RGTCards/RGTList.csv`` and exits 1 if it is absent
    (unless ``--random-cards``/``--sources`` supply the deck instead).  The scan
    root itself is included when it qualifies.

    Args:
        root: Directory to scan (non-recursive, top level only).

    Returns:
        Sorted relative paths (as strings); ``"."`` for the scan root itself.
    """
    out: list[str] = []
    if not root.is_dir():
        return out
    if (root / "RGTCards" / "RGTList.csv").is_file():
        out.append(".")
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith((".", "__")):
            continue
        if (child / "RGTCards" / "RGTList.csv").is_file():
            out.append(child.name)
    return out


def find_output_dirs(root: pathlib.Path) -> list[str]:
    """Existing sub-directories offered as ``--dump-cards`` targets.

    Args:
        root: Directory to scan (non-recursive, top level only).

    Returns:
        Sorted relative paths (as strings) of sub-directories.
    """
    out: list[str] = []
    if not root.is_dir():
        return out
    for child in sorted(root.iterdir()):
        if child.is_dir() and not child.name.startswith((".", "__")):
            out.append(child.name)
    return out


def looks_like_sources_dir(path: pathlib.Path) -> bool:
    """True if *path* is a directory holding a ``RGTCards*.csv`` sheet."""
    return path.is_dir() and bool(list(path.glob("RGTCards*.csv")))


def looks_like_wdir(path: pathlib.Path) -> bool:
    """True if *path* is a directory holding ``RGTCards/RGTList.csv``."""
    return (path / "RGTCards" / "RGTList.csv").is_file()


# ---------------------------------------------------------------------------
# Command assembly (pure, testable)
# ---------------------------------------------------------------------------
class ConfigError(ValueError):
    """Raised when the collected wizard values cannot form a valid command."""


def build_argv(cfg: dict, cwd: pathlib.Path) -> list[str]:
    """Assemble the ``pasyans-4.py`` argv from wizard values.

    Args:
        cfg: Mapping of collected values.  Keys (all optional unless noted):
            ``mode`` (str, required, one of :data:`MODES`), ``size``, ``border``,
            ``edge_range`` (int), ``seed`` (int or None), ``split_axis`` (str),
            ``random_cards`` (bool), ``no_duplicates`` (bool), ``sources``
            (str path or None), ``wdir`` (str path or None), ``dump_cards``
            (str path or None).
        cwd: Current working directory, used to resolve relative directory
            values and to validate them.

    Returns:
        Argument list beginning with the engine path, ready for
        ``subprocess``.

    Raises:
        ConfigError: A value is missing or fails validation.
    """
    mode = cfg.get("mode")
    if mode not in MODES:
        raise ConfigError(f"Unknown run mode: {mode!r}")

    argv: list[str] = [str(ENGINE)]

    # --- numeric options (validated ints) --------------------------------
    size = cfg.get("size")
    if size is not None:
        _require_int(size, "Card size", lo=1, hi=4096)
        argv += ["--size", str(size)]

    border = cfg.get("border")
    if border is not None:
        _require_int(border, "Border", lo=0, hi=100)
        argv += ["--border", str(border)]

    # --- board dimensions (independent of the deck) ----------------------
    rows = cfg.get("rows")
    if rows is not None:
        _require_int(rows, "Grid rows", lo=1, hi=64)
        argv += ["--rows", str(rows)]
    cols = cfg.get("cols")
    if cols is not None:
        _require_int(cols, "Grid columns", lo=1, hi=64)
        argv += ["--cols", str(cols)]

    seed = cfg.get("seed")
    if seed not in (None, ""):
        _require_int(seed, "Seed", lo=None, hi=None)
        argv += ["--seed", str(seed)]

    # --- void cells & debug panel (pass-through; the engine validates) ----
    void = cfg.get("void")
    if void:
        argv += ["--void", str(void)]

    if cfg.get("debug"):
        argv += ["--debug"]

    # --- split axis (only meaningful without --sources, but harmless) ----
    split_axis = cfg.get("split_axis")
    if split_axis:
        if split_axis not in SPLIT_AXES:
            raise ConfigError(f"Invalid split axis: {split_axis!r}")
        argv += ["--split-axis", split_axis]

    # --- random deck options ---------------------------------------------
    random_cards = bool(cfg.get("random_cards"))
    if random_cards:
        argv.append("--random-cards")
        edge_range = cfg.get("edge_range")
        if edge_range is not None:
            _require_int(edge_range, "Edge range", lo=1, hi=1000)
            if cfg.get("no_duplicates") and int(edge_range) < 4:
                raise ConfigError(
                    "--no-duplicates needs --edge-range >= 4 (32 distinct edges)."
                )
            argv += ["--edge-range", str(edge_range)]

    if cfg.get("no_duplicates"):
        if not random_cards:
            raise ConfigError("--no-duplicates only applies with --random-cards.")
        argv.append("--no-duplicates")

    if cfg.get("match_once"):
        if not random_cards:
            raise ConfigError("--match-once only applies with --random-cards.")
        argv.append("--match-once")

    # --- directories (validated) -----------------------------------------
    sources = cfg.get("sources")
    if sources:
        src = (cwd / sources) if not pathlib.Path(sources).is_absolute() else pathlib.Path(sources)
        if not src.exists():
            raise ConfigError(f"Sources directory does not exist: {src}")
        if not looks_like_sources_dir(src):
            raise ConfigError(
                f"{src} has no RGTCards*.csv sheet; it is not a recognised "
                "source-images directory."
            )
        argv += ["--sources", str(src)]

    wdir = cfg.get("wdir")
    if wdir:
        wd = (cwd / wdir) if not pathlib.Path(wdir).is_absolute() else pathlib.Path(wdir)
        if not wd.is_dir():
            raise ConfigError(f"--wdir is not a directory: {wd}")
        if not looks_like_wdir(wd) and not (random_cards or sources):
            raise ConfigError(
                f"{wd} has no RGTCards/RGTList.csv. Pick a valid wdir, or enable "
                "--random-cards/--sources to build the deck without it."
            )
        argv += ["--wdir", str(wd)]

    # --- run mode --------------------------------------------------------
    if mode == "dry-run":
        argv.append("--dry-run")
    elif mode == "self-test":
        argv.append("--self-test")
    elif mode == "list-sources":
        if not sources:
            raise ConfigError("--list-sources needs a --sources directory.")
        argv.append("--list-sources")
    elif mode == "dump-cards":
        if not sources:
            raise ConfigError("--dump-cards needs a --sources directory.")
        dump = cfg.get("dump_cards")
        if not dump:
            raise ConfigError("Dump-cards mode needs an output directory.")
        out = (cwd / dump) if not pathlib.Path(dump).is_absolute() else pathlib.Path(dump)
        if out.exists() and not out.is_dir():
            raise ConfigError(f"Dump target exists but is not a directory: {out}")
        argv += ["--dump-cards", str(out)]

    return argv


def _require_int(value, label: str, lo: Optional[int], hi: Optional[int]) -> None:
    """Validate that *value* is an int (or int-like str) within optional bounds.

    Raises:
        ConfigError: Not an integer, or out of range.
    """
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{label} must be an integer, got {value!r}") from None
    if lo is not None and ivalue < lo:
        raise ConfigError(f"{label} must be >= {lo}, got {ivalue}")
    if hi is not None and ivalue > hi:
        raise ConfigError(f"{label} must be <= {hi}, got {ivalue}")


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
class _Choice(tk.StringVar):
    """A combobox value that also remembers a prompted 'Other...' entry.

    Holds the *displayed* value (a preset, a directory name, or the ``Other…``
    sentinel).  When the user picks ``Other…`` we prompt, validate, and store the
    real value in :attr:`custom`; :meth:`value` returns the real value.
    """

    def __init__(self, master=None, presets=(), *, prompt=None, validate=None):
        super().__init__(master)
        self.presets = list(presets)
        self._prompt = prompt            # callable() -> raw string or None
        self._validate = validate        # callable(raw) -> accepted value or raises
        self.custom = None               # accepted free value, or None
        if self.presets:
            self.set(self.presets[0])

    def value(self):
        """Return the effective value (custom if set, else the displayed preset)."""
        if self.get() == _OTHER and self.custom is not None:
            return self.custom
        return self.get()

    def on_selected(self, _event=None):
        """Combobox handler: when 'Other…' is chosen, prompt and validate."""
        if self.get() != _OTHER:
            self.custom = None
            return
        raw = self._prompt() if self._prompt else None
        if raw is None or raw == "":
            # Cancelled: revert to the first preset.
            self.set(self.presets[0] if self.presets else "")
            self.custom = None
            return
        try:
            self.custom = self._validate(raw) if self._validate else raw
        except ConfigError as exc:
            messagebox.showerror("Invalid value", str(exc))
            self.set(self.presets[0] if self.presets else "")
            self.custom = None


class WizardApp(tk.Tk):
    """Main wizard window: build a pasyans-4.py command and launch it."""

    def __init__(self, cwd: pathlib.Path):
        super().__init__()
        self.cwd = cwd
        self.title("Pasyans Wizard")
        self.resizable(False, False)

        self.var_mode = tk.StringVar(value="play")
        self.var_size = self._int_choice(SIZE_PRESETS, 1, 4096, "Card size (px)")
        self.var_border = self._int_choice(BORDER_PRESETS, 0, 100, "Border (px)")
        self.var_edge = self._int_choice(EDGE_RANGE_PRESETS, 1, 1000, "Edge range")
        self.var_rows = self._int_choice(GRID_DIM_PRESETS, 1, 64, "Grid rows")
        self.var_cols = self._int_choice(GRID_DIM_PRESETS, 1, 64, "Grid columns")
        self.var_seed = tk.StringVar(value="")
        self.var_void = tk.StringVar(value="")
        self.var_debug = tk.BooleanVar(value=False)
        self.var_split = tk.StringVar(value="random")
        self.var_random = tk.BooleanVar(value=False)
        self.var_nodup = tk.BooleanVar(value=False)
        self.var_matchonce = tk.BooleanVar(value=False)
        self.var_sources = self._dir_choice(find_source_dirs(cwd), "sources")
        self.var_wdir = self._dir_choice(find_wdir_roots(cwd), "wdir")
        self.var_dump = self._dir_choice(find_output_dirs(cwd), "dump")

        self._build()
        self._refresh_preview()

    # -- widget factories --------------------------------------------------
    def _int_choice(self, presets, lo, hi, label):
        var = _Choice(
            self, [str(p) for p in presets] + [_OTHER],
            prompt=lambda: simpledialog.askstring(label, f"{label} (integer):", parent=self),
            validate=lambda raw: str(_require_int_return(raw, label, lo, hi)),
        )
        return var

    def _dir_choice(self, options, kind):
        presets = options + [_OTHER]
        var = _Choice(
            self, presets,
            prompt=lambda: filedialog.askdirectory(
                title=f"Choose {kind} directory", parent=self, initialdir=str(self.cwd)),
            validate=lambda raw: raw,
        )
        if options:
            var.set(options[0])
        else:
            var.set(_OTHER)
        return var

    # -- layout ------------------------------------------------------------
    def _build(self):
        pad = {"padx": 8, "pady": 4}
        row = 0

        # Run mode
        frm = ttk.LabelFrame(self, text="Run mode")
        frm.grid(row=row, column=0, sticky="we", **pad)
        for i, mode in enumerate(MODES):
            ttk.Radiobutton(frm, text=mode, value=mode, variable=self.var_mode,
                            command=self._refresh_preview).grid(
                row=0, column=i, padx=6, pady=4)
        row += 1

        # Directories
        frm = ttk.LabelFrame(self, text="Directories")
        frm.grid(row=row, column=0, sticky="we", **pad)
        self._dir_row(frm, 0, "Sources (--sources)", self.var_sources)
        self._dir_row(frm, 1, "Asset root (--wdir)", self.var_wdir)
        self._dir_row(frm, 2, "Dump target (--dump-cards)", self.var_dump)
        row += 1

        # Deck options
        frm = ttk.LabelFrame(self, text="Deck")
        frm.grid(row=row, column=0, sticky="we", **pad)
        self._combo_row(frm, 0, "Card size", self.var_size)
        self._combo_row(frm, 1, "Border", self.var_border)
        self._combo_row(frm, 2, "Split axis", self.var_split, SPLIT_AXES)
        self._combo_row(frm, 3, "Edge range", self.var_edge)
        ttk.Label(frm, text="Seed").grid(row=4, column=0, sticky="e", padx=6, pady=2)
        ttk.Entry(frm, textvariable=self.var_seed, width=12).grid(
            row=4, column=1, sticky="w", pady=2)
        ttk.Checkbutton(frm, text="Random cards", variable=self.var_random,
                        command=self._refresh_preview).grid(row=5, column=1, sticky="w")
        ttk.Checkbutton(frm, text="No duplicates", variable=self.var_nodup,
                        command=self._refresh_preview).grid(row=6, column=1, sticky="w")
        ttk.Checkbutton(frm, text="Match once", variable=self.var_matchonce,
                        command=self._refresh_preview).grid(row=7, column=1, sticky="w")
        self._combo_row(frm, 8, "Grid rows", self.var_rows)
        self._combo_row(frm, 9, "Grid cols", self.var_cols)
        ttk.Label(frm, text="Void cells").grid(row=10, column=0, sticky="e", padx=6, pady=2)
        ttk.Entry(frm, textvariable=self.var_void, width=24).grid(
            row=10, column=1, sticky="w", pady=2)
        ttk.Checkbutton(frm, text="Show debug text", variable=self.var_debug).grid(
            row=11, column=1, sticky="w")
        row += 1

        # Preview + actions
        frm = ttk.LabelFrame(self, text="Command")
        frm.grid(row=row, column=0, sticky="we", **pad)
        self.preview = ttk.Label(frm, text="", wraplength=560, justify="left")
        self.preview.grid(row=0, column=0, sticky="we", padx=6, pady=4)
        row += 1

        btns = ttk.Frame(self)
        btns.grid(row=row, column=0, sticky="we", **pad)
        ttk.Button(btns, text="Launch", command=self._launch).pack(side="left", padx=4)
        ttk.Button(btns, text="Close", command=self.destroy).pack(side="left", padx=4)

    def _dir_row(self, parent, r, label, var):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="e", padx=6, pady=2)
        box = ttk.Combobox(parent, textvariable=var, state="readonly", width=28)
        box.grid(row=r, column=1, sticky="we", pady=2)
        box.bind("<<ComboboxSelected>>", lambda e, v=var: (v.on_selected(e), self._refresh_preview()))

    def _combo_row(self, parent, r, label, var, presets=None):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="e", padx=6, pady=2)
        values = presets if presets is not None else (list(var.presets))
        box = ttk.Combobox(parent, textvariable=var, state="readonly", values=values, width=12)
        box.grid(row=r, column=1, sticky="w", pady=2)
        if presets is None:  # numeric _Choice with Other… handling
            box.bind("<<ComboboxSelected>>", lambda e, v=var: (v.on_selected(e), self._refresh_preview()))
        else:
            box.bind("<<ComboboxSelected>>", lambda e: self._refresh_preview())

    # -- behaviour ---------------------------------------------------------
    def _collect(self) -> dict:
        seed = self.var_seed.get().strip()
        return {
            "mode": self.var_mode.get(),
            "size": int(self.var_size.value()),
            "border": int(self.var_border.value()),
            "edge_range": int(self.var_edge.value()),
            "rows": int(self.var_rows.value()),
            "cols": int(self.var_cols.value()),
            "seed": int(seed) if seed else None,
            "void": self.var_void.get().strip(),
            "debug": bool(self.var_debug.get()),
            "split_axis": self.var_split.get(),
            "random_cards": self.var_random.get(),
            "no_duplicates": self.var_nodup.get(),
            "match_once": self.var_matchonce.get(),
            "sources": self.var_sources.value(),
            "wdir": self.var_wdir.value(),
            "dump_cards": self.var_dump.value(),
        }

    def _refresh_preview(self):
        try:
            argv = build_argv(self._collect(), self.cwd)
            text = " ".join(argv)
        except (ConfigError, ValueError) as exc:
            text = f"\u26a0 {exc}"
        self.preview.config(text=text)

    def _launch(self):
        try:
            argv = build_argv(self._collect(), self.cwd)
        except (ConfigError, ValueError) as exc:
            messagebox.showerror("Cannot launch", str(exc))
            return
        try:
            # Run via the interpreter: pasyans-4.py has no execute bit and, by
            # design, no shebang, so exec'ing it directly raises EACCES.
            subprocess.Popen([sys.executable, *argv], cwd=str(self.cwd))
        except OSError as exc:
            messagebox.showerror("Launch failed", str(exc))
            return
        self.destroy()


def _require_int_return(value, label, lo, hi) -> int:
    """Validate an int and *return* it (build_argv's _require_int returns None)."""
    _require_int(value, label, lo, hi)
    return int(value)


def main():
    if not ENGINE.is_file():
        print(f"[ERROR] engine not found next to wizard: {ENGINE}", file=sys.stderr)
        raise SystemExit(1)
    app = WizardApp(pathlib.Path.cwd())
    app.mainloop()


if __name__ == "__main__":
    main()

