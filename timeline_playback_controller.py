"""
timeline_playback_controller.py -- TimelineController: realtime playback
across an entire timeline.Timeline, composing each clip's own Stack with
the Sequence Stack per frame (stack.compose_stacks) and pushing the
result into a SampleImagePanel's Viewer -- coexists with (does not
replace) video_playback_controller.VideoPlaybackController, which still
drives a single directly-loaded clip.

TRANSPORT SPLIT (Tom, 2026-08-24): "the transport can be exclusive to
the timeline BUT there should be a scrub bar only that's exclusive to
the clip." The Viewer's transport bar (Play/Pause/Step/Loop/Speed) is
now EXCLUSIVE to Timeline playback -- driving THIS controller, not
VideoPlaybackController -- while the Viewer's own scrub slider stays
exclusive to whatever single clip is directly loaded as Source
(VideoPlaybackController's own domain, untouched). See sample_image_
panel.py's transport-bar wiring for exactly how the buttons are
retargeted. Timeline-level Set In/Set Out/Clear In-Out (a partial-range
sub-selection of the WHOLE timeline) is explicitly NOT implemented here
-- the approved Timeline plan's own scope note rules out "complex trim
editing" for this pass, so those three buttons are simply disabled while
this controller is what Play drives. Loop, here, means "loop the whole
timeline back to its first clip," not an in/out sub-range.

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
source in this app (image_library.ImageLibraryEntry, timeline.
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
from PySide6.QtCore import QObject, QTimer, Signal

import app_logic
import stack as st
import video_source
from video_playback_controller import (
    DECODE_PROXY_MAX_DIMENSION, DEFAULT_SPEED,
    push_graded_frame_to_viewer,
)

TIMELINE_PLAYBACK_STATUS_TEXT = "TIMELINE PLAYBACK — grade locked"


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
        self._timer.timeout.connect(self._tick)
        self._playing = False
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
        if not self._enter_clip(clip, frame_in_clip=self._frame_in_clip):
            return
        self._playing = True
        self._panel.status_label.setText(TIMELINE_PLAYBACK_STATUS_TEXT)
        self._base_interval_ms = max(1, round(1000.0 / timeline_obj.frame_rate))
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
        self._playing = False
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

    def _current_sequence_stack(self):
        getter = self._sequence_stack_getter
        return getter() if getter else st.Stack()

    def _enter_clip(self, clip, frame_in_clip=0):
        """Opens/decodes whatever `clip` needs and pushes ITS frame at
        `frame_in_clip` (an offset from clip.in_frame) to the Viewer --
        returns False (Viewer left untouched) if the clip's own source
        can't be opened/decoded, so callers (play()/_advance_to_next_
        clip) can bail out cleanly on a missing/moved file rather than
        getting stuck. Always closes any previously-open VideoSource
        first (module docstring -- only one clip's source is ever open
        at a time)."""
        self._close_video_source()
        composed = st.compose_stacks(clip.stack, self._current_sequence_stack())
        if clip.media_kind == "video":
            try:
                source = video_source.VideoSource(clip.source_path)
            except Exception:  # noqa: BLE001 -- missing/moved/corrupt file, same as any other re-decode path
                return False
            frame = source.seek_to_frame(
                clip.in_frame + frame_in_clip, max_dimension=DECODE_PROXY_MAX_DIMENSION)
            if frame is None:
                source.close()
                return False
            self._video_source = source
            push_graded_frame_to_viewer(self._panel, composed, frame)
        else:
            try:
                array = app_logic.load_image_as_array(
                    clip.source_path, max_dimension=DECODE_PROXY_MAX_DIMENSION)
            except Exception:  # noqa: BLE001 -- missing/moved/corrupt file
                return False
            push_graded_frame_to_viewer(self._panel, composed, array)
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

    def _tick(self):
        timeline_obj = self._timeline_getter() if self._timeline_getter else None
        if timeline_obj is None or not timeline_obj.clips or self._clip_index >= len(timeline_obj.clips):
            self.stop()
            return
        clip = timeline_obj.clips[self._clip_index]
        step = self._frame_step()
        next_offset = self._frame_in_clip + step

        if clip.media_kind == "still":
            # Held frozen -- already pushed once by _enter_clip (see that
            # method's own docstring). Just count ticks toward this
            # clip's own frame_count; nothing to decode or re-push.
            if next_offset > clip.frame_count - 1:
                self._advance_to_next_clip(timeline_obj)
                return
            self._frame_in_clip = next_offset
            self.frame_changed.emit()
            return

        source = self._video_source
        if source is None:
            self.stop()
            return
        if next_offset > clip.frame_count - 1:
            self._advance_to_next_clip(timeline_obj)
            return
        if step == 1:
            frame = source.decode_next_frame(max_dimension=DECODE_PROXY_MAX_DIMENSION)
        else:
            # speed > 1 -- a SEEK straight to the target offset rather
            # than stepping through intermediate frames whose results
            # would just be discarded, same reasoning as
            # VideoPlaybackController's own _tick.
            frame = source.seek_to_frame(
                clip.in_frame + next_offset, max_dimension=DECODE_PROXY_MAX_DIMENSION)
        if frame is None:
            # End of stream earlier than clip.out_frame promised
            # (frame_count is an estimate for some containers -- see
            # video_source.py's own docstring) -- treat it the same as
            # reaching the clip's own last frame.
            self._advance_to_next_clip(timeline_obj)
            return
        self._frame_in_clip = next_offset
        composed = st.compose_stacks(clip.stack, self._current_sequence_stack())
        push_graded_frame_to_viewer(self._panel, composed, frame)
        self.frame_changed.emit()

    def _advance_to_next_clip(self, timeline_obj):
        next_index = self._clip_index + 1
        if next_index >= len(timeline_obj.clips):
            if not self.loop_enabled:
                self.stop()
                return
            next_index = 0
        clip = timeline_obj.clips[next_index]
        if not self._enter_clip(clip, frame_in_clip=0):
            # Couldn't open the next clip (missing file, say) -- stop
            # rather than get stuck ticking on a broken source forever.
            self.stop()
            return
        self._clip_index = next_index
