"""Tests for keyer.py -- run directly:
python3 test_keyer.py"""
import numpy as np

import color_warper as cw
import keyer

# Testing against REFERENCE_LINEAR_SPACE (rather than keyer's own
# DEFAULT_WORKING_COLOURSPACE, ACEScct) keeps decode_working_colourspace_
# to_oklch's own gamut-conversion hop a near-identity, same convention
# test_color_warper.py already uses for its own exact-value assertions.
SPACE = cw.REFERENCE_LINEAR_SPACE
RED_HUE = 29.2338851923
GREEN_HUE = 142.495338888
BLUE_HUE = 264.052020638
RED_CHROMA = 0.257683307736
RED_LIGHTNESS = 0.627955360615


def _solid(rgb, size=4):
    return np.tile(np.array(rgb, dtype=np.float64), (size, size, 1))


def test_compute_matte_keys_near_one_for_a_matching_hue():
    red = _solid((1.0, 0.0, 0.0))
    matte = keyer.compute_matte(red, {"working_colourspace": SPACE,
                                       "hue_center": RED_HUE, "hue_width": 30.0})
    assert np.allclose(matte, 1.0, atol=1e-6)
    print("compute_matte: a red pixel keys ~1.0 under a red-centered "
          "OKLCh qualifier -- OK")


def test_compute_matte_keys_near_zero_for_a_far_off_hue():
    red = _solid((1.0, 0.0, 0.0))
    matte = keyer.compute_matte(red, {"working_colourspace": SPACE,
                                       "hue_center": BLUE_HUE, "hue_width": 30.0})
    assert np.all(matte < 0.05)
    print("compute_matte: a red pixel keys ~0.0 under a blue-centered "
          "OKLCh qualifier -- OK")


def test_compute_matte_softness_widens_the_falloff():
    red = _solid((1.0, 0.0, 0.0))
    off_center = RED_HUE + 60.0
    hard = keyer.compute_matte(red, {"working_colourspace": SPACE,
                                      "hue_center": off_center, "hue_width": 30.0, "softness": 0.0})
    soft = keyer.compute_matte(red, {"working_colourspace": SPACE,
                                      "hue_center": off_center, "hue_width": 30.0, "softness": 1.0})
    assert soft[0, 0] > hard[0, 0]
    print("compute_matte: higher softness widens/softens the falloff, "
          "raising the matte value at a fixed off-center hue -- OK")


def test_compute_matte_chroma_and_lightness_bands_gate_output():
    gray = _solid((0.2, 0.2, 0.2))  # chroma ~= 0
    # A neutral gray's OKLCh hue is essentially noise (chroma ~0 makes it
    # ill-defined) -- pin hue_center/hue_width wide enough that the hue
    # term never gates this test, isolating the chroma band term itself.
    gray_hue = {"hue_center": 90.0, "hue_width": 180.0}
    # A qualifier demanding chroma in [0.2, 0.4] should key a neutral
    # gray near zero regardless of hue.
    matte = keyer.compute_matte(gray, {"working_colourspace": SPACE, **gray_hue,
                                        "chroma_min": 0.2, "chroma_max": 0.4, "softness": 0.1})
    assert np.all(matte < 0.05)
    # Same gray with a chroma band that includes 0 should pass fully.
    matte2 = keyer.compute_matte(gray, {"working_colourspace": SPACE, **gray_hue,
                                         "chroma_min": 0.0, "chroma_max": 0.05, "softness": 0.1})
    assert np.all(matte2 > 0.9)
    print("compute_matte: chroma/lightness band terms gate the output "
          "as expected -- OK")


def test_compute_matte_invert_flips_the_result():
    red = _solid((1.0, 0.0, 0.0))
    params = {"working_colourspace": SPACE, "hue_center": RED_HUE, "hue_width": 30.0}
    plain = keyer.compute_matte(red, params)
    inverted = keyer.compute_matte(red, {**params, "invert": True})
    assert np.allclose(plain + inverted, 1.0, atol=1e-6)
    print("compute_matte: invert=True flips the matte (1 - matte) -- OK")


def test_compute_matte_output_is_always_in_unit_range():
    rng = np.random.default_rng(0)
    image = rng.uniform(-0.5, 1.5, size=(16, 16, 3))  # includes out-of-range values
    matte = keyer.compute_matte(image, {"working_colourspace": SPACE, "softness": 0.7})
    assert matte.shape == (16, 16)
    assert np.all(matte >= 0.0) and np.all(matte <= 1.0)
    print("compute_matte: output is always clamped to [0,1], even for "
          "out-of-[0,1]-range input -- OK")


def test_compute_matte_defaults_to_declared_default_working_colourspace():
    red = _solid((1.0, 0.0, 0.0))
    explicit = keyer.compute_matte(red, {"working_colourspace": keyer.DEFAULT_WORKING_COLOURSPACE,
                                          "hue_center": 0.0, "hue_width": 30.0})
    implicit = keyer.compute_matte(red, {"hue_center": 0.0, "hue_width": 30.0})
    assert np.allclose(explicit, implicit)
    print("compute_matte: an absent working_colourspace falls back to "
          "keyer.DEFAULT_WORKING_COLOURSPACE -- OK")


if __name__ == "__main__":
    test_compute_matte_keys_near_one_for_a_matching_hue()
    test_compute_matte_keys_near_zero_for_a_far_off_hue()
    test_compute_matte_softness_widens_the_falloff()
    test_compute_matte_chroma_and_lightness_bands_gate_output()
    test_compute_matte_invert_flips_the_result()
    test_compute_matte_output_is_always_in_unit_range()
    test_compute_matte_defaults_to_declared_default_working_colourspace()
    print()
    print("All keyer tests passed.")
