"""
curve_editor_widget.py -- CurveEditorWidget: a draggable-control-point
curve editor for a 'grading_rgb_curve' stage's four channel curves (see
grading_rgb_curve.py, and docs/archive/OCIO_TIER1_GRADING_OPERATORS_PLAN_2026-08-09.md's
Stage 5). Same "Qt-free math as plain functions + thin QWidget paint/drag
layer" split every other interactive control in this app uses (see
color_wheel_widget.py's own docstring) -- kept as free functions in THIS
file (not a fully separate module) since color_wheel_widget.py's own
_pos_to_unit/_unit_to_pos are the direct precedent for keeping this kind
of "widget-pixel <-> value-space" conversion in the widget file itself,
independently testable without constructing the widget.

RESEARCHED, AND DELIBERATELY NOT pyqtgraph-based -- a background research
agent read this app's actual installed pyqtgraph (0.14.0) source and
confirmed it has no usable 2D curve-editor primitive: GradientEditorItem/
TickSliderItem is strictly 1D (color ticks along one axis, no Y-drag),
PolyLineROI has draggable handles + click-to-add/right-click-to-remove but
only ever renders straight line segments -- no spline/Bezier/Catmull-Rom
utility exists anywhere in the package. Hand-rolled here instead, using
this app's own established custom-QWidget-with-QPainter convention rather
than pulling in QGraphicsView/QGraphicsScene, which this app doesn't use
for any other interactive control.

ONE ACTIVE CHANNEL AT A TIME -- red/green/blue/master curves are all held
here at once (so switching channels, or a caller reading every channel
back for a Stack param write, never loses the other three), but only the
ACTIVE channel's control points are draggable; the other three paint as
dimmed, non-interactive reference curves in their own channel color so the
user can see how channels relate without a channel they're not editing
cluttering the drag targets. A caller writes stage.params[channel] from
get_points(channel) for ALL FOUR channels on every pointsChanged emission
-- same "write the entire param block back on every edit" convention
grading_tone's 5 ranges already follow in stack_panel.py.

CURVE-SPACE DOMAIN is exactly [0, 1] on both axes (DOMAIN_MIN/DOMAIN_MAX)
-- a control point can never be dragged or added outside the box it's
drawn in, confirmed live: an earlier revision let points overshoot into a
surrounding margin area that was still technically inside the widget but
visually outside the drawn graph box, which read as "not actually
constrained." grading_rgb_curve.py itself never clamps point values (log/
lin OCIO working spaces can legitimately need points outside [0, 1]), but
this widget's own draggable range is a deliberately simpler, strictly
bounded UI choice -- a user who needs an out-of-[0,1] curve point can
still set one by editing a session file directly; this widget doesn't
need to be the only way to reach that value.

CATMULL-ROM -> BEZIER (catmull_rom_bezier_segments, standard tension=1/6
conversion) is a VISUAL approximation only -- the real curve math for the
actual color transform is OCIO's own GradingBSplineCurve, evaluated
separately (CPU: grading_rgb_curve.py; GPU: gpu_stack_renderer.py's
grading_rgb_curve branch) from whatever this widget reports back via
pointsChanged. The two are not required to be pixel-identical -- this
widget exists for interaction feedback, not a preview render.

NOT VISUALLY VERIFIED IN THIS ENVIRONMENT -- same situation as
color_wheel_widget.py's own docstring: the underlying math (widget-pixel
<-> curve-space conversion, neighbor-bound clamping, hit-testing,
Catmull-Rom segment generation) is exercised headlessly via real
QWidget/QPainter/QMouseEvent objects, but whether dragging actually FEELS
right, and whether the curve reads clearly at a real size, needs a real
display and a real mouse.
"""
import bisect

from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPen, QColor, QBrush
from PySide6.QtWidgets import QWidget, QSizePolicy

import grading_rgb_curve as grc

DOMAIN_MIN = 0.0
DOMAIN_MAX = 1.0
_DOMAIN_SPAN = DOMAIN_MAX - DOMAIN_MIN

# Minimum enforced x gap between neighboring control points, in curve
# space -- keeps every segment a well-formed, non-degenerate x range for
# the Catmull-Rom conversion, and keeps the points list ALWAYS sorted
# during a drag (see _neighbor_x_bounds) rather than needing a mid-drag
# re-sort that would leave the dragged index stale.
_MIN_X_SPACING = 0.001

POINT_RADIUS = 5.0
HIT_RADIUS = 9.0  # a bit larger than the painted radius -- forgiving click target
MARGIN = 14.0  # widget-pixel margin around the plot area, room for a point sitting at the domain edge

CHANNEL_ORDER = ("red", "green", "blue", "master")
CHANNEL_COLORS = {
    "red": QColor(224, 90, 90),
    "green": QColor(90, 200, 110),
    "blue": QColor(100, 140, 230),
    "master": QColor(225, 225, 225),
}
CHANNEL_LABELS = {"red": "R", "green": "G", "blue": "B", "master": "M"}


def _plot_rect(widget_rect):
    """widget_rect: QRectF of the whole widget -- the inset QRectF the
    curve actually plots into, leaving MARGIN room so a point sitting at
    DOMAIN_MIN/DOMAIN_MAX isn't painted half off the widget."""
    return QRectF(widget_rect.left() + MARGIN, widget_rect.top() + MARGIN,
                  max(1.0, widget_rect.width() - 2 * MARGIN),
                  max(1.0, widget_rect.height() - 2 * MARGIN))


def _curve_to_widget(point, plot_rect):
    """(x, y) curve-space -> QPointF widget-space. Y is FLIPPED (curve
    value DOMAIN_MAX paints at the TOP of the plot -- the standard graph
    convention, opposite of Qt's own +y-down widget pixels)."""
    x, y = point
    frac_x = (x - DOMAIN_MIN) / _DOMAIN_SPAN
    frac_y = (y - DOMAIN_MIN) / _DOMAIN_SPAN
    px = plot_rect.left() + frac_x * plot_rect.width()
    py = plot_rect.top() + (1.0 - frac_y) * plot_rect.height()
    return QPointF(px, py)


def _widget_to_curve(pos, plot_rect):
    """QPointF widget-space -> (x, y) curve-space, NOT clamped to
    [DOMAIN_MIN, DOMAIN_MAX] -- callers that need a clamped value (e.g. a
    freshly placed point) call _clamp_point separately."""
    frac_x = (pos.x() - plot_rect.left()) / plot_rect.width()
    frac_y = 1.0 - (pos.y() - plot_rect.top()) / plot_rect.height()
    x = DOMAIN_MIN + frac_x * _DOMAIN_SPAN
    y = DOMAIN_MIN + frac_y * _DOMAIN_SPAN
    return (x, y)


def _clamp_point(x, y):
    return (min(max(x, DOMAIN_MIN), DOMAIN_MAX), min(max(y, DOMAIN_MIN), DOMAIN_MAX))


def _neighbor_x_bounds(points, index):
    """The [lo, hi] an already-sorted points[index]'s x may move within
    without crossing its immediate neighbors."""
    lo = DOMAIN_MIN if index == 0 else points[index - 1][0] + _MIN_X_SPACING
    hi = DOMAIN_MAX if index == len(points) - 1 else points[index + 1][0] - _MIN_X_SPACING
    if hi < lo:  # neighbors already closer than 2*_MIN_X_SPACING -- pin in place rather than invert
        hi = lo
    return lo, hi


def sort_points(points):
    return sorted((float(x), float(y)) for x, y in points)


def insertion_index(points, x):
    """Where a new point at curve-space x would land in an already-sorted
    points list -- exposed as a plain function so both the widget's
    click-to-add handler and tests share the same "where does this x
    belong" logic."""
    return bisect.bisect_left([p[0] for p in points], x)


def catmull_rom_bezier_segments(points):
    """points: [(x, y), ...] sorted ascending by x, len >= 2. Returns a
    list of (c1, c2, p1) triples (each a plain (x, y) tuple) -- the cubic
    Bezier control/end points for each segment after the path's initial
    moveTo(points[0]), in curve space. Standard tension=1/6 Catmull-Rom ->
    Bezier conversion; endpoints reuse their own adjacent point as the
    missing tangent source (clamped, not looping)."""
    n = len(points)
    segments = []
    for i in range(n - 1):
        p0 = points[i - 1] if i - 1 >= 0 else points[i]
        p1 = points[i]
        p2 = points[i + 1]
        p3 = points[i + 2] if i + 2 < n else points[i + 1]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6.0, p1[1] + (p2[1] - p0[1]) / 6.0)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6.0, p2[1] - (p3[1] - p1[1]) / 6.0)
        segments.append((c1, c2, p2))
    return segments


class CurveEditorWidget(QWidget):
    """pointsChanged(channel: str) fires on every drag step, add, remove,
    and per-channel reset -- continuous "apply live" semantics, same
    convention this app's sliders already follow (e.g. fork_merge's blend
    slider). Callers read the changed channel (and, per this app's own
    "write the whole param block back" convention, every other channel
    too) via get_points."""

    pointsChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._points = {ch: sort_points(grc.DEFAULT_CONTROL_POINTS) for ch in CHANNEL_ORDER}
        self._active_channel = "master"
        self._drag_index = None

    # ---- public API -------------------------------------------------

    def set_active_channel(self, channel):
        if channel not in CHANNEL_ORDER:
            raise ValueError(f"Unknown curve channel: {channel!r}")
        self._active_channel = channel
        self._drag_index = None
        self.update()

    def active_channel(self):
        return self._active_channel

    def set_points(self, channel, points):
        """External setter -- does NOT emit pointsChanged (mirrors every
        other inspector's _refresh_inspector_body convention: driving
        widgets to reflect a newly selected stage is not itself a live
        edit). Falls back to the identity curve if fewer than
        grading_rgb_curve.MIN_CONTROL_POINTS are given, matching that
        module's own _normalize_control_points so what this widget SHOWS
        never disagrees with what apply_grading_rgb_curve would actually
        do with the same input.

        Does NOT unconditionally clear an in-progress drag. stack_panel.py
        calls this from _refresh_inspector_body, which every OTHER live-
        apply handler in that file also re-enters on its OWN edits (see
        _refresh_list's own docstring/comment on why it unconditionally
        calls _on_selection_changed) -- so a mouseMoveEvent here that
        emits pointsChanged synchronously triggers a re-entrant set_points
        call on THIS SAME channel, mid-drag, with the exact points this
        drag itself just produced. Blowing away _drag_index on every such
        echo made a drag move one pixel and then silently stop responding
        (mouseMoveEvent bails out immediately once _drag_index is None) --
        confirmed live. Only reset it if it's no longer a valid index into
        the new list (e.g. a genuinely different stage's shorter curve)."""
        points = sort_points(points)
        if len(points) < grc.MIN_CONTROL_POINTS:
            points = sort_points(grc.DEFAULT_CONTROL_POINTS)
        self._points[channel] = points
        if (channel == self._active_channel and self._drag_index is not None
                and self._drag_index >= len(points)):
            self._drag_index = None
        self.update()

    def get_points(self, channel):
        return list(self._points[channel])

    def reset_active_channel(self):
        """Resets ONLY the active channel to the identity curve, and DOES
        emit pointsChanged (unlike set_points) -- a user-initiated edit
        (the Reset button), same "setValue fires the same live-apply path
        as typing would" convention every other kind's Reset to Identity
        button already follows (see e.g. _on_ec_reset_clicked in
        stack_panel.py)."""
        channel = self._active_channel
        self._points[channel] = sort_points(grc.DEFAULT_CONTROL_POINTS)
        self._drag_index = None
        self.update()
        self.pointsChanged.emit(channel)

    # ---- painting -----------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        plot_rect = _plot_rect(rect)

        painter.fillRect(rect, self.palette().base())

        # Border framing the plot area -- this box IS the entire draggable
        # domain (DOMAIN_MIN..DOMAIN_MAX is exactly [0,1], see this
        # module's own docstring), so it doubles as the visual "you can't
        # drag a point past here" boundary.
        painter.setPen(QPen(QColor(255, 255, 255, 40), 1))
        painter.drawRect(plot_rect)

        # Identity reference line (0,0)->(1,1).
        painter.setPen(QPen(QColor(255, 255, 255, 55), 1, Qt.PenStyle.DashLine))
        painter.drawLine(_curve_to_widget((0.0, 0.0), plot_rect), _curve_to_widget((1.0, 1.0), plot_rect))

        # Dimmed reference curves for the three non-active channels.
        for channel in CHANNEL_ORDER:
            if channel == self._active_channel:
                continue
            color = QColor(CHANNEL_COLORS[channel])
            color.setAlpha(70)
            self._paint_curve(painter, self._points[channel], plot_rect, color, 1.5, with_points=False)

        # Active channel's curve + its draggable points, painted last so
        # they're always on top.
        self._paint_curve(painter, self._points[self._active_channel], plot_rect,
                           CHANNEL_COLORS[self._active_channel], 2.0, with_points=True)

        painter.end()

    @staticmethod
    def _paint_curve(painter, points, plot_rect, color, width, with_points):
        path = QPainterPath()
        path.moveTo(_curve_to_widget(points[0], plot_rect))
        for c1, c2, p1 in catmull_rom_bezier_segments(points):
            path.cubicTo(_curve_to_widget(c1, plot_rect), _curve_to_widget(c2, plot_rect),
                         _curve_to_widget(p1, plot_rect))
        painter.setPen(QPen(color, width))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        if with_points:
            painter.setBrush(QBrush(color))
            painter.setPen(QPen(QColor(20, 20, 20, 220), 1))
            for point in points:
                painter.drawEllipse(_curve_to_widget(point, plot_rect), POINT_RADIUS, POINT_RADIUS)

    # ---- mouse interaction ---------------------------------------------

    def _hit_test(self, pos):
        """Index of the active channel's control point under widget pos
        `pos` (a QPointF), within HIT_RADIUS, or None. Checked in REVERSE
        index order so a later (higher-x) point wins ties over an earlier
        one sitting at nearly the same pixel -- arbitrary but
        deterministic."""
        plot_rect = _plot_rect(QRectF(self.rect()))
        points = self._points[self._active_channel]
        for i in reversed(range(len(points))):
            center = _curve_to_widget(points[i], plot_rect)
            dx = center.x() - pos.x()
            dy = center.y() - pos.y()
            if dx * dx + dy * dy <= HIT_RADIUS * HIT_RADIUS:
                return i
        return None

    def mousePressEvent(self, event):
        pos = event.position()
        index = self._hit_test(pos)
        if event.button() == Qt.MouseButton.RightButton:
            if index is not None:
                self._remove_point(index)
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if index is not None:
            self._drag_index = index
            event.accept()
            return
        # A plain click on empty plot area (not on an existing point) adds
        # a new control point right there and immediately starts dragging
        # it -- a single click-and-drag both places and positions a point
        # in one motion, the standard curve-tool gesture. An earlier
        # revision only supported this via double-click, which wasn't a
        # discoverable way to add a point (confirmed live).
        new_index = self._insert_point_at(pos)
        if new_index is not None:
            self._drag_index = new_index
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_index is None:
            return
        plot_rect = _plot_rect(QRectF(self.rect()))
        x, y = _widget_to_curve(event.position(), plot_rect)
        points = self._points[self._active_channel]
        lo, hi = _neighbor_x_bounds(points, self._drag_index)
        x = min(max(x, lo), hi)
        y = min(max(y, DOMAIN_MIN), DOMAIN_MAX)
        points[self._drag_index] = (x, y)
        self.update()
        self.pointsChanged.emit(self._active_channel)

    def mouseReleaseEvent(self, event):
        self._drag_index = None

    def _insert_point_at(self, pos):
        """Adds a new control point to the ACTIVE channel at widget pos
        `pos` (clamped into the domain) -- returns its new index, or None
        if it landed too close to an existing point's x to insert without
        creating a near-zero-width (degenerate) segment, in which case
        nothing is added."""
        plot_rect = _plot_rect(QRectF(self.rect()))
        x, y = _clamp_point(*_widget_to_curve(pos, plot_rect))
        points = self._points[self._active_channel]
        idx = insertion_index(points, x)
        if idx > 0 and abs(points[idx - 1][0] - x) < _MIN_X_SPACING:
            return None
        if idx < len(points) and abs(points[idx][0] - x) < _MIN_X_SPACING:
            return None
        points.insert(idx, (x, y))
        self.update()
        self.pointsChanged.emit(self._active_channel)
        return idx

    def _remove_point(self, index):
        points = self._points[self._active_channel]
        if len(points) <= grc.MIN_CONTROL_POINTS:
            return  # never drop below the minimum -- see grading_rgb_curve.MIN_CONTROL_POINTS
        del points[index]
        self._drag_index = None
        self.update()
        self.pointsChanged.emit(self._active_channel)
