"""Tests for browser_panel.py -- run directly:
QT_QPA_PLATFORM=offscreen python3 test_browser_panel.py
"""
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import browser_panel as bp
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


def _item_names(panel):
    return [panel.list_widget.item(i).text() for i in range(panel.list_widget.count())]


def test_search_box_debounces_before_refreshing():
    """Regression guard for a real performance bug (found in code
    review): every keystroke used to trigger its own full recursive
    scan_lut_folder walk, with no debounce at all. Typing must NOT
    re-scan on every character -- only once, after typing pauses for
    SEARCH_DEBOUNCE_MS."""
    _app()
    panel = BrowserPanel()
    panel.set_folder(str(SAMPLES_DIR))
    before = _item_names(panel)
    assert len(before) > 1, "expected multiple files in samples/ for this test to mean anything"

    scan_calls = []
    original_refresh = panel.refresh
    panel.refresh = lambda: (scan_calls.append(1), original_refresh())[1]

    for ch in "roundtrip":
        panel.search_box.setText(panel.search_box.text() + ch)
    # No refresh yet -- still within the debounce window (no qWait at all
    # since setText above happens synchronously, well under SEARCH_
    # DEBOUNCE_MS in wall-clock terms).
    assert scan_calls == [], "typing must not trigger an immediate re-scan per keystroke"
    assert _item_names(panel) == before, "the list must still show the OLD results while debouncing"

    QTest.qWait(bp.SEARCH_DEBOUNCE_MS + 100)  # let the debounce timer fire

    assert scan_calls == [1], f"expected exactly one debounced refresh, got {len(scan_calls)}"
    after = _item_names(panel)
    assert after != before
    assert all("roundtrip" in name.lower() for name in after)
    print("BrowserPanel: search box debounces -- typing 9 characters "
          "triggers exactly ONE re-scan, not nine, and the list updates "
          "once the debounce settles -- OK")


def test_set_folder_refreshes_immediately_not_debounced():
    """Choosing a folder is a single deliberate action, not a per-
    keystroke event -- must populate the list synchronously, with no
    wait needed (already implicitly relied on by every other test in
    this file that calls set_folder() and immediately inspects
    list_widget)."""
    _app()
    panel = BrowserPanel()
    panel.set_folder(str(SAMPLES_DIR))
    assert panel.list_widget.count() > 0
    print("BrowserPanel.set_folder: refreshes immediately, not debounced -- OK")


def test_set_folder_cancels_a_pending_search_debounce():
    """Switching folders while a search-box debounce is still pending
    must not let a stale, already-superseded refresh fire again
    afterward -- set_folder's OWN immediate refresh (with whatever
    search text is currently in the box) must be the last word, not
    followed by a redundant second one once the old timer's delay
    happens to elapse."""
    _app()
    panel = BrowserPanel()
    panel.set_folder(str(SAMPLES_DIR))
    panel.search_box.setText("roundtrip")  # arms the debounce timer, doesn't fire yet
    assert panel._search_debounce_timer.isActive()

    scan_calls = []
    original_refresh = panel.refresh
    panel.refresh = lambda: (scan_calls.append(1), original_refresh())[1]

    empty_dir = SAMPLES_DIR / "cdl"  # a real subfolder, different contents than SAMPLES_DIR itself
    panel.set_folder(str(empty_dir))
    assert not panel._search_debounce_timer.isActive(), (
        "set_folder must cancel any pending search debounce for the old folder")
    assert scan_calls == [1], "set_folder's own refresh must have run exactly once, synchronously"

    QTest.qWait(bp.SEARCH_DEBOUNCE_MS + 100)
    assert scan_calls == [1], (
        "the cancelled timer must not fire a redundant second refresh after the fact")
    print("BrowserPanel.set_folder: cancels a pending search-box "
          "debounce from the previous folder, no redundant refresh "
          "fires afterward -- OK")


if __name__ == "__main__":
    test_hover_info_includes_full_path_kind_and_size_for_a_lut()
    test_hover_info_flags_cdl_kind()
    test_hover_info_returns_none_for_an_item_with_no_path_data()
    test_search_box_debounces_before_refreshing()
    test_set_folder_refreshes_immediately_not_debounced()
    test_set_folder_cancels_a_pending_search_debounce()
    print()
    print("All browser_panel tests passed.")
