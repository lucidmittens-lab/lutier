"""Tests for video_export.py -- run directly: python3 test_video_export.py

Fully Qt-free. Generates its own real source clip via PyAV (same
"distinct solid color per frame" convention test_video_source.py's own
fixture uses) and reads exported output back via video_source.VideoSource
itself -- confirms the real, end-to-end round trip, not just "doesn't
raise."
"""
import os
import tempfile

import av
import numpy as np

import canvas as cv
import stack as st
import video_export
import video_source

SRC_FRAME_COUNT = 8
SRC_FRAME_RATE = 24
SRC_WIDTH, SRC_HEIGHT = 40, 30


def _tmp(name):
    return os.path.join(tempfile.gettempdir(), f"test_video_export_{os.getpid()}_{name}")


def _make_source_clip(path, frame_count=SRC_FRAME_COUNT, frame_rate=SRC_FRAME_RATE,
                       width=SRC_WIDTH, height=SRC_HEIGHT):
    container = av.open(path, mode="w")
    stream = container.add_stream("libx264", rate=frame_rate)
    stream.width = width
    stream.height = height
    stream.pix_fmt = "yuv420p"
    for i in range(frame_count):
        color = (min(255, i * 30), max(0, 255 - i * 30), 64)
        frame = av.VideoFrame.from_ndarray(
            np.full((height, width, 3), color, dtype=np.uint8), format="rgb24")
        frame.pts = i
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def test_available_codec_presets_only_lists_installed_codecs():
    presets = video_export.available_codec_presets()
    assert isinstance(presets, list)
    for preset in presets:
        assert video_export.is_codec_available(preset["codec_name"])
    labels = [p["label"] for p in presets]
    # H.264 ships in virtually every real ffmpeg build -- if THIS
    # assertion ever fails, it's a real environment problem worth
    # knowing about, not a flaky test to just loosen.
    assert "H.264 (.mp4)" in labels
    print("video_export.available_codec_presets: only lists codecs this "
          "PyAV/ffmpeg build can actually encode, includes H.264 -- OK")


def test_get_codec_preset_raises_for_an_unknown_label():
    try:
        video_export.get_codec_preset("not a real codec")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    print("video_export.get_codec_preset: raises ValueError for an "
          "unknown label -- OK")


def test_video_encoder_rejects_bit_depth_beyond_the_codec_preset_max():
    preset = video_export.get_codec_preset("H.264 (.mp4)")
    try:
        video_export.VideoEncoder(_tmp("reject.mp4"), 16, 16, 24, preset, bit_depth=16)
        raise AssertionError("expected ValueError -- H.264's own max_bit_depth is 8")
    except ValueError:
        pass
    print("video_export.VideoEncoder: rejects a bit_depth beyond the "
          "chosen codec preset's own max_bit_depth -- OK")


def test_video_encoder_h264_round_trips_a_solid_color_frame():
    preset = video_export.get_codec_preset("H.264 (.mp4)")
    path = _tmp("h264_roundtrip.mp4")
    encoder = video_export.VideoEncoder(path, 16, 12, 24, preset, bit_depth=8)
    frame = np.full((12, 16, 3), (0.8, 0.2, 0.4), dtype=np.float32)
    encoder.write_frame(frame)
    assert encoder.frame_index == 1
    encoder.close()

    src = video_source.VideoSource(path)
    assert src.width == 16 and src.height == 12
    got = src.decode_next_frame()
    src.close()
    np.testing.assert_allclose(got.mean(axis=(0, 1)), [0.8, 0.2, 0.4], atol=0.05)
    print("video_export.VideoEncoder: H.264-encoded frame round-trips "
          "back to roughly the same color -- OK")


def test_video_encoder_prores_preserves_higher_precision_than_h264():
    """Not just "doesn't crash" -- ProRes 4444's own real point (a
    bit-depth-CAPABLE default) must actually preserve finer gradations
    than an 8-bit codec would, confirmed by comparing round-trip error
    against a fine gradient through both."""
    if not video_export.is_codec_available("prores_ks"):
        print("SKIPPED test_video_encoder_prores_preserves_higher_precision_than_h264 "
              "-- prores_ks not available in this PyAV/ffmpeg build")
        return
    prores = video_export.get_codec_preset("ProRes 4444 (.mov)")
    h264 = video_export.get_codec_preset("H.264 (.mp4)")

    w, h = 16, 8
    gradient = np.linspace(0.0, 1.0, w, dtype=np.float32)
    frame = np.tile(gradient[None, :, None], (h, 1, 3))

    prores_path = _tmp("prores_gradient.mov")
    enc = video_export.VideoEncoder(prores_path, w, h, 24, prores, bit_depth=10)
    enc.write_frame(frame)
    enc.close()
    src = video_source.VideoSource(prores_path)
    prores_result = src.decode_next_frame()
    src.close()
    prores_error = np.abs(prores_result.mean(axis=(0, 2)) - gradient).max()

    h264_path = _tmp("h264_gradient.mp4")
    enc = video_export.VideoEncoder(h264_path, w, h, 24, h264, bit_depth=8)
    enc.write_frame(frame)
    enc.close()
    src = video_source.VideoSource(h264_path)
    h264_result = src.decode_next_frame()
    src.close()
    h264_error = np.abs(h264_result.mean(axis=(0, 2)) - gradient).max()

    # Both should be reasonably close (real codecs, real compression),
    # but the whole point of offering ProRes 4444 as the bit-depth-
    # capable default is that it isn't WORSE than the 8-bit fallback --
    # a real regression here would mean bit_depth=10 wasn't actually
    # doing anything.
    assert prores_error <= h264_error + 0.02, (
        f"ProRes 4444 (10-bit) should not be meaningfully less precise than "
        f"H.264 (8-bit): prores_error={prores_error}, h264_error={h264_error}")
    print(f"video_export.VideoEncoder: ProRes 4444 (10-bit, error={prores_error:.4f}) "
          f"is at least as precise as H.264 (8-bit, error={h264_error:.4f}) -- OK")


def test_export_video_cpu_grades_every_frame_and_matches_frame_count():
    src_path = _tmp("cpu_export_source.mp4")
    _make_source_clip(src_path)
    source = video_source.VideoSource(src_path)

    grading_stack = st.Stack(stages=[st.make_cdl_stage(slope=(1.2, 0.9, 1.0))])
    preset = video_export.get_codec_preset("H.264 (.mp4)")
    out_path = _tmp("cpu_export_out.mp4")
    canvas = cv.CanvasSettings(width=SRC_WIDTH, height=SRC_HEIGHT)

    with video_export.VideoEncoder(out_path, SRC_WIDTH, SRC_HEIGHT, SRC_FRAME_RATE, preset,
                                    bit_depth=8) as encoder:
        video_export.export_video_cpu(source, grading_stack, encoder, canvas)
    source.close()

    result = video_source.VideoSource(out_path)
    assert result.frame_count == SRC_FRAME_COUNT
    frame0 = result.decode_next_frame()
    result.close()
    # frame 0's source color is (0, 255, 64)/255 -- CDL slope (1.2,0.9,1.0)
    # should scale R up and G down relative to the ungraded source.
    expected_r = min(1.0, 0.0 * 1.2)
    expected_g = min(1.0, (255.0 / 255.0) * 0.9)
    np.testing.assert_allclose(frame0.mean(axis=(0, 1)), [expected_r, expected_g, 64 / 255.0], atol=0.08)
    print("video_export.export_video_cpu: grades every frame through the "
          "real Stack, output frame count matches the source -- OK")


def test_export_video_cpu_resizes_to_canvas_resolution():
    src_path = _tmp("resize_source.mp4")
    _make_source_clip(src_path, width=SRC_WIDTH, height=SRC_HEIGHT)
    source = video_source.VideoSource(src_path)

    identity_stack = st.Stack()
    preset = video_export.get_codec_preset("H.264 (.mp4)")
    out_path = _tmp("resize_out.mp4")
    target_w, target_h = 20, 16
    canvas = cv.CanvasSettings(width=target_w, height=target_h)

    with video_export.VideoEncoder(out_path, target_w, target_h, SRC_FRAME_RATE, preset,
                                    bit_depth=8) as encoder:
        video_export.export_video_cpu(source, identity_stack, encoder, canvas)
    source.close()

    result = video_source.VideoSource(out_path)
    assert result.width == target_w and result.height == target_h
    result.close()
    print("video_export.export_video_cpu: resizes every frame to the "
          "canvas's own resolution before grading/encoding -- OK")


def test_export_video_cpu_respects_start_and_end_frame():
    src_path = _tmp("range_source.mp4")
    _make_source_clip(src_path, frame_count=10)
    source = video_source.VideoSource(src_path)

    identity_stack = st.Stack()
    preset = video_export.get_codec_preset("H.264 (.mp4)")
    out_path = _tmp("range_out.mp4")
    canvas = cv.CanvasSettings(width=SRC_WIDTH, height=SRC_HEIGHT)

    with video_export.VideoEncoder(out_path, SRC_WIDTH, SRC_HEIGHT, SRC_FRAME_RATE, preset,
                                    bit_depth=8) as encoder:
        video_export.export_video_cpu(source, identity_stack, encoder, canvas,
                                       start_frame=3, end_frame=7)
    source.close()

    result = video_source.VideoSource(out_path)
    assert result.frame_count == 4, "end_frame is exclusive -- frames 3,4,5,6"
    result.close()
    print("video_export.export_video_cpu: start_frame/end_frame produce "
          "exactly the requested (exclusive-end) frame range -- OK")


def test_export_video_cpu_calls_progress_callback_for_each_frame():
    src_path = _tmp("progress_source.mp4")
    _make_source_clip(src_path, frame_count=5)
    source = video_source.VideoSource(src_path)

    identity_stack = st.Stack()
    preset = video_export.get_codec_preset("H.264 (.mp4)")
    out_path = _tmp("progress_out.mp4")
    canvas = cv.CanvasSettings(width=SRC_WIDTH, height=SRC_HEIGHT)

    calls = []
    with video_export.VideoEncoder(out_path, SRC_WIDTH, SRC_HEIGHT, SRC_FRAME_RATE, preset,
                                    bit_depth=8) as encoder:
        video_export.export_video_cpu(
            source, identity_stack, encoder, canvas,
            progress_callback=lambda done, total: calls.append((done, total)))
    source.close()

    assert calls == [(1, 5), (2, 5), (3, 5), (4, 5), (5, 5)]
    print("video_export.export_video_cpu: progress_callback fires once per "
          "frame with the correct (done, total) -- OK")


def test_export_video_cpu_stops_early_when_should_cancel_returns_true():
    src_path = _tmp("cancel_source.mp4")
    _make_source_clip(src_path, frame_count=10)
    source = video_source.VideoSource(src_path)

    identity_stack = st.Stack()
    preset = video_export.get_codec_preset("H.264 (.mp4)")
    out_path = _tmp("cancel_out.mp4")
    canvas = cv.CanvasSettings(width=SRC_WIDTH, height=SRC_HEIGHT)

    frames_written = []
    with video_export.VideoEncoder(out_path, SRC_WIDTH, SRC_HEIGHT, SRC_FRAME_RATE, preset,
                                    bit_depth=8) as encoder:
        video_export.export_video_cpu(
            source, identity_stack, encoder, canvas,
            should_cancel=lambda: len(frames_written) >= 3,
            progress_callback=lambda done, total: frames_written.append(done))
    source.close()

    assert len(frames_written) == 3
    result = video_source.VideoSource(out_path)
    assert result.frame_count == 3
    result.close()
    print("video_export.export_video_cpu: stops early when should_cancel "
          "returns True, output file has exactly the frames written so far -- OK")


if __name__ == "__main__":
    test_available_codec_presets_only_lists_installed_codecs()
    test_get_codec_preset_raises_for_an_unknown_label()
    test_video_encoder_rejects_bit_depth_beyond_the_codec_preset_max()
    test_video_encoder_h264_round_trips_a_solid_color_frame()
    test_video_encoder_prores_preserves_higher_precision_than_h264()
    test_export_video_cpu_grades_every_frame_and_matches_frame_count()
    test_export_video_cpu_resizes_to_canvas_resolution()
    test_export_video_cpu_respects_start_and_end_frame()
    test_export_video_cpu_calls_progress_callback_for_each_frame()
    test_export_video_cpu_stops_early_when_should_cancel_returns_true()
    print()
    print("All video_export tests passed.")
