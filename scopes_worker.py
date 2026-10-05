"""
scopes_worker.py -- grades the Scopes' small sample OFF the UI thread
during playback (Tom, 2026-10-05: "make the performance feel more
slick").

Measured: grading the Scopes sample on the CPU every playback tick cost
~50 ms (CDL + Printer Lights + CAMier DRT at Scopes resolution) -- more
than a whole 24 fps frame, on the UI thread, so a DRT in the stack alone
held playback to ~14 fps. During playback the Scopes now update from
here instead: latest-wins (a newer frame replaces a queued one), at most
every MIN_INTERVAL_S, delivered back to the UI thread via a queued Qt
signal. Paused/parked previews still grade synchronously, as before.

Only used while the stack is locked for playback, so the Stack it's
handed can't change underneath the worker.
"""
import threading
import time

from PySide6.QtCore import QObject, Signal

import stack as st

MIN_INTERVAL_S = 1.0 / 6.0


class ScopesWorker(QObject):
    # (graded scopes array, token) -- emitted from the worker thread, so
    # receivers on the UI thread get it queued.
    result_ready = Signal(object, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cond = threading.Condition()
        self._job = None           # (stack, array, token) -- latest wins
        self._token = 0
        self._thread = threading.Thread(target=self._run, name="lutier-scopes", daemon=True)
        self._thread.start()

    @property
    def token(self):
        return self._token

    def submit(self, stack, array):
        """Grade `array` (already Scopes-sized) through `stack` soon;
        replaces any job not started yet."""
        with self._cond:
            self._job = (stack, array, self._token)
            self._cond.notify()

    def cancel(self):
        """Drop any queued job and make in-flight results stale -- call
        before a synchronous Scopes update (pause, seek) so an older
        playback frame can't land on top of it."""
        with self._cond:
            self._token += 1
            self._job = None

    def _run(self):
        last = 0.0
        while True:
            with self._cond:
                while self._job is None:
                    self._cond.wait()
                wait = MIN_INTERVAL_S - (time.perf_counter() - last)
                if wait > 0:
                    self._cond.wait(wait)  # a newer frame may replace the job meanwhile
                    if self._job is None:
                        continue
                stack, array, token = self._job
                self._job = None
            try:
                graded = st.apply_stack_to_image_array(stack, array) if stack is not None and stack.stages else array
            except Exception:  # noqa: BLE001 -- a Scopes miss must never take playback down
                continue
            last = time.perf_counter()
            self.result_ready.emit(graded, token)
