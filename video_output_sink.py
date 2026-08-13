"""
video_output_sink.py -- backend-agnostic interface for pushing the
Viewer's live graded output to an external video destination.

TWO PLANNED BACKENDS, ONE SHAPE: AirPlay (airplay_output_sink.py, built
now) and BMD UltraStudio/DeckLink SDI output (planned follow-up, not
built yet) are both fundamentally "push rendered frames to an external
video destination" -- this module is the shared interface both fit,
kept deliberately free of any AirPlay-specific (CVPixelBuffer/
CMSampleBuffer/AVFoundation) or DeckLink-specific (Blackmagic's own C++
SDK types) vocabulary, so casting_controller.py -- the code that DRIVES
whichever sink is active -- never needs to know which backend it's
talking to, and a future DeckLinkOutputSink can implement this exact
interface without this module or casting_controller.py changing at all.
Same "Qt-free math module + thin per-destination wrapper" split this app
already uses everywhere (aces_drt.py/cdl.py/printer_lights.py/...),
just generalized to "sink-free frame producer + thin per-destination
sink" instead of "Qt-free."

FRAME FORMAT -- VideoFrame carries a plain (H, W, 3) float32 numpy array,
RGB, matching this app's own internal GPU-readback convention (see e.g.
wipe_compare_widget.py's/gpu_stack_renderer.py's own texture readbacks) --
NOT yet converted to any destination-specific pixel format (10-bit 4:2:0
YCbCr for AirPlay, whatever DeckLink's own SDK wants). Each concrete sink
converts internally (see ycbcr_convert.py, which airplay_output_sink.py
uses) -- keeping that conversion OUT of this interface is exactly what
lets a DeckLink sink use a completely different pixel format later
without touching this module.

Values in `rgb` are NOT clamped to [0, 1] here -- an HDR ACES DRT view
can legitimately produce values outside that range (see aces_drt.py's
own docstring), same "don't silently discard legitimate range" rule this
app follows everywhere else. Clamping/tone-mapping for a specific
destination's actual dynamic range is that sink's own job, not this
interface's.
"""
import abc
from dataclasses import dataclass

import numpy as np


@dataclass
class VideoFrame:
    """One frame ready to push to a VideoOutputSink. `rgb` is (H, W, 3)
    float32, RGB -- see module docstring. `timestamp_seconds` is a
    monotonically increasing float (wall-clock time is fine); concrete
    sinks that need a real presentation timestamp (e.g.
    AirPlayOutputSink's CMSampleBuffer) derive their own CMTime from it."""
    rgb: np.ndarray
    timestamp_seconds: float

    def __post_init__(self):
        self.rgb = np.ascontiguousarray(np.asarray(self.rgb, dtype=np.float32))
        if self.rgb.ndim != 3 or self.rgb.shape[2] != 3:
            raise ValueError(f"VideoFrame.rgb must be (H, W, 3), got {self.rgb.shape}")

    @property
    def height(self):
        return self.rgb.shape[0]

    @property
    def width(self):
        return self.rgb.shape[1]


class VideoOutputSink(abc.ABC):
    """One external video destination. Lifecycle: start() once, then
    push_frame() for every new frame while casting is active, then
    stop(). push_frame() before start()/after stop() is a silent no-op,
    not an error -- so casting_controller.py doesn't need to track sink
    lifecycle state redundantly on top of its own "is a cast active"
    flag; it can just always forward frames to whichever sink is
    currently selected."""

    @property
    @abc.abstractmethod
    def name(self):
        """Human-readable name for this sink kind, e.g. "Independent
        Viewer Window" -- shown in the Viewer menu/status text."""

    @abc.abstractmethod
    def is_available(self):
        """True if this sink's backend dependencies are present (e.g.
        PyObjC + the AVFoundation/AVKit frameworks installed) and it's
        usable on this platform -- checked BEFORE offering this sink in
        the UI, not discovered by a failed start()."""

    @abc.abstractmethod
    def start(self):
        """Begin a session -- e.g. opens AirPlayOutputSink's own
        independent viewer window. Safe to call again while already
        started (a no-op)."""

    @abc.abstractmethod
    def push_frame(self, frame):
        """Send one VideoFrame to the destination. Safe to call before
        start()/after stop() -- silently dropped, not an error."""

    @abc.abstractmethod
    def stop(self):
        """End the session and release any destination-specific
        resources (e.g. AirPlayOutputSink's own window, a future
        DeckLink device handle). Safe to call again while already
        stopped (a no-op)."""
