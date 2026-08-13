"""Tests for gpu_printer_lights_shader.py -- run directly:
python3 test_gpu_printer_lights_shader.py

Requires a real (offscreen but genuine) OpenGL context -- excluded from
`pytest -q` via conftest.py's collect_ignore, same reason as every other
real-GL-context GPU test file. Run standalone: `python3
test_gpu_printer_lights_shader.py`.
"""
import numpy as np
import OpenGL.GL as gl

import aces_drt
import gpu_printer_lights_shader as gpls
import gpu_shader_prototype as gsp
import printer_lights


def test_is_printer_lights_gpu_eligible_for_a_real_working_space():
    assert gpls.is_printer_lights_gpu_eligible(aces_drt.DEFAULT_CONFIG_NAME, "ACEScct") is True
    print("gpu_printer_lights_shader.is_printer_lights_gpu_eligible: "
          "True for a real working colourspace with an unambiguous linear sibling -- OK")


def test_is_printer_lights_gpu_eligible_false_for_a_bogus_working_space():
    assert gpls.is_printer_lights_gpu_eligible(aces_drt.DEFAULT_CONFIG_NAME, "not a real colourspace") is False
    print("gpu_printer_lights_shader.is_printer_lights_gpu_eligible: "
          "False (not a crash) for an unknown working colourspace name -- OK")


def _set_uniforms(gain):
    def _set(program):
        loc = gl.glGetUniformLocation(program, gpls._UNIFORM_NAME)
        gl.glUniform3f(loc, *gain)
    return _set


def test_gpu_render_matches_cpu_for_a_log_working_space():
    _app, context, surface = gsp._make_offscreen_gl_context()
    cache = gpls.PrinterLightsGpuCache()
    entry = cache.get_entry(aces_drt.DEFAULT_CONFIG_NAME, "ACEScct")

    rng = np.random.default_rng(0)
    test_image = rng.uniform(0.05, 0.95, size=(6, 10, 3)).astype(np.float32)
    r_points, g_points, b_points = 6.0, -4.0, 2.5
    gain = (printer_lights.point_gain(r_points), printer_lights.point_gain(g_points),
            printer_lights.point_gain(b_points))

    gpu_result = gsp.render_program_on_gpu(entry.program, test_image, set_uniforms=_set_uniforms(gain))
    cpu_result = printer_lights.apply_printer_lights_in_working_space(
        test_image.astype(np.float64), "ACEScct", r_points, g_points, b_points)

    delta = np.abs(gpu_result.astype(np.float64) - cpu_result)
    cache.clear()
    context.doneCurrent()
    assert delta.max() < 1e-3, delta.max()
    print(f"gpu_printer_lights_shader: GPU render matches printer_lights.apply_printer_lights_in_working_space "
          f"for a log working space (ACEScct), max|delta|={delta.max():.6f} -- OK")


def test_gpu_render_matches_cpu_for_an_already_linear_working_space():
    """ACEScg is scene-linear itself -- decode_to_linear/encode_from_linear
    are both a no-op conversion (ACEScg -> ACEScg), a real, distinct code
    path from the log case above (aces_drt._find_native_linear_colourspace
    returns the space itself when it's already linear)."""
    _app, context, surface = gsp._make_offscreen_gl_context()
    cache = gpls.PrinterLightsGpuCache()
    entry = cache.get_entry(aces_drt.DEFAULT_CONFIG_NAME, "ACEScg")

    rng = np.random.default_rng(1)
    test_image = rng.uniform(0.0, 2.0, size=(6, 10, 3)).astype(np.float32)
    r_points, g_points, b_points = -3.0, 5.0, 0.0
    gain = (printer_lights.point_gain(r_points), printer_lights.point_gain(g_points),
            printer_lights.point_gain(b_points))

    gpu_result = gsp.render_program_on_gpu(entry.program, test_image, set_uniforms=_set_uniforms(gain))
    cpu_result = printer_lights.apply_printer_lights_in_working_space(
        test_image.astype(np.float64), "ACEScg", r_points, g_points, b_points)

    delta = np.abs(gpu_result.astype(np.float64) - cpu_result)
    cache.clear()
    context.doneCurrent()
    assert delta.max() < 1e-3, delta.max()
    print(f"gpu_printer_lights_shader: GPU render matches CPU for an already-linear "
          f"working space (ACEScg), max|delta|={delta.max():.6f} -- OK")


def test_zero_points_is_an_exact_noop_on_the_gpu():
    _app, context, surface = gsp._make_offscreen_gl_context()
    cache = gpls.PrinterLightsGpuCache()
    entry = cache.get_entry(aces_drt.DEFAULT_CONFIG_NAME, "ACEScct")

    rng = np.random.default_rng(2)
    test_image = rng.uniform(0.05, 0.95, size=(4, 6, 3)).astype(np.float32)
    gain = (1.0, 1.0, 1.0)

    gpu_result = gsp.render_program_on_gpu(entry.program, test_image, set_uniforms=_set_uniforms(gain))
    delta = np.abs(gpu_result.astype(np.float64) - test_image.astype(np.float64))
    cache.clear()
    context.doneCurrent()
    assert delta.max() < 1e-3, delta.max()
    print(f"gpu_printer_lights_shader: zero points (gain 1,1,1) is a GPU-side "
          f"decode/encode round trip, max|delta|={delta.max():.6f} -- OK")


def test_cache_reuses_the_same_entry_for_the_same_fingerprint():
    _app, context, surface = gsp._make_offscreen_gl_context()
    cache = gpls.PrinterLightsGpuCache()

    entry_a = cache.get_entry(aces_drt.DEFAULT_CONFIG_NAME, "ACEScct")
    entry_b = cache.get_entry(aces_drt.DEFAULT_CONFIG_NAME, "ACEScct")
    assert entry_a is entry_b, "the same (config, working_colourspace) must return the SAME cache entry"

    entry_c = cache.get_entry(aces_drt.DEFAULT_CONFIG_NAME, "ACEScg")
    assert entry_c is not entry_a, "a different working_colourspace must get its own entry"
    assert entry_c.program != entry_a.program

    cache.clear()
    context.doneCurrent()
    print("PrinterLightsGpuCache: reuses the same entry per (config, working_colourspace), "
          "a different working space gets its own entry -- OK")


def test_clear_deletes_compiled_programs():
    _app, context, surface = gsp._make_offscreen_gl_context()
    cache = gpls.PrinterLightsGpuCache()
    entry = cache.get_entry(aces_drt.DEFAULT_CONFIG_NAME, "ACEScct")
    assert gl.glIsProgram(entry.program)
    cache.clear()
    assert not gl.glIsProgram(entry.program), "clear() should delete the compiled program"
    context.doneCurrent()
    print("PrinterLightsGpuCache.clear(): deletes the compiled program -- OK")


if __name__ == "__main__":
    test_is_printer_lights_gpu_eligible_for_a_real_working_space()
    test_is_printer_lights_gpu_eligible_false_for_a_bogus_working_space()
    test_gpu_render_matches_cpu_for_a_log_working_space()
    test_gpu_render_matches_cpu_for_an_already_linear_working_space()
    test_zero_points_is_an_exact_noop_on_the_gpu()
    test_cache_reuses_the_same_entry_for_the_same_fingerprint()
    test_clear_deletes_compiled_programs()
    print()
    print("All gpu_printer_lights_shader tests passed.")
