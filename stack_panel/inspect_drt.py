"""stack_panel/inspect_drt.py -- inspector pages for the colour-management
stages: ACES DRT, CAMier (custom_drt) and OCIO Colour Space Convert. A
mixin: StackPanel inherits it, so these methods share the panel's own
`self`.
"""
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QComboBox, QDoubleSpinBox,
    QGroupBox,
)
import aces_drt
import colorspace_convert as csc
import custom_drt
from spinbox_scrub import enable_drag_to_scrub
from .widgets import _ParamLabel


class DrtInspectorMixin:
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
        # Every spin's own valueChanged (_on_cdr_field_changed) and the
        # mastering-display combo's own currentTextChanged
        # (_on_cdr_mastering_display_changed) each apply live
        # independently -- signals are blocked here so resetting ~11
        # fields plus the display combo doesn't fire ~11+ separate
        # live-applies (each a full re-render) for what's conceptually
        # ONE reset, same ordering-hazard/redundant-apply fix as
        # _reset_one_gpr_field's own docstring explains for Grading
        # Primary. _populate_cdr_final_target_combo/_populate_cdr_
        # mastering_brightness_combo already block their own signals
        # internally (see their own docstrings), so only the spins and
        # the mastering-display combo itself need it here.
        for fields in (self._CDR_TONESCALE_FIELDS, self._CDR_DESAT_FIELDS, self._CDR_GAMUT_FIELDS):
            for key, _label, _lo, _hi, _step, _decimals, default in fields:
                spin = self._cdr_spins[key]
                spin.blockSignals(True)
                spin.setValue(default)
                spin.blockSignals(False)
        idx = self.cdr_mastering_display_combo.findText(custom_drt.DEFAULT_MASTERING_DISPLAY)
        if idx >= 0:
            self.cdr_mastering_display_combo.blockSignals(True)
            self.cdr_mastering_display_combo.setCurrentIndex(idx)
            self.cdr_mastering_display_combo.blockSignals(False)
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

    def _refresh_aces_drt_inspector(self, stage):
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

    def _refresh_custom_drt_inspector(self, stage):
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

    def _refresh_colorspace_inspector(self, stage):
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
