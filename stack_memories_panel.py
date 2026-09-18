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
    reference still, via the mono "Import..." action -- Load as
    Reference sends it to the Viewer's Reference slot (via
    sample_image_panel.load_reference_from_path, same path-only
    decode-on-demand a media_library.MediaEntry now also uses -- see
    that class's own docstring).

Deliberately kept OUT of the real Media Library (media_library.py/
media_panel.py) -- Tom's own instruction: "for now just relevant
that we're not folding it into the media library." Update/Export/Import
.ltm only make sense for a memory row -- selecting a still or CDL row and
invoking one of those is a guarded no-op with an explanatory message,
same "wrong-kind selection" treatment media_panel.py's own
video-vs-still asymmetry (Load as Reference) already uses. Rename/Delete
work generically across all three kinds. Double-clicking (or Enter) a row
does whichever action is that row's own PRIMARY one -- Recall for a
memory (Load as Reference stays a secondary, menu-only action for a
memory row, see _on_item_double_clicked), Load as Reference for a still,
Apply to Stack for a CDL entry.

CDL LIBRARY, FOLDED IN (per Tom: "CDL library should also be rolled into
the gallery") -- a THIRD row kind, cdl_library.CDLEntry, owned here via
its own self.cdl_library (a cdl_library.CDLLibrary, unchanged class --
only which panel owns/displays it moved). Two ways an entry gets added,
identical to how cdl_library_panel.py used to work before this fold-in:
saved from the currently-selected CDL stage in the Stack (StackPanel's
"Save to CDL Library..." button, still that label, now routed into
save_cdl_entry instead), or imported from a real .cc/.cdl/.ccc file
(the mono "Import..." action, dispatched by extension, a CDL file
dragged in from the Browser via _GalleryDropListWidget, or
double-clicking one in the Browser -- see
import_cdl_from_path). Its own primary action is "Apply to Stack"
(apply_cdl_requested, routed by MainWindow into StackPanel.
apply_cdl_entry exactly as before) -- has no thumbnail, no Update/
Export/Import .ltm/Load as Reference (all guarded no-ops/explanatory
messages for a CDL row, same treatment a still row already gets for the
actions that don't apply to IT). session_io.py's cdl_library_to_session_
list/_from_session_list are UNCHANGED -- same "cdl_library" session key,
same CDLLibrary shape -- a session saved before this fold-in loads its
CDL entries here exactly as it used to load them into the old standalone
CDLLibraryPanel.

NOT TESTED IN THIS ENVIRONMENT -- same PySide6/libEGL wall as the rest of
the GUI layer (see browser_panel.py's docstring). Only py_compile
syntax-checked. Please run main_window.py and confirm: (1) Grab Still
prompts for a name (pre-filled with a source-name+timestamp suggestion)
and adds an entry showing its stage count, (2) right-clicking a row
shows Recall/Update Still/Apply CDL to Stack/Load as Reference/
Rename.../Delete/Export..., each acting on that row (double-click also
does Recall/Load as Reference/Apply to Stack for its own row kind), (3)
Update overwrites the selected memory with whatever the stack currently
looks like, (4) Rename/Delete work and ask before Delete, (5) memories
survive Save Session -> Open Session, (6) Export... writes a .ltm file
and Import... on that same file adds back an equivalent memory,
including into a DIFFERENT session/app instance.
"""
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QListWidget, QListWidgetItem,
    QMessageBox, QInputDialog, QFileDialog, QToolButton, QMenu,
)

from stack_memory import StackMemoryLibrary, GalleryStill, GalleryStillLibrary
from cdl_library import CDLLibrary, CDLEntry
import cdl_file_io
from app_logic import load_image_as_array, array_to_png_bytes, png_bytes_to_array
import stack_memory_file_io
import hover_inspector
import media_cache
import thumbnail
from browser_panel import CDL_PATH_MIME

MEMORY_FILE_FILTER = "LUTier Stack Memory (*.ltm);;All files (*)"
STILL_FILE_FILTER = (
    "Image files (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.exr)"
    ";;All files (*)"
)
CDL_FILE_FILTER = "ASC CDL (*.cc *.cdl *.ccc);;All files (*)"
STILL_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".exr"}
CDL_EXTENSIONS = {".cc", ".cdl", ".ccc"}
# One mono Import... action (per Tom) covers all three importable file
# kinds this panel understands -- dispatched by extension in
# _on_import_clicked, see that method.
IMPORT_FILE_FILTER = (
    "All supported (*.ltm *.cc *.cdl *.ccc *.png *.jpg *.jpeg *.tif *.tiff "
    "*.bmp *.exr);;"
    "LUTier Stack Memory (*.ltm);;"
    "ASC CDL (*.cc *.cdl *.ccc);;"
    "Image files (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.exr);;"
    "All files (*)"
)


class _GalleryDropListWidget(QListWidget):
    """QListWidget that also accepts a CDL file dragged in from the
    Browser panel (see browser_panel.py's CDL_PATH_MIME/
    _DraggableFileListWidget, same mechanism cdl_library_panel.py's own
    _CDLDropListWidget used before CDL Library was folded into this
    Gallery, per Tom) -- drops straight through to the panel's own
    import_cdl_from_path, same import path the mono "Import..." action
    and a CDL double-click in the Browser both use."""

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
        self._panel.import_cdl_from_path(path)


class StackMemoriesPanel(QWidget):
    recall_requested = Signal(object)  # a ready-to-use stack.Stack (already copied)
    load_as_reference_requested = Signal(object)  # a stack_memory.GalleryStill
    load_full_still_as_reference_requested = Signal(object)  # a decoded (H,W,3) float array
    apply_cdl_requested = Signal(object)  # a cdl_library.CDLEntry
    insert_in_stack_requested = Signal(object)  # a stack_memory.StackMemory or a cdl_library.CDLEntry

    def __init__(self, parent=None):
        super().__init__(parent)
        self.library = StackMemoryLibrary()
        self.gallery_library = GalleryStillLibrary()
        self.cdl_library = CDLLibrary()
        self._stack_getter = None
        self._still_capture_getter = None
        self._source_name_getter = None
        self._rows = []  # combined [memory, ..., still, ..., cdl, ...] -- see _refresh_list
        self._build_layout()
        self._refresh_list()

    def set_stack_getter(self, getter):
        """getter: a zero-arg callable returning StackPanel's live
        stack.Stack object -- same pattern SampleImagePanel uses, so Save/
        Update always capture the stack exactly as it stands right now."""
        self._stack_getter = getter

    def set_still_capture_getter(self, getter):
        """getter: a zero-arg callable returning a full CANVAS-resolution
        graded array (or None if there's nothing to capture -- no source
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

    def set_source_name_getter(self, getter):
        """getter: a zero-arg callable returning SampleImagePanel's
        current source filename stem (or None if nothing's loaded) --
        used by _on_save_clicked to pre-fill the Grab Still name prompt
        with "<source name> <date> <time>" (per Tom: "automatic still
        naming with the source name + date/time should show up in text
        box as a suggestion for file name when grabbing a still"). Just a
        starting suggestion, not enforced -- the text field stays fully
        editable/clearable before confirming."""
        self._source_name_getter = getter

    def _build_layout(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)  # PanelFrame provides the outer gutter

        self.list_widget = _GalleryDropListWidget(self)
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        # Per-item actions live on a right-click context menu, not the
        # "..." header menu -- see populate_menu/_on_context_menu_requested's
        # own docstrings.
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._on_context_menu_requested)
        layout.addWidget(self.list_widget, stretch=1)
        hover_inspector.register(self.list_widget, self._hover_info_for_item)

        self._build_menu_button()

    def _hover_info_for_item(self, item):
        """Alt+hover info card content (see hover_inspector.py) -- a
        stage-by-stage breakdown for a memory row (which the row's own
        compact "N stages" label, see _refresh_list, doesn't show at
        all), provenance for a gallery still row, or full SOP/SAT values
        for a CDL row (same detail cdl_library_panel.py's own hover card
        used to show, before CDL Library was folded into this Gallery,
        per Tom)."""
        row = self.list_widget.row(item)
        if not (0 <= row < len(self._rows)):
            return None
        entry = self._rows[row]
        if isinstance(entry, GalleryStill):
            lines = [entry.name]
            if entry.source_path:
                lines.append(entry.source_path)
            return "\n".join(lines)
        if isinstance(entry, CDLEntry):
            lines = [entry.name]
            if entry.source_path:
                lines.append(entry.source_path)
            r, g, b = entry.slope
            ro, go, bo = entry.offset
            rp, gp, bp = entry.power
            lines.append(f"Slope: {r:.4f}, {g:.4f}, {b:.4f}")
            lines.append(f"Offset: {ro:.4f}, {go:.4f}, {bo:.4f}")
            lines.append(f"Power: {rp:.4f}, {gp:.4f}, {bp:.4f}")
            lines.append(f"Saturation: {entry.sat:.4f}")
            lines.append(f"Style: {entry.style}")
            if not entry.sop_enabled:
                lines.append("SOP: bypassed")
            if not entry.sat_enabled:
                lines.append("SAT: bypassed")
            return "\n".join(lines)
        lines = [entry.name, f"{len(entry.stack.stages)} stage(s):"]
        for i, stage in enumerate(entry.stack.stages, start=1):
            state = "on" if stage.enabled else "OFF"
            lines.append(f"  {i}. {stage.label} [{stage.kind}, {state}, {stage.opacity:.0%}]")
        return "\n".join(lines)

    def _build_menu_button(self):
        """The Stack Memories panel's "..." header menu (see corner_widget
        on PanelFrame, main_window.py) -- panel-level actions only now
        (see populate_menu's own docstring on why the per-item ones moved
        to a right-click context menu instead). Double-clicking a list
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
        they're wired to the exact same methods. Only PANEL-LEVEL actions
        live here now (Grab Still and the one mono Import..., neither of
        which need a selected row) -- Recall/Update Still/Apply CDL to
        Stack/Load as Reference/Rename/Delete/Export... all moved to the
        right-click context menu instead (per Tom: "recall, update, load
        as reference, rename, delete and export in the gallery should be
        on right click of a still/memory" -- extended to the CDL entry
        actions too for the same reason, once CDL folded in here), see
        _on_context_menu_requested."""
        save_action = menu.addAction("Grab Still", self._on_save_clicked)
        save_action.setShortcut(QKeySequence("Ctrl+G"))
        menu.addSeparator()
        import_action = menu.addAction("Import...", self._on_import_clicked)
        import_action.setToolTip(
            "Import a still image, a .ltm (Export... file), or a "
            ".cc/.cdl/.ccc CDL file -- the right kind of gallery entry is "
            "picked from the file's own extension, see _on_import_clicked.")

    def _on_context_menu_requested(self, pos):
        """Right-click menu for a specific row (per Tom, see populate_menu's
        own docstring) -- Recall/Update Still/Apply CDL to Stack/Load as
        Reference/Rename/Delete/Export..., all of which act on whichever
        row was clicked. Each handler already guards for the row's actual
        kind (StackMemory/GalleryStill/CDLEntry) internally -- see e.g.
        _on_recall_clicked -- so showing every action regardless of kind
        here (rather than a different menu per kind) is safe: the wrong-
        kind ones just no-op or explain themselves via QMessageBox, same
        as they always have. No menu when the click lands on empty space
        below the last row (`itemAt` returns None there)."""
        item = self.list_widget.itemAt(pos)
        if item is None:
            return
        self.list_widget.setCurrentItem(item)
        menu = QMenu(self.list_widget)
        menu.addAction("Recall", self._on_recall_clicked)
        menu.addAction("Update Still", self._on_update_clicked)
        menu.addAction("Apply CDL to Stack", self._on_apply_cdl_clicked)
        insert_action = menu.addAction("Insert in Stack", self._on_insert_in_stack_clicked)
        insert_action.setToolTip(
            "Adds this entry's operator(s) into the working Stack, "
            "immediately after whichever stage is currently selected -- "
            "never replaces or overwrites anything already there (unlike "
            "Recall/Apply CDL to Stack above).")
        menu.addAction("Load as Reference", self._on_load_as_reference_clicked)
        menu.addSeparator()
        menu.addAction("Rename...", self._on_rename_clicked)
        menu.addAction("Delete", self._on_delete_clicked)
        menu.addSeparator()
        menu.addAction("Export...", self._on_export_clicked)
        menu.exec(self.list_widget.mapToGlobal(pos))

    # ---- list -----------------------------------------------------------

    def _selected_entry(self):
        """Returns the selected row's own object -- a StackMemory, a
        GalleryStill, or a CDLEntry (see _rows/_refresh_list) -- or None
        if nothing's selected. Callers that only make sense for one kind
        check isinstance themselves (see e.g. _on_recall_clicked)."""
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self._rows):
            return None
        return self._rows[row]

    def _refresh_list(self, keep_selection=None):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        self._rows = (list(self.library.memories) + list(self.gallery_library.stills)
                      + list(self.cdl_library.entries))
        for entry in self._rows:
            if isinstance(entry, GalleryStill):
                item = QListWidgetItem(f"{entry.name}  [reference still]")
            elif isinstance(entry, CDLEntry):
                item = QListWidgetItem(f"{entry.name}  [CDL]")
            else:
                stage_count = len(entry.stack.stages)
                suffix = "stage" if stage_count == 1 else "stages"
                item = QListWidgetItem(f"{entry.name}  ({stage_count} {suffix})")
            # CDLEntry has no thumbnail concept at all (see cdl_library.py)
            # -- getattr rather than a bare attribute access so a CDL row
            # just renders icon-less instead of raising.
            thumbnail_descriptor = getattr(entry, "thumbnail", None)
            png_bytes = media_cache.load_thumbnail(thumbnail_descriptor) if thumbnail_descriptor else None
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
        one -- Recall for a memory, Load as Reference for a still, Apply
        to Stack for a CDL entry (see module docstring)."""
        entry = self._selected_entry()
        if isinstance(entry, GalleryStill):
            self._on_load_as_reference_clicked()
        elif isinstance(entry, CDLEntry):
            self._on_apply_cdl_clicked()
        elif entry is not None:
            self._on_recall_clicked()

    def _on_save_clicked(self):
        if self._stack_getter is None:
            return
        stack = self._stack_getter()
        if not stack.stages:
            QMessageBox.warning(
                self, "Empty stack", "Add stages to the Stack before grabbing a still.")
            return
        name, ok = QInputDialog.getText(
            self, "Grab Still", "Name:", text=self._suggested_still_name())
        name = name.strip()
        if not ok or not name:
            return
        self.save_memory(name, stack)

    def _suggested_still_name(self):
        """"<source name> <date> <time>" (per Tom, see set_source_name_
        getter's own docstring) -- just a starting suggestion for the
        Grab Still name prompt, not enforced. Falls back to a bare
        timestamp if no source_name_getter is wired up or nothing's
        currently loaded, rather than leaving the field empty."""
        source_name = self._source_name_getter() if self._source_name_getter else None
        timestamp = datetime.now().strftime("%Y-%m-%d %H-%M-%S")
        return f"{source_name} {timestamp}" if source_name else timestamp

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
        pulled out so MainWindow's Export dialog "Grab Still"
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
        if not isinstance(entry, (GalleryStill, CDLEntry)) and entry is not None:
            memory = entry
            # StackMemoryLibrary.recall already returns a fresh copy --
            # safe to hand straight to whoever's listening (MainWindow ->
            # set_stack) without this panel needing to know or care what
            # they do with it.
            self.recall_requested.emit(self.library.recall(memory.id))

    def _on_apply_cdl_clicked(self, *_args):
        """A CDLEntry's own primary action (mirrors CDLLibraryPanel's old
        "Apply to Stack" -- see apply_cdl_requested, routed by MainWindow
        into StackPanel.apply_cdl_entry: updates the selected 'cdl' stage
        in place if one's selected, otherwise adds a new one). Silently
        no-ops for any other row kind, same convention _on_recall_clicked
        already uses for a still."""
        entry = self._selected_entry()
        if isinstance(entry, CDLEntry):
            self.apply_cdl_requested.emit(entry)

    def _on_insert_in_stack_clicked(self, *_args):
        """"Insert in Stack" (per Tom, see insert_in_stack_requested's
        own docstring) -- valid for a StackMemory (inserts every one of
        its stages) or a CDLEntry (inserts one new 'cdl' stage); a
        GalleryStill has no operators to insert, so gets an explanatory
        message instead, same "wrong-kind selection" treatment Update
        Still/Export.../Load as Reference already use elsewhere on this
        panel."""
        entry = self._selected_entry()
        if entry is None:
            return
        if isinstance(entry, GalleryStill):
            QMessageBox.information(
                self, "Insert in Stack",
                "Insert in Stack only applies to a saved still (its stages) "
                "or a CDL entry, not an imported reference still.")
            return
        self.insert_in_stack_requested.emit(entry)

    def _on_load_as_reference_clicked(self):
        """A GalleryStill always supports this (load_as_reference_requested,
        routed to sample_image_panel.load_reference_from_path -- a fresh
        disk re-decode). A StackMemory ALSO supports this now (Phase 3
        follow-up, per Tom) IF it has a captured full_still -- decoded
        here from its media_cache.py PNG bytes and routed through a
        separate signal (load_full_still_as_reference_requested) to
        sample_image_panel.load_reference_from_array, since a memory's
        capture has no source file to reload from the way a GalleryStill
        does. A CDLEntry has no image at all -- shows an explanatory
        message instead, same "wrong-kind selection" treatment
        Update Still/Export.../Import... below already use."""
        entry = self._selected_entry()
        if isinstance(entry, GalleryStill):
            self.load_as_reference_requested.emit(entry)
            return
        if isinstance(entry, CDLEntry):
            QMessageBox.information(
                self, "Load as Reference",
                "Load as Reference only applies to a still, not a CDL entry.")
            return
        if entry is not None:
            memory = entry
            png_bytes = media_cache.load_thumbnail(memory.full_still) if memory.full_still else None
            if png_bytes is None:
                QMessageBox.information(
                    self, "Load as Reference",
                    f'"{memory.name}" has no captured still to load -- '
                    "either it was saved before this feature existed, or "
                    "no source image was loaded at save time.")
                return
            array = png_bytes_to_array(png_bytes)
            self.load_full_still_as_reference_requested.emit(array)

    def import_still_from_path(self, path):
        """Decode `path` just long enough to generate a thumbnail -- a
        GalleryStill is disk-referenced only (see that class's own
        docstring), same as a media_library.MediaEntry, so the decoded
        array itself is never kept around."""
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
        # (memories first, then gallery stills, then CDL entries -- see
        # _refresh_list).
        new_index = len(self.library.memories) + len(self.gallery_library.stills) - 1
        self._refresh_list(keep_selection=new_index)

    # ---- CDL (folded in from cdl_library_panel.py, per Tom: "CDL library
    # should also be rolled into the gallery") -------------------------

    def save_cdl_entry(self, name, params):
        """Called by MainWindow in response to StackPanel.
        cdl_save_requested (see stack_panel.py's "Save to CDL Library..."
        button, its label unchanged even though the destination is now
        this Gallery) -- `params` is that stage's own params dict
        (slope/offset/power/sat/style/sop_enabled/sat_enabled). No
        source_filename/source_path -- this entry came from a live
        stage, not an imported file. Mirrors the old CDLLibraryPanel.
        save_entry exactly, just landing in self.cdl_library instead."""
        self.cdl_library.save(
            name,
            params.get("slope", (1.0, 1.0, 1.0)),
            params.get("offset", (0.0, 0.0, 0.0)),
            params.get("power", (1.0, 1.0, 1.0)),
            params.get("sat", 1.0),
            style=params.get("style", "asc"),
            sop_enabled=params.get("sop_enabled", True),
            sat_enabled=params.get("sat_enabled", True),
        )
        new_index = (len(self.library.memories) + len(self.gallery_library.stills)
                     + len(self.cdl_library.entries) - 1)
        self._refresh_list(keep_selection=new_index)

    def import_cdl_from_path(self, path):
        """Shared by the mono "Import..." action (dispatched by
        extension, see _on_import_clicked), a CDL file dragged in from
        the Browser panel (_GalleryDropListWidget), and
        BrowserPanel.cdl_file_selected on double-click (see
        main_window.py's wiring) -- all three land here so behavior
        can't drift between them. Mirrors the old CDLLibraryPanel.
        import_from_path exactly, just landing in self.cdl_library
        instead."""
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
            self.cdl_library.add_entry(entry)
            last_index = (len(self.library.memories) + len(self.gallery_library.stills)
                          + len(self.cdl_library.entries) - 1)
        self._refresh_list(keep_selection=last_index)

    def _on_update_clicked(self):
        entry = self._selected_entry()
        if isinstance(entry, (GalleryStill, CDLEntry)):
            QMessageBox.information(
                self, "Update Still",
                "Update Still only applies to a grabbed still, not an "
                "imported reference still or a CDL entry.")
            return
        memory = entry
        if memory is None or self._stack_getter is None:
            return
        reply = QMessageBox.question(
            self, "Update Still",
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
        if isinstance(entry, GalleryStill):
            title = "Rename Reference Still"
        elif isinstance(entry, CDLEntry):
            title = "Rename CDL"
        else:
            title = "Rename Still"
        name, ok = QInputDialog.getText(self, title, "Name:", text=entry.name)
        name = name.strip()
        if not ok or not name:
            return
        if isinstance(entry, GalleryStill):
            self.gallery_library.rename(entry.id, name)
        elif isinstance(entry, CDLEntry):
            self.cdl_library.rename(entry.id, name)
        else:
            self.library.rename(entry.id, name)
        self._refresh_list(keep_selection=self.list_widget.currentRow())

    def _on_delete_clicked(self):
        entry = self._selected_entry()
        if entry is None:
            return
        if isinstance(entry, GalleryStill):
            noun = "reference still"
        elif isinstance(entry, CDLEntry):
            noun = "CDL entry"
        else:
            noun = "still"
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
        if isinstance(entry, CDLEntry):
            self.cdl_library.remove(entry.id)
            self._refresh_list()
            return
        self.library.remove(entry.id)
        self._refresh_list()

    # ---- export/import (see stack_memory_file_io.py) -- MEMORY only,
    # a GalleryStill has no analogous single-file format (module docstring)

    def _on_export_clicked(self):
        entry = self._selected_entry()
        if isinstance(entry, (GalleryStill, CDLEntry)):
            QMessageBox.information(
                self, "Export",
                "Export... only applies to a grabbed still, not an "
                "imported reference still or a CDL entry.")
            return
        memory = entry
        if memory is None:
            return
        default_name = f"{memory.name}.ltm"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Still (.ltm)", default_name, MEMORY_FILE_FILTER)
        if not path:
            return
        try:
            stack_memory_file_io.export_memory(memory, path)
        except Exception as e:  # noqa: BLE001 -- surface any write failure to the user
            QMessageBox.critical(self, "Failed to export still", str(e))
            return

    def _on_import_clicked(self):
        """One mono Import... action (per Tom, replacing the previous
        separate Import Still.../Import CDL.../Import... trio) -- a
        single file dialog whose filter covers all three kinds this
        panel understands, dispatched to the right import path by the
        chosen file's own extension rather than asking the user to
        pick the right button first."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Import", "", IMPORT_FILE_FILTER)
        if not path:
            return
        suffix = Path(path).suffix.lower()
        if suffix in CDL_EXTENSIONS:
            self.import_cdl_from_path(path)
        elif suffix == ".ltm":
            self.import_memory_from_path(path)
        elif suffix in STILL_EXTENSIONS:
            self.import_still_from_path(path)
        else:
            QMessageBox.warning(
                self, "Unrecognized file",
                f"Don't know how to import this file:\n{path}\n\n"
                "Expected a still image, a .ltm file, or a .cc/.cdl/.ccc "
                "CDL file.")

    def import_memory_from_path(self, path):
        try:
            memory, warnings = stack_memory_file_io.import_memory(path)
        except Exception as e:  # noqa: BLE001 -- surface any read/parse failure to the user
            QMessageBox.critical(self, "Failed to import still", str(e))
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

    def get_cdl_library(self):
        return self.cdl_library

    def load_cdl_library(self, cdl_library):
        """Replace the whole CDLLibrary wholesale -- same pattern as
        load_library/load_gallery_library. Session persistence itself is
        UNCHANGED (see session_io.py's cdl_library_to_session_list/
        _from_session_list, still their own independent "cdl_library"
        session key) -- only which panel OWNS the library moved, per Tom
        folding CDL Library into this Gallery; a session saved before
        that move loads here exactly the same as it loaded into the old
        standalone CDLLibraryPanel."""
        self.cdl_library = cdl_library
        self._refresh_list()
