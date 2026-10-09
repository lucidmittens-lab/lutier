"""main_window_parts/render.py -- the Render suggested layout's wiring
(2026-10-09): turns Render Settings' RenderRequest into render_job
RenderItems (the planner), and queues them on the Render Queue. A mixin:
MainWindow inherits it, so these methods share the window's own `self`.

Each segment's stack is what Record renders for that clip -- the clip's
grade composed with the Sequence grade, wrapped by the colour pipeline
(_composed_preview_stack's recipe) -- copied when queued, so later edits
don't reach a queued job. "Working Space" drops the display transform.
"""
import datetime
import os

from PySide6.QtWidgets import QMessageBox

import colour_pipeline
import render_job as rj
import stack as st
from render_settings_panel import TIMELINE


class RenderMixin:
    def _wire_render_panels(self):
        self.render_settings_panel.set_planner(self._plan_render)
        self.render_settings_panel.add_requested.connect(self._on_render_add_requested)
        for signal in (self.timeline_panel.timeline_changed, self.timeline_panel.clip_selected,
                       self.sample_image.image_loaded):
            signal.connect(lambda *_a: self.render_settings_panel.refresh())

    def _render_stack(self, user_stacks, input_colourspace, as_viewed):
        snapshot = st.compose_stacks(*(s.copy() for s in user_stacks))
        display = self._display_transform() if as_viewed else colour_pipeline.NO_DISPLAY_TRANSFORM
        return colour_pipeline.build_effective_stack(
            snapshot, self._canvas.working_colourspace, input_colourspace, display)

    def _render_source_size(self, path, media_kind, request):
        if not request.use_source_size:
            return None
        cache = self._render_size_cache
        if path not in cache:
            cache[path] = rj.native_size(path, media_kind)
        return cache[path]

    def _render_segment_for_clip(self, clip, sequence_stack, request, timeline_rate):
        rate = timeline_rate
        if clip.media_kind == "video":
            rate = self._render_clip_frame_rate(clip.source_path) or timeline_rate
        return rj.Segment(
            clip.name, clip.source_path, clip.media_kind, clip.in_frame, clip.out_frame,
            self._render_stack((clip.stack, sequence_stack), clip.input_colourspace, request.as_viewed),
            reel=self._render_reel(clip.source_path), frame_rate=rate,
            source_size=self._render_source_size(clip.source_path, clip.media_kind, request))

    def _render_entry_for(self, path):
        for library in self.media_panel.get_pool().libraries:
            for entry in library.entries:
                if entry.source_path == path:
                    return entry
        return None

    def _render_reel(self, path):
        entry = self._render_entry_for(path)
        return entry.metadata.get("reel", "") if entry is not None else ""

    def _render_clip_frame_rate(self, path):
        entry = self._render_entry_for(path)
        return entry.frame_rate if entry is not None and entry.frame_rate else None

    def _plan_render(self, request):
        """Render Settings' planner: [RenderItem] for `request`, or
        ValueError saying what's missing."""
        sequence_stack = self.stack_panel.get_sequence_stack()
        timeline_rate = None
        if request.source == TIMELINE:
            timeline = self.timeline_panel.get_timeline()
            if timeline is None or not timeline.clips:
                raise ValueError("The timeline is empty. Add clips from Media Libraries.")
            name, timeline_rate = timeline.name, timeline.frame_rate
            segments = [self._render_segment_for_clip(clip, sequence_stack, request, timeline_rate)
                        for clip in timeline.clips]
        else:
            clip = self.timeline_panel.get_selected_clip()
            if clip is not None:
                name = clip.name
                segments = [self._render_segment_for_clip(
                    clip, sequence_stack, request, self.timeline_panel.get_timeline().frame_rate)]
            else:
                name, segments = self._render_viewer_segment(sequence_stack, request)
        canvas = self._canvas
        settings = rj.RenderSettings(
            format_label=request.format_label, bit_depth=request.bit_depth,
            directory=request.directory, name_template=request.name_template,
            single_file=request.single_file or request.source != TIMELINE,
            use_source_size=request.use_source_size, width=canvas.width, height=canvas.height,
            pixel_aspect_ratio=canvas.pixel_aspect_ratio)
        return rj.plan_items(name, segments, settings, datetime.date.today().isoformat(),
                             timeline_frame_rate=timeline_rate)

    def _render_viewer_segment(self, sequence_stack, request):
        """The media loaded in Record (no timeline clip selected), whole."""
        path = self.sample_image.current_path
        if not path:
            raise ValueError("Load a clip in Record, or select one on the Timeline.")
        source = self.sample_image._video_source
        if source is not None:
            kind, last, rate = "video", max(0, source.frame_count - 1), source.frame_rate
        else:
            kind, last, rate = "still", 0, self._canvas.frame_rate
        entry = self._render_entry_for(path)
        name = entry.name if entry is not None else os.path.splitext(os.path.basename(path))[0]
        segment = rj.Segment(
            name, path, kind, 0, last,
            self._render_stack((sequence_stack,), self._input_colourspace_for_path(path),
                               request.as_viewed),
            reel=self._render_reel(path), frame_rate=rate,
            source_size=self._render_source_size(path, kind, request))
        return name, [segment]

    def _on_render_add_requested(self, request):
        try:
            items = self._plan_render(request)
        except ValueError as e:
            QMessageBox.information(self, "Nothing to render", str(e))
            return
        if not items:
            return
        queued = {p for job in self.render_queue_panel.jobs() if not job.is_finished
                  for p in job.output_paths()}
        clashes = sorted(set(rj.existing_outputs(items))
                         | {i.output_path for i in items if i.output_path in queued})
        if clashes:
            listing = "\n".join(clashes[:6]) + ("\n…" if len(clashes) > 6 else "")
            answer = QMessageBox.question(
                self, "Replace existing files?",
                f"These already exist or are already queued, and will be replaced:\n\n{listing}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Yes:
                return
        name = items[0].name if len(items) == 1 else f"{items[0].name} + {len(items) - 1}"
        self.render_queue_panel.add_job(rj.RenderJob(name, items))
