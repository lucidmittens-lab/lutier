"""
test_concatenate.py -- tests for app_logic.concatenate_luts. Fully Qt-free.
Uses the real uploaded production LUT (samples/roundtrip_check.cube) for
the real-data check, plus synthetic non-commutative transforms to catch
ordering bugs a symmetric test would miss.
"""
from pathlib import Path

import numpy as np
import colour

from app_logic import concatenate_luts

SAMPLE_LUT_PATH = str(Path(__file__).parent / "samples" / "roundtrip_check.cube")


class Gain:
    """y = x * factor -- deliberately not commutative with Offset below."""
    def __init__(self, factor, name="gain"):
        self.factor = factor
        self.name = name

    def apply(self, RGB):
        return np.asarray(RGB, dtype=float) * self.factor


class Offset:
    """y = x + amount."""
    def __init__(self, amount, name="offset"):
        self.amount = amount
        self.name = name

    def apply(self, RGB):
        return np.asarray(RGB, dtype=float) + self.amount


def test_concatenate_composes_in_order():
    """gain-then-offset should differ from offset-then-gain -- if these
    matched, order wouldn't actually be respected."""
    gain = Gain(2.0)
    offset = Offset(0.1)

    gain_then_offset = concatenate_luts([gain, offset], size=5)
    offset_then_gain = concatenate_luts([offset, gain], size=5)

    test_point = np.array([0.3, 0.3, 0.3])
    # (0.3 * 2.0) + 0.1 = 0.7
    assert np.allclose(gain_then_offset.apply(test_point), [0.7, 0.7, 0.7], atol=1e-6)
    # (0.3 + 0.1) * 2.0 = 0.8
    assert np.allclose(offset_then_gain.apply(test_point), [0.8, 0.8, 0.8], atol=1e-6)
    assert not np.allclose(gain_then_offset.apply(test_point), offset_then_gain.apply(test_point))
    print("concatenate_luts respects order (gain->offset != offset->gain) -- OK")


def test_concatenate_matches_manual_composition():
    """The baked combined LUT, evaluated anywhere, should match manually
    chaining .apply() calls directly -- baking is supposed to be a
    faithful sampling of the composed function, within grid-interpolation
    tolerance."""
    gain = Gain(1.4)
    offset = Offset(-0.05)
    combined = concatenate_luts([gain, offset], size=17)

    test_points = np.array([[0.1, 0.2, 0.3], [0.9, 0.5, 0.05], [0.0, 1.0, 0.5]])
    manual = offset.apply(gain.apply(test_points))
    baked = combined.apply(test_points)
    max_diff = np.max(np.abs(manual - baked))
    assert max_diff < 1e-4, max_diff
    print(f"concatenate_luts matches manual composition, max diff: {max_diff:.2e} -- OK")


def test_concatenate_single_lut_is_passthrough_of_that_lut():
    gain = Gain(1.7)
    combined = concatenate_luts([gain], size=9)
    test_point = np.array([0.2, 0.4, 0.6])
    assert np.allclose(combined.apply(test_point), gain.apply(test_point), atol=1e-6)
    print("concatenate_luts with a single LUT reproduces that LUT -- OK")


def test_concatenate_empty_list_raises():
    try:
        concatenate_luts([])
        raise AssertionError("expected ValueError for empty lut list")
    except ValueError:
        print("concatenate_luts([]) raises ValueError as expected -- OK")


def test_concatenate_default_size_matches_largest_input():
    small = colour.LUT3D(size=9, name="small")
    big = colour.LUT3D(size=65, name="big")
    combined = concatenate_luts([small, big])
    assert combined.table.shape[0] == 65
    print("concatenate_luts default size matches the largest LUT3D input (65) -- OK")


def test_concatenate_real_lut_with_synthetic_stage():
    """Real production LUT chained with a synthetic gain stage -- checks
    the real-data path end to end, not just synthetic-on-synthetic."""
    real_lut = colour.read_LUT(SAMPLE_LUT_PATH)
    gain = Gain(0.9)
    combined = concatenate_luts([real_lut, gain], size=33)

    test_points = np.array([[0.2, 0.5, 0.8], [0.6, 0.1, 0.3]])
    manual = gain.apply(real_lut.apply(test_points))
    baked = combined.apply(test_points)
    max_diff = np.max(np.abs(manual - baked))
    # 33^3 combined grid vs the real LUT's own 65^3 -- allow real
    # interpolation error, not just float noise.
    assert max_diff < 0.01, max_diff
    print(f"concatenate_luts with real production LUT + synthetic stage, max diff: {max_diff:.5f} -- OK")


if __name__ == "__main__":
    tests = [
        test_concatenate_composes_in_order,
        test_concatenate_matches_manual_composition,
        test_concatenate_single_lut_is_passthrough_of_that_lut,
        test_concatenate_empty_list_raises,
        test_concatenate_default_size_matches_largest_input,
        test_concatenate_real_lut_with_synthetic_stage,
    ]
    for t in tests:
        t()
    print("\nAll concatenate tests passed.")
