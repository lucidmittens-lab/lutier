# LUTier — Known Good Summary (2026-07-24, #2)

Checkpoint reference for picking this back up later, superseding
`KNOWN_GOOD_SUMMARY_2026-07-24.md` (kept alongside, not deleted, for
migration diffing). A lot of ground was covered between that checkpoint
and this one across several migration sessions whose individual
transcripts weren't rolled into `RUNNING.md` (its last numbered entry,
#28, predates all of it) -- this file describes CURRENT state directly
from the code rather than trying to reconstruct a session-by-session diary
for that gap. Written right before starting on Stack Forking (see Backlog
below), specifically so that feature has a clean baseline to diff against.

## What this is

LUTier — a PySide6 desktop app for building and previewing LUT-based
color grades: load LUTs/CDLs from a folder, stack operations (LUT,
Printer Lights trim, ASC CDL SOP/SAT, Color Space Conversion, ACES DRT)
non-destructively, preview against a Sample/Reference image pair with a
wipe compare, inspect via histogram/waveform/vectorscope/3D viewer, save
named "Stack Memory" snapshots, maintain reusable LUT/CDL/Image
libraries, undo/redo Stack edits, save/load whole sessions (`.ltr`,
including workspace layout), export the baked result to
`.cube`/`.clf`/`.3dl`/`.cub`/`.spi3d`/`.csp` via a dedicated Export
dialog with optional Export Still/Import to LUT Library/Save Stack Memory
side effects. Packaged as a native macOS `.app` via py2app, icon
included. A basic in-app manual is available from Help > Manual, and
also lives on disk as `USER_MANUAL.md` (the two are kept in sync by hand,
see `help_text.py`'s docstring).

## Architecture

- **`stack.py`** — Qt-free core: `Stack` holds an ordered list of `Stage`
  objects (kind = `lut` / `printer_lights` / `cdl` / `colorspace` /
  `aces_drt`), each independently enabled/opacity-blended, baked to one
  combined `LUT3D` on demand (`bake_stack_to_lut3d`) or applied directly
  to real pixels for preview (`apply_stack_to_image_array`) — both share
  one fold (`_run_stages`/`_apply_stage_to_array`) so the two evaluation
  paths can't disagree. `Stack.size`/`domain_min`/`domain_max` are the
  grid-size/domain settings (Stack's "Edit Size/Domain..." dialog). A
  `lut` stage can carry `retarget_from`/`retarget_to` params
  (`retarget.py`) to reinterpret a LUT's declared color space without
  resampling its table.
- **Per-operator Qt-free backends** — `printer_lights.py`, `cdl.py`,
  `colorspace_convert.py`, `aces_drt.py`. All follow the same "Qt-free,
  fully unit-testable, bake-to-LUT3D" pattern `stack.py` itself uses.
- **`app_logic.py`** — Qt-free backend for file I/O, image loading/
  decoding (`load_image_as_array`, `get_image_native_size`,
  `render_full_resolution_still` for Export Still), LUT loading/baking,
  colorspace listing, `export_image_array_to_file`.
- **Library backends** — `lut_library.py` (fully embedded, survives a
  moved/deleted source file), `cdl_library.py` (copied-in values, nothing
  to reconstruct), `image_library.py` (path-referenced, re-decoded on
  session load — a same-session-plus-same-machine guarantee only, weaker
  than LUT/CDL Library), `stack_memory.py` — each a plain Qt-free
  container class plus a matching `*_panel.py` Qt wrapper. `cdl_file_io.py`
  / `stack_memory_file_io.py` handle their own import/export file formats
  (`.cc`/`.cdl`/`.ccc`, `.ltm`).
- **`undo_history.py`** — plain pointer-based list of `Stack.copy()`
  snapshots, Qt-free, uncapped. Scoped to the working Stack only — not
  library management or the sample image.
- **`sample_image_panel.py`** — Sample (runs through the Stack) and
  Reference (Stack never touches it) images, independently adaptable,
  wipe compare with an enable/disable toggle (plain single-image view
  when off) and independently-pickable left/right sources (Sample
  Before/Sample After/Reference). `has_sample_image()`/
  `render_full_resolution_after(stack)` are the two public entry points
  Export Still (both the Viewer's own menu action and the Export
  dialog's checkbox) share, so both produce identically processed stills.
- **`main_window.py`** — top-level window, wires every panel together via
  nested `QSplitter`s so every seam is user-resizable; owns the native
  menu bar (File/Edit/Workspace + one category menu per panel with a
  "..." menu, redundant with that "..." button on purpose + Help), and
  `get_workspace_state`/`load_workspace_state` (window size/panel
  visibility/splitter sizes, round-tripped through the session file).
- **`help_text.py`** — the Help > Manual popup's text, as a plain Python
  string constant (frozen-app resource-path safety, see that module's
  docstring), kept byte-identical to `USER_MANUAL.md` by hand.
- **Session format** — `.ltr` (JSON) via `session_io.py`, schema
  **VERSION 6**: Stack, sample image reference (Sample + optional
  Reference), Stack Memories, CDL Library, LUT Library, Image Library,
  and (new in V6) workspace layout. Every older version still loads,
  missing sections default to empty/no-op.

## Current panel layout

```
+----------+----------------------------+--------------+
| Browser  |                            |  Parameters  |
|  (top    |          Viewer            +--------------+
|   left)  |                            |    Scopes    |
+----------+----------------------------+--------------+
|  Image   |    Stack -- the stage list |  3D Viewer   |
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

No bottom export bar anymore (see below) — the window is just the three
columns plus the native menu bar. Ten independently toggleable panels
(Workspace menu, all shown by default): Browser/Image Library/LUT
Library/CDL Library/Stack Memories (`⇧1`-`⇧5`), Viewer/Stack (`⇧6`-`⇧7`),
Parameters/Scopes/3D Viewer (`⇧8`-`⇧0`, 3D Viewer's own pane again, split
back out of Scopes). Panel visibility, window size, and every splitter's
sizes now round-trip through the session file (`get_workspace_state`/
`load_workspace_state`, VERSION 6) — a saved session reopens looking the
way it did when saved.

File menu: New Session (`⌘N`), Open Session... (`⌘O`), Save Session
(`⌘S`), Save Session As... (`⌘⇧S`), Export... (`⌘E`). Edit menu: Undo/Redo
(`⌘Z`/`⌘⇧Z`).

## Export dialog (File > Export..., `⌘E`)

Replaced the old always-visible bottom-bar row (format dropdown +
"Export..." button) entirely. Shows a live specs readout (grid size,
domain, stage list — `palette(highlight)` blue, chosen for dark-mode
legibility, matches the accent color already used for the Stack list's
enabled-stage toggle) plus four checkboxes, none of which prompt any
further dialog of their own — everything's derived from the LUT's own
filename:

- **Export Still** — also saves a full-resolution graded still (same
  name as the LUT, `.png`), reusing `SampleImagePanel.
  render_full_resolution_after`. Skipped with a note in the confirmation
  if no sample image is loaded, doesn't block the LUT export either way.
  - **Import Still to Image Library** (secondary, indented, only enabled
    while Export Still is checked — force-unchecked/disabled the moment
    Export Still is turned off) — additionally ingests the just-written
    still into the Image Library via `ImageLibraryPanel.import_from_path`
    (a real re-decode, consistent with how every other Image Library
    import already works).
- **Import to LUT Library** — ingests the exported LUT via
  `LUTLibraryPanel.import_lut_object`, which takes the in-memory baked
  object directly rather than re-reading the just-written file (some
  export formats, `.3dl` especially, are lossy or restructure the table —
  see that method's docstring).
- **Save Stack Memory** — saves the current Stack as a new Stack Memory
  via `StackMemoriesPanel.save_memory`, named after the exported file.

## Test status

`python3 -m pytest -q` — **208 passed, 19 failed**, in a sandbox with
PySide6/pyqtgraph/PyOpenGL/scipy/colour-science/colour-clf-io/numpy/
Pillow/opencolorio all installed. All 19 failures are the same
pre-existing `PermissionError` on hardcoded `/tmp/test_*` paths this
project has seen in every prior session (owned by a different uid from an
earlier sandbox instance) — confirmed again by hand, not a code issue.
22 `test_*.py` files.

**This sandbox has a working headless Qt** via a stub `libEGL.so.1`
(`.eglstub/` in the outputs folder) — `QT_QPA_PLATFORM=offscreen` +
`LD_LIBRARY_PATH` pointing at that folder lets `QApplication`/
`MainWindow` actually construct and run real widget code, not just
`py_compile`. This session's GUI-layer changes (workspace persistence,
the whole Export dialog rework, all four checkboxes including the
secondary one's enable/disable coupling) were verified this way: real
`MainWindow`, real exports against temp files, real assertions on the
resulting LUT/still/library-entry/memory state, not just reasoning
against the widget code. Worth preserving/re-finding this trick in
whatever environment picks this up next if it's not already there.

## Known limitations

- **App icon**: smallest-size legibility issue (flagged 07-23, still
  open as of 07-24) — not touched this round.
- **`setup.py`'s `includes` list still looks incomplete** relative to the
  actual current set of local modules `main_window.py` transitively
  imports (`cdl_library_panel`, `lut_library_panel`, `lut_library`,
  `cdl_library`, `undo_history`, `cdl`, `colorspace_convert`,
  `color_balance`, `color_wheel_widget` aren't listed). Likely harmless —
  py2app's `modulegraph` walks plain imports from the entry script
  regardless of `includes` — but still unverified against a real build.
- **Retarget is still single-pair** (`stack_panel.py`'s
  `_build_lut_inspector`): only picks the color space to reinterpret a
  LUT's already-declared input/output as, not full four-field override.
- **QOpenGLWidget/real on-screen rendering** remains unverified with
  actual mouse input on an actual display — offscreen Qt proves the
  widget tree builds and wiring works, not real-monitor rendering or drag
  ergonomics.
- **Disk space** was tight as of 07-23/07-24 (regenerable `build`/`dist`
  py2app output ballooning); not re-checked this round, worth a look if
  packaging again.

## Backlog

- **Stack Forking (starting now, this is what this checkpoint is for)**:
  a new `fork` Stage kind — two branch stages run in PARALLEL against the
  same input (not serially, unlike every other stage), linearly
  crossfaded against each other via one `blend` param (inverse-linked A/
  B controls — moving one moves the other), then the fork stage's own
  ordinary `Stage.opacity` blends that combined result back against its
  own input, exactly like every other operator row's total-opacity
  control — no new mechanism needed there, it's the existing
  `_run_stages` fold applied one level up. Motivating use case: blend two
  LMTs. Next stage in the Stack sees the single blended signal, same as
  today — forking rejoins before continuing down the chain. Backend
  design (branches as ordinary `Stage` objects, reusing
  `_apply_stage_to_array` recursively, no nested forks in v1) is
  kind-agnostic; the open question is how much of that generality the
  branch-editing UI should expose at first (any stage kind per branch vs.
  LUT-only to start, matching the concrete "two LMTs" case with much less
  UI work) — to be settled with Tom before `stack_panel.py` UI work
  starts, backend/serialization work doesn't need to wait on that answer.
- **App icon small-size legibility** (see Known limitations above).
- **Retarget 4-field expansion** (see Known limitations above).

## Files

**Active** (imported by `main_window.py`, directly or transitively — 35
modules): `main_window.py`, `browser_panel.py`, `stack_memories_panel.py`,
`lut_library_panel.py`, `cdl_library_panel.py`, `image_library_panel.py`,
`sample_image_panel.py`, `stack_panel.py`, `scopes_panel.py`,
`panel_frame.py`, `style.py`, `stack.py`, `stack_memory.py`,
`lut_library.py`, `cdl_library.py`, `image_library.py`, `undo_history.py`,
`help_text.py`, `app_logic.py`, `session_io.py`, `clf_adapter.py`,
`format_writers.py`, `wipe_compare_widget.py`, `lut_viewer_3d.py`,
`scopes.py`, `colorspace.py`, `colorspace_convert.py`, `retarget.py`,
`printer_lights.py`, `aces_drt.py`, `cdl.py`, `cdl_file_io.py`,
`stack_memory_file_io.py`, `color_balance.py`, `color_wheel_widget.py`.

**Legacy / orphaned** — not imported anywhere, still sitting in the
folder: `concatenate_panel.py`, `aces_drt_panel.py`, `lut_controls_panel.py`,
`collapsible_section.py`. Same as every prior checkpoint — still
candidates for deletion if the repo needs tidying, still flagging rather
than removing since that wasn't asked for.

**Tests**: 22 `test_*.py` files, 208 passing (see Test status above).

**Docs**: `USER_MANUAL.md` (kept byte-identical to `help_text.py`'s
`USER_MANUAL_MARKDOWN` by hand — verified this checkpoint), `BUILD_ON_MAC.md`
(py2app packaging steps), `RUNNING.md` (session-by-session changelog, 28
numbered sections — stale as of this checkpoint, doesn't cover the Image
Library/wipe/workspace-persistence/Export-dialog work described above),
`KNOWN_GOOD_SUMMARY_2026-07-23.md` and `KNOWN_GOOD_SUMMARY_2026-07-24.md`
(previous checkpoints), this file.

**Build**: `python3 setup.py py2app` (from a clean `build`/`dist`) produces
`dist/LUTier.app`, universal2. Icon via `make_icon.sh AppIcon_source.png`
(macOS-only).

**Environment**: `requirements.txt` covers PySide6, pyqtgraph, PyOpenGL,
scipy, colour-science, colour-clf-io, numpy, Pillow, opencolorio. This
sandbox has all of them installed plus a working offscreen-Qt setup (see
Test status above).
