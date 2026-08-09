"""
gpu_shader_cache_benchmark.py -- GPU_ACCELERATION_PLAN_2026-08-08.md,
STAGE 3 deliverable: "a benchmark showing shader-generation cost is paid
once per distinct stage configuration, not once per frame/edit."

Two comparisons, both against the naive "regenerate+recompile on every
edit" approach this whole caching layer exists to avoid:

1. CDL: simulates a slider drag (many edits, each a NEW parameter value)
   -- naive (OCIO codegen + GL compile per edit) vs. gpu_cdl_shader's
   uniform-driven approach (compile once, glUniform* per edit).
2. ACES DRT: simulates switching between a few display/view choices
   (each picked more than once, e.g. flipping back and forth) -- naive
   (OCIO codegen + GL compile every time) vs. OcioGpuStageCache
   (compile once per distinct fingerprint, dict lookup thereafter).

Run with: python3 gpu_shader_cache_benchmark.py
"""
import functools
import time

import numpy as np
import PyOpenColorIO as ocio

import aces_drt
import cdl
import gpu_cdl_shader
import gpu_shader_cache
import gpu_shader_prototype as gsp


def benchmark_cdl(n=30):
    print(f"=== CDL: simulating {n} slider-drag edits ===")
    rng = np.random.default_rng(42)
    image = rng.uniform(0.0, 1.0, size=(256, 256, 3)).astype(np.float32)
    param_sequence = [
        dict(slope=tuple(rng.uniform(0.5, 1.5, size=3)),
             offset=tuple(rng.uniform(-0.1, 0.1, size=3)),
             power=tuple(rng.uniform(0.8, 1.2, size=3)),
             sat=float(rng.uniform(0.5, 1.5)))
        for _ in range(n)
    ]

    def naive_edit(params):
        transform = cdl.build_cdl_transform(**params, style="no_clip")
        processor = cdl._get_raw_config().getProcessor(transform)
        shader_text, *_ = gsp._extract_ocio_glsl(processor)
        program = gsp.compile_ocio_program(shader_text)
        gsp.render_program_on_gpu(program, image)
        gsp.gl.glDeleteProgram(program)

    t0 = time.perf_counter()
    for params in param_sequence:
        naive_edit(params)
    naive_ms = (time.perf_counter() - t0) / n * 1000.0

    cached = gpu_shader_cache.CdlGpuProgram()
    program = cached.get_program()

    def cached_edit(params):
        set_uniforms = functools.partial(
            gpu_cdl_shader.set_cdl_uniforms, gsp.gl,
            slope=params["slope"], offset=params["offset"],
            power=params["power"], saturation=params["sat"], style="no_clip")
        gsp.render_program_on_gpu(program, image, set_uniforms=set_uniforms)

    t0 = time.perf_counter()
    for params in param_sequence:
        cached_edit(params)
    cached_ms = (time.perf_counter() - t0) / n * 1000.0

    print(f"  naive (regenerate + recompile every edit):  {naive_ms:.3f} ms/edit")
    print(f"  cached (compile once, uniforms per edit):   {cached_ms:.3f} ms/edit")
    print(f"  speedup: {naive_ms / cached_ms:.1f}x")


def benchmark_aces_drt(n_switches=20):
    print(f"\n=== ACES DRT: simulating {n_switches} display/view switches "
          f"among 3 choices ===")
    rng = np.random.default_rng(1)
    image = rng.uniform(0.0, 1.0, size=(256, 256, 3)).astype(np.float32)
    config_name = aces_drt.DEFAULT_CONFIG_NAME
    cfg = aces_drt._get_config(config_name)
    all_displays = aces_drt.list_displays(config_name)
    choices = []
    for display in all_displays[:3] or all_displays:
        views = aces_drt.list_views(config_name, display=display)
        if views:
            choices.append((config_name, "ACEScct", display, views[0]))
    if len(choices) < 2:
        print("  (fewer than 2 real display/view choices available in this "
              "OCIO config -- skipping, nothing meaningful to switch between)")
        return
    sequence = [choices[i % len(choices)] for i in range(n_switches)]

    def naive_switch(fp):
        config_name, working_colourspace, display, view = fp
        processor = cfg.getProcessor(
            ocio.DisplayViewTransform(src=working_colourspace, display=display, view=view))
        shader_text, *_ = gsp._extract_ocio_glsl(processor)
        program = gsp.compile_ocio_program(shader_text)
        gsp.render_program_on_gpu(program, image)
        gsp.gl.glDeleteProgram(program)

    t0 = time.perf_counter()
    for fp in sequence:
        naive_switch(fp)
    naive_ms = (time.perf_counter() - t0) / n_switches * 1000.0

    def generate_glsl(config_name, working_colourspace, display, view):
        processor = cfg.getProcessor(
            ocio.DisplayViewTransform(src=working_colourspace, display=display, view=view))
        shader_text, *_ = gsp._extract_ocio_glsl(processor)
        return shader_text

    stage_cache = gpu_shader_cache.OcioGpuStageCache("benchmark_aces_drt", generate_glsl)
    import shutil
    import tempfile
    from pathlib import Path
    tmpdir = Path(tempfile.mkdtemp())
    stage_cache._disk_dir = tmpdir
    try:
        t0 = time.perf_counter()
        for fp in sequence:
            program = stage_cache.get_program(fp)
            gsp.render_program_on_gpu(program, image)
        cached_ms = (time.perf_counter() - t0) / n_switches * 1000.0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print(f"  naive (regenerate + recompile every switch): {naive_ms:.3f} ms/switch")
    print(f"  cached (compile once per distinct config):   {cached_ms:.3f} ms/switch")
    print(f"  speedup: {naive_ms / cached_ms:.1f}x")


def main():
    _app, context, surface = gsp._make_offscreen_gl_context()
    benchmark_cdl()
    benchmark_aces_drt()
    context.doneCurrent()


if __name__ == "__main__":
    main()
