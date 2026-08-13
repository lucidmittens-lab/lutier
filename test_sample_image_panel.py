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
from sample_image_panel import SampleImagePanel, WIPE_SOURCE_SAMPLE_BEFORE, WIPE_SOURCE_SAMPLE_AFTER
from stack import Stack, make_cdl_stage, make_lut_stage


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
    _app()
    panel = SampleImagePanel()
    path = _make_test_video(frame_count=10, size=(64, 48))
    assert panel.load_video(path)
    assert panel._video_timecode_label.text() == "1 / 10"
    panel.seek_video_frame(4)
    assert panel._video_timecode_label.text() == "5 / 10"
    print("SampleImagePanel: timecode label reflects the current frame "
          "after a seek -- OK")


def test_video_transport_step_buttons_move_one_frame_and_clamp():
    _app()
    panel = SampleImagePanel()
    path = _make_test_video(frame_count=5, size=(64, 48))
    assert panel.load_video(path)

    panel._on_video_step_forward_clicked()
    assert panel._video_frame_index == 1
    panel._on_video_step_back_clicked()
    assert panel._video_frame_index == 0
    panel._on_video_step_back_clicked()  # already at 0 -- must clamp, not go negative
    assert panel._video_frame_index == 0

    for _ in range(10):
        panel._on_video_step_forward_clicked()
    assert panel._video_frame_index == 4, "must clamp at frame_count-1, not overshoot"
    print("SampleImagePanel: step-back/step-forward move one frame and "
          "clamp at both ends -- OK")


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


def test_video_play_pause_button_toggles_playback_and_its_own_label():
    _app()
    panel = SampleImagePanel()
    path = _make_test_video(frame_count=10, size=(64, 48))
    assert panel.load_video(path)
    assert panel._video_play_button.text() == "▶"

    panel._on_video_play_pause_clicked()
    assert panel._playback_controller.is_playing
    assert panel._video_play_button.text() == "⏸"

    panel._on_video_play_pause_clicked()
    assert not panel._playback_controller.is_playing
    assert panel._video_play_button.text() == "▶"
    print("SampleImagePanel: Play/Pause button toggles playback and its "
          "own label -- OK")


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
    assert panel._reference_source_colourspace is None
    assert panel._reference_adapt_enabled is False
    print("SampleImagePanel.load_reference_from_array: loads an "
          "already-decoded array directly into the Reference slot, "
          "always Unmanaged -- OK")


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
    test_gpu_eligible_stack_always_gets_full_resolution_even_mid_burst()
    test_downres_array_is_a_grow_only_decimation_of_the_working_array()
    test_scopes_array_is_capped_at_the_target_dimension()
    test_scopes_array_is_unchanged_for_a_small_source()
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
    test_load_reference_from_array_loads_directly_into_reference_slot()
    test_render_full_resolution_after_for_capture_delegates_for_a_still()
    test_render_full_resolution_after_for_capture_redecodes_the_parked_video_frame()
    test_get_session_state_flags_a_video_sample_as_is_video()
    test_get_session_state_omits_is_video_for_a_still_sample()
    test_load_session_state_reopens_a_video_sample_through_load_video()
    test_video_sample_session_state_round_trips_through_json()
    print()
    print("All sample_image_panel tests passed.")
