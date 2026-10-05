"""
bake_settings_form.py -- the LUT grid size / input domain fields
(stack.BakeSettings), shown in File > Export. Moved out of the Stack
panel's "Stack Output -- Grid Size / Domain" dialog when they stopped
being Stack fields (Tom, 2026-10-05: "decouple the LUT domain from the
color pipeline stack ... should only be calculated on export").

Edits apply as they're made (no separate Apply button): each valid
change emits settings_changed(BakeSettings); an invalid one (a min not
below its max) shows inline and emits nothing, leaving the last valid
settings in force.
"""
import numpy as np
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QCheckBox,
    QDoubleSpinBox, QPushButton, QMessageBox,
)

import stack as st
from spinbox_scrub import enable_drag_to_scrub
from style import HINT_TEXT_HEX

COMMON_SIZES = [9, 17, 25, 33, 45, 65, 97]
_CHANNELS = ("R", "G", "B")


def describe_bake_settings(settings):
    """One-line summary, e.g. "33³ · domain [0, 1]" -- the Export
    dialog's summary uses it, so the format lives in one place."""
    lo, hi = settings.domain_min, settings.domain_max
    if len(set(lo)) == 1 and len(set(hi)) == 1:
        domain_text = f"[{lo[0]:g}, {hi[0]:g}]"
    else:
        domain_text = " ".join(
            f"{c}[{lo[i]:g},{hi[i]:g}]" for i, c in enumerate(_CHANNELS))
    return f"{settings.size}³ · domain {domain_text}"


def _make_domain_spin(default_value):
    """Wide range and 4 decimals: a domain bound is an HDR/scene-linear
    LUT's actual input range, which can legitimately be negative or run
    well past [0,1] (e.g. an extended-range IDT/LMT)."""
    spin = QDoubleSpinBox()
    spin.setRange(-10000.0, 10000.0)
    spin.setDecimals(4)
    spin.setSingleStep(0.01)
    spin.setValue(default_value)
    enable_drag_to_scrub(spin)
    return spin


class BakeSettingsForm(QWidget):
    settings_changed = Signal(object)  # a stack.BakeSettings

    def __init__(self, stack_getter, parent=None):
        """`stack_getter`: returns the current Stack -- only read by "Use
        Input Bounds From First Stack Entry"."""
        super().__init__(parent)
        self._stack_getter = stack_getter
        self._loading = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Grid size:"))
        self.size_combo = QComboBox()
        self.size_combo.setEditable(True)
        self.size_combo.addItems([str(s) for s in COMMON_SIZES])
        self.size_combo.currentTextChanged.connect(self._on_edited)
        size_row.addWidget(self.size_combo)
        size_row.addStretch(1)
        outer.addLayout(size_row)

        self.link_checkbox = QCheckBox("Link R/G/B")
        self.link_checkbox.setChecked(True)
        self.link_checkbox.toggled.connect(self._on_link_toggled)
        outer.addWidget(self.link_checkbox)

        self.domain_fields = {}
        for channel in _CHANNELS:
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{channel} min:"))
            min_field = _make_domain_spin(0.0)
            row.addWidget(min_field)
            row.addWidget(QLabel(f"{channel} max:"))
            max_field = _make_domain_spin(1.0)
            row.addWidget(max_field)
            self.domain_fields[channel] = (min_field, max_field)
            min_field.valueChanged.connect(self._on_edited)
            max_field.valueChanged.connect(self._on_edited)
            outer.addLayout(row)

        use_first_stage_button = QPushButton("Use Input Bounds From First Stack Entry")
        use_first_stage_button.setToolTip(
            "Reads the first stack entry's own declared input domain (e.g. "
            "a non-stock IDT or LMT with an extended range) and fills the "
            "fields above with it, so the exported LUT covers that range "
            "instead of [0,1].")
        use_first_stage_button.clicked.connect(self._on_use_first_stage_bounds_clicked)
        outer.addWidget(use_first_stage_button)

        self.error_label = QLabel()
        self.error_label.setStyleSheet(f"color: {HINT_TEXT_HEX};")
        self.error_label.setVisible(False)
        outer.addWidget(self.error_label)
        self._loading = True  # just applying the linked state, nothing edited yet
        self._on_link_toggled(True)
        self._loading = False

    def set_settings(self, settings):
        """Show `settings` without emitting settings_changed."""
        self._loading = True
        try:
            self.size_combo.setCurrentText(str(settings.size))
            lo, hi = settings.domain_min, settings.domain_max
            self.link_checkbox.setChecked(len(set(lo)) == 1 and len(set(hi)) == 1)
            for i, channel in enumerate(_CHANNELS):
                min_field, max_field = self.domain_fields[channel]
                min_field.setValue(lo[i])
                max_field.setValue(hi[i])
            self.error_label.setVisible(False)
        finally:
            self._loading = False

    def current_settings(self):
        """The BakeSettings the fields describe; raises ValueError with a
        user-facing message if they don't describe a valid one."""
        try:
            size = int(self.size_combo.currentText())
        except ValueError:
            raise ValueError("Grid size must be a whole number.") from None
        return st.BakeSettings(
            size=size,
            domain_min=tuple(self.domain_fields[c][0].value() for c in _CHANNELS),
            domain_max=tuple(self.domain_fields[c][1].value() for c in _CHANNELS))

    def _on_edited(self, *_args):
        if self._loading:
            return
        self._sync_linked_fields()
        try:
            settings = self.current_settings()
        except ValueError as e:
            self.error_label.setText(str(e))
            self.error_label.setVisible(True)
            return
        self.error_label.setVisible(False)
        self.settings_changed.emit(settings)

    def _on_link_toggled(self, linked):
        for channel in ("G", "B"):
            for field in self.domain_fields[channel]:
                field.setEnabled(not linked)
        if linked:
            self._on_edited()

    def _sync_linked_fields(self):
        if not self.link_checkbox.isChecked():
            return
        r_min, r_max = self.domain_fields["R"]
        loading, self._loading = self._loading, True
        try:
            for channel in ("G", "B"):
                min_field, max_field = self.domain_fields[channel]
                min_field.setValue(r_min.value())
                max_field.setValue(r_max.value())
        finally:
            self._loading = loading

    def _on_use_first_stage_bounds_clicked(self):
        stack = self._stack_getter()
        if not stack.stages:
            QMessageBox.information(
                self, "Empty stack",
                "Add a stage first -- there's nothing to read bounds from.")
            return
        first_stage = stack.stages[0]
        if first_stage.kind != "lut":
            QMessageBox.information(
                self, "Not a LUT stage",
                f"The first stack entry is a {first_stage.kind.replace('_', ' ')} "
                "stage, which doesn't declare its own input bounds -- this "
                "only works when the first entry is a LUT (e.g. a non-stock "
                "IDT or LMT loaded from a file).")
            return
        domain = getattr(first_stage.params.get("lut"), "domain", None)
        if domain is None:
            QMessageBox.information(
                self, "No declared bounds", "This LUT doesn't declare its own input domain.")
            return
        domain = np.asarray(domain, dtype=float)
        if domain.ndim == 1:  # one [min, max] pair shared by every channel (LUT1D)
            lo, hi = np.full(3, domain[0]), np.full(3, domain[1])
        else:
            lo = np.broadcast_to(domain[0], (3,)).astype(float)
            hi = np.broadcast_to(domain[1], (3,)).astype(float)
        current_size = self.size_combo.currentText()
        self._loading = True
        try:
            self.link_checkbox.setChecked(bool(np.all(lo == lo[0]) and np.all(hi == hi[0])))
            for i, channel in enumerate(_CHANNELS):
                min_field, max_field = self.domain_fields[channel]
                min_field.setValue(float(lo[i]))
                max_field.setValue(float(hi[i]))
            self.size_combo.setCurrentText(current_size)
        finally:
            self._loading = False
        self._on_edited()
