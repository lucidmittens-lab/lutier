"""Tests for airplay_output_sink.py -- run directly:
python3 test_airplay_output_sink.py

Requires a real macOS process with PyObjC + the AVFoundation/AVKit/
CoreMedia/Quartz framework bindings installed (see that module's own
docstring) -- this is NOT verifying actual wireless AirPlay casting to a
receiver (no receiver hardware available in this environment, see
VIEWER_CASTING_PLAN_2026-08-11.md), only that the real CVPixelBuffer ->
CMSampleBuffer -> AVSampleBufferDisplayLayer pipeline and the native
NSWindow lifecycle both work correctly end to end.

Creates real, briefly-visible native windows while running -- same
"exercise the real thing, not a mock" bar this app's own GPU tests hold
for OpenGL, just for Cocoa/AVFoundation instead.
"""
import sys

import numpy as np
from PySide6.QtWidgets import QApplication

import airplay_output_sink as aos
from video_output_sink import VideoFrame


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _test_frame(size=(180, 320), fill=None, seed=0):
    h, w = size
    if fill is not None:
        rgb = np.full((h, w, 3), fill, dtype=np.float32)
    else:
        rgb = np.random.default_rng(seed).uniform(0, 1, size=(h, w, 3)).astype(np.float32)
    return VideoFrame(rgb=rgb, timestamp_seconds=0.0)


def test_is_available_on_this_real_macos_pyobjc_environment():
    _app()
    sink = aos.AirPlayOutputSink()
    assert sink.is_available() is True
    assert sink.name == "Independent Viewer Window"
    print("AirPlayOutputSink: is_available() is True with PyObjC installed on macOS -- OK")


def test_push_frame_before_start_is_a_silent_noop():
    _app()
    sink = aos.AirPlayOutputSink()
    sink.push_frame(_test_frame())  # must not raise
    print("AirPlayOutputSink: push_frame before start() is a silent no-op -- OK")


def test_start_creates_a_real_window_and_is_idempotent():
    _app()
    sink = aos.AirPlayOutputSink()
    sink.start()
    try:
        assert sink._window is not None
        assert sink._display_layer is not None
        first_window = sink._window
        sink.start()  # second call must not create a second window
        assert sink._window is first_window
    finally:
        sink.stop()
    print("AirPlayOutputSink: start() creates a real NSWindow, a second start() is a no-op -- OK")


def test_push_frame_after_start_enqueues_without_error():
    _app()
    sink = aos.AirPlayOutputSink()
    sink.start()
    try:
        sink.push_frame(_test_frame(fill=(0.5, 0.2, 0.8)))
        assert sink._display_layer.error() is None
        assert sink._display_layer.status() == 1  # AVQueuedSampleBufferRenderingStatusRendering
    finally:
        sink.stop()
    print("AirPlayOutputSink: a real frame enqueues on AVSampleBufferDisplayLayer with no error -- OK")


def test_push_frame_twice_in_a_row_both_succeed():
    _app()
    sink = aos.AirPlayOutputSink()
    sink.start()
    try:
        sink.push_frame(_test_frame(fill=(1.0, 0.0, 0.0), seed=1))
        sink.push_frame(_test_frame(fill=(0.0, 1.0, 0.0), seed=2))
        assert sink._display_layer.error() is None
        assert sink._last_push_time is not None
    finally:
        sink.stop()
    print("AirPlayOutputSink: two consecutive frames both enqueue without error -- OK")


def test_push_frame_at_a_different_size_rebuilds_cleanly():
    _app()
    sink = aos.AirPlayOutputSink()
    sink.start()
    try:
        sink.push_frame(_test_frame(size=(120, 200)))
        sink.push_frame(_test_frame(size=(360, 640)))
        assert sink._display_layer.error() is None
        assert sink._last_frame_size == (640, 360)
    finally:
        sink.stop()
    print("AirPlayOutputSink: pushing frames at a changed size still enqueues cleanly -- OK")


def test_stop_closes_the_window_and_is_idempotent():
    _app()
    sink = aos.AirPlayOutputSink()
    sink.start()
    sink.stop()
    assert sink._window is None
    assert sink._display_layer is None
    sink.stop()  # must not raise
    print("AirPlayOutputSink: stop() closes the window, a second stop() is a no-op -- OK")


def test_push_frame_after_stop_is_a_silent_noop():
    _app()
    sink = aos.AirPlayOutputSink()
    sink.start()
    sink.stop()
    sink.push_frame(_test_frame())  # must not raise
    print("AirPlayOutputSink: push_frame after stop() is a silent no-op -- OK")


if __name__ == "__main__":
    test_is_available_on_this_real_macos_pyobjc_environment()
    test_push_frame_before_start_is_a_silent_noop()
    test_start_creates_a_real_window_and_is_idempotent()
    test_push_frame_after_start_enqueues_without_error()
    test_push_frame_twice_in_a_row_both_succeed()
    test_push_frame_at_a_different_size_rebuilds_cleanly()
    test_stop_closes_the_window_and_is_idempotent()
    test_push_frame_after_stop_is_a_silent_noop()
    print()
    print("All airplay_output_sink tests passed.")
