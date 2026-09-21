"""Tests for timeline_panel.py -- run directly:
QT_QPA_PLATFORM=offscreen python3 test_timeline_panel.py

First real test coverage for this module (see its own module docstring's
long-standing "NOT TESTED IN THIS ENVIRONMENT" note) -- added alongside
add_clip_with_range (the marked-Source-range counterpart to
add_clip_from_library_entry, per Tom: "will be necessary to be able to
mark desired ranges and add to sequence").
"""
import sys

from PySide6.QtWidgets import QApplication

from media_library import MediaEntry
from timeline_panel import TimelinePanel, DEFAULT_STILL_HOLD_SECONDS


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _panel():
    _app()
    return TimelinePanel()


def test_add_clip_from_library_entry_video_uses_the_full_frame_range():
    panel = _panel()
    entry = MediaEntry(
        "Clip", "/tmp/clip.mp4", media_kind="video", frame_count=48,
        thumbnail={"mode": "embedded", "data_b64": "Zm9v"})
    panel.add_clip_from_library_entry(entry)

    assert len(panel.timeline.clips) == 1
    clip = panel.timeline.clips[0]
    assert clip.name == "Clip"
    assert clip.source_path == "/tmp/clip.mp4"
    assert clip.media_kind == "video"
    assert (clip.in_frame, clip.out_frame) == (0, 47)
    assert clip.thumbnail == entry.thumbnail
    print("TimelinePanel.add_clip_from_library_entry: a video entry "
          "adds a clip covering its full frame range -- OK")


def test_add_clip_from_library_entry_still_uses_the_default_hold_duration():
    panel = _panel()
    entry = MediaEntry("Still", "/tmp/still.png", media_kind="still")
    panel.add_clip_from_library_entry(entry)

    clip = panel.timeline.clips[0]
    expected_out = max(0, int(round(DEFAULT_STILL_HOLD_SECONDS * panel.timeline.frame_rate)) - 1)
    assert (clip.in_frame, clip.out_frame) == (0, expected_out)
    print("TimelinePanel.add_clip_from_library_entry: a still entry "
          "adds a clip held for DEFAULT_STILL_HOLD_SECONDS -- OK")


def test_add_clip_with_range_covers_exactly_the_given_range():
    """The new method, per direct feedback: "will be necessary to be
    able to mark desired ranges and add to sequence" -- unlike
    add_clip_from_library_entry, this must NOT default to the full
    source range; it uses exactly what the caller (SampleImagePanel's
    marked In/Out) hands it."""
    panel = _panel()
    panel.add_clip_with_range("Marked Range", "/tmp/src.mp4", "video", 12, 40)

    assert len(panel.timeline.clips) == 1
    clip = panel.timeline.clips[0]
    assert clip.name == "Marked Range"
    assert clip.source_path == "/tmp/src.mp4"
    assert clip.media_kind == "video"
    assert (clip.in_frame, clip.out_frame) == (12, 40)
    assert clip.thumbnail is None
    print("TimelinePanel.add_clip_with_range: adds a clip covering "
          "exactly the given [in_frame, out_frame] -- OK")


def test_add_clip_with_range_appends_after_existing_clips():
    panel = _panel()
    panel.add_clip_with_range("First", "/tmp/a.mp4", "video", 0, 9)
    panel.add_clip_with_range("Second", "/tmp/b.mp4", "video", 5, 15)

    assert len(panel.timeline.clips) == 2
    assert panel.timeline.clips[0].name == "First"
    assert panel.timeline.clips[1].name == "Second"
    assert panel.get_selected_clip_index() == 1, "the newly-added clip should be selected"
    print("TimelinePanel.add_clip_with_range: appends after existing "
          "clips and selects the new row -- OK")


def test_add_clip_with_range_fires_timeline_changed():
    panel = _panel()
    fired = []
    panel.timeline_changed.connect(lambda: fired.append(True))
    panel.add_clip_with_range("Clip", "/tmp/a.mp4", "video", 0, 9)
    assert fired, "add_clip_with_range must go through _refresh_list, which fires timeline_changed"
    print("TimelinePanel.add_clip_with_range: fires timeline_changed "
          "(so the Viewer's transport bar shows up) -- OK")


def test_clicking_a_clip_block_relays_clip_selected():
    """The widget's own clip_selected (a real click, not a keep_selection
    sync) must reach the panel's external clip_selected exactly as-is --
    see _on_widget_clip_selected."""
    panel = _panel()
    panel.add_clip_with_range("First", "/tmp/a.mp4", "video", 0, 9)
    panel.add_clip_with_range("Second", "/tmp/b.mp4", "video", 0, 9)
    # add_clip_with_range already selects the newly-added ("Second") clip
    # via keep_selection -- select "First" to get an actual, real change.
    received = []
    panel.clip_selected.connect(lambda clip: received.append(clip))

    panel.track_widget._set_selected(panel.timeline.clips[0])

    assert received == [panel.timeline.clips[0]]
    print("TimelinePanel: a widget-driven selection relays through "
          "clip_selected -- OK")


def test_removing_the_selected_clip_emits_clip_selected_none():
    panel = _panel()
    panel.add_clip_with_range("Clip", "/tmp/a.mp4", "video", 0, 9)
    panel.track_widget._set_selected(panel.timeline.clips[0])
    received = []
    panel.clip_selected.connect(lambda clip: received.append(clip))

    clip = panel.timeline.clips[0]
    panel.timeline.remove_clip(clip.id)
    panel._refresh_list()

    assert received == [None]
    assert panel.get_selected_clip() is None
    print("TimelinePanel: removing the selected clip clears selection "
          "and fires clip_selected(None) -- OK")


def test_seek_requested_relays_from_the_track_widget():
    panel = _panel()
    received = []
    panel.seek_requested.connect(lambda frame: received.append(frame))

    panel.track_widget.seek_requested.emit(42)

    assert received == [42]
    print("TimelinePanel: seek_requested relays straight from "
          "TimelineTrackWidget.seek_requested -- OK")


def test_reordering_via_drag_fires_timeline_changed_not_clip_selected():
    panel = _panel()
    panel.add_clip_with_range("A", "/tmp/a.mp4", "video", 0, 9)
    panel.add_clip_with_range("B", "/tmp/b.mp4", "video", 0, 9)
    a_id = panel.timeline.clips[0].id
    changed = []
    selected = []
    panel.timeline_changed.connect(lambda: changed.append(True))
    panel.clip_selected.connect(lambda clip: selected.append(clip))

    panel.timeline.move_clip(a_id, 1)
    panel._on_clips_reordered()

    assert changed == [True]
    assert selected == [], "a reorder shouldn't touch selection"
    assert panel.timeline.clips[-1].id == a_id
    print("TimelinePanel: _on_clips_reordered propagates timeline_changed "
          "without touching selection -- OK")


def test_edl_subpanel_starts_collapsed():
    panel = _panel()
    assert not panel.edl_toggle_button.isChecked()
    # isVisible() would be False here regardless (the panel itself is
    # never .show()n in these offscreen tests) -- isHidden() reflects
    # this specific widget's own explicit setVisible() flag, unaffected
    # by whether any ancestor window was ever shown.
    assert panel.edl_text_view.isHidden()
    print("TimelinePanel: the EDL sub-panel starts collapsed -- OK")


def test_edl_toggle_button_expands_and_collapses_the_text_view():
    panel = _panel()
    panel.edl_toggle_button.setChecked(True)
    assert not panel.edl_text_view.isHidden()
    assert panel.edl_toggle_button.text().startswith("▾")

    panel.edl_toggle_button.setChecked(False)
    assert panel.edl_text_view.isHidden()
    assert panel.edl_toggle_button.text().startswith("▸")
    print("TimelinePanel: the EDL toggle button shows/hides the text "
          "view and flips its own arrow -- OK")


def test_edl_text_reflects_the_current_clip_list_even_while_collapsed():
    panel = _panel()
    panel.add_clip_with_range("A", "/tmp/a.mp4", "video", 0, 9)
    panel.add_clip_with_range("B", "/tmp/b.mp4", "video", 0, 4)

    text = panel.edl_text_view.toPlainText()
    assert "* FROM CLIP NAME: A" in text
    assert "* FROM CLIP NAME: B" in text
    print("TimelinePanel: the EDL text view stays current on every "
          "mutation, even while the sub-panel is collapsed -- OK")


def test_edl_text_reflects_a_drag_reorder():
    panel = _panel()
    panel.add_clip_with_range("A", "/tmp/a.mp4", "video", 0, 9)
    panel.add_clip_with_range("B", "/tmp/b.mp4", "video", 0, 4)
    a_id = panel.timeline.clips[0].id

    panel.timeline.move_clip(a_id, 1)
    panel._on_clips_reordered()

    text = panel.edl_text_view.toPlainText()
    assert text.index("* FROM CLIP NAME: B") < text.index("* FROM CLIP NAME: A"), (
        "the EDL's own event order must follow the reordered Timeline")
    print("TimelinePanel: the EDL text view reflects a drag-reorder -- OK")


def test_refresh_playhead_pulls_from_the_configured_getter():
    panel = _panel()
    panel.add_clip_with_range("Clip", "/tmp/a.mp4", "video", 0, 99)
    panel.set_record_frame_getter(lambda: 33)

    panel.refresh_playhead()

    assert panel.track_widget.playhead_frame == 33
    print("TimelinePanel.refresh_playhead: pulls the current record "
          "frame from the configured getter into the track widget -- OK")


if __name__ == "__main__":
    test_add_clip_from_library_entry_video_uses_the_full_frame_range()
    test_add_clip_from_library_entry_still_uses_the_default_hold_duration()
    test_add_clip_with_range_covers_exactly_the_given_range()
    test_add_clip_with_range_appends_after_existing_clips()
    test_add_clip_with_range_fires_timeline_changed()
    test_clicking_a_clip_block_relays_clip_selected()
    test_removing_the_selected_clip_emits_clip_selected_none()
    test_seek_requested_relays_from_the_track_widget()
    test_reordering_via_drag_fires_timeline_changed_not_clip_selected()
    test_edl_subpanel_starts_collapsed()
    test_edl_toggle_button_expands_and_collapses_the_text_view()
    test_edl_text_reflects_the_current_clip_list_even_while_collapsed()
    test_edl_text_reflects_a_drag_reorder()
    test_refresh_playhead_pulls_from_the_configured_getter()
    print()
    print("All timeline_panel tests passed.")
