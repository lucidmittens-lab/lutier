"""Tests for media_grid.py and the Media Libraries panel's List | Grid
views -- run directly: QT_QPA_PLATFORM=offscreen python3 -m tests.test_media_grid
"""
import sys

from PySide6.QtCore import QItemSelectionModel, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from media_grid import CARD_WIDTH, GAP, MARGIN, GridItem, MediaGrid
from media_library import MediaEntry
from media_panel import COLUMN_THUMBNAIL, GRID_VIEW, LIST_VIEW, MediaPanel


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _grid_with(n, width):
    grid = MediaGrid()
    grid.resize(width, 400)
    grid.set_items([GridItem(row=i, name=f"clip {i}") for i in range(n)])
    return grid


def test_cards_wrap_to_the_width_and_the_height_follows():
    _app()
    three_wide = MARGIN * 2 + 3 * CARD_WIDTH + 2 * GAP
    grid = _grid_with(7, three_wide)
    assert grid.card_rect(2).top() == grid.card_rect(0).top()
    assert grid.card_rect(3).left() == grid.card_rect(0).left(), "4th card starts row 2"
    assert grid.minimumHeight() > 2 * grid.card_rect(0).height(), "three rows of cards"
    assert not grid.grab().isNull()
    print("media grid: cards wrap to the width; height follows the rows -- OK")


def test_click_ctrl_click_and_shift_click_selection():
    _app()
    grid = _grid_with(6, 2000)
    seen = []
    grid.selection_changed.connect(seen.append)
    click = lambda i, mod=Qt.KeyboardModifier.NoModifier: QTest.mouseClick(
        grid, Qt.MouseButton.LeftButton, mod, grid.card_rect(i).center().toPoint())
    click(1)
    assert grid.selected_rows() == [1]
    click(3, Qt.KeyboardModifier.ControlModifier)
    assert grid.selected_rows() == [1, 3]
    click(3, Qt.KeyboardModifier.ControlModifier)
    assert grid.selected_rows() == [1], "ctrl-click toggles (and moves the anchor, like Qt lists)"
    click(1)
    click(4, Qt.KeyboardModifier.ShiftModifier)
    assert grid.selected_rows() == [1, 2, 3, 4], "shift = range from the anchor"
    assert seen[-1] == [1, 2, 3, 4]
    # Qt takes its global keyboard-modifier state from the last input
    # event, so a Shift-click leaves "Shift held" for every later test in
    # this process (selectRow() and friends honour it). A plain click on
    # empty space resets it.
    QTest.mouseClick(grid, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(1, 1))
    assert QApplication.keyboardModifiers() == Qt.KeyboardModifier.NoModifier
    print("media grid: click / ctrl-click / shift-click selection -- OK")


def test_panel_defaults_to_grid_and_grid_selection_drives_the_table():
    _app()
    panel = MediaPanel()
    for i, name in enumerate(("A001", "B002", "C003")):
        panel.library.add_entry(MediaEntry(name, f"/media/{name}.mxf", media_kind="video",
                                           width=1920, height=1080, frame_rate=24.0,
                                           frame_count=24 * (i + 1), duration=float(i + 1)))
    panel._refresh_table()
    assert panel.view_mode() == GRID_VIEW
    assert [item.name for item in panel.grid.items()] == ["A001", "B002", "C003"]
    assert "1920x1080" in panel.grid.items()[0].details

    panel._select_table_rows([0, 2])
    assert [e.name for e in panel._selected_entries()] == ["A001", "C003"]
    assert panel._selected_entry().name == "A001", "current row = the first selected"

    # list -> grid mirroring. Through the selection model with explicit
    # flags, not selectRow(): selectRow honours "held" modifiers, and the
    # Ctrl/Shift clicks in the test above can leave QTest's modifier state
    # behind in this process.
    model = panel.table.selectionModel()
    model.select(panel.table.model().index(1, 0),
                 QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)
    assert panel.grid.selected_rows() == [1]

    panel.filter_edit.setText("C00")
    assert [item.name for item in panel.grid.items()] == ["C003"], "the filter hides cards too"
    panel.set_view_mode(LIST_VIEW)
    assert panel.view_stack.currentWidget() is panel.table
    assert panel.table.item(0, COLUMN_THUMBNAIL) is not None
    print("media panel: Grid by default, selection shared with the table, filter applies -- OK")


if __name__ == "__main__":
    test_cards_wrap_to_the_width_and_the_height_follows()
    test_click_ctrl_click_and_shift_click_selection()
    test_panel_defaults_to_grid_and_grid_selection_drives_the_table()
    print("\nAll media grid tests passed.")
