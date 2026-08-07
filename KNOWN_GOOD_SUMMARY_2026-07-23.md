# LUTier — Known Good Summary (2026-07-23)

Checkpoint reference for picking this back up later. This is the third disk
migration on this project (Dev 001 → remigration → this one, "v004") — the
first two ran out of space mid-session, root cause and fix noted below.
Full session-by-session history lives in `RUNNING.md`; this file is a
snapshot of where things stand right now, not a replacement for it.

## What this is

LUTier — a PySide6 desktop app for building and previewing LUT-based color
grades: load LUTs from a folder, stack operations (LUT / Printer Lights
trim / ACES DRT) non-destructively, preview against a sample image with a
wipe compare, inspect via histogram/waveform/vectorscope/3D viewer, save
named "Stack Memory" snapshots, save/load whole sessions (`.ltr`), export
the baked result to `.cube`/`.clf`/`.3dl`/`.cub`/`.spi3d`/`.csp`.

## Architecture

- **`stack.py`** — Qt-free core: `Stack` holds an ordered list of `Stage`
  objects (kind = `lut` / `printer_lights` / `aces_drt`), each independently
  enabled/opacity-blended, baked to one combined `LUT3D` on demand. This
  replaced four earlier single-purpose panels (Concatenate, ACES DRT,
  Retarget/Resize/Bounds/Printer-Lights) with one reorderable stack.
- **`app_logic.py`** — Qt-free backend for file I/O, image loading, LUT
  loading/baking, colorspace listing.
- **`main_window.py`** — top-level window, wires every panel together via
  nested `QSplitter`s so every seam is user-resizable.
- **Session format** — `.ltr` (JSON), via `session_io.py`. Stores the
  Stack, grid size/domain, sample image reference, and the Stack Memories
  library. Old `.lutier` files still open.

## Current panel layout

```
+----------+---------------------------+--------------+
| Browser  |                           |  Parameters  |
|  (top    |         Viewer            +--------------+
|   left)  |     (top center)          |    Scopes    |
+----------+---------------------------+ (incl. 3D    |
|  Stack   |    Stack -- the stage list |  Viewer,     |
| Memories |    (center, main area)    |  scope picker|
| (bottom  |                           |  in header)  |
|   left)  |                           |              |
+----------+---------------------------+--------------+
                Export current stack as: [...] [Export...]
```

Every panel is independently toggleable from the Workspace menu, all six
default visible on launch. Viewer, Stack, and Scopes each have a "..."
menu on their title row instead of a button row underneath (Viewer:
import/adapt/export; Stack: add stage/edit size-domain; Scopes: pick
which scope).

## This session's changes, roughly chronological

1. **v004 remigration** — previous session ran out of disk space mid-edit
   (found it stopped mid-panel-title-rename). Root cause: `lut_tool/dist/`
   + `build/` (py2app packaging output, ~2.15GB) sitting in the project
   folder — regenerable, not source. Deleted (had to be done from Finder;
   this sandbox's mount couldn't remove those specific files).
2. **Laundry list (6 items)** — Workspace-menu checkboxes replacing
   on-screen panel toggles; Browser/Viewer/Stack Params title cleanup
   (Browser heading removed, renamed to Viewer/Parameters); LUT inspector
   shows path + basic details instead of being blank; Sample Image →
   Viewer rename; stack list redesigned as bordered cards with a directly
   clickable enable toggle; drag-to-reorder stages + drag-a-LUT-in from
   Browser.
3. **Environment fixed** — this sandbox had no PySide6 at all originally.
   Installed the full `requirements.txt`, found a stub `libEGL.so.1`
   (`.eglstub/`, left over from an earlier session) that lets
   `QApplication` actually construct under `QT_QPA_PLATFORM=offscreen`.
   Everything since has been verified against real Qt objects (constructed
   `MainWindow`, driven real widgets, asserted on resulting state) instead
   of just `py_compile`.
4. **Polish batch** — Viewer's "..." menu moved onto the title row instead
   of its own row; fixed a real bug where dragging a stage into empty
   space deleted it (Qt's own `startDrag()` was double-removing the row);
   dark-mode QSS pass (`QToolTip`/`QComboBox` popups/`QMenu` were falling
   back to unstyled defaults once an app-wide stylesheet existed); Printer
   Lights now applies live, no Update Stage button; Stack's add-stage
   buttons consolidated into one header menu.
5. **Small notes** — color-management readout ("Color: Unmanaged" / "X →
   Y") now overlays the image itself (lower-right chip) instead of its own
   label row; wipe-usage hint line removed; Scopes' scope picker moved
   from a combo into the same header-menu pattern.
6. **Browser / Stack Memories split** — was one tabbed panel, now two
   separate panels (Browser top-left, Stack Memories bottom-left), each
   independently toggleable.
7. **Stack list housekeeping** — Move Up/Move Down/Remove button row
   removed (reordering was already drag-and-drop); each stage card now has
   its own "×" to delete regardless of selection, plus Delete/Backspace
   removes whatever's currently selected.

## Test status

- `python3 -m py_compile *.py` — clean.
- `python3 -m pytest -q` — **111 passed, 19 failed.** The 19 failures are
  all `PermissionError` on hardcoded `/tmp/test_*.png` paths owned by a
  different uid from an earlier sandbox instance — confirmed via `ls -la`
  and `rm` (operation not permitted), not a code issue. 111/111 matches
  the last fully-clean baseline from before this failure mode appeared.
- Every GUI change this session has been exercised against real
  `QApplication`/`MainWindow`/widget objects headless (see individual
  files' docstrings for the specific assertions made).

## Known limitations

- `QOpenGLWidget` doesn't work under the offscreen Qt platform — the 3D
  viewer and any actual pixel-rendering correctness are still unverified
  here; needs a real on-screen pass.
- Real mouse-driven drag-and-drop *feel* is unverified — the drag/drop
  tests call `dropEvent()`/the underlying callbacks directly (confirms the
  data-level logic and the Qt drop-action fix), not actual on-screen mouse
  ergonomics.
- This sandbox's disk is tight (~7.3GB free on a shared 229GB volume,
  mostly other stuff already there) — watch for `dist/`/`build/`
  reappearing if `python3 setup.py py2app` gets run here.

## Backlog (not started)

- **Retarget 4-field expansion** — currently only picks the color space to
  wrap a LUT's declared in/out as; Tom wants current-in/current-out/
  target-in/target-out eventually. TODO comment sits in
  `stack_panel.py`'s `_build_lut_inspector`.
- **ASC SOP / ASC SAT (CDL) support** — next feature under discussion, not
  scoped yet. Likely shape: a fourth `Stage` kind alongside
  `lut`/`printer_lights`/`aces_drt`, probably via OpenColorIO's
  `CDLTransform` (already a dependency) rather than hand-rolled math, with
  `.cc`/`.cdl`/`.ccc` import/export following `format_writers.py`'s
  pattern eventually.

## Files

**Active** (imported by `main_window.py`, directly or transitively):
`main_window.py`, `browser_panel.py`, `stack_memories_panel.py`,
`sample_image_panel.py`, `stack_panel.py`, `scopes_panel.py`,
`panel_frame.py`, `style.py`, `stack.py`, `stack_memory.py`,
`app_logic.py`, `session_io.py`, `clf_adapter.py`, `format_writers.py`,
`wipe_compare_widget.py`, `lut_viewer_3d.py`, `scopes.py`, `colorspace.py`,
`retarget.py`, `printer_lights.py`, `aces_drt.py`.

**Legacy / orphaned** — not imported anywhere, superseded by
`stack_panel.py` but still sitting in the folder: `concatenate_panel.py`,
`aces_drt_panel.py`, `lut_controls_panel.py`, `collapsible_section.py`
(only referenced from `setup.py`'s py2app include list). Candidates for
deletion if you want to tidy the repo further — flagging rather than
removing since that wasn't asked for.

**Tests**: 14 `test_*.py` files, 111 passing (see Test status above).

**Docs**: `BUILD_ON_MAC.md` (py2app packaging), `RUNNING.md` (full
session-by-session changelog, ~700 lines), this file.

**Environment**: `.eglstub/libEGL.so.1` + `LD_LIBRARY_PATH` pointed at it
is what makes PySide6 importable/runnable at all in a sandbox with no
display; `QT_QPA_PLATFORM=offscreen` for headless `QApplication`.
`requirements.txt` covers every dependency.
