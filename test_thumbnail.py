"""Tests for thumbnail.py -- run directly: python3 test_thumbnail.py

Fully Qt-free.
"""
import io

import numpy as np
from PIL import Image

import thumbnail as th


def _decode_png(png_bytes):
    return np.asarray(Image.open(io.BytesIO(png_bytes)).convert("RGB"))


def test_thumbnail_downsamples_a_large_image_to_the_max_dimension():
    array = np.random.default_rng(0).uniform(0.0, 1.0, (400, 800, 3)).astype(np.float32)
    png_bytes = th.make_thumbnail_png_bytes(array, max_dimension=160)
    decoded = _decode_png(png_bytes)
    assert max(decoded.shape[:2]) == 160
    assert decoded.shape[0] / decoded.shape[1] == 400 / 800 or \
        abs(decoded.shape[0] / decoded.shape[1] - 400 / 800) < 0.01
    print("thumbnail.make_thumbnail_png_bytes: downsamples a large image "
          "to max_dimension, preserving aspect ratio -- OK")


def test_thumbnail_never_upscales_a_small_image():
    array = np.random.default_rng(1).uniform(0.0, 1.0, (40, 60, 3)).astype(np.float32)
    png_bytes = th.make_thumbnail_png_bytes(array, max_dimension=160)
    decoded = _decode_png(png_bytes)
    assert decoded.shape[:2] == (40, 60)
    print("thumbnail.make_thumbnail_png_bytes: never upscales a source "
          "already smaller than max_dimension -- OK")


def test_thumbnail_is_always_8bit_png_regardless_of_source_range():
    """HDR/graded content (values outside [0,1]) must clip cleanly, not
    raise or produce garbage -- a thumbnail is always a plain 8-bit
    preview regardless of the source's own real bit depth/range."""
    array = np.random.default_rng(2).uniform(-0.5, 4.0, (20, 20, 3)).astype(np.float32)
    png_bytes = th.make_thumbnail_png_bytes(array, max_dimension=160)
    decoded = _decode_png(png_bytes)
    assert decoded.dtype == np.uint8
    assert decoded.min() >= 0 and decoded.max() <= 255
    print("thumbnail.make_thumbnail_png_bytes: HDR/out-of-[0,1] source "
          "clips cleanly to a valid 8-bit PNG -- OK")


def test_thumbnail_roughly_preserves_color():
    """Not a pixel-exact check (resize + PNG encode both perturb values
    slightly) -- just confirms the thumbnail is recognizably the same
    color as the source, not corrupted."""
    array = np.full((100, 100, 3), (0.8, 0.2, 0.4), dtype=np.float32)
    png_bytes = th.make_thumbnail_png_bytes(array, max_dimension=50)
    decoded = _decode_png(png_bytes).astype(np.float32) / 255.0
    np.testing.assert_allclose(decoded.mean(axis=(0, 1)), [0.8, 0.2, 0.4], atol=0.02)
    print("thumbnail.make_thumbnail_png_bytes: roughly preserves the "
          "source's own color -- OK")


if __name__ == "__main__":
    test_thumbnail_downsamples_a_large_image_to_the_max_dimension()
    test_thumbnail_never_upscales_a_small_image()
    test_thumbnail_is_always_8bit_png_regardless_of_source_range()
    test_thumbnail_roughly_preserves_color()
    print()
    print("All thumbnail tests passed.")
