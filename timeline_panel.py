"""
timeline_panel.py -- the "Timeline" panel: an ordered list of clips built
up from the Media Library for continuity grading review (see timeline.py's
own docstring for the full feature background).

The view is timeline_track_widget.TimelineTrackWidget -- clips drawn as
time-proportional blocks along an x-axis, NLE-style, per Tom: "start
laying the groundwork for a more robust timeline viewer. Something more
akin to a normal NLE." This panel is a thin controller around that
widget: it owns the actual timeline.Timeline, funnels every mutation
through _refresh_list (which pushes the new state into the widget), and
translates the widget's low-level signals (clip_selected/clips_reordered/
seek_requested/context_menu_requested_for_clip) into this panel's own
external contract -- unchanged from the old QListWidget-based version, so
main_window.py's wiring didn't need to change at all: clip_selected,
timeline_changed, add_clip_from_library_entry, add_clip_with_range,
get_selected_clip, get_selected_clip_index, get_timeline, load_timeline,
populate_menu. The one addition is seek_requested (new: there was no
click-to-seek gesture on the old plain list), plus set_record_frame_getter
/refresh_playhead for driving the widget's playhead line from the
Timeline's own playback position -- both wired up in main_window.py
alongside TimelineController.

Clips are added from the OUTSIDE (via add_clip_from_library_entry), the
target of add_to_timeline_requested on MediaPanel -- this panel has no
file dialog or ingestion path of its own, same "Timeline only ever
sources from the Media Library" restriction the approved plan calls for.

A clip added from a Media Library entry (add_clip_from_library_entry)
gets an in/out range covering the full source (video) or a fixed hold
duration at this Timeline's own frame_rate (still) -- these are its
working defaults until trimmed. A clip added from a marked Source
in/out selection (add_clip_with_range, per Tom: "will be necessary to
be able to mark desired ranges and add to sequence") covers exactly
that range instead. A clip ALREADY on the Timeline can also be re-
trimmed in place now (per Tom, 2026-10-03: "make it more similar to a
typical NLE") by dragging its own left/right edge in the track view --
see TimelineTrackWidget's own module docstring on that mechanism
(clip_trimmed, relayed here to _on_clip_trimmed) -- a RIPPLE trim, since
clips have no gap concept in this data model (timeline.py's own
docstring): shortening one clip shifts every later one automatically.

Also holds a toolbar row (a Split button -- see split_at_playhead,
splitting whichever clip the Timeline's current RECORD position sits
inside, timeline.Timeline.split_clip_at_record_frame's own razor/blade-
tool cut -- plus a QScrollBar and Zoom In/Out/Fit buttons wired straight
to the track widget's own zoom_in/zoom_out/zoom_to_fit/set_scroll_
offset_frames -- see _build_toolbar_row/_refresh_scrollbar) and a
minimizable EDL (Edit Decision List) sub-panel below the track view, per
Tom: "add an EDL expression of the timeline in as a sub panel which can
be minimized in the timeline view." A plain read-
only text expression (see edl.py's own docstring on scope -- not a
round-trippable import/export format), regenerated from self.timeline
on every mutation (_refresh_list/_on_clips_reordered/_on_clip_trimmed)
-- see _refresh_edl_text. Starts collapsed; the header button toggles
it, same flat QToolButton convention as the panel's own "..." menu
button.

Delete/Backspace (while the track view has keyboard focus, see
TimelineTrackWidget.keyPressEvent) removes the selected clip, routed
through the exact same confirm-and-remove path as the context menu's
own Remove action (_on_remove_clicked) -- no separate "are you sure"
dialog variant to keep in sync.

NOT VISUALLY VERIFIED IN THIS ENVIRONMENT -- same PySide6/libEGL wall as
the rest of the GUI layer (see timeline_track_widget.py's own docstring).
Please run main_window.py and confirm: (1) "Add to Timeline" on a Media
Library row appends a clip block sized to its own duration, (2) dragging
a clip block to a new position reorders playback order, (3) right-click
Rename/Remove work and Remove asks before deleting, (4) clicking a clip
selects it (watch clip_selected fire), (5) clicking the ruler seeks the
Timeline and moves the playhead line, (6) the playhead line tracks
playback in real time, (7) the EDL sub-panel expands/collapses and its
text reflects the current clip order/trim, (8) dragging a clip's own
edge trims it and ripples later clips, (9) the zoom buttons/scrollbar/
Ctrl+scroll all actually zoom and scroll the track view, (10) Delete
with a clip selected removes it.
"""
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QMessageBox, QInputDialog, QToolButton,
    QMenu, QPlainTextEdit, QScrollBar, QLabel, QStackedWidget, QScrollArea,
    QFrame, QPushButton, QButtonGroup, QSizePolicy,
)

import edl as edl_module
import timeline as tl
from timeline_track_widget import TimelineTrackWidget
from timeline_thumbnail_strip import STRIP_HEIGHT, TimelineThumbnailStrip

TIMELINE_VIEW = "timeline"
THUMBNAILS_VIEW = "thumbnails"

# The EDL sub-panel's own collapsed/expanded max height -- a sub panel,
# not the main view, so it never grows to dominate the Timeline dock even
# with a long clip list (the text view scrolls internally instead).
_EDL_TEXT_MAX_HEIGHT = 160

# A still-kind clip has no native duration of its own -- held for this
# many seconds at the Timeline's own frame_rate (see timeline.py's
# DEFAULT_FRAME_RATE docstring on that rate's dual role). No trim UI yet
# (module docstring), so this is just a reasonable working default.
DEFAULT_STILL_HOLD_SECONDS = 4.0


class TimelinePanel(QWidget):
    # Fires on every selection change, including to None (nothing
    # selected) -- a timeline.TimelineClip, or None. StackPanel's
    # Sequence/Shot toggle is wired through main_window.py to this, to
    # know which clip's own Stack "Shot" mode should point at.
    clip_selected = Signal(object)
    # Fires on every add/remove/clear/reorder/load (see _refresh_list --
    # the one place every mutation path already funnels through).
    # Wired through main_window.py to SampleImagePanel.
    # refresh_video_transport_visibility, so the transport bar shows up
    # as soon as the Timeline has clips to play, not only once a single
    # video also happens to be loaded as Source.
    timeline_changed = Signal()
    # A record-space frame index, from clicking the ruler/empty track --
    # relayed straight from TimelineTrackWidget's own seek_requested.
    # Wired through main_window.py to TimelineController.seek_to_record_frame.
    seek_requested = Signal(int)
    # (entry_id, insert_index) -- relayed straight from TimelineTrackWidget's
    # own media_entry_drop_requested (a Media Library row dragged onto the
    # Timeline). Wired through main_window.py, which resolves entry_id
    # against MediaPanel's own library and calls insert_clip_from_
    # library_entry_at -- this panel has no MediaLibrary reference of its
    # own, same "the other panel's data stays the other panel's" split
    # every other cross-panel signal in this app already follows.
    media_entry_drop_requested = Signal(int, int)
    # The loaded timeline changed: a timeline.Timeline, or None after
    # unload_timeline -- MediaPanel marks which library item is open.
    loaded_timeline_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        # The timeline open for editing -- one of the Media Libraries'
        # timelines (media_library.py, LIBRARIES AND TIMELINES), edited in
        # place -- or None when nothing is loaded.
        self.timeline = None
        # Called to create-and-load a timeline when a clip is added with
        # none loaded (MainWindow makes it in the current library) -- see
        # set_timeline_creator.
        self._timeline_creator = None
        # The last clip clip_selected was actually fired for -- see
        # _sync_selected_clip's own docstring in _refresh_list on why this
        # cache (not a before/after _selected_clip() comparison) is what
        # makes "did the selection really change" correct across a
        # _refresh_list call.
        self._last_selected_clip = None
        self._record_frame_getter = None
        self._build_layout()
        self._refresh_list()
        # The grading screen's navigator by default; the NLE-style track
        # is one click away (IDENTITY PASS, 2026-10-09).
        self.set_view_mode(THUMBNAILS_VIEW)

    def _build_layout(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)  # PanelFrame provides the outer gutter


        self.track_widget = TimelineTrackWidget(self.timeline)
        self.track_widget.clip_selected.connect(self._on_widget_clip_selected)
        self.track_widget.clips_reordered.connect(self._on_clips_reordered)
        self.track_widget.clip_trimmed.connect(self._on_clip_trimmed)
        self.track_widget.delete_requested.connect(self._on_remove_clicked)
        self.track_widget.split_requested.connect(self.split_at_playhead)
        self.track_widget.seek_requested.connect(self.seek_requested.emit)
        self.track_widget.media_entry_drop_requested.connect(self.media_entry_drop_requested.emit)
        self.track_widget.context_menu_requested_for_clip.connect(
            self._on_context_menu_requested_for_clip)
        self.track_widget.view_changed.connect(self._refresh_scrollbar)

        # Thumbnails view (timeline_thumbnail_strip.py): the same Timeline
        # as clip cards, in a horizontal scroll area. The two views are
        # QStackedWidget pages (CLAUDE.md: switch views with pages, not
        # setVisible) and share one selection -- see set_view_mode.
        self.thumbnail_strip = TimelineThumbnailStrip(self.timeline)
        self.thumbnail_strip.clip_selected.connect(self._on_strip_clip_selected)
        self.thumbnail_strip.seek_requested.connect(self.seek_requested.emit)
        self.thumbnail_scroll = QScrollArea()
        self.thumbnail_scroll.setObjectName("thumbnailStripScroll")
        self.thumbnail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.thumbnail_scroll.setWidget(self.thumbnail_strip)
        self.thumbnail_scroll.setWidgetResizable(False)
        self.thumbnail_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Room for the whole card (thumbnail + caption) plus the
        # horizontal scrollbar (style.py: 10px).
        self.thumbnail_scroll.setMinimumHeight(STRIP_HEIGHT + 12)

        self.view_stack = QStackedWidget()
        self.view_stack.addWidget(self.track_widget)       # TIMELINE_VIEW
        self.view_stack.addWidget(self.thumbnail_scroll)   # THUMBNAILS_VIEW
        layout.addWidget(self.view_stack, stretch=1)

        self._build_toolbar_row(layout)
        self._build_edl_subpanel(layout)
        layout.addStretch(1)  # spare height goes below the controls, not around the track
        self._build_menu_button()

    def _build_toolbar_row(self, layout):
        """ONE row under the track (Tom, 2026-10-05: "everything feels
        rather crowded"): loaded timeline's name + unload, split, the
        scroll bar, zoom, and the EDL toggle -- these used to be three
        separate rows eating the track's height."""
        row = QHBoxLayout()
        row.setContentsMargins(4, 0, 4, 0)

        # Timeline | Thumbnails (style.py's segmented control).
        segmented = QFrame()
        segmented.setObjectName("segmentedControl")
        segmented.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        segmented_layout = QHBoxLayout(segmented)
        segmented_layout.setContentsMargins(2, 2, 2, 2)
        segmented_layout.setSpacing(2)
        self._view_mode_buttons = {}
        self._view_mode_group = QButtonGroup(self)
        self._view_mode_group.setExclusive(True)
        for mode, label in ((TIMELINE_VIEW, "Timeline"), (THUMBNAILS_VIEW, "Thumbnails")):
            button = QPushButton(label)
            button.setObjectName("segmentButton")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, m=mode: self.set_view_mode(m))
            self._view_mode_group.addButton(button)
            segmented_layout.addWidget(button)
            self._view_mode_buttons[mode] = button
        row.addWidget(segmented)
        row.addSpacing(8)

        self.timeline_name_label = QLabel()
        self.timeline_name_label.setMaximumWidth(220)
        row.addWidget(self.timeline_name_label)
        self.unload_button = QToolButton()
        self.unload_button.setObjectName("panelMenuButton")
        self.unload_button.setAutoRaise(True)
        self.unload_button.setText("⏏")
        self.unload_button.setToolTip("Unload this timeline (it stays in its Media Library)")
        self.unload_button.clicked.connect(self.unload_timeline)
        row.addWidget(self.unload_button)
        # Takes the hidden scrollbar's stretch in Thumbnails mode, so the
        # row's items stay left/right instead of spreading out.
        self._thumbnails_row_spacer = QWidget()
        self._thumbnails_row_spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row.addWidget(self._thumbnails_row_spacer, stretch=1)
        row.addSpacing(8)

        self.split_button = QToolButton()
        self.split_button.setObjectName("panelMenuButton")
        self.split_button.setAutoRaise(True)
        self.split_button.setText("✂")
        self.split_button.setToolTip("Split the clip under the playhead into two (S)")
        self.split_button.clicked.connect(self.split_at_playhead)
        row.addWidget(self.split_button)

        self.timeline_scrollbar = QScrollBar(Qt.Orientation.Horizontal)
        self.timeline_scrollbar.valueChanged.connect(self._on_scrollbar_changed)
        row.addWidget(self.timeline_scrollbar, stretch=1)

        self.zoom_out_button = QToolButton()
        self.zoom_out_button.setObjectName("panelMenuButton")
        self.zoom_out_button.setAutoRaise(True)
        self.zoom_out_button.setText("−")
        self.zoom_out_button.setToolTip("Zoom out (Ctrl+scroll also works)")
        self.zoom_out_button.clicked.connect(self.track_widget.zoom_out)
        row.addWidget(self.zoom_out_button)

        self.zoom_fit_button = QToolButton()
        self.zoom_fit_button.setObjectName("panelMenuButton")
        self.zoom_fit_button.setAutoRaise(True)
        self.zoom_fit_button.setText("Fit")
        self.zoom_fit_button.setToolTip("Zoom to fit the whole Timeline")
        self.zoom_fit_button.clicked.connect(self.track_widget.zoom_to_fit)
        row.addWidget(self.zoom_fit_button)

        self.zoom_in_button = QToolButton()
        self.zoom_in_button.setObjectName("panelMenuButton")
        self.zoom_in_button.setAutoRaise(True)
        self.zoom_in_button.setText("+")
        self.zoom_in_button.setToolTip("Zoom in (Ctrl+scroll also works)")
        self.zoom_in_button.clicked.connect(self.track_widget.zoom_in)
        row.addWidget(self.zoom_in_button)
        self._toolbar_row = row  # the EDL toggle joins it (see _build_edl_subpanel)

        layout.addLayout(row)
        self._refresh_scrollbar()
        # Controls that only mean something in the time-proportional view.
        self._timeline_only_controls = (
            self.split_button, self.timeline_scrollbar, self.zoom_out_button,
            self.zoom_fit_button, self.zoom_in_button)

    def _refresh_scrollbar(self):
        """Called on every TimelineTrackWidget.view_changed (zoom/scroll/
        resize/auto-fit recompute, see that signal's own docstring) --
        keeps the scrollbar's range/page-step/value/enabled state in
        step with the widget's own current view."""
        max_scroll = self.track_widget.max_scroll_frames()
        visible = self.track_widget.visible_frame_count()
        self.timeline_scrollbar.blockSignals(True)
        self.timeline_scrollbar.setRange(0, round(max_scroll))
        self.timeline_scrollbar.setPageStep(max(1, round(visible)))
        self.timeline_scrollbar.setValue(round(self.track_widget.scroll_offset_frames()))
        self.timeline_scrollbar.setEnabled(max_scroll > 0)
        self.timeline_scrollbar.blockSignals(False)

    def _on_scrollbar_changed(self, value):
        self.track_widget.set_scroll_offset_frames(value)

    def _on_clip_trimmed(self):
        # Timeline.TimelineClip's own in_frame/out_frame have already
        # been mutated in place by the widget itself (see
        # TimelineTrackWidget._apply_trim_drag) -- just keep the EDL
        # sub-panel's text and the outside world in step, same as
        # _on_clips_reordered.
        self._refresh_edl_text()
        self.timeline_changed.emit()

    def _build_edl_subpanel(self, layout):
        self.edl_toggle_button = QToolButton()
        self.edl_toggle_button.setObjectName("panelMenuButton")
        self.edl_toggle_button.setAutoRaise(True)
        self.edl_toggle_button.setCheckable(True)
        self.edl_toggle_button.setChecked(False)  # starts minimized
        self.edl_toggle_button.setText("▸ EDL")
        self.edl_toggle_button.toggled.connect(self._on_edl_toggle_toggled)
        self._toolbar_row.addSpacing(8)
        self._toolbar_row.addWidget(self.edl_toggle_button)

        self.edl_text_view = QPlainTextEdit()
        self.edl_text_view.setObjectName("edlTextView")
        self.edl_text_view.setReadOnly(True)
        self.edl_text_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.edl_text_view.setMaximumHeight(_EDL_TEXT_MAX_HEIGHT)
        self.edl_text_view.setVisible(False)  # starts minimized, matches edl_toggle_button's own state
        layout.addWidget(self.edl_text_view)

    def _on_edl_toggle_toggled(self, checked):
        self.edl_text_view.setVisible(checked)
        self.edl_toggle_button.setText("▾ EDL" if checked else "▸ EDL")

    def _build_menu_button(self):
        self.menu_button = QToolButton()
        self.menu_button.setObjectName("panelMenuButton")
        self.menu_button.setAutoRaise(True)
        self.menu_button.setText("☰")
        self.menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.menu_button)
        self.populate_menu(menu)
        self.menu_button.setMenu(menu)

    def populate_menu(self, menu):
        """Shared by the "..." corner button above and MainWindow's
        top-level "Timeline" menu-bar menu, same redundant-on-purpose
        arrangement every other panel's populate_menu already follows.
        Rename/Remove need a selected clip, so they live on the
        right-click context menu instead (see
        _on_context_menu_requested_for_clip) -- "Clear Timeline" and
        "Split at Playhead" (same standing action as the toolbar's own
        Split button, see split_at_playhead) are the two that belong
        here, needing only the Timeline's own current state, not a
        selection."""
        menu.addAction("Split at Playhead", self.split_at_playhead)
        menu.addSeparator()
        menu.addAction("Clear Timeline", self._on_clear_clicked)
        menu.addAction("Unload Timeline", self.unload_timeline)

    def _on_context_menu_requested_for_clip(self, clip, global_pos):
        # The widget has already selected `clip` (and fired clip_selected,
        # see _on_widget_clip_selected) before emitting this.
        menu = QMenu(self.track_widget)
        menu.addAction("Rename...", self._on_rename_clicked)
        if clip.media_kind == "still":
            # Only a still-kind clip has a user-adjustable hold duration --
            # a video clip's own out_frame is its native duration (module
            # docstring, DEFAULT_STILL_HOLD_SECONDS). Per Tom: stills need
            # this to be user-controllable, not just the fixed default
            # applied at add-time.
            menu.addAction("Set Duration...", self._on_set_duration_clicked)
        menu.addAction("Remove", self._on_remove_clicked)
        menu.exec(global_pos)

    # ---- selection / playhead -----------------------------------------------

    def _selected_clip(self):
        return self.track_widget.selected_clip()

    def get_selected_clip(self):
        """Pull-based equivalent of clip_selected -- wired to StackPanel's
        set_selected_clip_getter (see main_window.py) so it can find out
        which clip's Stack to load the moment the user clicks "Shot,"
        without this panel needing to know StackPanel exists."""
        return self._selected_clip()

    def get_selected_clip_index(self):
        """The selected clip's own position in self.timeline.clips, or
        None if nothing's selected -- used by main_window.py to point
        TimelineController.seek_to_clip at the right clip (so pressing
        Play resumes from wherever the Timeline's own selection
        currently is, not some earlier play/pause session's position --
        Tom: "the transport is still a little tricky to interact
        with")."""
        clip = self._selected_clip()
        if clip is None or self.timeline is None:
            return None
        for index, candidate in enumerate(self.timeline.clips):
            if candidate.id == clip.id:
                return index
        return None

    def _on_widget_clip_selected(self, clip):
        self._last_selected_clip = clip
        self.thumbnail_strip.set_selected_clip_id(clip.id if clip is not None else None)
        self.clip_selected.emit(clip)

    def _on_strip_clip_selected(self, clip):
        """A card clicked in the Thumbnails view: select the same clip in
        the track view (without it re-emitting) and report it once."""
        self.track_widget.set_selected_clip_id(clip.id)
        self._on_widget_clip_selected(clip)

    def view_mode(self):
        return TIMELINE_VIEW if self.view_stack.currentIndex() == 0 else THUMBNAILS_VIEW

    def set_view_mode(self, mode):
        """TIMELINE_VIEW (time-proportional track, trims, zoom) or
        THUMBNAILS_VIEW (clip cards -- the grading screen's navigator)."""
        self.view_stack.setCurrentIndex(0 if mode == TIMELINE_VIEW else 1)
        self._view_mode_buttons[mode].setChecked(True)
        for control in self._timeline_only_controls:
            control.setVisible(mode == TIMELINE_VIEW)
        self._thumbnails_row_spacer.setVisible(mode == THUMBNAILS_VIEW)

    def set_record_frame_getter(self, getter):
        """Pull-based playhead position source (see refresh_playhead) --
        same push-signal/pull-getter split as this app's other cross-panel
        wiring (e.g. StackPanel's set_selected_clip_getter)."""
        self._record_frame_getter = getter

    def refresh_playhead(self):
        """Call whenever TimelineController.frame_changed fires (wired in
        main_window.py) to move the widget's playhead line to the
        Timeline's current record-space position."""
        if self._record_frame_getter is None:
            return
        frame = self._record_frame_getter()
        self.track_widget.set_playhead_frame(frame)
        self.thumbnail_strip.set_playhead_frame(frame)
        current = self.thumbnail_strip.current_clip_index()
        if current is not None and self.view_mode() == THUMBNAILS_VIEW:
            rect = self.thumbnail_strip.ensure_visible_rect(current)
            self.thumbnail_scroll.ensureVisible(
                int(rect.center().x()), int(rect.center().y()), int(rect.width() / 2) + 8, 0)

    def split_at_playhead(self):
        """Splits whichever clip the Timeline's own current RECORD
        position sits inside into two adjacent clips -- per Tom: "make
        it more similar to a typical NLE" (a razor/blade-tool cut), see
        timeline.Timeline.split_clip_at_record_frame's own docstring on
        exactly where a split lands/when it's a no-op. Selects the FIRST
        of the two resulting clips afterward, same "land on something
        sensible" convention add_clip_with_range's own keep_selection
        already follows."""
        if self._record_frame_getter is None or self.timeline is None:
            return
        record_frame = self._record_frame_getter()
        if record_frame is None:
            return
        result = self.timeline.split_clip_at_record_frame(record_frame)
        if result is None:
            return
        first, _second = result
        index = next(i for i, c in enumerate(self.timeline.clips) if c.id == first.id)
        self._refresh_list(keep_selection=index)

    def _on_clips_reordered(self):
        # Timeline.move_clip has already been applied by the widget itself
        # (see TimelineTrackWidget.mouseReleaseEvent) -- just propagate,
        # and keep the EDL sub-panel's own text in step with the new order
        # (this path doesn't go through _refresh_list, which handles that
        # for every other mutation).
        self._refresh_edl_text()
        self.timeline_changed.emit()

    # ---- add / rename / remove ---------------------------------------------

    def add_clip_from_library_entry(self, entry):
        """Target of MediaPanel's add_to_timeline_requested signal
        (wired in main_window.py) -- the FULL source range every time,
        always appended (see add_clip_with_range for a caller-chosen
        sub-range, e.g. a marked Source in/out selection, and
        insert_clip_from_library_entry_at for a caller-chosen
        POSITION, e.g. a Media Library row dragged onto the Timeline)."""
        out_frame = self._full_range_out_frame(entry)
        self.add_clip_with_range(
            entry.name, entry.source_path, entry.media_kind, 0, out_frame,
            thumbnail=entry.thumbnail, input_colourspace=entry.source_colourspace)

    def insert_clip_from_library_entry_at(self, entry, index):
        """Same full-source-range clip construction as add_clip_from_
        library_entry, but inserted at a specific position rather than
        always appended -- the target of TimelineTrackWidget.media_
        entry_drop_requested (via main_window.py, which resolves the
        signal's own entry_id back to a MediaEntry), per Tom: "make it
        more similar to a typical NLE" (dragging a clip from the Media
        Library straight onto the Timeline at a chosen position)."""
        out_frame = self._full_range_out_frame(entry)
        self.add_clip_with_range(
            entry.name, entry.source_path, entry.media_kind, 0, out_frame,
            thumbnail=entry.thumbnail, index=index, input_colourspace=entry.source_colourspace)

    def _full_range_out_frame(self, entry):
        if entry.media_kind == "video":
            return max(0, (entry.frame_count or 1) - 1)
        frame_rate = self.timeline.frame_rate if self.timeline is not None else tl.DEFAULT_FRAME_RATE
        return max(0, int(round(DEFAULT_STILL_HOLD_SECONDS * frame_rate)) - 1)

    def add_clip_with_range(self, name, source_path, media_kind, in_frame, out_frame, thumbnail=None,
                            index=None, input_colourspace=None):
        """Target of SampleImagePanel's add_source_range_to_timeline_
        requested signal (wired in main_window.py, per Tom: "will be
        necessary to be able to mark desired ranges and add to
        sequence") -- a clip covering exactly [in_frame, out_frame] of
        `source_path`, the marked-range counterpart to add_clip_from_
        library_entry's always-full-range add. `index` (default: append
        at the end, same as before this parameter existed) -- the only
        place that ever constructs a TimelineClip and adds it, shared by
        every caller above so they all stay in sync."""
        if self.timeline is None and self._timeline_creator is not None:
            self._timeline_creator()  # creates one and load_timeline()s it
        if self.timeline is None:
            return
        clip = tl.TimelineClip(name, source_path, media_kind, in_frame, out_frame, thumbnail=thumbnail,
                               input_colourspace=input_colourspace)
        self.timeline.add_clip(clip, index=index)
        inserted_index = index if index is not None else len(self.timeline.clips) - 1
        self._refresh_list(keep_selection=inserted_index)

    def still_hold_out_frame(self):
        """out_frame for a still added at the default hold duration."""
        rate = self.timeline.frame_rate if self.timeline is not None else tl.DEFAULT_FRAME_RATE
        return max(0, int(round(DEFAULT_STILL_HOLD_SECONDS * rate)) - 1)

    def apply_edit(self, mode, clip, record_frame):
        """Source viewer edits: "insert" / "overwrite" at `record_frame`, or
        "append" -- see timeline.Timeline's own methods. Selects the new clip."""
        if self.timeline is None:
            return
        if mode == "insert":
            index = self.timeline.insert_clip_at_record_frame(clip, record_frame)
        elif mode == "overwrite":
            index = self.timeline.overwrite_clip_at_record_frame(clip, record_frame)
        else:
            index = self.timeline.append_clip(clip)
        self._refresh_list(keep_selection=index)

    def _on_rename_clicked(self):
        clip = self._selected_clip()
        if clip is None:
            return
        name, ok = QInputDialog.getText(self, "Rename Clip", "Name:", text=clip.name)
        name = name.strip()
        if not ok or not name:
            return
        clip.name = name
        self._refresh_list(keep_selection=self.get_selected_clip_index())

    def _on_set_duration_clicked(self):
        """Still clips only (see _on_context_menu_requested_for_clip) --
        reframes the clip's own out_frame as a hold duration in seconds
        at this Timeline's own frame_rate (see DEFAULT_STILL_HOLD_SECONDS
        /timeline.py's DEFAULT_FRAME_RATE docstring on that rate's role
        for a still), keeping in_frame at 0 -- there's no trim UI yet
        (module docstring), just a single duration knob per Tom's own
        request."""
        clip = self._selected_clip()
        if clip is None or clip.media_kind != "still" or self.timeline is None:
            return
        current_seconds = clip.frame_count / self.timeline.frame_rate
        seconds, ok = QInputDialog.getDouble(
            self, "Set Duration", "Hold duration (seconds):",
            current_seconds, 0.1, 3600.0, 1)
        if not ok:
            return
        clip.out_frame = max(0, int(round(seconds * self.timeline.frame_rate)) - 1)
        self._refresh_list(keep_selection=self.get_selected_clip_index())

    def _on_remove_clicked(self):
        clip = self._selected_clip()
        if clip is None or self.timeline is None:
            return
        reply = QMessageBox.question(
            self, "Remove Clip",
            f'Remove "{clip.name}" from the timeline? This can\'t be undone.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.timeline.remove_clip(clip.id)
        self._refresh_list()

    def _on_clear_clicked(self):
        if self.timeline is None or not self.timeline.clips:
            return
        reply = QMessageBox.question(
            self, "Clear Timeline",
            "Remove every clip from the timeline? This can't be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.timeline.clips = []
        self._refresh_list()

    # ---- shared refresh funnel ------------------------------------------

    def _refresh_list(self, keep_selection=None):
        """The one place every mutation path (add/rename/remove/clear/
        load/reorder-via-drag is its own separate path, see
        _on_clips_reordered) funnels through -- pushes self.timeline into
        the widget, resolves `keep_selection` (a clip INDEX, matching the
        old QListWidget-based API) to that clip's id, clears the
        selection if the previously-selected clip no longer exists, and
        fires clip_selected/timeline_changed as needed."""
        self.track_widget.set_timeline(self.timeline)
        self.thumbnail_strip.set_timeline(self.timeline)
        self._refresh_loaded_state()
        clips = self.timeline.clips if self.timeline is not None else []
        if keep_selection is not None and 0 <= keep_selection < len(clips):
            self.track_widget.set_selected_clip_id(clips[keep_selection].id)
        elif self.track_widget.selected_clip() is None and self.track_widget.selected_clip_id is not None:
            self.track_widget.set_selected_clip_id(None)
        # set_selected_clip_id above deliberately doesn't re-emit
        # clip_selected (same "external sync shouldn't re-trigger the
        # signal that started it" convention every other panel's load_*
        # follows) -- _sync_selected_clip below is what actually fires it,
        # compared against _last_selected_clip rather than a before/after
        # snapshot, since every caller here already mutated self.timeline
        # .clips BEFORE calling this method.
        self._sync_selected_clip()
        self.thumbnail_strip.set_selected_clip_id(self.track_widget.selected_clip_id)
        self._refresh_edl_text()
        self.timeline_changed.emit()

    def _sync_selected_clip(self):
        current = self._selected_clip()
        if current is self._last_selected_clip:
            return
        self._last_selected_clip = current
        self.clip_selected.emit(current)

    def _refresh_edl_text(self):
        """Regenerates the EDL sub-panel's text from self.timeline (see
        edl.py) -- called on every mutation (_refresh_list/_on_clips_
        reordered), not just while the sub-panel happens to be expanded,
        so it's already current the moment it IS expanded."""
        if self.timeline is None:
            self.edl_text_view.clear()
            return
        self.edl_text_view.setPlainText(
            edl_module.generate_edl(self.timeline, title=self.timeline.name or "Timeline"))

    # ---- session save/load (see session_io.py) -----------------------------

    def _refresh_loaded_state(self):
        loaded = self.timeline is not None
        self.timeline_name_label.setText(
            self.timeline.name if loaded else "No timeline loaded")
        self.timeline_name_label.setToolTip(
            "" if loaded else "Double-click a timeline in Media Libraries, or add a "
            "clip to start a new one")
        self.timeline_name_label.setEnabled(loaded)
        for widget in (self.unload_button, self.split_button, self.zoom_out_button,
                       self.zoom_fit_button, self.zoom_in_button):
            widget.setEnabled(loaded)

    def set_timeline_creator(self, creator):
        """`creator()` makes a new timeline and load_timeline()s it --
        called when a clip is added with nothing loaded."""
        self._timeline_creator = creator

    def get_timeline(self):
        """The loaded timeline.Timeline, or None."""
        return self.timeline

    def load_timeline(self, timeline):
        """Open `timeline` (or None) for editing -- the same object that
        lives in its Media Library, edited in place."""
        changed = timeline is not self.timeline
        self.timeline = timeline
        self._refresh_list()
        if changed:
            self.loaded_timeline_changed.emit(timeline)

    def unload_timeline(self):
        """Close the loaded timeline; it stays in its Media Library."""
        self.load_timeline(None)

    def refresh_timeline_name(self):
        """After the loaded timeline is renamed elsewhere (Media Libraries)."""
        self._refresh_loaded_state()
        self._refresh_edl_text()
