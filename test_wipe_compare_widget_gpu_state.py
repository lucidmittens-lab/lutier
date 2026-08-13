"""Tests for wipe_compare_widget._SideState's GL-resident state -- run
directly: python3 test_wipe_compare_widget_gpu_state.py

Requires a real (offscreen but genuine) OpenGL context -- excluded from
`pytest -q` via conftest.py's collect_ignore, same reason as every other
real-GL-context GPU test file (test_wipe_compare_widget.py itself stays
in the regular suite since its OWN tests -- zoom/pan/wheel event logic --
never touch real GL, only this module's _SideState.ensure_texture does).

Covers a real, reproduced 2026-08-10 bug: _SideState.ensure_texture used
to gate texture re-upload and GPU-chain re-render behind ONE combined
(array_fp, stack_fp) fingerprint, so a live slider drag -- which changes
the STACK fingerprint on every tick -- also re-uploaded the completely
UNCHANGED native-resolution source array to the GPU from scratch on
every single edit (a real, measurable cost: ~130MB of glTexImage2D
traffic for a 4K source, on every tick of a drag), not just re-running
the actual color chain against the already-uploaded texture. Found while
chasing a "still feels slow" report after an earlier, unrelated CPU-side
fix (see sample_image_panel.py's SCOPES_MAX_DIMENSION) didn't fully
explain the remaining lag.
"""
import numpy as np
import OpenGL.GL as gl

import gpu_shader_prototype as gsp
import gpu_stack_renderer
from stack import Stack, make_cdl_stage
from wipe_compare_widget import _SideState


def _read_texture(tex, w, h):
    fbo = gl.glGenFramebuffers(1)
    gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, fbo)
    gl.glFramebufferTexture2D(gl.GL_FRAMEBUFFER, gl.GL_COLOR_ATTACHMENT0, gl.GL_TEXTURE_2D, tex, 0)
    pixels = gl.glReadPixels(0, 0, w, h, gl.GL_RGBA, gl.GL_FLOAT)
    result = np.frombuffer(pixels, dtype=np.float32).reshape(h, w, 4)[..., :3].copy()
    gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0)
    gl.glDeleteFramebuffers(1, [fbo])
    return result


def test_editing_the_stack_does_not_reupload_the_unchanged_array():
    """The actual bug: two ensure_texture() calls with the SAME array
    object but a DIFFERENT (edited) stack must reuse the same native
    texture object (no delete+reupload), while still re-running the
    color chain so the graded output reflects the new stack params."""
    _app, context, surface = gsp._make_offscreen_gl_context()
    renderer = gpu_stack_renderer.GpuStackRenderer()
    state = _SideState()

    array = np.random.default_rng(0).uniform(0, 1, size=(16, 16, 3)).astype(np.float32)
    stack = Stack()
    stack.add_stage(make_cdl_stage(slope=(1.0, 1.0, 1.0)))

    state.set(array, stack)
    state.ensure_texture(renderer)
    native_tex_before = state.native_texture
    w, h = state.native_texture_size
    result_before = _read_texture(state.graded_texture, w, h)
    assert native_tex_before is not None

    # Simulate a live edit -- SAME array object, stack params changed.
    stack.stages[0].params["slope"] = (1.2, 0.9, 1.0)
    state.set(array, stack)  # sample_image_panel.py calls set_after(array, gpu_stack=...) on every refresh
    state.ensure_texture(renderer)
    result_after = _read_texture(state.graded_texture, w, h)

    assert state.native_texture == native_tex_before, (
        "editing the stack must NOT delete/reupload the unchanged native texture")
    assert not np.allclose(result_before, result_after, atol=1e-4), (
        "editing the stack MUST re-run the color chain -- the graded pixels should actually differ")

    renderer.clear_gl_resources()
    state.clear_gl_resources()
    context.doneCurrent()
    print("_SideState.ensure_texture: editing the stack reuses the "
          "already-uploaded native texture, only re-runs the color chain -- OK")


def test_changing_the_array_does_reupload():
    _app, context, surface = gsp._make_offscreen_gl_context()
    renderer = gpu_stack_renderer.GpuStackRenderer()
    state = _SideState()

    rng = np.random.default_rng(1)
    array_a = rng.uniform(0, 1, size=(16, 16, 3)).astype(np.float32)
    array_b = rng.uniform(0, 1, size=(16, 16, 3)).astype(np.float32)
    stack = Stack()
    stack.add_stage(make_cdl_stage(slope=(1.0, 1.0, 1.0)))

    state.set(array_a, stack)
    state.ensure_texture(renderer)
    w, h = state.native_texture_size
    uploaded_a = _read_texture(state.native_texture, w, h)

    state.set(array_b, stack)  # a genuinely new array (e.g. a different image loaded)
    state.ensure_texture(renderer)
    uploaded_b = _read_texture(state.native_texture, w, h)

    # A freed texture ID can be recycled by the driver -- NOT a valid
    # signal of "was this actually re-uploaded" on its own, only the
    # actual pixel content is -- so compare content, not object identity.
    assert np.allclose(uploaded_a, array_a, atol=1e-4), "sanity check: array_a was actually uploaded first"
    assert np.allclose(uploaded_b, array_b, atol=1e-4), "a genuinely different array MUST get a fresh texture upload"
    assert not np.allclose(uploaded_b, array_a, atol=1e-4)

    renderer.clear_gl_resources()
    state.clear_gl_resources()
    context.doneCurrent()
    print("_SideState.ensure_texture: a genuinely new array is re-uploaded, as expected -- OK")


def test_repeated_calls_with_nothing_changed_are_a_true_noop():
    _app, context, surface = gsp._make_offscreen_gl_context()
    renderer = gpu_stack_renderer.GpuStackRenderer()
    state = _SideState()

    array = np.random.default_rng(2).uniform(0, 1, size=(16, 16, 3)).astype(np.float32)
    stack = Stack()
    stack.add_stage(make_cdl_stage(slope=(1.0, 1.0, 1.0)))
    state.set(array, stack)

    state.ensure_texture(renderer)
    native_tex_1, graded_tex_1 = state.native_texture, state.graded_texture

    state.set(array, stack)  # e.g. a repaint triggered by dragging the wipe divider, nothing edited
    state.ensure_texture(renderer)

    assert state.native_texture == native_tex_1
    assert state.graded_texture == graded_tex_1

    renderer.clear_gl_resources()
    state.clear_gl_resources()
    context.doneCurrent()
    print("_SideState.ensure_texture: a repeat call with nothing changed "
          "re-uploads nothing and re-renders nothing -- OK")


if __name__ == "__main__":
    test_editing_the_stack_does_not_reupload_the_unchanged_array()
    test_changing_the_array_does_reupload()
    test_repeated_calls_with_nothing_changed_are_a_true_noop()
    print()
    print("All wipe_compare_widget_gpu_state tests passed.")
