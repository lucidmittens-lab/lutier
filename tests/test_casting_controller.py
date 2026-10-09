"""Tests for casting_controller.py -- run directly:
python3 -m tests.test_casting_controller

Uses a small in-process fake video_output_sink.VideoOutputSink (not
airplay_output_sink.AirPlayOutputSink) so this file tests the
orchestration logic -- when to push, the throttle, the multi-sink
fan-out -- independently of any real Cocoa/AVFoundation backend, same
"test the controller, not the hardware" split test_airplay_output_sink.py's
own docstring describes for the reverse half. `frame_getter` is a plain
fake callable here too, standing in for wipe_compare_widget.
WipeCompareWidget.read_back_after_frame (see casting_controller.py's own
docstring on the real "fork 3 ways from one shared source" design).

Requires a real (if offscreen) QApplication for the QTimer-based
trailing push -- see RUNNING.md.
"""
import sys
import time

import numpy as np
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from casting_controller import CastingController, _THROTTLE_SECONDS
from video_output_sink import VideoOutputSink


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


class _FakeSink(VideoOutputSink):
    def __init__(self, label="Fake"):
        self._label = label
        self.started = False
        self.stopped = False
        self.pushed_frames = []

    @property
    def name(self):
        return self._label

    def is_available(self):
        return True

    def start(self):
        self.started = True

    def push_frame(self, frame):
        self.pushed_frames.append(frame)

    def stop(self):
        self.stopped = True


def _controller_with_frame(frame=None):
    if frame is None:
        frame = np.random.default_rng(0).uniform(0, 1, size=(4, 6, 3)).astype(np.float32)
    box = {"frame": frame}
    controller = CastingController(lambda: box["frame"])
    return controller, box


def test_is_casting_false_before_any_start():
    _app()
    controller, _box = _controller_with_frame()
    assert controller.is_casting is False
    print("CastingController: is_casting is False before start_sink -- OK")


def test_start_sink_starts_it_and_pushes_an_initial_frame():
    _app()
    controller, box = _controller_with_frame()
    sink = _FakeSink()
    controller.start_sink(sink)
    try:
        assert sink.started is True
        assert controller.is_casting is True
        assert len(sink.pushed_frames) == 1, "start_sink should push one frame immediately"
        assert np.array_equal(sink.pushed_frames[0].rgb, box["frame"])
    finally:
        controller.stop_all()
    print("CastingController.start_sink: starts the sink and pushes the current shared frame immediately -- OK")


def test_starting_the_same_sink_twice_is_a_noop():
    _app()
    controller, _box = _controller_with_frame()
    sink = _FakeSink()
    controller.start_sink(sink)
    controller.start_sink(sink)
    try:
        assert len(controller._sinks) == 1
        assert len(sink.pushed_frames) == 1, "the second start_sink call must not push a duplicate initial frame"
    finally:
        controller.stop_all()
    print("CastingController.start_sink: starting the same sink instance twice is a no-op -- OK")


def test_stop_sink_stops_only_that_one_leaving_others_active():
    """Directly covers the "fork 3 ways" design -- two sinks active at
    once (e.g. Independent Viewer + a future DeckLink sink), stopping
    one must not disturb the other."""
    _app()
    controller, _box = _controller_with_frame()
    sink_a = _FakeSink("A")
    sink_b = _FakeSink("B")
    controller.start_sink(sink_a)
    controller.start_sink(sink_b)
    try:
        assert controller.is_casting is True
        controller.stop_sink(sink_a)
        assert sink_a.stopped is True
        assert sink_b.stopped is False
        assert controller.is_casting is True, "sink_b is still active"
    finally:
        controller.stop_all()
    print("CastingController.stop_sink: stops only the specified sink, others stay active -- OK")


def test_stop_all_stops_every_active_sink():
    _app()
    controller, _box = _controller_with_frame()
    sink_a = _FakeSink("A")
    sink_b = _FakeSink("B")
    controller.start_sink(sink_a)
    controller.start_sink(sink_b)
    controller.stop_all()
    assert sink_a.stopped is True
    assert sink_b.stopped is True
    assert controller.is_casting is False
    print("CastingController.stop_all: stops every active sink -- OK")


def test_on_stack_or_image_changed_is_a_noop_when_not_casting():
    _app()
    controller, _box = _controller_with_frame()
    controller.on_stack_or_image_changed()  # must not raise, must not start any timer
    assert not controller._trailing_timer.isActive()
    print("CastingController.on_stack_or_image_changed: a no-op when no sink is active -- OK")


def test_on_stack_or_image_changed_throttles_within_the_window_then_pushes_the_new_shared_frame_to_every_sink():
    """Covers the TRAILING edge -- a change arriving too soon after the
    last real push must not push synchronously, but must still land,
    exactly once, once the throttle window closes."""
    _app()
    controller, box = _controller_with_frame()
    sink_a = _FakeSink("A")
    sink_b = _FakeSink("B")
    controller.start_sink(sink_a)
    controller.start_sink(sink_b)
    try:
        assert len(sink_a.pushed_frames) == 1 and len(sink_b.pushed_frames) == 1  # the immediate initial pushes

        new_frame = np.random.default_rng(1).uniform(0, 1, size=(4, 6, 3)).astype(np.float32)
        box["frame"] = new_frame
        controller.on_stack_or_image_changed()
        assert len(sink_a.pushed_frames) == 1, "must not push synchronously -- still inside the throttle window from start_sink's own initial push"
        assert controller._trailing_timer.isActive()

        QTest.qWait(int(_THROTTLE_SECONDS * 1000) + 150)

        assert len(sink_a.pushed_frames) == 2 and len(sink_b.pushed_frames) == 2
        assert np.array_equal(sink_a.pushed_frames[-1].rgb, new_frame)
        assert np.array_equal(sink_b.pushed_frames[-1].rgb, new_frame)
    finally:
        controller.stop_all()
    print("CastingController.on_stack_or_image_changed: throttles within the window, then pushes the new shared frame to EVERY active sink -- OK")


def test_on_stack_or_image_changed_pushes_immediately_once_outside_the_throttle_window():
    """Covers the LEADING edge -- this is the actual fix for Tom's
    2026-08-12 report ("only updates once i stop changing a parameter"):
    once enough real time has passed since the last push, the next
    change must push right away, not wait for a timer."""
    _app()
    controller, box = _controller_with_frame()
    sink = _FakeSink()
    controller.start_sink(sink)
    try:
        assert len(sink.pushed_frames) == 1
        time.sleep(_THROTTLE_SECONDS + 0.05)

        new_frame = np.random.default_rng(2).uniform(0, 1, size=(4, 6, 3)).astype(np.float32)
        box["frame"] = new_frame
        controller.on_stack_or_image_changed()

        assert len(sink.pushed_frames) == 2, "outside the throttle window, the push must happen synchronously"
        assert np.array_equal(sink.pushed_frames[-1].rgb, new_frame)
        assert not controller._trailing_timer.isActive(), "no trailing push should be left pending after a leading-edge push"
    finally:
        controller.stop_all()
    print("CastingController.on_stack_or_image_changed: pushes immediately once outside the throttle window -- OK")


def test_on_stack_or_image_changed_during_a_continuous_burst_updates_periodically_not_only_at_the_end():
    """Simulates a slider drag: on_stack_or_image_changed() fires every
    ~20ms (far faster than _THROTTLE_SECONDS) for several throttle
    windows' worth of time. Must see more than one push land DURING the
    burst, not just one push after it ends -- this is the exact
    "way faster to update but seems to only happen once i stop changing
    a parameter" symptom this fix addresses."""
    _app()
    controller, box = _controller_with_frame()
    sink = _FakeSink()
    controller.start_sink(sink)
    try:
        assert len(sink.pushed_frames) == 1
        start = time.monotonic()
        tick = 0
        while time.monotonic() - start < (_THROTTLE_SECONDS * 3.5):
            tick += 1
            box["frame"] = np.random.default_rng(100 + tick).uniform(0, 1, size=(4, 6, 3)).astype(np.float32)
            controller.on_stack_or_image_changed()
            QTest.qWait(20)
        pushes_during_burst = len(sink.pushed_frames)
        assert pushes_during_burst >= 3, (
            f"expected multiple pushes DURING a ~{_THROTTLE_SECONDS * 3.5:.2f}s burst, "
            f"got {pushes_during_burst} total (including the initial push) -- looks debounced, not throttled"
        )
        QTest.qWait(int(_THROTTLE_SECONDS * 1000) + 150)
        assert np.array_equal(sink.pushed_frames[-1].rgb, box["frame"]), "the final trailing push must reflect the very last edit"
    finally:
        controller.stop_all()
    print("CastingController.on_stack_or_image_changed: a continuous burst updates periodically DURING the burst, not just once at the end -- OK")


def test_push_skips_every_sink_when_frame_getter_returns_none():
    _app()
    controller = CastingController(lambda: None)
    sink = _FakeSink()
    controller.start_sink(sink)
    try:
        assert sink.pushed_frames == [], "frame_getter returning None (nothing rendered yet) means nothing to push"
    finally:
        controller.stop_all()
    print("CastingController: frame_getter returning None means no frame is pushed to any sink -- OK")


if __name__ == "__main__":
    test_is_casting_false_before_any_start()
    test_start_sink_starts_it_and_pushes_an_initial_frame()
    test_starting_the_same_sink_twice_is_a_noop()
    test_stop_sink_stops_only_that_one_leaving_others_active()
    test_stop_all_stops_every_active_sink()
    test_on_stack_or_image_changed_is_a_noop_when_not_casting()
    test_on_stack_or_image_changed_throttles_within_the_window_then_pushes_the_new_shared_frame_to_every_sink()
    test_on_stack_or_image_changed_pushes_immediately_once_outside_the_throttle_window()
    test_on_stack_or_image_changed_during_a_continuous_burst_updates_periodically_not_only_at_the_end()
    test_push_skips_every_sink_when_frame_getter_returns_none()
    print()
    print("All casting_controller tests passed.")
