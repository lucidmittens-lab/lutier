# LUTier — Known Good Summary (2026-07-24)

Checkpoint reference for picking this back up later, superseding
`KNOWN_GOOD_SUMMARY_2026-07-23.md` (kept alongside, not deleted, for
migration diffing). Full session-by-session history lives in
`RUNNING.md` (28 numbered sections as of this writing); this file is a
snapshot of where things stand right now, written specifically so a
future migration can get oriented fast without re-reading all of that.

## What this is

LUTier — a PySide6 desktop app for building and previewing LUT-based
color grades: load LUTs/CDLs from a folder, stack operations (LUT,
Printer Lights trim, ASC CDL SOP/SAT, Color Space Conversion, ACES DRT)
non-destructively, preview against a sample image with a wipe compare,
inspect via histogram/waveform/vectorscope/3D viewer, save named "Stack
Memory" snapshots, maintain reusable LUT and CDL libraries, undo/redo
Stack edits, save/load whole sessions (`.ltr`), export the baked result
to `.cube`/`.clf`/`.3dl`/`.cub`/`.spi3d`/`.csp`. Packaged as a native
macOS `.app` via py2app, icon included. A basic in-app manual is
available from Help > Manual, and also lives on disk as
`USER_MANUAL.md`.

## Architecture

- **`stack.py`** — Qt-free core: `Stack` holds an ordered list of `Stage`
  objects (kind = `lut` / `printer_lights` / `cdl` / `colorspace_convert`
  / `aces_drt`), each independently enabled/opacity-blended, baked to one
  combined `LUT3D` on demand. `Stack.size`/`domain_min`/`domain_max` are
  the grid-size/domain settings (Stack's "Edit Size/Domain..." dialog).
  A `lut` stage can carry `retarget_from`/`retarget_to` params
  (`retarget.py`) to reinterpret a LUT's declared color space without
  resampling its table.
- **Per-operator Qt-free backends** — `printer_lights.py` (points-based
  R/G/B trim, decode-gain-encode in a declared working space), `cdl.py`
  (ASC CDL SOP/SAT via OCIO's `CDLTransform`, ASC-clamped or no-clip
  style, independent SOP/SAT bypass), `colorspace_convert.py` (plain
  OCIO `ColorSpaceTransform`, scene-referred or display-referred, no
  rendering), `aces_drt.py` (real RRT+ODT / ACES 2.0 unified transform
  via OCIO's built-in ACES configs). All four follow the same
  "Qt-free, fully unit-testable, bake-to-LUT3D" pattern `stack.py` itself
  uses.
- **`app_logic.py`** — Qt-free backend for file I/O, image loading
  (`load_image_as_array`, `get_image_native_size`), LUT loading/baking,
  colorspace listing, and `default_sample_image_path()` (see "This
  session's changes" below for why that function looks the way it does).
- **Library backends** — `lut_library.py` / `cdl_library.py` /
  `stack_memory.py`, each a plain Qt-free container class holding
  ingested-by-value entries (never a live file reference) plus the
  matching `*_panel.py` Qt wrapper. `cdl_file_io.py` /
  `stack_memory_file_io.py` handle their respective import/export file
  formats (`.cc`/`.cdl`/`.ccc`, `.ltm`).
- **`undo_history.py`** — plain pointer-based list of `Stack.copy()`
  snapshots, Qt-free, uncapped (cheap because `Stage.copy()` never
  duplicates a LUT's actual table). Scoped to the working Stack only —
  not library management or the sample image.
- **`main_window.py`** — top-level window, wires every panel together via
  nested `QSplitter`s so every seam is user-resizable; also owns the
  native menu bar (File/Edit/Workspace + one category menu per panel with
  a "..." menu, redundant with that "..." button on purpose + Help).
- **`help_text.py`** — the Help > Manual popup's text, as a plain Python
  string constant (see below for why it's not a runtime read of
  `USER_MANUAL.md`).
- **Session format** — `.ltr` (JSON) via `session_io.py`, schema version
  3. Stores the Stack, grid size/domain, sample image reference, and all
  three libraries (LUT/CDL/Stack Memories). Older `.lutier`/v1/v2 session
  files still load (missing sections default to empty).

## Current panel layout

```
+----------+---------------------------+--------------+
| Browser  |                           |  Parameters  |
+----------+         Viewer            +--------------+
|   LUT    |     (top center)          |  3D Viewer   |
| Library  |                           |  (center     |
+----------+---------------------------+   right)     |
|   CDL    |    Stack -- the stage list +--------------+
| Library  |    (center, main area)    |    Scopes    |
+----------+                           |  (LR corner) |
|  Stack   |                           |              |
| Memories |                           |              |
| (bottom  |                           |              |
|   left)  |                           |              |
+----------+---------------------------+--------------+
                Export current stack as: [...] [Export...]
```

Every panel is independently toggleable from the **Workspace** menu (all
show on launch). Toggle checkboxes replaced the old on-screen toggle
buttons entirely. Browser/LUT Library/CDL Library/Stack Memories/Viewer/
Stack/Scopes each have a "..." corner menu that's exactly mirrored by a
same-named top-level menu-bar menu (both populate from the same
`populate_menu(menu)` method, so neither can drift out of sync).
Keyboard shortcuts: `⌘S`/`⌘⇧S`/`⌘O` for session Save/Save As/Open,
`⌘Z`/`⌘⇧Z` for Undo/Redo, `Shift+1`..`Shift+8` for the eight Workspace
panel toggles (assigned by list position, not hand-typed).

## Since the 2026-07-23 checkpoint

Everything below happened after that file was written, roughly
chronological (see `RUNNING.md` sections 25–28 for the earlier half of
this in full detail; the icon/build/manual items are this session's own
work, not yet in `RUNNING.md`):

1. **ASC CDL (SOP/SAT) stage** — new `cdl.py` backend + `stack_panel.py`
   inspector: color-balance wheel + master + fine R/G/B spinboxes for
   Slope/Offset/Power, a Sat slider, independent SOP/SAT bypass
   checkboxes, ASC-clamped vs No-Clip style choice, "Save to CDL
   Library..." button. Was the "next feature, not scoped yet" item in the
   07-23 backlog.
2. **CDL Library + LUT Library panels** — both ingest-by-value (parsed
   data copied in immediately; the original file can move/be deleted
   afterward with no effect), each with Import/Apply to Stack/Rename/
   Delete, both persisted in the session file (schema bumped to v3 for
   CDL Library, matching the earlier LUT Library bump).
3. **Browser recognizes CDL files** (`.cc`/`.cdl`/`.ccc`, tagged `[CDL]`)
   alongside LUT files, double-click/drag routes to the right library or
   Stack; search box supports wildcard patterns
   (`rec709*constrain709`-style, fixed to mean "contains this shape
   somewhere" after an initial anchoring bug).
4. **Stack Memory export/import** (`.ltm`, independent of the session
   file) via `stack_memory_file_io.py`.
5. **Menu-bar category menus + keyboard shortcuts** (described above).
6. **Undo/Redo** (`undo_history.py`, described above), plus the first
   `USER_MANUAL.md` (very basic, per Tom's request at the time).
7. **This session (2026-07-24):**
   - **Fixed a real py2app packaging bug**: `app_logic.default_sample_image_path()`
     used to build the bundled reference image's path as
     `Path(__file__).parent / "assets" / ...`. That's correct when
     running from source, but `app_logic.py` is an `includes` module, not
     the entry script — py2app zips it into
     `Contents/Resources/lib/python3XX.zip`, so at runtime `__file__`
     resolves *inside that zip archive*, and `.parent` lands one level
     too deep (`.../python311.zip/assets/...`, which doesn't exist) —
     `assets/` actually sits at `Contents/Resources/assets`, a sibling of
     the zip, not inside it. This is exactly why the bundled test/
     reference image went missing only in the built `.app`, never when
     running from source. Fixed by checking `sys.frozen` +
     `os.environ["RESOURCEPATH"]` (set by py2app's own `__boot__`) and
     anchoring there instead when frozen. Verified against the actual
     `dist/LUTier.app` bundle layout (parsed `python311.zip`'s contents
     directly to confirm `app_logic.pyc` really does live inside it).
     **The existing `dist/LUTier.app` predates this fix — needs a rebuild
     (`rm -rf build dist && python3 setup.py py2app`) to pick it up.**
   - **Investigated the app icon's smallest-size legibility complaint.**
     Confirmed the `.icns` file itself is well-formed (all 10 standard
     representations present and correctly encoded — legacy raw-ARGB for
     the two smallest slots, PNG for the rest, verified by parsing the
     icns chunk structure directly). The actual cause: the last icon
     design iteration (`icon_design/gen_icon7.py`) replaced the earlier
     bold caliper graphic with echoed/dimmed copies of the same fine
     color-mosaic ribbon (its own comment says "No caliper..." — a
     deliberate design choice at the time, not a bug), and that fine grid
     pattern aliases into indistinct noise at 16–32px with no caliper (or
     any bold shape) left to carry legibility. Confirmed via direct
     rendering at each target size. **Not yet fixed** — Tom's last
     direction ("just do the rough scaling of one of the current logos
     without caliper") wasn't conclusively actioned; this is a loose
     end, see Backlog below.
   - **Fixed Viewer image scaling.** The sample-image preview loaded once
     at a fixed 480px cap (`PREVIEW_MAX_DIMENSION`) for responsiveness,
     and `WipeCompareWidget.paintEvent` stretched that fixed-res pixmap
     to fill whatever size the panel was — so enlarging the Viewer panel
     upscaled a low-res image instead of showing more real detail.
     `WipeCompareWidget` now emits a `resized` signal on real size
     changes; `SampleImagePanel` listens (debounced 150ms) and reloads
     the source at a resolution tracking the panel's actual size —
     grow-only (never re-downsamples on shrink), capped at both the
     source's real native resolution and a 4096px safety ceiling. New
     `app_logic.get_image_native_size()` helper (cheap — reads the file
     header only, no raster decode) backs the "don't ask for more detail
     than the file has" cap.
   - **Rewrote `USER_MANUAL.md`** with real detail on color management:
     a "Stack operators" section (Color Space Conversion, LUT Retarget,
     Printer Lights, ASC CDL, ACES DRT, each explained by what real
     question it answers) and a "Grid size and domain" section, replacing
     the old one-line mentions. Basic Workflow section removed (redundant
     once the operators are documented directly). ACES DRT described
     matter-of-factly ("engages the selected ACES DRT for the
     scene-to-display transform") rather than as a "creative" step, per
     Tom's correction.
   - **Added Help > Manual** — new menu bar entry opening a `QDialog` with
     a `QTextBrowser` rendering the manual as real Markdown (headers,
     bold, the shortcut table). Text lives in `help_text.py` as a plain
     Python string constant, **not** a runtime read of `USER_MANUAL.md` —
     deliberately, to sidestep the exact same frozen-app resource-path
     bug class just fixed for the sample image (this module also gets
     zipped when frozen; a string constant needs no path resolution at
     all once imported). **Trade-off, flagged in that module's own
     docstring: `help_text.py` and `USER_MANUAL.md` are two copies that
     must be kept in sync by hand** — there's no build step that
     generates one from the other yet.

## Test status

`python3 -m pytest -q` — **189 passed, 19 failed**, with `PyOpenColorIO`
installed in the environment (`pip install opencolorio`, not in
`requirements.txt`'s literal pinning path but the same package). All 19
failures are the same pre-existing `PermissionError` on hardcoded
`/tmp/test_*` paths this project has seen in every prior session (owned
by a different uid from an earlier sandbox instance) — confirmed again
by hand, not a code issue. This matches the last fully-clean baseline
(189 passed after undo/redo landed in `RUNNING.md` #28) exactly — no
regressions from anything done this session.

**Environment note for next migration**: this sandbox does *not* have
PySide6 or a working Qt install (no `.eglstub` trick like the 07-23
session had) — everything GUI-touching this session was verified by
`py_compile` + reasoning against the actual widget code, plus one-off
standalone reproductions of the specific bugs (e.g. parsing the real
`.icns`/`python311.zip` byte layout, simulating `sys.frozen`+
`RESOURCEPATH` by hand) rather than driving real `QApplication`/
`MainWindow` objects. If picking this up somewhere with real Qt
available, re-verify the two GUI-layer fixes above (Viewer resize
behavior, Help > Manual rendering) against the actual running app.

## Known limitations

- **Disk is tight**: 7.0GB free on a 229GB volume (97% used), same
  situation flagged in the 07-23 summary. `dist/` (2.1GB) + `build/`
  (35MB) are regenerable py2app output sitting in the project folder —
  watch for these ballooning again if `python3 setup.py py2app` runs
  here; delete them first if space gets critical.
- **App icon**: smallest-size legibility issue investigated but not
  fixed (see above) — pinned per Tom, revisit when picked back up.
- **`setup.py`'s `includes` list looks incomplete** relative to the
  actual current set of local modules `main_window.py` transitively
  imports — `cdl_library_panel`, `lut_library_panel`, `lut_library`,
  `cdl_library`, `undo_history`, `cdl`, `colorspace_convert`,
  `color_balance`, `color_wheel_widget` aren't listed, even though
  `help_text` (added this session) and everything from the 07-23
  baseline is. Likely harmless in practice — py2app's `modulegraph`
  automatically walks plain `import`/`from...import` statements from the
  entry script regardless of the `includes` list (that list is mainly a
  manual backstop for imports the scanner can't see, e.g. dynamic ones),
  and every one of these is a plain top-level import — but this hasn't
  been verified against a real macOS build this session, so worth a
  double-check next time a `.app` gets built and run for real, same
  spirit as the Cocoa-platform-plugin warning already in `setup.py`'s own
  comments.
- **Retarget is still single-pair** (`stack_panel.py`'s `_build_lut_inspector`
  TODO, unchanged since 07-23): only picks the color space to reinterpret
  a LUT's already-declared input/output as, not full four-field override
  (current-in/current-out/target-in/target-out). Still flagged, not
  started.
- `QOpenGLWidget`/real on-screen rendering remains unverified in every
  sandbox this project has run in so far (no display anywhere it's been
  built) — the 3D viewer and real mouse drag-and-drop feel still need an
  actual on-screen pass whenever that's available.

## Backlog

- **Viewer upgrade (reach feature, actively being scoped as of this
  file)**: enable/disable the wipe compare (i.e. a plain single-image
  mode, not just before/after), an Image Library (parallel to the LUT/CDL
  Library pattern), and a two-image model — "Sample" (runs through the
  Stack, i.e. today's existing before/after image) plus a new "Reference"
  image the Stack never touches — with each side of the wipe divider
  independently selectable from three options: Sample Before, Sample
  After, Reference. Not started; see the chat alongside this file for
  the scoping discussion.
- **App icon small-size legibility** (see Known limitations above).
- **Retarget 4-field expansion** (see Known limitations above).

## Files

**Active** (imported by `main_window.py`, directly or transitively):
`main_window.py`, `browser_panel.py`, `stack_memories_panel.py`,
`lut_library_panel.py`, `cdl_library_panel.py`, `sample_image_panel.py`,
`stack_panel.py`, `scopes_panel.py`, `panel_frame.py`, `style.py`,
`stack.py`, `stack_memory.py`, `lut_library.py`, `cdl_library.py`,
`undo_history.py`, `help_text.py`, `app_logic.py`, `session_io.py`,
`clf_adapter.py`, `format_writers.py`, `wipe_compare_widget.py`,
`lut_viewer_3d.py`, `scopes.py`, `colorspace.py`, `colorspace_convert.py`,
`retarget.py`, `printer_lights.py`, `aces_drt.py`, `cdl.py`,
`cdl_file_io.py`, `stack_memory_file_io.py`, `color_balance.py`,
`color_wheel_widget.py`.

**Legacy / orphaned** — not imported anywhere, superseded by
`stack_panel.py`/the library panels but still sitting in the folder:
`concatenate_panel.py`, `aces_drt_panel.py`, `lut_controls_panel.py`,
`collapsible_section.py` (only referenced from `setup.py`'s `includes`
list, not from any active code). Same as 07-23 — still candidates for
deletion if the repo needs tidying, still flagging rather than removing
since that wasn't asked for.

**Tests**: 21 `test_*.py` files, 189 passing (see Test status above).

**Docs**: `USER_MANUAL.md` (rewritten this session, also embedded in
`help_text.py` for the in-app Help > Manual popup — keep both in sync by
hand), `BUILD_ON_MAC.md` (py2app packaging steps), `RUNNING.md` (full
session-by-session changelog, 28 sections, ~55KB),
`KNOWN_GOOD_SUMMARY_2026-07-23.md` (previous checkpoint), this file.
(`lutier_build-2_man.txt` in the project root is a stray plain-text
export of an older manual draft, not part of the active doc set.)

**Build**: `python3 setup.py py2app` (from a clean `build`/`dist` —
`rm -rf build dist` first if either exists) produces
`dist/LUTier.app`, universal2 (Intel + Apple Silicon). Icon via
`make_icon.sh AppIcon_source.png` (macOS-only, `sips`/`iconutil`).
**Must be rebuilt to pick up this session's `default_sample_image_path`
fix** — the `.app` currently in `dist/` predates it.

**Environment**: `requirements.txt` covers PySide6, pyqtgraph, PyOpenGL,
scipy, colour-science, colour-clf-io, numpy, Pillow, opencolorio. A real
macOS run needs all of these actually installed; this sandbox only has
the Qt-free subset installed as of this session (see Test status above)
plus PyOpenColorIO added mid-session to get a clean test baseline.
