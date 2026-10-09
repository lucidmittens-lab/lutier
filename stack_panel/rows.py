"""stack_panel/rows.py -- the Stack list's row widgets: one _StageRowWidget per
stage (badge, enable toggle, label, opacity/matte controls, delete), the
two-column fork grid rows, and the project working space their
"in <space>" notes compare against (_project_working_space, set by
StackPanel.set_default_working_colourspace).
"""
from PySide6.QtCore import Qt, QSize, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QSpinBox, QFrame, QToolButton, QSizePolicy,
)
import stack as st
import colour_pipeline
from printer_lights import DEFAULT_WORKING_COLOURSPACE
from spinbox_scrub import enable_drag_to_scrub
from style import PANEL_BORDER_RADIUS
from .labels import (
    _stage_kind_badge_text,
)


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


# The project working space, for rows' "in <space>" notes (see
# _row_label_text) -- set by StackPanel.set_default_working_colourspace.
_project_working_space = DEFAULT_WORKING_COLOURSPACE


def _row_label_text(stage):
    """A row's label: the stage's own, plus where it works when that's not
    the project working space ("CDL · in ARRI LogC4") -- the pipeline's
    order of operations made visible (colour_pipeline.py)."""
    note = colour_pipeline.stage_space_note(stage, _project_working_space)
    return f"{stage.label}  ·  {note}" if note else stage.label


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
        self.label_widget = _ElidingLabel(_row_label_text(stage))
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
        if stage.kind in st.MATTE_PRODUCING_KINDS:
            # A matte-PRODUCING row's own opacity is repurposed as "matte
            # amount" (see make_keyer_stage's own docstring) -- it never
            # consumes ANOTHER stage's matte via this per-row dropdown,
            # so hide it here too, not just for "keyer" specifically
            # (generalized while adding "matte_combine" -- which reads
            # its own TWO matte inputs via its own dedicated inspector
            # dropdowns, a completely different mechanism from this row
            # control).
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
        self.label_widget.setText(_row_label_text(stage))
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
