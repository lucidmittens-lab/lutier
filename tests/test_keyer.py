"""Tests for keyer.py -- run directly:
python3 -m tests.test_keyer"""
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


def test_compute_matte_prefilter_blur_defaults_to_a_noop():
    red = _solid((1.0, 0.0, 0.0))
    params = {"working_colourspace": SPACE, "hue_center": RED_HUE, "hue_width": 30.0}
    default = keyer.compute_matte(red, params)
    explicit_zero = keyer.compute_matte(red, {**params, "prefilter_blur": 0.0})
    assert np.allclose(default, explicit_zero)
    print("compute_matte: prefilter_blur=0.0 (the default) is an exact "
          "no-op -- OK")


def test_compute_matte_prefilter_blur_softens_a_hard_edge():
    size = 16
    image = np.zeros((size, size, 3), dtype=np.float64)
    image[:, :size // 2] = (1.0, 0.0, 0.0)  # left half red
    image[:, size // 2:] = (0.0, 0.0, 1.0)  # right half blue
    params = {"working_colourspace": SPACE, "hue_center": RED_HUE, "hue_width": 30.0, "softness": 0.0}
    sharp = keyer.compute_matte(image, params)
    blurred = keyer.compute_matte(image, {**params, "prefilter_blur": 2.0})
    row = size // 2
    # A hard qualifier (softness=0) on an unblurred hard edge produces a
    # strictly binary matte -- every pixel reads exactly 0 or 1.
    assert np.all((sharp[row] == 0.0) | (sharp[row] == 1.0))
    # Blurring image_array before the OKLCh decode mixes red and blue
    # into each other near the edge, producing genuinely intermediate
    # matte values the unblurred pass never has.
    assert np.any((blurred[row] > 0.01) & (blurred[row] < 0.99))
    print("compute_matte: prefilter_blur softens a hard color edge by "
          "blurring image_array before the OKLCh decode -- OK")


def test_compute_matte_matte_blur_defaults_to_a_noop():
    red = _solid((1.0, 0.0, 0.0))
    params = {"working_colourspace": SPACE, "hue_center": RED_HUE, "hue_width": 30.0}
    default = keyer.compute_matte(red, params)
    explicit_zero = keyer.compute_matte(red, {**params, "matte_blur": 0.0})
    assert np.allclose(default, explicit_zero)
    print("compute_matte: matte_blur=0.0 (the default) is an exact "
          "no-op -- OK")


def test_compute_matte_matte_blur_softens_the_mattes_own_edge():
    """matte_blur operates on the ALREADY-COMPUTED matte -- distinct
    from prefilter_blur, which blurs image_array before the OKLCh decode
    (see module docstring for why Resolve splits these into two
    separate controls: source noise-reduction vs. matte edge feather)."""
    size = 16
    image = np.zeros((size, size, 3), dtype=np.float64)
    image[:, :size // 2] = (1.0, 0.0, 0.0)  # left half: matches the qualifier
    image[:, size // 2:] = (0.0, 0.0, 1.0)  # right half: doesn't
    params = {"working_colourspace": SPACE, "hue_center": RED_HUE, "hue_width": 30.0, "softness": 0.0}
    sharp = keyer.compute_matte(image, params)
    blurred = keyer.compute_matte(image, {**params, "matte_blur": 2.0})
    row = size // 2
    assert np.all((sharp[row] == 0.0) | (sharp[row] == 1.0))
    assert np.any((blurred[row] > 0.01) & (blurred[row] < 0.99))
    print("compute_matte: matte_blur softens the computed matte's own "
          "edges directly -- OK")


def test_compute_matte_matte_blur_and_prefilter_blur_are_independent():
    size = 16
    image = np.zeros((size, size, 3), dtype=np.float64)
    image[:, :size // 2] = (1.0, 0.0, 0.0)
    image[:, size // 2:] = (0.0, 0.0, 1.0)
    params = {"working_colourspace": SPACE, "hue_center": RED_HUE, "hue_width": 30.0, "softness": 0.0}
    matte_blur_only = keyer.compute_matte(image, {**params, "matte_blur": 2.0})
    prefilter_only = keyer.compute_matte(image, {**params, "prefilter_blur": 2.0})
    both = keyer.compute_matte(image, {**params, "matte_blur": 2.0, "prefilter_blur": 2.0})
    row = size // 2
    assert not np.allclose(both[row], matte_blur_only[row])
    assert not np.allclose(both[row], prefilter_only[row])
    print("compute_matte: matte_blur and prefilter_blur act independently "
          "and combine -- OK")


def test_compute_matte_matte_blur_applies_before_clip():
    size = 16
    image = np.zeros((size, size, 3), dtype=np.float64)
    image[:, :size // 2] = (1.0, 0.0, 0.0)
    image[:, size // 2:] = (0.0, 0.0, 1.0)
    params = {"working_colourspace": SPACE, "hue_center": RED_HUE, "hue_width": 30.0,
              "softness": 0.0, "matte_blur": 2.0}
    blurred = keyer.compute_matte(image, params)
    row = size // 2
    assert np.any((blurred[row] > 0.01) & (blurred[row] < 0.99)), \
        "sanity: matte_blur must actually soften something before clip can re-harden it"
    # A tight clean black/white band re-hardens the blurred edge back to
    # binary, proving clip runs AFTER matte_blur (matching Resolve's own
    # Matte Finesse ordering: Blur Radius above Clean Black/White).
    cleaned = keyer.compute_matte(image, {**params, "clip_black": 0.45, "clip_white": 0.55})
    assert np.all((cleaned[row] == 0.0) | (cleaned[row] == 1.0))
    print("compute_matte: matte_blur runs BEFORE clip_black/clip_white, "
          "so cleanup can re-harden a blurred edge -- OK")


def test_compute_matte_clip_defaults_are_a_noop():
    red = _solid((1.0, 0.0, 0.0))
    params = {"working_colourspace": SPACE, "hue_center": RED_HUE + 40.0, "hue_width": 30.0, "softness": 0.5}
    default = keyer.compute_matte(red, params)
    explicit = keyer.compute_matte(red, {**params, "clip_black": 0.0, "clip_white": 1.0})
    assert np.allclose(default, explicit)
    print("compute_matte: clip_black=0.0/clip_white=1.0 (the defaults) "
          "are an exact identity remap -- OK")


def test_compute_matte_clip_black_zeroes_out_below_threshold():
    red = _solid((1.0, 0.0, 0.0))
    params = {"working_colourspace": SPACE, "hue_center": RED_HUE + 40.0, "hue_width": 30.0, "softness": 1.0}
    plain = keyer.compute_matte(red, params)
    assert 0.0 < plain[0, 0] < 1.0  # a genuinely partial/soft value, not saturated
    clipped = keyer.compute_matte(red, {**params, "clip_black": plain[0, 0] + 0.05})
    assert np.all(clipped < 1e-6)
    print("compute_matte: clip_black clamps everything at/below it to "
          "0 -- OK")


def test_compute_matte_clip_white_saturates_to_one_above_threshold():
    red = _solid((1.0, 0.0, 0.0))
    params = {"working_colourspace": SPACE, "hue_center": RED_HUE + 40.0, "hue_width": 30.0, "softness": 1.0}
    plain = keyer.compute_matte(red, params)
    assert 0.0 < plain[0, 0] < 1.0
    clipped = keyer.compute_matte(red, {**params, "clip_white": plain[0, 0] - 0.05})
    assert np.allclose(clipped, 1.0, atol=1e-6)
    print("compute_matte: clip_white clamps everything at/above it to "
          "1 -- OK")


def test_compute_matte_clip_applies_before_invert():
    red = _solid((1.0, 0.0, 0.0))
    params = {"working_colourspace": SPACE, "hue_center": RED_HUE + 40.0, "hue_width": 30.0, "softness": 1.0}
    plain = keyer.compute_matte(red, params)
    clip_black = plain[0, 0] + 0.05
    clipped = keyer.compute_matte(red, {**params, "clip_black": clip_black})
    inverted = keyer.compute_matte(red, {**params, "clip_black": clip_black, "invert": True})
    assert np.allclose(clipped, 0.0, atol=1e-6)
    assert np.allclose(inverted, 1.0, atol=1e-6)
    print("compute_matte: clip_black/clip_white apply BEFORE invert, so "
          "invert always flips the already-cleaned key -- OK")


if __name__ == "__main__":
    test_compute_matte_keys_near_one_for_a_matching_hue()
    test_compute_matte_keys_near_zero_for_a_far_off_hue()
    test_compute_matte_softness_widens_the_falloff()
    test_compute_matte_chroma_and_lightness_bands_gate_output()
    test_compute_matte_invert_flips_the_result()
    test_compute_matte_output_is_always_in_unit_range()
    test_compute_matte_defaults_to_declared_default_working_colourspace()
    test_compute_matte_prefilter_blur_defaults_to_a_noop()
    test_compute_matte_prefilter_blur_softens_a_hard_edge()
    test_compute_matte_matte_blur_defaults_to_a_noop()
    test_compute_matte_matte_blur_softens_the_mattes_own_edge()
    test_compute_matte_matte_blur_and_prefilter_blur_are_independent()
    test_compute_matte_matte_blur_applies_before_clip()
    test_compute_matte_clip_defaults_are_a_noop()
    test_compute_matte_clip_black_zeroes_out_below_threshold()
    test_compute_matte_clip_white_saturates_to_one_above_threshold()
    test_compute_matte_clip_applies_before_invert()
    print()
    print("All keyer tests passed.")
