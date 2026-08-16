"""Tests for keyer.py -- run directly:
python3 test_keyer.py"""
import numpy as np

import keyer


def _solid(rgb, size=4):
    return np.tile(np.array(rgb, dtype=np.float64), (size, size, 1))


def test_rgb_to_hsl_matches_colorsys_on_known_colors():
    import colorsys
    samples = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0),
               (1.0, 1.0, 1.0), (0.0, 0.0, 0.0), (0.5, 0.5, 0.5),
               (0.8, 0.3, 0.6), (0.2, 0.7, 0.7)]
    rgb = np.array(samples).reshape(2, 4, 3)
    h, s, l = keyer._rgb_to_hsl(rgb)
    for i, (r, g, b) in enumerate(samples):
        yy, xx = divmod(i, 4)
        expected_h, expected_l, expected_s = colorsys.rgb_to_hls(r, g, b)
        assert np.isclose(h[yy, xx], expected_h * 360.0, atol=1e-6)
        assert np.isclose(s[yy, xx], expected_s, atol=1e-6)
        assert np.isclose(l[yy, xx], expected_l, atol=1e-6)
    print("_rgb_to_hsl: matches colorsys.rgb_to_hls on known colors -- OK")


def test_compute_matte_keys_near_one_for_a_matching_saturated_color():
    red = _solid((1.0, 0.0, 0.0))
    matte = keyer.compute_matte(red, {"hue_center": 0.0, "hue_width": 30.0})
    assert np.allclose(matte, 1.0, atol=1e-6)
    print("compute_matte: a saturated red pixel keys ~1.0 under a "
          "red-centered qualifier -- OK")


def test_compute_matte_keys_near_zero_for_a_far_off_hue():
    red = _solid((1.0, 0.0, 0.0))
    matte = keyer.compute_matte(red, {"hue_center": 240.0, "hue_width": 30.0})
    assert np.all(matte < 0.05)
    print("compute_matte: a saturated red pixel keys ~0.0 under a "
          "blue-centered qualifier -- OK")


def test_compute_matte_softness_widens_the_falloff():
    red = _solid((1.0, 0.0, 0.0))
    hard = keyer.compute_matte(red, {"hue_center": 60.0, "hue_width": 30.0, "softness": 0.0})
    soft = keyer.compute_matte(red, {"hue_center": 60.0, "hue_width": 30.0, "softness": 1.0})
    assert soft[0, 0] > hard[0, 0]
    print("compute_matte: higher softness widens/softens the falloff, "
          "raising the matte value at a fixed off-center hue -- OK")


def test_compute_matte_saturation_and_luminance_bands_gate_output():
    gray = _solid((0.5, 0.5, 0.5))  # s=0, l=0.5
    # Gray has zero saturation -- a qualifier demanding sat in [0.8, 1.0]
    # should key it near zero regardless of hue.
    matte = keyer.compute_matte(gray, {"sat_min": 0.8, "sat_max": 1.0, "softness": 0.1})
    assert np.all(matte < 0.05)
    # Same gray with a sat band that includes 0 should pass fully.
    matte2 = keyer.compute_matte(gray, {"sat_min": 0.0, "sat_max": 0.2, "softness": 0.1})
    assert np.all(matte2 > 0.9)
    print("compute_matte: saturation/luminance band terms gate the "
          "output as expected -- OK")


def test_compute_matte_invert_flips_the_result():
    red = _solid((1.0, 0.0, 0.0))
    params = {"hue_center": 0.0, "hue_width": 30.0}
    plain = keyer.compute_matte(red, params)
    inverted = keyer.compute_matte(red, {**params, "invert": True})
    assert np.allclose(plain + inverted, 1.0, atol=1e-6)
    print("compute_matte: invert=True flips the matte (1 - matte) -- OK")


def test_compute_matte_output_is_always_in_unit_range():
    rng = np.random.default_rng(0)
    image = rng.uniform(-0.5, 1.5, size=(16, 16, 3))  # includes out-of-range values
    matte = keyer.compute_matte(image, {"softness": 0.7})
    assert matte.shape == (16, 16)
    assert np.all(matte >= 0.0) and np.all(matte <= 1.0)
    print("compute_matte: output is always clamped to [0,1], even for "
          "out-of-[0,1]-range input -- OK")


if __name__ == "__main__":
    test_rgb_to_hsl_matches_colorsys_on_known_colors()
    test_compute_matte_keys_near_one_for_a_matching_saturated_color()
    test_compute_matte_keys_near_zero_for_a_far_off_hue()
    test_compute_matte_softness_widens_the_falloff()
    test_compute_matte_saturation_and_luminance_bands_gate_output()
    test_compute_matte_invert_flips_the_result()
    test_compute_matte_output_is_always_in_unit_range()
    print()
    print("All keyer tests passed.")
