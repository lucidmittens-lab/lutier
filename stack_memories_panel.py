"""
stack_memories_panel.py -- the "Stack Memories" panel, its own dock
widget in the left column below Browser by default (see main_window.py's
_build_default_dock_layout; freely draggable elsewhere since the docking
migration, see RUNNING.md #40) -- used to be a tab living alongside the
filesystem browser inside one Browser PanelFrame, split into its own
panel per Tom so both are visible at once instead of one-tab-at-a-time;
BrowserPanel itself is unchanged).
Lets you save the CURRENT Stack as a named, reusable snapshot, recall one back
into the working Stack, update a saved snapshot with the stack's current
state, rename, or delete -- all backed by stack_memory.StackMemoryLibrary,
which does the actual non-destructive copy-on-save/copy-on-recall work
(see that module's docstring). Saved as part of the session (see
session_io.py's memory_library_to_session_list), so a shelf of alternate
stack configs survives a save/reopen the same way the working stack does.

Owns its own StackMemoryLibrary directly (same "this widget IS the source
of truth for its own data" pattern StackPanel uses for stack.Stack) --
get_library()/load_library() are the session save/load hooks, mirroring
StackPanel.get_stack()/set_stack(). Reads the live working stack via a
set_stack_getter callback rather than a stored reference, same reasoning
SampleImagePanel already uses for its own stack_getter: always reflects
the stack exactly as it stands right now, not whatever it was when wired
up.

Recall doesn't apply anything directly -- it emits recall_requested with
a ready-to-use Stack (already copied, see StackMemoryLibrary.recall), and
MainWindow routes that into StackPanel.set_stack, same as it does for
opening a session. This widget has no idea StackPanel exists.

save_memory() (per Tom) is save's own name-prompt-then-save split into
two pieces so MainWindow's Export dialog "Save Stack Memory" checkbox can
save one without a second QInputDialog stacked in the middle of Export --
see that method's docstring and main_window.py's export_current_lut.

Export.../Import... (per Tom) save/load a SINGLE memory to/from its own
.ltm file via stack_memory_file_io.py, independent of the whole .ltr
session -- lets one saved memory be shared or moved into a different
session without exporting/opening the entire thing. Export writes the
selected memory; Import appends a new memory (its own fresh id, see
stack_memory_file_io.import_memory) and surfaces any per-stage warnings
the same way opening a session does. Both are MEMORY-only concepts (a
GalleryStill has no analogous "sharable single-file" format) -- see
Phase 3 note below.

STILLS GALLERY (Phase 3, per Tom: "Stack library should not be part of
[the Media Library] but it SHOULD be enhanced to be a stills gallery")
-- this panel's list is now a UNION of two kinds of row, both thumbnailed
(see thumbnail.py/media_cache.py):

  - a StackMemory (unchanged concept, now also carries a thumbnail AND a
    full canvas-resolution still, both captured automatically at save
    time via set_still_capture_getter -- see _on_save_clicked/
    save_memory) -- Recall (formerly "Load") restores its saved stack.
    ALSO supports Load as Reference (Phase 3 follow-up, per Tom: "when i
    try to load the stack memory as reference..." -- originally guarded
    off, now supported) whenever it has a captured full_still: decoded
    from its media_cache.py PNG bytes and routed through
    load_full_still_as_reference_requested to sample_image_panel.
    load_reference_from_array. A memory with no full_still (saved before
    this feature existed, or saved with no Sample loaded) shows an
    explanatory message instead of silently doing nothing.
  - a NEW GalleryStill (stack_memory.py) -- an externally-imported
    reference still, via the new "Import Still..." action -- Load as
    Reference sends it to the Viewer's Reference slot (via
    sample_image_panel.load_reference_from_path, since a GalleryStill
    doesn't keep a decoded array in memory the way an ImageLibraryEntry
    does -- see that class's own docstring).

Deliberately kept OUT of the real Media Library (image_library.py/
image_library_panel.py) -- Tom's own instruction: "for now just relevant
that we're not folding it into the media library." Update/Export/Import
.ltm only make sense for a memory row -- selecting a still row and
invoking one of those is a guarded no-op with an explanatory message,
same "wrong-kind selection" treatment image_library_panel.py's own
video-vs-still asymmetry (Load as Reference) already uses. Rename/Delete
work generically across both kinds. Double-clicking (or Enter) a row
does whichever action is that row's own PRIMARY one -- Recall for a
memory (Load as Reference stays a secondary, menu-only action for a
memory row, see _on_item_double_clicked), Load as Reference for a still.

NOT TESTED IN THIS ENVIRONMENT -- same PySide6/libEGL wall as the rest of
the GUI layer (see browser_panel.py's docstring). Only py_compile
syntax-checked. Please run main_window.py and confirm: (1) Save Current
Stack... prompts for a name and adds an entry showing its stage count,
(2) double-clicking or Load recalls it into the working Stack without
disturbing the saved memory when you edit afterward, (3) Update
overwrites the selected memory with whatever the stack currently looks
like, (4) Rename/Delete work and ask before Delete, (5) memories survive
Save Session -> Open Session, (6) Export... writes a .ltm file and
Import... on that same file adds back an equivalent memory, including
into a DIFFERENT session/app instance.
"""
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QListWidget, QListWidgetItem,
    QMessageBox, QInputDialog, QFileDialog, QToolButton, QMenu,
)

from stack_memory import StackMemoryLibrary, GalleryStill, GalleryStillLibrary
from app_logic import load_image_as_array, array_to_png_bytes, png_bytes_to_array
import stack_memory_file_io
import hover_inspector
import media_cache
import thumbnail

MEMORY_FILE_FILTER = "LUTier Stack Memory (*.ltm);;All files (*)"
STILL_FILE_FILTER = (
    "Image files (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.exr)"
    ";;All files (*)"
)


class StackMemoriesPanel(QWidget):
    recall_requested = Signal(object)  # a ready-to-use stack.Stack (already copied)
    load_as_reference_requested = Signal(object)  # a stack_memory.GalleryStill
    load_full_still_as_reference_requested = Signal(object)  # a decoded (H,W,3) float array

    def __init__(self, parent=None):
        super().__init__(parent)
        self.library = StackMemoryLibrary()
        self.gallery_library = GalleryStillLibrary()
        self._stack_getter = None
        self._still_capture_getter = None
        self._rows = []  # combined [memory, ..., still, ...] -- see _refresh_list
        self._build_layout()
        self._refresh_list()

    def set_stack_getter(self, getter):
        """getter: a zero-arg callable returning StackPanel's live
        stack.Stack object -- same pattern SampleImagePanel uses, so Save/
        Update always capture the stack exactly as it stands right now."""
        self._stack_getter = getter

    def set_still_capture_getter(self, getter):
        """getter: a zero-arg callable returning a full CANVAS-resolution
        graded array (or None if there's nothing to capture -- no Sample
        loaded, empty stack, or the render itself failed), used to
        capture a StackMemory's thumbnail AND full_still at save time
        (Phase 3 follow-up, per Tom: "also capture full canvas-res
        still" -- see MainWindow._capture_gallery_still for what actually
        builds that array: a full-resolution re-render, resized to the
        configured canvas raster). Deliberately ONE getter for both --
        the thumbnail is derived FROM the full-res array (downsized),
        not captured separately, so this is exactly one render per save/
        update, not two."""
        self._still_capture_getter = getter

    def _build_layout(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)  # PanelFrame provides the outer gutter

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.list_widget, stretch=1)
        hover_inspector.register(self.list_widget, self._hover_info_for_item)

        self._build_menu_button()

    def _hover_info_for_item(self, item):
        """Alt+hover info card content (see hover_inspector.py) -- a
        stage-by-stage breakdown for a memory row (which the row's own
        compact "N stages" label, see _refresh_list, doesn't show at
        all), or provenance for a gallery still row."""
        row = self.list_widget.row(item)
        if not (0 <= row < len(self._rows)):
            return None
        entry = self._rows[row]
        if isinstance(entry, GalleryStill):
            lines = [entry.name]
            if entry.source_path:
                lines.append(entry.source_path)
            return "\n".join(lines)
        lines = [entry.name, f"{len(entry.stack.stages)} stage(s):"]
        for i, stage in enumerate(entry.stack.stages, start=1):
            state = "on" if stage.enabled else "OFF"
            lines.append(f"  {i}. {stage.label} [{stage.kind}, {state}, {stage.opacity:.0%}]")
        return "\n".join(lines)

    def _build_menu_button(self):
        """The Stack Memories panel's "..." header menu (see corner_widget
        on PanelFrame, main_window.py) -- replaces the old always-visible
        Save/Load/Update/Rename/Delete/Export/Import button rows with menu
        actions instead, per Tom, same treatment Stack's/CDL Library's/LUT
        Library's own header menus already got. Double-clicking a list
        entry (Load) still works independent of this menu."""
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
        corner button above and MainWindow's top-level "Stack Memories"
        menu-bar menu (per Tom, making the "..." menu redundant rather
        than the only way in), so both stay in sync automatically since
        they're wired to the exact same methods."""
        save_action = menu.addAction("Save Current Stack...", self._on_save_clicked)
        save_action.setShortcut(QKeySequence("Ctrl+G"))
        recall_action = menu.addAction("Recall", self._on_recall_clicked)
        recall_action.setShortcut(QKeySequence("Ctrl+Shift+G"))
        update_action = menu.addAction("Update", self._on_update_clicked)
        update_action.setShortcut(QKeySequence("Ctrl+U"))
        menu.addSeparator()
        import_still_action = menu.addAction("Import Still...", self._on_import_still_clicked)
        import_still_action.setToolTip(
            "Add an external reference still to the gallery (Phase 3) -- "
            "kept separate from the Image Library, see this panel's own "
            "docstring.")
        load_as_reference_action = menu.addAction(
            "Load as Reference", self._on_load_as_reference_clicked)
        menu.addSeparator()
        menu.addAction("Rename...", self._on_rename_clicked)
        menu.addAction("Delete", self._on_delete_clicked)
        menu.addSeparator()
        export_action = menu.addAction("Export...", self._on_export_clicked)
        export_action.setShortcut(QKeySequence("Ctrl+Shift+E"))
        export_action.setToolTip(
            "Save the selected memory to its own .ltm file -- lets you "
            "share it or move it into another session without exporting "
            "the whole session.")
        import_action = menu.addAction("Import...", self._on_import_clicked)
        import_action.setToolTip("Load a .ltm file saved by Export... as a new memory.")

    # ---- list -----------------------------------------------------------

    def _selected_entry(self):
        """Returns the selected row's own object -- a StackMemory OR a
        GalleryStill (see _rows/_refresh_list) -- or None if nothing's
        selected. Callers that only make sense for one kind check
        isinstance themselves (see e.g. _on_recall_clicked)."""
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self._rows):
            return None
        return self._rows[row]

    def _refresh_list(self, keep_selection=None):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        self._rows = list(self.library.memories) + list(self.gallery_library.stills)
        for entry in self._rows:
            if isinstance(entry, GalleryStill):
                item = QListWidgetItem(f"{entry.name}  [reference still]")
            else:
                stage_count = len(entry.stack.stages)
                suffix = "stage" if stage_count == 1 else "stages"
                item = QListWidgetItem(f"{entry.name}  ({stage_count} {suffix})")
            png_bytes = media_cache.load_thumbnail(entry.thumbnail) if entry.thumbnail else None
            if png_bytes:
                pixmap = QPixmap()
                if pixmap.loadFromData(png_bytes, "PNG"):
                    item.setIcon(pixmap)
            self.list_widget.addItem(item)
        if keep_selection is not None and 0 <= keep_selection < len(self._rows):
            self.list_widget.setCurrentRow(keep_selection)
        self.list_widget.blockSignals(False)

    # ---- actions ----------------------------------------------------------

    def _on_item_double_clicked(self, *_args):
        """Double-click does whichever action is that row's own PRIMARY
        one -- Recall for a memory, Load as Reference for a still (see
        module docstring)."""
        entry = self._selected_entry()
        if isinstance(entry, GalleryStill):
            self._on_load_as_reference_clicked()
        elif entry is not None:
            self._on_recall_clicked()

    def _on_save_clicked(self):
        if self._stack_getter is None:
            return
        stack = self._stack_getter()
        if not stack.stages:
            QMessageBox.warning(
                self, "Empty stack", "Add stages to the Stack before saving a memory.")
            return
        name, ok = QInputDialog.getText(self, "Save Stack Memory", "Name:")
        name = name.strip()
        if not ok or not name:
            return
        self.save_memory(name, stack)

    def _capture_still_and_thumbnail(self, name_hint):
        """Best-effort: no capture getter wired up, or nothing to capture
        (no Sample loaded/empty stack/render failure -- see
        set_still_capture_getter), both just mean "nothing captured"
        ((None, None)), never a crash -- same tolerance every other
        media_cache consumer already has for a missing/failed thumbnail.
        Returns (thumbnail_descriptor, full_still_descriptor) -- ONE
        full-resolution render, downsized once for the thumbnail (see
        set_still_capture_getter's own docstring on why this isn't two
        separate captures)."""
        if self._still_capture_getter is None:
            return None, None
        array = self._still_capture_getter()
        if array is None:
            return None, None
        thumb = media_cache.store_thumbnail(
            f"stack-memory-thumb-{name_hint}", thumbnail.make_thumbnail_png_bytes(array))
        full_still = media_cache.store_thumbnail(
            f"stack-memory-still-{name_hint}", array_to_png_bytes(array))
        return thumb, full_still

    def save_memory(self, name, stack):
        """Save `stack` as a new named Stack Memory -- the actual save +
        list-refresh _on_save_clicked does after its own name prompt,
        pulled out so MainWindow's Export dialog "Save Stack Memory"
        checkbox (per Tom) can reuse it directly with a name it derives
        itself (see main_window.py's export_current_lut), no second
        name-prompt dialog stacked in the middle of Export. Returns the
        new StackMemory, same object library.save() itself returns.
        Captures a thumbnail AND a full canvas-resolution still
        automatically (Phase 3 follow-up, see set_still_capture_getter/
        _capture_still_and_thumbnail) -- both None if no capture getter
        is wired up or there's nothing to capture from."""
        thumb, full_still = self._capture_still_and_thumbnail(name)
        memory = self.library.save(name, stack, thumbnail=thumb, full_still=full_still)
        # New memory lands at the end of the memories SEGMENT of _rows
        # (memories first, then gallery stills -- see _refresh_list) --
        # not len(_rows) itself, which (before this refresh runs) is
        # still the pre-save row count and would land on the wrong row
        # whenever any gallery stills already exist.
        self._refresh_list(keep_selection=len(self.library.memories) - 1)
        return memory

    def _on_recall_clicked(self, *_args):
        entry = self._selected_entry()
        if not isinstance(entry, GalleryStill) and entry is not None:
            memory = entry
            # StackMemoryLibrary.recall already returns a fresh copy --
            # safe to hand straight to whoever's listening (MainWindow ->
            # set_stack) without this panel needing to know or care what
            # they do with it.
            self.recall_requested.emit(self.library.recall(memory.id))

    def _on_load_as_reference_clicked(self):
        """A GalleryStill always supports this (load_as_reference_requested,
        routed to sample_image_panel.load_reference_from_path -- a fresh
        disk re-decode). A StackMemory ALSO supports this now (Phase 3
        follow-up, per Tom) IF it has a captured full_still -- decoded
        here from its media_cache.py PNG bytes and routed through a
        separate signal (load_full_still_as_reference_requested) to
        sample_image_panel.load_reference_from_array, since a memory's
        capture has no source file to reload from the way a GalleryStill
        does."""
        entry = self._selected_entry()
        if isinstance(entry, GalleryStill):
            self.load_as_reference_requested.emit(entry)
            return
        if entry is not None:
            memory = entry
            png_bytes = media_cache.load_thumbnail(memory.full_still) if memory.full_still else None
            if png_bytes is None:
                QMessageBox.information(
                    self, "Load as Reference",
                    f'"{memory.name}" has no captured still to load -- '
                    "either it was saved before this feature existed, or "
                    "no Sample image was loaded at save time.")
                return
            array = png_bytes_to_array(png_bytes)
            self.load_full_still_as_reference_requested.emit(array)

    def _on_import_still_clicked(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Reference Still", "", STILL_FILE_FILTER)
        if not path:
            return
        self.import_still_from_path(path)

    def import_still_from_path(self, path):
        """Decode `path` just long enough to generate a thumbnail -- a
        GalleryStill is disk-referenced only (see that class's own
        docstring), so, unlike ImageLibraryEntry, the decoded array
        itself is never kept around."""
        try:
            array = load_image_as_array(path, max_dimension=None)
        except Exception as e:  # noqa: BLE001 -- surface any decode failure to the user
            QMessageBox.critical(self, "Failed to import still", str(e))
            return
        name = Path(path).stem
        thumb = media_cache.store_thumbnail(
            f"gallery-still-{name}", thumbnail.make_thumbnail_png_bytes(array))
        still = GalleryStill(
            name, thumb, str(path), source_filename=Path(path).name)
        self.gallery_library.add_still(still)
        # New still lands at the end of the stills SEGMENT of _rows
        # (memories first, then gallery stills -- see _refresh_list).
        new_index = len(self.library.memories) + len(self.gallery_library.stills) - 1
        self._refresh_list(keep_selection=new_index)

    def _on_update_clicked(self):
        entry = self._selected_entry()
        if isinstance(entry, GalleryStill):
            QMessageBox.information(
                self, "Update",
                "Update only applies to a saved Stack Memory, not a "
                "gallery still.")
            return
        memory = entry
        if memory is None or self._stack_getter is None:
            return
        reply = QMessageBox.question(
            self, "Update Memory",
            f'Overwrite "{memory.name}" with the current stack?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        thumb, full_still = self._capture_still_and_thumbnail(memory.name)
        self.library.update(memory.id, self._stack_getter(), thumbnail=thumb, full_still=full_still)
        self._refresh_list(keep_selection=self.list_widget.currentRow())

    def _on_rename_clicked(self):
        entry = self._selected_entry()
        if entry is None:
            return
        title = "Rename Reference Still" if isinstance(entry, GalleryStill) else "Rename Memory"
        name, ok = QInputDialog.getText(self, title, "Name:", text=entry.name)
        name = name.strip()
        if not ok or not name:
            return
        if isinstance(entry, GalleryStill):
            self.gallery_library.rename(entry.id, name)
        else:
            self.library.rename(entry.id, name)
        self._refresh_list(keep_selection=self.list_widget.currentRow())

    def _on_delete_clicked(self):
        entry = self._selected_entry()
        if entry is None:
            return
        noun = "reference still" if isinstance(entry, GalleryStill) else "memory"
        reply = QMessageBox.question(
            self, "Delete",
            f'Delete "{entry.name}" ({noun})? This can\'t be undone.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if isinstance(entry, GalleryStill):
            self.gallery_library.remove(entry.id)
            self._refresh_list()
            return
        self.library.remove(entry.id)
        self._refresh_list()

    # ---- export/import (see stack_memory_file_io.py) -- MEMORY only,
    # a GalleryStill has no analogous single-file format (module docstring)

    def _on_export_clicked(self):
        entry = self._selected_entry()
        if isinstance(entry, GalleryStill):
            QMessageBox.information(
                self, "Export",
                "Export... only applies to a saved Stack Memory, not a "
                "gallery still.")
            return
        memory = entry
        if memory is None:
            return
        default_name = f"{memory.name}.ltm"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Stack Memory", default_name, MEMORY_FILE_FILTER)
        if not path:
            return
        try:
            stack_memory_file_io.export_memory(memory, path)
        except Exception as e:  # noqa: BLE001 -- surface any write failure to the user
            QMessageBox.critical(self, "Failed to export memory", str(e))
            return

    def _on_import_clicked(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Stack Memory", "", MEMORY_FILE_FILTER)
        if not path:
            return
        try:
            memory, warnings = stack_memory_file_io.import_memory(path)
        except Exception as e:  # noqa: BLE001 -- surface any read/parse failure to the user
            QMessageBox.critical(self, "Failed to import memory", str(e))
            return
        # Imported memories are appended with a fresh id (see
        # stack_memory_file_io.import_memory) -- never collides with an
        # existing memory already in this library.
        self.library.memories.append(memory)
        self._refresh_list(keep_selection=len(self.library.memories) - 1)
        if warnings:
            QMessageBox.warning(
                self, "Imported with warnings",
                f'"{memory.name}" imported, but some stages couldn\'t be '
                "restored:\n\n" + "\n".join(warnings))

    # ---- session save/load (see session_io.py) -----------------------

    def get_library(self):
        return self.library

    def get_gallery_library(self):
        return self.gallery_library

    def load_library(self, library):
        """Replace the whole StackMemory library wholesale -- used by
        MainWindow for session load, same "wholesale replace + rebuild
        the list" pattern as StackPanel.set_stack."""
        self.library = library
        self._refresh_list()

    def load_gallery_library(self, gallery_library):
        """Replace the whole GalleryStill library wholesale -- same
        pattern as load_library, a separate method since the two are
        independent libraries persisted independently (see session_io.py)."""
        self.gallery_library = gallery_library
        self._refresh_list()
