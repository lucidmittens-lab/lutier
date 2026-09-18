"""Tests for WipeCompareWidget.read_back_after_frame -- run directly:
python3 test_wipe_compare_widget_readback.py

Requires a REAL windowing system, not just a real (offscreen but
genuine) GL context -- unlike test_wipe_compare_widget_gpu_state.py
(which drives _SideState.ensure_texture directly against a raw
QOffscreenSurface/QOpenGLContext, bypassing Qt's own widget paint
delivery entirely), read_back_after_frame needs WipeCompareWidget's OWN
paintGL to have actually run at least once, which needs the widget shown
in a real window and the Qt event loop pumped -- confirmed directly:
under QT_QPA_PLATFORM=offscreen, QOpenGLWidget.isValid() is always False
and paintGL never runs at all (same limitation documented elsewhere in
this project, e.g. lut_viewer_3d.py's own notes on pyqtgraph under
offscreen). Run this file's own process WITHOUT setting
QT_QPA_PLATFORM=offscreen -- excluded from `pytest -q` (which runs the
rest of this project's suite under offscreen) via conftest.py's
collect_ignore for exactly that reason.
"""
import sys

import numpy as np
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import stack as st
from stack import Stack, make_cdl_stage
from wipe_compare_widget import WipeCompareWidget

_PAINT_WAIT_MS = 200


def _app():
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _shown_widget(size=(64, 48)):
    _app()
    w = WipeCompareWidget()
    w.resize(*size)
    w.show()
    return w


def test_read_back_after_frame_returns_none_before_any_set_after():
    w = _shown_widget()
    QTest.qWait(_PAINT_WAIT_MS)
    assert w.read_back_after_frame() is None
    print("WipeCompareWidget.read_back_after_frame: returns None before any set_after -- OK")


def test_read_back_after_frame_matches_cpu_grading_gpu_eligible():
    w = _shown_widget()
    array = np.random.default_rng(0).uniform(0, 1, size=(48, 64, 3)).astype(np.float32)
    stack = Stack()
    stack.add_stage(make_cdl_stage(slope=(1.2, 0.9, 1.0), offset=(0.02, 0.0, -0.01)))
    w.set_after(array, gpu_stack=stack)

    QTest.qWait(_PAINT_WAIT_MS)
    result = w.read_back_after_frame()
    assert result is not None
    assert result.shape == (48, 64, 3)

    # Not clamped, same as render_chain's own documented contract (an
    # out-of-[0,1] value from a slope > 1 legitimately shows up here) --
    # clip both sides before comparing against the CPU path, which DOES
    # clip at the end (apply_stack_to_image_array's own final np.clip).
    expected = st.apply_stack_to_image_array(stack, array)
    delta = np.abs(np.clip(result, 0.0, 1.0).astype(np.float64) - expected.astype(np.float64))
    assert delta.max() < 1e-3, delta.max()
    print(f"WipeCompareWidget.read_back_after_frame: matches CPU grading for a GPU-eligible "
          f"stack, max|delta|={delta.max():.7f} -- OK")


def test_read_back_after_frame_reflects_a_live_stack_edit():
    w = _shown_widget()
    array = np.random.default_rng(1).uniform(0, 1, size=(48, 64, 3)).astype(np.float32)
    stack = Stack()
    stack.add_stage(make_cdl_stage(slope=(1.0, 1.0, 1.0)))
    w.set_after(array, gpu_stack=stack)
    QTest.qWait(_PAINT_WAIT_MS)
    first = w.read_back_after_frame()

    stack.stages[0].params["slope"] = (1.3, 0.7, 1.0)
    w.set_after(array, gpu_stack=stack)  # sample_image_panel.py's own refresh_preview pattern
    QTest.qWait(_PAINT_WAIT_MS)
    second = w.read_back_after_frame()

    assert not np.allclose(first, second, atol=1e-4), "an edited stack must produce a genuinely different readback"
    expected = st.apply_stack_to_image_array(stack, array)
    delta = np.abs(np.clip(second, 0.0, 1.0).astype(np.float64) - expected.astype(np.float64))
    assert delta.max() < 1e-3, delta.max()
    print("WipeCompareWidget.read_back_after_frame: reflects a live stack edit on the very next readback -- OK")


def test_read_back_after_frame_works_for_a_gpu_ineligible_already_graded_array():
    """sample_image_panel.py's own CPU fallback path (a GPU-ineligible
    stack, e.g. containing a fork): it CPU-grades the array itself first,
    then calls set_after(already_graded_array, gpu_stack=None) --
    _SideState.ensure_texture's own contract is that graded_texture ==
    native_texture verbatim in this case (no further GPU transform), so
    the readback should hand back exactly the already-graded array."""
    w = _shown_widget()
    already_graded = np.random.default_rng(2).uniform(0, 1, size=(48, 64, 3)).astype(np.float32)
    w.set_after(already_graded, gpu_stack=None)

    QTest.qWait(_PAINT_WAIT_MS)
    result = w.read_back_after_frame()
    assert result is not None
    assert np.allclose(result, already_graded, atol=1e-4)
    print("WipeCompareWidget.read_back_after_frame: hands back the already-CPU-graded array "
          "verbatim for a GPU-ineligible stack -- OK")


def _click(target, pos, button=Qt.MouseButton.LeftButton):
    """Same synthetic-QMouseEvent approach test_wipe_compare_widget.py's
    own _press uses -- calling this directly (not QTest.mouseClick, which
    needs actual OS event delivery to the shown window) is enough since
    mousePressEvent is a plain Qt virtual method."""
    event = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(*pos), QPointF(*pos),
                         button, button, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target, event)


def test_arm_eyedropper_resolves_the_real_on_screen_click_fraction():
    """The one thing test_wipe_compare_widget.py's own offscreen
    eyedropper tests can't cover (a real _image_rect() geometry from an
    actually-painted frame) -- an armed click against a REAL painted
    frame resolves to the correct image-relative fraction. arm_eyedropper
    itself no longer samples a pixel VALUE at all (see its own docstring
    for why: it only knows the final on-screen composited result, which
    is the wrong thing for a caller -- e.g. a Keyer's Pick -- that wants
    a specific mid-stack stage's own actual input; resolving position to
    a pixel VALUE is the caller's own job now, against whatever array it
    considers authoritative)."""
    w = _shown_widget()
    array = np.zeros((48, 64, 3), dtype=np.float32)
    array[:, :32] = (0.6, 0.2, 0.2)  # left half reddish
    array[:, 32:] = (0.2, 0.2, 0.6)  # right half bluish
    w.set_after(array, gpu_stack=None)
    QTest.qWait(_PAINT_WAIT_MS)

    picked = []
    w.arm_eyedropper(lambda x_frac, y_frac: picked.append((x_frac, y_frac)))
    image_rect = w._image_rect()
    left_pos = (image_rect.left() + image_rect.width() * 0.25, image_rect.center().y())
    _click(w, left_pos)

    assert len(picked) == 1
    assert abs(picked[0][0] - 0.25) < 1e-2
    assert abs(picked[0][1] - 0.5) < 1e-2
    assert w._eyedropper_armed is False
    print(f"WipeCompareWidget.arm_eyedropper: a click against a real "
          f"painted frame resolves the correct image fraction, got "
          f"{picked[0]} -- OK")


def test_arm_eyedropper_disarms_after_exactly_one_pick():
    w = _shown_widget()
    array = np.full((48, 64, 3), (0.5, 0.5, 0.5), dtype=np.float32)
    w.set_after(array, gpu_stack=None)
    QTest.qWait(_PAINT_WAIT_MS)

    picked = []
    w.arm_eyedropper(lambda x_frac, y_frac: picked.append((x_frac, y_frac)))
    image_rect = w._image_rect()
    pos = (image_rect.center().x(), image_rect.center().y())
    _click(w, pos)
    _click(w, pos)  # second click, after disarming -- must not resolve again

    assert len(picked) == 1
    print("WipeCompareWidget.arm_eyedropper: one-shot -- a second click "
          "after the first pick resolves nothing -- OK")


def test_read_back_after_frame_returns_none_after_clear():
    w = _shown_widget()
    array = np.random.default_rng(3).uniform(0, 1, size=(48, 64, 3)).astype(np.float32)
    stack = Stack()
    stack.add_stage(make_cdl_stage(slope=(1.1, 1.0, 1.0)))
    w.set_after(array, gpu_stack=stack)
    QTest.qWait(_PAINT_WAIT_MS)
    assert w.read_back_after_frame() is not None

    w.clear()
    QTest.qWait(_PAINT_WAIT_MS)
    assert w.read_back_after_frame() is None
    print("WipeCompareWidget.read_back_after_frame: returns None again after clear() -- OK")


if __name__ == "__main__":
    test_read_back_after_frame_returns_none_before_any_set_after()
    test_read_back_after_frame_matches_cpu_grading_gpu_eligible()
    test_read_back_after_frame_reflects_a_live_stack_edit()
    test_read_back_after_frame_works_for_a_gpu_ineligible_already_graded_array()
    test_arm_eyedropper_resolves_the_real_on_screen_click_fraction()
    test_arm_eyedropper_disarms_after_exactly_one_pick()
    test_read_back_after_frame_returns_none_after_clear()
    print()
    print("All wipe_compare_widget_readback tests passed.")
