"""
stack_panel.py -- the single, unified operation stack: build an ordered
list of stages (a Printer Lights trim, an ACES DRT output transform, or an
ASC CDL SOP/SAT grade; LUT stages are added via the Browser panel
-- see add_lut_stage_from_path below), reorder them,
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

LUT grid size/domain are no longer edited here: they're export settings
(stack.BakeSettings), edited in File > Export -- see bake_settings_form.py.

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
with the selected one highlighted; (5) a card's enabled-toggle button
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

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidgetItem,
    QPushButton, QLabel, QMessageBox, QGroupBox, QSlider, QFrame, QToolButton, QMenu,
    QButtonGroup, QScrollArea,
)

import stack as st
import aces_drt
import cdl
import colorspace_convert as csc
import exposure_contrast as ec
import grading_primary as gpr
import grading_rgb_curve as grc
import grading_tone as gtn
from app_logic import load_lut_any_format
from printer_lights import DEFAULT_WORKING_COLOURSPACE
import hover_inspector
from stack_memory import StackMemory
from cdl_library import CDLEntry
# style.HINT_TEXT_HEX -- see style.py's FOURTEENTH PASS docstring: hint
# text is a literal hex now, not palette(placeholder-text) (doesn't
# reliably survive Qt's style-polish pass on this Qt build).
from style import HINT_TEXT_HEX
from .labels import (
    _format_cdl_label, _format_exposure_contrast_label,
    _format_fork_merge_label, _format_grading_primary_label,
    _format_grading_rgb_curve_label, _format_grading_tone_label,
    _matte_options_by_index, _stage_info_text,
)
from . import rows
from .rows import _ForkBranchGridRowWidget, _StageRowWidget
from .list_widget import _RowTargets, _StackListWidget
from .widgets import _CurrentPageStackedWidget
from .inspect_lut import LutInspectorMixin
from .inspect_drt import DrtInspectorMixin
from .inspect_cdl import CdlInspectorMixin
from .inspect_primary import PrimaryInspectorMixin
from .inspect_tone import ToneInspectorMixin
from .inspect_warper import WarperInspectorMixin
from .inspect_mattes import MatteInspectorMixin

# Sentinel default for StackPanel._add_stage's `branch` param -- lets a
# caller distinguish "didn't specify, auto-assign a branch if this lands
# inside an open fork region" (every "Add Operator" menu action, which
# has no column information) from "explicitly wants None" (a drag-and-
# drop insert that resolved to an ordinary, non-fork position -- see
# StackPanel._resolve_drop_target). Plain `None` can't serve as that
# "unspecified" default since it's also the legitimate explicit value
# for "no branch."
_UNSPECIFIED_BRANCH = object()

class StackPanel(LutInspectorMixin, DrtInspectorMixin, CdlInspectorMixin, PrimaryInspectorMixin, ToneInspectorMixin, WarperInspectorMixin, MatteInspectorMixin, QObject):
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
    # no idea the Gallery exists; MainWindow routes this into
    # StackMemoriesPanel.save_cdl_entry (CDL Library was folded into the
    # Gallery, per Tom -- see stack_memories_panel.py's own docstring),
    # same "panels don't know about each other, MainWindow bridges"
    # architecture as StackMemoriesPanel.recall_requested ->
    # StackPanel.set_stack. `object` (not `dict`) as the second signal
    # type for safety across PySide6 versions -- Qt's signal machinery
    # handles a plain Python dict fine as an "object" payload either way.
    cdl_save_requested = Signal(str, object)
    # Sequence/Shot toggle requests (see _build_mode_toggle_row) -- REQUESTS,
    # not the swap itself: MainWindow is what actually calls
    # activate_sequence_mode()/activate_shot_mode(clip) in response,
    # wrapped in its own _restoring_stack_history flag (same convention as
    # every other wholesale stack-replace path -- session open/new, undo/
    # redo) so switching modes never itself creates a spurious undo entry.
    # StackPanel can't set that flag itself -- it has no reference to
    # MainWindow -- hence routing the actual activation back out through
    # MainWindow rather than just calling activate_*_mode directly from
    # the button's own click handler.
    sequence_mode_requested = Signal()
    shot_mode_requested = Signal(object)  # the TimelineClip to activate

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
        # The stage (if any) currently Highlight-toggled in its own
        # inspector (per Tom: "a way to preview the mask similar to
        # resolve's highlight mode") -- see get_highlight_stage_id,
        # _on_ky_highlight_toggled, and SampleImagePanel.set_highlight_
        # getter, which is what actually reads this. Deliberately
        # ephemeral UI state, NOT persisted with the stage/session (see
        # get_highlight_stage_id's own docstring) -- and, like the row
        # selection above, only one at a time; auto-cleared in
        # _on_selection_changed whenever the selection moves away from
        # it, so it can never silently stay "on" for a stage you've
        # clicked away from.
        self._highlight_stage_id = None
        # Zero-arg-callback-in, callback-out getter for the Viewer's own
        # eyedropper (per Tom) -- see set_eyedropper_requester's own
        # docstring for why this indirection exists (StackPanel doesn't
        # hold a WipeCompareWidget reference of its own).
        self._eyedropper_requester = None
        self._row_stage_ids = []
        self._row_insert_index = []
        # stage_id -> _StageRowWidget, rebuilt every _refresh_list() call
        # (see _add_stage_row_item/_flush_fork_grid) -- lets a live param
        # edit (_update_row_content, called by every _apply_*_live
        # handler and _on_row_enabled_clicked) update just the one row a
        # change actually touched instead of tearing down and rebuilding
        # every card via _refresh_list(), which used to run on every
        # single tick of a slider/spinbox drag -- see _StageRowWidget's
        # own docstring, "SNAPPIER/MORE INTERACTIVE PASS."
        self._stage_row_widgets = {}
        self.stack = st.Stack()
        # Continuity Grading Timeline's Sequence/Shot toggle (per Tom: "one
        # panel...a toggle...between sequence and shot stack") -- self.stack
        # is always whichever Stack is CURRENTLY loaded into this editor
        # (Sequence's own, or a Timeline clip's, per _shot_mode_active),
        # unchanged in meaning for every existing get_stack()/get_baked_lut()/
        # preview consumer -- toggling to Shot mode is exactly "load a
        # different Stack into the same editor," so those all keep working
        # unmodified and now naturally reflect whichever stack is being
        # edited. self._sequence_stack is this panel's OWN separate memory
        # of "the Sequence stack," kept current by set_stack() below
        # regardless of which stack is currently on screen -- MainWindow's
        # session save uses get_sequence_stack() (never get_stack(), which
        # would return a clip's stack while Shot mode is active) so a save
        # made mid-Shot-edit still persists the Sequence grade, not
        # whatever clip happened to be on screen (a clip's own grade is
        # separately embedded via timeline_to_session_dict already).
        self._sequence_stack = self.stack
        self._shot_mode_active = False
        # Zero-arg callable -> the Timeline's own currently-selected clip
        # (duck-typed: only .stack/.name read below, see
        # set_selected_clip_getter's own docstring) or None -- wired from
        # MainWindow, same pull-based convention as set_eyedropper_requester.
        self._selected_clip_getter = None
        # Reused across repeated get_baked_lut() calls (MainWindow's
        # _on_stack_changed calls it on every single edit) so nudging one
        # stage's opacity/params doesn't re-bake every OTHER stage from
        # scratch too -- see StageCache's own docstring in stack.py.
        # set_stack (session load/undo-redo/Stack Memory recall, below)
        # clears it, since a wholesale-replaced Stack has nothing in
        # common with whatever was cached for the old one.
        self._bake_cache = st.StageCache()
        # The project's working color space (MainWindow pushes it from
        # the canvas, see set_default_working_colourspace) -- what every
        # newly added color-aware stage starts out assuming.
        self._default_working_colourspace = DEFAULT_WORKING_COLOURSPACE
        self.list_widget_area = QWidget()
        self.params_widget_area = QWidget()
        self._build_list_ui()
        self._build_params_ui()
        self._refresh_list()
        self._refresh_inspector()

    # ---- list area (stage list, reorder, add) ----------------------------

    def _build_list_ui(self):
        layout = QVBoxLayout(self.list_widget_area)
        layout.setContentsMargins(0, 0, 0, 0)  # PanelFrame provides the outer gutter

        self._build_mode_toggle_row(layout)

        # The color pipeline around this Stack: input -> working -> display
        # (colour_pipeline.py) -- MainWindow keeps it current via
        # set_pipeline_summary.
        self.pipeline_label = QLabel()
        self.pipeline_label.setObjectName("stackPipelineLabel")
        self.pipeline_label.setWordWrap(True)
        self.pipeline_label.setStyleSheet(f"color: {HINT_TEXT_HEX}; font-size: 10px;")
        layout.addWidget(self.pipeline_label)

        # _StackListWidget (not plain QListWidget) -- adds card-style rows
        # with a clickable enable toggle per stage, drag-to-reorder, and
        # drag-a-LUT-in-from-the-Browser -- see its docstring above.
        self.list_widget = _StackListWidget(self)
        self.list_widget.currentRowChanged.connect(self._on_selection_changed)
        layout.addWidget(self.list_widget, stretch=1)
        hover_inspector.register(self.list_widget, self._hover_info_for_item)

        # No more standalone "Enabled" checkbox here -- each row's own
        # enabled indicator (a filled/hollow dot) is directly clickable
        # now, see _StageRowWidget / _on_row_enabled_clicked. Likewise no
        # more standalone "Opacity:" control here either (removed per
        # Tom, 2026-08-12 round two -- confusing, disconnected from the
        # row it actually affected) -- each row now has its OWN opacity
        # spin box built into the card, see _StageRowWidget.opacity_spin
        # / StackPanel._on_row_opacity_changed.

        # No more Move Up/Move Down/Remove button row (per Tom) --
        # reordering is drag-and-drop (see _StackListWidget), removing is
        # each row's own "x" (_StageRowWidget.delete_button /
        # _on_row_delete_clicked) or Delete/Backspace on the selected row
        # (see _StackListWidget.keyPressEvent -> _on_remove_clicked).

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

    def _build_mode_toggle_row(self, layout):
        """Sequence/Shot toggle (per Tom: "share one panel and make it a
        toggle...between sequence and shot stack") -- Shot starts disabled
        since nothing's selected in the Timeline yet at construction time
        (see notify_selected_clip_changed, wired from MainWindow to
        TimelinePanel.clip_selected, for how it gets enabled/re-labeled).
        Exclusive QPushButtons (not a QComboBox) so the current mode is
        always visible at a glance without opening anything, same
        "state you need to see, not just set" reasoning as this panel's
        own per-row enabled dot."""
        row = QHBoxLayout()
        self.sequence_mode_button = QPushButton("Sequence")
        self.sequence_mode_button.setCheckable(True)
        self.sequence_mode_button.setChecked(True)
        self.sequence_mode_button.clicked.connect(lambda: self.sequence_mode_requested.emit())
        self.shot_mode_button = QPushButton("Shot")
        self.shot_mode_button.setCheckable(True)
        self.shot_mode_button.setEnabled(False)
        self.shot_mode_button.clicked.connect(self._on_shot_mode_button_clicked)
        self._mode_button_group = QButtonGroup(self)
        self._mode_button_group.setExclusive(True)
        self._mode_button_group.addButton(self.sequence_mode_button)
        self._mode_button_group.addButton(self.shot_mode_button)
        row.addWidget(self.sequence_mode_button)
        row.addWidget(self.shot_mode_button)
        row.addStretch(1)
        layout.addLayout(row)

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
        """Add this panel's "Add stage" actions to
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
        custom_drt_action = menu.addAction("Add CAMier DRT", self._on_add_custom_drt_clicked)
        custom_drt_action.setEnabled(aces_drt.OCIO_AVAILABLE)
        if not aces_drt.OCIO_AVAILABLE:
            custom_drt_action.setToolTip(
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
        gpr_action = menu.addAction("Add Grading Primary", self._on_add_grading_primary_clicked)
        gpr_action.setEnabled(gpr.OCIO_AVAILABLE)
        if not gpr.OCIO_AVAILABLE:
            gpr_action.setToolTip(
                f"PyOpenColorIO isn't installed: {gpr.OCIO_IMPORT_ERROR}")
        ec_action = menu.addAction("Add Exposure/Contrast", self._on_add_exposure_contrast_clicked)
        ec_action.setEnabled(ec.OCIO_AVAILABLE)
        if not ec.OCIO_AVAILABLE:
            ec_action.setToolTip(
                f"PyOpenColorIO isn't installed: {ec.OCIO_IMPORT_ERROR}")
        gtn_action = menu.addAction("Add Grading Tone", self._on_add_grading_tone_clicked)
        gtn_action.setEnabled(gtn.OCIO_AVAILABLE)
        if not gtn.OCIO_AVAILABLE:
            gtn_action.setToolTip(
                f"PyOpenColorIO isn't installed: {gtn.OCIO_IMPORT_ERROR}")
        grc_action = menu.addAction("Add Grading RGB Curve", self._on_add_grading_rgb_curve_clicked)
        grc_action.setEnabled(grc.OCIO_AVAILABLE)
        if not grc.OCIO_AVAILABLE:
            grc_action.setToolTip(
                f"PyOpenColorIO isn't installed: {grc.OCIO_IMPORT_ERROR}")
        # No OCIO_AVAILABLE gate -- like Printer Lights, WARPier works
        # either way (auto-dispatches to the colour-science fallback,
        # see color_warper.py's own docstring).
        menu.addAction("Add WARPier", self._on_add_color_warper_clicked)
        # No OCIO gate either -- keyer.py auto-dispatches OCIO-vs-colour-
        # science the same way WARPier's own decode does (see keyer.py's
        # own docstring), so it works either way too.
        menu.addAction("Add Keyer", self._on_add_keyer_clicked)
        # No OCIO gate -- pure numpy matte combination, no color-space
        # conversion involved at all (see matte_combine.py's own
        # docstring).
        menu.addAction("Add Matte Combine", self._on_add_matte_combine_clicked)
        # No OCIO gate -- pure numpy geometry, no color-space conversion
        # involved at all (see matte_shape.py's own docstring).
        menu.addAction("Add Matte Shape", self._on_add_matte_shape_clicked)
        menu.addSeparator()
        fork_action = menu.addAction("Add Fork (Split + Merge)", self._on_add_fork_clicked)
        fork_action.setToolTip(
            "Adds a Fork Split and a matching Fork Merge, appended as a "
            "pair. Add any stage(s) between them and tag each one Branch "
            "A or Branch B using that row's own branch dropdown -- see "
            "stack.py's docstring, STACK FORKING.")

    # ---- params area (stage inspector) ------------------------------------

    def _build_params_ui(self):
        layout = QVBoxLayout(self.params_widget_area)
        layout.setContentsMargins(0, 0, 0, 0)  # PanelFrame provides the outer gutter

        self.inspector_stack = _CurrentPageStackedWidget()
        self._build_empty_inspector()
        self._build_lut_inspector()
        self._build_printer_lights_inspector()
        self._build_aces_drt_inspector()
        self._build_custom_drt_inspector()
        self._build_cdl_inspector()
        self._build_colorspace_inspector()
        self._build_grading_primary_inspector()
        self._build_exposure_contrast_inspector()
        self._build_grading_tone_inspector()
        self._build_grading_rgb_curve_inspector()
        self._build_color_warper_inspector()
        self._build_keyer_inspector()
        self._build_matte_combine_inspector()
        self._build_matte_shape_inspector()
        self._build_fork_split_inspector()
        self._build_fork_merge_inspector()
        # Scroll area, so the dock's floor isn't the current page's FULL
        # minimum height: unwrapped, selecting most operators (CAMier DRT
        # ~713px, ASC CDL ~567px of inspector alone) pushed MainWindow's
        # own minimumSizeHint past a 1080p-logical screen's available
        # height (measured 2026-10-05, 4K@200% GNOME), so the window grew
        # itself off the bottom of the screen just from loading a LUT.
        # _CurrentPageStackedWidget still matters inside it -- it's what
        # makes the scroll range track the page actually showing.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.inspector_stack)
        layout.addWidget(scroll, stretch=1)

    # ---- stage list -------------------------------------------------------

    def _hover_info_for_item(self, item):
        """Alt+hover info card content (see hover_inspector.py). Rows
        aren't 1:1 with self.stack.stages once a fork region is in play
        (see _refresh_list's own docstring) -- self._row_stage_ids[row]
        is the same per-row stage-ID lookup _on_selection_changed/drag-
        and-drop already rely on, so this stays correct for a fork-grid
        row exactly the same way they do. A header/spacer row (empty
        stage-ID list) has nothing to show."""
        row = self.list_widget.row(item)
        if not (0 <= row < len(self._row_stage_ids)):
            return None
        stage_ids = self._row_stage_ids[row]
        if not stage_ids:
            return None
        blocks = []
        for stage_id in stage_ids:
            stage = self.stack.find_stage(stage_id)
            if stage is not None:
                blocks.append(_stage_info_text(stage))
        return "\n---\n".join(blocks) if blocks else None

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
        - self._row_insert_index[row] -- a _RowTargets (see that class's
          own docstring) saying where a stage dropped at this row's
          position should land -- an ordinary self.stack.stages index
          for a ordinary/non-fork row, or a branch_a/branch_b pair for a
          fork-interior row (see _on_stage_reordered/_on_lut_path_
          dropped). Has one extra trailing entry for "dropped past the
          last row."

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
        self._stage_row_widgets = {}
        stage_id_to_row = {}

        stages = self.stack.stages
        # See _matte_options_by_index's own docstring -- both computed
        # once here (letters shared between the two, so
        # stack.matte_letters_for only ever runs once per rebuild),
        # consumed by index in _add_stage_row_item/_flush_fork_grid
        # below, so every row's matte_combo offers exactly the upstream
        # matte-producing stages, and a matte-PRODUCING row can show its
        # own current letter (see _StageRowWidget's own_letter param),
        # regardless of where that row ends up rendered.
        self._matte_letters = st.matte_letters_for(stages)
        self._matte_options = _matte_options_by_index(stages, self._matte_letters)
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
                        # -- should be UNREACHABLE now via drag, add, or
                        # delete (branch assignment is purely positional,
                        # see _StackListWidget's own docstring); this is
                        # a defensive rendering path for a corrupted or
                        # hand-edited session file (see set_bake_error,
                        # which is what actually surfaces the problem to
                        # the user -- _run_stages raises on exactly this
                        # state). Flush whatever's been buffered so far
                        # as its own grid block, THEN render this one as
                        # an ordinary full-width row, so it doesn't
                        # silently vanish into a column it was never
                        # actually placed in.
                        self._flush_fork_grid(region_a, region_b, i, stage_id_to_row)
                        region_a, region_b = [], []
                        self._add_stage_row_item(s, i, stage_id_to_row)
                        i += 1
                merge_index = i  # the fork_merge's own index, or n if unmatched (already-invalid state)
                self._flush_fork_grid(region_a, region_b, merge_index, stage_id_to_row)
                if i < n and stages[i].kind == "fork_merge":
                    # Branch-only _RowTargets, not the ordinary default
                    # _add_stage_row_item would otherwise use -- dropping
                    # just ABOVE fork_merge is still logically inside the
                    # fork (append to a branch), not an ordinary
                    # top-level position; see _RowTargets' own docstring.
                    self._add_stage_row_item(
                        stages[i], i, stage_id_to_row,
                        row_targets=_RowTargets(None, merge_index, merge_index))
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
        # "append at the end of the stack," always ordinary (the end of
        # the whole stack can never itself be inside an open fork
        # region -- an unclosed fork_split is already an invalid state
        # flagged via set_bake_error, not a legitimate drop target).
        self._row_insert_index.append(_RowTargets(len(stages), None, None))

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

    def _update_row_content(self, stage_id):
        """Updates just ONE row's card in place (label/opacity/enabled
        state) via _StageRowWidget.update_content, instead of
        _refresh_list()'s clear-and-rebuild-everything -- see
        _stage_row_widgets' own docstring in __init__ and
        _StageRowWidget's "SNAPPIER/MORE INTERACTIVE PASS" docstring.
        Every _apply_*_live handler below (CDL, ACES DRT, Color Space,
        Grading Primary, Exposure/Contrast, Grading Tone, Grading RGB
        Curve, Fork Merge's blend) and _on_row_enabled_clicked call this
        instead of self._refresh_list() now, since none of them change
        a stage's POSITION or its fork-region membership -- only its
        label text, opacity sub-label, or enabled glyph, none of which
        need the list rebuilt out from under every other row too.

        Falls back to a full _refresh_list() if `stage_id` isn't in
        _stage_row_widgets (shouldn't happen in practice -- would mean a
        live-apply handler fired for a stage that was never actually
        rendered -- but a stale/missing row is exactly the kind of thing
        worth recovering from safely rather than silently no-opping)."""
        try:
            stage = self.stack.find_stage(stage_id)
        except KeyError:
            return
        widget = self._stage_row_widgets.get(stage_id)
        if widget is None:  # pragma: no cover -- defensive, see docstring
            self._refresh_list()
            return
        widget.update_content(stage)

    def _add_stage_row_item(self, stage, stack_index, stage_id_to_row, row_targets=None):
        """Appends one full-width QListWidgetItem for `stage` (at
        self.stack.stages[stack_index]) -- used for every row that isn't
        part of a fork region's two-column table (this now INCLUDES
        fork_split/fork_merge themselves -- both are ordinary full-width
        cards, just with special meaning to _run_stages). Records this
        row's position in both lookup tables _refresh_list builds; see
        its docstring.

        `row_targets`: the _RowTargets this row should use for drop
        resolution -- defaults to `_RowTargets(stack_index, None, None)`
        (an ordinary, non-fork-interior drop target), which is correct
        for every caller except fork_merge's own row: dropping just
        ABOVE fork_merge is still logically inside the fork (append to
        the end of whichever branch), not an ordinary top-level position,
        so _refresh_list passes an explicit branch-only override for
        that one row. See _RowTargets' own docstring."""
        item = QListWidgetItem()
        row_widget = _StageRowWidget(
            stage, self._on_row_clicked, self._on_row_enabled_clicked,
            self._on_row_delete_clicked, on_opacity_changed=self._on_row_opacity_changed,
            matte_options=self._matte_options[stack_index],
            on_matte_changed=self._on_row_matte_changed,
            own_matte_letter=self._matte_letters.get(stage.id))
        item.setSizeHint(row_widget.sizeHint())
        self.list_widget.addItem(item)
        self.list_widget.setItemWidget(item, row_widget)
        row = self.list_widget.count() - 1
        self._row_stage_ids.append([stage.id])
        self._row_insert_index.append(row_targets or _RowTargets(stack_index, None, None))
        stage_id_to_row[stage.id] = row
        self._stage_row_widgets[stage.id] = row_widget

    def _flush_fork_grid(self, region_a, region_b, merge_index, stage_id_to_row):
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

        ALWAYS renders the header plus at least one paired row now, even
        when BOTH branches are empty (e.g. right after "Add Fork") -- per
        Tom, a fork region needs a real, always-droppable landing zone
        immediately, not just header text with nothing to drop onto.

        Every cell is a genuine, fully draggable _StageRowWidget now (see
        _StackListWidget's own docstring on resolving drag source/target
        by stage id and column rather than QListWidget row) or an inert
        _EmptyBranchCell. `merge_index` -- this region's fork_merge's own
        self.stack.stages index -- is what a PADDED (empty) side's
        _RowTargets branch entry resolves to: "append to the end of that
        branch," always well-defined even when the branch is currently
        empty, which is exactly what makes every interior row a valid
        drop target for BOTH columns, never landing "in neither branch"
        (see _RowTargets' own docstring)."""
        first_a = region_a[0][0] if region_a else merge_index
        first_b = region_b[0][0] if region_b else merge_index
        self._add_fork_grid_header(first_a, first_b)
        pairs = list(zip_longest(region_a, region_b)) or [(None, None)]
        for a_entry, b_entry in pairs:
            a_index, a_stage = a_entry if a_entry else (None, None)
            b_index, b_stage = b_entry if b_entry else (None, None)
            item = QListWidgetItem()
            row_widget = _ForkBranchGridRowWidget(
                a_stage, b_stage, self._on_row_clicked, self._on_row_enabled_clicked,
                self._on_row_delete_clicked, self._on_row_opacity_changed,
                left_matte_options=self._matte_options[a_index] if a_index is not None else None,
                right_matte_options=self._matte_options[b_index] if b_index is not None else None,
                on_matte_changed=self._on_row_matte_changed,
                left_matte_letter=self._matte_letters.get(a_stage.id) if a_stage is not None else None,
                right_matte_letter=self._matte_letters.get(b_stage.id) if b_stage is not None else None)
            item.setSizeHint(row_widget.sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, row_widget)
            row = self.list_widget.count() - 1
            ids_here = [s.id for s in (a_stage, b_stage) if s is not None]
            self._row_stage_ids.append(ids_here)
            self._row_insert_index.append(_RowTargets(
                None,
                a_index if a_index is not None else merge_index,
                b_index if b_index is not None else merge_index))
            if a_stage is not None:
                stage_id_to_row[a_stage.id] = row
                self._stage_row_widgets[a_stage.id] = row_widget.left_widget
            if b_stage is not None:
                stage_id_to_row[b_stage.id] = row
                self._stage_row_widgets[b_stage.id] = row_widget.right_widget

    def _add_fork_grid_header(self, first_a_index, first_b_index):
        """A lightweight "Branch A | Branch B" column header, added once
        right before the first paired row of a fork region's table --
        so which side is which doesn't rely purely on remembering
        left/right (see _flush_fork_grid). Not selectable (NoItemFlags)
        so keyboard Up/Down skips over it, same as a real spacer would --
        but it's still a genuine drop target: dropping on it means
        "insert as the very first thing in that branch," same as
        dropping on the upper half of the first paired row below it
        would, which is why `first_a_index`/`first_b_index` (each
        already resolved by the caller to either the first stage's own
        index, or the region's fork_merge index if that branch is
        currently empty) are exactly what the first grid row itself
        would use -- see _RowTargets' own docstring."""
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
        self._row_insert_index.append(_RowTargets(None, first_a_index, first_b_index))

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

        # Highlight auto-clears the moment selection moves away from the
        # stage it belongs to (per Tom's own Highlight-mode ask) -- one
        # central place, rather than scattering this check across every
        # matte-producing kind's own inspector, so it can never silently
        # stay "on" for a stage you've clicked away from. Not just a
        # local reset -- the Viewer is currently showing a highlighted
        # preview for the OLD stage, so this has to re-trigger a render
        # the same way every other live edit does.
        if self._highlight_stage_id is not None and self._highlight_stage_id != self._selected_stage_id:
            self._highlight_stage_id = None
            self._emit_changed()

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
            if self.list_widget.currentRow() == row:
                # A fork-grid row holds BOTH branches' cells under the
                # same QListWidget row index -- clicking branch B while
                # branch A (same row) is selected leaves currentRow()
                # unchanged, so Qt swallows setCurrentRow() below as a
                # no-op and currentRowChanged never fires, leaving
                # _on_selection_changed (and the styling/inspector
                # refresh it drives) stale even though
                # self._selected_stage_id above already moved to B.
                # Drive it directly in that case.
                self._on_selection_changed(row)
            else:
                self.list_widget.setCurrentRow(row)
        else:  # pragma: no cover -- defensive, shouldn't happen
            self._on_selection_changed(-1)

    def _on_row_enabled_clicked(self, stage_id):
        try:
            stage = self.stack.find_stage(stage_id)
        except KeyError:
            return
        stage.enabled = not stage.enabled
        # Targeted update, not _refresh_list() -- toggling enabled never
        # changes a stage's position or fork-region membership, so there's
        # nothing structural for a full rebuild to catch; see
        # _update_row_content's own docstring. Also lets the toggle's own
        # dim animation actually play (a fresh widget from _refresh_list()
        # would just appear already-dimmed, nothing to ease from).
        self._update_row_content(stage_id)
        self._emit_changed()

    def _resolve_drop_target(self, target_row, branch_guess):
        """Turns a raw (target_row, branch_guess) pair from
        _StackListWidget -- target_row a QListWidget row POSITION (see
        _drop_row), branch_guess a purely x-position-derived "a"/"b" (see
        _drop_branch, always one or the other regardless of whether the
        row is actually fork-interior) -- into the two things a caller
        actually needs: (insert_index, branch), where `branch` is the
        REAL answer (None for an ordinary row, ignoring branch_guess
        entirely; "a"/"b" for a fork-interior row, picking branch_guess's
        side).

        self._row_insert_index (rebuilt every _refresh_list call, see
        that method's own docstring and _RowTargets) is what makes this
        possible without inspecting stack structure directly here --
        each row already recorded, at render time, whichever of
        (ordinary, branch_a, branch_b) actually applies to it. Falls
        back to "append at the very end, ordinary" for an out-of-range
        row (dropped below the last real row)."""
        if 0 <= target_row < len(self._row_insert_index):
            targets = self._row_insert_index[target_row]
        else:
            targets = _RowTargets(len(self.stack.stages), None, None)
        if targets.ordinary is not None:
            return targets.ordinary, None
        if branch_guess == "a":
            return targets.branch_a, "a"
        return targets.branch_b, "b"

    def _on_stage_reordered(self, source_stage_id, target_row, branch_guess):
        """source_stage_id is the STAGE that was pressed (see
        _StackListWidget._pressed_stage_id/_stage_id_at) -- not a
        QListWidget row, since a fork-grid row can hold two independently
        draggable stages. target_row/branch_guess resolve via
        _resolve_drop_target into an actual self.stack.stages index and
        the real target branch (None outside any fork, "a"/"b" inside
        one)."""
        if source_stage_id is None:
            return
        try:
            stage = self.stack.find_stage(source_stage_id)
        except KeyError:
            return
        source_index = self.stack.stages.index(stage)
        target_index, branch = self._resolve_drop_target(target_row, branch_guess)
        # target_index is where the stage should land if the list were
        # rebuilt fresh; pulling it out of its OWN current spot first
        # shifts everything after that spot down by one, so any target
        # past it needs the same adjustment move_stage's callers have
        # always needed.
        dest = target_index - 1 if target_index > source_index else target_index
        dest = max(0, min(dest, len(self.stack.stages) - 1))
        # Position AND branch both have to be unchanged for this to be a
        # true no-op -- a same-index cross-branch move (e.g. the last
        # slot of a short Branch A dropped into Branch B, which can
        # numerically resolve to the same index) must still apply.
        if dest == source_index and branch == stage.branch:
            return
        self.stack.move_stage(stage.id, dest)
        # Unconditional, not just "if branch is not None" -- this is what
        # clears a stray tag when a stage is dragged OUT of a fork region
        # back to an ordinary position (previously, only the now-removed
        # branch combo's "No branch" option could do that at all).
        stage.branch = branch
        self._refresh_list(keep_selection=stage.id)
        self._emit_changed()

    def _on_lut_path_dropped(self, path, target_row, branch_guess):
        """A LUT file was dragged in from the Browser panel and dropped at
        `target_row`/`branch_guess` -- see _resolve_drop_target."""
        insert_index, branch = self._resolve_drop_target(target_row, branch_guess)
        self.add_lut_stage_from_path(path, index=insert_index, branch=branch)

    def _on_row_opacity_changed(self, stage_id, value):
        """`_StageRowWidget.opacity_spin`'s own valueChanged, for THAT
        row's stage specifically -- not "whichever stage happens to be
        selected" (see class docstring point 4 on why the old panel-level
        opacity control worked that way and why it moved). Targeted
        update, not _refresh_list() -- this spin has drag-to-scrub
        enabled, so it fires on every tick of a drag same as the various
        _apply_*_live handlers; see _update_row_content's own docstring."""
        try:
            stage = self.stack.find_stage(stage_id)
        except KeyError:
            return
        stage.opacity = value / 100.0
        self._update_row_content(stage_id)
        self._emit_changed()

    def _on_row_matte_changed(self, stage_id, matte_source_id):
        """`_StageRowWidget.matte_combo`'s own currentIndexChanged, for
        THAT row's stage specifically -- same "targeted update, not
        _refresh_list()" shape as _on_row_opacity_changed just above
        (this doesn't change the stage's POSITION or fork-region
        membership, so a full rebuild would be needless work here too).
        `matte_source_id` is already the combo's own itemData -- the
        PRODUCING keyer's Stage.id, or None for "None" (see this class's
        own docstring / stack.py's "MATTE CHANNELS"), never a letter."""
        try:
            stage = self.stack.find_stage(stage_id)
        except KeyError:
            return
        if matte_source_id is None:
            stage.params.pop("matte_source_id", None)
        else:
            stage.params["matte_source_id"] = matte_source_id
        self._update_row_content(stage_id)
        self._emit_changed()

    def _delete_stage_linked(self, stage_id):
        """The one place both stage-removal entry points (_on_remove_
        clicked, _on_row_delete_clicked) actually mutate self.stack --
        per Tom: fork_split/fork_merge "should also be linked so they
        delete together." Deleting either half of a matched pair removes
        BOTH markers as one action, rather than leaving the other half
        dangling (previously an invalid, bake-erroring stack state until
        manually cleaned up -- see set_bake_error). Any stage currently
        assigned to a branch inside that region is NOT deleted -- left
        exactly where it already sits in self.stack.stages, just with
        `.branch` cleared back to None, so it becomes an ordinary
        top-level stage instead of silently losing a user's tuned grade.
        No reordering: in the common "add a fork, then fill Branch A,
        then Branch B" workflow, physical list order already reads as
        "A's stages, then B's" once un-tagged, so this is both the
        lowest-risk option and, in practice, usually the visually
        obvious one too.

        Falls back to a plain Stack.remove_stage for an ordinary stage,
        or a fork marker with no resolvable partner (find_fork_partner
        returning None -- an already-corrupted/hand-edited stack;
        removing just the one marker is no worse than today)."""
        stage = self.stack.find_stage(stage_id)  # KeyError propagates -- caller decides how to handle
        if stage.kind in ("fork_split", "fork_merge"):
            partner = st.find_fork_partner(self.stack.stages, stage_id)
            if partner is not None:
                split_id, merge_id, interior_ids = partner
                for interior_id in interior_ids:
                    self.stack.find_stage(interior_id).branch = None
                self.stack.remove_stage(split_id)
                self.stack.remove_stage(merge_id)
                return
        self.stack.remove_stage(stage_id)

    def _on_remove_clicked(self):
        """Removes whichever stage is currently SELECTED -- the
        Delete/Backspace path (see _StackListWidget.keyPressEvent). For
        "remove this specific row regardless of selection" (the per-row
        "x" button), see _on_row_delete_clicked instead."""
        stage = self._selected_stage()
        if stage is None:
            return
        self._delete_stage_linked(stage.id)
        # _refresh_list's own tail already calls _on_selection_changed
        # (directly, not via signal -- its own rebuild runs with the list
        # widget's signals blocked), which itself calls _refresh_inspector
        # -- a separate call here was a redundant second inspector
        # rebuild on every single stage deletion (found in code review).
        self._refresh_list()
        self._emit_changed()

    def _on_row_delete_clicked(self, stage_id):
        """A stage row's own "x" was clicked -- removes THAT stage
        specifically, whether or not it's the currently selected one
        (same "act on the row you clicked" convention as
        _on_row_enabled_clicked). fork_split/fork_merge's "x" removes
        BOTH markers together -- see _delete_stage_linked."""
        try:
            self._delete_stage_linked(stage_id)
        except KeyError:
            return
        # See _on_remove_clicked's own comment just above -- _refresh_list
        # already refreshes the inspector via _on_selection_changed.
        self._refresh_list()
        self._emit_changed()

    # ---- add stage ----------------------------------------------------
    #
    # LUT File doesn't have its own button here -- the Browser panel
    # already has an identical "load a file" (double-click) entry point,
    # and MainWindow routes that signal into the public method below
    # instead of setting "the current LUT" directly, so there's no need
    # for a second, duplicate way to do the same thing inside the Stack
    # panel itself. "New Identity LUT..." (the Browser's other entry
    # point, and this panel's own add_identity_stage) were both removed
    # (per Tom) -- the Viewer already previews an empty stack directly
    # now (see sample_image_panel.py's _recompute_sample_after), which
    # made "start from an identity cube just to have something to grade"
    # redundant: an empty stack IS that starting point, with no synthetic
    # LUT stage sitting in the list to delete once a real one's added.

    def add_lut_stage_from_path(self, path, index=None, branch=_UNSPECIFIED_BRANCH):
        """Public entry point -- used by MainWindow to route Browser's
        file_selected signal here, and by _on_lut_path_dropped for a
        drag-and-drop from the Browser panel's file list (which supplies
        `index`/`branch`, the drop position already resolved; the plain
        file_selected/double-click path leaves both at their defaults --
        append at the end, auto-assign a branch if that lands inside an
        open fork region, same as always)."""
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
        self._add_stage(stage, index=index, branch=branch)

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

    def set_default_working_colourspace(self, name):
        """Project Settings' working color space -- applies to stages
        added from now on; existing stages keep whatever they were set
        to (each one's own Working Color Space combo still overrides).
        Also what rows' "in <space>" notes compare against."""
        self._default_working_colourspace = name
        if name != rows._project_working_space:
            rows._project_working_space = name
            self._refresh_list()

    def set_pipeline_summary(self, input_space, working_space, display_text):
        """The "Input -> Working -> Display" line above the stage list."""
        text = (f"Input: {input_space or 'working space'}  →  Working: {working_space}"
                f"  →  Display: {display_text}")
        if self.pipeline_label.text() != text:
            self.pipeline_label.setText(text)

    def _on_add_printer_lights_clicked(self):
        stage = st.make_printer_lights_stage(
            0, 0, 0, working_colourspace=self._default_working_colourspace)
        self._add_stage(stage)

    def _on_add_aces_drt_clicked(self):
        if not aces_drt.OCIO_AVAILABLE:
            return
        config_name = aces_drt.DEFAULT_CONFIG_NAME
        display, view = aces_drt.get_default_display_and_view(config_name)
        stage = st.make_aces_drt_stage(self._default_working_colourspace, display, view,
                                       config_name=config_name)
        self._add_stage(stage)

    def _on_add_custom_drt_clicked(self):
        if not aces_drt.OCIO_AVAILABLE:
            return
        # The project working space (ACEScct by default, the bundled
        # sample image's encoding). Getting this wrong doesn't error --
        # it silently reinterprets log-encoded values as linear (mean
        # output 0.40 -> 0.59 on the bundled image) -- which is why it's
        # a project-level choice now, not a per-stage hardcode.
        stage = st.make_custom_drt_stage(self._default_working_colourspace)
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

    def _on_add_grading_primary_clicked(self):
        if not gpr.OCIO_AVAILABLE:
            return
        stage = st.make_grading_primary_stage()
        stage.label = _format_grading_primary_label(stage.params)
        self._add_stage(stage)

    def _on_add_exposure_contrast_clicked(self):
        if not ec.OCIO_AVAILABLE:
            return
        stage = st.make_exposure_contrast_stage()
        stage.label = _format_exposure_contrast_label(stage.params)
        self._add_stage(stage)

    def _on_add_grading_tone_clicked(self):
        if not gtn.OCIO_AVAILABLE:
            return
        stage = st.make_grading_tone_stage()
        stage.label = _format_grading_tone_label(stage.params)
        self._add_stage(stage)

    def _on_add_grading_rgb_curve_clicked(self):
        if not grc.OCIO_AVAILABLE:
            return
        stage = st.make_grading_rgb_curve_stage()
        stage.label = _format_grading_rgb_curve_label(stage.params)
        self._add_stage(stage)

    def _on_add_color_warper_clicked(self):
        stage = st.make_color_warper_stage(working_colourspace=self._default_working_colourspace)
        self._add_stage(stage)

    def _on_add_keyer_clicked(self):
        stage = st.make_keyer_stage(working_colourspace=self._default_working_colourspace)
        self._add_stage(stage)

    def _on_add_matte_combine_clicked(self):
        stage = st.make_matte_combine_stage()
        self._add_stage(stage)

    def _on_add_matte_shape_clicked(self):
        stage = st.make_matte_shape_stage()
        self._add_stage(stage)

    def _on_add_fork_clicked(self):
        """Adds a fork_split/fork_merge pair, appended at the end as
        ordinary flat stages (per Tom's Stack Forking spec -- see
        stack.py's docstring, STACK FORKING). Nothing goes between them
        yet; add any stage kind the normal way, then DRAG it into either
        column of the two-column table that immediately appears between
        the two markers (see _flush_fork_grid -- a fork region always
        renders a real, droppable landing zone now, even empty) to place
        it inside this region -- which column you drop it in IS its
        branch, there's no separate control for it. Both markers are
        added in one go (not via _add_stage twice, which would each
        emit/refresh separately) so the list only rebuilds once and
        selection lands on the new Fork Split row."""
        split = st.make_fork_split_stage()
        merge = st.make_fork_merge_stage()
        merge.label = _format_fork_merge_label(merge.params)
        self.stack.add_stage(split)
        self.stack.add_stage(merge)
        self._refresh_list(keep_selection=split.id)
        self._emit_changed()

    def _add_stage(self, stage, index=None, branch=_UNSPECIFIED_BRANCH):
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

        `branch` left at its _UNSPECIFIED_BRANCH sentinel default (every
        caller with no column information -- the "Add Operator" menu)
        falls through to _maybe_auto_assign_fork_branch, which now ALWAYS
        picks a real Branch A/B if the final position lands inside an
        open fork region -- never leaves it unassigned, same "never in
        neither branch" invariant the drag-and-drop paths already
        enforce structurally (see that method's own docstring for the
        priority it uses). A caller that already resolved a real branch
        value from a drop position (_on_lut_path_dropped, via
        add_lut_stage_from_path) passes it explicitly instead, bypassing
        auto-assignment entirely -- including passing `None` explicitly
        for an ordinary, non-fork drop, which auto-assignment would
        otherwise never produce on its own (it only ever sets "a"/"b",
        never clears an existing tag)."""
        # Captured BEFORE computing index -- _default_insert_index()
        # derives index from this exact same selection, and
        # _maybe_auto_assign_fork_branch wants to know what was actually
        # selected (not just the resulting index) to prefer "continue
        # this stage's own branch" over a pure empty-side guess.
        selected_stage = self._selected_stage()
        if index is None:
            index = self._default_insert_index()
        if branch is _UNSPECIFIED_BRANCH:
            self._maybe_auto_assign_fork_branch(stage, index, selected_stage)
        else:
            stage.branch = branch
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

    def _maybe_auto_assign_fork_branch(self, stage, index, selected_stage=None):
        """If `index` (a gap position in self.stack.stages BEFORE `stage`
        is inserted) falls strictly inside an open fork_split/fork_merge
        region, auto-assign `stage`'s branch -- NEVER leaves it
        unassigned (round two, per Tom: adding an operator with the
        fork_split marker selected, or with a branch-interior stage
        selected, was landing the new stage orphaned -- "not in either
        branch" -- whenever the OTHER branch also happened to be
        non-empty, since the original heuristic only ever looked at
        emptiness, never at what was actually selected). Priority:

        1. `selected_stage` (whichever stage _default_insert_index
           actually derived `index` from) is itself already branch-
           tagged -> continue THAT SAME branch. This is the common case
           Tom described: a branch-interior stage is selected, "add
           operator" should obviously join it in the same branch it's
           spawning under, regardless of whether the other branch also
           has content.
        2. Otherwise (selected_stage is fork_split itself, or nothing
           useful to key off of): one side empty, other populated -> the
           EMPTY side, same as before.
        3. Both sides empty -> Branch A, same as before.
        4. Both sides already populated AND no branch context from
           selection (e.g. fork_split itself was selected, or nothing
           was selected at all) -> ALSO Branch A now, closing the one
           remaining gap where this used to leave a stage orphaned --
           the user can still drag it into Branch B afterward if that's
           not what they wanted, same as any other stage.

        A plain stage being added is never itself a fork_split/
        fork_merge, so there's no need to guard against that here.

        Only reached from _add_stage (the "Add Operator" menu path,
        which has no column information) -- the drag-and-drop insert
        paths (_on_stage_reordered/_on_lut_path_dropped) resolve their
        own branch directly from drop position instead and never call
        this."""
        if index is None:
            return
        region = st.find_enclosing_fork_region(self.stack.stages, index)
        if region is None:
            return
        if selected_stage is not None and selected_stage.branch in ("a", "b"):
            stage.branch = selected_stage.branch
            return
        split_index, merge_index = region
        counts = st.fork_region_branch_counts(self.stack.stages, split_index, merge_index)
        if counts["a"] == 0:
            stage.branch = "a"
        elif counts["b"] == 0:
            stage.branch = "b"
        else:
            stage.branch = "a"  # both already populated, no branch context to key off -- never leave it unassigned

    # ---- inspector: empty ----------------------------------------------

    def _wrap_inspector_page(self, page):
        # QStackedWidget force-resizes its CURRENT page to its own full
        # geometry regardless of that page's sizeHint (confirmed via a
        # live repro: a QGroupBox with sizeHint (74, 57) was resized to
        # fill a 400x600 stack when shown directly). Adding a bordered
        # QGroupBox straight to the stack means the leftover space still
        # ends up INSIDE the box's visible border -- reads as "one big
        # empty box" rather than genuinely blank space below a compact
        # panel. Wrapping the page in a plain, borderless container with
        # its own trailing stretch means the WRAPPER (not the bordered
        # page) is what gets force-stretched, so the box itself sizes to
        # its natural content height and the leftover space lands
        # outside/below it instead.
        wrapper = QWidget()
        wrapper_layout = QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.addWidget(page)
        wrapper_layout.addStretch(1)
        return wrapper

    # ---- inspector: fork_split stage ------------------------------------

    def _build_fork_split_inspector(self):
        """Nothing to configure on a fork_split stage itself -- it's a
        pure marker (see stack.py's docstring, STACK FORKING). This
        inspector is informational only, same "select a stage to see its
        settings" spirit as _build_empty_inspector, just explaining what
        the marker does and pointing at the row-level controls (branch
        combo, enabled toggle) that actually matter for it."""
        group = QGroupBox("Fork Split")
        outer = QVBoxLayout(group)
        info = QLabel(
            "Opens two parallel branches from here. Add any stage kind "
            "after this one and use that row's own Branch A / Branch B "
            "dropdown (in the stage list) to place it inside this fork -- "
            "as many stages per branch as you like. Close the fork with a "
            "matching Fork Merge stage below.\n\n"
            "Disabling this stage (the row's enabled-toggle dot) treats "
            "the WHOLE fork region -- both branches and the merge -- as a "
            "no-op, same as if none of it were here."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {HINT_TEXT_HEX};")
        outer.addWidget(info)
        # See _build_printer_lights_inspector's own comment on why every
        # inspector page needs a trailing vertical stretch -- most
        # visible of all here, since this page is just one label.
        outer.addStretch(1)
        self.inspector_stack.addWidget(self._wrap_inspector_page(group))
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
        group = QGroupBox("Fork Merge")
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

        # See _build_printer_lights_inspector's own comment on why every
        # inspector page needs a trailing vertical stretch.
        outer.addStretch(1)

        self.inspector_stack.addWidget(self._wrap_inspector_page(group))
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
        self._update_row_content(stage.id)
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

        refresh = {
            "lut": self._refresh_lut_inspector,
            "printer_lights": self._refresh_printer_lights_inspector,
            "aces_drt": self._refresh_aces_drt_inspector,
            "custom_drt": self._refresh_custom_drt_inspector,
            "cdl": self._refresh_cdl_inspector,
            "colorspace": self._refresh_colorspace_inspector,
            "grading_primary": self._refresh_grading_primary_inspector,
            "exposure_contrast": self._refresh_exposure_contrast_inspector,
            "grading_tone": self._refresh_grading_tone_inspector,
            "grading_rgb_curve": self._refresh_grading_rgb_curve_inspector,
            "color_warper": self._refresh_color_warper_inspector,
            "keyer": self._refresh_keyer_inspector,
            "matte_combine": self._refresh_matte_combine_inspector,
            "matte_shape": self._refresh_matte_shape_inspector,
            "fork_split": self._refresh_fork_split_inspector,
            "fork_merge": self._refresh_fork_merge_inspector,
        }.get(stage.kind)
        if refresh is not None:
            refresh(stage)

    def _refresh_fork_split_inspector(self, stage):
        self.inspector_stack.setCurrentIndex(self._fork_split_inspector_index)

    def _refresh_fork_merge_inspector(self, stage):
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

    def set_locked_for_playback(self, locked):
        """Locks/unlocks BOTH of this panel's actual on-screen widgets
        (list_widget_area, params_widget_area -- see this class's own
        docstring on why there are two) for the duration of realtime
        playback (video or Timeline, see main_window.py's own wiring of
        VideoPlaybackController/TimelineController's playback_started/
        playback_stopped to this method).

        StackPanel itself is a QObject, not a QWidget (see this class's
        own docstring) -- it has no setEnabled of its own. A previous
        version of this lock called self.stack_panel.setEnabled(...)
        directly from MainWindow, which raised AttributeError every time
        playback started (silently swallowed by Qt's slot dispatch, so
        the app never crashed -- it just never actually locked anything,
        undetected until Timeline playback's own end-to-end test caught
        it). Locking both real widgets here, once, is what actually
        works, and keeps MainWindow from needing to know StackPanel's
        internal two-widget split at all."""
        self.list_widget_area.setEnabled(not locked)
        self.params_widget_area.setEnabled(not locked)

    def get_sequence_stack(self):
        """The Sequence stack specifically, regardless of which one is
        currently loaded into the editor (see set_stack's own docstring on
        why self.stack and self._sequence_stack can diverge while Shot
        mode is active) -- MainWindow's session save uses this (never
        get_stack()) so a save made mid-Shot-edit still persists the
        Sequence grade, not whatever Timeline clip happened to be on
        screen at the time (that clip's own grade is separately embedded
        via timeline_to_session_dict already)."""
        return self._sequence_stack

    def is_shot_mode_active(self):
        """True while a Timeline clip's own Stack (not Sequence's) is
        loaded into this editor -- MainWindow uses this to disable Undo/
        Redo while it's active (see activate_shot_mode's own docstring on
        why per-clip edits are deliberately NOT undo-tracked at all yet)."""
        return self._shot_mode_active

    def set_selected_clip_getter(self, getter):
        """Zero-arg callable returning the Timeline's own currently
        selected clip (duck-typed: only .stack/.name are read here, via
        activate_shot_mode/_update_shot_button_label -- this module never
        imports timeline.py) or None -- wired from MainWindow to
        TimelinePanel's own selection state, same pull-based convention as
        set_eyedropper_requester. Used by _on_shot_mode_button_clicked to
        find out which clip's Stack to request when the user clicks
        "Shot.\""""
        self._selected_clip_getter = getter

    def notify_selected_clip_changed(self, clip):
        """Called by MainWindow whenever TimelinePanel.clip_selected fires
        -- `clip` is the newly selected TimelineClip, or None. PUSH rather
        than pull (unlike set_selected_clip_getter above) because a
        selection change must actively re-point Shot mode's own target
        the instant it happens, not just be readable on demand next time
        someone asks. MainWindow wraps this call in _restoring_stack_
        history (same as it wraps activate_sequence_mode/activate_shot_
        mode below), so re-pointing to a different clip -- or falling
        back to Sequence if the selection was cleared -- never itself
        creates a spurious undo entry.

        Always re-labels/enables the Shot button for the new selection.
        If Shot mode is already active, per Tom ("selecting a different
        clip while already in shot mode re-points the toggle"), also
        re-points the editor at the newly selected clip's own Stack --
        or, if the selection was cleared entirely (nothing left to point
        Shot mode at), falls back to Sequence automatically."""
        self._update_shot_button_label(clip)
        if not self._shot_mode_active:
            return
        if clip is None:
            self.activate_sequence_mode()
        else:
            self.activate_shot_mode(clip)

    def _update_shot_button_label(self, clip):
        self.shot_mode_button.setEnabled(clip is not None)
        self.shot_mode_button.setText(f"Shot: {clip.name}" if clip is not None else "Shot")

    def _on_shot_mode_button_clicked(self):
        """Only ever REQUESTS the switch (shot_mode_requested) -- the
        actual activate_shot_mode call happens in MainWindow, wrapped in
        _restoring_stack_history, same reasoning as sequence_mode_
        requested above (see that signal's own docstring)."""
        clip = self._selected_clip_getter() if self._selected_clip_getter else None
        if clip is None:
            # Shouldn't happen -- the button is disabled with no selection
            # (_update_shot_button_label) -- but leaves Sequence checked
            # rather than landing on a checked-but-inert Shot button.
            self.sequence_mode_button.setChecked(True)
            return
        self.shot_mode_requested.emit(clip)

    def activate_sequence_mode(self):
        """Points this editor back at the Sequence stack -- called by
        MainWindow in response to sequence_mode_requested (the user
        clicked "Sequence") or notify_selected_clip_changed (the Timeline
        selection was cleared while Shot mode was active). A no-op if
        Sequence is already active (a redundant click, or a redundant
        clip-deselect notification) -- reloading the exact same stack
        object that's already on screen would needlessly clear whatever
        stage row is currently selected in the inspector for nothing."""
        if not self._shot_mode_active:
            return
        self._shot_mode_active = False
        self.sequence_mode_button.setChecked(True)
        self._load_stack_into_editor(self._sequence_stack)

    def activate_shot_mode(self, clip):
        """Points this editor directly at `clip`'s own live Stack object
        (per Tom: no separate save-back step -- edits mutate it in place,
        same as timeline.py's own docstring on why a TimelineClip's Stack
        is live rather than a StackMemory-style snapshot). Called by
        MainWindow in response to shot_mode_requested (the user clicked
        "Shot") or notify_selected_clip_changed (a different clip got
        selected while already in Shot mode) -- a no-op if `clip` is
        already the one on screen, same "don't needlessly clear the
        inspector selection" reasoning as activate_sequence_mode above.

        Undo tracking for ONGOING edits made while Shot mode is active is
        separately, deliberately skipped entirely (see MainWindow's
        _on_stack_changed_for_history, gated on is_shot_mode_active) --
        per-clip undo is an explicit fast-follow, not built yet (see the
        approved Timeline plan's own scope note)."""
        if self._shot_mode_active and self.stack is clip.stack:
            return
        self._shot_mode_active = True
        self.shot_mode_button.setChecked(True)
        self._load_stack_into_editor(clip.stack)

    def get_highlight_stage_id(self):
        """The Stage.id currently Highlight-toggled (see this class's
        own docstring on self._highlight_stage_id), or None --
        SampleImagePanel reads this each render via
        set_highlight_getter, mirroring get_stack's own pull-based
        convention just above. Deliberately NOT part of Stage.params or
        session_io.py's own persisted fields -- this is a transient
        viewing preference, not something a saved session should
        remember was "on.\""""
        return self._highlight_stage_id

    def set_eyedropper_requester(self, requester):
        """requester: a callable taking (stage_id, callback) that arms
        the Viewer's own eyedropper for THAT stage's own actual input --
        SampleImagePanel.pick_pixel_color_for_stage, wired in from
        main_window.py alongside set_stack_getter/set_highlight_getter.
        Unlike those two, this one is PUSH rather than PULL: StackPanel
        can't reach the Viewer's WipeCompareWidget directly (panels only
        ever talk through main_window.py), so this stores a way to ASK
        it to arm itself instead of a value to read. None (never wired,
        or in a standalone/test StackPanel) makes _on_ky_pick_clicked a
        no-op -- same defensive convention every other cross-panel
        getter/callback in this class already uses."""
        self._eyedropper_requester = requester

    def get_baked_lut(self, settings=st.DEFAULT_BAKE_SETTINGS, name=None):
        """Bake the current stack into a flat colour.LUT3D at `settings`
        (stack.BakeSettings) -- the Graph panel's live preview and export
        both go through here. Passes self._bake_cache so repeated preview
        bakes only recompute the stages that changed since the last call;
        the cache keys on `settings` too, so an export at a different
        size/domain recomputes from scratch rather than reusing preview
        checkpoints."""
        return st.bake_stack_to_lut3d(self.stack, settings, name=name, cache=self._bake_cache)

    def set_bake_error(self, message):
        """Shows/hides the inline warning below the stage list (see
        _build_list_ui) -- `message` a string to show it, None/"" to hide
        it. Called by MainWindow's _on_stack_changed with the ValueError
        stack.check_stack_structure raises for a structurally invalid
        fork region (unmatched fork_split/fork_merge, nested forks, a
        branch-region stage missing its tag -- see stack.py's docstring,
        STACK FORKING), or a preview bake's own failure."""
        if message:
            self.bake_error_label.setText(f"⚠ {message}")
            self.bake_error_label.show()
        else:
            self.bake_error_label.clear()
            self.bake_error_label.hide()

    def set_stack(self, new_stack):
        """Replace the SEQUENCE Stack wholesale -- used by MainWindow for
        session load, undo/redo, Stack Memory recall, CDL/LUT apply, etc.
        (see session_io.py and this class's various apply_*/insert_*
        methods). Always updates self._sequence_stack (get_sequence_stack's
        own source of truth) -- but only actually loads it into the editor
        (rebuild list, Grid Size/Domain readout, emit stack_changed) when
        Shot mode ISN'T active: while a Timeline clip's own Stack is on
        screen (see activate_shot_mode), an incoming Sequence replacement
        is remembered for whenever the user next toggles back to Sequence,
        rather than yanking the clip they're actively editing off screen.
        Clears the current selection either way it does load (nothing in
        the new stack corresponds to whatever row was selected before)."""
        self._sequence_stack = new_stack
        if not self._shot_mode_active:
            self._load_stack_into_editor(new_stack)

    def _load_stack_into_editor(self, stack_to_show):
        self.stack = stack_to_show
        # The incoming Stack's stages are fresh Stage objects with their
        # own ids -- nothing in the old cache would fingerprint-match them
        # anyway, so this is purely about releasing that memory right now
        # rather than one block at a time as edits happen to touch it.
        self._bake_cache.clear()
        self._refresh_list()
        self._emit_changed()

    def apply_cdl_entry(self, entry):
        """Bring a cdl_library.CDLEntry back into the Stack -- the single
        entry point StackMemoriesPanel's "Apply to Stack" action (via
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

    def insert_entry_in_stack(self, entry):
        """"Insert in Stack" (per Tom: "adds all of a saved stack's
        operators (or a cdl) into the existing stack below the selected
        entry") -- the Gallery's own right-click action for a
        stack_memory.StackMemory or a cdl_library.CDLEntry row, routed
        here by MainWindow same as apply_cdl_entry/recall_requested
        above. Deliberately DIFFERENT from both of those: Recall REPLACES
        the whole working stack, Apply CDL to Stack can overwrite an
        already-selected 'cdl' stage in place -- this always ADDS new
        stage(s), never replaces or overwrites anything already in the
        stack, landing immediately after whichever stage is currently
        selected (see _add_stage/_default_insert_index, the same "insert
        here" placement every other add entry point already gets) and
        selected in turn moving to the LAST stage inserted.

        A StackMemory inserts every one of its stages, in order -- each
        one calling _add_stage in turn means stage N+1 lands right after
        stage N (since _add_stage's own default-insert-index tracks
        whatever's currently selected, and each call re-selects the
        stage it just added). Rebuilt as fresh Stage objects (kind/label/
        params/enabled/opacity/branch copied, but a NEW id via
        st.next_stage_id()) rather than reusing entry.stack.stages
        directly -- those keep the SAME id every time the memory itself
        is copied (see stack.Stage.copy()'s own docstring on why that's
        right for Recall's non-destructive semantics), which would
        collide with an already-live stage's id (or with itself, if this
        same memory gets inserted a second time) since this ADDS onto an
        existing stack rather than replacing it outright. `branch` is
        passed through explicitly (not left for _add_stage's own auto-
        assignment) so a saved fork region's own A/B tags survive the
        insert exactly as saved, rather than being re-guessed.

        A CDLEntry inserts one new 'cdl' stage, built the identical way
        apply_cdl_entry's own "nothing cdl-shaped selected" branch does
        -- just always taking that branch, never the overwrite-in-place
        one, since Insert is unconditionally an ADD."""
        if isinstance(entry, StackMemory):
            for stage in entry.stack.stages:
                new_stage = stage.copy()
                new_stage.id = st.next_stage_id()
                self._add_stage(new_stage, branch=stage.branch)
        elif isinstance(entry, CDLEntry):
            new_stage = st.make_cdl_stage(
                slope=entry.slope, offset=entry.offset, power=entry.power, sat=entry.sat,
                style=entry.style, sop_enabled=entry.sop_enabled, sat_enabled=entry.sat_enabled,
            )
            new_stage.label = _format_cdl_label(new_stage.params)
            self._add_stage(new_stage)

    def _emit_changed(self):
        self.stack_changed.emit()
