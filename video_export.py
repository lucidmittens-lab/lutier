"""
video_export.py -- encodes a graded video to disk via PyAV. Qt-free (see
module docstring convention every other backend module here follows) --
the per-frame CPU decode-grade-encode loop lives here and is safe to
drive from ANY thread, including a background QThread (see
main_window.py's own Export Video dialog: the CPU path runs this via a
worker thread, the GPU path instead drives VideoEncoder.write_frame
directly from a UI-thread-chunked loop, bypassing export_video_cpu
entirely since GPU-graded frames never go through
stack.apply_stack_to_image_array at all). GPU-path rendering itself
(gpu_stack_renderer.GpuStackRenderer, GL-context-tied) is deliberately
NOT here -- it belongs at the UI layer, same "Qt-free backend, GUI owns
anything GL/thread-related" split this whole app follows.

CODEC PRESETS -- ProRes 4444 (yuv444p10le container, .mov) as the
bit-depth-capable default when the local PyAV/ffmpeg build has it
available; H.264 (yuv420p, .mp4) as the universal 8-bit-only fallback.
Probed via is_codec_available at dialog-open time (see main_window.py)
rather than assumed -- a PyAV build genuinely may not have every codec
compiled in. Both verified directly (real encode -> real decode round
trip) before this module shipped: ProRes 4444 preserves fine 16-bit-
sourced gradient steps through its 10-bit encode (not crushed to 256
levels), H.264/yuv420p matches video_source.py's own tested convention.

BIT DEPTH FOR VIDEO -- capped at 12-bit (ProRes 4444's own real ceiling
here); "32f" isn't offered for video at all -- no codec this module
offers can carry float samples, unlike image_io.py's EXR path for
stills. Quantization reuses image_io.quantize_to_bit_depth directly
(not a second, independently-maintained copy of that same logic) --
10/12-bit values are re-expressed across the full UINT16 sample range
before encode, matching image_io's own documented DPX/TIFF convention,
and PyAV's rgb48le input format expects exactly that same convention.

Same rule as every other backend module in this app: no PySide6 import
here, fully testable without a display.
"""
import av
import numpy as np

import image_io
import stack as st

CODEC_PRESETS = [
    {
        "label": "ProRes 4444 (.mov)",
        "extension": ".mov",
        "codec_name": "prores_ks",
        "pix_fmt": "yuv444p10le",
        "options": {"profile": "4"},
        "max_bit_depth": 12,
    },
    {
        "label": "H.264 (.mp4)",
        "extension": ".mp4",
        "codec_name": "libx264",
        "pix_fmt": "yuv420p",
        "options": {},
        "max_bit_depth": 8,
    },
]

DEFAULT_CODEC_LABEL = "ProRes 4444 (.mov)"

# Video export's own real ceiling (see module docstring) -- "32f" and any
# depth beyond what ANY offered codec preset supports are rejected by
# VideoEncoder rather than silently downgraded.
MAX_SUPPORTED_BIT_DEPTH = 12


def is_codec_available(codec_name):
    return codec_name in av.codec.codecs_available


def available_codec_presets():
    """CODEC_PRESETS filtered to what this PyAV/ffmpeg build can actually
    encode -- see is_codec_available. Always includes at least one entry
    in practice (libx264 ships in virtually every ffmpeg build), but
    callers should still handle an empty list rather than assume."""
    return [preset for preset in CODEC_PRESETS if is_codec_available(preset["codec_name"])]


def get_codec_preset(label):
    for preset in CODEC_PRESETS:
        if preset["label"] == label:
            return preset
    raise ValueError(f"Unknown codec preset {label!r} (expected one of "
                      f"{[p['label'] for p in CODEC_PRESETS]})")


def _fraction_from_float(value, max_denominator=1000):
    """A simple, dependency-free float -> (numerator, denominator) pair
    for av.Fraction (pixel aspect ratio is written as container metadata
    only, no anamorphic resampling -- see module docstring)."""
    from fractions import Fraction
    return Fraction(value).limit_denominator(max_denominator)


def _ndarray_format_for_bit_depth(bit_depth):
    if bit_depth == 8:
        return "rgb24"
    if bit_depth in (10, 12, 16):
        return "rgb48le"
    raise ValueError(
        f"video export doesn't support bit_depth={bit_depth!r} -- no offered codec "
        f"can carry it (see MAX_SUPPORTED_BIT_DEPTH)")


class VideoEncoder:
    """Wraps PyAV's write-side API -- open once, write_frame() any number
    of already-graded (H,W,3) float32 arrays (already resized to this
    encoder's own width/height -- this class does NOT resize, that's the
    caller's job, see stack.apply_stack_to_image_array's own "runs on
    whatever's handed to it" convention), close() once. `bit_depth` (one
    of canvas.BIT_DEPTHS, capped by the caller to the chosen codec
    preset's own max_bit_depth -- this class trusts what it's given, see
    module docstring) drives per-frame quantization via
    image_io.quantize_to_bit_depth, reused directly rather than
    duplicated."""

    def __init__(self, output_path, width, height, frame_rate, codec_preset,
                 bit_depth=8, pixel_aspect_ratio=1.0):
        if bit_depth > codec_preset["max_bit_depth"]:
            raise ValueError(
                f"bit_depth={bit_depth} exceeds {codec_preset['label']}'s own "
                f"max_bit_depth={codec_preset['max_bit_depth']}")
        self._container = av.open(str(output_path), mode="w")
        # av.add_stream's own `rate` wants something with .numerator/
        # .denominator (a Fraction, or a plain int -- ints satisfy that
        # protocol too) -- a genuine Python float (e.g. VideoSource.
        # frame_rate, which IS a real float) raises AttributeError deep
        # inside PyAV's own to_avrational, confirmed directly (a real bug
        # caught by an end-to-end smoke test, not by this module's own
        # unit tests, which only ever passed plain int frame rates).
        self._stream = self._container.add_stream(
            codec_preset["codec_name"], rate=_fraction_from_float(frame_rate))
        self._stream.width = width
        self._stream.height = height
        self._stream.pix_fmt = codec_preset["pix_fmt"]
        if codec_preset["options"]:
            self._stream.codec_context.options = dict(codec_preset["options"])
        self._stream.sample_aspect_ratio = _fraction_from_float(pixel_aspect_ratio)
        self._target_pix_fmt = codec_preset["pix_fmt"]
        self._ndarray_format = _ndarray_format_for_bit_depth(bit_depth)
        self._bit_depth = bit_depth
        self._frame_index = 0
        self._closed = False

    @property
    def frame_index(self):
        """How many frames have been written so far -- also the pts of
        the NEXT frame write_frame will encode."""
        return self._frame_index

    def write_frame(self, rgb_array):
        """`rgb_array`: (H,W,3) float32, matching this encoder's own
        width/height exactly (caller's job to resize beforehand -- see
        class docstring)."""
        quantized, _oiio_type = image_io.quantize_to_bit_depth(rgb_array, self._bit_depth)
        frame = av.VideoFrame.from_ndarray(quantized, format=self._ndarray_format)
        frame = frame.reformat(format=self._target_pix_fmt)
        frame.pts = self._frame_index
        for packet in self._stream.encode(frame):
            self._container.mux(packet)
        self._frame_index += 1

    def close(self):
        if self._closed:
            return
        for packet in self._stream.encode():
            self._container.mux(packet)
        self._container.close()
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


def export_video_cpu(source, stack, encoder, canvas, start_frame=0, end_frame=None,
                      progress_callback=None, should_cancel=None):
    """The full CPU-path per-frame pipeline: seek/decode each source
    frame, resize to `encoder`'s own (canvas) resolution, run it through
    `stack` via stack.apply_stack_to_image_array (CPU), write the graded
    result via `encoder`. Safe to call from a background thread -- pure
    Qt-free work throughout (see module docstring); the CALLER owns
    `encoder`'s lifecycle (opened before this call, closed after --
    letting a cancelled/failed export still close and produce a valid,
    if incomplete, file).

    `end_frame` (exclusive, like a Python slice) defaults to
    source.frame_count. `progress_callback(frames_done, frames_total)`,
    if given, is called after each frame. `should_cancel()`, if given, is
    checked before each frame -- returning True stops the loop early
    (already-written frames stay in the file, same "no partially-written
    frame" guarantee VideoEncoder.write_frame's own all-or-nothing
    per-frame shape already provides)."""
    end_frame = source.frame_count if end_frame is None else end_frame
    total = max(0, end_frame - start_frame)

    for i, frame_index in enumerate(range(start_frame, end_frame)):
        if should_cancel is not None and should_cancel():
            return
        array = source.seek_to_frame(frame_index) if i == 0 else source.decode_next_frame()
        if array is None:
            break
        if array.shape[1] != canvas.width or array.shape[0] != canvas.height:
            array = image_io.resize_array(array, canvas.width, canvas.height)
        graded = st.apply_stack_to_image_array(stack, array)
        encoder.write_frame(graded)
        if progress_callback is not None:
            progress_callback(i + 1, total)
