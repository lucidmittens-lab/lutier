"""Tests for gpu_shader_cache.py -- run directly:
python3 -m tests.test_gpu_shader_cache

Requires a real (offscreen but genuine) OpenGL context, same as
gpu_shader_prototype.py/test_gpu_cdl_shader.py -- excluded from the plain
`pytest -q` suite via conftest.py's `collect_ignore` for the same real,
reproduced GL-context-interaction reason documented there and in
test_gpu_cdl_shader.py's own docstring. Run standalone: `python3
-m tests.test_gpu_shader_cache`. Uses a temp directory for the disk-cache tests
rather than the real workspace_io.DEFAULT_WORKSPACE_DIR, so running this
never touches (or depends on) whatever a real launch of the app has
already cached.
"""
import shutil
import tempfile
from pathlib import Path

import numpy as np
import PyOpenColorIO as ocio
from OpenGL import GL as gl

import aces_drt
import gpu_cdl_shader
import gpu_shader_cache
import gpu_shader_prototype as gsp


def _aces_drt_fingerprint():
    config_name = aces_drt.DEFAULT_CONFIG_NAME
    display, view = aces_drt.get_default_display_and_view(config_name)
    return (config_name, "ACEScct", display, view)


def _generate_aces_drt_glsl(config_name, working_colourspace, display, view):
    cfg = aces_drt._get_config(config_name)
    processor = cfg.getProcessor(
        ocio.DisplayViewTransform(src=working_colourspace, display=display, view=view))
    shader_text, *_ = gsp._extract_ocio_glsl(processor)
    return shader_text


def test_ocio_gpu_stage_cache_compiles_once_per_fingerprint():
    _app, context, surface = gsp._make_offscreen_gl_context()
    calls = []

    def counting_generate(*fingerprint):
        calls.append(fingerprint)
        return _generate_aces_drt_glsl(*fingerprint)

    tmpdir = Path(tempfile.mkdtemp())
    try:
        cache = gpu_shader_cache.OcioGpuStageCache("aces_drt_test", counting_generate)
        cache._disk_dir = tmpdir / "aces_drt_test"  # isolate from the real workspace dir

        fp = _aces_drt_fingerprint()
        program_a = cache.get_program(fp)
        program_b = cache.get_program(fp)
        assert program_a == program_b, "same fingerprint must return the SAME compiled program"
        assert len(calls) == 1, (
            f"expected exactly 1 GLSL-generation call for a repeated fingerprint, got {len(calls)}")
        print("OcioGpuStageCache: repeated get_program() for the same fingerprint "
              "compiles exactly once -- OK")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        context.doneCurrent()


def test_ocio_gpu_stage_cache_disk_cache_skips_regeneration_across_instances():
    """A fresh cache INSTANCE (simulating a new app launch) pointed at the
    same disk directory should skip calling generate_glsl_fn again --
    that's the actual cross-restart win this cache provides (see module
    docstring on why compiled programs themselves aren't persisted)."""
    _app, context, surface = gsp._make_offscreen_gl_context()
    tmpdir = Path(tempfile.mkdtemp())
    try:
        calls_a = []
        cache_a = gpu_shader_cache.OcioGpuStageCache(
            "aces_drt_test", lambda *fp: (calls_a.append(fp), _generate_aces_drt_glsl(*fp))[1])
        cache_a._disk_dir = tmpdir
        fp = _aces_drt_fingerprint()
        cache_a.get_program(fp)
        assert len(calls_a) == 1

        calls_b = []
        cache_b = gpu_shader_cache.OcioGpuStageCache(
            "aces_drt_test", lambda *fp: (calls_b.append(fp), _generate_aces_drt_glsl(*fp))[1])
        cache_b._disk_dir = tmpdir  # same directory cache_a just wrote to
        cache_b.get_program(fp)
        assert len(calls_b) == 0, (
            "a fresh cache instance with a pre-populated disk dir should "
            "read the cached GLSL, not regenerate it via OCIO")
        print("OcioGpuStageCache: a fresh instance reads a previously-written "
              "disk cache instead of regenerating GLSL via OCIO -- OK")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        context.doneCurrent()


def test_ocio_gpu_stage_cache_program_actually_renders_correctly():
    """Not just "compiles without error" -- the returned program produces
    the same output aces_drt.apply_aces_output_transform (CPU) does,
    same standard this whole project holds every stage to."""
    _app, context, surface = gsp._make_offscreen_gl_context()
    tmpdir = Path(tempfile.mkdtemp())
    try:
        cache = gpu_shader_cache.OcioGpuStageCache("aces_drt_test", _generate_aces_drt_glsl)
        cache._disk_dir = tmpdir
        fp = _aces_drt_fingerprint()
        program = cache.get_program(fp)

        rng = np.random.default_rng(7)
        image = rng.uniform(0.0, 1.0, size=(32, 32, 3)).astype(np.float32)
        gpu_result = gsp.render_program_on_gpu(program, image)
        cpu_result = aces_drt.apply_aces_output_transform(
            image, working_colourspace=fp[1], display=fp[2], view=fp[3], config_name=fp[0])
        delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
        assert delta.max() < 1e-3, f"cached program's rendered output diverges from CPU by {delta.max()}"
        print(f"OcioGpuStageCache: cached program's actual rendered output matches "
              f"the CPU reference, max|delta|={delta.max():.8f} -- OK")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        context.doneCurrent()


def test_cdl_gpu_program_compiles_once_and_is_reused():
    _app, context, surface = gsp._make_offscreen_gl_context()
    cache = gpu_shader_cache.CdlGpuProgram()
    program_a = cache.get_program()
    program_b = cache.get_program()
    assert program_a == program_b
    context.doneCurrent()
    print("CdlGpuProgram: repeated get_program() returns the same compiled "
          "program, no per-call recompilation -- OK")


def test_clear_forces_recompilation():
    _app, context, surface = gsp._make_offscreen_gl_context()
    tmpdir = Path(tempfile.mkdtemp())
    try:
        calls = []
        cache = gpu_shader_cache.OcioGpuStageCache(
            "aces_drt_test", lambda *fp: (calls.append(fp), _generate_aces_drt_glsl(*fp))[1])
        cache._disk_dir = tmpdir
        fp = _aces_drt_fingerprint()
        cache.get_program(fp)
        cache.clear()
        cache.get_program(fp)
        assert len(calls) == 1, (
            "clear() should drop the in-memory compiled program, but the "
            "disk-cached GLSL should still be read rather than "
            "regenerated -- got a second OCIO call" if len(calls) > 1
            else "unexpected: clear() should still require a fresh compile")
        print("OcioGpuStageCache.clear(): drops compiled programs but the "
              "disk-cached GLSL source is still reused, not regenerated -- OK")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        context.doneCurrent()


if __name__ == "__main__":
    test_ocio_gpu_stage_cache_compiles_once_per_fingerprint()
    test_ocio_gpu_stage_cache_disk_cache_skips_regeneration_across_instances()
    test_ocio_gpu_stage_cache_program_actually_renders_correctly()
    test_cdl_gpu_program_compiles_once_and_is_reused()
    test_clear_forces_recompilation()
    print()
    print("All gpu_shader_cache tests passed.")
