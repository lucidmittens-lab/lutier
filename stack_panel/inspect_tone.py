"""stack_panel/inspect_tone.py -- inspector pages for OCIO Grading Tone (five
tonal-range wheels) and Grading RGB Curves (the curve editor). A mixin:
StackPanel inherits it, so these methods share the panel's own `self`.
"""
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QComboBox, QDoubleSpinBox,
    QGroupBox, QToolButton, QButtonGroup,
)
import grading_rgb_curve as grc
import grading_tone as gtn
import color_balance as cb
from color_wheel_widget import ColorBalanceWheel
from curve_editor_widget import CurveEditorWidget, CHANNEL_ORDER, CHANNEL_COLORS, CHANNEL_LABELS
from spinbox_scrub import enable_drag_to_scrub
from .labels import (
    GRADING_TONE_RANGE_LABELS,
    GRADING_TONE_WHEEL_DIAMETER, GRADING_WHEEL_STRENGTH,
    _format_grading_rgb_curve_label, _format_grading_tone_label,
)
from .widgets import _ParamLabel


class ToneInspectorMixin:
    # ---- inspector: grading_tone stage ------------------------------------

    def _build_grading_tone_inspector(self):
        """Largest of the four Tier 1 inspectors (5 ranges x 6 fields each,
        see docs/archive/OCIO_TIER1_GRADING_OPERATORS_PLAN_2026-08-09.md's own note on
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

    def _refresh_grading_tone_inspector(self, stage):
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

    def _refresh_grading_rgb_curve_inspector(self, stage):
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
