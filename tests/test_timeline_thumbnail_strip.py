"""Tests for timeline_thumbnail_strip.py and the Timeline panel's
Timeline | Thumbnails modes -- run directly:
QT_QPA_PLATFORM=offscreen python3 -m tests.test_timeline_thumbnail_strip
"""
import sys

from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import stack as st
import timeline as tl
from timeline_panel import THUMBNAILS_VIEW, TIMELINE_VIEW, TimelinePanel
from timeline_thumbnail_strip import (
    CARD_GAP, CARD_WIDTH, MARGIN, TimelineThumbnailStrip, _has_own_grade)


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _timeline():
    graded = tl.TimelineClip("B002", "/media/b.mxf", "video", 0, 47,
                             stack=st.Stack(stages=[st.make_cdl_stage(slope=(1.1, 1.0, 1.0))]))
    return tl.Timeline(clips=[
        tl.TimelineClip("A001", "/media/a.mxf", "video", 0, 23),
        graded,
        tl.TimelineClip("C003", "/media/c.mxf", "video", 10, 19),
    ], frame_rate=24.0)


def test_strip_sizes_to_its_cards_and_paints_without_a_timeline():
    _app()
    strip = TimelineThumbnailStrip()
    strip.grab()  # empty state paints ("No timeline loaded")
    strip.set_timeline(_timeline())
    assert strip.minimumWidth() == MARGIN * 2 + 3 * CARD_WIDTH + 2 * CARD_GAP
    strip.resize(strip.minimumWidth(), 160)
    assert not strip.grab().isNull()
    print("thumbnail strip: sizes to 3 cards, paints empty and full -- OK")


def test_clicking_a_card_selects_it_and_seeks_to_its_first_frame():
    _app()
    timeline = _timeline()
    strip = TimelineThumbnailStrip(timeline)
    strip.resize(strip.minimumWidth(), 160)
    selected, seeks = [], []
    strip.clip_selected.connect(selected.append)
    strip.seek_requested.connect(seeks.append)
    QTest.mouseClick(strip, Qt.MouseButton.LeftButton, pos=strip.card_rect(1).center().toPoint())
    assert [c.name for c in selected] == ["B002"]
    assert seeks == [24]  # A001 is frames 0-23, so B002 starts at 24
    assert strip.selected_clip_id == timeline.clips[1].id
    QTest.mouseClick(strip, Qt.MouseButton.LeftButton, pos=QPointF(1, 1).toPoint())  # margin
    assert len(selected) == 1, "a click between cards selects nothing"
    print("thumbnail strip: click selects the card and seeks to its start -- OK")


def test_current_clip_follows_the_playhead_and_grade_dot_reflects_the_stack():
    _app()
    timeline = _timeline()
    strip = TimelineThumbnailStrip(timeline)
    assert strip.current_clip_index() is None
    for frame, expected in ((0, 0), (23, 0), (24, 1), (71, 1), (72, 2), (81, 2), (82, None)):
        strip.set_playhead_frame(frame)
        assert strip.current_clip_index() == expected, (frame, expected)
    assert [_has_own_grade(c) for c in timeline.clips] == [False, True, False]
    timeline.clips[1].stack.stages[0].enabled = False
    assert not _has_own_grade(timeline.clips[1]), "a disabled stage is not a grade"
    print("thumbnail strip: current clip tracks the playhead; grade dot = enabled stage -- OK")


def test_timeline_panel_defaults_to_thumbnails_and_shares_selection_across_views():
    _app()
    panel = TimelinePanel()
    timeline = _timeline()
    panel.load_timeline(timeline)
    assert panel.view_mode() == THUMBNAILS_VIEW
    assert panel.zoom_in_button.isHidden(), "zoom is a timeline-view control"

    reported = []
    panel.clip_selected.connect(reported.append)
    panel.thumbnail_strip.resize(panel.thumbnail_strip.minimumWidth(), 160)
    QTest.mouseClick(panel.thumbnail_strip, Qt.MouseButton.LeftButton,
                     pos=panel.thumbnail_strip.card_rect(2).center().toPoint())
    assert [c.name for c in reported] == ["C003"]
    assert panel.track_widget.selected_clip_id == timeline.clips[2].id

    panel.set_view_mode(TIMELINE_VIEW)
    assert panel.view_stack.currentWidget() is panel.track_widget
    assert not panel.zoom_in_button.isHidden()
    panel.track_widget.clip_selected.emit(timeline.clips[0])
    assert panel.thumbnail_strip.selected_clip_id == timeline.clips[0].id
    print("timeline panel: Thumbnails by default, one selection across both views -- OK")


if __name__ == "__main__":
    test_strip_sizes_to_its_cards_and_paints_without_a_timeline()
    test_clicking_a_card_selects_it_and_seeks_to_its_first_frame()
    test_current_clip_follows_the_playhead_and_grade_dot_reflects_the_stack()
    test_timeline_panel_defaults_to_thumbnails_and_shares_selection_across_views()
    print("\nAll timeline thumbnail strip tests passed.")
