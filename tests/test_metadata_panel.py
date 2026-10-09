"""Tests for metadata_panel.py, the Media Libraries column improvements and
the newer media_probe fields -- run directly:
QT_QPA_PLATFORM=offscreen python3 -m tests.test_metadata_panel
"""
import sys
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem

import media_panel
import media_probe
import timeline as tl
from media_library import MediaEntry
from media_panel import COLUMN_KEYS, MediaPanel
from metadata_panel import MetadataPanel, entry_sections


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _entry(**metadata):
    return MediaEntry("F003C002_190925_MN99", "/fast/media/F003C002_190925_MN99.mxf",
                      source_colourspace="ARRI LogC3 (EI800)",
                      source_filename="F003C002_190925_MN99.mxf", media_kind="video",
                      width=1920, height=1080, frame_count=279, frame_rate=24.0,
                      duration=11.625, metadata=metadata)


def test_entry_sections_group_the_fields_and_leave_out_unknown_ones():
    sections = dict(entry_sections(_entry(
        reel="F003", clip="C002", codec="ProRes 4444", camera="ARRI ALEXA Mini LF",
        firmware="SUP 6.00.15", recorded="2019-09-25T09:56:02", start_timecode="00:38:16:12",
        colour_matrix="BT.709", audio_channels=5, audio_sample_rate=48000, audio_bit_depth=24)))
    assert list(sections) == ["Clip", "Timing", "Image", "Camera", "Audio", "File"]
    assert dict(sections["Clip"])["Reel"] == "F003"
    assert dict(sections["Timing"])["Start TC"] == "00:38:16:12"
    image = dict(sections["Image"])
    assert image["Matrix"] == "BT.709" and "Primaries" not in image, "undeclared tags are omitted"
    assert dict(sections["Camera"])["Recorded"] == "2019-09-25 09:56:02"
    assert dict(sections["Audio"]) == {"Channels": "5", "Sample Rate": "48 kHz", "Bit Depth": "24-bit"}
    bare = dict(entry_sections(_entry()))
    assert "Camera" not in bare and "Audio" not in bare, "empty sections are dropped"
    print("metadata panel: grouped sections, unknown fields omitted -- OK")


def test_panel_shows_a_hint_one_entry_a_timeline_or_a_count():
    _app()
    panel = MetadataPanel()
    assert panel.sections() == []
    panel.show_selection([_entry(camera="ARRI ALEXA Mini LF")], [])
    assert [title for title, _rows in panel.sections()][0] == "Clip"
    timeline = tl.Timeline(clips=[tl.TimelineClip("A", "/a.mxf", "video", 0, 47)], frame_rate=24.0)
    panel.show_selection([], [timeline])
    assert dict(panel.sections()[0][1])["Record Out"] == "00:00:01:23"
    panel.show_selection([_entry(), _entry()], [])
    assert panel.sections() == []  # "2 items selected."
    print("metadata panel: hint / entry / timeline / count -- OK")


def test_media_panel_selection_reaches_the_metadata_panel_from_the_grid():
    _app()
    media = MediaPanel()
    media.library.add_entry(_entry(reel="F003"))
    media._refresh_table()
    details = MetadataPanel()
    media.selection_changed.connect(lambda: details.show_selection(*media.selected_items()))
    media._select_table_rows([0])  # what a Grid click does
    assert dict(details.sections()[0][1])["Reel"] == "F003"
    print("metadata panel: follows a Grid selection -- OK")


def test_columns_defaults_empty_cells_fit_and_reread():
    _app()
    panel = MediaPanel()
    for key in ("reel", "start_tc", "camera", "name"):
        assert not panel.table.isColumnHidden(COLUMN_KEYS.index(key)), key
    assert panel.table.isColumnHidden(COLUMN_KEYS.index("kind"))
    entry = _entry()
    panel.library.add_entry(entry)
    panel._refresh_table()

    camera_index = panel.table.model().index(0, COLUMN_KEYS.index("camera"))
    option = QStyleOptionViewItem()
    panel.table.itemDelegate().initStyleOption(option, camera_index)
    assert option.text == "—", "an unknown value draws as a dash"
    assert panel.table.item(0, COLUMN_KEYS.index("camera")).text() == "", "...but the data stays empty"

    panel.fit_columns_to_contents()
    assert all(48 <= panel.table.columnWidth(i) <= 360 for i in range(1, len(COLUMN_KEYS))
               if not panel.table.isColumnHidden(i))

    original = media_panel._probe_or_empty
    media_panel._probe_or_empty = lambda path, is_video: {"camera": "ARRI ALEXA Mini LF", "reel": "F003"}
    try:
        panel.reread_metadata([entry])
    finally:
        media_panel._probe_or_empty = original
    assert panel.table.item(0, COLUMN_KEYS.index("camera")).text() == "ARRI ALEXA Mini LF"
    assert panel.table.item(0, COLUMN_KEYS.index("reel")).text() == "F003"
    print("media columns: defaults, dash for unknown, bounded fit, re-read metadata -- OK")


def test_first_import_fits_columns_but_later_ones_keep_user_widths():
    _app()
    panel = MediaPanel()
    fitted = []
    panel.fit_columns_to_contents = lambda: fitted.append(1)
    panel._build_entry = lambda path: _entry()
    panel.import_paths(["/m/a.mxf"])
    panel.import_paths(["/m/b.mxf"])
    assert fitted == [1], "only the first import into an empty library fits"
    print("media columns: first import fits, later imports keep widths -- OK")


def test_probe_field_helpers():
    assert media_probe._reel_and_clip(None, "/x/F003C002_190925_MN99.mxf") == {"reel": "F003", "clip": "C002"}
    assert media_probe._reel_and_clip(None, "A001_C003_0101AB.mov") == {"reel": "A001", "clip": "C003"}
    assert media_probe._reel_and_clip("ROLL7", "B005C011.mov") == {"reel": "ROLL7", "clip": "C011"}
    assert media_probe._reel_and_clip(None, "holiday.mov") == {}
    assert media_probe._iso_date("2019-09-25T09:56:02.000000Z") == "2019-09-25T09:56:02"
    assert media_probe._iso_date("2019:09:25 09:56:02") == "2019-09-25T09:56:02"
    cc = SimpleNamespace(color_primaries=9, color_trc=16, colorspace=2, name="prores")
    assert media_probe._colour_tags(cc) == {"colour_primaries": "BT.2020", "colour_transfer": "PQ"}
    assert media_probe._colour_tags(SimpleNamespace(color_primaries=2, color_trc=2, colorspace=2)) == {}
    assert media_probe._audio_bit_depth(SimpleNamespace(name="pcm_s24le")) == 24
    assert media_probe._audio_bit_depth(SimpleNamespace(name="aac")) is None
    print("media_probe: reel/clip, dates, colour tags, audio bits -- OK")


if __name__ == "__main__":
    test_entry_sections_group_the_fields_and_leave_out_unknown_ones()
    test_panel_shows_a_hint_one_entry_a_timeline_or_a_count()
    test_media_panel_selection_reaches_the_metadata_panel_from_the_grid()
    test_columns_defaults_empty_cells_fit_and_reread()
    test_first_import_fits_columns_but_later_ones_keep_user_widths()
    test_probe_field_helpers()
    print("\nAll metadata panel tests passed.")
