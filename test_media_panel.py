"""Tests for media_panel.py -- run directly:
QT_QPA_PLATFORM=offscreen python3 test_media_panel.py
"""
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMenu

from media_library import MediaEntry, MEDIA_ENTRY_DRAG_MIME_TYPE
from media_panel import MediaPanel, COLUMN_NAME, COLUMN_THUMBNAIL


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
    texts = [a.text() for a in menu.actions()]
    assert texts == ["Import..."]
    print("MediaPanel.populate_menu: only Import... remains -- "
          "Load as Source/Load as Reference/Rename/Delete moved off it -- OK")


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

    class _FakeMenu:
        def __init__(self, *a, **k):
            pass

        def addAction(self, *a, **k):
            pass

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
    print()
    print("All media_panel tests passed.")
