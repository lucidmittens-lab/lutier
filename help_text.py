"""
help_text.py -- the in-app Help > Manual text, as a plain Python string
constant rather than something read from USER_MANUAL.md at runtime.

That's a deliberate choice, not laziness: app_logic.default_sample_image_path
already taught this codebase the hard way that a bundled data FILE's path
resolution breaks under py2app -- this module (like every other local
`includes` entry) gets its .pyc zipped into Contents/Resources/lib/
python3XX.zip when frozen, so __file__-relative lookups land inside the
zip archive, not next to it. A plain string constant sidesteps that whole
class of bug entirely: once this module is imported, the text is just an
in-memory Python object, whether running from source or from the frozen
.app, no path resolution involved at all.

TRADE-OFF: this is a second copy of USER_MANUAL.md's content, not a live
read of that file -- editing USER_MANUAL.md alone will NOT update what
Help > Manual shows in the app. Keep USER_MANUAL_MARKDOWN below in sync by
hand whenever USER_MANUAL.md changes. (An alternative would be a build-time
step that generates this file FROM USER_MANUAL.md rather than hand-copying
-- not done here since the manual doesn't change often enough to justify
the extra moving part, but worth reconsidering if that stops being true.)

Rendered via QTextBrowser.setMarkdown() (see main_window.py's Help menu),
which is why this is kept as real Markdown (headers, bold, a table) rather
than plain text.

2026-08-12: resynced after USER_MANUAL.md's Stack Forking section picked
up the enable-toggle glyph change (checkmark/cross -> filled/hollow dot)
and the opacity control's relocation (below the list -> built into each
row), both part of the Stack panel's "snappier and more interactive"
revamp.
"""

USER_MANUAL_MARKDOWN = r"""# LUTier — User Manual

LUTier is a LUT/color-grading tool: build up a grade as an ordered stack
of operations, preview it live, and export it in the format you need.
This is a feature outline for testers, organized by panel — for each
panel's operators/functions, a **Source** line names the file(s) that
actually implement the color math, so a question like "what does the
Grading Tone slider actually run?" has a direct answer.

Every panel can be **dragged by its title bar** to rearrange the whole
workspace freely, **shown/hidden** from the Workspace menu (or its own
Shift+1 through Shift+9 shortcut), and **floated** into its own window
(Cmd+Shift+1 through Cmd+Shift+9, matching digit). There is no fixed
layout to fight against — see **Workspace & Window Management** near the
end for the full set of layout/window features shared across every panel.

---

## Browser

A file-system browser: a folder tree on top (**Choose Root...** picks the
top folder, **↑** goes up a level) and, below it, the recognized files in
the selected folder — media (stills and video), LUTs (`.cube`, `.clf`,
`.spi1d`, `.spi3d`, `.spimtx`, `.csp`) and ASC CDLs (`.cc`, `.cdl`,
`.ccc`) — with Name, Type, Size and Modified columns. Tick
**Subfolders** (next to the search box) to list everything under the selected folder (a Folder
column then shows where each file lives; hidden folders are skipped and
very large trees stop at 5,000 files). Narrow the list with the type menu
(All / Media / LUTs & CDLs) or the search box (wildcards ok).

Select several files and drag them onto the Media Libraries table (or
onto a library in its side list) to import them. Double-click a media
file to import it into the current library, a LUT to add it to the
Stack, a CDL to import it into the Gallery. A single LUT or CDL can
still be dragged onto the Stack or its Library/Gallery panel.

**Source:** `browser_panel.py`; LUT file reading is
`app_logic.load_lut_any_format`.

## Media Libraries

A session holds any number of named **libraries**, listed in the side
panel on the left (right-click it for New / Rename / Delete; each shows
how many items it holds). The selected library is what the table shows
and where imports, drops and New Timeline land; drop files onto another
library in the list to import straight into it.

The table lists the library's still and video files, catalogued by
reference, and its **timelines**. Right-click the column header to
choose columns (timecode, codec, bit depth, audio, camera, file size,
...), drag headers to reorder, click one to sort. Double-click media to
load it as the Viewer's Source (right-click for Load as Reference / Add
to Timeline / Rename / Delete; video only supports Source). Entries are
catalogued by path and header metadata only — moving or deleting the
source file after import breaks that entry.

Only the essential columns show by default (Name, Kind, Resolution,
Duration, FPS, Codec); Path, timecode and the rest are a header
right-click away, and Alt+hover shows them for any row.

Double-click a timeline (or right-click → Load Timeline) to open it in
the Timeline panel; the loaded one is marked ▶. Unload it from the same
menu or the ⏏ button next to its name under the Timeline track — it stays in its library. **☰ →
New Timeline** makes an empty one at the project's edit rate; adding a
clip with no timeline loaded makes one automatically.

**Source:** `media_panel.py`; `media_library.py`'s `MediaPool` /
`MediaLibrary` / `MediaEntry`; header metadata from `media_probe.py`.

## LUT Library

LUTs you've ingested into the session. Once imported, a LUT's full data
lives in the session — moving, renaming, or deleting the original file
afterward won't break anything.

**Source:** `lut_library_panel.py`; entries are `lut_library.py`'s
`LUTLibraryEntry`/`LUTLibrary`.

## Gallery

Named snapshots of the whole Stack, so you can save a look, keep working,
and get back to it later ("Recall") — each capture includes a thumbnail
and a full canvas-resolution still, both loadable as Reference. Can also
be exported to/imported from their own `.ltm` file, independent of the
session. The same panel also holds externally-imported reference stills,
kept separate from the Media Libraries, AND saved ASC CDL grades (SOP/SAT
values, either saved from a CDL stage in the Stack or imported from a
file — "Apply to Stack" brings one back onto a CDL stage). One mono
"Import..." action picks the right kind of entry (still image, `.ltm`,
or `.cc`/`.cdl`/`.ccc`) from the chosen file's own extension. Right-click
any row for Recall/Update Still/Apply CDL to
Stack/Insert in Stack/Load as Reference/Rename.../Delete/Export... —
whichever of those actually apply to that row's own kind (Insert in
Stack, unlike Recall/Apply, always ADDS new stage(s) rather than
replacing/overwriting).

**Source:** `stack_memories_panel.py`; snapshots are serialized the same
way a session's Stack is (see `session_io.py`); a CDL entry's underlying
SOP/SAT values are the same `cdl.py` structures a CDL stage in the Stack
uses (see **ASC CDL** under **Stack → Operators** below).

## Viewer

The source image preview, before/after wipe compare. Two roles:
**Source**, which shows the Stack's live result, and **Reference**, a
second image the Stack never touches — useful for comparing your grade
against a separate plate, still, or approved frame. Load each from the
Media Libraries ("Load as Source"/"Load as Reference"). The Viewer's "☰"
menu's "Left Image"/"Right Image" submenus each pick independently from
three sources — Source Before, Source After, or Reference — and "Enable
Wipe" switches between a plain single-image view (showing whatever
"Right Image" is set to, the default at launch) and the split-screen
compare. The Viewer previews images exactly as decoded, with no implicit
color-space reinterpretation step. Scroll to zoom in/out (anchored on
the cursor), middle-mouse-drag to pan around, middle-double-click to
reset back to fit — independent of the left-click wipe divider drag, so
both work at once without conflicting.

**Source:** `sample_image_panel.py` (image loading/wipe-source logic)
driving `wipe_compare_widget.py` (the actual compare/zoom/pan widget).
The Stack's live result shown as "Source After" is graded on the GPU when
the Stack is GPU-eligible (`gpu_stack_renderer.py`), falling back to the
same CPU path (`stack.apply_stack_to_image_array`) otherwise — visually
identical either way, just a performance difference.

## Stack

The main work area: an ordered list of stages. Reorder by dragging,
toggle any stage on/off, give it its own opacity, click a stage to edit
its parameters in the **Parameters** panel.

**Source:** `stack_panel.py` (the list UI) driving `stack.py` (the
`Stage`/`Stack` model and `_apply_stage_to_array` dispatch every operator
below ultimately runs through).

### Adding a stage

From the Stack's **"☰"** menu or **Stack** menu bar entry, **Add [Operator
Name]** adds a fresh stage with identity/no-op parameters, ready to edit
in Parameters. A LUT stage can also be started by double-clicking or
dragging a file in from the Browser.

### Operators

Every operator below is a `Stage` in the Stack, dispatched by
`stack.py`'s `_apply_stage_to_array`. Every one is a genuine no-op at its
identity/default parameters, and every field can be click-drag-scrubbed
(see **Parameters** below) — hold **Shift** while dragging for finer
control.

- **LUT** — applies a loaded LUT's own table directly (3D or 1D,
  whichever the file declares). Not itself a live-editable operator —
  the table came from a file, not sliders.
  **Source:** `colour-science`'s own `colour.LUT3D`/`colour.LUT1D`
  (`.apply()`), loaded via `app_logic.load_lut_any_format`. GPU-
  accelerated for a plain, default-domain `colour.LUT3D` (`gpu_lut_shader.py`,
  routed through OCIO's own `Lut3DTransform` GPU codegen); a LUT1D, an
  explicit-domain LUT, or a retarget-wrapped one (below) stays on CPU.

- **LUT Retarget** — not its own stage, but a checkbox on any LUT stage's
  own inspector ("Retarget (reinterpret this LUT's declared input/output
  color space)"). Use it when a LUT was authored expecting one space but
  you want to feed it / read it in another — e.g. an LMT built for
  ACEScct fed ACES2065-1 values directly instead. Pick From (the space
  the LUT actually expects) and To (the space you want to use instead);
  LUTier wraps the LUT with a pre-conversion and post-conversion rather
  than resampling the LUT's own table, so the original data never
  changes. **Source:** `retarget.py`'s `retarget_io` (wraps the LUT
  object); baking a retargeted LUT to a flat table is
  `app_logic.bake_retargeted_lut`. Not GPU-accelerated — a retarget-
  wrapped LUT stage always falls the whole Stack back to CPU.

- **Printer Lights** — a per-channel R/G/B trim in printer-point units
  (~12 points per stop, the standard film-lab convention), not raw gain
  numbers. Set the stage's **Working Color Space** to whatever encoding
  its input actually is (log, camera log, gamma, or already-linear) —
  this matters because "a stop" is only a well-defined doubling in
  scene-linear light, so LUTier decodes to linear in that declared space,
  applies the gain there, then re-encodes back. Getting the working space
  wrong doesn't break anything visibly obvious, it just means the points
  no longer correspond to real stops. "Gang R/G/B" links all three
  sliders together for an even trim; "Reset to 0" clears the stage back
  to a no-op. **Source:** `printer_lights.py` (`apply_printer_lights_in_working_space`
  — decode-to-linear via `aces_drt.py`'s OCIO registry, gain, re-encode).
  GPU-accelerated via `gpu_printer_lights_shader.py` (composes the same
  decode/encode as two OCIO-generated shaders around a hand-written gain
  uniform) whenever OCIO is installed and the working space has an
  unambiguous linear sibling; otherwise CPU-only.

- **ASC CDL (SOP/SAT)** — the standard primary-grade formula every
  grading tool exchanges as `.cc`/`.cdl`/`.ccc` files: Slope/Offset/Power
  per channel, plus an overall Saturation. Each of Slope/Offset/Power
  gets a color-balance wheel plus master and fine R/G/B spinboxes; Sat is
  a single slider/spinbox. SOP and SAT bypass independently via their own
  Enable checkboxes. Style picks ASC (clamped between SOP and SAT — the
  spec default, matches other CDL tools) or No Clip (extended range,
  matching this app's other stages — the default). "Save to CDL
  Library..." saves the stage's current values as a named, reusable
  entry. **Source:** `cdl.py` (wraps OCIO's own `CDLTransform`); the
  color-balance wheel widget's geometry/math is `color_balance.py` +
  `color_wheel_widget.py`. GPU-accelerated via a hand-written uniform
  shader, `gpu_cdl_shader.py` (matches OCIO's own generated CDL shader to
  within ~1.4e-7).

- **Grading Primary** — OCIO's pivot-based lift/gamma/gain-style primary
  grade, distinct from CDL (which is specifically ASC-standard
  interchange). A Style dropdown picks which field set applies: **Log**
  (Brightness/Contrast/Gamma, tuned for a log-encoded working space,
  default), **Lin** (Offset/Exposure/Contrast, scene-linear), or **Video**
  (Lift/Gamma/Gain, the classic 3-wheel grade) — each of the three visible
  fields gets its own drag-and-tint color-balance wheel (same control CDL
  uses) alongside its R/G/B/Master spinboxes; the wheel tints R/G/B apart
  from Master without changing Master, editing Master shifts all three
  together and keeps the wheel's own tint, and typing a fine R/G/B value
  updates the wheel's position without touching Master. Pivot and
  Saturation are always shown regardless of style. Contrast/Gamma/Gain
  are floored at 0.01 (OCIO's own `validate()` requires them strictly
  positive).
  **Source:** `grading_primary.py` (wraps OCIO's `GradingPrimaryTransform`/
  `GradingPrimary`/`GradingRGBM`). GPU-accelerated via OCIO's own
  `makeDynamic()` mechanism (`gpu_ocio_dynamic.py`) — dragging a slider
  pushes a GPU uniform, never recompiles the shader.

- **Exposure/Contrast** — OCIO's exposure/contrast/gamma tool. A Style
  dropdown picks which mid-gray reference contrast pivots around:
  **Linear** (scene-referred, default), **Logarithmic** (log-encoded),
  or **Video** (display-referred). Three fields: Exposure (stops),
  Contrast, Gamma. (Pivot/logExposureStep/logMidGray are intentionally
  not exposed — OCIO bakes those as shader constants even in a
  dynamic-uniform shader, so exposing them would force a recompile on
  every edit; OCIO's own defaults are used.) **Source:**
  `exposure_contrast.py` (wraps OCIO's `ExposureContrastTransform`).
  GPU-accelerated via `gpu_ocio_dynamic.py` (three independent dynamic
  properties — exposure/contrast/gamma each push their own uniform).

- **Grading Tone** — OCIO's print-style tone shaping across 5
  overlapping ranges: Blacks, Shadows, Midtones, Highlights, Whites, each
  with independent R/G/B/Master gain (each range also gets its own
  color-balance wheel, same behavior as Grading Primary's above, just a
  smaller diameter to fit all 5 ranges) plus its own Start/Width along the
  tone curve, plus a single S-Contrast scalar. Same Style dropdown as
  Grading Primary (Log/Lin/Video). Every range's gain is bounded (OCIO's
  own real, undocumented `validate()` limits — 0.1–1.9 for
  Blacks/Midtones/Whites, 0.2–1.8 for Shadows/Highlights), S-Contrast
  bounded 0.01–1.99; values are clamped rather than rejected. **Source:**
  `grading_tone.py` (wraps OCIO's `GradingToneTransform`/`GradingTone`/
  `GradingRGBMSW`). GPU-accelerated via `gpu_ocio_dynamic.py` — every
  field here (all 5 ranges plus S-Contrast) is a real GPU uniform under
  one dynamic property.

- **Grading RGB Curve** — four independently-editable channel curves
  (Red/Green/Blue/Master) mapping input value to output value, OCIO's
  curve-grading tool. Same Style dropdown (Log/Lin/Video). Pick a channel
  with the R/G/B/M buttons, then edit that channel's curve directly:
  click-drag on empty plot area to add a point and position it in one
  motion, right-click an existing point to remove it (minimum 2 points
  per curve), "Reset Channel to Identity" resets just the active channel.
  Points are constrained to the drawn plot box. **Source:**
  `grading_rgb_curve.py` (wraps OCIO's `GradingRGBCurveTransform`/
  `GradingRGBCurve`/`GradingBSplineCurve`); the curve-editing widget
  itself (drag/add/remove, Catmull-Rom curve rendering) is
  `curve_editor_widget.py`, a hand-rolled `QWidget`/`QPainter` control
  (pyqtgraph has no usable 2D curve-editor primitive — checked directly).
  GPU-accelerated via `gpu_ocio_dynamic.py` — all four curves push
  through one dynamic property's array-typed uniforms.

- **WARPier** — a hue/saturation warp equivalent to Resolve's Color
  Warper, built on OKLCh (a perceptually uniform color space) instead of
  raw HSL — equal steps around the wheel correspond to equal perceived
  color difference, unlike HSL's famously uneven hue spacing. Click empty
  space on the wheel to drop a new warp point (up to 12), drag any point
  both around the ring (hue) and toward/away from the white center
  (saturation), right-click a point to remove it. **Wake** controls how
  far a point's influence spreads to neighboring hues (shown as a
  translucent wash on the wheel); **Drop-off** controls how hard or soft
  that falloff is, from a plain gradual taper to a plateau that holds
  strength across most of the wake radius before cutting off sharply.
  "Reset to Identity" restores the three default seed points and default
  Wake/Drop-off. Working Color Space aware — the warp happens in a fixed
  perceptual reference space regardless of what gamut/encoding the
  footage is actually in, converting there and back around the warp
  itself. **Source:** `color_warper.py` (OKLab/OKLCh math, ported from
  the published Ottosson reference constants, plus the working-space-
  aware apply); `color_warper_wheel.py` (the wheel widget, same
  Qt-free-math/thin-Qt-wrapper split as `color_wheel_widget.py`).
  GPU-accelerated via `gpu_color_warper_shader.py` — point/Wake/Drop-off
  values are plain uniforms, so dragging a point never recompiles the
  shader.

- **ACES DRT** — engages the selected ACES DRT (Display Rendering
  Transform — the RRT+ODT chain, or ACES 2.0's unified Output Transform)
  for the scene-to-display transform, via OpenColorIO's built-in ACES
  configs. Pick a Config (which ACES version/config to use — Studio
  configs cover far more real camera spaces than CG configs, which are
  synthetic/render-only), a Working Color Space (what your footage is
  actually in), a Display, and a View (the specific Output Transform
  variant, e.g. "ACES 1.0 - SDR Video" or an HDR variant like "ACES 1.1 -
  HDR Video (1000 nits & P3 lim)"). **Source:** `aces_drt.py`
  (`apply_aces_output_transform`, via `PyOpenColorIO.Config.CreateFromBuiltinConfig`
  and a `DisplayViewTransform`). GPU-accelerated via `gpu_aces_drt_shader.py`
  — every SDR display/view needs zero GPU textures, but every HDR
  display/view needs one (a packed 1D shaper LUT OCIO's own codegen
  emits), which this module uploads and binds; this was the root cause of
  a real, fixed "milky"/compressed-looking HDR Viewer bug (the texture
  bind was previously missing entirely for this stage kind).

- **Color Space Conversion** — "these code values are actually in space
  X, re-express them in space Y." A plain reinterpretation with no
  rendering or creative decision involved: pick a Config (which OCIO ACES
  config to read spaces from), a Category (Scene-Referred camera/working
  spaces, or Display-Referred display standards), then From and To within
  that category. Scene-to-scene covers things like ARRI LogC3 → ACEScg;
  display-to-display covers repurposing an already-graded master between
  display standards (e.g. sRGB code values reinterpreted as Rec.709). It
  deliberately won't let you cross from scene-referred to display-
  referred or back — that conversion needs an actual rendering decision,
  which is what ACES DRT (above) is for. **Source:** `colorspace_convert.py`
  (`apply_colorspace_conversion`, a plain `ocio.ColorSpaceTransform`,
  reusing `aces_drt.py`'s config registry). GPU-accelerated via
  `gpu_shader_cache.py`'s `OcioGpuStageCache` (no texture needed for any
  conversion observed so far).

### Stack Forking

Blend two parallel chains of stages back into one signal — useful for
blending two LMTs, or any two grades, against each other. From the
Stack's **"☰"** menu or **Stack** menu bar entry, **Add Fork (Split +
Merge)** adds a **Fork Split** and a matching **Fork Merge** as a pair,
styled like any other stage row. Add any stage kind after Fork Split,
then use that row's own **Branch A / Branch B** dropdown (in the stage
list) to place it — assigned stages render as a two-column table between
Split and Merge, Branch A on the left half and Branch B on the right,
each evaluated top-to-bottom in order exactly like an ordinary run of
stages. If one branch has more stages than the other, the shorter column
just shows blank cells at the bottom to keep the rows lined up — as many
stages per branch as you like. The next stage after Fork Merge sees only
the single, rejoined signal.

Fork Merge's own slider sets the A↔B crossfade between the two branches'
results. Its **Opacity** (the same control every stage row has, built
into the row next to its name) is the fork's *total* opacity — it blends
the merged result against the signal as it stood right before the Fork
Split, same "this stage's fully-applied output blended against this
stage's own input" rule every other stage's opacity already follows.
Disabling Fork Split (its row's enabled-toggle dot) treats the whole
region — both branches and the merge — as a no-op.

Deleting one half of a fork pair (Fork Split or Fork Merge, via that
row's own "×") leaves the other half unmatched, which is a genuinely
invalid stack until you remove or re-pair it — an inline warning appears
below the Stack list explaining why, rather than the app crashing or
silently showing a stale preview.

**Source:** `stack.py`'s `_run_stages` (structural split/merge handling,
not a color operation itself — the operators inside each branch are each
still dispatched through `_apply_stage_to_array` as usual). Forks render
on the GPU (`gpu_stack_renderer.py`, fork merge via
`gpu_fork_merge_shader.py`) as long as every stage in the Stack is
GPU-eligible; nested forks aren't supported anywhere.

### Grid size and domain

These are export settings, not part of the Stack: they only matter when
a LUT is baked for export, and they're set in **File > Export...** under
**LUT Grid / Input Domain** (saved with the session; recalling a Stack
Memory doesn't change them). Grid size is the resolution of the exported
LUT (9 through 97, or type your own — 33 is the common default). Domain
is the input range each channel is sampled over, normally [0, 1];
uncheck "Link R/G/B" to set channels independently. "Use Input Bounds
From First Stack Entry" pulls the domain straight from the first stage
if it's a LUT that declares its own (e.g. a non-stock IDT/LMT). Changes
apply as you make them.

**Source:** `stack.py`'s `BakeSettings` and `bake_stack_to_lut3d`;
`bake_settings_form.py`.

## Parameters

The inspector for whichever stage is selected in the Stack — one
inspector page per operator kind, described under **Stack → Operators**
above (each operator's own fields, ranges, and source file are listed
there, not duplicated here). Every numeric field throughout the app (CDL
Slope/Offset/Power/Sat, Stack opacity, Domain min/max, ...) can be
click-dragged left/right to scrub its value up/down, not just typed into
or nudged with its up/down arrows — hold **Shift** while dragging for
finer control. Double-clicking a parameter's own name label resets just
that one parameter to its default — a single control (Printer Lights'
R/G/B, Exposure/Contrast's fields, ...) for a plain scalar, or a whole
wheel+spins field/range at once (CDL's Slope/Offset/Power, Grading
Primary's fields, Grading Tone's ranges) when the label names the group
rather than one spinbox.

**Source:** `stack_panel.py`'s per-kind `_build_*_inspector`/`_apply_*_live`
methods; the double-click-to-reset label is `_ParamLabel`.

## Scopes

Histogram, Waveform, and Vectorscope share one panel (pick which shows
from its "☰" menu or the Scopes menu bar entry). Two Waveform-only tree
submenus live in that same menu: "Waveform Style" (RGB Parade / Luma / R
/ G / B) and "Waveform Graticule" (None / PQ / mV / %) — PQ overlays
standard SMPTE ST 2084 nit levels (100/203/1000/4000/10000 nits), mV the
conventional 0–700mV waveform-monitor scale, and % a plain 0–100% scale
in steps of 10; exactly one graticule (or none) shows at a time.
"Graticule Intensity..." (same menu) opens a dialog with a single opacity
slider, shared across all three graticules, for dimming an overlay that's
too bright against a given image.

**Source:** `scopes_panel.py` (the panel/UI) driving `scopes.py`
(`compute_histogram`/`compute_waveform`/`compute_vectorscope`, plus the
graticule stop calculations) — always computed on CPU, capped to a small
fixed resolution for a GPU-eligible Stack (see `sample_image_panel.py`'s
`SCOPES_MAX_DIMENSION`) so this recompute stays cheap regardless of how
expensive the actual grade is.

## Graph

Two views of the Stack's baked LUT, picked from its **"☰"** menu (or the
**Graph** menu-bar entry): **3D**, or one of **1D Y** / **1D R** / **1D
G** / **1D B**. **3D** is the interactive point-cloud visualization of
the LUT's deformation lattice, with a wireframe **cage** connecting each
point to its lattice neighbors so the deformation reads as a mesh, not
just a cloud; rotate/zoom with the mouse; its **"☰"** menu's **3D
Settings...** opens a small dialog with "Grid density" (how many points
are sampled) and "Cage opacity" (fades the cage in/out, 0–100%, defaults
to 30%). Each **1D** choice plots exactly one of the same LUT's Y/R/G/B
response curves — output vs. input, the traditional "curves" view — swept
across [0, 1] — the Graph previews the Stack at a fixed 33³ / [0, 1]
grid, independent of the export grid size and domain (**Grid size and
domain** above). Views update live while the Graph is on screen (a
hidden or tabbed-away Graph catches up when shown) and stay in sync with
the Stack; switching between them never loses data or resizes the panel.

**Source:** `lut_viewer_3d.py`'s `LutViewer3D` (built on pyqtgraph's
`GLViewWidget`/`GLScatterPlotItem`/`GLLinePlotItem`), fed the same
`bake_stack_to_lut3d` result Export/the LUT Library use.

---

## Workspace & Window Management

Every panel can be **dragged by its title bar** to rearrange the whole
workspace freely — drop it against any edge of another panel to place it
there, drop it onto another panel's title bar to tab the two together, or
drag it out into open space to pop it into its own floating window. There
is no fixed layout to fight against; the arrangement described panel-by-
panel above is only where LUTier starts, not a constraint.

Every panel can also be shown/hidden from the **Workspace** menu (or its
own Shift+1 through Shift+9 shortcut), and every panel's "☰" button in
its top-right corner has the same actions as its matching entry in the
menu bar — whichever you find faster. Any panel can also be **floated**
out of the main window into its own window without dragging — Cmd+Shift+1
through Cmd+Shift+9 (same digit as its show/hide shortcut), or "Float
[Panel]" in the Workspace menu — which stays above the main window
specifically, though it can still be covered by (or cover) other floating
panels/dialogs and other applications entirely. Closing a floated panel's
window, or floating it again, puts it right back into the main window.

Hold **Alt** while hovering over an entry in Gallery (including a CDL
entry), LUT Library, Media Libraries, the Browser, or a stage in the
Stack itself to see a
floating info card with that entry's full details near the cursor —
handy for a saved Stack Memory's full stage-by-stage breakdown, or a
Library entry's full source file path, without selecting it first. Move
the cursor off the entry, or release Alt, to dismiss it.

LUTier remembers your panel arrangement (position, size, shown/hidden,
floating, window size — however you last left it, dragged or not)
automatically between launches — no separate save step. The Workspace
menu also has **Reset to Default Workspace** (snaps everything back to
LUTier's built-in layout) and **Export Workspace Preset...**/**Import
Workspace Preset...** (saves or loads a specific arrangement, including
floating state, as its own file — independent of whatever's currently
open, and separate from a saved session — see **Sessions** below).

The Workspace menu's **Accent Color...** lets you pick your own accent
color (used for selection, focus, panel headers, and highlights
throughout the app) in place of LUTier's default muted blue. It applies
immediately and is remembered between launches, independent of your
panel arrangement.

**Source:** `workspace_io.py` (layout persistence/presets), `hover_inspector.py`
(the Alt-hover info card), `theme_prefs.py`/`style.py` (accent color).

## Sessions

File > New/Open/Save/Save As saves everything — the Stack, the Source
and Reference images, and all three libraries (LUT, Gallery -- which
includes CDL entries, Media) — to a single `.ltr` file, so a saved
session reopens with the same grade and libraries. Unlike LUT Library or
Gallery entries, a Media Libraries entry is stored by reference to its
source file, not embedded — it survives a save/reload on the same
machine as long as the original file hasn't moved. Panel layout (position, size, shown/hidden/floating,
window size) is NOT part of a session — it's remembered automatically
between launches instead, and can be saved/loaded independently as its
own preset file — see **Workspace & Window Management** above.

**Source:** `session_io.py`.

## Export

File > Export... bakes the current Stack and saves it in the format
picked from the dropdown (Cube, CLF, Sony SPI3D, Cinespace, Autodesk 3D
LUT in either Flame or Lustre dialect, or FilmLight Truelight/Baselight
Cube) — pick the format, click Export..., choose where to save. The
dialog also holds the export grid size and domain (**Grid size and
domain** above) and shows the stage list of what's about to be baked,
plus three optional checkboxes, none of which prompt any
further dialogs of their own — everything's derived from the LUT's own
filename:

- **Export Still** — also saves a full-resolution graded still (same
  name as the LUT, `.png`) using whatever's loaded in the Viewer.
  Skipped with a note in the confirmation if no source image is loaded.
  Its own indented sub-checkbox, **Import Still to Media Libraries**, is
  only enabled when Export Still is checked, and additionally ingests
  that still into the Media Libraries.
- **Import to LUT Library** — also ingests the exported LUT into the LUT
  Library (the full-precision baked table, not a re-read of the exported
  file — see `lut_library_panel.py`'s docstring on why that distinction
  matters for a lossy format like `.3dl`).
- **Grab Still** — also saves the current Stack as a new Gallery still,
  named after the exported file.

**Source:** `main_window.py`'s Export dialog, writing via `colour-science`'s
own `colour.write_LUT` for most formats and `colour_clf_io`/`clf_adapter.py`
specifically for CLF.

## Undo/Redo

Edit > Undo/Redo covers edits to the Stack itself — adding, removing,
reordering, or adjusting a stage; recalling a Stack Memory; applying a
CDL or LUT Library entry; opening a session. It doesn't cover library
management (importing/renaming/deleting a saved LUT or CDL) or the
source image.

**Source:** `undo_history.py`'s `UndoHistory` (`session_io.py`'s
`undo_history_to_session_list` persists it across a saved session).

## Keyboard shortcuts

| Action | Shortcut |
|---|---|
| Undo | ⌘Z |
| Redo | ⌘⇧Z |
| New Session | ⌘N |
| Open Session... | ⌘O |
| Save Session | ⌘S |
| Save Session As... | ⌘⇧S |
| Export... | ⌘E |
| Show/Hide Browser | ⇧1 |
| Show/Hide Media Libraries | ⇧2 |
| Show/Hide LUT Library | ⇧3 |
| Show/Hide Galleries | ⇧4 |
| Show/Hide Viewer | ⇧5 |
| Show/Hide Stack | ⇧6 |
| Show/Hide Parameters | ⇧7 |
| Show/Hide Scopes | ⇧8 |
| Show/Hide Graph | ⇧9 |
| Media Libraries: Import... | ⌘I |
| Gallery: Grab Still | ⌘G |
| Viewer: Enable Wipe | ⌘W |
| Scopes: Histogram | ⌘1 |
| Scopes: Waveform | ⌘2 |
| Scopes: Vectorscope | ⌘3 |
"""
