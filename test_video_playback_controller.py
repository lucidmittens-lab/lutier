"""Tests for video_playback_controller.py -- run directly:
python3 test_video_playback_controller.py

Fully offscreen-Qt-testable (SampleImagePanel/WipeCompareWidget don't
need a real GL context for these -- only real GL rendering does, see
conftest.py's collect_ignore). Calls _tick() directly rather than
running the real QTimer/event loop, for deterministic, fast tests --
play()/pause() themselves ARE exercised for real, only the timer's own
periodic firing is bypassed.
"""
import os
import sys
import tempfile

import av
import numpy as np
from PySide6.QtWidgets import QApplication

from sample_image_panel import SampleImagePanel
from video_playback_controller import VideoPlaybackController, PLAYBACK_STATUS_TEXT

FRAME_COUNT = 10
FRAME_RATE = 24
WIDTH, HEIGHT = 32, 24


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _make_test_video(frame_count=FRAME_COUNT, frame_rate=FRAME_RATE, size=(WIDTH, HEIGHT)):
    w, h = size
    path = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
    container = av.open(path, mode="w")
    stream = container.add_stream("libx264", rate=frame_rate)
    stream.width = w
    stream.height = h
    stream.pix_fmt = "yuv420p"
    for i in range(frame_count):
        color = (min(255, i * 25), max(0, 255 - i * 25), 128)
        frame = av.VideoFrame.from_ndarray(
            np.full((h, w, 3), color, dtype=np.uint8), format="rgb24")
        frame.pts = i
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()
    return path


def _panel_with_video(frame_count=FRAME_COUNT, frame_rate=FRAME_RATE):
    _app()
    panel = SampleImagePanel()
    path = _make_test_video(frame_count=frame_count, frame_rate=frame_rate)
    assert panel.load_video(path)
    return panel


def test_play_starts_the_timer_and_sets_playback_status_text():
    panel = _panel_with_video()
    controller = VideoPlaybackController(panel)
    assert not controller.is_playing
    controller.play()
    assert controller.is_playing
    assert controller._timer.isActive()
    assert panel.status_label.text() == PLAYBACK_STATUS_TEXT
    controller.pause()
    print("VideoPlaybackController.play: starts the timer and shows the "
          "ungraded-playback status text -- OK")


def test_play_is_a_noop_with_no_video_loaded():
    _app()
    panel = SampleImagePanel()  # no video loaded
    controller = VideoPlaybackController(panel)
    controller.play()
    assert not controller.is_playing
    assert not controller._timer.isActive()
    print("VideoPlaybackController.play: no-op when the panel has no "
          "video open -- OK")


def test_tick_decodes_frames_in_order_and_pushes_to_the_viewer():
    panel = _panel_with_video()
    controller = VideoPlaybackController(panel)
    controller.play()
    for expected_index in (1, 2, 3):
        controller._tick()
        assert controller._frame_index == expected_index
        pushed = panel.compare._after_state.array
        assert pushed is not None
        assert pushed.shape == (HEIGHT, WIDTH, 3)
    controller.pause()
    print("VideoPlaybackController._tick: decodes frames in order and "
          "pushes each one straight to the Viewer -- OK")


def test_tick_pushes_a_resolution_capped_proxy_not_full_resolution():
    """Regression coverage for the real perf mitigation this module's own
    docstring documents -- every tick MUST push a capped proxy, not a
    fresh full-native-resolution array (see DECODE_PROXY_MAX_DIMENSION)."""
    panel = _panel_with_video(frame_count=8)
    # Force a source resolution bigger than the cap to actually exercise it.
    import video_playback_controller as vpc_module
    original_cap = vpc_module.DECODE_PROXY_MAX_DIMENSION
    vpc_module.DECODE_PROXY_MAX_DIMENSION = 16
    try:
        controller = VideoPlaybackController(panel)
        controller.play()
        controller._tick()
        pushed = panel.compare._after_state.array
        assert max(pushed.shape[:2]) <= 16, (
            f"expected a proxy capped at 16px, got shape {pushed.shape}")
        controller.pause()
    finally:
        vpc_module.DECODE_PROXY_MAX_DIMENSION = original_cap
    print("VideoPlaybackController._tick: pushes a resolution-capped "
          "proxy while playing, not full native resolution -- OK")


def test_pause_reparks_at_full_resolution_through_the_grading_pipeline():
    panel = _panel_with_video()
    controller = VideoPlaybackController(panel)
    controller.play()
    controller._tick()
    controller._tick()
    controller._tick()  # now on frame 3, via the low-res playback path
    assert controller._frame_index == 3

    controller.pause()
    assert not controller.is_playing
    assert panel._video_frame_index == 3, "pause must re-park the PANEL's own frame index too"
    assert panel._image_array.shape[:2] == (HEIGHT, WIDTH), (
        "pause must re-decode at FULL native resolution, not leave the low-res proxy")
    assert panel.status_label.text() == "", "pause must clear the playback status text"
    print("VideoPlaybackController.pause: re-parks the Viewer at full "
          "resolution through the normal grading pipeline -- OK")


def test_playback_stopped_signal_fires_on_pause():
    panel = _panel_with_video()
    controller = VideoPlaybackController(panel)
    received = []
    controller.playback_stopped.connect(lambda: received.append(True))
    controller.play()
    controller.pause()
    assert received == [True]
    print("VideoPlaybackController: playback_stopped fires on an "
          "explicit pause -- OK")


def test_reaching_end_of_stream_stops_playback_and_reparks_on_the_last_frame():
    panel = _panel_with_video(frame_count=4)
    controller = VideoPlaybackController(panel)
    stopped = []
    controller.playback_stopped.connect(lambda: stopped.append(True))
    controller.play()
    # 4 frames total; frame 0 is already parked (the initial load), so
    # ticking 3 more times reaches frame 3 (the last), and a 4th tick
    # must find nothing left to decode and stop.
    for _ in range(10):
        if not controller.is_playing:
            break
        controller._tick()
    assert not controller.is_playing
    assert stopped == [True]
    assert panel._video_frame_index == 3, "must re-park on the LAST real frame, not one past it"
    print("VideoPlaybackController: reaching end of stream stops playback "
          "and re-parks on the last real frame -- OK")


def test_stop_halts_playback_without_reparking():
    panel = _panel_with_video()
    controller = VideoPlaybackController(panel)
    controller.play()
    controller._tick()
    controller._tick()  # now on frame 2
    stopped = []
    controller.playback_stopped.connect(lambda: stopped.append(True))

    controller.stop()
    assert not controller.is_playing
    assert stopped == [True]
    # Unlike pause(), stop() must NOT touch the panel's own frame index/
    # working array -- it's meant for teardown, where re-parking would
    # be wasted work about to be discarded anyway.
    assert panel._video_frame_index == 0, "stop() must not re-park -- panel's own index stays at its last PARKED value"
    print("VideoPlaybackController.stop: halts playback immediately "
          "without re-parking the panel -- OK")


def test_toggle_flips_between_play_and_pause():
    panel = _panel_with_video()
    controller = VideoPlaybackController(panel)
    controller.toggle()
    assert controller.is_playing
    controller.toggle()
    assert not controller.is_playing
    print("VideoPlaybackController.toggle: flips play/pause state -- OK")


def test_frame_changed_signal_reports_each_ticked_frame_index():
    panel = _panel_with_video()
    controller = VideoPlaybackController(panel)
    received = []
    controller.frame_changed.connect(received.append)
    controller.play()
    controller._tick()
    controller._tick()
    controller.pause()
    assert received == [1, 2]
    print("VideoPlaybackController: frame_changed reports each ticked "
          "frame index in order -- OK")


def test_play_resumes_from_wherever_parked_frame_stepping_last_left_off():
    panel = _panel_with_video(frame_count=10)
    panel.seek_video_frame(4)  # scrub to frame 4 while paused
    controller = VideoPlaybackController(panel)
    controller.play()
    assert controller._frame_index == 4
    controller._tick()
    assert controller._frame_index == 5
    controller.pause()
    print("VideoPlaybackController.play: resumes from wherever parked-"
          "frame scrubbing last left the panel -- OK")


if __name__ == "__main__":
    test_play_starts_the_timer_and_sets_playback_status_text()
    test_play_is_a_noop_with_no_video_loaded()
    test_tick_decodes_frames_in_order_and_pushes_to_the_viewer()
    test_tick_pushes_a_resolution_capped_proxy_not_full_resolution()
    test_pause_reparks_at_full_resolution_through_the_grading_pipeline()
    test_playback_stopped_signal_fires_on_pause()
    test_stop_halts_playback_without_reparking()
    test_reaching_end_of_stream_stops_playback_and_reparks_on_the_last_frame()
    test_toggle_flips_between_play_and_pause()
    test_frame_changed_signal_reports_each_ticked_frame_index()
    test_play_resumes_from_wherever_parked_frame_stepping_last_left_off()
    print()
    print("All video_playback_controller tests passed.")
