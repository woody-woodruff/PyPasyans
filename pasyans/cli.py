"""
pasyans.cli -- command-line argument parser for the Pasyans engines.

Extracted verbatim from pasyans-4.py during the P2 module split.
"""
import argparse
import pathlib



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
        description="PyPasyans — Pasyans divination deck GUI.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # ---- display -------------------------------------------------------
    p.add_argument(
        "-s", "--size",
        help="Width and height of each card in pixels.",
        default=300, type=int,
    )
    p.add_argument(
        "--seed",
        help="Determinism for the in-memory wedge deck and edge values.",
        default=None, type=int,
    )
    p.add_argument(
        "--dry-run",
        help="Build the deck, print it, and exit before opening any window.",
        action="store_true", dest="dry_run",
    )
    p.add_argument(
        "--self-test",
        help="Check the geometry and deck-discipline invariants, print PASS/FAIL, "
             "exit 0/1. Runs before any window, so it works from IDLE.",
        action="store_true", dest="self_test",
    )
    p.add_argument(
        "--dump-cards",
        help="Compose every face into this directory (plus sheet.png) and exit, "
             "without opening the game window.",
        default=None, type=pathlib.Path, dest="dump_cards",
    )
    p.add_argument(
        "--list-sources",
        help="Report which source files the pool loader accepted or rejected, "
             "and exit.",
        action="store_true", dest="list_sources",
    )
    p.add_argument(
        "--idle-safe",
        help="Re-exec this script as an independent process and return "
             "immediately. Under IDLE a second Tk mainloop inside the shell can "
             "beachball it; this keeps the shell free. Combine with --dry-run to "
             "test the hand-off without opening a window.",
        action="store_true", dest="idle_safe",
    )

    p.add_argument(
        "--rows",
        help="Number of grid rows on the board.  Independent of the deck size; "
             "cards run out (cells > cards) or go unused (cards > cells).",
        default=5, type=int,
    )
    p.add_argument(
        "--cols",
        help="Number of grid columns on the board.  Independent of the deck size.",
        default=5, type=int,
    )
    p.add_argument(
        "-b", "--border",
        help="Highlight-border thickness in pixels for matched cards.",
        default=5, type=int,
    )
    p.add_argument(
        "--wdir",
        help="Root directory containing the RGTCards asset folder "
             "(default: the project directory this script lives in).",
        default=pathlib.Path(__file__).resolve().parent.parent, type=pathlib.Path,
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
        "--match-once",
        help="When --random-cards is set, build cards so any two cards share at "
             "most one edge magnitude (a pair can match at most once).  Applied "
             "best-effort: when the magnitude pool is too small to satisfy every "
             "pair, the least-overlapping deck is produced.  Requires "
             "--edge-range >= 4.",
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
        "--source-images", "--sources",
        help="Directory of source images used to composite card faces.  Each file "
             "must be numbered with LEADING digits (e.g. 07-SomeName.png); that "
             "number is its source id, and -n/+n pick its left/right half.  "
             "'--sources' is accepted as an alias.  Falls back to procedural "
             "placeholders if omitted or if too few numbered images are found.",
        default=None, type=pathlib.Path, dest="source_images",
    )
    p.add_argument(
        "--split-axis",
        help="Axis along which source images are split when compositing: "
             "'h' (horizontal), 'v' (vertical), 'random' (per card).",
        default="random", choices=["h", "v", "random"], dest="split_axis",
    )

    return p
