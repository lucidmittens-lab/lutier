"""Tests for stack_panel.py -- run directly: python3 test_stack_panel.py

Currently just _ElidingLabel, the QLabel subclass _StageRowWidget uses so
a stage row's label truncates ("…") to fit whatever width it's actually
given rather than forcing the row to grow past the panel's edge or
pushing the delete button off past it (see that class's own docstring).
Everything else in this module needs a full StackPanel/real Stack/signal
wiring to exercise meaningfully and has no dedicated test file of its own
yet -- this one is deliberately narrow, not a stand-in for that.

Requires a real (if offscreen) QApplication -- same headless-Qt setup as
every other GUI-touching verification in this project (see RUNNING.md).
Every label below is .show()n right after construction, before any
.resize() call: Qt does not deliver resizeEvent to a widget that's never
been shown (confirmed directly -- .resize() on an unshown widget updates
.width() immediately but never fires resizeEvent), unlike a real
_StageRowWidget, which always lives inside an actually-visible window --
so this is a test-only requirement, not something the class itself needs.
"""
import sys

from PySide6.QtWidgets import QApplication

from stack_panel import _ElidingLabel


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_short_text_that_fits_is_not_elided_and_has_no_tooltip():
    _app()
    label = _ElidingLabel("Short")
    label.show()
    label.resize(500, 20)
    assert label.text() == "Short"
    assert label.toolTip() == ""
    print("_ElidingLabel: text that fits at the given width is shown in "
          "full, no tooltip -- OK")


def test_long_text_at_narrow_width_is_elided_with_ellipsis_and_gets_a_tooltip():
    _app()
    full = "A very long stage label that will not fit in a narrow row"
    label = _ElidingLabel(full)
    label.show()
    label.resize(60, 20)
    assert label.text() != full
    assert label.text().endswith("…")
    assert label.toolTip() == full
    print("_ElidingLabel: text too wide for the given width is elided "
          "with '…', full text available as a tooltip -- OK")


def test_full_text_survives_resizing_back_and_forth():
    _app()
    full = "Another fairly long stage label for this test"
    label = _ElidingLabel(full)
    label.show()
    label.resize(40, 20)
    assert label.text() != full  # elided at narrow width

    label.resize(600, 20)
    assert label.text() == full  # room enough now -- shown in full
    assert label.toolTip() == ""

    label.resize(40, 20)
    assert label.text() != full  # narrow again -- elided again
    assert label.full_text() == full, "full_text() must always return the original, unelided string"
    print("_ElidingLabel: re-elides correctly across repeated resizes, "
          "full_text() always returns the original string -- OK")


def test_set_text_updates_the_full_text_not_just_the_displayed_text():
    _app()
    label = _ElidingLabel("Original")
    label.show()
    label.resize(20, 20)
    label.setText("A brand new, much longer replacement label")
    assert label.full_text() == "A brand new, much longer replacement label"
    print("_ElidingLabel.setText: updates the underlying full text (not "
          "just what's currently displayed) -- OK")


if __name__ == "__main__":
    test_short_text_that_fits_is_not_elided_and_has_no_tooltip()
    test_long_text_at_narrow_width_is_elided_with_ellipsis_and_gets_a_tooltip()
    test_full_text_survives_resizing_back_and_forth()
    test_set_text_updates_the_full_text_not_just_the_displayed_text()
    print()
    print("All stack_panel tests passed.")
