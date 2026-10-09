"""
media_grid.py -- the Media Libraries panel's Grid view (2026-10-09): the
library's entries and timelines as thumbnail cards that wrap to the
panel's width, like a media pool's thumbnail view. The List view (the
sortable, columned table in media_panel.py) stays the source of truth:
MediaPanel feeds this grid one GridItem per VISIBLE table row, in the
table's current (sorted) order, so filtering and sorting carry over, and
mirrors this grid's selection back into the table's rows -- every action
(context menu, double-click, rename, delete, drag) then runs exactly as
it does from the list.

Card: a 16:9 thumbnail (cropped to fill) with the duration in its corner,
then the name and a mono line of format details. Timelines get a plain
card with a "Timeline" label; the one loaded in the Timeline panel is
marked with the accent colour. Selection: click, Ctrl-click (toggle),
Shift-click (range); the selected cards get the accent outline.

Qt widget, custom-painted; meant to sit in a QScrollArea with
setWidgetResizable(True) -- it wraps to its width and sets its own
minimum height from the number of rows.
"""
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

import media_cache
import style

CARD_WIDTH = 176
THUMB_HEIGHT = 99         # 16:9 at CARD_WIDTH
CAPTION_HEIGHT = 40
GAP = 10
MARGIN = 10
CARD_RADIUS = 6


@dataclass
class GridItem:
    row: int              # the MediaPanel table row this card stands for
    name: str
    details: str = ""     # "1920x1080 · 24 · ProRes 4444"
    corner_text: str = "" # drawn over the thumbnail (duration)
    thumbnail: object = None  # a media_cache thumbnail descriptor
    is_timeline: bool = False
    is_loaded: bool = False   # timeline loaded in the Timeline panel


class MediaGrid(QWidget):
    selection_changed = Signal(list)          # table rows, in grid order
    activated = Signal(int)                   # double-clicked row
    context_menu_requested = Signal(int, QPoint)  # row, global position
    drag_requested = Signal(list)             # rows to drag

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []
        self._selected_rows = set()
        self._anchor_index = None
        self._press_pos = None
        self._pixmaps = {}
        self.setMouseTracking(True)

    # ---- data --------------------------------------------------------------

    def set_items(self, items):
        self._items = list(items)
        rows = {item.row for item in self._items}
        self._selected_rows &= rows
        self._update_height()
        self.update()

    def items(self):
        return list(self._items)

    def selected_rows(self):
        return [item.row for item in self._items if item.row in self._selected_rows]

    def set_selected_rows(self, rows):
        """Programmatic (mirrored from the table) -- no signal."""
        self._selected_rows = set(rows)
        self.update()

    # ---- geometry ----------------------------------------------------------

    def _columns(self):
        usable = max(0, self.width() - 2 * MARGIN + GAP)
        return max(1, usable // (CARD_WIDTH + GAP))

    def card_rect(self, index):
        columns = self._columns()
        row, column = divmod(index, columns)
        return QRectF(MARGIN + column * (CARD_WIDTH + GAP),
                      MARGIN + row * (THUMB_HEIGHT + CAPTION_HEIGHT + GAP),
                      CARD_WIDTH, THUMB_HEIGHT + CAPTION_HEIGHT)

    def index_at(self, pos):
        for index in range(len(self._items)):
            if self.card_rect(index).contains(pos):
                return index
        return None

    def _update_height(self):
        rows = -(-len(self._items) // self._columns()) if self._items else 0
        height = 2 * MARGIN + rows * (THUMB_HEIGHT + CAPTION_HEIGHT) + max(0, rows - 1) * GAP
        self.setMinimumHeight(height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_height()

    # ---- input -------------------------------------------------------------

    def _emit_selection(self):
        self.update()
        self.selection_changed.emit(self.selected_rows())

    def mousePressEvent(self, event):
        index = self.index_at(event.position())
        if event.button() == Qt.MouseButton.RightButton:
            if index is not None:
                row = self._items[index].row
                if row not in self._selected_rows:
                    self._selected_rows = {row}
                    self._anchor_index = index
                    self._emit_selection()
                self.context_menu_requested.emit(row, event.globalPosition().toPoint())
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self._press_pos = event.position()
        modifiers = event.modifiers()
        if index is None:
            if not modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
                self._selected_rows = set()
                self._emit_selection()
            return
        row = self._items[index].row
        if modifiers & Qt.KeyboardModifier.ShiftModifier and self._anchor_index is not None:
            lo, hi = sorted((self._anchor_index, index))
            self._selected_rows = {self._items[i].row for i in range(lo, hi + 1)}
        elif modifiers & Qt.KeyboardModifier.ControlModifier:
            self._selected_rows ^= {row}
            self._anchor_index = index
        else:
            if row not in self._selected_rows or len(self._selected_rows) > 1:
                self._selected_rows = {row}
            self._anchor_index = index
        self._emit_selection()

    def mouseMoveEvent(self, event):
        if (self._press_pos is not None and event.buttons() & Qt.MouseButton.LeftButton
                and self._selected_rows
                and (event.position() - self._press_pos).manhattanLength()
                >= QApplication.startDragDistance()):
            self._press_pos = None
            self.drag_requested.emit(self.selected_rows())

    def mouseReleaseEvent(self, event):
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        index = self.index_at(event.position())
        if index is not None and event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self._items[index].row)

    # ---- painting ----------------------------------------------------------

    def _pixmap(self, descriptor):
        if not descriptor:
            return None
        key = str(descriptor)
        if key not in self._pixmaps:
            png = media_cache.load_thumbnail(descriptor)
            pixmap = QPixmap()
            self._pixmaps[key] = pixmap if png and pixmap.loadFromData(png, "PNG") else None
        return self._pixmaps[key]

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(style._DARK_BASE))
        if not self._items:
            painter.setPen(QColor(style._HINT_TEXT))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Drop files here, or ☰ → Import...")
            painter.end()
            return
        accent = self.palette().highlight().color()
        name_font = QFont(self.font())
        name_font.setPixelSize(12)
        detail_font = QFont(style.MONO_FONT_FAMILY)
        detail_font.setPixelSize(11)
        corner_font = QFont(style.MONO_FONT_FAMILY)
        corner_font.setPixelSize(10)
        visible = event.rect()
        for index, item in enumerate(self._items):
            rect = self.card_rect(index)
            if not rect.intersects(QRectF(visible)):
                continue
            self._paint_card(painter, item, rect, accent, name_font, detail_font, corner_font)
        painter.end()

    def _paint_card(self, painter, item, rect, accent, name_font, detail_font, corner_font):
        path = QPainterPath()
        path.addRoundedRect(rect, CARD_RADIUS, CARD_RADIUS)
        thumb = QRectF(rect.left(), rect.top(), CARD_WIDTH, THUMB_HEIGHT)
        painter.save()
        painter.setClipPath(path)
        painter.fillRect(rect, QColor(style._DARK_ALT_BASE))
        pixmap = self._pixmap(item.thumbnail)
        if pixmap is not None:
            scaled = pixmap.scaled(thumb.size().toSize(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                   Qt.TransformationMode.SmoothTransformation)
            painter.save()
            painter.setClipRect(thumb, Qt.ClipOperation.IntersectClip)
            painter.drawPixmap(int(thumb.left() + (thumb.width() - scaled.width()) / 2),
                               int(thumb.top() + (thumb.height() - scaled.height()) / 2), scaled)
            painter.restore()
        else:
            painter.fillRect(thumb, QColor(style._FIELD_BG))
            painter.setPen(QColor(style._HINT_TEXT))
            painter.setFont(name_font)
            painter.drawText(thumb, Qt.AlignmentFlag.AlignCenter,
                             "Timeline" if item.is_timeline else "No preview")
        if item.corner_text:
            painter.setFont(corner_font)
            metrics = QFontMetrics(corner_font)
            text_w = metrics.horizontalAdvance(item.corner_text) + 8
            chip = QRectF(thumb.right() - text_w - 4, thumb.bottom() - 18, text_w, 14)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 170))
            painter.drawRoundedRect(chip, 3, 3)
            painter.setPen(QColor(style._TEXT_PRIMARY))
            painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, item.corner_text)

        caption_top = rect.top() + THUMB_HEIGHT
        painter.setFont(name_font)
        painter.setPen(accent if item.is_loaded else QColor(style._TEXT_PRIMARY))
        name = ("▶ " if item.is_loaded else "") + item.name
        painter.drawText(QRectF(rect.left() + 8, caption_top + 4, CARD_WIDTH - 16, 16),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         QFontMetrics(name_font).elidedText(name, Qt.TextElideMode.ElideMiddle, CARD_WIDTH - 16))
        painter.setFont(detail_font)
        painter.setPen(QColor(style._HINT_TEXT))
        painter.drawText(QRectF(rect.left() + 8, caption_top + 21, CARD_WIDTH - 16, 14),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         QFontMetrics(detail_font).elidedText(item.details, Qt.TextElideMode.ElideRight, CARD_WIDTH - 16))
        painter.restore()

        selected = item.row in self._selected_rows
        pen = painter.pen()
        pen.setColor(accent if selected else QColor(style._DARK_MID))
        pen.setWidthF(2.0 if selected else 1.0)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        inset = 1.0 if selected else 0.5
        painter.drawRoundedRect(rect.adjusted(inset, inset, -inset, -inset), CARD_RADIUS, CARD_RADIUS)
