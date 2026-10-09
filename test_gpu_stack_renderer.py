"""Tests for gpu_stack_renderer.py -- run directly:
python3 test_gpu_stack_renderer.py

The real question this answers: does the GPU chain (per-stage compiled
programs, ping-ponged in stack order) produce the same result as
stack.apply_stack_to_image_array (CPU) for a REAL multi-stage stack --
not just one stage kind in isolation (already covered by
test_gpu_cdl_shader.py/test_gpu_shader_cache.py), but the actual fold
this app runs: opacity blending, a disabled stage being skipped, and
kinds chained together in stack order.

Requires a real (offscreen but genuine) OpenGL context -- excluded from
`pytest -q` via conftest.py's collect_ignore for the same real,
reproduced GL-context-interaction reason documented there. Run
standalone: `python3 test_gpu_stack_renderer.py`.
"""
import numpy as np
import colour

import aces_drt
import app_logic
import gpu_shader_prototype as gsp
import gpu_stack_renderer
from stack import (Stack, apply_stack_to_image_array, make_aces_drt_stage, make_cdl_stage,
                    make_color_warper_stage, make_custom_drt_stage, make_exposure_contrast_stage,
                    make_fork_merge_stage, make_fork_split_stage, make_grading_primary_stage,
                    make_grading_rgb_curve_stage, make_grading_tone_stage, make_lut_stage,
                    make_printer_lights_stage)


def _random_lut3d(size=9, seed=0):
    rng = np.random.default_rng(seed)
    identity = colour.LUT3D(size=size).table
    perturb = rng.uniform(-0.15, 0.15, size=identity.shape)
    table = np.clip(identity + perturb, 0.0, 1.0).astype(np.float64)
    return colour.LUT3D(table=table, size=size)


def _render_stack_on_gpu(stack, image_array):
    """Uploads `image_array`, runs it through gpu_stack_renderer's chain,
    reads back the result -- the GPU-side equivalent of
    apply_stack_to_image_array, built directly on this module's own
    primitives (not a hidden helper this module doesn't already expose),
    since real integration code (sample_image_panel.py) will do the same
    upload/render/readback sequence itself.

    Clips to [0,1] before returning, matching apply_stack_to_image_array's
    own final np.clip -- render_chain itself deliberately does NOT clip
    (it mirrors stack._run_stages' unclipped internal math exactly, the
    same "don't silently discard legitimate range" rule
    aces_drt.apply_aces_output_transform's own docstring states), so a
    real ACES DRT stage output can and does legitimately land outside
    [0,1] for some pixels -- this function's job is to match the PUBLIC
    apply_stack_to_image_array contract for a fair CPU/GPU comparison,
    not to test render_chain's raw (intentionally unclipped) output."""
    renderer = gpu_stack_renderer.GpuStackRenderer()
    tex_in, w, h = gsp.upload_rgba32f_texture(image_array)
    result_tex = renderer.render_chain(stack, tex_in, w, h)

    import OpenGL.GL as gl
    fbo = gl.glGenFramebuffers(1)
    gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, fbo)
    gl.glFramebufferTexture2D(gl.GL_FRAMEBUFFER, gl.GL_COLOR_ATTACHMENT0,
                               gl.GL_TEXTURE_2D, result_tex, 0)
    pixels = gl.glReadPixels(0, 0, w, h, gl.GL_RGBA, gl.GL_FLOAT)
    result = np.frombuffer(pixels, dtype=np.float32).reshape(h, w, 4)[..., :3].copy()

    gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0)
    gl.glDeleteFramebuffers(1, [fbo])
    renderer.clear_gl_resources()
    gl.glDeleteTextures([tex_in])
    return np.clip(result, 0.0, 1.0)


def _test_image():
    return app_logic.load_image_as_array(app_logic.default_sample_image_path(), max_dimension=200)


def test_is_stack_gpu_eligible():
    eligible = Stack()
    eligible.add_stage(make_cdl_stage(slope=(1.1, 1.0, 0.9)))
    assert gpu_stack_renderer.is_stack_gpu_eligible(eligible) is True

    grading_primary_eligible = Stack()
    grading_primary_eligible.add_stage(make_grading_primary_stage())
    assert gpu_stack_renderer.is_stack_gpu_eligible(grading_primary_eligible) is True

    exposure_contrast_eligible = Stack()
    exposure_contrast_eligible.add_stage(make_exposure_contrast_stage())
    assert gpu_stack_renderer.is_stack_gpu_eligible(exposure_contrast_eligible) is True

    grading_tone_eligible = Stack()
    grading_tone_eligible.add_stage(make_grading_tone_stage())
    assert gpu_stack_renderer.is_stack_gpu_eligible(grading_tone_eligible) is True

    grading_rgb_curve_eligible = Stack()
    grading_rgb_curve_eligible.add_stage(make_grading_rgb_curve_stage())
    assert gpu_stack_renderer.is_stack_gpu_eligible(grading_rgb_curve_eligible) is True

    lut3d_eligible = Stack()
    lut3d_eligible.add_stage(make_lut_stage(_random_lut3d()))
    assert gpu_stack_renderer.is_stack_gpu_eligible(lut3d_eligible) is True, (
        "a plain, default-domain LUT3D stage should be GPU-eligible")

    lut_with_retarget = Stack()
    lut_with_retarget.add_stage(make_lut_stage(
        _random_lut3d(), retarget_from="ACEScct", retarget_to="ACES2065-1"))
    assert gpu_stack_renderer.is_stack_gpu_eligible(lut_with_retarget) is False, (
        "a retarget-wrapped LUT stage isn't GPU-eligible yet, see gpu_lut_shader.py's SCOPE LIMIT")

    printer_lights_eligible = Stack()
    printer_lights_eligible.add_stage(make_printer_lights_stage(1.0, 0.0, 0.0))
    assert gpu_stack_renderer.is_stack_gpu_eligible(printer_lights_eligible) is True, (
        "printer_lights with a real working_colourspace should be GPU-eligible")

    printer_lights_bogus_space = Stack()
    printer_lights_bogus_space.add_stage(
        make_printer_lights_stage(1.0, 0.0, 0.0, working_colourspace="not a real colourspace"))
    assert gpu_stack_renderer.is_stack_gpu_eligible(printer_lights_bogus_space) is False, (
        "an unresolvable working_colourspace falls the whole stack back to CPU, not a crash")

    disabled_bogus_printer_lights_ok = Stack()
    disabled_bogus_printer_lights_ok.add_stage(
        make_printer_lights_stage(1.0, 0.0, 0.0, working_colourspace="not a real colourspace"))
    disabled_bogus_printer_lights_ok.stages[0].enabled = False
    disabled_bogus_printer_lights_ok.add_stage(make_cdl_stage())
    assert gpu_stack_renderer.is_stack_gpu_eligible(disabled_bogus_printer_lights_ok) is True, (
        "a DISABLED, otherwise-ineligible stage should not block GPU eligibility")

    custom_drt_eligible = Stack()
    custom_drt_eligible.add_stage(make_custom_drt_stage("ACEScct"))
    assert gpu_stack_renderer.is_stack_gpu_eligible(custom_drt_eligible) is True, (
        "custom_drt with a real working_colourspace should be GPU-eligible")

    custom_drt_bogus_space = Stack()
    custom_drt_bogus_space.add_stage(make_custom_drt_stage("not a real colourspace"))
    assert gpu_stack_renderer.is_stack_gpu_eligible(custom_drt_bogus_space) is False, (
        "an unresolvable working_colourspace falls the whole stack back to CPU, not a crash")

    color_warper_eligible = Stack()
    color_warper_eligible.add_stage(make_color_warper_stage("ACEScct"))
    assert gpu_stack_renderer.is_stack_gpu_eligible(color_warper_eligible) is True, (
        "color_warper with a real working_colourspace should be GPU-eligible")

    color_warper_bogus_space = Stack()
    color_warper_bogus_space.add_stage(make_color_warper_stage("not a real colourspace"))
    assert gpu_stack_renderer.is_stack_gpu_eligible(color_warper_bogus_space) is False, (
        "an unresolvable working_colourspace falls the whole stack back to CPU, not a crash")

    print("gpu_stack_renderer.is_stack_gpu_eligible: correctly gates on "
          "enabled unsupported/unresolvable stage kinds, ignores disabled ones -- OK")


def test_is_stack_gpu_eligible_false_for_a_color_warper_stage_over_max_points():
    """Regression guard for a real, previously-shipped bug (found in code
    review): a color_warper stage with more points than gpu_color_
    warper_shader.MAX_POINTS used to still be judged GPU-eligible (only
    working_colourspace was ever checked), so it rendered on the GPU with
    the extras silently dropped -- disagreeing with the CPU path (used
    for export/bake), which honors every point. Must fall back to CPU
    entirely instead."""
    import color_warper as cw
    too_many_points = [{"h0": float(i), "c0": 0.1, "h": float(i), "c": 0.1}
                        for i in range(cw.MAX_POINTS + 1)]
    stack = Stack()
    stack.add_stage(make_color_warper_stage("ACEScct", points=too_many_points))
    assert gpu_stack_renderer.is_stack_gpu_eligible(stack) is False, (
        "a color_warper stage with more points than MAX_POINTS must fall back to CPU")

    at_the_limit = Stack()
    at_the_limit.add_stage(make_color_warper_stage(
        "ACEScct", points=too_many_points[:cw.MAX_POINTS]))
    assert gpu_stack_renderer.is_stack_gpu_eligible(at_the_limit) is True, (
        "exactly MAX_POINTS must still be GPU-eligible"
    )
    print("gpu_stack_renderer.is_stack_gpu_eligible: a color_warper stage "
          "with more points than MAX_POINTS falls back to CPU rather than "
          "silently rendering with the extras dropped -- OK")


def _fork_stack(branch_a_kinds=("cdl",), branch_b_kinds=("cdl",), split_enabled=True, merge_enabled=True):
    """A minimal Stack with one fork region -- `branch_a_kinds`/
    `branch_b_kinds` each a sequence of "cdl"/"grading_primary"/etc.
    make_*_stage names (without the make_/_stage wrapping) tagged onto
    that branch, for building varied test fixtures without repeating the
    same split/merge boilerplate everywhere below."""
    kind_to_maker = {
        "cdl": make_cdl_stage,
        "grading_primary": make_grading_primary_stage,
        "exposure_contrast": make_exposure_contrast_stage,
    }
    s = Stack()
    split = make_fork_split_stage(enabled=split_enabled)
    s.add_stage(split)
    for kind in branch_a_kinds:
        stage = kind_to_maker[kind]()
        stage.branch = "a"
        s.add_stage(stage)
    for kind in branch_b_kinds:
        stage = kind_to_maker[kind]()
        stage.branch = "b"
        s.add_stage(stage)
    s.add_stage(make_fork_merge_stage(blend=0.5, enabled=merge_enabled))
    return s


def test_is_stack_gpu_eligible_with_forks():
    eligible = _fork_stack()
    assert gpu_stack_renderer.is_stack_gpu_eligible(eligible) is True, (
        "a well-formed fork with GPU-eligible stages on both branches should be eligible")

    ineligible_kind_in_branch = _fork_stack(branch_a_kinds=("cdl",), branch_b_kinds=())
    bogus_printer_lights = make_printer_lights_stage(1.0, 0.0, 0.0, working_colourspace="not a real colourspace")
    bogus_printer_lights.branch = "b"
    ineligible_kind_in_branch.stages.insert(2, bogus_printer_lights)  # after split+branch-A's cdl, before merge
    assert gpu_stack_renderer.is_stack_gpu_eligible(ineligible_kind_in_branch) is False, (
        "a GPU-ineligible stage INSIDE a branch falls the whole stack back to CPU, same as one outside a fork")

    nested = _fork_stack()
    inner_split_index = 2  # right after the outer split, before its own interior/merge
    nested.stages.insert(inner_split_index, make_fork_split_stage())
    assert gpu_stack_renderer.is_stack_gpu_eligible(nested) is False, (
        "a nested fork_split isn't supported -- same 'one open region at a time' rule _run_stages enforces")

    orphaned = _fork_stack(branch_a_kinds=("cdl",), branch_b_kinds=())
    orphaned.stages[1].branch = None  # positionally inside the region, but no real branch tag
    assert gpu_stack_renderer.is_stack_gpu_eligible(orphaned) is False, (
        "an interior stage with no real branch tag is already an invalid stack -- falls back to CPU "
        "so the existing bake-error surfacing catches it, same as _run_stages raising on it directly")

    unmatched_split = Stack()
    unmatched_split.add_stage(make_fork_split_stage())
    assert gpu_stack_renderer.is_stack_gpu_eligible(unmatched_split) is False

    unmatched_merge = Stack()
    unmatched_merge.add_stage(make_fork_merge_stage())
    assert gpu_stack_renderer.is_stack_gpu_eligible(unmatched_merge) is False

    disabled_split_ignores_interior_kind = _fork_stack(split_enabled=False)
    disabled_split_ignores_interior_kind.stages[1].branch = None  # would be "orphaned" if the region were live
    assert gpu_stack_renderer.is_stack_gpu_eligible(disabled_split_ignores_interior_kind) is True, (
        "a DISABLED fork_split's interior is skipped entirely for eligibility -- kind/branch-tag "
        "validity genuinely doesn't matter there, matching _run_stages' own 'whole region is a no-op'")
    print("gpu_stack_renderer.is_stack_gpu_eligible: correctly allows a "
          "well-formed fork, rejects nesting/orphaned-interior/unmatched "
          "markers, and skips a DISABLED split's interior entirely -- OK")


def test_single_cdl_stage_matches_cpu():
    _app, context, surface = gsp._make_offscreen_gl_context()
    image = _test_image()
    s = Stack()
    s.add_stage(make_cdl_stage(slope=(1.1, 0.95, 1.0), offset=(0.02, 0.0, -0.01), sat=1.2))

    cpu_result = apply_stack_to_image_array(s, image)
    gpu_result = _render_stack_on_gpu(s, image)
    delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
    assert delta.max() < 1e-3, delta.max()
    context.doneCurrent()
    print(f"GpuStackRenderer: single CDL stage matches CPU, "
          f"max|delta|={delta.max():.6f} -- OK")


def test_cdl_then_aces_drt_chain_matches_cpu():
    """The exact scenario Tom reported feeling slow: a CDL stage feeding
    into an ACES DRT stage -- confirms the GPU chain's ping-pong ordering
    (CDL's output becomes ACES DRT's input) matches _run_stages' fold."""
    _app, context, surface = gsp._make_offscreen_gl_context()
    image = _test_image()
    display, view = aces_drt.get_default_display_and_view(aces_drt.DEFAULT_CONFIG_NAME)
    s = Stack()
    s.add_stage(make_cdl_stage(slope=(1.08, 0.97, 1.0), offset=(0.01, 0.0, -0.005)))
    s.add_stage(make_aces_drt_stage("ACEScct", display, view))

    cpu_result = apply_stack_to_image_array(s, image)
    gpu_result = _render_stack_on_gpu(s, image)
    delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
    assert delta.max() < 1e-3, delta.max()
    context.doneCurrent()
    print(f"GpuStackRenderer: CDL -> ACES DRT chain matches CPU, "
          f"max|delta|={delta.max():.6f} -- OK")


def test_single_grading_primary_stage_matches_cpu():
    _app, context, surface = gsp._make_offscreen_gl_context()
    image = _test_image()
    s = Stack()
    s.add_stage(make_grading_primary_stage(
        brightness=(0.02, -0.01, 0.03, 0.0), contrast=(1.1, 1.05, 0.95, 1.0), saturation=1.1))

    cpu_result = apply_stack_to_image_array(s, image)
    gpu_result = _render_stack_on_gpu(s, image)
    delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
    assert delta.max() < 1e-3, delta.max()
    context.doneCurrent()
    print(f"GpuStackRenderer: single grading_primary stage matches CPU, "
          f"max|delta|={delta.max():.6f} -- OK")


def test_grading_primary_then_aces_drt_chain_matches_cpu():
    """Mirrors test_cdl_then_aces_drt_chain_matches_cpu -- confirms
    grading_primary's dynamic-uniform GPU program (a different caching
    mechanism than cdl/aces_drt, see gpu_ocio_dynamic.py) still chains
    correctly through the same ping-pong render_chain."""
    _app, context, surface = gsp._make_offscreen_gl_context()
    image = _test_image()
    display, view = aces_drt.get_default_display_and_view(aces_drt.DEFAULT_CONFIG_NAME)
    s = Stack()
    s.add_stage(make_grading_primary_stage(contrast=(1.08, 0.97, 1.0, 1.0)))
    s.add_stage(make_aces_drt_stage("ACEScct", display, view))

    cpu_result = apply_stack_to_image_array(s, image)
    gpu_result = _render_stack_on_gpu(s, image)
    delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
    assert delta.max() < 1e-3, delta.max()
    context.doneCurrent()
    print(f"GpuStackRenderer: grading_primary -> ACES DRT chain matches CPU, "
          f"max|delta|={delta.max():.6f} -- OK")


def test_single_exposure_contrast_stage_matches_cpu():
    """The one kind with THREE independent dynamic properties per
    transform (see gpu_ocio_dynamic.py's own docstring) -- confirms that
    shape chains correctly through render_chain too, not just in
    isolation (already covered by test_gpu_ocio_dynamic.py)."""
    _app, context, surface = gsp._make_offscreen_gl_context()
    image = _test_image()
    s = Stack()
    s.add_stage(make_exposure_contrast_stage(exposure=0.4, contrast=1.15, gamma=1.05))

    cpu_result = apply_stack_to_image_array(s, image)
    gpu_result = _render_stack_on_gpu(s, image)
    delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
    assert delta.max() < 1e-3, delta.max()
    context.doneCurrent()
    print(f"GpuStackRenderer: single exposure_contrast stage matches CPU, "
          f"max|delta|={delta.max():.6f} -- OK")


def test_grading_primary_then_exposure_contrast_chain_matches_cpu():
    """Two DIFFERENT dynamic-uniform kinds chained together -- confirms
    each stage's own OcioDynamicGpuStageCache entry (and its own
    DynamicProperty handle(s)) stays correctly isolated per stage, not
    accidentally shared/clobbered across stages of different kinds."""
    _app, context, surface = gsp._make_offscreen_gl_context()
    image = _test_image()
    s = Stack()
    s.add_stage(make_grading_primary_stage(contrast=(1.05, 1.0, 0.97, 1.0)))
    s.add_stage(make_exposure_contrast_stage(exposure=0.2))

    cpu_result = apply_stack_to_image_array(s, image)
    gpu_result = _render_stack_on_gpu(s, image)
    delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
    assert delta.max() < 1e-3, delta.max()
    context.doneCurrent()
    print(f"GpuStackRenderer: grading_primary -> exposure_contrast chain matches CPU, "
          f"max|delta|={delta.max():.6f} -- OK")


def test_single_lut_stage_matches_cpu():
    _app, context, surface = gsp._make_offscreen_gl_context()
    image = _test_image()
    s = Stack()
    s.add_stage(make_lut_stage(_random_lut3d(size=9, seed=7)))

    cpu_result = apply_stack_to_image_array(s, image)
    gpu_result = _render_stack_on_gpu(s, image)
    delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
    assert delta.max() < 1e-3, delta.max()
    context.doneCurrent()
    print(f"GpuStackRenderer: single lut stage matches CPU, "
          f"max|delta|={delta.max():.6f} -- OK")


def test_lut_then_cdl_chain_matches_cpu():
    """The scenario Stage 4 was actually motivated by: a cube stage
    feeding into another GPU-eligible kind should keep the WHOLE chain
    on the GPU now, not fall back to CPU for everything just because a
    'lut' stage is present."""
    _app, context, surface = gsp._make_offscreen_gl_context()
    image = _test_image()
    s = Stack()
    s.add_stage(make_lut_stage(_random_lut3d(size=9, seed=8)))
    s.add_stage(make_cdl_stage(slope=(1.05, 0.98, 1.0), offset=(0.01, 0.0, -0.005)))

    cpu_result = apply_stack_to_image_array(s, image)
    gpu_result = _render_stack_on_gpu(s, image)
    delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
    assert delta.max() < 1e-3, delta.max()
    context.doneCurrent()
    print(f"GpuStackRenderer: lut -> CDL chain matches CPU, "
          f"max|delta|={delta.max():.6f} -- OK")


def test_single_grading_tone_stage_matches_cpu():
    _app, context, surface = gsp._make_offscreen_gl_context()
    image = _test_image()
    s = Stack()
    s.add_stage(make_grading_tone_stage(midtones=(1.05, 0.98, 1.02, 1.0, 0.4, 0.6)))

    cpu_result = apply_stack_to_image_array(s, image)
    gpu_result = _render_stack_on_gpu(s, image)
    delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
    assert delta.max() < 1e-3, delta.max()
    context.doneCurrent()
    print(f"GpuStackRenderer: single grading_tone stage matches CPU, "
          f"max|delta|={delta.max():.6f} -- OK")


def test_grading_tone_then_aces_drt_chain_matches_cpu():
    _app, context, surface = gsp._make_offscreen_gl_context()
    image = _test_image()
    display, view = aces_drt.get_default_display_and_view(aces_drt.DEFAULT_CONFIG_NAME)
    s = Stack()
    s.add_stage(make_grading_tone_stage(highlights=(1.1, 1.0, 0.95, 1.0, 0.3, 1.0)))
    s.add_stage(make_aces_drt_stage("ACEScct", display, view))

    cpu_result = apply_stack_to_image_array(s, image)
    gpu_result = _render_stack_on_gpu(s, image)
    delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
    assert delta.max() < 1e-3, delta.max()
    context.doneCurrent()
    print(f"GpuStackRenderer: grading_tone -> ACES DRT chain matches CPU, "
          f"max|delta|={delta.max():.6f} -- OK")


def test_single_grading_rgb_curve_stage_matches_cpu():
    _app, context, surface = gsp._make_offscreen_gl_context()
    image = _test_image()
    s = Stack()
    s.add_stage(make_grading_rgb_curve_stage(master=((0.0, 0.0), (0.5, 0.62), (1.0, 1.0))))

    cpu_result = apply_stack_to_image_array(s, image)
    gpu_result = _render_stack_on_gpu(s, image)
    delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
    assert delta.max() < 1e-3, delta.max()
    context.doneCurrent()
    print(f"GpuStackRenderer: single grading_rgb_curve stage matches CPU, "
          f"max|delta|={delta.max():.6f} -- OK")


def test_grading_rgb_curve_then_aces_drt_chain_matches_cpu():
    _app, context, surface = gsp._make_offscreen_gl_context()
    image = _test_image()
    display, view = aces_drt.get_default_display_and_view(aces_drt.DEFAULT_CONFIG_NAME)
    s = Stack()
    s.add_stage(make_grading_rgb_curve_stage(red=((0.0, 0.0), (0.3, 0.22), (1.0, 1.0))))
    s.add_stage(make_aces_drt_stage("ACEScct", display, view))

    cpu_result = apply_stack_to_image_array(s, image)
    gpu_result = _render_stack_on_gpu(s, image)
    delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
    assert delta.max() < 1e-3, delta.max()
    context.doneCurrent()
    print(f"GpuStackRenderer: grading_rgb_curve -> ACES DRT chain matches CPU, "
          f"max|delta|={delta.max():.6f} -- OK")


def test_single_printer_lights_stage_matches_cpu():
    _app, context, surface = gsp._make_offscreen_gl_context()
    image = _test_image()
    s = Stack()
    s.add_stage(make_printer_lights_stage(6.0, -4.0, 2.5, working_colourspace="ACEScct"))

    cpu_result = apply_stack_to_image_array(s, image)
    gpu_result = _render_stack_on_gpu(s, image)
    delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
    assert delta.max() < 1e-3, delta.max()
    context.doneCurrent()
    print(f"GpuStackRenderer: single printer_lights stage matches CPU, "
          f"max|delta|={delta.max():.6f} -- OK")


def test_single_custom_drt_stage_matches_cpu():
    _app, context, surface = gsp._make_offscreen_gl_context()
    image = _test_image()
    s = Stack()
    s.add_stage(make_custom_drt_stage("ACEScct"))

    cpu_result = apply_stack_to_image_array(s, image)
    gpu_result = _render_stack_on_gpu(s, image)
    delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
    assert delta.max() < 1e-3, delta.max()
    context.doneCurrent()
    print(f"GpuStackRenderer: single custom_drt stage matches CPU, "
          f"max|delta|={delta.max():.6f} -- OK")


def test_custom_drt_with_tuned_values_matches_cpu():
    """Tom's own tuned values (2026-08-13), not just the shipped
    defaults -- a second stage kind chained after it (CDL) too, same
    'not just one kind in isolation' bar every other chain test here
    meets."""
    _app, context, surface = gsp._make_offscreen_gl_context()
    image = _test_image()
    s = Stack()
    s.add_stage(make_cdl_stage(slope=(1.05, 1.0, 0.97)))
    s.add_stage(make_custom_drt_stage(
        "ARRI LogC4", contrast=0.96, headroom=0.6, blend_percentile=0.95,
        tail_width=0.5, desat_start=0.87, desat_end=1.0, identity_zone=0.8,
        transition_softness=0.3, fullness_r=0.0, fullness_g=0.0, fullness_b=0.0))

    cpu_result = apply_stack_to_image_array(s, image)
    gpu_result = _render_stack_on_gpu(s, image)
    delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
    assert delta.max() < 1e-3, delta.max()
    context.doneCurrent()
    print(f"GpuStackRenderer: CDL -> custom_drt chain (Tom's tuned values) matches CPU, "
          f"max|delta|={delta.max():.6f} -- OK")


def test_printer_lights_then_aces_drt_chain_matches_cpu():
    _app, context, surface = gsp._make_offscreen_gl_context()
    image = _test_image()
    display, view = aces_drt.get_default_display_and_view(aces_drt.DEFAULT_CONFIG_NAME)
    s = Stack()
    s.add_stage(make_printer_lights_stage(3.0, 0.0, -5.0, working_colourspace="ACEScct"))
    s.add_stage(make_aces_drt_stage("ACEScct", display, view))

    cpu_result = apply_stack_to_image_array(s, image)
    gpu_result = _render_stack_on_gpu(s, image)
    delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
    assert delta.max() < 1e-3, delta.max()
    context.doneCurrent()
    print(f"GpuStackRenderer: printer_lights -> ACES DRT chain matches CPU, "
          f"max|delta|={delta.max():.6f} -- OK")


def test_single_color_warper_stage_matches_cpu():
    _app, context, surface = gsp._make_offscreen_gl_context()
    image = _test_image()
    points = [{"h0": 35.0, "c0": 0.15, "h": 55.0, "c": 0.22},
              {"h0": 250.0, "c0": 0.1, "h": 250.0, "c": 0.02}]
    s = Stack()
    s.add_stage(make_color_warper_stage(
        "ACEScct", points=points, wake=40.0, dropoff=0.3))

    cpu_result = apply_stack_to_image_array(s, image)
    gpu_result = _render_stack_on_gpu(s, image)
    delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
    assert delta.max() < 1e-2, delta.max()
    context.doneCurrent()
    print(f"GpuStackRenderer: single color_warper stage matches CPU, "
          f"max|delta|={delta.max():.6f} -- OK")


def test_color_warper_then_aces_drt_chain_matches_cpu():
    _app, context, surface = gsp._make_offscreen_gl_context()
    image = _test_image()
    display, view = aces_drt.get_default_display_and_view(aces_drt.DEFAULT_CONFIG_NAME)
    points = [{"h0": 142.5, "c0": 0.2, "h": 120.0, "c": 0.3}]
    s = Stack()
    s.add_stage(make_color_warper_stage(
        "ACEScct", points=points, wake=25.0, dropoff=0.6))
    s.add_stage(make_aces_drt_stage("ACEScct", display, view))

    cpu_result = apply_stack_to_image_array(s, image)
    gpu_result = _render_stack_on_gpu(s, image)
    delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
    assert delta.max() < 1e-2, delta.max()
    context.doneCurrent()
    print(f"GpuStackRenderer: color_warper -> ACES DRT chain matches CPU, "
          f"max|delta|={delta.max():.6f} -- OK")


def test_stage_opacity_blend_matches_cpu():
    _app, context, surface = gsp._make_offscreen_gl_context()
    image = _test_image()
    s = Stack()
    s.add_stage(make_cdl_stage(slope=(1.3, 0.8, 1.0), offset=(0.05, 0.0, -0.03), opacity=0.4))

    cpu_result = apply_stack_to_image_array(s, image)
    gpu_result = _render_stack_on_gpu(s, image)
    delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
    assert delta.max() < 1e-3, delta.max()
    # Also confirm this ACTUALLY exercised the blend, not a no-op --
    # opacity=0.4 must differ meaningfully from the un-blended (opacity=1)
    # result, otherwise the assertion above would pass vacuously.
    full_strength = apply_stack_to_image_array(
        Stack(stages=[make_cdl_stage(slope=(1.3, 0.8, 1.0), offset=(0.05, 0.0, -0.03))]), image)
    assert np.abs(cpu_result - full_strength).max() > 0.01
    context.doneCurrent()
    print(f"GpuStackRenderer: per-stage opacity blend matches CPU, "
          f"max|delta|={delta.max():.6f} -- OK")


def test_disabled_stage_is_skipped():
    _app, context, surface = gsp._make_offscreen_gl_context()
    image = _test_image()
    s = Stack()
    s.add_stage(make_cdl_stage(slope=(2.0, 2.0, 2.0)))  # would be very visible if it ran
    s.stages[0].enabled = False
    s.add_stage(make_cdl_stage(slope=(1.05, 1.0, 0.98)))

    cpu_result = apply_stack_to_image_array(s, image)
    gpu_result = _render_stack_on_gpu(s, image)
    delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
    assert delta.max() < 1e-3, delta.max()
    context.doneCurrent()
    print("GpuStackRenderer: a disabled stage is skipped entirely, "
          "matching CPU -- OK")


def test_empty_stack_is_a_no_op():
    _app, context, surface = gsp._make_offscreen_gl_context()
    image = _test_image()
    s = Stack()

    gpu_result = _render_stack_on_gpu(s, image)
    delta = np.abs(image.astype(np.float64) - gpu_result.astype(np.float64))
    assert delta.max() < 1e-6, delta.max()
    context.doneCurrent()
    print("GpuStackRenderer: an empty stack returns the image unchanged -- OK")


# ---- Fork rendering (docs/archive/GPU_ACCELERATION_PLAN_2026-08-08.md Stage 6) ---------
# fork_split/fork_merge, the one remaining CPU-only holdout, now render via
# gpu_fork_merge_shader.py's dedicated 3-texture blend pass -- see
# gpu_stack_renderer.render_chain's own docstring for exactly how the
# shared ping-pong pair gets safely reused across both branches (snapshot
# the pre-split texture and Branch A's result before they'd otherwise be
# overwritten). These tests exercise that path end to end against the
# EXACT SAME CPU reference (apply_stack_to_image_array) every other test
# in this file already trusts, since fork's CPU math (stack.py's
# _run_stages) doesn't change here at all -- only the GPU side gains a
# path for it.

def test_fork_with_stages_on_both_branches_matches_cpu():
    _app, context, surface = gsp._make_offscreen_gl_context()
    image = _test_image()
    s = Stack()
    s.add_stage(make_cdl_stage(slope=(1.05, 1.0, 0.98)))  # before the fork
    s.add_stage(make_fork_split_stage())
    a1 = make_cdl_stage(offset=(0.05, 0.0, -0.02))
    a1.branch = "a"
    s.add_stage(a1)
    b1 = make_cdl_stage(slope=(1.1, 1.0, 0.95))
    b1.branch = "b"
    s.add_stage(b1)
    s.add_stage(make_fork_merge_stage(blend=0.35, opacity=0.8))
    s.add_stage(make_cdl_stage(sat=1.15))  # after the fork

    cpu_result = apply_stack_to_image_array(s, image)
    gpu_result = _render_stack_on_gpu(s, image)
    delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
    assert delta.max() < 1e-3, delta.max()
    # Confirm this ACTUALLY exercised forking, not a vacuous pass -- the
    # merge's own blend/opacity must meaningfully differ the result from
    # either branch alone.
    branch_a_only = apply_stack_to_image_array(
        Stack(stages=[make_cdl_stage(slope=(1.05, 1.0, 0.98)), make_cdl_stage(offset=(0.05, 0.0, -0.02)),
                      make_cdl_stage(sat=1.15)]),
        image)
    assert np.abs(cpu_result - branch_a_only).max() > 0.01
    context.doneCurrent()
    print(f"GpuStackRenderer: fork with stages on both branches matches "
          f"CPU, max|delta|={delta.max():.6f} -- OK")


def test_fork_with_one_branch_empty_matches_cpu():
    _app, context, surface = gsp._make_offscreen_gl_context()
    image = _test_image()
    s = Stack()
    s.add_stage(make_fork_split_stage())
    a1 = make_cdl_stage(slope=(1.2, 0.9, 1.05))
    a1.branch = "a"
    s.add_stage(a1)
    a2 = make_cdl_stage(offset=(0.03, -0.01, 0.02))
    a2.branch = "a"
    s.add_stage(a2)
    s.add_stage(make_fork_merge_stage(blend=0.6))  # Branch B is genuinely empty

    cpu_result = apply_stack_to_image_array(s, image)
    gpu_result = _render_stack_on_gpu(s, image)
    delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
    assert delta.max() < 1e-3, delta.max()
    context.doneCurrent()
    print(f"GpuStackRenderer: fork with one branch empty (2 stages on the "
          f"other) matches CPU, max|delta|={delta.max():.6f} -- OK")


def test_fork_with_mixed_stage_kinds_matches_cpu():
    _app, context, surface = gsp._make_offscreen_gl_context()
    image = _test_image()
    s = Stack()
    s.add_stage(make_fork_split_stage())
    a1 = make_grading_primary_stage(style="log")
    a1.params["contrast"] = (1.1, 1.05, 0.95, 1.0)
    a1.branch = "a"
    s.add_stage(a1)
    a2 = make_cdl_stage(sat=0.8)
    a2.branch = "a"
    s.add_stage(a2)
    b1 = make_exposure_contrast_stage()
    b1.params["exposure"] = 0.3
    b1.branch = "b"
    s.add_stage(b1)
    s.add_stage(make_fork_merge_stage(blend=0.45, opacity=0.9))

    cpu_result = apply_stack_to_image_array(s, image)
    gpu_result = _render_stack_on_gpu(s, image)
    delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
    assert delta.max() < 1e-3, delta.max()
    context.doneCurrent()
    print(f"GpuStackRenderer: fork mixing grading_primary/cdl/exposure_"
          f"contrast across branches matches CPU, max|delta|={delta.max():.6f} -- OK")


def test_fork_split_as_the_very_first_stage_with_a_single_branch_b_stage_matches_cpu():
    """Direct regression coverage for a real GL feedback-loop bug (see
    render_chain's own docstring, "FORK RENDERING"): with NO stages
    before the split (the outer ping-pong `output_index` is still at its
    initial 0) and exactly one enabled Branch B stage, Branch B's own
    result texture used to land in the SAME ping-pong slot the merge
    pass would then pick as its own render target (via the merge pass's
    outer `output_index`, unrelated to _render_stage_sequence's own
    fresh, locally-reset index) -- reading and writing the same texture
    in one draw call, undefined GL behavior. Branch A already got a
    snapshot before this fix; Branch B now does too. A strong, easily-
    visible transform on Branch B (large gain) makes any reintroduced
    aliasing show up as a large, obvious delta rather than a marginal
    one."""
    _app, context, surface = gsp._make_offscreen_gl_context()
    image = _test_image()
    s = Stack()
    s.add_stage(make_fork_split_stage())  # the very first stage -- outer output_index == 0 here
    b1 = make_cdl_stage(slope=(2.5, 0.4, 1.8))  # branch B's ONLY stage -- large, visible transform
    b1.branch = "b"
    s.add_stage(b1)
    s.add_stage(make_fork_merge_stage(blend=1.0))  # blend=1.0 -- the merge result IS branch B

    cpu_result = apply_stack_to_image_array(s, image)
    gpu_result = _render_stack_on_gpu(s, image)
    delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
    assert delta.max() < 1e-3, delta.max()
    context.doneCurrent()
    print(f"GpuStackRenderer: fork_split as the first stage with a single "
          f"Branch B stage matches CPU (no ping-pong aliasing), "
          f"max|delta|={delta.max():.6f} -- OK")


def test_disabled_fork_split_is_a_noop_matching_cpu():
    _app, context, surface = gsp._make_offscreen_gl_context()
    image = _test_image()
    s = Stack()
    s.add_stage(make_cdl_stage(slope=(1.05, 1.0, 0.98)))
    split = make_fork_split_stage(enabled=False)
    s.add_stage(split)
    a1 = make_cdl_stage(slope=(3.0, 3.0, 3.0))  # would be very visible if it ran
    a1.branch = "a"
    s.add_stage(a1)
    s.add_stage(make_fork_merge_stage())
    s.add_stage(make_cdl_stage(sat=1.15))

    cpu_result = apply_stack_to_image_array(s, image)
    gpu_result = _render_stack_on_gpu(s, image)
    delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
    assert delta.max() < 1e-3, delta.max()
    context.doneCurrent()
    print(f"GpuStackRenderer: a DISABLED fork_split makes the whole "
          f"region a no-op, matching CPU, max|delta|={delta.max():.6f} -- OK")


def test_disabled_fork_merge_matches_cpu():
    """Per stack.py's _run_stages: a disabled fork_merge still computes
    both branches, but the final blended result is discarded -- `running`
    stays exactly what it was pre-split. gpu_stack_renderer.render_chain
    takes the (numerically equivalent, cheaper) shortcut of not rendering
    either branch at all in this case -- see its own docstring."""
    _app, context, surface = gsp._make_offscreen_gl_context()
    image = _test_image()
    s = Stack()
    s.add_stage(make_fork_split_stage())
    a1 = make_cdl_stage(slope=(1.3, 0.7, 1.0))
    a1.branch = "a"
    s.add_stage(a1)
    b1 = make_cdl_stage(offset=(0.1, -0.1, 0.05))
    b1.branch = "b"
    s.add_stage(b1)
    s.add_stage(make_fork_merge_stage(blend=0.5, enabled=False))
    s.add_stage(make_cdl_stage(sat=1.1))

    cpu_result = apply_stack_to_image_array(s, image)
    gpu_result = _render_stack_on_gpu(s, image)
    delta = np.abs(cpu_result.astype(np.float64) - gpu_result.astype(np.float64))
    assert delta.max() < 1e-3, delta.max()
    context.doneCurrent()
    print(f"GpuStackRenderer: a DISABLED fork_merge discards both "
          f"branches, matching CPU, max|delta|={delta.max():.6f} -- OK")


if __name__ == "__main__":
    test_is_stack_gpu_eligible()
    test_is_stack_gpu_eligible_false_for_a_color_warper_stage_over_max_points()
    test_is_stack_gpu_eligible_with_forks()
    test_single_cdl_stage_matches_cpu()
    test_cdl_then_aces_drt_chain_matches_cpu()
    test_single_grading_primary_stage_matches_cpu()
    test_grading_primary_then_aces_drt_chain_matches_cpu()
    test_single_exposure_contrast_stage_matches_cpu()
    test_grading_primary_then_exposure_contrast_chain_matches_cpu()
    test_single_grading_tone_stage_matches_cpu()
    test_grading_tone_then_aces_drt_chain_matches_cpu()
    test_single_grading_rgb_curve_stage_matches_cpu()
    test_grading_rgb_curve_then_aces_drt_chain_matches_cpu()
    test_single_printer_lights_stage_matches_cpu()
    test_printer_lights_then_aces_drt_chain_matches_cpu()
    test_single_color_warper_stage_matches_cpu()
    test_color_warper_then_aces_drt_chain_matches_cpu()
    test_single_custom_drt_stage_matches_cpu()
    test_custom_drt_with_tuned_values_matches_cpu()
    test_single_lut_stage_matches_cpu()
    test_lut_then_cdl_chain_matches_cpu()
    test_stage_opacity_blend_matches_cpu()
    test_disabled_stage_is_skipped()
    test_empty_stack_is_a_no_op()
    test_fork_with_stages_on_both_branches_matches_cpu()
    test_fork_with_one_branch_empty_matches_cpu()
    test_fork_with_mixed_stage_kinds_matches_cpu()
    test_fork_split_as_the_very_first_stage_with_a_single_branch_b_stage_matches_cpu()
    test_disabled_fork_split_is_a_noop_matching_cpu()
    test_disabled_fork_merge_matches_cpu()
    print()
    print("All gpu_stack_renderer tests passed.")
