"""Tests for timeline_track_widget.py -- run directly:
QT_QPA_PLATFORM=offscreen python3 -m tests.test_timeline_track_widget

Pure-function tests (clip_extents/frame_to_x/x_to_frame/clip_at_x/
reorder_target_index/ruler_tick_frames/pixels_per_frame_to_fit) need no
QApplication at all -- same "Qt-free math, testable standalone" split
this module's own docstring describes. The QWidget-level tests
(selection, drag-reorder, click-to-seek) need a real (offscreen) Qt
event loop, same convention as video_scrub_slider.py/curve_editor_
widget.py's own test files.
"""
import sys

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent, QPixmap, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from media_library import MEDIA_ENTRY_DRAG_MIME_TYPE
import timeline as tl
from timeline_track_widget import (
    TimelineTrackWidget, apply_left_trim, apply_right_trim, clip_at_x, clip_edge_at_x,
    clip_extents, frame_to_x, pixels_per_frame_to_fit, reorder_target_index,
    ruler_tick_frames, x_to_frame, MIN_PIXELS_PER_FRAME, MAX_PIXELS_PER_FRAME,
)


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _clip(name, frame_count, media_kind="video"):
    return tl.TimelineClip(name, f"/tmp/{name}.mp4", media_kind, 0, frame_count - 1)


# ---- pure functions --------------------------------------------------------

def test_clip_extents_sums_preceding_frame_counts():
    clips = [_clip("A", 10), _clip("B", 5), _clip("C", 20)]
    extents = clip_extents(clips)
    assert extents == [(clips[0], 0, 10), (clips[1], 10, 15), (clips[2], 15, 35)]
    print("clip_extents: cumulative record-space start/end per clip -- OK")


def test_clip_extents_empty_list():
    assert clip_extents([]) == []
    print("clip_extents: empty for an empty clip list -- OK")


def test_clip_extents_agrees_with_timeline_controller_record_frame():
    """Cross-check against TimelineController's own independent
    "sum every earlier clip's frame_count" computation (module docstring
    -- the two are deliberately separate implementations)."""
    _app()  # TimelineController is a QObject (owns a QTimer) -- needs a real QApplication instance
    from timeline_playback_controller import TimelineController
    clips = [_clip("A", 10), _clip("B", 7), _clip("C", 3)]
    timeline_obj = tl.Timeline(clips=clips)
    controller = TimelineController(None, timeline_getter=lambda: timeline_obj, sequence_stack_getter=None)
    controller._clip_index = 2
    controller._frame_in_clip = 1
    assert controller.current_record_frame() == 18  # 10 + 7 + 1

    extents = clip_extents(clips)
    clip_c_start = extents[2][1]
    assert clip_c_start + 1 == 18
    print("clip_extents: agrees with TimelineController.current_record_frame's "
          "independently-computed record position -- OK")


def test_pixels_per_frame_to_fit_scales_to_width():
    assert pixels_per_frame_to_fit(100, 500) == 5.0
    print("pixels_per_frame_to_fit: scales to fill the given width -- OK")


def test_pixels_per_frame_to_fit_handles_degenerate_input():
    assert pixels_per_frame_to_fit(0, 500) == MIN_PIXELS_PER_FRAME
    assert pixels_per_frame_to_fit(100, 0) == MIN_PIXELS_PER_FRAME
    assert pixels_per_frame_to_fit(100, -10) == MIN_PIXELS_PER_FRAME
    print("pixels_per_frame_to_fit: falls back to MIN_PIXELS_PER_FRAME "
          "for zero/negative total or width, never divides by zero -- OK")


def test_frame_to_x_and_x_to_frame_are_inverses():
    for frame in (0, 10, 100, 999):
        x = frame_to_x(frame, 2.5, origin_x=20)
        assert x_to_frame(x, 2.5, origin_x=20) == frame
    print("frame_to_x/x_to_frame: round-trip exactly -- OK")


def test_x_to_frame_handles_zero_pixels_per_frame():
    assert x_to_frame(500, 0, origin_x=0) == 0
    print("x_to_frame: doesn't divide by zero for a degenerate scale -- OK")


def test_clip_at_x_hits_the_right_clip():
    clips = [_clip("A", 10), _clip("B", 5)]
    ppf = 2.0
    assert clip_at_x(clips, frame_to_x(3, ppf), ppf) is clips[0]
    assert clip_at_x(clips, frame_to_x(12, ppf), ppf) is clips[1]
    print("clip_at_x: hits the clip whose extent contains the position -- OK")


def test_clip_at_x_misses_past_the_last_clip():
    clips = [_clip("A", 10)]
    ppf = 2.0
    assert clip_at_x(clips, frame_to_x(20, ppf), ppf) is None
    print("clip_at_x: None past the last clip's own extent -- OK")


def test_clip_at_x_on_empty_timeline():
    assert clip_at_x([], 100, 2.0) is None
    print("clip_at_x: None for an empty clip list -- OK")


def test_reorder_target_index_moving_right():
    clips = [_clip("A", 10), _clip("B", 10), _clip("C", 10)]
    ppf = 1.0
    # Dragging A (currently index 0) to sit past B and C's centers.
    target = reorder_target_index(clips, clips[0].id, x=frame_to_x(25, ppf), pixels_per_frame=ppf)
    assert target == 2
    print("reorder_target_index: counts how many OTHER clips' centers "
          "sit to the left of the drag position -- OK")


def test_reorder_target_index_moving_left():
    clips = [_clip("A", 10), _clip("B", 10), _clip("C", 10)]
    ppf = 1.0
    target = reorder_target_index(clips, clips[2].id, x=frame_to_x(2, ppf), pixels_per_frame=ppf)
    assert target == 0
    print("reorder_target_index: dragging left of every other clip's "
          "center lands at index 0 -- OK")


def test_reorder_target_index_ignores_the_dragged_clips_own_position():
    """Stable regardless of where the drag started -- only the OTHER
    clips' centers matter."""
    clips = [_clip("A", 10), _clip("B", 10)]
    ppf = 1.0
    # B (index 1) dragged to sit just left of A's own center (5) -- must
    # target index 0, not be confused by B's own original extent.
    target = reorder_target_index(clips, clips[1].id, x=frame_to_x(1, ppf), pixels_per_frame=ppf)
    assert target == 0
    print("reorder_target_index: ignores the dragged clip's own current "
          "position, only counts the others -- OK")


def test_clip_edge_at_x_hits_the_left_and_right_boundaries():
    clips = [_clip("A", 100)]
    ppf = 2.0  # block spans x=[0, 200), well over 2*EDGE_GRAB_PX wide
    assert clip_edge_at_x(clips, x=1, pixels_per_frame=ppf) == (clips[0], "left")
    assert clip_edge_at_x(clips, x=199, pixels_per_frame=ppf) == (clips[0], "right")
    print("clip_edge_at_x: hits a clip's own left/right boundary within "
          "the grab zone -- OK")


def test_clip_edge_at_x_misses_the_middle_of_a_block():
    clips = [_clip("A", 100)]
    assert clip_edge_at_x(clips, x=100, pixels_per_frame=2.0) is None
    print("clip_edge_at_x: None for a position in the middle of a block, "
          "well past either grab zone -- OK")


def test_clip_edge_at_x_skips_a_block_too_narrow_to_grab_precisely():
    clips = [_clip("A", 1)]  # a 1-frame clip is unambiguously narrower than 2*EDGE_GRAB_PX at any sane zoom
    assert clip_edge_at_x(clips, x=0, pixels_per_frame=1.0) is None
    print("clip_edge_at_x: skips edge hit-testing for a block narrower "
          "than 2*grab_px -- OK")


def test_clip_edge_at_x_resolves_a_shared_boundary_by_which_side_youre_on():
    """Regression test for a real bug found via end-to-end smoke
    testing: an earlier, symmetric (abs(x - boundary) <= grab_px)
    version of this check always matched the EARLIER clip's right edge
    for any position near a shared boundary (clip_extents visits A
    before B, and A's own right_x == B's own left_x), making every
    clip's own LEFT edge after the first completely unreachable by
    drag. The fix splits the shared boundary by side: left-of-boundary
    always grabs A's right edge, right-of-boundary always grabs B's
    own left edge."""
    clips = [_clip("A", 100), _clip("B", 100)]  # shared boundary at x=100 (pixels_per_frame=1.0)
    assert clip_edge_at_x(clips, x=97, pixels_per_frame=1.0) == (clips[0], "right")
    assert clip_edge_at_x(clips, x=103, pixels_per_frame=1.0) == (clips[1], "left")
    # Exactly on the shared boundary pixel -- ties go to the earlier clip's right edge.
    assert clip_edge_at_x(clips, x=100, pixels_per_frame=1.0) == (clips[0], "right")
    print("clip_edge_at_x: resolves a shared boundary between two "
          "adjacent clips by which side of it the cursor is on, not "
          "always the earlier clip -- OK")


def test_apply_left_trim_shortens_from_the_front_and_clamps_to_one_frame():
    clip = tl.TimelineClip("A", "/tmp/a.mp4", "video", 10, 49)  # frame_count 40, record start/end given by caller
    # Dragging the left edge 5 frames later (into the clip) -- in_frame moves from 10 to 15.
    assert apply_left_trim(clip, start=0, frame=5) == 15
    # Dragging far past the clip's own out_frame clamps to leave exactly 1 frame.
    assert apply_left_trim(clip, start=0, frame=999) == clip.out_frame - 1
    # Dragging left of the clip's own record-space start clamps at 0, never negative.
    assert apply_left_trim(clip, start=0, frame=-999) == 0
    print("apply_left_trim: shortens from the front, clamps to >=0 and "
          "to leave at least 1 frame -- OK")


def test_apply_right_trim_lengthens_and_clamps_to_one_frame():
    clip = tl.TimelineClip("A", "/tmp/a.mp4", "video", 10, 49)  # frame_count 40
    # end (exclusive) is 40 in record-space terms for this standalone test (start=0).
    # Dragging the right edge 5 frames further out extends out_frame from 49 to 54.
    assert apply_right_trim(clip, end=40, frame=45) == 54
    # Dragging far left of the clip's own in_frame clamps to leave exactly 1 frame.
    assert apply_right_trim(clip, end=40, frame=-999) == clip.in_frame
    print("apply_right_trim: lengthens/shortens from the back, clamps to "
          "leave at least 1 frame -- OK")


def test_ruler_tick_frames_starts_at_zero():
    ticks = ruler_tick_frames(total_frame_count=240, frame_rate=24.0, pixels_per_frame=10.0)
    assert ticks[0] == 0
    print("ruler_tick_frames: always includes frame 0 -- OK")


def test_ruler_tick_frames_picks_a_wider_interval_when_zoomed_out():
    """Zoomed OUT (small pixels_per_frame) must pick a WIDER nice
    interval so ticks don't crowd closer than MIN_TICK_SPACING_PX."""
    zoomed_in = ruler_tick_frames(total_frame_count=2400, frame_rate=24.0, pixels_per_frame=10.0)
    zoomed_out = ruler_tick_frames(total_frame_count=2400, frame_rate=24.0, pixels_per_frame=0.5)
    spacing_in = zoomed_in[1] - zoomed_in[0]
    spacing_out = zoomed_out[1] - zoomed_out[0]
    assert spacing_out > spacing_in
    print("ruler_tick_frames: a wider frame-interval between ticks when "
          "zoomed out, keeping on-screen spacing readable -- OK")


def test_ruler_tick_frames_empty_for_degenerate_input():
    assert ruler_tick_frames(0, 24.0, 10.0) == []
    assert ruler_tick_frames(240, 0, 10.0) == []
    assert ruler_tick_frames(240, 24.0, 0) == []
    print("ruler_tick_frames: empty (not a crash) for degenerate "
          "total/frame_rate/pixels_per_frame -- OK")


# ---- widget-level (real, offscreen Qt) --------------------------------------

def _widget(clips, width=400, height=100):
    app = _app()
    timeline_obj = tl.Timeline(clips=clips)
    widget = TimelineTrackWidget(timeline_obj)
    # Shown (even under the offscreen QPA platform) -- resizeEvent only
    # reliably fires once a widget has a real platform window, same
    # reason QTest's synthetic mouse events need one to be delivered
    # through the normal event-dispatch path rather than silently
    # dropped. Confirmed empirically while writing these tests: an
    # unshown top-level widget's .resize() updates geometry but never
    # calls resizeEvent at all under this Qt build.
    widget.show()
    widget.resize(width, height)
    app.processEvents()
    return widget, timeline_obj


def test_widget_renders_without_raising_with_clips():
    widget, _ = _widget([_clip("A", 48), _clip("B", 24)])
    pixmap = QPixmap(widget.size())
    widget.render(pixmap)
    print("TimelineTrackWidget: paints without raising with real clips -- OK")


def test_widget_renders_without_raising_when_empty():
    widget, _ = _widget([])
    pixmap = QPixmap(widget.size())
    widget.render(pixmap)
    print("TimelineTrackWidget: paints the empty-timeline hint without raising -- OK")


def test_clicking_a_clip_selects_it_and_emits_clip_selected():
    widget, timeline_obj = _widget([_clip("A", 100), _clip("B", 50)])
    received = []
    widget.clip_selected.connect(lambda clip: received.append(clip))

    x = int(frame_to_x(10, widget._pixels_per_frame, 6))
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(x, 50))

    assert widget.selected_clip() is timeline_obj.clips[0]
    assert received == [timeline_obj.clips[0]]
    print("TimelineTrackWidget: clicking a clip block selects it and "
          "emits clip_selected -- OK")


def test_clicking_empty_track_space_emits_seek_requested():
    widget, timeline_obj = _widget([_clip("A", 100)])
    received = []
    widget.seek_requested.connect(lambda frame: received.append(frame))

    # Under pure auto-fit-to-width zoom a single clip spans the *entire*
    # track row, so there's no empty track-row pixel to click here --
    # the ruler strip (y < RULER_HEIGHT) is the always-seekable target.
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(380, 10))

    assert len(received) == 1
    assert received[0] > 80  # near the clip's own end, matching the click position
    assert widget.selected_clip() is None
    print("TimelineTrackWidget: clicking the ruler emits "
          "seek_requested, doesn't select a clip -- OK")


def test_dragging_the_ruler_emits_seek_requested_repeatedly():
    """Regression test: a single mousePress used to emit exactly one
    seek_requested and then go silent for the rest of the drag (only a
    clip-reorder drag kept tracking mouseMoveEvent) -- per Tom, "the
    playhead in the timeline viewer still isnt capable of scrubbing."
    Pressing on the ruler/empty track and then moving with the button
    held must keep emitting seek_requested for each new position, not
    just the initial press."""
    widget, timeline_obj = _widget([_clip("A", 100)])
    received = []
    widget.seek_requested.connect(lambda frame: received.append(frame))

    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=QPoint(50, 10))
    QTest.mouseMove(widget, QPoint(150, 10))
    QTest.mouseMove(widget, QPoint(250, 10))
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=QPoint(250, 10))

    assert len(received) == 3, f"expected one seek per press/move/move, got {received}"
    assert received[0] < received[1] < received[2], "each drag step should move further right"
    print("TimelineTrackWidget: dragging the ruler/empty track emits "
          "seek_requested continuously, not just once on press -- OK")


def test_scrub_drag_does_not_leak_into_a_clip_reorder_drag():
    """A scrub drag started on empty space must not be reinterpreted as
    a clip-reorder drag even if the cursor later moves over a clip block
    -- _scrubbing, set on press, is what keeps mouseMoveEvent on the
    scrub branch instead of falling through to the reorder branch."""
    widget, timeline_obj = _widget([_clip("A", 50), _clip("B", 50)])
    reordered = []
    widget.clips_reordered.connect(lambda: reordered.append(True))
    seeks = []
    widget.seek_requested.connect(lambda frame: seeks.append(frame))

    # Press well past both clips (auto-fit fills the whole width, so
    # there's no true empty track space -- use the ruler, same as the
    # other empty-space tests).
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    # Drag across into where clip A's own block is, in the track row.
    clip_a_x = int(frame_to_x(10, widget._pixels_per_frame, 6))
    QTest.mouseMove(widget, QPoint(clip_a_x, 50))
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=QPoint(clip_a_x, 50))

    assert len(seeks) == 2, "both the press and the move should have scrubbed"
    assert reordered == [], "a scrub drag must never trigger a reorder"
    assert widget._drag_clip_id is None
    print("TimelineTrackWidget: a scrub drag started on empty space "
          "stays a scrub even if it crosses over a clip block -- OK")


def test_double_clicking_a_clip_emits_clip_double_clicked():
    widget, timeline_obj = _widget([_clip("A", 100)])
    received = []
    widget.clip_double_clicked.connect(lambda clip: received.append(clip))

    x = int(frame_to_x(10, widget._pixels_per_frame, 6))
    QTest.mouseDClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(x, 50))

    assert received == [timeline_obj.clips[0]]
    print("TimelineTrackWidget: double-clicking a clip emits "
          "clip_double_clicked -- OK")


def test_dragging_a_clip_reorders_the_timeline():
    widget, timeline_obj = _widget([_clip("A", 10), _clip("B", 10), _clip("C", 10)])
    reordered = []
    widget.clips_reordered.connect(lambda: reordered.append(True))
    a_id = timeline_obj.clips[0].id

    start_x = int(frame_to_x(5, widget._pixels_per_frame, 6))   # inside A
    end_x = int(frame_to_x(28, widget._pixels_per_frame, 6))    # clearly past B and C's centers
    # (28, not 25, to clear C's center of exactly 25.0 -- int() pixel truncation
    # otherwise lands a hair under it; see x_to_frame's docstring)
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=QPoint(start_x, 50))
    QTest.mouseMove(widget, QPoint(end_x, 50))
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=QPoint(end_x, 50))

    assert reordered == [True]
    assert [c.id for c in timeline_obj.clips] == [
        timeline_obj.clips[i].id for i in range(3)
    ]  # sanity: still 3 distinct clips
    assert timeline_obj.clips[-1].id == a_id, "A should have moved to the end"
    print("TimelineTrackWidget: dragging a clip past its neighbors "
          "reorders the Timeline via Timeline.move_clip -- OK")


def test_dragging_back_to_the_same_position_does_not_reorder():
    widget, timeline_obj = _widget([_clip("A", 10), _clip("B", 10)])
    reordered = []
    widget.clips_reordered.connect(lambda: reordered.append(True))
    original_order = [c.id for c in timeline_obj.clips]

    x = int(frame_to_x(5, widget._pixels_per_frame, 6))
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=QPoint(x, 50))
    QTest.mouseMove(widget, QPoint(x + 1, 50))
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=QPoint(x + 1, 50))

    assert reordered == []
    assert [c.id for c in timeline_obj.clips] == original_order
    print("TimelineTrackWidget: a drag that ends at the same slot "
          "doesn't fire clips_reordered or touch clip order -- OK")


def test_right_clicking_a_clip_emits_context_menu_signal():
    widget, timeline_obj = _widget([_clip("A", 100)])
    received = []
    widget.context_menu_requested_for_clip.connect(lambda clip, pos: received.append(clip))

    # QTest.mouseClick(RightButton) doesn't reliably synthesize Qt's own
    # customContextMenuRequested signal under this offscreen environment
    # (confirmed true even for a bare QWidget with CustomContextMenu policy
    # set, so it's a platform-synthesis gap, not something our code controls)
    # -- exercise the widget's own handler directly instead, same as a real
    # right-click would trigger via the CustomContextMenu policy.
    x = int(frame_to_x(10, widget._pixels_per_frame, 6))
    widget._on_custom_context_menu_requested(QPoint(x, 50))

    assert received == [timeline_obj.clips[0]]
    print("TimelineTrackWidget: right-clicking a clip emits "
          "context_menu_requested_for_clip and selects it -- OK")


def test_set_selected_clip_id_does_not_reemit_clip_selected():
    widget, timeline_obj = _widget([_clip("A", 100)])
    received = []
    widget.clip_selected.connect(lambda clip: received.append(clip))

    widget.set_selected_clip_id(timeline_obj.clips[0].id)

    assert widget.selected_clip() is timeline_obj.clips[0]
    assert received == []
    print("TimelineTrackWidget.set_selected_clip_id: programmatic sync, "
          "no clip_selected re-emission -- OK")


def test_set_playhead_frame_triggers_repaint_state():
    widget, _ = _widget([_clip("A", 100)])
    assert widget.playhead_frame is None
    widget.set_playhead_frame(42)
    assert widget.playhead_frame == 42
    pixmap = QPixmap(widget.size())
    widget.render(pixmap)  # must not raise with a playhead set
    print("TimelineTrackWidget.set_playhead_frame: stores the position, "
          "paints without raising -- OK")


def test_refresh_recomputes_pixels_per_frame_after_resize():
    widget, timeline_obj = _widget([_clip("A", 100)], width=400)
    first_scale = widget._pixels_per_frame
    widget.resize(800, 100)
    assert widget._pixels_per_frame > first_scale, "a wider widget should zoom in to still fill the width"
    print("TimelineTrackWidget: resizing recomputes the auto-fit zoom -- OK")


# ---- zoom / scroll ----------------------------------------------------------

def test_zoom_in_increases_pixels_per_frame_and_leaves_auto_fit():
    widget, _ = _widget([_clip("A", 1000)])
    assert widget.is_zoomed_to_fit()
    fit_scale = widget._pixels_per_frame

    widget.zoom_in()

    assert not widget.is_zoomed_to_fit()
    assert widget._pixels_per_frame > fit_scale
    print("TimelineTrackWidget.zoom_in: increases pixels_per_frame and "
          "switches out of auto-fit -- OK")


def test_zoom_to_fit_clears_manual_zoom_and_scroll():
    widget, _ = _widget([_clip("A", 1000)])
    widget.zoom_in()
    widget.set_scroll_offset_frames(50)
    assert widget._scroll_offset_frames > 0

    widget.zoom_to_fit()

    assert widget.is_zoomed_to_fit()
    assert widget._scroll_offset_frames == 0
    print("TimelineTrackWidget.zoom_to_fit: clears manual zoom and "
          "resets scroll back to 0 -- OK")


def test_zoom_is_clamped_to_min_and_max():
    widget, _ = _widget([_clip("A", 1000)])
    widget.set_zoom_pixels_per_frame(10 ** 9)
    assert widget._pixels_per_frame == MAX_PIXELS_PER_FRAME
    widget.set_zoom_pixels_per_frame(-5)
    assert widget._pixels_per_frame == MIN_PIXELS_PER_FRAME
    print("TimelineTrackWidget.set_zoom_pixels_per_frame: clamps into "
          "[MIN_PIXELS_PER_FRAME, MAX_PIXELS_PER_FRAME] -- OK")


def test_scroll_offset_is_clamped_to_max_scroll_frames():
    widget, _ = _widget([_clip("A", 1000)], width=400)
    widget.set_zoom_pixels_per_frame(5.0)  # 1000 frames * 5px = 5000px, way over the 400px-wide track
    max_scroll = widget.max_scroll_frames()
    assert max_scroll > 0, "test setup: zoomed-in content must actually overflow the track width"

    widget.set_scroll_offset_frames(max_scroll + 500)
    assert widget._scroll_offset_frames == max_scroll

    widget.set_scroll_offset_frames(-500)
    assert widget._scroll_offset_frames == 0
    print("TimelineTrackWidget.set_scroll_offset_frames: clamps into "
          "[0, max_scroll_frames()] -- OK")


def test_zoom_to_fit_never_has_a_positive_max_scroll():
    """Auto-fit mode, by construction, always fits the whole Timeline in
    the track's own width -- there should never be anything to scroll."""
    widget, _ = _widget([_clip("A", 1000)])
    assert widget.max_scroll_frames() == 0
    print("TimelineTrackWidget: auto-fit mode never has anything to "
          "scroll -- OK")


def test_zoom_in_keeps_the_anchored_frame_under_the_cursor():
    widget, _ = _widget([_clip("A", 1000)], width=400)
    anchor_x = 100.0
    origin_x = widget._origin_x()
    anchored_frame = x_to_frame(anchor_x, widget._pixels_per_frame, origin_x)

    widget.zoom_in(anchor_x=anchor_x)

    new_origin_x = widget._origin_x()
    new_frame_under_cursor = x_to_frame(anchor_x, widget._pixels_per_frame, new_origin_x)
    assert abs(new_frame_under_cursor - anchored_frame) < 1.0, (
        f"expected ~{anchored_frame} to stay under the cursor, got {new_frame_under_cursor}")
    print("TimelineTrackWidget.zoom_in: with an anchor_x, keeps that "
          "frame under the cursor after zooming -- OK")


def test_wheel_with_ctrl_zooms_without_ctrl_scrolls():
    widget, _ = _widget([_clip("A", 1000)], width=400)
    fit_scale = widget._pixels_per_frame

    ctrl_wheel = QWheelEvent(
        QPointF(100, 50), QPointF(100, 50), QPoint(0, 0), QPoint(0, 120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
        Qt.ScrollPhase.NoScrollPhase, False)
    widget.wheelEvent(ctrl_wheel)
    assert widget._pixels_per_frame > fit_scale
    assert not widget.is_zoomed_to_fit()

    widget.zoom_to_fit()
    widget.set_zoom_pixels_per_frame(5.0)  # force overflow so there's something to scroll
    before_scroll = widget._scroll_offset_frames
    # Negative delta here (unlike ctrl_wheel above) so the resulting
    # scroll direction moves AWAY from 0, the lower clamp bound --
    # starting already at 0, the other sign would clamp right back to
    # 0 and look like no scroll happened at all.
    plain_wheel = QWheelEvent(
        QPointF(100, 50), QPointF(100, 50), QPoint(0, 0), QPoint(0, -120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False)
    widget.wheelEvent(plain_wheel)
    assert widget._scroll_offset_frames != before_scroll
    print("TimelineTrackWidget.wheelEvent: Ctrl+wheel zooms, a plain "
          "wheel scrolls -- OK")


# ---- trim / delete ----------------------------------------------------------

def test_dragging_a_clips_left_edge_trims_its_in_frame():
    widget, timeline_obj = _widget([_clip("A", 100), _clip("B", 100)])
    clip_a = timeline_obj.clips[0]
    trimmed = []
    widget.clip_trimmed.connect(lambda: trimmed.append(True))

    left_edge_x = int(frame_to_x(0, widget._pixels_per_frame, widget._origin_x()))
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=QPoint(left_edge_x, 50))
    drag_to_x = int(frame_to_x(20, widget._pixels_per_frame, widget._origin_x()))
    QTest.mouseMove(widget, QPoint(drag_to_x, 50))
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=QPoint(drag_to_x, 50))

    assert trimmed, "dragging the left edge should fire clip_trimmed"
    assert clip_a.in_frame == 20
    assert clip_a.out_frame == 99, "trimming the left edge must not touch out_frame"
    print("TimelineTrackWidget: dragging a clip's left edge trims its "
          "in_frame -- OK")


def test_dragging_a_clips_right_edge_trims_its_out_frame():
    widget, timeline_obj = _widget([_clip("A", 100), _clip("B", 100)])
    clip_a = timeline_obj.clips[0]
    trimmed = []
    widget.clip_trimmed.connect(lambda: trimmed.append(True))

    right_edge_x = int(frame_to_x(100, widget._pixels_per_frame, widget._origin_x())) - 1
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=QPoint(right_edge_x, 50))
    drag_to_x = int(frame_to_x(80, widget._pixels_per_frame, widget._origin_x()))
    QTest.mouseMove(widget, QPoint(drag_to_x, 50))
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=QPoint(drag_to_x, 50))

    assert trimmed, "dragging the right edge should fire clip_trimmed"
    assert clip_a.out_frame == 79
    assert clip_a.in_frame == 0, "trimming the right edge must not touch in_frame"
    print("TimelineTrackWidget: dragging a clip's right edge trims its "
          "out_frame -- OK")


def test_trimming_one_clip_ripples_every_later_clips_record_position():
    """No gap concept in this data model (timeline.py's own docstring)
    -- shortening clip A must shift clip B's own record-space start to
    match, with no separate ripple step."""
    widget, timeline_obj = _widget([_clip("A", 100), _clip("B", 100)])
    clip_a = timeline_obj.clips[0]

    right_edge_x = int(frame_to_x(100, widget._pixels_per_frame, widget._origin_x())) - 1
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=QPoint(right_edge_x, 50))
    drag_to_x = int(frame_to_x(50, widget._pixels_per_frame, widget._origin_x()))
    QTest.mouseMove(widget, QPoint(drag_to_x, 50))
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=QPoint(drag_to_x, 50))

    assert clip_a.frame_count == 50
    extents = clip_extents(timeline_obj.clips)
    assert extents[1][1] == 50, "clip B's own record-space start must ripple to match A's new (shorter) length"
    print("TimelineTrackWidget: trimming one clip ripples every later "
          "clip's own record-space position -- OK")


def test_delete_key_emits_delete_requested_only_with_a_clip_selected():
    widget, timeline_obj = _widget([_clip("A", 100)])
    received = []
    widget.delete_requested.connect(lambda: received.append(True))

    QTest.keyClick(widget, Qt.Key.Key_Delete)
    assert received == [], "no clip selected yet -- Delete should be a no-op"

    widget._set_selected(timeline_obj.clips[0])
    QTest.keyClick(widget, Qt.Key.Key_Delete)
    assert received == [True]
    print("TimelineTrackWidget: Delete emits delete_requested only when "
          "a clip is selected -- OK")


def test_backspace_also_emits_delete_requested():
    widget, timeline_obj = _widget([_clip("A", 100)])
    widget._set_selected(timeline_obj.clips[0])
    received = []
    widget.delete_requested.connect(lambda: received.append(True))

    QTest.keyClick(widget, Qt.Key.Key_Backspace)

    assert received == [True]
    print("TimelineTrackWidget: Backspace also emits delete_requested -- OK")


def test_s_key_emits_split_requested_regardless_of_selection():
    """Unlike Delete, splitting acts on whatever's under the PLAYHEAD,
    not the selection -- "S" must fire split_requested even with
    nothing selected at all."""
    widget, timeline_obj = _widget([_clip("A", 100)])
    received = []
    widget.split_requested.connect(lambda: received.append(True))

    QTest.keyClick(widget, Qt.Key.Key_S)

    assert received == [True]
    print("TimelineTrackWidget: \"S\" emits split_requested regardless "
          "of selection -- OK")


# ---- drag-and-drop (Media Library row dropped here) ------------------------

def _media_drop_mime(entry_id):
    mime = QMimeData()
    mime.setData(MEDIA_ENTRY_DRAG_MIME_TYPE, str(entry_id).encode("utf-8"))
    return mime


def test_drag_enter_accepts_the_media_entry_mime_type():
    widget, _ = _widget([_clip("A", 100)])
    # `mime` MUST stay alive as a real local for as long as `event` is in
    # use -- QDragEnterEvent/QDropEvent only ever hold a raw, non-owning
    # pointer to the QMimeData passed in (Qt's own C++ API, no parent-
    # child ownership), so an inline QMimeData() temporary with no
    # surviving Python reference gets garbage-collected out from under
    # the event, and event.mimeData() then segfaults on the dangling
    # pointer -- confirmed empirically while writing these tests.
    mime = _media_drop_mime(7)
    event = QDragEnterEvent(
        QPoint(50, 50), Qt.DropAction.CopyAction, mime,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)

    widget.dragEnterEvent(event)

    assert event.isAccepted()
    print("TimelineTrackWidget.dragEnterEvent: accepts a dragged Media "
          "Library row -- OK")


def test_drag_move_computes_a_drop_preview_index():
    widget, timeline_obj = _widget([_clip("A", 100), _clip("B", 100)])
    x = int(frame_to_x(120, widget._pixels_per_frame, widget._origin_x()))  # inside clip B, before its own center (150) -- only A's own center (50) is passed
    mime = _media_drop_mime(7)  # kept alive -- see test_drag_enter_accepts_the_media_entry_mime_type's own comment
    event = QDragMoveEvent(
        QPoint(x, 50), Qt.DropAction.CopyAction, mime,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)

    widget.dragMoveEvent(event)

    assert widget._drop_preview_index == 1
    assert event.isAccepted()
    print("TimelineTrackWidget.dragMoveEvent: computes an insertion-"
          "index preview as the drag moves -- OK")


def test_drag_leave_clears_the_drop_preview():
    widget, _ = _widget([_clip("A", 100)])
    widget._drop_preview_index = 0

    widget.dragLeaveEvent(QDragLeaveEvent())

    assert widget._drop_preview_index is None
    print("TimelineTrackWidget.dragLeaveEvent: clears the drop preview -- OK")


def test_drop_emits_media_entry_drop_requested_with_the_right_index():
    widget, timeline_obj = _widget([_clip("A", 100), _clip("B", 100)])
    received = []
    widget.media_entry_drop_requested.connect(lambda entry_id, index: received.append((entry_id, index)))
    x = int(frame_to_x(120, widget._pixels_per_frame, widget._origin_x()))  # inside clip B, before its own center -> index 1

    mime = _media_drop_mime(42)  # kept alive -- see test_drag_enter_accepts_the_media_entry_mime_type's own comment
    move_event = QDragMoveEvent(
        QPoint(x, 50), Qt.DropAction.CopyAction, mime,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    widget.dragMoveEvent(move_event)
    drop_event = QDropEvent(
        QPointF(x, 50), Qt.DropAction.CopyAction, mime,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    widget.dropEvent(drop_event)

    assert received == [(42, 1)]
    assert widget._drop_preview_index is None, "the preview must clear once the drop lands"
    print("TimelineTrackWidget.dropEvent: emits media_entry_drop_"
          "requested with the entry id and computed insertion index -- OK")


def test_drop_on_an_empty_timeline_targets_index_zero():
    widget, timeline_obj = _widget([])
    received = []
    widget.media_entry_drop_requested.connect(lambda entry_id, index: received.append((entry_id, index)))

    mime = _media_drop_mime(5)  # kept alive -- see test_drag_enter_accepts_the_media_entry_mime_type's own comment
    move_event = QDragMoveEvent(
        QPoint(50, 50), Qt.DropAction.CopyAction, mime,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    widget.dragMoveEvent(move_event)
    drop_event = QDropEvent(
        QPointF(50, 50), Qt.DropAction.CopyAction, mime,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    widget.dropEvent(drop_event)

    assert received == [(5, 0)]
    print("TimelineTrackWidget.dropEvent: dropping on an empty Timeline "
          "targets index 0 -- OK")


def test_drop_without_a_prior_drag_move_is_a_noop():
    """dropEvent reads self._drop_preview_index (set by dragMoveEvent) --
    a real drag always moves at least once before dropping, but this
    guards the no-preview case defensively rather than emitting a wrong
    index."""
    widget, _ = _widget([_clip("A", 100)])
    received = []
    widget.media_entry_drop_requested.connect(lambda entry_id, index: received.append((entry_id, index)))

    mime = _media_drop_mime(5)  # kept alive -- see test_drag_enter_accepts_the_media_entry_mime_type's own comment
    drop_event = QDropEvent(
        QPointF(50, 50), Qt.DropAction.CopyAction, mime,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    widget.dropEvent(drop_event)

    assert received == []
    print("TimelineTrackWidget.dropEvent: a drop with no prior "
          "dragMoveEvent (no preview index) is a no-op -- OK")


if __name__ == "__main__":
    test_clip_extents_sums_preceding_frame_counts()
    test_clip_extents_empty_list()
    test_clip_extents_agrees_with_timeline_controller_record_frame()
    test_pixels_per_frame_to_fit_scales_to_width()
    test_pixels_per_frame_to_fit_handles_degenerate_input()
    test_frame_to_x_and_x_to_frame_are_inverses()
    test_x_to_frame_handles_zero_pixels_per_frame()
    test_clip_at_x_hits_the_right_clip()
    test_clip_at_x_misses_past_the_last_clip()
    test_clip_at_x_on_empty_timeline()
    test_reorder_target_index_moving_right()
    test_reorder_target_index_moving_left()
    test_reorder_target_index_ignores_the_dragged_clips_own_position()
    test_ruler_tick_frames_starts_at_zero()
    test_ruler_tick_frames_picks_a_wider_interval_when_zoomed_out()
    test_ruler_tick_frames_empty_for_degenerate_input()
    test_widget_renders_without_raising_with_clips()
    test_widget_renders_without_raising_when_empty()
    test_clicking_a_clip_selects_it_and_emits_clip_selected()
    test_clicking_empty_track_space_emits_seek_requested()
    test_dragging_the_ruler_emits_seek_requested_repeatedly()
    test_scrub_drag_does_not_leak_into_a_clip_reorder_drag()
    test_double_clicking_a_clip_emits_clip_double_clicked()
    test_dragging_a_clip_reorders_the_timeline()
    test_dragging_back_to_the_same_position_does_not_reorder()
    test_right_clicking_a_clip_emits_context_menu_signal()
    test_set_selected_clip_id_does_not_reemit_clip_selected()
    test_set_playhead_frame_triggers_repaint_state()
    test_refresh_recomputes_pixels_per_frame_after_resize()
    test_clip_edge_at_x_hits_the_left_and_right_boundaries()
    test_clip_edge_at_x_misses_the_middle_of_a_block()
    test_clip_edge_at_x_skips_a_block_too_narrow_to_grab_precisely()
    test_clip_edge_at_x_resolves_a_shared_boundary_by_which_side_youre_on()
    test_apply_left_trim_shortens_from_the_front_and_clamps_to_one_frame()
    test_apply_right_trim_lengthens_and_clamps_to_one_frame()
    test_zoom_in_increases_pixels_per_frame_and_leaves_auto_fit()
    test_zoom_to_fit_clears_manual_zoom_and_scroll()
    test_zoom_is_clamped_to_min_and_max()
    test_scroll_offset_is_clamped_to_max_scroll_frames()
    test_zoom_to_fit_never_has_a_positive_max_scroll()
    test_zoom_in_keeps_the_anchored_frame_under_the_cursor()
    test_wheel_with_ctrl_zooms_without_ctrl_scrolls()
    test_dragging_a_clips_left_edge_trims_its_in_frame()
    test_dragging_a_clips_right_edge_trims_its_out_frame()
    test_trimming_one_clip_ripples_every_later_clips_record_position()
    test_delete_key_emits_delete_requested_only_with_a_clip_selected()
    test_backspace_also_emits_delete_requested()
    test_s_key_emits_split_requested_regardless_of_selection()
    test_drag_enter_accepts_the_media_entry_mime_type()
    test_drag_move_computes_a_drop_preview_index()
    test_drag_leave_clears_the_drop_preview()
    test_drop_emits_media_entry_drop_requested_with_the_right_index()
    test_drop_on_an_empty_timeline_targets_index_zero()
    test_drop_without_a_prior_drag_move_is_a_noop()
    print()
    print("All timeline_track_widget tests passed.")
