"""pasyans-4-sources.py -- launcher wrapper: pasyans-4.py with --sources baked in.

NOTE: deliberately NO shebang line -- PythonLauncher quotes a shebang like
``#!/usr/bin/env python3`` as a single path token and fails.  With no shebang
it uses its default interpreter, same as pasyans-4.py.  Run from a shell with
``python3 pasyans-4-sources.py``.

Why this exists: PythonLauncher prepends its "Script Arguments" field BEFORE
the script path, so the interpreter itself receives ``--source-images`` and
dies with ``unknown option --source-images`` before the engine ever starts.
Drag THIS file onto PythonLauncher instead -- it needs no Script Arguments.

Extra arguments still work (they override/extend the baked-in ones):
    python3 pasyans-4-sources.py --dry-run --seed 7
"""
import pathlib
import runpy
import sys

HERE = pathlib.Path(__file__).resolve().parent
ENGINE = HERE / 'pasyans-4.py'
DEFAULT_SOURCES = HERE / 'LoremIpsoCards'

# Baked-in args first; anything the caller passes comes after, so argparse
# last-wins lets them override (e.g. a different --sources or --seed).
sys.argv = [str(ENGINE), '--sources', str(DEFAULT_SOURCES)] + sys.argv[1:]

runpy.run_path(str(ENGINE), run_name='__main__')
