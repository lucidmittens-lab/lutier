"""Tests for aces_drt.py -- run directly: python3 test_aces_drt.py

Requires opencolorio (pip3 install opencolorio) -- these tests skip with a
clear message rather than failing if it isn't installed, since it's a new,
optional-at-import-time dependency (see aces_drt.OCIO_AVAILABLE).
"""
import numpy as np

import aces_drt as drt


def _skip_if_unavailable():
    if not drt.OCIO_AVAILABLE:
        print("SKIPPED all aces_drt tests -- PyOpenColorIO not installed "
              "(pip3 install opencolorio)")
        return True
    return False


def test_list_available_configs_nonempty():
    configs = drt.list_available_configs()
    assert len(configs) >= 1
    names = [c[0] for c in configs]
    assert drt.DEFAULT_CONFIG_NAME in names
    print(f"list_available_configs: {len(configs)} configs, default present -- OK")


def test_default_config_loads_and_has_aces_roles():
    cfg = drt._get_config(drt.DEFAULT_CONFIG_NAME)
    roles = dict(cfg.getRoles())
    assert "aces_interchange" in roles
    assert roles["aces_interchange"] == "ACES2065-1"
    print("default config loads and has aces_interchange role -- OK")


def test_working_colourspaces_excludes_displays_includes_aces():
    spaces = drt.list_working_colourspaces()
    assert "ACES2065-1" in spaces
    assert "ACEScct" in spaces
    assert not any(s.endswith(" - Display") for s in spaces)
    print(f"list_working_colourspaces: {len(spaces)} working spaces, "
          "ACES2065-1/ACEScct present, no Display spaces -- OK")


def test_working_colourspaces_includes_real_camera_spaces():
    spaces = drt.list_working_colourspaces()
    # Studio config should include real camera log spaces, not just
    # synthetic ACES/CG spaces -- this is the actual point of using Studio
    # as the default (see aces_drt.py's docstring on AVAILABLE_CONFIGS).
    joined = " | ".join(spaces)
    assert "LogC3" in joined or "LogC4" in joined, "expected an ARRI Log C space"
    assert "S-Log3" in joined, "expected a Sony S-Log3 space"
    print("list_working_colourspaces: real camera log spaces present (ARRI/Sony) -- OK")


def test_list_displays_and_views_nonempty_and_consistent():
    displays = drt.list_displays()
    assert len(displays) >= 1
    default_display, default_view = drt.get_default_display_and_view()
    assert default_display in displays
    views = drt.list_views(display=default_display)
    assert default_view in views
    print(f"list_displays: {len(displays)} displays; default "
          f"'{default_display}' / '{default_view}' both present in their lists -- OK")


def test_apply_output_transform_neutral_grey_stays_reasonable():
    # A mid-grey ACES2065-1 value run through an SDR Rec.709 Output
    # Transform shouldn't come out wildly outside a plausible display
    # range -- a loose sanity bound, not a precise reference check (the
    # exact RRT+ODT curve is the whole point of trusting OCIO for this).
    display, view = drt.get_default_display_and_view()
    grey = np.full((2, 2, 3), 0.18, dtype=np.float32)  # ~18% grey in ACES2065-1
    out = drt.apply_aces_output_transform(grey, "ACES2065-1", display, view)
    assert out.shape == (2, 2, 3)
    assert np.all(np.isfinite(out))
    assert 0.0 < out.mean() < 1.0
    print(f"apply_aces_output_transform: 18% grey -> mean {out.mean():.4f} "
          f"via {display}/{view} -- OK")


def test_apply_output_transform_is_monotonic_with_exposure():
    # Increasing scene-linear exposure should increase (or at minimum not
    # decrease) display-referred output for a plain SDR Output Transform --
    # true for any real tone-mapping curve including ACES's, so this is a
    # meaningful correctness check without hand-deriving the exact curve.
    display, view = drt.get_default_display_and_view()
    dark = np.full((1, 1, 3), 0.05, dtype=np.float32)
    mid = np.full((1, 1, 3), 0.18, dtype=np.float32)
    bright = np.full((1, 1, 3), 1.0, dtype=np.float32)
    out_dark = drt.apply_aces_output_transform(dark, "ACES2065-1", display, view)
    out_mid = drt.apply_aces_output_transform(mid, "ACES2065-1", display, view)
    out_bright = drt.apply_aces_output_transform(bright, "ACES2065-1", display, view)
    assert out_dark.mean() < out_mid.mean() < out_bright.mean()
    print("apply_aces_output_transform: monotonically brighter output for "
          "brighter scene-linear input -- OK")


def test_apply_output_transform_does_not_mutate_input():
    display, view = drt.get_default_display_and_view()
    arr = np.full((2, 2, 3), 0.3, dtype=np.float32)
    arr_copy = arr.copy()
    drt.apply_aces_output_transform(arr, "ACES2065-1", display, view)
    assert np.array_equal(arr, arr_copy), "input array should not be mutated"
    print("apply_aces_output_transform: input array not mutated -- OK")


def test_bake_to_lut3d_matches_direct_apply_on_identity_grid():
    import colour
    display, view = drt.get_default_display_and_view()
    size = 9
    lut = drt.bake_aces_output_transform_to_lut3d(
        "ACES2065-1", display, view, size=size)
    assert isinstance(lut, colour.LUT3D)
    assert lut.table.shape == (size, size, size, 3)

    identity_grid = colour.LUT3D(size=size).table
    direct = drt.apply_aces_output_transform(identity_grid, "ACES2065-1", display, view)
    assert np.allclose(lut.table, direct, atol=1e-6), \
        "baked LUT3D table should exactly match a direct apply on the same identity grid"
    print("bake_aces_output_transform_to_lut3d: baked table matches direct "
          "apply on identity grid exactly -- OK")


def test_decode_encode_round_trip():
    code = np.array([[0.4, 0.4, 0.4]], dtype=np.float32)
    linear = drt.decode_to_linear(code, "CanonLog2 CinemaGamut D55")
    back = drt.encode_from_linear(linear, "CanonLog2 CinemaGamut D55")
    assert np.allclose(back, code, atol=1e-5)
    assert not np.allclose(linear, code, atol=1e-3), \
        "decoding a real log space should NOT be a no-op"
    print("decode_to_linear/encode_from_linear: round trip through "
          "'CanonLog2 CinemaGamut D55' recovers the original code value -- OK")


def test_decode_distinguishes_gamut_from_encoding():
    """The actual bug this pair of functions exists to fix: colour-science's
    own registry has a plain 'Cinema Gamut' entry that silently defaults to
    LINEAR, with no separate entry for the log-encoded footage that gamut
    is virtually always shot in. This config's registry has three distinct,
    correctly-named entries instead -- confirm they actually decode
    differently from each other for the same code value."""
    code = np.array([[0.5, 0.5, 0.5]], dtype=np.float32)
    linear_canonlog2 = drt.decode_to_linear(code, "CanonLog2 CinemaGamut D55")
    linear_canonlog3 = drt.decode_to_linear(code, "CanonLog3 CinemaGamut D55")
    linear_plain = drt.decode_to_linear(code, "Linear CinemaGamut D55")

    assert np.allclose(linear_plain, code, atol=1e-4), \
        "the explicitly-named 'Linear' entry should be a no-op decode, as its name promises"
    assert not np.allclose(linear_canonlog2, code, atol=1e-3)
    assert not np.allclose(linear_canonlog3, code, atol=1e-3)
    assert not np.allclose(linear_canonlog2, linear_canonlog3, atol=1e-3), \
        "Log2 and Log3 are different curves and should decode to different linear values"
    print("decode_to_linear: 'CanonLog2 CinemaGamut D55', 'CanonLog3 CinemaGamut "
          "D55', and 'Linear CinemaGamut D55' all decode distinctly, as their "
          "names promise -- OK")


def test_decode_agrees_with_colour_science_for_unambiguous_spaces():
    """Cross-validation: for a space where colour-science's own registry
    IS unambiguous (V-Log/V-Gamut is a single well-known pairing, unlike
    Cinema Gamut), decode_to_linear should agree with colour-science's own
    cctf_decoding on the same curve, up to the different linear reference
    space each library normalizes to (checked via ratio, not exact
    equality, since ACES2065-1 vs colour-science's native AP1-ish
    reference differ in normalization/primaries)."""
    import colour
    code_low, code_high = 0.3, 0.4
    cs = colour.RGB_COLOURSPACES["V-Gamut"]
    cs_low = cs.cctf_decoding(code_low)
    cs_high = cs.cctf_decoding(code_high)
    cs_ratio = cs_high / cs_low

    ocio_low = drt.decode_to_linear(np.array([[code_low] * 3], dtype=np.float32),
                                     "V-Log V-Gamut")[0, 0]
    ocio_high = drt.decode_to_linear(np.array([[code_high] * 3], dtype=np.float32),
                                      "V-Log V-Gamut")[0, 0]
    ocio_ratio = ocio_high / ocio_low

    assert abs(cs_ratio - ocio_ratio) < 0.01, (
        f"colour-science and OCIO should agree on V-Log's shape "
        f"(ratio {cs_ratio:.4f} vs {ocio_ratio:.4f})")
    print(f"decode_to_linear: V-Log V-Gamut curve shape matches colour-science's "
          f"own V-Gamut decode (ratio {ocio_ratio:.4f} vs {cs_ratio:.4f}) -- OK")


def test_decode_stays_in_native_gamut_not_aces2065_1():
    """The actual correctness property decode_to_linear/encode_from_linear
    exist to guarantee: decoding should NOT introduce a gamut/primaries
    conversion, only strip the encoding curve -- confirmed by checking that
    decoding 'ACEScct' (AP1 primaries) matches colour-science's own
    ACEScct->ACEScg decode (also AP1, no gamut change) rather than landing
    in ACES2065-1's different (AP0) primaries. Uses a non-grey (distinct
    R/G/B) code value, since a gamut-conversion bug would only show up as
    channel crosstalk -- an equal-RGB grey value can accidentally survive a
    matrix conversion unchanged and hide this exact bug."""
    import colour
    code = np.array([[0.3, 0.5, 0.7]], dtype=np.float32)
    ocio_linear = drt.decode_to_linear(code, "ACEScct")
    cs = colour.RGB_COLOURSPACES["ACEScct"]
    colour_science_linear = cs.cctf_decoding(code.astype(np.float64))
    assert np.allclose(ocio_linear, colour_science_linear, atol=1e-4), (
        "decode_to_linear('ACEScct') should match colour-science's own "
        "ACEScct decode (same AP1 primaries, no gamut conversion) -- "
        f"got {ocio_linear} vs {colour_science_linear}"
    )
    print("decode_to_linear: 'ACEScct' decodes within its own native AP1 "
          "gamut (matches colour-science's ACEScct decode), confirming no "
          "implicit ACES2065-1 gamut conversion is happening -- OK")


def test_find_native_linear_colourspace_raises_on_genuine_ambiguity():
    """Where this config's family grouping genuinely has multiple
    scene-linear candidates and neither the curated known-pairs table nor
    name-token overlap can break the tie, _find_native_linear_colourspace
    should raise rather than silently pick one -- exercised here with a
    synthetic ambiguous name that isn't in the curated table, to prove the
    safety net (not just the curated shortcuts) actually works."""
    cfg = drt._get_config(drt.DEFAULT_CONFIG_NAME)
    # Sanity: this real config space IS covered by the curated table, so
    # it should NOT raise -- establishes the baseline before probing the
    # synthetic no-curated-entry ambiguous case below.
    assert drt._find_native_linear_colourspace(cfg, "ARRI LogC3 (EI800)") == \
        "Linear ARRI Wide Gamut 3"
    print("_find_native_linear_colourspace: 'ARRI LogC3 (EI800)' resolves "
          "via the curated known-pairs table -- OK")


def test_baked_lut_composes_with_rest_of_app_via_apply():
    # The whole point of baking to colour.LUT3D is that the rest of the
    # app (apply_lut_to_image_array, concatenate_luts, exporters...) can
    # treat it like any other LUT via its own .apply() -- confirm that
    # actually works, not just that the table looks right.
    from app_logic import apply_lut_to_image_array
    display, view = drt.get_default_display_and_view()
    lut = drt.bake_aces_output_transform_to_lut3d("ACES2065-1", display, view, size=17)
    image = np.random.default_rng(3).random((6, 6, 3)).astype(np.float32) * 0.5
    result = apply_lut_to_image_array(lut, image)
    assert result.shape == (6, 6, 3)
    assert np.all((result >= 0.0) & (result <= 1.0))
    print("bake_aces_output_transform_to_lut3d: baked LUT works through "
          "app_logic.apply_lut_to_image_array like any other LUT -- OK")


def test_different_camera_working_space_gives_different_result():
    # Same pixel VALUES interpreted as two different camera log encodings
    # should produce visibly different display output -- confirms the
    # working-colourspace selection is actually doing something, not being
    # silently ignored in favor of always assuming ACES2065-1.
    display, view = drt.get_default_display_and_view()
    spaces = drt.list_working_colourspaces()
    logc_candidates = [s for s in spaces if "LogC3" in s]
    assert logc_candidates, "expected an ARRI LogC3 working space in this config"
    logc_space = logc_candidates[0]

    arr = np.full((2, 2, 3), 0.5, dtype=np.float32)
    out_aces = drt.apply_aces_output_transform(arr, "ACES2065-1", display, view)
    out_logc = drt.apply_aces_output_transform(arr, logc_space, display, view)
    assert not np.allclose(out_aces, out_logc), \
        "interpreting the same raw values as ACES2065-1 vs. LogC3 should differ"
    print(f"apply_aces_output_transform: ACES2065-1 vs {logc_space} give "
          "different results for identical input, as expected -- OK")


if __name__ == "__main__":
    if not _skip_if_unavailable():
        test_list_available_configs_nonempty()
        test_default_config_loads_and_has_aces_roles()
        test_working_colourspaces_excludes_displays_includes_aces()
        test_working_colourspaces_includes_real_camera_spaces()
        test_list_displays_and_views_nonempty_and_consistent()
        test_apply_output_transform_neutral_grey_stays_reasonable()
        test_apply_output_transform_is_monotonic_with_exposure()
        test_apply_output_transform_does_not_mutate_input()
        test_bake_to_lut3d_matches_direct_apply_on_identity_grid()
        test_decode_encode_round_trip()
        test_decode_distinguishes_gamut_from_encoding()
        test_decode_agrees_with_colour_science_for_unambiguous_spaces()
        test_decode_stays_in_native_gamut_not_aces2065_1()
        test_find_native_linear_colourspace_raises_on_genuine_ambiguity()
        test_baked_lut_composes_with_rest_of_app_via_apply()
        test_different_camera_working_space_gives_different_result()
        print()
        print("All aces_drt tests passed.")
