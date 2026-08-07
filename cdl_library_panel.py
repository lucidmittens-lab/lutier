"""
cdl_library_panel.py -- the "CDL Library" panel (left column by default,
see main_window.py's _build_default_dock_layout: Browser/Image Library/
LUT Library/CDL Library/Stack Memories, top to bottom -- freely
draggable elsewhere since the docking migration, see RUNNING.md #40) --
a named shelf of saved ASC CDL grades, mirroring
stack_memories_panel.py's "named, reusable snapshot" pattern, but each
entry is a single CDL's SOP/SAT values (cdl_library.CDLEntry) rather than
a whole Stack.

Two ways entries get added:
  - Saved from the currently-selected CDL stage in the Stack -- initiated
    from stack_panel.py's CDL inspector ("Save to CDL Library..." button,
    see StackPanel.cdl_save_requested), which prompts for a name itself
    and hands MainWindow a (name, params) pair; MainWindow routes that
    into this panel's save_entry(). This panel has no idea StackPanel
    exists, same "MainWindow bridges, panels don't know about each other"
    architecture as StackMemoriesPanel.recall_requested/StackPanel.set_stack.
  - Imported from a real .cc/.cdl/.ccc file on disk -- via
    cdl_file_io.parse_cdl_file (OCIO's own parser, handles all three
    container formats uniformly). Three equivalent entry points all
    funnel into the same import_from_path(): the "..." header menu's
    Import... action (file dialog), dragging a CDL file in from the
    Browser panel (_CDLDropListWidget below, CDL_PATH_MIME), or
    double-clicking a CDL file in the Browser itself (routed here via
    BrowserPanel.cdl_file_selected, see main_window.py's wiring and
    browser_panel.py's docstring on why CDL files go to the Library
    rather than straight to the Stack the way LUT files do). PER TOM: the
    parsed SOP/SAT VALUES are copied into each new CDLEntry immediately
    at import time -- source_filename/source_path are recorded on the
    entry ONLY as on-screen reference, never read from again, so a CDL
    Library entry can never go missing or break just because the
    original file was moved, renamed, or deleted afterward (unlike a
    "lut" stage's source_path, which DOES get reloaded on session open --
    see session_io.py's docstring on why that's the right call there
    specifically, and cdl_library.py's docstring on why CDL entries are
    different).

Recalling an entry ("Apply to Stack") doesn't touch the Stack directly --
it emits apply_requested with the CDLEntry, and MainWindow routes that
into StackPanel.apply_cdl_entry (updates the selected 'cdl' stage in
place if one's selected, otherwise adds a new one -- see that method's
own docstring for why one action covers both cases).

Import.../Apply to Stack/Rename.../Delete all live in the "..." header
menu now (see _build_menu_button, corner_widget on PanelFrame in
main_window.py) rather than always-visible button rows, per Tom -- same
treatment Stack's own header menu already got. Double-clicking a list
entry (Apply to Stack) and dragging/double-clicking a CDL file in from
the Browser (Import) both still work independent of this menu.

NOT TESTED IN THIS ENVIRONMENT -- same PySide6/libEGL wall as the rest of
the GUI layer. Please run main_window.py and confirm: (1) "Save to CDL
Library..." in the CDL inspector prompts for a name and adds an entry
showing its SOP/SAT summary, (2) the "..." menu's Import... on a real
.cc/.cdl/.ccc file adds one entry per ColorCorrection found, named from
each one's own id/description when present, falling back to the filename
otherwise, (3) dragging a CDL file from the Browser onto this panel, and
double-clicking one in the Browser, both do the same import, (4)
double-clicking or "Apply to Stack" updates the selected CDL stage in
place without disturbing anything else in the stack, or adds a new CDL
stage if nothing CDL-shaped is selected, (5) Rename/Delete work and ask
before Delete, (6) entries survive Save Session -> Open Session, and (7)
moving/deleting an imported file afterward doesn't break the entry that
came from it (per Tom's "can't possibly go missing" requirement) -- only
the on-screen source note would look stale, nothing about applying the
entry depends on that file still existing.
"""
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QListWidget, QListWidgetItem,
    QMessageBox, QInputDialog, QFileDialog, QToolButton, QMenu,
)

from cdl_library import CDLLibrary, CDLEntry
import cdl_file_io
from browser_panel import CDL_PATH_MIME

CDL_FILE_FILTER = "ASC CDL (*.cc *.cdl *.ccc);;All files (*)"


class _CDLDropListWidget(QListWidget):
    """QListWidget that also accepts a CDL file dragged in from the
    Browser panel (see browser_panel.py's CDL_PATH_MIME/
    _DraggableFileListWidget) -- drops straight through to the panel's
    own import_from_path, same import path the Import... menu action and
    a CDL double-click in the Browser both use, so all three entry points
    behave identically."""

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self._panel = panel
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(CDL_PATH_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(CDL_PATH_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        mime = event.mimeData()
        if not mime.hasFormat(CDL_PATH_MIME):
            event.ignore()
            return
        path = bytes(mime.data(CDL_PATH_MIME)).decode("utf-8")
        event.acceptProposedAction()
        self._panel.import_from_path(path)


class CDLLibraryPanel(QWidget):
    apply_requested = Signal(object)  # a cdl_library.CDLEntry

    def __init__(self, parent=None):
        super().__init__(parent)
        self.library = CDLLibrary()
        self._build_layout()
        self._refresh_list()

    def _build_layout(self):
        layout = QVBoxLayout(self)

        self.list_widget = _CDLDropListWidget(self)
        self.list_widget.itemDoubleClicked.connect(self._on_apply_clicked)
        layout.addWidget(self.list_widget, stretch=1)

        self._build_menu_button()

    def _build_menu_button(self):
        """The CDL Library panel's "..." header menu (see corner_widget on
        PanelFrame, main_window.py) -- replaces the old always-visible
        Import.../Apply to Stack/Rename.../Delete button rows with menu
        actions instead, per Tom, same treatment Stack's own header menu
        already got (see stack_panel.py's _build_list_menu_button).
        Double-clicking a list entry (Apply to Stack) and dragging a CDL
        file in from the Browser (Import) both still work independent of
        this menu."""
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
        corner button above and MainWindow's top-level "CDL Library"
        menu-bar menu (per Tom, making the "..." menu redundant rather
        than the only way in), so both stay in sync automatically since
        they're wired to the exact same methods."""
        import_action = menu.addAction("Import...", self._on_import_clicked)
        # No shortcut -- Tom asked for "Shift+I", but a bare Shift+letter
        # just types a capital letter, which a WindowShortcut-context
        # QAction shortcut would intercept and steal from any focused text
        # field (e.g. typing "Import" during a Rename...). Flagged back to
        # Tom; per his call, skipped rather than reworked -- see
        # RUNNING.md #47.
        import_action.setToolTip(
            "Import a .cc/.cdl/.ccc file -- its SOP/SAT values are copied "
            "in, not just referenced.")
        apply_action = menu.addAction("Apply to Stack", self._on_apply_clicked)
        # Same "Shift+A" hold as Import... above.
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
        r, g, b = entry.slope
        ro, go, bo = entry.offset
        rp, gp, bp = entry.power
        provenance = f"  [{entry.source_filename}]" if entry.source_filename else ""
        bypass = ""
        if not entry.sop_enabled:
            bypass += " [SOP off]"
        if not entry.sat_enabled:
            bypass += " [SAT off]"
        return (
            f"{entry.name}{provenance}{bypass}\n"
            f"  S({r:.3f},{g:.3f},{b:.3f}) O({ro:.3f},{go:.3f},{bo:.3f}) "
            f"P({rp:.3f},{gp:.3f},{bp:.3f}) Sat {entry.sat:.3f}"
        )

    def _refresh_list(self, keep_selection=None):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for entry in self.library.entries:
            item = QListWidgetItem(self._entry_summary_text(entry))
            self.list_widget.addItem(item)
        if keep_selection is not None and 0 <= keep_selection < len(self.library.entries):
            self.list_widget.setCurrentRow(keep_selection)
        self.list_widget.blockSignals(False)

    # ---- save (from the CDL inspector) -------------------------------

    def save_entry(self, name, params):
        """Called by MainWindow in response to
        StackPanel.cdl_save_requested (see stack_panel.py's "Save to CDL
        Library..." button) -- `params` is that stage's own params dict
        (slope/offset/power/sat/style/sop_enabled/sat_enabled). No
        source_filename/source_path -- this entry came from a live stage,
        not an imported file."""
        self.library.save(
            name,
            params.get("slope", (1.0, 1.0, 1.0)),
            params.get("offset", (0.0, 0.0, 0.0)),
            params.get("power", (1.0, 1.0, 1.0)),
            params.get("sat", 1.0),
            style=params.get("style", "asc"),
            sop_enabled=params.get("sop_enabled", True),
            sat_enabled=params.get("sat_enabled", True),
        )
        self._refresh_list(keep_selection=len(self.library.entries) - 1)

    # ---- import (from a real file) -----------------------------------

    def _on_import_clicked(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import CDL", "", CDL_FILE_FILTER)
        if not path:
            return
        self.import_from_path(path)

    def import_from_path(self, path):
        """Shared by the Import... menu action (file dialog above) and a
        CDL file dragged in from the Browser panel (_CDLDropListWidget,
        and BrowserPanel.cdl_file_selected on double-click -- see
        main_window.py's wiring) -- all three land here so behavior can't
        drift between them."""
        try:
            parsed = cdl_file_io.parse_cdl_file(path)
        except Exception as e:  # noqa: BLE001 -- surface any parse failure to the user
            QMessageBox.critical(self, "Failed to import CDL", str(e))
            return
        if not parsed:
            QMessageBox.warning(
                self, "Nothing found",
                f"No ColorCorrection entries found in:\n{path}")
            return

        stem = Path(path).stem
        filename = Path(path).name
        multiple = len(parsed) > 1
        last_index = None
        for i, data in enumerate(parsed):
            # Prefer the file's own id/description; fall back to the
            # filename (suffixed with its position if the file held more
            # than one entry, so several unnamed entries from the same
            # file don't all collide on one identical name).
            name = data.get("id") or data.get("description") or (
                f"{stem} #{i + 1}" if multiple else stem)
            entry = CDLEntry(
                name, data["slope"], data["offset"], data["power"], data["sat"],
                style=data.get("style", "asc"),
                source_filename=filename, source_path=str(path),
            )
            self.library.add_entry(entry)
            last_index = len(self.library.entries) - 1
        self._refresh_list(keep_selection=last_index)

    # ---- apply / rename / delete --------------------------------------

    def _on_apply_clicked(self, *_args):
        entry = self._selected_entry()
        if entry is None:
            return
        self.apply_requested.emit(entry)

    def _on_rename_clicked(self):
        entry = self._selected_entry()
        if entry is None:
            return
        name, ok = QInputDialog.getText(self, "Rename CDL", "Name:", text=entry.name)
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
            self, "Delete CDL",
            f'Delete "{entry.name}"? This can\'t be undone.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.library.remove(entry.id)
        self._refresh_list()

    # ---- session save/load (see session_io.py) -----------------------

    def get_library(self):
        return self.library

    def load_library(self, library):
        """Replace the whole library wholesale -- used by MainWindow for
        session load, same "wholesale replace + rebuild the list" pattern
        as StackMemoriesPanel.load_library/StackPanel.set_stack."""
        self.library = library
        self._refresh_list()
