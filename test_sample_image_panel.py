"""Tests for sample_image_panel.py's editing-time downres behavior -- run
directly: python3 test_sample_image_panel.py

Covers the 2026-08-09 rework (see that module's "PREVIEW RESOLUTION"
comment and GPU_ACCELERATION_PLAN_2026-08-08.md's "band-aid" note) AND
its 2026-08-10 follow-up (see SCOPES_MAX_DIMENSION's own comment):

- A GPU-INELIGIBLE stack (e.g. containing a fork) behaves exactly
  as the 2026-08-09 rework left it: refresh_preview() renders at full
  resolution for an isolated edit, downsamples (EDIT_DOWNRES_FACTOR) only
  once a burst is detected, and upgrades back to full resolution once the
  burst settles.
- A GPU-ELIGIBLE stack (e.g. a CDL/lut/Tier-1-operator stack) ALWAYS
  recomputes at the small, fixed SCOPES_MAX_DIMENSION-capped resolution,
  regardless of burst-vs-isolated state, and never upgrades on settle --
  this recompute exists ONLY for ScopesPanel (the Viewer itself grades
  the stack on the GPU, at native resolution, independently -- see
  _array_and_gpu_stack_for_source). The ORIGINAL 2026-08-09 version of
  this only skipped the recompute DURING a burst, still paying full
  native-resolution CPU cost on every isolated edit and on every burst's
  settle tick -- which broke badly for a stack containing a 'lut' stage
  (colour.LUT3D.apply() measured 0.5-3.3 SECONDS at native/half preview
  resolution), a real, reported "painfully slow" regression this rework
  fixes at the root.

The first-ever direct tests of SampleImagePanel itself -- previously
untestable in this project's old sandbox environment (see
test_image_preview.py's own docstring: "this actually runs here, unlike
sample_image_panel.py itself"), but real widget construction works fine
on real hardware, which is what this project now runs on.

Requires a real (if offscreen) QApplication -- same headless-Qt setup as
every other GUI-touching verification in this project (see RUNNING.md).
"""
import sys
import tempfile

import numpy as np
from PIL import Image
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import app_logic
import sample_image_panel as sample_image_panel_module
import timeline as tl
from sample_image_panel import SampleImagePanel, WIPE_SOURCE_SAMPLE_BEFORE, WIPE_SOURCE_SAMPLE_AFTER
from stack import (Stack, apply_stack_to_image_array, make_cdl_stage, make_lut_stage,
                    make_keyer_stage, running_array_before_stage)


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _make_test_image(size=(200, 120)):
    """A gradient (not flat color) test image, same reasoning as
    test_image_preview.py's own _make_test_image -- a flat color can't
    distinguish "the right pixels" from "the right average.\""""
    w, h = size
    x = np.linspace(0, 1, w, dtype=np.float32)
    y = np.linspace(0, 1, h, dtype=np.float32)
    xx, yy = np.meshgrid(x, y)
    array = np.clip(np.stack([xx, yy, 1.0 - xx], axis=-1) * 255, 0, 255).astype(np.uint8)
    path = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
    Image.fromarray(array, mode="RGB").save(path)
    return path


def _panel_with_image_and_stack(size=(200, 120), gpu_eligible=True):
    """A SampleImagePanel with a real loaded image and a real (cheap,
    non-identity so the applied result is actually distinguishable from
    the input) one-stage Stack wired in via set_stack_getter -- the
    minimum setup every test below needs.

    `gpu_eligible` picks the stack shape: a single CDL stage (the default)
    is GPU-eligible (see gpu_stack_renderer.is_stack_gpu_eligible),
    matching what most tests here want to exercise since Stage 5. Tests of
    the CPU-side downres burst path specifically (which only still
    applies to GPU-INeligible stacks, see refresh_preview's own comment)
    pass False to get a retarget-wrapped LUT stage instead -- a
    fork_split/fork_merge pair used to be this fixture's "stable,
    permanently GPU-ineligible" choice, but GPU Stage 6 (2026-08-12) gave
    forking its own GPU path too, same as printer_lights partially
    gaining one at GPU Stage 7 before it. A retarget-wrapped LUT is the
    new stable choice: gpu_lut_shader.py's own SCOPE LIMIT permanently,
    deliberately excludes retarget wrapping (no GPU equivalent planned),
    unlike a fork's ineligibility, which only ever reflected "not
    supported YET.\""""
    panel = SampleImagePanel()
    path = _make_test_image(size=size)
    ok = panel.load_image(path)
    assert ok, "test setup: image failed to load"
    stack = Stack()
    if gpu_eligible:
        stack.add_stage(make_cdl_stage(slope=(1.1, 0.9, 1.0), offset=(0.02, 0.0, -0.01)))
    else:
        identity_lut = app_logic.create_identity_lut(size=5)
        lut_stage = make_lut_stage(identity_lut)
        lut_stage.params["retarget_from"] = "ACEScct"
        lut_stage.params["retarget_to"] = "ACES2065-1"
        stack.add_stage(lut_stage)
    panel.set_stack_getter(lambda: stack)
    return panel


def test_sample_loads_at_native_resolution_not_a_capped_preview_size():
    _app()
    panel = _panel_with_image_and_stack(size=(200, 120))
    assert panel._image_array.shape[:2] == (120, 200), (
        "Sample should load at the source's real native resolution, no "
        "480px-style preview cap")
    print("SampleImagePanel: Sample image loads at native resolution, "
          "no capped preview size -- OK")


def test_isolated_single_edit_on_gpu_ineligible_stack_renders_at_full_resolution():
    _app()
    panel = _panel_with_image_and_stack(size=(200, 120), gpu_eligible=False)
    panel.refresh_preview()  # one isolated edit, not part of a burst
    assert panel._last_after_was_downres is False
    assert panel.get_current_after_array().shape[:2] == (120, 200)
    print("SampleImagePanel.refresh_preview: a GPU-INeligible stack's isolated "
          "edit renders at full resolution, never downres -- OK")


def test_gpu_ineligible_stack_burst_downreses_starting_on_the_second_tick():
    _app()
    panel = _panel_with_image_and_stack(size=(200, 120), gpu_eligible=False)
    panel.refresh_preview()  # tick 1 -- starts the settle timer, full res
    assert panel._last_after_was_downres is False

    panel.refresh_preview()  # tick 2, arriving before tick 1 settled -- a real burst
    assert panel._last_after_was_downres is True
    after = panel.get_current_after_array()
    assert after.shape[:2] == (60, 100), (
        f"expected the EDIT_DOWNRES_FACTOR=2 decimation (120,200)->(60,100), got {after.shape}")
    print("SampleImagePanel.refresh_preview: a GPU-INeligible stack's second "
          "tick arriving before the first settles renders from the downres array -- OK")


def test_gpu_ineligible_stack_settling_after_a_burst_renders_full_resolution():
    _app()
    panel = _panel_with_image_and_stack(size=(200, 120), gpu_eligible=False)
    panel.refresh_preview()
    panel.refresh_preview()  # now in downres mode, per the test above
    assert panel._last_after_was_downres is True

    QTest.qWait(sample_image_panel_module.EDIT_SETTLE_DEBOUNCE_MS + 100)

    assert panel._last_after_was_downres is False
    assert panel.get_current_after_array().shape[:2] == (120, 200), (
        "once the burst goes quiet, the preview should snap back to a "
        "full-resolution render")
    print("SampleImagePanel: a GPU-INeligible stack, once a burst goes quiet, "
          "gets one final full-resolution frame replacing the downres one -- OK")


def test_gpu_eligible_stack_always_recomputes_at_scopes_resolution():
    """2026-08-10 fix for a real, reported "painfully slow" regression:
    the 2026-08-09 version of this only skipped the CPU recompute DURING
    a burst, still paying full native-resolution cost on every isolated
    edit and on every burst's settle tick -- which broke badly once a
    'lut' stage was GPU-eligible too (colour.LUT3D.apply() measured
    0.5-3.3 SECONDS at native/half preview resolution). Now a GPU-
    eligible stack ALWAYS recomputes at the small, fixed SCOPES_MAX_
    DIMENSION-capped resolution, for BOTH an isolated edit and every
    burst tick -- confirmed here with a wide (2200x100) test image so
    the scopes-capped shape is unambiguously different from both full
    resolution and the EDIT_DOWNRES_FACTOR=2 shape."""
    _app()
    panel = _panel_with_image_and_stack(size=(2200, 100), gpu_eligible=True)
    expected_scopes_shape = panel._image_array_scopes.shape[:2]
    assert expected_scopes_shape != (100, 2200), "test setup: the image must actually be big enough to downsample"
    assert expected_scopes_shape != panel._image_array_downres.shape[:2], (
        "test setup: scopes and EDIT_DOWNRES_FACTOR shapes must differ for this test to be unambiguous")

    panel.refresh_preview()  # isolated edit
    assert panel.get_current_after_array().shape[:2] == expected_scopes_shape, (
        "an isolated edit on a GPU-eligible stack should ALREADY be at "
        "scopes resolution, not full resolution")
    assert panel._last_after_was_downres is False

    panel.refresh_preview()  # tick 2, mid-burst
    assert panel.get_current_after_array().shape[:2] == expected_scopes_shape, (
        "a burst tick on a GPU-eligible stack should stay at scopes "
        "resolution too, same as an isolated edit")

    QTest.qWait(sample_image_panel_module.EDIT_SETTLE_DEBOUNCE_MS + 100)
    assert panel.get_current_after_array().shape[:2] == expected_scopes_shape, (
        "settling should NOT upgrade a GPU-eligible stack's resolution -- "
        "there's no higher-resolution version of this array to catch up to")
    print("SampleImagePanel.refresh_preview: a GPU-eligible stack always "
          "recomputes at scopes resolution, isolated edit or burst, no "
          "settle upgrade -- OK")


def test_gpu_eligible_stack_settle_does_not_trigger_a_redundant_rerender():
    _app()
    panel = _panel_with_image_and_stack(size=(200, 120), gpu_eligible=True)
    seen = []
    panel.after_image_changed.connect(lambda arr: seen.append(arr))

    panel.refresh_preview()
    assert len(seen) == 1

    QTest.qWait(sample_image_panel_module.EDIT_SETTLE_DEBOUNCE_MS + 100)

    assert len(seen) == 1, (
        "a GPU-eligible stack never sets _last_after_was_downres, so the "
        "settle timer firing afterward should be a no-op")
    print("SampleImagePanel: a GPU-eligible stack's settle timer firing "
          "doesn't trigger a redundant re-render -- OK")


def test_isolated_edit_on_gpu_ineligible_stack_does_not_trigger_a_redundant_settle_rerender():
    _app()
    panel = _panel_with_image_and_stack(size=(200, 120), gpu_eligible=False)
    seen = []
    panel.after_image_changed.connect(lambda arr: seen.append(arr))

    panel.refresh_preview()  # isolated, full-res immediately
    assert len(seen) == 1

    QTest.qWait(sample_image_panel_module.EDIT_SETTLE_DEBOUNCE_MS + 100)

    assert len(seen) == 1, (
        "an isolated edit was never downres'd, so the settle timer firing "
        "afterward should be a no-op, not a redundant identical re-render")
    print("SampleImagePanel: a GPU-INeligible stack's isolated (non-burst) "
          "edit doesn't trigger a pointless redundant re-render once its "
          "settle timer fires -- OK")


def test_empty_stack_previews_the_source_image_unchanged():
    """Per Tom: "New identity lut should be removed and you should be
    able to preview an empty stack" -- an empty stack (zero stages) must
    NOT be treated the same as "no stack at all"; refresh_preview should
    compute and show the source image unmodified, not clear the after
    array to None."""
    _app()
    panel = SampleImagePanel()
    path = _make_test_image(size=(64, 48))
    ok = panel.load_image(path)
    assert ok, "test setup: image failed to load"
    empty_stack = Stack()
    panel.set_stack_getter(lambda: empty_stack)

    panel.refresh_preview()

    after = panel.get_current_after_array()
    assert after is not None
    np.testing.assert_array_equal(after, panel._image_array)
    print("SampleImagePanel: an empty stack previews the source image "
          "unchanged, rather than clearing the after array -- OK")


def test_gpu_eligible_stack_always_gets_full_resolution_even_mid_burst():
    """GPU_ACCELERATION_PLAN_2026-08-08.md's Stage 5: a GPU-eligible stack
    (this test's CDL-only stack qualifies, see gpu_stack_renderer.
    is_stack_gpu_eligible) is graded by WipeCompareWidget itself, on the
    GPU, which is fast regardless of an edit burst -- so
    _array_and_gpu_stack_for_source should ALWAYS hand back the full
    native-resolution array (never the CPU-side downres/scopes array) for
    "Sample After," even while a burst is independently in progress and
    the CPU-side recompute (for Scopes only, see refresh_preview) is at
    scopes resolution. This is WHY the old "make 'before' match 'after's
    downres resolution" workaround (this test used to cover, before the
    Stage 5 rewrite) is gone -- there's no downres "after" to match in
    the GPU-eligible case at all, and WipeCompositor resamples each side
    independently anyway (see gpu_lanczos_compositor.py's own docstring)
    so a genuine resolution mismatch was never actually a problem to
    begin with."""
    _app()
    panel = _panel_with_image_and_stack(size=(200, 120))
    panel.refresh_preview()
    panel.refresh_preview()  # burst -- CPU recompute is at scopes resolution, see above

    stack = panel._stack_getter()
    gpu_stack = stack  # this test's stack (CDL only) is GPU-eligible
    before_array, before_gpu_stack, _ = panel._array_and_gpu_stack_for_source(
        WIPE_SOURCE_SAMPLE_BEFORE, gpu_stack)
    after_array, after_gpu_stack, _ = panel._array_and_gpu_stack_for_source(
        WIPE_SOURCE_SAMPLE_AFTER, gpu_stack)

    assert before_array.shape[:2] == (120, 200), "\"before\" is always the full native array"
    assert after_array.shape[:2] == (120, 200), (
        "\"after\" should be the full native pre-stack array (for the widget's "
        "GPU chain to grade), NOT the CPU-side scopes-resolution array, even mid-burst")
    assert after_gpu_stack is gpu_stack, "the stack itself must be handed to the widget for GPU grading"
    assert before_gpu_stack is None, "\"before\" is never stack-graded, by definition"
    print("SampleImagePanel._array_and_gpu_stack_for_source: a GPU-eligible "
          "stack always gets full-resolution native arrays, even mid-burst -- OK")


def test_downres_array_is_a_grow_only_decimation_of_the_working_array():
    _app()
    panel = _panel_with_image_and_stack(size=(200, 120))
    assert panel._image_array_downres.shape[:2] == (60, 100)
    # Every pixel of the decimated array is a real sample from the source
    # (nearest-neighbor stride), not synthesized -- spot-check one corner.
    assert np.array_equal(panel._image_array_downres[0, 0], panel._image_array[0, 0])
    print("SampleImagePanel._make_downres_array: produces a real strided "
          "decimation of the working array at the expected size -- OK")


def test_scopes_array_is_capped_at_the_target_dimension():
    _app()
    panel = _panel_with_image_and_stack(size=(2200, 100))
    h, w = panel._image_array_scopes.shape[:2]
    assert max(h, w) <= sample_image_panel_module.SCOPES_MAX_DIMENSION
    assert np.array_equal(panel._image_array_scopes[0, 0], panel._image_array[0, 0])
    print("SampleImagePanel._make_scopes_array: caps the long edge at "
          "SCOPES_MAX_DIMENSION, real strided samples not synthesized -- OK")


def test_scopes_array_is_unchanged_for_a_small_source():
    """A source already smaller than SCOPES_MAX_DIMENSION on its long
    edge shouldn't be downsampled at all -- factor floors at 1."""
    _app()
    panel = _panel_with_image_and_stack(size=(200, 120))
    assert panel._image_array_scopes.shape[:2] == (120, 200)
    print("SampleImagePanel._make_scopes_array: a source already under "
          "SCOPES_MAX_DIMENSION is left at its own resolution -- OK")


# --- matte highlight (per Tom: "a way to preview the mask similar to
# resolve's highlight mode") ------------------------------------------

def test_compose_highlight_overlay_matte_one_is_unchanged():
    graded = np.random.default_rng(0).uniform(0.0, 1.0, size=(4, 4, 3))
    matte = np.ones((4, 4))
    result = sample_image_panel_module._compose_highlight_overlay(graded, matte)
    assert np.allclose(result, graded)
    print("_compose_highlight_overlay: matte=1 everywhere leaves the "
          "graded array unchanged -- OK")


def test_compose_highlight_overlay_matte_zero_is_solid_gray():
    graded = np.array([[[1.0, 0.0, 0.0]]])  # pure red
    matte = np.zeros((1, 1))
    result = sample_image_panel_module._compose_highlight_overlay(graded, matte)
    level = sample_image_panel_module.HIGHLIGHT_GRAY_LEVEL
    assert np.allclose(result[0, 0], [level] * 3, atol=1e-6)
    print("_compose_highlight_overlay: matte=0 everywhere becomes a "
          "flat HIGHLIGHT_GRAY_LEVEL gray, not the image's own "
          "brightness -- OK")


def test_compose_highlight_overlay_gray_is_flat_regardless_of_underlying_brightness():
    """The whole point of switching to a solid gray (per Tom: "solid
    gray for pixels outside of the key") -- unlike a luma-derived
    grayscale, two very differently-bright pixels must both key down to
    the EXACT SAME gray level."""
    bright = np.array([[[0.9, 0.9, 0.9]]])
    dark = np.array([[[0.05, 0.05, 0.05]]])
    matte = np.zeros((1, 1))
    bright_result = sample_image_panel_module._compose_highlight_overlay(bright, matte)
    dark_result = sample_image_panel_module._compose_highlight_overlay(dark, matte)
    assert np.allclose(bright_result, dark_result, atol=1e-6)
    print("_compose_highlight_overlay: the un-keyed gray is flat, not "
          "derived from the underlying image's own brightness -- OK")


def test_compose_highlight_overlay_blends_at_a_partial_matte():
    graded = np.array([[[1.0, 0.0, 0.0]]])
    matte = np.array([[0.5]])
    result = sample_image_panel_module._compose_highlight_overlay(graded, matte)
    level = sample_image_panel_module.HIGHLIGHT_GRAY_LEVEL
    expected = np.array([1.0, 0.0, 0.0]) * 0.5 + np.array([level] * 3) * 0.5
    assert np.allclose(result[0, 0], expected, atol=1e-6)
    print("_compose_highlight_overlay: a partial matte blends real "
          "color and the flat gray proportionally -- OK")


def test_no_highlight_getter_leaves_the_after_source_unchanged():
    _app()
    panel = _panel_with_image_and_stack()
    panel.refresh_preview()
    array, gpu_stage, _placeholder = panel._array_and_gpu_stack_for_source(
        WIPE_SOURCE_SAMPLE_AFTER, None)
    assert np.array_equal(array, panel.get_current_after_array())
    print("SampleImagePanel: with no highlight getter set, Sample After "
          "is returned completely unchanged -- OK")


def test_highlight_getter_composites_the_toggled_stages_matte():
    _app()
    panel = SampleImagePanel()
    path = _make_test_image(size=(200, 120))
    ok = panel.load_image(path)
    assert ok, "test setup: image failed to load"
    keyer_stage = make_keyer_stage(hue_center=0.0, hue_width=180.0)
    stack = Stack(stages=[keyer_stage])
    panel.set_stack_getter(lambda: stack)
    panel.set_highlight_getter(lambda: keyer_stage.id)
    panel.refresh_preview()

    real_after = panel.get_current_after_array().copy()
    array, gpu_stage, _placeholder = panel._array_and_gpu_stack_for_source(
        WIPE_SOURCE_SAMPLE_AFTER, None)
    assert not np.array_equal(array, real_after), \
        "a real, non-trivial matte should visibly change the composited preview"
    # Scopes/export's own source of truth must stay the REAL graded
    # array -- Highlight only ever affects what's handed to the widget.
    assert np.array_equal(panel.get_current_after_array(), real_after)
    print("SampleImagePanel: a toggled highlight composites the stage's "
          "own matte into the WIDGET's array without touching "
          "get_current_after_array()/Scopes' own source -- OK")


def test_highlight_getter_for_a_stage_not_in_the_stack_falls_back_to_plain():
    _app()
    panel = _panel_with_image_and_stack()  # plain CDL stack, no matte-producing stage at all
    panel.set_highlight_getter(lambda: 999999)  # no such stage id anywhere
    panel.refresh_preview()
    array, _gpu_stage, _placeholder = panel._array_and_gpu_stack_for_source(
        WIPE_SOURCE_SAMPLE_AFTER, None)
    assert np.array_equal(array, panel.get_current_after_array())
    print("SampleImagePanel: a highlight getter pointing at a stage "
          "that never produced a matte falls back to the plain array, "
          "no error -- OK")


def test_pick_pixel_color_for_stage_reflects_this_stages_input_not_final_output():
    """Direct regression coverage for the fix (per Tom: "the color picker
    for the key still seems to rely on actually pulling a pixel from the
    preview image, it should be pulling up the pixel address of the
    input of the key entry"): a keyer that ISN'T the last stage in the
    stack must pick its own actual input, not the fully-graded on-screen
    output every stage AFTER it would also have touched."""
    _app()
    panel = _panel_with_image_and_stack(size=(200, 120))  # one CDL stage already
    stack = panel._stack_getter()
    keyer_stage = make_keyer_stage()
    stack.add_stage(keyer_stage)
    stack.add_stage(make_cdl_stage(slope=(1.5, 1.5, 1.5)))  # a SECOND grade, AFTER the keyer

    x_frac, y_frac = 0.3, 0.4
    picked = []
    panel.pick_pixel_color_for_stage(keyer_stage.id, lambda rgb: picked.append(rgb))
    assert panel.compare._eyedropper_armed
    panel.compare._eyedropper_callback(x_frac, y_frac)
    assert len(picked) == 1

    h, w = panel._image_array.shape[:2]
    y, x = int(y_frac * h), int(x_frac * w)
    source_pixel = panel._image_array[y:y + 1, x:x + 1, :]
    expected_input = running_array_before_stage(stack, source_pixel, keyer_stage.id)[0, 0]
    final_output = apply_stack_to_image_array(stack, panel._image_array)[y, x]

    assert np.allclose(picked[0], expected_input, atol=1e-5)
    assert not np.allclose(picked[0], final_output, atol=1e-3), \
        "picking must reflect the keyer's own input, not the fully-graded on-screen output"
    print("SampleImagePanel.pick_pixel_color_for_stage: resolves the "
          "picked stage's own actual input, NOT the final composited "
          "output -- OK")


def test_pick_pixel_color_for_stage_is_a_noop_without_a_loaded_image():
    _app()
    panel = SampleImagePanel()
    stack = Stack()
    keyer_stage = make_keyer_stage()
    stack.add_stage(keyer_stage)
    panel.set_stack_getter(lambda: stack)
    picked = []
    panel.pick_pixel_color_for_stage(keyer_stage.id, lambda rgb: picked.append(rgb))
    panel.compare._eyedropper_callback(0.5, 0.5)
    assert picked == []
    print("SampleImagePanel.pick_pixel_color_for_stage: a no-op (never "
          "calls back) with no image loaded -- OK")


def test_pick_pixel_color_for_stage_is_a_noop_for_an_unresolved_stage_id():
    _app()
    panel = _panel_with_image_and_stack()
    picked = []
    panel.pick_pixel_color_for_stage("not-a-real-stage-id", lambda rgb: picked.append(rgb))
    panel.compare._eyedropper_callback(0.5, 0.5)
    assert picked == []
    print("SampleImagePanel.pick_pixel_color_for_stage: a no-op when "
          "stage_id can't be resolved in the current stack -- OK")


def _make_test_video(frame_count=8, frame_rate=24, size=(64, 48)):
    """A short real clip, distinct solid color per frame -- same
    "unambiguous per-frame marker" convention test_video_source.py's own
    fixture uses, so a test can confirm parked-frame loading landed on
    the RIGHT frame, not just decoded successfully."""
    import av

    w, h = size
    path = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
    container = av.open(path, mode="w")
    stream = container.add_stream("libx264", rate=frame_rate)
    stream.width = w
    stream.height = h
    stream.pix_fmt = "yuv420p"
    for i in range(frame_count):
        color = (min(255, i * 30), max(0, 255 - i * 30), 128)
        frame = av.VideoFrame.from_ndarray(
            np.full((h, w, 3), color, dtype=np.uint8), format="rgb24")
        frame.pts = i
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()
    return path


def test_load_video_parks_on_the_first_frame_through_the_still_pipeline():
    """Parked-frame grading reuses the EXACT SAME downstream pipeline a
    still image load already uses (Tom's own confirmed scope) -- this
    confirms _image_array/_current_after_array are populated exactly the
    way they would be for a still, not through some separate code path."""
    _app()
    panel = SampleImagePanel()
    path = _make_test_video(frame_count=8, size=(64, 48))
    ok = panel.load_video(path)
    assert ok
    assert panel._video_source is not None
    assert panel._video_frame_index == 0
    assert panel._image_array.shape[:2] == (48, 64)
    print("SampleImagePanel.load_video: parks on frame 0 through the same "
          "pipeline a still-image load uses -- OK")


def test_seek_video_frame_updates_the_working_array():
    _app()
    panel = SampleImagePanel()
    path = _make_test_video(frame_count=8, size=(64, 48))
    assert panel.load_video(path)
    before = panel._image_array.copy()

    ok = panel.seek_video_frame(5)
    assert ok
    assert panel._video_frame_index == 5
    after = panel._image_array
    assert not np.array_equal(before, after), "frame 5 should look different from frame 0"
    print("SampleImagePanel.seek_video_frame: re-decodes and updates the "
          "working array for the new frame -- OK")


def test_video_loaded_signal_reports_frame_count_and_rate():
    _app()
    panel = SampleImagePanel()
    path = _make_test_video(frame_count=12, frame_rate=30, size=(64, 48))
    received = []
    panel.video_loaded.connect(lambda count, rate: received.append((count, rate)))
    assert panel.load_video(path)
    assert len(received) == 1
    frame_count, frame_rate = received[0]
    assert frame_count == 12
    assert abs(frame_rate - 30.0) < 0.1
    print("SampleImagePanel.video_loaded: reports the real frame count/rate -- OK")


def test_loading_a_still_image_closes_any_open_video_source():
    _app()
    panel = SampleImagePanel()
    video_path = _make_test_video(frame_count=4, size=(64, 48))
    assert panel.load_video(video_path)
    assert panel._video_source is not None

    still_path = _make_test_image(size=(50, 40))
    assert panel.load_image(still_path)
    assert panel._video_source is None, "loading a still must close/clear any open video source"
    assert panel._image_array.shape[:2] == (40, 50)
    print("SampleImagePanel.load_image: closes any previously-open video "
          "source rather than leaking it -- OK")


def test_loading_a_new_video_closes_the_previous_one():
    _app()
    panel = SampleImagePanel()
    first_path = _make_test_video(frame_count=4, size=(64, 48))
    assert panel.load_video(first_path)
    first_source = panel._video_source

    second_path = _make_test_video(frame_count=6, size=(80, 60))
    assert panel.load_video(second_path)
    assert panel._video_source is not first_source
    assert panel._image_array.shape[:2] == (60, 80)
    print("SampleImagePanel.load_video: replaces (and closes) any "
          "previously-open video source -- OK")


def test_seek_video_frame_is_a_noop_with_no_video_loaded():
    _app()
    panel = SampleImagePanel()
    assert panel.seek_video_frame(3) is False
    print("SampleImagePanel.seek_video_frame: returns False, doesn't crash, "
          "when no video is loaded -- OK")


def test_video_transport_bar_hidden_until_a_video_loads():
    _app()
    panel = SampleImagePanel()
    assert panel._video_transport_widget.isHidden(), (
        "hidden by default -- SampleImagePanel starts with a still image, not a video")
    path = _make_test_video(frame_count=6, size=(64, 48))
    assert panel.load_video(path)
    assert not panel._video_transport_widget.isHidden()
    print("SampleImagePanel: video transport bar hidden until a video "
          "actually loads, then shown -- OK")


def test_video_transport_bar_hides_again_when_a_still_replaces_the_video():
    _app()
    panel = SampleImagePanel()
    video_path = _make_test_video(frame_count=6, size=(64, 48))
    assert panel.load_video(video_path)
    assert not panel._video_transport_widget.isHidden()

    still_path = _make_test_image(size=(50, 40))
    assert panel.load_image(still_path)
    assert panel._video_transport_widget.isHidden()
    print("SampleImagePanel: video transport bar hides again once a "
          "still image replaces the loaded video -- OK")


def test_video_transport_slider_range_matches_frame_count():
    _app()
    panel = SampleImagePanel()
    path = _make_test_video(frame_count=15, size=(64, 48))
    assert panel.load_video(path)
    assert panel._video_scrub_slider.minimum() == 0
    assert panel._video_scrub_slider.maximum() == 14
    assert panel._video_scrub_slider.value() == 0
    print("SampleImagePanel: scrub slider range matches [0, frame_count-1] -- OK")


def test_video_transport_timecode_label_updates_on_seek():
    """Phase 4: the transport bar shows real SMPTE timecode (see
    timecode.py), not a plain "N / total" count -- frame count moved to
    the label's tooltip instead (see _update_video_timecode_label)."""
    _app()
    panel = SampleImagePanel()
    path = _make_test_video(frame_count=10, frame_rate=24, size=(64, 48))
    assert panel.load_video(path)
    assert panel._video_timecode_label.text() == "00:00:00:00"
    assert panel._video_timecode_label.toolTip() == "Frame 1 / 10"
    panel.seek_video_frame(4)
    assert panel._video_timecode_label.text() == "00:00:00:04"
    assert panel._video_timecode_label.toolTip() == "Frame 5 / 10"
    print("SampleImagePanel: timecode label shows real SMPTE timecode "
          "and updates on seek, frame count moved to the tooltip -- OK")


def test_video_transport_step_buttons_are_disabled_and_are_noops():
    """Step/Set In/Set Out/Clear are all disabled now (Tom, 2026-08-24:
    "the transport can be exclusive to the timeline" -- see sample_
    image_panel.py's _on_video_step_back_clicked docstring). Left wired
    (not removed), but must be a true no-op -- if they still seeked the
    single loaded clip, pressing Left/Right (still-active keyboard
    shortcuts) while the Viewer shows Timeline content would silently
    hijack it back to this clip."""
    _app()
    panel = SampleImagePanel()
    path = _make_test_video(frame_count=5, size=(64, 48))
    assert panel.load_video(path)
    assert not panel._video_step_back_button.isEnabled()
    assert not panel._video_step_forward_button.isEnabled()

    panel._on_video_step_forward_clicked()
    assert panel._video_frame_index == 0, "step forward must be a no-op"
    panel.seek_video_frame(2)
    panel._on_video_step_back_clicked()
    assert panel._video_frame_index == 2, "step back must be a no-op"
    print("SampleImagePanel: step-back/step-forward are disabled and are "
          "true no-ops -- OK")


def test_video_transport_scrub_slider_drag_seeks_the_video():
    """The slider's valueChanged handler must actually re-decode/re-grade
    -- a real user drag, not a programmatic sync (see
    _suppress_video_scrub_signal's own guard, which THIS test must NOT
    be blocked by)."""
    _app()
    panel = SampleImagePanel()
    path = _make_test_video(frame_count=10, size=(64, 48))
    assert panel.load_video(path)
    before = panel._image_array.copy()

    panel._video_scrub_slider.setValue(6)  # simulates a real user drag
    assert panel._video_frame_index == 6
    assert not np.array_equal(before, panel._image_array)
    print("SampleImagePanel: dragging the scrub slider actually seeks "
          "and re-grades the video -- OK")


def _panel_with_timeline_clip(frame_count=10, frame_rate=24, size=(64, 48)):
    """Wires a one-clip Timeline directly into a bare SampleImagePanel,
    same getters main_window.py wires from TimelinePanel/StackPanel --
    Play/Pause/Loop/Speed now drive self._timeline_controller (Tom,
    2026-08-24), not the single-clip self._playback_controller, so these
    tests need a Timeline, not just panel.load_video."""
    _app()
    panel = SampleImagePanel()
    path = _make_test_video(frame_count=frame_count, frame_rate=frame_rate, size=size)
    clip = tl.TimelineClip("clip", path, "video", 0, frame_count - 1)
    timeline_obj = tl.Timeline(clips=[clip])
    panel.set_timeline_getter(lambda: timeline_obj)
    panel.set_sequence_stack_getter(lambda: Stack())
    return panel


def test_video_play_pause_button_toggles_timeline_playback_and_its_own_label():
    """RECORD mode explicitly -- the toggle button decides which
    controller Play targets (see _active_transport_controller), and the
    default is SOURCE (see test_viewer_defaults_to_source_mode)."""
    panel = _panel_with_timeline_clip()
    panel._viewer_mode_badge.setChecked(True)
    assert panel._video_play_button.text() == "▶"

    panel._on_video_play_pause_clicked()
    assert panel._timeline_controller.is_playing
    assert panel._video_play_button.text() == "⏸"

    panel._on_video_play_pause_clicked()
    assert not panel._timeline_controller.is_playing
    assert panel._video_play_button.text() == "▶"
    print("SampleImagePanel: Play/Pause button toggles Timeline playback "
          "and its own label -- OK")


def test_viewer_defaults_to_source_mode():
    _app()
    panel = SampleImagePanel()
    assert panel._viewer_mode == "source"
    assert panel._viewer_mode_badge.text() == "SOURCE"
    assert panel._viewer_mode_frame.property("viewerMode") == "source"
    print("SampleImagePanel: the Viewer defaults to SOURCE mode -- OK")


def test_viewer_mode_toggle_button_is_a_manual_switch_not_play_driven():
    """Per the follow-up round of direct feedback: "it should be a
    toggle you can control all the time and play should work for
    either" -- NOT something play/pause flips automatically anymore.
    The toggle button itself must move the mode regardless of playback
    state, and playing/pausing must NOT move it back."""
    panel = _panel_with_timeline_clip()
    assert panel._viewer_mode == "source"
    assert not panel._viewer_mode_badge.isChecked()

    panel._viewer_mode_badge.setChecked(True)
    assert panel._viewer_mode == "record"
    assert panel._viewer_mode_badge.text() == "RECORD"
    assert panel._viewer_mode_frame.property("viewerMode") == "record"

    # Playing and pausing must not touch the toggle -- this is the
    # actual regression this test guards: the OLD behavior auto-flipped
    # mode on playback_started/playback_stopped.
    panel._on_video_play_pause_clicked()
    assert panel._viewer_mode == "record"
    panel._on_video_play_pause_clicked()
    assert panel._viewer_mode == "record"

    panel._viewer_mode_badge.setChecked(False)
    assert panel._viewer_mode == "source"
    assert panel._viewer_mode_badge.text() == "SOURCE"
    print("SampleImagePanel: the SOURCE/RECORD toggle is a manual "
          "switch, unaffected by play/pause -- OK")


def test_play_in_source_mode_drives_the_single_clip_controller():
    """"play should work for either" -- while the toggle is on SOURCE,
    Play/Pause must drive self._playback_controller (the single
    directly-loaded/selected clip), not self._timeline_controller."""
    panel = _panel_with_timeline_clip()
    assert panel._viewer_mode == "source"
    panel.load_video(_make_test_video(frame_count=10, size=(64, 48)))

    panel._on_video_play_pause_clicked()
    assert panel._playback_controller.is_playing
    assert not panel._timeline_controller.is_playing
    assert panel._video_play_button.text() == "⏸"

    panel._on_video_play_pause_clicked()
    assert not panel._playback_controller.is_playing
    print("SampleImagePanel: Play in SOURCE mode drives the single-clip "
          "controller, not the Timeline -- OK")


def test_play_in_record_mode_drives_the_timeline_controller():
    """Same as test_play_in_source_mode_drives_the_single_clip_controller,
    flipped -- RECORD mode's Play must drive self._timeline_controller,
    matching the pre-toggle default behavior exactly."""
    panel = _panel_with_timeline_clip()
    panel._viewer_mode_badge.setChecked(True)
    assert panel._viewer_mode == "record"

    panel._on_video_play_pause_clicked()
    assert panel._timeline_controller.is_playing
    assert not panel._playback_controller.is_playing
    assert panel._video_play_button.text() == "⏸"

    panel._on_video_play_pause_clicked()
    assert not panel._timeline_controller.is_playing
    print("SampleImagePanel: Play in RECORD mode drives the Timeline "
          "controller -- OK")


def test_switching_viewer_mode_mid_playback_pauses_both_controllers():
    """Only one Viewer display exists (self.compare) -- switching the
    toggle while the OLD mode is mid-playback must stop it, or two
    playback loops would both be trying to push frames to the same
    surface (see _on_viewer_mode_toggled's own docstring)."""
    panel = _panel_with_timeline_clip()
    panel.load_video(_make_test_video(frame_count=10, size=(64, 48)))
    panel._on_video_play_pause_clicked()  # SOURCE mode's Play
    assert panel._playback_controller.is_playing

    panel._viewer_mode_badge.setChecked(True)  # switch to RECORD mid-playback
    assert not panel._playback_controller.is_playing
    assert not panel._timeline_controller.is_playing
    assert panel._video_play_button.text() == "▶"
    print("SampleImagePanel: switching SOURCE/RECORD mid-playback pauses "
          "both controllers -- OK")


def test_viewer_stays_in_source_mode_for_a_plain_loaded_video():
    """Loading a video directly (not via the Timeline) never touches
    self._timeline_controller at all -- the Viewer must stay in SOURCE
    mode, not get stuck on whatever mode an earlier Timeline session
    left behind."""
    _app()
    panel = SampleImagePanel()
    path = _make_test_video(frame_count=10, size=(64, 48))
    assert panel.load_video(path)
    assert panel._viewer_mode == "source"
    print("SampleImagePanel: a plain loaded video stays in SOURCE mode "
          "-- OK")


def _panel_with_two_clip_timeline(frame_counts=(10, 6), frame_rate=24, size=(64, 48)):
    """Same shape as _panel_with_timeline_clip, but TWO clips -- needed
    for confirming the scrub bar's RECORD-mode range spans the whole
    Timeline (per direct feedback: "the scrub bar seems to only
    represent a single clip in the timeline. it should represent the
    whole timeline"), not just one clip's own frame_count."""
    _app()
    panel = SampleImagePanel()
    clips = [
        tl.TimelineClip(f"clip{i}", _make_test_video(frame_count=n, frame_rate=frame_rate, size=size),
                         "video", 0, n - 1)
        for i, n in enumerate(frame_counts)
    ]
    timeline_obj = tl.Timeline(clips=clips)
    panel.set_timeline_getter(lambda: timeline_obj)
    panel.set_sequence_stack_getter(lambda: Stack())
    return panel, timeline_obj


def test_scrub_bar_range_spans_the_whole_timeline_in_record_mode():
    panel, timeline_obj = _panel_with_two_clip_timeline(frame_counts=(10, 6))
    panel._viewer_mode_badge.setChecked(True)  # RECORD
    assert panel._video_scrub_slider.maximum() == 15, (
        f"expected 16 total frames (10+6) - 1 = 15, got {panel._video_scrub_slider.maximum()}")
    print("SampleImagePanel: RECORD mode's scrub bar range spans the "
          "whole Timeline's total_frame_count, not one clip -- OK")


def test_scrub_bar_range_is_the_single_clip_in_source_mode():
    """The same panel/Timeline as the RECORD test above -- confirms
    SOURCE mode's range is unaffected by the Timeline's own total and
    reflects only the directly-loaded clip."""
    panel, timeline_obj = _panel_with_two_clip_timeline(frame_counts=(10, 6))
    panel.load_video(_make_test_video(frame_count=8, size=(64, 48)))
    assert panel._viewer_mode == "source"
    assert panel._video_scrub_slider.maximum() == 7
    print("SampleImagePanel: SOURCE mode's scrub bar range is the "
          "single loaded clip's own, unaffected by the Timeline's "
          "total -- OK")


def test_dragging_scrub_bar_in_record_mode_seeks_the_whole_timeline():
    panel, timeline_obj = _panel_with_two_clip_timeline(frame_counts=(10, 6))
    panel._viewer_mode_badge.setChecked(True)  # RECORD

    panel._on_video_scrub_slider_changed(12)  # clip 0 has 10 frames -> clip 1's own offset 2
    assert panel._timeline_controller.current_record_frame() == 12
    assert panel._timeline_controller._clip_index == 1
    assert not panel._playback_controller.is_playing
    print("SampleImagePanel: dragging the scrub bar in RECORD mode "
          "seeks the whole Timeline via TimelineController."
          "seek_to_record_frame -- OK")


def test_dragging_scrub_bar_in_source_mode_seeks_only_the_single_clip():
    """Regression guard: RECORD-mode routing must not have broken the
    original SOURCE-mode scrub behavior, or leaked into the Timeline
    controller's own state at all."""
    panel, timeline_obj = _panel_with_two_clip_timeline(frame_counts=(10, 6))
    panel.load_video(_make_test_video(frame_count=8, size=(64, 48)))
    assert panel._viewer_mode == "source"
    untouched_record_frame = panel._timeline_controller.current_record_frame()

    panel._on_video_scrub_slider_changed(5)
    assert panel._video_frame_index == 5
    assert panel._timeline_controller.current_record_frame() == untouched_record_frame, (
        "dragging the scrub bar in SOURCE mode must not touch the Timeline controller at all")
    print("SampleImagePanel: dragging the scrub bar in SOURCE mode "
          "still only seeks the single directly-loaded clip -- OK")


def test_scrub_bar_tracks_record_position_during_timeline_playback():
    panel, timeline_obj = _panel_with_two_clip_timeline(frame_counts=(10, 6))
    panel._viewer_mode_badge.setChecked(True)  # RECORD
    panel._on_video_play_pause_clicked()
    assert panel._timeline_controller.is_playing

    panel._timeline_controller._tick()
    panel._timeline_controller._tick()
    assert panel._video_scrub_slider.value() == panel._timeline_controller.current_record_frame()
    panel._timeline_controller.stop()
    print("SampleImagePanel: the scrub bar's VALUE tracks live during "
          "RECORD-mode Timeline playback -- OK")


def test_switching_to_record_mode_positions_scrub_bar_at_current_record_frame():
    """Switching the toggle isn't just a range change -- the scrub bar's
    VALUE should immediately reflect wherever the Timeline controller
    already is (e.g. from an earlier seek_to_clip), not silently reset
    to 0."""
    panel, timeline_obj = _panel_with_two_clip_timeline(frame_counts=(10, 6))
    panel._timeline_controller.seek_to_record_frame(4)

    panel._viewer_mode_badge.setChecked(True)  # RECORD
    assert panel._video_scrub_slider.value() == 4
    print("SampleImagePanel: switching to RECORD mode positions the "
          "scrub bar at the Timeline controller's current position -- OK")


def test_video_set_in_out_and_clear_buttons_are_disabled_and_are_noops():
    """Timeline-level in/out is out of scope for this pass (see
    timeline_playback_controller.py's own module docstring) -- these
    three buttons must be disabled and inert, not silently acting on the
    single-clip controller anymore."""
    _app()
    panel = SampleImagePanel()
    path = _make_test_video(frame_count=10, size=(64, 48))
    assert panel.load_video(path)
    assert not panel._video_set_in_button.isEnabled()
    assert not panel._video_set_out_button.isEnabled()
    assert not panel._video_clear_in_out_button.isEnabled()

    panel.seek_video_frame(3)
    panel._on_video_set_in_clicked()
    panel._on_video_set_out_clicked()
    panel._on_video_clear_in_out_clicked()
    assert panel._playback_controller.in_point == 0
    assert panel._playback_controller.out_point is None
    print("SampleImagePanel: Set In/Set Out/Clear buttons are disabled "
          "and are true no-ops -- OK")


def test_video_loop_button_toggles_timeline_controller_loop_state():
    """RECORD mode explicitly -- see test_video_play_pause_button_
    toggles_timeline_playback_and_its_own_label's own note."""
    panel = _panel_with_timeline_clip()
    panel._viewer_mode_badge.setChecked(True)
    assert not panel._timeline_controller.loop_enabled
    panel._video_loop_button.setChecked(True)
    assert panel._timeline_controller.loop_enabled
    panel._video_loop_button.setChecked(False)
    assert not panel._timeline_controller.loop_enabled
    print("SampleImagePanel: the Loop toolbutton drives the Timeline "
          "controller's loop_enabled -- OK")


def test_video_speed_combo_drives_the_timeline_controller_speed():
    """RECORD mode explicitly -- see test_video_play_pause_button_
    toggles_timeline_playback_and_its_own_label's own note."""
    panel = _panel_with_timeline_clip()
    panel._viewer_mode_badge.setChecked(True)
    assert panel._timeline_controller.speed == 1.0

    index_of_2x = panel._video_speed_combo.findText("2x")
    assert index_of_2x >= 0
    panel._video_speed_combo.setCurrentIndex(index_of_2x)
    assert panel._timeline_controller.speed == 2.0
    print("SampleImagePanel: the speed combo drives the Timeline "
          "controller's speed -- OK")


def test_video_home_end_shortcuts_are_disabled_and_are_noops():
    _app()
    panel = SampleImagePanel()
    path = _make_test_video(frame_count=10, size=(64, 48))
    assert panel.load_video(path)
    panel.seek_video_frame(4)

    panel._on_video_jump_to_in_clicked()
    assert panel._video_frame_index == 4, "Home must be a no-op"

    panel._on_video_jump_to_out_clicked()
    assert panel._video_frame_index == 4, "End must be a no-op"
    print("SampleImagePanel: Home/End shortcut handlers are disabled and "
          "are true no-ops -- OK")


def test_load_reference_from_array_loads_directly_into_reference_slot():
    """Stills Gallery, Phase 3 follow-up: a StackMemory's captured
    full_still has no source path to reload from -- load_reference_from_
    array must accept an already-decoded array directly."""
    _app()
    panel = _panel_with_image_and_stack()
    array = np.full((20, 30, 3), 0.5, dtype=np.float32)
    panel.load_reference_from_array(array)
    assert panel._reference_original_array is array
    assert panel._reference_current_path is None
    print("SampleImagePanel.load_reference_from_array: loads an "
          "already-decoded array directly into the Reference slot -- OK")


def test_render_full_resolution_after_for_capture_delegates_for_a_still():
    _app()
    panel = _panel_with_image_and_stack(size=(200, 120))
    stack = panel._stack_getter()
    result = panel.render_full_resolution_after_for_capture(stack)
    expected = panel.render_full_resolution_after(stack)
    np.testing.assert_array_equal(result, expected)
    print("SampleImagePanel.render_full_resolution_after_for_capture: "
          "delegates to render_full_resolution_after for a still-backed "
          "Sample -- OK")


def test_render_full_resolution_after_for_capture_redecodes_the_parked_video_frame():
    _app()
    panel = SampleImagePanel()
    path = _make_test_video(frame_count=8, size=(64, 48))
    assert panel.load_video(path)
    panel.seek_video_frame(3)
    stack = Stack()
    stack.add_stage(make_cdl_stage(slope=(1.1, 0.9, 1.0)))

    result = panel.render_full_resolution_after_for_capture(stack)
    assert result.shape[:2] == (48, 64)
    # Re-decoding frame 3 must not have moved the panel's own parked
    # position -- capture is read-only from the panel's perspective.
    assert panel._video_frame_index == 3
    print("SampleImagePanel.render_full_resolution_after_for_capture: "
          "re-decodes the current parked VIDEO frame at full resolution "
          "and grades it, without disturbing the panel's parked position "
          "-- OK")


def test_get_session_state_flags_a_video_sample_as_is_video():
    """A real bug this test would have caught before it shipped: without
    this flag, load_session_state can't tell a video Sample's path from
    a still's, and routes it through the still-image decoder (OIIO,
    which can't read a video container) -- reported by Tom via a real
    save/reload of a video Sample."""
    _app()
    panel = SampleImagePanel()
    path = _make_test_video(frame_count=8, size=(64, 48))
    assert panel.load_video(path)
    panel.seek_video_frame(3)

    state = panel.get_session_state()
    assert state["is_video"] is True
    assert state["video_frame_index"] == 3
    assert state["path"] == path
    print("SampleImagePanel.get_session_state: flags a video Sample with "
          "is_video/video_frame_index -- OK")


def test_get_session_state_omits_is_video_for_a_still_sample():
    _app()
    panel = _panel_with_image_and_stack()
    state = panel.get_session_state()
    assert "is_video" not in state
    print("SampleImagePanel.get_session_state: a still Sample has no "
          "is_video key -- OK")


def test_load_session_state_reopens_a_video_sample_through_load_video():
    """The actual fix -- load_session_state must route an is_video state
    through load_video (real decode), not _load_image_core (OIIO), and
    land back on the exact frame that was parked when the session was
    saved."""
    _app()
    saving_panel = SampleImagePanel()
    path = _make_test_video(frame_count=8, size=(64, 48))
    assert saving_panel.load_video(path)
    saving_panel.seek_video_frame(5)
    state = saving_panel.get_session_state()

    loading_panel = SampleImagePanel()
    loading_panel.load_session_state(state)
    assert loading_panel._video_source is not None
    assert loading_panel._video_frame_index == 5
    assert loading_panel._image_array.shape[:2] == (48, 64)
    print("SampleImagePanel.load_session_state: reopens a video Sample "
          "via load_video and re-parks on the saved frame -- OK")


def test_video_sample_session_state_round_trips_through_json():
    """The end-to-end shape session_io.save_session/load_session actually
    exercise -- state must survive a real json.dumps/json.loads round
    trip (plain JSON-able types only), not just a same-process dict
    passthrough."""
    import json
    _app()
    saving_panel = SampleImagePanel()
    path = _make_test_video(frame_count=6, size=(64, 48))
    assert saving_panel.load_video(path)
    saving_panel.seek_video_frame(2)
    state = json.loads(json.dumps(saving_panel.get_session_state()))

    loading_panel = SampleImagePanel()
    loading_panel.load_session_state(state)
    assert loading_panel._video_source is not None
    assert loading_panel._video_frame_index == 2
    print("SampleImagePanel: a video Sample's session state survives a "
          "real JSON round trip and reopens correctly -- OK")


if __name__ == "__main__":
    test_sample_loads_at_native_resolution_not_a_capped_preview_size()
    test_isolated_single_edit_on_gpu_ineligible_stack_renders_at_full_resolution()
    test_gpu_ineligible_stack_burst_downreses_starting_on_the_second_tick()
    test_gpu_ineligible_stack_settling_after_a_burst_renders_full_resolution()
    test_gpu_eligible_stack_always_recomputes_at_scopes_resolution()
    test_gpu_eligible_stack_settle_does_not_trigger_a_redundant_rerender()
    test_isolated_edit_on_gpu_ineligible_stack_does_not_trigger_a_redundant_settle_rerender()
    test_empty_stack_previews_the_source_image_unchanged()
    test_gpu_eligible_stack_always_gets_full_resolution_even_mid_burst()
    test_downres_array_is_a_grow_only_decimation_of_the_working_array()
    test_scopes_array_is_capped_at_the_target_dimension()
    test_scopes_array_is_unchanged_for_a_small_source()
    test_compose_highlight_overlay_matte_one_is_unchanged()
    test_compose_highlight_overlay_matte_zero_is_solid_gray()
    test_compose_highlight_overlay_gray_is_flat_regardless_of_underlying_brightness()
    test_compose_highlight_overlay_blends_at_a_partial_matte()
    test_no_highlight_getter_leaves_the_after_source_unchanged()
    test_highlight_getter_composites_the_toggled_stages_matte()
    test_highlight_getter_for_a_stage_not_in_the_stack_falls_back_to_plain()
    test_pick_pixel_color_for_stage_reflects_this_stages_input_not_final_output()
    test_pick_pixel_color_for_stage_is_a_noop_without_a_loaded_image()
    test_pick_pixel_color_for_stage_is_a_noop_for_an_unresolved_stage_id()
    test_load_video_parks_on_the_first_frame_through_the_still_pipeline()
    test_seek_video_frame_updates_the_working_array()
    test_video_loaded_signal_reports_frame_count_and_rate()
    test_loading_a_still_image_closes_any_open_video_source()
    test_loading_a_new_video_closes_the_previous_one()
    test_seek_video_frame_is_a_noop_with_no_video_loaded()
    test_video_transport_bar_hidden_until_a_video_loads()
    test_video_transport_bar_hides_again_when_a_still_replaces_the_video()
    test_video_transport_slider_range_matches_frame_count()
    test_video_transport_timecode_label_updates_on_seek()
    test_video_transport_step_buttons_move_one_frame_and_clamp()
    test_video_transport_scrub_slider_drag_seeks_the_video()
    test_video_play_pause_button_toggles_playback_and_its_own_label()
    test_video_set_in_out_and_clear_buttons_drive_the_controller()
    test_video_loop_button_toggles_controller_loop_state()
    test_video_speed_combo_drives_the_controller_speed()
    test_video_home_end_shortcuts_jump_to_in_out_points()
    test_video_jump_to_out_uses_last_frame_when_out_point_unset()
    test_load_reference_from_array_loads_directly_into_reference_slot()
    test_render_full_resolution_after_for_capture_delegates_for_a_still()
    test_render_full_resolution_after_for_capture_redecodes_the_parked_video_frame()
    test_get_session_state_flags_a_video_sample_as_is_video()
    test_get_session_state_omits_is_video_for_a_still_sample()
    test_load_session_state_reopens_a_video_sample_through_load_video()
    test_video_sample_session_state_round_trips_through_json()
    print()
    print("All sample_image_panel tests passed.")
