"""
video_playback_controller.py -- VideoPlaybackController: realtime,
UNGRADED playback of a SampleImagePanel's currently-open VideoSource,
pushed directly into the Viewer -- deliberately DECOUPLED from grading
(Tom, 2026-08-13: "realtime playback but no simultaneous grading/
playback"). Parked-frame stepping/scrubbing (see
sample_image_panel.seek_video_frame) owns color review; this
controller's only job is motion.

WHY UNGRADED, NOT A CACHED GRADE -- applying a static Stack snapshot to a
stream of DIFFERENT frame content on every tick would look plausible on
screen but be neither accurate (the grade might not even apply cleanly
to every frame, e.g. an exposure-dependent curve) nor honestly labeled --
worse than showing nothing graded at all. self._panel.status_label shows
PLAYBACK_STATUS_TEXT for the whole time playback is active, so this is
never mistaken for a graded preview; pausing/stepping re-parks on the
current frame through the NORMAL grading pipeline
(sample_image_panel.seek_video_frame), which already clears
status_label back to "" as a side effect of that normal load path -- no
special cleanup needed here.

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
from PySide6.QtCore import QObject, QTimer, Signal

# Long-side cap for the decoded proxy pushed to the Viewer WHILE playing
# -- see module docstring's PERF section. Not user-configurable (yet);
# generous enough to still look sharp on typical displays, small enough
# to keep the per-tick texture upload cheap.
DECODE_PROXY_MAX_DIMENSION = 1280

PLAYBACK_STATUS_TEXT = "UNGRADED — PLAYBACK"


class VideoPlaybackController(QObject):
    """`panel`: the SampleImagePanel this controller plays into -- reads
    panel._video_source/.compare/.status_label directly (an internal
    collaborator, not a public API surface of that panel) and calls
    panel.seek_video_frame on pause to re-park at full resolution,
    matching the "own module, tight collaboration with one specific
    panel" shape casting_controller.py already uses for the SAME panel.

    Emits frame_changed(frame_index) on every ticked frame (the
    transport UI's own scrub position while playing -- panel's own
    _video_frame_index is deliberately NOT updated during playback, only
    on pause/step/scrub, see pause()'s own docstring) and
    playback_stopped() whenever playback stops for ANY reason (explicit
    pause, end of stream, or no video source)."""

    frame_changed = Signal(int)
    playback_stopped = Signal()

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self._panel = panel
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._frame_index = 0
        self._playing = False

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
        the last time playback ran."""
        if self._playing:
            return
        source = self._panel._video_source
        if source is None or not source.frame_rate:
            return
        self._frame_index = self._panel._video_frame_index
        source.seek_to_frame(self._frame_index)
        self._playing = True
        self._panel.status_label.setText(PLAYBACK_STATUS_TEXT)
        interval_ms = max(1, round(1000.0 / source.frame_rate))
        self._timer.start(interval_ms)

    def pause(self):
        """No-op if not currently playing. Re-parks the Viewer on the
        CURRENT frame at full native resolution through the normal
        grading pipeline (sample_image_panel.seek_video_frame) -- so
        anything left on screen after pausing is full quality and
        graded, not the low-res raw playback proxy (see module
        docstring)."""
        if not self._playing:
            return
        self._timer.stop()
        self._playing = False
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
        self._playing = False
        self.playback_stopped.emit()

    def toggle(self):
        self.pause() if self._playing else self.play()

    def _tick(self):
        source = self._panel._video_source
        if source is None:
            self.pause()
            return
        proxy = source.decode_next_frame(max_dimension=DECODE_PROXY_MAX_DIMENSION)
        if proxy is None:
            # End of stream -- self._frame_index still holds the LAST
            # successfully decoded frame (never incremented for this
            # failed one), so pause() re-parks there, not one past it.
            self.pause()
            return
        self._frame_index += 1
        self._panel.compare.set_after(proxy, gpu_stack=None)
        self.frame_changed.emit(self._frame_index)
