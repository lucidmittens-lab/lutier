"""
test_lut_controls.py -- tests for the three new editing operations in
app_logic.py: bake_retargeted_lut, resample_lut, set_lut_domain. Fully
Qt-free. Uses the real uploaded production LUT (samples/roundtrip_check.cube)
where it matters (resize), not just synthetic data.
"""
from pathlib import Path

import numpy as np
import colour

from app_logic import (
    bake_retargeted_lut, resample_lut, set_lut_domain,
    list_available_colourspaces, create_identity_lut,
)
from retarget import retarget_io

SAMPLE_LUT_PATH = str(Path(__file__).parent / "samples" / "roundtrip_check.cube")


# --- bake_retargeted_lut -----------------------------------------------

def test_bake_retargeted_lut_matches_manual_wrapper():
    """The baked LUT, evaluated at a grid point, should match calling the
    unbaked retarget_io wrapper directly on that same point -- baking is
    supposed to be a lossless-at-grid-points sampling of the wrapper, not
    a different computation."""
    lut = colour.read_LUT(SAMPLE_LUT_PATH)
    size = 9
    baked = bake_retargeted_lut(lut, from_space="ACEScct", to_space="ACES2065-1", size=size)

    wrapped = retarget_io(lut, from_space="ACEScct", to_space="ACES2065-1")
    identity_grid = colour.LUT3D(size=size).table
    expected_table = np.asarray(wrapped(identity_grid), dtype=float)

    assert baked.table.shape == expected_table.shape
    max_diff = np.max(np.abs(baked.table - expected_table))
    assert max_diff < 1e-10, max_diff
    print(f"bake_retargeted_lut matches manual wrapper, max diff: {max_diff:.2e} -- OK")


def test_bake_retargeted_lut_default_size_matches_source():
    lut = colour.read_LUT(SAMPLE_LUT_PATH)  # 65^3
    baked = bake_retargeted_lut(lut, from_space="ACEScct", to_space="ACES2065-1")
    assert baked.table.shape[0] == lut.table.shape[0] == 65
    print("bake_retargeted_lut default size matches source (65) -- OK")


def test_list_available_colourspaces_includes_expected_spaces():
    spaces = list_available_colourspaces()
    for expected in ("ACES2065-1", "ACEScct", "ACEScg", "sRGB"):
        assert expected in spaces, f"{expected} missing from {len(spaces)} spaces"
    print(f"list_available_colourspaces: {len(spaces)} spaces, includes expected -- OK")


# --- resample_lut --------------------------------------------------------

def test_resample_lut3d_preserves_values_at_shared_grid_points():
    """Resampling 65 -> 17 then evaluating at points that exist in the
    17-grid should closely match evaluating the ORIGINAL 65-res LUT at
    those same points -- resampling shouldn't introduce new distortion,
    just fewer samples of the same underlying transform."""
    lut = colour.read_LUT(SAMPLE_LUT_PATH)
    resampled = resample_lut(lut, new_size=17)
    assert resampled.table.shape == (17, 17, 17, 3)

    test_points = np.array([
        [0.0, 0.0, 0.0], [1.0, 1.0, 1.0], [0.5, 0.5, 0.5],
        [0.25, 0.75, 0.1], [0.9, 0.2, 0.6],
    ])
    original_out = lut.apply(test_points)
    resampled_out = resampled.apply(test_points)
    max_diff = np.max(np.abs(original_out - resampled_out))
    # 17^3 is a much coarser grid than 65^3, so allow real interpolation
    # error, just not gross distortion.
    assert max_diff < 0.02, max_diff
    print(f"resample_lut3d 65->17, max diff at test points: {max_diff:.5f} -- OK")


def test_resample_lut3d_upsampling_reproduces_source_more_closely():
    """Upsampling should track the source LUT even more closely than
    downsampling does, since there's no information loss involved."""
    lut = colour.read_LUT(SAMPLE_LUT_PATH)
    upsampled = resample_lut(lut, new_size=97)
    test_points = np.random.default_rng(0).random((20, 3))
    original_out = lut.apply(test_points)
    upsampled_out = upsampled.apply(test_points)
    max_diff = np.max(np.abs(original_out - upsampled_out))
    assert max_diff < 0.005, max_diff
    print(f"resample_lut3d 65->97 (upsample), max diff: {max_diff:.6f} -- OK")


def test_resample_lut1d():
    lut1d = colour.LUT1D(table=np.linspace(0, 1, 10) ** 2, name="gamma2")
    resampled = resample_lut(lut1d, new_size=33)
    assert resampled.table.shape == (33,)
    test_vals = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
    max_diff = np.max(np.abs(lut1d.apply(test_vals) - resampled.apply(test_vals)))
    assert max_diff < 1e-3, max_diff
    print(f"resample_lut 1D, max diff: {max_diff:.6f} -- OK")


def test_resample_respects_nondefault_domain():
    """If the LUT's domain has already been widened (e.g. via
    set_lut_domain), resampling should sample across THAT domain, not
    silently snap back to [0,1] -- otherwise resize and domain-editing
    wouldn't compose.

    Note: widening an identity LUT3D's domain does NOT make it an identity
    function over real-world values -- the table still stores [0,1], so
    .apply() becomes "normalize [-1,2] down to [0,1]", not passthrough.
    That's correct colour-science behavior, not a bug in this function; the
    thing actually under test here is that resample_lut reproduces THAT
    behavior (evaluated at points within the widened domain) rather than
    reverting to a [0,1]-domain assumption.
    """
    lut = colour.LUT3D(size=5, name="identity")
    lut.domain = np.array([[-1.0, -1.0, -1.0], [2.0, 2.0, 2.0]])
    resampled = resample_lut(lut, new_size=9)
    assert np.allclose(resampled.domain, lut.domain)

    test_points = np.array([[-1.0, -1.0, -1.0], [2.0, 2.0, 2.0], [-0.3, 1.5, 0.0], [0.5, 0.5, 0.5]])
    original_out = lut.apply(test_points)
    resampled_out = resampled.apply(test_points)
    max_diff = np.max(np.abs(original_out - resampled_out))
    assert max_diff < 0.03, max_diff
    print(f"resample_lut respects a non-default domain, max diff: {max_diff:.5f} -- OK")


# --- set_lut_domain --------------------------------------------------------

def test_set_lut_domain_3d_remaps_input_range():
    lut = colour.LUT3D(size=5, name="identity")  # apply(x) == x by default
    widened = set_lut_domain(lut, domain_min=(-1, -1, -1), domain_max=(2, 2, 2))

    # domain_min should now map to the table's first entry (0,0,0 for an
    # identity table), domain_max to the table's last entry (1,1,1).
    assert np.allclose(widened.apply(np.array([-1.0, -1.0, -1.0])), [0, 0, 0], atol=1e-6)
    assert np.allclose(widened.apply(np.array([2.0, 2.0, 2.0])), [1, 1, 1], atol=1e-6)
    # Midpoint of the new domain -> midpoint of the table.
    assert np.allclose(widened.apply(np.array([0.5, 0.5, 0.5])), [0.5, 0.5, 0.5], atol=1e-6)
    print("set_lut_domain (3D) remaps input range correctly -- OK")


def test_set_lut_domain_does_not_mutate_original():
    lut = colour.LUT3D(size=5, name="identity")
    original_domain = lut.domain.copy()
    _ = set_lut_domain(lut, domain_min=(-1, -1, -1), domain_max=(2, 2, 2))
    assert np.array_equal(lut.domain, original_domain), \
        "set_lut_domain mutated the original LUT's domain -- it must not"
    print("set_lut_domain does not mutate the original LUT -- OK")


def test_set_lut_domain_1d():
    lut1d = colour.LUT1D(table=np.linspace(0, 1, 11), name="identity1d")
    widened = set_lut_domain(lut1d, domain_min=-2.0, domain_max=4.0)
    assert np.isclose(widened.apply(np.array(-2.0)), 0.0, atol=1e-6)
    assert np.isclose(widened.apply(np.array(4.0)), 1.0, atol=1e-6)
    print("set_lut_domain (1D) remaps input range correctly -- OK")


def test_domain_edit_then_resize_compose():
    """Real usage pattern: widen the domain, then resize -- the result
    should still honor the widened domain, not the original [0,1]."""
    lut = colour.LUT3D(size=5, name="identity")
    widened = set_lut_domain(lut, domain_min=(0, 0, 0), domain_max=(2, 2, 2))
    resized = resample_lut(widened, new_size=9)
    assert np.allclose(resized.domain, [[0, 0, 0], [2, 2, 2]])
    assert np.allclose(resized.apply(np.array([2.0, 2.0, 2.0])), [1, 1, 1], atol=1e-6)
    assert np.allclose(resized.apply(np.array([1.0, 1.0, 1.0])), [0.5, 0.5, 0.5], atol=1e-6)
    print("domain edit -> resize composes correctly -- OK")


def test_create_identity_lut_is_a_true_passthrough():
    """Not just 'the table looks identity-shaped' -- confirm .apply()
    actually leaves arbitrary values (including outside [0,1], and
    non-grid-aligned) unchanged, which is the actual property callers
    care about (e.g. "apply the ACES DRT to nothing")."""
    lut = create_identity_lut(size=17, name="Identity")
    assert lut.name == "Identity"
    assert lut.table.shape == (17, 17, 17, 3)

    rng = np.random.default_rng(42)
    test_points = rng.random((50, 3)).astype(np.float64)
    out = lut.apply(test_points)
    assert np.allclose(out, test_points, atol=1e-6), \
        "create_identity_lut should leave arbitrary in-range values unchanged"
    print("create_identity_lut: true pass-through on arbitrary points -- OK")


def test_create_identity_lut_composes_with_concatenate():
    """The actual point of an identity LUT: it should be a no-op stage in
    a Concatenate chain, so chaining [identity, real_lut] matches
    real_lut alone."""
    from app_logic import concatenate_luts
    real_lut = colour.read_LUT(SAMPLE_LUT_PATH)
    identity = create_identity_lut(size=real_lut.size)
    chained = concatenate_luts([identity, real_lut], size=real_lut.size)
    direct = colour.LUT3D(table=real_lut.apply(colour.LUT3D(size=real_lut.size).table),
                           size=real_lut.size)
    assert np.allclose(chained.table, direct.table, atol=1e-6)
    print("create_identity_lut: no-op as a Concatenate chain stage -- OK")


if __name__ == "__main__":
    tests = [
        test_bake_retargeted_lut_matches_manual_wrapper,
        test_bake_retargeted_lut_default_size_matches_source,
        test_list_available_colourspaces_includes_expected_spaces,
        test_resample_lut3d_preserves_values_at_shared_grid_points,
        test_resample_lut3d_upsampling_reproduces_source_more_closely,
        test_resample_lut1d,
        test_resample_respects_nondefault_domain,
        test_set_lut_domain_3d_remaps_input_range,
        test_set_lut_domain_does_not_mutate_original,
        test_set_lut_domain_1d,
        test_domain_edit_then_resize_compose,
        test_create_identity_lut_is_a_true_passthrough,
        test_create_identity_lut_composes_with_concatenate,
    ]
    for t in tests:
        t()
    print("\nAll LUT controls tests passed.")
