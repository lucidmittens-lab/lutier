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

from PySide6.QtCore import QItemSelection, QItemSelectionModel, QMimeData, QTimer, Signal, Qt
from PySide6.QtGui import QDrag, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem, QAbstractItemView,
    QHeaderView, QMessageBox, QInputDialog, QFileDialog, QToolButton, QMenu,
    QLineEdit, QListWidget, QListWidgetItem, QSplitter, QStackedWidget, QScrollArea,
    QFrame, QHBoxLayout, QPushButton, QButtonGroup, QSizePolicy,
)

from app_logic import load_image_as_array, get_image_native_size
from media_library import MediaPool, MediaEntry, MEDIA_ENTRY_DRAG_MIME_TYPE
import timeline as tl
import hover_inspector
import media_cache
from media_grid import GridItem, MediaGrid
import media_probe
import colour_pipeline
import thumbnail
import video_source
import timecode

# Shared with the Browser, which lists these as media (see browser_panel.py).
from browser_panel import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS  # noqa: E402


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
LIST_VIEW = "list"
GRID_VIEW = "grid"
SORT_KEY_ROLE = Qt.ItemDataRole.UserRole + 1  # numeric sort key, see _NumericItem
# A timeline row carries its timeline id here instead of ENTRY_ID_ROLE (so
# every entry-only action and the drag-out simply don't see it).
TIMELINE_ID_ROLE = Qt.ItemDataRole.UserRole + 2
LIBRARY_ID_ROLE = Qt.ItemDataRole.UserRole  # on the side panel's items


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


def _timeline_cells(timeline):
    """{column key: (text, sort_key)} for a timeline's row -- the
    columns that mean something for a timeline (record-side timecode
    from 00:00:00:00); every other column stays blank."""
    frames = timeline.total_frame_count()
    rate = timeline.frame_rate
    cells = {
        "name": (timeline.name, None),
        "kind": ("Timeline", None),
        "frames": (str(frames), frames),
        "fps": (f"{round(rate, 3):g}", rate),
        "duration": (_format_duration(timeline.duration), timeline.duration),
        "start_tc": (timecode.frames_to_timecode(0, rate), 0),
        "end_tc": (timecode.frames_to_timecode(max(0, frames - 1), rate), max(0, frames - 1)),
    }
    return cells


class _Column:
    """One Media Libraries table column. `cell(entry)` returns (text,
    sort_key): a non-None sort_key makes the cell a _NumericItem (so
    "10:02" sorts after "1:05"); None sorts by the text itself.
    `visible` is the default before the user picks columns from the
    header's right-click menu (see _on_header_context_menu)."""

    def __init__(self, key, header, cell, visible, width, numeric=False):
        self.key, self.header, self.cell = key, header, cell
        self.visible, self.width, self.numeric = visible, width, numeric


# Logical order is the default left-to-right order. Only the essentials
# start visible (Tom, 2026-10-05: "everything feels rather crowded") --
# the rest are a header right-click away, and the hover card shows path,
# timecode and format details regardless. The user can drag
# columns around and show/hide them (see MediaPanel.get_header_state).
# Thumbnail stays logical column 0: it carries the entry id (ENTRY_ID_ROLE).
COLUMNS = (
    _Column("thumbnail", "", lambda e: ("", None), True, 40),
    _Column("name", "Name", lambda e: _text_cell(e.name), True, 150),
    _Column("kind", "Kind", lambda e: ("Video" if e.is_video else "Still", None), True, 66),
    _Column("resolution", "Resolution", _resolution_cell, True, 84, numeric=True),
    _Column("duration", "Duration", _duration_cell, True, 64, numeric=True),
    _Column("start_tc", "Start TC", lambda e: _timecode_cell(e, 0), False, 96, numeric=True),
    _Column("end_tc", "End TC",
            lambda e: _timecode_cell(e, max(0, (e.frame_count or 1) - 1)), False, 96, numeric=True),
    _Column("frames", "Frames", _frames_cell, False, 64, numeric=True),
    _Column("fps", "FPS", _fps_cell, True, 44, numeric=True),
    _Column("codec", "Codec", lambda e: _text_cell(e.metadata.get("codec")), True, 100),
    _Column("bit_depth", "Bit Depth", _bit_depth_cell, False, 80, numeric=True),
    _Column("chroma", "Chroma", _chroma_cell, False, 64),
    _Column("audio", "Audio", _audio_cell, False, 96, numeric=True),
    _Column("pixel_aspect", "Pixel Aspect", _par_cell, False, 80, numeric=True),
    _Column("camera", "Camera", lambda e: _text_cell(e.metadata.get("camera")), False, 140),
    _Column("format", "Format", lambda e: _text_cell(e.metadata.get("format")), False, 90),
    _Column("size", "Size", _size_cell, False, 76, numeric=True),
    _Column("modified", "Date Modified", _modified_cell, False, 130, numeric=True),
    # The source's INPUT color space (colour_pipeline.py) -- what Record
    # converts from into the project working space. Shown by default: it's
    # the first thing to check on camera footage.
    _Column("colorspace", "Input Space",
            lambda e: (e.source_colourspace or "Working space", None), True, 120),
    _Column("filename", "File Name",
            lambda e: _text_cell(e.source_filename or (Path(e.source_path).name if e.source_path else "")),
            False, 160),
    _Column("path", "Path", lambda e: _text_cell(e.source_path), False, 200),
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
# Bumped when the default column set changes, so a remembered layout from
# before (which just recorded the old defaults) gives way to the new ones.
HEADER_STATE_VERSION = 3  # 3: Input Space on by default
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


def _dropped_media_paths(event, source_to_ignore=None):
    """Local media file paths in a drag (file URLs from the Browser or
    the desktop file manager), or [] -- also [] for a drag that started
    in `source_to_ignore` (a table's own outgoing row drag)."""
    if event.source() is source_to_ignore and source_to_ignore is not None:
        return []
    mime = event.mimeData()
    if not mime.hasUrls():
        return []
    paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
    return [p for p in paths if Path(p).suffix.lower() in IMAGE_EXTENSIONS + VIDEO_EXTENSIONS]


class _LibraryList(QListWidget):
    """The side panel's library list -- also a drop target: media files
    dropped on a library are imported into THAT library."""
    files_dropped = Signal(list, object)  # (paths, library item or None)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

    def dragEnterEvent(self, event):
        if _dropped_media_paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if _dropped_media_paths(event) and self.itemAt(event.position().toPoint()) is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = _dropped_media_paths(event)
        item = self.itemAt(event.position().toPoint())
        if paths and item is not None:
            event.acceptProposedAction()
            self.files_dropped.emit(paths, item)
        else:
            event.ignore()


class _MediaTable(QTableWidget):
    """QTableWidget with Qt's own row-drag machinery repurposed as a
    drag SOURCE carrying this library's entry id (MEDIA_ENTRY_DRAG_MIME
    _TYPE) -- the half of "drag a clip from the Media Library onto the
    Timeline" (per Tom: "make it more similar to a typical NLE") that
    lives here; see timeline_track_widget.py's own dragEnterEvent/
    dropEvent for the drop TARGET half. DragOnly (not DragDrop) -- this
    panel has no internal reorder/drop target of its own, only an
    outgoing drag."""

    files_dropped = Signal(list)

    # Drop target for media files from outside (the Browser, the desktop
    # file manager) -- imported into the current library. Its own row
    # drags (to the Timeline) are ignored here.
    def dragEnterEvent(self, event):
        if _dropped_media_paths(event, self):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if _dropped_media_paths(event, self):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = _dropped_media_paths(event, self)
        if paths:
            event.acceptProposedAction()
            self.files_dropped.emit(paths)
        else:
            event.ignore()

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


class _DropMediaGrid(MediaGrid):
    """The Grid view, also a drop target for media files (from the
    Browser or the desktop) -- the same rule and import path as the
    table's own dropEvent (_dropped_media_paths)."""
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if _dropped_media_paths(event, self):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if _dropped_media_paths(event, self):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = _dropped_media_paths(event, self)
        if paths:
            event.acceptProposedAction()
            self.files_dropped.emit(paths)
        else:
            event.ignore()


class MediaPanel(QWidget):
    load_as_sample_requested = Signal(object)      # a media_library.MediaEntry -- grade it in Record
    open_in_source_requested = Signal(object)      # a media_library.MediaEntry -- view it in Source
    load_as_reference_requested = Signal(object)   # a media_library.MediaEntry
    add_to_timeline_requested = Signal(object)     # a media_library.MediaEntry

    # A timeline row was opened (double-click / Load Timeline) -- a
    # timeline.Timeline from this pool; MainWindow loads it into the
    # Timeline panel.
    timeline_load_requested = Signal(object)
    timeline_unload_requested = Signal()
    # A timeline was renamed or deleted here -- MainWindow refreshes or
    # unloads the Timeline panel if it's the loaded one.
    timeline_renamed = Signal(object)
    timeline_deleted = Signal(object)
    # An entry's input color space was changed here (media_library.MediaEntry)
    # -- MainWindow updates timeline clips of that file and the viewers.
    input_colourspace_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = MediaPool()
        # The library selected in the side panel: what the table shows and
        # where imports, drops and New Timeline land.
        self.library = self.pool.libraries[0]
        self._loaded_timeline = None
        self._frame_rate_getter = None
        self._build_layout()
        self._refresh_library_list()
        self._refresh_table()

    def _build_layout(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)  # PanelFrame provides the outer gutter
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(self.splitter)

        # Side panel: the session's libraries (Tom, 2026-10-05: "a side
        # panel to select the target library").
        self.library_list = _LibraryList()
        self.library_list.files_dropped.connect(self._on_files_dropped_on_library)
        self.library_list.setObjectName("mediaLibraryList")
        self.library_list.currentItemChanged.connect(self._on_library_selected)
        self.library_list.itemDoubleClicked.connect(lambda _item: self._on_rename_library_clicked())
        self.library_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.library_list.customContextMenuRequested.connect(self._on_library_context_menu)
        self.splitter.addWidget(self.library_list)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        self.splitter.addWidget(content)
        self.splitter.setStretchFactor(1, 1)
        # The library list never collapses to a sliver ("Media (" was all
        # that showed in a narrow dock).
        self.library_list.setMinimumWidth(110)
        self.splitter.setCollapsible(0, False)
        self.splitter.setSizes([130, 600])

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter by name or path...")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        # Filter box, then List | Grid (style.py's segmented control).
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        filter_row.addWidget(self.filter_edit, stretch=1)
        segmented = QFrame()
        segmented.setObjectName("segmentedControl")
        segmented.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        segmented_layout = QHBoxLayout(segmented)
        segmented_layout.setContentsMargins(2, 2, 2, 2)
        segmented_layout.setSpacing(2)
        self._view_mode_buttons = {}
        view_group = QButtonGroup(segmented)
        view_group.setExclusive(True)
        for mode, label in ((LIST_VIEW, "List"), (GRID_VIEW, "Grid")):
            button = QPushButton(label)
            button.setObjectName("segmentButton")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, m=mode: self.set_view_mode(m))
            view_group.addButton(button)
            segmented_layout.addWidget(button)
            self._view_mode_buttons[mode] = button
        filter_row.addWidget(segmented)
        layout.addLayout(filter_row)

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
        # DragOnly turns drops off; files from the Browser/file manager are
        # accepted by _MediaTable's own drop handlers.
        self.table.setAcceptDrops(True)
        self.table.files_dropped.connect(lambda paths: self.import_paths(paths))
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
        self.table.itemDoubleClicked.connect(self._on_row_double_clicked)
        # Per-item actions (Load as Source/Load as Reference/Rename/
        # Delete) live on a right-click context menu, not the "..."
        # header menu (per Tom: "rename, load as source, delete and load
        # as ref should move to right clicking a particular clip") --
        # see _on_context_menu_requested. Only Import... (panel-level,
        # not tied to a selection) stays in populate_menu below.
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu_requested)
        hover_inspector.register(self.table, self._hover_info_for_item)

        # Grid view (media_grid.py): cards for the table's VISIBLE rows, in
        # the table's order; its selection drives the table's, so every
        # action below runs the same from either view.
        self.grid = _DropMediaGrid()
        self.grid.files_dropped.connect(lambda paths: self.import_paths(paths))
        self.grid.selection_changed.connect(self._select_table_rows)
        self.grid.activated.connect(lambda row: self._on_row_double_clicked(self.table.item(row, COLUMN_THUMBNAIL)))
        self.grid.context_menu_requested.connect(
            lambda row, global_pos: self._exec_context_menu_for_row(row, global_pos, self.grid))
        self.grid.drag_requested.connect(self._start_grid_drag)
        self.grid_scroll = QScrollArea()
        self.grid_scroll.setObjectName("mediaGridScroll")
        self.grid_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.grid_scroll.setWidgetResizable(True)
        self.grid_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grid_scroll.setWidget(self.grid)
        self.table.itemSelectionChanged.connect(
            lambda: self.grid.set_selected_rows(self._table_selected_row_set()))
        self.table.horizontalHeader().sortIndicatorChanged.connect(
            lambda *_args: QTimer.singleShot(0, self._rebuild_grid))

        self.view_stack = QStackedWidget()
        self.view_stack.addWidget(self.grid_scroll)  # GRID_VIEW
        self.view_stack.addWidget(self.table)        # LIST_VIEW
        layout.addWidget(self.view_stack, stretch=1)
        self.set_view_mode(GRID_VIEW)

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
        menu.addAction("New Timeline", self.new_timeline)
        menu.addSeparator()
        menu.addAction("New Library...", self._on_new_library_clicked)

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
        self._exec_context_menu_for_row(item.row(), self.table.viewport().mapToGlobal(pos), self.table)

    def _exec_context_menu_for_row(self, row, global_pos, parent):
        """The per-row menu, for the List (above) and the Grid alike --
        acts on the table's current selection."""
        menu = QMenu(parent)
        timeline = self._timeline_at_row(row)
        if timeline is not None:
            if timeline is self._loaded_timeline:
                menu.addAction("Unload Timeline", self.timeline_unload_requested.emit)
            else:
                menu.addAction("Load Timeline", lambda: self.timeline_load_requested.emit(timeline))
            menu.addSeparator()
            menu.addAction("Rename...", self._on_rename_clicked)
            menu.addAction("Delete", self._on_delete_clicked)
            menu.exec(global_pos)
            return
        menu.addAction("Open in Source Viewer", self._on_open_in_source_clicked)
        menu.addAction("Grade in Record (no timeline)", self._on_load_as_sample_clicked)
        menu.addAction("Load as Reference", self._on_load_as_reference_clicked)
        menu.addAction("Add to Timeline", self._on_add_to_timeline_clicked)
        self._add_input_space_menu(menu)
        menu.addSeparator()
        menu.addAction("Rename...", self._on_rename_clicked)
        menu.addAction("Delete", self._on_delete_clicked)
        menu.exec(global_pos)

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
            "version": HEADER_STATE_VERSION,
            "order": order,
            "hidden": [k for i, k in enumerate(COLUMN_KEYS) if self.table.isColumnHidden(i)],
            "widths": {k: self.table.columnWidth(i) for i, k in enumerate(COLUMN_KEYS)
                       if not self.table.isColumnHidden(i)},
        }

    def restore_header_state(self, state):
        """Inverse of get_header_state. Unknown keys (a column since
        removed) are ignored; a None/malformed state leaves the defaults."""
        if not isinstance(state, dict) or state.get("version", 1) < HEADER_STATE_VERSION:
            return  # malformed, or saved under older defaults -- keep the current ones
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
        lines.append(f"Input: {entry.source_colourspace or 'working space'}")
        # Probed format details, whether or not their columns are showing.
        details = [entry.metadata.get("codec"), _bit_depth_cell(entry)[0],
                   _chroma_cell(entry)[0], _audio_cell(entry)[0]]
        details = [d for d in details if d]
        if details:
            lines.append(" · ".join(details))
        if entry.metadata.get("camera"):
            lines.append(entry.metadata["camera"])
        return "\n".join(lines)

    def _refresh_table(self, keep_selection_id=None, keep_timeline_id=None):
        was_sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for timeline in self.library.timelines:
            self._append_timeline_row(timeline)
        for entry in self.library.entries:
            self._append_row(entry)
        self.table.blockSignals(False)
        self.table.setSortingEnabled(was_sorting)
        for row in range(self.table.rowCount()):
            if ((keep_selection_id is not None and self._entry_id_at_row(row) == keep_selection_id)
                    or (keep_timeline_id is not None
                        and self._timeline_id_at_row(row) == keep_timeline_id)):
                self.table.selectRow(row)
                break
        self._apply_filter()
        self._refresh_library_counts()

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
            if entry is not None:
                haystack = f"{entry.name} {entry.source_path or ''}".lower()
            else:
                timeline = self._timeline_at_row(row)
                haystack = timeline.name.lower() if timeline is not None else ""
            self.table.setRowHidden(row, text not in haystack)
        self._rebuild_grid()

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
        """Catalogs `path` into the current library by reference:
        header-only metadata plus a one-time thumbnail decode -- never a
        full raster decode kept around afterward (see module/
        media_library.py docstrings). Reports a failure in a dialog."""
        try:
            entry = self._build_entry(path)
        except Exception as e:  # noqa: BLE001 -- surface any read failure to the user
            kind = "video" if Path(path).suffix.lower() in VIDEO_EXTENSIONS else "image"
            QMessageBox.critical(self, f"Failed to import {kind}", str(e))
            return None
        self.library.add_entry(entry)
        self._refresh_table(keep_selection_id=entry.id)
        return entry

    def import_paths(self, paths, library=None):
        """Import several files at once into `library` (default: the
        current one) -- Browser drags/double-clicks and file-manager
        drops. Files that aren't media are ignored; files that fail to
        read are listed in ONE warning at the end rather than a dialog
        each. Returns the new entries."""
        library = library or self.library
        media = [p for p in paths if Path(p).suffix.lower() in IMAGE_EXTENSIONS + VIDEO_EXTENSIONS]
        entries, failures = [], []
        for path in media:
            try:
                entries.append(library.add_entry(self._build_entry(path)))
            except Exception as e:  # noqa: BLE001 -- one bad file shouldn't stop the rest
                failures.append(f"{Path(path).name}: {e}")
        if library is self.library:
            self._refresh_table(keep_selection_id=entries[-1].id if entries else None)
        else:
            self._refresh_library_counts()
        if failures:
            QMessageBox.warning(
                self, "Some files weren't imported",
                f"Imported {len(entries)} of {len(media)}.\n\n" + "\n".join(failures))
        return entries

    def _build_entry(self, path):
        """A MediaEntry for `path` (still or video by extension); raises
        if the file can't be read."""
        if Path(path).suffix.lower() in VIDEO_EXTENSIONS:
            return self._build_video_entry(path)
        width, height = get_image_native_size(path)
        name = Path(path).stem
        thumb_descriptor = None
        try:
            array = load_image_as_array(path, max_dimension=None)
            thumb_descriptor = media_cache.store_thumbnail(
                f"media-library-{name}", thumbnail.make_thumbnail_png_bytes(array))
        except Exception:  # noqa: BLE001 -- a thumbnail failure shouldn't block cataloging the file
            pass
        metadata = _probe_or_empty(path, is_video=False)
        return MediaEntry(
            name, str(path), source_filename=Path(path).name,
            source_colourspace=colour_pipeline.suggest_input_colourspace(metadata),
            media_kind="still", width=width, height=height,
            thumbnail=thumb_descriptor, metadata=metadata)

    def _build_video_entry(self, path):
        """Opens `path` just long enough to read its stream metadata (no
        frame decode needed for that, see video_source.VideoSource) and
        decode ONE frame for the thumbnail -- always closed again, and
        that one frame is never kept on the entry."""
        source = video_source.VideoSource(path)
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
        metadata = _probe_or_empty(path, is_video=True)
        return MediaEntry(
            name, str(path), source_filename=Path(path).name,
            source_colourspace=colour_pipeline.suggest_input_colourspace(metadata),
            media_kind="video", width=width, height=height,
            frame_count=frame_count, frame_rate=frame_rate,
            duration=duration, thumbnail=thumb_descriptor, metadata=metadata)

    # ---- load as sample/reference / rename / delete ----------------------

    # Common camera/delivery spaces first; the rest of the config after.
    _COMMON_INPUT_SPACES = (
        "ARRI LogC4", "ARRI LogC3 (EI800)", "S-Log3 S-Gamut3.Cine", "S-Log3 Venice S-Gamut3.Cine",
        "Log3G10 REDWideGamutRGB", "BMDFilm WideGamut Gen5", "V-Log V-Gamut", "Apple Log",
        "ACES2065-1", "ACEScct", "Camera Rec.709", "sRGB Encoded Rec.709 (sRGB)",
        "Gamma 2.4 Encoded Rec.709", "Linear Rec.709 (sRGB)")

    def _add_input_space_menu(self, menu):
        """"Input Color Space" submenu for the selected entries (see
        colour_pipeline.py) -- checked on the current one."""
        entries = self._selected_entries()
        if not entries:
            return
        current = entries[0].source_colourspace
        submenu = menu.addMenu("Input Color Space")
        available = colour_pipeline.available_input_colourspaces()

        def add(label, value):
            action = submenu.addAction(label, lambda: self.set_input_colourspace(entries, value))
            action.setCheckable(True)
            action.setChecked(value == current)

        add("Same as working space", None)
        submenu.addSeparator()
        common = [s for s in self._COMMON_INPUT_SPACES if s in available]
        for space in common:
            add(space, space)
        more = submenu.addMenu("All color spaces")
        for space in available:
            action = more.addAction(space, lambda s=space: self.set_input_colourspace(entries, s))
            action.setCheckable(True)
            action.setChecked(space == current)

    def set_input_colourspace(self, entries, space):
        for entry in entries:
            entry.source_colourspace = space
            self.input_colourspace_changed.emit(entry)
        self._refresh_table(keep_selection_id=entries[0].id if entries else None)

    def _on_open_in_source_clicked(self, *_args):
        entry = self._selected_entry()
        if entry is not None:
            self.open_in_source_requested.emit(entry)

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
        timeline = self._timeline_at_row(self.table.currentRow())
        if timeline is not None:
            name, ok = QInputDialog.getText(self, "Rename Timeline", "Name:", text=timeline.name)
            name = name.strip()
            if ok and name:
                timeline.name = name
                self._refresh_table(keep_timeline_id=timeline.id)
                self.timeline_renamed.emit(timeline)
            return
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
        timelines = self._selected_timelines()
        items = [e.name for e in entries] + [t.name for t in timelines]
        if not items:
            return
        if len(items) == 1:
            message = f'Delete "{items[0]}" from the library? This can\'t be undone.'
        else:
            message = f"Delete {len(items)} items from the library? This can't be undone."
        reply = QMessageBox.question(
            self, "Delete Media", message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        for entry in entries:
            self.library.remove(entry.id)
        for timeline in timelines:
            self.library.remove_timeline(timeline.id)
            self.timeline_deleted.emit(timeline)
        self._refresh_table()

    # ---- timelines (media_library.py, LIBRARIES AND TIMELINES) -----------

    def _append_timeline_row(self, timeline):
        row = self.table.rowCount()
        self.table.insertRow(row)
        cells = _timeline_cells(timeline)
        bold = timeline is self._loaded_timeline
        for index, column in enumerate(COLUMNS):
            text, sort_key = cells.get(column.key, ("", None))
            item = _NumericItem(text, sort_key) if column.numeric else QTableWidgetItem(text)
            if index == COLUMN_THUMBNAIL:
                item.setData(TIMELINE_ID_ROLE, timeline.id)
                item.setText("▶" if bold else "")
                item.setToolTip("Loaded in the Timeline panel" if bold else "")
            if bold:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self.table.setItem(row, index, item)

    def _timeline_id_at_row(self, row):
        item = self.table.item(row, COLUMN_THUMBNAIL) if row >= 0 else None
        return item.data(TIMELINE_ID_ROLE) if item is not None else None

    def _timeline_at_row(self, row):
        timeline_id = self._timeline_id_at_row(row)
        if timeline_id is None:
            return None
        return next((t for t in self.library.timelines if t.id == timeline_id), None)

    def _selected_timelines(self):
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        return [t for t in (self._timeline_at_row(r) for r in rows) if t is not None]

    def _on_row_double_clicked(self, item):
        timeline = self._timeline_at_row(item.row())
        if timeline is not None:
            self.timeline_load_requested.emit(timeline)
        else:
            self._on_open_in_source_clicked()

    def set_frame_rate_getter(self, getter):
        """`getter()` -> the frame rate a New Timeline starts at (MainWindow:
        the project's edit rate)."""
        self._frame_rate_getter = getter

    def new_timeline(self, name=None):
        """Create an empty timeline in the current library, at the
        project's edit rate (set_frame_rate_getter), and ask for it to be
        loaded. Returns it."""
        existing = [t.name for lib in self.pool.libraries for t in lib.timelines]
        rate = self._frame_rate_getter() if self._frame_rate_getter else tl.DEFAULT_FRAME_RATE
        timeline = tl.Timeline(name=name or MediaPool.unique_name("Timeline", existing),
                               frame_rate=rate)
        self.library.add_timeline(timeline)
        self._refresh_table(keep_timeline_id=timeline.id)
        self.timeline_load_requested.emit(timeline)
        return timeline

    def set_loaded_timeline(self, timeline):
        """Mark which timeline (or None) the Timeline panel has open."""
        self._loaded_timeline = timeline
        self._refresh_table()

    # ---- libraries (the side panel) ---------------------------------------

    def _refresh_library_list(self):
        self.library_list.blockSignals(True)
        self.library_list.clear()
        for library in self.pool.libraries:
            item = QListWidgetItem()
            item.setData(LIBRARY_ID_ROLE, library.id)
            self.library_list.addItem(item)
            if library is self.library:
                self.library_list.setCurrentItem(item)
        self.library_list.blockSignals(False)
        self._refresh_library_counts()

    def _refresh_library_counts(self):
        for row in range(self.library_list.count()):
            item = self.library_list.item(row)
            try:
                library = self.pool.find_library(item.data(LIBRARY_ID_ROLE))
            except KeyError:
                continue
            count = len(library.entries) + len(library.timelines)
            item.setText(f"{library.name} ({count})" if count else library.name)

    def _on_library_selected(self, current, _previous):
        if current is None:
            return
        try:
            self.library = self.pool.find_library(current.data(LIBRARY_ID_ROLE))
        except KeyError:
            return
        self._refresh_table()

    def select_library(self, library):
        self.library = library
        self._refresh_library_list()
        self._refresh_table()

    def _on_files_dropped_on_library(self, paths, item):
        try:
            library = self.pool.find_library(item.data(LIBRARY_ID_ROLE))
        except KeyError:
            return
        self.import_paths(paths, library=library)

    def _on_library_context_menu(self, pos):
        menu = QMenu(self.library_list)
        menu.addAction("New Library...", self._on_new_library_clicked)
        if self.library_list.itemAt(pos) is not None:
            menu.addAction("Rename...", self._on_rename_library_clicked)
            delete = menu.addAction("Delete", self._on_delete_library_clicked)
            delete.setEnabled(len(self.pool.libraries) > 1)
        menu.exec(self.library_list.viewport().mapToGlobal(pos))

    def _on_new_library_clicked(self):
        default = MediaPool.unique_name("Library", [lib.name for lib in self.pool.libraries])
        name, ok = QInputDialog.getText(self, "New Library", "Name:", text=default)
        name = name.strip()
        if ok and name:
            self.select_library(self.pool.add_library(name))

    def _on_rename_library_clicked(self):
        name, ok = QInputDialog.getText(self, "Rename Library", "Name:", text=self.library.name)
        name = name.strip()
        if ok and name:
            self.library.name = name
            self._refresh_library_counts()

    def _on_delete_library_clicked(self):
        if len(self.pool.libraries) <= 1:
            return
        library = self.library
        if not library.is_empty():
            reply = QMessageBox.question(
                self, "Delete Library",
                f'Delete the library "{library.name}" and everything in it '
                "(the media files themselves aren't touched)? This can't be undone.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if reply != QMessageBox.StandardButton.Yes:
                return
        for timeline in list(library.timelines):
            self.timeline_deleted.emit(timeline)
        self.pool.remove_library(library.id)
        self.select_library(self.pool.libraries[0])

    # ---- session save/load (see session_io.py) ---------------------------

    def get_pool(self):
        return self.pool

    def find_entry(self, entry_id):
        """The MediaEntry with `entry_id` in any library (raises KeyError)
        -- a drag from the table carries only the id."""
        return self.pool.find_entry(entry_id)

    def load_pool(self, pool):
        """Replace every library wholesale -- session load/New Session,
        same "wholesale replace + rebuild" pattern as LUTLibraryPanel.
        load_library/StackMemoriesPanel.load_library. Selects the first
        library."""
        self.pool = pool
        self.library = pool.libraries[0]
        self._loaded_timeline = None
        self._refresh_library_list()
        self._refresh_table()

    # ---- Grid view (media_grid.py) ------------------------------------------

    def view_mode(self):
        return GRID_VIEW if self.view_stack.currentWidget() is self.grid_scroll else LIST_VIEW

    def set_view_mode(self, mode):
        self.view_stack.setCurrentWidget(self.grid_scroll if mode == GRID_VIEW else self.table)
        self._view_mode_buttons[mode].setChecked(True)

    def _cell_text(self, row, key):
        item = self.table.item(row, COLUMN_KEYS.index(key))
        return item.text().strip() if item is not None else ""

    def _rebuild_grid(self):
        """One card per VISIBLE table row, in the table's order."""
        items = []
        for row in range(self.table.rowCount()):
            if self.table.isRowHidden(row):
                continue
            timeline = self._timeline_at_row(row)
            if timeline is not None:
                items.append(GridItem(
                    row=row, name=timeline.name, is_timeline=True,
                    is_loaded=timeline is self._loaded_timeline,
                    details=f"Timeline · {len(timeline.clips)} clip{'s' if len(timeline.clips) != 1 else ''}",
                    corner_text=self._cell_text(row, "duration")))
                continue
            entry_id = self._entry_id_at_row(row)
            try:
                entry = self.library.find(entry_id) if entry_id is not None else None
            except KeyError:
                entry = None
            if entry is None:
                continue
            details = " · ".join(t for t in (self._cell_text(row, "resolution"),
                                             self._cell_text(row, "fps"),
                                             self._cell_text(row, "codec")) if t)
            items.append(GridItem(row=row, name=entry.name, details=details,
                                  corner_text=self._cell_text(row, "duration"),
                                  thumbnail=entry.thumbnail))
        self.grid.set_items(items)
        self.grid.set_selected_rows(self._table_selected_row_set())

    def _table_selected_row_set(self):
        """Rows with any selected cell -- selectedRows() needs EVERY column
        selected, and selectRow() leaves the hidden columns out."""
        return {index.row() for index in self.table.selectionModel().selectedIndexes()}

    def _select_table_rows(self, rows):
        """Mirror the Grid's selection into the table (current row = the
        first), so the table-based actions see it."""
        model = self.table.selectionModel()
        selection = QItemSelection()
        for row in rows:
            selection.select(self.table.model().index(row, 0),
                             self.table.model().index(row, self.table.columnCount() - 1))
        self.table.blockSignals(True)
        model.select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        if rows:
            model.setCurrentIndex(self.table.model().index(rows[0], 0),
                                  QItemSelectionModel.SelectionFlag.NoUpdate)
        self.table.blockSignals(False)

    def _start_grid_drag(self, rows):
        """Drag from the Grid -- the same payload as dragging table rows."""
        items = [self.table.item(row, COLUMN_THUMBNAIL) for row in rows]
        mime = self.table.mimeData([i for i in items if i is not None])
        if mime is None:
            return
        drag = QDrag(self.grid)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)
