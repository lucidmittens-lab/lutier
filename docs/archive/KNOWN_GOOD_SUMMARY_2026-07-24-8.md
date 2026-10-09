# LUTier — Known Good Summary (2026-07-24, #8)

Checkpoint reference for picking this back up later, superseding
`KNOWN_GOOD_SUMMARY_2026-07-24-7.md` (kept alongside, not deleted). That
checkpoint shipped the QMainWindow/QDockWidget free-form-panel migration
but flagged six specific things as unverified on real hardware (dragging
to rearrange, tabbing, tearing off to float, whether the custom title bar
feels right to grab, whether floating chrome looks doubled-up, a real
preset round trip with a drag-built arrangement). Tom has now actually
used it: **"This is amazing. This works perfectly."** All six are
confirmed (RUNNING.md #41) -- this checkpoint is the new stable baseline,
with the docking migration fully retired from "risky, unverified change"
to "known good, working feature."

## What this is

LUTier — a PySide6 desktop app for building and previewing LUT-based
color grades: load LUTs/CDLs from a folder, stack operations (LUT,
Printer Lights trim, ASC CDL SOP/SAT, Color Space Conversion, ACES DRT,
Stack Forking) non-destructively, preview against a Sample/Reference
image pair with a wipe compare, inspect via histogram/waveform/
vectorscope/Graph (3D point cloud + wireframe cage + 1D YRGB curves),
save named "Stack Memory" snapshots, maintain reusable LUT/CDL/Image
libraries, undo/redo Stack edits, save/load whole sessions (`.ltr`),
export the baked result to `.cube`/`.clf`/`.3dl`/`.cub`/`.spi3d`/`.csp`
via a dedicated Export dialog, with every panel freely drag-to-rearrange/
tab/float (confirmed working end to end on real hardware this
checkpoint). Packaged as a native macOS `.app` via py2app, icon included.
In-app manual via Help > Manual, also on disk as `USER_MANUAL.md` (kept
byte-identical to `help_text.py`'s `USER_MANUAL_MARKDOWN`, re-verified
this checkpoint).

## What changed since #7 (RUNNING.md #41)

No code changes -- purely a confirmation entry. Real-hardware testing
retired every item #7's "Known limitations" flagged about the docking
migration itself: dragging a panel's title bar to rearrange, tabbing
panels together, tearing a panel off to float, the custom title bar's
feel, floating window chrome, and a real preset export/import round trip
all work as intended. The one item NOT explicitly called out as
individually re-tested is the GL-safety gap on the drag-to-float path
(#40's item 6, dragging the Graph panel specifically) -- Tom's blanket
"works perfectly" covers it implicitly (nothing crashed or rendered
broken), but if anything about the Graph panel's rendering looks off
specifically after a drag (vs. a menu/shortcut float), that's the one
thing worth a second look given the acknowledged, narrower gap in that
specific path (see RUNNING.md #40).

## Architecture

Unchanged from #7 -- see that file (and #6/#5/#4/#3 before it) for the
full rundown of `stack.py`, Stack Forking, per-operator backends, library
backends, `undo_history.py`, `sample_image_panel.py`, `session_io.py`
(still VERSION 7), `floating_dialog.py`, `workspace_io.py`, and the
QMainWindow/QDockWidget layout itself (`main_window.py`'s
`_build_default_dock_layout`/`_make_dock`/`_PanelDockWidget`/
`_on_dock_top_level_changed`).

## Test status

`python3 -m pytest -q` — **304 passed, 19 failed**, 25 `test_*.py` files.
All 19 failures are the same pre-existing `PermissionError` on hardcoded
`/tmp/test_*` paths this project has seen in every prior session
(sandbox-specific, not a code issue) -- all in `test_format_writers.py`/
`test_image_preview.py`. Re-confirmed fresh this checkpoint (no code
changed since #7, so an unchanged count is the expected, correct result,
not a rerun for its own sake).

## Known limitations

- **The GL-safety gap on the drag-to-float path** (see RUNNING.md #40,
  item 6) -- not individually re-tested this round; worth a specific
  look if the Graph panel ever renders oddly right after being dragged
  (as opposed to floated via menu/shortcut).
- **App icon**: smallest-size legibility issue (flagged 07-23) — not
  touched since.
- **`setup.py`'s `includes` list still looks incomplete** — likely
  harmless, still unverified against a real build.
- **Retarget is still single-pair**.
- **Stack Forking: no within-branch or cross-branch drag reordering yet**.
- **A real on-screen QA pass of the fork table** — still only verified
  headlessly.
- **Graph panel's Y curve X-axis**: plotted against `input_r`
  specifically — correct when the Stack's domain is linked across
  channels (the default), an approximation if deliberately set
  asymmetric. Still flagged, not solved.
- **Cage mesh edge count grows fast with Grid density** — unverified
  whether that's too slow in practice on real hardware.

## Backlog

- **UI polish/beautification** -- Tom's next explicit ask, just raised,
  scope not yet defined (about to clarify: colors/theme? spacing/
  density? icons? something else?). Nothing implemented yet as of this
  checkpoint.
- Stack Forking: within-branch drag reordering (see Known limitations).
- App icon small-size legibility (see Known limitations).
- Retarget 4-field expansion (see Known limitations).
- A real on-screen QA pass of the fork table (see Known limitations).

## Files

**Active** (imported by `main_window.py`, directly or transitively — 35
modules, unchanged from #7):
`main_window.py`, `browser_panel.py`, `stack_memories_panel.py`,
`lut_library_panel.py`, `cdl_library_panel.py`, `image_library_panel.py`,
`sample_image_panel.py`, `stack_panel.py`, `scopes_panel.py`,
`panel_frame.py`, `style.py`, `stack.py`, `stack_memory.py`,
`lut_library.py`, `cdl_library.py`, `image_library.py`, `undo_history.py`,
`help_text.py`, `app_logic.py`, `session_io.py`, `clf_adapter.py`,
`format_writers.py`, `wipe_compare_widget.py`, `lut_viewer_3d.py`,
`scopes.py`, `colorspace.py`, `colorspace_convert.py`, `retarget.py`,
`printer_lights.py`, `aces_drt.py`, `cdl.py`, `cdl_file_io.py`,
`stack_memory_file_io.py`, `color_balance.py`, `color_wheel_widget.py`,
`floating_dialog.py`, `workspace_io.py`.

Modified this checkpoint: none (docs-only -- `RUNNING.md` #41, this file).
Docstring-only touch-ups in four left-column panel modules
(`cdl_library_panel.py`/`image_library_panel.py`/`lut_library_panel.py`/
`stack_memories_panel.py`, replacing stale `left_splitter` mentions with
`_build_default_dock_layout` references) landed at the very end of #7's
work, already reflected in that checkpoint's file list.

**Legacy / orphaned** — unchanged, still candidates for deletion if the
repo needs tidying: `concatenate_panel.py`, `aces_drt_panel.py`,
`lut_controls_panel.py`, `collapsible_section.py`.

**Harmless leftover artifacts** (sandbox couldn't delete them):
`main_window.py.bak`, `sample_image_panel.py.bak`, `scopes_panel.py.bak`,
`stack_panel.py.bak` from an old `sed -i.bak`, still present, still
harmless, still ignorable.

**Tests**: 25 `test_*.py` files, 304 passing (see Test status above).

**Docs**: `USER_MANUAL.md`/`help_text.py` (byte-identical, re-verified),
`BUILD_ON_MAC.md`, `RUNNING.md` (up to date through #41),
`KNOWN_GOOD_SUMMARY_2026-07-23.md` through
`KNOWN_GOOD_SUMMARY_2026-07-24-7.md` (previous checkpoints), this file.

**Build**: `python3 setup.py py2app` (from a clean `build`/`dist`) produces
`dist/LUTier.app`, universal2. Icon via `make_icon.sh AppIcon_source.png`
(macOS-only). Tom is now running the packaged/real app itself (per his
"works perfectly" confirmation) -- still worth a fresh
`python3 setup.py py2app` before the NEXT round of changes ships, same
standing practice, not because anything's suspected broken.

**Environment**: `requirements.txt` covers PySide6, pyqtgraph, PyOpenGL,
scipy, colour-science, colour-clf-io, numpy, Pillow, opencolorio, all
confirmed working in this migration's sandbox (existing `.eglstub/`
offscreen-Qt setup).
