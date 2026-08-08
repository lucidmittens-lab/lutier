"""
spinbox_scrub.py -- click-and-drag "scrubbing" for QSpinBox/QDoubleSpinBox
(per Tom): drag left/right on the number itself to decrease/increase its
value, instead of only being able to click the tiny up/down arrow buttons
or type a new value directly. A common convention in creative/DCC tools
(Nuke, Houdini, Blender, ...) for exactly this kind of numeric field,
which Qt's own QAbstractSpinBox doesn't provide out of the box.

enable_drag_to_scrub(spin_box) is the only thing callers need. It wires
an event filter onto the spin box's own internal QLineEdit -- that's
what QAbstractSpinBox actually delegates its text area's mouse events
to; the up/down step buttons are handled separately by the spin box
itself and are completely untouched by this, so they keep working
exactly as before.
"""
from PySide6.QtCore import QEvent, QObject, Qt

# Pixels of horizontal drag per one singleStep() of value change --
# smaller means more sensitive. A field's own singleStep() (already
# tuned per-field elsewhere, e.g. 0.01 for a 0-1 CDL value, 1 for a
# percent) does the rest of the per-field sensitivity scaling for free.
_PIXELS_PER_STEP = 4

# Slows the drag while held, for fine adjustment (per Tom's own
# follow-up ask) -- Shift, the standard fine-control modifier in the
# same DCC tools this scrubbing convention is borrowed from.
_FINE_MODIFIER = Qt.KeyboardModifier.ShiftModifier
_FINE_DIVISOR = 10

# How far the mouse has to move before a press-then-move counts as a
# drag/scrub rather than an ordinary click -- which should still place a
# text cursor or select-all-to-type, Qt's own normal QLineEdit behavior.
# Below this threshold, every event is left alone (returns False) so
# that normal behavior is never interfered with.
_DRAG_THRESHOLD_PX = 4


class _ScrubFilter(QObject):
    def __init__(self, spin_box):
        super().__init__(spin_box)
        self._spin_box = spin_box
        self._dragging = False
        self._press_x = None
        self._press_value = None

    def eventFilter(self, obj, event):
        et = event.type()

        if et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._press_x = event.position().x()
            self._press_value = self._spin_box.value()
            self._dragging = False
            return False  # let the normal press (cursor placement) still happen

        if et == QEvent.Type.MouseMove and self._press_x is not None:
            delta_x = event.position().x() - self._press_x
            if not self._dragging and abs(delta_x) < _DRAG_THRESHOLD_PX:
                return False
            self._dragging = True
            step = self._spin_box.singleStep()
            if event.modifiers() & _FINE_MODIFIER:
                step /= _FINE_DIVISOR
            # Always relative to the ORIGINAL press position/value, not
            # the previous move event -- so the total scrub amount is
            # exactly proportional to total drag distance, with no
            # incremental rounding drift across many small move events.
            self._spin_box.setValue(self._press_value + (delta_x / _PIXELS_PER_STEP) * step)
            return True  # consume -- don't ALSO let this drag-select the text

        if et == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            was_dragging = self._dragging
            self._press_x = None
            self._press_value = None
            self._dragging = False
            return was_dragging  # consume only if this was actually a scrub, not a plain click

        return False


def enable_drag_to_scrub(spin_box):
    """Call once per QSpinBox/QDoubleSpinBox to opt it into click-drag
    scrubbing. Safe to call on any QAbstractSpinBox subclass -- only
    relies on .value()/.setValue()/.singleStep(), which every one of
    them provides."""
    filt = _ScrubFilter(spin_box)
    spin_box.lineEdit().installEventFilter(filt)
    # Hints that the field is horizontally scrubbable, the same way a
    # splitter handle's own cursor hints that IT can be dragged.
    spin_box.lineEdit().setCursor(Qt.CursorShape.SizeHorCursor)
