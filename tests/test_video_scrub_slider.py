"""Tests for video_scrub_slider.py -- run directly:
QT_QPA_PLATFORM=offscreen python3 -m tests.test_video_scrub_slider

Offscreen-Qt-testable (no real GL context needed) -- render() to a
QPixmap forces a real paintEvent without an actual visible window, so
this exercises the custom paintEvent for real, not just the state
bookkeeping.
"""
import sys

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from video_scrub_slider import VideoScrubSlider


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _slider(maximum=99):
    _app()
    # Horizontal explicitly -- QSlider defaults to Vertical, but every
    # real construction site (sample_image_panel.py) passes Horizontal;
    # matching that here matters once a test cares about groove geometry
    # (see the click/drag tests below), not just "paintEvent doesn't
    # crash" -- a vertical slider's groove is a few px wide, not the
    # widget's full resized width.
    slider = VideoScrubSlider(Qt.Orientation.Horizontal)
    slider.setMinimum(0)
    slider.setMaximum(maximum)
    slider.resize(200, 24)
    return slider


def test_set_in_out_stores_the_values():
    slider = _slider()
    slider.set_in_out(10, 50)
    assert slider._in_point == 10
    assert slider._out_point == 50
    print("VideoScrubSlider.set_in_out: stores in_point/out_point -- OK")


def test_default_state_has_no_markers():
    slider = _slider()
    assert slider._in_point is None
    assert slider._out_point is None
    print("VideoScrubSlider: no markers by default (plain slider) -- OK")


def test_paint_event_does_not_raise_with_no_markers():
    slider = _slider()
    pixmap = QPixmap(slider.size())
    slider.render(pixmap)  # forces a real paintEvent
    print("VideoScrubSlider.paintEvent: doesn't raise with no markers "
          "set -- OK")


def test_paint_event_does_not_raise_with_markers_set():
    slider = _slider()
    slider.set_in_out(10, 50)
    pixmap = QPixmap(slider.size())
    slider.render(pixmap)
    print("VideoScrubSlider.paintEvent: doesn't raise with in/out "
          "markers set -- OK")


def test_paint_event_does_not_raise_with_out_point_none():
    """out_point=None (matching VideoPlaybackController's own "None
    means end of clip" convention) must draw at maximum(), not crash."""
    slider = _slider()
    slider.set_in_out(0, None)
    pixmap = QPixmap(slider.size())
    slider.render(pixmap)
    print("VideoScrubSlider.paintEvent: out_point=None draws at "
          "maximum() without raising -- OK")


def test_paint_event_does_not_raise_with_degenerate_range():
    """minimum() == maximum() (e.g. a single-frame clip) is a degenerate
    range -- must not divide by zero or otherwise crash."""
    slider = _slider(maximum=0)
    slider.set_in_out(0, 0)
    pixmap = QPixmap(slider.size())
    slider.render(pixmap)
    print("VideoScrubSlider.paintEvent: a degenerate (min==max) range "
          "doesn't raise -- OK")


def test_set_in_out_clears_markers():
    slider = _slider()
    slider.set_in_out(10, 50)
    slider.set_in_out(None, None)
    assert slider._in_point is None
    assert slider._out_point is None
    pixmap = QPixmap(slider.size())
    slider.render(pixmap)
    print("VideoScrubSlider.set_in_out: (None, None) clears markers "
          "back to a plain slider -- OK")


def test_clicking_far_from_the_playhead_jumps_there_directly():
    """Regression guard for direct feedback ("the scrub bar still only
    moves slightly when you click far from the playhead") -- default
    QSlider behavior on a groove click is a single page-step nudge
    TOWARD the click, not a jump TO it. A click near the RIGHT edge of
    the groove, starting from value 0, must land close to maximum(),
    not somewhere barely past 0."""
    slider = _slider(maximum=99)
    slider.setValue(0)
    click_x = int(slider.width() * 0.9)
    QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=QPoint(click_x, slider.height() // 2))
    assert slider.value() > 70, (
        f"a click 90% across the groove should jump near maximum(), landed at {slider.value()}")
    print("VideoScrubSlider: a click far from the playhead jumps "
          "straight to the clicked position, not a small step -- OK")


def test_clicking_the_left_edge_jumps_to_minimum():
    slider = _slider(maximum=99)
    slider.setValue(80)
    QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=QPoint(1, slider.height() // 2))
    assert slider.value() < 10, (
        f"a click at the groove's left edge should land near minimum(), got {slider.value()}")
    print("VideoScrubSlider: a click at the groove's left edge jumps "
          "straight to (near) minimum() -- OK")


def test_dragging_after_a_click_keeps_tracking_the_cursor():
    slider = _slider(maximum=99)
    slider.setValue(0)
    mid_x = slider.width() // 2
    QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=QPoint(1, slider.height() // 2))
    QTest.mouseMove(slider, QPoint(mid_x, slider.height() // 2))
    QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=QPoint(mid_x, slider.height() // 2))
    assert 30 < slider.value() < 70, (
        f"dragging to the groove's midpoint should land near its middle value, got {slider.value()}")
    print("VideoScrubSlider: a click-and-drag keeps tracking the cursor "
          "position, same as the initial jump -- OK")


if __name__ == "__main__":
    test_set_in_out_stores_the_values()
    test_default_state_has_no_markers()
    test_paint_event_does_not_raise_with_no_markers()
    test_paint_event_does_not_raise_with_markers_set()
    test_paint_event_does_not_raise_with_out_point_none()
    test_paint_event_does_not_raise_with_degenerate_range()
    test_set_in_out_clears_markers()
    test_clicking_far_from_the_playhead_jumps_there_directly()
    test_clicking_the_left_edge_jumps_to_minimum()
    test_dragging_after_a_click_keeps_tracking_the_cursor()
    print()
    print("All video_scrub_slider tests passed.")
