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
    assert panel.list_widget.currentRow() == 1, "the newly-added clip should be selected"
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


if __name__ == "__main__":
    test_add_clip_from_library_entry_video_uses_the_full_frame_range()
    test_add_clip_from_library_entry_still_uses_the_default_hold_duration()
    test_add_clip_with_range_covers_exactly_the_given_range()
    test_add_clip_with_range_appends_after_existing_clips()
    test_add_clip_with_range_fires_timeline_changed()
    print()
    print("All timeline_panel tests passed.")
