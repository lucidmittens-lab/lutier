"""
thumbnail.py -- generates a small preview PNG from an already-decoded
image array. The first thumbnail-generation code in this app (Tom,
2026-08-13: Media Library + Stills Gallery both need real thumbnails,
not just filename text) -- deliberately composed entirely from already-
verified building blocks (image_io.resize_array's own Lanczos resize,
app_logic.array_to_png_bytes's own PNG encode) rather than introducing a
second, independent resize/encode path.

Callers own deciding WHERE the resulting bytes end up (embedded in a
session file, or written to a cache directory) -- see media_cache.py.
This module only ever produces bytes, no I/O of its own.

Same rule as every other backend module in this app: no PySide6 import
here, fully testable without a display.
"""
import image_io
from app_logic import array_to_png_bytes

DEFAULT_MAX_DIMENSION = 160


def make_thumbnail_png_bytes(array, max_dimension=DEFAULT_MAX_DIMENSION):
    """`array`: any (H,W,3) float array (any domain -- HDR/graded content
    included, array_to_png_bytes's own clip handles values outside
    [0,1]). Downsamples (aspect-preserving, Lanczos) so the longer side
    is at most `max_dimension` -- never upscales a source already
    smaller than that, matching app_logic.load_image_as_array's own
    "scale < 1.0" guard for the identical reason. Returns PNG-encoded
    bytes, always 8-bit (a thumbnail has no reason to carry more
    precision than a preview needs, regardless of the source's own real
    bit depth)."""
    h, w = array.shape[:2]
    scale = max_dimension / max(w, h)
    if scale < 1.0:
        new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))
        array = image_io.resize_array(array, new_w, new_h)
    return array_to_png_bytes(array)
