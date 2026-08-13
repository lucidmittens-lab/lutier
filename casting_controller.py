"""
casting_controller.py -- CastingController: fans a SINGLE shared graded
frame out to zero or more currently-active video_output_sink.
VideoOutputSink instances. Wired into sample_image_panel.py's Viewer
"..." menu ("Open Independent Viewer Window").

THE FORK-3-WAYS DESIGN (Tom's own explicit request, 2026-08-12) -- the
Stack's processed picture forks three ways from ONE shared source: the
Viewer panel itself (always), the Independent Viewer window (if the user
enables it), and a future BMD UltraStudio/DeckLink SDI output (if the
user enables THAT, once it exists). All three show the exact same
pixels; none of them re-derive it independently. `frame_getter` (a
zero-arg callable, see __init__) is that ONE shared source -- wired to
wipe_compare_widget.WipeCompareWidget.read_back_after_frame, which reads
back whatever the Viewer panel ITSELF just painted (see that method's own
docstring: correct for both the GPU-eligible and GPU-ineligible case,
since a GPU-ineligible stack's "graded_texture" is already the CPU-graded
result, uploaded verbatim). `_sinks` is a plain list, not a single slot,
specifically so the Independent Viewer and a future DeckLink sink can
both be active AT THE SAME TIME, each getting the identical frame.

SUPERSEDES AN EARLIER VERSION (2026-08-11) that ran its own independent
stack.apply_stack_to_image_array CPU recompute instead of reading the
Viewer's own rendered frame -- built that way specifically to avoid
adding readback plumbing to WipeCompareWidget.paintGL, the single most
hardened, crash-prone hot path in this codebase (see that module's own
docstring on the real, reproduced Shiboken/SIGSEGV crash this session
fixed there earlier). Tom explicitly asked for the shared-source design
instead, after confirming the independent-CPU-recompute version was
noticeably slow to update. The actual implementation still doesn't touch
paintGL's own code path: WipeCompareWidget.read_back_after_frame is a
wholly separate, ON-DEMAND method (makeCurrent()/doneCurrent() bracketing
its own GL calls) that reads whatever texture the LAST real paint already
left cached -- it costs the interactive-editing hot path nothing when
nothing is polling it, and this controller only polls it from its own
throttle logic below, never from inside a paint callback.

THROTTLED, not debounced (see _THROTTLE_SECONDS) -- a slider drag can
still fire stack_changed on every tick, and pushing a fresh frame through
the whole YCbCr-convert/CVPixelBuffer/CMSampleBuffer pipeline on every
single tick (see airplay_output_sink.py) is real, non-zero, per-consumer
work this controller shouldn't force on every tick just because SOMETHING
is listening. An EARLIER version of this method used a pure debounce
(QTimer.setSingleShot(True), restarted via .start() on every call) --
Tom reported that behavior directly (2026-08-12): "way faster to update
but seems to only happen once i stop changing a parameter". Root cause:
calling QTimer.start() on an already-running singleShot timer resets its
countdown (standard Qt behavior), so a continuous burst of calls (every
tick of a drag) kept postponing the fire forever, and the timer only
actually ran once ticks stopped arriving for a full _THROTTLE_SECONDS --
textbook debounce (wait for quiet, fire once), not what a *live* cast
window wants (fire periodically DURING the activity too). Fixed by a
leading+trailing throttle instead: on_stack_or_image_changed() pushes
IMMEDIATELY if _THROTTLE_SECONDS has already elapsed since the last real
push (leading edge -- this is what makes a drag update periodically, not
just once at the end), otherwise it schedules exactly ONE trailing push
for whenever the throttle window closes (so the LAST edit in a burst is
never silently dropped, same guarantee the old debounce gave, just no
longer the ONLY time it fires).

NOT clamped to [0,1] here -- an HDR ACES DRT Viewer output can
legitimately exceed that range (see aces_drt.py's own docstring), and
read_back_after_frame's own contract is to hand back the unclamped GPU
result, matching render_chain's "don't silently discard legitimate
range" rule; ycbcr_convert.py's own digital-quantization step (inside
airplay_output_sink.py) already clips correctly downstream, so clamping
here first would just be redundant, not safer.
"""
import time

from PySide6.QtCore import QObject, QTimer

import video_output_sink

_THROTTLE_SECONDS = 0.25  # deliberately its own constant, not sample_image_panel.EDIT_SETTLE_DEBOUNCE_MS -- a decoupled concern; at most 4 pushed frames/sec per sink during continuous editing


class CastingController(QObject):
    """`frame_getter`: a zero-arg callable returning the Viewer's
    current graded (H, W, 3) float32 RGB frame, or None if there's
    nothing to show yet -- see module docstring on why this is the ONE
    shared source every active sink is fed from."""

    def __init__(self, frame_getter, parent=None):
        super().__init__(parent)
        self._frame_getter = frame_getter
        self._sinks = []  # every currently-active VideoOutputSink -- see module docstring on why a list, not one slot
        self._last_push_time = None  # time.monotonic() of the last actual push, or None before the first one
        self._trailing_timer = QTimer(self)
        self._trailing_timer.setSingleShot(True)
        self._trailing_timer.timeout.connect(self._push_to_all_sinks)

    @property
    def is_casting(self):
        return len(self._sinks) > 0

    def start_sink(self, sink):
        """`sink`: a fresh, not-yet-started video_output_sink.VideoOutputSink
        instance (e.g. airplay_output_sink.AirPlayOutputSink()) -- this
        controller calls start() on it and owns its lifecycle from here.
        A no-op if `sink` (by identity) is already active.

        Pushes an initial frame to JUST this sink, not every active
        sink -- an earlier version called _push_to_all_sinks() here,
        which meant starting a SECOND sink while a first one was already
        running re-pushed a redundant (if harmless) extra frame to that
        first one too, found by this module's own tests."""
        if sink in self._sinks:
            return
        sink.start()
        self._sinks.append(sink)
        frame = self._current_frame()
        if frame is not None:
            self._last_push_time = time.monotonic()
            sink.push_frame(frame)

    def stop_sink(self, sink):
        """Stops and removes ONE specific sink, leaving any others (e.g.
        a future DeckLink sink active alongside this one) untouched. A
        no-op if `sink` isn't currently active."""
        if sink not in self._sinks:
            return
        sink.stop()
        self._sinks.remove(sink)

    def stop_all(self):
        """Stops every active sink -- e.g. MainWindow.closeEvent, so the
        app never quits with a stray window (or a future stray hardware
        output) still running."""
        for sink in list(self._sinks):
            sink.stop()
        self._sinks.clear()
        self._trailing_timer.stop()

    def on_stack_or_image_changed(self):
        """Call on every stack_changed / new-image-loaded event, same
        call sites refresh_preview() already has -- a cheap no-op
        (doesn't even touch the timer) when no sink is active, see
        video_output_sink.VideoOutputSink's own "push_frame is always
        safe to call, sinks handle their own on/off state" convention,
        mirrored here at the controller level too so callers never need
        to check is_casting themselves first.

        THROTTLE, not debounce -- see module docstring for why. If at
        least _THROTTLE_SECONDS has passed since the last real push,
        pushes right now (leading edge) and cancels any pending trailing
        push it just preempted. Otherwise, schedules exactly one trailing
        push for whenever the throttle window closes, so a burst of calls
        collapses into a single pending push rather than one per call."""
        if not self._sinks:
            return
        now = time.monotonic()
        if self._last_push_time is None or (now - self._last_push_time) >= _THROTTLE_SECONDS:
            self._trailing_timer.stop()
            self._push_to_all_sinks()
        else:
            remaining_seconds = _THROTTLE_SECONDS - (now - self._last_push_time)
            self._trailing_timer.start(max(1, int(remaining_seconds * 1000)))

    def _current_frame(self):
        rgb = self._frame_getter()
        if rgb is None:
            return None
        return video_output_sink.VideoFrame(rgb=rgb, timestamp_seconds=time.monotonic())

    def _push_to_all_sinks(self):
        if not self._sinks:
            return
        frame = self._current_frame()
        if frame is None:
            return
        self._last_push_time = time.monotonic()
        for sink in self._sinks:
            sink.push_frame(frame)
