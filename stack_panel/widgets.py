"""stack_panel/widgets.py -- small widgets the inspector pages share:
_ParamLabel (a parameter's name label; double-click resets that parameter)
and _CurrentPageStackedWidget (the inspector's page stack, sized by the
page currently showing rather than the tallest one).
"""
from PySide6.QtWidgets import QLabel, QStackedWidget


class _ParamLabel(QLabel):
    """A parameter's own name label -- double-click resets it to default
    (per Tom: "add the ability to double click the name of a parameter
    to reset it individually"). `reset_callback` does the actual reset,
    almost always one or more setValue() calls on the control(s) this
    label names -- setValue already fires that control's normal
    valueChanged handler, so a reset applies live exactly the same way
    a manual edit would, no separate apply call needed at any call
    site. Used both for a single scalar control (Printer Lights'
    R/G/B, Exposure/Contrast's fields, Custom DRT's fields, CDL's Sat,
    Grading Primary's Pivot/Saturation, Grading Tone's S-Contrast) and
    for a whole wheel+spins field/range group at once (CDL's Slope/
    Offset/Power, Grading Primary's 7 fields, Grading Tone's 5 ranges)
    -- see each _reset_one_*_field helper for the latter."""

    def __init__(self, text, reset_callback, parent=None):
        super().__init__(text, parent)
        self._reset_callback = reset_callback
        self.setToolTip(f"Double-click to reset {text.rstrip(':')} to its default.")
        # Deliberately NOT PointingHandCursor -- Qt has no built-in
        # "reset" cursor, and the hand reads as a hyperlink/navigation
        # affordance (per Tom, "the gloved hand with finger icon"),
        # which is the wrong signal for a reset action. Left at the
        # default arrow; the tooltip carries the double-click hint.

    def mouseDoubleClickEvent(self, event):
        self._reset_callback()
        super().mouseDoubleClickEvent(event)


class _CurrentPageStackedWidget(QStackedWidget):
    """Plain QStackedWidget reports sizeHint()/minimumSizeHint() as the
    MAX across every page it holds, not just the one currently shown --
    confirmed via a live repro (a 2-page stack's minimumSizeHint stayed
    pinned to its tallest page's height even with the shortest page
    selected). That floor propagates up through this panel's own
    layout to the surrounding dock/splitter, so the Parameters dock
    can never be dragged shorter than the tallest operator (Grading
    Tone) even while a much sparser operator (e.g. Printer Lights) is
    selected -- the "stuck" panel height Tom flagged. Overriding both
    hints to delegate to currentWidget() (each page already wrapped by
    _wrap_inspector_page, so currentWidget() reports its own real
    content height) lets the dock's floor track whichever operator is
    actually showing."""

    def sizeHint(self):
        page = self.currentWidget()
        return page.sizeHint() if page is not None else super().sizeHint()

    def minimumSizeHint(self):
        page = self.currentWidget()
        return page.minimumSizeHint() if page is not None else super().minimumSizeHint()

    def setCurrentIndex(self, index):
        super().setCurrentIndex(index)
        self.updateGeometry()

    def setCurrentWidget(self, widget):
        super().setCurrentWidget(widget)
        self.updateGeometry()
