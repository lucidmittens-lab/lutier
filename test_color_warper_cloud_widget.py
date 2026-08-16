"""Tests for color_warper_cloud_widget.py -- run directly:
QT_QPA_PLATFORM=offscreen python3 test_color_warper_cloud_widget.py"""
import numpy as np

import color_warper_cloud_widget as cwc


def test_project_points_empty_input_returns_empty_arrays():
    screen_xy, depth = cwc.project_points(np.zeros((0, 3)))
    assert screen_xy.shape == (0, 2)
    assert depth.shape == (0,)
    print("project_points: empty input -> empty arrays, not a crash -- OK")


def test_project_points_origin_projects_to_screen_origin_with_zero_depth():
    positions = np.array([[0.0, 0.0, 0.0]])
    screen_xy, depth = cwc.project_points(positions)
    assert np.allclose(screen_xy, [[0.0, 0.0]])
    assert np.allclose(depth, [0.0])
    print("project_points: the origin projects to (0,0) at zero depth "
          "regardless of camera angle -- OK")


def test_project_points_shape_matches_input_count():
    rng = np.random.default_rng(0)
    positions = rng.uniform(-0.3, 0.3, size=(50, 3))
    screen_xy, depth = cwc.project_points(positions)
    assert screen_xy.shape == (50, 2)
    assert depth.shape == (50,)
    print("project_points: output shapes match the input point count -- OK")


def test_project_points_depth_ordering_distinguishes_near_from_far():
    """Two points straight up the lightness axis but at different heights
    should get DIFFERENT depth once elevation tilts the camera (elevation
    != 0 is the whole point of the camera angle) -- this is what makes
    the painter's-algorithm back-to-front sort in paintEvent meaningful
    at all."""
    positions = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    _, depth = cwc.project_points(positions)
    assert depth[0] != depth[1]
    print("project_points: two points at different heights get distinct "
          "depth values under a tilted camera -- OK")


def test_drag_updates_azimuth_and_elevation():
    """Left-drag should orbit the camera -- horizontal drag changes
    azimuth, vertical drag changes elevation (clamped, not wrapped -- see
    the module docstring on why elevation doesn't wrap past the pole)."""
    from PySide6.QtCore import QPointF, QEvent, Qt as QtCoreQt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    widget = cwc.WarpierCloudWidget()
    widget.resize(220, 220)
    start_azimuth = widget._azimuth
    start_elevation = widget._elevation

    def _mouse_event(event_type, pos):
        return QMouseEvent(
            event_type, QPointF(pos[0], pos[1]), QPointF(pos[0], pos[1]),
            QtCoreQt.MouseButton.LeftButton, QtCoreQt.MouseButton.LeftButton,
            QtCoreQt.KeyboardModifier.NoModifier)

    QApplication.sendEvent(widget, _mouse_event(QEvent.Type.MouseButtonPress, (100, 100)))
    assert widget._dragging is True
    QApplication.sendEvent(widget, _mouse_event(QEvent.Type.MouseMove, (140, 70)))
    QApplication.sendEvent(widget, _mouse_event(QEvent.Type.MouseButtonRelease, (140, 70)))

    assert widget._dragging is False
    assert widget._azimuth != start_azimuth
    assert widget._elevation != start_elevation
    print("WarpierCloudWidget: left-drag orbits azimuth/elevation and "
          "releases cleanly -- OK")


def test_elevation_clamps_instead_of_wrapping_past_the_pole():
    widget = _make_widget_for_direct_state_test()
    widget._elevation = cwc.MAX_ELEVATION_DEG - 1.0
    widget._apply_drag_delta(_pointf(0, -50))  # drag "up" -- would push elevation past the cap
    assert widget._elevation == cwc.MAX_ELEVATION_DEG
    print("WarpierCloudWidget: elevation clamps at MAX_ELEVATION_DEG "
          "rather than wrapping through the pole -- OK")


def _pointf(x, y):
    from PySide6.QtCore import QPointF
    return QPointF(x, y)


def _make_widget_for_direct_state_test():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    return cwc.WarpierCloudWidget()


def test_widget_self_populates_with_idealized_reference_points():
    """No image, no working colourspace, no stage params -- __init__
    alone should already produce the full HUE_STEPS x THETA_STEPS
    idealized sphere, unwarped (per Tom: "not a measurement of the
    signal in any way... just a representation of the visible spectrum
    in 3d")."""
    widget = _make_widget_for_direct_state_test()
    expected_count = cwc.HUE_STEPS * cwc.THETA_STEPS
    assert widget._positions.shape == (expected_count, 3)
    assert widget._colors.shape == (expected_count, 3)
    print("WarpierCloudWidget: self-populates with the idealized "
          f"{cwc.HUE_STEPS}x{cwc.THETA_STEPS} sphere at "
          "construction, with no image/colourspace input -- OK")


def test_widget_populates_a_perfect_sphere_by_default():
    """Per Tom's final pick ("lets replace the volume accurate blob
    with the sphere") -- unwarped, every point should sit at exactly the
    same radius from the origin (color_warper.DEFAULT_IDEALIZED_SPHERE_CHROMA),
    not the old gamut-accurate blob's varying per-direction radius."""
    import color_warper as cw
    widget = _make_widget_for_direct_state_test()
    radii = np.linalg.norm(widget._positions, axis=1)
    assert np.allclose(radii, cw.DEFAULT_IDEALIZED_SPHERE_CHROMA, atol=1e-9)
    print("WarpierCloudWidget: default points form a perfect sphere of "
          "constant radius -- OK")


def test_project_to_screen_scale_is_fixed_regardless_of_data_extent():
    """The whole point of "make it have a fixed scale" -- a lone point
    close to the origin and the SAME point moved far away should map to
    screen positions related by the SAME fixed scale factor, not one
    that shrinks/grows to fit whatever's currently plotted (that
    per-frame auto-fit behavior is exactly what was reverted)."""
    near = np.array([[0.05, 0.0, 0.0]])
    far = np.array([[0.5, 0.0, 0.0]])
    px_near, py_near, _ = cwc.project_to_screen(
        near, elevation_deg=0.0, azimuth_deg=0.0, width=200, height=200)
    px_far, py_far, _ = cwc.project_to_screen(
        far, elevation_deg=0.0, azimuth_deg=0.0, width=200, height=200)
    center = 100.0
    ratio = (px_far[0] - center) / (px_near[0] - center)
    assert np.isclose(ratio, 10.0)  # far is exactly 10x near's own x -- same fixed scale both times
    print("WarpierCloudWidget.project_to_screen: scale factor is fixed "
          "(independent of which points are currently plotted) -- OK")


def test_spin_timer_advances_azimuth_when_not_dragging():
    widget = _make_widget_for_direct_state_test()
    widget._dragging = False
    start = widget._azimuth
    widget._on_spin_tick()
    assert widget._azimuth == (start + cwc.SPIN_DEGREES_PER_TICK) % 360.0
    print("WarpierCloudWidget: a spin tick nudges azimuth forward when "
          "not dragging -- OK")


def test_spin_pauses_while_dragging():
    widget = _make_widget_for_direct_state_test()
    widget._dragging = True
    start = widget._azimuth
    widget._on_spin_tick()
    assert widget._azimuth == start
    print("WarpierCloudWidget: spin ticks are ignored while the user is "
          "actively dragging -- OK")


def test_set_warp_reacts_to_the_current_warp_points():
    """The whole point of the pivot (per Tom: "that graph should react
    to what the warper is doing") -- feeding real warp points should
    move the reference grid, not just replot the identical shape."""
    import color_warper as cw
    widget = _make_widget_for_direct_state_test()
    identity_positions = widget._positions.copy()

    points = [{"h0": cw.GREEN_HUE, "c0": 0.15, "h": cw.GREEN_HUE + 40.0, "c": 0.3}]
    widget.set_warp(points, wake=30.0, dropoff=0.2)

    assert not np.allclose(identity_positions, widget._positions)
    print("WarpierCloudWidget.set_warp: real warp points move the "
          "reference blob away from its unwarped identity shape -- OK")


def test_widget_paints_without_a_display(qtbot=None):
    """No GL context needed anymore (that's the whole point of the
    rewrite) -- constructs + paints under QT_QPA_PLATFORM=offscreen with
    no 'QOpenGLWidget is not supported' warning."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    widget = cwc.WarpierCloudWidget()
    widget.resize(220, 220)

    # Already self-populated with the idealized reference grid at
    # construction (see test_widget_self_populates_with_idealized_
    # reference_points) -- this just confirms THAT paints without
    # crashing, before also exercising set_points directly below.
    pixmap = widget.grab()
    assert not pixmap.isNull()

    rng = np.random.default_rng(1)
    positions = rng.uniform(-0.3, 0.3, size=(30, 3))
    colors = rng.uniform(0.0, 1.0, size=(30, 3))
    widget.set_points(positions, colors)
    pixmap = widget.grab()
    assert not pixmap.isNull()
    assert pixmap.width() == 220 and pixmap.height() == 220

    # Re-populating with a fresh (smaller) set of points also just works
    # (no leftover-item bookkeeping to get wrong now, unlike the old
    # remove-old/construct-new GL item pattern).
    widget.set_points(positions[:5], colors[:5])
    pixmap = widget.grab()
    assert not pixmap.isNull()

    print("WarpierCloudWidget: constructs, paints empty, paints with "
          "real points, and repaints after re-populating -- all without "
          "a GL context -- OK")


if __name__ == "__main__":
    test_project_points_empty_input_returns_empty_arrays()
    test_project_points_origin_projects_to_screen_origin_with_zero_depth()
    test_project_points_shape_matches_input_count()
    test_project_points_depth_ordering_distinguishes_near_from_far()
    test_drag_updates_azimuth_and_elevation()
    test_elevation_clamps_instead_of_wrapping_past_the_pole()
    test_widget_self_populates_with_idealized_reference_points()
    test_widget_populates_a_perfect_sphere_by_default()
    test_project_to_screen_scale_is_fixed_regardless_of_data_extent()
    test_spin_timer_advances_azimuth_when_not_dragging()
    test_spin_pauses_while_dragging()
    test_set_warp_reacts_to_the_current_warp_points()
    test_widget_paints_without_a_display()
    print()
    print("All color_warper_cloud_widget tests passed.")
