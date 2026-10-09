# LUTier — Known Good Summary (2026-07-24, #3)

Checkpoint reference for picking this back up later, superseding
`KNOWN_GOOD_SUMMARY_2026-07-24-2.md` (kept alongside, not deleted, for
migration diffing). That checkpoint was written right before starting
Stack Forking; this one is written right after finishing it (backend,
serialization, and two full passes of UI, the second a redesign per
Tom's feedback on the first) — so this file's job is to hand a clean,
fully-tested baseline to whatever comes next, including "a big feature
tweak" Tom flagged as imminent.

## What this is

LUTier — a PySide6 desktop app for building and previewing LUT-based
color grades: load LUTs/CDLs from a folder, stack operations (LUT,
Printer Lights trim, ASC CDL SOP/SAT, Color Space Conversion, ACES DRT,
and now Stack Forking — see below) non-destructively, preview against a
Sample/Reference image pair with a wipe compare, inspect via
histogram/waveform/vectorscope/3D viewer, save named "Stack Memory"
snapshots, maintain reusable LUT/CDL/Image libraries, undo/redo Stack
edits, save/load whole sessions (`.ltr`, including workspace layout),
export the baked result to `.cube`/`.clf`/`.3dl`/`.cub`/`.spi3d`/`.csp`
via a dedicated Export dialog with optional Export Still/Import to LUT
Library/Save Stack Memory side effects. Packaged as a native macOS `.app`
via py2app, icon included. A basic in-app manual is available from
Help > Manual, and also lives on disk as `USER_MANUAL.md` (the two are
kept in sync by hand, see `help_text.py`'s docstring).

## Architecture

- **`stack.py`** — Qt-free core: `Stack` holds an ordered list of `Stage`
  objects. `STAGE_KINDS`: `lut`, `printer_lights`, `cdl`, `colorspace`,
  `aces_drt`, `fork_split`, `fork_merge`. Every ordinary stage is
  independently enabled/opacity-blended, baked to one combined `LUT3D` on
  demand (`bake_stack_to_lut3d`) or applied directly to real pixels for
  preview (`apply_stack_to_image_array`) — both share one fold
  (`_run_stages`/`_apply_stage_to_array`) so the two evaluation paths
  can't disagree. `Stack.size`/`domain_min`/`domain_max` are the
  grid-size/domain settings (Stack's "Edit Size/Domain..." dialog). A
  `lut` stage can carry `retarget_from`/`retarget_to` params
  (`retarget.py`) to reinterpret a LUT's declared color space without
  resampling its table.
- **Stack Forking** (new this checkpoint, see its own section below) —
  `fork_split`/`fork_merge` are ordinary `Stage` kinds; any stage between
  a matched pair can carry `Stage.branch` (`None`/`"a"`/`"b"`).
  `_run_stages` evaluates both branches against the signal as it stood at
  the split, crossfades them per `fork_merge`'s `blend`, then blends that
  combined result against the pre-split signal via `fork_merge`'s own
  `opacity` — the same "fully-applied output blended against this
  stage's own input" rule every other stage's opacity already follows,
  just applied one level up.
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
  `_on_stack_changed` (the single place that re-bakes on every Stack
  edit) now wraps the bake in try/except — see Stack Forking below for
  why that became necessary.
- **`help_text.py`** — the Help > Manual popup's text, as a plain Python
  string constant (frozen-app resource-path safety, see that module's
  docstring), kept byte-identical to `USER_MANUAL.md` by hand.
- **Session format** — `.ltr` (JSON) via `session_io.py`, schema
  **VERSION 6** (unchanged this checkpoint — Stack Forking needed no
  version bump, see its section below): Stack, sample image reference
  (Sample + optional Reference), Stack Memories, CDL Library, LUT
  Library, Image Library, and workspace layout. Every older version still
  loads, missing sections default to empty/no-op.

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

Ten independently toggleable panels (Workspace menu, all shown by
default): Browser/Image Library/LUT Library/CDL Library/Stack Memories
(`⇧1`-`⇧5`), Viewer/Stack (`⇧6`-`⇧7`), Parameters/Scopes/3D Viewer
(`⇧8`-`⇧0`). Panel visibility, window size, and every splitter's sizes
round-trip through the session file (`get_workspace_state`/
`load_workspace_state`, VERSION 6) — a saved session reopens looking the
way it did when saved.

File menu: New Session (`⌘N`), Open Session... (`⌘O`), Save Session
(`⌘S`), Save Session As... (`⌘⇧S`), Export... (`⌘E`). Edit menu: Undo/Redo
(`⌘Z`/`⌘⇧Z`).

## Export dialog (File > Export..., `⌘E`)

Shows a live specs readout (grid size, domain, stage list —
`palette(highlight)` blue, chosen for dark-mode legibility) plus four
checkboxes, none of which prompt any further dialog of their own —
everything's derived from the LUT's own filename:

- **Export Still** — also saves a full-resolution graded still (same
  name as the LUT, `.png`), reusing `SampleImagePanel.
  render_full_resolution_after`. Skipped with a note in the confirmation
  if no sample image is loaded, doesn't block the LUT export either way.
  - **Import Still to Image Library** (secondary, indented, only enabled
    while Export Still is checked) — additionally ingests the
    just-written still into the Image Library.
- **Import to LUT Library** — ingests the exported LUT via
  `LUTLibraryPanel.import_lut_object`, using the in-memory baked object
  directly rather than re-reading the (possibly lossy-format) written
  file.
- **Save Stack Memory** — saves the current Stack as a new Stack Memory,
  named after the exported file.

## Stack Forking (new this checkpoint)

Per Tom: "Two stages in parallel that are then opacity blended with each
other... FORK - SPLIT be its own operator to separate the two chains,
then a FORK - MERGE that goes back to one chain... multiple items can be
added to each side of split." Landed in three passes this checkpoint:
backend + serialization, a first UI pass, then a UI redesign per Tom's
direct feedback on that first pass.

**Backend (`stack.py`)**: `fork_split`/`fork_merge` are ordinary `Stage`
kinds, not a special nested structure — `make_fork_split_stage()` /
`make_fork_merge_stage(blend=0.5, ...)`. Any stage between a matched pair
carries `Stage.branch` (`None`/`"a"`/`"b"`, set as a plain attribute —
none of the `make_xxx_stage` helpers take it as a constructor kwarg).
`_run_stages` tracks one open fork region at a time (nesting raises,
matching stack.py's own one-level rule) via a `fork_stack` list, running
each branch's tagged stages against the signal as it stood at the split,
crossfading via `fork_merge`'s `blend` (0.0 = 100% branch A, 1.0 = 100%
branch B), then blending that combined result against the pre-split
signal via `fork_merge`'s own `opacity` — deliberately the SAME
"total opacity control" every other operator row already has, no new
mechanism. `fork_split.enabled = False` makes the whole region — both
branches and the merge — a no-op with zero special-casing, since the
shared `running` signal is simply never touched inside an open region.
A branch tag outside any open region is inert (not an error) — most
often seen transiently right after adding a stage, before its branch is
picked, or after deleting one half of a fork pair. 37/37 tests in
`test_stack.py` cover blend endpoints/midpoint, multiple stages per
branch, branch-local enabled/opacity, total-opacity-blends-against-
pre-split-signal, split-disabled/merge-disabled no-ops, unmatched
split/merge raising, nested-fork raising, missing/stray branch tags.

**Serialization (`session_io.py`)**: every stage entry (not just fork
ones) now carries a `"branch"` field; `fork_split`/`fork_merge` get their
own (trivial) entry cases. No `SESSION_VERSION` bump needed — `"stages"`
already existed, these are just new values `"kind"` can take plus one new
per-entry field defaulting to `null`/`None` on load. A branch-tagged
stage that fails to reconstruct (e.g. a missing LUT file) is skipped
individually with its own warning, same as any other stage — there's no
"whole fork" unit to lose the way an earlier, superseded single-Stage
fork design would have needed. 15/15 tests in `test_session_io.py`.

**UI (`stack_panel.py`, `style.py`, `main_window.py`)**: "Add Fork
(Split + Merge)" (Stack's `...` menu or menu-bar entry) appends a
Fork Split/Fork Merge pair, styled as plain operator cards — identical
to every other stage row, no special decoration. Add any stage kind
after Fork Split, then use that row's own **Branch A / Branch B**
dropdown to place it; assigned stages render as a genuine two-column
table between Split and Merge — Branch A left half, Branch B right half,
a small column-header row, each branch evaluated top-to-bottom in stack
order. If one branch has more stages than the other, the shorter column
pads with blank cells strictly at the bottom (built via `zip_longest` on
each branch's own ordered stage list, so a gap can only ever appear past
the end of the shorter list — "can't checkerboard" falls out of that
structurally, not from an extra check). Grid cells are compact: elided
labels (full text still in the tooltip) and a narrow single-letter
A/B/— branch combo. Fork Merge's own inspector has the live A↔B blend
slider (no Update button, same real-time convention as Printer
Lights/CDL/ACES DRT/Color Space); its total opacity is deliberately just
the ordinary shared Opacity control below the stage list, not duplicated
in the inspector.

Selection had to move from "QListWidget row index" to "stage ID" as the
source of truth (`StackPanel._selected_stage_id`) — once a single visual
row could represent zero, one, or two actual stages (a grid row, or the
non-selectable column-header row), row index stopped being a stable
proxy for "which stage." Drag-reorder and Browser-LUT-drop targeting
were reworked around the same per-row `_row_stage_ids`/
`_row_insert_index` lookup tables this rebuilt, and both were verified
still correct headlessly. Reordering a stage BETWEEN branches, or within
one branch's own order, is not a drag target this round — only the
branch combo moves a stage between "unassigned"/A/B; genuinely
reordering two same-branch stages relative to each other isn't yet
exposed in the UI (worth flagging for next time, see Backlog).

**Crash guard**: deleting one half of a fork pair (either row's own "×")
is an entirely ordinary action for any other stage kind, but leaves an
unmatched split or merge — which `_run_stages` correctly raises on.
`MainWindow._on_stack_changed` (the one place that re-bakes on every
edit) now catches that and any other bake failure, surfaces it via a new
inline warning label in the Stack panel (`StackPanel.set_bake_error`)
rather than crashing or silently going stale. Verified headlessly: no
crash, correct warning text, `_current_lut`/3D Viewer preserved at their
last valid state, warning clears once the structure's fixed.

Docs (`help_text.py`/`USER_MANUAL.md`, kept byte-identical, re-verified
this checkpoint) have a "Stack Forking" section describing all of the
above from a user's perspective.

## Test status

`python3 -m pytest -q` — **226 passed, 19 failed**, 22 `test_*.py` files,
in a sandbox with PySide6/pyqtgraph/PyOpenGL/scipy/colour-science/
colour-clf-io/numpy/Pillow/opencolorio all installed. All 19 failures are
the same pre-existing `PermissionError` on hardcoded `/tmp/test_*` paths
this project has seen in every prior session (owned by a different uid
from an earlier sandbox instance) — confirmed again by hand, not a code
issue, all in `test_format_writers.py`/`test_image_preview.py`.

**This sandbox has a working headless Qt** via a stub `libEGL.so.1`
(`.eglstub/` in the outputs folder) — `QT_QPA_PLATFORM=offscreen` +
`LD_LIBRARY_PATH` pointing at that folder lets `QApplication`/
`MainWindow` actually construct and run real widget code. Every Stack
Forking UI claim above (grid structure/no-checkerboard, selection-by-id
surviving rebuilds, label truncation, compact branch combos, drag-reorder
and Browser-drop index translation, the crash guard) was verified this
way — real `MainWindow`, real `StackPanel`, real assertions on resulting
widget/Stage state, not just reasoning against the widget code. Still
worth an actual on-screen pass at some point (see Known limitations) —
offscreen Qt proves the widget tree builds and the data-level wiring is
correct, not real-monitor pixel layout or mouse-drag ergonomics.

## Known limitations

- **App icon**: smallest-size legibility issue (flagged 07-23) — not
  touched since.
- **`setup.py`'s `includes` list still looks incomplete** relative to the
  actual current set of local modules `main_window.py` transitively
  imports — likely harmless (py2app's `modulegraph` walks plain imports
  from the entry script regardless of `includes`) but still unverified
  against a real build.
- **Retarget is still single-pair** (`stack_panel.py`'s
  `_build_lut_inspector`): only picks the color space to reinterpret a
  LUT's already-declared input/output as, not full four-field override.
- **QOpenGLWidget/real on-screen rendering** remains unverified with
  actual mouse input on an actual display, INCLUDING the new fork
  two-column table's actual pixel layout (50/50 split, cell/placeholder
  heights lining up, truncation looking right at real widget widths) --
  built and headlessly verified at the data/widget-tree level only.
- **Stack Forking: no within-branch or cross-branch drag reordering yet**
  — the branch combo can move a stage to A, B, or unassigned, but two
  stages already in the same branch can't be drag-reordered relative to
  each other from the UI (only via the underlying Stack API). Worth
  raising with Tom if it turns out to matter in practice.
- **Disk space** was tight as of 07-23/07-24 (regenerable `build`/`dist`
  py2app output ballooning); not re-checked this round.

## Backlog

- **Whatever the next feature/tweak is** — Tom flagged one as imminent
  when this checkpoint was requested; no specifics captured yet.
- **Stack Forking: within-branch drag reordering** (see Known
  limitations above) — not requested yet, flagging proactively given how
  central drag-and-drop is to the rest of this panel.
- **App icon small-size legibility** (see Known limitations above).
- **Retarget 4-field expansion** (see Known limitations above).
- **A real on-screen QA pass of the fork table** (see Known limitations
  above) — first chance to actually look at 50/50 column proportions,
  elision thresholds, and empty-cell styling on a real display.

## Files

**Active** (imported by `main_window.py`, directly or transitively — 35
modules, unchanged from the prior checkpoint, no new files this round):
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

Modified this checkpoint: `stack.py`, `session_io.py`, `stack_panel.py`,
`style.py`, `main_window.py`, `help_text.py`, `USER_MANUAL.md`,
`test_stack.py`, `test_session_io.py`.

**Legacy / orphaned** — not imported anywhere, still sitting in the
folder: `concatenate_panel.py`, `aces_drt_panel.py`, `lut_controls_panel.py`,
`collapsible_section.py`. Same as every prior checkpoint — still
candidates for deletion if the repo needs tidying, still flagging rather
than removing since that wasn't asked for.

**Tests**: 22 `test_*.py` files, 226 passing (see Test status above).

**Docs**: `USER_MANUAL.md` (kept byte-identical to `help_text.py`'s
`USER_MANUAL_MARKDOWN` by hand — verified this checkpoint), `BUILD_ON_MAC.md`
(py2app packaging steps), `RUNNING.md` (session-by-session changelog —
stale, doesn't cover recent work), `KNOWN_GOOD_SUMMARY_2026-07-23.md`,
`KNOWN_GOOD_SUMMARY_2026-07-24.md`, `KNOWN_GOOD_SUMMARY_2026-07-24-2.md`
(previous checkpoints), this file.

**Build**: `python3 setup.py py2app` (from a clean `build`/`dist`) produces
`dist/LUTier.app`, universal2. Icon via `make_icon.sh AppIcon_source.png`
(macOS-only).

**Environment**: `requirements.txt` covers PySide6, pyqtgraph, PyOpenGL,
scipy, colour-science, colour-clf-io, numpy, Pillow, opencolorio. This
sandbox has all of them installed plus a working offscreen-Qt setup (see
Test status above).
