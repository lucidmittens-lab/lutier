"""
hover_inspector.py -- a general-purpose "Alt+hover" info card (per Tom,
"like Flame"): holding Alt while hovering over an item in a supported
list-based panel shows a floating card near the cursor with that item's
full details, without needing to click/select it first. Several panels
(Browser, Stack Memories, LUT Library, CDL Library, Image Library, the
Stack itself) have entries with more detail than fits in a compact row
label, and some (Stack Memories) have no per-item detail view at all
otherwise.

ONE shared mechanism, not five different per-panel implementations: each
panel calls `register(list_widget, info_for_item)` once, where
`info_for_item(item) -> str | None` is a small callback describing what
that panel's own data means (plain text; a couple of newlines is fine,
this isn't rich HTML). Everything about WHEN to show/update/hide the
card -- Alt held AND a real item under the cursor, hidden the instant
either stops being true -- lives here, once.

APPLICATION-WIDE, NOT PER-WIDGET FOCUS: this deliberately does NOT rely
on the hovered widget having keyboard focus (e.g. watching that widget's
own keyPressEvent for Alt) -- hovering without clicking first is the
whole point of the feature, and a widget the mouse is merely sitting over
does not generally have focus. Instead, ONE application-wide event
filter (installed on QApplication itself) watches for mouse-move and
Alt key press/release events REGARDLESS of which widget currently has
focus, and on each one re-checks "what's under the cursor right now" via
QApplication.widgetAt(QCursor.pos()) -- the same global-position query a
native tooltip effectively relies on -- rather than depending on events
being delivered to a particular widget.
"""
from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QLabel

_ALT = Qt.KeyboardModifier.AltModifier

# Events worth re-evaluating "should the card be showing, and for what"
# on. MouseMove covers the common case (cursor moves while Alt is
# already down, or Alt goes down/up while ALSO moving); KeyPress/
# KeyRelease covers Alt going down/up while the cursor is stationary,
# which generates no mouse event at all but should still show/hide
# immediately, not wait for the next pixel of mouse movement. Leave
# covers the cursor exiting the application's own windows entirely
# (no widget-under-cursor at all at that point, so nothing else would
# catch it).
_WATCHED_EVENT_TYPES = (
    QEvent.Type.MouseMove, QEvent.Type.KeyPress, QEvent.Type.KeyRelease,
    QEvent.Type.Leave,
)


class _HoverCard(QLabel):
    """The floating card itself. One shared instance for the whole app
    (see HoverInspector.instance()) -- only one can ever be relevant at
    a time anyway, whatever's under the cursor right now."""

    def __init__(self):
        super().__init__(None)
        self.setObjectName("hoverInspectorCard")
        # ToolTip window type (not a plain frameless window): this is
        # what makes QApplication.widgetAt() skip over the card itself
        # when it happens to sit under the cursor (same reason a native
        # tooltip never re-triggers itself), on top of
        # WA_TransparentForMouseEvents below doing the same for actual
        # mouse event delivery. WindowStaysOnTopHint since this has to
        # stay visible above whatever panel it's describing, including a
        # floated one.
        self.setWindowFlags(
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setMargin(8)
        # Standalone popup -- not part of the main window's own QSS
        # cascade (STYLESHEET is applied to the QApplication, which
        # would still reach this via inheritance, but pinning it
        # explicitly here means this card's look doesn't shift if the
        # global stylesheet's own rules ever change for unrelated
        # widgets). Deliberately plain hardcoded colors, not a reach
        # into style.py's private _DARK_BASE/_HINT_TEXT constants --
        # same "don't import another module's underscore-prefixed
        # internals" convention this app already follows elsewhere
        # (see splash_launcher.py's own docstring on the same point).
        self.setStyleSheet(
            "QLabel#hoverInspectorCard {"
            " background-color: #1c1c1c;"
            " color: #e6e6e6;"
            " border: 1px solid #4a4a4a;"
            " border-radius: 4px;"
            " font-size: 11px;"
            "}"
        )
        self.hide()

    def show_info(self, text, global_pos):
        self.setText(text)
        self.adjustSize()
        self.move(self._clamped_position(global_pos))
        self.show()

    def _clamped_position(self, global_pos):
        # Small down-right offset from the cursor (per Tom: "replaces
        # the cursor" -- reads as appearing right where you're
        # pointing, not literally hiding the OS cursor, which Qt has no
        # clean cross-platform way to do anyway), then clamped to the
        # current screen's own geometry so the card can't render
        # partly off-screen near an edge/corner.
        pos = global_pos + QPoint(16, 16)
        screen = QApplication.screenAt(global_pos) or QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            pos.setX(min(pos.x(), geo.right() - self.width()))
            pos.setY(min(pos.y(), geo.bottom() - self.height()))
            pos.setX(max(pos.x(), geo.left()))
            pos.setY(max(pos.y(), geo.top()))
        return pos


class HoverInspector(QObject):
    """The application-wide event filter + registry -- see module
    docstring. Use the module-level register()/unregister() functions
    rather than this class directly; they lazily create the one shared
    instance this app ever needs."""

    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        super().__init__()
        self._registry = {}  # id(list_widget) -> (list_widget, info_for_item)
        self._card = _HoverCard()
        self._refreshing = False  # re-entrancy guard, see eventFilter's own comment
        QApplication.instance().installEventFilter(self)

    def register(self, list_widget, info_for_item):
        self._registry[id(list_widget)] = (list_widget, info_for_item)

    def unregister(self, list_widget):
        self._registry.pop(id(list_widget), None)

    def eventFilter(self, obj, event):
        # Re-entrancy guard -- confirmed on real hardware that _refresh's
        # own self._card.hide()/show() calls can synchronously trigger
        # MORE watched events (e.g. Leave) that Qt delivers through this
        # SAME application-wide filter before the outer _refresh() call
        # has returned, recursing. Ordinarily shallow and harmless, but a
        # real reproduced case (a burst of Stack edits driving many rapid
        # repaints while an unrelated widget elsewhere is also being
        # constructed) recursed deeply enough to exhaust the C stack --
        # a hard segfault, not a catchable RecursionError. This isn't
        # about the Viewer's own GPU rendering specifically -- ordinary
        # QListWidget construction reproduced it too -- so the safe fix
        # is here, at the one place both recursion legs pass through,
        # not in whichever caller happens to be generating events that
        # particular time.
        if self._refreshing:
            return False
        if event.type() in _WATCHED_EVENT_TYPES:
            self._refreshing = True
            try:
                self._refresh()
            finally:
                self._refreshing = False
        return False  # purely observational -- never actually consume the event

    def _registered_ancestor(self, widget):
        while widget is not None:
            entry = self._registry.get(id(widget))
            if entry is not None:
                return entry
            widget = widget.parentWidget()
        return None, None

    def _refresh(self):
        app = QApplication.instance()
        if not (app.keyboardModifiers() & _ALT):
            self._card.hide()
            return
        cursor_pos = QCursor.pos()
        widget = app.widgetAt(cursor_pos)
        list_widget, info_for_item = self._registered_ancestor(widget)
        if list_widget is None:
            self._card.hide()
            return
        item = list_widget.itemAt(list_widget.viewport().mapFromGlobal(cursor_pos))
        if item is None:
            self._card.hide()
            return
        info = info_for_item(item)
        if not info:
            self._card.hide()
            return
        self._card.show_info(info, cursor_pos)


def register(list_widget, info_for_item):
    """Call once per panel wanting Alt+hover info cards. `list_widget`:
    any QListWidget (or subclass) whose items should respond.
    `info_for_item`: callable(QListWidgetItem) -> str | None -- return
    None/"" for an item with nothing to show. No unregister() call is
    needed for the app's own lifetime-long panels; it exists for
    completeness (e.g. a panel that gets torn down before the app
    quits)."""
    HoverInspector.instance().register(list_widget, info_for_item)


def unregister(list_widget):
    HoverInspector.instance().unregister(list_widget)
