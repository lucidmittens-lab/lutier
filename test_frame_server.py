"""run directly: QT_QPA_PLATFORM=offscreen python3 test_frame_server.py"""

import os
import tempfile
import time
import av
import numpy as np

from frame_server import FrameServer, Segment
from scopes_worker import ScopesWorker

def _expected(raw):
    """What FrameServer hands back for a frame written with value `raw`
    (uint16): float32 RGB in [0, 1]."""
    return np.full((16, 32, 3), min(65535, raw) / 65535.0, np.float32)


def _write_video(path, frame_count, value_step=10):
    container = av.open(path, "w")
    stream = container.add_stream("ffv1", rate=24)
    stream.width, stream.height, stream.pix_fmt = 32, 16, "rgb48le"
    for i in range(frame_count):
        rgb = np.full((16, 32, 3), min(65535, i * value_step * 257), np.uint16)
        for packet in stream.encode(av.VideoFrame.from_ndarray(rgb, format="rgb48le")):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()

def _wait_for(server, segment, offset, timeout=5.0):
    start = time.time()
    while time.time() - start < timeout:
        frame = server.take(segment, offset)
        if frame is not None:
            return frame
        time.sleep(0.01)
    return None

def test_decodes_ahead_in_order():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "video.mkv")
        _write_video(path, 10)
        server = FrameServer(max_dimension=None, capacity=4)
        try:
            server.restart([Segment(path, "video", 0, 10)], 0, 0)
            for i in range(3):
                frame = _wait_for(server, 0, i)
                expected = _expected(i * 10 * 257)
                assert np.allclose(frame, expected, atol=2e-3), f"Frame {i} mismatch"
        finally:
            server.stop()
    print("test_decodes_ahead_in_order -- OK")

def test_take_drops_earlier_frames():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "video.mkv")
        _write_video(path, 10)
        server = FrameServer(max_dimension=None, capacity=4)
        try:
            server.restart([Segment(path, "video", 0, 10)], 0, 0)
            while server.buffered_count() < 3:
                time.sleep(0.01)
            frame = server.take(0, 2)
            assert frame is not None
            assert server.take(0, 0) is None
            assert server.take(0, 1) is None
        finally:
            server.stop()
    print("test_take_drops_earlier_frames -- OK")

def test_capacity_bounds_the_buffer():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "video.mkv")
        _write_video(path, 10)
        server = FrameServer(max_dimension=None, capacity=4)
        try:
            server.restart([Segment(path, "video", 0, 10)], 0, 0)
            time.sleep(0.5)
            assert server.buffered_count() <= 4
        finally:
            server.stop()
    print("test_capacity_bounds_the_buffer -- OK")

def test_crosses_into_the_next_segment():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path_a = os.path.join(tmp_dir, "video_a.mkv")
        path_b = os.path.join(tmp_dir, "video_b.mkv")
        _write_video(path_a, 5, 10)
        _write_video(path_b, 5, 20)
        server = FrameServer(max_dimension=None, capacity=4)
        try:
            server.restart([Segment(path_a, "video", 0, 5), Segment(path_b, "video", 0, 5)], 0, 3)
            frame = _wait_for(server, 0, 3)
            expected_a = _expected(3 * 10 * 257)
            assert np.allclose(frame, expected_a, atol=2e-3), "Frame (0,3) mismatch"
            frame = _wait_for(server, 0, 4)
            expected_a = _expected(4 * 10 * 257)
            assert np.allclose(frame, expected_a, atol=2e-3), "Frame (0,4) mismatch"
            frame = _wait_for(server, 1, 0)
            expected_b = _expected(0 * 20 * 257)
            assert np.allclose(frame, expected_b, atol=2e-3), "Frame (1,0) mismatch"
        finally:
            server.stop()
    print("test_crosses_into_the_next_segment -- OK")

def test_segment_first_frame_offsets_into_the_source():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "video.mkv")
        _write_video(path, 10, 10)
        server = FrameServer(max_dimension=None, capacity=4)
        try:
            server.restart([Segment(path, "video", 2, 3)], 0, 0)
            frame = _wait_for(server, 0, 0)
            expected = _expected(2 * 10 * 257)
            assert np.allclose(frame, expected, atol=2e-3), "Frame (0,0) mismatch"
        finally:
            server.stop()
    print("test_segment_first_frame_offsets_into_the_source -- OK")

def test_loop_wraps_to_the_start():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "video.mkv")
        _write_video(path, 10, 10)
        server = FrameServer(max_dimension=None, capacity=4)
        try:
            server.restart([Segment(path, "video", 0, 3)], 0, 1, loop=True)
            frame = _wait_for(server, 0, 1)
            expected = _expected(1 * 10 * 257)
            assert np.allclose(frame, expected, atol=2e-3), "Frame (0,1) mismatch"
            frame = _wait_for(server, 0, 2)
            expected = _expected(2 * 10 * 257)
            assert np.allclose(frame, expected, atol=2e-3), "Frame (0,2) mismatch"
            frame = _wait_for(server, 0, 0)
            expected = _expected(0 * 10 * 257)
            assert np.allclose(frame, expected, atol=2e-3), "Frame (0,0) mismatch"
        finally:
            server.stop()
    print("test_loop_wraps_to_the_start -- OK")

def test_step_skips_frames():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "video.mkv")
        _write_video(path, 10, 10)
        server = FrameServer(max_dimension=None, capacity=4)
        try:
            server.restart([Segment(path, "video", 0, 10)], 0, 0, step=2)
            frame = _wait_for(server, 0, 0)
            expected = _expected(0 * 10 * 257)
            assert np.allclose(frame, expected, atol=2e-3), "Frame (0,0) mismatch"
            frame = _wait_for(server, 0, 2)
            expected = _expected(2 * 10 * 257)
            assert np.allclose(frame, expected, atol=2e-3), "Frame (0,2) mismatch"
            frame = _wait_for(server, 0, 4)
            expected = _expected(4 * 10 * 257)
            assert np.allclose(frame, expected, atol=2e-3), "Frame (0,4) mismatch"
        finally:
            server.stop()
    print("test_step_skips_frames -- OK")

def test_take_latest_up_to():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "video.mkv")
        _write_video(path, 10, 10)
        server = FrameServer(max_dimension=None, capacity=4)
        try:
            server.restart([Segment(path, "video", 0, 10)], 0, 0)
            while server.buffered_count() < 3:
                time.sleep(0.01)
            offset, frame = server.take_latest_up_to(0, 9)
            assert offset >= 2
            expected = _expected(offset * 10 * 257)
            assert np.allclose(frame, expected, atol=2e-3), f"Frame (0,{offset}) mismatch"
        finally:
            server.stop()
    print("test_take_latest_up_to -- OK")

def test_restart_discards_the_old_plan():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "video.mkv")
        _write_video(path, 10, 10)
        server = FrameServer(max_dimension=None, capacity=4)
        try:
            server.restart([Segment(path, "video", 0, 10)], 0, 0)
            while server.buffered_count() < 2:
                time.sleep(0.01)
            server.restart([Segment(path, "video", 0, 10)], 0, 6)
            frame = _wait_for(server, 0, 6)
            expected = _expected(6 * 10 * 257)
            assert np.allclose(frame, expected, atol=2e-3), "Frame (0,6) mismatch"
            assert server.take(0, 0) is None
        finally:
            server.stop()
    print("test_restart_discards_the_old_plan -- OK")

def test_scopes_worker_delivers_latest_and_cancel_makes_results_stale():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest

    def _app():
        app = QApplication.instance()
        if app is None:
            app = QApplication([])
        return app

    _app()
    worker = ScopesWorker()
    results = []
    worker.result_ready.connect(lambda array, token: results.append((array, token)))
    worker.submit(None, np.zeros((4, 4, 3)))
    start = time.time()
    while not results and time.time() - start < 2:
        QTest.qWait(25)
    assert results and results[-1][1] == worker.token
    before = worker.token
    worker.cancel()
    assert worker.token == before + 1
    print("test_scopes_worker_delivers_latest_and_cancel_makes_results_stale -- OK")

if __name__ == "__main__":
    test_decodes_ahead_in_order()
    test_take_drops_earlier_frames()
    test_capacity_bounds_the_buffer()
    test_crosses_into_the_next_segment()
    test_segment_first_frame_offsets_into_the_source()
    test_loop_wraps_to_the_start()
    test_step_skips_frames()
    test_take_latest_up_to()
    test_restart_discards_the_old_plan()
    test_scopes_worker_delivers_latest_and_cancel_makes_results_stale()
    print()
    print("All frame_server tests passed.")
