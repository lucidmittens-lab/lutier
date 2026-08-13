"""Tests for gpu_custom_drt_shader.py -- run directly:
python3 test_gpu_custom_drt_shader.py

Requires a real (offscreen but genuine) OpenGL context -- excluded from
`pytest -q` via conftest.py's collect_ignore, same reason as every other
real-GL-context GPU test file. Run standalone: `python3
test_gpu_custom_drt_shader.py`.
"""
import numpy as np
import OpenGL.GL as gl

import aces_drt
import custom_drt as cdr
import gpu_custom_drt_shader as gcdr
import gpu_shader_prototype as gsp

_DEFAULT_UNIFORM_PARAMS = dict(
    contrast=cdr.DEFAULT_CONTRAST, headroom=cdr.DEFAULT_HEADROOM,
    blend_percentile=cdr.DEFAULT_BLEND_PERCENTILE, tail_width=cdr.DEFAULT_TAIL_WIDTH,
    desat_start=cdr.DEFAULT_DESAT_START, desat_end=cdr.DEFAULT_DESAT_END,
    identity_zone=cdr.DEFAULT_IDENTITY_ZONE, transition_softness=cdr.DEFAULT_TRANSITION_SOFTNESS,
    fullness_r=cdr.DEFAULT_FULLNESS_R, fullness_g=cdr.DEFAULT_FULLNESS_G,
    fullness_b=cdr.DEFAULT_FULLNESS_B, mastering_brightness=cdr.DEFAULT_MASTERING_BRIGHTNESS,
)


def test_is_custom_drt_gpu_eligible_for_a_real_working_space():
    assert gcdr.is_custom_drt_gpu_eligible(aces_drt.DEFAULT_CONFIG_NAME, "ACEScct", "SDR", None) is True
    print("gpu_custom_drt_shader.is_custom_drt_gpu_eligible: "
          "True for a real working colourspace -- OK")


def test_is_custom_drt_gpu_eligible_false_for_a_bogus_working_space():
    assert gcdr.is_custom_drt_gpu_eligible(
        aces_drt.DEFAULT_CONFIG_NAME, "not a real colourspace", "SDR", None) is False
    print("gpu_custom_drt_shader.is_custom_drt_gpu_eligible: "
          "False (not a crash) for an unknown working colourspace name -- OK")


def test_is_custom_drt_gpu_eligible_false_for_a_bogus_mastering_display():
    assert gcdr.is_custom_drt_gpu_eligible(
        aces_drt.DEFAULT_CONFIG_NAME, "ACEScct", "not a real display", None) is False
    print("gpu_custom_drt_shader.is_custom_drt_gpu_eligible: "
          "False (not a crash) for an unknown mastering_display -- OK")


def test_is_custom_drt_gpu_eligible_false_for_a_cross_family_final_target():
    assert gcdr.is_custom_drt_gpu_eligible(
        aces_drt.DEFAULT_CONFIG_NAME, "ACEScct", "SDR", "DCI-P3") is False
    print("gpu_custom_drt_shader.is_custom_drt_gpu_eligible: "
          "False (not a crash) for a cross-family final_target -- OK")


def _set_uniforms(params):
    def _set(program):
        gcdr.set_custom_drt_uniforms(gl, program, **params)
    return _set


def _render_and_compare(working_colourspace, mastering_display, final_target, uniform_params,
                         test_image, label):
    cache = gcdr.CustomDrtGpuCache()
    entry = cache.get_entry(aces_drt.DEFAULT_CONFIG_NAME, working_colourspace, mastering_display,
                             final_target)

    gpu_result = gsp.render_program_on_gpu(
        entry.program, test_image, set_uniforms=_set_uniforms(uniform_params))
    cpu_result = cdr.apply_custom_drt(
        test_image.astype(np.float64), working_colourspace, mastering_display=mastering_display,
        final_target=final_target, **uniform_params)

    delta = np.abs(gpu_result.astype(np.float64) - cpu_result)
    cache.clear()
    assert delta.max() < 1e-3, (label, delta.max())
    print(f"gpu_custom_drt_shader: GPU render matches custom_drt.apply_custom_drt for "
          f"{label}, max|delta|={delta.max():.6f} -- OK")


def test_gpu_render_matches_cpu_at_default_params():
    _app, context, surface = gsp._make_offscreen_gl_context()
    rng = np.random.default_rng(0)
    # Wide range including values over 1.0 -- real ACEScct scene-linear
    # data routinely exceeds that, same range custom_drt.py's own tests use.
    test_image = rng.uniform(-0.1, 3.0, size=(8, 12, 3)).astype(np.float32)
    _render_and_compare(
        "ACEScct", "SDR", None, _DEFAULT_UNIFORM_PARAMS, test_image,
        "default params (ACEScct, SDR passthrough)")
    context.doneCurrent()


def test_gpu_render_matches_cpu_at_toms_tuned_values():
    """The exact values from Tom's own tuning pass (2026-08-13, ARRI
    LogC4 source, contrast 0.96/headroom 0.6/tight highlight blend) --
    not just the shipped defaults, so a real, previously-used
    configuration is covered too."""
    _app, context, surface = gsp._make_offscreen_gl_context()
    rng = np.random.default_rng(1)
    test_image = rng.uniform(-0.1, 3.0, size=(8, 12, 3)).astype(np.float32)
    params = dict(_DEFAULT_UNIFORM_PARAMS,
                  contrast=0.96, headroom=0.6, blend_percentile=0.95, tail_width=0.5,
                  desat_start=0.87, desat_end=1.0, identity_zone=0.8, transition_softness=0.3,
                  fullness_r=0.0, fullness_g=0.0, fullness_b=0.0)
    _render_and_compare(
        "ARRI LogC4", "SDR", None, params, test_image, "Tom's tuned values (ARRI LogC4)")
    context.doneCurrent()


def test_gpu_render_matches_cpu_with_nonzero_fullness():
    _app, context, surface = gsp._make_offscreen_gl_context()
    rng = np.random.default_rng(2)
    test_image = rng.uniform(0.0, 4.0, size=(8, 12, 3)).astype(np.float32)
    _render_and_compare(
        "ACEScct", "SDR", None, _DEFAULT_UNIFORM_PARAMS, test_image, "nonzero per-channel fullness")
    context.doneCurrent()


def test_gpu_render_matches_cpu_for_a_neutral_ramp():
    """Regression coverage for the same neutral-pixel edge case
    custom_drt.py's own test_neutral_pixels_do_not_produce_nan guards on
    the CPU side -- rayBoxT's GLSL port sidesteps it structurally (see
    this module's own docstring) rather than needing an isfinite guard,
    but this confirms that actually holds against the real CPU reference,
    not just in isolation."""
    _app, context, surface = gsp._make_offscreen_gl_context()
    grey_ramp = np.stack([np.linspace(0, 2.0, 32)] * 3, axis=-1).reshape(1, 32, 3).astype(np.float32)
    _render_and_compare("ACEScct", "SDR", None, _DEFAULT_UNIFORM_PARAMS, grey_ramp, "a neutral ramp")
    context.doneCurrent()


def test_gpu_render_matches_cpu_for_every_mastering_display_and_final_target():
    """Each (mastering_display, final_target) combination bakes a
    DIFFERENT compile-time matrix + EOTF into the shader (see
    _format_rec2020_to_mastering_glsl/_format_final_target_glsl) --
    exercise every combination, not just SDR's own passthrough every
    other test above uses."""
    _app, context, surface = gsp._make_offscreen_gl_context()
    rng = np.random.default_rng(3)
    test_image = rng.uniform(-0.1, 3.0, size=(8, 12, 3)).astype(np.float32)
    for mastering_display, options in cdr.FINAL_TARGETS.items():
        for final_target in options:
            params = dict(_DEFAULT_UNIFORM_PARAMS)
            if mastering_display == "HDR":
                params["mastering_brightness"] = 1000
            _render_and_compare(
                "ACEScct", mastering_display, final_target, params, test_image,
                f"mastering_display={mastering_display}, final_target={final_target}")
    context.doneCurrent()


def test_gpu_render_matches_cpu_for_hdr_mastering_brightness_picklist():
    """PQ's mastering_brightness stays a plain uniform (not baked into
    the shader) -- exercised across a few of the picklist values (see
    custom_drt.MASTERING_BRIGHTNESS_OPTIONS) against a single compiled
    HDR program, confirming no recompile is needed to change it."""
    _app, context, surface = gsp._make_offscreen_gl_context()
    rng = np.random.default_rng(4)
    test_image = rng.uniform(0.0, 4.0, size=(8, 12, 3)).astype(np.float32)
    for mastering_brightness in (100, 1000, 10000):
        params = dict(_DEFAULT_UNIFORM_PARAMS, mastering_brightness=mastering_brightness)
        _render_and_compare(
            "ACEScct", "HDR", "P3-D65", params, test_image,
            f"HDR/P3-D65, mastering_brightness={mastering_brightness}")
    context.doneCurrent()


def test_gpu_render_matches_cpu_for_theatrical_dci_white_adaptation():
    """The one combination that bakes a REAL chromatic-adaptation matrix
    (D65 mastering white -> DCI white) into the shader, not just a
    primaries-only change -- confirms _format_final_target_glsl's matrix
    transcription is correct for that case too, not just the simpler
    shared-whitepoint ones."""
    _app, context, surface = gsp._make_offscreen_gl_context()
    rng = np.random.default_rng(5)
    test_image = rng.uniform(-0.1, 5.0, size=(8, 12, 3)).astype(np.float32)
    _render_and_compare(
        "ACEScct", "Theatrical", "DCI-P3", _DEFAULT_UNIFORM_PARAMS, test_image,
        "Theatrical -> DCI-P3 (real white-point adaptation)")
    context.doneCurrent()


if __name__ == "__main__":
    test_is_custom_drt_gpu_eligible_for_a_real_working_space()
    test_is_custom_drt_gpu_eligible_false_for_a_bogus_working_space()
    test_is_custom_drt_gpu_eligible_false_for_a_bogus_mastering_display()
    test_is_custom_drt_gpu_eligible_false_for_a_cross_family_final_target()
    test_gpu_render_matches_cpu_at_default_params()
    test_gpu_render_matches_cpu_at_toms_tuned_values()
    test_gpu_render_matches_cpu_with_nonzero_fullness()
    test_gpu_render_matches_cpu_for_a_neutral_ramp()
    test_gpu_render_matches_cpu_for_every_mastering_display_and_final_target()
    test_gpu_render_matches_cpu_for_hdr_mastering_brightness_picklist()
    test_gpu_render_matches_cpu_for_theatrical_dci_white_adaptation()
    print()
    print("All gpu_custom_drt_shader tests passed.")
