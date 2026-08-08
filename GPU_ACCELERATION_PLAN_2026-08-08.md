# GPU Acceleration Project — Plan (2026-08-08)

A staged plan for introducing hardware-accelerated live preview to
LUTier, per Tom: "let's make a project of introducing hwaccel soon... I
know the way Resolve handles cubes is to convert them into shaders and
keep them in a system library folder." This is a multi-session effort —
each stage below is meant to be its own session (or close to it), with
its own working, demoable checkpoint, not one big-bang rewrite.

Background: `project_gpu_accelerated_preview.md` (auto-memory) has the
original benchmark (46ms/apply @ 480x270 vs. 764ms/apply @ native
1920x1080, confirming the live-preview path is 100% CPU-bound and scales
~linearly with pixel count) and the initial scope notes this plan expands
on.

## Why, concretely

Every OCIO-backed stage (`aces_drt.py`, `cdl.py`, `colorspace_convert.py`)
calls `getDefaultCPUProcessor()`, never `getDefaultGPUProcessor()`.
`stack.apply_stack_to_image_array` runs the whole enabled stage chain
against real pixels, on the CPU, every time the live preview needs a new
frame. This is why the Viewer's preview resolution is capped well below
native (`sample_image_panel.py`'s `PREVIEW_MAX_DIMENSION` /
`_desired_preview_dimension_for`) and why zooming in required a band-aid
(wiring zoom into the resize-triggered resolution-upgrade mechanism)
rather than just rendering at native res live. GPU acceleration is the
real fix for both the resolution ceiling and the interaction latency.

## Ground truth: what's actually in the codebase today

- **`stack.py`** is the single evaluation engine. `STAGE_KINDS = ("lut",
  "printer_lights", "aces_drt", "cdl", "colorspace", "fork_split",
  "fork_merge")`. `_apply_stage_to_array` is the one dispatch point --
  any GPU path has to produce equivalent output for each of these kinds,
  not just "run a LUT."
- **Only `lut`, `aces_drt`, `cdl`, `colorspace` stages are numerically
  interesting for a GPU port.** `printer_lights` and the fork stages are
  thin wrappers/compositing logic around the other four.
- **`lut` stages do NOT go through OCIO.** They call `lut.apply(array)`
  (a `colour.LUT3D` object, `colour-science`/numpy-based) or, if
  retargeted, `retarget_io(...)`. OCIO's GPU shader API has no direct
  reach into this path today.
- **`aces_drt`, `cdl`, `colorspace` all already go through OCIO**, each
  via `cfg.getProcessor(transform).getDefaultCPUProcessor()`. The two
  modules cache at different levels, and the difference matters for
  shader design: `aces_drt.py` (`_get_output_transform_processor`,
  `_get_colourspace_conversion_processor`, both `lru_cache`d on their
  string args -- this session's own perf-pass fix) caches the whole
  `CPUProcessor`, because a stage's working-colourspace/display/view
  rarely change once picked. `cdl.py` only caches the bare
  `ocio.Config.CreateRaw()` (`_get_raw_config`) and rebuilds the
  `CDLTransform`/`Processor`/`CPUProcessor` on every single call
  (`_apply_ocio_transform`) -- because CDL's slope/offset/power/sat come
  from continuously-dragged sliders, so caching a processor keyed on
  those exact values would almost never hit. **Implication for Stage 3**:
  a GPU CDL shader should take slope/offset/power/sat as uniforms fed
  into one compiled shader program, not bake them into shader source text
  that gets regenerated per slider tick -- the CPU side already learned
  this lesson (there's no equivalent "cache the CDL processor" perf fix
  because it wouldn't help), and the GPU design should start from it
  rather than rediscovering it. **Confirmed in Stage 2 (see that
  section's findings below): OCIO's own GPU codegen does NOT do this for
  free** -- `getDefaultGPUProcessor()` bakes CDL's values as literal GLSL
  constants by default, same as everything else it generates. Getting
  real uniforms means either switching to `ocio.GradingPrimaryTransform`
  (has `makeDynamic()`) or hand-writing CDL's shader directly instead of
  going through OCIO's codegen for this one stage kind -- a real decision
  Stage 3 needs to make explicitly, not something OCIO hands over
  automatically just because CDL's math happens to be uniform-friendly.
- **There is already a block-level result cache: `StageCache`
  (`stack.py`).** It partitions the stage list into blocks (one ordinary
  stage = one block; a fork_split..fork_merge region = one block),
  fingerprints each block (`_stage_fingerprint`), and re-runs only the
  changed suffix on each edit. This is the CPU-side precedent for exactly
  the shader-caching strategy Tom described for Resolve -- the natural
  design is to mirror this same block/fingerprint model on the GPU side
  (cache compiled shader stages, not just CPU array checkpoints),
  rather than inventing a separate caching scheme.
- **The Viewer (`WipeCompareWidget` in `wipe_compare_widget.py`) is a
  plain `QWidget` painted with `QPainter`/`QPixmap`.** No OpenGL
  anywhere in it. It currently owns: the wipe-divider drag/reset, zoom
  (scroll wheel, cursor-anchored) and pan (middle-drag), and a
  color-readout chip drawn on top (`_draw_color_readout`, driven by
  `set_color_readout_text`, called from `SampleImagePanel`). Any GPU
  rendering path needs to either keep this whole interaction surface
  working unchanged or deliberately re-implement each piece on the new
  surface -- there is no "just swap the paint call," these are stateful
  mouse-driven interactions tied to `_image_rect()`.
- **The ONLY existing OpenGL usage in this app is `lut_viewer_3d.py`**
  (pyqtgraph's `GLViewWidget`, for the 3D point-cloud/cage view) -- a
  completely separate concern (geometry rendering, not image/texture
  rendering), but worth checking for any macOS-specific GL context
  gotchas already solved there (context sharing, `AA_ShareOpenGLContexts`
  -- see `conftest.py`) before assuming a second, independent GL surface
  in the same window is trouble-free.
- **`bake_stack_to_lut3d` (export path) must stay CPU/exact, unchanged.**
  It's the thing that produces the actual `.cube`/`.clf`/etc. files users
  ship. GPU acceleration is a live-preview-only concern.

## The precision trade-off, made on purpose (Stage 0)

`apply_stack_to_image_array`'s own docstring is explicit that the live
preview deliberately does exact per-stage math on real pixels rather than
baking to one interpolated cube, specifically to avoid LUT-interpolation
error. A GPU shader path is a **different, lower-precision numerical
path** almost by definition (typically float32 3D-texture sampling with
hardware trilinear interpolation for the `lut` stage kind, and whatever
precision OCIO's generated GLSL uses internally for the others) -- not
bit-identical to today's float64/exact CPU result.

This needs an explicit, written decision before Stage 1 writes any
rendering code, not a decision made implicitly by whatever falls out of
the implementation:

- **Recommended framing**: the live preview becomes "fast and
  representative," not "exact" -- same spirit as any NLE/grading tool's
  real-time preview vs. its render/export pass. Bake/export
  (`bake_stack_to_lut3d`) stays the one source of truth for exact output,
  completely unaffected by any of this.
- **Concretely surfaced to the user**: worth deciding whether the GPU
  preview needs any UI signal ("preview approximate" indicator, or a
  manual "exact preview" toggle that falls back to the existing CPU path)
  or whether the difference is expected to be visually negligible enough
  not to need one. This is a product decision, not just an engineering
  one -- flag it to Tom before Stage 5 (first real GPU pixels on screen)
  rather than deciding it silently.

## Staged plan

Each stage should end with something that can actually be looked at /
tested, per the standing "one task at a time" process. Stages are ordered
to front-load risk (context/plumbing risk in Stage 1, correctness risk in
Stage 2) before any UI-visible behavior changes.

### Stage 1 -- OpenGL surface swap, still CPU pixels

Replace `WipeCompareWidget`'s `QPainter`/`QPixmap` rendering with a
`QOpenGLWidget`-based one, but keep feeding it CPU-computed pixels (via
`glTexImage2D`/PySide6's OpenGL bindings) -- i.e. change *only* the
rendering surface, not the color math. Re-implement on the new surface,
verified against the existing behavior/tests:
- The wipe divider (drag, `reset_wipe`, double-click-left)
- Zoom (wheel, cursor-anchored math from `_image_rect`) and pan
  (middle-drag, `reset_view`, middle-double-click)
- The color-readout chip
- All 14 existing zoom/pan tests plus the pre-existing wipe tests in
  `test_wipe_compare_widget.py` should keep passing against the new
  widget with minimal changes (ideally none to the test file's
  assertions, since the *behavior* isn't changing, only *how* it's
  drawn) -- if a test needs real rewriting to keep passing, that's a
  signal the interaction logic leaked into paint-specific code paths and
  needs to be separated out.

Deliverable: the Viewer looks and behaves identically to today, launched
and confirmed on real hardware, but is now rendering through OpenGL.
Sets up the surface Stage 5 will actually draw shaded output onto.

### Stage 2 -- OCIO GPU shader prototype (standalone, no UI)

A standalone script (not wired into the app yet): take a real stack
containing an `aces_drt` and/or `cdl` stage, call
`processor.getDefaultGPUProcessor()` -> `GpuShaderDesc` -> real GLSL,
run it against a test image via a minimal offscreen GL context, and
diff the result against `stack.apply_stack_to_image_array`'s CPU output
pixel-by-pixel. Confirms (a) OCIO's GPU codegen actually produces correct
output for the specific transforms this app uses (ACES DRT, CDL), not
just that the API exists, and (b) puts a real number on the precision
delta from Stage 0's trade-off decision, instead of an assumption.

Deliverable: a written note (numbers, not vibes) on max/mean pixel delta
between CPU and GPU output for a representative stack.

**DONE (2026-08-08) -- see `gpu_shader_prototype.py`, run directly with
`python3 gpu_shader_prototype.py`.** Real GPU render via a Qt
`QOffscreenSurface`/`QOpenGLContext` (no visible window, but a genuine
GPU context -- `GL_VERSION` reported `4.1 Metal - 88`, `GL_RENDERER`
`Apple M1`), full float32 throughout (input texture, FBO attachment, and
readback all `RGBA32F`) so the measured delta reflects the shader math
itself, not an incidental 8-bit texture round-trip. Tested against both a
real bundled sample image and a synthetic gradient deliberately spanning
outside [0,1] (since ACES-family working spaces routinely produce
negative and >1.0 values, and CDL's `power()` term in particular could in
principle behave differently GPU vs. CPU on a negative base):

| Transform | Test image | max \|delta\| | mean \|delta\| | CPU output range |
|---|---|---|---|---|
| CDL | bundled sample (256px, [0,1]) | 0.0000102 | 0.0000031 | [0.09, 0.87] |
| CDL | synthetic gradient ([-0.1, 1.1]) | 0.0000174 | 0.0000038 | [-0.31, 1.42] |
| ACES DRT | bundled sample (256px, [0,1]) | 0.0000422 | 0.0000024 | [-0.93, 1.00] |
| ACES DRT | synthetic gradient ([-0.1, 1.1]) | 0.0001533 | 0.0000041 | [-6.10, 1.20] |

**Verdict on Stage 0's precision question**: negligible in practice. Even
the worst case (ACES DRT, wide-range synthetic input) is ~0.00015 against
a multi-unit output range -- more than 30x smaller than one 8-bit display
quantization step (1/255 ≈ 0.0039), and still under one 12-bit code value
(1/4095 ≈ 0.000244) -- checked since 8-bit is a low bar for a color tool:
CDL's worst case is ~0.07 of a 12-bit step (0.0000174 / 0.000244), ACES
DRT's worst case is ~0.63 of a 12-bit step on the synthetic out-of-range
test but only ~0.17 of a step (0.0000422 / 0.000244) on the realistic
bundled-image test -- the worst number only shows up on deliberately
extreme synthetic input, not realistic content. Stage 0's "surface an
approximate-preview indicator in the UI" branch does not appear
warranted based on this measurement, at either bit depth; the "fast and
representative" framing needs no visible caveat for these two transform
kinds. (Not yet measured: the non-OCIO `lut` stage kind, deferred to
Stage 4 where its GPU path is actually decided.)

**A second, equally important finding -- shader SHAPE, not just
accuracy**: `getDefaultGPUProcessor()`'s default codegen bakes BOTH
CDL's and ACES DRT's parameter values as literal GLSL constants
(0 uniforms, 0 textures, for both) rather than exposing them as
uniforms. Checked directly: `ocio.CDLTransform` has no dynamic-property
API at all; `ocio.GradingPrimaryTransform` (a newer, real-time-editing-
oriented OCIO 2.x transform type) does, via `makeDynamic()`/`isDynamic()`
/`makeNonDynamic()`. Practical implication for Stage 3:
- **ACES DRT is fine as-is.** Its parameters (working colourspace,
  display, view) are picked, not continuously dragged -- regenerating/
  recompiling its shader only on an actual selection change matches
  exactly how the CPU side already caches it
  (`_get_output_transform_processor`, `lru_cache`d on those same
  strings). No design change needed.
- **CDL is the real design decision Stage 3 needs to make**, since its
  slope/offset/power/sat come from continuously-dragged sliders and
  regenerating+recompiling a shader on every tick would reintroduce
  exactly the kind of per-edit cost this whole project exists to
  eliminate. Two real options, not yet chosen: (a) migrate this app's
  CDL representation from `ocio.CDLTransform` to
  `ocio.GradingPrimaryTransform` to get real OCIO-managed uniforms via
  `makeDynamic()`, or (b) bypass OCIO's CDL codegen entirely for this one
  stage kind and hand-write the (small, ~10-line) CDL fragment shader
  directly with explicit `glUniform3fv` calls for slope/offset/power and
  a `glUniform1f` for saturation -- CDL's math is simple enough that
  hand-writing it isn't a real burden, unlike ACES DRT's ~13,500-char
  generated shader (B-spline curves, gamut tables), which is not
  something to hand-write. Decide this at the start of Stage 3, not
  during it.

### Stage 3 -- Shader generation + caching layer

Wrap Stage 2's shader generation into a real module (analogous to
`aces_drt.py`'s/`cdl.py`'s existing `lru_cache`d `_get_cpu_processor`
functions, but producing compiled GPU shader programs instead of
`CPUProcessor`s). Mirror `StageCache`'s block/fingerprint model
(`_stage_fingerprint`, `_partition_into_blocks`) so editing one stage's
params only recompiles that stage's shader, not the whole chain -- and
add the disk-persistence layer Tom asked for (a cache directory storing
compiled shader source/binaries keyed by a stage's fingerprint, so a
previously-seen stage configuration doesn't need to regenerate GLSL
after an app restart, mirroring "Resolve keeps them in a system library
folder").

Deliverable: a benchmark showing shader-generation cost is paid once per
distinct stage configuration, not once per frame/edit.

### Stage 4 -- The non-OCIO `lut` stage kind

Plain `lut` stages (`colour.LUT3D`, numpy-based) have no OCIO GPU path.
Two real options to evaluate against each other, not just pick blindly:
1. Route them through OCIO's own `Lut3DTransform`/`FileTransform`
   machinery (if the loaded LUT can be re-expressed that way) to reuse
   Stage 3's infrastructure uniformly.
2. Hand-write a GPU 3D-texture sampler (upload the LUT table as a 3D
   texture, sample with hardware trilinear interpolation in a small
   fragment shader) as a separate, LUT-specific GPU path alongside OCIO's.

Deliverable: whichever approach is chosen, working and validated against
CPU `lut.apply()` output the same way Stage 2 validated the OCIO stages.

### Stage 5 -- Wire it into the live Viewer

Combine Stages 1+3+4: `SampleImagePanel`'s live preview path switches
from calling `apply_stack_to_image_array` (CPU) to rendering through the
Stage 1 GL surface with Stage 3/4's compiled shaders, chained per stage
in stack order. This is the first point real GPU-shaded pixels appear on
screen in the actual app -- resolve Stage 0's UI-signal question before
this ships. `bake_stack_to_lut3d` (export) is completely untouched.

Deliverable: live preview renders via GPU, at full native resolution,
with interaction latency low enough that the zoom-resolution band-aid
(`sample_image_panel.py`'s zoom-triggered resolution upgrade) becomes
unnecessary at typical preview sizes -- worth explicitly re-measuring
whether it's still needed at all once this lands, rather than assuming.

### Stage 6 -- Fork regions on GPU

`fork_split`/`fork_merge` (two independent branches blended back
together, see `stack.py`'s "STACK FORKING" docstring) need two render
targets (one per branch) and a blend pass, rather than a single linear
shader chain. Deliberately last, since it's the one structurally
different case in `_run_stages` and shouldn't block the common
single-chain case from shipping first.

## Risks / open questions to revisit as stages land

- Whether `QOpenGLWidget` alongside `lut_viewer_3d.py`'s pyqtgraph
  `GLViewWidget` in the same window causes any context-sharing surprises
  on macOS specifically (this app's only real target platform) --
  check early in Stage 1, not assumed fine.
- Whether OCIO's generated GLSL version/profile is compatible with
  whatever GL context PySide6's `QOpenGLWidget` gives on macOS (Apple's
  OpenGL is capped at 4.1 core and deprecated in favor of Metal) --
  worth confirming in Stage 2's standalone prototype before Stage 5
  depends on it.
- Whether the precision delta measured in Stage 2 is actually small
  enough in practice to be a non-issue, or whether Stage 0's "surface it
  in the UI" branch needs to actually be built.

## What does NOT change

- `bake_stack_to_lut3d` and every export format writer -- exact CPU path,
  untouched, remains the one source of truth for shipped output.
- `lut_viewer_3d.py`'s 3D point-cloud/cage view -- unrelated concern
  (geometry, not image rendering).
- The Scopes panel (histogram/waveform/vectorscope) -- reads from
  whatever array the preview already produced; not in scope unless a
  later session decides scope computation itself should also move to
  GPU (not assumed here).
