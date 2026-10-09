"""stack_panel/inspect_warper.py -- the WARPier (color_warper) inspector page:
the warper wheel and point cloud, wake/dropoff controls and reset. A mixin:
StackPanel inherits it, so these methods share the panel's own `self`.
"""
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QComboBox, QDoubleSpinBox,
    QGroupBox,
)
import aces_drt
import color_warper as cw
from color_warper_wheel import WarpierWheel
from color_warper_cloud_widget import WarpierCloudWidget
from app_logic import list_available_colourspaces
from spinbox_scrub import enable_drag_to_scrub
from .widgets import _ParamLabel


class WarperInspectorMixin:
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

        # Chroma-axis falloff (per Tom, confirmed directly: "if i move a
        # point on a given hue angle, one half way from center, and then
        # move to the same spot on another hue angle, it shifts that
        # whole angle there, meaning all points along the line from
        # center to the edge") -- an independent falloff over how far a
        # pixel's own CHROMA sits from the point's own anchor chroma,
        # multiplied into the existing hue-based falloff (see
        # color_warper.warp_hue_chroma's own docstring). Same row shape
        # as Wake/Drop-off just above.
        chroma_wake_row = QHBoxLayout()
        self.cw_chroma_wake_spin = QDoubleSpinBox()
        self.cw_chroma_wake_spin.setRange(0.01, 0.5)
        self.cw_chroma_wake_spin.setDecimals(3)
        self.cw_chroma_wake_spin.setSingleStep(0.01)
        self.cw_chroma_wake_spin.setValue(cw.DEFAULT_CHROMA_WAKE)
        enable_drag_to_scrub(self.cw_chroma_wake_spin)
        self.cw_chroma_wake_spin.valueChanged.connect(self._on_cw_chroma_wake_changed)
        chroma_wake_row.addWidget(_ParamLabel(
            "Chroma Wake:", lambda: self.cw_chroma_wake_spin.setValue(cw.DEFAULT_CHROMA_WAKE)))
        chroma_wake_row.addWidget(self.cw_chroma_wake_spin, stretch=1)
        outer.addLayout(chroma_wake_row)

        chroma_dropoff_row = QHBoxLayout()
        self.cw_chroma_dropoff_spin = QDoubleSpinBox()
        self.cw_chroma_dropoff_spin.setRange(0.0, 1.0)
        self.cw_chroma_dropoff_spin.setDecimals(2)
        self.cw_chroma_dropoff_spin.setSingleStep(0.05)
        self.cw_chroma_dropoff_spin.setValue(cw.DEFAULT_CHROMA_DROPOFF)
        enable_drag_to_scrub(self.cw_chroma_dropoff_spin)
        self.cw_chroma_dropoff_spin.valueChanged.connect(self._on_cw_chroma_dropoff_changed)
        chroma_dropoff_row.addWidget(_ParamLabel(
            "Chroma Drop-off:", lambda: self.cw_chroma_dropoff_spin.setValue(cw.DEFAULT_CHROMA_DROPOFF)))
        chroma_dropoff_row.addWidget(self.cw_chroma_dropoff_spin, stretch=1)
        outer.addLayout(chroma_dropoff_row)

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

    def _on_cw_chroma_wake_changed(self, value):
        self.cw_wheel.set_chroma_wake(value)
        self._apply_color_warper_live()

    def _on_cw_chroma_dropoff_changed(self, value):
        self.cw_wheel.set_chroma_dropoff(value)
        self._apply_color_warper_live()

    def _on_cw_space_changed(self, _text):
        self._apply_color_warper_live()

    def _on_cw_reset_clicked(self):
        # The wheel's own points_changed and each spin's own valueChanged
        # (_on_cw_wake_changed/_on_cw_dropoff_changed/_on_cw_chroma_wake_
        # changed/_on_cw_chroma_dropoff_changed) each apply live
        # independently -- suppressed here so resetting all five doesn't
        # fire 5 separate live-applies (each a full re-render, including
        # a color_warper stage's own relatively expensive OKLCh warp) for
        # what's conceptually ONE reset, same ordering-hazard/redundant-
        # apply fix as _reset_one_gpr_field's own docstring explains for
        # Grading Primary.
        self.cw_wheel.reset(emit=False)
        # blockSignals stops each spin's own valueChanged handler from
        # firing (which would otherwise apply live AND update the
        # wheel's own wake/dropoff state) -- so the wheel's copy of that
        # state has to be set directly here instead, mirroring exactly
        # what each blocked handler would have done.
        for spin, default, wheel_setter in (
                (self.cw_wake_spin, cw.DEFAULT_WAKE, self.cw_wheel.set_wake),
                (self.cw_dropoff_spin, cw.DEFAULT_DROPOFF, self.cw_wheel.set_dropoff),
                (self.cw_chroma_wake_spin, cw.DEFAULT_CHROMA_WAKE, self.cw_wheel.set_chroma_wake),
                (self.cw_chroma_dropoff_spin, cw.DEFAULT_CHROMA_DROPOFF, self.cw_wheel.set_chroma_dropoff)):
            spin.blockSignals(True)
            spin.setValue(default)
            spin.blockSignals(False)
            wheel_setter(default)
        self._apply_color_warper_live()

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
        stage.params["chroma_wake"] = self.cw_chroma_wake_spin.value()
        stage.params["chroma_dropoff"] = self.cw_chroma_dropoff_spin.value()
        stage.params["working_colourspace"] = self.cw_space_combo.currentText()
        self.cw_cloud.set_warp(stage.params["points"], stage.params["wake"], stage.params["dropoff"],
                                stage.params["chroma_wake"], stage.params["chroma_dropoff"])
        self._update_row_content(stage.id)
        self._emit_changed()

    def _refresh_color_warper_inspector(self, stage):
        p = stage.params
        self.cw_wheel.set_points(p.get("points") or [], emit=False)
        wake = p.get("wake", cw.DEFAULT_WAKE)
        dropoff = p.get("dropoff", cw.DEFAULT_DROPOFF)
        chroma_wake = p.get("chroma_wake", cw.DEFAULT_CHROMA_WAKE)
        chroma_dropoff = p.get("chroma_dropoff", cw.DEFAULT_CHROMA_DROPOFF)
        self.cw_wheel.set_wake(wake)
        self.cw_wheel.set_dropoff(dropoff)
        self.cw_wheel.set_chroma_wake(chroma_wake)
        self.cw_wheel.set_chroma_dropoff(chroma_dropoff)
        self.cw_wake_spin.setValue(wake)
        self.cw_dropoff_spin.setValue(dropoff)
        self.cw_chroma_wake_spin.setValue(chroma_wake)
        self.cw_chroma_dropoff_spin.setValue(chroma_dropoff)
        idx = self.cw_space_combo.findText(p.get("working_colourspace", cw.DEFAULT_WORKING_COLOURSPACE))
        if idx >= 0:
            self.cw_space_combo.setCurrentIndex(idx)
        self.inspector_stack.setCurrentIndex(self._color_warper_inspector_index)
        # Sync the cloud to THIS stage's own warp -- different
        # color_warper stages can have different points/wake/dropoff/
        # chroma_wake/chroma_dropoff, so switching stages needs to
        # update it too, not just leave it showing whichever stage
        # was selected before.
        self.cw_cloud.set_warp(p.get("points") or [], wake, dropoff, chroma_wake, chroma_dropoff)
