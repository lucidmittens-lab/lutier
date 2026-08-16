"""
gpu_color_warper_shader.py -- GPU path for "color_warper" (WARPier)
stages, following gpu_printer_lights_shader.py's own established pattern
for a stage kind that needs work beyond a single OCIO-generated shader
(see that module's docstring for the general shape this one mirrors).

WHAT THIS BUILDS: color_warper.apply_color_warper_in_working_space is
"convert working_colourspace -> REFERENCE_LINEAR_SPACE (one OCIO-composed
hop, primaries AND encoding) -> OKLCh warp -> convert back". There's no
OCIO-native transform for an arbitrary, variable-count-of-points OKLCh
warp with a live-editable wake/dropoff falloff, so -- same split as
printer_lights' gain -- this module composes TWO separately-extracted
OCIO ColorSpaceTransform shaders (working_colourspace <-> REFERENCE_
LINEAR_SPACE, see color_warper.py's own docstring for why a real gamut
conversion is needed here, unlike printer_lights' deliberately same-gamut
decode/encode) with a HAND-WRITTEN GLSL middle implementing the OKLab
matrices + warp blend math, ported line-for-line from color_warper.py's
own numpy implementation (same published Ottosson constants).

POINT COUNT: point data (anchor h0/c0, target h/c) is pushed as two
fixed-size uniform arrays (MAX_POINTS = color_warper.MAX_POINTS, the same
UI-side cap the wheel widget enforces), plus a uPointCount uniform the
warp loop's `if (i >= uPointCount) break;` respects -- unused slots are
simply never read. This means the shader's own SOURCE never changes as
points are added/dragged/removed (only the uniform values do), so the
compiled-program cache below fingerprints on (config_name,
working_colourspace) ONLY, same "shader structure doesn't change when a
control moves" bar every other continuously-edited kind in this app
meets (see printer_lights' own FINGERPRINT section).

GPU ELIGIBILITY REQUIRES OCIO, same reasoning as printer_lights.py:
color_warper.py's colour-science fallback (colorspace.convert) has no GPU
equivalent, and OCIO's own ColorSpaceTransform codegen is the only source
of the decode/encode shaders here. is_color_warper_gpu_eligible actually
builds the two shaders (via the cache) to decide eligibility, same
"eligibility checks do real inspection" precedent every other GPU-kind
eligibility check here follows.
"""
import numpy as np
import OpenGL.GL as gl
import PyOpenColorIO as ocio

import aces_drt
import color_warper
import gpu_shader_prototype as gsp

MAX_POINTS = color_warper.MAX_POINTS

_UNIFORM_POINT_COUNT = "uCwPointCount"
_UNIFORM_POINT_ANCHOR = "uCwPointAnchor"
_UNIFORM_POINT_TARGET = "uCwPointTarget"
_UNIFORM_WAKE = "uCwWake"
_UNIFORM_DROPOFF = "uCwDropoff"

# Ported line-for-line from color_warper.py's own linear_srgb_to_oklab/
# oklab_to_linear_srgb/oklab_to_oklch/oklch_to_oklab/falloff_weight/
# warp_hue_chroma -- see that module's docstring for the published OKLab
# reference matrices and the falloff/warp math itself. cwCbrtSafe exists
# because GLSL's pow() is undefined for a negative base (unlike numpy's
# cbrt, which returns the real root, needed here since an out-of-gamut
# color's LMS components can go negative).
_WARP_GLSL = f"""
float cwCbrtSafe(float x) {{
    return sign(x) * pow(abs(x), 1.0 / 3.0);
}}

vec3 cwLinearToOklab(vec3 rgb) {{
    float l = dot(rgb, vec3(0.4122214708, 0.5363325363, 0.0514459929));
    float m = dot(rgb, vec3(0.2119034982, 0.6806995451, 0.1073969566));
    float s = dot(rgb, vec3(0.0883024619, 0.2817188376, 0.6299787005));
    float l_ = cwCbrtSafe(l);
    float m_ = cwCbrtSafe(m);
    float s_ = cwCbrtSafe(s);
    float L = dot(vec3(l_, m_, s_), vec3(0.2104542553, 0.7936177850, -0.0040720468));
    float a = dot(vec3(l_, m_, s_), vec3(1.9779984951, -2.4285922050, 0.4505937099));
    float b = dot(vec3(l_, m_, s_), vec3(0.0259040371, 0.7827717662, -0.8086757660));
    return vec3(L, a, b);
}}

vec3 cwOklabToLinear(vec3 lab) {{
    float l_ = lab.x + 0.3963377774 * lab.y + 0.2158037573 * lab.z;
    float m_ = lab.x - 0.1055613458 * lab.y - 0.0638541728 * lab.z;
    float s_ = lab.x - 0.0894841775 * lab.y - 1.2914855480 * lab.z;
    float l = l_ * l_ * l_;
    float m = m_ * m_ * m_;
    float s = s_ * s_ * s_;
    float r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s;
    float g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s;
    float b = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s;
    return vec3(r, g, b);
}}

float cwAngularDelta(float h0, float h1) {{
    return mod(h1 - h0 + 180.0, 360.0) - 180.0;
}}

float cwFalloffWeight(float dd, float wake, float dropoff) {{
    float n = 1.0 + dropoff * 7.0;
    float sigma = max(wake, 1e-6);
    return exp(-0.5 * pow(abs(dd) / sigma, 2.0 * n));
}}

uniform int {_UNIFORM_POINT_COUNT};
uniform vec2 {_UNIFORM_POINT_ANCHOR}[{MAX_POINTS}];
uniform vec2 {_UNIFORM_POINT_TARGET}[{MAX_POINTS}];
uniform float {_UNIFORM_WAKE};
uniform float {_UNIFORM_DROPOFF};

vec3 cwWarp(vec3 linearRgb) {{
    vec3 lab = cwLinearToOklab(linearRgb);
    float lightness = lab.x;
    float chroma = length(lab.yz);
    float hue = mod(degrees(atan(lab.z, lab.y)), 360.0);

    float weightSum = 0.0;
    float hueDeltaSum = 0.0;
    float chromaScaleSum = 0.0;
    for (int i = 0; i < {MAX_POINTS}; i++) {{
        if (i >= {_UNIFORM_POINT_COUNT}) break;
        float h0 = {_UNIFORM_POINT_ANCHOR}[i].x;
        float c0 = {_UNIFORM_POINT_ANCHOR}[i].y;
        float dd = cwAngularDelta(h0, hue);
        float w = cwFalloffWeight(dd, {_UNIFORM_WAKE}, {_UNIFORM_DROPOFF});
        float hueDelta = cwAngularDelta(h0, {_UNIFORM_POINT_TARGET}[i].x);
        float cScale = c0 > 1e-6 ? ({_UNIFORM_POINT_TARGET}[i].y / c0) : 1.0;
        weightSum += w;
        hueDeltaSum += w * hueDelta;
        chromaScaleSum += w * cScale;
    }}

    // Implicit identity contribution (hueDelta=0, chromaScale=1) with
    // weight max(0, 1-weightSum) -- see color_warper.warp_hue_chroma's
    // own docstring on why: without this, a SINGLE active point's
    // weighted average degenerates to that one point's own raw delta
    // almost everywhere (w*x/w == x for any w > 0), rigidly rotating the
    // whole hue circle instead of tapering with distance.
    float identityWeight = max(0.0, 1.0 - weightSum);
    float totalWeight = weightSum + identityWeight;
    float hueOut = hue + hueDeltaSum / totalWeight;
    float chromaScaleOut = (chromaScaleSum + identityWeight) / totalWeight;
    hueOut = mod(hueOut, 360.0);
    float chromaOut = max(0.0, chroma * chromaScaleOut);

    vec3 labOut = vec3(lightness, chromaOut * cos(radians(hueOut)), chromaOut * sin(radians(hueOut)));
    return cwOklabToLinear(labOut);
}}
"""


class _CacheEntry:
    __slots__ = ("program", "decode_gpu_processor", "encode_gpu_processor")

    def __init__(self, program, decode_gpu_processor, encode_gpu_processor):
        self.program = program
        self.decode_gpu_processor = decode_gpu_processor
        self.encode_gpu_processor = encode_gpu_processor


def _generate_combined_shader_text(config_name, working_colourspace):
    """The GL-CONTEXT-FREE half of building a cache entry -- see
    gpu_printer_lights_shader.py's own docstring on why this is split
    from _build_entry (a real, reproduced SIGSEGV from calling a GL
    compile with no current context) and why is_color_warper_gpu_eligible
    only calls this half.

    Raises (an OCIO ocio.Exception, or an explicit RuntimeError for the
    "needs a texture" case this module doesn't support) if
    `working_colourspace` isn't actually GPU-eligible."""
    cfg = aces_drt._get_config(config_name)
    reference = color_warper.REFERENCE_LINEAR_SPACE

    decode_processor = cfg.getProcessor(
        ocio.ColorSpaceTransform(src=working_colourspace, dst=reference))
    encode_processor = cfg.getProcessor(
        ocio.ColorSpaceTransform(src=reference, dst=working_colourspace))

    decode_text, decode_tex, decode_tex3d, _decode_uni = gsp._extract_ocio_glsl(
        decode_processor, function_name="OCIODecodeToLinear", resource_prefix="CWDecode_")
    encode_text, encode_tex, encode_tex3d, _encode_uni = gsp._extract_ocio_glsl(
        encode_processor, function_name="OCIOEncodeFromLinear", resource_prefix="CWEncode_")
    if decode_tex or decode_tex3d or encode_tex or encode_tex3d:
        raise RuntimeError(
            f"color_warper GPU path doesn't support a working colourspace "
            f"({working_colourspace!r}) whose decode/encode needs a texture")

    combined_shader_text = f"""
{decode_text}

{encode_text}

{_WARP_GLSL}

vec4 OCIOMain(vec4 inPixel) {{
    vec4 linearColor = OCIODecodeToLinear(inPixel);
    linearColor.rgb = cwWarp(linearColor.rgb);
    return OCIOEncodeFromLinear(linearColor);
}}
"""
    return combined_shader_text, decode_processor, encode_processor


def _build_entry(config_name, working_colourspace):
    """The full build, INCLUDING the real GL compile -- only call this
    with a current OpenGL context, same convention as
    gpu_printer_lights_shader._build_entry."""
    combined_shader_text, decode_processor, encode_processor = _generate_combined_shader_text(
        config_name, working_colourspace)
    program = gsp.compile_ocio_program(combined_shader_text)
    return _CacheEntry(program, decode_processor.getDefaultGPUProcessor(), encode_processor.getDefaultGPUProcessor())


def is_color_warper_gpu_eligible(config_name, working_colourspace):
    """True if a "color_warper" stage using this (config_name,
    working_colourspace) can render on the GPU. Safe to call with NO
    current GL context -- generates and inspects the GLSL text a real
    build would compile, never actually compiles it."""
    if not aces_drt.OCIO_AVAILABLE:
        return False
    try:
        _generate_combined_shader_text(config_name, working_colourspace)
        return True
    except Exception:
        return False


class ColorWarperGpuCache:
    """Compiled-program cache for "color_warper" stages, fingerprinted on
    (config_name, working_colourspace) ONLY -- see module docstring's
    POINT COUNT section for why point/wake/dropoff values never touch
    this cache. Meant to live for the lifetime of one GL context, same
    convention as every other per-kind cache in this app."""

    def __init__(self):
        self._entries = {}

    def get_entry(self, config_name, working_colourspace):
        fingerprint = (config_name, working_colourspace)
        entry = self._entries.get(fingerprint)
        if entry is not None:
            return entry
        entry = _build_entry(config_name, working_colourspace)
        self._entries[fingerprint] = entry
        return entry

    def clear(self):
        """Call before this cache's owning GL context goes away -- every
        compiled program held here is only valid for that one context."""
        for entry in self._entries.values():
            gl.glDeleteProgram(entry.program)
        self._entries.clear()


def set_color_warper_uniforms(gl_module, program, points, wake, dropoff, opacity=1.0):
    """Pushes this stage's actual point/wake/dropoff values onto whichever
    program render_stage_pass has already made current -- takes `gl_module`
    as a parameter (not this module's own top-level `gl` import) so this
    stays callable via functools.partial the same way gpu_cdl_shader.
    set_cdl_uniforms is, from a caller that already has its own `gl`
    reference. Point arrays beyond len(points) are left at whatever the
    driver defaults an unset uniform array slot to (never read anyway,
    since the shader's own loop breaks at uPointCount)."""
    count = min(len(points), MAX_POINTS)
    anchor_flat = np.asarray(
        [v for p in points[:count] for v in (p["h0"], p["c0"])], dtype=np.float32)
    target_flat = np.asarray(
        [v for p in points[:count] for v in (p["h"], p["c"])], dtype=np.float32)

    gl_module.glUniform1i(gl_module.glGetUniformLocation(program, _UNIFORM_POINT_COUNT), count)
    if count > 0:
        gl_module.glUniform2fv(
            gl_module.glGetUniformLocation(program, _UNIFORM_POINT_ANCHOR), count, anchor_flat)
        gl_module.glUniform2fv(
            gl_module.glGetUniformLocation(program, _UNIFORM_POINT_TARGET), count, target_flat)
    gl_module.glUniform1f(gl_module.glGetUniformLocation(program, _UNIFORM_WAKE), float(wake))
    gl_module.glUniform1f(gl_module.glGetUniformLocation(program, _UNIFORM_DROPOFF), float(dropoff))
    gl_module.glUniform1f(gl_module.glGetUniformLocation(program, "uStageOpacity"), float(opacity))
