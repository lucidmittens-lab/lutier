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
"""

USER_MANUAL_MARKDOWN = r"""# LUTier — Basic User Manual

LUTier is a LUT/color-grading tool: build up a grade as an ordered stack
of operations, preview it live, and export it in the format you need.
This is a quick feature outline for testers, not a full reference.

## The window, at a glance

- **Browser** (top left) — point it at a folder; it lists LUT files
  (`.cube`, `.clf`, `.spi1d`, `.spi3d`, `.spimtx`, `.csp`) and ASC CDL
  files (`.cc`, `.cdl`, `.ccc`) together, CDL ones tagged `[CDL]`.
  Double-click a LUT to add it to the Stack; double-click a CDL to import
  it into the CDL Library. Drag either kind onto its matching Library
  panel, or drag a LUT onto the Stack directly. "New Identity LUT..."
  starts a stage from a neutral pass-through instead of a file.
- **Image Library** (between Browser and LUT Library) — a shelf of
  reference images imported into the session. Import a file, then
  double-click an entry (or use its "..." menu) to load it as the
  Viewer's Sample or Reference image.
- **LUT Library** — LUTs you've ingested into the session. Once
  imported, a LUT's full data lives in the session — moving, renaming, or
  deleting the original file afterward won't break anything.
- **CDL Library** — saved ASC CDL grades (SOP/SAT values), either saved
  from a CDL stage in the Stack or imported from a file.
- **Stack Memories** — named snapshots of the whole Stack, so you can
  save a look, keep working, and get back to it later. Can also be
  exported to/imported from their own `.ltm` file, independent of the
  session.
- **Viewer** — the sample image preview, before/after wipe compare.
  Two roles: **Sample**, which shows the Stack's live result, and
  **Reference**, a second image the Stack never touches — useful for
  comparing your grade against a separate plate, still, or approved
  frame. Load each from the Image Library or via the Viewer's "..."
  menu ("Load Sample Image..." / "Load Reference Image..."). That same
  menu's "Left Image"/"Right Image" submenus each pick independently
  from three sources — Sample Before, Sample After, or Reference — and
  "Enable Wipe" switches to a plain single-image view (showing whatever
  "Right Image" is set to) when you don't need the split-screen compare.
  Reference has its own "Adapt Reference..." entry, unmanaged by default.
- **Stack** — the main work area: an ordered list of stages (LUT,
  Printer Lights, ACES DRT, ASC CDL, Color Space Conversion). Reorder by
  dragging, toggle any stage on/off, give it its own opacity, click a
  stage to edit its parameters in the Parameters panel.
- **Parameters** — the inspector for whichever stage is selected in the
  Stack.
- **Scopes** — Histogram, Waveform, and Vectorscope share one panel (pick
  which shows from its "..." menu or the Scopes menu bar entry). Two
  Waveform-only tree submenus live in that same menu: "Waveform Style"
  (RGB Parade / Luma / R / G / B) and "Waveform Graticule" (None / PQ /
  mV / %) — PQ overlays standard SMPTE ST 2084 nit levels
  (100/203/1000/4000/10000 nits), mV the conventional 0–700mV
  waveform-monitor scale, and % a plain 0–100% scale in steps of 10;
  exactly one graticule (or none) shows at a time. "Graticule
  Intensity..." (same menu) opens a dialog with a single opacity slider,
  shared across all three graticules, for dimming an overlay that's too
  bright against a given image.
- **Graph** (bottom right, below Scopes) — two views of the Stack's baked
  LUT, picked from its **"..."** menu (or the **Graph** menu-bar entry):
  **3D**, or one of **1D Y** / **1D R** / **1D G** / **1D B**. **3D** is
  the interactive point-cloud visualization of the LUT's deformation
  lattice, with a wireframe **cage** connecting each point to its lattice
  neighbors so the deformation reads as a mesh, not just a cloud; rotate/
  zoom with the mouse, "Grid density" controls how many points are
  sampled, "Cage opacity" fades the cage in/out (0–100%, defaults to
  30%). Each **1D** choice plots exactly one of the same LUT's Y/R/G/B
  response curves — output vs. input, the traditional "curves" view —
  swept across the Stack's own Domain Min/Max (Stack's "..." menu → Edit
  Size/Domain...) rather than a fixed 0–1 range, so it stays correct for
  HDR/scene-linear grades. All views update live and stay in sync with
  the Stack; switching between them never loses data or resizes the
  panel.

Every panel can be **dragged by its title bar** to rearrange the whole
workspace freely — drop it against any edge of another panel to place it
there, drop it onto another panel's title bar to tab the two together, or
drag it out into open space to pop it into its own floating window. There
is no fixed layout to fight against; the arrangement shown in this manual
is only where LUTier starts, not a constraint.

Every panel can also be shown/hidden from the **Workspace** menu (or its
own Shift+1 through Shift+0 shortcut), and every panel's "..." button in
its top-right corner has the same actions as its matching entry in the
menu bar — whichever you find faster. Any panel can also be **floated**
out of the main window into its own window without dragging — Cmd+Shift+1
through Cmd+Shift+0 (same digit as its show/hide shortcut), or "Float
[Panel]" in the Workspace menu — which stays above the main window
specifically, though it can still be covered by (or cover) other
floating panels/dialogs and other applications entirely. Closing a
floated panel's window, or floating it again, puts it right back into
the main window.

LUTier remembers your panel arrangement (position, size, shown/hidden,
floating, window size — however you last left it, dragged or not)
automatically between launches — no separate save step. The Workspace
menu also has **Reset to Default Workspace** (snaps everything back to
LUTier's built-in layout) and **Export Workspace Preset...**/**Import
Workspace Preset...** (saves or loads a specific arrangement, including
floating state, as its own file — independent of whatever's currently
open, and separate from a saved session — see Sessions below).

## Stack operators

The Viewer previews raw code values, Unmanaged by default — use the
Viewer's "..." menu's **Adapt Sample Image...** to declare a source
space and convert it non-destructively before preview.

- **Color Space Conversion** — "these code values are actually in space
  X, re-express them in space Y." A plain reinterpretation with no
  rendering or creative decision involved: pick a Config (which OCIO
  ACES config to read spaces from), a Category (Scene-Referred camera/
  working spaces, or Display-Referred display standards), then From and
  To within that category. Scene-to-scene covers things like ARRI LogC3
  → ACEScg; display-to-display covers repurposing an already-graded
  master between display standards (e.g. sRGB code values reinterpreted
  as Rec.709). It deliberately won't let you cross from scene-referred to
  display-referred or back — that conversion needs an actual rendering
  decision, which is what ACES DRT (below) is for.
- **LUT Retarget** — not its own stage, but a checkbox on any LUT stage's
  own inspector ("Retarget (reinterpret this LUT's declared input/output
  color space)"). Use it when a LUT was authored expecting one space but
  you want to feed it / read it in another — e.g. an LMT built for
  ACEScct fed ACES2065-1 values directly instead. Pick From (the space
  the LUT actually expects) and To (the space you want to use instead);
  LUTier wraps the LUT with a pre-conversion and post-conversion rather
  than resampling the LUT's own table, so the original data never
  changes.
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
  to a no-op.
- **ASC CDL (SOP/SAT)** — the standard primary-grade formula every
  grading tool exchanges as `.cc`/`.cdl`/`.ccc` files: Slope/Offset/Power
  per channel, plus an overall Saturation. Each of Slope/Offset/Power
  gets a color-balance wheel plus master and fine R/G/B spinboxes; Sat is
  a single slider/spinbox. SOP and SAT bypass independently via their own
  Enable checkboxes. Style picks ASC (clamped between SOP and SAT — the
  spec default, matches other CDL tools) or No Clip (extended range,
  matching this app's other stages). "Save to CDL Library..." saves the
  stage's current values as a named, reusable entry.
- **ACES DRT** — engages the selected ACES DRT for the scene-to-display
  transform, via OpenColorIO's built-in ACES configs. Pick a Config
  (which ACES version/config to use — Studio configs cover far more real
  camera spaces than CG configs, which are synthetic/render-only), a
  Working Color Space (what your footage is actually in), a Display, and
  a View (the specific Output Transform variant, e.g. "ACES 1.0 - SDR
  Video" or an HDR variant).

## Stack Forking

Blend two parallel chains of stages back into one signal — useful for
blending two LMTs, or any two grades, against each other. From the
Stack's **"..."** menu or **Stack** menu bar entry, **Add Fork (Split +
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
results. Its **Opacity** (the same control every stage row has, below
the stage list) is the fork's *total* opacity — it blends the merged
result against the signal as it stood right before the Fork Split,
same "this stage's fully-applied output blended against this stage's
own input" rule every other stage's opacity already follows. Disabling
Fork Split (its row's ✓/✗ toggle) treats the whole region — both
branches and the merge — as a no-op.

Deleting one half of a fork pair (Fork Split or Fork Merge, via that
row's own "×") leaves the other half unmatched, which is a genuinely
invalid stack until you remove or re-pair it — an inline warning appears
below the Stack list explaining why, rather than the app crashing or
silently showing a stale preview.

## Grid size and domain

Set from the Stack's **"..."** menu or **Stack** menu bar entry →
**Edit Size/Domain...**. Grid size is the resolution of the final baked
LUT (9 through 97, or type your own — 33 is the common default). Domain
is the input range each channel is sampled over, normally [0, 1];
uncheck "Link R/G/B" to set channels independently. "Use Input Bounds
From First Stack Entry" pulls the domain straight from the first stage
if it's a LUT that declares its own (e.g. a non-stock IDT/LMT). Hit
**Apply Grid Size / Domain** to commit.

## Sessions

File > New/Open/Save/Save As saves everything — the Stack, the Sample
and Reference images, and all four libraries (LUT, CDL, Stack Memories,
Image) — to a single `.ltr` file, so a saved session reopens with the
same grade and libraries. Unlike LUT/CDL Library entries, an Image
Library entry is stored by reference to its source file, not embedded —
it survives a save/reload on the same machine as long as the original
file hasn't moved. Panel layout (position, size, shown/hidden/floating,
window size) is NOT part of a session — it's remembered automatically
between launches instead, and can be saved/loaded independently as its
own preset file — see Workspace above.

## Export

File > Export... bakes the current Stack and saves it in the format
picked from the dropdown (Cube, CLF, Sony SPI3D, Cinespace, Autodesk 3D
LUT in either Flame or Lustre dialect, or FilmLight Truelight/Baselight
Cube) — pick the format, click Export..., choose where to save. The
dialog also shows the grid size, domain, and stage list of what's about
to be baked, plus three optional checkboxes, none of which prompt any
further dialogs of their own — everything's derived from the LUT's own
filename:

- **Export Still** — also saves a full-resolution graded still (same
  name as the LUT, `.png`) using whatever's loaded in the Viewer.
  Skipped with a note in the confirmation if no sample image is loaded.
  Its own indented sub-checkbox, **Import Still to Image Library**, is
  only enabled when Export Still is checked, and additionally ingests
  that still into the Image Library.
- **Import to LUT Library** — also ingests the exported LUT into the LUT
  Library (the full-precision baked table, not a re-read of the exported
  file — see lut_library_panel.py's docstring on why that distinction
  matters for a lossy format like .3dl).
- **Save Stack Memory** — also saves the current Stack as a new Stack
  Memory, named after the exported file.

## Undo/Redo

Edit > Undo/Redo covers edits to the Stack itself — adding, removing,
reordering, or adjusting a stage; recalling a Stack Memory; applying a
CDL or LUT Library entry; opening a session. It doesn't cover library
management (importing/renaming/deleting a saved LUT or CDL) or the
sample image.

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
| Show/Hide Image Library | ⇧2 |
| Show/Hide LUT Library | ⇧3 |
| Show/Hide CDL Library | ⇧4 |
| Show/Hide Stack Memories | ⇧5 |
| Show/Hide Viewer | ⇧6 |
| Show/Hide Stack | ⇧7 |
| Show/Hide Parameters | ⇧8 |
| Show/Hide Scopes | ⇧9 |
| Show/Hide Graph | ⇧0 |
| Image Library: Import... | ⌘I |
| Image Library: Load as Sample | ⌘L |
| Image Library: Load as Reference | ⌘R |
| Stack Memories: Save Current Stack... | ⌘G |
| Stack Memories: Load | ⌘⇧G |
| Stack Memories: Update | ⌘U |
| Stack Memories: Export... | ⌘⇧E |
| Viewer: Enable Wipe | ⌘W |
| Scopes: Histogram | ⌘1 |
| Scopes: Waveform | ⌘2 |
| Scopes: Vectorscope | ⌘3 |
"""
