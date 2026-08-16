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
from collections import namedtuple

import numpy as np
import shiboken6
from PySide6.QtCore import Qt, QObject, QSize, QPoint, Signal, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QPainter, QPen, QDrag, QPixmap, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLabel, QMessageBox, QComboBox, QSpinBox, QDoubleSpinBox,
    QGroupBox, QSlider, QCheckBox, QStackedWidget, QInputDialog,
    QDialog, QDialogButtonBox, QFrame, QToolButton, QAbstractItemView, QMenu,
    QSizePolicy, QButtonGroup, QGraphicsOpacityEffect,
)

import stack as st
import aces_drt
import cdl
import colorspace_convert as csc
import custom_drt
import exposure_contrast as ec
import grading_primary as gpr
import grading_rgb_curve as grc
import grading_tone as gtn
import color_balance as cb
import color_warper as cw
import keyer as ky
from color_wheel_widget import ColorBalanceWheel
from color_warper_wheel import WarpierWheel
from color_warper_cloud_widget import WarpierCloudWidget
from curve_editor_widget import CurveEditorWidget, CHANNEL_ORDER, CHANNEL_COLORS, CHANNEL_LABELS
from app_logic import load_lut_any_format, list_available_colourspaces
from printer_lights import POINTS_PER_STOP, DEFAULT_WORKING_COLOURSPACE
import hover_inspector
from spinbox_scrub import enable_drag_to_scrub
from browser_panel import LUT_PATH_MIME
from floating_dialog import make_floating_tool_window, show_floating
from stack_memory import StackMemory
from cdl_library import CDLEntry
# style.HINT_TEXT_HEX -- see style.py's FOURTEENTH PASS docstring: hint
# text is a literal hex now, not palette(placeholder-text) (doesn't
# reliably survive Qt's style-polish pass on this Qt build).
from style import HINT_TEXT_HEX, PANEL_BORDER_RADIUS

COMMON_SIZES = [9, 17, 25, 33, 45, 65, 97]
PRINTER_LIGHT_RANGE = 50

# One entry per _StackListWidget row (see StackPanel._row_insert_index,
# built fresh in _refresh_list) -- where a dropped/dragged stage should
# land if it's dropped at this row's position. `ordinary` is a plain
# self.stack.stages insertion index, used for a row OUTSIDE any fork
# region (an int) or None if this row is fork-interior. `branch_a`/
# `branch_b` are each a self.stack.stages insertion index for landing in
# that branch specifically, or None if this row is NOT fork-interior.
# Exactly one of (`ordinary`, the pair `branch_a`/`branch_b`) is
# populated for any given row -- never both, never neither -- which is
# what makes "drop resolves to no branch" structurally unreachable for
# any row inside a fork's two-column table (see _StackListWidget.
# _drop_branch/StackPanel._on_stage_reordered). Replaces the single
# plain int _row_insert_index held per row before forking became
# drag-native (2026-08-12).
_RowTargets = namedtuple("_RowTargets", ["ordinary", "branch_a", "branch_b"])

# Sentinel default for StackPanel._add_stage's `branch` param -- lets a
# caller distinguish "didn't specify, auto-assign a branch if this lands
# inside an open fork region" (every "Add Operator" menu action, which
# has no column information) from "explicitly wants None" (a drag-and-
# drop insert that resolved to an ordinary, non-fork position -- see
# StackPanel._resolve_drop_target). Plain `None` can't serve as that
# "unspecified" default since it's also the legitimate explicit value
# for "no branch."
_UNSPECIFIED_BRANCH = object()

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
CDL_SOP_FIELD_DEFAULTS = {fk: default for fk, _l, _lo, _hi, default, _s, _st in CDL_SOP_FIELD_SPECS}

# Which of GradingPrimary's RGBM fields are actually meaningful per style
# (see grading_primary.py's own docstring) -- _build_grading_primary_
# inspector builds a row for every field below, but only these three (per
# style) are ever shown at once, toggled by _update_grading_primary_field_
# visibility on style change. Pivot and Saturation are always shown --
# both are meaningful (or at least harmless) regardless of style.
GRADING_PRIMARY_STYLE_FIELDS = {
    "log": ("brightness", "contrast", "gamma"),
    "lin": ("offset", "exposure", "contrast"),
    "video": ("lift", "gamma", "gain"),
}
# (field_key, label, is_additive) -- additive fields (identity 0.0, e.g.
# brightness) get a different range/step than multiplicative ones
# (identity 1.0, e.g. contrast/gamma/gain) -- same "own spin range around
# identity" reasoning as CDL_SOP_FIELD_SPECS above. UNLIKE CDL, these
# fields' R/G/B/M spinboxes now ALSO get a color-balance wheel (per Tom:
# "adding ... color wheels to operators that dont have them") -- but
# GradingRGBM's 4th "master" (M) component is a REAL, independently
# stored value here, unlike CDL's wheel-computed master, which is a
# UI-only convenience derived from mean(R,G,B) and never itself stored
# (see grading_primary.py's docstring, and color_balance.
# wheel_from_rgb_given_master's own docstring for why that difference
# means this wheel's sync math can't just reuse CDL's rgb_to_master_and_
# wheel unchanged -- see _on_gpr_wheel_changed/_on_gpr_master_changed/
# _on_gpr_channel_spin_changed below). One shared strength for every
# field (unlike CDL_SOP_FIELD_SPECS' per-field column) -- simplicity over
# per-field tuning, matching how every field here already shares just two
# ranges (additive/multiplicative) rather than its own bespoke one.
GRADING_WHEEL_STRENGTH = 1.0
GRADING_PRIMARY_WHEEL_DIAMETER = 90
GRADING_PRIMARY_FIELD_SPECS = [
    ("brightness", "Brightness", True),
    ("contrast", "Contrast", False),
    ("gamma", "Gamma", False),
    ("offset", "Offset", True),
    ("exposure", "Exposure", True),
    ("lift", "Lift", True),
    ("gain", "Gain", False),
]
GRADING_PRIMARY_FIELD_DEFAULTS = {
    fk: (0.0 if is_additive else 1.0) for fk, _l, is_additive in GRADING_PRIMARY_FIELD_SPECS}
GRADING_PRIMARY_ADDITIVE_RANGE = (-2.0, 2.0, 0.01)  # lo, hi, step
# Floor is 0.01, not 0.0 -- ocio.GradingPrimary.validate() requires
# contrast/gamma to stay strictly positive (confirmed via probing, see
# grading_primary.py's own _POSITIVE_FIELD_FLOOR comment); gain doesn't
# strictly need this floor but shares this range constant for simplicity.
GRADING_PRIMARY_MULT_RANGE = (0.01, 4.0, 0.01)
GRADING_TONE_WHEEL_DIAMETER = 60  # smaller than CDL/Grading Primary's 90 --
# 5 ranges here (vs CDL's 3, Grading Primary's 3-visible-at-once) means 5
# wheels stacked vertically at 90px each would meaningfully bloat this
# already-the-largest inspector (see _build_grading_tone_inspector's own
# docstring on "largest of the four Tier 1 inspectors"); 60px keeps the
# per-range row compact while still being genuinely drag-usable.


def _format_grading_primary_label(params):
    """Stage-row label for a 'grading_primary' stage -- shows the active
    style (same "the row tells you what it actually does" convention
    _format_cdl_label follows), not a static "Grading Primary". Doesn't
    dump every RGBM field's values the way CDL's label does -- there are
    7 possible fields here vs. CDL's fixed 3, so a full dump would be
    unreadable; the style name plus the inspector (which shows only the
    fields that style actually uses) is enough context."""
    style = params.get("style", gpr.DEFAULT_STYLE)
    style_label = dict(gpr.list_available_styles()).get(style, style)
    # Style labels are long/descriptive (good for a combo box); the row
    # label just wants the short name before the first " (".
    short_style = style_label.split(" (")[0]
    return f"Grading Primary ({short_style})"


def _format_exposure_contrast_label(params):
    """Stage-row label for an 'exposure_contrast' stage -- shows its live
    exposure/contrast/gamma values (same "the row tells you what it
    actually does" convention _format_cdl_label/_format_grading_primary_
    label follow) -- only 3 scalar fields here, so (unlike grading_
    primary's style-only label) a full value dump fits comfortably."""
    exposure = params.get("exposure", ec.IDENTITY_EXPOSURE)
    contrast = params.get("contrast", ec.IDENTITY_CONTRAST)
    gamma = params.get("gamma", ec.IDENTITY_GAMMA)
    return f"Exposure/Contrast  Exp {exposure:+.2f}  Con {contrast:.2f}  Gam {gamma:.2f}"


# Human-readable labels for grading_tone.RANGE_KEYS, in display order
# (matches OCIO's own blacks->shadows->midtones->highlights->whites
# ordering, dark to light).
GRADING_TONE_RANGE_LABELS = {
    "blacks": "Blacks", "shadows": "Shadows", "midtones": "Midtones",
    "highlights": "Highlights", "whites": "Whites",
}


def _format_grading_tone_label(params):
    """Stage-row label for a 'grading_tone' stage -- shows the active
    style only (same reasoning as _format_grading_primary_label: 5
    ranges x 6 fields is far too much to dump into one row label, the
    inspector is where the actual values live)."""
    style = params.get("style", gtn.DEFAULT_STYLE)
    style_label = dict(gtn.list_available_styles()).get(style, style)
    short_style = style_label.split(" (")[0]
    return f"Grading Tone ({short_style})"


def _format_grading_rgb_curve_label(params):
    """Stage-row label for a 'grading_rgb_curve' stage -- shows the
    active style only (same reasoning as _format_grading_primary_label/
    _format_grading_tone_label: 4 curves x an arbitrary number of control
    points each is far too much to dump into one row label, the inspector
    -- the curve editor itself -- is where the actual shape lives)."""
    style = params.get("style", grc.DEFAULT_STYLE)
    style_label = dict(grc.list_available_styles()).get(style, style)
    short_style = style_label.split(" (")[0]
    return f"Grading RGB Curve ({short_style})"


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


# Short, fixed-width badge text per stage.kind -- a quick-scan visual cue
# in each row (per Tom, "snappier and more interactive" pass) so "what's
# in my stack" is readable at a glance without parsing every label's
# prose. Plain monospace-friendly abbreviations rather than pictographic
# icons -- this app has no icon/image asset pipeline anywhere else (every
# existing per-purpose glyph, the ☰ menu button, the ●/○ enabled toggle,
# the × delete button, is a plain Unicode character styled through QSS),
# so a text badge is the one approach consistent with that. Falls back to
# the kind string itself (upper, truncated) for anything not listed here
# rather than showing nothing.
STAGE_KIND_BADGES = {
    "lut": "LUT",
    "printer_lights": "PL",
    "aces_drt": "DRT",
    "custom_drt": "CDR",
    "cdl": "CDL",
    "colorspace": "CS",
    "grading_primary": "GP",
    "exposure_contrast": "EC",
    "grading_tone": "GT",
    "grading_rgb_curve": "RGB",
    "color_warper": "WRP",
    "keyer": "KEY",
    "fork_split": "FRK",
    "fork_merge": "MRG",
}


def _stage_kind_badge_text(kind):
    return STAGE_KIND_BADGES.get(kind, kind[:3].upper())


def _matte_options_by_index(stages, letters):
    """options[i] -- every MATTE_PRODUCING_KINDS stage (currently
    "keyer"/"segment_mask") at a position STRICTLY BEFORE index i in
    `stages`, as (letter, stage_id) pairs in stack order (see stack.py's
    docstring, "MATTE CHANNELS"). `letters` is the SAME dict
    stack.matte_letters_for(stages) already produced for this same
    stages list -- passed in rather than recomputed here so
    _refresh_list only calls that function once per rebuild. Computed
    once per _refresh_list() call rather than per row: each row's own
    "which matte channels can I reference" only depends on its OWN
    original position in the flat stages list, so a single upfront pass
    lets every row -- including ones rendered inside a fork region's
    two-column table, where visual row order and original stack-index
    order can differ (see _flush_fork_grid's own docstring on why cells
    are paired by POSITION WITHIN a branch, not by original index) --
    look its own options up in O(1) by that original index, with no
    fork-aware bookkeeping needed here at all."""
    options = []
    seen_so_far = []
    for stage in stages:
        options.append(list(seen_so_far))
        if stage.kind in st.MATTE_PRODUCING_KINDS:
            seen_so_far.append((letters[stage.id], stage.id))
    return options


# Stack()'s own constructor defaults, read once rather than hardcoded here
# a second time -- used by _update_domain_readout to flag when the current
# grid size/domain differ from a freshly-created stack's, so it's obvious
# at a glance whether either has actually been touched.
_STACK_DEFAULTS = st.Stack()


def _stage_info_text(stage):
    """Alt+hover info card content (see hover_inspector.py) for one
    Stage -- a per-kind summary of the params the Parameters panel would
    otherwise make you select the stage and look at. Deliberately not
    exhaustive for every kind's every param (e.g. skips config_name when
    it's just the default) -- enough to answer "what is this stage
    actually doing" at a glance, not a full dump."""
    p = stage.params
    lines = [stage.label, f"[{stage.kind}]"]
    if not stage.enabled:
        lines.append("Disabled")
    if stage.opacity < 1.0:
        lines.append(f"Opacity: {stage.opacity:.0%}")
    if stage.branch:
        lines.append(f"Branch: {stage.branch.upper()}")

    if stage.kind == "lut":
        source_kind = p.get("source_kind")
        if source_kind == "file":
            lines.append(f"Source: {p.get('source_path') or '?'}")
        elif source_kind == "identity":
            lines.append(f"Identity, size {p.get('identity_size', '?')}")
        elif source_kind == "embedded":
            lines.append("Source: LUT Library (embedded)")
        retarget_from, retarget_to = p.get("retarget_from"), p.get("retarget_to")
        if retarget_from is not None and retarget_to is not None:
            lines.append(f"Retarget: {retarget_from} -> {retarget_to}")
    elif stage.kind == "printer_lights":
        lines.append(
            f"R {p.get('r_points', 0.0):+.1f}  G {p.get('g_points', 0.0):+.1f}  "
            f"B {p.get('b_points', 0.0):+.1f} points")
        lines.append(f"Working space: {p.get('working_colourspace', '?')}")
    elif stage.kind == "aces_drt":
        lines.append(f"{p.get('working_colourspace', '?')} -> {p.get('display', '?')}/{p.get('view', '?')}")
    elif stage.kind == "custom_drt":
        mastering_display = p.get("mastering_display", custom_drt.DEFAULT_MASTERING_DISPLAY)
        final_target = p.get(
            "final_target", custom_drt.DEFAULT_FINAL_TARGET_FOR_DISPLAY[mastering_display])
        lines.append(f"{p.get('working_colourspace', '?')} -> {mastering_display} -> {final_target}")
        if mastering_display == "HDR":
            lines.append(
                f"PQ, mastering {p.get('mastering_brightness', custom_drt.DEFAULT_MASTERING_BRIGHTNESS)} nits")
        elif mastering_display == "SDR":
            lines.append("Gamma 2.4" if final_target != "sRGB" else "sRGB")
        else:
            lines.append("Gamma 2.6")
        lines.append(
            f"Contrast {p.get('contrast', custom_drt.DEFAULT_CONTRAST):.2f}  "
            f"Headroom {p.get('headroom', custom_drt.DEFAULT_HEADROOM):.1f} stops")
        lines.append(
            f"Identity zone {p.get('identity_zone', custom_drt.DEFAULT_IDENTITY_ZONE):.0%}  "
            f"Transition {p.get('transition_softness', custom_drt.DEFAULT_TRANSITION_SOFTNESS):.0%}")
        lines.append(
            f"Fullness R{p.get('fullness_r', custom_drt.DEFAULT_FULLNESS_R):.2f} "
            f"G{p.get('fullness_g', custom_drt.DEFAULT_FULLNESS_G):.2f} "
            f"B{p.get('fullness_b', custom_drt.DEFAULT_FULLNESS_B):.2f}")
    elif stage.kind == "cdl":
        r, g, b = p.get("slope", cdl.IDENTITY_SLOPE)
        ro, go, bo = p.get("offset", cdl.IDENTITY_OFFSET)
        rp, gp, bp = p.get("power", cdl.IDENTITY_POWER)
        lines.append(f"S({r:.3f},{g:.3f},{b:.3f}) O({ro:.3f},{go:.3f},{bo:.3f}) P({rp:.3f},{gp:.3f},{bp:.3f})")
        lines.append(f"Sat {p.get('sat', cdl.IDENTITY_SAT):.3f}  Style: {p.get('style', cdl.DEFAULT_STYLE)}")
        if not p.get("sop_enabled", True):
            lines.append("SOP: bypassed")
        if not p.get("sat_enabled", True):
            lines.append("SAT: bypassed")
    elif stage.kind == "colorspace":
        lines.append(f"{p.get('src', '?')} -> {p.get('dst', '?')}")
    elif stage.kind == "grading_primary":
        style = p.get("style", gpr.DEFAULT_STYLE)
        lines.append(f"Style: {style}")
        for field_key in GRADING_PRIMARY_STYLE_FIELDS.get(style, ()):
            default = gpr.IDENTITY_ADD if field_key in ("brightness", "offset", "lift") else gpr.IDENTITY_MULT
            r, g, b, m = p.get(field_key, default)
            label = dict((k, lbl) for k, lbl, _add in GRADING_PRIMARY_FIELD_SPECS)[field_key]
            lines.append(f"{label}: R{r:.3f} G{g:.3f} B{b:.3f} M{m:.3f}")
        lines.append(f"Pivot {p.get('pivot', gpr.DEFAULT_PIVOT):.3f}  "
                     f"Sat {p.get('saturation', gpr.DEFAULT_SATURATION):.3f}")
    elif stage.kind == "exposure_contrast":
        lines.append(f"Style: {p.get('style', ec.DEFAULT_STYLE)}")
        lines.append(
            f"Exposure {p.get('exposure', ec.IDENTITY_EXPOSURE):+.3f}  "
            f"Contrast {p.get('contrast', ec.IDENTITY_CONTRAST):.3f}  "
            f"Gamma {p.get('gamma', ec.IDENTITY_GAMMA):.3f}")
    elif stage.kind == "grading_tone":
        style = p.get("style", gtn.DEFAULT_STYLE)
        lines.append(f"Style: {style}")
        for range_key in gtn.RANGE_KEYS:
            r, g, b, m, start, width = p.get(range_key) or gtn.DEFAULT_RANGE_VALUES[range_key]
            lines.append(
                f"{GRADING_TONE_RANGE_LABELS[range_key]}: R{r:.2f} G{g:.2f} B{b:.2f} M{m:.2f}  "
                f"(start {start:.2f}, width {width:.2f})")
        lines.append(f"S-Contrast {p.get('scontrast', gtn.DEFAULT_SCONTRAST):.3f}")
    elif stage.kind == "grading_rgb_curve":
        style = p.get("style", grc.DEFAULT_STYLE)
        lines.append(f"Style: {style}")
        for channel in grc.RGB_CHANNELS:
            points = p.get(channel) or grc.DEFAULT_CONTROL_POINTS
            lines.append(f"{channel.capitalize()}: {len(points)} points")
    elif stage.kind == "fork_merge":
        lines.append(f"Blend: {p.get('blend', 0.5):.0%}")

    return "\n".join(lines)


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
    toggle, opacity spin, or delete button to StackPanel via
    on_row_clicked(stage.id).

    Delete button ("x" at the row's far end) removes THIS stage directly,
    no selection required first -- same "act on the row you clicked, not
    whatever happens to be selected" convention as the enabled toggle
    (fork_split/fork_merge's "x" removes BOTH markers together, see
    StackPanel._delete_stage_linked). Reordering is drag-and-drop (see
    _StackListWidget), removal is this button or Delete/Backspace on the
    selected row (see _StackListWidget.keyPressEvent).

    STACK FORKING (per Tom, REWORKED 2026-08-12): fork_split/fork_merge
    stages get this exact same card, no special styling -- a fork
    region's boundaries read as ordinary operator rows, not marker rows;
    the region itself is visually obvious from the two-column table
    between them (see StackPanel._flush_fork_grid/
    _ForkBranchGridRowWidget). `compact=True` is used for a cell INSIDE
    that two-column table -- tighter margins and an elided label (see
    _ElidingLabel, full text still in the tooltip), same as before, but
    there's no branch combo anymore (removed entirely, along with the
    stage-number prefix from an earlier round): a stage's branch is now
    purely a function of which column it's been dragged into -- see
    _StackListWidget._drop_branch/StackPanel._on_stage_reordered. Every
    _StageRowWidget, compact or not, is uniformly draggable now (no more
    ItemIsDragEnabled carve-out for fork-grid cells) via _StackListWidget
    resolving drag source/target by STAGE ID (_stage_id_at), not by
    QListWidget row, since a fork-grid row can hold two independently
    draggable stages, one per side.

    SNAPPIER/MORE INTERACTIVE PASS (per Tom, 2026-08-12, two rounds):

    1. update_content() -- a targeted in-place refresh (label text,
       opacity, enabled state) for a stage whose PARAMS changed live but
       whose position in the list/fork layout didn't. Added so
       StackPanel's _apply_*_live handlers (CDL, ACES DRT, Color Space,
       Grading Primary, Exposure/Contrast, Grading Tone, Grading RGB
       Curve, Fork Merge's blend), _on_row_enabled_clicked, and this
       row's own opacity_spin can update just THIS card instead of
       calling StackPanel._refresh_list(), which tears down and
       reconstructs every card in the whole list on every single tick of
       a drag -- the concrete, measured cause of "laggy" live editing
       found while building this. _refresh_list() itself is unchanged
       and still used for anything actually structural (add/remove/
       reorder/branch-region changes).
    2. An animated (~140ms) translucent dim overlay for the disabled
       state, painted in paintEvent instead of an instant QSS unpolish/
       polish snap -- see _disabled_anim/_on_disabled_progress. Selection
       does NOT get this treatment (round two, per Tom): a first pass
       gave it the same overlay-wash-plus-bold-label treatment, but Tom's
       call after trying it was that the highlight should be JUST the
       card border's own color changing to accent (see style.py's
       [stageSelected] rule) -- nothing else, so set_selected below is
       now a plain property+repolish, same mechanism as any other QSS
       property switch elsewhere in this app. Hover took three attempts
       (see style.py's own QFrame#stackStageRow:hover comment for the
       full account): a barely-visible neutral-gray brighten, then the
       app's own text-field convention (full accent, same as selected --
       too easy to confuse with actual selection), and finally its own
       dedicated color, palette(accent) -- see style.py's
       get_tertiary_color(), a 33%-darker derivative of the accent
       that's distinct from both idle-gray and selected-accent. WA_Hover
       set below is what makes the :hover QSS pseudo-state actually
       repaint on this QFrame.
    3. A kind badge (see STAGE_KIND_BADGES/_stage_kind_badge_text) next
       to the enabled toggle on EVERY card, compact (fork-grid) cells
       included (round two, per Tom: badges were originally full-width-
       only, back when a compact cell also had to fit the now-removed
       branch combo in the same cramped space) -- a quick-scan visual
       cue for what a card actually is, without reading its label's
       prose.
    4. opacity_spin -- moved IN-ROW, next to the stage name, always
       visible and directly editable (drag-to-scrub, same as before).
       Round one had opacity as a separate "Opacity:" control below the
       whole list, operating on "whichever stage happens to be
       selected" -- Tom's round-two feedback was that this location was
       confusing, disconnected from the row it actually affects. That
       control (StackPanel.opacity_spin, _on_opacity_changed) is gone;
       StackPanel._on_row_opacity_changed is this row's own handler now.
    5. The enabled toggle is a filled/hollow dot (●/○), not a checkmark/
       cross -- per Tom, "something more elegant than the checkmark."
       See _apply_enabled_state.
    6. No more leading "1. "/"2. " stage-number prefix on the label
       (round two, per Tom: "lose the number before the operator name")
       -- the old display_number param this constructor and update_
       content took is gone entirely, not just left unused; a stage's
       position was already visually obvious from its order in the
       list, and the badge (point 3) now carries more of the "what is
       this at a glance" job the number was also partly doing."""

    _DISABLED_OVERLAY_ALPHA = 0.32
    _ANIM_DURATION_MS = 140

    def __init__(self, stage, on_row_clicked, on_enabled_clicked, on_delete_clicked,
                 on_opacity_changed=None, matte_options=None, on_matte_changed=None,
                 own_matte_letter=None, compact=False, parent=None):
        super().__init__(parent)
        self.stage_id = stage.id
        self.compact = compact
        self._on_row_clicked = on_row_clicked
        self.setObjectName("stackStageRow")
        # QFrame doesn't opt into QSS :hover repaints on its own (unlike a
        # QPushButton etc.) -- needed for style.py's new
        # QFrame#stackStageRow:hover rule (per Tom: hovering a row should
        # brighten its border a little, independent of selection).
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        # Only flagged for QSS on a stray tag OUTSIDE a fork region (see
        # style.py's [stageBranch] rule) -- inside the two-column table,
        # column position already says which branch a cell belongs to, so
        # repeating it as a per-cell accent would be exactly the kind of
        # redundant decoration Tom's "bulky" note was about.
        if stage.branch and not compact:
            self.setProperty("stageBranch", stage.branch)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._selected = False

        self._enabled_progress = 1.0 if stage.enabled else 0.0
        self._disabled_anim = QVariantAnimation(self)
        self._disabled_anim.setDuration(self._ANIM_DURATION_MS)
        self._disabled_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._disabled_anim.valueChanged.connect(self._on_disabled_progress)

        # Slightly tighter than before (per Tom's "tighten row density"
        # ask) -- both compact and full-width margins/spacing trimmed a
        # couple px each, still comfortable to click, just less wasted
        # padding per card so more of the stack is visible at once.
        layout = QHBoxLayout(self)
        if compact:
            layout.setContentsMargins(4, 2, 4, 2)
            layout.setSpacing(3)
        else:
            layout.setContentsMargins(6, 4, 6, 4)
            layout.setSpacing(6)

        self.enabled_button = QToolButton()
        self.enabled_button.setObjectName("stageEnabledToggle")
        self.enabled_button.setAutoRaise(True)
        self.enabled_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.enabled_button.setToolTip("Click to enable/disable this stage")
        self.enabled_button.clicked.connect(lambda: on_enabled_clicked(stage.id))
        layout.addWidget(self.enabled_button)

        # Kind badge -- shown on EVERY card now, compact (fork-grid) cells
        # included (round two, per Tom: "the glyphs for operators dont
        # show up when they are in a fork"). Originally full-width-only,
        # back when a compact cell also had to fit the branch combo in
        # the same cramped space -- that's gone now (branch is purely
        # positional, see class docstring's STACK FORKING section), so
        # there's no longer a real space reason to omit it, and fork
        # cells behaving like ordinary rows in every other respect
        # (full drag support, opacity spin) made the missing badge read
        # as an oversight rather than a deliberate simplification.
        self.badge_widget = QLabel(_stage_kind_badge_text(stage.kind))
        self.badge_widget.setObjectName("stageKindBadge")
        self.badge_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge_widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.badge_widget)

        # Matte-channel letter badge (per Tom: "it needs to be clear what
        # the letter code for each matte is") -- shown ONLY on a matte-
        # PRODUCING row (a "keyer"/"segment_mask" stage currently, see
        # stack.MATTE_PRODUCING_KINDS), right next to the kind badge, so
        # you can tell "this row is Matte A" at a glance without having
        # to open some OTHER row's matte_combo to find out. Accent-
        # colored (not the kind badge's own muted pill) since this is
        # the one piece of identity a downstream row's dropdown actually
        # needs to match up -- see style.py's QLabel#stageMatteLetterBadge
        # rule. `own_matte_letter` is None for every other stage kind,
        # in which case this widget is built but hidden -- simpler than
        # conditionally not constructing it, and matches matte_combo's
        # own "always exists, sometimes hidden" convention just below.
        self.matte_letter_badge = QLabel(own_matte_letter or "")
        self.matte_letter_badge.setObjectName("stageMatteLetterBadge")
        self.matte_letter_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.matte_letter_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.matte_letter_badge.setToolTip("This stage's own matte-channel letter")
        self.matte_letter_badge.setVisible(own_matte_letter is not None)
        layout.addWidget(self.matte_letter_badge)

        self._text_col = QVBoxLayout()
        self._text_col.setSpacing(0)
        # _ElidingLabel truncates with "…" to fit whatever width it
        # actually ends up with (compact fork-grid cell or a full-width
        # row alike), keeping the untruncated text as its own tooltip --
        # see that class's docstring. Same mechanism for both cases now
        # (previously compact-only, via a cruder fixed-character-count
        # _elide()) since a full-width row can run out of space too, per
        # Tom: the delete button at the row's far end should stay visible
        # regardless of panel width, not get pushed off past it.
        self.label_widget = _ElidingLabel(stage.label)
        self.label_widget.setObjectName("stageLabel")
        # Transparent to mouse events -- otherwise a click on the label
        # text itself is swallowed by the QLabel rather than reaching this
        # frame's mousePressEvent below, and "click anywhere on the card
        # to select it" would only work in the card's empty margins.
        self.label_widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._text_col.addWidget(self.label_widget)
        layout.addLayout(self._text_col, stretch=1)

        # Opacity, live, right in the row next to the stage name (per Tom
        # -- the old version lived in a separate "Opacity:" control below
        # the whole list, tied to "whichever stage happens to be
        # selected," which read as confusing/disconnected from the stage
        # it actually affected). Always visible now, not just when
        # non-default, since it's an actionable control here rather than
        # a passive "opacity isn't 100%" hint -- you shouldn't have to
        # already know a row has non-default opacity before you can see
        # where to change it. Narrower in compact (fork-grid) cells, same
        # "keep the compact cell lean" precedent the badge (below) follows.
        self.opacity_spin = QSpinBox()
        self.opacity_spin.setObjectName("stageOpacitySpin")
        self.opacity_spin.setRange(0, 100)
        self.opacity_spin.setSuffix("%")
        self.opacity_spin.setValue(round(stage.opacity * 100))
        self.opacity_spin.setFixedWidth(48 if compact else 60)
        self.opacity_spin.setToolTip("This stage's opacity -- 0% is fully bypassed, 100% is full strength.")
        enable_drag_to_scrub(self.opacity_spin)
        if on_opacity_changed is not None:
            self.opacity_spin.valueChanged.connect(
                lambda value: on_opacity_changed(stage.id, value))
        layout.addWidget(self.opacity_spin)

        # Matte channel selector (see stack.py's docstring, "MATTE
        # CHANNELS") -- "None" (this stage's opacity behaves exactly as
        # before) plus one entry per upstream "keyer" stage's own current
        # display letter, itemData the PRODUCING stage's own Stage.id
        # (never the letter itself -- see matte_options' own docstring in
        # StackPanel._matte_options_by_index on why letters are display-
        # only). Hidden for a "keyer" row itself (a keyer doesn't consume
        # a matte in v1) -- shown uniformly for every OTHER kind
        # (including fork_split/fork_merge, whose rows already show an
        # opacity spin that's similarly inert for fork_split specifically
        # -- consistent with this file's existing "one uniform row shape"
        # convention rather than special-casing which kinds get which
        # controls).
        self.matte_combo = QComboBox()
        self.matte_combo.setObjectName("stageMatteCombo")
        self.matte_combo.setToolTip(
            "Gate this stage's opacity by an upstream Keyer's matte, "
            "per pixel, instead of a flat percentage.")
        self.matte_combo.setFixedWidth(52 if compact else 64)
        self.matte_combo.addItem("None", None)
        for letter, keyer_stage_id in (matte_options or []):
            self.matte_combo.addItem(letter, keyer_stage_id)
        current_matte_source = stage.params.get("matte_source_id")
        idx = self.matte_combo.findData(current_matte_source)
        self.matte_combo.setCurrentIndex(idx if idx >= 0 else 0)
        if stage.kind == "keyer":
            self.matte_combo.setVisible(False)
        elif on_matte_changed is not None:
            self.matte_combo.currentIndexChanged.connect(
                lambda _index: on_matte_changed(stage.id, self.matte_combo.currentData()))
        layout.addWidget(self.matte_combo)

        # "x" (not the enabled toggle's dot glyph -- a DIFFERENT symbol on
        # purpose, so the two aren't visually confusable at a glance) at
        # the row's far end.
        self.delete_button = QToolButton()
        self.delete_button.setObjectName("stageDeleteButton")
        self.delete_button.setAutoRaise(True)
        self.delete_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_button.setText("×")
        self.delete_button.setToolTip("Remove this stage")
        self.delete_button.clicked.connect(lambda: on_delete_clicked(stage.id))
        layout.addWidget(self.delete_button)

        # Sets stageEnabled on self/enabled_button/label_widget and the
        # glyph, all in one place -- see _apply_enabled_state. animate=
        # False at construction time: nothing's been painted yet, so
        # there's nothing to ease from.
        self._apply_enabled_state(stage.enabled, animate=False, repolish=False)

    def _apply_enabled_state(self, enabled, animate=True, repolish=True):
        """Sets the enabled/disabled glyph + QSS property on every part of
        the card that depends on it, and (unless this is the initial
        construction, see __init__'s call) eases the dim overlay in/out
        (see paintEvent) instead of snapping it -- called both by
        __init__ and by update_content (StackPanel._on_row_enabled_
        clicked's targeted path, see class docstring point 1)."""
        # Filled/hollow dot (per Tom: "something more elegant than the
        # checkmark") -- a solid accent-colored dot reads as "on," a
        # hollow outline as "off," without the heavier, more insistent
        # look a bold checkmark/cross pair had at this size.
        self.enabled_button.setText("●" if enabled else "○")
        self.enabled_button.setProperty("stageEnabled", enabled)
        self.setProperty("stageEnabled", enabled)
        self.label_widget.setProperty("stageEnabled", enabled)
        if repolish:
            for widget in (self, self.enabled_button, self.label_widget):
                widget.style().unpolish(widget)
                widget.style().polish(widget)
        target = 1.0 if enabled else 0.0
        if animate:
            self._disabled_anim.stop()
            self._disabled_anim.setStartValue(self._enabled_progress)
            self._disabled_anim.setEndValue(target)
            self._disabled_anim.start()
        else:
            self._enabled_progress = target

    def _on_disabled_progress(self, value):
        self._enabled_progress = value
        self.update()

    def set_selected(self, selected):
        # Plain property + repolish -- see class docstring point 2 on why
        # this is deliberately NOT animated the way the disabled dim is:
        # selection here is just the card border's own color switching to
        # accent (style.py's [stageSelected] rule), same mechanism as any
        # other QSS property change in this app, not a custom-painted
        # effect.
        if selected == self._selected:
            return
        self._selected = selected
        self.setProperty("stageSelected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def update_content(self, stage):
        """Refreshes THIS card's label/opacity/enabled-state in place for
        a stage whose params changed live but whose position in the
        list/fork layout didn't -- see class docstring point 1 and
        StackPanel._update_row_content. Deliberately does NOT touch
        stage_id or the badge -- neither of those can change
        via a live param edit, only via a structural mutation that
        already goes through StackPanel._refresh_list().

        Opacity is synced with blockSignals -- this same spin box is also
        this row's OWN opacity control (see __init__), so an edit that
        came from somewhere else entirely (e.g. recalling a CDL Library
        entry, undo/redo) needs to update the displayed value without
        re-firing valueChanged straight back into
        StackPanel._on_row_opacity_changed."""
        self.label_widget.setText(stage.label)
        self.opacity_spin.blockSignals(True)
        self.opacity_spin.setValue(round(stage.opacity * 100))
        self.opacity_spin.blockSignals(False)
        # Re-syncs the SELECTION only (not the item list -- which upstream
        # keyers are even offered only changes on a structural mutation,
        # already routed through a full _refresh_list(), see this class's
        # own docstring point 1) -- defensive, for a matte_source_id that
        # changed via something other than this row's own combo (e.g.
        # undo/redo landing here instead of a full _refresh_list somehow).
        idx = self.matte_combo.findData(stage.params.get("matte_source_id"))
        self.matte_combo.blockSignals(True)
        self.matte_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.matte_combo.blockSignals(False)
        self._apply_enabled_state(stage.enabled, animate=True)

    def paintEvent(self, event):
        # Animated overlay on top of the QSS-painted card -- see class
        # docstring point 2. A translucent palette(window) wash that eases
        # in/out as _enabled_progress moves (1.0 = fully enabled = no
        # dim), on top of whatever the QSS border/background already
        # painted for the enabled/selected/hover state.
        super().paintEvent(event)
        disabled_alpha = self._DISABLED_OVERLAY_ALPHA * (1.0 - self._enabled_progress)
        if disabled_alpha <= 0.002:
            return
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            rect = self.rect().adjusted(0, 0, -1, -1)
            color = QColor(self.palette().window().color())
            color.setAlphaF(disabled_alpha)
            painter.setBrush(color)
            painter.drawRoundedRect(rect, PANEL_BORDER_RADIUS, PANEL_BORDER_RADIUS)
        finally:
            painter.end()

    def mousePressEvent(self, event):
        # Let the enabled toggle / opacity spin / delete button handle
        # their own clicks -- only treat this as "select the row" when
        # the press lands elsewhere on the card (label, empty margin).
        pos = event.position().toPoint()
        if not (self.enabled_button.geometry().contains(pos)
                or self.delete_button.geometry().contains(pos)
                or self.opacity_spin.geometry().contains(pos)
                or self.matte_combo.geometry().contains(pos)):
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
                 on_delete_clicked, on_opacity_changed, left_matte_options=None,
                 right_matte_options=None, on_matte_changed=None,
                 left_matte_letter=None, right_matte_letter=None, parent=None):
        super().__init__(parent)
        self.setObjectName("stackForkGridRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.left_widget = self._build_cell(
            left_stage, on_row_clicked, on_enabled_clicked, on_delete_clicked,
            on_opacity_changed, left_matte_options, on_matte_changed, left_matte_letter)
        layout.addWidget(self.left_widget, stretch=1)

        self.right_widget = self._build_cell(
            right_stage, on_row_clicked, on_enabled_clicked, on_delete_clicked,
            on_opacity_changed, right_matte_options, on_matte_changed, right_matte_letter)
        layout.addWidget(self.right_widget, stretch=1)

    @staticmethod
    def _build_cell(stage, on_row_clicked, on_enabled_clicked, on_delete_clicked,
                     on_opacity_changed, matte_options=None, on_matte_changed=None,
                     own_matte_letter=None):
        if stage is None:
            return _EmptyBranchCell()
        return _StageRowWidget(
            stage, on_row_clicked, on_enabled_clicked, on_delete_clicked,
            on_opacity_changed=on_opacity_changed, matte_options=matte_options,
            on_matte_changed=on_matte_changed, own_matte_letter=own_matte_letter, compact=True)

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
        # So style.py can scope OFF the generic QListWidget::item:selected/
        # :hover rectangular background just for this list -- see that
        # rule's own comment on why: those global rules were painting a
        # sharp-cornered highlight rectangle behind/around each row's own
        # rounded "pill" card (_StageRowWidget's border-based selection
        # cue), a real, longstanding bug Tom caught (2026-08-12, "get rid
        # of the large highlight filling the regular 4 sided polygon
        # around the row's pill shaped entry") that had nothing to do
        # with this round's border-color simplification -- it was already
        # there, just never addressed, because the border cue used to be
        # bold/washed enough (round one) to not visually compete with it.
        self.setObjectName("stackListWidget")
        self._drop_indicator_row = None  # see dragMoveEvent/paintEvent below
        # Which column the indicator line should span for a fork-interior
        # row -- "a"/"b", or None for an ordinary row (full-width line).
        # See paintEvent.
        self._drop_indicator_branch = None
        # The stage id under the press that (maybe) becomes a drag -- see
        # mousePressEvent/_stage_id_at. NOT the same as currentRow()/
        # currentItem(): a fork-grid row can hold two independently
        # draggable stages (one per column), so drag source identity has
        # to be resolved at the stage level, not the QListWidget row
        # level. Only ever read by startDrag/dropEvent for the lifetime
        # of a single press-to-drop gesture.
        self._pressed_stage_id = None
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        # NOT Qt's built-in indicator -- dragMoveEvent below is a full
        # override that never calls super(), which is exactly what
        # QAbstractItemView's OWN dragMoveEvent would normally use to
        # compute and repaint that indicator; with it skipped entirely
        # (deliberately, per this class's docstring -- Qt's default
        # InternalMove drop handling doesn't reliably carry a row's
        # setItemWidget() content), setDropIndicatorShown(True) here would
        # be a no-op flag with nothing ever actually driving it. Painted
        # by hand instead -- see paintEvent -- driven by the exact same
        # _drop_row() this widget's own dropEvent already uses, so the
        # line always points at precisely where a drop will actually land.
        self.setDropIndicatorShown(False)
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

    def mousePressEvent(self, event):
        """Records which STAGE (not which QListWidget row) the press
        landed on, for startDrag to use as the drag source -- see
        _pressed_stage_id's own comment in __init__ and _stage_id_at
        below. Doesn't consume/replace anything: super() still runs
        unconditionally, same current-row/selection tracking as before."""
        self._pressed_stage_id = self._stage_id_at(event.position().toPoint())
        super().mousePressEvent(event)

    def _stage_id_at(self, pos):
        """Resolves a viewport position to the specific stage id under
        it. For an ordinary row this is just that row's one stage. For a
        fork-grid row (see _ForkBranchGridRowWidget) it's whichever
        SIDE's cell `pos` actually falls in -- left and right hold
        independently draggable stages now, so "which row" alone isn't
        enough anymore. None for a position with nothing draggable under
        it: the "Branch A | Branch B" header, an _EmptyBranchCell, or
        genuinely outside every row."""
        item = self.itemAt(pos)
        if item is None:
            return None
        widget = self.itemWidget(item)
        if isinstance(widget, _StageRowWidget):
            return widget.stage_id
        if isinstance(widget, _ForkBranchGridRowWidget):
            # left_widget/right_widget's own .geometry() is in THIS
            # composite widget's local coordinates, not the viewport's --
            # translate pos into that space before testing either cell.
            local_pos = pos - widget.geometry().topLeft()
            for cell in (widget.left_widget, widget.right_widget):
                if isinstance(cell, _StageRowWidget) and cell.geometry().contains(local_pos):
                    return cell.stage_id
        return None

    def startDrag(self, supportedActions):
        """Custom drag visuals (per Tom, "drag ghost/lift on the dragged
        row"). Qt's default QAbstractItemView.startDrag() renders its
        drag pixmap from the delegate, which has nothing to paint for a
        setItemWidget()-covered row (see class docstring on why every row
        uses setItemWidget rather than a delegate) -- the default drag
        pixmap is blank/generic. This grabs the actual card's rendered
        pixels instead, composited at reduced opacity for a "lifted"
        look, and dims the SOURCE row for the duration of the drag (a
        "ghost" left behind) so it's obvious both where the card came
        from and where it currently is.

        Resolves the dragged widget via self._pressed_stage_id ->
        self._panel._stage_row_widgets (not self.currentItem()/
        itemWidget() -- those only identify a QListWidget ROW, which
        isn't precise enough once a fork-grid row can hold two
        independently draggable stages; _stage_id_at, called from
        mousePressEvent, already resolved the exact stage the press
        landed on). self._stage_row_widgets already maps every stage id
        to its real live card either way -- an ordinary row's own widget,
        or a fork cell's left_widget/right_widget -- so this needs no
        special-casing for which kind of row the drag started from.

        The dim is cleared unconditionally right after drag.exec()
        returns (that call blocks until the drag ends one way or
        another), covering a cancelled or dropped-outside-any-target drag
        too -- a successful reorder drop rebuilds the whole list via
        StackPanel._on_stage_reordered -> _refresh_list() anyway.

        REAL CRASH, found via Tom's own on-screen testing: a successful
        reorder drop doesn't just make that rebuild redundant, it makes
        touching `widget` afterward actively dangerous. dropEvent fires
        SYNCHRONOUSLY inside drag.exec()'s own nested event loop (Qt
        processes the drop before exec() returns control here), and
        _refresh_list() -> self.list_widget.clear() destroys every
        _StageRowWidget's underlying C++ object right then, including
        this row's -- so by the time this method's own `finally` runs,
        `widget` can already be a dangling Python wrapper around a
        deleted C++ object ("libshiboken: Internal C++ object
        (_StageRowWidget) already deleted", reproduced directly).
        shiboken6.isValid() is the correct guard for exactly this --
        same class of "don't touch a Qt object PySide's own wrapper
        outlived" issue wipe_compare_widget.py's isValid() checks guard
        against elsewhere in this codebase, just at the shiboken layer
        instead of QOpenGLWidget's own isValid()."""
        if self._pressed_stage_id is None:  # pragma: no cover -- defensive, e.g. header/empty cell
            return
        widget = self._panel._stage_row_widgets.get(self._pressed_stage_id)
        item = self.currentItem()
        drag = QDrag(self)
        # Qt's own DnD acceptance bookkeeping still wants real mimeData
        # on the QDrag -- its CONTENT is never actually read for the
        # internal-reorder case (dropEvent below identifies the source
        # via self._pressed_stage_id instead), so an empty list here if
        # there's genuinely no current item is a harmless fallback, not
        # a real gap.
        drag.setMimeData(self.model().mimeData([self.indexFromItem(item)] if item is not None else []))

        opacity_effect = None
        if widget is not None:
            pixmap = widget.grab()
            if not pixmap.isNull():
                lifted = QPixmap(pixmap.size())
                lifted.fill(Qt.GlobalColor.transparent)
                painter = QPainter(lifted)
                painter.setOpacity(0.85)
                painter.drawPixmap(0, 0, pixmap)
                painter.end()
                drag.setPixmap(lifted)
                drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))
            opacity_effect = QGraphicsOpacityEffect(widget)
            opacity_effect.setOpacity(0.35)
            widget.setGraphicsEffect(opacity_effect)

        try:
            drag.exec(supportedActions, Qt.DropAction.CopyAction)
        finally:
            if widget is not None and shiboken6.isValid(widget):
                widget.setGraphicsEffect(None)

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
            pos = event.position().toPoint()
            self._set_drop_indicator(self._drop_row(pos), self._drop_branch(pos))
        else:
            event.ignore()
            self._set_drop_indicator(None, None)

    def dragLeaveEvent(self, event):
        self._set_drop_indicator(None, None)
        super().dragLeaveEvent(event)

    def _set_drop_indicator(self, row, branch_guess):
        """`branch_guess`: the raw, purely x-position-derived side from
        _drop_branch (always "a" or "b" while dragging, meaningless for
        an ordinary row) -- paintEvent below is what actually decides
        whether the indicator line should span the whole row (ordinary)
        or just that one column (fork-interior), by checking the target
        row's own _RowTargets via self._panel."""
        if row == self._drop_indicator_row and branch_guess == self._drop_indicator_branch:
            return
        self._drop_indicator_row = row
        self._drop_indicator_branch = branch_guess
        self.viewport().update()

    def _drop_row(self, pos):
        item = self.itemAt(pos)
        if item is None:
            return self.count()
        row = self.row(item)
        if pos.y() > self.visualItemRect(item).center().y():
            row += 1
        return row

    def _drop_branch(self, pos):
        """Which column a drop position falls in -- "a" (left) or "b"
        (right) -- always resolvable regardless of row, split at the
        exact horizontal midpoint of the viewport (both branch columns
        are laid out stretch=1 in _ForkBranchGridRowWidget, so the
        visual split always sits there too). StackPanel's drop-target
        resolver (see _RowTargets) only actually consults this for a
        fork-interior row; it's harmless to compute unconditionally for
        every drop, ordinary rows just ignore it."""
        return "a" if pos.x() < self.viewport().width() / 2 else "b"

    def dropEvent(self, event):
        self._set_drop_indicator(None, None)
        mime = event.mimeData()
        pos = event.position().toPoint()
        target_row = self._drop_row(pos)
        branch = self._drop_branch(pos)
        if mime.hasFormat(LUT_PATH_MIME):
            path = bytes(mime.data(LUT_PATH_MIME)).decode("utf-8")
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self._panel._on_lut_path_dropped(path, target_row, branch)
        elif event.source() is self:
            source_stage_id = self._pressed_stage_id
            # CopyAction here too (see __init__) -- this IS a move at the
            # data level, just one _on_stage_reordered/_refresh_list does
            # itself rather than handing back to Qt's own row-removal.
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self._panel._on_stage_reordered(source_stage_id, target_row, branch)
        else:
            event.ignore()

    def paintEvent(self, event):
        """Draws the drag-to-reorder drop-position indicator (see
        __init__'s comment on why this is hand-painted rather than Qt's
        built-in setDropIndicatorShown mechanism) as a highlighted line
        positioned exactly at the boundary _drop_row() would actually
        insert at -- above the row under the cursor, or below it,
        matching that method's own upper/lower-half split. Drawn AFTER
        super().paintEvent() so it sits on top of the already-painted
        rows/cards, not underneath them.

        Per Tom: for a fork-interior target row, the line should only
        span the ONE column the drop will actually land in, not the
        full row width -- a full-width line there would suggest the
        drop affects both branches, when it only ever affects one. Reads
        the target row's own _RowTargets (via self._panel -- see
        _refresh_list/_RowTargets) to tell an ordinary row (full width)
        apart from a fork-interior one (half width, whichever half
        _drop_indicator_branch says)."""
        super().paintEvent(event)
        if self._drop_indicator_row is None:
            return
        viewport = self.viewport()
        if self._drop_indicator_row < self.count():
            y = self.visualItemRect(self.item(self._drop_indicator_row)).top()
        elif self.count() > 0:
            y = self.visualItemRect(self.item(self.count() - 1)).bottom()
        else:
            y = 0
        x_start, x_end = 0, viewport.width()
        if self._panel is not None:
            row_targets = self._panel._row_insert_index
            if 0 <= self._drop_indicator_row < len(row_targets):
                targets = row_targets[self._drop_indicator_row]
                if targets.ordinary is None:  # fork-interior -- span just the one column
                    half = viewport.width() / 2
                    if self._drop_indicator_branch == "b":
                        x_start, x_end = half, viewport.width()
                    else:
                        x_start, x_end = 0, half
        painter = QPainter(viewport)
        try:
            pen = QPen(self.palette().highlight().color())
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawLine(int(x_start), y, int(x_end), y)
        finally:
            painter.end()

    def keyPressEvent(self, event):
        # Delete/Backspace removes the currently selected stage -- same
        # action as clicking that row's own "x" (_StageRowWidget.
        # delete_button) or the old Remove button this + that button
        # replaced (per Tom).
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._panel._on_remove_clicked()
            return
        super().keyPressEvent(event)


class _ParamLabel(QLabel):
    """A parameter's own name label -- double-click resets it to default
    (per Tom: "add the ability to double click the name of a parameter
    to reset it individually"). `reset_callback` does the actual reset,
    almost always one or more setValue() calls on the control(s) this
    label names -- setValue already fires that control's normal
    valueChanged handler, so a reset applies live exactly the same way
    a manual edit would, no separate apply call needed at any call
    site. Used both for a single scalar control (Printer Lights'
    R/G/B, Exposure/Contrast's fields, Custom DRT's fields, CDL's Sat,
    Grading Primary's Pivot/Saturation, Grading Tone's S-Contrast) and
    for a whole wheel+spins field/range group at once (CDL's Slope/
    Offset/Power, Grading Primary's 7 fields, Grading Tone's 5 ranges)
    -- see each _reset_one_*_field helper for the latter."""

    def __init__(self, text, reset_callback, parent=None):
        super().__init__(text, parent)
        self._reset_callback = reset_callback
        self.setToolTip(f"Double-click to reset {text.rstrip(':')} to its default.")
        # Deliberately NOT PointingHandCursor -- Qt has no built-in
        # "reset" cursor, and the hand reads as a hyperlink/navigation
        # affordance (per Tom, "the gloved hand with finger icon"),
        # which is the wrong signal for a reset action. Left at the
        # default arrow; the tooltip carries the double-click hint.

    def mouseDoubleClickEvent(self, event):
        self._reset_callback()
        super().mouseDoubleClickEvent(event)


class _CurrentPageStackedWidget(QStackedWidget):
    """Plain QStackedWidget reports sizeHint()/minimumSizeHint() as the
    MAX across every page it holds, not just the one currently shown --
    confirmed via a live repro (a 2-page stack's minimumSizeHint stayed
    pinned to its tallest page's height even with the shortest page
    selected). That floor propagates up through this panel's own
    layout to the surrounding dock/splitter, so the Parameters dock
    can never be dragged shorter than the tallest operator (Grading
    Tone) even while a much sparser operator (e.g. Printer Lights) is
    selected -- the "stuck" panel height Tom flagged. Overriding both
    hints to delegate to currentWidget() (each page already wrapped by
    _wrap_inspector_page, so currentWidget() reports its own real
    content height) lets the dock's floor track whichever operator is
    actually showing."""

    def sizeHint(self):
        page = self.currentWidget()
        return page.sizeHint() if page is not None else super().sizeHint()

    def minimumSizeHint(self):
        page = self.currentWidget()
        return page.minimumSizeHint() if page is not None else super().minimumSizeHint()

    def setCurrentIndex(self, index):
        super().setCurrentIndex(index)
        self.updateGeometry()

    def setCurrentWidget(self, widget):
        super().setCurrentWidget(widget)
        self.updateGeometry()


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
    # no idea the Gallery exists; MainWindow routes this into
    # StackMemoriesPanel.save_cdl_entry (CDL Library was folded into the
    # Gallery, per Tom -- see stack_memories_panel.py's own docstring),
    # same "panels don't know about each other, MainWindow bridges"
    # architecture as StackMemoriesPanel.recall_requested ->
    # StackPanel.set_stack. `object` (not `dict`) as the second signal
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
        # No OCIO gate either -- keyer.py is plain numpy HSL math, no OCIO
        # dependency at all (see that module's own docstring).
        menu.addAction("Add Keyer", self._on_add_keyer_clicked)
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
        self._build_fork_split_inspector()
        self._build_fork_merge_inspector()
        layout.addWidget(self.inspector_stack, stretch=1)

    # ---- global settings (grid size + domain, formerly Resize/Bounds) ----
    # Own popup dialog rather than inline in Stack Params -- see this
    # module's docstring.

    @staticmethod
    def _make_domain_spin(default_value):
        """One Domain min/max field (per Tom -- these used to be plain
        QLineEdits, "supposed to be" spin boxes already but weren't).
        Wide range and 4 decimals: a domain bound is a HDR/scene-linear
        LUT's actual input range, which can legitimately be negative or
        run well past [0,1] (e.g. an extended-range IDT/LMT), not just a
        display-referred 0-1 value like most of this app's other numeric
        fields."""
        spin = QDoubleSpinBox()
        spin.setRange(-10000.0, 10000.0)
        spin.setDecimals(4)
        spin.setSingleStep(0.01)
        spin.setValue(default_value)
        enable_drag_to_scrub(spin)
        return spin

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
            min_field = self._make_domain_spin(0.0)
            row.addWidget(min_field)
            row.addWidget(QLabel(f"{channel} max:"))
            max_field = self._make_domain_spin(1.0)
            row.addWidget(max_field)
            self._domain_fields[channel] = (min_field, max_field)
            outer.addLayout(row)
        for channel in ("G", "B"):
            min_field, max_field = self._domain_fields[channel]
            min_field.setEnabled(False)
            max_field.setEnabled(False)
        r_min, r_max = self._domain_fields["R"]
        r_min.valueChanged.connect(self._sync_linked_domain_fields)
        r_max.valueChanged.connect(self._sync_linked_domain_fields)

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
            min_field.setValue(lo[i])
            max_field.setValue(hi[i])
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
            min_field.setValue(float(lo[i]))
            max_field.setValue(float(hi[i]))

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
            min_field.setValue(r_min.value())
            max_field.setValue(r_max.value())

    def _on_apply_global_settings_clicked(self):
        try:
            size = int(self.size_combo.currentText())
        except ValueError:
            QMessageBox.warning(self._global_settings_dialog, "Invalid size", "Grid size must be an integer.")
            return
        if size < 2:
            QMessageBox.warning(self._global_settings_dialog, "Invalid size", "Grid size must be at least 2.")
            return
        # No "is this actually a number?" check needed anymore -- these
        # are QDoubleSpinBox fields now (per Tom), which can never hold
        # invalid text in the first place, unlike the plain QLineEdits
        # they replaced.
        domain_min = [self._domain_fields[c][0].value() for c in ("R", "G", "B")]
        domain_max = [self._domain_fields[c][1].value() for c in ("R", "G", "B")]
        if any(mn >= mx for mn, mx in zip(domain_min, domain_max)):
            QMessageBox.warning(self._global_settings_dialog, "Invalid domain", "Each channel's min must be less than its max.")
            return

        self.stack.size = size
        self.stack.domain_min = tuple(domain_min)
        self.stack.domain_max = tuple(domain_max)
        self._update_domain_readout()
        self._emit_changed()

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
        self._refresh_list()
        self._refresh_inspector()
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
        self._refresh_list()
        self._refresh_inspector()
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

    def _on_add_custom_drt_clicked(self):
        if not aces_drt.OCIO_AVAILABLE:
            return
        # "ACEScct", not aces_drt's own "ACES2065-1" default -- matches
        # DEFAULT_SAMPLE_IMAGE_SOURCE_COLOURSPACE (app_logic.py), the
        # declared encoding of the bundled default sample image every
        # CAMier render in the design/validation pass was checked
        # against. Getting this wrong doesn't error -- it just silently
        # reinterprets log-encoded values as if they were already linear,
        # producing a real but wrong-looking result (confirmed: mean
        # output shifts from 0.40 to 0.59 on the bundled image). Still
        # just a starting point -- change the Working Color Space combo
        # to match whatever's actually loaded.
        stage = st.make_custom_drt_stage("ACEScct")
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
        stage = st.make_color_warper_stage(working_colourspace=cw.DEFAULT_WORKING_COLOURSPACE)
        self._add_stage(stage)

    def _on_add_keyer_clicked(self):
        stage = st.make_keyer_stage()
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

    def _build_empty_inspector(self):
        widget = QLabel("Select a stage above to see/edit its settings.")
        widget.setWordWrap(True)
        widget.setStyleSheet(f"color: {HINT_TEXT_HEX};")
        self.inspector_stack.addWidget(self._wrap_inspector_page(widget))
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
        group = QGroupBox("LUT")
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

        # See _build_printer_lights_inspector's own comment on why every
        # inspector page needs a trailing vertical stretch.
        outer.addStretch(1)

        self.inspector_stack.addWidget(self._wrap_inspector_page(group))
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
        group = QGroupBox(f"Printer Lights (points, ~{POINTS_PER_STOP:.0f}/stop)")
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
        # the working space applies immediately, same as the spinboxes below.
        self.pl_space_combo.currentTextChanged.connect(self._on_pl_space_changed)
        space_row.addWidget(self.pl_space_combo, stretch=1)
        outer.addLayout(space_row)

        gang_row = QHBoxLayout()
        self.pl_gang_checkbox = QCheckBox("Gang R/G/B")
        self.pl_gang_checkbox.toggled.connect(self._on_pl_gang_toggled)
        gang_row.addWidget(self.pl_gang_checkbox)
        gang_row.addStretch(1)
        outer.addLayout(gang_row)

        # Sliding spinboxes, not system QSliders (per Tom: "printer lights
        # shouldn't use system sliders, move to sliding spinboxes") -- same
        # click-drag-to-scrub treatment (spinbox_scrub.enable_drag_to_scrub)
        # every other numeric field in this inspector stack already uses
        # (CDL/Grading Primary/Exposure-Contrast/Grading Tone), so this was
        # the one inspector still standing out with plain native slider
        # chrome. Integer-valued (QSpinBox, not QDoubleSpinBox) -- printer
        # light "points" were always whole numbers here (the old slider's
        # own int range/int value), unchanged by this swap. One spin per
        # channel now shows its own value directly, so the separate value
        # QLabel this used to need alongside each slider is gone too.
        self._pl_spins = {}
        for channel in ("R", "G", "B"):
            row = QHBoxLayout()
            spin = QSpinBox()
            spin.setRange(-PRINTER_LIGHT_RANGE, PRINTER_LIGHT_RANGE)
            spin.setValue(0)
            enable_drag_to_scrub(spin)
            spin.valueChanged.connect(lambda v, ch=channel: self._on_pl_spin_changed(ch, v))
            row.addWidget(_ParamLabel(f"{channel}:", lambda s=spin: s.setValue(0)))
            row.addWidget(spin, stretch=1)
            self._pl_spins[channel] = spin
            outer.addLayout(row)

        # No Update Stage button here (unlike LUT/ACES DRT) -- per Tom,
        # Printer Lights applies in real time: every spinbox edit and
        # working-space change writes straight into the selected stage's
        # params and re-bakes immediately, see _apply_printer_lights_live.
        buttons_row = QHBoxLayout()
        reset_button = QPushButton("Reset to 0")
        reset_button.clicked.connect(self._on_pl_reset_clicked)
        buttons_row.addWidget(reset_button)
        buttons_row.addStretch(1)
        outer.addLayout(buttons_row)

        # Vertical trailing stretch (per Tom: visible empty space between
        # every row) -- inspector_stack (a QStackedWidget, see _build_
        # layout) sizes its CURRENT page to fill whatever height it's
        # been given, which is driven by the TALLEST page among all of
        # them (Grading Tone's 5-range page). Without a stretch item to
        # absorb that leftover height, Qt's QVBoxLayout spreads it evenly
        # across every row instead -- stretching each control's own row
        # tall rather than leaving the rows at their natural height with
        # dead space collected at the bottom, which is what actually
        # looked like "wasted space between fields" for every inspector
        # sparser than Grading Tone. Applied to every _build_*_inspector
        # method for the same reason, not just this one.
        outer.addStretch(1)

        self.inspector_stack.addWidget(self._wrap_inspector_page(group))
        self._printer_lights_inspector_index = 2

    def _on_pl_gang_toggled(self, ganged):
        if not ganged:
            return
        r_value = self._pl_spins["R"].value()
        for channel in ("G", "B"):
            spin = self._pl_spins[channel]
            if spin.value() != r_value:
                spin.setValue(r_value)
        self._apply_printer_lights_live()

    def _on_pl_spin_changed(self, channel, value):
        if self.pl_gang_checkbox.isChecked():
            for other_channel, other_spin in self._pl_spins.items():
                if other_channel == channel or other_spin.value() == value:
                    continue
                other_spin.blockSignals(True)
                other_spin.setValue(value)
                other_spin.blockSignals(False)
        self._apply_printer_lights_live()

    def _on_pl_space_changed(self, _text):
        self._apply_printer_lights_live()

    def _on_pl_reset_clicked(self):
        # Each setValue below fires _on_pl_spin_changed (unless already
        # 0), which applies live the same as dragging would -- no separate
        # apply call needed here.
        for spin in self._pl_spins.values():
            spin.setValue(0)

    def _apply_printer_lights_live(self):
        stage = self._selected_stage()
        if stage is None or stage.kind != "printer_lights":
            return
        stage.params["r_points"] = self._pl_spins["R"].value()
        stage.params["g_points"] = self._pl_spins["G"].value()
        stage.params["b_points"] = self._pl_spins["B"].value()
        stage.params["working_colourspace"] = self.pl_space_combo.currentText()
        self._emit_changed()

    # ---- inspector: ACES DRT stage -----------------------------------

    def _build_aces_drt_inspector(self):
        group = QGroupBox("ACES DRT")
        outer = QVBoxLayout(group)

        if not aces_drt.OCIO_AVAILABLE:
            label = QLabel(
                "ACES DRT stages are unavailable -- PyOpenColorIO failed to "
                f"import:\n{aces_drt.OCIO_IMPORT_ERROR}\n\n"
                "Install with: pip3 install opencolorio, then restart."
            )
            label.setWordWrap(True)
            outer.addWidget(label)
            self.inspector_stack.addWidget(self._wrap_inspector_page(group))
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

        # See _build_printer_lights_inspector's own comment on why every
        # inspector page needs a trailing vertical stretch.
        outer.addStretch(1)

        self.inspector_stack.addWidget(self._wrap_inspector_page(group))
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
        self._update_row_content(stage.id)
        self._emit_changed()

    # ---- inspector: custom_drt ("CAMier") stage ------------------------

    # (attr suffix, label, min, max, step, decimals, default) -- one row
    # per numeric param, grouped under bold section labels below rather
    # than a nested QGroupBox each, same convention grading_tone's ranges
    # use. Order matches custom_drt.py's own pipeline stages.
    _CDR_TONESCALE_FIELDS = (
        ("contrast", "Contrast", 0.0, 1.0, 0.01, 3, custom_drt.DEFAULT_CONTRAST),
        ("headroom", "Headroom (stops)", 0.1, 10.0, 0.1, 2, custom_drt.DEFAULT_HEADROOM),
        ("blend_percentile", "Highlight blend starts at", 0.5, 0.999, 0.001, 3,
         custom_drt.DEFAULT_BLEND_PERCENTILE),
        ("tail_width", "Highlight width (stops)", 0.1, 20.0, 0.1, 2, custom_drt.DEFAULT_TAIL_WIDTH),
    )
    _CDR_DESAT_FIELDS = (
        ("desat_start", "Desaturation starts at", 0.0, 1.0, 0.01, 3, custom_drt.DEFAULT_DESAT_START),
        ("desat_end", "Fully neutral at", 0.0, 1.0, 0.01, 3, custom_drt.DEFAULT_DESAT_END),
    )
    _CDR_GAMUT_FIELDS = (
        ("identity_zone", "Identity zone", 0.0, 1.0, 0.01, 3, custom_drt.DEFAULT_IDENTITY_ZONE),
        ("transition_softness", "Transition softness", 0.0, 1.0, 0.01, 3,
         custom_drt.DEFAULT_TRANSITION_SOFTNESS),
        ("fullness_r", "Fullness R", 0.0, 1.0, 0.01, 3, custom_drt.DEFAULT_FULLNESS_R),
        ("fullness_g", "Fullness G", 0.0, 1.0, 0.01, 3, custom_drt.DEFAULT_FULLNESS_G),
        ("fullness_b", "Fullness B", 0.0, 1.0, 0.01, 3, custom_drt.DEFAULT_FULLNESS_B),
    )

    def _build_custom_drt_inspector(self):
        """CAMier -- LUTier's own DRT (see custom_drt.py's docstring for
        the full four-stage pipeline this panel exposes: tonescale,
        highlight desaturation, gamut compression). Real-time like
        Printer Lights (see _build_printer_lights_inspector) -- every
        combo/spin box change writes straight into the selected stage's
        params and re-bakes immediately, no Update Stage button."""
        group = QGroupBox("CAMier DRT")
        outer = QVBoxLayout(group)

        if not aces_drt.OCIO_AVAILABLE:
            label = QLabel(
                "CAMier DRT stages are unavailable -- PyOpenColorIO failed to "
                f"import:\n{aces_drt.OCIO_IMPORT_ERROR}\n\n"
                "Install with: pip3 install opencolorio, then restart."
            )
            label.setWordWrap(True)
            outer.addWidget(label)
            self.inspector_stack.addWidget(self._wrap_inspector_page(group))
            self._custom_drt_inspector_index = self.inspector_stack.count() - 1
            return

        space_row = QHBoxLayout()
        space_row.addWidget(QLabel("Working Color Space:"))
        self.cdr_space_combo = QComboBox()
        self.cdr_space_combo.addItems(custom_drt.list_working_colourspaces())
        self.cdr_space_combo.currentTextChanged.connect(self._on_cdr_field_changed)
        space_row.addWidget(self.cdr_space_combo, stretch=1)
        outer.addLayout(space_row)

        self._cdr_spins = {}

        def add_section(title, fields):
            section_label = QLabel(title)
            section_label.setStyleSheet("font-weight: 600;")
            outer.addWidget(section_label)
            for key, field_label, lo, hi, step, decimals, default in fields:
                row = QHBoxLayout()
                spin = QDoubleSpinBox()
                spin.setRange(lo, hi)
                spin.setDecimals(decimals)
                spin.setSingleStep(step)
                spin.setValue(default)
                enable_drag_to_scrub(spin)
                spin.valueChanged.connect(self._on_cdr_field_changed)
                row.addWidget(_ParamLabel(field_label, lambda s=spin, d=default: s.setValue(d)))
                row.addWidget(spin, stretch=1)
                self._cdr_spins[key] = spin
                outer.addLayout(row)

        add_section("Tonescale", self._CDR_TONESCALE_FIELDS)
        add_section("Highlight Desaturation", self._CDR_DESAT_FIELDS)

        gamut_label = QLabel("Gamut Compression")
        gamut_label.setStyleSheet("font-weight: 600;")
        outer.addWidget(gamut_label)
        for key, field_label, lo, hi, step, decimals, default in self._CDR_GAMUT_FIELDS:
            row = QHBoxLayout()
            spin = QDoubleSpinBox()
            spin.setRange(lo, hi)
            spin.setDecimals(decimals)
            spin.setSingleStep(step)
            spin.setValue(default)
            enable_drag_to_scrub(spin)
            spin.valueChanged.connect(self._on_cdr_field_changed)
            row.addWidget(_ParamLabel(field_label, lambda s=spin, d=default: s.setValue(d)))
            row.addWidget(spin, stretch=1)
            self._cdr_spins[key] = spin
            outer.addLayout(row)

        # ---- Mastering Display -> Final Target (Tom, 2026-08-13 "good
        # hygiene" pass -- see custom_drt.py's own docstring). Mastering
        # Display bundles BOTH the gamut step 4's radial compression
        # targets AND that display's own reference EOTF (SDR=Rec.709/
        # gamma 2.4, HDR=P3-D65/PQ, Theatrical=P3-D65/gamma 2.6). Final
        # Target's own OPTIONS depend on which Mastering Display is
        # selected -- repopulated by _populate_cdr_final_target_combo
        # whenever Mastering Display changes. Mastering Brightness only
        # means anything for an HDR (PQ) master, so it's disabled (not
        # hidden -- less layout churn on every toggle) otherwise.
        mastering_label = QLabel("Mastering Display -> Final Target")
        mastering_label.setStyleSheet("font-weight: 600;")
        outer.addWidget(mastering_label)

        mastering_display_row = QHBoxLayout()
        mastering_display_row.addWidget(QLabel("Mastering Display"))
        self.cdr_mastering_display_combo = QComboBox()
        self.cdr_mastering_display_combo.addItems(list(custom_drt.MASTERING_DISPLAYS))
        self.cdr_mastering_display_combo.currentTextChanged.connect(
            self._on_cdr_mastering_display_changed)
        mastering_display_row.addWidget(self.cdr_mastering_display_combo, stretch=1)
        outer.addLayout(mastering_display_row)

        final_target_row = QHBoxLayout()
        final_target_row.addWidget(QLabel("Final Target"))
        self.cdr_final_target_combo = QComboBox()
        self.cdr_final_target_combo.currentTextChanged.connect(self._on_cdr_field_changed)
        final_target_row.addWidget(self.cdr_final_target_combo, stretch=1)
        outer.addLayout(final_target_row)
        self._populate_cdr_final_target_combo(custom_drt.DEFAULT_MASTERING_DISPLAY)

        brightness_row = QHBoxLayout()
        brightness_row.addWidget(QLabel("Mastering Brightness"))
        self.cdr_mastering_brightness_combo = QComboBox()
        self.cdr_mastering_brightness_combo.currentIndexChanged.connect(self._on_cdr_field_changed)
        brightness_row.addWidget(self.cdr_mastering_brightness_combo, stretch=1)
        outer.addLayout(brightness_row)
        self._populate_cdr_mastering_brightness_combo(custom_drt.DEFAULT_MASTERING_DISPLAY)
        self.cdr_mastering_brightness_combo.setEnabled(False)  # SDR is the default mastering display

        reset_button = QPushButton("Reset to Defaults")
        reset_button.clicked.connect(self._on_cdr_reset_clicked)
        outer.addWidget(reset_button)

        # See _build_printer_lights_inspector's own comment on why every
        # inspector page needs a trailing vertical stretch.
        outer.addStretch(1)

        self.inspector_stack.addWidget(self._wrap_inspector_page(group))
        self._custom_drt_inspector_index = self.inspector_stack.count() - 1

    def _populate_cdr_final_target_combo(self, mastering_display, select=None):
        """Repopulates self.cdr_final_target_combo's OPTIONS from
        custom_drt.FINAL_TARGETS[mastering_display] -- Final Target's own
        valid choices genuinely depend on which Mastering Display is
        selected (Tom: "Final target EOTF and Gamut with options
        dependent on mastering display"), so this can't be a fixed,
        pre-populated combo like the other pickers in this inspector.
        Signals blocked during the clear+repopulate so this never counts
        as a live edit on its own -- only the resulting selection (which
        DOES fire currentTextChanged once, same as any other combo pick)
        does."""
        select = select or custom_drt.DEFAULT_FINAL_TARGET_FOR_DISPLAY[mastering_display]
        self.cdr_final_target_combo.blockSignals(True)
        self.cdr_final_target_combo.clear()
        self.cdr_final_target_combo.addItems(list(custom_drt.FINAL_TARGETS[mastering_display]))
        idx = self.cdr_final_target_combo.findText(select)
        self.cdr_final_target_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.cdr_final_target_combo.blockSignals(False)

    def _populate_cdr_mastering_brightness_combo(self, mastering_display, select=None):
        """Theatrical gets its own single, disabled "48 nits (reference)"
        entry -- the real SMPTE/DCI theatrical reference projection level
        (custom_drt.DEFAULT_THEATRICAL_REFERENCE_BRIGHTNESS), shown for
        context only (Tom, 2026-08-13: "more true contextually"), NOT
        wired into the render math -- Theatrical's own EOTF is a plain
        gamma 2.6 power law that never consumes mastering_brightness at
        all (only PQ does). SDR/HDR both get the normal fixed picklist,
        matching custom_drt.MASTERING_BRIGHTNESS_OPTIONS -- only HDR ever
        enables the combo (see _on_cdr_mastering_display_changed)."""
        self.cdr_mastering_brightness_combo.blockSignals(True)
        self.cdr_mastering_brightness_combo.clear()
        if mastering_display == "Theatrical":
            self.cdr_mastering_brightness_combo.addItem(
                "48 nits (reference)", custom_drt.DEFAULT_THEATRICAL_REFERENCE_BRIGHTNESS)
            self.cdr_mastering_brightness_combo.setCurrentIndex(0)
        else:
            for nits in custom_drt.MASTERING_BRIGHTNESS_OPTIONS:
                self.cdr_mastering_brightness_combo.addItem(f"{nits} nits", nits)
            idx = self.cdr_mastering_brightness_combo.findData(
                select or custom_drt.DEFAULT_MASTERING_BRIGHTNESS)
            self.cdr_mastering_brightness_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.cdr_mastering_brightness_combo.blockSignals(False)

    def _on_cdr_field_changed(self, _value=None):
        self._apply_custom_drt_live()

    def _on_cdr_mastering_display_changed(self, mastering_display=None):
        mastering_display = mastering_display or self.cdr_mastering_display_combo.currentText()
        self._populate_cdr_final_target_combo(mastering_display)
        self._populate_cdr_mastering_brightness_combo(mastering_display)
        self.cdr_mastering_brightness_combo.setEnabled(mastering_display == "HDR")
        self._apply_custom_drt_live()

    def _on_cdr_reset_clicked(self):
        # Each setValue below fires _on_cdr_field_changed (unless already
        # at that value), which applies live the same as typing would --
        # same convention as _on_gtn_reset_clicked/_on_pl_reset_clicked.
        for fields in (self._CDR_TONESCALE_FIELDS, self._CDR_DESAT_FIELDS, self._CDR_GAMUT_FIELDS):
            for key, _label, _lo, _hi, _step, _decimals, default in fields:
                self._cdr_spins[key].setValue(default)
        idx = self.cdr_mastering_display_combo.findText(custom_drt.DEFAULT_MASTERING_DISPLAY)
        if idx >= 0:
            self.cdr_mastering_display_combo.setCurrentIndex(idx)
        self._populate_cdr_final_target_combo(custom_drt.DEFAULT_MASTERING_DISPLAY)
        self._populate_cdr_mastering_brightness_combo(custom_drt.DEFAULT_MASTERING_DISPLAY)
        self.cdr_mastering_brightness_combo.setEnabled(custom_drt.DEFAULT_MASTERING_DISPLAY == "HDR")
        self._apply_custom_drt_live()

    def _apply_custom_drt_live(self):
        """Guarded by _refreshing_inspector, same convention as
        _apply_aces_drt_live/_apply_printer_lights_live -- switching TO a
        custom_drt stage also drives these widgets via
        _refresh_inspector_body, which shouldn't count as a live edit."""
        if self._refreshing_inspector:
            return
        stage = self._selected_stage()
        if stage is None or stage.kind != "custom_drt":
            return
        working_space = self.cdr_space_combo.currentText()
        mastering_display = self.cdr_mastering_display_combo.currentText()
        final_target = self.cdr_final_target_combo.currentText()
        if not working_space or not mastering_display or not final_target:
            return
        stage.params["working_colourspace"] = working_space
        for fields in (self._CDR_TONESCALE_FIELDS, self._CDR_DESAT_FIELDS, self._CDR_GAMUT_FIELDS):
            for key, _label, _lo, _hi, _step, _decimals, _default in fields:
                stage.params[key] = self._cdr_spins[key].value()
        stage.params["mastering_display"] = mastering_display
        stage.params["final_target"] = final_target
        stage.params["mastering_brightness"] = (
            self.cdr_mastering_brightness_combo.currentData() or custom_drt.DEFAULT_MASTERING_BRIGHTNESS)
        stage.label = f"CAMier DRT ({working_space} -> {mastering_display}/{final_target})"
        self._update_row_content(stage.id)
        self._emit_changed()

    # ---- inspector: ASC CDL stage -------------------------------------

    def _build_cdl_inspector(self):
        group = QGroupBox("ASC CDL")
        outer = QVBoxLayout(group)

        if not cdl.OCIO_AVAILABLE:
            label = QLabel(
                "ASC CDL stages are unavailable -- PyOpenColorIO failed to "
                f"import:\n{cdl.OCIO_IMPORT_ERROR}\n\n"
                "Install with: pip3 install opencolorio, then restart."
            )
            label.setWordWrap(True)
            outer.addWidget(label)
            self.inspector_stack.addWidget(self._wrap_inspector_page(group))
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
            master_col.addWidget(_ParamLabel(
                f"{field_label} (Master):", lambda fk=field_key: self._reset_one_cdl_field(fk)))
            master_spin = QDoubleSpinBox()
            master_spin.setRange(lo, hi)
            master_spin.setDecimals(4)
            master_spin.setSingleStep(step)
            master_spin.setValue(default)
            enable_drag_to_scrub(master_spin)
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
                enable_drag_to_scrub(spin)
                spin.valueChanged.connect(
                    lambda _v, fk=field_key: self._on_cdl_channel_spin_changed(fk))
                fine_row.addWidget(_ParamLabel(channel, lambda s=spin, d=default: s.setValue(d)))
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
        sat_row.addWidget(_ParamLabel("Sat:", lambda: self.cdl_sat_spin.setValue(1.0)))
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
        enable_drag_to_scrub(self.cdl_sat_spin)
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

        # See _build_printer_lights_inspector's own comment on why every
        # inspector page needs a trailing vertical stretch.
        outer.addStretch(1)

        self.inspector_stack.addWidget(self._wrap_inspector_page(group))
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
        for field_key in CDL_SOP_FIELD_DEFAULTS:
            self._reset_one_cdl_field(field_key)

    def _reset_one_cdl_field(self, field_key):
        """Resets just ONE SOP field's fine R/G/B spins to identity --
        _on_cdl_channel_spin_changed (already wired on each spin) then
        recomputes that field's own master/wheel position from the new
        R/G/B, so a single-field reset stays fully in sync the same way
        any other fine-spin edit does. Shared by Reset SOP to Identity
        (all three fields) and each field's own "{label} (Master):"
        label double-click (per Tom, see _ParamLabel)."""
        default = CDL_SOP_FIELD_DEFAULTS[field_key]
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
        # _update_row_content call to actually repaint the row, same as
        # _apply_aces_drt_live/_apply_colorspace_live. Was a full
        # _refresh_list() -- the measured cause of "laggy" CDL wheel/spin
        # drags, see _StageRowWidget's docstring.
        stage.label = _format_cdl_label(stage.params)
        self._update_row_content(stage.id)
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
        group = QGroupBox("Color Space Conversion")
        outer = QVBoxLayout(group)

        if not csc.OCIO_AVAILABLE:
            label = QLabel(
                "Color space conversion stages are unavailable -- "
                f"PyOpenColorIO failed to import:\n{csc.OCIO_IMPORT_ERROR}\n\n"
                "Install with: pip3 install opencolorio, then restart."
            )
            label.setWordWrap(True)
            outer.addWidget(label)
            self.inspector_stack.addWidget(self._wrap_inspector_page(group))
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

        # See _build_printer_lights_inspector's own comment on why every
        # inspector page needs a trailing vertical stretch.
        outer.addStretch(1)

        self.inspector_stack.addWidget(self._wrap_inspector_page(group))
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
        self._update_row_content(stage.id)
        self._emit_changed()

    # ---- inspector: grading_primary stage --------------------------------

    def _build_grading_primary_inspector(self):
        group = QGroupBox("Grading Primary")
        outer = QVBoxLayout(group)

        if not gpr.OCIO_AVAILABLE:
            label = QLabel(
                "Grading Primary stages are unavailable -- PyOpenColorIO failed "
                f"to import:\n{gpr.OCIO_IMPORT_ERROR}\n\n"
                "Install with: pip3 install opencolorio, then restart."
            )
            label.setWordWrap(True)
            outer.addWidget(label)
            self.inspector_stack.addWidget(self._wrap_inspector_page(group))
            self._grading_primary_inspector_index = self.inspector_stack.count() - 1
            return

        style_row = QHBoxLayout()
        style_row.addWidget(QLabel("Style:"))
        self.gpr_style_combo = QComboBox()
        for style_key, style_label in gpr.list_available_styles():
            self.gpr_style_combo.addItem(style_label, style_key)
        self.gpr_style_combo.currentIndexChanged.connect(self._on_gpr_style_changed)
        style_row.addWidget(self.gpr_style_combo, stretch=1)
        outer.addLayout(style_row)

        # Every RGBM field gets its own row: a color-balance wheel (drag
        # to tint R/G/B apart from M, per Tom -- see GRADING_WHEEL_
        # STRENGTH's own comment on the CDL-vs-here master difference)
        # plus the label and R/G/B/M spinboxes for exact numeric entry,
        # kept in sync in both directions the same way CDL's wheel/spins
        # are (see _on_gpr_wheel_changed/_on_gpr_master_changed/
        # _on_gpr_channel_spin_changed below). Only the 3 fields
        # GRADING_PRIMARY_STYLE_FIELDS says the current style actually
        # uses are ever VISIBLE at once (see _update_gpr_field_
        # visibility) -- every field's row still exists and keeps its
        # own value even while hidden, so switching styles back and
        # forth never loses whatever was dialed in.
        self._gpr_field_rows = {}    # {field_key: QWidget} for show/hide
        self._gpr_field_spins = {}   # {field_key: {"R":.., "G":.., "B":.., "M":..}}
        self._gpr_wheels = {}        # {field_key: ColorBalanceWheel}

        for field_key, field_label, is_additive in GRADING_PRIMARY_FIELD_SPECS:
            default = 0.0 if is_additive else 1.0
            lo, hi, step = GRADING_PRIMARY_ADDITIVE_RANGE if is_additive else GRADING_PRIMARY_MULT_RANGE

            row_widget = QWidget()
            field_row = QHBoxLayout(row_widget)
            field_row.setContentsMargins(0, 0, 0, 0)

            wheel = ColorBalanceWheel(diameter=GRADING_PRIMARY_WHEEL_DIAMETER)
            wheel.balance_changed.connect(
                lambda x, y, fk=field_key: self._on_gpr_wheel_changed(fk, x, y))
            field_row.addWidget(wheel)
            self._gpr_wheels[field_key] = wheel

            fields_col = QVBoxLayout()
            fields_col.addWidget(_ParamLabel(
                f"{field_label}:", lambda fk=field_key: self._reset_one_gpr_field(fk)))
            spins_row = QHBoxLayout()
            channel_spins = {}
            for channel in ("R", "G", "B", "M"):
                spin = QDoubleSpinBox()
                spin.setRange(lo, hi)
                spin.setDecimals(4)
                spin.setSingleStep(step)
                spin.setValue(default)
                enable_drag_to_scrub(spin)
                if channel == "M":
                    spin.valueChanged.connect(
                        lambda _v, fk=field_key: self._on_gpr_master_changed(fk))
                else:
                    spin.valueChanged.connect(
                        lambda _v, fk=field_key: self._on_gpr_channel_spin_changed(fk))
                spins_row.addWidget(_ParamLabel(channel, lambda s=spin, d=default: s.setValue(d)))
                spins_row.addWidget(spin)
                channel_spins[channel] = spin
            self._gpr_field_spins[field_key] = channel_spins
            fields_col.addLayout(spins_row)

            field_row.addLayout(fields_col, stretch=1)
            self._gpr_field_rows[field_key] = row_widget
            outer.addWidget(row_widget)

        pivot_row = QHBoxLayout()
        self.gpr_pivot_spin = QDoubleSpinBox()
        self.gpr_pivot_spin.setRange(-2.0, 2.0)
        self.gpr_pivot_spin.setDecimals(4)
        self.gpr_pivot_spin.setSingleStep(0.01)
        self.gpr_pivot_spin.setValue(gpr.DEFAULT_PIVOT)
        enable_drag_to_scrub(self.gpr_pivot_spin)
        self.gpr_pivot_spin.valueChanged.connect(self._on_gpr_field_changed)
        pivot_row.addWidget(_ParamLabel(
            "Pivot:", lambda: self.gpr_pivot_spin.setValue(gpr.DEFAULT_PIVOT)))
        pivot_row.addWidget(self.gpr_pivot_spin)
        outer.addLayout(pivot_row)

        sat_row = QHBoxLayout()
        self.gpr_sat_spin = QDoubleSpinBox()
        self.gpr_sat_spin.setRange(0.0, 4.0)
        self.gpr_sat_spin.setDecimals(4)
        self.gpr_sat_spin.setSingleStep(0.01)
        self.gpr_sat_spin.setValue(gpr.DEFAULT_SATURATION)
        enable_drag_to_scrub(self.gpr_sat_spin)
        self.gpr_sat_spin.valueChanged.connect(self._on_gpr_field_changed)
        sat_row.addWidget(_ParamLabel(
            "Saturation:", lambda: self.gpr_sat_spin.setValue(gpr.DEFAULT_SATURATION)))
        sat_row.addWidget(self.gpr_sat_spin)
        outer.addLayout(sat_row)

        reset_button = QPushButton("Reset to Identity")
        reset_button.clicked.connect(self._on_gpr_reset_clicked)
        outer.addWidget(reset_button)

        # See _build_printer_lights_inspector's own comment on why every
        # inspector page needs a trailing vertical stretch. Only 3 of the
        # 7 fields above are ever visible at once (see
        # _update_gpr_field_visibility), so without this the leftover
        # height from the 4 hidden rows would otherwise stretch the
        # visible ones even further apart.
        outer.addStretch(1)

        self.inspector_stack.addWidget(self._wrap_inspector_page(group))
        self._grading_primary_inspector_index = self.inspector_stack.count() - 1
        self._update_gpr_field_visibility()

    def _update_gpr_field_visibility(self):
        style = self.gpr_style_combo.currentData() or gpr.DEFAULT_STYLE
        visible_fields = set(GRADING_PRIMARY_STYLE_FIELDS.get(style, ()))
        for field_key, row_widget in self._gpr_field_rows.items():
            row_widget.setVisible(field_key in visible_fields)

    def _on_gpr_style_changed(self, _index):
        self._update_gpr_field_visibility()
        self._apply_gpr_live()

    def _on_gpr_field_changed(self, _value=None):
        self._apply_gpr_live()

    # ---- Grading Primary wheel/fine-spinbox sync (see color_balance.py,
    # and GRADING_WHEEL_STRENGTH's own comment on why M can't be
    # recomputed from mean(R,G,B) the way CDL's master is) --------------

    def _on_gpr_wheel_changed(self, field_key, x, y):
        """Puck dragged -- M (master) stays put, R/G/B fan out around it."""
        master = self._gpr_field_spins[field_key]["M"].value()
        r, g, b = cb.rgb_from_master_and_wheel(master, x, y, strength=GRADING_WHEEL_STRENGTH)
        self._set_gpr_fine_spins(field_key, r, g, b)
        self._apply_gpr_live()

    def _on_gpr_master_changed(self, field_key):
        """M spinbox edited directly -- wheel position (the tint) is
        unchanged, R/G/B all shift together to the new master, same
        "editing master preserves the balance" behavior CDL's own master
        spin gives (see _on_cdl_master_changed) -- just applied to this
        field's own already-existing M spinbox rather than a separate
        UI-only one."""
        wheel = self._gpr_wheels[field_key]
        x, y = wheel.balance()
        master = self._gpr_field_spins[field_key]["M"].value()
        r, g, b = cb.rgb_from_master_and_wheel(master, x, y, strength=GRADING_WHEEL_STRENGTH)
        self._set_gpr_fine_spins(field_key, r, g, b)
        self._apply_gpr_live()

    def _on_gpr_channel_spin_changed(self, field_key):
        """A fine R/G/B spinbox was typed into directly -- the exact typed
        values are what gets written to stage.params (see _apply_gpr_live,
        which reads these spinboxes directly); M is left completely alone
        here (unlike CDL's own fine-spin handler, which recomputes master
        as mean(R,G,B) -- wrong for this field, see GRADING_WHEEL_
        STRENGTH's own comment on why M is a real, independently stored
        value, not derived). This just back-computes a best-fit wheel
        position so the wheel visually reflects wherever R/G/B's spread
        around the CURRENT M now points (color_balance.
        wheel_from_rgb_given_master)."""
        spins = self._gpr_field_spins[field_key]
        r, g, b = spins["R"].value(), spins["G"].value(), spins["B"].value()
        master = spins["M"].value()
        x, y = cb.wheel_from_rgb_given_master(r, g, b, master, strength=GRADING_WHEEL_STRENGTH)
        self._gpr_wheels[field_key].set_balance(x, y, emit=False)
        self._apply_gpr_live()

    def _set_gpr_fine_spins(self, field_key, r, g, b):
        spins = self._gpr_field_spins[field_key]
        for channel, value in zip(("R", "G", "B"), (r, g, b)):
            spin = spins[channel]
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    def _on_gpr_reset_clicked(self):
        for field_key, _label, _is_additive in GRADING_PRIMARY_FIELD_SPECS:
            self._reset_one_gpr_field(field_key, apply=False)
        self.gpr_pivot_spin.setValue(gpr.DEFAULT_PIVOT)
        self.gpr_sat_spin.setValue(gpr.DEFAULT_SATURATION)
        self._apply_gpr_live()

    def _reset_one_gpr_field(self, field_key, apply=True):
        """Resets just ONE field's R/G/B/M spins + wheel to identity.
        Directly blockSignals + set every spin AND the wheel here,
        rather than relying on the sync handlers above to cascade (like
        CDL's own per-field reset does) -- with M independently stored,
        resetting R/G/B before M would transiently recompute a wrong
        wheel position, and resetting M last would then shift the
        just-reset R/G/B away from identity again (see
        _on_gpr_master_changed). Setting everything directly first and
        applying once at the end sidesteps that ordering hazard
        entirely. `apply=False` lets _on_gpr_reset_clicked call this
        once per field without re-applying (and re-baking) after every
        single one -- it does its own single apply at the end instead."""
        default = GRADING_PRIMARY_FIELD_DEFAULTS[field_key]
        for spin in self._gpr_field_spins[field_key].values():
            spin.blockSignals(True)
            spin.setValue(default)
            spin.blockSignals(False)
        self._gpr_wheels[field_key].set_balance(0.0, 0.0, emit=False)
        if apply:
            self._apply_gpr_live()

    def _apply_gpr_live(self):
        """Reads every field/pivot/saturation spinbox plus the style combo
        and writes into the selected stage's params -- called after every
        edit instead of needing an Update Stage click, same convention as
        _apply_cdl_live/_apply_colorspace_live. Guarded by
        _refreshing_inspector (colorspace's simpler style, rather than
        CDL's per-widget blockSignals) since switching TO a
        grading_primary stage also drives these spinboxes via
        _refresh_inspector_body, which shouldn't count as a live edit."""
        if self._refreshing_inspector:
            return
        stage = self._selected_stage()
        if stage is None or stage.kind != "grading_primary":
            return
        for field_key, _label, _is_additive in GRADING_PRIMARY_FIELD_SPECS:
            spins = self._gpr_field_spins[field_key]
            stage.params[field_key] = tuple(spins[c].value() for c in ("R", "G", "B", "M"))
        stage.params["pivot"] = self.gpr_pivot_spin.value()
        stage.params["saturation"] = self.gpr_sat_spin.value()
        stage.params["style"] = self.gpr_style_combo.currentData() or gpr.DEFAULT_STYLE
        stage.label = _format_grading_primary_label(stage.params)
        self._update_row_content(stage.id)
        self._emit_changed()

    # ---- inspector: exposure_contrast stage ------------------------------

    def _build_exposure_contrast_inspector(self):
        group = QGroupBox("Exposure/Contrast")
        outer = QVBoxLayout(group)

        if not ec.OCIO_AVAILABLE:
            label = QLabel(
                "Exposure/Contrast stages are unavailable -- PyOpenColorIO failed "
                f"to import:\n{ec.OCIO_IMPORT_ERROR}\n\n"
                "Install with: pip3 install opencolorio, then restart."
            )
            label.setWordWrap(True)
            outer.addWidget(label)
            self.inspector_stack.addWidget(self._wrap_inspector_page(group))
            self._exposure_contrast_inspector_index = self.inspector_stack.count() - 1
            return

        style_row = QHBoxLayout()
        style_row.addWidget(QLabel("Style:"))
        self.ec_style_combo = QComboBox()
        for style_key, style_label in ec.list_available_styles():
            self.ec_style_combo.addItem(style_label, style_key)
        self.ec_style_combo.currentIndexChanged.connect(self._on_ec_field_changed)
        style_row.addWidget(self.ec_style_combo, stretch=1)
        outer.addLayout(style_row)

        # Simplest inspector of the four Tier 1 kinds -- just exposure/
        # contrast/gamma, each a single scalar (no per-channel R/G/B/M
        # here, unlike grading_primary's RGBM fields). Pivot/
        # logExposureStep/logMidGray are deliberately not exposed here --
        # see exposure_contrast.py's own docstring on why (not dynamic
        # GPU properties, would force a shader recompile per edit).
        self._ec_field_spins = {}
        for field_key, field_label, default, lo, hi in (
            ("exposure", "Exposure", ec.IDENTITY_EXPOSURE, -8.0, 8.0),
            ("contrast", "Contrast", ec.IDENTITY_CONTRAST, 0.0, 4.0),
            ("gamma", "Gamma", ec.IDENTITY_GAMMA, 0.01, 4.0),
        ):
            row = QHBoxLayout()
            spin = QDoubleSpinBox()
            spin.setRange(lo, hi)
            spin.setDecimals(4)
            spin.setSingleStep(0.01)
            spin.setValue(default)
            enable_drag_to_scrub(spin)
            spin.valueChanged.connect(self._on_ec_field_changed)
            row.addWidget(_ParamLabel(f"{field_label}:", lambda s=spin, d=default: s.setValue(d)))
            row.addWidget(spin, stretch=1)
            self._ec_field_spins[field_key] = spin
            outer.addLayout(row)

        reset_button = QPushButton("Reset to Identity")
        reset_button.clicked.connect(self._on_ec_reset_clicked)
        outer.addWidget(reset_button)

        # See _build_printer_lights_inspector's own comment on why every
        # inspector page needs a trailing vertical stretch.
        outer.addStretch(1)

        self.inspector_stack.addWidget(self._wrap_inspector_page(group))
        self._exposure_contrast_inspector_index = self.inspector_stack.count() - 1

    def _on_ec_field_changed(self, _value=None):
        self._apply_ec_live()

    def _on_ec_reset_clicked(self):
        # Each setValue below fires _on_ec_field_changed (unless already
        # at that value), which applies live the same as typing would --
        # same convention as _on_cdl_reset_sop_clicked/_on_gpr_reset_clicked.
        self._ec_field_spins["exposure"].setValue(ec.IDENTITY_EXPOSURE)
        self._ec_field_spins["contrast"].setValue(ec.IDENTITY_CONTRAST)
        self._ec_field_spins["gamma"].setValue(ec.IDENTITY_GAMMA)

    def _apply_ec_live(self):
        """Guarded by _refreshing_inspector, same convention as
        _apply_gpr_live/_apply_colorspace_live -- switching TO an
        exposure_contrast stage also drives these widgets via
        _refresh_inspector_body, which shouldn't count as a live edit."""
        if self._refreshing_inspector:
            return
        stage = self._selected_stage()
        if stage is None or stage.kind != "exposure_contrast":
            return
        stage.params["exposure"] = self._ec_field_spins["exposure"].value()
        stage.params["contrast"] = self._ec_field_spins["contrast"].value()
        stage.params["gamma"] = self._ec_field_spins["gamma"].value()
        stage.params["style"] = self.ec_style_combo.currentData() or ec.DEFAULT_STYLE
        stage.label = _format_exposure_contrast_label(stage.params)
        self._update_row_content(stage.id)
        self._emit_changed()

    # ---- inspector: grading_tone stage ------------------------------------

    def _build_grading_tone_inspector(self):
        """Largest of the four Tier 1 inspectors (5 ranges x 6 fields each,
        see OCIO_TIER1_GRADING_OPERATORS_PLAN_2026-08-09.md's own note on
        this) -- one compact QGroupBox row per range (R/G/B/M gain +
        Start/Width, all on one line) built from a shared loop rather than
        5 hand-laid-out sections, same "don't repeat the widget-building
        code 5 times" reasoning as GRADING_PRIMARY_FIELD_SPECS' loop."""
        group = QGroupBox("Grading Tone")
        outer = QVBoxLayout(group)

        if not gtn.OCIO_AVAILABLE:
            label = QLabel(
                "Grading Tone stages are unavailable -- PyOpenColorIO failed "
                f"to import:\n{gtn.OCIO_IMPORT_ERROR}\n\n"
                "Install with: pip3 install opencolorio, then restart."
            )
            label.setWordWrap(True)
            outer.addWidget(label)
            self.inspector_stack.addWidget(self._wrap_inspector_page(group))
            self._grading_tone_inspector_index = self.inspector_stack.count() - 1
            return

        style_row = QHBoxLayout()
        style_row.addWidget(QLabel("Style:"))
        self.gtn_style_combo = QComboBox()
        for style_key, style_label in gtn.list_available_styles():
            self.gtn_style_combo.addItem(style_label, style_key)
        self.gtn_style_combo.currentIndexChanged.connect(self._on_gtn_field_changed)
        style_row.addWidget(self.gtn_style_combo, stretch=1)
        outer.addLayout(style_row)

        # Each range also gets a color-balance wheel now (per Tom, R/G/B
        # gain fanned out around M -- same GRADING_WHEEL_STRENGTH/
        # wheel_from_rgb_given_master treatment Grading Primary's own
        # fields just got, since M here is equally a real, independently
        # stored 4th value, not derived from mean(R,G,B) -- see that
        # module-level comment). Smaller diameter than Grading Primary's
        # (GRADING_TONE_WHEEL_DIAMETER, see its own comment) to keep 5
        # stacked ranges from bloating this already-the-largest inspector.
        self._gtn_range_spins = {}  # {range_key: {"R":.., "G":.., "B":.., "M":.., "Start":.., "Width":..}}
        self._gtn_wheels = {}       # {range_key: ColorBalanceWheel}
        for range_key in gtn.RANGE_KEYS:
            r, g, b, m, start, width = gtn.DEFAULT_RANGE_VALUES[range_key]
            defaults = {"R": r, "G": g, "B": b, "M": m, "Start": start, "Width": width}
            # R/G/B/M's range is gtn.GAIN_BOUNDS[range_key] -- OCIO's own
            # validate() bound, NOT the same for every range (shadows/
            # highlights are tighter than blacks/midtones/whites) -- see
            # that constant's own comment for why this matters: a value
            # outside it raised a real ocio.Exception INSIDE paintGL on a
            # live slider drag, which crashed the app before paintGL grew
            # its own exception guard. Start/Width have no such per-field
            # bound (their validity depends on the OTHER ranges' values
            # too) -- Width's floor here is just "can't be negative," a
            # reasonable UI constraint on its own terms, not a guarantee
            # of validity (gpu_stack_renderer.py's own exception handling
            # around setGradingTone is what actually covers that case).
            gain_lo, gain_hi = gtn.GAIN_BOUNDS[range_key]

            range_row = QHBoxLayout()
            wheel = ColorBalanceWheel(diameter=GRADING_TONE_WHEEL_DIAMETER)
            wheel.balance_changed.connect(
                lambda x, y, rk=range_key: self._on_gtn_wheel_changed(rk, x, y))
            range_row.addWidget(wheel)
            self._gtn_wheels[range_key] = wheel

            range_col = QVBoxLayout()
            # A plain bold label, not a nested QGroupBox -- every other
            # inspector page in this panel (CDL, ACES DRT, Grading
            # Primary, ...) uses exactly ONE outer QGroupBox for the
            # whole page and plain rows/labels inside it; a second,
            # nested bordered box per range here was the one place that
            # broke that convention (5 extra boxes-within-a-box).
            range_label = _ParamLabel(
                GRADING_TONE_RANGE_LABELS[range_key],
                lambda rk=range_key: self._reset_one_gtn_range(rk))
            range_label.setStyleSheet("font-weight: 600;")
            range_col.addWidget(range_label)
            row = QHBoxLayout()
            channel_spins = {}
            for channel, lo, hi in (
                ("R", gain_lo, gain_hi), ("G", gain_lo, gain_hi),
                ("B", gain_lo, gain_hi), ("M", gain_lo, gain_hi),
                ("Start", -2.0, 2.0), ("Width", 0.0, 2.0),
            ):
                spin = QDoubleSpinBox()
                spin.setRange(lo, hi)
                spin.setDecimals(4)
                spin.setSingleStep(0.01)
                spin.setValue(defaults[channel])
                enable_drag_to_scrub(spin)
                if channel == "M":
                    spin.valueChanged.connect(
                        lambda _v, rk=range_key: self._on_gtn_master_changed(rk))
                elif channel in ("R", "G", "B"):
                    spin.valueChanged.connect(
                        lambda _v, rk=range_key: self._on_gtn_channel_spin_changed(rk))
                else:  # Start/Width -- plain fields, no wheel involvement
                    spin.valueChanged.connect(self._on_gtn_field_changed)
                # A single isolated reset here (unlike the ALL-AT-ONCE
                # bulk reset _reset_one_gtn_range's own docstring
                # explains the M-vs-R/G/B ordering hazard for) is always
                # well-defined even for R/G/B/M -- each channel's own
                # already-wired valueChanged handler (master-changed/
                # channel-spin-changed/field-changed above) does exactly
                # what a normal single-value edit would.
                row.addWidget(_ParamLabel(channel, lambda s=spin, d=defaults[channel]: s.setValue(d)))
                row.addWidget(spin)
                channel_spins[channel] = spin
            self._gtn_range_spins[range_key] = channel_spins
            range_col.addLayout(row)

            range_row.addLayout(range_col, stretch=1)
            outer.addLayout(range_row)

        scontrast_row = QHBoxLayout()
        self.gtn_scontrast_spin = QDoubleSpinBox()
        self.gtn_scontrast_spin.setRange(*gtn.SCONTRAST_BOUNDS)
        self.gtn_scontrast_spin.setDecimals(4)
        self.gtn_scontrast_spin.setSingleStep(0.01)
        self.gtn_scontrast_spin.setValue(gtn.DEFAULT_SCONTRAST)
        enable_drag_to_scrub(self.gtn_scontrast_spin)
        self.gtn_scontrast_spin.valueChanged.connect(self._on_gtn_field_changed)
        scontrast_row.addWidget(_ParamLabel(
            "S-Contrast:", lambda: self.gtn_scontrast_spin.setValue(gtn.DEFAULT_SCONTRAST)))
        scontrast_row.addWidget(self.gtn_scontrast_spin, stretch=1)
        outer.addLayout(scontrast_row)

        reset_button = QPushButton("Reset to Identity")
        reset_button.clicked.connect(self._on_gtn_reset_clicked)
        outer.addWidget(reset_button)

        # See _build_printer_lights_inspector's own comment -- this is
        # usually the tallest page (so this stretch rarely has anything
        # left to absorb), but added for the same consistency reason: if
        # a taller page is ever added later, this one shouldn't start
        # silently stretching its own rows apart too.
        outer.addStretch(1)

        self.inspector_stack.addWidget(self._wrap_inspector_page(group))
        self._grading_tone_inspector_index = self.inspector_stack.count() - 1

    def _on_gtn_field_changed(self, _value=None):
        self._apply_gtn_live()

    # ---- Grading Tone wheel/fine-spinbox sync -- identical reasoning to
    # Grading Primary's own (see GRADING_WHEEL_STRENGTH's own comment) --
    # just keyed by range_key instead of field_key. ---------------------

    def _on_gtn_wheel_changed(self, range_key, x, y):
        master = self._gtn_range_spins[range_key]["M"].value()
        r, g, b = cb.rgb_from_master_and_wheel(master, x, y, strength=GRADING_WHEEL_STRENGTH)
        self._set_gtn_fine_spins(range_key, r, g, b)
        self._apply_gtn_live()

    def _on_gtn_master_changed(self, range_key):
        wheel = self._gtn_wheels[range_key]
        x, y = wheel.balance()
        master = self._gtn_range_spins[range_key]["M"].value()
        r, g, b = cb.rgb_from_master_and_wheel(master, x, y, strength=GRADING_WHEEL_STRENGTH)
        self._set_gtn_fine_spins(range_key, r, g, b)
        self._apply_gtn_live()

    def _on_gtn_channel_spin_changed(self, range_key):
        spins = self._gtn_range_spins[range_key]
        r, g, b = spins["R"].value(), spins["G"].value(), spins["B"].value()
        master = spins["M"].value()
        x, y = cb.wheel_from_rgb_given_master(r, g, b, master, strength=GRADING_WHEEL_STRENGTH)
        self._gtn_wheels[range_key].set_balance(x, y, emit=False)
        self._apply_gtn_live()

    def _set_gtn_fine_spins(self, range_key, r, g, b):
        spins = self._gtn_range_spins[range_key]
        for channel, value in zip(("R", "G", "B"), (r, g, b)):
            spin = spins[channel]
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    def _on_gtn_reset_clicked(self):
        for range_key in gtn.RANGE_KEYS:
            self._reset_one_gtn_range(range_key, apply=False)
        self.gtn_scontrast_spin.setValue(gtn.DEFAULT_SCONTRAST)
        self._apply_gtn_live()

    def _reset_one_gtn_range(self, range_key, apply=True):
        """Resets just ONE range's R/G/B/M/Start/Width spins + wheel to
        identity. Direct blockSignals + set every spin AND the wheel
        here, same ordering-hazard reasoning as _reset_one_gpr_field (M
        is independent, so letting the sync handlers above cascade
        during a reset can leave R/G/B not actually at identity).
        `apply=False` lets _on_gtn_reset_clicked reset all 5 ranges
        without re-applying (and re-baking) after each one -- see
        _reset_one_gpr_field's own docstring for the same pattern."""
        r, g, b, m, start, width = gtn.DEFAULT_RANGE_VALUES[range_key]
        spins = self._gtn_range_spins[range_key]
        for channel, value in zip(("R", "G", "B", "M", "Start", "Width"), (r, g, b, m, start, width)):
            spin = spins[channel]
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        self._gtn_wheels[range_key].set_balance(0.0, 0.0, emit=False)
        if apply:
            self._apply_gtn_live()

    def _apply_gtn_live(self):
        """Guarded by _refreshing_inspector, same convention as
        _apply_gpr_live/_apply_ec_live -- switching TO a grading_tone
        stage also drives these widgets via _refresh_inspector_body,
        which shouldn't count as a live edit."""
        if self._refreshing_inspector:
            return
        stage = self._selected_stage()
        if stage is None or stage.kind != "grading_tone":
            return
        for range_key in gtn.RANGE_KEYS:
            spins = self._gtn_range_spins[range_key]
            stage.params[range_key] = tuple(
                spins[c].value() for c in ("R", "G", "B", "M", "Start", "Width"))
        stage.params["scontrast"] = self.gtn_scontrast_spin.value()
        stage.params["style"] = self.gtn_style_combo.currentData() or gtn.DEFAULT_STYLE
        stage.label = _format_grading_tone_label(stage.params)
        self._update_row_content(stage.id)
        self._emit_changed()

    # ---- inspector: grading_rgb_curve stage --------------------------------

    def _build_grading_rgb_curve_inspector(self):
        """Curve editor for a 'grading_rgb_curve' stage's four channel
        curves -- unlike every other Tier 1 inspector (all spin-box
        driven), the actual value-editing widget here is
        CurveEditorWidget (see that module's own docstring on why it's
        hand-rolled rather than pyqtgraph-based, per a background
        research pass that read pyqtgraph's own installed source). The
        channel selector row picks which ONE of the four curves is
        currently draggable; all four are always held by the widget and
        always written back to stage.params on every edit (see
        CurveEditorWidget's own docstring on why -- same "write the whole
        param block back" convention grading_tone's 5 ranges follow)."""
        group = QGroupBox("Grading RGB Curve")
        outer = QVBoxLayout(group)

        if not grc.OCIO_AVAILABLE:
            label = QLabel(
                "Grading RGB Curve stages are unavailable -- PyOpenColorIO failed "
                f"to import:\n{grc.OCIO_IMPORT_ERROR}\n\n"
                "Install with: pip3 install opencolorio, then restart."
            )
            label.setWordWrap(True)
            outer.addWidget(label)
            self.inspector_stack.addWidget(self._wrap_inspector_page(group))
            self._grading_rgb_curve_inspector_index = self.inspector_stack.count() - 1
            return

        style_row = QHBoxLayout()
        style_row.addWidget(QLabel("Style:"))
        self.grc_style_combo = QComboBox()
        for style_key, style_label in grc.list_available_styles():
            self.grc_style_combo.addItem(style_label, style_key)
        self.grc_style_combo.currentIndexChanged.connect(self._on_grc_field_changed)
        style_row.addWidget(self.grc_style_combo, stretch=1)
        outer.addLayout(style_row)

        channel_row = QHBoxLayout()
        channel_row.addWidget(QLabel("Channel:"))
        self.grc_channel_buttons = {}
        self._grc_channel_button_group = QButtonGroup(group)
        self._grc_channel_button_group.setExclusive(True)
        for channel in CHANNEL_ORDER:
            button = QToolButton()
            button.setText(CHANNEL_LABELS[channel])
            button.setCheckable(True)
            color = CHANNEL_COLORS[channel]
            button.setStyleSheet(
                f"QToolButton {{ color: rgb({color.red()},{color.green()},{color.blue()}); "
                "font-weight: 600; }"
                f"QToolButton:checked {{ background-color: rgba({color.red()},{color.green()},"
                f"{color.blue()},60); border: 1px solid rgb({color.red()},{color.green()},{color.blue()}); }}"
            )
            button.clicked.connect(lambda _checked, ch=channel: self._on_grc_channel_button_clicked(ch))
            self._grc_channel_button_group.addButton(button)
            self.grc_channel_buttons[channel] = button
            channel_row.addWidget(button)
        self.grc_channel_buttons["master"].setChecked(True)
        channel_row.addStretch(1)
        outer.addLayout(channel_row)

        self.grc_curve_editor = CurveEditorWidget()
        self.grc_curve_editor.pointsChanged.connect(self._on_grc_points_changed)
        outer.addWidget(self.grc_curve_editor, stretch=1)

        reset_button = QPushButton("Reset Channel to Identity")
        reset_button.clicked.connect(lambda: self.grc_curve_editor.reset_active_channel())
        outer.addWidget(reset_button)

        self.inspector_stack.addWidget(self._wrap_inspector_page(group))
        self._grading_rgb_curve_inspector_index = self.inspector_stack.count() - 1

    def _on_grc_channel_button_clicked(self, channel):
        self.grc_curve_editor.set_active_channel(channel)

    def _on_grc_field_changed(self, _value=None):
        self._apply_grc_live()

    def _on_grc_points_changed(self, _channel):
        self._apply_grc_live()

    def _apply_grc_live(self):
        """Guarded by _refreshing_inspector, same convention as
        _apply_ec_live/_apply_gtn_live -- switching TO a grading_rgb_curve
        stage also drives the curve editor's points via
        _refresh_inspector_body, which shouldn't count as a live edit.
        Writes ALL FOUR channels back every time (not just whichever one
        CurveEditorWidget's pointsChanged just reported), same "whole
        param block on every edit" convention grading_tone's ranges use --
        the widget itself always holds all four curves regardless of
        which one is currently active/draggable."""
        if self._refreshing_inspector:
            return
        stage = self._selected_stage()
        if stage is None or stage.kind != "grading_rgb_curve":
            return
        for channel in grc.RGB_CHANNELS:
            stage.params[channel] = tuple(self.grc_curve_editor.get_points(channel))
        stage.params["style"] = self.grc_style_combo.currentData() or grc.DEFAULT_STYLE
        stage.label = _format_grading_rgb_curve_label(stage.params)
        self._update_row_content(stage.id)
        self._emit_changed()

    # ---- inspector: color_warper stage (WARPier) -------------------------

    def _build_color_warper_inspector(self):
        group = QGroupBox("WARPier")
        outer = QVBoxLayout(group)

        space_row = QHBoxLayout()
        space_row.addWidget(QLabel("Working Color Space:"))
        self.cw_space_combo = QComboBox()
        self._cw_using_ocio = aces_drt.OCIO_AVAILABLE
        if self._cw_using_ocio:
            self.cw_space_combo.addItems(aces_drt.list_working_colourspaces())
        else:
            self.cw_space_combo.addItems(list_available_colourspaces())
        # Real-time (per Tom), same convention as Printer Lights/CAMier DRT
        # -- no separate Update Stage button, every change below applies
        # and re-bakes immediately (see _apply_color_warper_live).
        self.cw_space_combo.currentTextChanged.connect(self._on_cw_space_changed)
        space_row.addWidget(self.cw_space_combo, stretch=1)
        outer.addLayout(space_row)

        # Wheel and cloud side by side (per Tom's own follow-up on the
        # layout) -- the wheel keeps its natural fixed size, the cloud
        # stretches to fill whatever width remains. The cloud is an
        # idealized gamut-following reference blob (see
        # color_warper_cloud_widget.py's own docstring --
        # color_warper.warped_gamut_blob_points) that reacts live to this
        # stage's own points/wake/dropoff (see set_warp calls below), but
        # is NOT derived from the loaded image or the Working Color Space
        # combo above at all.
        wheel_row = QHBoxLayout()
        wheel_row.addStretch(1)
        self.cw_wheel = WarpierWheel()
        self.cw_wheel.points_changed.connect(self._on_cw_points_changed)
        wheel_row.addWidget(self.cw_wheel)

        self.cw_cloud = WarpierCloudWidget()
        wheel_row.addWidget(self.cw_cloud, stretch=1)
        outer.addLayout(wheel_row)

        wake_row = QHBoxLayout()
        self.cw_wake_spin = QDoubleSpinBox()
        self.cw_wake_spin.setRange(5.0, 90.0)
        self.cw_wake_spin.setDecimals(1)
        self.cw_wake_spin.setSingleStep(1.0)
        self.cw_wake_spin.setValue(cw.DEFAULT_WAKE)
        enable_drag_to_scrub(self.cw_wake_spin)
        self.cw_wake_spin.valueChanged.connect(self._on_cw_wake_changed)
        wake_row.addWidget(_ParamLabel(
            "Wake:", lambda: self.cw_wake_spin.setValue(cw.DEFAULT_WAKE)))
        wake_row.addWidget(self.cw_wake_spin, stretch=1)
        outer.addLayout(wake_row)

        dropoff_row = QHBoxLayout()
        self.cw_dropoff_spin = QDoubleSpinBox()
        self.cw_dropoff_spin.setRange(0.0, 1.0)
        self.cw_dropoff_spin.setDecimals(2)
        self.cw_dropoff_spin.setSingleStep(0.05)
        self.cw_dropoff_spin.setValue(cw.DEFAULT_DROPOFF)
        enable_drag_to_scrub(self.cw_dropoff_spin)
        self.cw_dropoff_spin.valueChanged.connect(self._on_cw_dropoff_changed)
        dropoff_row.addWidget(_ParamLabel(
            "Drop-off:", lambda: self.cw_dropoff_spin.setValue(cw.DEFAULT_DROPOFF)))
        dropoff_row.addWidget(self.cw_dropoff_spin, stretch=1)
        outer.addLayout(dropoff_row)

        reset_button = QPushButton("Reset to Identity")
        reset_button.clicked.connect(self._on_cw_reset_clicked)
        outer.addWidget(reset_button)

        # See _build_printer_lights_inspector's own comment on why every
        # inspector page needs a trailing vertical stretch.
        outer.addStretch(1)

        self.inspector_stack.addWidget(self._wrap_inspector_page(group))
        self._color_warper_inspector_index = self.inspector_stack.count() - 1

    def _on_cw_points_changed(self, _points):
        self._apply_color_warper_live()

    def _on_cw_wake_changed(self, value):
        self.cw_wheel.set_wake(value)
        self._apply_color_warper_live()

    def _on_cw_dropoff_changed(self, value):
        self.cw_wheel.set_dropoff(value)
        self._apply_color_warper_live()

    def _on_cw_space_changed(self, _text):
        self._apply_color_warper_live()

    def _on_cw_reset_clicked(self):
        self.cw_wheel.reset()  # emits points_changed -> _apply_color_warper_live
        self.cw_wake_spin.setValue(cw.DEFAULT_WAKE)
        self.cw_dropoff_spin.setValue(cw.DEFAULT_DROPOFF)

    def _apply_color_warper_live(self):
        """Guarded by _refreshing_inspector, same convention as
        _apply_gpr_live/_apply_grc_live -- switching TO a color_warper
        stage also drives the wheel/spinboxes via _refresh_inspector_body,
        which shouldn't count as a live edit."""
        if self._refreshing_inspector:
            return
        stage = self._selected_stage()
        if stage is None or stage.kind != "color_warper":
            return
        stage.params["points"] = self.cw_wheel.points()
        stage.params["wake"] = self.cw_wake_spin.value()
        stage.params["dropoff"] = self.cw_dropoff_spin.value()
        stage.params["working_colourspace"] = self.cw_space_combo.currentText()
        self.cw_cloud.set_warp(stage.params["points"], stage.params["wake"], stage.params["dropoff"])
        self._update_row_content(stage.id)
        self._emit_changed()

    # ---- inspector: keyer stage -------------------------------------

    def _build_keyer_inspector(self):
        # Title updated live to show this stage's own matte letter (per
        # Tom: "it needs to be clear what the letter code for each
        # matte is") -- see _refresh_inspector_body's own "keyer" branch,
        # which calls self.ky_group.setTitle(...) with the current
        # letter every time a keyer stage becomes selected (its own
        # letter can change on reorder, so this can't just be set once
        # here at construction time).
        self.ky_group = QGroupBox("Keyer")
        group = self.ky_group
        outer = QVBoxLayout(group)

        # Working Color Space -- same combo, same OCIO-vs-colour-science
        # auto-dispatch, as WARPier's own cw_space_combo (see
        # _build_color_warper_inspector) -- the qualifier below now reads
        # real OKLCh values decoded from THIS space (see keyer.py's own
        # docstring for why raw HSL on clipped [0,1] RGB was wrong on
        # this app's own log/HDR working spaces).
        ky_space_row = QHBoxLayout()
        ky_space_row.addWidget(QLabel("Working Color Space:"))
        self.ky_space_combo = QComboBox()
        self._ky_using_ocio = aces_drt.OCIO_AVAILABLE
        if self._ky_using_ocio:
            self.ky_space_combo.addItems(aces_drt.list_working_colourspaces())
        else:
            self.ky_space_combo.addItems(list_available_colourspaces())
        self.ky_space_combo.currentTextChanged.connect(self._apply_keyer_live)
        ky_space_row.addWidget(self.ky_space_combo, stretch=1)
        outer.addLayout(ky_space_row)

        hue_row = QHBoxLayout()
        self.ky_hue_center_spin = QDoubleSpinBox()
        self.ky_hue_center_spin.setRange(0.0, 360.0)
        self.ky_hue_center_spin.setDecimals(1)
        self.ky_hue_center_spin.setSingleStep(1.0)
        self.ky_hue_center_spin.setValue(ky.DEFAULT_HUE_CENTER)
        self.ky_hue_center_spin.setWrapping(True)
        enable_drag_to_scrub(self.ky_hue_center_spin)
        self.ky_hue_center_spin.valueChanged.connect(self._apply_keyer_live)
        hue_row.addWidget(_ParamLabel(
            "Hue Center:", lambda: self.ky_hue_center_spin.setValue(ky.DEFAULT_HUE_CENTER)))
        hue_row.addWidget(self.ky_hue_center_spin, stretch=1)

        self.ky_hue_width_spin = QDoubleSpinBox()
        self.ky_hue_width_spin.setRange(1.0, 180.0)
        self.ky_hue_width_spin.setDecimals(1)
        self.ky_hue_width_spin.setSingleStep(1.0)
        self.ky_hue_width_spin.setValue(ky.DEFAULT_HUE_WIDTH)
        enable_drag_to_scrub(self.ky_hue_width_spin)
        self.ky_hue_width_spin.valueChanged.connect(self._apply_keyer_live)
        hue_row.addWidget(_ParamLabel(
            "Hue Width:", lambda: self.ky_hue_width_spin.setValue(ky.DEFAULT_HUE_WIDTH)))
        hue_row.addWidget(self.ky_hue_width_spin, stretch=1)

        # Eyedropper (per Tom) -- arms WipeCompareWidget's own one-shot
        # click-to-sample (see set_eyedropper_requester's own docstring
        # for the getter-based cross-panel wiring), sets Hue Center ONLY
        # from whatever's actually on screen at the clicked pixel (see
        # _on_ky_pixel_picked) -- width/chroma/lightness stay whatever
        # the user already has them, a deliberate "click to say which
        # hue, sliders stay a deliberate choice" v1 scope decision.
        self.ky_pick_button = QPushButton("Pick")
        self.ky_pick_button.setToolTip(
            "Click, then click a pixel in the Viewer to set Hue Center "
            "from it.")
        self.ky_pick_button.clicked.connect(self._on_ky_pick_clicked)
        hue_row.addWidget(self.ky_pick_button)
        outer.addLayout(hue_row)

        chroma_row = QHBoxLayout()
        self.ky_chroma_min_spin = QDoubleSpinBox()
        self.ky_chroma_max_spin = QDoubleSpinBox()
        for spin, default in ((self.ky_chroma_min_spin, ky.DEFAULT_CHROMA_MIN),
                               (self.ky_chroma_max_spin, ky.DEFAULT_CHROMA_MAX)):
            # OKLCh chroma's own natural range runs well past 1.0 isn't
            # right either -- see keyer.DEFAULT_CHROMA_MAX's own comment
            # for why 0.5 (comfortable headroom above default 0.4) is
            # this spin box's own ceiling rather than reusing 1.0 the
            # way HSL saturation could.
            spin.setRange(0.0, 0.5)
            spin.setDecimals(3)
            spin.setSingleStep(0.01)
            spin.setValue(default)
            enable_drag_to_scrub(spin)
            spin.valueChanged.connect(self._apply_keyer_live)
        chroma_row.addWidget(_ParamLabel(
            "Chroma Min:", lambda: self.ky_chroma_min_spin.setValue(ky.DEFAULT_CHROMA_MIN)))
        chroma_row.addWidget(self.ky_chroma_min_spin, stretch=1)
        chroma_row.addWidget(_ParamLabel(
            "Chroma Max:", lambda: self.ky_chroma_max_spin.setValue(ky.DEFAULT_CHROMA_MAX)))
        chroma_row.addWidget(self.ky_chroma_max_spin, stretch=1)
        outer.addLayout(chroma_row)

        lightness_row = QHBoxLayout()
        self.ky_lightness_min_spin = QDoubleSpinBox()
        self.ky_lightness_max_spin = QDoubleSpinBox()
        for spin, default in ((self.ky_lightness_min_spin, ky.DEFAULT_LIGHTNESS_MIN),
                               (self.ky_lightness_max_spin, ky.DEFAULT_LIGHTNESS_MAX)):
            spin.setRange(0.0, 1.0)
            spin.setDecimals(2)
            spin.setSingleStep(0.05)
            spin.setValue(default)
            enable_drag_to_scrub(spin)
            spin.valueChanged.connect(self._apply_keyer_live)
        lightness_row.addWidget(_ParamLabel(
            "Lightness Min:", lambda: self.ky_lightness_min_spin.setValue(ky.DEFAULT_LIGHTNESS_MIN)))
        lightness_row.addWidget(self.ky_lightness_min_spin, stretch=1)
        lightness_row.addWidget(_ParamLabel(
            "Lightness Max:", lambda: self.ky_lightness_max_spin.setValue(ky.DEFAULT_LIGHTNESS_MAX)))
        lightness_row.addWidget(self.ky_lightness_max_spin, stretch=1)
        outer.addLayout(lightness_row)

        softness_row = QHBoxLayout()
        self.ky_softness_spin = QDoubleSpinBox()
        self.ky_softness_spin.setRange(0.0, 1.0)
        self.ky_softness_spin.setDecimals(2)
        self.ky_softness_spin.setSingleStep(0.05)
        self.ky_softness_spin.setValue(ky.DEFAULT_SOFTNESS)
        enable_drag_to_scrub(self.ky_softness_spin)
        self.ky_softness_spin.valueChanged.connect(self._apply_keyer_live)
        softness_row.addWidget(_ParamLabel(
            "Softness:", lambda: self.ky_softness_spin.setValue(ky.DEFAULT_SOFTNESS)))
        softness_row.addWidget(self.ky_softness_spin, stretch=1)

        self.ky_invert_checkbox = QCheckBox("Invert")
        self.ky_invert_checkbox.setChecked(ky.DEFAULT_INVERT)
        self.ky_invert_checkbox.toggled.connect(self._apply_keyer_live)
        softness_row.addWidget(self.ky_invert_checkbox)
        outer.addLayout(softness_row)

        # Highlight (per Tom: "a way to preview the mask similar to
        # resolve's highlight mode") -- toggles a live Viewer overlay
        # showing this stage's own matte (full color where selected,
        # grayscale elsewhere), see _on_ky_highlight_toggled/
        # get_highlight_stage_id/sample_image_panel.py's own
        # "MATTE HIGHLIGHT" comment for the actual compositing. A plain
        # viewing preference, not a qualifier parameter, so it lives on
        # its own row rather than sharing softness_row with Invert.
        highlight_row = QHBoxLayout()
        self.ky_highlight_checkbox = QCheckBox("Highlight")
        self.ky_highlight_checkbox.setToolTip(
            "Show this stage's own matte in the Viewer -- full color "
            "where selected, grayscale elsewhere.")
        self.ky_highlight_checkbox.toggled.connect(self._on_ky_highlight_toggled)
        highlight_row.addWidget(self.ky_highlight_checkbox)
        highlight_row.addStretch(1)
        outer.addLayout(highlight_row)

        outer.addStretch(1)

        self.inspector_stack.addWidget(self._wrap_inspector_page(group))
        self._keyer_inspector_index = self.inspector_stack.count() - 1

    def _apply_keyer_live(self, *_args):
        """Guarded by _refreshing_inspector, same convention as
        _apply_color_warper_live -- switching TO a keyer stage also
        drives every spin/checkbox here via _refresh_inspector_body,
        which shouldn't count as a live edit. `*_args` swallows whichever
        single value each different signal (valueChanged/toggled) hands
        this shared handler -- every field is read fresh from its own
        widget below regardless."""
        if self._refreshing_inspector:
            return
        stage = self._selected_stage()
        if stage is None or stage.kind != "keyer":
            return
        stage.params["working_colourspace"] = self.ky_space_combo.currentText()
        stage.params["hue_center"] = self.ky_hue_center_spin.value()
        stage.params["hue_width"] = self.ky_hue_width_spin.value()
        stage.params["chroma_min"] = self.ky_chroma_min_spin.value()
        stage.params["chroma_max"] = self.ky_chroma_max_spin.value()
        stage.params["lightness_min"] = self.ky_lightness_min_spin.value()
        stage.params["lightness_max"] = self.ky_lightness_max_spin.value()
        stage.params["softness"] = self.ky_softness_spin.value()
        stage.params["invert"] = self.ky_invert_checkbox.isChecked()
        self._update_row_content(stage.id)
        self._emit_changed()

    def _on_ky_highlight_toggled(self, checked):
        """Guarded by _refreshing_inspector, same convention as
        _apply_keyer_live -- switching TO a keyer stage also syncs this
        checkbox via _refresh_inspector_body, which shouldn't count as a
        live edit. Unlike every other keyer control, this does NOT write
        into stage.params -- self._highlight_stage_id is ephemeral UI
        state (see get_highlight_stage_id's own docstring), not
        something a saved session should remember was "on." Still calls
        _emit_changed() same as any other control here, since the
        Viewer's own preview genuinely needs to re-render either way."""
        if self._refreshing_inspector:
            return
        stage = self._selected_stage()
        if stage is None or stage.kind != "keyer":
            return
        self._highlight_stage_id = stage.id if checked else None
        self._emit_changed()

    def _on_ky_pick_clicked(self):
        """Arms the Viewer's own eyedropper (see set_eyedropper_requester's
        own docstring) for the CURRENTLY selected keyer stage -- captures
        its own Stage.id now, at click time, rather than re-reading
        self._selected_stage() from inside the picked-pixel callback,
        since the selection could have moved on by the time the user
        actually clicks a pixel in the Viewer."""
        if self._eyedropper_requester is None:
            return
        stage = self._selected_stage()
        if stage is None or stage.kind != "keyer":
            return
        stage_id = stage.id
        self._eyedropper_requester(lambda rgb: self._on_ky_pixel_picked(stage_id, rgb))

    def _on_ky_pixel_picked(self, stage_id, rgb):
        """Callback from WipeCompareWidget.arm_eyedropper, via Sample
        ImagePanel.pick_pixel_color -- `rgb` is a plain (r, g, b) float
        tuple sampled off the ACTUAL on-screen pixel (see read_back_
        after_frame's own docstring). Decoded here through THIS stage's
        OWN working_colourspace -- NOT a fixed display space (an earlier
        version of this method assumed the on-screen pixel was always
        display-encoded sRGB, which is simply wrong: this app's Viewer
        applies no unconditional display transform of its own --
        gpu_stack_renderer.render_chain runs only the stages actually IN
        the user's stack, so read_back_after_frame hands back whatever
        encoding the working pipeline is already in, e.g. ACEScct,
        exactly matching what compute_matte's own decode_working_
        colourspace_to_oklch call already assumes for THIS SAME stage's
        params. Using working_colourspace here makes the eyedropper key
        off the pixel's real perceptually-uniform OKLCh position exactly
        as this keyer's own qualifier would see it, rather than off
        whatever raw code values happened to be on screen misinterpreted
        through the wrong transfer function.) Sets Hue Center ONLY -- see
        the Pick button's own tooltip for why width/chroma/lightness are
        left alone. No-ops if the keyer that requested the pick is no
        longer selected -- the stage_id captured at click time may not
        resolve to anything meaningful anymore by the time the Viewer
        click actually lands."""
        stage = self._selected_stage()
        if stage is None or stage.id != stage_id or stage.kind != "keyer":
            return
        working_colourspace = stage.params.get("working_colourspace", ky.DEFAULT_WORKING_COLOURSPACE)
        sample = np.array(rgb, dtype=np.float64).reshape(1, 1, 3)
        _, _, hue = cw.decode_working_colourspace_to_oklch(sample, working_colourspace)
        self.ky_hue_center_spin.setValue(float(hue[0, 0]))

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
                spin = self._pl_spins[channel]
                spin.blockSignals(True)
                spin.setValue(int(round(p.get(key, 0))))
                spin.blockSignals(False)
            idx = self.pl_space_combo.findText(p.get("working_colourspace", DEFAULT_WORKING_COLOURSPACE))
            if idx >= 0:
                # Blocked -- switching TO this stage shouldn't itself count
                # as a live edit (see _on_pl_space_changed); spinboxes above
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

        elif stage.kind == "custom_drt":
            if aces_drt.OCIO_AVAILABLE:
                p = stage.params
                idx = self.cdr_space_combo.findText(p.get("working_colourspace", ""))
                if idx >= 0:
                    self.cdr_space_combo.blockSignals(True)
                    self.cdr_space_combo.setCurrentIndex(idx)
                    self.cdr_space_combo.blockSignals(False)
                for fields in (self._CDR_TONESCALE_FIELDS, self._CDR_DESAT_FIELDS, self._CDR_GAMUT_FIELDS):
                    for key, _label, _lo, _hi, _step, _decimals, default in fields:
                        spin = self._cdr_spins[key]
                        spin.blockSignals(True)
                        spin.setValue(p.get(key, default))
                        spin.blockSignals(False)
                mastering_display = p.get(
                    "mastering_display", custom_drt.DEFAULT_MASTERING_DISPLAY)
                idx = self.cdr_mastering_display_combo.findText(mastering_display)
                if idx >= 0:
                    self.cdr_mastering_display_combo.blockSignals(True)
                    self.cdr_mastering_display_combo.setCurrentIndex(idx)
                    self.cdr_mastering_display_combo.blockSignals(False)
                final_target = p.get(
                    "final_target", custom_drt.DEFAULT_FINAL_TARGET_FOR_DISPLAY[mastering_display])
                self._populate_cdr_final_target_combo(mastering_display, select=final_target)
                self._populate_cdr_mastering_brightness_combo(
                    mastering_display,
                    select=p.get("mastering_brightness", custom_drt.DEFAULT_MASTERING_BRIGHTNESS))
                self.cdr_mastering_brightness_combo.setEnabled(mastering_display == "HDR")
            self.inspector_stack.setCurrentIndex(self._custom_drt_inspector_index)

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

        elif stage.kind == "grading_primary":
            if gpr.OCIO_AVAILABLE:
                p = stage.params
                style = p.get("style", gpr.DEFAULT_STYLE)
                idx = self.gpr_style_combo.findData(style)
                if idx >= 0:
                    self.gpr_style_combo.setCurrentIndex(idx)
                self._update_gpr_field_visibility()

                for field_key, _label, is_additive in GRADING_PRIMARY_FIELD_SPECS:
                    default = gpr.IDENTITY_ADD if is_additive else gpr.IDENTITY_MULT
                    r, g, b, m = p.get(field_key, default)
                    spins = self._gpr_field_spins[field_key]
                    # Blocked -- see _on_gpr_channel_spin_changed/_on_gpr_
                    # master_changed's own docstrings: with M genuinely
                    # independent of R/G/B, letting those handlers fire
                    # mid-population (on a still-partially-old spin state)
                    # would recompute a wrong wheel position and, once M
                    # itself gets set, overwrite R/G/B with values that
                    # don't match what's actually stored. Set every value
                    # directly instead, then back-compute the wheel
                    # position once from the fully-updated (r, g, b, m).
                    for channel, value in zip(("R", "G", "B", "M"), (r, g, b, m)):
                        spin = spins[channel]
                        spin.blockSignals(True)
                        spin.setValue(value)
                        spin.blockSignals(False)
                    x, y = cb.wheel_from_rgb_given_master(r, g, b, m, strength=GRADING_WHEEL_STRENGTH)
                    self._gpr_wheels[field_key].set_balance(x, y, emit=False)

                self.gpr_pivot_spin.setValue(p.get("pivot", gpr.DEFAULT_PIVOT))
                self.gpr_sat_spin.setValue(p.get("saturation", gpr.DEFAULT_SATURATION))
            self.inspector_stack.setCurrentIndex(self._grading_primary_inspector_index)

        elif stage.kind == "exposure_contrast":
            if ec.OCIO_AVAILABLE:
                p = stage.params
                idx = self.ec_style_combo.findData(p.get("style", ec.DEFAULT_STYLE))
                if idx >= 0:
                    self.ec_style_combo.setCurrentIndex(idx)
                self._ec_field_spins["exposure"].setValue(p.get("exposure", ec.IDENTITY_EXPOSURE))
                self._ec_field_spins["contrast"].setValue(p.get("contrast", ec.IDENTITY_CONTRAST))
                self._ec_field_spins["gamma"].setValue(p.get("gamma", ec.IDENTITY_GAMMA))
            self.inspector_stack.setCurrentIndex(self._exposure_contrast_inspector_index)

        elif stage.kind == "grading_tone":
            if gtn.OCIO_AVAILABLE:
                p = stage.params
                idx = self.gtn_style_combo.findData(p.get("style", gtn.DEFAULT_STYLE))
                if idx >= 0:
                    self.gtn_style_combo.setCurrentIndex(idx)
                for range_key in gtn.RANGE_KEYS:
                    r, g, b, m, start, width = p.get(range_key) or gtn.DEFAULT_RANGE_VALUES[range_key]
                    spins = self._gtn_range_spins[range_key]
                    # Blocked -- same reasoning as Grading Primary's own
                    # refresh block (see that one's own comment): M is
                    # independent, so letting the sync handlers fire
                    # mid-population would compute a wrong wheel position
                    # and then overwrite R/G/B once M itself gets set.
                    for channel, value in zip(("R", "G", "B", "M", "Start", "Width"),
                                               (r, g, b, m, start, width)):
                        spin = spins[channel]
                        spin.blockSignals(True)
                        spin.setValue(value)
                        spin.blockSignals(False)
                    x, y = cb.wheel_from_rgb_given_master(r, g, b, m, strength=GRADING_WHEEL_STRENGTH)
                    self._gtn_wheels[range_key].set_balance(x, y, emit=False)
                self.gtn_scontrast_spin.setValue(p.get("scontrast", gtn.DEFAULT_SCONTRAST))
            self.inspector_stack.setCurrentIndex(self._grading_tone_inspector_index)

        elif stage.kind == "grading_rgb_curve":
            if grc.OCIO_AVAILABLE:
                p = stage.params
                idx = self.grc_style_combo.findData(p.get("style", grc.DEFAULT_STYLE))
                if idx >= 0:
                    self.grc_style_combo.setCurrentIndex(idx)
                for channel in grc.RGB_CHANNELS:
                    self.grc_curve_editor.set_points(
                        channel, p.get(channel) or grc.DEFAULT_CONTROL_POINTS)
                # Deliberately does NOT reset the active channel/checked
                # button to "master" here -- this branch re-runs on every
                # single live curve edit too (see _refresh_list's own
                # comment on why it unconditionally calls
                # _on_selection_changed -> _refresh_inspector, not just on
                # an actual stage switch), and forcing "master" here made
                # clicking R/G/B immediately snap back to Master on the
                # very next edit (confirmed live). The channel buttons'
                # own checked state (set by _on_grc_channel_button_clicked)
                # is left to persist across stage switches too, same as
                # every other inspector's widgets keep their layout/style
                # across a switch and only their VALUES get resynced here.
            self.inspector_stack.setCurrentIndex(self._grading_rgb_curve_inspector_index)

        elif stage.kind == "color_warper":
            p = stage.params
            self.cw_wheel.set_points(p.get("points") or [], emit=False)
            wake = p.get("wake", cw.DEFAULT_WAKE)
            dropoff = p.get("dropoff", cw.DEFAULT_DROPOFF)
            self.cw_wheel.set_wake(wake)
            self.cw_wheel.set_dropoff(dropoff)
            self.cw_wake_spin.setValue(wake)
            self.cw_dropoff_spin.setValue(dropoff)
            idx = self.cw_space_combo.findText(p.get("working_colourspace", cw.DEFAULT_WORKING_COLOURSPACE))
            if idx >= 0:
                self.cw_space_combo.setCurrentIndex(idx)
            self.inspector_stack.setCurrentIndex(self._color_warper_inspector_index)
            # Sync the cloud to THIS stage's own warp -- different
            # color_warper stages can have different points/wake/dropoff,
            # so switching stages needs to update it too, not just leave
            # it showing whichever stage was selected before.
            self.cw_cloud.set_warp(p.get("points") or [], wake, dropoff)

        elif stage.kind == "keyer":
            p = stage.params
            idx = self.ky_space_combo.findText(p.get("working_colourspace", ky.DEFAULT_WORKING_COLOURSPACE))
            if idx >= 0:
                self.ky_space_combo.setCurrentIndex(idx)
            self.ky_hue_center_spin.setValue(p.get("hue_center", ky.DEFAULT_HUE_CENTER))
            self.ky_hue_width_spin.setValue(p.get("hue_width", ky.DEFAULT_HUE_WIDTH))
            self.ky_chroma_min_spin.setValue(p.get("chroma_min", ky.DEFAULT_CHROMA_MIN))
            self.ky_chroma_max_spin.setValue(p.get("chroma_max", ky.DEFAULT_CHROMA_MAX))
            self.ky_lightness_min_spin.setValue(p.get("lightness_min", ky.DEFAULT_LIGHTNESS_MIN))
            self.ky_lightness_max_spin.setValue(p.get("lightness_max", ky.DEFAULT_LIGHTNESS_MAX))
            self.ky_softness_spin.setValue(p.get("softness", ky.DEFAULT_SOFTNESS))
            self.ky_invert_checkbox.blockSignals(True)
            self.ky_invert_checkbox.setChecked(p.get("invert", ky.DEFAULT_INVERT))
            self.ky_invert_checkbox.blockSignals(False)
            self.ky_highlight_checkbox.blockSignals(True)
            self.ky_highlight_checkbox.setChecked(self._highlight_stage_id == stage.id)
            self.ky_highlight_checkbox.blockSignals(False)
            # Own matte letter in the group title (per Tom -- see
            # _StageRowWidget.matte_letter_badge's own docstring for the
            # row-level counterpart) -- recomputed fresh every refresh
            # since reordering can change it.
            letter = st.matte_letters_for(self.stack.stages).get(stage.id)
            self.ky_group.setTitle(f"Keyer ({letter})" if letter else "Keyer")
            self.inspector_stack.setCurrentIndex(self._keyer_inspector_index)

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
        """requester: a callable taking one arg (a callback) that arms
        the Viewer's own eyedropper -- SampleImagePanel.pick_pixel_color,
        wired in from main_window.py alongside set_stack_getter/set_
        highlight_getter. Unlike those two, this one is PUSH rather than
        PULL: StackPanel can't reach the Viewer's WipeCompareWidget
        directly (panels only ever talk through main_window.py), so this
        stores a way to ASK it to arm itself instead of a value to read.
        None (never wired, or in a standalone/test StackPanel) makes
        _on_ky_pick_clicked a no-op -- same defensive convention every
        other cross-panel getter/callback in this class already uses."""
        self._eyedropper_requester = requester

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
