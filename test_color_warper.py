"""Tests for color_warper.py -- run directly:
QT_QPA_PLATFORM=offscreen python3 test_color_warper.py"""
import math

import numpy as np

import aces_drt
import color_warper as cw

# Same convention as test_printer_lights.py: a linear working space
# (identity cctf) and a display-referred gamma space, present under the
# same name in both the colour-science and OCIO ACES registries.
LINEAR_SPACE = "ACEScg"
SRGB_LIKE_SPACE = "sRGB Encoded Rec.709 (sRGB)" if aces_drt.OCIO_AVAILABLE else "sRGB"


def _sample_identity_points():
    """A non-trivial (3-point) all-identity (h==h0, c==c0) points list --
    color_warper.default_points() used to provide this, but WarpierWheel
    no longer seeds any default points (per Tom), so this stays purely a
    local test fixture, not production API. Only used where a test needs
    to exercise the REAL per-point code path (not just the trivially-true
    empty-list case) while still expecting an exact no-op."""
    hues = (35.0, 142.5, 250.0)
    chroma = 0.15
    return [{"h0": h, "c0": chroma, "h": h, "c": chroma} for h in hues]


# ---- OKLab/OKLCh math, against published reference values -------------

def test_white_and_black_match_published_oklab_reference():
    """Per Ottosson's own OKLab writeup: white (1,1,1 linear) -> L=1,
    a=0, b=0; black -> L=0, a=0, b=0 -- the two fixed points every OKLab
    implementation should agree on exactly."""
    white_lab = cw.linear_srgb_to_oklab(np.array([1.0, 1.0, 1.0]))
    assert abs(white_lab[0] - 1.0) < 1e-6
    assert abs(white_lab[1]) < 1e-6 and abs(white_lab[2]) < 1e-6
    black_lab = cw.linear_srgb_to_oklab(np.array([0.0, 0.0, 0.0]))
    assert np.allclose(black_lab, 0.0, atol=1e-9)
    print("linear_srgb_to_oklab: white -> (1,0,0), black -> (0,0,0), "
          "matching the published OKLab reference points -- OK")


def test_oklab_round_trip():
    rng = np.random.default_rng(0)
    rgb = rng.uniform(0.02, 0.98, size=(50, 3))
    lab = cw.linear_srgb_to_oklab(rgb)
    rgb2 = cw.oklab_to_linear_srgb(lab)
    assert np.max(np.abs(rgb - rgb2)) < 1e-5
    print("linear_srgb_to_oklab / oklab_to_linear_srgb: round trip within "
          "1e-5 (forward/inverse are independently published constants, "
          "not exact algebraic inverses -- see this module's docstring) -- OK")


def test_oklch_round_trip():
    rng = np.random.default_rng(1)
    lab = rng.uniform(-0.3, 0.3, size=(30, 3))
    lab[:, 0] = rng.uniform(0.1, 0.9, size=30)
    L, C, H = cw.oklab_to_oklch(lab)
    lab2 = cw.oklch_to_oklab(L, C, H)
    assert np.allclose(lab, lab2, atol=1e-9)
    print("oklab_to_oklch / oklch_to_oklab: exact round trip -- OK")


# ---- falloff / warp blend math -----------------------------------------

def test_falloff_weight_is_one_at_zero_distance():
    assert cw.falloff_weight(0.0, wake=30, dropoff=0.0) == 1.0
    assert cw.falloff_weight(0.0, wake=30, dropoff=1.0) == 1.0
    print("falloff_weight(0, ...): full strength exactly at a point's "
          "own hue, for any dropoff -- OK")


def test_falloff_weight_decreases_with_distance():
    near = cw.falloff_weight(5.0, wake=30, dropoff=0.0)
    far = cw.falloff_weight(40.0, wake=30, dropoff=0.0)
    assert 0.0 <= far < near <= 1.0
    print("falloff_weight: monotonically decreases with angular distance -- OK")


def test_hard_dropoff_stays_higher_within_the_wake_than_soft():
    """Per Tom: dropoff controls "how hard they follow" -- a hard
    setting should keep MORE strength across most of the wake radius
    (a plateau) than a soft (plain Gaussian) one, at the same offset
    well inside the radius."""
    offset = 20.0  # inside a wake of 30
    soft = cw.falloff_weight(offset, wake=30, dropoff=0.0)
    hard = cw.falloff_weight(offset, wake=30, dropoff=1.0)
    assert hard > soft
    print(f"falloff_weight({offset}, wake=30): hard dropoff ({hard:.3f}) "
          f"stays stronger than soft ({soft:.3f}) within the wake radius -- OK")


def test_warp_hue_chroma_exact_at_a_points_own_anchor():
    """A pixel exactly at a point's own h0 should be warped to exactly
    that point's dragged (h, c) target, for any wake/dropoff (weight=1,
    every other point's contribution is what it is, but with only one
    point in play the blend is exact)."""
    points = [{"h0": 100.0, "c0": 0.1, "h": 130.0, "c": 0.25}]
    H_out, C_out = cw.warp_hue_chroma(np.array([100.0]), np.array([0.1]), points,
                                       wake=25, dropoff=0.3)
    assert abs(H_out[0] - 130.0) < 1e-9
    assert abs(C_out[0] - 0.25) < 1e-9
    print("warp_hue_chroma: a pixel at a point's own anchor hue lands "
          "exactly on that point's dragged target -- OK")


def test_warp_hue_chroma_no_points_is_noop():
    H = np.array([10.0, 200.0])
    C = np.array([0.1, 0.2])
    H_out, C_out = cw.warp_hue_chroma(H, C, [])
    assert np.array_equal(H_out, H) and np.array_equal(C_out, C)
    print("warp_hue_chroma: no points -> exact no-op -- OK")


def test_warp_hue_chroma_far_outside_wake_is_unaffected():
    points = [{"h0": 0.0, "c0": 0.1, "h": 90.0, "c": 0.1}]
    H_out, _ = cw.warp_hue_chroma(np.array([180.0]), np.array([0.1]), points,
                                   wake=15, dropoff=0.0)
    assert abs(H_out[0] - 180.0) < 1e-6, "a hue 180 deg from a narrow-wake point should be untouched"
    print("warp_hue_chroma: a hue far outside the wake radius is left alone -- OK")


def test_warp_hue_chroma_default_chroma_wake_still_pulls_the_whole_spoke():
    """Documents TODAY's default behavior (per Tom's own repro: "if i
    move a point on a given hue angle, one half way from center... it
    shifts that whole angle there, meaning all points along the line from
    center to the edge") -- with chroma_wake left at its wide-open
    default, a point dropped near center (c0=0.05) still strongly affects
    a pixel at the SAME hue but far out in chroma (C=0.35), because the
    old falloff never measured chroma distance at all. This is the
    documented, deliberate no-op default (see DEFAULT_CHROMA_WAKE's own
    comment) -- the NEXT test proves narrowing chroma_wake actually fixes
    it."""
    points = [{"h0": 35.0, "c0": 0.05, "h": 185.0, "c": 0.05}]  # +150 deg drag, near center
    H = np.array([35.0, 35.0])
    C = np.array([0.05, 0.35])  # at the point's own anchor chroma, and far out at the rim
    H_out, _ = cw.warp_hue_chroma(H, C, points, wake=20.0, dropoff=0.0)
    deltas = ((H_out - H + 180.0) % 360.0) - 180.0
    assert deltas[1] > 100.0, "with the default wide-open chroma_wake, the far-chroma pixel still gets pulled almost the full delta"
    print("warp_hue_chroma: default chroma_wake reproduces the 'whole "
          "spoke' behavior exactly as reported -- OK")


def test_warp_hue_chroma_tight_chroma_wake_fixes_the_whole_spoke_bug():
    """The actual fix: narrowing chroma_wake makes the SAME point from
    the test above leave the far-chroma pixel essentially untouched,
    while a pixel at the point's own anchor chroma is still fully
    affected -- proving the new axis genuinely gates by how far a
    pixel's OWN chroma sits from where the point was dropped, independent
    of the hue axis (which stays fully satisfied for both pixels here)."""
    points = [{"h0": 35.0, "c0": 0.05, "h": 185.0, "c": 0.05}]
    H = np.array([35.0, 35.0])
    C = np.array([0.05, 0.35])
    H_out, _ = cw.warp_hue_chroma(H, C, points, wake=20.0, dropoff=0.0,
                                   chroma_wake=0.05, chroma_dropoff=0.3)
    deltas = ((H_out - H + 180.0) % 360.0) - 180.0
    assert abs(deltas[0] - 150.0) < 1e-6, "at the point's own anchor chroma, the full delta still applies"
    assert abs(deltas[1]) < 1.0, "far out in chroma, a tight chroma_wake leaves the pixel essentially untouched"
    print("warp_hue_chroma: a tight chroma_wake stops a point from "
          "pulling its own entire radial spoke -- OK")


def test_warp_hue_chroma_single_point_does_not_rotate_the_whole_circle():
    """Regression test for a real bug (per Tom: "the whole graph will
    spin when i make and move a custom point"): with exactly ONE active
    point, a plain weighted average degenerates to that single point's
    own raw hue_delta applied almost everywhere (w*x/w == x for any
    w > 0, regardless of how small w is), completely ignoring wake --
    confirmed directly: a lone point with wake=20 was still shifting a
    hue 55 degrees away by its own FULL, undiminished delta. The fix
    blends an implicit identity contribution (weight = max(0, 1 -
    weight_sum)) so coverage that isn't accounted for by real points
    falls back toward a true no-op instead. This must hold with a SINGLE
    point specifically -- multiple points already masked the bug by each
    contributing their own competing weight."""
    points = [{"h0": 35.0, "c0": 0.15, "h": 185.0, "c": 0.15}]  # +150 deg drag
    H = np.array([35.0, 55.0, 180.0, 300.0])
    C = np.full_like(H, 0.15)
    H_out, _ = cw.warp_hue_chroma(H, C, points, wake=20.0, dropoff=0.0)
    deltas = ((H_out - H + 180.0) % 360.0) - 180.0

    assert abs(deltas[0] - 150.0) < 1e-6, "at the point's own anchor, the full delta still applies"
    assert abs(deltas[1]) < abs(deltas[0]) - 1.0, (
        "55 deg away (well outside wake=20) must be shifted LESS than the anchor itself, "
        "not by the same full +150 the old buggy math produced")
    assert abs(deltas[2]) < 1.0, "145 deg away should be essentially untouched"
    assert abs(deltas[3]) < 1.0, "on the far side of the circle should be essentially untouched"
    print("warp_hue_chroma: a single point's effect correctly tapers with "
          "distance instead of rigidly rotating the entire hue circle -- OK")


def test_points_are_identity():
    assert cw.points_are_identity([])
    assert cw.points_are_identity(_sample_identity_points())
    moved = _sample_identity_points()
    moved[0]["h"] += 5.0
    assert not cw.points_are_identity(moved)
    print("points_are_identity: true for an empty list AND for a "
          "non-trivial all-identity list, false once one point is "
          "dragged -- OK")


# ---- working-colourspace-aware apply -----------------------------------

def test_apply_no_points_is_exact_noop():
    rng = np.random.default_rng(2)
    img = rng.random((6, 6, 3))
    out = cw.apply_color_warper_in_working_space(img, LINEAR_SPACE, [])
    assert np.array_equal(out, img)
    print("apply_color_warper_in_working_space: no points -> exact no-op, "
          "skips the color-space round trip entirely -- OK")


def test_apply_identity_points_is_exact_noop_for_every_working_space():
    rng = np.random.default_rng(3)
    img = rng.random((6, 6, 3)) * 0.9 + 0.05
    points = _sample_identity_points()
    for space in (LINEAR_SPACE, SRGB_LIKE_SPACE, cw.DEFAULT_WORKING_COLOURSPACE):
        out = cw.apply_color_warper_in_working_space(img, space, points)
        assert np.array_equal(out, img), (
            f"identity points should be an exact no-op for {space!r}")
    print("apply_color_warper_in_working_space: a non-trivial all-"
          "identity points list is an exact no-op for linear/gamma/log "
          "working spaces alike -- OK")


def test_apply_a_moved_point_changes_pixels_near_its_own_hue():
    """A pure gray pixel has no hue to warp (C=0, weight-independent),
    so use a moderately saturated warm pixel and confirm the warp
    actually moves it when a point sits on/near its hue."""
    img = np.array([[[0.6, 0.35, 0.2]]])  # a warm, moderately saturated pixel
    points = [{"h0": 35.0, "c0": 0.1, "h": 55.0, "c": 0.1}]
    out = cw.apply_color_warper_in_working_space(
        img, LINEAR_SPACE, points, wake=40, dropoff=0.0)
    assert not np.allclose(out, img)
    print("apply_color_warper_in_working_space: a point placed near a "
          "pixel's own hue visibly warps it -- OK")


def test_apply_matches_manual_pipeline_for_the_reference_space():
    """Working directly in REFERENCE_LINEAR_SPACE should skip any real
    color-space conversion work (src==dst), so the result should match
    hand-driving linear_srgb_to_oklab/warp_hue_chroma/oklab_to_linear_srgb
    directly."""
    img = np.array([[[0.5, 0.25, 0.15], [0.1, 0.4, 0.3]]])
    points = [{"h0": 20.0, "c0": 0.05, "h": 20.0, "c": 0.12}]
    out = cw.apply_color_warper_in_working_space(
        img, cw.REFERENCE_LINEAR_SPACE, points, wake=45, dropoff=0.2)

    lab = cw.linear_srgb_to_oklab(img)
    L, C, H = cw.oklab_to_oklch(lab)
    H2, C2 = cw.warp_hue_chroma(H, C, points, wake=45, dropoff=0.2)
    expected = cw.oklab_to_linear_srgb(cw.oklch_to_oklab(L, C2, H2))
    assert np.allclose(out, expected, atol=1e-6)
    print("apply_color_warper_in_working_space: matches the manual OKLCh "
          "pipeline exactly when working_colourspace IS the reference "
          "linear space -- OK")


# ---- gamut boundary / idealized reference grid -------------------------

def test_decode_working_colourspace_to_oklch_matches_manual_pipeline():
    """Working directly in REFERENCE_LINEAR_SPACE should skip any real
    color-space conversion (src==dst), so the result should match hand-
    driving linear_srgb_to_oklab/oklab_to_oklch directly -- same
    convention test_apply_matches_manual_pipeline_for_the_reference_space
    already uses for the full apply function."""
    img = np.array([[[0.5, 0.25, 0.15], [0.1, 0.4, 0.3]]])
    L, C, H = cw.decode_working_colourspace_to_oklch(img, cw.REFERENCE_LINEAR_SPACE)
    expected_L, expected_C, expected_H = cw.oklab_to_oklch(cw.linear_srgb_to_oklab(img))
    assert np.allclose(L, expected_L) and np.allclose(C, expected_C) and np.allclose(H, expected_H)
    print("decode_working_colourspace_to_oklch: matches the manual "
          "linear_srgb_to_oklab/oklab_to_oklch pipeline when "
          "working_colourspace IS the reference linear space -- OK")


def test_apply_color_warper_still_matches_manual_pipeline_after_the_refactor():
    """Regression guard for the decode_working_colourspace_to_oklch
    extraction -- apply_color_warper_in_working_space must still behave
    byte-identically to before across a REAL (non-reference) working
    colourspace, not just the reference space the other test above
    already covers."""
    img = np.array([[[0.5, 0.25, 0.15], [0.1, 0.4, 0.3]]])
    points = [{"h0": 20.0, "c0": 0.05, "h": 20.0, "c": 0.12}]
    out = cw.apply_color_warper_in_working_space(
        img, SRGB_LIKE_SPACE, points, wake=45, dropoff=0.2)

    L, C, H = cw.decode_working_colourspace_to_oklch(img, SRGB_LIKE_SPACE)
    H2, C2 = cw.warp_hue_chroma(H, C, points, wake=45, dropoff=0.2)
    warped_linear = cw.oklab_to_linear_srgb(cw.oklch_to_oklab(L, C2, H2))
    if aces_drt.OCIO_AVAILABLE:
        expected = aces_drt.convert_colourspace(warped_linear, cw.REFERENCE_LINEAR_SPACE, SRGB_LIKE_SPACE)
    else:
        import colorspace
        expected = colorspace.convert(warped_linear, cw.REFERENCE_LINEAR_SPACE, SRGB_LIKE_SPACE)
    assert np.allclose(out, expected, atol=1e-6)
    print("apply_color_warper_in_working_space: still matches the "
          "manual pipeline for a real (non-reference) working "
          "colourspace after extracting decode_working_colourspace_to_"
          "oklch -- OK")


def test_is_in_gamut_mid_gray_true_absurd_chroma_false():
    assert bool(cw.is_in_gamut(0.5, 0.0, 0.0))
    assert not bool(cw.is_in_gamut(0.5, 5.0, 0.0))
    print("is_in_gamut: neutral mid-gray is in gamut, absurd chroma is "
          "not -- OK")


def test_is_in_gamut_vectorizes_over_arrays():
    l = np.array([0.5, 0.5])
    c = np.array([0.0, 5.0])
    h = np.array([0.0, 0.0])
    result = cw.is_in_gamut(l, c, h)
    assert result.shape == (2,)
    assert bool(result[0]) and not bool(result[1])
    print("is_in_gamut: works elementwise over arrays, not just scalars "
          "-- OK")


def test_warped_gamut_blob_points_shape_and_default_count():
    positions, colors = cw.warped_gamut_blob_points([])
    assert positions.shape == (26 * 16, 3)  # default 26 hues x 16 polar-angle steps
    assert colors.shape == (26 * 16, 3)
    print("warped_gamut_blob_points: default 26x16 grid -> 416 points, "
          "shapes match -- OK")


def test_warped_gamut_blob_points_respects_step_counts():
    positions, colors = cw.warped_gamut_blob_points([], hue_steps=4, theta_steps=3)
    assert positions.shape == (12, 3)
    assert colors.shape == (12, 3)
    print("warped_gamut_blob_points: hue_steps/theta_steps control the "
          "point count directly -- OK")


def test_warped_gamut_blob_points_colors_are_valid_rgb():
    _, colors = cw.warped_gamut_blob_points([])
    assert colors.min() >= 0.0 and colors.max() <= 1.0
    print("warped_gamut_blob_points: every color is clipped to the valid "
          "[0,1] display range -- OK")


def test_warped_gamut_blob_points_white_is_at_the_center():
    """The whole point of the spherical embedding (per Tom: "white at the
    center") -- points near the poles (theta near 0 or pi, i.e. L near 1
    or 0) should have near-zero chroma, so their cartesian position
    should land close to the coordinate origin regardless of hue --
    unlike a straight z=lightness cylinder, where black/white spread
    along an axis instead of collapsing to a single center point."""
    positions, _ = cw.warped_gamut_blob_points([], hue_steps=8, theta_steps=9)
    radius = np.linalg.norm(positions, axis=1)
    # theta_steps=9 over [0,pi] puts the very first row exactly at theta=0
    # (the white pole) -- every hue at that row should collapse near the
    # origin.
    first_row = radius[:8]
    assert np.all(first_row < 0.05)
    print("warped_gamut_blob_points: near-white points collapse toward "
          "the shape's own center regardless of hue -- OK")


def test_warped_gamut_blob_points_is_deterministic_when_unwarped():
    """A pure function of its own arguments -- no randomness, no
    dependency on any loaded image or app state -- so two calls with
    identity points must return identical results."""
    positions_a, colors_a = cw.warped_gamut_blob_points([])
    positions_b, colors_b = cw.warped_gamut_blob_points([])
    assert np.array_equal(positions_a, positions_b)
    assert np.array_equal(colors_a, colors_b)
    print("warped_gamut_blob_points: deterministic across repeated calls "
          "-- OK")


def test_warped_gamut_blob_points_reacts_to_warp_points():
    """Per Tom: "that graph should react to what the warper is doing" --
    real warp points should move the blob away from its unwarped shape."""
    identity_positions, _ = cw.warped_gamut_blob_points([])
    points = [{"h0": cw.GREEN_HUE, "c0": 0.15, "h": cw.GREEN_HUE + 40.0, "c": 0.3}]
    warped_positions, _ = cw.warped_gamut_blob_points(points, wake=30.0, dropoff=0.2)
    assert not np.allclose(identity_positions, warped_positions)
    print("warped_gamut_blob_points: real warp points move the blob away "
          "from its unwarped identity shape -- OK")


def test_warped_idealized_sphere_points_shape_and_default_count():
    positions, colors = cw.warped_idealized_sphere_points([])
    assert positions.shape == (26 * 16, 3)
    assert colors.shape == (26 * 16, 3)
    print("warped_idealized_sphere_points: default 26x16 grid -> 416 "
          "points, shapes match -- OK")


def test_warped_idealized_sphere_points_is_a_perfect_sphere_when_unwarped():
    """Per Tom: 'replace the volume accurate blob with the sphere' -- a
    FIXED chroma in every direction, unlike the gamut blob's own
    per-direction real boundary -- so every unwarped point's radius
    should be identical (a true sphere), not varying with hue/lightness.
    Unlike the gamut blob, the poles do NOT collapse toward the origin
    here (real max chroma naturally shrinks to ~0 near black/white, but
    this shape's chroma is a constant regardless of lightness) -- the
    "white at the center" framing is about the coordinate SYSTEM (chroma
    0 is the origin), not a literal rendered point on this particular
    hollow-shell shape."""
    positions, _ = cw.warped_idealized_sphere_points([])
    radii = np.linalg.norm(positions, axis=1)
    assert np.allclose(radii, cw.DEFAULT_IDEALIZED_SPHERE_CHROMA, atol=1e-9)
    print("warped_idealized_sphere_points: every unwarped point sits at "
          f"the exact same radius ({cw.DEFAULT_IDEALIZED_SPHERE_CHROMA}) "
          "-- a true sphere, not a gamut-shaped blob -- OK")


def test_warped_idealized_sphere_points_colors_are_valid_rgb():
    _, colors = cw.warped_idealized_sphere_points([])
    assert colors.min() >= 0.0 and colors.max() <= 1.0
    print("warped_idealized_sphere_points: every color is clipped to the "
          "valid [0,1] display range -- OK")


def test_warped_idealized_sphere_points_reacts_to_warp_points():
    identity_positions, _ = cw.warped_idealized_sphere_points([])
    points = [{"h0": cw.GREEN_HUE, "c0": 0.15, "h": cw.GREEN_HUE + 40.0, "c": 0.3}]
    warped_positions, _ = cw.warped_idealized_sphere_points(points, wake=30.0, dropoff=0.2)
    assert not np.allclose(identity_positions, warped_positions)
    print("warped_idealized_sphere_points: real warp points move the "
          "sphere away from its unwarped identity shape -- OK")


if __name__ == "__main__":
    test_white_and_black_match_published_oklab_reference()
    test_oklab_round_trip()
    test_oklch_round_trip()
    test_falloff_weight_is_one_at_zero_distance()
    test_falloff_weight_decreases_with_distance()
    test_hard_dropoff_stays_higher_within_the_wake_than_soft()
    test_warp_hue_chroma_exact_at_a_points_own_anchor()
    test_warp_hue_chroma_no_points_is_noop()
    test_warp_hue_chroma_far_outside_wake_is_unaffected()
    test_warp_hue_chroma_default_chroma_wake_still_pulls_the_whole_spoke()
    test_warp_hue_chroma_tight_chroma_wake_fixes_the_whole_spoke_bug()
    test_warp_hue_chroma_single_point_does_not_rotate_the_whole_circle()
    test_points_are_identity()
    test_apply_no_points_is_exact_noop()
    test_apply_identity_points_is_exact_noop_for_every_working_space()
    test_apply_a_moved_point_changes_pixels_near_its_own_hue()
    test_apply_matches_manual_pipeline_for_the_reference_space()
    test_decode_working_colourspace_to_oklch_matches_manual_pipeline()
    test_apply_color_warper_still_matches_manual_pipeline_after_the_refactor()
    test_is_in_gamut_mid_gray_true_absurd_chroma_false()
    test_is_in_gamut_vectorizes_over_arrays()
    test_warped_gamut_blob_points_shape_and_default_count()
    test_warped_gamut_blob_points_respects_step_counts()
    test_warped_gamut_blob_points_colors_are_valid_rgb()
    test_warped_gamut_blob_points_white_is_at_the_center()
    test_warped_gamut_blob_points_is_deterministic_when_unwarped()
    test_warped_gamut_blob_points_reacts_to_warp_points()
    test_warped_idealized_sphere_points_shape_and_default_count()
    test_warped_idealized_sphere_points_is_a_perfect_sphere_when_unwarped()
    test_warped_idealized_sphere_points_colors_are_valid_rgb()
    test_warped_idealized_sphere_points_reacts_to_warp_points()
    print()
    print("All color_warper tests passed.")
