"""stack_panel/inspect_lut.py -- inspector pages for the empty selection, LUT
stages (with optional Retarget) and Printer Lights. A mixin: StackPanel
inherits it, so these methods share the panel's own `self`.
"""
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QComboBox, QSpinBox, QGroupBox, QCheckBox,
)
import aces_drt
from app_logic import list_available_colourspaces
from printer_lights import POINTS_PER_STOP, DEFAULT_WORKING_COLOURSPACE
from spinbox_scrub import enable_drag_to_scrub
from style import HINT_TEXT_HEX
from .labels import PRINTER_LIGHT_RANGE
from .widgets import _ParamLabel


class LutInspectorMixin:
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
        # Each spin's own valueChanged (_on_pl_spin_changed) applies live
        # AND handles gang-mode cascading -- signals are blocked here so
        # resetting all three doesn't fire up to 3 separate live-applies
        # (each a full re-render) for what's conceptually ONE reset, same
        # ordering-hazard/redundant-apply fix as _reset_one_gpr_field's
        # own docstring explains for Grading Primary.
        for spin in self._pl_spins.values():
            spin.blockSignals(True)
            spin.setValue(0)
            spin.blockSignals(False)
        self._apply_printer_lights_live()

    def _apply_printer_lights_live(self):
        stage = self._selected_stage()
        if stage is None or stage.kind != "printer_lights":
            return
        stage.params["r_points"] = self._pl_spins["R"].value()
        stage.params["g_points"] = self._pl_spins["G"].value()
        stage.params["b_points"] = self._pl_spins["B"].value()
        stage.params["working_colourspace"] = self.pl_space_combo.currentText()
        self._emit_changed()

    def _refresh_lut_inspector(self, stage):
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

    def _refresh_printer_lights_inspector(self, stage):
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
