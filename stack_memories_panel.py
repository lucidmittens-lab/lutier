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
the same way opening a session does.

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
from PySide6.QtCore import Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QListWidget, QListWidgetItem,
    QMessageBox, QInputDialog, QFileDialog, QToolButton, QMenu,
)

from stack_memory import StackMemoryLibrary
import stack_memory_file_io
import hover_inspector

MEMORY_FILE_FILTER = "LUTier Stack Memory (*.ltm);;All files (*)"


class StackMemoriesPanel(QWidget):
    recall_requested = Signal(object)  # a ready-to-use stack.Stack (already copied)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.library = StackMemoryLibrary()
        self._stack_getter = None
        self._build_layout()
        self._refresh_list()

    def set_stack_getter(self, getter):
        """getter: a zero-arg callable returning StackPanel's live
        stack.Stack object -- same pattern SampleImagePanel uses, so Save/
        Update always capture the stack exactly as it stands right now."""
        self._stack_getter = getter

    def _build_layout(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)  # PanelFrame provides the outer gutter

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._on_load_clicked)
        layout.addWidget(self.list_widget, stretch=1)
        hover_inspector.register(self.list_widget, self._hover_info_for_item)

        self._build_menu_button()

    def _hover_info_for_item(self, item):
        """Alt+hover info card content (see hover_inspector.py) -- a
        stage-by-stage breakdown, which the row's own compact "N stages"
        label (see _refresh_list) doesn't show at all."""
        row = self.list_widget.row(item)
        if not (0 <= row < len(self.library.memories)):
            return None
        memory = self.library.memories[row]
        lines = [memory.name, f"{len(memory.stack.stages)} stage(s):"]
        for i, stage in enumerate(memory.stack.stages, start=1):
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
        load_action = menu.addAction("Load", self._on_load_clicked)
        load_action.setShortcut(QKeySequence("Ctrl+Shift+G"))
        update_action = menu.addAction("Update", self._on_update_clicked)
        update_action.setShortcut(QKeySequence("Ctrl+U"))
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

    def _selected_memory(self):
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self.library.memories):
            return None
        return self.library.memories[row]

    def _refresh_list(self, keep_selection=None):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for memory in self.library.memories:
            stage_count = len(memory.stack.stages)
            suffix = "stage" if stage_count == 1 else "stages"
            item = QListWidgetItem(f"{memory.name}  ({stage_count} {suffix})")
            self.list_widget.addItem(item)
        if keep_selection is not None and 0 <= keep_selection < len(self.library.memories):
            self.list_widget.setCurrentRow(keep_selection)
        self.list_widget.blockSignals(False)

    # ---- actions ----------------------------------------------------------

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

    def save_memory(self, name, stack):
        """Save `stack` as a new named Stack Memory -- the actual save +
        list-refresh _on_save_clicked does after its own name prompt,
        pulled out so MainWindow's Export dialog "Save Stack Memory"
        checkbox (per Tom) can reuse it directly with a name it derives
        itself (see main_window.py's export_current_lut), no second
        name-prompt dialog stacked in the middle of Export. Returns the
        new StackMemory, same object library.save() itself returns."""
        memory = self.library.save(name, stack)
        self._refresh_list(keep_selection=len(self.library.memories) - 1)
        return memory

    def _on_load_clicked(self, *_args):
        memory = self._selected_memory()
        if memory is None:
            return
        # StackMemoryLibrary.recall already returns a fresh copy -- safe to
        # hand straight to whoever's listening (MainWindow -> set_stack)
        # without this panel needing to know or care what they do with it.
        self.recall_requested.emit(self.library.recall(memory.id))

    def _on_update_clicked(self):
        memory = self._selected_memory()
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
        self.library.update(memory.id, self._stack_getter())
        self._refresh_list(keep_selection=self.list_widget.currentRow())

    def _on_rename_clicked(self):
        memory = self._selected_memory()
        if memory is None:
            return
        name, ok = QInputDialog.getText(self, "Rename Memory", "Name:", text=memory.name)
        name = name.strip()
        if not ok or not name:
            return
        self.library.rename(memory.id, name)
        self._refresh_list(keep_selection=self.list_widget.currentRow())

    def _on_delete_clicked(self):
        memory = self._selected_memory()
        if memory is None:
            return
        reply = QMessageBox.question(
            self, "Delete Memory",
            f'Delete "{memory.name}"? This can\'t be undone.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.library.remove(memory.id)
        self._refresh_list()

    # ---- export/import (see stack_memory_file_io.py) -------------------

    def _on_export_clicked(self):
        memory = self._selected_memory()
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

    def load_library(self, library):
        """Replace the whole library wholesale -- used by MainWindow for
        session load, same "wholesale replace + rebuild the list" pattern
        as StackPanel.set_stack."""
        self.library = library
        self._refresh_list()
