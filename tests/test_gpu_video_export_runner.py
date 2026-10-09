"""Tests for main_window._GpuVideoExportRunner -- run directly:
QT_QPA_PLATFORM=offscreen python3 -m tests.test_gpu_video_export_runner

Covers the runner's own pure-Python control flow (frame stepping,
cancel handling, resize dispatch, error handling, Viewer restoration)
against FAKE panel/compare/source/encoder stand-ins -- no real GL
context involved at all, so this can't verify the actual GPU rendering
is CORRECT (that needs real hardware -- see the scratchpad end-to-end
smoke tests run directly against a real MainWindow/WipeCompareWidget
during development, which caught and confirmed the fix for a real,
reproduced one-frame-lag bug in render_and_read_back_after_frame). This
file exists to guard the surrounding control flow against regressions,
same "test everything that doesn't need Qt/GL, document what does"
convention wipe_compare_widget.py's own test coverage (none, by design)
already follows.
"""
import sys

import numpy as np
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

import canvas as cv
import stack as st
from main_window import _GpuVideoExportRunner


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


class _FakeCompare:
    """Stands in for WipeCompareWidget -- no real GL, just records calls
    and hands back pre-baked "graded" frames, or None to simulate a
    readback failure."""

    def __init__(self, frames_to_return):
        self._frames_to_return = frames_to_return
        self.render_calls = 0
        self.set_after_calls = []

    def set_after(self, array, gpu_stack=None):
        self.set_after_calls.append((array, gpu_stack))

    def render_and_read_back_after_frame(self):
        frame = (self._frames_to_return[self.render_calls]
                  if self.render_calls < len(self._frames_to_return) else None)
        self.render_calls += 1
        return frame


class _FakePanel:
    def __init__(self, compare):
        self.compare = compare
        self.refresh_preview_calls = 0

    def refresh_preview(self):
        self.refresh_preview_calls += 1


class _FakeSource:
    """Stands in for video_source.VideoSource -- decodes from a plain
    in-memory list of arrays instead of a real file."""

    def __init__(self, frames, frame_count=None):
        self._frames = frames
        self.frame_count = frame_count if frame_count is not None else len(frames)
        self._next_index = 0

    def seek_to_frame(self, index):
        self._next_index = index + 1
        return self._frames[index] if index < len(self._frames) else None

    def decode_next_frame(self):
        if self._next_index >= len(self._frames):
            return None
        array = self._frames[self._next_index]
        self._next_index += 1
        return array


class _FakeEncoder:
    def __init__(self):
        self.written = []

    def write_frame(self, array):
        self.written.append(array)


def _run_until_done(runner):
    loop = QEventLoop()
    result = {}
    runner.finished.connect(lambda: (result.setdefault("outcome", "finished"), loop.quit()))
    runner.failed.connect(lambda msg: (result.update(outcome="failed", error=msg), loop.quit()))
    safety = QTimer()
    safety.setSingleShot(True)
    safety.timeout.connect(lambda: (result.setdefault("outcome", "timeout"), loop.quit()))
    safety.start(5000)
    runner.start()
    loop.exec()
    return result


def _make_frames(n, size=(8, 6)):
    h, w = size
    return [np.full((h, w, 3), i / max(1, n - 1), dtype=np.float32) for i in range(n)]


def test_runs_every_frame_and_writes_them_in_order():
    _app()
    frames = _make_frames(4, size=(6, 4))
    compare = _FakeCompare(frames_to_return=frames)
    panel = _FakePanel(compare)
    source = _FakeSource(frames)
    encoder = _FakeEncoder()
    canvas = cv.CanvasSettings(width=4, height=6)
    stack = st.Stack(stages=[st.make_cdl_stage(slope=(1.1, 1.0, 1.0))])

    runner = _GpuVideoExportRunner(panel, source, stack, encoder, canvas)
    result = _run_until_done(runner)

    assert result["outcome"] == "finished"
    assert len(encoder.written) == 4
    for i, written in enumerate(encoder.written):
        np.testing.assert_array_equal(written, frames[i])
    assert panel.refresh_preview_calls == 1, "Viewer must be restored exactly once when export finishes"
    print("_GpuVideoExportRunner: writes every frame in order, restores "
          "the Viewer once on completion -- OK")


def test_pushes_gpu_stack_on_every_set_after_call():
    _app()
    frames = _make_frames(3, size=(4, 4))
    compare = _FakeCompare(frames_to_return=frames)
    panel = _FakePanel(compare)
    source = _FakeSource(frames)
    encoder = _FakeEncoder()
    canvas = cv.CanvasSettings(width=4, height=4)
    stack = st.Stack(stages=[st.make_cdl_stage(slope=(1.2, 1.0, 1.0))])

    runner = _GpuVideoExportRunner(panel, source, stack, encoder, canvas)
    _run_until_done(runner)

    assert len(compare.set_after_calls) == 3
    for _array, gpu_stack in compare.set_after_calls:
        assert gpu_stack is stack
    print("_GpuVideoExportRunner: every set_after call carries the "
          "SAME stack instance for GPU grading -- OK")


def test_resizes_frames_that_do_not_match_canvas():
    _app()
    # Source frames are 8x6 (h,w) -- canvas wants 4x3.
    frames = _make_frames(2, size=(6, 8))
    compare = _FakeCompare(frames_to_return=[np.zeros((3, 4, 3), dtype=np.float32)] * 2)
    panel = _FakePanel(compare)
    source = _FakeSource(frames)
    encoder = _FakeEncoder()
    canvas = cv.CanvasSettings(width=4, height=3)
    stack = st.Stack(stages=[st.make_cdl_stage(slope=(1.05, 1.0, 1.0))])

    runner = _GpuVideoExportRunner(panel, source, stack, encoder, canvas)
    _run_until_done(runner)

    for array, _gpu_stack in compare.set_after_calls:
        assert array.shape[:2] == (3, 4), f"expected resized (3,4), got {array.shape[:2]}"
    print("_GpuVideoExportRunner: resizes frames that don't already "
          "match the canvas resolution before pushing them to the GPU "
          "-- OK")


def test_skips_resize_when_frame_already_matches_canvas():
    _app()
    frames = _make_frames(2, size=(6, 4))
    compare = _FakeCompare(frames_to_return=[np.zeros((6, 4, 3), dtype=np.float32)] * 2)
    panel = _FakePanel(compare)
    source = _FakeSource(frames)
    encoder = _FakeEncoder()
    canvas = cv.CanvasSettings(width=4, height=6)
    stack = st.Stack(stages=[st.make_cdl_stage(slope=(1.05, 1.0, 1.0))])

    runner = _GpuVideoExportRunner(panel, source, stack, encoder, canvas)
    _run_until_done(runner)

    # Identity check -- the exact same array objects should have been
    # pushed, not a resized copy.
    pushed_arrays = [array for array, _gpu_stack in compare.set_after_calls]
    assert pushed_arrays[0] is frames[0]
    assert pushed_arrays[1] is frames[1]
    print("_GpuVideoExportRunner: skips the resize step entirely when a "
          "frame already matches the canvas resolution -- OK")


def test_cancel_stops_early_and_still_finishes_cleanly():
    _app()
    frames = _make_frames(10, size=(4, 4))
    compare = _FakeCompare(frames_to_return=frames)
    panel = _FakePanel(compare)
    source = _FakeSource(frames)
    encoder = _FakeEncoder()
    canvas = cv.CanvasSettings(width=4, height=4)
    stack = st.Stack(stages=[st.make_cdl_stage(slope=(1.1, 1.0, 1.0))])

    runner = _GpuVideoExportRunner(panel, source, stack, encoder, canvas)
    cancelled_after = []

    def maybe_cancel(done, _total):
        cancelled_after.append(done)
        if done >= 3:
            runner.request_cancel()

    runner.progress.connect(maybe_cancel)
    result = _run_until_done(runner)

    assert result["outcome"] == "finished", "a cancel is a clean stop, not a failure"
    assert len(encoder.written) == 3
    assert panel.refresh_preview_calls == 1
    print("_GpuVideoExportRunner: request_cancel stops early, still "
          "emits finished (not failed), and restores the Viewer -- OK")


def test_readback_failure_emits_failed_and_restores_viewer():
    _app()
    frames = _make_frames(3, size=(4, 4))
    # Second readback returns None -- simulates render_and_read_back_
    # after_frame failing (e.g. Viewer not currently paintable).
    compare = _FakeCompare(frames_to_return=[frames[0], None, frames[2]])
    panel = _FakePanel(compare)
    source = _FakeSource(frames)
    encoder = _FakeEncoder()
    canvas = cv.CanvasSettings(width=4, height=4)
    stack = st.Stack(stages=[st.make_cdl_stage(slope=(1.1, 1.0, 1.0))])

    runner = _GpuVideoExportRunner(panel, source, stack, encoder, canvas)
    result = _run_until_done(runner)

    assert result["outcome"] == "failed"
    assert "readback" in result["error"].lower()
    assert len(encoder.written) == 1, "only the frame before the failure should have been written"
    assert panel.refresh_preview_calls == 1, "Viewer must still be restored on failure"
    print("_GpuVideoExportRunner: a GPU readback failure emits failed "
          "with a clear message and still restores the Viewer -- OK")


def test_end_of_source_stops_cleanly_even_if_frame_count_was_wrong():
    """source.frame_count says 10 but decode_next_frame runs dry at 3 --
    same "trust the decoder, not the metadata" tolerance video_source.py
    itself documents for frame_count estimates."""
    _app()
    frames = _make_frames(3, size=(4, 4))
    compare = _FakeCompare(frames_to_return=frames)
    panel = _FakePanel(compare)
    source = _FakeSource(frames, frame_count=10)
    encoder = _FakeEncoder()
    canvas = cv.CanvasSettings(width=4, height=4)
    stack = st.Stack(stages=[st.make_cdl_stage(slope=(1.1, 1.0, 1.0))])

    runner = _GpuVideoExportRunner(panel, source, stack, encoder, canvas)
    result = _run_until_done(runner)

    assert result["outcome"] == "finished"
    assert len(encoder.written) == 3
    print("_GpuVideoExportRunner: stops cleanly once the source runs "
          "dry, even if frame_count over-promised -- OK")


if __name__ == "__main__":
    test_runs_every_frame_and_writes_them_in_order()
    test_pushes_gpu_stack_on_every_set_after_call()
    test_resizes_frames_that_do_not_match_canvas()
    test_skips_resize_when_frame_already_matches_canvas()
    test_cancel_stops_early_and_still_finishes_cleanly()
    test_readback_failure_emits_failed_and_restores_viewer()
    test_end_of_source_stops_cleanly_even_if_frame_count_was_wrong()
    print()
    print("All _GpuVideoExportRunner tests passed.")
