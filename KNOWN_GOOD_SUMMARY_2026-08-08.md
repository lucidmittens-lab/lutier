# LUTier — Known Good Summary (2026-08-08)

Checkpoint reference for picking this back up later, superseding
`KNOWN_GOOD_SUMMARY_2026-07-25.md` (kept alongside, not deleted). That
checkpoint closed out a color-palette arc with everything unverified on
real hardware. This checkpoint covers the opposite: an entire session run
directly on Tom's real Mac (not a sandbox) — a full bug-fix pass, a
performance pass, two rounds of UI polish, a real GPU-acceleration
investigation (with an actual benchmark), and a real `python3 setup.py
py2app` build that was launched and confirmed working. See RUNNING.md #69
for the full narrative; this file is the structural snapshot.

## What this is

LUTier — a PySide6 desktop app for building and previewing LUT-based
color grades: load LUTs/CDLs from a folder, stack operations (LUT,
Printer Lights trim, ASC CDL SOP/SAT, Color Space Conversion, ACES DRT,
Stack Forking) non-destructively, preview against a Sample/Reference
image pair with a wipe compare (now with scroll-to-zoom/middle-drag-to-pan
in the Viewer), inspect via histogram/waveform/vectorscope/Graph (3D
point cloud + wireframe cage + 1D YRGB curves, with Grid density/Cage
opacity now living in a "3D Settings..." dialog off the ☰ menu), save
named "Stack Memory" snapshots, maintain reusable LUT/CDL/Image libraries,
undo/redo Stack edits, save/load whole sessions (`.ltr`), export the
baked result to `.cube`/`.clf`/`.3dl`/`.cub`/`.spi3d`/`.csp` via a
dedicated Export dialog, with every panel freely drag-to-rearrange/tab/
float. An Alt+hover inspector overlay works across panels (Flame-style).
Numeric fields in the Stack panel are horizontal click-drag-to-scrub spin
boxes (Shift = fine adjustment). Packaged as a native macOS `.app` via
py2app, icon included. In-app manual via Help > Manual, also on disk as
`USER_MANUAL.md`.

## What changed since 07-25 (RUNNING.md #69, this session)

**Bug fixes**: atomic (write-temp-then-rename) saves in `session_io.py`/
`stack_memory_file_io.py`/`workspace_io.py`; CDL `style="asc"` no longer
silently clips out-of-range input by default; undo history now resets on
New Session/Open Session; a dialog mistitled "Adapt Sample Image" now
correctly reads "Adapt Reference Image"; `retarget_from`/`retarget_to`
None-checks made consistent; the save path now surfaces a warning if it
has to drop a stage it can't serialize.

**Performance**: OCIO processors are now cached instead of rebuilt on
every repaint; undo-history writes are debounced; the waveform scope
computation is vectorized; the 3D viewer's GL repaint is gated on actual
visibility.

**UI polish (two rounds)**: hamburger menu (was ellipses) on every panel;
close-button restyle (0px inner border, consistently right-aligned);
reduced panel padding, with panel header + content unified into one
continuous QDockWidget-level border (previously two separately-bordered
shapes, kept apart historically to dodge a texture-bleed bug that a later
fix had already neutralized); Stack entry text truncates so the delete
control stays visible; drag-reorder shows a drop-position indicator;
Alt+hover inspector overlay (new `hover_inspector.py`); Graph panel's
Grid density/Cage opacity sliders moved into a "3D Settings..." dialog;
Stack numeric fields converted to `QDoubleSpinBox` with horizontal
drag-to-scrub (new `spinbox_scrub.py`, Shift = 1/10-speed fine
adjustment); Viewer gained scroll-wheel zoom (cursor-anchored) and
middle-drag pan, with 14 new tests covering the cursor-anchoring math in
`test_wipe_compare_widget.py`, plus a new `test_spinbox_scrub.py`.

**GPU-acceleration investigation**: benchmarked the live-preview compute
path directly — confirmed 100% CPU-bound (`getDefaultCPUProcessor()`
everywhere, no GPU path anywhere in the app except the unrelated 3D
point-cloud GL view), ~46ms/apply at 480x270 vs. ~764ms/apply at native
1920x1080 (near-linear with pixel count). Confirmed OCIO 2.5.2's GPU
shader API (`getDefaultGPUProcessor()`) works in this environment. Shipped
a band-aid for now — wired Viewer zoom level into the existing
resize-triggered preview-resolution-upgrade mechanism
(`sample_image_panel.py`) — and scoped the real GPU-acceleration work as
a dedicated future project (see `project_gpu_accelerated_preview.md` in
auto-memory).

**Real build**: `setup.py` was requesting `'arch': 'universal2'`, but
`lipo -info` confirmed the installed `numpy`/`scipy` wheels are arm64-only
(even under a universal2 python.org Python) — the exact failure mode
`BUILD_ON_MAC.md` already documented. Removed the `universal2` setting,
built single-arch (arm64) instead per that doc's own fallback guidance.
`python3 setup.py py2app` completed clean; `dist/LUTier.app` was launched
and confirmed working directly on Tom's Mac (splash → main window, no
errors) — the first real py2app build+launch confirmation this project
has had. `BUILD_ON_MAC.md` updated to state the confirmed root cause
instead of a hypothetical, and its stale "ACES DRT panel" / "never run on
real hardware" framing corrected.

## Architecture

Unchanged in shape from 07-25's checkpoint — `stack.py`'s Stage/Stack
model, Stack Forking, per-operator backends, library backends,
`undo_history.py`, `sample_image_panel.py`, `session_io.py`,
`floating_dialog.py`, `workspace_io.py`, QMainWindow/QDockWidget layout.
Two new modules this session: `spinbox_scrub.py` (drag-to-scrub event
filter) and `hover_inspector.py` (Alt+hover overlay), both now real,
live, imported dependencies (in `setup.py`'s `includes`, not orphaned).

## Test status

`python3 -m pytest -q` — **392 passed, 0 failed** (8 benign
`RuntimeWarning: divide/invalid value encountered in log2` from
`colour-science`'s ACES transfer function on edge-case inputs, not test
failures). Also independently confirmed via this project's own
`python3 test_X.py` convention: all 33 `test_*.py` files run directly,
383 individual `-- OK` checks, all clean. Run on real hardware (Tom's
Mac), not the old sandbox — the 19 previously-failing
`PermissionError`-on-`/tmp` sandbox artifacts from the 07-25 checkpoint
are gone because this environment doesn't have that constraint.

## Known limitations

- **No GPU acceleration anywhere in the color pipeline** — confirmed via
  direct benchmark this session (see above). Scoped as a dedicated future
  project, not yet started.
- **Plain `lut` stages don't go through OCIO at all** — `colour.LUT3D`/
  numpy-based, a separate code path from the OCIO-backed stages (ACES DRT,
  CDL, colorspace convert). Relevant to the GPU project: a GPU shader path
  would need to cover this path too, not just OCIO's own stages.
- **Live preview deliberately isn't LUT-baked** — `apply_stack_to_image_array`
  applies each stage's exact math rather than baking to one cube first, to
  avoid interpolation error. A future GPU shader path is a genuinely
  different (lower-precision, texture-based) numerical path — a real
  precision/exactness trade-off to make on purpose, not something to
  discover by accident later.
- **Single-arch build only, for now** — `setup.py` no longer requests
  `universal2`; the packaged `.app` matches whatever Mac builds it
  (arm64, on the machine this was built and tested on). Fine for peer
  testing; needs universal2 `numpy`/`scipy` wheels before wider
  distribution — see `BUILD_ON_MAC.md`'s troubleshooting section.
- Low-profile rectangular slider handles (still circular) — not started.
- Deriving a tertiary UI color automatically from the accent color (33%
  darker) — not started.
- Retarget is still single-pair.
- Stack Forking: no within-branch or cross-branch drag reordering yet.
- Graph panel's Y curve X-axis plotted against `input_r` specifically —
  still flagged, not solved.
- Cage mesh edge count grows fast with Grid density — unverified whether
  that's too slow in practice at high settings.

## Backlog

- **The GPU hardware-acceleration project** (see Known limitations and
  `project_gpu_accelerated_preview.md`) — the explicit next thing, per
  Tom: "let's make a project of introducing hwaccel soon."
- Low-profile slider handles (see Known limitations).
- Tertiary accent-derived color (see Known limitations).
- Universal2 build (needs universal2 numpy/scipy wheels first — see Known
  limitations).
- Stack Forking: within-branch drag reordering (see Known limitations).
- Retarget 4-field expansion (see Known limitations).

## Files

**Active** (`setup.py`'s `APP` + `OPTIONS['includes']`, i.e. the real,
current dependency list — 40 modules; two new since 07-25:
`spinbox_scrub`, `hover_inspector`):
`splash_launcher.py`, `main_window.py`, `aces_drt.py`, `app_logic.py`,
`atomic_write.py`, `browser_panel.py`, `cdl.py`, `cdl_file_io.py`,
`cdl_library.py`, `cdl_library_panel.py`, `clf_adapter.py`,
`color_balance.py`, `color_wheel_widget.py`, `colorspace.py`,
`colorspace_convert.py`, `floating_dialog.py`, `format_writers.py`,
`help_text.py`, `hover_inspector.py`, `image_library.py`,
`image_library_panel.py`, `lut_library.py`, `lut_library_panel.py`,
`lut_viewer_3d.py`, `panel_frame.py`, `printer_lights.py`, `retarget.py`,
`sample_image_panel.py`, `scopes.py`, `scopes_panel.py`, `session_io.py`,
`spinbox_scrub.py`, `stack.py`, `stack_memories_panel.py`,
`stack_memory.py`, `stack_memory_file_io.py`, `stack_panel.py`,
`style.py`, `theme_prefs.py`, `undo_history.py`, `wipe_compare_widget.py`,
`workspace_io.py`.

Modified this session (partial list — see RUNNING.md #69 for the full
per-file breakdown): `session_io.py`, `stack_memory_file_io.py`,
`workspace_io.py`, `cdl.py`, `undo_history.py`, `stack.py`,
`stack_panel.py`, `panel_frame.py`, `style.py`, `hover_inspector.py`
(new), `spinbox_scrub.py` (new), `lut_viewer_3d.py`,
`wipe_compare_widget.py`, `sample_image_panel.py`, `setup.py`,
`BUILD_ON_MAC.md`, `help_text.py`, `USER_MANUAL.md`.

**Legacy / orphaned** — unchanged, still candidates for deletion if the
repo needs tidying: `concatenate_panel.py`, `aces_drt_panel.py`,
`lut_controls_panel.py`, `collapsible_section.py`.

**Tests**: 33 `test_*.py` files, 392 passing under pytest (0 failing).
Two new test files this session: `test_spinbox_scrub.py`,
`test_wipe_compare_widget.py`.

**Docs**: `USER_MANUAL.md`/`help_text.py` (kept in sync with every UI
change this session), `BUILD_ON_MAC.md` (corrected this session — see
above), `RUNNING.md` (up to date through #69),
`KNOWN_GOOD_SUMMARY_2026-07-23.md` through
`KNOWN_GOOD_SUMMARY_2026-07-25.md` (previous checkpoints), this file.

**Build**: `rm -rf build dist && python3 setup.py py2app` produces
`dist/LUTier.app`, **single-arch (arm64)** as of this session (see
`setup.py`'s `OPTIONS` — `'arch': 'universal2'` was removed; add it back
once universal2 `numpy`/`scipy` wheels are available). Icon via
`make_icon.sh AppIcon_source.png` (macOS-only). **Confirmed working**:
built and launched on real hardware this session, splash screen through
main window, no errors — the first time this project's build has actually
been confirmed end-to-end rather than left as "should work, untested."

**Environment**: this session ran entirely on Tom's own Mac (Python 3.11.4
at `/Library/Frameworks/Python.framework/...`, itself a universal2 build,
py2app 0.28.10), not a sandbox — no venv workarounds, `TMPDIR` hacks, or
EGL stub needed this time. The one real constraint found:
`pip`-installed `numpy`/`scipy` on this machine are arm64-only wheels,
which is what forced the single-arch build decision above.
