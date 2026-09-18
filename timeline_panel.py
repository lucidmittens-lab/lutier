"""
timeline_panel.py -- the "Timeline" panel: an ordered list of clips built
up from the Media Library for continuity grading review (see timeline.py's
own docstring for the full feature background). This first pass covers
list/add/remove/reorder/selection only -- no playback yet (that's
timeline_playback_controller.py, a later build step) and no Sequence/Shot
toggle wiring yet (that's stack_panel.py's job, driven by clip_selected
below).

A plain-icon QListWidget row shape (a thumbnail via setIcon + a name/
duration label) rather than stack_panel.py's setItemWidget-based rows,
specifically so Qt's own built-in InternalMove drag-reorder can be used
as-is -- setItemWidget content is what makes InternalMove unreliable
there (see stack_panel.py's own _StackListWidget docstring), and Timeline
rows don't use it. (media_panel.py's own rows moved to a QTableWidget in
the Media Panel rewrite -- Timeline's list-row shape here is unrelated
and unaffected.)

Clips are added from the OUTSIDE (via add_clip_from_library_entry), the
target of a new add_to_timeline_requested signal on MediaPanel --
this panel has no file dialog or ingestion path of its own, same
"Timeline only ever sources from the Media Library" restriction the
approved plan calls for.

A freshly-added clip gets an in/out range covering the full source (video)
or a fixed hold duration at this Timeline's own frame_rate (still) -- v1
has no trim UI (per plan, out of scope for this pass), so these are the
clip's working defaults until trim editing exists as a later fast-follow.

NOT TESTED IN THIS ENVIRONMENT -- same PySide6/libEGL wall as the rest of
the GUI layer. Please run main_window.py and confirm: (1) "Add to
Timeline" on an Image Library row appends a clip here with the right
thumbnail/name/duration, (2) dragging a row to a new position actually
reorders playback order (not just the visual row), (3) right-click
Rename/Remove work and Remove asks before deleting, (4) selecting a row
fires clip_selected (watch it via a temporary print if needed) so Step 5
has something to wire into.
"""
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QListWidget, QListWidgetItem, QAbstractItemView,
    QMessageBox, QInputDialog, QToolButton, QMenu,
)

import media_cache
import timeline as tl

# A still-kind clip has no native duration of its own -- held for this
# many seconds at the Timeline's own frame_rate (see timeline.py's
# DEFAULT_FRAME_RATE docstring on that rate's dual role). No trim UI yet
# (module docstring), so this is just a reasonable working default.
DEFAULT_STILL_HOLD_SECONDS = 4.0

_CLIP_ID_ROLE = Qt.ItemDataRole.UserRole


def _format_duration(frame_count, frame_rate):
    if not frame_rate:
        return f"{frame_count}f"
    seconds = frame_count / frame_rate
    minutes, secs = divmod(max(0, round(seconds)), 60)
    return f"{minutes}:{secs:02d}"


class TimelinePanel(QWidget):
    # Fires on every selection change, including to None (nothing
    # selected) -- a timeline.TimelineClip, or None. Step 5 (StackPanel's
    # Sequence/Shot toggle) wires this through main_window.py to know
    # which clip's own Stack "Shot" mode should point at.
    clip_selected = Signal(object)
    # Fires on every add/remove/clear/reorder/load (see _refresh_list --
    # the one place every mutation path already funnels through). Step 6
    # (Timeline playback) wires this through main_window.py to
    # SampleImagePanel.refresh_video_transport_visibility, so the
    # transport bar shows up as soon as the Timeline has clips to play,
    # not only once a single video also happens to be loaded as Source.
    timeline_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.timeline = tl.Timeline()
        # The last clip clip_selected was actually fired for -- see
        # _sync_selected_clip's own docstring in _refresh_list on why this
        # cache (not a before/after _selected_clip() comparison) is what
        # makes "did the selection really change" correct across a
        # _refresh_list call.
        self._last_selected_clip = None
        self._build_layout()
        self._refresh_list()

    def _build_layout(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)  # PanelFrame provides the outer gutter

        self.list_widget = QListWidget()
        # Qt's own built-in reorder -- safe here specifically because
        # these rows use plain setIcon, not setItemWidget (module
        # docstring). rowsMoved fires once Qt's internal move completes;
        # resync the Timeline's own clip order from the view rather than
        # trying to interpret rowsMoved's own from/to indices directly.
        self.list_widget.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list_widget.model().rowsMoved.connect(self._on_rows_moved)
        self.list_widget.itemSelectionChanged.connect(self._on_selection_changed)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._on_context_menu_requested)
        layout.addWidget(self.list_widget, stretch=1)

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
        Rename/Remove need a selected row, so they live on the right-click
        context menu instead (see _on_context_menu_requested) -- only the
        panel-level "Clear Timeline" belongs here."""
        menu.addAction("Clear Timeline", self._on_clear_clicked)

    def _on_context_menu_requested(self, pos):
        item = self.list_widget.itemAt(pos)
        if item is None:
            return
        self.list_widget.setCurrentItem(item)
        menu = QMenu(self.list_widget)
        menu.addAction("Rename...", self._on_rename_clicked)
        clip = self._selected_clip()
        if clip is not None and clip.media_kind == "still":
            # Only a still-kind clip has a user-adjustable hold duration --
            # a video clip's own out_frame is its native duration (module
            # docstring, DEFAULT_STILL_HOLD_SECONDS). Per Tom: stills need
            # this to be user-controllable, not just the fixed default
            # applied at add-time.
            menu.addAction("Set Duration...", self._on_set_duration_clicked)
        menu.addAction("Remove", self._on_remove_clicked)
        menu.exec(self.list_widget.mapToGlobal(pos))

    # ---- list -------------------------------------------------------------

    def _selected_clip(self):
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self.timeline.clips):
            return None
        return self.timeline.clips[row]

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
        row = self.list_widget.currentRow()
        return row if 0 <= row < len(self.timeline.clips) else None

    def _clip_summary_text(self, clip):
        duration = _format_duration(clip.frame_count, self.timeline.frame_rate)
        return f"{clip.name}\n  {duration}, {clip.media_kind}"

    def _refresh_list(self, keep_selection=None):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for clip in self.timeline.clips:
            item = QListWidgetItem(self._clip_summary_text(clip))
            item.setData(_CLIP_ID_ROLE, clip.id)
            png_bytes = media_cache.load_thumbnail(clip.thumbnail) if clip.thumbnail else None
            if png_bytes:
                pixmap = QPixmap()
                if pixmap.loadFromData(png_bytes, "PNG"):
                    item.setIcon(pixmap)
            self.list_widget.addItem(item)
        if keep_selection is not None and 0 <= keep_selection < len(self.timeline.clips):
            self.list_widget.setCurrentRow(keep_selection)
        self.list_widget.blockSignals(False)
        # blockSignals suppressed itemSelectionChanged above (including for
        # keep_selection) -- _sync_selected_clip fires the hand-off
        # explicitly instead. NOTE: can't compare "before" vs "after" by
        # calling _selected_clip() on both sides of this method the way an
        # earlier version of this code did -- every caller already mutates
        # self.timeline.clips BEFORE calling _refresh_list (that's the
        # point), so a "before" snapshot computed AFTER that mutation
        # would be wrong (e.g. Remove: by the time this runs, the removed
        # clip is already gone from self.timeline.clips, so a row-index
        # lookup can't tell "was clip A" from "was already nothing").
        # self._last_selected_clip (updated only by _sync_selected_clip
        # itself) is what makes this comparison correct regardless of
        # when/how the model changed.
        self._sync_selected_clip()
        self.timeline_changed.emit()

    def _on_selection_changed(self):
        self._sync_selected_clip()

    def _sync_selected_clip(self):
        current = self._selected_clip()
        if current is self._last_selected_clip:
            return
        self._last_selected_clip = current
        self.clip_selected.emit(current)

    def _on_rows_moved(self, *_args):
        self._resync_clip_order_from_list_widget()

    def _resync_clip_order_from_list_widget(self):
        """Qt has already moved the visual row -- rebuild self.timeline
        .clips to match the view's own new order, keyed by each row's
        stored clip id (not position, which is exactly what just
        changed). Left untouched if the row/clip counts don't match --
        should never happen, but a no-op is safer than corrupting clip
        order on a state this panel doesn't fully understand."""
        clips_by_id = {clip.id: clip for clip in self.timeline.clips}
        new_order = []
        for row in range(self.list_widget.count()):
            clip = clips_by_id.get(self.list_widget.item(row).data(_CLIP_ID_ROLE))
            if clip is not None:
                new_order.append(clip)
        if len(new_order) == len(self.timeline.clips):
            self.timeline.clips = new_order

    # ---- add / rename / remove ---------------------------------------------

    def add_clip_from_library_entry(self, entry):
        """Target of MediaPanel's add_to_timeline_requested signal
        (wired in main_window.py) -- the only way a clip enters the
        Timeline (module docstring)."""
        if entry.media_kind == "video":
            out_frame = max(0, (entry.frame_count or 1) - 1)
        else:
            out_frame = max(0, int(round(DEFAULT_STILL_HOLD_SECONDS * self.timeline.frame_rate)) - 1)
        clip = tl.TimelineClip(
            entry.name, entry.source_path, entry.media_kind, 0, out_frame,
            thumbnail=entry.thumbnail)
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
        self._refresh_list(keep_selection=self.list_widget.currentRow())

    def _on_set_duration_clicked(self):
        """Still clips only (see _on_context_menu_requested) -- reframes
        the clip's own out_frame as a hold duration in seconds at this
        Timeline's own frame_rate (see DEFAULT_STILL_HOLD_SECONDS/timeline
        .py's DEFAULT_FRAME_RATE docstring on that rate's role for a
        still), keeping in_frame at 0 -- there's no trim UI yet (module
        docstring), just a single duration knob per Tom's own request."""
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
        self._refresh_list(keep_selection=self.list_widget.currentRow())

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

    # ---- session save/load (see session_io.py) -----------------------------

    def get_timeline(self):
        return self.timeline

    def load_timeline(self, timeline):
        self.timeline = timeline
        self._refresh_list()
