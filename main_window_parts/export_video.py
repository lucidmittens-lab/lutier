"""main_window_parts/export_video.py -- the Export Video dialog and its two
render paths: the CPU worker thread (_VideoExportWorker) and the GPU runner
(_GpuVideoExportRunner). A mixin: MainWindow inherits it, so these methods
share the window's own `self`.
"""
from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal
import image_io
import video_export
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout,
    QComboBox, QFileDialog, QMessageBox, QLabel, QDialog, QDialogButtonBox, QPushButton, QProgressDialog,
)
import canvas as cv
import video_source
import gpu_stack_renderer
from floating_dialog import make_floating_tool_window, show_floating


class _VideoExportWorker(QObject):
    """Runs video_export.export_video_cpu on a background QThread -- the
    FIRST use of a real background thread in this codebase (every prior
    long operation, including LUT baking, has run synchronously on the
    UI thread). Owns its OWN VideoSource (opened fresh on the export's
    own file path, NOT the Viewer's live self.sample_image._video_source
    -- see MainWindow._start_video_export's own comment on why: PyAV
    container/decoder objects aren't documented as thread-safe, so
    sharing the Viewer's own live source between this background thread
    and any live scrub/playback on the UI thread would be a real data
    race, not a hypothetical one).

    `progress`/`finished`/`failed` are the only things this worker
    touches on the Qt side -- everything else (video_export.py,
    stack.apply_stack_to_image_array) is pure Qt-free work, safe to run
    off the UI thread."""

    progress = Signal(int, int)
    finished = Signal()
    failed = Signal(str)

    def __init__(self, source, stack, encoder, canvas):
        super().__init__()
        self._source = source
        self._stack = stack
        self._encoder = encoder
        self._canvas = canvas
        self._cancel_requested = False

    def request_cancel(self):
        """Safe to call from the UI thread -- just flips a plain bool
        flag video_export.export_video_cpu's own should_cancel callback
        polls between frames, no Qt cross-thread signal machinery needed
        for a single flag like this."""
        self._cancel_requested = True

    def run(self):
        try:
            video_export.export_video_cpu(
                self._source, self._stack, self._encoder, self._canvas,
                progress_callback=lambda done, total: self.progress.emit(done, total),
                should_cancel=lambda: self._cancel_requested,
            )
        except Exception as e:  # noqa: BLE001 -- surface any failure back to the UI thread
            self.failed.emit(str(e))
            return
        self.finished.emit()


class _GpuVideoExportRunner(QObject):
    """Drives GPU-accelerated video export -- the fast path video_export.
    py's own module docstring already anticipated ("the GPU path instead
    drives VideoEncoder.write_frame directly from a UI-thread-chunked
    loop, bypassing export_video_cpu entirely since GPU-graded frames
    never go through stack.apply_stack_to_image_array at all"), deferred
    at the time and built here.

    ALWAYS runs on the UI THREAD -- unlike _VideoExportWorker's CPU path,
    this can NEVER move to a background QThread: it drives the live
    Viewer's own WipeCompareWidget (`panel.compare`) through set_after +
    render_and_read_back_after_frame (see that method's own docstring on
    why a plain repaint()+read_back_after_frame() -- casting_controller.
    py's own pattern for a DIFFERENT purpose -- doesn't work for THIS
    back-to-back-ticks use, and produced a real, reproduced one-frame-
    lag bug here before that method existed). A QOpenGLWidget's GL
    context is only ever safe to touch from the UI thread it lives on --
    reusing that already-proven surface instead of standing up a
    brand-new offscreen
    QOffscreenSurface/QOpenGLContext (never attempted anywhere in this
    app) is a deliberate risk-reduction choice, not an oversight.

    VISIBLE SIDE EFFECT, by design: driving the Viewer this way means the
    user sees each export frame flash through their own Viewer panel
    while a GPU export runs, the same "the Viewer IS the shared
    rendering surface" model realtime playback (video_playback_
    controller.py) already uses, not a separate hidden render target.
    _restore_viewer() re-parks the Viewer back to whatever it was
    actually showing once export finishes/fails/is cancelled, same
    "always clean up, even on the unhappy path" discipline
    VideoPlaybackController.pause() already follows.

    CONCURRENT-EDIT HAZARD: nothing here stops some OTHER code path from
    also calling panel.compare.set_after() while this runner is mid-
    export (a Stack edit would do exactly that via refresh_preview) --
    that would silently corrupt in-flight frames. Deliberately NOT solved
    with a new lock/flag here: MainWindow._start_video_export_gpu reuses
    the exact same WindowModal QProgressDialog the CPU path already sets
    up, which blocks user input to the whole MainWindow for the export's
    duration -- since a Stack edit can only ever originate from user
    input in this app, that already closes the hole with an existing,
    already-tested mechanism rather than a new one.

    CHUNKED via a zero-delay QTimer (one frame per tick) rather than one
    tight synchronous loop over the whole clip -- keeps the UI thread's
    own event loop pumping BETWEEN frames, so the progress dialog
    actually repaints and its Cancel button stays responsive throughout,
    without needing a second thread at all."""

    progress = Signal(int, int)
    finished = Signal()
    failed = Signal(str)

    def __init__(self, panel, source, stack, encoder, canvas, parent=None):
        super().__init__(parent)
        self._panel = panel
        self._source = source
        self._stack = stack
        self._encoder = encoder
        self._canvas = canvas
        self._frame_index = 0
        self._total = source.frame_count
        self._cancel_requested = False
        self._timer = QTimer(self)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._tick)

    def start(self):
        self._timer.start()

    def request_cancel(self):
        """Safe to call directly -- everything here already lives on the
        UI thread, no cross-thread flag-passing needed the way
        _VideoExportWorker.request_cancel's own docstring explains for
        its background-QThread case."""
        self._cancel_requested = True

    def _stop(self):
        self._timer.stop()
        self._restore_viewer()

    def _restore_viewer(self):
        """Re-parks the Viewer on whatever it was ACTUALLY showing before
        this export hijacked it -- mirrors VideoPlaybackController.
        pause()'s own "always re-park on the way out" discipline.
        refresh_preview() re-runs the normal Sample-After pipeline from
        the panel's own still-intact underlying state (this runner only
        ever pushed frames into panel.compare directly, it never touched
        SampleImagePanel's own tracked Sample/Reference state at all) --
        exactly the same "something external touched the shared
        rendering surface, please redraw yourself accurately" contract
        StackPanel.stack_changed already relies on this method for."""
        self._panel.refresh_preview()

    def _tick(self):
        if self._cancel_requested or self._frame_index >= self._total:
            self._stop()
            self.finished.emit()
            return
        try:
            array = (self._source.seek_to_frame(0) if self._frame_index == 0
                     else self._source.decode_next_frame())
            if array is None:
                self._stop()
                self.finished.emit()
                return
            if array.shape[1] != self._canvas.width or array.shape[0] != self._canvas.height:
                array = image_io.resize_array(array, self._canvas.width, self._canvas.height)
            self._panel.compare.set_after(array, gpu_stack=self._stack)
            # render_and_read_back_after_frame, NOT repaint()+
            # read_back_after_frame -- see that method's own docstring
            # for the real, reproduced bug this replaced (repaint()
            # doesn't reliably guarantee paintGL has actually finished
            # by the time it returns under this runner's own back-to-
            # back-ticks-with-no-idle-time usage pattern, which made
            # every exported frame show the PREVIOUS frame's grade).
            graded = self._panel.compare.render_and_read_back_after_frame()
            if graded is None:
                raise RuntimeError(
                    "GPU readback failed -- the Viewer panel isn't currently "
                    "paintable (hidden, minimized, or not yet shown once). "
                    "Make sure the Viewer panel is visible and try again.")
            self._encoder.write_frame(graded)
        except Exception as e:  # noqa: BLE001 -- surface any failure, stop cleanly
            self._stop()
            self.failed.emit(str(e))
            return
        self._frame_index += 1
        self.progress.emit(self._frame_index, self._total)


class VideoExportMixin:
    def _show_export_video_dialog(self):
        """File > Export Video... (per Tom, 2026-08-13) -- same lazy-
        built-cached-QDialog shape as _show_export_dialog/
        _show_canvas_settings_dialog. Requires a video currently loaded
        in the Viewer (self.sample_image._video_source) -- guarded here
        rather than disabling the menu action itself, matching this
        menu's existing "always visible, guard at the point of use"
        convention (see _show_canvas_settings_dialog's own lack of a
        disabled-state check for comparison).

        CPU-path export only for this pass (Tom's approved plan's own
        GPU-eligible-stack fast path is a deliberate, documented follow-
        up, not built here) -- video_export.export_video_cpu (via
        stack.apply_stack_to_image_array) grades ANY stack correctly
        regardless of GPU eligibility; that check only ever mattered for
        LIVE PREVIEW performance, never export correctness, so every
        stack exports through the exact same, already-verified path."""
        if self.sample_image._video_source is None:
            QMessageBox.information(
                self, "No video loaded",
                "Load a video in the Viewer first (File > Load Video... "
                "or the Viewer's own ☰ menu).")
            return

        if getattr(self, "_export_video_dialog", None) is None:
            dialog = QDialog(self)
            make_floating_tool_window(dialog)
            dialog.setWindowTitle("Export Video")

            outer = QVBoxLayout(dialog)

            path_row = QHBoxLayout()
            path_row.addWidget(QLabel("Output File"))
            self.export_video_path_label = QLabel("(not set)")
            self.export_video_path_label.setWordWrap(True)
            path_row.addWidget(self.export_video_path_label, stretch=1)
            browse_button = QPushButton("Browse...")
            browse_button.clicked.connect(self._on_export_video_browse_clicked)
            path_row.addWidget(browse_button)
            outer.addLayout(path_row)

            codec_row = QHBoxLayout()
            codec_row.addWidget(QLabel("Codec"))
            self.export_video_codec_combo = QComboBox()
            for preset in video_export.available_codec_presets():
                self.export_video_codec_combo.addItem(preset["label"], preset["label"])
            self.export_video_codec_combo.currentIndexChanged.connect(
                self._on_export_video_codec_changed)
            codec_row.addWidget(self.export_video_codec_combo, stretch=1)
            outer.addLayout(codec_row)

            depth_row = QHBoxLayout()
            depth_row.addWidget(QLabel("Bit Depth"))
            self.export_video_bit_depth_combo = QComboBox()
            depth_row.addWidget(self.export_video_bit_depth_combo, stretch=1)
            outer.addLayout(depth_row)

            button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
            export_button = button_box.addButton(
                "Export...", QDialogButtonBox.ButtonRole.AcceptRole)
            export_button.clicked.connect(self._start_video_export)
            button_box.rejected.connect(dialog.close)
            outer.addWidget(button_box)

            self._export_video_dialog = dialog
            self._export_video_output_path = None
            self._on_export_video_codec_changed()  # populate the bit-depth combo initially

        show_floating(self._export_video_dialog)

    def _selected_export_video_codec_preset(self):
        label = self.export_video_codec_combo.currentData()
        return video_export.get_codec_preset(label) if label is not None else None

    def _on_export_video_browse_clicked(self):
        preset = self._selected_export_video_codec_preset()
        default_ext = preset["extension"] if preset else ".mov"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Video", f"export{default_ext}",
            f"Video (*{default_ext})",
        )
        if path:
            self._export_video_output_path = path
            self.export_video_path_label.setText(path)

    def _on_export_video_codec_changed(self, _index=None):
        """Repopulates the Bit Depth combo to whatever THIS codec preset
        can actually carry (see video_export.CODEC_PRESETS' own
        max_bit_depth) -- "32f" is never offered at all, no codec this
        app offers can carry float samples (see video_export.py's own
        docstring)."""
        preset = self._selected_export_video_codec_preset()
        self.export_video_bit_depth_combo.clear()
        if preset is None:
            return
        for depth in cv.BIT_DEPTHS:
            if depth == "32f" or depth > preset["max_bit_depth"]:
                continue
            self.export_video_bit_depth_combo.addItem(cv.BIT_DEPTH_LABELS[depth], depth)
        # Default to the canvas's own bit depth if this codec can carry
        # it, otherwise the highest depth it CAN carry.
        idx = self.export_video_bit_depth_combo.findData(self._canvas.bit_depth)
        if idx < 0:
            idx = self.export_video_bit_depth_combo.count() - 1
        if idx >= 0:
            self.export_video_bit_depth_combo.setCurrentIndex(idx)

    def _start_video_export(self):
        if self._export_video_output_path is None:
            QMessageBox.warning(self, "No output file", "Choose an output file first.")
            return
        preset = self._selected_export_video_codec_preset()
        bit_depth = self.export_video_bit_depth_combo.currentData()
        if preset is None or bit_depth is None:
            return

        live_source = self.sample_image._video_source
        if live_source is None:
            QMessageBox.warning(self, "No video loaded", "Load a video in the Viewer first.")
            return

        # A FRESH VideoSource on the same file, independent of the
        # Viewer's own live self.sample_image._video_source -- see
        # _VideoExportWorker's own docstring on why: this decodes on a
        # background thread for the whole export's duration, and PyAV
        # container/decoder objects aren't documented thread-safe, so
        # sharing the Viewer's live source with any scrub/playback the
        # user does DURING export would be a real data race.
        try:
            export_source = video_source.VideoSource(live_source.path)
        except Exception as e:  # noqa: BLE001 -- surface any open failure to the user
            QMessageBox.critical(self, "Failed to start export", str(e))
            return

        canvas = self._canvas
        try:
            encoder = video_export.VideoEncoder(
                self._export_video_output_path, canvas.width, canvas.height,
                export_source.frame_rate, preset, bit_depth=bit_depth,
                pixel_aspect_ratio=canvas.pixel_aspect_ratio)
        except Exception as e:  # noqa: BLE001 -- surface any encoder-open failure to the user
            export_source.close()
            QMessageBox.critical(self, "Failed to start export", str(e))
            return

        stack = self.stack_panel.get_stack()
        self._export_video_dialog.close()

        # Held on self, not local variables closed over by a lambda --
        # see _video_export_cleanup's own docstring on why every one of
        # this export's signal connections below goes to a real BOUND
        # METHOD of self (a QObject whose thread affinity is the UI
        # thread) rather than a plain lambda/closure.
        self._video_export_encoder = encoder
        self._video_export_source = export_source
        self._video_export_output_path_pending = self._export_video_output_path

        self._video_export_progress = QProgressDialog(
            "Exporting video...", "Cancel", 0, max(1, export_source.frame_count), self)
        self._video_export_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._video_export_progress.setMinimumDuration(0)
        self._video_export_progress.setValue(0)

        # GPU-eligible stack -- see _GpuVideoExportRunner's own docstring
        # for why this branches to an entirely different (UI-thread,
        # no QThread) execution shape rather than reusing
        # _VideoExportWorker with a GPU flag.
        if gpu_stack_renderer.is_stack_gpu_eligible(stack):
            self._start_video_export_gpu(export_source, stack, encoder, canvas)
        else:
            self._start_video_export_cpu(export_source, stack, encoder, canvas)

    def _start_video_export_cpu(self, export_source, stack, encoder, canvas):
        self._video_export_thread = QThread(self)
        self._video_export_worker = _VideoExportWorker(export_source, stack, encoder, canvas)
        self._video_export_worker.moveToThread(self._video_export_thread)
        self._video_export_thread.started.connect(self._video_export_worker.run)
        # Bound methods of THIS QObject (self), not lambdas -- Qt's
        # auto-connection can only recognize a signal/slot pair as
        # cross-thread (and therefore correctly QUEUE delivery onto the
        # receiver's own thread) when it can determine the RECEIVER's
        # thread affinity from the connection itself. A plain Python
        # lambda has no QObject/thread affinity of its own, so PySide6
        # can't reliably queue it -- confirmed directly (a real bug,
        # caught by an end-to-end smoke test the CPU-path unit tests
        # never would have caught, since those never actually ran the
        # worker on a real second QThread): connecting worker.finished/
        # worker.progress to plain lambdas produced "QObject::killTimer:
        # Timers cannot be stopped from another thread" and
        # "QWidget::repaint: Recursive repaint detected" warnings, i.e.
        # the "cleanup" work was runing on the WORKER thread instead of
        # being safely queued back onto the UI thread.
        self._video_export_worker.progress.connect(self._on_video_export_progress)
        self._video_export_worker.finished.connect(self._on_video_export_finished)
        self._video_export_worker.failed.connect(self._on_video_export_failed)
        self._video_export_progress.canceled.connect(self._video_export_worker.request_cancel)

        self._video_export_thread.start()

    def _start_video_export_gpu(self, export_source, stack, encoder, canvas):
        """GPU-eligible stack -- see _GpuVideoExportRunner's own
        docstring for the full reasoning (UI-thread only, drives the
        live Viewer directly). The WindowModal progress dialog set up by
        the caller does double duty here: besides showing progress, it
        blocks user input to this MainWindow for the export's whole
        duration, which is what stops a concurrent Stack edit from
        hijacking the Viewer's shared rendering surface mid-export (see
        the runner's own docstring on that hazard)."""
        self._video_export_gpu_runner = _GpuVideoExportRunner(
            self.sample_image, export_source, stack, encoder, canvas, parent=self)
        self._video_export_gpu_runner.progress.connect(self._on_video_export_progress)
        self._video_export_gpu_runner.finished.connect(self._on_video_export_finished)
        self._video_export_gpu_runner.failed.connect(self._on_video_export_failed)
        self._video_export_progress.canceled.connect(self._video_export_gpu_runner.request_cancel)

        self._video_export_gpu_runner.start()

    def _on_video_export_progress(self, done, _total):
        self._video_export_progress.setValue(done)

    def _on_video_export_finished(self):
        self._video_export_cleanup(success=True)

    def _on_video_export_failed(self, error_message):
        self._video_export_cleanup(success=False, error_message=error_message)

    def _video_export_cleanup(self, success, error_message=None):
        """The ONE place every video export -- successful, failed, or
        cancelled -- ends up, always called via a real bound-method
        signal connection (see _start_video_export's own comment on why
        that matters for correct cross-thread delivery) so this always
        actually runs on the UI thread, safe to touch self._video_export_
        progress/QMessageBox/etc. directly. Handles BOTH the CPU
        (background QThread) and GPU (UI-thread runner) shapes -- exactly
        one of self._video_export_thread/self._video_export_gpu_runner is
        set for any given export (see __init__'s own comment), so only
        that one's teardown actually runs; the GPU runner has already
        stopped its own QTimer and re-parked the Viewer (see
        _GpuVideoExportRunner._stop) by the time finished/failed fires,
        so there's nothing further to do for it here beyond dropping the
        reference below."""
        self._video_export_encoder.close()
        self._video_export_source.close()
        if self._video_export_thread is not None:
            self._video_export_thread.quit()
            self._video_export_thread.wait()
        self._video_export_progress.close()
        if error_message:
            QMessageBox.critical(self, "Video export failed", error_message)
        elif success:
            QMessageBox.information(
                self, "Export complete", f"Exported to:\n{self._video_export_output_path_pending}")
        # Drop the strong references -- PySide6 doesn't keep a
        # QThread/QObject alive on its own once _start_video_export
        # returns, only Qt parent-child ownership (the thread has `self`
        # as parent, the worker doesn't) or an explicit reference like
        # this does, so these had to be held on self for the export's
        # whole duration.
        self._video_export_thread = None
        self._video_export_worker = None
        self._video_export_gpu_runner = None
        self._video_export_encoder = None
        self._video_export_source = None
        self._video_export_progress = None
