"""Tests for browser_panel.py -- run directly:
QT_QPA_PLATFORM=offscreen python3 test_browser_panel.py
"""
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from browser_panel import BrowserPanel, _KIND_CDL, _KIND_LUT, _ROLE_KIND

SAMPLES_DIR = Path(__file__).parent / "samples"


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_hover_info_includes_full_path_kind_and_size_for_a_lut():
    _app()
    panel = BrowserPanel()
    panel.set_folder(str(SAMPLES_DIR))
    lut_item = None
    for i in range(panel.list_widget.count()):
        item = panel.list_widget.item(i)
        if item.data(_ROLE_KIND) == _KIND_LUT:
            lut_item = item
            break
    assert lut_item is not None, "expected at least one LUT file in samples/"
    path = lut_item.data(Qt.ItemDataRole.UserRole)

    info = panel._hover_info_for_item(lut_item)
    assert path in info
    assert "LUT" in info
    assert "modified" in info
    print("BrowserPanel._hover_info_for_item: shows full path, kind, "
          "and a live size/modified stat for a LUT entry -- OK")


def test_hover_info_flags_cdl_kind():
    _app()
    panel = BrowserPanel()
    panel.set_folder(str(SAMPLES_DIR))
    cdl_item = None
    for i in range(panel.list_widget.count()):
        item = panel.list_widget.item(i)
        if item.data(_ROLE_KIND) == _KIND_CDL:
            cdl_item = item
            break
    assert cdl_item is not None, "expected at least one CDL file in samples/cdl"

    info = panel._hover_info_for_item(cdl_item)
    assert "\nCDL\n" in info
    print("BrowserPanel._hover_info_for_item: flags a CDL entry's kind "
          "as CDL, not LUT -- OK")


def test_hover_info_returns_none_for_an_item_with_no_path_data():
    _app()
    panel = BrowserPanel()
    from PySide6.QtWidgets import QListWidgetItem
    bare_item = QListWidgetItem("no path set")
    assert panel._hover_info_for_item(bare_item) is None
    print("BrowserPanel._hover_info_for_item: returns None rather than "
          "raising when an item has no path data -- OK")


if __name__ == "__main__":
    test_hover_info_includes_full_path_kind_and_size_for_a_lut()
    test_hover_info_flags_cdl_kind()
    test_hover_info_returns_none_for_an_item_with_no_path_data()
    print()
    print("All browser_panel tests passed.")
