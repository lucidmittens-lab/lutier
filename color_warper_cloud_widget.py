"""
color_warper_cloud_widget.py -- WarpierCloudWidget: a live 3D OKLCh point
cloud embedded directly in WARPier's own inspector page (stack_panel.py's
_build_color_warper_inspector), placed side by side with the WarpierWheel
-- the "wheel + cloud together" layout the original concept mockup used
(see color_warper_wheel.py's own docstring), per Tom's own follow-up on
exactly how the two should sit together.

REWRITTEN away from pyqtgraph's GLScatterPlotItem/GLViewWidget entirely
(per Tom: "just looks like the volumetric scope but scaled differently...
may require writing some graphing code by hand, moreso than usual") to a
hand-rolled QPainter renderer: a fixed-angle rotate + orthographic project
(project_points, below -- same elevation/azimuth idea GLViewWidget's own
camera used, just computed by hand now), sorted back-to-front (QPainter
has no depth buffer) and drawn as flat, solid-color dots. A side benefit:
no GL context at all for this widget, so it paints correctly under
QT_QPA_PLATFORM=offscreen with no "QOpenGLWidget is not supported on this
platform" warning.

MANUALLY ROTATABLE (per Tom): left-drag orbits the fixed camera angle --
self._azimuth/self._elevation start at AZIMUTH_DEG/ELEVATION_DEG but are
just plain instance state from then on, nudged by mouseMoveEvent deltas
while dragging. Azimuth wraps freely (mod 360); elevation is clamped to
[MIN_ELEVATION_DEG, MAX_ELEVATION_DEG] instead of wrapping -- past +-90
the camera would flip upside down through the pole, which reads as a
jump rather than a continuous orbit.

Deliberately still a SEPARATE widget from the Scopes panel's own
VolumetricScopeWidget (volumetric_scope_widget.py), which stays exactly
as it is -- GL-rendered, full scope-panel-sized, and (unlike this widget)
a live measurement of the currently loaded image -- per Tom: "leave the
volumetric scope you added to the scopes panel since i like that in
general."

NOT A MEASUREMENT OF ANYTHING (per Tom's own correction -- "the
volumetric scope inside of warpier should just be an idealized
perceptually uniform color space... not a measurement of the signal in
any way. just a representation of the visible spectrum in 3d"): this
widget never samples the loaded image's own pixels -- it plots
color_warper.warped_idealized_sphere_points(), a FIXED grid, independent
of any image or working colourspace.

IDEALIZED UNIFORM SPHERE, WHITE AT THE CENTER (per Tom's own final pick,
after trying the OTHER option -- the gamut-accurate blob -- first: "Lets
please add the vectorscope... nevermind" ... "One thing we can change
tho, lets replace the volume accurate blob with the sphere you showed
me"): color_warper.warped_idealized_sphere_points embeds the reference
grid SPHERICALLY (hue=azimuth, lightness=polar angle, chroma=a FIXED
constant radius, not the real per-direction gamut boundary the earlier
blob used) -- decorative, uniformly saturated in every direction, same
"achromatic origin at the shape's own geometric center" idea, just
simpler/rounder. Rendering matches the validated mockup exactly: flat
solid-color dots (no glow/gradient), constant POINT_RADIUS regardless of
depth, full opacity, Screen composition (see paintEvent's own comment on
why), dark flat background.

FIXED DISPLAY SCALE (per Tom, alongside the sphere swap -- "make it have
a fixed scale"): paintEvent used to auto-fit its own pixel-to-data scale
to the CURRENT frame's own projected bounding box every repaint -- fine
for a static shape, but combined with continuous rotation (see PASSIVE
SPIN below) and any live warp deformation, a per-frame auto-fit is
exactly the same category of visual instability this app's own
WarpierWheel just got bitten by (see color_warper_wheel.py's own
docstring on why an auto-expanding scale was reverted -- rescaling the
whole picture in response to a changing value reads as unstable/spinning
even when the underlying numbers are bounded). FIXED_DISPLAY_EXTENT is a
constant instead, sized with headroom over the sphere's own natural
diameter so ordinary warps still fit comfortably without needing to
adapt.

PASSIVE SPIN (per Tom -- "making it passively spin"): a QTimer nudges
self._azimuth forward a small fixed amount on a steady interval,
independent of (and layered on top of) manual left-drag orbiting -- see
_on_spin_tick, paused while the user is actively dragging (self._dragging)
so a slow deliberate drag isn't fighting a stack of tiny automatic
increments.

NOT VISUALLY VERIFIED IN THIS ENVIRONMENT -- exercised headlessly
(paintEvent runs and produces a real QPixmap under offscreen Qt), but
whether it reads as attractive/legible needs a real display.
"""
import numpy as np
from PySide6.QtCore import Qt, QPointF, QTimer
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import QWidget

import color_warper as cw

MIN_HEIGHT = 200
MIN_WIDTH = 160

# Degrees of orbit per pixel of drag -- picked by feel, not derived from
# anything (this widget has no other rotate/drag precedent in this app to
# match -- lut_viewer_3d.py's own orbit comes from GLViewWidget's built-in
# mouse handling, not hand-written drag code).
DRAG_SENSITIVITY = 0.4
MIN_ELEVATION_DEG = -85.0
MAX_ELEVATION_DEG = 85.0

BACKGROUND_COLOR = QColor(18, 18, 20)

# Same fixed camera angle the old GL version's setCameraPosition used
# (elevation=20, azimuth=45) -- kept for visual continuity, just applied
# by hand now via project_points.
ELEVATION_DEG = 20.0
AZIMUTH_DEG = 45.0

# Constant, not depth-scaled -- matches the validated concept mockup
# exactly (Tom: "make it look exactly like that").
POINT_RADIUS = 3.2

# color_warper.warped_idealized_sphere_points' own defaults (26 hues x 16
# polar-angle/lightness steps) -- named here too since stack_panel.py's
# tests reference the resulting point count.
HUE_STEPS = 26
THETA_STEPS = 16

# A FIXED reference for paintEvent's pixel-to-data scale (see the module
# docstring, "FIXED DISPLAY SCALE") -- sized with headroom (~2.7x) over
# the sphere's own natural diameter (2*DEFAULT_IDEALIZED_SPHERE_CHROMA)
# so an ordinary warp's chroma_scale still fits comfortably without the
# scale itself needing to adapt.
FIXED_DISPLAY_EXTENT = cw.DEFAULT_IDEALIZED_SPHERE_CHROMA * 2.7

# Degrees of automatic rotation added per spin tick (see PASSIVE SPIN in
# the module docstring) -- picked by feel for a slow, ambient drift, not
# derived from anything.
SPIN_DEGREES_PER_TICK = 0.25
SPIN_INTERVAL_MS = 40


def project_points(positions, elevation_deg=ELEVATION_DEG, azimuth_deg=AZIMUTH_DEG):
    """Rotates `positions` (N,3), x/y/z = the spherical-embedding
    cartesian coordinates color_warper.warped_idealized_sphere_points
    returns, by a fixed azimuth (around the Z axis) then elevation (tilting Z
    toward the viewer), then projects orthographically to 2D. Returns
    `(screen_xy, depth)`: `screen_xy` is (N,2) in the SAME units as the
    input (no widget-fitting yet -- see WarpierCloudWidget.paintEvent for
    that), `depth` is (N,) with LARGER values closer to the camera (used
    for back-to-front painter's-algorithm sorting).

    Pure numpy, no Qt -- same "plain geometry helper function" testability
    convention as color_warper_wheel.py's own pixel<->hue/chroma
    conversions."""
    positions = np.asarray(positions, dtype=np.float64)
    if positions.shape[0] == 0:
        return np.zeros((0, 2)), np.zeros((0,))

    az = np.radians(azimuth_deg)
    el = np.radians(elevation_deg)

    x, y, z = positions[:, 0], positions[:, 1], positions[:, 2]

    # Rotate around Z by azimuth.
    x1 = x * np.cos(az) - y * np.sin(az)
    y1 = x * np.sin(az) + y * np.cos(az)
    z1 = z

    # Tilt by elevation (rotate around the new X axis), bringing the Z
    # axis toward/away from the viewer -- y2 becomes "distance into the
    # screen" (used for depth below), z2 the vertical screen position.
    y2 = y1 * np.cos(el) - z1 * np.sin(el)
    z2 = y1 * np.sin(el) + z1 * np.cos(el)
    x2 = x1

    screen_xy = np.stack([x2, z2], axis=-1)
    depth = -y2  # more negative y2 (nearer the camera) -> larger depth
    return screen_xy, depth


def project_to_screen(positions, elevation_deg, azimuth_deg, width, height, margin=0.16):
    """project_points, then place the result on a `width`x`height` pixel
    surface using a FIXED scale/center (see the module docstring, "FIXED
    DISPLAY SCALE") -- derived only from FIXED_DISPLAY_EXTENT and the
    coordinate origin, never from `positions`' own CURRENT bounding box.
    The sphere's own achromatic point already sits exactly at (0,0,0) by
    construction (see warped_idealized_sphere_points' own spherical
    embedding), so centering on the origin needs no recomputation either.

    Split out from paintEvent so the fixed-scale behavior itself is
    directly testable without a real paint surface -- same "plain
    geometry helper function" convention as project_points above.
    Returns `(px, py, depth)`, each `(N,)`."""
    screen_xy, depth = project_points(positions, elevation_deg=elevation_deg, azimuth_deg=azimuth_deg)
    side = min(width, height)
    scale = side * (1.0 - 2 * margin) / FIXED_DISPLAY_EXTENT
    center_screen_x = width / 2.0
    center_screen_y = height / 2.0

    px = screen_xy[:, 0] * scale + center_screen_x
    # Qt's Y grows downward; the data's own Z should read as UP on screen,
    # so flip.
    py = center_screen_y - screen_xy[:, 1] * scale
    return px, py, depth


class WarpierCloudWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(MIN_HEIGHT)
        self.setMinimumWidth(MIN_WIDTH)
        self._positions = np.zeros((0, 3))
        self._colors = np.zeros((0, 3))
        self._azimuth = AZIMUTH_DEG
        self._elevation = ELEVATION_DEG
        self._dragging = False
        self._last_drag_pos = None
        # Self-populates with the UNWARPED sphere (points=[] is an exact
        # identity warp, see warp_hue_chroma's own docstring) -- StackPanel
        # calls set_warp with the real stage's current points/wake/dropoff
        # once a color_warper stage is actually selected (see
        # stack_panel.py's _apply_color_warper_live and
        # _refresh_inspector_body).
        self.set_warp([], cw.DEFAULT_WAKE, cw.DEFAULT_DROPOFF)

        # Passive spin (see the module docstring) -- runs continuously
        # regardless of whether a color_warper stage is even selected;
        # cheap (a float increment + repaint of a small widget) and
        # simpler than wiring it to pause/resume on visibility.
        self._spin_timer = QTimer(self)
        self._spin_timer.timeout.connect(self._on_spin_tick)
        self._spin_timer.start(SPIN_INTERVAL_MS)

    def _on_spin_tick(self):
        if not self._dragging:
            self._azimuth = (self._azimuth + SPIN_DEGREES_PER_TICK) % 360.0
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._last_drag_pos = event.position()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging:
            pos = event.position()
            self._apply_drag_delta(pos - self._last_drag_pos)
            self._last_drag_pos = pos
            self.update()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def _apply_drag_delta(self, delta):
        """delta: a QPointF of pixels moved since the last event. Split
        out from mouseMoveEvent so the azimuth/elevation math itself is
        directly testable without synthesizing real QMouseEvents."""
        self._azimuth = (self._azimuth + delta.x() * DRAG_SENSITIVITY) % 360.0
        self._elevation = min(MAX_ELEVATION_DEG, max(
            MIN_ELEVATION_DEG, self._elevation - delta.y() * DRAG_SENSITIVITY))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def set_points(self, positions, colors):
        """positions/colors: (N,3) arrays, same convention
        color_warper.warped_gamut_blob_points (called by set_warp below)
        returns. Public mainly for testability -- set_warp is the actual
        entry point everything else in this app uses. Just stores +
        triggers a repaint -- unlike the old GL version there's no
        scene-graph item to rebuild, paintEvent below re-derives
        everything from these arrays fresh every time."""
        self._positions = np.asarray(positions, dtype=np.float64)
        self._colors = np.asarray(colors, dtype=np.float64)
        self.update()

    def set_warp(self, points, wake=cw.DEFAULT_WAKE, dropoff=cw.DEFAULT_DROPOFF):
        """The actual entry point StackPanel drives (see
        _apply_color_warper_live/_refresh_inspector_body) -- `points`/
        `wake`/`dropoff` are the SAME values driving the real grade
        (cw_wheel.points()/cw_wake_spin.value()/cw_dropoff_spin.value()),
        so this reference sphere always shows exactly what the current
        WARPier stage is actually doing to OKLCh space, live, with no
        image involved anywhere."""
        positions, colors = cw.warped_idealized_sphere_points(
            points, wake=wake, dropoff=dropoff,
            hue_steps=HUE_STEPS, theta_steps=THETA_STEPS)
        self.set_points(positions, colors)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), BACKGROUND_COLOR)

        if self._positions.shape[0] == 0:
            painter.end()
            return

        px, py, depth = project_to_screen(
            self._positions, elevation_deg=self._elevation, azimuth_deg=self._azimuth,
            width=self.width(), height=self.height())

        # Back-to-front (painter's algorithm) -- QPainter has no depth
        # buffer, so farther points must be painted first and nearer ones
        # drawn on top.
        order = np.argsort(depth)

        # Screen (not the default SourceOver) for the dots themselves --
        # SourceOver blends overlapping opaque circles by simply replacing
        # whichever is drawn on top, which loses the UNDER point entirely;
        # Screen composites each channel as `1 - (1-a)*(1-b)` -- overlapping
        # colors brighten and blend toward white rather than occlude, so a
        # dense cluster of distinct hues near the center reads as a
        # visible, energetic cluster instead of just whatever happened to
        # paint last. Same idea validated in the concept mockup (Tom:
        # "make it look exactly like that").
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Screen)
        painter.setPen(Qt.PenStyle.NoPen)

        for i in order:
            r, g, b = self._colors[i]
            color = QColor(
                max(0, min(255, round(r * 255))),
                max(0, min(255, round(g * 255))),
                max(0, min(255, round(b * 255))),
            )
            painter.setBrush(color)
            painter.drawEllipse(QPointF(float(px[i]), float(py[i])), POINT_RADIUS, POINT_RADIUS)

        painter.end()
