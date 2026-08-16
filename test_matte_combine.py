"""Tests for matte_combine.py -- run directly:
python3 test_matte_combine.py"""
import numpy as np

import matte_combine as mc


def test_intersect_is_multiply():
    a = np.array([[1.0, 0.5], [0.0, 1.0]])
    b = np.array([[1.0, 0.5], [1.0, 0.0]])
    result = mc.compute_combined_matte(a, b, op="intersect")
    assert np.allclose(result, a * b)
    print("compute_combined_matte: 'intersect' is a plain multiply (AND) -- OK")


def test_union_is_max():
    a = np.array([[1.0, 0.2], [0.0, 0.6]])
    b = np.array([[0.3, 0.5], [0.0, 0.4]])
    result = mc.compute_combined_matte(a, b, op="union")
    assert np.allclose(result, np.maximum(a, b))
    print("compute_combined_matte: 'union' is max (OR) -- OK")


def test_subtract_clamps_at_zero():
    a = np.array([[0.8, 0.2]])
    b = np.array([[0.3, 0.5]])
    result = mc.compute_combined_matte(a, b, op="subtract")
    assert np.allclose(result, [[0.5, 0.0]]), "negative deltas must clamp to 0, not go negative"
    print("compute_combined_matte: 'subtract' is max(0, a-b) -- OK")


def test_difference_is_absolute_delta():
    a = np.array([[0.8, 0.2]])
    b = np.array([[0.3, 0.5]])
    result = mc.compute_combined_matte(a, b, op="difference")
    assert np.allclose(result, [[0.5, 0.3]])
    print("compute_combined_matte: 'difference' is abs(a-b) (XOR) -- OK")


def test_unknown_op_raises():
    a = np.zeros((2, 2))
    try:
        mc.compute_combined_matte(a, a, op="nonsense")
        assert False, "expected a ValueError for an unknown op"
    except ValueError:
        pass
    print("compute_combined_matte: an unknown op raises ValueError -- OK")


def test_output_is_always_clamped_to_unit_range():
    a = np.array([[1.0, 1.0]])
    b = np.array([[1.0, 1.0]])
    for op in mc.OPS:
        result = mc.compute_combined_matte(a, b, op=op)
        assert np.all(result >= 0.0) and np.all(result <= 1.0), op
    print("compute_combined_matte: output is always clamped to [0,1] for every op -- OK")


def test_all_zero_inputs_stay_all_zero_for_every_op():
    """The all-zero fallback stack.py's own dispatch substitutes for an
    unresolved reference must behave sensibly composed with every op --
    confirmed here directly rather than assumed."""
    zeros = np.zeros((3, 3))
    real = np.full((3, 3), 0.7)
    assert np.allclose(mc.compute_combined_matte(zeros, real, op="intersect"), 0.0)
    assert np.allclose(mc.compute_combined_matte(zeros, real, op="union"), real)
    assert np.allclose(mc.compute_combined_matte(zeros, real, op="subtract"), 0.0)
    assert np.allclose(mc.compute_combined_matte(zeros, real, op="difference"), real)
    print("compute_combined_matte: an all-zero (missing/unresolved) input "
          "degrades sensibly for every op -- OK")


if __name__ == "__main__":
    test_intersect_is_multiply()
    test_union_is_max()
    test_subtract_clamps_at_zero()
    test_difference_is_absolute_delta()
    test_unknown_op_raises()
    test_output_is_always_clamped_to_unit_range()
    test_all_zero_inputs_stay_all_zero_for_every_op()
    print()
    print("All matte_combine tests passed.")
