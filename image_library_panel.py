"""
image_library_panel.py -- the "Media Libraries" panel (left column by
default, between Browser and LUT Library, per Tom -- see main_window.py's
_build_default_dock_layout; freely draggable elsewhere since the docking
migration, see RUNNING.md #40), a named shelf of reference images
ingested into session memory: Import...
decodes any image file Pillow supports and keeps the full decoded pixel
array itself (not just its file path) -- see image_library.py's docstring
on what that does and doesn't guarantee. Mirrors lut_library_panel.py's
"named shelf" pattern, but "apply" here means "load into the
Viewer's Source or Reference slot" (two separate actions, see
sample_image_panel.py's docstring on those two roles) rather than "add a
Stack stage." Import... (panel-level, no selection needed) lives in the
"..." header menu; Load as Source/Load as Reference/Rename/Delete (all
per-item) live on a right-click context menu instead (per Tom, see
_on_context_menu_requested).

No drag-and-drop from the Browser (unlike LUT Library/CDL Library) --
BrowserPanel only ever lists LUT/CDL files, it has no notion of images, so
this panel's only ingestion path is its own Import... file dialog.

VIDEO ENTRIES (Media Library Phase 2, per Tom's "comprehensive media
library" request) -- Import... now also accepts video files (same
extension set as VIDEO_EXTENSIONS below), opened via video_source.
VideoSource just long enough to read its metadata and decode its first
frame (for the thumbnail/row preview -- see image_library.py's docstring
on why only that one frame is kept, not the whole clip), then closed
immediately; the real file is reopened fresh by whatever later consumes
it (see load_video's own already-existing open/close lifecycle). "Load
as Source" on a video entry hands off to sample_image_panel.
load_sample_from_library_entry's own video branch (opens the full video
with transport, not a frozen frame) -- "Load as Reference" stays
disabled for a selected video entry, since Reference has never supported
video and adding that is out of scope here.

NOT TESTED IN THIS ENVIRONMENT -- same PySide6/libEGL wall as the rest of
the GUI layer. Please run main_window.py and confirm: (1) Import... on a
real image file adds an entry showing its pixel dimensions, (2) right-
clicking a row shows "Load as Source"/"Load as Reference"/"Rename..."/
"Delete", each acting on that row (and double-click, which does "Load as
Source") route to the right slot in the Viewer and the preview updates,
(3) Rename/Delete work and ask before Delete, (4) an entry survives Save
Session -> Open Session on the same machine (see image_library.py's
docstring on why this is a same-session-plus-same-machine guarantee, not
the "can't ever go missing" one LUT/CDL Library entries have).
"""
from pathlib import Path

from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QListWidget, QListWidgetItem,
    QMessageBox, QInputDialog, QFileDialog, QToolButton, QMenu,
)

from app_logic import load_image_as_array
from image_library import ImageLibrary, ImageLibraryEntry
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


def _format_duration(seconds):
    seconds = max(0, round(seconds))
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}:{secs:02d}"


class ImageLibraryPanel(QWidget):
    load_as_sample_requested = Signal(object)      # an image_library.ImageLibraryEntry
    load_as_reference_requested = Signal(object)   # an image_library.ImageLibraryEntry

    def __init__(self, parent=None):
        super().__init__(parent)
        self.library = ImageLibrary()
        self._build_layout()
        self._refresh_list()

    def _build_layout(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)  # PanelFrame provides the outer gutter

        self.list_widget = QListWidget()
        # Double-click defaults to "Load as Source" -- the more common of
        # the two roles (every existing Import/Export Still workflow
        # already assumes a Source image), same "double-click is the fast
        # path for the common action" convention LUT Library (Apply to
        # Stack) and CDL Library (Apply to Stack) already use.
        self.list_widget.itemDoubleClicked.connect(self._on_load_as_sample_clicked)
        # Per-item actions (Load as Source/Load as Reference/Rename/
        # Delete) live on a right-click context menu, not the "..."
        # header menu (per Tom: "rename, load as source, delete and load
        # as ref should move to right clicking a particular clip") --
        # see _on_context_menu_requested. Only Import... (panel-level,
        # not tied to a selection) stays in populate_menu below.
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._on_context_menu_requested)
        layout.addWidget(self.list_widget, stretch=1)
        hover_inspector.register(self.list_widget, self._hover_info_for_item)

        self._build_menu_button()

    def _build_menu_button(self):
        """The Image Library panel's "..." header menu -- same treatment
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
        corner button above and MainWindow's top-level "Image Library"
        menu-bar menu, same redundant-on-purpose arrangement every other
        panel's populate_menu already follows. Only Import... lives here
        now -- Load as Source/Load as Reference/Rename/Delete moved to
        the right-click context menu (see _on_context_menu_requested),
        since they all need a selected row and don't make sense as a
        standing header-menu action the way Import... does."""
        import_action = menu.addAction("Import...", self._on_import_clicked)
        import_action.setShortcut(QKeySequence("Ctrl+I"))
        import_action.setToolTip(
            "Decode an image file into this session -- its full pixel data "
            "is copied in, not just referenced (see this panel's own "
            "docstring for the one caveat: this guarantee doesn't survive "
            "a session save/reload if the source file has since moved).")

    def _on_context_menu_requested(self, pos):
        """Right-click menu for a specific row (per Tom, see populate_menu's
        own docstring) -- Load as Source/Load as Reference/Rename/Delete.
        No menu at all when the click lands on empty space below the last
        row (`itemAt` returns None there), same as every other panel's
        list widget already leaves empty space unclickable."""
        item = self.list_widget.itemAt(pos)
        if item is None:
            return
        self.list_widget.setCurrentItem(item)
        menu = QMenu(self.list_widget)
        menu.addAction("Load as Source", self._on_load_as_sample_clicked)
        menu.addAction("Load as Reference", self._on_load_as_reference_clicked)
        menu.addSeparator()
        menu.addAction("Rename...", self._on_rename_clicked)
        menu.addAction("Delete", self._on_delete_clicked)
        menu.exec(self.list_widget.mapToGlobal(pos))

    # ---- list -----------------------------------------------------------

    def _selected_entry(self):
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self.library.entries):
            return None
        return self.library.entries[row]

    @staticmethod
    def _entry_summary_text(entry):
        h, w = entry.array.shape[:2] if entry.array is not None else (None, None)
        provenance = f"  [{entry.source_filename}]" if entry.source_filename else ""
        if entry.is_video:
            duration_badge = f", {_format_duration(entry.duration)}" if entry.duration else ""
            return f"{entry.name}{provenance}\n  {w}x{h}{duration_badge}, video"
        space = entry.source_colourspace or "Unmanaged"
        return f"{entry.name}{provenance}\n  {w}x{h}, {space}"

    def _hover_info_for_item(self, item):
        """Alt+hover info card content (see hover_inspector.py) -- adds
        the full source path on top of what _entry_summary_text already
        shows in the row itself (just the filename)."""
        row = self.list_widget.row(item)
        if not (0 <= row < len(self.library.entries)):
            return None
        entry = self.library.entries[row]
        lines = [entry.name]
        if entry.source_path:
            lines.append(entry.source_path)
        if entry.array is not None:
            h, w = entry.array.shape[:2]
            lines.append(f"{w}x{h}")
        if entry.is_video:
            lines.append(f"Duration: {_format_duration(entry.duration)}")
            if entry.frame_rate:
                start_tc = timecode.frames_to_timecode(0, entry.frame_rate)
                end_frame = max(0, (entry.frame_count or 1) - 1)
                end_tc = timecode.frames_to_timecode(end_frame, entry.frame_rate)
                lines.append(f"Start: {start_tc}  End: {end_tc}")
        else:
            lines.append(f"Color space: {entry.source_colourspace or 'Unmanaged'}")
        return "\n".join(lines)

    def _refresh_list(self, keep_selection=None):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for entry in self.library.entries:
            item = QListWidgetItem(self._entry_summary_text(entry))
            png_bytes = media_cache.load_thumbnail(entry.thumbnail) if entry.thumbnail else None
            if png_bytes:
                pixmap = QPixmap()
                if pixmap.loadFromData(png_bytes, "PNG"):
                    item.setIcon(pixmap)
            self.list_widget.addItem(item)
        if keep_selection is not None and 0 <= keep_selection < len(self.library.entries):
            self.list_widget.setCurrentRow(keep_selection)
        self.list_widget.blockSignals(False)

    # ---- import (ingest a real file) -------------------------------------

    def _on_import_clicked(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import Image into Library", "", IMAGE_FILE_FILTER)
        if not path:
            return
        self.import_from_path(path)

    def import_from_path(self, path):
        """Decodes `path` at full native resolution (no preview downsample
        -- this is the one-time ingest a Source/Reference slot later
        downsamples FROM as needed, see sample_image_panel.py's
        load_sample_from_library_entry/load_reference_from_library_entry,
        the same "ingest at full quality, downsample per-consumer" split
        Export Still already uses for the Source image itself). Dispatches
        to a still or video import based on `path`'s extension (see
        VIDEO_EXTENSIONS/module docstring)."""
        if Path(path).suffix.lower() in VIDEO_EXTENSIONS:
            self._import_video_from_path(path)
            return
        try:
            array = load_image_as_array(path, max_dimension=None)
        except Exception as e:  # noqa: BLE001 -- surface any decode failure to the user
            QMessageBox.critical(self, "Failed to import image", str(e))
            return
        name = Path(path).stem
        thumb_descriptor = media_cache.store_thumbnail(
            f"image-library-{name}", thumbnail.make_thumbnail_png_bytes(array))
        entry = ImageLibraryEntry(
            name, array, source_filename=Path(path).name, source_path=str(path),
            media_kind="still", thumbnail=thumb_descriptor)
        self.library.add_entry(entry)
        self._refresh_list(keep_selection=len(self.library.entries) - 1)

    def _import_video_from_path(self, path):
        """Opens `path` just long enough to read its metadata and decode
        its first frame (module docstring) -- always closed again before
        returning, whether or not the import succeeds."""
        try:
            source = video_source.VideoSource(path)
        except Exception as e:  # noqa: BLE001 -- surface any open/decode failure to the user
            QMessageBox.critical(self, "Failed to import video", str(e))
            return
        try:
            first_frame = source.decode_next_frame()
            frame_count, frame_rate, duration = source.frame_count, source.frame_rate, source.duration
        finally:
            source.close()
        name = Path(path).stem
        thumb_descriptor = None
        if first_frame is not None:
            thumb_descriptor = media_cache.store_thumbnail(
                f"image-library-{name}", thumbnail.make_thumbnail_png_bytes(first_frame))
        entry = ImageLibraryEntry(
            name, first_frame, source_filename=Path(path).name, source_path=str(path),
            media_kind="video", frame_count=frame_count, frame_rate=frame_rate,
            duration=duration, thumbnail=thumb_descriptor)
        self.library.add_entry(entry)
        self._refresh_list(keep_selection=len(self.library.entries) - 1)

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

    def _on_rename_clicked(self):
        entry = self._selected_entry()
        if entry is None:
            return
        name, ok = QInputDialog.getText(self, "Rename Image", "Name:", text=entry.name)
        name = name.strip()
        if not ok or not name:
            return
        self.library.rename(entry.id, name)
        self._refresh_list(keep_selection=self.list_widget.currentRow())

    def _on_delete_clicked(self):
        entry = self._selected_entry()
        if entry is None:
            return
        reply = QMessageBox.question(
            self, "Delete Image",
            f'Delete "{entry.name}" from the library? This can\'t be undone.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.library.remove(entry.id)
        self._refresh_list()

    # ---- session save/load (see session_io.py) ---------------------------

    def get_library(self):
        return self.library

    def load_library(self, library):
        """Replace the whole library wholesale -- used by MainWindow for
        session load, same "wholesale replace + rebuild the list" pattern
        as LUTLibraryPanel.load_library/StackMemoriesPanel.load_library."""
        self.library = library
        self._refresh_list()
