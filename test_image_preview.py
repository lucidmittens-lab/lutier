"""
test_image_preview.py -- tests for the before/after sample-image pipeline
in app_logic.py (load_image_as_array, apply_lut_to_image_array,
array_to_png_bytes). Fully Qt-free, so this actually runs here, unlike
sample_image_panel.py itself.

Generates its own test image with PIL rather than depending on a real
uploaded file being present, so this is runnable standalone. The real
uploaded LUT (samples/roundtrip_check.cube) IS used below, though, so the
LUT-application half of the pipeline is tested against real production
data, not just a synthetic LUT.
"""
from pathlib import Path

import numpy as np
import colour
from PIL import Image

from app_logic import (
    load_image_as_array, apply_lut_to_image_array, array_to_png_bytes,
    apply_lut_chain_to_image_array, concatenate_luts,
    apply_lut_chain_with_opacity, concatenate_luts_with_opacity,
    export_image_array_to_file, render_full_resolution_still,
)

TEST_IMAGE_PATH = "/tmp/test_preview_image.png"


def _make_test_image(path=TEST_IMAGE_PATH, size=(800, 600)):
    """A gradient test image (not flat color) so channel-dependent LUT
    behavior actually has something non-trivial to act on."""
    w, h = size
    x = np.linspace(0, 1, w, dtype=np.float32)
    y = np.linspace(0, 1, h, dtype=np.float32)
    xx, yy = np.meshgrid(x, y)
    r = xx
    g = yy
    b = 1.0 - xx
    array = np.clip(np.stack([r, g, b], axis=-1) * 255, 0, 255).astype(np.uint8)
    Image.fromarray(array, mode="RGB").save(path)
    return path


def test_load_image_downsamples_and_normalizes():
    path = _make_test_image(size=(800, 600))
    array = load_image_as_array(path, max_dimension=480)
    assert array.dtype == np.float32
    assert array.max() <= 1.0 and array.min() >= 0.0
    # 800x600 -> longer side (800) capped to 480 -> scale 0.6 -> 480x360
    assert max(array.shape[:2]) == 480, array.shape
    assert array.shape[1] / array.shape[0] == 800 / 600 or \
        abs(array.shape[1] / array.shape[0] - 800 / 600) < 0.01
    print(f"load_image_as_array: 800x600 -> {array.shape[1]}x{array.shape[0]} -- OK")


def test_load_image_no_downsample_when_max_dimension_none():
    path = _make_test_image(size=(200, 150))
    array = load_image_as_array(path, max_dimension=None)
    assert array.shape[:2] == (150, 200)
    print("load_image_as_array with max_dimension=None: no resize -- OK")


def test_apply_identity_lut_leaves_image_unchanged():
    path = _make_test_image(size=(64, 48))
    array = load_image_as_array(path, max_dimension=None)
    identity_lut = colour.LUT3D(size=17)
    result = apply_lut_to_image_array(identity_lut, array)
    max_diff = np.max(np.abs(result - array))
    # Trilinear interpolation on a 17^3 identity grid should reproduce
    # input near-exactly (identity is piecewise-linear, exactly what
    # trilinear interpolation represents without error).
    assert max_diff < 1e-4, max_diff
    print(f"identity LUT applied to image, max diff: {max_diff:.6f} -- OK")


def test_apply_real_lut_actually_changes_image():
    """Sanity check the other direction: a real, non-identity production
    LUT should visibly change the pixels, not silently no-op."""
    path = _make_test_image(size=(64, 48))
    array = load_image_as_array(path, max_dimension=None)
    lut = colour.read_LUT(str(Path(__file__).parent / "samples" / "roundtrip_check.cube"))
    result = apply_lut_to_image_array(lut, array)
    assert result.shape == array.shape
    assert result.dtype == np.float32
    assert np.max(np.abs(result - array)) > 1e-3, "real LUT had no visible effect -- suspicious"
    assert result.min() >= 0.0 and result.max() <= 1.0
    print("real production LUT changes image and stays in [0,1] -- OK")


def test_array_to_png_bytes_round_trips():
    path = _make_test_image(size=(32, 24))
    array = load_image_as_array(path, max_dimension=None)
    png_bytes = array_to_png_bytes(array)
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n", "not a valid PNG header"

    # Decode back and compare -- should match within 8-bit quantization.
    decoded = np.asarray(Image.open(__import__("io").BytesIO(png_bytes)).convert("RGB"),
                          dtype=np.float32) / 255.0
    max_diff = np.max(np.abs(decoded - array))
    assert max_diff < 1.0 / 255 + 1e-6, max_diff
    print(f"array_to_png_bytes round-trip max diff: {max_diff:.6f} -- OK")


def test_apply_lut_chain_matches_sequential_manual_apply():
    path = _make_test_image(size=(48, 36))
    array = load_image_as_array(path, max_dimension=None)
    lut = colour.read_LUT(str(Path(__file__).parent / "samples" / "roundtrip_check.cube"))
    identity = colour.LUT3D(size=9)  # no-op stage, just to prove chaining works

    chain_result = apply_lut_chain_to_image_array([lut, identity], array)
    manual = apply_lut_to_image_array(identity, apply_lut_to_image_array(lut, array))
    max_diff = np.max(np.abs(chain_result - manual))
    assert max_diff < 1e-6, max_diff
    print(f"apply_lut_chain_to_image_array matches manual sequential apply, max diff: {max_diff:.2e} -- OK")


def test_apply_lut_chain_empty_list_is_passthrough():
    path = _make_test_image(size=(32, 24))
    array = load_image_as_array(path, max_dimension=None)
    result = apply_lut_chain_to_image_array([], array)
    assert np.array_equal(result, array)
    print("apply_lut_chain_to_image_array([], image) is a passthrough -- OK")


def test_chain_preview_is_more_accurate_than_baked_concatenate():
    """The whole point of apply_lut_chain_to_image_array existing
    separately from concatenate_luts is that it skips the intermediate
    grid-baking step -- so it should be at least as close to a fully
    manual sequential apply as the baked-then-applied version is, usually
    closer, never meaningfully worse."""
    path = _make_test_image(size=(40, 30))
    array = load_image_as_array(path, max_dimension=None)
    lut = colour.read_LUT(str(Path(__file__).parent / "samples" / "roundtrip_check.cube"))

    class Gain:
        name = "gain"
        def apply(self, RGB):
            return np.asarray(RGB, dtype=float) * 0.85

    gain = Gain()
    manual = apply_lut_to_image_array(gain, apply_lut_to_image_array(lut, array))

    direct_chain = apply_lut_chain_to_image_array([lut, gain], array)
    direct_diff = np.max(np.abs(direct_chain - manual))
    assert direct_diff < 1e-6, direct_diff

    baked = concatenate_luts([lut, gain], size=17)
    baked_result = apply_lut_to_image_array(baked, array)
    baked_diff = np.max(np.abs(baked_result - manual))

    assert direct_diff <= baked_diff, (
        f"direct chain preview ({direct_diff}) should be at least as accurate "
        f"as the baked-17^3 version ({baked_diff})"
    )
    print(f"direct chain diff {direct_diff:.2e} <= baked-grid diff {baked_diff:.5f} -- OK")


def test_chain_with_opacity_full_strength_matches_plain_chain():
    """opacity=1.0 for every stage should be pixel-identical to the
    existing no-opacity chain functions -- this is what makes the
    opacity-aware functions a safe drop-in for every caller, not just an
    alternative that happens to agree at the default."""
    path = _make_test_image(size=(40, 30))
    array = load_image_as_array(path, max_dimension=None)
    lut = colour.read_LUT(str(Path(__file__).parent / "samples" / "roundtrip_check.cube"))
    identity = colour.LUT3D(size=9)

    plain = apply_lut_chain_to_image_array([lut, identity], array)
    with_opacity = apply_lut_chain_with_opacity([(lut, 1.0), (identity, 1.0)], array)
    assert np.array_equal(plain, with_opacity)
    print("apply_lut_chain_with_opacity at 1.0/1.0 exactly matches "
          "apply_lut_chain_to_image_array -- OK")

    baked_plain = concatenate_luts([lut, identity], size=17)
    baked_with_opacity = concatenate_luts_with_opacity([(lut, 1.0), (identity, 1.0)], size=17)
    assert np.array_equal(baked_plain.table, baked_with_opacity.table)
    print("concatenate_luts_with_opacity at 1.0/1.0 exactly matches "
          "concatenate_luts -- OK")


def test_chain_with_opacity_zero_is_noop_for_that_stage():
    path = _make_test_image(size=(32, 24))
    array = load_image_as_array(path, max_dimension=None)
    lut = colour.read_LUT(str(Path(__file__).parent / "samples" / "roundtrip_check.cube"))

    # A stage at opacity 0 should behave as if it weren't in the chain.
    with_zero_stage = apply_lut_chain_with_opacity([(lut, 1.0), (lut, 0.0)], array)
    without_that_stage = apply_lut_chain_with_opacity([(lut, 1.0)], array)
    assert np.array_equal(with_zero_stage, without_that_stage)
    print("apply_lut_chain_with_opacity: opacity=0.0 stage is an exact no-op -- OK")


def test_concatenate_with_opacity_fifty_percent_is_the_midpoint():
    """concatenate_luts_with_opacity (the baking path) doesn't clip
    between stages -- same as concatenate_luts -- so a >1.0-producing
    gain is fine to use here to check the exact midpoint math."""
    array = colour.LUT3D(size=5).table  # identity grid, a stand-in "image"

    class Gain:
        name = "gain"
        def apply(self, RGB):
            return np.asarray(RGB, dtype=float) * 2.0

    expected = array * 0.5 + (array * 2.0) * 0.5  # midpoint between input and 2x output
    baked_half = concatenate_luts_with_opacity([(Gain(), 0.5)], size=5)
    assert np.allclose(baked_half.table, expected, atol=1e-6)
    print("concatenate_luts_with_opacity: 50% opacity lands exactly at the "
          "midpoint between stage input and stage output -- OK")


def test_chain_with_opacity_fifty_percent_is_the_midpoint():
    """apply_lut_chain_with_opacity (the live-preview path) clips each
    stage's output to [0,1] via apply_lut_to_image_array, same convention
    as the existing apply_lut_chain_to_image_array -- so this uses a
    darkening gain (values only shrink, never approach the clip edge) to
    check the midpoint math without that clipping muddying the check."""
    array = colour.LUT3D(size=5).table

    class Darken:
        name = "darken"
        def apply(self, RGB):
            return np.asarray(RGB, dtype=float) * 0.4

    half = apply_lut_chain_with_opacity([(Darken(), 0.5)], array)
    expected = array * 0.5 + (array * 0.4) * 0.5  # midpoint between input and 0.4x output
    assert np.allclose(half, expected, atol=1e-6)
    print("apply_lut_chain_with_opacity: 50% opacity lands exactly at the "
          "midpoint between stage input and stage output -- OK")


def test_export_image_array_to_file_png_and_tiff():
    path = _make_test_image(size=(48, 36))
    array = load_image_as_array(path, max_dimension=None)

    for ext, out_path in [(".png", "/tmp/test_export.png"), (".tif", "/tmp/test_export.tif")]:
        export_image_array_to_file(array, out_path)
        decoded = np.asarray(Image.open(out_path).convert("RGB"), dtype=np.float32) / 255.0
        max_diff = np.max(np.abs(decoded - array))
        # Both PNG and TIFF (as saved here) are lossless -- only 8-bit
        # quantization error expected.
        assert max_diff < 1.0 / 255 + 1e-6, (ext, max_diff)
        print(f"export_image_array_to_file {ext}: max diff {max_diff:.6f} -- OK")


def test_render_full_resolution_still_matches_manual_full_res_apply():
    """The whole point of render_full_resolution_still is that it re-reads
    the ORIGINAL file at full resolution rather than reusing a downsampled
    preview array -- confirm the output actually matches applying the LUT
    to a freshly, fully loaded copy, at real (non-downsampled) dimensions,
    using the real uploaded production LUT."""
    path = _make_test_image(size=(600, 400))  # a real, non-trivial resolution to confirm against
    lut = colour.read_LUT(str(Path(__file__).parent / "samples" / "roundtrip_check.cube"))

    result = render_full_resolution_still(path, lut=lut)
    assert result.shape == (400, 600, 3), result.shape  # full res, NOT downsampled to 480

    manual_full_res = load_image_as_array(path, max_dimension=None)
    manual_result = apply_lut_to_image_array(lut, manual_full_res)
    assert np.array_equal(result, manual_result)
    print(f"render_full_resolution_still: {result.shape[1]}x{result.shape[0]} "
          f"(not downsampled), matches manual full-res apply exactly -- OK")


def test_render_full_resolution_still_chain_mode():
    path = _make_test_image(size=(64, 48))
    lut = colour.read_LUT(str(Path(__file__).parent / "samples" / "roundtrip_check.cube"))

    class Gain:
        name = "gain"
        def apply(self, RGB):
            return np.asarray(RGB, dtype=float) * 0.9

    chain = [lut, Gain()]
    result = render_full_resolution_still(path, lut_chain=chain)

    full_res = load_image_as_array(path, max_dimension=None)
    manual = apply_lut_chain_to_image_array(chain, full_res)
    assert np.array_equal(result, manual)
    print("render_full_resolution_still chain mode matches manual chain apply -- OK")


def test_render_full_resolution_still_chain_stages_mode():
    path = _make_test_image(size=(64, 48))
    lut = colour.read_LUT(str(Path(__file__).parent / "samples" / "roundtrip_check.cube"))

    class Darken:
        name = "darken"
        def apply(self, RGB):
            return np.asarray(RGB, dtype=float) * 0.7

    stages = [(lut, 1.0), (Darken(), 0.5)]
    result = render_full_resolution_still(path, lut_chain_stages=stages)

    full_res = load_image_as_array(path, max_dimension=None)
    manual = apply_lut_chain_with_opacity(stages, full_res)
    assert np.array_equal(result, manual)
    print("render_full_resolution_still lut_chain_stages mode matches "
          "manual apply_lut_chain_with_opacity -- OK")


def test_render_full_resolution_still_requires_exactly_one_source():
    path = _make_test_image(size=(32, 24))
    lut = colour.LUT3D(size=5)
    try:
        render_full_resolution_still(path, lut=None, lut_chain=None, lut_chain_stages=None)
        raise AssertionError("expected ValueError when none of the three are given")
    except ValueError:
        pass
    try:
        render_full_resolution_still(path, lut=lut, lut_chain=[lut])
        raise AssertionError("expected ValueError when both lut and lut_chain given")
    except ValueError:
        pass
    try:
        render_full_resolution_still(path, lut=lut, lut_chain_stages=[(lut, 1.0)])
        raise AssertionError("expected ValueError when both lut and lut_chain_stages given")
    except ValueError:
        pass
    print("render_full_resolution_still rejects ambiguous lut/lut_chain args -- OK")


if __name__ == "__main__":
    test_load_image_downsamples_and_normalizes()
    test_load_image_no_downsample_when_max_dimension_none()
    test_apply_identity_lut_leaves_image_unchanged()
    test_apply_real_lut_actually_changes_image()
    test_array_to_png_bytes_round_trips()
    test_apply_lut_chain_matches_sequential_manual_apply()
    test_apply_lut_chain_empty_list_is_passthrough()
    test_chain_preview_is_more_accurate_than_baked_concatenate()
    test_chain_with_opacity_full_strength_matches_plain_chain()
    test_chain_with_opacity_zero_is_noop_for_that_stage()
    test_concatenate_with_opacity_fifty_percent_is_the_midpoint()
    test_chain_with_opacity_fifty_percent_is_the_midpoint()
    test_export_image_array_to_file_png_and_tiff()
    test_render_full_resolution_still_matches_manual_full_res_apply()
    test_render_full_resolution_still_chain_mode()
    test_render_full_resolution_still_chain_stages_mode()
    test_render_full_resolution_still_requires_exactly_one_source()
    print("\nAll image preview pipeline tests passed.")
