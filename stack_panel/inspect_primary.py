"""stack_panel/inspect_primary.py -- inspector pages for OCIO Grading Primary
(log/lin/video styles, wheels with master and fine spinboxes) and
Exposure/Contrast. A mixin: StackPanel inherits it, so these methods share the panel's own
`self`.
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QComboBox, QDoubleSpinBox,
    QGroupBox,
)
import exposure_contrast as ec
import grading_primary as gpr
import color_balance as cb
from color_wheel_widget import ColorBalanceWheel
from spinbox_scrub import enable_drag_to_scrub
from .labels import (
    GRADING_PRIMARY_ADDITIVE_RANGE,
    GRADING_PRIMARY_FIELD_DEFAULTS, GRADING_PRIMARY_FIELD_SPECS,
    GRADING_PRIMARY_MULT_RANGE, GRADING_PRIMARY_STYLE_FIELDS,
    GRADING_PRIMARY_WHEEL_DIAMETER, GRADING_WHEEL_STRENGTH,
    _format_exposure_contrast_label,
    _format_grading_primary_label,
)
from .widgets import _ParamLabel


class PrimaryInspectorMixin:
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
        # Each spin's own valueChanged (_on_ec_field_changed) applies
        # live independently -- signals are blocked here so resetting
        # all three doesn't fire 3 separate live-applies (each a full
        # re-render) for what's conceptually ONE reset, same ordering-
        # hazard/redundant-apply fix as _reset_one_gpr_field's own
        # docstring explains for Grading Primary.
        for key, default in (("exposure", ec.IDENTITY_EXPOSURE), ("contrast", ec.IDENTITY_CONTRAST),
                              ("gamma", ec.IDENTITY_GAMMA)):
            spin = self._ec_field_spins[key]
            spin.blockSignals(True)
            spin.setValue(default)
            spin.blockSignals(False)
        self._apply_ec_live()

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

    def _refresh_grading_primary_inspector(self, stage):
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

    def _refresh_exposure_contrast_inspector(self, stage):
        if ec.OCIO_AVAILABLE:
            p = stage.params
            idx = self.ec_style_combo.findData(p.get("style", ec.DEFAULT_STYLE))
            if idx >= 0:
                self.ec_style_combo.setCurrentIndex(idx)
            self._ec_field_spins["exposure"].setValue(p.get("exposure", ec.IDENTITY_EXPOSURE))
            self._ec_field_spins["contrast"].setValue(p.get("contrast", ec.IDENTITY_CONTRAST))
            self._ec_field_spins["gamma"].setValue(p.get("gamma", ec.IDENTITY_GAMMA))
        self.inspector_stack.setCurrentIndex(self._exposure_contrast_inspector_index)
