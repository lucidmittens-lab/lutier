"""
lut_viewer_3d.py -- interactive 3D point-cloud + wireframe-cage visualization
of a LUT's deformation lattice, using pyqtgraph's OpenGL view (GLViewWidget +
GLScatterPlotItem + GLLinePlotItem). Each point is positioned by where the
LUT sends it and colored by its original input value, so hue/saturation/
luminance shifts are visible directly; the cage connects each point to its
immediate lattice neighbors so the deformation reads as a mesh, not just a
cloud -- the standard "LUT cube" visualization. Also home to the Graph
panel's 1D mode (Y/R/G/B response curves) -- see the "GRAPH PANEL: 3D + 1D"
section below.

NOT TESTED IN THIS ENVIRONMENT -- the sandbox this was built in is missing a
system library (libEGL) that PySide6 needs even to import, and OpenGL
rendering specifically would need a working GL context this sandbox doesn't
have either way. Only python3 -m py_compile syntax-checked, never run.
Please run main_window.py as the first real test and report anything that
breaks, especially anything OpenGL/rendering related since that's the least
certain part.

CRASH FIX (per Tom's report, closing/redocking a floated panel segfaulted):
gl_view is `_SafeGLViewWidget`, not a plain `gl.GLViewWidget` -- see its
docstring below for the actual guard. main_window.py's `_float_panel`/
`_dock_panel` pair this with their own `_reparent_gl_safely` helper, which
suspends paint delivery to every QOpenGLWidget under a panel for the
duration of the reparent that floating/docking always does. Both halves
matter: the main_window.py half tries to stop Qt from delivering a paint
event during the reparent at all; this module's half is the last line of
defense if one gets through anyway (e.g. from a source neither of us
anticipated -- window-manager-triggered repaints on redock, for instance).

RENDER FIX (per Tom's follow-up: the crash is gone, but confirmed the
graph is blank/black the whole time while floated -- not a stalled last
frame, not a slow repaint, genuinely never draws anything, even across
rotate/resize/density-slider changes). That points at the GL context
itself never coming up valid for this widget once it's promoted from "a
child widget inside the main window" to "its own top-level, always-on-top
window" -- not just a missed repaint _SafeGLViewWidget's isValid() guard
would eventually recover from on its own. Reparenting an EXISTING
QOpenGLWidget across that kind of promotion is a scenario Qt doesn't
reliably handle on every platform; the standard workaround is to not ask
Qt to carry the old widget's internal GL state across the transition at
all -- throw the widget away and build a fresh one in its final location
instead, so context/surface setup happens cleanly from scratch against
wherever it actually ends up.

`rebuild_gl_view()` does exactly that: tears down the current `gl_view`
(and its grid/axis/scatter items) and replaces it with a brand new
`_SafeGLViewWidget`, re-adding the grid/axis and re-applying whatever LUT
was loaded (so the visual content survives the rebuild, only the
underlying native widget changes). main_window.py's `_float_panel`/
`_dock_panel` call this on `self.viewer_3d` specifically, AFTER the panel
has actually been reparented and shown/docked -- see that module for
exactly where.

DELETELATER/GC RACE (per Tom's report AFTER all three fixes above: closing
the floated 3D Viewer crashed again, specifically when no LUT was loaded).
`rebuild_gl_view()`'s first version called `old_view.setParent(None)` then
`old_view.deleteLater()` and let `old_view` fall out of scope with nothing
else referencing it. deleteLater() only schedules the C++ deletion for
later -- it does nothing to protect the PYTHON wrapper from being
garbage-collected in the meantime, and PySide6/Shiboken's default rule for
a PARENTLESS QObject is that Python's own GC deletes the C++ object too.
So the old widget could get deleted TWICE, racing itself -- a hard native
crash. See rebuild_gl_view's own docstring for the fix (keep a permanent
Python reference until Qt's deferred delete has actually run).

CAMERA-CENTERING BUG (found auditing the above per Tom's question -- "is
it possible it's not centering the graph in the new space?"): pyqtgraph's
GLViewWidget.reset() defaults the camera's 'center' to Vector(0,0,0) --
the coordinate ORIGIN -- and nothing in this file ever overrode it, only
distance/elevation/azimuth. For a typical identity-domain LUT, the
deformed lattice `set_lut` plots lives roughly in [0,1]^3, whose actual
CENTER is (0.5, 0.5, 0.5) -- the origin is one CORNER of that cube, not
its middle. Worse, for any LUT whose output legitimately goes outside
[0,1] (HDR grades, scene-linear stages -- this app doesn't clip anywhere
upstream on purpose), a hardcoded (0.5,0.5,0.5) would be just as wrong.
`_frame_camera_on` now recomputes the camera's center AND distance from
the actual bounding box of whatever `positions` set_lut just computed,
every time a LUT loads, so framing is correct for the real data instead
of a guess. This was there in EVERY case (docked too), not something the
float/dock rebuild introduced -- if it was ever hard to notice while
docked, that's plausibly because pyqtgraph's default distance=10 (before
this file's distance=3 override) or accumulated manual
pan/zoom/rotate on a single long-lived widget was hiding it, both of
which rebuild_gl_view's fresh widget doesn't get to inherit.

All of the above (crash, render, centering) is confirmed fixed on real
hardware.

GRAPH PANEL: 3D + 1D (per Tom's request for "1D YRGB viewers" -- checked in
on the design first: LUT response curves, not an image-derived scope,
worked into this same panel rather than a new one or a Scopes mode, X axis
following the Stack's own domain rather than a fixed [0,1]). This panel is
renamed "Graph" in the UI (see main_window.py's PANEL_TOGGLES/PanelFrame
title; this module's own class/internals keep the old `viewer_3d`/
`LutViewer3D` names to avoid churning every reference).

View selection is a "..." corner menu (`populate_menu`/`_build_menu_button`,
mirrored by MainWindow into a top-level "Graph" menu-bar entry too) --
per Tom's follow-up ("the buttons for 1d curve is broken, should go in an
ellipses menu, select 3d or 1d y 1d r 1d g 1d b"), replacing an earlier
checkable-QPushButton-pair (3D/1D-combined) that didn't work right. Five
choices, one QActionGroup, matching scopes_panel.py's own scope-select
convention exactly: 3D (the point cloud + cage), and 1D Y/1D R/1D G/1D B
-- each showing exactly ONE of the four response curves at a time, not all
four overlaid (see VIEW_CHOICES/_VIEW_TO_CURVE_CHANNEL/_on_view_changed).

The GL view (`_gl_panel`, wrapping gl_view + its density/cage-opacity
controls) and the 1D curve plot (`curve_plot`) are both pages of one
`QStackedWidget` (`self.stack`), not siblings toggled via setVisible() --
this is deliberate, fixing a real bug Tom hit: a plain sibling-visibility
toggle changes which widget's minimum size the containing layout sees,
which let the panel's actual on-screen size snap around on every mode
switch (Qt's `QSplitter` reflowing neighboring panes to match). A
`QStackedWidget`'s own `minimumSizeHint()` is the MAX across all its pages
regardless of which one is current (confirmed empirically, not just
assumed) -- so the panel's footprint never changes on a mode switch, only
its content does. Same widget already used for exactly this reason in
scopes_panel.py's Histogram/Waveform/Vectorscope switch.

Both `_gl_panel` and `curve_plot` are still built once and kept alive
permanently regardless of which is the current stack page -- switching
modes never destroys/rebuilds either one. This remains deliberate:
`rebuild_gl_view`'s whole reason for existing is that tearing down and
recreating the GL widget is a real, historically crash-prone operation
(see DELETELATER/GC RACE above) -- a mode switch firing far more often
than float/dock ever did is not where that risk belongs. `set_lut()`
updates the 3D scatter+cage AND the 1D curves on every call regardless of
which page is current, so switching modes always shows current data with
no extra recompute step.

KNOWN LIMITATION: `compute_yrgb_curve`'s Y (luma) curve is plotted against
`input_r` as its X axis (see that function's own docstring) -- correct
whenever R/G/B/domain are linked (the common case, same "Link R/G/B"
default the rest of this app uses for Stack Output's Domain Min/Max), but
an approximation if a Stack's domain is deliberately set asymmetric across
channels. Flagged rather than solved here since it wasn't clear from
Tom's request whether that edge case matters in practice -- worth raising
if it turns out to.

CAGE MESH (per Tom's correction: "the grid on the 3d is not what i
thought. I want more of a cage mesh that draws lines between the points
rendered in the graph"). The original "Grid opacity" feature (a flat
`GLGridItem` ground-plane reference, unrelated to the LUT's own data) is
GONE -- replaced by `_cage_item`, a `GLLinePlotItem` connecting each
scatter point to its immediate neighbors along all three grid axes (see
`app_logic.extract_3d_cage_lines_for_visualization`), recomputed alongside
the scatter in `_update_3d_view` every time the LUT/density changes (a
flat reference grid didn't need to track the LUT at all; a cage inherently
does, since it connects the LUT's OWN deformed points). The opacity
slider/label/attributes are renamed grid->cage throughout (`_cage_opacity`,
`cage_opacity_slider`, `_apply_cage_opacity`) -- a genuine feature change,
not just a relabeled string, unlike the panel's own "3D Viewer"->"Graph"
rename above. `GLAxisItem` (world axis lines, for orientation) is
unaffected -- Tom's complaint was specifically about the grid, not the
axes. Edge count grows fast with Grid density (811,200 edges at the
slider's max, 65) -- same order of magnitude as the point count that
slider already asks pyqtgraph/OpenGL to handle at that density, so no
separate cap was added; flag if that's too slow in practice.

Still UNVERIFIED ON REAL HARDWARE, same as every round above -- please
re-test: (1) the "..." menu's 3D/1D Y/1D R/1D G/1D B options actually
switch what's showing and look right (one curve at a time in 1D modes);
(2) switching between 3D and any 1D mode does NOT change the panel's
size/cause other panels to shift; (3) the 3D view now shows a wireframe
cage connecting the plotted points (not a flat reference plane), and its
opacity slider fades that cage in/out.
"""
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QStackedWidget,
    QToolButton, QMenu,
)
import pyqtgraph.opengl as gl

from app_logic import (
    extract_3d_points_for_visualization,
    extract_3d_cage_lines_for_visualization,
    compute_yrgb_curve,
)

# Same R/G/B palette scopes_panel.py's Histogram/Waveform use (per Tom's
# "1D YRGB viewers" -- worked into this panel, see LutViewer3D's 1D mode
# below, rather than a separate Scopes mode, since it's a second
# projection of the exact same baked-LUT data the 3D view already shows).
# Kept as a local copy rather than importing scopes_panel's private
# _CHANNEL_COLORS -- this module has no other dependency on scopes_panel.py
# and one three-tuple isn't worth introducing one over.
_CURVE_COLORS = {
    "y": (230, 230, 230),
    "r": (220, 60, 60),
    "g": (60, 200, 90),
    "b": (70, 120, 230),
}

# Graph panel view selection (per Tom's follow-up -- "select 3d or 1d y 1d
# r 1d g 1d b") -- five mutually exclusive choices, one QActionGroup, same
# convention as scopes_panel.py's SCOPE_HISTOGRAM/SCOPE_WAVEFORM/
# SCOPE_VECTORSCOPE. Each 1D choice shows exactly ONE curve at a time
# (_VIEW_TO_CURVE_CHANNEL), not all four overlaid.
VIEW_3D = "3D"
VIEW_1D_Y = "1D Y"
VIEW_1D_R = "1D R"
VIEW_1D_G = "1D G"
VIEW_1D_B = "1D B"
VIEW_CHOICES = (VIEW_3D, VIEW_1D_Y, VIEW_1D_R, VIEW_1D_G, VIEW_1D_B)
_VIEW_TO_CURVE_CHANNEL = {
    VIEW_1D_Y: "y", VIEW_1D_R: "r", VIEW_1D_G: "g", VIEW_1D_B: "b",
}


class _SafeGLViewWidget(gl.GLViewWidget):
    """Identical to pyqtgraph's GLViewWidget, except paintGL() bails out
    before issuing a single native GL call if the widget doesn't
    currently have a valid GL context/framebuffer.

    Why this exists: pyqtgraph.opengl.GLViewWidget.paintGL() calls
    straight into raw ctypes-bound GL calls (glClear/glDrawArrays/etc via
    PyOpenGL's `GL` module) with no guard of its own. Floating or docking
    a panel means reparenting this widget (see main_window.py's
    _float_panel/_dock_panel) -- on macOS that reparent can make Qt
    deliver a paint event for this widget while its GL context is
    mid-teardown or mid-recreate, i.e. before initializeGL() has run
    against whatever context it ends up with. Calling real GL functions
    at that moment is what produced the SIGSEGV/EXC_BAD_ACCESS Tom
    reported -- a hard native crash, not a Python exception, so no amount
    of try/except here would have caught it after the fact. isValid() is
    Qt's own supported "do I actually have a usable context right now"
    check, and it's the only point at which this is preventable: check it
    BEFORE calling super().paintGL(), never after.

    Silently skips (no error, no log) rather than raising when invalid --
    this isn't an error condition, it's an expected transient state during
    reparenting, and Qt will simply deliver another real paint event once
    the widget has settled into its new parent with a valid context."""

    def paintGL(self):
        if not self.isValid():
            return
        super().paintGL()


class LutViewer3D(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_lut = None
        self._scatter_item = None
        self._cage_item = None
        # See set_lut/showEvent below -- True when a set_lut() call was
        # skipped because this panel wasn't visible at the time (hidden
        # via the Workspace menu, or tabbed behind another panel), so the
        # next time it actually becomes visible again knows there's a
        # real rebuild still owed, not just a no-op showEvent.
        self._stale_lut_pending = False
        # See rebuild_gl_view's docstring (DELETELATER RACE) -- every
        # gl_view discarded by a rebuild gets appended here and NEVER
        # removed, on purpose: this is what keeps a real Python reference
        # to it alive until Qt's own deleteLater() has actually run, so
        # CPython's refcounting can't free (and thus double-delete
        # alongside Qt) the underlying C++ widget first. Deliberately
        # unbounded rather than trying to time a safe moment to clear it
        # -- float/dock is a rare, human-paced action, so a few retained
        # already-deleted-in-C++ Python wrapper objects over an entire
        # session is a non-issue, and getting the clear-timing wrong
        # would silently reopen exactly the crash this exists to prevent.
        self._pending_gl_view_deletions = []
        # Cage opacity (per Tom -- see this module's docstring, CAGE MESH)
        # -- 0-100%, applied to the wireframe cage's alpha. Kept as
        # widget-level state (not read off self._cage_item, which gets
        # discarded and rebuilt every time the LUT/density changes, same
        # as the scatter) so it survives those rebuilds the same way
        # density_slider's value already does. 30% matches pyqtgraph's own
        # GLGridItem default alpha (76.5/255) -- an inherited starting
        # point from the original grid feature, not re-tuned for the cage
        # specifically; flag if it should change now that this is the
        # LUT's own data rather than a reference plane.
        self._cage_opacity = 0.3
        # 1D graph mode (per Tom -- "1D YRGB viewers"). See this module's
        # docstring for why num_samples is a plain constant rather than a
        # slider -- unlike Grid density (which trades render cost for
        # visual detail on a real GPU), this is a cheap 1D numpy computation
        # regardless of sample count, so there's no real tradeoff to expose.
        self._curve_num_samples = 256
        self._curve_items = {}
        self._current_view = VIEW_3D
        # Same "one {choice: action} dict per menu instance" pattern
        # scopes_panel.py's _scope_action_sets uses -- this panel's "..."
        # corner menu and MainWindow's top-level "Graph" menu-bar menu
        # each get their own QActionGroup, so a selection made in either
        # one needs to be mirrored into the other's matching action (see
        # _on_view_changed).
        self._view_action_sets = []
        self._build_layout()

    def _build_layout(self):
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)  # PanelFrame provides the outer gutter

        # Not added to this panel's own layout -- MainWindow hands
        # self.menu_button to PanelFrame as corner_widget, same as
        # Viewer's/Stack's/Scopes' own "..." menus (see their docstrings).
        self._build_menu_button()

        # 3D view and 1D curve plot are pages of one QStackedWidget, not
        # toggled siblings -- see this module's docstring (GRAPH PANEL) for
        # why: a QStackedWidget's minimumSizeHint is the max across ALL
        # pages regardless of which is current, so switching modes can't
        # change the panel's own footprint the way sibling setVisible()
        # calls did.
        self.stack = QStackedWidget()
        self._layout.addWidget(self.stack, stretch=1)

        self._gl_panel = self._build_gl_panel()
        self.stack.addWidget(self._gl_panel)

        self.curve_plot = self._make_curve_plot()
        self.stack.addWidget(self.curve_plot)

        self.stack.setCurrentWidget(self._gl_panel)

    def _build_menu_button(self):
        """The Graph panel's "..." header menu -- see this module's
        docstring (GRAPH PANEL) for why this replaced an earlier
        checkable-QPushButton-pair mode switch."""
        self.menu_button = QToolButton()
        self.menu_button.setObjectName("panelMenuButton")
        self.menu_button.setAutoRaise(True)
        self.menu_button.setText("☰")  # hamburger icon, per Tom (was "...")
        self.menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.menu_button)
        self.populate_menu(menu)
        self.menu_button.setMenu(menu)

    def populate_menu(self, menu):
        """Add the view-select actions to `menu` -- shared by the "..."
        corner button above and MainWindow's top-level "Graph" menu-bar
        menu, exactly matching scopes_panel.py's populate_menu/
        _on_scope_changed pattern for its own scope selector."""
        action_group = QActionGroup(menu)
        action_group.setExclusive(True)
        actions = {}
        for name in VIEW_CHOICES:
            action = menu.addAction(name)
            action.setCheckable(True)
            action.triggered.connect(
                lambda _checked=False, n=name: self._on_view_changed(n))
            action_group.addAction(action)
            actions[name] = action
        actions[self._current_view].setChecked(True)
        self._view_action_sets.append(actions)
        return actions

    def _on_view_changed(self, view_name):
        self._current_view = view_name
        # Keep EVERY menu's checked state in sync -- there are two (the
        # "..." corner menu and MainWindow's top-level "Graph" menu-bar
        # menu), each with its own QActionGroup, so picking a view from
        # either one needs to update the other's matching action too.
        for actions in self._view_action_sets:
            action = actions.get(view_name)
            if action is not None and not action.isChecked():
                action.setChecked(True)

        if view_name == VIEW_3D:
            self.stack.setCurrentWidget(self._gl_panel)
            return

        self.stack.setCurrentWidget(self.curve_plot)
        # Exactly one curve visible at a time, per Tom's "select 3d or 1d
        # y 1d r 1d g 1d b" -- not all four overlaid. The underlying data
        # for every curve is still kept current regardless (see
        # _update_curve_plot/set_lut), only visibility changes here.
        channel = _VIEW_TO_CURVE_CHANNEL[view_name]
        for item_channel, item in self._curve_items.items():
            item.setVisible(item_channel == channel)

    def _build_gl_panel(self):
        """Builds the 3D-mode page: the GL view plus its density/cage-
        opacity controls, all in one container widget so it can be one
        page of the outer QStackedWidget (see _build_layout)."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        self.gl_view = self._make_gl_view()
        layout.addWidget(self.gl_view, stretch=1)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Grid density:"))
        self.density_slider = QSlider(Qt.Orientation.Horizontal)
        self.density_slider.setMinimum(3)
        # 17 was an arbitrary guess, not a measured limit -- nothing in
        # extract_3d_points_for_visualization or GLScatterPlotItem caps
        # meaningfully before here. 65 matches the resolution of a typical
        # full-size production LUT, so "match the source LUT's own grid"
        # is now a real option, not something the slider blocked.
        self.density_slider.setMaximum(65)
        self.density_slider.setValue(9)
        # Live update on every tick -- tried gating the recompute to
        # slider-release to avoid re-sampling + re-uploading points on
        # every drag frame, but that was a guess at a perf problem that
        # apparently isn't real on real hardware. Back to simple and live.
        self.density_slider.valueChanged.connect(self._on_density_changed)
        controls.addWidget(self.density_slider, stretch=1)
        self.density_label = QLabel("9³ points")
        controls.addWidget(self.density_label)
        layout.addLayout(controls)

        cage_controls = QHBoxLayout()
        cage_controls.addWidget(QLabel("Cage opacity:"))
        self.cage_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.cage_opacity_slider.setMinimum(0)
        self.cage_opacity_slider.setMaximum(100)
        self.cage_opacity_slider.setValue(int(round(self._cage_opacity * 100)))
        self.cage_opacity_slider.valueChanged.connect(self._on_cage_opacity_changed)
        cage_controls.addWidget(self.cage_opacity_slider, stretch=1)
        self.cage_opacity_label = QLabel(f"{self.cage_opacity_slider.value()}%")
        cage_controls.addWidget(self.cage_opacity_label)
        layout.addLayout(cage_controls)

        return panel

    def _make_curve_plot(self):
        """Builds the 1D YRGB curve PlotWidget once -- four PlotDataItems
        (Y/R/G/B), same color convention scopes_panel.py's Histogram/
        Waveform use (see _CURVE_COLORS above) so a color means the same
        thing across every plot in this app. Data is filled in later by
        _update_curve_plot, called from set_lut -- this method only builds
        the empty widget/curve-item shells, matching _build_histogram_view/
        _build_waveform_view's own split in scopes_panel.py. Visibility of
        individual items is controlled by _on_view_changed (exactly one
        visible at a time, per Tom -- see VIEW_CHOICES above)."""
        plot = pg.PlotWidget()
        plot.setBackground(None)
        plot.setLabel("bottom", "Input")
        plot.setLabel("left", "Output")
        plot.showGrid(x=True, y=True, alpha=0.2)

        labels = {"y": "Y", "r": "R", "g": "G", "b": "B"}
        for channel, color in _CURVE_COLORS.items():
            pen = pg.mkPen(color=color, width=1.5)
            self._curve_items[channel] = plot.plot(
                [], [], pen=pen, name=labels[channel])
        return plot

    def _update_curve_plot(self, lut):
        """Recomputes the four YRGB curves for `lut` and pushes them into
        the already-built PlotDataItems -- called from set_lut on every
        LUT change, regardless of which view is currently selected, so
        switching views always shows current data with no extra recompute
        step (see this module's docstring). See
        app_logic.compute_yrgb_curve's docstring for the domain-following
        ramp this is built from, and for why 'y' is plotted against
        input_r specifically (KNOWN LIMITATION, this module's docstring)."""
        curve = compute_yrgb_curve(lut, num_samples=self._curve_num_samples)
        self._curve_items["y"].setData(curve["input_r"], curve["y"])
        self._curve_items["r"].setData(curve["input_r"], curve["r"])
        self._curve_items["g"].setData(curve["input_g"], curve["g"])
        self._curve_items["b"].setData(curve["input_b"], curve["b"])

    def _on_cage_opacity_changed(self, value):
        self._cage_opacity = value / 100.0
        self.cage_opacity_label.setText(f"{value}%")
        self._apply_cage_opacity()

    def _apply_cage_opacity(self):
        """Applies self._cage_opacity to whichever cage GLLinePlotItem
        currently exists -- called on every slider move AND right after
        _update_3d_view rebuilds the cage (a new LUT/density means an
        entirely new set of line segments), since a fresh GLLinePlotItem
        always starts at pyqtgraph's own default color/alpha, with no
        memory of whatever the slider was last set to.

        GLLinePlotItem's `color` is a plain 0.0-1.0 float RGBA tuple
        (confirmed against the actual installed pyqtgraph, not assumed) --
        NOT the 0-255 range GLGridItem.setColor used, which is why this
        multiplies by 1.0, not 255.0, unlike the old _apply_grid_opacity
        this replaced."""
        if self._cage_item is None:
            return
        self._cage_item.setData(color=(1.0, 1.0, 1.0, self._cage_opacity))

    def _make_gl_view(self):
        """Builds one fresh, fully set-up `_SafeGLViewWidget` -- camera
        position, minimum height, and an axis item for orientation, but
        deliberately NOT the current LUT's scatter/cage (those depend on
        the actual LUT data, computed in _update_3d_view instead -- see
        that method and rebuild_gl_view, the two callers besides
        __init__/_build_gl_panel). Pulled out of _build_gl_panel so both
        the initial construction and a later rebuild (see
        rebuild_gl_view) build an identically-configured widget from one
        place, rather than two copies of this setup code drifting apart
        over time.

        No flat reference GLGridItem here anymore -- see this module's
        docstring (CAGE MESH) for why: per Tom, that wasn't what "grid"
        meant to him. The replacement (a wireframe cage connecting the
        LUT's own plotted points) depends on the LUT, so it's built in
        _update_3d_view, not here."""
        view = _SafeGLViewWidget()
        view.setCameraPosition(distance=3, elevation=20, azimuth=45)
        # Without this, a tall stack of toggled-on panels competing for
        # vertical space in a fixed-height window could squeeze the GL
        # view toward 0px while the (non-stretch) density slider row below
        # kept its natural size -- looked like "the graph disappeared but
        # the slider didn't." MainWindow now puts this panel in a scroll
        # area specifically so squeezing shouldn't happen at all, but this
        # floor stays as a second line of defense regardless of what
        # container ends up holding this widget.
        view.setMinimumHeight(280)

        axis = gl.GLAxisItem()
        axis.setSize(x=1, y=1, z=1)
        view.addItem(axis)

        return view

    def rebuild_gl_view(self):
        """Discards the current gl_view and replaces it with a brand new
        one in the same spot in the layout -- see this module's docstring
        (RENDER FIX) for why: reparenting an EXISTING QOpenGLWidget across
        "child widget" <-> "its own top-level window" isn't reliable, so
        rather than trying to carry a live widget's GL state across that
        transition, don't -- build a fresh widget in its final location
        instead and let Qt set up its context from scratch there.

        Called by main_window.py's _float_panel/_dock_panel, AFTER the
        panel has been reparented and shown/docked (i.e. once gl_view's
        parent hierarchy has already settled into its final state for
        this transition) -- rebuilding before that would just mean doing
        it again a moment later once the parent changes again.

        Whatever LUT was loaded survives the rebuild (re-applied via
        _update_3d_view at the end) -- only the underlying native GL
        widget is actually new, not the visible content once this
        returns.

        DELETELATER RACE (found chasing Tom's report that closing a
        floated, empty -- no LUT loaded -- 3D Viewer crashed again after
        #29/#30's fixes): the previous version of this method called
        `old_view.setParent(None)` then `old_view.deleteLater()` and let
        `old_view` fall out of scope. deleteLater() only schedules C++-
        side deletion for later, on the next spin of the event loop -- it
        does nothing to protect the PYTHON wrapper object from being
        garbage-collected in the meantime. Once `old_view` was parentless
        (setParent(None)) and this method returned with nothing else
        referencing it, CPython's refcounting could collect the Python
        wrapper immediately -- and PySide6/Shiboken's default ownership
        rule for a parentless QObject is that Python's GC deletes the
        underlying C++ object too. That's two independent deletions of
        the same QOpenGLWidget racing each other (Qt's deferred delete vs
        Python's immediate one) -- a double-free/use-after-free, a hard
        native crash, not something a try/except could catch. Loading no
        LUT is simply the fastest path back to this function returning
        with nothing keeping old_view's refcount above zero, which is
        plausibly why Tom only saw it that way, though the underlying bug
        was there regardless of what was loaded.

        Fix: `self._pending_gl_view_deletions` (see __init__) keeps a
        real, permanent Python reference to every discarded old_view, so
        it can never be garbage-collected out from under Qt's own
        deleteLater(). Qt's deferred delete remains the ONLY thing that
        ever actually destroys these widgets.

        Operates on `self._gl_panel`'s own layout, not the panel's
        top-level `self._layout` -- gl_view has always lived one level
        deeper, inside its own `_gl_panel` container (now a page of
        `self.stack`, see this module's docstring). `self._scatter_item`/
        `self._cage_item` are explicitly reset to None BEFORE `self.gl_view`
        is reassigned to the new widget -- both were added to the OLD
        gl_view, and `_update_3d_view` (called at the end, below) would
        otherwise try to `removeItem()` them from the NEW gl_view, which
        never had them and would raise."""
        old_view = self.gl_view
        gl_panel_layout = self._gl_panel.layout()
        index = gl_panel_layout.indexOf(old_view)

        new_view = self._make_gl_view()
        gl_panel_layout.insertWidget(index, new_view, stretch=1)

        gl_panel_layout.removeWidget(old_view)
        old_view.hide()
        self._pending_gl_view_deletions.append(old_view)
        old_view.deleteLater()

        self._scatter_item = None
        self._cage_item = None
        self.gl_view = new_view

        if self._current_lut is not None:
            # Only the 3D scatter/cage need redoing -- the curve plot
            # doesn't depend on gl_view at all, and re-running it here
            # would just be redundant work on every float/dock cycle.
            self._update_3d_view(self._current_lut)

    def _on_density_changed(self, value):
        self.density_label.setText(f"{value}³ points")
        if self._current_lut is not None:
            # Grid density only affects the 3D scatter/cage -- the 1D
            # curve plot has its own fixed, cheap sample count (see
            # __init__) and doesn't need recomputing here.
            self._update_3d_view(self._current_lut)

    def set_lut(self, lut):
        """lut: anything with .apply(RGB) -- a colour.LUT3D/LUT1D, or the
        result of app_logic.load_lut_any_format(). Updates BOTH the 3D
        scatter+cage and the 1D YRGB curves regardless of which view is
        currently selected, so switching views always shows current data
        (see this module's docstring).

        Skips the actual rebuild (real GL buffer uploads plus a curve
        recompute) while this panel isn't visible -- hidden via the
        Workspace menu, or tabbed behind another panel -- since
        main_window._on_stack_changed calls this on EVERY stack edit,
        including every tick of a live slider drag, whether or not
        anyone can currently see the result. showEvent below runs the
        deferred rebuild the next time this panel actually becomes
        visible again, so it's never stale once shown."""
        self._current_lut = lut
        if not self.isVisible():
            self._stale_lut_pending = True
            return
        self._stale_lut_pending = False
        self._update_3d_view(lut)
        self._update_curve_plot(lut)

    def showEvent(self, event):
        super().showEvent(event)
        if self._stale_lut_pending and self._current_lut is not None:
            self._stale_lut_pending = False
            self._update_3d_view(self._current_lut)
            self._update_curve_plot(self._current_lut)

    def _update_3d_view(self, lut):
        """Rebuilds both the scatter points AND the wireframe cage from
        `lut` at the current Grid density -- the two are computed from
        the exact same identity-grid/lut.apply() call (see
        app_logic.extract_3d_points_for_visualization/
        extract_3d_cage_lines_for_visualization), just projected two
        different ways (a point cloud vs. the lines connecting it), so
        they're always kept in sync by construction, never independently
        stale relative to each other."""
        subsample_size = self.density_slider.value()
        positions, colors = extract_3d_points_for_visualization(
            lut, subsample_size=subsample_size)

        # GLScatterPlotItem expects RGBA; we only have RGB from the LUT.
        rgba = np.concatenate([colors, np.ones((colors.shape[0], 1))], axis=1)

        if self._scatter_item is not None:
            self.gl_view.removeItem(self._scatter_item)

        self._scatter_item = gl.GLScatterPlotItem(
            pos=positions, color=rgba, size=6, pxMode=True)
        self.gl_view.addItem(self._scatter_item)

        cage_lines = extract_3d_cage_lines_for_visualization(
            lut, subsample_size=subsample_size)

        if self._cage_item is not None:
            self.gl_view.removeItem(self._cage_item)

        if cage_lines.shape[0] > 0:
            self._cage_item = gl.GLLinePlotItem(
                pos=cage_lines, mode="lines", width=1.0, antialias=True)
            self._apply_cage_opacity()
            self.gl_view.addItem(self._cage_item)
        else:
            self._cage_item = None

        self._frame_camera_on(positions)

    def _frame_camera_on(self, positions):
        """Points the camera at the ACTUAL center of the deformed lattice
        set_lut just plotted, and sets distance to comfortably fit it --
        see this module's docstring (CAMERA-CENTERING BUG) for why this
        exists: pyqtgraph's GLViewWidget defaults its camera center to
        the coordinate origin, which is a corner (not the middle) of a
        typical [0,1]^3 LUT cube, and would be wrong in a different way
        for any LUT whose output legitimately lands outside [0,1].
        Recomputed from the real data every call rather than hardcoded,
        so it's correct regardless of what range this particular LUT's
        output actually occupies.

        Runs on every _update_3d_view call, including density-slider-
        driven re-samples of the same LUT (not just the first load of a
        new one) -- harmless when the LUT hasn't changed (same bounding
        box in, same camera position out), and correct is more important
        than avoiding a redundant call here."""
        if positions.size == 0:
            return
        mins = positions.min(axis=0)
        maxs = positions.max(axis=0)
        center = (mins + maxs) / 2.0
        extent = float(np.max(maxs - mins))
        # "2.5x the largest axis extent" is a plain fit-it-in-frame
        # heuristic (with a floor so a near-flat/degenerate LUT, e.g. a
        # pure desaturate, doesn't put the camera implausibly close),
        # not measured against real footage -- flag if real LUTs need a
        # tighter or looser default once this can actually be seen.
        distance = max(extent * 2.5, 0.5)
        self.gl_view.setCameraPosition(
            pos=pg.Vector(float(center[0]), float(center[1]), float(center[2])),
            distance=distance,
        )

    def clear(self):
        if self._scatter_item is not None:
            self.gl_view.removeItem(self._scatter_item)
            self._scatter_item = None
        if self._cage_item is not None:
            self.gl_view.removeItem(self._cage_item)
            self._cage_item = None
        for curve_item in self._curve_items.values():
            curve_item.setData([], [])
        self._current_lut = None
