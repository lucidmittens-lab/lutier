"""Tests for gpu_cdl_shader.py -- run directly: python3 test_gpu_cdl_shader.py

The one thing that matters here: gpu_cdl_shader.CDL_SHADER_SOURCE is a
hand-transcribed copy of OCIO's own generated CDL GLSL (see that module's
docstring for why it exists at all instead of just using OCIO's codegen
directly), so it needs its own direct, repeated proof that it still
matches OCIO's real output -- for both styles, across many parameter
sets -- not a one-time eyeball check. Renders BOTH the hand-written
shader and OCIO's own generated shader through the exact same real GPU
render path (gpu_shader_prototype.render_program_on_gpu) and diffs them.

Requires a real (offscreen but genuine) OpenGL context via Qt's
QOffscreenSurface/QOpenGLContext -- same requirement as
gpu_shader_prototype.py itself. Excluded from the plain `pytest -q` suite
via conftest.py's `collect_ignore` -- running this in the same process as
test_undo_debounce.py's real MainWindow (own live pyqtgraph GLViewWidget)
reproduced a real (caught, non-fatal) OpenGL.error.GLError in pyqtgraph's
own rendering, see conftest.py's own comment for the full explanation.
Run standalone: `python3 test_gpu_cdl_shader.py`.
"""
import functools

import numpy as np
import PyOpenColorIO as ocio

import cdl
import gpu_cdl_shader
import gpu_shader_prototype as gsp


def _random_cdl_params(rng):
    return dict(
        slope=tuple(rng.uniform(0.5, 1.5, size=3)),
        offset=tuple(rng.uniform(-0.15, 0.15, size=3)),
        power=tuple(rng.uniform(0.7, 1.3, size=3)),
        sat=float(rng.uniform(0.3, 1.7)),
    )


def _ocio_gpu_result(params, style, image):
    transform = cdl.build_cdl_transform(**params, style=style)
    processor = cdl._get_raw_config().getProcessor(transform)
    shader_text, n_tex, n_tex3d, n_uniforms = gsp._extract_ocio_glsl(processor)
    assert (n_tex, n_tex3d, n_uniforms) == (0, 0, 0), (
        "OCIO's CDL codegen shape changed (now has textures/uniforms) -- "
        "gpu_cdl_shader.py's whole premise (OCIO bakes constants, so we "
        "hand-write instead) needs re-checking, not just this test.")
    return gsp.render_ocio_shader_on_gpu(shader_text, image)


def _handwritten_gpu_result(params, style, image):
    program = gsp.compile_ocio_program(gpu_cdl_shader.CDL_SHADER_SOURCE)
    try:
        set_uniforms = functools.partial(
            gpu_cdl_shader.set_cdl_uniforms, gsp.gl,
            slope=params["slope"], offset=params["offset"],
            power=params["power"], saturation=params["sat"], style=style)
        return gsp.render_program_on_gpu(program, image, set_uniforms=set_uniforms)
    finally:
        gsp.gl.glDeleteProgram(program)


def test_handwritten_cdl_shader_matches_ocios_own_generated_shader():
    _app, context, surface = gsp._make_offscreen_gl_context()
    rng = np.random.default_rng(20260808)
    image = rng.uniform(-0.2, 1.2, size=(48, 48, 3)).astype(np.float32)

    worst = 0.0
    for style in ("no_clip", "asc"):
        for _ in range(8):
            params = _random_cdl_params(rng)
            ocio_result = _ocio_gpu_result(params, style, image)
            handwritten_result = _handwritten_gpu_result(params, style, image)
            delta = np.abs(ocio_result.astype(np.float64) - handwritten_result.astype(np.float64))
            worst = max(worst, delta.max())
            assert delta.max() < 1e-5, (
                f"style={style!r} params={params!r}: hand-written CDL shader "
                f"diverges from OCIO's own generated shader by {delta.max()} "
                "-- this is supposed to be the same formula.")

    context.doneCurrent()
    print(f"gpu_cdl_shader.CDL_SHADER_SOURCE: matches OCIO's own generated "
          f"CDL shader across 16 random (style, params) combinations, "
          f"worst max|delta|={worst:.8f} -- OK")


def test_handwritten_cdl_shader_matches_cpu_apply_cdl():
    """Same real-world check Stage 2 already did for OCIO's own generated
    shader (see gpu_shader_prototype.py) -- confirms the hand-written
    substitute doesn't just match OCIO's GPU codegen, it ALSO still tracks
    the actual CPU reference path this app runs today (cdl.apply_cdl)."""
    _app, context, surface = gsp._make_offscreen_gl_context()
    rng = np.random.default_rng(1)
    image = rng.uniform(-0.1, 1.1, size=(48, 48, 3)).astype(np.float32)

    worst = 0.0
    for style in ("no_clip", "asc"):
        for _ in range(4):
            params = _random_cdl_params(rng)
            cpu_result = cdl.apply_cdl(image, slope=params["slope"], offset=params["offset"],
                                       power=params["power"], sat=params["sat"], style=style)
            gpu_result = _handwritten_gpu_result(params, style, image)
            delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
            worst = max(worst, delta.max())
            # Same tolerance class as Stage 2's CPU-vs-OCIO-GPU numbers
            # (docs/archive/GPU_ACCELERATION_PLAN_2026-08-08.md) -- float32 GPU math vs.
            # this app's CPU path, not a bit-exact requirement.
            assert delta.max() < 1e-3, (
                f"style={style!r} params={params!r}: hand-written CDL shader "
                f"diverges from cdl.apply_cdl (CPU) by {delta.max()}.")

    context.doneCurrent()
    print(f"gpu_cdl_shader.CDL_SHADER_SOURCE: matches cdl.apply_cdl (CPU) "
          f"across 8 random (style, params) combinations, "
          f"worst max|delta|={worst:.8f} -- OK")


if __name__ == "__main__":
    test_handwritten_cdl_shader_matches_ocios_own_generated_shader()
    test_handwritten_cdl_shader_matches_cpu_apply_cdl()
    print()
    print("All gpu_cdl_shader tests passed.")
