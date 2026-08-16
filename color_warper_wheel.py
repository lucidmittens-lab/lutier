"""
color_warper_wheel.py -- WarpierWheel: the draggable-multi-point warp
control for WARPier (see color_warper.py for the actual OKLCh math --
this file is the thin Qt paint/drag layer on top of it, same "Qt-free
math module + Qt widget wrapper" split as color_wheel_widget.py/
ColorBalanceWheel).

Unlike ColorBalanceWheel's single fixed puck, WarpierWheel holds a
VARIABLE number of points (color_warper.MAX_POINTS cap): click empty
space to add one (the same press-drag-release gesture that creates it can
immediately reposition it), drag an existing point both angularly (hue)
and radially (chroma -- toward the center = white/neutral, away = more
saturated), right-click to remove. Geometry conversion (widget pixel <->
hue/chroma) is exposed as plain module-level functions so tests can
exercise the exact math the mouse handlers use, same testability
convention as color_wheel_widget.py's _pos_to_unit/_unit_to_pos.

Oriented so green points toward the bottom of the wheel and magenta
toward the top (per Tom) -- GREEN_HUE/HUE_ROTATE (imported from
color_warper.py, which is Qt-free -- see that module's own comment on
why they live there, not here) are picked so GREEN_HUE (OKLCh green's
own approximate hue angle) lands at screen-down. A plain uniform rotation
(NOT the piecewise-linear "regular diamond corner" remap an earlier
version of this widget used) -- per Tom's own course-correction away from
a diamond shape toward "something a little more like the spider web of
Resolve's color warper": a real vectorscope-style tool doesn't force its
hue wheel into artificially regular spacing either, it just uses a plain
hue rotation and lets its target markers land wherever their own real
hues fall (see VECTORSCOPE_TARGETS/_draw_vectorscope_graticule below).

DEFORMABLE SPIDER-WEB MESH (per Tom, after seeing a validated concept
mockup): _draw_mesh below draws a polar grid (MESH_RINGS concentric
rings x MESH_SPOKES radial spokes) and DEFORMS it by running each grid
vertex's own (hue, chroma) through the REAL color_warper.warp_hue_chroma
-- the exact same function the actual grade uses on real pixels -- so the
mesh always shows PRECISELY what the current wake/dropoff/points are
doing, not a separate approximate visualization. This replaces the
earlier flat translucent wake/dropoff "wash" overlay entirely: the
deforming mesh IS the visualization now.

RESOLVE-STYLE VECTORSCOPE GRATICULE (per Tom -- "make sure you have some
display of the colors underneath. and maybe a vector scope underlay like
in resolve too"): a dashed reference ring plus six small target boxes
for the standard R/G/B/C/M/Y vectorscope primaries/secondaries
(color_warper.RED_HUE/GREEN_HUE/BLUE_HUE/CYAN_HUE/MAGENTA_HUE/
YELLOW_HUE -- each hue's own real OKLCh angle, same derivation as
GREEN_HUE), purely decorative/reference (this widget has no live image
data flowing through it -- LUTier's own real Vectorscope, built from
actual pixels, lives in scopes_panel.py and is unrelated).

Each point stores ABSOLUTE OKLCh chroma (see color_warper.py's own
docstring on why), but the WIDGET lets you drag it as a natural "fraction
of a shared reference chroma, at a fixed DISPLAY_L" gesture -- placement
math is a single linear scale (see chroma_to_pixel_radius/
pixel_radius_to_chroma) against self._max_boundary, a FIXED reference,
never recomputed from the current points. An earlier version tried
auto-expanding this scale to cover whatever the current warp produced,
to avoid clipping on an extreme drag -- but rescaling the WHOLE display
in response to one changing point's own chroma is a feedback loop
(interpreting mouse input through a scale the drag itself was changing
made chroma grow geometrically without bound, confirmed directly) and,
even after fixing that specific feedback, still LOOKED unstable (per
Tom: "its still completely pulling itself apart, just slower now" --
rescaling everything else on screen every time one point changes is
inherently unsettling to watch, whether or not the underlying numbers
are technically bounded). Fixed scale, always: instead,
chroma_to_pixel_radius clamps at the DISPLAY-conversion layer, so an
extreme drag compresses just that one point/mesh region near the rim
rather than moving anything else.

FULLY POPULATED COLOR FILL (per Tom -- "the entirety of the round spider
web should be populated by color... there is still a diamond in the
center"): _build_fill_pixmap normalizes EVERY hue's own real gamut
boundary to reach the full disc radius (an ordinary HSL/HSV-style color
wheel already works this way) rather than placing each hue's real chroma
at its own absolute fraction of a single shared scale, which left
visible gaps (reading as a "diamond"/star-shaped hole) in every
direction whose own real max chroma fell short of the single strongest
hue's. Precomputed once into a cached QPixmap (color_warper's own OKLab/
OKLCh conversion functions decide which colors are in-gamut -- the SAME
math the real grade uses, not a separate approximation) since it never
changes for a fixed DISPLAY_L and is now fully decoupled from the point/
mesh chroma scale, so dragging a point repaints only the cheap dynamic
overlay (mesh, points, graticule) on top of it, not the whole fill.

NOT VISUALLY VERIFIED IN THIS ENVIRONMENT -- same PySide6/offscreen-Qt
situation as color_wheel_widget.py: exercised headlessly via real
QWidget/QPainter/QMouseEvent objects, but whether the web, graticule, and
drag feel read clearly needs a real display and a real mouse.
"""
import math

from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import QPainter, QPen, QColor, QPixmap, QPolygonF
from PySide6.QtWidgets import QWidget, QSizePolicy

import color_warper as cw
from color_warper import GREEN_HUE, HUE_ROTATE

DEFAULT_DIAMETER = 220
POINT_RADIUS = 7

DISPLAY_L = 0.72
BOUNDARY_STEPS = 360
DISC_RING_STEPS = 16
DISC_ANGLE_STEP_DEG = 2

MESH_RINGS = 5
MESH_BASE_SPOKE_COUNT = 18
MESH_COLOR = QColor(170, 190, 255, 110)

GRATICULE_BOX_SIZE = 9.0
GRATICULE_COLOR = QColor(255, 255, 255, 235)

# The six standard vectorscope primary/secondary targets, in hue order --
# label, own real OKLCh hue (color_warper.py). Purely decorative
# reference markers (see this module's own docstring).
VECTORSCOPE_TARGETS = (
    ("R", cw.RED_HUE),
    ("Y", cw.YELLOW_HUE),
    ("G", cw.GREEN_HUE),
    ("C", cw.CYAN_HUE),
    ("B", cw.BLUE_HUE),
    ("M", cw.MAGENTA_HUE),
)


def _mesh_spoke_hues():
    """MESH_BASE_SPOKE_COUNT evenly-spaced hues, merged with the six
    vectorscope primary/secondary hues (VECTORSCOPE_TARGETS) so the
    web's own spokes land EXACTLY on R/Y/G/C/B/M (per Tom: "the color
    background should put the primaries and secondaries on the web") --
    still full 360-degree coverage ("the web should cover the whole
    spectrum"), just with six of the spokes pinned to known reference
    hues instead of being purely evenly spaced. Sorted, deduplicated
    (rounded to 6 decimals -- a primary landing exactly on a base spoke
    is vanishingly unlikely but not worth a near-duplicate line if it
    ever does)."""
    base = [i * 360.0 / MESH_BASE_SPOKE_COUNT for i in range(MESH_BASE_SPOKE_COUNT)]
    primaries = [hue for _label, hue in VECTORSCOPE_TARGETS]
    merged = sorted({round(h, 6) for h in base} | {round(h, 6) for h in primaries})
    return merged


_MESH_SPOKE_HUES = _mesh_spoke_hues()
MESH_SPOKES = len(_MESH_SPOKE_HUES)

CENTER_MARK_COLOR = QColor(255, 255, 255, 130)
POINT_FILL = QColor(255, 255, 255, 240)
POINT_BORDER = QColor(20, 20, 20, 220)
ANCHOR_MARK_COLOR = QColor(255, 255, 255, 100)


def hue_to_screen_angle(hue_deg):
    """Hue degrees -> screen angle in radians (0 = 3-o'clock, angle grows
    clockwise since Qt's own y axis grows downward) -- see HUE_ROTATE's
    own module-level comment for the rotation offset."""
    return math.radians(hue_deg + HUE_ROTATE)


def screen_angle_to_hue(angle_rad):
    """Inverse of hue_to_screen_angle."""
    deg = (math.degrees(angle_rad) - HUE_ROTATE) % 360.0
    # A near-zero NEGATIVE deg before the modulo (e.g. -7e-15, from the
    # radians/degrees round trip at hue=0) can round to exactly 360.0
    # rather than ~360-epsilon -- float64 simply can't represent a value
    # that close to 360 as anything other than 360.0 itself. Confirmed
    # directly (deg=-7.105427357601002e-15 -> deg % 360.0 == 360.0
    # exactly, not 359.999...), so this guard is a real correctness fix,
    # not just test tidiness -- storing 360.0 instead of 0.0 would drift
    # this point out of the [0, 360) range every other hue value uses.
    if deg >= 360.0:
        deg -= 360.0
    return deg


def compute_gamut_boundary(l=DISPLAY_L, steps=BOUNDARY_STEPS):
    """A `steps`-entry table (one per whole degree) of the max OKLCh
    chroma still in gamut at lightness `l` -- used only for the wheel's
    OWN visual color fill/point placement (a fixed reference lightness),
    never for the real grade math, which operates on a pixel's ACTUAL
    lightness (see color_warper.py's own docstring on why a point's
    chroma is stored absolute, not as a fraction of any display-only
    boundary like this one)."""
    boundary = [0.0] * steps
    step_size = 0.004
    ceiling = 0.4
    for deg in range(steps):
        c = 0.0
        while c + step_size < ceiling and cw.is_in_gamut(l, c + step_size, float(deg)):
            c += step_size
        boundary[deg] = c
    return boundary


def boundary_chroma_at(boundary, hue_deg):
    return boundary[int(round(hue_deg)) % len(boundary)]


def chroma_to_pixel_radius(chroma, max_boundary, disc_radius_px):
    """Clamped to [0, disc_radius_px] -- a point's/mesh vertex's STORED
    chroma is intentionally allowed to exceed max_boundary (see
    color_warper.py's own docstring on why it's absolute, not
    display-clamped: warp_hue_chroma's chroma_scale can genuinely push a
    warped value past this display-only reference), but drawing it
    unclamped pushed points and web mesh lines past the widget's own
    pixel bounds -- "cut off" (per Tom). Clamping HERE (the display
    conversion) rather than at the data layer keeps the real grade math
    exact while keeping every visual element inside the wheel."""
    if max_boundary <= 0:
        return 0.0
    return max(0.0, min((chroma / max_boundary) * disc_radius_px, disc_radius_px))


def pixel_radius_to_chroma(pixel_radius, max_boundary, disc_radius_px):
    if disc_radius_px <= 0:
        return 0.0
    return (pixel_radius / disc_radius_px) * max_boundary


def hue_chroma_to_pos(hue_deg, chroma, max_boundary, rect):
    """(hue, absolute chroma) -> widget-local QPointF."""
    center = rect.center()
    radius = rect.width() / 2.0
    r_px = chroma_to_pixel_radius(chroma, max_boundary, radius)
    angle = hue_to_screen_angle(hue_deg)
    return QPointF(center.x() + math.cos(angle) * r_px, center.y() + math.sin(angle) * r_px)


def pos_to_hue_chroma(pos, max_boundary, rect):
    """Inverse of hue_chroma_to_pos -- widget-local QPointF -> (hue, absolute chroma)."""
    center = rect.center()
    radius = rect.width() / 2.0
    dx, dy = pos.x() - center.x(), pos.y() - center.y()
    hue = screen_angle_to_hue(math.atan2(dy, dx))
    chroma = pixel_radius_to_chroma(math.hypot(dx, dy), max_boundary, radius)
    return hue, chroma


def build_mesh_grid(max_boundary):
    """Base (unwarped) hue/chroma grid for the spider-web mesh -- two
    equal-length plain lists (H, C), MESH_SPOKES hue-degree columns (see
    _mesh_spoke_hues -- evenly spaced, PLUS the six vectorscope primary/
    secondary hues merged in) x MESH_RINGS evenly-spaced chroma rows
    (fractions of max_boundary), ring-major order. Plain Python, not
    numpy -- small enough (MESH_RINGS*MESH_SPOKES points) that a real
    numpy dependency buys nothing here, and warp_hue_chroma
    (color_warper.py) already accepts plain lists (converts
    internally)."""
    hues = _MESH_SPOKE_HUES
    chromas = [i / MESH_RINGS * max_boundary for i in range(1, MESH_RINGS + 1)]
    H = [h for c in chromas for h in hues]
    C = [c for c in chromas for h in hues]
    return H, C


class WarpierWheel(QWidget):
    """The WARPier warp wheel. `points_changed(list)` fires with the
    whole points list (each a plain {"h0","c0","h","c"} dict) whenever a
    point is added, dragged, or removed. `wake`/`dropoff` are set from
    outside (the inspector's own spinboxes drive them) purely to keep the
    mesh's deformation in sync -- this widget never edits them itself."""

    points_changed = Signal(list)

    def __init__(self, diameter=DEFAULT_DIAMETER, parent=None):
        super().__init__(parent)
        # No seeded starting points (per Tom) -- an empty list is already
        # an exact identity/no-op (points_are_identity is vacuously true
        # for it), so this starts the wheel truly blank, not warping
        # anything until the user actually clicks to add a point.
        self._points = []
        self._wake = cw.DEFAULT_WAKE
        self._dropoff = cw.DEFAULT_DROPOFF
        self._chroma_wake = cw.DEFAULT_CHROMA_WAKE
        self._chroma_dropoff = cw.DEFAULT_CHROMA_DROPOFF
        self._dragging_point = None
        self._boundary = compute_gamut_boundary()
        self._max_boundary = max(self._boundary) if self._boundary else 0.0
        self._fill_pixmap = None
        self.setFixedSize(diameter, diameter)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    # ---- points / wake / dropoff access --------------------------------

    def points(self):
        return [dict(p) for p in self._points]

    def set_points(self, points, emit=True):
        self._points = [dict(p) for p in points]
        self.update()
        if emit:
            self.points_changed.emit(self.points())

    def set_wake(self, wake):
        self._wake = wake
        self.update()

    def set_dropoff(self, dropoff):
        self._dropoff = dropoff
        self.update()

    def set_chroma_wake(self, chroma_wake):
        self._chroma_wake = chroma_wake
        self.update()

    def set_chroma_dropoff(self, chroma_dropoff):
        self._chroma_dropoff = chroma_dropoff
        self.update()

    def reset(self):
        self.set_points([])

    # ---- geometry -------------------------------------------------------

    def _wheel_rect(self):
        side = min(self.width(), self.height()) - POINT_RADIUS * 2
        x = (self.width() - side) / 2.0
        y = (self.height() - side) / 2.0
        return QRectF(x, y, side, side)

    def _compute_frame_state(self):
        """Everything paintEvent needs for the CURRENT warp: `(H2, C2)`,
        the mesh's own warped hue/chroma arrays. Scale is ALWAYS
        self._max_boundary -- a fixed reference, never recomputed from
        the current points (see the module docstring, "FIXED SCALE,
        NEVER DYNAMIC" -- an earlier auto-expanding version made the
        entire wheel visibly rescale/spin as a single point was dragged,
        per Tom: "its still completely pulling itself apart, just
        slower now" -- rescaling the WHOLE display in response to ONE
        changing value is inherently unstable-looking regardless of
        whether the underlying numbers are mathematically bounded).
        Anything that would draw past the widget's own edge is clamped
        at the display-conversion layer instead (see
        chroma_to_pixel_radius) -- a fixed point/mesh vertex compressing
        near the rim on an extreme drag, not the whole wheel rescaling
        around it."""
        H, C = build_mesh_grid(self._max_boundary)
        H2, C2 = cw.warp_hue_chroma(H, C, self._points, self._wake, self._dropoff,
                                     self._chroma_wake, self._chroma_dropoff)
        return H2, C2

    def _point_pixel_pos(self, point, rect):
        return hue_chroma_to_pos(point["h"], point["c"], self._max_boundary, rect)

    def _nearest_point(self, widget_pos, rect):
        best, best_dist = None, POINT_RADIUS * 2.5
        for p in self._points:
            pos = self._point_pixel_pos(p, rect)
            dist = math.hypot(widget_pos.x() - pos.x(), widget_pos.y() - pos.y())
            if dist < best_dist:
                best, best_dist = p, dist
        return best

    # ---- painting -------------------------------------------------------

    def _build_fill_pixmap(self, side_px):
        """Per Tom: "the entirety of the round spider web should be
        populated by color" -- every hue's OWN gamut boundary (edge_chroma,
        varying per hue) is rescaled to reach the FULL disc radius here
        (r_outer always ends at `radius`), rather than each hue's real
        chroma being placed at its own absolute fraction of a single
        SHARED max_boundary scale (the earlier version) -- that left
        visible gaps in every direction whose own real max chroma fell
        short of the single strongest hue's, reading as a "diamond" (or
        star) shaped hole rather than a filled disc. The color painted at
        each ring is still the REAL, accurate color for that hue at that
        FRACTION of its own boundary (c_val = edge_chroma * fraction) --
        only the on-screen radius mapping is normalized per hue, same
        idea an ordinary HSL/HSV color wheel already relies on (each hue
        column reaches the rim by construction). This fill no longer
        depends on self._max_boundary at all -- it's a fixed decorative
        backdrop, entirely independent of the points/mesh."""
        pixmap = QPixmap(int(math.ceil(side_px)), int(math.ceil(side_px)))
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(side_px / 2.0, side_px / 2.0)
        radius = side_px / 2.0
        painter.setPen(Qt.PenStyle.NoPen)
        for deg in range(0, 360, DISC_ANGLE_STEP_DEG):
            edge_chroma = boundary_chroma_at(self._boundary, deg)
            a0 = hue_to_screen_angle(deg - DISC_ANGLE_STEP_DEG / 2.0)
            a1 = hue_to_screen_angle(deg + DISC_ANGLE_STEP_DEG / 2.0)
            for ring in range(DISC_RING_STEPS):
                r_inner = radius * ring / DISC_RING_STEPS
                r_outer = radius * (ring + 1) / DISC_RING_STEPS
                c_val = edge_chroma * (ring + 0.5) / DISC_RING_STEPS
                r, g, b = cw.oklab_to_linear_srgb(
                    cw.oklch_to_oklab(DISPLAY_L, c_val, float(deg)))
                r, g, b = (max(0, min(255, round(v * 255))) for v in (r, g, b))
                poly = QPolygonF([
                    QPointF(center.x() + math.cos(a0) * r_inner, center.y() + math.sin(a0) * r_inner),
                    QPointF(center.x() + math.cos(a0) * r_outer, center.y() + math.sin(a0) * r_outer),
                    QPointF(center.x() + math.cos(a1) * r_outer, center.y() + math.sin(a1) * r_outer),
                    QPointF(center.x() + math.cos(a1) * r_inner, center.y() + math.sin(a1) * r_inner),
                ])
                painter.setBrush(QColor(r, g, b))
                painter.drawPolygon(poly)
        painter.end()
        return pixmap

    def _draw_mesh(self, painter, rect, H2, C2):
        """The deformable spider-web overlay -- see this module's own
        docstring on why it's driven by the REAL warp_hue_chroma rather
        than a separate approximate falloff model. `H2`/`C2` come from
        paintEvent's own _compute_frame_state call rather than being
        recomputed here, so the warp math runs once per paint, not
        twice. Scale is always self._max_boundary (fixed, see
        _compute_frame_state's own docstring)."""
        center = rect.center()
        radius = rect.width() / 2.0

        positions = []
        for h, c in zip(H2, C2):
            angle = hue_to_screen_angle(float(h))
            r_px = chroma_to_pixel_radius(float(c), self._max_boundary, radius)
            positions.append(QPointF(center.x() + math.cos(angle) * r_px,
                                      center.y() + math.sin(angle) * r_px))

        painter.setPen(QPen(MESH_COLOR, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for ring in range(MESH_RINGS):
            base = ring * MESH_SPOKES
            ring_pts = [positions[base + j] for j in range(MESH_SPOKES)]
            ring_pts.append(ring_pts[0])
            painter.drawPolyline(QPolygonF(ring_pts))
        for j in range(MESH_SPOKES):
            spoke_pts = [center] + [positions[ring * MESH_SPOKES + j] for ring in range(MESH_RINGS)]
            painter.drawPolyline(QPolygonF(spoke_pts))

    def _draw_vectorscope_graticule(self, painter, rect):
        """Purely decorative Resolve-style reference overlay -- see this
        module's own docstring, RESOLVE-STYLE VECTORSCOPE GRATICULE. Sits
        at the SAME radius as the now-always-full-radius color fill (see
        _build_fill_pixmap), per Tom: "overlay a vectorscope to the same
        proportions" -- one shared scale, not two independently-tuned
        overlays. Drawn LAST (see paintEvent) and at high contrast/opacity
        (per Tom: "there is no vectorscope" -- the earlier low-alpha
        version blended into the mesh's own similarly-colored, similarly-
        placed outer ring and effectively disappeared) so it always reads
        as its own distinct layer regardless of what's under it."""
        center = rect.center()
        radius = rect.width() / 2.0

        painter.setPen(QPen(GRATICULE_COLOR, 1.5, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, radius, radius)

        half = GRATICULE_BOX_SIZE / 2.0
        painter.setPen(QPen(GRATICULE_COLOR, 2, Qt.PenStyle.SolidLine))
        font = painter.font()
        font.setPixelSize(11)
        font.setBold(True)
        painter.setFont(font)
        for label, hue in VECTORSCOPE_TARGETS:
            angle = hue_to_screen_angle(hue)
            x = center.x() + math.cos(angle) * radius
            y = center.y() + math.sin(angle) * radius
            painter.drawRect(QRectF(x - half, y - half, GRATICULE_BOX_SIZE, GRATICULE_BOX_SIZE))
            painter.drawText(QPointF(x + half + 3, y + 4), label)

    def paintEvent(self, event):
        rect = self._wheel_rect()
        side = rect.width()
        if self._fill_pixmap is None or self._fill_pixmap.width() != int(math.ceil(side)):
            self._fill_pixmap = self._build_fill_pixmap(side)

        H2, C2 = self._compute_frame_state()

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.drawPixmap(rect.topLeft(), self._fill_pixmap)

        self._draw_mesh(painter, rect, H2, C2)

        center = rect.center()
        painter.setPen(QPen(CENTER_MARK_COLOR, 1))
        painter.drawEllipse(center, 3, 3)

        for p in self._points:
            pos = self._point_pixel_pos(p, rect)
            if p["h"] != p["h0"] or p["c"] != p["c0"]:
                anchor_pos = hue_chroma_to_pos(p["h0"], p["c0"], self._max_boundary, rect)
                painter.setPen(QPen(ANCHOR_MARK_COLOR, 1.5))
                painter.drawLine(anchor_pos, pos)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(ANCHOR_MARK_COLOR)
                painter.drawEllipse(anchor_pos, 4, 4)
            painter.setPen(QPen(POINT_BORDER, 2))
            painter.setBrush(POINT_FILL)
            painter.drawEllipse(pos, POINT_RADIUS, POINT_RADIUS)

        # Drawn LAST -- see _draw_vectorscope_graticule's own docstring on
        # why (needs to stay visible on top of everything else).
        self._draw_vectorscope_graticule(painter, rect)

        painter.end()

    # ---- mouse interaction ------------------------------------------

    def mousePressEvent(self, event):
        rect = self._wheel_rect()
        pos = event.position()
        hit = self._nearest_point(pos, rect)

        if event.button() == Qt.MouseButton.RightButton:
            if hit is not None and len(self._points) > 0:
                self._points.remove(hit)
                self.update()
                self.points_changed.emit(self.points())
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        if hit is not None:
            self._dragging_point = hit
        elif len(self._points) < cw.MAX_POINTS:
            hue, chroma = pos_to_hue_chroma(pos, self._max_boundary, rect)
            chroma = max(0.0, chroma)
            fresh = {"h0": hue, "c0": chroma, "h": hue, "c": chroma}
            self._points.append(fresh)
            self._dragging_point = fresh
            self.update()
            self.points_changed.emit(self.points())

    def mouseMoveEvent(self, event):
        if self._dragging_point is None:
            return
        rect = self._wheel_rect()
        hue, chroma = pos_to_hue_chroma(event.position(), self._max_boundary, rect)
        self._dragging_point["h"] = hue
        self._dragging_point["c"] = max(0.0, chroma)
        self.update()
        self.points_changed.emit(self.points())

    def mouseReleaseEvent(self, event):
        self._dragging_point = None
