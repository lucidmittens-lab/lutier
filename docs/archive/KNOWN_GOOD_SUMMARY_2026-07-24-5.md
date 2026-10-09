# LUTier — Known Good Summary (2026-07-24, #5)

Checkpoint reference for picking this back up later, superseding
`KNOWN_GOOD_SUMMARY_2026-07-24-4.md` (kept alongside, not deleted, for
migration diffing). That checkpoint shipped the Graph panel's first pass
(3D/1D mode buttons, a flat reference grid). Tom tried it and reported
three real bugs, all fixed this round (`RUNNING.md` #38) -- this file's
job is a clean, fully-tested baseline for whatever comes next.

## What this is

LUTier — a PySide6 desktop app for building and previewing LUT-based
color grades: load LUTs/CDLs from a folder, stack operations (LUT,
Printer Lights trim, ASC CDL SOP/SAT, Color Space Conversion, ACES DRT,
Stack Forking) non-destructively, preview against a Sample/Reference
image pair with a wipe compare, inspect via histogram/waveform/
vectorscope/Graph (3D point cloud + wireframe cage + 1D YRGB curves),
save named "Stack Memory" snapshots, maintain reusable LUT/CDL/Image
libraries, undo/redo Stack edits, save/load whole sessions (`.ltr`,
including workspace layout), export the baked result to `.cube`/`.clf`/
`.3dl`/`.cub`/`.spi3d`/`.csp` via a dedicated Export dialog. Packaged as
a native macOS `.app` via py2app, icon included. In-app manual via
Help > Manual, also on disk as `USER_MANUAL.md` (kept byte-identical to
`help_text.py`'s `USER_MANUAL_MARKDOWN` by hand, re-verified this
checkpoint).

## What changed since #4 (RUNNING.md #38)

Three real bugs Tom caught in the Graph panel's first pass, all fixed:

1. **Mode switch moved from a broken button pair into a "..." menu**,
   matching `scopes_panel.py`'s own scope-selector convention exactly
   (one `QActionGroup`, mirrored into a new top-level "Graph" menu-bar
   entry). Five choices now, not two: **3D**, **1D Y**, **1D R**,
   **1D G**, **1D B** -- each 1D choice shows exactly one curve, not all
   four overlaid.
2. **Panel-resize-on-switch bug fixed**: the GL panel and curve plot are
   now pages of one `QStackedWidget` instead of setVisible()-toggled
   siblings -- confirmed empirically that a `QStackedWidget`'s
   `minimumSizeHint()` is the max across all pages regardless of which is
   current, so the panel's footprint can no longer change on a view
   switch.
3. **The flat reference grid is GONE, replaced by an actual wireframe
   cage** connecting each plotted point to its lattice neighbors (new
   `app_logic.extract_3d_cage_lines_for_visualization`), per Tom's
   correction that "grid" meant the LUT's own deformed mesh, not a
   ground-plane reference. Renamed grid->cage throughout
   `lut_viewer_3d.py` (`_cage_opacity`, `cage_opacity_slider`,
   `_apply_cage_opacity`, `_cage_item`) -- a real feature change, not a
   relabel. One real API gotcha caught before shipping: `GLLinePlotItem`'s
   `color` is a 0.0-1.0 float tuple, not `GLGridItem`'s 0-255 range.

Also fixed a latent bug surfaced while building the cage:
`rebuild_gl_view` must reset `_scatter_item`/`_cage_item` to `None`
BEFORE reassigning `self.gl_view`, or the rebuild's `removeItem()` calls
target the wrong (new) GL view and raise -- caught by following the
existing (correct) pattern carefully, not by a failing test.

## Architecture

Unchanged from #4 except where noted above -- see that file (and #3
before it) for the full rundown of `stack.py`, Stack Forking,
per-operator backends, library backends, `undo_history.py`,
`sample_image_panel.py`, `session_io.py` (still VERSION 6), etc. Panel
layout diagram unchanged from #4 (Graph still bottom-right, `⇧0`).

## Test status

`python3 -m pytest -q` — **289 passed, 19 failed**, 24 `test_*.py` files
(`test_gl_reparent_safety.py` grew from 32 to 38 tests: 5 cage tests + 7
menu/QStackedWidget tests replacing 3 grid-opacity + 4 button-mode
tests), in a sandbox with PySide6/pyqtgraph/PyOpenGL/scipy/colour-
science/colour-clf-io/numpy/Pillow/opencolorio all installed. All 19
failures are the same pre-existing `PermissionError` on hardcoded
`/tmp/test_*` paths this project has seen in every prior session
(sandbox-specific, not a code issue) -- all in
`test_format_writers.py`/`test_image_preview.py`.

Offscreen Qt via the `.eglstub/` stub continues to let real `MainWindow`/
`LutViewer3D` instances construct and run for verification, including a
direct empirical check that `QStackedWidget.minimumSizeHint()` doesn't
change across page switches (the actual regression test for bug #2
above) -- real GPU rendering still can't be exercised here.

## Known limitations

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
- **Cage mesh edge count grows fast with Grid density** (811,200 edges
  at the slider's max, 65) — same order of magnitude as the point count
  that density already asks pyqtgraph/OpenGL to handle, so no separate
  cap was added; unverified whether that's too slow in practice on real
  hardware.
- **The entire Graph panel (3D cage + all five view modes) is still
  unverified on real hardware** — this round fixed three bugs Tom found
  by actually running it, which is exactly why "passing tests" and
  "confirmed on Tom's machine" keep being tracked as separate facts in
  this project.

## Backlog

- Whatever the next feature/tweak is — nothing outstanding from Tom's
  explicit requests as of this checkpoint.
- Stack Forking: within-branch drag reordering (see Known limitations).
- App icon small-size legibility (see Known limitations).
- Retarget 4-field expansion (see Known limitations).
- A real on-screen QA pass of the fork table (see Known limitations).
- Real-hardware confirmation of the Graph panel end to end (menu-based
  view switch, no more resize-snap, the cage mesh actually looks like a
  cage) — see Known limitations.

## Files

**Active** (imported by `main_window.py`, directly or transitively — 35
modules, unchanged from prior checkpoint):
`main_window.py`, `browser_panel.py`, `stack_memories_panel.py`,
`lut_library_panel.py`, `cdl_library_panel.py`, `image_library_panel.py`,
`sample_image_panel.py`, `stack_panel.py`, `scopes_panel.py`,
`panel_frame.py`, `style.py`, `stack.py`, `stack_memory.py`,
`lut_library.py`, `cdl_library.py`, `image_library.py`, `undo_history.py`,
`help_text.py`, `app_logic.py`, `session_io.py`, `clf_adapter.py`,
`format_writers.py`, `wipe_compare_widget.py`, `lut_viewer_3d.py`,
`scopes.py`, `colorspace.py`, `colorspace_convert.py`, `retarget.py`,
`printer_lights.py`, `aces_drt.py`, `cdl.py`, `cdl_file_io.py`,
`stack_memory_file_io.py`, `color_balance.py`, `color_wheel_widget.py`.

Modified this checkpoint: `main_window.py`, `lut_viewer_3d.py`
(substantial rewrite -- QStackedWidget, "..." menu, cage mesh),
`app_logic.py` (new `extract_3d_cage_lines_for_visualization`),
`help_text.py`, `USER_MANUAL.md`, `test_gl_reparent_safety.py`.

**Legacy / orphaned** — unchanged, still candidates for deletion if the
repo needs tidying: `concatenate_panel.py`, `aces_drt_panel.py`,
`lut_controls_panel.py`, `collapsible_section.py`.

**Tests**: 24 `test_*.py` files, 289 passing (see Test status above).

**Docs**: `USER_MANUAL.md`/`help_text.py` (byte-identical, re-verified),
`BUILD_ON_MAC.md`, `RUNNING.md` (up to date through #38),
`KNOWN_GOOD_SUMMARY_2026-07-23.md` through
`KNOWN_GOOD_SUMMARY_2026-07-24-4.md` (previous checkpoints), this file.

**Build**: `python3 setup.py py2app` (from a clean `build`/`dist`) produces
`dist/LUTier.app`, universal2. Icon via `make_icon.sh AppIcon_source.png`
(macOS-only).

**Environment**: `requirements.txt` covers PySide6, pyqtgraph, PyOpenGL,
scipy, colour-science, colour-clf-io, numpy, Pillow, opencolorio, all
confirmed working in this migration's sandbox (fresh `pip install`, plus
the existing `.eglstub/` offscreen-Qt setup).
