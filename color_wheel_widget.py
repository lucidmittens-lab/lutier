"""
color_wheel_widget.py -- ColorBalanceWheel: the draggable-puck circular
control for a color-balance parameter (see color_balance.py for the actual
math -- this file is the thin Qt paint/drag layer on top of it, same
"Qt-free math module + Qt widget wrapper" split as every other interactive
widget in this app, e.g. wipe_compare_widget.py wraps its own geometry
helpers).

Geometry conversion (widget pixel position <-> normalized (x,y) in [-1,1])
is exposed as plain functions (_pos_to_unit/_unit_to_pos) so tests can
exercise the exact same math the mouse handlers use without needing to
synthesize real mouse events -- mirroring wipe_compare_widget.py's
_update_wipe_from_x being called directly in that widget's own tests.

NOT VISUALLY VERIFIED IN THIS ENVIRONMENT -- same PySide6/offscreen-Qt
situation as wipe_compare_widget.py: the underlying math (balance
projection, geometry conversion, signal emission) is exercised headlessly
via real QWidget/QPainter/QMouseEvent objects (this sandbox's Qt DOES
construct and paint under QT_QPA_PLATFORM=offscreen -- see
docs/archive/KNOWN_GOOD_SUMMARY_2026-07-23.md), but whether the gradient actually
*looks* like a usable color wheel, and whether dragging feels right, needs
a real display and a real mouse. Please drag it around and say whether the
hue/desaturation gradient and puck size read clearly before treating the
visual design as settled.
"""
import math

from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import QPainter, QPen, QColor, QConicalGradient, QRadialGradient
from PySide6.QtWidgets import QWidget, QSizePolicy

import color_balance as cb

DEFAULT_DIAMETER = 96
PUCK_RADIUS = 6
RING_COLOR = QColor(90, 90, 90)
CENTER_MARK_COLOR = QColor(255, 255, 255, 160)
PUCK_FILL = QColor(255, 255, 255, 235)
PUCK_BORDER = QColor(20, 20, 20, 220)

# Hue stops at each axis angle (see color_balance.py's _AXIS_ANGLES_DEG:
# R=90 deg, G=210 deg, B=330 deg) plus the complementary secondaries
# halfway between each pair, so the ring reads as a normal RGB/CMY color
# wheel rather than three bare primaries with dead space between them.
# QConicalGradient's angle is measured counterclockwise from the
# 3-o'clock position, matching the same convention color_balance.py uses.
_HUE_STOPS = [
    (90, QColor(255, 60, 60)),    # R
    (150, QColor(255, 230, 60)),  # R/G -> yellow
    (210, QColor(60, 255, 90)),   # G
    (270, QColor(60, 230, 255)),  # G/B -> cyan
    (330, QColor(90, 90, 255)),   # B
    (30, QColor(230, 60, 255)),   # B/R -> magenta
    (90, QColor(255, 60, 60)),    # wrap back to R
]


def _pos_to_unit(pos, rect):
    """Widget-local QPointF -> normalized (x, y) in the wheel's own
    coordinate system: origin at the wheel's center, +x right, +y UP
    (screen y is flipped since Qt's pixel y grows downward but
    color_balance.py's angles assume the usual math convention), NOT yet
    clamped to the unit circle."""
    cx, cy = rect.center().x(), rect.center().y()
    radius = rect.width() / 2.0
    if radius <= 0:
        return 0.0, 0.0
    x = (pos.x() - cx) / radius
    y = -(pos.y() - cy) / radius
    return x, y


def _unit_to_pos(x, y, rect):
    """Inverse of _pos_to_unit -- normalized (x,y) -> widget-local
    QPointF, for drawing the puck."""
    cx, cy = rect.center().x(), rect.center().y()
    radius = rect.width() / 2.0
    return QPointF(cx + x * radius, cy - y * radius)


class ColorBalanceWheel(QWidget):
    """A single color-balance wheel. `balance_changed(x, y)` fires with the
    new normalized position (each in [-1,1], clamped to the unit circle)
    whenever the puck moves, whether by drag or by set_balance(). Double-
    click recenters (balance (0,0)), same convention as
    WipeCompareWidget.mouseDoubleClickEvent resetting to 0.5."""

    balance_changed = Signal(float, float)

    def __init__(self, diameter=DEFAULT_DIAMETER, parent=None):
        super().__init__(parent)
        self._x = 0.0
        self._y = 0.0
        self._dragging = False
        self.setFixedSize(diameter, diameter)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def balance(self):
        return self._x, self._y

    def set_balance(self, x, y, emit=True):
        """Programmatic move (e.g. syncing FROM a spinbox edit) -- clamps
        to the unit circle same as a drag would. `emit=False` lets callers
        that are themselves reacting to balance_changed update the widget
        without re-triggering their own handler (the same blockSignals-
        during-sync convention used throughout stack_panel.py, just as a
        parameter here since this is a plain method, not a Qt signal
        connection, on the more common call path)."""
        self._x, self._y = cb.clamp_to_unit_circle(x, y)
        self.update()
        if emit:
            self.balance_changed.emit(self._x, self._y)

    def reset(self):
        self.set_balance(0.0, 0.0)

    def mouseDoubleClickEvent(self, event):
        self.reset()

    # ---- geometry -------------------------------------------------------

    def _wheel_rect(self):
        side = min(self.width(), self.height()) - PUCK_RADIUS * 2
        x = (self.width() - side) / 2.0
        y = (self.height() - side) / 2.0
        return QRectF(x, y, side, side)

    # ---- painting -------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self._wheel_rect()
        center = rect.center()
        radius = rect.width() / 2.0

        hue_gradient = QConicalGradient(center, 0.0)
        # QConicalGradient stops are in [0,1], mapped from our
        # counterclockwise-from-3-o'clock degrees, and must be
        # non-decreasing -- the wrap-around final 90 deg stop is emitted
        # as 1.0 exactly to close the ring without a seam.
        for i, (angle_deg, color) in enumerate(_HUE_STOPS):
            stop = 1.0 if i == len(_HUE_STOPS) - 1 else (angle_deg % 360) / 360.0
            hue_gradient.setColorAt(stop, color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(hue_gradient)
        painter.drawEllipse(rect)

        # Desaturate toward the center (a real color wheel is gray/neutral
        # at its middle -- full saturation only at the rim) via a white
        # radial gradient fading out before the edge.
        desaturate = QRadialGradient(center, radius)
        desaturate.setColorAt(0.0, QColor(255, 255, 255, 235))
        desaturate.setColorAt(0.55, QColor(255, 255, 255, 60))
        desaturate.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(desaturate)
        painter.drawEllipse(rect)

        painter.setPen(QPen(RING_COLOR, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(rect)

        painter.setPen(QPen(CENTER_MARK_COLOR, 1))
        painter.drawLine(QPointF(center.x() - 4, center.y()), QPointF(center.x() + 4, center.y()))
        painter.drawLine(QPointF(center.x(), center.y() - 4), QPointF(center.x(), center.y() + 4))

        puck_pos = _unit_to_pos(self._x, self._y, rect)
        painter.setPen(QPen(PUCK_BORDER, 1.5))
        painter.setBrush(PUCK_FILL)
        painter.drawEllipse(puck_pos, PUCK_RADIUS, PUCK_RADIUS)

        painter.end()

    # ---- mouse interaction ------------------------------------------

    def mousePressEvent(self, event):
        self._dragging = True
        self._update_from_widget_pos(event.position())

    def mouseMoveEvent(self, event):
        if self._dragging:
            self._update_from_widget_pos(event.position())

    def mouseReleaseEvent(self, event):
        self._dragging = False

    def _update_from_widget_pos(self, widget_pos):
        rect = self._wheel_rect()
        x, y = _pos_to_unit(widget_pos, rect)
        self.set_balance(x, y)
