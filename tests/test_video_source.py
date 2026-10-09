"""Tests for video_source.py -- run directly: python3 -m tests.test_video_source

Fully Qt-free. Generates its own small real test clip via PyAV's own
encoder (a distinct, easily-checkable solid color per frame) rather than
depending on a fixture file being present, so this is runnable standalone
and exercises the real decode path, not a mock.
"""
import os
import tempfile

import av
import numpy as np

from video_source import VideoSource

FRAME_COUNT = 20
FRAME_RATE = 24
WIDTH, HEIGHT = 32, 24


def _make_test_clip(path, frame_count=FRAME_COUNT, frame_rate=FRAME_RATE,
                     width=WIDTH, height=HEIGHT):
    """Each frame N is a solid color (N*10, 255-N*10, 128) -- distinct and
    trivially checkable per-frame, matching the "unambiguous marker per
    frame" convention test_gpu_wipe_compositor.py's own top/bottom-split
    source image uses for the same reason (confirm decode landed on the
    RIGHT frame, not just SOME frame)."""
    container = av.open(path, mode="w")
    stream = container.add_stream("libx264", rate=frame_rate)
    stream.width = width
    stream.height = height
    stream.pix_fmt = "yuv420p"
    for i in range(frame_count):
        color = (min(255, i * 10), max(0, 255 - i * 10), 128)
        frame = av.VideoFrame.from_ndarray(
            np.full((height, width, 3), color, dtype=np.uint8), format="rgb24")
        frame.pts = i
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def _tmp_clip_path():
    return os.path.join(tempfile.gettempdir(), f"test_video_source_{os.getpid()}.mp4")


def _expected_color_normalized(index):
    color = (min(255, index * 10), max(0, 255 - index * 10), 128)
    return np.array(color, dtype=np.float32) / 255.0


def test_metadata_matches_the_encoded_clip():
    path = _tmp_clip_path()
    _make_test_clip(path)
    src = VideoSource(path)
    assert src.width == WIDTH and src.height == HEIGHT
    assert abs(src.frame_rate - FRAME_RATE) < 0.01
    assert src.frame_count == FRAME_COUNT
    assert abs(src.duration - FRAME_COUNT / FRAME_RATE) < 0.1
    src.close()
    print(f"VideoSource: metadata ({WIDTH}x{HEIGHT}, {FRAME_RATE}fps, "
          f"{FRAME_COUNT} frames) matches the encoded clip -- OK")


def test_decode_next_frame_returns_frames_in_order():
    path = _tmp_clip_path()
    _make_test_clip(path)
    src = VideoSource(path)
    for i in range(FRAME_COUNT):
        arr = src.decode_next_frame()
        assert arr is not None, f"frame {i} was None before end of stream"
        assert arr.shape == (HEIGHT, WIDTH, 3)
        assert arr.dtype == np.float32
        expected = _expected_color_normalized(i)
        # A modest tolerance -- H.264/yuv420p round-trips introduce real,
        # small chroma-subsampling/compression error, not exact-equality
        # like a lossless format would.
        np.testing.assert_allclose(arr.mean(axis=(0, 1)), expected, atol=0.05)
    assert src.decode_next_frame() is None, "must return None past the last frame"
    src.close()
    print("VideoSource.decode_next_frame: returns every frame in order, "
          "each matching its own known color, None past the end -- OK")


def test_decode_next_frame_with_max_dimension_produces_a_capped_proxy():
    """Used by video_playback_controller.py's realtime-playback proxy --
    the reformatted frame must actually be smaller (aspect-preserving)
    and still land on roughly the right color, confirming the reformat
    happens BEFORE the color conversion, not some no-op."""
    path = _tmp_clip_path()
    _make_test_clip(path, width=64, height=48)
    src = VideoSource(path)
    arr = src.decode_next_frame(max_dimension=16)
    assert arr is not None
    assert max(arr.shape[:2]) <= 16
    # 64x48 -> long side 64 capped to 16 -> scale 0.25 -> 16x12
    assert arr.shape[:2] == (12, 16), arr.shape
    np.testing.assert_allclose(arr.mean(axis=(0, 1)), _expected_color_normalized(0), atol=0.05)
    src.close()
    print("VideoSource.decode_next_frame(max_dimension=...): produces an "
          "aspect-preserving capped proxy -- OK")


def test_decode_next_frame_max_dimension_is_a_noop_when_already_smaller():
    path = _tmp_clip_path()
    _make_test_clip(path, width=WIDTH, height=HEIGHT)
    src = VideoSource(path)
    arr = src.decode_next_frame(max_dimension=1000)
    assert arr.shape[:2] == (HEIGHT, WIDTH)
    src.close()
    print("VideoSource.decode_next_frame: max_dimension larger than the "
          "source is a no-op, no upscaling -- OK")


def test_seek_to_frame_lands_on_the_exact_target():
    path = _tmp_clip_path()
    _make_test_clip(path)
    src = VideoSource(path)
    for target in (0, 1, 5, 10, FRAME_COUNT - 1):
        arr = src.seek_to_frame(target)
        assert arr is not None, f"seek to frame {target} returned None"
        expected = _expected_color_normalized(target)
        np.testing.assert_allclose(arr.mean(axis=(0, 1)), expected, atol=0.05)
    src.close()
    print("VideoSource.seek_to_frame: lands on the exact target frame "
          "(low, high, and boundary indices) -- OK")


def test_seek_to_frame_matches_sequential_decode_to_the_same_frame():
    """Frame-accuracy cross-check: seeking directly to frame N must
    produce the SAME array sequential decode_next_frame calls up to frame
    N would -- confirms the seek+discard-forward logic isn't off by one
    or landing on the wrong side of a keyframe boundary."""
    path = _tmp_clip_path()
    _make_test_clip(path)

    sequential = VideoSource(path)
    target = 7
    frame_via_sequential = None
    for i in range(target + 1):
        frame_via_sequential = sequential.decode_next_frame()
    sequential.close()

    seeking = VideoSource(path)
    frame_via_seek = seeking.seek_to_frame(target)
    seeking.close()

    np.testing.assert_allclose(frame_via_seek, frame_via_sequential, atol=1e-6)
    print("VideoSource.seek_to_frame: matches sequential decode to the "
          "same frame exactly -- OK")


def test_seek_to_frame_clamps_out_of_range_indices():
    path = _tmp_clip_path()
    _make_test_clip(path)
    src = VideoSource(path)
    first = src.seek_to_frame(-5)
    np.testing.assert_allclose(first.mean(axis=(0, 1)), _expected_color_normalized(0), atol=0.05)
    last = src.seek_to_frame(FRAME_COUNT + 100)
    np.testing.assert_allclose(
        last.mean(axis=(0, 1)), _expected_color_normalized(FRAME_COUNT - 1), atol=0.05)
    src.close()
    print("VideoSource.seek_to_frame: clamps negative/past-end indices to "
          "the valid [0, frame_count-1] range -- OK")


def test_seek_to_frame_with_max_dimension_produces_a_capped_proxy():
    """Phase 4 addition (video_playback_controller.py's loop-back/speed-
    skip use this so a seek doesn't jump back to full-res just because
    it isn't a sequential decode_next_frame call)."""
    path = _tmp_clip_path()
    _make_test_clip(path, width=64, height=48)
    src = VideoSource(path)
    arr = src.seek_to_frame(3, max_dimension=16)
    assert arr is not None
    assert arr.shape[:2] == (12, 16), arr.shape
    np.testing.assert_allclose(arr.mean(axis=(0, 1)), _expected_color_normalized(3), atol=0.05)
    src.close()
    print("VideoSource.seek_to_frame(max_dimension=...): produces an "
          "aspect-preserving capped proxy, same as decode_next_frame's "
          "own parameter -- OK")


def test_context_manager_closes_the_container():
    path = _tmp_clip_path()
    _make_test_clip(path)
    with VideoSource(path) as src:
        assert src.decode_next_frame() is not None
    # No assertion beyond "doesn't raise" -- PyAV's own container.close()
    # has no public "is closed" flag to probe, this just confirms the
    # context-manager protocol itself doesn't error.
    print("VideoSource: usable as a context manager -- OK")


if __name__ == "__main__":
    test_metadata_matches_the_encoded_clip()
    test_decode_next_frame_returns_frames_in_order()
    test_decode_next_frame_with_max_dimension_produces_a_capped_proxy()
    test_decode_next_frame_max_dimension_is_a_noop_when_already_smaller()
    test_seek_to_frame_lands_on_the_exact_target()
    test_seek_to_frame_matches_sequential_decode_to_the_same_frame()
    test_seek_to_frame_clamps_out_of_range_indices()
    test_seek_to_frame_with_max_dimension_produces_a_capped_proxy()
    test_context_manager_closes_the_container()
    print()
    print("All video_source tests passed.")
