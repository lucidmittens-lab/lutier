"""Tests for style.py -- run directly:
QT_QPA_PLATFORM=offscreen python3 test_style.py
"""
import sys

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from style import build_dark_palette


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_disabled_backgrounds_stay_dark():
    """Stack Params is disabled during playback; an unset Disabled group
    falls back to Qt's light defaults and turned the panel white."""
    _app()
    palette = build_dark_palette()
    for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base,
                 QPalette.ColorRole.Button, QPalette.ColorRole.AlternateBase):
        active = palette.color(QPalette.ColorGroup.Active, role)
        disabled = palette.color(QPalette.ColorGroup.Disabled, role)
        assert disabled == active, (role, disabled.name(), active.name())
        assert disabled.lightness() < 100, (role, disabled.name())
    print("build_dark_palette: disabled backgrounds match the dark active "
          "ones -- OK")


if __name__ == "__main__":
    test_disabled_backgrounds_stay_dark()
    print()
    print("All style tests passed.")
