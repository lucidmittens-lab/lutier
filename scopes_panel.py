"""
scopes_panel.py -- Histogram / Waveform / Vectorscope panel, all three
sharing one QStackedWidget so they occupy a single splitter pane instead of
each needing its own dedicated slot. All three read whichever image
SampleImagePanel is currently previewing (after the stack is applied), so
they always match what's on screen in the wipe compare.

3D Viewer USED to be a fourth tab in this same panel (see main_window.py's
docstring on the toggle-visibility fixes that originally motivated the
merge) -- split back out into its own panel (per Tom, bottom-right, below
Scopes) now that the original worry (the SUM of Stack Params/Scopes/3D
Viewer's minimum heights exceeding the window's available height) turned
out to be a multi-monitor/display-scaling quirk on Tom's machine, not a
real unavoidable layout conflict. See main_window.py's docstring for where
LutViewer3D is built and placed now -- this file no longer imports or
embeds it at all.

All the actual scope math lives in scopes.py and is fully tested there
without Qt (compute_histogram/compute_waveform/compute_vectorscope,
pq_graticule_stops/mv_graticule_stops/percent_graticule_stops) -- this
file is just the widget: a "..." menu to pick which one is showing, a
pyqtgraph plot/image per scope, and turning SampleImagePanel's
after_image_changed signal into a re-render.

Every Waveform-specific control (which channel/mode to show, which
graticule if any) lives in "Waveform Style"/"Waveform Graticule" tree
submenus inside this same "..." menu now (per Tom) -- there's no separate
on-panel row of controls at all anymore, same "toolbar-space-saving"
direction every other panel's "..." menu already follows. Waveform Style
picks RGB Parade/Luma/R/G/B (previously a combo box in its own row);
Waveform Graticule picks None/PQ/mV/% (previously a single "PQ Graticule"
checkbox) -- exactly one of the three reference-line overlays shows at a
time, all built once in _build_waveform_view and toggled by visibility,
never rebuilt.

Uses pyqtgraph's 2D plotting (PlotWidget/ImageItem/PlotDataItem) -- no
dependency on pyqtgraph.opengl (that's lut_viewer_3d.py's, for the now-
separate 3D Viewer panel).

NOT TESTED IN THIS ENVIRONMENT -- same PySide6/pyqtgraph/libEGL wall as the
rest of the GUI layer (see browser_panel.py's docstring). Only py_compile
syntax-checked. Please run main_window.py, import a sample image, and
confirm: (1) Histogram shows three colored curves (+ a white luma curve)
that visibly shift when a LUT changes the image, (2) Waveform's "..." menu
-> Waveform Style switches between RGB Parade (three side-by-side colored
columns) and Luma/R/G/B (single-channel), and Waveform Graticule's
None/PQ/mV/% options each show the right reference lines (PQ:
100/203/1000/4000/10000 nits; mV: 0-700 in steps of 100; %: 0-100 in
steps of 10) at the right vertical positions, one at a time, and
"Graticule Intensity..." dims/brightens whichever one is currently shown
live as the slider moves, and the chosen intensity carries over if you
switch to a different graticule afterward, (3) Vectorscope shows a dot
cluster that moves toward the graticule's outer ring for a highly
saturated image and collapses to the center for a desaturated one.
"""
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QStackedWidget, QToolButton,
    QMenu, QDialog, QSlider, QPushButton,
)

from scopes import (
    compute_histogram, compute_waveform, compute_vectorscope,
    pq_graticule_stops, mv_graticule_stops, percent_graticule_stops,
)
from floating_dialog import make_floating_tool_window, show_floating
# style.HINT_TEXT_HEX -- see style.py's FOURTEENTH PASS docstring: QSS's
# palette(placeholder-text) doesn't reliably survive Qt's style-polish
# pass for arbitrary widgets on this Qt build, so hint-text color is now
# a literal hex interpolated from style.py's single source of truth
# instead, here and everywhere else this app colors hint/muted text.
from style import HINT_TEXT_HEX

SCOPE_HISTOGRAM = "Histogram"
SCOPE_WAVEFORM = "Waveform"
SCOPE_VECTORSCOPE = "Vectorscope"
# Number of vertical buckets compute_waveform bins into -- a module-level
# constant (rather than a bare 256 inline) since every graticule's line
# positions (see _build_waveform_view) need to use the EXACT same value
# compute_waveform was called with in _render_waveform, or the lines would
# land at the wrong height.
WAVEFORM_LEVELS = 256

WAVEFORM_LUMA = "Luma"
WAVEFORM_R = "R"
WAVEFORM_G = "G"
WAVEFORM_B = "B"
WAVEFORM_PARADE = "RGB Parade"
WAVEFORM_STYLE_CHOICES = (WAVEFORM_PARADE, WAVEFORM_LUMA, WAVEFORM_R, WAVEFORM_G, WAVEFORM_B)

# Waveform Graticule tree menu options (per Tom) -- exactly one active at
# a time, "None" meaning no overlay at all. GRATICULE_COLORS gives each
# its own line/label color so switching between them (or briefly
# recalling which is on) doesn't rely on reading the label text alone.
GRATICULE_NONE = "None"
GRATICULE_PQ = "PQ"
GRATICULE_MV = "mV"
GRATICULE_PERCENT = "%"
GRATICULE_CHOICES = (GRATICULE_NONE, GRATICULE_PQ, GRATICULE_MV, GRATICULE_PERCENT)
GRATICULE_COLORS = {
    GRATICULE_PQ: (255, 200, 60),
    GRATICULE_MV: (120, 200, 255),
    GRATICULE_PERCENT: (200, 140, 255),
}

_CHANNEL_COLORS = {"r": (220, 60, 60), "g": (60, 200, 90), "b": (70, 120, 230)}


class ScopesPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._image_array = None
        self._current_scope = SCOPE_HISTOGRAM
        # One dict (scope_name -> QAction) per menu that's ever had
        # populate_menu() called on it -- see that method and
        # _on_scope_changed, which loops over every entry here so the
        # "..." corner menu and MainWindow's top-level "Scopes" menu-bar
        # menu (per Tom) stay checked in sync no matter which one the
        # user actually picked from.
        self._scope_action_sets = []
        # Waveform Style (RGB Parade/Luma/R/G/B) -- same "one dict per menu
        # instance, synced on change" pattern as _scope_action_sets, now
        # that this lives in a "..." submenu instead of an on-panel combo
        # box. See populate_menu/_on_waveform_style_changed.
        self._waveform_style = WAVEFORM_PARADE
        self._style_action_sets = []
        # Waveform Graticule (None/PQ/mV/%) -- same pattern again, one
        # active at a time. self._graticule_lines maps each non-None
        # choice to the list of pg.InfiniteLine items built once in
        # _build_waveform_view; _update_graticule_visibility shows only
        # the currently-selected list's lines and hides the other two.
        self._waveform_graticule = GRATICULE_NONE
        self._graticule_action_sets = []
        self._graticule_lines = {}
        # Graticule Intensity (per Tom) -- a single opacity shared by
        # whichever graticule is active (switching graticules doesn't
        # reset it). 1.0 = fully opaque, matching the original look
        # before this dialog existed. See _build_graticule_opacity_dialog/
        # _apply_graticule_opacity.
        self._graticule_opacity = 1.0
        self._build_layout()
        self._build_graticule_opacity_dialog()

    # ---- layout ---------------------------------------------------------

    def _build_layout(self):
        layout = QVBoxLayout(self)

        # No on-panel controls at all anymore -- which scope is showing,
        # Waveform Style, and Waveform Graticule are all picked from the
        # "..." header menu now (see _build_menu_button/populate_menu
        # below / corner_widget on PanelFrame, main_window.py), same
        # consolidation as Viewer's and Stack's menus.
        self._build_menu_button()

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, stretch=1)

        self.stack.addWidget(self._build_histogram_view())
        self.stack.addWidget(self._build_waveform_view())
        self.stack.addWidget(self._build_vectorscope_view())

        self.no_image_label = QLabel(
            "Import a sample image (Sample Image panel) to see scopes.")
        self.no_image_label.setStyleSheet(f"color: {HINT_TEXT_HEX}; font-size: 11px;")
        layout.addWidget(self.no_image_label)

        self._on_scope_changed(SCOPE_HISTOGRAM)

    def _build_menu_button(self):
        """The Scopes panel's "..." header menu -- not added to this
        panel's own layout, MainWindow hands it to PanelFrame as
        corner_widget the same way it does for Viewer's and Stack's menus,
        see their docstrings."""
        self.menu_button = QToolButton()
        self.menu_button.setObjectName("panelMenuButton")
        self.menu_button.setAutoRaise(True)
        self.menu_button.setText("☰")  # hamburger icon, per Tom (was "...")
        self.menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.menu_button)
        self.populate_menu(menu)
        self.menu_button.setMenu(menu)

    def populate_menu(self, menu):
        """Add the scope-select actions to `menu` -- shared by the "..."
        corner button above and MainWindow's top-level "Scopes" menu-bar
        menu (per Tom, making the "..." menu redundant rather than the
        only way in). Checkable/mutually-exclusive (its own QActionGroup
        PER menu, since a QActionGroup only enforces exclusivity within
        itself) rather than a combo's dropdown list; _on_scope_changed
        does the actual switching either way and keeps every menu built
        by this method (tracked in self._scope_action_sets) in sync with
        each other, regardless of which one the user actually picked
        from.

        "Waveform Style" and "Waveform Graticule" below are tree (sub)menus
        -- per Tom, moved out of the on-panel row entirely -- each built
        by _build_exclusive_submenu, the same "one dict per menu instance,
        synced on change" idea as the scope-select group above, just
        nested one level deeper since these are sub-choices of Waveform
        specifically rather than peers of "which scope" itself."""
        # Ctrl+1/2/3, per Tom -- Ctrl (Cmd on macOS) is the one modifier
        # that never composes/types a character into a focused text field,
        # so it's the only single-key-plus-modifier option that's safe as
        # a WindowShortcut-context QAction here (see the keyboard-shortcuts
        # conversation in RUNNING.md for the fuller reasoning).
        scope_shortcuts = {
            SCOPE_HISTOGRAM: "Ctrl+1", SCOPE_WAVEFORM: "Ctrl+2", SCOPE_VECTORSCOPE: "Ctrl+3",
        }
        action_group = QActionGroup(menu)
        action_group.setExclusive(True)
        actions = {}
        for name in (SCOPE_HISTOGRAM, SCOPE_WAVEFORM, SCOPE_VECTORSCOPE):
            action = menu.addAction(name)
            action.setShortcut(QKeySequence(scope_shortcuts[name]))
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, n=name: self._on_scope_changed(n))
            action_group.addAction(action)
            actions[name] = action
        actions[self._current_scope].setChecked(True)
        self._scope_action_sets.append(actions)

        menu.addSeparator()
        style_menu = menu.addMenu("Waveform Style")
        self._build_exclusive_submenu(
            style_menu, WAVEFORM_STYLE_CHOICES, self._waveform_style,
            self._on_waveform_style_changed, self._style_action_sets)

        graticule_menu = menu.addMenu("Waveform Graticule")
        self._build_exclusive_submenu(
            graticule_menu, GRATICULE_CHOICES, self._waveform_graticule,
            self._on_waveform_graticule_changed, self._graticule_action_sets)

        # Graticule Intensity (per Tom) -- a plain dialog, not another
        # submenu (it's a continuous value, not a discrete choice), same
        # "one shared dialog instance, shown from any menu copy" pattern
        # as SampleImagePanel's Adapt.../Adapt Reference... dialogs.
        menu.addAction("Graticule Intensity...", self.show_graticule_opacity_dialog)

        return actions

    @staticmethod
    def _build_exclusive_submenu(submenu, choices, current_choice, on_selected, action_sets_list):
        """Populate `submenu` with one checkable, mutually exclusive
        (QActionGroup) entry per `choices`, matching `current_choice`'s
        checked state -- and record the resulting {choice: action} dict
        into `action_sets_list` so a selection made here can be mirrored
        into every OTHER menu instance's matching submenu (see
        _on_waveform_style_changed/_on_waveform_graticule_changed, which
        loop over exactly these lists the same way _on_scope_changed loops
        over self._scope_action_sets)."""
        group = QActionGroup(submenu)
        group.setExclusive(True)
        actions = {}
        for choice in choices:
            action = submenu.addAction(choice)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, c=choice: on_selected(c))
            group.addAction(action)
            actions[choice] = action
        actions[current_choice].setChecked(True)
        action_sets_list.append(actions)
        return actions

    def _build_histogram_view(self):
        self.histogram_plot = pg.PlotWidget()
        self.histogram_plot.setBackground(None)
        self.histogram_plot.setLabel("bottom", "Value")
        self.histogram_plot.setLabel("left", "Pixel count")
        self.histogram_plot.setXRange(0.0, 1.0, padding=0)
        self.histogram_plot.setMouseEnabled(x=False, y=False)

        self._histogram_curves = {}
        for channel, color in {**_CHANNEL_COLORS, "luma": (230, 230, 230)}.items():
            pen = pg.mkPen(color=color, width=1.5)
            self._histogram_curves[channel] = self.histogram_plot.plot([], [], pen=pen)
        return self.histogram_plot

    def _build_waveform_view(self):
        self.waveform_plot = pg.PlotWidget()
        self.waveform_plot.setBackground((15, 15, 18))
        self.waveform_plot.setLabel("bottom", "X position")
        self.waveform_plot.setLabel("left", "Level")
        self.waveform_plot.setMouseEnabled(x=False, y=False)
        self.waveform_plot.getPlotItem().hideAxis("left")

        self.waveform_image_item = pg.ImageItem()
        self.waveform_plot.addItem(self.waveform_image_item)

        # Waveform Graticule (per Tom) -- three alternative sets of dashed
        # reference lines (PQ nit levels / mV / %), picked via the "..."
        # menu's "Waveform Graticule" tree (see populate_menu/
        # _on_waveform_graticule_changed); exactly one set is visible at a
        # time (or none, for GRATICULE_NONE). All built once here (static
        # -- the code value each stop maps to never changes) rather than
        # per render; only visibility toggles afterward. Positioned in the
        # SAME implicit data-coordinate space compute_waveform's image
        # already renders in -- no explicit setRect/invertY anywhere in
        # this file, and no row flip in _render_waveform either (see that
        # method's comment -- pyqtgraph's ImageItem already puts row 0 at
        # the bottom of the widget by default under this app's config, a
        # real bug fix, not always true), so a stop's y position is just
        # its code value (0=darkest..1=brightest) scaled by
        # WAVEFORM_LEVELS, matching the SAME levels compute_waveform is
        # called with below -- this was already correct even while
        # _render_waveform's now-removed flip made the actual image
        # disagree with it.
        # pg.InfiniteLine's own label positions itself relative to the
        # current view range, so it tracks correctly even as the plot's
        # x-extent changes with each new image's width.
        stops_and_labels_by_graticule = {
            GRATICULE_PQ: [(f"{nits} nits", code_value) for nits, code_value in pq_graticule_stops()],
            GRATICULE_MV: [(f"{mv} mV", code_value) for mv, code_value in mv_graticule_stops()],
            GRATICULE_PERCENT: [(f"{pct}%", code_value) for pct, code_value in percent_graticule_stops()],
        }
        self._graticule_lines = {}
        for graticule_name, stops_and_labels in stops_and_labels_by_graticule.items():
            color = GRATICULE_COLORS[graticule_name]
            lines = []
            for label, code_value in stops_and_labels:
                line = pg.InfiniteLine(
                    pos=code_value * WAVEFORM_LEVELS, angle=0,
                    pen=pg.mkPen(color=color, width=1,
                                 style=pg.QtCore.Qt.PenStyle.DashLine),
                    label=label,
                    labelOpts={"position": 0.97, "color": color},
                    movable=False,
                )
                line.setVisible(False)  # GRATICULE_NONE is the default -- see __init__
                self.waveform_plot.addItem(line)
                lines.append(line)
            self._graticule_lines[graticule_name] = lines

        return self.waveform_plot

    def _build_vectorscope_view(self):
        self.vectorscope_plot = pg.PlotWidget()
        self.vectorscope_plot.setBackground((15, 15, 18))
        self.vectorscope_plot.setAspectLocked(True)
        self.vectorscope_plot.setMouseEnabled(x=False, y=False)
        self.vectorscope_plot.hideAxis("bottom")
        self.vectorscope_plot.hideAxis("left")

        self.vectorscope_image_item = pg.ImageItem()
        self.vectorscope_plot.addItem(self.vectorscope_image_item)

        # Graticule: outer ring at chroma magnitude 0.5 (100% saturation,
        # by this app's normalization -- see scopes.compute_chroma_plane)
        # plus a center cross. Purely a static reference overlay, not
        # interactive/editable.
        theta = np.linspace(0, 2 * np.pi, 200)
        ring = pg.PlotDataItem(
            0.5 * np.cos(theta), 0.5 * np.sin(theta),
            pen=pg.mkPen(color=(110, 110, 110), width=1),
        )
        self.vectorscope_plot.addItem(ring)
        cross_pen = pg.mkPen(color=(110, 110, 110), width=1)
        self.vectorscope_plot.addItem(pg.PlotDataItem([-0.5, 0.5], [0, 0], pen=cross_pen))
        self.vectorscope_plot.addItem(pg.PlotDataItem([0, 0], [-0.5, 0.5], pen=cross_pen))
        self.vectorscope_plot.setRange(xRange=(-0.6, 0.6), yRange=(-0.6, 0.6), padding=0)
        return self.vectorscope_plot

    def _build_graticule_opacity_dialog(self):
        """"Graticule Intensity..." dialog (per Tom) -- a single slider
        controlling how opaque the currently-built graticule line items
        are, shared across all three graticules (switching which
        graticule is active via the tree menu doesn't reset this). Live
        apply on every slider move (see _on_graticule_opacity_changed),
        same "no separate commit step" convention as SampleImagePanel's
        Adapt dialogs -- "Close" just dismisses, nothing is pending."""
        dialog = QDialog(self)
        make_floating_tool_window(dialog)
        dialog.setWindowTitle("Graticule Intensity")
        outer = QVBoxLayout(dialog)

        row = QHBoxLayout()
        row.addWidget(QLabel("Intensity:"))
        self.graticule_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.graticule_opacity_slider.setMinimum(0)
        self.graticule_opacity_slider.setMaximum(100)
        self.graticule_opacity_slider.setValue(round(self._graticule_opacity * 100))
        self.graticule_opacity_slider.valueChanged.connect(self._on_graticule_opacity_changed)
        row.addWidget(self.graticule_opacity_slider, stretch=1)
        self.graticule_opacity_value_label = QLabel(f"{round(self._graticule_opacity * 100)}%")
        row.addWidget(self.graticule_opacity_value_label)
        outer.addLayout(row)

        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.close)
        outer.addWidget(close_button)

        self._graticule_opacity_dialog = dialog

    def show_graticule_opacity_dialog(self):
        show_floating(self._graticule_opacity_dialog)

    def _on_graticule_opacity_changed(self, value):
        self._graticule_opacity = value / 100.0
        self.graticule_opacity_value_label.setText(f"{value}%")
        self._apply_graticule_opacity()

    def _apply_graticule_opacity(self):
        """Re-applies self._graticule_opacity to EVERY graticule's line
        items, not just the currently-visible one -- so switching
        Waveform Graticule afterward shows the newly-selected set at the
        same intensity rather than snapping back to fully opaque."""
        alpha = round(self._graticule_opacity * 255)
        for graticule_name, lines in self._graticule_lines.items():
            color = GRATICULE_COLORS[graticule_name]
            pen = pg.mkPen(color=(*color, alpha), width=1,
                            style=pg.QtCore.Qt.PenStyle.DashLine)
            for line in lines:
                line.setPen(pen)

    # ---- external API -----------------------------------------------------

    def set_image_array(self, image_array):
        """Called whenever SampleImagePanel's previewed "after" image
        changes (see main_window.py wiring: sample_image.after_image_changed
        -> scopes_panel.set_image_array). `image_array` may be None (no
        image loaded, or the current mode has nothing to show), in which
        case all three scopes clear and a hint label shows instead."""
        self._image_array = image_array
        self._render_current_scope()

    # ---- rendering ---------------------------------------------------------

    def _on_scope_changed(self, scope_name):
        self._current_scope = scope_name
        # Keep EVERY menu's checked state in sync -- there are now two
        # (the "..." corner menu and MainWindow's top-level "Scopes"
        # menu-bar menu, per Tom), each with its own QActionGroup, so
        # picking a scope from either one needs to update the other's
        # action too (Qt's own exclusivity only applies within a single
        # QActionGroup, not across the two separate ones populate_menu()
        # builds).
        for actions in self._scope_action_sets:
            action = actions.get(scope_name)
            if action is not None and not action.isChecked():
                action.setChecked(True)
        index = {SCOPE_HISTOGRAM: 0, SCOPE_WAVEFORM: 1,
                  SCOPE_VECTORSCOPE: 2}[scope_name]
        self.stack.setCurrentIndex(index)
        self._render_current_scope()

    def _on_waveform_style_changed(self, style_name):
        """Wired to every "Waveform Style" submenu's QActionGroup (one per
        menu instance) -- same cross-instance sync idea as
        _on_scope_changed: only the action matching `style_name` ends up
        checked, in every submenu instance, not just the one that was
        clicked."""
        self._waveform_style = style_name
        for actions in self._style_action_sets:
            action = actions.get(style_name)
            if action is not None and not action.isChecked():
                action.setChecked(True)
        self._render_waveform()

    def _on_waveform_graticule_changed(self, graticule_name):
        """Wired to every "Waveform Graticule" submenu's QActionGroup (one
        per menu instance) -- same cross-instance sync idea as
        _on_waveform_style_changed, plus showing exactly the newly-
        selected graticule's line items (built once in
        _build_waveform_view) and hiding the other two (GRATICULE_NONE
        hides all three)."""
        self._waveform_graticule = graticule_name
        for actions in self._graticule_action_sets:
            action = actions.get(graticule_name)
            if action is not None and not action.isChecked():
                action.setChecked(True)
        for name, lines in self._graticule_lines.items():
            visible = name == graticule_name
            for line in lines:
                line.setVisible(visible)

    def _render_current_scope(self):
        scope_name = self._current_scope
        if self._image_array is None:
            self.no_image_label.setVisible(True)
            self.stack.setVisible(False)
            return
        self.no_image_label.setVisible(False)
        self.stack.setVisible(True)

        if scope_name == SCOPE_HISTOGRAM:
            self._render_histogram()
        elif scope_name == SCOPE_WAVEFORM:
            self._render_waveform()
        else:
            self._render_vectorscope()

    def _render_histogram(self):
        hist = compute_histogram(self._image_array, bins=256)
        edges = hist["bin_edges"]
        centers = (edges[:-1] + edges[1:]) / 2.0
        for channel, curve in self._histogram_curves.items():
            curve.setData(centers, hist[channel])

    def _render_waveform(self):
        if self._image_array is None:
            return
        # levels=WAVEFORM_LEVELS (not a bare literal) since the graticule
        # lines built once in _build_waveform_view were positioned
        # assuming this exact same value -- keep the two in sync by
        # construction rather than by two separately-maintained numbers.
        wf = compute_waveform(self._image_array, levels=WAVEFORM_LEVELS)
        channel = self._waveform_style

        if channel == WAVEFORM_PARADE:
            image = np.stack(
                [self._normalize_for_display(wf["r"]),
                 self._normalize_for_display(wf["g"]),
                 self._normalize_for_display(wf["b"])],
                axis=-1,
            )
        else:
            key = {WAVEFORM_LUMA: "luma", WAVEFORM_R: "r",
                   WAVEFORM_G: "g", WAVEFORM_B: "b"}[channel]
            mono = self._normalize_for_display(wf[key])
            image = np.stack([mono, mono, mono], axis=-1)

        # WAS: image = np.flipud(image) here, on the theory that
        # pyqtgraph's ImageItem draws row 0 at the top by default. That
        # was wrong -- checked empirically (rendered a real ImageItem
        # offscreen with a distinct value per row and sampled actual
        # pixel colors): with this app's config (imageAxisOrder=
        # 'col-major', the transpose below, no invertY() call on the
        # ViewBox anywhere in this file), row 0 already lands at the
        # BOTTOM of the widget and the last row at the TOP -- i.e.
        # compute_waveform's row 0 = darkest already reads bottom=dark,
        # top=bright with NO flip needed. The removed flipud was
        # inverting an already-correctly-oriented image, which is
        # exactly the bug Tom reported: the waveform trace read
        # upside-down (bright content bunched toward the bottom, e.g.
        # after an HDR limiter push) while the graticule lines -- placed
        # independently via pq_graticule_stops()/etc in
        # _build_waveform_view, never touched by this flip -- stayed
        # correctly positioned, so the two visibly disagreed.
        #
        # ImageItem expects (col, row, rgb) i.e. transposed from our
        # (row, col, rgb) array -- swap the first two axes.
        self.waveform_image_item.setImage(np.transpose(image, (1, 0, 2)), levels=(0, 1))

    def _render_vectorscope(self):
        vs = compute_vectorscope(self._image_array, bins=256)
        density = vs["density"]
        lo, hi = vs["chroma_range"]
        # Log-scale so the usual dense near-center cluster doesn't wash out
        # rarer highly-saturated pixels entirely.
        display_data = np.log1p(density)
        self.vectorscope_image_item.setImage(display_data, autoLevels=True)
        self.vectorscope_image_item.setRect(pg.QtCore.QRectF(lo, lo, hi - lo, hi - lo))

    @staticmethod
    def _normalize_for_display(count_array):
        """Per-array max-normalize to [0,1] with a mild log compression, so
        a single very tall spike (e.g. a large flat-color region) doesn't
        crush everything else in the same waveform to near-invisible."""
        compressed = np.log1p(count_array.astype(np.float64))
        peak = compressed.max()
        if peak <= 0:
            return compressed
        return compressed / peak
