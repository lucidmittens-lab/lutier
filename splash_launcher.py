"""
splash_launcher.py -- the app's actual entry point (see setup.py's APP
list), in front of main_window.py rather than instead of it.

WHY THIS FILE EXISTS: `import main_window` alone costs several real
seconds (colour-science's own module-level setup is ~2.7s of that,
pyqtgraph + pyqtgraph.opengl another ~1.2s, measured directly on this
machine) before a single window has appeared. main_window.py cannot show
a splash before paying that cost itself: Python executes a module
top-to-bottom before any code inside it runs, and main_window.py's own
top-level `import stack_panel`/`import sample_image_panel`/etc. lines are
exactly what pull colour-science/pyqtgraph/scipy/PyOpenColorIO in -- by
the time main_window.main() could show anything, the slow part is
already done. So the splash has to live in a separate module that
imports nothing heavy until after it's already on screen.

A previous attempt at this (see git history) used a tkinter pre-launch
window, torn down before handing off to a completely separate Qt
QApplication/MainWindow -- reported as popping up for only a couple of
frames rather than actually covering the load. That two-toolkit handoff
is the likely reason: destroying the tkinter window happens instantly,
but the Qt side then still has to build a fresh QApplication, construct
MainWindow, and show() it (roughly another second, measured) with
nothing on screen to bridge that gap -- and separately, setup.py's own
APP entry was never actually pointed at that launcher, so it's not clear
the two-toolkit version was ever exercised by a real packaged build at
all.

This version uses ONE toolkit throughout: build the real QApplication
here, show a QSplashScreen with it immediately, do the slow `import
main_window` while it's up, then hand that SAME QApplication to
main_window.build_window() (see that function's docstring) to construct
and show the real MainWindow, and only then finish() the splash. There's
never a moment with zero windows on screen, and this is exactly the use
QSplashScreen's own Qt documentation describes (show it, do lengthy
initialization, show the real window, finish() the splash) -- not a
workaround, the standard pattern.

Run directly with `python3 splash_launcher.py`. main_window.py's own
`if __name__ == "__main__"` block is untouched, so `python3
main_window.py` still works directly too, skipping the splash entirely
-- useful during development, when waiting through it while iterating
isn't worth it (see main_window.main()'s own docstring).
"""
import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen

_SPLASH_WIDTH, _SPLASH_HEIGHT = 480, 320

# Mirrors style._DARK_WINDOW -- a literal duplicate, not an import, so
# the splash canvas matches the real window's own background right up to
# the handoff without pulling in style.build_dark_palette()/STYLESHEET
# before build_window() actually needs them. (style.py itself is cheap
# to import -- QtGui + theme_prefs.py's plain json/pathlib, nothing like
# the colour-science/pyqtgraph cost this file exists to defer -- so
# get_accent_color() below IS imported from it directly rather than
# duplicating ITS logic too; only this one background literal is copied,
# same "each module keeps its own tiny copy of a shared literal" rule
# app_logic.py/main_window.py's own asset-path helpers already use.)
_BACKGROUND_HEX = "#242427"


def _asset_path(filename):
    """Resolve a bundle-root file (AppIcon_source.png sits directly
    alongside setup.py/main_window.py in source, not under assets/) the
    same RESOURCEPATH-anchoring way style._texture_asset_path/
    app_logic.default_sample_image_path do, and for the identical reason:
    py2app zips this module's .pyc into Contents/Resources/lib/
    python3XX.zip for a frozen build, so __file__'s parent at runtime is
    that zip archive, not Contents/Resources where DATA_FILES actually
    places it (see setup.py). Returns a Path that may not exist --
    _build_splash_pixmap already tolerates that (falls back to a
    text-only splash), same as every other asset-loading path in this
    app."""
    if getattr(sys, "frozen", False) and "RESOURCEPATH" in os.environ:
        base = Path(os.environ["RESOURCEPATH"])
    else:
        base = Path(__file__).parent
    return base / filename


def _build_splash_pixmap():
    """The pixmap shown in the QSplashScreen: dark background, the real
    app icon centered near the top, "LUTier" underneath in the user's
    saved accent color -- see splash_preview_check.png in the repo root
    for the reference design this matches. Never raises: a missing icon
    file or an unreadable accent-color preference both fall back to
    something reasonable rather than blocking startup over a cosmetic
    detail (same tolerance this app's other best-effort theme/asset code
    already uses -- see theme_prefs.load_accent_color's own docstring)."""
    import style  # cheap (see module docstring) -- for get_accent_color()

    pixmap = QPixmap(_SPLASH_WIDTH, _SPLASH_HEIGHT)
    pixmap.fill(QColor(_BACKGROUND_HEX))

    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        icon_path = _asset_path("AppIcon_source.png")
        if icon_path.exists():
            icon = QPixmap(str(icon_path))
            if not icon.isNull():
                icon = icon.scaled(
                    160, 160, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                x = (_SPLASH_WIDTH - icon.width()) // 2
                painter.drawPixmap(x, 48, icon)

        try:
            accent_hex = style.get_accent_color()
        except Exception:  # noqa: BLE001 -- a splash is the last place to crash over a theme read
            accent_hex = style.DEFAULT_ACCENT_HEX

        font = QFont()
        font.setBold(True)
        font.setPointSize(20)
        painter.setFont(font)
        painter.setPen(QColor(accent_hex))
        text_rect = pixmap.rect().adjusted(0, 226, 0, 0)
        painter.drawText(
            text_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, "LUTier")
    finally:
        painter.end()

    return pixmap


def main():
    # Must be set before ANY QApplication is constructed -- see
    # main_window.main()'s own detailed comment for the full explanation
    # (AA_ShareOpenGLContexts, pyqtgraph's class-level shader-program
    # cache). Duplicated here rather than left to main_window.py because
    # THIS is now what constructs the process's one QApplication --
    # main_window's own copy of this line only ever runs when
    # main_window.py is executed directly, skipping this file.
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    app = QApplication(sys.argv)

    splash = QSplashScreen(_build_splash_pixmap())
    splash.show()
    app.processEvents()  # force the actual paint now, before the slow
    # `import main_window` below. One call is enough here -- unlike
    # main_window.main()'s own historical struggles getting a single
    # processEvents() to fully settle ITS window (a real QMainWindow with
    # a full dock/panel layout to lay out), a QSplashScreen is a single
    # flat pixmap with no child widgets or layout to settle, so one paint
    # event completes it.

    import main_window  # the slow part -- colour-science/pyqtgraph/
    # OpenColorIO/scipy all load transitively through this module's own
    # top-level imports (see this file's docstring for measured costs).
    # The splash is already fully painted and composited by the window
    # server at this point, so it stays visible through this entire
    # blocking import with nothing needing to keep pumping its event loop
    # in the meantime -- the same physical reasoning the previous
    # tkinter-based attempt relied on, just without a second toolkit (and
    # the handoff gap that came with tearing one down to start the other).

    window = main_window.build_window(app)
    splash.finish(window)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
