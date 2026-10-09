# OCIO Tier 1 Grading Operators — Plan (2026-08-09)

## Why

The GPU acceleration project (`GPU_ACCELERATION_PLAN_2026-08-08.md`) left a
backlog item: CDL's role in this app is standard/interchange ASC CDL
support, not the primary grading tool, and OCIO ships a purpose-built,
real-time-editing-oriented family of "Grading" transforms instead. Tom
asked to survey the full OCIO transform catalog; the resulting Tier 1 —
`GradingPrimaryTransform`, `GradingRGBCurveTransform`,
`GradingToneTransform`, `ExposureContrastTransform` — are the four judged
both GPU-viable and genuinely useful as new Stack stage kinds. This plan
covers all four in one push, per Tom's choice.

## Ground truth: what's already confirmed (real, tested, not assumed)

Before committing to an architecture, each of the four was validated
directly against real OCIO 2.5.2 + a real GPU render (same
`gpu_shader_prototype._make_offscreen_gl_context()` this project always
uses for "measure the real thing, don't extrapolate" checks) — not
assumed from documentation:

| Transform | Style tested | max\|GPU − CPU\| |
|---|---|---|
| `GradingPrimaryTransform` | `GRADING_LOG` | 1.2e-5 |
| `GradingRGBCurveTransform` | `GRADING_LOG` | 0.0 (bit-exact) |
| `ExposureContrastTransform` | linear, all 3 props dynamic | 2.6e-5 |
| `GradingToneTransform` | `GRADING_LOG` | 1.2e-7 |

All within float32 noise. `DynamicProperty` mutate-then-reread cost
benchmarked at ~0.065 ms/update (timeit, 2000 iterations) — cheap enough
for continuous slider drags, nowhere near the cost that caused GPU Stage
5's "performance isn't improved much" bug.

**The big finding, and the reason this plan differs from CDL's precedent**:
unlike `CDLTransform` (which OCIO's default codegen bakes as literal GLSL
constants, forcing the hand-written `gpu_cdl_shader.py`), all four Tier 1
transforms — when `makeDynamic()`'d before building the processor — emit
**OCIO's own generated GLSL with real named uniforms** (e.g. `uniform vec3
ocio_grading_primary_brightness;`). No hand-writing needed for any of
these four. The GPU-side work is mostly ONE generic, kind-agnostic
mechanism (compile OCIO's dynamic GLSL once per (kind, style), push its
current uniform values every edit), reused across all four — not four
bespoke shader files like CDL required.

Confirmed per-kind shapes (via direct `GpuShaderDesc.getUniforms()`
inspection on a `makeDynamic()`'d transform):

- **`GradingPrimaryTransform`**: 10 uniforms — `brightness`/`contrast`/
  `gamma` as `vec3` (r,g,b — note: NOT r,g,b,master; OCIO folds the
  master component in before generating the uniform), `pivot`/
  `pivotBlack`/`pivotWhite`/`clampBlack`/`clampWhite`/`saturation` as
  `float`, `localBypass` as `bool`. No textures. One
  `DYNAMIC_PROPERTY_GRADING_PRIMARY` handle, value type `GradingPrimary`
  (itself built from `GradingRGBM(r, g, b, master)` sub-structs for
  brightness/contrast/gamma/offset/exposure/lift/gain, plus scalar
  pivot/saturation/pivotBlack/pivotWhite/clampBlack/clampWhite fields —
  which of these fields actually matter depends on `GradingStyle`, see
  Open Questions).
- **`ExposureContrastTransform`**: 3 uniforms — `exposureVal`/
  `contrastVal`/`gammaVal`, each `float`/`double`. **Different shape from
  the other three**: this transform has THREE INDEPENDENT dynamic flags
  (`makeExposureDynamic()`/`makeContrastDynamic()`/`makeGammaDynamic()`),
  each its own `DynamicPropertyType`
  (`DYNAMIC_PROPERTY_EXPOSURE`/`_CONTRAST`/`_GAMMA`) with its own
  `DynamicProperty.getDouble()/setDouble()` — not one combined property
  like the other three. The generic push mechanism (Stage 1 below) must
  handle "a transform with multiple independent dynamic-property
  handles," not assume exactly one per stage.
- **`GradingToneTransform`**: 32 uniforms — 5 tone ranges (`blacks`/
  `shadows`/`midtones`/`highlights`/`whites`), each with R/G/B/M (as
  `GradingRGBMSW`, includes `start`/`width` too) as 6 floats, plus
  `sContrast` (float) and `localBypass` (bool). No textures. One
  `DYNAMIC_PROPERTY_GRADING_TONE` handle, value type `GradingTone`.
- **`GradingRGBCurveTransform`**: 5 uniforms, all FIXED-SIZE ARRAYS —
  `knotsOffsets[8]`/`knots[120]`/`coefsOffsets[8]`/`coefs[360]` (int/float
  vectors) plus `localBypass` (bool). No textures — a fully self-contained
  GLSL B-spline evaluator (real `for` loop over knot count, same
  "variable-bound loops work fine on this hardware" fact the Lanczos
  minification fix already leaned on). One
  `DYNAMIC_PROPERTY_GRADING_RGBCURVE` handle, value type `GradingRGBCurve`
  (four `GradingBSplineCurve` sub-curves: red/green/blue/master).
  **Real API gotcha, confirmed by testing, not assumed**: mutating
  `curve.master.getControlPoints()`'s returned items in place does NOT
  persist — `getControlPoints()` returns copies. The working pattern is
  rebuilding the whole curve via the flat-sequence constructor overload:
  `ocio.GradingBSplineCurve([x0, y0, x1, y1, ...])`, then assigning it
  back (`curve.master = new_curve`) before `setGradingRGBCurve(curve)`.
  This must be the documented pattern from the start of Stage 5 below,
  not rediscovered mid-implementation.

Confirmed generic uniform-push dispatch (covers every uniform type seen
across all four kinds above — no fifth type exists in this survey):

```python
for name, data in shader_desc.getUniforms():
    loc = gl.glGetUniformLocation(program, name)
    t = data.type
    if t == ocio.UNIFORM_DOUBLE:
        gl.glUniform1f(loc, data.getDouble())
    elif t == ocio.UNIFORM_BOOL:
        gl.glUniform1i(loc, 1 if data.getBool() else 0)
    elif t == ocio.UNIFORM_FLOAT3:
        gl.glUniform3f(loc, *data.getFloat3())
    elif t == ocio.UNIFORM_VECTOR_INT:
        arr = np.array(data.getVectorInt(), dtype=np.int32)
        gl.glUniform1iv(loc, len(arr), arr)
    elif t == ocio.UNIFORM_VECTOR_FLOAT:
        arr = np.array(data.getVectorFloat(), dtype=np.float32)
        gl.glUniform1fv(loc, len(arr), arr)
```

### Existing integration points (extend, don't rearchitect)

- **CPU dispatch**: `stack.STAGE_KINDS` / `make_X_stage()` factories /
  `stack._apply_stage_to_array`'s `stage.kind ==` dispatch — already a
  flat, additive dispatch table (`cdl.py`/`aces_drt.py`/
  `colorspace_convert.py` each expose an `apply_X(array, ...)` function
  built on a `@lru_cache`d `_get_cpu_processor`-shaped helper; a new
  `grading_primary.py` etc. mirrors this exactly).
- **GPU dispatch**: `gpu_stack_renderer.GPU_ELIGIBLE_KINDS` /
  `is_stack_gpu_eligible` / `_program_for_stage` / `_set_uniforms_for_stage`
  — same story, additive.
- **Export path**: `bake_stack_to_lut3d` and `apply_stack_to_image_array`
  both go through `_apply_stage_to_array`'s single dispatch — a new kind
  is automatically covered by both once that one function knows about it,
  per that module's own docstring guarantee. No separate export-path work.
- **UI**: `stack_panel.py`'s `self.inspector_stack` (`QStackedWidget`),
  one page per stage kind, populated/written back via a `stage.kind ==`
  dispatch (see CDL's own page for the existing shape: wheel + fine
  spinbox pairs per SOP field, checkbox/combo for style/enables).

### The one genuinely new problem: caching shape

- CDL: one hand-written shader, `CdlGpuProgram` caches exactly one
  compiled program, forever (its uniforms cover the whole param space).
- ACES DRT / colorspace: `OcioGpuStageCache`, fingerprinted on
  CONFIG-shaped params (working_colourspace/display/view, or src/dst) —
  these change rarely (a combo-box pick) and the compiled shader
  STRUCTURE genuinely differs per config.
- Tier 1: the shader TEXT/STRUCTURE is constant for a given (kind, style)
  pair regardless of the live slider values (that's what `makeDynamic()`
  buys us, confirmed above) — so caching must be fingerprinted on
  **(kind, style) only**, never on live parameter values, or we'd
  recompile on every edit and reintroduce exactly the cost GPU Stage 3
  eliminated for CDL/ACES DRT.
- BUT Tier 1's cache entry needs to hold MORE than a compiled GL program:
  it needs the live `GpuShaderDesc` (and, for `ExposureContrastTransform`,
  potentially multiple `DynamicProperty` handles) that generated it, kept
  alive together, since THAT object is what gets mutated on every edit to
  produce fresh uniform values. This is a new cache shape — not a
  drop-in reuse of `CdlGpuProgram` or `OcioGpuStageCache` as they exist
  today, though it can live alongside them in `gpu_shader_cache.py`.

### No existing curve-editor widget — confirmed, not assumed

`lut_viewer_3d.py`'s Graph panel (`app_logic.compute_yrgb_curve`) plots a
**read-only** Y/R/G/B response curve of the current bake — a static
visualization, not an interactive control. There is nothing to reuse for
`GradingRGBCurveTransform`'s UI; it needs a genuinely new interactive
curve-editing widget (draggable control points, add/remove points, live
feed into the `GradingBSplineCurve` flat-sequence constructor pattern
above). This is the single largest piece of new UI in this whole plan.

## Staged plan

### Stage 1 — Shared dynamic-uniform GPU plumbing (new module)

A new `gpu_ocio_dynamic.py`: the generic uniform-push dispatcher above,
plus a cache class (working name `OcioDynamicGpuStageCache`) holding
`(compiled program, GpuShaderDesc, {DynamicPropertyType: DynamicProperty})`
per (kind, style) fingerprint — the tuple-of-handles form is needed
specifically for `ExposureContrastTransform`'s multiple-independent-
properties case, but works uniformly for the single-property kinds too
(a dict with one entry).

Deliverable: a standalone validation script (mirroring
`gpu_shader_prototype.py`'s original Stage 2 role) proving the mechanism
for all four kinds against `getDefaultCPUProcessor()`, turning the ad hoc
checks in this plan's "Ground truth" section into a permanent, repeatable
test — this must land and pass before any single operator's Stack/UI
integration begins, since all four depend on it.

**DONE (2026-08-09).** `gpu_ocio_dynamic.py` -- `build_dynamic_ocio_shader`
(compiles a `makeDynamic()`'d transform's GLSL via `cdl._get_raw_config()`,
returns `(shader_text, shader_desc)` with `shader_desc` kept alive),
`push_dynamic_uniforms` (the 5-branch `UniformDataType` dispatch from
"Ground truth" above, now permanent code), and
`OcioDynamicGpuStageCache` (fingerprint -> `{program, shader_desc,
dynamic_properties dict}`, `dynamic_property_types` passed at
construction so `ExposureContrastTransform`'s 3-handle shape and the
other three's 1-handle shape are the same code path). `test_gpu_ocio_
dynamic.py` (7 tests, excluded from `pytest -q` via `conftest.py`'s
`collect_ignore` like every other real-GL-context GPU test) confirms all
four kinds against `getDefaultCPUProcessor()` on the real GPU
(max\|delta\|: 1.2e-5 / 0.0 / 2.6e-5 / 1.2e-7 -- matching this plan's own
ad hoc numbers exactly) plus cache-reuse, per-style cache separation, and
`clear()` behavior. Full suite (401 tests) re-confirmed passing.

### Stage 2 — `GradingPrimaryTransform` end to end

- `grading_primary.py` (CPU apply, mirrors `cdl.py`'s shape).
- `stack.py`: `"grading_primary"` added to `STAGE_KINDS`,
  `make_grading_primary_stage(...)`, dispatch in `_apply_stage_to_array`.
- `gpu_stack_renderer.py`: added to `GPU_ELIGIBLE_KINDS`, wired via
  Stage 1's plumbing.
- UI: new inspector page — sliders per RGB+master for
  brightness/contrast/gamma (or lift/gamma/gain, depending on style
  choice — see Open Questions), pivot/saturation, reusing CDL's existing
  wheel+spinbox pattern where it fits.
- Tests: CPU-vs-OCIO-reference, GPU-vs-CPU, Stack integration, UI —
  mirroring the existing `cdl.py`/`gpu_cdl_shader.py` test shapes.

**DONE (2026-08-09).** Style exposed as a stage param/dropdown (per the
"Open Questions" note below, decided rather than left open): `"log"`
(default, brightness/contrast/gamma — matches `printer_lights.py`'s own
ACEScct-default working-space convention), `"lin"` (offset/exposure/
contrast), `"video"` (lift/gamma/gain). Every RGBM field is stored in
`Stage.params` regardless of active style (harmless — OCIO's own
processor/shader simply never reads a field a given style doesn't use,
confirmed via the GLSL dump), so switching styles never loses whatever
was dialed into the other fields.

- **`grading_primary.py`** (new) — mirrors `cdl.py`'s shape exactly:
  `apply_grading_primary`/`build_grading_primary_transform`/
  `build_grading_primary_value`, `AVAILABLE_STYLES`/`list_available_
  styles()`, reuses `cdl._get_raw_config()` (bare config, no named
  colorspace needed). `test_grading_primary.py` (6 tests): identity is a
  no-op for every style (including already-out-of-range input — no CDL-
  style intermediate-clamp gotcha here), matches a by-hand-built
  `ocio.GradingPrimaryTransform` exactly (catches field-mapping bugs),
  contrast direction sanity check, unknown-style error.
- **`stack.py`** — `"grading_primary"` added to `STAGE_KINDS`,
  `make_grading_primary_stage(...)`, dispatch in `_apply_stage_to_array`.
  Two new `test_stack.py` tests (bake matches direct apply; composes
  after a `lut` stage).
- **`session_io.py`** — save/load entries for all 10 fields (style +
  7 RGBM tuples + pivot + saturation). One new `test_session_io.py` test
  (round-trips through plain JSON, rebaked output matches exactly).
- **`gpu_stack_renderer.py`** — `"grading_primary"` added to
  `GPU_ELIGIBLE_KINDS`; `_grading_primary_cache` (a
  `gpu_ocio_dynamic.OcioDynamicGpuStageCache`, fingerprinted on style
  only, per Stage 1's caching design); `_program_for_stage`/
  `_set_uniforms_for_stage` both extended (the latter converted from
  `@staticmethod` to an instance method, needed to reach the new cache).
  Two new `test_gpu_stack_renderer.py` tests: a single grading_primary
  stage matches CPU (max\|delta\|=0.0), and a grading_primary → ACES DRT
  chain matches CPU (max\|delta\|=2.3e-5) — confirming the dynamic-
  uniform mechanism chains correctly through the same ping-pong
  `render_chain` the hand-written/cached kinds use.
- **`stack_panel.py`** UI — new inspector page: style combo, one R/G/B/M
  spinbox row per RGBM field (plain spinboxes, no color-balance wheel
  like CDL's — a deliberate scope decision, see the module's own
  `GRADING_PRIMARY_FIELD_SPECS` comment; wheel-based editing for this
  stage is a possible future refinement, not required for a functional
  first version), with only the 3 fields the active style actually uses
  visible at once (toggled on style change, values preserved underneath
  when hidden). Pivot/Saturation always visible. "Add Grading Primary"
  menu action, dynamic row label (`"Grading Primary (Log)"` etc.,
  matching CDL's/colorspace's "the row tells you what it actually does"
  convention), Alt+hover info-card branch. No dedicated UI test file —
  matches this codebase's existing precedent (CDL/ACES DRT/colorspace's
  own inspector pages have none either, per `test_stack_panel.py`'s own
  docstring: "deliberately narrow"); wiring double-checked against the
  `colorspace` stage's exact `_refreshing_inspector`-guard pattern.
- Full suite (410 tests) and all 6 standalone GPU test scripts re-
  confirmed passing; live app relaunched clean.

### Stage 3 — `ExposureContrastTransform` end to end

Same shape as Stage 2. Simplest UI of the four: exposure/contrast/gamma
sliders (pivot is a fixed 0.18 mid-gray reference in OCIO's own default,
worth confirming whether it should be user-exposed at all).

**DONE (2026-08-09).** Pivot/logExposureStep/logMidGray decided NOT
user-facing (resolving the "Open Questions" note below): confirmed these
three are the ONLY non-dynamic properties on this transform (only
`DYNAMIC_PROPERTY_EXPOSURE`/`_CONTRAST`/`_GAMMA` exist), so OCIO bakes
them as literal GLSL constants even in a `makeXDynamic()`'d shader --
exposing them as sliders would force a shader recompile on every edit,
reintroducing exactly the per-tick-cost problem this project fixed.
OCIO's own defaults (pivot=0.18) are used unconditionally.

- **`exposure_contrast.py`** (new) — mirrors `grading_primary.py`'s
  shape: `apply_exposure_contrast`/`build_exposure_contrast_transform`,
  `AVAILABLE_STYLES` (`"linear"` default/`"log"`/`"video"`, mapping to
  `EXPOSURE_CONTRAST_LINEAR`/`_LOGARITHMIC`/`_VIDEO`). `test_exposure_
  contrast.py` (5 tests): identity no-op every style, by-hand-built-
  transform field-mapping check, `+1` stop doubles mid-gray (`pow(2,1)`),
  unknown-style error.
- **`stack.py`/`session_io.py`** — `"exposure_contrast"` added to
  `STAGE_KINDS`, `make_exposure_contrast_stage(...)`, CPU dispatch, save/
  load entries (style + 3 scalars, much smaller than grading_primary's
  10 fields). Two new `test_stack.py` tests, one new `test_session_io.py`
  round-trip test.
- **`gpu_stack_renderer.py`** — `"exposure_contrast"` added to
  `GPU_ELIGIBLE_KINDS`; `_exposure_contrast_cache` (fingerprinted on
  style only, same as grading_primary's cache). **The one real
  structural difference**: `_build_dynamic_exposure_contrast_transform`
  calls `makeExposureDynamic()`/`makeContrastDynamic()`/
  `makeGammaDynamic()` (three separate calls, three separate
  `DynamicPropertyType`s) instead of one `makeDynamic()` — exactly the
  multi-property shape Stage 1's `OcioDynamicGpuStageCache` was designed
  to handle (dict of handles, not a single one), now exercised for real
  by a second GPU-eligible kind, not just Stage 1's own isolated tests.
  Two new `test_gpu_stack_renderer.py` tests: single stage matches CPU
  (max\|delta\|=1.1e-5), and a grading_primary → exposure_contrast chain
  (two DIFFERENT dynamic-uniform kinds back to back) matches CPU exactly
  — confirms each stage's own cache entry/DynamicProperty handles stay
  correctly isolated per stage, not accidentally shared across kinds.
- **`stack_panel.py`** UI — simplest of the four Tier 1 inspectors: style
  combo + 3 plain scalar spinboxes (exposure/contrast/gamma), no RGBM
  fields, no field-visibility toggling needed. "Add Exposure/Contrast"
  menu action, dynamic row label showing live values (all 3 fit
  comfortably, unlike grading_primary's style-only label), Alt+hover
  info-card branch.
- Full suite (418 tests) and all 6 standalone GPU test scripts re-
  confirmed passing; live app relaunched clean.

### Stage 4 — `GradingToneTransform` end to end

Same shape. Largest slider-only UI of the four (5 ranges × up to 6
fields each) — likely needs a shared "tone range row" widget reused 5x
rather than a hand-laid-out page, to keep the inspector page a reasonable
size.

**DONE (2026-08-09).** Real difference from grading_primary/exposure_
contrast: EVERY field here (all 5 ranges' R/G/B/Master/Start/Width, plus
S-contrast) is a genuine live GPU uniform under one
`DYNAMIC_PROPERTY_GRADING_TONE` property — confirmed directly, no
CDL-style/exposure_contrast-style "exclude this field, it's baked as a
constant" carve-out needed anywhere in this one. Style decided fresh
(per the "Open Questions" note about not assuming it should match
grading_primary) — ended up the same three (`log`/`lin`/`video`, `log`
default) since `GradingToneTransform` shares `GradingStyle` with
`GradingPrimaryTransform`, but that was checked, not assumed.

- **`grading_tone.py`** (new) — `RANGE_KEYS`/`DEFAULT_RANGE_VALUES` read
  directly off a freshly constructed `ocio.GradingTone(GRADING_LOG)`
  rather than hand-transcribed (a real "measure the actual defaults"
  step, since e.g. `shadows` defaults to `width=0.0`, not an obviously
  guessable number). `apply_grading_tone`/`build_grading_tone_value`/
  `build_grading_tone_transform` mirror the established shape.
  `test_grading_tone.py` (5 tests): identity no-op every style, by-hand-
  built-transform field-mapping check (this one specifically catches a
  wrong range->attribute mapping, e.g. accidentally swapping midtones
  and highlights), a range-isolation sanity check (boosting midtones'
  gain moves a mid-value pixel more than a near-black/near-white one),
  unknown-style error.
- **`stack.py`/`session_io.py`** — `"grading_tone"` added to
  `STAGE_KINDS`, `make_grading_tone_stage(...)` (each of the 5 range
  kwargs defaults to `None` -> falls back to THAT range's own OCIO
  default, not a shared generic one), CPU dispatch, save/load entries
  (style + 5 range 6-tuples + scontrast). Two new `test_stack.py` tests,
  one new `test_session_io.py` round-trip test.
- **`gpu_stack_renderer.py`** — `"grading_tone"` added to
  `GPU_ELIGIBLE_KINDS`; `_grading_tone_cache` (fingerprinted on style
  only, single `makeDynamic()` call — same one-property shape as
  grading_primary, NOT exposure_contrast's three-property shape,
  confirmed before writing the code rather than assumed from the name
  alone). Two new `test_gpu_stack_renderer.py` tests: single stage
  matches CPU (max\|delta\|=0.0), grading_tone → ACES DRT chain matches
  CPU (max\|delta\|=3.3e-5).
- **`stack_panel.py`** UI — the "shared tone range row" this Stage's own
  plan note called for: one `QGroupBox` per range (Blacks/Shadows/
  Midtones/Highlights/Whites), each a single compact row of 6 labeled
  spinboxes (R/G/B/M/Start/Width), built from ONE loop over
  `grading_tone.RANGE_KEYS` rather than 5 hand-laid-out sections (30
  spinboxes total, would have been a lot of repeated code otherwise) +
  S-contrast + style combo. "Add Grading Tone" menu action, style-only
  row label (5 ranges is too much for one label, same reasoning as
  grading_primary's), Alt+hover info-card branch showing all 5 ranges'
  values plus S-contrast.
- Full suite (426 tests) and all 6 standalone GPU test scripts re-
  confirmed passing; live app relaunched clean.

### Stage 5 — `GradingRGBCurveTransform` end to end

The big one. Needs the new interactive curve-editor widget (see "No
existing curve-editor widget" above) before Stack/GPU wiring is even
useful to a user. Worth a short investigation into whether `pyqtgraph`
(already a dependency, used in `lut_viewer_3d.py`) has draggable-point
primitives suitable for this, before designing a fully custom
QPainter-based widget from scratch.

**BOOKMARKED DESIGN IDEA (2026-08-10, not yet scoped in, revisit when
this stage actually starts)**: rather than the curve starting as a flat
identity line, let the user pick a transfer function (sRGB, Rec.709, PQ,
HLG, a specific camera log curve, etc. -- `colour-science` already has a
broad library of these) and edit the curve *in that domain* -- an
explicit pre/post EOTF encode-then-decode wrap around the curve stage,
not OCIO's own `GradingRGBCurveTransform.bypassLinToLog` (which only
offers one fixed, generic built-in log curve, not a real choice of named
transfer functions). The curve itself stays a clean identity until
touched; switching the chosen transfer function only changes the
encode/decode wrapper, so it never requires refitting or reconciling
already-made control-point edits. Real added scope beyond the bare curve
tool (a transfer-function picker, the wrapping transform, its own
validation) -- intended design for when this stage is actually built,
not something to retrofit onto a simpler first version.

## Risks / open questions to revisit as stages land

- **Which `GradingStyle` (`GRADING_LOG`/`GRADING_LIN`/`GRADING_VIDEO`) is
  right for `GradingPrimaryTransform`/`GradingToneTransform`** — affects
  both the CPU apply function's fixed style choice and which of
  `GradingPrimary`'s fields are actually meaningful/shown in the UI (e.g.
  `GRADING_VIDEO` favors lift/gamma/gain, `GRADING_LIN` favors
  exposure/contrast/pivot). Should be decided per-operator against this
  app's typical working colourspace, not assumed to be `GRADING_LOG`
  just because that's what this plan's validation runs used.
- ~~Whether style should be user-facing (a dropdown, like CDL's
  `no_clip`/`asc` style choice) or fixed per stage kind~~ — **decided in
  Stage 2**: user-facing dropdown (`grading_primary.AVAILABLE_STYLES`),
  default `"log"`. Revisit the same question fresh for
  `GradingToneTransform` (Stage 4) rather than assuming it should match.
- `OcioGpuStageCache`'s existing disk-persisted-GLSL-SOURCE layer (from
  GPU Stage 3) should still apply here in principle — Tier 1's shader
  TEXT depends only on (kind, style), same as ACES DRT/colorspace — but
  the compiled-program-plus-live-DynamicProperty part is new/in-memory-
  only and needs its own lifecycle, not a retrofit of the existing two
  cache classes.
- ~~`ExposureContrastTransform`'s pivot/logExposureStep/logMidGray fields
  are NOT dynamic~~ — **decided in Stage 3**: not exposed in the UI at
  all, OCIO's own defaults used unconditionally. See that stage's own
  "DONE" note above for the full reasoning.
- Fully orthogonal to `GPU_ACCELERATION_PLAN_2026-08-08.md`'s still-
  pending Stage 4 (non-OCIO `lut` GPU path) and Stage 6 (fork regions) —
  no dependency either direction, can interleave freely.

## What does NOT change

- `cdl`/`aces_drt`/`colorspace`/`lut`/`printer_lights`/fork stages:
  untouched, same CPU+GPU treatment as today.
- `bake_stack_to_lut3d` (export path): covers new kinds for free once
  `_apply_stage_to_array` dispatches them — no separate work needed.
