"""Tests for media_probe.py -- run directly: python3 -m tests.test_media_probe

Fully Qt-free. The video fixture is a tiny ProRes .mov written here with
PyAV, carrying an embedded start timecode and a stereo audio track.
"""
import os
import tempfile

import av
import numpy as np

import media_probe
from app_logic import default_sample_image_path


def _write_prores_mov(path, timecode="01:00:00:00", audio=True):
    container = av.open(path, "w")
    video = container.add_stream("prores_ks", rate=24)
    video.width, video.height, video.pix_fmt = 64, 32, "yuv422p10le"
    if timecode:
        video.metadata["timecode"] = timecode  # the mov muxer writes a tmcd track from this
    audio_stream = container.add_stream("pcm_s16le", rate=48000, layout="stereo") if audio else None
    for _ in range(3):
        frame = av.VideoFrame.from_ndarray(np.zeros((32, 64, 3), np.uint8), format="rgb24")
        for packet in video.encode(frame):
            container.mux(packet)
    if audio_stream is not None:
        samples = av.AudioFrame.from_ndarray(
            np.zeros((1, 2 * 2000), np.int16), format="s16", layout="stereo")
        samples.sample_rate = 48000
        for packet in audio_stream.encode(samples):
            container.mux(packet)
    for stream in (video, audio_stream):
        if stream is not None:
            for packet in stream.encode():
                container.mux(packet)
    container.close()


def test_video_probe_reads_codec_pixel_format_timecode_and_audio():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "clip.mov")
        _write_prores_mov(path)
        metadata = media_probe.probe(path, is_video=True)
        assert metadata["format"] == "QuickTime/MP4"
        assert metadata["codec"].startswith("ProRes"), metadata["codec"]
        assert metadata["bit_depth"] == 10
        assert metadata["chroma"] == "4:2:2"
        assert metadata["has_alpha"] is False
        assert metadata["start_timecode"] == "01:00:00:00"
        assert metadata["audio_channels"] == 2
        assert metadata["audio_sample_rate"] == 48000
        assert metadata["file_size"] == os.path.getsize(path)
    print("media_probe: video codec/bit depth/chroma/timecode/audio read "
          "from the header -- OK")


def test_video_probe_leaves_out_what_the_file_does_not_carry():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "plain.mov")
        _write_prores_mov(path, timecode=None, audio=False)
        metadata = media_probe.probe(path, is_video=True)
        assert "start_timecode" not in metadata
        assert "audio_channels" not in metadata
        assert "camera" not in metadata
    print("media_probe: no timecode/audio/camera keys when the file has "
          "none -- OK")


def test_still_probe_reads_format_bit_depth_and_channels():
    metadata = media_probe.probe(default_sample_image_path(), is_video=False)
    assert metadata["format"] == "TIFF"
    assert metadata["bit_depth"] == 16
    assert metadata["is_float"] is False
    assert metadata["chroma"] == "RGB"
    assert "start_timecode" not in metadata
    print("media_probe: still format/bit depth/channels read from the "
          "header -- OK")


def test_still_is_not_probed_as_video():
    # ffmpeg opens a TIFF as a one-frame "video"; is_video=False must
    # keep it on the OpenImageIO path.
    metadata = media_probe.probe(default_sample_image_path(), is_video=False)
    assert "audio_channels" not in metadata and metadata["codec"] != "TIFF"
    print("media_probe: is_video=False keeps a still off the video path -- OK")


def test_pixel_format_fields_cover_common_layouts():
    cases = {
        "yuv444p12le": (12, "4:4:4", False),
        "yuv420p": (8, "4:2:0", False),
        "yuv411p": (8, "4:1:1", False),
        "yuva444p10le": (10, "4:4:4", True),
        "gbrp12le": (12, "RGB", False),
        "gray16le": (16, "Mono", False),
    }
    for pix_fmt, (bits, chroma, alpha) in cases.items():
        fields = media_probe._pixel_format_fields(pix_fmt)
        assert (fields["bit_depth"], fields["chroma"], fields["has_alpha"]) == (bits, chroma, alpha), pix_fmt
    print("media_probe: bit depth/chroma/alpha from common pixel formats -- OK")


def test_camera_from_tags_handles_mxf_and_quicktime_and_duplicate_make():
    assert media_probe._camera_from_tags(
        [{"company_name": "ARRI", "product_name": "ALEXA Mini LF"}]) == "ARRI ALEXA Mini LF"
    assert media_probe._camera_from_tags(
        [{}, {"com.apple.quicktime.make": "Apple", "com.apple.quicktime.model": "iPhone 15"}]
    ) == "Apple iPhone 15"
    assert media_probe._camera_from_tags([{"make": "Canon", "model": "Canon EOS R5"}]) == "Canon EOS R5"
    assert media_probe._camera_from_tags([{"make": "", "model": ""}]) is None
    print("media_probe: camera from MXF/QuickTime/EXIF tags, no doubled "
          "make -- OK")


if __name__ == "__main__":
    test_video_probe_reads_codec_pixel_format_timecode_and_audio()
    test_video_probe_leaves_out_what_the_file_does_not_carry()
    test_still_probe_reads_format_bit_depth_and_channels()
    test_still_is_not_probed_as_video()
    test_pixel_format_fields_cover_common_layouts()
    test_camera_from_tags_handles_mxf_and_quicktime_and_duplicate_make()
    print()
    print("All media_probe tests passed.")
