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
that range instead. Still no in-place trim UI for a clip ALREADY on the
Timeline (per plan, out of scope for this pass) -- marking a range only
ever happens before a clip is added, on the Source side.

NOT VISUALLY VERIFIED IN THIS ENVIRONMENT -- same PySide6/libEGL wall as
the rest of the GUI layer (see timeline_track_widget.py's own docstring).
Please run main_window.py and confirm: (1) "Add to Timeline" on a Media
Library row appends a clip block sized to its own duration, (2) dragging
a clip block to a new position reorders playback order, (3) right-click
Rename/Remove work and Remove asks before deleting, (4) clicking a clip
selects it (watch clip_selected fire), (5) clicking the ruler seeks the
Timeline and moves the playhead line, (6) the playhead line tracks
playback in real time.
"""
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QMessageBox, QInputDialog, QToolButton, QMenu,
)

import timeline as tl
from timeline_track_widget import TimelineTrackWidget

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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.timeline = tl.Timeline()
        # The last clip clip_selected was actually fired for -- see
        # _sync_selected_clip's own docstring in _refresh_list on why this
        # cache (not a before/after _selected_clip() comparison) is what
        # makes "did the selection really change" correct across a
        # _refresh_list call.
        self._last_selected_clip = None
        self._record_frame_getter = None
        self._build_layout()
        self._refresh_list()

    def _build_layout(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)  # PanelFrame provides the outer gutter

        self.track_widget = TimelineTrackWidget(self.timeline)
        self.track_widget.clip_selected.connect(self._on_widget_clip_selected)
        self.track_widget.clips_reordered.connect(self._on_clips_reordered)
        self.track_widget.seek_requested.connect(self.seek_requested.emit)
        self.track_widget.context_menu_requested_for_clip.connect(
            self._on_context_menu_requested_for_clip)
        layout.addWidget(self.track_widget, stretch=1)

        self._build_menu_button()

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
        _on_context_menu_requested_for_clip) -- only the panel-level
        "Clear Timeline" belongs here."""
        menu.addAction("Clear Timeline", self._on_clear_clicked)

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
        if clip is None:
            return None
        for index, candidate in enumerate(self.timeline.clips):
            if candidate.id == clip.id:
                return index
        return None

    def _on_widget_clip_selected(self, clip):
        self._last_selected_clip = clip
        self.clip_selected.emit(clip)

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
        self.track_widget.set_playhead_frame(self._record_frame_getter())

    def _on_clips_reordered(self):
        # Timeline.move_clip has already been applied by the widget itself
        # (see TimelineTrackWidget.mouseReleaseEvent) -- just propagate.
        self.timeline_changed.emit()

    # ---- add / rename / remove ---------------------------------------------

    def add_clip_from_library_entry(self, entry):
        """Target of MediaPanel's add_to_timeline_requested signal
        (wired in main_window.py) -- the FULL source range every time
        (see add_clip_with_range for a caller-chosen sub-range, e.g. a
        marked Source in/out selection)."""
        if entry.media_kind == "video":
            out_frame = max(0, (entry.frame_count or 1) - 1)
        else:
            out_frame = max(0, int(round(DEFAULT_STILL_HOLD_SECONDS * self.timeline.frame_rate)) - 1)
        self.add_clip_with_range(
            entry.name, entry.source_path, entry.media_kind, 0, out_frame,
            thumbnail=entry.thumbnail)

    def add_clip_with_range(self, name, source_path, media_kind, in_frame, out_frame, thumbnail=None):
        """Target of SampleImagePanel's add_source_range_to_timeline_
        requested signal (wired in main_window.py, per Tom: "will be
        necessary to be able to mark desired ranges and add to
        sequence") -- appends a clip covering exactly [in_frame,
        out_frame] of `source_path`, the marked-range counterpart to
        add_clip_from_library_entry's always-full-range add. The only
        two places that ever construct a TimelineClip and append it,
        sharing this one tail so both stay in sync."""
        clip = tl.TimelineClip(name, source_path, media_kind, in_frame, out_frame, thumbnail=thumbnail)
        self.timeline.add_clip(clip)
        self._refresh_list(keep_selection=len(self.timeline.clips) - 1)

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
        if clip is None or clip.media_kind != "still":
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
        if clip is None:
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
        if not self.timeline.clips:
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
        if keep_selection is not None and 0 <= keep_selection < len(self.timeline.clips):
            self.track_widget.set_selected_clip_id(self.timeline.clips[keep_selection].id)
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
        self.timeline_changed.emit()

    def _sync_selected_clip(self):
        current = self._selected_clip()
        if current is self._last_selected_clip:
            return
        self._last_selected_clip = current
        self.clip_selected.emit(current)

    # ---- session save/load (see session_io.py) -----------------------------

    def get_timeline(self):
        return self.timeline

    def load_timeline(self, timeline):
        self.timeline = timeline
        self._refresh_list()
