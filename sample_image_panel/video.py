"""sample_image_panel/video.py -- video in the Record viewer: loading, seeking
and decoding frames, following the timeline's frame, and switching between
Clip (source) and Record (timeline) mode. A mixin: SampleImagePanel
inherits it, so these methods share the panel's own `self`.
"""
from PySide6.QtWidgets import QMessageBox
import video_source
import timecode
from .constants import MODE_LABELS, SCRUB_DOWNRES_FACTOR


class VideoMixin:
    def _close_video_source(self):
        if self._video_source is not None:
            # stop(), not pause() -- no point re-parking through a full
            # seek+decode just to immediately discard it (see
            # VideoPlaybackController.stop's own docstring); also avoids
            # pause() trying to decode from a source we're about to close.
            self._playback_controller.stop()
            self._video_source.close()
            self._video_source = None
            self._video_frame_index = 0
        self.refresh_video_transport_visibility()

    def load_video(self, path):
        """Opens `path` as a video source and loads its FIRST frame into
        the exact same grading pipeline every still-image Sample load
        already uses (see _load_video_frame_core) -- this is PARKED-FRAME
        review only, not realtime playback (see video_playback_
        controller.py for that, a fully separate, decoupled path per
        Tom's own confirmed scope: "realtime playback but no simultaneous
        grading/playback"). Returns True on success.

        A NO-OP (still returns True) if `path` is already the currently
        open source -- reopening the identical file would otherwise
        discard the user's own in/out marks, loop state, and parked
        scrub position for no reason (Tom: "marking in and out dont
        seem to work at all" -- one real cause, found while building Add
        to Sequence: selecting a just-added Timeline clip whose
        source_path is the SAME file it was just marked on re-triggers
        this method via main_window._on_timeline_clip_selected, which
        used to always clear those marks right back out). A genuinely
        DIFFERENT path still gets the full "fresh clip" reset just
        below, unchanged."""
        if self._video_source is not None and str(self._video_source.path) == str(path):
            return True
        try:
            source = video_source.VideoSource(path)
        except Exception as e:  # noqa: BLE001 -- surface any open/decode failure to the user
            QMessageBox.critical(self, "Failed to open video", f"{path}\n\n{e}")
            return False
        self._close_video_source()
        self._video_source = source
        # Fresh clip -- any in/out points or loop state left over from a
        # PREVIOUSLY loaded video are stale (Phase 4 transport upgrade),
        # not something a newly opened clip should inherit.
        self._playback_controller.clear_in_out()
        self._playback_controller.set_loop_enabled(False)
        self._video_loop_button.setChecked(False)  # keep the button in sync with the controller reset above
        ok = self._load_video_frame_core(0)
        if ok:
            # Only actually touches the scrub bar if SOURCE mode is
            # current (see _refresh_scrub_slider_for_mode's own
            # docstring) -- loading a new Source clip while parked in
            # RECORD mode must leave the Timeline-range scrub bar alone.
            self._refresh_scrub_slider_for_mode()
            self.refresh_video_transport_visibility()
            self.video_loaded.emit(source.frame_count, source.frame_rate)
        return ok

    def seek_video_frame(self, frame_index, downres=False):
        """Scrub/step to a specific frame of the currently-open video --
        the public entry point the transport UI (video_playback_
        controller.py / the scrub bar) calls. No-op (returns False) if no
        video is currently open. `downres`: see _load_video_frame_core's
        own docstring -- callers other than the scrub bar itself always
        want the default full-resolution decode."""
        if self._video_source is None:
            return False
        return self._load_video_frame_core(frame_index, downres=downres)

    def _load_video_frame_core(self, frame_index, downres=False):
        """The video equivalent of _load_image_core -- decodes ONE frame
        from self._video_source and feeds it through the EXACT SAME
        downstream pipeline (_recompute_working_array/
        _recompute_sample_after/_render_wipe) a still image load already
        uses, so grading a parked video frame works identically to
        grading a still -- no separate grading code path exists or is
        needed.

        `downres` (see SCRUB_DOWNRES_FACTOR/_on_video_scrub_slider_
        changed): decodes at half the video's own native resolution
        instead of full native -- only ever passed True mid-scrub-burst;
        every other caller (a plain click-to-jump, Step, Set In/Out,
        session restore) keeps the default full-native decode this
        method has always done."""
        max_dimension = None
        if downres:
            native_long_side = max(self._video_source.width, self._video_source.height)
            max_dimension = max(1, native_long_side // SCRUB_DOWNRES_FACTOR)
        try:
            array = self._video_source.seek_to_frame(frame_index, max_dimension=max_dimension)
        except Exception as e:  # noqa: BLE001 -- surface any decode failure to the user
            QMessageBox.critical(self, "Failed to decode video frame", str(e))
            return False
        if array is None:
            return False

        self._video_frame_index = frame_index
        self._current_path = self._video_source.path
        self._original_array = array
        self.status_label.setText("")
        self._recompute_working_array()
        self._recompute_sample_after()
        self._render_wipe()
        self._casting_controller.on_stack_or_image_changed()
        self.image_loaded.emit(array.shape[1], array.shape[0])
        self._set_video_transport_position(frame_index)
        return True

    def _on_timeline_frame_changed(self):
        """TimelineController.frame_changed fired. Updates the RECORD
        field unconditionally -- the Timeline's own position is
        unambiguous regardless of which mode the toggle is on, per
        direct feedback: "one source [field] ... and the record
        timecode" (a standing field, not mode-gated the way Source is).
        Updates the shared _source_timecode_label too, but ONLY while
        RECORD mode is active -- the Timeline's CURRENT clip's own
        source position ("the clip under the playhead"); SOURCE mode's
        writes to that same field come from _update_video_timecode_label
        instead, gated the identical way in reverse, so exactly one of
        the two ever writes to it at a time.

        Also keeps the scrub bar's VALUE live during RECORD-mode
        playback/seeking (its RANGE is set separately, see
        _refresh_scrub_slider_for_mode) -- gated to RECORD only, so a
        stray tick while SOURCE mode is active can't overwrite the
        scrub bar's current SOURCE-range value with a position from the
        wrong numbering."""
        timeline_obj = self._timeline_getter() if self._timeline_getter else None
        source_frame = self._timeline_controller.current_source_frame()
        record_frame = self._timeline_controller.current_record_frame()
        if timeline_obj is None or record_frame is None:
            self._record_timecode_label.setText("")
            if self._viewer_mode == "record":
                self._source_timecode_label.setText("")
            return
        self._record_timecode_label.setText(
            timecode.frames_to_timecode(record_frame, timeline_obj.frame_rate))
        if self._viewer_mode == "record":
            if source_frame is not None:
                self._source_timecode_label.setText(
                    timecode.frames_to_timecode(source_frame, timeline_obj.frame_rate))
                self._source_timecode_label.setToolTip(self._SOURCE_TIMECODE_TOOLTIP)
            else:
                self._source_timecode_label.setText("")
            self._suppress_video_scrub_signal = True
            self._video_scrub_slider.setValue(record_frame)
            self._suppress_video_scrub_signal = False

    def activate_clip_mode(self):
        """Switch Record to CLIP mode (grading one clip, no timeline) --
        "Grade in Record". Counterpart of activate_record_mode."""
        if self._viewer_mode == "source":
            return
        self._timeline_controller.pause()
        self._set_viewer_mode("source")
        self._update_video_play_button_text()

    def activate_record_mode(self):
        """Public switch to RECORD mode, for a caller outside this panel
        (MainWindow, when a seek originates from the Timeline view
        itself -- see its own wiring of TimelinePanel.seek_requested).
        Clicking the Timeline's ruler should visibly put the Viewer into
        RECORD, not silently seek the Timeline underneath a still-SOURCE
        transport (scrub bar/labels gated to the active mode, see
        _on_timeline_frame_changed's own docstring) -- without this, the
        Timeline view's own playhead would move while the transport
        looked frozen, an actual reported "playhead desync." No-op if
        already in RECORD mode. Same pause-the-side-being-left-first
        reasoning as _on_viewer_mode_toggled, but only the SOURCE side --
        this is called immediately before a RECORD-mode seek, which
        pauses the Timeline controller itself already."""
        if self._viewer_mode == "record":
            return
        self._playback_controller.pause()
        self._set_viewer_mode("record")
        self._update_video_play_button_text()

    def _on_viewer_mode_toggled(self, checked):
        """The SOURCE/RECORD toggle button itself (per Tom's follow-up:
        "it should be a toggle you can control all the time" -- not
        something play/pause flips automatically anymore, see this
        panel's own docstring on the toggle button). Pauses BOTH
        transport controllers before switching -- there's only one
        Viewer display (self.compare), so if the one you're leaving was
        mid-playback it must stop before the other can safely start;
        without this, switching mid-playback would leave two playback
        loops both trying to push frames to the same surface."""
        self._playback_controller.pause()
        self._timeline_controller.pause()
        self._set_viewer_mode("record" if checked else "source")
        self._update_video_play_button_text()

    def _set_viewer_mode(self, mode):
        """"source" or "record" -- see _active_transport_controller/
        _on_viewer_mode_toggled and style.py's QFrame#viewerModeFrame/
        #viewerModeBadge rules. Purely a manual switch (this panel's own
        toggle button, see _on_viewer_mode_toggled) -- NOT driven by
        play/pause state; SOURCE and RECORD are equally valid states to
        be sitting in while paused OR while playing.

        The badge button's own RECORD/SOURCE look comes from Qt's
        native :checked pseudo-state (style.py, same convention the
        Loop transport button already uses) -- setChecked() below
        repaints it with no unpolish/polish dance needed. The wrapping
        frame isn't a button (no native checked state to hook), so it
        still goes through the plain custom-property + repolish
        convention every other dynamic-property QSS rule in this app
        uses (see stack_panel.py's StackStageRow.set_selected)."""
        if mode == self._viewer_mode:
            return
        self._viewer_mode = mode
        self._viewer_mode_badge.setText(MODE_LABELS[mode])
        self._viewer_mode_badge.blockSignals(True)
        self._viewer_mode_badge.setChecked(mode == "record")
        self._viewer_mode_badge.blockSignals(False)
        self._viewer_mode_frame.setProperty("viewerMode", mode)
        self._viewer_mode_frame.style().unpolish(self._viewer_mode_frame)
        self._viewer_mode_frame.style().polish(self._viewer_mode_frame)
        self._refresh_scrub_slider_for_mode()
        self._refresh_source_timecode_for_mode()

    def _refresh_source_timecode_for_mode(self):
        """Immediately recomputes the shared Source timecode field to
        match self._viewer_mode -- called on every mode switch, same
        "don't wait for the next tick" reasoning as _refresh_scrub_
        slider_for_mode. Without this, switching modes while PAUSED
        would leave the field showing whatever the PREVIOUSLY active
        mode's own last tick wrote, not the mode just switched to (the
        Record field needs no equivalent -- it's mode-independent and
        already kept live by every _on_timeline_frame_changed call)."""
        if self._viewer_mode == "source":
            self._update_video_timecode_label(self._video_frame_index)
        else:
            timeline_obj = self._timeline_getter() if self._timeline_getter else None
            source_frame = self._timeline_controller.current_source_frame()
            if timeline_obj is not None and source_frame is not None:
                self._source_timecode_label.setText(
                    timecode.frames_to_timecode(source_frame, timeline_obj.frame_rate))
            else:
                self._source_timecode_label.setText("")
            self._source_timecode_label.setToolTip(self._SOURCE_TIMECODE_TOOLTIP)

    def _refresh_scrub_slider_for_mode(self):
        """Recomputes the scrub bar's range AND position to match
        self._viewer_mode -- SOURCE shows the single directly-loaded/
        selected clip's own frame range (self._video_source), RECORD
        shows the WHOLE Timeline's (timeline.Timeline.total_frame_count
        -- per direct feedback: "the scrub bar seems to only represent a
        single clip in the timeline. it should represent the whole
        timeline"). Called on every mode switch (_set_viewer_mode) and
        whenever the underlying thing that mode's range depends on
        changes (refresh_video_transport_visibility -- a new Source
        video loaded/closed, or the Timeline's own clip list changing,
        see that method's own docstring on both triggers) -- NOT on
        every playback tick, see _on_video_frame_changed/_on_timeline_
        frame_changed for the per-tick position updates that keep the
        already-set range's VALUE live during actual playback.

        No Timeline-level in/out sub-range exists yet (see this
        module's own docstring and timeline_playback_controller.py's),
        so RECORD mode always clears the scrub bar's in/out markers --
        SOURCE mode still shows the single clip's own, via the existing
        _sync_video_in_out_markers."""
        self._suppress_video_scrub_signal = True
        if self._viewer_mode == "record":
            timeline_obj = self._timeline_getter() if self._timeline_getter else None
            total = timeline_obj.total_frame_count() if timeline_obj else 0
            self._video_scrub_slider.setMaximum(max(0, total - 1))
            record_frame = self._timeline_controller.current_record_frame()
            self._video_scrub_slider.setValue(record_frame if record_frame is not None else 0)
            self._video_scrub_slider.set_in_out(None, None)
        else:
            source = self._video_source
            self._video_scrub_slider.setMaximum(max(0, source.frame_count - 1) if source else 0)
            self._video_scrub_slider.setValue(self._video_frame_index if source else 0)
            self._sync_video_in_out_markers()
        self._suppress_video_scrub_signal = False

    def _on_video_frame_changed(self, frame_index):
        # Realtime playback ticking -- update the slider/timecode WITHOUT
        # touching self._video_frame_index (that stays at the last
        # PARKED position until pause re-syncs it -- see
        # VideoPlaybackController.pause's own docstring). Scrub bar
        # write gated to SOURCE mode, same reasoning as _on_timeline_
        # frame_changed's own RECORD-only gate -- its RANGE belongs to
        # whichever mode is active (see _refresh_scrub_slider_for_mode),
        # so a stray SOURCE tick while RECORD is active must not stomp
        # the Timeline-range value with a single-clip frame index.
        if self._viewer_mode == "source":
            self._suppress_video_scrub_signal = True
            self._video_scrub_slider.setValue(frame_index)
            self._suppress_video_scrub_signal = False
        self._update_video_timecode_label(frame_index)

    def _on_video_playback_stopped(self):
        self._update_video_play_button_text()
