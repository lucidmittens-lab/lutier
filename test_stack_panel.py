"""Tests for stack_panel.py -- run directly: python3 test_stack_panel.py

Currently just _ElidingLabel, the QLabel subclass _StageRowWidget uses so
a stage row's label truncates ("…") to fit whatever width it's actually
given rather than forcing the row to grow past the panel's edge or
pushing the delete button off past it (see that class's own docstring).
Everything else in this module needs a full StackPanel/real Stack/signal
wiring to exercise meaningfully and has no dedicated test file of its own
yet -- this one is deliberately narrow, not a stand-in for that.

Requires a real (if offscreen) QApplication -- same headless-Qt setup as
every other GUI-touching verification in this project (see RUNNING.md).
Every label below is .show()n right after construction, before any
.resize() call: Qt does not deliver resizeEvent to a widget that's never
been shown (confirmed directly -- .resize() on an unshown widget updates
.width() immediately but never fires resizeEvent), unlike a real
_StageRowWidget, which always lives inside an actually-visible window --
so this is a test-only requirement, not something the class itself needs.
"""
import sys

from PySide6.QtCore import QPoint
from PySide6.QtGui import QDragLeaveEvent
from PySide6.QtWidgets import QApplication, QMainWindow

import stack as st
from stack_panel import (
    _ElidingLabel, _StackListWidget, StackPanel, _stage_kind_badge_text,
    _ForkBranchGridRowWidget, _StageRowWidget,
)


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_short_text_that_fits_is_not_elided_and_has_no_tooltip():
    _app()
    label = _ElidingLabel("Short")
    label.show()
    label.resize(500, 20)
    assert label.text() == "Short"
    assert label.toolTip() == ""
    print("_ElidingLabel: text that fits at the given width is shown in "
          "full, no tooltip -- OK")


def test_long_text_at_narrow_width_is_elided_with_ellipsis_and_gets_a_tooltip():
    _app()
    full = "A very long stage label that will not fit in a narrow row"
    label = _ElidingLabel(full)
    label.show()
    label.resize(60, 20)
    assert label.text() != full
    assert label.text().endswith("…")
    assert label.toolTip() == full
    print("_ElidingLabel: text too wide for the given width is elided "
          "with '…', full text available as a tooltip -- OK")


def test_full_text_survives_resizing_back_and_forth():
    _app()
    full = "Another fairly long stage label for this test"
    label = _ElidingLabel(full)
    label.show()
    label.resize(40, 20)
    assert label.text() != full  # elided at narrow width

    label.resize(600, 20)
    assert label.text() == full  # room enough now -- shown in full
    assert label.toolTip() == ""

    label.resize(40, 20)
    assert label.text() != full  # narrow again -- elided again
    assert label.full_text() == full, "full_text() must always return the original, unelided string"
    print("_ElidingLabel: re-elides correctly across repeated resizes, "
          "full_text() always returns the original string -- OK")


def test_set_text_updates_the_full_text_not_just_the_displayed_text():
    _app()
    label = _ElidingLabel("Original")
    label.show()
    label.resize(20, 20)
    label.setText("A brand new, much longer replacement label")
    assert label.full_text() == "A brand new, much longer replacement label"
    print("_ElidingLabel.setText: updates the underlying full text (not "
          "just what's currently displayed) -- OK")


# ---- _StackListWidget drag-to-reorder drop-position indicator ------------
# Per Tom: there should be a visual indicator of where a drag-to-reorder
# will land. Qt's own built-in drop indicator (setDropIndicatorShown)
# never actually painted anything here -- this class's dragMoveEvent is a
# full override that never calls super(), which is exactly what would
# normally drive that built-in indicator -- so it's hand-painted instead
# (see _StackListWidget.paintEvent), driven by _set_drop_indicator.
# Full native drag-and-drop can't be synthesized in a headless test
# without a lot of Qt plumbing for little extra confidence; these tests
# exercise the actual state machine directly (_drop_row's row-from-
# position math, _set_drop_indicator's dedup, dragLeaveEvent's
# cleanup) -- the same calls a real drag sequence drives.

def _list_with_three_rows():
    _app()
    lst = _StackListWidget(panel=None)
    lst.show()
    for label in ("First", "Second", "Third"):
        lst.addItem(label)
    lst.resize(200, 200)
    return lst


def test_drop_row_upper_half_of_a_row_inserts_before_it():
    lst = _list_with_three_rows()
    rect = lst.visualItemRect(lst.item(1))
    pos = QPoint(rect.center().x(), rect.top() + 1)
    assert lst._drop_row(pos) == 1
    print("_StackListWidget._drop_row: a position in a row's upper half "
          "inserts BEFORE that row -- OK")


def test_drop_row_lower_half_of_a_row_inserts_after_it():
    lst = _list_with_three_rows()
    rect = lst.visualItemRect(lst.item(1))
    pos = QPoint(rect.center().x(), rect.bottom() - 1)
    assert lst._drop_row(pos) == 2
    print("_StackListWidget._drop_row: a position in a row's lower half "
          "inserts AFTER that row -- OK")


def test_drop_row_below_every_row_appends_at_the_end():
    lst = _list_with_three_rows()
    pos = QPoint(10, 10_000)  # far below the last real row
    assert lst._drop_row(pos) == lst.count()
    print("_StackListWidget._drop_row: a position below every row "
          "appends at the end -- OK")


def test_set_drop_indicator_dedupes_unchanged_row_and_branch():
    lst = _list_with_three_rows()
    calls = []
    lst.viewport().update = lambda: calls.append(True)

    lst._set_drop_indicator(1, "a")
    assert lst._drop_indicator_row == 1
    assert len(calls) == 1, "setting a NEW row must trigger exactly one repaint"

    lst._set_drop_indicator(1, "a")
    assert len(calls) == 1, "setting the SAME (row, branch) again must not trigger another repaint"

    lst._set_drop_indicator(1, "b")
    assert len(calls) == 2, "the SAME row but a DIFFERENT branch must still trigger a repaint"

    lst._set_drop_indicator(2, "b")
    assert len(calls) == 3, "a genuinely different row must trigger another repaint"
    print("_StackListWidget._set_drop_indicator: only repaints when the "
          "target row OR branch actually changes -- OK")


def test_drag_leave_clears_the_indicator():
    lst = _list_with_three_rows()
    lst._set_drop_indicator(1, "a")
    assert lst._drop_indicator_row == 1

    lst.dragLeaveEvent(QDragLeaveEvent())

    assert lst._drop_indicator_row is None
    assert lst._drop_indicator_branch is None
    print("_StackListWidget.dragLeaveEvent: clears the drop indicator "
          "(e.g. dragging back out, or Escape cancelling the drag) -- OK")


def test_stack_list_widget_is_scoped_out_of_the_generic_item_selected_highlight():
    """REAL BUG, found by Tom (2026-08-12): each row already draws its own
    selection cue (a border-color change on its rounded card, see
    QFrame#stackStageRow in style.py), but the GENERIC QListWidget::item:
    selected rule (used by every other list in this app -- Browser, LUT/
    CDL Library, Stack Memories) was ALSO painting its own sharp-cornered
    highlight rectangle behind/around every row here too, competing with
    the card's own rounded border. _StackListWidget must carry its own
    objectName so style.py can scope that generic rule OFF just for this
    one list (see QListWidget#stackListWidget::item:selected there),
    without touching every other list's own selection highlight."""
    lst = _list_with_three_rows()
    assert lst.objectName() == "stackListWidget"
    import style
    assert "QListWidget#stackListWidget::item:selected" in style.STYLESHEET
    print("_StackListWidget: carries its own objectName so the generic "
          "QListWidget::item:selected rectangle can be scoped off just "
          "for this list -- OK")


def test_row_hover_uses_its_own_tertiary_color_not_the_selected_accent():
    """Hover went through three rounds (2026-08-12) before landing on its
    own dedicated color -- see style.py's QFrame#stackStageRow:hover
    comment for the full account. Round two used palette(highlight), the
    SAME color selection uses, which per Tom made a merely-hovered row
    indistinguishable from an actually-selected one. This locks in round
    three: hover uses palette(accent) (style.get_tertiary_color(), a
    33%-darker derivative of the accent), and a SELECTED row being
    hovered stays at full accent strength rather than demoting to the
    darker tertiary."""
    import style
    from PySide6.QtGui import QColor
    accent = QColor(style.get_accent_color())
    tertiary = QColor(style.get_tertiary_color())
    assert tertiary != accent, "the tertiary color must be genuinely distinct from the accent itself"
    assert tertiary.lightness() < accent.lightness(), "get_tertiary_color() must be DARKER than the accent"

    assert "QFrame#stackStageRow:hover" in style.STYLESHEET
    hover_rule_start = style.STYLESHEET.index("QFrame#stackStageRow:hover")
    hover_rule_body = style.STYLESHEET[hover_rule_start:hover_rule_start + 200]
    assert "palette(accent)" in hover_rule_body, "plain hover must use the tertiary color, not palette(highlight)"

    selected_hover_start = style.STYLESHEET.index('QFrame#stackStageRow[stageSelected="true"]:hover')
    selected_hover_body = style.STYLESHEET[selected_hover_start:selected_hover_start + 200]
    assert "palette(highlight)" in selected_hover_body, (
        "a SELECTED row being hovered must stay at full accent strength, not the darker hover-only tertiary")
    print("Stack row hover: uses its own tertiary color, distinct from the "
          "selected-row accent, except a selected row stays full-accent "
          "even when hovered -- OK")


# ---- Targeted row updates (perf fix, per Tom's "snappier and more ------
# interactive" pass, 2026-08-12) -------------------------------------------
# _refresh_list() used to run on every single live param edit (a CDL
# wheel/spin drag, an opacity scrub, an enable toggle click) -- a full
# QListWidget clear-and-rebuild-every-card operation, the measured cause
# of "laggy" live editing (see _StageRowWidget's own docstring, "SNAPPIER/
# MORE INTERACTIVE PASS"). These tests confirm the fix actually landed:
# the SAME _StageRowWidget instance (not a freshly rebuilt one) is what
# StackPanel._stage_row_widgets holds after a live edit, with its content
# updated in place -- object identity is the direct, load-bearing signal
# here, not just "does the right text end up on screen eventually."

def _panel_with_one_cdl_stage():
    _app()
    panel = StackPanel()
    panel._on_add_cdl_clicked()
    stage = panel.stack.stages[0]
    return panel, stage


def test_cdl_live_edit_updates_the_existing_row_widget_in_place():
    panel, stage = _panel_with_one_cdl_stage()
    widget_before = panel._stage_row_widgets[stage.id]

    panel._cdl_sop_spins["slope"]["R"].setValue(1.5)
    panel._apply_cdl_live()

    widget_after = panel._stage_row_widgets[stage.id]
    assert widget_after is widget_before, (
        "a live CDL param edit must update the existing card in place, not rebuild the list")
    assert "1.50" in widget_after.label_widget.full_text()
    assert stage.params["slope"][0] == 1.5
    print("StackPanel._apply_cdl_live: updates the existing row widget in "
          "place instead of rebuilding the whole list -- OK")


def test_opacity_spin_lives_on_the_row_and_edits_that_stage_in_place():
    """Opacity moved IN-ROW (per Tom, 2026-08-12 round two) -- each card
    now owns its own opacity_spin, wired to StackPanel._on_row_opacity_
    changed for THAT stage specifically, not a panel-level control tied
    to "whichever stage happens to be selected." """
    panel, stage = _panel_with_one_cdl_stage()
    widget_before = panel._stage_row_widgets[stage.id]

    widget_before.opacity_spin.setValue(50)

    widget_after = panel._stage_row_widgets[stage.id]
    assert widget_after is widget_before, (
        "a live opacity scrub must update the existing card in place, not rebuild the list")
    assert stage.opacity == 0.5
    assert widget_after.opacity_spin.value() == 50
    print("_StageRowWidget.opacity_spin: lives on the row and edits that "
          "row's own stage in place, no full rebuild -- OK")


def test_enabled_toggle_updates_the_existing_row_widget_in_place():
    panel, stage = _panel_with_one_cdl_stage()
    widget_before = panel._stage_row_widgets[stage.id]
    assert stage.enabled is True
    assert widget_before.enabled_button.text() == "●"

    panel._on_row_enabled_clicked(stage.id)

    widget_after = panel._stage_row_widgets[stage.id]
    assert widget_after is widget_before, (
        "toggling enabled must update the existing card in place, not rebuild the list")
    assert stage.enabled is False
    assert widget_after.enabled_button.text() == "○"
    print("StackPanel._on_row_enabled_clicked: updates the existing row "
          "widget in place instead of rebuilding the whole list -- OK")


def test_stage_kind_badge_text_known_and_unknown_kinds():
    assert _stage_kind_badge_text("cdl") == "CDL"
    assert _stage_kind_badge_text("grading_rgb_curve") == "RGB"
    assert _stage_kind_badge_text("some_future_kind") == "SOM"
    print("_stage_kind_badge_text: known kinds get their own short badge, "
          "unknown kinds fall back to a truncated upper-case guess -- OK")


def test_cdl_row_widget_shows_the_cdl_badge():
    panel, stage = _panel_with_one_cdl_stage()
    widget = panel._stage_row_widgets[stage.id]
    assert widget.badge_widget is not None
    assert widget.badge_widget.text() == "CDL"
    print("_StageRowWidget: a full-width row shows its stage kind's badge -- OK")


def test_row_label_has_no_leading_stage_number_prefix():
    """Round two, per Tom: "lose the number before the operator name" --
    a full-width row's label must be the stage's own label verbatim, not
    prefixed with its 1-based position in the stack."""
    panel, stage = _panel_with_one_cdl_stage()
    widget = panel._stage_row_widgets[stage.id]
    assert widget.label_widget.full_text() == stage.label
    assert not widget.label_widget.full_text().startswith("1.")

    panel._on_add_cdl_clicked()  # a second stage, so a stale "2. " prefix would be obvious too
    second_stage = panel.stack.stages[1]
    second_widget = panel._stage_row_widgets[second_stage.id]
    assert second_widget.label_widget.full_text() == second_stage.label
    print("_StageRowWidget: the row label carries no leading stage-number prefix -- OK")


# ---- Stack Forking, drag-native rework (2026-08-12) -----------------------
# Per Tom: fork should behave like the normal stack (full drag-to-reorder),
# just rendered split down the middle, with branch assignment determined
# PURELY by which column a stage is dropped into -- no more branch_combo.
# Real native mouse drags can't be meaningfully synthesized under
# QT_QPA_PLATFORM=offscreen (same limitation the drop-position-indicator
# tests above already work around) -- these tests instead drive the
# underlying resolver methods (_stage_id_at, _drop_branch,
# _resolve_drop_target) and the StackPanel handlers they feed
# (_on_stage_reordered, _delete_stage_linked) directly, against a REAL,
# shown (not synthetic) StackPanel/_StackListWidget so row/cell geometry
# is real and meaningful.

def _shown_panel_with_fork(interior_stages="both"):
    """A StackPanel with one fork region, shown in a real QMainWindow so
    row/cell geometry is valid (matches _list_with_three_rows' own
    show()+resize() pattern above). `interior_stages`:
    - "both": Branch A gets one CDL stage (a1), Branch B gets one (b1).
    - "empty": the fork has nothing in either branch yet (right after
      "Add Fork (Split + Merge)"), for the empty-landing-zone tests.
    Returns (panel, win) -- caller indexes panel.stack.stages itself for
    whichever markers/interior stages it built."""
    _app()
    panel = StackPanel()
    panel._on_add_fork_clicked()  # selects fork_split
    if interior_stages == "both":
        # Built directly (not via _on_add_cdl_clicked() + auto-assignment)
        # so this fixture's a1/b1 branches are unambiguous regardless of
        # _maybe_auto_assign_fork_branch's own heuristic -- same "build
        # the Stage objects directly" pattern test_stack.py's own fork
        # fixtures already use.
        split_index = panel.stack.stages.index(panel.stack.stages[0])
        a1 = st.make_cdl_stage()
        a1.branch = "a"
        b1 = st.make_cdl_stage()
        b1.branch = "b"
        panel.stack.add_stage(a1, index=split_index + 1)
        panel.stack.add_stage(b1, index=split_index + 2)
        panel._refresh_list(keep_selection=a1.id)
    win = QMainWindow()
    win.setCentralWidget(panel.list_widget_area)
    win.resize(400, 400)
    win.show()
    _app().processEvents()
    return panel, win


def _row_of_widget_type(panel, widget_type):
    """The row index of the first _StackListWidget row whose item widget
    is an instance of `widget_type` -- lets tests refer to "the header
    row" / "the grid row" / etc. without hardcoding row numbers that
    would silently go stale if _refresh_list's rendering order ever
    changes."""
    lst = panel.list_widget
    for row in range(lst.count()):
        if isinstance(lst.itemWidget(lst.item(row)), widget_type):
            return row
    raise AssertionError(f"no row with a {widget_type.__name__} widget")


def test_drop_branch_resolves_left_and_right_of_the_viewport_midpoint():
    panel, _win = _shown_panel_with_fork("empty")
    lst = panel.list_widget
    width = lst.viewport().width()
    assert lst._drop_branch(QPoint(2, 0)) == "a"
    assert lst._drop_branch(QPoint(width - 2, 0)) == "b"
    assert lst._drop_branch(QPoint(int(width / 2) - 1, 0)) == "a"
    assert lst._drop_branch(QPoint(int(width / 2) + 1, 0)) == "b"
    print("_StackListWidget._drop_branch: left half -> 'a', right half -> "
          "'b', at the viewport's own midpoint -- OK")


def test_stage_id_at_resolves_ordinary_row_and_each_fork_grid_side():
    panel, _win = _shown_panel_with_fork("both")
    split, a1, b1, merge = panel.stack.stages
    lst = panel.list_widget

    split_row = _row_of_widget_type(panel, _StageRowWidget)
    split_rect = lst.visualItemRect(lst.item(split_row))
    assert lst._stage_id_at(split_rect.center()) == split.id

    grid_row = _row_of_widget_type(panel, _ForkBranchGridRowWidget)
    grid_rect = lst.visualItemRect(lst.item(grid_row))
    left_pos = QPoint(grid_rect.left() + 10, grid_rect.center().y())
    right_pos = QPoint(grid_rect.right() - 10, grid_rect.center().y())
    assert lst._stage_id_at(left_pos) == a1.id
    assert lst._stage_id_at(right_pos) == b1.id
    print("_StackListWidget._stage_id_at: resolves an ordinary row's own "
          "stage, and each SIDE of a fork-grid row independently -- OK")


def test_stage_id_at_is_none_for_header_and_empty_cell():
    panel, _win = _shown_panel_with_fork("both")
    lst = panel.list_widget

    header_row = None
    for row in range(lst.count()):
        widget = lst.itemWidget(lst.item(row))
        if not isinstance(widget, (_StageRowWidget, _ForkBranchGridRowWidget)):
            header_row = row
            break
    assert header_row is not None, "expected a non-selectable header row somewhere in a fork region"
    header_rect = lst.visualItemRect(lst.item(header_row))
    assert lst._stage_id_at(header_rect.center()) is None

    # An empty fork's single landing row -- both sides are _EmptyBranchCell.
    panel2, _win2 = _shown_panel_with_fork("empty")
    lst2 = panel2.list_widget
    grid_row = _row_of_widget_type(panel2, _ForkBranchGridRowWidget)
    grid_rect = lst2.visualItemRect(lst2.item(grid_row))
    left_pos = QPoint(grid_rect.left() + 10, grid_rect.center().y())
    assert lst2._stage_id_at(left_pos) is None
    print("_StackListWidget._stage_id_at: None for the header row and for "
          "an _EmptyBranchCell -- nothing draggable under either -- OK")


def test_empty_fork_still_renders_a_header_and_a_droppable_landing_row():
    """Per Tom: a fork region needs a real, always-droppable landing zone
    immediately, even with nothing in either branch yet -- the early
    return _flush_fork_grid used to have for "both branches empty" is
    gone."""
    panel, _win = _shown_panel_with_fork("empty")
    split, merge = panel.stack.stages
    assert _row_of_widget_type(panel, _ForkBranchGridRowWidget) is not None
    grid_row = _row_of_widget_type(panel, _ForkBranchGridRowWidget)
    # Both sides resolve to the merge's own index -- "append to that
    # (currently empty) branch" -- never None/unresolvable.
    assert panel._resolve_drop_target(grid_row, "a") == (panel.stack.stages.index(merge), "a")
    assert panel._resolve_drop_target(grid_row, "b") == (panel.stack.stages.index(merge), "b")
    print("StackPanel: a brand-new empty fork still renders a real grid "
          "row that resolves to a valid branch target on both sides -- OK")


def test_resolve_drop_target_across_every_boundary():
    panel, _win = _shown_panel_with_fork("both")
    split, a1, b1, merge = panel.stack.stages
    lst = panel.list_widget
    split_row = _row_of_widget_type(panel, _StageRowWidget)
    grid_row = _row_of_widget_type(panel, _ForkBranchGridRowWidget)
    merge_row = None
    for row in range(lst.count()):
        widget = lst.itemWidget(lst.item(row))
        if isinstance(widget, _StageRowWidget) and widget.stage_id == merge.id:
            merge_row = row
    assert merge_row is not None

    # Above the split: ordinary, right at the split's own index.
    assert panel._resolve_drop_target(split_row, "a") == (panel.stack.stages.index(split), None)
    # Into the grid row: each side resolves to that stage's own index.
    assert panel._resolve_drop_target(grid_row, "a") == (panel.stack.stages.index(a1), "a")
    assert panel._resolve_drop_target(grid_row, "b") == (panel.stack.stages.index(b1), "b")
    # Just above the merge (this row's own index, reached via its upper
    # half): still branch-interior, append-to-branch semantics, NOT an
    # ordinary insert at the merge's position.
    assert panel._resolve_drop_target(merge_row, "a") == (panel.stack.stages.index(merge), "a")
    assert panel._resolve_drop_target(merge_row, "b") == (panel.stack.stages.index(merge), "b")
    # Past the very last row (below the merge, or off the end of a
    # shorter stack): ordinary, append at the true end.
    assert panel._resolve_drop_target(lst.count(), "a") == (len(panel.stack.stages), None)
    print("StackPanel._resolve_drop_target: correctly resolves every "
          "boundary -- above the split, into either branch, just above "
          "the merge (still branch-interior), and past the last row -- OK")


def _bakes_without_raising(stack):
    try:
        st.bake_stack_to_lut3d(stack)
        return True
    except ValueError:
        return False


def test_reorder_within_a_branch_keeps_its_tag_and_moves_position():
    panel, _win = _shown_panel_with_fork("both")
    split, a1, b1, merge = panel.stack.stages
    lst = panel.list_widget
    a2 = st.make_cdl_stage()
    a2.branch = "a"
    panel.stack.add_stage(a2, index=panel.stack.stages.index(a1) + 1)
    panel._refresh_list()

    # Move a2 to land BEFORE a1 within Branch A (drop on the grid row,
    # branch "a" -- a1's own current position).
    grid_row = _row_of_widget_type(panel, _ForkBranchGridRowWidget)
    panel._on_stage_reordered(a2.id, grid_row, "a")

    stages = panel.stack.stages
    assert stages.index(a2) < stages.index(a1)
    assert a2.branch == "a"
    assert _bakes_without_raising(panel.stack)
    print("StackPanel._on_stage_reordered: reorders within a branch, "
          "keeps its branch tag, still bakes cleanly -- OK")


def test_drag_from_branch_a_to_branch_b_flips_the_tag():
    panel, _win = _shown_panel_with_fork("both")
    split, a1, b1, merge = panel.stack.stages
    grid_row = _row_of_widget_type(panel, _ForkBranchGridRowWidget)

    panel._on_stage_reordered(a1.id, grid_row, "b")

    assert a1.branch == "b"
    assert _bakes_without_raising(panel.stack)
    print("StackPanel._on_stage_reordered: dragging from Branch A's "
          "column to Branch B's flips the tag, still bakes cleanly -- OK")


def test_drag_an_ordinary_stage_into_a_branch_column_assigns_it():
    panel, _win = _shown_panel_with_fork("both")
    split, a1, b1, merge = panel.stack.stages
    ordinary = st.make_cdl_stage()
    panel.stack.add_stage(ordinary, index=0)  # before the fork entirely
    panel._refresh_list()
    assert ordinary.branch is None

    grid_row = _row_of_widget_type(panel, _ForkBranchGridRowWidget)
    panel._on_stage_reordered(ordinary.id, grid_row, "a")

    assert ordinary.branch == "a"
    split_idx = panel.stack.stages.index(split)
    merge_idx = panel.stack.stages.index(merge)
    assert split_idx < panel.stack.stages.index(ordinary) < merge_idx
    assert _bakes_without_raising(panel.stack)
    print("StackPanel._on_stage_reordered: dragging an ordinary stage "
          "into a branch column assigns it and moves it inside the "
          "region, still bakes cleanly -- OK")


def test_drag_a_branch_tagged_stage_out_of_the_fork_clears_its_tag():
    panel, _win = _shown_panel_with_fork("both")
    split, a1, b1, merge = panel.stack.stages
    lst = panel.list_widget
    split_row = _row_of_widget_type(panel, _StageRowWidget)

    # Drop on the split's own row (upper half via row index) -- ordinary,
    # right above the whole fork region.
    panel._on_stage_reordered(a1.id, split_row, "a")

    assert a1.branch is None
    assert panel.stack.stages.index(a1) <= panel.stack.stages.index(split)
    assert _bakes_without_raising(panel.stack)
    print("StackPanel._on_stage_reordered: dragging a branch-tagged "
          "stage out to an ordinary position clears its tag, still "
          "bakes cleanly -- OK")


def test_delete_stage_linked_removes_both_markers_and_untags_interior():
    panel, _win = _shown_panel_with_fork("both")
    split, a1, b1, merge = panel.stack.stages
    before_count = len(panel.stack.stages)

    panel._on_row_delete_clicked(split.id)

    assert len(panel.stack.stages) == before_count - 2
    assert split not in panel.stack.stages
    assert merge not in panel.stack.stages
    assert a1 in panel.stack.stages and a1.branch is None
    assert b1 in panel.stack.stages and b1.branch is None
    assert panel.bake_error_label.text() == ""
    print("StackPanel._on_row_delete_clicked: deleting Fork Split removes "
          "BOTH markers together and un-tags the interior stages (left in "
          "place, not deleted) -- OK")


def test_delete_stage_linked_works_via_fork_merge_and_via_remove_clicked():
    panel, _win = _shown_panel_with_fork("both")
    split, a1, b1, merge = panel.stack.stages

    panel._on_row_delete_clicked(merge.id)  # via the OTHER marker's own "x"
    assert split not in panel.stack.stages and merge not in panel.stack.stages
    assert panel.bake_error_label.text() == ""

    panel2, _win2 = _shown_panel_with_fork("both")
    split2, a1_2, b1_2, merge2 = panel2.stack.stages
    panel2._on_row_clicked(split2.id)  # select the split
    panel2._on_remove_clicked()  # Delete/Backspace path
    assert split2 not in panel2.stack.stages and merge2 not in panel2.stack.stages
    assert panel2.bake_error_label.text() == ""
    print("StackPanel: linked fork delete works via the OTHER marker's "
          "own \"x\" too, and via Delete/Backspace -- OK")


# ---- Never-orphaned "Add Operator" auto-assignment (round two) -----------
# Per Tom: adding a new operator while fork_split itself was selected (so
# it lands right below the split), or while a branch-interior stage was
# selected (so it spawns right under that one), could still leave the new
# stage orphaned -- not in EITHER branch -- whenever the OTHER branch also
# happened to be non-empty, since the original heuristic only ever looked
# at which side was empty, never at what was actually selected.

def test_add_with_fork_split_selected_and_both_branches_already_populated_is_never_orphaned():
    panel, _win = _shown_panel_with_fork("both")
    split, a1, b1, merge = panel.stack.stages
    panel._on_row_clicked(split.id)  # fork_split selected -- no branch context at all

    panel._on_add_cdl_clicked()

    new_stage = panel.stack.stages[panel.stack.stages.index(merge) - 1]
    assert new_stage.branch in ("a", "b")
    assert _bakes_without_raising(panel.stack)
    print("StackPanel._maybe_auto_assign_fork_branch: adding with "
          "fork_split selected, both branches already populated, still "
          "never orphans the new stage -- OK")


def test_add_with_a_branch_interior_stage_selected_continues_that_same_branch():
    panel, _win = _shown_panel_with_fork("both")
    split, a1, b1, merge = panel.stack.stages
    panel._on_row_clicked(a1.id)  # a Branch-A stage selected -- Branch B is ALSO already populated

    panel._on_add_cdl_clicked()

    new_stage = panel.stack.stages[panel.stack.stages.index(a1) + 1]
    assert new_stage.branch == "a", "must continue a1's own branch, not fall back to an empty-side guess"
    assert _bakes_without_raising(panel.stack)
    print("StackPanel._maybe_auto_assign_fork_branch: adding with a "
          "branch-interior stage selected joins that SAME branch, even "
          "when the other branch is also already populated -- OK")


def test_delete_stage_linked_falls_back_to_plain_removal_for_an_ordinary_stage():
    panel, _win = _shown_panel_with_fork("both")
    stage = st.make_cdl_stage()
    panel.stack.add_stage(stage, index=0)
    panel._refresh_list()
    before_count = len(panel.stack.stages)

    panel._on_row_delete_clicked(stage.id)

    assert len(panel.stack.stages) == before_count - 1
    assert stage not in panel.stack.stages
    print("StackPanel._on_row_delete_clicked: an ordinary (non-fork) "
          "stage is still just removed by itself, as before -- OK")


def test_fork_grid_cell_shows_its_kind_badge_too():
    """Round two, per Tom: "the glyphs for operators dont show up when
    they are in a fork." Badges used to be full-width-row-only, back
    when a compact cell also had to fit the (now-removed) branch combo
    in the same cramped space -- no longer a real reason to omit it."""
    panel, _win = _shown_panel_with_fork("both")
    split, a1, b1, merge = panel.stack.stages
    widget = panel._stage_row_widgets[a1.id]
    assert widget.compact is True
    assert widget.badge_widget is not None
    assert widget.badge_widget.text() == "CDL"
    print("_StageRowWidget: a compact (fork-grid) cell shows its kind "
          "badge too, same as a full-width row -- OK")


def _highlight_pixel_at(lst, x, y):
    from PySide6.QtGui import QColor
    img = lst.viewport().grab().toImage()
    return QColor(img.pixel(x, y)) == lst.palette().highlight().color()


def test_drop_indicator_spans_only_the_target_column_inside_a_fork():
    """Per Tom: the drop-position line should only span the HALF the
    entry will actually land in when dropped, not the full row width,
    once the target is inside a fork's two-column table. Verified
    against real rendered pixels (same .grab()-based technique already
    used elsewhere in this file for border-color states), not just the
    _RowTargets input the decision is driven by."""
    panel, _win = _shown_panel_with_fork("both")
    lst = panel.list_widget
    width = lst.viewport().width()
    grid_row = _row_of_widget_type(panel, _ForkBranchGridRowWidget)
    # One pixel above the row's own top edge -- the line itself, sampled
    # just before its 2px stroke's own antialiased lower edge, which
    # bleeds a little past the strict midpoint right at the exact top y.
    y = lst.visualItemRect(lst.item(grid_row)).top() - 1
    left_x, right_x = width // 4, width * 3 // 4

    lst._set_drop_indicator(grid_row, "a")
    lst.viewport().repaint()
    assert _highlight_pixel_at(lst, left_x, y), "branch 'a': the line must reach into the LEFT half"
    assert not _highlight_pixel_at(lst, right_x, y), "branch 'a': the line must NOT reach the right half"

    lst._set_drop_indicator(grid_row, "b")
    lst.viewport().repaint()
    assert not _highlight_pixel_at(lst, left_x, y), "branch 'b': the line must NOT reach the left half"
    assert _highlight_pixel_at(lst, right_x, y), "branch 'b': the line must reach into the RIGHT half"
    print("_StackListWidget.paintEvent: the drop indicator only spans "
          "the column the entry will actually land in, for a "
          "fork-interior target row -- OK")


def test_drop_indicator_spans_the_full_width_for_an_ordinary_row():
    panel, _win = _shown_panel_with_fork("both")
    lst = panel.list_widget
    width = lst.viewport().width()
    split_row = _row_of_widget_type(panel, _StageRowWidget)
    # split is the very first row (top() == 0) -- no "-1" headroom here,
    # unlike the fork-interior test's midpoint-antialiasing dodge above;
    # a full-width line has no boundary to bleed across in the first
    # place, so the exact top edge renders cleanly.
    y = lst.visualItemRect(lst.item(split_row)).top()
    left_x, right_x = width // 4, width * 3 // 4

    lst._set_drop_indicator(split_row, "a")  # branch is meaningless/ignored for an ordinary row
    lst.viewport().repaint()
    assert _highlight_pixel_at(lst, left_x, y)
    assert _highlight_pixel_at(lst, right_x, y)
    print("_StackListWidget.paintEvent: the drop indicator still spans "
          "the FULL row width for an ordinary (non-fork) target row -- OK")


if __name__ == "__main__":
    test_short_text_that_fits_is_not_elided_and_has_no_tooltip()
    test_long_text_at_narrow_width_is_elided_with_ellipsis_and_gets_a_tooltip()
    test_full_text_survives_resizing_back_and_forth()
    test_set_text_updates_the_full_text_not_just_the_displayed_text()
    test_drop_row_upper_half_of_a_row_inserts_before_it()
    test_drop_row_lower_half_of_a_row_inserts_after_it()
    test_drop_row_below_every_row_appends_at_the_end()
    test_set_drop_indicator_dedupes_unchanged_row_and_branch()
    test_drag_leave_clears_the_indicator()
    test_stack_list_widget_is_scoped_out_of_the_generic_item_selected_highlight()
    test_row_hover_uses_its_own_tertiary_color_not_the_selected_accent()
    test_cdl_live_edit_updates_the_existing_row_widget_in_place()
    test_opacity_spin_lives_on_the_row_and_edits_that_stage_in_place()
    test_enabled_toggle_updates_the_existing_row_widget_in_place()
    test_stage_kind_badge_text_known_and_unknown_kinds()
    test_cdl_row_widget_shows_the_cdl_badge()
    test_row_label_has_no_leading_stage_number_prefix()
    test_drop_branch_resolves_left_and_right_of_the_viewport_midpoint()
    test_stage_id_at_resolves_ordinary_row_and_each_fork_grid_side()
    test_stage_id_at_is_none_for_header_and_empty_cell()
    test_empty_fork_still_renders_a_header_and_a_droppable_landing_row()
    test_resolve_drop_target_across_every_boundary()
    test_reorder_within_a_branch_keeps_its_tag_and_moves_position()
    test_drag_from_branch_a_to_branch_b_flips_the_tag()
    test_drag_an_ordinary_stage_into_a_branch_column_assigns_it()
    test_drag_a_branch_tagged_stage_out_of_the_fork_clears_its_tag()
    test_add_with_fork_split_selected_and_both_branches_already_populated_is_never_orphaned()
    test_add_with_a_branch_interior_stage_selected_continues_that_same_branch()
    test_delete_stage_linked_removes_both_markers_and_untags_interior()
    test_delete_stage_linked_works_via_fork_merge_and_via_remove_clicked()
    test_delete_stage_linked_falls_back_to_plain_removal_for_an_ordinary_stage()
    test_fork_grid_cell_shows_its_kind_badge_too()
    test_drop_indicator_spans_only_the_target_column_inside_a_fork()
    test_drop_indicator_spans_the_full_width_for_an_ordinary_row()
    print()
    print("All stack_panel tests passed.")
