"""
render_queue_panel.py -- the "Render Queue" panel (2026-10-09): jobs added
from Render Settings, rendered one after another on a background thread.
Part of the Render suggested layout.

Each job (render_job.RenderJob) is a card: name, status, what it makes
(files, frames, size, format), a progress bar and where it writes. Render
All renders every queued job in order; Stop cancels the one rendering
(what it wrote so far is kept). Failed and cancelled jobs can be queued
again; finished ones cleared. The queue lives for the session only.

Rendering uses render_job.run_job on a QThread, the same "own
VideoSource, signals back to bound methods" shape as Export Video's
_VideoExportWorker (see main_window_parts/export_video.py on why bound
methods, not lambdas). On the GPU when the job prefers it and GL works:
the worker makes its own gpu_frame_renderer context, current on the
render thread (the offscreen surface is made here, on the GUI thread);
a stack the GPU can't run, or no GL, renders on the CPU. Each card says
which ran. Call shutdown() before the window closes.
"""
import os

from PySide6.QtCore import QObject, QSize, QThread, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu,
    QProgressBar, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

import gpu_frame_renderer as gfr
import render_job as rj


class _RenderWorker(QObject):
    progress = Signal(int, int, int)   # job id, frames done, frames total
    finished = Signal(int)              # job id (status is on the job)

    def __init__(self, job, surface):
        super().__init__()
        self._job = job
        self._surface = surface
        self._cancel = False

    def request_cancel(self):
        self._cancel = True

    def run(self):
        gpu = None
        if self._job.prefer_gpu and self._surface is not None:
            try:
                gpu = gfr.GpuFrameRenderer(self._surface)  # current on this thread
            except RuntimeError:
                gpu = None  # no usable GL: the CPU renders it
        try:
            rj.run_job(self._job,
                       progress=lambda done, total: self.progress.emit(self._job.id, done, total),
                       should_cancel=lambda: self._cancel, gpu=gpu)
        finally:
            if gpu is not None:
                gpu.close()
        self.finished.emit(self._job.id)


class _ElidedLabel(QLabel):
    """A one-line label that never asks for more width than it gets: the
    text is elided in the middle (paths keep their start and file name)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.setFont(self.font())
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideMiddle, self.width())
        painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text)


class _JobCard(QWidget):
    def __init__(self, job, parent=None):
        super().__init__(parent)
        self.setObjectName("renderJobCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)
        top = QHBoxLayout()
        self.name_label = _ElidedLabel()
        self.name_label.setObjectName("renderJobName")
        self.status_label = QLabel()
        self.status_label.setObjectName("renderJobStatus")
        top.addWidget(self.name_label, stretch=1)
        top.addWidget(self.status_label)
        layout.addLayout(top)
        self.summary_label = _ElidedLabel()
        self.summary_label.setObjectName("metadataHint")
        layout.addWidget(self.summary_label)
        bar_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("renderProgress")
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(4)
        self.count_label = QLabel()
        self.count_label.setObjectName("metadataValueMono")
        bar_row.addWidget(self.progress_bar, stretch=1)
        bar_row.addWidget(self.count_label)
        layout.addLayout(bar_row)
        self.path_label = _ElidedLabel()
        self.path_label.setObjectName("renderJobPath")
        layout.addWidget(self.path_label)
        self.update_from(job)

    def update_from(self, job):
        self.name_label.setText(job.name)
        self.status_label.setText(f"{job.status} · {job.engine}" if job.engine else job.status)
        self.status_label.setProperty("state", job.status.lower())
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.status_label.setToolTip(job.error)
        self.summary_label.setText(job.summary())
        total = max(1, job.frames_total)
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(min(job.frames_done, total))
        self.count_label.setText(f"{job.frames_done} / {job.frames_total}")
        outputs = job.output_paths()
        where = os.path.dirname(outputs[0]) if len(outputs) > 1 else (outputs[0] if outputs else "")
        self.path_label.setText(where)
        self.path_label.setToolTip("\n".join(outputs) + (f"\n\n{job.error}" if job.error else ""))


class RenderQueuePanel(QWidget):
    """The queue's UI and its runner."""

    queue_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._jobs = []
        self._cards = {}
        self._rendering_all = False
        self._thread = None
        self._worker = None
        self._current = None
        self._surface = None   # the GPU renderer's offscreen surface (GUI thread)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        buttons = QHBoxLayout()
        self.render_all_button = QPushButton("Render All")
        self.render_all_button.setObjectName("primaryButton")
        self.render_all_button.clicked.connect(self.render_all)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop)
        buttons.addWidget(self.render_all_button)
        buttons.addWidget(self.stop_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.list = QListWidget()
        self.list.setObjectName("renderQueueList")
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.list, stretch=1)

        self.hint_label = QLabel("Add jobs from Render Settings.")
        self.hint_label.setObjectName("metadataHint")
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)
        self.status_label = QLabel()
        self.status_label.setObjectName("panelStatusLabel")
        layout.addWidget(self.status_label)
        self._refresh_state()

    # ---- the queue ----------------------------------------------------

    def jobs(self):
        return list(self._jobs)

    def add_job(self, job):
        self._jobs.append(job)
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, job.id)
        card = _JobCard(job)
        # Width 0: the card takes the list's width (and its labels elide)
        # rather than widening the list.
        item.setSizeHint(QSize(0, card.sizeHint().height()))
        self.list.addItem(item)
        self.list.setItemWidget(item, card)
        self._cards[job.id] = card
        self._refresh_state()

    def remove_jobs(self, jobs):
        """Removes `jobs` (never the one rendering)."""
        for job in jobs:
            if job is self._current or job not in self._jobs:
                continue
            row = self._jobs.index(job)
            self._jobs.pop(row)
            self.list.takeItem(row)
            self._cards.pop(job.id, None)
        self._refresh_state()

    def clear_finished(self):
        self.remove_jobs([job for job in self._jobs if job.status == rj.DONE])

    def requeue(self, jobs):
        for job in jobs:
            if job.status in (rj.FAILED, rj.CANCELLED):
                job.status, job.frames_done, job.error = rj.QUEUED, 0, ""
                self._cards[job.id].update_from(job)
        self._refresh_state()

    def selected_jobs(self):
        rows = sorted(index.row() for index in self.list.selectedIndexes())
        return [self._jobs[row] for row in rows]

    # ---- rendering ----------------------------------------------------

    def is_rendering(self):
        return self._current is not None

    def render_all(self):
        self._rendering_all = True
        self._start_next()

    def stop(self):
        """Cancel the job rendering now, and don't start the rest."""
        self._rendering_all = False
        if self._worker is not None:
            self._worker.request_cancel()
        self._refresh_state()

    def shutdown(self):
        """Stop and wait for the render thread (call before closing)."""
        self.stop()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait()

    def wait_until_idle(self, timeout_ms=60000):
        """For tests: block (processing events) until no job is rendering."""
        from PySide6.QtCore import QDeadlineTimer, QEventLoop
        from PySide6.QtWidgets import QApplication
        deadline = QDeadlineTimer(timeout_ms)
        while self.is_rendering() and not deadline.hasExpired():
            QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        return not self.is_rendering()

    def _start_next(self):
        if self._current is not None or not self._rendering_all:
            self._refresh_state()
            return
        job = next((j for j in self._jobs if j.status == rj.QUEUED), None)
        if job is None:
            self._rendering_all = False
            self._refresh_state()
            return
        self._current = job
        job.status = rj.RENDERING
        self._cards[job.id].update_from(job)
        if job.prefer_gpu and self._surface is None and gfr.gpu_rendering_possible():
            self._surface = gfr.make_offscreen_surface()
        self._thread = QThread(self)
        self._worker = _RenderWorker(job, self._surface)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._thread.start()
        self._refresh_state()

    def _on_progress(self, job_id, _done, _total):
        card = self._cards.get(job_id)
        if card is not None and self._current is not None:
            card.update_from(self._current)

    def _on_finished(self, job_id):
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        self._worker = None
        job, self._current = self._current, None
        card = self._cards.get(job_id)
        if card is not None and job is not None:
            card.update_from(job)
        self._start_next()

    # ---- UI -----------------------------------------------------------

    def _refresh_state(self):
        queued = [j for j in self._jobs if j.status == rj.QUEUED]
        self.render_all_button.setEnabled(bool(queued) and not self.is_rendering())
        self.stop_button.setEnabled(self.is_rendering())
        self.hint_label.setVisible(not self._jobs)
        if self._current is not None:
            text = f"Rendering {self._current.name}"
            if queued:
                text += f" · {len(queued)} more queued"
        elif queued:
            frames = sum(j.frames_total for j in queued)
            text = f"{len(queued)} queued · {frames} frames"
        elif self._jobs:
            text = f"{sum(j.status == rj.DONE for j in self._jobs)} of {len(self._jobs)} done"
        else:
            text = ""
        self.status_label.setText(text)
        self.queue_changed.emit()

    def _show_context_menu(self, pos):
        jobs = self.selected_jobs()
        menu = QMenu(self)
        if jobs:
            outputs = jobs[0].output_paths()
            if outputs:
                folder = os.path.dirname(outputs[0])
                reveal = menu.addAction("Show in File Manager")
                reveal.setEnabled(os.path.isdir(folder))
                reveal.triggered.connect(
                    lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(folder)))
            again = menu.addAction("Queue Again")
            again.setEnabled(any(j.status in (rj.FAILED, rj.CANCELLED) for j in jobs))
            again.triggered.connect(lambda: self.requeue(jobs))
            remove = menu.addAction("Remove")
            remove.setEnabled(any(j is not self._current for j in jobs))
            remove.triggered.connect(lambda: self.remove_jobs(jobs))
            menu.addSeparator()
        clear = menu.addAction("Clear Finished")
        clear.setEnabled(any(j.status == rj.DONE for j in self._jobs))
        clear.triggered.connect(self.clear_finished)
        menu.exec(self.list.viewport().mapToGlobal(pos))
