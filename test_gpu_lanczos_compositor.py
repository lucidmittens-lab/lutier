"""Tests for gpu_lanczos_compositor.py -- run directly:
python3 test_gpu_lanczos_compositor.py

Validates the GPU Lanczos-3 resampler against a plain numpy/float64
implementation of the SAME algorithm (same kernel, same tap loop, same
texel-center convention, same clamp-to-edge boundary handling) -- NOT
against PIL's Image.resize(..., Image.LANCZOS), which was tried first and
diverges by up to ~0.32 per channel. That divergence turned out to be
expected, not a bug: Lanczos legitimately rings/overshoots outside [0,1]
near sharp edges (a known property of any negative-lobed filter, unlike
bilinear), and PIL's uint8 pipeline forcibly clips that ringing while
this float32 implementation doesn't -- so PIL isn't actually computing
the same unclamped values to compare against. Matching a from-scratch
reference of the intended algorithm itself is the fair, apples-to-apples
check.

Requires a real (offscreen but genuine) OpenGL context -- excluded from
`pytest -q` via conftest.py's collect_ignore, same reason as the other
GPU test files. Run standalone: `python3 test_gpu_lanczos_compositor.py`.
"""
import numpy as np

import gpu_lanczos_compositor as glc
import gpu_shader_prototype as gsp


def _lanczos3(x):
    x = np.asarray(x, dtype=np.float64)
    out = np.ones_like(x)
    nz = x != 0
    px = np.pi * x[nz]
    out[nz] = 3.0 * np.sin(px) * np.sin(px / 3.0) / (px * px)
    out[np.abs(x) >= 3] = 0.0
    return out


def _resample_axis_reference(arr, axis, target_n, offset, scale):
    """Plain numpy/float64 reference implementing the exact same
    separable Lanczos-3 algorithm as gpu_lanczos_compositor's shaders,
    INCLUDING the 2026-08-09 adaptive-minification widening (see that
    module's own docstring) -- see this module's docstring on why this
    (not PIL) is the fair comparison oracle. `_resample_axis_fixed_six_tap`
    below is the OLD (pre-fix) fixed-support version, kept only as the
    "before" baseline for test_heavy_downscale_reduces_aliasing_vs_fixed_
    six_tap -- every correctness test against the real GPU output uses
    THIS adaptive version, since that's what the shaders now do."""
    src_n = arr.shape[axis]
    out_shape = list(arr.shape)
    out_shape[axis] = target_n
    out = np.zeros(out_shape, dtype=np.float64)
    filter_scale = min(max(1.0, 1.0 / abs(scale)), glc._MAX_FILTER_SCALE)
    radius = int(np.ceil(3.0 * filter_scale))
    for screen_i in range(target_n):
        src_x = (screen_i + 0.5 - offset) / scale - 0.5
        base = int(np.floor(src_x))
        taps = np.arange(base - radius + 1, base + radius + 1)
        w = _lanczos3((src_x - taps) / filter_scale)
        clamped = np.clip(taps, 0, src_n - 1)
        wsum = w.sum()
        selected = np.take(arr, clamped, axis=axis).astype(np.float64)
        weight_shape = [1] * arr.ndim
        weight_shape[axis] = len(w)
        summed = (selected * w.reshape(weight_shape)).sum(axis=axis)
        out_index = [slice(None)] * arr.ndim
        out_index[axis] = screen_i
        out[tuple(out_index)] = summed / wsum if wsum > 0 else 0.0
    return out


def _resample_axis_fixed_six_tap(arr, axis, target_n, offset, scale):
    """The OLD (pre-2026-08-09) fixed-6-tap algorithm -- support does NOT
    widen under minification, which is exactly the aliasing bug being
    fixed. Only used as the "before" baseline in
    test_heavy_downscale_reduces_aliasing_vs_fixed_six_tap."""
    src_n = arr.shape[axis]
    out_shape = list(arr.shape)
    out_shape[axis] = target_n
    out = np.zeros(out_shape, dtype=np.float64)
    for screen_i in range(target_n):
        src_x = (screen_i + 0.5 - offset) / scale - 0.5
        base = int(np.floor(src_x))
        taps = np.arange(base - 2, base + 4)
        w = _lanczos3(src_x - taps)
        clamped = np.clip(taps, 0, src_n - 1)
        wsum = w.sum()
        selected = np.take(arr, clamped, axis=axis).astype(np.float64)
        weight_shape = [1] * arr.ndim
        weight_shape[axis] = len(w)
        summed = (selected * w.reshape(weight_shape)).sum(axis=axis)
        out_index = [slice(None)] * arr.ndim
        out_index[axis] = screen_i
        out[tuple(out_index)] = summed / wsum if wsum > 0 else 0.0
    return out


def _reference_resample(src, target_w, target_h, offset_x, offset_y, scale):
    """Full separable resample (X then Y) matching resample_into's own
    two-pass order exactly."""
    after_x = _resample_axis_reference(src.astype(np.float64), axis=1,
                                        target_n=target_w, offset=offset_x, scale=scale)
    return _resample_axis_reference(after_x, axis=0,
                                     target_n=target_h, offset=offset_y, scale=scale)


def _reference_resample_fixed_six_tap(src, target_w, target_h, offset_x, offset_y, scale):
    after_x = _resample_axis_fixed_six_tap(src.astype(np.float64), axis=1,
                                            target_n=target_w, offset=offset_x, scale=scale)
    return _resample_axis_fixed_six_tap(after_x, axis=0,
                                         target_n=target_h, offset=offset_y, scale=scale)


def test_upscale_matches_reference_algorithm():
    _app, context, surface = gsp._make_offscreen_gl_context()
    rng = np.random.default_rng(5)
    src = rng.uniform(0, 1, size=(40, 60, 3)).astype(np.float32)
    target_w, target_h = 180, 120  # 3x uniform upscale
    scale = target_w / 60

    resampler = glc.LanczosResampler()
    gpu_result = glc.resample_to_array(resampler, src, target_w, target_h,
                                        offset_x=0, offset_y=0, scale_x=scale)
    reference = _reference_resample(src, target_w, target_h, 0, 0, scale)
    delta = np.abs(gpu_result.astype(np.float64) - reference)
    context.doneCurrent()
    assert delta.max() < 1e-4, delta.max()
    print(f"LanczosResampler: 3x upscale matches the reference algorithm, "
          f"max|delta|={delta.max():.7f} -- OK")


def test_downscale_matches_reference_algorithm():
    _app, context, surface = gsp._make_offscreen_gl_context()
    rng = np.random.default_rng(6)
    src = rng.uniform(0, 1, size=(120, 160, 3)).astype(np.float32)
    target_w, target_h = 80, 60  # 0.5x uniform downscale
    scale = target_w / 160

    resampler = glc.LanczosResampler()
    gpu_result = glc.resample_to_array(resampler, src, target_w, target_h,
                                        offset_x=0, offset_y=0, scale_x=scale)
    reference = _reference_resample(src, target_w, target_h, 0, 0, scale)
    delta = np.abs(gpu_result.astype(np.float64) - reference)
    context.doneCurrent()
    assert delta.max() < 1e-4, delta.max()
    print(f"LanczosResampler: 0.5x downscale matches the reference algorithm, "
          f"max|delta|={delta.max():.7f} -- OK")


def test_offset_pan_matches_reference_algorithm():
    """Confirms the offset_x/offset_y terms (pan + centering, see
    wipe_compare_widget.py's _image_rect) are applied correctly, not just
    the zero-offset case."""
    _app, context, surface = gsp._make_offscreen_gl_context()
    rng = np.random.default_rng(7)
    src = rng.uniform(0, 1, size=(50, 50, 3)).astype(np.float32)
    target_w, target_h = 100, 100
    scale = 1.5
    offset_x, offset_y = 12.5, -7.0

    resampler = glc.LanczosResampler()
    gpu_result = glc.resample_to_array(resampler, src, target_w, target_h,
                                        offset_x=offset_x, offset_y=offset_y, scale_x=scale)
    reference = _reference_resample(src, target_w, target_h, offset_x, offset_y, scale)
    delta = np.abs(gpu_result.astype(np.float64) - reference)
    context.doneCurrent()
    assert delta.max() < 1e-4, delta.max()
    print(f"LanczosResampler: a non-zero pan offset matches the reference "
          f"algorithm, max|delta|={delta.max():.7f} -- OK")


def test_negative_scale_y_flips_the_vertical_axis():
    """The mechanism WipeCompositor relies on to reconcile Qt's top-down
    image_rect with GL's bottom-up gl_FragCoord (see resample_into's own
    docstring): a negative scale_y should read source rows in REVERSE,
    without the shader itself needing any Qt-specific logic. Confirmed
    directly here with a source that's asymmetric top-to-bottom (top rows
    are 0, bottom rows are 1) -- a real, unambiguous way to tell "did the
    flip actually happen" apart from "did nothing change.\""""
    _app, context, surface = gsp._make_offscreen_gl_context()
    h, w = 20, 20
    src = np.zeros((h, w, 3), dtype=np.float32)
    src[h // 2:, :, :] = 1.0  # top half 0, bottom half 1

    resampler = glc.LanczosResampler()
    # scale_y negative, offset_y = h (the source's own GL-space "top" when
    # traversed in reverse starting from the far end) -- mirrors exactly
    # how WipeCompositor.render will call this for the real widget.
    flipped = glc.resample_to_array(resampler, src, w, h, offset_x=0, offset_y=h,
                                     scale_x=1.0, scale_y=-1.0)
    not_flipped = glc.resample_to_array(resampler, src, w, h, offset_x=0, offset_y=0,
                                         scale_x=1.0, scale_y=1.0)
    context.doneCurrent()

    assert not_flipped[:h // 4].mean() < 0.1, "sanity check: unflipped top should still read ~0"
    assert flipped[:h // 4].mean() > 0.9, (
        "with scale_y=-1, the source's BOTTOM half (value 1.0) should now "
        "appear at the TOP of the output -- the flip didn't happen")
    print("LanczosResampler: negative scale_y correctly reverses the "
          "vertical traversal -- OK")


def test_heavy_downscale_reduces_aliasing_vs_fixed_six_tap():
    """Confirms the 2026-08-09 adaptive-minification widening (see module
    docstring) actually reduces aliasing under heavy downscale, not just
    that it matches its own reference (the tests above already establish
    that) -- a high-frequency alternating-column source, downscaled 10x,
    should average out close to a flat midtone once properly filtered.
    The old fixed-6-tap kernel undersamples that same footprint instead,
    leaving strong per-output-pixel variation (aliasing/banding) -- this
    is the actual user-reported symptom ("very crunchy... lots of scaling
    artifacts" when the image is small in the viewer)."""
    _app, context, surface = gsp._make_offscreen_gl_context()
    w, h = 400, 8
    period = 2  # single-pixel alternating columns -- far above Nyquist at 10x downscale
    pattern = (np.arange(w) // period) % 2
    src = np.tile(pattern[None, :, None], (h, 1, 3)).astype(np.float32)
    target_w, target_h = 40, 8  # 10x downscale in X only
    scale = target_w / w

    resampler = glc.LanczosResampler()
    gpu_result = glc.resample_to_array(resampler, src, target_w, target_h,
                                        offset_x=0, offset_y=0, scale_x=scale, scale_y=1.0)
    naive_reference = _reference_resample_fixed_six_tap(src, target_w, target_h, 0, 0, scale)
    context.doneCurrent()

    adaptive_std = float(gpu_result[:, :, 0].std())
    naive_std = float(naive_reference[:, :, 0].std())
    assert adaptive_std < naive_std * 0.5, (
        f"expected the adaptive filter's output variance to be meaningfully "
        f"lower than the naive fixed-tap one under 10x downscale, got "
        f"adaptive={adaptive_std:.4f} naive={naive_std:.4f}")
    print(f"LanczosResampler: 10x downscale has much less aliasing than the "
          f"old fixed-6-tap kernel (std {adaptive_std:.4f} vs {naive_std:.4f}) -- OK")


if __name__ == "__main__":
    test_upscale_matches_reference_algorithm()
    test_downscale_matches_reference_algorithm()
    test_offset_pan_matches_reference_algorithm()
    test_negative_scale_y_flips_the_vertical_axis()
    test_heavy_downscale_reduces_aliasing_vs_fixed_six_tap()
    print()
    print("All gpu_lanczos_compositor tests passed.")
