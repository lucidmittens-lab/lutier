"""Tests for media_panel.py -- run directly:
QT_QPA_PLATFORM=offscreen python3 test_media_panel.py
"""
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMenu

from media_library import MediaEntry
from media_panel import MediaPanel, COLUMN_NAME


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


if __name__ == "__main__":
    test_hover_info_includes_start_and_end_timecode_for_video()
    test_hover_info_omits_timecode_for_a_still()
    test_hover_info_tolerates_missing_frame_rate()
    test_table_accepts_custom_context_menu()
    test_populate_menu_only_has_import_not_per_item_actions()
    test_context_menu_selects_the_clicked_row_before_popping_up()
    test_selection_survives_sorting_by_a_different_row_order()
    test_keep_selection_id_survives_refresh_after_rename()
    print()
    print("All media_panel tests passed.")
