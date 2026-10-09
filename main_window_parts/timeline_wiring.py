"""main_window_parts/timeline_wiring.py -- the timeline and Source viewer
wiring: clip selection, seeking, media dropped on the timeline, Source
edits, match frame, and loading or renaming timelines from the library. A
mixin: MainWindow inherits it, so these methods share the window's own
`self`.
"""
import timeline as tl
import timecode


class TimelineMixin:
    def _on_timeline_clip_selected(self, clip):
        """TimelinePanel.clip_selected fired -- same wrapping reasoning as
        _on_sequence_mode_requested above: notify_selected_clip_changed
        can itself call activate_sequence_mode/activate_shot_mode (re-
        pointing Shot mode at a newly selected clip, or falling back to
        Sequence if the selection was cleared), which must not create a
        spurious undo entry either.

        Also loads the newly selected clip's own footage into the
        Viewer's Source slot (Tom, after trying the skeleton build:
        "theres no way to use the timeline panel to select which clip
        you want to work on" / "it defaults to the sample test image" --
        selecting a clip previously only swapped which Stack Shot mode
        would edit, with nothing to show it against; a Shot-mode grade
        with no visible connection to the footage it's grading is not
        usable). Reuses load_video/load_image exactly as if the user had
        picked "Load as Source" on that same file -- this is what makes
        the EXISTING scrub bar (see sample_image_panel.py's own
        "transport exclusive to timeline, scrub exclusive to clip" split)
        actually have something to scrub once a clip is selected. Does
        nothing on deselection (clip is None) -- leaves whatever was
        already loaded as Source alone rather than clearing it.

        Also re-points TimelineController at this clip (seek_to_clip) so
        the NEXT Play starts here, not wherever an earlier play/pause
        session left off -- see that method's own docstring."""
        self._restoring_stack_history = True
        try:
            self.stack_panel.notify_selected_clip_changed(clip)
        finally:
            self._restoring_stack_history = False
        self._update_undo_redo_actions()
        if clip is not None:
            self.sample_image.activate_record_mode()
            if clip.media_kind == "video":
                self.sample_image.load_video(clip.source_path)
            else:
                self.sample_image.load_image(clip.source_path)
            # Also points a SUBSEQUENT Play at this clip -- see
            # TimelineController.seek_to_clip's own docstring.
            clip_index = self.timeline_panel.get_selected_clip_index()
            if clip_index is not None:
                self.sample_image._timeline_controller.seek_to_clip(clip_index)

    def _on_timeline_seek_requested(self, record_frame):
        """TimelinePanel.seek_requested fired -- a click OR a continuous
        drag on the Timeline view's own ruler/empty track (see
        TimelineTrackWidget's own mousePressEvent/mouseMoveEvent, which
        fire this repeatedly while dragging, not just once). Switches
        the Viewer to RECORD first (see SampleImagePanel.
        activate_record_mode's own docstring on why: without it, the
        Timeline view's playhead would move while the transport's scrub
        bar/labels stayed frozen on a stale SOURCE position, a visible
        desync), then routes the seek through the transport's own scrub
        bar (setValue -> _on_video_scrub_slider_changed) rather than
        calling TimelineController.seek_to_record_frame directly -- this
        is what gives a Timeline-view drag-scrub the same scrub-time
        downres treatment (SCRUB_DOWNRES_FACTOR) a drag on the
        horizontal scrub bar already gets, and keeps that scrub bar's
        own displayed value in step with wherever the Timeline view's
        playhead is being dragged to."""
        self.sample_image.activate_record_mode()
        self.sample_image._video_scrub_slider.setValue(record_frame)

    def _on_media_entry_dropped_on_timeline(self, entry_id, index):
        """TimelinePanel.media_entry_drop_requested fired -- a Media
        Library row dragged onto the Timeline view (per Tom: "make it
        more similar to a typical NLE"). Resolves `entry_id` against
        MediaPanel's own library (the Timeline panel has no reference to
        it) and inserts a full-range clip at `index`. Silently a no-op
        if the entry was removed from the library between the drag
        starting and landing -- same "stale id, just drop it" tolerance
        TimelinePanel._selected_clip's own KeyError guards use elsewhere
        for a vanished row."""
        try:
            entry = self.media_panel.find_entry(entry_id)
        except KeyError:
            return
        self.timeline_panel.insert_clip_from_library_entry_at(entry, index)

    def _open_in_source_viewer(self, entry):
        self.source_viewer_frame.setVisible(True)
        self.source_viewer_frame.raise_()
        self.source_viewer.load_entry(entry)

    def _on_source_edit_requested(self, mode, spec):
        """Source viewer's Insert / Overwrite / Append: the marked range of
        the Source clip into the loaded timeline (a new one is made in the
        current library if none is loaded), at Record's playhead."""
        timeline_obj = self.timeline_panel.get_timeline()
        if timeline_obj is None:
            self.media_panel.new_timeline()
            timeline_obj = self.timeline_panel.get_timeline()
            if timeline_obj is None:
                return
        in_frame, out_frame = spec["in_frame"], spec["out_frame"]
        if spec["kind"] != "video":
            in_frame, out_frame = 0, self.timeline_panel.still_hold_out_frame()
        clip = tl.TimelineClip(spec["name"], spec["path"], spec["kind"], in_frame, out_frame,
                               thumbnail=spec.get("thumbnail"))
        record_frame = self.sample_image._timeline_controller.current_record_frame() or 0
        self.timeline_panel.apply_edit(mode, clip, record_frame)
        self.sample_image.activate_record_mode()

    def match_frame(self):
        """Match Frame (F): load the clip under Record's playhead into the
        Source viewer, on the same source frame."""
        controller = self.sample_image._timeline_controller
        timeline_obj = self.timeline_panel.get_timeline()
        record_frame = controller.current_record_frame()
        if timeline_obj is None or not timeline_obj.clips or record_frame is None:
            return
        clip_index, offset = controller._locate_record_frame(timeline_obj, record_frame)
        clip = timeline_obj.clips[clip_index]
        start_frame = 0
        for library in self.media_panel.get_pool().libraries:
            entry = next((e for e in library.entries if e.source_path == clip.source_path), None)
            if entry is not None:
                tc = entry.metadata.get("start_timecode")
                start_frame = timecode.timecode_to_frames(tc, entry.frame_rate or 0) or 0 if tc else 0
                break
        self.source_viewer_frame.setVisible(True)
        self.source_viewer.load_path(clip.source_path, clip.media_kind, clip.name,
                                     frame_index=clip.in_frame + offset, start_frame=start_frame,
                                     thumbnail=clip.thumbnail)

    def _load_timeline(self, timeline):
        """Open `timeline` (a Media Library timeline, or None to unload) in
        the Timeline panel. Stops Timeline playback first and drops any
        Shot-mode clip selection, same as New/Open Session do when they
        replace the timeline."""
        if timeline is self.timeline_panel.get_timeline():
            return
        self.sample_image._timeline_controller.stop()
        self.stack_panel.notify_selected_clip_changed(None)
        self.timeline_panel.load_timeline(timeline)
        if timeline is not None:
            self.sample_image.activate_record_mode()

    def _on_library_timeline_renamed(self, timeline):
        if timeline is self.timeline_panel.get_timeline():
            self.timeline_panel.refresh_timeline_name()

    def _on_library_timeline_deleted(self, timeline):
        if timeline is self.timeline_panel.get_timeline():
            self._load_timeline(None)
