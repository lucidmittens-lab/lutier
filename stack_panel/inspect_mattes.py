"""stack_panel/inspect_mattes.py -- inspector pages for the matte stages: Keyer
(with the Viewer pixel pick), Matte Combine and Matte Shape, each with its
matte-highlight toggle. A mixin: StackPanel inherits it, so these methods
share the panel's own `self`.
"""
import numpy as np
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QComboBox, QDoubleSpinBox,
    QGroupBox, QCheckBox,
)
import stack as st
import matte_combine
import matte_shape
import aces_drt
import color_warper as cw
import keyer as ky
from app_logic import list_available_colourspaces
from spinbox_scrub import enable_drag_to_scrub
from .labels import _matte_options_by_index
from .widgets import _ParamLabel


class MatteInspectorMixin:
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
        # for the getter-based cross-panel wiring), seeds a TIGHT starting
        # qualifier box (Hue/Chroma/Lightness) around THIS STAGE'S OWN
        # ACTUAL INPUT at the clicked pixel -- not whatever's on screen
        # (see _on_ky_pixel_picked's own docstring for why those can
        # differ, and its PICK_*_HALF_WIDTH constants) -- widen from
        # there with the sliders/Softness as needed.
        self.ky_pick_button = QPushButton("Pick")
        self.ky_pick_button.setToolTip(
            "Click, then click a pixel in the Viewer to set a tight "
            "starting Hue/Chroma/Lightness box around it.")
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

        # Pre-filter + matte blur + cleanup (per Tom: "pre filtering/blur
        # for keys as well as typical cleanup features (clean white/
        # black)", then clarified he meant Prefilter and Blur as TWO
        # separate controls -- the same split DaVinci Resolve's own
        # Qualifier makes) -- see keyer.py's own module docstring for
        # the math and blend order. Prefilter Blur blurs the SOURCE
        # IMAGE before qualifying (Nuke-style noise reduction so grain
        # doesn't speckle the key); Matte Blur softens the ALREADY-
        # COMPUTED matte's own edges (Resolve's Matte Finesse "Blur
        # Radius"); Clip Black/White remap the matte afterward (the
        # standard "clean black/clean white" cleanup).
        prefilter_row = QHBoxLayout()
        self.ky_prefilter_blur_spin = QDoubleSpinBox()
        self.ky_matte_blur_spin = QDoubleSpinBox()
        for spin, default in ((self.ky_prefilter_blur_spin, ky.DEFAULT_PREFILTER_BLUR),
                               (self.ky_matte_blur_spin, ky.DEFAULT_MATTE_BLUR)):
            spin.setRange(0.0, 25.0)
            spin.setDecimals(2)
            spin.setSingleStep(0.25)
            spin.setValue(default)
            enable_drag_to_scrub(spin)
            spin.valueChanged.connect(self._apply_keyer_live)
        prefilter_row.addWidget(_ParamLabel(
            "Prefilter Blur:", lambda: self.ky_prefilter_blur_spin.setValue(ky.DEFAULT_PREFILTER_BLUR)))
        prefilter_row.addWidget(self.ky_prefilter_blur_spin, stretch=1)
        prefilter_row.addWidget(_ParamLabel(
            "Matte Blur:", lambda: self.ky_matte_blur_spin.setValue(ky.DEFAULT_MATTE_BLUR)))
        prefilter_row.addWidget(self.ky_matte_blur_spin, stretch=1)
        outer.addLayout(prefilter_row)

        clip_row = QHBoxLayout()
        self.ky_clip_black_spin = QDoubleSpinBox()
        self.ky_clip_white_spin = QDoubleSpinBox()
        for spin, default in ((self.ky_clip_black_spin, ky.DEFAULT_CLIP_BLACK),
                               (self.ky_clip_white_spin, ky.DEFAULT_CLIP_WHITE)):
            spin.setRange(0.0, 1.0)
            spin.setDecimals(3)
            spin.setSingleStep(0.01)
            spin.setValue(default)
            enable_drag_to_scrub(spin)
            spin.valueChanged.connect(self._apply_keyer_live)
        clip_row.addWidget(_ParamLabel(
            "Clip Black:", lambda: self.ky_clip_black_spin.setValue(ky.DEFAULT_CLIP_BLACK)))
        clip_row.addWidget(self.ky_clip_black_spin, stretch=1)
        clip_row.addWidget(_ParamLabel(
            "Clip White:", lambda: self.ky_clip_white_spin.setValue(ky.DEFAULT_CLIP_WHITE)))
        clip_row.addWidget(self.ky_clip_white_spin, stretch=1)
        outer.addLayout(clip_row)

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
        stage.params["prefilter_blur"] = self.ky_prefilter_blur_spin.value()
        stage.params["matte_blur"] = self.ky_matte_blur_spin.value()
        stage.params["clip_black"] = self.ky_clip_black_spin.value()
        stage.params["clip_white"] = self.ky_clip_white_spin.value()
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
        self._eyedropper_requester(stage_id, lambda rgb: self._on_ky_pixel_picked(stage_id, rgb))

    # A pick seeds a TIGHT starting box around the picked pixel's own
    # OKLCh position on all three axes, not just hue -- see
    # _on_ky_pixel_picked's own docstring for why "hue only, leave
    # chroma/lightness wide open" (this method's original v1 behavior)
    # was a real bug in practice, not just a stylistic choice: a keyer's
    # DEFAULT chroma/lightness bands are effectively "no gating," so
    # anything sharing roughly the picked hue -- including a much less
    # saturated or differently-lit surface -- passed straight through.
    # These half-widths are a deliberately tight STARTING point (the user
    # widens from here with the sliders/softness, same as any qualifier
    # tool's own click-then-refine workflow) rather than a scientifically
    # derived constant -- tune directly from feedback if still too
    # tight/loose in practice.
    PICK_HUE_HALF_WIDTH = 12.0
    PICK_CHROMA_HALF_WIDTH = 0.04
    PICK_LIGHTNESS_HALF_WIDTH = 0.12

    def _on_ky_pixel_picked(self, stage_id, rgb):
        """Callback from WipeCompareWidget.arm_eyedropper, via Sample
        ImagePanel.pick_pixel_color_for_stage -- `rgb` is a plain
        (r, g, b) float tuple, this stage's own ACTUAL INPUT at the
        clicked pixel (see stack.running_array_before_stage's own
        docstring), NOT whatever's on screen. Two distinct bugs, fixed
        at two different points in this session, both came from
        conflating "what's on screen" with "what this stage reads":
        an EARLIER version assumed the on-screen pixel was always
        display-encoded sRGB, which was simply wrong (this app's Viewer
        applies no unconditional display transform of its own). That was
        fixed by decoding through THIS stage's own working_colourspace
        instead of a fixed display space -- still correct and unchanged
        here. But per Tom ("the color picker for the key still seems to
        rely on actually pulling a pixel from the preview image, it
        should be pulling up the pixel address of the input of the key
        entry"), the on-screen pixel ALSO reflects every stage AFTER
        this one, plus wipe-compare source selection and any active
        Highlight overlay -- all irrelevant to what THIS stage itself
        actually reads, unless it happens to be the very last stage in
        the stack. `rgb` is now resolved against this stage's own real
        input instead, exactly matching what compute_matte's own decode_
        working_colourspace_to_oklch call already assumes for THIS SAME
        stage's params.

        Sets Hue Center/Width AND Chroma/Lightness min/max -- a TIGHT box
        centered on the picked pixel's own OKLCh position (see the
        PICK_*_HALF_WIDTH constants just above), not hue alone. Per Tom,
        the original hue-only version caught unrelated colors sharing
        roughly the same hue (a red building pulling in beige ones too)
        because chroma/lightness were left at their default "no gating"
        full range -- the qualifier's own falloff (color_warper.
        falloff_weight) has real, non-negligible tails, so an unconstrained
        axis genuinely lets a lot through, not just a theoretical edge
        case. No-ops if the keyer that requested the pick is no longer
        selected -- the stage_id captured at click time may not resolve
        to anything meaningful anymore by the time the Viewer click
        actually lands."""
        stage = self._selected_stage()
        if stage is None or stage.id != stage_id or stage.kind != "keyer":
            return
        working_colourspace = stage.params.get("working_colourspace", ky.DEFAULT_WORKING_COLOURSPACE)
        sample = np.array(rgb, dtype=np.float64).reshape(1, 1, 3)
        lightness, chroma, hue = cw.decode_working_colourspace_to_oklch(sample, working_colourspace)
        lightness, chroma, hue = float(lightness[0, 0]), float(chroma[0, 0]), float(hue[0, 0])

        self.ky_hue_center_spin.setValue(hue)
        self.ky_hue_width_spin.setValue(
            max(self.ky_hue_width_spin.minimum(), self.PICK_HUE_HALF_WIDTH))
        self.ky_chroma_min_spin.setValue(
            max(self.ky_chroma_min_spin.minimum(), chroma - self.PICK_CHROMA_HALF_WIDTH))
        self.ky_chroma_max_spin.setValue(
            min(self.ky_chroma_max_spin.maximum(), chroma + self.PICK_CHROMA_HALF_WIDTH))
        self.ky_lightness_min_spin.setValue(
            max(self.ky_lightness_min_spin.minimum(), lightness - self.PICK_LIGHTNESS_HALF_WIDTH))
        self.ky_lightness_max_spin.setValue(
            min(self.ky_lightness_max_spin.maximum(), lightness + self.PICK_LIGHTNESS_HALF_WIDTH))

    # ---- inspector: matte_combine stage ----------------------------------

    def _build_matte_combine_inspector(self):
        # Title updated live to show this stage's own matte letter, same
        # convention as the Keyer inspector's own title (see
        # _refresh_inspector_body's "matte_combine" branch below).
        self.mc_group = QGroupBox("Matte Combine")
        group = self.mc_group
        outer = QVBoxLayout(group)

        # Two matte-letter dropdowns, reusing the EXACT SAME (letter,
        # stage_id) options _StageRowWidget's own per-row matte_combo
        # already offers -- see self._matte_options (built once per
        # _refresh_list, keyed by this stage's own position) and
        # _matte_options_by_index's own docstring for why that's the
        # right upstream-only, position-based source of truth rather
        # than recomputing anything here.
        a_row = QHBoxLayout()
        a_row.addWidget(QLabel("Matte A:"))
        self.mc_a_combo = QComboBox()
        self.mc_a_combo.currentIndexChanged.connect(self._apply_matte_combine_live)
        a_row.addWidget(self.mc_a_combo, stretch=1)
        outer.addLayout(a_row)

        b_row = QHBoxLayout()
        b_row.addWidget(QLabel("Matte B:"))
        self.mc_b_combo = QComboBox()
        self.mc_b_combo.currentIndexChanged.connect(self._apply_matte_combine_live)
        b_row.addWidget(self.mc_b_combo, stretch=1)
        outer.addLayout(b_row)

        # Colorist-familiar blend-mode vocabulary rather than raw
        # boolean terms (see matte_combine.py's own docstring) -- itemData
        # is the actual op string matte_combine.compute_combined_matte
        # expects, item text is the human-readable label.
        op_row = QHBoxLayout()
        op_row.addWidget(QLabel("Op:"))
        self.mc_op_combo = QComboBox()
        self.mc_op_combo.addItem("Intersect (A AND B)", "intersect")
        self.mc_op_combo.addItem("Union (A OR B)", "union")
        self.mc_op_combo.addItem("Subtract (A minus B)", "subtract")
        self.mc_op_combo.addItem("Difference (A XOR B)", "difference")
        self.mc_op_combo.currentIndexChanged.connect(self._apply_matte_combine_live)
        op_row.addWidget(self.mc_op_combo, stretch=1)
        outer.addLayout(op_row)

        # Highlight -- same generic mechanism the Keyer inspector's own
        # checkbox already drives (self._highlight_stage_id/
        # get_highlight_stage_id/sample_image_panel.py's "MATTE
        # HIGHLIGHT" compositing never cared which kind produced the
        # matte), just a second entry point onto it -- see
        # _build_keyer_inspector's own highlight_row comment for the
        # full rationale.
        highlight_row = QHBoxLayout()
        self.mc_highlight_checkbox = QCheckBox("Highlight")
        self.mc_highlight_checkbox.setToolTip(
            "Show this stage's own combined matte in the Viewer -- full "
            "color where selected, grayscale elsewhere.")
        self.mc_highlight_checkbox.toggled.connect(self._on_mc_highlight_toggled)
        highlight_row.addWidget(self.mc_highlight_checkbox)
        highlight_row.addStretch(1)
        outer.addLayout(highlight_row)

        outer.addStretch(1)

        self.inspector_stack.addWidget(self._wrap_inspector_page(group))
        self._matte_combine_inspector_index = self.inspector_stack.count() - 1

    def _apply_matte_combine_live(self, *_args):
        """Guarded by _refreshing_inspector, same convention as every
        other live-apply handler -- switching TO a matte_combine stage
        also drives the two combos via _refresh_inspector_body, which
        shouldn't count as a live edit."""
        if self._refreshing_inspector:
            return
        stage = self._selected_stage()
        if stage is None or stage.kind != "matte_combine":
            return
        stage.params["matte_a_source_id"] = self.mc_a_combo.currentData()
        stage.params["matte_b_source_id"] = self.mc_b_combo.currentData()
        stage.params["op"] = self.mc_op_combo.currentData()
        self._update_row_content(stage.id)
        self._emit_changed()

    def _on_mc_highlight_toggled(self, checked):
        """Same convention as _on_ky_highlight_toggled -- guarded by
        _refreshing_inspector, does NOT write into stage.params (ephemeral
        UI state, see get_highlight_stage_id's own docstring), still
        calls _emit_changed() so the Viewer actually re-renders."""
        if self._refreshing_inspector:
            return
        stage = self._selected_stage()
        if stage is None or stage.kind != "matte_combine":
            return
        self._highlight_stage_id = stage.id if checked else None
        self._emit_changed()

    # ---- inspector: matte_shape stage --------------------------------

    def _build_matte_shape_inspector(self):
        # Title updated live to show this stage's own matte letter, same
        # convention as Keyer/Matte Combine's own titles.
        self.ms_group = QGroupBox("Matte Shape")
        group = self.ms_group
        outer = QVBoxLayout(group)

        shape_row = QHBoxLayout()
        shape_row.addWidget(QLabel("Shape:"))
        self.ms_shape_combo = QComboBox()
        self.ms_shape_combo.addItem("Ellipse", "ellipse")
        self.ms_shape_combo.addItem("Rectangle", "rectangle")
        self.ms_shape_combo.currentIndexChanged.connect(self._apply_matte_shape_live)
        shape_row.addWidget(self.ms_shape_combo, stretch=1)
        outer.addLayout(shape_row)

        center_row = QHBoxLayout()
        self.ms_center_x_spin = QDoubleSpinBox()
        self.ms_center_y_spin = QDoubleSpinBox()
        for spin, default in ((self.ms_center_x_spin, matte_shape.DEFAULT_CENTER_X),
                               (self.ms_center_y_spin, matte_shape.DEFAULT_CENTER_Y)):
            spin.setRange(0.0, 1.0)
            spin.setDecimals(3)
            spin.setSingleStep(0.01)
            spin.setValue(default)
            enable_drag_to_scrub(spin)
            spin.valueChanged.connect(self._apply_matte_shape_live)
        center_row.addWidget(_ParamLabel(
            "Center X:", lambda: self.ms_center_x_spin.setValue(matte_shape.DEFAULT_CENTER_X)))
        center_row.addWidget(self.ms_center_x_spin, stretch=1)
        center_row.addWidget(_ParamLabel(
            "Center Y:", lambda: self.ms_center_y_spin.setValue(matte_shape.DEFAULT_CENTER_Y)))
        center_row.addWidget(self.ms_center_y_spin, stretch=1)
        outer.addLayout(center_row)

        size_row = QHBoxLayout()
        self.ms_width_spin = QDoubleSpinBox()
        self.ms_height_spin = QDoubleSpinBox()
        for spin, default in ((self.ms_width_spin, matte_shape.DEFAULT_WIDTH),
                               (self.ms_height_spin, matte_shape.DEFAULT_HEIGHT)):
            spin.setRange(0.01, 2.0)
            spin.setDecimals(3)
            spin.setSingleStep(0.01)
            spin.setValue(default)
            enable_drag_to_scrub(spin)
            spin.valueChanged.connect(self._apply_matte_shape_live)
        size_row.addWidget(_ParamLabel(
            "Width:", lambda: self.ms_width_spin.setValue(matte_shape.DEFAULT_WIDTH)))
        size_row.addWidget(self.ms_width_spin, stretch=1)
        size_row.addWidget(_ParamLabel(
            "Height:", lambda: self.ms_height_spin.setValue(matte_shape.DEFAULT_HEIGHT)))
        size_row.addWidget(self.ms_height_spin, stretch=1)
        outer.addLayout(size_row)

        rotation_softness_row = QHBoxLayout()
        self.ms_rotation_spin = QDoubleSpinBox()
        self.ms_rotation_spin.setRange(-180.0, 180.0)
        self.ms_rotation_spin.setDecimals(1)
        self.ms_rotation_spin.setSingleStep(1.0)
        self.ms_rotation_spin.setValue(matte_shape.DEFAULT_ROTATION)
        self.ms_rotation_spin.setWrapping(True)
        enable_drag_to_scrub(self.ms_rotation_spin)
        self.ms_rotation_spin.valueChanged.connect(self._apply_matte_shape_live)
        rotation_softness_row.addWidget(_ParamLabel(
            "Rotation:", lambda: self.ms_rotation_spin.setValue(matte_shape.DEFAULT_ROTATION)))
        rotation_softness_row.addWidget(self.ms_rotation_spin, stretch=1)

        self.ms_softness_spin = QDoubleSpinBox()
        self.ms_softness_spin.setRange(0.0, 1.0)
        self.ms_softness_spin.setDecimals(2)
        self.ms_softness_spin.setSingleStep(0.05)
        self.ms_softness_spin.setValue(matte_shape.DEFAULT_SOFTNESS)
        enable_drag_to_scrub(self.ms_softness_spin)
        self.ms_softness_spin.valueChanged.connect(self._apply_matte_shape_live)
        rotation_softness_row.addWidget(_ParamLabel(
            "Softness:", lambda: self.ms_softness_spin.setValue(matte_shape.DEFAULT_SOFTNESS)))
        rotation_softness_row.addWidget(self.ms_softness_spin, stretch=1)
        outer.addLayout(rotation_softness_row)

        invert_highlight_row = QHBoxLayout()
        self.ms_invert_checkbox = QCheckBox("Invert")
        self.ms_invert_checkbox.setChecked(matte_shape.DEFAULT_INVERT)
        self.ms_invert_checkbox.toggled.connect(self._apply_matte_shape_live)
        invert_highlight_row.addWidget(self.ms_invert_checkbox)

        # Highlight -- same generic mechanism the Keyer/Matte Combine
        # inspectors' own checkboxes already drive.
        self.ms_highlight_checkbox = QCheckBox("Highlight")
        self.ms_highlight_checkbox.setToolTip(
            "Show this stage's own shape matte in the Viewer -- full "
            "color where selected, grayscale elsewhere.")
        self.ms_highlight_checkbox.toggled.connect(self._on_ms_highlight_toggled)
        invert_highlight_row.addWidget(self.ms_highlight_checkbox)
        invert_highlight_row.addStretch(1)
        outer.addLayout(invert_highlight_row)

        outer.addStretch(1)

        self.inspector_stack.addWidget(self._wrap_inspector_page(group))
        self._matte_shape_inspector_index = self.inspector_stack.count() - 1

    def _apply_matte_shape_live(self, *_args):
        """Guarded by _refreshing_inspector, same convention as every
        other live-apply handler."""
        if self._refreshing_inspector:
            return
        stage = self._selected_stage()
        if stage is None or stage.kind != "matte_shape":
            return
        stage.params["shape"] = self.ms_shape_combo.currentData()
        stage.params["center_x"] = self.ms_center_x_spin.value()
        stage.params["center_y"] = self.ms_center_y_spin.value()
        stage.params["width"] = self.ms_width_spin.value()
        stage.params["height"] = self.ms_height_spin.value()
        stage.params["rotation"] = self.ms_rotation_spin.value()
        stage.params["softness"] = self.ms_softness_spin.value()
        stage.params["invert"] = self.ms_invert_checkbox.isChecked()
        self._update_row_content(stage.id)
        self._emit_changed()

    def _on_ms_highlight_toggled(self, checked):
        """Same convention as _on_ky_highlight_toggled/_on_mc_highlight_
        toggled -- guarded, ephemeral UI state, still re-renders."""
        if self._refreshing_inspector:
            return
        stage = self._selected_stage()
        if stage is None or stage.kind != "matte_shape":
            return
        self._highlight_stage_id = stage.id if checked else None
        self._emit_changed()

    def _refresh_keyer_inspector(self, stage):
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
        self.ky_prefilter_blur_spin.setValue(p.get("prefilter_blur", ky.DEFAULT_PREFILTER_BLUR))
        self.ky_matte_blur_spin.setValue(p.get("matte_blur", ky.DEFAULT_MATTE_BLUR))
        self.ky_clip_black_spin.setValue(p.get("clip_black", ky.DEFAULT_CLIP_BLACK))
        self.ky_clip_white_spin.setValue(p.get("clip_white", ky.DEFAULT_CLIP_WHITE))
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

    def _refresh_matte_combine_inspector(self, stage):
        p = stage.params
        letters = st.matte_letters_for(self.stack.stages)
        stage_index = next(i for i, s in enumerate(self.stack.stages) if s.id == stage.id)
        upstream_options = _matte_options_by_index(self.stack.stages, letters)[stage_index]

        self.mc_a_combo.clear()
        self.mc_a_combo.addItem("None", None)
        for letter, producer_id in upstream_options:
            self.mc_a_combo.addItem(letter, producer_id)
        idx = self.mc_a_combo.findData(p.get("matte_a_source_id"))
        self.mc_a_combo.setCurrentIndex(idx if idx >= 0 else 0)

        self.mc_b_combo.clear()
        self.mc_b_combo.addItem("None", None)
        for letter, producer_id in upstream_options:
            self.mc_b_combo.addItem(letter, producer_id)
        idx = self.mc_b_combo.findData(p.get("matte_b_source_id"))
        self.mc_b_combo.setCurrentIndex(idx if idx >= 0 else 0)

        idx = self.mc_op_combo.findData(p.get("op", matte_combine.DEFAULT_OP))
        self.mc_op_combo.setCurrentIndex(idx if idx >= 0 else 0)

        self.mc_highlight_checkbox.blockSignals(True)
        self.mc_highlight_checkbox.setChecked(self._highlight_stage_id == stage.id)
        self.mc_highlight_checkbox.blockSignals(False)

        # Own matte letter in the group title, same convention as
        # the Keyer inspector's own title just above.
        own_letter = letters.get(stage.id)
        self.mc_group.setTitle(f"Matte Combine ({own_letter})" if own_letter else "Matte Combine")
        self.inspector_stack.setCurrentIndex(self._matte_combine_inspector_index)

    def _refresh_matte_shape_inspector(self, stage):
        p = stage.params
        idx = self.ms_shape_combo.findData(p.get("shape", matte_shape.DEFAULT_SHAPE))
        if idx >= 0:
            self.ms_shape_combo.setCurrentIndex(idx)
        self.ms_center_x_spin.setValue(p.get("center_x", matte_shape.DEFAULT_CENTER_X))
        self.ms_center_y_spin.setValue(p.get("center_y", matte_shape.DEFAULT_CENTER_Y))
        self.ms_width_spin.setValue(p.get("width", matte_shape.DEFAULT_WIDTH))
        self.ms_height_spin.setValue(p.get("height", matte_shape.DEFAULT_HEIGHT))
        self.ms_rotation_spin.setValue(p.get("rotation", matte_shape.DEFAULT_ROTATION))
        self.ms_softness_spin.setValue(p.get("softness", matte_shape.DEFAULT_SOFTNESS))
        self.ms_invert_checkbox.blockSignals(True)
        self.ms_invert_checkbox.setChecked(p.get("invert", matte_shape.DEFAULT_INVERT))
        self.ms_invert_checkbox.blockSignals(False)
        self.ms_highlight_checkbox.blockSignals(True)
        self.ms_highlight_checkbox.setChecked(self._highlight_stage_id == stage.id)
        self.ms_highlight_checkbox.blockSignals(False)

        letters = st.matte_letters_for(self.stack.stages)
        own_letter = letters.get(stage.id)
        self.ms_group.setTitle(f"Matte Shape ({own_letter})" if own_letter else "Matte Shape")
        self.inspector_stack.setCurrentIndex(self._matte_shape_inspector_index)
