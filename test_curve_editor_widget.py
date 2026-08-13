"""Tests for curve_editor_widget.py -- run directly:
python3 test_curve_editor_widget.py

Split to match the module's own "Qt-free math + thin widget" structure:
the geometry/spline helpers are tested as plain functions first (no Qt
event synthesis needed), then the widget's drag/add/remove interactions
are exercised via real QMouseEvent objects sent through QApplication,
same convention as test_wipe_compare_widget.py/test_color_wheel_widget.py.

Requires a real (if offscreen) QApplication -- see RUNNING.md.
"""
import sys

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

import curve_editor_widget as cew
import grading_rgb_curve as grc


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _widget(size=(300, 200)):
    _app()
    w = cew.CurveEditorWidget()
    w.resize(*size)
    return w


def _mouse(target, event_type, pos, button, buttons):
    event = QMouseEvent(event_type, QPointF(*pos), QPointF(*pos), button, buttons,
                         Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target, event)


def _press(target, pos, button=Qt.MouseButton.LeftButton):
    _mouse(target, QEvent.Type.MouseButtonPress, pos, button, button)


def _move(target, pos, buttons=Qt.MouseButton.LeftButton):
    _mouse(target, QEvent.Type.MouseMove, pos, Qt.MouseButton.NoButton, buttons)


def _release(target, pos, button=Qt.MouseButton.LeftButton):
    _mouse(target, QEvent.Type.MouseButtonRelease, pos, button, Qt.MouseButton.NoButton)


# ---- pure math -----------------------------------------------------------

def test_sort_points_orders_by_x():
    assert cew.sort_points([(1.0, 0.0), (0.0, 1.0), (0.5, 0.5)]) == [(0.0, 1.0), (0.5, 0.5), (1.0, 0.0)]
    print("sort_points: orders by x ascending -- OK")


def test_insertion_index_finds_the_right_gap():
    points = [(0.0, 0.0), (0.5, 0.5), (1.0, 1.0)]
    assert cew.insertion_index(points, 0.25) == 1
    assert cew.insertion_index(points, -1.0) == 0
    assert cew.insertion_index(points, 2.0) == 3
    print("insertion_index: locates the correct sorted-list gap -- OK")


def test_neighbor_x_bounds_respects_domain_and_neighbors():
    points = [(0.0, 0.0), (0.5, 0.5), (1.0, 1.0)]
    lo, hi = cew._neighbor_x_bounds(points, 1)
    assert lo == 0.0 + cew._MIN_X_SPACING
    assert hi == 1.0 - cew._MIN_X_SPACING
    lo0, hi0 = cew._neighbor_x_bounds(points, 0)
    assert lo0 == cew.DOMAIN_MIN
    loN, hiN = cew._neighbor_x_bounds(points, 2)
    assert hiN == cew.DOMAIN_MAX
    print("_neighbor_x_bounds: middle point bounded by neighbors, ends bounded by the domain -- OK")


def test_curve_widget_roundtrip():
    rect = QRectF(0, 0, 300, 200)
    plot_rect = cew._plot_rect(rect)
    for point in [(0.0, 0.0), (1.0, 1.0), (0.5, 0.25), (cew.DOMAIN_MIN, cew.DOMAIN_MAX)]:
        widget_pos = cew._curve_to_widget(point, plot_rect)
        back = cew._widget_to_curve(widget_pos, plot_rect)
        assert abs(back[0] - point[0]) < 1e-9
        assert abs(back[1] - point[1]) < 1e-9
    print("_curve_to_widget/_widget_to_curve: round-trip exactly for points across the domain -- OK")


def test_curve_to_widget_y_is_flipped():
    rect = QRectF(0, 0, 300, 200)
    plot_rect = cew._plot_rect(rect)
    top = cew._curve_to_widget((0.5, cew.DOMAIN_MAX), plot_rect)
    bottom = cew._curve_to_widget((0.5, cew.DOMAIN_MIN), plot_rect)
    assert top.y() < bottom.y(), "the highest curve value should paint nearer the top of the widget"
    print("_curve_to_widget: Y is flipped so higher curve values paint higher on screen -- OK")


def test_catmull_rom_segments_pass_through_every_point():
    points = [(0.0, 0.0), (0.3, 0.6), (0.6, 0.2), (1.0, 1.0)]
    segments = cew.catmull_rom_bezier_segments(points)
    assert len(segments) == len(points) - 1
    # Each segment's own endpoint (p1 of the (c1, c2, p1) triple) is
    # exactly the corresponding original control point -- the curve is
    # required to pass through every point, not just approximate them.
    for (_c1, _c2, p1), expected in zip(segments, points[1:]):
        assert p1 == expected
    print("catmull_rom_bezier_segments: every original control point is an exact path endpoint -- OK")


# ---- widget interaction ----------------------------------------------

def test_set_points_and_get_points_roundtrip_without_emitting():
    w = _widget()
    seen = []
    w.pointsChanged.connect(lambda ch: seen.append(ch))
    w.set_points("red", [(0.0, 0.0), (0.4, 0.7), (1.0, 1.0)])
    assert w.get_points("red") == [(0.0, 0.0), (0.4, 0.7), (1.0, 1.0)]
    assert seen == [], "set_points is an external sync, not a live edit -- must not emit"
    print("CurveEditorWidget.set_points/get_points: round-trips without emitting pointsChanged -- OK")


def test_set_points_falls_back_to_identity_below_minimum():
    w = _widget()
    w.set_points("master", [(0.3, 0.3)])
    assert w.get_points("master") == cew.sort_points(grc.DEFAULT_CONTROL_POINTS)
    print("CurveEditorWidget.set_points: fewer than MIN_CONTROL_POINTS falls back to the identity curve -- OK")


def test_set_active_channel_switches_which_channel_drags_affect():
    w = _widget()
    w.set_active_channel("red")
    assert w.active_channel() == "red"
    print("CurveEditorWidget.set_active_channel/active_channel: round-trips -- OK")


def test_dragging_a_control_point_moves_it_and_emits():
    w = _widget()
    w.set_active_channel("master")
    w.set_points("master", [(0.0, 0.0), (0.5, 0.5), (1.0, 1.0)])
    plot_rect = cew._plot_rect(QRectF(w.rect()))
    mid_pos = cew._curve_to_widget((0.5, 0.5), plot_rect)
    target_pos = cew._curve_to_widget((0.5, 0.8), plot_rect)

    seen = []
    w.pointsChanged.connect(lambda ch: seen.append(ch))
    _press(w, (mid_pos.x(), mid_pos.y()))
    _move(w, (target_pos.x(), target_pos.y()))
    _release(w, (target_pos.x(), target_pos.y()))

    points = w.get_points("master")
    assert len(points) == 3
    moved_x, moved_y = points[1]
    assert abs(moved_x - 0.5) < 1e-6, "dragging straight up/down shouldn't move x"
    assert abs(moved_y - 0.8) < 1e-3
    assert "master" in seen
    print("CurveEditorWidget: dragging a control point moves it (bounded by neighbors) and emits pointsChanged -- OK")


def test_set_points_echo_during_a_drag_does_not_interrupt_it():
    """Regression test for a real, reported bug: stack_panel.py's
    _refresh_list unconditionally re-enters _refresh_inspector_body on
    every live edit (not just an actual stage switch, see that method's
    own comment on why), which calls CurveEditorWidget.set_points with
    the SAME channel currently being dragged, echoing back exactly the
    points this drag itself just produced -- confirmed live to make a
    drag move one pixel and then stop responding, because set_points used
    to unconditionally clear _drag_index. A mid-drag set_points call for
    the active channel must NOT interrupt the drag."""
    w = _widget()
    w.set_active_channel("master")
    w.set_points("master", [(0.0, 0.0), (0.5, 0.5), (1.0, 1.0)])
    plot_rect = cew._plot_rect(QRectF(w.rect()))
    start_pos = cew._curve_to_widget((0.5, 0.5), plot_rect)
    step1_pos = cew._curve_to_widget((0.5, 0.7), plot_rect)
    step2_pos = cew._curve_to_widget((0.5, 0.85), plot_rect)

    _press(w, (start_pos.x(), start_pos.y()))
    _move(w, (step1_pos.x(), step1_pos.y()))
    assert w._drag_index is not None, "a drag must be in progress after the first move"

    # Simulate the re-entrant refresh: an external caller echoes back
    # exactly the points this drag itself just produced, on the same
    # (active) channel, mid-drag.
    w.set_points("master", w.get_points("master"))
    assert w._drag_index is not None, "a same-channel echo of the drag's own data must not clear _drag_index"

    _move(w, (step2_pos.x(), step2_pos.y()))
    _release(w, (step2_pos.x(), step2_pos.y()))

    moved_y = w.get_points("master")[1][1]
    assert abs(moved_y - 0.85) < 1e-3, "the drag must keep tracking mouse movement after the echo"
    print("CurveEditorWidget.set_points: a same-channel echo of the drag's own data "
          "does not interrupt an in-progress drag -- OK")


def test_dragging_cannot_cross_a_neighboring_point():
    w = _widget()
    w.set_active_channel("master")
    w.set_points("master", [(0.0, 0.0), (0.5, 0.5), (0.6, 0.9), (1.0, 1.0)])
    plot_rect = cew._plot_rect(QRectF(w.rect()))
    start_pos = cew._curve_to_widget((0.5, 0.5), plot_rect)
    # Try to drag the point at x=0.5 far past its neighbor at x=0.6.
    overshoot_pos = cew._curve_to_widget((0.9, 0.5), plot_rect)

    _press(w, (start_pos.x(), start_pos.y()))
    _move(w, (overshoot_pos.x(), overshoot_pos.y()))
    _release(w, (overshoot_pos.x(), overshoot_pos.y()))

    points = w.get_points("master")
    assert points[1][0] < points[2][0], "a dragged point must never cross its neighbor's x"
    assert points[1][0] < 0.6
    print("CurveEditorWidget: dragging is clamped so a point can never cross a neighboring point's x -- OK")


def test_click_on_empty_area_adds_a_point_and_starts_dragging_it():
    """Per direct feedback: double-click-to-add wasn't a discoverable
    gesture -- a plain click on empty plot area now adds a point AND
    starts dragging it immediately, so click-and-drag places and
    positions a point in one motion, same as a standard curve tool."""
    w = _widget()
    w.set_active_channel("master")
    w.set_points("master", [(0.0, 0.0), (1.0, 1.0)])
    plot_rect = cew._plot_rect(QRectF(w.rect()))
    new_pos = cew._curve_to_widget((0.5, 0.9), plot_rect)
    drag_to_pos = cew._curve_to_widget((0.5, 0.6), plot_rect)

    seen = []
    w.pointsChanged.connect(lambda ch: seen.append(ch))
    _press(w, (new_pos.x(), new_pos.y()))

    points = w.get_points("master")
    assert len(points) == 3
    assert abs(points[1][0] - 0.5) < 1e-6
    assert abs(points[1][1] - 0.9) < 1e-6
    assert seen == ["master"]
    assert w._drag_index == 1, "the newly added point should be the one now being dragged"

    # Still holding the mouse down -- moving it should reposition the
    # point that was just added, not require a separate click.
    _move(w, (drag_to_pos.x(), drag_to_pos.y()))
    _release(w, (drag_to_pos.x(), drag_to_pos.y()))
    assert abs(w.get_points("master")[1][1] - 0.6) < 1e-3
    print("CurveEditorWidget: clicking empty plot area adds a point and starts dragging it immediately -- OK")


def test_clicking_near_an_existing_point_does_not_add_a_duplicate():
    w = _widget()
    w.set_active_channel("master")
    w.set_points("master", [(0.0, 0.0), (0.5, 0.5), (1.0, 1.0)])
    plot_rect = cew._plot_rect(QRectF(w.rect()))
    mid_pos = cew._curve_to_widget((0.5, 0.5), plot_rect)

    _press(w, (mid_pos.x(), mid_pos.y()))
    assert len(w.get_points("master")) == 3, "clicking an existing point must drag it, not add a new one"
    assert w._drag_index == 1
    print("CurveEditorWidget: clicking an existing point drags it instead of adding a duplicate -- OK")


def test_dragging_is_constrained_to_the_domain_box_even_past_the_widget_edge():
    """Per direct feedback: points must stay inside the box they're drawn
    in. Qt delivers mouseMoveEvent to the widget that received the press
    even once the cursor leaves that widget's own bounds (implicit mouse
    grab on press) -- so a real drag gesture that overshoots the widget
    must still clamp the resulting point into [DOMAIN_MIN, DOMAIN_MAX]."""
    w = _widget()
    w.set_active_channel("master")
    w.set_points("master", [(0.0, 0.0), (0.5, 0.5), (1.0, 1.0)])
    plot_rect = cew._plot_rect(QRectF(w.rect()))
    mid_pos = cew._curve_to_widget((0.5, 0.5), plot_rect)

    _press(w, (mid_pos.x(), mid_pos.y()))
    _move(w, (-500.0, -500.0))  # far outside the widget entirely, up and to the left
    _release(w, (-500.0, -500.0))

    x, y = w.get_points("master")[1]
    assert cew.DOMAIN_MIN <= x <= cew.DOMAIN_MAX
    assert cew.DOMAIN_MIN <= y <= cew.DOMAIN_MAX
    assert y == cew.DOMAIN_MAX, "dragging far above the widget should pin the point at the top of the box"
    print("CurveEditorWidget: a drag that overshoots the widget's own bounds still clamps into the domain box -- OK")


def test_right_click_removes_a_point_but_not_below_the_minimum():
    w = _widget()
    w.set_active_channel("master")
    w.set_points("master", [(0.0, 0.0), (0.5, 0.5), (1.0, 1.0)])
    plot_rect = cew._plot_rect(QRectF(w.rect()))
    mid_pos = cew._curve_to_widget((0.5, 0.5), plot_rect)

    _press(w, (mid_pos.x(), mid_pos.y()), button=Qt.MouseButton.RightButton)
    assert len(w.get_points("master")) == 2

    # Now at the minimum (2 points) -- right-clicking either remaining
    # point must be a no-op, never dropping below grc.MIN_CONTROL_POINTS.
    remaining = w.get_points("master")
    first_pos = cew._curve_to_widget(remaining[0], plot_rect)
    _press(w, (first_pos.x(), first_pos.y()), button=Qt.MouseButton.RightButton)
    assert len(w.get_points("master")) == 2
    print("CurveEditorWidget: right-click removes a point, refuses to drop below MIN_CONTROL_POINTS -- OK")


def test_reset_active_channel_resets_only_that_channel_and_emits():
    w = _widget()
    w.set_points("red", [(0.0, 0.1), (1.0, 0.9)])
    w.set_points("master", [(0.0, 0.2), (1.0, 0.8)])
    w.set_active_channel("master")

    seen = []
    w.pointsChanged.connect(lambda ch: seen.append(ch))
    w.reset_active_channel()

    assert w.get_points("master") == cew.sort_points(grc.DEFAULT_CONTROL_POINTS)
    assert w.get_points("red") == [(0.0, 0.1), (1.0, 0.9)], "reset must not touch an inactive channel"
    assert seen == ["master"]
    print("CurveEditorWidget.reset_active_channel: resets only the active channel and emits pointsChanged -- OK")


if __name__ == "__main__":
    test_sort_points_orders_by_x()
    test_insertion_index_finds_the_right_gap()
    test_neighbor_x_bounds_respects_domain_and_neighbors()
    test_curve_widget_roundtrip()
    test_curve_to_widget_y_is_flipped()
    test_catmull_rom_segments_pass_through_every_point()
    test_set_points_and_get_points_roundtrip_without_emitting()
    test_set_points_falls_back_to_identity_below_minimum()
    test_set_active_channel_switches_which_channel_drags_affect()
    test_dragging_a_control_point_moves_it_and_emits()
    test_set_points_echo_during_a_drag_does_not_interrupt_it()
    test_dragging_cannot_cross_a_neighboring_point()
    test_dragging_is_constrained_to_the_domain_box_even_past_the_widget_edge()
    test_click_on_empty_area_adds_a_point_and_starts_dragging_it()
    test_clicking_near_an_existing_point_does_not_add_a_duplicate()
    test_right_click_removes_a_point_but_not_below_the_minimum()
    test_reset_active_channel_resets_only_that_channel_and_emits()
    print()
    print("All curve_editor_widget tests passed.")
