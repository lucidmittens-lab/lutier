# RUNNING.md: how LUTier got here

The original dev log (entries #1 to #69, 200 KB, through 2026-08-08) is
archived unchanged as
[docs/archive/RUNNING_LOG_2026-08-08.md](docs/archive/RUNNING_LOG_2026-08-08.md).
Code comments that cite "RUNNING.md #N" mean an entry there; the index below
says what each one is. Later history is in git. How to run the app and the
tests is in [CLAUDE.md](CLAUDE.md) and `docs/BUILD_ON_*.md`.

## The spirit of it

The log was written by an AI assistant to Tom, the product owner, one
"round" at a time. For most of its life the assistant worked in a sandbox
that couldn't run Qt at all (no `libEGL`, no display, no GPU), so the
working loop was:

1. Tom asks for something, often as a numbered list or in his own words
   (quoted in the entries as "per Tom: ...").
2. The assistant builds it, keeping the maths and file I/O Qt-free so that
   part could be unit-tested in the sandbox against Tom's real LUT files.
3. Each entry says plainly what was verified and what wasn't ("only
   syntax-checked", "untested end-to-end", "not yet confirmed on real
   hardware").
4. Tom runs it on his Mac and reports back: "Whatever breaks, paste the
   error here and we'll fix it."
5. Fixes are labelled "Real bug" with a root cause. When a fix didn't hold
   on real hardware, the next entry says so ("#58 wasn't the full fix",
   "#60 tested 'not working' too") instead of quietly replacing it.

The habits that came out of this, which CLAUDE.md still asks for: report
"tests pass" and "confirmed on real hardware" separately, find the root
cause rather than patch symptoms, verify visual changes by pixel-sampling
rather than by eye, and keep Tom's exact wording when it defines the
requirement.

## Stated goals

- **A pro colour tool, judged against Resolve, Baselight and Flame.**
  Controls are modelled on theirs: LUT "strength" opacity per stage,
  Resolve/Baselight-style gain wheels, a Flame-style Alt+hover card, and
  free-form dockable panels. The current one-line goal (in CLAUDE.md): a
  free, Resolve-like tool (media → timeline → grade) with Baselight-grade
  colour management.
- **It started as a LUT utility:** browse and search LUT folders, inspect a
  LUT in 3D, preview it on an image, and convert between formats (`.cube`,
  `.clf`, Autodesk `.3dl` in Flame and Lustre dialects, Truelight/Baselight
  `.cub`). It could also retarget, resize and re-bound LUTs. Byte structure
  was validated against Tom's real 65³ LUT.
- **Non-destructive grading (#20, the turning point).** Tom's request:
  rather than separate panels that each overwrote "the current LUT", "start
  from an Identity or an imported LUT, add operations on top (including
  more LUTs), reorder them, and get one live preview/scopes reading the
  whole thing."
- **Correct colour science over convenience.** For example, #18 fixed an
  ambiguous Working Color Space in Printer Lights, and #69 fixed CDL
  silently clipping HDR values.
- **Feels like a real app.** Keyboard shortcuts for everything, menus that
  mirror each panel's ☰ menu, undo/redo, a user manual, a workspace that
  remembers your layout, a dark fixed palette that doesn't follow the OS
  theme, and a packaged macOS `.app` (py2app).
- **Speed like Resolve's.** Per Tom in #69: "I know the way that Resolve
  handles cubes is to convert them into shaders and keep them in a system
  library folder." That set up the GPU renderer project, done since.

## Broad mechanisms

- **The Stack** (`stack.py`): an ordered list of stages, each with its own
  `params`, `enabled` and `opacity`. It is evaluated two ways. Baking folds
  every stage over an identity grid into one LUT3D, for export and the 3D
  graph. Direct-pixel application runs the stages on real pixels, for the
  preview. Grid size and domain are Stack-level settings, not stages.
  Retarget is an option on a LUT stage.
- **Colour via OCIO.** The ACES DRT came first (#17); OCIO grading
  operators, CAMier and input/operator/display spaces followed (see
  CLAUDE.md).
- **Panels as `QDockWidget`s** (#40), with layout saved separately from the
  session through Qt's `saveState`/`restoreState`. Closing a floating panel
  hides it rather than redocking it (#61).
- **Files and libraries:** sessions (`.ltr`), Stack Memories (`.ltm`), the
  LUT Library and CDL files, and the Stills Gallery. Every save is atomic
  (write to temp, then rename; #69).
- **Viewer:** a wipe compare between before and after or a reference, with
  zoom and pan. Scopes are computed on a capped-resolution CPU sample.
- **Testing:** a plain-`assert` test file per module, and real hardware for
  anything GL or visual.

## Entry index

Entries #1 to #7 are the original how-to-run preamble.

**#8 to #19: from LUT utility to grading tool**

- #8 LUT Controls panel (Retarget, Resize, Input Bounds) and the wipe compare
- #9, #10, #11 tabs, then toggle buttons; Concatenate (LUT chains); preview mode and export-source options
- #12 bordered panels and layout fixes; #13 toggle buttons not showing state on macOS
- #14, #15 button styling and wildcard search (the first version was broken)
- #16 Export Still
- #17 Scopes and the ACES DRT (OpenColorIO arrives)
- #18 Printer Lights: real Working Color Space ambiguity bug; #19 Gang R/G/B

**#20 to #28: the Stack and app structure**

- #20 the unified, reorderable Stack replaces Controls/Concatenate/ACES DRT
- #21, #22, #23 Stack panel decluttered, spatial window layout, Info panel removed, Scopes and 3D Viewer moved
- #24 real bug in #23's group-splitter fix
- #25 Stack Memory export/import (`.ltm`) and the LUT Library panel
- #26 Browser sees CDL files; drag and drop into both libraries; buttons become "..." menus
- #27 menu-bar category menus and keyboard shortcuts
- #28 basic undo/redo and `USER_MANUAL.md`

**#29 to #39: floating panels and the GL crash hunt**

- #29 crash closing or redocking a floated panel (3D Viewer)
- #30 3D viewer blank while floated; #31 camera never centred on the data
- #32 `deleteLater()` and Python GC race causing a double free
- #33 suspect: the remember-workspace feature; #34 the actual root cause of the blank render, found from Tom's traceback
- #35 waveform: HDR-limiter values bunching at the bottom
- #36 semi-opaque 3D grid with an opacity slider
- #37 3D Viewer renamed "Graph", adds a 1D YRGB curve mode; #38 three bugs Tom caught in it
- #39 floating windows stayed above every app, not just LUTier

**#40 to #57: docking and the look**

- #40 free-form panel layout: migration to `QMainWindow` + `QDockWidget`; #41 confirmed on real hardware
- #42 to #45 beautification passes: accent colour, sliders and scrollbars, herringbone texture, separator grain
- #46 real bug: herringbone texture bleeding through panel title bars
- #47 keyboard shortcuts (11 of 38 requested); #49 manual updated for them
- #48 real bug: packaged app crashed on launch (py2app `ModuleNotFoundError`)
- #50 native-look float/close buttons on dock title bars
- #51 Shift-to-dock override: floating panels no longer eagerly redock
- #52, #53 UI cleanup: stray menu arrow, separator grain removed, rounded corners
- #54, #55 real bugs: square corners under rounded title bars (`WA_StyledBackground`) and the dock background bleeding through
- #56 Shift-gated pop-out, reverted in #57 ("not working," per Tom)

**#58 to #69: redocking, palette, cleanup**

- #58, #59, #60 closing a moved floating panel stranded it; three attempts, each tested "not working" on real hardware
- #61 Tom's fix: closing a floating panel hides it instead
- #62 fixed dark palette, independent of the OS theme, with blue panel-header accents; #63 corrected values and a hint-text colour
- #64 Viewer's empty space explicitly filled
- #65, #66, #67 panel grey taken down to #141414, header and hint text #BDBDBD, separators #0D0D0D; #66 also found that `palette(placeholder-text)` is lost after polish
- #68 dead-code cleanup; live preview no longer round-trips through PNG
- #69 one long session on real hardware: bug-fix pass (atomic saves, CDL HDR clipping, undo across sessions), performance pass (OCIO processor caching), two UI polish rounds (☰ menus, scrub spinboxes, Viewer zoom and pan, Alt+hover card), the GPU-preview investigation, and the first real py2app build (arm64)

## Open at the end of the log (2026-08-08)

- Low-profile rectangular slider handles. Still open: the handles are
  14 px circles in `style.py`.
- A tertiary UI colour derived automatically from the accent (33% darker).
  Done since: `style.get_tertiary_color`.
- The GPU acceleration project from #69. Done since: see
  `gpu_stack_renderer.py` and `docs/archive/` for the plan.
