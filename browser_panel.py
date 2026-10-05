"""
browser_panel.py -- the file-system Browser (Tom, 2026-10-05: "the
browser should be a proper fs browser with a recursive list option for
drilling into sub folders. you should be able to drag and drop from that
panel into the media library").

Layout: a folder tree (QFileSystemModel, folders only) rooted at a
chosen folder (Choose Root..., default: home), and below it the
recognized files in the selected folder -- media (stills and video), LUTs
and ASC CDLs -- with Name / Type / Folder / Size / Modified columns.
"Include subfolders" lists the selected folder's whole subtree instead
(the Folder column then shows where under it each file lives). A type
filter and the name search (wildcards ok) narrow the list.

Dragging out: any selection drags as standard file URLs (text/uri-list),
which the Media Libraries panel imports (see media_panel.py -- the same
drop also accepts files from the desktop file manager). A single LUT or
CDL ALSO carries LUT_PATH_MIME / CDL_PATH_MIME, the payloads the Stack,
LUT Library and CDL Library drop targets already understand.

Double-click: a LUT goes to the Stack (file_selected), a CDL to the CDL
Library (cdl_file_selected), a media file into the current Media Library
(media_files_activated).

The subfolder listing walks the tree itself (os.walk, hidden folders
skipped) and stops at MAX_LISTED files so pointing it at a huge tree
can't freeze the UI; the status line says when it was cut short.
"""
import fnmatch
import os
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, QDir, QMimeData, QUrl
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QToolButton, QLabel,
    QFileDialog, QTreeView, QFileSystemModel, QTreeWidget, QTreeWidgetItem,
    QCheckBox, QComboBox, QSplitter, QAbstractItemView, QHeaderView,
)

from app_logic import SUPPORTED_EXTENSIONS as LUT_EXTENSIONS
from cdl_file_io import CDL_EXTENSIONS
import hover_inspector

# Custom MIME types carrying just the dragged file's full path -- the
# Stack/LUT Library (LUTs) and CDL Library (CDLs) drop targets check
# hasFormat() for the one kind each understands. Media drops use plain
# file URLs instead (see module docstring).
LUT_PATH_MIME = "application/x-lutier-lut-path"
CDL_PATH_MIME = "application/x-lutier-cdl-path"

# Media extensions -- the same lists the Media Libraries panel imports
# (media_panel.py re-exports these; defined here so this module doesn't
# import that panel).
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".exr")
# .mxf: camera-original ProRes/DNxHR (e.g. ARRI ALEXA Mini LF ProRes 4444).
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mxf", ".mkv", ".avi", ".m4v")

KIND_LUT, KIND_CDL, KIND_STILL, KIND_VIDEO = "lut", "cdl", "still", "video"
KIND_LABELS = {KIND_LUT: "LUT", KIND_CDL: "CDL", KIND_STILL: "Still", KIND_VIDEO: "Video"}

# Type filter choices: label -> the kinds it shows.
TYPE_FILTERS = (
    ("All", (KIND_STILL, KIND_VIDEO, KIND_LUT, KIND_CDL)),
    ("Media", (KIND_STILL, KIND_VIDEO)),
    ("LUTs & CDLs", (KIND_LUT, KIND_CDL)),
)

# Debounces the search box -- every keystroke used to trigger its own
# full rescan (a real, previously-shipped performance bug).
SEARCH_DEBOUNCE_MS = 200

# Cap on files listed by one scan (see module docstring).
MAX_LISTED = 5000

COLUMN_NAME, COLUMN_TYPE, COLUMN_FOLDER, COLUMN_SIZE, COLUMN_MODIFIED = range(5)
_ROLE_PATH = Qt.ItemDataRole.UserRole
_ROLE_KIND = Qt.ItemDataRole.UserRole + 1
_ROLE_SORT = Qt.ItemDataRole.UserRole + 2


def kind_for_path(path):
    """KIND_* for a recognized file extension, else None."""
    ext = os.path.splitext(str(path))[1].lower()
    if ext in VIDEO_EXTENSIONS:
        return KIND_VIDEO
    if ext in IMAGE_EXTENSIONS:
        return KIND_STILL
    if ext in CDL_EXTENSIONS:
        return KIND_CDL
    if ext in LUT_EXTENSIONS:
        return KIND_LUT
    return None


def _name_matches(name, name_filter):
    """Wildcards (* ? [) match the whole name; plain text is a substring
    match -- both case-insensitive."""
    name, name_filter = name.lower(), name_filter.lower()
    if any(c in name_filter for c in "*?["):
        return fnmatch.fnmatch(name, name_filter)
    return name_filter in name


def scan_folder(folder, recursive=False, name_filter=None, kinds=None, limit=MAX_LISTED):
    """[(Path, kind)] for recognized files in `folder` (its whole subtree
    when `recursive`, hidden folders skipped), sorted by name, plus
    whether `limit` cut the scan short. Unreadable subfolders are
    skipped; an unreadable `folder` itself raises OSError."""
    folder = Path(folder)
    kinds = set(kinds) if kinds else None
    results, truncated = [], False

    def consider(directory, name):
        nonlocal truncated
        kind = kind_for_path(name)
        if kind is None or (kinds and kind not in kinds):
            return True
        if name_filter and not _name_matches(name, name_filter):
            return True
        if len(results) >= limit:
            truncated = True
            return False
        results.append((Path(directory) / name, kind))
        return True

    if recursive:
        for directory, subdirs, files in os.walk(folder, onerror=None):
            subdirs[:] = sorted(d for d in subdirs if not d.startswith("."))
            if not all(consider(directory, name) for name in files):
                break
    else:
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.is_file() and not consider(folder, entry.name):
                    break
    results.sort(key=lambda pair: pair[0].name.lower())
    return results, truncated


def _format_size(num_bytes):
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


class _FileItem(QTreeWidgetItem):
    """Sorts Size/Modified numerically (by _ROLE_SORT), the rest by text."""

    def __lt__(self, other):
        column = self.treeWidget().sortColumn() if self.treeWidget() else COLUMN_NAME
        mine, theirs = self.data(column, _ROLE_SORT), other.data(column, _ROLE_SORT)
        if mine is not None and theirs is not None:
            return mine < theirs
        return self.text(column).lower() < other.text(column).lower()


class _FileList(QTreeWidget):
    """Drags its selection out as file URLs, plus LUT_PATH_MIME /
    CDL_PATH_MIME for a single LUT/CDL (see module docstring)."""

    def mimeTypes(self):
        return ["text/uri-list", LUT_PATH_MIME, CDL_PATH_MIME]

    def mimeData(self, items):
        paths = [it.data(COLUMN_NAME, _ROLE_PATH) for it in items if it.data(COLUMN_NAME, _ROLE_PATH)]
        if not paths:
            return None
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        if len(items) == 1:
            kind = items[0].data(COLUMN_NAME, _ROLE_KIND)
            if kind == KIND_LUT:
                mime.setData(LUT_PATH_MIME, paths[0].encode("utf-8"))
            elif kind == KIND_CDL:
                mime.setData(CDL_PATH_MIME, paths[0].encode("utf-8"))
        return mime


class BrowserPanel(QWidget):
    file_selected = Signal(str)           # a LUT was double-clicked -> Stack
    cdl_file_selected = Signal(str)       # a CDL was double-clicked -> CDL Library
    media_files_activated = Signal(list)  # media double-clicked -> current Media Library

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_folder = None
        self._search_debounce_timer = QTimer(self)
        self._search_debounce_timer.setSingleShot(True)
        self._search_debounce_timer.timeout.connect(self.refresh)
        self._build_layout()
        self.set_root(QDir.homePath())

    def _build_layout(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)  # PanelFrame provides the outer gutter

        root_row = QHBoxLayout()
        self.root_label = QLabel()
        self.root_label.setMinimumWidth(1)
        root_row.addWidget(self.root_label, stretch=1)
        up_button = QToolButton()
        up_button.setObjectName("chooseFolderButton")
        up_button.setAutoRaise(True)
        up_button.setText("↑")
        up_button.setToolTip("Root the folder tree one level up")
        up_button.clicked.connect(self._on_root_up_clicked)
        root_row.addWidget(up_button)
        browse_button = QToolButton()
        browse_button.setObjectName("chooseFolderButton")
        browse_button.setAutoRaise(True)
        browse_button.setText("Choose Root...")
        browse_button.clicked.connect(self.choose_folder)
        root_row.addWidget(browse_button)
        layout.addLayout(root_row)

        splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(splitter, stretch=1)

        self.fs_model = QFileSystemModel(self)
        self.fs_model.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot)
        self.folder_tree = QTreeView()
        self.folder_tree.setModel(self.fs_model)
        self.folder_tree.setHeaderHidden(True)
        for column in range(1, self.fs_model.columnCount()):
            self.folder_tree.hideColumn(column)  # name only -- no size/type/date for folders
        self.folder_tree.selectionModel().currentChanged.connect(self._on_folder_changed)
        splitter.addWidget(self.folder_tree)

        files = QWidget()
        files_layout = QVBoxLayout(files)
        files_layout.setContentsMargins(0, 0, 0, 0)
        filter_row = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search by name... (wildcards ok: *.cube, A001*)")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._on_search_text_changed)
        filter_row.addWidget(self.search_box, stretch=1)
        self.type_combo = QComboBox()
        for label, _kinds in TYPE_FILTERS:
            self.type_combo.addItem(label)
        self.type_combo.currentIndexChanged.connect(lambda _i: self.refresh())
        filter_row.addWidget(self.type_combo)
        files_layout.addLayout(filter_row)
        self.recursive_checkbox = QCheckBox("Include subfolders")
        self.recursive_checkbox.setToolTip(
            "List every recognized file under the selected folder, not just "
            "the ones directly in it")
        self.recursive_checkbox.toggled.connect(lambda _on: self.refresh())
        files_layout.addWidget(self.recursive_checkbox)

        self.file_list = _FileList()
        self.file_list.setColumnCount(5)
        self.file_list.setHeaderLabels(["Name", "Type", "Folder", "Size", "Modified"])
        self.file_list.setRootIsDecorated(False)
        self.file_list.setSortingEnabled(True)
        self.file_list.sortByColumn(COLUMN_NAME, Qt.SortOrder.AscendingOrder)
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.setDragEnabled(True)
        self.file_list.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        # Name gets the room; the rest size to their contents (Folder is
        # user-sizable since nested paths can be long). The panel usually
        # lives in a narrow left column.
        header = self.file_list.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(COLUMN_NAME, QHeaderView.ResizeMode.Stretch)
        for column in (COLUMN_TYPE, COLUMN_SIZE, COLUMN_MODIFIED):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COLUMN_FOLDER, QHeaderView.ResizeMode.Interactive)
        self.file_list.setColumnWidth(COLUMN_FOLDER, 80)
        self.file_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        files_layout.addWidget(self.file_list, stretch=1)
        hover_inspector.register(self.file_list, self._hover_info_for_item)
        splitter.addWidget(files)
        splitter.setSizes([200, 300])

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

    # ---- folder tree ------------------------------------------------------

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose Browser root folder", self._root or "")
        if folder:
            self.set_root(folder)
            self.set_folder(folder)

    def set_root(self, folder):
        """Root the folder tree at `folder` (it doesn't change the folder
        whose files are listed -- see set_folder)."""
        self._root = str(folder)
        index = self.fs_model.setRootPath(self._root)
        self.folder_tree.setRootIndex(index)
        self.root_label.setText(os.path.basename(self._root.rstrip(os.sep)) or self._root)
        self.root_label.setToolTip(self._root)

    def _on_root_up_clicked(self):
        parent = os.path.dirname(self._root.rstrip(os.sep)) or os.sep
        if parent != self._root:
            self.set_root(parent)

    def set_folder(self, folder):
        """List `folder`'s files and select it in the tree (re-rooting the
        tree if it's outside the current root)."""
        folder = str(folder)
        if not (folder + os.sep).startswith(self._root.rstrip(os.sep) + os.sep):
            self.set_root(folder)
        self._current_folder = folder
        index = self.fs_model.index(folder)
        if index.isValid():
            self.folder_tree.selectionModel().blockSignals(True)
            self.folder_tree.setCurrentIndex(index)
            self.folder_tree.selectionModel().blockSignals(False)
            self.folder_tree.scrollTo(index)
        # Immediate, not debounced -- picking a folder is one deliberate
        # action, and any pending search debounce is for the old folder.
        self._search_debounce_timer.stop()
        self.refresh()

    def _on_folder_changed(self, current, _previous):
        path = self.fs_model.filePath(current)
        if path and path != self._current_folder:
            self._current_folder = path
            self._search_debounce_timer.stop()
            self.refresh()

    def current_folder(self):
        return self._current_folder

    # ---- file list ----------------------------------------------------------

    def _on_search_text_changed(self, _text):
        self._search_debounce_timer.start(SEARCH_DEBOUNCE_MS)

    def refresh(self):
        self.file_list.clear()
        if not self._current_folder:
            self.status_label.setText("")
            return
        recursive = self.recursive_checkbox.isChecked()
        try:
            results, truncated = scan_folder(
                self._current_folder, recursive=recursive,
                name_filter=self.search_box.text().strip() or None,
                kinds=TYPE_FILTERS[self.type_combo.currentIndex()][1])
        except OSError as e:
            self.status_label.setText(f"Error reading folder: {e}")
            return
        self.file_list.setSortingEnabled(False)
        counts = {}
        for path, kind in results:
            counts[kind] = counts.get(kind, 0) + 1
            self.file_list.addTopLevelItem(self._make_item(path, kind))
        self.file_list.setSortingEnabled(True)
        self.file_list.setColumnHidden(COLUMN_FOLDER, not recursive)
        parts = [f"{counts[k]} {KIND_LABELS[k]}{'s' if counts[k] != 1 else ''}"
                 for k in (KIND_VIDEO, KIND_STILL, KIND_LUT, KIND_CDL) if counts.get(k)]
        text = ", ".join(parts) if parts else "No recognized files"
        if truncated:
            text += f" (first {MAX_LISTED} shown)"
        self.status_label.setText(text)

    def _make_item(self, path, kind):
        item = _FileItem()
        item.setText(COLUMN_NAME, path.name)
        item.setData(COLUMN_NAME, _ROLE_PATH, str(path))
        item.setData(COLUMN_NAME, _ROLE_KIND, kind)
        item.setToolTip(COLUMN_NAME, str(path))
        item.setText(COLUMN_TYPE, KIND_LABELS[kind])
        relative = os.path.relpath(path.parent, self._current_folder)
        item.setText(COLUMN_FOLDER, "" if relative == "." else relative)
        try:
            stat = path.stat()
        except OSError:
            return item
        item.setText(COLUMN_SIZE, _format_size(stat.st_size))
        item.setData(COLUMN_SIZE, _ROLE_SORT, stat.st_size)
        item.setText(COLUMN_MODIFIED, datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"))
        item.setData(COLUMN_MODIFIED, _ROLE_SORT, stat.st_mtime)
        return item

    def listed_paths(self):
        """Every listed file's path, in the list's current order."""
        return [self.file_list.topLevelItem(i).data(COLUMN_NAME, _ROLE_PATH)
                for i in range(self.file_list.topLevelItemCount())]

    def _hover_info_for_item(self, item):
        """Alt+hover info card (see hover_inspector.py): name, full path,
        kind, and a live stat() of the file."""
        path = item.data(COLUMN_NAME, _ROLE_PATH)
        if not path:
            return None
        lines = [os.path.basename(path), path, KIND_LABELS.get(item.data(COLUMN_NAME, _ROLE_KIND), "")]
        try:
            stat = os.stat(path)
        except OSError:
            return "\n".join(lines)
        modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        lines.append(f"{_format_size(stat.st_size)} -- modified {modified}")
        return "\n".join(lines)

    def _on_item_double_clicked(self, item, _column=0):
        path = item.data(COLUMN_NAME, _ROLE_PATH)
        if not path:
            return
        kind = item.data(COLUMN_NAME, _ROLE_KIND)
        if kind == KIND_CDL:
            self.cdl_file_selected.emit(path)
        elif kind == KIND_LUT:
            self.file_selected.emit(path)
        else:
            self.media_files_activated.emit([path])
