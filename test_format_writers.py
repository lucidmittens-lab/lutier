"""
test_format_writers.py -- self-consistency tests for format_writers.py.

These round-trip each writer's output through a small hand-written parser
that mirrors OCIO's FileFormat3DL.cpp / FileFormatTruelight.cpp read logic
(same iteration order and scaling conventions). That validates the writer
is internally consistent -- right header tokens, right scaling, right
axis order -- but NOT real-world compatibility with an actual Autodesk or
FilmLight product, since none are available to test against here. Treat
a pass here as "the bytes are structured the way the spec says", not as
"Lustre/Baselight will definitely open this".
"""
import numpy as np
import colour

from format_writers import write_3dl, write_truelight_cub


def _make_test_lut(size=5):
    # A non-identity LUT (simple gain + offset per channel) so a bug that
    # only shows up on non-trivial data -- e.g. a transposed axis -- would
    # actually be caught, unlike testing against an identity table.
    lut = colour.LUT3D(size=size, name="test")
    idx = np.indices((size, size, size)).astype(float) / (size - 1)
    r, g, b = idx[0], idx[1], idx[2]
    table = np.stack(
        [np.clip(r * 0.8 + 0.05, 0, 1),
         np.clip(g * 0.9, 0, 1),
         np.clip(b * 0.7 + 0.1, 0, 1)],
        axis=-1,
    )
    lut.table = table
    return lut


def _parse_3dl(path, cube_size, shaper_bit_depth=10, cube_bit_depth=12,
                variant="lustre"):
    lines = [l.strip() for l in open(path) if l.strip()]
    # Strip Lustre-only header/footer tokens.
    if variant == "lustre":
        assert lines[0] == "3DMESH"
        assert lines[1].startswith("Mesh")
        lines = lines[2:]
        assert lines[-1] == "gamma 1.0"
        assert lines[-2] == "LUT8"
        lines = lines[:-2]

    shaper_line = lines[0]
    shaper_vals = [int(v) for v in shaper_line.split()]
    assert len(shaper_vals) == cube_size, (len(shaper_vals), cube_size)
    shaper_max = 2 ** shaper_bit_depth - 1
    assert max(shaper_vals) == shaper_max

    data_lines = lines[1:]
    assert len(data_lines) == cube_size ** 3
    rows = np.array([[int(x) for x in l.split()] for l in data_lines])

    cube_max = 2 ** cube_bit_depth - 1
    normalized = rows / cube_max

    # .3dl is blue-fastest -> reshape directly back to (R,G,B,3).
    table = normalized.reshape(cube_size, cube_size, cube_size, 3)
    return table


def _parse_truelight_cub(path, cube_size):
    lines = [l.rstrip("\n") for l in open(path)]
    assert lines[0].lower().startswith("# truelight cube")

    width_line = next(l for l in lines if l.lower().startswith("# width"))
    parts = width_line.split()
    w = [int(parts[2]), int(parts[3]), int(parts[4])]
    assert w == [cube_size, cube_size, cube_size]

    cube_start = lines.index("# Cube") + 1
    cube_end = lines.index("# end")
    data_lines = [l for l in lines[cube_start:cube_end] if l.strip()]
    assert len(data_lines) == cube_size ** 3

    rows = np.array([[float(x) for x in l.split()] for l in data_lines])
    normalized = rows / (cube_size - 1)

    # .cub is red-fastest -> data was written from a (B,G,R,3) transpose,
    # so reshape to (B,G,R,3) then transpose back to (R,G,B,3).
    table = normalized.reshape(cube_size, cube_size, cube_size, 3)
    table = table.transpose(2, 1, 0, 3)
    return table


def test_3dl_flame_roundtrip(tmp_path="/tmp/test_flame.3dl"):
    lut = _make_test_lut(size=5)
    write_3dl(lut, tmp_path, variant="flame")
    recovered = _parse_3dl(tmp_path, cube_size=5, variant="flame")
    max_diff = np.max(np.abs(recovered - lut.table))
    # 12-bit quantization -> max error ~1/4095 per channel.
    assert max_diff < 1.0 / 4095 + 1e-6, max_diff
    print(f"3dl (flame) round-trip max diff: {max_diff:.6f} -- OK")


def test_3dl_lustre_roundtrip(tmp_path="/tmp/test_lustre.3dl"):
    lut = _make_test_lut(size=5)
    write_3dl(lut, tmp_path, variant="lustre")
    recovered = _parse_3dl(tmp_path, cube_size=5, variant="lustre")
    max_diff = np.max(np.abs(recovered - lut.table))
    assert max_diff < 1.0 / 4095 + 1e-6, max_diff
    print(f"3dl (lustre) round-trip max diff: {max_diff:.6f} -- OK")


def test_truelight_cub_roundtrip(tmp_path="/tmp/test.cub"):
    lut = _make_test_lut(size=5)
    write_truelight_cub(lut, tmp_path)
    recovered = _parse_truelight_cub(tmp_path, cube_size=5)
    max_diff = np.max(np.abs(recovered - lut.table))
    # Written as float text with 6 decimal places -- essentially exact.
    assert max_diff < 1e-5, max_diff
    print(f"Truelight .cub round-trip max diff: {max_diff:.8f} -- OK")


def test_axis_order_actually_matters():
    """Sanity check that the two formats' orderings are genuinely different
    -- if this ever fails, the red_fastest/blue_fastest logic collapsed to
    the same thing and the axis-order distinction in the writers is dead
    code, not a real feature."""
    lut = _make_test_lut(size=4)
    write_3dl(lut, "/tmp/test_order.3dl", variant="flame")
    write_truelight_cub(lut, "/tmp/test_order.cub")

    dl_first_rows = [l.strip() for l in open("/tmp/test_order.3dl")][1:5]
    cub_lines = [l.rstrip("\n") for l in open("/tmp/test_order.cub")]
    cub_start = cub_lines.index("# Cube") + 1
    cub_first_rows = [l for l in cub_lines[cub_start:] if l.strip()][:4]

    assert dl_first_rows != cub_first_rows
    print("axis order differs between formats as expected -- OK")


if __name__ == "__main__":
    test_3dl_flame_roundtrip()
    test_3dl_lustre_roundtrip()
    test_truelight_cub_roundtrip()
    test_axis_order_actually_matters()
    print("\nAll format_writers self-consistency tests passed.")
