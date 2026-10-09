"""Tests for image_io.py -- run directly: python3 -m tests.test_image_io

Fully Qt-free. Real files written to a temp directory and read back --
the whole point of this module is getting real-world bit-depth/container
conventions right (see its own docstring on the DPX 10/12-bit "full
16-bit range, not raw low bits" bug caught before this ever shipped), so
these tests exercise the actual OIIO plugins, not just this module's own
Python logic in isolation.
"""
import os
import tempfile

import numpy as np
from PIL import Image

import canvas as cv
import image_io as iio


def _tmp(name):
    return os.path.join(tempfile.gettempdir(), f"test_image_io_{os.getpid()}_{name}")


def test_8bit_png_round_trips_within_one_quantization_step():
    arr = np.random.default_rng(0).uniform(0.0, 1.0, (12, 10, 3)).astype(np.float32)
    path = _tmp("8bit.png")
    iio.write_image(arr, path, 8)
    got, bit_depth = iio.read_image_as_array(path)
    assert bit_depth == 8
    assert got.shape == arr.shape
    assert np.abs(got - arr).max() < 1.0 / 255.0
    print("image_io: 8-bit PNG round-trips within one quantization step, "
          "reports bit_depth=8 -- OK")


def test_16bit_tiff_round_trips_within_one_quantization_step():
    arr = np.random.default_rng(1).uniform(0.0, 1.0, (12, 10, 3)).astype(np.float32)
    path = _tmp("16bit.tif")
    iio.write_image(arr, path, 16)
    got, bit_depth = iio.read_image_as_array(path)
    assert bit_depth == 16
    assert np.abs(got - arr).max() < 1.0 / 65535.0 + 1e-6
    print("image_io: 16-bit TIFF round-trips within one quantization step, "
          "reports bit_depth=16 -- OK")


def test_10bit_dpx_round_trips_within_true_10bit_quantization_not_16bit_noise():
    """Regression test for the real bug caught before this module ever
    shipped: 10-bit DPX values are stored SCALED TO THE FULL 16-bit
    container range, not as raw 0-1023 values in the low bits -- getting
    this wrong produced errors of ~0.06 in [0,1] (nowhere near true
    10-bit quantization noise of ~1/1023). This asserts the error is
    close to that TRUE 10-bit step, not merely "small"."""
    arr = np.random.default_rng(2).uniform(0.0, 1.0, (12, 10, 3)).astype(np.float32)
    path = _tmp("10bit.dpx")
    iio.write_image(arr, path, 10)
    got, bit_depth = iio.read_image_as_array(path)
    assert bit_depth == 10
    max_error = np.abs(got - arr).max()
    ten_bit_step = 1.0 / 1023.0
    assert max_error < ten_bit_step * 1.5, (
        f"error {max_error} is much larger than a true 10-bit quantization step "
        f"({ten_bit_step}) -- likely storing raw low-bit values again, not full-range-scaled")
    print(f"image_io: 10-bit DPX round-trips within true 10-bit quantization "
          f"({max_error:.6f} vs step {ten_bit_step:.6f}), reports bit_depth=10 -- OK")


def test_12bit_dpx_round_trips_within_true_12bit_quantization():
    arr = np.random.default_rng(3).uniform(0.0, 1.0, (12, 10, 3)).astype(np.float32)
    path = _tmp("12bit.dpx")
    iio.write_image(arr, path, 12)
    got, bit_depth = iio.read_image_as_array(path)
    assert bit_depth == 12
    max_error = np.abs(got - arr).max()
    twelve_bit_step = 1.0 / 4095.0
    assert max_error < twelve_bit_step * 1.5, (max_error, twelve_bit_step)
    print(f"image_io: 12-bit DPX round-trips within true 12-bit quantization "
          f"({max_error:.6f} vs step {twelve_bit_step:.6f}) -- OK")


def test_32f_exr_round_trips_exactly_including_hdr_and_negative_values():
    """Unlike the integer depths, float must round-trip EXACTLY (float32
    -> float32, no quantization at all) and must preserve values outside
    [0,1] entirely -- unbounded HDR range is the whole point of "32f"
    (see module docstring, matches custom_drt.py's own unbounded design)."""
    arr = np.random.default_rng(4).uniform(-0.5, 4.0, (8, 12, 3)).astype(np.float32)
    path = _tmp("32f.exr")
    iio.write_image(arr, path, "32f")
    got, bit_depth = iio.read_image_as_array(path)
    assert bit_depth == "32f"
    assert np.array_equal(got, arr)
    assert got.min() < 0.0, "negative (below-black) values must survive, not get clipped"
    assert got.max() > 1.0, "over-white HDR values must survive, not get clipped"
    print("image_io: 32-bit float EXR round-trips exactly, preserving "
          "negative and over-1.0 HDR range -- OK")


def test_grayscale_source_replicates_to_three_channels():
    """Matches PIL's old .convert("RGB") behavior for a single-channel
    source -- app_logic.py's callers all expect (H,W,3), not (H,W,1) or
    (H,W)."""
    path = _tmp("gray.png")
    gray_u8 = np.random.default_rng(5).integers(0, 256, (6, 9), dtype=np.uint8)
    Image.fromarray(gray_u8, mode="L").save(path)
    got, bit_depth = iio.read_image_as_array(path)
    assert got.shape == (6, 9, 3)
    assert bit_depth == 8
    assert np.array_equal(got[..., 0], got[..., 1])
    assert np.array_equal(got[..., 0], got[..., 2])
    np.testing.assert_allclose(got[..., 0], gray_u8.astype(np.float32) / 255.0, atol=1e-6)
    print("image_io: a grayscale source replicates to 3 identical channels, "
          "matching PIL's old .convert('RGB') behavior -- OK")


def test_rgba_source_drops_the_alpha_channel():
    path = _tmp("rgba.png")
    rgba = np.random.default_rng(6).integers(0, 256, (5, 7, 4), dtype=np.uint8)
    rgba[..., 3] = 128  # non-trivial alpha, would corrupt the result if not dropped
    Image.fromarray(rgba, mode="RGBA").save(path)
    got, bit_depth = iio.read_image_as_array(path)
    assert got.shape == (5, 7, 3)
    np.testing.assert_allclose(got, rgba[..., :3].astype(np.float32) / 255.0, atol=1e-6)
    print("image_io: an RGBA source drops the alpha channel, keeping only RGB -- OK")


def test_get_native_size_matches_array_shape():
    arr = np.random.default_rng(7).uniform(0.0, 1.0, (20, 30, 3)).astype(np.float32)
    path = _tmp("size.tif")
    iio.write_image(arr, path, 16)
    w, h = iio.get_native_size(path)
    assert (w, h) == (30, 20)  # (width, height) -- array shape is (H, W, 3)
    print("image_io.get_native_size: matches the written array's own (width, height) -- OK")


def test_resize_array_produces_the_requested_dimensions():
    arr = np.random.default_rng(8).uniform(0.0, 1.0, (40, 60, 3)).astype(np.float32)
    resized = iio.resize_array(arr, 20, 12)
    assert resized.shape == (12, 20, 3)
    assert resized.dtype == np.float32
    print("image_io.resize_array: produces the requested target dimensions -- OK")


def test_write_image_rejects_unknown_bit_depth():
    arr = np.zeros((2, 2, 3), dtype=np.float32)
    try:
        iio.write_image(arr, _tmp("bogus.png"), 9)
        raise AssertionError("expected ValueError for an unknown bit_depth")
    except ValueError:
        pass
    print("image_io.write_image: raises ValueError for an unknown bit_depth "
          "rather than silently writing something wrong -- OK")


def test_write_image_clips_out_of_range_integer_output():
    """Integer depths must clip HDR/negative scene values to [0,1] before
    quantizing -- unlike "32f", an 8/10/12/16-bit file has no way to
    represent values outside that range at all."""
    arr = np.array([[[-0.5, 0.5, 2.0]]], dtype=np.float32)
    path = _tmp("clip.png")
    iio.write_image(arr, path, 8)
    got, _bit_depth = iio.read_image_as_array(path)
    np.testing.assert_allclose(got[0, 0], [0.0, 0.5, 1.0], atol=1.0 / 255.0)
    print("image_io.write_image: clips out-of-[0,1]-range values for integer "
          "output rather than wrapping/corrupting -- OK")


if __name__ == "__main__":
    test_8bit_png_round_trips_within_one_quantization_step()
    test_16bit_tiff_round_trips_within_one_quantization_step()
    test_10bit_dpx_round_trips_within_true_10bit_quantization_not_16bit_noise()
    test_12bit_dpx_round_trips_within_true_12bit_quantization()
    test_32f_exr_round_trips_exactly_including_hdr_and_negative_values()
    test_grayscale_source_replicates_to_three_channels()
    test_rgba_source_drops_the_alpha_channel()
    test_get_native_size_matches_array_shape()
    test_resize_array_produces_the_requested_dimensions()
    test_write_image_rejects_unknown_bit_depth()
    test_write_image_clips_out_of_range_integer_output()
    print()
    print("All image_io tests passed.")
