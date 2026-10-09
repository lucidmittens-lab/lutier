"""main_window_parts/layout.py -- MainWindow._build_layout: builds every panel
and dock and wires the panels' signals to each other. A mixin: MainWindow
inherits it, so these methods share the window's own `self`.
"""
from pathlib import Path
from browser_panel import BrowserPanel
from stack_memories_panel import StackMemoriesPanel
from lut_library_panel import LUTLibraryPanel
from media_panel import MediaPanel
from timeline_panel import TimelinePanel
from sample_image_panel import SampleImagePanel
from stack_panel import StackPanel
from source_viewer import SourceViewerPanel
from scopes_panel import ScopesPanel
from lut_viewer_3d import LutViewer3D
import canvas as cv
from .constants import PANEL_TOGGLES


class LayoutMixin:
    def _build_layout(self):
        # No central widget -- every panel is a QDockWidget (see
        # _make_dock), so the dock area is free to occupy the ENTIRE
        # window rather than surrounding a fixed centerpiece. Nesting
        # enabled so a column can itself be split further (see
        # _build_default_dock_layout, and any future drag the user makes)
        # rather than being limited to one split per edge.
        self.setDockNestingEnabled(True)

        # StackPanel built first (before the left column) since the
        # Stack Memories panel needs a live reference to it
        # (set_stack_getter) at construction time -- nothing about
        # building StackPanel itself depends on anything built after it.
        self.stack_panel = StackPanel()
        self.stack_panel.stack_changed.connect(self._on_stack_changed)

        # ---- left column: Browser (top) / Media Libraries / LUT Library -
        # ---- / Galleries (bottom) -----------------------------------
        # Four separate panels now (per Tom, Media Libraries inserted
        # between Browser and LUT Library) -- used to be one Browser
        # PanelFrame with an internal "Files" / "Stack Memories" QTabWidget,
        # split into their own stacked group so all four are visible at
        # once instead of one-tab-at-a-time. "Files" is the unchanged
        # filesystem LUT browser, "Media Libraries" (between Browser and
        # LUT Library, per Tom) is a sortable, columned table of media
        # files catalogued by reference (path + header metadata, never a
        # decoded pixel array) -- see media_panel.py/media_library.py --
        # "LUT Library" is a shelf of LUTs ingested
        # into session memory -- see lut_library_panel.py/lut_library.py --
        # "Galleries" is a shelf of named, saved Stack snapshots you can
        # recall/update/rename/delete/export/import, externally-imported
        # reference stills, AND named, saved ASC CDL grades (folded in per
        # Tom, see stack_memories_panel.py's own docstring) -- all three
        # libraries ride along with the session file (see session_io.py).
        self.browser = BrowserPanel()
        # Loading a file from the Browser ADDS a stage to the Stack
        # instead of replacing "the current LUT" -- there's no separate
        # current-LUT slot anymore, see stack_panel.py. "New Identity
        # LUT..." (per Tom, removed) used to be a second entry point
        # here -- see stack_panel.py's docstring on why previewing an
        # empty stack directly made that redundant.
        self.browser.file_selected.connect(lambda path: self.stack_panel.add_lut_stage_from_path(path))
        self._make_dock("browser_frame", "Browser", self.browser)

        # Media Panel's two apply_requested signals get connected to
        # self.sample_image once that's built further below (SampleImagePanel
        # doesn't exist yet at this point in the method).
        self.media_panel = MediaPanel()
        # "image_library_frame" (the dock's objectName, used by
        # workspace_io.py's saved-layout persistence) deliberately stays
        # the old name from before the Media Panel rewrite -- renaming it
        # would silently reset this one panel's remembered position for
        # every user with an existing saved workspace layout, same reason
        # session_io.py's load_session keeps reading the old
        # "image_library" session key too.
        self._make_dock(
            "image_library_frame", "Media Libraries", self.media_panel,
            corner_widget=self.media_panel.menu_button)

        self.lut_library_panel = LUTLibraryPanel()
        # Applying an entry hands back the LUTLibraryEntry itself (already
        # holding the fully-loaded LUT, no disk read here) --
        # StackPanel.add_lut_stage_from_library_entry adds a new stage
        # from it directly, tagged so session_io.py embeds its table
        # rather than a file path (see that method's docstring). This
        # panel has no idea StackPanel exists, same arrangement as CDL
        # Library/Stack Memories.
        self.lut_library_panel.apply_requested.connect(self.stack_panel.add_lut_stage_from_library_entry)
        # corner_widget=self.lut_library_panel.menu_button -- Import.../
        # Apply to Stack/Rename.../Delete moved into the "..." header menu
        # (per Tom), same treatment as Viewer/Stack/Scopes.
        self._make_dock(
            "lut_library_frame", "LUT Library", self.lut_library_panel,
            corner_widget=self.lut_library_panel.menu_button)

        self.stack_memories_panel = StackMemoriesPanel()
        self.stack_memories_panel.set_stack_getter(self.stack_panel.get_stack)
        # Recalling a memory hands back a ready-to-use (already-copied)
        # Stack -- this panel has no idea StackPanel exists, MainWindow is
        # what wires the two together, same arrangement as opening a
        # session.
        self.stack_memories_panel.recall_requested.connect(self.stack_panel.set_stack)
        # CDL (folded into this Gallery, per Tom -- see this panel's own
        # docstring). Applying a CDL entry hands back nothing itself -- it
        # emits the CDLEntry, and StackPanel.apply_cdl_entry decides
        # whether to update the currently-selected 'cdl' stage in place or
        # add a new one (see that method's docstring). This panel has no
        # idea StackPanel exists, same arrangement as Recall above.
        self.stack_memories_panel.apply_cdl_requested.connect(self.stack_panel.apply_cdl_entry)
        # "Insert in Stack" (per Tom, see StackPanel.insert_entry_in_stack's
        # own docstring) -- a Gallery row's own right-click action, ALWAYS
        # adds new stage(s) rather than replacing/overwriting, unlike
        # Recall/Apply CDL to Stack above.
        self.stack_memories_panel.insert_in_stack_requested.connect(
            self.stack_panel.insert_entry_in_stack)
        # The reverse direction -- saving FROM the CDL inspector's "Save
        # to CDL Library..." button (see stack_panel.py) -- routes back
        # into this panel the same way; StackPanel has no idea
        # StackMemoriesPanel exists either.
        self.stack_panel.cdl_save_requested.connect(self.stack_memories_panel.save_cdl_entry)
        # Double-clicking a CDL file (.cc/.cdl/.ccc) in the Browser routes
        # here too (see browser_panel.py's docstring on why CDL files land
        # in the Gallery rather than going straight to the Stack the way
        # LUT files do) -- same import_cdl_from_path() the "..." menu's
        # mono "Import..." action and drag-and-drop both already use.
        self.browser.cdl_file_selected.connect(self.stack_memories_panel.import_cdl_from_path)
        # Media double-clicked in the Browser -> the current Media Library.
        self.browser.media_files_activated.connect(self.media_panel.import_paths)
        # corner_widget=self.stack_memories_panel.menu_button -- same "..."
        # header-menu treatment as LUT Library above.
        self._make_dock(
            "stack_memories_frame", "Galleries", self.stack_memories_panel,
            corner_widget=self.stack_memories_panel.menu_button)

        # Continuity Grading Timeline (see timeline.py's own docstring) --
        # sources clips from the Media Panel only, wired further below once
        # self.media_panel's add_to_timeline_requested signal is
        # ready to connect (deferred, same reasoning as
        # load_as_sample_requested below).
        self.timeline_panel = TimelinePanel()
        self._make_dock(
            "timeline_frame", "Timeline", self.timeline_panel,
            corner_widget=self.timeline_panel.menu_button)

        # ---- center column: Viewer (top) / Stack (main area) -----------
        self.sample_image = SampleImagePanel()
        # Project canvas (Canvas Settings, per Tom 2026-08-13; Project
        # Settings since 2026-10-05). _canvas_confirmed: the user has
        # explicitly set it (Project Settings / New Project prompt / an
        # opened session) -- until then the first media loaded seeds it,
        # see _on_sample_image_loaded.
        self._canvas = cv.DEFAULT_CANVAS
        self._canvas_confirmed = False
        self.sample_image.image_loaded.connect(self._on_sample_image_loaded)
        self._apply_canvas_to_viewer()
        # corner_widget=self.sample_image.menu_button -- the "..." menu now
        # sits on the SAME row as the "Viewer" title (per Tom) instead of
        # its own row underneath; see panel_frame.py / sample_image_panel.py.
        self._make_dock(
            "sample_image_frame", "Record", self.sample_image,
            corner_widget=self.sample_image.menu_button)
        # The Source viewer: the clip loaded from Media Libraries, ungraded,
        # with its own transport and Insert/Overwrite/Append into the
        # timeline (source_viewer.py). Record (above) is the grading monitor.
        self.source_viewer = SourceViewerPanel()
        self.source_viewer.edit_requested.connect(self._on_source_edit_requested)
        self._make_dock("source_viewer_frame", "Source", self.source_viewer)
        # SampleImagePanel previews the live stack directly (no baking --
        # exact per-pixel apply, see stack.apply_stack_to_image_array),
        # re-rendering on every stack_changed the same way it used to for
        # ConcatenatePanel's chain_changed. _composed_preview_stack, not
        # a plain stack_panel.get_stack passthrough -- see that method's
        # own docstring on why (Tom: the Viewer must always show clip +
        # Sequence together, not whichever ONE of them the Sequence/Shot
        # toggle currently has loaded for editing).
        self.sample_image.set_stack_getter(self._composed_preview_stack)
        self.sample_image._timeline_controller.set_pipeline(self._timeline_pipeline)
        self.media_panel.input_colourspace_changed.connect(self._on_input_colourspace_changed)
        # Matte Highlight mode (per Tom: "a way to preview the mask
        # similar to resolve's highlight mode") -- same pull-based
        # convention as set_stack_getter just above; toggling Highlight
        # in a Keyer's own inspector already emits stack_changed, so no
        # separate signal/connection is needed for it to take effect.
        self.sample_image.set_highlight_getter(self.stack_panel.get_highlight_stage_id)
        # Eyedropper (per Tom: "we really need an eyedropper") -- PUSH
        # rather than pull (see set_eyedropper_requester's own docstring):
        # a Keyer inspector's own Pick button calls this to arm the
        # Viewer's one-shot click-to-sample.
        self.stack_panel.set_eyedropper_requester(self.sample_image.pick_pixel_color_for_stage)
        self.stack_panel.stack_changed.connect(self.sample_image.refresh_preview)
        # Locks the Stack panel for the duration of realtime video
        # playback (Tom, 2026-08-13: playback shows the grade, but the
        # grade itself must not be editable WHILE it's playing) -- the
        # panel re-enables the instant playback stops for any reason
        # (explicit pause, end of stream, or the video source going
        # away), see video_playback_controller.py's own docstring and
        # StackPanel.set_locked_for_playback's own docstring on why THAT
        # method (not a plain setEnabled call MainWindow used to make
        # directly) is what actually does the locking.
        self.sample_image._playback_controller.playback_started.connect(
            lambda: self.stack_panel.set_locked_for_playback(True))
        self.sample_image._playback_controller.playback_stopped.connect(
            lambda: self.stack_panel.set_locked_for_playback(False))
        # Same lock, for the same reason, during Timeline playback (see
        # timeline_playback_controller.py's own docstring) -- Play there
        # is reachable via the SAME transport bar as above now (Tom,
        # 2026-08-24: "the transport can be exclusive to the timeline"),
        # so both controllers need this exact lock independently.
        self.sample_image._timeline_controller.playback_started.connect(
            lambda: self.stack_panel.set_locked_for_playback(True))
        self.sample_image._timeline_controller.playback_stopped.connect(
            lambda: self.stack_panel.set_locked_for_playback(False))
        # Also locks the Timeline panel itself during Timeline playback --
        # TimelinePanel IS a plain QWidget (unlike StackPanel), so a
        # direct setEnabled works here. Without this, selecting a
        # different clip WHILE the Timeline is actively ticking would
        # call load_video/load_image (see _on_timeline_clip_selected)
        # and fight the running controller for control of the Viewer.
        self.sample_image._timeline_controller.playback_started.connect(
            lambda: self.timeline_panel.setEnabled(False))
        self.sample_image._timeline_controller.playback_stopped.connect(
            lambda: self.timeline_panel.setEnabled(True))
        # No image at launch (per Tom, 2026-10-05: "default to launching
        # without the test image loaded") -- the New Project prompt comes
        # up instead (show_on_launch). The bundled still is still one
        # click away: the Viewer menu's "Load Default Source".

        # Media Library entries "apply" into one of the Viewer's two
        # image slots rather than adding a Stack stage (see
        # media_panel.py's docstring) -- this panel has no idea
        # SampleImagePanel exists, MainWindow wires the two together, same
        # arrangement as every other library's apply_requested. Deferred to
        # here (rather than right after media_panel is built, up in
        # the left column above) since self.sample_image doesn't exist
        # until this point in the method.
        self.media_panel.load_as_sample_requested.connect(
            self.sample_image.load_sample_from_library_entry)
        self.media_panel.open_in_source_requested.connect(self._open_in_source_viewer)
        self.media_panel.load_as_reference_requested.connect(
            self.sample_image.load_reference_from_library_entry)
        self.media_panel.add_to_timeline_requested.connect(
            self.timeline_panel.add_clip_from_library_entry)
        # Timelines are Media Library items (media_library.py, LIBRARIES
        # AND TIMELINES): opening one loads it into the Timeline panel;
        # adding a clip with none loaded makes a new one in the current
        # library first.
        self.media_panel.timeline_load_requested.connect(self._load_timeline)
        self.media_panel.timeline_unload_requested.connect(lambda: self._load_timeline(None))
        self.media_panel.timeline_renamed.connect(self._on_library_timeline_renamed)
        self.media_panel.timeline_deleted.connect(self._on_library_timeline_deleted)
        self.media_panel.set_frame_rate_getter(lambda: self._canvas.frame_rate)
        self.timeline_panel.loaded_timeline_changed.connect(self.media_panel.set_loaded_timeline)
        self.timeline_panel.set_timeline_creator(self.media_panel.new_timeline)
        # Add to Sequence (per Tom: "will be necessary to be able to
        # mark desired ranges and add to sequence") -- the Viewer's own
        # marked-range counterpart to the Media Library's always-full-
        # range add just above; same "this panel has no idea the other
        # one exists" arrangement.
        self.sample_image.add_source_range_to_timeline_requested.connect(
            self.timeline_panel.add_clip_with_range)

        # Sequence/Shot toggle (see stack_panel.py's own docstring on
        # activate_shot_mode/notify_selected_clip_changed) -- this panel
        # has no idea TimelinePanel exists either, same arrangement as
        # every other cross-panel wire in this method. All three routes
        # into the actual mode switch go through MainWindow (below) rather
        # than straight into StackPanel, so each can be wrapped in
        # _restoring_stack_history -- see _on_sequence_mode_requested's
        # own docstring on why that's required here specifically.
        self.stack_panel.set_selected_clip_getter(self.timeline_panel.get_selected_clip)
        self.stack_panel.sequence_mode_requested.connect(self._on_sequence_mode_requested)
        self.stack_panel.shot_mode_requested.connect(self._on_shot_mode_requested)
        self.timeline_panel.clip_selected.connect(self._on_timeline_clip_selected)

        # Timeline playback (see timeline_playback_controller.py) -- pull-
        # based getters, same convention as every other cross-panel state
        # read in this method. timeline_changed re-evaluates the
        # transport bar's own visibility on every Timeline add/remove/
        # clear/load, not just on a single video's own load/unload (see
        # SampleImagePanel.refresh_video_transport_visibility's own
        # docstring on why both triggers are needed).
        self.sample_image.set_timeline_getter(self.timeline_panel.get_timeline)
        self.sample_image.set_sequence_stack_getter(self.stack_panel.get_sequence_stack)
        self.timeline_panel.timeline_changed.connect(self.sample_image.refresh_video_transport_visibility)

        # Timeline track widget playhead + click-to-seek (see
        # timeline_track_widget.py) -- same pull-based-getter convention
        # as current_record_frame's other consumers (e.g. the Record
        # timecode label). frame_changed already fires on every playback
        # tick AND every explicit seek (see TimelineController's own
        # docstring), so this single connection covers both "the
        # playhead moves while playing" and "the playhead jumps when you
        # click elsewhere."
        self.timeline_panel.set_record_frame_getter(
            self.sample_image._timeline_controller.current_record_frame)
        self.sample_image._timeline_controller.frame_changed.connect(
            self.timeline_panel.refresh_playhead)
        # Clicking the Timeline panel's ruler/empty track scrubs the whole
        # Timeline, same "click jumps straight to the cursor" convention
        # the Record-mode scrub bar already established. Also switches
        # the Viewer to RECORD first (activate_record_mode) -- without
        # this, seeking from the Timeline view while SOURCE mode was
        # showing left the transport's own scrub bar/labels frozen on
        # their old SOURCE position (gated to the active mode, see
        # sample_image_panel.py's _on_timeline_frame_changed), while the
        # Timeline view's own playhead moved -- a visible desync between
        # the two.
        self.timeline_panel.seek_requested.connect(self._on_timeline_seek_requested)
        # Dragging a Media Library row onto the Timeline (per Tom: "make
        # it more similar to a typical NLE") -- the Timeline panel has no
        # MediaLibrary reference of its own (same "the other panel's data
        # stays the other panel's" split every cross-panel wire in this
        # method already follows), so this is where the signal's own
        # entry_id gets resolved back to a real MediaEntry.
        self.timeline_panel.media_entry_drop_requested.connect(
            self._on_media_entry_dropped_on_timeline)

        # Stills Gallery (Phase 3, Stack Memories panel) -- same "deferred
        # wiring, this panel has no idea SampleImagePanel exists" reasoning
        # as Image Library above. set_still_capture_getter feeds
        # _capture_gallery_still (a full CANVAS-resolution graded render,
        # per Tom's follow-up: "also capture full canvas-res still") into
        # Stack Memory's automatic thumbnail+full_still capture at save
        # time; a GalleryStill's "Load as Reference" routes to
        # load_reference_from_path (not load_reference_from_library_entry
        # -- a GalleryStill has no cached decoded array, same as a
        # MediaEntry, see that class's own docstring), via a lambda
        # since both sides live on the UI thread (no cross-thread signal
        # concern, same as the video playback lock/unlock wiring above).
        self.stack_memories_panel.set_still_capture_getter(self._capture_gallery_still)
        # Feeds Grab Still's name-prompt suggestion (per Tom: "automatic
        # still naming with the source name + date/time"), see
        # StackMemoriesPanel.set_source_name_getter's own docstring.
        self.stack_memories_panel.set_source_name_getter(
            lambda: Path(self.sample_image.current_path).stem
            if self.sample_image.current_path else None)
        self.stack_memories_panel.load_as_reference_requested.connect(
            lambda still: self.sample_image.load_reference_from_path(still.source_path))
        # A StackMemory's captured full_still has no source file to
        # reload from at all (it's a decoded-from-PNG-bytes graded
        # snapshot, see stack_memories_panel.py's own docstring) -- a
        # separate signal/array-based load, not a path-based one.
        self.stack_memories_panel.load_full_still_as_reference_requested.connect(
            self.sample_image.load_reference_from_array)

        # corner_widget=list_menu_button -- Add Printer Lights / Add ACES
        # DRT / Edit Size-Domain consolidated into one header menu (per
        # Tom), replacing the old always-visible button row -- see
        # StackPanel._build_list_menu_button.
        self._make_dock(
            "stack_frame", "Stack", self.stack_panel.list_widget_area,
            corner_widget=self.stack_panel.list_menu_button)

        # ---- right column: Stack Params / Scopes / Graph ---------------
        # Stack Params at top (it's the one you reach for most, editing
        # whatever's selected in the Stack); Scopes in the middle; Graph
        # at the bottom (per Tom -- default starting position only now,
        # see _build_default_dock_layout; freely rearrangeable afterward).
        self._make_dock("stack_params_frame", "Parameters", self.stack_panel.params_widget_area)

        self.scopes_panel = ScopesPanel()
        # corner_widget=scopes_panel.menu_button -- the old "Scope:" combo
        # is now the same "..." header-menu pattern as Viewer/Stack (per
        # Tom), see scopes_panel.py's _build_menu_button.
        self._make_dock(
            "scopes_frame", "Scopes", self.scopes_panel,
            corner_widget=self.scopes_panel.menu_button)
        # Scopes read whatever SampleImagePanel is currently previewing
        # (post-stack) -- same source of truth as the wipe compare, not a
        # separate re-derivation.
        self.sample_image.after_image_changed.connect(self.scopes_panel.set_image_array)
        self.scopes_panel.set_image_array(self.sample_image.get_current_after_array())
        # NOTE: WARPier's own embedded cloud widget (stack_panel.py's
        # cw_cloud) deliberately does NOT get this feed -- it shows an
        # idealized OKLCh gamut reference blob
        # (color_warper.warped_gamut_blob_points), not a measurement of
        # the loaded image, per Tom.

        # Graph -- its own panel again (see scopes_panel.py's docstring).
        # Renamed from "3D Viewer" per Tom, once LutViewer3D grew a 1D
        # YRGB-curve mode alongside the original 3D point cloud (see
        # lut_viewer_3d.py's docstring) -- "3D Viewer" stopped describing
        # what the panel actually shows. Internal names (viewer_3d,
        # viewer_3d_frame, viewer_3d_toggle, LutViewer3D) are unchanged --
        # only the user-facing label moved, renaming every internal
        # reference wasn't worth the churn.
        #
        # Has a "..." corner menu (per Tom's earlier follow-up -- the view
        # switch moved from an on-panel button pair into a menu, same
        # convention Viewer/Stack/Scopes already use, see
        # LutViewer3D.populate_menu) -- Grid density/Cage opacity stay
        # inline in LutViewer3D's own layout, only view selection moved.
        self.viewer_3d = LutViewer3D()
        self._make_dock(
            "viewer_3d_frame", "Graph", self.viewer_3d,
            corner_widget=self.viewer_3d.menu_button)
        # Reads the baked stack LUT directly (see _on_stack_changed below)
        # -- independent of the sample image entirely, unlike Scopes.

        # Now that every dock exists, lay them out in the default spatial
        # arrangement (see _build_default_dock_layout's own docstring) --
        # purely a starting position, not a structural constraint.
        self._build_default_dock_layout()
        self._snapshot_all_docked_state()

        # Export lives in its own dialog now (File > Export..., ⌘E, per
        # Tom) rather than an always-visible bottom-bar row -- see
        # _show_export_dialog. self.format_combo is built there instead of
        # here now.

        # Apply default visibility -- must happen after every dock above
        # exists.
        for _btn_attr, _label, frame_attr, default_visible in PANEL_TOGGLES:
            getattr(self, frame_attr).setVisible(default_visible)
