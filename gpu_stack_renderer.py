"""
gpu_stack_renderer.py -- GPU_ACCELERATION_PLAN_2026-08-08.md, STAGE 5.

Renders an entire eligible stack.Stack chain on the GPU, at native
resolution, by running Stage 2/3's per-stage-kind compiled programs
(gpu_cdl_shader.CdlGpuProgram for "cdl", gpu_aces_drt_shader.
AcesDrtGpuCache for "aces_drt", gpu_shader_cache.OcioGpuStageCache for
"colorspace") in sequence via ping-pong framebuffers -- one render pass
per enabled stage, in stack order, mirroring stack._run_stages' linear
fold for exactly the stage kinds this supports.

"aces_drt" got its OWN cache (Stage 9, 2026-08-11) after a real,
reported bug: every HDR display/view needs a GPU texture (a packed 1D
shaper LUT) that the OcioGpuStageCache it used to share with "colorspace"
had no concept of at all -- see gpu_aces_drt_shader.py's own docstring
for the full story (Task #31, "HDR output... very milky in viewer").

GPU ELIGIBILITY (is_stack_gpu_eligible) -- every ENABLED stage's kind
must be one of "cdl"/"aces_drt"/"colorspace"/"grading_primary"/
"exposure_contrast"/"grading_tone"/"grading_rgb_curve"/"lut"/
"printer_lights". A stack with any GPU-ineligible stage anywhere falls
back to the existing CPU path (stack.apply_stack_to_image_array)
entirely, same math as always. Disabled stages of ANY kind are harmless
to have present -- render_chain simply skips them, same "disabled ->
exact no-op" contract stack._apply_single_stage_with_opacity already
guarantees on CPU.

FORK SUPPORT (GPU_ACCELERATION_PLAN_2026-08-08.md Stage 6, 2026-08-12) --
fork_split/fork_merge no longer disqualify a stack outright. Mirrors
_run_stages' own bookkeeping (stack.py, "STACK FORKING") closely enough
to stay correct against it: nested forks are still NOT eligible (same
"one open region at a time" rule _run_stages itself enforces -- GPU fork
rendering doesn't attempt to handle nesting either, see render_chain's
own docstring); an interior stage positioned strictly between a matched,
ENABLED split/merge pair with no real branch tag ("a"/"b") is already an
invalid/corrupted stack (_run_stages raises on exactly this) -- GPU
eligibility doesn't try to guess a branch for it either, just falls back
to CPU so the EXISTING bake-error surfacing (StackPanel.set_bake_error,
driven by _run_stages' own ValueError) catches it the normal way, same
as it always has. A DISABLED fork_split's interior is skipped entirely
for eligibility purposes too (matches _run_stages' own "whole region is
a no-op" contract exactly -- an interior stage's kind/branch tag
genuinely doesn't matter there, since it's never evaluated either way).
fork_split/fork_merge themselves need no eligibility check of their
own -- they have no shader; fork_merge's blend+opacity render as a
dedicated 3-texture blend pass instead (see gpu_fork_merge_shader.py).

"printer_lights" is only PARTIALLY eligible even when enabled -- see
gpu_printer_lights_shader.py's own docstring: requires PyOpenColorIO (no
GPU equivalent of that stage's colour-science CPU fallback) and an
unambiguous native-linear sibling for its working_colourspace. Either gap
falls the WHOLE stack back to CPU, same as any other GPU-ineligible
stage. Uses its own hand-written-uniform GLSL (decode -> gain -> encode,
gain pushed as a plain vec3 uniform every render, never touching the
compiled program) rather than gpu_ocio_dynamic.py's dynamic-property
mechanism, since ocio.MatrixTransform (the natural fit for a diagonal
gain) has no dynamic-property support at all -- confirmed directly.

"lut" is only PARTIALLY eligible even when enabled -- see
gpu_lut_shader.py's own docstring, "SCOPE LIMIT": no retarget_from/
retarget_to wrapping, and the lut object itself must be a plain,
default-[0,1]-domain colour.LUT3D (not LUT1D, not a non-default domain).
Either gap falls the WHOLE stack back to CPU, same as any genuinely
GPU-ineligible kind -- is_stack_gpu_eligible checks both conditions
itself rather than deferring to gpu_lut_shader for the retarget check.

"grading_primary"/"exposure_contrast"/"grading_tone"/"grading_rgb_curve"
(added per OCIO_TIER1_GRADING_OPERATORS_PLAN_2026-08-09.md) use a
DIFFERENT caching mechanism than "cdl"/"aces_drt"/"colorspace":
gpu_ocio_dynamic.OcioDynamicGpuStageCache, fingerprinted on style ONLY
(not on the live brightness/contrast/gamma/exposure/curve-points/etc.
values -- see that module's own docstring on why the shader STRUCTURE
doesn't change when a slider or curve point moves for a makeDynamic()'d
OCIO transform, only the uniform VALUES pushed into it do).
"exposure_contrast" is the one kind with THREE independent dynamic
properties per transform instead of one (DYNAMIC_PROPERTY_EXPOSURE/
_CONTRAST/_GAMMA, see gpu_ocio_dynamic.py's own docstring) -- its cache
entry's dynamic_properties dict just has three keys instead of one, same
code path otherwise. "grading_rgb_curve" pushes its four B-spline curves
(red/green/blue/master) through ONE DYNAMIC_PROPERTY_GRADING_RGBCURVE
property, exercising the array-typed uniforms (knots/coefs/offsets)
gpu_ocio_dynamic.push_dynamic_uniforms was built generically over every
ocio.UniformDataType to support -- see grading_rgb_curve.py's own
docstring. _grading_primary_cache/_exposure_contrast_cache/
_grading_tone_cache/_grading_rgb_curve_cache are separate cache instances
from _cdl_program/_aces_drt_cache/_colorspace_cache, but live alongside
them for the same reason: compiled programs are only valid for this
instance's one GL context.
"""
import functools

import OpenGL.GL as gl

import aces_drt
import cdl
import colorspace_convert
import custom_drt
import gpu_aces_drt_shader
import gpu_cdl_shader
import gpu_custom_drt_shader
import gpu_fork_merge_shader as gfm
import gpu_lut_shader
import gpu_ocio_dynamic
import gpu_printer_lights_shader
import gpu_shader_cache
import gpu_shader_prototype as gsp
import exposure_contrast
import grading_primary
import grading_rgb_curve
import grading_tone
import printer_lights

GPU_ELIGIBLE_KINDS = frozenset({"cdl", "aces_drt", "custom_drt", "colorspace", "grading_primary",
                                "exposure_contrast", "grading_tone", "grading_rgb_curve",
                                "lut", "printer_lights"})


def is_stack_gpu_eligible(stack):
    """See this module's docstring, "GPU ELIGIBILITY"/"FORK SUPPORT", for
    the full reasoning. `fork_state` tracks the currently-open fork
    region (mirroring _run_stages' own fork_stack, just a single flag
    since nesting isn't supported either place): None outside any fork,
    True inside an ENABLED one (interior stages must carry a real branch
    tag and pass the same eligibility checks as any other stage), False
    inside a DISABLED one (interior stages are skipped entirely -- kind,
    branch tag, none of it matters, since _run_stages never evaluates
    them either)."""
    fork_state = None
    for stage in stack.stages:
        if stage.kind == "fork_split":
            if fork_state is not None:
                return False  # nested fork -- not supported (see _run_stages)
            fork_state = stage.enabled
            continue
        if stage.kind == "fork_merge":
            if fork_state is None:
                return False  # unmatched merge -- already an invalid stack
            fork_state = None
            continue
        if fork_state is False:
            continue  # inside a DISABLED fork region -- whole region is a no-op, skip entirely
        if fork_state is True and stage.branch not in ("a", "b"):
            return False  # orphaned interior stage -- invalid stack, let CPU's error surfacing catch it
        if not stage.enabled:
            continue
        if stage.kind not in GPU_ELIGIBLE_KINDS:
            return False
        if stage.kind == "lut":
            # A "lut" stage is only PARTIALLY GPU-eligible -- see
            # gpu_lut_shader.py's own docstring, "SCOPE LIMIT": no
            # retarget wrapping (a whole separate colorspace-conversion
            # step this renderer doesn't chain in), and the LUT's domain
            # must be implicit (any [min, max] pair, not just [0,1] --
            # see gpu_lut_shader.is_lut3d_gpu_eligible). Either gap falls
            # the WHOLE stack back to the CPU path, same as any other
            # GPU-ineligible stage.
            if stage.params.get("retarget_from") or stage.params.get("retarget_to"):
                return False
            if not gpu_lut_shader.is_lut3d_gpu_eligible(stage.params.get("lut")):
                return False
        if stage.kind == "printer_lights":
            # See gpu_printer_lights_shader.py's own docstring -- requires
            # OCIO (no GPU equivalent of the colour-science CPU fallback)
            # and an unambiguous native-linear sibling for this stage's
            # working_colourspace. Safe to call with no current GL context
            # (a real, reproduced crash otherwise -- see that module's
            # own docstring on is_printer_lights_gpu_eligible).
            working_colourspace = stage.params.get(
                "working_colourspace", printer_lights.DEFAULT_WORKING_COLOURSPACE)
            if not gpu_printer_lights_shader.is_printer_lights_gpu_eligible(
                    aces_drt.DEFAULT_CONFIG_NAME, working_colourspace):
                return False
        if stage.kind == "custom_drt":
            # See gpu_custom_drt_shader.py's own docstring -- requires
            # OCIO (custom_drt.py has no CPU-only fallback path at all)
            # and a working_colourspace whose conversion to Linear
            # Rec.2020 needs no baked texture. Safe to call with no
            # current GL context, same "generate/inspect GLSL text only"
            # convention printer_lights' own eligibility check follows.
            working_colourspace = stage.params.get("working_colourspace")
            config_name = stage.params.get("config_name", custom_drt.DEFAULT_CONFIG_NAME)
            mastering_display = stage.params.get(
                "mastering_display", custom_drt.DEFAULT_MASTERING_DISPLAY)
            final_target = stage.params.get("final_target")
            if not gpu_custom_drt_shader.is_custom_drt_gpu_eligible(
                    config_name, working_colourspace, mastering_display, final_target):
                return False
    if fork_state is not None:
        return False  # unmatched split at the end of the stack -- already invalid
    return True


def _build_aces_drt_processor(config_name, working_colourspace, display, view):
    """Mirrors aces_drt.apply_aces_output_transform's own processor
    construction exactly (same _get_config, same DisplayViewTransform) --
    used by AcesDrtGpuCache.get_entry, which does its OWN shader/texture
    extraction (see gpu_aces_drt_shader.py's own docstring on why this
    kind needs a real texture for HDR displays/views, unlike every other
    OCIO-backed GPU kind here)."""
    cfg = aces_drt._get_config(config_name)
    return cfg.getProcessor(
        aces_drt.ocio.DisplayViewTransform(src=working_colourspace, display=display, view=view))


def _build_dynamic_grading_primary_transform(style):
    """build_dynamic_transform for gpu_ocio_dynamic.OcioDynamicGpuStageCache
    -- a fresh, makeDynamic()'d GradingPrimaryTransform for `style`. Called
    ONLY on a cache miss (see that class's own docstring); the transform
    itself is discarded once its shader/DynamicProperty are extracted --
    only entry.shader_desc's live DynamicProperty handle is kept around
    for subsequent edits, not this transform object."""
    transform = aces_drt.ocio.GradingPrimaryTransform(grading_primary._style_to_ocio(style))
    transform.makeDynamic()
    return transform


def _build_dynamic_exposure_contrast_transform(style):
    """build_dynamic_transform for the exposure_contrast cache -- unlike
    every other kind here, this transform has THREE independent dynamic
    flags to set, not one makeDynamic() call (see gpu_ocio_dynamic.py's
    own docstring on why ExposureContrastTransform is shaped this way)."""
    transform = aces_drt.ocio.ExposureContrastTransform()
    transform.setStyle(exposure_contrast._style_to_ocio(style))
    transform.makeExposureDynamic()
    transform.makeContrastDynamic()
    transform.makeGammaDynamic()
    return transform


def _build_dynamic_grading_tone_transform(style):
    """build_dynamic_transform for the grading_tone cache -- single
    makeDynamic() call, same shape as grading_primary's (one
    DYNAMIC_PROPERTY_GRADING_TONE property covers all 5 ranges + scontrast,
    see grading_tone.py's own docstring)."""
    transform = aces_drt.ocio.GradingToneTransform(grading_tone._style_to_ocio(style))
    transform.makeDynamic()
    return transform


def _build_dynamic_grading_rgb_curve_transform(style):
    """build_dynamic_transform for the grading_rgb_curve cache -- single
    makeDynamic() call under one DYNAMIC_PROPERTY_GRADING_RGBCURVE
    property covering all four channel curves at once (see
    grading_rgb_curve.py's own docstring; already validated bit-exact
    against CPU in test_gpu_ocio_dynamic.py's test_grading_rgb_curve_
    matches_cpu before this stage kind had any Stack/UI wiring at all)."""
    transform = aces_drt.ocio.GradingRGBCurveTransform(grading_rgb_curve._style_to_ocio(style))
    transform.makeDynamic()
    return transform


def _generate_colorspace_glsl(config_name, src, dst):
    """Mirrors colorspace_convert.apply_colorspace_conversion's own
    processor construction exactly (same _get_config, same
    ColorSpaceTransform), just asking for GPU shader text instead of a
    CPUProcessor."""
    cfg = aces_drt._get_config(config_name)
    processor = cfg.getProcessor(aces_drt.ocio.ColorSpaceTransform(src=src, dst=dst))
    shader_text, _n_tex, _n_tex3d, _n_uniforms = gsp._extract_ocio_glsl(processor)
    return shader_text


class GpuStackRenderer:
    """Owns the compiled-program caches for all three GPU-eligible stage
    kinds, plus the ping-pong render-target pair the chain renders
    through and the shared fullscreen-triangle VAO every pass draws with.
    Meant to live for the lifetime of one GL context (e.g. one instance
    per WipeCompareWidget, created in initializeGL) -- see
    clear_gl_resources for what to call before that context goes away."""

    def __init__(self):
        self._cdl_program = gpu_shader_cache.CdlGpuProgram()
        self._aces_drt_cache = gpu_aces_drt_shader.AcesDrtGpuCache()
        self._colorspace_cache = gpu_shader_cache.OcioGpuStageCache(
            "colorspace", _generate_colorspace_glsl)
        self._grading_primary_cache = gpu_ocio_dynamic.OcioDynamicGpuStageCache(
            "grading_primary", (aces_drt.ocio.DYNAMIC_PROPERTY_GRADING_PRIMARY,))
        self._exposure_contrast_cache = gpu_ocio_dynamic.OcioDynamicGpuStageCache(
            "exposure_contrast", (aces_drt.ocio.DYNAMIC_PROPERTY_EXPOSURE,
                                   aces_drt.ocio.DYNAMIC_PROPERTY_CONTRAST,
                                   aces_drt.ocio.DYNAMIC_PROPERTY_GAMMA))
        self._grading_tone_cache = gpu_ocio_dynamic.OcioDynamicGpuStageCache(
            "grading_tone", (aces_drt.ocio.DYNAMIC_PROPERTY_GRADING_TONE,))
        self._grading_rgb_curve_cache = gpu_ocio_dynamic.OcioDynamicGpuStageCache(
            "grading_rgb_curve", (aces_drt.ocio.DYNAMIC_PROPERTY_GRADING_RGBCURVE,))
        self._lut3d_cache = gpu_lut_shader.Lut3DGpuCache()
        self._printer_lights_cache = gpu_printer_lights_shader.PrinterLightsGpuCache()
        self._custom_drt_cache = gpu_custom_drt_shader.CustomDrtGpuCache()
        self._vbo = None
        # Ping-pong pair, sized lazily to the first render_chain call's
        # actual image dimensions and recreated whenever that changes
        # (see _ensure_ping_pong) -- two render targets, alternated so
        # each stage's OUTPUT becomes the NEXT stage's INPUT without ever
        # reading and writing the same texture in one draw call (which
        # is undefined behavior in OpenGL).
        self._ping_pong_size = None
        self._ping_pong_textures = None
        self._ping_pong_fbos = None
        # Fork support (Stage 6) -- one static compiled program (see
        # gpu_fork_merge_shader.py, no fingerprinting needed, its source
        # never varies), a second pair of render targets used to snapshot
        # a texture that would otherwise get overwritten by the shared
        # ping-pong pair being reused for each branch in turn (see
        # render_chain's own docstring), and one reusable FBO for the
        # plain glBlitFramebuffer-based copy _snapshot_texture uses to
        # populate them.
        self._fork_merge_program = None
        self._fork_snapshot_size = None
        self._fork_snapshot_textures = None
        self._fork_snapshot_fbos = None
        self._blit_source_fbo = None

    @staticmethod
    def _aces_drt_fingerprint(stage):
        p = stage.params
        return (p.get("config_name", aces_drt.DEFAULT_CONFIG_NAME),
                p["working_colourspace"], p["display"], p["view"])

    def _aces_drt_entry(self, stage):
        fingerprint = self._aces_drt_fingerprint(stage)
        return self._aces_drt_cache.get_entry(
            fingerprint, functools.partial(_build_aces_drt_processor, *fingerprint))

    def _program_for_stage(self, stage):
        if stage.kind == "cdl":
            return self._cdl_program.get_program()
        if stage.kind == "aces_drt":
            return self._aces_drt_entry(stage).program
        if stage.kind == "colorspace":
            p = stage.params
            fingerprint = (p.get("config_name", colorspace_convert.DEFAULT_CONFIG_NAME),
                           p["src"], p["dst"])
            return self._colorspace_cache.get_program(fingerprint)
        if stage.kind == "grading_primary":
            style = stage.params.get("style", grading_primary.DEFAULT_STYLE)
            entry = self._grading_primary_cache.get_entry(
                (style,), functools.partial(_build_dynamic_grading_primary_transform, style))
            return entry.program
        if stage.kind == "exposure_contrast":
            style = stage.params.get("style", exposure_contrast.DEFAULT_STYLE)
            entry = self._exposure_contrast_cache.get_entry(
                (style,), functools.partial(_build_dynamic_exposure_contrast_transform, style))
            return entry.program
        if stage.kind == "grading_tone":
            style = stage.params.get("style", grading_tone.DEFAULT_STYLE)
            entry = self._grading_tone_cache.get_entry(
                (style,), functools.partial(_build_dynamic_grading_tone_transform, style))
            return entry.program
        if stage.kind == "grading_rgb_curve":
            style = stage.params.get("style", grading_rgb_curve.DEFAULT_STYLE)
            entry = self._grading_rgb_curve_cache.get_entry(
                (style,), functools.partial(_build_dynamic_grading_rgb_curve_transform, style))
            return entry.program
        if stage.kind == "lut":
            entry = self._lut3d_cache.get_entry(stage.params["lut"])
            return entry.program
        if stage.kind == "printer_lights":
            working_colourspace = stage.params.get(
                "working_colourspace", printer_lights.DEFAULT_WORKING_COLOURSPACE)
            entry = self._printer_lights_cache.get_entry(aces_drt.DEFAULT_CONFIG_NAME, working_colourspace)
            return entry.program
        if stage.kind == "custom_drt":
            p = stage.params
            entry = self._custom_drt_cache.get_entry(
                p.get("config_name", custom_drt.DEFAULT_CONFIG_NAME), p["working_colourspace"],
                p.get("mastering_display", custom_drt.DEFAULT_MASTERING_DISPLAY),
                p.get("final_target"))
            return entry.program
        raise AssertionError(  # pragma: no cover -- caller must check is_stack_gpu_eligible first
            f"{stage.kind!r} is not GPU-eligible; caller should have "
            "checked is_stack_gpu_eligible before calling render_chain.")

    def _set_uniforms_for_stage(self, stage):
        """A callable for render_stage_pass's own set_uniforms hook --
        pushes this stage's actual param values (for "cdl", whose shader
        is uniform-driven, see gpu_cdl_shader.py; for "grading_primary",
        whose shader is OCIO's own dynamic-uniform GLSL, see
        gpu_ocio_dynamic.py) plus this stage's own `opacity` (every
        GPU-eligible kind's shader has uStageOpacity, see
        gpu_shader_prototype.py's fragment shader template) onto
        whichever program render_stage_pass has already made current."""
        if stage.kind == "cdl":
            p = stage.params
            return functools.partial(
                gpu_cdl_shader.set_cdl_uniforms, gl,
                slope=p.get("slope", cdl.IDENTITY_SLOPE),
                offset=p.get("offset", cdl.IDENTITY_OFFSET),
                power=p.get("power", cdl.IDENTITY_POWER),
                saturation=p.get("sat", cdl.IDENTITY_SAT),
                style=p.get("style", cdl.DEFAULT_STYLE),
                opacity=stage.opacity)

        if stage.kind == "aces_drt":
            # Real fix for Task #31 (see gpu_aces_drt_shader.py's own
            # docstring) -- binds whatever textures (zero for SDR, one for
            # every HDR display/view confirmed so far) this fingerprint's
            # shader actually declared, starting at GL_TEXTURE1 (unit 0 is
            # always uInput, see _render_stage_pass). Every SDR display/
            # view has an empty gl_textures list, so this loop is simply a
            # no-op for them -- same code path either way, no special-
            # casing needed for "does this one need a texture."
            entry = self._aces_drt_entry(stage)
            opacity = stage.opacity

            def _set_aces_drt_uniforms(program):
                for i, (gl_texture, sampler_name) in enumerate(entry.gl_textures):
                    gl.glActiveTexture(gl.GL_TEXTURE1 + i)
                    gl.glBindTexture(gl.GL_TEXTURE_2D, gl_texture)
                    gl.glUniform1i(gl.glGetUniformLocation(program, sampler_name), 1 + i)
                gl.glUniform1f(gl.glGetUniformLocation(program, "uStageOpacity"), opacity)
            return _set_aces_drt_uniforms

        if stage.kind == "grading_primary":
            p = stage.params
            style = p.get("style", grading_primary.DEFAULT_STYLE)
            entry = self._grading_primary_cache.get_entry(
                (style,), functools.partial(_build_dynamic_grading_primary_transform, style))
            value = grading_primary.build_grading_primary_value(
                style=style,
                brightness=p.get("brightness", grading_primary.IDENTITY_ADD),
                contrast=p.get("contrast", grading_primary.IDENTITY_MULT),
                gamma=p.get("gamma", grading_primary.IDENTITY_MULT),
                offset=p.get("offset", grading_primary.IDENTITY_ADD),
                exposure=p.get("exposure", grading_primary.IDENTITY_ADD),
                lift=p.get("lift", grading_primary.IDENTITY_ADD),
                gain=p.get("gain", grading_primary.IDENTITY_MULT),
                pivot=p.get("pivot", grading_primary.DEFAULT_PIVOT),
                saturation=p.get("saturation", grading_primary.DEFAULT_SATURATION))
            dynamic_property = entry.dynamic_properties[aces_drt.ocio.DYNAMIC_PROPERTY_GRADING_PRIMARY]
            opacity = stage.opacity

            def _set_grading_primary_uniforms(program):
                dynamic_property.setGradingPrimary(value)
                gpu_ocio_dynamic.push_dynamic_uniforms(program, entry.shader_desc, entry.uniform_locations)
                gl.glUniform1f(gl.glGetUniformLocation(program, "uStageOpacity"), opacity)
            return _set_grading_primary_uniforms

        if stage.kind == "exposure_contrast":
            p = stage.params
            style = p.get("style", exposure_contrast.DEFAULT_STYLE)
            entry = self._exposure_contrast_cache.get_entry(
                (style,), functools.partial(_build_dynamic_exposure_contrast_transform, style))
            exposure_value = p.get("exposure", exposure_contrast.IDENTITY_EXPOSURE)
            contrast_value = p.get("contrast", exposure_contrast.IDENTITY_CONTRAST)
            gamma_value = p.get("gamma", exposure_contrast.IDENTITY_GAMMA)
            dynamic_properties = entry.dynamic_properties
            opacity = stage.opacity

            def _set_exposure_contrast_uniforms(program):
                dynamic_properties[aces_drt.ocio.DYNAMIC_PROPERTY_EXPOSURE].setDouble(exposure_value)
                dynamic_properties[aces_drt.ocio.DYNAMIC_PROPERTY_CONTRAST].setDouble(contrast_value)
                dynamic_properties[aces_drt.ocio.DYNAMIC_PROPERTY_GAMMA].setDouble(gamma_value)
                gpu_ocio_dynamic.push_dynamic_uniforms(program, entry.shader_desc, entry.uniform_locations)
                gl.glUniform1f(gl.glGetUniformLocation(program, "uStageOpacity"), opacity)
            return _set_exposure_contrast_uniforms

        if stage.kind == "grading_tone":
            p = stage.params
            style = p.get("style", grading_tone.DEFAULT_STYLE)
            entry = self._grading_tone_cache.get_entry(
                (style,), functools.partial(_build_dynamic_grading_tone_transform, style))
            value = grading_tone.build_grading_tone_value(
                style=style, blacks=p.get("blacks"), shadows=p.get("shadows"),
                midtones=p.get("midtones"), highlights=p.get("highlights"), whites=p.get("whites"),
                scontrast=p.get("scontrast", grading_tone.DEFAULT_SCONTRAST))
            dynamic_property = entry.dynamic_properties[aces_drt.ocio.DYNAMIC_PROPERTY_GRADING_TONE]
            opacity = stage.opacity

            def _set_grading_tone_uniforms(program):
                # grading_tone.build_grading_tone_value already clamps
                # each range's R/G/B/Master gain and scontrast to OCIO's
                # own validate() bounds -- but Start/Width are NOT
                # clamped there (see GAIN_BOUNDS' own comment: their
                # validity depends on the OTHER ranges' current values
                # too, not just their own, so no single per-field clamp
                # is both correct and permissive). setGradingTone is
                # therefore the one remaining place an invalid Start/
                # Width combination can still raise -- caught here rather
                # than letting it propagate through render_chain into
                # paintGL, where an uncaught exception is a REAL,
                # reproduced hazard (see WipeCompareWidget.paintGL's own
                # docstring). On failure, this stage's chain simply keeps
                # rendering with whatever GradingTone value was last
                # successfully applied, rather than aborting the frame.
                try:
                    dynamic_property.setGradingTone(value)
                except aces_drt.ocio.Exception as e:
                    print(f"gpu_stack_renderer: grading_tone stage {stage.label!r} has an "
                          f"invalid Start/Width combination, keeping the previous value "
                          f"for this frame ({e})")
                gpu_ocio_dynamic.push_dynamic_uniforms(program, entry.shader_desc, entry.uniform_locations)
                gl.glUniform1f(gl.glGetUniformLocation(program, "uStageOpacity"), opacity)
            return _set_grading_tone_uniforms

        if stage.kind == "grading_rgb_curve":
            p = stage.params
            style = p.get("style", grading_rgb_curve.DEFAULT_STYLE)
            entry = self._grading_rgb_curve_cache.get_entry(
                (style,), functools.partial(_build_dynamic_grading_rgb_curve_transform, style))
            value = grading_rgb_curve.build_grading_rgb_curve_value(
                style=style, red=p.get("red"), green=p.get("green"),
                blue=p.get("blue"), master=p.get("master"))
            dynamic_property = entry.dynamic_properties[aces_drt.ocio.DYNAMIC_PROPERTY_GRADING_RGBCURVE]
            opacity = stage.opacity

            def _set_grading_rgb_curve_uniforms(program):
                # Unlike grading_tone's Start/Width, every control-point
                # sequence here is already normalized/floored to
                # MIN_CONTROL_POINTS by grading_rgb_curve._normalize_
                # control_points before build_grading_rgb_curve_value ever
                # builds the GradingBSplineCurve -- no OCIO validate()
                # failure is reachable from live UI values, so no try/
                # except guard is needed here (contrast grading_tone's own
                # comment on why IT still needs one).
                dynamic_property.setGradingRGBCurve(value)
                gpu_ocio_dynamic.push_dynamic_uniforms(program, entry.shader_desc, entry.uniform_locations)
                gl.glUniform1f(gl.glGetUniformLocation(program, "uStageOpacity"), opacity)
            return _set_grading_rgb_curve_uniforms

        if stage.kind == "lut":
            # A "lut" stage's own table never changes via any live UI
            # edit (only enabled/opacity/retarget do, and retarget-
            # wrapped stages are excluded from GPU eligibility entirely
            # -- see gpu_lut_shader.py's own docstring) -- so unlike
            # every dynamic-uniform kind above, there's no per-edit value
            # to push here at all, just the cached 3D texture (bound to
            # unit 1 -- render_stage_pass's own uInput 2D texture always
            # takes unit 0, see that method's own binding right after
            # set_uniforms runs) and this stage's own opacity.
            entry = self._lut3d_cache.get_entry(stage.params["lut"])
            opacity = stage.opacity

            def _set_lut3d_uniforms(program):
                loc = gl.glGetUniformLocation(program, entry.sampler_name)
                gl.glActiveTexture(gl.GL_TEXTURE1)
                gl.glBindTexture(gl.GL_TEXTURE_3D, entry.gl_texture)
                gl.glUniform1i(loc, 1)
                gl.glUniform1f(gl.glGetUniformLocation(program, "uStageOpacity"), opacity)
            return _set_lut3d_uniforms

        if stage.kind == "printer_lights":
            # No dynamic-property mechanism here (ocio.MatrixTransform has
            # none, confirmed by probing -- see gpu_printer_lights_shader.
            # py's own docstring) -- the gain is a plain hand-written GLSL
            # uniform instead, so a slider drag never touches the compiled
            # program at all, same "cheap to re-parameterize" bar every
            # other continuously-edited kind here meets.
            p = stage.params
            r_gain = printer_lights.point_gain(p.get("r_points", 0.0))
            g_gain = printer_lights.point_gain(p.get("g_points", 0.0))
            b_gain = printer_lights.point_gain(p.get("b_points", 0.0))
            opacity = stage.opacity

            def _set_printer_lights_uniforms(program):
                loc = gl.glGetUniformLocation(program, gpu_printer_lights_shader._UNIFORM_NAME)
                gl.glUniform3f(loc, float(r_gain), float(g_gain), float(b_gain))
                gl.glUniform1f(gl.glGetUniformLocation(program, "uStageOpacity"), opacity)
            return _set_printer_lights_uniforms

        if stage.kind == "custom_drt":
            # Every CAMier param is a plain hand-written GLSL uniform
            # (see gpu_custom_drt_shader.py's own docstring) -- same
            # "never recompile on a slider drag" bar printer_lights/CDL
            # meet, just with more of them.
            p = stage.params
            kwargs = dict(
                contrast=p.get("contrast", custom_drt.DEFAULT_CONTRAST),
                headroom=p.get("headroom", custom_drt.DEFAULT_HEADROOM),
                blend_percentile=p.get("blend_percentile", custom_drt.DEFAULT_BLEND_PERCENTILE),
                tail_width=p.get("tail_width", custom_drt.DEFAULT_TAIL_WIDTH),
                desat_start=p.get("desat_start", custom_drt.DEFAULT_DESAT_START),
                desat_end=p.get("desat_end", custom_drt.DEFAULT_DESAT_END),
                identity_zone=p.get("identity_zone", custom_drt.DEFAULT_IDENTITY_ZONE),
                transition_softness=p.get(
                    "transition_softness", custom_drt.DEFAULT_TRANSITION_SOFTNESS),
                fullness_r=p.get("fullness_r", custom_drt.DEFAULT_FULLNESS_R),
                fullness_g=p.get("fullness_g", custom_drt.DEFAULT_FULLNESS_G),
                fullness_b=p.get("fullness_b", custom_drt.DEFAULT_FULLNESS_B),
                mastering_brightness=p.get(
                    "mastering_brightness", custom_drt.DEFAULT_MASTERING_BRIGHTNESS),
            )
            opacity = stage.opacity

            def _set_custom_drt_uniforms(program):
                gpu_custom_drt_shader.set_custom_drt_uniforms(gl, program, **kwargs)
                gl.glUniform1f(gl.glGetUniformLocation(program, "uStageOpacity"), opacity)
            return _set_custom_drt_uniforms

        def _set_opacity_only(program):
            gl.glUniform1f(gl.glGetUniformLocation(program, "uStageOpacity"), stage.opacity)
        return _set_opacity_only

    def _ensure_ping_pong(self, w, h):
        if self._ping_pong_size == (w, h):
            return
        self._delete_ping_pong()
        textures, fbos = [], []
        for _ in range(2):
            tex, fbo = gsp.create_rgba32f_render_target(w, h)
            textures.append(tex)
            fbos.append(fbo)
        self._ping_pong_textures = textures
        self._ping_pong_fbos = fbos
        self._ping_pong_size = (w, h)

    def _delete_ping_pong(self):
        if self._ping_pong_textures:
            gl.glDeleteTextures(self._ping_pong_textures)
        if self._ping_pong_fbos:
            gl.glDeleteFramebuffers(len(self._ping_pong_fbos), self._ping_pong_fbos)
        self._ping_pong_textures = None
        self._ping_pong_fbos = None
        self._ping_pong_size = None

    def _ensure_fork_snapshots(self, w, h):
        """A second pair of render targets, lazily created only once a
        stack containing a fork actually needs them (most stacks never
        do) -- see render_chain's own docstring for exactly what each
        slot holds and why the shared ping-pong pair alone isn't enough
        once a fork's two branches both need to render FROM the same
        pre-split starting point."""
        if self._fork_snapshot_size == (w, h):
            return
        self._delete_fork_snapshots()
        textures, fbos = [], []
        for _ in range(2):
            tex, fbo = gsp.create_rgba32f_render_target(w, h)
            textures.append(tex)
            fbos.append(fbo)
        self._fork_snapshot_textures = textures
        self._fork_snapshot_fbos = fbos
        self._fork_snapshot_size = (w, h)

    def _delete_fork_snapshots(self):
        if self._fork_snapshot_textures:
            gl.glDeleteTextures(self._fork_snapshot_textures)
        if self._fork_snapshot_fbos:
            gl.glDeleteFramebuffers(len(self._fork_snapshot_fbos), self._fork_snapshot_fbos)
        self._fork_snapshot_textures = None
        self._fork_snapshot_fbos = None
        self._fork_snapshot_size = None

    def _copy_texture(self, src_texture, dst_fbo, w, h):
        """Copies `src_texture`'s current contents into whatever texture
        `dst_fbo` is attached to, via glBlitFramebuffer -- a plain
        GPU-side copy, no shader/draw call needed. Reuses ONE FBO
        (`self._blit_source_fbo`) as the read side across every call,
        just re-pointing its color attachment at whatever texture needs
        copying this time, rather than allocating/freeing a throwaway
        FBO per call (fork rendering needs at least two of these copies
        per region -- see render_chain)."""
        if self._blit_source_fbo is None:
            self._blit_source_fbo = gl.glGenFramebuffers(1)
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, self._blit_source_fbo)
        gl.glFramebufferTexture2D(
            gl.GL_FRAMEBUFFER, gl.GL_COLOR_ATTACHMENT0, gl.GL_TEXTURE_2D, src_texture, 0)
        gl.glBindFramebuffer(gl.GL_READ_FRAMEBUFFER, self._blit_source_fbo)
        gl.glBindFramebuffer(gl.GL_DRAW_FRAMEBUFFER, dst_fbo)
        gl.glBlitFramebuffer(0, 0, w, h, 0, 0, w, h, gl.GL_COLOR_BUFFER_BIT, gl.GL_NEAREST)

    def _render_stage_sequence(self, stages, start_texture, w, h):
        """Renders `stages` (already filtered to plain, non-fork,
        ENABLED stages, in order) against `start_texture` via the shared
        ping-pong pair, returning the final texture -- `start_texture`
        UNCHANGED if `stages` is empty (e.g. a genuinely empty branch),
        same "empty -> unchanged" contract render_chain's own top-level
        walk guarantees. Used for BOTH the top-level chain (ordinary
        stages between forks) and each fork branch (see render_chain) --
        safe to reuse the same ping-pong pair across all of these since
        they only ever run one after another, never concurrently, as
        long as a caller that needs an EARLIER result to survive a LATER
        call snapshots it out first via _copy_texture (exactly what
        render_chain's own fork handling does for the pre-split texture
        and Branch A's result)."""
        current_texture = start_texture
        output_index = 0
        for stage in stages:
            program = self._program_for_stage(stage)
            set_uniforms = self._set_uniforms_for_stage(stage)
            target_fbo = self._ping_pong_fbos[output_index]
            self._render_stage_pass(program, current_texture, target_fbo, w, h, set_uniforms, self._vbo)
            current_texture = self._ping_pong_textures[output_index]
            output_index = 1 - output_index
        return current_texture

    def render_chain(self, stack, input_texture, w, h):
        """Renders `stack` (caller must have already confirmed
        is_stack_gpu_eligible(stack)) against `input_texture` (a w x h
        RGBA32F GL texture -- e.g. the widget's uploaded native-
        resolution "before" texture), returns the GL texture holding the
        final graded result. An empty/fully-disabled stack returns
        `input_texture` UNCHANGED, no render pass at all -- same "empty
        stack -> image unchanged" contract apply_stack_to_image_array
        itself guarantees.

        Walks self.stack.stages directly now (not a pre-filtered
        `[s for s in stack.stages if s.enabled]` list, the way this
        method did before fork support) -- a DISABLED fork_split's whole
        region has to be skipped as one unit regardless of what its
        OWN-enabled interior stages say (see stack.py's _run_stages,
        "STACK FORKING"), which a flat enabled-only pre-filter can't
        express: an interior stage individually marked enabled=True
        would otherwise wrongly survive the filter and get rendered as
        if it were an ordinary top-level stage. Enabled/disabled is
        instead checked contextually at each step below, mirroring
        _run_stages itself.

        FORK RENDERING: hitting an ENABLED fork_split snapshots the
        current (pre-split) texture into _fork_snapshot_textures[0] (via
        _copy_texture) BEFORE rendering either branch -- required
        because both branches render through the SAME shared ping-pong
        pair _render_stage_sequence uses, one after another, which would
        otherwise silently overwrite the pre-split texture once the
        first branch's own rendering cycles back through both ping-pong
        slots. Branch A renders first (from that stable snapshot), and
        its OWN final result is immediately snapshotted into
        _fork_snapshot_textures[1] for the same reason -- Branch B is
        about to reuse the ping-pong pair too. Branch B's own final
        result doesn't need a snapshot; it's the last thing rendered
        before the merge pass consumes it. The merge pass itself (see
        gpu_fork_merge_shader.py) blends the two branch snapshots via
        fork_merge's own `blend`, then that combined result against the
        pre-split snapshot via fork_merge's own `opacity` -- exactly
        stack.py's own _run_stages fork_merge branch, just as one GLSL
        pass instead of two Python-level array operations.

        A DISABLED fork_merge is a special case worth its own path:
        _run_stages computes both branches fully but then discards them
        (the final "running = ..." assignment is skipped, `running`
        stays exactly what it was pre-split) -- there's no reason to pay
        for rendering either branch at all here if the result is
        thrown away regardless, so this skips straight to re-using the
        pre-split snapshot as the region's result, unlike the CPU path
        (which still does the wasted work, just doesn't act on it)."""
        stages = stack.stages
        n = len(stages)
        if n == 0:
            return input_texture

        if self._vbo is None:
            self._vbo = gsp.make_fullscreen_quad_vbo()
        self._ensure_ping_pong(w, h)

        # Capture the AMBIENT framebuffer binding (Qt's own internal FBO
        # for this widget, when called from paintGL) once, up front, and
        # restore it once at the end -- NOT "0" in between, which is what
        # a real, reproduced OpenGL.error.GLError(GL_INVALID_FRAMEBUFFER_
        # OPERATION) traced back to: framebuffer 0 is not a valid
        # "default" to leave things bound to inside a QOpenGLWidget's
        # paintGL (its actual render target is an internally-managed FBO,
        # never system framebuffer 0 the way a plain top-level GL context
        # would have) -- WipeCompositor.render's own capture of "whatever
        # was bound" would silently capture "0" here instead of the real
        # target if THIS function left it bound to 0, since render_chain
        # runs (via _SideState.ensure_texture) BEFORE WipeCompositor.
        # render is even called. Every stage pass now targets its own
        # ping-pong FBO directly without ever touching the ambient
        # binding until this one restore at the very end.
        ambient_fbo = gl.glGetIntegerv(gl.GL_FRAMEBUFFER_BINDING)

        current_texture = input_texture
        output_index = 0
        i = 0
        while i < n:
            stage = stages[i]

            if stage.kind == "fork_split":
                merge_i = i + 1
                while merge_i < n and stages[merge_i].kind != "fork_merge":
                    merge_i += 1
                # merge_i < n is guaranteed here -- caller already
                # confirmed is_stack_gpu_eligible(stack), which rejects
                # an unmatched split.
                if not stage.enabled:
                    i = merge_i + 1  # whole region is a no-op -- current_texture untouched
                    continue
                merge_stage = stages[merge_i]
                interior = stages[i + 1:merge_i]
                branch_a = [s for s in interior if s.branch == "a" and s.enabled]
                branch_b = [s for s in interior if s.branch == "b" and s.enabled]

                self._ensure_fork_snapshots(w, h)
                self._copy_texture(current_texture, self._fork_snapshot_fbos[0], w, h)
                pre_split_snapshot = self._fork_snapshot_textures[0]

                if not merge_stage.enabled:
                    # See this method's own docstring -- discarded either
                    # way, so skip actually rendering either branch.
                    target_fbo = self._ping_pong_fbos[output_index]
                    self._copy_texture(pre_split_snapshot, target_fbo, w, h)
                    current_texture = self._ping_pong_textures[output_index]
                    output_index = 1 - output_index
                    i = merge_i + 1
                    continue

                branch_a_result = self._render_stage_sequence(branch_a, pre_split_snapshot, w, h)
                self._copy_texture(branch_a_result, self._fork_snapshot_fbos[1], w, h)
                branch_a_snapshot = self._fork_snapshot_textures[1]

                branch_b_result = self._render_stage_sequence(branch_b, pre_split_snapshot, w, h)

                if self._fork_merge_program is None:
                    self._fork_merge_program = gfm.compile_fork_merge_program()
                blend = float(merge_stage.params.get("blend", 0.5))
                target_fbo = self._ping_pong_fbos[output_index]
                gfm.render_fork_merge_pass(
                    self._fork_merge_program, pre_split_snapshot, branch_a_snapshot, branch_b_result,
                    blend, merge_stage.opacity, target_fbo, w, h, self._vbo)
                current_texture = self._ping_pong_textures[output_index]
                output_index = 1 - output_index
                i = merge_i + 1
                continue

            if not stage.enabled:
                i += 1
                continue
            program = self._program_for_stage(stage)
            set_uniforms = self._set_uniforms_for_stage(stage)
            target_fbo = self._ping_pong_fbos[output_index]
            self._render_stage_pass(program, current_texture, target_fbo, w, h, set_uniforms, self._vbo)
            current_texture = self._ping_pong_textures[output_index]
            output_index = 1 - output_index
            i += 1

        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, ambient_fbo)
        return current_texture

    @staticmethod
    def _render_stage_pass(program, input_texture, target_fbo, w, h, set_uniforms, vbo):
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, target_fbo)
        gl.glViewport(0, 0, w, h)
        gl.glUseProgram(program)
        gl.glUniform1f(gl.glGetUniformLocation(program, "uStageOpacity"), 1.0)  # default, see set_uniforms
        if set_uniforms is not None:
            set_uniforms(program)
        gl.glActiveTexture(gl.GL_TEXTURE0)
        gl.glBindTexture(gl.GL_TEXTURE_2D, input_texture)
        gl.glUniform1i(gl.glGetUniformLocation(program, "uInput"), 0)
        aPos_loc = gsp.bind_fullscreen_quad(vbo, program)
        gl.glDrawArrays(gl.GL_TRIANGLE_STRIP, 0, 4)
        gsp.unbind_fullscreen_quad(aPos_loc)
        # No framebuffer-0 rebind here -- see render_chain's own comment
        # on why "0" is never a safe ambient value to leave things in,
        # and why restoring the ambient binding is this METHOD's caller's
        # job (render_chain), done exactly once, not per stage pass.

    def clear_gl_resources(self):
        """Call before this instance's GL context goes away (e.g.
        WipeCompareWidget being destroyed) -- every compiled program and
        render target held here is only valid for that one context."""
        self._delete_ping_pong()
        self._delete_fork_snapshots()
        if self._blit_source_fbo is not None:
            gl.glDeleteFramebuffers(1, [self._blit_source_fbo])
            self._blit_source_fbo = None
        if self._fork_merge_program is not None:
            gl.glDeleteProgram(self._fork_merge_program)
            self._fork_merge_program = None
        if self._vbo is not None:
            gl.glDeleteBuffers(1, [self._vbo])
            self._vbo = None
        self._cdl_program.clear()
        self._aces_drt_cache.clear()
        self._colorspace_cache.clear()
        self._grading_primary_cache.clear()
        self._exposure_contrast_cache.clear()
        self._grading_tone_cache.clear()
        self._grading_rgb_curve_cache.clear()
        self._lut3d_cache.clear()
        self._printer_lights_cache.clear()
        self._custom_drt_cache.clear()
