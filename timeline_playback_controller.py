"""
timeline_playback_controller.py -- TimelineController: realtime playback
across an entire timeline.Timeline, composing each clip's own Stack with
the Sequence Stack per frame (stack.compose_stacks) and pushing the
result into a SampleImagePanel's Viewer -- coexists with (does not
replace) video_playback_controller.VideoPlaybackController, which still
drives a single directly-loaded clip.

TRANSPORT SPLIT -- originally (Tom, 2026-08-24) "the transport can be
exclusive to the timeline BUT there should be a scrub bar only that's
exclusive to the clip," later replaced by a manual SOURCE/RECORD toggle
(per further direct feedback: "it should be a toggle you can control
all the time and play should work for either"): the Viewer's transport
bar (Play/Pause/Loop/Speed) now drives THIS controller only while that
toggle is set to RECORD -- see sample_image_panel.py's own
_active_transport_controller/_on_viewer_mode_toggled for the actual
routing, this module doesn't know the toggle exists. The scrub bar
follows the same toggle (seek_to_record_frame, below, is its RECORD-mode
seek target -- per still further feedback: "the scrub bar seems to only
represent a single clip in the timeline. it should represent the whole
timeline"). Step/Set In/Set Out/Clear In-Out (a partial-range
sub-selection of the WHOLE timeline) are still explicitly NOT
implemented here -- the approved Timeline plan's own scope note rules
out "complex trim editing" for this pass, so those three buttons stay
disabled regardless of which mode the toggle is on. Loop, here, means
"loop the whole timeline back to its first clip," not an in/out
sub-range.

PER-FRAME GRADING mirrors VideoPlaybackController's own GPU-eligible/
CPU-fallback split exactly, via the shared push_graded_frame_to_viewer
helper in that module -- the only new wrinkle is WHICH Stack: each tick
composes the CURRENT clip's own Stack with the Sequence Stack fresh
(stack.compose_stacks(clip.stack, sequence_stack)) rather than reading a
single fixed stack_getter, since which two stacks apply changes at every
clip boundary.

STILL CLIPS hold a single decoded frame for clip.frame_count ticks --
per Tom ("it doesn't need to continuously re-render"), the frame is
graded and pushed to the Viewer ONCE on entering the clip (_enter_clip),
not on every tick while it holds: nothing about a frozen frame's own
grade changes tick to tick, since the Stack panel is locked for the
duration of any playback (see playback_started/playback_stopped below,
same lock video_playback_controller.py's own docstring established for
single-clip playback). Re-decoded fresh from clip.source_path each time
the clip comes up (never cached across plays) -- same "how to rebuild
it, not the built thing" principle as every other path-referenced
source in this app (media_library.MediaEntry, timeline.
TimelineClip's own docstring on why a clip only ever stores a path + a
small thumbnail, not the full decoded array).

VIDEO CLIPS open their own dedicated video_source.VideoSource on
entering the clip and close it on leaving (or on stop) -- this
controller owns that lifecycle entirely independently of SampleImage
Panel's own self._video_source (a single directly-loaded clip's source,
used by VideoPlaybackController instead); the two never touch the same
VideoSource object, so Timeline playback and a directly-loaded Source
clip can coexist without stepping on each other.

Same "own QObject, own QTimer" shape as VideoPlaybackController/
CastingController.
"""
import time

from PySide6.QtCore import QObject, QTimer, Signal, Qt

import app_logic
import stack as st
import video_source
from frame_server import FrameServer, Segment
from video_playback_controller import (
    DECODE_PROXY_MAX_DIMENSION, DEFAULT_SPEED,
    push_graded_frame_to_viewer, _cancel_async_scopes,
)

TIMELINE_PLAYBACK_STATUS_TEXT = "TIMELINE PLAYBACK — grade locked"

# Extra temporary halving of DECODE_PROXY_MAX_DIMENSION during an active
# RECORD-mode scrub burst (see _enter_clip's own `downres` param, driven
# by sample_image_panel.SCRUB_DOWNRES_FACTOR/_on_video_scrub_slider_
# changed) -- Timeline clips already never decode above DECODE_PROXY_MAX_
# DIMENSION even at rest (see TimelineController.pause's own docstring on
# why there's no full-native "parked" mode here), so "settled" for RECORD
# mode means back to that existing cap, not native.
RECORD_SEEK_DOWNRES_FACTOR = 2


class TimelineController(QObject):
    """`panel`: the SampleImagePanel this controller plays into (same
    tight-collaboration shape as VideoPlaybackController -- see that
    module's own docstring; reads panel.compare/status_label/_make_
    scopes_array/_set_current_after_array directly). `timeline_getter`:
    zero-arg -> the current timeline.Timeline (pull-based, wired from
    MainWindow to TimelinePanel.get_timeline). `sequence_stack_getter`:
    zero-arg -> the Sequence Stack (StackPanel.get_sequence_stack, NOT
    get_stack -- playback must always compose against Sequence
    regardless of whatever StackPanel's own editor happens to be showing
    right now, see stack_panel.py's own docstring on why those two can
    diverge while Shot mode is active).

    Emits playback_started()/playback_stopped() (MainWindow locks/
    unlocks the Stack panel for the duration, same convention as
    VideoPlaybackController's own signals) and frame_changed() on every
    ticked/entered frame -- no payload; readers pull the actual position
    back out via current_source_frame()/current_record_frame() (see
    those methods' own docstrings), same "callback/pull over a signal
    payload" convention as the rest of this app's cross-object state
    reads. Consumed by sample_image_panel.py's own Source/Record
    timecode readout (Tom: "it would be good to get more detail (in and
    out source/record tc)")."""

    playback_started = Signal()
    playback_stopped = Signal()
    frame_changed = Signal()

    def __init__(self, panel, timeline_getter, sequence_stack_getter, parent=None):
        super().__init__(parent)
        self._panel = panel
        self._timeline_getter = timeline_getter
        self._sequence_stack_getter = sequence_stack_getter
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._on_timer)
        self._playing = False
        # Decodes ahead on a worker thread while playing (frame_server.py);
        # _tick takes from it and only decodes itself on a miss.
        self._frame_server = FrameServer(DECODE_PROXY_MAX_DIMENSION)
        self._clock_start = None       # perf_counter() when playback (re)started
        self._frames_advanced = 0      # frames moved since the clock started
        # (clip, composed Stack) -> the Stack to render: MainWindow wraps it
        # in the clip's input transform and the display transform
        # (colour_pipeline.py). Identity until set.
        self._pipeline = None
        self.loop_enabled = False
        self.speed = DEFAULT_SPEED
        self._base_interval_ms = None
        # Playback position -- persists across pause/play (resume where
        # you left off, same convention as VideoPlaybackController's own
        # panel._video_frame_index) -- reset to (0, 0) only by stop(),
        # never by pause().
        self._clip_index = 0
        self._frame_in_clip = 0
        self._video_source = None  # this controller's OWN VideoSource -- video clips only, see module docstring

    @property
    def is_playing(self):
        return self._playing

    def seek_to_clip(self, clip_index):
        """Repositions this controller (without playing) to the START of
        the clip at `clip_index` -- called by MainWindow whenever the
        Timeline's own selection changes (see main_window.py's
        _on_timeline_clip_selected), so a SUBSEQUENT play() always
        starts from wherever the user is currently looking/editing, not
        wherever an earlier play/pause session happened to leave off
        (Tom: "the transport is still a little tricky to interact
        with" -- pressing Play jumping to an unrelated, stale position
        was a real source of that). No-op while playing -- the Timeline
        panel is locked during playback (see main_window.py's own
        wiring), so selection can't normally change then anyway, but
        this guards against it regardless rather than yanking the
        source out from under a running tick.

        Does NOT itself push anything to the Viewer -- the Viewer's own
        PARKED preview (as opposed to active playback) is driven by
        sample_image.load_video/load_image plus MainWindow._composed_
        preview_stack instead (see that method's own docstring); this
        method only affects where the NEXT play() resumes from."""
        if self._playing:
            return
        self._close_video_source()
        self._clip_index = clip_index
        self._frame_in_clip = 0

    def seek_to_record_frame(self, record_frame, downres=False):
        """Repositions this controller (pausing first if currently
        playing) to an arbitrary frame within the WHOLE Timeline, not
        just a clip's own start (see seek_to_clip) -- `record_frame` is
        a position in the same numbering current_record_frame() returns,
        clamped into [0, total_frame_count-1]. Unlike seek_to_clip, this
        immediately pushes the resulting frame to the Viewer (via
        _enter_clip) rather than only affecting where a LATER play()
        resumes from -- this is the Timeline-level counterpart to
        VideoPlaybackController.seek_video_frame/sample_image_panel.
        _on_video_scrub_slider_changed for the single-clip case, driving
        the RECORD half of the Viewer's SOURCE/RECORD scrub bar (per
        direct feedback: "the scrub bar seems to only represent a single
        clip in the timeline. it should represent the whole timeline" --
        see sample_image_panel.py's own wiring). No-op on an empty
        Timeline, same as play().

        `downres` (per Tom: "make scrub drop the render resolution to
        half until settled so its more performant") -- passed straight
        to _enter_clip; only sample_image_panel.py's own scrub-burst
        detection ever passes True here, see that module's SCRUB_DOWNRES
        _FACTOR/_on_video_scrub_slider_changed."""
        self.pause()
        timeline_obj = self._timeline_getter() if self._timeline_getter else None
        if timeline_obj is None or not timeline_obj.clips:
            return
        total = timeline_obj.total_frame_count()
        record_frame = max(0, min(record_frame, total - 1))
        clip_index, frame_in_clip = self._locate_record_frame(timeline_obj, record_frame)
        clip = timeline_obj.clips[clip_index]
        self._enter_clip(clip_index, clip, frame_in_clip=frame_in_clip, downres=downres)

    @staticmethod
    def _locate_record_frame(timeline_obj, record_frame):
        """(clip_index, frame_in_clip) for an absolute position within
        the whole Timeline -- the inverse of current_record_frame()'s
        own "sum every earlier clip's frame_count" computation. Assumes
        record_frame is already clamped into [0, total_frame_count-1]
        by the caller (seek_to_record_frame) -- the final clip's last
        frame is still returned as a defensive fallback if rounding
        somehow overshoots, rather than raising an IndexError."""
        offset = record_frame
        for index, clip in enumerate(timeline_obj.clips):
            if offset < clip.frame_count:
                return index, offset
            offset -= clip.frame_count
        last_index = len(timeline_obj.clips) - 1
        return last_index, max(0, timeline_obj.clips[last_index].frame_count - 1)

    def play(self):
        """No-op if already playing, or if the Timeline has no clips.
        Resumes from wherever pause() last left off (self._clip_index/
        _frame_in_clip), clamped back onto the clip list's own current
        bounds first -- the Timeline may have shrunk (a clip Removed)
        since the last time this controller ran."""
        if self._playing:
            return
        timeline_obj = self._timeline_getter() if self._timeline_getter else None
        if timeline_obj is None or not timeline_obj.clips:
            return
        if self._clip_index >= len(timeline_obj.clips):
            self._clip_index = 0
            self._frame_in_clip = 0
        clip = timeline_obj.clips[self._clip_index]
        if not self._enter_clip(self._clip_index, clip, frame_in_clip=self._frame_in_clip):
            return
        self._playing = True
        self._panel.status_label.setText(TIMELINE_PLAYBACK_STATUS_TEXT)
        self._base_interval_ms = max(1, round(1000.0 / timeline_obj.frame_rate))
        self._restart_frame_server(timeline_obj, self._clip_index, self._frame_in_clip + self._frame_step())
        self._clock_start = time.perf_counter()
        self._frames_advanced = 0
        self._timer.start(self._tick_interval_ms())
        self.playback_started.emit()

    def pause(self):
        """No-op if not currently playing. Unlike VideoPlaybackController
        .pause(), there's no full-resolution re-park step here -- a
        Timeline clip's own DECODE_PROXY_MAX_DIMENSION-capped frame is
        already what a static single-clip preview would show for it too
        (Timeline clips have no separate "parked at full res" viewing
        mode of their own yet, see the approved plan's own scope note on
        what's deferred past this pass)."""
        if not self._playing:
            return
        self._timer.stop()
        self._frame_server.stop()
        self._playing = False
        _cancel_async_scopes(self._panel)
        self.playback_stopped.emit()

    def stop(self):
        """Halts playback AND resets position back to the very start --
        unlike pause() (which resumes where it left off). Used both for
        teardown (Timeline about to be replaced wholesale, see
        MainWindow's _new_session/_open_session -- a fresh session
        shouldn't resume a stale mid-timeline position from whatever was
        loaded before) and for the ordinary "ran off the end, not
        looping" case inside _tick/_advance_to_next_clip."""
        was_playing = self._playing
        if was_playing:
            self._timer.stop()
            self._playing = False
        self._frame_server.stop()
        _cancel_async_scopes(self._panel)
        self._close_video_source()
        self._clip_index = 0
        self._frame_in_clip = 0
        # Refreshes the Source/Record timecode readout to reflect the
        # reset position (or go blank, if the Timeline is now empty too)
        # rather than leaving it showing a stale pre-stop position.
        self.frame_changed.emit()
        if was_playing:
            self.playback_stopped.emit()

    def toggle(self):
        self.pause() if self._playing else self.play()

    def set_loop_enabled(self, enabled):
        self.loop_enabled = bool(enabled)

    def set_speed(self, speed):
        """Same behavior/reasoning as VideoPlaybackController.set_speed
        -- see that method's own docstring."""
        self.speed = speed
        if self._playing and self._base_interval_ms is not None:
            self._timer.setInterval(self._tick_interval_ms())
            # The worker's stride depends on speed, and the clock restarts.
            timeline_obj = self._timeline_getter() if self._timeline_getter else None
            if timeline_obj is not None and timeline_obj.clips:
                self._restart_frame_server(
                    timeline_obj, self._clip_index, self._frame_in_clip + self._frame_step())
            self._clock_start = time.perf_counter()
            self._frames_advanced = 0

    @staticmethod
    def _segments(timeline_obj):
        return [Segment(c.source_path, c.media_kind, c.in_frame, c.frame_count)
                for c in timeline_obj.clips]

    def _restart_frame_server(self, timeline_obj, clip_index, frame_in_clip):
        """Decode ahead from (clip_index, frame_in_clip) -- rolled over
        into the next clip if it's past this one's end."""
        clips = timeline_obj.clips
        while clip_index < len(clips) and frame_in_clip >= clips[clip_index].frame_count:
            frame_in_clip -= clips[clip_index].frame_count
            clip_index += 1
        if clip_index >= len(clips):
            if not self.loop_enabled:
                self._frame_server.stop()
                return
            clip_index, frame_in_clip = 0, 0
        self._frame_server.restart(self._segments(timeline_obj), clip_index, frame_in_clip,
                                   step=self._frame_step(), loop=self.loop_enabled)

    def _on_timer(self):
        """The playback clock: advance by however many frames are DUE
        since play started (at least the normal step), so a slow moment
        drops frames and playback stays in time instead of slowing down.
        Not exact (see _tick): never blocks the UI thread on a decoder."""
        timeline_obj = self._timeline_getter() if self._timeline_getter else None
        rate = timeline_obj.frame_rate if timeline_obj is not None else 0
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
        return 1 if self.speed <= 1.0 else max(1, round(self.speed))

    def _close_video_source(self):
        if self._video_source is not None:
            self._video_source.close()
            self._video_source = None

    def set_pipeline(self, pipeline):
        self._pipeline = pipeline

    def _render_stack(self, clip):
        composed = st.compose_stacks(clip.stack, self._current_sequence_stack())
        return self._pipeline(clip, composed) if self._pipeline else composed

    def _current_sequence_stack(self):
        getter = self._sequence_stack_getter
        return getter() if getter else st.Stack()

    def _enter_clip(self, clip_index, clip, frame_in_clip=0, downres=False, prefetched=None):
        """Opens/decodes whatever `clip` needs (the clip at `clip_index`)
        and pushes ITS frame at `frame_in_clip` (an offset from clip.
        in_frame) to the Viewer -- returns False (Viewer left untouched,
        self._clip_index/_frame_in_clip left AT THEIR OLD VALUES) if the
        clip's own source can't be opened/decoded, so callers (play()/
        seek_to_record_frame/_advance_to_next_clip) can bail out cleanly
        on a missing/moved file rather than getting stuck. Always closes
        any previously-open VideoSource first (module docstring -- only
        one clip's source is ever open at a time).

        Sets self._clip_index HERE, before frame_changed.emit() below --
        callers must NOT set it themselves afterward. Previously every
        caller set self._clip_index only AFTER this method returned,
        which meant frame_changed listeners reading current_record_frame
        () during THIS method's own emit -- e.g. TimelinePanel's playhead
        sync -- paired the NEW frame_in_clip with the OLD (stale)
        clip_index, computing a wrong record-space position at every
        seek and every clip-boundary crossing during playback (the
        visible "transport playhead vs. Timeline view playhead" desync).

        `downres` (see RECORD_SEEK_DOWNRES_FACTOR/seek_to_record_frame's
        own docstring) halves DECODE_PROXY_MAX_DIMENSION for this one
        decode -- play()/_advance_to_next_clip never pass it, only an
        active RECORD-mode scrub burst does."""
        self._close_video_source()
        composed = self._render_stack(clip)
        max_dimension = (
            DECODE_PROXY_MAX_DIMENSION // RECORD_SEEK_DOWNRES_FACTOR if downres
            else DECODE_PROXY_MAX_DIMENSION)
        if prefetched is not None:
            # Already decoded ahead by the frame server -- no file open or
            # seek on the UI thread at a cut. A later miss opens the
            # source lazily (_decode_here).
            push_graded_frame_to_viewer(self._panel, composed, prefetched, playing=self._playing)
        elif clip.media_kind == "video":
            try:
                source = video_source.VideoSource(clip.source_path)
            except Exception:  # noqa: BLE001 -- missing/moved/corrupt file, same as any other re-decode path
                return False
            frame = source.seek_to_frame(
                clip.in_frame + frame_in_clip, max_dimension=max_dimension)
            if frame is None:
                source.close()
                return False
            self._video_source = source
            push_graded_frame_to_viewer(self._panel, composed, frame)
        else:
            try:
                array = app_logic.load_image_as_array(
                    clip.source_path, max_dimension=max_dimension)
            except Exception:  # noqa: BLE001 -- missing/moved/corrupt file
                return False
            push_graded_frame_to_viewer(self._panel, composed, array)
        self._clip_index = clip_index
        self._frame_in_clip = frame_in_clip
        self.frame_changed.emit()
        return True

    def current_source_frame(self):
        """The current frame's position within its OWN clip's source
        media, from that media's own frame 0 (clip.in_frame + frame_in_
        clip) -- e.g. for a clip trimmed to start at frame 100, sitting
        20 frames into playback, this is 120, not 20. None if nothing's
        entered yet (empty Timeline, or a clip_index past the end after
        the Timeline shrank underneath this controller)."""
        timeline_obj = self._timeline_getter() if self._timeline_getter else None
        if timeline_obj is None or not timeline_obj.clips or self._clip_index >= len(timeline_obj.clips):
            return None
        clip = timeline_obj.clips[self._clip_index]
        return clip.in_frame + self._frame_in_clip

    def current_record_frame(self):
        """The current frame's position within the WHOLE Timeline -- every
        earlier clip's own frame_count, summed, plus this clip's current
        offset. Same None-on-nothing-entered contract as current_source_
        frame."""
        timeline_obj = self._timeline_getter() if self._timeline_getter else None
        if timeline_obj is None or not timeline_obj.clips or self._clip_index >= len(timeline_obj.clips):
            return None
        preceding = sum(c.frame_count for c in timeline_obj.clips[:self._clip_index])
        return preceding + self._frame_in_clip

    def _tick(self, advance=None, exact=True):
        """Advance `advance` frames (default: one normal step, or several
        at speed > 1) and show that frame -- crossing into the next clip
        at the end of this one. `exact` (direct calls, tests): always
        lands on exactly that frame, decoding it here if the frame server
        doesn't have it. Not exact (_on_timer): takes the newest decoded
        frame up to the target instead, and holds the current frame if
        nothing's decoded yet -- never blocks on a decoder."""
        timeline_obj = self._timeline_getter() if self._timeline_getter else None
        if timeline_obj is None or not timeline_obj.clips or self._clip_index >= len(timeline_obj.clips):
            self.stop()
            return
        clip = timeline_obj.clips[self._clip_index]
        advance = self._frame_step() if advance is None else advance
        next_offset = self._frame_in_clip + advance
        if next_offset > clip.frame_count - 1:
            self._frames_advanced += clip.frame_count - self._frame_in_clip
            self._advance_to_next_clip(timeline_obj)
            return
        if clip.media_kind == "still":
            # Held, not re-decoded -- _enter_clip already pushed its image.
            self._frame_in_clip = next_offset
            self._frames_advanced += advance
            self.frame_changed.emit()
            return
        frame = self._frame_server.take(self._clip_index, next_offset)
        if frame is None and not exact:
            latest = self._frame_server.take_latest_up_to(self._clip_index, next_offset)
            if latest is None:
                return  # nothing decoded yet -- hold; the clock keeps running
            next_offset, frame = latest
        if frame is None:
            frame = self._decode_here(timeline_obj, clip, next_offset)
        if frame is None:  # ran out early (frame_count was an estimate)
            self._advance_to_next_clip(timeline_obj)
            return
        self._frames_advanced += next_offset - self._frame_in_clip
        self._frame_in_clip = next_offset
        composed = self._render_stack(clip)
        push_graded_frame_to_viewer(self._panel, composed, frame, playing=self._playing)
        self.frame_changed.emit()

    def _decode_here(self, timeline_obj, clip, offset):
        """The old synchronous decode, for a frame-server miss: opens this
        clip's source here if needed, decodes `offset`, and restarts the
        server just past it while playing."""
        source = self._video_source
        if source is None:
            try:
                source = self._video_source = video_source.VideoSource(clip.source_path)
            except Exception:  # noqa: BLE001 -- missing/moved file, same as _enter_clip
                return None
            frame = source.seek_to_frame(clip.in_frame + offset, max_dimension=DECODE_PROXY_MAX_DIMENSION)
        elif offset == self._frame_in_clip + 1:
            frame = source.decode_next_frame(max_dimension=DECODE_PROXY_MAX_DIMENSION)
        else:
            frame = source.seek_to_frame(clip.in_frame + offset, max_dimension=DECODE_PROXY_MAX_DIMENSION)
        if frame is not None and self._playing:
            self._restart_frame_server(timeline_obj, self._clip_index, offset + self._frame_step())
        return frame

    def _advance_to_next_clip(self, timeline_obj):
        next_index = self._clip_index + 1
        if next_index >= len(timeline_obj.clips):
            if not self.loop_enabled:
                self.stop()
                return
            next_index = 0
        clip = timeline_obj.clips[next_index]
        prefetched = self._frame_server.take(next_index, 0)
        if not self._enter_clip(next_index, clip, frame_in_clip=0, prefetched=prefetched):
            # Couldn't open the next clip (missing file, say) -- stop
            # rather than get stuck ticking on a broken source forever.
            self.stop()
            return
