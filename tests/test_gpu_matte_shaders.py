"""Tests for gpu_matte_shaders.py -- run directly:
python3 -m tests.test_gpu_matte_shaders

Requires a real (offscreen but genuine) OpenGL context -- excluded from
`pytest -q` via conftest.py's collect_ignore, same reason as every other
real-GL-context GPU test file.
"""
import numpy as np
import OpenGL.GL as gl
import gpu_shader_prototype as gsp
import gpu_matte_shaders as gms
import gpu_stack_renderer as gsr
import stack as st
import keyer
import matte_shape
import matte_combine
import app_logic

_ctx = None
_images = {}

def _context():
    global _ctx
    if _ctx is None:
        _ctx = gsp._make_offscreen_gl_context()
    return _ctx

def _image(max_dimension=160):
    if max_dimension not in _images:
        _images[max_dimension] = app_logic.load_image_as_array(
            app_logic.default_sample_image_path(), max_dimension=max_dimension).astype(np.float32)
    return _images[max_dimension]


def _gate(stage, source):
    stage.params["matte_source_id"] = source.id
    return stage


def _tag(stage, branch):
    stage.branch = branch
    return stage

def _read(fbo, w, h):
    gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, fbo)
    return np.frombuffer(gl.glReadPixels(0, 0, w, h, gl.GL_RGBA, gl.GL_FLOAT), np.float32).reshape(h, w, 4)[..., 0]

def _render_chain(renderer, stack, tex, w, h):
    out = renderer.render_chain(stack, tex, w, h)
    fbo = gl.glGenFramebuffers(1); gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, fbo)
    gl.glFramebufferTexture2D(gl.GL_FRAMEBUFFER, gl.GL_COLOR_ATTACHMENT0, gl.GL_TEXTURE_2D, out, 0)
    px = np.frombuffer(gl.glReadPixels(0, 0, w, h, gl.GL_RGBA, gl.GL_FLOAT), np.float32).reshape(h, w, 4)[..., :3]
    gl.glDeleteFramebuffers(1, [fbo]); return px

def test_shape_passes():
    ctx, _, _ = _context()
    img = _image()
    h, w = img.shape[:2]
    tex, _, _ = gsp.upload_rgba32f_texture(img)
    vbo = gsp.make_fullscreen_quad_vbo()
    mr = gms.MatteRenderer()
    tgt_tex, tgt_fbo = gsp.create_rgba32f_render_target(w, h)
    for params in ({"shape": "ellipse"}, {"shape": "rectangle", "rotation": 30, "width": 0.5, "height": 0.2, "softness": 0.7, "center_x": 0.3},
                   {"shape": "ellipse", "invert": True, "softness": 0.0}):
        s = st.make_matte_shape_stage(**params); s.opacity = 0.8
        mr.render_shape(s, tgt_fbo, w, h, vbo)
        gpu_result = _read(tgt_fbo, w, h)
        cpu_result = matte_shape.compute_shape_matte((h, w), s.params) * 0.8
        assert np.abs(gpu_result - cpu_result).max() < 1e-4, f"max|diff| = {np.abs(gpu_result - cpu_result).max():.2e}"
        print(f"shape {params} -- OK")

def test_combine_ops():
    ctx, _, _ = _context()
    img = _image()
    h, w = img.shape[:2]
    tex, _, _ = gsp.upload_rgba32f_texture(img)
    vbo = gsp.make_fullscreen_quad_vbo()
    mr = gms.MatteRenderer()
    _tgt_tex, tgt_fbo = gsp.create_rgba32f_render_target(w, h)
    ta, fa = gsp.create_rgba32f_render_target(w, h); tb, fb = gsp.create_rgba32f_render_target(w, h)
    a = st.make_matte_shape_stage(shape="ellipse"); b = st.make_matte_shape_stage(shape="rectangle", center_x=0.6)
    mr.render_shape(a, fa, w, h, vbo); mr.render_shape(b, fb, w, h, vbo)
    ma, mb = matte_shape.compute_shape_matte((h, w), a.params), matte_shape.compute_shape_matte((h, w), b.params)
    for op in matte_combine.OPS:
        c = st.make_matte_combine_stage(); c.params["op"] = op
        mr.render_combine(c, ta, tb, tgt_fbo, w, h, vbo)
        gpu_result = _read(tgt_fbo, w, h)
        cpu_result = matte_combine.compute_combined_matte(ma, mb, op)
        assert np.abs(gpu_result - cpu_result).max() < 1e-4, f"max|diff| = {np.abs(gpu_result - cpu_result).max():.2e}"
        print(f"combine {op} -- OK")
    c = st.make_matte_combine_stage(); c.params["op"] = "union"
    mr.render_combine(c, ta, None, tgt_fbo, w, h, vbo)
    gpu_result = _read(tgt_fbo, w, h)
    cpu_result = matte_combine.compute_combined_matte(ma, np.zeros_like(ma), "union")
    assert np.abs(gpu_result - cpu_result).max() < 1e-4, f"max|diff| = {np.abs(gpu_result - cpu_result).max():.2e}"
    print("combine union, B missing -- OK")

def test_keyer():
    ctx, _, _ = _context()
    img = _image()
    h, w = img.shape[:2]
    tex, _, _ = gsp.upload_rgba32f_texture(img)
    vbo = gsp.make_fullscreen_quad_vbo()
    mr = gms.MatteRenderer()
    tgt_tex, tgt_fbo = gsp.create_rgba32f_render_target(w, h)
    for extra in ({}, {"prefilter_blur": 2.0}, {"matte_blur": 3.0}, {"prefilter_blur": 1.5, "matte_blur": 2.5, "clip_black": 0.1, "clip_white": 0.8, "invert": True}):
        k = st.make_keyer_stage(); k.params.update({"hue_center": 40.0, "hue_width": 40.0, "chroma_max": 0.3, **extra})
        mr.render_keyer(k, tex, tgt_fbo, w, h, vbo)
        gpu_result = _read(tgt_fbo, w, h)
        cpu_result = keyer.compute_matte(img.astype(np.float64), k.params)
        assert np.abs(gpu_result - cpu_result).max() < 2e-3, f"max|diff| = {np.abs(gpu_result - cpu_result).max():.2e}"
        print(f"keyer {extra} -- OK")

def test_chain_cases():
    ctx, _, _ = _context()
    img = _image(max_dimension=200)
    h, w = img.shape[:2]
    renderer = gsr.GpuStackRenderer()
    tex, _, _ = gsp.upload_rgba32f_texture(img)
    cases = {}
    shape = st.make_matte_shape_stage(shape="ellipse", softness=0.5)
    cases["CDL gated by a shape"] = [shape, _gate(st.make_cdl_stage(slope=(1.4, 0.9, 0.7)), shape)]
    key = st.make_keyer_stage(); key.params.update(hue_center=40.0, hue_width=40.0, chroma_max=0.3, matte_blur=2.0)
    pl = _gate(st.make_printer_lights_stage(4, 0, -4, working_colourspace="ACEScct"), key); pl.opacity = 0.7
    cases["Printer Lights gated by a keyer"] = [key, pl]
    a, b = st.make_matte_shape_stage(shape="ellipse"), st.make_matte_shape_stage(shape="rectangle", center_x=0.65)
    comb = st.make_matte_combine_stage(); comb.params.update(matte_a_source_id=a.id, matte_b_source_id=b.id, op="subtract")
    cases["combine(a - b) gating a CDL"] = [a, b, comb, _gate(st.make_cdl_stage(offset=(0.1, 0, -0.1)), comb)]
    k2 = st.make_keyer_stage(); k2.params.update(hue_center=200.0, hue_width=60.0)
    merge = st.make_fork_merge_stage(blend=0.5)
    cases["fork: keyer in A gating A, gated merge"] = [
        st.make_cdl_stage(sat=1.2), st.make_fork_split_stage(), _tag(k2, "a"),
        _tag(_gate(st.make_cdl_stage(slope=(0.6, 1.0, 1.3)), k2), "a"),
        _tag(st.make_printer_lights_stage(0, 3, 0, working_colourspace="ACEScct"), "b"), _gate(merge, shape), shape]
    off = st.make_matte_shape_stage(); off.enabled = False
    cases["gate on a DISABLED shape -> plain opacity"] = [off, _gate(st.make_cdl_stage(slope=(1.5, 1, 1)), off)]
    for name, stages in cases.items():
        stack = st.Stack(stages=stages)
        assert gsr.is_stack_gpu_eligible(stack), f"{name} NOT ELIGIBLE"
        gpu_result = _render_chain(renderer, stack, tex, w, h)
        cpu_result = st.apply_stack_to_image_array(stack, img.astype(np.float64))
        assert np.abs(np.clip(gpu_result, 0, 1) - cpu_result).max() < 1e-3, f"max|diff| = {np.abs(np.clip(gpu_result, 0, 1) - cpu_result).max():.2e}"
        print(f"{name} -- OK")

def test_cross_branch_matte_in_list_order_stays_on_cpu():
    """The GPU renders branch A before branch B; the CPU walks the list.
    A matte made in B ahead of an A stage that uses it would differ, so
    that layout stays on the CPU. (A consumer listed BEFORE its producer
    sees no matte on either path, so it's fine on the GPU.)"""
    _context()
    producer_b = _tag(st.make_matte_shape_stage(), "b")
    risky = st.Stack(stages=[st.make_fork_split_stage(), producer_b,
                             _tag(_gate(st.make_cdl_stage(), producer_b), "a"), st.make_fork_merge_stage()])
    assert not gsr.is_stack_gpu_eligible(risky)
    producer_b = _tag(st.make_matte_shape_stage(), "b")
    harmless = st.Stack(stages=[st.make_fork_split_stage(), _tag(_gate(st.make_cdl_stage(), producer_b), "a"),
                                producer_b, st.make_fork_merge_stage()])
    assert gsr.is_stack_gpu_eligible(harmless)
    print("is_stack_gpu_eligible: a cross-branch matte the GPU would order "
          "differently stays on the CPU -- OK")


def test_keyer_blur_past_the_ui_range_stays_on_cpu():
    keyer_stage = st.make_keyer_stage()
    keyer_stage.params["prefilter_blur"] = 30.0  # radius 120 > MAX_BLUR_RADIUS
    assert not gsr.is_stack_gpu_eligible(st.Stack(stages=[keyer_stage]))
    keyer_stage.params["prefilter_blur"] = 25.0  # the UI max still fits
    assert gsr.is_stack_gpu_eligible(st.Stack(stages=[keyer_stage]))
    print("is_stack_gpu_eligible: keyer blur beyond MAX_BLUR_RADIUS stays on "
          "the CPU, the UI's max fits -- OK")


if __name__ == "__main__":
    test_shape_passes()
    test_combine_ops()
    test_keyer()
    test_chain_cases()
    test_cross_branch_matte_in_list_order_stays_on_cpu()
    test_keyer_blur_past_the_ui_range_stays_on_cpu()
    print()
    print("All gpu_matte_shaders tests passed.")
