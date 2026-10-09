"""Tests for grading_primary.py -- run directly: python3 -m tests.test_grading_primary"""
import numpy as np

import grading_primary as gp


def _skip(name):
    print(f"SKIPPED {name} -- PyOpenColorIO not installed")


def test_identity_params_are_exact_noop_for_every_style():
    if not gp.OCIO_AVAILABLE:
        return _skip("test_identity_params_are_exact_noop_for_every_style")
    rng = np.random.default_rng(0)
    img = rng.random((6, 6, 3)).astype(np.float32)
    for style, _label in gp.AVAILABLE_STYLES:
        out = gp.apply_grading_primary(img, style=style)
        assert np.allclose(out, img, atol=1e-6), style
        print(f"apply_grading_primary: identity params are an exact "
              f"no-op under style={style!r} -- OK")


def test_identity_is_a_real_noop_for_out_of_range_input():
    """Unlike CDL's "asc" style, GradingPrimary has no intermediate clamp
    that applies unconditionally -- see module docstring."""
    if not gp.OCIO_AVAILABLE:
        return _skip("test_identity_is_a_real_noop_for_out_of_range_input")
    img = np.array([[[-0.3, 1.8, 0.5]]], dtype=np.float32)
    for style, _label in gp.AVAILABLE_STYLES:
        out = gp.apply_grading_primary(img, style=style)
        assert np.allclose(out, img, atol=1e-6), style
    print("apply_grading_primary: identity params are a real no-op even "
          "for already-out-of-range input, every style -- OK")


def test_default_style_is_log():
    assert gp.DEFAULT_STYLE == "log"


def test_matches_a_directly_built_ocio_transform():
    """Regression guard for the wrapper's own field-mapping (r/g/b/master
    order, correct attribute names, correct style enum) -- builds the
    exact same GradingPrimary by hand, independent of
    build_grading_primary_value/_transform, and confirms apply_grading_
    primary's output matches applying that by-hand transform directly."""
    if not gp.OCIO_AVAILABLE:
        return _skip("test_matches_a_directly_built_ocio_transform")
    import PyOpenColorIO as ocio
    import cdl

    rng = np.random.default_rng(1)
    img = rng.uniform(0, 1, size=(8, 8, 3)).astype(np.float32)

    brightness = (0.02, -0.01, 0.03, 0.0)
    contrast = (1.1, 1.05, 0.95, 1.0)
    gamma = (1.0, 1.0, 1.0, 1.05)
    saturation = 1.1
    pivot = -0.15

    out = gp.apply_grading_primary(
        img, style="log", brightness=brightness, contrast=contrast, gamma=gamma,
        saturation=saturation, pivot=pivot,
    )

    by_hand = ocio.GradingPrimary(ocio.GRADING_LOG)
    by_hand.brightness = ocio.GradingRGBM(*brightness)
    by_hand.contrast = ocio.GradingRGBM(*contrast)
    by_hand.gamma = ocio.GradingRGBM(*gamma)
    by_hand.saturation = saturation
    by_hand.pivot = pivot
    by_hand_transform = ocio.GradingPrimaryTransform(ocio.GRADING_LOG)
    by_hand_transform.setValue(by_hand)

    cfg = cdl._get_raw_config()
    cpu = cfg.getProcessor(by_hand_transform).getDefaultCPUProcessor()
    expected = img.copy()
    desc = ocio.PackedImageDesc(expected.reshape(-1, 3), expected.shape[0] * expected.shape[1], 1, 3)
    cpu.apply(desc)

    assert np.allclose(out, expected, atol=1e-6)
    print("apply_grading_primary: matches a by-hand-built ocio.GradingPrimaryTransform "
          "exactly -- wrapper's field mapping is correct -- OK")


def test_contrast_increases_spread_around_pivot():
    """Sanity check on direction, not exact math: increasing master
    contrast around a mid-value pivot should push a value ABOVE the pivot
    higher and a value BELOW the pivot lower."""
    if not gp.OCIO_AVAILABLE:
        return _skip("test_contrast_increases_spread_around_pivot")
    pivot = 0.0
    low = np.full((1, 1, 3), 0.2, dtype=np.float32)
    high = np.full((1, 1, 3), 0.8, dtype=np.float32)
    identity_low = gp.apply_grading_primary(low, style="log", pivot=pivot)[0, 0, 0]
    identity_high = gp.apply_grading_primary(high, style="log", pivot=pivot)[0, 0, 0]
    boosted_low = gp.apply_grading_primary(
        low, style="log", contrast=(1.0, 1.0, 1.0, 1.3), pivot=pivot)[0, 0, 0]
    boosted_high = gp.apply_grading_primary(
        high, style="log", contrast=(1.0, 1.0, 1.0, 1.3), pivot=pivot)[0, 0, 0]
    assert boosted_high > identity_high, "higher contrast should push an above-pivot value further up"
    assert boosted_low < identity_low, "higher contrast should push a below-pivot value further down"
    print("apply_grading_primary: increasing master contrast spreads "
          "values further from the pivot in the expected direction -- OK")


def test_zero_or_negative_contrast_gamma_is_clamped_not_raised():
    """Regression guard, same bug class as grading_tone's: ocio.
    GradingPrimary.validate() requires contrast/gamma to stay strictly
    positive (confirmed via probing -- floor ~0.01), but the UI's old
    spin range allowed dragging down to 0.0 exactly, which raises
    ocio.Exception from inside setGradingPrimary."""
    if not gp.OCIO_AVAILABLE:
        return _skip("test_zero_or_negative_contrast_gamma_is_clamped_not_raised")
    img = np.full((2, 2, 3), 0.4, dtype=np.float32)
    out = gp.apply_grading_primary(img, contrast=(0.0, 1.0, 1.0, 1.0))
    assert out is not None and not np.any(np.isnan(out))
    out_negative = gp.apply_grading_primary(img, gamma=(-0.5, 1.0, 1.0, 1.0))
    assert out_negative is not None and not np.any(np.isnan(out_negative))
    print("apply_grading_primary: zero/negative contrast or gamma is "
          "clamped to a strictly-positive floor instead of raising -- OK")


def test_unknown_style_raises():
    if not gp.OCIO_AVAILABLE:
        return _skip("test_unknown_style_raises")
    try:
        gp.apply_grading_primary(np.zeros((1, 1, 3), dtype=np.float32), style="nonsense")
        raise AssertionError("expected a ValueError for an unknown style")
    except ValueError:
        print("apply_grading_primary: an unknown style raises ValueError -- OK")


if __name__ == "__main__":
    test_identity_params_are_exact_noop_for_every_style()
    test_identity_is_a_real_noop_for_out_of_range_input()
    test_default_style_is_log()
    test_matches_a_directly_built_ocio_transform()
    test_contrast_increases_spread_around_pivot()
    test_zero_or_negative_contrast_gamma_is_clamped_not_raised()
    test_unknown_style_raises()
    print()
    print("All grading_primary tests passed.")
