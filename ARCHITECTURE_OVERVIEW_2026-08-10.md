# LUTier — Architecture Overview (2026-08-10)

A from-scratch orientation doc for someone new to this codebase (written
for a friend of the primary developer, also working with Claude Code) —
not a chronological changelog like `RUNNING.md`, and not a diff-since-
last-checkpoint like the `KNOWN_GOOD_SUMMARY_*.md` files. This explains
**how the app is put together**: the UI shell, the data model everything
else reads from, the color/OCIO pipeline, and — the newest and most
involved piece — the GPU acceleration architecture. Where a design
decision has a "why," this doc gives the why, not just the what.

If you want the blow-by-blow history behind any of this, `RUNNING.md` is
a long-form dev log (100+ numbered entries); `GPU_ACCELERATION_PLAN_
2026-08-08.md` and `OCIO_TIER1_GRADING_OPERATORS_PLAN_2026-08-09.md` are
the two planning documents the GPU work below was actually built from,
with real benchmark numbers and validation results at every stage.

## What this is

LUTier is a PySide6 (Qt6) desktop app for building and previewing
LUT-based color grades. The core idea: instead of a single fixed
pipeline, you build an ordered **Stack** of grading operations (a LUT, a
CDL, an ACES Output Transform, a plain color space conversion, or one of
several newer OCIO "Grading" operators), reorder/toggle/opacity-blend
them, preview the result live against a Sample/Reference image pair via
a wipe compare, inspect it with histogram/waveform/vectorscope/3D-cube
scopes, and export the baked result as a real `.cube`/`.clf`/`.3dl`/
`.cub`/`.spi3d`/`.csp` file.

## 1. UI shell

`main_window.py`'s `MainWindow` is a plain `QMainWindow` with **no
central widget** — every one of the app's 10 panels is a real
`QDockWidget` (via `_make_dock`), which is what gives the app its
Resolve/Nuke-like "drag any panel anywhere, tab panels together, pop one
out as a floating window" behavior for free from Qt's own docking
system, rather than hand-rolled panel management.

The 10 panels: **Browser** (file browser for LUTs/CDLs), **Image
Library**, **LUT Library**, **CDL Library**, **Stack Memories** (named
Stack snapshots), **Viewer** (`sample_image_panel.py` — the live
wipe-compare preview, see §4), **Stack** (the list of stages) +
**Parameters** (the selected stage's inspector — two docks sharing one
underlying `StackPanel`), **Scopes** (`scopes_panel.py`), **Graph**
(`lut_viewer_3d.py` — a pyqtgraph-based 3D point-cloud/wireframe-cage
view of the baked LUT, plus 1D YRGB response curves).

Shared panel chrome lives in `panel_frame.py` (`PanelFrame`) — the
hamburger-menu button, consistent border/padding, and the custom title
bar `_build_dock_title_bar` builds for every dock (a close button that
doesn't rely on Qt's own, since that couldn't be tied cleanly to
`QDockWidget`'s close signal the way a custom `QToolButton` can).
`style.py` holds the whole app's QSS stylesheet (dark theme, one
accent color, palette-driven so it adapts if the OS theme changes) —
`QGroupBox`, panel borders, stage-row cards, etc. are all styled from
one place, which is why **every inspector page in the Stack panel uses
exactly one outer `QGroupBox` and plain rows/labels inside it** — a
convention worth keeping if you add a new stage kind's UI (nesting a
second `QGroupBox` for sub-grouping was a real bug fixed this session:
it silently broke that one-box-per-page rule and looked out of place).

Two small but load-bearing UI utility modules: `spinbox_scrub.py`
(click-drag-horizontally-to-scrub numeric fields, Shift = fine
adjustment — used everywhere a `QDoubleSpinBox` appears in the Stack
panel) and `hover_inspector.py` (an app-wide `Alt`+hover overlay that
shows a Flame-style info card for whatever's under the cursor, via a
single `QApplication`-installed event filter).

## 2. The Stack data model (`stack.py`)

This is the one thing every other part of the app reads from. A
**Stack** is an ordered list of **Stage**s; each `Stage` has a `kind`
(one of `STAGE_KINDS`), a `params` dict specific to that kind, `enabled`,
`opacity`, and an optional `branch` tag (`"a"`/`"b"`, only meaningful
inside a fork region — see below).

**Current `STAGE_KINDS`**: `lut`, `printer_lights`, `aces_drt`, `cdl`,
`colorspace`, `grading_primary`, `exposure_contrast`, `grading_tone`,
`fork_split`, `fork_merge`. Each non-fork kind has a matching backend
module (`cdl.py`, `aces_drt.py`, `colorspace_convert.py`,
`grading_primary.py`, `exposure_contrast.py`, `grading_tone.py`,
`printer_lights.py` — the `lut` kind is handled inline, see §3) exposing
an `apply_X(array, **params) -> array` function, plus a `make_X_stage(**
params) -> Stage` factory in `stack.py` itself.

**One dispatch function, every consumer.** `stack._apply_stage_to_array`
is the single place that knows "given this stage, what does it do to an
array" — it's reused, unmodified, by:
- `apply_stack_to_image_array` (direct pixel-array apply — what the live
  CPU preview and ScopesPanel use)
- `bake_stack_to_lut3d` (applies the whole stack to an identity grid to
  produce one flat `colour.LUT3D` — what gets exported)

This is why adding a new stage kind's export support is "free" once its
CPU apply function exists and is wired into that one dispatch function —
neither the export path nor Scopes need separate work.

**`StageCache`** (also in `stack.py`) makes re-running a stack after a
small edit cheap: it fingerprints each stage (`_stage_fingerprint` — a
hashable tuple of everything about a stage that could change what it
computes; a `lut` stage's actual LUT object is fingerprinted by Python
`id()`, not value, since loaded/baked LUT objects are treated as
immutable everywhere in this app) and only re-runs stages from the first
one whose fingerprint actually changed, reusing every earlier stage's
already-computed result.

**Stack Forking** (`fork_split`/`fork_merge`): two branches (tagged
stages between a split and its matching merge) computed independently
and blended back together. Deliberately modeled as ordinary flat
`Stage`s in the list (not a nested tree) — `_run_stages` matches
split/merge pairs structurally. This is the one part of the Stack model
the GPU renderer doesn't support yet (see §5).

## 3. Color pipeline (CPU) — the source of truth

Every stage kind except `lut` goes through **OpenColorIO** (`PyOpenColorIO`,
v2.5.2), via `getDefaultCPUProcessor()`. Two config styles are used:

- **A bare config** (`ocio.Config.CreateRaw()`, cached via `cdl.
  _get_raw_config()` and reused by every module that needs it) for
  transforms that never reference a named colorspace: `cdl`,
  `grading_primary`, `grading_tone`, `exposure_contrast`.
- **A real ACES config** (`aces_drt._get_config`) for `aces_drt`
  (a real ACES Output Transform / RRT+ODT) and `colorspace` (a plain
  src→dst `ColorSpaceTransform`) — these genuinely need named
  colorspaces/displays/views.

The `lut` stage kind is different: it's `colour.LUT3D`/`colour.LUT1D`
objects from the `colour-science` library, applied via their own
`.apply()` method (trilinear interpolation by default), not OCIO at all
— this is a real seam in the codebase (`gpu_lut_shader.py`'s own
docstring calls it out) that mattered a lot for the GPU work, see §5.

**This CPU path is the actual source of truth for the whole app.**
Everything the app *delivers* — the exported LUT file, the Scopes
readout — goes through this path, at full precision, unconditionally.
The GPU path described next is exclusively for the interactive on-screen
preview; nothing GPU-rendered is ever baked or exported.

## 4. GPU acceleration architecture

### Why this exists

The live Viewer preview used to be 100% CPU-bound — every stack apply
went through the exact same `getDefaultCPUProcessor()` path as export.
Benchmarked directly (not assumed): ~764ms per apply at native 1080p,
scaling ~linearly with pixel count. Genuinely unusable for dragging a
slider. `GPU_ACCELERATION_PLAN_2026-08-08.md` is the plan this was built
from, in 6 staged pieces; `OCIO_TIER1_GRADING_OPERATORS_PLAN_2026-08-09.md`
covers the three newer "Grading" operator kinds specifically.

### The platform-specific GLSL choice

Every shader in this pipeline targets **GLSL 1.20 / OpenGL 2.1
Compatibility Profile** — not a newer Core Profile. This was confirmed,
not assumed: a direct `GL_VERSION`/`GL_SHADING_LANGUAGE_VERSION` query
against the real `QOpenGLWidget` context on macOS returned `2.1 Metal -
88` / `1.20`. Forcing a newer Core Profile (tried, both per-widget and
app-wide) broke the Graph panel's pyqtgraph rendering outright — Apple's
OpenGL on this Mac shares one GL "share group" across every
`QOpenGLWidget` in the process (`Qt.ApplicationAttribute.
AA_ShareOpenGLContexts`), and pyqtgraph's own GL code never binds its
own Vertex Array Object, which Compatibility Profile tolerates but Core
Profile requires. So: no `in`/`out`, no `texture()`, no `gl_VertexID`
"no-VBO-needed" trick — `varying`, `texture2D()`, `gl_FragColor`, and
real VBOs throughout.

### The Viewer is genuinely GPU-resident

`wipe_compare_widget.WipeCompareWidget` (the Viewer) is a real
`QOpenGLWidget` — `paintGL` runs the ENTIRE color pipeline plus the
spatial scaling/compositing on the GPU, at native image resolution, not
"compute on GPU then hand a CPU array back to the old pipeline." Two
independent layers:

- **Color** (`gpu_stack_renderer.GpuStackRenderer.render_chain`) — runs
  a stack's enabled stages as a ping-pong chain of compiled GL programs,
  one pass per stage, mirroring `_run_stages`' own fold.
- **Spatial** (`gpu_lanczos_compositor.py`) — separable 2-pass Lanczos-3
  resampling from native resolution into the widget's own pixel size,
  with **adaptive minification support**: the kernel's support widens by
  roughly `1/scale` under heavy downscale (capped) to avoid aliasing —
  a real, measured fix (a naive fixed 6-tap kernel produced a heavily
  aliased "crunchy" image when the Viewer was zoomed out a lot). The
  same module also does the final wipe-divider/gutter/placeholder
  compositing, entirely on GPU.

### How a CPU-side OCIO transform becomes a live GPU shader

Two genuinely different mechanisms, because OCIO's own GPU codegen
treats different transform kinds differently:

1. **"Static" kinds** (`cdl`, `aces_drt`, `colorspace`, `lut`) — OCIO's
   default codegen bakes a transform's parameter values as literal GLSL
   constants, not uniforms. For `cdl` specifically, this meant hand-
   writing the shader (`gpu_cdl_shader.py`, transcribed from OCIO's own
   generated GLSL, exposing real uniforms) so a live SOP/SAT drag doesn't
   force a shader recompile on every tick. `aces_drt`/`colorspace` don't
   need that treatment — their params (working colourspace/display/view,
   or src/dst) only change via a combo-box pick, not continuous
   dragging, so `gpu_shader_cache.OcioGpuStageCache` just compiles once
   per distinct config and caches the result (with a disk-persisted GLSL
   **source** cache across app restarts — not the compiled binary, which
   isn't portably cacheable on macOS's GL driver).
2. **"Dynamic uniform" kinds** (`grading_primary`, `grading_tone`,
   `exposure_contrast`) — a real, useful discovery: when you call
   `transform.makeDynamic()` before building the processor, OCIO emits
   its OWN generated GLSL with genuine named uniforms (confirmed by
   inspecting the actual generated GLSL text) — no hand-writing needed
   at all. `gpu_ocio_dynamic.py` is one **generic**, kind-agnostic
   mechanism covering all three: `push_dynamic_uniforms` dispatches over
   every `ocio.UniformDataType` these three kinds produce (scalar, bool,
   `vec3`, int-array, float-array — the last two are what
   `GradingRGBCurveTransform` would need too, if that gets built later)
   and pushes an OCIO `DynamicProperty`'s current value into the
   compiled program's real GL uniform locations, cached per compiled
   program so repeated pushes during a drag don't re-pay a
   `glGetUniformLocation` string lookup every frame. `exposure_contrast`
   is the one kind with **three independent** dynamic properties per
   transform (exposure/contrast/gamma each have their own
   `makeXDynamic()` flag) rather than one combined property — the
   generic mechanism handles that as a dict of handles, not a special
   case.

`gpu_lut_shader.py` handles the `lut` kind specifically: rather than
hand-writing a 3D-texture sampler, it routes the LUT through OCIO's own
`Lut3DTransform` GPU codegen (which already solves the half-texel
coordinate-remap problem correctly — verified directly in the generated
GLSL). It also wraps that generated shader with one extra function doing
the same domain-normalization `colour.LUT3D.apply()` itself does before
indexing, needed because real-world grading cubes often specify a
domain like `[[0,0,0], [1.79,1.79,1.79]]` (a LogC-range cube, not
`[0,1]`) — found live, not anticipated, and fixed by baking the domain
bounds as GLSL constants (a different domain always means a different
cache entry anyway).

### Caching — always fingerprinted on structure, never on values

Every one of these caches (`gpu_shader_cache.OcioGpuStageCache`,
`gpu_ocio_dynamic.OcioDynamicGpuStageCache`, `gpu_lut_shader.
Lut3DGpuCache`, `gpu_cdl_shader.CdlGpuProgram`) is keyed on something
**structural** — a config name, a style, a LUT object's identity — never
on the live parameter values a slider is currently at. That's the whole
point of the dynamic-uniform mechanism above: a value changing pushes a
cheap uniform update; only a genuinely different *shape* of transform
(a new LUT loaded, a style dropdown changed) triggers a real recompile.

### `gpu_stack_renderer.is_stack_gpu_eligible` — the fast-path gate

A whole Stack either goes through the GPU renderer or falls back to the
CPU path in full — there's no partial/mixed rendering. A stack is
eligible only if it has **no** `fork_split`/`fork_merge` stage at all,
and every *enabled* stage's kind is GPU-supported. `lut` stages have
extra conditions (no Retarget wrapping, an implicit — not per-sample
explicit — domain, and must actually be a `LUT3D` not a `LUT1D`) checked
via `gpu_lut_shader.is_lut3d_gpu_eligible`. Any single ineligible stage
anywhere in the stack — even one unrelated to what you're currently
editing — sends the **whole** stack back to the old CPU path for the
Viewer. This was the actual explanation behind a real "why does the GPU
work feel like it's not helping" report: one plain `lut` stage used to
disqualify an entire stack (fixed — `lut` is now GPU-eligible for the
common case), and later a `lut` stage with a non-default domain did the
same thing (also fixed, see above).

### Texture upload discipline — a real bug worth knowing about

`wipe_compare_widget._SideState.ensure_texture` uploads the Viewer's
native-resolution source array to a GL texture. This used to be gated by
one combined `(array_fingerprint, stack_fingerprint)` check — which
meant a live slider drag (which changes the stack fingerprint every
tick) was **also** re-uploading the completely unchanged, full-native-
resolution source array to the GPU from scratch on every single edit
(tens to hundreds of MB of `glTexImage2D` traffic per tick for a large
image). Fixed by tracking upload-worthiness and re-render-worthiness as
two independent checks: re-upload only when the array itself changes,
re-run the color chain whenever the stack changes. If you're
instrumenting GPU perf again later, this class of bug (a combined cache
key gating two things that should invalidate independently) is worth
checking for first.

### Validation discipline

Every GPU shader in this pipeline was validated against its CPU
equivalent by actually rendering both and diffing the pixels — never
assumed from reading OCIO's docs. Typical deltas are ~1e-4 to 1e-5
(float32 noise), which is why the GPU path is explicitly scoped as
preview-only (§3) rather than treated as bit-identical to CPU.

### Non-Viewer CPU cost — Scopes, and why it's capped small

Scopes (`scopes_panel.py`) needs its own CPU-computed graded array
regardless of whether the Viewer is GPU-accelerated — it reads pixel
statistics, which the GPU pipeline never reads back to the CPU (that
would defeat the point). `sample_image_panel.py`'s `refresh_preview`
recomputes this via `apply_stack_to_image_array` (the same CPU path as
export). For a GPU-eligible stack, this recompute always runs against a
small, fixed-size array (`SCOPES_MAX_DIMENSION` = 512px long edge,
regardless of whether the current edit is an isolated tick or mid-drag-
burst) — not native resolution, ever. This exists because a real `lut`
stage's CPU apply (`colour.LUT3D.apply()`) measured **0.5–3.3 seconds**
at native/half preview resolution — multiple real seconds of UI-thread
stall per edit, entirely independent of how fast the GPU-rendered Viewer
itself had become. (A GPU-*ineligible* stack still uses the older
two-tier scheme — full resolution for an isolated edit, halved
resolution during a detected burst — since for that stack the Viewer
itself genuinely needs this CPU-computed array, not just Scopes.)

## 5. What isn't GPU-accelerated (yet), and why

- **`GradingRGBCurveTransform`** — OCIO does emit real dynamic uniforms
  for this one too (confirmed the same way as the three kinds already
  done), but there's no interactive curve-editing widget in this
  codebase yet to drive it — the Graph panel's own curve display is
  read-only. This is the one piece of Tier 1 (the four newer "Grading"
  operators) not started; it's a real, substantial new UI component, not
  just shader work.
- **`printer_lights`** — never ported to the GPU path.
- **`LUT1D`/`LUT3x1D` stages, explicit-domain LUTs, Retarget-wrapped
  `lut` stages** — each would need its own additional shader/domain
  work `gpu_lut_shader.py` doesn't do yet; documented, not silent, scope
  limits (see that module's own docstring).
- **Fork regions** (`fork_split`/`fork_merge`) — need two render targets
  (one per branch) plus a blend pass; the one structurally different
  case in `_run_stages`, deliberately left for last.

## 6. Persistence & testing conventions

**Sessions** (`session_io.py`) round-trip an entire Stack — every stage
kind, including all the newer Tier 1 kinds — plus Stack Memory, undo
history, and library references, as plain JSON `.ltr` files, written
atomically (write-temp-then-rename) to avoid a half-written file on a
crash mid-save.

**Testing**: every module has a matching `test_X.py`. Each one is
runnable two ways — directly (`python3 test_X.py`, prints one `-- OK`
line per check) and via `pytest -q` — except the handful that need a
real (if offscreen) OpenGL context, which are excluded from the shared
`pytest` process via `conftest.py`'s `collect_ignore` list (a real,
reproduced GL-state-interaction bug with pyqtgraph's own class-level
compiled-shader caching if they share a process) and only run standalone.
As of this session: 430 tests passing under `pytest -q`, plus 9
standalone real-GL-context test files.

## 7. Build

`rm -rf build dist && python3 setup.py py2app` → `dist/LUTier.app`,
single-arch (Apple Silicon) — `numpy`/`scipy` wheels on this machine are
arm64-only, which is why `setup.py` doesn't request `universal2` (see
`BUILD_ON_MAC.md` for the troubleshooting history behind that).
