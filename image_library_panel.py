"""
image_library_panel.py -- the "Image Library" panel (left column by
default, between Browser and LUT Library, per Tom -- see main_window.py's
_build_default_dock_layout; freely draggable elsewhere since the docking
migration, see RUNNING.md #40), a named shelf of reference images
ingested into session memory: Import...
decodes any image file Pillow supports and keeps the full decoded pixel
array itself (not just its file path) -- see image_library.py's docstring
on what that does and doesn't guarantee. Mirrors lut_library_panel.py's/
cdl_library_panel.py's "named shelf" pattern -- import/apply/rename/delete,
all in the "..." header menu -- but "apply" here means "load into the
Viewer's Sample or Reference slot" (two separate actions, see
sample_image_panel.py's docstring on those two roles) rather than "add a
Stack stage."

No drag-and-drop from the Browser (unlike LUT Library/CDL Library) --
BrowserPanel only ever lists LUT/CDL files, it has no notion of images, so
this panel's only ingestion path is its own Import... file dialog.

NOT TESTED IN THIS ENVIRONMENT -- same PySide6/libEGL wall as the rest of
the GUI layer. Please run main_window.py and confirm: (1) Import... on a
real image file adds an entry showing its pixel dimensions, (2) "Load as
Sample"/"Load as Reference" (and double-click, which does "Load as
Sample") route to the right slot in the Viewer and the preview updates,
(3) Rename/Delete work and ask before Delete, (4) an entry survives Save
Session -> Open Session on the same machine (see image_library.py's
docstring on why this is a same-session-plus-same-machine guarantee, not
the "can't ever go missing" one LUT/CDL Library entries have).
"""
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QListWidget, QListWidgetItem,
    QMessageBox, QInputDialog, QFileDialog, QToolButton, QMenu,
)

from app_logic import load_image_as_array
from image_library import ImageLibrary, ImageLibraryEntry

IMAGE_FILE_FILTER = (
    "Image files (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.exr)"
    ";;All files (*)"
)


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
        # Double-click defaults to "Load as Sample" -- the more common of
        # the two roles (every existing Import Image/Adapt/Export Still
        # workflow already assumes a Sample image), same "double-click is
        # the fast path for the common action" convention LUT Library
        # (Apply to Stack) and CDL Library (Apply to Stack) already use.
        self.list_widget.itemDoubleClicked.connect(self._on_load_as_sample_clicked)
        layout.addWidget(self.list_widget, stretch=1)

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
        panel's populate_menu already follows."""
        import_action = menu.addAction("Import...", self._on_import_clicked)
        import_action.setShortcut(QKeySequence("Ctrl+I"))
        import_action.setToolTip(
            "Decode an image file into this session -- its full pixel data "
            "is copied in, not just referenced (see this panel's own "
            "docstring for the one caveat: this guarantee doesn't survive "
            "a session save/reload if the source file has since moved).")
        load_as_sample_action = menu.addAction("Load as Sample", self._on_load_as_sample_clicked)
        load_as_sample_action.setShortcut(QKeySequence("Ctrl+L"))
        load_as_reference_action = menu.addAction("Load as Reference", self._on_load_as_reference_clicked)
        load_as_reference_action.setShortcut(QKeySequence("Ctrl+R"))
        menu.addSeparator()
        menu.addAction("Rename...", self._on_rename_clicked)
        menu.addAction("Delete", self._on_delete_clicked)

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
        space = entry.source_colourspace or "Unmanaged"
        return f"{entry.name}{provenance}\n  {w}x{h}, {space}"

    def _refresh_list(self, keep_selection=None):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for entry in self.library.entries:
            item = QListWidgetItem(self._entry_summary_text(entry))
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
        -- this is the one-time ingest a Sample/Reference slot later
        downsamples FROM as needed, see sample_image_panel.py's
        load_sample_from_library_entry/load_reference_from_library_entry,
        the same "ingest at full quality, downsample per-consumer" split
        Export Still already uses for the Sample image itself)."""
        try:
            array = load_image_as_array(path, max_dimension=None)
        except Exception as e:  # noqa: BLE001 -- surface any decode failure to the user
            QMessageBox.critical(self, "Failed to import image", str(e))
            return
        name = Path(path).stem
        entry = ImageLibraryEntry(
            name, array, source_filename=Path(path).name, source_path=str(path))
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
        as LUTLibraryPanel.load_library/CDLLibraryPanel.load_library."""
        self.library = library
        self._refresh_list()
