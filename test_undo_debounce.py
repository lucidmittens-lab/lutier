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
import numpy as np
import stack as st
from media_library import MediaEntry
from test_media_probe import _write_prores_mov
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


def test_graph_preview_bakes_only_while_visible_at_the_fixed_preview_grid():
    """Stack edits don't bake for export any more; the Graph's preview
    bake runs only while the Graph is on screen, at DEFAULT_BAKE_SETTINGS
    regardless of the session's export size/domain."""
    _app()
    win = MainWindow()
    try:
        baked = []
        win.viewer_3d.set_lut = baked.append
        visible = [False]
        win.viewer_3d.isVisible = lambda: visible[0]
        win._set_bake_settings(st.BakeSettings(size=9, domain_max=(4.0, 4.0, 4.0)))

        win.stack_panel._on_add_cdl_clicked()
        assert baked == [] and win._graph_preview_stale

        visible[0] = True
        win._refresh_graph_preview()
        assert len(baked) == 1 and not win._graph_preview_stale
        assert baked[0].size == st.DEFAULT_BAKE_SETTINGS.size
        assert np.allclose(np.asarray(baked[0].domain)[1], 1.0)
    finally:
        win.close()
    print("MainWindow: Graph preview skipped while hidden, baked at the "
          "fixed preview grid once visible -- OK")


def test_fork_structure_error_is_reported_without_a_bake():
    _app()
    win = MainWindow()
    try:
        win.viewer_3d.isVisible = lambda: False
        win.stack_panel._on_add_fork_clicked()
        split = win.stack_panel.get_stack().stages[0]
        win.stack_panel.get_stack().remove_stage(split.id)
        win._on_stack_changed()
        assert not win.stack_panel.bake_error_label.isHidden()
        assert "fork_merge" in win.stack_panel.bake_error_label.text()
    finally:
        win.close()
    print("MainWindow: an unmatched fork is flagged without baking -- OK")


def test_export_bakes_at_the_session_settings_and_they_follow_the_session():
    _app()
    path = str(Path(_workspace_tmp_dir.name) / "bake_settings.ltr")
    custom = st.BakeSettings(size=9, domain_min=(-0.5,) * 3, domain_max=(2.0,) * 3)
    win = MainWindow()
    try:
        win.stack_panel._on_add_cdl_clicked()
        win._set_bake_settings(custom)
        lut, _stem = win._resolve_export_source()
        assert lut.size == 9
        assert np.allclose(np.asarray(lut.domain), [[-0.5] * 3, [2.0] * 3])

        win._write_session(path)
        question = main_window_module.QMessageBox.question
        # The stack isn't empty, so New Session asks before discarding it.
        main_window_module.QMessageBox.question = staticmethod(
            lambda *a, **k: main_window_module.QMessageBox.StandardButton.Yes)
        try:
            assert win._new_session() is True
        finally:
            main_window_module.QMessageBox.question = question
        assert win._bake_settings == st.DEFAULT_BAKE_SETTINGS

        original = main_window_module.QFileDialog.getOpenFileName
        main_window_module.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (path, ""))
        try:
            win._open_session()
        finally:
            main_window_module.QFileDialog.getOpenFileName = original
        assert win._bake_settings == custom
    finally:
        win.close()
    print("MainWindow: export bakes at the session's grid/domain, which save, "
          "reset on New Session, and reload -- OK")


def test_timelines_load_from_and_unload_to_the_media_library():
    """Tom, 2026-10-05: "the timeline should be a media library element
    that can be loaded or unloaded"."""
    _app()
    path = str(Path(_workspace_tmp_dir.name) / "timelines.ltr")
    clip_path = str(Path(_workspace_tmp_dir.name) / "shot.mov")
    _write_prores_mov(clip_path)  # selecting a clip loads it, so it must be real
    win = MainWindow()
    try:
        assert win.timeline_panel.get_timeline() is None
        entry = win.media_panel.library.add_entry(MediaEntry(
            "Shot", clip_path, media_kind="video", frame_count=3, frame_rate=24.0))
        # Adding with nothing loaded makes a timeline in the current library.
        win.media_panel.add_to_timeline_requested.emit(entry)
        timeline = win.timeline_panel.get_timeline()
        assert timeline is not None and timeline in win.media_panel.library.timelines
        assert [c.name for c in timeline.clips] == ["Shot"]
        assert win.media_panel._loaded_timeline is timeline

        win.media_panel.timeline_unload_requested.emit()
        assert win.timeline_panel.get_timeline() is None
        assert timeline in win.media_panel.library.timelines  # still in the library

        win.media_panel.timeline_load_requested.emit(timeline)
        assert win.timeline_panel.get_timeline() is timeline
        win._write_session(path)

        win.media_panel.library.remove_timeline(timeline.id)
        win.media_panel.timeline_deleted.emit(timeline)
        assert win.timeline_panel.get_timeline() is None

        original = main_window_module.QFileDialog.getOpenFileName
        main_window_module.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (path, ""))
        try:
            win._open_session()
        finally:
            main_window_module.QFileDialog.getOpenFileName = original
        reopened = win.timeline_panel.get_timeline()
        assert reopened is not None and [c.name for c in reopened.clips] == ["Shot"]
        assert reopened in win.media_panel.get_pool().libraries[0].timelines
    finally:
        win.close()
    print("MainWindow: timelines load from / unload to the Media Library, "
          "and the open one survives save/open -- OK")


def test_source_viewer_edits_land_at_the_record_playhead_and_match_frame_finds_them():
    """Source/Record (Tom, 2026-10-05): Append/Insert/Overwrite from the
    Source viewer edit the loaded timeline (creating one if none is
    loaded) at Record's playhead; Match Frame loads the clip under the
    playhead into Source on the same source frame."""
    _app()
    clip_path = str(Path(_workspace_tmp_dir.name) / "source_edit.mov")
    _write_prores_mov(clip_path)  # 3 frames
    win = MainWindow()
    try:
        assert win.timeline_panel.get_timeline() is None
        spec = {"name": "Shot", "path": clip_path, "kind": "video",
                "in_frame": 0, "out_frame": 2, "thumbnail": None}
        win._on_source_edit_requested("append", spec)
        timeline_obj = win.timeline_panel.get_timeline()
        assert timeline_obj is not None and [c.frame_count for c in timeline_obj.clips] == [3]
        win._on_source_edit_requested("append", dict(spec, name="Second"))
        # Record playhead to frame 1, then insert a one-frame piece there.
        win.sample_image._timeline_controller.seek_to_record_frame(1)
        win._on_source_edit_requested("insert", dict(spec, name="Inserted", in_frame=2, out_frame=2))
        assert [(c.name, c.frame_count) for c in timeline_obj.clips] == [
            ("Shot", 1), ("Inserted", 1), ("Shot", 2), ("Second", 3)]

        win.sample_image._timeline_controller.seek_to_record_frame(1)
        win.match_frame()
        assert win.source_viewer.player.path == clip_path
        assert win.source_viewer.player.frame_index == 2  # the Inserted piece is source frame 2
    finally:
        win.close()
    print("MainWindow: Source edits land at Record's playhead; Match Frame "
          "finds the source frame -- OK")


def test_record_renders_through_the_input_and_display_transforms():
    """colour_pipeline.py wired into Record: the loaded clip's input space
    leads the effective stack, the project display transform ends it
    (and steps aside for a DRT in the Stack), the Stack shows the
    pipeline, and changing an entry's input space reaches its clips."""
    _app()
    clip_path = str(Path(_workspace_tmp_dir.name) / "pipeline.mov")
    _write_prores_mov(clip_path)
    win = MainWindow()
    try:
        entry = win.media_panel.library.add_entry(MediaEntry(
            "Shot", clip_path, media_kind="video", frame_count=3, frame_rate=24.0,
            source_colourspace="ARRI LogC4"))
        win.sample_image.load_video(clip_path)
        kinds = [s.kind for s in win._composed_preview_stack().stages]
        assert kinds == ["colorspace"], kinds
        assert "ARRI LogC4" in win.stack_panel.pipeline_label.text()

        win._set_project_canvas(cv.replace_canvas(win._canvas, display_drt="aces"))
        assert [s.kind for s in win._composed_preview_stack().stages] == ["colorspace", "aces_drt"]
        win.stack_panel._on_add_custom_drt_clicked()
        assert "aces_drt" not in [s.kind for s in win._composed_preview_stack().stages]

        win.media_panel.add_to_timeline_requested.emit(entry)
        clip = win.timeline_panel.get_timeline().clips[0]
        assert clip.input_colourspace == "ARRI LogC4"
        win.media_panel.set_input_colourspace([entry], None)
        assert clip.input_colourspace is None
    finally:
        win.close()
    print("MainWindow: Record renders through the input and display "
          "transforms -- OK")


if __name__ == "__main__":
    test_rapid_stack_changes_coalesce_into_one_debounced_push()
    test_undo_flushes_a_pending_debounced_push_first()
    test_redo_flushes_a_pending_debounced_push_first()
    test_new_session_cancels_a_pending_debounced_push()
    test_launch_and_new_session_start_without_an_image_and_unconfirmed_canvas()
    test_opening_a_session_saved_without_an_image_clears_the_viewer()
    test_graph_preview_bakes_only_while_visible_at_the_fixed_preview_grid()
    test_fork_structure_error_is_reported_without_a_bake()
    test_export_bakes_at_the_session_settings_and_they_follow_the_session()
    test_timelines_load_from_and_unload_to_the_media_library()
    test_source_viewer_edits_land_at_the_record_playhead_and_match_frame_finds_them()
    test_record_renders_through_the_input_and_display_transforms()
    print()
    print("All undo-debounce tests passed.")
