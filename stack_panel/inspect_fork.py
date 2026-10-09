"""stack_panel/inspect_fork.py -- inspector pages for Fork Split and Fork Merge
(the A/B blend). A mixin: StackPanel inherits it, so these methods share the
panel's own `self`.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QGroupBox, QSlider,
)
from style import HINT_TEXT_HEX
from .labels import _format_fork_merge_label


class ForkInspectorMixin:
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

    def _refresh_fork_split_inspector(self, stage):
        self.inspector_stack.setCurrentIndex(self._fork_split_inspector_index)

    def _refresh_fork_merge_inspector(self, stage):
        blend = stage.params.get("blend", 0.5)
        self.fork_merge_blend_slider.blockSignals(True)
        self.fork_merge_blend_slider.setValue(round(blend * 100))
        self.fork_merge_blend_slider.blockSignals(False)
        a_pct = round((1.0 - blend) * 100)
        b_pct = round(blend * 100)
        self.fork_merge_blend_value_label.setText(f"A {a_pct}% / B {b_pct}%")
        self.inspector_stack.setCurrentIndex(self._fork_merge_inspector_index)
