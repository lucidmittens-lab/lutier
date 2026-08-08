"""Tests for spinbox_scrub.py -- run directly: python3 test_spinbox_scrub.py

Exercises enable_drag_to_scrub via QApplication.sendEvent() on the spin
box's own lineEdit() -- that's Qt's real event-filter dispatch path (any
installed eventFilter() is offered the event before the target widget's
own event() sees it), so this drives the actual installed filter exactly
the way a real mouse drag would, just with hand-built QMouseEvents
instead of a real pointer.

Requires a real (if offscreen) QApplication -- same headless-Qt setup as
every other GUI-touching verification in this project (see RUNNING.md).
"""
import sys

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QDoubleSpinBox

from spinbox_scrub import _DRAG_THRESHOLD_PX, _FINE_DIVISOR, _PIXELS_PER_STEP, enable_drag_to_scrub


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _press(target, x, modifiers=Qt.KeyboardModifier.NoModifier):
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(x, 0), QPointF(x, 0),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, modifiers)
    QApplication.sendEvent(target, event)


def _move(target, x, modifiers=Qt.KeyboardModifier.NoModifier):
    event = QMouseEvent(
        QEvent.Type.MouseMove, QPointF(x, 0), QPointF(x, 0),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, modifiers)
    QApplication.sendEvent(target, event)


def _release(target, x, modifiers=Qt.KeyboardModifier.NoModifier):
    event = QMouseEvent(
        QEvent.Type.MouseButtonRelease, QPointF(x, 0), QPointF(x, 0),
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, modifiers)
    QApplication.sendEvent(target, event)


def _make_spin(minimum=0.0, maximum=100.0, value=50.0, step=1.0):
    spin = QDoubleSpinBox()
    spin.setRange(minimum, maximum)
    spin.setSingleStep(step)
    spin.setValue(value)
    enable_drag_to_scrub(spin)
    return spin


def test_movement_below_threshold_does_not_change_value():
    _app()
    spin = _make_spin(value=50.0)
    line_edit = spin.lineEdit()

    _press(line_edit, 100)
    _move(line_edit, 100 + _DRAG_THRESHOLD_PX - 1)  # stays just under the threshold
    assert spin.value() == 50.0
    _release(line_edit, 100 + _DRAG_THRESHOLD_PX - 1)
    print("spinbox_scrub: movement below the drag threshold leaves the "
          "value untouched (still a plain click) -- OK")


def test_dragging_right_increases_the_value():
    _app()
    spin = _make_spin(value=50.0, step=1.0)
    line_edit = spin.lineEdit()

    _press(line_edit, 100)
    _move(line_edit, 100 + _PIXELS_PER_STEP * 5)  # 5 whole steps to the right
    assert abs(spin.value() - 55.0) < 1e-9
    _release(line_edit, 100 + _PIXELS_PER_STEP * 5)
    print("spinbox_scrub: dragging right increases the value by "
          "singleStep() per _PIXELS_PER_STEP dragged -- OK")


def test_dragging_left_decreases_the_value():
    _app()
    spin = _make_spin(value=50.0, step=1.0)
    line_edit = spin.lineEdit()

    _press(line_edit, 100)
    _move(line_edit, 100 - _PIXELS_PER_STEP * 5)
    assert abs(spin.value() - 45.0) < 1e-9
    print("spinbox_scrub: dragging left decreases the value -- OK")


def test_value_is_relative_to_press_position_not_incremental():
    """Confirms deltas are computed from the ORIGINAL press position on
    every move, not accumulated tick-to-tick -- dragging out and partway
    back must land exactly where that net distance implies, not drift."""
    _app()
    spin = _make_spin(value=50.0, step=1.0)
    line_edit = spin.lineEdit()

    _press(line_edit, 100)
    _move(line_edit, 100 + _PIXELS_PER_STEP * 10)
    assert abs(spin.value() - 60.0) < 1e-9
    _move(line_edit, 100 + _PIXELS_PER_STEP * 3)  # partway back, same drag
    assert abs(spin.value() - 53.0) < 1e-9
    print("spinbox_scrub: value tracks total distance from the press "
          "point, not accumulated per-move deltas -- OK")


def test_fine_modifier_slows_the_drag():
    _app()
    spin = _make_spin(value=50.0, step=1.0)
    line_edit = spin.lineEdit()

    _press(line_edit, 100, modifiers=Qt.KeyboardModifier.ShiftModifier)
    _move(line_edit, 100 + _PIXELS_PER_STEP * 5, modifiers=Qt.KeyboardModifier.ShiftModifier)
    expected = 50.0 + 5 * (1.0 / _FINE_DIVISOR)
    assert abs(spin.value() - expected) < 1e-9
    print("spinbox_scrub: holding the fine-adjustment modifier scales "
          "the drag down by _FINE_DIVISOR -- OK")


def test_value_respects_the_spin_boxs_own_range():
    _app()
    spin = _make_spin(minimum=0.0, maximum=100.0, value=98.0, step=1.0)
    line_edit = spin.lineEdit()

    _press(line_edit, 100)
    _move(line_edit, 100 + _PIXELS_PER_STEP * 50)  # would be 148 unclamped
    assert spin.value() == 100.0
    print("spinbox_scrub: dragging past the spin box's own maximum "
          "clamps to it (QDoubleSpinBox.setValue's own behavior) -- OK")


def test_release_after_a_real_scrub_does_not_leave_dragging_state_stuck():
    """A second, independent press/drag/release cycle must behave
    identically to the first -- confirms release properly resets
    internal state rather than leaving it primed from the prior drag."""
    _app()
    spin = _make_spin(value=50.0, step=1.0)
    line_edit = spin.lineEdit()

    _press(line_edit, 100)
    _move(line_edit, 100 + _PIXELS_PER_STEP * 5)
    _release(line_edit, 100 + _PIXELS_PER_STEP * 5)
    assert abs(spin.value() - 55.0) < 1e-9

    _press(line_edit, 200)
    _move(line_edit, 200 + _PIXELS_PER_STEP * 5)
    _release(line_edit, 200 + _PIXELS_PER_STEP * 5)
    assert abs(spin.value() - 60.0) < 1e-9
    print("spinbox_scrub: state resets cleanly after release, a second "
          "drag behaves independently of the first -- OK")


if __name__ == "__main__":
    test_movement_below_threshold_does_not_change_value()
    test_dragging_right_increases_the_value()
    test_dragging_left_decreases_the_value()
    test_value_is_relative_to_press_position_not_incremental()
    test_fine_modifier_slows_the_drag()
    test_value_respects_the_spin_boxs_own_range()
    test_release_after_a_real_scrub_does_not_leave_dragging_state_stuck()
    print()
    print("All spinbox_scrub tests passed.")
