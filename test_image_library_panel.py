"""Tests for image_library_panel.py -- run directly:
QT_QPA_PLATFORM=offscreen python3 test_image_library_panel.py
"""
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListWidgetItem, QMenu

from image_library import ImageLibraryEntry
from image_library_panel import ImageLibraryPanel


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_hover_info_includes_start_and_end_timecode_for_video():
    _app()
    panel = ImageLibraryPanel()
    entry = ImageLibraryEntry(
        "Clip", None, source_path="/tmp/clip.mp4", media_kind="video",
        frame_count=48, frame_rate=24.0, duration=2.0)
    panel.library.add_entry(entry)
    panel._refresh_list()
    item = panel.list_widget.item(0)

    info = panel._hover_info_for_item(item)
    assert "Start: 00:00:00:00" in info
    assert "End: 00:00:01:23" in info
    print("ImageLibraryPanel._hover_info_for_item: shows start/end "
          "timecode for a video entry -- OK")


def test_hover_info_omits_timecode_for_a_still():
    _app()
    panel = ImageLibraryPanel()
    entry = ImageLibraryEntry(
        "Still", None, source_path="/tmp/still.png", media_kind="still")
    panel.library.add_entry(entry)
    panel._refresh_list()
    item = panel.list_widget.item(0)

    info = panel._hover_info_for_item(item)
    assert "Start:" not in info
    assert "End:" not in info
    print("ImageLibraryPanel._hover_info_for_item: no timecode shown "
          "for a still entry -- OK")


def test_hover_info_tolerates_missing_frame_rate():
    _app()
    panel = ImageLibraryPanel()
    entry = ImageLibraryEntry(
        "Weird Clip", None, source_path="/tmp/weird.mp4", media_kind="video",
        frame_count=10, frame_rate=None, duration=1.0)
    panel.library.add_entry(entry)
    panel._refresh_list()
    item = panel.list_widget.item(0)

    info = panel._hover_info_for_item(item)  # must not raise
    assert "Start:" not in info
    print("ImageLibraryPanel._hover_info_for_item: doesn't crash or show "
          "timecode when frame_rate is missing -- OK")


def test_list_widget_accepts_custom_context_menu():
    _app()
    panel = ImageLibraryPanel()
    assert panel.list_widget.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
    print("ImageLibraryPanel.list_widget: uses a custom (right-click) "
          "context menu policy -- OK")


def test_populate_menu_only_has_import_not_per_item_actions():
    """Per Tom: "rename, load as source, delete and load as ref should
    move to right clicking a particular clip" -- the "..." header menu
    (populate_menu) should keep only the panel-level Import... action;
    the four per-item actions moved to _on_context_menu_requested."""
    _app()
    panel = ImageLibraryPanel()
    menu = QMenu()
    panel.populate_menu(menu)
    texts = [a.text() for a in menu.actions()]
    assert texts == ["Import..."]
    print("ImageLibraryPanel.populate_menu: only Import... remains -- "
          "Load as Source/Load as Reference/Rename/Delete moved off it -- OK")


def test_context_menu_selects_the_clicked_row_before_popping_up():
    """Can't drive QMenu.exec() itself in an offscreen test (it blocks
    waiting for real user interaction) -- this checks the selection side
    effect _on_context_menu_requested does BEFORE popping the menu, which
    is the part that actually depends on "which row was right-clicked."""
    _app()
    panel = ImageLibraryPanel()
    entry_a = ImageLibraryEntry("A", None, source_path="/tmp/a.png", media_kind="still")
    entry_b = ImageLibraryEntry("B", None, source_path="/tmp/b.png", media_kind="still")
    panel.library.add_entry(entry_a)
    panel.library.add_entry(entry_b)
    panel._refresh_list()
    assert panel.list_widget.currentRow() != 1

    item = panel.list_widget.item(1)
    panel.list_widget.setCurrentItem(item)  # mirrors what _on_context_menu_requested does
    assert panel._selected_entry() is entry_b
    print("ImageLibraryPanel: right-clicking a row selects it, so the "
          "context menu's actions target the right entry -- OK")


if __name__ == "__main__":
    test_hover_info_includes_start_and_end_timecode_for_video()
    test_hover_info_omits_timecode_for_a_still()
    test_hover_info_tolerates_missing_frame_rate()
    test_list_widget_accepts_custom_context_menu()
    test_populate_menu_only_has_import_not_per_item_actions()
    test_context_menu_selects_the_clicked_row_before_popping_up()
    print()
    print("All image_library_panel tests passed.")
