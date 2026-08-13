"""Tests for gpu_fork_merge_shader.py -- run directly:
python3 test_gpu_fork_merge_shader.py

Isolation-level tests for the shader itself, in ADDITION to (not instead
of) the full end-to-end fork tests in test_gpu_stack_renderer.py -- those
already exercise this shader as part of a real multi-stage chain; these
verify its own 3-texture blend math directly against stack.py's CPU
reference (_blend_two + the fork_merge opacity blend in _run_stages),
same "own dedicated test file per shader module" convention every other
gpu_*_shader.py module in this app already follows.

Requires a real (offscreen but genuine) OpenGL context via Qt's
QOffscreenSurface/QOpenGLContext -- same requirement as
gpu_shader_prototype.py itself. Excluded from the plain `pytest -q` suite
via conftest.py's `collect_ignore`, same real GL-context-interaction
reason documented there for every other gpu_*_shader test file.
Run standalone: `python3 test_gpu_fork_merge_shader.py`.
"""
import numpy as np
import OpenGL.GL as gl

import gpu_fork_merge_shader as gfm
import gpu_shader_prototype as gsp


def _blend_two_reference(a, b, blend):
    """Mirrors stack.py's own _blend_two exactly -- duplicated here
    (rather than imported) so this file tests the SHADER against the
    documented CPU contract directly, not against whatever stack.py
    happens to import at test time."""
    if blend >= 1.0:
        return b
    if blend <= 0.0:
        return a
    return a * (1.0 - blend) + b * blend


def _fork_merge_reference(running, branch_a, branch_b, blend, opacity):
    combined = _blend_two_reference(branch_a, branch_b, blend)
    if opacity >= 1.0:
        return combined
    if opacity <= 0.0:
        return running
    return running * (1.0 - opacity) + combined * opacity


def _render_and_read(program, running, branch_a, branch_b, blend, opacity, vbo):
    tex_running, w, h = gsp.upload_rgba32f_texture(running)
    tex_a, _, _ = gsp.upload_rgba32f_texture(branch_a)
    tex_b, _, _ = gsp.upload_rgba32f_texture(branch_b)
    tex_out, fbo = gsp.create_rgba32f_render_target(w, h)
    try:
        gfm.render_fork_merge_pass(program, tex_running, tex_a, tex_b, blend, opacity, fbo, w, h, vbo)
        pixels = gl.glReadPixels(0, 0, w, h, gl.GL_RGBA, gl.GL_FLOAT)
        return np.frombuffer(pixels, dtype=np.float32).reshape(h, w, 4)[..., :3].copy()
    finally:
        gl.glDeleteFramebuffers(1, [fbo])
        gl.glDeleteTextures([tex_running, tex_a, tex_b, tex_out])


def test_fork_merge_shader_matches_cpu_across_blend_and_opacity_values():
    _app, context, surface = gsp._make_offscreen_gl_context()
    program = gfm.compile_fork_merge_program()
    vbo = gsp.make_fullscreen_quad_vbo()

    rng = np.random.default_rng(20260812)
    # Deliberately a wide range including negative and >1.0 values -- a
    # pre-split "running" signal from an earlier ACES-family stage can
    # legitimately be outside [0,1], same reasoning gpu_shader_prototype.
    # py's own _test_images() docstring gives for testing this range.
    running = rng.uniform(-0.15, 1.15, size=(48, 48, 3)).astype(np.float32)
    branch_a = rng.uniform(-0.15, 1.15, size=(48, 48, 3)).astype(np.float32)
    branch_b = rng.uniform(-0.15, 1.15, size=(48, 48, 3)).astype(np.float32)

    max_delta = 0.0
    for blend in (0.0, 0.25, 0.5, 0.75, 1.0):
        for opacity in (0.0, 0.3, 0.5, 0.7, 1.0):
            gpu_result = _render_and_read(program, running, branch_a, branch_b, blend, opacity, vbo)
            cpu_result = _fork_merge_reference(running, branch_a, branch_b, blend, opacity)
            delta = np.abs(gpu_result.astype(np.float64) - cpu_result.astype(np.float64))
            max_delta = max(max_delta, delta.max())
            assert delta.max() < 1e-5, (blend, opacity, delta.max())

    gl.glDeleteBuffers(1, [vbo])
    gl.glDeleteProgram(program)
    context.doneCurrent()
    print(f"gpu_fork_merge_shader: matches the CPU reference across every "
          f"blend/opacity combination tested, max|delta|={max_delta:.7f} -- OK")


def test_opacity_zero_leaves_running_completely_unchanged():
    """The one case worth its own dedicated test: opacity=0.0 must return
    `running` EXACTLY (bit-for-bit through the texture round-trip), even
    if branch_a/branch_b contain values that would produce a very
    different result at any other opacity -- same guarantee stack.py's
    own _run_stages gives by literally skipping the blend line entirely
    at opacity<=0.0, not just approaching it in the limit."""
    _app, context, surface = gsp._make_offscreen_gl_context()
    program = gfm.compile_fork_merge_program()
    vbo = gsp.make_fullscreen_quad_vbo()

    rng = np.random.default_rng(7)
    running = rng.uniform(0.0, 1.0, size=(16, 16, 3)).astype(np.float32)
    branch_a = np.ones((16, 16, 3), dtype=np.float32) * 5.0  # wildly different from `running`
    branch_b = np.zeros((16, 16, 3), dtype=np.float32)

    gpu_result = _render_and_read(program, running, branch_a, branch_b, 0.5, 0.0, vbo)
    delta = np.abs(gpu_result.astype(np.float64) - running.astype(np.float64))
    assert delta.max() < 1e-6, delta.max()

    gl.glDeleteBuffers(1, [vbo])
    gl.glDeleteProgram(program)
    context.doneCurrent()
    print("gpu_fork_merge_shader: opacity=0.0 leaves the running signal "
          "completely unchanged regardless of branch content -- OK")


if __name__ == "__main__":
    test_fork_merge_shader_matches_cpu_across_blend_and_opacity_values()
    test_opacity_zero_leaves_running_completely_unchanged()
    print()
    print("All gpu_fork_merge_shader tests passed.")
