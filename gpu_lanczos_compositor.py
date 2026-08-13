"""
gpu_lanczos_compositor.py -- GPU_ACCELERATION_PLAN_2026-08-08.md, STAGE 5.

Replaces WipeCompareWidget's old QPainter.drawPixmap (bilinear
SmoothPixmapTransform) scaling with a GPU Lanczos-3 resample, and folds
the wipe-divide compositing into the same pass -- per Tom: use Lanczos
scaling, and keep the whole native-resolution-source -> screen mapping as
ONE concatenated scale operation (never "downscale once, commit, then
scale again"), the same "single scale op" property this app's CPU/
QPainter path already had (see sample_image_panel.py's "PREVIEW
RESOLUTION" comment) -- just upgraded from bilinear to Lanczos and moved
from CPU into the shader.

SEPARABLE 2-PASS DESIGN -- the screen<-source mapping here is always a
uniform scale (fit_scale * zoom, identical on both axes -- see
wipe_compare_widget.py's own _image_rect) plus a translation (pan +
centering), never a rotation/shear, so a standard separable Lanczos
(resample X, then resample Y) is exactly equivalent to a full 2D
resample, at a fraction of the cost (Lanczos-3 = 6 taps/axis, so 12 taps
total separable vs. 36 non-separable). "Target size" for both passes is
always the WIDGET's own pixel size, not image_rect's (which can be far
larger than the widget when zoomed in, or smaller when zoomed out) --
each output pixel maps back to a source texel coordinate via the same
offset+scale the CPU path's _image_rect already computes, so a screen
pixel outside the image entirely just samples clamped source edge texels
(the actual "is this inside the image or in the gutter" decision, and the
wipe-divide left/right choice, both happen in the composite pass below).

ADAPTIVE MINIFICATION SUPPORT (2026-08-09) -- a fixed 6-tap kernel is
correct for magnification and mild minification, but aliases (undersamples)
under heavy downscale: a source texel run that should blend into one
output pixel instead gets sparsely point-sampled, since 6 fixed taps
cover less and less of the actual source footprint as `scale` shrinks.
The standard fix (see e.g. any EWA/Lanczos-minification writeup) is to
widen the kernel's support by `1/scale` when `scale < 1` -- i.e. stretch
the SAME lanczos3() shape across a wider footprint in source-texel space,
so every output pixel's footprint is actually covered, at the cost of
more taps. `filterScale = max(1.0, 1.0 / scale)` (see both fragment
shaders below) implements exactly that: taps are spaced `filterScale`
texels apart instead of 1, and the kernel argument is divided by
`filterScale` to match. `filterScale` is capped at `_MAX_FILTER_SCALE`
(8.0) to bound the worst-case tap count -- beyond an 8x downscale, some
residual aliasing can remain, a deliberate cost/quality tradeoff rather
than an unbounded per-pixel loop for arbitrarily extreme zoom-out.
"""
import numpy as np
import OpenGL.GL as gl

import gpu_shader_prototype as gsp

_MAX_FILTER_SCALE = 8.0  # see module docstring's "ADAPTIVE MINIFICATION SUPPORT"

_LANCZOS_GLSL = """
const float PI = 3.14159265358979;
float lanczos3(float x) {
    if (x == 0.0) return 1.0;
    if (abs(x) >= 3.0) return 0.0;
    float px = PI * x;
    return 3.0 * sin(px) * sin(px / 3.0) / (px * px);
}
"""

# Horizontal pass -- resamples along X from `uSourceWidth` source texels
# to the widget's own pixel width (the framebuffer this renders into is
# sized targetW x sourceH -- Y is untouched, an exact 1:1 passthrough,
# see module docstring). `uOffsetX`/`uScale` are screen-space: uOffsetX
# is where the source's texel-0 left edge lands on screen (image_rect's
# left, in widget pixels), uScale is screen pixels per source texel
# (fit_scale * zoom).
_HORIZONTAL_FRAGMENT_SHADER = ("""
#version 120
uniform sampler2D uSource;
uniform float uSourceWidth;
uniform float uSourceHeight;
uniform float uOffsetX;
uniform float uScale;

""" + _LANCZOS_GLSL + """
void main() {
    float srcX = (gl_FragCoord.x - uOffsetX) / uScale - 0.5;  // texel-index units
    int baseTexel = int(floor(srcX));
    float srcYNorm = gl_FragCoord.y / uSourceHeight;  // pass 1: Y is untouched

    // See module docstring's "ADAPTIVE MINIFICATION SUPPORT" -- widen the
    // kernel's support by 1/uScale when downscaling, so heavy minification
    // doesn't sparsely point-sample a source footprint wider than 6 taps.
    float filterScale = clamp(1.0 / abs(uScale), 1.0, %(max_filter_scale)s);
    int radius = int(ceil(3.0 * filterScale));

    vec4 sum = vec4(0.0);
    float wsum = 0.0;
    for (int i = -radius + 1; i <= radius; i++) {
        int texel = baseTexel + i;
        float w = lanczos3((srcX - float(texel)) / filterScale);
        float clampedTexel = clamp(float(texel), 0.0, uSourceWidth - 1.0);
        vec4 s = texture2D(uSource, vec2((clampedTexel + 0.5) / uSourceWidth, srcYNorm));
        sum += s * w;
        wsum += w;
    }
    gl_FragColor = wsum > 0.0 ? sum / wsum : vec4(0.0);
}
""") % {"max_filter_scale": _MAX_FILTER_SCALE}

# Vertical pass -- resamples along Y from `uSourceHeight` (the horizontal
# pass's OWN output height, i.e. the original source height) to the
# widget's pixel height. Input here is the horizontal pass's output
# texture (already at target width), so no X remapping at all -- straight
# passthrough via gl_FragCoord.x / target width.
_VERTICAL_FRAGMENT_SHADER = ("""
#version 120
uniform sampler2D uSource;
uniform float uSourceWidth;
uniform float uSourceHeight;
uniform float uOffsetY;
uniform float uScale;

""" + _LANCZOS_GLSL + """
void main() {
    float srcY = (gl_FragCoord.y - uOffsetY) / uScale - 0.5;
    int baseTexel = int(floor(srcY));
    float srcXNorm = gl_FragCoord.x / uSourceWidth;

    // See _HORIZONTAL_FRAGMENT_SHADER's own comment -- same adaptive
    // minification widening, applied along Y here.
    float filterScale = clamp(1.0 / abs(uScale), 1.0, %(max_filter_scale)s);
    int radius = int(ceil(3.0 * filterScale));

    vec4 sum = vec4(0.0);
    float wsum = 0.0;
    for (int i = -radius + 1; i <= radius; i++) {
        int texel = baseTexel + i;
        float w = lanczos3((srcY - float(texel)) / filterScale);
        float clampedTexel = clamp(float(texel), 0.0, uSourceHeight - 1.0);
        vec4 s = texture2D(uSource, vec2(srcXNorm, (clampedTexel + 0.5) / uSourceHeight));
        sum += s * w;
        wsum += w;
    }
    gl_FragColor = wsum > 0.0 ? sum / wsum : vec4(0.0);
}
""") % {"max_filter_scale": _MAX_FILTER_SCALE}


def _link(fragment_source):
    return gsp._link_program(gsp._VERTEX_SHADER, fragment_source)


class LanczosResampler:
    """Resamples a native-resolution source texture into `target_w` x
    `target_h` via the separable 2-pass Lanczos-3 above, given a screen-
    space (offset_x, offset_y, scale) mapping -- see module docstring.
    Owns its own compiled programs (created lazily, once) and an
    intermediate render target reused/resized across calls, same
    "compile once, reuse across many renders" discipline as
    gpu_stack_renderer.GpuStackRenderer."""

    def __init__(self):
        self._h_program = None
        self._v_program = None
        self._vbo = None
        self._intermediate_size = None
        self._intermediate_texture = None
        self._intermediate_fbo = None

    def _ensure_programs(self):
        if self._h_program is None:
            self._h_program = _link(_HORIZONTAL_FRAGMENT_SHADER)
            self._v_program = _link(_VERTICAL_FRAGMENT_SHADER)
        if self._vbo is None:
            self._vbo = gsp.make_fullscreen_quad_vbo()

    def _ensure_intermediate(self, w, h):
        if self._intermediate_size == (w, h):
            return
        if self._intermediate_texture is not None:
            gl.glDeleteTextures([self._intermediate_texture])
            gl.glDeleteFramebuffers(1, [self._intermediate_fbo])
        self._intermediate_texture, self._intermediate_fbo = \
            gsp.create_rgba32f_render_target(w, h)
        self._intermediate_size = (w, h)

    def resample_into(self, source_texture, source_w, source_h,
                       target_fbo, target_w, target_h, offset_x, offset_y,
                       scale_x, scale_y):
        """Resamples `source_texture` (source_w x source_h) into whatever
        `target_fbo` has bound as its color attachment (target_w x
        target_h), via the mapping screen_pixel = source_texel * scale +
        offset, independently per axis. Renders into `target_fbo`
        directly (does NOT return a new texture) so a caller (the wipe
        compositor) can render "before" and "after" into two different
        pre-existing targets.

        `scale_x`/`scale_y` are allowed to differ in SIGN (not just
        magnitude) -- the shader math (`(gl_FragCoord - offset) / scale`)
        works unchanged for a negative scale, it just traverses that axis
        in reverse. This is what a real caller needs for Y specifically:
        GL's own viewport/gl_FragCoord convention has Y=0 at the BOTTOM
        of the target and increasing upward, while this app's widget-
        space image_rect (Qt's convention) has Y=0 at the TOP and
        increasing downward -- so source row 0 (the top of the actual
        image) needs to land at the numerically LARGEST gl_FragCoord.y
        within the target, not the smallest. Passing a negative scale_y
        (with offset_y set to the image rect's GL-space TOP, see
        WipeCompositor.render) achieves exactly that flip without the
        shader itself needing any Qt-vs-GL awareness at all -- this
        module otherwise knows nothing about Qt, on purpose, same
        separation-of-concerns reason wipe_compare_widget.py's own
        _image_rect docstring keeps "did the caller ask for zoom > 1"
        completely separate from "how does GL happen to store texels.\""""
        self._ensure_programs()
        # Capture/restore the AMBIENT framebuffer binding, exactly the
        # same discipline as gpu_stack_renderer.GpuStackRenderer.
        # render_chain's own comment explains in full -- "0" is never a
        # safe thing to rebind as a "reset" inside a QOpenGLWidget's
        # paintGL, since its real render target is an internally-managed
        # FBO, never system framebuffer 0. This was a REAL bug (not
        # theoretical): confirmed via a real OpenGL.error.GLError(
        # GL_INVALID_FRAMEBUFFER_OPERATION) on the final composite draw
        # in WipeCompositor.render, traced back to this function (and
        # render_chain) leaving "0" bound instead of the ambient target.
        ambient_fbo = gl.glGetIntegerv(gl.GL_FRAMEBUFFER_BINDING)
        # Pass 1 (horizontal): source_w x source_h -> target_w x source_h.
        self._ensure_intermediate(target_w, source_h)
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, self._intermediate_fbo)
        gl.glViewport(0, 0, target_w, source_h)
        gl.glUseProgram(self._h_program)
        gl.glUniform1f(gl.glGetUniformLocation(self._h_program, "uSourceWidth"), float(source_w))
        gl.glUniform1f(gl.glGetUniformLocation(self._h_program, "uSourceHeight"), float(source_h))
        gl.glUniform1f(gl.glGetUniformLocation(self._h_program, "uOffsetX"), float(offset_x))
        gl.glUniform1f(gl.glGetUniformLocation(self._h_program, "uScale"), float(scale_x))
        gl.glActiveTexture(gl.GL_TEXTURE0)
        gl.glBindTexture(gl.GL_TEXTURE_2D, source_texture)
        gl.glUniform1i(gl.glGetUniformLocation(self._h_program, "uSource"), 0)
        aPos_loc = gsp.bind_fullscreen_quad(self._vbo, self._h_program)
        gl.glDrawArrays(gl.GL_TRIANGLE_STRIP, 0, 4)
        gsp.unbind_fullscreen_quad(aPos_loc)

        # Pass 2 (vertical): target_w x source_h -> target_w x target_h.
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, target_fbo)
        gl.glViewport(0, 0, target_w, target_h)
        gl.glUseProgram(self._v_program)
        gl.glUniform1f(gl.glGetUniformLocation(self._v_program, "uSourceWidth"), float(target_w))
        gl.glUniform1f(gl.glGetUniformLocation(self._v_program, "uSourceHeight"), float(source_h))
        gl.glUniform1f(gl.glGetUniformLocation(self._v_program, "uOffsetY"), float(offset_y))
        gl.glUniform1f(gl.glGetUniformLocation(self._v_program, "uScale"), float(scale_y))
        gl.glActiveTexture(gl.GL_TEXTURE0)
        gl.glBindTexture(gl.GL_TEXTURE_2D, self._intermediate_texture)
        gl.glUniform1i(gl.glGetUniformLocation(self._v_program, "uSource"), 0)
        aPos_loc = gsp.bind_fullscreen_quad(self._vbo, self._v_program)
        gl.glDrawArrays(gl.GL_TRIANGLE_STRIP, 0, 4)
        gsp.unbind_fullscreen_quad(aPos_loc)

        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, ambient_fbo)

    def clear_gl_resources(self):
        if self._intermediate_texture is not None:
            gl.glDeleteTextures([self._intermediate_texture])
            gl.glDeleteFramebuffers(1, [self._intermediate_fbo])
            self._intermediate_texture = None
            self._intermediate_fbo = None
            self._intermediate_size = None
        if self._h_program is not None:
            gl.glDeleteProgram(self._h_program)
            gl.glDeleteProgram(self._v_program)
            self._h_program = None
            self._v_program = None
        if self._vbo is not None:
            gl.glDeleteBuffers(1, [self._vbo])
            self._vbo = None


def resample_to_array(resampler, source_array, target_w, target_h, offset_x, offset_y,
                       scale_x, scale_y=None):
    """Convenience for tests/validation: resample a plain numpy array
    (not an already-uploaded texture) and read the result straight back
    to a numpy array -- not used by the real widget (which stays
    GPU-resident end to end), but this is exactly the kind of "real
    render, then compare numerically" check this project insists on, so
    it needs a clean way to run the resampler in isolation. `scale_y`
    defaults to `scale_x` (the common uniform-scale case every existing
    test uses) -- pass it explicitly to test a Y-flip/anisotropic case."""
    if scale_y is None:
        scale_y = scale_x
    tex_in, w, h = gsp.upload_rgba32f_texture(source_array)
    tex_out, fbo_out = gsp.create_rgba32f_render_target(target_w, target_h)
    resampler.resample_into(tex_in, w, h, fbo_out, target_w, target_h, offset_x, offset_y,
                             scale_x, scale_y)

    gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, fbo_out)
    pixels = gl.glReadPixels(0, 0, target_w, target_h, gl.GL_RGBA, gl.GL_FLOAT)
    result = np.frombuffer(pixels, dtype=np.float32).reshape(target_h, target_w, 4)[..., :3].copy()
    gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0)

    gl.glDeleteTextures([tex_in, tex_out])
    gl.glDeleteFramebuffers(1, [fbo_out])
    return result


# Final composite -- takes two ALREADY widget-sized, already-Lanczos-
# resampled textures (see WipeCompositor.render) and picks between them
# per pixel: outside uImageRect entirely -> uBackgroundColor (the gutter
# fill, palette(base) -- matches WipeCompareWidget.paintEvent's own
# unconditional background fill, same reasoning as that code's own "ELEVENTH
# PASS" comment); inside it, either uAfterResampled directly (wipe off) or
# whichever side of uDividerScreenX (wipe on) -- falling back to
# uPlaceholderColor for whichever side has no image loaded yet (matches
# WipeCompareWidget's existing PLACEHOLDER_BG). uImageRect/uDividerScreenX
# are in the SAME GL-space convention as the resample passes' own
# offset_y (see resample_into's docstring) -- WipeCompositor.render does
# that one Qt-to-GL conversion, this shader has no Qt awareness at all.
_COMPOSITE_FRAGMENT_SHADER = """
#version 120
uniform sampler2D uBeforeResampled;
uniform sampler2D uAfterResampled;
uniform bool uHasBefore;
uniform bool uHasAfter;
uniform bool uWipeEnabled;
uniform float uDividerScreenX;
uniform vec4 uImageRect;  // left, bottom, width, height -- GL space, screen pixels
uniform vec2 uTargetSize;  // widget_w, widget_h -- both resampled textures are always this size
uniform vec3 uBackgroundColor;
uniform vec3 uPlaceholderColor;
// Canvas Settings letterboxing (per Tom, 2026-08-13): the region inside
// the canvas box but outside the actual image gets uCanvasColor (a real,
// distinct fill -- black by convention, same as any pro NLE/compositor's
// canvas bounds) instead of just falling through to uBackgroundColor,
// which is the app's own panel background and was making the canvas
// box invisible -- confirmed directly ("didn't actually render black in
// the space outside of the image in the canvas, just shines thru to
// bg"). uCanvasRectEnabled is false whenever WipeCompareWidget has no
// canvas aspect ratio set at all (see set_canvas_aspect_ratio), in which
// case behavior is unchanged from before this existed.
uniform bool uCanvasRectEnabled;
uniform vec4 uCanvasRect;  // left, bottom, width, height -- GL space, same convention as uImageRect
uniform vec3 uCanvasColor;

void main() {
    vec2 fragXY = gl_FragCoord.xy;
    bool insideImage = fragXY.x >= uImageRect.x && fragXY.x < uImageRect.x + uImageRect.z &&
                        fragXY.y >= uImageRect.y && fragXY.y < uImageRect.y + uImageRect.w;
    if (!insideImage) {
        bool insideCanvas = uCanvasRectEnabled &&
            fragXY.x >= uCanvasRect.x && fragXY.x < uCanvasRect.x + uCanvasRect.z &&
            fragXY.y >= uCanvasRect.y && fragXY.y < uCanvasRect.y + uCanvasRect.w;
        gl_FragColor = insideCanvas ? vec4(uCanvasColor, 1.0) : vec4(uBackgroundColor, 1.0);
        return;
    }

    vec2 uv = fragXY / uTargetSize;

    bool showBefore = uWipeEnabled && (fragXY.x < uDividerScreenX);
    if (showBefore) {
        gl_FragColor = uHasBefore ? vec4(clamp(texture2D(uBeforeResampled, uv).rgb, 0.0, 1.0), 1.0)
                                   : vec4(uPlaceholderColor, 1.0);
    } else {
        gl_FragColor = uHasAfter ? vec4(clamp(texture2D(uAfterResampled, uv).rgb, 0.0, 1.0), 1.0)
                                  : vec4(uPlaceholderColor, 1.0);
    }
}
"""


class WipeCompositor:
    """The top-level entry point WipeCompareWidget's paintGL actually
    calls: owns a LanczosResampler and the composite program/targets,
    and does the one Qt-space -> GL-space conversion this whole module
    otherwise stays deliberately ignorant of (see resample_into's own
    docstring)."""

    def __init__(self):
        self._resampler = LanczosResampler()
        self._composite_program = None
        self._vbo = None
        self._before_target_size = None
        self._before_texture = None
        self._before_fbo = None
        self._after_target_size = None
        self._after_texture = None
        self._after_fbo = None

    def _ensure_composite_program(self):
        if self._composite_program is None:
            self._composite_program = _link(_COMPOSITE_FRAGMENT_SHADER)
        if self._vbo is None:
            self._vbo = gsp.make_fullscreen_quad_vbo()

    def _ensure_side_target(self, which, w, h):
        size_attr, tex_attr, fbo_attr = {
            "before": ("_before_target_size", "_before_texture", "_before_fbo"),
            "after": ("_after_target_size", "_after_texture", "_after_fbo"),
        }[which]
        if getattr(self, size_attr) == (w, h):
            return
        old_tex, old_fbo = getattr(self, tex_attr), getattr(self, fbo_attr)
        if old_tex is not None:
            gl.glDeleteTextures([old_tex])
            gl.glDeleteFramebuffers(1, [old_fbo])
        tex, fbo = gsp.create_rgba32f_render_target(w, h)
        setattr(self, tex_attr, tex)
        setattr(self, fbo_attr, fbo)
        setattr(self, size_attr, (w, h))

    def render(self, before_texture, before_size, after_texture, after_size,
               widget_w, widget_h, image_rect_qt,
               wipe_enabled, divider_fraction, background_color, placeholder_color,
               canvas_rect_qt=None, canvas_color=(0.0, 0.0, 0.0)):
        """Renders the final composited frame directly into whatever
        framebuffer is currently bound (the widget's own default
        framebuffer, when called from paintGL) -- no return value.

        `before_texture`/`after_texture` are native-resolution GL
        textures (or None if that side has no image loaded/no stack
        result yet -- see uHasBefore/uHasAfter above), each with its own
        `_size` (w, h) in texels -- allowed to differ between before and
        after (e.g. Reference vs. Sample, see wipe_compare_widget.py's
        own docstring on that pre-existing tolerance). `image_rect_qt`
        is (left, top, width, height) in WIDGET pixels, Qt convention
        (Y-down) -- exactly what _image_rect() already computes. The
        per-side scale (screen pixels per source texel) is derived below
        from image_rect's size against EACH side's own texture size --
        deliberately per-image, not a single shared value, since before
        and after can have different native sizes (e.g. Reference vs.
        Sample) yet still need to land in the exact same on-screen rect.
        `divider_fraction` is in [0,1] across image_rect's width,
        matching WipeCompareWidget._wipe_fraction.

        `canvas_rect_qt` (same (left, top, width, height) Qt convention
        as image_rect_qt, or None) is Canvas Settings' own letterbox box
        -- see WipeCompareWidget._canvas_rect and the fragment shader's
        own docstring above. None (the default) disables it entirely,
        same visual result as before this parameter existed."""
        self._ensure_composite_program()
        # resample_into (below) binds/unbinds several FBOs of its own and
        # explicitly rebinds framebuffer 0 at its own end -- capture the
        # framebuffer the CALLER actually wants drawn into (Qt's own
        # internal FBO, when called from paintGL) now, and restore it
        # right before the final composite draw, rather than assuming
        # nothing after this point touches the binding.
        target_fbo = gl.glGetIntegerv(gl.GL_FRAMEBUFFER_BINDING)

        left, top, rect_w, rect_h = image_rect_qt
        # The one Qt (Y-down) -> GL (Y-up) conversion this module needed
        # -- see resample_into's own docstring for the full reasoning.
        # Qt's top edge (smaller Y) is where source row 0 belongs, and in
        # GL-space that's the numerically LARGEST Y within the rect.
        gl_image_bottom = widget_h - (top + rect_h)
        gl_image_top = widget_h - top

        if after_texture is not None:
            self._ensure_side_target("after", widget_w, widget_h)
            after_w, after_h = after_size
            scale_x = rect_w / after_w
            scale_y = rect_h / after_h
            self._resampler.resample_into(
                after_texture, after_w, after_h, self._after_fbo, widget_w, widget_h,
                offset_x=left, offset_y=gl_image_top, scale_x=scale_x, scale_y=-scale_y)

        if wipe_enabled and before_texture is not None:
            self._ensure_side_target("before", widget_w, widget_h)
            before_w, before_h = before_size
            scale_x = rect_w / before_w
            scale_y = rect_h / before_h
            self._resampler.resample_into(
                before_texture, before_w, before_h, self._before_fbo, widget_w, widget_h,
                offset_x=left, offset_y=gl_image_top, scale_x=scale_x, scale_y=-scale_y)

        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, target_fbo)
        gl.glViewport(0, 0, widget_w, widget_h)
        gl.glUseProgram(self._composite_program)
        p = self._composite_program
        gl.glUniform1i(gl.glGetUniformLocation(p, "uHasBefore"),
                        1 if (wipe_enabled and before_texture is not None) else 0)
        gl.glUniform1i(gl.glGetUniformLocation(p, "uHasAfter"), 1 if after_texture is not None else 0)
        gl.glUniform1i(gl.glGetUniformLocation(p, "uWipeEnabled"), 1 if wipe_enabled else 0)
        gl.glUniform1f(gl.glGetUniformLocation(p, "uDividerScreenX"), left + rect_w * divider_fraction)
        gl.glUniform4f(gl.glGetUniformLocation(p, "uImageRect"),
                        left, gl_image_bottom, rect_w, rect_h)
        gl.glUniform2f(gl.glGetUniformLocation(p, "uTargetSize"), float(widget_w), float(widget_h))
        gl.glUniform3f(gl.glGetUniformLocation(p, "uBackgroundColor"), *background_color)
        gl.glUniform3f(gl.glGetUniformLocation(p, "uPlaceholderColor"), *placeholder_color)

        gl.glUniform1i(gl.glGetUniformLocation(p, "uCanvasRectEnabled"), 1 if canvas_rect_qt is not None else 0)
        if canvas_rect_qt is not None:
            canvas_left, canvas_top, canvas_w, canvas_h = canvas_rect_qt
            # Same Qt (Y-down) -> GL (Y-up) conversion as image_rect above.
            canvas_bottom = widget_h - (canvas_top + canvas_h)
            gl.glUniform4f(gl.glGetUniformLocation(p, "uCanvasRect"),
                            canvas_left, canvas_bottom, canvas_w, canvas_h)
        gl.glUniform3f(gl.glGetUniformLocation(p, "uCanvasColor"), *canvas_color)

        gl.glActiveTexture(gl.GL_TEXTURE0)
        gl.glBindTexture(gl.GL_TEXTURE_2D, self._after_texture if after_texture is not None else 0)
        gl.glUniform1i(gl.glGetUniformLocation(p, "uAfterResampled"), 0)
        gl.glActiveTexture(gl.GL_TEXTURE1)
        gl.glBindTexture(gl.GL_TEXTURE_2D,
                          self._before_texture if (wipe_enabled and before_texture is not None) else 0)
        gl.glUniform1i(gl.glGetUniformLocation(p, "uBeforeResampled"), 1)

        aPos_loc = gsp.bind_fullscreen_quad(self._vbo, p)
        gl.glDrawArrays(gl.GL_TRIANGLE_STRIP, 0, 4)
        gsp.unbind_fullscreen_quad(aPos_loc)

    def clear_gl_resources(self):
        self._resampler.clear_gl_resources()
        for tex_attr, fbo_attr in (("_before_texture", "_before_fbo"), ("_after_texture", "_after_fbo")):
            tex = getattr(self, tex_attr)
            if tex is not None:
                gl.glDeleteTextures([tex])
                gl.glDeleteFramebuffers(1, [getattr(self, fbo_attr)])
                setattr(self, tex_attr, None)
                setattr(self, fbo_attr, None)
        if self._composite_program is not None:
            gl.glDeleteProgram(self._composite_program)
            self._composite_program = None
        if self._vbo is not None:
            gl.glDeleteBuffers(1, [self._vbo])
            self._vbo = None
