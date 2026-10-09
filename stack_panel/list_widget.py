"""stack_panel/list_widget.py -- _StackListWidget, the Stack panel's QListWidget:
drag-to-reorder (including into/out of fork branches), LUT file drops from
the Browser, and _RowTargets, the per-row drop-target record that
StackPanel._refresh_list builds for it.
"""
from collections import namedtuple
import shiboken6
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QPainter, QPen, QDrag, QPixmap
from PySide6.QtWidgets import (
    QListWidget, QAbstractItemView, QGraphicsOpacityEffect,
)
from browser_panel import LUT_PATH_MIME
from .rows import _ForkBranchGridRowWidget, _StageRowWidget


# One entry per _StackListWidget row (see StackPanel._row_insert_index,
# built fresh in _refresh_list) -- where a dropped/dragged stage should
# land if it's dropped at this row's position. `ordinary` is a plain
# self.stack.stages insertion index, used for a row OUTSIDE any fork
# region (an int) or None if this row is fork-interior. `branch_a`/
# `branch_b` are each a self.stack.stages insertion index for landing in
# that branch specifically, or None if this row is NOT fork-interior.
# Exactly one of (`ordinary`, the pair `branch_a`/`branch_b`) is
# populated for any given row -- never both, never neither -- which is
# what makes "drop resolves to no branch" structurally unreachable for
# any row inside a fork's two-column table (see _StackListWidget.
# _drop_branch/StackPanel._on_stage_reordered). Replaces the single
# plain int _row_insert_index held per row before forking became
# drag-native (2026-08-12).
_RowTargets = namedtuple("_RowTargets", ["ordinary", "branch_a", "branch_b"])


class _StackListWidget(QListWidget):
    """The stage list itself. Two custom drag/drop behaviors layered on
    top of plain QListWidget, both routed through StackPanel's own
    Stack/_refresh_list rather than Qt's built-in item-move machinery:

    - Reordering stages by dragging one to a new position.
    - Dragging a LUT file in from the Browser panel's file list (see
      browser_panel.py's LUT_PATH_MIME / _DraggableFileListWidget) to add
      it as a new stage at the drop position.

    Both go through StackPanel's callbacks instead of relying on Qt's
    default InternalMove drop handling, which moves the QListWidgetItem
    but is unreliable about carrying its setItemWidget() content along for
    the ride -- rebuilding the whole list from self.stack via
    _refresh_list() after every drop sidesteps that entirely."""

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self._panel = panel
        # So style.py can scope OFF the generic QListWidget::item:selected/
        # :hover rectangular background just for this list -- see that
        # rule's own comment on why: those global rules were painting a
        # sharp-cornered highlight rectangle behind/around each row's own
        # rounded "pill" card (_StageRowWidget's border-based selection
        # cue), a real, longstanding bug Tom caught (2026-08-12, "get rid
        # of the large highlight filling the regular 4 sided polygon
        # around the row's pill shaped entry") that had nothing to do
        # with this round's border-color simplification -- it was already
        # there, just never addressed, because the border cue used to be
        # bold/washed enough (round one) to not visually compete with it.
        self.setObjectName("stackListWidget")
        self._drop_indicator_row = None  # see dragMoveEvent/paintEvent below
        # Which column the indicator line should span for a fork-interior
        # row -- "a"/"b", or None for an ordinary row (full-width line).
        # See paintEvent.
        self._drop_indicator_branch = None
        # The stage id under the press that (maybe) becomes a drag -- see
        # mousePressEvent/_stage_id_at. NOT the same as currentRow()/
        # currentItem(): a fork-grid row can hold two independently
        # draggable stages (one per column), so drag source identity has
        # to be resolved at the stage level, not the QListWidget row
        # level. Only ever read by startDrag/dropEvent for the lifetime
        # of a single press-to-drop gesture.
        self._pressed_stage_id = None
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        # NOT Qt's built-in indicator -- dragMoveEvent below is a full
        # override that never calls super(), which is exactly what
        # QAbstractItemView's OWN dragMoveEvent would normally use to
        # compute and repaint that indicator; with it skipped entirely
        # (deliberately, per this class's docstring -- Qt's default
        # InternalMove drop handling doesn't reliably carry a row's
        # setItemWidget() content), setDropIndicatorShown(True) here would
        # be a no-op flag with nothing ever actually driving it. Painted
        # by hand instead -- see paintEvent -- driven by the exact same
        # _drop_row() this widget's own dropEvent already uses, so the
        # line always points at precisely where a drop will actually land.
        self.setDropIndicatorShown(False)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        # CopyAction, not MoveAction -- dropEvent below does the actual
        # data-level move itself (via _refresh_list(), see class
        # docstring). If a drop here resolves to Qt::MoveAction,
        # QAbstractItemView::startDrag()'s own post-drag cleanup ALSO
        # removes the dragged row from this same widget afterwards --
        # redundant with (and racing) our rebuild, and with only one stage
        # in the list that second removal empties it entirely (reported by
        # Tom: dragging a single stack entry into empty space made it
        # vanish). Every dropEvent branch below explicitly sets CopyAction
        # before accepting so that cleanup never fires.
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

    def mousePressEvent(self, event):
        """Records which STAGE (not which QListWidget row) the press
        landed on, for startDrag to use as the drag source -- see
        _pressed_stage_id's own comment in __init__ and _stage_id_at
        below. Doesn't consume/replace anything: super() still runs
        unconditionally, same current-row/selection tracking as before."""
        self._pressed_stage_id = self._stage_id_at(event.position().toPoint())
        super().mousePressEvent(event)

    def _stage_id_at(self, pos):
        """Resolves a viewport position to the specific stage id under
        it. For an ordinary row this is just that row's one stage. For a
        fork-grid row (see _ForkBranchGridRowWidget) it's whichever
        SIDE's cell `pos` actually falls in -- left and right hold
        independently draggable stages now, so "which row" alone isn't
        enough anymore. None for a position with nothing draggable under
        it: the "Branch A | Branch B" header, an _EmptyBranchCell, or
        genuinely outside every row."""
        item = self.itemAt(pos)
        if item is None:
            return None
        widget = self.itemWidget(item)
        if isinstance(widget, _StageRowWidget):
            return widget.stage_id
        if isinstance(widget, _ForkBranchGridRowWidget):
            # left_widget/right_widget's own .geometry() is in THIS
            # composite widget's local coordinates, not the viewport's --
            # translate pos into that space before testing either cell.
            local_pos = pos - widget.geometry().topLeft()
            for cell in (widget.left_widget, widget.right_widget):
                if isinstance(cell, _StageRowWidget) and cell.geometry().contains(local_pos):
                    return cell.stage_id
        return None

    def startDrag(self, supportedActions):
        """Custom drag visuals (per Tom, "drag ghost/lift on the dragged
        row"). Qt's default QAbstractItemView.startDrag() renders its
        drag pixmap from the delegate, which has nothing to paint for a
        setItemWidget()-covered row (see class docstring on why every row
        uses setItemWidget rather than a delegate) -- the default drag
        pixmap is blank/generic. This grabs the actual card's rendered
        pixels instead, composited at reduced opacity for a "lifted"
        look, and dims the SOURCE row for the duration of the drag (a
        "ghost" left behind) so it's obvious both where the card came
        from and where it currently is.

        Resolves the dragged widget via self._pressed_stage_id ->
        self._panel._stage_row_widgets (not self.currentItem()/
        itemWidget() -- those only identify a QListWidget ROW, which
        isn't precise enough once a fork-grid row can hold two
        independently draggable stages; _stage_id_at, called from
        mousePressEvent, already resolved the exact stage the press
        landed on). self._stage_row_widgets already maps every stage id
        to its real live card either way -- an ordinary row's own widget,
        or a fork cell's left_widget/right_widget -- so this needs no
        special-casing for which kind of row the drag started from.

        The dim is cleared unconditionally right after drag.exec()
        returns (that call blocks until the drag ends one way or
        another), covering a cancelled or dropped-outside-any-target drag
        too -- a successful reorder drop rebuilds the whole list via
        StackPanel._on_stage_reordered -> _refresh_list() anyway.

        REAL CRASH, found via Tom's own on-screen testing: a successful
        reorder drop doesn't just make that rebuild redundant, it makes
        touching `widget` afterward actively dangerous. dropEvent fires
        SYNCHRONOUSLY inside drag.exec()'s own nested event loop (Qt
        processes the drop before exec() returns control here), and
        _refresh_list() -> self.list_widget.clear() destroys every
        _StageRowWidget's underlying C++ object right then, including
        this row's -- so by the time this method's own `finally` runs,
        `widget` can already be a dangling Python wrapper around a
        deleted C++ object ("libshiboken: Internal C++ object
        (_StageRowWidget) already deleted", reproduced directly).
        shiboken6.isValid() is the correct guard for exactly this --
        same class of "don't touch a Qt object PySide's own wrapper
        outlived" issue wipe_compare_widget.py's isValid() checks guard
        against elsewhere in this codebase, just at the shiboken layer
        instead of QOpenGLWidget's own isValid()."""
        if self._pressed_stage_id is None:  # pragma: no cover -- defensive, e.g. header/empty cell
            return
        widget = self._panel._stage_row_widgets.get(self._pressed_stage_id)
        item = self.currentItem()
        drag = QDrag(self)
        # Qt's own DnD acceptance bookkeeping still wants real mimeData
        # on the QDrag -- its CONTENT is never actually read for the
        # internal-reorder case (dropEvent below identifies the source
        # via self._pressed_stage_id instead), so an empty list here if
        # there's genuinely no current item is a harmless fallback, not
        # a real gap.
        drag.setMimeData(self.model().mimeData([self.indexFromItem(item)] if item is not None else []))

        opacity_effect = None
        if widget is not None:
            pixmap = widget.grab()
            if not pixmap.isNull():
                lifted = QPixmap(pixmap.size())
                lifted.fill(Qt.GlobalColor.transparent)
                painter = QPainter(lifted)
                painter.setOpacity(0.85)
                painter.drawPixmap(0, 0, pixmap)
                painter.end()
                drag.setPixmap(lifted)
                drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))
            opacity_effect = QGraphicsOpacityEffect(widget)
            opacity_effect.setOpacity(0.35)
            widget.setGraphicsEffect(opacity_effect)

        try:
            drag.exec(supportedActions, Qt.DropAction.CopyAction)
        finally:
            if widget is not None and shiboken6.isValid(widget):
                widget.setGraphicsEffect(None)

    def _accepts(self, event):
        return event.mimeData().hasFormat(LUT_PATH_MIME) or event.source() is self

    def dragEnterEvent(self, event):
        if self._accepts(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._accepts(event):
            event.acceptProposedAction()
            pos = event.position().toPoint()
            self._set_drop_indicator(self._drop_row(pos), self._drop_branch(pos))
        else:
            event.ignore()
            self._set_drop_indicator(None, None)

    def dragLeaveEvent(self, event):
        self._set_drop_indicator(None, None)
        super().dragLeaveEvent(event)

    def _set_drop_indicator(self, row, branch_guess):
        """`branch_guess`: the raw, purely x-position-derived side from
        _drop_branch (always "a" or "b" while dragging, meaningless for
        an ordinary row) -- paintEvent below is what actually decides
        whether the indicator line should span the whole row (ordinary)
        or just that one column (fork-interior), by checking the target
        row's own _RowTargets via self._panel."""
        if row == self._drop_indicator_row and branch_guess == self._drop_indicator_branch:
            return
        self._drop_indicator_row = row
        self._drop_indicator_branch = branch_guess
        self.viewport().update()

    def _drop_row(self, pos):
        item = self.itemAt(pos)
        if item is None:
            return self.count()
        row = self.row(item)
        if pos.y() > self.visualItemRect(item).center().y():
            row += 1
        return row

    def _drop_branch(self, pos):
        """Which column a drop position falls in -- "a" (left) or "b"
        (right) -- always resolvable regardless of row, split at the
        exact horizontal midpoint of the viewport (both branch columns
        are laid out stretch=1 in _ForkBranchGridRowWidget, so the
        visual split always sits there too). StackPanel's drop-target
        resolver (see _RowTargets) only actually consults this for a
        fork-interior row; it's harmless to compute unconditionally for
        every drop, ordinary rows just ignore it."""
        return "a" if pos.x() < self.viewport().width() / 2 else "b"

    def dropEvent(self, event):
        self._set_drop_indicator(None, None)
        mime = event.mimeData()
        pos = event.position().toPoint()
        target_row = self._drop_row(pos)
        branch = self._drop_branch(pos)
        if mime.hasFormat(LUT_PATH_MIME):
            path = bytes(mime.data(LUT_PATH_MIME)).decode("utf-8")
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self._panel._on_lut_path_dropped(path, target_row, branch)
        elif event.source() is self:
            source_stage_id = self._pressed_stage_id
            # CopyAction here too (see __init__) -- this IS a move at the
            # data level, just one _on_stage_reordered/_refresh_list does
            # itself rather than handing back to Qt's own row-removal.
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self._panel._on_stage_reordered(source_stage_id, target_row, branch)
        else:
            event.ignore()

    def paintEvent(self, event):
        """Draws the drag-to-reorder drop-position indicator (see
        __init__'s comment on why this is hand-painted rather than Qt's
        built-in setDropIndicatorShown mechanism) as a highlighted line
        positioned exactly at the boundary _drop_row() would actually
        insert at -- above the row under the cursor, or below it,
        matching that method's own upper/lower-half split. Drawn AFTER
        super().paintEvent() so it sits on top of the already-painted
        rows/cards, not underneath them.

        Per Tom: for a fork-interior target row, the line should only
        span the ONE column the drop will actually land in, not the
        full row width -- a full-width line there would suggest the
        drop affects both branches, when it only ever affects one. Reads
        the target row's own _RowTargets (via self._panel -- see
        _refresh_list/_RowTargets) to tell an ordinary row (full width)
        apart from a fork-interior one (half width, whichever half
        _drop_indicator_branch says)."""
        super().paintEvent(event)
        if self._drop_indicator_row is None:
            return
        viewport = self.viewport()
        if self._drop_indicator_row < self.count():
            y = self.visualItemRect(self.item(self._drop_indicator_row)).top()
        elif self.count() > 0:
            y = self.visualItemRect(self.item(self.count() - 1)).bottom()
        else:
            y = 0
        x_start, x_end = 0, viewport.width()
        if self._panel is not None:
            row_targets = self._panel._row_insert_index
            if 0 <= self._drop_indicator_row < len(row_targets):
                targets = row_targets[self._drop_indicator_row]
                if targets.ordinary is None:  # fork-interior -- span just the one column
                    half = viewport.width() / 2
                    if self._drop_indicator_branch == "b":
                        x_start, x_end = half, viewport.width()
                    else:
                        x_start, x_end = 0, half
        painter = QPainter(viewport)
        try:
            pen = QPen(self.palette().highlight().color())
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawLine(int(x_start), y, int(x_end), y)
        finally:
            painter.end()

    def keyPressEvent(self, event):
        # Delete/Backspace removes the currently selected stage -- same
        # action as clicking that row's own "x" (_StageRowWidget.
        # delete_button) or the old Remove button this + that button
        # replaced (per Tom).
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._panel._on_remove_clicked()
            return
        super().keyPressEvent(event)
