"""
video_playback_controller.py -- VideoPlaybackController: realtime
playback of a SampleImagePanel's currently-open VideoSource, pushed
directly into the Viewer WITH the current grade applied -- but the grade
itself is LOCKED (not editable) for the duration of playback (Tom,
2026-08-13, clarifying the original scope: "i meant realtime playback
without the ability to modify the grade as playback is going. playback
should still show the grade"). MainWindow locks the Stack panel via
playback_started/playback_stopped (see those signals below); this
controller's own job is just showing the right, already-fixed grade on
each frame as it plays, and unlocking cleanly on stop.

GRADING PER TICK mirrors sample_image_panel.py's OWN live-preview split
exactly (see that module's _render_wipe/_array_and_gpu_stack_for_source):
a GPU-eligible, non-empty stack pushes the RAW decoded frame with
gpu_stack=stack, so WipeCompareWidget grades it live on the GPU inside
its own paintGL -- the same fast path stills already use, so realtime
playback stays realtime. A GPU-INELIGIBLE stack instead grades the frame
on the CPU (stack.apply_stack_to_image_array) before pushing it with
gpu_stack=None -- slower, a real, inherent limitation of a CPU-bound
per-frame grade at playback frame rates, not something this module
tries to hide or work around.

PERF: pushes DECODE_PROXY_MAX_DIMENSION-capped frames (see
VideoSource.decode_next_frame's own max_dimension parameter) while
actively playing, not full native resolution -- every decoded video
frame is a genuinely NEW array object, and WipeCompareWidget's own
_array_fingerprint is an id()-based identity check (see that module's
own docstring), so EVERY tick forces a full glTexImage2D upload
regardless of resolution; capping resolution during active playback
keeps that upload cheap enough to sustain realtime frame rates. This is
NOT the removed 2026-08-09 stills-downres mechanism resurrected -- that
removal was about a settled-state artifact with a clear "stop editing"
moment; playback has no such moment (new frames arrive continuously for
as long as it plays), so a resolution cap scoped to "while actively
ticking" is a different, still-justified tradeoff. Pausing immediately
re-parks at full native resolution through the normal grading pipeline
(see pause()).

Same "own QObject, own QTimer" shape as casting_controller.
CastingController -- a separate concern from that controller's own frame
FANOUT job (this one PRODUCES frames by decoding, doesn't distribute an
already-produced one).
"""
import time

from PySide6.QtCore import QObject, QTimer, Signal, Qt

import gpu_stack_renderer
import stack as st
from frame_server import FrameServer, Segment

# Long-side cap for the decoded proxy pushed to the Viewer WHILE playing
# -- see module docstring's PERF section. Not user-configurable (yet);
# generous enough to still look sharp on typical displays, small enough
# to keep the per-tick texture upload cheap.
DECODE_PROXY_MAX_DIMENSION = 1280

PLAYBACK_STATUS_TEXT = "PLAYBACK — grade locked"

# Speed control (Phase 4 transport upgrade) -- see set_speed's own
# docstring for how each preset actually changes tick behavior.
SPEED_PRESETS = (0.5, 1.0, 2.0, 4.0)
DEFAULT_SPEED = 1.0


class VideoPlaybackController(QObject):
    """`panel`: the SampleImagePanel this controller plays into -- reads
    panel._video_source/.compare/.status_label/._stack_getter directly
    (an internal collaborator, not a public API surface of that panel)
    and calls panel.seek_video_frame on pause to re-park at full
    resolution, matching the "own module, tight collaboration with one
    specific panel" shape casting_controller.py already uses for the
    SAME panel.

    Emits frame_changed(frame_index) on every ticked frame (the
    transport UI's own scrub position while playing -- panel's own
    _video_frame_index is deliberately NOT updated during playback, only
    on pause/step/scrub, see pause()'s own docstring), playback_started()
    when playback actually begins, and playback_stopped() whenever
    playback stops for ANY reason (explicit pause, end of stream, or no
    video source) -- MainWindow connects these two to lock/unlock the
    Stack panel for the duration of playback."""

    frame_changed = Signal(int)
    playback_started = Signal()
    playback_stopped = Signal()

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self._panel = panel
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._on_timer)
        self._frame_index = 0
        self._playing = False
        # Decodes ahead on a worker thread while playing (frame_server.py);
        # the play range [in_point, out limit] is its one segment.
        self._frame_server = FrameServer(DECODE_PROXY_MAX_DIMENSION)
        self._clock_start = None
        self._frames_advanced = 0
        # In/out points + loop (Phase 4 transport upgrade) -- in_point/
        # out_point define the PLAYBACK RANGE [in_point, out_point]
        # inclusive; out_point=None means "the end of the clip" (resolved
        # lazily against the open source's own frame_count in _out_limit,
        # since no source may be open yet when this controller is
        # constructed). Both are frame indices, not seconds/timecode.
        self.in_point = 0
        self.out_point = None
        self.loop_enabled = False
        # Speed control (Phase 4) -- see set_speed's own docstring.
        # _base_interval_ms is None until the first play() (needs
        # source.frame_rate, not known before a video's even open).
        self.speed = DEFAULT_SPEED
        self._base_interval_ms = None

    @property
    def is_playing(self):
        return self._playing

    def play(self):
        """No-op if already playing, or if the panel has no video open.
        Always resumes from wherever parked-frame stepping/scrubbing
        last left the panel (panel._video_frame_index) -- re-seeks the
        DECODER itself (not just this controller's own bookkeeping), so
        the first ticked frame is genuinely the one right after the
        current park point, even if the user scrubbed elsewhere since
        the last time playback ran. Clamped into [in_point, out_limit]
        first (Phase 4 in/out points) -- starting play from OUTSIDE the
        active range (e.g. parked past out_point from an earlier scrub)
        would otherwise end the clip on tick 1, which isn't useful."""
        if self._playing:
            return
        source = self._panel._video_source
        if source is None or not source.frame_rate:
            return
        start_index = self._panel._video_frame_index
        out_limit = self._out_limit(source)
        start_index = max(self.in_point, start_index)
        if out_limit is not None:
            start_index = min(out_limit, start_index)
        self._frame_index = start_index
        source.seek_to_frame(self._frame_index)
        self._playing = True
        self._panel.status_label.setText(PLAYBACK_STATUS_TEXT)
        self._base_interval_ms = max(1, round(1000.0 / source.frame_rate))
        self._restart_frame_server(self._frame_index + self._frame_step())
        self._timer.start(self._tick_interval_ms())
        self.playback_started.emit()

    def pause(self):
        """No-op if not currently playing. Re-parks the Viewer on the
        CURRENT frame at full native resolution through the normal
        grading pipeline (sample_image_panel.seek_video_frame) -- so
        anything left on screen after pausing is full quality, not the
        low-res playback proxy (see module docstring)."""
        if not self._playing:
            return
        self._timer.stop()
        self._frame_server.stop()
        self._playing = False
        _cancel_async_scopes(self._panel)
        self._panel.seek_video_frame(self._frame_index)
        self.playback_stopped.emit()

    def stop(self):
        """Immediately halts playback WITHOUT re-parking (unlike
        pause()) -- for teardown scenarios where the video source is
        about to be closed or replaced anyway (see
        sample_image_panel._close_video_source), so there's no point
        decoding one more frame through seek_video_frame just to
        discard it. No-op if not currently playing."""
        if not self._playing:
            return
        self._timer.stop()
        self._frame_server.stop()
        self._playing = False
        _cancel_async_scopes(self._panel)
        self.playback_stopped.emit()

    def toggle(self):
        self.pause() if self._playing else self.play()

    def set_speed(self, speed):
        """speed: one of SPEED_PRESETS (or any positive float). For
        speed <= 1, SLOWS the QTimer interval (interval_ms =
        base_interval_ms / speed) so the same frame sequence just takes
        longer to play -- a real slow-motion preview, no interpolation.
        For speed > 1, the interval stays at the base (realtime) rate,
        but each tick advances round(speed) source frames instead of one
        (see _frame_step/_tick) -- skips decode work rather than
        over-driving the timer, since every tick already forces a full
        texture upload regardless of resolution (see module docstring);
        firing the timer faster than realtime would only make that
        worse, not smoother. Re-arms the running QTimer's interval
        immediately if already playing, so a speed change mid-playback
        is felt on the very next tick, not after the next play()."""
        self.speed = speed
        if self._playing and self._base_interval_ms is not None:
            self._timer.setInterval(self._tick_interval_ms())
            self._restart_frame_server(self._frame_index + self._frame_step())

    def _restart_frame_server(self, start_index):
        """Decode ahead from `start_index` through the play range (and
        around again when looping) -- clock restarted too. Called on
        play() and whenever the range/speed/loop changes mid-play."""
        source = self._panel._video_source
        if source is None:
            return
        out_limit = self._out_limit(source)
        length = (out_limit - self.in_point + 1) if out_limit is not None else 10 ** 12
        offset = start_index - self.in_point
        if offset >= length:
            if not self.loop_enabled:
                self._frame_server.stop()
                return
            offset = 0
        self._frame_server.restart(
            [Segment(source.path, "video", self.in_point, length)], 0, max(0, offset),
            step=self._frame_step(), loop=self.loop_enabled)
        self._clock_start = time.perf_counter()
        self._frames_advanced = 0

    def _on_timer(self):
        """The playback clock -- see TimelineController._on_timer: advance
        by however many frames are due (at least one step), never block
        on a decoder."""
        source = self._panel._video_source
        rate = source.frame_rate if source is not None else 0
        step = self._frame_step()
        advance = step
        if self._clock_start is not None and rate:
            due = int((time.perf_counter() - self._clock_start) * rate * self.speed)
            advance = max(step, due - self._frames_advanced)
        self._tick(advance=advance, exact=False)

    def _tick_interval_ms(self):
        if self._base_interval_ms is None:
            return 1
        if self.speed <= 1.0:
            return max(1, round(self._base_interval_ms / self.speed))
        return self._base_interval_ms

    def _frame_step(self):
        """How many source frames one tick advances -- 1 for speed <= 1
        (slow-motion is achieved via the TIMER interval instead, see
        set_speed), round(speed) for speed > 1."""
        return 1 if self.speed <= 1.0 else max(1, round(self.speed))

    def _current_frame_index(self):
        """Whichever frame is authoritative right now -- the actively
        ticking position while playing, or the panel's own parked
        position while paused (same value the transport UI's scrub bar
        already shows). Used by set_in_point/set_out_point so "set to
        current frame" means the same thing regardless of playback
        state."""
        return self._frame_index if self._playing else self._panel._video_frame_index

    def set_in_point(self):
        """Sets in_point to the CURRENT frame (see _current_frame_index).
        Pushes out_point up to match if this would otherwise create an
        inverted/empty range (in_point > out_point) -- an explicit user
        action moving one bound always wins over silently rejecting it."""
        self.in_point = self._current_frame_index()
        if self.out_point is not None and self.in_point > self.out_point:
            self.out_point = self.in_point
        self._range_changed()

    def set_out_point(self):
        """Sets out_point to the CURRENT frame -- see set_in_point for
        the symmetric clamp."""
        self.out_point = self._current_frame_index()
        if self.out_point < self.in_point:
            self.in_point = self.out_point
        self._range_changed()

    def clear_in_out(self):
        """Resets to the full clip range -- in_point=0, out_point=None
        (see this class's own docstring on what None means)."""
        self.in_point = 0
        self.out_point = None
        self._range_changed()

    def set_loop_enabled(self, enabled):
        self.loop_enabled = bool(enabled)
        self._range_changed()

    def _range_changed(self):
        """In/out/loop changed: re-plan the decode-ahead if playing."""
        if self._playing:
            self._restart_frame_server(self._frame_index + self._frame_step())

    def _out_limit(self, source):
        """The effective last frame of the current playback range --
        out_point if explicitly set, otherwise the source's own last
        frame (frame_count - 1), or None if frame_count isn't known
        (some containers don't populate it -- see VideoSource.
        _detect_frame_count) and no explicit out_point was set either,
        meaning "play until decode_next_frame itself hits end of
        stream," the same behavior this controller had before in/out
        existed at all."""
        if self.out_point is not None:
            return self.out_point
        return (source.frame_count - 1) if source.frame_count else None

    def _tick(self, advance=None, exact=True):
        """Advance `advance` frames (default: one step) through the play
        range, wrapping to in_point when looping, and show that frame.
        `exact` (direct calls, tests): lands on exactly that frame,
        decoding it here if the frame server doesn't have it. Not exact
        (_on_timer): takes the newest decoded frame up to the target, and
        holds if nothing's decoded yet -- never blocks on a decoder."""
        source = self._panel._video_source
        if source is None:
            self.pause()
            return
        advance = self._frame_step() if advance is None else advance
        out_limit = self._out_limit(source)
        target = self._frame_index + advance
        if out_limit is not None and target > out_limit:
            if not self.loop_enabled:
                # End of range -- self._frame_index still holds the last
                # shown frame, so pause() re-parks there.
                self.pause()
                return
            target = self.in_point
        frame = self._frame_server.take(0, target - self.in_point)
        if frame is None and not exact:
            latest = self._frame_server.take_latest_up_to(0, target - self.in_point)
            if latest is None:
                return  # nothing decoded yet -- hold; the clock keeps running
            offset, frame = latest
            target = self.in_point + offset
        if frame is None:
            if target == self._frame_index + 1:
                frame = source.decode_next_frame(max_dimension=DECODE_PROXY_MAX_DIMENSION)
            else:
                frame = source.seek_to_frame(target, max_dimension=DECODE_PROXY_MAX_DIMENSION)
            if frame is None:
                # End of stream (frame_count was unknown/wrong) -- re-park
                # on the last good frame.
                self.pause()
                return
            if self._playing:
                self._restart_frame_server(target + self._frame_step())
        wrapped = target < self._frame_index
        self._frames_advanced += advance if wrapped else target - self._frame_index
        self._frame_index = target

        stack_getter = self._panel._stack_getter
        stack = stack_getter() if stack_getter else None
        push_graded_frame_to_viewer(self._panel, stack, frame, playing=self._playing)
        self.frame_changed.emit(self._frame_index)


def push_graded_frame_to_viewer(panel, stack, array, playing=False):
    """Shared by VideoPlaybackController and TimelineController's own
    per-tick logic -- grades `array` through `stack` (or None) and pushes
    the result to `panel`'s Viewer + Scopes, same GPU-eligible/CPU-
    fallback split sample_image_panel.py's own live-preview path already
    uses (see this module's docstring). Takes the already-resolved Stack,
    not a getter -- VideoPlaybackController resolves its single stack_
    getter once per tick same as before; TimelineController resolves a
    freshly-composed Stack (stack.compose_stacks(clip.stack, sequence_
    stack)) per tick instead, which isn't a plain getter callable at all.

    Scopes (via after_image_changed, see sample_image_panel.py's own
    "GPU-ELIGIBLE STACKS ALWAYS RECOMPUTE AT SCOPES RESOLUTION" comment)
    never reads a GPU-graded frame directly -- it always wants a small,
    CPU-graded representative sample instead, the same one refresh_
    preview's own _recompute_sample_after(scopes=True) produces for an
    ordinary parameter edit. Without this, realtime playback pushed
    frames straight to the Viewer and never called _set_current_after_
    array at all, so Scopes froze on whichever frame playback started
    from for the entire duration it played. Downsize FIRST, then grade,
    so this stays cheap enough for every tick even during playback --
    `array` is already capped to DECODE_PROXY_MAX_DIMENSION by the caller,
    but that's still well above SCOPES_MAX_DIMENSION."""
    if stack is not None and stack.stages and gpu_stack_renderer.is_stack_gpu_eligible(stack):
        panel.compare.set_after(array, gpu_stack=stack)
        scopes_proxy = panel._make_scopes_array(array)
        worker = getattr(panel, "_scopes_worker", None)
        if playing and worker is not None:
            # Off the UI thread while playing (scopes_worker.py: this grade
            # alone cost more than a 24 fps frame with a DRT in the stack).
            worker.submit(stack, scopes_proxy)
        else:
            panel._set_current_after_array(st.apply_stack_to_image_array(stack, scopes_proxy))
    elif stack is not None and stack.stages:
        graded = st.apply_stack_to_image_array(stack, array)
        panel.compare.set_after(graded, gpu_stack=None)
        # Same Scopes notification as the GPU-eligible branch above --
        # `graded` is already this tick's own full grade, just downsized
        # for Scopes rather than re-running the stack a second time.
        panel._set_current_after_array(panel._make_scopes_array(graded))
    else:
        panel.compare.set_after(array, gpu_stack=None)
        panel._set_current_after_array(panel._make_scopes_array(array))


def _cancel_async_scopes(panel):
    """Playback stopped: a Scopes result still in flight from an earlier
    frame mustn't land on top of the parked frame's own (see
    scopes_worker.py)."""
    worker = getattr(panel, "_scopes_worker", None)
    if worker is not None:
        worker.cancel()
