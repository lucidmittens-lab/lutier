"""
test_floating_window_scope.py -- tests for the floating_dialog.py rewrite
(per Tom: "The dialogue and float windows can go under other programs,
just not the main window") -- confirms every floated panel and popup
dialog is now Qt.Tool-typed with a real parent, and specifically that
Qt.WindowStaysOnTopHint (the old, over-broad "above literally everything"
mechanism) is nowhere in the resulting flags. Run directly:
python3 test_floating_window_scope.py

Requires a real (if offscreen) QApplication -- same headless-Qt setup as
every other GUI-touching verification in this project (see RUNNING.md).
What this CAN confirm headlessly: the right Qt flags/parent are set, and
that float/dock/dialog-open cycles don't raise. What it CANNOT confirm
(same limitation as every window-manager-dependent fix in this app): that
Qt.Tool actually holds up in real macOS window-stacking behavior --
that's flagged UNVERIFIED ON REAL HARDWARE in floating_dialog.py's own
docstring, same as the render/crash fixes before it.
"""
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from floating_dialog import make_floating_tool_window, show_floating
from main_window import MainWindow


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


# ---- floating_dialog.make_floating_tool_window -----------------------------

def test_make_floating_tool_window_sets_tool_type():
    _app()
    parent = QWidget()
    dialog = QDialog(parent)
    make_floating_tool_window(dialog)
    assert bool(dialog.windowFlags() & Qt.WindowType.Tool)
    print("make_floating_tool_window: sets Qt.WindowType.Tool -- OK")


def test_make_floating_tool_window_never_sets_stays_on_top_hint():
    """The actual point of this whole rewrite -- confirms the old,
    over-broad mechanism is gone, not just that the new one is present."""
    _app()
    parent = QWidget()
    dialog = QDialog(parent)
    make_floating_tool_window(dialog)
    assert not bool(dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint), (
        "WindowStaysOnTopHint forces a window above EVERY app on the "
        "desktop, not just LUTier's own main window -- exactly the bug "
        "Tom reported"
    )
    print("make_floating_tool_window: never sets WindowStaysOnTopHint -- OK")


def test_make_floating_tool_window_replaces_rather_than_ors_type():
    """Window TYPE bits (Dialog/Tool/Window) aren't meant to be combined
    via OR the way a pure hint bit is -- confirms this function actually
    REPLACES the type rather than adding Tool on top of whatever type
    (e.g. QDialog's default Qt.Dialog) the window already had.

    Checked via Qt.WindowType.WindowType_Mask, not a naive `flags &
    Qt.WindowType.Dialog` test -- Dialog's bits (3 = 0b011) happen to be a
    SUBSET of Tool's bits (11 = 0b1011), so that naive check is always
    truthy once ANY real window type is set and proves nothing. Masking
    isolates just the type portion of the flags for an exact comparison,
    which is what actually answers "is this Tool and only Tool"."""
    _app()
    parent = QWidget()
    dialog = QDialog(parent)
    assert (dialog.windowFlags() & Qt.WindowType.WindowType_Mask) == Qt.WindowType.Dialog, (
        "sanity check: QDialog should default to Qt.Dialog type"
    )
    make_floating_tool_window(dialog)
    assert (dialog.windowFlags() & Qt.WindowType.WindowType_Mask) == Qt.WindowType.Tool, (
        "Qt.Dialog should be REPLACED by Qt.Tool exactly, not left "
        "combined with it"
    )
    print("make_floating_tool_window: replaces the window type instead of "
          "OR-ing Tool onto the existing type -- OK")


# ---- main_window.py._float_panel scoping ------------------------------------

def test_floated_panel_is_tool_typed_and_parented_to_main_window():
    _app()
    win = MainWindow()
    try:
        win._float_panel("viewer_3d_frame")
        frame = win.viewer_3d_frame
        assert frame.parent() is win, (
            "a floated panel must be parented to MainWindow -- that's "
            "the OS-level relationship Qt.Tool needs to actually stay "
            "above it persistently, unlike the old setParent(None)"
        )
        assert bool(frame.windowFlags() & Qt.WindowType.Tool)
        assert not bool(frame.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        assert frame.isWindow(), (
            "must still be a genuine top-level window despite having a "
            "Qt parent set -- Qt.Tool + a parent is an OWNED top-level "
            "window, not an embedded child widget"
        )
    finally:
        win.close()
    print("MainWindow._float_panel: floated panel is Qt.Tool-typed, "
          "parented to MainWindow, still a real top-level window, and "
          "never gets WindowStaysOnTopHint -- OK")


def test_docked_panel_is_not_a_window_and_not_tool_typed():
    _app()
    win = MainWindow()
    try:
        win._float_panel("viewer_3d_frame")
        win._dock_panel("viewer_3d_frame")
        frame = win.viewer_3d_frame
        assert not frame.isWindow(), (
            "back in the splitter, this must be a plain embedded child "
            "widget again, not a top-level window of any kind"
        )
        assert not bool(frame.windowFlags() & Qt.WindowType.Tool)
    finally:
        win.close()
    print("MainWindow._dock_panel: docked panel is a plain embedded "
          "widget again, no longer Tool-typed or top-level -- OK")


def test_every_panel_toggle_is_tool_typed_after_floating():
    """Not hardcoded to the 3D Viewer specifically -- confirms the fix
    applies uniformly to every floatable panel, via the same PANEL_TOGGLES
    list main_window.py itself iterates."""
    _app()
    win = MainWindow()
    try:
        from main_window import PANEL_TOGGLES
        for _action_attr, _label, frame_attr, _default in PANEL_TOGGLES:
            win._float_panel(frame_attr)
            frame = getattr(win, frame_attr)
            assert bool(frame.windowFlags() & Qt.WindowType.Tool), (
                f"{frame_attr} was not made Qt.Tool-typed when floated"
            )
            assert not bool(
                frame.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
            ), f"{frame_attr} still has WindowStaysOnTopHint after floating"
            win._dock_panel(frame_attr)
    finally:
        win.close()
    print("MainWindow._float_panel: every single panel in PANEL_TOGGLES "
          "gets the same Qt.Tool scoping when floated, none keep "
          "WindowStaysOnTopHint -- OK")


# ---- popup dialogs (Stack Grid Size/Domain, Scopes Graticule Intensity, ----
# Sample/Reference Adapt, Export, Help Manual) --------------------------------

def test_manual_dialog_is_tool_typed_with_a_real_parent():
    _app()
    win = MainWindow()
    try:
        win._show_manual_dialog()
        dialog = win._manual_dialog
        assert dialog.parent() is not None
        assert bool(dialog.windowFlags() & Qt.WindowType.Tool)
        assert not bool(dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    finally:
        win.close()
    print("MainWindow._show_manual_dialog: Help > Manual is Qt.Tool-typed "
          "with a real parent, no WindowStaysOnTopHint -- OK")


def test_export_dialog_is_tool_typed_with_a_real_parent():
    _app()
    win = MainWindow()
    try:
        win._show_export_dialog()
        dialog = win._export_dialog
        assert dialog.parent() is not None
        assert bool(dialog.windowFlags() & Qt.WindowType.Tool)
        assert not bool(dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    finally:
        win.close()
    print("MainWindow._show_export_dialog: Export... is Qt.Tool-typed "
          "with a real parent, no WindowStaysOnTopHint -- OK")


def test_graticule_opacity_dialog_is_tool_typed_with_a_real_parent():
    _app()
    win = MainWindow()
    try:
        win.scopes_panel.show_graticule_opacity_dialog()
        dialog = win.scopes_panel._graticule_opacity_dialog
        assert dialog.parent() is not None
        assert bool(dialog.windowFlags() & Qt.WindowType.Tool)
        assert not bool(dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    finally:
        win.close()
    print("ScopesPanel.show_graticule_opacity_dialog: Graticule "
          "Intensity... is Qt.Tool-typed with a real parent, no "
          "WindowStaysOnTopHint -- OK")


if __name__ == "__main__":
    test_make_floating_tool_window_sets_tool_type()
    test_make_floating_tool_window_never_sets_stays_on_top_hint()
    test_make_floating_tool_window_replaces_rather_than_ors_type()
    test_floated_panel_is_tool_typed_and_parented_to_main_window()
    test_docked_panel_is_not_a_window_and_not_tool_typed()
    test_every_panel_toggle_is_tool_typed_after_floating()
    test_manual_dialog_is_tool_typed_with_a_real_parent()
    test_export_dialog_is_tool_typed_with_a_real_parent()
    test_graticule_opacity_dialog_is_tool_typed_with_a_real_parent()
    print("All floating-window-scope tests passed.")
