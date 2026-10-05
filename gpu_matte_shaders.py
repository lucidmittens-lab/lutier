"""
gpu_matte_shaders.py -- GPU rendering for the matte stages (Tom,
2026-10-05: "add GPU for matte stages"): Keyer, Matte Shape and Matte
Combine, plus the per-pixel matte blend any stage uses when its
matte_source_id points at one of them. Before this, any of those kinds
sent the WHOLE stack to the CPU path (gpu_stack_renderer's eligibility).
Segment Mask stays CPU-only -- its seeded region-growing is a flood
fill, not a per-pixel shader.

CPU references, reproduced pass by pass (see stack.py's "MATTE CHANNELS"):
  - matte_shape.compute_shape_matte -> shape pass (pixel-space geometry,
    rotation, super-Gaussian edge via color_warper.falloff_weight).
  - matte_combine.compute_combined_matte -> combine pass (a missing
    input reads as all-zero, same as stack._compute_raw_matte).
  - keyer.compute_matte -> optional prefilter blur of the image (two
    separable passes), the qualifier pass (OCIO decode to the reference
    linear space, OKLCh, hue/chroma/lightness terms), optional matte blur
    (two passes), and a finalize pass (clip black/white, invert, clamp).
  - stack._maybe_populate_matte multiplies every matte by its producing
    stage's opacity -- done in each producer's last pass.
  - stack._stage_blend_factor/_blend_running_toward -> blend pass:
    mix(before, stage_output, opacity * matte).

BLUR matches scipy.ndimage.gaussian_filter's defaults: truncate=4.0
(radius = int(4*sigma + 0.5)), normalized Gaussian weights, "reflect"
edges (d c b a | a b c d). Weights are computed in the shader; the
radius is capped at MAX_BLUR_RADIUS, which covers the Keyer UI's whole
0-25 range -- a larger value (only reachable from a hand-edited file)
keeps the stack on the CPU (blur_radius_for_sigma / eligibility).

Every matte lives in the R channel of an RGBA32F texture (G/B mirror it
so a matte is viewable as-is). Pixel coordinates are gl_FragCoord - 0.5,
which index exactly the same rows/columns as the CPU arrays, since
upload_rgba32f_texture puts array row 0 at texture row 0.
"""
import math

import OpenGL.GL as gl
import PyOpenColorIO as ocio

import aces_drt
import color_warper
import gpu_shader_prototype as gsp
from gpu_color_warper_shader import OKLAB_HELPERS_GLSL
import keyer
import matte_combine
import matte_shape

MAX_BLUR_RADIUS = 100  # = blur_radius_for_sigma(25.0), the Keyer UI's max

_COMBINE_OPS = {op: i for i, op in enumerate(matte_combine.OPS)}
_SHAPES = {shape: i for i, shape in enumerate(matte_shape.SHAPES)}


def blur_radius_for_sigma(sigma):
    """scipy.ndimage.gaussian_filter's own kernel radius (truncate=4.0)."""
    return int(4.0 * float(sigma) + 0.5)


def is_keyer_gpu_eligible(params, config_name=None):
    """True if a "keyer" stage with these params renders on the GPU: its
    working colourspace's decode is shader-only (no LUT textures), and
    both blurs fit MAX_BLUR_RADIUS. Safe with no current GL context."""
    if not aces_drt.OCIO_AVAILABLE:
        return False
    for key, default in (("prefilter_blur", keyer.DEFAULT_PREFILTER_BLUR),
                         ("matte_blur", keyer.DEFAULT_MATTE_BLUR)):
        if blur_radius_for_sigma(max(0.0, float(params.get(key, default)))) > MAX_BLUR_RADIUS:
            return False
    try:
        _keyer_shader_text(config_name or aces_drt.DEFAULT_CONFIG_NAME,
                           params.get("working_colourspace", keyer.DEFAULT_WORKING_COLOURSPACE))
        return True
    except Exception:  # noqa: BLE001 -- any OCIO failure just means "not on the GPU"
        return False


# ---- shaders ---------------------------------------------------------------

_SHAPE_FRAGMENT = """
#version 120
uniform vec2 uCenterPx;     // shape center, in pixels
uniform vec2 uHalfExtentPx; // half width/height, in pixels
uniform float uCosT;
uniform float uSinT;
uniform int uShape;         // 0 ellipse, 1 rectangle (matte_shape.SHAPES order)
uniform float uEdgeWidth;
uniform float uDropoff;
uniform int uInvert;
uniform float uOpacity;

float falloffWeight(float d, float wake, float dropoff) {
    float n = 1.0 + dropoff * 7.0;
    return exp(-0.5 * pow(abs(d) / max(wake, 1e-6), 2.0 * n));
}

void main() {
    vec2 d = (gl_FragCoord.xy - 0.5) - uCenterPx;
    vec2 r = vec2(d.x * uCosT - d.y * uSinT, d.x * uSinT + d.y * uCosT);
    vec2 nrm = r / uHalfExtentPx;
    float excess = uShape == 0
        ? max(0.0, length(nrm) - 1.0)
        : max(0.0, max(abs(nrm.x), abs(nrm.y)) - 1.0);
    float m = falloffWeight(excess, uEdgeWidth, uDropoff);
    if (uInvert == 1) m = 1.0 - m;
    m = clamp(m, 0.0, 1.0) * uOpacity;
    gl_FragColor = vec4(m, m, m, 1.0);
}
"""

_COMBINE_FRAGMENT = """
#version 120
varying vec2 vUV;
uniform sampler2D uMatteA;
uniform sampler2D uMatteB;
uniform int uHasA;
uniform int uHasB;
uniform int uOp;            // matte_combine.OPS order
uniform float uOpacity;

void main() {
    float a = uHasA == 1 ? texture2D(uMatteA, vUV).r : 0.0;
    float b = uHasB == 1 ? texture2D(uMatteB, vUV).r : 0.0;
    float m;
    if (uOp == 0) m = a * b;
    else if (uOp == 1) m = max(a, b);
    else if (uOp == 2) m = max(0.0, a - b);
    else m = abs(a - b);
    m = clamp(m, 0.0, 1.0) * uOpacity;
    gl_FragColor = vec4(m, m, m, 1.0);
}
"""

# Separable Gaussian, one axis per pass; reflect edges (see module docstring).
_BLUR_FRAGMENT = f"""
#version 120
uniform sampler2D uSource;
uniform vec2 uSize;         // texture size in pixels
uniform vec2 uAxis;         // (1,0) horizontal, (0,1) vertical
uniform int uRadius;
uniform float uSigma;

float reflectIndex(float i, float n) {{
    if (i < 0.0) return -i - 1.0;
    if (i >= n) return 2.0 * n - i - 1.0;
    return i;
}}

void main() {{
    vec2 px = gl_FragCoord.xy - 0.5;
    float n = dot(uSize, uAxis);
    float base = dot(px, uAxis);
    vec4 sum = vec4(0.0);
    float weightSum = 0.0;
    for (int k = -{MAX_BLUR_RADIUS}; k <= {MAX_BLUR_RADIUS}; k++) {{
        if (k < -uRadius || k > uRadius) continue;
        float w = exp(-0.5 * (float(k) * float(k)) / (uSigma * uSigma));
        vec2 samplePx = px + uAxis * (reflectIndex(base + float(k), n) - base);
        sum += w * texture2D(uSource, (samplePx + 0.5) / uSize);
        weightSum += w;
    }}
    gl_FragColor = vec4((sum / weightSum).rgb, 1.0);
}}
"""

_KEYER_FINALIZE_FRAGMENT = """
#version 120
varying vec2 vUV;
uniform sampler2D uRaw;
uniform float uClipBlack;
uniform float uClipWhite;
uniform int uInvert;
uniform float uOpacity;

void main() {
    float m = texture2D(uRaw, vUV).r;
    float span = uClipWhite - uClipBlack;
    m = span <= 1e-6 ? (m > uClipBlack ? 1.0 : 0.0) : clamp((m - uClipBlack) / span, 0.0, 1.0);
    if (uInvert == 1) m = 1.0 - m;
    m = clamp(m, 0.0, 1.0) * uOpacity;
    gl_FragColor = vec4(m, m, m, 1.0);
}
"""

_MATTE_BLEND_FRAGMENT = """
#version 120
varying vec2 vUV;
uniform sampler2D uBefore;
uniform sampler2D uStageOutput;
uniform sampler2D uMatte;
uniform float uOpacity;

void main() {
    vec4 before = texture2D(uBefore, vUV);
    vec4 after = texture2D(uStageOutput, vUV);
    gl_FragColor = mix(before, after, uOpacity * texture2D(uMatte, vUV).r);
}
"""

# The qualifier: an OCIOMain (so it goes through compile_ocio_program's
# usual template, rendered at uStageOpacity=1) writing the RAW matte --
# hue * chroma * lightness terms, before blur/clip/invert (keyer.compute_matte).
_KEYER_QUALIFIER_GLSL = """
uniform float uKyHueCenter;
uniform float uKyHueWidth;
uniform float uKyChromaMin;
uniform float uKyChromaMax;
uniform float uKyLightnessMin;
uniform float uKyLightnessMax;
uniform float uKyDropoff;
uniform float uKyChromaEdge;
uniform float uKyLightnessEdge;

float kyBand(float v, float lo, float hi, float edge) {
    float outside = max(0.0, lo - v) + max(0.0, v - hi);
    return cwFalloffWeight(outside, edge, uKyDropoff);
}

vec4 OCIOMain(vec4 inPixel) {
    vec3 lab = cwLinearToOklab(KYDecodeToLinear(inPixel).rgb);
    float L = lab.x;
    float C = length(lab.yz);
    float H = mod(degrees(atan(lab.z, lab.y)), 360.0);
    float hueTerm = cwFalloffWeight(cwAngularDelta(uKyHueCenter, H), uKyHueWidth, uKyDropoff);
    float m = hueTerm
        * kyBand(C, uKyChromaMin, uKyChromaMax, uKyChromaEdge)
        * kyBand(L, uKyLightnessMin, uKyLightnessMax, uKyLightnessEdge);
    return vec4(m, m, m, 1.0);
}
"""


def _keyer_shader_text(config_name, working_colourspace):
    """OCIOMain text for the qualifier -- GL-context-free (eligibility
    calls this). Raises if the decode needs LUT textures."""
    cfg = aces_drt._get_config(config_name)
    decode_processor = cfg.getProcessor(
        ocio.ColorSpaceTransform(src=working_colourspace, dst=color_warper.REFERENCE_LINEAR_SPACE))
    decode_text, decode_tex, decode_tex3d, _uniforms = gsp._extract_ocio_glsl(
        decode_processor, function_name="KYDecodeToLinear", resource_prefix="KYDecode_")
    if decode_tex or decode_tex3d:
        raise RuntimeError(
            f"Keyer GPU path doesn't support a working colourspace ({working_colourspace!r}) "
            "whose decode needs a texture")
    return f"{decode_text}\n{OKLAB_HELPERS_GLSL}\n{_KEYER_QUALIFIER_GLSL}"


# ---- renderer ----------------------------------------------------------------

class MatteRenderer:
    """Owns the matte passes' programs and scratch targets for one GL
    context (gpu_stack_renderer.GpuStackRenderer holds one). Matte
    textures themselves belong to the caller (one per producing stage)."""

    def __init__(self):
        self._programs = {}
        self._qualifier_programs = {}  # (config, working_colourspace) -> program
        self._scratch = []             # [(texture, fbo)] x 3 -- see render_keyer
        self._scratch_size = None

    def _program(self, name, fragment):
        program = self._programs.get(name)
        if program is None:
            program = gsp.link_custom_program(gsp.FULLSCREEN_QUAD_VERTEX_SHADER, fragment)
            self._programs[name] = program
        return program

    def _ensure_scratch(self, w, h):
        if self._scratch_size == (w, h):
            return
        self._delete_scratch()
        self._scratch = [gsp.create_rgba32f_render_target(w, h) for _ in range(3)]
        self._scratch_size = (w, h)

    def _delete_scratch(self):
        for tex, fbo in self._scratch:
            gl.glDeleteFramebuffers(1, [fbo])
            gl.glDeleteTextures([tex])
        self._scratch, self._scratch_size = [], None

    def clear_gl_resources(self):
        self._delete_scratch()
        for program in list(self._programs.values()) + list(self._qualifier_programs.values()):
            gl.glDeleteProgram(program)
        self._programs.clear()
        self._qualifier_programs.clear()

    @staticmethod
    def _draw(program, target_fbo, w, h, vbo, textures=(), uniforms=None):
        """One fullscreen pass: bind `target_fbo`, bind `textures` as
        (sampler_name, texture) pairs on units 0.., call uniforms(program)."""
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, target_fbo)
        gl.glViewport(0, 0, w, h)
        gl.glUseProgram(program)
        for unit, (name, texture) in enumerate(textures):
            gl.glActiveTexture(gl.GL_TEXTURE0 + unit)
            gl.glBindTexture(gl.GL_TEXTURE_2D, texture)
            gl.glUniform1i(gl.glGetUniformLocation(program, name), unit)
        if uniforms is not None:
            uniforms(program)
        loc = gsp.bind_fullscreen_quad(vbo, program)
        gl.glDrawArrays(gl.GL_TRIANGLE_STRIP, 0, 4)
        gsp.unbind_fullscreen_quad(loc)
        gl.glActiveTexture(gl.GL_TEXTURE0)

    def _blur(self, source_texture, sigma, w, h, vbo, via, into):
        """Separable Gaussian (sigma > 0) of `source_texture`, through
        scratch[via] into scratch[into] (both must differ from the
        source); returns scratch[into]'s texture."""
        program = self._program("blur", _BLUR_FRAGMENT)
        radius = blur_radius_for_sigma(sigma)

        def uniforms_for(axis):
            def set_uniforms(p):
                gl.glUniform2f(gl.glGetUniformLocation(p, "uSize"), float(w), float(h))
                gl.glUniform2f(gl.glGetUniformLocation(p, "uAxis"), *axis)
                gl.glUniform1i(gl.glGetUniformLocation(p, "uRadius"), radius)
                gl.glUniform1f(gl.glGetUniformLocation(p, "uSigma"), float(sigma))
            return set_uniforms

        # scipy blurs axis 0 (rows, i.e. vertical) first, then axis 1.
        self._draw(program, self._scratch[via][1], w, h, vbo,
                   [("uSource", source_texture)], uniforms_for((0.0, 1.0)))
        self._draw(program, self._scratch[into][1], w, h, vbo,
                   [("uSource", self._scratch[via][0])], uniforms_for((1.0, 0.0)))
        return self._scratch[into][0]

    # -- producers: each renders its final matte (x opacity) into target_fbo

    def render_shape(self, stage, target_fbo, w, h, vbo):
        p = stage.params
        width = max(1e-6, float(p.get("width", matte_shape.DEFAULT_WIDTH)))
        height = max(1e-6, float(p.get("height", matte_shape.DEFAULT_HEIGHT)))
        theta = math.radians(-float(p.get("rotation", matte_shape.DEFAULT_ROTATION)))
        softness = min(1.0, max(0.0, float(p.get("softness", matte_shape.DEFAULT_SOFTNESS))))
        shape = p.get("shape", matte_shape.DEFAULT_SHAPE)

        def set_uniforms(prog):
            u = lambda name: gl.glGetUniformLocation(prog, name)  # noqa: E731
            gl.glUniform2f(u("uCenterPx"), float(p.get("center_x", matte_shape.DEFAULT_CENTER_X)) * w,
                           float(p.get("center_y", matte_shape.DEFAULT_CENTER_Y)) * h)
            gl.glUniform2f(u("uHalfExtentPx"), max(1e-6, width * w / 2.0), max(1e-6, height * h / 2.0))
            gl.glUniform1f(u("uCosT"), math.cos(theta))
            gl.glUniform1f(u("uSinT"), math.sin(theta))
            gl.glUniform1i(u("uShape"), _SHAPES[shape])
            gl.glUniform1f(u("uEdgeWidth"), 0.05 + 0.3 * softness)
            gl.glUniform1f(u("uDropoff"), color_warper.softness_to_dropoff(softness))
            gl.glUniform1i(u("uInvert"), int(bool(p.get("invert", matte_shape.DEFAULT_INVERT))))
            gl.glUniform1f(u("uOpacity"), float(stage.opacity))

        self._draw(self._program("shape", _SHAPE_FRAGMENT), target_fbo, w, h, vbo, (), set_uniforms)

    def render_combine(self, stage, matte_a, matte_b, target_fbo, w, h, vbo):
        """`matte_a`/`matte_b`: textures, or None for a missing input."""
        op = stage.params.get("op", matte_combine.DEFAULT_OP)
        # A texture must be bound to each sampler even when unused.
        fallback = matte_a or matte_b or 0

        def set_uniforms(prog):
            u = lambda name: gl.glGetUniformLocation(prog, name)  # noqa: E731
            gl.glUniform1i(u("uHasA"), int(matte_a is not None))
            gl.glUniform1i(u("uHasB"), int(matte_b is not None))
            gl.glUniform1i(u("uOp"), _COMBINE_OPS[op])
            gl.glUniform1f(u("uOpacity"), float(stage.opacity))

        self._draw(self._program("combine", _COMBINE_FRAGMENT), target_fbo, w, h, vbo,
                   [("uMatteA", matte_a or fallback), ("uMatteB", matte_b or fallback)], set_uniforms)

    def render_keyer(self, stage, image_texture, target_fbo, w, h, vbo, config_name=None):
        p = stage.params
        config_name = config_name or aces_drt.DEFAULT_CONFIG_NAME
        working_colourspace = p.get("working_colourspace", keyer.DEFAULT_WORKING_COLOURSPACE)
        self._ensure_scratch(w, h)

        # Scratch routing: prefiltered image -> s1 (via s0); raw matte ->
        # s2; blurred matte -> s1 (via s0, the prefiltered image is done
        # with by then). No pass ever reads the target it writes.
        source = image_texture
        prefilter = max(0.0, float(p.get("prefilter_blur", keyer.DEFAULT_PREFILTER_BLUR)))
        if prefilter > 0.0:
            source = self._blur(image_texture, prefilter, w, h, vbo, via=0, into=1)

        key = (config_name, working_colourspace)
        qualifier = self._qualifier_programs.get(key)
        if qualifier is None:
            qualifier = gsp.compile_ocio_program(_keyer_shader_text(config_name, working_colourspace))
            self._qualifier_programs[key] = qualifier
        softness = min(1.0, max(0.0, float(p.get("softness", keyer.DEFAULT_SOFTNESS))))
        chroma_max = float(p.get("chroma_max", keyer.DEFAULT_CHROMA_MAX))

        def set_qualifier_uniforms(prog):
            u = lambda name: gl.glGetUniformLocation(prog, name)  # noqa: E731
            gl.glUniform1f(u("uStageOpacity"), 1.0)
            gl.glUniform1f(u("uKyHueCenter"), float(p.get("hue_center", keyer.DEFAULT_HUE_CENTER)))
            gl.glUniform1f(u("uKyHueWidth"), max(1e-3, float(p.get("hue_width", keyer.DEFAULT_HUE_WIDTH))))
            gl.glUniform1f(u("uKyChromaMin"), float(p.get("chroma_min", keyer.DEFAULT_CHROMA_MIN)))
            gl.glUniform1f(u("uKyChromaMax"), chroma_max)
            gl.glUniform1f(u("uKyLightnessMin"), float(p.get("lightness_min", keyer.DEFAULT_LIGHTNESS_MIN)))
            gl.glUniform1f(u("uKyLightnessMax"), float(p.get("lightness_max", keyer.DEFAULT_LIGHTNESS_MAX)))
            gl.glUniform1f(u("uKyDropoff"), color_warper.softness_to_dropoff(softness))
            gl.glUniform1f(u("uKyChromaEdge"), 0.03 + 0.25 * softness * max(chroma_max, 1e-6))
            gl.glUniform1f(u("uKyLightnessEdge"), 0.03 + 0.25 * softness)

        self._draw(qualifier, self._scratch[2][1], w, h, vbo,
                   [("uInput", source)], set_qualifier_uniforms)
        raw = self._scratch[2][0]

        matte_blur = max(0.0, float(p.get("matte_blur", keyer.DEFAULT_MATTE_BLUR)))
        if matte_blur > 0.0:
            raw = self._blur(raw, matte_blur, w, h, vbo, via=0, into=1)

        clip_black = min(1.0, max(0.0, float(p.get("clip_black", keyer.DEFAULT_CLIP_BLACK))))
        clip_white = min(1.0, max(0.0, float(p.get("clip_white", keyer.DEFAULT_CLIP_WHITE))))

        def set_finalize_uniforms(prog):
            u = lambda name: gl.glGetUniformLocation(prog, name)  # noqa: E731
            gl.glUniform1f(u("uClipBlack"), clip_black)
            gl.glUniform1f(u("uClipWhite"), clip_white)
            gl.glUniform1i(u("uInvert"), int(bool(p.get("invert", keyer.DEFAULT_INVERT))))
            gl.glUniform1f(u("uOpacity"), float(stage.opacity))

        self._draw(self._program("keyer_finalize", _KEYER_FINALIZE_FRAGMENT), target_fbo, w, h, vbo,
                   [("uRaw", raw)], set_finalize_uniforms)

    # -- consumer blend

    def render_matte_blend(self, before_texture, stage_output_texture, matte_texture, opacity,
                           target_fbo, w, h, vbo):
        def set_uniforms(prog):
            gl.glUniform1f(gl.glGetUniformLocation(prog, "uOpacity"), float(opacity))

        self._draw(self._program("matte_blend", _MATTE_BLEND_FRAGMENT), target_fbo, w, h, vbo,
                   [("uBefore", before_texture), ("uStageOutput", stage_output_texture),
                    ("uMatte", matte_texture)], set_uniforms)
