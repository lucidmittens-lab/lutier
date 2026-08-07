"""Tests for cdl.py -- run directly: python3 test_cdl.py"""
import numpy as np

import cdl


def _skip(name):
    print(f"SKIPPED {name} -- PyOpenColorIO not installed")


# --- identity / no-op behavior --------------------------------------------

def test_identity_params_are_exact_noop_both_styles():
    if not cdl.OCIO_AVAILABLE:
        return _skip("test_identity_params_are_exact_noop_both_styles")
    rng = np.random.default_rng(0)
    img = rng.random((6, 6, 3)).astype(np.float32)
    for style in ("asc", "no_clip"):
        out = cdl.apply_cdl(img, style=style)
        assert np.allclose(out, img, atol=1e-6), style
        print(f"apply_cdl: identity slope/offset/power/sat is an exact "
              f"no-op under style={style!r} -- OK")


def test_bypassed_sop_and_sat_together_is_exact_noop():
    if not cdl.OCIO_AVAILABLE:
        return _skip("test_bypassed_sop_and_sat_together_is_exact_noop")
    rng = np.random.default_rng(1)
    img = rng.random((4, 4, 3)).astype(np.float32)
    out = cdl.apply_cdl(
        img, slope=(2.0, 1.5, 0.5), offset=(0.3, -0.2, 0.1), power=(1.5, 0.8, 1.2),
        sat=1.8, sop_enabled=False, sat_enabled=False,
    )
    assert np.allclose(out, img, atol=1e-6)
    print("apply_cdl: sop_enabled=False and sat_enabled=False together is an "
          "exact no-op regardless of the (unused) parameter values -- OK")


# --- SOP math --------------------------------------------------------------

def test_slope_scales_each_channel_independently():
    if not cdl.OCIO_AVAILABLE:
        return _skip("test_slope_scales_each_channel_independently")
    img = np.full((2, 2, 3), 0.2, dtype=np.float32)
    out = cdl.apply_cdl(img, slope=(2.0, 1.0, 0.5), style="no_clip")
    assert np.allclose(out[..., 0], 0.4, atol=1e-5)
    assert np.allclose(out[..., 1], 0.2, atol=1e-5)
    assert np.allclose(out[..., 2], 0.1, atol=1e-5)
    print("apply_cdl: slope scales R/G/B channels independently, matching "
          "out = in * slope for a zero-offset/unity-power case -- OK")


def test_offset_adds_a_constant():
    if not cdl.OCIO_AVAILABLE:
        return _skip("test_offset_adds_a_constant")
    img = np.full((2, 2, 3), 0.3, dtype=np.float32)
    out = cdl.apply_cdl(img, offset=(0.1, -0.05, 0.2), style="no_clip")
    assert np.allclose(out[..., 0], 0.4, atol=1e-5)
    assert np.allclose(out[..., 1], 0.25, atol=1e-5)
    assert np.allclose(out[..., 2], 0.5, atol=1e-5)
    print("apply_cdl: offset adds a per-channel constant, matching "
          "out = in + offset for a unity-slope/unity-power case -- OK")


def test_power_matches_manual_formula_no_clip():
    if not cdl.OCIO_AVAILABLE:
        return _skip("test_power_matches_manual_formula_no_clip")
    img = np.full((2, 2, 3), 0.5, dtype=np.float32)
    slope, offset, power = (1.0, 1.0, 1.0), (0.0, 0.0, 0.0), (2.0, 0.5, 1.0)
    out = cdl.apply_cdl(img, slope=slope, offset=offset, power=power, style="no_clip")
    expected = np.asarray(slope) * 0.5 + np.asarray(offset)
    expected = expected ** np.asarray(power)
    assert np.allclose(out[0, 0], expected, atol=1e-5)
    print(f"apply_cdl (no_clip): out={out[0, 0]} matches manual "
          f"(in*slope+offset)**power = {expected} -- OK")


# --- ASC clamp vs no-clip style ---------------------------------------------

def test_asc_style_clamps_between_sop_and_sat_no_clip_does_not():
    if not cdl.OCIO_AVAILABLE:
        return _skip("test_asc_style_clamps_between_sop_and_sat_no_clip_does_not")
    # slope pushes well above 1.0, no saturation change -- so any
    # difference between the two styles is purely the intermediate clamp.
    img = np.full((2, 2, 3), 0.9, dtype=np.float32)
    out_asc = cdl.apply_cdl(img, slope=(2.0, 2.0, 2.0), style="asc")
    out_no_clip = cdl.apply_cdl(img, slope=(2.0, 2.0, 2.0), style="no_clip")
    assert np.all(out_asc <= 1.0 + 1e-5), "ASC style should clamp to <=1.0"
    assert np.any(out_no_clip > 1.0 + 1e-3), "no_clip style should preserve the >1.0 value"
    assert not np.allclose(out_asc, out_no_clip, atol=1e-3)
    print(f"apply_cdl: style='asc' clamps ({out_asc[0, 0]}) while "
          f"style='no_clip' preserves the out-of-range value "
          f"({out_no_clip[0, 0]}) -- OK")


def test_styles_agree_when_result_stays_in_range():
    if not cdl.OCIO_AVAILABLE:
        return _skip("test_styles_agree_when_result_stays_in_range")
    img = np.full((2, 2, 3), 0.4, dtype=np.float32)
    out_asc = cdl.apply_cdl(img, slope=(1.1, 1.1, 1.1), offset=(0.02, 0.02, 0.02), style="asc")
    out_no_clip = cdl.apply_cdl(img, slope=(1.1, 1.1, 1.1), offset=(0.02, 0.02, 0.02), style="no_clip")
    assert np.allclose(out_asc, out_no_clip, atol=1e-5), \
        "the ASC-clamp-vs-no-clip distinction should only matter once values leave [0,1]"
    print("apply_cdl: 'asc' and 'no_clip' styles agree exactly when the SOP "
          "result never leaves [0,1] -- OK")


# --- bypass toggles ----------------------------------------------------

def test_sop_enabled_false_ignores_slope_offset_power():
    if not cdl.OCIO_AVAILABLE:
        return _skip("test_sop_enabled_false_ignores_slope_offset_power")
    img = np.full((2, 2, 3), 0.5, dtype=np.float32)
    out = cdl.apply_cdl(
        img, slope=(3.0, 3.0, 3.0), offset=(0.5, 0.5, 0.5), power=(2.0, 2.0, 2.0),
        sop_enabled=False, style="no_clip",
    )
    assert np.allclose(out, img, atol=1e-6), \
        "sop_enabled=False should ignore slope/offset/power entirely (identity SOP)"
    print("apply_cdl: sop_enabled=False makes SOP an identity regardless of "
          "slope/offset/power values -- OK")


def test_sat_enabled_false_ignores_sat():
    if not cdl.OCIO_AVAILABLE:
        return _skip("test_sat_enabled_false_ignores_sat")
    img = np.random.default_rng(2).random((3, 3, 3)).astype(np.float32)
    out_with_sat_disabled = cdl.apply_cdl(img, sat=0.0, sat_enabled=False)
    out_sat_identity = cdl.apply_cdl(img, sat=1.0, sat_enabled=True)
    assert np.allclose(out_with_sat_disabled, out_sat_identity, atol=1e-6), \
        "sat_enabled=False should ignore sat=0.0 (full desaturation) and behave like sat=1.0"
    assert not np.allclose(out_with_sat_disabled, np.zeros_like(img[..., :1]) + img.mean())
    print("apply_cdl: sat_enabled=False makes SAT an identity (sat=0.0 has "
          "no effect) -- OK")


def test_sat_actually_desaturates_when_enabled():
    if not cdl.OCIO_AVAILABLE:
        return _skip("test_sat_actually_desaturates_when_enabled")
    img = np.array([[[0.8, 0.2, 0.1]]], dtype=np.float32)
    out = cdl.apply_cdl(img, sat=0.0, sat_enabled=True)
    r, g, b = out[0, 0]
    assert abs(r - g) < 1e-4 and abs(g - b) < 1e-4, \
        f"sat=0.0 should fully desaturate to a gray, got {out[0, 0]}"
    print(f"apply_cdl: sat=0.0 fully desaturates a saturated pixel to gray "
          f"({out[0, 0]}) -- OK")


# --- build_cdl_transform / list_available_styles ---------------------------

def test_list_available_styles_has_asc_and_no_clip():
    styles = dict(cdl.list_available_styles())
    assert "asc" in styles and "no_clip" in styles
    print("list_available_styles: includes both 'asc' and 'no_clip' -- OK")


def test_build_cdl_transform_round_trips_params():
    if not cdl.OCIO_AVAILABLE:
        return _skip("test_build_cdl_transform_round_trips_params")
    transform = cdl.build_cdl_transform(
        slope=(1.1, 1.2, 1.3), offset=(0.01, 0.02, 0.03), power=(0.9, 1.0, 1.1), sat=1.2)
    assert np.allclose(transform.getSlope(), [1.1, 1.2, 1.3])
    assert np.allclose(transform.getOffset(), [0.01, 0.02, 0.03])
    assert np.allclose(transform.getPower(), [0.9, 1.0, 1.1])
    assert abs(transform.getSat() - 1.2) < 1e-9
    print("build_cdl_transform: slope/offset/power/sat round-trip through "
          "the resulting ocio.CDLTransform exactly -- OK")


if __name__ == "__main__":
    test_identity_params_are_exact_noop_both_styles()
    test_bypassed_sop_and_sat_together_is_exact_noop()
    test_slope_scales_each_channel_independently()
    test_offset_adds_a_constant()
    test_power_matches_manual_formula_no_clip()
    test_asc_style_clamps_between_sop_and_sat_no_clip_does_not()
    test_styles_agree_when_result_stays_in_range()
    test_sop_enabled_false_ignores_slope_offset_power()
    test_sat_enabled_false_ignores_sat()
    test_sat_actually_desaturates_when_enabled()
    test_list_available_styles_has_asc_and_no_clip()
    test_build_cdl_transform_round_trips_params()
    print()
    print("All cdl tests passed.")
