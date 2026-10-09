"""Tests for grading_tone.py -- run directly: python3 -m tests.test_grading_tone"""
import numpy as np

import grading_tone as gt


def _skip(name):
    print(f"SKIPPED {name} -- PyOpenColorIO not installed")


def test_identity_params_are_exact_noop_for_every_style():
    if not gt.OCIO_AVAILABLE:
        return _skip("test_identity_params_are_exact_noop_for_every_style")
    rng = np.random.default_rng(0)
    img = rng.random((6, 6, 3)).astype(np.float32)
    for style, _label in gt.AVAILABLE_STYLES:
        out = gt.apply_grading_tone(img, style=style)
        assert np.allclose(out, img, atol=1e-6), style
        print(f"apply_grading_tone: identity params are an exact "
              f"no-op under style={style!r} -- OK")


def test_default_style_is_log():
    assert gt.DEFAULT_STYLE == "log"


def test_matches_a_directly_built_ocio_transform():
    """Regression guard for the wrapper's own field-mapping (which range
    maps to which attribute, R/G/B/M/start/width order) -- builds the
    exact same GradingTone by hand, independent of build_grading_tone_
    value/_transform, and confirms apply_grading_tone's output matches
    applying that by-hand transform directly."""
    if not gt.OCIO_AVAILABLE:
        return _skip("test_matches_a_directly_built_ocio_transform")
    import PyOpenColorIO as ocio
    import cdl

    rng = np.random.default_rng(1)
    img = rng.uniform(0, 1, size=(8, 8, 3)).astype(np.float32)
    midtones = (1.05, 0.98, 1.02, 1.0, 0.4, 0.6)
    highlights = (1.1, 1.05, 0.95, 1.0, 0.3, 1.0)
    scontrast = 1.15

    out = gt.apply_grading_tone(img, style="log", midtones=midtones, highlights=highlights,
                                 scontrast=scontrast)

    by_hand = ocio.GradingTone(ocio.GRADING_LOG)
    by_hand.midtones = ocio.GradingRGBMSW(*midtones)
    by_hand.highlights = ocio.GradingRGBMSW(*highlights)
    by_hand.scontrast = scontrast
    by_hand_transform = ocio.GradingToneTransform(ocio.GRADING_LOG)
    by_hand_transform.setValue(by_hand)

    cfg = cdl._get_raw_config()
    cpu = cfg.getProcessor(by_hand_transform).getDefaultCPUProcessor()
    expected = img.copy()
    desc = ocio.PackedImageDesc(expected.reshape(-1, 3), expected.shape[0] * expected.shape[1], 1, 3)
    cpu.apply(desc)

    assert np.allclose(out, expected, atol=1e-6)
    print("apply_grading_tone: matches a by-hand-built ocio.GradingToneTransform "
          "exactly -- wrapper's field mapping is correct -- OK")


def test_midtones_gain_only_affects_midtone_region():
    """Sanity check on the range shape, not exact math: boosting
    midtones' master gain should change a genuinely mid-value pixel more
    than it changes a near-black or near-white one (the whole point of
    range-limited tone shaping vs. a flat gain)."""
    if not gt.OCIO_AVAILABLE:
        return _skip("test_midtones_gain_only_affects_midtone_region")
    dark = np.full((1, 1, 3), 0.02, dtype=np.float32)
    mid = np.full((1, 1, 3), 0.4, dtype=np.float32)
    bright = np.full((1, 1, 3), 0.98, dtype=np.float32)
    boosted_midtones = (1.0, 1.0, 1.0, 1.5, 0.4, 0.6)

    dark_delta = abs(float(gt.apply_grading_tone(dark, midtones=boosted_midtones)[0, 0, 0]) - 0.02)
    mid_delta = abs(float(gt.apply_grading_tone(mid, midtones=boosted_midtones)[0, 0, 0]) - 0.4)
    bright_delta = abs(float(gt.apply_grading_tone(bright, midtones=boosted_midtones)[0, 0, 0]) - 0.98)

    assert mid_delta > dark_delta, "boosting midtones should move a mid-value pixel more than a near-black one"
    assert mid_delta > bright_delta, "boosting midtones should move a mid-value pixel more than a near-white one"
    print("apply_grading_tone: boosting midtones' gain affects a mid-value "
          "pixel more than near-black/near-white pixels -- OK")


def test_out_of_bounds_gain_is_clamped_not_raised():
    """Regression guard for a real, reproduced bug: ocio.GradingTone.
    validate() rejects R/G/B/Master gain outside a narrow, PER-RANGE
    band (confirmed via probing -- see GAIN_BOUNDS' own comment), and a
    live UI slider used to allow dragging past it, raising a real
    ocio.Exception from inside setGradingTone on every render tick --
    this is what a "seemed to work slowly, then crashed" report traced
    back to (the exception crashed PySide's own exception-reporting path
    for a Python override of a C++ virtual method). apply_grading_tone
    must clamp rather than let that exception reach the caller at all."""
    if not gt.OCIO_AVAILABLE:
        return _skip("test_out_of_bounds_gain_is_clamped_not_raised")
    img = np.full((2, 2, 3), 0.4, dtype=np.float32)
    # Master gain of 3.0 is well beyond midtones' real upper bound (1.9)
    # -- this used to raise ocio.Exception directly out of apply_grading_
    # tone (via build_grading_tone_value -> setValue), now must clamp.
    out = gt.apply_grading_tone(img, midtones=(1.0, 1.0, 1.0, 3.0, 0.4, 0.6))
    assert out is not None and not np.any(np.isnan(out))
    # And confirm it actually clamped to the real bound, not silently
    # ignored the value entirely (which would just be another kind of bug).
    clamped_at_bound = gt.apply_grading_tone(img, midtones=(1.0, 1.0, 1.0, 1.9, 0.4, 0.6))
    identity = gt.apply_grading_tone(img)
    assert not np.allclose(out, identity, atol=1e-6), "a clamped-but-still-nonzero gain should still do something"
    assert np.allclose(out, clamped_at_bound, atol=1e-6), (
        "master=3.0 should clamp to the same result as master=1.9 (the real bound)")
    print("apply_grading_tone: an out-of-bounds gain value is clamped to "
          "OCIO's real validate() bound instead of raising -- OK")


def test_unknown_style_raises():
    if not gt.OCIO_AVAILABLE:
        return _skip("test_unknown_style_raises")
    try:
        gt.apply_grading_tone(np.zeros((1, 1, 3), dtype=np.float32), style="nonsense")
        raise AssertionError("expected a ValueError for an unknown style")
    except ValueError:
        print("apply_grading_tone: an unknown style raises ValueError -- OK")


if __name__ == "__main__":
    test_identity_params_are_exact_noop_for_every_style()
    test_default_style_is_log()
    test_matches_a_directly_built_ocio_transform()
    test_midtones_gain_only_affects_midtone_region()
    test_out_of_bounds_gain_is_clamped_not_raised()
    test_unknown_style_raises()
    print()
    print("All grading_tone tests passed.")
