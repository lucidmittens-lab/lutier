"""
floating_dialog.py -- keeps this app's popup dialogs (Stack's Grid Size/
Domain, Scopes' Graticule Intensity, Sample/Reference Adapt, Help Manual,
Export) AND floated panels (main_window.py's _float_panel) from ever
sliding behind the MAIN WINDOW once opened, without making them modal --
every dialog is deliberately non-modal (show(), not exec()) so the user
can keep working in the main window while one is open (see each dialog's
own "why show() not exec()" comment at its call site).

REVISED per Tom (originally used Qt.WindowStaysOnTopHint -- see below for
why that was wrong): "The dialogue and float windows can go under other
programs, just not the main window." Qt.WindowStaysOnTopHint fixes a
window's stacking position above EVERY other window system-wide,
including other applications entirely -- LUTier dialogs/floats would sit
on top of Finder, a browser, another app's palette, anything, forever.
That's a real behavioral bug, not a preference call.

The correct, narrower tool is Qt.WindowType.Tool -- a top-level window
type specifically meant for palettes/utility windows that stay above
their OWNER window (here, MainWindow) without being forced above
unrelated windows from other apps. This is the exact same OS-level
"owned window" relationship Photoshop-style floating palettes use, which
is structurally what these dialogs/floated panels already are. Requires
the window to actually have a real Qt parent set (not None) -- every
dialog here already gets one at construction (QDialog(self) etc., self
being some descendant of MainWindow), and _float_panel now explicitly
re-parents to MainWindow itself (`self`) instead of severing the parent
entirely, specifically so this relationship exists to enforce.

An earlier attempt at plain Qt.Dialog + a real parent was tried and found
insufficient in practice (per this file's prior version) -- clicking back
on the main window brought IT forward and covered the dialog again,
because Qt.Dialog's default stacking is only a momentary raise, not a
persistent OS-enforced relationship. Qt.Tool is the stronger, still-
scoped-correctly tool for a PERSISTENT "stays above just this one owner"
relationship -- not the same thing as WindowStaysOnTopHint's "stays above
everything, always," which is what actually caused Tom's complaint.

Still UNVERIFIED ON REAL HARDWARE (same class of issue as every OpenGL/
window-manager fix in this app -- this sandbox has no real display/window
manager to confirm actual stacking behavior against). Please test: (1)
float a panel or open a dialog, (2) click into a DIFFERENT application
(Finder, a browser, etc.) -- confirm the LUTier window/dialog now sits
BEHIND that other app's window, not on top of it, (3) click back on
LUTier's main window -- confirm the floated panel/dialog comes back above
the main window specifically. If Qt.Tool doesn't hold up reliably on
macOS in practice, the fallback would be listening for
QApplication.applicationStateChanged and toggling WindowStaysOnTopHint
off only while LUTier itself isn't the active app -- not built here since
it's meaningfully more complex and Qt.Tool is the documented right tool
for this exact scenario; only worth it if Qt.Tool is confirmed insufficient.
"""
from PySide6.QtCore import Qt


def make_floating_tool_window(window):
    """Set `window`'s type to Qt.Tool -- REPLACES the window's existing
    type flags (Qt window TYPE bits, e.g. Dialog vs. Tool vs. Window,
    aren't meant to be OR'd together the way a pure hint bit like
    WindowStaysOnTopHint was) rather than adding onto them, since every
    caller here wants exactly "floating utility window, nothing else."
    `window` must already have its real parent set -- every dialog gets
    one at construction (QDialog(self)); _float_panel sets one explicitly
    via setParent(main_window, ...) right before/instead of calling this
    (see that method for why it does the parent+flags change in one call
    instead of calling this function separately).

    Call once, right after constructing a QDialog (or any other top-level
    widget meant to float above just the main window), before its first
    show() -- changing window flags requires an explicit show() afterward
    regardless, which show_floating below already provides."""
    window.setWindowFlags(Qt.WindowType.Tool)


def show_floating(window):
    """show() + raise_() + activateWindow() in one call -- the exact
    combination every one of this app's non-modal dialogs already needs
    on open: raise_() alone doesn't restore keyboard focus, and a plain
    show() on a widget that's already visible-but-buried doesn't bring it
    back to the front on its own."""
    window.show()
    window.raise_()
    window.activateWindow()
