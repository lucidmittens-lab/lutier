"""Tests for matte_shape.py -- run directly:
python3 test_matte_shape.py"""
import numpy as np

import matte_shape as ms


def test_center_pixel_is_fully_keyed():
    matte = ms.compute_shape_matte((100, 200), {})
    assert matte[50, 100] == 1.0
    print("compute_shape_matte: the shape's own center pixel is fully "
          "keyed (1.0) -- OK")


def test_far_corner_is_unkeyed():
    matte = ms.compute_shape_matte((100, 200), {})
    assert matte[0, 0] == 0.0
    assert matte[99, 199] == 0.0
    print("compute_shape_matte: a far corner, well outside the default "
          "shape, is unkeyed (0.0) -- OK")


def test_ellipse_stays_circular_regardless_of_image_aspect_ratio():
    """width/height fractions are converted against the image's own real
    (H, W) before the in/out test -- so equal REAL pixel half-extents
    (not equal fractions) must produce a symmetric result on a
    non-square image. Even at softness=0 there's a ~1px transition zone
    (same "never a razor-thin binary edge" convention keyer.py's own
    _band_term already uses), so the probe points here sit comfortably
    inside/outside that zone rather than right at the boundary."""
    h, w = 100, 200
    params = {"width": 0.2, "height": 0.4, "softness": 0.0}  # both -> 20px half-extent
    matte = ms.compute_shape_matte((h, w), params)
    assert matte[50, 100 + 15] == 1.0 and matte[50, 100 + 25] == 0.0
    assert matte[50 + 15, 100] == 1.0 and matte[50 + 25, 100] == 0.0
    print("compute_shape_matte: an ellipse with matching REAL pixel "
          "half-extents stays circular on a non-square image -- OK")


def test_rectangle_hard_edge_is_a_clean_box():
    h, w = 60, 60
    matte = ms.compute_shape_matte((h, w), {
        "shape": "rectangle", "center_x": 0.5, "center_y": 0.5,
        "width": 0.5, "height": 0.5, "softness": 0.0})
    # half-extent = 0.5*60/2 = 15px in each direction from center (30,30)
    # -- probe well clear of the ~1px transition zone (see the ellipse
    # test's own comment on why softness=0 still isn't a razor edge).
    assert matte[30, 30 + 10] == 1.0 and matte[30, 30 + 20] == 0.0
    assert matte[30 + 10, 30] == 1.0 and matte[30 + 20, 30] == 0.0
    print("compute_shape_matte: a hard-edged (softness=0) rectangle is "
          "a clean box at its own declared half-extent -- OK")


def test_rotation_reorients_a_rectangle():
    h, w = 100, 200
    unrotated = ms.compute_shape_matte((h, w), {
        "shape": "rectangle", "width": 0.5, "height": 0.1, "softness": 0.0})
    rotated_90 = ms.compute_shape_matte((h, w), {
        "shape": "rectangle", "width": 0.5, "height": 0.1, "rotation": 90.0, "softness": 0.0})
    assert not np.array_equal(unrotated, rotated_90), \
        "a 90-degree rotation must visibly change a non-square rectangle's own footprint"
    print("compute_shape_matte: 'rotation' visibly reorients the shape "
          "-- OK")


def test_softness_widens_the_edge():
    h, w = 100, 200
    hard = ms.compute_shape_matte((h, w), {"softness": 0.0})
    soft = ms.compute_shape_matte((h, w), {"softness": 1.0})
    # a fixed point just outside the shape's own hard edge
    probe = (50, 100 + int(0.3 * w / 2) + 3)
    assert soft[probe] > hard[probe]
    print("compute_shape_matte: higher softness widens the edge, "
          "raising the matte value just outside the hard boundary -- OK")


def test_invert_flips_the_result():
    h, w = 40, 40
    plain = ms.compute_shape_matte((h, w), {})
    inverted = ms.compute_shape_matte((h, w), {"invert": True})
    assert np.allclose(plain + inverted, 1.0, atol=1e-9)
    print("compute_shape_matte: invert=True flips the matte (1 - matte) "
          "-- OK")


def test_rectangle_and_ellipse_differ_at_the_same_params():
    h, w = 100, 200
    params = {"width": 0.4, "height": 0.4, "softness": 0.1}
    ellipse = ms.compute_shape_matte((h, w), {**params, "shape": "ellipse"})
    rectangle = ms.compute_shape_matte((h, w), {**params, "shape": "rectangle"})
    assert not np.allclose(ellipse, rectangle), \
        "a corner region should be inside the rectangle but outside the inscribed ellipse"
    print("compute_shape_matte: 'ellipse' and 'rectangle' produce "
          "genuinely different footprints for the same size params -- OK")


def test_unknown_shape_raises():
    try:
        ms.compute_shape_matte((10, 10), {"shape": "triangle"})
        assert False, "expected a ValueError for an unknown shape"
    except ValueError:
        pass
    print("compute_shape_matte: an unknown shape raises ValueError -- OK")


def test_output_is_always_in_unit_range():
    matte = ms.compute_shape_matte((50, 80), {"softness": 0.9, "width": 1.5, "height": 1.5})
    assert np.all(matte >= 0.0) and np.all(matte <= 1.0)
    print("compute_shape_matte: output is always clamped to [0,1], even "
          "for an oversized shape -- OK")


if __name__ == "__main__":
    test_center_pixel_is_fully_keyed()
    test_far_corner_is_unkeyed()
    test_ellipse_stays_circular_regardless_of_image_aspect_ratio()
    test_rectangle_hard_edge_is_a_clean_box()
    test_rotation_reorients_a_rectangle()
    test_softness_widens_the_edge()
    test_invert_flips_the_result()
    test_rectangle_and_ellipse_differ_at_the_same_params()
    test_unknown_shape_raises()
    test_output_is_always_in_unit_range()
    print()
    print("All matte_shape tests passed.")
