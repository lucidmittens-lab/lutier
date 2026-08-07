"""
stack_panel.py -- the single, unified operation stack: build an ordered
list of stages (a Printer Lights trim, an ACES DRT output transform, or an
ASC CDL SOP/SAT grade; LUT/Identity stages are added via the Browser panel
-- see add_lut_stage_from_path/add_identity_stage below), reorder them,
toggle any stage on/off or give it its own opacity, tweak a selected
stage's own parameters in place, and the whole thing re-bakes live into
one combined LUT feeding the 3D viewer / sample image preview / scopes /
exporter.

ASC CDL SCOPE (this round, per Tom -- "first get an ASC SOP / ASC SAT
operator working, then we'll add a panel for a CDL library"): this adds
the CDL stage kind itself (see cdl.py for the SOP+SAT math, via OCIO's
CDLTransform) plus its inspector below, so a CDL grade composes into the
stack like any other stage and survives save/load (session_io.py). A
dedicated CDL *library* panel (save/recall/organize named CDL grades,
mirroring stack_memories_panel.py) and .cc/.cdl/.ccc import/export
(mirroring format_writers.py's pattern) are the deliberate next steps, not
built here.

This REPLACES concatenate_panel.py, aces_drt_panel.py, and the
Retarget/Resize/Bounds/Printer-Lights sections of lut_controls_panel.py --
all of that functionality is still here, just unified into one reorderable
stack instead of four separate "operate on the current LUT" panels that
each destructively overwrote the same slot. See stack.py's module
docstring for the full reasoning (why this was feasible without a math
rewrite, and why Resize/Bounds became stack-level settings rather than
their own stage kind).

All the actual math -- Stage/Stack, bake_stack_to_lut3d,
apply_stack_to_image_array -- lives in stack.py and is fully tested there
without Qt (test_stack.py: single-stage stacks matching every prior
one-purpose bake function exactly, opacity/order/enable semantics,
resize-by-rebaking being strictly more accurate than resampling an
already-baked table). This file is just the widget.

TWO SEPARATE WIDGETS, ONE CONTROLLER: StackPanel is a QObject (not a
QWidget) so it can hand MainWindow two independently placeable widgets
sharing the same underlying Stack/selection state --
`list_widget_area` (the stage list + reorder/remove + Enabled/Opacity,
meant to sit in the main/center of the window) and `params_widget_area`
(just the per-stage inspector now, meant to sit in a side panel) --
rather than one big combined panel. All the logic that used to live in
one _build_layout is split into _build_list_ui and _build_params_ui, but
every other method (selection handling, the inspector dispatch,
add/remove/reorder) is unchanged.

Grid Size/Domain (_build_global_settings_dialog) used to be a QGroupBox
inline at the bottom of params_widget_area, but that made Stack Params'
own minimum height big enough to leave ~0 slack in the right-column
splitter for anything else (see main_window.py's docstring on the
toggle-visibility quirks) -- moved into its own QDialog instead, opened
via a button in list_widget_area (the stage list), since it's edited
rarely compared to per-stage params. All the same fields/validation/Apply
behavior, just windowed instead of inline; see _show_global_settings_dialog.

This sandbox originally had no PySide6 install at all -- fixed by
installing the full requirements.txt plus a stub libEGL.so.1 (see
.eglstub/ in the outputs folder, LD_LIBRARY_PATH pointed at it) that
no-ops every EGL call, letting QApplication construct under
QT_QPA_PLATFORM=offscreen. Everything below has now actually been
exercised headless (constructed a real MainWindow, driven real
QListWidget/QToolButton/QSlider/QComboBox objects, asserted on the
resulting Stage/params state) -- NOT just py_compile'd. Still worth a
real on-screen pass for anything about actual pixel rendering (3D
viewer, wipe compare, scopes) or real mouse drag-and-drop, which offscreen
can't meaningfully exercise (drag tests here call dropEvent()/the
StackPanel callbacks it delegates to directly, with a manually built
QDropEvent for the Browser-drop case -- confirms the data-level +
Qt-drop-action logic, not actual on-screen mouse-drag ergonomics).

Headless-verified across sessions: (1) Add Printer Lights / Add ACES DRT
(in the Stack panel's "..." header menu, see _build_list_menu_button) add
a stage and the 3D-view/sample-image-preview getter chain still wires up,
loading a file or Identity LUT from the Browser does too; (2) selecting a
stage shows the right inspector in the PARAMS area; (3) "Edit
Size/Domain..." (was "Stack Output...", now also in the header menu)
still opens the same dialog; (4) each stage renders as a bordered card
with the selected one highlighted; (5) a card's check/cross button
toggles that stage's enabled state directly, no selection needed;
(6) dragging a stage to a new position reorders the real Stack, INCLUDING
the single-stage-dragged-into-empty-space case that used to empty the
list entirely (see _StackListWidget.__init__'s CopyAction note -- Qt's
own startDrag() was auto-deleting the row a second time on top of our
own rebuild); (7) dragging a LUT in from the Browser adds it at the drop
position, same as double-clicking; (8) Printer Lights has no Update Stage
button anymore -- every slider drag and working-space change applies to
the selected stage and re-bakes immediately.

This round (per Tom): Move Up/Move Down/Remove are gone from below the
list entirely -- reordering was already drag-and-drop (see (6) above);
removing is now each row's own "x" (_StageRowWidget.delete_button ->
_on_row_delete_clicked, works on that row regardless of what's selected)
or Delete/Backspace with a row selected (_StackListWidget.keyPressEvent
-> _on_remove_clicked, the same method the old Remove button used to
call). Headless-verified: clicking a non-selected row's "x" removes THAT
stage without disturbing the actual selection; Delete/Backspace with
nothing selected is a safe no-op; both paths re-bake and update the
inspector correctly afterward.
"""
from pathlib import Path
from itertools import zip_longest

import numpy as np
from PySide6.QtCore import Qt, QObject, QSize, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLabel, QMessageBox, QComboBox, QSpinBox, QDoubleSpinBox,
    QGroupBox, QSlider, QCheckBox, QLineEdit, QStackedWidget, QInputDialog,
    QDialog, QDialogButtonBox, QFrame, QToolButton, QAbstractItemView, QMenu,
    QSizePolicy,
)

import stack as st
import aces_drt
import cdl
import colorspace_convert as csc
import color_balance as cb
from color_wheel_widget import ColorBalanceWheel
from app_logic import load_lut_any_format, create_identity_lut, list_available_colourspaces
from printer_lights import POINTS_PER_STOP, DEFAULT_WORKING_COLOURSPACE
from browser_panel import LUT_PATH_MIME
from floating_dialog import make_floating_tool_window, show_floating
# style.HINT_TEXT_HEX -- see style.py's FOURTEENTH PASS docstring: hint
# text is a literal hex now, not palette(placeholder-text) (doesn't
# reliably survive Qt's style-polish pass on this Qt build).
from style import HINT_TEXT_HEX

COMMON_SIZES = [9, 17, 25, 33, 45, 65, 97]
PRINTER_LIGHT_RANGE = 50

# CDL SOP fields: (params key, display label, spin min, spin max, identity
# value, spin step, wheel strength). "wheel strength" is how much a
# channel's value shifts when the puck sits at the wheel's very edge along
# that channel's own axis -- see color_balance.py's rgb_from_master_and_wheel.
# 1.0 for all three keeps the wheel's full-radius swing within each field's
# own spin range around identity (e.g. offset's [-1,1] range, slope/power's
# identity-of-1.0 with headroom on both sides) without needing a separate
# per-field tuning pass -- exact numeric extremes are still reachable via
# the fine R/G/B spinboxes regardless of what the wheel's radius can reach.
CDL_SOP_FIELD_SPECS = [
    ("slope", "Slope", 0.0, 4.0, 1.0, 0.01, 1.0),
    ("offset", "Offset", -1.0, 1.0, 0.0, 0.001, 1.0),
    ("power", "Power", 0.01, 4.0, 1.0, 0.01, 1.0),
]
CDL_WHEEL_DIAMETER = 90
CDL_SAT_RANGE = 400  # slider units -> /100 -> 0.0-4.0, matching the spin range


def _format_cdl_label(params):
    """Stage-row label for a 'cdl' stage -- shows its actual SOP/SAT
    values instead of a static "ASC CDL" (per Tom: same "the row tells you
    what it actually does" convention the 'colorspace' stage's dynamic
    "Color Space (src -> dst)" label already follows). Appends "[SOP off]"/
    "[SAT off]" when the corresponding bypass toggle is off, so a bypassed
    half is obvious at a glance in the stage list, not just in the
    inspector. Shared by _on_add_cdl_clicked (initial identity label),
    _apply_cdl_live (every live edit), and apply_cdl_entry (recalling a
    CDL Library entry) so all three ways a 'cdl' stage's params can change
    keep the row's label in sync the same way."""
    r, g, b = params.get("slope", cdl.IDENTITY_SLOPE)
    ro, go, bo = params.get("offset", cdl.IDENTITY_OFFSET)
    rp, gp, bp = params.get("power", cdl.IDENTITY_POWER)
    sat = params.get("sat", cdl.IDENTITY_SAT)
    label = (
        f"ASC CDL  S({r:.2f},{g:.2f},{b:.2f}) "
        f"O({ro:.2f},{go:.2f},{bo:.2f}) "
        f"P({rp:.2f},{gp:.2f},{bp:.2f}) Sat {sat:.2f}"
    )
    if not params.get("sop_enabled", True):
        label += " [SOP off]"
    if not params.get("sat_enabled", True):
        label += " [SAT off]"
    return label


def _format_fork_merge_label(params):
    """Stage-row label for a 'fork_merge' stage -- shows its live A/B
    blend split instead of a static "Fork Merge" (same "the row tells you
    what it actually does" convention _format_cdl_label/the 'colorspace'
    stage's dynamic label already follow). blend=0.0 is 100% branch A,
    blend=1.0 is 100% branch B -- see stack.py's make_fork_merge_stage/
    _blend_two. Shared by _on_add_fork_clicked (initial 50/50 label) and
    _apply_fork_merge_live (every live slider edit)."""
    blend = params.get("blend", 0.5)
    a_pct = round((1.0 - blend) * 100)
    b_pct = round(blend * 100)
    return f"Fork Merge  (A {a_pct}% / B {b_pct}%)"


# Stack()'s own constructor defaults, read once rather than hardcoded here
# a second time -- used by _update_domain_readout to flag when the current
# grid size/domain differ from a freshly-created stack's, so it's obvious
# at a glance whether either has actually been touched.
_STACK_DEFAULTS = st.Stack()


class _ElidingLabel(QLabel):
    """A QLabel that elides its own text with "…" to fit whatever width
    it's actually given, instead of Qt's default QLabel behavior (its
    minimumSizeHint equals its full-text sizeHint with word-wrap off),
    which would force the row's QHBoxLayout to either grow the whole row
    past the panel's edge or squeeze a sibling widget instead -- per Tom:
    a stage row's label should truncate as needed so the row's own
    delete button ("x" at the far end) stays visible regardless of panel
    width, not get pushed off past it. The full, untruncated text always
    remains available as a tooltip, so nothing is actually lost, just
    visually shortened when the row is narrow.

    QFontMetrics-based (real per-pixel measurement of THIS label's own
    font against its actual current width, re-measured on every resize),
    not a fixed character-count guess -- correct at any zoom/font/DPI
    setting and any panel width, unlike a hardcoded "N characters" rule."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full_text = text
        # Ignored (not the QLabel default Preferred): tells the layout
        # this label's sizeHint should NOT set a floor on how wide the
        # row/layout needs to be -- without this, Qt would size the row
        # to comfortably fit the FULL text and elision would never
        # actually be exercised except by an explicit, tighter resize.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._apply_elided_text()

    def setText(self, text):
        self._full_text = text
        self._apply_elided_text()

    def full_text(self):
        return self._full_text

    def _apply_elided_text(self):
        elided = self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, max(self.width(), 0))
        super().setText(elided)
        # Tooltip only when actually truncated -- a tooltip that just
        # repeats the fully-visible text on hover would be pure noise.
        self.setToolTip(self._full_text if elided != self._full_text else "")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_elided_text()

    def minimumSizeHint(self):
        # A few characters' worth is enough of a floor -- anything
        # smaller and there'd be nothing readable left to elide down to;
        # Qt's own default (the full untruncated text's width) is
        # exactly what this class exists to override.
        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance("…m"), super().minimumSizeHint().height())


class _StageRowWidget(QFrame):
    """One stage's "card" in the stack list (see StackPanel._refresh_list).
    A bordered card per stage, a directly clickable enabled indicator, and
    the stage label/opacity broken out as their own bits of text instead
    of one hand-formatted string.

    Selecting a stage: a setItemWidget()-covered QListWidgetItem doesn't
    get Qt's normal "click anywhere in the row selects it" behavior for
    free (the item widget eats the mouse press before QAbstractItemView
    ever sees it) -- so this forwards any press that isn't on the enabled
    toggle, branch combo, or delete button to StackPanel via
    on_row_clicked(stage.id).

    Delete button ("x" at the row's far end) removes THIS stage directly,
    no selection required first -- same "act on the row you clicked, not
    whatever happens to be selected" convention as the enabled toggle.
    Reordering is drag-and-drop (see _StackListWidget), removal is this
    button or Delete/Backspace on the selected row (see
    _StackListWidget.keyPressEvent).

    STACK FORKING (per Tom): fork_split/fork_merge stages get this exact
    same card, no special styling -- Tom's call after the first pass
    ("bulky and non-matching") was that a fork region's boundaries should
    read as ordinary operator rows, not marker rows; the region itself is
    now visually obvious from the two-column table between them (see
    StackPanel._flush_fork_grid/_ForkBranchGridRowWidget) rather than from
    a decorated split/merge row. `compact=True` is used for a cell INSIDE
    that two-column table (via _ForkBranchGridRowWidget) -- tighter
    margins, no stage-number prefix (a single incrementing number doesn't
    mean much once two branches run in parallel), an elided label (see
    _ElidingLabel, full text still in the tooltip), and a narrow single-letter
    branch combo ("A"/"B"/"—") instead of the full "Branch A"/"Branch B"/
    "No branch" text, since a compact cell is only ever used for a stage
    that's ALREADY inside a fork region (column position already implies
    which branch), so the combo here is purely a way to move it to the
    OTHER branch or clear it back to unassigned, not to newly place a
    stage that has nowhere better to go yet -- that's what the wider combo
    on a full-width row is for."""

    def __init__(self, stage, on_row_clicked, on_enabled_clicked, on_delete_clicked,
                 on_branch_changed=None, in_fork_region=False, compact=False,
                 display_number=None, parent=None):
        super().__init__(parent)
        self.stage_id = stage.id
        self._on_row_clicked = on_row_clicked
        self.setObjectName("stackStageRow")
        self.setProperty("stageEnabled", stage.enabled)
        is_fork_marker = stage.kind in ("fork_split", "fork_merge")
        # Only flagged for QSS on a stray tag OUTSIDE a fork region (see
        # style.py's [stageBranch] rule) -- inside the two-column table,
        # column position already says which branch a cell belongs to, so
        # repeating it as a per-cell accent would be exactly the kind of
        # redundant decoration Tom's "bulky" note was about.
        if stage.branch and not compact:
            self.setProperty("stageBranch", stage.branch)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        if compact:
            layout.setContentsMargins(5, 3, 5, 3)
            layout.setSpacing(4)
        else:
            layout.setContentsMargins(8, 6, 8, 6)
            layout.setSpacing(8)

        self.enabled_button = QToolButton()
        self.enabled_button.setObjectName("stageEnabledToggle")
        self.enabled_button.setAutoRaise(True)
        self.enabled_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.enabled_button.setToolTip("Click to enable/disable this stage")
        self.enabled_button.clicked.connect(lambda: on_enabled_clicked(stage.id))
        self._set_enabled_glyph(stage.enabled)
        layout.addWidget(self.enabled_button)

        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        label_text = stage.label if display_number is None else f"{display_number}. {stage.label}"
        # _ElidingLabel truncates with "…" to fit whatever width it
        # actually ends up with (compact fork-grid cell or a full-width
        # row alike), keeping the untruncated text as its own tooltip --
        # see that class's docstring. Same mechanism for both cases now
        # (previously compact-only, via a cruder fixed-character-count
        # _elide()) since a full-width row can run out of space too, per
        # Tom: the delete button at the row's far end should stay visible
        # regardless of panel width, not get pushed off past it.
        self.label_widget = _ElidingLabel(label_text)
        self.label_widget.setObjectName("stageLabel")
        self.label_widget.setProperty("stageEnabled", stage.enabled)
        # Transparent to mouse events -- otherwise a click on the label
        # text itself is swallowed by the QLabel rather than reaching this
        # frame's mousePressEvent below, and "click anywhere on the card
        # to select it" would only work in the card's empty margins.
        self.label_widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        text_col.addWidget(self.label_widget)
        if stage.opacity < 1.0:
            opacity_label = QLabel(f"{round(stage.opacity * 100)}% opacity")
            opacity_label.setStyleSheet(f"color: {HINT_TEXT_HEX}; font-size: 10px;")
            opacity_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            text_col.addWidget(opacity_label)
        layout.addLayout(text_col, stretch=1)

        # Branch A/B assignment -- only shown for an ordinary stage (not
        # fork_split/fork_merge themselves) that's either currently inside
        # an open fork region (in_fork_region) or already carries a branch
        # tag that needs a way to be cleared (a stray tag from having been
        # left outside a region -- see stack.py's Stage docstring: inert,
        # not an error, but still worth surfacing so it doesn't look like
        # it silently vanished). No combo at all for the common case of an
        # ordinary stage nowhere near a fork, keeping every other row
        # exactly as before.
        self.branch_combo = None
        if not is_fork_marker and (in_fork_region or stage.branch):
            self.branch_combo = QComboBox()
            self.branch_combo.setObjectName("stageBranchCombo")
            if compact:
                # Single-letter items -- this cell is already inside the
                # two-column table, so the combo's only job here is
                # "move to the other branch" or "clear," not "place."
                self.branch_combo.addItem("—", None)
                self.branch_combo.addItem("A", "a")
                self.branch_combo.addItem("B", "b")
                self.branch_combo.setFixedWidth(40)
            else:
                self.branch_combo.addItem("No branch", None)
                self.branch_combo.addItem("Branch A", "a")
                self.branch_combo.addItem("Branch B", "b")
            idx = self.branch_combo.findData(stage.branch)
            self.branch_combo.setCurrentIndex(idx if idx >= 0 else 0)
            if stage.branch and not in_fork_region:
                self.branch_combo.setToolTip(
                    f"Tagged Branch {stage.branch.upper()}, but this stage isn't "
                    "between a matched Fork Split/Fork Merge pair right now -- "
                    "the tag is inert. Pick \"No branch\" to clear it, or move "
                    "this stage back inside the fork region.")
            else:
                self.branch_combo.setToolTip(
                    "Move this stage to Branch A or B, or clear it back to "
                    "unassigned.")
            if on_branch_changed is not None:
                self.branch_combo.currentIndexChanged.connect(
                    lambda _i: on_branch_changed(stage.id, self.branch_combo.currentData()))
            layout.addWidget(self.branch_combo)

        # "x" (not the enabled toggle's checkmark/cross glyphs -- a
        # DIFFERENT symbol on purpose, so the two aren't visually
        # confusable at a glance) at the row's far end.
        self.delete_button = QToolButton()
        self.delete_button.setObjectName("stageDeleteButton")
        self.delete_button.setAutoRaise(True)
        self.delete_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_button.setText("×")
        self.delete_button.setToolTip("Remove this stage")
        self.delete_button.clicked.connect(lambda: on_delete_clicked(stage.id))
        layout.addWidget(self.delete_button)

    def _set_enabled_glyph(self, enabled):
        self.enabled_button.setText("✓" if enabled else "✗")  # checkmark / cross
        self.enabled_button.setProperty("stageEnabled", enabled)

    def set_selected(self, selected):
        self.setProperty("stageSelected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event):
        # Let the enabled toggle / branch combo / delete button handle
        # their own clicks -- only treat this as "select the row" when the
        # press lands elsewhere on the card (label, opacity text, empty
        # margin).
        pos = event.position().toPoint()
        on_branch_combo = self.branch_combo is not None and self.branch_combo.geometry().contains(pos)
        if not (self.enabled_button.geometry().contains(pos)
                or self.delete_button.geometry().contains(pos)
                or on_branch_combo):
            self._on_row_clicked(self.stage_id)
        super().mousePressEvent(event)


class _EmptyBranchCell(QFrame):
    """A blank placeholder cell inside the fork region's two-column table
    (see _ForkBranchGridRowWidget) -- keeps the two branches' row counts
    equal without pretending there's a real stage there. Deliberately
    inert: nothing to click, select, or drag. Only ever appears past the
    end of the SHORTER branch's own stage list (see
    StackPanel._flush_fork_grid's zip_longest), so empty cells can only
    pad the bottom of one column, never alternate with real ones --
    that's what "can't checkerboard" (per Tom) comes down to structurally."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("stackForkEmptyCell")
        self.setMinimumHeight(30)


class _ForkBranchGridRowWidget(QFrame):
    """One row of the fork region's two-column table -- Branch A's stage
    at this position on the left half, Branch B's on the right half, each
    at stretch=1 so the split is a true 50/50 on the X axis (per Tom).
    Either side is an _EmptyBranchCell instead of a stage card when that
    branch has fewer stages than the other at this row (see
    StackPanel._flush_fork_grid)."""

    def __init__(self, left_stage, right_stage, on_row_clicked, on_enabled_clicked,
                 on_delete_clicked, on_branch_changed, parent=None):
        super().__init__(parent)
        self.setObjectName("stackForkGridRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.left_widget = self._build_cell(
            left_stage, on_row_clicked, on_enabled_clicked, on_delete_clicked, on_branch_changed)
        layout.addWidget(self.left_widget, stretch=1)

        self.right_widget = self._build_cell(
            right_stage, on_row_clicked, on_enabled_clicked, on_delete_clicked, on_branch_changed)
        layout.addWidget(self.right_widget, stretch=1)

    @staticmethod
    def _build_cell(stage, on_row_clicked, on_enabled_clicked, on_delete_clicked, on_branch_changed):
        if stage is None:
            return _EmptyBranchCell()
        return _StageRowWidget(
            stage, on_row_clicked, on_enabled_clicked, on_delete_clicked,
            on_branch_changed=on_branch_changed, in_fork_region=True, compact=True)

    def set_selected(self, selected_stage_id):
        """Highlights whichever cell (if either) matches the currently
        selected stage -- called by StackPanel._update_row_selection_
        styling the same way a plain _StageRowWidget's set_selected is,
        just fanned out to both of this composite row's cells."""
        for widget in (self.left_widget, self.right_widget):
            if isinstance(widget, _StageRowWidget):
                widget.set_selected(widget.stage_id == selected_stage_id)


class _StackListWidget(QListWidget):
    """The stage list itself. Two custom drag/drop behaviors layered on
    top of plain QListWidget, both routed through StackPanel's own
    Stack/_refresh_list rather than Qt's built-in item-move machinery:

    - Reordering stages by dragging one to a new position.
    - Dragging a LUT file in from the Browser panel's file list (see
      browser_panel.py's LUT_PATH_MIME / _DraggableFileListWidget) to add
      it as a new stage at the drop position.

    Both go through StackPanel's callbacks instead of relying on Qt's
    default InternalMove drop handling, which moves the QListWidgetItem
    but is unreliable about carrying its setItemWidget() content along for
    the ride -- rebuilding the whole list from self.stack via
    _refresh_list() after every drop sidesteps that entirely."""

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self._panel = panel
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        # CopyAction, not MoveAction -- dropEvent below does the actual
        # data-level move itself (via _refresh_list(), see class
        # docstring). If a drop here resolves to Qt::MoveAction,
        # QAbstractItemView::startDrag()'s own post-drag cleanup ALSO
        # removes the dragged row from this same widget afterwards --
        # redundant with (and racing) our rebuild, and with only one stage
        # in the list that second removal empties it entirely (reported by
        # Tom: dragging a single stack entry into empty space made it
        # vanish). Every dropEvent branch below explicitly sets CopyAction
        # before accepting so that cleanup never fires.
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

    def _accepts(self, event):
        return event.mimeData().hasFormat(LUT_PATH_MIME) or event.source() is self

    def dragEnterEvent(self, event):
        if self._accepts(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._accepts(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def _drop_row(self, pos):
        item = self.itemAt(pos)
        if item is None:
            return self.count()
        row = self.row(item)
        if pos.y() > self.visualItemRect(item).center().y():
            row += 1
        return row

    def dropEvent(self, event):
        mime = event.mimeData()
        target_row = self._drop_row(event.position().toPoint())
        if mime.hasFormat(LUT_PATH_MIME):
            path = bytes(mime.data(LUT_PATH_MIME)).decode("utf-8")
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self._panel._on_lut_path_dropped(path, target_row)
        elif event.source() is self:
            source_row = self.currentRow()
            # CopyAction here too (see __init__) -- this IS a move at the
            # data level, just one _on_stage_reordered/_refresh_list does
            # itself rather than handing back to Qt's own row-removal.
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self._panel._on_stage_reordered(source_row, target_row)
        else:
            event.ignore()

    def keyPressEvent(self, event):
        # Delete/Backspace removes the currently selected stage -- same
        # action as clicking that row's own "x" (_StageRowWidget.
        # delete_button) or the old Remove button this + that button
        # replaced (per Tom).
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._panel._on_remove_clicked()
            return
        super().keyPressEvent(event)


class StackPanel(QObject):
    """Emits stack_changed() after any mutation -- add/remove/reorder/
    enable-toggle/opacity change/param update/grid size/domain -- so
    MainWindow can re-bake and push the result to the 3D viewer/info
    panel, and SampleImagePanel can re-render its own direct-pixel
    preview via get_stack() + stack.apply_stack_to_image_array. A single
    signal covers everything, same "one emission point per mutation path"
    convention concatenate_panel.py established for chain_changed.

    list_widget_area and params_widget_area are the two widgets MainWindow
    actually places in the window (see this module's docstring) -- both
    parentless QWidgets until MainWindow puts them in a PanelFrame."""

    stack_changed = Signal()
    # (name, params dict) -- emitted by the CDL inspector's "Save to CDL
    # Library" button (see _on_cdl_save_to_library_clicked). This panel has
    # no idea a CDL Library exists; MainWindow routes this into
    # CDLLibraryPanel.save_entry, same "panels don't know about each other,
    # MainWindow bridges" architecture as StackMemoriesPanel.recall_requested
    # -> StackPanel.set_stack. `object` (not `dict`) as the second signal
    # type for safety across PySide6 versions -- Qt's signal machinery
    # handles a plain Python dict fine as an "object" payload either way.
    cdl_save_requested = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        # Guards every combo-driven live-apply handler (see
        # _apply_aces_drt_live/_apply_colorspace_live) against firing
        # mid-cascade while _refresh_inspector is repopulating combos for
        # a newly SELECTED stage -- switching to a stage shouldn't itself
        # count as a live edit, same intent as the blockSignals() calls
        # elsewhere in this file, just covering a case (cascading combo
        # rebuilds that legitimately need their own change signal to fire
        # in order to repopulate child combos) that a plain blockSignals
        # on the top-level combo alone can't cover.
        self._refreshing_inspector = False
        # The source of truth for "which stage is selected" -- a stage ID,
        # not a QListWidget row index. Necessary once a fork region's rows
        # stopped being 1:1 with stack.stages entries (see _refresh_list/
        # _flush_fork_grid, STACK FORKING): a single visual row inside the
        # two-column table can represent zero, one, or two stages at once,
        # so "row 4 is selected" stopped being a meaningful statement on
        # its own. self._row_stage_ids/_row_insert_index (rebuilt fresh by
        # every _refresh_list call) are the per-row metadata that lets
        # click handling, keyboard nav, and drag-and-drop all still work
        # in terms of this ID rather than a row position -- see
        # _on_selection_changed/_update_row_selection_styling/
        # _on_stage_reordered/_on_lut_path_dropped.
        self._selected_stage_id = None
        self._row_stage_ids = []
        self._row_insert_index = []
        self.stack = st.Stack()
        # Reused across repeated get_baked_lut() calls (MainWindow's
        # _on_stack_changed calls it on every single edit) so nudging one
        # stage's opacity/params doesn't re-bake every OTHER stage from
        # scratch too -- see StageCache's own docstring in stack.py.
        # set_stack (session load/undo-redo/Stack Memory recall, below)
        # clears it, since a wholesale-replaced Stack has nothing in
        # common with whatever was cached for the old one.
        self._bake_cache = st.StageCache()
        self.list_widget_area = QWidget()
        self.params_widget_area = QWidget()
        self._build_list_ui()
        self._build_params_ui()
        self._build_global_settings_dialog()
        self._update_domain_readout()
        self._refresh_list()
        self._refresh_inspector()

    # ---- list area (stage list, reorder, add) ----------------------------

    def _build_list_ui(self):
        layout = QVBoxLayout(self.list_widget_area)
        layout.setContentsMargins(0, 0, 0, 0)  # PanelFrame provides the outer gutter

        # _StackListWidget (not plain QListWidget) -- adds card-style rows
        # with a clickable enable toggle per stage, drag-to-reorder, and
        # drag-a-LUT-in-from-the-Browser -- see its docstring above.
        self.list_widget = _StackListWidget(self)
        self.list_widget.currentRowChanged.connect(self._on_selection_changed)
        layout.addWidget(self.list_widget, stretch=1)

        # No more standalone "Enabled" checkbox here -- each row's own
        # enabled indicator (the check/cross button) is directly clickable
        # now, see _StageRowWidget / _on_row_enabled_clicked.
        selection_row = QHBoxLayout()
        selection_row.addWidget(QLabel("Opacity:"))
        self.opacity_spin = QSpinBox()
        self.opacity_spin.setRange(0, 100)
        self.opacity_spin.setValue(100)
        self.opacity_spin.setSuffix("%")
        self.opacity_spin.valueChanged.connect(self._on_opacity_changed)
        selection_row.addWidget(self.opacity_spin)
        selection_row.addStretch(1)
        layout.addLayout(selection_row)

        # No more Move Up/Move Down/Remove button row (per Tom) --
        # reordering is drag-and-drop (see _StackListWidget), removing is
        # each row's own "x" (_StageRowWidget.delete_button /
        # _on_row_delete_clicked) or Delete/Backspace on the selected row
        # (see _StackListWidget.keyPressEvent -> _on_remove_clicked).

        # Live readout so it's obvious at a glance whether the grid size/
        # domain have actually been changed from the default -- styled
        # differently (see _update_domain_readout) when they have, since
        # "quietly still 33 / [0,1]" and "quietly still an LMT's extended
        # range from three sessions ago" look identical without this. The
        # actions that used to sit next to it (Printer Lights / ACES DRT /
        # "Stack Output...") are all in the header's "..." menu now, see
        # _build_list_menu_button below -- this readout is the one bit
        # that's informational rather than an action, so it stays inline.
        domain_row = QHBoxLayout()
        self.domain_readout_label = QLabel()
        domain_row.addWidget(self.domain_readout_label)
        domain_row.addStretch(1)
        layout.addLayout(domain_row)

        # Surfaces a bake failure inline rather than letting it propagate
        # as an unhandled exception -- see set_bake_error/MainWindow's
        # _on_stack_changed. Hidden (empty text) the rest of the time.
        # Most likely trigger (per Tom's Stack Forking spec): deleting one
        # half of a fork_split/fork_merge pair via that row's own "x"
        # button -- an entirely ordinary, single-click action for any
        # other stage kind, but here leaves the stack in a structurally
        # invalid state (an unmatched split or merge, see stack.py's
        # _run_stages) until the other half is removed too. A hardcoded
        # warning-ish color rather than a palette() role is deliberate
        # here (unlike the rest of this file's styling) -- this is the
        # one label in this panel meant to read as "something's actually
        # wrong," not just informational, and none of Qt's palette roles
        # reliably map to "error" the way highlight/mid/link do.
        self.bake_error_label = QLabel()
        self.bake_error_label.setWordWrap(True)
        self.bake_error_label.setStyleSheet("color: #d9534f; font-size: 10px;")
        self.bake_error_label.hide()
        layout.addWidget(self.bake_error_label)

        self._build_list_menu_button()

    def _build_list_menu_button(self):
        """The Stack panel's "..." header menu (see corner_widget on
        PanelFrame, main_window.py) -- replaces the old always-visible "Add
        stage:" row (Printer Lights / ACES DRT buttons) and the separate
        "Stack Output (Grid Size / Domain)..." button with the three of
        them as menu actions instead, per Tom. LUT/Identity stages still
        come from the Browser panel (drag-drop or double-click), not from
        here -- same as before."""
        self.list_menu_button = QToolButton()
        self.list_menu_button.setObjectName("panelMenuButton")
        self.list_menu_button.setAutoRaise(True)
        self.list_menu_button.setText("☰")  # hamburger icon, per Tom (was "...")
        self.list_menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.list_menu_button)
        self.populate_menu(menu)
        self.list_menu_button.setMenu(menu)

    def populate_menu(self, menu):
        """Add this panel's "Add stage"/"Edit Size-Domain..." actions to
        `menu` -- shared by the "..." corner button above and MainWindow's
        top-level "Stack" menu-bar menu (per Tom, making the "..." menu
        redundant rather than the only way in), so both stay in sync
        automatically since they're wired to the exact same methods."""
        menu.addAction("Add Printer Lights", self._on_add_printer_lights_clicked)
        aces_drt_action = menu.addAction("Add ACES DRT", self._on_add_aces_drt_clicked)
        aces_drt_action.setEnabled(aces_drt.OCIO_AVAILABLE)
        if not aces_drt.OCIO_AVAILABLE:
            aces_drt_action.setToolTip(
                f"PyOpenColorIO isn't installed: {aces_drt.OCIO_IMPORT_ERROR}")
        cdl_action = menu.addAction("Add ASC CDL", self._on_add_cdl_clicked)
        cdl_action.setEnabled(cdl.OCIO_AVAILABLE)
        if not cdl.OCIO_AVAILABLE:
            cdl_action.setToolTip(
                f"PyOpenColorIO isn't installed: {cdl.OCIO_IMPORT_ERROR}")
        csc_action = menu.addAction("Add Color Space Conversion", self._on_add_colorspace_clicked)
        csc_action.setEnabled(csc.OCIO_AVAILABLE)
        if not csc.OCIO_AVAILABLE:
            csc_action.setToolTip(
                f"PyOpenColorIO isn't installed: {csc.OCIO_IMPORT_ERROR}")
        menu.addSeparator()
        fork_action = menu.addAction("Add Fork (Split + Merge)", self._on_add_fork_clicked)
        fork_action.setToolTip(
            "Adds a Fork Split and a matching Fork Merge, appended as a "
            "pair. Add any stage(s) between them and tag each one Branch "
            "A or Branch B using that row's own branch dropdown -- see "
            "stack.py's docstring, STACK FORKING.")
        menu.addSeparator()
        menu.addAction("Edit Size/Domain...", self._show_global_settings_dialog)

    # ---- params area (stage inspector) ------------------------------------

    def _build_params_ui(self):
        layout = QVBoxLayout(self.params_widget_area)
        layout.setContentsMargins(0, 0, 0, 0)  # PanelFrame provides the outer gutter

        self.inspector_stack = QStackedWidget()
        self._build_empty_inspector()
        self._build_lut_inspector()
        self._build_printer_lights_inspector()
        self._build_aces_drt_inspector()
        self._build_cdl_inspector()
        self._build_colorspace_inspector()
        self._build_fork_split_inspector()
        self._build_fork_merge_inspector()
        layout.addWidget(self.inspector_stack, stretch=1)

    # ---- global settings (grid size + domain, formerly Resize/Bounds) ----
    # Own popup dialog rather than inline in Stack Params -- see this
    # module's docstring.

    def _build_global_settings_dialog(self):
        dialog = QDialog(self.list_widget_area)
        make_floating_tool_window(dialog)
        dialog.setWindowTitle("Stack Output -- Grid Size / Domain")
        outer = QVBoxLayout(dialog)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Grid size:"))
        self.size_combo = QComboBox()
        self.size_combo.setEditable(True)
        self.size_combo.addItems([str(s) for s in COMMON_SIZES])
        self.size_combo.setCurrentText(str(self.stack.size))
        size_row.addWidget(self.size_combo)
        size_row.addStretch(1)
        outer.addLayout(size_row)

        self.domain_link_checkbox = QCheckBox("Link R/G/B")
        self.domain_link_checkbox.setChecked(True)
        self.domain_link_checkbox.toggled.connect(self._on_domain_link_toggled)
        outer.addWidget(self.domain_link_checkbox)

        self._domain_fields = {}
        for channel in ("R", "G", "B"):
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{channel} min:"))
            min_field = QLineEdit("0.0")
            row.addWidget(min_field)
            row.addWidget(QLabel(f"{channel} max:"))
            max_field = QLineEdit("1.0")
            row.addWidget(max_field)
            self._domain_fields[channel] = (min_field, max_field)
            outer.addLayout(row)
        for channel in ("G", "B"):
            min_field, max_field = self._domain_fields[channel]
            min_field.setEnabled(False)
            max_field.setEnabled(False)
        r_min, r_max = self._domain_fields["R"]
        r_min.textChanged.connect(self._sync_linked_domain_fields)
        r_max.textChanged.connect(self._sync_linked_domain_fields)

        use_first_stage_button = QPushButton("Use Input Bounds From First Stack Entry")
        use_first_stage_button.setToolTip(
            "Reads the first stack entry's own declared input domain (e.g. "
            "a non-stock IDT or LMT with an extended range) and fills the "
            "fields above with it, so concatenating on top of it doesn't "
            "silently clamp back to [0,1] metadata. Only fills the fields "
            "-- doesn't apply anything until you hit Apply below."
        )
        use_first_stage_button.clicked.connect(self._on_use_first_stage_bounds_clicked)
        outer.addWidget(use_first_stage_button)

        apply_button = QPushButton("Apply Grid Size / Domain")
        apply_button.clicked.connect(self._on_apply_global_settings_clicked)
        outer.addWidget(apply_button)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        button_box.rejected.connect(dialog.close)
        button_box.button(QDialogButtonBox.StandardButton.Close).clicked.connect(dialog.close)
        outer.addWidget(button_box)

        self._global_settings_dialog = dialog

    def _show_global_settings_dialog(self):
        # Re-sync the fields to self.stack every time this opens rather
        # than only at dialog-build time -- otherwise a session load
        # (set_stack) that brings in a different size/domain would leave
        # this dialog showing stale 33/[0,1] defaults until Apply or "Use
        # Input Bounds..." overwrote them.
        self.size_combo.setCurrentText(str(self.stack.size))
        lo, hi = self.stack.domain_min, self.stack.domain_max
        for i, channel in enumerate(("R", "G", "B")):
            min_field, max_field = self._domain_fields[channel]
            min_field.setText(f"{lo[i]:g}")
            max_field.setText(f"{hi[i]:g}")
        uniform = len(set(lo)) == 1 and len(set(hi)) == 1
        self.domain_link_checkbox.setChecked(uniform)

        # Non-modal (show(), not exec()) -- Apply already pushes changes
        # live via stack_changed, no reason to block interacting with the
        # rest of the window while this is open. Qt.Tool (see
        # floating_dialog.py) keeps it from sliding behind the main window
        # if you click back into it while this stays open -- without also
        # forcing it above every other application's windows, unlike the
        # WindowStaysOnTopHint this used to use.
        show_floating(self._global_settings_dialog)

    def _on_use_first_stage_bounds_clicked(self):
        if not self.stack.stages:
            QMessageBox.information(
                self._global_settings_dialog, "Empty stack",
                "Add a stage first -- there's nothing to read bounds from.")
            return
        first_stage = self.stack.stages[0]
        if first_stage.kind != "lut":
            QMessageBox.information(
                self._global_settings_dialog, "Not a LUT stage",
                f"The first stack entry is a {first_stage.kind.replace('_', ' ')} "
                "stage, which doesn't declare its own input bounds -- this "
                "only works when the first entry is a LUT (e.g. a non-stock "
                "IDT or LMT loaded from a file).")
            return
        lut = first_stage.params.get("lut")
        domain = getattr(lut, "domain", None)
        if domain is None:
            QMessageBox.information(
                self._global_settings_dialog, "No declared bounds",
                "This LUT doesn't declare its own input domain.")
            return

        domain = np.asarray(domain, dtype=float)
        if domain.ndim == 1:
            # A single [min, max] pair shared by every channel (LUT1D).
            lo = np.full(3, domain[0])
            hi = np.full(3, domain[1])
        else:
            lo = np.broadcast_to(domain[0], (3,)).astype(float)
            hi = np.broadcast_to(domain[1], (3,)).astype(float)

        for i, channel in enumerate(("R", "G", "B")):
            min_field, max_field = self._domain_fields[channel]
            min_field.setText(f"{lo[i]:g}")
            max_field.setText(f"{hi[i]:g}")

        uniform = bool(np.all(lo == lo[0]) and np.all(hi == hi[0]))
        self.domain_link_checkbox.setChecked(uniform)

    def _update_domain_readout(self):
        lo = self.stack.domain_min
        hi = self.stack.domain_max
        if len(set(lo)) == 1 and len(set(hi)) == 1:
            domain_text = f"[{lo[0]:g}, {hi[0]:g}]"
        else:
            domain_text = (
                f"R[{lo[0]:g},{hi[0]:g}] "
                f"G[{lo[1]:g},{hi[1]:g}] "
                f"B[{lo[2]:g},{hi[2]:g}]"
            )
        self.domain_readout_label.setText(f"{self.stack.size}³ · domain {domain_text}")

        is_default = (
            self.stack.size == _STACK_DEFAULTS.size
            and tuple(lo) == _STACK_DEFAULTS.domain_min
            and tuple(hi) == _STACK_DEFAULTS.domain_max
        )
        if is_default:
            self.domain_readout_label.setStyleSheet(f"color: {HINT_TEXT_HEX}; font-size: 10px;")
        else:
            self.domain_readout_label.setStyleSheet(
                "color: palette(link); font-size: 10px; font-weight: 600;")

    def _on_domain_link_toggled(self, linked):
        for channel in ("G", "B"):
            min_field, max_field = self._domain_fields[channel]
            min_field.setEnabled(not linked)
            max_field.setEnabled(not linked)
        if linked:
            self._sync_linked_domain_fields()

    def _sync_linked_domain_fields(self):
        if not self.domain_link_checkbox.isChecked():
            return
        r_min, r_max = self._domain_fields["R"]
        for channel in ("G", "B"):
            min_field, max_field = self._domain_fields[channel]
            min_field.setText(r_min.text())
            max_field.setText(r_max.text())

    def _on_apply_global_settings_clicked(self):
        try:
            size = int(self.size_combo.currentText())
        except ValueError:
            QMessageBox.warning(self._global_settings_dialog, "Invalid size", "Grid size must be an integer.")
            return
        if size < 2:
            QMessageBox.warning(self._global_settings_dialog, "Invalid size", "Grid size must be at least 2.")
            return
        try:
            domain_min = [float(self._domain_fields[c][0].text()) for c in ("R", "G", "B")]
            domain_max = [float(self._domain_fields[c][1].text()) for c in ("R", "G", "B")]
        except ValueError:
            QMessageBox.warning(self._global_settings_dialog, "Invalid domain", "Min/max must be numbers.")
            return
        if any(mn >= mx for mn, mx in zip(domain_min, domain_max)):
            QMessageBox.warning(self._global_settings_dialog, "Invalid domain", "Each channel's min must be less than its max.")
            return

        self.stack.size = size
        self.stack.domain_min = tuple(domain_min)
        self.stack.domain_max = tuple(domain_max)
        self._update_domain_readout()
        self._emit_changed()

    # ---- stage list -------------------------------------------------------

    def _refresh_list(self, keep_selection=None):
        """Rebuilds every row from self.stack.stages, in stack order,
        EXCEPT that a fork region's branch-tagged stages no longer get
        one QListWidgetItem each -- see _flush_fork_grid/
        _ForkBranchGridRowWidget: they're grouped into a two-column table
        between the fork_split and fork_merge rows, Branch A stages on
        the left, Branch B on the right, one stage per position, so a
        visual "row" inside that table can hold zero, one, or two actual
        Stage objects. That breaks the assumption every earlier version
        of this method relied on (QListWidget row index == index into
        self.stack.stages), so this rebuilds two lookup tables alongside
        the widgets themselves:

        - self._row_stage_ids[row] -- the stage ID(s) rendered at that
          row (usually one, empty for a non-selectable header/spacer row,
          two would only happen if a future change put both branches'
          cells under one selectable id, which isn't the case here).
        - self._row_insert_index[row] -- the self.stack.stages index a
          NEW stage should be inserted at if something is dropped at that
          row's position (see _on_stage_reordered/_on_lut_path_dropped).
          Has one extra trailing entry for "dropped past the last row."

        `keep_selection`: a stage ID to select after rebuilding (used
        when a mutation just created/moved a specific stage the caller
        wants to land on), or None to keep whatever was selected before
        this call if it still exists (the common case -- nearly every
        mutation handler below just wants "don't lose the selection").
        Selection itself now lives in self._selected_stage_id, not
        anything QListWidget tracks on its own -- see __init__'s
        docstring on why."""
        target_id = keep_selection if keep_selection is not None else self._selected_stage_id

        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        self._row_stage_ids = []
        self._row_insert_index = []
        stage_id_to_row = {}

        stages = self.stack.stages
        n = len(stages)
        i = 0
        while i < n:
            stage = stages[i]
            if stage.kind == "fork_split":
                self._add_stage_row_item(stage, i, stage_id_to_row)
                i += 1
                region_a, region_b = [], []
                while i < n and stages[i].kind != "fork_merge":
                    s = stages[i]
                    if s.branch == "a":
                        region_a.append((i, s))
                        i += 1
                    elif s.branch == "b":
                        region_b.append((i, s))
                        i += 1
                    else:
                        # An untagged stage sitting inside an open region
                        # -- normally only transient (right after being
                        # added, before its branch is picked) or a sign
                        # the structure is currently invalid (see
                        # set_bake_error). Flush whatever's been
                        # buffered so far as its own grid block, THEN
                        # render this one as an ordinary full-width row
                        # (with the wide branch combo, so it's the first
                        # thing offering an assignment), so it doesn't
                        # silently vanish into a column it was never
                        # actually placed in.
                        self._flush_fork_grid(region_a, region_b, stage_id_to_row)
                        region_a, region_b = [], []
                        self._add_stage_row_item(s, i, stage_id_to_row, in_fork_region=True)
                        i += 1
                self._flush_fork_grid(region_a, region_b, stage_id_to_row)
                if i < n and stages[i].kind == "fork_merge":
                    self._add_stage_row_item(stages[i], i, stage_id_to_row)
                    i += 1
                # else: fork_split with no fork_merge before the end of
                # the stack -- already an invalid, bake-erroring state
                # (see set_bake_error); nothing further to render for
                # this split, the loop just continues from wherever i
                # landed.
            else:
                self._add_stage_row_item(stage, i, stage_id_to_row)
                i += 1

        # Trailing sentinel -- "dropped past the very last row" means
        # "append at the end of the stack."
        self._row_insert_index.append(len(stages))

        if target_id is not None and target_id in stage_id_to_row:
            self._selected_stage_id = target_id
            self.list_widget.setCurrentRow(stage_id_to_row[target_id])
        else:
            self._selected_stage_id = None
        self.list_widget.blockSignals(False)
        self._update_row_selection_styling()
        # setCurrentRow above runs while signals are blocked (rebuilding
        # the list -- clear() then re-adding every item -- would otherwise
        # spam currentRowChanged once per row as they're reinserted), which
        # means _on_selection_changed's inspector refresh never fires from
        # that call, even when the selection now genuinely points at a
        # different -- or brand new -- stage. Explicit call here closes
        # that gap unconditionally, regardless of whether Qt considered the
        # row "changed" -- it never got the chance to.
        self._on_selection_changed(self.list_widget.currentRow())

    def _add_stage_row_item(self, stage, stack_index, stage_id_to_row, in_fork_region=False):
        """Appends one full-width QListWidgetItem for `stage` (at
        self.stack.stages[stack_index]) -- used for every row that isn't
        part of a fork region's two-column table. Records this row's
        position in both lookup tables _refresh_list builds; see its
        docstring."""
        item = QListWidgetItem()
        row_widget = _StageRowWidget(
            stage, self._on_row_clicked, self._on_row_enabled_clicked,
            self._on_row_delete_clicked, on_branch_changed=self._on_row_branch_changed,
            in_fork_region=in_fork_region, display_number=stack_index + 1)
        item.setSizeHint(row_widget.sizeHint())
        self.list_widget.addItem(item)
        self.list_widget.setItemWidget(item, row_widget)
        row = self.list_widget.count() - 1
        self._row_stage_ids.append([stage.id])
        self._row_insert_index.append(stack_index)
        stage_id_to_row[stage.id] = row

    def _flush_fork_grid(self, region_a, region_b, stage_id_to_row):
        """Appends one _ForkBranchGridRowWidget QListWidgetItem per paired
        position across region_a/region_b -- each a list of
        (stack_index, Stage) tuples already in stack order for that
        branch. Paired by POSITION within each branch's own list (via
        zip_longest), not by original stack index, so a branch with fewer
        stages simply runs out and pads with None (-> an empty cell, see
        _EmptyBranchCell) for the remaining rows. Because that padding can
        only start once the shorter list is exhausted, empty cells land
        at the END of the shorter column only, never in the middle --
        "can't checkerboard" (per Tom) falls straight out of zip_longest's
        own behavior rather than needing a separate check here.

        No-op if both branches are empty (nothing buffered yet, or an
        untagged stage immediately followed fork_split with nothing
        tagged before it)."""
        if not region_a and not region_b:
            return
        self._add_fork_grid_header()
        for a_entry, b_entry in zip_longest(region_a, region_b):
            a_index, a_stage = a_entry if a_entry else (None, None)
            b_index, b_stage = b_entry if b_entry else (None, None)
            item = QListWidgetItem()
            # Not draggable -- a cell inside this table isn't a
            # reorderable top-level row in this round (see
            # _StackListWidget's docstring); moving a stage between
            # branches or out of the fork is the branch combo's job, see
            # _StageRowWidget(compact=True).
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
            row_widget = _ForkBranchGridRowWidget(
                a_stage, b_stage, self._on_row_clicked, self._on_row_enabled_clicked,
                self._on_row_delete_clicked, self._on_row_branch_changed)
            item.setSizeHint(row_widget.sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, row_widget)
            row = self.list_widget.count() - 1
            ids_here = [s.id for s in (a_stage, b_stage) if s is not None]
            self._row_stage_ids.append(ids_here)
            # "Insert before this row" means "insert before whichever of
            # this row's real stages comes first in the actual flat
            # stack" -- the two branches are interleaved in stack order,
            # so this is well-defined even when only one side has a
            # stage at this position.
            indices_here = [idx for idx in (a_index, b_index) if idx is not None]
            self._row_insert_index.append(min(indices_here))
            if a_stage is not None:
                stage_id_to_row[a_stage.id] = row
            if b_stage is not None:
                stage_id_to_row[b_stage.id] = row

    def _add_fork_grid_header(self):
        """A lightweight "Branch A | Branch B" column header, added once
        right before the first paired row of a fork region's table --
        so which side is which doesn't rely purely on remembering
        left/right (see _flush_fork_grid). Not selectable (NoItemFlags)
        so keyboard Up/Down skips over it, same as a real spacer would."""
        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(4, 2, 4, 2)
        for text in ("Branch A", "Branch B"):
            label = QLabel(text)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet(f"color: {HINT_TEXT_HEX}; font-size: 10px; font-weight: 600;")
            layout.addWidget(label, stretch=1)
        item.setSizeHint(header.sizeHint())
        self.list_widget.addItem(item)
        self.list_widget.setItemWidget(item, header)
        self._row_stage_ids.append([])
        # A drop landing on the header behaves the same as landing right
        # at the top of the table -- reuse whichever insert index the
        # previous row (fork_split itself) already recorded.
        self._row_insert_index.append(self._row_insert_index[-1] if self._row_insert_index else 0)

    def _selected_stage(self):
        if self._selected_stage_id is None:
            return None
        try:
            return self.stack.find_stage(self._selected_stage_id)
        except KeyError:
            return None

    def _on_selection_changed(self, row):
        # Reconciles self._selected_stage_id against whatever row Qt
        # actually landed on -- matters for keyboard Up/Down navigation,
        # which moves QListWidget's currentRow directly without going
        # through _on_row_clicked, so self._selected_stage_id could
        # otherwise go stale (still pointing at the previous row's
        # stage). If the current id is still one of this row's stages
        # (the common case -- an explicit click already set it, or a
        # rebuild's keep_selection did), leave it alone; otherwise adopt
        # whichever stage this row actually has (first cell of a
        # fork-region table row, if any), or clear the selection if this
        # row has none (a header/spacer row, or nothing selected at all).
        if 0 <= row < len(self._row_stage_ids):
            ids_here = self._row_stage_ids[row]
            if self._selected_stage_id not in ids_here:
                self._selected_stage_id = ids_here[0] if ids_here else None
        else:
            self._selected_stage_id = None

        stage = self._selected_stage()
        self.opacity_spin.blockSignals(True)
        if stage is None:
            self.opacity_spin.setValue(100)
            self.opacity_spin.setEnabled(False)
        else:
            self.opacity_spin.setEnabled(True)
            self.opacity_spin.setValue(round(stage.opacity * 100))
        self.opacity_spin.blockSignals(False)
        self._update_row_selection_styling()
        self._refresh_inspector()

    def _update_row_selection_styling(self):
        """Each row widget draws its own selection highlight (a
        setItemWidget()-covered item doesn't pick up QListWidget's normal
        selected-row painting on its own, since the widget opaquely covers
        it) -- sync all of them to self._selected_stage_id on every
        selection change and every list rebuild. A plain _StageRowWidget
        takes a bool; a _ForkBranchGridRowWidget takes the id directly and
        fans it out to whichever of its two cells (if either) matches."""
        for row in range(self.list_widget.count()):
            widget = self.list_widget.itemWidget(self.list_widget.item(row))
            if isinstance(widget, _StageRowWidget):
                widget.set_selected(widget.stage_id == self._selected_stage_id)
            elif isinstance(widget, _ForkBranchGridRowWidget):
                widget.set_selected(self._selected_stage_id)

    def _on_row_clicked(self, stage_id):
        self._selected_stage_id = stage_id
        row = None
        for i, ids in enumerate(self._row_stage_ids):
            if stage_id in ids:
                row = i
                break
        if row is not None:
            self.list_widget.setCurrentRow(row)
        else:  # pragma: no cover -- defensive, shouldn't happen
            self._on_selection_changed(-1)

    def _on_row_enabled_clicked(self, stage_id):
        try:
            stage = self.stack.find_stage(stage_id)
        except KeyError:
            return
        stage.enabled = not stage.enabled
        self._refresh_list()
        self._emit_changed()

    def _on_row_branch_changed(self, stage_id, branch):
        """A stage row's own Branch A/B combo was changed (see
        _StageRowWidget) -- writes straight to Stage.branch the same way
        stack.py's own tests tag a branch onto a Stage (plain attribute
        assignment, not one of the make_xxx_stage constructors -- branch
        is an orthogonal tag any stage kind can carry, see stack.py's
        Stage docstring). `branch` is None ("No branch"/"—") or "a"/"b"
        -- exactly the combo's own item data, so no translation needed
        here."""
        try:
            stage = self.stack.find_stage(stage_id)
        except KeyError:
            return
        stage.branch = branch
        self._refresh_list()
        self._emit_changed()

    def _on_stage_reordered(self, source_row, target_row):
        """source_row/target_row are QListWidget row POSITIONS (from
        _StackListWidget._drop_row), not self.stack.stages indices -- see
        _refresh_list's docstring on why those stopped being the same
        thing once a fork region's rows could represent two stages (or
        zero) at once. self._row_stage_ids/_row_insert_index (rebuilt
        every _refresh_list call) translate between the two."""
        if source_row < 0 or source_row >= len(self._row_stage_ids):
            return
        source_ids = self._row_stage_ids[source_row]
        if len(source_ids) != 1:
            # Not a single ordinary/reorderable row (a fork-grid cell
            # pair, or a non-selectable header) -- shouldn't be
            # reachable anyway since those rows have drag disabled (see
            # _flush_fork_grid), but bail out safely rather than guess.
            return
        try:
            stage = self.stack.find_stage(source_ids[0])
        except KeyError:
            return
        source_index = self.stack.stages.index(stage)
        if 0 <= target_row < len(self._row_insert_index):
            target_index = self._row_insert_index[target_row]
        else:
            target_index = len(self.stack.stages)
        # target_index is where the stage should land if the list were
        # rebuilt fresh; pulling it out of its OWN current spot first
        # shifts everything after that spot down by one, so any target
        # past it needs the same adjustment move_stage's callers have
        # always needed.
        dest = target_index - 1 if target_index > source_index else target_index
        dest = max(0, min(dest, len(self.stack.stages) - 1))
        if dest == source_index:
            return
        self.stack.move_stage(stage.id, dest)
        self._refresh_list(keep_selection=stage.id)
        self._emit_changed()

    def _on_lut_path_dropped(self, path, target_row):
        """A LUT file was dragged in from the Browser panel and dropped at
        `target_row` (a QListWidget row position -- see
        _on_stage_reordered's docstring on why that needs translating,
        via _row_insert_index, into an actual self.stack.stages index)."""
        if 0 <= target_row < len(self._row_insert_index):
            insert_index = self._row_insert_index[target_row]
        else:
            insert_index = None
        self.add_lut_stage_from_path(path, index=insert_index)

    def _on_opacity_changed(self, value):
        stage = self._selected_stage()
        if stage is None:
            return
        stage.opacity = value / 100.0
        self._refresh_list()
        self._emit_changed()

    def _on_remove_clicked(self):
        """Removes whichever stage is currently SELECTED -- the
        Delete/Backspace path (see _StackListWidget.keyPressEvent). For
        "remove this specific row regardless of selection" (the per-row
        "x" button), see _on_row_delete_clicked instead."""
        stage = self._selected_stage()
        if stage is None:
            return
        self.stack.remove_stage(stage.id)
        self._refresh_list()
        self._refresh_inspector()
        self._emit_changed()

    def _on_row_delete_clicked(self, stage_id):
        """A stage row's own "x" was clicked -- removes THAT stage
        specifically, whether or not it's the currently selected one
        (same "act on the row you clicked" convention as
        _on_row_enabled_clicked)."""
        try:
            self.stack.remove_stage(stage_id)
        except KeyError:
            return
        self._refresh_list()
        self._refresh_inspector()
        self._emit_changed()

    # ---- add stage ----------------------------------------------------
    #
    # LUT File / Identity LUT no longer have their own buttons here -- the
    # Browser panel already has an identical "load a file" (double-click)
    # / "New Identity LUT" entry point, and MainWindow routes both of
    # those signals into the two public methods below instead of setting
    # "the current LUT" directly, so there's no need for a second,
    # duplicate way to do the same thing inside the Stack panel itself.

    def add_lut_stage_from_path(self, path, index=None):
        """Public entry point -- used by MainWindow to route Browser's
        file_selected signal here, and by _on_lut_path_dropped for a
        drag-and-drop from the Browser panel's file list (which supplies
        `index`, the drop position; the plain file_selected/double-click
        path leaves it None and appends at the end, same as always)."""
        try:
            lut = load_lut_any_format(path)
        except Exception as e:  # noqa: BLE001 -- surface any loader failure to the user
            QMessageBox.critical(self.list_widget_area, "Failed to load LUT", str(e))
            return
        stage = st.make_lut_stage(lut, label=Path(path).name)
        # Tags provenance for session_io -- a "lut" stage's params holds a
        # real LUT object, which isn't JSON-serializable and shouldn't be
        # embedded as a giant baked table anyway; what a saved session
        # needs is HOW to rebuild this exact stage, which for a file-based
        # LUT is just "reload this path." See session_io.py's docstring.
        stage.params["source_kind"] = "file"
        stage.params["source_path"] = str(path)
        self._add_stage(stage, index=index)

    def add_identity_stage(self, size, index=None):
        """Public entry point -- used by MainWindow to route Browser's
        identity_lut_requested signal here."""
        lut = create_identity_lut(size=size, name=f"Identity_{size}")
        stage = st.make_lut_stage(lut, label=f"Identity ({size}³)")
        # See add_lut_stage_from_path above -- same provenance tagging,
        # but an identity LUT is regenerated from its size, not reloaded
        # from a file.
        stage.params["source_kind"] = "identity"
        stage.params["identity_size"] = size
        self._add_stage(stage, index=index)

    def add_lut_stage_from_library_entry(self, entry, index=None):
        """Public entry point -- used by MainWindow to route
        LUTLibraryPanel's apply_requested signal here (see
        lut_library_panel.py). Unlike add_lut_stage_from_path, this never
        touches disk -- `entry.lut` is already the loaded LUT object,
        ingested into the LUT Library at import time (see
        lut_library.py's docstring on why that matters). Tagged
        source_kind="embedded" so session_io.py writes this stage's full
        table directly into the session file instead of a file path --
        see session_io.py's docstring on LUT stage provenance -- so the
        stage survives a session move independent of whether the library
        entry it came from still exists."""
        stage = st.make_lut_stage(entry.lut, label=entry.name)
        stage.params["source_kind"] = "embedded"
        self._add_stage(stage, index=index)

    def _on_add_printer_lights_clicked(self):
        stage = st.make_printer_lights_stage(
            0, 0, 0, working_colourspace=DEFAULT_WORKING_COLOURSPACE)
        self._add_stage(stage)

    def _on_add_aces_drt_clicked(self):
        if not aces_drt.OCIO_AVAILABLE:
            return
        config_name = aces_drt.DEFAULT_CONFIG_NAME
        display, view = aces_drt.get_default_display_and_view(config_name)
        stage = st.make_aces_drt_stage("ACES2065-1", display, view, config_name=config_name)
        self._add_stage(stage)

    def _on_add_cdl_clicked(self):
        if not cdl.OCIO_AVAILABLE:
            return
        stage = st.make_cdl_stage()
        stage.label = _format_cdl_label(stage.params)
        self._add_stage(stage)

    def _on_add_colorspace_clicked(self):
        if not csc.OCIO_AVAILABLE:
            return
        # src == dst (a genuine no-op) as the initial state, same
        # "identity until the user actually picks something" convention as
        # make_cdl_stage()'s default and Printer Lights' 0/0/0 default --
        # picks the config's own first scene-referred space for both.
        config_name = csc.DEFAULT_CONFIG_NAME
        spaces = csc.list_scene_referred_colourspaces(config_name)
        default_space = spaces[0] if spaces else ""
        stage = st.make_colorspace_stage(default_space, default_space, config_name=config_name)
        self._add_stage(stage)

    def _on_add_fork_clicked(self):
        """Adds a fork_split/fork_merge pair, appended at the end as
        ordinary flat stages (per Tom's Stack Forking spec -- see
        stack.py's docstring, STACK FORKING). Nothing goes between them
        yet; add any stage kind the normal way, then use that row's own
        Branch A/B combo (see _StageRowWidget/_on_row_branch_changed) to
        place it inside this region. Both are added in one go (not via
        _add_stage twice, which would each emit/refresh separately) so
        the list only rebuilds once and selection lands on the new Fork
        Split row."""
        split = st.make_fork_split_stage()
        merge = st.make_fork_merge_stage()
        merge.label = _format_fork_merge_label(merge.params)
        self.stack.add_stage(split)
        self.stack.add_stage(merge)
        self._refresh_list(keep_selection=split.id)
        self._emit_changed()

    def _add_stage(self, stage, index=None):
        """The one place every "add a stage" entry point in this class
        (Add Printer Lights/ACES DRT/CDL/Color Space, LUT file/Identity/
        Library-entry, CDL Library apply-as-new-stage) funnels through.

        `index=None` (every caller above except drag-and-drop and a
        Browser-drop) used to mean "append at the end" -- now means
        "immediately after whichever stage is currently SELECTED" (see
        _default_insert_index), so an add/tweak/select-next/add pass down
        the stack lands each new stage where you're actually working
        instead of always piling up at the bottom. Passing an explicit
        `index` (drag-and-drop's own drop position) is left alone --
        that's the caller deliberately choosing a position, not asking
        for the default.

        Either way, once the final position is known, _maybe_auto_assign_
        fork_branch checks whether it falls inside an open fork region
        and, if so, picks Branch A/B for it automatically (see that
        method) rather than always leaving it an unassigned "stray" tag
        for the user to resolve by hand via the row's own combo."""
        if index is None:
            index = self._default_insert_index()
        self._maybe_auto_assign_fork_branch(stage, index)
        self.stack.add_stage(stage, index=index)
        self._refresh_list(keep_selection=stage.id)
        self._emit_changed()

    def _default_insert_index(self):
        """Where a new stage lands when the caller doesn't already have a
        specific position in mind: immediately after the currently
        SELECTED stage, or at the end of the stack if nothing's selected
        (same behavior as before this method existed)."""
        stage = self._selected_stage()
        if stage is None:
            return None
        idx = next((i for i, s in enumerate(self.stack.stages) if s.id == stage.id), None)
        return idx + 1 if idx is not None else None

    def _maybe_auto_assign_fork_branch(self, stage, index):
        """If `index` (a gap position in self.stack.stages BEFORE `stage`
        is inserted) falls strictly inside an open fork_split/fork_merge
        region, auto-assign `stage`'s branch per Tom: both sides empty ->
        Branch A; one side already has stages and the other's empty ->
        the EMPTY side. Both sides already populated is left alone
        (stage.branch stays None/"stray" until the user picks a side via
        that row's own combo, same as today) -- there's no reasonable
        default to prefer when both sides are equally valid. A plain
        stage being added is never itself a fork_split/fork_merge, so
        there's no need to guard against that here."""
        if index is None:
            return
        region = st.find_enclosing_fork_region(self.stack.stages, index)
        if region is None:
            return
        split_index, merge_index = region
        counts = st.fork_region_branch_counts(self.stack.stages, split_index, merge_index)
        if counts["a"] == 0:
            stage.branch = "a"
        elif counts["b"] == 0:
            stage.branch = "b"

    # ---- inspector: empty ----------------------------------------------

    def _build_empty_inspector(self):
        widget = QLabel("Select a stage above to see/edit its settings.")
        widget.setWordWrap(True)
        widget.setStyleSheet(f"color: {HINT_TEXT_HEX};")
        self.inspector_stack.addWidget(widget)
        self._empty_inspector_index = 0

    # ---- inspector: lut stage (with optional Retarget) -------------------

    def _build_lut_inspector(self):
        # TODO(Tom, later -- not this batch): Retarget currently only
        # lets you pick the color space to REINTERPRET the LUT's
        # already-declared input/output as (From/To below, i.e. what the
        # LUT gets wrapped to accept/return). Tom wants to eventually
        # expand this to four fields -- current in, current out, target
        # in, target out -- so a LUT's ACTUAL declared in/out can be
        # overridden too, not just what it gets retargeted to. Needs a
        # real design pass (retarget_io's signature, what "current"
        # defaults to when the LUT doesn't declare one, session_io schema
        # bump) before touching this -- flagged here so it isn't lost.
        group = QGroupBox("LUT Stage")
        outer = QVBoxLayout(group)

        self.lut_inspector_name_label = QLabel()
        self.lut_inspector_name_label.setWordWrap(True)
        # Selectable so the path can be copied straight out of the
        # inspector rather than round-tripping through the Browser to
        # find the file again.
        self.lut_inspector_name_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.lut_inspector_name_label)

        self.lut_retarget_checkbox = QCheckBox(
            "Retarget (reinterpret this LUT's declared input/output color space)")
        self.lut_retarget_checkbox.toggled.connect(self._on_lut_retarget_toggled)
        outer.addWidget(self.lut_retarget_checkbox)

        spaces = list_available_colourspaces()
        retarget_row = QHBoxLayout()
        retarget_row.addWidget(QLabel("From:"))
        self.lut_retarget_from_combo = QComboBox()
        self.lut_retarget_from_combo.addItems(spaces)
        retarget_row.addWidget(self.lut_retarget_from_combo, stretch=1)
        retarget_row.addWidget(QLabel("To:"))
        self.lut_retarget_to_combo = QComboBox()
        self.lut_retarget_to_combo.addItems(spaces)
        retarget_row.addWidget(self.lut_retarget_to_combo, stretch=1)
        outer.addLayout(retarget_row)
        self.lut_retarget_from_combo.setEnabled(False)
        self.lut_retarget_to_combo.setEnabled(False)

        update_button = QPushButton("Update Stage")
        update_button.clicked.connect(self._on_lut_stage_update_clicked)
        outer.addWidget(update_button)

        self.inspector_stack.addWidget(group)
        self._lut_inspector_index = 1

    def _on_lut_retarget_toggled(self, checked):
        self.lut_retarget_from_combo.setEnabled(checked)
        self.lut_retarget_to_combo.setEnabled(checked)

    def _on_lut_stage_update_clicked(self):
        stage = self._selected_stage()
        if stage is None or stage.kind != "lut":
            return
        if self.lut_retarget_checkbox.isChecked():
            stage.params["retarget_from"] = self.lut_retarget_from_combo.currentText()
            stage.params["retarget_to"] = self.lut_retarget_to_combo.currentText()
        else:
            stage.params["retarget_from"] = None
            stage.params["retarget_to"] = None
        self._refresh_list()
        self._emit_changed()

    def _lut_details_text(self, stage, lut):
        """Multi-line summary for the LUT inspector -- path (or how it was
        generated, for an Identity stage) plus a few basic facts about the
        LUT object itself, so selecting a lut stage isn't just a blank
        panel with a Retarget checkbox in it."""
        name = getattr(lut, "name", None) or "unnamed"
        lines = [f"LUT: {name}"]

        source_kind = stage.params.get("source_kind")
        if source_kind == "file":
            lines.append(f"Path: {stage.params.get('source_path', '(unknown)')}")
        elif source_kind == "identity":
            size = stage.params.get("identity_size", "?")
            lines.append(f"Generated: Identity LUT, {size}³ (not loaded from a file)")
        elif source_kind == "embedded":
            lines.append("From: LUT Library (table embedded in this session, not file-based)")
        else:
            lines.append("Path: (not file-based)")

        kind_name = type(lut).__name__ if lut is not None else "?"
        detail_bits = [kind_name]
        size = getattr(lut, "size", None)
        if size is not None:
            detail_bits.append(f"{size}³" if kind_name == "LUT3D" else f"size {size}")
        else:
            table = getattr(lut, "table", None)
            if table is not None:
                detail_bits.append(f"shape {tuple(table.shape)}")
        domain = getattr(lut, "domain", None)
        if domain is not None:
            domain_arr = np.asarray(domain, dtype=float)
            lo, hi = domain_arr[0], domain_arr[1]
            if np.ndim(lo) == 0:
                detail_bits.append(f"domain [{lo:g}, {hi:g}]")
            elif len(set(lo)) == 1 and len(set(hi)) == 1:
                detail_bits.append(f"domain [{lo[0]:g}, {hi[0]:g}]")
            else:
                detail_bits.append(
                    f"domain R[{lo[0]:g},{hi[0]:g}] "
                    f"G[{lo[1]:g},{hi[1]:g}] B[{lo[2]:g},{hi[2]:g}]")
        lines.append(", ".join(detail_bits))

        return "\n".join(lines)

    # ---- inspector: printer lights stage ----------------------------------

    def _build_printer_lights_inspector(self):
        group = QGroupBox(f"Printer Lights Stage (points, ~{POINTS_PER_STOP:.0f}/stop)")
        outer = QVBoxLayout(group)

        space_row = QHBoxLayout()
        space_row.addWidget(QLabel("Working Color Space:"))
        self.pl_space_combo = QComboBox()
        self._pl_using_ocio = aces_drt.OCIO_AVAILABLE
        if self._pl_using_ocio:
            self.pl_space_combo.addItems(aces_drt.list_working_colourspaces())
        else:
            self.pl_space_combo.addItems(list_available_colourspaces())
        # Real-time (per Tom) -- no separate Update Stage button, changing
        # the working space applies immediately, same as the sliders below.
        self.pl_space_combo.currentTextChanged.connect(self._on_pl_space_changed)
        space_row.addWidget(self.pl_space_combo, stretch=1)
        outer.addLayout(space_row)

        gang_row = QHBoxLayout()
        self.pl_gang_checkbox = QCheckBox("Gang R/G/B")
        self.pl_gang_checkbox.toggled.connect(self._on_pl_gang_toggled)
        gang_row.addWidget(self.pl_gang_checkbox)
        gang_row.addStretch(1)
        outer.addLayout(gang_row)

        self._pl_sliders = {}
        self._pl_value_labels = {}
        for channel in ("R", "G", "B"):
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{channel}:"))
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(-PRINTER_LIGHT_RANGE, PRINTER_LIGHT_RANGE)
            slider.setValue(0)
            slider.setTickPosition(QSlider.TickPosition.TicksBelow)
            slider.setTickInterval(PRINTER_LIGHT_RANGE // 2)
            row.addWidget(slider, stretch=1)
            value_label = QLabel("0")
            value_label.setMinimumWidth(28)
            row.addWidget(value_label)
            slider.valueChanged.connect(lambda v, lbl=value_label: lbl.setText(str(v)))
            slider.valueChanged.connect(lambda v, ch=channel: self._on_pl_slider_changed(ch, v))
            self._pl_sliders[channel] = slider
            self._pl_value_labels[channel] = value_label
            outer.addLayout(row)

        # No Update Stage button here (unlike LUT/ACES DRT) -- per Tom,
        # Printer Lights applies in real time: every slider drag and
        # working-space change writes straight into the selected stage's
        # params and re-bakes immediately, see _apply_printer_lights_live.
        buttons_row = QHBoxLayout()
        reset_button = QPushButton("Reset to 0")
        reset_button.clicked.connect(self._on_pl_reset_clicked)
        buttons_row.addWidget(reset_button)
        buttons_row.addStretch(1)
        outer.addLayout(buttons_row)

        self.inspector_stack.addWidget(group)
        self._printer_lights_inspector_index = 2

    def _on_pl_gang_toggled(self, ganged):
        if not ganged:
            return
        r_value = self._pl_sliders["R"].value()
        for channel in ("G", "B"):
            slider = self._pl_sliders[channel]
            if slider.value() != r_value:
                slider.setValue(r_value)
        self._apply_printer_lights_live()

    def _on_pl_slider_changed(self, channel, value):
        if self.pl_gang_checkbox.isChecked():
            for other_channel, other_slider in self._pl_sliders.items():
                if other_channel == channel or other_slider.value() == value:
                    continue
                other_slider.blockSignals(True)
                other_slider.setValue(value)
                other_slider.blockSignals(False)
                self._pl_value_labels[other_channel].setText(str(value))
        self._apply_printer_lights_live()

    def _on_pl_space_changed(self, _text):
        self._apply_printer_lights_live()

    def _on_pl_reset_clicked(self):
        # Each setValue below fires _on_pl_slider_changed (unless already
        # 0), which applies live the same as dragging would -- no separate
        # apply call needed here.
        for slider in self._pl_sliders.values():
            slider.setValue(0)

    def _apply_printer_lights_live(self):
        stage = self._selected_stage()
        if stage is None or stage.kind != "printer_lights":
            return
        stage.params["r_points"] = self._pl_sliders["R"].value()
        stage.params["g_points"] = self._pl_sliders["G"].value()
        stage.params["b_points"] = self._pl_sliders["B"].value()
        stage.params["working_colourspace"] = self.pl_space_combo.currentText()
        self._emit_changed()

    # ---- inspector: ACES DRT stage -----------------------------------

    def _build_aces_drt_inspector(self):
        group = QGroupBox("ACES DRT Stage")
        outer = QVBoxLayout(group)

        if not aces_drt.OCIO_AVAILABLE:
            label = QLabel(
                "ACES DRT stages are unavailable -- PyOpenColorIO failed to "
                f"import:\n{aces_drt.OCIO_IMPORT_ERROR}\n\n"
                "Install with: pip3 install opencolorio, then restart."
            )
            label.setWordWrap(True)
            outer.addWidget(label)
            self.inspector_stack.addWidget(group)
            self._aces_drt_inspector_index = 3
            return

        config_row = QHBoxLayout()
        config_row.addWidget(QLabel("ACES Config:"))
        self.drt_config_combo = QComboBox()
        for name, label in aces_drt.list_available_configs():
            self.drt_config_combo.addItem(label, name)
        self.drt_config_combo.currentIndexChanged.connect(self._on_drt_config_changed)
        config_row.addWidget(self.drt_config_combo, stretch=1)
        outer.addLayout(config_row)

        working_row = QHBoxLayout()
        working_row.addWidget(QLabel("Working Color Space:"))
        self.drt_working_space_combo = QComboBox()
        self.drt_working_space_combo.currentIndexChanged.connect(self._on_drt_working_space_changed)
        working_row.addWidget(self.drt_working_space_combo, stretch=1)
        outer.addLayout(working_row)

        display_row = QHBoxLayout()
        display_row.addWidget(QLabel("Display:"))
        self.drt_display_combo = QComboBox()
        self.drt_display_combo.currentIndexChanged.connect(self._on_drt_display_changed)
        display_row.addWidget(self.drt_display_combo, stretch=1)
        outer.addLayout(display_row)

        view_row = QHBoxLayout()
        view_row.addWidget(QLabel("View (Output Transform):"))
        self.drt_view_combo = QComboBox()
        self.drt_view_combo.currentIndexChanged.connect(self._on_drt_view_changed)
        view_row.addWidget(self.drt_view_combo, stretch=1)
        outer.addLayout(view_row)

        # No Update Stage button (per Tom) -- every combo change applies
        # immediately and re-bakes, same real-time convention as Printer
        # Lights/ASC CDL (see _apply_aces_drt_live).
        self._populate_drt_configs()

        self.inspector_stack.addWidget(group)
        self._aces_drt_inspector_index = 3

    def _populate_drt_configs(self):
        self.drt_config_combo.blockSignals(True)
        self.drt_config_combo.setCurrentIndex(0)
        self.drt_config_combo.blockSignals(False)
        self._on_drt_config_changed(0)

    def _on_drt_config_changed(self, _index):
        config_name = self.drt_config_combo.currentData()
        if config_name is None:
            return
        self.drt_working_space_combo.blockSignals(True)
        self.drt_working_space_combo.clear()
        self.drt_working_space_combo.addItems(aces_drt.list_working_colourspaces(config_name))
        self.drt_working_space_combo.blockSignals(False)

        default_display, _default_view = aces_drt.get_default_display_and_view(config_name)
        self.drt_display_combo.blockSignals(True)
        self.drt_display_combo.clear()
        self.drt_display_combo.addItems(aces_drt.list_displays(config_name))
        idx = self.drt_display_combo.findText(default_display)
        if idx >= 0:
            self.drt_display_combo.setCurrentIndex(idx)
        self.drt_display_combo.blockSignals(False)
        self._populate_drt_views()
        self._apply_aces_drt_live()

    def _on_drt_working_space_changed(self, _index):
        self._apply_aces_drt_live()

    def _on_drt_display_changed(self, _index):
        self._populate_drt_views()
        self._apply_aces_drt_live()

    def _on_drt_view_changed(self, _index):
        self._apply_aces_drt_live()

    def _populate_drt_views(self):
        config_name = self.drt_config_combo.currentData()
        display = self.drt_display_combo.currentText()
        if not config_name or not display:
            return
        default_display, default_view = aces_drt.get_default_display_and_view(config_name)
        self.drt_view_combo.blockSignals(True)
        self.drt_view_combo.clear()
        self.drt_view_combo.addItems(aces_drt.list_views(config_name, display))
        if display == default_display:
            idx = self.drt_view_combo.findText(default_view)
            if idx >= 0:
                self.drt_view_combo.setCurrentIndex(idx)
        self.drt_view_combo.blockSignals(False)

    def _apply_aces_drt_live(self):
        """Reads all four combos and writes into the selected stage's
        params, same as the old "Update Stage" button did -- now called
        after every combo change instead of needing a click (see
        _on_drt_config_changed/_on_drt_working_space_changed/
        _on_drt_display_changed/_on_drt_view_changed). Guarded by
        _refreshing_inspector (see __init__'s docstring on that flag) so
        switching TO an aces_drt stage -- which itself drives these same
        combos via findText/setCurrentIndex in _refresh_inspector -- never
        counts as a live edit. Silently no-ops (no warning dialog) if the
        combos are mid-cascade and don't yet have a complete
        config/working-space/display/view selection -- the next
        repopulation step supplies it a moment later, there's nothing
        actionable for the user to fix here."""
        if self._refreshing_inspector:
            return
        stage = self._selected_stage()
        if stage is None or stage.kind != "aces_drt":
            return
        config_name = self.drt_config_combo.currentData()
        working_space = self.drt_working_space_combo.currentText()
        display = self.drt_display_combo.currentText()
        view = self.drt_view_combo.currentText()
        if not all([config_name, working_space, display, view]):
            return
        stage.params["config_name"] = config_name
        stage.params["working_colourspace"] = working_space
        stage.params["display"] = display
        stage.params["view"] = view
        stage.label = f"ACES DRT ({working_space} -> {display}/{view})"
        self._refresh_list()
        self._emit_changed()

    # ---- inspector: ASC CDL stage -------------------------------------

    def _build_cdl_inspector(self):
        group = QGroupBox("ASC CDL Stage")
        outer = QVBoxLayout(group)

        if not cdl.OCIO_AVAILABLE:
            label = QLabel(
                "ASC CDL stages are unavailable -- PyOpenColorIO failed to "
                f"import:\n{cdl.OCIO_IMPORT_ERROR}\n\n"
                "Install with: pip3 install opencolorio, then restart."
            )
            label.setWordWrap(True)
            outer.addWidget(label)
            self.inspector_stack.addWidget(group)
            self._cdl_inspector_index = self.inspector_stack.count() - 1
            return

        # SOP (Slope/Offset/Power) -------------------------------------
        sop_header = QHBoxLayout()
        self.cdl_sop_enabled_checkbox = QCheckBox("Enable SOP (Slope / Offset / Power)")
        self.cdl_sop_enabled_checkbox.setChecked(True)
        self.cdl_sop_enabled_checkbox.toggled.connect(self._on_cdl_sop_enabled_toggled)
        sop_header.addWidget(self.cdl_sop_enabled_checkbox)
        sop_header.addStretch(1)
        outer.addLayout(sop_header)

        # Each SOP field gets a color-balance wheel (drag to tint R/G/B
        # apart from each other) + a master spinbox (moves all three
        # together) + the three fine R/G/B spinboxes for exact numeric
        # entry -- wheel/master and the fine spinboxes stay in sync in
        # both directions (see _on_cdl_wheel_changed/_on_cdl_master_changed/
        # _on_cdl_channel_spin_changed and color_balance.py's module
        # docstring for the math). The wheel is the fast/intuitive control;
        # the fine spinboxes are what makes an exact CDL value (e.g. read
        # off a shot's own .cc file) reproducible to the number.
        self._cdl_wheels = {}         # {"slope": ColorBalanceWheel, ...}
        self._cdl_master_spins = {}   # {"slope": QDoubleSpinBox, ...}
        self._cdl_sop_spins = {}      # {"slope": {"R": spin, ...}, ...}
        self._cdl_wheel_strength = {}

        for field_key, field_label, lo, hi, default, step, strength in CDL_SOP_FIELD_SPECS:
            self._cdl_wheel_strength[field_key] = strength

            field_row = QHBoxLayout()
            wheel = ColorBalanceWheel(diameter=CDL_WHEEL_DIAMETER)
            wheel.balance_changed.connect(
                lambda x, y, fk=field_key: self._on_cdl_wheel_changed(fk, x, y))
            field_row.addWidget(wheel)
            self._cdl_wheels[field_key] = wheel

            master_col = QVBoxLayout()
            master_col.addWidget(QLabel(f"{field_label} (Master):"))
            master_spin = QDoubleSpinBox()
            master_spin.setRange(lo, hi)
            master_spin.setDecimals(4)
            master_spin.setSingleStep(step)
            master_spin.setValue(default)
            master_spin.valueChanged.connect(
                lambda _v, fk=field_key: self._on_cdl_master_changed(fk))
            master_col.addWidget(master_spin)
            self._cdl_master_spins[field_key] = master_spin

            fine_row = QHBoxLayout()
            channel_spins = {}
            for channel in ("R", "G", "B"):
                spin = QDoubleSpinBox()
                spin.setRange(lo, hi)
                spin.setDecimals(4)
                spin.setSingleStep(step)
                spin.setValue(default)
                spin.valueChanged.connect(
                    lambda _v, fk=field_key: self._on_cdl_channel_spin_changed(fk))
                fine_row.addWidget(QLabel(channel))
                fine_row.addWidget(spin)
                channel_spins[channel] = spin
            self._cdl_sop_spins[field_key] = channel_spins
            master_col.addLayout(fine_row)

            field_row.addLayout(master_col, stretch=1)
            outer.addLayout(field_row)

        reset_sop_button = QPushButton("Reset SOP to Identity")
        reset_sop_button.clicked.connect(self._on_cdl_reset_sop_clicked)
        outer.addWidget(reset_sop_button)

        # SAT (Saturation) ------------------------------------------------
        # Scalar, not a balance -- a slider (matching Printer Lights'
        # points sliders) rather than a wheel, since there's no "direction"
        # to a saturation change, just an amount.
        sat_header = QHBoxLayout()
        self.cdl_sat_enabled_checkbox = QCheckBox("Enable SAT (Saturation)")
        self.cdl_sat_enabled_checkbox.setChecked(True)
        self.cdl_sat_enabled_checkbox.toggled.connect(self._on_cdl_sat_enabled_toggled)
        sat_header.addWidget(self.cdl_sat_enabled_checkbox)
        sat_header.addStretch(1)
        outer.addLayout(sat_header)

        sat_row = QHBoxLayout()
        sat_row.addWidget(QLabel("Sat:"))
        self.cdl_sat_slider = QSlider(Qt.Orientation.Horizontal)
        self.cdl_sat_slider.setRange(0, CDL_SAT_RANGE)
        self.cdl_sat_slider.setValue(100)
        self.cdl_sat_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.cdl_sat_slider.setTickInterval(CDL_SAT_RANGE // 4)
        self.cdl_sat_slider.valueChanged.connect(self._on_cdl_sat_slider_changed)
        sat_row.addWidget(self.cdl_sat_slider, stretch=1)
        self.cdl_sat_spin = QDoubleSpinBox()
        self.cdl_sat_spin.setRange(0.0, CDL_SAT_RANGE / 100.0)
        self.cdl_sat_spin.setDecimals(4)
        self.cdl_sat_spin.setSingleStep(0.01)
        self.cdl_sat_spin.setValue(1.0)
        self.cdl_sat_spin.setMaximumWidth(80)
        self.cdl_sat_spin.valueChanged.connect(self._on_cdl_sat_spin_changed)
        sat_row.addWidget(self.cdl_sat_spin)
        outer.addLayout(sat_row)

        reset_sat_button = QPushButton("Reset SAT to 1.0")
        reset_sat_button.clicked.connect(self._on_cdl_reset_sat_clicked)
        outer.addWidget(reset_sat_button)

        # Style -------------------------------------------------------
        style_row = QHBoxLayout()
        style_row.addWidget(QLabel("Style:"))
        self.cdl_style_combo = QComboBox()
        for style_key, style_label in cdl.list_available_styles():
            self.cdl_style_combo.addItem(style_label, style_key)
        self.cdl_style_combo.currentIndexChanged.connect(self._on_cdl_field_changed)
        style_row.addWidget(self.cdl_style_combo, stretch=1)
        outer.addLayout(style_row)

        # Save to CDL Library -------------------------------------------
        save_to_library_button = QPushButton("Save to CDL Library...")
        save_to_library_button.setToolTip(
            "Save this stage's current SOP/SAT values as a named, reusable "
            "entry in the CDL Library panel.")
        save_to_library_button.clicked.connect(self._on_cdl_save_to_library_clicked)
        outer.addWidget(save_to_library_button)

        self.inspector_stack.addWidget(group)
        self._cdl_inspector_index = self.inspector_stack.count() - 1

    def _on_cdl_sop_enabled_toggled(self, enabled):
        for channel_spins in self._cdl_sop_spins.values():
            for spin in channel_spins.values():
                spin.setEnabled(enabled)
        for master_spin in self._cdl_master_spins.values():
            master_spin.setEnabled(enabled)
        for wheel in self._cdl_wheels.values():
            wheel.setEnabled(enabled)
        self._apply_cdl_live()

    def _on_cdl_sat_enabled_toggled(self, enabled):
        self.cdl_sat_spin.setEnabled(enabled)
        self.cdl_sat_slider.setEnabled(enabled)
        self._apply_cdl_live()

    # ---- CDL wheel/master/fine-spinbox sync (see color_balance.py) -----

    def _on_cdl_wheel_changed(self, field_key, x, y):
        """Puck dragged -- master stays put, R/G/B fan out around it."""
        master = self._cdl_master_spins[field_key].value()
        strength = self._cdl_wheel_strength[field_key]
        r, g, b = cb.rgb_from_master_and_wheel(master, x, y, strength=strength)
        self._set_cdl_fine_spins(field_key, r, g, b)
        self._apply_cdl_live()

    def _on_cdl_master_changed(self, field_key):
        """Master spinbox edited -- wheel position (the tint) is
        unchanged, R/G/B all shift together."""
        wheel = self._cdl_wheels[field_key]
        x, y = wheel.balance()
        master = self._cdl_master_spins[field_key].value()
        strength = self._cdl_wheel_strength[field_key]
        r, g, b = cb.rgb_from_master_and_wheel(master, x, y, strength=strength)
        self._set_cdl_fine_spins(field_key, r, g, b)
        self._apply_cdl_live()

    def _on_cdl_channel_spin_changed(self, field_key):
        """A fine R/G/B spinbox was typed into directly -- the exact typed
        values are what gets written to stage.params (see _apply_cdl_live,
        which reads these spinboxes), this just back-computes a
        best-fit master/wheel position so the wheel visually reflects
        wherever the fine values now point (see
        color_balance.rgb_to_master_and_wheel)."""
        spins = self._cdl_sop_spins[field_key]
        r, g, b = spins["R"].value(), spins["G"].value(), spins["B"].value()
        strength = self._cdl_wheel_strength[field_key]
        master, x, y = cb.rgb_to_master_and_wheel(r, g, b, strength=strength)

        master_spin = self._cdl_master_spins[field_key]
        master_spin.blockSignals(True)
        master_spin.setValue(master)
        master_spin.blockSignals(False)
        self._cdl_wheels[field_key].set_balance(x, y, emit=False)
        self._apply_cdl_live()

    def _set_cdl_fine_spins(self, field_key, r, g, b):
        spins = self._cdl_sop_spins[field_key]
        for channel, value in zip(("R", "G", "B"), (r, g, b)):
            spin = spins[channel]
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    def _on_cdl_sat_slider_changed(self, value):
        self.cdl_sat_spin.blockSignals(True)
        self.cdl_sat_spin.setValue(value / 100.0)
        self.cdl_sat_spin.blockSignals(False)
        self._apply_cdl_live()

    def _on_cdl_sat_spin_changed(self, value):
        self.cdl_sat_slider.blockSignals(True)
        self.cdl_sat_slider.setValue(round(value * 100))
        self.cdl_sat_slider.blockSignals(False)
        self._apply_cdl_live()

    def _on_cdl_field_changed(self, _value=None):
        self._apply_cdl_live()

    def _on_cdl_reset_sop_clicked(self):
        # Each setValue below fires _on_cdl_field_changed (unless already
        # at that value), which applies live the same as typing would --
        # no separate apply call needed here, same convention as
        # _on_pl_reset_clicked.
        for field_key, default in (("slope", 1.0), ("offset", 0.0), ("power", 1.0)):
            for spin in self._cdl_sop_spins[field_key].values():
                spin.setValue(default)

    def _on_cdl_reset_sat_clicked(self):
        self.cdl_sat_spin.setValue(1.0)

    def _apply_cdl_live(self):
        stage = self._selected_stage()
        if stage is None or stage.kind != "cdl":
            return
        stage.params["slope"] = tuple(
            self._cdl_sop_spins["slope"][c].value() for c in ("R", "G", "B"))
        stage.params["offset"] = tuple(
            self._cdl_sop_spins["offset"][c].value() for c in ("R", "G", "B"))
        stage.params["power"] = tuple(
            self._cdl_sop_spins["power"][c].value() for c in ("R", "G", "B"))
        stage.params["sat"] = self.cdl_sat_spin.value()
        stage.params["style"] = self.cdl_style_combo.currentData() or cdl.DEFAULT_STYLE
        stage.params["sop_enabled"] = self.cdl_sop_enabled_checkbox.isChecked()
        stage.params["sat_enabled"] = self.cdl_sat_enabled_checkbox.isChecked()
        # Row label shows the live SOP/SAT values (per Tom, matching the
        # 'colorspace' stage's dynamic label) -- needs its own
        # _refresh_list call to actually repaint the row, same as
        # _apply_aces_drt_live/_apply_colorspace_live.
        stage.label = _format_cdl_label(stage.params)
        self._refresh_list()
        self._emit_changed()

    def _on_cdl_save_to_library_clicked(self):
        stage = self._selected_stage()
        if stage is None or stage.kind != "cdl":
            return
        name, ok = QInputDialog.getText(self.params_widget_area, "Save to CDL Library", "Name:")
        name = name.strip()
        if not ok or not name:
            return
        # dict(...) -- a shallow copy is enough: every value inside is an
        # immutable float/str/bool/tuple, so the library's own copy can
        # never be retroactively changed by further edits to this stage.
        self.cdl_save_requested.emit(name, dict(stage.params))

    # ---- inspector: color space conversion stage -----------------------

    def _build_colorspace_inspector(self):
        group = QGroupBox("Color Space Conversion Stage")
        outer = QVBoxLayout(group)

        if not csc.OCIO_AVAILABLE:
            label = QLabel(
                "Color space conversion stages are unavailable -- "
                f"PyOpenColorIO failed to import:\n{csc.OCIO_IMPORT_ERROR}\n\n"
                "Install with: pip3 install opencolorio, then restart."
            )
            label.setWordWrap(True)
            outer.addWidget(label)
            self.inspector_stack.addWidget(group)
            self._colorspace_inspector_index = self.inspector_stack.count() - 1
            return

        config_row = QHBoxLayout()
        config_row.addWidget(QLabel("Config:"))
        self.csc_config_combo = QComboBox()
        for name, label in csc.list_available_configs():
            self.csc_config_combo.addItem(label, name)
        self.csc_config_combo.currentIndexChanged.connect(self._on_csc_config_changed)
        config_row.addWidget(self.csc_config_combo, stretch=1)
        outer.addLayout(config_row)

        category_row = QHBoxLayout()
        category_row.addWidget(QLabel("Category:"))
        self.csc_category_combo = QComboBox()
        self.csc_category_combo.addItem("Scene-Referred (camera/working spaces)", "scene")
        self.csc_category_combo.addItem("Display-Referred (display standards)", "display")
        self.csc_category_combo.currentIndexChanged.connect(self._on_csc_category_changed)
        category_row.addWidget(self.csc_category_combo, stretch=1)
        outer.addLayout(category_row)

        from_row = QHBoxLayout()
        from_row.addWidget(QLabel("From:"))
        self.csc_from_combo = QComboBox()
        self.csc_from_combo.currentIndexChanged.connect(self._on_csc_from_or_to_changed)
        from_row.addWidget(self.csc_from_combo, stretch=1)
        outer.addLayout(from_row)

        to_row = QHBoxLayout()
        to_row.addWidget(QLabel("To:"))
        self.csc_to_combo = QComboBox()
        self.csc_to_combo.currentIndexChanged.connect(self._on_csc_from_or_to_changed)
        to_row.addWidget(self.csc_to_combo, stretch=1)
        outer.addLayout(to_row)

        # No Update Stage button (per Tom) -- every combo change applies
        # immediately and re-bakes, same real-time convention as Printer
        # Lights/ASC CDL/ACES DRT (see _apply_colorspace_live).
        self._populate_csc_configs()

        self.inspector_stack.addWidget(group)
        self._colorspace_inspector_index = self.inspector_stack.count() - 1

    def _populate_csc_configs(self):
        self.csc_config_combo.blockSignals(True)
        self.csc_config_combo.setCurrentIndex(0)
        self.csc_config_combo.blockSignals(False)
        self._on_csc_config_changed(0)

    def _on_csc_config_changed(self, _index):
        self._populate_csc_spaces()
        self._apply_colorspace_live()

    def _on_csc_category_changed(self, _index):
        self._populate_csc_spaces()
        self._apply_colorspace_live()

    def _on_csc_from_or_to_changed(self, _index):
        self._apply_colorspace_live()

    def _populate_csc_spaces(self):
        config_name = self.csc_config_combo.currentData()
        category = self.csc_category_combo.currentData()
        if not config_name or not category:
            return
        if category == "scene":
            spaces = csc.list_scene_referred_colourspaces(config_name)
        else:
            spaces = csc.list_display_referred_colourspaces(config_name)

        for combo in (self.csc_from_combo, self.csc_to_combo):
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(spaces)
            idx = combo.findText(current)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    def _apply_colorspace_live(self):
        """Reads all four combos and writes into the selected stage's
        params -- called after every combo change instead of needing an
        Update Stage click (see _on_csc_config_changed/
        _on_csc_category_changed/_on_csc_from_or_to_changed). Guarded by
        _refreshing_inspector for the same reason _apply_aces_drt_live is
        -- switching TO a colorspace stage also drives these combos via
        _refresh_inspector, and that shouldn't count as a live edit.
        Silently no-ops if the combos don't yet have a complete
        config/From/To selection (e.g. mid-cascade right after a Category
        switch, before _populate_csc_spaces has finished)."""
        if self._refreshing_inspector:
            return
        stage = self._selected_stage()
        if stage is None or stage.kind != "colorspace":
            return
        config_name = self.csc_config_combo.currentData()
        src = self.csc_from_combo.currentText()
        dst = self.csc_to_combo.currentText()
        if not all([config_name, src, dst]):
            return
        stage.params["config_name"] = config_name
        stage.params["src"] = src
        stage.params["dst"] = dst
        stage.label = f"Color Space ({src} -> {dst})"
        self._refresh_list()
        self._emit_changed()

    # ---- inspector: fork_split stage ------------------------------------

    def _build_fork_split_inspector(self):
        """Nothing to configure on a fork_split stage itself -- it's a
        pure marker (see stack.py's docstring, STACK FORKING). This
        inspector is informational only, same "select a stage to see its
        settings" spirit as _build_empty_inspector, just explaining what
        the marker does and pointing at the row-level controls (branch
        combo, enabled toggle) that actually matter for it."""
        group = QGroupBox("Fork Split Stage")
        outer = QVBoxLayout(group)
        info = QLabel(
            "Opens two parallel branches from here. Add any stage kind "
            "after this one and use that row's own Branch A / Branch B "
            "dropdown (in the stage list) to place it inside this fork -- "
            "as many stages per branch as you like. Close the fork with a "
            "matching Fork Merge stage below.\n\n"
            "Disabling this stage (the row's ✓/✗ toggle) treats "
            "the WHOLE fork region -- both branches and the merge -- as a "
            "no-op, same as if none of it were here."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {HINT_TEXT_HEX};")
        outer.addWidget(info)
        self.inspector_stack.addWidget(group)
        self._fork_split_inspector_index = self.inspector_stack.count() - 1

    # ---- inspector: fork_merge stage ------------------------------------

    def _build_fork_merge_inspector(self):
        """The A/B crossfade lives here (blend, real-time like Printer
        Lights/CDL/ACES DRT/Color Space -- no Update Stage button). The
        fork's TOTAL opacity (per Tom: "a total opacity control like every
        other operator row") is deliberately NOT duplicated here -- it's
        already the shared Opacity spin below the stage list, same control
        every other stage kind uses, blending the merged result against
        the signal as it stood right before the matching Fork Split (see
        stack.py's _run_stages)."""
        group = QGroupBox("Fork Merge Stage")
        outer = QVBoxLayout(group)

        info = QLabel(
            "Blends this fork's two branches back into one signal. This "
            "stage's own Opacity (below the stage list) is the fork's "
            "total opacity control, same as every other operator row."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {HINT_TEXT_HEX};")
        outer.addWidget(info)

        blend_row = QHBoxLayout()
        blend_row.addWidget(QLabel("Branch A"))
        self.fork_merge_blend_slider = QSlider(Qt.Orientation.Horizontal)
        self.fork_merge_blend_slider.setRange(0, 100)
        self.fork_merge_blend_slider.setValue(50)
        self.fork_merge_blend_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.fork_merge_blend_slider.setTickInterval(25)
        self.fork_merge_blend_slider.valueChanged.connect(self._apply_fork_merge_live)
        blend_row.addWidget(self.fork_merge_blend_slider, stretch=1)
        blend_row.addWidget(QLabel("Branch B"))
        outer.addLayout(blend_row)

        self.fork_merge_blend_value_label = QLabel("A 50% / B 50%")
        self.fork_merge_blend_value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self.fork_merge_blend_value_label)

        reset_button = QPushButton("Reset to 50/50")
        reset_button.clicked.connect(lambda: self.fork_merge_blend_slider.setValue(50))
        outer.addWidget(reset_button)

        self.inspector_stack.addWidget(group)
        self._fork_merge_inspector_index = self.inspector_stack.count() - 1

    def _apply_fork_merge_live(self, value):
        """Reads the blend slider and writes into the selected stage's
        params -- called after every slider move (no Update Stage button,
        same real-time convention as Printer Lights/CDL/ACES DRT/Color
        Space). Guarded by _refreshing_inspector for the same reason those
        are -- switching TO a fork_merge stage also drives this slider via
        _refresh_inspector, and that shouldn't count as a live edit."""
        if self._refreshing_inspector:
            return
        stage = self._selected_stage()
        if stage is None or stage.kind != "fork_merge":
            return
        blend = value / 100.0
        stage.params["blend"] = blend
        stage.label = _format_fork_merge_label(stage.params)
        a_pct = round((1.0 - blend) * 100)
        b_pct = round(blend * 100)
        self.fork_merge_blend_value_label.setText(f"A {a_pct}% / B {b_pct}%")
        self._refresh_list()
        self._emit_changed()

    # ---- inspector dispatch -----------------------------------------

    def _refresh_inspector(self):
        # Guards _apply_aces_drt_live/_apply_colorspace_live against
        # firing while THIS method drives their combos to reflect a newly
        # selected stage -- see __init__'s docstring on
        # _refreshing_inspector. try/finally so the flag can't get stuck
        # True if anything below raises.
        self._refreshing_inspector = True
        try:
            self._refresh_inspector_body()
        finally:
            self._refreshing_inspector = False

    def _refresh_inspector_body(self):
        stage = self._selected_stage()
        if stage is None:
            self.inspector_stack.setCurrentIndex(self._empty_inspector_index)
            return

        if stage.kind == "lut":
            lut = stage.params.get("lut")
            self.lut_inspector_name_label.setText(self._lut_details_text(stage, lut))
            has_retarget = bool(stage.params.get("retarget_from") and stage.params.get("retarget_to"))
            self.lut_retarget_checkbox.blockSignals(True)
            self.lut_retarget_checkbox.setChecked(has_retarget)
            self.lut_retarget_checkbox.blockSignals(False)
            self.lut_retarget_from_combo.setEnabled(has_retarget)
            self.lut_retarget_to_combo.setEnabled(has_retarget)
            if has_retarget:
                idx = self.lut_retarget_from_combo.findText(stage.params["retarget_from"])
                if idx >= 0:
                    self.lut_retarget_from_combo.setCurrentIndex(idx)
                idx = self.lut_retarget_to_combo.findText(stage.params["retarget_to"])
                if idx >= 0:
                    self.lut_retarget_to_combo.setCurrentIndex(idx)
            self.inspector_stack.setCurrentIndex(self._lut_inspector_index)

        elif stage.kind == "printer_lights":
            p = stage.params
            for channel, key in (("R", "r_points"), ("G", "g_points"), ("B", "b_points")):
                slider = self._pl_sliders[channel]
                slider.blockSignals(True)
                slider.setValue(int(round(p.get(key, 0))))
                slider.blockSignals(False)
                self._pl_value_labels[channel].setText(str(slider.value()))
            idx = self.pl_space_combo.findText(p.get("working_colourspace", DEFAULT_WORKING_COLOURSPACE))
            if idx >= 0:
                # Blocked -- switching TO this stage shouldn't itself count
                # as a live edit (see _on_pl_space_changed); sliders above
                # get the same treatment.
                self.pl_space_combo.blockSignals(True)
                self.pl_space_combo.setCurrentIndex(idx)
                self.pl_space_combo.blockSignals(False)
            self.inspector_stack.setCurrentIndex(self._printer_lights_inspector_index)

        elif stage.kind == "aces_drt":
            if aces_drt.OCIO_AVAILABLE:
                p = stage.params
                idx = self.drt_config_combo.findData(p.get("config_name"))
                if idx >= 0 and idx != self.drt_config_combo.currentIndex():
                    self.drt_config_combo.setCurrentIndex(idx)
                idx = self.drt_working_space_combo.findText(p.get("working_colourspace", ""))
                if idx >= 0:
                    self.drt_working_space_combo.setCurrentIndex(idx)
                idx = self.drt_display_combo.findText(p.get("display", ""))
                if idx >= 0:
                    self.drt_display_combo.setCurrentIndex(idx)
                idx = self.drt_view_combo.findText(p.get("view", ""))
                if idx >= 0:
                    self.drt_view_combo.setCurrentIndex(idx)
            self.inspector_stack.setCurrentIndex(self._aces_drt_inspector_index)

        elif stage.kind == "cdl":
            if cdl.OCIO_AVAILABLE:
                p = stage.params
                slope = p.get("slope", cdl.IDENTITY_SLOPE)
                offset = p.get("offset", cdl.IDENTITY_OFFSET)
                power = p.get("power", cdl.IDENTITY_POWER)
                for field_key, values in (("slope", slope), ("offset", offset), ("power", power)):
                    r, g, b = values
                    for channel, value in zip(("R", "G", "B"), values):
                        spin = self._cdl_sop_spins[field_key][channel]
                        spin.blockSignals(True)
                        spin.setValue(value)
                        spin.blockSignals(False)
                    # Back-compute the wheel/master position from the
                    # stored R/G/B so the wheel shows wherever this
                    # stage's values actually point, same as any other
                    # fine-spinbox-driven update (see
                    # _on_cdl_channel_spin_changed) -- just without
                    # re-triggering that handler's own writes during
                    # inspector population.
                    strength = self._cdl_wheel_strength[field_key]
                    master, x, y = cb.rgb_to_master_and_wheel(r, g, b, strength=strength)
                    master_spin = self._cdl_master_spins[field_key]
                    master_spin.blockSignals(True)
                    master_spin.setValue(master)
                    master_spin.blockSignals(False)
                    self._cdl_wheels[field_key].set_balance(x, y, emit=False)

                self.cdl_sat_spin.blockSignals(True)
                self.cdl_sat_spin.setValue(p.get("sat", cdl.IDENTITY_SAT))
                self.cdl_sat_spin.blockSignals(False)
                self.cdl_sat_slider.blockSignals(True)
                self.cdl_sat_slider.setValue(round(self.cdl_sat_spin.value() * 100))
                self.cdl_sat_slider.blockSignals(False)

                idx = self.cdl_style_combo.findData(p.get("style", cdl.DEFAULT_STYLE))
                if idx >= 0:
                    self.cdl_style_combo.blockSignals(True)
                    self.cdl_style_combo.setCurrentIndex(idx)
                    self.cdl_style_combo.blockSignals(False)

                sop_enabled = p.get("sop_enabled", True)
                self.cdl_sop_enabled_checkbox.blockSignals(True)
                self.cdl_sop_enabled_checkbox.setChecked(sop_enabled)
                self.cdl_sop_enabled_checkbox.blockSignals(False)
                for channel_spins in self._cdl_sop_spins.values():
                    for spin in channel_spins.values():
                        spin.setEnabled(sop_enabled)
                for master_spin in self._cdl_master_spins.values():
                    master_spin.setEnabled(sop_enabled)
                for wheel in self._cdl_wheels.values():
                    wheel.setEnabled(sop_enabled)

                sat_enabled = p.get("sat_enabled", True)
                self.cdl_sat_enabled_checkbox.blockSignals(True)
                self.cdl_sat_enabled_checkbox.setChecked(sat_enabled)
                self.cdl_sat_enabled_checkbox.blockSignals(False)
                self.cdl_sat_spin.setEnabled(sat_enabled)
                self.cdl_sat_slider.setEnabled(sat_enabled)
            self.inspector_stack.setCurrentIndex(self._cdl_inspector_index)

        elif stage.kind == "colorspace":
            if csc.OCIO_AVAILABLE:
                p = stage.params
                idx = self.csc_config_combo.findData(p.get("config_name"))
                if idx >= 0 and idx != self.csc_config_combo.currentIndex():
                    self.csc_config_combo.setCurrentIndex(idx)

                # Infer which category this stage's src belongs to (a
                # stage only stores concrete src/dst names, not which
                # category the user originally picked from) so the
                # Category combo -- and therefore which list From/To
                # populate from -- lands on the right one when
                # re-selecting an existing stage.
                src = p.get("src", "")
                category = "display" if src.endswith(csc.DISPLAY_SUFFIX) else "scene"
                cat_idx = self.csc_category_combo.findData(category)
                if cat_idx >= 0 and cat_idx != self.csc_category_combo.currentIndex():
                    self.csc_category_combo.blockSignals(True)
                    self.csc_category_combo.setCurrentIndex(cat_idx)
                    self.csc_category_combo.blockSignals(False)
                self._populate_csc_spaces()

                idx = self.csc_from_combo.findText(src)
                if idx >= 0:
                    self.csc_from_combo.setCurrentIndex(idx)
                idx = self.csc_to_combo.findText(p.get("dst", ""))
                if idx >= 0:
                    self.csc_to_combo.setCurrentIndex(idx)
            self.inspector_stack.setCurrentIndex(self._colorspace_inspector_index)

        elif stage.kind == "fork_split":
            self.inspector_stack.setCurrentIndex(self._fork_split_inspector_index)

        elif stage.kind == "fork_merge":
            blend = stage.params.get("blend", 0.5)
            self.fork_merge_blend_slider.blockSignals(True)
            self.fork_merge_blend_slider.setValue(round(blend * 100))
            self.fork_merge_blend_slider.blockSignals(False)
            a_pct = round((1.0 - blend) * 100)
            b_pct = round(blend * 100)
            self.fork_merge_blend_value_label.setText(f"A {a_pct}% / B {b_pct}%")
            self.inspector_stack.setCurrentIndex(self._fork_merge_inspector_index)

    # ---- shared -----------------------------------------------------

    def get_stack(self):
        """The live Stack object -- SampleImagePanel reads this each
        render via stack.apply_stack_to_image_array for an exact
        direct-pixel preview. Callers should treat this as read-mostly;
        StackPanel is what mutates it and emits stack_changed."""
        return self.stack

    def get_baked_lut(self, name=None):
        """Bake the current stack into a flat colour.LUT3D -- used by
        MainWindow for the 3D viewer/info panel/export, same integration
        point every other baking function in this app has always had.
        Passes self._bake_cache so repeated calls (this fires on every
        single Stack edit -- see MainWindow._on_stack_changed) only
        recompute the stages that actually changed since the last call."""
        return st.bake_stack_to_lut3d(self.stack, name=name, cache=self._bake_cache)

    def set_bake_error(self, message):
        """Shows/hides the inline warning below the domain readout (see
        _build_list_ui) -- `message` a string to show it, None/"" to hide
        it. Called by MainWindow's _on_stack_changed, which is the one
        place that actually calls get_baked_lut() on every edit and can
        catch the ValueError _run_stages raises for a structurally
        invalid fork region (unmatched fork_split/fork_merge, nested
        forks, a branch-region stage missing its tag -- see stack.py's
        docstring, STACK FORKING) without crashing. This panel doesn't
        bake itself, so it can't catch that error on its own -- MainWindow
        hands the message back in rather than this panel needing its own
        redundant try/except around a bake it doesn't perform."""
        if message:
            self.bake_error_label.setText(f"⚠ {message}")
            self.bake_error_label.show()
        else:
            self.bake_error_label.clear()
            self.bake_error_label.hide()

    def set_stack(self, new_stack):
        """Replace the live Stack wholesale -- used by MainWindow for
        session load (see session_io.py). Clears the current selection
        (nothing in the new stack corresponds to whatever row was selected
        before), rebuilds the stage list and Grid Size/Domain readout from
        the new stack, and emits stack_changed exactly once, same as any
        other mutation path here -- pushes the new bake to the 3D
        viewer/scopes/sample image preview in one go rather than
        per-stage."""
        self.stack = new_stack
        # The incoming Stack's stages are fresh Stage objects with their
        # own ids -- nothing in the old cache would fingerprint-match them
        # anyway, so this is purely about releasing that memory right now
        # rather than one block at a time as edits happen to touch it.
        self._bake_cache.clear()
        self.size_combo.setCurrentText(str(self.stack.size))
        self._refresh_list()
        self._update_domain_readout()
        self._emit_changed()

    def apply_cdl_entry(self, entry):
        """Bring a cdl_library.CDLEntry back into the Stack -- the single
        entry point CDLLibraryPanel's "Apply to Stack" action (via
        MainWindow, same "panel doesn't know about StackPanel" wiring as
        StackMemoriesPanel.recall_requested -> StackPanel.set_stack) uses
        regardless of which of two cases applies:

          - If the currently SELECTED stage is a 'cdl' stage, overwrite
            its params in place -- the stage keeps its position, opacity,
            and enabled state in the stack, only its SOP/SAT/style/bypass
            values change (and its label updates to match, via
            _format_cdl_label) -- "swap in a different grade for the
            stage I'm already editing."
          - Otherwise, ADD A NEW 'cdl' stage at the end of the stack
            seeded with the entry's values -- so applying an entry is
            never a no-op just because nothing CDL-shaped happened to be
            selected.
        """
        stage = self._selected_stage()
        if stage is not None and stage.kind == "cdl":
            stage.params["slope"] = entry.slope
            stage.params["offset"] = entry.offset
            stage.params["power"] = entry.power
            stage.params["sat"] = entry.sat
            stage.params["style"] = entry.style
            stage.params["sop_enabled"] = entry.sop_enabled
            stage.params["sat_enabled"] = entry.sat_enabled
            stage.label = _format_cdl_label(stage.params)
            self._refresh_list()
            self._refresh_inspector()
            self._emit_changed()
        else:
            new_stage = st.make_cdl_stage(
                slope=entry.slope, offset=entry.offset, power=entry.power, sat=entry.sat,
                style=entry.style, sop_enabled=entry.sop_enabled, sat_enabled=entry.sat_enabled,
            )
            new_stage.label = _format_cdl_label(new_stage.params)
            self._add_stage(new_stage)

    def _emit_changed(self):
        self.stack_changed.emit()
