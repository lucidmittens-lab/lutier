"""Tests for color_warper_wheel.py -- run directly:
QT_QPA_PLATFORM=offscreen python3 -m tests.test_color_warper_wheel"""
import sys

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

import color_warper as cw
from color_warper_wheel import (
    WarpierWheel, hue_to_screen_angle, screen_angle_to_hue,
    chroma_to_pixel_radius, pixel_radius_to_chroma,
    hue_chroma_to_pos, pos_to_hue_chroma, compute_gamut_boundary,
    boundary_chroma_at, build_mesh_grid,
    MESH_RINGS, MESH_SPOKES, VECTORSCOPE_TARGETS,
)


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _mouse(widget, event_type, pos, button, buttons):
    event = QMouseEvent(event_type, pos, pos, button, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)


def _click(widget, pos, button=Qt.MouseButton.LeftButton):
    _mouse(widget, QEvent.Type.MouseButtonPress, pos, button, button)


def _drag_to(widget, pos):
    _mouse(widget, QEvent.Type.MouseMove, pos, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)


def _release(widget, pos):
    _mouse(widget, QEvent.Type.MouseButtonRelease, pos, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)


# ---- geometry helpers ---------------------------------------------------

def test_hue_screen_angle_round_trip():
    for hue in (0.0, 35.0, 142.5, 250.0, 359.9):
        angle = hue_to_screen_angle(hue)
        back = screen_angle_to_hue(angle)
        assert abs(back - hue) < 1e-6
    print("hue_to_screen_angle / screen_angle_to_hue: exact round trip -- OK")


def test_green_points_down_magenta_points_up():
    """Per Tom: green at the bottom, magenta near the top -- screen-down
    is angle +90 deg (Qt y grows downward), screen-up is -90 deg."""
    green_angle_deg = (hue_to_screen_angle(142.5) * 180 / 3.14159265) % 360
    assert abs(green_angle_deg - 90.0) < 0.5
    print(f"hue_to_screen_angle(142.5 [green]) ~= 90 deg (screen-down) -- OK ({green_angle_deg:.2f})")


def test_vectorscope_targets_are_six_standard_primaries_secondaries():
    labels = [label for label, _hue in VECTORSCOPE_TARGETS]
    assert labels == ["R", "Y", "G", "C", "B", "M"]
    print("VECTORSCOPE_TARGETS: the six standard R/Y/G/C/B/M vectorscope "
          "targets, in hue order -- OK")


def test_mesh_spokes_include_every_vectorscope_primary_exactly():
    """Per Tom: 'the color background should put the primaries and
    secondaries on the web' -- each of the six R/Y/G/C/B/M hues must be
    an actual spoke in the mesh's own hue list, not just an independent
    overlay marker floating near (but not exactly on) a spoke."""
    from color_warper_wheel import _MESH_SPOKE_HUES
    for _label, hue in VECTORSCOPE_TARGETS:
        assert any(abs(h - hue) < 1e-6 for h in _MESH_SPOKE_HUES), (
            f"hue {hue} has no exact matching mesh spoke")
    print("_MESH_SPOKE_HUES: every vectorscope primary/secondary is an "
          "exact mesh spoke -- OK")


def test_build_mesh_grid_shape_and_range():
    max_boundary = 0.3
    H, C = build_mesh_grid(max_boundary)
    assert len(H) == len(C) == MESH_RINGS * MESH_SPOKES
    assert all(0.0 <= h < 360.0 for h in H)
    assert all(0.0 < c <= max_boundary + 1e-9 for c in C)
    print(f"build_mesh_grid: {MESH_RINGS}x{MESH_SPOKES} grid, hues in "
          "[0,360), chromas in (0, max_boundary] -- OK")


def test_mesh_deforms_with_real_warp_points():
    """Per Tom: the mesh should react to what the warper is actually
    doing -- confirmed via the SAME warp_hue_chroma function the wheel's
    own _draw_mesh calls, not a separate re-implementation."""
    max_boundary = 0.3
    H, C = build_mesh_grid(max_boundary)
    H0, C0 = cw.warp_hue_chroma(H, C, [])  # no points -> identity
    points = [{"h0": cw.GREEN_HUE, "c0": 0.15, "h": cw.GREEN_HUE + 30.0, "c": 0.25}]
    H1, C1 = cw.warp_hue_chroma(H, C, points, wake=40.0, dropoff=0.2)
    assert list(H0) == list(H)
    assert any(abs(a - b) > 1e-6 for a, b in zip(H0, H1))
    print("warp_hue_chroma: identity with no points, genuinely deforms "
          "the mesh grid with real warp points -- OK")


def test_chroma_pixel_radius_round_trip():
    max_boundary = 0.3
    disc_radius = 100.0
    for chroma in (0.0, 0.05, 0.15, 0.3):
        px = chroma_to_pixel_radius(chroma, max_boundary, disc_radius)
        back = pixel_radius_to_chroma(px, max_boundary, disc_radius)
        assert abs(back - chroma) < 1e-9
    print("chroma_to_pixel_radius / pixel_radius_to_chroma: exact round trip -- OK")


def test_chroma_to_pixel_radius_clamps_past_max_boundary():
    """Per Tom: 'the web easily gets cut off' -- a warped chroma value
    CAN legitimately exceed max_boundary (chroma_scale can push it past
    1.0, see color_warper.warp_hue_chroma), and the STORED value is
    intentionally left uncapped (see color_warper.py's own docstring),
    but the DISPLAYED pixel radius must never exceed the widget's own
    bounds -- otherwise a point or mesh line renders past the edge."""
    max_boundary = 0.3
    disc_radius = 100.0
    px = chroma_to_pixel_radius(max_boundary * 3.0, max_boundary, disc_radius)
    assert px == disc_radius
    px_negative = chroma_to_pixel_radius(-0.1, max_boundary, disc_radius)
    assert px_negative == 0.0
    print("chroma_to_pixel_radius: clamps to [0, disc_radius_px] even "
          "when chroma exceeds max_boundary -- OK")


def test_compute_gamut_boundary_is_sane():
    boundary = compute_gamut_boundary()
    assert len(boundary) == 360
    assert all(0.0 <= c <= 0.4 for c in boundary)
    assert max(boundary) > 0.1, "at least one hue should reach a real amount of chroma before leaving sRGB"
    print(f"compute_gamut_boundary: 360 entries, all in [0, 0.4], max={max(boundary):.3f} -- OK")


# ---- widget behavior ------------------------------------------------------

def test_wheel_starts_with_no_points():
    """Per Tom: no seeded default points -- a fresh wheel starts fully
    blank (an empty list is already an exact identity/no-op, see
    color_warper.points_are_identity)."""
    _app()
    wheel = WarpierWheel()
    assert wheel.points() == []
    print("WarpierWheel: starts with an empty points list, no seeded "
          "defaults -- OK")


def test_set_points_round_trips():
    _app()
    wheel = WarpierWheel()
    custom = [{"h0": 10.0, "c0": 0.05, "h": 40.0, "c": 0.1}]
    wheel.set_points(custom, emit=False)
    assert wheel.points() == custom
    print("WarpierWheel.set_points/points: round trips exactly -- OK")


def test_click_empty_space_adds_a_point():
    _app()
    wheel = WarpierWheel()
    wheel.show()
    before = len(wheel.points())
    rect = wheel._wheel_rect()
    pos = QPointF(rect.center().x() + rect.width() * 0.3, rect.center().y())
    _click(wheel, pos)
    assert len(wheel.points()) == before + 1
    new_point = wheel.points()[-1]
    assert new_point["h"] == new_point["h0"] and new_point["c"] == new_point["c0"], (
        "a freshly clicked point starts at its own anchor (no warp yet)")
    print("WarpierWheel: click on empty space adds a fresh identity point -- OK")


def test_click_and_drag_moves_target_leaves_anchor():
    _app()
    wheel = WarpierWheel()
    wheel.show()
    rect = wheel._wheel_rect()
    click_pos = QPointF(rect.center().x() + 40, rect.center().y())
    _click(wheel, click_pos)
    added = wheel.points()[-1]
    h0_before, c0_before = added["h0"], added["c0"]

    drag_pos = QPointF(rect.center().x() + 20, rect.center().y() - 40)
    _drag_to(wheel, drag_pos)
    _release(wheel, drag_pos)

    dragged = wheel.points()[-1]
    assert dragged["h0"] == h0_before and dragged["c0"] == c0_before, "anchor must stay put while dragging"
    assert dragged["h"] != h0_before or dragged["c"] != c0_before, "target should have moved"
    print("WarpierWheel: dragging an existing point moves h/c, leaves h0/c0 (its anchor) alone -- OK")


def test_dragging_past_the_edge_does_not_run_away():
    """Regression test for a real bug (per Tom: 'im allowed to sort of
    push beyond the border of the graph but it just shrinks the scale of
    the overall web and moves the point out infinitely. breaking the
    whole graph'): mousePressEvent/mouseMoveEvent used to interpret the
    CURRENT mouse position via _effective_max_boundary(), the DYNAMIC
    display scale -- but that scale itself is derived from the dragged
    point's own current chroma, so repeated ticks at a fixed overshoot
    position fed back into themselves and grew geometrically without
    bound. The fix uses the STATIC self._max_boundary for interpreting
    mouse input (only rendering uses the dynamic scale), so repeated
    identical mouse positions must produce IDENTICAL chroma, not
    ever-growing values."""
    _app()
    wheel = WarpierWheel()
    wheel.show()
    rect = wheel._wheel_rect()

    click_pos = QPointF(rect.center().x() + rect.width() * 0.5, rect.center().y())
    _click(wheel, click_pos)

    overshoot_pos = QPointF(rect.center().x() + rect.width() * 0.9, rect.center().y())
    chromas = []
    for _ in range(6):
        _drag_to(wheel, overshoot_pos)
        chromas.append(wheel.points()[-1]["c"])
    _release(wheel, overshoot_pos)

    assert all(abs(c - chromas[0]) < 1e-9 for c in chromas), (
        f"chroma should be stable across repeated identical-position "
        f"drag ticks past the edge, not growing: {chromas}")
    print("WarpierWheel: dragging past the edge and holding stays at a "
          "stable chroma across repeated ticks, no runaway -- OK")


def test_max_points_cap():
    _app()
    wheel = WarpierWheel()
    wheel.show()
    rect = wheel._wheel_rect()
    # Evenly spread every click around the ring (24 deg apart for
    # MAX_POINTS+3=15 clicks) -- comfortably above the ~12 deg minimum
    # separation the POINT_RADIUS*2.5 hit threshold needs at this radius
    # to tell two clicks apart as distinct points rather than the same
    # one (an earlier "i*37 deg" scheme wasn't reliably even and could
    # wrap two clicks within a few degrees of each other; with no seeded
    # starting points anymore -- per Tom -- every one of these 15 clicks
    # now has to land as a genuinely new point to reach the cap, so this
    # needs to be actually collision-free, not just "usually fine").
    total_clicks = cw.MAX_POINTS + 3
    import math
    for i in range(total_clicks):
        angle = math.radians(i * 360.0 / total_clicks)
        pos = QPointF(rect.center().x() + math.cos(angle) * rect.width() * 0.4,
                       rect.center().y() + math.sin(angle) * rect.width() * 0.4)
        _click(wheel, pos)
        _release(wheel, pos)
    assert len(wheel.points()) == cw.MAX_POINTS
    print(f"WarpierWheel: point count caps at MAX_POINTS ({cw.MAX_POINTS}) -- OK")


def test_right_click_removes_nearest_point():
    _app()
    wheel = WarpierWheel()
    wheel.show()
    rect = wheel._wheel_rect()
    # No seeded points anymore (per Tom) -- add one first so there's
    # something to remove.
    click_pos = QPointF(rect.center().x() + 30, rect.center().y())
    _click(wheel, click_pos)
    _release(wheel, click_pos)
    before = len(wheel.points())
    target = wheel.points()[0]
    from color_warper_wheel import hue_chroma_to_pos
    pos = hue_chroma_to_pos(target["h"], target["c"], wheel._max_boundary, rect)
    _click(wheel, pos, button=Qt.MouseButton.RightButton)
    assert len(wheel.points()) == before - 1
    assert target not in wheel.points()
    print("WarpierWheel: right-click removes the nearest point -- OK")


def test_points_changed_signal_fires_on_add_drag_remove():
    _app()
    wheel = WarpierWheel()
    wheel.show()
    events = []
    wheel.points_changed.connect(lambda pts: events.append(list(pts)))
    rect = wheel._wheel_rect()

    pos = QPointF(rect.center().x() + 30, rect.center().y())
    _click(wheel, pos)
    assert len(events) == 1

    _drag_to(wheel, QPointF(pos.x() + 5, pos.y() + 5))
    assert len(events) == 2

    from color_warper_wheel import hue_chroma_to_pos
    remove_pos = hue_chroma_to_pos(
        wheel.points()[0]["h"], wheel.points()[0]["c"], wheel._max_boundary, rect)
    _click(wheel, remove_pos, button=Qt.MouseButton.RightButton)
    assert len(events) == 3
    print("WarpierWheel.points_changed: fires once per add/drag/remove -- OK")


def test_reset_clears_to_no_points():
    _app()
    wheel = WarpierWheel()
    wheel.set_points([{"h0": 0.0, "c0": 0.0, "h": 0.0, "c": 0.0}], emit=False)
    wheel.reset()
    assert wheel.points() == []
    print("WarpierWheel.reset: clears back to an empty points list -- OK")


def test_reset_emit_false_suppresses_points_changed():
    """Regression coverage for the reset(emit=) passthrough added so
    stack_panel.py's own Color Warper "Reset to Identity" button can
    clear the wheel without firing a live-apply for every one of several
    controls it resets together -- see StackPanel._on_cw_reset_clicked's
    own docstring."""
    _app()
    wheel = WarpierWheel()
    wheel.set_points([{"h0": 0.0, "c0": 0.0, "h": 0.0, "c": 0.0}], emit=False)
    events = []
    wheel.points_changed.connect(events.append)
    wheel.reset(emit=False)
    assert wheel.points() == []
    assert events == [], "emit=False must suppress points_changed entirely"
    print("WarpierWheel.reset: emit=False clears points without firing "
          "points_changed -- OK")


if __name__ == "__main__":
    test_hue_screen_angle_round_trip()
    test_green_points_down_magenta_points_up()
    test_vectorscope_targets_are_six_standard_primaries_secondaries()
    test_mesh_spokes_include_every_vectorscope_primary_exactly()
    test_build_mesh_grid_shape_and_range()
    test_mesh_deforms_with_real_warp_points()
    test_chroma_to_pixel_radius_clamps_past_max_boundary()
    test_chroma_pixel_radius_round_trip()
    test_compute_gamut_boundary_is_sane()
    test_wheel_starts_with_no_points()
    test_set_points_round_trips()
    test_click_empty_space_adds_a_point()
    test_click_and_drag_moves_target_leaves_anchor()
    test_dragging_past_the_edge_does_not_run_away()
    test_max_points_cap()
    test_right_click_removes_nearest_point()
    test_points_changed_signal_fires_on_add_drag_remove()
    test_reset_clears_to_no_points()
    test_reset_emit_false_suppresses_points_changed()
    print()
    print("All color_warper_wheel tests passed.")
