"""Tests for gpu_lanczos_compositor.WipeCompositor -- run directly:
python3 test_gpu_wipe_compositor.py

The critical thing this checks that the lower-level LanczosResampler
tests (test_gpu_lanczos_compositor.py) don't: WipeCompositor.render is
the ONE place that converts a Qt-space (Y-down) image_rect into GL-space
(Y-up) for the resample passes (see render's own docstring). Get that
conversion wrong and every other test in this project could still pass
while the actual on-screen image renders upside down -- so this uses a
source image with a real, unambiguous top-vs-bottom marker and reads the
composited result back via glReadPixels (which has ITS OWN bottom-up
convention, a second place this could get flipped) to confirm the final
array, interpreted in normal "row 0 = top" convention, actually shows the
source's top where the top should be.

Requires a real (offscreen but genuine) OpenGL context -- excluded from
`pytest -q` via conftest.py's collect_ignore, same reason as the other
GPU test files. Run standalone: `python3 test_gpu_wipe_compositor.py`.
"""
import numpy as np
import OpenGL.GL as gl

import gpu_lanczos_compositor as glc
import gpu_shader_prototype as gsp


def _render_and_read_back(compositor, widget_w, widget_h, **render_kwargs):
    """Binds a REAL, explicit render target before calling
    compositor.render(**render_kwargs), then reads it back in NORMAL
    top-down row order (row 0 = top of the image) so callers can compare
    directly against a plain numpy/PIL-style array. Deliberately does NOT
    render into the offscreen surface's own "default" framebuffer (0) --
    confirmed via glCheckFramebufferStatus that it's GL_FRAMEBUFFER_
    UNDEFINED under a bare QOffscreenSurface (no real window-system
    backing store the way an actual on-screen QOpenGLWidget's internal
    FBO provides). In the real app, WipeCompareWidget.paintGL is called
    with Qt's OWN internal FBO already bound and valid -- this helper's
    explicit target FBO is this test's own stand-in for that, not a
    workaround for a bug in WipeCompositor itself."""
    target_tex, target_fbo = gsp.create_rgba32f_render_target(widget_w, widget_h)
    gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, target_fbo)
    compositor.render(widget_w=widget_w, widget_h=widget_h, **render_kwargs)
    pixels = gl.glReadPixels(0, 0, widget_w, widget_h, gl.GL_RGBA, gl.GL_FLOAT)
    arr = np.frombuffer(pixels, dtype=np.float32).reshape(widget_h, widget_w, 4)[..., :3].copy()
    gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0)
    gl.glDeleteTextures([target_tex])
    gl.glDeleteFramebuffers(1, [target_fbo])
    return arr[::-1, :, :]  # GL row 0 = bottom -> flip to row 0 = top


def test_after_only_renders_right_side_up_filling_the_widget():
    """wipe disabled, image_rect exactly fills the widget, source has an
    unambiguous top(black)/bottom(white) split -- the composited result
    should have the SAME split in the SAME orientation, not flipped."""
    _app, context, surface = gsp._make_offscreen_gl_context()
    widget_w, widget_h = 64, 64
    src = np.zeros((40, 40, 3), dtype=np.float32)
    src[20:, :, :] = 1.0  # top half black, bottom half white

    tex, w, h = gsp.upload_rgba32f_texture(src)

    compositor = glc.WipeCompositor()
    result = _render_and_read_back(
        compositor, widget_w, widget_h,
        before_texture=None, before_size=None,
        after_texture=tex, after_size=(w, h),
        image_rect_qt=(0, 0, widget_w, widget_h),
        wipe_enabled=False, divider_fraction=0.5,
        background_color=(0.0, 0.5, 0.0), placeholder_color=(0.2, 0.2, 0.2))
    compositor.clear_gl_resources()
    gl.glDeleteTextures([tex])
    context.doneCurrent()

    top_quarter = result[: widget_h // 4]
    bottom_quarter = result[-widget_h // 4:]
    assert top_quarter.mean() < 0.1, (
        f"top of the rendered result should be black (source's top half), got mean {top_quarter.mean()}")
    assert bottom_quarter.mean() > 0.9, (
        f"bottom of the rendered result should be white (source's bottom half), got mean {bottom_quarter.mean()}")
    print("WipeCompositor: a full-widget \"after\" render preserves top/"
          "bottom orientation correctly (no accidental vertical flip) -- OK")


def test_gutter_outside_image_rect_shows_background_color():
    _app, context, surface = gsp._make_offscreen_gl_context()
    widget_w, widget_h = 80, 80
    src = np.full((30, 30, 3), 0.9, dtype=np.float32)  # bright, so it's obviously NOT the background
    tex, w, h = gsp.upload_rgba32f_texture(src)

    # A small centered image_rect, well inside the widget -- plenty of
    # gutter on all sides to check.
    rect = (25, 25, 30, 30)
    background = (0.1, 0.1, 0.6)

    compositor = glc.WipeCompositor()
    result = _render_and_read_back(
        compositor, widget_w, widget_h,
        before_texture=None, before_size=None,
        after_texture=tex, after_size=(w, h),
        image_rect_qt=rect,
        wipe_enabled=False, divider_fraction=0.5,
        background_color=background, placeholder_color=(0.2, 0.2, 0.2))
    compositor.clear_gl_resources()
    gl.glDeleteTextures([tex])
    context.doneCurrent()

    corner = result[0:5, 0:5]
    np.testing.assert_allclose(corner.mean(axis=(0, 1)), background, atol=0.01)
    center = result[35:45, 35:45]
    assert center.mean() > 0.8, "the image area itself should show the bright source, not the background"
    print("WipeCompositor: the gutter outside image_rect shows the "
          "background color, the image area shows the actual content -- OK")


def test_canvas_rect_paints_a_distinct_color_between_canvas_and_image():
    """Regression test for a real, confirmed bug (Tom, 2026-08-13):
    before canvas_rect_qt existed, the region between the canvas box and
    the (smaller) image inside it fell through to background_color --
    visually indistinguishable from the region OUTSIDE the canvas box
    entirely, so Canvas Settings' letterboxing had no visible effect.
    Three concentric regions here, each a different color: background
    (outside the canvas box), canvas_color (inside the canvas box, outside
    the image), and the actual image content."""
    _app, context, surface = gsp._make_offscreen_gl_context()
    widget_w, widget_h = 100, 100
    src = np.full((20, 20, 3), 0.9, dtype=np.float32)  # bright image content
    tex, w, h = gsp.upload_rgba32f_texture(src)

    canvas_rect = (10, 10, 80, 80)  # a canvas box well inside the widget
    image_rect = (40, 40, 20, 20)  # a smaller image box well inside the canvas
    background = (0.1, 0.1, 0.6)
    canvas_color = (0.0, 0.0, 0.0)

    compositor = glc.WipeCompositor()
    result = _render_and_read_back(
        compositor, widget_w, widget_h,
        before_texture=None, before_size=None,
        after_texture=tex, after_size=(w, h),
        image_rect_qt=image_rect,
        wipe_enabled=False, divider_fraction=0.5,
        background_color=background, placeholder_color=(0.2, 0.2, 0.2),
        canvas_rect_qt=canvas_rect, canvas_color=canvas_color)
    compositor.clear_gl_resources()
    gl.glDeleteTextures([tex])
    context.doneCurrent()

    outside_canvas = result[0:5, 0:5]  # corner -- outside the canvas box entirely
    np.testing.assert_allclose(outside_canvas.mean(axis=(0, 1)), background, atol=0.01)

    inside_canvas_outside_image = result[15:20, 15:20]  # inside canvas_rect, well clear of image_rect
    np.testing.assert_allclose(inside_canvas_outside_image.mean(axis=(0, 1)), canvas_color, atol=0.01)
    assert not np.allclose(inside_canvas_outside_image.mean(axis=(0, 1)), background, atol=0.05), (
        "the canvas letterbox area must NOT match the background color, or it's invisible as a canvas at all")

    inside_image = result[45:55, 45:55]
    assert inside_image.mean() > 0.8, "the image area itself should show the bright source"
    print("WipeCompositor: canvas_rect_qt paints a real, distinct color between "
          "the canvas box and the image, separate from the outer background -- OK")


def test_canvas_rect_qt_none_leaves_original_behavior_unchanged():
    """canvas_rect_qt=None (the default -- every call site before Canvas
    Settings existed) must produce byte-identical output to before this
    parameter was added: the whole gutter outside image_rect is
    background_color, no distinct canvas-color region at all."""
    _app, context, surface = gsp._make_offscreen_gl_context()
    widget_w, widget_h = 80, 80
    src = np.full((30, 30, 3), 0.9, dtype=np.float32)
    tex, w, h = gsp.upload_rgba32f_texture(src)
    rect = (25, 25, 30, 30)
    background = (0.1, 0.1, 0.6)

    compositor = glc.WipeCompositor()
    result = _render_and_read_back(
        compositor, widget_w, widget_h,
        before_texture=None, before_size=None,
        after_texture=tex, after_size=(w, h),
        image_rect_qt=rect,
        wipe_enabled=False, divider_fraction=0.5,
        background_color=background, placeholder_color=(0.2, 0.2, 0.2))
    compositor.clear_gl_resources()
    gl.glDeleteTextures([tex])
    context.doneCurrent()

    corner = result[0:5, 0:5]
    np.testing.assert_allclose(corner.mean(axis=(0, 1)), background, atol=0.01)
    print("WipeCompositor: canvas_rect_qt=None (the default) leaves the "
          "original background-everywhere-outside-image behavior unchanged -- OK")


def test_wipe_divider_splits_before_and_after_at_the_right_screen_x():
    _app, context, surface = gsp._make_offscreen_gl_context()
    widget_w, widget_h = 60, 40
    before_src = np.zeros((20, 20, 3), dtype=np.float32)  # black
    after_src = np.ones((20, 20, 3), dtype=np.float32)  # white
    before_tex, bw, bh = gsp.upload_rgba32f_texture(before_src)
    after_tex, aw, ah = gsp.upload_rgba32f_texture(after_src)

    rect = (0, 0, widget_w, widget_h)  # fills the widget exactly

    compositor = glc.WipeCompositor()
    result = _render_and_read_back(
        compositor, widget_w, widget_h,
        before_texture=before_tex, before_size=(bw, bh),
        after_texture=after_tex, after_size=(aw, ah),
        image_rect_qt=rect,
        wipe_enabled=True, divider_fraction=0.5,
        background_color=(0.0, 1.0, 0.0), placeholder_color=(0.2, 0.2, 0.2))
    compositor.clear_gl_resources()
    gl.glDeleteTextures([before_tex, after_tex])
    context.doneCurrent()

    left_side = result[:, : widget_w // 4]
    right_side = result[:, -widget_w // 4:]
    assert left_side.mean() < 0.1, f"left of the divider should show \"before\" (black), got {left_side.mean()}"
    assert right_side.mean() > 0.9, f"right of the divider should show \"after\" (white), got {right_side.mean()}"
    print("WipeCompositor: the wipe divider correctly shows \"before\" on "
          "the left, \"after\" on the right, at the expected screen "
          "position -- OK")


if __name__ == "__main__":
    test_after_only_renders_right_side_up_filling_the_widget()
    test_gutter_outside_image_rect_shows_background_color()
    test_canvas_rect_paints_a_distinct_color_between_canvas_and_image()
    test_canvas_rect_qt_none_leaves_original_behavior_unchanged()
    test_wipe_divider_splits_before_and_after_at_the_right_screen_x()
    print()
    print("All gpu_wipe_compositor tests passed.")
