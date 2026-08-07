"""
clf_adapter.py -- bridges colour-science's LUT objects (LUT1D, LUT3D,
LUTOperatorMatrix, LUTSequence) to colour_clf_io's CLF ProcessList/node
representation, so anything colour-science can read (.cube, .3dl, Sony SPI
formats, etc.) becomes convertible to/from CLF.

IMPORTANT, verified by direct testing (see test_clf_adapter.py):
colour_clf_io's own CLFProcessList.apply() is correct at exact grid-corner
inputs but WRONG for general (non-corner) 3D LUT interpolation -- confirmed
against colour-science's independently-correct LUT3D.apply() on identical
data. The XML it reads/writes is not affected (array data round-trips
exactly, byte for byte); only colour_clf_io's own in-Python execution path
is unreliable. No open issue on their tracker matches this as of this
writing, so treat it as an undocumented limitation.

Consequence for this codebase: use colour_clf_io ONLY for CLF read/write
(clf.read_clf / clf.write_clf / node construction). Never use
CLFProcessList.apply() for preview/execution -- use colour-science's own
.apply() on the equivalent LUT1D/LUT3D/LUTSequence object instead, which is
what colour_to_clf_node()/clf_node_to_colour() round trip against.
"""
import numpy as np
import colour
import colour_clf_io as clf


def matrix_to_clf_node(matrix_3x3, node_id="matrix", name=None, description=None):
    """colour-science 3x3 matrix (numpy array) -> CLF Matrix node.

    Verified dim convention: a plain 3x3 matrix uses Array(dim=(3, 3)) --
    NOT (3, 3, 3). Confirmed by executing the resulting node through
    CLFProcessList.apply() and matching a direct numpy matmul exactly.
    """
    matrix_3x3 = np.asarray(matrix_3x3, dtype=float)
    assert matrix_3x3.shape == (3, 3), f"expected 3x3 matrix, got {matrix_3x3.shape}"
    array = clf.Array(values=list(matrix_3x3.flatten()), dim=(3, 3))
    return clf.Matrix(
        id=node_id, name=name,
        in_bit_depth=clf.BitDepth.f32, out_bit_depth=clf.BitDepth.f32,
        description=[description] if description else None,
        array=array,
    )


def colour_lut3d_to_clf_node(lut3d: "colour.LUT3D", node_id="lut3d",
                              interpolation=clf.Interpolation3D.TRILINEAR):
    """colour.LUT3D -> CLF LUT3D node.

    Verified: array dim = lut3d.table.shape (size, size, size, 3) directly
    matches colour-science's own table layout -- confirmed by checking all
    5 axis-aligned corner values (000/100/010/001/111) match exactly
    between colour.LUT3D.apply() and the CLF node's array data, and by
    round-tripping the full array through write_clf/read_clf.

    Do not evaluate this node via CLFProcessList.apply() -- see module
    docstring. Use lut3d.apply() (the original colour-science object) for
    any actual execution/preview; this function only produces the node for
    writing into a CLF file.
    """
    array = clf.Array(values=list(lut3d.table.flatten()), dim=lut3d.table.shape)
    return clf.LUT3D(
        id=node_id, name=lut3d.name,
        in_bit_depth=clf.BitDepth.f32, out_bit_depth=clf.BitDepth.f32,
        description=[], array=array,
        half_domain=False, raw_halfs=False,
        interpolation=interpolation,
    )


def process_list_wrapping(nodes, list_id="process_list", name=None,
                           compatible_clf_version="3.0"):
    """Convenience: wrap a list of CLF process nodes in a minimal valid
    ProcessList, ready for clf.write_clf()."""
    return clf.ProcessList(
        id=list_id, compatible_CLF_version=compatible_clf_version, name=name,
        inverse_of=None, description=[], input_descriptor="", output_descriptor="",
        process_nodes=nodes, info=None,
    )


def colour_lut1d_to_clf_node(lut1d: "colour.LUT1D", node_id="lut1d",
                              interpolation=clf.Interpolation1D.LINEAR):
    """colour.LUT1D -> CLF LUT1D node. Array dim is (size, 1) for a single
    shared curve across channels -- verified by round-tripping through
    write_clf/read_clf and checking the reparsed array against the source
    table."""
    array = clf.Array(values=list(lut1d.table.flatten()), dim=(lut1d.table.shape[0], 1))
    return clf.LUT1D(
        id=node_id, name=lut1d.name,
        in_bit_depth=clf.BitDepth.f32, out_bit_depth=clf.BitDepth.f32,
        description=[], array=array,
        half_domain=False, raw_halfs=False,
        interpolation=interpolation,
    )


# ---------------------------------------------------------------------------
# Reverse direction: CLF node/ProcessList -> execution, WITHOUT going through
# colour_clf_io's own CLFProcessList.apply() (see module docstring -- it is
# wrong for non-corner LUT3D interpolation). Each node type below is applied
# using colour-science's independently-verified math instead.
# ---------------------------------------------------------------------------

def _array_to_matrix3x3(node) -> np.ndarray:
    values = np.asarray(node.array.values, dtype=float)
    dim = node.array.dim
    if tuple(dim) == (3, 3):
        return values.reshape(3, 3)
    raise NotImplementedError(
        f"Matrix node array dim {dim} not supported yet (only plain 3x3 handled)")


def apply_clf_node(node, RGB):
    """Apply a single CLF process node to RGB values using colour-science's
    verified math for the node's operation type. Raises NotImplementedError
    for node types not yet supported rather than silently producing a wrong
    result."""
    RGB = np.asarray(RGB, dtype=float)

    if isinstance(node, clf.Matrix):
        M = _array_to_matrix3x3(node)
        return np.einsum("ij,...j->...i", M, RGB)

    if isinstance(node, clf.LUT3D):
        size = node.array.dim[0]
        table = np.asarray(node.array.values, dtype=float).reshape(node.array.dim)
        lut3d = colour.LUT3D(table=table, size=size, name=node.name or "")
        return lut3d.apply(RGB)

    if isinstance(node, clf.LUT1D):
        table = np.asarray(node.array.values, dtype=float).reshape(-1)
        lut1d = colour.LUT1D(table=table, name=node.name or "")
        return lut1d.apply(RGB)

    if isinstance(node, clf.Range):
        min_in = node.min_in_value if node.min_in_value is not None else 0.0
        max_in = node.max_in_value if node.max_in_value is not None else 1.0
        min_out = node.min_out_value if node.min_out_value is not None else 0.0
        max_out = node.max_out_value if node.max_out_value is not None else 1.0
        scale = (max_out - min_out) / (max_in - min_in)
        result = (RGB - min_in) * scale + min_out
        return np.clip(result, min(min_out, max_out), max(min_out, max_out))

    raise NotImplementedError(
        f"apply_clf_node: node type {type(node).__name__} not yet supported "
        f"(ASC_CDL, Log, Exponent still TODO)"
    )


def apply_process_list(process_list: "clf.ProcessList", RGB):
    """Apply every node in a ProcessList in order, using apply_clf_node for
    each -- this is the correct, verified alternative to
    CLFProcessList.apply()."""
    result = np.asarray(RGB, dtype=float)
    for node in process_list.process_nodes:
        result = apply_clf_node(node, result)
    return result


def bake_process_list_to_lut3d(process_list: "clf.ProcessList", size=33,
                                name=None) -> "colour.LUT3D":
    """Sample an arbitrary CLF ProcessList (any chain of supported nodes) on
    an identity grid to produce a single flat colour.LUT3D. Needed for: (1)
    visualizing a multi-node CLF as one 3D lattice, and (2) exporting a
    node-chain CLF to a monolithic format like .cube that can't express a
    chain. Uses apply_process_list, not colour_clf_io's apply(), so this is
    safe even for chains containing LUT3D nodes."""
    identity_grid = colour.LUT3D(size=size).table
    baked_table = apply_process_list(process_list, identity_grid)
    return colour.LUT3D(
        table=baked_table, size=size,
        name=name or process_list.name or process_list.id or "baked",
    )
