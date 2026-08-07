"""
lut_library_panel.py -- the "LUT Library" panel (left column by default,
between Browser and CDL Library per Tom -- see main_window.py's
_build_default_dock_layout; freely draggable elsewhere since the docking
migration, see RUNNING.md #40), a named shelf of LUTs ingested into
session memory: Import... reads any
format app_logic.load_lut_any_format supports and keeps the loaded LUT
object itself (table/domain/name), not just its file path -- see
lut_library.py's docstring on why that's the whole point ("can't go
offline if the session is transferred"). Mirrors cdl_library_panel.py's
"named shelf" pattern -- save/apply/rename/delete, persisted as part of
the session (session_io.py) -- but each entry is a whole LUT rather than
a handful of SOP/SAT floats.

Two equivalent ways to ingest a file, both funneling into
import_from_path(): the "..." header menu's Import... action (file
dialog), or dragging a LUT file in from the Browser panel
(_LUTDropListWidget below, browser_panel.LUT_PATH_MIME) -- a LUT
double-click in the Browser still goes straight to the Stack, unchanged,
same as always (see browser_panel.py's docstring on why LUT and CDL files
route differently). A third entry point, import_lut_object(), skips the
file read entirely for a LUT object MainWindow already has baked in
memory -- used by the Export dialog's "Also add to LUT Library" checkbox
(per Tom), see that method's docstring for why it doesn't just call
import_from_path() on the just-written export file.

"Apply to Stack" adds a new Stack stage straight from the entry's
in-memory LUT (see stack_panel.add_lut_stage_from_library_entry) -- no
disk read at all, and that stage is itself tagged so session_io.py
embeds its table directly too (source_kind="embedded"), independent of
whether this library entry still exists later.

Import.../Apply to Stack/Rename.../Delete all live in the "..." header
menu now (see _build_menu_button, corner_widget on PanelFrame in
main_window.py) rather than always-visible button rows, per Tom -- same
treatment Stack's own header menu already got. Double-clicking a list
entry (Apply to Stack) and dragging a LUT file in from the Browser
(Import) both still work independent of this menu.

NOT TESTED IN THIS ENVIRONMENT -- same PySide6/libEGL wall as the rest of
the GUI layer. Please run main_window.py and confirm: (1) the "..." menu's
Import... on a real LUT file adds an entry showing its dimensionality/
size, (2) dragging a LUT file from the Browser onto this panel does the
same import, (3) double-clicking or Apply to Stack adds a new LUT stage
using the embedded LUT -- try renaming/moving/deleting the source file
afterward and confirm the Stack stage (and the library entry) are
unaffected, (4) Rename/Delete work and ask before Delete, (5) entries --
and any Stack stage built from one -- survive Save Session -> Open
Session even opened on a machine that never had the original file,
(6) checking "Also add to LUT Library" in File > Export... (see
main_window.py's export_current_lut) adds an entry named after the
exported file, and that entry's table matches what was actually written
to disk.
"""
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QListWidget, QListWidgetItem,
    QMessageBox, QInputDialog, QFileDialog, QToolButton, QMenu,
)

from app_logic import load_lut_any_format, SUPPORTED_EXTENSIONS
from lut_library import LUTLibrary, LUTLibraryEntry
from browser_panel import LUT_PATH_MIME

# Ingesting into the library is a one-time, keep-forever read (per Tom's
# "can't go offline" requirement) -- bake CLF chains at export-quality
# resolution (65) rather than app_logic's interactive-preview default
# (33). Native single-LUT formats (.cube etc.) already load at their own
# real resolution regardless of this value.
LIBRARY_BAKE_SIZE = 65

LUT_FILE_FILTER = (
    "LUT files (" + " ".join(f"*{ext}" for ext in sorted(SUPPORTED_EXTENSIONS)) + ")"
    ";;All files (*)"
)


class _LUTDropListWidget(QListWidget):
    """QListWidget that also accepts a LUT file dragged in from the
    Browser panel (see browser_panel.py's LUT_PATH_MIME/
    _DraggableFileListWidget) -- drops straight through to the panel's
    own import_from_path, same import path the Import... menu action
    uses, so both entry points behave identically."""

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self._panel = panel
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(LUT_PATH_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(LUT_PATH_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        mime = event.mimeData()
        if not mime.hasFormat(LUT_PATH_MIME):
            event.ignore()
            return
        path = bytes(mime.data(LUT_PATH_MIME)).decode("utf-8")
        event.acceptProposedAction()
        self._panel.import_from_path(path)


class LUTLibraryPanel(QWidget):
    apply_requested = Signal(object)  # a lut_library.LUTLibraryEntry

    def __init__(self, parent=None):
        super().__init__(parent)
        self.library = LUTLibrary()
        self._build_layout()
        self._refresh_list()

    def _build_layout(self):
        layout = QVBoxLayout(self)

        self.list_widget = _LUTDropListWidget(self)
        self.list_widget.itemDoubleClicked.connect(self._on_apply_clicked)
        layout.addWidget(self.list_widget, stretch=1)

        self._build_menu_button()

    def _build_menu_button(self):
        """The LUT Library panel's "..." header menu (see corner_widget on
        PanelFrame, main_window.py) -- replaces the old always-visible
        Import.../Apply to Stack/Rename.../Delete button rows with menu
        actions instead, per Tom, same treatment Stack's and CDL
        Library's own header menus already got."""
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
        corner button above and MainWindow's top-level "LUT Library"
        menu-bar menu (per Tom, making the "..." menu redundant rather
        than the only way in), so both stay in sync automatically since
        they're wired to the exact same methods."""
        import_action = menu.addAction("Import...", self._on_import_clicked)
        # No shortcut -- Tom asked for "Alt+I", but on macOS Option+letter
        # often composes an accented/special character in a focused text
        # field (Option+I is literally the circumflex dead-key) -- a
        # QAction shortcut bound to it would intercept that instead of
        # letting it reach the field. Flagged back to Tom; per his call,
        # skipped rather than reworked -- see RUNNING.md #47.
        import_action.setToolTip(
            "Ingest a LUT file into this session -- its full table is copied "
            "in, not just referenced, so it can't go missing if the session "
            "moves to another machine.")
        apply_action = menu.addAction("Apply to Stack", self._on_apply_clicked)
        # Same "Alt+A" hold as Import... above.
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
        table = getattr(entry.lut, "table", None)
        is_3d = table is not None and table.ndim == 4
        dims = "3D" if is_3d else "1D"
        size = table.shape[0] if table is not None else "?"
        provenance = f"  [{entry.source_filename}]" if entry.source_filename else ""
        return f"{entry.name}{provenance}\n  {dims} LUT, size {size}"

    def _refresh_list(self, keep_selection=None):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for entry in self.library.entries:
            item = QListWidgetItem(self._entry_summary_text(entry))
            self.list_widget.addItem(item)
        if keep_selection is not None and 0 <= keep_selection < len(self.library.entries):
            self.list_widget.setCurrentRow(keep_selection)
        self.list_widget.blockSignals(False)

    # ---- import (ingest a real file) -----------------------------------

    def _on_import_clicked(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import LUT into Library", "", LUT_FILE_FILTER)
        if not path:
            return
        self.import_from_path(path)

    def import_from_path(self, path):
        """Shared by the Import... menu action (file dialog above) and a
        LUT file dragged in from the Browser panel (_LUTDropListWidget) --
        both land here so behavior can't drift between them."""
        try:
            lut = load_lut_any_format(path, clf_bake_size=LIBRARY_BAKE_SIZE)
        except Exception as e:  # noqa: BLE001 -- surface any loader failure to the user
            QMessageBox.critical(self, "Failed to import LUT", str(e))
            return
        name = getattr(lut, "name", None) or Path(path).stem
        entry = LUTLibraryEntry(
            name, lut, source_filename=Path(path).name, source_path=str(path))
        self.library.add_entry(entry)
        self._refresh_list(keep_selection=len(self.library.entries) - 1)

    def import_lut_object(self, name, lut, source_filename=None, source_path=None):
        """Like import_from_path, but for a LUT object ALREADY in memory --
        used by MainWindow's Export dialog's "Also add to LUT Library"
        checkbox (per Tom). Deliberately does NOT re-read the file it just
        wrote via import_from_path/load_lut_any_format: some export
        formats (.3dl in particular, and CLF's process-list wrapping) are
        lossy or restructure the table relative to what's actually baked
        in memory, so re-decoding the just-written file could hand back a
        subtly different table than what was really exported. Taking the
        baked colour.LUT3D object directly guarantees the library entry
        matches exactly what MainWindow baked and wrote, full precision,
        no round trip. source_filename/source_path are still recorded
        (same DISPLAY-ONLY provenance as import_from_path) since a real
        file now exists at that path even though it was never read back
        in."""
        entry = LUTLibraryEntry(
            name, lut, source_filename=source_filename, source_path=source_path)
        self.library.add_entry(entry)
        self._refresh_list(keep_selection=len(self.library.entries) - 1)
        return entry

    # ---- apply / rename / delete ----------------------------------------

    def _on_apply_clicked(self, *_args):
        entry = self._selected_entry()
        if entry is None:
            return
        self.apply_requested.emit(entry)

    def _on_rename_clicked(self):
        entry = self._selected_entry()
        if entry is None:
            return
        name, ok = QInputDialog.getText(self, "Rename LUT", "Name:", text=entry.name)
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
            self, "Delete LUT",
            f'Delete "{entry.name}" from the library? This can\'t be undone.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.library.remove(entry.id)
        self._refresh_list()

    # ---- session save/load (see session_io.py) --------------------------

    def get_library(self):
        return self.library

    def load_library(self, library):
        """Replace the whole library wholesale -- used by MainWindow for
        session load, same "wholesale replace + rebuild the list" pattern
        as StackMemoriesPanel.load_library/CDLLibraryPanel.load_library."""
        self.library = library
        self._refresh_list()
