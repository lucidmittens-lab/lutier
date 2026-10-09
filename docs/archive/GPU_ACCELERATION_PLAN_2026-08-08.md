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
  thin wrappers/compositing logic around the other four. (As of this
  doc's original writing -- `printer_lights` got its own real GPU path in
  Stage 7 below, once it turned out to need more than a thin wrapper.)
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

**DONE (2026-08-08).** Per the CDL uniform question Stage 2 raised, Tom's
decision: hand-write CDL's shader (bit-accuracy required), and separately
backlog `ocio.GradingPrimaryTransform` as a possible FUTURE, ADDITIONAL
stage kind of its own -- not a CDL replacement, since CDL's job in this
app is specifically standard/interchange ASC CDL support, not the
primary grading tool (added to Backlog below).

- **`gpu_cdl_shader.py`** -- `CDL_SHADER_SOURCE`, a hand-transcribed copy
  of OCIO's own generated GLSL for both CDL styles (`no_clip`/`asc`),
  parameterized via real uniforms (`uCdlSlope`/`uCdlOffset`/`uCdlPower`/
  `uCdlSaturation`/`uCdlClamped`) instead of baked constants.
  `test_gpu_cdl_shader.py` confirms it matches OCIO's own generated
  shader to within 1.4e-7 (float32 noise) across 16 random (style,
  params) combinations, and matches `cdl.apply_cdl` (CPU) to within
  2.9e-5 -- not a one-time eyeball check, a repeatable test.
- **`gpu_shader_cache.py`** -- `OcioGpuStageCache` (fingerprint ->
  compiled program, for ACES DRT/colorspace-shaped stage kinds whose
  shader depends on a small, rarely-changing config rather than
  continuously-dragged values) plus a disk-persisted GLSL SOURCE cache
  under `~/Library/Application Support/LUTier/gpu_shader_cache/`
  (Tom's "system library folder," per Resolve) keyed by a hashed
  fingerprint. `CdlGpuProgram` is the trivial CDL-specific equivalent:
  compiles `CDL_SHADER_SOURCE` once, lazily, forever after. **Deliberately
  NOT caching compiled GL binaries to disk** -- `glGetProgramBinary`'s
  format is opaque/driver-specific and macOS's OpenGL (deprecated, Metal-
  backed) is a real portability risk that hasn't been validated; disk
  caching covers the OCIO-codegen step only, the GL compile itself still
  happens at least once per process. Flagged as a real follow-up, not
  silently scoped out.
- **`gpu_shader_prototype.py` refactored**: `render_ocio_shader_on_gpu`
  split into `compile_ocio_program` + `render_program_on_gpu`, so a
  compiled program can be rendered many times across edits without
  recompiling -- the actual mechanism this caching layer depends on.
- **`gpu_shader_cache_benchmark.py`** -- the actual deliverable. Real
  numbers, both against the naive "regenerate + recompile every edit"
  baseline, both at 256x256:

  | Scenario | naive | cached | speedup |
  |---|---|---|---|
  | CDL, 30 simulated slider edits | 19.981 ms/edit | 1.789 ms/edit | 11.2x |
  | ACES DRT, 20 simulated display/view switches among 3 | 11.448 ms/switch | 2.770 ms/switch | 4.1x |

  **A real, useful surprise worth flagging**: Stage 2's own isolated
  compile-time measurement (`_link_program` in a tight loop, no draw
  calls) said ~0.95ms/compile -- this benchmark's naive-vs-cached delta
  is far larger (~18ms for CDL, ~8.7ms for ACES DRT). The likely
  explanation: Apple's OpenGL-over-Metal driver appears to defer real
  shader compilation (to a Metal pipeline state object) until the
  program's FIRST ACTUAL DRAW CALL, not `glLinkProgram` time -- Stage 2's
  isolated measurement never issued a draw call, so it measured only the
  cheap part. This benchmark's numbers, which do include a real
  `glDrawArrays`, are the trustworthy ones; Stage 2's ~0.95ms figure
  undersold the true cost and should not be relied on. The qualitative
  conclusion is solid either way (caching is a real, large win, not a
  micro-optimization) but the exact ms figures above should be treated as
  indicative for this GPU/driver, not a portable constant -- consistent
  with this whole project's "measure the real thing, don't extrapolate
  from a synthetic microbenchmark" discipline.

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

**DONE (2026-08-09), done AFTER Stage 5 rather than before** (reordered
per the original plan's own Stage 5 reordering precedent -- a live report
that a `lut` stage anywhere in a stack was forcing the ENTIRE chain back
to CPU, silently undoing Stages 2/3/5's work for every OTHER stage in
that same stack, made this the actual bottleneck once Tier 1's operators
were also GPU-eligible). **Option 1 (route through OCIO) won**, decisively,
after validating both against real numbers rather than picking blindly:
`ocio.Lut3DTransform` with `INTERP_LINEAR` matches `colour.LUT3D.apply()`'s
own default trilinear interpolator to 1.4e-7 on CPU, and a REAL GPU
render through OCIO's own generated shader matches `colour.LUT3D.apply()`
to 5.4e-4 -- OCIO's own codegen already solves the half-texel coordinate
remap a hand-written `sampler3D` lookup would have needed to get right
itself (confirmed directly in the generated GLSL: `(color.zyx * (size-1)
+ 0.5) / size`, not a naive `color.rgb`), so Option 2's "hand-write it"
work was never actually needed.

- **`gpu_lut_shader.py`** (new) -- `Lut3DGpuCache`, fingerprinted on
  `id(lut)` (not the table's values -- matches `stack._stage_fingerprint`'s
  own established "loaded/baked LUT objects are never mutated in place"
  guarantee for `"lut"` stages). Builds an `ocio.Lut3DTransform` from the
  `colour.LUT3D`'s own grid values, compiles OCIO's generated shader, and
  uploads the LUT's own values (read back via `GpuShaderDesc.get3DTextures()`
  rather than re-deriving them) as a real `GL_TEXTURE_3D` with linear
  filtering. `test_gpu_lut_shader.py` (4 tests) confirms eligibility
  gating, the GPU-vs-CPU match above, cache identity-reuse, and resource
  cleanup.
- **SCOPE LIMIT, documented not silent**: only a plain, default-`[0,1]`-
  domain `colour.LUT3D` is GPU-eligible -- a `LUT1D`, a non-default
  domain, or `retarget_from`/`retarget_to` wrapping (a whole separate
  colorspace-conversion step this doesn't chain in yet) all fall the
  WHOLE containing stack back to CPU, exactly the behavior every `"lut"`
  stage already had before this landed -- never a regression, just not
  yet a widened fast path for those specific cases.
- **`gpu_stack_renderer.py`** -- `"lut"` added to `GPU_ELIGIBLE_KINDS`;
  `is_stack_gpu_eligible` grew a `"lut"`-specific extra check (retarget +
  `gpu_lut_shader.is_lut3d_gpu_eligible`) alongside the general kind
  check. Unlike every Tier 1 kind, a `"lut"` stage's own table is never
  live-edited via any UI (only enabled/opacity/retarget are), so there's
  no per-edit uniform push here at all -- just the cached compiled
  program + a `GL_TEXTURE_3D` bound to unit 1 (`uInput`'s own 2D texture
  always takes unit 0). Two new `test_gpu_stack_renderer.py` render
  tests (single `lut` stage, `lut` -> `cdl` chain) plus eligibility-gating
  updates (a plain LUT3D is now eligible; the OLD assertion that ANY
  `"lut"` stage disqualified a stack no longer holds and was updated,
  not just patched around, since that's the actual, intended behavior
  change here) -- both chains matched CPU to ~7e-4, consistent with the
  standalone validation above.
- Full suite (428 tests) and all 7 standalone GPU test scripts (this
  Stage's own `test_gpu_lut_shader.py` now included) re-confirmed
  passing.

**CRITICAL FOLLOW-UP (2026-08-10): "painfully slow" reported right after
this landed -- NOT a GPU problem.** `sample_image_panel.py`'s CPU
recompute (for `ScopesPanel` only, unrelated to what the GPU-rendered
Viewer shows) was still running at up to native resolution on every
isolated edit and on every burst's settle tick (the 2026-08-09 burst-skip
fix only covered MID-burst ticks) -- and `colour.LUT3D.apply()`, a `"lut"`
stage's own CPU path, measured **0.5-3.3 SECONDS** at native/half preview
resolution (1920x1080 to 3840x2160), multiple real seconds of UI-thread
stall per edit for any stack with a cube in it, regardless of how fast
the GPU path itself had become. Fixed in `sample_image_panel.py`: a new
`SCOPES_MAX_DIMENSION` (512px long edge) tier -- a GPU-eligible stack's
CPU recompute now ALWAYS runs at this small, fixed, ceil-rounded-down
size (never native, never even the existing `EDIT_DOWNRES_FACTOR`-halved
size), for both isolated edits and every burst tick, with no "settle"
upgrade needed at all (there's no higher-resolution version of this
array to catch up to, since the Viewer never reads it). `_after_
recompute_skipped` (the old skip-only-mid-burst flag) is gone entirely,
replaced by this simpler always-cheap approach. `test_sample_image_panel.py`
rewritten accordingly (11 tests, including a real regression guard using
a 2200x100 test image sized specifically so the scopes-capped shape is
unambiguously different from both full resolution and the
`EDIT_DOWNRES_FACTOR` shape). Full suite (430 tests) and all 8 standalone
GPU test scripts re-confirmed passing.

**SECOND FOLLOW-UP (2026-08-10, same day): still "pretty slow" after the
above -- found two more real, independent bugs by actually profiling
(temporary `[PERF]` timing added throughout the paint/render/recompute
path, then removed once both were confirmed fixed) rather than guessing
further:**

1. **The real remaining bottleneck**: `wipe_compare_widget._SideState.
   ensure_texture` gated texture re-upload and GPU-chain re-render behind
   ONE combined `(array_fp, stack_fp)` fingerprint. Since a live slider
   drag changes the STACK fingerprint on every tick, that combined check
   was also true every tick -- meaning the completely UNCHANGED native-
   resolution source array was being deleted and re-uploaded to the GPU
   from scratch (`glTexImage3D`... `glTexImage2D` -- ~130MB of PCIe
   traffic for a 4K source) on every single edit, not just re-running the
   actual (cheap) color chain against the already-uploaded texture. This
   -- not the color math, not anything CPU-side -- was the dominant
   remaining cost. Fixed by tracking `uploaded_array_fingerprint` and
   `applied_stack_fingerprint` as two INDEPENDENT checks: re-upload only
   when the array itself changes, re-render only when the stack changes.
   New `test_wipe_compare_widget_gpu_state.py` (3 tests, real GL context)
   is a direct regression guard: confirms editing the stack reuses the
   native texture object (verified by pixel content, not GL object ID --
   a freed ID can be recycled by the driver, a real gotcha hit while
   writing these tests) while still producing different graded output.
2. **A LogC-range grading cube fell back to CPU entirely**: found via a
   temporary diagnostic print in `is_stack_gpu_eligible` explaining
   exactly why a stack was ineligible (removed once diagnosed) -- a real
   test cube (`LogC_VisionLook2_v170.cube`) had domain `[[0,0,0],
   [1.79,1.79,1.79]]`, not `[0,1]`, hitting Stage 4's own documented
   (but, it turned out, unnecessarily narrow) SCOPE LIMIT. LogC-range
   domains are common for grading cubes, not an edge case -- widened
   `gpu_lut_shader.py` to support any IMPLICIT domain (any `[min, max]`
   pair, per `colour.LUT3D.is_domain_explicit()`), by wrapping OCIO's own
   generated `Lut3DTransform` shader in one extra function that does the
   same per-channel linear remap into `[0,1]` `colour.LUT3D.apply()`
   itself does before indexing -- baked as GLSL literal constants (a
   different domain always means a different `lut` object, hence a
   different cache entry, anyway). Validated the same way as everything
   else in this project: a real GPU render through the wrapped shader
   matched `colour.LUT3D.apply()` with this exact domain to 4.7e-4 (same
   tolerance as the default-domain case). Only a genuinely EXPLICIT
   (per-sample) domain remains excluded now, a much narrower real gap
   than "anything but [0,1]."

Full suite (430 tests) and all 9 standalone GPU/widget test scripts
(`test_wipe_compare_widget_gpu_state.py` now included) re-confirmed
passing after both fixes, temporary diagnostics fully removed.

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

**DONE (2026-08-09), reordered ahead of Stage 4 per Tom** (a live
performance complaint -- "adjusting a CDL behind an ACES DRT" was still
slow even after Stage 3's caching -- made clear the CPU path itself, not
compile cost, was the remaining bottleneck for the actual Viewer). Tom's
explicit choice: true GPU-resident rendering directly on the Viewer's own
GL surface (not "GPU-compute then hand back to the existing CPU-pixmap
pipeline"), plus Lanczos scaling instead of bilinear, plus keeping
scale-to-screen a single concatenated operation regardless of zoom. The
band-aid mentioned above (zoom-triggered resolution upgrade) was removed
entirely as part of this -- superseded by loading natively at all times
and letting `WipeCompositor`'s own Lanczos resample handle scale-to-
screen, see "What does NOT change" below for what that band-aid's removal
did and didn't touch.

- **`gpu_stack_renderer.py`** (new) -- `GpuStackRenderer.render_chain`
  runs an entire eligible `Stack` (see `is_stack_gpu_eligible`: no
  fork_split/fork_merge at all, every enabled stage's kind in `{cdl,
  aces_drt, colorspace}`) as a linear ping-pong chain of Stage 3's
  compiled programs, one pass per enabled stage, at native resolution,
  never touching the CPU.
- **`gpu_lanczos_compositor.py`** (new) -- a separable 2-pass Lanczos-3
  resampler (`LanczosResampler`) plus `WipeCompositor`, which resamples
  each side of the wipe independently into widget space and composites
  wipe divider / gutter background / placeholder text entirely on the
  GPU. Validated against a hand-written float64 numpy reference of the
  same algorithm (not PIL -- PIL's uint8 pipeline clips Lanczos ringing,
  causing a false ~0.32 divergence that was investigated and ruled out
  as a real bug).
- **`wipe_compare_widget.py`** rewritten onto `QOpenGLWidget`'s real
  `paintGL`, owning one `GpuStackRenderer` + one `WipeCompositor` per
  widget; `sample_image_panel.py` now hands arrays (+ an optional
  GPU-eligible `Stack` to grade) instead of pre-rendered `QPixmap`s.
- **Known, accepted limitation**: the Lanczos resampler uses a fixed
  6-tap kernel support per axis, correct for magnification/mild
  minification but aliasing under heavy downscale (small-in-viewer,
  zoomed-out case) -- a proper minification filter needs to widen its
  support by roughly `1/scale`, not yet implemented. Confirmed as a real,
  user-visible artifact in practice (color remains correct, only spatial
  aliasing), not just a theoretical gap. Left as an open follow-up below.
- **Real hardware GLSL version conflict, found and fixed**: the app's
  actual (unforced) `QOpenGLWidget` context on macOS is OpenGL 2.1
  Compatibility Profile / GLSL 1.20 (confirmed via direct `GL_VERSION`/
  `GL_SHADING_LANGUAGE_VERSION` query on Apple M1: `2.1 Metal - 88` /
  `1.20`) -- Stage 2/3's `#version 400`/Core-Profile-shaped shaders
  failed outright against it, and forcing Core Profile 4.1 (per-widget or
  app-wide) broke `pyqtgraph`'s Graph panel (`AA_ShareOpenGLContexts`
  context-sharing failure, then `ShaderValidationError: No vertex array
  object bound` -- `pyqtgraph`'s own GL code doesn't bind a VAO, which
  Compatibility Profile tolerates but Core Profile doesn't). Tom's
  decision: rewrite the entire pipeline to GLSL 1.20/Compatibility
  Profile rather than fight the platform default -- done across
  `gpu_shader_prototype.py`/`gpu_lanczos_compositor.py` (real VBOs
  instead of the `gl_VertexID`-only fullscreen-triangle trick, `varying`/
  `texture2D()`/`gl_FragColor` instead of `in`/`out`/`texture()`, an
  explicit `uTargetSize` uniform instead of `textureSize()`), verified
  against every existing GPU test afterward with no regressions.
- **A real, reproduced `GL_INVALID_FRAMEBUFFER_OPERATION` bug, found via
  the live app's own log rather than guessing**: several GL helper
  functions (`GpuStackRenderer._render_stage_pass`,
  `LanczosResampler.resample_into`,
  `gpu_shader_prototype.create_rgba32f_render_target`) unconditionally
  rebound framebuffer `0` as an assumed-safe "reset" when done -- but
  inside a `QOpenGLWidget`'s `paintGL`, framebuffer `0` is NOT Qt's real
  render target (Qt manages its own internal FBO invisibly), so this
  corrupted the ambient GL state for whatever ran next, surfacing to Tom
  as "the image goes black often when I make a change to a stack entry."
  Fixed by capturing the ambient framebuffer binding
  (`glGetIntegerv(GL_FRAMEBUFFER_BINDING)`) once at each entry point and
  restoring THAT instead of `0`. Confirmed fixed against the live app's
  own log (`/tmp/lutier_launch.log`), not just the isolated unit tests
  (which used their own explicit offscreen render targets and so never
  exercised the real bug).
- **A second, separate performance bug, found and fixed**: even after the
  Viewer itself went GPU-resident, `sample_image_panel.py`'s
  `refresh_preview()` was still running the full CPU/OCIO recompute
  (`apply_stack_to_image_array`) on every single edit tick, for
  `ScopesPanel`'s benefit -- meaning a GPU-eligible stack's Viewer got
  faster but the UI thread was still paying the old CPU cost regardless,
  explaining "the performance isn't improved much." Fixed: a GPU-eligible
  stack's burst ticks (`_editing_settle_timer.isActive()`) now skip the
  CPU recompute entirely (tracked via the new `_after_recompute_skipped`
  flag) rather than merely downres-ing it -- Scopes goes stale for the
  duration of a burst and catches up with one real, full-resolution
  recompute once `_on_editing_settled` fires. The existing CPU-side
  downres path (`EDIT_DOWNRES_FACTOR`) is unchanged and still applies to
  GPU-INeligible stacks (e.g. any stack with a fork, a `lut` stage with a
  non-default domain, or -- as of Stage 7 -- a `printer_lights` stage
  whose working_colourspace has no unambiguous native-linear sibling),
  which still need a real CPU-computed "after" array for the Viewer
  itself.

### Stage 6 -- Fork regions on GPU

`fork_split`/`fork_merge` (two independent branches blended back
together, see `stack.py`'s "STACK FORKING" docstring) need two render
targets (one per branch) and a blend pass, rather than a single linear
shader chain. Deliberately last, since it's the one structurally
different case in `_run_stages` and shouldn't block the common
single-chain case from shipping first.

### Stage 7 (2026-08-11) -- `printer_lights` GPU path -- DONE

`printer_lights.py`'s own math (decode to linear -> per-channel R/G/B
gain -> encode from linear, see that module's docstring) has no single
OCIO transform with a live-editable gain: `ocio.MatrixTransform` (the
natural fit for a diagonal per-channel gain) has NO dynamic-property
support at all, confirmed by probing `PyOpenColorIO` directly (unlike
GradingPrimary/GradingRGBCurve/ExposureContrast/GradingTone, which all
expose `makeXDynamic()`). So `gpu_printer_lights_shader.py` composes TWO
separately-extracted OCIO shaders (decode: working_colourspace -> its
native-linear sibling; encode: the reverse) with a small hand-written
GLSL wrapper in between that multiplies by a real `uPrinterLightsGain`
uniform -- same "OCIO for the reference math, a hand-written uniform for
the continuously-live-edited part" split `gpu_cdl_shader.py` already
established for CDL.

Composing two independently-extracted OCIO shaders into one GLSL program
needed `GpuShaderDesc.setResourcePrefix` (confirmed present in
`PyOpenColorIO`, added as an optional param to
`gpu_shader_prototype._extract_ocio_glsl`) to keep each half's internal
symbols from colliding -- probed directly that neither half needs a
texture (0 textures, 0 uniforms on both sides for a real ACEScct<->ACEScg
pair), so this module's own texture-binding gap (it doesn't wire one up)
never triggers for any working space in this app's builtin configs today,
but `is_printer_lights_gpu_eligible` checks for it anyway and falls back
to CPU rather than assume.

Fingerprinted on `(config_name, working_colourspace)` ONLY -- same
"shader STRUCTURE doesn't change when a slider moves" reasoning
`gpu_ocio_dynamic.py` uses for the dynamic-property kinds, just via a
plain uniform instead. GPU eligibility requires PyOpenColorIO (no GPU
equivalent of `printer_lights.py`'s own colour-science CPU fallback) and
an unambiguous native-linear sibling for the working space
(`aces_drt._find_native_linear_colourspace`'s own "raise rather than
silently guess wrong" rule) -- either gap falls the whole stack back to
CPU, same as any other GPU-ineligible case.

**Real bug found and fixed during this work:** the first version of
`is_printer_lights_gpu_eligible` called `gsp.compile_ocio_program` (a
real `glCreateShader`/`glCompileShader` call) to answer the eligibility
question, and was called from `is_stack_gpu_eligible` with no guarantee
of a current OpenGL context -- a real, reproduced SIGSEGV (exit code 139)
when exercised standalone with no context set up first, the same class of
"uncontexted GL call" crash this app already hit once elsewhere this
session (see `WipeCompareWidget.paintGL`'s own docstring on the Shiboken
crash). Fixed by splitting the GL-context-FREE half (config/native-linear
lookup, OCIO's own shader-info codegen -- pure string generation, no real
GL driver call) from the GL-COMPILE half, and having
`is_printer_lights_gpu_eligible` call only the former.

Validated (`test_gpu_printer_lights_shader.py`, `test_gpu_stack_renderer.py`):
GPU vs CPU max|delta| 6e-6 for a single `printer_lights` stage (ACEScct),
7.1e-5 chained with an ACES DRT stage, 0.0 exact for zero points (a pure
decode/encode round trip) -- both a log working space (ACEScct) and an
already-linear one (ACEScg, where decode/encode is a same-space no-op
conversion) covered.

### Stage 9 (2026-08-11) -- Task #31: HDR `aces_drt` Viewer milkiness -- FIXED

Reported symptom: "HDR output color spaces in ACES DRT operator broken.
very milky in viewer. scope seems correct" -- later clarified as "almost
1 uniform gray with no details," not a monitor/EDR-hardware color-
management issue.

**Root cause, confirmed directly:** every SDR display/view this app's own
GPU tests happened to exercise needs ZERO GPU textures from OCIO's own
codegen (pure baked-constant curve/matrix math). Every HDR display/view
(`ST2084-P3-D65 - Display`, `Rec.2100-PQ - Display`, `Rec.2100-HLG -
Display` -- confirmed directly, all three) needs ONE real GPU texture: a
4096x17 single-channel 2D texture OCIO packs its internal 1D shaper LUT
into. `gpu_stack_renderer.py`'s `aces_drt` handling (shared with
`colorspace` via `gpu_shader_cache.OcioGpuStageCache`, which has no
texture concept at all) never bound ANY texture for this stage kind, for
ANY display/view -- so an HDR view's shader silently sampled an unbound
(all-zero) texture, producing severely compressed, nearly-uniform-gray
output. Confirmed on a real image: GPU min/max/mean 0.204/0.518/0.467 vs.
CPU's correct 0.0/0.694/0.329 for the exact same stack -- exactly the
reported symptom, and exactly why Scopes (CPU-computed) looked correct
while the Viewer (GPU-rendered) didn't: two entirely separate code paths,
only one of them missing this bind.

**Fix:** a new, dedicated `gpu_aces_drt_shader.AcesDrtGpuCache` (NOT
sharing `OcioGpuStageCache` with `colorspace` anymore, which stays
exactly as it was -- colorspace transforms have never been observed to
need a texture) that extracts and uploads whatever 2D textures (zero or
more) a given `(config, working_colourspace, display, view)` fingerprint
actually needs, via a new `gpu_shader_prototype.
_extract_ocio_glsl_with_2d_textures` (returns real texture values/
dimensions/sampler names, not just counts). `gpu_stack_renderer.py`'s own
`_set_uniforms_for_stage` now has a real `"aces_drt"` branch binding each
texture starting at `GL_TEXTURE1` (unit 0 is always `uInput`) -- a no-op
loop for every SDR display/view, so this fix costs nothing for the
common case.

Validated (`test_gpu_aces_drt_shader.py`): all three HDR displays match
CPU exactly (max|delta| 7.7e-5 for ST2084-P3-D65, 2e-6/4e-6 for
Rec.2100-PQ/HLG on a real image), and an explicit dynamic-range assertion
(not just a pixel-delta check) guards against a regression that happens
to average out back to a small delta while still being visually
compressed.

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
- Whether real cross-restart compiled-BINARY caching (not just the
  source caching Stage 3 shipped) is worth building -- depends on
  `glGetProgramBinary`/`glProgramBinary` actually round-tripping reliably
  under this app's GL context on macOS, which hasn't been checked. Only
  worth doing if the per-process GL compile cost (Stage 3's benchmark:
  order of several-to-twenty ms per distinct shader, apparently much of
  it deferred to first draw call under Apple's OpenGL-over-Metal driver)
  is shown to actually matter once Stage 5 measures real interactive use,
  not assumed upfront.

## Follow-ups discovered along the way (not blocking, not forgotten)

- **Add `ocio.GradingPrimaryTransform` as its own, separate, ADDITIONAL
  stage kind** (not a CDL replacement) -- per Tom, while deciding Stage
  3's CDL uniform question: CDL's role in this app is specifically to
  support standard/interchange ASC CDL adjustments, not to be the
  primary/preferred grading tool. `GradingPrimaryTransform` is OCIO's
  own newer, real-time-editing-oriented grading transform (supports
  `makeDynamic()` for genuine GPU uniform-driven editing, unlike
  `CDLTransform`) with a different parameterization (pivot/contrast/
  gamma rather than slope/offset/power) -- a real candidate as a new
  `STAGE_KINDS` entry in `stack.py` alongside `cdl`, giving users a
  second, more modern grading tool without touching CDL's own exact,
  well-known formula. Out of scope for the GPU project itself (this is
  an app feature addition, not an acceleration technique) -- worth its
  own task whenever there's room for it.
- **Proper Lanczos minification filtering** -- `gpu_lanczos_compositor.py`'s
  `LanczosResampler` uses a fixed 6-tap kernel support per axis regardless
  of scale, which is correct for magnification/mild minification but
  aliases under heavy downscale. Confirmed by Tom in practice: "when the
  image is very small in the viewer and not zoomed in it's very crunchy
  and there are lots of scaling artifacts" (color remained correct --
  purely a spatial-filtering gap). A real fix widens the kernel's support
  by roughly `1/scale` when `scale < 1` (i.e. sample more source texels
  per output pixel, proportionally to how much the image is being shrunk)
  -- not yet implemented.

## What does NOT change

- `bake_stack_to_lut3d` and every export format writer -- exact CPU path,
  untouched, remains the one source of truth for shipped output.
- `lut_viewer_3d.py`'s 3D point-cloud/cage view -- unrelated concern
  (geometry, not image rendering).
- The Scopes panel (histogram/waveform/vectorscope) -- reads from
  whatever array the preview already produced; not in scope unless a
  later session decides scope computation itself should also move to
  GPU (not assumed here).
