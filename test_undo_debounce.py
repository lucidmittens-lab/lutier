"""Tests for MainWindow's undo-history debouncing -- run directly:
python3 test_undo_debounce.py

The bug this guards against: StackPanel.stack_changed fires on every
SINGLE TICK of a live drag (a Printer Lights/CDL slider, the fork-merge
blend slider, ...), not once per gesture. Pushing an undo step on every
tick meant one press of Undo only reverted a tiny fraction of a drag, and
the history list grew by dozens-to-hundreds of entries for a single
continuous edit. See main_window.py's _on_stack_changed_for_history/
_commit_pending_undo_push/_flush_pending_undo_push docstrings for the
fix (a short debounce timer) this file exercises directly, via
stack_changed.emit() -- no real slider widget needed, since the fix
lives entirely in how MainWindow reacts to that one signal.

Requires a real (if offscreen) QApplication -- same headless-Qt setup as
every other GUI-touching verification in this project (see RUNNING.md).
"""
import sys
import tempfile
from pathlib import Path

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import main_window as main_window_module
import workspace_io
import canvas as cv
from main_window import MainWindow

# Same redirect-for-the-duration-of-tests discipline test_gl_reparent_
# safety.py uses, for the identical reason: MainWindow.__init__/
# closeEvent both touch the real remembered-workspace file otherwise.
_workspace_tmp_dir = tempfile.TemporaryDirectory()
workspace_io.DEFAULT_WORKSPACE_DIR = Path(_workspace_tmp_dir.name) / "LUTier"
workspace_io.DEFAULT_WORKSPACE_PATH = workspace_io.DEFAULT_WORKSPACE_DIR / "workspace.json"


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_rapid_stack_changes_coalesce_into_one_debounced_push():
    _app()
    win = MainWindow()
    win.show()
    QApplication.processEvents()
    try:
        initial_len = len(win._undo_history)

        for _ in range(20):  # simulates ~20 ticks of one continuous slider drag
            win.stack_panel.stack_changed.emit()
        QApplication.processEvents()
        assert len(win._undo_history) == initial_len, (
            "must not push anything while the debounce window is still running"
        )

        QTest.qWait(main_window_module._UNDO_DEBOUNCE_MS + 150)
        assert len(win._undo_history) == initial_len + 1, (
            "20 rapid signals within the debounce window must coalesce into exactly one push"
        )
    finally:
        win.close()
    print("MainWindow: a burst of rapid stack_changed signals (one continuous "
          "drag) coalesces into exactly one undo push -- OK")


def test_undo_flushes_a_pending_debounced_push_first():
    _app()
    win = MainWindow()
    win.show()
    QApplication.processEvents()
    try:
        initial_len = len(win._undo_history)

        win.stack_panel.stack_changed.emit()  # arms the debounce timer
        assert win._undo_debounce_timer.isActive()

        win._undo()  # must flush the pending push before navigating

        assert not win._undo_debounce_timer.isActive()
        assert len(win._undo_history) == initial_len + 1, (
            "the pending edit must have been committed, not lost or left for later"
        )
    finally:
        win.close()
    print("MainWindow._undo: flushes a still-pending debounced push before "
          "navigating history, rather than losing it or committing it later "
          "at the wrong point -- OK")


def test_redo_flushes_a_pending_debounced_push_first():
    _app()
    win = MainWindow()
    win.show()
    QApplication.processEvents()
    try:
        win.stack_panel.stack_changed.emit()
        win._undo()  # flush -> 2 entries, pointer moved back to entry 0

        win.stack_panel.stack_changed.emit()  # a NEW pending edit, mid-history
        assert win._undo_debounce_timer.isActive()

        win._redo()  # must flush first, which also truncates the old redo tail

        assert not win._undo_debounce_timer.isActive()
    finally:
        win.close()
    print("MainWindow._redo: flushes a still-pending debounced push before "
          "navigating history -- OK")


def test_new_session_cancels_a_pending_debounced_push():
    """The pending push must not land on the FRESH history New Session
    creates -- see _new_session's own comment on why this cancels rather
    than flushes."""
    _app()
    win = MainWindow()
    win.show()
    QApplication.processEvents()
    try:
        win.stack_panel.stack_changed.emit()
        assert win._undo_debounce_timer.isActive()

        win._new_session()

        assert not win._undo_debounce_timer.isActive()
        assert len(win._undo_history) == 1, "New Session must start a fresh, single-entry history"
    finally:
        win.close()
    print("MainWindow._new_session: cancels a pending debounced push rather "
          "than letting it land on the fresh history -- OK")


def test_launch_and_new_session_start_without_an_image_and_unconfirmed_canvas():
    """Per Tom (2026-10-05): no bundled test image at launch or after New
    Session -- the New Project prompt sets the project up instead. Until
    it's confirmed, the first loaded media still seeds the canvas, and
    the project working space reaches the Stack panel."""
    _app()
    win = MainWindow()
    try:
        assert win.sample_image.current_path is None
        assert not win._canvas_confirmed
        win._set_project_canvas(cv.CanvasSettings(width=4096, height=2160,
                                                  working_colourspace="ARRI LogC4"))
        assert win.stack_panel._default_working_colourspace == "ARRI LogC4"
        win._on_sample_image_loaded(1920, 1080)
        assert win._canvas.width == 4096, "a confirmed project canvas must not be reseeded"

        assert win._new_session() is True
        assert win.sample_image.current_path is None
        assert win._canvas == cv.DEFAULT_CANVAS and not win._canvas_confirmed
    finally:
        win.close()
    print("MainWindow: launch/New Session start with no image and an "
          "unconfirmed default canvas -- OK")


def test_opening_a_session_saved_without_an_image_clears_the_viewer():
    """A session saved with no Source (the norm now that launch starts
    empty) must not leave the previous session's image in the Viewer."""
    _app()
    path = str(Path(_workspace_tmp_dir.name) / "no_image.ltr")
    win = MainWindow()
    try:
        win._write_session(path)
        win.sample_image.restore_default_image()
        assert win.sample_image.current_path is not None
        original = main_window_module.QFileDialog.getOpenFileName
        main_window_module.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (path, ""))
        try:
            win._open_session()
        finally:
            main_window_module.QFileDialog.getOpenFileName = original
        assert win.sample_image.current_path is None
    finally:
        win.close()
    print("MainWindow: opening a session saved without an image clears the "
          "Viewer -- OK")


if __name__ == "__main__":
    test_rapid_stack_changes_coalesce_into_one_debounced_push()
    test_undo_flushes_a_pending_debounced_push_first()
    test_redo_flushes_a_pending_debounced_push_first()
    test_new_session_cancels_a_pending_debounced_push()
    test_launch_and_new_session_start_without_an_image_and_unconfirmed_canvas()
    test_opening_a_session_saved_without_an_image_clears_the_viewer()
    print()
    print("All undo-debounce tests passed.")
