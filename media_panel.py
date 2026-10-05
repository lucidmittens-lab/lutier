"""
media_panel.py -- the "Media Libraries" panel (left column by default,
between Browser and LUT Library, per Tom -- see main_window.py's
_build_default_dock_layout; freely draggable elsewhere since the docking
migration, see RUNNING.md #40), a sortable, columned table of media files
catalogued by reference: Import... reads a file's HEADER metadata only
(dimensions, video frame_count/frame_rate/duration) plus a one-time
thumbnail decode -- never the full raster data, and never keeps anything
decoded around after that (see media_library.py's docstring on the "row,
not an embedded image" data model this panel is built on). Mirrors
lut_library_panel.py's "named shelf" pattern, but "apply" here means
"load into the Viewer's Source or Reference slot" (two separate actions,
see sample_image_panel.py's docstring on those two roles) rather than
"add a Stack stage." Import... (panel-level, no selection needed) lives
in the "..." header menu; Load as Source/Load as Reference/Rename/Delete
(all per-item) live on a right-click context menu instead (per Tom, see
_on_context_menu_requested).

Renamed from image_library_panel.py -- same panel, same dock slot, same
on-screen title ("Media Libraries"), but the old QListWidget icon-row
list is replaced here with a QTableWidget so the entries' own columns
are directly visible and sortable, matching the "columned database"
framing this rewrite is built around. The full column set (COLUMNS:
timecode, codec, bit depth, audio, camera, file size, ... -- mostly
from media_probe.py's header metadata) can be shown/hidden and
reordered from the header, and that layout is remembered with the
workspace (get_header_state).

SORTING AND ROW IDENTITY -- with setSortingEnabled(True), the table's row
order no longer matches self.library.entries' order (a still-QListWidget
assumption the old panel relied on, safe there only because sorting was
never enabled). Every row's column-0 item carries the entry's own id in
Qt.ItemDataRole.UserRole; _entry_id_at_row/_selected_entry always resolve
through that id and MediaLibrary.find(), never a raw list index, so
selection/context-menu/double-click all stay correct no matter which
column the table is currently sorted by.

No drag-and-drop from the Browser (unlike LUT Library/CDL Library) --
BrowserPanel only ever lists LUT/CDL files, it has no notion of images,
so this panel's only ingestion path is its own Import... file dialog
("we'll make the file browser more robust later" -- per Tom, a folder-
scan ingestion path is a deliberate follow-up, not attempted here).

OUTGOING drag-and-drop onto the Timeline, though (per Tom, 2026-10-03:
"make it more similar to a typical NLE") -- this table IS a drag source,
via _MediaTable's own mimeData() override, carrying the dragged row's
entry id in a custom MIME type (media_library.MEDIA_ENTRY_DRAG_MIME_
TYPE). timeline_track_widget.py is the drop target; see that module's
own docstring for the insertion-index math and main_window.py's wiring
for how an (entry_id, index) pair turns into an actual TimelineClip.

VIDEO ENTRIES -- Import... accepts video files (same extension set as
VIDEO_EXTENSIONS below); video metadata (width/height/frame_count/
frame_rate/duration) comes straight off video_source.VideoSource's own
stream metadata with no frame decode at all, and exactly one frame is
decoded (then discarded, not kept on the entry) to build the thumbnail.
"Load as Source" on a video entry hands off to sample_image_panel.
load_sample_from_library_entry's own video branch (opens the full video
with transport, not a frozen frame) -- "Load as Reference" stays
disabled for a selected video entry, since Reference has never supported
video and adding that is out of scope here.

MULTI-SELECT + FILTER (per Tom, 2026-10-03: "make it more similar to a
typical NLE") -- ExtendedSelection (was SingleSelection): Add to Timeline
and Delete now act on EVERY selected row (_selected_entries, plural),
not just the current one -- Load as Source/Load as Reference/Rename stay
single-row actions (_selected_entry), since none of those make sense for
more than one entry at once. Right-clicking a row that's already part of
a multi-selection keeps the whole selection (doesn't collapse to just
that row) -- see _on_context_menu_requested. A plain QLineEdit above the
table filters rows by a case-insensitive substring match against name OR
path (_apply_filter, re-applied after every _refresh_table so it survives
Import/Rename/Delete) -- a typical media-browser fixture, not a saved/
named search.

NOT TESTED IN THIS ENVIRONMENT -- same PySide6/libEGL wall as the rest of
the GUI layer. Please run main_window.py and confirm: (1) Import... on a
real image/video file adds a row showing its resolution/duration/color
space, (2) clicking a column header sorts the table (numerically for
Resolution/Duration, not lexicographically -- "10:02" must sort after
"1:05", not before), (3) right-clicking a row shows "Load as Source"/
"Load as Reference"/"Rename..."/"Delete", each acting on THAT row even
after sorting has reordered it, (4) Rename/Delete work and ask before
Delete, (5) an entry survives Save Session -> Open Session on the same
machine, (6) Ctrl/Shift-clicking selects multiple rows and Delete/Add to
Timeline act on all of them, (7) the filter box actually hides non-
matching rows live as you type.
"""
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QMimeData, Signal, Qt
from PySide6.QtGui import QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem, QAbstractItemView,
    QHeaderView, QMessageBox, QInputDialog, QFileDialog, QToolButton, QMenu,
    QLineEdit,
)

from app_logic import load_image_as_array, get_image_native_size
from media_library import MediaLibrary, MediaEntry, MEDIA_ENTRY_DRAG_MIME_TYPE
import hover_inspector
import media_cache
import media_probe
import thumbnail
import video_source
import timecode

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".exr")
# .mxf: camera-original ProRes/DNxHR (e.g. ARRI ALEXA Mini LF ProRes 4444)
# -- PyAV/ffmpeg decodes it like any other container; it just wasn't listed.
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mxf", ".mkv", ".avi", ".m4v")


def _globs(extensions):
    return " ".join("*" + ext for ext in extensions)


# Built from the tuples above so the dialog filter and the is-it-a-video
# check can't drift apart again (they're both what .mxf was missing from).
IMAGE_FILE_FILTER = (
    f"Media files ({_globs(IMAGE_EXTENSIONS + VIDEO_EXTENSIONS)})"
    f";;Image files ({_globs(IMAGE_EXTENSIONS)})"
    f";;Video files ({_globs(VIDEO_EXTENSIONS)})"
    ";;All files (*)"
)

ENTRY_ID_ROLE = Qt.ItemDataRole.UserRole  # carried on every row's column-0 item, see module docstring
SORT_KEY_ROLE = Qt.ItemDataRole.UserRole + 1  # numeric sort key, see _NumericItem


def _format_duration(seconds):
    seconds = max(0, round(seconds))
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}:{secs:02d}"


def _format_size(num_bytes):
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def _start_frame(entry):
    """The clip's first frame as an absolute frame number from its
    embedded start timecode (0 when it has none) -- what Start/End TC
    display and sort by."""
    tc = entry.metadata.get("start_timecode")
    frames = timecode.timecode_to_frames(tc, entry.frame_rate or 0) if tc else None
    return frames or 0


def _timecode_cell(entry, offset_frames):
    """(text, sort_key) for Start TC (offset 0) or End TC (offset
    frame_count - 1). Video only; a clip with no embedded timecode
    starts at 00:00:00:00, the same as every NLE shows it."""
    if not entry.is_video or not entry.frame_rate:
        return "", None
    tc = entry.metadata.get("start_timecode")
    frame = _start_frame(entry) + offset_frames
    drop = timecode.is_drop_frame_timecode(tc) if tc else None
    return timecode.frames_to_timecode(frame, entry.frame_rate, drop_frame=drop), frame


def _bit_depth_cell(entry):
    bits = entry.metadata.get("bit_depth")
    if not bits:
        return "", None
    is_float = entry.metadata.get("is_float")
    # Float after integer at the same width (16-bit half after 16-bit int).
    return f"{bits}-bit float" if is_float else f"{bits}-bit", bits + (0.5 if is_float else 0)


def _chroma_cell(entry):
    chroma = entry.metadata.get("chroma", "")
    if chroma and entry.metadata.get("has_alpha"):
        chroma = {"RGB": "RGBA", "Mono": "Mono + Alpha"}.get(chroma, f"{chroma}:4")
    return chroma, None


def _audio_cell(entry):
    channels = entry.metadata.get("audio_channels")
    if not channels:
        return "", None
    rate = entry.metadata.get("audio_sample_rate")
    text = f"{channels} ch" + (f", {rate / 1000:g} kHz" if rate else "")
    return text, channels


def _fps_cell(entry):
    if not entry.is_video or not entry.frame_rate:
        return "", None
    return f"{round(entry.frame_rate, 3):g}", entry.frame_rate


def _resolution_cell(entry):
    if entry.width and entry.height:
        return f"{entry.width}x{entry.height}", entry.width * entry.height
    return "", None


def _duration_cell(entry):
    if entry.is_video and entry.duration:
        return _format_duration(entry.duration), entry.duration
    return "", None


def _frames_cell(entry):
    if entry.is_video and entry.frame_count:
        return str(entry.frame_count), entry.frame_count
    return "", None


def _par_cell(entry):
    par = entry.metadata.get("pixel_aspect_ratio")
    return (f"{par:.4g}", par) if par else ("", None)


def _size_cell(entry):
    size = entry.metadata.get("file_size")
    return (_format_size(size), size) if size is not None else ("", None)


def _modified_cell(entry):
    mtime = entry.metadata.get("file_modified")
    if mtime is None:
        return "", None
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"), mtime


def _text_cell(value):
    return value or "", None


def _probe_or_empty(path, is_video):
    """media_probe.probe for an import -- the file already opened fine
    for its size/thumbnail by the time this runs, so a failure here only
    costs the extra columns, never the import itself."""
    try:
        return media_probe.probe(path, is_video=is_video)
    except Exception:  # noqa: BLE001 -- see docstring
        return {}


class _Column:
    """One Media Libraries table column. `cell(entry)` returns (text,
    sort_key): a non-None sort_key makes the cell a _NumericItem (so
    "10:02" sorts after "1:05"); None sorts by the text itself.
    `visible` is the default before the user picks columns from the
    header's right-click menu (see _on_header_context_menu)."""

    def __init__(self, key, header, cell, visible, width, numeric=False):
        self.key, self.header, self.cell = key, header, cell
        self.visible, self.width, self.numeric = visible, width, numeric


# Logical order is the default left-to-right order; the user can drag
# columns around and show/hide them (see MediaPanel.get_header_state).
# Thumbnail stays logical column 0: it carries the entry id (ENTRY_ID_ROLE).
COLUMNS = (
    _Column("thumbnail", "", lambda e: ("", None), True, 56),
    _Column("name", "Name", lambda e: _text_cell(e.name), True, 160),
    _Column("kind", "Kind", lambda e: ("Video" if e.is_video else "Still", None), True, 56),
    _Column("resolution", "Resolution", _resolution_cell, True, 90, numeric=True),
    _Column("duration", "Duration", _duration_cell, True, 70, numeric=True),
    _Column("start_tc", "Start TC", lambda e: _timecode_cell(e, 0), True, 96, numeric=True),
    _Column("end_tc", "End TC",
            lambda e: _timecode_cell(e, max(0, (e.frame_count or 1) - 1)), False, 96, numeric=True),
    _Column("frames", "Frames", _frames_cell, False, 64, numeric=True),
    _Column("fps", "FPS", _fps_cell, True, 56, numeric=True),
    _Column("codec", "Codec", lambda e: _text_cell(e.metadata.get("codec")), True, 110),
    _Column("bit_depth", "Bit Depth", _bit_depth_cell, True, 80, numeric=True),
    _Column("chroma", "Chroma", _chroma_cell, False, 64),
    _Column("audio", "Audio", _audio_cell, False, 96, numeric=True),
    _Column("pixel_aspect", "Pixel Aspect", _par_cell, False, 80, numeric=True),
    _Column("camera", "Camera", lambda e: _text_cell(e.metadata.get("camera")), False, 140),
    _Column("format", "Format", lambda e: _text_cell(e.metadata.get("format")), False, 90),
    _Column("size", "Size", _size_cell, False, 76, numeric=True),
    _Column("modified", "Date Modified", _modified_cell, False, 130, numeric=True),
    _Column("colorspace", "Color Space",
            lambda e: ("" if e.is_video else (e.source_colourspace or "Unmanaged"), None), True, 100),
    _Column("filename", "File Name",
            lambda e: _text_cell(e.source_filename or (Path(e.source_path).name if e.source_path else "")),
            False, 160),
    _Column("path", "Path", lambda e: _text_cell(e.source_path), True, 200),
)
COLUMN_KEYS = tuple(c.key for c in COLUMNS)
COLUMN_HEADERS = tuple(c.header for c in COLUMNS)
COLUMN_THUMBNAIL = COLUMN_KEYS.index("thumbnail")
COLUMN_NAME = COLUMN_KEYS.index("name")
COLUMN_KIND = COLUMN_KEYS.index("kind")
COLUMN_RESOLUTION = COLUMN_KEYS.index("resolution")
COLUMN_DURATION = COLUMN_KEYS.index("duration")
COLUMN_COLORSPACE = COLUMN_KEYS.index("colorspace")
COLUMN_PATH = COLUMN_KEYS.index("path")
# Never hideable -- a row with no visible name is unidentifiable.
_ALWAYS_VISIBLE = {"name"}


class _NumericItem(QTableWidgetItem):
    """A QTableWidgetItem that sorts by a numeric key stashed in
    SORT_KEY_ROLE rather than its own display text -- plain text sorting
    would put "10:02" before "1:05" (lexicographic) or "1080" before
    "640x480" (Resolution has no single numeric-looking column at all).
    `sort_key=None` (e.g. Resolution/Duration on a video-less still, or a
    still with a failed header probe) sorts before every real value, same
    "unknown sorts first" convention Qt's own default text sort gives an
    empty string."""

    def __init__(self, text, sort_key):
        super().__init__(text)
        self.setData(SORT_KEY_ROLE, sort_key)

    def __lt__(self, other):
        mine = self.data(SORT_KEY_ROLE)
        theirs = other.data(SORT_KEY_ROLE) if isinstance(other, _NumericItem) else None
        if mine is None:
            return theirs is not None
        if theirs is None:
            return False
        return mine < theirs


class _MediaTable(QTableWidget):
    """QTableWidget with Qt's own row-drag machinery repurposed as a
    drag SOURCE carrying this library's entry id (MEDIA_ENTRY_DRAG_MIME
    _TYPE) -- the half of "drag a clip from the Media Library onto the
    Timeline" (per Tom: "make it more similar to a typical NLE") that
    lives here; see timeline_track_widget.py's own dragEnterEvent/
    dropEvent for the drop TARGET half. DragOnly (not DragDrop) -- this
    panel has no internal reorder/drop target of its own, only an
    outgoing drag."""

    def mimeData(self, items):
        rows = sorted({item.row() for item in items})
        if not rows:
            return None
        id_item = self.item(rows[0], COLUMN_THUMBNAIL)
        entry_id = id_item.data(ENTRY_ID_ROLE) if id_item is not None else None
        if entry_id is None:
            return None
        mime = QMimeData()
        mime.setData(MEDIA_ENTRY_DRAG_MIME_TYPE, str(entry_id).encode("utf-8"))
        return mime


class MediaPanel(QWidget):
    load_as_sample_requested = Signal(object)      # a media_library.MediaEntry
    load_as_reference_requested = Signal(object)   # a media_library.MediaEntry
    add_to_timeline_requested = Signal(object)     # a media_library.MediaEntry

    def __init__(self, parent=None):
        super().__init__(parent)
        self.library = MediaLibrary()
        self._build_layout()
        self._refresh_table()

    def _build_layout(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)  # PanelFrame provides the outer gutter

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter by name or path...")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self.filter_edit)

        self.table = _MediaTable(0, len(COLUMN_HEADERS))
        self.table.setHorizontalHeaderLabels(COLUMN_HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # ExtendedSelection (was SingleSelection, per Tom: "make it more
        # similar to a typical NLE") -- Ctrl/Shift-click selects multiple
        # rows; Add to Timeline/Delete act on all of them (_selected_
        # entries), Load as Source/Reference/Rename stay single-row
        # (_selected_entry, the CURRENT row) since none of those make
        # sense for more than one entry at once.
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        # Drag a row onto the Timeline (per Tom: "make it more similar
        # to a typical NLE") -- DragOnly, since this table has no
        # internal reorder/drop target of its own, only an outgoing
        # drag (see _MediaTable's own docstring).
        self.table.setDragEnabled(True)
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        # Interactive (the default, but set explicitly here) on every
        # column -- Stretch/ResizeToContents (the previous setup for
        # Name/Path/Thumbnail) both make a column's width fixed against
        # manual drag-resize, which is exactly what made columns "not
        # easy to adjust." setStretchLastSection instead gives any
        # LEFTOVER space (beyond the sum of the columns' own widths) to
        # the last column (Path) without taking away its own
        # interactive resize handle.
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        # Drag a header to reorder; right-click it to show/hide columns
        # (most of COLUMNS start hidden -- see _on_header_context_menu).
        header.setSectionsMovable(True)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._on_header_context_menu)
        for index, column in enumerate(COLUMNS):
            self.table.setColumnWidth(index, column.width)
            self.table.setColumnHidden(index, not column.visible)
        # Double-click defaults to "Load as Source" -- the more common of
        # the two roles (every existing Import/Export Still workflow
        # already assumes a Source image), same "double-click is the fast
        # path for the common action" convention LUT Library (Apply to
        # Stack) and CDL Library (Apply to Stack) already use.
        self.table.itemDoubleClicked.connect(self._on_load_as_sample_clicked)
        # Per-item actions (Load as Source/Load as Reference/Rename/
        # Delete) live on a right-click context menu, not the "..."
        # header menu (per Tom: "rename, load as source, delete and load
        # as ref should move to right clicking a particular clip") --
        # see _on_context_menu_requested. Only Import... (panel-level,
        # not tied to a selection) stays in populate_menu below.
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu_requested)
        layout.addWidget(self.table, stretch=1)
        hover_inspector.register(self.table, self._hover_info_for_item)

        self._build_menu_button()

    def _build_menu_button(self):
        """The Media Libraries panel's "..." header menu -- same treatment
        every other library/list panel in this app already got (Stack,
        Viewer, LUT Library, CDL Library, Stack Memories, Scopes)."""
        self.menu_button = QToolButton()
        self.menu_button.setObjectName("panelMenuButton")
        self.menu_button.setAutoRaise(True)
        self.menu_button.setText("☰")  # hamburger icon, per Tom (was "...")
        self.menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.menu_button)
        self.populate_menu(menu)
        self.menu_button.setMenu(menu)

    def populate_menu(self, menu):
        """Add this panel's actions to `menu` -- shared by the "..."
        corner button above and MainWindow's top-level "Media Libraries"
        menu-bar menu, same redundant-on-purpose arrangement every other
        panel's populate_menu already follows. Only Import... lives here
        now -- Load as Source/Load as Reference/Rename/Delete moved to
        the right-click context menu (see _on_context_menu_requested),
        since they all need a selected row and don't make sense as a
        standing header-menu action the way Import... does."""
        import_action = menu.addAction("Import...", self._on_import_clicked)
        import_action.setShortcut(QKeySequence("Ctrl+I"))
        import_action.setToolTip(
            "Catalog a media file by reference -- its path and header "
            "metadata are read in, not its pixel data (see this panel's "
            "own docstring: moving/deleting the source file afterward "
            "breaks this entry).")

    def _on_context_menu_requested(self, pos):
        """Right-click menu for a specific row (per Tom, see populate_menu's
        own docstring) -- Load as Source/Load as Reference/Rename/Delete.
        No menu at all when the click lands on empty space below the last
        row (`itemAt` returns None there), same as every other panel's
        list widget already leaves empty space unclickable.

        Right-clicking a row that's already part of a multi-selection
        (ExtendedSelection, see _build_layout) keeps the WHOLE selection
        -- only collapses to just that one row if it wasn't already
        selected, same convention most multi-select lists/tables use so
        a right-click can act on "everything I already picked" rather
        than silently losing the rest of the selection."""
        item = self.table.itemAt(pos)
        if item is None:
            return
        if not self.table.selectionModel().isRowSelected(item.row(), self.table.rootIndex()):
            self.table.selectRow(item.row())
        menu = QMenu(self.table)
        menu.addAction("Load as Source", self._on_load_as_sample_clicked)
        menu.addAction("Load as Reference", self._on_load_as_reference_clicked)
        menu.addAction("Add to Timeline", self._on_add_to_timeline_clicked)
        menu.addSeparator()
        menu.addAction("Rename...", self._on_rename_clicked)
        menu.addAction("Delete", self._on_delete_clicked)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _on_header_context_menu(self, pos):
        """Right-click on the column header: a checkable entry per column,
        in the header's current left-to-right order, plus a reset back to
        COLUMNS' defaults."""
        header = self.table.horizontalHeader()
        menu = QMenu(header)
        for visual in range(header.count()):
            index = header.logicalIndex(visual)
            column = COLUMNS[index]
            action = menu.addAction(column.header or "Thumbnail")
            action.setCheckable(True)
            action.setChecked(not self.table.isColumnHidden(index))
            action.setEnabled(column.key not in _ALWAYS_VISIBLE)
            action.toggled.connect(
                lambda checked, i=index: self.table.setColumnHidden(i, not checked))
        menu.addSeparator()
        menu.addAction("Reset Columns", self.reset_columns)
        menu.exec(header.mapToGlobal(pos))

    def reset_columns(self):
        header = self.table.horizontalHeader()
        for index, column in enumerate(COLUMNS):
            header.moveSection(header.visualIndex(index), index)
            self.table.setColumnWidth(index, column.width)
            self.table.setColumnHidden(index, not column.visible)

    def get_header_state(self):
        """The table's column layout for the remembered workspace (see
        MainWindow.get_workspace_state) -- keyed by column key, not by
        QHeaderView.saveState()'s positional blob, so a layout saved
        before a column was added still restores (the new column just
        takes its COLUMNS default)."""
        header = self.table.horizontalHeader()
        order = [COLUMN_KEYS[header.logicalIndex(v)] for v in range(header.count())]
        return {
            "order": order,
            "hidden": [k for i, k in enumerate(COLUMN_KEYS) if self.table.isColumnHidden(i)],
            "widths": {k: self.table.columnWidth(i) for i, k in enumerate(COLUMN_KEYS)
                       if not self.table.isColumnHidden(i)},
        }

    def restore_header_state(self, state):
        """Inverse of get_header_state. Unknown keys (a column since
        removed) are ignored; a None/malformed state leaves the defaults."""
        if not isinstance(state, dict):
            return
        self.reset_columns()
        header = self.table.horizontalHeader()
        known = [k for k in state.get("order", []) if k in COLUMN_KEYS]
        for visual, key in enumerate(known):
            header.moveSection(header.visualIndex(COLUMN_KEYS.index(key)), visual)
        hidden = set(state.get("hidden", [])) - _ALWAYS_VISIBLE
        for index, key in enumerate(COLUMN_KEYS):
            if key in known or key in hidden:  # otherwise new since saved: keep default
                self.table.setColumnHidden(index, key in hidden)
        for key, width in (state.get("widths") or {}).items():
            if key in COLUMN_KEYS and isinstance(width, int) and width > 0:
                self.table.setColumnWidth(COLUMN_KEYS.index(key), width)

    # ---- table ------------------------------------------------------------

    def _entry_id_at_row(self, row):
        id_item = self.table.item(row, COLUMN_THUMBNAIL)
        return id_item.data(ENTRY_ID_ROLE) if id_item is not None else None

    def _selected_entry(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        entry_id = self._entry_id_at_row(row)
        if entry_id is None:
            return None
        try:
            return self.library.find(entry_id)
        except KeyError:
            return None

    def _selected_entries(self):
        """Every currently-selected row's own MediaEntry, in TABLE row
        order -- backs the actions that make sense across a multi-
        selection (Add to Timeline, Delete). A row whose own id has
        gone stale (should not normally happen -- see _entry_id_at_row)
        is silently skipped rather than raising, same tolerance
        _selected_entry's own KeyError guard already has."""
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        entries = []
        for row in rows:
            entry_id = self._entry_id_at_row(row)
            if entry_id is None:
                continue
            try:
                entries.append(self.library.find(entry_id))
            except KeyError:
                continue
        return entries

    def _hover_info_for_item(self, item):
        """Alt+hover info card content (see hover_inspector.py) -- adds
        the full source path on top of what the row's own Path column
        already shows (which may be elided by the column's current
        width)."""
        entry_id = self._entry_id_at_row(item.row())
        if entry_id is None:
            return None
        try:
            entry = self.library.find(entry_id)
        except KeyError:
            return None
        lines = [entry.name]
        if entry.source_path:
            lines.append(entry.source_path)
        if entry.width and entry.height:
            lines.append(f"{entry.width}x{entry.height}")
        if entry.is_video:
            if entry.duration:
                lines.append(f"Duration: {_format_duration(entry.duration)}")
            if entry.frame_rate:
                start_tc, _ = _timecode_cell(entry, 0)
                end_tc, _ = _timecode_cell(entry, max(0, (entry.frame_count or 1) - 1))
                lines.append(f"Start: {start_tc}  End: {end_tc}")
        else:
            lines.append(f"Color space: {entry.source_colourspace or 'Unmanaged'}")
        # Probed format details, whether or not their columns are showing.
        details = [entry.metadata.get("codec"), _bit_depth_cell(entry)[0],
                   _chroma_cell(entry)[0], _audio_cell(entry)[0]]
        details = [d for d in details if d]
        if details:
            lines.append(" · ".join(details))
        if entry.metadata.get("camera"):
            lines.append(entry.metadata["camera"])
        return "\n".join(lines)

    def _refresh_table(self, keep_selection_id=None):
        was_sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for entry in self.library.entries:
            self._append_row(entry)
        self.table.blockSignals(False)
        self.table.setSortingEnabled(was_sorting)
        if keep_selection_id is not None:
            for row in range(self.table.rowCount()):
                if self._entry_id_at_row(row) == keep_selection_id:
                    self.table.selectRow(row)
                    break
        self._apply_filter()

    def _apply_filter(self, text=None):
        """Hides every row whose own name/path doesn't contain `text`
        (case-insensitive, a plain substring match -- not a saved/named
        search, see module docstring) -- `text=None` (the default) re-
        reads the filter box itself, which is how _refresh_table keeps
        an active filter applied across Import/Rename/Delete rebuilding
        every row from scratch."""
        text = (text if text is not None else self.filter_edit.text()).strip().lower()
        for row in range(self.table.rowCount()):
            if not text:
                self.table.setRowHidden(row, False)
                continue
            entry_id = self._entry_id_at_row(row)
            entry = None
            if entry_id is not None:
                try:
                    entry = self.library.find(entry_id)
                except KeyError:
                    pass
            haystack = f"{entry.name} {entry.source_path or ''}".lower() if entry is not None else ""
            self.table.setRowHidden(row, text not in haystack)

    def _append_row(self, entry):
        row = self.table.rowCount()
        self.table.insertRow(row)

        thumb_item = QTableWidgetItem()
        thumb_item.setData(ENTRY_ID_ROLE, entry.id)
        png_bytes = media_cache.load_thumbnail(entry.thumbnail) if entry.thumbnail else None
        if png_bytes:
            pixmap = QPixmap()
            if pixmap.loadFromData(png_bytes, "PNG"):
                thumb_item.setIcon(pixmap)
        self.table.setItem(row, COLUMN_THUMBNAIL, thumb_item)

        for index, column in enumerate(COLUMNS):
            if index == COLUMN_THUMBNAIL:
                continue
            text, sort_key = column.cell(entry)
            item = _NumericItem(text, sort_key) if column.numeric else QTableWidgetItem(text)
            self.table.setItem(row, index, item)

    # ---- import (ingest a real file) -------------------------------------

    def _on_import_clicked(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import Media", "", IMAGE_FILE_FILTER)
        if not path:
            return
        self.import_from_path(path)

    def import_from_path(self, path):
        """Catalogs `path` by reference: header-only metadata (dimensions
        for a still, stream metadata for video) plus a one-time thumbnail
        decode -- never a full raster decode kept around afterward (see
        module/media_library.py docstrings). Dispatches to a still or
        video import based on `path`'s extension (see
        VIDEO_EXTENSIONS/module docstring)."""
        if Path(path).suffix.lower() in VIDEO_EXTENSIONS:
            self._import_video_from_path(path)
            return
        try:
            width, height = get_image_native_size(path)
        except Exception as e:  # noqa: BLE001 -- surface any header-read failure to the user
            QMessageBox.critical(self, "Failed to import image", str(e))
            return
        name = Path(path).stem
        thumb_descriptor = None
        try:
            array = load_image_as_array(path, max_dimension=None)
            thumb_descriptor = media_cache.store_thumbnail(
                f"media-library-{name}", thumbnail.make_thumbnail_png_bytes(array))
        except Exception:  # noqa: BLE001 -- a thumbnail failure shouldn't block cataloging the file
            pass
        entry = MediaEntry(
            name, str(path), source_filename=Path(path).name,
            media_kind="still", width=width, height=height,
            thumbnail=thumb_descriptor, metadata=_probe_or_empty(path, is_video=False))
        self.library.add_entry(entry)
        self._refresh_table(keep_selection_id=entry.id)

    def _import_video_from_path(self, path):
        """Opens `path` just long enough to read its stream metadata (no
        frame decode needed for that, see video_source.VideoSource) and
        decode ONE frame for the thumbnail -- always closed again before
        returning, whether or not the import succeeds, and that one frame
        is never kept on the entry."""
        try:
            source = video_source.VideoSource(path)
        except Exception as e:  # noqa: BLE001 -- surface any open failure to the user
            QMessageBox.critical(self, "Failed to import video", str(e))
            return
        try:
            width, height = source.width, source.height
            frame_count, frame_rate, duration = source.frame_count, source.frame_rate, source.duration
            first_frame = source.decode_next_frame()
        finally:
            source.close()
        name = Path(path).stem
        thumb_descriptor = None
        if first_frame is not None:
            thumb_descriptor = media_cache.store_thumbnail(
                f"media-library-{name}", thumbnail.make_thumbnail_png_bytes(first_frame))
        entry = MediaEntry(
            name, str(path), source_filename=Path(path).name,
            media_kind="video", width=width, height=height,
            frame_count=frame_count, frame_rate=frame_rate,
            duration=duration, thumbnail=thumb_descriptor,
            metadata=_probe_or_empty(path, is_video=True))
        self.library.add_entry(entry)
        self._refresh_table(keep_selection_id=entry.id)

    # ---- load as sample/reference / rename / delete ----------------------

    def _on_load_as_sample_clicked(self, *_args):
        entry = self._selected_entry()
        if entry is None:
            return
        self.load_as_sample_requested.emit(entry)

    def _on_load_as_reference_clicked(self):
        entry = self._selected_entry()
        if entry is None:
            return
        if entry.is_video:
            QMessageBox.information(
                self, "Load as Reference",
                "The Reference slot doesn't support video -- use \"Load as "
                "Source\" instead.")
            return
        self.load_as_reference_requested.emit(entry)

    def _on_add_to_timeline_clicked(self):
        # Every selected row, in table order -- add_to_timeline_requested
        # is a plain one-entry signal, so a multi-selection just emits it
        # once per entry; main_window.py's own handler (TimelinePanel.
        # add_clip_from_library_entry) appends one clip per emission, in
        # the order received.
        for entry in self._selected_entries():
            self.add_to_timeline_requested.emit(entry)

    def _on_rename_clicked(self):
        entry = self._selected_entry()
        if entry is None:
            return
        name, ok = QInputDialog.getText(self, "Rename Media", "Name:", text=entry.name)
        name = name.strip()
        if not ok or not name:
            return
        self.library.rename(entry.id, name)
        self._refresh_table(keep_selection_id=entry.id)

    def _on_delete_clicked(self):
        entries = self._selected_entries()
        if not entries:
            return
        if len(entries) == 1:
            message = f'Delete "{entries[0].name}" from the library? This can\'t be undone.'
        else:
            message = f"Delete {len(entries)} items from the library? This can't be undone."
        reply = QMessageBox.question(
            self, "Delete Media", message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        for entry in entries:
            self.library.remove(entry.id)
        self._refresh_table()

    # ---- session save/load (see session_io.py) ---------------------------

    def get_library(self):
        return self.library

    def load_library(self, library):
        """Replace the whole library wholesale -- used by MainWindow for
        session load, same "wholesale replace + rebuild the table" pattern
        as LUTLibraryPanel.load_library/StackMemoriesPanel.load_library."""
        self.library = library
        self._refresh_table()
