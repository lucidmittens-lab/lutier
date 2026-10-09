"""Tests for Workspace > Interface Scale (main_window_parts/menus.py) --
run directly: QT_QPA_PLATFORM=offscreen python3 -m tests.test_ui_scale_menu

Uses MenuMixin's two scale methods on a bare QMainWindow host (no full
MainWindow needed); theme_prefs is pointed at a temp file throughout.
"""
import sys
import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QMessageBox

import theme_prefs as tp
from main_window_parts.menus import MenuMixin


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


class _Host(MenuMixin, QMainWindow):
    pass


def test_interface_scale_menu_offers_the_choices_checks_the_saved_one_and_saves():
    _app()
    original_path = tp.THEME_PREFS_PATH
    original_info = QMessageBox.information
    notes = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        tp.THEME_PREFS_PATH = Path(tmp_dir) / "theme_prefs.json"
        QMessageBox.information = staticmethod(lambda _parent, title, text: notes.append((title, text)))
        try:
            tp.save_ui_scale(0.9)
            host = _Host()
            menu = QMenu()
            host._build_ui_scale_menu(menu)
            labels = [a.text() for a in menu.actions()]
            assert labels == ["70%", "75%", "80%", "90%", "100%", "110%", "125%"]
            assert [a.text() for a in menu.actions() if a.isChecked()] == ["90%"]

            next(a for a in menu.actions() if a.text() == "75%").trigger()
            assert tp.load_ui_scale() == 0.75
            assert notes and "Takes effect after restart" in notes[-1][1]

            notes.clear()
            host._choose_ui_scale(0.75)  # unchanged -> no save, no note
            assert notes == []
        finally:
            tp.THEME_PREFS_PATH = original_path
            QMessageBox.information = original_info
    print("Interface Scale menu: 7 choices, saved one checked, picking saves "
          "and notes 'after restart' -- OK")


if __name__ == "__main__":
    test_interface_scale_menu_offers_the_choices_checks_the_saved_one_and_saves()
    print("\nAll Interface Scale menu tests passed.")
