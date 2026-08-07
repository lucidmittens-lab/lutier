"""
test_clf_adapter.py -- validates clf_adapter.py, and documents the
colour_clf_io LUT3D interpolation bug this project routes around.

Run with: python3 test_clf_adapter.py
"""
import numpy as np
import colour
import colour_clf_io as clf

from clf_adapter import matrix_to_clf_node, colour_lut3d_to_clf_node, process_list_wrapping


def test_matrix_node_functionally_correct():
    """Matrix nodes ARE safe to execute via CLFProcessList.apply() --
    verified against a direct numpy matmul of the same matrix."""
    M = colour.matrix_RGB_to_RGB(
        colour.RGB_COLOURSPACES["ACEScg"], colour.RGB_COLOURSPACES["ACES2065-1"])
    node = matrix_to_clf_node(M, node_id="m1", name="AP1_to_AP0")
    pl = process_list_wrapping([node])
    proc = clf.CLFProcessList(pl)

    test_val = [0.5, 0.25, 0.75]
    clf_result = proc.apply(test_val)
    manual_result = M @ np.array(test_val)
    assert np.allclose(clf_result, manual_result, atol=1e-6)


def test_lut3d_node_array_data_is_correct():
    """The LUT3D node's array DATA is correct and round-trips exactly
    through write_clf/read_clf -- this is what actually ends up in the
    delivered CLF file, and it's fine."""
    size = 5
    lut3d = colour.LUT3D(size=size, name="Test LUT")
    lut3d.table = np.clip(lut3d.table * 0.9 + 0.02, 0, 1)

    node = colour_lut3d_to_clf_node(lut3d, node_id="l1")
    pl = process_list_wrapping([node])

    xml = clf.write_clf(pl)
    pl2 = clf.read_clf(xml)
    reparsed = np.array(pl2.process_nodes[0].array.values).reshape(lut3d.table.shape)
    assert np.allclose(reparsed, lut3d.table, atol=1e-6)


def test_lut3d_node_corners_match_but_interior_does_not_KNOWN_BUG():
    """Documents the colour_clf_io execution bug so a future library update
    that fixes it will make this test fail loudly (a good thing) rather
    than the bug silently going unnoticed. Corners must match; if the
    interior ALSO starts matching, colour_clf_io's apply() has been fixed
    and clf_adapter.py's docstring/warnings can be relaxed."""
    size = 5
    lut3d = colour.LUT3D(size=size, name="Test LUT")
    lut3d.table = np.clip(lut3d.table * 0.9 + 0.02, 0, 1)

    node = colour_lut3d_to_clf_node(lut3d, node_id="l1")
    pl = process_list_wrapping([node])
    proc = clf.CLFProcessList(pl)

    for corner in ([0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 1]):
        expected = lut3d.apply(np.array(corner, dtype=float))
        actual = proc.apply(corner)
        assert np.allclose(expected, actual, atol=1e-6), f"corner {corner} mismatch"

    # known-broken interior point -- CLF's apply() gives a wrong answer here
    interior = [0.42, 0.6, 0.15]
    expected_interior = lut3d.apply(np.array(interior))
    actual_interior = proc.apply(interior)
    still_broken = not np.allclose(expected_interior, actual_interior, atol=1e-3)
    assert still_broken, (
        "colour_clf_io's LUT3D interpolation appears FIXED -- "
        "re-check whether clf_adapter.py's execution-path warning is still needed"
    )


if __name__ == "__main__":
    tests = [
        test_matrix_node_functionally_correct,
        test_lut3d_node_array_data_is_correct,
        test_lut3d_node_corners_match_but_interior_does_not_KNOWN_BUG,
    ]
    for t in tests:
        t()
        print(f"PASS: {t.__name__}")
    print("\nAll tests passed.")
