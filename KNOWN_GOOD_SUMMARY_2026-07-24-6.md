# LUTier — Known Good Summary (2026-07-24, #6)

Checkpoint reference for picking this back up later, superseding
`KNOWN_GOOD_SUMMARY_2026-07-24-5.md` (kept alongside, not deleted, for
migration diffing). Written **deliberately right before the riskiest
change in this project's history**: Tom has greenlit free-form,
drag-to-rearrange panels ("Yeah. we want free form.") and asked for the
migration to Qt's native `QMainWindow`/`QDockWidget` docking system,
replacing `main_window.py`'s current hand-built `QWidget` + three
hardcoded nested `QSplitter`s layout. This file is the rollback point if
that migration goes sideways.

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

## What changed since #5 (RUNNING.md #39)

One fix, no new features: floated panels and popup dialogs (Stack's Grid
Size/Domain, Scopes' Graticule Intensity, Sample/Reference Adapt, Export,
Help Manual) were using `Qt.WindowStaysOnTopHint`, pinning them above
*every* window on the desktop, not just LUTier's own main window. Per
Tom: "The dialogue and float windows can go under other programs, just
not the main window." Fixed by switching to `Qt.WindowType.Tool`
(`floating_dialog.py`'s `make_floating_tool_window`, replacing
`make_always_on_top`) plus giving floated panels a real Qt parent
(`main_window.py`'s `_float_panel` now does
`frame.setParent(self, Qt.WindowType.Tool)` in one call instead of
severing the parent with `setParent(None)`) — Tool only enforces
"stays above owner" for windows that actually have one. New
`test_floating_window_scope.py` (9 tests). Full detail in RUNNING.md #39.

**Still UNVERIFIED ON REAL HARDWARE** — this sandbox has no real window
manager. Please test: float a panel or open a dialog, click into a
different app, confirm LUTier now sits behind it; click back on LUTier's
main window, confirm the float/dialog returns above it specifically.

## Architecture

Unchanged from #5 except the floating-window fix above — see #4/#3 for
the full rundown of `stack.py`, Stack Forking, per-operator backends,
library backends, `undo_history.py`, `sample_image_panel.py`,
`session_io.py` (still VERSION 6), etc. Panel layout is still the
**pre-migration** shape: `main_window.py`'s `_build_layout()` builds
`main_splitter` (horizontal) containing `left_splitter`/`center_splitter`/
`right_splitter` (each vertical), each panel's `PanelFrame` added via
`.addWidget()` in a fixed order chosen once at startup. `get_workspace_
state()`/`load_workspace_state()` persist window size, per-panel
visibility, the floating set, and splitter `.sizes()` — never splitter
*membership*, so there is currently no runtime concept of moving a panel
from one splitter to another. This is exactly what the pending
`QMainWindow`/`QDockWidget` migration replaces. Default arrangement:
Browser top-left; Image/LUT/CDL Library + Stack Memories below it;
Viewer+Stack center; Parameters/Scopes/Graph on the right (Scopes
bottom-right, Graph center-right).

## Test status

`python3 -m pytest -q` — **298 passed, 19 failed**, 25 `test_*.py` files.
All 19 failures are the same pre-existing `PermissionError` on hardcoded
`/tmp/test_*` paths this project has seen in every prior session
(sandbox-specific, not a code issue) — all in `test_format_writers.py`/
`test_image_preview.py`. Re-confirmed this checkpoint by rerunning the
full suite fresh.

Offscreen Qt via the `.eglstub/` stub continues to let real `MainWindow`/
`LutViewer3D` instances construct and run for verification — real GPU
rendering and real window-manager stacking behavior still can't be
exercised here, which is exactly why the docking migration ahead carries
real risk that only Tom's own machine can fully retire.

## Known limitations

- **Free-form panel rearranging does not exist yet** — this is the very
  next thing being built, starting from this checkpoint.
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
  at the slider's max, 65) — unverified whether that's too slow in
  practice on real hardware.
- **The entire Graph panel and the new Qt.Tool floating-window scoping
  are both still unverified on real hardware** — see above.

## Backlog

- **Free-form drag-to-rearrange panels via `QMainWindow`/`QDockWidget`**
  — in progress as of this checkpoint. Must preserve: the default spatial
  arrangement above, all 10 panels' functionality including each panel's
  own "..." corner menu and menu-bar mirror, the workspace-remember/
  preset system (likely simplified via `QMainWindow.saveState()`/
  `restoreState()`), and critically the hard-won GL-widget crash safety
  for `LutViewer3D` (`_SafeGLViewWidget`, `rebuild_gl_view()`,
  `_pending_gl_view_deletions`, `AA_ShareOpenGLContexts`) — `QDockWidget`
  floating/docking also reparents its content widget under the hood and
  could reintroduce a already-fixed crash class if not handled carefully.
  The just-shipped `Qt.Tool` floating-window-scoping fix (#39) will also
  need reconciling with (or may be subsumed by) whatever floating
  behavior `QDockWidget` provides natively.
- Stack Forking: within-branch drag reordering (see Known limitations).
- App icon small-size legibility (see Known limitations).
- Retarget 4-field expansion (see Known limitations).
- A real on-screen QA pass of the fork table (see Known limitations).
- Real-hardware confirmation of the Graph panel and the Qt.Tool floating
  fix (see Known limitations).

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
`stack_memory_file_io.py`, `color_balance.py`, `color_wheel_widget.py`,
`floating_dialog.py`, `workspace_io.py`.

Modified this checkpoint: `floating_dialog.py` (rewrite — Tool instead of
StaysOnTop), `main_window.py` (`_float_panel` reparenting),
`sample_image_panel.py`/`scopes_panel.py`/`stack_panel.py` (import/call
renames), `help_text.py`, `USER_MANUAL.md`, new
`test_floating_window_scope.py`.

**Legacy / orphaned** — unchanged, still candidates for deletion if the
repo needs tidying: `concatenate_panel.py`, `aces_drt_panel.py`,
`lut_controls_panel.py`, `collapsible_section.py`.

**Harmless leftover artifacts** (sandbox couldn't delete them —
permission-restricted, same as outputs-folder delete rule): `main_window.
py.bak`, `sample_image_panel.py.bak`, `scopes_panel.py.bak`,
`stack_panel.py.bak` from an earlier `sed -i.bak`. Ignore/delete manually
if it bothers you; avoid `sed -i.bak` going forward (plain `-i` or the
Edit tool instead).

**Tests**: 25 `test_*.py` files, 298 passing (see Test status above).

**Docs**: `USER_MANUAL.md`/`help_text.py` (byte-identical, re-verified),
`BUILD_ON_MAC.md`, `RUNNING.md` (up to date through #39),
`KNOWN_GOOD_SUMMARY_2026-07-23.md` through
`KNOWN_GOOD_SUMMARY_2026-07-24-5.md` (previous checkpoints), this file.

**Build**: `python3 setup.py py2app` (from a clean `build`/`dist`) produces
`dist/LUTier.app`, universal2. Icon via `make_icon.sh AppIcon_source.png`
(macOS-only).

**Environment**: `requirements.txt` covers PySide6, pyqtgraph, PyOpenGL,
scipy, colour-science, colour-clf-io, numpy, Pillow, opencolorio, all
confirmed working in this migration's sandbox (fresh `pip install`, plus
the existing `.eglstub/` offscreen-Qt setup).
