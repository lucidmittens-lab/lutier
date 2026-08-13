"""Tests for grading_rgb_curve.py -- run directly: python3 test_grading_rgb_curve.py"""
import numpy as np

import grading_rgb_curve as grc


def _skip(name):
    print(f"SKIPPED {name} -- PyOpenColorIO not installed")


def test_identity_curves_are_exact_noop_for_every_style():
    if not grc.OCIO_AVAILABLE:
        return _skip("test_identity_curves_are_exact_noop_for_every_style")
    rng = np.random.default_rng(0)
    img = rng.random((6, 6, 3)).astype(np.float32)
    for style, _label in grc.AVAILABLE_STYLES:
        out = grc.apply_grading_rgb_curve(img, style=style)
        assert np.allclose(out, img, atol=1e-6), style
        print(f"apply_grading_rgb_curve: identity curves are an exact "
              f"no-op under style={style!r} -- OK")


def test_default_style_is_log():
    assert grc.DEFAULT_STYLE == "log"


def test_matches_a_directly_built_ocio_transform():
    """Regression guard for the wrapper's own field-mapping (which
    channel maps to which attribute) -- builds the exact same
    GradingRGBCurve by hand, independent of build_grading_rgb_curve_
    value/_transform, and confirms apply_grading_rgb_curve's output
    matches applying that by-hand transform directly."""
    if not grc.OCIO_AVAILABLE:
        return _skip("test_matches_a_directly_built_ocio_transform")
    import PyOpenColorIO as ocio
    import cdl

    rng = np.random.default_rng(1)
    img = rng.uniform(0, 1, size=(8, 8, 3)).astype(np.float32)
    master_points = [(0.0, 0.0), (0.2, 0.35), (0.5, 0.55), (0.8, 0.7), (1.0, 1.0)]
    red_points = [(0.0, 0.0), (0.5, 0.6), (1.0, 1.0)]

    out = grc.apply_grading_rgb_curve(img, style="log", master=master_points, red=red_points)

    by_hand = ocio.GradingRGBCurve(ocio.GRADING_LOG)
    flat_master = [v for pt in master_points for v in pt]
    flat_red = [v for pt in red_points for v in pt]
    by_hand.master = ocio.GradingBSplineCurve(flat_master)
    by_hand.red = ocio.GradingBSplineCurve(flat_red)
    by_hand_transform = ocio.GradingRGBCurveTransform(ocio.GRADING_LOG)
    by_hand_transform.setValue(by_hand)

    cfg = cdl._get_raw_config()
    cpu = cfg.getProcessor(by_hand_transform).getDefaultCPUProcessor()
    expected = img.copy()
    desc = ocio.PackedImageDesc(expected.reshape(-1, 3), expected.shape[0] * expected.shape[1], 1, 3)
    cpu.apply(desc)

    assert np.allclose(out, expected, atol=1e-6)
    print("apply_grading_rgb_curve: matches a by-hand-built ocio.GradingRGBCurveTransform "
          "exactly -- wrapper's field mapping is correct -- OK")


def test_boosting_master_curve_midpoint_lifts_midtones():
    """Sanity check on direction, not exact math: raising the master
    curve's middle control point should raise a mid-value pixel, and
    matter less for near-black/near-white pixels (the whole point of a
    curve tool vs. a flat offset)."""
    if not grc.OCIO_AVAILABLE:
        return _skip("test_boosting_master_curve_midpoint_lifts_midtones")
    lifted_master = [(0.0, 0.0), (0.5, 0.65), (1.0, 1.0)]
    mid = np.full((1, 1, 3), 0.5, dtype=np.float32)
    dark = np.full((1, 1, 3), 0.02, dtype=np.float32)
    bright = np.full((1, 1, 3), 0.98, dtype=np.float32)

    mid_out = grc.apply_grading_rgb_curve(mid, master=lifted_master)[0, 0, 0]
    dark_out = grc.apply_grading_rgb_curve(dark, master=lifted_master)[0, 0, 0]
    bright_out = grc.apply_grading_rgb_curve(bright, master=lifted_master)[0, 0, 0]

    assert mid_out > 0.55, f"expected the lifted midpoint to noticeably raise 0.5, got {mid_out}"
    assert abs(dark_out - 0.02) < abs(mid_out - 0.5), "near-black should move less than the midpoint"
    assert abs(bright_out - 0.98) < abs(mid_out - 0.5), "near-white should move less than the midpoint"
    print("apply_grading_rgb_curve: lifting the master curve's midpoint "
          "raises midtones more than near-black/near-white -- OK")


def test_too_few_control_points_falls_back_to_identity():
    """A curve with fewer than MIN_CONTROL_POINTS (e.g. a UI briefly
    left with one point mid-edit) shouldn't raise -- it should fall back
    to the identity curve rather than handing OCIO an invalid one."""
    if not grc.OCIO_AVAILABLE:
        return _skip("test_too_few_control_points_falls_back_to_identity")
    img = np.random.default_rng(2).uniform(0, 1, size=(2, 2, 3)).astype(np.float32)
    out = grc.apply_grading_rgb_curve(img, master=[(0.3, 0.3)])
    assert np.allclose(out, img, atol=1e-6)
    print("apply_grading_rgb_curve: fewer than MIN_CONTROL_POINTS falls "
          "back to the identity curve instead of raising -- OK")


def test_unknown_style_raises():
    if not grc.OCIO_AVAILABLE:
        return _skip("test_unknown_style_raises")
    try:
        grc.apply_grading_rgb_curve(np.zeros((1, 1, 3), dtype=np.float32), style="nonsense")
        raise AssertionError("expected a ValueError for an unknown style")
    except ValueError:
        print("apply_grading_rgb_curve: an unknown style raises ValueError -- OK")


if __name__ == "__main__":
    test_identity_curves_are_exact_noop_for_every_style()
    test_default_style_is_log()
    test_matches_a_directly_built_ocio_transform()
    test_boosting_master_curve_midpoint_lifts_midtones()
    test_too_few_control_points_falls_back_to_identity()
    test_unknown_style_raises()
    print()
    print("All grading_rgb_curve tests passed.")
