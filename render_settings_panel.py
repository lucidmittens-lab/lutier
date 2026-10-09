"""
render_settings_panel.py -- the "Render Settings" panel (2026-10-09): what
to render and how, ending in "Add to Render Queue". Part of the Render
suggested layout, beside Record and the Render Queue.

  Render       Timeline | Current Clip
  Output       Single File | Individual Clips   (a timeline only)
  Format       ProRes 4444, H.264, EXR/DPX/TIFF/PNG sequences
  Bit Depth    what the format can carry
  Size         Project | Source
  Colour       As Viewed | Working Space
  Location     a folder
  File Name    a template ({name} {clip} {reel} {timeline} {source} {n} {date})

The panel knows nothing about timelines or stacks: MainWindow gives it a
planner (set_planner) that turns a RenderRequest into render_job
RenderItems, or raises ValueError with what's missing. The panel uses it
for the live preview (first file name, frames, size) and emits
add_requested(RenderRequest) from the button.
"""
import os
from dataclasses import dataclass

from PySide6.QtCore import QStandardPaths, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

import canvas as cv
import render_job as rj

TIMELINE, CURRENT_CLIP = "timeline", "clip"


@dataclass
class RenderRequest:
    source: str = TIMELINE
    single_file: bool = True
    format_label: str = ""
    bit_depth: object = 10
    use_source_size: bool = False
    as_viewed: bool = True
    directory: str = ""
    name_template: str = rj.DEFAULT_NAME_TEMPLATE


def _default_directory():
    return (QStandardPaths.writableLocation(QStandardPaths.StandardLocation.MoviesLocation)
            or os.path.expanduser("~"))


class _Segmented(QFrame):
    """style.py's segmented control: exclusive checkable buttons."""

    def __init__(self, options, on_change, parent=None):
        super().__init__(parent)
        self.setObjectName("segmentedControl")
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons = {}
        for value, label in options:
            button = QPushButton(label)
            button.setObjectName("segmentButton")
            button.setCheckable(True)
            button.clicked.connect(lambda _c=False: on_change())
            self._group.addButton(button)
            layout.addWidget(button)
            self.buttons[value] = button
        next(iter(self.buttons.values())).setChecked(True)

    def value(self):
        return next(v for v, b in self.buttons.items() if b.isChecked())

    def set_value(self, value):
        self.buttons[value].setChecked(True)


class RenderSettingsPanel(QWidget):
    add_requested = Signal(object)   # RenderRequest

    def __init__(self, parent=None):
        super().__init__(parent)
        self._planner = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("metadataScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        content = QWidget()
        content.setObjectName("metadataContent")
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(10)

        changed = self._refresh_preview
        self.source_control = _Segmented(((TIMELINE, "Timeline"), (CURRENT_CLIP, "Current Clip")),
                                         self._on_source_changed)
        self.output_control = _Segmented(((True, "Single File"), (False, "Individual Clips")), changed)
        self.format_combo = QComboBox()
        for label, _kind in rj.available_formats():
            self.format_combo.addItem(label, label)
        self.format_combo.currentIndexChanged.connect(self._on_format_changed)
        self.bit_depth_combo = QComboBox()
        self.bit_depth_combo.currentIndexChanged.connect(lambda _i: changed())
        self.size_control = _Segmented(((False, "Project"), (True, "Source")), changed)
        self.colour_control = _Segmented(((True, "As Viewed"), (False, "Working Space")), changed)
        self.colour_control.buttons[True].setToolTip(
            "What Record shows: input transform, grade and the project's display transform")
        self.colour_control.buttons[False].setToolTip(
            "The grade in the project working space, without the display transform (for VFX pulls)")

        self.directory_edit = QLineEdit(_default_directory())
        self.directory_edit.textChanged.connect(lambda _t: changed())
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        location = QHBoxLayout()
        location.setSpacing(6)
        location.addWidget(self.directory_edit, stretch=1)
        location.addWidget(browse)
        self.name_edit = QLineEdit(rj.DEFAULT_NAME_TEMPLATE)
        self.name_edit.textChanged.connect(lambda _t: changed())
        tokens = QLabel(" ".join("{" + t + "}" for t in rj.TEMPLATE_TOKENS))
        tokens.setObjectName("metadataHint")
        tokens.setWordWrap(True)
        tokens.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self._section(layout, "Render", [("Render", self.source_control),
                                         ("Output", self.output_control)])
        self._section(layout, "Format", [("Format", self.format_combo),
                                         ("Bit Depth", self.bit_depth_combo),
                                         ("Size", self.size_control),
                                         ("Colour", self.colour_control)])
        self._section(layout, "Destination", [("Location", location),
                                              ("File Name", self.name_edit),
                                              ("", tokens)])

        caption = QLabel("Will Render")
        caption.setObjectName("metadataSection")
        layout.addWidget(caption)
        self.preview_label = QLabel()
        self.preview_label.setObjectName("metadataValueMono")
        self.preview_label.setWordWrap(True)
        self.preview_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.preview_label)
        self.summary_label = QLabel()
        self.summary_label.setObjectName("metadataHint")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.add_button = QPushButton("Add to Render Queue")
        self.add_button.setObjectName("primaryButton")
        self.add_button.clicked.connect(lambda: self.add_requested.emit(self.request()))
        layout.addWidget(self.add_button)
        layout.addStretch(1)

        self._on_format_changed()
        self._on_source_changed()

    @staticmethod
    def _section(layout, title, rows):
        caption = QLabel(title)
        caption.setObjectName("metadataSection")
        layout.addWidget(caption)
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(6)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        for label, field in rows:
            key = QLabel(label)
            key.setObjectName("metadataKey")
            form.addRow(key, field)  # a widget or a layout
        layout.addLayout(form)

    # ---- state ---------------------------------------------------------

    def set_planner(self, planner):
        """`planner(RenderRequest)` -> [render_job.RenderItem], or raises
        ValueError saying why there's nothing to render."""
        self._planner = planner
        self._refresh_preview()

    def request(self):
        return RenderRequest(
            source=self.source_control.value(),
            single_file=self.output_control.value(),
            format_label=self.format_combo.currentData() or "",
            bit_depth=self.bit_depth_combo.currentData(),
            use_source_size=self.size_control.value(),
            as_viewed=self.colour_control.value(),
            directory=self.directory_edit.text().strip(),
            name_template=self.name_edit.text())

    def set_request(self, request):
        """Restores the controls from `request` (e.g. a saved session)."""
        self.source_control.set_value(request.source)
        self.output_control.set_value(bool(request.single_file))
        index = self.format_combo.findData(request.format_label)
        if index >= 0:
            self.format_combo.setCurrentIndex(index)
        index = self.bit_depth_combo.findData(request.bit_depth)
        if index >= 0:
            self.bit_depth_combo.setCurrentIndex(index)
        self.size_control.set_value(bool(request.use_source_size))
        self.colour_control.set_value(bool(request.as_viewed))
        if request.directory:
            self.directory_edit.setText(request.directory)
        self.name_edit.setText(request.name_template or rj.DEFAULT_NAME_TEMPLATE)
        self._on_source_changed()

    def refresh(self):
        """Re-plan the preview (the timeline, clip or project changed)."""
        self._refresh_preview()

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_preview()

    # ---- handlers ------------------------------------------------------

    def _on_source_changed(self):
        timeline = self.source_control.value() == TIMELINE
        self.output_control.setEnabled(timeline)
        self._refresh_preview()

    def _on_format_changed(self, _index=None):
        label = self.format_combo.currentData()
        previous = self.bit_depth_combo.currentData()
        self.bit_depth_combo.blockSignals(True)
        self.bit_depth_combo.clear()
        if label:
            for depth in rj.format_bit_depths(label):
                self.bit_depth_combo.addItem(cv.BIT_DEPTH_LABELS[depth], depth)
            index = self.bit_depth_combo.findData(previous)
            self.bit_depth_combo.setCurrentIndex(max(0, index))
        self.bit_depth_combo.blockSignals(False)
        self._refresh_preview()

    def _browse(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Render To", self.directory_edit.text() or _default_directory())
        if directory:
            self.directory_edit.setText(directory)

    def _refresh_preview(self):
        if not hasattr(self, "add_button"):
            return  # still building
        request = self.request()
        problem, items = None, []
        if not request.directory:
            problem = "Choose a location."
        elif self._planner is None:
            problem = "Nothing to render yet."
        else:
            try:
                items = self._planner(request)
            except ValueError as e:
                problem = str(e)
            if not items and problem is None:
                problem = "Nothing to render."
        self.add_button.setEnabled(problem is None)
        if problem:
            self.preview_label.setText("")
            self.summary_label.setText(problem)
            return
        first = os.path.basename(items[0].output_path)
        if items[0].is_sequence:
            first = f"{os.path.basename(os.path.dirname(items[0].output_path))}/{first}"
        more = f"\n…and {len(items) - 1} more" if len(items) > 1 else ""
        self.preview_label.setText(first + more)
        self.summary_label.setText(rj.RenderJob("", items, id=0).summary()
                                   + f" · {items[0].frame_rate:g} fps")
