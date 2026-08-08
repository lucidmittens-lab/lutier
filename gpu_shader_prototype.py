"""
gpu_shader_prototype.py -- GPU_ACCELERATION_PLAN_2026-08-08.md, STAGE 2.

Standalone script, NOT wired into the app. Answers two real questions
with numbers instead of assumptions, for the two OCIO-backed stage kinds
this app actually uses interactively (CDL, ACES DRT):

1. Does OCIO's GPU shader codegen (`getDefaultGPUProcessor()` ->
   `GpuShaderDesc` -> real GLSL) produce output that numerically matches
   this app's existing CPU path (`cdl.apply_cdl` /
   `aces_drt.apply_aces_output_transform`) closely enough to trust for a
   live preview?
2. What SHAPE does each stage kind's generated shader actually take --
   is it uniform-driven (cheap to re-parameterize without recompiling,
   which is what continuous slider drags need) or does it bake its
   parameter values as literal GLSL constants (which would mean
   regenerating shader source on every single edit)?

Renders through a real, current OpenGL context via Qt's
QOffscreenSurface/QOpenGLContext (no on-screen window, no new
dependency -- PySide6 and PyOpenGL are both already project
requirements) -- an actual GPU render, not a CPU-side shader simulator.

Run with: python3 gpu_shader_prototype.py
"""
import sys

import numpy as np
from PySide6.QtGui import QGuiApplication, QOffscreenSurface, QOpenGLContext, QSurfaceFormat
from OpenGL import GL as gl
import PyOpenColorIO as ocio

import aces_drt
import app_logic
import cdl

# Fullscreen-triangle vertex shader -- the standard "no VBO needed" trick:
# 3 vertices, positions/UVs derived purely from gl_VertexID, covering the
# whole viewport with one triangle (the corner outside [-1,1] gets clipped
# away for free). Kept completely separate from whatever OCIO generates --
# OCIO only ever emits the OCIOMain(vec4) color-transform function, never
# a full shader program, so wiring it into an actual renderable program is
# entirely this script's own job.
_VERTEX_SHADER = """
#version 400 core
out vec2 vUV;
void main() {
    vec2 pos = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
    vUV = pos;
    gl_Position = vec4(pos * 2.0 - 1.0, 0.0, 1.0);
}
"""

_FRAGMENT_SHADER_TEMPLATE = """
#version 400 core
in vec2 vUV;
out vec4 fragColor;
uniform sampler2D uInput;

{ocio_shader_text}

void main() {{
    vec4 inPixel = texture(uInput, vUV);
    fragColor = OCIOMain(inPixel);
}}
"""


def _make_offscreen_gl_context():
    """Real (not simulated) OpenGL core-profile context via a Qt
    QOffscreenSurface -- no visible window, but a genuine GPU-backed
    context, same "actual render, not theory" bar as this project's own
    screenshot-verification discipline elsewhere. QGuiApplication (not
    the heavier QApplication -- no widgets used here) is the minimum Qt
    needs before any surface/context can be created. GL 4.1 core is the
    highest macOS itself exposes, comfortably covering GLSL_4_0 (needs
    GL 4.0+) which is the language OCIO is asked to emit below."""
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)

    fmt = QSurfaceFormat()
    fmt.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setVersion(4, 1)
    QSurfaceFormat.setDefaultFormat(fmt)

    surface = QOffscreenSurface()
    surface.setFormat(fmt)
    surface.create()
    if not surface.isValid():
        raise RuntimeError("QOffscreenSurface failed to become valid -- no usable GL here.")

    context = QOpenGLContext()
    context.setFormat(fmt)
    if not context.create():
        raise RuntimeError("QOpenGLContext.create() failed.")
    if not context.makeCurrent(surface):
        raise RuntimeError("QOpenGLContext.makeCurrent() failed.")

    return app, context, surface


def _extract_ocio_glsl(processor, function_name="OCIOMain"):
    """`processor` -> (shader_text, num_textures, num_3d_textures,
    num_uniforms). The counts matter as much as the text itself here --
    see this module's docstring, question 2."""
    gpu = processor.getDefaultGPUProcessor()
    desc = ocio.GpuShaderDesc.CreateShaderDesc()
    desc.setLanguage(ocio.GPU_LANGUAGE_GLSL_4_0)
    desc.setFunctionName(function_name)
    gpu.extractGpuShaderInfo(desc)
    return (desc.getShaderText(), len(list(desc.getTextures())),
            len(list(desc.get3DTextures())), len(list(desc.getUniforms())))


def _compile_shader(kind, source):
    shader = gl.glCreateShader(kind)
    gl.glShaderSource(shader, source)
    gl.glCompileShader(shader)
    if not gl.glGetShaderiv(shader, gl.GL_COMPILE_STATUS):
        raise RuntimeError(gl.glGetShaderInfoLog(shader).decode())
    return shader


def _link_program(vertex_src, fragment_src):
    vs = _compile_shader(gl.GL_VERTEX_SHADER, vertex_src)
    fs = _compile_shader(gl.GL_FRAGMENT_SHADER, fragment_src)
    program = gl.glCreateProgram()
    gl.glAttachShader(program, vs)
    gl.glAttachShader(program, fs)
    gl.glLinkProgram(program)
    if not gl.glGetProgramiv(program, gl.GL_LINK_STATUS):
        raise RuntimeError(gl.glGetProgramInfoLog(program).decode())
    gl.glDeleteShader(vs)
    gl.glDeleteShader(fs)
    return program


def render_ocio_shader_on_gpu(ocio_shader_text, rgb_array):
    """Render `rgb_array` (H,W,3 float32, any range -- NOT assumed [0,1])
    through `ocio_shader_text`'s OCIOMain function on the actual GPU,
    returns an (H,W,3) float32 array. Full float32 throughout (input
    texture, framebuffer attachment, and readback all RGBA32F) so any
    precision delta measured against the CPU path is coming from the
    shader MATH itself, not from an incidental 8-bit texture round-trip
    that would exist in a real app's preview pipeline anyway but would
    muddy what this specific comparison is trying to isolate."""
    h, w = rgb_array.shape[:2]
    rgba = np.empty((h, w, 4), dtype=np.float32)
    rgba[..., :3] = rgb_array
    rgba[..., 3] = 1.0
    # OpenGL's texture origin is bottom-left; numpy/PIL's is top-left.
    # Irrelevant to the actual per-pixel math being validated here (every
    # pixel gets the same transform regardless of row order), so not
    # bothering to flip -- flagged only so a future reader doesn't wonder
    # why row 0 here doesn't visually match row 0 of a saved PNG.
    rgba = np.ascontiguousarray(rgba)

    program = _link_program(_VERTEX_SHADER,
                             _FRAGMENT_SHADER_TEMPLATE.format(ocio_shader_text=ocio_shader_text))

    vao = gl.glGenVertexArrays(1)
    gl.glBindVertexArray(vao)

    tex_in = gl.glGenTextures(1)
    gl.glBindTexture(gl.GL_TEXTURE_2D, tex_in)
    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_NEAREST)
    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_NEAREST)
    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
    gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA32F, w, h, 0, gl.GL_RGBA, gl.GL_FLOAT, rgba)

    tex_out = gl.glGenTextures(1)
    gl.glBindTexture(gl.GL_TEXTURE_2D, tex_out)
    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_NEAREST)
    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_NEAREST)
    gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA32F, w, h, 0, gl.GL_RGBA, gl.GL_FLOAT, None)

    fbo = gl.glGenFramebuffers(1)
    gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, fbo)
    gl.glFramebufferTexture2D(gl.GL_FRAMEBUFFER, gl.GL_COLOR_ATTACHMENT0,
                               gl.GL_TEXTURE_2D, tex_out, 0)
    status = gl.glCheckFramebufferStatus(gl.GL_FRAMEBUFFER)
    if status != gl.GL_FRAMEBUFFER_COMPLETE:
        raise RuntimeError(f"Incomplete FBO: 0x{status:x}")

    gl.glViewport(0, 0, w, h)
    gl.glUseProgram(program)
    gl.glActiveTexture(gl.GL_TEXTURE0)
    gl.glBindTexture(gl.GL_TEXTURE_2D, tex_in)
    gl.glUniform1i(gl.glGetUniformLocation(program, "uInput"), 0)
    gl.glDrawArrays(gl.GL_TRIANGLES, 0, 3)

    result = gl.glReadPixels(0, 0, w, h, gl.GL_RGBA, gl.GL_FLOAT)
    result = np.frombuffer(result, dtype=np.float32).reshape(h, w, 4)

    gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0)
    gl.glDeleteFramebuffers(1, [fbo])
    gl.glDeleteTextures([tex_in, tex_out])
    gl.glDeleteProgram(program)
    gl.glDeleteVertexArrays(1, [vao])

    return result[..., :3].copy()


def _report_delta(label, cpu_result, gpu_result):
    delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
    print(f"  {label}: max |delta|={delta.max():.7f}  mean |delta|={delta.mean():.7f}  "
          f"(cpu range [{cpu_result.min():.4f}, {cpu_result.max():.4f}])")


def _test_images():
    """Two deliberately different test cases: a real bundled sample image
    (realistic content, but only ever [0,1] since it's an 8-bit source --
    see app_logic.load_image_as_array) and a synthetic gradient spanning
    a wider range including negative and >1.0 values, since ACES-family
    working spaces routinely produce both and a GPU/CPU parity check that
    only ever sees [0,1] input would miss any behavior difference at the
    boundary or beyond it (e.g. differing power()/pow() handling of
    negative bases, a real risk point for CDL's power term specifically)."""
    real = app_logic.load_image_as_array(app_logic.default_sample_image_path(), max_dimension=256)
    rng = np.random.default_rng(20260808)
    wide = rng.uniform(-0.1, 1.1, size=(64, 64, 3)).astype(np.float32)
    return [("bundled sample image (256px, [0,1] only)", real),
            ("synthetic wide-range gradient (64px, [-0.1, 1.1])", wide)]


def main():
    _app, context, surface = _make_offscreen_gl_context()
    print(f"GL context: {gl.glGetString(gl.GL_VERSION).decode()}  "
          f"renderer: {gl.glGetString(gl.GL_RENDERER).decode()}")

    print("\n=== CDL ===")
    cdl_kwargs = dict(slope=(1.1, 0.95, 1.0), offset=(0.02, 0.0, -0.01),
                       power=(1.0, 1.05, 1.0), sat=1.2)
    cdl_transform = cdl.build_cdl_transform(**cdl_kwargs)
    cdl_cfg = cdl._get_raw_config()
    cdl_processor = cdl_cfg.getProcessor(cdl_transform)
    shader_text, n_tex, n_tex3d, n_uniforms = _extract_ocio_glsl(cdl_processor)
    print(f"  shader: {len(shader_text)} chars, {n_tex} textures, "
          f"{n_tex3d} 3D textures, {n_uniforms} uniforms "
          f"({'parameter-driven, cheap to re-tune' if n_uniforms else 'VALUES BAKED AS GLSL CONSTANTS -- see finding below'})")
    for label, image in _test_images():
        cpu_result = cdl.apply_cdl(image, **cdl_kwargs)
        gpu_result = render_ocio_shader_on_gpu(shader_text, image)
        _report_delta(label, cpu_result, gpu_result)

    print("\n=== ACES DRT ===")
    display, view = aces_drt.get_default_display_and_view(aces_drt.DEFAULT_CONFIG_NAME)
    print(f"  display/view: {display} / {view}")
    aces_drt_kwargs = dict(working_colourspace="ACEScct", display=display, view=view,
                            config_name=aces_drt.DEFAULT_CONFIG_NAME)
    aces_cpu_processor = aces_drt._get_output_transform_processor(
        aces_drt_kwargs["config_name"], aces_drt_kwargs["working_colourspace"],
        display, view)
    aces_gpu_cfg = aces_drt._get_config(aces_drt_kwargs["config_name"])
    aces_gpu_processor = aces_gpu_cfg.getProcessor(
        ocio.DisplayViewTransform(src=aces_drt_kwargs["working_colourspace"],
                                   display=display, view=view))
    shader_text, n_tex, n_tex3d, n_uniforms = _extract_ocio_glsl(aces_gpu_processor)
    print(f"  shader: {len(shader_text)} chars, {n_tex} textures, "
          f"{n_tex3d} 3D textures, {n_uniforms} uniforms "
          f"({'parameter-driven, cheap to re-tune' if n_uniforms else 'VALUES BAKED AS GLSL CONSTANTS -- see finding below'})")
    for label, image in _test_images():
        cpu_result = aces_drt.apply_aces_output_transform(image, **aces_drt_kwargs)
        gpu_result = render_ocio_shader_on_gpu(shader_text, image)
        _report_delta(label, cpu_result, gpu_result)

    context.doneCurrent()


if __name__ == "__main__":
    main()
