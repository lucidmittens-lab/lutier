"""Tests for segmentation.py -- run directly:
python3 -m tests.test_segmentation"""
import numpy as np

import segmentation as sg


def _two_region_image(size=20):
    """Left half solid red, right half solid blue -- same shape as
    test_keyer.py's own _half_red_half_blue, reused here for the same
    reason (a clean two-region test image with a real seam to detect
    connectivity boundaries against)."""
    image = np.zeros((size, size, 3), dtype=np.float64)
    image[:, :size // 2] = (0.8, 0.1, 0.1)
    image[:, size // 2:] = (0.1, 0.1, 0.8)
    return image


def test_empty_points_returns_an_all_zero_mask():
    image = _two_region_image()
    mask = sg.compute_mask_from_points(image, [])
    assert mask.shape == (20, 20)
    assert np.all(mask == 0.0)
    print("compute_mask_from_points: no seed points -> an all-zero mask, "
          "not an error -- OK")


def test_a_seed_in_the_red_region_selects_only_the_red_region():
    image = _two_region_image()
    mask = sg.compute_mask_from_points(image, [{"x": 0.25, "y": 0.5}])
    # The seed's own pixel should be selected at full strength.
    assert mask[10, 5] > 0.99
    # The blue region should be entirely untouched -- separate color AND
    # not connected to the seed's own region.
    assert np.all(mask[:, 15:] < 1e-6)
    print("compute_mask_from_points: a seed in one region selects only "
          "that connected, similarly-colored region -- OK")


def test_a_disconnected_same_color_blob_is_not_selected():
    """The whole point of region GROWING (not a plain global color
    threshold): two same-colored but disconnected blobs, seeding one
    must not also select the other."""
    image = np.zeros((20, 20, 3), dtype=np.float64)
    image[:5, :5] = (0.8, 0.1, 0.1)     # top-left red blob
    image[-5:, -5:] = (0.8, 0.1, 0.1)   # bottom-right red blob, disconnected
    mask = sg.compute_mask_from_points(image, [{"x": 0.1, "y": 0.1}])
    assert mask[2, 2] > 0.99            # the seeded (top-left) blob is selected
    assert np.all(mask[-5:, -5:] < 1e-6)  # the disconnected same-color blob is NOT
    print("compute_mask_from_points: a disconnected blob of the exact "
          "same color is NOT selected -- region growing, not a global "
          "color threshold -- OK")


def test_multiple_seeds_union_their_own_regions():
    image = _two_region_image()
    mask = sg.compute_mask_from_points(
        image, [{"x": 0.25, "y": 0.5}, {"x": 0.75, "y": 0.5}])
    assert mask[10, 5] > 0.99   # red region
    assert mask[10, 15] > 0.99  # blue region
    print("compute_mask_from_points: multiple seed points union their "
          "own regions -- OK")


def test_tolerance_controls_how_far_the_region_grows():
    """A gradient image where a tight tolerance should stop the region
    from growing very far, and a loose one should let it grow further."""
    size = 40
    image = np.zeros((size, size, 3), dtype=np.float64)
    gradient = np.linspace(0.0, 1.0, size)
    image[:, :, 0] = gradient[np.newaxis, :]  # red channel ramps left->right
    tight = sg.compute_mask_from_points(
        image, [{"x": 0.0, "y": 0.5}], tolerance=0.05, feather=0.0)
    loose = sg.compute_mask_from_points(
        image, [{"x": 0.0, "y": 0.5}], tolerance=0.5, feather=0.0)
    tight_extent = np.sum(tight[size // 2] > 0.5)
    loose_extent = np.sum(loose[size // 2] > 0.5)
    assert loose_extent > tight_extent
    print("compute_mask_from_points: a looser tolerance grows the "
          "selected region further along a gradient -- OK")


def test_feather_softens_the_edge():
    size = 40
    image = np.zeros((size, size, 3), dtype=np.float64)
    gradient = np.linspace(0.0, 1.0, size)
    image[:, :, 0] = gradient[np.newaxis, :]
    hard = sg.compute_mask_from_points(
        image, [{"x": 0.0, "y": 0.5}], tolerance=0.3, feather=0.0)
    soft = sg.compute_mask_from_points(
        image, [{"x": 0.0, "y": 0.5}], tolerance=0.3, feather=1.0)
    # feather=0 (dropoff=1) is a near-flat PLATEAU that stays close to
    # 1.0 almost all the way to the tolerance boundary before cutting
    # off sharply; feather=1 (dropoff=0, a plain Gaussian) decays
    # gradually from the seed's own pixel onward -- so the two curves
    # are NOT simply "higher vs lower" at any one fixed distance (they
    # actually cross near the boundary itself, same property
    # color_warper.falloff_weight's own docstring describes), but the
    # SOFT curve's own gradual-transition BAND (values strictly between
    # 0.05 and 0.95) should span more pixels than the hard curve's much
    # narrower plateau-then-cliff transition.
    def _transition_width(mask, row):
        band = mask[row]
        return int(np.sum((band > 0.05) & (band < 0.95)))

    hard_width = _transition_width(hard, size // 2)
    soft_width = _transition_width(soft, size // 2)
    assert soft_width > hard_width, (soft_width, hard_width)
    print("compute_mask_from_points: higher feather widens the gradual "
          "transition band vs feather=0's near-plateau-then-cliff shape "
          "-- OK")


def test_invert_flips_the_mask():
    image = _two_region_image()
    plain = sg.compute_mask_from_points(image, [{"x": 0.25, "y": 0.5}])
    inverted = sg.compute_mask_from_points(image, [{"x": 0.25, "y": 0.5}], invert=True)
    assert np.allclose(plain + inverted, 1.0, atol=1e-9)
    print("compute_mask_from_points: invert=True flips the mask "
          "(1 - mask) -- OK")


def test_output_is_always_in_unit_range():
    rng = np.random.default_rng(0)
    image = rng.uniform(-0.3, 1.3, size=(16, 16, 3))  # includes out-of-[0,1] values
    mask = sg.compute_mask_from_points(
        image, [{"x": 0.5, "y": 0.5}, {"x": 0.1, "y": 0.9}])
    assert mask.shape == (16, 16)
    assert np.all(mask >= 0.0) and np.all(mask <= 1.0)
    print("compute_mask_from_points: output is always clamped to "
          "[0,1] -- OK")


if __name__ == "__main__":
    test_empty_points_returns_an_all_zero_mask()
    test_a_seed_in_the_red_region_selects_only_the_red_region()
    test_a_disconnected_same_color_blob_is_not_selected()
    test_multiple_seeds_union_their_own_regions()
    test_tolerance_controls_how_far_the_region_grows()
    test_feather_softens_the_edge()
    test_invert_flips_the_mask()
    test_output_is_always_in_unit_range()
    print()
    print("All segmentation tests passed.")
