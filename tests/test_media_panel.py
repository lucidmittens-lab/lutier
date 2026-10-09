"""Tests for media_panel.py -- run directly:
QT_QPA_PLATFORM=offscreen python3 -m tests.test_media_panel
"""
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMenu

import timeline as tl
from media_library import MediaEntry, MEDIA_ENTRY_DRAG_MIME_TYPE
import media_panel
from media_panel import MediaPanel, COLUMN_NAME, COLUMN_THUMBNAIL, COLUMN_KEYS, COLUMNS


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_hover_info_includes_start_and_end_timecode_for_video():
    _app()
    panel = MediaPanel()
    entry = MediaEntry(
        "Clip", "/tmp/clip.mp4", media_kind="video",
        frame_count=48, frame_rate=24.0, duration=2.0)
    panel.library.add_entry(entry)
    panel._refresh_table()
    item = panel.table.item(0, 0)

    info = panel._hover_info_for_item(item)
    assert "Start: 00:00:00:00" in info
    assert "End: 00:00:01:23" in info
    print("MediaPanel._hover_info_for_item: shows start/end "
          "timecode for a video entry -- OK")


def test_hover_info_omits_timecode_for_a_still():
    _app()
    panel = MediaPanel()
    entry = MediaEntry("Still", "/tmp/still.png", media_kind="still")
    panel.library.add_entry(entry)
    panel._refresh_table()
    item = panel.table.item(0, 0)

    info = panel._hover_info_for_item(item)
    assert "Start:" not in info
    assert "End:" not in info
    print("MediaPanel._hover_info_for_item: no timecode shown "
          "for a still entry -- OK")


def test_hover_info_tolerates_missing_frame_rate():
    _app()
    panel = MediaPanel()
    entry = MediaEntry(
        "Weird Clip", "/tmp/weird.mp4", media_kind="video",
        frame_count=10, frame_rate=None, duration=1.0)
    panel.library.add_entry(entry)
    panel._refresh_table()
    item = panel.table.item(0, 0)

    info = panel._hover_info_for_item(item)  # must not raise
    assert "Start:" not in info
    print("MediaPanel._hover_info_for_item: doesn't crash or show "
          "timecode when frame_rate is missing -- OK")


def test_table_accepts_custom_context_menu():
    _app()
    panel = MediaPanel()
    assert panel.table.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
    print("MediaPanel.table: uses a custom (right-click) "
          "context menu policy -- OK")


def test_populate_menu_only_has_import_not_per_item_actions():
    """Per Tom: "rename, load as source, delete and load as ref should
    move to right clicking a particular clip" -- the "..." header menu
    (populate_menu) should keep only the panel-level Import... action;
    the four per-item actions moved to _on_context_menu_requested."""
    _app()
    panel = MediaPanel()
    menu = QMenu()
    panel.populate_menu(menu)
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert texts == ["Import...", "New Timeline", "New Library..."]
    print("MediaPanel.populate_menu: panel-level actions only (Import, New "
          "Timeline, New Library) -- per-item actions are on the context menu -- OK")


def test_context_menu_selects_the_clicked_row_before_popping_up():
    """Can't drive QMenu.exec() itself in an offscreen test (it blocks
    waiting for real user interaction) -- this checks the selection side
    effect _on_context_menu_requested does BEFORE popping the menu, which
    is the part that actually depends on "which row was right-clicked."""
    _app()
    panel = MediaPanel()
    entry_a = MediaEntry("A", "/tmp/a.png", media_kind="still")
    entry_b = MediaEntry("B", "/tmp/b.png", media_kind="still")
    panel.library.add_entry(entry_a)
    panel.library.add_entry(entry_b)
    panel._refresh_table()
    assert panel.table.currentRow() != 1

    panel.table.selectRow(1)  # mirrors what _on_context_menu_requested does
    assert panel._selected_entry() is entry_b
    print("MediaPanel: right-clicking a row selects it, so the "
          "context menu's actions target the right entry -- OK")


def test_selection_survives_sorting_by_a_different_row_order():
    """The whole reason _selected_entry()/_entry_id_at_row() resolve
    through an id stored on the row rather than a raw list index (see
    media_panel.py's own module docstring, "SORTING AND ROW IDENTITY") --
    with sorting enabled, the table's row order no longer matches
    self.library.entries' order. Sort by Name descending ("B" before "A")
    and confirm selecting what's now row 0 still resolves to entry_b, not
    whatever happened to be library.entries[0]."""
    _app()
    panel = MediaPanel()
    entry_a = panel.library.add_entry(MediaEntry("A", "/tmp/a.png"))
    entry_b = panel.library.add_entry(MediaEntry("B", "/tmp/b.png"))
    panel._refresh_table()
    assert panel.library.entries[0] is entry_a  # insertion order, unaffected by sorting

    panel.table.sortItems(COLUMN_NAME, Qt.SortOrder.DescendingOrder)
    panel.table.selectRow(0)
    selected = panel._selected_entry()
    assert selected is entry_b, (
        f"expected the DESCENDING-sorted row 0 to resolve to entry_b, got {selected!r}")
    print("MediaPanel: selection resolves through the row's own stored "
          "entry id, staying correct after the table is sorted -- OK")


def test_keep_selection_id_survives_refresh_after_rename():
    """_on_rename_clicked calls _refresh_table(keep_selection_id=entry.id)
    -- confirms that actually re-selects the renamed row rather than
    losing the selection (e.g. to whatever row 0 now is)."""
    _app()
    panel = MediaPanel()
    entry_a = panel.library.add_entry(MediaEntry("A", "/tmp/a.png"))
    entry_b = panel.library.add_entry(MediaEntry("B", "/tmp/b.png"))
    panel._refresh_table()
    panel.library.rename(entry_b.id, "Z-Renamed")
    panel._refresh_table(keep_selection_id=entry_b.id)
    assert panel._selected_entry() is entry_b
    print("MediaPanel._refresh_table: keep_selection_id re-selects the "
          "right row after a library change -- OK")


def test_table_allows_extended_multi_selection():
    _app()
    panel = MediaPanel()
    assert panel.table.selectionMode() == panel.table.SelectionMode.ExtendedSelection
    print("MediaPanel.table: allows Ctrl/Shift-click multi-selection "
          "(per Tom: \"make it more similar to a typical NLE\") -- OK")


def test_selected_entries_returns_every_selected_row_in_table_order():
    _app()
    panel = MediaPanel()
    entry_a = panel.library.add_entry(MediaEntry("A", "/tmp/a.png"))
    entry_b = panel.library.add_entry(MediaEntry("B", "/tmp/b.png"))
    entry_c = panel.library.add_entry(MediaEntry("C", "/tmp/c.png"))
    panel._refresh_table()

    # selectRow() always REPLACES the current selection regardless of
    # selection mode (that's how QTableWidget's own API works) -- a real
    # multi-row selection (what a Ctrl-click produces) has to go through
    # the selection model directly instead.
    selection_model = panel.table.selectionModel()
    selection_model.clearSelection()
    for row in (0, 2):
        selection_model.select(
            panel.table.model().index(row, 0),
            selection_model.SelectionFlag.Select | selection_model.SelectionFlag.Rows)

    entries = panel._selected_entries()

    assert entries == [entry_a, entry_c]
    print("MediaPanel._selected_entries: every selected row, in table "
          "order -- OK")


def test_delete_removes_every_selected_entry():
    _app()
    panel = MediaPanel()
    entry_a = panel.library.add_entry(MediaEntry("A", "/tmp/a.png"))
    entry_b = panel.library.add_entry(MediaEntry("B", "/tmp/b.png"))
    panel.library.add_entry(MediaEntry("C", "/tmp/c.png"))
    panel._refresh_table()
    selection_model = panel.table.selectionModel()
    for row in (0, 1):
        selection_model.select(
            panel.table.model().index(row, 0),
            selection_model.SelectionFlag.Select | selection_model.SelectionFlag.Rows)
    from PySide6.QtWidgets import QMessageBox
    original_question = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    try:
        panel._on_delete_clicked()
    finally:
        QMessageBox.question = original_question

    assert [e.name for e in panel.library.entries] == ["C"]
    print("MediaPanel._on_delete_clicked: removes every selected entry, "
          "not just the current row -- OK")


def test_add_to_timeline_emits_once_per_selected_entry():
    _app()
    panel = MediaPanel()
    entry_a = panel.library.add_entry(MediaEntry("A", "/tmp/a.png"))
    entry_b = panel.library.add_entry(MediaEntry("B", "/tmp/b.png"))
    panel._refresh_table()
    selection_model = panel.table.selectionModel()
    for row in (0, 1):
        selection_model.select(
            panel.table.model().index(row, 0),
            selection_model.SelectionFlag.Select | selection_model.SelectionFlag.Rows)
    received = []
    panel.add_to_timeline_requested.connect(lambda entry: received.append(entry))

    panel._on_add_to_timeline_clicked()

    assert received == [entry_a, entry_b]
    print("MediaPanel._on_add_to_timeline_clicked: emits once per "
          "selected entry, in table order -- OK")


def test_right_click_on_an_already_selected_row_keeps_the_multi_selection():
    _app()
    panel = MediaPanel()
    panel.library.add_entry(MediaEntry("A", "/tmp/a.png"))
    panel.library.add_entry(MediaEntry("B", "/tmp/b.png"))
    panel._refresh_table()
    selection_model = panel.table.selectionModel()
    for row in (0, 1):
        selection_model.select(
            panel.table.model().index(row, 0),
            selection_model.SelectionFlag.Select | selection_model.SelectionFlag.Rows)
    assert len(panel._selected_entries()) == 2

    # Right-clicking row 0, which is ALREADY part of the selection, must
    # not collapse it back down to just row 0. Monkeypatching the real
    # QMenu.exec() doesn't reliably take (it's a shiboken-wrapped C++
    # method) and really popping one up blocks/warns under offscreen --
    # swap in a trivial stand-in for the module's own `QMenu` name
    # instead, same "patch the name the code under test looks up, not
    # the real class" approach as the QMessageBox.question patches
    # elsewhere in this file.
    import media_panel as media_panel_module

    class _FakeAction:
        def __getattr__(self, _name):  # setCheckable, setChecked, ...
            return lambda *a, **k: None

    class _FakeMenu:
        def __init__(self, *a, **k):
            pass

        def addAction(self, *a, **k):
            return _FakeAction()

        def addMenu(self, *a, **k):
            return _FakeMenu()

        def addSeparator(self):
            pass

        def exec(self, *a, **k):
            pass

    item = panel.table.item(0, COLUMN_THUMBNAIL)
    pos = panel.table.visualItemRect(item).center()
    original_menu_cls = media_panel_module.QMenu
    media_panel_module.QMenu = _FakeMenu
    try:
        panel._on_context_menu_requested(pos)
    finally:
        media_panel_module.QMenu = original_menu_cls

    assert len(panel._selected_entries()) == 2
    print("MediaPanel._on_context_menu_requested: right-clicking an "
          "already-selected row keeps the whole multi-selection -- OK")


def test_media_table_mime_data_carries_the_first_selected_entry_id():
    _app()
    panel = MediaPanel()
    entry = panel.library.add_entry(MediaEntry("A", "/tmp/a.png"))
    panel._refresh_table()
    panel.table.selectRow(0)

    mime = panel.table.mimeData(panel.table.selectedItems())

    assert mime is not None
    assert mime.hasFormat(MEDIA_ENTRY_DRAG_MIME_TYPE)
    assert bytes(mime.data(MEDIA_ENTRY_DRAG_MIME_TYPE)).decode("utf-8") == str(entry.id)
    print("MediaPanel._MediaTable.mimeData: carries the selected row's "
          "own entry id in the drag MIME type -- OK")


def test_filter_hides_non_matching_rows_by_name_or_path():
    _app()
    panel = MediaPanel()
    panel.library.add_entry(MediaEntry("Sunset Shot", "/footage/a.mov"))
    panel.library.add_entry(MediaEntry("Interior", "/footage/sunset_hallway.mov"))
    panel.library.add_entry(MediaEntry("Unrelated", "/footage/other.mov"))
    panel._refresh_table()

    panel.filter_edit.setText("sunset")

    hidden = [panel.table.isRowHidden(row) for row in range(panel.table.rowCount())]
    # Both "Sunset Shot" (matches by name) and "Interior" (matches by
    # path) must stay visible; "Unrelated" must be hidden.
    assert hidden.count(True) == 1
    visible_names = {
        panel.table.item(row, COLUMN_NAME).text()
        for row in range(panel.table.rowCount()) if not panel.table.isRowHidden(row)
    }
    assert visible_names == {"Sunset Shot", "Interior"}
    print("MediaPanel: the filter box hides rows that match neither "
          "name nor path -- OK")


def test_clearing_the_filter_shows_every_row_again():
    _app()
    panel = MediaPanel()
    panel.library.add_entry(MediaEntry("A", "/tmp/a.png"))
    panel.library.add_entry(MediaEntry("B", "/tmp/b.png"))
    panel._refresh_table()
    panel.filter_edit.setText("a")
    assert any(panel.table.isRowHidden(row) for row in range(panel.table.rowCount()))

    panel.filter_edit.setText("")

    assert not any(panel.table.isRowHidden(row) for row in range(panel.table.rowCount()))
    print("MediaPanel: clearing the filter box shows every row again -- OK")


def test_filter_survives_a_refresh_after_import():
    """_refresh_table re-applies the current filter text (see its own
    call to _apply_filter) -- without that, importing/renaming/deleting
    anything would silently reset an active filter."""
    _app()
    panel = MediaPanel()
    panel.library.add_entry(MediaEntry("Keep", "/tmp/keep.png"))
    panel._refresh_table()
    panel.filter_edit.setText("keep")

    panel.library.add_entry(MediaEntry("Drop", "/tmp/drop.png"))
    panel._refresh_table()

    hidden_names = {
        panel.table.item(row, COLUMN_NAME).text()
        for row in range(panel.table.rowCount()) if panel.table.isRowHidden(row)
    }
    assert hidden_names == {"Drop"}
    print("MediaPanel: an active filter survives _refresh_table (e.g. "
          "after Import) -- OK")


def _arri_like_entry():
    return MediaEntry(
        "A001C004", "/media/A001C004.mxf", media_kind="video", width=4448, height=3096,
        frame_count=48, frame_rate=24.0, duration=2.0,
        metadata={"start_timecode": "00:42:45:19", "codec": "ProRes 4444", "bit_depth": 12,
                  "chroma": "4:4:4", "has_alpha": False, "audio_channels": 5,
                  "audio_sample_rate": 48000, "camera": "ARRI ALEXA Mini LF",
                  "format": "MXF", "file_size": 1106604116})


def _cell_text(panel, row, key):
    return panel.table.item(row, COLUMN_KEYS.index(key)).text()


def test_probed_columns_show_formatted_metadata():
    _app()
    panel = MediaPanel()
    panel.library.add_entry(_arri_like_entry())
    panel._refresh_table()
    expected = {
        "start_tc": "00:42:45:19", "end_tc": "00:42:47:18", "frames": "48", "fps": "24",
        "codec": "ProRes 4444", "bit_depth": "12-bit", "chroma": "4:4:4",
        "audio": "5 ch, 48 kHz", "camera": "ARRI ALEXA Mini LF", "format": "MXF",
        "size": "1.0 GB", "filename": "A001C004.mxf",
    }
    for key, text in expected.items():
        assert _cell_text(panel, 0, key) == text, (key, _cell_text(panel, 0, key))
    print("MediaPanel: probed metadata columns show formatted values -- OK")


def test_hover_info_uses_the_embedded_start_timecode():
    _app()
    panel = MediaPanel()
    panel.library.add_entry(_arri_like_entry())
    panel._refresh_table()
    info = panel._hover_info_for_item(panel.table.item(0, 0))
    assert "Start: 00:42:45:19  End: 00:42:47:18" in info, info
    assert "ProRes 4444 · 12-bit · 4:4:4 · 5 ch, 48 kHz" in info, info
    print("MediaPanel: hover card uses the clip's embedded start timecode -- OK")


def test_start_tc_sorts_by_frame_not_text():
    _app()
    panel = MediaPanel()
    for name, tc in (("late", "10:00:00:00"), ("early", "09:59:59:23"), ("none", None)):
        metadata = {"start_timecode": tc} if tc else {}
        panel.library.add_entry(MediaEntry(
            name, f"/tmp/{name}.mov", media_kind="video", frame_count=24,
            frame_rate=24.0, duration=1.0, metadata=metadata))
    panel._refresh_table()
    panel.table.sortItems(COLUMN_KEYS.index("start_tc"), Qt.SortOrder.AscendingOrder)
    names = [panel.table.item(r, COLUMN_NAME).text() for r in range(3)]
    assert names == ["none", "early", "late"], names
    print("MediaPanel: Start TC sorts by frame number -- OK")


def test_default_column_visibility_follows_columns_spec():
    _app()
    panel = MediaPanel()
    for index, column in enumerate(COLUMNS):
        assert panel.table.isColumnHidden(index) == (not column.visible), column.key
    print("MediaPanel: columns start shown/hidden per COLUMNS -- OK")


def test_header_state_round_trips_order_visibility_and_width():
    _app()
    panel = MediaPanel()
    header = panel.table.horizontalHeader()
    codec = COLUMN_KEYS.index("codec")
    camera = COLUMN_KEYS.index("camera")
    header.moveSection(header.visualIndex(codec), 1)
    panel.table.setColumnHidden(camera, False)
    panel.table.setColumnHidden(COLUMN_KEYS.index("path"), True)
    panel.table.setColumnWidth(camera, 222)
    state = panel.get_header_state()

    restored = MediaPanel()
    restored.restore_header_state(state)
    assert restored.get_header_state() == state
    assert restored.table.horizontalHeader().visualIndex(codec) == 1
    assert not restored.table.isColumnHidden(camera)
    assert restored.table.columnWidth(camera) == 222
    print("MediaPanel: header order/visibility/width survive "
          "get_header_state -> restore_header_state -- OK")


def test_restoring_an_older_header_state_keeps_new_columns_at_their_defaults():
    _app()
    panel = MediaPanel()
    # Saved before "camera" (default hidden) and "fps" (default shown)
    # existed, and naming a column that has since been removed.
    old_keys = [k for k in COLUMN_KEYS if k not in ("camera", "fps")] + ["removed_column"]
    panel.restore_header_state({"version": media_panel.HEADER_STATE_VERSION, "order": old_keys,
                                "hidden": ["kind", "removed_column"], "widths": {}})
    assert panel.table.isColumnHidden(COLUMN_KEYS.index("kind"))
    assert panel.table.isColumnHidden(COLUMN_KEYS.index("camera"))
    assert not panel.table.isColumnHidden(COLUMN_KEYS.index("fps"))
    print("MediaPanel: an older saved layout restores, new columns keep "
          "their defaults -- OK")


def test_name_column_cannot_be_hidden_by_a_saved_state():
    _app()
    panel = MediaPanel()
    panel.restore_header_state({"version": media_panel.HEADER_STATE_VERSION,
                                "order": list(COLUMN_KEYS), "hidden": ["name"]})
    assert not panel.table.isColumnHidden(COLUMN_NAME)
    print("MediaPanel: Name stays visible whatever a saved layout says -- OK")


def test_library_side_panel_switches_the_target_library():
    _app()
    panel = MediaPanel()
    first = panel.library
    panel.library.add_entry(MediaEntry("In First", "/tmp/a.png"))
    second = panel.pool.add_library("Selects")
    panel._refresh_library_list()
    panel.library_list.setCurrentRow(1)
    assert panel.library is second
    assert panel.table.rowCount() == 0
    panel.library.add_entry(MediaEntry("In Second", "/tmp/b.png"))
    panel._refresh_table()
    assert panel.library_list.item(1).text() == "Selects (1)"
    panel.library_list.setCurrentRow(0)
    assert panel.library is first
    assert [panel.table.item(r, COLUMN_NAME).text() for r in range(panel.table.rowCount())] == ["In First"]
    print("MediaPanel: the side panel picks which library the table shows "
          "and imports land in -- OK")


def test_timelines_show_as_rows_and_open_on_double_click():
    _app()
    panel = MediaPanel()
    opened = []
    panel.timeline_load_requested.connect(opened.append)
    timeline = panel.new_timeline()
    assert opened == [timeline] and timeline in panel.library.timelines
    timeline.add_clip(tl.TimelineClip("Shot", "/a.mov", "video", 0, 47))
    panel.set_loaded_timeline(timeline)
    row = 0
    assert _cell_text(panel, row, "kind") == "Timeline"
    assert _cell_text(panel, row, "frames") == "48"
    assert panel.table.item(row, COLUMN_THUMBNAIL).text() == "▶"  # loaded marker
    assert panel._entry_id_at_row(row) is None  # never treated as a media entry
    opened.clear()
    panel._on_row_double_clicked(panel.table.item(row, COLUMN_NAME))
    assert opened == [timeline]
    print("MediaPanel: timelines are library rows; double-click asks to "
          "load one -- OK")


def test_new_timeline_names_are_unique_and_use_the_project_rate():
    _app()
    panel = MediaPanel()
    panel.set_frame_rate_getter(lambda: 25.0)
    first, second = panel.new_timeline(), panel.new_timeline()
    assert (first.name, second.name) == ("Timeline", "Timeline 2")
    assert first.frame_rate == 25.0
    print("MediaPanel: New Timeline names are unique and use the project "
          "edit rate -- OK")


import tempfile
import os
from PIL import Image
import numpy as np
from tests.test_media_probe import _write_prores_mov
from PySide6.QtCore import QMimeData, QUrl, QPointF
from PySide6.QtGui import QDropEvent

def test_import_paths_imports_media_and_ignores_other_files():
    _app()
    panel = MediaPanel()
    with tempfile.TemporaryDirectory() as temp_dir:
        png_path = os.path.join(temp_dir, "test.png")
        Image.fromarray(np.full((4, 6, 3), 128, dtype=np.uint8)).save(png_path)
        mov_path = os.path.join(temp_dir, "test.mov")
        _write_prores_mov(mov_path)
        txt_path = os.path.join(temp_dir, "notes.txt")
        with open(txt_path, "w") as f:
            f.write("This is a test file.")
        paths = [png_path, mov_path, txt_path]
        entries = panel.import_paths(paths)
        assert len(entries) == 2
        assert entries[0].name == "test"
        assert entries[1].name == "test"
        assert entries[1].media_kind == "video"
    print("test_import_paths_imports_media_and_ignores_other_files -- OK")

def test_import_paths_reports_all_failures_in_one_warning():
    _app()
    panel = MediaPanel()
    with tempfile.TemporaryDirectory() as temp_dir:
        png_path = os.path.join(temp_dir, "good.png")
        Image.fromarray(np.full((4, 6, 3), 128, dtype=np.uint8)).save(png_path)
        broken_png1_path = os.path.join(temp_dir, "broken1.png")
        with open(broken_png1_path, "wb") as f:
            f.write(b"not an image")
        broken_png2_path = os.path.join(temp_dir, "broken2.png")
        with open(broken_png2_path, "wb") as f:
            f.write(b"not an image")
        paths = [png_path, broken_png1_path, broken_png2_path]
        failures = []
        original_warning = media_panel.QMessageBox.warning
        def mock_warning(*args, **kwargs):
            failures.append(args[2])
        media_panel.QMessageBox.warning = mock_warning
        try:
            entries = panel.import_paths(paths)
            assert len(entries) == 1
            assert len(failures) == 1
            assert "broken1.png" in failures[0]
            assert "broken2.png" in failures[0]
        finally:
            media_panel.QMessageBox.warning = original_warning
    print("test_import_paths_reports_all_failures_in_one_warning -- OK")

def test_import_paths_into_another_library_leaves_the_current_one_alone():
    _app()
    panel = MediaPanel()
    panel.pool.add_library("Selects")
    with tempfile.TemporaryDirectory() as temp_dir:
        png_path = os.path.join(temp_dir, "test.png")
        Image.fromarray(np.full((4, 6, 3), 128, dtype=np.uint8)).save(png_path)
        entries = panel.import_paths([png_path], library=panel.pool.libraries[1])
        assert len(entries) == 1
        assert len(panel.library.entries) == 0
        assert len(panel.pool.libraries[1].entries) == 1
    print("test_import_paths_into_another_library_leaves_the_current_one_alone -- OK")

def test_dropping_files_on_the_table_imports_into_the_current_library():
    _app()
    panel = MediaPanel()
    with tempfile.TemporaryDirectory() as temp_dir:
        png_path = os.path.join(temp_dir, "test.png")
        Image.fromarray(np.full((4, 6, 3), 128, dtype=np.uint8)).save(png_path)
        txt_path = os.path.join(temp_dir, "notes.txt")
        with open(txt_path, "w") as f:
            f.write("This is a test file.")
        paths = [png_path, txt_path]
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        event = QDropEvent(QPointF(panel.table.viewport().rect().center()), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        panel.table.dropEvent(event)
        assert len(panel.library.entries) == 1
    print("test_dropping_files_on_the_table_imports_into_the_current_library -- OK")

def test_dropping_files_on_a_library_imports_into_that_library():
    _app()
    panel = MediaPanel()
    panel.pool.add_library("Selects")
    panel._refresh_library_list()
    with tempfile.TemporaryDirectory() as temp_dir:
        png_path = os.path.join(temp_dir, "test.png")
        Image.fromarray(np.full((4, 6, 3), 128, dtype=np.uint8)).save(png_path)
        item = panel.library_list.item(1)
        point = panel.library_list.visualItemRect(item).center()
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(png_path)])
        event = QDropEvent(point, Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        panel.library_list.dropEvent(event)
        assert len(panel.pool.libraries[0].entries) == 0
        assert len(panel.pool.libraries[1].entries) == 1
    print("test_dropping_files_on_a_library_imports_into_that_library -- OK")


def test_a_layout_saved_under_the_old_defaults_gives_way_to_the_new_ones():
    """Version-1 layouts just recorded the old, crowded defaults (every
    probed column on) -- they're ignored so the trimmed defaults apply."""
    _app()
    panel = MediaPanel()
    panel.restore_header_state({"order": list(COLUMN_KEYS), "hidden": [], "widths": {}})
    for index, column in enumerate(COLUMNS):
        assert panel.table.isColumnHidden(index) == (not column.visible), column.key
    assert panel.table.isColumnHidden(COLUMN_KEYS.index("path"))
    print("MediaPanel: an old-version saved layout falls back to the "
          "current defaults -- OK")


if __name__ == "__main__":
    test_hover_info_includes_start_and_end_timecode_for_video()
    test_hover_info_omits_timecode_for_a_still()
    test_hover_info_tolerates_missing_frame_rate()
    test_table_accepts_custom_context_menu()
    test_populate_menu_only_has_import_not_per_item_actions()
    test_context_menu_selects_the_clicked_row_before_popping_up()
    test_selection_survives_sorting_by_a_different_row_order()
    test_keep_selection_id_survives_refresh_after_rename()
    test_table_allows_extended_multi_selection()
    test_selected_entries_returns_every_selected_row_in_table_order()
    test_delete_removes_every_selected_entry()
    test_add_to_timeline_emits_once_per_selected_entry()
    test_right_click_on_an_already_selected_row_keeps_the_multi_selection()
    test_media_table_mime_data_carries_the_first_selected_entry_id()
    test_filter_hides_non_matching_rows_by_name_or_path()
    test_clearing_the_filter_shows_every_row_again()
    test_filter_survives_a_refresh_after_import()
    test_probed_columns_show_formatted_metadata()
    test_hover_info_uses_the_embedded_start_timecode()
    test_start_tc_sorts_by_frame_not_text()
    test_default_column_visibility_follows_columns_spec()
    test_header_state_round_trips_order_visibility_and_width()
    test_restoring_an_older_header_state_keeps_new_columns_at_their_defaults()
    test_name_column_cannot_be_hidden_by_a_saved_state()
    test_library_side_panel_switches_the_target_library()
    test_timelines_show_as_rows_and_open_on_double_click()
    test_new_timeline_names_are_unique_and_use_the_project_rate()
    test_import_paths_imports_media_and_ignores_other_files()
    test_import_paths_reports_all_failures_in_one_warning()
    test_import_paths_into_another_library_leaves_the_current_one_alone()
    test_dropping_files_on_the_table_imports_into_the_current_library()
    test_dropping_files_on_a_library_imports_into_that_library()
    test_a_layout_saved_under_the_old_defaults_gives_way_to_the_new_ones()
    print()
    print("All media_panel tests passed.")
