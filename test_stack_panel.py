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

import numpy as np
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QDragLeaveEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMainWindow

import stack as st
import color_warper as cw
import keyer as ky
from stack_panel import (
    _ElidingLabel, _StackListWidget, StackPanel, _stage_kind_badge_text,
    _ForkBranchGridRowWidget, _StageRowWidget, _ParamLabel, GRADING_TONE_RANGE_LABELS,
    GRADING_PRIMARY_FIELD_SPECS,
)
from stack_memory import StackMemory
from cdl_library import CDLEntry


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


def _panel_with_one_printer_lights_stage():
    _app()
    panel = StackPanel()
    panel._on_add_printer_lights_clicked()
    stage = panel.stack.stages[0]
    panel._on_row_clicked(stage.id)
    return panel, stage


def _panel_with_one_grading_primary_stage():
    _app()
    panel = StackPanel()
    panel._on_add_grading_primary_clicked()
    stage = panel.stack.stages[0]
    panel._on_row_clicked(stage.id)
    return panel, stage


def _panel_with_one_grading_tone_stage():
    _app()
    panel = StackPanel()
    panel._on_add_grading_tone_clicked()
    stage = panel.stack.stages[0]
    panel._on_row_clicked(stage.id)
    return panel, stage


def _panel_with_one_color_warper_stage():
    _app()
    panel = StackPanel()
    panel._on_add_color_warper_clicked()
    stage = panel.stack.stages[0]
    panel._on_row_clicked(stage.id)
    return panel, stage


def _panel_with_one_keyer_stage():
    _app()
    panel = StackPanel()
    panel._on_add_keyer_clicked()
    stage = panel.stack.stages[0]
    panel._on_row_clicked(stage.id)
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


def test_insert_entry_in_stack_with_a_stack_memory_adds_all_its_stages_below_selected():
    """Per Tom: "insert in stack" adds all of a saved stack's operators
    into the existing stack below the selected entry."""
    panel, first_cdl = _panel_with_one_cdl_stage()
    panel._on_row_clicked(first_cdl.id)  # select the pre-existing stage

    memory_stack = st.Stack(stages=[
        st.make_lut_stage(st.colour.LUT3D(size=3), label="Saved LUT"),
        st.make_cdl_stage(slope=(1.2, 1.0, 0.9), label="Saved CDL"),
    ])
    memory = StackMemory("My Look", memory_stack)

    panel.insert_entry_in_stack(memory)

    kinds = [s.kind for s in panel.stack.stages]
    labels = [s.label for s in panel.stack.stages]
    assert kinds == ["cdl", "lut", "cdl"]
    assert labels == [first_cdl.label, "Saved LUT", "Saved CDL"]
    print("StackPanel.insert_entry_in_stack: a StackMemory's stages land, "
          "in order, immediately after the selected stage -- OK")


def test_insert_entry_in_stack_with_a_stack_memory_uses_fresh_ids():
    """Stage.copy() (used elsewhere for Recall's non-destructive
    semantics) keeps the SAME id -- wrong here, since Insert ADDS onto an
    existing stack rather than replacing it, so a fresh id is required to
    avoid colliding with an already-live stage (or with itself, on a
    second insert of the same memory)."""
    panel = StackPanel()
    _app()
    memory_stack = st.Stack(stages=[st.make_cdl_stage(label="Saved CDL")])
    original_id = memory_stack.stages[0].id
    memory = StackMemory("My Look", memory_stack)

    panel.insert_entry_in_stack(memory)
    panel.insert_entry_in_stack(memory)  # insert the SAME memory twice

    ids = [s.id for s in panel.stack.stages]
    assert len(ids) == 2
    assert len(set(ids)) == 2, "inserting the same memory twice must not produce colliding stage ids"
    assert original_id not in ids
    print("StackPanel.insert_entry_in_stack: gives every inserted stage a "
          "fresh id, even across two inserts of the same memory -- OK")


def test_insert_entry_in_stack_with_a_cdl_entry_adds_one_new_stage():
    panel, first_cdl = _panel_with_one_cdl_stage()
    panel._on_row_clicked(first_cdl.id)

    entry = CDLEntry("My CDL", slope=(1.3, 1.0, 0.9), offset=(0.0, 0.0, 0.0), power=(1.0, 1.0, 1.0), sat=1.1)
    panel.insert_entry_in_stack(entry)

    assert len(panel.stack.stages) == 2
    new_stage = panel.stack.stages[1]
    assert new_stage.kind == "cdl"
    assert new_stage.params["slope"] == (1.3, 1.0, 0.9)
    print("StackPanel.insert_entry_in_stack: a CDLEntry adds one new 'cdl' "
          "stage -- OK")


def test_insert_entry_in_stack_never_overwrites_a_selected_cdl_stage():
    """Distinct from apply_cdl_entry, which overwrites an already-
    selected 'cdl' stage in place -- Insert in Stack is unconditionally
    an ADD, even when a 'cdl' stage happens to be selected."""
    panel, first_cdl = _panel_with_one_cdl_stage()
    panel._on_row_clicked(first_cdl.id)
    original_slope = first_cdl.params["slope"]

    entry = CDLEntry("My CDL", slope=(1.3, 1.0, 0.9), offset=(0.0, 0.0, 0.0), power=(1.0, 1.0, 1.0), sat=1.1)
    panel.insert_entry_in_stack(entry)

    assert len(panel.stack.stages) == 2
    assert panel.stack.stages[0].params["slope"] == original_slope, (
        "the already-selected cdl stage must be untouched, not overwritten")
    print("StackPanel.insert_entry_in_stack: never overwrites an already-"
          "selected 'cdl' stage, unlike apply_cdl_entry -- OK")


def test_printer_lights_inspector_uses_spinboxes_not_sliders():
    """Per Tom: "printer lights shouldn't use system sliders, move to
    sliding spinboxes." """
    from PySide6.QtWidgets import QSlider, QSpinBox
    panel, stage = _panel_with_one_printer_lights_stage()
    inspector = panel.inspector_stack.widget(panel._printer_lights_inspector_index)
    assert not inspector.findChildren(QSlider), (
        "printer lights inspector must not contain any QSlider")
    for channel in ("R", "G", "B"):
        assert isinstance(panel._pl_spins[channel], QSpinBox)
    print("StackPanel printer lights inspector: R/G/B are QSpinBox, no "
          "QSlider involved -- OK")


def test_printer_lights_spins_have_drag_to_scrub_enabled():
    from PySide6.QtCore import Qt as _Qt
    panel, stage = _panel_with_one_printer_lights_stage()
    for channel in ("R", "G", "B"):
        cursor_shape = panel._pl_spins[channel].lineEdit().cursor().shape()
        assert cursor_shape == _Qt.CursorShape.SizeHorCursor, (
            f"{channel} spin doesn't look like enable_drag_to_scrub ran on it")
    print("StackPanel printer lights inspector: R/G/B spinboxes have "
          "drag-to-scrub enabled (SizeHorCursor) -- OK")


def test_printer_lights_spin_change_applies_live():
    panel, stage = _panel_with_one_printer_lights_stage()
    panel._pl_spins["R"].setValue(12)
    assert stage.params["r_points"] == 12
    print("StackPanel printer lights inspector: editing the R spinbox "
          "applies to the selected stage live -- OK")


def test_printer_lights_gang_mirrors_r_to_g_and_b():
    panel, stage = _panel_with_one_printer_lights_stage()
    panel.pl_gang_checkbox.setChecked(True)
    panel._pl_spins["R"].setValue(20)
    assert panel._pl_spins["G"].value() == 20
    assert panel._pl_spins["B"].value() == 20
    assert stage.params["g_points"] == 20
    assert stage.params["b_points"] == 20
    print("StackPanel printer lights inspector: Gang R/G/B mirrors an R "
          "spinbox edit onto G/B -- OK")


def test_printer_lights_reset_zeroes_all_three_spins():
    panel, stage = _panel_with_one_printer_lights_stage()
    panel._pl_spins["R"].setValue(30)
    panel._pl_spins["G"].setValue(-10)
    panel._on_pl_reset_clicked()
    assert panel._pl_spins["R"].value() == 0
    assert panel._pl_spins["G"].value() == 0
    assert panel._pl_spins["B"].value() == 0
    assert stage.params["r_points"] == 0
    print("StackPanel printer lights inspector: Reset to 0 zeroes all "
          "three spinboxes and the stage's params -- OK")


def test_grading_primary_field_has_a_color_balance_wheel():
    """Per Tom: "adding ... color wheels to operators that dont have
    them" -- Grading Primary didn't have one before."""
    from color_wheel_widget import ColorBalanceWheel
    panel, stage = _panel_with_one_grading_primary_stage()
    assert isinstance(panel._gpr_wheels["gamma"], ColorBalanceWheel)
    print("StackPanel Grading Primary inspector: each field has its own "
          "ColorBalanceWheel -- OK")


def test_grading_primary_wheel_drag_shifts_rgb_leaves_master_alone():
    panel, stage = _panel_with_one_grading_primary_stage()
    spins = panel._gpr_field_spins["gamma"]
    spins["M"].setValue(1.2)  # a non-default master, so we can tell it's untouched

    # (0, 1) is R's own axis (see color_balance.py: R at 90 degrees) --
    # set_balance (not calling the handler directly) so the wheel WIDGET's
    # own internal position actually updates too, same as a real drag.
    panel._gpr_wheels["gamma"].set_balance(0.0, 1.0)

    assert spins["M"].value() == 1.2, "wheel drag must not touch master"
    assert spins["R"].value() != spins["G"].value(), "wheel drag must fan R/G/B apart"
    assert abs(spins["G"].value() - spins["B"].value()) < 1e-9, "G and B move together for a pure R tilt"
    assert stage.params["gamma"][3] == 1.2  # M unchanged in the applied params too
    print("StackPanel Grading Primary: dragging the wheel fans R/G/B "
          "apart around M, without moving M itself -- OK")


def test_grading_primary_master_edit_shifts_rgb_preserves_wheel_position():
    """Editing M directly should behave like CDL's own master spin: the
    wheel's tint (relative spread) stays put, R/G/B all shift together to
    the new master."""
    panel, stage = _panel_with_one_grading_primary_stage()
    spins = panel._gpr_field_spins["gamma"]
    panel._gpr_wheels["gamma"].set_balance(0.5, 0.0)  # establish a real tint
    r_before, g_before, b_before = spins["R"].value(), spins["G"].value(), spins["B"].value()
    wheel_before = panel._gpr_wheels["gamma"].balance()

    spins["M"].setValue(2.0)

    wheel_after = panel._gpr_wheels["gamma"].balance()
    assert abs(wheel_after[0] - wheel_before[0]) < 1e-9
    assert abs(wheel_after[1] - wheel_before[1]) < 1e-9
    # Each channel should have shifted by the same delta as the master did.
    delta_m = 2.0 - 1.0
    assert abs(spins["R"].value() - (r_before + delta_m)) < 1e-6
    assert abs(spins["G"].value() - (g_before + delta_m)) < 1e-6
    assert abs(spins["B"].value() - (b_before + delta_m)) < 1e-6
    print("StackPanel Grading Primary: editing M shifts R/G/B together, "
          "keeping the wheel's own tint position unchanged -- OK")


def test_grading_primary_channel_spin_edit_moves_wheel_leaves_master_alone():
    """Typing an exact R/G/B value directly must NOT touch M -- unlike
    CDL's fine-spin handler, which recomputes master as mean(R,G,B) (see
    GRADING_WHEEL_STRENGTH's own comment on why that's wrong here)."""
    panel, stage = _panel_with_one_grading_primary_stage()
    spins = panel._gpr_field_spins["gamma"]
    spins["M"].setValue(1.3)

    spins["R"].setValue(1.8)

    assert spins["M"].value() == 1.3, "typing R directly must not touch M"
    x, y = panel._gpr_wheels["gamma"].balance()
    assert abs(x) > 1e-9 or abs(y) > 1e-9, "wheel should have moved off-center"
    assert stage.params["gamma"][0] == 1.8
    assert stage.params["gamma"][3] == 1.3
    print("StackPanel Grading Primary: typing a fine R/G/B value directly "
          "moves the wheel, leaves M completely alone -- OK")


def test_grading_primary_reset_zeroes_wheel_and_all_fields():
    panel, stage = _panel_with_one_grading_primary_stage()
    spins = panel._gpr_field_spins["gamma"]
    panel._gpr_wheels["gamma"].set_balance(0.7, 0.3)
    spins["M"].setValue(2.5)

    panel._on_gpr_reset_clicked()

    assert spins["R"].value() == spins["G"].value() == spins["B"].value() == spins["M"].value() == 1.0
    x, y = panel._gpr_wheels["gamma"].balance()
    assert abs(x) < 1e-9 and abs(y) < 1e-9
    assert stage.params["gamma"] == (1.0, 1.0, 1.0, 1.0)
    print("StackPanel Grading Primary: Reset to Identity zeroes every "
          "field's spinboxes AND its wheel -- OK")


def test_grading_primary_refresh_on_select_does_not_corrupt_stored_values():
    """The real bug this needs to guard against: refreshing the inspector
    for a stage whose R/G/B/M genuinely don't sum to a wheel-consistent
    shape (i.e. M != mean(R,G,B), the normal case for independently-set
    values) must display them EXACTLY as stored, not silently mutate them
    via cascading sync handlers."""
    panel = StackPanel()
    stage1 = st.make_grading_primary_stage()
    stage1.params["gamma"] = (1.9, 1.1, 1.0, 1.3)  # M deliberately != mean(R,G,B)
    stage2 = st.make_grading_primary_stage()
    panel.stack.add_stage(stage1)
    panel.stack.add_stage(stage2)
    panel._refresh_list()

    panel._on_row_clicked(stage1.id)  # select stage1, populating the inspector
    panel._on_row_clicked(stage2.id)  # select stage2 (a different stage)
    panel._on_row_clicked(stage1.id)  # back to stage1 -- inspector repopulates again

    assert stage1.params["gamma"] == (1.9, 1.1, 1.0, 1.3), (
        "selecting away and back must not have mutated stage1's stored values")
    spins = panel._gpr_field_spins["gamma"]
    assert (spins["R"].value(), spins["G"].value(), spins["B"].value(), spins["M"].value()) == (1.9, 1.1, 1.0, 1.3)
    print("StackPanel Grading Primary: switching stage selection away and "
          "back never mutates a stage's own stored R/G/B/M values -- OK")


def test_grading_tone_range_has_a_color_balance_wheel():
    from color_wheel_widget import ColorBalanceWheel
    panel, stage = _panel_with_one_grading_tone_stage()
    assert isinstance(panel._gtn_wheels["midtones"], ColorBalanceWheel)
    print("StackPanel Grading Tone inspector: each range has its own "
          "ColorBalanceWheel -- OK")


def test_grading_tone_wheel_drag_shifts_rgb_leaves_master_alone():
    panel, stage = _panel_with_one_grading_tone_stage()
    spins = panel._gtn_range_spins["midtones"]
    spins["M"].setValue(1.2)

    # (0, 1) is R's own axis (see color_balance.py) -- set_balance so the
    # wheel WIDGET's own internal position actually updates, same as a
    # real drag.
    panel._gtn_wheels["midtones"].set_balance(0.0, 1.0)

    assert spins["M"].value() == 1.2
    assert spins["R"].value() != spins["G"].value()
    assert stage.params["midtones"][3] == 1.2
    print("StackPanel Grading Tone: dragging a range's wheel fans R/G/B "
          "apart around M, without moving M itself -- OK")


def test_grading_tone_channel_spin_edit_moves_wheel_leaves_master_alone():
    panel, stage = _panel_with_one_grading_tone_stage()
    spins = panel._gtn_range_spins["midtones"]
    spins["M"].setValue(1.1)

    spins["G"].setValue(1.5)

    assert spins["M"].value() == 1.1
    x, y = panel._gtn_wheels["midtones"].balance()
    assert abs(x) > 1e-9 or abs(y) > 1e-9
    assert stage.params["midtones"][1] == 1.5
    print("StackPanel Grading Tone: typing a fine R/G/B value directly "
          "moves the wheel, leaves M completely alone -- OK")


def test_grading_tone_reset_zeroes_wheel_and_all_ranges():
    panel, stage = _panel_with_one_grading_tone_stage()
    spins = panel._gtn_range_spins["midtones"]
    panel._gtn_wheels["midtones"].set_balance(0.4, -0.2)
    spins["M"].setValue(1.6)

    panel._on_gtn_reset_clicked()

    assert spins["R"].value() == spins["G"].value() == spins["B"].value() == spins["M"].value() == 1.0
    x, y = panel._gtn_wheels["midtones"].balance()
    assert abs(x) < 1e-9 and abs(y) < 1e-9
    print("StackPanel Grading Tone: Reset to Identity zeroes every "
          "range's spinboxes AND its wheel -- OK")


def test_grading_tone_start_width_are_unaffected_by_wheel_sync():
    """Start/Width aren't part of the R/G/B/M wheel model at all -- must
    keep working exactly as before (plain apply-on-change, no wheel
    involvement)."""
    panel, stage = _panel_with_one_grading_tone_stage()
    spins = panel._gtn_range_spins["midtones"]
    spins["Start"].setValue(0.1)
    spins["Width"].setValue(0.9)
    assert stage.params["midtones"][4] == 0.1
    assert stage.params["midtones"][5] == 0.9
    print("StackPanel Grading Tone: Start/Width still apply directly, "
          "untouched by the new wheel sync -- OK")


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


def _find_param_label(inspector, text):
    matches = [w for w in inspector.findChildren(_ParamLabel) if w.text() == text]
    assert len(matches) == 1, f"expected exactly one _ParamLabel with text {text!r}, found {len(matches)}"
    return matches[0]


def test_param_label_double_click_calls_its_reset_callback():
    """Isolated test of the reusable widget itself (per Tom: "add the
    ability to double click the name of a parameter to reset it
    individually") -- every per-operator test below just confirms this
    class is actually WIRED to the right control(s); this confirms the
    class's own double-click handling works at all."""
    _app()
    calls = []
    label = _ParamLabel("Brightness:", lambda: calls.append(True))
    label.show()
    QTest.mouseDClick(label, Qt.MouseButton.LeftButton)
    assert calls == [True]
    print("_ParamLabel: double-click fires the reset callback exactly "
          "once -- OK")


def test_printer_lights_channel_label_double_click_resets_just_that_channel():
    panel, stage = _panel_with_one_printer_lights_stage()
    panel._pl_spins["R"].setValue(30)
    panel._pl_spins["G"].setValue(-10)
    inspector = panel.inspector_stack.widget(panel._printer_lights_inspector_index)
    label = _find_param_label(inspector, "R:")
    label.show()
    QTest.mouseDClick(label, Qt.MouseButton.LeftButton)
    assert panel._pl_spins["R"].value() == 0
    assert panel._pl_spins["G"].value() == -10, "G must be untouched by resetting R alone"
    print("StackPanel printer lights inspector: double-clicking the 'R:' "
          "label resets only R, leaves G/B alone -- OK")


def test_cdl_field_label_double_click_resets_whole_field_to_identity():
    panel, stage = _panel_with_one_cdl_stage()
    panel._cdl_sop_spins["slope"]["R"].setValue(2.0)
    panel._cdl_sop_spins["slope"]["G"].setValue(0.5)
    panel._cdl_sop_spins["offset"]["R"].setValue(0.3)  # a different field -- must be untouched
    inspector = panel.inspector_stack.widget(panel._cdl_inspector_index)
    label = _find_param_label(inspector, "Slope (Master):")
    label.show()
    QTest.mouseDClick(label, Qt.MouseButton.LeftButton)
    for channel in ("R", "G", "B"):
        assert panel._cdl_sop_spins["slope"][channel].value() == 1.0
    assert panel._cdl_sop_spins["offset"]["R"].value() == 0.3, "offset must be untouched by resetting slope alone"
    print("StackPanel CDL inspector: double-clicking 'Slope (Master):' "
          "resets the whole Slope field to identity, leaves Offset alone -- OK")


def test_cdl_channel_label_double_click_resets_just_that_channel():
    panel, stage = _panel_with_one_cdl_stage()
    panel._cdl_sop_spins["slope"]["R"].setValue(2.0)
    panel._cdl_sop_spins["slope"]["G"].setValue(0.5)
    inspector = panel.inspector_stack.widget(panel._cdl_inspector_index)
    labels_r = [w for w in inspector.findChildren(_ParamLabel) if w.text() == "R"]
    assert len(labels_r) == 3  # one per SOP field (slope/offset/power)
    labels_r[0].show()  # CDL_SOP_FIELD_SPECS' first entry is "slope"
    QTest.mouseDClick(labels_r[0], Qt.MouseButton.LeftButton)
    assert panel._cdl_sop_spins["slope"]["R"].value() == 1.0
    assert panel._cdl_sop_spins["slope"]["G"].value() == 0.5, "G must be untouched by resetting R alone"
    print("StackPanel CDL inspector: double-clicking a fine 'R' label "
          "resets just that channel, leaves G/B alone -- OK")


def test_grading_primary_field_label_double_click_resets_whole_field_to_identity():
    panel, stage = _panel_with_one_grading_primary_stage()
    spins = panel._gpr_field_spins["brightness"]
    for channel in ("R", "G", "B", "M"):
        spins[channel].setValue(0.5)
    inspector = panel.inspector_stack.widget(panel._grading_primary_inspector_index)
    label = _find_param_label(inspector, "Brightness:")
    label.show()
    QTest.mouseDClick(label, Qt.MouseButton.LeftButton)
    for channel in ("R", "G", "B", "M"):
        assert spins[channel].value() == 0.0
    wheel_x, wheel_y = panel._gpr_wheels["brightness"].balance()
    assert wheel_x == 0.0 and wheel_y == 0.0
    print("StackPanel Grading Primary inspector: double-clicking "
          "'Brightness:' resets that field's R/G/B/M and wheel to "
          "identity -- OK")


def test_grading_primary_channel_label_double_click_resets_just_that_channel():
    panel, stage = _panel_with_one_grading_primary_stage()
    spins = panel._gpr_field_spins["brightness"]
    spins["R"].setValue(0.4)
    spins["M"].setValue(0.3)
    inspector = panel.inspector_stack.widget(panel._grading_primary_inspector_index)
    labels_r = [w for w in inspector.findChildren(_ParamLabel) if w.text() == "R"]
    assert len(labels_r) == len(GRADING_PRIMARY_FIELD_SPECS)
    labels_r[0].show()  # GRADING_PRIMARY_FIELD_SPECS' first entry is "brightness"
    QTest.mouseDClick(labels_r[0], Qt.MouseButton.LeftButton)
    assert spins["R"].value() == 0.0
    assert spins["M"].value() == 0.3, "M must be untouched by resetting R alone"
    print("StackPanel Grading Primary inspector: double-clicking a "
          "channel's own 'R' label resets just that channel, leaves M "
          "alone -- OK")


def test_grading_tone_range_label_double_click_resets_whole_range_to_identity():
    panel, stage = _panel_with_one_grading_tone_stage()
    import grading_tone as gtn
    spins = panel._gtn_range_spins["blacks"]
    for channel in ("R", "G", "B", "M", "Start", "Width"):
        spins[channel].setValue(spins[channel].value() + 0.2)
    inspector = panel.inspector_stack.widget(panel._grading_tone_inspector_index)
    label = _find_param_label(inspector, GRADING_TONE_RANGE_LABELS["blacks"])
    label.show()
    QTest.mouseDClick(label, Qt.MouseButton.LeftButton)
    r, g, b, m, start, width = gtn.DEFAULT_RANGE_VALUES["blacks"]
    for channel, expected in zip(("R", "G", "B", "M", "Start", "Width"), (r, g, b, m, start, width)):
        assert abs(spins[channel].value() - expected) < 1e-9, channel
    print("StackPanel Grading Tone inspector: double-clicking a range's "
          "own name resets that whole range to identity -- OK")


def test_grading_tone_start_width_labels_reset_individually():
    panel, stage = _panel_with_one_grading_tone_stage()
    spins = panel._gtn_range_spins["blacks"]
    spins["Start"].setValue(spins["Start"].value() + 0.5)
    spins["Width"].setValue(spins["Width"].value() + 0.5)
    r_before = spins["R"].value()
    inspector = panel.inspector_stack.widget(panel._grading_tone_inspector_index)
    labels = [w for w in inspector.findChildren(_ParamLabel) if w.text() == "Start"]
    assert len(labels) == 5  # one per range
    labels[0].show()
    QTest.mouseDClick(labels[0], Qt.MouseButton.LeftButton)
    import grading_tone as gtn
    _r, _g, _b, _m, default_start, _width = gtn.DEFAULT_RANGE_VALUES["blacks"]
    assert abs(spins["Start"].value() - default_start) < 1e-9
    assert spins["R"].value() == r_before, "R must be untouched by resetting Start alone"
    print("StackPanel Grading Tone inspector: 'Start'/'Width' labels "
          "reset just that one field, not the whole range -- OK")


def test_grading_tone_channel_label_double_click_resets_just_that_channel():
    panel, stage = _panel_with_one_grading_tone_stage()
    spins = panel._gtn_range_spins["blacks"]
    spins["R"].setValue(spins["R"].value() + 0.3)
    spins["G"].setValue(spins["G"].value() + 0.3)
    inspector = panel.inspector_stack.widget(panel._grading_tone_inspector_index)
    labels_r = [w for w in inspector.findChildren(_ParamLabel) if w.text() == "R"]
    assert len(labels_r) == 5  # one per range
    labels_r[0].show()  # gtn.RANGE_KEYS' first entry is "blacks"
    QTest.mouseDClick(labels_r[0], Qt.MouseButton.LeftButton)
    import grading_tone as gtn
    r_default, _g, _b, _m, _start, _width = gtn.DEFAULT_RANGE_VALUES["blacks"]
    assert abs(spins["R"].value() - r_default) < 1e-9
    print("StackPanel Grading Tone inspector: double-clicking a range's "
          "own 'R' label resets just that channel -- OK")


def test_exposure_contrast_field_label_double_click_resets_that_field():
    import exposure_contrast as ec
    _app()
    panel = StackPanel()
    panel._on_add_exposure_contrast_clicked()
    stage = panel.stack.stages[0]
    panel._on_row_clicked(stage.id)
    panel._ec_field_spins["exposure"].setValue(2.5)
    panel._ec_field_spins["contrast"].setValue(1.5)  # untouched control
    inspector = panel.inspector_stack.widget(panel._exposure_contrast_inspector_index)
    label = _find_param_label(inspector, "Exposure:")
    label.show()
    QTest.mouseDClick(label, Qt.MouseButton.LeftButton)
    assert panel._ec_field_spins["exposure"].value() == ec.IDENTITY_EXPOSURE
    assert panel._ec_field_spins["contrast"].value() == 1.5, "contrast must be untouched"
    print("StackPanel Exposure/Contrast inspector: double-clicking "
          "'Exposure:' resets just that field -- OK")


# ---- WARPier (color_warper) inspector -----------------------------------

def test_add_color_warper_starts_at_identity():
    panel, stage = _panel_with_one_color_warper_stage()
    assert stage.kind == "color_warper"
    assert cw.points_are_identity(stage.params["points"])
    assert stage.params["wake"] == cw.DEFAULT_WAKE
    assert stage.params["dropoff"] == cw.DEFAULT_DROPOFF
    print("StackPanel: a freshly-added 'color_warper' stage starts at "
          "identity (no seeded points, default wake/dropoff) -- OK")


def test_color_warper_wheel_drag_applies_live():
    panel, stage = _panel_with_one_color_warper_stage()
    # No seeded points (per Tom) -- add one directly (same shape a real
    # click-then-drag on the wheel would produce) rather than relying on
    # points()[0] already existing.
    dragged = {"h0": 35.0, "c0": 0.15, "h": 55.0, "c": 0.2}
    panel.cw_wheel.set_points([dragged])  # emits points_changed -> live apply
    assert stage.params["points"][0]["h"] == dragged["h"]
    assert stage.params["points"][0]["c"] == dragged["c"]
    print("StackPanel WARPier inspector: setting a wheel point writes "
          "straight into the selected stage's params -- OK")


def test_color_warper_wake_and_dropoff_spins_apply_live():
    panel, stage = _panel_with_one_color_warper_stage()
    panel.cw_wake_spin.setValue(60.0)
    panel.cw_dropoff_spin.setValue(0.75)
    assert stage.params["wake"] == 60.0
    assert stage.params["dropoff"] == 0.75
    assert panel.cw_wheel._wake == 60.0, "the wheel's own wash rendering must stay in sync"
    assert panel.cw_wheel._dropoff == 0.75
    print("StackPanel WARPier inspector: Wake/Drop-off spinboxes apply "
          "live and keep the wheel's wash overlay in sync -- OK")


def test_color_warper_space_combo_applies_live():
    panel, stage = _panel_with_one_color_warper_stage()
    other_space = "ACEScg"
    idx = panel.cw_space_combo.findText(other_space)
    assert idx >= 0, f"{other_space!r} should be in the working-space combo"
    panel.cw_space_combo.setCurrentIndex(idx)
    assert stage.params["working_colourspace"] == other_space
    print("StackPanel WARPier inspector: changing Working Color Space "
          "applies live -- OK")


def test_color_warper_reset_restores_identity():
    panel, stage = _panel_with_one_color_warper_stage()
    panel.cw_wake_spin.setValue(80.0)
    panel.cw_dropoff_spin.setValue(0.9)
    panel.cw_wheel.set_points([{"h0": 35.0, "c0": 0.15, "h": 50.0, "c": 0.15}])
    assert not cw.points_are_identity(stage.params["points"])

    panel._on_cw_reset_clicked()
    assert stage.params["points"] == []
    assert cw.points_are_identity(stage.params["points"])
    assert stage.params["wake"] == cw.DEFAULT_WAKE
    assert stage.params["dropoff"] == cw.DEFAULT_DROPOFF
    print("StackPanel WARPier inspector: Reset to Identity clears back "
          "to no points and default wake/dropoff -- OK")


def test_color_warper_refresh_on_select_does_not_corrupt_stored_values():
    """Switching AWAY from a color_warper stage and back must not treat
    the resulting widget resync as a live edit -- same class of bug the
    Grading Primary/Tone refresh-guard tests already cover."""
    panel, stage = _panel_with_one_color_warper_stage()
    panel.cw_wheel.set_points([{"h0": 99.0, "c0": 0.08, "h": 99.0, "c": 0.08}])
    panel.cw_wake_spin.setValue(50.0)
    stored_points = [dict(p) for p in stage.params["points"]]
    stored_wake = stage.params["wake"]

    other = st.make_printer_lights_stage()
    panel.stack.add_stage(other)
    panel._on_row_clicked(other.id)
    panel._on_row_clicked(stage.id)  # back to the color_warper stage

    assert stage.params["points"] == stored_points
    assert stage.params["wake"] == stored_wake
    print("StackPanel WARPier inspector: switching away and back does "
          "not corrupt the stage's stored points/wake -- OK")


def test_color_warper_badge_text():
    assert _stage_kind_badge_text("color_warper") == "WRP"
    print("_stage_kind_badge_text('color_warper') == 'WRP' -- OK")


# --- keyer stage + matte channel row/inspector ----------------------------

def test_keyer_badge_text():
    assert _stage_kind_badge_text("keyer") == "KEY"
    print("_stage_kind_badge_text('keyer') == 'KEY' -- OK")


def test_add_keyer_starts_at_defaults():
    panel, stage = _panel_with_one_keyer_stage()
    assert stage.kind == "keyer"
    assert stage.params["hue_center"] == ky.DEFAULT_HUE_CENTER
    assert stage.params["hue_width"] == ky.DEFAULT_HUE_WIDTH
    assert stage.params["softness"] == ky.DEFAULT_SOFTNESS
    assert stage.params["invert"] == ky.DEFAULT_INVERT
    print("StackPanel: a freshly-added 'keyer' stage starts at keyer.py's "
          "own defaults -- OK")


def test_keyer_inspector_fields_apply_live():
    panel, stage = _panel_with_one_keyer_stage()
    space_idx = panel.ky_space_combo.findText(cw.REFERENCE_LINEAR_SPACE)
    assert space_idx >= 0
    panel.ky_space_combo.setCurrentIndex(space_idx)
    panel.ky_hue_center_spin.setValue(210.0)
    panel.ky_hue_width_spin.setValue(45.0)
    panel.ky_chroma_min_spin.setValue(0.05)
    panel.ky_chroma_max_spin.setValue(0.3)
    panel.ky_lightness_min_spin.setValue(0.1)
    panel.ky_lightness_max_spin.setValue(0.8)
    panel.ky_softness_spin.setValue(0.6)
    panel.ky_invert_checkbox.setChecked(True)
    assert stage.params["working_colourspace"] == cw.REFERENCE_LINEAR_SPACE
    assert stage.params["hue_center"] == 210.0
    assert stage.params["hue_width"] == 45.0
    assert stage.params["chroma_min"] == 0.05
    assert stage.params["chroma_max"] == 0.3
    assert stage.params["lightness_min"] == 0.1
    assert stage.params["lightness_max"] == 0.8
    assert stage.params["softness"] == 0.6
    assert stage.params["invert"] is True
    print("StackPanel Keyer inspector: every field applies live to the "
          "selected stage's params -- OK")


def test_keyer_inspector_title_shows_its_own_matte_letter():
    panel, stage = _panel_with_one_keyer_stage()
    assert panel.ky_group.title() == "Keyer (A)"
    print("StackPanel Keyer inspector: the group title shows this "
          "stage's own current matte letter -- OK")


def test_keyer_inspector_title_updates_when_the_letter_shifts_on_reorder():
    panel, stage = _panel_with_one_keyer_stage()
    other_keyer = st.make_keyer_stage()
    panel.stack.add_stage(other_keyer)
    panel.stack.move_stage(other_keyer.id, 0)  # now BEFORE the inspected keyer
    panel._on_row_clicked(stage.id)
    assert panel.ky_group.title() == "Keyer (B)"
    print("StackPanel Keyer inspector: the title's own letter updates "
          "when reordering shifts it -- OK")


def test_highlight_checkbox_toggles_get_highlight_stage_id():
    panel, stage = _panel_with_one_keyer_stage()
    assert panel.get_highlight_stage_id() is None
    panel.ky_highlight_checkbox.setChecked(True)
    assert panel.get_highlight_stage_id() == stage.id
    panel.ky_highlight_checkbox.setChecked(False)
    assert panel.get_highlight_stage_id() is None
    print("StackPanel: the Highlight checkbox toggles "
          "get_highlight_stage_id() -- OK")


def test_highlight_checkbox_toggle_emits_stack_changed():
    panel, stage = _panel_with_one_keyer_stage()
    seen = []
    panel.stack_changed.connect(lambda: seen.append(True))
    panel.ky_highlight_checkbox.setChecked(True)
    assert seen == [True]
    print("StackPanel: toggling Highlight emits stack_changed so the "
          "Viewer actually re-renders -- OK")


def test_selecting_a_different_stage_auto_clears_highlight():
    panel, stage = _panel_with_one_keyer_stage()
    panel.ky_highlight_checkbox.setChecked(True)
    assert panel.get_highlight_stage_id() == stage.id

    other = st.make_cdl_stage()
    panel._add_stage(other)  # selects `other`, moving selection away from the keyer
    assert panel.get_highlight_stage_id() is None
    print("StackPanel: selecting a different stage auto-clears "
          "Highlight -- OK")


def test_reselecting_the_same_highlighted_stage_does_not_clear_it():
    """The auto-clear in _on_selection_changed must only fire when
    selection actually moves AWAY -- re-clicking (or a no-op reselect
    of) the SAME highlighted stage must leave it on."""
    panel, stage = _panel_with_one_keyer_stage()
    panel.ky_highlight_checkbox.setChecked(True)
    panel._on_row_clicked(stage.id)
    assert panel.get_highlight_stage_id() == stage.id
    print("StackPanel: reselecting the SAME highlighted stage leaves "
          "Highlight on -- OK")


def test_highlight_checkbox_syncs_when_switching_between_two_keyers():
    panel, stage = _panel_with_one_keyer_stage()
    panel.ky_highlight_checkbox.setChecked(True)
    other_keyer = st.make_keyer_stage()
    panel._add_stage(other_keyer)  # selects other_keyer; auto-clears highlight
    assert panel.ky_highlight_checkbox.isChecked() is False
    print("StackPanel: the Highlight checkbox itself resyncs (unchecked) "
          "when switching to a different keyer -- OK")


def test_ky_pick_clicked_requests_the_eyedropper_with_a_callback():
    panel, stage = _panel_with_one_keyer_stage()
    requests = []
    panel.set_eyedropper_requester(lambda callback: requests.append(callback))
    panel._on_ky_pick_clicked()
    assert len(requests) == 1 and callable(requests[0])
    print("StackPanel: clicking Pick requests the Viewer's own eyedropper "
          "via the wired requester -- OK")


def test_ky_pick_clicked_is_a_noop_without_a_wired_requester():
    panel, stage = _panel_with_one_keyer_stage()
    panel._on_ky_pick_clicked()  # never wired -- must not raise
    print("StackPanel: Pick is a no-op when no eyedropper requester was "
          "ever wired -- OK")


def _oklch_of(rgb, working_colourspace):
    sample = np.array(rgb, dtype=np.float64).reshape(1, 1, 3)
    L, C, H = cw.decode_working_colourspace_to_oklch(sample, working_colourspace)
    return float(L[0, 0]), float(C[0, 0]), float(H[0, 0])


def test_ky_pixel_picked_sets_a_tight_box_around_the_picked_pixel():
    """Per Tom (after trying the hue-only v1): picking a red building was
    also catching beige ones, because chroma/lightness were left at their
    default full-range (effectively "no gating") bands -- only hue was
    narrowed. Pick now seeds a TIGHT box on all three axes (see
    _on_ky_pixel_picked's own PICK_*_HALF_WIDTH constants), decoded
    through THIS stage's own working_colourspace (its default, ACEScct,
    since _panel_with_one_keyer_stage never changes it) -- matching
    exactly what keyer.compute_matte itself would decode for the same
    pixel."""
    panel, stage = _panel_with_one_keyer_stage()
    panel.ky_softness_spin.setValue(0.4)

    expected_l, expected_c, expected_hue = _oklch_of((0.6, 0.2, 0.2), ky.DEFAULT_WORKING_COLOURSPACE)
    panel._on_ky_pixel_picked(stage.id, (0.6, 0.2, 0.2))

    # Spin boxes round to their own displayed precision (1 decimal for
    # hue, 3 for chroma, 2 for lightness -- see _build_keyer_inspector),
    # so these aren't bit-exact.
    assert abs(stage.params["hue_center"] - expected_hue) < 0.05
    assert abs(stage.params["hue_width"] - StackPanel.PICK_HUE_HALF_WIDTH) < 0.05
    assert abs(stage.params["chroma_min"] - (expected_c - StackPanel.PICK_CHROMA_HALF_WIDTH)) < 0.005
    assert abs(stage.params["chroma_max"] - (expected_c + StackPanel.PICK_CHROMA_HALF_WIDTH)) < 0.005
    assert abs(stage.params["lightness_min"] - (expected_l - StackPanel.PICK_LIGHTNESS_HALF_WIDTH)) < 0.005
    assert abs(stage.params["lightness_max"] - (expected_l + StackPanel.PICK_LIGHTNESS_HALF_WIDTH)) < 0.005
    assert stage.params["softness"] == 0.4, "picking must not disturb Softness"
    print(f"StackPanel: a picked pixel seeds a tight Hue/Chroma/Lightness "
          f"box around it (hue={expected_hue:.2f}), leaving Softness alone -- OK")


def test_ky_pixel_picked_decodes_through_this_stages_own_working_colourspace():
    """Direct regression coverage for the fix: two keyers with DIFFERENT
    working_colourspace values must decode the SAME picked RGB to
    DIFFERENT hues -- proving the decode genuinely reads each stage's
    own param rather than any single fixed space."""
    panel, stage = _panel_with_one_keyer_stage()
    reference_space = cw.REFERENCE_LINEAR_SPACE
    idx = panel.ky_space_combo.findText(reference_space)
    assert idx >= 0
    panel.ky_space_combo.setCurrentIndex(idx)
    assert stage.params["working_colourspace"] == reference_space

    picked_rgb = (0.6, 0.2, 0.2)
    hue_in_aces = _oklch_hue_of(picked_rgb, ky.DEFAULT_WORKING_COLOURSPACE)
    hue_in_reference = _oklch_hue_of(picked_rgb, reference_space)
    assert abs(hue_in_aces - hue_in_reference) > 0.5, "the two spaces must actually decode differently for this test to mean anything"

    panel._on_ky_pixel_picked(stage.id, picked_rgb)
    assert abs(stage.params["hue_center"] - hue_in_reference) < 0.05
    print("StackPanel: the picked pixel decodes through THIS stage's own "
          "working_colourspace, not a fixed one -- OK")


def test_ky_pixel_picked_is_ignored_if_selection_moved_to_a_different_stage():
    panel, stage = _panel_with_one_keyer_stage()
    original_hue = stage.params["hue_center"]
    other = st.make_cdl_stage()
    panel._add_stage(other)  # selection moves to `other`

    panel._on_ky_pixel_picked(stage.id, (0.6, 0.2, 0.2))
    assert stage.params["hue_center"] == original_hue
    print("StackPanel: a stale pick (selection moved away before the "
          "Viewer click landed) is silently ignored -- OK")


def _oklch_hue_of(rgb, working_colourspace):
    sample = np.array(rgb, dtype=np.float64).reshape(1, 1, 3)
    _, _, h = cw.decode_working_colourspace_to_oklch(sample, working_colourspace)
    return float(h[0, 0])


def test_keyer_refresh_on_select_does_not_corrupt_stored_values():
    panel, stage = _panel_with_one_keyer_stage()
    panel.ky_hue_center_spin.setValue(77.0)
    panel.ky_softness_spin.setValue(0.42)
    stored_hue = stage.params["hue_center"]
    stored_softness = stage.params["softness"]

    other = st.make_cdl_stage()
    panel.stack.add_stage(other)
    panel._on_row_clicked(other.id)
    panel._on_row_clicked(stage.id)  # back to the keyer stage

    assert stage.params["hue_center"] == stored_hue
    assert stage.params["softness"] == stored_softness
    print("StackPanel Keyer inspector: switching away and back does not "
          "corrupt the stage's stored params -- OK")


def test_keyer_row_hides_its_own_matte_combo():
    panel, stage = _panel_with_one_keyer_stage()
    row = panel._stage_row_widgets[stage.id]
    assert row.matte_combo.isVisible() is False
    print("_StageRowWidget: a 'keyer' row's own matte combo is hidden "
          "(a keyer doesn't consume a matte in v1) -- OK")


def test_keyer_row_shows_its_own_matte_letter_badge():
    panel, stage = _panel_with_one_keyer_stage()
    row = panel._stage_row_widgets[stage.id]
    # isHidden() (this widget's OWN explicit hidden state), not
    # isVisible() -- these tests never actually .show() the row widget
    # inside a real top-level window, so isVisible() would read False
    # regardless of setVisible(True) here (it also depends on every
    # ancestor actually being shown on screen). isHidden() reflects
    # only what THIS widget's own setVisible call last set.
    assert row.matte_letter_badge.isHidden() is False
    assert row.matte_letter_badge.text() == "A"
    print("_StageRowWidget: a matte-producing row shows its own current "
          "letter in a dedicated badge -- OK")


def test_non_matte_producing_row_hides_the_matte_letter_badge():
    panel, stage = _panel_with_one_keyer_stage()
    cdl_stage = st.make_cdl_stage()
    panel._add_stage(cdl_stage)
    row = panel._stage_row_widgets[cdl_stage.id]
    assert row.matte_letter_badge.isVisible() is False
    print("_StageRowWidget: an ordinary (non-matte-producing) row's "
          "letter badge stays hidden -- OK")


def test_matte_letter_badge_updates_on_reorder():
    panel, stage = _panel_with_one_keyer_stage()
    other_keyer = st.make_keyer_stage()
    panel._add_stage(other_keyer)
    assert panel._stage_row_widgets[other_keyer.id].matte_letter_badge.text() == "B"
    panel.stack.move_stage(other_keyer.id, 0)
    panel._refresh_list(keep_selection=other_keyer.id)
    assert panel._stage_row_widgets[other_keyer.id].matte_letter_badge.text() == "A"
    assert panel._stage_row_widgets[stage.id].matte_letter_badge.text() == "B"
    print("_StageRowWidget: the letter badge reflects the current "
          "stack order after a reorder + refresh -- OK")


def test_downstream_stage_row_offers_the_upstream_keyer_as_a_matte_option():
    panel, keyer_stage = _panel_with_one_keyer_stage()
    cdl_stage = st.make_cdl_stage()
    panel._add_stage(cdl_stage)
    row = panel._stage_row_widgets[cdl_stage.id]
    items = [row.matte_combo.itemText(i) for i in range(row.matte_combo.count())]
    assert items == ["None", "A"]
    assert row.matte_combo.itemData(1) == keyer_stage.id
    print("_StageRowWidget: a downstream row's matte combo offers "
          "'None' plus the upstream keyer's own current letter -- OK")


def test_downstream_stage_row_offers_an_upstream_segment_mask_as_a_matte_option():
    """Regression guard: _matte_options_by_index used to hardcode
    "keyer", so a "segment_mask" stage never showed up as an offerable
    matte option even though stack.matte_letters_for already treats it
    as matte-producing -- fixed to key off stack.MATTE_PRODUCING_KINDS."""
    _app()
    panel = StackPanel()
    seg_stage = st.make_segment_mask_stage()
    panel._add_stage(seg_stage)
    cdl_stage = st.make_cdl_stage()
    panel._add_stage(cdl_stage)
    row = panel._stage_row_widgets[cdl_stage.id]
    items = [row.matte_combo.itemText(i) for i in range(row.matte_combo.count())]
    assert items == ["None", "A"]
    assert row.matte_combo.itemData(1) == seg_stage.id
    print("_StageRowWidget: a downstream row's matte combo also offers "
          "an upstream 'segment_mask' stage's own current letter -- OK")


def test_selecting_a_matte_option_writes_matte_source_id():
    panel, keyer_stage = _panel_with_one_keyer_stage()
    cdl_stage = st.make_cdl_stage()
    panel._add_stage(cdl_stage)
    row = panel._stage_row_widgets[cdl_stage.id]

    row.matte_combo.setCurrentIndex(1)  # "A" -- the keyer
    assert cdl_stage.params.get("matte_source_id") == keyer_stage.id

    row.matte_combo.setCurrentIndex(0)  # back to "None"
    assert cdl_stage.params.get("matte_source_id") is None
    print("StackPanel: selecting a row's matte combo writes/clears "
          "matte_source_id on that stage's own params -- OK")


def test_matte_combo_never_offers_a_downstream_keyer():
    """A stage positioned BEFORE a keyer must not be offered that
    keyer's letter at all -- see stack.py's own 'can't reference a
    matte that doesn't exist yet' guarantee, enforced here purely by
    which stages precede this row in the flat stack order."""
    _app()
    panel = StackPanel()
    cdl_stage = st.make_cdl_stage()
    panel._add_stage(cdl_stage)
    keyer_stage = st.make_keyer_stage()
    panel._add_stage(keyer_stage)  # appended AFTER the CDL stage
    row = panel._stage_row_widgets[cdl_stage.id]
    items = [row.matte_combo.itemText(i) for i in range(row.matte_combo.count())]
    assert items == ["None"]
    print("_StageRowWidget: a row never offers a keyer that sits "
          "downstream of it -- OK")


def test_matte_combo_options_update_on_reorder():
    """Dragging a keyer below its own former consumer must drop that
    consumer's selection back to 'None' cleanly on the next refresh --
    the row-widget-level counterpart to test_matte_source_id_referencing_
    a_downstream_keyer_is_ignored in test_stack.py."""
    panel, keyer_stage = _panel_with_one_keyer_stage()
    cdl_stage = st.make_cdl_stage()
    panel._add_stage(cdl_stage)
    row = panel._stage_row_widgets[cdl_stage.id]
    row.matte_combo.setCurrentIndex(1)
    assert cdl_stage.params.get("matte_source_id") == keyer_stage.id

    panel.stack.move_stage(keyer_stage.id, len(panel.stack.stages) - 1)  # keyer now AFTER cdl_stage
    panel._refresh_list(keep_selection=cdl_stage.id)

    row = panel._stage_row_widgets[cdl_stage.id]
    items = [row.matte_combo.itemText(i) for i in range(row.matte_combo.count())]
    assert items == ["None"]
    # The row's own displayed selection falls back to "None" (its own
    # combo no longer HAS an item for the now-downstream keyer), even
    # though stage.params["matte_source_id"] itself is left untouched --
    # see stack.py's own docstring on this being a self-healing runtime
    # fallback rather than something that needs active cleanup here.
    assert row.matte_combo.currentText() == "None"
    print("StackPanel: reordering a keyer below its consumer drops that "
          "row's own combo selection back to 'None' on refresh -- OK")


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
    test_insert_entry_in_stack_with_a_stack_memory_adds_all_its_stages_below_selected()
    test_insert_entry_in_stack_with_a_stack_memory_uses_fresh_ids()
    test_insert_entry_in_stack_with_a_cdl_entry_adds_one_new_stage()
    test_insert_entry_in_stack_never_overwrites_a_selected_cdl_stage()
    test_printer_lights_inspector_uses_spinboxes_not_sliders()
    test_printer_lights_spins_have_drag_to_scrub_enabled()
    test_printer_lights_spin_change_applies_live()
    test_printer_lights_gang_mirrors_r_to_g_and_b()
    test_printer_lights_reset_zeroes_all_three_spins()
    test_grading_primary_field_has_a_color_balance_wheel()
    test_grading_primary_wheel_drag_shifts_rgb_leaves_master_alone()
    test_grading_primary_master_edit_shifts_rgb_preserves_wheel_position()
    test_grading_primary_channel_spin_edit_moves_wheel_leaves_master_alone()
    test_grading_primary_reset_zeroes_wheel_and_all_fields()
    test_grading_primary_refresh_on_select_does_not_corrupt_stored_values()
    test_grading_tone_range_has_a_color_balance_wheel()
    test_grading_tone_wheel_drag_shifts_rgb_leaves_master_alone()
    test_grading_tone_channel_spin_edit_moves_wheel_leaves_master_alone()
    test_grading_tone_reset_zeroes_wheel_and_all_ranges()
    test_grading_tone_start_width_are_unaffected_by_wheel_sync()
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
    test_param_label_double_click_calls_its_reset_callback()
    test_printer_lights_channel_label_double_click_resets_just_that_channel()
    test_cdl_field_label_double_click_resets_whole_field_to_identity()
    test_cdl_channel_label_double_click_resets_just_that_channel()
    test_grading_primary_field_label_double_click_resets_whole_field_to_identity()
    test_grading_primary_channel_label_double_click_resets_just_that_channel()
    test_grading_tone_range_label_double_click_resets_whole_range_to_identity()
    test_grading_tone_start_width_labels_reset_individually()
    test_grading_tone_channel_label_double_click_resets_just_that_channel()
    test_exposure_contrast_field_label_double_click_resets_that_field()
    test_add_color_warper_starts_at_identity()
    test_color_warper_wheel_drag_applies_live()
    test_color_warper_wake_and_dropoff_spins_apply_live()
    test_color_warper_space_combo_applies_live()
    test_color_warper_reset_restores_identity()
    test_color_warper_refresh_on_select_does_not_corrupt_stored_values()
    test_color_warper_badge_text()
    test_keyer_badge_text()
    test_add_keyer_starts_at_defaults()
    test_keyer_inspector_fields_apply_live()
    test_keyer_inspector_title_shows_its_own_matte_letter()
    test_keyer_inspector_title_updates_when_the_letter_shifts_on_reorder()
    test_highlight_checkbox_toggles_get_highlight_stage_id()
    test_highlight_checkbox_toggle_emits_stack_changed()
    test_selecting_a_different_stage_auto_clears_highlight()
    test_reselecting_the_same_highlighted_stage_does_not_clear_it()
    test_highlight_checkbox_syncs_when_switching_between_two_keyers()
    test_ky_pick_clicked_requests_the_eyedropper_with_a_callback()
    test_ky_pick_clicked_is_a_noop_without_a_wired_requester()
    test_ky_pixel_picked_sets_a_tight_box_around_the_picked_pixel()
    test_ky_pixel_picked_decodes_through_this_stages_own_working_colourspace()
    test_ky_pixel_picked_is_ignored_if_selection_moved_to_a_different_stage()
    test_keyer_refresh_on_select_does_not_corrupt_stored_values()
    test_keyer_row_hides_its_own_matte_combo()
    test_keyer_row_shows_its_own_matte_letter_badge()
    test_non_matte_producing_row_hides_the_matte_letter_badge()
    test_matte_letter_badge_updates_on_reorder()
    test_downstream_stage_row_offers_the_upstream_keyer_as_a_matte_option()
    test_downstream_stage_row_offers_an_upstream_segment_mask_as_a_matte_option()
    test_selecting_a_matte_option_writes_matte_source_id()
    test_matte_combo_never_offers_a_downstream_keyer()
    test_matte_combo_options_update_on_reorder()
    print()
    print("All stack_panel tests passed.")
