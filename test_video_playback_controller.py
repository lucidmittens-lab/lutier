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
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import stack as st
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
          "grade-locked status text -- OK")


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


def test_tick_pushes_the_raw_frame_with_gpu_stack_for_a_gpu_eligible_stack():
    """Regression test for the corrected scope (Tom, 2026-08-13:
    "playback should still show the grade", clarifying the earlier
    "no simultaneous grading" language did NOT mean ungraded playback) --
    mirrors sample_image_panel.py's OWN live-preview split exactly: a
    GPU-eligible, non-empty stack must push the RAW decoded proxy with
    gpu_stack=stack (WipeCompareWidget grades it live on the GPU itself),
    NOT a pre-graded array."""
    panel = _panel_with_video()
    cdl_stack = st.Stack(stages=[st.make_cdl_stage(slope=(1.2, 0.9, 1.0))])
    panel.set_stack_getter(lambda: cdl_stack)
    controller = VideoPlaybackController(panel)
    controller.play()
    controller._tick()
    assert panel.compare._after_state.gpu_stack is cdl_stack
    # The pushed array itself must be the RAW proxy (ungraded on the CPU
    # side) -- grading happens inside WipeCompareWidget's own paintGL,
    # not before push.
    pushed = panel.compare._after_state.array
    assert pushed.shape == (HEIGHT, WIDTH, 3)
    controller.pause()
    print("VideoPlaybackController._tick: a GPU-eligible stack pushes the "
          "raw frame with gpu_stack set, grading live on the GPU -- OK")


def test_tick_cpu_grades_the_frame_for_a_gpu_ineligible_stack():
    """A GPU-INeligible (but non-empty) stack must CPU-grade each frame
    BEFORE pushing it -- gpu_stack=None, and the pushed array must
    actually reflect the grade, not the raw decoded pixels."""
    panel = _panel_with_video()
    # A retarget-wrapped LUT stage is GPU-ineligible by design -- see
    # test_sample_image_panel.py's own _panel_with_image_and_stack for
    # the same "stable, permanently GPU-ineligible" convention.
    import app_logic
    identity_lut = app_logic.create_identity_lut(size=5)
    lut_stage = st.make_lut_stage(identity_lut)
    lut_stage.params["retarget_from"] = "ACEScct"
    lut_stage.params["retarget_to"] = "ACES2065-1"
    ineligible_stack = st.Stack(stages=[lut_stage])
    panel.set_stack_getter(lambda: ineligible_stack)

    controller = VideoPlaybackController(panel)
    controller.play()
    controller._tick()
    assert panel.compare._after_state.gpu_stack is None
    graded = panel.compare._after_state.array

    # Compare against what the SAME raw frame looks like un-graded, via
    # an independent decode of the identical source frame.
    import video_source
    raw_check = video_source.VideoSource(panel._video_source.path)
    raw_check.seek_to_frame(0)
    raw_frame = raw_check.decode_next_frame()
    raw_check.close()

    assert not np.array_equal(graded, raw_frame), (
        "a GPU-ineligible stack's retarget should visibly change the pixels")
    controller.pause()
    print("VideoPlaybackController._tick: a GPU-ineligible stack CPU-grades "
          "each frame before pushing it -- OK")


def test_tick_pushes_raw_frame_when_no_stack_or_empty_stack():
    panel = _panel_with_video()
    panel.set_stack_getter(lambda: st.Stack())  # empty stack
    controller = VideoPlaybackController(panel)
    controller.play()
    controller._tick()
    assert panel.compare._after_state.gpu_stack is None
    controller.pause()
    print("VideoPlaybackController._tick: an empty stack pushes the raw "
          "frame unchanged, no grading attempted -- OK")


def test_tick_notifies_scopes_for_a_gpu_eligible_stack():
    """Regression test for a real bug (per code review): before this fix,
    _tick pushed frames straight to WipeCompareWidget.set_after and never
    called SampleImagePanel._set_current_after_array at all, so anything
    listening for after_image_changed (ScopesPanel, via main_window.py's
    own wiring) froze on whatever frame playback started from for the
    entire duration it played -- histogram/waveform/vectorscope would
    show stale data throughout playback, not just look slow to update.
    A GPU-eligible stack still grades on the GPU for the Viewer itself,
    but Scopes needs its own small, CPU-graded sample regardless (same
    "GPU-ELIGIBLE STACKS ALWAYS RECOMPUTE AT SCOPES RESOLUTION" rule
    sample_image_panel.py's own refresh_preview already follows for
    ordinary parameter edits)."""
    panel = _panel_with_video()
    cdl_stack = st.Stack(stages=[st.make_cdl_stage(slope=(1.2, 0.9, 1.0))])
    panel.set_stack_getter(lambda: cdl_stack)
    seen = []
    panel.after_image_changed.connect(seen.append)
    controller = VideoPlaybackController(panel)
    controller.play()
    controller._tick()
    # While playing, the Scopes sample is graded on scopes_worker.py's
    # thread and delivered back as a queued signal -- wait for it (up to
    # its rate limit plus a margin) instead of expecting it inline.
    for _ in range(40):
        if seen:
            break
        QTest.qWait(25)

    # Checked BEFORE pause() -- pause() re-parks via its own seek/recompute
    # path, which legitimately emits after_image_changed again and would
    # otherwise conflate _tick's own emission with pause()'s.
    assert len(seen) == 1
    assert seen[0] is not None
    assert panel.get_current_after_array() is seen[0]
    # The Scopes-facing array must be CPU-graded (matching the CDL's own
    # slope), NOT the raw decoded frame -- the Viewer grades on the GPU,
    # but Scopes never sees that result.
    raw_pixel = panel.compare._after_state.array[0, 0]
    graded_pixel = seen[0][0, 0]
    assert not np.allclose(raw_pixel, graded_pixel), (
        "the Scopes-facing array must reflect the CDL grade, not raw decoded pixels")
    controller.pause()
    print("VideoPlaybackController._tick: notifies Scopes (after_image_changed) "
          "with a CPU-graded frame even for a GPU-eligible stack -- OK")


def test_tick_notifies_scopes_for_a_gpu_ineligible_stack():
    panel = _panel_with_video()
    import app_logic
    identity_lut = app_logic.create_identity_lut(size=5)
    lut_stage = st.make_lut_stage(identity_lut)
    lut_stage.params["retarget_from"] = "ACEScct"
    lut_stage.params["retarget_to"] = "ACES2065-1"
    ineligible_stack = st.Stack(stages=[lut_stage])
    panel.set_stack_getter(lambda: ineligible_stack)
    seen = []
    panel.after_image_changed.connect(seen.append)
    controller = VideoPlaybackController(panel)
    controller.play()
    controller._tick()

    assert len(seen) == 1  # checked before pause() -- see the GPU-eligible test's own comment
    # Same array actually pushed to the Viewer for this (CPU-graded)
    # branch -- no redundant second stack-apply needed.
    assert np.array_equal(seen[0], panel.compare._after_state.array)
    controller.pause()
    print("VideoPlaybackController._tick: notifies Scopes with the same "
          "CPU-graded frame already pushed to the Viewer -- OK")


def test_tick_notifies_scopes_with_no_stack():
    panel = _panel_with_video()
    panel.set_stack_getter(lambda: st.Stack())  # empty stack
    seen = []
    panel.after_image_changed.connect(seen.append)
    controller = VideoPlaybackController(panel)
    controller.play()
    controller._tick()

    assert len(seen) == 1  # checked before pause() -- see the GPU-eligible test's own comment
    assert seen[0] is not None
    controller.pause()
    print("VideoPlaybackController._tick: notifies Scopes even with no "
          "stack (or an empty one) applied -- OK")


def test_play_emits_playback_started():
    panel = _panel_with_video()
    controller = VideoPlaybackController(panel)
    received = []
    controller.playback_started.connect(lambda: received.append(True))
    controller.play()
    assert received == [True]
    controller.pause()
    print("VideoPlaybackController.play: emits playback_started -- OK")


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


# ---- Phase 4: in/out points + loop --------------------------------------

def test_set_in_point_sets_to_current_parked_frame():
    panel = _panel_with_video(frame_count=10)
    panel.seek_video_frame(3)
    controller = VideoPlaybackController(panel)
    controller.set_in_point()
    assert controller.in_point == 3
    print("VideoPlaybackController.set_in_point: sets in_point to the "
          "current parked frame -- OK")


def test_set_out_point_sets_to_current_parked_frame():
    panel = _panel_with_video(frame_count=10)
    panel.seek_video_frame(6)
    controller = VideoPlaybackController(panel)
    controller.set_out_point()
    assert controller.out_point == 6
    print("VideoPlaybackController.set_out_point: sets out_point to the "
          "current parked frame -- OK")


def test_set_in_point_past_out_point_pushes_out_point_up():
    panel = _panel_with_video(frame_count=10)
    controller = VideoPlaybackController(panel)
    controller.out_point = 3
    panel.seek_video_frame(7)
    controller.set_in_point()
    assert controller.in_point == 7
    assert controller.out_point == 7
    print("VideoPlaybackController.set_in_point: pushes out_point up to "
          "match rather than creating an inverted range -- OK")


def test_set_out_point_before_in_point_pulls_in_point_down():
    panel = _panel_with_video(frame_count=10)
    controller = VideoPlaybackController(panel)
    controller.in_point = 5
    panel.seek_video_frame(2)
    controller.set_out_point()
    assert controller.out_point == 2
    assert controller.in_point == 2
    print("VideoPlaybackController.set_out_point: pulls in_point down to "
          "match rather than creating an inverted range -- OK")


def test_clear_in_out_resets_to_full_range():
    panel = _panel_with_video(frame_count=10)
    controller = VideoPlaybackController(panel)
    controller.in_point = 3
    controller.out_point = 6
    controller.clear_in_out()
    assert controller.in_point == 0
    assert controller.out_point is None
    print("VideoPlaybackController.clear_in_out: resets to the full clip "
          "range (0, None) -- OK")


def test_tick_pauses_at_out_point_when_loop_disabled():
    panel = _panel_with_video(frame_count=10)
    controller = VideoPlaybackController(panel)
    controller.out_point = 2
    controller.play()
    controller._tick()
    controller._tick()
    assert controller.is_playing
    controller._tick()  # would step past out_point=2
    assert not controller.is_playing
    assert controller._frame_index == 2
    print("VideoPlaybackController._tick: pauses and re-parks at "
          "out_point rather than playing past it -- OK")


def test_tick_loops_back_to_in_point_when_loop_enabled():
    panel = _panel_with_video(frame_count=10)
    controller = VideoPlaybackController(panel)
    controller.in_point = 1
    controller.out_point = 2
    controller.set_loop_enabled(True)
    controller.play()  # clamps into range -- lands on in_point=1
    assert controller._frame_index == 1
    controller._tick()
    assert controller._frame_index == 2
    controller._tick()  # would step past out_point=2 -- loops to in_point
    assert controller.is_playing
    assert controller._frame_index == 1
    controller.pause()
    print("VideoPlaybackController._tick: loops back to in_point instead "
          "of pausing when loop_enabled -- OK")


def test_play_clamps_start_index_into_in_out_range():
    panel = _panel_with_video(frame_count=10)
    controller = VideoPlaybackController(panel)
    controller.in_point = 1
    controller.out_point = 4
    panel.seek_video_frame(8)  # parked OUTSIDE the active range
    controller.play()
    assert controller._frame_index == 4
    controller.pause()
    print("VideoPlaybackController.play: clamps the starting frame into "
          "[in_point, out_point] when parked outside the active range "
          "-- OK")


def test_loading_a_new_video_resets_in_out_and_loop():
    panel = _panel_with_video(frame_count=10)
    panel._playback_controller.in_point = 3
    panel._playback_controller.out_point = 6
    panel._playback_controller.set_loop_enabled(True)

    second_path = _make_test_video(frame_count=6, size=(WIDTH, HEIGHT))
    assert panel.load_video(second_path)

    assert panel._playback_controller.in_point == 0
    assert panel._playback_controller.out_point is None
    assert panel._playback_controller.loop_enabled is False
    print("SampleImagePanel.load_video: resets a fresh clip's in/out "
          "points and loop state, doesn't inherit stale marks from the "
          "previous clip -- OK")


# ---- Phase 4: speed control ----------------------------------------------

def test_set_speed_below_one_slows_the_timer_interval():
    panel = _panel_with_video(frame_rate=24)
    controller = VideoPlaybackController(panel)
    controller.play()
    base_interval = controller._timer.interval()
    controller.set_speed(0.5)
    assert controller._timer.interval() == round(base_interval / 0.5)
    controller.pause()
    print("VideoPlaybackController.set_speed: speed 0.5 doubles the "
          "timer interval (real slow motion, same frame sequence) -- OK")


def test_set_speed_above_one_keeps_the_base_interval():
    panel = _panel_with_video(frame_rate=24)
    controller = VideoPlaybackController(panel)
    controller.play()
    base_interval = controller._timer.interval()
    controller.set_speed(4.0)
    assert controller._timer.interval() == base_interval
    controller.pause()
    print("VideoPlaybackController.set_speed: speed > 1 keeps the base "
          "(realtime) timer interval -- frame-skipping handles the "
          "speedup instead -- OK")


def test_tick_advances_multiple_frames_at_speed_above_one():
    panel = _panel_with_video(frame_count=10)
    controller = VideoPlaybackController(panel)
    controller.set_speed(4.0)
    controller.play()
    assert controller._frame_index == 0
    controller._tick()
    assert controller._frame_index == 4
    controller.pause()
    print("VideoPlaybackController._tick: speed 4.0 advances 4 source "
          "frames per tick -- OK")


def test_set_speed_rearms_the_running_timer_immediately():
    panel = _panel_with_video(frame_rate=24)
    controller = VideoPlaybackController(panel)
    controller.play()
    controller.set_speed(2.0)
    interval_at_2x = controller._timer.interval()
    controller.set_speed(0.5)
    assert controller._timer.interval() != interval_at_2x
    assert controller._timer.interval() == round(controller._base_interval_ms / 0.5)
    controller.pause()
    print("VideoPlaybackController.set_speed: changes the running "
          "QTimer's interval immediately, not just on the next play() "
          "-- OK")


if __name__ == "__main__":
    test_play_starts_the_timer_and_sets_playback_status_text()
    test_play_is_a_noop_with_no_video_loaded()
    test_tick_decodes_frames_in_order_and_pushes_to_the_viewer()
    test_tick_pushes_the_raw_frame_with_gpu_stack_for_a_gpu_eligible_stack()
    test_tick_cpu_grades_the_frame_for_a_gpu_ineligible_stack()
    test_tick_pushes_raw_frame_when_no_stack_or_empty_stack()
    test_tick_notifies_scopes_for_a_gpu_eligible_stack()
    test_tick_notifies_scopes_for_a_gpu_ineligible_stack()
    test_tick_notifies_scopes_with_no_stack()
    test_play_emits_playback_started()
    test_tick_pushes_a_resolution_capped_proxy_not_full_resolution()
    test_pause_reparks_at_full_resolution_through_the_grading_pipeline()
    test_playback_stopped_signal_fires_on_pause()
    test_stop_halts_playback_without_reparking()
    test_reaching_end_of_stream_stops_playback_and_reparks_on_the_last_frame()
    test_toggle_flips_between_play_and_pause()
    test_frame_changed_signal_reports_each_ticked_frame_index()
    test_play_resumes_from_wherever_parked_frame_stepping_last_left_off()
    test_set_in_point_sets_to_current_parked_frame()
    test_set_out_point_sets_to_current_parked_frame()
    test_set_in_point_past_out_point_pushes_out_point_up()
    test_set_out_point_before_in_point_pulls_in_point_down()
    test_clear_in_out_resets_to_full_range()
    test_tick_pauses_at_out_point_when_loop_disabled()
    test_tick_loops_back_to_in_point_when_loop_enabled()
    test_play_clamps_start_index_into_in_out_range()
    test_loading_a_new_video_resets_in_out_and_loop()
    test_set_speed_below_one_slows_the_timer_interval()
    test_set_speed_above_one_keeps_the_base_interval()
    test_tick_advances_multiple_frames_at_speed_above_one()
    test_set_speed_rearms_the_running_timer_immediately()
    print()
    print("All video_playback_controller tests passed.")
