"""Tests for gpu_color_warper_shader.py -- run directly:
python3 test_gpu_color_warper_shader.py

Requires a real (offscreen but genuine) OpenGL context -- excluded from
`pytest -q` via conftest.py's collect_ignore, same reason as every other
real-GL-context GPU test file.
"""
import functools

import numpy as np
import OpenGL.GL as gl

import aces_drt
import color_warper as cw
import gpu_color_warper_shader as gcw
import gpu_shader_prototype as gsp


def _set_uniforms(points, wake, dropoff):
    return functools.partial(
        gcw.set_color_warper_uniforms, gl, points=points, wake=wake, dropoff=dropoff)


def _sample_identity_points():
    """A non-trivial (3-point) all-identity (h==h0, c==c0) points list --
    color_warper.default_points() used to provide this, but WarpierWheel
    no longer seeds any default points (per Tom), so this stays purely a
    local test fixture. Used where a test needs to exercise the REAL
    per-point uniform-passing/shader code path (not just the trivially-
    empty uCwPointCount=0 case) while still expecting an exact no-op."""
    hues = (35.0, 142.5, 250.0)
    chroma = 0.15
    return [{"h0": h, "c0": chroma, "h": h, "c": chroma} for h in hues]


def test_is_color_warper_gpu_eligible_for_a_real_working_space():
    assert gcw.is_color_warper_gpu_eligible(aces_drt.DEFAULT_CONFIG_NAME, "ACEScct") is True
    print("gpu_color_warper_shader.is_color_warper_gpu_eligible: True for "
          "a real working colourspace -- OK")


def test_is_color_warper_gpu_eligible_false_for_a_bogus_working_space():
    assert gcw.is_color_warper_gpu_eligible(aces_drt.DEFAULT_CONFIG_NAME, "not a real colourspace") is False
    print("gpu_color_warper_shader.is_color_warper_gpu_eligible: False (not "
          "a crash) for an unknown working colourspace name -- OK")


def test_gpu_render_matches_cpu_for_a_log_working_space():
    _app, context, surface = gsp._make_offscreen_gl_context()
    cache = gcw.ColorWarperGpuCache()
    entry = cache.get_entry(aces_drt.DEFAULT_CONFIG_NAME, "ACEScct")

    rng = np.random.default_rng(0)
    test_image = rng.uniform(0.05, 0.95, size=(6, 10, 3)).astype(np.float32)
    points = [{"h0": 35.0, "c0": 0.15, "h": 60.0, "c": 0.25},
              {"h0": 250.0, "c0": 0.1, "h": 250.0, "c": 0.02}]
    wake, dropoff = 40.0, 0.3

    gpu_result = gsp.render_program_on_gpu(
        entry.program, test_image, set_uniforms=_set_uniforms(points, wake, dropoff))
    cpu_result = cw.apply_color_warper_in_working_space(
        test_image.astype(np.float64), "ACEScct", points, wake=wake, dropoff=dropoff)

    delta = np.abs(gpu_result.astype(np.float64) - cpu_result)
    cache.clear()
    context.doneCurrent()
    assert delta.max() < 1e-2, delta.max()
    print(f"gpu_color_warper_shader: GPU render matches "
          f"color_warper.apply_color_warper_in_working_space for a log "
          f"working space (ACEScct), max|delta|={delta.max():.6f} -- OK")


def test_gpu_render_matches_cpu_for_an_already_linear_working_space():
    _app, context, surface = gsp._make_offscreen_gl_context()
    cache = gcw.ColorWarperGpuCache()
    entry = cache.get_entry(aces_drt.DEFAULT_CONFIG_NAME, "ACEScg")

    rng = np.random.default_rng(1)
    test_image = rng.uniform(0.02, 0.9, size=(6, 10, 3)).astype(np.float32)
    points = [{"h0": 142.5, "c0": 0.2, "h": 120.0, "c": 0.35}]
    wake, dropoff = 25.0, 0.8

    gpu_result = gsp.render_program_on_gpu(
        entry.program, test_image, set_uniforms=_set_uniforms(points, wake, dropoff))
    cpu_result = cw.apply_color_warper_in_working_space(
        test_image.astype(np.float64), "ACEScg", points, wake=wake, dropoff=dropoff)

    delta = np.abs(gpu_result.astype(np.float64) - cpu_result)
    cache.clear()
    context.doneCurrent()
    assert delta.max() < 1e-2, delta.max()
    print(f"gpu_color_warper_shader: GPU render matches CPU for an "
          f"already-linear working space (ACEScg), "
          f"max|delta|={delta.max():.6f} -- OK")


def test_identity_points_is_an_exact_noop_on_the_gpu():
    _app, context, surface = gsp._make_offscreen_gl_context()
    cache = gcw.ColorWarperGpuCache()
    entry = cache.get_entry(aces_drt.DEFAULT_CONFIG_NAME, "ACEScct")

    rng = np.random.default_rng(2)
    test_image = rng.uniform(0.05, 0.95, size=(4, 6, 3)).astype(np.float32)
    points = _sample_identity_points()

    gpu_result = gsp.render_program_on_gpu(
        entry.program, test_image, set_uniforms=_set_uniforms(points, cw.DEFAULT_WAKE, cw.DEFAULT_DROPOFF))
    delta = np.abs(gpu_result.astype(np.float64) - test_image.astype(np.float64))
    cache.clear()
    context.doneCurrent()
    assert delta.max() < 1e-2, delta.max()
    print(f"gpu_color_warper_shader: a non-trivial all-identity points "
          f"list is a GPU-side decode/warp/encode round trip, "
          f"max|delta|={delta.max():.6f} -- OK")


def test_no_points_is_an_exact_noop_on_the_gpu():
    """uCwPointCount=0 -- the warp loop's own weight_sum stays 0, so the
    shader's fallback branch (hueOut=hue, chromaScaleOut=1.0) should
    leave every pixel exactly where it started, same as the CPU side's
    early-return for an empty points list."""
    _app, context, surface = gsp._make_offscreen_gl_context()
    cache = gcw.ColorWarperGpuCache()
    entry = cache.get_entry(aces_drt.DEFAULT_CONFIG_NAME, "ACEScct")

    rng = np.random.default_rng(3)
    test_image = rng.uniform(0.05, 0.95, size=(4, 6, 3)).astype(np.float32)

    gpu_result = gsp.render_program_on_gpu(
        entry.program, test_image, set_uniforms=_set_uniforms([], cw.DEFAULT_WAKE, cw.DEFAULT_DROPOFF))
    delta = np.abs(gpu_result.astype(np.float64) - test_image.astype(np.float64))
    cache.clear()
    context.doneCurrent()
    assert delta.max() < 1e-2, delta.max()
    print(f"gpu_color_warper_shader: zero points is a GPU-side no-op, "
          f"max|delta|={delta.max():.6f} -- OK")


def test_cache_reuses_the_same_entry_for_the_same_fingerprint():
    _app, context, surface = gsp._make_offscreen_gl_context()
    cache = gcw.ColorWarperGpuCache()

    entry_a = cache.get_entry(aces_drt.DEFAULT_CONFIG_NAME, "ACEScct")
    entry_b = cache.get_entry(aces_drt.DEFAULT_CONFIG_NAME, "ACEScct")
    assert entry_a is entry_b, "the same (config, working_colourspace) must return the SAME cache entry"

    entry_c = cache.get_entry(aces_drt.DEFAULT_CONFIG_NAME, "ACEScg")
    assert entry_c is not entry_a, "a different working_colourspace must get its own entry"
    assert entry_c.program != entry_a.program

    cache.clear()
    context.doneCurrent()
    print("ColorWarperGpuCache: reuses the same entry per (config, working_colourspace), "
          "a different working space gets its own entry -- OK")


def test_clear_deletes_compiled_programs():
    _app, context, surface = gsp._make_offscreen_gl_context()
    cache = gcw.ColorWarperGpuCache()
    entry = cache.get_entry(aces_drt.DEFAULT_CONFIG_NAME, "ACEScct")
    assert gl.glIsProgram(entry.program)
    cache.clear()
    assert not gl.glIsProgram(entry.program), "clear() should delete the compiled program"
    context.doneCurrent()
    print("ColorWarperGpuCache.clear(): deletes the compiled program -- OK")


if __name__ == "__main__":
    test_is_color_warper_gpu_eligible_for_a_real_working_space()
    test_is_color_warper_gpu_eligible_false_for_a_bogus_working_space()
    test_gpu_render_matches_cpu_for_a_log_working_space()
    test_gpu_render_matches_cpu_for_an_already_linear_working_space()
    test_identity_points_is_an_exact_noop_on_the_gpu()
    test_no_points_is_an_exact_noop_on_the_gpu()
    test_cache_reuses_the_same_entry_for_the_same_fingerprint()
    test_clear_deletes_compiled_programs()
    print()
    print("All gpu_color_warper_shader tests passed.")
