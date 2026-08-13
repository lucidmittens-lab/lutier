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
from PySide6.QtGui import QOffscreenSurface, QOpenGLContext
from PySide6.QtWidgets import QApplication
from OpenGL import GL as gl
import PyOpenColorIO as ocio

import aces_drt
import app_logic
import cdl

# GLSL 1.20 (OpenGL 2.1 Compatibility Profile) -- NOT a newer Core
# Profile, and deliberately so: confirmed on real hardware that this is
# the TRUE platform-default QOpenGLWidget context on macOS (checked
# directly via GL_VERSION/GL_SHADING_LANGUAGE_VERSION), and that forcing
# a newer Core Profile app-wide breaks the Graph panel's pyqtgraph
# rendering outright (GLScatterPlotItem/GLLinePlotItem never bind their
# own Vertex Array Object, which Core Profile requires but Compatibility
# Profile doesn't -- a real, reproduced ShaderValidationError, not a
# guess). See conftest.py/main_window.main()'s own comments for the full
# story. `varying`/`texture2D`/`gl_FragColor`, and a real vertex buffer
# instead of the newer (GLSL 130+) gl_VertexID "no VBO needed" trick, are
# all consequences of this same version choice, not independent style
# preferences.
_VERTEX_SHADER = """
#version 120
attribute vec2 aPos;
varying vec2 vUV;
void main() {
    vUV = aPos * 0.5 + 0.5;
    gl_Position = vec4(aPos, 0.0, 1.0);
}
"""

# Public alias -- every fullscreen-quad shader in this pipeline (a custom
# one built via link_custom_program included) shares this exact vertex
# stage, so a module reaching for link_custom_program needs a sanctioned
# way to get it too, not just the module-private _VERTEX_SHADER name.
FULLSCREEN_QUAD_VERTEX_SHADER = _VERTEX_SHADER

_FRAGMENT_SHADER_TEMPLATE = """
#version 120
varying vec2 vUV;
uniform sampler2D uInput;
// Blends this stage's own output against its own input -- the GPU-side
// equivalent of stack._apply_single_stage_with_opacity's `running *
// (1-opacity) + stage_output * opacity`, needed so the real multi-stage
// GPU chain (gpu_stack_renderer.py, Stage 5) matches _run_stages' exact
// per-stage opacity semantics rather than only ever rendering at full
// strength. Defaults to 1.0 in render_program_on_gpu below (set right
// after glUseProgram, before any caller-supplied set_uniforms runs) --
// GLSL uniforms default to 0.0 if never explicitly set, which would
// silently turn every existing caller's render into a pure passthrough
// (opacity=0.0) instead of the intended full-strength transform.
uniform float uStageOpacity;

{ocio_shader_text}

void main() {{
    vec4 inPixel = texture2D(uInput, vUV);
    vec4 stageOutput = OCIOMain(inPixel);
    gl_FragColor = mix(inPixel, stageOutput, uStageOpacity);
}}
"""


def _make_offscreen_gl_context():
    """Real (not simulated) OpenGL context via a Qt QOffscreenSurface --
    no visible window, but a genuine GPU-backed context, same "actual
    render, not theory" bar as this project's own screenshot-verification
    discipline elsewhere. Uses QApplication, not the lighter
    QGuiApplication a widget-less GL script would normally reach for --
    Qt allows only ONE application singleton per process, and every other
    test file in this project constructs a real QApplication (needed for
    MainWindow's actual widgets). A QGuiApplication constructed first
    here caused a real, reproduced "Fatal Python error: Aborted" crash
    when a LATER test in the same pytest run tried to build a
    QApplication-backed MainWindow (with its own real GLViewWidget, see
    lut_viewer_3d.py) against a process whose app singleton was only ever
    a QGuiApplication -- confirmed by switching to QApplication here,
    which fixed it.

    No QSurfaceFormat override -- deliberately takes whatever the
    platform hands back by default, which on this Mac is confirmed (via
    GL_VERSION/GL_SHADING_LANGUAGE_VERSION, checked directly) to be
    OpenGL 2.1 Compatibility Profile / GLSL 1.20, exactly the version
    this whole pipeline's shaders target (see _VERTEX_SHADER's own
    comment for why: forcing a newer Core Profile breaks the Graph
    panel's pyqtgraph rendering elsewhere in the real app). This function
    isn't shared with that app process, but it should still exercise the
    SAME GL version the real app actually runs under, not a different,
    untested one."""
    app = QApplication.instance() or QApplication(sys.argv)

    surface = QOffscreenSurface()
    surface.create()
    if not surface.isValid():
        raise RuntimeError("QOffscreenSurface failed to become valid -- no usable GL here.")

    context = QOpenGLContext()
    if not context.create():
        raise RuntimeError("QOpenGLContext.create() failed.")
    if not context.makeCurrent(surface):
        raise RuntimeError("QOpenGLContext.makeCurrent() failed.")

    return app, context, surface


def _extract_ocio_glsl(processor, function_name="OCIOMain", resource_prefix=None):
    """`processor` -> (shader_text, num_textures, num_3d_textures,
    num_uniforms). The counts matter as much as the text itself here --
    see this module's docstring, question 2.

    `resource_prefix`, when given, namespaces every symbol OCIO's own
    codegen emits (via GpuShaderDesc.setResourcePrefix) -- needed only
    when TWO separately-extracted OCIO shaders are combined into one GLSL
    program (see gpu_printer_lights_shader.py, which decodes to linear,
    applies a hand-written gain uniform, then re-encodes -- each half
    needs its own prefix so neither's internal globals collide with the
    other's, even though setFunctionName alone already keeps their two
    entry-point function names distinct). None (the default) matches
    every existing single-OCIO-shader caller's prior behavior exactly."""
    gpu = processor.getDefaultGPUProcessor()
    desc = ocio.GpuShaderDesc.CreateShaderDesc()
    desc.setLanguage(ocio.GPU_LANGUAGE_GLSL_1_2)  # matches this pipeline's GLSL 1.20 target -- see _VERTEX_SHADER's comment
    if resource_prefix is not None:
        desc.setResourcePrefix(resource_prefix)
    desc.setFunctionName(function_name)
    gpu.extractGpuShaderInfo(desc)
    return (desc.getShaderText(), len(list(desc.getTextures())),
            len(list(desc.get3DTextures())), len(list(desc.getUniforms())))


def _extract_ocio_glsl_with_2d_textures(processor, function_name="OCIOMain", resource_prefix=None):
    """Like _extract_ocio_glsl, but also returns the REAL 2D-texture data
    (values/width/height/sampler name) any 2D textures OCIO's own codegen
    baked into the shader need uploaded -- see gpu_aces_drt_shader.py's
    own docstring for a real, reproduced case (every HDR ACES Output
    Transform view needs one, a real bug this app shipped with until it
    was found and fixed) of a caller that can't just ignore this the way
    every previous single-OCIO-shader caller here safely could (each of
    THEIR transforms happens to need zero textures, confirmed directly).

    Only handles 2D textures (desc.getTextures()) -- no caller of this
    function has needed a 3D texture; that's gpu_lut_shader.py's own,
    separate, Lut3DTransform-specific path (a different OCIO transform
    kind with a genuinely different GPU shape, same "different shape,
    different code path" precedent every GPU cache module in this app
    already follows).

    Returns (shader_text, textures) -- `textures` a list of dicts
    ({"sampler_name", "values" (a (height, width) float32 ndarray),
    "width", "height"}), empty for any transform that needs none."""
    gpu = processor.getDefaultGPUProcessor()
    desc = ocio.GpuShaderDesc.CreateShaderDesc()
    desc.setLanguage(ocio.GPU_LANGUAGE_GLSL_1_2)
    if resource_prefix is not None:
        desc.setResourcePrefix(resource_prefix)
    desc.setFunctionName(function_name)
    gpu.extractGpuShaderInfo(desc)
    textures = []
    for t in desc.getTextures():
        values = np.asarray(t.getValues(), dtype=np.float32).reshape(t.height, t.width)
        textures.append({"sampler_name": t.samplerName, "values": values,
                          "width": t.width, "height": t.height})
    return desc.getShaderText(), textures


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


def compile_ocio_program(ocio_shader_text):
    """Link `ocio_shader_text` (an OCIOMain-defining GLSL fragment --
    either OCIO's own generated text, or gpu_cdl_shader.CDL_SHADER_SOURCE,
    since both define the same OCIOMain(vec4)->vec4 contract) into a full,
    ready-to-use program. Split out from render_ocio_shader_on_gpu (below)
    so gpu_shader_cache.py's caching layer can compile once per distinct
    stage configuration and reuse the resulting program across many
    render_program_on_gpu calls -- the whole point of Stage 3."""
    return _link_program(_VERTEX_SHADER,
                          _FRAGMENT_SHADER_TEMPLATE.format(ocio_shader_text=ocio_shader_text))


def link_custom_program(vertex_src, fragment_src):
    """Public escape hatch for a shader that does NOT fit the OCIOMain(
    vec4)->vec4 / single-uInput contract every compile_ocio_program
    caller so far has used -- e.g. gpu_fork_merge_shader.py's fork_merge
    blend, which takes THREE input textures (the pre-split signal, and
    each branch's own result) instead of one. Every other GPU module in
    this app reaches for `_link_program`/`_VERTEX_SHADER` directly as
    module-private (leading-underscore) details of this file; this is
    the one sanctioned, public way for another module to compile a
    genuinely custom vertex+fragment pair instead of reaching past that
    underscore itself."""
    return _link_program(vertex_src, fragment_src)


_FULLSCREEN_QUAD_VERTICES = np.array(
    [-1.0, -1.0, 1.0, -1.0, -1.0, 1.0, 1.0, 1.0], dtype=np.float32)


def make_fullscreen_quad_vbo():
    """A VBO holding 4 vertices (NDC corners) for a GL_TRIANGLE_STRIP
    covering the whole viewport -- GLSL 1.20/OpenGL 2.1 needs an actual
    vertex buffer for this (unlike the newer gl_VertexID-based "no VBO
    needed" trick, which needs GLSL 130+ and isn't available under this
    pipeline's target version -- see _VERTEX_SHADER's own comment on why
    that version was chosen). Callers that issue many draws per frame
    (gpu_stack_renderer.py's per-stage chain, gpu_lanczos_compositor.py's
    passes) should create ONE of these and reuse it via
    bind_fullscreen_quad before each draw, rather than recreating a
    buffer every single pass."""
    vbo = gl.glGenBuffers(1)
    gl.glBindBuffer(gl.GL_ARRAY_BUFFER, vbo)
    gl.glBufferData(gl.GL_ARRAY_BUFFER, _FULLSCREEN_QUAD_VERTICES.nbytes,
                     _FULLSCREEN_QUAD_VERTICES, gl.GL_STATIC_DRAW)
    gl.glBindBuffer(gl.GL_ARRAY_BUFFER, 0)
    return vbo


def bind_fullscreen_quad(vbo, program):
    """Binds `vbo` and wires it up to `program`'s `aPos` vertex attribute
    -- call once before each `glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)` that
    renders the fullscreen quad (every draw in this whole GPU pipeline).
    Returns `loc` (the attribute location just enabled) -- callers MUST
    pass this to `unbind_fullscreen_quad` right after their draw call, so
    the enabled vertex attrib array doesn't leak past this one draw (a
    real gap found and fixed 2026-08-09: with no VAO in this GLSL 1.20/
    Compatibility-Profile pipeline, glEnableVertexAttribArray sets
    CONTEXT-level state with no natural per-draw scope of its own -- left
    enabled indefinitely, unlike everything else this pipeline binds
    (textures/framebuffers/programs), which the NEXT bind call always
    overwrites regardless. A stray enabled attrib array at this widget's
    OWN attribute-0-shaped location, still live the next time this same
    context does ANYTHING else that reads vertex attributes, is exactly
    the kind of leftover state this whole GLSL-1.20 rewrite was already
    working around pyqtgraph's own VAO-less GL code for -- disabling it
    explicitly removes one more real unknown from that interaction)."""
    gl.glBindBuffer(gl.GL_ARRAY_BUFFER, vbo)
    loc = gl.glGetAttribLocation(program, "aPos")
    gl.glEnableVertexAttribArray(loc)
    gl.glVertexAttribPointer(loc, 2, gl.GL_FLOAT, gl.GL_FALSE, 0, None)
    return loc


def unbind_fullscreen_quad(loc):
    """Disables the vertex attrib array `bind_fullscreen_quad` enabled --
    see that function's own docstring for why this pairing matters."""
    gl.glDisableVertexAttribArray(loc)


def upload_rgba32f_texture(rgb_array):
    """(H,W,3) float array (any range) -> a new GL_TEXTURE_2D holding it
    as RGBA32F (alpha forced to 1.0), returns (texture_id, w, h). Full
    float32, not a normalized 8-bit format, for the same reason
    render_program_on_gpu always has been: a precision comparison against
    the CPU path should reflect the shader math itself, not an incidental
    texture-format round-trip. NEAREST filtering + CLAMP_TO_EDGE -- this
    is a source texture for exact per-texel stage math (gpu_stack_
    renderer.py) or Lanczos-kernel sampling (a future compositor), never
    something that should get GL's own built-in bilinear/mipmap
    filtering applied on top."""
    h, w = rgb_array.shape[:2]
    rgba = np.empty((h, w, 4), dtype=np.float32)
    rgba[..., :3] = rgb_array
    rgba[..., 3] = 1.0
    # OpenGL's texture origin is bottom-left; numpy/PIL's is top-left.
    # Irrelevant to the actual per-pixel math (every pixel gets the same
    # transform regardless of row order), so not bothering to flip --
    # flagged only so a future reader doesn't wonder why row 0 here
    # doesn't visually match row 0 of a saved PNG.
    rgba = np.ascontiguousarray(rgba)

    tex = gl.glGenTextures(1)
    gl.glBindTexture(gl.GL_TEXTURE_2D, tex)
    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_NEAREST)
    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_NEAREST)
    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
    gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA32F, w, h, 0, gl.GL_RGBA, gl.GL_FLOAT, rgba)
    return tex, w, h


def create_rgba32f_render_target(w, h):
    """A new (empty) GL_TEXTURE_2D + the GL_FRAMEBUFFER it's attached to
    as GL_COLOR_ATTACHMENT0, sized w x h, RGBA32F -- a renderable output
    target for a stage/compositor pass. Returns (texture_id, fbo_id).
    Raises if the FBO isn't complete -- that's a real setup bug, not a
    recoverable condition, so failing loudly here beats a silently wrong
    render later."""
    tex = gl.glGenTextures(1)
    gl.glBindTexture(gl.GL_TEXTURE_2D, tex)
    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_NEAREST)
    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_NEAREST)
    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
    gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA32F, w, h, 0, gl.GL_RGBA, gl.GL_FLOAT, None)

    # Restores whatever was ambiently bound before this call, NOT "0" --
    # this can run mid-paintGL (e.g. via _ensure_side_target/
    # _ensure_ping_pong creating a new render target on a resize), and
    # "0" is not a valid/meaningful framebuffer to leave bound inside a
    # QOpenGLWidget's paintGL (its real render target is an internally-
    # managed FBO) -- see gpu_stack_renderer.GpuStackRenderer.render_
    # chain's own comment for the full story on why this class of bug
    # produced a real, reproduced GL_INVALID_FRAMEBUFFER_OPERATION.
    ambient_fbo = gl.glGetIntegerv(gl.GL_FRAMEBUFFER_BINDING)
    fbo = gl.glGenFramebuffers(1)
    gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, fbo)
    gl.glFramebufferTexture2D(gl.GL_FRAMEBUFFER, gl.GL_COLOR_ATTACHMENT0,
                               gl.GL_TEXTURE_2D, tex, 0)
    status = gl.glCheckFramebufferStatus(gl.GL_FRAMEBUFFER)
    gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, ambient_fbo)
    if status != gl.GL_FRAMEBUFFER_COMPLETE:
        raise RuntimeError(f"Incomplete FBO: 0x{status:x}")
    return tex, fbo


def render_program_on_gpu(program, rgb_array, set_uniforms=None):
    """Render `rgb_array` (H,W,3 float32, any range -- NOT assumed [0,1])
    through an already-compiled/linked `program` (see compile_ocio_program)
    on the actual GPU, returns an (H,W,3) float32 array. Full float32
    throughout (input texture, framebuffer attachment, and readback all
    RGBA32F) so a precision comparison against the CPU path reflects the
    shader MATH itself, not an incidental 8-bit texture round-trip that
    would exist in a real app's preview pipeline anyway but would muddy
    what this specific comparison is trying to isolate.

    One-shot convenience (fresh VBO/textures/FBO every call, all deleted
    before returning) -- fine for this script's own validation runs and
    the test suite, wasteful for a real per-frame render loop, which is
    exactly why gpu_stack_renderer.py's chain renderer uses
    upload_rgba32f_texture/create_rgba32f_render_target/
    make_fullscreen_quad_vbo directly instead of this function, reusing
    them across many renders rather than paying GL object churn every
    pass.

    `set_uniforms`, if given, is called as `set_uniforms(program)` right
    after glUseProgram but before the draw call -- e.g.
    gpu_cdl_shader.set_cdl_uniforms bound via functools.partial -- the
    hook a uniform-driven shader (unlike OCIO's own constants-baked
    codegen) needs to actually push its current parameter values before
    each render. Does NOT compile/delete `program` -- caller (or
    gpu_shader_cache.py) owns that lifecycle, since the entire reason this
    is split from render_ocio_shader_on_gpu is to let one compiled program
    render many times across edits without recompiling."""
    vbo = make_fullscreen_quad_vbo()
    tex_in, w, h = upload_rgba32f_texture(rgb_array)
    tex_out, fbo = create_rgba32f_render_target(w, h)

    gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, fbo)
    gl.glViewport(0, 0, w, h)
    gl.glUseProgram(program)
    # Default full-strength (see _FRAGMENT_SHADER_TEMPLATE's own comment on
    # uStageOpacity) -- set BEFORE set_uniforms so a caller that actually
    # wants a specific opacity (gpu_stack_renderer.py's per-stage chain)
    # can still override it; every other existing caller doesn't need to
    # know uStageOpacity exists at all.
    gl.glUniform1f(gl.glGetUniformLocation(program, "uStageOpacity"), 1.0)
    if set_uniforms is not None:
        set_uniforms(program)
    gl.glActiveTexture(gl.GL_TEXTURE0)
    gl.glBindTexture(gl.GL_TEXTURE_2D, tex_in)
    gl.glUniform1i(gl.glGetUniformLocation(program, "uInput"), 0)
    aPos_loc = bind_fullscreen_quad(vbo, program)
    gl.glDrawArrays(gl.GL_TRIANGLE_STRIP, 0, 4)
    unbind_fullscreen_quad(aPos_loc)

    result = gl.glReadPixels(0, 0, w, h, gl.GL_RGBA, gl.GL_FLOAT)
    result = np.frombuffer(result, dtype=np.float32).reshape(h, w, 4)

    gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0)
    gl.glDeleteFramebuffers(1, [fbo])
    gl.glDeleteTextures([tex_in, tex_out])
    gl.glDeleteBuffers(1, [vbo])

    return result[..., :3].copy()


def render_ocio_shader_on_gpu(ocio_shader_text, rgb_array):
    """One-shot convenience: compile `ocio_shader_text` into a fresh
    program, render `rgb_array` through it once, delete the program.
    Used by this script's own CDL/ACES DRT validation below, where each
    stage configuration is only ever rendered once -- gpu_shader_cache.py
    uses compile_ocio_program/render_program_on_gpu directly instead,
    since ITS whole point is reusing one compiled program across many
    renders."""
    program = compile_ocio_program(ocio_shader_text)
    try:
        return render_program_on_gpu(program, rgb_array)
    finally:
        gl.glDeleteProgram(program)


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
