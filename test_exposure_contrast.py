"""Tests for exposure_contrast.py -- run directly: python3 test_exposure_contrast.py"""
import numpy as np

import exposure_contrast as ec


def _skip(name):
    print(f"SKIPPED {name} -- PyOpenColorIO not installed")


def test_identity_params_are_exact_noop_for_every_style():
    if not ec.OCIO_AVAILABLE:
        return _skip("test_identity_params_are_exact_noop_for_every_style")
    rng = np.random.default_rng(0)
    img = rng.random((6, 6, 3)).astype(np.float32)
    for style, _label in ec.AVAILABLE_STYLES:
        out = ec.apply_exposure_contrast(img, style=style)
        assert np.allclose(out, img, atol=1e-6), style
        print(f"apply_exposure_contrast: identity params are an exact "
              f"no-op under style={style!r} -- OK")


def test_default_style_is_linear():
    assert ec.DEFAULT_STYLE == "linear"


def test_matches_a_directly_built_ocio_transform():
    """Regression guard for the wrapper's own field-mapping -- builds the
    exact same ExposureContrastTransform by hand, independent of
    build_exposure_contrast_transform, and confirms apply_exposure_
    contrast's output matches applying that by-hand transform directly."""
    if not ec.OCIO_AVAILABLE:
        return _skip("test_matches_a_directly_built_ocio_transform")
    import PyOpenColorIO as ocio
    import cdl

    rng = np.random.default_rng(1)
    img = rng.uniform(0, 1, size=(8, 8, 3)).astype(np.float32)
    exposure, contrast, gamma = 0.4, 1.15, 1.05

    out = ec.apply_exposure_contrast(img, style="log", exposure=exposure, contrast=contrast, gamma=gamma)

    by_hand = ocio.ExposureContrastTransform()
    by_hand.setStyle(ocio.EXPOSURE_CONTRAST_LOGARITHMIC)
    by_hand.setExposure(exposure)
    by_hand.setContrast(contrast)
    by_hand.setGamma(gamma)

    cfg = cdl._get_raw_config()
    cpu = cfg.getProcessor(by_hand).getDefaultCPUProcessor()
    expected = img.copy()
    desc = ocio.PackedImageDesc(expected.reshape(-1, 3), expected.shape[0] * expected.shape[1], 1, 3)
    cpu.apply(desc)

    assert np.allclose(out, expected, atol=1e-6)
    print("apply_exposure_contrast: matches a by-hand-built "
          "ocio.ExposureContrastTransform exactly -- wrapper's field mapping is correct -- OK")


def test_positive_exposure_brightens():
    if not ec.OCIO_AVAILABLE:
        return _skip("test_positive_exposure_brightens")
    img = np.full((1, 1, 3), 0.2, dtype=np.float32)
    out = ec.apply_exposure_contrast(img, exposure=1.0)  # +1 stop -> 2x
    assert np.allclose(out, 0.4, atol=1e-4)
    print("apply_exposure_contrast: +1 stop of exposure doubles a mid-gray "
          "value, matching pow(2, exposure) -- OK")


def test_unknown_style_raises():
    if not ec.OCIO_AVAILABLE:
        return _skip("test_unknown_style_raises")
    try:
        ec.apply_exposure_contrast(np.zeros((1, 1, 3), dtype=np.float32), style="nonsense")
        raise AssertionError("expected a ValueError for an unknown style")
    except ValueError:
        print("apply_exposure_contrast: an unknown style raises ValueError -- OK")


if __name__ == "__main__":
    test_identity_params_are_exact_noop_for_every_style()
    test_default_style_is_linear()
    test_matches_a_directly_built_ocio_transform()
    test_positive_exposure_brightens()
    test_unknown_style_raises()
    print()
    print("All exposure_contrast tests passed.")
