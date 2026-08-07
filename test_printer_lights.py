"""Tests for printer_lights.py -- run directly: python3 test_printer_lights.py"""
from pathlib import Path

import numpy as np
import colour

from printer_lights import (
    point_gain, apply_printer_lights, apply_printer_lights_in_colourspace,
    apply_printer_lights_in_working_space,
    bake_printer_lights_on_lut, LOG_INCREMENT_PER_POINT, POINTS_PER_STOP,
    DEFAULT_WORKING_COLOURSPACE,
)
from colorspace import lookup_colourspace
import aces_drt

SAMPLE_LUT_PATH = str(Path(__file__).parent / "samples" / "roundtrip_check.cube")

# A linear working space -- cctf_decoding/encoding are both the identity
# function, so apply_printer_lights_in_colourspace should reduce exactly
# to the plain linear-domain apply_printer_lights for these. Present under
# the same name in both the colour-science and OCIO ACES registries, so
# it's safe to use in tests that exercise either backend.
LINEAR_SPACE = "ACEScg"

# bake_printer_lights_on_lut now routes through the OCIO-preferring
# apply_printer_lights_in_working_space (see printer_lights.py's docstring),
# whose registry names an sRGB-gamma working space differently than
# colour-science's own plain "sRGB" -- so tests that exercise bake_* with a
# gamma-encoded space need whichever name is actually valid for the active
# backend. Tests that call apply_printer_lights_in_colourspace directly
# (always colour-science, never OCIO) should keep using plain "sRGB".
SRGB_LIKE_SPACE = "sRGB Encoded Rec.709 (sRGB)" if aces_drt.OCIO_AVAILABLE else "sRGB"


def test_point_gain_zero_is_unity():
    assert point_gain(0) == 1.0
    print("point_gain(0) == 1.0 -- OK")


def test_points_per_stop_is_about_twelve():
    assert 11.5 < POINTS_PER_STOP < 12.5
    print(f"POINTS_PER_STOP ~= {POINTS_PER_STOP:.2f} (expected ~12) -- OK")


def test_point_gain_twelve_points_is_about_one_stop():
    gain = point_gain(POINTS_PER_STOP)
    assert abs(gain - 2.0) < 1e-6, "POINTS_PER_STOP points should be EXACTLY one stop by construction"
    print(f"point_gain(POINTS_PER_STOP) = {gain:.6f} (should be 2.0 exactly) -- OK")

    gain_12 = point_gain(12)
    assert abs(gain_12 - 2.0) < 0.01, "12 points (the rounded convention) should be close to a stop"
    print(f"point_gain(12) = {gain_12:.4f} (~2.0, the rounded 12-points-per-stop convention) -- OK")


def test_point_gain_negative_is_reciprocal_of_positive():
    assert abs(point_gain(10) * point_gain(-10) - 1.0) < 1e-9
    print("point_gain(+10) * point_gain(-10) == 1.0 -- OK")


def test_apply_printer_lights_zero_points_is_noop():
    rng = np.random.default_rng(0)
    img = rng.random((10, 10, 3))
    out = apply_printer_lights(img, 0, 0, 0)
    assert np.array_equal(out, img)
    print("apply_printer_lights with all-zero points is an exact no-op -- OK")


def test_apply_printer_lights_isolates_channels():
    img = np.full((3, 3, 3), 0.2)
    out = apply_printer_lights(img, r_points=12, g_points=0, b_points=-12)
    assert np.allclose(out[..., 0], 0.2 * point_gain(12))
    assert np.allclose(out[..., 1], 0.2)  # unchanged
    assert np.allclose(out[..., 2], 0.2 * point_gain(-12))
    assert out[..., 0].mean() > out[..., 1].mean() > out[..., 2].mean()
    print("apply_printer_lights: R/G/B channels adjusted independently, "
          "brighter points -> brighter channel -- OK")


def test_apply_printer_lights_no_clipping():
    img = np.full((2, 2, 3), 0.9)
    out = apply_printer_lights(img, r_points=50, g_points=0, b_points=0)
    assert out[..., 0].max() > 1.0, "large positive points should be allowed to exceed 1.0, not clip"
    print("apply_printer_lights: no clipping, legitimate out-of-range values preserved -- OK")


# --- color-space awareness ----------------------------------------------

def test_in_colourspace_matches_plain_gain_for_a_linear_space():
    """A linear working space's cctf_decoding/encoding are both the
    identity function, so going through apply_printer_lights_in_colourspace
    should give EXACTLY the same result as the plain linear-domain
    apply_printer_lights -- confirms the new colourspace-aware path is a
    strict superset, not a behavior change, for the linear case."""
    rng = np.random.default_rng(1)
    img = rng.random((8, 8, 3)).astype(np.float64)
    plain = apply_printer_lights(img, r_points=10, g_points=-6, b_points=3)
    aware = apply_printer_lights_in_colourspace(
        img, LINEAR_SPACE, r_points=10, g_points=-6, b_points=3)
    assert np.allclose(plain, aware, atol=1e-9)
    print(f"apply_printer_lights_in_colourspace({LINEAR_SPACE!r}) matches "
          "plain apply_printer_lights exactly, as expected for a linear space -- OK")


def test_in_colourspace_zero_points_is_noop_for_every_curve_type():
    """Zero points should round-trip to (essentially) the same value
    regardless of whether the working space is log-encoded (ACEScct),
    gamma-encoded (sRGB), or linear (ACEScg) -- cctf_encoding/cctf_decoding
    are meant to be exact inverses of one another."""
    rng = np.random.default_rng(2)
    img = rng.random((6, 6, 3)).astype(np.float64) * 0.9 + 0.05  # avoid the extreme edges
    for space in ("ACEScct", "sRGB", LINEAR_SPACE):
        out = apply_printer_lights_in_colourspace(img, space, 0, 0, 0)
        assert np.allclose(out, img, atol=1e-6), f"{space} round-trip should be a no-op"
        print(f"apply_printer_lights_in_colourspace({space!r}): zero points "
              "round-trips to essentially the same value -- OK")


def test_in_colourspace_log_space_uses_additive_stop_shift_not_multiplicative():
    """The whole point of this feature: for a LOG-encoded space, +1 stop
    should be a roughly CONSTANT additive code-value shift (ACEScct's own
    documented 1/17.52 per stop), not a multiplicative one -- confirming
    the decode/gain/re-encode path actually behaves differently (and
    correctly) versus naively multiplying the raw code value, which is
    what the old, non-colourspace-aware implementation did."""
    cs = lookup_colourspace("ACEScct")
    stop_points = POINTS_PER_STOP

    code_low = 0.3
    code_high = 0.6  # different starting code value than code_low
    out_low = apply_printer_lights_in_colourspace(
        np.array([code_low] * 3), "ACEScct", stop_points, stop_points, stop_points)
    out_high = apply_printer_lights_in_colourspace(
        np.array([code_high] * 3), "ACEScct", stop_points, stop_points, stop_points)

    delta_low = out_low[0] - code_low
    delta_high = out_high[0] - code_high
    expected_delta = 1.0 / 17.52  # ACEScct's documented code-values-per-stop constant

    assert abs(delta_low - expected_delta) < 1e-6
    assert abs(delta_high - expected_delta) < 1e-6
    assert abs(delta_low - delta_high) < 1e-9, \
        "additive shift should be the same constant regardless of starting code value"
    print(f"ACEScct +1 stop: additive code-value shift is {delta_low:.6f} at both "
          f"code {code_low} and {code_high} (expected ~{expected_delta:.6f}) -- OK")

    # And confirm this is NOT what naive multiplication would have given --
    # the two starting points would get DIFFERENT deltas under a plain gain,
    # which is exactly the bug this feature fixes.
    naive_low = code_low * point_gain(stop_points) - code_low
    naive_high = code_high * point_gain(stop_points) - code_high
    assert abs(naive_low - naive_high) > 1e-3, \
        "sanity check: naive multiplicative deltas should actually differ by starting point"
    print("Confirmed naive multiplication on raw code values would have given "
          f"different deltas ({naive_low:.4f} vs {naive_high:.4f}) at those two "
          "points -- the additive/correct behavior above is a real fix, not a no-op change.")


def test_in_colourspace_different_spaces_give_different_results():
    """Same raw code values, same points, different working color space
    declared -- should give a different result, since the space
    determines how those code values get linearized before the gain is
    applied. Mirrors aces_drt.py's equivalent "the space selection
    actually matters" test."""
    img = np.full((2, 2, 3), 0.5)
    out_aces = apply_printer_lights_in_colourspace(img, "ACEScct", 12, 12, 12)
    out_srgb = apply_printer_lights_in_colourspace(img, "sRGB", 12, 12, 12)
    assert not np.allclose(out_aces, out_srgb), \
        "same code values under different working-space assumptions should diverge"
    print("apply_printer_lights_in_colourspace: ACEScct vs sRGB give different "
          "results for identical code values/points, as expected -- OK")


# --- bake_printer_lights_on_lut ------------------------------------------

def test_bake_zero_points_reproduces_source():
    lut = colour.read_LUT(SAMPLE_LUT_PATH)
    size = 9
    baked = bake_printer_lights_on_lut(lut, 0, 0, 0, size=size)
    identity_grid = colour.LUT3D(size=size).table
    direct = lut.apply(identity_grid)
    assert np.allclose(baked.table, direct, atol=1e-6)
    print("bake_printer_lights_on_lut: zero points exactly reproduces the "
          "source LUT (default working color space) -- OK")


def test_bake_zero_points_reproduces_source_for_other_working_spaces():
    lut = colour.read_LUT(SAMPLE_LUT_PATH)
    size = 9
    identity_grid = colour.LUT3D(size=size).table
    direct = lut.apply(identity_grid)
    for space in (SRGB_LIKE_SPACE, LINEAR_SPACE):
        baked = bake_printer_lights_on_lut(lut, 0, 0, 0, working_colourspace=space, size=size)
        assert np.allclose(baked.table, direct, atol=1e-6), space
        print(f"bake_printer_lights_on_lut: zero points reproduces source "
              f"under working_colourspace={space!r} too -- OK")


def test_bake_applies_after_source_lut_not_before():
    """Printer lights should trim the OUTPUT of the source LUT, not its
    input -- confirms composition order by using a LUT whose output isn't
    just the identity, so "trim before" vs "trim after" give different
    results, and checking against the "after" calculation explicitly.
    Uses the linear working space so the expected value can be computed
    with the plain apply_printer_lights, keeping this test focused on
    ORDER rather than color-space math (that's covered separately above)."""
    lut = colour.read_LUT(SAMPLE_LUT_PATH)
    size = 9
    r_points, g_points, b_points = 8, -4, 2
    baked = bake_printer_lights_on_lut(
        lut, r_points, g_points, b_points,
        working_colourspace=LINEAR_SPACE, size=size)

    identity_grid = colour.LUT3D(size=size).table
    lut_output = lut.apply(identity_grid)
    expected = apply_printer_lights(lut_output, r_points, g_points, b_points)
    assert np.allclose(baked.table, expected, atol=1e-9)

    # And confirm it's NOT the same as trimming before (order actually matters)
    trimmed_input = apply_printer_lights(identity_grid, r_points, g_points, b_points)
    wrong_order = lut.apply(trimmed_input)
    assert not np.allclose(baked.table, wrong_order, atol=1e-6), \
        "trim-after and trim-before should differ for a non-trivial LUT"
    print("bake_printer_lights_on_lut: trims the source LUT's OUTPUT (after), "
          "confirmed distinct from trimming its input -- OK")


def test_bake_default_working_colourspace_is_acescct():
    assert DEFAULT_WORKING_COLOURSPACE == "ACEScct"
    lut = colour.read_LUT(SAMPLE_LUT_PATH)
    size = 9
    default_result = bake_printer_lights_on_lut(lut, 10, 10, 10, size=size)
    explicit_result = bake_printer_lights_on_lut(
        lut, 10, 10, 10, working_colourspace="ACEScct", size=size)
    assert np.array_equal(default_result.table, explicit_result.table)
    print("bake_printer_lights_on_lut: default working_colourspace is "
          "ACEScct, confirmed by matching an explicit ACEScct call -- OK")


def test_bake_different_working_colourspace_gives_different_result():
    lut = colour.read_LUT(SAMPLE_LUT_PATH)
    size = 9
    baked_acescct = bake_printer_lights_on_lut(
        lut, 15, 15, 15, working_colourspace="ACEScct", size=size)
    baked_srgb = bake_printer_lights_on_lut(
        lut, 15, 15, 15, working_colourspace=SRGB_LIKE_SPACE, size=size)
    assert not np.allclose(baked_acescct.table, baked_srgb.table, atol=1e-4)
    print("bake_printer_lights_on_lut: ACEScct vs sRGB-gamma working color "
          "space produce different baked results for the same points -- OK")


def test_bake_default_size_matches_source():
    lut = colour.read_LUT(SAMPLE_LUT_PATH)
    baked = bake_printer_lights_on_lut(lut, 5, 5, 5)
    assert baked.table.shape[0] == lut.table.shape[0]
    print("bake_printer_lights_on_lut: default size matches source LUT resolution -- OK")


# --- OCIO-preferring dispatch (apply_printer_lights_in_working_space) ---

def test_working_space_dispatch_prefers_ocio_when_available():
    """apply_printer_lights_in_working_space should route through
    aces_drt.decode_to_linear/encode_from_linear when OCIO is installed --
    confirmed by feeding it an OCIO-only, disambiguated working space name
    ('CanonLog2 CinemaGamut D55') that doesn't exist at all in
    colour-science's own registry. If this were silently using the
    colour-science path instead, colorspace.py's lookup_colourspace would
    raise a KeyError for that name -- getting a plausible result back
    proves the OCIO registry is the one actually being used."""
    if not aces_drt.OCIO_AVAILABLE:
        print("SKIPPED test_working_space_dispatch_prefers_ocio_when_available -- "
              "PyOpenColorIO not installed")
        return
    img = np.full((2, 2, 3), 0.5, dtype=np.float32)
    out = apply_printer_lights_in_working_space(
        img, "CanonLog2 CinemaGamut D55", 12, 0, 0)
    assert out.shape == (2, 2, 3)
    assert not np.allclose(out, img)
    print("apply_printer_lights_in_working_space: correctly dispatched an "
          "OCIO-only working space name ('CanonLog2 CinemaGamut D55') through "
          "the OCIO registry -- confirms it isn't silently falling back to "
          "colour-science's own (ambiguous, and here incompatible) registry -- OK")


def test_working_space_dispatch_falls_back_without_ocio():
    """When OCIO isn't available, apply_printer_lights_in_working_space
    should fall back to the plain colour-science path and give an IDENTICAL
    result to calling apply_printer_lights_in_colourspace directly.
    Verified by monkeypatching aces_drt.OCIO_AVAILABLE off for the duration
    of this test (restored in a finally block) rather than depending on
    whatever happens to be installed in the test environment -- so this
    test is meaningful regardless of whether OCIO is actually present."""
    original = aces_drt.OCIO_AVAILABLE
    aces_drt.OCIO_AVAILABLE = False
    try:
        img = np.full((2, 2, 3), 0.4, dtype=np.float64)
        via_dispatch = apply_printer_lights_in_working_space(img, "ACEScct", 8, -4, 2)
        via_direct = apply_printer_lights_in_colourspace(img, "ACEScct", 8, -4, 2)
        assert np.allclose(via_dispatch, via_direct, atol=1e-9)
        print("apply_printer_lights_in_working_space: with OCIO unavailable "
              "(monkeypatched off), falls back to apply_printer_lights_in_colourspace "
              "exactly -- OK")
    finally:
        aces_drt.OCIO_AVAILABLE = original


if __name__ == "__main__":
    test_point_gain_zero_is_unity()
    test_points_per_stop_is_about_twelve()
    test_point_gain_twelve_points_is_about_one_stop()
    test_point_gain_negative_is_reciprocal_of_positive()
    test_apply_printer_lights_zero_points_is_noop()
    test_apply_printer_lights_isolates_channels()
    test_apply_printer_lights_no_clipping()
    test_in_colourspace_matches_plain_gain_for_a_linear_space()
    test_in_colourspace_zero_points_is_noop_for_every_curve_type()
    test_in_colourspace_log_space_uses_additive_stop_shift_not_multiplicative()
    test_in_colourspace_different_spaces_give_different_results()
    test_bake_zero_points_reproduces_source()
    test_bake_zero_points_reproduces_source_for_other_working_spaces()
    test_bake_applies_after_source_lut_not_before()
    test_bake_default_working_colourspace_is_acescct()
    test_bake_different_working_colourspace_gives_different_result()
    test_bake_default_size_matches_source()
    test_working_space_dispatch_prefers_ocio_when_available()
    test_working_space_dispatch_falls_back_without_ocio()
    print()
    print("All printer_lights tests passed.")
