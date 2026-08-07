"""
format_writers.py -- export writers for LUT formats colour-science doesn't
support natively: Autodesk .3dl (Flame/Lustre) and FilmLight Truelight/
Baselight .cub. Read support isn't implemented -- only export/write, since
that's what was actually asked for; loading these back in is a reasonable
follow-up but a separate task.

Neither Autodesk nor FilmLight publish a formal public spec for these
formats. The closest thing to a canonical reference is OpenColorIO's own
file-format implementations (FileFormat3DL.cpp, FileFormatTruelight.cpp --
BSD-3-Clause, AcademySoftwareFoundation/OpenColorIO), which is what these
writers are built against -- re-implemented from scratch in Python to match
their documented behavior (header tokens, bit depths, iteration order), not
transcribed from their C++.

NOT VALIDATED AGAINST A REAL AUTODESK/FILMLIGHT TOOL -- this sandbox has no
Lustre, Flame, or Baselight to test against, and no OCIO install either.
Self-tested in test_format_writers.py by round-tripping through a
hand-written parser that mirrors OCIO's read-side logic, which validates
internal consistency (byte-level structure, scaling, iteration order) but
NOT real-world compatibility. Please load the exported files into an actual
Baselight/Resolve/Lustre (whichever you have) and report back before
trusting these on a deliverable, same as everything else in this project.
"""
from pathlib import Path

import numpy as np


def _require_cubic_table(lut):
    table = np.asarray(lut.table, dtype=float)
    if table.ndim != 4 or not (table.shape[0] == table.shape[1] == table.shape[2]):
        raise ValueError(
            f"Expected a cubic (N,N,N,3) LUT3D table, got shape {table.shape}"
        )
    return table


def _identity_shaper_ints(size, max_value):
    """size evenly spaced ints from 0 to max_value inclusive."""
    return [round(i * max_value / (size - 1)) for i in range(size)]


def _table_rows(table, order, bit_depth):
    """Flatten (N,N,N,3) table to integer (r,g,b) rows in the requested
    iteration order, scaled to bit_depth.

    order="blue_fastest": table.reshape(-1,3) already iterates with blue
    (the last axis before channel) varying fastest -- this is the .3dl
    convention.

    order="red_fastest": transpose to (B,G,R,3) first so red becomes the
    fastest-varying axis after flattening -- this is the Truelight .cub
    convention.
    """
    max_value = 2 ** bit_depth - 1
    if order == "blue_fastest":
        flat = table.reshape(-1, 3)
    elif order == "red_fastest":
        flat = table.transpose(2, 1, 0, 3).reshape(-1, 3)
    else:
        raise ValueError(f"Unknown order: {order}")
    scaled = np.clip(flat, 0.0, 1.0) * max_value
    return np.rint(scaled).astype(int)


def write_3dl(lut, path, variant="lustre", shaper_bit_depth=10, cube_bit_depth=12):
    """Write an Autodesk .3dl file (Flame or Lustre dialect).

    variant="lustre": adds the "3DMESH"/"Mesh W D" header and trailing
    "LUT8"/"gamma 1.0" tokens Lustre expects.
    variant="flame": bare shaper + cube body, no extra tokens -- both Flame
    and Lustre can read this form; it's what real-world Autodesk 3dl exports
    (e.g. Truelight's own .3dl exporter) actually look like.

    Defaults (10-bit shaper, 12-bit cube, blue-fastest ordering) match
    OpenColorIO's own 3dl bake() defaults for both dialects.
    """
    if variant not in ("lustre", "flame"):
        raise ValueError(f"variant must be 'lustre' or 'flame', got {variant!r}")

    table = _require_cubic_table(lut)
    size = table.shape[0]

    shaper_max = 2 ** shaper_bit_depth - 1
    shaper_values = _identity_shaper_ints(size, shaper_max)
    rows = _table_rows(table, order="blue_fastest", bit_depth=cube_bit_depth)

    lines = []
    if variant == "lustre":
        # 65 -> 6, 33 -> 5, 17 -> 4 (log2 of cube edge minus one)
        mesh_input_bit_depth = round(np.log2(size - 1))
        lines.append("3DMESH")
        lines.append(f"Mesh {mesh_input_bit_depth} {cube_bit_depth}")

    lines.append(" ".join(str(v) for v in shaper_values))
    for r, g, b in rows:
        lines.append(f"{r} {g} {b}")

    if variant == "lustre":
        lines.append("")
        lines.append("LUT8")
        lines.append("gamma 1.0")

    Path(path).write_text("\n".join(lines) + "\n")


def write_truelight_cub(lut, path, shaper_size=1024):
    """Write a FilmLight Truelight/Baselight .cub file ("Truelight Cube
    v2.0" dialect). Includes an identity float InputLUT shaper plus the 3D
    cube data in red-fastest order, matching FileFormatTruelight.cpp's
    bake() layout: header comments, "# InputLUT" shaper block, "# Cube"
    data block, "# end" terminator.
    """
    table = _require_cubic_table(lut)
    size = table.shape[0]

    rows = np.clip(
        table.transpose(2, 1, 0, 3).reshape(-1, 3), 0.0, 1.0
    ) * (size - 1)

    lines = [
        "# Truelight Cube v2.0",
        f"# lutLength {shaper_size}",
        "# iDims     3",
        "# oDims     3",
        f"# width     {size} {size} {size}",
        "",
        "# InputLUT",
    ]
    for i in range(shaper_size - 1):
        v = (i / (shaper_size - 1)) * (size - 1)
        lines.append(f"{v:.6f} {v:.6f} {v:.6f}")
    v = float(size - 1)
    lines.append(f"{v:.6f} {v:.6f} {v:.6f}")

    lines.append("")
    lines.append("# Cube")
    for r, g, b in rows:
        lines.append(f"{r:.6f} {g:.6f} {b:.6f}")
    lines.append("# end")

    Path(path).write_text("\n".join(lines) + "\n")
