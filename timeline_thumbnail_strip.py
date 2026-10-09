"""
timeline_thumbnail_strip.py -- the Timeline panel's "Thumbnails" view: the
same timeline.Timeline as TimelineTrackWidget, shown as a horizontal strip
of clip cards instead of time-proportional blocks (style.py IDENTITY PASS,
2026-10-09: "we ARE going to need a thumbnail view, but maybe that could
be a version of the timeline" -- the grading screen's clip navigator,
like Resolve's Color-page clip strip).

Each card: the clip's cached thumbnail (media_cache, the same PNGs the
track view draws), its number and record-start timecode in mono, its
name, and an amber dot when the clip carries its own grade (any enabled
stage in clip.stack). The SELECTED clip gets the accent outline; the clip
under the PLAYHEAD gets its number in the accent colour. Clicking a card
selects it and asks to seek to its first frame (seek_requested) -- the
same two signals TimelineTrackWidget emits, so TimelinePanel wires both
views identically.

Qt widget, custom-painted (QPainter, no child widgets per card); it sizes
its own width to the cards so a QScrollArea scrolls it (see
TimelinePanel), and ensure_visible_rect() tells that scroll area where the
current card is.
"""
from PySide6.QtCore import Qt, QRectF, QSize, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

import media_cache
import style
import timecode as tc_module

CARD_WIDTH = 160
THUMB_HEIGHT = 90          # 16:9 at CARD_WIDTH
CAPTION_HEIGHT = 36
CARD_GAP = 8
MARGIN = 8
GRADE_DOT_RADIUS = 3.5
CARD_RADIUS = 6
STRIP_HEIGHT = MARGIN * 2 + THUMB_HEIGHT + CAPTION_HEIGHT


def _has_own_grade(clip):
    """True when the clip's own Stack has at least one enabled stage."""
    stack = getattr(clip, "stack", None)
    return bool(stack and any(getattr(s, "enabled", True) for s in stack.stages))


class TimelineThumbnailStrip(QWidget):
    clip_selected = Signal(object)   # a timeline.TimelineClip
    seek_requested = Signal(int)     # record frame (the clicked clip's first)

    def __init__(self, timeline=None, parent=None):
        super().__init__(parent)
        self.timeline = timeline
        self.selected_clip_id = None
        self._playhead_frame = None
        self._pixmap_cache = {}  # thumbnail descriptor (str) -> QPixmap or None
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(STRIP_HEIGHT)  # captions must never be cut off
        self.setMouseTracking(True)
        self._update_width()

    # ---- state -------------------------------------------------------------

    def _clips(self):
        return self.timeline.clips if self.timeline is not None else []

    def set_timeline(self, timeline):
        self.timeline = timeline
        self._update_width()
        self.update()

    def set_selected_clip_id(self, clip_id):
        """Programmatic selection (sync from the track view) -- does not
        emit clip_selected."""
        if clip_id != self.selected_clip_id:
            self.selected_clip_id = clip_id
            self.update()

    def set_playhead_frame(self, frame):
        if frame != self._playhead_frame:
            self._playhead_frame = frame
            self.update()

    def current_clip_index(self):
        """Index of the clip under the playhead, or None."""
        if self._playhead_frame is None:
            return None
        start = 0
        for index, clip in enumerate(self._clips()):
            if start <= self._playhead_frame < start + clip.frame_count:
                return index
            start += clip.frame_count
        return None

    # ---- geometry ----------------------------------------------------------

    def card_rect(self, index):
        x = MARGIN + index * (CARD_WIDTH + CARD_GAP)
        return QRectF(x, MARGIN, CARD_WIDTH, THUMB_HEIGHT + CAPTION_HEIGHT)

    def card_index_at(self, pos):
        for index in range(len(self._clips())):
            if self.card_rect(index).contains(pos):
                return index
        return None

    def ensure_visible_rect(self, index):
        """The card's rect, for a parent QScrollArea's ensureVisible."""
        return self.card_rect(index)

    def _update_width(self):
        count = len(self._clips())
        width = MARGIN * 2 + count * CARD_WIDTH + max(0, count - 1) * CARD_GAP
        self.setMinimumWidth(max(width, 1))
        self.resize(max(width, 1), STRIP_HEIGHT)

    def sizeHint(self):
        return QSize(self.minimumWidth(), MARGIN * 2 + THUMB_HEIGHT + CAPTION_HEIGHT)

    def minimumSizeHint(self):
        return QSize(0, MARGIN * 2 + THUMB_HEIGHT + CAPTION_HEIGHT)

    # ---- input -------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        index = self.card_index_at(event.position())
        if index is None:
            return
        clip = self._clips()[index]
        self.selected_clip_id = clip.id
        self.update()
        self.clip_selected.emit(clip)
        self.seek_requested.emit(self.timeline.record_start_of(clip.id))

    # ---- painting ----------------------------------------------------------

    def _thumbnail(self, descriptor):
        if not descriptor:
            return None
        key = str(descriptor)
        if key not in self._pixmap_cache:
            png = media_cache.load_thumbnail(descriptor)
            pixmap = QPixmap()
            self._pixmap_cache[key] = pixmap if png and pixmap.loadFromData(png, "PNG") else None
        return self._pixmap_cache[key]

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(style._DARK_BASE))
        clips = self._clips()
        if not clips:
            painter.setPen(QColor(style._HINT_TEXT))
            text = ("No timeline loaded" if self.timeline is None
                    else "No clips -- Add to Timeline from a Media Library")
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)
            painter.end()
            return

        accent = self.palette().highlight().color()
        current = self.current_clip_index()
        rate = self.timeline.frame_rate
        mono = QFont(style.MONO_FONT_FAMILY)
        mono.setPixelSize(11)
        name_font = QFont(self.font())
        name_font.setPixelSize(12)
        record = 0
        for index, clip in enumerate(clips):
            rect = self.card_rect(index)
            thumb_rect = QRectF(rect.left(), rect.top(), CARD_WIDTH, THUMB_HEIGHT)
            # Everything inside the card is clipped to its rounded shape.
            card_path = QPainterPath()
            card_path.addRoundedRect(rect, CARD_RADIUS, CARD_RADIUS)
            painter.save()
            painter.setClipPath(card_path)
            painter.fillRect(rect, QColor(style._DARK_ALT_BASE))
            pixmap = self._thumbnail(clip.thumbnail)
            if pixmap is not None:
                scaled = pixmap.scaled(thumb_rect.size().toSize(),
                                       Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                       Qt.TransformationMode.SmoothTransformation)
                painter.save()
                painter.setClipRect(thumb_rect, Qt.ClipOperation.IntersectClip)
                painter.drawPixmap(
                    int(thumb_rect.left() + (thumb_rect.width() - scaled.width()) / 2),
                    int(thumb_rect.top() + (thumb_rect.height() - scaled.height()) / 2),
                    scaled)
                painter.restore()
            else:
                painter.fillRect(thumb_rect, QColor(style._FIELD_BG))

            # Caption: "01  01:00:04:12" (mono) then the name, elided.
            caption_top = rect.top() + THUMB_HEIGHT
            painter.setFont(mono)
            painter.setPen(accent if index == current else QColor(style._HINT_TEXT))
            number = f"{index + 1:02d}"
            painter.drawText(QRectF(rect.left() + 6, caption_top + 2, 28, 16),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, number)
            painter.setPen(QColor(style._HINT_TEXT))
            painter.drawText(QRectF(rect.left() + 32, caption_top + 2, CARD_WIDTH - 50, 16),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             tc_module.frames_to_timecode(record, rate) if rate else str(record))
            painter.setFont(name_font)
            painter.setPen(QColor(style._TEXT_PRIMARY))
            name = QFontMetrics(name_font).elidedText(
                clip.name, Qt.TextElideMode.ElideRight, CARD_WIDTH - 12)
            painter.drawText(QRectF(rect.left() + 6, caption_top + 18, CARD_WIDTH - 12, 16),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name)

            if _has_own_grade(clip):
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(accent)
                painter.drawEllipse(QRectF(rect.right() - 12, caption_top + 6,
                                           GRADE_DOT_RADIUS * 2, GRADE_DOT_RADIUS * 2))

            painter.restore()  # the card clip

            selected = clip.id == self.selected_clip_id
            pen = painter.pen()
            pen.setColor(accent if selected else QColor(style._DARK_MID))
            pen.setWidthF(2.0 if selected else 1.0)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            inset = 1.0 if selected else 0.5
            painter.drawRoundedRect(rect.adjusted(inset, inset, -inset, -inset), CARD_RADIUS, CARD_RADIUS)
            record += clip.frame_count
        painter.end()
