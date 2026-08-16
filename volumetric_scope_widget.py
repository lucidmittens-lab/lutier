"""
volumetric_scope_widget.py -- VolumetricScopeWidget: the Qt/pyqtgraph
rendering layer for the "Volumetric" scope in scopes_panel.py (see
volumetric_scope.py for the actual OKLCh sampling math -- this file is
the thin Qt paint layer on top of it, same "Qt-free math module + Qt
widget wrapper" split as color_warper.py/color_warper_wheel.py).

Reuses lut_viewer_3d.py's own proven GL patterns rather than
reinventing them: `_SafeGLViewWidget` (imported from its shared home,
safe_gl_view_widget.py) for the GL-context-safety guard, and the same
remove-old/construct-new `GLScatterPlotItem` + bounding-box camera
refit (`_frame_camera_on`) convention that module's own `_update_3d_view`
established -- a small, self-contained rewrite against OKLCh's own point
cloud, not a cross-file call into LutViewer3D's methods (this widget has
no cage/wireframe, no density slider, no float/dock rebuild -- it's not
a standalone panel of its own, just one page inside ScopesPanel's
existing QStackedWidget).

NOT VISUALLY VERIFIED IN THIS ENVIRONMENT -- same situation as
lut_viewer_3d.py/color_warper_wheel.py: exercised headlessly, but
whether the point cloud actually reads clearly needs a real display.
"""
import numpy as np
import pyqtgraph as pg
import pyqtgraph.opengl as gl
from PySide6.QtWidgets import QWidget, QVBoxLayout

from safe_gl_view_widget import _SafeGLViewWidget

MIN_HEIGHT = 220
# OKLCh's own natural bounds (chroma roughly [0, 0.4], lightness [0, 1])
# are much smaller than a LUT's [0,1]^3 cube -- distance=3 (lut_viewer_3d's
# own default) would be needlessly zoomed out here.
INITIAL_CAMERA_DISTANCE = 1.4


class VolumetricScopeWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._scatter_item = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.gl_view = _SafeGLViewWidget()
        self.gl_view.setCameraPosition(distance=INITIAL_CAMERA_DISTANCE, elevation=20, azimuth=45)
        self.gl_view.setMinimumHeight(MIN_HEIGHT)
        layout.addWidget(self.gl_view)

        axis = gl.GLAxisItem()
        axis.setSize(x=0.5, y=0.5, z=1.0)
        self.gl_view.addItem(axis)

    def set_points(self, positions, colors):
        """positions/colors: (N,3) arrays from
        volumetric_scope.sample_pixels_to_oklch. Rebuild-not-mutate, same
        convention lut_viewer_3d.py's own _update_3d_view uses -- a fresh
        GLScatterPlotItem each call rather than calling setData on the
        existing one."""
        if self._scatter_item is not None:
            self.gl_view.removeItem(self._scatter_item)
            self._scatter_item = None

        if positions.shape[0] == 0:
            return

        # GLScatterPlotItem expects RGBA; colors from sample_pixels_to_oklch
        # are RGB only.
        rgba = np.concatenate(
            [colors, np.ones((colors.shape[0], 1))], axis=1)
        self._scatter_item = gl.GLScatterPlotItem(
            pos=positions, color=rgba, size=4, pxMode=True)
        self.gl_view.addItem(self._scatter_item)

        self._frame_camera_on(positions)

    def _frame_camera_on(self, positions):
        """Points the camera at the actual center of the plotted cloud
        and sets distance to comfortably fit it -- same reasoning as
        lut_viewer_3d.py's own _frame_camera_on (pyqtgraph's GLViewWidget
        defaults camera 'center' to the coordinate origin, which is not
        the middle of an OKLCh point cloud any more than it's the middle
        of a [0,1]^3 LUT cube)."""
        if positions.size == 0:
            return
        mins = positions.min(axis=0)
        maxs = positions.max(axis=0)
        center = (mins + maxs) / 2.0
        extent = float(np.max(maxs - mins))
        distance = max(extent * 2.5, 0.3)
        self.gl_view.setCameraPosition(
            pos=pg.Vector(float(center[0]), float(center[1]), float(center[2])),
            distance=distance,
        )
