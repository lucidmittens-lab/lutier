"""Tests for gpu_lut_shader.py -- run directly: python3 -m tests.test_gpu_lut_shader

Requires a real (offscreen but genuine) OpenGL context -- excluded from
`pytest -q` via conftest.py's collect_ignore, same reason as every other
real-GL-context GPU test file. Run standalone: `python3
-m tests.test_gpu_lut_shader`.
"""
import numpy as np
import colour
import OpenGL.GL as gl

import gpu_lut_shader
import gpu_shader_prototype as gsp


def _random_lut3d(size=9, seed=0, domain=None):
    rng = np.random.default_rng(seed)
    identity = colour.LUT3D(size=size).table
    perturb = rng.uniform(-0.15, 0.15, size=identity.shape)
    table = np.clip(identity + perturb, 0.0, 1.0).astype(np.float64)
    kwargs = {"table": table, "size": size}
    if domain is not None:
        kwargs["domain"] = domain
    return colour.LUT3D(**kwargs)


def test_is_lut3d_gpu_eligible():
    lut = _random_lut3d()
    assert gpu_lut_shader.is_lut3d_gpu_eligible(lut) is True

    lut1d = colour.LUT1D()
    assert gpu_lut_shader.is_lut3d_gpu_eligible(lut1d) is False, "LUT1D is not a LUT3D"

    # A non-default but still IMPLICIT (2-row [min, max]) domain -- the
    # LogC-range-cube case found live, see gpu_lut_shader.py's own
    # "DOMAIN SUPPORT" docstring -- IS eligible now.
    logc_range_domain = colour.LUT3D(
        size=5, domain=np.array([[0.0, 0.0, 0.0], [1.785714, 1.785714, 1.785714]]))
    assert gpu_lut_shader.is_lut3d_gpu_eligible(logc_range_domain) is True, (
        "a non-default but still IMPLICIT domain (e.g. a LogC-range cube) should be eligible")

    # An EXPLICIT (per-sample, >2 rows) domain is a genuinely different,
    # still-unsupported case -- see module docstring's SCOPE LIMIT.
    explicit_domain = np.tile(np.linspace(0, 1, 5)[:, None], (1, 3))
    explicit = colour.LUT3D(size=5, domain=explicit_domain)
    assert explicit.is_domain_explicit(), "test setup: this domain must actually be explicit"
    assert gpu_lut_shader.is_lut3d_gpu_eligible(explicit) is False, (
        "an explicit (per-sample) domain isn't handled by this GPU path")
    print("gpu_lut_shader.is_lut3d_gpu_eligible: accepts any implicit-domain "
          "LUT3D (default or not), rejects LUT1D and an explicit domain -- OK")


def test_gpu_render_matches_colour_lut3d_apply():
    _app, context, surface = gsp._make_offscreen_gl_context()
    lut = _random_lut3d(size=9, seed=3)
    cache = gpu_lut_shader.Lut3DGpuCache()
    entry = cache.get_entry(lut)

    rng = np.random.default_rng(4)
    test_image = rng.uniform(0, 1, size=(4, 8, 3)).astype(np.float32)

    def set_uniforms(program):
        loc = gl.glGetUniformLocation(program, entry.sampler_name)
        gl.glActiveTexture(gl.GL_TEXTURE1)
        gl.glBindTexture(gl.GL_TEXTURE_3D, entry.gl_texture)
        gl.glUniform1i(loc, 1)

    gpu_result = gsp.render_program_on_gpu(entry.program, test_image, set_uniforms=set_uniforms)
    cpu_result = lut.apply(test_image.astype(np.float64))

    delta = np.abs(gpu_result.astype(np.float64) - cpu_result)
    cache.clear()
    context.doneCurrent()
    assert delta.max() < 1e-3, delta.max()
    print(f"gpu_lut_shader: GPU-rendered LUT3D lookup matches colour.LUT3D.apply() "
          f"(the real CPU path), max|delta|={delta.max():.6f} -- OK")


def test_gpu_render_matches_colour_lut3d_apply_with_non_default_domain():
    """Regression guard for a real, reproduced bug: a LogC-range grading
    cube (domain [[0,0,0], [1.79,1.79,1.79]], not [0,1]) was found live
    falling back to the CPU path entirely -- see gpu_lut_shader.py's own
    "DOMAIN SUPPORT" docstring. Exercises input values ABOVE 1.0 (well
    within this domain's actual range), not just [0,1], since that's
    exactly the case a default-domain-only implementation would get wrong."""
    _app, context, surface = gsp._make_offscreen_gl_context()
    domain = np.array([[0.0, 0.0, 0.0], [1.785714, 1.785714, 1.785714]])
    lut = _random_lut3d(size=9, seed=3, domain=domain)
    cache = gpu_lut_shader.Lut3DGpuCache()
    entry = cache.get_entry(lut)

    rng = np.random.default_rng(4)
    test_image = rng.uniform(0, 1.7, size=(4, 8, 3)).astype(np.float32)

    def set_uniforms(program):
        loc = gl.glGetUniformLocation(program, entry.sampler_name)
        gl.glActiveTexture(gl.GL_TEXTURE1)
        gl.glBindTexture(gl.GL_TEXTURE_3D, entry.gl_texture)
        gl.glUniform1i(loc, 1)

    gpu_result = gsp.render_program_on_gpu(entry.program, test_image, set_uniforms=set_uniforms)
    cpu_result = lut.apply(test_image.astype(np.float64))

    delta = np.abs(gpu_result.astype(np.float64) - cpu_result)
    cache.clear()
    context.doneCurrent()
    assert delta.max() < 1e-3, delta.max()
    print(f"gpu_lut_shader: a LogC-range-domain LUT3D matches colour.LUT3D.apply() "
          f"on the GPU, max|delta|={delta.max():.6f} -- OK")


def test_cache_reuses_the_same_entry_for_the_same_lut_object():
    _app, context, surface = gsp._make_offscreen_gl_context()
    cache = gpu_lut_shader.Lut3DGpuCache()
    lut = _random_lut3d(size=5, seed=1)

    entry_a = cache.get_entry(lut)
    entry_b = cache.get_entry(lut)
    assert entry_a is entry_b, "the same LUT object must return the SAME cache entry"

    other_lut = _random_lut3d(size=5, seed=2)
    entry_c = cache.get_entry(other_lut)
    assert entry_c is not entry_a, "a DIFFERENT LUT object must get its own cache entry"
    assert entry_c.gl_texture != entry_a.gl_texture

    cache.clear()
    context.doneCurrent()
    print("Lut3DGpuCache: reuses the same entry for the same LUT object "
          "(by identity), a different LUT gets its own entry -- OK")


def test_clear_deletes_textures_and_programs():
    _app, context, surface = gsp._make_offscreen_gl_context()
    cache = gpu_lut_shader.Lut3DGpuCache()
    lut = _random_lut3d(size=5, seed=5)
    entry = cache.get_entry(lut)
    assert gl.glIsTexture(entry.gl_texture)
    cache.clear()
    assert not gl.glIsTexture(entry.gl_texture), "clear() should delete the uploaded 3D texture"
    context.doneCurrent()
    print("Lut3DGpuCache.clear(): deletes the uploaded 3D texture -- OK")


if __name__ == "__main__":
    test_is_lut3d_gpu_eligible()
    test_gpu_render_matches_colour_lut3d_apply()
    test_gpu_render_matches_colour_lut3d_apply_with_non_default_domain()
    test_cache_reuses_the_same_entry_for_the_same_lut_object()
    test_clear_deletes_textures_and_programs()
    print()
    print("All gpu_lut_shader tests passed.")
