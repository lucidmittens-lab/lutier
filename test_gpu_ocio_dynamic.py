"""Tests for gpu_ocio_dynamic.py -- run directly:
python3 test_gpu_ocio_dynamic.py

Turns OCIO_TIER1_GRADING_OPERATORS_PLAN_2026-08-09.md's own "Ground
truth" validation (done ad hoc while writing that plan) into a permanent,
repeatable check: for each of the four Tier 1 dynamic OCIO transforms
(GradingPrimary, GradingRGBCurve, ExposureContrast, GradingTone), builds
a real GL program via OcioDynamicGpuStageCache, pushes real parameter
values through push_dynamic_uniforms, renders on the actual GPU, and
compares against ocio.<Transform>.setValue(...)/getDefaultCPUProcessor()
-- the same "measure the real thing, don't extrapolate" standard every
other GPU stage kind in this project is held to.

Requires a real (offscreen but genuine) OpenGL context -- excluded from
`pytest -q` via conftest.py's collect_ignore, same reason as every other
GPU test file. Run standalone: `python3 test_gpu_ocio_dynamic.py`.
"""
import numpy as np
import PyOpenColorIO as ocio

import cdl
import gpu_ocio_dynamic as god
import gpu_shader_prototype as gsp


def _render(entry, rgb_array):
    def set_uniforms(program):
        god.push_dynamic_uniforms(program, entry.shader_desc)
    return gsp.render_program_on_gpu(entry.program, rgb_array, set_uniforms=set_uniforms)


def _cpu_apply(transform, rgb_array):
    cfg = cdl._get_raw_config()
    proc = cfg.getProcessor(transform).getDefaultCPUProcessor()
    result = rgb_array.copy()
    proc.applyRGB(result)
    return result


def test_grading_primary_matches_cpu():
    _app, context, surface = gsp._make_offscreen_gl_context()
    cache = god.OcioDynamicGpuStageCache(
        "grading_primary", (ocio.DYNAMIC_PROPERTY_GRADING_PRIMARY,))

    def build():
        t = ocio.GradingPrimaryTransform(ocio.GRADING_LOG)
        t.makeDynamic()
        return t

    entry = cache.get_entry(("grading_primary", "log"), build)
    target = ocio.GradingPrimary(ocio.GRADING_LOG)
    target.brightness = ocio.GradingRGBM(0.02, -0.01, 0.03, 0.0)
    target.contrast = ocio.GradingRGBM(1.1, 1.05, 0.95, 1.0)
    target.gamma = ocio.GradingRGBM(1.0, 1.0, 1.0, 1.05)
    target.saturation = 1.1
    entry.dynamic_properties[ocio.DYNAMIC_PROPERTY_GRADING_PRIMARY].setGradingPrimary(target)

    rng = np.random.default_rng(0)
    src = rng.uniform(0, 1, size=(16, 16, 3)).astype(np.float32)
    gpu_result = _render(entry, src)

    cpu_transform = ocio.GradingPrimaryTransform(ocio.GRADING_LOG)
    cpu_transform.setValue(target)
    cpu_result = _cpu_apply(cpu_transform, src)

    delta = np.abs(gpu_result.astype(np.float64) - cpu_result.astype(np.float64))
    context.doneCurrent()
    assert delta.max() < 1e-3, delta.max()
    print(f"gpu_ocio_dynamic: GradingPrimaryTransform matches CPU, "
          f"max|delta|={delta.max():.7f} -- OK")


def test_grading_rgb_curve_matches_cpu():
    _app, context, surface = gsp._make_offscreen_gl_context()
    cache = god.OcioDynamicGpuStageCache(
        "grading_rgb_curve", (ocio.DYNAMIC_PROPERTY_GRADING_RGBCURVE,))

    def build():
        t = ocio.GradingRGBCurveTransform(ocio.GRADING_LOG)
        t.makeDynamic()
        return t

    entry = cache.get_entry(("grading_rgb_curve", "log"), build)
    dp = entry.dynamic_properties[ocio.DYNAMIC_PROPERTY_GRADING_RGBCURVE]
    curve = dp.getGradingRGBCurve()
    # Real API gotcha (see plan doc): getControlPoints() items don't write
    # back -- rebuild the whole curve via the flat-sequence constructor.
    new_master = ocio.GradingBSplineCurve([0.0, 0.0, 0.2, 0.35, 0.5, 0.55, 0.8, 0.7, 1.0, 1.0])
    curve.master = new_master
    dp.setGradingRGBCurve(curve)

    rng = np.random.default_rng(1)
    src = rng.uniform(0, 1, size=(16, 16, 3)).astype(np.float32)
    gpu_result = _render(entry, src)

    cpu_transform = ocio.GradingRGBCurveTransform(ocio.GRADING_LOG)
    cpu_value = cpu_transform.getValue()
    cpu_value.master = new_master
    cpu_transform.setValue(cpu_value)
    cpu_result = _cpu_apply(cpu_transform, src)

    delta = np.abs(gpu_result.astype(np.float64) - cpu_result.astype(np.float64))
    context.doneCurrent()
    assert delta.max() < 1e-3, delta.max()
    print(f"gpu_ocio_dynamic: GradingRGBCurveTransform matches CPU, "
          f"max|delta|={delta.max():.7f} -- OK")


def test_exposure_contrast_matches_cpu():
    """The one kind with THREE independent dynamic properties instead of
    one -- see module/plan docstrings on why the cache's
    dynamic_properties dict shape has to handle this."""
    _app, context, surface = gsp._make_offscreen_gl_context()
    prop_types = (ocio.DYNAMIC_PROPERTY_EXPOSURE,
                  ocio.DYNAMIC_PROPERTY_CONTRAST,
                  ocio.DYNAMIC_PROPERTY_GAMMA)
    cache = god.OcioDynamicGpuStageCache("exposure_contrast", prop_types)

    def build():
        t = ocio.ExposureContrastTransform()
        t.makeExposureDynamic()
        t.makeContrastDynamic()
        t.makeGammaDynamic()
        return t

    entry = cache.get_entry(("exposure_contrast", "linear"), build)
    entry.dynamic_properties[ocio.DYNAMIC_PROPERTY_EXPOSURE].setDouble(0.4)
    entry.dynamic_properties[ocio.DYNAMIC_PROPERTY_CONTRAST].setDouble(1.15)
    entry.dynamic_properties[ocio.DYNAMIC_PROPERTY_GAMMA].setDouble(1.05)

    rng = np.random.default_rng(2)
    src = rng.uniform(0, 1, size=(16, 16, 3)).astype(np.float32)
    gpu_result = _render(entry, src)

    cpu_transform = ocio.ExposureContrastTransform()
    cpu_transform.setExposure(0.4)
    cpu_transform.setContrast(1.15)
    cpu_transform.setGamma(1.05)
    cpu_result = _cpu_apply(cpu_transform, src)

    delta = np.abs(gpu_result.astype(np.float64) - cpu_result.astype(np.float64))
    context.doneCurrent()
    assert delta.max() < 1e-3, delta.max()
    print(f"gpu_ocio_dynamic: ExposureContrastTransform (3 independent "
          f"dynamic properties) matches CPU, max|delta|={delta.max():.7f} -- OK")


def test_grading_tone_matches_cpu():
    _app, context, surface = gsp._make_offscreen_gl_context()
    cache = god.OcioDynamicGpuStageCache(
        "grading_tone", (ocio.DYNAMIC_PROPERTY_GRADING_TONE,))

    def build():
        t = ocio.GradingToneTransform(ocio.GRADING_LOG)
        t.makeDynamic()
        return t

    entry = cache.get_entry(("grading_tone", "log"), build)
    dp = entry.dynamic_properties[ocio.DYNAMIC_PROPERTY_GRADING_TONE]
    tone = dp.getGradingTone()
    midtones = tone.midtones
    tone.midtones = ocio.GradingRGBMSW(1.05, 0.98, 1.02, 1.0, midtones.start, midtones.width)
    dp.setGradingTone(tone)

    rng = np.random.default_rng(3)
    src = rng.uniform(0, 1, size=(16, 16, 3)).astype(np.float32)
    gpu_result = _render(entry, src)

    cpu_transform = ocio.GradingToneTransform(ocio.GRADING_LOG)
    cpu_value = cpu_transform.getValue()
    cpu_value.midtones = tone.midtones
    cpu_transform.setValue(cpu_value)
    cpu_result = _cpu_apply(cpu_transform, src)

    delta = np.abs(gpu_result.astype(np.float64) - cpu_result.astype(np.float64))
    context.doneCurrent()
    assert delta.max() < 1e-3, delta.max()
    print(f"gpu_ocio_dynamic: GradingToneTransform matches CPU, "
          f"max|delta|={delta.max():.7f} -- OK")


def test_cache_reuses_the_same_entry_for_a_repeated_fingerprint():
    _app, context, surface = gsp._make_offscreen_gl_context()
    cache = god.OcioDynamicGpuStageCache(
        "grading_primary", (ocio.DYNAMIC_PROPERTY_GRADING_PRIMARY,))
    calls = []

    def build():
        calls.append(1)
        t = ocio.GradingPrimaryTransform(ocio.GRADING_LOG)
        t.makeDynamic()
        return t

    entry_a = cache.get_entry(("grading_primary", "log"), build)
    entry_b = cache.get_entry(("grading_primary", "log"), build)
    context.doneCurrent()
    assert entry_a is entry_b, "same fingerprint must return the SAME cache entry"
    assert len(calls) == 1, (
        f"expected exactly 1 build call for a repeated fingerprint, got {len(calls)}")
    print("OcioDynamicGpuStageCache: repeated get_entry() for the same "
          "fingerprint builds exactly once -- OK")


def test_cache_distinguishes_fingerprints_by_style():
    """A different style is a genuinely different shader STRUCTURE (see
    plan doc's caching-fingerprint reasoning) -- must NOT be conflated
    with the same cache entry just because both are "grading_primary.\""""
    _app, context, surface = gsp._make_offscreen_gl_context()
    cache = god.OcioDynamicGpuStageCache(
        "grading_primary", (ocio.DYNAMIC_PROPERTY_GRADING_PRIMARY,))

    def build_log():
        t = ocio.GradingPrimaryTransform(ocio.GRADING_LOG)
        t.makeDynamic()
        return t

    def build_lin():
        t = ocio.GradingPrimaryTransform(ocio.GRADING_LIN)
        t.makeDynamic()
        return t

    entry_log = cache.get_entry(("grading_primary", "log"), build_log)
    entry_lin = cache.get_entry(("grading_primary", "lin"), build_lin)
    context.doneCurrent()
    assert entry_log is not entry_lin, "different styles must get different cache entries"
    assert entry_log.program != entry_lin.program, "different styles must get different compiled programs"
    print("OcioDynamicGpuStageCache: different styles get different cache "
          "entries/compiled programs -- OK")


def test_clear_deletes_programs_and_forces_a_rebuild():
    _app, context, surface = gsp._make_offscreen_gl_context()
    cache = god.OcioDynamicGpuStageCache(
        "grading_primary", (ocio.DYNAMIC_PROPERTY_GRADING_PRIMARY,))
    calls = []

    def build():
        calls.append(1)
        t = ocio.GradingPrimaryTransform(ocio.GRADING_LOG)
        t.makeDynamic()
        return t

    cache.get_entry(("grading_primary", "log"), build)
    cache.clear()
    cache.get_entry(("grading_primary", "log"), build)
    context.doneCurrent()
    assert len(calls) == 2, "clear() should force a fresh build on the next get_entry()"
    print("OcioDynamicGpuStageCache.clear(): forces a rebuild on the next "
          "get_entry() call -- OK")


if __name__ == "__main__":
    test_grading_primary_matches_cpu()
    test_grading_rgb_curve_matches_cpu()
    test_exposure_contrast_matches_cpu()
    test_grading_tone_matches_cpu()
    test_cache_reuses_the_same_entry_for_a_repeated_fingerprint()
    test_cache_distinguishes_fingerprints_by_style()
    test_clear_deletes_programs_and_forces_a_rebuild()
    print()
    print("All gpu_ocio_dynamic tests passed.")
