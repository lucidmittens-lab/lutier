"""
test_retarget.py -- validates retarget.py against an independently-assembled
manual composition, not just internal self-consistency.

Run with: python3 test_retarget.py
"""
import numpy as np
import colour

from colorspace import convert
from retarget import retarget_io


class SimpleLMT:
    """Stand-in for a real LMT until a real one is available to test against.
    Pivots around ACEScct's 18%-grey code value (0.413588402492, the known
    Academy reference point) with a contrast push -- deliberately not an
    identity, so a bug that silently no-ops would be caught."""

    def apply(self, RGB):
        pivot = 0.413588402492
        contrast = 1.15
        return (np.asarray(RGB) - pivot) * contrast + pivot


def test_rgb_to_rgb_round_trip():
    test_val = np.array([0.35, 0.18, 0.05])
    to_cct = convert(test_val, "ACES2065-1", "ACEScct")
    back = convert(to_cct, "ACEScct", "ACES2065-1")
    assert np.allclose(test_val, back, atol=1e-6), "AP0->ACEScct->AP0 round trip failed"


def test_acescct_reference_grey_point():
    # Known Academy reference value: 18% grey through ACEScct log encoding.
    assert abs(colour.models.log_encoding_ACEScct(0.18) - 0.413588402492) < 1e-6


def test_retarget_matches_manual_composition():
    lmt = SimpleLMT()
    retargeted = retarget_io(lmt, from_space="ACEScct", to_space="ACES2065-1")

    test_ap0_input = np.array([0.22, 0.30, 0.09])

    manual_cct = convert(test_ap0_input, "ACES2065-1", "ACEScct")
    manual_cct_out = lmt.apply(manual_cct)
    manual_ap0_out = convert(manual_cct_out, "ACEScct", "ACES2065-1")

    wrapper_ap0_out = retargeted(test_ap0_input)

    assert np.allclose(manual_ap0_out, wrapper_ap0_out, atol=1e-8), (
        f"retarget_io result {wrapper_ap0_out} does not match manual "
        f"composition {manual_ap0_out}"
    )


def test_retarget_actually_differs_from_naive_direct_apply():
    # Guards against a wrapper that accidentally becomes a no-op passthrough.
    lmt = SimpleLMT()
    retargeted = retarget_io(lmt, from_space="ACEScct", to_space="ACES2065-1")
    test_ap0_input = np.array([0.22, 0.30, 0.09])

    naive_direct = lmt.apply(test_ap0_input)
    wrapper_ap0_out = retargeted(test_ap0_input)

    assert not np.allclose(naive_direct, wrapper_ap0_out, atol=1e-4), (
        "retargeted output should differ from naively applying the LMT "
        "directly to un-converted values -- if these match, the space "
        "conversion isn't actually doing anything"
    )


if __name__ == "__main__":
    tests = [
        test_rgb_to_rgb_round_trip,
        test_acescct_reference_grey_point,
        test_retarget_matches_manual_composition,
        test_retarget_actually_differs_from_naive_direct_apply,
    ]
    for t in tests:
        t()
        print(f"PASS: {t.__name__}")
    print("\nAll tests passed.")
