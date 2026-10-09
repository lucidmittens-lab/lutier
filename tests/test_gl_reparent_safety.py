"""Tests for the GL-widget-reparent crash guard AND the float/dock render
fix (lut_viewer_3d.py's _SafeGLViewWidget + LutViewer3D.rebuild_gl_view,
main_window.py's _reparent_gl_safely + _rebuild_gl_content_for_panel) --
run directly: python3 -m tests.test_gl_reparent_safety

Requires a real (if offscreen) QApplication -- same headless-Qt setup as
every other GUI-touching verification in this project (QT_QPA_PLATFORM=
offscreen + the .eglstub/ libEGL stub, see RUNNING.md). Under the offscreen
platform plugin there's no real GPU, so gl_view.isValid() is always False
here -- which actually makes this the right environment to exercise the
guard's early-return branch specifically (the branch that matters: no real
GL call is ever reached), even though it can't reproduce the ORIGINAL
segfault itself (that needs a real GPU/display -- see lut_viewer_3d.py's
own docstring on why this fix is still flagged unverified-on-real-hardware).
Full float/dock/close-button-redock cycles against a real MainWindow are
exercised too, confirming the reparent sequence itself doesn't raise --
this is the closest thing to Tom's actual crash repro available in this
sandbox.

Also covers the QMainWindow/QDockWidget docking migration (per Tom,
"Yeah. we want free form... do the migration to new system") -- see the
"docking migration" section near the bottom: every panel is a real,
placed QDockWidget with Movable/Floatable features; _float_panel/
_dock_panel/_PanelDockWidget's redock-on-native-close all still hold
under the new mechanism; Reset to Default Workspace and an old-format
(pre-migration) saved-workspace dict are both handled correctly. What
this CANNOT verify (same class of limitation as everything else in this
file): actually dragging a panel by its title bar, or the visual result
of a drag-driven float, since this sandbox has no real window manager --
see RUNNING.md's docking-migration entry for the specific real-hardware
tests this still needs.
"""
import inspect
import sys
import tempfile
from pathlib import Path

import numpy as np
from PySide6.QtWidgets import QApplication

import lut_viewer_3d
import main_window as main_window_module
import workspace_io
from app_logic import create_identity_lut
from main_window import MainWindow

# Every test in this file constructs at least one real MainWindow(), and
# MainWindow.closeEvent unconditionally writes the CURRENT workspace state
# (including whatever's currently floating) to workspace_io's fixed,
# not-test-aware DEFAULT_WORKSPACE_PATH on every win.close() -- and
# MainWindow.__init__ reads that SAME file back via workspace_io.
# load_remembered_workspace(). Left un-redirected, this file's own tests
# were bleeding state into EACH OTHER (a test that floats the Graph panel
# then closes writes "Graph floating" to that real file; the NEXT test's
# MainWindow(), fully unrelated, reads it right back and floats Graph on
# its own next processEvents() call) -- caught via
# test_load_workspace_state_tolerates_pre_migration_format unexpectedly
# failing with Graph floating despite never asking for that. Same
# redirect-for-the-duration-of-tests discipline test_workspace_io.py
# already uses for its own remembered-workspace tests, applied here once
# for the whole module (every test in this file constructs a MainWindow,
# so there's no test that WANTS the real path) rather than per-test.
_workspace_tmp_dir = tempfile.TemporaryDirectory()
workspace_io.DEFAULT_WORKSPACE_DIR = Path(_workspace_tmp_dir.name) / "LUTier"
workspace_io.DEFAULT_WORKSPACE_PATH = workspace_io.DEFAULT_WORKSPACE_DIR / "workspace.json"


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _settle_events(condition, max_ticks=20):
    """Pump the event loop until `condition()` is true (returns on the
    first tick it holds) or `max_ticks` is reached, whichever comes
    first. Real hardware (a real window manager confirming a resize/
    restoreState()) can take a variable, hardware-dependent number of
    event-loop ticks to finish applying a layout change -- a fixed
    tick count tuned against this sandbox's offscreen platform (no real
    window manager, so geometry applies synchronously) isn't reliable
    there. Always calls processEvents() at least once."""
    for _ in range(max_ticks):
        QApplication.processEvents()
        if condition():
            return


# ---- _SafeGLViewWidget.paintGL guard ---------------------------------------

def test_safe_gl_view_widget_paintgl_skips_when_invalid():
    _app()
    view = lut_viewer_3d._SafeGLViewWidget()
    calls = []
    # Patch the PARENT class's paintGL (what super().paintGL() resolves to)
    # so we can tell whether the real, GL-calling implementation was ever
    # reached -- that's the thing the original crash happened inside.
    orig = lut_viewer_3d.gl.GLViewWidget.paintGL
    lut_viewer_3d.gl.GLViewWidget.paintGL = lambda self: calls.append(True)
    try:
        assert view.isValid() is False, (
            "expected no real GPU context under the offscreen QPA platform"
        )
        view.paintGL()
        assert calls == [], (
            "must not reach the real GL-calling paintGL when isValid() is False"
        )
    finally:
        lut_viewer_3d.gl.GLViewWidget.paintGL = orig
    print("_SafeGLViewWidget.paintGL: skips the real GL call when "
          "isValid() is False -- OK")


def test_safe_gl_view_widget_paintgl_calls_through_when_valid():
    _app()
    view = lut_viewer_3d._SafeGLViewWidget()
    calls = []
    orig = lut_viewer_3d.gl.GLViewWidget.paintGL
    lut_viewer_3d.gl.GLViewWidget.paintGL = lambda self: calls.append(True)
    try:
        view.isValid = lambda: True  # simulate a real, valid context
        view.paintGL()
        assert calls == [True], (
            "must call through to the real paintGL when isValid() is True"
        )
    finally:
        lut_viewer_3d.gl.GLViewWidget.paintGL = orig
    print("_SafeGLViewWidget.paintGL: still calls through to the real "
          "paintGL when isValid() is True -- OK (guard isn't just a "
          "no-op that broke normal rendering)")


# ---- LutViewer3D camera centering -------------------------------------------

def test_set_lut_centers_camera_on_data_not_origin():
    """The actual bug Tom asked about: pyqtgraph's default camera center
    is the coordinate ORIGIN, which is a corner (not the middle) of a
    typical [0,1]^3 LUT cube. An identity LUT's plotted positions are
    exactly the identity grid -- domain [0,1] in each channel -- so the
    real bounding-box center is exactly (0.5, 0.5, 0.5), not (0, 0, 0)."""
    _app()
    viewer = lut_viewer_3d.LutViewer3D()
    viewer.show()  # set_lut() now skips its rebuild while invisible -- see lut_viewer_3d.py
    lut = create_identity_lut(size=5)
    viewer.set_lut(lut)

    center = viewer.gl_view.opts["center"]
    assert abs(center.x() - 0.5) < 1e-9
    assert abs(center.y() - 0.5) < 1e-9
    assert abs(center.z() - 0.5) < 1e-9
    print("LutViewer3D.set_lut: camera centers on (0.5, 0.5, 0.5) for an "
          "identity LUT's [0,1]^3 data, not the coordinate origin -- OK")


def test_set_lut_sets_distance_from_actual_data_extent():
    _app()
    viewer = lut_viewer_3d.LutViewer3D()
    viewer.show()  # set_lut() now skips its rebuild while invisible -- see lut_viewer_3d.py
    lut = create_identity_lut(size=5)
    viewer.set_lut(lut)

    # Identity LUT's positions span exactly [0, 1] per axis -> extent 1.0
    # -> distance = max(1.0 * 2.5, 0.5) = 2.5, per _frame_camera_on.
    assert abs(viewer.gl_view.opts["distance"] - 2.5) < 1e-9
    print("LutViewer3D.set_lut: camera distance is derived from the "
          "actual data extent (2.5x), not a fixed guess -- OK")


def test_set_lut_skips_rebuild_while_hidden_then_catches_up_on_show():
    """The actual perf fix: main_window._on_stack_changed calls set_lut()
    on EVERY stack edit, including every tick of a live slider drag,
    whether or not the 3D Viewer panel is currently visible (hidden via
    the Workspace menu, or tabbed behind another panel) -- paying for a
    real GL scatter/cage rebuild plus a curve recompute for a view nobody
    can see. Confirms the skip-while-hidden/catch-up-on-show contract
    directly, not just indirectly via other tests now needing .show()."""
    _app()
    viewer = lut_viewer_3d.LutViewer3D()  # deliberately NOT shown yet
    assert not viewer.isVisible()

    lut = create_identity_lut(size=5)
    viewer.set_lut(lut)

    assert viewer._stale_lut_pending is True
    assert viewer._scatter_item is None, (
        "set_lut must not do the real rebuild while the panel is hidden"
    )
    assert viewer._current_lut is lut, (
        "the LUT itself must still be recorded, so showEvent has something to apply"
    )

    viewer.show()

    assert viewer._stale_lut_pending is False
    assert viewer._scatter_item is not None, (
        "showEvent must run the deferred rebuild now that the panel is visible"
    )
    print("LutViewer3D.set_lut/showEvent: skips the real rebuild while "
          "hidden, catches up automatically once shown -- OK")


def test_set_lut_while_visible_still_rebuilds_immediately():
    """The non-regression half of the same guard: a visible panel must
    keep behaving exactly as before -- no deferred/stale update, no
    dependence on a later showEvent that may never come again."""
    _app()
    viewer = lut_viewer_3d.LutViewer3D()
    viewer.show()

    viewer.set_lut(create_identity_lut(size=5))

    assert viewer._stale_lut_pending is False
    assert viewer._scatter_item is not None
    print("LutViewer3D.set_lut: still rebuilds immediately while visible "
          "-- OK")


def test_frame_camera_on_empty_positions_does_not_raise():
    _app()
    viewer = lut_viewer_3d.LutViewer3D()
    viewer.show()  # set_lut() now skips its rebuild while invisible -- see lut_viewer_3d.py
    viewer._frame_camera_on(np.empty((0, 3)))
    print("LutViewer3D._frame_camera_on: empty positions array is a "
          "safe no-op -- OK")


def test_rebuild_gl_view_reframes_camera_on_the_new_widget():
    """The whole point of rebuild_gl_view is a brand new GL widget --
    confirm its camera also gets correctly centered on the re-applied
    LUT, not left at the fresh widget's own (0,0,0) default."""
    _app()
    viewer = lut_viewer_3d.LutViewer3D()
    viewer.show()  # set_lut() now skips its rebuild while invisible -- see lut_viewer_3d.py
    lut = create_identity_lut(size=5)
    viewer.set_lut(lut)

    viewer.rebuild_gl_view()

    center = viewer.gl_view.opts["center"]
    assert abs(center.x() - 0.5) < 1e-9
    assert abs(center.y() - 0.5) < 1e-9
    assert abs(center.z() - 0.5) < 1e-9
    print("LutViewer3D.rebuild_gl_view: new widget's camera is correctly "
          "re-centered on the re-applied LUT, not left at the default "
          "origin -- OK")


# ---- LutViewer3D cage mesh (per Tom's correction -- "I want more of a cage
# mesh that draws lines between the points", replacing the earlier flat
# GLGridItem reference plane) -------------------------------------------

def test_cage_opacity_slider_default_matches_pyqtgraph_grid_default():
    """30% (0.3) is inherited from the original grid feature's default
    (pyqtgraph's own GLGridItem default alpha, 76.5/255) -- kept as the
    cage's starting point too, not re-tuned."""
    _app()
    viewer = lut_viewer_3d.LutViewer3D()
    viewer.show()  # set_lut() now skips its rebuild while invisible -- see lut_viewer_3d.py
    assert viewer.cage_opacity_slider.value() == 30
    print("LutViewer3D: cage opacity slider defaults to 30% -- OK")


def test_set_lut_builds_a_cage_connecting_the_scatter_points():
    """The actual point of the cage mesh -- confirm it's really built
    from app_logic.extract_3d_cage_lines_for_visualization against the
    SAME lut/density as the scatter, not left over from some other
    source."""
    _app()
    viewer = lut_viewer_3d.LutViewer3D()
    viewer.show()  # set_lut() now skips its rebuild while invisible -- see lut_viewer_3d.py
    lut = create_identity_lut(size=6)
    viewer.set_lut(lut)

    assert viewer._cage_item is not None
    from app_logic import extract_3d_cage_lines_for_visualization
    expected = extract_3d_cage_lines_for_visualization(
        lut, subsample_size=viewer.density_slider.value())
    assert viewer._cage_item.pos.shape == expected.shape
    assert np.allclose(viewer._cage_item.pos, expected)
    print("LutViewer3D.set_lut: cage mesh matches "
          "extract_3d_cage_lines_for_visualization's own output exactly -- OK")


def test_cage_opacity_slider_updates_cage_item_color():
    _app()
    viewer = lut_viewer_3d.LutViewer3D()
    viewer.show()  # set_lut() now skips its rebuild while invisible -- see lut_viewer_3d.py
    viewer.set_lut(create_identity_lut(size=6))
    viewer.cage_opacity_slider.setValue(80)
    assert viewer._cage_opacity == 0.8
    # GLLinePlotItem's color is a plain 0.0-1.0 float RGBA tuple (NOT the
    # 0-255 range the old GLGridItem.setColor used) -- confirmed against
    # the real pyqtgraph install before writing _apply_cage_opacity this
    # way, see that method's docstring.
    assert abs(viewer._cage_item.color[3] - 0.8) < 1e-9
    print("LutViewer3D: moving the cage opacity slider updates the "
          "GLLinePlotItem's actual alpha (0.0-1.0 float) -- OK")


def test_cage_opacity_survives_density_change_and_rebuild():
    """The actual point of storing _cage_opacity on the widget (not just
    reading it off the old cage item) -- both a density change and
    rebuild_gl_view discard the old cage and build a fresh one, which
    starts back at pyqtgraph's hardcoded default alpha unless something
    re-applies the slider's value. Confirms it does, across both paths."""
    _app()
    viewer = lut_viewer_3d.LutViewer3D()
    viewer.show()  # set_lut() now skips its rebuild while invisible -- see lut_viewer_3d.py
    viewer.set_lut(create_identity_lut(size=6))
    viewer.cage_opacity_slider.setValue(65)
    old_cage_item = viewer._cage_item

    viewer.density_slider.setValue(10)  # triggers _update_3d_view via _on_density_changed
    assert viewer._cage_item is not old_cage_item
    assert abs(viewer._cage_item.color[3] - 0.65) < 1e-9

    viewer.rebuild_gl_view()
    assert abs(viewer._cage_item.color[3] - 0.65) < 1e-9
    assert viewer.cage_opacity_slider.value() == 65, (
        "the slider itself shouldn't reset just because the cage/GL "
        "widget was rebuilt"
    )
    print("LutViewer3D: cage opacity survives both a density change and "
          "rebuild_gl_view, not reset to pyqtgraph's default -- OK")


def test_no_lut_loaded_means_no_cage_item():
    _app()
    viewer = lut_viewer_3d.LutViewer3D()
    viewer.show()  # set_lut() now skips its rebuild while invisible -- see lut_viewer_3d.py
    assert viewer._cage_item is None
    print("LutViewer3D: no cage item exists before any LUT is loaded -- OK")


# ---- LutViewer3D Graph view selection (per Tom's follow-up -- "the buttons
# for 1d curve is broken. should go in an ellipses menu, select 3d or 1d y
# 1d r 1d g 1d b" -- replacing an earlier checkable-button-pair mode switch
# with a "..." menu, same convention as scopes_panel.py's scope selector) --

def test_menu_button_exists_and_defaults_to_3d_checked():
    _app()
    viewer = lut_viewer_3d.LutViewer3D()
    viewer.show()  # set_lut() now skips its rebuild while invisible -- see lut_viewer_3d.py
    assert viewer.menu_button.menu() is not None
    all_actions = viewer.menu_button.menu().actions()
    # "3D Settings..." (behind a separator) is a real, deliberate 6th
    # entry now (see populate_menu), not a view choice -- checked
    # separately below rather than folded into the view-choice set.
    actions = {a.text(): a for a in all_actions if a.text() in lut_viewer_3d.VIEW_CHOICES}
    assert set(actions) == set(lut_viewer_3d.VIEW_CHOICES)
    assert actions[lut_viewer_3d.VIEW_3D].isChecked()
    for name in lut_viewer_3d.VIEW_CHOICES:
        if name != lut_viewer_3d.VIEW_3D:
            assert not actions[name].isChecked()
    assert any(a.text() == "3D Settings..." for a in all_actions)
    print("LutViewer3D: '...' menu has exactly the 5 expected view "
          "choices (3D checked by default) plus '3D Settings...' -- OK")


def test_3d_settings_action_opens_the_dialog_with_the_sliders():
    """Per Tom: Grid density/Cage opacity moved out of the panel's
    permanent, always-visible footprint and into a dialog reachable from
    the "..." menu -- confirms the dialog itself exists, holds the same
    slider widgets set_lut/_on_density_changed/_apply_cage_opacity
    already rely on by attribute name, and that the menu action actually
    shows it."""
    _app()
    viewer = lut_viewer_3d.LutViewer3D()
    viewer.show()  # set_lut() now skips its rebuild while invisible -- see lut_viewer_3d.py

    assert viewer.density_slider is not None
    assert viewer.cage_opacity_slider is not None
    assert not viewer._3d_settings_dialog.isVisible()

    viewer.show_3d_settings_dialog()

    assert viewer._3d_settings_dialog.isVisible()
    print("LutViewer3D: '3D Settings...' opens a dialog holding the "
          "Grid density/Cage opacity sliders -- OK")


def test_default_view_shows_gl_panel():
    _app()
    viewer = lut_viewer_3d.LutViewer3D()
    viewer.show()  # set_lut() now skips its rebuild while invisible -- see lut_viewer_3d.py
    assert viewer.stack.currentWidget() is viewer._gl_panel
    print("LutViewer3D: defaults to showing the GL panel (3D view) -- OK")


def test_selecting_1d_view_shows_curve_plot_with_exactly_one_curve_visible():
    _app()
    viewer = lut_viewer_3d.LutViewer3D()
    viewer.show()  # set_lut() now skips its rebuild while invisible -- see lut_viewer_3d.py
    viewer.set_lut(create_identity_lut(size=6))

    for view_name, channel in lut_viewer_3d._VIEW_TO_CURVE_CHANNEL.items():
        viewer._on_view_changed(view_name)
        assert viewer.stack.currentWidget() is viewer.curve_plot
        for item_channel, item in viewer._curve_items.items():
            assert item.isVisible() == (item_channel == channel), (
                f"selecting {view_name!r} should show ONLY the {channel!r} "
                f"curve, per Tom's explicit per-channel choices"
            )
    print("LutViewer3D: each '1D X' choice shows the curve plot with "
          "exactly that one channel's curve visible -- OK")


def test_selecting_3d_after_1d_restores_gl_panel():
    _app()
    viewer = lut_viewer_3d.LutViewer3D()
    viewer.show()  # set_lut() now skips its rebuild while invisible -- see lut_viewer_3d.py
    viewer._on_view_changed(lut_viewer_3d.VIEW_1D_R)
    viewer._on_view_changed(lut_viewer_3d.VIEW_3D)
    assert viewer.stack.currentWidget() is viewer._gl_panel
    print("LutViewer3D: selecting 3D after a 1D view restores the GL "
          "panel -- OK")


def test_view_switch_keeps_stack_minimum_size_hint_constant():
    """The actual bug Tom hit -- 'The panel snaps to a new size when
    switching from 3d to curve and fucks up the UI'. A QStackedWidget's
    minimumSizeHint is the max across ALL pages regardless of which is
    current (confirmed empirically against the real installed PySide6,
    not assumed) -- so switching views must never change it, which is
    what actually prevents the panel from resizing on every switch."""
    _app()
    viewer = lut_viewer_3d.LutViewer3D()
    viewer.show()  # set_lut() now skips its rebuild while invisible -- see lut_viewer_3d.py
    min_at_3d = viewer.stack.minimumSizeHint().height()
    for view_name in lut_viewer_3d.VIEW_CHOICES:
        viewer._on_view_changed(view_name)
        assert viewer.stack.minimumSizeHint().height() == min_at_3d, (
            f"switching to {view_name!r} changed the stack's minimum "
            f"size hint -- this is exactly the resize-snap bug Tom hit"
        )
    print("LutViewer3D: the outer QStackedWidget's minimum size hint "
          "never changes across any view switch -- OK (fixes the "
          "panel-snapping bug)")


def test_mode_switch_never_destroys_or_rebuilds_gl_view():
    """The whole point of a QStackedWidget page switch instead of
    destroying/rebuilding either widget on every view change -- see this
    module's docstring on rebuild_gl_view being a real, historically
    crash-prone operation that a view switch shouldn't trigger needlessly
    (DELETELATER RACE et al)."""
    _app()
    viewer = lut_viewer_3d.LutViewer3D()
    viewer.show()  # set_lut() now skips its rebuild while invisible -- see lut_viewer_3d.py
    gl_view_before = viewer.gl_view
    for _ in range(5):
        for view_name in lut_viewer_3d.VIEW_CHOICES:
            viewer._on_view_changed(view_name)
    assert viewer.gl_view is gl_view_before, (
        "gl_view must be the same instance across view switches -- "
        "switching views is not a float/dock event"
    )
    print("LutViewer3D: repeated view switching never replaces "
          "gl_view -- OK")


def test_corner_menu_and_menu_bar_menu_stay_in_sync():
    """Graph now has TWO menu instances (the '...' corner menu and
    MainWindow's top-level 'Graph' menu-bar menu, see main_window.py) --
    same dual-menu-sync requirement scopes_panel.py's _on_scope_changed
    already covers for its own scope selector. Confirms LutViewer3D
    follows the same pattern: picking a view from one menu checks the
    matching action in EVERY OTHER menu instance built via populate_menu."""
    _app()
    from PySide6.QtWidgets import QMenu
    viewer = lut_viewer_3d.LutViewer3D()
    viewer.show()  # set_lut() now skips its rebuild while invisible -- see lut_viewer_3d.py
    second_menu = QMenu()
    second_actions = viewer.populate_menu(second_menu)
    assert len(viewer._view_action_sets) == 2

    viewer._on_view_changed(lut_viewer_3d.VIEW_1D_G)

    first_actions = {a.text(): a for a in viewer.menu_button.menu().actions()}
    assert first_actions[lut_viewer_3d.VIEW_1D_G].isChecked()
    assert second_actions[lut_viewer_3d.VIEW_1D_G].isChecked()
    for name in lut_viewer_3d.VIEW_CHOICES:
        if name != lut_viewer_3d.VIEW_1D_G:
            assert not first_actions[name].isChecked()
            assert not second_actions[name].isChecked()
    print("LutViewer3D: picking a view from one menu checks the matching "
          "action in every other populate_menu() instance too -- OK")


def test_set_lut_populates_curve_plot_with_domain_aware_data():
    _app()
    viewer = lut_viewer_3d.LutViewer3D()
    viewer.show()  # set_lut() now skips its rebuild while invisible -- see lut_viewer_3d.py
    lut = create_identity_lut(size=9)
    viewer.set_lut(lut)

    for channel in ("y", "r", "g", "b"):
        x_data = viewer._curve_items[channel].xData
        y_data = viewer._curve_items[channel].yData
        assert x_data is not None and len(x_data) == viewer._curve_num_samples
        assert y_data is not None and len(y_data) == viewer._curve_num_samples
    # Identity LUT, default [0,1] domain -- R/G/B curves should be a
    # straight input==output line (see test_yrgb_curve.py for the
    # exhaustive version of this check against app_logic directly).
    assert np.allclose(viewer._curve_items["r"].xData, viewer._curve_items["r"].yData)
    print("LutViewer3D.set_lut: populates all four curve items with "
          "correctly-sized, correct data -- OK")


def test_curve_plot_updates_even_while_3d_view_is_current():
    """set_lut must update the curve data regardless of which view is
    currently selected, so switching to a 1D view always reflects the
    CURRENT LUT, not whatever was loaded the last time it was visible."""
    _app()
    viewer = lut_viewer_3d.LutViewer3D()
    viewer.show()  # set_lut() now skips its rebuild while invisible -- see lut_viewer_3d.py
    assert viewer.stack.currentWidget() is viewer._gl_panel  # still 3D
    viewer.set_lut(create_identity_lut(size=9))
    assert len(viewer._curve_items["r"].xData) == viewer._curve_num_samples
    print("LutViewer3D.set_lut: updates the curve plot even while the 3D "
          "view is the one currently selected -- OK")


def test_clear_resets_curve_plot_data_too():
    _app()
    viewer = lut_viewer_3d.LutViewer3D()
    viewer.show()  # set_lut() now skips its rebuild while invisible -- see lut_viewer_3d.py
    viewer.set_lut(create_identity_lut(size=9))
    viewer.clear()
    for channel in ("y", "r", "g", "b"):
        assert len(viewer._curve_items[channel].xData or []) == 0
    print("LutViewer3D.clear: also resets the curve plot's data, not "
          "just the 3D scatter/cage items -- OK")


def test_clear_also_removes_cage_item():
    _app()
    viewer = lut_viewer_3d.LutViewer3D()
    viewer.show()  # set_lut() now skips its rebuild while invisible -- see lut_viewer_3d.py
    viewer.set_lut(create_identity_lut(size=6))
    assert viewer._cage_item is not None
    viewer.clear()
    assert viewer._cage_item is None
    print("LutViewer3D.clear: also removes the cage item, not just the "
          "scatter -- OK")


# ---- LutViewer3D.rebuild_gl_view -------------------------------------------

def test_rebuild_gl_view_replaces_the_widget_but_keeps_the_lut():
    _app()
    viewer = lut_viewer_3d.LutViewer3D()
    viewer.show()  # set_lut() now skips its rebuild while invisible -- see lut_viewer_3d.py
    lut = create_identity_lut(size=5)
    viewer.set_lut(lut)

    old_gl_view = viewer.gl_view
    old_scatter_item = viewer._scatter_item
    assert old_scatter_item is not None

    viewer.rebuild_gl_view()

    assert viewer.gl_view is not old_gl_view, (
        "rebuild_gl_view must actually replace the widget, not just repaint it"
    )
    assert isinstance(viewer.gl_view, lut_viewer_3d._SafeGLViewWidget)
    assert viewer._current_lut is lut, "the loaded LUT must survive a rebuild"
    assert viewer._scatter_item is not None
    assert viewer._scatter_item is not old_scatter_item, (
        "the scatter item must be re-created against the NEW gl_view, not "
        "left pointing at the discarded one"
    )
    assert viewer._cage_item is not None, (
        "the cage mesh must also be re-created against the NEW gl_view"
    )
    print("LutViewer3D.rebuild_gl_view: swaps in a fresh _SafeGLViewWidget "
          "and re-applies the current LUT (scatter AND cage) -- OK")


def test_rebuild_gl_view_with_no_lut_loaded_does_not_raise():
    """Rebuild can happen (float/dock) before any LUT has ever been
    loaded -- must not assume _current_lut/_scatter_item/_cage_item are
    set."""
    _app()
    viewer = lut_viewer_3d.LutViewer3D()
    viewer.show()  # set_lut() now skips its rebuild while invisible -- see lut_viewer_3d.py
    assert viewer._current_lut is None
    viewer.rebuild_gl_view()
    assert viewer._current_lut is None
    assert viewer._scatter_item is None
    assert viewer._cage_item is None
    print("LutViewer3D.rebuild_gl_view: safe to call with no LUT loaded -- OK")


def test_rebuild_gl_view_stays_in_the_same_layout_position():
    """The GL view must land back above the density-slider row, not get
    appended after it -- rebuild_gl_view uses indexOf(old_view) on its
    own container's layout specifically so this can't drift even if that
    layout grows more rows later.

    Checked against `viewer._gl_panel.layout()`, not `viewer._layout` --
    gl_view lives one level deeper, inside its own `_gl_panel` container
    (alongside its density/cage-opacity controls), which is itself just
    one page of `viewer.stack` (see lut_viewer_3d.py's docstring, GRAPH
    PANEL) -- `viewer._layout` (the panel's top-level layout) now holds
    only the QStackedWidget, not gl_view directly."""
    _app()
    viewer = lut_viewer_3d.LutViewer3D()
    viewer.show()  # set_lut() now skips its rebuild while invisible -- see lut_viewer_3d.py
    gl_panel_layout = viewer._gl_panel.layout()
    gl_view_index_before = gl_panel_layout.indexOf(viewer.gl_view)
    viewer.rebuild_gl_view()
    gl_view_index_after = gl_panel_layout.indexOf(viewer.gl_view)
    assert gl_view_index_after == gl_view_index_before == 0
    print("LutViewer3D.rebuild_gl_view: new gl_view keeps its original "
          "position within _gl_panel's layout -- OK")


# ---- main_window.py float/dock render-fix wiring ---------------------------

def test_float_panel_rebuilds_3d_viewer_gl_view():
    _app()
    win = MainWindow()
    try:
        old_gl_view = win.viewer_3d.gl_view
        win._float_panel("viewer_3d_frame")
        assert win.viewer_3d.gl_view is not old_gl_view, (
            "floating the 3D Viewer panel must rebuild its GL view "
            "(_rebuild_gl_content_for_panel), per Tom's blank-render report"
        )
    finally:
        win.close()
    print("MainWindow._float_panel: rebuilds the 3D Viewer's GL view after "
          "floating -- OK")


def test_dock_panel_rebuilds_3d_viewer_gl_view():
    _app()
    win = MainWindow()
    try:
        win._float_panel("viewer_3d_frame")
        floated_gl_view = win.viewer_3d.gl_view
        win._dock_panel("viewer_3d_frame")
        assert win.viewer_3d.gl_view is not floated_gl_view, (
            "docking the 3D Viewer panel back must also rebuild its GL "
            "view, not just leave whatever it had while floating"
        )
    finally:
        win.close()
    print("MainWindow._dock_panel: rebuilds the 3D Viewer's GL view after "
          "docking back -- OK")


def test_float_dock_rebuild_does_not_affect_other_panels():
    """_rebuild_gl_content_for_panel is a per-panel hook -- confirm it
    doesn't do anything (or raise) for a panel with no GL content."""
    _app()
    win = MainWindow()
    try:
        win._float_panel("scopes_frame")
        win._dock_panel("scopes_frame")
    finally:
        win.close()
    print("MainWindow: float/dock on a non-GL panel (Scopes) is unaffected "
          "by the render-fix hook -- OK")


# ---- deleteLater/GC race (Tom's second crash report) -----------------------

def test_rebuild_gl_view_keeps_discarded_widget_referenced():
    """The actual bug: the old gl_view must not be allowed to become
    unreferenced (and therefore Python-GC-able) between setParent/hide
    and Qt's own deferred deleteLater() actually running -- see
    rebuild_gl_view's docstring (DELETELATER RACE). Confirms the
    permanent-reference list is really what's keeping it alive."""
    _app()
    viewer = lut_viewer_3d.LutViewer3D()
    viewer.show()  # set_lut() now skips its rebuild while invisible -- see lut_viewer_3d.py
    old_view = viewer.gl_view
    assert old_view not in viewer._pending_gl_view_deletions

    viewer.rebuild_gl_view()

    assert old_view in viewer._pending_gl_view_deletions, (
        "the discarded gl_view must be kept referenced until Qt's "
        "deferred delete actually runs"
    )
    print("LutViewer3D.rebuild_gl_view: discarded gl_view is kept "
          "referenced (not left to CPython's GC) until deleteLater() "
          "actually runs -- OK")


def test_close_floated_3d_viewer_with_no_lut_loaded_does_not_raise():
    """Tom's exact second repro: float the 3D Viewer WITHOUT ever
    importing a LUT, then close it via the native titlebar close button.
    (MainWindow always has SOME bake in viewer_3d from its own empty-Stack
    default -- see main_window.py's _on_stack_changed -- so this doesn't
    literally mean _current_lut is None; it means "no user action beyond
    just opening the app," Tom's actual repro. Explicitly clearing
    _current_lut below reproduces the never-loaded state exactly, which
    IS the fastest possible path back to rebuild_gl_view() returning with
    nothing else referencing the old gl_view -- the condition the
    deleteLater/GC race needed to actually trigger.)

    Since the docking migration, "the native titlebar close button" is
    QDockWidget.close() itself (intercepted by _PanelDockWidget.closeEvent
    while floating -- see that class's docstring), not a hand-rolled
    eventFilter -- frame.close() below is the real call path, not a
    simulation of it.

    RUNNING.md #61 -- closing a floating panel now HIDES it rather than
    redocking (see closeEvent's docstring), so it stays floating
    internally (still in _floating_panels) -- this test's own assertion
    updated accordingly (was "not in _floating_panels" back when close
    meant redock; now checks isVisible() instead, the thing #61 actually
    changed)."""
    _app()
    win = MainWindow()
    try:
        win.viewer_3d.clear()
        assert win.viewer_3d._current_lut is None

        win._float_panel("viewer_3d_frame")
        frame = win.viewer_3d_frame

        frame.close()

        assert "viewer_3d_frame" in win._floating_panels, (
            "still floating internally -- #61 only hides on close, "
            "doesn't redock"
        )
        assert not frame.isVisible()
    finally:
        win.close()
    print("MainWindow: floating the 3D Viewer with NO LUT loaded, then "
          "closing via the native close button, does not raise -- OK "
          "(Tom's exact second crash repro)")


def test_repeated_float_dock_with_no_lut_loaded_does_not_raise():
    """A few round trips with nothing loaded -- cheap extra coverage for
    the same no-LUT-loaded condition across repeated rebuilds."""
    _app()
    win = MainWindow()
    try:
        win.viewer_3d.clear()
        assert win.viewer_3d._current_lut is None
        for _ in range(3):
            win._float_panel("viewer_3d_frame")
            win._dock_panel("viewer_3d_frame")
        assert len(win.viewer_3d._pending_gl_view_deletions) == 6  # 2 rebuilds/cycle x 3
    finally:
        win.close()
    print("MainWindow: 3 repeated float/dock cycles with no LUT loaded "
          "complete without raising, and every discarded gl_view stayed "
          "referenced -- OK")


# ---- AA_ShareOpenGLContexts (the actual root cause of the blank render) ---

def test_main_sets_share_opengl_contexts_before_constructing_qapplication():
    """The real fix, per the traceback Tom pasted: glUseProgram was
    failing (GLError 1281/1282) because pyqtgraph's GLLinePlotItem/
    GLScatterPlotItem cache their compiled shader program at the CLASS
    level, valid only in whichever GL context compiled it first -- every
    OTHER top-level QOpenGLWidget (floating a panel, or rebuild_gl_view's
    fresh widget) has its own independent context by default, so that
    cached program is invalid everywhere else. Qt.AA_ShareOpenGLContexts
    fixes this by putting every QOpenGLWidget's context in one shared
    group -- but ONLY if set before QApplication is constructed, so this
    specifically checks ORDERING, not just presence.

    Can't actually construct a real QApplication with this attribute
    active and prove shader sharing works -- that needs a real GPU/
    display this sandbox doesn't have -- so this checks main()'s SOURCE
    instead: the attribute is referenced, and it appears textually before
    the QApplication(...) construction call within the function body."""
    source = inspect.getsource(main_window_module.main)
    assert "AA_ShareOpenGLContexts" in source
    attr_pos = source.index("AA_ShareOpenGLContexts")
    qapp_pos = source.index("QApplication(sys.argv)")
    assert attr_pos < qapp_pos, (
        "AA_ShareOpenGLContexts must be set BEFORE QApplication is "
        "constructed -- Qt only reads this attribute at that point"
    )
    print("main(): sets Qt.AA_ShareOpenGLContexts before constructing "
          "QApplication -- OK")


# ---- deferred-floating-restore (Tom's "is this the remembered workspace   --
# ---- feature?" question) ---------------------------------------------------

def test_load_workspace_state_defers_dock_state_restoration():
    """The actual fix for Tom's question: restoring a remembered dock
    layout (including a floating panel) must NOT happen synchronously
    inside load_workspace_state (and therefore not synchronously inside
    __init__, before the window has ever been shown or the event loop
    started) -- it must wait for the next event-loop tick. Since the
    docking migration the saved shape is an opaque QMainWindow.
    saveState() blob (see get_workspace_state), not a hand-constructable
    dict -- so this test builds a REAL one (float the Graph panel on a
    throwaway window, capture its state) rather than fabricating one by
    hand."""
    _app()
    source_win = MainWindow()
    try:
        source_win._float_panel("viewer_3d_frame")
        state = source_win.get_workspace_state()
    finally:
        source_win.close()

    win = MainWindow()
    try:
        assert "viewer_3d_frame" not in win._floating_panels

        win.load_workspace_state(state)
        assert "viewer_3d_frame" not in win._floating_panels, (
            "dock state restoration must NOT be applied synchronously -- "
            "see load_workspace_state's DEFERRED docstring"
        )

        QApplication.processEvents()
        assert "viewer_3d_frame" in win._floating_panels, (
            "restoration SHOULD be applied once the event loop actually runs"
        )
    finally:
        win.close()
    print("MainWindow.load_workspace_state: dock state restoration "
          "(including a remembered floating panel) is deferred to the "
          "next event-loop tick, not applied synchronously -- OK")


def test_mainwindow_construction_does_not_float_synchronously():
    """Reproduces the real startup path: __init__ -> load_workspace_state
    (workspace_io.load_remembered_workspace()) happens entirely BEFORE
    main() would ever call window.show() or app.exec(). Monkeypatches
    load_remembered_workspace to simulate "Graph was floating last
    session" -- a REAL saved state (see test_load_workspace_state_defers_
    dock_state_restoration for why this can't be hand-constructed anymore)
    -- and confirms MainWindow() itself returns without having floated
    anything yet."""
    _app()
    source_win = MainWindow()
    try:
        source_win._float_panel("viewer_3d_frame")
        remembered_state = source_win.get_workspace_state()
    finally:
        source_win.close()

    orig = main_window_module.workspace_io.load_remembered_workspace
    main_window_module.workspace_io.load_remembered_workspace = (
        lambda: remembered_state
    )
    try:
        win = MainWindow()
        try:
            assert "viewer_3d_frame" not in win._floating_panels, (
                "a remembered floating panel must not be restored during "
                "__init__ itself, before the window is ever shown"
            )
            QApplication.processEvents()
            assert "viewer_3d_frame" in win._floating_panels
        finally:
            win.close()
    finally:
        main_window_module.workspace_io.load_remembered_workspace = orig
    print("MainWindow.__init__: a remembered floating Graph panel is NOT "
          "restored synchronously during construction -- OK (this was "
          "plausibly the actual root cause behind #29-#32 never visibly "
          "helping, per Tom's own question -- the docking migration keeps "
          "the same discipline for restoreState() too)")


def test_close_event_docks_without_rebuilding_gl_content():
    """closeEvent's shutdown-time dock must NOT rebuild the 3D Viewer's
    GL widget -- see _dock_panel's rebuild_gl_content docstring. Building
    a brand new QOpenGLWidget nobody will ever see, right as the app is
    quitting, is pure risk with no benefit."""
    _app()
    win = MainWindow()
    win._float_panel("viewer_3d_frame")
    gl_view_while_floating = win.viewer_3d.gl_view

    win.close()  # triggers closeEvent synchronously

    assert win.viewer_3d.gl_view is gl_view_while_floating, (
        "closeEvent's dock-before-quit must not rebuild the GL widget"
    )
    print("MainWindow.closeEvent: docking a floating 3D Viewer before "
          "quitting does NOT rebuild its GL widget -- OK")


# ---- main_window.py float/dock reparent path -------------------------------

def test_float_then_dock_3d_viewer_does_not_raise():
    _app()
    win = MainWindow()
    try:
        assert "viewer_3d_frame" not in win._floating_panels
        win._float_panel("viewer_3d_frame")
        assert "viewer_3d_frame" in win._floating_panels
        assert win.viewer_3d_frame.isFloating()

        win._dock_panel("viewer_3d_frame")
        assert "viewer_3d_frame" not in win._floating_panels
        assert not win.viewer_3d_frame.isFloating(), (
            "Graph panel's dock should be docked, not floating, again"
        )
        assert not win.viewer_3d_frame.isWindow(), (
            "a docked panel must be a plain embedded widget again, not a "
            "top-level window -- see this test's pre-migration version, "
            "which checked splitter membership instead (no splitters "
            "exist anymore, see RUNNING.md's docking-migration entry)"
        )
    finally:
        win.close()
    print("MainWindow._float_panel/_dock_panel: full float+dock cycle on "
          "the Graph panel completes without raising -- OK")


def test_close_floated_3d_viewer_via_native_close_button_hides_not_redocks():
    """RUNNING.md #61 -- superseded expectation. Reproduces the exact path
    Tom's original crash report went through (the floated window's own OS
    titlebar close button, not the Workspace menu's Float toggle -- see
    _PanelDockWidget's docstring), but the correct behavior changed: #58
    through #60 all made this REDOCK the panel; #61, per Tom ("hitting X
    sends a panel back home. if home is lost it refuses to go anywhere...
    hitting X hides that panel"), makes it just HIDE instead, staying
    floating internally so there's no "home" placement decision left to
    get wrong. Since the docking migration this is QDockWidget.close()
    itself (intercepted by _PanelDockWidget.closeEvent while floating),
    not a hand-rolled eventFilter -- frame.close() below is the real call
    path."""
    _app()
    win = MainWindow()
    try:
        win._float_panel("viewer_3d_frame")
        frame = win.viewer_3d_frame
        assert frame.isFloating()

        frame.close()

        assert frame.isFloating(), (
            "RUNNING.md #61 -- closing a floating panel via its native "
            "close button must now HIDE it, staying floating internally, "
            "NOT redock it (that was #58-#60's behavior, superseded)"
        )
        assert not frame.isVisible(), (
            "the actual point of #61 -- the panel must disappear from "
            "view even though it's still floating underneath"
        )
        assert "viewer_3d_frame" in win._floating_panels, (
            "still floating, just hidden -- _floating_panels must still "
            "reflect that"
        )
    finally:
        win.close()
    print("_PanelDockWidget.closeEvent: native close button on a floated "
          "Graph panel window HIDES it (stays floating, just invisible) "
          "instead of redocking -- OK (RUNNING.md #61)")


def test_dock_widget_area_is_too_coarse_to_trust_for_placement():
    """RUNNING.md #60 -- guards against ever trusting dockWidgetArea() for
    per-panel placement again, the false premise #59's first attempt was
    built on. Every panel in this app's default layout is nested into ONE
    split-tree via splitDockWidget, rooted at the single addDockWidget(
    Left, browser_frame) call in _build_default_dock_layout -- Qt only
    tracks which of the 4 cardinal areas a whole tree is rooted in, not
    where within it an individual dock sits, so EVERY panel reports
    LeftDockWidgetArea here, including ones that are visually nowhere near
    the left column (the Graph panel, deep in the right column). If this
    ever starts failing, it means the layout construction changed enough
    that per-area tracking might be viable again -- worth knowing, not
    just an accident to shrug off."""
    _app()
    from PySide6.QtCore import Qt as _Qt
    win = MainWindow()
    try:
        areas = {
            frame_attr: win.dockWidgetArea(getattr(win, frame_attr))
            for _a, _l, frame_attr, _d in main_window_module.PANEL_TOGGLES
        }
        assert len(set(areas.values())) == 1, (
            f"expected every panel to report the SAME dockWidgetArea() "
            f"(the false premise #59 first tried to build on) -- got "
            f"distinct areas instead, meaning per-area placement may "
            f"actually be viable now: {areas}"
        )
        assert areas["viewer_3d_frame"] == _Qt.DockWidgetArea.LeftDockWidgetArea
    finally:
        win.close()
    print("dockWidgetArea(): confirmed every panel reports the same "
          "coarse area regardless of real nested position -- OK (this is "
          "WHY #59's first fix attempt didn't work, and why #60 uses "
          "saveState()/restoreState() instead)")


def test_uncheck_float_menu_after_moving_panel_lands_back_in_its_own_spot():
    """RUNNING.md #59/#60/#61. This exercises #60's saveState()/
    restoreState() redock logic -- as of #61, that logic no longer runs
    from the native X button (see _PanelDockWidget.closeEvent's
    docstring, and test_close_floated_3d_viewer_via_native_close_button_
    hides_not_redocks), only from MainWindow._dock_panel, i.e. the
    Workspace menu's Float-checkbox being unchecked (or its shortcut) --
    called directly here rather than through the menu action itself,
    same "call the underlying method directly" convention this whole
    file already uses for _float_panel/_dock_panel.

    Originally written for #59's follow-up report: "it only goes away if
    it hasn't been moved since popping out. if it has been moved, it just
    gets pushed behind other panels." #59's first fix attempt (per-panel
    dockWidgetArea() tracking) turned out to be built on a false premise
    -- see test_dock_widget_area_is_too_coarse_to_trust_for_placement --
    #60 replaces it with MainWindow._last_all_docked_state, a saveState()
    blob restored via restoreState(), which DOES reproduce a panel's
    exact nested position (not just some technically-valid coarse area).

    This sandbox has no real window manager, so an actual mouse-drag
    can't be simulated -- dock.move() (used in RUNNING.md #58/#59's own
    verification too) is the closest available proxy for "the panel has
    been repositioned since popping out."

    Asserts the panel's geometry after redocking EXACTLY matches its
    geometry before it ever floated -- a much stronger check than #58/
    #59's own tests used (dockWidgetArea() != NoDockWidgetArea, parent()
    is win, isWindow() False, or even dockWidgetArea() equality -- all of
    which, per the coarse-area test above, would trivially pass for EVERY
    panel regardless of whether it actually landed in the right spot).

    win.show() + processEvents() before floating -- RUNNING.md #56's own
    footnote already flagged that float/dock operations (there, the
    first-ever float specifically) are flaky under this sandbox's
    headless offscreen platform on a window that's never been shown;
    restoreState() (new in #60) turns out to be sensitive to the same
    condition even for a NON-first float, confirmed directly (this test
    failed without show() until this line was added -- a real MainWindow
    is always shown long before a user could possibly float/move/close
    anything, so this only affects the TEST's fidelity, not the fix)."""
    _app()
    win = MainWindow()
    win.show()
    # Two ticks, not one -- a single processEvents() left geometry still
    # settling under this sandbox's offscreen platform (seen directly: a
    # snapshot taken after only one tick didn't match a LATER read of the
    # exact same still-docked geometry, off by exactly one internal
    # layout pass), which would make this test's own baseline unreliable
    # before the real float/move/close sequence even starts.
    QApplication.processEvents()
    QApplication.processEvents()
    try:
        dock = win.scopes_frame
        original_geometry = dock.geometry()

        win._float_panel("scopes_frame")
        QApplication.processEvents()
        assert dock.isFloating()

        # Simulate "moved since popping out" -- the specific trigger Tom
        # described.
        dock.move(dock.pos().x() + 150, dock.pos().y() + 100)
        QApplication.processEvents()

        win._dock_panel("scopes_frame")  # the Float menu's uncheck path
        QApplication.processEvents()

        assert not dock.isFloating(), (
            "unchecking Float on a moved floating panel must redock it, "
            "not leave it floating"
        )
        assert not dock.isWindow(), (
            "must come back as a plain embedded widget, not a stray "
            "top-level window"
        )
        assert dock.parentWidget() is win
        assert dock.geometry() == original_geometry, (
            f"must land back in its EXACT original spot -- got "
            f"{dock.geometry()}, expected {original_geometry}. A "
            f"technically-docked-somewhere-valid-but-wrong placement is "
            f"exactly what would still look like 'pushed behind other "
            f"panels'"
        )
        assert "scopes_frame" not in win._floating_panels
    finally:
        win.close()
    print("MainWindow._dock_panel: unchecking Float on a Graph panel "
          "AFTER moving it redocks back into its EXACT original spot -- "
          "OK (Tom's #59/#60 repro, now exercised via the Float-menu path "
          "since #61 moved the native-X-button behavior to hide instead)")


def test_uncheck_float_menu_lands_in_remembered_position_not_default():
    """RUNNING.md #60/#61 -- exercises the Float-menu-uncheck path (see
    test_uncheck_float_menu_after_moving_panel_lands_back_in_its_own_spot's
    docstring for why this file's redock tests moved off the native X
    button after #61). Tom's own hunch: "any chance this is related to
    the code that retains window position between launches?" Confirmed
    real, independent of the coarse-area bug: _snapshot_all_docked_state
    only ran synchronously during __init__ (describing the DEFAULT
    layout) until this fix -- the remembered workspace's restoreState()
    call is deliberately DEFERRED to the next event-loop tick (see
    load_workspace_state's own DEFERRED docstring, for an unrelated
    startup-ordering reason), which is AFTER that snapshot, so it used to
    go stale the instant a real remembered (non-default) layout loaded --
    which, for an app Tom's been actively free-form rearranging, is most
    launches, not an edge case.

    Builds a REAL customized layout (moves Graph to BottomDockWidgetArea,
    somewhere its own default layout never puts it), captures it via
    get_workspace_state() the same way a real closeEvent would, then
    monkeypatches load_remembered_workspace so a FRESH MainWindow loads
    it -- same pattern test_mainwindow_construction_does_not_float_
    synchronously already uses for the same reason (the saved shape is an
    opaque restoreState() blob since the docking migration, not a
    hand-constructable dict)."""
    _app()
    from PySide6.QtCore import Qt as _Qt
    source_win = MainWindow()
    source_win.show()
    # Two ticks -- see test_close_floated_panel_after_moving_it_lands_
    # back_in_its_own_spot's comment on why one tick alone left geometry
    # still settling under this sandbox's offscreen platform.
    QApplication.processEvents()
    QApplication.processEvents()
    try:
        source_win.addDockWidget(_Qt.DockWidgetArea.BottomDockWidgetArea, source_win.scopes_frame)
        QApplication.processEvents()
        customized_geometry = source_win.scopes_frame.geometry()
        remembered_state = source_win.get_workspace_state()
    finally:
        source_win.close()

    orig = main_window_module.workspace_io.load_remembered_workspace
    main_window_module.workspace_io.load_remembered_workspace = lambda: remembered_state
    try:
        win = MainWindow()
        win.show()
        dock = win.scopes_frame
        # Applies the deferred restoreState() -- on real hardware (a real
        # window manager actually confirming the resize) this can take more
        # than a couple of ticks, unlike this sandbox's offscreen platform,
        # where geometry applies synchronously with no real WM round trip.
        _settle_events(lambda: dock.geometry() == customized_geometry)
        try:
            assert dock.geometry() == customized_geometry, (
                "sanity check: the remembered (non-default) layout must "
                "actually have been restored before this test's real "
                "assertion means anything"
            )

            win._float_panel("scopes_frame")
            QApplication.processEvents()
            dock.move(dock.pos().x() + 150, dock.pos().y() + 100)
            QApplication.processEvents()
            win._dock_panel("scopes_frame")  # the Float menu's uncheck path
            QApplication.processEvents()

            assert not dock.isFloating()
            assert dock.geometry() == customized_geometry, (
                f"must redock to the REMEMBERED position ({customized_geometry}), "
                f"not the default-layout position -- got {dock.geometry()}"
            )
        finally:
            win.close()
    finally:
        main_window_module.workspace_io.load_remembered_workspace = orig
    print("MainWindow._dock_panel: unchecking Float on a floated-then-moved "
          "panel redocks to the "
          "REMEMBERED (non-default) position, not a stale default-layout "
          "guess -- OK (Tom's own #60 hunch, confirmed)")


def test_repeated_float_dock_cycles_do_not_raise():
    """Tom's report happened after some amount of normal use, not
    necessarily the first float/dock -- a few round trips is a cheap way
    to catch anything that only breaks on the second+ reparent (e.g.
    stale references, double-registration of the event filter)."""
    _app()
    win = MainWindow()
    try:
        for _ in range(3):
            win._float_panel("viewer_3d_frame")
            win._dock_panel("viewer_3d_frame")
        assert "viewer_3d_frame" not in win._floating_panels
    finally:
        win.close()
    print("MainWindow: 3 repeated float/dock cycles on the 3D Viewer "
          "panel complete without raising -- OK")


# ---- docking migration (per Tom -- "Yeah. we want free form... do the ----
# ---- migration to new system") -- QMainWindow + QDockWidget replacing the
# ---- old hand-built QWidget + three hardcoded nested QSplitters ------------

def test_every_panel_is_a_real_dock_placed_in_the_main_window():
    """The actual point of the migration -- confirms every one of the ten
    panels is a genuine QDockWidget that QMainWindow itself has placed
    somewhere (dockWidgetArea() returns a real area, not
    Qt.DockWidgetArea.NoDockWidgetArea, which is what an UN-added dock
    widget would report), not just an object that happens to be named
    "_frame" the way PanelFrame was before this migration."""
    _app()
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtWidgets import QDockWidget as _QDockWidget
    win = MainWindow()
    try:
        for _action_attr, _label, frame_attr, _default in main_window_module.PANEL_TOGGLES:
            dock = getattr(win, frame_attr)
            assert isinstance(dock, _QDockWidget), f"{frame_attr} is not a QDockWidget"
            assert win.dockWidgetArea(dock) != _Qt.DockWidgetArea.NoDockWidgetArea, (
                f"{frame_attr} was never actually placed in the main window's "
                f"dock layout"
            )
    finally:
        win.close()
    print("MainWindow: all ten panels are real QDockWidgets placed "
          "somewhere in the main window's dock layout -- OK")


def test_docks_are_movable_and_floatable():
    """The literal feature Tom asked for -- confirms every dock actually
    has DockWidgetMovable (drag-to-rearrange) and DockWidgetFloatable
    (drag-out-to-float) set, not just that it happens to be a QDockWidget
    instance (a QDockWidget with neither feature set would be a fixed,
    unmovable panel -- Qt supports that mode too, so this has to be
    checked explicitly, not assumed)."""
    _app()
    from PySide6.QtWidgets import QDockWidget as _QDockWidget
    win = MainWindow()
    try:
        for _action_attr, _label, frame_attr, _default in main_window_module.PANEL_TOGGLES:
            dock = getattr(win, frame_attr)
            features = dock.features()
            assert bool(features & _QDockWidget.DockWidgetFeature.DockWidgetMovable), (
                f"{frame_attr} is not draggable/movable"
            )
            assert bool(features & _QDockWidget.DockWidgetFeature.DockWidgetFloatable), (
                f"{frame_attr} cannot be dragged out to float"
            )
    finally:
        win.close()
    print("MainWindow: every panel's QDockWidget has Movable+Floatable "
          "features set -- OK (this is what makes free-form rearranging "
          "actually possible, not just theoretically supported by the "
          "widget class)")


def test_custom_title_bar_carries_label_and_corner_menu():
    """Confirms _build_dock_title_bar actually did its job for a panel
    that has a corner "..." menu (Graph) -- the dock's titleBarWidget is
    the custom row (reusing the same #panelTitleRow/#panelTitle object
    names style.py already styles, see that function's docstring), and
    that row's corner_widget slot IS the panel's own real menu_button
    object (not a copy/rebuild of it) -- same object identity
    SampleImagePanel/StackPanel/ScopesPanel/LutViewer3D's menu_button
    already had in the pre-migration PanelFrame title row."""
    _app()
    from PySide6.QtWidgets import QLabel
    win = MainWindow()
    try:
        title_bar = win.viewer_3d_frame.titleBarWidget()
        assert title_bar is not None
        assert title_bar.objectName() == "panelTitleRow"

        label = title_bar.findChild(QLabel)
        assert label is not None
        assert label.objectName() == "panelTitle"
        assert label.text() == "Graph"

        assert win.viewer_3d.menu_button in title_bar.findChildren(type(win.viewer_3d.menu_button))
    finally:
        win.close()
    print("_build_dock_title_bar: Graph's dock has a custom title bar "
          "carrying the 'Graph' label (#panelTitle) and the panel's own "
          "real '...' menu_button, reusing the pre-migration "
          "#panelTitleRow styling -- OK")


def test_reset_to_default_workspace_redocks_and_reshows_everything():
    """Workspace > "Reset to Default Workspace" (per Tom) -- confirms it
    actually undoes both kinds of departure from the default: a panel
    floating, and a panel hidden entirely."""
    _app()
    win = MainWindow()
    try:
        win._float_panel("viewer_3d_frame")
        win.scopes_frame.setVisible(False)
        assert win.viewer_3d_frame.isFloating()
        assert win.scopes_frame.isHidden()

        win._reset_workspace_to_default()

        assert not win.viewer_3d_frame.isFloating(), (
            "Reset to Default Workspace must redock a floating panel"
        )
        assert not win.scopes_frame.isHidden(), (
            "Reset to Default Workspace must re-show a hidden panel"
        )
        assert "viewer_3d_frame" not in win._floating_panels
    finally:
        win.close()
    print("MainWindow._reset_workspace_to_default: redocks a floating "
          "panel and re-shows a hidden one -- OK")


def test_load_workspace_state_tolerates_pre_migration_format():
    """An old, pre-migration remembered-workspace or preset file has
    "panels"/"splitters"/"floating" keys and no "dock_state_b64" -- see
    workspace_io.py's WORKSPACE_FORMAT_VERSION 1->2 bump. window_size is
    still honored (harmless, and arguably still useful, to resize the
    window from an old preset) but the dock-layout-shaped keys
    ("panels"/"splitters"/"floating") are simply never read by the new
    code at all -- confirms that's a graceful no-op rather than a
    raise/crash, not that literally nothing in the dict is applied."""
    _app()
    win = MainWindow()
    try:
        old_format_state = {
            "window_size": [1400, 900],
            "panels": {"browser_frame": False},
            "floating": ["viewer_3d_frame"],
            "splitters": {"main_splitter": [260, 700, 340]},
        }
        win.load_workspace_state(old_format_state)  # must not raise
        QApplication.processEvents()
        assert "viewer_3d_frame" not in win._floating_panels, (
            "an old-format dict's 'floating' key must be ignored, not "
            "interpreted -- there is no code path that reads it anymore"
        )
    finally:
        win.close()
    print("MainWindow.load_workspace_state: a pre-migration-format dict "
          "(no dock_state_b64) is tolerated as a no-op, not applied or "
          "raised on -- OK")


def test_docked_panel_close_event_is_not_intercepted():
    """_PanelDockWidget.closeEvent only intervenes while isFloating() is
    True (see that class's docstring) -- confirms a DOCKED panel's
    closeEvent falls through to the normal Qt handling (hide) rather than
    being redocked (it's already docked -- there's nothing to redock)."""
    _app()
    win = MainWindow()
    try:
        dock = win.scopes_frame
        assert not dock.isFloating()
        dock.close()
        assert dock.isHidden()
        assert not dock.isFloating()
    finally:
        win.close()
    print("_PanelDockWidget.closeEvent: a docked (non-floating) panel's "
          "close just hides it normally, no redock logic kicks in -- OK")


if __name__ == "__main__":
    test_safe_gl_view_widget_paintgl_skips_when_invalid()
    test_safe_gl_view_widget_paintgl_calls_through_when_valid()
    test_set_lut_centers_camera_on_data_not_origin()
    test_set_lut_sets_distance_from_actual_data_extent()
    test_set_lut_skips_rebuild_while_hidden_then_catches_up_on_show()
    test_set_lut_while_visible_still_rebuilds_immediately()
    test_frame_camera_on_empty_positions_does_not_raise()
    test_rebuild_gl_view_reframes_camera_on_the_new_widget()
    test_cage_opacity_slider_default_matches_pyqtgraph_grid_default()
    test_set_lut_builds_a_cage_connecting_the_scatter_points()
    test_cage_opacity_slider_updates_cage_item_color()
    test_cage_opacity_survives_density_change_and_rebuild()
    test_no_lut_loaded_means_no_cage_item()
    test_menu_button_exists_and_defaults_to_3d_checked()
    test_3d_settings_action_opens_the_dialog_with_the_sliders()
    test_default_view_shows_gl_panel()
    test_selecting_1d_view_shows_curve_plot_with_exactly_one_curve_visible()
    test_selecting_3d_after_1d_restores_gl_panel()
    test_view_switch_keeps_stack_minimum_size_hint_constant()
    test_mode_switch_never_destroys_or_rebuilds_gl_view()
    test_corner_menu_and_menu_bar_menu_stay_in_sync()
    test_set_lut_populates_curve_plot_with_domain_aware_data()
    test_curve_plot_updates_even_while_3d_view_is_current()
    test_clear_resets_curve_plot_data_too()
    test_clear_also_removes_cage_item()
    test_rebuild_gl_view_keeps_discarded_widget_referenced()
    test_close_floated_3d_viewer_with_no_lut_loaded_does_not_raise()
    test_repeated_float_dock_with_no_lut_loaded_does_not_raise()
    test_main_sets_share_opengl_contexts_before_constructing_qapplication()
    test_load_workspace_state_defers_dock_state_restoration()
    test_mainwindow_construction_does_not_float_synchronously()
    test_close_event_docks_without_rebuilding_gl_content()
    test_rebuild_gl_view_replaces_the_widget_but_keeps_the_lut()
    test_rebuild_gl_view_with_no_lut_loaded_does_not_raise()
    test_rebuild_gl_view_stays_in_the_same_layout_position()
    test_float_panel_rebuilds_3d_viewer_gl_view()
    test_dock_panel_rebuilds_3d_viewer_gl_view()
    test_float_dock_rebuild_does_not_affect_other_panels()
    test_float_then_dock_3d_viewer_does_not_raise()
    test_close_floated_3d_viewer_via_native_close_button_hides_not_redocks()
    test_repeated_float_dock_cycles_do_not_raise()
    test_every_panel_is_a_real_dock_placed_in_the_main_window()
    test_docks_are_movable_and_floatable()
    test_custom_title_bar_carries_label_and_corner_menu()
    test_reset_to_default_workspace_redocks_and_reshows_everything()
    test_load_workspace_state_tolerates_pre_migration_format()
    test_docked_panel_close_event_is_not_intercepted()
    test_dock_widget_area_is_too_coarse_to_trust_for_placement()
    test_uncheck_float_menu_after_moving_panel_lands_back_in_its_own_spot()
    test_uncheck_float_menu_lands_in_remembered_position_not_default()
    print("All GL reparent safety tests passed.")
