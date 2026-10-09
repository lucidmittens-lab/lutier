"""
gpu_frame_renderer.py -- GPU rendering for the Render Queue (2026-10-09):
grades whole frames on the GPU in a GL context of its OWN, on whatever
thread renders, so a render never touches Record (Export Video's GPU path
drives the Viewer's own widget on the UI thread, which blocks the window
and hijacks the picture for the whole export).

Per frame: upload the decoded frame (the decoder's raw uint16 rgb48
samples, normalised by the texture unit, or a float array for stills),
Lanczos-resize it to the output size if it differs
(gpu_lanczos_compositor.LanczosResampler -- matches image_io.resize_array
to ~2e-5 on the Mini LF 4K->1080 downscale; at the same size it's an exact
copy), run the stack through
gpu_stack_renderer.render_chain, read back RGB float and clip to [0,1] --
the same steps, in the same order, as render_job's CPU path (resize, then
stack.apply_stack_to_image_array). The CPU stays the truth: a stack that
isn't gpu_stack_renderer.is_stack_gpu_eligible renders on the CPU
(can_render says which).

Threading: the QOffscreenSurface must be created on the GUI thread
(make_offscreen_surface); the context is created and made current on the
rendering thread, in __init__, and stays current there until close().
Every call must come from that thread.

GL 2.1 / GLSL 1.20 like the rest of the GPU code (no QSurfaceFormat).
"""
import numpy as np
import OpenGL.GL as gl
from PySide6.QtGui import QGuiApplication, QOffscreenSurface, QOpenGLContext

import gpu_shader_prototype as gsp
import gpu_stack_renderer as gsr
from gpu_lanczos_compositor import LanczosResampler

# QPA platforms with no GL integration at all (the test suite's offscreen).
_NO_GL_PLATFORMS = {"offscreen", "minimal"}


def gpu_rendering_possible():
    """False where this process can't have a GL context at all. A True
    here can still fail in GpuFrameRenderer (no driver); callers fall back
    to the CPU either way."""
    app = QGuiApplication.instance()
    return app is not None and app.platformName() not in _NO_GL_PLATFORMS


def make_offscreen_surface():
    """A QOffscreenSurface for GpuFrameRenderer -- call on the GUI thread."""
    surface = QOffscreenSurface()
    surface.create()
    return surface if surface.isValid() else None


class GpuFrameRenderer:
    def __init__(self, surface):
        """Creates the context and makes it current on the CALLING thread.
        Raises RuntimeError if there's no usable GL."""
        if surface is None:
            raise RuntimeError("No offscreen GL surface")
        self._surface = surface
        self._context = QOpenGLContext()
        if not self._context.create():
            raise RuntimeError("Couldn't create an OpenGL context")
        if not self._context.makeCurrent(surface):
            raise RuntimeError("Couldn't make the OpenGL context current")
        self.renderer_name = gl.glGetString(gl.GL_RENDERER).decode(errors="replace")
        self._stack_renderer = gsr.GpuStackRenderer()
        self._resampler = LanczosResampler()
        self._source = None   # (texture, w, h, dtype)
        self._target = None   # (texture, fbo, w, h)
        self._read_fbo = gl.glGenFramebuffers(1)

    @staticmethod
    def can_render(stack):
        return gsr.is_stack_gpu_eligible(stack)

    def render(self, stack, frame, out_w, out_h):
        """`frame` (H,W,3): uint16 (0..65535) or float32 -> the graded
        (out_h, out_w, 3) float32, clipped to [0,1]."""
        h, w = frame.shape[:2]
        source = self._upload(frame)
        # Always through the RGBA32F target, even at the same size (Lanczos
        # at scale 1 is an exact copy: every other tap has weight 0):
        # render_chain may blit from its input, and an RGB16 texture
        # isn't reliably framebuffer-attachable.
        texture, target_fbo = self._ensure_target(out_w, out_h)
        self._resampler.resample_into(source, w, h, target_fbo, out_w, out_h,
                                      0.0, 0.0, out_w / w, out_h / h)
        result = self._stack_renderer.render_chain(stack, texture, out_w, out_h)
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, self._read_fbo)
        gl.glFramebufferTexture2D(gl.GL_FRAMEBUFFER, gl.GL_COLOR_ATTACHMENT0,
                                  gl.GL_TEXTURE_2D, result, 0)
        gl.glPixelStorei(gl.GL_PACK_ALIGNMENT, 4)
        pixels = gl.glReadPixels(0, 0, out_w, out_h, gl.GL_RGB, gl.GL_FLOAT)
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0)
        graded = np.frombuffer(pixels, dtype=np.float32).reshape(out_h, out_w, 3)
        return np.clip(graded, 0.0, 1.0)

    def close(self):
        """Frees every GL object and releases the context (same thread)."""
        if self._context is None:
            return
        self._stack_renderer.clear_gl_resources()
        self._resampler.clear_gl_resources()
        textures = [t for t in (self._source and self._source[0], self._target and self._target[0]) if t]
        if textures:
            gl.glDeleteTextures(textures)
        fbos = [self._read_fbo] + ([self._target[1]] if self._target else [])
        gl.glDeleteFramebuffers(len(fbos), fbos)
        self._context.doneCurrent()
        self._context = None

    # ---- GL objects ----------------------------------------------------

    def _upload(self, frame):
        h, w = frame.shape[:2]
        is_uint16 = frame.dtype == np.uint16
        pixels = np.ascontiguousarray(frame if is_uint16 else frame.astype(np.float32, copy=False))
        if self._source is None or self._source[1:] != (w, h, is_uint16):
            if self._source is not None:
                gl.glDeleteTextures([self._source[0]])
            texture = gl.glGenTextures(1)
            gl.glBindTexture(gl.GL_TEXTURE_2D, texture)
            for name, value in ((gl.GL_TEXTURE_MIN_FILTER, gl.GL_NEAREST),
                                (gl.GL_TEXTURE_MAG_FILTER, gl.GL_NEAREST),
                                (gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE),
                                (gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)):
                gl.glTexParameteri(gl.GL_TEXTURE_2D, name, value)
            internal = gl.GL_RGB16 if is_uint16 else gl.GL_RGB32F
            gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, internal, w, h, 0, gl.GL_RGB,
                            gl.GL_UNSIGNED_SHORT if is_uint16 else gl.GL_FLOAT, None)
            self._source = (texture, w, h, is_uint16)
        gl.glBindTexture(gl.GL_TEXTURE_2D, self._source[0])
        gl.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 1)
        gl.glTexSubImage2D(gl.GL_TEXTURE_2D, 0, 0, 0, w, h, gl.GL_RGB,
                           gl.GL_UNSIGNED_SHORT if is_uint16 else gl.GL_FLOAT, pixels)
        return self._source[0]

    def _ensure_target(self, w, h):
        if self._target is None or self._target[2:] != (w, h):
            if self._target is not None:
                gl.glDeleteTextures([self._target[0]])
                gl.glDeleteFramebuffers(1, [self._target[1]])
            texture, fbo = gsp.create_rgba32f_render_target(w, h)
            self._target = (texture, fbo, w, h)
        return self._target[0], self._target[1]
