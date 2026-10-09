"""Tests for gpu_frame_renderer.py and render_job's GPU path -- needs a real
GL context (conftest.GL_TEST_FILES): ./run_linux.sh test-gl, or
QT_QPA_PLATFORM=xcb PYOPENGL_PLATFORM=glx python3 -m tests.test_gpu_frame_renderer
"""
import os
import sys
import tempfile

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

import canvas as cv
import colour_pipeline
import gpu_frame_renderer as gfr
import image_io
import render_job as rj
import stack as st
import video_source
from tests.test_render_job import _make_clip


def _app():
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    return QApplication.instance() or QApplication(sys.argv)


def _graded_stack():
    user = st.Stack(stages=[st.make_cdl_stage(slope=(1.2, 1.0, 0.8), power=(1.1, 1.0, 0.9))])
    return colour_pipeline.build_effective_stack(
        user, cv.DEFAULT_CANVAS.working_colourspace, "ARRI LogC3 (EI800)")


def _gradient_uint16(w, h):
    x = np.linspace(0.05, 0.95, w, dtype=np.float32)[None, :]
    y = np.linspace(0.1, 0.9, h, dtype=np.float32)[:, None]
    rgb = np.stack([np.broadcast_to(x, (h, w)), np.broadcast_to(y, (h, w)),
                    np.broadcast_to((x + y) / 2, (h, w))], axis=-1)
    return np.round(rgb * 65535).astype(np.uint16)


def test_gpu_frame_matches_the_cpu_resize_then_grade():
    _app()
    renderer = gfr.GpuFrameRenderer(gfr.make_offscreen_surface())
    try:
        frame = _gradient_uint16(640, 360)
        stack = _graded_stack()
        assert renderer.can_render(stack)
        gpu = renderer.render(stack, frame, 320, 180)
        as_float = frame.astype(np.float32) / 65535.0
        cpu = st.apply_stack_to_image_array(stack, image_io.resize_array(as_float, 320, 180))
        assert gpu.shape == (180, 320, 3)
        diff = np.abs(gpu - cpu)
        assert diff.max() < 2e-3 and diff.mean() < 1e-4, (diff.max(), diff.mean())

        same = renderer.render(st.Stack(), frame, 640, 360)
        assert np.abs(same - as_float).max() < 1e-6, "same size + empty stack = exact copy"

        still = renderer.render(stack, as_float, 640, 360)   # float input (stills)
        cpu_still = st.apply_stack_to_image_array(stack, as_float)
        assert np.abs(still - cpu_still).max() < 1e-3
    finally:
        renderer.close()
    print("gpu_frame_renderer: resize + grade matches the CPU; exact copy; float input -- OK")


def test_run_job_on_the_gpu_matches_the_cpu_render():
    _app()
    d = tempfile.mkdtemp()
    src = os.path.join(d, "src.mp4")
    _make_clip(src, frame_count=6)
    stack = _graded_stack()

    def job(name):
        seg = rj.Segment(name, src, "video", 0, 5, stack, frame_rate=24.0)
        settings = rj.RenderSettings("TIFF sequence (.tif)", 16, d, width=20, height=16)
        return rj.RenderJob(name, rj.plan_items(name, [seg], settings, "x"))

    on_cpu, on_gpu = job("cpu"), job("gpu")
    assert rj.run_job(on_cpu) == rj.DONE and on_cpu.engine == "CPU"
    renderer = gfr.GpuFrameRenderer(gfr.make_offscreen_surface())
    try:
        assert rj.run_job(on_gpu, gpu=renderer) == rj.DONE and on_gpu.engine == "GPU"
        # A stack the GPU can't run renders on the CPU, under the same renderer.
        import colour
        lut_stage = st.make_lut_stage(colour.LUT1D())  # a 1D LUT isn't on the GPU
        mixed = rj.RenderJob("mixed", rj.plan_items("mixed", [
            rj.Segment("a", src, "video", 0, 1, stack, frame_rate=24.0),
            rj.Segment("b", src, "video", 2, 3, st.Stack(stages=[lut_stage]), frame_rate=24.0)],
            rj.RenderSettings("H.264 (.mp4)", 8, d, width=20, height=16), "x"))
        assert rj.run_job(mixed, gpu=renderer) == rj.DONE
        assert mixed.engine == "GPU + CPU"
    finally:
        renderer.close()
    for frame in range(6):
        a, _ = image_io.read_image_as_array(on_cpu.items[0].frame_path(frame))
        b, _ = image_io.read_image_as_array(on_gpu.items[0].frame_path(frame))
        assert np.abs(a - b).max() < 2e-3, frame
    print("render_job: GPU render matches the CPU render; ineligible segments fall back -- OK")


def test_render_queue_renders_on_the_gpu_from_its_worker_thread():
    _app()
    from render_queue_panel import RenderQueuePanel
    d = tempfile.mkdtemp()
    src = os.path.join(d, "src.mp4")
    _make_clip(src, frame_count=12)
    seg = rj.Segment("q", src, "video", 0, 11, _graded_stack(), frame_rate=24.0)
    job = rj.RenderJob("q", rj.plan_items("q", [seg], rj.RenderSettings(
        "H.264 (.mp4)", 8, d, width=40, height=30), "x"))
    queue = RenderQueuePanel()
    queue.add_job(job)
    queue.render_all()
    assert queue.wait_until_idle()
    queue.shutdown()
    assert job.status == rj.DONE, job.error
    assert job.engine == "GPU"
    out = video_source.VideoSource(job.items[0].output_path)
    try:
        assert out.frame_count == 12
    finally:
        out.close()
    print("render queue: renders on the GPU in its own context on the worker thread -- OK")


if __name__ == "__main__":
    test_gpu_frame_matches_the_cpu_resize_then_grade()
    test_run_job_on_the_gpu_matches_the_cpu_render()
    test_render_queue_renders_on_the_gpu_from_its_worker_thread()
    print("\nAll GPU frame renderer tests passed.")
