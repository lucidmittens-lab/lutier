"""Tests for wipe_compare_widget.py -- run directly:
python3 test_wipe_compare_widget.py

Focused on the zoom/pan addition (per Tom: scroll to zoom anchored on
the cursor, middle-mouse-drag to pan, middle-double-click to reset) --
the pre-existing wipe-divider drag/reset behavior is covered here too,
specifically to confirm the new middle-button gestures didn't disturb it
(different button, same widget).

Requires a real (if offscreen) QApplication -- same headless-Qt setup as
every other GUI-touching verification in this project (see RUNNING.md).
"""
import sys

import numpy as np
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication

from wipe_compare_widget import MAX_ZOOM, MIN_ZOOM, WipeCompareWidget, widget_pos_to_image_fraction


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _widget(image_size=(200, 100), widget_size=(400, 300)):
    """As of the Stage 5 GPU rewrite, set_after takes a raw (H,W,3)
    float32 array (domain [0,1]), not a QPixmap -- image_size is (w, h),
    matching the old QPixmap(*image_size) convention, so array shape is
    (h, w, 3), the numpy/image convention (reversed from image_size)."""
    _app()
    w = WipeCompareWidget()
    w.resize(*widget_size)
    img_w, img_h = image_size
    array = np.full((img_h, img_w, 3), (1.0, 0.0, 0.0), dtype=np.float32)  # solid red
    w.set_after(array)
    return w


def _wheel(target, pos, notches, modifiers=Qt.KeyboardModifier.NoModifier):
    event = QWheelEvent(
        QPointF(*pos), QPointF(*pos), QPoint(0, 0), QPoint(0, int(notches * 120)),
        Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(target, event)


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


def _double_click(target, pos, button=Qt.MouseButton.LeftButton):
    event = QMouseEvent(QEvent.Type.MouseButtonDblClick, QPointF(*pos), QPointF(*pos),
                         button, button, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target, event)


def test_default_zoom_matches_original_fit_behavior():
    w = _widget()
    rect = w._image_rect()
    # 200x100 image fit into 400x300 -> width-limited, scale 2.0 -> 400x200,
    # vertically centered (letterboxed top/bottom by 50px each side).
    assert abs(rect.width() - 400) < 1e-6
    assert abs(rect.height() - 200) < 1e-6
    assert abs(rect.top() - 50) < 1e-6
    print("WipeCompareWidget._image_rect: zoom=1.0/pan=0 exactly "
          "reproduces the original always-fit behavior -- OK")


def test_zoom_scales_the_image_rect_relative_to_fit():
    w = _widget()
    w._zoom = 2.0
    rect = w._image_rect()
    assert abs(rect.width() - 800) < 1e-6
    assert abs(rect.height() - 400) < 1e-6
    print("WipeCompareWidget._image_rect: zoom multiplies the base fit "
          "scale -- OK")


# --- widget_pos_to_image_fraction (segmentation-groundwork utility) -------

def test_widget_pos_to_image_fraction_maps_corners_and_center():
    w = _widget()  # image_rect = (left=0, top=50, width=400, height=200)
    rect = w._image_rect()
    assert widget_pos_to_image_fraction(QPointF(0, 50), rect) == (0.0, 0.0)
    assert widget_pos_to_image_fraction(QPointF(400, 250), rect) == (1.0, 1.0)
    cx, cy = widget_pos_to_image_fraction(QPointF(200, 150), rect)
    assert abs(cx - 0.5) < 1e-9 and abs(cy - 0.5) < 1e-9
    print("widget_pos_to_image_fraction: maps the image rect's own "
          "corners/center to (0,0)/(1,1)/(0.5,0.5) -- OK")


def test_widget_pos_to_image_fraction_returns_none_outside_the_image_rect():
    w = _widget()
    rect = w._image_rect()
    # y=10 is above top=50 -- inside the letterboxed margin, not the image.
    assert widget_pos_to_image_fraction(QPointF(200, 10), rect) is None
    print("widget_pos_to_image_fraction: a click in the letterboxed "
          "margin (outside the image itself) returns None -- OK")


def test_widget_pos_to_image_fraction_none_image_rect_returns_none():
    assert widget_pos_to_image_fraction(QPointF(10, 10), None) is None
    print("widget_pos_to_image_fraction: no image loaded (image_rect is "
          "None) returns None rather than raising -- OK")


def test_widget_pos_to_image_fraction_is_stable_under_zoom():
    """The SAME relative position on the image should map to the SAME
    fraction regardless of zoom level -- confirms this is a genuine
    inverse of _image_rect's own transform, not coincidentally correct
    only at zoom=1."""
    w = _widget()
    w._zoom = 2.5
    rect = w._image_rect()
    quarter_point = QPointF(rect.left() + rect.width() * 0.25, rect.top() + rect.height() * 0.75)
    frac = widget_pos_to_image_fraction(quarter_point, rect)
    assert abs(frac[0] - 0.25) < 1e-9 and abs(frac[1] - 0.75) < 1e-9
    print("widget_pos_to_image_fraction: stays correct at a non-default "
          "zoom level -- OK")


def test_canvas_aspect_ratio_letterboxes_a_square_canvas_into_a_wider_widget():
    """Widget is 400x300 (4:3, aspect 1.333). A square (1:1) canvas
    letterboxes top/bottom into a 300x300 box first (per _image_rect's
    own nested-fit docstring); the 200x100 (2:1) image then fits WITHIN
    that 300x300 box, not the raw widget -- pillarboxed inside it too."""
    w = _widget()
    w.set_canvas_aspect_ratio(1.0)
    rect = w._image_rect()
    assert abs(rect.width() - 300) < 1e-6
    assert abs(rect.height() - 150) < 1e-6
    assert abs(rect.left() - 50) < 1e-6
    assert abs(rect.top() - 75) < 1e-6
    print("WipeCompareWidget._image_rect: a square canvas letterboxes into "
          "a wider widget, and the image nests inside THAT box -- OK")


def test_canvas_aspect_ratio_pillarboxes_a_wider_canvas_into_the_widget():
    """A canvas wider than the widget itself (aspect 3.0 vs. the widget's
    own 1.333) letterboxes top/bottom instead; the image (aspect 2.0,
    narrower than the canvas box's 3.0) then pillarboxes left/right
    within THAT box -- confirms both letterbox directions nest
    correctly, not just the one the first test exercises."""
    w = _widget()
    w.set_canvas_aspect_ratio(3.0)
    rect = w._image_rect()
    assert abs(rect.width() - (800.0 / 3.0)) < 1e-3
    assert abs(rect.height() - (400.0 / 3.0)) < 1e-3
    assert abs(rect.top() - (250.0 / 3.0)) < 1e-3
    print("WipeCompareWidget._image_rect: a canvas wider than the widget "
          "letterboxes it, and the image pillarboxes inside THAT box -- OK")


def test_canvas_rect_matches_the_box_image_rect_nests_inside():
    """_canvas_rect() (fed to the GPU compositor as canvas_rect_qt, see
    gpu_lanczos_compositor.py) must be the SAME box _image_rect() nests
    the image inside -- if these ever drifted apart, the letterbox bars
    painted black by the compositor wouldn't line up with where the
    image actually sits."""
    w = _widget()
    w.set_canvas_aspect_ratio(1.0)
    canvas_rect = w._canvas_rect()
    # Widget is 400x300 -- a square canvas fits as a centered 300x300 box.
    assert abs(canvas_rect.width() - 300) < 1e-6
    assert abs(canvas_rect.height() - 300) < 1e-6
    assert abs(canvas_rect.left() - 50) < 1e-6
    assert abs(canvas_rect.top() - 0) < 1e-6
    image_rect = w._image_rect()
    assert canvas_rect.left() <= image_rect.left()
    assert canvas_rect.top() <= image_rect.top()
    assert canvas_rect.right() >= image_rect.right()
    assert canvas_rect.bottom() >= image_rect.bottom()
    print("WipeCompareWidget._canvas_rect: matches the box _image_rect nests "
          "the image inside -- OK")


def test_canvas_rect_is_none_when_no_canvas_aspect_ratio_is_set():
    w = _widget()
    assert w._canvas_rect() is None
    print("WipeCompareWidget._canvas_rect: None when no canvas aspect ratio is set -- OK")


def test_canvas_rect_scales_and_moves_together_with_the_image_on_zoom_and_pan():
    """Regression test for a real, confirmed bug (Tom, 2026-08-13): "when
    i move the image around or zoom in and out the black stays pinned to
    its location in the panel while the image moves around". The canvas
    box and the image inside it must scale/move as ONE rigid unit --
    their relative position/size to each other must stay constant across
    any zoom/pan."""
    w = _widget()
    w.set_canvas_aspect_ratio(1.0)
    canvas_before = w._canvas_rect()
    image_before = w._image_rect()
    offset_x_before = image_before.left() - canvas_before.left()
    offset_y_before = image_before.top() - canvas_before.top()
    width_ratio_before = image_before.width() / canvas_before.width()

    w._zoom = 2.0
    w._pan = QPointF(15.0, -8.0)
    canvas_after = w._canvas_rect()
    image_after = w._image_rect()

    # The canvas itself must have actually moved/grown (otherwise this
    # test would trivially pass by both staying pinned).
    assert abs(canvas_after.width() - canvas_before.width() * 2.0) < 1e-6
    assert canvas_after.left() != canvas_before.left()

    offset_x_after = image_after.left() - canvas_after.left()
    offset_y_after = image_after.top() - canvas_after.top()
    width_ratio_after = image_after.width() / canvas_after.width()
    # The image's offset from the canvas's own top-left scales WITH zoom
    # (2x zoom -> 2x offset in canvas-relative pixels), and its size
    # relative to the canvas stays exactly constant -- i.e. the image
    # never drifts relative to its own canvas frame.
    assert abs(offset_x_after - offset_x_before * 2.0) < 1e-6
    assert abs(offset_y_after - offset_y_before * 2.0) < 1e-6
    assert abs(width_ratio_after - width_ratio_before) < 1e-6
    print("WipeCompareWidget: the canvas box and the image inside it scale/move "
          "together as one rigid unit across zoom and pan -- OK")


def test_canvas_aspect_ratio_none_restores_original_fit_behavior():
    w = _widget()
    w.set_canvas_aspect_ratio(2.5)
    w.set_canvas_aspect_ratio(None)
    rect = w._image_rect()
    assert abs(rect.width() - 400) < 1e-6
    assert abs(rect.height() - 200) < 1e-6
    assert abs(rect.top() - 50) < 1e-6
    print("WipeCompareWidget.set_canvas_aspect_ratio(None): restores the "
          "original fit-to-widget behavior exactly -- OK")


def test_wheel_up_zooms_in_wheel_down_zooms_out():
    w = _widget()
    _wheel(w, (200, 150), notches=1)
    assert w._zoom > 1.0
    zoomed_in = w._zoom
    _wheel(w, (200, 150), notches=-1)
    assert w._zoom < zoomed_in
    print("WipeCompareWidget.wheelEvent: scrolling up zooms in, "
          "scrolling down zooms back out -- OK")


def test_wheel_zoom_clamps_to_min_and_max():
    w = _widget()
    for _ in range(200):
        _wheel(w, (200, 150), notches=-1)
    assert abs(w._zoom - MIN_ZOOM) < 1e-9
    for _ in range(400):
        _wheel(w, (200, 150), notches=1)
    assert abs(w._zoom - MAX_ZOOM) < 1e-9
    print("WipeCompareWidget.wheelEvent: zoom clamps to MIN_ZOOM/"
          "MAX_ZOOM rather than running away -- OK")


def test_zoom_getter_matches_internal_state():
    w = _widget()
    assert w.zoom() == 1.0
    _wheel(w, (200, 150), notches=2)
    assert w.zoom() == w._zoom
    assert w.zoom() > 1.0
    print("WipeCompareWidget.zoom(): public getter matches internal "
          "state -- OK")


def test_zoom_changed_emits_on_a_real_zoom_change():
    w = _widget()
    seen = []
    w.zoom_changed.connect(lambda: seen.append(w.zoom()))

    _wheel(w, (200, 150), notches=1)
    assert len(seen) == 1
    assert seen[0] == w.zoom()
    print("WipeCompareWidget.zoom_changed: fires once per real wheel-"
          "driven zoom change -- OK")


def test_zoom_changed_does_not_emit_once_already_clamped():
    w = _widget()
    for _ in range(200):
        _wheel(w, (200, 150), notches=-1)  # drive to MIN_ZOOM
    assert w.zoom() == MIN_ZOOM

    seen = []
    w.zoom_changed.connect(lambda: seen.append(True))
    _wheel(w, (200, 150), notches=-1)  # already at the floor -- no-op

    assert seen == []
    print("WipeCompareWidget.zoom_changed: does not fire when already "
          "clamped at MIN_ZOOM/MAX_ZOOM (SampleImagePanel shouldn't "
          "reload the preview for a scroll that changed nothing) -- OK")


def test_zoom_changed_emits_on_reset_view_only_if_zoom_was_not_already_fit():
    w = _widget()
    _wheel(w, (200, 150), notches=2)
    seen = []
    w.zoom_changed.connect(lambda: seen.append(True))

    w.reset_view()
    assert len(seen) == 1

    w.reset_view()  # already at fit -- must not re-fire
    assert len(seen) == 1
    print("WipeCompareWidget.reset_view: fires zoom_changed only when "
          "zoom actually changes -- OK")


def test_wheel_zoom_keeps_the_point_under_the_cursor_fixed():
    """The actual point of cursor-anchored zoom: whatever image-space
    point was under the cursor before zooming must still be under it
    after -- not the widget's center, unless the cursor happens to BE
    at the center."""
    w = _widget()
    cursor = (120, 90)  # off-center on purpose
    before = w._image_rect()
    rel_x = (cursor[0] - before.left()) / before.width()
    rel_y = (cursor[1] - before.top()) / before.height()

    _wheel(w, cursor, notches=3)

    after = w._image_rect()
    now_x = after.left() + rel_x * after.width()
    now_y = after.top() + rel_y * after.height()
    assert abs(now_x - cursor[0]) < 1e-6
    assert abs(now_y - cursor[1]) < 1e-6
    print("WipeCompareWidget.wheelEvent: the image point under the "
          "cursor stays under it as zoom changes -- OK")


def test_middle_drag_pans_the_image():
    w = _widget()
    w._zoom = 2.0  # panning is only meaningful once zoomed past fit
    before = w._image_rect()

    _press(w, (200, 150), button=Qt.MouseButton.MiddleButton)
    _move(w, (230, 170), buttons=Qt.MouseButton.MiddleButton)

    after = w._image_rect()
    assert abs((after.left() - before.left()) - 30) < 1e-6
    assert abs((after.top() - before.top()) - 20) < 1e-6
    print("WipeCompareWidget: middle-mouse-drag pans the image by "
          "exactly the mouse delta -- OK")


def test_middle_release_stops_panning():
    w = _widget()
    w._zoom = 2.0
    _press(w, (200, 150), button=Qt.MouseButton.MiddleButton)
    _move(w, (230, 170), buttons=Qt.MouseButton.MiddleButton)
    _release(w, (230, 170), button=Qt.MouseButton.MiddleButton)
    panned = w._image_rect()

    _move(w, (300, 300), buttons=Qt.MouseButton.NoButton)  # no button held anymore
    assert w._image_rect().left() == panned.left()
    assert w._image_rect().top() == panned.top()
    print("WipeCompareWidget: releasing the middle button stops "
          "panning -- OK")


def test_middle_double_click_resets_zoom_and_pan():
    w = _widget()
    w._zoom = 3.5
    w._pan = QPointF(40, -20)

    _double_click(w, (200, 150), button=Qt.MouseButton.MiddleButton)

    assert w._zoom == 1.0
    assert w._pan == QPointF(0.0, 0.0)
    print("WipeCompareWidget: middle-button double-click resets zoom/"
          "pan back to fit -- OK")


def test_left_double_click_still_resets_wipe_not_view():
    w = _widget()
    w._zoom = 2.0
    w._pan = QPointF(10, 10)
    w.set_wipe_fraction(0.1)

    _double_click(w, (200, 150), button=Qt.MouseButton.LeftButton)

    assert w.wipe_fraction() == 0.5
    assert w._zoom == 2.0  # untouched by the WIPE reset gesture
    assert w._pan == QPointF(10, 10)
    print("WipeCompareWidget: left-button double-click still only "
          "resets the wipe divider, leaves zoom/pan alone -- OK")


def test_left_drag_still_moves_the_wipe_divider_not_pan():
    w = _widget()
    before_zoom, before_pan = w._zoom, QPointF(w._pan)

    image_rect = w._image_rect()
    x = image_rect.left() + image_rect.width() * 0.9
    y = image_rect.center().y()
    _press(w, (x, y), button=Qt.MouseButton.LeftButton)
    _move(w, (x - 50, y), buttons=Qt.MouseButton.LeftButton)

    assert w.wipe_fraction() != 0.5
    assert w._zoom == before_zoom
    assert w._pan == before_pan
    print("WipeCompareWidget: left-button drag still moves the wipe "
          "divider, doesn't pan -- OK")


if __name__ == "__main__":
    test_default_zoom_matches_original_fit_behavior()
    test_zoom_scales_the_image_rect_relative_to_fit()
    test_widget_pos_to_image_fraction_maps_corners_and_center()
    test_widget_pos_to_image_fraction_returns_none_outside_the_image_rect()
    test_widget_pos_to_image_fraction_none_image_rect_returns_none()
    test_widget_pos_to_image_fraction_is_stable_under_zoom()
    test_canvas_aspect_ratio_letterboxes_a_square_canvas_into_a_wider_widget()
    test_canvas_aspect_ratio_pillarboxes_a_wider_canvas_into_the_widget()
    test_canvas_rect_matches_the_box_image_rect_nests_inside()
    test_canvas_rect_is_none_when_no_canvas_aspect_ratio_is_set()
    test_canvas_rect_scales_and_moves_together_with_the_image_on_zoom_and_pan()
    test_canvas_aspect_ratio_none_restores_original_fit_behavior()
    test_wheel_up_zooms_in_wheel_down_zooms_out()
    test_wheel_zoom_clamps_to_min_and_max()
    test_zoom_getter_matches_internal_state()
    test_zoom_changed_emits_on_a_real_zoom_change()
    test_zoom_changed_does_not_emit_once_already_clamped()
    test_zoom_changed_emits_on_reset_view_only_if_zoom_was_not_already_fit()
    test_wheel_zoom_keeps_the_point_under_the_cursor_fixed()
    test_middle_drag_pans_the_image()
    test_middle_release_stops_panning()
    test_middle_double_click_resets_zoom_and_pan()
    test_left_double_click_still_resets_wipe_not_view()
    test_left_drag_still_moves_the_wipe_divider_not_pan()
    print()
    print("All wipe_compare_widget tests passed.")
