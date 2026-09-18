"""
gpu_custom_drt_shader.py -- GPU path for "custom_drt" ("CAMier") stages,
following gpu_printer_lights_shader.py's own established pattern for a
stage kind that needs work beyond a single OCIO-generated shader: ONE
OCIO-generated shader (working_colourspace -> Linear Rec.2020) composed
with a hand-written GLSL port of custom_drt.py's own tonescale/highlight-
desaturation/gamut-compression/output-encoding math, all wrapped into one
OCIOMain(vec4)->vec4 function -- same "OCIO for the part that needs
OCIO's own reference conversion, hand-written uniforms for the part that
needs to be continuously live-editable without recompiling" split every
other GPU-accelerated custom stage in this app (CDL, printer_lights)
already uses.

Every one of custom_drt.py's numeric params (contrast, headroom,
blend_percentile, tail_width, desat_start, desat_end, identity_zone,
transition_softness, fullness_r/g/b, mastering_brightness) is a plain
`uniform float`/`uniform vec3` here, pushed fresh every render via
set_custom_drt_uniforms -- never baked into the shader source, so
dragging any CAMier slider, or switching mastering_brightness, never
triggers a recompile (same bar CDL/printer_lights meet).

`mastering_display` and `final_target`, unlike the sliders above,
genuinely ARE baked into the shader source (see
_format_matrix_glsl/_format_eotf_glsl) -- both the Rec.2020-to-mastering-
gamut matrix (cdrRec2020ToMastering) and the Final Target step (matrix +
EOTF, cdrFinalTarget) have to be compile-time GLSL, not something to
reconstruct per-pixel from uniforms when they're picked, not dragged,
exactly the same "picked, not continuously edited" reasoning
gpu_stack_renderer.py's OcioGpuStageCache already applies to
working_colourspace/display/view combos elsewhere in this app).
FINGERPRINT for CustomDrtGpuCache is therefore (config_name,
working_colourspace, mastering_display, final_target) --
mastering_brightness stays out of it since it's a plain uniform, matching
gpu_printer_lights_shader.PrinterLightsGpuCache's own "only the
shader-STRUCTURE-changing choices" fingerprint philosophy.

GLSL 1.20 HAS NO isinf/isnan (added in GLSL 1.30) -- custom_drt.py's own
CPU _ray_box_t relies on real IEEE infinity plus an explicit np.isfinite
guard downstream (see that function's docstring: a perfectly neutral
pixel's ray never exits the gamut cube, so it correctly computes +inf,
and inf-inf is NaN, not 0, in float math -- a real bug Tom hit and this
module's Python sibling now guards against). rayBoxT below sidesteps the
whole problem instead of porting the guard: it starts from a large FINITE
sentinel (1e6, exactly the same stand-in custom_drt.py substitutes for an
actual infinity) rather than ever computing a real division-produced
infinity, so every downstream computation here is finite by construction
and needs no separate isfinite check to match the CPU reference's actual
numeric behavior.

MATRIX MULTIPLY -- the generated cdrRec2020ToMastering/cdrFinalTarget
matrix products are written as three explicit scalar dot products, NOT
`mat3(...) * rgb` -- GLSL's mat3(a,b,c,d,e,f,g,h,i) constructor fills
COLUMNS, not rows, so a naive transcription of custom_drt.py's row-major
Python matrix literals into 9 scalars would silently compute the
TRANSPOSED matrix product. Explicit components have no such ambiguity
and read as a direct, checkable transcription of the same 3 rows.

PQ (ST 2084) -- _PQ_GLSL below is a direct transcription of custom_drt.
py's own verified _PQ_M1/_PQ_M2/_PQ_C1/_PQ_C2/_PQ_C3 constants (already
checked there against published reference points), reached via a runtime
branch on uOutputEncoding rather than two separately-compiled shader
variants -- see module docstring above on why that stays a uniform, not
part of the fingerprint.
"""
import OpenGL.GL as gl
import PyOpenColorIO as ocio

import aces_drt
import custom_drt
import gpu_shader_prototype as gsp

# GLSL port of custom_drt.py's own tonescale/desaturation math -- see that
# module's docstring for the pipeline; every formula here is a direct
# transcription of its Python counterpart, same variable names where GLSL
# allows. Shared across every mastering_display/final_target combination
# (those matrices + the Final Target EOTF choice are generated
# separately, see _format_rec2020_to_mastering_glsl/_format_final_target_glsl).
_SHARED_GLSL = """
uniform float uContrast;
uniform float uHeadroom;
uniform float uBlendPercentile;
uniform float uTailWidth;
uniform float uDesatStart;
uniform float uDesatEnd;
uniform float uIdentityZone;
uniform float uTransitionSoftness;
uniform vec3 uFullness;
uniform float uMasteringBrightness;

const float CDR_GREY = 0.18;

float cdrToStops(float x) {
    return log2(max(x, 1e-8) / CDR_GREY);
}

float cdrHyperbolicTonescale(float stops, float contrast, float headroom) {
    float x = CDR_GREY * pow(2.0, stops);
    float s = CDR_GREY * pow(2.0, headroom);
    float p = 0.7 + contrast * 1.6;
    float v = x / (x + s);
    return pow(max(v, 0.0), p);
}

float cdrHyperbolicInverseStops(float y, float contrast, float headroom) {
    float s = CDR_GREY * pow(2.0, headroom);
    float p = 0.7 + contrast * 1.6;
    float v = pow(y, 1.0 / p);
    float x = s * v / (1.0 - v);
    return log2(x / CDR_GREY);
}

float cdrSigmoidTail(float stops, float s0, float y0, float width) {
    float t = (stops - s0) / width;
    return y0 + (1.0 - y0) * (2.0 / (1.0 + exp(-t)) - 1.0);
}

float cdrHybridTonescale(float stops, float contrast, float headroom, float blendPercentile, float tailWidth) {
    float hyp = cdrHyperbolicTonescale(stops, contrast, headroom);
    if (hyp < blendPercentile) {
        return hyp;
    }
    float s0 = cdrHyperbolicInverseStops(blendPercentile, contrast, headroom);
    return cdrSigmoidTail(stops, s0, blendPercentile, tailWidth);
}

vec3 cdrTonemapAndDesaturate(vec3 rgb, float contrast, float headroom, float blendPercentile,
                              float tailWidth, float desatStart, float desatEnd) {
    float norm = max(max(rgb.r, rgb.g), rgb.b);
    float normSafe = max(norm, 1e-8);
    float outNorm = cdrHybridTonescale(cdrToStops(normSafe), contrast, headroom, blendPercentile, tailWidth);
    vec3 toned = rgb * (outNorm / normSafe);

    float span = max(desatEnd - desatStart, 1e-6);
    float t = clamp((outNorm - desatStart) / span, 0.0, 1.0);
    float desatAmount = t * t * (3.0 - 2.0 * t);
    return mix(toned, vec3(outNorm), desatAmount);
}

// Smallest positive t such that anchor + t*direction reaches the [0,1]^3
// box boundary -- starts from a large finite sentinel rather than ever
// computing a real infinity, see this module's own docstring. eps is
// RELATIVE to the pixel's own brightness (anchor), not a fixed absolute
// value -- see custom_drt._ray_box_t's own (much longer) docstring for
// the real, reproduced bug this fixes: a fixed-absolute epsilon is
// tighter than the actual floating-point noise floor between this GLSL
// decode and its CPU/numpy sibling, so a near-but-not-quite-neutral
// pixel (real ACEScct decode noise, ~1e-6 relative) got treated as a
// genuine direction, and dividing by something that close to zero is a
// near-singularity -- CPU and GPU's own independently-signed/-sized
// noise at that scale then amplifies into wildly different (not just
// slightly different) results for what should be the same neutral ray.
float cdrRayBoxT(vec3 anchor, vec3 dir) {
    vec3 eps = max(abs(anchor), vec3(1e-6)) * 1e-4;
    float t = 1.0e6;
    if (dir.r > eps.r) { t = min(t, (1.0 - anchor.r) / dir.r); }
    else if (dir.r < -eps.r) { t = min(t, (0.0 - anchor.r) / dir.r); }
    if (dir.g > eps.g) { t = min(t, (1.0 - anchor.g) / dir.g); }
    else if (dir.g < -eps.g) { t = min(t, (0.0 - anchor.g) / dir.g); }
    if (dir.b > eps.b) { t = min(t, (1.0 - anchor.b) / dir.b); }
    else if (dir.b < -eps.b) { t = min(t, (0.0 - anchor.b) / dir.b); }
    return t;
}

vec3 cdrRadialCompress(vec3 rgb2020, float identityZone, float transitionSoftness, vec3 fullness) {
    float N = max(max(rgb2020.r, rgb2020.g), rgb2020.b);
    vec3 anchor = vec3(N);
    vec3 dir2020 = rgb2020 - anchor;
    vec3 rawTarget = cdrRec2020ToMastering(rgb2020);
    vec3 dirTarget = rawTarget - anchor;

    float t2020 = cdrRayBoxT(anchor, dir2020);
    float tTarget = cdrRayBoxT(anchor, dirTarget);

    float split = tTarget * identityZone;
    float w = tTarget * transitionSoftness;
    float lo = split - w / 2.0;
    float hi = split + w / 2.0;
    float denom = max(t2020 - split, 1e-6);
    float compressAt1 = split + (1.0 - split) / denom * (tTarget - split);

    float span = max(hi - lo, 1e-9);
    float x = clamp((1.0 - lo) / span, 0.0, 1.0);
    float sstep = x * x * (3.0 - 2.0 * x);
    float weight;
    if (1.0 <= lo) {
        weight = 0.0;
    } else if (1.0 >= hi) {
        weight = 1.0;
    } else {
        weight = sstep;
    }
    float tOut = (1.0 - weight) + compressAt1 * weight;

    // cdrRayBoxT never actually produces IEEE infinity (see its own
    // comment) -- it starts at the 1.0e6 sentinel and only ever LOWERS
    // it via min(), so tTarget still being exactly that literal means
    // this ray genuinely never crosses (every channel's direction was
    // within eps of zero). Without this guard, the fullness push below
    // computes fullness*(1.0e6 - tOut) -- a huge, wrong value -- for
    // EXACTLY this case; a real, reproduced bug (Tom, 2026-08-13) once
    // the wider relative eps above made "never crosses" common instead
    // of rare. Mirrors custom_drt._radial_compress's own finite_target
    // override exactly.
    bool neverCrosses = (tTarget >= 1.0e6);
    if (neverCrosses) {
        tOut = 1.0;
    }

    vec3 tOutPerChannel = vec3(tOut) + fullness * (vec3(tTarget) - vec3(tOut));
    if (neverCrosses) {
        tOutPerChannel = vec3(1.0);
    }
    return anchor + tOutPerChannel * dirTarget;
}

float cdrSrgbEncodeChannel(float v) {
    v = clamp(v, 0.0, 1.0);
    return v <= 0.0031308 ? v * 12.92 : 1.055 * pow(v, 1.0 / 2.4) - 0.055;
}

vec3 cdrSrgbEncode(vec3 rgb) {
    return vec3(cdrSrgbEncodeChannel(rgb.r), cdrSrgbEncodeChannel(rgb.g), cdrSrgbEncodeChannel(rgb.b));
}

// Simple power-law EOTF (Tom, 2026-08-13: "simple power law", not the
// fuller Rec.1886 EOTF with its black-level offset term) -- direct
// transcription of custom_drt._gamma_encode. `gamma` is a compile-time
// constant baked in per Final Target (see _format_eotf_glsl), not a
// uniform -- it's picked, not dragged.
vec3 cdrGammaEncode(vec3 rgb, float gamma) {
    vec3 v = clamp(rgb, 0.0, 1.0);
    return pow(v, vec3(1.0 / gamma));
}

// SMPTE ST 2084 (PQ) OETF -- direct transcription of custom_drt.py's own
// _PQ_M1/_PQ_M2/_PQ_C1/_PQ_C2/_PQ_C3 (verified there against published
// reference points before this module ever shipped with them).
const float CDR_PQ_M1 = 0.1593017578125;
const float CDR_PQ_M2 = 78.84375;
const float CDR_PQ_C1 = 0.8359375;
const float CDR_PQ_C2 = 18.8515625;
const float CDR_PQ_C3 = 18.6875;

float cdrPqEncodeChannel(float v, float masteringBrightness) {
    v = clamp(v, 0.0, 1.0);
    float absoluteNits = v * masteringBrightness;
    float L = absoluteNits / 10000.0;
    float Lm = pow(L, CDR_PQ_M1);
    return pow((CDR_PQ_C1 + CDR_PQ_C2 * Lm) / (1.0 + CDR_PQ_C3 * Lm), CDR_PQ_M2);
}

vec3 cdrPqEncode(vec3 rgb, float masteringBrightness) {
    return vec3(cdrPqEncodeChannel(rgb.r, masteringBrightness),
                cdrPqEncodeChannel(rgb.g, masteringBrightness),
                cdrPqEncodeChannel(rgb.b, masteringBrightness));
}
"""

# cdrFinalTarget (generated per mastering_display/final_target, see
# _format_final_target_glsl) calls cdrGammaEncode/cdrSrgbEncode/
# cdrPqEncode, all defined in _SHARED_GLSL above -- GLSL has no forward
# declarations for this codebase's style (explicit function bodies only,
# see module docstring on matrix products), so cdrFinalTarget's generated
# text must be assembled AFTER _SHARED_GLSL and BEFORE _OCIO_MAIN_GLSL,
# which in turn calls cdrFinalTarget itself. See _generate_combined_shader_text.
_OCIO_MAIN_GLSL = """
vec4 OCIOMain(vec4 inPixel) {
    vec4 rgb2020 = OCIODecodeToRec2020(inPixel);
    vec3 toned = cdrTonemapAndDesaturate(
        rgb2020.rgb, uContrast, uHeadroom, uBlendPercentile, uTailWidth, uDesatStart, uDesatEnd);
    vec3 compressed = cdrRadialCompress(toned, uIdentityZone, uTransitionSoftness, uFullness);
    // Still-linear compressed -> a direct primaries matrix (identity if
    // the mastering gamut already IS this Final Target's own gamut) ->
    // ONE EOTF encode, no intermediate decode/re-encode round trip. See
    // module + custom_drt.py docstrings.
    vec3 displayColor = cdrFinalTarget(compressed, uMasteringBrightness);
    return vec4(displayColor, inPixel.a);
}
"""


def _format_matrix_product_glsl(matrix, var_name):
    """`matrix` (a 3x3 numpy array, row-major) -> a GLSL expression for
    `matrix @ var_name`, written as three explicit scalar dot products
    (see this module's own docstring on why, not a mat3 constructor).
    `matrix=None` means identity -- just returns `var_name` unchanged."""
    if matrix is None:
        return var_name
    m = matrix
    rows = []
    for i in range(3):
        rows.append(
            f"{m[i][0]:.8f} * {var_name}.r + {m[i][1]:.8f} * {var_name}.g + {m[i][2]:.8f} * {var_name}.b")
    return "vec3(\n        " + ",\n        ".join(rows) + "\n    )"


def _format_rec2020_to_mastering_glsl(mastering_matrix):
    body = _format_matrix_product_glsl(mastering_matrix, "rgb")
    return f"vec3 cdrRec2020ToMastering(vec3 rgb) {{\n    return {body};\n}}\n"


def _format_final_target_glsl(final_target_spec):
    """`final_target_spec` (one of custom_drt.FINAL_TARGETS[mastering_display]
    values, e.g. {"matrix": ..., "eotf": ("pq", None)}) -> the
    cdrFinalTarget GLSL function: the Final Target's own compile-time
    matrix (or identity) applied to the still-linear radial-compress
    output, then that Final Target's own single EOTF encode -- see
    custom_drt.apply_custom_drt's own docstring for why no intermediate
    decode/re-encode round trip is needed."""
    matrix_expr = _format_matrix_product_glsl(final_target_spec["matrix"], "linear")
    eotf_kind, eotf_param = final_target_spec["eotf"]
    if eotf_kind == "pq":
        encode_expr = "cdrPqEncode(m, masteringBrightness)"
    elif eotf_kind == "srgb":
        encode_expr = "cdrSrgbEncode(m)"
    else:
        encode_expr = f"cdrGammaEncode(m, {float(eotf_param):.8f})"
    return (
        "vec3 cdrFinalTarget(vec3 linear, float masteringBrightness) {\n"
        f"    vec3 m = {matrix_expr};\n"
        f"    return {encode_expr};\n"
        "}\n"
    )


class _CacheEntry:
    __slots__ = ("program", "decode_gpu_processor")

    def __init__(self, program, decode_gpu_processor):
        self.program = program
        # Kept alive alongside the compiled program -- same "don't rely
        # on undocumented Python/C++ object lifetime guarantees in the
        # OCIO bindings" insurance every other GPU cache in this app takes.
        self.decode_gpu_processor = decode_gpu_processor


def _resolve_final_target(mastering_display, final_target):
    if mastering_display not in custom_drt.MASTERING_DISPLAYS:
        raise ValueError(
            f"Unknown mastering_display {mastering_display!r} "
            f"(expected one of {custom_drt.MASTERING_DISPLAYS})")
    if final_target is None:
        final_target = custom_drt.DEFAULT_FINAL_TARGET_FOR_DISPLAY[mastering_display]
    options = custom_drt.FINAL_TARGETS[mastering_display]
    if final_target not in options:
        raise ValueError(
            f"Unknown final_target {final_target!r} for mastering_display "
            f"{mastering_display!r} (expected one of {tuple(options)})")
    return final_target, options[final_target]


def _generate_combined_shader_text(config_name, working_colourspace, mastering_display, final_target):
    """The GL-CONTEXT-FREE half of building a cache entry -- see
    gpu_printer_lights_shader._generate_combined_shader_text's own
    docstring for why this split exists (a real SIGSEGV from calling a
    GL-compiling function with no current context)."""
    final_target, final_target_spec = _resolve_final_target(mastering_display, final_target)

    cfg = aces_drt._get_config(config_name)
    decode_processor = cfg.getProcessor(
        ocio.ColorSpaceTransform(src=working_colourspace, dst="Linear Rec.2020"))
    decode_text, decode_tex, decode_tex3d, _decode_uni = gsp._extract_ocio_glsl(
        decode_processor, function_name="OCIODecodeToRec2020", resource_prefix="CDR_")
    if decode_tex or decode_tex3d:
        # Not reachable for any working space in this app's own configs
        # as of this module's writing -- a plain-gamut-conversion
        # ColorSpaceTransform to a synthetic scene-linear space needs no
        # baked LUT/shaper texture, same "0 textures" shape aces_drt/
        # colorspace/printer_lights GPU shaders already confirmed for
        # their own conversions. This module has no texture-binding
        # plumbing, so surface it as an eligibility failure rather than
        # silently producing a shader missing a texture bind, same
        # precedent gpu_printer_lights_shader.py already set.
        raise RuntimeError(
            f"custom_drt GPU path doesn't support a working colourspace "
            f"({working_colourspace!r}) whose decode needs a texture")

    mastering_matrix_glsl = _format_rec2020_to_mastering_glsl(
        custom_drt._MASTERING_GAMUT_MATRIX[mastering_display])
    final_target_glsl = _format_final_target_glsl(final_target_spec)
    # Ordering matters -- see _OCIO_MAIN_GLSL's own comment: cdrFinalTarget
    # (final_target_glsl) calls encode helpers defined in _SHARED_GLSL, and
    # _OCIO_MAIN_GLSL's OCIOMain calls cdrFinalTarget, so this is the only
    # valid declare-before-use order.
    combined_shader_text = f"""
{decode_text}

{mastering_matrix_glsl}

{_SHARED_GLSL}

{final_target_glsl}

{_OCIO_MAIN_GLSL}
"""
    return combined_shader_text, decode_processor


def _build_entry(config_name, working_colourspace, mastering_display, final_target):
    """The full build, INCLUDING the real GL compile -- only call this
    with a current OpenGL context (see _generate_combined_shader_text's
    own docstring)."""
    combined_shader_text, decode_processor = _generate_combined_shader_text(
        config_name, working_colourspace, mastering_display, final_target)
    program = gsp.compile_ocio_program(combined_shader_text)
    return _CacheEntry(program, decode_processor.getDefaultGPUProcessor())


def is_custom_drt_gpu_eligible(config_name, working_colourspace, mastering_display, final_target):
    """True if a "custom_drt" stage using this (config_name,
    working_colourspace, mastering_display, final_target) can render on
    the GPU. Safe to call with NO current GL context -- generates and
    inspects GLSL text only, never compiles."""
    if not aces_drt.OCIO_AVAILABLE:
        return False
    try:
        _generate_combined_shader_text(config_name, working_colourspace, mastering_display, final_target)
        return True
    except Exception:
        return False


def set_custom_drt_uniforms(gl_module, program, contrast, headroom, blend_percentile,
                             tail_width, desat_start, desat_end, identity_zone,
                             transition_softness, fullness_r, fullness_g, fullness_b,
                             mastering_brightness):
    """Pushes every CAMier param as a live uniform -- called fresh every
    render (see gpu_stack_renderer.GpuStackRenderer._set_uniforms_for_
    stage), so no CAMier slider drag (or mastering_brightness pick) ever
    recompiles this stage's program, same bar gpu_cdl_shader.
    set_cdl_uniforms meets for CDL.

    Clamped the SAME way custom_drt.apply_custom_drt's own CPU path is
    (see that function's own comment, and custom_drt._BLEND_PERCENTILE_
    EPSILON) -- this GLSL mirror hand-ports the identical cdrHyperbolic
    InverseStops math, which is just as NaN/inf-prone at
    blend_percentile<=0 or >=1 on the GPU as it is on the CPU."""
    blend_percentile = min(max(float(blend_percentile), custom_drt._BLEND_PERCENTILE_EPSILON),
                            1.0 - custom_drt._BLEND_PERCENTILE_EPSILON)
    desat_start = min(max(float(desat_start), 0.0), 1.0)
    desat_end = min(max(float(desat_end), 0.0), 1.0)
    identity_zone = min(max(float(identity_zone), 0.0), 1.0)
    transition_softness = min(max(float(transition_softness), 0.0), 1.0)

    gl_module.glUniform1f(gl_module.glGetUniformLocation(program, "uContrast"), float(contrast))
    gl_module.glUniform1f(gl_module.glGetUniformLocation(program, "uHeadroom"), float(headroom))
    gl_module.glUniform1f(
        gl_module.glGetUniformLocation(program, "uBlendPercentile"), float(blend_percentile))
    gl_module.glUniform1f(gl_module.glGetUniformLocation(program, "uTailWidth"), float(tail_width))
    gl_module.glUniform1f(gl_module.glGetUniformLocation(program, "uDesatStart"), float(desat_start))
    gl_module.glUniform1f(gl_module.glGetUniformLocation(program, "uDesatEnd"), float(desat_end))
    gl_module.glUniform1f(
        gl_module.glGetUniformLocation(program, "uIdentityZone"), float(identity_zone))
    gl_module.glUniform1f(
        gl_module.glGetUniformLocation(program, "uTransitionSoftness"), float(transition_softness))
    gl_module.glUniform3f(
        gl_module.glGetUniformLocation(program, "uFullness"),
        float(fullness_r), float(fullness_g), float(fullness_b))
    gl_module.glUniform1f(
        gl_module.glGetUniformLocation(program, "uMasteringBrightness"), float(mastering_brightness))


class CustomDrtGpuCache:
    """Compiled-program cache for "custom_drt" stages, fingerprinted on
    (config_name, working_colourspace, mastering_display, final_target)
    -- see module docstring."""

    def __init__(self):
        self._entries = {}

    def get_entry(self, config_name, working_colourspace, mastering_display, final_target):
        fingerprint = (config_name, working_colourspace, mastering_display, final_target)
        entry = self._entries.get(fingerprint)
        if entry is not None:
            return entry
        entry = _build_entry(config_name, working_colourspace, mastering_display, final_target)
        self._entries[fingerprint] = entry
        return entry

    def clear(self):
        """Call before this cache's owning GL context goes away."""
        for entry in self._entries.values():
            gl.glDeleteProgram(entry.program)
        self._entries.clear()
