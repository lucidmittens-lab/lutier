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

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QPixmap, QWheelEvent
from PySide6.QtWidgets import QApplication

from wipe_compare_widget import MAX_ZOOM, MIN_ZOOM, WipeCompareWidget


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _widget(image_size=(200, 100), widget_size=(400, 300)):
    _app()
    w = WipeCompareWidget()
    w.resize(*widget_size)
    pixmap = QPixmap(*image_size)
    pixmap.fill(Qt.GlobalColor.red)
    w.set_after(pixmap)
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
