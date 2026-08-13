"""Tests for gpu_aces_drt_shader.py -- run directly:
python3 test_gpu_aces_drt_shader.py

Regression coverage for Task #31 ("HDR output color spaces in ACES DRT
operator broken... very milky in viewer, scope seems correct") -- see
that module's own docstring for the real root cause: every HDR display/
view needs a GPU texture (a packed 1D shaper LUT) that gpu_stack_renderer.
py never bound before this fix, silently sampling an unbound texture and
producing severely compressed, nearly-uniform-gray output.

Requires a real (offscreen but genuine) OpenGL context -- excluded from
`pytest -q` via conftest.py's collect_ignore, same reason as every other
real-GL-context GPU test file. Run standalone: `python3
test_gpu_aces_drt_shader.py`.
"""
import numpy as np
import OpenGL.GL as gl

import aces_drt
import app_logic
import gpu_aces_drt_shader as gads
import gpu_shader_prototype as gsp


def _test_image():
    return app_logic.load_image_as_array(app_logic.default_sample_image_path(), max_dimension=200)


def test_sdr_display_needs_no_textures():
    _app, context, surface = gsp._make_offscreen_gl_context()
    cache = gads.AcesDrtGpuCache()
    fingerprint = (aces_drt.DEFAULT_CONFIG_NAME, "ACEScct", "sRGB - Display", "ACES 1.0 - SDR Video")
    entry = cache.get_entry(fingerprint, lambda: aces_drt._get_config(fingerprint[0]).getProcessor(
        aces_drt.ocio.DisplayViewTransform(src=fingerprint[1], display=fingerprint[2], view=fingerprint[3])))
    assert entry.gl_textures == [], "an SDR display/view should need zero GPU textures"
    cache.clear()
    context.doneCurrent()
    print("AcesDrtGpuCache: an SDR display/view (sRGB) needs zero GPU textures -- OK")


def test_hdr_display_needs_a_texture():
    _app, context, surface = gsp._make_offscreen_gl_context()
    cache = gads.AcesDrtGpuCache()
    fingerprint = (aces_drt.DEFAULT_CONFIG_NAME, "ACEScct", "ST2084-P3-D65 - Display",
                   "ACES 1.1 - HDR Video (1000 nits & P3 lim)")
    entry = cache.get_entry(fingerprint, lambda: aces_drt._get_config(fingerprint[0]).getProcessor(
        aces_drt.ocio.DisplayViewTransform(src=fingerprint[1], display=fingerprint[2], view=fingerprint[3])))
    assert len(entry.gl_textures) == 1, "this HDR display/view is known to need exactly one GPU texture"
    gl_texture, sampler_name = entry.gl_textures[0]
    assert gl.glIsTexture(gl_texture)
    assert sampler_name
    cache.clear()
    context.doneCurrent()
    print("AcesDrtGpuCache: an HDR display/view (ST2084-P3-D65) needs and uploads a real GPU texture -- OK")


def _render_via_gpu_stack_renderer(display, view, image):
    """Same clip-before-returning convention as test_gpu_stack_renderer.
    py's own _render_stack_on_gpu -- render_chain itself deliberately does
    NOT clip (it mirrors stack._run_stages' unclipped internal math
    exactly), and a real HDR ACES Output Transform legitimately produces
    values outside [0,1] for some pixels -- apply_stack_to_image_array's
    own final np.clip is the PUBLIC contract this needs to match for a
    fair CPU/GPU comparison."""
    import gpu_stack_renderer
    from stack import Stack, make_aces_drt_stage

    s = Stack()
    s.add_stage(make_aces_drt_stage("ACEScct", display, view))
    renderer = gpu_stack_renderer.GpuStackRenderer()
    tex_in, w, h = gsp.upload_rgba32f_texture(image)
    result_tex = renderer.render_chain(s, tex_in, w, h)

    fbo = gl.glGenFramebuffers(1)
    gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, fbo)
    gl.glFramebufferTexture2D(gl.GL_FRAMEBUFFER, gl.GL_COLOR_ATTACHMENT0, gl.GL_TEXTURE_2D, result_tex, 0)
    pixels = gl.glReadPixels(0, 0, w, h, gl.GL_RGBA, gl.GL_FLOAT)
    result = np.frombuffer(pixels, dtype=np.float32).reshape(h, w, 4)[..., :3].copy()

    gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0)
    gl.glDeleteFramebuffers(1, [fbo])
    renderer.clear_gl_resources()
    gl.glDeleteTextures([tex_in])
    return np.clip(result, 0.0, 1.0), s


def test_hdr_display_gpu_render_matches_cpu_not_milky():
    """The actual reported bug, end to end: render a real image through
    an HDR ACES Output Transform on the GPU (via the real
    gpu_stack_renderer.GpuStackRenderer path, not gpu_aces_drt_shader.py
    in isolation) and confirm it matches CPU -- NOT the severely
    compressed, nearly-uniform-gray output the missing texture bind used
    to produce (confirmed directly before this fix: GPU min/max/mean
    0.204/0.518/0.467 vs. CPU's correct 0.0/0.694/0.329 on this same
    image/display/view)."""
    _app, context, surface = gsp._make_offscreen_gl_context()
    from stack import apply_stack_to_image_array

    image = _test_image()
    display, view = "ST2084-P3-D65 - Display", "ACES 1.1 - HDR Video (1000 nits & P3 lim)"
    gpu_result, s = _render_via_gpu_stack_renderer(display, view, image)
    cpu_result = apply_stack_to_image_array(s, image)

    # A milky/compressed render has near-zero dynamic range -- assert the
    # GPU render's own range is close to the CPU one's, not just a raw
    # pixel-delta check, so a regression that happens to average out
    # would still be caught.
    assert gpu_result.max() - gpu_result.min() > 0.5 * (cpu_result.max() - cpu_result.min()), (
        f"GPU dynamic range collapsed relative to CPU: GPU {gpu_result.min():.3f}-{gpu_result.max():.3f}, "
        f"CPU {cpu_result.min():.3f}-{cpu_result.max():.3f}")

    delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
    context.doneCurrent()
    assert delta.max() < 1e-3, delta.max()
    print(f"GpuStackRenderer: HDR aces_drt (ST2084-P3-D65) stage matches CPU, "
          f"not milky/compressed, max|delta|={delta.max():.6f} -- OK")


def test_hlg_and_pq_hdr_displays_also_match_cpu():
    _app, context, surface = gsp._make_offscreen_gl_context()
    from stack import apply_stack_to_image_array

    image = _test_image()
    for display, view in [
        ("Rec.2100-PQ - Display", "ACES 1.1 - HDR Video (1000 nits & Rec.2020 lim)"),
        ("Rec.2100-HLG - Display", "ACES 1.1 - HDR Video (1000 nits & Rec.2020 lim)"),
    ]:
        gpu_result, s = _render_via_gpu_stack_renderer(display, view, image)
        cpu_result = apply_stack_to_image_array(s, image)
        delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
        assert delta.max() < 1e-3, (display, view, delta.max())
        print(f"GpuStackRenderer: HDR aces_drt ({display}) stage matches CPU, "
              f"max|delta|={delta.max():.6f} -- OK")
    context.doneCurrent()


def test_cache_reuses_the_same_entry_for_the_same_fingerprint():
    _app, context, surface = gsp._make_offscreen_gl_context()
    cache = gads.AcesDrtGpuCache()
    fingerprint = (aces_drt.DEFAULT_CONFIG_NAME, "ACEScct", "sRGB - Display", "ACES 1.0 - SDR Video")
    build = lambda: aces_drt._get_config(fingerprint[0]).getProcessor(  # noqa: E731
        aces_drt.ocio.DisplayViewTransform(src=fingerprint[1], display=fingerprint[2], view=fingerprint[3]))

    entry_a = cache.get_entry(fingerprint, build)
    entry_b = cache.get_entry(fingerprint, build)
    assert entry_a is entry_b, "the same fingerprint must return the SAME cache entry"
    cache.clear()
    context.doneCurrent()
    print("AcesDrtGpuCache: reuses the same entry for the same fingerprint -- OK")


def test_clear_deletes_programs_and_textures():
    _app, context, surface = gsp._make_offscreen_gl_context()
    cache = gads.AcesDrtGpuCache()
    fingerprint = (aces_drt.DEFAULT_CONFIG_NAME, "ACEScct", "ST2084-P3-D65 - Display",
                   "ACES 1.1 - HDR Video (1000 nits & P3 lim)")
    entry = cache.get_entry(fingerprint, lambda: aces_drt._get_config(fingerprint[0]).getProcessor(
        aces_drt.ocio.DisplayViewTransform(src=fingerprint[1], display=fingerprint[2], view=fingerprint[3])))
    assert gl.glIsProgram(entry.program)
    gl_texture = entry.gl_textures[0][0]
    assert gl.glIsTexture(gl_texture)
    cache.clear()
    assert not gl.glIsProgram(entry.program)
    assert not gl.glIsTexture(gl_texture)
    context.doneCurrent()
    print("AcesDrtGpuCache.clear(): deletes both the compiled program and the uploaded texture -- OK")


if __name__ == "__main__":
    test_sdr_display_needs_no_textures()
    test_hdr_display_needs_a_texture()
    test_hdr_display_gpu_render_matches_cpu_not_milky()
    test_hlg_and_pq_hdr_displays_also_match_cpu()
    test_cache_reuses_the_same_entry_for_the_same_fingerprint()
    test_clear_deletes_programs_and_textures()
    print()
    print("All gpu_aces_drt_shader tests passed.")
