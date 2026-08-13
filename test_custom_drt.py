"""Tests for custom_drt.py ("CAMier") -- run directly:
python3 test_custom_drt.py

Requires opencolorio (pip3 install opencolorio) -- these tests skip with a
clear message rather than failing if it isn't installed, same convention
as test_aces_drt.py/test_printer_lights.py.
"""
import numpy as np

import aces_drt
import custom_drt as cdr


def _skip_if_unavailable():
    if not aces_drt.OCIO_AVAILABLE:
        print("SKIPPED all custom_drt tests -- PyOpenColorIO not installed "
              "(pip3 install opencolorio)")
        return True
    return False


def test_apply_custom_drt_produces_finite_bounded_output():
    rng = np.random.default_rng(0)
    # Wide range including values well over 1.0 -- real ACEScct scene
    # data routinely exceeds that in bright regions, and the whole point
    # of the rendering-space design (see this module's docstring) is
    # handling that without hard-clipping upstream of the tonescale.
    img = rng.uniform(-0.2, 3.0, size=(24, 24, 3)).astype(np.float64)
    out = cdr.apply_custom_drt(img, "ACEScct")
    assert np.isfinite(out).all(), "output must never contain NaN/Inf"
    assert out.min() >= 0.0 and out.max() <= 1.0, "sRGB-encoded output must be in [0,1]"
    print("apply_custom_drt: finite, bounded [0,1] output across a wide input range -- OK")


def test_neutral_pixels_do_not_produce_nan():
    """Regression test: a perfectly neutral pixel gives tTarget=+inf in
    _ray_box_t (correctly -- grey never leaves the gamut), and an earlier
    version of _radial_compress computed inf-inf downstream, which is NaN
    in IEEE float math, not 0 (Tom caught this via ~13% black-pixel
    corruption in a real render before it shipped)."""
    grey_ramp = np.stack([np.linspace(0, 2.0, 50)] * 3, axis=-1).reshape(1, 50, 3)
    out = cdr.apply_custom_drt(grey_ramp, "ACEScct")
    assert not np.isnan(out).any(), "a neutral ramp must never produce NaN"
    print("apply_custom_drt: a perfectly neutral ramp produces no NaN -- OK")


def test_tonescale_preserves_hue_before_highlight_desaturation():
    """Below the highlight-desaturation threshold, the tonescale step is
    norm-preserving by construction -- same RGB ratio in and out. Uses a
    dim, saturated color specifically so its tonescale output stays well
    under DEFAULT_DESAT_START, isolating stage 2 from stage 3."""
    dim_saturated = np.array([[[0.05, 0.02, 0.01]]])
    toned = cdr._tonemap_and_desaturate(
        dim_saturated, cdr.DEFAULT_CONTRAST, cdr.DEFAULT_HEADROOM,
        cdr.DEFAULT_BLEND_PERCENTILE, cdr.DEFAULT_TAIL_WIDTH,
        cdr.DEFAULT_DESAT_START, cdr.DEFAULT_DESAT_END)
    in_ratio = dim_saturated[0, 0] / dim_saturated[0, 0].max()
    out_ratio = toned[0, 0] / toned[0, 0].max()
    assert np.allclose(in_ratio, out_ratio, atol=1e-9), \
        "RGB ratio must be preserved exactly below the desaturation threshold"
    print("_tonemap_and_desaturate: hue-preserving below the desaturation threshold -- OK")


def test_highlight_desaturation_pulls_bright_saturated_colors_toward_neutral():
    """Above DEFAULT_DESAT_START, saturation must decrease -- the whole
    point of stage 3 (see module docstring: a hue-preserving tonescale
    alone keeps full saturation into extreme brightness, which reads as
    neon/plastic instead of rolling to white)."""
    bright_saturated = np.array([[[50.0, 25.0, 5.0]]])
    toned = cdr._tonemap_and_desaturate(
        bright_saturated, cdr.DEFAULT_CONTRAST, cdr.DEFAULT_HEADROOM,
        cdr.DEFAULT_BLEND_PERCENTILE, cdr.DEFAULT_TAIL_WIDTH,
        cdr.DEFAULT_DESAT_START, cdr.DEFAULT_DESAT_END)
    out = toned[0, 0]
    saturation = (out.max() - out.min()) / max(out.max(), 1e-8)
    assert saturation < 0.5, f"a very bright color should desaturate substantially, got {saturation:.3f}"
    print(f"_tonemap_and_desaturate: bright saturated color desaturates to {saturation:.3f} -- OK")


def test_radial_compress_never_exceeds_the_true_mastering_gamut_boundary():
    """The core guarantee of the whole gamut-compression design (see
    module docstring, stage 4): no matter how saturated/bright the
    Rec.2020 input, the compressed result -- ALREADY expressed in the
    mastering gamut's own coordinates by construction (anchor + t*dir),
    not a Rec.2020-space value needing a further conversion -- must land
    AT OR INSIDE the [0,1]^3 cube, even at fullness=1.0 (maximum push
    toward the edge), which by construction still caps at tTarget, never
    past it."""
    rng = np.random.default_rng(1)
    rgb2020 = rng.uniform(0.0, 1.0, size=(200, 3))
    for mastering_display, mastering_matrix in cdr._MASTERING_GAMUT_MATRIX.items():
        for fullness in ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0)):
            compressed = cdr._radial_compress(rgb2020, mastering_matrix, cdr.DEFAULT_IDENTITY_ZONE,
                                               cdr.DEFAULT_TRANSITION_SOFTNESS, fullness)
            assert compressed.min() >= -1e-4, (
                f"{mastering_display}, fullness={fullness}: a channel went below 0 ({compressed.min()})")
            assert compressed.max() <= 1.0 + 1e-4, (
                f"{mastering_display}, fullness={fullness}: a channel exceeded 1.0 ({compressed.max()})")
    print("_radial_compress: never exceeds the true mastering-gamut boundary, "
          "at any fullness, for every mastering display -- OK")


def test_identity_zone_leaves_safely_in_gamut_colors_untouched():
    """A color already well within every mastering gamut at a full 100%
    identity zone (no soft transition either) must come back essentially
    unchanged."""
    in_gamut = np.array([[0.3, 0.25, 0.2]])  # mild, unsaturated -- safely inside any reasonable gamut
    for mastering_display, mastering_matrix in cdr._MASTERING_GAMUT_MATRIX.items():
        compressed = cdr._radial_compress(in_gamut, mastering_matrix, 1.0, 0.0, (0.0, 0.0, 0.0))
        target_direct = np.einsum("ij,...j->...i", mastering_matrix, in_gamut)
        assert np.allclose(compressed, target_direct, atol=1e-6), mastering_display
    print("_radial_compress: a mild in-gamut color is untouched at "
          "identity_zone=1.0, for every mastering display -- OK")


def test_fullness_does_not_blow_up_for_a_ray_that_never_crosses():
    """Regression test for a real bug found while widening _ray_box_t's
    epsilon to be relative (Tom, 2026-08-13): a ray that genuinely never
    crosses the gamut boundary must still return exactly anchor + 1*dir
    (pure identity) even with nonzero fullness -- fullness pushes toward
    tTarget, and for a non-crossing ray tTarget is the 1e6 finite
    stand-in (see _ray_box_t's own docstring), which without the
    finite_target override this function already has would blow up to
    roughly fullness*1e6. Wasn't reachable with the OLD, much tighter
    absolute epsilon (only an almost-exactly-neutral pixel could trigger
    "never crosses" at all); the wider relative epsilon makes it common,
    which is what surfaced this on the GPU side (gpu_custom_drt_shader.py
    had the same gap, found and fixed the same day)."""
    # A pixel bright and saturated enough that its tonescale output sits
    # extremely close to white with only residual saturation -- shaped to
    # land in the "all channels within the relative epsilon" regime.
    bright = np.array([[[60.0, -15.0, 8000.0]]])
    toned = cdr._tonemap_and_desaturate(
        bright, cdr.DEFAULT_CONTRAST, cdr.DEFAULT_HEADROOM, cdr.DEFAULT_BLEND_PERCENTILE,
        cdr.DEFAULT_TAIL_WIDTH, cdr.DEFAULT_DESAT_START, cdr.DEFAULT_DESAT_END)
    compressed = cdr._radial_compress(
        toned, cdr._MASTERING_GAMUT_MATRIX["SDR"], cdr.DEFAULT_IDENTITY_ZONE,
        cdr.DEFAULT_TRANSITION_SOFTNESS, (0.10, 0.0, 0.04))
    assert np.isfinite(compressed).all()
    assert compressed.max() < 2.0, f"fullness blew up a non-crossing ray: {compressed}"
    print("_radial_compress: fullness does not blow up a ray that never "
          "crosses the gamut boundary -- OK")


def test_final_target_matrices_preserve_white():
    """Every non-XYZ Final Target matrix (where not None/identity) must
    have each row summing to ~1.0 -- white (1,1,1) in the mastering
    gamut must map to white (1,1,1) in the Final Target's own RGB gamut,
    whether or not real chromatic adaptation (Theatrical's D65->DCI
    white-point shift) is baked in. See custom_drt.py's own docstring/
    comments on FINAL_TARGETS for why this is a universal property for
    RGB->RGB matrices, not a proxy for "adaptation happened". The "XYZ"
    targets are the one deliberate exception -- RGB (1,1,1) correctly
    maps to the mastering gamut's own whitepoint XYZ tristimulus values
    (not (1,1,1)), verified here against colour's own whitepoint XYZ."""
    import colour

    for mastering_display, options in cdr.FINAL_TARGETS.items():
        for final_target, spec in options.items():
            if spec["matrix"] is None:
                continue
            if final_target == "XYZ":
                continue
            row_sums = spec["matrix"].sum(axis=1)
            assert np.allclose(row_sums, 1.0, atol=1e-5), (mastering_display, final_target, row_sums)
    print("FINAL_TARGETS: every non-identity, non-XYZ matrix preserves white (rows sum to 1) -- OK")

    p3d65_white_xyz = colour.xy_to_XYZ(colour.RGB_COLOURSPACES["P3-D65"].whitepoint)
    hdr_white_result = cdr.FINAL_TARGETS["HDR"]["XYZ"]["matrix"] @ np.array([1.0, 1.0, 1.0])
    assert np.allclose(hdr_white_result, p3d65_white_xyz, atol=1e-4), hdr_white_result

    dci_white_xyz = colour.xy_to_XYZ(colour.RGB_COLOURSPACES["DCI-P3"].whitepoint)
    theatrical_white_result = cdr.FINAL_TARGETS["Theatrical"]["XYZ"]["matrix"] @ np.array([1.0, 1.0, 1.0])
    assert np.allclose(theatrical_white_result, dci_white_xyz, atol=1e-4), theatrical_white_result
    print("FINAL_TARGETS: HDR/Theatrical XYZ targets correctly map RGB white to their "
          "own gamut's real whitepoint XYZ tristimulus -- OK")


def test_apply_custom_drt_rejects_unknown_mastering_display_and_final_target():
    rng = np.random.default_rng(3)
    img = rng.uniform(0.0, 1.0, size=(4, 4, 3))
    try:
        cdr.apply_custom_drt(img, "ACEScct", mastering_display="not a real display")
        raise AssertionError("expected ValueError for an unknown mastering_display")
    except ValueError:
        pass
    try:
        cdr.apply_custom_drt(img, "ACEScct", final_target="not a real target")
        raise AssertionError("expected ValueError for an unknown final_target")
    except ValueError:
        pass
    try:
        # A real final_target, but for the WRONG mastering_display's
        # family (Tom: "Same-family only for now") -- must still reject.
        cdr.apply_custom_drt(img, "ACEScct", mastering_display="HDR", final_target="DCI-P3")
        raise AssertionError("expected ValueError for a cross-family final_target")
    except ValueError:
        pass
    print("apply_custom_drt: raises ValueError for an unknown/cross-family "
          "mastering_display/final_target, rather than silently producing a wrong result -- OK")


def test_pq_encode_matches_published_st2084_reference_points():
    """Verified independently against SMPTE ST 2084's own well-known
    reference points before this module ever shipped with these
    constants (see custom_drt.py's own docstring on _PQ_M1 etc): 100
    nits -> ~0.5081, 203 nits (the ITU-R BT.2408 HDR reference white
    level) -> ~0.5807, 10000 nits (PQ's own ceiling) -> exactly 1.0."""
    checks = [(100, 0.5081), (203, 0.5807), (10000, 1.0)]
    for nits, expected in checks:
        # v=1.0, mastering_brightness=nits -- so absolute_nits = nits exactly.
        got = cdr._pq_encode(np.array([1.0]), nits)[0]
        assert abs(got - expected) < 1e-3, (nits, got, expected)
    print("_pq_encode: matches published ST 2084 reference points "
          "(100/203/10000 nits) -- OK")


def test_pq_output_bounded_and_higher_mastering_brightness_encodes_brighter():
    """A fixed relative tonescale output (e.g. mid-grey-ish 0.5) should
    encode to a HIGHER PQ code value under a brighter mastering_brightness
    -- the whole point of the parameter (see module docstring, "MASTERING
    DISPLAY -> FINAL TARGET"): the same relative code means more real
    nits, so a higher absolute-luminance PQ code. HDR is the only
    mastering display whose EOTF is PQ."""
    rng = np.random.default_rng(4)
    img = rng.uniform(0.0, 2.0, size=(4, 4, 3))
    out_100 = cdr.apply_custom_drt(img, "ACEScct", mastering_display="HDR", mastering_brightness=100)
    out_1000 = cdr.apply_custom_drt(img, "ACEScct", mastering_display="HDR", mastering_brightness=1000)
    assert np.isfinite(out_100).all() and out_100.min() >= 0.0 and out_100.max() <= 1.0
    assert np.isfinite(out_1000).all() and out_1000.min() >= 0.0 and out_1000.max() <= 1.0
    assert (out_1000 >= out_100 - 1e-9).all(), \
        "a brighter mastering_brightness must never encode a DARKER PQ result for the same input"
    assert out_1000.mean() > out_100.mean()
    print("apply_custom_drt: PQ output is bounded, and a brighter mastering_brightness "
          "encodes brighter -- OK")


def test_stack_integration_bake_agrees_with_direct_apply():
    """Same 'grid bake agrees with direct apply' contract every other
    stage kind in this app is held to (see test_stack.py's own
    test_apply_to_image_array_agrees_with_bake_on_identity_grid) --
    exercised across every mastering_display/final_target combination,
    not just the shipped defaults."""
    import stack as st
    import colour

    size = 9
    combos = [{}]  # shipped defaults (SDR -> Rec.709 Gamma 2.4)
    for mastering_display, options in cdr.FINAL_TARGETS.items():
        for final_target in options:
            kwargs = {"mastering_display": mastering_display, "final_target": final_target}
            if mastering_display == "HDR":
                kwargs["mastering_brightness"] = 1000
            combos.append(kwargs)

    for kwargs in combos:
        stack = st.Stack(size=size, stages=[st.make_custom_drt_stage("ACEScct", **kwargs)])
        baked = st.bake_stack_to_lut3d(stack)
        identity_grid = colour.LUT3D(size=size).table
        direct = st.apply_stack_to_image_array(stack, identity_grid)
        assert np.allclose(direct, baked.table, atol=1e-6), kwargs
    print("stack.py: custom_drt stage bake agrees with direct apply on the identity grid, "
          "across every mastering_display/final_target combination -- OK")


if __name__ == "__main__":
    if not _skip_if_unavailable():
        test_apply_custom_drt_produces_finite_bounded_output()
        test_neutral_pixels_do_not_produce_nan()
        test_tonescale_preserves_hue_before_highlight_desaturation()
        test_highlight_desaturation_pulls_bright_saturated_colors_toward_neutral()
        test_radial_compress_never_exceeds_the_true_mastering_gamut_boundary()
        test_identity_zone_leaves_safely_in_gamut_colors_untouched()
        test_fullness_does_not_blow_up_for_a_ray_that_never_crosses()
        test_final_target_matrices_preserve_white()
        test_apply_custom_drt_rejects_unknown_mastering_display_and_final_target()
        test_pq_encode_matches_published_st2084_reference_points()
        test_pq_output_bounded_and_higher_mastering_brightness_encodes_brighter()
        test_stack_integration_bake_agrees_with_direct_apply()
        print()
        print("All custom_drt tests passed.")
