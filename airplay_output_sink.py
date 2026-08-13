"""
airplay_output_sink.py -- AirPlayOutputSink: the first concrete
video_output_sink.VideoOutputSink, rendering the Viewer's live graded
output into a real, independent native window at true 10-bit 4:2:0
(kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange -- see
ycbcr_convert.py for the actual RGB->YCbCr math).

REAL FINDING THAT SHAPED THIS DESIGN -- confirmed directly against the
installed AVFoundation/AVKit/CoreMedia/Quartz(CoreVideo) frameworks
(PyObjC), not assumed: there is NO publicly documented (or discoverable
via the actual API surface) way for a THIRD-PARTY app to drive AirPlay
DESTINATION SELECTION for a custom, non-AVPlayer frame source.
`AVRoutePickerView`'s only real hook is `setPlayer:`, which strictly
wants a real `AVPlayer` -- `AVSampleBufferDisplayLayer` (the correct,
documented layer for enqueuing custom `CMSampleBuffer`s, confirmed via
Apple's own "Integrating AirPlay for long-form video apps" doc) has NO
routing-related API of its own at all (checked directly: no "route"/
"airplay"/"external" members). So this sink does NOT attempt in-app
AirPlay device selection.

SECOND REAL FINDING, from Tom's own live testing (2026-08-11) -- macOS's
Control Center "Screen Mirroring" picker does NOT list individual windows
either, contradicting this module's own earlier assumption. It offers
exactly two choices: "Mirror" (the whole desktop) or "Use As Separate
Display" (a genuine new NSScreen, extending the desktop onto the AirPlay
receiver). Plain whole-screen Mirror shows the receiver everything on
the Mac's display, not a clean isolated feed. "Use As Separate Display"
is the one that actually gets a clean feed: drag this window onto that
new screen and fullscreen it there, and the receiver shows ONLY this
window's content. This module does not (yet) automate that drag/
fullscreen step -- Tom explicitly declined that follow-up for now (see
VIEWER_CASTING_PLAN_2026-08-11.md).

WHAT THIS MODULE ACTUALLY DOES -- opens a small, dedicated, chrome-free
native NSWindow (title "LUTier — Independent Viewer") whose content is an
AVSampleBufferDisplayLayer showing the live graded frame at true 10-bit
4:2:0. What the user does with that window (drag it to a "Use As
Separate Display" AirPlay screen and fullscreen it, or just use it as a
plain second monitor output, or anything else) is entirely up to them --
the actual wireless transport, HEVC encoding, and receiver negotiation
(when AirPlay is involved) is handled entirely by macOS's own per-screen
mirroring pipeline, not hand-rolled here. This is a deliberate, considered
narrowing from "in-app Cast button with a route picker" (the original
framing) to "a clean, independent native window showing the graded
output" -- a real, buildable, honest feature, found by actually trying
the documented custom-content path (and then Tom's own live testing)
rather than assumed possible without checking either time.

VERIFIED END TO END IN THIS ENVIRONMENT (no AirPlay receiver available
here, so real wireless casting itself is NOT verified -- see this
module's own is_available()/manual-testing notes and
VIEWER_CASTING_PLAN_2026-08-11.md):
  - CVPixelBufferCreate with kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange
    succeeds, reports 2 planes at the expected 4:2:0 dimensions, and its
    own PixelFormatDescription metadata (BitsPerComponent=10, plane 0
    BitsPerBlock=16, plane 1 BitsPerBlock=32 with 2x/2x subsampling)
    independently CONFIRMS ycbcr_convert.py's packing/layout assumptions
    (16-bit-per-luma-sample, interleaved-CbCr-pairs chroma) match the
    pixel format's real declared layout, not just this module's guess
    about it.
  - Writing numpy plane data into a locked CVPixelBuffer's memory (via
    CVPixelBufferGetBaseAddressOfPlane(...).as_buffer(...), a writable
    memoryview) and reading it back confirms real, correct memory
    writes, respecting each plane's own bytesPerRow (which can exceed
    the tightly-packed row width -- IOSurface-backed buffers routinely
    pad for GPU alignment).
  - CMVideoFormatDescriptionCreateForImageBuffer ->
    CMSampleBufferCreateForImageBuffer -> AVSampleBufferDisplayLayer.
    enqueueSampleBuffer_ all succeed (status 0), and the layer reports
    status()==1 (rendering) with error()==None after enqueuing.
  - NSWindow + NSView(wantsLayer) + AVSampleBufferDisplayLayer as a
    sublayer constructs without error and rides on the same Cocoa run
    loop PySide6/Qt already runs on macOS (Qt IS a Cocoa app under the
    hood) -- no separate NSApplication run loop needed.
"""
import sys
import time

import numpy as np

import video_output_sink
import ycbcr_convert

try:
    import Cocoa
    import AVFoundation
    import CoreMedia
    import Quartz
    PYOBJC_AVAILABLE = True
    PYOBJC_IMPORT_ERROR = None
except ImportError as e:  # pragma: no cover -- exercised on non-macOS or without PyObjC installed
    Cocoa = AVFoundation = CoreMedia = Quartz = None
    PYOBJC_AVAILABLE = False
    PYOBJC_IMPORT_ERROR = str(e)

_WINDOW_TITLE = "LUTier — Independent Viewer"
_DEFAULT_WINDOW_SIZE = (960, 540)
_NOMINAL_FRAME_DURATION_SECONDS = 1.0 / 30.0  # only a CMTime hint -- see push_frame's own comment


def _require_pyobjc():
    if not PYOBJC_AVAILABLE:
        raise RuntimeError(
            "PyObjC (with the AVFoundation/AVKit/CoreMedia/Quartz framework "
            f"bindings) isn't available (import error: {PYOBJC_IMPORT_ERROR}). "
            "Install with: pip3 install pyobjc-core pyobjc-framework-Cocoa "
            "pyobjc-framework-AVFoundation pyobjc-framework-AVKit "
            "pyobjc-framework-CoreMedia pyobjc-framework-Quartz"
        )


def _write_plane(pixel_buffer, plane_index, packed_uint16_array):
    """Copies `packed_uint16_array` ((H, W) or (H, W, 2) uint16, already
    10-bit-packed-into-16-bit -- see ycbcr_convert.py) into
    `pixel_buffer`'s plane `plane_index`, respecting that plane's own
    bytesPerRow (see module docstring's "VERIFIED END TO END" section --
    this can legitimately exceed the tightly-packed row width). Caller
    must already hold the pixel buffer's lock."""
    bytes_per_row = Quartz.CVPixelBufferGetBytesPerRowOfPlane(pixel_buffer, plane_index)
    plane_height = Quartz.CVPixelBufferGetHeightOfPlane(pixel_buffer, plane_index)
    base = Quartz.CVPixelBufferGetBaseAddressOfPlane(pixel_buffer, plane_index)
    dest_buf = base.as_buffer(bytes_per_row * plane_height)
    dest = np.frombuffer(dest_buf, dtype=np.uint8).reshape(plane_height, bytes_per_row)

    src_bytes = np.ascontiguousarray(packed_uint16_array).view(np.uint8).reshape(plane_height, -1)
    row_bytes = src_bytes.shape[1]
    dest[:, :row_bytes] = src_bytes


def _build_pixel_buffer(y_plane, cbcr_plane):
    """(y_plane, cbcr_plane) from ycbcr_convert.rgb_to_yuv420p10_planes ->
    a real CVPixelBuffer (kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange),
    IOSurface-backed (required for AirPlay/window-compositor
    compatibility -- see kCVPixelBufferIOSurfacePropertiesKey below).
    Raises RuntimeError with the real CVReturn code on failure rather
    than returning None, so a construction failure surfaces immediately
    instead of a confusing later AttributeError."""
    height, width = y_plane.shape
    attrs = {Quartz.kCVPixelBufferIOSurfacePropertiesKey: {}}
    status, pixel_buffer = Quartz.CVPixelBufferCreate(
        None, width, height, Quartz.kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange, attrs, None)
    if status != 0:
        raise RuntimeError(f"CVPixelBufferCreate failed with CVReturn {status}")

    Quartz.CVPixelBufferLockBaseAddress(pixel_buffer, 0)
    try:
        _write_plane(pixel_buffer, 0, y_plane)
        _write_plane(pixel_buffer, 1, cbcr_plane)
    finally:
        Quartz.CVPixelBufferUnlockBaseAddress(pixel_buffer, 0)
    return pixel_buffer


def _build_sample_buffer(pixel_buffer, presentation_seconds, duration_seconds):
    """pixel_buffer -> a CMSampleBuffer ready for
    AVSampleBufferDisplayLayer.enqueueSampleBuffer_ -- see module
    docstring's "VERIFIED END TO END" section for confirmation this
    exact call sequence succeeds against the real frameworks."""
    fmt_status, format_desc = CoreMedia.CMVideoFormatDescriptionCreateForImageBuffer(None, pixel_buffer, None)
    if fmt_status != 0:
        raise RuntimeError(f"CMVideoFormatDescriptionCreateForImageBuffer failed with status {fmt_status}")

    # Timescale 90000 -- the conventional MPEG/video timebase, fine
    # resolution for any plausible edit-driven push rate this app uses
    # (not a fixed-framerate video source, see push_frame's own comment).
    timescale = 90000
    timing = CoreMedia.CMSampleTimingInfo()
    timing.duration = CoreMedia.CMTimeMake(int(round(duration_seconds * timescale)), timescale)
    timing.presentationTimeStamp = CoreMedia.CMTimeMake(int(round(presentation_seconds * timescale)), timescale)
    timing.decodeTimeStamp = CoreMedia.kCMTimeInvalid

    sb_status, sample_buffer = CoreMedia.CMSampleBufferCreateForImageBuffer(
        None, pixel_buffer, True, None, None, format_desc, timing, None)
    if sb_status != 0:
        raise RuntimeError(f"CMSampleBufferCreateForImageBuffer failed with status {sb_status}")
    return sample_buffer


class AirPlayOutputSink(video_output_sink.VideoOutputSink):
    """See module docstring for what this actually renders (a dedicated
    independent native window -- "LUTier — Independent Viewer") and why
    there's no in-app AirPlay device picker (no documented route-
    selection API exists for a custom, non-AVPlayer frame source)."""

    def __init__(self):
        self._window = None
        self._display_layer = None
        self._last_frame_size = None  # (width, height) of the last pixel buffer built, to detect a resize
        self._last_push_time = None
        self._start_time = None

    @property
    def name(self):
        return "Independent Viewer Window"

    def is_available(self):
        return PYOBJC_AVAILABLE and sys.platform == "darwin"

    def start(self):
        _require_pyobjc()
        if self._window is not None:
            return  # already started -- see VideoOutputSink's own docstring on idempotent start()

        width, height = _DEFAULT_WINDOW_SIZE
        rect = Cocoa.NSMakeRect(200, 200, width, height)
        style = (Cocoa.NSWindowStyleMaskTitled | Cocoa.NSWindowStyleMaskClosable
                 | Cocoa.NSWindowStyleMaskResizable | Cocoa.NSWindowStyleMaskMiniaturizable)
        window = Cocoa.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, Cocoa.NSBackingStoreBuffered, False)
        window.setTitle_(_WINDOW_TITLE)
        # Released when explicitly closed, not when the user clicks the
        # red close button while this Python object is still alive --
        # this sink's own stop() (or the user re-triggering it) is what
        # actually tears the window down; a stray click shouldn't leave
        # this object holding a dangling native window reference.
        window.setReleasedWhenClosed_(False)

        content_view = Cocoa.NSView.alloc().initWithFrame_(rect)
        content_view.setWantsLayer_(True)
        content_view.layer().setBackgroundColor_(Cocoa.NSColor.blackColor().CGColor())

        display_layer = AVFoundation.AVSampleBufferDisplayLayer.alloc().init()
        display_layer.setFrame_(content_view.bounds())
        display_layer.setVideoGravity_(AVFoundation.AVLayerVideoGravityResizeAspect)
        # Autoresize with the window -- AVSampleBufferDisplayLayer itself
        # has no autolayout/constraint API of its own, so this uses the
        # older autoresizing-mask mechanism (still fully supported) to
        # keep it filling content_view on a window resize.
        display_layer.setAutoresizingMask_(
            Quartz.kCALayerWidthSizable | Quartz.kCALayerHeightSizable)
        content_view.layer().addSublayer_(display_layer)

        window.setContentView_(content_view)
        window.orderFront_(None)  # NOT makeKeyAndOrderFront_ -- shows the window without stealing focus/keyboard from the main LUTier window

        self._window = window
        self._display_layer = display_layer
        self._last_frame_size = None
        self._start_time = time.monotonic()
        self._last_push_time = None

    def push_frame(self, frame):
        if self._display_layer is None:
            return  # before start()/after stop() -- see VideoOutputSink's own docstring

        y_plane, cbcr_plane = ycbcr_convert.rgb_to_yuv420p10_planes(frame.rgb)
        pixel_buffer = _build_pixel_buffer(y_plane, cbcr_plane)

        now = time.monotonic()
        presentation_seconds = now - self._start_time
        # Real elapsed time since the last push when available (this is
        # an edit-driven "current state" feed, not a fixed-framerate
        # source -- pushes arrive whenever the Stack/Viewer actually
        # changes, not on a metronome), falling back to a nominal
        # duration for the very first frame, which has no "since last
        # push" to measure.
        duration_seconds = (now - self._last_push_time) if self._last_push_time is not None \
            else _NOMINAL_FRAME_DURATION_SECONDS
        self._last_push_time = now

        sample_buffer = _build_sample_buffer(pixel_buffer, presentation_seconds, duration_seconds)
        self._display_layer.enqueueSampleBuffer_(sample_buffer)
        self._last_frame_size = (frame.width, frame.height)

    def stop(self):
        if self._window is not None:
            self._window.close()
        self._window = None
        self._display_layer = None
        self._last_frame_size = None
        self._last_push_time = None
        self._start_time = None
