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

from PySide6.QtCore import QPoint
from PySide6.QtGui import QDragLeaveEvent
from PySide6.QtWidgets import QApplication

from stack_panel import _ElidingLabel, _StackListWidget


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


# ---- _StackListWidget drag-to-reorder drop-position indicator ------------
# Per Tom: there should be a visual indicator of where a drag-to-reorder
# will land. Qt's own built-in drop indicator (setDropIndicatorShown)
# never actually painted anything here -- this class's dragMoveEvent is a
# full override that never calls super(), which is exactly what would
# normally drive that built-in indicator -- so it's hand-painted instead
# (see _StackListWidget.paintEvent), driven by _set_drop_indicator_row.
# Full native drag-and-drop can't be synthesized in a headless test
# without a lot of Qt plumbing for little extra confidence; these tests
# exercise the actual state machine directly (_drop_row's row-from-
# position math, _set_drop_indicator_row's dedup, dragLeaveEvent's
# cleanup) -- the same calls a real drag sequence drives.

def _list_with_three_rows():
    _app()
    lst = _StackListWidget(panel=None)
    lst.show()
    for label in ("First", "Second", "Third"):
        lst.addItem(label)
    lst.resize(200, 200)
    return lst


def test_drop_row_upper_half_of_a_row_inserts_before_it():
    lst = _list_with_three_rows()
    rect = lst.visualItemRect(lst.item(1))
    pos = QPoint(rect.center().x(), rect.top() + 1)
    assert lst._drop_row(pos) == 1
    print("_StackListWidget._drop_row: a position in a row's upper half "
          "inserts BEFORE that row -- OK")


def test_drop_row_lower_half_of_a_row_inserts_after_it():
    lst = _list_with_three_rows()
    rect = lst.visualItemRect(lst.item(1))
    pos = QPoint(rect.center().x(), rect.bottom() - 1)
    assert lst._drop_row(pos) == 2
    print("_StackListWidget._drop_row: a position in a row's lower half "
          "inserts AFTER that row -- OK")


def test_drop_row_below_every_row_appends_at_the_end():
    lst = _list_with_three_rows()
    pos = QPoint(10, 10_000)  # far below the last real row
    assert lst._drop_row(pos) == lst.count()
    print("_StackListWidget._drop_row: a position below every row "
          "appends at the end -- OK")


def test_set_drop_indicator_row_dedupes_unchanged_value():
    lst = _list_with_three_rows()
    calls = []
    lst.viewport().update = lambda: calls.append(True)

    lst._set_drop_indicator_row(1)
    assert lst._drop_indicator_row == 1
    assert len(calls) == 1, "setting a NEW row must trigger exactly one repaint"

    lst._set_drop_indicator_row(1)
    assert len(calls) == 1, "setting the SAME row again must not trigger another repaint"

    lst._set_drop_indicator_row(2)
    assert len(calls) == 2, "setting a genuinely different row must trigger another repaint"
    print("_StackListWidget._set_drop_indicator_row: only repaints when "
          "the target row actually changes -- OK")


def test_drag_leave_clears_the_indicator():
    lst = _list_with_three_rows()
    lst._set_drop_indicator_row(1)
    assert lst._drop_indicator_row == 1

    lst.dragLeaveEvent(QDragLeaveEvent())

    assert lst._drop_indicator_row is None
    print("_StackListWidget.dragLeaveEvent: clears the drop indicator "
          "(e.g. dragging back out, or Escape cancelling the drag) -- OK")


if __name__ == "__main__":
    test_short_text_that_fits_is_not_elided_and_has_no_tooltip()
    test_long_text_at_narrow_width_is_elided_with_ellipsis_and_gets_a_tooltip()
    test_full_text_survives_resizing_back_and_forth()
    test_set_text_updates_the_full_text_not_just_the_displayed_text()
    test_drop_row_upper_half_of_a_row_inserts_before_it()
    test_drop_row_lower_half_of_a_row_inserts_after_it()
    test_drop_row_below_every_row_appends_at_the_end()
    test_set_drop_indicator_row_dedupes_unchanged_value()
    test_drag_leave_clears_the_indicator()
    print()
    print("All stack_panel tests passed.")
