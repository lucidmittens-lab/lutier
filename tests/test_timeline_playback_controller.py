"""Tests for timeline_playback_controller.py -- run directly:
python3 -m tests.test_timeline_playback_controller

Same "fully offscreen-Qt-testable, call _tick() directly rather than
running the real QTimer" convention as test_video_playback_controller.py
-- see that file's own docstring.
"""
import sys
import tempfile

import av
import numpy as np
from PIL import Image
from PySide6.QtWidgets import QApplication

import app_logic
import stack as st
import timeline as tl
from sample_image_panel import SampleImagePanel
from timeline_playback_controller import TimelineController, TIMELINE_PLAYBACK_STATUS_TEXT

FRAME_RATE = 24
WIDTH, HEIGHT = 32, 24


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _make_test_video(frame_count, frame_rate=FRAME_RATE, size=(WIDTH, HEIGHT), color_offset=0):
    """color_offset shifts the per-frame color ramp so two test videos
    are trivially distinguishable by pixel value -- used to confirm
    playback actually crossed from one clip's source into the next."""
    w, h = size
    path = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
    container = av.open(path, mode="w")
    stream = container.add_stream("libx264", rate=frame_rate)
    stream.width = w
    stream.height = h
    stream.pix_fmt = "yuv420p"
    for i in range(frame_count):
        color = (min(255, i * 25 + color_offset), max(0, 255 - i * 25), 128)
        frame = av.VideoFrame.from_ndarray(
            np.full((h, w, 3), color, dtype=np.uint8), format="rgb24")
        frame.pts = i
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()
    return path


def _make_test_still(size=(WIDTH, HEIGHT), value=200):
    w, h = size
    array = np.full((h, w, 3), value, dtype=np.uint8)
    path = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
    Image.fromarray(array, mode="RGB").save(path)
    return path


def _video_clip(frame_count, name="clip", color_offset=0, frame_rate=FRAME_RATE):
    path = _make_test_video(frame_count, frame_rate=frame_rate, color_offset=color_offset)
    return tl.TimelineClip(name, path, "video", 0, frame_count - 1)


def _still_clip(hold_frames, name="still"):
    path = _make_test_still()
    return tl.TimelineClip(name, path, "still", 0, hold_frames - 1)


def _controller(timeline_obj, sequence_stack=None):
    _app()
    panel = SampleImagePanel()
    sequence_stack = sequence_stack if sequence_stack is not None else st.Stack()
    controller = TimelineController(
        panel, timeline_getter=lambda: timeline_obj,
        sequence_stack_getter=lambda: sequence_stack)
    return panel, controller


def test_play_is_a_noop_with_no_clips():
    panel, controller = _controller(tl.Timeline())
    controller.play()
    assert not controller.is_playing
    assert not controller._timer.isActive()
    print("TimelineController.play: no-op with an empty Timeline -- OK")


def test_play_starts_the_timer_and_pushes_the_first_clips_first_frame():
    timeline_obj = tl.Timeline(clips=[_video_clip(10)])
    panel, controller = _controller(timeline_obj)
    controller.play()
    assert controller.is_playing
    assert controller._timer.isActive()
    assert panel.status_label.text() == TIMELINE_PLAYBACK_STATUS_TEXT
    pushed = panel.compare._after_state.array
    assert pushed is not None
    assert pushed.shape == (HEIGHT, WIDTH, 3)
    controller.stop()
    print("TimelineController.play: starts the timer, shows the "
          "grade-locked status text, and pushes the first clip's first "
          "frame -- OK")


def test_tick_advances_frames_within_a_clip():
    timeline_obj = tl.Timeline(clips=[_video_clip(10)])
    panel, controller = _controller(timeline_obj)
    controller.play()
    for expected_offset in (1, 2, 3):
        controller._tick()
        assert controller._frame_in_clip == expected_offset
        assert controller._clip_index == 0
    controller.stop()
    print("TimelineController._tick: advances frame_in_clip within a "
          "single clip -- OK")


def test_tick_crosses_clip_boundary_and_opens_the_next_clips_source():
    clip_a = _video_clip(3, name="A", color_offset=0)
    clip_b = _video_clip(3, name="B", color_offset=100)
    timeline_obj = tl.Timeline(clips=[clip_a, clip_b])
    panel, controller = _controller(timeline_obj)
    controller.play()
    # 3-frame clip: offsets 0 (already parked by play()), 1, 2 are clip A;
    # the 3rd tick must cross into clip B at its own offset 0.
    controller._tick()
    controller._tick()
    assert controller._clip_index == 0
    controller._tick()
    assert controller._clip_index == 1
    assert controller._frame_in_clip == 0
    pushed = panel.compare._after_state.array
    # clip B's frame 0 color has +100 red offset baked in (color_offset,
    # normalized to [0,1] by video_source.py) -- confirms this is
    # genuinely clip B's own source, not clip A's tail (whose red channel
    # never exceeds (2*25)/255 ~= 0.2 across its own 3 frames).
    assert pushed[0, 0, 0] > 0.3
    controller.stop()
    print("TimelineController._tick: crosses a clip boundary and opens "
          "the next clip's own VideoSource -- OK")


def test_composes_clip_stack_with_sequence_stack():
    """Both the clip's own grade AND the Sequence grade must apply
    together during Timeline playback (compose_stacks), not just one or
    the other -- the whole point of a per-clip grade "on top of" the
    Sequence look."""
    clip_stage = st.make_cdl_stage(slope=(1.1, 1.0, 1.0))
    sequence_stage = st.make_cdl_stage(slope=(1.0, 1.2, 1.0))
    clip = _video_clip(5)
    clip.stack = st.Stack(stages=[clip_stage])
    sequence_stack = st.Stack(stages=[sequence_stage])
    timeline_obj = tl.Timeline(clips=[clip])
    panel, controller = _controller(timeline_obj, sequence_stack=sequence_stack)
    controller.play()
    pushed_stack = panel.compare._after_state.gpu_stack
    assert pushed_stack is not None
    assert len(pushed_stack.stages) == 2, "compose_stacks must concatenate BOTH stacks' stages"
    controller.stop()
    print("TimelineController: composes the clip's own Stack with the "
          "Sequence Stack for every pushed frame -- OK")


def test_still_clip_pushes_once_and_does_not_redecode_per_tick():
    still = _still_clip(hold_frames=5)
    timeline_obj = tl.Timeline(clips=[still])
    panel, controller = _controller(timeline_obj)

    import timeline_playback_controller as tpc
    calls = []
    original = tpc.push_graded_frame_to_viewer
    tpc.push_graded_frame_to_viewer = lambda *a, **k: (calls.append(1), original(*a, **k))[-1]
    try:
        controller.play()  # 1 push, on entering the clip
        for _ in range(4):
            controller._tick()  # still holding -- must NOT push again
        assert len(calls) == 1, "a held still must be pushed once, not re-pushed every tick"
    finally:
        tpc.push_graded_frame_to_viewer = original
    controller.stop()
    print("TimelineController: a still clip is graded/pushed once on "
          "entry, never re-pushed while just holding -- OK")


def test_still_clip_advances_to_next_clip_after_its_hold_duration():
    still = _still_clip(hold_frames=3)
    video = _video_clip(5, name="after-still", color_offset=50)
    timeline_obj = tl.Timeline(clips=[still, video])
    panel, controller = _controller(timeline_obj)
    controller.play()
    # hold_frames=3: offsets 0 (parked), 1, 2 are the still; the 3rd tick
    # crosses into the video clip.
    controller._tick()
    controller._tick()
    assert controller._clip_index == 0
    controller._tick()
    assert controller._clip_index == 1
    assert controller._frame_in_clip == 0
    controller.stop()
    print("TimelineController: a still clip advances to the next clip "
          "after exactly its own hold duration -- OK")


def test_reaching_end_without_loop_stops_and_resets_position():
    timeline_obj = tl.Timeline(clips=[_video_clip(3)])
    panel, controller = _controller(timeline_obj)
    stopped = []
    controller.playback_stopped.connect(lambda: stopped.append(True))
    controller.play()
    for _ in range(10):
        if not controller.is_playing:
            break
        controller._tick()
    assert not controller.is_playing
    assert stopped == [True]
    assert controller._clip_index == 0
    assert controller._frame_in_clip == 0
    print("TimelineController: reaching the end without looping stops "
          "and resets position back to the start -- OK")


def test_looping_wraps_back_to_the_first_clip():
    clip_a = _video_clip(3, name="A", color_offset=0)
    clip_b = _video_clip(2, name="B", color_offset=100)
    timeline_obj = tl.Timeline(clips=[clip_a, clip_b])
    panel, controller = _controller(timeline_obj)
    controller.set_loop_enabled(True)
    controller.play()
    # Clip A (3 frames, offset 0 already parked by play()): ticks 1-2
    # reach offsets 1,2; tick 3 crosses into clip B at offset 0. Clip B
    # (2 frames): tick 4 reaches offset 1; tick 5 must wrap back to clip
    # A at offset 0 rather than stopping.
    for _ in range(5):
        controller._tick()
    assert controller.is_playing, "looping must never stop playback"
    assert controller._clip_index == 0
    assert controller._frame_in_clip == 0
    controller.stop()
    print("TimelineController: loop_enabled wraps playback back to the "
          "first clip instead of stopping -- OK")


def test_pause_then_play_resumes_from_the_same_position():
    timeline_obj = tl.Timeline(clips=[_video_clip(10)])
    panel, controller = _controller(timeline_obj)
    controller.play()
    controller._tick()
    controller._tick()
    controller.pause()
    assert not controller.is_playing
    assert controller._frame_in_clip == 2
    controller.play()
    assert controller.is_playing
    assert controller._frame_in_clip == 2, "play() after pause() must resume, not restart"
    controller.stop()
    print("TimelineController: pause() then play() resumes from the "
          "same position, doesn't restart -- OK")


def test_stop_resets_position_to_the_start():
    timeline_obj = tl.Timeline(clips=[_video_clip(10)])
    panel, controller = _controller(timeline_obj)
    controller.play()
    controller._tick()
    controller._tick()
    controller.stop()
    assert not controller.is_playing
    assert controller._clip_index == 0
    assert controller._frame_in_clip == 0
    print("TimelineController.stop: resets position back to the very "
          "start, unlike pause() -- OK")


def test_playback_started_and_stopped_signals_fire():
    timeline_obj = tl.Timeline(clips=[_video_clip(5)])
    panel, controller = _controller(timeline_obj)
    started, stopped = [], []
    controller.playback_started.connect(lambda: started.append(True))
    controller.playback_stopped.connect(lambda: stopped.append(True))
    controller.play()
    assert started == [True]
    controller.pause()
    assert stopped == [True]
    print("TimelineController: playback_started/playback_stopped fire "
          "on play()/pause() -- OK")


def test_a_missing_clip_file_stops_gracefully_instead_of_crashing():
    bad_clip = tl.TimelineClip("missing", "/no/such/file.mp4", "video", 0, 9)
    timeline_obj = tl.Timeline(clips=[bad_clip])
    panel, controller = _controller(timeline_obj)
    controller.play()  # must not raise
    assert not controller.is_playing
    print("TimelineController.play: a missing/unopenable clip file fails "
          "gracefully rather than crashing -- OK")


def test_seek_to_clip_repositions_where_the_next_play_resumes():
    clip_a = _video_clip(5, name="A")
    clip_b = _video_clip(5, name="B", color_offset=100)
    timeline_obj = tl.Timeline(clips=[clip_a, clip_b])
    panel, controller = _controller(timeline_obj)
    controller.play()
    controller._tick()
    controller._tick()
    controller.pause()
    assert controller._clip_index == 0 and controller._frame_in_clip == 2

    controller.seek_to_clip(1)
    assert controller._clip_index == 1
    assert controller._frame_in_clip == 0

    controller.play()
    assert controller._clip_index == 1, "play() must resume where seek_to_clip pointed it, not the old position"
    controller.stop()
    print("TimelineController.seek_to_clip: repositions where a "
          "subsequent play() resumes from -- OK")


def test_seek_to_clip_is_a_noop_while_playing():
    timeline_obj = tl.Timeline(clips=[_video_clip(5), _video_clip(5)])
    panel, controller = _controller(timeline_obj)
    controller.play()
    controller.seek_to_clip(1)
    assert controller._clip_index == 0, "seek_to_clip must not disturb an in-progress play"
    controller.stop()
    print("TimelineController.seek_to_clip: no-op while already playing -- OK")


def test_seek_to_record_frame_lands_in_the_right_clip_and_offset():
    """Per direct feedback: "the scrub bar seems to only represent a
    single clip in the timeline. it should represent the whole
    timeline" -- seek_to_record_frame takes a position in the WHOLE
    Timeline's own numbering (current_record_frame's), not a per-clip
    one (unlike seek_to_clip, which only ever jumps to a clip's start)."""
    clip_a = _video_clip(5, name="A")
    clip_b = _video_clip(5, name="B", color_offset=100)
    timeline_obj = tl.Timeline(clips=[clip_a, clip_b])
    panel, controller = _controller(timeline_obj)

    controller.seek_to_record_frame(2)
    assert controller._clip_index == 0 and controller._frame_in_clip == 2
    assert controller.current_record_frame() == 2

    controller.seek_to_record_frame(7)  # clip A has 5 frames -> clip B's own offset 2
    assert controller._clip_index == 1 and controller._frame_in_clip == 2
    assert controller.current_record_frame() == 7
    print("TimelineController.seek_to_record_frame: lands in the right "
          "clip at the right offset for a whole-Timeline position -- OK")


def test_seek_to_record_frame_pushes_the_resulting_frame_to_the_viewer():
    clip_a = _video_clip(5, name="A")
    clip_b = _video_clip(5, name="B", color_offset=100)
    timeline_obj = tl.Timeline(clips=[clip_a, clip_b])
    panel, controller = _controller(timeline_obj)

    controller.seek_to_record_frame(7)  # clip B, offset 2
    pushed = panel.compare._after_state.array
    assert pushed is not None
    assert pushed.shape == (HEIGHT, WIDTH, 3)
    print("TimelineController.seek_to_record_frame: pushes the "
          "resulting frame to the Viewer immediately, unlike "
          "seek_to_clip -- OK")


def test_seek_to_record_frame_clamps_out_of_range_positions():
    timeline_obj = tl.Timeline(clips=[_video_clip(5), _video_clip(5)])
    panel, controller = _controller(timeline_obj)

    controller.seek_to_record_frame(-3)
    assert controller.current_record_frame() == 0

    controller.seek_to_record_frame(999)
    assert controller.current_record_frame() == 9, "should clamp to the last frame (10 total, 0-indexed)"
    print("TimelineController.seek_to_record_frame: clamps an "
          "out-of-range position into [0, total_frame_count-1] -- OK")


def test_seek_to_record_frame_pauses_playback_first():
    timeline_obj = tl.Timeline(clips=[_video_clip(5), _video_clip(5)])
    panel, controller = _controller(timeline_obj)
    controller.play()
    assert controller.is_playing

    controller.seek_to_record_frame(6)
    assert not controller.is_playing
    assert controller.current_record_frame() == 6
    print("TimelineController.seek_to_record_frame: pauses an "
          "in-progress play before seeking, unlike seek_to_clip's "
          "no-op-while-playing -- OK")


def test_seek_to_record_frame_is_a_noop_with_no_clips():
    panel, controller = _controller(tl.Timeline())
    controller.seek_to_record_frame(5)  # must not raise
    assert controller.current_record_frame() is None
    print("TimelineController.seek_to_record_frame: no-op on an empty "
          "Timeline -- OK")


def test_frame_changed_listeners_see_the_correct_record_frame_during_the_signal():
    """Regression test for a real bug: _enter_clip used to emit
    frame_changed BEFORE its caller updated self._clip_index, so a
    listener reading current_record_frame() synchronously during that
    signal (e.g. TimelinePanel's playhead sync) got the NEW frame_in_clip
    paired with the OLD clip_index -- e.g. seeking to record frame 30 on
    a Timeline whose selection had left _clip_index at 1 (a later clip)
    would emit frame_changed while current_record_frame() briefly read
    80 instead of 30. _enter_clip now sets self._clip_index itself,
    before frame_changed.emit()."""
    clip_a = _video_clip(5, name="A")
    clip_b = _video_clip(5, name="B", color_offset=100)
    timeline_obj = tl.Timeline(clips=[clip_a, clip_b])
    panel, controller = _controller(timeline_obj)
    # Leave stale state pointing at clip B, as a prior selection/seek_to_
    # clip would (see main_window.py's _on_timeline_clip_selected) --
    # the exact precondition that exposed the bug.
    controller.seek_to_clip(1)
    assert controller._clip_index == 1

    seen_during_signal = []
    controller.frame_changed.connect(lambda: seen_during_signal.append(controller.current_record_frame()))
    controller.seek_to_record_frame(3)  # clip A, offset 3 -- NOT clip B's numbering
    assert seen_during_signal == [3], (
        "a frame_changed listener must see the correct, already-updated "
        "record frame, not a stale clip_index paired with the new offset")

    # Same check across an ordinary clip-boundary crossing during playback
    # (clip_a/clip_b are both 5 frames -- offsets 0..4 -- so 5 ticks past
    # play()'s own initial parked push are needed to cross into clip B).
    controller.stop()
    controller.play()
    seen_during_signal.clear()  # drop play()'s own initial push
    for _ in range(5):
        controller._tick()
    assert controller._clip_index == 1 and controller._frame_in_clip == 0
    assert seen_during_signal[-1] == 5, "clip A's own 5 frames precede clip B's offset 0 in record-space"
    controller.stop()
    print("TimelineController: frame_changed listeners see the correct "
          "record frame during the signal itself, at both a seek and a "
          "clip-boundary crossing -- OK")


def test_toggle_flips_between_play_and_pause():
    timeline_obj = tl.Timeline(clips=[_video_clip(10)])
    panel, controller = _controller(timeline_obj)
    controller.toggle()
    assert controller.is_playing
    controller.toggle()
    assert not controller.is_playing
    print("TimelineController.toggle: flips between play and pause -- OK")


def test_set_speed_above_one_advances_multiple_frames_per_tick():
    timeline_obj = tl.Timeline(clips=[_video_clip(20)])
    panel, controller = _controller(timeline_obj)
    controller.play()
    controller.set_speed(4.0)
    controller._tick()
    assert controller._frame_in_clip == 4
    controller.stop()
    print("TimelineController: speed > 1 advances multiple source frames "
          "per tick -- OK")


if __name__ == "__main__":
    test_play_is_a_noop_with_no_clips()
    test_play_starts_the_timer_and_pushes_the_first_clips_first_frame()
    test_tick_advances_frames_within_a_clip()
    test_tick_crosses_clip_boundary_and_opens_the_next_clips_source()
    test_composes_clip_stack_with_sequence_stack()
    test_still_clip_pushes_once_and_does_not_redecode_per_tick()
    test_still_clip_advances_to_next_clip_after_its_hold_duration()
    test_reaching_end_without_loop_stops_and_resets_position()
    test_looping_wraps_back_to_the_first_clip()
    test_pause_then_play_resumes_from_the_same_position()
    test_stop_resets_position_to_the_start()
    test_playback_started_and_stopped_signals_fire()
    test_a_missing_clip_file_stops_gracefully_instead_of_crashing()
    test_seek_to_clip_repositions_where_the_next_play_resumes()
    test_seek_to_clip_is_a_noop_while_playing()
    test_frame_changed_listeners_see_the_correct_record_frame_during_the_signal()
    test_toggle_flips_between_play_and_pause()
    test_set_speed_above_one_advances_multiple_frames_per_tick()
    print("\nAll timeline_playback_controller tests passed.")
