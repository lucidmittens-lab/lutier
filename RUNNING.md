# Running LUTier

## 1. Install dependencies

From inside `lut_tool/`:

```
pip3 install -r requirements.txt
```

## 2. Run it

```
python3 main_window.py
```

## 3. What to test

- Choose Folder → point it at a directory with some LUTs in it (the
  `samples/` folder in here works for a first try)
- Search box → filter the list by name
- Double-click a LUT → info panel should populate, 3D view should populate
- Drag to rotate the 3D view, try the grid density slider
- Export → pick a format, save, confirm the file actually shows up

## 4. Why this matters more than usual

`browser_panel.py`, `lut_viewer_3d.py`, and `main_window.py` have never
actually been run — only syntax-checked. My sandbox is missing a system
library (`libEGL`) that PySide6 needs even to import, so this is a bigger
leap of faith than the metadata patcher GUI was. The 3D viewer specifically
is the least certain part, since real OpenGL rendering is the one thing I
have zero ability to test here.

Everything these files call into — folder scanning, format loading,
CLF read/write, the retargeting math — is separately tested and confirmed
working against your real files. It's specifically the window/widget code
itself that's unverified.

## 5. Report back

Whatever breaks, paste the error here and we'll fix it — same loop as
every other tool we've built.

## 6. Recently added, worth testing specifically

- LUT Browser and LUT Info are now collapsible (click the header row) —
  confirm the arrow toggles and the 3D viewer actually reclaims the space.
- Grid density slider is back to live updates (no more drag-then-release).
- Sample Image panel now shows a real before/after: import an image, load
  a LUT, the "After" pane should update automatically. Untested end-to-end
  since I can't run Qt here — first real check is whether it visibly looks
  like what the LUT should do.
- New export formats: Autodesk `.3dl` (Flame and Lustre dialects) and
  FilmLight Truelight/Baselight `.cub`. Validated byte-structure against
  your real 65³ LUT, but never opened in an actual Lustre/Flame/Baselight —
  please try loading one back into whichever you have and confirm it reads
  correctly and looks right.

## 7. Backlog (not started)

- **`.aml` (Arri Look File 2) export** — deferred, see the assistant's
  explanation in chat: it's an XML container (LUT + CDL + curves), not a
  plain LUT, so it's a bigger separate project, not a quick add alongside
  `.3dl`/`.cub`.

## 8. Newest round — LUT Controls + wipe compare

- **LUT Controls** panel (collapsed by default, click to expand): Retarget
  (pick from/to color space, bakes a new LUT3D), Resize (resample to a new
  grid size), Input Bounds (edit domain min/max per channel, with a "link
  RGB" checkbox for the common case). All three replace the "current LUT"
  in place — 3D view, sample image preview, and info panel all update
  together. Backend math (bake_retargeted_lut/resample_lut/set_lut_domain
  in app_logic.py) is fully tested, including against your real 65³ LUT.
  What's NOT verified: the actual widget — combo boxes populating, text
  field validation, whether "link RGB" behaves the way you'd expect when
  toggled with existing values in the fields.
- **Before/after is now a wipe compare**, not side-by-side panels — drag
  the handle, double-click to re-center. This is a first pass on "make it
  attractive" specifically — functional and interactive, but the visual
  styling (handle look, label treatment, corner-fade behavior near the
  edges) is a starting point, not a settled design. Look at
  `wipe_compare_widget.py`'s top comment for the constants to tweak
  (`HANDLE_COLOR`, `HANDLE_GRIP_RADIUS`, `LABEL_BG`, etc.) if the direction
  needs to change before more time goes into it.

## 9. Latest round

- **Removed** the "Center Divider" button — double-click on the wipe
  compare still re-centers it, that was redundant.
- **Tabs.** Right side is now a QTabWidget: 3D Viewer / Sample Image /
  Controls / Concatenate / Info, instead of everything stacked vertically.
  Browser stays a persistent sidebar (you're likely picking files while
  doing other things), Export stays a persistent bar under the tabs (it
  acts on "the current LUT" regardless of which tab is showing). Untested:
  whether this actually feels less cramped, and whether Export being
  outside the tabs is intuitive or confusing — say if it should move.
- **Concatenate tab** — build an ordered chain of LUTs (top applied
  first), from the currently loaded LUT and/or any file on disk, reorder/
  remove as needed, then bake into one combined LUT at a chosen grid size.
  Backend (`concatenate_luts` in app_logic.py) is fully tested, including
  an order-matters check (chaining A→B must differ from B→A) and your real
  production LUT chained with a synthetic stage. What's unverified: the
  actual list widget — add/remove/reorder buttons, whether the chain order
  after reordering is really what gets applied.

## 10. Latest round

- **Toggle buttons instead of tabs.** A "Show:" toolbar across the top —
  Browser / 3D Viewer / Sample Image / Controls / Concatenate / Info — each
  independently shows/hides its panel. Not mutually exclusive like tabs:
  turn on 3D Viewer *and* Sample Image together if that's what you want on
  screen. Browser + 3D Viewer are on by default, everything else starts
  hidden. Untested: whether toggling actually reclaims layout space
  cleanly (the browser toggle specifically hides/shows a pane inside a
  QSplitter, which I can't verify behaves the way I expect without a real
  display).
- **Sample Image now has a Preview mode dropdown**: "Current LUT" (as
  before) or "Concatenate Chain" — the second applies whatever's currently
  built in the Concatenate tab directly to the sample image, live, as you
  add/remove/reorder LUTs there, without needing to hit the Concatenate
  button first. This uses a new, separate code path
  (`apply_lut_chain_to_image_array`) rather than baking a LUT first, so
  it's actually MORE accurate than what Concatenate itself produces (no
  grid-interpolation error) — confirmed by test. If the chain is empty,
  the preview says so instead of showing something misleading.

## 11. Latest round

- **Concatenate button now confirms.** It was working before — output was
  going straight into 3D Viewer / Sample Image / Info silently — but with
  no confirmation and those panels now toggle-off by default, clicking it
  looked like nothing happened. Now shows a dialog: "Combined N LUTs into
  a size³ LUT. It's now the current LUT."
- **Export source toggle**, next to the format dropdown: "Current LUT" or
  "Concatenate Chain". Chain mode bakes the chain fresh at export time
  (same math as the Concatenate button, same grid size field) — it's a
  read of "what's in the chain right now," not a requirement to hit
  Concatenate first. Untested: whether switching the combo and exporting
  actually produces a sane default filename (`concatenated_chain.<ext>`
  vs. whatever the loaded file was named).

## 12. Latest round -- borders, layout fix, styling

- **Bordered/titled panels.** New `panel_frame.py` wraps every toggleable
  panel (Browser, 3D Viewer, Sample Image, Controls, Concatenate, Info) in
  a bordered card with its name at the top. Toggling a panel off now hides
  the whole card in one `setVisible()` call — border, title, and content
  together, nothing left behind.
- **Fixed the "slider visible, graph gone" bug.** Root cause: several
  stretch=1 panels stacked in a fixed-height column were fighting for
  space, so the 3D view's OpenGL widget got squeezed toward 0px while its
  sibling controls row (not stretchable) kept its natural size — looked
  like a partial hide, was actually a squeeze. Fixed two ways: the right
  side is now inside a scroll area, so a tall combination of toggled-on
  panels scrolls instead of getting force-compressed; and the 3D view
  itself now has a 280px minimum height as a second line of defense
  regardless of what container it ends up in.
- **Visual pass** — new `style.py`, one app-wide stylesheet applied via
  `QApplication.setStyleSheet()`. Panel borders/titles, and — the more
  useful part — checkable toggle buttons now clearly show ON (filled,
  bold) vs OFF, since Qt's default checked-state styling is subtle enough
  to miss at a glance. Everything uses `palette(...)` colors, not
  hardcoded hex, so it should respect whatever light/dark OS theme you're
  running rather than fighting it. This is a first pass, not a final
  opinion — say if the direction should change (color choices, border
  radius, spacing all live in `style.py` as easy knobs).
- `collapsible_section.py` is no longer used anywhere (superseded by the
  toggle-button system) — left in the folder in case it's useful again,
  not deleted.

## 13. Latest round -- toggle buttons weren't showing state

Root cause, almost certainly: **macOS's native ("Aqua") widget style
largely ignores QSS on QPushButton** — background-color, border, and
checked-state styling in particular get silently dropped regardless of
how correct the stylesheet is. This is a known Qt/macOS thing, not a bug
in style.py's rules. Two fixes, stacked so one doesn't have to be perfect:

- `main()` now calls `app.setStyle("Fusion")` before applying the
  stylesheet — Fusion is Qt's own cross-platform style and actually
  honors QSS, at the cost of a little native-macOS look-and-feel.
- Independent of styling entirely: each toggle button's own TEXT now
  changes — `● Panel Name` when on, `○ Panel Name` when off. This can't
  fail to render regardless of style/theme quirks, so it's the backstop
  if Fusion's checked-state coloring still isn't obvious enough once you
  see it.

## 14. Latest round -- button styling gap + wildcard search

- **"Choose Folder" (and every other plain action button) had the same
  invisible-button problem**, but for a different reason than the toggle
  buttons: `style.py`'s border/background rule was only scoped to
  `QPushButton:checkable`, so ordinary one-shot buttons (Choose Folder,
  Import Image, Add From File, Export...) never got any border or fill at
  all. Fixed by moving border/background onto the base `QPushButton` rule
  (plus hover/pressed/disabled states), with `:checked` layering the
  highlighted look on top just for toggle buttons.
- **Wildcard search** in the LUT browser. Typing `*.cube`, `K_0??_*`, or
  similar now works as glob-style matching (via Python's `fnmatch`).
  Plain text with no wildcard characters still works exactly as before
  (matches anywhere in the filename) -- you don't need to learn wildcard
  syntax for the common case, it only kicks in if you actually type `*`,
  `?`, or `[...]`. Tested against the real files in `samples/`, including
  confirming `.3dl`/`.cub` files correctly stay excluded from browsing
  (they're export-only formats, not readable yet) regardless of the
  filter.

## 15. Latest round -- wildcard search was actually broken

Reported: `part*part` wasn't working, only `part*` (which is just a
prefix search wearing a costume). Real bug, found it: `fnmatch.fnmatch`
anchors to the *entire* string, so `"rec709*constrain709"` meant
"starts with rec709, ends with constrain709" -- literally end of string.
Any real filename with more after that (an extension, a version suffix,
basically always) failed to match. A trailing `*` alone happened to work
because "ends with anything" is trivially true, which is exactly why it
looked like wildcards "worked" for prefixes but did nothing useful beyond
that.

Fix: the pattern now always gets an implicit leading/trailing `*` if one
isn't already there, so it means "contains this shape somewhere," not "is
exactly this shape." `rec709*constrain709` now correctly matches
`rec709_to_p3d65pq_constrain709_v173.clf` despite `_v173.clf` coming
after. Added a test reproducing the exact bug shape against that real
filename in `samples/` -- confirmed failing before the fix, passing after.

## 16. Latest round -- Export Still

New "Export Still..." button in the Sample Image panel. Saves the graded
result as a real PNG/TIFF/JPEG file -- deliberately NOT the downsampled
480px preview array: it reloads the original image at full resolution
(`render_full_resolution_still`) and grades that, so what a peer opens is
an actual deliverable, not a thumbnail. Respects whichever Preview mode
is active (Current LUT or Concatenate Chain) the same way the on-screen
preview does, so what you export always matches what you were just
looking at. Backend fully tested, including a check that the output is
genuinely full resolution (not silently capped at the preview size) and
matches a from-scratch manual full-res apply exactly.

## 17. Latest round -- Scopes + ACES DRT

Two new toggle panels: **Scopes** and **ACES DRT**.

- **Scopes** reads whatever Sample Image is currently previewing (current
  LUT or Concatenate chain, whichever mode is active) and shows Histogram
  (R/G/B + luma curves), Waveform (per-channel or RGB Parade -- the classic
  three-side-by-side-colored-columns broadcast layout), and Vectorscope
  (Cb/Cr density plot with a graticule ring at 100% saturation). All three
  update live as the sample image, current LUT, or chain changes -- backend
  math (`scopes.py`: compute_histogram/compute_waveform/compute_vectorscope)
  is fully tested without Qt. Untested: the actual pyqtgraph rendering --
  confirm the histogram's three colored curves + white luma curve visibly
  shift when you load a different LUT, and that Vectorscope shows a tight
  cluster near center for a flat/desaturated image vs. spread toward the
  ring for a saturated one.
- **ACES DRT** lets you pick an ACES config (Studio recommended -- includes
  real camera working spaces: ARRI LogC3/4, RED Log3G10, Sony S-Log3,
  Canon Log, etc., not just synthetic ACES spaces), a working color space,
  a display, and a view (the actual Output Transform variant -- SDR/HDR,
  Rec.709/P3/Rec.2020), then Apply bakes the REAL RRT+ODT / Output
  Transform via PyOpenColorIO's built-in ACES config (no config file to
  download or ship -- OCIO >=2.3 bundles these) into a normal LUT. From
  that point it's just "the current LUT" like anything else -- viewable,
  previewable, chainable, exportable. Backend (`aces_drt.py`) is fully
  tested including a check that the baked LUT3D table exactly matches a
  direct apply on the same identity grid, and that different working
  color spaces genuinely produce different results for identical input.
  Untested: the actual widget -- confirm the four combos (Config ->
  Working Space / Display -> View) stay consistent as you change Config or
  Display, and that Apply visibly changes the 3D view/sample image to a
  plausible display-referred grade.
- New dependency: `opencolorio` (added to `requirements.txt`). If it's not
  installed, the ACES DRT panel disables itself with an explanatory
  message rather than erroring -- Scopes has no such dependency and works
  regardless.
- **Concatenate's button is now "Set as Current LUT."** It used to be two
  buttons (Concatenate, requiring 2+ chain items; a separately-added Set
  as Current LUT, requiring 1+) -- since the latter can do everything the
  former could plus work on a single-item chain, the old "Concatenate"
  button was pure redundancy and got removed. One button now: bake
  whatever's in the chain (1 or more items) and commit it as the current
  LUT, same one-click "lock this in" action as the ACES DRT panel's Apply
  button. Same tested backend as always (`concatenate_luts`, including
  the single-LUT-reproduces-itself check) -- this was a GUI
  simplification, not a logic change.
- **Printer Lights** group in Controls, below Input Bounds: R/G/B point
  sliders (-50..+50, 0 = no change), using the standard lab convention of
  ~12 points per stop (`printer_lights.py`: 0.025 log10 exposure units per
  point), plus a **Working Color Space** combo (same registry Retarget
  uses). Trims whatever's currently loaded -- applies AFTER the current
  LUT, as the final timing-pass role printer lights traditionally play,
  not a replacement for the grade underneath.
  **Color-space aware, not just a raw multiply:** a "stop" is only a real
  doubling in scene-linear light, so the points are converted to a gain,
  applied after DECODING the working space's own transfer function to
  linear, then RE-ENCODED -- correct whether the current LUT's output is
  log (ACEScct, camera logs), gamma (sRGB/Rec.709), or already linear
  (ACEScg). Verified against colour-science directly: in ACEScct, +12
  points now produces the same ~0.057 additive code-value shift
  (ACEScct's own documented per-stop constant) regardless of starting
  code value -- naive multiplication would have given a different shift
  depending on where you started, which was the old (pre-color-space-aware)
  behavior and is the actual bug this fixes. Backend fully tested,
  including zero points being a no-op for every curve type checked
  (log/gamma/linear), the log-space additive-vs-multiplicative proof
  above, and that picking a different working color space for the same
  points genuinely changes the result. Defaults to ACEScct. Untested: the
  actual sliders/combo/labels updating live and Apply doing something
  visible on a loaded image -- and specifically, picking the WRONG
  working color space for what's actually loaded won't error, it'll just
  make the points not mean real stops, so that's worth eyeballing on a
  known image (a stop up should look like a stop up) rather than just
  trusting the math.
- **Per-item opacity in Concatenate.** Each chain item now has a 0-100%
  opacity ("LUT strength," same control Resolve/Baselight offer per
  layer) -- select an item, adjust the spinbox below the list, list label
  shows a "(N%)" suffix when it's not 100. Backend
  (`concatenate_luts_with_opacity` / `apply_lut_chain_with_opacity` in
  app_logic.py) is fully tested: opacity=1.0 for every stage is pixel-
  identical to the original no-opacity concatenate_luts/
  apply_lut_chain_to_image_array (so nothing changes for chains that
  never touch opacity), opacity=0.0 is an exact no-op for that stage, and
  0.5 lands exactly at the arithmetic midpoint between a stage's input
  and output. Export's Concatenate Chain mode and the live sample-image
  chain preview both now go through the opacity-aware path. Untested: the
  actual spinbox/selection-sync widget behavior -- confirm selecting a
  different chain item updates the spinbox to THAT item's stored opacity
  (not the previous selection's), and that dragging opacity to 0% on one
  item visibly matches removing it entirely.
- **New Identity LUT** button in the Browser panel (next to Choose
  Folder), for starting from a neutral pass-through LUT instead of a real
  file -- e.g. to apply the ACES DRT bake "to nothing," or as a no-op
  first stage in a Concatenate chain. Backend (`create_identity_lut` in
  app_logic.py) is fully tested, including that it's a true pass-through
  on arbitrary points (not just identity-shaped) and a genuine no-op when
  chained ahead of a real LUT via concatenate_luts. Untested: the actual
  dialog (grid-size prompt) and that it correctly resets the 3D
  viewer/sample image to "unchanged" the same way loading a real identity
  file would.

## 18. Printer Lights: fixed a real Working Color Space ambiguity bug

Spotted by Tom: the Printer Lights "Working Color Space" combo was mostly
showing gamut names, not encoding/EOTF names, and it turned out to be a
genuine bug, not just a naming quirk. colour-science's own registry (what
that combo used to be populated from) has some gamut-only entries -- e.g.
plain `'Cinema Gamut'` -- whose `cctf_decoding` silently defaults to
**linear**, even though real Canon footage in that gamut is virtually
always Log2/Log3-encoded. Picking that entry for actual log footage
wouldn't error, it would just silently apply the wrong stop math (a
multiplicative gain on an already-linear assumption instead of the
correct decode/gain/re-encode), with no warning to notice.

**Fix:** `aces_drt.py` gained `decode_to_linear`/`encode_from_linear`,
backed by OCIO's ACES config registry, whose names correctly disambiguate
gamut from encoding (`'CanonLog2 CinemaGamut D55'` / `'CanonLog3 CinemaGamut
D55'` / `'Linear CinemaGamut D55'` as three distinct entries -- no
ambiguous bare-gamut fallback). Critically, these decode/encode **within
the same gamut** (via a curated known-pairs table plus a family/encoding
metadata search for the ACES config's other color spaces, e.g. `'Linear
ARRI Wide Gamut 3'` for `'ARRI LogC3 (EI800)'`) rather than normalizing
through ACES2065-1 -- an earlier draft of this fix did the latter and a
test caught that it introduces channel crosstalk (a diagonal R/G/B gain
only means what you'd expect relative to its OWN gamut's primaries;
converting through a different gamut's primaries first breaks that).
Where a config family has multiple linear candidates and neither the
curated table nor name-token matching can confidently pick one, this
raises a clear error rather than guessing wrong.

`printer_lights.py` gained `apply_printer_lights_in_working_space`, which
prefers this OCIO path when PyOpenColorIO is installed
(`aces_drt.OCIO_AVAILABLE`) and falls back to the original
colour-science-only `apply_printer_lights_in_colourspace` when it isn't.
`bake_printer_lights_on_lut` now goes through this auto-dispatching
function instead of calling the colour-science path directly. The GUI's
Working Color Space combo (`lut_controls_panel.py`) now populates from
`aces_drt.list_working_colourspaces()` when OCIO is available (the same
disambiguated registry the ACES DRT panel uses) instead of
`app_logic.list_available_colourspaces()`, with a hint label that
explains which registry is active and why it matters.

Backend fully tested (`test_aces_drt.py`, `test_printer_lights.py`),
including: the three Cinema-Gamut-style entries decoding distinctly from
each other: a same-gamut round trip recovering the exact original code
value; a non-grey code value proving `decode_to_linear('ACEScct')` matches
colour-science's own ACEScct decode (same AP1 primaries) rather than
silently landing in ACES2065-1's different primaries; the dispatch
function correctly using the OCIO registry when available (checked via an
OCIO-only working space name that doesn't exist in colour-science's
registry at all) and falling back identically to the colour-science path
when OCIO is monkeypatched unavailable. Untested: the actual combo
populating with the OCIO list and the hint label text switching correctly
based on whether PyOpenColorIO is installed -- confirm the combo shows
names like `CanonLog2 CinemaGamut D55` rather than plain `Cinema Gamut`,
and that Apply on real Canon-log footage now produces a believable stop
shift instead of a silently-wrong flat multiply.

## 19. Printer Lights: Gang R/G/B

New checkbox above the three sliders. Off (default): R/G/B behave exactly
as before, fully independent. On: dragging any one slider snaps the other
two to match immediately, and turning it on syncs G/B to R's current value
right away -- same "master trim" concept as the Gang button on Lift/Gamma/
Gain wheels in Resolve/Baselight (all three channels equal = a pure
exposure trim with no color skew). Pure GUI addition, `printer_lights.py`
and `aces_drt.py` untouched -- Apply still just reads whatever the three
sliders currently show. Untested (GUI-only, no backend change to test):
confirm dragging one slider while Gang is checked moves the other two in
real time, and that unchecking Gang lets you dial each channel
independently again from wherever they landed.

## 20. The Stack -- unified, reorderable operation stack (replaces Controls/Concatenate/ACES DRT)

Tom's request: rather than four separate panels that each destructively
overwrote "the current LUT" (Controls' Retarget/Resize/Bounds/Printer
Lights, Concatenate, ACES DRT), build one thing -- start from an Identity
or an imported LUT, add operations on top (including more LUTs), reorder
them, and get one live preview/scopes reading the whole thing. That's now
how the app works.

**New backend: `stack.py`.** A `Stack` is an ordered list of `Stage`
objects (`kind` one of `lut` / `printer_lights` / `aces_drt`, each with
its own `params`, `enabled` flag, and `opacity`). `bake_stack_to_lut3d`
folds every enabled stage over an identity grid into one flat
`colour.LUT3D` (for the 3D viewer/export); `apply_stack_to_image_array`
does the same directly on real pixels with no intermediate grid (for
live sample-image preview) -- same "grid-bake vs direct-pixel" split as
the old Concatenate/`apply_lut_chain_with_opacity` pair, just
generalized to every stage kind, not just LUTs.

This was possible without rewriting any math because every operation this
app has ever offered (Retarget, Printer Lights, ACES DRT, plain LUT
application) already worked by baking onto an identity grid into a flat
LUT3D -- see `stack.py`'s module docstring for the full reasoning,
including WHY Resize and Bounds are deliberately **not** stage kinds
(they became Stack-level `size`/`domain_min`/`domain_max` settings
instead -- Resize used to mean "resample an already-baked table," which
the Stack makes strictly more accurate by just recomputing every stage
fresh at the new size instead; Bounds was always metadata-only, never
touching the computed table, so there's nothing for a "Bounds stage" to
recompute mid-chain). Retarget is folded in as an optional per-stage
declaration on `lut` stages (`retarget_from`/`retarget_to`) rather than
its own stage kind, since `retarget_io` wraps a *specific* LUT to change
its declared color space -- it was never a general "convert array from
space A to B" tool independent of some underlying LUT.

Fully tested in `test_stack.py` (17 tests) without Qt: every stage kind's
single-stage-stack output matches the prior one-purpose bake function
exactly (`bake_retargeted_lut`, `bake_printer_lights_on_lut`,
`bake_aces_output_transform_to_lut3d`); opacity 0/0.5/1.0 match
`concatenate_luts_with_opacity`'s proven semantics; a disabled stage is an
exact no-op; stage order is respected and reordering matches building the
new order from scratch; the two evaluation paths (bake vs direct-pixel)
agree; domain metadata never touches the table; and resizing-by-rebaking
is demonstrably more accurate than the old resample-an-already-baked-table
approach (shown by comparing the two directly on a real production LUT).

**New GUI: `stack_panel.py`**, replacing `concatenate_panel.py`,
`aces_drt_panel.py`, and the Retarget/Resize/Bounds/Printer-Lights
sections of `lut_controls_panel.py` (those three files are still on disk
but no longer imported anywhere -- kept for reference, not wired up).
Layout: a reorderable stage list (Move Up/Down, Remove, per-stage Enabled
checkbox + Opacity spinbox), "Add stage" buttons (LUT File..., Identity
LUT..., Printer Lights, ACES DRT), a stage inspector below that swaps
content based on the selected stage's kind (LUT stage: name + optional
Retarget from/to; Printer Lights stage: the same sliders/Gang/working-space
combo from before; ACES DRT stage: the same config/working-space/
display/view combo chain from before) with an explicit "Update Stage"
button per inspector (matches every other panel's existing "Apply"-style
convention rather than continuously re-baking on every slider tick), and
a "Stack Output" group for the old Resize/Bounds settings (Grid Size,
Domain Min/Max with the same Link R/G/B convention Bounds always used),
also with an explicit Apply button. Every OTHER mutation (add/remove/
reorder/enabled toggle/opacity change) re-bakes and updates the preview
immediately, no separate commit step -- there's no more "Set as Current
LUT"/"Apply as Current LUT" button anywhere, because there's no longer a
separate "current LUT" slot to commit into. The stack's own bake **is**
the current result, continuously.

**main_window.py** rewired accordingly: the toggle toolbar is now Browser
/ 3D Viewer / Sample Image / **Stack** / Scopes / Info (Controls/
Concatenate/ACES DRT toggles are gone). Loading a file or an Identity LUT
from the Browser now **adds a stage** to the Stack instead of replacing
"the current LUT." Export always reads from the Stack's live bake (no
more Current-LUT-vs-Concatenate-Chain source combo -- there's only one
source now). `sample_image_panel.py` dropped its old Current-LUT/
Concatenate-Chain mode toggle entirely and just previews
`apply_stack_to_image_array` on whatever the Stack currently holds.

Untested (GUI-only, same libEGL wall as everything else in this app):
please run `main_window.py` and confirm the checklist at the top of
`stack_panel.py`'s docstring -- adding each stage type, editing a
selected stage's params via Update Stage, reordering/removing/toggling
stages, and Grid Size/Domain Apply all behave and update the 3D
view/sample image/scopes live as expected.

**Not done in this pass** (flagged for later, not started): drag-and-drop
reordering in the stage list (Move Up/Down buttons only, for now); a way
to duplicate/reuse the Stack's own current bake as a new stage (e.g. to
branch); saving/loading a Stack definition to/from disk (right now a
Stack only exists for the life of the running session).

## 21. Stack panel decluttered + spatial window layout

Two follow-up requests after the Stack rewrite (#20):

**Removed "LUT File..." / "Identity LUT..." buttons from the Stack
panel.** They were exact duplicates of what the Browser panel already
does (double-click a file / New Identity LUT), both of which already
route into the Stack via `add_lut_stage_from_path`/`add_identity_stage`
(see #20) -- no functionality lost, just one fewer redundant way to do
the same thing. The Stack panel's own "Add stage" row is now just
Printer Lights / ACES DRT, the two stage kinds that don't have another
entry point.

**`stack_panel.py`'s `StackPanel` is now a `QObject`, not a `QWidget`.**
It hands out two separately-placeable widgets instead of being one
combined panel: `list_widget_area` (the stage list, reorder/remove,
Enabled/Opacity, Add Printer Lights/ACES DRT) and `params_widget_area`
(the per-stage inspector + Grid Size/Domain settings) -- both built from
the same underlying `Stack`/selection state, so editing a stage in
`params_widget_area` still targets whatever's selected in
`list_widget_area`. This is what let the two pieces move to different
parts of the window (see below) without duplicating any logic -- every
method except the two `_build_*_ui` layout functions is unchanged from
before.

**`main_window.py`'s layout is now spatial**, per Tom's requested
arrangement, via nested `QSplitter`s instead of one scrolling vertical
stack:

- Browser -- left column
- 3D Viewer / Sample Image / Scopes -- share a row across the top of the
  center column (all three are "preview the current stack" tools; 3D
  Viewer wasn't explicitly placed in the request, so it was grouped here
  with the other two preview panels as the closest fit -- flag if that's
  wrong and it can move)
- Stack (the stage list) -- center, the main/largest area, below the
  preview row
- Info -- bottom of the center column
- Stack Params -- right column

Every seam is a draggable splitter handle now (initial sizes set via
`setSizes`, but freely resizable), replacing the old QScrollArea-based
"stack everything vertically and scroll if it doesn't fit" approach. The
toggle-button toolbar order was changed to match, left-to-right/
top-to-bottom: Browser, 3D Viewer, Sample Image, Scopes, Stack, Info,
Stack Params -- Stack and Stack Params are now independent toggles
(both default-visible) rather than one combined "Stack" toggle, since
they're two separate frames in two separate places.

Untested (GUI-only): please run `main_window.py` and confirm the layout
actually matches the intended arrangement, that all seven toggle buttons
show/hide the right frame in the right spot, and that dragging splitter
handles resizes panels sensibly instead of squashing something unusable.

## 22. Info panel removed, toggle buttons moved to the nearest edge/corner, Browser decluttered

Three follow-up requests after #21:

**Info panel removed entirely.** It always showed `get_lut_info()` on
whatever the Stack currently baked to -- with everything now unified into
the Stack, that was always just describing the same table (size, type, a
value range), rarely worth a whole panel. `main_window.py` no longer
builds `info_text`/`info_frame`, `_on_stack_changed` no longer computes
or displays it, and `get_lut_info`/`QTextEdit` are no longer imported.
`center_splitter` is now just the top preview row + Stack (was three
widgets, now two).

**Toggle buttons moved off one central toolbar onto whichever edge/corner
is closest to their panel**, via a new `_build_toggle_row(left, center,
right)` helper (any group can be empty) used twice: once for a top row
(Browser flush left, 3D Viewer/Sample Image/Scopes centered, Stack Params
flush right) and once for a bottom-center-only row (just Stack's toggle,
directly under the stage list it controls). `PANEL_TOGGLES` split into
four grouped lists (`_TOP_LEFT`/`_TOP_CENTER`/`_TOP_RIGHT`/
`_BOTTOM_CENTER`) that concatenate back into the original flat
`PANEL_TOGGLES` wherever the grouping doesn't matter (applying default
visibility). Stack and Stack Params are still independent toggles, same
as #21.

**Browser panel decluttered:**
- The folder path label now elides ("...") to fit the panel's current
  width instead of word-wrapping across several lines -- a long path was
  eating most of the narrow left-column panel's height just to display
  itself. Re-elides on every resize (`resizeEvent` override) since
  available width changes as the splitter does; the full path is still
  available as a tooltip on hover. `set_folder`/loading a folder is
  otherwise unchanged.
- The explanatory sentence next to "New Identity LUT..." ("No LUT to
  work from? Start from a neutral pass-through LUT:") is gone -- the
  button now sits directly in the folder row next to "Choose Folder...",
  no label needed. Functionality (still emits `identity_lut_requested`,
  still prompts for a grid size) is unchanged.

Full regression pass: all `py_compile` clean, all test modules still
pass (same two pre-existing `/tmp`-permission sandbox failures as always,
unrelated -- see earlier entries). Untested (GUI-only): please run
`main_window.py` and confirm the toggle buttons now sit where expected,
the Browser's folder path truncates instead of wrapping when you pick a
long path, and Info is gone without anything else breaking.

## 23. Two layout quirks fixed, Scopes/3D Viewer moved

Tom caught two real bugs right after #22's layout landed, plus asked for
a further repositioning of two panels:

**Quirk 1 -- re-enabling a panel widened the whole window.** Qt grows a
top-level window to satisfy a newly-shown child's minimum size when the
splitter has nowhere else to take the space from, but it never shrinks
the window back down when that panel is hidden again -- so toggling Stack
Params off then back on left the window permanently wider than before.
Fixed in `_on_panel_toggled`: snapshot `self.size()` before the
visibility change, then restore it via `QTimer.singleShot(0, ...)`
afterward (deferred, not immediate -- Qt's own auto-grow pass isn't
guaranteed to happen synchronously with `setVisible()`, so an immediate
`resize()` call could get silently overridden a moment later by a
LayoutRequest event Qt was still processing). The splitter still
redistributes space among visible panes exactly as before; it just can no
longer resize the window itself to make room.

**Quirk 2 -- disabling every panel in the old top row left empty space.**
When 3D Viewer, Sample Image, and Scopes (the old shared top-center row,
before this section's repositioning) were all switched off, the row's
own wrapping splitter didn't disappear -- QSplitter only collapses a pane
when THAT pane's own widget is hidden; hiding everything INSIDE a nested
splitter doesn't make Qt hide the nested splitter itself, so it just sat
there as reserved-but-empty space. Fixed generally (not just for that one
row): `GROUP_SPLITTER_MEMBERS` maps every "splitter that's itself a pane
in another splitter" to its member frames, and
`_sync_group_splitter_visibility` (called after every toggle, plus once
after applying initial default visibility) sets that splitter's own
`setVisible()` to "is at least one of my children visible right now."
Applies to both `center_splitter` and the new `right_splitter` (below).

**Repositioned per Tom's request: Scopes to the lower-right corner, 3D
Viewer to center-right, Stack Params confirmed top-right.** The right
column is now a single vertical splitter (`right_splitter`) with Stack
Params on top, 3D Viewer in the middle, Scopes at the bottom -- the
"LR corner" spot. The old 3-way horizontal `top_splitter` (3D Viewer /
Sample Image / Scopes sharing the top-center row) is gone entirely --
Sample Image is now the only thing at top-center, directly above Stack in
`center_splitter` (2 panes instead of 3, no more nested horizontal
splitter needed there either). Toggle buttons followed: 3D Viewer's moved
from top-center to top-right (next to Stack Params -- there's no toggle
row that sits at a true vertically-centered "middle-right" position, so
top-right is the closest fit, noted honestly in main_window.py's
docstring rather than pretending it's exact), and Scopes' moved from
top-center to a new bottom-right group, matching its new lower-right
corner position.

Full regression pass: all `py_compile` clean, all test modules unaffected
(this was all `main_window.py`/`browser_panel.py` GUI-layer work, no
backend changed). Untested (GUI-only, the usual libEGL wall): please run
`main_window.py` and confirm (1) toggling Stack Params (or any panel) off
and back on leaves the window exactly the size it was, (2) turning off
all of Stack Params/3D Viewer/Scopes collapses the whole right column
rather than leaving a blank strip, and turning any one back on restores
it, (3) Scopes now sits bottom-right and 3D Viewer center-right as
described.

## 24. #23's group-splitter fix had a real bug -- fixed

Tom reported the app came up completely broken right after #23: only
Browser visible, every other toggle button doing nothing. Real bug, not
an environment issue -- `_sync_group_splitter_visibility` was checking
`frame.isVisible()` to decide whether a group splitter (`center_splitter`,
`right_splitter`) should show itself, and `isVisible()` was the wrong
property for this, for two compounding reasons:

1. `isVisible()` means "visible ON SCREEN right now," which requires
   EVERY ancestor -- including the very group splitter this check was
   about to decide the fate of, and the top-level window itself -- to
   already be visible too. `_sync_all_group_splitters()` runs once at the
   end of `_build_layout()`, which happens inside `__init__`, before
   `main()` ever calls `window.show()` -- so at that first call, EVERY
   widget's `isVisible()` was False regardless of what `setVisible()` had
   just been called with, because the top-level window itself wasn't
   showing yet. Both `center_splitter` and `right_splitter` read "no
   children visible" and hid themselves immediately -- leaving only
   Browser (a direct, non-grouped `main_splitter` child, unaffected by
   this check) on screen. That's the "only Browser visible" symptom
   exactly.
2. Worse, it couldn't self-correct afterward: once a group splitter is
   hidden, its children's `isVisible()` STAYS False forever after, purely
   because their parent is hidden -- not because anyone asked them to
   hide. So every later toggle click set the clicked frame's own flag
   correctly, but the re-sync check still saw "no children visible" (same
   ancestor problem) and never re-showed the group splitter -- the panel
   toggled on, invisibly, behind a still-hidden parent. That's the
   "toggles do nothing" symptom.

Fixed by checking `not frame.isHidden()` instead of `frame.isVisible()`.
`isHidden()` reflects only whether `hide()`/`setVisible(False)` was
explicitly called on THAT SPECIFIC widget -- independent of ancestor
state or whether the top-level window has ever been shown -- which is
exactly "did the matching toggle button turn this panel off," the actual
question being asked. No more circularity, no more pre-show-time false
negative. Verified by hand-tracing every default-visibility and toggle
sequence from #23 against the corrected logic (see the code comment on
`_sync_group_splitter_visibility` for the full trace); full regression
pass unaffected (backend-only test suite, this bug was entirely in
GUI-layer widget-visibility logic). Sorry for shipping that one broken --
please re-run `main_window.py` and confirm the app actually comes up
usable this time, not just that this specific bug's symptom is gone.

## 25. Stack Memory export/import (.ltm) + new LUT Library panel

Two follow-up requests: hint-text cleanup landed first (three remaining
explanatory `QLabel`s removed from Stack Memories, CDL Library, and the
Sample Image "Adapt Reference Image" dialog -- no functional change), then:

**Stack Memories can now Export.../Import... a single memory to/from its
own `.ltm` file**, independent of the whole `.ltr` session (new
`stack_memory_file_io.py`, reusing `session_io.py`'s existing
`stack_to_session_dict`/`stack_from_session_dict`). Lets one saved memory
be shared, backed up, or moved into a different session without
exporting/opening the entire working session. Imported memories get a
fresh id (never collide with what's already in the library) and surface
per-stage warnings the same way opening a session does. Fully tested
(`test_stack_memory_file_io.py`) including a real file round trip and
rejecting a non-Stack-Memory file.

**New "LUT Library" panel** (`lut_library_panel.py` + `lut_library.py`),
left column between Browser and CDL Library per Tom. Import... reads any
LUT file `app_logic.load_lut_any_format` already supports and keeps the
*loaded LUT object itself* -- table, domain, name -- not just its file
path. This is the actual point (per Tom, "so they can't go offline if the
session is transferred"): `session_io.py` (bumped to VERSION 4) now
embeds a LUT Library entry's full table directly into the `.ltr` JSON
(`lut_to_embedded_dict`/`embedded_dict_to_lut`), and "Apply to Stack"
adds a new Stack stage straight from the entry's in-memory LUT
(`StackPanel.add_lut_stage_from_library_entry`), tagged
`source_kind="embedded"` so *that* stage's table gets embedded too --
independent of whether the library entry it came from still exists.
Verified end-to-end by hand (not just unit tests): imported a real
`.cube`, applied it to the Stack, deleted the source file entirely,
saved the session, and reloaded it fresh -- the Stack stage's table
matched the original exactly, zero warnings, zero dependency on the
deleted file. Old (pre-version-4) session files with no `lut_library`
key at all still load fine with an empty LUT Library.

Backend fully tested: `test_lut_library.py` (entries never read back
from `source_path`, copy/rename/remove semantics) and new coverage in
`test_session_io.py` (LUT3D/LUT1D embedded-dict round trips through real
JSON, an "embedded" lut stage surviving `stack_to_session_dict`/
`stack_from_session_dict`, the full `save_session`/`load_session` round
trip via a real temp file, and the version-3-compat default). Full
regression: 178 passed (was 159 -- +19 new), same pre-existing
`/tmp`-permission failures as always, nothing new broken.

Untested (GUI-only, same libEGL wall as everything else): please run
`main_window.py` and confirm (1) the LUT Library panel shows up between
Browser and CDL Library and its Workspace-menu toggle works, (2)
Import... on a real LUT file adds an entry, Apply to Stack adds a stage,
and deleting/renaming the source file afterward doesn't break either,
(3) Rename/Delete on a LUT Library entry work and ask before Delete, (4)
Stack Memories' new Export.../Import... buttons produce/read a real
`.ltm` file via the actual file dialogs (the automated test drives the
same code path directly, skipping the dialog itself).

## 26. Browser sees CDL files, drag-and-drop into both libraries, buttons -> "..." menus

Three follow-up requests:

**Browser now lists ASC CDL files (`.cc`/`.cdl`/`.ccc`) alongside LUT
files.** `app_logic.scan_lut_folder` gained an `extra_extensions` param
(purely additive/opt-in -- existing callers with no param passed are
unaffected, confirmed by `test_default_call_excludes_cdl_files`);
`browser_panel.py` feeds it `cdl_file_io.CDL_EXTENSIONS` (new constant
there). Each list item remembers its own kind (LUT vs CDL); CDL items get
a visible `[CDL]` suffix so a mixed folder doesn't look like all one
thing. Double-clicking a LUT file behaves exactly as before (adds a Stack
stage directly) -- double-clicking a CDL file now emits a new
`cdl_file_selected` signal instead of trying (and failing) to load it as
a LUT, routed to `CDLLibraryPanel.import_from_path` (there's no "just
apply this file directly" concept for a CDL the way there is for a LUT --
a Stack CDL stage is built from parameters, not loaded from a file, so
the Library is the right landing spot).

**Drag-and-drop from Browser into both libraries.** Two new MIME types
(`LUT_PATH_MIME`, already existed; new `CDL_PATH_MIME`) -- the Browser's
draggable list picks whichever one matches the dragged item's kind.
`LUTLibraryPanel`/`CDLLibraryPanel` each gained a small drop-enabled
`QListWidget` subclass (`_LUTDropListWidget`/`_CDLDropListWidget`,
mirroring `stack_panel.py`'s existing `_StackListWidget` drop handling)
that routes a drop straight into a new shared `import_from_path(path)`
method -- the same method the "..." menu's Import... action and (for
CDL) Browser double-click all call, so behavior can't drift between the
three entry points. Dragging a LUT onto the Stack panel still works
exactly as before, unchanged.

**Buttons moved into "..." header menus.** `CDLLibraryPanel`,
`LUTLibraryPanel`, and `StackMemoriesPanel` all had their always-visible
button rows (Import/Apply/Rename/Delete, Save/Load/Update/Rename/Delete/
Export/Import) replaced with a single corner "..." `QToolButton`+`QMenu`
per panel, wired via `PanelFrame`'s `corner_widget` in `main_window.py` --
the exact same treatment `stack_panel.py` gave the Stack panel's own
header menu a few rounds back. Double-click (Apply to Stack / Load) and
drag-and-drop both still work independent of these menus.

Verified end-to-end by hand: pointed the Browser at `samples/` (which has
real LUT files plus `samples/cdl/` with real `.cc`/`.cdl`/`.ccc` files,
see `test_cdl_file_io.py`) -- found 4 LUT + 3 CDL files correctly tagged
and labeled; double-clicking a LUT file added a Stack stage; double-
clicking a CDL file (`multi.ccc`, 2 ColorCorrections) added 2 entries to
the CDL Library; confirmed each list item's drag `mimeData()` carries
the right MIME type for its kind and nothing else; confirmed all three
panels' "..." menus contain the expected actions.

New backend tests in `test_browser_search.py`
(`test_default_call_excludes_cdl_files`,
`test_extra_extensions_includes_cdl_files_alongside_lut_files`,
`test_extra_extensions_respects_name_filter_too`). Full regression: 181
passed (was 178 -- +3 new), same pre-existing `/tmp`-permission failures
as always, nothing new broken.

Untested (GUI-only, same libEGL wall as everything else): please run
`main_window.py` and confirm (1) a mixed folder shows LUT and CDL files
together with CDL ones visibly tagged, (2) actually dragging (real mouse,
not the automated test's direct method call) a LUT file onto the LUT
Library panel and a CDL file onto the CDL Library panel both ingest
correctly, (3) each panel's "..." menu opens and every action still does
what it did as a button, (4) a LUT still drags onto the Stack panel the
same as always.

## 27. Menu-bar category menus (redundant with "...") + keyboard shortcuts

Two follow-up requests:

**Every "..." corner menu now has a matching top-level menu-bar menu**,
per Tom -- LUT Library, CDL Library, Stack Memories, Viewer, Stack,
Scopes, alongside the existing File/Workspace menus. Each panel that
already had a "..." menu (`sample_image_panel.py`, `stack_panel.py`,
`scopes_panel.py`, `lut_library_panel.py`, `cdl_library_panel.py`,
`stack_memories_panel.py`) got its menu-building code split into a
`populate_menu(menu)` method that adds the SAME actions (same callback
methods) to whatever `QMenu` it's handed -- `_build_menu_button`/
`_build_list_menu_button` call it once for their own corner button,
`main_window.py`'s new `_build_menu_bar` code calls it again for the
matching top-level menu. Neither menu owns separate state, so they can't
drift out of sync -- the "..." menus are now genuinely redundant (a
second way in, per Tom's request), not replaced.

Scopes' scope-select actions are checkable/mutually-exclusive
(`QActionGroup`), which needed one extra step since Qt's exclusivity only
applies within a single `QActionGroup` -- `populate_menu` now builds its
OWN group per menu and appends its `{name: action}` dict to
`self._scope_action_sets`; `_on_scope_changed` loops over every set in
that list and syncs the matching action's checked state, so picking a
scope from either the menu bar or the corner menu updates both. Verified
by hand: triggering "Histogram" from the menu-bar Scopes menu leaves
BOTH menus showing Histogram checked.

Building the top-level menus requires the panels to already exist (they
call `panel.populate_menu(...)` directly), so `MainWindow.__init__` now
calls `_build_layout()` before `_build_menu_bar()` (reversed from
before) -- Workspace/File didn't actually need the panels at build time
(Workspace's toggle actions resolve their target frame lazily via
`getattr()` when clicked), so this reordering doesn't change their
behavior.

**Keyboard shortcuts**, per Tom's list: `Cmd+S` Save Session, `Cmd+Shift+S`
Save Session As..., `Cmd+O` Open Session... (written as `"Ctrl+..."` in
code -- `QKeySequence` maps that to Cmd on macOS automatically, the
portable way to write it, not a Mac-only string), and `Shift+1` through
`Shift+8` for the eight Workspace panel toggles, assigned automatically
by each panel's position in `PANEL_TOGGLES` (Browser=1, LUT Library=2,
CDL Library=3, Stack Memories=4, Viewer=5, Stack=6, Parameters=7,
Scopes=8) rather than hand-typed, so a future reordering doesn't require
re-assigning shortcuts by hand.

Verified end-to-end by hand: printed every top-level menu's contents
(text + resolved shortcut string) and confirmed they match exactly --
see the chat message alongside this summary for the full list, since Tom
asked for it directly to assign shortcuts to the category-menu items too.

Full regression: 181 passed, same pre-existing `/tmp`-permission failures
as always, nothing new broken (this round was menu/shortcut wiring only,
no backend changes).

Untested (GUI-only, same libEGL wall as everything else): please run
`main_window.py` and confirm (1) all six new top-level menus show up and
their actions work identically to the matching "..." menu, (2) Cmd+S/
Cmd+Shift+S/Cmd+O trigger Save/Save As/Open from anywhere in the window,
(3) Shift+1..Shift+8 toggle the right panel each, (4) picking a Scopes
view from the menu bar updates the "..." menu's checkmark too and vice
versa.

**Not started, per Tom ("let's think about it")**: undo/redo + history.
See the chat message for an initial architecture sketch -- not built
this round, deliberately, pending a decision on scope (depth, what
counts as one undo step, memory cost for a long CDL/LUT-heavy session)
before committing to an approach.

## 28. Basic undo/redo + USER_MANUAL.md, for tomorrow's test send

Tom's sending this out for people to test tomorrow -- two things landed
to get ready:

**Undo/Redo (new `undo_history.py`).** Scoped to the working Stack only
(per Tom, "basic... don't go crazy") -- not the LUT/CDL Library, Stack
Memories, or the sample image. `UndoHistory` is a plain pointer-based
list of `stack.Stack.copy()` snapshots -- Qt-free, no dependency on
anything GUI. Cheap by construction: `Stack.copy()` never duplicates a
LUT's actual table (`Stage.copy()` only shallow-copies each stage's
params dict, so a "lut" stage's table is SHARED by reference across every
snapshot, and tables are never mutated in place anywhere in this app),
so history is left uncapped rather than speculatively bounded.

Wired into `main_window.py` via the same `StackPanel.stack_changed`
signal `_on_stack_changed` already listens to (fires on every single
mutation kind -- add/remove/reorder/enable/opacity/param edit, size/
domain, recalling a Stack Memory, applying a CDL/LUT Library entry,
opening a session, New Session -- see `stack_panel.py`'s 14
`_emit_changed()` call sites). A `_restoring_stack_history` guard flag
stops `_undo()`/`_redo()`'s own `set_stack()` calls from being recorded
as new edits, which would otherwise make Redo unreachable immediately
after every Undo. New `Edit` menu: Undo (⌘Z) / Redo (⌘⇧Z), enabled state
kept in sync with `UndoHistory.can_undo`/`can_redo`.

Verified by hand through the real GUI stack (not just the backend unit
tests): added three stages, undid twice, redid once, made a new edit
(confirmed the abandoned redo tail is correctly discarded, not
resurrected later), then undid all the way back to empty -- every step
landed on the expected stage list, Undo/Redo enabled state matched at
each point. Backend fully tested (`test_undo_history.py`: push/undo/
redo, boundary conditions at both ends, the redo-tail-discard rule,
`len()`). Full regression: 189 passed (was 181 -- +8 new), same
pre-existing `/tmp`-permission failures as always, nothing new broken.

**New `USER_MANUAL.md`.** Deliberately short (per Tom, "very basic...
just outline the features") -- window layout, the core add-stages/
reorder/adjust/export workflow, what each library is for, sessions,
undo/redo's scope, and the full keyboard shortcut table. Not a reference
doc, just enough for a first-time tester to find their way around.

Untested (GUI-only, same libEGL wall as everything else): please run
`main_window.py` and confirm (1) Undo/Redo from the Edit menu (and ⌘Z/
⌘⇧Z) behave as expected while actually dragging/clicking through the UI,
not just via the direct method calls the hand-verification above used,
(2) the Undo/Redo menu items visibly grey out/enable at the right times.

## 29. Crash fix: closing/redocking a floated panel segfaulted (3D Viewer)

Picked up at the start of this migration from the crash report you pasted
at the end of last session: closing a floated panel window crashed the
app (SIGSEGV/EXC_BAD_ACCESS), and separately the 3D Viewer was reported
broken when floated. Root cause from the crash trace: reparenting a panel
(float or dock) can make Qt deliver a paint event to the 3D Viewer's
`GLViewWidget` (a `QOpenGLWidget`) while its GL context is mid-teardown/
mid-recreate -- `paintGL()` then called straight into a raw ctypes GL
call (`glDrawArrays`) with no valid context, which is the actual segfault
-- a hard native crash, so no Python `try/except` could ever have caught
it after the fact.

Two-layer fix:

- **`lut_viewer_3d.py`**: `gl_view` is now `_SafeGLViewWidget`, a thin
  subclass of pyqtgraph's `GLViewWidget` whose `paintGL()` checks
  `self.isValid()` -- Qt's own "do I have a real context right now" flag
  -- and returns immediately, before any GL call, if not. This is the
  actual point where the crash becomes preventable.
- **`main_window.py`**: new `_reparent_gl_safely(frame, reparent_fn)`,
  used by both `_float_panel` and `_dock_panel` around their existing
  `setParent(None)`/`setWindowFlags(...)`/`splitter.insertWidget(...)`
  calls. Finds every `QOpenGLWidget` under the panel (not hardcoded to
  the 3D Viewer -- covers any future panel that adds GL content too),
  calls `setUpdatesEnabled(False)` before the reparent and `True` +
  `update()` after -- Qt's supported way to stop a widget scheduling/
  delivering paint events for itself during a risky operation, and to
  force one fresh repaint once it's settled afterward (aimed at "broken
  when floated" too, though that report had no further detail to
  reproduce against -- flag if it's still not right).

New `test_gl_reparent_safety.py` (5 tests): confirms `_SafeGLViewWidget.
paintGL` skips the real GL-calling code when `isValid()` is False and
still calls through when it's True (so the guard isn't a no-op that
silently broke real rendering); a real `MainWindow`, float+dock cycle on
the 3D Viewer completes without raising; the exact path from your crash
report -- floating the 3D Viewer, then closing it via the window's own
native close button (`MainWindow.eventFilter`'s redock path, not the
Workspace menu toggle) -- redocks without raising; 3 repeated float/dock
cycles in a row don't raise either. Full regression: 247 passed (was
242 -- +5 new), same 19 pre-existing `/tmp`-permission failures as
always, nothing new broken.

**Still flagged UNVERIFIED ON REAL HARDWARE** -- this sandbox has no GPU/
display, so `isValid()` is always False here and the above tests can only
exercise the guard's early-return branch, not reproduce the actual
segfault-then-confirm-it's-gone. Please specifically re-run the original
repro: float the 3D Viewer panel, then close it via its native titlebar
close button (not the Workspace menu), and confirm it no longer crashes
-- and separately let me know what "broken when floated" actually looked
like if the `update()` call above doesn't already resolve it, since that
part of your report never got any further detail before the last session
ended.

## 30. Render fix: 3D viewer was blank/black the whole time while floated

Follow-up in the same sitting: you confirmed #29's crash fix worked, then
clarified "broken when floated" -- the graph was genuinely blank/black
the entire time, not stalled on a last frame, not slow to update, never
drew anything at all, even across rotate/resize/density-slider changes.
That rules out "just a missed repaint" (which `_SafeGLViewWidget`'s
`isValid()` guard would eventually recover from on its own once a real
paint event lands) -- it points at the GL context itself never coming up
valid for this widget once it's promoted from a child widget inside the
main window into its own top-level, always-on-top window. Reparenting an
EXISTING `QOpenGLWidget` across that kind of promotion isn't something Qt
handles reliably on every platform.

Also caught and fixed a real bug while touching this: `LutViewer3D.
_build_layout` was still constructing a plain `gl.GLViewWidget()`, not
`_SafeGLViewWidget` -- #29's crash guard was defined but never actually
wired into the running app. Sorry for that one -- fixed now.

**Fix (`lut_viewer_3d.py`):** new `LutViewer3D.rebuild_gl_view()` -- tears
down the current `gl_view` and builds a brand new `_SafeGLViewWidget` in
its place (same layout position, same grid/axis setup via a new shared
`_make_gl_view()` helper both `__init__` and `rebuild_gl_view` call), then
re-applies whichever LUT was loaded so the visible content survives even
though the underlying native widget is new. Standard workaround for "an
existing QOpenGLWidget doesn't reliably survive being reparented into/out
of its own top-level window" -- rather than trying to carry old GL state
across that transition, don't; let a fresh widget set up its context from
scratch wherever it actually ends up.

**Wiring (`main_window.py`):** new `_rebuild_gl_content_for_panel(frame_
attr)`, a small per-panel hook (only `viewer_3d_frame` has an entry --
no other panel has GL content) called from both `_float_panel` and
`_dock_panel`, AFTER the panel has been reparented and made visible --
rebuilding any earlier would just mean redoing it once the parent settles
into its real final state.

New tests in `test_gl_reparent_safety.py` (6 more, 11 total): confirms
`rebuild_gl_view` actually swaps the widget instance (not just repaints
it) while keeping the loaded LUT and its layout position, is safe to call
with no LUT loaded yet, and that `_float_panel`/`_dock_panel` both trigger
a rebuild for the 3D Viewer specifically (and leave a non-GL panel like
Scopes untouched). Full regression: 253 passed (was 247 -- +6 new), same
19 pre-existing `/tmp`-permission failures, nothing new broken.

**Still UNVERIFIED ON REAL HARDWARE**, same as #29 -- this sandbox has no
GPU/display, so neither the original blank-render symptom nor this fix
for it can be reproduced-then-confirmed here. Please re-test: float the
3D Viewer with a LUT loaded, confirm the point cloud actually draws and
updates on rotate/resize/density-slider -- and if it's STILL blank after
this, that would mean the underlying cause is something rebuild_gl_view
doesn't touch (e.g. a deeper WindowStaysOnTopHint/OpenGL compositing
interaction on macOS itself), worth knowing so the next attempt doesn't
retread this one.

## 31. Found the likely real cause: camera was never centered on the data

You reported #30's fix didn't help -- still no image -- and asked directly
whether it's a centering problem. Good instinct: auditing the camera setup
turned up a real, pre-existing bug that's plausibly THE actual cause, not
just a contributing one.

pyqtgraph's `GLViewWidget.reset()` defaults the camera's `center` to the
coordinate ORIGIN, `Vector(0,0,0)`. Nothing in `lut_viewer_3d.py` ever
overrode it -- only `distance`/`elevation`/`azimuth` were set. For a
typical identity-domain LUT, the deformed lattice `set_lut` plots lives in
roughly `[0,1]^3`, whose actual center is `(0.5, 0.5, 0.5)` -- the origin
is one CORNER of that cube, not the middle. A hardcoded `(0.5,0.5,0.5)`
wouldn't have been right either, for any LUT whose output legitimately
goes outside `[0,1]` (HDR grades, scene-linear stages -- this app doesn't
clip anywhere upstream on purpose).

This was there in every case, docked included -- not something #29/#30's
float/dock work introduced. Best guess for why it was survivable while
docked: pyqtgraph's own pre-override default distance is 10 (this file
sets 3), and/or you'd already manually panned/zoomed the one long-lived
widget over the course of using the app, which happened to compensate.
`rebuild_gl_view` (#30) builds a brand new widget on every float/dock,
which doesn't get to inherit either of those accidental saves -- so
whatever was marginal before became fully invisible.

**Fix (`lut_viewer_3d.py`):** new `LutViewer3D._frame_camera_on(positions)`,
called at the end of every `set_lut` -- computes the REAL bounding box of
whatever `positions` was just plotted (not a hardcoded guess) and sets
the camera's center to its midpoint, distance to 2.5x the largest axis
extent (floored at 0.5 so a near-flat LUT, e.g. a pure desaturate, doesn't
put the camera implausibly close -- this multiplier is a plain
fit-it-in-frame heuristic, flag if it needs tightening/loosening once
this can actually be seen). Runs on every `set_lut` call including
density-slider re-samples of the same LUT, not just first load --
harmless when the LUT hasn't changed (same bounding box in, same camera
position out).

New tests in `test_gl_reparent_safety.py` (4 more, 15 total): an identity
LUT's positions span exactly `[0,1]` per axis, so its real center is
provably `(0.5, 0.5, 0.5)` and distance `2.5` -- confirmed exactly, not
just "isn't the origin." Also confirms `rebuild_gl_view`'s brand new
widget gets correctly re-centered (not left at ITS OWN fresh (0,0,0)
default), and that an empty positions array is a safe no-op. Full
regression: 257 passed (was 253 -- +4 new), same 19 pre-existing
`/tmp`-permission failures, nothing new broken.

**Still UNVERIFIED ON REAL HARDWARE**, same caveat as #29/#30 -- please
re-test with a LUT loaded, both docked and floated: confirm the point
cloud is actually visible and reasonably framed (not just present
somewhere off-camera). If it's STILL blank after this, the remaining
candidates are lower-level than anything in this file -- viewport/surface
setup timing, or a WindowStaysOnTopHint-specific OpenGL compositing issue
on macOS -- and would need an actual screenshot or more detail from you
to chase further, since I have no way to reproduce any of this without a
real GPU.

## 32. Found a real second bug: deleteLater()/Python-GC race caused the new crash

You confirmed #31 didn't fix the black screen, then reported the crash was
BACK -- specifically, only when closing the floated 3D Viewer with no LUT
loaded. That specific detail was the key clue.

`rebuild_gl_view` (#30) called `old_view.setParent(None)` then
`old_view.deleteLater()` and let `old_view` fall out of scope once the
method returned. That's a real bug: `deleteLater()` only schedules the
C++-side deletion for later, on the next spin of the event loop -- it
does nothing to stop the PYTHON wrapper object from being
garbage-collected in the meantime. Once `old_view` was parentless and
nothing else referenced it, CPython could collect the Python wrapper
immediately -- and PySide6/Shiboken's default rule for a parentless
QObject is that Python's own GC deletes the underlying C++ object too.
That's the same widget getting deleted twice, racing itself -- a
double-free/use-after-free, a hard native crash exactly like the
original one, just from a different cause. Loading no LUT was plausibly
just the fastest path back to the function returning with nothing else
keeping `old_view`'s refcount above zero -- the bug was there regardless
of what was loaded, just harder to hit.

**Fix (`lut_viewer_3d.py`):** `LutViewer3D` now keeps a permanent,
never-cleared list, `self._pending_gl_view_deletions` -- every `old_view`
a rebuild discards gets appended there instead of being allowed to fall
out of scope, so a real Python reference survives until Qt's own
`deleteLater()` has actually run. Also dropped the `setParent(None)` call
(just `hide()` now) since it's not needed once the reference is held
properly, and removes one more way for ownership semantics to surprise
us. Deliberately unbounded rather than timed -- float/dock is rare and
human-paced, so a few retained already-deleted wrapper objects over a
session is a non-issue, and getting a "safe to clear now" timing wrong
would silently reopen this exact bug.

New tests in `test_gl_reparent_safety.py` (3 more, 18 total): confirms a
discarded `gl_view` lands in `_pending_gl_view_deletions`; reproduces
your exact second repro -- float the 3D Viewer with `viewer_3d.clear()`
called first (MainWindow always has SOME bake from its own empty-Stack
default, so this is what "nothing loaded" actually means in a real
session -- see the test's own docstring), then close via the native
titlebar button; and 3 repeated float/dock cycles with nothing loaded.
Full regression: 260 passed (was 257 -- +3 new), same 19 pre-existing
`/tmp`-permission failures, nothing new broken.

**Still UNVERIFIED ON REAL HARDWARE**, same as every round above. This
one I'm more confident about than the render fix, since parentless-
QObject-plus-deleteLater is a well-documented PySide6/Shiboken footgun
independent of anything platform-specific -- but please confirm: (1) the
crash is actually gone this time, closing floated with and without a LUT
loaded; (2) whether the black-screen render issue is still there
separately, since #31's centering fix and this crash fix are independent
of each other.

## 33. Found it (probably): the remember-workspace feature itself, per Tom's own question

You asked directly: "any chance this is related to the retain workspace
between sessions feature?" -- yes, very likely, and this explains why
#29-#32's fixes never visibly changed anything despite being real,
individually-verified fixes.

`__init__` calls `load_workspace_state(workspace_io.load_remembered_
workspace())` -- and `load_workspace_state` restores a remembered
floating panel by calling `_float_panel()` directly, SYNCHRONOUSLY,
still inside `__init__`. That's before `main()` ever calls `window.
show()`, before `QApplication.exec()` has started processing events --
the app isn't really "running" yet in any meaningful sense. Once you
floated the 3D Viewer the very first time, it got remembered -- so EVERY
launch since then has auto-restored it floating during this early,
half-constructed moment, meaning every test you ran after that first one
went through this exact path, never a fresh manual float in an
already-running app. Building a top-level window with a QOpenGLWidget
child at that moment is a fundamentally different situation than doing
it once everything's warmed up -- and would fully explain why #29-#32's
fixes (all correct on their own terms, per their own tests) never showed
any visible difference: they were fixing the right mechanism, running at
the wrong time.

Also touched while here: `closeEvent`'s own `_dock_all_floating_panels()`
call (redocking everything right before quitting, so no orphan window
survives the main window closing) was ALSO rebuilding the 3D Viewer's GL
widget every time -- brand new QOpenGLWidget, right as the app tears
down, for a widget nobody will ever see again. Pure risk, zero benefit,
so that's gone too.

**Fix (`main_window.py`):**
- `load_workspace_state`'s floating-restoration loop now runs via
  `QTimer.singleShot(0, ...)` (pulled into new `_apply_floating_state`)
  instead of inline -- so it happens on the next event-loop tick,
  whether triggered by `__init__`, Reset to Default, or importing a
  preset. For `__init__` specifically, that tick doesn't happen until
  AFTER `main()`'s `window.show()` and `app.exec()` have both run --
  restoring a floating panel now happens in the same "fully up and
  running" state a manual float already did.
- `_dock_panel`/`_dock_all_floating_panels` gained `rebuild_gl_content`
  (default `True`); `closeEvent` passes `rebuild_gl_content=False` so
  redocking-before-quit skips the GL rebuild entirely.

New tests in `test_gl_reparent_safety.py` (3 more, 21 total): confirms
`load_workspace_state` does NOT float synchronously (only after
`QApplication.processEvents()`); reproduces the real startup path by
monkeypatching `workspace_io.load_remembered_workspace()` to simulate "3D
Viewer was floating last session" and confirming a fresh `MainWindow()`
returns without having floated anything yet; confirms `closeEvent`
docks the 3D Viewer WITHOUT rebuilding its GL widget. Full regression:
263 passed (was 260 -- +3 new), same 19 pre-existing `/tmp`-permission
failures, nothing new broken.

**Still UNVERIFIED ON REAL HARDWARE**, same as every round above -- but
this is the first fix in this whole chase that targets something about
WHEN code runs rather than guessing at WHAT macOS/Qt/OpenGL does
internally, which I'm more confident actually matches what you've been
observing. Please test the full cycle: float the 3D Viewer, quit the
app entirely (not just close the floating window), relaunch, and see
whether it comes back floating AND actually rendering this time.

## 34. The actual root cause of the blank render (found from your real traceback)

You pasted the real console output this time, and it was the answer.
`GLError(err=1281, description=b'invalid value', baseOperation=
glUseProgram, ...)` and `err=1282 ('invalid operation')`, both from
inside pyqtgraph's own `GLLinePlotItem.paint()`/`GLScatterPlotItem.
paint()`, at `with program: glUseProgram(self)`. Every previous fix in
this thread (#29-#33) was real and independently correct for the bug it
targeted, but none of them were THIS bug -- the blank render had a
completely different cause the whole time.

pyqtgraph's `GLLinePlotItem`/`GLScatterPlotItem` both cache their compiled
shader PROGRAM at the CLASS level (`klass._shaderProgram`, see
`getShaderProgram()` in pyqtgraph's own source) -- compiled exactly once,
ever, in whichever GL context happens to be current the first time ANY
instance of that class is painted, then reused by every future instance
regardless of which widget or context is asking. A compiled shader
program's integer ID only means something WITHIN the specific GL context
that created it. By default, separate top-level `QOpenGLWidget`s --
which is exactly what floating a panel creates, and what #30's
`rebuild_gl_view` creates fresh copies of every time -- each get their
own fully independent GL context. So the very first widget to ever
successfully paint a scatter/line item "poisons" that class-level cache
for every OTHER widget for the rest of the process: `glUseProgram` gets
handed a program ID that's meaningless in the current context, and every
single paint attempt fails the same way, forever -- which is exactly
"blank/black the whole time," on every widget after the first one. This
plausibly also explains the renewed segfault on quit: repeatedly calling
GL functions with a stale/invalid handle is undefined behavior, not
guaranteed to just return a clean error every time.

**Fix (`main_window.py`):** `Qt.ApplicationAttribute.
AA_ShareOpenGLContexts` set to `True` via `QApplication.setAttribute(...)`
in `main()`, BEFORE `QApplication(sys.argv)` is constructed (Qt only
reads this attribute at that point, per its own docs) -- this is Qt's
documented, built-in answer to exactly this scenario: it puts every
`QOpenGLWidget`'s context into one shared "share group," so a resource
(like a compiled shader program) created via any one of them stays valid
in all the others too, including ones created later.

New test in `test_gl_reparent_safety.py` (1 more, 22 total): can't
actually prove shader sharing works without a real GPU, so this checks
`main()`'s source directly -- confirms `AA_ShareOpenGLContexts` is both
present and appears BEFORE the `QApplication(...)` call specifically
(ordering is what makes this work at all). Full regression: 264 passed
(was 263 -- +1 new), same 19 pre-existing `/tmp`-permission failures,
nothing new broken.

Everything from #29-#33 stays in place -- the crash guard, the deleteLater
fix, the deferred-floating-restore fix, and the camera centering are all
independently real and worth keeping regardless of this one. This is
just the piece that was actually causing the blank screen the entire
time. **Still UNVERIFIED ON REAL HARDWARE**, but this is a named,
documented Qt attribute for exactly this exact failure mode (not a
guess reasoned from a stack trace) -- please test the full cycle again:
float the 3D Viewer with a LUT loaded, confirm the point cloud actually
renders, then quit and relaunch to confirm nothing crashes.

Tom confirmed this fixed it -- floating/docking and quitting are all
working on real hardware now. This closes out the #29-#34 crash/render
chase.

## 35. Waveform scope fix: HDR-limiter values were bunching at the bottom

Tom reported: applying an HDR limiter made the waveform trace bunch up
toward the bottom of the graph, almost inverted, while the graticule
stayed correctly positioned.

**Root cause (`scopes_panel.py`):** `_render_waveform` was calling
`np.flipud()` on the image array on the assumption that pyqtgraph's
`ImageItem` draws row 0 at the top by default. Confirmed empirically
(rendered a real `ImageItem` offscreen with a distinct value per row and
sampled actual pixel colors) that under this app's specific setup
(`imageAxisOrder='col-major'`, the row/col transpose already applied
before `setImage`, no `invertY()` call anywhere) row 0 already lands at
the BOTTOM by default -- so the flip was inverting an already-correct
image. The graticule never had this flip applied to it, which is why it
stayed correctly positioned while the trace didn't -- exactly matching
what Tom saw.

**Fix:** removed the `np.flipud()` call. New `test_waveform_orientation.py`
(2 tests) -- a deterministic, data-level check that the array handed to
`setImage()` exactly matches `compute_waveform`'s own row order with no
reversal, for both the luma and RGB Parade styles. (A pixel-rendering
version of this same check was also tried -- reading back actual rendered
pixel colors -- but dropped after it kept hitting an unrelated rendering
artifact specific to this sandbox's offscreen QPA platform, unrelated to
image content; the data-level check reproduces the actual code change
directly rather than inferring it from rendered pixels, so nothing was
lost by dropping the flakier one.) Full regression: 266 passed (was 264
-- +2 new), same 19 pre-existing `/tmp`-permission failures, nothing new
broken.

Untested on real hardware: please reload an HDR-limited grade and confirm
the waveform trace now sits correctly relative to the graticule instead
of bunched at the bottom.

## 36. Semi-opaque 3D viewer grid with an opacity slider

Second of Tom's two feature requests from the same message as #35 (the
first, well-defined one -- the 1D YRGB viewer, the second request in that
message, is being held for a design check-in before starting, see chat).

**`lut_viewer_3d.py`:** new `_grid_opacity` (0.0-1.0, stored on the widget
itself, not read off `self._grid_item` -- that gets discarded and rebuilt
by `rebuild_gl_view`, same reasoning `density_slider`'s value already
follows) and a new "Grid opacity:" slider (0-100%) next to the existing
Grid density control. Defaults to 30%, matching pyqtgraph's own
`GLGridItem` default alpha (76.5/255) -- this feature makes that
adjustable, not a different starting point. `_apply_grid_opacity()` sets
the current `GLGridItem`'s color/alpha and is called both on every slider
move and right after `_make_gl_view()` builds a fresh grid item (initial
construction and every `rebuild_gl_view` call alike), since a brand new
`GLGridItem` always starts back at pyqtgraph's hardcoded default with no
memory of the slider's last value.

New tests in `test_gl_reparent_safety.py` (3 more, 25 total): slider
defaults to 30% matching pyqtgraph's own default; moving the slider
updates `_grid_opacity` and the live `GLGridItem` color; the value
survives a `rebuild_gl_view` call (float/dock cycle) the same way grid
density already does. Full regression: 269 passed (was 266 -- +3 new),
same 19 pre-existing `/tmp`-permission failures, nothing new broken.

Untested on real hardware, same libEGL wall as always: please confirm the
slider visibly fades the grid in/out and that a float/dock cycle doesn't
reset it back to 30%.

## 37. 3D Viewer renamed "Graph", gains a 1D YRGB curve mode

Second half of Tom's two-part request (grid opacity was #36); checked in
on the design first before building (data source, panel placement, axis
range -- see chat) rather than guessing:

- **Data**: the LUT's own response curves (Y/R/G/B vs. input), not
  something derived from the sample image -- a second projection of the
  exact same baked-LUT data the 3D view already shows, not a new Scopes
  mode.
- **Placement**: worked into the existing 3D Viewer panel via a mode
  switch, rather than a new panel -- so the panel itself is renamed
  **"Graph"** (`main_window.py`'s `PANEL_TOGGLES`/`PanelFrame` title;
  internal names -- `viewer_3d`, `viewer_3d_frame`, `LutViewer3D` --
  unchanged, only the user-facing label moved).
- **Axis range**: follows the Stack's own domain (`lut.domain`, the same
  array `stack.bake_stack_to_lut3d` already writes from Stack.domain_min/
  domain_max), not a fixed 0-1 range -- correct for HDR/scene-linear
  grades, consistent with how Grid Size/Domain and the 3D view's own
  camera-centering fix already treat domain as real data, not a constant.

**Backend (`app_logic.py`): new `compute_yrgb_curve(lut, num_samples=256)`.**
Feeds a synthetic ramp through `lut.apply()` -- one shared parameter `t`
(0 to 1) sweeps each channel through ITS OWN `[domain_min, domain_max]` in
lockstep, which reduces to the familiar R=G=B neutral ramp whenever the
domain is linked (the common case) without assuming [0,1] when it isn't.
Falls back to [0,1] if the LUT object has no `.domain` at all. Y (luma)
uses the same Rec.709 coefficients (`scopes.LUMA_COEFFICIENTS_REC709`) the
Histogram/Waveform scopes already use, for one consistent meaning of
"luma" across every plot in the app. New `test_yrgb_curve.py` (7 tests,
fully Qt-free): identity-LUT straight-line sanity check, default [0,1]
span, a real asymmetric non-[0,1] domain producing the correct per-channel
ramp, the no-`.domain`-attribute fallback, Y matching the Rec.709 formula
by hand, a real production LUT producing a genuinely non-identity curve,
and array-length correctness.

**GUI (`lut_viewer_3d.py`): a 3D/1D mode switch above the GL view** --
plain checkable `QPushButton`s in an exclusive `QButtonGroup` ("3D" /
"1D (Curves)"), not a dropdown, matching this app's general preference for
visible buttons over a combo for a binary choice. The GL view and its
density/grid-opacity controls now live in their own `_gl_panel` container
(one `setVisible()` toggles the whole group); the new `curve_plot`
(`pg.PlotWidget`, same R/G/B color convention as Histogram/Waveform, plus
gray for Y) sits alongside it. Both are built once in `__init__` and kept
alive permanently -- switching modes ONLY toggles visibility, it never
destroys/rebuilds either widget, deliberately: `rebuild_gl_view` exists
specifically because tearing down/recreating the GL widget is a real,
historically crash-prone operation (see #29-#34 above), and a mode switch
firing far more often than float/dock ever did is not where that risk
belongs. `set_lut()` now updates both the 3D scatter and the 1D curves on
every call regardless of which mode is currently showing, so switching
modes always reflects the current LUT with no extra recompute step;
`_on_density_changed`/`rebuild_gl_view`'s internal re-apply were narrowed
to only touch the 3D scatter (`_update_3d_scatter`), since neither grid
density nor a float/dock rebuild has anything to do with the 1D curves.

New tests in `test_gl_reparent_safety.py` (7 more, 32 total): default mode
is 3D; switching to 1D hides the GL panel and shows the curve plot (and
back); 5 rapid mode switches never replace `gl_view` (confirms no
accidental rebuild); `set_lut` populates all four curve items at the
correct length with correct (identity-line) data; the curve plot updates
even while 3D mode is the one currently visible; `clear()` resets curve
data too. One pre-existing test
(`test_rebuild_gl_view_stays_in_the_same_layout_position`) was updated,
not broken -- it now checks position within `_gl_panel`'s own layout
instead of the panel's top-level layout, since gl_view moved one level
deeper when `_gl_panel` was introduced; the behavior it verifies
(rebuilt view lands back in the same slot) is unchanged. Full regression:
283 passed (was 269 -- +14 new: 7 in `test_yrgb_curve.py`, 7 in
`test_gl_reparent_safety.py`), same 19 pre-existing `/tmp`-permission
failures, nothing new broken.

`USER_MANUAL.md`/`help_text.py` updated to match (kept byte-identical,
re-verified) -- new Graph panel description covering both modes, and the
keyboard shortcut table's "Show/Hide 3D Viewer" row renamed to "Show/Hide
Graph" (⇧0 is unchanged, only the label).

**KNOWN LIMITATION, flagged rather than solved:** the Y curve is plotted
against `input_r` as its X axis (see `compute_yrgb_curve`'s docstring) --
exactly right whenever domain is linked across channels (the default),
an approximation if a Stack's domain is deliberately set asymmetric.
Wasn't clear from the request whether that edge case matters in practice.

Untested on real hardware, same libEGL wall as always: please confirm (1)
the "3D" / "1D (Curves)" buttons actually switch views and look right,
(2) the 1D curves are visually sane against a real LUT (Y roughly tracks
R/G/B, a real grade's curves look like a grade, not noise), (3) the panel
title/Workspace-menu entry/tooltip all now say "Graph" instead of "3D
Viewer" everywhere you'd expect.

## 38. Graph panel: three real bugs Tom caught in #37, all fixed

Tom tried #37 and reported three problems -- all real, none of them
"working as intended":

**1. "the buttons for 1d curve is broken. should go in an ellipses menu,
select 3d or 1d y 1d r 1d g 1d b."** The checkable-QPushButton-pair mode
switch is GONE, replaced with a "..." corner menu -- the exact same
convention `scopes_panel.py` already uses for its own Histogram/Waveform/
Vectorscope selector (`populate_menu`/`_build_menu_button`, one
`QActionGroup` per menu instance, mirrored into a new top-level "Graph"
menu-bar entry in `main_window.py` alongside Scopes'). Five choices, not
two: **3D**, **1D Y**, **1D R**, **1D G**, **1D B** (`VIEW_CHOICES`) --
each 1D choice shows exactly ONE of the four response curves
(`_VIEW_TO_CURVE_CHANNEL`/`_on_view_changed`), not all four overlaid as
#37 did.

**2. "The panel snaps to a new size when switching from 3d to curve and
fucks up the UI."** Root cause: the 3D GL panel and the 1D curve plot
were SIBLINGS toggled via `setVisible()`, so the containing layout's
minimum-size calculation changed depending on which was currently shown
(the GL panel has a 280px `setMinimumHeight` floor plus two control rows,
the bare curve plot didn't) -- exactly the class of bug #23's Quirk 1
diagnosed for full-panel toggles, just one level down inside this panel
specifically. **Fix:** both are now pages of one `QStackedWidget`
(`self.stack`), matching `scopes_panel.py`'s own Histogram/Waveform/
Vectorscope switch -- confirmed empirically (not just assumed) that a
`QStackedWidget`'s `minimumSizeHint()` is the MAX across ALL its pages
regardless of which is current, so the panel's on-screen footprint
literally cannot change on a view switch anymore.

**3. "The grid on the 3d is not what i thought. I want more of a cage
mesh that draws lines between the points rendered in the graph."** #37's
"Grid opacity" was a flat `GLGridItem` ground-plane reference, unrelated
to the LUT's own data -- not what Tom meant by "grid" at all. **Fix:**
new `app_logic.extract_3d_cage_lines_for_visualization(lut,
subsample_size)` builds a wireframe connecting each scatter point to its
immediate neighbors along all three grid axes (same identity-grid/
`lut.apply()` call `extract_3d_points_for_visualization` already makes,
just NOT flattened, so `(i,j,k)` neighbor relationships are still
available to connect) -- returned as `(2*E, 3)` line-segment endpoint
pairs, pyqtgraph's `GLLinePlotItem(mode='lines')` convention. Replaces
`_grid_item`/`GLGridItem` with `_cage_item`/`GLLinePlotItem` throughout
`lut_viewer_3d.py`, renamed grid->cage everywhere (`_cage_opacity`,
`cage_opacity_slider`, `_apply_cage_opacity`) since this is a genuinely
different feature, not a relabel. Recomputed in `_update_3d_view`
alongside the scatter (same LUT, same Grid density, always in sync).
`GLAxisItem` (world axes) is untouched -- Tom's complaint was specifically
about the grid, not the axes. One real API gotcha caught before shipping:
`GLLinePlotItem.color` is a plain 0.0-1.0 float RGBA tuple, NOT the 0-255
range `GLGridItem.setColor` used -- confirmed against the actual
installed pyqtgraph before writing `_apply_cage_opacity`, not assumed.

**Also fixed a latent bug while rebuilding `rebuild_gl_view`** for the
cage: `self._scatter_item`/`self._cage_item` must be reset to `None`
BEFORE `self.gl_view` is reassigned to the new widget, exactly matching
the existing (already-correct) pattern for `_scatter_item` -- both items
were added to the OLD gl_view, and `_update_3d_view`'s `removeItem()`
calls would otherwise target the NEW gl_view, which never had them, and
raise. Caught by writing the cage feature carefully against the existing
pattern, not by a failing test -- flagging since it's exactly the kind of
bug that's easy to reintroduce if this code is touched again without
re-reading this note.

New/rewritten tests in `test_gl_reparent_safety.py` (net +6, 38 total
now — 5 cage tests, 7 menu/QStackedWidget tests replacing the old 3
grid-opacity + 4 button-mode tests): cage opacity default/update/survives-
density-change/survives-rebuild, no cage before any LUT loads, the "..."
menu has exactly the 5 expected choices with 3D checked by default,
selecting each 1D choice shows the curve plot with EXACTLY that channel's
curve visible, `stack.minimumSizeHint()` is proven constant across every
view switch (the actual regression test for bug #2), 5 rapid full-cycle
view switches never replace `gl_view` (still no accidental rebuild), and
picking a view from a second `populate_menu()` instance checks the
matching action in the first one too (dual-menu sync, mirroring
`scopes_panel.py`'s own `_on_scope_changed` test coverage). Full
regression: 289 passed (was 283 -- +6 net), same 19 pre-existing
`/tmp`-permission failures, nothing new broken.

`USER_MANUAL.md`/`help_text.py` updated to match (kept byte-identical,
re-verified) -- Graph panel description now covers the "..." menu's five
choices and the cage (not grid).

Untested on real hardware, same libEGL wall as always: please confirm (1)
the "..." menu (both the corner button and the "Graph" menu-bar entry)
actually switches between 3D/1D Y/1D R/1D G/1D B and looks right, (2)
switching views does NOT resize the panel or shift neighboring panels
anymore, (3) the 3D view now shows a wireframe cage connecting the
plotted points specifically (not a flat reference plane), and the
opacity slider fades THAT in/out.

## 39. Floating windows were staying above EVERY app, not just the main window

Tom, after confirming #38's fixes worked and asking about free-form panel
rearranging (noted as a real future project, not started this round --
current layout is still three hardcoded `QSplitter`s, no runtime concept
of moving a panel between them; see chat for the two options discussed,
hand-rolled drag-and-drop vs. migrating to Qt's native `QMainWindow`/
`QDockWidget` docking system): "The dialogue and float windows can go
under other programs, just not the main window."

Real bug, not a preference call. Every floated panel and popup dialog
(Stack's Grid Size/Domain, Scopes' Graticule Intensity, Sample/Reference
Adapt, Export, Help Manual) used `Qt.WindowStaysOnTopHint`
(`floating_dialog.py`'s `make_always_on_top`), which pins a window above
literally every other window on the desktop -- Finder, a browser, another
app's own palette, anything -- not just LUTier's own main window. That's
a real behavioral bug that would be actively annoying day to day, not
"working as designed."

**Fix:** `make_always_on_top` -> `make_floating_tool_window`
(`floating_dialog.py`), now setting `Qt.WindowType.Tool` instead of
`Qt.WindowStaysOnTopHint`. Tool is the correct, narrower tool for exactly
this -- a floating utility/palette window that stays above its OWNER
specifically (an OS-level "owned window" relationship), the same
structural pattern Photoshop-style floating palettes use, which is what
these panels/dialogs already conceptually are. Window TYPE bits (Dialog/
Tool/Window) aren't meant to be OR'd together the way a hint bit is, so
this REPLACES the window's flags (`setWindowFlags(Qt.WindowType.Tool)`)
rather than adding Tool onto whatever type it already had.

Tool only enforces "stays above" for a window that actually HAS a real Qt
parent -- `main_window.py`'s `_float_panel` used to call
`frame.setParent(None)`, severing the parent entirely (that's WHY it
needed the blunt always-on-top hint in the first place: nothing else was
keeping it above the main window). Now does
`frame.setParent(self, Qt.WindowType.Tool)` in one call -- reparents AND
sets the type together, establishing the exact relationship Tool needs to
enforce persistently. Every dialog already had a real parent from
construction (`QDialog(self)` etc.), so those only needed the flag
change, not a parenting change.

An earlier attempt at plain `Qt.Dialog` + a parent (this file's own
history, before `WindowStaysOnTopHint` was introduced) was reportedly
insufficient -- clicking the main window brought IT forward and re-covered
the dialog, because `Qt.Dialog`'s default stacking is only a momentary
raise, not a persistent OS-enforced relationship. `Qt.Tool` is a
different, stronger mechanism -- a genuine owned-window relationship, not
just a hint -- so this isn't retrying the same fix that already failed.

New `test_floating_window_scope.py` (9 tests): `make_floating_tool_window`
sets Tool and never sets `WindowStaysOnTopHint`; confirms it REPLACES the
window type rather than combining with `Qt.Dialog` (checked via
`Qt.WindowType.WindowType_Mask` -- a naive `flags & Qt.WindowType.Dialog`
check is a false-positive trap here, since `Dialog`'s bits happen to be a
subset of `Tool`'s); a floated panel is Tool-typed, parented to
MainWindow, still genuinely top-level (`isWindow()` True); a docked panel
is back to a plain embedded widget (`isWindow()` False, no Tool type);
EVERY panel in `PANEL_TOGGLES` gets the same treatment when floated, not
just Graph; the Manual/Export/Graticule Intensity dialogs are all
Tool-typed with a real parent. Full float/dock regression in
`test_gl_reparent_safety.py` (all 38 tests, including the crash-history
ones) re-run unchanged and still pass -- this change doesn't touch the GL
reparent-safety machinery, just what parent/flags the reparent targets.
Full suite: 298 passed (was 289 -- +9 new), same 19 pre-existing
`/tmp`-permission failures, nothing new broken.

**Still UNVERIFIED ON REAL HARDWARE**, and more than usual this
time -- this sandbox has no real window manager, so nothing here can
confirm actual desktop stacking behavior, only that the right Qt flags
and parent are set. Please test specifically: (1) float a panel or open
a dialog, (2) click into a DIFFERENT application (Finder, a browser) --
confirm the LUTier window now sits BEHIND it, not on top, (3) click back
on LUTier's main window -- confirm the floated panel/dialog comes back
above the main window specifically. If `Qt.Tool` doesn't hold up
reliably in practice on macOS, `floating_dialog.py`'s docstring notes a
fallback (toggling `WindowStaysOnTopHint` based on
`QApplication.applicationStateChanged`) -- not built since it's
meaningfully more complex and Tool is the documented right tool for this
exact scenario; only worth it if Tool is confirmed insufficient.

## 40. Free-form panel rearranging: migrated to QMainWindow + QDockWidget

Tom, after confirming #38/#39's fixes: "Great. This is working... can you
tell me how hard it would be to make the panels re-arrangeable in
session." Two options were discussed (hand-rolled drag-and-drop vs. Qt's
native docking system), recommending the native one given this project's
extensive GL-widget reparenting crash history -- hand-rolling drag
mechanics on top of that would mean re-deriving all of it a second time.
Tom: "Yeah. we want free form." Then, after a checkpoint request: "Yes
you can go for it now but make a known good summary first please. do the
migration to new system" (`KNOWN_GOOD_SUMMARY_2026-07-24-6.md`, written
first as asked, is the pre-migration rollback point).

**What changed:** `main_window.py`'s layout -- previously a `QWidget`
central widget containing three hardcoded, nested `QSplitter`s
(`main_splitter`/`left_splitter`/`center_splitter`/`right_splitter`),
built once at startup with no runtime concept of moving a panel between
them -- is now ten `QDockWidget`s (one per panel, via the new `_make_dock`
helper) placed via `QMainWindow`'s own `addDockWidget`/`splitDockWidget`
(`_build_default_dock_layout`, called at startup and by "Reset to Default
Workspace"). Every panel can now be dragged by its title bar to any edge
of any other panel, tabbed with a neighbor, or torn off into a floating
window -- Qt's own native docking mechanics, not anything hand-rolled
here. `MainWindow` was already a `QMainWindow` subclass (for `menuBar()`),
so this is a rearchitecture of the central layout, not a base-class
change.

**Title bars:** each dock gets a custom title bar (`_build_dock_title_bar`)
reusing the exact `#panelTitleRow`/`#panelTitle` object names `style.py`
already had (from `PanelFrame`'s own title row, pre-migration) -- same
visual language, zero new CSS. `PanelFrame` is still used underneath each
dock (`PanelFrame(None, content_widget)`), now purely for its
border/background; its own title-row responsibility moved to the dock's
title bar, since drawing both would have meant two stacked header rows
for one panel -- the exact "two rows" anti-pattern Tom already flagged
once before (#38, corner-menu placement) in a related context, avoided
here deliberately rather than repeated. Each panel's existing "..."
corner-menu button (`self.viewer_3d.menu_button` etc., unchanged) sits on
this new title bar instead, same spot it held in `PanelFrame`'s row
before.

**Floating subsumes #39's fix, doesn't duplicate it:** confirmed
empirically in this sandbox (a standalone `QMainWindow`/`QDockWidget`
script, not assumed) that `QDockWidget.setFloating(True)` already
produces exactly `Qt.WindowType.Tool`, parented to the main window, with
NO `Qt.WindowStaysOnTopHint` -- the identical end state #39 built by hand
(`setParent(self, Qt.WindowType.Tool)`) is what Qt's own native floating
already does. `_float_panel`/`_dock_panel` are now thin wrappers over
`dock.setFloating(True/False)`, still passed through `_reparent_gl_safely`
(the `setUpdatesEnabled` paint-suspend dance) for the paths this file
triggers directly (Workspace menu, Cmd+Shift+N shortcut).

**One acknowledged, deliberately-flagged gap:** a panel can now ALSO
become floating by being DRAGGED out, which Qt performs internally with
no hook to suspend paint delivery beforehand the way `_reparent_gl_safely`
does for the paths this file controls. The three other GL-safety layers
(`_SafeGLViewWidget.isValid()` guard, `rebuild_gl_view()`'s full rebuild
after any transition, `AA_ShareOpenGLContexts`) are unchanged and still
apply regardless of what triggered the transition -- but this is the one
scenario this migration couldn't directly test against the original
crash class, and is called out explicitly (not glossed over) in
`main_window.py`'s own docstring. Please test dragging the Graph panel
(the one with live GL content) specifically, repeatedly, on real hardware.

**A new `_on_dock_top_level_changed` handler**, connected to every dock's
`topLevelChanged` signal at construction, replaced the old scheme of only
updating `_floating_panels`/the Float-menu checkbox/`_rebuild_gl_content_
for_panel` from inside `_float_panel`/`_dock_panel` themselves -- fires
for ANY float/dock transition regardless of cause (this file, the
shortcut, or a drag), which a hand-rolled call site inside just those two
methods couldn't have covered for the new drag path. This also let a
whole mechanism be deleted outright: `MainWindow.eventFilter` (catching a
floating panel's native close button via `QEvent.Close`) is gone,
replaced by a much smaller `_PanelDockWidget` subclass (`closeEvent`
override: while floating, `event.ignore()` + `setFloating(False)` instead
of Qt's own default "just hide it" -- confirmed empirically that Qt's
default here would otherwise leave a new, never-before-exercised
"hidden-but-still-floating" state) -- deliberately kept behaving exactly
like the pre-migration eventFilter, since that's the exact path Tom's
original crash report went through and it was already tested against
that history; not an oversight that a "hide" default was available and
simpler.

**Workspace persistence rewritten around Qt's own state, not a hand-
rolled dict:** `get_workspace_state`/`load_workspace_state` now capture/
restore `QMainWindow.saveState()`/`restoreState()` (a `QByteArray`,
base64-encoded for JSON storage as `dock_state_b64`) instead of the old
`{window_size, panels, splitters, floating}` dict -- Qt's own mechanism
already captures dock position/size/floating/geometry together, correctly,
for an arbitrary drag-built arrangement that has no fixed "which splitter
is my home" structure to hand-serialize anymore. `restoreState()` is
still deferred via `QTimer.singleShot(0, ...)` (`_apply_dock_state`) --
same one-tick discipline #33 established for the old floating-restore
loop, since restoreState() can reconstruct a floating dock (with a live
QOpenGLWidget child) exactly like that old loop could, and there's no
reason to assume Qt's own restore mechanism is immune to the timing risk
#33 diagnosed as the actual root cause behind #29-#32 never visibly
helping on their own. `workspace_io.py`'s `WORKSPACE_FORMAT_VERSION`
bumped 1 -> 2 (purely informational -- that module does no per-field
interpretation, see its own docstring); `load_workspace_state` treats a
dict with no `dock_state_b64` key (an old, pre-migration remembered-
workspace or preset file) as empty, falling back to whatever layout is
already in place, rather than attempting to apply `panels`/`splitters`/
`floating` keys that no longer mean anything.

**"Reset to Default Workspace" no longer goes through `load_workspace_
state`** -- the default arrangement is deterministic CODE
(`_build_default_dock_layout`'s `addDockWidget`/`splitDockWidget` calls),
not a serialized blob, so `_reset_workspace_to_default` rebuilds it
directly. **A real bug caught by this round's own new tests, not shipped
silently:** the first version of this method didn't actually redock a
currently-floating panel -- confirmed empirically that neither
`addDockWidget()` nor `splitDockWidget()` reliably redocks a widget
that's ALREADY floating (both silently leave it floating), unlike
`restoreState()`, which handles both directions correctly (also confirmed
empirically). Fixed by calling `_dock_all_floating_panels()` explicitly
first.

**A second real bug caught while writing this round's tests, unrelated
to the migration's own logic:** `MainWindow.closeEvent` has always written
the CURRENT workspace state to `workspace_io`'s fixed, real
`DEFAULT_WORKSPACE_PATH` on every `win.close()` -- and
`test_gl_reparent_safety.py` (unlike `test_workspace_io.py`, which always
redirected this path for its own tests) never redirected it, so every
`MainWindow()`-constructing test in that file was writing to, and reading
back from, the SAME real file on disk, across test runs. A test that
floated the Graph panel then closed would leave that real file saying so;
a completely unrelated LATER test's fresh `MainWindow()` would read it
right back on its own next `QApplication.processEvents()` call and end up
with a panel floating it never asked for -- caught via a new test
(`test_load_workspace_state_tolerates_pre_migration_format`) unexpectedly
failing with the Graph panel floating despite the test dict having no
`dock_state_b64` at all. Fixed by redirecting `workspace_io.
DEFAULT_WORKSPACE_DIR`/`DEFAULT_WORKSPACE_PATH` to a temp directory once,
module-level, in `test_gl_reparent_safety.py` -- same discipline
`test_workspace_io.py` already had, now applied here too. This was a
pre-existing test-isolation gap (present before this migration as well,
just never exercised by an assertion specific enough to notice it), not
something the docking migration introduced.

**Tests:** `test_gl_reparent_safety.py` grew from 38 to 55 tests -- 5
rewritten to use the new API (`right_splitter` membership check ->
`isFloating()`/`isWindow()` checks; `win.eventFilter(frame, QEvent.Close)`
-> `frame.close()`, now the REAL call path instead of a simulation of it;
the old-format `{"floating": [...]}` dict-based deferred-restore tests
rebuilt around a real `get_workspace_state()`-captured blob, since that's
the only shape that means anything now), 6 new ones covering the
migration itself (every panel is a real, placed `QDockWidget`; Movable+
Floatable features are actually set, not just implied by the class; the
custom title bar carries the right label and the real `menu_button`
object, not a copy; Reset to Default redocks AND reshows; an old-format
saved-workspace dict is tolerated as a no-op; a DOCKED panel's close
isn't intercepted, only a floating one's). `test_floating_window_scope.py`
(all 9) re-run unchanged and still pass -- the Qt.Tool scoping they check
holds under `dock.setFloating()` exactly as it did under the old
`setParent()`+`setWindowFlags()` call. Full suite: 304 passed (was 298),
same 19 pre-existing `/tmp`-permission failures, nothing new broken.
`USER_MANUAL.md`/`help_text.py` updated (Workspace section: dragging to
rearrange, "stays above main window" not "always on top"; Sessions
section: "splitter sizes" -> "position, size") and re-verified
byte-identical.

**Still UNVERIFIED ON REAL HARDWARE**, more than usual -- this sandbox has
no real window manager, so none of the following has ever actually been
exercised, only that the right Qt objects/signals/flags are wired up:
(1) actually dragging a panel's title bar to another dock area or onto
another panel to tab them, (2) dragging a panel out into open space to
float it (vs. this file's own programmatic `setFloating(True)` calls),
(3) the Graph panel specifically, dragged repeatedly, given the
acknowledged GL-safety gap on that path noted above, (4) whether a custom
`titleBarWidget()` feels right to drag from in practice (confirmed only
that it doesn't structurally BREAK dragging, not that it feels good),
(5) whether `QDockWidget`'s native floating window chrome (the OS-level
title bar/close button a floating dock gets in addition to this app's own
custom title row) looks reasonable rather than doubled-up, (6) a real
Export/Import Workspace Preset round trip carrying an actual drag-built
(non-default) arrangement. Please test all six and report back.

## 41. #40 confirmed working on real hardware

Tom, after actually using it: "This is amazing. This works perfectly."
All six items #40 flagged as unverified (dragging a panel to another dock
area, tabbing, tearing off to float, whether the custom title bar feels
right to grab, whether floating chrome looks doubled-up, a real preset
round trip with a drag-built arrangement) are now confirmed working on
real hardware, not just headlessly plausible -- same "confirmed on Tom's
machine" milestone #29-#34's GL crash/render fixes eventually reached.
No code changes this entry -- purely updating this project's own
running record of what's actually been exercised for real versus still
theoretical, per this project's standing discipline of tracking those as
separate facts.

## 42. UI beautification, pass 1: color/accent enrichment (style.py only)

Tom's "beautify the UI" ask from #41, scope narrowed via a quick
clarifying round to specifically color/accents (not spacing/density or
icons -- those are still open, unstarted backlog items). Second
clarifying round settled the actual hue question: enrich the existing
`palette(highlight)` usage rather than introduce a fixed custom brand
accent (an amber or cyan option was on the table) -- keeps the app
respecting the OS's actual accent-color setting, not just light/dark
mode, consistent with `style.py`'s original "no hardcoded color" rule.

Concretely, in `style.py`: `QLineEdit`/`QComboBox`/`QSpinBox`/
`QDoubleSpinBox` get real `:hover` (1px highlight border) and `:focus`
(2px highlight border, padding shrunk 1px to compensate so nothing
jumps) states for the first time -- previously static regardless of
mouse/keyboard focus. `QPushButton:hover`/`:pressed` gain a
highlight-tinted border on top of the existing gray background shift.
`QListWidget::item` gains a `:hover:!selected` state (`palette(midlight)`)
where previously nothing happened before the click that selected a row,
plus a small item padding bump so that hover background doesn't look
cramped. `QGroupBox::title` now uses `palette(highlight)` instead of
plain text color, tying Controls' group headers to the same accent
panel titles already use. A selected stack stage row
(`QFrame#stackStageRow[stageSelected="true"]`) now also colors and
bolds its `QLabel#stageLabel` text, not just its card border -- a
second, stronger selection cue on top of the existing one. Caught one
real correctness issue while writing that last rule: QSS resolves by
selector specificity, not just declaration order, so the new
selected-text rule would have silently outranked the existing
`[stageEnabled="false"]` dimming rule for a row that's disabled AND
selected at once (a real, reachable combination -- selection and the
enabled toggle are independent). Added an explicit, more-specific
override so disabled still wins (dimmed gray, but still bold) rather
than a disabled stage appearing to read as active. No literal hex/rgb
anywhere in the file, same as before this pass.

Verified: `style.py` compiles, `QApplication.setStyleSheet(STYLESHEET)`
applies with no Qt QSS parse warnings on stderr, a full `MainWindow()`
still constructs headlessly with the new stylesheet applied. Full
regression: 304 passed, same 19 pre-existing sandbox-only `/tmp`
`PermissionError` failures, no change from baseline -- expected, since
this pass touched zero non-style code paths.

Untested on real hardware, same libEGL wall as always (this is a purely
visual change, so this gap matters more than most): please confirm (1)
the new hover/focus states on text fields and buttons actually look
like an improvement and not just "flickery," (2) the list-row hover
tint isn't too subtle or too strong against your actual OS accent
color, (3) the group box title color and the stage-row selected-text
color don't clash with anything, (4) the disabled+selected stage row
case specifically -- select a stage, then toggle it off without
deselecting -- reads as "disabled" first, not "highlighted." Spacing/
density and icon polish (see #41's backlog) are still separate,
unstarted follow-ups.

## 43. UI beautification, pass 2: sliders + scrollbars (style.py only)

Tom's follow-up to #42: split "more style" into an easy accent-expansion
pass (this entry) and a separate, bigger texture pass (empty-space
texture on the main window background, not started -- see backlog).

`QSlider` (printer-lights/CDL-sat/fork-blend trims, graticule intensity,
3D grid density, cage opacity -- all currently type-selector-only, none
`setObjectName`'d) had zero styling before this -- fully native look.
Now the filled portion left of the handle (`::sub-page`) uses
`palette(highlight)`, same accent as everything else, so the slider
visually communicates "this much is active" instead of being a plain
gray native control. Handle is a plain accent-filled circle, no separate
hover state -- kept deliberately simple for the "easy" half of this
split. `:disabled` explicitly falls back to `palette(mid)` rather than
trusting Qt to auto-dim a QSS-overridden color, matching the existing
`QPushButton:disabled` pattern already in this file.

`QScrollBar` (only ever appears implicitly, via the `QListWidget`s in
every library/browser panel -- Browser, LUT/CDL/Image Library, Stack
Memories, the Stack list itself) also had zero styling before -- whatever
the native OS scrollbar looked like. Now: thin (10px), transparent
track, no arrow-button steppers, handle at `palette(mid)` quiet-at-rest
so it doesn't compete with list content, `palette(highlight)` on hover
only.

Deliberately skipped this pass: `QCheckBox`/`QRadioButton` indicators.
Styling `::indicator` at all switches Qt off native painting for that
subcontrol including the checkmark glyph -- doing it properly needs an
actual image asset, not just palette() colors, which is real asset work
rather than a QSS tweak, so it's deferred to whenever the texture pass
happens (same kind of work, image assets rather than pure palette()
colors). No `QRadioButton` exists anywhere in this codebase currently
either way.

Verified same way as #42: `style.py` compiles, stylesheet applies with
no Qt QSS parse warnings on stderr, a full `MainWindow()` still
constructs headlessly. Full regression: 304 passed, same 19 pre-existing
sandbox-only failures, unchanged from #42's baseline.

Untested on real hardware, same libEGL wall, same reason it matters more
than most (purely visual): please confirm (1) slider fill/handle color
actually looks intentional and not just "half-colored," (2) scrollbar
handle is easy enough to grab at 10px without accidentally feeling too
thin, (3) scrollbar hover-highlight doesn't feel laggy/inconsistent
against your actual OS's native scrolling behavior (momentum scrolling
on macOS can make hover state feel different from a plain click-drag
scrollbar). Texture pass (main window background) and spacing/density/
icons (see #41 backlog) still open.

## 44. UI beautification, pass 3: herringbone background texture

Tom's follow-up to #43: the deferred texture pass. Generated five
candidate patterns (grain/dots/diagonal-hatch/grid/herringbone) as
preview swatches -- each rendered against both a light and dark backdrop
in one image so the "no separate light/dark asset needed" claim was
checkable before committing to one (see texture_design/gen_textures.py
and gen_herringbone.py, kept on disk as the record of what was tried,
not part of the shipped app). Tom picked herringbone.

`assets/bg_herringbone.png`: a 48px seamless tile, neutral gray
(128,128,128) diagonal strokes at low alpha (34/255) -- same
"alpha-blended neutral gray reads correctly against both light and dark
without detecting the theme" trick as everything else, no literal hex
anywhere in style.py itself (the PNG's own pixels are the one place
actual color values live, unavoidably, but they're neutral gray, not a
themed color). Wired into `QMainWindow`'s stylesheet via a new
`_texture_asset_path()` helper in style.py -- duplicates
`app_logic.default_sample_image_path`'s exact RESOURCEPATH-vs-__file__
logic (same py2app-freezing wrinkle, different module, no existing
dependency between the two to share it through) since a frozen build
needs to find assets/ under Contents/Resources rather than inside the
zipped .pyc archive. `setup.py`'s `DATA_FILES` updated to bundle the new
asset alongside the existing default sample image.

Real bug caught while wiring this up, not just cosmetic: the obvious
first attempt (`background-image: url(...); background-repeat: repeat;`)
does NOT tile in Qt's QSS engine -- confirmed empirically by
screenshot-grabbing a bare headless `QMainWindow` and a bare `QWidget`
with that exact stylesheet: the image painted once in the top-left
corner and stopped, `background-repeat` having no visible effect at all.
Switched to `border-image: url(...) 0 0 0 0 repeat repeat` instead
(zero slice widths on all sides, explicit horizontal+vertical repeat
keywords) -- confirmed tiling correctly the same way, then confirmed
again in the real app with two panels hidden to expose actual empty
`QMainWindow` background (Viewer/Stack/Parameters/Scopes/Graph fill the
whole window in the default layout -- there's normally no empty space to
see this at all outside a rearranged/partial workspace, worth knowing
going in). This is the kind of thing that's *easy* to ship silently
broken (no error, no warning, the stylesheet "applies" fine, it just
doesn't look right) -- worth flagging as a general caution for any
future QSS background-image work in this file, not just this one
instance.

Verified: `style.py`/`setup.py` compile, stylesheet applies with no Qt
QSS parse warnings on stderr, `MainWindow()` constructs headlessly, and
-- new this entry -- an actual headless screenshot (`QWidget.grab()`
under `QT_QPA_PLATFORM=offscreen`) visually confirms the tile repeats
correctly, both on a bare window and inside the real app with an
exposed gap. Full regression: 304 passed, same 19 pre-existing
sandbox-only failures, unchanged from #43's baseline.

Known limitation, not solved here: single-density asset, no @2x variant
-- may read softer than the rest of this file's crisp vector-drawn QSS
chrome on a real Retina display. Untested on real hardware for that
specific reason (a headless screenshot proves the tiling MECHANISM
works, not how crisp/sharp/dense it actually looks at native resolution
and real physical size): please confirm (1) it doesn't look blurry
compared to everything else, (2) the tile density/scale feels right at
actual window size, not too busy or too subtle, (3) it doesn't fight
visually with the herringbone-adjacent diagonal `[stageBranch]` accent
borders on stack stage cards (#42) since both are diagonal-ish accents
now, just in very different places. Spacing/density and icon polish
(see #41 backlog) still the only open beautification items after this.

## 45. UI beautification, pass 4: grain on panel separators

Tom's follow-up to #44: keep the herringbone background, add the
"grain" candidate (the other option from the original five in #44) on
`QMainWindow::separator` specifically -- the real, documented Qt
subcontrol for the draggable dividers BETWEEN docked panels, distinct
from `QFrame#panelFrame`'s own static per-panel border.

`assets/border_grain.png`: reuses the exact density (22%) and alpha
range (8-26/255) as the original grain preview Tom already saw, at the
original 32px tile size (not bumped up like `bg_herringbone.png` was --
a separator strip is only a few px thick, so extra tile density
wouldn't be visible either way). Wired in the same way as #44's
`QMainWindow` rule: `border-image` (not `background-image` +
`background-repeat`, which #44 already established doesn't tile),
`palette(mid)` background-color underneath as the fallback/base layer.
Explicit `width`/`height: 6px` on the subcontrol -- Qt's own default
separator thickness is style-dependent and thin enough the grain would
barely register; 6px is a real-hardware taste call like everything else
visual in this file, not a settled number. `setup.py`'s `DATA_FILES`
updated to bundle the new asset alongside the other two.

Verified the same way as #44, including the same "don't trust a QSS
rule that applies without error to actually look right" lesson: a
headless screenshot (`QWidget.grab()`), this time cropped and upscaled
around an actual separator strip between docked panels, to directly
confirm the grain tiles along the separator rather than assuming
`border-image`'s tiling behavior (confirmed for `QMainWindow` in #44)
automatically carries over to a different selector/subcontrol. It does.
Full regression: 304 passed, same 19 pre-existing sandbox-only
failures, unchanged from #44's baseline.

Untested on real hardware, same reason as always (purely visual, and
this time genuinely visible in the DEFAULT layout unlike #44's
background texture, since separators exist between every panel
regardless of arrangement): please confirm (1) 6px reads as an
intentional grain strip and not just "a slightly dirty-looking
divider," (2) the separator is still comfortably grabbable/draggable at
that width, (3) grain-on-separators + herringbone-on-background
together doesn't feel like two competing textures fighting each other
now that both are live at once. Spacing/density and icon polish (see
#41 backlog) still the only open beautification items after this.

## 46. Real bug: herringbone texture bleeding through panel title bars

Tom, on real hardware, asked me to look at a screenshot showing a
chevron/zigzag pattern visibly behind a panel's title ("Stack") and its
"..." corner button -- not the separator grain from #45, something else
entirely. Reproduced headlessly before touching anything
(screenshot_titlebar_zoom.png, cropped tight on a title row) -- same
bleed-through, confirming a real bug rather than a dark-mode-only
rendering quirk.

Root cause: `QWidget#panelTitleRow` (a `QDockWidget`'s
`setTitleBarWidget`, see main_window.py's `_build_dock_title_bar`) never
had an explicit `background-color` -- it painted transparent, which was
completely invisible/harmless back when `QMainWindow`'s own background
was a flat `palette(window)` color (transparent-showing-flat-color and
opaque-flat-color look identical). #44 changed `QMainWindow`'s
background to the herringbone `border-image`, and that pre-existing
transparency suddenly became visible: every panel's title bar sat
directly on top of the textured `QMainWindow` surface with nothing
opaque between them, so the texture showed straight through behind
every single panel's title and "..." menu button, not just the one Tom
happened to screenshot.

Fix: `QWidget#panelTitleRow` gets `background-color: palette(base)` --
matches `QFrame#panelFrame`'s own background so the title row and the
panel's content below it still read as one continuous card, no visible
seam introduced by the fix itself. Verified by re-running the exact same
headless crop-and-zoom that reproduced the bug
(screenshot_titlebar_zoom_fixed.png) -- clean solid background now, no
texture visible -- plus a full-window screenshot confirming every
panel's title bar is clean, not just Stack's. Full regression: 304
passed, same 19 pre-existing sandbox-only failures, unchanged.

This is the second real (not just cosmetic-taste) bug this beautify-the-
UI arc has caught via careful verification (#44's border-image-vs-
background-image tiling bug was the first) -- both were the kind of
thing that applies without any error or warning and just quietly looks
wrong, which is exactly why every pass in this arc has insisted on an
actual rendered screenshot rather than trusting "the stylesheet applied
without raising." Worth Tom re-confirming on real hardware that this
specific fix looks right now, same as every other visual change in this
arc.

## 47. New keyboard shortcuts (partial -- 11 of 38 requested wired in)

Tom sent back a filled-in list of desired shortcuts for every menu-bar
item across the app. Before wiring anything in, checked each one for
collisions -- both against every existing shortcut (File/Edit/Workspace
already had 27) and against normal text entry, since a menu QAction's
default shortcut context (WindowShortcut) fires window-wide, including
while a text field has focus.

**Wired in (11, all Ctrl or Ctrl+Shift -- the one modifier that never
composes/types a character in a focused field, so genuinely collision-
free):** Image Library Import (Ctrl+I), Load as Sample (Ctrl+L), Load as
Reference (Ctrl+R); Stack Memories Save Current Stack (Ctrl+G), Load
(Ctrl+Shift+G), Update (Ctrl+U), Export (Ctrl+Shift+E); Viewer Enable
Wipe (Ctrl+W); Scopes Histogram/Waveform/Vectorscope (Ctrl+1/2/3).
Confirmed zero collisions with the existing 27 by walking the actual
built menu bar's QActions headlessly and diffing every `QKeySequence`
(script inline in this session, not a checked-in test) -- 38 total
shortcuts now, all unique. Full regression: 304 passed, same 19
pre-existing sandbox-only failures.

**NOT wired in (27) -- flagged back to Tom rather than implemented
as-asked:** three separate collision classes, all sharing one root
cause (only Ctrl/Cmd is guaranteed to never produce a character):
- Bare digit, no modifier (Viewer's 10 submenu items: Adapt Sample
  Image.../Export Still.../Load Default Sample/Adapt Reference... as
  7/8/9/0, Left/Right Image's 6 choices as 1-6) -- would steal digit
  keystrokes from every numeric field in the app (Grid density, Cage
  opacity, Printer Lights/CDL SOP-SAT trims, Domain bounds, Graticule
  Intensity, and more).
- Bare letter, no modifier (Stack's 6 "Add stage" actions as p/a/c/n/f/x,
  Help > Manual as m) -- would steal those letters from every text
  field (Browser search, any Rename... dialog, etc.) whenever pressed.
- Shift+letter or Alt+letter (Workspace > Reset as Shift+W; LUT Library
  Import/Apply as Alt+I/Alt+A; CDL Library Import/Apply as Shift+I/
  Shift+A; Graph's 5 view choices as Alt+1-5) -- Shift+letter is just
  how a capital letter gets typed, so same problem as the bare-letter
  case; Alt+letter has an extra macOS-specific wrinkle on top --
  Option+letter is how several accented/special characters get composed
  (Option+I is literally the circumflex dead-key, Option+A types "å"),
  so it wouldn't just block a plain letter, it'd block composing those
  characters at all anywhere in the app.

Every one of these 27 QAction objects already exists in the code with
its label and callback wired exactly as before -- only `.setShortcut()`
is deliberately not yet called, with an inline comment at each one
explaining why, so nothing here is silently missing or forgotten.
Tom's call, once shown the tradeoff: skip all 27 rather than rework them
with a Ctrl prefix. Final state -- 11 of 38 requested shortcuts shipped,
27 explicitly not implemented (each with an inline comment at the call
site explaining why, not just silently absent), 38 total shortcuts in
the app, zero collisions. This closes out the keyboard-shortcuts ask.

## 48. Real bug: packaged app crashed on launch (py2app build, `ModuleNotFoundError`)

Tom's first py2app build of this checkout succeeded cleanly (no errors in
the ~57k-line build log -- the giant "Modules not found (conditional
imports)" tail is normal py2app noise, not a problem, and both new
texture assets landed correctly under `Contents/Resources/assets/`) but
`dist/LUTier.app` crashed immediately on launch. Running the binary
directly (`./dist/LUTier.app/Contents/MacOS/LUTier`) surfaced the real
traceback GUI-launching a `.app` normally swallows:

```
ModuleNotFoundError: No module named 'main_window'
```

...from inside `_print_startup_verification_banner()`, a leftover
debug helper from the #29-#32 GL-crash-chasing era, explicitly marked
in its own docstring as "TEMPORARY... Safe to delete once that's
confirmed no longer in question -- purely diagnostic, no behavior
depends on it." It did `__import__("main_window")` to print the running
file's path/mtime as a sanity check. That accidentally worked every time
under `python3 main_window.py` (running a script directly puts its own
directory on `sys.path`, so Python can re-import the same file under its
own name as a technically-separate module object) but has no equivalent
in a frozen py2app build -- the entry script runs as `__main__` via
`__boot__.py`'s `exec()`, never registered under the name `main_window`
in `sys.modules`, and there's no ordinary importable module by that name
on the frozen `sys.path` either. Every previous verification of this app
(`python3 main_window.py`, plus every headless sandbox check this whole
session) went through the unfrozen path where the accident held --
today's py2app build was the first time this code path ever actually
ran frozen, and it broke immediately.

Fix: deleted `_print_startup_verification_banner()` and its call in
`main()` entirely, per its own stated deletion criteria -- the GL crash
bugs it was added to help diagnose were confirmed fixed multiple
checkpoints ago (#33 area onward, long since superseded by #40/#41's
real-hardware confirmation). Nothing else in the app depended on it
(grepped for other references -- none). Verified: compiles,
`MainWindow()` still constructs headlessly with no crash, full suite now
**323 passed, 0 failed** (the 19 previously-failing sandbox-only `/tmp`-
permission tests cleared up on their own between sessions -- unrelated
environment drift, not caused by or related to this fix).

This is the third real (not cosmetic) bug this session's verification
caught -- #44's border-image tiling bug and #46's title-bar texture
bleed were both caught by insisting on an actual headless screenshot
rather than "the code runs without raising"; this one was only catchable
by Tom actually attempting a real frozen build and sharing the real
crash output rather than a GUI dialog, which is exactly why asking for
`Contents/MacOS/LUTier`'s direct terminal output (instead of guessing
from "it fails to launch") mattered here.

**Next step for Tom:** rebuild (`python3 setup.py py2app` from a clean
`build`/`dist`, standard practice) and relaunch. Nothing else in this
traceback pointed at a second problem, but that's still only proven once
a fresh build actually launches all the way to a visible window.

## 49. Manual updated with this session's new shortcuts

Tom asked for the manual to cover this session's new features. The
UI-beautification pass (#42-#45: accent colors, sliders/scrollbars,
herringbone background, separator grain) is purely visual, not a new
capability, and the manual's own stated scope is a functional feature
outline for testers (see its own opening line) -- deliberately not
documented there. The 11 new keyboard shortcuts from #47 (the 27
held-back ones from that same entry are, correctly, still absent -- they
were never actually wired in) ARE a real new capability, so added to the
Keyboard Shortcuts table in both `help_text.py`'s
`USER_MANUAL_MARKDOWN` and `USER_MANUAL.md`: Image Library's
Import/Load as Sample/Load as Reference (⌘I/⌘L/⌘R), Stack Memories'
Save/Load/Update/Export (⌘G/⌘⇧G/⌘U/⌘⇧E), Viewer's Enable Wipe (⌘W),
Scopes' Histogram/Waveform/Vectorscope (⌘1/⌘2/⌘3) -- same ⌘/⇧ glyph
convention the existing rows already use.

Verified: both files compile, re-confirmed byte-identical (diffed the
file against the constant, matching this project's standing "keep these
two in sync" discipline), and actually rendered the new markdown through
`QTextBrowser.setMarkdown()` headlessly to confirm it parses and the new
rows show up in the rendered text, not just in the source string. Full
suite: 323 passed (matches #48's cleared baseline), no regressions.

## 50. Dock title bars get native-look float/close buttons

Tom noticed another app built from this project's migration notes had
nicer-looking pop-out/pop-in buttons and asked for the same treatment
here, then explicitly said to wire it into LUTier itself rather than
just hand back a snippet. `_build_dock_title_bar` (`main_window.py`) now
adds two `QToolButton`s -- `dockFloatButton` (toggles
`dock.setFloating()`, uses Qt's own `SP_TitleBarNormalButton` icon) and
`dockCloseButton` (calls `dock.close()` -- not `.hide()`, so it goes
through the exact same close path the Workspace menu/toggleViewAction
and `_PanelDockWidget.closeEvent`'s redock-if-floating logic already
use, not a second parallel path) -- placed on the title row before the
existing "..." corner menu button, `autoRaise=True` for flat/borderless-
until-hover chrome. `style.py` adds matching QSS
(`QToolButton#dockFloatButton, QToolButton#dockCloseButton`) with a
`palette(highlight)` hover backdrop, consistent with this project's
standing "build hover/focus off `palette()`, not a fixed hex" rule.
`_PanelDockWidget`'s docstring updated to note the close button is a new
path to the same `closeEvent` behavior, not a Qt-native
`DockWidgetClosable` X (this custom title bar never had Qt's native one).

Verified: compiles clean; a headless screenshot
(`texture_design/screenshot_titlebar_buttons.png` and
`..._zoom.png`) confirms float button, close button (X), and the "..."
menu button all render together, in that order, on every panel's title
bar. Full suite re-run fresh this migration (new sandbox, dependencies
reinstalled from `requirements.txt`): **304 passed, 19 failed** -- the
19 are the same pre-existing hardcoded-`/tmp`-path `PermissionError`s in
`test_format_writers.py`/`test_image_preview.py` this project has seen
every session (sandbox-specific, unrelated to this change); nothing
docking- or title-bar-related among them.

This closes out the interrupted work from the previous chat (the
verification's last step -- the full suite re-run -- never finished
before that session ended). **Tom is now testing this on real hardware**
(dragging to float/dock, tabbing, drag-to-rearrange with the new buttons
present) -- not yet confirmed working outside the headless sandbox.

## 51. Shift-to-dock override -- floating panels no longer eagerly redock

Tom's next report: once a panel is popped out, it's hard to position it
as an overlay over the main window -- stock Qt's QDockWidget shows its
dock-preview indicator and snap-redocks the instant a floating panel is
dragged anywhere near an existing dock's edge, and with
`setDockNestingEnabled(True)` (needed for this app's nested-column
default layout) that's nearly the entire main window, not just the
seams. There's no public Qt API to shrink that hit-test margin, and
narrowing each panel's `setAllowedAreas()` to a fixed home edge was
considered and rejected -- it would also block freely relocating a panel
to a DIFFERENT column/edge, a feature confirmed working in RUNNING.md
#41.

Tom's explicit call once shown the tradeoff: invert Qt's default rather
than tune it -- floating panels should NOT eagerly redock by default,
and holding Shift is what turns Qt's normal eager snap-preview/redock
behavior back ON for as long as it's held (the opposite of the more
obvious "hold a key to suppress docking" pattern).

Implementation, all in `main_window.py`:
- `_on_dock_top_level_changed` now also sets the just-floated panel's
  `allowedAreas` to `NoDockWidgetArea` the instant it starts floating
  (checking `QApplication.keyboardModifiers()` first, in case Shift
  happens to already be held at that exact instant), and resets it back
  to Qt's own default (`AllDockWidgetAreas`) the instant it redocks --
  so the NEXT float-out always starts from a deliberate, known state
  rather than inheriting whatever Shift last left it at.
- A new `eventFilter`, installed on the `QApplication` SINGLETON (not
  `self`) in `__init__` -- has to be app-wide because a floating panel is
  its own separate top-level window, so Shift key events during the drag
  land on THAT widget, not on MainWindow. Watches raw KeyPress/KeyRelease
  for `Qt.Key.Key_Shift` (autorepeat events filtered out via
  `isAutoRepeat()`) and flips every currently-floating panel's
  `allowedAreas` between `AllDockWidgetAreas` (Shift held) and
  `NoDockWidgetArea` (released) in real time, mid-drag.
- `closeEvent` now calls `QApplication.instance().removeEventFilter(self)`
  before tearing down. Real usage only ever builds one MainWindow so this
  was latent, not a live bug there -- but it surfaced immediately as a
  ~2x test-suite slowdown (21s -> 45s+ timeout) once discovered, because
  the test suite's throwaway-`MainWindow()`-per-test pattern (28
  instances across `test_floating_window_scope.py`/
  `test_gl_reparent_safety.py`) was leaving one stale filter registered
  against the shared `QApplication` instance per instance ever
  constructed, each one still consulted on every event for the rest of
  the process.

Verified: compiles clean. A standalone headless script drove the actual
transition logic end-to-end -- float with Shift up -> `NoDockWidgetArea`;
simulated Shift press via a real `QKeyEvent` through `eventFilter` ->
`AllDockWidgetAreas`; release -> back to `NoDockWidgetArea`; redock ->
reset to `AllDockWidgetAreas` -- all five checks passed. Full suite:
**304 passed, 19 failed** (same standing `/tmp`-permission baseline, no
new failures, no regression in the 53
`test_floating_window_scope.py`/`test_gl_reparent_safety.py` tests
specifically -- reran those two files alone to confirm, 53/53 passed).
Runtime back to ~21s after the `removeEventFilter` fix (was hanging past
45s before it).

**Not yet verified:** the actual feel of this on real hardware -- whether
suppressing the indicator entirely (rather than just narrowing it) is
the right amount of "not eager," and whether reaching for Shift
mid-drag feels natural. Tom's call once tried.

**Confirmed working** -- Tom tried it on real hardware and reported back
"that works great."

## 52. UI cleanup: corner menu button's stray arrow, separator grain removed

Two small polish items from Tom looking at a real screenshot.

**1. The "..." corner menu button was showing "..." AND a dropdown
arrow.** Every panel's `_build_menu_button` (`_build_list_menu_button`
in `stack_panel.py`) calls `setMenu()` on a plain `QToolButton()` with no
`objectName` -- unlike `dockFloatButton`/`dockCloseButton` (#50), this
button was never actually styled by this file at all, so it rendered
with Fusion's raw default QToolButton chrome: raised/bordered, AND
Fusion draws a small arrow indicator for any QToolButton with a menu
regardless of `ToolButtonPopupMode` (`InstantPopup` here didn't
suppress it) -- so it really was showing the same "open this menu"
affordance twice. Fixed in both places:
- All 8 panel files (`cdl_library_panel.py`, `lut_library_panel.py`,
  `scopes_panel.py`, `sample_image_panel.py`, `stack_memories_panel.py`,
  `image_library_panel.py`, `lut_viewer_3d.py`, `stack_panel.py`) now
  give their menu button `setObjectName("panelMenuButton")` and
  `setAutoRaise(True)`.
- `style.py`: folded `QToolButton#panelMenuButton` into the exact same
  flat/`autoRaise`/`palette(highlight)`-hover rule `dockFloatButton`/
  `dockCloseButton` already use (#50) -- genuine visual parity, not a
  similar-but-separately-tuned rule. Added a new blanket
  `QToolButton::menu-indicator {{ image: none; width: 0px; }}` rule --
  safe app-wide since these 8 buttons are the only `QToolButton`s in the
  app that ever have a menu set, so nothing else has a menu-indicator
  subcontrol to suppress.

**2. Separator grain texture read as "rough edges," not texture.** The
FIFTH PASS grain treatment on `QMainWindow::separator` (RUNNING.md,
style.py comment) looked intentional in the headless screenshot it was
verified with, but on Tom's real display it just looked rough. Removed
the `border-image` from that rule entirely -- flat `palette(mid)`, same
6px width/height as before (thickness itself was never part of the
complaint, still a reasonable splitter-handle size). `_GRAIN_TEXTURE`
and `border_grain.png` are left in place, just unused, per that rule's
own updated comment (SIXTH PASS), in case a future pass wants texture
back with a lighter touch.

Verified: all 9 touched files compile clean. Headless screenshot
(`texture_design/screenshot_menu_button_zoom.png`) confirms the "..."
button now renders with no arrow, centered, same flat chrome as the
float/close buttons next to it;
`texture_design/screenshot_ui_cleanup_full.png` confirms separators
render as flat solid bars, no dot pattern. Full suite: **304 passed, 19
failed** -- same standing baseline, no regressions.

**Not yet confirmed on real hardware** -- headless screenshots are a
good proxy but Tom's own dark-mode display is the real test, same as
every other visual change in this file.

## 53. UI cleanup, round two: curved corners everywhere

Tom's follow-up after #52: the menu-button fix landed but the app still
read as hard-edged overall. Root cause -- `QFrame#panelFrame` already had
`PANEL_BORDER_RADIUS`, but every docked panel's actual title lives in a
separate sibling widget (`QWidget#panelTitleRow`, handed to
`QDockWidget.setTitleBarWidget()`) sitting ABOVE panelFrame, not inside
it (every live `PanelFrame()` call passes `title=None` now, confirmed by
grepping -- the docking migration moved the title out, see
`panel_frame.py`'s own docstring). panelTitleRow previously had only a
`border-bottom` divider line -- no left/right/top border, no radius at
all -- so the most visually prominent part of every panel (its header)
was plain and square no matter what panelFrame itself did underneath.

Changed in `style.py` (see file's own SEVENTH PASS docstring for full
reasoning): `panelTitleRow` now gets the same full
`border: 1px solid palette(mid); border-radius: {PANEL_BORDER_RADIUS}px`
treatment as panelFrame (plus real padding instead of just
`padding-bottom`), so header and body each read as their own clean
rounded piece -- deliberately NOT merged into one continuous outline,
since they already sit `margin-bottom: 4px` apart (the deliberate gap
from #46's title-bleed fix; closing it to seam the two shapes together
would risk reopening that bug's conditions). Also rounded: `QListWidget`
(every library/browser list had a background-color but no border/radius
at all before -- hard native rectangular frame), `QMenu` and
`QComboBox`'s popup view, `QToolTip`, and `QMainWindow::separator` (a
light 3px radius even on the thin 6px drag handle, for consistency).
Deliberately NOT touched: `QMainWindow`'s own outer window corners --
that's OS-native window chrome (title bar, traffic lights), not
paintable via QSS; would need `Qt.WA_TranslucentBackground` plus
hand-painting the entire window frame, well outside a stylesheet pass
and not what the screenshot in question was actually showing (panel
chrome, not the outer app window) -- flagged to Tom rather than silently
skipped.

Verified: compiles clean. Headless screenshots
(`texture_design/screenshot_rounded_full.png`,
`..._browser_zoom.png`) confirm every panel now reads as a rounded
title pill over a rounded body card, list boxes/inputs/dropdowns all
show soft corners. Full suite: **304 passed, 19 failed** -- same
standing baseline, no regressions.

**Not yet confirmed on real hardware.**

## 54. Real bug: rounded title bar showed square corners underneath (WA_StyledBackground)

Tom tried #53 on real hardware and reported "nothing has changed" plus
a specific new symptom: visible gray square patches at the corners of
the rounded title bar, and a rough seam where the main window's
herringbone texture met the panel. Both were the same root cause, not
two separate bugs.

`panelTitleRow` (built in `main_window.py`'s `_build_dock_title_bar` --
the actual live title bar every real panel uses; `panel_frame.py`'s own
copy is dead code, no live caller ever passes a real title, see that
file) is a plain `QWidget()`, not a `QFrame`. Plain `QWidget`s do NOT
clip their background to a QSS `border-radius` by default -- only
`QFrame` (which `panelFrame` uses via `setFrameShape(StyledPanel)`)
paints its stylesheet background/border in a radius-aware way
automatically. Without `Qt.WidgetAttribute.WA_StyledBackground` set
explicitly, Qt still painted `panelTitleRow`'s full SQUARE auto-fill
background underneath the rounded border #53 added, so the actual
widget corners showed as a square patch of `palette(base)` poking out
past the rounded outline -- invisible before #53 only because
panelTitleRow never had a `border-radius` to clip to until that pass.
This is also very likely why Tom saw "nothing has changed" overall --
a jarring square-cornered artifact sitting right where a clean rounded
corner was expected reads as "this didn't work," not as a subtler
partial success.

Fix: `row_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)`
right after construction in `_build_dock_title_bar` (`main_window.py`).
Same attribute added to `panel_frame.py`'s dead-code mirror of this
widget too, purely so it stays behaviorally identical to the live one if
that path's ever revived.

Verified: compiles clean. Headless screenshots
(`texture_design/screenshot_titlebar_corner_fix.png`, docked, and
`..._floated_corner_fix.png`, floating -- Tom's screenshot was
specifically of a floated panel, so both states were checked
separately) confirm the corners are now cleanly rounded with no square
bleed in either state. Full suite: **304 passed, 19 failed** -- same
standing baseline, no regressions.

**Not yet confirmed on real hardware** -- this is the second round-trip
on the same visual change; real-display confirmation matters more than
usual here.

## 55. Real bug (and a testing blind spot): dock's own background bleeding through rounded corners

Tom tried #54 and reported no visible change, then specifically said he
could see the same issue even in MY OWN headless verification
screenshot from #54, "just harder to see." That was the key tip-off --
it meant the bug was real and my verification method had a blind spot,
not that the fix silently failed to apply.

Root cause: #54 fixed `panelTitleRow`'s OWN corners correctly, but the
actual visible artifact was never that widget's corners in isolation --
it's `QMainWindow`'s own herringbone-textured background bleeding
through the `QDockWidget`'s bare rectangular cell wherever
panelTitleRow/panelFrame's rounded corners recede from it: every outer
corner, plus the deliberate 4px gap between title and body. #54's
verification used `dock.grab()` on the QDockWidget in isolation, which
fills that recess with a neutral default background instead of actually
compositing what's behind it in the real window -- a real methodology
gap, not a rendering difference between the sandbox and Tom's machine.
Re-grabbing the FULL `MainWindow` instead (`win.grab()`) reproduced the
exact artifact Tom described on the first try.

Fix, same shape as #54's: `QDockWidget` is a plain `QWidget` under the
hood (not a `QFrame`), so it doesn't paint a QSS `background-color`
without `WA_StyledBackground` either. `_make_dock` (`main_window.py`)
now sets that attribute on every dock, and `style.py` adds
`QDockWidget {{ background-color: palette(base); }}` -- giving the
recess a solid color matching the card content instead of textured
bleed-through. The dock's true shape is still a plain rectangle
underneath; this doesn't change that, it just makes the corner region
read as "part of the same card" instead of a mismatched textured patch.

Verified: compiles clean. Re-ran the SAME full-window composited
capture that reproduced the bug, both docked
(`texture_design/screenshot_REAL_composited_corner_AFTER_fix.png`, vs.
`..._BEFORE_fix.png` showing the original artifact for comparison) and
floated (`screenshot_floated_AFTER_dockbg_fix.png`) -- both clean now,
no gray/textured patch at any corner. Full suite: **304 passed, 19
failed** -- same standing baseline, no regressions.

**Lesson for future visual verification in this file:** grab the full
`MainWindow`, not an individual dock/panel in isolation, whenever a
change could interact with what's actually behind a widget in the real
composited layout -- an isolated `widget.grab()` substitutes its own
default background for whatever's truly behind it and can silently hide
exactly this class of bug.

**Not yet confirmed on real hardware.**

## 56. Shift-gated pop-out, the other half of #51's spec [REVERTED, see #57]

Tom's precise spec, once #51's redock-via-Shift landed: Shift+drag a
DOCKED panel should pop it out to floating; Shift+drag a FLOATING panel
should allow redocking (already exactly what #51 built); a plain drag
(no Shift) on a docked panel should only rearrange its position, never
accidentally tear it off into floating; a plain drag on a floating panel
should just move it freely (also already #51). So only the DOCKED-panel
half needed new work -- the floating-panel half was already correct.

Docked panels now default to `DockWidgetFloatable` OFF (`_make_dock`),
leaving `DockWidgetMovable`/`DockWidgetClosable` untouched -- plain drag-
to-rearrange and the close button always work regardless of Shift; only
Qt's own drag-tear-off-into-floating gesture is gated. `eventFilter`
(same Shift KeyPress/KeyRelease watcher from #51) now also flips every
currently-docked panel's `DockWidgetFloatable` on/off in sync, alongside
the existing floating-panel `allowedAreas` toggle. `_on_dock_top_level_changed`
re-syncs a freshly-redocked panel's `DockWidgetFloatable` to the CURRENT
Shift state (same reasoning as #51's allowedAreas reset) rather than
leaving it at whatever a stale drag left it.

**A wrong assumption caught by testing, not assumed away:** the first
draft of this claimed `QDockWidget.setFloating()` ignores the
`DockWidgetFloatable` feature flag, reasoning the flag only gates
*interactive* drag/double-click gestures. Empirically false --
confirmed by direct test that `setFloating(True)` silently no-ops
without the feature. Since a docked panel's default is now Floatable-
OFF, this would have silently broken the dockFloatButton icon, the
Workspace menu's Float toggle, AND the Cmd+Shift+N shortcut the instant
Shift wasn't held -- a real regression, caught before it shipped by
actually testing the assumption instead of trusting the comment. Fixed
by force-adding `DockWidgetFloatable` right before every EXPLICIT
`setFloating(True)` call (`_float_panel`, and the title bar's own float
button) -- deliberate actions always work regardless of Shift; only an
actual drag gesture is gated.

**A second real bug, also caught by testing:** the float button's new
closure was `def _toggle_float(dock=dock):` -- `QToolButton.clicked`
emits `clicked(bool checked=False)`, and PySide inspects a connected
Python callable's argument count to decide how many of the signal's own
arguments to pass; a parameter with a default value still counts, so
Qt/PySide passed the emitted `checked` bool as `_toggle_float`'s ONE
argument, overwriting the intended `dock` default and crashing with
`AttributeError: 'bool' object has no attribute 'isFloating'` the
instant the button was clicked. Fixed by making `_toggle_float()` take
no parameters at all (closing over `dock` normally) and connecting via
`lambda checked=False: _toggle_float()`, which absorbs the emitted
argument instead of colliding with it.

**A testing-methodology footnote, not a code bug:** floating a panel
immediately after constructing `MainWindow()` with no prior `win.show()`
was flaky under this sandbox's headless offscreen platform (worked
sometimes, silently no-op'd others) -- specific to the very first float
operation on a never-shown window. Calling `win.show()` +
`app.processEvents()` first (which every real launch already does, per
`main()`) made it reliable across repeated runs. Worth remembering for
any future headless verification in this file: show the window before
testing float/dock behavior, not just before screenshotting.

Updated `test_gl_reparent_safety.py::test_docks_are_movable_and_floatable`
to match the new spec -- it previously asserted `DockWidgetFloatable` is
always on for every panel, which #56 deliberately makes false; now
asserts Movable is always on, Floatable starts OFF, and that
`_float_panel` still succeeds and correctly redocks (proving "can't be
dragged out by accident" doesn't mean "can't be floated at all").

Verified: compiles clean. Direct empirical test (with `win.show()`
first, see above) confirms: docked+no-Shift has Floatable off with
Movable on; `_float_panel` and the float button both still float
correctly with no Shift held; redocking resets Floatable off; Shift
held flips a docked panel's Floatable on and a floating panel's
allowedAreas to All simultaneously; releasing Shift reverts both.
`test_gl_reparent_safety.py` reran 3x clean (44/44 each time). Full
suite: **304 passed, 19 failed** -- same standing baseline, no
regressions (one NEW failure surfaced mid-pass from the intentional
behavior change, fixed by updating that one test to the new spec, see
above -- not a leftover).

**Not yet confirmed on real hardware.**

## 57. #56 reverted -- "not working," per Tom

Tom tried #56 on real hardware: "not working. it was fine the way it
was. please revert." Reverted in full rather than trying to patch it
further blind -- headless verification had passed every check this
session could construct, but that's exactly the position #54/#55 were
in too before turning out to have a real, only-visible-when-composited-
for-real bug; #56 not working with no more specific symptom to go on
isn't enough to safely guess a fix.

Reverted, all in `main_window.py`: `_make_dock`'s features() call back
to unconditional `DockWidgetMovable | DockWidgetFloatable |
DockWidgetClosable` for every panel; the dockFloatButton's click handler
back to the plain `lambda: dock.setFloating(not dock.isFloating())`;
`_float_panel` back to a bare `setFloating(True)` call, no feature
force-add; `_on_dock_top_level_changed` back to only touching
allowedAreas (no DockWidgetFloatable sync on redock); `eventFilter` back
to only toggling floating-panels' allowedAreas, no docked-panel feature
loop. `test_gl_reparent_safety.py::test_docks_are_movable_and_floatable`
reverted to its pre-#56 form (asserts Floatable is always on for every
panel). Confirmed no `#56`/`_toggle_float` residue left anywhere via
grep.

Net effect: back to exactly #51/#55's behavior -- floating panels don't
eagerly redock unless Shift is held (still in place, Tom confirmed this
part worked); docked panels drag-to-rearrange AND drag-to-float exactly
as Qt's own defaults always allowed, no Shift gating on the pop-out
side.

Verified: compiles clean. Full suite: **304 passed, 19 failed** -- same
standing baseline.

**Standing question for Tom, not yet answered:** what specifically
didn't work? ("not working" could mean the drag-tear-off gate did
nothing, or the float button/menu/shortcut broke, or something else
entirely.) If this is worth revisiting later, that detail is what the
next attempt would need -- this revert restores known-good behavior but
doesn't diagnose what went wrong.

## 58. Real bug: closing a moved floating panel stranded it behind other panels

Tom's report: pop a panel out, close it via the title bar's X -- if it
hasn't been moved since popping out, it redocks cleanly (the intended
behavior, see `_PanelDockWidget`'s docstring). If it HAS been dragged to
a new position first, closing it instead leaves it visually stranded
behind other panels in the main window, not properly redocked.

Root cause: #51/#55's shift-to-dock override leaves a floating panel's
`allowedAreas()` at `NoDockWidgetArea` for as long as it stays floating
without Shift held (deliberately, so a plain drag can't snap it back
in). But `_PanelDockWidget.closeEvent` (the X button) and
`MainWindow._dock_panel` (the Workspace menu's Float-uncheck, and
`_dock_all_floating_panels`'s session-load/app-quit safety net) both
redock by calling `setFloating(False)` directly -- and with
`NoDockWidgetArea` still in effect, Qt has no valid target area to
place the widget back into. It still clears the floating flag and
reparents the widget, just without a proper layout slot, so it comes
back as a stray, badly-positioned widget instead of resuming its real
spot. `_on_dock_top_level_changed`'s own allowedAreas-reset-to-All fires
from the `topLevelChanged` signal too late to help -- by the time it
runs, Qt has already made the (broken) placement decision this exact
`setFloating(False)` call triggered.

Tom's "works until actually moved" detail lines up with this exactly:
right after popping out, Qt likely still has the panel's PRIOR dock slot
cached well enough to reuse regardless of allowedAreas; once genuinely
dragged elsewhere, that association is invalidated, and redocking then
actually has to consult allowedAreas to find a target -- finding none.

Fix: `setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)` immediately
before every `setFloating(False)` call -- added to both
`_PanelDockWidget.closeEvent` and `_dock_panel`, the only two places
that ever redock a panel.

Verified: compiles clean. Headless test reproducing the exact scenario
-- float without Shift (`allowedAreas() == NoDockWidgetArea`, confirmed
first), `dock.move()` to a new position (simulating the drag), then
`dock.close()` (the X button's real code path) -- confirms `isFloating()`
False, `allowedAreas()` back to All, `parent() is win`, `isWindow()`
False (a real embedded dock, not a stray top-level widget), and its
geometry lands back exactly at its pre-float docked rect
(`QRect(0, 19, 386, 244)`, identical before and after). Full-window
screenshot after the same sequence
(`texture_design/screenshot_after_move_close_fix.png`) confirms no
stray overlap -- Browser sits cleanly in its normal spot. Full suite:
**304 passed, 19 failed** -- same standing baseline, no regressions.

**Not yet confirmed on real hardware** -- the exact z-order/stranding
visual Tom described is hard to fully reproduce headlessly (offscreen
platform has no real window manager); the geometry/parent/isWindow
checks above are the closest proxy available in this sandbox.

## 59. #58 wasn't the full fix -- Tom's report says it's still happening

Tom's report, in his own words: "once a window is popped out, if you hit
the x to close it, it only goes away if it hasn't been moved since
popping out. if it has been moved, it just gets pushed behind other
panels in the main window." Same symptom #58 already targeted -- #58's
fix is confirmed still present in the code (`_PanelDockWidget.closeEvent`
and `_dock_panel` both call `setAllowedAreas(AllDockWidgetAreas)` right
before `setFloating(False)`) -- but per Tom, still not actually fixed.

**Real gap in #58's own fix, found by re-reading it against what it
actually guarantees:** broadening allowedAreas stops Qt from stranding
the widget with NO valid target area at all (a total placement failure),
but it does nothing to tell Qt WHICH of the now-many allowed areas is
this specific panel's real home. This app's default layout is a nested
three-column arrangement built entirely from one-time
`addDockWidget`/`splitDockWidget` calls (`_build_default_dock_layout`) --
nothing anywhere remembered which column/neighbor a given panel actually
belonged in. Once Qt's own short-lived "prior slot" cache is invalidated
by a real move (#58's own docstring already established this happens),
`setFloating(False)` has to guess a target from scratch with no domain
knowledge of this app's layout -- a guess that's technically valid (so
#58's own checks -- `dockWidgetArea() != NoDockWidgetArea`,
`parent() is win`, `isWindow()` False -- would all still pass) but lands
in an area already claimed by a stretched sibling, which reads on screen
as exactly what Tom described: not obviously broken, just gone/behind
other panels.

**Fix: track and restore each panel's own dock area explicitly, rather
than trusting Qt's placement guess.** New `MainWindow._panel_dock_area`
dict, keyed by dock widget (not frame_attr -- `_PanelDockWidget.
closeEvent` only has `self`, not a frame_attr string). Populated by new
`_snapshot_panel_dock_areas()`, called once right after each of the two
places the default layout gets (re)built from scratch (`_build_layout`
at startup, `_reset_workspace_to_default`) -- necessary because initial
placement via addDockWidget/splitDockWidget never fires `topLevelChanged`
(that signal only fires on an actual floating-state transition, never on
first construction), so without this the very first-ever float of any
panel would have nothing recorded to restore to. Kept current after that
by `_on_dock_top_level_changed` itself, which now updates the dict every
time a panel finishes docking, regardless of cause.

Both redock sites (`_PanelDockWidget.closeEvent`, `MainWindow.
_dock_panel`) now do `setFloating(False)` first (confirmed elsewhere in
this file -- `_reset_workspace_to_default`'s own docstring -- that
`addDockWidget` does NOT reliably pull an still-floating widget in on its
own; it needs to already be docked somewhere first), THEN a corrective
`addDockWidget(area, dock)` using the panel's own recorded area -- Qt's
docs confirm this just MOVES an already-added widget to a new area
rather than adding a duplicate. Falls back to #58's old behavior
(whatever `setFloating(False)` alone produces) if a panel has no
recorded area for some reason -- shouldn't happen given the two snapshot
points above, but a missing dict entry should never crash a close
button. `_dock_panel`'s version still runs through the existing
`_reparent_gl_safely` GL-safety wrapper (RUNNING.md #29/#54) --
unchanged, just wrapping a slightly larger callable now.

New test: `test_close_floated_panel_after_moving_it_lands_back_in_its_
own_area` (`test_gl_reparent_safety.py`) -- float the Graph panel,
`dock.move()` it (the same "moved since popping out" proxy #58's own
verification used, since this sandbox has no real window manager to drag
with), close via the native X, then assert THREE things #58's own
existing tests didn't check: it lands back in the EXACT SAME
`dockWidgetArea()` it started in (not just *some* valid area), it's not
a stray top-level window, and its final width/height are both genuinely
nonzero (a panel docked into an already-full area with zero space ceded
to it would still pass every one of #58's own checks while still looking
exactly like "pushed behind other panels" on screen). Full suite: **305
passed** (was 304 -- +1 new), same 19 pre-existing `/tmp`-permission
sandbox failures as always, no regressions.

**Not yet confirmed on real hardware** -- same standing limitation as
#58 itself (no real window manager in this sandbox to actually drag a
panel with the mouse); `dock.move()` is the closest available proxy.
Please pop a panel out, actually drag it somewhere with the mouse (not
just close it immediately after popping out), then hit its X and confirm
it lands back in its real spot, fully visible, not tucked behind
anything.

## 60. #59 tested "not working" on real hardware -- found TWO real gaps in it

Tom, after actually trying #59: "Still not fixed." Then, unprompted, the
sharp guess that turned out to matter: "any chance this is related to the
code that retains window position between launches?" Two separate, real
gaps found -- his hunch was one of them, but not the only one.

**Gap 1 (the bigger one): #59's whole approach was built on a false
premise.** #59 tracked each panel's `dockWidgetArea()` and "corrected"
a redock with `addDockWidget(that_area, dock)`. Checked directly against
a real MainWindow (new test,
`test_dock_widget_area_is_too_coarse_to_trust_for_placement`): **every
single panel reports the same area, `LeftDockWidgetArea`**, including
the Graph panel sitting deep in the right column -- because this app's
whole layout is ONE nested split-tree, built via `splitDockWidget` off a
single `addDockWidget(Left, browser_frame)` root
(`_build_default_dock_layout`), and Qt's `dockWidgetArea()` only reports
which of the 4 cardinal areas a dock's entire tree is rooted in, not
where within that tree an individual dock actually sits. #59's
"corrective" `addDockWidget` call was therefore moving panels to a coarse
area almost totally unrelated to their real nested position -- likely
making Tom's symptom worse, or at best doing nothing, never fixing it.
This should have been checked empirically before shipping #59 in the
first place; it wasn't, and that's on this session, not something
real-hardware-only could have caught.

**Gap 2 (Tom's own hunch, also real, independent of Gap 1): the
remembered-workspace timing race.** `_build_layout`'s dock-area snapshot
ran synchronously during `__init__`, describing the DEFAULT layout --
but the remembered workspace's `restoreState()` call is deliberately
DEFERRED to the next event-loop tick (`load_workspace_state`'s own
DEFERRED docstring, for an unrelated startup-ordering reason predating
this bug). So on any launch where a real (non-default) remembered layout
loads -- which, for a "drag panels anywhere" app Tom's been actively
rearranging, is most launches -- any snapshot taken only at the earlier
point was stale before Tom could touch anything.

**The actual fix, replacing #59's mechanism entirely:**
`QMainWindow.saveState()`/`restoreState()` -- confirmed directly (not
assumed) to reproduce a dock's exact nested position, not just some
coarse valid area: floating a docked panel, moving it, then
`restoreState()`-ing a blob saved from before it floated reproduces
pixel-identical geometry. This app already relies on exactly this pair
for whole-session workspace persistence
(`get_workspace_state`/`load_workspace_state`), so it's a well-exercised
Qt path here, not a new risk. New `MainWindow._last_all_docked_state`, a
saveState() blob refreshed by `_snapshot_all_docked_state()` at both
places the layout is rebuilt from scratch (startup, Reset to Default)
AND after `_apply_dock_state`'s real `restoreState()` call (fixing Gap 2
directly), plus kept current via `_on_dock_top_level_changed` any time
the window returns to fully-docked (the LAST floating panel redocks).
Both redock sites (`_PanelDockWidget.closeEvent`, `_dock_panel`) now
restore this blob when the panel being closed is the ONLY one currently
floating (confirmed the common case, and Tom's exact repro) --
deliberately falls back to #58's plain `setFloating(False)` when more
than one panel is floating at once, since restoring a blob that predates
a DIFFERENT still-floating panel could pull that one back in too;
precision in that rarer multi-float case is accepted as unsolved for
now, flag if it turns out to matter.

New tests (`test_gl_reparent_safety.py`):
`test_dock_widget_area_is_too_coarse_to_trust_for_placement` (guards
against ever trusting `dockWidgetArea()` for placement again -- the
false premise behind Gap 1), the renamed/strengthened
`test_close_floated_panel_after_moving_it_lands_back_in_its_own_spot`
(now asserts exact pixel geometry, not just "some valid area" -- the
weaker check that would have let #59 pass despite being broken), and
`test_close_floated_panel_lands_in_remembered_position_not_default`
(Tom's own hunch, Gap 2, built via a real customized layout + monkeypatched
`load_remembered_workspace`, same pattern
`test_mainwindow_construction_does_not_float_synchronously` already
uses). All three needed `win.show()` + a couple of `processEvents()`
ticks before their baseline geometry reads to be reliable -- confirmed
directly that `restoreState()`-based redocking is sensitive to the same
never-shown-window flakiness RUNNING.md #56's footnote already flagged
for floating in general under this sandbox's offscreen platform; a real
launch is always shown long before a user could touch anything, so this
only affects test fidelity, not the fix itself. Full suite: **307
passed** (was 305 -- +2 net: one new coarse-area guard test, one new
remembered-workspace test, one existing test strengthened in place, no
count change from that one). Same 19 pre-existing `/tmp`-permission
sandbox failures as always, no regressions.

**Not yet confirmed on real hardware.** Please retry the same repro:
pop a panel out, actually drag it with the mouse, close it via the X,
confirm it lands back exactly where it was -- and separately, since
Gap 2 specifically depends on it, please also try this after quitting
and relaunching the app at least once (so a real remembered/non-default
layout is actually in play, not just the fresh-install default).

## 61. #60 tested "not working" too -- Tom asks for X to just hide instead

Tom, after trying #60 on real hardware: "This is great and actually
addresses a few things. BUT now hitting x sends a panel back home. if
home is lost it refuses to go anywhere. Keep it just as it is now but
hitting X hides that panel."

Real new failure mode, distinct from #59/#60's own bugs: if
`_last_all_docked_state` doesn't fully agree with the window's actual
current state (or more than one panel is floating, where #60 deliberately
declines to use it at all -- see #60's own entry), `restoreState()` can
silently no-op -- Qt's documented behavior when a saved blob doesn't
cleanly apply is to leave everything exactly as it was. Since
`event.ignore()` had already told Qt not to actually process the close,
the net effect was: X does nothing, the panel is still floating exactly
where it was, unresponsive -- "if home is lost it refuses to go
anywhere," an accurate description of a silent no-op the code never
checked for.

Rather than keep patching an ever-growing set of "redock correctly" edge
cases (#58 -> #59 -> #60, each fixing one real gap and exposing another),
Tom's ask sidesteps the whole class of bug: **closing a floating panel
via its native X no longer tries to figure out where it goes.** It just
hides (`self.setVisible(False)`), staying floating internally
(`isFloating()` untouched) -- there's no "home" placement decision left
to make, so there's nothing left to get wrong. Re-showing the panel
later (Workspace menu, its shortcut) brings it back as a floating window
in its last position, same relationship a DOCKED panel's close already
has to its own dockedness (closing a docked panel just hides it too,
untouched by any of #58-#61 -- see `_PanelDockWidget.closeEvent`'s
docstring). `_floating_panels`/the Float-menu-checkbox bookkeeping is
unaffected (no `topLevelChanged` fires from a plain hide, and shouldn't
-- the panel really is still floating).

**Scope, per Tom's own "keep it just as it is now" -- only the native X
button changed.** `MainWindow._dock_panel` (the Workspace menu's
Float-uncheck path, and `_dock_all_floating_panels`' session-load/
app-quit/reset-to-default safety net) is UNTOUCHED, still using #60's
saveState()/restoreState() redock logic -- those are deliberate,
explicit "put this panel back" actions, not the specific X-button
complaint Tom raised, and #60's mechanism is still correct for them (its
one weakness, the silent-no-op case, simply isn't the thing X does
anymore).

Updated three existing tests that asserted the now-superseded
"X redocks" behavior to assert hiding instead
(`test_close_floated_3d_viewer_via_native_close_button_hides_not_redocks`,
renamed from `..._redocks`; `test_close_floated_3d_viewer_with_no_lut_
loaded_does_not_raise`'s assertion switched from "not in
_floating_panels" to isVisible()/`_floating_panels` membership), and
retargeted the two tests that specifically exercise #60's redock
precision (moved-then-closed geometry, remembered-position geometry) onto
`MainWindow._dock_panel` directly -- the Float-menu-uncheck path -- since
the native X button no longer runs that code at all (renamed to
`test_uncheck_float_menu_after_moving_panel_lands_back_in_its_own_spot`/
`test_uncheck_float_menu_lands_in_remembered_position_not_default`).
Full suite: **307 passed** (same total -- three tests changed behavior/
name in place, none added or removed net), same 19 pre-existing
`/tmp`-permission sandbox failures as always, no regressions.

**Not yet confirmed on real hardware.** Please pop a panel out, actually
drag it somewhere, hit X, and confirm it simply disappears (not
redocks, not stuck) -- then check the Workspace menu still shows it as
floating/checked, and that re-enabling it from there brings it back as a
floating window (not docked) in roughly its last spot.

## 62. Fixed dark palette (no longer OS-theme-dependent) + blue panel-header accents

Tom, after seeing real light-mode screenshots: "i want to move away from
a system theme dependent style. the screenshots youve shown me in light
mode are terrible. Lets keep the multiple grays etc etc and add blue
accents for panel headers."

style.py's whole stylesheet has always routed through `palette(...)` QSS
functions rather than hardcoded hex, deliberately, so it would respect
whatever light/dark theme the OS was set to (see this file's SECOND PASS
comment from 2026-07-24). That was the right call for "don't fight dark
mode" -- it was the wrong call for "look good regardless of OS setting,"
which is what Tom's actually asking for now: a light OS theme made every
`palette(...)` reference resolve light too, and the result looked bad.

**Fix, deliberately NOT a rewrite of the stylesheet:** every rule still
goes through the exact same `palette(...)` role names as before (`mid`
for borders, `highlight` for accent/selection, etc.) -- the ARCHITECTURE
was already right, it just needed a fixed source of truth instead of the
OS. New `style.build_dark_palette()` constructs a real `QPalette` with
fixed hex colors (`_DARK_WINDOW`/`_DARK_BASE`/`_ACCENT_BLUE`/etc., the new
easy knobs), and `main_window.py`'s `main()` calls
`app.setPalette(build_dark_palette())` right after `app.setStyle(
"Fusion")` and before `app.setStyleSheet(STYLESHEET)` -- since `palette(
...)` resolves against whatever QPalette QApplication holds AT THAT
POINT, setting a fixed one first means every reference (and every native/
unstyled widget this app doesn't explicitly QSS-style -- QMessageBox,
QFileDialog, native menus) draws from OUR colors from then on, regardless
of the OS's actual setting. Two tiers of gray (`_DARK_WINDOW` for the
space between panels, `_DARK_BASE` -- one shade lighter -- for panel/card
bodies) keeps the existing "cards floating on a background" language
(SEVENTH PASS's border+radius work) intact now that it's a fixed color
instead of an OS-provided one.

**Blue accents for panel headers, per Tom's specific ask:** `#panelTitle`
(the label showing "Browser"/"Viewer"/"Stack"/etc. on every dock's title
bar) now uses `palette(highlight)` instead of `palette(text)`, and
`#panelTitleRow`'s border-bottom is `palette(highlight)` at 2px instead of
the plain 1px `palette(mid)` used on its other three sides -- every
panel's name now reads in blue with a blue accent line underneath,
consistent across all ten panels (confirmed visually, see below), not a
one-off hardcoded color on a single rule.

`palette(link)` (Stack Forking's Branch B accent, previously whatever the
OS's own link color happened to be) is now `_ACCENT_AMBER` in
`build_dark_palette` -- deliberately NOT blue, since blue is now the
header/selection/focus accent everywhere else; Branch A (`palette(
highlight)`) and Branch B need to stay visually distinct from each other.

**Verified visually, not just logically.** Grabbed a full composited
screenshot of a real `MainWindow` (`win.grab()`, same "grab the whole
window, not an isolated widget" methodology RUNNING.md #55 already
established as the only reliable way to catch real compositing bugs) --
confirms fixed dark grays throughout, every panel header showing its name
in blue with a blue underline, blue slider handles/fill (Grid density,
Cage opacity), all independent of this sandbox's own (irrelevant here)
palette, since `build_dark_palette()` overrides it before any of this
renders. Full suite: **307 passed**, same 19 pre-existing
`/tmp`-permission sandbox failures as always, no regressions (this was a
palette/QSS-only change, no backend touched).

**Not yet confirmed on real hardware, specifically the light-OS-theme
case this was FOR** -- please launch with the OS set to light mode (the
exact condition that looked bad before this pass) and confirm the app
now looks identical to dark-OS-mode: fixed dark grays, blue panel-header
text/underline, blue slider fills, no washed-out/low-contrast chrome
anywhere.

## 63. #62's exact color values corrected + a real hint-text role added

Tom's direct follow-up after seeing #62: "Right idea, valuea are a
little off from what id want. the panel's gray should be darker.
(#3D3D3D) and the accent should be darker and more muted (#2C4066) Make
hint text (#617191)."

Three concrete color changes in `style.py`'s `build_dark_palette()`:
`_DARK_BASE` (panel/card bodies, list/input backgrounds) is now
`#3D3D3D`; `_ACCENT_BLUE` (selection/focus/panel-header accent) is now
the darker, more muted `#2C4066`, replacing #62's brighter blue; and a
genuinely NEW color, `_HINT_TEXT` (`#617191`), for muted informational
text specifically.

`_HINT_TEXT` isn't just a value swap -- it's a real role split. Until
now, `_DARK_MID` did double duty as both "structural border/divider
color" AND "muted text color" (the same `palette(mid)` token covered
both). Tom's ask for a distinct hint-text color meant separating those:
`_DARK_MID` keeps the border/divider job, plus plain iconographic glyph
colors (the Stack stage row's ✓/✗ enabled-toggle and × delete button,
muted-until-hover -- these read as icons, not prose, so they stayed on
the neutral gray); `_HINT_TEXT` (mapped onto `QPalette.PlaceholderText`,
referenced in QSS as `palette(placeholder-text)`) now covers every place
this file or the panel modules color actual PROSE dim/secondary/disabled
text -- hint labels ("Select a stage above...", "Import a sample
image...", Stack Forking's explanatory paragraphs, opacity/domain
readouts, the Branch A/Branch B column headers), disabled QPushButton
and QMenu-item text, and a disabled Stack stage's name label (both the
plain and disabled+selected variants). The Disabled QPalette group's
text roles (WindowText/Text/ButtonText, for any native widget this app
doesn't explicitly `:disabled`-style) moved from `_DARK_MID` to
`_HINT_TEXT` too, same reasoning. Updated every matching
`color: palette(mid)` site across `style.py`, `scopes_panel.py`,
`lut_controls_panel.py`, `aces_drt_panel.py`, and `stack_panel.py` (5
labels) to `palette(placeholder-text)` -- left the legacy/orphaned
files (`concatenate_panel.py`, `collapsible_section.py`, and
`aces_drt_panel.py`'s OWN abandoned Controls-era sibling -- none
imported by `main_window.py` anymore, see KNOWN_GOOD_SUMMARY's "Legacy /
orphaned" list) untouched since they're not part of the running app.

`_DARK_LIGHT`/`_DARK_MIDLIGHT` (hover states) also got a small bump --
not something Tom asked for directly, but a necessary side effect of
`_DARK_BASE` getting lighter: both were originally tuned to sit just
above #62's darker base; against the new, lighter `#3D3D3D`, the old
`_DARK_MIDLIGHT` (`#3a3a3f`) had gone slightly DARKER than the base it's
meant to hover-lift away from, which would have made list-row hover read
as a dimming instead of a highlight. Nothing else changed --
`_DARK_WINDOW`, `_DARK_ALT_BASE`, `_DARK_BUTTON`, `_DARK_MID`,
`_DARK_DARK` are all identical to #62.

Verified visually again, same `win.grab()` full-composite methodology as
#62: panel bodies read as the new, slightly lighter `#3D3D3D` gray;
panel-header text and the header underline are now a darker, more muted
navy-blue rather than the brighter blue #62 shipped; every hint/disabled
text label ("Select a stage above...", "Import a sample image...") now
reads in the new muted blue-gray, visibly distinct from the plain
structural grays around it. Full suite: **307 passed**, same 19
pre-existing `/tmp`-permission sandbox failures as always, no
regressions (palette/QSS-only change again, no backend touched).

**Not yet confirmed on real hardware.** Please confirm the exact shades
read correctly on a real display (color values can look different
between this sandbox's headless render and Tom's actual monitor/color
management) -- specifically that the panel gray isn't too light/washed
out now, the accent blue reads as "darker and muted" rather than flat or
muddy, and the hint-text blue-gray is legible against the panel
background at actual size (not just in a full-window screenshot).

## 64. Viewer panel's empty space -- explicit background fill (wipe_compare_widget.py)

Tom's direct follow-up after #63: "Not exactly what i was going for. The
space in the body of the panel (ex. the space around the image viewer's
actual image) seems a much lighter gray than #3D3D3D."

Checked empirically first rather than guessing at a new hex value --
grabbed a full composited `win.grab()` and sampled actual pixel colors
(via PIL) at the letterboxed gutter around the Viewer panel's image, the
full-panel empty space above/below it, and every other panel's body for
comparison. Every single sampled pixel, including the gutter around the
image, was **already** exactly `#3D3D3D` -- numerically identical to
every other panel body. So #63's color values were never wrong; the bug
was something else.

Root cause: `WipeCompareWidget.paintEvent` (wipe_compare_widget.py) never
explicitly painted its own background. It only ever fills the specific
`image_rect` it draws the pixmap (or PLACEHOLDER_BG) into -- any
letterboxed margin around that rect (when the image's aspect ratio
doesn't match the widget's own), or the entire canvas in the true
empty-state (no image loaded, no stack, early-return branch), was left
completely unpainted. It happened to composite correctly in this
sandbox's offscreen platform, which let panelFrame's own `palette(base)`
fill show through cleanly from underneath -- but that's relying on
transparency and "whatever's already painted behind this widget," not
this widget owning its own background, which is exactly the same class
of implicit, only-verified-headless assumption that already caused two
real (not cosmetic) compositing bugs elsewhere in this app's history
(#44's border-image tiling, #46's title-bar texture bleed-through).
Rather than treat "it already matches headless" as proof of nothing to
fix, hardened it: `paintEvent` now unconditionally fills the widget's
ENTIRE rect with `self.palette().color(QPalette.ColorRole.Base)` as the
very first paint operation, before the empty-state text, the placeholder
rect, or either pixmap. Same fixed-palette source of truth as
style.py's `palette(base)` QSS token everywhere else -- not a new
hardcoded hex.

Verified with a deliberate stress case a same-aspect screenshot wouldn't
catch: resized the window so the Viewer panel is much taller than the
sample image's aspect ratio requires (411x559 vs. the image's native
~4:3), forcing a large letterboxed gutter above and below the image.
Sampled that gutter at multiple points (well above/below the image, and
right at its top/bottom edge) plus another panel's body for comparison --
all read `#3D3D3D`, no visible seam. Full suite: **307 passed**, same 19
pre-existing `/tmp`-permission sandbox failures as always, no
regressions (paint-only change, no logic touched).

**Not yet confirmed on real hardware.** This fix addresses a real
fragility (an unpainted region relying on implicit compositing) whether
or not that fragility was the actual cause of what Tom saw -- please
confirm on your actual display that the space around the image now
reads as the same gray as the rest of the panel, with no visible seam or
lighter patch, at the actual window size/aspect you were looking at.

## 65. Panel gray darkened to #2B2B2B (style.py)

Tom's follow-up after #64: "Maybe its just my eyes. try this gray
#2B2B2B." `_DARK_BASE` in `build_dark_palette()` is now `#2B2B2B`,
replacing #63's `#3D3D3D` -- every panel body, list/input background,
and the Viewer's letterboxed gutter (all routed through `palette(base)`,
including #64's explicit fill) all move together automatically, same
single-source-of-truth architecture as every color pass before this one.

`_DARK_WINDOW` (`#242427`, the space BETWEEN panels) was left unchanged
-- `#2B2B2B` is still lighter than it, so the "cards floating on a
background" two-tier-gray language this file's own comment describes
still holds, just with a narrower gap between the tiers than #63 had.
`_DARK_LIGHT`/`_DARK_MIDLIGHT` (hover states) also left unchanged --
unlike #63 (where base got lighter and hover had to be bumped to stay
above it), base got DARKER here, so the existing hover values sit even
more clearly above it than before; no adjustment needed for the
hover-lift direction to keep reading correctly.

Verified: headless `win.grab()`, sampled Browser/Parameters panel bodies
and the Viewer's letterboxed gutter (the same region #64 just hardened)
-- all read exactly `#2B2B2B`, uniform, no seam. Full suite: **307
passed**, same 19 pre-existing `/tmp`-permission sandbox failures as
always, no regressions (palette-only change).

**Not yet confirmed on real hardware** -- please confirm this darker
value reads right on your actual display.

## 66. Panel gray to #242424 + header text = hint color (style.py THIRTEENTH PASS) + a real bug this surfaced (FOURTEENTH PASS)

Tom's follow-up after #65: "This is okay but can you lets go even darker
#242424. Also lets make the Panel header text the same color as the hint
text, but leave the accent on the bar."

`_DARK_BASE` is now `#242424`. Flagged (not blocked on) one thing: this
is only 3 blue-channel values off `_DARK_WINDOW` (`#242427`, the space
BETWEEN panels) -- panels are now only barely lighter than the gap
between them, so the border+radius "cards floating on a background"
language is carrying almost all of that visual separation on its own.
Applied exactly as asked; noting it in case it reads as too subtle on a
real display.

`QLabel#panelTitle` now uses the hint-text color instead of the blue
accent -- `QWidget#panelTitleRow`'s border-bottom stays `palette(
highlight)` at 2px, untouched, so the accent is still there as an
underline, just not on the label text itself anymore.

**Real bug found while verifying the above, not just applying it:**
pixel-sampled the rendered title text (habit this project's history
keeps insisting on, per #44/#46/#55/#64) and it came back `#e6e6e9`
(plain text color), not `#617191` (hint color) -- despite the QSS rule
correctly saying `palette(placeholder-text)`. Checking further, EVERY
existing `palette(placeholder-text)` usage in this app -- the hint
labels #63 added, disabled button/menu text, every panel module's own
hint labels -- renders the same wrong way. #63's own verification
claiming these "read in the new muted blue-gray" was a real miss (an
eyeballed screenshot, not a pixel-sampled one).

Root cause, confirmed with an isolated repro: `QApplication.palette()`
correctly holds `_HINT_TEXT` for `PlaceholderText` the whole time, but
`MainWindow.palette()` silently drops that role back to the Text color
specifically once `.show()` triggers Qt's style-polish pass -- every
other role checked the same way (`Base`, `Highlight`, `Text`, `Mid`,
`Window`, `Light`, `Midlight`) survives polish correctly; only
`PlaceholderText` doesn't. Reproduced identically under both the
`offscreen` and `minimal` Qt platform plugins, ruling out a sandbox-only
artifact -- this looks like a genuine PySide6/Qt (6.8.0.2) behavior where
`QPalette::PlaceholderText`'s real, narrower purpose (a QLineEdit/
QTextEdit's own native placeholder rendering) doesn't reliably propagate
through style-polish when reused via QSS for arbitrary widgets.

**Fix:** stopped routing hint-text color through `palette(placeholder-
text)` entirely. `style.HINT_TEXT_HEX` (public alias for `_HINT_TEXT`) is
now the single source of truth -- every `color: palette(placeholder-
text);` rule in `style.py`'s STYLESHEET is a literal `color: {_HINT_TEXT}`
f-string interpolation instead, and every panel module's own inline
`setStyleSheet("color: palette(placeholder-text)...")` call (
`scopes_panel.py`, `aces_drt_panel.py`, `lut_controls_panel.py`,
`stack_panel.py` -- 6 call sites) now imports `HINT_TEXT_HEX` from
`style` and does the same. `QPalette.ColorRole.PlaceholderText` itself is
left mapped to `_HINT_TEXT` in `build_dark_palette()`, unchanged -- still
correct for genuine native placeholder text (e.g. the Browser panel's
search box), a real, separately-working mechanism from the broken QSS
indirection this pass removes.

Verified: pixel-sampled (not just re-read the QSS) the panel title text
and a hint label after the fix -- both dominated by `#617191`, confirmed
via `win.grab()` + a per-pixel color histogram, not a visual glance.
Panel body/gutter confirmed `#242424` the same way. Full suite: **307
passed**, same 19 pre-existing sandbox failures, no regressions
(paint-only changes).

**Not yet confirmed on real hardware** -- please confirm both the darker
panel gray and the hint-colored header text (with the blue accent still
on the underline only) read right on your actual display.

## 67. Four more color changes: #141414 panel gray, #BDBDBD header/hint text, #0D0D0D separator bars

Tom's follow-up after #66, four changes in one message:

1. Panel gray darker still, `#141414`.
2. Panel header (title) text `#BDBDBD`.
3. The bars between panels (the draggable dock splitters) `#0D0D0D`.
4. Hint text also `#BDBDBD`.

`_DARK_BASE` is now `#141414`. Flagging, not blocking: this INVERTS the
"two tiers of gray" relationship this file's own comment has described
since NINTH PASS -- panels (`20,20,20`) are now literally darker than
the space between them (`_DARK_WINDOW`, `36,36,39`), not lighter. The
border+radius language still gives each panel a visible edge, it now
just reads as a recessed/inset panel rather than a raised card. Applied
exactly as asked.

`_HINT_TEXT` is now `#BDBDBD` (was `#617191`, a plain light gray instead
of a blue-tinted one) -- this single constant covers BOTH #2 and #4 at
once, since `QLabel#panelTitle` (the header text) has routed through
`_HINT_TEXT` since #66's THIRTEENTH/FOURTEENTH PASS work; no separate
rule needed touching for the header-text ask.

New dedicated constant `_SEPARATOR_DIVIDER = "#0D0D0D"` for
`QMainWindow::separator` (the actual bars between panels) -- deliberately
NOT a repoint of `_DARK_MID` (which also colors ordinary structural
borders everywhere else -- panelFrame's outline, QLineEdit/QListWidget/
QMenu borders, etc. -- none of which were asked to change), and
deliberately a literal hex rather than a new QPalette role, same
reasoning as #66's `_HINT_TEXT` fix: no reason to risk a color hitting
the same style-polish propagation gap `PlaceholderText` proved capable
of, when nothing else needs this value through QPalette anyway.

Verified the same pixel-sampled way #66 caught its bug with, not just a
QSS re-read: `win.grab()` + per-pixel scan confirms panel body/gutter
`#141414`, title text and hint labels showing genuine `#BDBDBD` pixels
(not just a nearby blend), and the separator bar between panels
dominated by `#0D0D0D`. Full suite: **307 passed**, same 19 pre-existing
sandbox failures, no regressions (paint-only).

**Not yet confirmed on real hardware** -- please confirm the darker
panel gray (now darker than the space between panels -- flagged above)
and all three text/bar colors read right on your actual display.

## 68. Dead-code cleanup + live preview no longer PNG round-trips (acting on CLEANUP_AND_PERF_OBSERVATIONS_2026-07-25.md)

Tom, after reviewing that observations doc: "Please execute 1-3 from
your list on the bottom and clean up the dead code inside panel_frame."
Four changes:

**1. Deleted 8 confirmed-dead files.** Four fully unreachable-from-
`main_window.py` modules (`aces_drt_panel.py`, `concatenate_panel.py`,
`lut_controls_panel.py`, `collapsible_section.py` -- 990 lines,
re-verified via the actual import graph, not assumed) plus the four
stale `.bak` leftovers (`main_window.py.bak`, `sample_image_panel.py.bak`,
`scopes_panel.py.bak`, `stack_panel.py.bak` -- 7,252 lines) that earlier
checkpoints had flagged as "harmless, sandbox couldn't delete them."
They're gone now -- ownership wasn't actually the blocker this session,
just needed the delete-permission prompt.

**2. Fixed `setup.py`'s py2app `includes` list.** Regenerated by
walking `main_window.py`'s real import graph (ast-parsed) instead of
hand-maintained: removed the now-deleted `collapsible_section` entry,
added the 13 live local modules that were silently missing (`cdl`,
`cdl_file_io`, `cdl_library`, `cdl_library_panel`, `color_balance`,
`color_wheel_widget`, `colorspace_convert`, `floating_dialog`,
`lut_library`, `lut_library_panel`, `stack_memory_file_io`,
`undo_history`, `workspace_io`). **Not verified against a real py2app
build** -- this sandbox is Linux and py2app can't cross-compile a macOS
`.app` (see `setup.py`'s own docstring) -- please run one real
`python3 setup.py py2app` to confirm before shipping. py2app's own
modulegraph scan usually catches plain `import` statements without
needing `includes` at all, which is likely why a build never visibly
broke over the old, wrong list -- but `includes` exists as the failsafe
for whatever that scan misses, so it should reflect the real dependency
set regardless.

**3. `_array_to_pixmap` (`sample_image_panel.py`) no longer round-trips
through PNG.** This ran on every single live preview repaint -- any
stack edit, including live opacity/CDL/printer-lights slider drags (see
`refresh_preview`'s own docstring: fires on every stack mutation) --
and did a real PIL PNG encode (zlib compress) immediately followed by a
Qt PNG decode (`QPixmap.loadFromData`), purely to move pixels already
sitting in memory as a numpy array into a `QPixmap`. Replaced with a
direct buffer hand-off: `uint8` array -> `QImage` (`Format_RGB888`,
built straight from the array's own buffer) -> `QPixmap.fromImage()`,
no compression step at all. `array_to_png_bytes` (`app_logic.py`) is
completely untouched -- still exactly right for Export Still's actual
file-writing job; only this panel's in-memory preview path changed, and
the now-unused `array_to_png_bytes` import was dropped from
`sample_image_panel.py` accordingly.

Verified pixel-EXACT equivalence before trusting this, not just "it
still looks like an image": built a random 37x53 test array, ran it
through both the OLD path (PNG encode+decode) and the NEW path (direct
`QImage`), compared every pixel -- 0 of 1,961 pixels differed, max
channel delta 0. Same output, no compression round-trip.

**4. `panel_frame.py`'s dead `if title:` branch removed.** `PanelFrame`
has had exactly one caller since the docking migration
(`main_window.py`'s `_make_dock`), always with no title and no
corner_widget -- confirmed by checking every call site, not just
trusting the file's own docstring caveat. Removed the title-row-building
branch entirely (`title_row_widget`/`title_label`/corner_widget
placement, `QHBoxLayout`/`QLabel`/`QWidget`/`Qt` imports that only
existed for it) rather than leaving it as an unexercised "may well get
used again" capability -- reviving it later would need re-deriving from
`_build_dock_title_bar`'s current behavior anyway to actually match,
so keeping a stale copy wasn't saving future work. `PanelFrame.__init__`
signature simplified from `(title, content_widget, corner_widget=None,
parent=None)` to `(content_widget, parent=None)`; the one call site in
`main_window.py` updated to match. Several other comments across
`main_window.py`/`style.py` that referenced the old `title=None`
signature were also updated so they don't describe a parameter that no
longer exists.

Verified: `py_compile` clean on every touched file
(`panel_frame.py`/`main_window.py`/`style.py`/`setup.py`/
`sample_image_panel.py`), full suite **307 passed**, same 19
pre-existing sandbox failures, no regressions. Fresh `win.grab()`
screenshot confirms the app still constructs and renders identically --
same layout, same colors, live wipe-compare preview still shows the
actual image correctly through the new QImage path.

**Not yet confirmed on real hardware**, same as every visual/paint
change this arc -- and #2 specifically still needs a real macOS py2app
build, which nothing in this sandbox can do.
