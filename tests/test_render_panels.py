"""Tests for render_settings_panel.py, render_queue_panel.py and
main_window_parts/render.py -- run directly:
QT_QPA_PLATFORM=offscreen python3 -m tests.test_render_panels
"""
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
from PySide6.QtWidgets import QApplication

import image_io
import render_job as rj
import stack as st
import timeline as tl
import workspace_io
from render_queue_panel import RenderQueuePanel
from render_settings_panel import CURRENT_CLIP, TIMELINE, RenderRequest, RenderSettingsPanel
from tests.test_render_job import _make_clip

_workspace_tmp_dir = tempfile.TemporaryDirectory()
workspace_io.DEFAULT_WORKSPACE_DIR = Path(_workspace_tmp_dir.name) / "LUTier"
workspace_io.DEFAULT_WORKSPACE_PATH = workspace_io.DEFAULT_WORKSPACE_DIR / "workspace.json"


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _job(directory, source, frames=8, name="J"):
    seg = rj.Segment(name, source, "video", 0, frames - 1, st.Stack(), frame_rate=24.0)
    settings = rj.RenderSettings("H.264 (.mp4)", 8, directory, width=40, height=30)
    return rj.RenderJob(name, rj.plan_items(name, [seg], settings, "x"))


def test_settings_panel_formats_preview_and_request():
    _app()
    panel = RenderSettingsPanel()
    panel.format_combo.setCurrentIndex(panel.format_combo.findData("DPX sequence (.dpx)"))
    assert [panel.bit_depth_combo.itemData(i) for i in range(panel.bit_depth_combo.count())] == [10, 12, 16]
    panel.format_combo.setCurrentIndex(panel.format_combo.findData("H.264 (.mp4)"))
    assert panel.bit_depth_combo.count() == 1 and panel.bit_depth_combo.currentData() == 8

    assert not panel.add_button.isEnabled(), "no planner yet"

    def planner(request):
        if request.source == CURRENT_CLIP:
            raise ValueError("Load a clip in Record.")
        seg = rj.Segment("A001", "/m/a.mov", "video", 0, 23, st.Stack(), frame_rate=24.0)
        return rj.plan_items("Reel 1", [seg], rj.RenderSettings(
            request.format_label, request.bit_depth, request.directory,
            name_template=request.name_template), "x")

    panel.directory_edit.setText("/renders")
    panel.set_planner(planner)
    assert panel.add_button.isEnabled()
    assert panel.preview_label.text() == "Reel 1.mp4"
    assert "24 frames" in panel.summary_label.text()
    panel.name_edit.setText("{name}_v2")
    assert panel.preview_label.text() == "Reel 1_v2.mp4"

    panel.source_control.buttons[CURRENT_CLIP].click()
    assert not panel.output_control.isEnabled(), "single file / individual is a timeline choice"
    assert not panel.add_button.isEnabled()
    assert panel.summary_label.text() == "Load a clip in Record."

    panel.source_control.buttons[TIMELINE].click()
    seen = []
    panel.add_requested.connect(seen.append)
    panel.add_button.click()
    assert seen and seen[0].name_template == "{name}_v2" and seen[0].directory == "/renders"

    restored = RenderSettingsPanel()
    restored.set_request(RenderRequest(source=CURRENT_CLIP, format_label="DPX sequence (.dpx)",
                                       bit_depth=12, directory="/x", name_template="{clip}"))
    assert restored.request().bit_depth == 12 and restored.request().source == CURRENT_CLIP
    print("render settings: formats, live preview, problems, request -- OK")


def test_queue_renders_jobs_in_order_and_can_stop():
    _app()
    d = tempfile.mkdtemp()
    source = os.path.join(d, "src.mp4")
    _make_clip(source)
    queue = RenderQueuePanel()
    first, second = _job(d, source, name="one"), _job(d, source, name="two")
    queue.add_job(first)
    queue.add_job(second)
    assert queue.render_all_button.isEnabled() and "2 queued" in queue.status_label.text()
    queue.render_all()
    assert queue.wait_until_idle()
    assert [first.status, second.status] == [rj.DONE, rj.DONE]
    assert all(os.path.exists(p) for p in first.output_paths() + second.output_paths())
    assert queue.status_label.text() == "2 of 2 done"
    queue.clear_finished()
    assert queue.jobs() == []

    long_still = os.path.join(d, "still.png")
    image_io.write_image(np.full((30, 40, 3), 0.5, dtype=np.float32), long_still, 8)
    seg = rj.Segment("hold", long_still, "still", 0, 20000, st.Stack(), frame_rate=24.0)
    slow = rj.RenderJob("slow", rj.plan_items("slow", [seg], rj.RenderSettings(
        "H.264 (.mp4)", 8, d, width=40, height=30), "x"))
    queue.add_job(slow)
    queue.render_all()
    queue.stop()
    assert queue.wait_until_idle()
    assert slow.status == rj.CANCELLED
    queue.requeue([slow])
    assert slow.status == rj.QUEUED and slow.frames_done == 0
    queue.remove_jobs([slow])
    assert queue.jobs() == []
    queue.shutdown()
    print("render queue: renders in order, stop cancels, requeue/remove/clear -- OK")


def test_main_window_plans_the_timeline_with_snapshotted_grades():
    _app()
    from main_window import MainWindow
    d = tempfile.mkdtemp()
    source = os.path.join(d, "A001C002.mp4")
    _make_clip(source)
    win = MainWindow()
    try:
        timeline = tl.Timeline(name="Reel 1", frame_rate=25.0, clips=[
            tl.TimelineClip("A001C002", source, "video", 0, 3),
            tl.TimelineClip("A001C003", source, "video", 4, 7)])
        timeline.clips[1].stack.add_stage(st.make_cdl_stage(slope=(0.5, 0.5, 0.5)))
        win.timeline_panel.load_timeline(timeline)

        request = RenderRequest(format_label="H.264 (.mp4)", bit_depth=8, directory=d)
        items = win._plan_render(request)
        assert len(items) == 1 and items[0].name == "Reel 1" and items[0].frame_count == 8
        assert items[0].frame_rate == 25.0
        assert (items[0].width, items[0].height) == (win._canvas.width, win._canvas.height)
        graded = items[0].segments[1].stack
        assert [s.kind for s in graded.stages].count("cdl") == 1

        timeline.clips[1].stack.add_stage(st.make_cdl_stage(slope=(2, 2, 2)))
        assert [s.kind for s in graded.stages].count("cdl") == 1, "a queued grade is a snapshot"

        each = win._plan_render(RenderRequest(format_label="H.264 (.mp4)", bit_depth=8, directory=d,
                                              single_file=False, name_template="{clip}_{n}"))
        assert [i.name for i in each] == ["A001C002_001", "A001C003_002"]

        win.render_settings_panel.directory_edit.setText(d)
        win._on_render_add_requested(request)
        assert len(win.render_queue_panel.jobs()) == 1
        assert win.render_queue_panel.jobs()[0].name == "Reel 1"

        win.timeline_panel.unload_timeline()
        try:
            win._plan_render(RenderRequest(source=CURRENT_CLIP, format_label="H.264 (.mp4)",
                                           bit_depth=8, directory=d))
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "Record" in str(e)
    finally:
        win.close()
    print("render wiring: timeline planned with snapshotted clip + Sequence grades -- OK")


if __name__ == "__main__":
    test_settings_panel_formats_preview_and_request()
    test_queue_renders_jobs_in_order_and_can_stop()
    test_main_window_plans_the_timeline_with_snapshotted_grades()
    print("\nAll render panel tests passed.")
