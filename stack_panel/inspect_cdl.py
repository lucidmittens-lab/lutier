"""stack_panel/inspect_cdl.py -- the ASC CDL inspector page: SOP wheels with
master and fine R/G/B spinboxes, saturation, reset and Save to CDL Library.
A mixin: StackPanel inherits it, so these methods share the panel's own
`self`.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QComboBox, QDoubleSpinBox,
    QGroupBox, QSlider, QCheckBox, QInputDialog,
)
import cdl
import color_balance as cb
from color_wheel_widget import ColorBalanceWheel
from spinbox_scrub import enable_drag_to_scrub
from .labels import (
    CDL_SAT_RANGE, CDL_SOP_FIELD_DEFAULTS, CDL_SOP_FIELD_SPECS,
    CDL_WHEEL_DIAMETER, _format_cdl_label,
)
from .widgets import _ParamLabel


class CdlInspectorMixin:
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
            self._reset_one_cdl_field(field_key, apply=False)
        self._apply_cdl_live()

    def _reset_one_cdl_field(self, field_key, apply=True):
        """Resets just ONE SOP field's fine R/G/B spins, master spin, and
        wheel to identity -- directly, rather than relying on
        _on_cdl_channel_spin_changed's own cascade (an earlier version of
        this method did, one spin's valueChanged at a time). Same
        ordering-hazard/redundant-apply reasoning as _reset_one_gpr_field's
        own docstring: with the cascade still live, resetting R/G/B one
        spin at a time fired _apply_cdl_live (a full live re-render) up
        to 3 times per field for what's conceptually ONE reset -- 9 times
        total for "Reset SOP to Identity" across all three fields. Since
        every channel resets to the SAME default value, the resulting
        wheel position is always dead-center (no tint, x=y=0) and the
        master is always that same default -- no need to round-trip
        through color_balance.rgb_to_master_and_wheel at all, unlike a
        real fine-spin edit that can land anywhere. `apply=False` lets
        _on_cdl_reset_sop_clicked call this once per field (all three)
        and apply/re-render exactly once at the end, instead of once per
        field; shared with each field's own "{label} (Master):" label
        double-click (per Tom, see _ParamLabel), which resets just that
        one field and still wants its own immediate apply (the
        default)."""
        default = CDL_SOP_FIELD_DEFAULTS[field_key]
        for spin in self._cdl_sop_spins[field_key].values():
            spin.blockSignals(True)
            spin.setValue(default)
            spin.blockSignals(False)
        master_spin = self._cdl_master_spins[field_key]
        master_spin.blockSignals(True)
        master_spin.setValue(default)
        master_spin.blockSignals(False)
        self._cdl_wheels[field_key].set_balance(0.0, 0.0, emit=False)
        if apply:
            self._apply_cdl_live()

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

    def _refresh_cdl_inspector(self, stage):
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
