# LUTier — Known Good Summary (2026-07-24, #4)

Checkpoint reference for picking this back up later, superseding
`KNOWN_GOOD_SUMMARY_2026-07-24-3.md` (kept alongside, not deleted, for
migration diffing). That checkpoint was written right after Stack
Forking landed; a lot happened since, spanning the v009→v010→v011
migration chain: the floating-panel crash/blank-render chase (#29-#34,
now CONFIRMED FIXED on real hardware), a real waveform-scope orientation
bug (#35), and two of Tom's feature requests -- a 3D-viewer grid opacity
slider (#36) and a 1D YRGB curve mode that renamed the panel to "Graph"
(#37). See `RUNNING.md` entries #29-#37 for the full blow-by-blow: this
file's job is just a clean, fully-tested baseline for whatever comes next.

## What this is

LUTier — a PySide6 desktop app for building and previewing LUT-based
color grades: load LUTs/CDLs from a folder, stack operations (LUT,
Printer Lights trim, ASC CDL SOP/SAT, Color Space Conversion, ACES DRT,
Stack Forking) non-destructively, preview against a Sample/Reference
image pair with a wipe compare, inspect via histogram/waveform/
vectorscope/Graph (3D point cloud + 1D YRGB curves), save named "Stack
Memory" snapshots, maintain reusable LUT/CDL/Image libraries, undo/redo
Stack edits, save/load whole sessions (`.ltr`, including workspace
layout), export the baked result to `.cube`/`.clf`/`.3dl`/`.cub`/
`.spi3d`/`.csp` via a dedicated Export dialog. Packaged as a native macOS
`.app` via py2app, icon included. In-app manual via Help > Manual, also
on disk as `USER_MANUAL.md` (kept byte-identical to `help_text.py`'s
`USER_MANUAL_MARKDOWN` by hand, re-verified this checkpoint).

## What changed since #3

- **The floating-panel crash + blank-render saga (#29-#34) is CLOSED.**
  Root causes, in the order actually found: a null-context paint during
  reparent (crash guard), an existing `QOpenGLWidget` not reliably
  surviving reparent into/out of a top-level window (rebuild-fresh-widget
  fix), a `deleteLater()`/Python-GC race on the discarded widget (double-
  free crash), the remember-workspace feature restoring a floating panel
  synchronously during `__init__` before the app was really running
  (deferred via `QTimer.singleShot`), and finally the actual cause of the
  blank render: pyqtgraph's `GLLinePlotItem`/`GLScatterPlotItem` cache
  their compiled shader program at the CLASS level, which only means
  anything within the GL context that compiled it -- fixed via
  `Qt.AA_ShareOpenGLContexts`. **Tom confirmed on real hardware: floating,
  docking, quitting, and rendering all work correctly now.**
- **Waveform scope fix (#35)**: an `np.flipud()` in `scopes_panel.py` was
  inverting an already-correctly-oriented image under this app's specific
  pyqtgraph config -- removed. This was Tom's "HDR limiter makes the
  waveform bunch at the bottom" report.
- **3D viewer grid opacity slider (#36)**: `lut_viewer_3d.py` gained a
  0-100% "Grid opacity" slider (defaults to 30%, matching pyqtgraph's own
  `GLGridItem` default alpha), surviving float/dock rebuilds the same way
  Grid density's value already does.
- **Graph panel + 1D YRGB curves (#37)**: the "3D Viewer" panel is now
  called **"Graph"** (internal names unchanged) and has a 3D/1D mode
  switch. 1D mode is a new `pg.PlotWidget` plotting the Stack's baked LUT
  response as four curves (Y/R/G/B vs. input) via new
  `app_logic.compute_yrgb_curve` -- the ramp fed in follows the LUT's own
  declared `.domain` (correct for HDR/scene-linear grades), not a fixed
  [0,1]. Both the 3D scatter and the 1D curves are built once and kept
  alive permanently; switching modes only toggles visibility, never
  destroys/rebuilds either widget (deliberately, given the float/dock
  crash history above).

## Architecture

Unchanged from #3 except where noted above -- see that file for the full
rundown of `stack.py`, Stack Forking, per-operator backends, library
backends, `undo_history.py`, `sample_image_panel.py`, `session_io.py`
(still VERSION 6), etc. `main_window.py`'s panel-layout diagram, updated:

```
+----------+----------------------------+--------------+
| Browser  |                            |  Parameters  |
|  (top    |          Viewer            +--------------+
|   left)  |                            |    Scopes    |
+----------+----------------------------+--------------+
|  Image   |    Stack -- the stage list |    Graph     |
| Library  |    (center, main area)    |  (bottom      |
+----------+                           |   right)      |
|   LUT    |                           |              |
| Library  |                           |              |
+----------+                           |              |
|   CDL    |                           |              |
| Library  |                           |              |
+----------+                           |              |
|  Stack   |                           |              |
| Memories |                           |              |
| (bottom  |                           |              |
|   left)  |                           |              |
+----------+---------------------------+--------------+
```

Ten independently toggleable panels, same `⇧1`-`⇧0` shortcuts as before
(position 10, "Graph", still gets `⇧0`).

## Test status

`python3 -m pytest -q` — **283 passed, 19 failed**, 24 `test_*.py` files
(new this checkpoint: `test_yrgb_curve.py`; `test_waveform_orientation.py`
and `test_gl_reparent_safety.py` both grew), in a sandbox with PySide6/
pyqtgraph/PyOpenGL/scipy/colour-science/colour-clf-io/numpy/Pillow/
opencolorio all installed. All 19 failures are the same pre-existing
`PermissionError` on hardcoded `/tmp/test_*` paths this project has seen
in every prior session (sandbox-specific, not a code issue) -- all in
`test_format_writers.py`/`test_image_preview.py`.

Offscreen Qt via the `.eglstub/` stub (`QT_QPA_PLATFORM=offscreen` +
`LD_LIBRARY_PATH`) continues to let real `MainWindow`/`LutViewer3D`
instances construct and run for verification, though real GPU rendering
still can't be exercised here -- see RUNNING.md's per-entry "UNVERIFIED ON
REAL HARDWARE" notes for what's been confirmed by Tom vs. still pending
(as of this checkpoint: everything through #34 is confirmed; #35-#37 are
freshly built and not yet confirmed on real hardware).

## Known limitations

- **App icon**: smallest-size legibility issue (flagged 07-23) — not
  touched since.
- **`setup.py`'s `includes` list still looks incomplete** relative to the
  actual current set of local modules — likely harmless, still unverified
  against a real build.
- **Retarget is still single-pair** — only picks the color space to
  reinterpret a LUT's already-declared input/output as, not full
  four-field override.
- **Stack Forking: no within-branch or cross-branch drag reordering yet**
  — only the branch combo moves a stage between A/B/unassigned.
- **A real on-screen QA pass of the fork table** — still only verified
  headlessly at the data/widget-tree level, never seen on a real display.
- **New this checkpoint — Graph panel's Y curve X-axis**: plotted against
  `input_r` specifically (see `compute_yrgb_curve`'s docstring) — correct
  when the Stack's domain is linked across channels (the default), an
  approximation if it's deliberately set asymmetric. Flagged, not solved
  — unclear whether this edge case matters in practice.
- **New this checkpoint — Graph panel's 1D mode is entirely unverified on
  real hardware**, same as the 3D mode always has been — offscreen Qt
  proves the widget tree builds and the data is correct, not that the
  plot looks right or the mode-switch buttons feel right to click.

## Backlog

- Whatever the next feature/tweak is — nothing outstanding from Tom's
  explicit requests as of this checkpoint (grid opacity and the Graph/1D
  curves both shipped this round).
- Stack Forking: within-branch drag reordering (see Known limitations).
- App icon small-size legibility (see Known limitations).
- Retarget 4-field expansion (see Known limitations).
- A real on-screen QA pass of the fork table (see Known limitations).
- Real-hardware confirmation of #35 (waveform fix), #36 (grid opacity),
  and #37 (Graph panel rename + 1D curves) — all shipped with passing
  tests but, per this project's own convention, "passing tests" and
  "confirmed on Tom's actual machine" are tracked as separate facts.

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

Modified this checkpoint: `main_window.py`, `lut_viewer_3d.py`,
`app_logic.py`, `scopes_panel.py`, `help_text.py`, `USER_MANUAL.md`,
`test_gl_reparent_safety.py`, `test_waveform_orientation.py` (new),
`test_yrgb_curve.py` (new).

**Legacy / orphaned** — unchanged from prior checkpoint, still candidates
for deletion if the repo needs tidying: `concatenate_panel.py`,
`aces_drt_panel.py`, `lut_controls_panel.py`, `collapsible_section.py`.

**Tests**: 24 `test_*.py` files, 283 passing (see Test status above).

**Docs**: `USER_MANUAL.md`/`help_text.py` (byte-identical, re-verified),
`BUILD_ON_MAC.md`, `RUNNING.md` (session-by-session changelog, up to date
through #37), `KNOWN_GOOD_SUMMARY_2026-07-23.md`,
`KNOWN_GOOD_SUMMARY_2026-07-24.md`, `KNOWN_GOOD_SUMMARY_2026-07-24-2.md`,
`KNOWN_GOOD_SUMMARY_2026-07-24-3.md` (previous checkpoints), this file.

**Build**: `python3 setup.py py2app` (from a clean `build`/`dist`) produces
`dist/LUTier.app`, universal2. Icon via `make_icon.sh AppIcon_source.png`
(macOS-only).

**Environment**: `requirements.txt` covers PySide6, pyqtgraph, PyOpenGL,
scipy, colour-science, colour-clf-io, numpy, Pillow, opencolorio. This
migration's sandbox needed a fresh `pip install -r requirements.txt`
(new container) plus the existing `.eglstub/` offscreen-Qt setup — both
confirmed working, see Test status above.
