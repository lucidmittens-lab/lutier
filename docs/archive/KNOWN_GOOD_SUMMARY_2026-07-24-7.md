# LUTier — Known Good Summary (2026-07-24, #7)

Checkpoint reference for picking this back up later, superseding
`KNOWN_GOOD_SUMMARY_2026-07-24-6.md` (kept alongside, not deleted).
That checkpoint was written deliberately right before the riskiest
change in this project's history -- migrating `main_window.py` from a
hand-built `QWidget` + three hardcoded `QSplitter`s to Qt's native
`QMainWindow`/`QDockWidget` docking system, for true free-form
drag-to-rearrange panels. This checkpoint reflects that migration
**done, tested, and documented** (RUNNING.md #40).

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
via a dedicated Export dialog. Packaged as a native macOS `.app` via
py2app, icon included. In-app manual via Help > Manual, also on disk as
`USER_MANUAL.md` (kept byte-identical to `help_text.py`'s
`USER_MANUAL_MARKDOWN`, re-verified this checkpoint). **New as of this
checkpoint: every panel can be freely dragged to rearrange, tab, or float
— the fixed spatial layout described in earlier checkpoints is now just
a starting position, not a structural constraint.**

## What changed since #6 (RUNNING.md #40)

The full docking migration, per Tom: "Yeah. we want free form... do the
migration to new system." Full detail in RUNNING.md #40; summary:

1. **Every panel is now a `QDockWidget`**, not a `PanelFrame` sitting in
   a fixed `QSplitter` slot -- placed via `QMainWindow.addDockWidget`/
   `splitDockWidget` (`_build_default_dock_layout`) into the same default
   spatial arrangement as before, but now freely draggable to any edge,
   tabbable with a neighbor, or tearable-off into a floating window, all
   via Qt's own native docking mechanics.
2. **Custom title bars** (`_build_dock_title_bar`) reuse the exact
   `#panelTitleRow`/`#panelTitle` object names `style.py` already styled
   for `PanelFrame`'s old title row -- same look, zero new CSS -- and
   carry each panel's existing "..." corner-menu button in the same spot.
3. **Floating now goes through `QDockWidget.setFloating()`**, Qt's own
   mechanism -- confirmed empirically that this already produces exactly
   `Qt.WindowType.Tool`, parented to the main window, no
   `WindowStaysOnTopHint`, i.e. #39's hand-built fix is now provided
   natively for free. A `_PanelDockWidget` subclass's `closeEvent`
   override keeps the exact pre-migration behavior (closing a floating
   panel's native close button redocks it, matching what #29-#32's crash
   fixes were tested against) rather than accepting Qt's own "just hide
   it" default.
4. **Workspace persistence rewritten** around `QMainWindow.saveState()`/
   `restoreState()` (base64-encoded blob) instead of a hand-rolled
   `{window_size, panels, splitters, floating}` dict --
   `workspace_io.WORKSPACE_FORMAT_VERSION` bumped 1->2; an old-format
   saved file is tolerated as a no-op, not applied or raised on.
5. **Two real bugs caught by this round's own new tests**, both fixed:
   "Reset to Default Workspace" didn't actually redock a floating panel
   (`addDockWidget`/`splitDockWidget` don't reliably redock an
   already-floating widget, unlike `restoreState()`, which does --
   fixed with an explicit `_dock_all_floating_panels()` first); and
   `test_gl_reparent_safety.py` was writing to/reading from the REAL
   `workspace_io.DEFAULT_WORKSPACE_PATH` across its own tests (a
   pre-existing test-isolation gap this migration's new tests happened
   to expose, not something it introduced) -- fixed by redirecting that
   path to a temp directory for the whole test module, same discipline
   `test_workspace_io.py` already had.
6. **One acknowledged, explicitly-documented gap, not glossed over**: a
   panel dragged out interactively can't get the same
   `setUpdatesEnabled`-based paint-suspend protection
   (`_reparent_gl_safely`) as a menu/shortcut-triggered float, since Qt
   performs that reparent internally mid-drag with no hook available
   beforehand. The other three GL-safety layers
   (`_SafeGLViewWidget.isValid()`, `rebuild_gl_view()`'s full rebuild,
   `AA_ShareOpenGLContexts`) are unchanged and still apply regardless.
   Flagged as the sharpest real-hardware test still needed (drag the
   Graph panel, repeatedly).

## Architecture

Unchanged from #6 except the layout mechanism above -- see #5/#4/#3 for
the full rundown of `stack.py`, Stack Forking, per-operator backends,
library backends, `undo_history.py`, `sample_image_panel.py`,
`session_io.py` (still VERSION 7, workspace separate from session),
`floating_dialog.py`, etc. Default spatial arrangement (Browser top-left;
Image/LUT/CDL Library + Stack Memories below it; Viewer+Stack center;
Parameters/Scopes/Graph on the right) is now just where LUTier STARTS,
not a constraint -- see `_build_default_dock_layout`.

## Test status

`python3 -m pytest -q` — **304 passed, 19 failed**, 25 `test_*.py` files
(`test_gl_reparent_safety.py` grew from 38 to 55 tests -- 5 rewritten for
the new dock-based API, 6 new ones covering the migration itself; module
now redirects `workspace_io`'s remembered-workspace path so it never
touches the real file). All 19 failures are the same pre-existing
`PermissionError` on hardcoded `/tmp/test_*` paths this project has seen
in every prior session (sandbox-specific, not a code issue) -- all in
`test_format_writers.py`/`test_image_preview.py`. Re-confirmed fresh this
checkpoint, including confirming the real `~/Library/Application
Support/LUTier/workspace.json` path is untouched by a full `pytest` run.

Offscreen Qt via the `.eglstub/` stub continues to let real `MainWindow`/
`LutViewer3D` instances construct and run for verification, including
empirically confirming (against the real installed PySide6, not assumed)
several non-obvious `QDockWidget` facts used in this migration: native
floating sets exactly `Qt.WindowType.Tool` with no `StaysOnTopHint`;
`addDockWidget`/`splitDockWidget` do NOT reliably redock an
already-floating widget, but `restoreState()` does; `QDockWidget.close()`
while floating hides rather than destroys. Real GPU rendering and real
window-manager drag/tab/tear-off behavior still can't be exercised here
-- see Known limitations.

## Known limitations

- **Free-form dragging itself is entirely unverified on real hardware**
  -- dragging a title bar to another dock area or to tab with a
  neighbor, dragging out to float, whether the custom title bar feels
  right to grab, whether a floating dock's OS-native chrome plus this
  app's own custom title row looks doubled-up rather than clean. See
  RUNNING.md #40's closing list of six specific things to test.
- **The GL-safety gap on the drag-to-float path** (see What changed #6
  above) -- flagged, not solved; the Graph panel specifically needs a
  real-hardware drag-float-drag-dock stress test.
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
- **Real Export/Import Workspace Preset round trip carrying an actual
  drag-built (non-default) arrangement** -- only the default-arrangement
  and programmatic-float paths have been exercised so far.

## Backlog

- Real-hardware confirmation of the docking migration end to end (see
  Known limitations) -- the natural next thing to hear back on.
- Stack Forking: within-branch drag reordering (see Known limitations).
- App icon small-size legibility (see Known limitations).
- Retarget 4-field expansion (see Known limitations).
- A real on-screen QA pass of the fork table (see Known limitations).
- Nothing else outstanding from Tom's explicit requests as of this
  checkpoint.

## Files

**Active** (imported by `main_window.py`, directly or transitively — 35
modules, unchanged set from prior checkpoint):
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

Modified this checkpoint: `main_window.py` (the migration itself --
substantial rewrite of `_build_layout`, all float/dock machinery,
workspace persistence; new `_PanelDockWidget`/`_build_dock_title_bar`/
`_make_dock`/`_build_default_dock_layout`/`_on_dock_top_level_changed`),
`workspace_io.py` (version bump + docstring), `help_text.py`/
`USER_MANUAL.md` (Workspace/Sessions sections), `test_gl_reparent_
safety.py` (5 tests rewritten, 6 new, path-redirect fix).
`panel_frame.py` unchanged but now called differently (`title=None`
everywhere) -- still actively used, not orphaned.

**Legacy / orphaned** — unchanged, still candidates for deletion if the
repo needs tidying: `concatenate_panel.py`, `aces_drt_panel.py`,
`lut_controls_panel.py`, `collapsible_section.py`.

**Harmless leftover artifacts** (sandbox couldn't delete them):
`main_window.py.bak`, `sample_image_panel.py.bak`, `scopes_panel.py.bak`,
`stack_panel.py.bak` from an old `sed -i.bak`, still present, still
harmless, still ignorable.

**Tests**: 25 `test_*.py` files, 304 passing (see Test status above).

**Docs**: `USER_MANUAL.md`/`help_text.py` (byte-identical, re-verified),
`BUILD_ON_MAC.md`, `RUNNING.md` (up to date through #40),
`KNOWN_GOOD_SUMMARY_2026-07-23.md` through
`KNOWN_GOOD_SUMMARY_2026-07-24-6.md` (previous checkpoints), this file.

**Build**: `python3 setup.py py2app` (from a clean `build`/`dist`) produces
`dist/LUTier.app`, universal2. Icon via `make_icon.sh AppIcon_source.png`
(macOS-only). NOT rebuilt/re-verified this checkpoint -- the docking
migration hasn't been packaged and run as a real `.app` yet, only via
`python3 main_window.py`-equivalent headless construction.

**Environment**: `requirements.txt` covers PySide6, pyqtgraph, PyOpenGL,
scipy, colour-science, colour-clf-io, numpy, Pillow, opencolorio, all
confirmed working in this migration's sandbox (existing `.eglstub/`
offscreen-Qt setup, no new dependencies needed for `QDockWidget` --
that's core `PySide6.QtWidgets`).
