"""Tests for ycbcr_convert.py -- run directly: python3 -m tests.test_ycbcr_convert

Pure numpy math, no hardware/PyObjC dependency at all -- see that
module's own docstring on why this half of the AirPlay casting pipeline
is fully verifiable without a real Mac video-output stack or an AirPlay
receiver, unlike airplay_output_sink.py itself.
"""
import numpy as np

import ycbcr_convert as yc


def test_black_maps_to_video_range_black_and_neutral_chroma():
    black = np.zeros((1, 1, 3), dtype=np.float32)
    y, cb, cr = yc.rgb_to_bt709_ycbcr_float(black)
    y_digital, cb_digital, cr_digital = yc.ycbcr_float_to_10bit_digital(y, cb, cr)
    assert y_digital[0, 0] == 64
    assert cb_digital[0, 0] == 512
    assert cr_digital[0, 0] == 512
    print("ycbcr_convert: RGB black maps to video-range 10-bit black (Y=64) and neutral chroma (Cb=Cr=512) -- OK")


def test_white_maps_to_video_range_white_and_neutral_chroma():
    white = np.ones((1, 1, 3), dtype=np.float32)
    y, cb, cr = yc.rgb_to_bt709_ycbcr_float(white)
    y_digital, cb_digital, cr_digital = yc.ycbcr_float_to_10bit_digital(y, cb, cr)
    assert y_digital[0, 0] == 940
    assert cb_digital[0, 0] == 512
    assert cr_digital[0, 0] == 512
    print("ycbcr_convert: RGB white maps to video-range 10-bit white (Y=940) and neutral chroma -- OK")


def test_neutral_gray_is_neutral_chroma_at_every_level():
    for level in (0.02, 0.18, 0.5, 0.9):
        gray = np.full((1, 1, 3), level, dtype=np.float32)
        y, cb, cr = yc.rgb_to_bt709_ycbcr_float(gray)
        assert abs(cb[0, 0]) < 1e-9, level
        assert abs(cr[0, 0]) < 1e-9, level
    print("ycbcr_convert: any R=G=B gray level produces exactly zero (neutral) chroma -- OK")


def test_pure_red_green_blue_match_textbook_bt709_matrix():
    """Direct check against the BT.709 formula's own known constants,
    not just internal self-consistency -- Kr=0.2126, Kg=0.7152,
    Kb=0.0722, and the derived Cb/Cr denominators (2*(1-Kb), 2*(1-Kr))."""
    colors = np.array([[[1.0, 0.0, 0.0]], [[0.0, 1.0, 0.0]], [[0.0, 0.0, 1.0]]], dtype=np.float32)
    y, cb, cr = yc.rgb_to_bt709_ycbcr_float(colors)

    # Pure red: Y = Kr, Cb = -Y/(2*(1-Kb)), Cr = (1-Y)/(2*(1-Kr))
    assert abs(y[0, 0] - 0.2126) < 1e-6
    assert abs(cb[0, 0] - (-0.2126 / 1.8556)) < 1e-6
    assert abs(cr[0, 0] - ((1.0 - 0.2126) / 1.5748)) < 1e-6

    # Pure green: Y = Kg
    assert abs(y[1, 0] - 0.7152) < 1e-6

    # Pure blue: Y = Kb, Cb = (1-Y)/(2*(1-Kb))
    assert abs(y[2, 0] - 0.0722) < 1e-6
    assert abs(cb[2, 0] - ((1.0 - 0.0722) / 1.8556)) < 1e-6
    print("ycbcr_convert: pure R/G/B match the textbook BT.709 Y/Cb/Cr formula exactly -- OK")


def test_out_of_range_input_clips_to_legal_digital_range_not_wraps():
    """An HDR-headroom (>1.0) or negative (out-of-gamut) input must clip
    to the legal 10-bit video-range window, never wrap/overflow the
    uint16 container -- a real, plausible input for an ACES DRT HDR
    Viewer output feeding this same SDR-range conversion path (see
    module docstring)."""
    extreme = np.array([[[3.0, 3.0, 3.0]], [[-1.0, -1.0, -1.0]]], dtype=np.float32)
    y, cb, cr = yc.rgb_to_bt709_ycbcr_float(extreme)
    y_digital, cb_digital, cr_digital = yc.ycbcr_float_to_10bit_digital(y, cb, cr)
    assert y_digital[0, 0] == 940, "far-above-white must clip to the legal white level, not overflow"
    assert y_digital[1, 0] == 64, "far-below-black must clip to the legal black level, not underflow"
    assert (cb_digital >= 64).all() and (cb_digital <= 960).all()
    assert (cr_digital >= 64).all() and (cr_digital <= 960).all()
    print("ycbcr_convert: out-of-[0,1] input clips to the legal 10-bit video-range window -- OK")


def test_10bit_packing_roundtrips_exactly_across_the_full_range():
    values = np.arange(0, 1024, dtype=np.uint16).reshape(32, 32)
    packed = yc.pack_10bit_to_16bit(values)
    assert packed.dtype == np.uint16
    assert (packed & 0x3F).max() == 0, "the low 6 bits must always be zero after packing"
    unpacked = yc.unpack_16bit_to_10bit(packed)
    assert np.array_equal(unpacked, values)
    print("ycbcr_convert: pack_10bit_to_16bit/unpack_16bit_to_10bit round-trip exactly for all 1024 values -- OK")


def test_box_downsample_2x_averages_each_block():
    plane = np.array([
        [0.0, 0.0, 1.0, 1.0],
        [0.0, 0.0, 1.0, 1.0],
        [0.4, 0.6, 0.8, 1.0],
        [0.4, 0.6, 0.8, 1.0],
    ], dtype=np.float64)
    result = yc._box_downsample_2x(plane)
    expected = np.array([[0.0, 1.0], [0.5, 0.9]])
    assert np.allclose(result, expected)
    print("ycbcr_convert._box_downsample_2x: averages each 2x2 block correctly -- OK")


def test_box_downsample_2x_handles_odd_dimensions_without_out_of_bounds():
    plane = np.full((3, 5), 0.5, dtype=np.float64)
    result = yc._box_downsample_2x(plane)
    assert result.shape == (2, 3), result.shape
    assert np.allclose(result, 0.5), "a flat input should downsample to the same flat value everywhere"
    print("ycbcr_convert._box_downsample_2x: handles odd H/W by edge-replicating, no out-of-bounds -- OK")


def test_rgb_to_yuv420p10_planes_shapes_and_dtypes():
    rgb = np.random.default_rng(0).uniform(0, 1, size=(8, 10, 3)).astype(np.float32)
    y_plane, cbcr_plane = yc.rgb_to_yuv420p10_planes(rgb)
    assert y_plane.shape == (8, 10)
    assert y_plane.dtype == np.uint16
    assert cbcr_plane.shape == (4, 5, 2), cbcr_plane.shape
    assert cbcr_plane.dtype == np.uint16
    print("ycbcr_convert.rgb_to_yuv420p10_planes: full-res Y plane + half-res interleaved CbCr plane, correct shapes/dtypes -- OK")


def test_rgb_to_yuv420p10_planes_flat_black_frame_is_uniform():
    rgb = np.zeros((6, 8, 3), dtype=np.float32)
    y_plane, cbcr_plane = yc.rgb_to_yuv420p10_planes(rgb)
    expected_y = yc.pack_10bit_to_16bit(np.full((6, 8), 64, dtype=np.uint16))
    expected_c = yc.pack_10bit_to_16bit(np.full((3, 4), 512, dtype=np.uint16))
    assert np.array_equal(y_plane, expected_y)
    assert np.array_equal(cbcr_plane[..., 0], expected_c)
    assert np.array_equal(cbcr_plane[..., 1], expected_c)
    print("ycbcr_convert.rgb_to_yuv420p10_planes: a flat black frame packs to a uniform, correctly-packed plane -- OK")


if __name__ == "__main__":
    test_black_maps_to_video_range_black_and_neutral_chroma()
    test_white_maps_to_video_range_white_and_neutral_chroma()
    test_neutral_gray_is_neutral_chroma_at_every_level()
    test_pure_red_green_blue_match_textbook_bt709_matrix()
    test_out_of_range_input_clips_to_legal_digital_range_not_wraps()
    test_10bit_packing_roundtrips_exactly_across_the_full_range()
    test_box_downsample_2x_averages_each_block()
    test_box_downsample_2x_handles_odd_dimensions_without_out_of_bounds()
    test_rgb_to_yuv420p10_planes_shapes_and_dtypes()
    test_rgb_to_yuv420p10_planes_flat_black_frame_is_uniform()
    print()
    print("All ycbcr_convert tests passed.")
