"""
test_yrgb_curve.py -- tests for app_logic.compute_yrgb_curve, the backend
for the Graph panel's 1D mode (per Tom: "1D YRGB viewers", worked into the
former 3D Viewer panel rather than a separate Scopes mode -- see
lut_viewer_3d.py). Fully Qt-free, run directly: python3 -m tests.test_yrgb_curve
"""
import numpy as np
import colour

from app_logic import compute_yrgb_curve, create_identity_lut
from scopes import LUMA_COEFFICIENTS_REC709


def test_identity_lut_default_domain_is_a_straight_line():
    """An identity LUT with the default [0,1] domain should produce
    input==output for every channel -- the simplest possible sanity
    check that the ramp and the LUT application line up."""
    lut = create_identity_lut(size=17)
    curve = compute_yrgb_curve(lut, num_samples=33)

    assert np.allclose(curve["input_r"], curve["r"], atol=1e-6)
    assert np.allclose(curve["input_g"], curve["g"], atol=1e-6)
    assert np.allclose(curve["input_b"], curve["b"], atol=1e-6)
    print("compute_yrgb_curve: identity LUT with default domain is a "
          "straight input==output line on all three channels -- OK")


def test_ramp_spans_full_0_1_range_by_default():
    lut = create_identity_lut(size=9)
    curve = compute_yrgb_curve(lut, num_samples=11)

    for ch in ("input_r", "input_g", "input_b"):
        assert np.isclose(curve[ch][0], 0.0)
        assert np.isclose(curve[ch][-1], 1.0)
    print("compute_yrgb_curve: default-domain ramp spans exactly "
          "[0, 1] on every channel -- OK")


def test_ramp_follows_luts_own_declared_domain():
    """The actual point of this function, per Tom -- the X axis (and the
    values fed into the LUT) must follow the Stack's domain settings, not
    assume [0,1] -- see this app's HDR/scene-linear support elsewhere
    (grid size/domain, the 3D Viewer's camera-centering fix)."""
    lut = colour.LUT3D(size=5)
    lut.domain = np.array([[-2.0, 0.0, 10.0], [4.0, 1.0, 20.0]])
    curve = compute_yrgb_curve(lut, num_samples=5)

    assert np.allclose(curve["input_r"], [-2.0, -0.5, 1.0, 2.5, 4.0])
    assert np.allclose(curve["input_g"], [0.0, 0.25, 0.5, 0.75, 1.0])
    assert np.allclose(curve["input_b"], [10.0, 12.5, 15.0, 17.5, 20.0])
    print("compute_yrgb_curve: per-channel ramp correctly follows the "
          "LUT's own asymmetric, non-[0,1] domain -- OK")


def test_falls_back_to_0_1_when_lut_has_no_domain_attribute():
    class _NoDomainLUT:
        def apply(self, rgb):
            return rgb  # pass-through

    curve = compute_yrgb_curve(_NoDomainLUT(), num_samples=5)
    assert np.allclose(curve["input_r"], [0.0, 0.25, 0.5, 0.75, 1.0])
    print("compute_yrgb_curve: an object with no .domain attribute at "
          "all falls back to [0, 1] instead of raising -- OK")


def test_y_matches_rec709_luma_of_the_output():
    lut = create_identity_lut(size=9)
    curve = compute_yrgb_curve(lut, num_samples=13)

    expected_y = (curve["r"] * LUMA_COEFFICIENTS_REC709[0]
                  + curve["g"] * LUMA_COEFFICIENTS_REC709[1]
                  + curve["b"] * LUMA_COEFFICIENTS_REC709[2])
    assert np.allclose(curve["y"], expected_y)
    print("compute_yrgb_curve: 'y' matches the same Rec.709 luma "
          "coefficients the Histogram/Waveform scopes use -- OK")


def test_a_real_grade_produces_distinct_non_identity_curves():
    """Against a real production LUT (not synthetic identity), confirm
    the curves actually reflect a non-trivial grade -- output should
    differ meaningfully from input somewhere along the ramp."""
    lut = colour.io.read_LUT("samples/roundtrip_check.cube")
    curve = compute_yrgb_curve(lut, num_samples=64)

    assert not np.allclose(curve["input_r"], curve["r"], atol=1e-3)
    print("compute_yrgb_curve: a real production LUT produces a "
          "genuinely non-identity response curve -- OK")


def test_num_samples_controls_array_length():
    lut = create_identity_lut(size=9)
    curve = compute_yrgb_curve(lut, num_samples=17)
    for key in ("input_r", "input_g", "input_b", "r", "g", "b", "y"):
        assert curve[key].shape == (17,)
    print("compute_yrgb_curve: every returned array has length "
          "num_samples -- OK")


if __name__ == "__main__":
    test_identity_lut_default_domain_is_a_straight_line()
    test_ramp_spans_full_0_1_range_by_default()
    test_ramp_follows_luts_own_declared_domain()
    test_falls_back_to_0_1_when_lut_has_no_domain_attribute()
    test_y_matches_rec709_luma_of_the_output()
    test_a_real_grade_produces_distinct_non_identity_curves()
    test_num_samples_controls_array_length()
    print("All YRGB curve tests passed.")
