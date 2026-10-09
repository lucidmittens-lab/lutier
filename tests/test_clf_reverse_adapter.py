"""
test_clf_reverse_adapter.py -- validates the CLF -> colour-science execution
path (apply_clf_node / apply_process_list / bake_process_list_to_lut3d),
which deliberately bypasses colour_clf_io's own buggy CLFProcessList.apply().

Run with: python3 -m tests.test_clf_reverse_adapter
"""
import numpy as np
import colour
import colour_clf_io as clf

from clf_adapter import (
    matrix_to_clf_node, colour_lut3d_to_clf_node, colour_lut1d_to_clf_node,
    process_list_wrapping, apply_clf_node, apply_process_list,
    bake_process_list_to_lut3d,
)


def test_matrix_node_apply():
    M = colour.matrix_RGB_to_RGB(
        colour.RGB_COLOURSPACES["ACEScg"], colour.RGB_COLOURSPACES["ACES2065-1"])
    node = matrix_to_clf_node(M, node_id="m1")
    test_val = np.array([0.5, 0.25, 0.75])
    assert np.allclose(apply_clf_node(node, test_val), M @ test_val, atol=1e-6)


def test_lut3d_node_apply_matches_colour_science_at_noncorner_point():
    lut3d = colour.LUT3D(size=9, name="t")
    lut3d.table = np.clip(lut3d.table * 0.85 + 0.03, 0, 1)
    node = colour_lut3d_to_clf_node(lut3d, node_id="l3")
    test_pt = np.array([0.37, 0.61, 0.22])  # deliberately not a grid node
    assert np.allclose(apply_clf_node(node, test_pt), lut3d.apply(test_pt), atol=1e-6)


def test_lut1d_node_apply():
    lut1d = colour.LUT1D(size=10)
    lut1d.table = np.clip(lut1d.table * 0.8 + 0.1, 0, 1)
    node = colour_lut1d_to_clf_node(lut1d, node_id="l1")
    test_val = np.array([0.42, 0.42, 0.42])
    assert np.allclose(apply_clf_node(node, test_val), lut1d.apply(test_val), atol=1e-6)


def test_range_node_apply():
    node = clf.Range(
        id="r1", name=None, in_bit_depth=clf.BitDepth.f32, out_bit_depth=clf.BitDepth.f32,
        description=None, min_in_value=0.0, max_in_value=1.0,
        min_out_value=0.0, max_out_value=2.0, style=None)
    result = apply_clf_node(node, np.array([0.5, 0.5, 0.5]))
    assert np.allclose(result, [1.0, 1.0, 1.0], atol=1e-6)


def test_process_list_chains_multiple_nodes_correctly():
    M = colour.matrix_RGB_to_RGB(
        colour.RGB_COLOURSPACES["ACEScg"], colour.RGB_COLOURSPACES["ACES2065-1"])
    mnode = matrix_to_clf_node(M, node_id="m1")

    lut3d = colour.LUT3D(size=9, name="t")
    lut3d.table = np.clip(lut3d.table * 0.85 + 0.03, 0, 1)
    l3node = colour_lut3d_to_clf_node(lut3d, node_id="l3")

    pl = process_list_wrapping([mnode, l3node], list_id="chain_test")
    test_val = np.array([0.5, 0.25, 0.75])

    chained_result = apply_process_list(pl, test_val)
    manual_chained = lut3d.apply(M @ test_val)
    assert np.allclose(chained_result, manual_chained, atol=1e-6)


def test_bake_same_size_reproduces_source_exactly():
    """Baking a single-LUT3D-node ProcessList at its own native grid size
    must reproduce the exact source table -- no interpolation error should
    be introduced when the output grid coincides with the input grid."""
    lut3d = colour.LUT3D(size=17, name="t")
    lut3d.table = np.clip(lut3d.table * 0.85 + 0.03, 0, 1)
    node = colour_lut3d_to_clf_node(lut3d, node_id="l3")
    pl = process_list_wrapping([node], list_id="bake_test")

    baked = bake_process_list_to_lut3d(pl, size=17)
    assert np.allclose(baked.table, lut3d.table, atol=1e-6)


if __name__ == "__main__":
    tests = [
        test_matrix_node_apply,
        test_lut3d_node_apply_matches_colour_science_at_noncorner_point,
        test_lut1d_node_apply,
        test_range_node_apply,
        test_process_list_chains_multiple_nodes_correctly,
        test_bake_same_size_reproduces_source_exactly,
    ]
    for t in tests:
        t()
        print(f"PASS: {t.__name__}")
    print("\nAll tests passed.")
