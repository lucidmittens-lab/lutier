"""
browser_panel.py -- shared-folder file browser: pick a folder, search by
name, see recognized LUT AND ASC CDL files together, pick one to load or
drag it out. All the actual scanning logic lives in app_logic.py; this
file is just the widget.

LUT files and CDL files (.cc/.cdl/.ccc, per Tom) are scanned together in
one list (see app_logic.scan_lut_folder's extra_extensions param, fed
cdl_file_io.CDL_EXTENSIONS here) -- each item remembers its own KIND
(_KIND_LUT/_KIND_CDL, stored as UserRole+1) so double-click and drag-out
can route a file to wherever that kind actually belongs: a LUT double-
click/drag still goes straight to the Stack (unchanged, its own working
area), a CDL double-click/drag goes to the CDL Library (there's no
"just apply this file directly" concept for a CDL the way there is for a
LUT -- a Stack CDL stage is built from parameters, not loaded from a
file, so the Library is the right landing spot). CDL items get a visible
"[CDL]" suffix in the list so a mixed folder doesn't look like all one
kind of file.

The folder path label elides (truncates with "...") to fit the panel's
current width instead of wrapping to multiple lines -- the panel lives in
a narrow left-column splitter pane (see main_window.py), where a long
folder path wrapped to 3-4 lines was eating most of the panel's vertical
space just to display a path nobody needs to read in full; the full path
is still available as a tooltip on hover.

This environment now has PySide6 installed plus a stub libEGL.so.1 (see
.eglstub/ in the outputs folder) that lets QApplication actually
construct under QT_QPA_PLATFORM=offscreen -- MainWindow builds headless
and the drag-out-to-Stack path (_DraggableFileListWidget.mimeData /
LUT_PATH_MIME) has been exercised via a manually built QDropEvent on the
receiving end, see stack_panel.py's docstring. Still worth a real
on-screen pass for actual mouse-driven drag ergonomics, which offscreen
can't meaningfully exercise.
"""
import os
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QListWidget,
    QListWidgetItem, QToolButton, QLabel, QFileDialog,
)

from app_logic import scan_lut_folder
from cdl_file_io import CDL_EXTENSIONS
import hover_inspector

# Custom MIME types carrying just the dragged file's full path -- used so
# a drop target (Stack panel for LUTs, the Gallery for CDLs) can tell "a
# file got dragged in from the Browser" apart from its own internal
# drags, and pull the path back out without depending on QListWidget's
# default model-index MIME payload (which doesn't survive across two
# unrelated QListWidgets anyway). Two distinct MIME types (not one with a
# type tag inside) so each drop target's dragEnterEvent can just check
# hasFormat() for the one kind it actually understands and ignore the
# other automatically, no shared parsing needed.
LUT_PATH_MIME = "application/x-lutier-lut-path"
CDL_PATH_MIME = "application/x-lutier-cdl-path"

_KIND_LUT = "lut"
_KIND_CDL = "cdl"
_ROLE_KIND = Qt.ItemDataRole.UserRole + 1

# Debounces the search box's own textChanged -- a real, previously-
# shipped performance bug (found in code review): every keystroke
# triggered its own full recursive scan_lut_folder(..., recursive=True)
# filesystem walk, with no debounce at all, so typing a multi-character
# search term meant one full folder re-scan per character. Same "wait
# for typing to actually pause" idea as sample_image_panel.py's own
# EDIT_SETTLE_DEBOUNCE_MS, just for keystrokes instead of slider ticks.
SEARCH_DEBOUNCE_MS = 200


class _DraggableFileListWidget(QListWidget):
    """QListWidget whose items drag out as LUT_PATH_MIME or CDL_PATH_MIME
    (the file's full path, tagged by that item's own kind) rather than
    Qt's default internal model-index payload, so a drop target outside
    this widget can actually use it."""

    def mimeData(self, items):
        data = super().mimeData(items)
        paths = [it.data(Qt.ItemDataRole.UserRole) for it in items
                 if it.data(Qt.ItemDataRole.UserRole)]
        if not paths:
            return data
        path = paths[0]
        kind = items[0].data(_ROLE_KIND)
        mime_type = CDL_PATH_MIME if kind == _KIND_CDL else LUT_PATH_MIME
        data.setData(mime_type, path.encode("utf-8"))
        return data


class BrowserPanel(QWidget):
    """Emits file_selected(str) with the full path whenever the user picks a
    LUT from the list, cdl_file_selected(str) when a CDL file (.cc/.cdl/
    .ccc) is picked instead (routed to the Gallery, see this module's
    docstring)."""

    file_selected = Signal(str)
    cdl_file_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_folder = None
        self._search_debounce_timer = QTimer(self)
        self._search_debounce_timer.setSingleShot(True)
        self._search_debounce_timer.timeout.connect(self.refresh)
        self._build_layout()

    def _build_layout(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)  # PanelFrame provides the outer gutter

        folder_row = QHBoxLayout()
        self.folder_label = QLabel("No folder selected")
        folder_row.addWidget(self.folder_label, stretch=1)
        browse_button = QToolButton()
        browse_button.setObjectName("chooseFolderButton")
        browse_button.setAutoRaise(True)
        browse_button.setText("Choose Folder...")
        browse_button.clicked.connect(self.choose_folder)
        folder_row.addWidget(browse_button)
        layout.addLayout(folder_row)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search by name... (wildcards ok: *.cube, K_0??_*)")
        self.search_box.textChanged.connect(self._on_search_text_changed)
        layout.addWidget(self.search_box)

        self.list_widget = _DraggableFileListWidget()
        # Drag a LUT straight onto the Stack panel as an alternative to
        # double-clicking -- see LUT_PATH_MIME / _DraggableFileListWidget
        # above and stack_panel.py's _StackListWidget on the receiving end.
        self.list_widget.setDragEnabled(True)
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.list_widget, stretch=1)
        hover_inspector.register(self.list_widget, self._hover_info_for_item)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose LUT folder")
        if folder:
            self.set_folder(folder)

    def set_folder(self, folder):
        self._current_folder = folder
        self._update_folder_label_text()
        # Immediate, not debounced -- picking a folder is a single
        # deliberate action, not a per-keystroke event, and any pending
        # search-box debounce for the OLD folder is now stale.
        self._search_debounce_timer.stop()
        self.refresh()

    def _on_search_text_changed(self, _text):
        self._search_debounce_timer.start(SEARCH_DEBOUNCE_MS)

    def _update_folder_label_text(self):
        """Elide the folder path to fit the label's current width instead
        of wrapping -- re-run on every resize (see resizeEvent below)
        since the available width changes as the panel/splitter does."""
        if not self._current_folder:
            self.folder_label.setText("No folder selected")
            self.folder_label.setToolTip("")
            return
        metrics = QFontMetrics(self.folder_label.font())
        elided = metrics.elidedText(
            self._current_folder, Qt.TextElideMode.ElideMiddle, self.folder_label.width())
        self.folder_label.setText(elided)
        self.folder_label.setToolTip(self._current_folder)  # full path on hover

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_folder_label_text()

    def refresh(self):
        self.list_widget.clear()
        if not self._current_folder:
            self.status_label.setText("")
            return
        try:
            results = scan_lut_folder(
                self._current_folder, recursive=True,
                name_filter=self.search_box.text().strip() or None,
                extra_extensions=CDL_EXTENSIONS,
            )
        except OSError as e:
            self.status_label.setText(f"Error reading folder: {e}")
            return

        cdl_count = 0
        for path in results:
            is_cdl = path.suffix.lower() in CDL_EXTENSIONS
            if is_cdl:
                cdl_count += 1
            # CDL files get a visible "[CDL]" suffix -- this list now mixes
            # two kinds of file, and unlike a bare ".cc" extension (easy to
            # miss at a glance in a narrow left-column list), this makes
            # the distinction obvious without needing a separate icon set.
            label = f"{path.name}  [CDL]" if is_cdl else path.name
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setData(_ROLE_KIND, _KIND_CDL if is_cdl else _KIND_LUT)
            item.setToolTip(str(path))
            self.list_widget.addItem(item)

        lut_count = len(results) - cdl_count
        count_text = f"{lut_count} LUT file{'s' if lut_count != 1 else ''}"
        if cdl_count:
            count_text += f", {cdl_count} CDL file{'s' if cdl_count != 1 else ''}"
        self.status_label.setText(count_text)

    def _hover_info_for_item(self, item):
        """Alt+hover info card content (see hover_inspector.py) -- the
        full path (the row itself only shows the filename, elided for
        CDLs with a "[CDL]" suffix) plus a live stat() of the actual file
        on disk (size, last modified) -- these entries aren't ingested
        into any in-memory library the way Image/LUT/CDL Library rows
        are, so there's no already-loaded metadata to show; re-reading
        the file's own stat is cheap and always current."""
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return None
        kind = "CDL" if item.data(_ROLE_KIND) == _KIND_CDL else "LUT"
        lines = [os.path.basename(path), path, kind]
        try:
            st = os.stat(path)
        except OSError:
            return "\n".join(lines)
        size = st.st_size
        if size < 1024:
            size_text = f"{size} B"
        elif size < 1024 * 1024:
            size_text = f"{size / 1024:.1f} KB"
        else:
            size_text = f"{size / (1024 * 1024):.1f} MB"
        modified = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
        lines.append(f"{size_text} -- modified {modified}")
        return "\n".join(lines)

    def _on_item_double_clicked(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        if item.data(_ROLE_KIND) == _KIND_CDL:
            self.cdl_file_selected.emit(path)
        else:
            self.file_selected.emit(path)
