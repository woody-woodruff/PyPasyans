# Running pasyans-4.py — CLI, IDLE, Python Launcher, BBEdit

Facts below were checked on this machine (macOS, 2026-10-26). Anything I could not verify
is marked **[not verified]**.

---

## 0. Interpreter: pick the one that matches IDLE

```
/usr/local/bin/python3      3.12.3   <-- active; its idlelib lives in
                                          /Library/Frameworks/Python.framework/Versions/3.12
/usr/bin/python3            Apple system Python   [Tk not checked]
/opt/homebrew/bin/python3   Homebrew              [Tk not checked]
idle3 -> /usr/local/bin/idle3, /opt/homebrew/bin/idle3
Tk / Tcl: 8.6 / 8.6
```

Use `/usr/local/bin/python3` (3.12) so the CLI and `idle3` run the same interpreter and the
same `compose_set.py` behaviour. Mixing the Homebrew interpreter with the 3.12 IDLE is how
you get "works in one, ImportError in the other".

`sys.framework` is **not** set for the active interpreter, so the Tk/Aqua drag-and-drop
workarounds inside `pasyans-3/4.py` (which key off `sys.framework`) stay inactive here.

**Runtime dependency:** `pasyans-4.py` imports `compose_set.py` for the wedge geometry.
Both files must sit in the same directory, and you must launch from that directory as CWD,
or the default relative `--sources ../LoremIpsoCards` will not resolve.

---

## 1. CLI — the reliable path

```bash
cd /Users/woody_old/Desktop/CurrentDevProjects/RGFT_Spyder
python3 pasyans-4.py --sources /Users/woody_old/Desktop/CurrentDevProjects/LoremIpsoCards --seed 7
```

Options registered in the parser (verified against `--help`):

```
-h --help   -s/--size (300)   --seed (None)   -b/--border (5)   --wdir (.)
--random-cards   --no-duplicates   --edge-range (12)
--source-images | --sources (None)   --split-axis {h,v,random}
--dry-run   --self-test   --list-sources   --dump-cards DIR   --idle-safe
```

Behaviour worth knowing:

* `--sources` is an exact alias of `--source-images`. Passing `--sources` used to be a hard
  argparse error (exit 2) — that was the "PythonLauncher breaks instantly" symptom.
* Giving a source dir **implies `--random-cards`**: faces are composed in memory and
  `RGTList.csv` is not read. An `[INFO]` line says so at startup.
* A non-existent or non-directory source path exits 2 with an explicit `[ERROR]` instead of
  silently showing flat placeholder cards.
* Values that begin with `-` need the `=` form in the sibling tools
  (`--ids=-1,-2,3,4`); a bare `--ids -1,...` is parsed as a flag.

**No-window diagnostics.** All four of these run and exit *before* `tk.Tk()` is created, so
they are safe under IDLE and PythonLauncher, where a second Tk mainloop is the problem:

| Flag | What it does |
|---|---|
| `--dry-run` | prints the 25-face deck plan (slot ids per card) and exits |
| `--self-test` | 9 invariant checks, PASS/FAIL, exit 0/1 |
| `--list-sources` | reports accepted / rejected / duplicate-numbered sources and exits |
| `--dump-cards DIR` | composes all faces through the engine's own path + `sheet.png` + `back.png`, exits |

Measured costs: deck plan build 0.00 s; all 25 faces 3.6 s (145 ms/face); the source pool
holds ~50 images at 1024² (~210 MB). Faces are generated **lazily, one per card reveal** —
only the card back is composed at import.

---

## 2. IDLE

```bash
/usr/local/bin/idle3 /Users/woody_old/Desktop/CurrentDevProjects/RGFT_Spyder/pasyans-4.py
```

**Passing arguments:** `Run → Run Customized…`. The args box is parsed by `idlelib/query.py`
with `shlex.split(cli_string, posix=True)`, so type:

```
--sources /Users/woody_old/Desktop/CurrentDevProjects/LoremIpsoCards --seed 7
```

Consequences: a path without spaces needs no quoting; a path *with* spaces must be quoted;
an unbalanced quote pops an error dialog rather than mis-parsing; backslashes are escapes
(posix mode), so avoid Windows-style paths.

**Why the shell appears to hang.** `root.mainloop()` is at module level. Plain
`Run → Run Module (F5)` therefore blocks the IDLE shell until the game window closes — the
shell is not crashed, it is waiting. It is worse than a wait: IDLE is itself a Tk app, and a
second Tk mainloop driven from inside its own shell is the classic way to beachball it. The
script prints `[WARN] running under IDLE...` when it detects `idlelib` in `sys.modules`.

### The practical fix: `--idle-safe`

Hand the job to an independent process and return immediately:

```
--sources /Users/woody_old/Desktop/CurrentDevProjects/LoremIpsoCards --seed 7 --idle-safe
```

The parent re-execs the same script — with `--idle-safe` stripped from the child's argv so it
cannot recurse — prints the child pid, and exits before it touches the source pool or
`tk.Tk()`. Verified: parent exits 0, the child builds the deck, no orphaned processes remain.
Add `--dry-run` to exercise the hand-off without opening any window.

This deliberately does **not** try to make Tk non-blocking. Tkinter has no supported way to
return control of a Tk mainloop to an enclosing Tk app, and the usual `update()` re-entry
tricks cause exactly the re-entrancy class of bug this module already documents around
`after()`/`destroy()`. Separate processes, separate event loops.

Rules of thumb:
1. `F5` with no args = quick procedural smoke test; expect the shell to block until you close
   the window.
2. Real play from IDLE = add `--idle-safe`.
3. If the shell does stall, close the game window first — that returns control.
4. IDLE restarts the process on every run, so `compose_set`'s module-level geometry constants
   are rebuilt each time: no stale state, but no caching either (all 50 sources re-read).

---

## 3. Python Launcher

Present on this machine: `/Applications/Python 3.12/Python Launcher.app` (plus a 3.10 and a
MacPorts 3.8 copy — prefer 3.12 to match the interpreter above).

**Current obstacle:** `pasyans-4.py` has **no shebang** (line 1 is a docstring; copied from
`pasyans-3.py`, which has none either) and is not executable, so double-clicking uses the
Launcher's default "Python" filetype binding rather than an explicit interpreter. Usually
fine, but it is the kind of implicit choice that later presents as "wrong Python". To fix:

```python
#!/usr/local/bin/python3     # line 1, above the docstring
```
```bash
chmod +x pasyans-4.py
```

From the shell you can hand a file to the Launcher with arguments explicitly:

```bash
open -a "/Applications/Python 3.12/Python Launcher.app" \
     --args /Users/woody_old/Desktop/CurrentDevProjects/RGFT_Spyder/pasyans-4.py \
     --sources /Users/woody_old/Desktop/CurrentDevProjects/LoremIpsoCards
```

**[not verified]** the Launcher's own dialog labels — I could not drive its UI from here.

Caveat specific to the Launcher: it gives no console unless "Interact" is on, and
`WedgeCompositor` reports deck-validation problems to stdout/stderr. Without a console those
`[ERROR]`/`[WARN]` lines vanish, so a refused deck (too few numbered sources, impossible
sibling-free set) looks like a silent quit. Reproduce in the CLI to diagnose.

---

## 4. BBEdit

Verified present on this machine:

```
/Applications/BBEdit.app                     present
/usr/local/bin/bbedit                        present (CLI)
bbcmd                                        NOT on PATH
~/Library/Application Support/BBEdit         present
```

What BBEdit is good for here is **editing and inspection** — `pasyans-4.py`,
`compose_set.py`, `RGTCards.csv`, `RGTList.csv` and this guide in one window set:

```bash
bbedit pasyans-4.py compose_set.py RUNNING.md pasyans-card-spec.md
```

`bbedit` targets a *running* BBEdit instance; if BBEdit is not open it opens the files on
launch rather than returning control to the shell, so do not put it inside a scripted
pipeline expecting exit-status semantics.

`bbcmd` — the CLI that lets BBEdit drive command selection — is **not installed**, so there
is no `bbcmd`-based run path until you enable it in BBEdit's helper-tools preferences.

**[not verified]** I could not drive BBEdit's menus from here, so I am not naming its
Scripts-menu items. What I can say is that BBEdit's "run this Python file with arguments"
facility is not something I confirmed exists; treat the CLI (or `--idle-safe`) as the launch
mechanism and BBEdit as the editor.

Practical pattern that needs nothing unverified: edit in BBEdit, then run from its shell —
BBEdit ships a "Shell Shell" / shell-script execution facility, and the command below is the
same one the CLI section documents:

```bash
cd /Users/woody_old/Desktop/CurrentDevProjects/RGFT_Spyder && \
python3 pasyans-4.py --sources ../LoremIpsoCards --seed 7 --idle-safe
```

Because BBEdit launches it as an external process, the game runs in its own Tk mainloop and
nothing inside BBEdit is asked to host a second event loop — which is the same reasoning as
`--idle-safe`. For a no-window check from BBEdit, swap `--idle-safe` for `--self-test`.

---

## 5. Which tool for what

| Task | Use |
|---|---|
| Real play / real art | CLI |
| Real play from IDLE | IDLE `Run Customized` **with `--idle-safe`** |
| Editing, review, CSV inspection | BBEdit |
| Quick no-arg smoke test | IDLE F5 (shell blocks until window closes) |
| Deck-logic work, no window | `--self-test`, `--dry-run`, `--list-sources` |
| Eyeball all 25 faces at once | `--dump-cards /tmp/dump` |
| Unit-testing the compositor headlessly | `test_wedge_compositor.py` |
| Demoing by double-click | Python Launcher, after adding shebang + `chmod +x` |

---

## 6. Known rough edges

* **Pool is exactly saturated.** A sibling-free deck of 25 cards needs 50 distinct numbered
  images (100 wedges for 100 slots). There is zero slack: one missing or renumbered image
  makes a full deck impossible, and `--self-test` / `--list-sources` will say so.
* **Source 15 has three files** (`15-SerpantsVine`, `15-SerpantsVineB`, `15-SerpentVIneC`).
  The loader keeps the first and ignores two — decide whether those should be renumbered
  into 51/52 or are genuinely rejects.
* **Filename convention:** files must contain a number; `07-Name.png` and `src-07.png` are
  both accepted now, anything unnumbered is skipped.
* The card back is still composed at import, so a run does a little work before the window.
* In-game faces carry **no id labels** — `label_card()` is only used by `--dump-cards` and
  the export tooling. Say the word if you want labels in play for debugging.
* Whether the deck reads as a multi-view illusion (which view is upright) is still
  artist-unconfirmed; nothing in the code depends on it.
* `--idle-safe` is currently exercised only via `--idle-safe --dry-run` (child exits before
  any window). The full hand-off that opens a real window has not been run by me, since that
  needs a GUI session.
