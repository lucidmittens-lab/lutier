"""Tests for volumetric_scope.py -- run directly:
QT_QPA_PLATFORM=offscreen python3 test_volumetric_scope.py"""
import numpy as np

import aces_drt
import color_warper as cw
import volumetric_scope as vs


def test_default_colourspace_is_display_encoded_not_a_grading_space():
    """Deliberately differs from color_warper.DEFAULT_WORKING_COLOURSPACE
    (ACEScct, a log/ACES-workflow assumption) -- a raw loaded still with
    no declared color space is far more likely already display-encoded."""
    assert vs.DEFAULT_VOLUMETRIC_COLOURSPACE != cw.DEFAULT_WORKING_COLOURSPACE
    assert "srgb" in vs.DEFAULT_VOLUMETRIC_COLOURSPACE.lower()
    print(f"DEFAULT_VOLUMETRIC_COLOURSPACE = {vs.DEFAULT_VOLUMETRIC_COLOURSPACE!r} "
          "-- a display-encoded sRGB-ish default, distinct from WARPier's own -- OK")


def test_shapes_and_ranges():
    rng = np.random.default_rng(0)
    img = rng.uniform(0.05, 0.95, size=(20, 30, 3))
    positions, colors = vs.sample_pixels_to_oklch(img)
    n_pixels = 20 * 30
    assert positions.shape == (n_pixels, 3)
    assert colors.shape == (n_pixels, 3)
    assert colors.min() >= 0.0 and colors.max() <= 1.0
    print("sample_pixels_to_oklch: positions/colors shaped (n_pixels, 3), "
          "colors clipped to [0,1] -- OK")


def test_max_points_caps_the_sample():
    rng = np.random.default_rng(1)
    img = rng.uniform(0.05, 0.95, size=(50, 50, 3))  # 2500 pixels
    positions, colors = vs.sample_pixels_to_oklch(img, max_points=200)
    assert positions.shape == (200, 3)
    assert colors.shape == (200, 3)
    print("sample_pixels_to_oklch: max_points caps the subsample "
          "regardless of the source image's actual pixel count -- OK")


def test_fewer_pixels_than_max_points_returns_all_of_them():
    rng = np.random.default_rng(2)
    img = rng.uniform(0.05, 0.95, size=(3, 3, 3))  # 9 pixels
    positions, colors = vs.sample_pixels_to_oklch(img, max_points=6000)
    assert positions.shape == (9, 3)
    assert colors.shape == (9, 3)
    print("sample_pixels_to_oklch: fewer pixels than max_points -> every "
          "pixel is used, no padding/truncation -- OK")


def test_empty_image_returns_empty_arrays_not_a_crash():
    positions, colors = vs.sample_pixels_to_oklch(np.zeros((0, 0, 3)))
    assert positions.shape == (0, 3)
    assert colors.shape == (0, 3)
    print("sample_pixels_to_oklch: an empty image returns empty (0,3) "
          "arrays, not a crash -- OK")


def test_seed_is_deterministic_across_calls():
    rng = np.random.default_rng(3)
    img = rng.uniform(0.05, 0.95, size=(40, 40, 3))
    pos_a, _ = vs.sample_pixels_to_oklch(img, max_points=500)
    pos_b, _ = vs.sample_pixels_to_oklch(img, max_points=500)
    assert np.array_equal(pos_a, pos_b), "the same image should subsample identically across ticks"
    print("sample_pixels_to_oklch: fixed seed -> identical subsample "
          "across repeated calls on the same image -- OK")


def test_different_working_colourspace_changes_the_result():
    rng = np.random.default_rng(4)
    img = rng.uniform(0.05, 0.95, size=(10, 10, 3))
    pos_default, _ = vs.sample_pixels_to_oklch(img, working_colourspace=vs.DEFAULT_VOLUMETRIC_COLOURSPACE)
    pos_other, _ = vs.sample_pixels_to_oklch(img, working_colourspace="ACEScg")
    assert not np.allclose(pos_default, pos_other), (
        "decoding the same raw array as a different color space should change the OKLCh positions")
    print("sample_pixels_to_oklch: changing working_colourspace actually "
          "changes the OKLCh positions (the dropdown does something) -- OK")


def test_neutral_gray_lands_at_the_wheel_center():
    """A perfectly neutral pixel has zero chroma regardless of hue --
    should land exactly on the wheel's own center axis (x=y=0),
    matching the same "gray is the achromatic axis" property OKLab/OKLCh
    guarantee by construction (see color_warper.py's own reference-value
    tests)."""
    img = np.full((2, 2, 3), 0.5)
    positions, _ = vs.sample_pixels_to_oklch(img, working_colourspace=cw.REFERENCE_LINEAR_SPACE)
    # 1e-6, not machine epsilon -- even a same-space OCIO ColorSpaceTransform
    # round trip crosses OCIO's float32 PackedImageDesc boundary (see
    # aces_drt._apply_ocio_processor's own docstring), same tolerance
    # class every other OCIO round-trip test in this app already uses.
    assert np.allclose(positions[:, 0], 0.0, atol=1e-6)
    assert np.allclose(positions[:, 1], 0.0, atol=1e-6)
    print("sample_pixels_to_oklch: a neutral gray image lands exactly on "
          "the wheel's own center (x=y=0) -- OK")


if __name__ == "__main__":
    test_default_colourspace_is_display_encoded_not_a_grading_space()
    test_shapes_and_ranges()
    test_max_points_caps_the_sample()
    test_fewer_pixels_than_max_points_returns_all_of_them()
    test_empty_image_returns_empty_arrays_not_a_crash()
    test_seed_is_deterministic_across_calls()
    test_different_working_colourspace_changes_the_result()
    test_neutral_gray_lands_at_the_wheel_center()
    print()
    print("All volumetric_scope tests passed.")
