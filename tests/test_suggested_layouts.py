"""Tests for main_window_parts/suggested_layouts.py (Media / Grade) -- run
directly: QT_QPA_PLATFORM=offscreen python3 -m tests.test_suggested_layouts
"""
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QPushButton

import workspace_io
from main_window import MainWindow
from main_window_parts.constants import (
    GRADE_LAYOUT, MEDIA_LAYOUT, PANEL_TOGGLES, SUGGESTED_LAYOUT_PANELS)

_workspace_tmp_dir = tempfile.TemporaryDirectory()
workspace_io.DEFAULT_WORKSPACE_DIR = Path(_workspace_tmp_dir.name) / "LUTier"
workspace_io.DEFAULT_WORKSPACE_PATH = workspace_io.DEFAULT_WORKSPACE_DIR / "workspace.json"


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _shown(win):
    return {frame_attr for _a, _l, frame_attr, _d in PANEL_TOGGLES
            if not getattr(win, frame_attr).isHidden()}


def test_suggested_layouts_show_exactly_their_panels_and_can_be_reapplied():
    _app()
    # No remembered workspace: other test modules in the same process point
    # workspace_io at their own temp dirs, where a closed window may have
    # saved one, so "launch = Grade" would depend on test order.
    original_load = workspace_io.load_remembered_workspace
    workspace_io.load_remembered_workspace = lambda: None
    try:
        win = MainWindow()
    finally:
        workspace_io.load_remembered_workspace = original_load
    win.show()
    QApplication.processEvents()
    try:
        assert _shown(win) == SUGGESTED_LAYOUT_PANELS[GRADE_LAYOUT], "launch = Grade"
        win.apply_suggested_layout(MEDIA_LAYOUT)
        QApplication.processEvents()
        assert _shown(win) == {"browser_frame", "source_viewer_frame", "image_library_frame"}
        assert win.dockWidgetArea(win.image_library_frame) == Qt.DockWidgetArea.BottomDockWidgetArea

        # Not a mode: the user can change things, and re-applying still works.
        win.scopes_frame.setVisible(True)
        win.apply_suggested_layout(GRADE_LAYOUT)
        QApplication.processEvents()
        assert _shown(win) == SUGGESTED_LAYOUT_PANELS[GRADE_LAYOUT]
        assert win.dockWidgetArea(win.image_library_frame) != Qt.DockWidgetArea.BottomDockWidgetArea
    finally:
        win.close()
    print("suggested layouts: Media and Grade show exactly their panels -- OK")


def test_corner_buttons_and_menu_actions_exist():
    _app()
    win = MainWindow()
    try:
        corner = win.menuBar().cornerWidget(Qt.Corner.TopRightCorner)
        assert corner is not None, "the corner widget must survive (Python owns it)"
        assert [b.text() for b in corner.findChildren(QPushButton)] == ["Media", "Grade"]
        shortcuts = {a.text(): a.shortcut().toString() for a in win.findChildren(QAction)
                     if a.text() in ("Media", "Grade")}
        assert shortcuts == {"Media": "Alt+1", "Grade": "Alt+2"}
    finally:
        win.close()
    print("suggested layouts: corner buttons and Alt+1/Alt+2 menu actions -- OK")


if __name__ == "__main__":
    test_suggested_layouts_show_exactly_their_panels_and_can_be_reapplied()
    test_corner_buttons_and_menu_actions_exist()
    print("\nAll suggested layout tests passed.")
