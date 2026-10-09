"""
metadata_panel.py -- the "Metadata" panel (2026-10-09): everything known
about the clip selected in Media Libraries, grouped the way a media page
shows it -- Clip, Timing, Image, Camera, Audio, File. Part of the Media
suggested layout (to the right of the Source viewer).

Values come from the same cell functions as Media Libraries' columns
(media_panel._*_cell), so the panel and the table can never disagree,
plus the media_probe fields that only make sense spelled out (each colour
tag on its own row). A field with no value is left out rather than shown
empty. Values are selectable, for copying a timecode or path.

Selection: one media entry -> its details; one timeline -> the timeline's;
several items -> a count; nothing -> a hint. MainWindow wires
MediaPanel.selection_changed -> show_selection(*media_panel.selected_items()).
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFormLayout, QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

import media_cache
import media_panel as mp
import timecode

THUMB_WIDTH = 240


def entry_sections(entry):
    """[(section title, [(label, value), ...]), ...] for a media entry --
    only fields that have a value; sections left empty are dropped."""
    def cell(fn):
        return fn(entry)[0]

    md = entry.metadata
    sections = [
        ("Clip", [
            ("Name", entry.name),
            ("Reel", md.get("reel", "")),
            ("Clip", md.get("clip", "")),
            ("Kind", "Video" if entry.is_video else "Still"),
        ]),
        ("Timing", [
            ("Start TC", cell(lambda e: mp._timecode_cell(e, 0))),
            ("End TC", cell(lambda e: mp._timecode_cell(e, max(0, (e.frame_count or 1) - 1)))),
            ("Duration", cell(mp._duration_cell)),
            ("Frames", cell(mp._frames_cell)),
            ("Frame Rate", cell(mp._fps_cell)),
        ]),
        ("Image", [
            ("Resolution", cell(mp._resolution_cell)),
            ("Pixel Aspect", cell(mp._par_cell)),
            ("Codec", md.get("codec", "")),
            ("Bit Depth", cell(mp._bit_depth_cell)),
            ("Chroma", cell(mp._chroma_cell)),
            ("Input Space", entry.source_colourspace or "Working space"),
            ("Primaries", md.get("colour_primaries", "")),
            ("Transfer", md.get("colour_transfer", "")),
            ("Matrix", md.get("colour_matrix", "")),
        ]),
        ("Camera", [
            ("Camera", md.get("camera", "")),
            ("Firmware", md.get("firmware", "")),
            ("Recorded", cell(mp._recorded_cell)),
        ]),
        ("Audio", [
            ("Channels", str(md.get("audio_channels", "")) if md.get("audio_channels") else ""),
            ("Sample Rate", f"{md['audio_sample_rate'] / 1000:g} kHz" if md.get("audio_sample_rate") else ""),
            ("Bit Depth", f"{md['audio_bit_depth']}-bit" if md.get("audio_bit_depth") else ""),
        ]),
        ("File", [
            ("Format", md.get("format", "")),
            ("File Name", entry.source_filename or ""),
            ("Size", cell(mp._size_cell)),
            ("Modified", cell(mp._modified_cell)),
            ("Path", entry.source_path or ""),
        ]),
    ]
    return [(title, [(k, v) for k, v in rows if v]) for title, rows in sections
            if any(v for _k, v in rows)]


def timeline_sections(timeline):
    frames = timeline.total_frame_count()
    rate = timeline.frame_rate
    return [
        ("Timeline", [
            ("Name", timeline.name),
            ("Clips", str(len(timeline.clips))),
            ("Duration", mp._format_duration(timeline.duration)),
            ("Frames", str(frames)),
            ("Frame Rate", f"{round(rate, 3):g}"),
            ("Record In", timecode.frames_to_timecode(0, rate)),
            ("Record Out", timecode.frames_to_timecode(max(0, frames - 1), rate)),
        ]),
    ]


# Values that read better in the mono face (numbers that line up).
_MONO_LABELS = {"Start TC", "End TC", "Duration", "Frames", "Frame Rate", "Resolution",
                "Pixel Aspect", "Record In", "Record Out", "Size", "Sample Rate",
                "Recorded", "Modified", "Reel", "Clip"}


class MetadataPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea()
        self._scroll.setObjectName("metadataScroll")
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self._scroll)
        self._sections = []
        self.show_selection([], [])

    def sections(self):
        """What's currently shown, as data (for tests)."""
        return list(self._sections)

    def show_selection(self, entries, timelines):
        total = len(entries) + len(timelines)
        if total == 0:
            self._show(None, None, [], hint="Select a clip in Media Libraries to see its details.")
        elif total > 1:
            self._show(None, None, [], hint=f"{total} items selected.")
        elif entries:
            entry = entries[0]
            self._show(entry.name, entry.thumbnail, entry_sections(entry))
        else:
            self._show(timelines[0].name, None, timeline_sections(timelines[0]))

    def _show(self, title, thumbnail, sections, hint=None):
        self._sections = sections
        content = QWidget()
        content.setObjectName("metadataContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(10)
        if hint:
            label = QLabel(hint)
            label.setWordWrap(True)
            label.setObjectName("metadataHint")
            layout.addWidget(label)
        if thumbnail:
            png = media_cache.load_thumbnail(thumbnail)
            pixmap = QPixmap()
            if png and pixmap.loadFromData(png, "PNG"):
                thumb = QLabel()
                thumb.setPixmap(pixmap.scaledToWidth(
                    THUMB_WIDTH, Qt.TransformationMode.SmoothTransformation))
                layout.addWidget(thumb)
        if title:
            heading = QLabel(title)
            heading.setObjectName("metadataTitle")
            heading.setWordWrap(True)
            heading.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(heading)
        for section_title, rows in sections:
            caption = QLabel(section_title)
            caption.setObjectName("metadataSection")
            layout.addWidget(caption)
            form = QFormLayout()
            form.setHorizontalSpacing(12)
            form.setVerticalSpacing(4)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
            for key, value in rows:
                name = QLabel(key)
                name.setObjectName("metadataKey")
                shown = QLabel(str(value))
                shown.setObjectName("metadataValueMono" if key in _MONO_LABELS else "metadataValue")
                shown.setWordWrap(True)
                shown.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                form.addRow(name, shown)
            layout.addLayout(form)
        layout.addStretch(1)
        self._scroll.setWidget(content)  # replaces (and deletes) the previous one
