"""
video_source.py -- PyAV-backed video read/decode for LUTier's video
support (Tom, 2026-08-13: "Video Support (viewing and rendering)").
Opens a video file, reports its own metadata (frame count, frame rate,
resolution, duration), and decodes individual frames -- both sequential
(decode_next_frame) and random-access (seek_to_frame + decode) -- as
plain (H,W,3) float32 [0,1] numpy arrays, matching every other
image-shaped array convention in this app (image_io.read_image_as_array,
custom_drt.py, the GPU RGBA32F texture pipeline).

DECODE PRECISION -- deliberately 8-bit (PyAV's "rgb24" pixel format) for
this v1, even for higher-bit-depth sources (ProRes/DNxHR 10/12-bit etc):
getting a genuinely bit-depth-aware decode right (chroma subsampling,
color range/matrix handling, HDR pixel format families) is a real rabbit
hole well beyond image_io.py's still-image case, where OIIO's own already
-correct, already-tested container conventions did the hard part. Scoped
down deliberately rather than shipping an unverified guess -- true
bit-depth-aware video decode is a documented follow-up, not an oversight.

Same rule as every other backend module in this app: no PySide6 import
here, fully testable without a display. Realtime PLAYBACK (a QTimer loop
pushing frames to the Viewer) is a separate module,
video_playback_controller.py -- this module only knows how to decode
frames, not how or when to show them.
"""
import av
import numpy as np


class VideoSource:
    """Wraps one open video file/stream. Not thread-safe -- PyAV's own
    container/decoder objects aren't, so a single VideoSource must only
    ever be touched from one thread at a time (matches every other
    non-GL-context-tied backend object in this app)."""

    def __init__(self, path):
        self.path = str(path)
        self._container = av.open(self.path)
        video_streams = self._container.streams.video
        if not video_streams:
            self._container.close()
            raise ValueError(f"{path} has no video stream")
        self._stream = video_streams[0]
        self._decoder = self._container.decode(video=0)

        self.width = self._stream.width
        self.height = self._stream.height
        # average_rate comes back 0/0 for some MXF muxes (ARRI ALEXA Mini
        # LF ProRes 4444, confirmed 2026-10-05) -- with frame_rate 0 the
        # clip had no duration/frame count and seek_to_frame returned
        # None. guessed_rate (ffmpeg's own r_frame_rate/codec fallback)
        # still has the real 24/1.
        rate = self._stream.average_rate or self._stream.guessed_rate
        self.frame_rate = float(rate) if rate else 0.0
        self.frame_count = self._detect_frame_count()
        self.duration = (self.frame_count / self.frame_rate) if self.frame_rate else 0.0

    def _detect_frame_count(self):
        """`self._stream.frames` (container metadata) when present and
        nonzero -- some containers/muxes don't populate this at all, in
        which case fall back to duration/time_base * frame_rate, rounded.
        Both are ESTIMATES in the general case (VFR sources, edited
        streams with stale metadata) -- good enough for a scrub bar's own
        range, not claimed to be frame-exact for every possible
        container."""
        if self._stream.frames:
            return self._stream.frames
        if self._stream.duration and self._stream.time_base and self.frame_rate:
            seconds = float(self._stream.duration * self._stream.time_base)
            return max(1, round(seconds * self.frame_rate))
        return 0

    def decode_next_frame(self, max_dimension=None):
        """Decodes and returns the NEXT frame in stream order as an
        (H,W,3) float32 [0,1] array, or None at end of stream. Advances
        this source's own internal decode position -- callers that need
        a SPECIFIC frame should call seek_to_frame first, not count
        decode_next_frame calls themselves.

        `max_dimension` (None by default -- full native resolution): when
        given, the frame is reformatted DOWN to at most this size on its
        longer side (aspect-preserving) BEFORE conversion to a numpy
        array -- cheaper than decoding full-res and downsampling after,
        used by video_playback_controller.py for its own realtime-
        playback proxy resolution (see that module's docstring on why
        realtime playback needs a resolution cap that parked-frame
        grading doesn't)."""
        try:
            frame = next(self._decoder)
        except StopIteration:
            return None
        frame = self._downscale_frame(frame, max_dimension)
        return self._frame_to_array(frame)

    def seek_to_frame(self, index, max_dimension=None):
        """Repositions decoding so THIS call returns frame `index`
        directly (unlike decode_next_frame, which only ever advances).
        Seeks to the nearest keyframe at or before `index` (PyAV/ffmpeg's
        own seek granularity) and decodes forward, discarding frames
        until landing exactly on the target -- real cost varies with
        keyframe distance, fine for user-driven scrubbing (one seek per
        drag tick) or an occasional loop-back (video_playback_controller.
        py's in/out+loop transport, Phase 4), not meant for a tight
        per-frame loop (use sequential decode_next_frame for that, e.g.
        realtime playback). Returns the landed-on frame's array, or None
        if `index` is past the end of the stream. Clamped to [0,
        frame_count-1] when frame_count is known (nonzero), otherwise
        clamped to >= 0 only.

        `max_dimension` (None by default -- full native resolution): same
        aspect-preserving downscale-before-array-conversion decode_next_
        frame's own parameter does -- used by the playback controller's
        loop-back so a looped realtime-playback tick stays at proxy
        resolution instead of jumping back to full-res just because it
        went through a seek instead of a sequential decode."""
        if self.frame_count:
            index = max(0, min(index, self.frame_count - 1))
        else:
            index = max(0, index)
        if not self.frame_rate:
            return None
        target_pts = int(round(index / self.frame_rate / self._stream.time_base))
        self._container.seek(target_pts, stream=self._stream, any_frame=False, backward=True)
        self._decoder = self._container.decode(video=0)
        for frame in self._decoder:
            if frame.pts is not None and frame.pts >= target_pts:
                frame = self._downscale_frame(frame, max_dimension)
                return self._frame_to_array(frame)
        return None

    @staticmethod
    def _downscale_frame(frame, max_dimension):
        """Shared by decode_next_frame/seek_to_frame -- aspect-preserving
        reformat down to at most `max_dimension` on the longer side, or a
        no-op if `max_dimension` is None or the frame's already smaller."""
        if max_dimension is not None and max(frame.width, frame.height) > max_dimension:
            scale = max_dimension / max(frame.width, frame.height)
            new_w = max(1, round(frame.width * scale))
            new_h = max(1, round(frame.height * scale))
            frame = frame.reformat(width=new_w, height=new_h)
        return frame

    def _frame_to_array(self, frame):
        rgb = frame.to_ndarray(format="rgb24")
        return rgb.astype(np.float32) / 255.0

    def close(self):
        self._container.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
