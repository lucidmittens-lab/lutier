"""Tests for video_scrub_slider.py -- run directly:
QT_QPA_PLATFORM=offscreen python3 test_video_scrub_slider.py

Offscreen-Qt-testable (no real GL context needed) -- render() to a
QPixmap forces a real paintEvent without an actual visible window, so
this exercises the custom paintEvent for real, not just the state
bookkeeping.
"""
import sys

from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from video_scrub_slider import VideoScrubSlider


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _slider(maximum=99):
    _app()
    slider = VideoScrubSlider()
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


if __name__ == "__main__":
    test_set_in_out_stores_the_values()
    test_default_state_has_no_markers()
    test_paint_event_does_not_raise_with_no_markers()
    test_paint_event_does_not_raise_with_markers_set()
    test_paint_event_does_not_raise_with_out_point_none()
    test_paint_event_does_not_raise_with_degenerate_range()
    test_set_in_out_clears_markers()
    print()
    print("All video_scrub_slider tests passed.")
