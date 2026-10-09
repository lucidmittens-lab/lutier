"""main_window_parts/pipeline.py -- reacting to Stack changes: the effective
stack (colour_pipeline's input, operator-space and display wrapping), input
colour spaces, the composed preview, Sequence/Shot mode, and Gallery
stills. A mixin: MainWindow inherits it, so these methods share the
window's own `self`.
"""
import colour_pipeline
import stack as st
import image_io


class PipelineMixin:
    def _capture_gallery_still(self):
        """Wired to StackMemoriesPanel.set_still_capture_getter -- builds
        the full CANVAS-resolution graded array a Stack Memory captures
        alongside its thumbnail at save/update time (Phase 3 follow-up,
        per Tom: "also capture full canvas-res still"). Returns None (no
        capture, never a crash) if there's no Sample loaded, the working
        stack is empty, or the render itself fails -- same tolerance
        stack_memories_panel.py's own capture path already has for a
        missing/failed thumbnail.

        Uses render_full_resolution_after_for_capture (handles BOTH a
        still-backed Sample and a parked VIDEO frame -- see that method's
        own docstring), then resizes to self._canvas.width/height if the
        render doesn't already match -- same per-frame resize convention
        video_export.py's own export_video_cpu already uses, just for one
        frame instead of a whole clip. pixel_aspect_ratio is NOT baked
        into the raster here (a PNG has no standard slot for it the way
        video_export's container-level sample_aspect_ratio does) --
        display-only, same as everywhere else a canvas aspect ratio is
        applied to the Viewer rather than the pixels themselves."""
        if not self.sample_image.has_sample_image():
            return None
        stack = self.stack_panel.get_stack()
        if not stack.stages:
            return None
        try:
            array = self.sample_image.render_full_resolution_after_for_capture(stack)
        except Exception:  # noqa: BLE001 -- best-effort capture, never blocks the actual save
            return None
        c = self._canvas
        if array.shape[1] != c.width or array.shape[0] != c.height:
            array = image_io.resize_array(array, c.width, c.height)
        return array

    def _on_stack_changed(self):
        """Fires on every stage add/remove/reorder/enable-toggle/opacity/
        param edit (StackPanel.stack_changed). No LUT is baked here for
        export -- that happens only in _resolve_export_source, at the
        session's BakeSettings (Tom, 2026-10-05). This only checks the
        stack's structure and refreshes the Graph's preview bake if the
        Graph is on screen (_refresh_graph_preview). Sample Image previews
        the stack directly (see set_stack_getter above).

        A stack can pass through a transiently invalid structural state
        (stack.check_stack_structure) -- most notably, deleting one half of a fork_split/fork_merge pair
        via that row's own "x" button (see stack_panel.py's
        _StageRowWidget.delete_button/_on_row_delete_clicked), which is
        an entirely ordinary, expected single-click action for every
        other stage kind but leaves an unmatched split or merge behind
        here until its other half is removed too. This fires on every
        single edit -- including live slider drags -- so a QMessageBox
        here would be far too disruptive; StackPanel.set_bake_error
        surfaces it inline instead (see its docstring) and the Graph keeps
        its last valid preview rather than crashing the whole app over
        what's very likely a mid-edit, about-to-be-fixed structure."""
        try:
            st.check_stack_structure(self.stack_panel.get_stack().stages)
        except ValueError as e:
            self.stack_panel.set_bake_error(str(e))
            return
        self.stack_panel.set_bake_error(None)
        self._graph_preview_stale = True
        self._refresh_graph_preview()

    def _refresh_graph_preview(self):
        """The Graph panel's live LUT view -- baked at the fixed
        DEFAULT_BAKE_SETTINGS (33³ over [0,1]), never the session's export
        size/domain, and only while the Graph is actually on screen (Tom,
        2026-10-05: "live, only when visible"). A hidden/tabbed-away Graph
        just stays stale; the Show event filter (see eventFilter) catches
        it up the moment it's visible again."""
        if not self._graph_preview_stale or not self.viewer_3d.isVisible():
            return
        try:
            baked = self.stack_panel.get_baked_lut(st.DEFAULT_BAKE_SETTINGS)
        except Exception as e:  # noqa: BLE001 -- a stage's own failure; shown inline, never crashes
            self.stack_panel.set_bake_error(str(e))
            return
        self._graph_preview_stale = False
        self.viewer_3d.set_lut(baked)

    # ---- Sequence/Shot toggle (see stack_panel.py's own docstring) -------

    def _display_transform(self):
        c = self._canvas
        return colour_pipeline.DisplayTransform(c.display_drt, c.display_name, c.display_view)

    def _effective_stack(self, user_stack, input_colourspace):
        """The Stack Record actually renders (colour_pipeline.py): the
        source's input transform, each stage in its own space, and the
        project's display transform, around `user_stack`."""
        return colour_pipeline.build_effective_stack(
            user_stack, self._canvas.working_colourspace, input_colourspace, self._display_transform())

    def _input_colourspace_for_path(self, path):
        """The input color space of the Media Library entry for `path`, or
        None (not in a library / already in the working space)."""
        if not path:
            return None
        for library in self.media_panel.get_pool().libraries:
            for entry in library.entries:
                if entry.source_path == path:
                    return entry.source_colourspace
        return None

    def _timeline_pipeline(self, clip, composed):
        return self._effective_stack(composed, clip.input_colourspace)

    def _on_input_colourspace_changed(self, entry):
        """A Media Library entry's input space changed: every timeline clip
        of that file follows it, and Record re-renders."""
        for library in self.media_panel.get_pool().libraries:
            for timeline_obj in library.timelines:
                for clip in timeline_obj.clips:
                    if clip.source_path == entry.source_path:
                        clip.input_colourspace = entry.source_colourspace
        self.sample_image.refresh_preview()

    def _composed_preview_stack(self):
        """The Viewer's own live-preview stack -- ALWAYS the currently
        selected Timeline clip's own grade composed with the Sequence
        grade together (stack.compose_stacks), regardless of which ONE
        of them the Sequence/Shot toggle currently has loaded for
        editing (Tom, after trying the build: "the result i see in the
        viewer should always be the result of the clip level grade and
        then the timeline level grade... right now when i switch back
        and forth it only shows one or the other"). Deliberately NOT
        stack_panel.get_stack() (which answers a different question --
        "what is StackPanel's editor currently showing," Sequence OR
        Shot, never both -- see that method's own docstring) and NOT
        get_sequence_stack() alone either.

        Falls back to the Sequence stack alone when no Timeline clip is
        selected -- ordinary non-Timeline Source/Reference grading,
        unchanged from before this feature existed. Wired as sample_
        image's own stack_getter (see _build_layout), so this same
        composition automatically covers the Viewer AND Scopes (both
        read through that one getter already -- see sample_image_panel.
        py's refresh_preview) without needing a second wire for each."""
        clip = self.timeline_panel.get_selected_clip()
        sequence_stack = self.stack_panel.get_sequence_stack()
        if clip is None:
            input_space = self._input_colourspace_for_path(self.sample_image.current_path)
            user_stack = sequence_stack
        else:
            input_space = clip.input_colourspace
            user_stack = st.compose_stacks(clip.stack, sequence_stack)
        self._refresh_pipeline_summary(input_space)
        return self._effective_stack(user_stack, input_space)

    def _refresh_pipeline_summary(self, input_space):
        c = self._canvas
        if c.display_drt == "aces":
            display = f"{c.display_view} ({c.display_name.replace(' - Display', '')})"
        else:
            display = "none (working space)"
        self.stack_panel.set_pipeline_summary(input_space, c.working_colourspace, display)

    def _on_sequence_mode_requested(self):
        """The user clicked "Sequence" -- wrapped in _restoring_stack_
        history, same reasoning as _undo/_redo/_new_session/_open_session's
        own set_stack() calls: switching which Stack is loaded into the
        editor is a wholesale replace, not a genuine edit, and must never
        itself create a spurious undo entry (stack_panel.py's
        activate_sequence_mode/activate_shot_mode both fire stack_changed,
        same as any other stack swap). StackPanel can't set this flag
        itself -- it has no reference to MainWindow -- hence routing the
        actual activation back out through here instead of letting the
        button call activate_sequence_mode() directly."""
        self._restoring_stack_history = True
        try:
            self.stack_panel.activate_sequence_mode()
        finally:
            self._restoring_stack_history = False
        self._update_undo_redo_actions()

    def _on_shot_mode_requested(self, clip):
        """The user clicked "Shot" -- same wrapping reasoning as
        _on_sequence_mode_requested above."""
        self._restoring_stack_history = True
        try:
            self.stack_panel.activate_shot_mode(clip)
        finally:
            self._restoring_stack_history = False
        self._update_undo_redo_actions()
