"""Tests for colorspace_convert.py -- run directly: python3 -m tests.test_colorspace_convert"""
import numpy as np

import colorspace_convert as csc


def _skip(name):
    print(f"SKIPPED {name} -- PyOpenColorIO not installed")


# --- listing -----------------------------------------------------------

def test_scene_and_display_lists_are_disjoint():
    if not csc.OCIO_AVAILABLE:
        return _skip("test_scene_and_display_lists_are_disjoint")
    scene = csc.list_scene_referred_colourspaces()
    display = csc.list_display_referred_colourspaces()
    assert len(scene) > 0 and len(display) > 0
    assert not (set(scene) & set(display))
    print(f"list_scene_referred_colourspaces ({len(scene)}) and "
          f"list_display_referred_colourspaces ({len(display)}) share no "
          "names -- OK")


def test_display_list_names_all_end_in_display_suffix():
    if not csc.OCIO_AVAILABLE:
        return _skip("test_display_list_names_all_end_in_display_suffix")
    for name in csc.list_display_referred_colourspaces():
        assert name.endswith(csc.DISPLAY_SUFFIX), name
    print("list_display_referred_colourspaces: every entry ends in "
          f"{csc.DISPLAY_SUFFIX!r} -- OK")


def test_scene_list_names_never_end_in_display_suffix():
    if not csc.OCIO_AVAILABLE:
        return _skip("test_scene_list_names_never_end_in_display_suffix")
    for name in csc.list_scene_referred_colourspaces():
        assert not name.endswith(csc.DISPLAY_SUFFIX), name
    print("list_scene_referred_colourspaces: no entry ends in "
          f"{csc.DISPLAY_SUFFIX!r} -- OK")


def test_list_available_configs_matches_aces_drt():
    import aces_drt
    assert csc.list_available_configs() == aces_drt.list_available_configs()
    print("list_available_configs: identical to aces_drt.list_available_configs "
          "-- confirms the shared single config registry -- OK")


# --- apply_colorspace_conversion ----------------------------------------

def test_identity_conversion_is_exact_noop():
    if not csc.OCIO_AVAILABLE:
        return _skip("test_identity_conversion_is_exact_noop")
    rng = np.random.default_rng(0)
    img = rng.random((6, 6, 3)).astype(np.float32)
    for space in ("ACEScct", "ACEScg", "ACES2065-1"):
        out = csc.apply_colorspace_conversion(img, space, space)
        assert np.allclose(out, img, atol=1e-6), space
    print("apply_colorspace_conversion: src==dst is an exact no-op for "
          "several scene-referred spaces -- OK")


def test_scene_to_scene_conversion_changes_values():
    if not csc.OCIO_AVAILABLE:
        return _skip("test_scene_to_scene_conversion_changes_values")
    img = np.full((2, 2, 3), 0.5, dtype=np.float32)
    out = csc.apply_colorspace_conversion(img, "ACEScct", "ACEScg")
    assert not np.allclose(out, img)
    print("apply_colorspace_conversion: ACEScct -> ACEScg (scene-to-scene) "
          "actually changes values -- OK")


def test_scene_to_scene_round_trip():
    if not csc.OCIO_AVAILABLE:
        return _skip("test_scene_to_scene_round_trip")
    rng = np.random.default_rng(1)
    img = rng.random((4, 4, 3)).astype(np.float32) * 0.8 + 0.1
    converted = csc.apply_colorspace_conversion(img, "ACEScct", "ACEScg")
    back = csc.apply_colorspace_conversion(converted, "ACEScg", "ACEScct")
    assert np.allclose(back, img, atol=1e-4)
    print("apply_colorspace_conversion: ACEScct -> ACEScg -> ACEScct "
          "round-trips to the original within tolerance -- OK")


def test_display_to_display_conversion_changes_values():
    if not csc.OCIO_AVAILABLE:
        return _skip("test_display_to_display_conversion_changes_values")
    display_spaces = csc.list_display_referred_colourspaces()
    if len(display_spaces) < 2:
        print("SKIPPED test_display_to_display_conversion_changes_values -- "
              "fewer than 2 display-referred spaces in the default config")
        return
    src, dst = display_spaces[0], display_spaces[1]
    img = np.full((2, 2, 3), 0.6, dtype=np.float32)
    out = csc.apply_colorspace_conversion(img, src, dst)
    assert not np.allclose(out, img)
    print(f"apply_colorspace_conversion: {src!r} -> {dst!r} (display-to-"
          "display) actually changes values -- OK")


def test_conversion_result_depends_on_which_pair_is_used():
    """Same input, different src/dst pairs -> different output -- confirms
    the space arguments actually reach OCIO's ColorSpaceTransform rather
    than being silently ignored (mirrors aces_drt.py's own equivalent
    sanity check). Uses a SATURATED (non-gray) pixel deliberately -- a
    pure gray value maps to itself across most RGB gamut conversions
    (chromaticity is unchanged for an achromatic point), which would make
    this test pass even if src/dst were silently ignored."""
    if not csc.OCIO_AVAILABLE:
        return _skip("test_conversion_result_depends_on_which_pair_is_used")
    img = np.full((2, 2, 3), (0.6, 0.3, 0.1), dtype=np.float32)
    out_a = csc.apply_colorspace_conversion(img, "ACEScct", "ACEScg")
    out_b = csc.apply_colorspace_conversion(img, "ACEScct", "ACES2065-1")
    assert not np.allclose(out_a, out_b)
    print("apply_colorspace_conversion: different dst spaces give different "
          "results for the same (saturated) input -- OK")


def test_apply_colorspace_conversion_reuses_the_cached_processor():
    """Regression guard for a real, previously-shipped performance bug
    (found in code review): apply_colorspace_conversion used to build a
    fresh cfg.getProcessor(...).getDefaultCPUProcessor() from scratch on
    EVERY call -- real, measurable work on the hot path for every
    "colorspace" stage's own live-preview repaint and every custom_drt
    apply. It now goes through aces_drt._get_colourspace_conversion_
    processor, an lru_cache-wrapped helper already used everywhere else
    in this app for the identical operation -- confirmed here via the
    cache's own hit counter actually incrementing on a repeated call
    with the same (config, src, dst), not just "the result is correct."
    """
    if not csc.OCIO_AVAILABLE:
        return _skip("test_apply_colorspace_conversion_reuses_the_cached_processor")
    import aces_drt
    img = np.full((2, 2, 3), (0.6, 0.3, 0.1), dtype=np.float32)
    # A src/dst pair this test file hasn't used elsewhere, so its own
    # cache entry starts fresh regardless of prior test execution order.
    src, dst = "ACES2065-1", "ACEScc"
    aces_drt._get_colourspace_conversion_processor.cache_clear()

    csc.apply_colorspace_conversion(img, src, dst)
    misses_after_first = aces_drt._get_colourspace_conversion_processor.cache_info().misses
    csc.apply_colorspace_conversion(img, src, dst)
    info = aces_drt._get_colourspace_conversion_processor.cache_info()

    assert misses_after_first == 1, "the first call must build (and cache) the processor"
    assert info.hits == 1, "the second, identical call must reuse the cached processor, not rebuild it"
    assert info.misses == 1
    print("apply_colorspace_conversion: reuses aces_drt's cached OCIO "
          "processor instead of rebuilding it from scratch every call -- OK")


if __name__ == "__main__":
    test_scene_and_display_lists_are_disjoint()
    test_display_list_names_all_end_in_display_suffix()
    test_scene_list_names_never_end_in_display_suffix()
    test_list_available_configs_matches_aces_drt()
    test_identity_conversion_is_exact_noop()
    test_scene_to_scene_conversion_changes_values()
    test_scene_to_scene_round_trip()
    test_display_to_display_conversion_changes_values()
    test_conversion_result_depends_on_which_pair_is_used()
    test_apply_colorspace_conversion_reuses_the_cached_processor()
    print()
    print("All colorspace_convert tests passed.")
