"""main_window_parts/docks.py -- MainWindow's dock system: the dock widget and
its custom title bar, the default dock layout and proportions, launch
sizing, floating/redocking panels, and reparenting GL panels safely. A
mixin: MainWindow inherits it, so these methods share the window's own
`self`.
"""
from functools import partial
from PySide6.QtWidgets import (
    QApplication, QDockWidget, QHBoxLayout, QLabel, QToolButton, QWidget,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtCore import QByteArray, QTimer, Qt
from panel_frame import PanelFrame


# Default column widths as FRACTIONS of the window's real width (Browser
# column / Viewer column / Parameters-Scopes-Graph column). Fractions,
# not pixels, because they're applied after the window reaches its final
# maximized size -- fixed pixels applied at the 1200px construction size
# left a maximized window's extra width wherever Qt happened to put it.
# The right column is deliberately narrow (per Tom: "make the graph,
# params and scopes more narrow by default").
_DEFAULT_COLUMN_FRACTIONS = (0.16, 0.60, 0.24)
# Same idea down each column, top to bottom.
_DEFAULT_LEFT_FRACTIONS = (0.31, 0.23, 0.23, 0.23)      # Browser / Media Libraries / LUT Library / Galleries
_DEFAULT_CENTER_FRACTIONS = (0.52, 0.24, 0.24)          # Viewer / Stack / Timeline
_DEFAULT_RIGHT_FRACTIONS = (0.30, 0.32, 0.38)           # Parameters / Scopes / Graph


class _PanelDockWidget(QDockWidget):
    """QDockWidget subclass with exactly one behavior change: closing a
    FLOATING panel via its native titlebar close button HIDES it (stays
    floating internally, just not visible) instead of trying to redock it.

    HISTORY (kept for context -- #58 through #60 all tried variations of
    "redock it correctly on close" before Tom, on RUNNING.md #61, asked
    for this simpler behavior instead): #58 fixed a stray-placement crash
    via allowedAreas; #59's attempted follow-up (tracking a coarse
    Qt.DockWidgetArea per panel) turned out to be built on a false premise
    -- every panel in this app's single nested split-tree reports the
    SAME dockWidgetArea() regardless of real position, so that "fix"
    wasn't fixing anything; #60 replaced it with QMainWindow.saveState()/
    restoreState(), confirmed to reproduce a panel's exact pixel position
    -- genuinely correct when it applies, but Tom found a NEW failure mode
    on real hardware: if `_last_all_docked_state` doesn't fully agree with
    the window's current state (or more than one panel is floating,
    where #60 deliberately declined to use it at all -- see #60's own
    entry), restoreState() can silently no-op, leaving the panel exactly
    where it was: still floating, unresponsive to the X button, per Tom
    "if home is lost it refuses to go anywhere." Rather than keep
    patching an ever-growing set of "redock correctly" edge cases, #61
    sidesteps the entire problem: closing a floating panel no longer
    tries to figure out where it goes at all, it just hides -- there's no
    "home" to lose, so there's nothing left to refuse.

    isFloating() stays True the whole time (deliberately not reset to
    False) -- re-showing the panel later (Workspace menu, its shortcut)
    brings it back exactly as a floating window in its last position,
    consistent with "closing it didn't change anything about its
    dock/float state, only its visibility," the same relationship a
    DOCKED panel's close already has to its own dockedness (see below).
    `_floating_panels`/the Float-menu-checkbox bookkeeping in
    _on_dock_top_level_changed is untouched by this (no topLevelChanged
    fires from a plain hide) -- both correctly continue reporting this
    panel as floating, which it still is.

    Docked panels are unaffected -- closeEvent only intervenes while
    isFloating() is True; a docked panel's close (via the title bar's own
    close button, added per Tom -- see _build_dock_title_bar; Qt's native
    DockWidgetClosable X was never part of this custom title bar, but this
    button calls the same dock.close() Qt's own X would) falls through to
    the normal hide behavior, same as `toggleViewAction` unchecking it --
    that path was always reachable via the Workspace menu/shortcut even
    before this button existed, this is just a second, on-panel way in.
    RUNNING.md #61 makes the floating case behave the same way in this
    one specific respect (X hides, doesn't reposition), while leaving the
    two states otherwise exactly as different as they've always been
    (docked-and-hidden vs. floating-and-hidden are still tracked
    separately, per isFloating()).

    MainWindow._dock_panel (the Workspace menu's Float-uncheck path, and
    _dock_all_floating_panels' session-load/app-quit/reset-to-default
    safety net) is UNCHANGED -- still uses #60's saveState()/restoreState()
    redock logic. Tom's ask was specifically about the native X button,
    not about those other, deliberate ways to redock a panel."""

    def closeEvent(self, event):
        if self.isFloating():
            event.ignore()
            self.setVisible(False)
        else:
            super().closeEvent(event)


def _build_dock_title_bar(dock, title, corner_widget=None):
    """Custom title bar widget for a panel's QDockWidget -- reuses the
    EXACT #panelTitleRow/#panelTitle object names style.py already styles
    for PanelFrame's own (now-unused-for-docks) title row, so the visual
    language is unchanged even though PanelFrame itself no longer draws a
    title for panels built this way (see _make_dock -- PanelFrame is
    still used underneath, purely for the border/background; its own
    title-row capability was removed entirely 2026-07-25, see
    panel_frame.py). corner_widget (optional) is the SAME "..." menu
    button each panel already builds (self.viewer_3d.menu_button etc.) --
    placed on the same row as the title, right-aligned, same spot it sat
    in PanelFrame's own title row before this migration.

    Close button (per Tom, added after the fact -- originally this row
    was just the label + corner_widget, with float/close reachable only
    via the Workspace menu/shortcuts, since replacing Qt's native title
    bar with this custom one also silently threw away its native float/
    close buttons; see _PanelDockWidget's docstring). Built as a plain
    QToolButton rather than relying on QDockWidget's own ::close-button
    QSS subcontrol -- an ordinary button is fully stylable with this
    file's existing QToolButton/QPushButton rules and sits in this same
    custom row next to corner_widget, where a native subcontrol button
    (tied to Qt's own title bar) couldn't. `dock` (the QDockWidget this
    title bar belongs to) needs to be passed in now, unlike before, so
    this button has something to call close() on.

    Text glyph ("✕"), not a QStyle standard icon (per Tom -- the "..."
    corner menu became a "☰" text glyph, and the close button's old
    QStyle.standardIcon() pixmap read as visually mismatched next to it,
    a different rendering style sitting in the same row). A plain text
    QToolButton is also colorable via QSS `color` the way a pixmap icon
    never was, so both buttons can be kept in visual sync going forward.

    ALWAYS LAST in the row (per Tom: "the X always on the right... all
    the way at the end, so it's in the same place on a given panel") --
    corner_widget is added to the layout BEFORE close_button below, not
    after, specifically so close_button's position never depends on
    whether a given panel happens to have a corner_widget at all. Before
    this, a panel WITH a corner_widget put it to the right of Close
    (Close in the middle), while a panel with none had Close at the true
    end -- two different positions depending on panel, not the
    consistent "always the rightmost button" this fixes.

    NO float button here (per Tom, removed): dragging a panel by this
    title bar already floats it via Qt's own native drag-to-float, and
    the float button that used to sit here called dock.setFloating()
    directly, bypassing _float_panel/_dock_panel's _reparent_gl_safely
    wrapper (which suspends GL paint delivery during the transition and
    triggers _rebuild_gl_content_for_panel afterward) -- exactly the
    protection a GL-backed panel (the 3D Viewer) needs to not render
    broken after a float/dock transition. The Workspace menu's per-panel
    Float checkbox and the Cmd+Shift+<N> shortcuts (_make_float_action)
    still exist and still go through that safe wrapper -- only this
    redundant, unsafe title-bar shortcut to the same action is gone.

    WA_StyledBackground (per Tom, real bug caught on real hardware --
    RUNNING.md #54) -- a plain QWidget does NOT clip its background to a
    QSS border-radius by default; only QFrame (see PanelFrame's
    setFrameShape(StyledPanel)) paints its stylesheet background/border
    in a radius-aware way out of the box. Without this attribute,
    row_widget still painted its full SQUARE auto-fill background
    underneath the rounded border line style.py's #panelTitleRow rule
    draws, so the actual corners showed as a square patch of palette(base)
    poking out past the rounded outline -- exactly the "extra gray on the
    corners" artifact, invisible before this pass only because
    panelTitleRow never had a border-radius to clip to until RUNNING.md
    #53. Setting this forces Qt to paint the ENTIRE background through
    the style/stylesheet system, radius and all, same as QFrame already
    does natively."""
    row_widget = QWidget()
    row_widget.setObjectName("panelTitleRow")
    row_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    row = QHBoxLayout(row_widget)
    row.setContentsMargins(8, 4, 8, 4)
    label = QLabel(title)
    label.setObjectName("panelTitle")
    row.addWidget(label, stretch=1)

    close_button = QToolButton()
    close_button.setObjectName("dockCloseButton")
    close_button.setAutoRaise(True)
    close_button.setToolTip("Close")
    close_button.setText("✕")
    # dock.close() -- NOT dock.hide() -- so this goes through
    # _PanelDockWidget's own closeEvent override exactly like Qt's native
    # close button would have: redocks if floating, hides (in sync with
    # toggleViewAction/the Workspace menu) if already docked.
    close_button.clicked.connect(dock.close)

    # corner_widget BEFORE close_button -- see this function's own
    # docstring on why close_button must always be the last (rightmost)
    # widget in the row regardless of whether a given panel has one.
    if corner_widget is not None:
        row.addWidget(corner_widget)
    row.addWidget(close_button)
    return row_widget


class DockMixin:
    def _make_dock(self, frame_attr, label, content_widget, corner_widget=None):
        """Build one panel's QDockWidget -- wraps content_widget in a
        border-only PanelFrame for visual continuity, gives
        it a custom title bar (see _build_dock_title_bar) carrying the
        label and, if given, the panel's own "..." menu button, and wires
        `topLevelChanged` to `_on_dock_top_level_changed` so a float/dock
        transition is tracked correctly no matter what triggers it (menu,
        shortcut, or a user drag -- see this module's docstring).
        Stores the result on self under `frame_attr` (e.g.
        self.browser_frame) and also returns it, matching this file's
        existing "self.X_frame = ..." call-site style from before the
        docking migration."""
        dock = _PanelDockWidget(label, self)
        dock.setObjectName(frame_attr)
        # Plain Python attribute (not a Qt property) -- RUNNING.md #60,
        # so _PanelDockWidget.closeEvent can tell whether IT is the only
        # currently-floating panel without MainWindow having to hand it a
        # frame_attr string at every call site. See closeEvent's docstring.
        dock._frame_attr = frame_attr
        # WA_StyledBackground + QSS #52/#53/#54 aftermath (RUNNING.md #55) --
        # the real, composited bug Tom kept seeing wasn't panelTitleRow's
        # own corners at all (that part was already fixed by #54) -- it
        # was QMainWindow's own herringbone-textured background bleeding
        # through the QDockWidget's bare rectangular cell wherever
        # panelTitleRow/panelFrame's rounded corners recede from it (every
        # outer corner, plus the deliberate 4px gap between the two). A
        # widget.grab() of just the dock in isolation (what this session's
        # earlier headless verification used) fills that recess with a
        # neutral default instead of actually compositing what's behind it
        # in the real window, which is exactly why that verification
        # missed this -- confirmed by re-grabbing the FULL MainWindow
        # instead and reproducing the identical artifact Tom described.
        # Giving the dock itself an opaque palette(base) backing (see
        # style.py's QDockWidget rule) turns that recess into a solid
        # color matching the card content around it instead of textured
        # bleed-through -- QDockWidget needs WA_StyledBackground for the
        # same reason panelTitleRow did (#54): it's not a QFrame, so it
        # won't paint a QSS background-color without this set explicitly.
        dock.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        dock.setTitleBarWidget(_build_dock_title_bar(dock, label, corner_widget))
        dock.setWidget(PanelFrame(content_widget))
        dock.topLevelChanged.connect(partial(self._on_dock_top_level_changed, frame_attr))
        setattr(self, frame_attr, dock)
        return dock

    def _snapshot_all_docked_state(self):
        """Records a saveState() blob of the WHOLE window into
        _last_all_docked_state (RUNNING.md #60, superseding #59's
        per-panel dockWidgetArea() tracking -- see _PanelDockWidget.
        closeEvent's docstring for why that approach turned out to be
        fundamentally too coarse: every panel in this app's single nested
        split-tree reports the SAME dockWidgetArea(), regardless of which
        column it actually lives in). Called once right after the default
        layout is (re)built from scratch (startup, Workspace > Reset to
        Default) AND once after a remembered/imported workspace's saved
        dock state is actually applied (_apply_dock_state) -- neither
        addDockWidget/splitDockWidget's initial placement NOR
        restoreState() fires topLevelChanged for a widget that stays
        docked throughout (that signal only fires on an actual
        floating-state TRANSITION), so without an explicit call at each of
        these "the real layout just changed out from under any earlier
        snapshot" points, _last_all_docked_state would silently keep
        describing a superseded arrangement. Every subsequent successful
        return-to-fully-docked keeps this current via
        _on_dock_top_level_changed instead -- see that method."""
        self._last_all_docked_state = bytes(self.saveState())

    def _build_default_dock_layout(self):
        """(Re)establish the DEFAULT spatial arrangement (see this
        module's docstring for the ASCII diagram) using QMainWindow's own
        addDockWidget/splitDockWidget/resizeDocks -- called once from
        _build_layout at startup, and again by _reset_workspace_to_default
        (per Tom's "Reset to Default Workspace" menu entry, unchanged in
        spirit from before the docking migration). Purely a STARTING
        position now, not a structural constraint -- every dock can be
        dragged anywhere afterward.

        Ordering matters: the three-column split happens FIRST, via one
        representative dock per column (Browser/Viewer/Parameters) --
        THEN each column's remaining members are stacked vertically
        underneath their column's own anchor. Splitting vertically
        BEFORE the three-column horizontal split would stack the whole
        window's height instead of just one column's width (confirmed
        empirically in this sandbox) -- this ordering is what confines
        each vertical split to its own column."""
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.browser_frame)
        self.splitDockWidget(self.browser_frame, self.sample_image_frame, Qt.Orientation.Horizontal)
        self.splitDockWidget(self.sample_image_frame, self.stack_params_frame, Qt.Orientation.Horizontal)

        # left column: Browser / Media Libraries / LUT Library /
        # Galleries, top to bottom.
        self.splitDockWidget(self.browser_frame, self.image_library_frame, Qt.Orientation.Vertical)
        self.splitDockWidget(self.image_library_frame, self.lut_library_frame, Qt.Orientation.Vertical)
        self.splitDockWidget(self.lut_library_frame, self.stack_memories_frame, Qt.Orientation.Vertical)

        # center column: Viewer / Stack / Timeline, top to bottom -- per
        # Tom, "put timeline bottom center so it has more real estate":
        # the center column is the widest of the three (700px vs. 260px
        # for the side columns in the proportions below), and previously
        # unused space at the bottom of it, so the Timeline moved here
        # from the bottom of the left column gets both a much wider track
        # view (more clips/more zoom visible without scrolling) and its
        # own dedicated vertical space instead of competing for height
        # with three other left-column panels.
        self.splitDockWidget(self.sample_image_frame, self.stack_frame, Qt.Orientation.Vertical)
        self.splitDockWidget(self.stack_frame, self.timeline_frame, Qt.Orientation.Vertical)

        # right column: Parameters / Scopes / Graph, top to bottom.
        self.splitDockWidget(self.stack_params_frame, self.scopes_frame, Qt.Orientation.Vertical)
        self.splitDockWidget(self.scopes_frame, self.viewer_3d_frame, Qt.Orientation.Vertical)

        # Source beside Record -- split AFTER the center column's vertical
        # splits, so the two viewers share the top of the column and Stack/
        # Timeline span both underneath.
        self._place_source_viewer_beside_record()

        self._apply_default_dock_proportions()

    def _place_source_viewer_beside_record(self):
        """Source to the LEFT of Record (the Avid/Resolve convention), half
        each. splitDockWidget only puts the second dock to the right of the
        first, so Record is re-split off Source afterwards."""
        # Widths BEFORE the split: Record's is shared between the two
        # viewers, and the outer columns keep theirs (Qt otherwise takes
        # the new dock's room from them).
        total = self.sample_image_frame.width()
        outer = [(d, d.width()) for d in (self.browser_frame, self.stack_params_frame)
                 if d.isVisible() and not d.isFloating()]
        self.splitDockWidget(self.sample_image_frame, self.source_viewer_frame, Qt.Orientation.Horizontal)
        self.splitDockWidget(self.source_viewer_frame, self.sample_image_frame, Qt.Orientation.Horizontal)
        self.source_viewer_frame.setVisible(True)
        half = max(1, total // 2)
        docks = [d for d, _ in outer] + [self.source_viewer_frame, self.sample_image_frame]
        widths = [w for _, w in outer] + [half, half]
        # A tick later: right after a split Qt hasn't laid the new dock out
        # yet, and resizing now works from stale sizes (measured: Source
        # took Record's whole width and the window grew past the screen).
        QTimer.singleShot(0, lambda: self.resizeDocks(docks, widths, Qt.Orientation.Horizontal))

    def _apply_default_dock_proportions(self):
        """Size the default columns (and each column's panels) to the
        _DEFAULT_*_FRACTIONS of the window's CURRENT size. Called from _build_default_dock_layout
        and again from resizeEvent while the window is still settling
        into its launch size (see show_on_launch)."""
        width, height = max(1, self.width()), max(1, self.height())

        def fit(docks, fractions, total, orientation):
            self.resizeDocks(docks, [round(total * f) for f in fractions], orientation)

        fit([self.browser_frame, self.sample_image_frame, self.stack_params_frame],
            _DEFAULT_COLUMN_FRACTIONS, width, Qt.Orientation.Horizontal)
        fit([self.browser_frame, self.image_library_frame, self.lut_library_frame,
             self.stack_memories_frame], _DEFAULT_LEFT_FRACTIONS, height, Qt.Orientation.Vertical)
        fit([self.sample_image_frame, self.stack_frame, self.timeline_frame],
            _DEFAULT_CENTER_FRACTIONS, height, Qt.Orientation.Vertical)
        fit([self.stack_params_frame, self.scopes_frame, self.viewer_3d_frame],
            _DEFAULT_RIGHT_FRACTIONS, height, Qt.Orientation.Vertical)

    def show_on_launch(self):
        """window.show() for app startup: maximized unless a remembered
        workspace was explicitly saved un-maximized. When the default
        layout is in use (no remembered dock state), the column widths
        are re-applied on every resize for the first second -- a
        maximize is asynchronous (the window manager decides when the
        final geometry arrives), so a single deferred call could still
        see the pre-maximize width.

        The maximize is re-requested shortly after mapping if it didn't
        stick: GNOME/mutter under XWayland silently drops a maximize set
        before the window is mapped -- reproduced with a bare
        QMainWindow.showMaximized() (2026-10-05, Rocky 10.2), which comes
        back WindowNoState at its 1200x800 construction size. A request
        ~100ms after mapping works there; on other platforms the first
        showMaximized() already sticks and the retries are no-ops."""
        if self._launch_maximized:
            self.showMaximized()
            for delay_ms in (150, 500):
                QTimer.singleShot(delay_ms, self._ensure_launch_maximized)
        else:
            self.show()
        if self._using_default_dock_layout:
            self._settling_default_layout = True
            QTimer.singleShot(1000, self._finish_launch_settling)
        # After the window has settled into its launch size, so the
        # prompt centers over the real (maximized) window.
        QTimer.singleShot(700, self._prompt_project_setup)

    def _ensure_launch_maximized(self):
        if not self.isMaximized():
            self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)

    def _finish_launch_settling(self):
        self._settling_default_layout = False

    def _has_reached_maximized_size(self):
        """By actual geometry, not isMaximized(): that flag already reads
        True from showMaximized()'s pre-map request while the window is
        still at its construction size (and GNOME/XWayland then drops the
        request -- see show_on_launch)."""
        screen = self.screen()
        if screen is None:
            return self.isMaximized()
        return self.width() >= 0.9 * screen.availableGeometry().width()

    def _apply_pending_dock_state(self):
        dock_state_b64, self._pending_dock_state_b64 = (
            getattr(self, "_pending_dock_state_b64", None), None)
        if dock_state_b64:
            self._apply_dock_state(dock_state_b64)

    def _make_float_action(self, menu, action_attr, label, frame_attr, shortcut=None):
        """One checkable Workspace menu entry per panel that pops it out
        into its own top-level window instead of showing/hiding it in
        place -- see _build_menu_bar's docstring, FLOATING, and _float_
        panel/_dock_panel below. Always starts unchecked (nothing floats
        at launch, and _on_dock_top_level_changed keeps this correctly in
        sync afterward regardless of what actually triggers a float/dock
        transition -- this menu, the shortcut, or a drag)."""
        action = QAction(f"Float {label}", self)
        action.setCheckable(True)
        action.blockSignals(True)
        action.setChecked(False)
        action.blockSignals(False)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.toggled.connect(partial(self._on_panel_float_toggled, frame_attr))
        menu.addAction(action)
        setattr(self, action_attr, action)
        return action

    def _on_panel_float_toggled(self, frame_attr, checked):
        if checked:
            self._float_panel(frame_attr)
        else:
            self._dock_panel(frame_attr)

    def _reparent_gl_safely(self, frame, reparent_fn):
        """Runs `reparent_fn()` -- a `dock.setFloating(True)` call in
        _float_panel, or `dock.setFloating(False)` in _dock_panel -- with
        paint delivery suspended for every QOpenGLWidget currently living
        under `frame` (findChildren digs through nested layouts, e.g.
        LutViewer3D's own internal structure, so this isn't hardcoded to
        the Graph panel specifically -- any future panel that adds GL
        content gets the same protection for free).

        Only covers float/dock transitions THIS FILE triggers directly
        (the Workspace menu's Float checkbox, the Cmd+Shift+N shortcut) --
        since the docking migration, a panel can ALSO become floating by
        being dragged out interactively, which Qt performs internally
        with no hook available to suspend paint delivery beforehand. See
        this module's docstring for why that gap is an accepted,
        deliberately-documented risk rather than something this method
        can close.

        Per Tom's crash report: closing/redocking a floated panel
        segfaulted, traced to a paint event landing on the 3D Viewer's
        GLViewWidget mid-reparent, while its GL context was between
        states. setUpdatesEnabled(False) is Qt's own supported way to
        tell a widget "don't schedule or deliver paint events for
        yourself right now" -- covering the actual moment the crash's own
        stack trace showed a paint event racing the reparent. Paired with
        _SafeGLViewWidget's own isValid() guard in lut_viewer_3d.py (a
        second, independent line of defense at the point the GL calls
        would actually happen) rather than relying on either alone.

        update() after re-enabling forces one fresh repaint once the
        widget has actually settled in its new home, rather than leaving
        it showing a stale frame from before the reparent -- relevant to
        the OTHER half of Tom's report ("3D viewer was broken when
        floated"), though this alone may not be the full story there;
        flag if it's still not right after this.

        UNVERIFIED ON REAL HARDWARE -- see lut_viewer_3d.py's docstring."""
        gl_children = frame.findChildren(QOpenGLWidget)
        for widget in gl_children:
            widget.setUpdatesEnabled(False)
        try:
            reparent_fn()
        finally:
            for widget in gl_children:
                widget.setUpdatesEnabled(True)
                widget.update()

    def _rebuild_gl_content_for_panel(self, frame_attr):
        """Per-panel hook for panels whose GL content needs a full rebuild
        (not just an update()) after being floated or docked -- see
        lut_viewer_3d.LutViewer3D.rebuild_gl_view's docstring (RENDER FIX)
        for why _reparent_gl_safely's setUpdatesEnabled dance alone isn't
        enough for the 3D Viewer: the report was a genuinely blank/black
        view, not a stale or missed repaint, meaning the GL context itself
        needs to be recreated from scratch in the widget's final location,
        not just repainted.

        Only viewer_3d_frame has a hook right now -- deliberately a
        one-entry mapping rather than a generic 'every panel must define
        this' interface, since no other panel has GL content today. Add
        an entry here if that changes rather than growing a parallel
        mechanism elsewhere.

        Called from _on_dock_top_level_changed AFTER the transition has
        actually completed (Qt's own signal, fired regardless of what
        triggered it -- see this module's docstring) -- rebuilding any
        earlier would just mean doing it again once the widget settles
        into its real final state."""
        if frame_attr == "viewer_3d_frame":
            self.viewer_3d.rebuild_gl_view()

    def _float_panel(self, frame_attr):
        """Pop the panel at `frame_attr` out of the main window into its
        own floating top-level window -- a thin wrapper over
        QDockWidget.setFloating(True), Qt's own native mechanism (see
        this module's docstring for why this migration REPLACED the old
        hand-rolled setParent()+setWindowFlags() dance #39 built, rather
        than keeping it: confirmed empirically that QDockWidget's native
        floating already produces exactly Qt.WindowType.Tool, parented to
        MainWindow, with no WindowStaysOnTopHint -- the same end state #39
        built by hand, now free). _on_dock_top_level_changed (connected to
        every dock's `topLevelChanged` signal at construction, see
        _make_dock) is what actually updates `_floating_panels`/the Float
        menu checkbox and triggers `_rebuild_gl_content_for_panel` --
        keeping that in ONE place means it stays correct whether the
        float was triggered here, by the shortcut, or by a user drag."""
        dock = getattr(self, frame_attr)
        if dock.isFloating():
            return
        self._reparent_gl_safely(dock, lambda: dock.setFloating(True))

    def _dock_panel(self, frame_attr, rebuild_gl_content=True):
        """Inverse of _float_panel -- QDockWidget.setFloating(False).

        rebuild_gl_content=False suppresses the rebuild
        _on_dock_top_level_changed would otherwise trigger -- used by
        closeEvent's shutdown-time _dock_all_floating_panels call (see
        that method), where the app is about to quit and no one will ever
        see the rebuilt widget: creating a brand new QOpenGLWidget with
        zero benefit, at the single riskiest moment for GL work
        (mid-teardown), is pure downside. Every other caller keeps the
        default (rebuild normally -- the app keeps running afterward, so
        the render fix still matters there). Implemented via the
        `_suppress_gl_rebuild_on_dock_change` instance flag rather than a
        parameter threaded through the signal handler, since the signal
        itself carries no way to pass extra arguments through to
        `_on_dock_top_level_changed`.

        Called three ways: the Float menu action being unchecked,
        _PanelDockWidget's own closeEvent override (a floating panel's
        native titlebar close button -- see that class's docstring), and
        _dock_all_floating_panels (session load safety AND app-quit
        safety, see that method)."""
        dock = getattr(self, frame_attr)
        if not dock.isFloating():
            return
        # Same fix as _PanelDockWidget.closeEvent, same reason (RUNNING.md
        # #58) -- a panel floated without Shift held sits at
        # allowedAreas=NoDockWidgetArea (see #51/#55); redocking it via
        # setFloating(False) while that's still the case starves Qt of any
        # valid target area, so it comes back as a stray, badly-placed
        # widget instead of resuming its real layout slot. Reset to
        # AllDockWidgetAreas immediately before, not after -- the
        # topLevelChanged-driven reset in _on_dock_top_level_changed fires
        # too late to help THIS call, only the next one.
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        if not rebuild_gl_content:
            self._suppress_gl_rebuild_on_dock_change = True
        try:
            # RUNNING.md #59/#60 -- see _PanelDockWidget.closeEvent's
            # docstring for the full reasoning (same fix, same bug, this
            # is just the other of the two places that ever redock a
            # panel -- the Workspace menu's Float-uncheck path, and
            # _dock_all_floating_panels' session-load/app-quit/reset-to-
            # default safety net, all funnel through here). When this
            # panel is the ONLY one currently floating, restoring
            # _last_all_docked_state reproduces its exact pre-float
            # nested position (confirmed directly -- see closeEvent's
            # docstring for why the coarse per-area approach #59 tried
            # first couldn't). With more than one panel floating at once
            # (e.g. _dock_all_floating_panels looping through several),
            # restoring that blob could also pull back in whichever
            # panel(s) besides this one are still floating -- falls back
            # to plain setFloating(False) (#58's behavior) in that case
            # rather than risk that; _dock_all_floating_panels doesn't
            # need placement precision anyway (quit/reset-to-default both
            # discard or immediately overwrite the result).
            def _redock():
                only_floater = self._floating_panels <= {frame_attr}
                if only_floater and self._last_all_docked_state is not None:
                    self.restoreState(QByteArray(self._last_all_docked_state))
                else:
                    dock.setFloating(False)
            self._reparent_gl_safely(dock, _redock)
        finally:
            self._suppress_gl_rebuild_on_dock_change = False

    def _dock_all_floating_panels(self, rebuild_gl_content=True):
        """Force every currently-floating panel back into the main window
        -- called before applying a NEW workspace state (session Open/New,
        see load_workspace_state/_new_session) so the dock layout starts
        from a fully-docked baseline before saved state is applied on top.
        Floating state itself was never part of a saved SESSION in the
        first place (see get_workspace_state/session_io.py) -- this is
        purely about not leaving stray floating windows open from
        whatever was going on in the PREVIOUS session once a new one
        loads.

        rebuild_gl_content is passed straight through to each _dock_panel
        call -- closeEvent passes False here (see that method) since the
        app is quitting right after and rebuilding GL content nobody will
        ever see is pure risk. Every other caller keeps the default."""
        for frame_attr in list(self._floating_panels):
            self._dock_panel(frame_attr, rebuild_gl_content=rebuild_gl_content)

    def _on_dock_top_level_changed(self, frame_attr, floating):
        """Single source of truth for a panel's float/dock transition,
        connected to every dock's own `topLevelChanged` signal at
        construction (see _make_dock) -- fires for EVERY transition
        regardless of cause (the Workspace menu, the Cmd+Shift+N
        shortcut, or the user dragging the panel out/back in), which is
        exactly why this replaced the old eventFilter-based bookkeeping
        that only covered the paths this file triggered directly. Keeps
        `_floating_panels` correct, triggers
        `_rebuild_gl_content_for_panel` (skipped during app-quit teardown
        -- see `_suppress_gl_rebuild_on_dock_change`, set by _dock_panel),
        and syncs the Float menu checkbox so a drag-triggered float still
        shows up checked there, not just floats "invisibly" to the menu."""
        if floating:
            self._floating_panels.add(frame_attr)
        else:
            self._floating_panels.discard(frame_attr)
            # RUNNING.md #60 -- keep _last_all_docked_state current
            # whenever the window returns to fully-docked (the LAST
            # floating panel just redocked), regardless of what triggered
            # it (menu, shortcut, a user drag-to-redock, or the
            # restoreState()/setFloating(False) call _dock_panel/
            # closeEvent just made for their own redock -- harmless to
            # snapshot again here, it'll just re-read whatever state that
            # call actually settled into). Only when _floating_panels is
            # now EMPTY -- see _last_all_docked_state's docstring
            # (__init__) for why a mid-multi-float snapshot would be the
            # wrong thing to save (it would describe an arrangement with
            # some OTHER panel already yanked back in early).
            if not self._floating_panels:
                self._snapshot_all_docked_state()

        if not self._suppress_gl_rebuild_on_dock_change:
            self._rebuild_gl_content_for_panel(frame_attr)

        float_attr = self._panel_float_action_attr.get(frame_attr)
        if float_attr:
            float_action = getattr(self, float_attr)
            float_action.blockSignals(True)
            float_action.setChecked(floating)
            float_action.blockSignals(False)

        # Shift-to-dock override (see __init__'s installEventFilter call
        # and eventFilter below) -- a panel that JUST started floating
        # gets Qt's dock-preview/snap suppressed immediately (unless Shift
        # happens to already be held at that exact instant, e.g. Tom
        # started the drag with it down), rather than waiting for the
        # next keyPress/Release to establish the right starting state.
        # A panel that just got docked is reset back to Qt's own default
        # (AllDockWidgetAreas) -- once it's docked, allowedAreas only
        # matters again the NEXT time it's dragged out, and resetting here
        # (rather than leaving whatever Shift last left it at) keeps that
        # next float-out starting from a known, deliberate state instead
        # of inheriting a stale one from this transition.
        if floating:
            shift_held = bool(
                QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
            getattr(self, frame_attr).setAllowedAreas(
                Qt.DockWidgetArea.AllDockWidgetAreas if shift_held
                else Qt.DockWidgetArea.NoDockWidgetArea)
        else:
            getattr(self, frame_attr).setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
