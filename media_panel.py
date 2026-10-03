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
(Kind, Resolution, Duration, Color Space, Path) are directly visible and
sortable, matching the "columned database" framing this rewrite is
built around.

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

NOT TESTED IN THIS ENVIRONMENT -- same PySide6/libEGL wall as the rest of
the GUI layer. Please run main_window.py and confirm: (1) Import... on a
real image/video file adds a row showing its resolution/duration/color
space, (2) clicking a column header sorts the table (numerically for
Resolution/Duration, not lexicographically -- "10:02" must sort after
"1:05", not before), (3) right-clicking a row shows "Load as Source"/
"Load as Reference"/"Rename..."/"Delete", each acting on THAT row even
after sorting has reordered it, (4) Rename/Delete work and ask before
Delete, (5) an entry survives Save Session -> Open Session on the same
machine.
"""
from pathlib import Path

from PySide6.QtCore import QMimeData, Signal, Qt
from PySide6.QtGui import QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem, QAbstractItemView,
    QHeaderView, QMessageBox, QInputDialog, QFileDialog, QToolButton, QMenu,
)

from app_logic import load_image_as_array, get_image_native_size
from media_library import MediaLibrary, MediaEntry, MEDIA_ENTRY_DRAG_MIME_TYPE
import hover_inspector
import media_cache
import thumbnail
import video_source
import timecode

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".exr")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".avi", ".m4v")

IMAGE_FILE_FILTER = (
    "Media files (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.exr "
    "*.mp4 *.mov *.mkv *.avi *.m4v)"
    ";;Image files (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.exr)"
    ";;Video files (*.mp4 *.mov *.mkv *.avi *.m4v)"
    ";;All files (*)"
)

COLUMN_THUMBNAIL, COLUMN_NAME, COLUMN_KIND, COLUMN_RESOLUTION, \
    COLUMN_DURATION, COLUMN_COLORSPACE, COLUMN_PATH = range(7)
COLUMN_HEADERS = ("", "Name", "Kind", "Resolution", "Duration", "Color Space", "Path")

ENTRY_ID_ROLE = Qt.ItemDataRole.UserRole  # carried on every row's column-0 item, see module docstring
SORT_KEY_ROLE = Qt.ItemDataRole.UserRole + 1  # numeric sort key, see _NumericItem


def _format_duration(seconds):
    seconds = max(0, round(seconds))
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}:{secs:02d}"


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

        self.table = _MediaTable(0, len(COLUMN_HEADERS))
        self.table.setHorizontalHeaderLabels(COLUMN_HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
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
        self.table.setColumnWidth(COLUMN_THUMBNAIL, 56)
        self.table.setColumnWidth(COLUMN_NAME, 160)
        self.table.setColumnWidth(COLUMN_KIND, 56)
        self.table.setColumnWidth(COLUMN_RESOLUTION, 90)
        self.table.setColumnWidth(COLUMN_DURATION, 70)
        self.table.setColumnWidth(COLUMN_COLORSPACE, 100)
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
        list widget already leaves empty space unclickable."""
        item = self.table.itemAt(pos)
        if item is None:
            return
        self.table.selectRow(item.row())
        menu = QMenu(self.table)
        menu.addAction("Load as Source", self._on_load_as_sample_clicked)
        menu.addAction("Load as Reference", self._on_load_as_reference_clicked)
        menu.addAction("Add to Timeline", self._on_add_to_timeline_clicked)
        menu.addSeparator()
        menu.addAction("Rename...", self._on_rename_clicked)
        menu.addAction("Delete", self._on_delete_clicked)
        menu.exec(self.table.viewport().mapToGlobal(pos))

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
                start_tc = timecode.frames_to_timecode(0, entry.frame_rate)
                end_frame = max(0, (entry.frame_count or 1) - 1)
                end_tc = timecode.frames_to_timecode(end_frame, entry.frame_rate)
                lines.append(f"Start: {start_tc}  End: {end_tc}")
        else:
            lines.append(f"Color space: {entry.source_colourspace or 'Unmanaged'}")
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

        self.table.setItem(row, COLUMN_NAME, QTableWidgetItem(entry.name))
        self.table.setItem(row, COLUMN_KIND, QTableWidgetItem(
            "Video" if entry.is_video else "Still"))

        if entry.width and entry.height:
            res_text = f"{entry.width}x{entry.height}"
            res_key = entry.width * entry.height
        else:
            res_text, res_key = "", None
        self.table.setItem(row, COLUMN_RESOLUTION, _NumericItem(res_text, res_key))

        if entry.is_video and entry.duration:
            dur_text, dur_key = _format_duration(entry.duration), entry.duration
        else:
            dur_text, dur_key = "", None
        self.table.setItem(row, COLUMN_DURATION, _NumericItem(dur_text, dur_key))

        space_text = "" if entry.is_video else (entry.source_colourspace or "Unmanaged")
        self.table.setItem(row, COLUMN_COLORSPACE, QTableWidgetItem(space_text))
        self.table.setItem(row, COLUMN_PATH, QTableWidgetItem(entry.source_path or ""))

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
            thumbnail=thumb_descriptor)
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
            duration=duration, thumbnail=thumb_descriptor)
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
        entry = self._selected_entry()
        if entry is None:
            return
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
        entry = self._selected_entry()
        if entry is None:
            return
        reply = QMessageBox.question(
            self, "Delete Media",
            f'Delete "{entry.name}" from the library? This can\'t be undone.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
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
