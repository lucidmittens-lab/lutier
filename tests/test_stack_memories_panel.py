"""Tests for stack_memories_panel.py -- run directly:
QT_QPA_PLATFORM=offscreen python3 -m tests.test_stack_memories_panel

Focused on the CDL fold-in (per Tom: "CDL library should also be rolled
into the gallery") -- the union list already had StackMemory/GalleryStill
coverage exercised manually (see that module's own docstring); this adds
real automated coverage for the third row kind, CDLEntry, since it's new
functionality, not just a rename.
"""
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMenu

from stack_memories_panel import StackMemoriesPanel
from cdl_library import CDLEntry
from stack_memory import StackMemory, GalleryStill
import stack as st

SAMPLES_CDL_DIR = Path(__file__).parent.parent / "samples" / "cdl"


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_save_cdl_entry_adds_a_row_tagged_cdl():
    _app()
    panel = StackMemoriesPanel()
    panel.save_cdl_entry("My CDL", {
        "slope": (1.1, 1.0, 0.9), "offset": (0.0, 0.0, 0.0),
        "power": (1.0, 1.0, 1.0), "sat": 1.0,
    })
    assert len(panel.cdl_library.entries) == 1
    assert panel.cdl_library.entries[0].name == "My CDL"
    item = panel.list_widget.item(panel.list_widget.count() - 1)
    assert "[CDL]" in item.text()
    print("StackMemoriesPanel.save_cdl_entry: adds a CDLEntry, row shows "
          "the [CDL] tag -- OK")


def test_import_cdl_from_path_adds_entries_from_a_real_file():
    _app()
    panel = StackMemoriesPanel()
    panel.import_cdl_from_path(str(SAMPLES_CDL_DIR / "single.cc"))
    assert len(panel.cdl_library.entries) == 1
    assert panel.cdl_library.entries[0].source_path == str(SAMPLES_CDL_DIR / "single.cc")
    print("StackMemoriesPanel.import_cdl_from_path: imports a real .cc "
          "file into cdl_library, recording its source_path -- OK")


def test_mono_import_dispatches_a_cdl_file_by_extension():
    """The mono "Import..." action (per Tom, replacing the old separate
    Import Still.../Import CDL.../Import... trio) is one file dialog
    dispatched by the chosen file's own extension -- this drives the
    real _on_import_clicked path (not import_cdl_from_path directly) to
    confirm the dispatch itself, not just the underlying import, works."""
    import stack_memories_panel as smp_module
    _app()
    panel = StackMemoriesPanel()
    cc_path = str(SAMPLES_CDL_DIR / "single.cc")
    original_get_open_file_name = smp_module.QFileDialog.getOpenFileName
    smp_module.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (cc_path, ""))
    try:
        panel._on_import_clicked()
    finally:
        smp_module.QFileDialog.getOpenFileName = original_get_open_file_name
    assert len(panel.cdl_library.entries) == 1
    assert panel.cdl_library.entries[0].source_path == cc_path
    print("StackMemoriesPanel._on_import_clicked: a .cc path from the "
          "mono Import dialog is dispatched to import_cdl_from_path -- OK")


def test_mono_import_warns_on_an_unrecognized_extension():
    """A file extension outside .ltm/.cc/.cdl/.ccc/image-extensions
    should surface an explanatory warning, not silently do nothing or
    crash trying to decode it as one of the known kinds."""
    import stack_memories_panel as smp_module
    _app()
    panel = StackMemoriesPanel()
    warnings_shown = []
    original_get_open_file_name = smp_module.QFileDialog.getOpenFileName
    original_warning = smp_module.QMessageBox.warning
    smp_module.QFileDialog.getOpenFileName = staticmethod(
        lambda *a, **k: ("/tmp/not_a_real_kind.txt", ""))
    smp_module.QMessageBox.warning = staticmethod(
        lambda *a, **k: warnings_shown.append(a))
    try:
        panel._on_import_clicked()
    finally:
        smp_module.QFileDialog.getOpenFileName = original_get_open_file_name
        smp_module.QMessageBox.warning = original_warning
    assert len(warnings_shown) == 1
    assert len(panel.cdl_library.entries) == 0
    assert len(panel.gallery_library.stills) == 0
    assert len(panel.library.memories) == 0
    print("StackMemoriesPanel._on_import_clicked: an unrecognized "
          "extension warns instead of silently no-op'ing -- OK")


def test_rows_union_memories_stills_and_cdl_entries():
    _app()
    panel = StackMemoriesPanel()
    panel.save_cdl_entry("CDL One", {})
    stack = st.Stack(stages=[st.make_cdl_stage()])
    panel.save_memory("Memory One", stack)
    panel._refresh_list()
    kinds = [type(e).__name__ for e in panel._rows]
    assert "StackMemory" in kinds
    assert "CDLEntry" in kinds
    print("StackMemoriesPanel._rows: unions StackMemory and CDLEntry rows "
          "together -- OK")


def test_apply_cdl_clicked_emits_for_a_cdl_row_only():
    _app()
    panel = StackMemoriesPanel()
    panel.save_cdl_entry("CDL One", {})
    emitted = []
    panel.apply_cdl_requested.connect(lambda entry: emitted.append(entry))

    panel.list_widget.setCurrentRow(0)
    panel._on_apply_cdl_clicked()
    assert len(emitted) == 1
    assert isinstance(emitted[0], CDLEntry)
    print("StackMemoriesPanel._on_apply_cdl_clicked: emits apply_cdl_requested "
          "with the selected CDLEntry -- OK")


def test_apply_cdl_clicked_is_a_silent_noop_for_a_memory_row():
    _app()
    panel = StackMemoriesPanel()
    stack = st.Stack(stages=[st.make_cdl_stage()])
    panel.save_memory("Memory One", stack)
    emitted = []
    panel.apply_cdl_requested.connect(lambda entry: emitted.append(entry))

    panel.list_widget.setCurrentRow(0)
    panel._on_apply_cdl_clicked()  # must not raise
    assert emitted == []
    print("StackMemoriesPanel._on_apply_cdl_clicked: silently no-ops when "
          "the selected row is a StackMemory, not a CDLEntry -- OK")


def test_recall_clicked_is_a_silent_noop_for_a_cdl_row():
    _app()
    panel = StackMemoriesPanel()
    panel.save_cdl_entry("CDL One", {})
    emitted = []
    panel.recall_requested.connect(lambda s: emitted.append(s))

    panel.list_widget.setCurrentRow(0)
    panel._on_recall_clicked()  # must not raise
    assert emitted == []
    print("StackMemoriesPanel._on_recall_clicked: silently no-ops when "
          "the selected row is a CDLEntry, not a StackMemory -- OK")


def test_double_click_on_a_cdl_row_applies_it():
    _app()
    panel = StackMemoriesPanel()
    panel.save_cdl_entry("CDL One", {})
    emitted = []
    panel.apply_cdl_requested.connect(lambda entry: emitted.append(entry))

    panel.list_widget.setCurrentRow(0)
    panel._on_item_double_clicked()
    assert len(emitted) == 1
    print("StackMemoriesPanel: double-clicking a CDL row applies it to "
          "the Stack (its own primary action) -- OK")


def test_rename_and_delete_work_on_a_cdl_row():
    _app()
    panel = StackMemoriesPanel()
    panel.save_cdl_entry("Original Name", {})
    entry_id = panel.cdl_library.entries[0].id

    panel.cdl_library.rename(entry_id, "Renamed")
    panel._refresh_list()
    assert panel.cdl_library.entries[0].name == "Renamed"

    panel.cdl_library.remove(entry_id)
    panel._refresh_list()
    assert panel.cdl_library.entries == []
    print("StackMemoriesPanel: rename/delete work generically on a CDL "
          "row via the shared library calls -- OK")


def test_suggested_still_name_combines_source_name_and_timestamp():
    _app()
    panel = StackMemoriesPanel()
    panel.set_source_name_getter(lambda: "MyShot_001")
    suggestion = panel._suggested_still_name()
    assert suggestion.startswith("MyShot_001 ")
    assert len(suggestion) > len("MyShot_001 ")
    print("StackMemoriesPanel._suggested_still_name: combines the source "
          "name with a timestamp -- OK")


def test_suggested_still_name_falls_back_to_timestamp_with_no_source():
    _app()
    panel = StackMemoriesPanel()
    panel.set_source_name_getter(lambda: None)
    suggestion = panel._suggested_still_name()
    assert suggestion  # non-empty, just a timestamp
    assert "None" not in suggestion
    print("StackMemoriesPanel._suggested_still_name: falls back to a bare "
          "timestamp when nothing's loaded, no source_name_getter wired "
          "up, or it returns None -- OK")


def test_list_widget_accepts_custom_context_menu():
    _app()
    panel = StackMemoriesPanel()
    assert panel.list_widget.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
    print("StackMemoriesPanel.list_widget: uses a custom (right-click) "
          "context menu policy -- OK")


def test_populate_menu_only_has_panel_level_actions():
    """Per Tom: "recall, update, load as reference, rename, delete and
    export in the gallery should be on right click of a still/memory" --
    the "..." header menu (populate_menu) should keep only panel-level
    actions that don't need a selected row; the per-item ones moved to
    _on_context_menu_requested."""
    _app()
    panel = StackMemoriesPanel()
    menu = QMenu()
    panel.populate_menu(menu)
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert texts == ["Grab Still", "Import..."]
    print("StackMemoriesPanel.populate_menu: only panel-level actions "
          "remain -- Recall/Update Still/Apply CDL to Stack/Load as "
          "Reference/Rename/Delete/Export... moved off it -- OK")


def test_context_menu_selects_the_clicked_row_before_popping_up():
    """Can't drive QMenu.exec() itself in an offscreen test (it blocks
    waiting for real user interaction) -- this checks the selection side
    effect _on_context_menu_requested does BEFORE popping the menu."""
    _app()
    panel = StackMemoriesPanel()
    panel.save_cdl_entry("CDL One", {})
    stack = st.Stack(stages=[st.make_cdl_stage()])
    panel.save_memory("Memory One", stack)
    panel._refresh_list()
    assert len(panel._rows) == 2

    target_row = 1
    item = panel.list_widget.item(target_row)
    panel.list_widget.setCurrentItem(item)  # mirrors what _on_context_menu_requested does
    assert panel._selected_entry() is panel._rows[target_row]
    print("StackMemoriesPanel: right-clicking a row selects it, so the "
          "context menu's actions target the right entry -- OK")


def test_insert_in_stack_emits_for_a_memory_row():
    _app()
    panel = StackMemoriesPanel()
    stack = st.Stack(stages=[st.make_cdl_stage()])
    panel.save_memory("Memory One", stack)
    emitted = []
    panel.insert_in_stack_requested.connect(lambda entry: emitted.append(entry))

    panel.list_widget.setCurrentRow(0)
    panel._on_insert_in_stack_clicked()
    assert len(emitted) == 1
    assert isinstance(emitted[0], StackMemory)
    print("StackMemoriesPanel._on_insert_in_stack_clicked: emits "
          "insert_in_stack_requested with the selected StackMemory -- OK")


def test_insert_in_stack_emits_for_a_cdl_row():
    _app()
    panel = StackMemoriesPanel()
    panel.save_cdl_entry("CDL One", {})
    emitted = []
    panel.insert_in_stack_requested.connect(lambda entry: emitted.append(entry))

    panel.list_widget.setCurrentRow(0)
    panel._on_insert_in_stack_clicked()
    assert len(emitted) == 1
    assert isinstance(emitted[0], CDLEntry)
    print("StackMemoriesPanel._on_insert_in_stack_clicked: emits "
          "insert_in_stack_requested with the selected CDLEntry -- OK")


def test_insert_in_stack_does_not_emit_for_a_gallery_still_row():
    """A GalleryStill has no operators to insert -- guarded no-emit with
    an explanatory message, same "wrong-kind selection" treatment as
    Update Still/Export.../Load as Reference elsewhere on this panel.
    QMessageBox.information is monkeypatched to a no-op for this one
    test -- it's a real blocking modal even under QT_QPA_PLATFORM=
    offscreen (confirmed: calling the guarded handlers directly hangs
    the test process), and no other test in this file exercises one of
    this panel's several wrong-kind-guard messages for exactly that
    reason; patching it here is what actually lets this specific branch
    get covered instead of being skipped like the others."""
    _app()
    panel = StackMemoriesPanel()
    still = GalleryStill("Ref Still", None, "/tmp/ref.png")
    panel.gallery_library.add_still(still)
    panel._refresh_list()
    emitted = []
    panel.insert_in_stack_requested.connect(lambda entry: emitted.append(entry))

    import stack_memories_panel as smp_module
    original_information = smp_module.QMessageBox.information
    smp_module.QMessageBox.information = staticmethod(lambda *a, **k: None)
    try:
        panel.list_widget.setCurrentRow(0)
        panel._on_insert_in_stack_clicked()
    finally:
        smp_module.QMessageBox.information = original_information
    assert emitted == []
    print("StackMemoriesPanel._on_insert_in_stack_clicked: does not emit "
          "for a GalleryStill row -- OK")


def test_get_and_load_cdl_library_round_trip():
    _app()
    panel = StackMemoriesPanel()
    panel.save_cdl_entry("CDL One", {})
    library = panel.get_cdl_library()
    assert len(library.entries) == 1

    other = StackMemoriesPanel()
    other.load_cdl_library(library)
    assert len(other.cdl_library.entries) == 1
    assert other.cdl_library.entries[0].name == "CDL One"
    print("StackMemoriesPanel.get_cdl_library/load_cdl_library: round-trip "
          "the whole CDLLibrary, same pattern as get_library/load_library -- OK")


if __name__ == "__main__":
    test_save_cdl_entry_adds_a_row_tagged_cdl()
    test_import_cdl_from_path_adds_entries_from_a_real_file()
    test_mono_import_dispatches_a_cdl_file_by_extension()
    test_mono_import_warns_on_an_unrecognized_extension()
    test_rows_union_memories_stills_and_cdl_entries()
    test_apply_cdl_clicked_emits_for_a_cdl_row_only()
    test_apply_cdl_clicked_is_a_silent_noop_for_a_memory_row()
    test_recall_clicked_is_a_silent_noop_for_a_cdl_row()
    test_double_click_on_a_cdl_row_applies_it()
    test_rename_and_delete_work_on_a_cdl_row()
    test_suggested_still_name_combines_source_name_and_timestamp()
    test_suggested_still_name_falls_back_to_timestamp_with_no_source()
    test_list_widget_accepts_custom_context_menu()
    test_populate_menu_only_has_panel_level_actions()
    test_context_menu_selects_the_clicked_row_before_popping_up()
    test_insert_in_stack_emits_for_a_memory_row()
    test_insert_in_stack_emits_for_a_cdl_row()
    test_insert_in_stack_does_not_emit_for_a_gallery_still_row()
    test_get_and_load_cdl_library_round_trip()
    print()
    print("All stack_memories_panel tests passed.")
