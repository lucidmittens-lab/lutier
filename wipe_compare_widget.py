"""
wipe_compare_widget.py -- an interactive before/after image compare: one
overlapping view with a draggable vertical divider, instead of two
side-by-side panels. Which source is on the left vs. right (Sample
Before/Sample After/Reference) is entirely sample_image_panel.py's call
(see its Left/Right "..." menu submenus) -- this widget just draws
whatever pixmaps it's handed via set_before/set_after, with no opinion on
what they actually are. There USED to be a fixed "BEFORE"/"AFTER" corner
label overlay, but with three independently-pickable sources per side that
label was wrong as often as it was right (per Tom) -- removed entirely
rather than taught to ask sample_image_panel.py what's actually loaded on
each side, see paintEvent's comment on why.

FIRST PASS on "make the before/after more attractive and wipeable" --
functional and interactive, but the visual styling (colors, handle shape)
is a starting point for iteration, not a settled opinion. Easy knobs if
the look needs to change: HANDLE_COLOR, HANDLE_WIDTH, HANDLE_GRIP_RADIUS,
LABEL_COLOR/LABEL_BG below (the latter two now only style the color
readout chip, see set_color_readout_text).

NOT TESTED IN THIS ENVIRONMENT -- same PySide6/libEGL wall as the rest of
the GUI layer. Only python3 -m py_compile syntax-checked -- drag feel and
the actual visual result are exactly the kind of thing that needs a real
display and a real mouse to judge. Please drag the handle around and say
whether it feels right (grab precision, handle visibility) before
treating this as done rather than a draft.

ELEVENTH PASS (explicit background fill, 2026-07-25) -- Tom, after #63's
color correction landed, reported the space in the panel body AROUND the
actual image (the letterboxed gutter when the image's aspect ratio
doesn't match this widget's, and the full canvas before any image is
loaded at all) reads as a noticeably lighter gray than the new
`#3D3D3D` panel color. Checked empirically first, not just by reasoning
about it (same "don't trust a theory, grab a screenshot" discipline
style.py's own history keeps insisting on): a headless `win.grab()` shows
every one of those pixels ALREADY numerically equal to `#3D3D3D` --
this widget's `paintEvent` never explicitly filled its own background,
it was relying on transparency to let the ancestor `QFrame#panelFrame`'s
`palette(base)` fill show through underneath, undrawn, and that happened
to composite correctly in this sandbox's offscreen platform.

That's exactly the kind of implicit, unverified-on-real-hardware
assumption that already caused two real (not cosmetic) bugs in this same
file's neighborhood (RUNNING.md #44's border-image tiling and #46's
title-bar texture bleed-through) -- relying on "whatever's already
painted underneath happens to show through correctly" instead of this
widget owning its own background outright. Rather than treat the
headless match as proof there's nothing to fix, paintEvent now
unconditionally fills its ENTIRE rect with `palette(base)` as the very
first paint operation, before the empty-state text, the placeholder
rect, or either pixmap -- so this widget's background is no longer
dependent on what any ancestor happens to have already drawn, headless
or on real hardware alike. Uses `self.palette().color(QPalette.
ColorRole.Base)` rather than a hardcoded hex, consistent with every
theme color elsewhere in this app (style.py's `palette(base)` QSS token
resolves from the exact same fixed-palette source, see style.py's
`build_dark_palette`) -- HANDLE_COLOR/LABEL_BG/PLACEHOLDER_BG below stay
hardcoded on purpose (this module's own docstring already explains why:
they're drawn ON TOP of arbitrary image content, not part of the app's
theme-following chrome), but the widget's own plain background is chrome,
not image content, so it follows the theme like everything else.
"""
from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QPalette
from PySide6.QtWidgets import QWidget, QSizePolicy

HANDLE_COLOR = QColor(255, 255, 255, 230)
HANDLE_WIDTH = 2
HANDLE_GRIP_RADIUS = 9
LABEL_COLOR = QColor(255, 255, 255, 210)
LABEL_BG = QColor(0, 0, 0, 140)
EMPTY_TEXT_COLOR = QColor(150, 150, 150)
PLACEHOLDER_BG = QColor(40, 40, 40)

# Zoom/pan (per Tom) -- ZOOM_STEP is the multiplier applied per "notch"
# of scroll wheel (angleDelta().y() normalized to /120, the traditional
# one-notch unit -- see wheelEvent), so the step size is independent of
# whatever units/resolution a given mouse or trackpad actually reports.
# MIN/MAX_ZOOM bound how far in/out you can go relative to "fit" (1.0) --
# wide enough for real use, not so wide that a runaway scroll lands
# somewhere useless (a single pixel, or so far out the image is a speck).
ZOOM_STEP = 1.1
MIN_ZOOM = 0.1
MAX_ZOOM = 20.0


class WipeCompareWidget(QWidget):
    """set_before(pixmap) / set_after(pixmap) to update images. Both are
    expected to be the same underlying image at the same size (one
    processed, one not) -- drawn into the same scaled/centered rect so the
    wipe lines up pixel-for-pixel. Not meant to align mismatched images."""

    wipe_changed = Signal(float)
    # Fires on every actual size change (not just paint) -- SampleImagePanel
    # listens to this to know when it might be worth reloading the source
    # at a higher resolution than whatever's currently cached. Emitted from
    # resizeEvent, not paintEvent, since a resize is the only thing that
    # changes the answer to "is the current preview resolution enough".
    resized = Signal()
    # Same idea as `resized`, for zoom (per Tom, following up on the zoom/
    # pan feature): zooming in raises the answer to "is the current
    # preview resolution enough" exactly the same way growing the panel
    # does -- SampleImagePanel listens to this the same way it already
    # listens to `resized` (see _desired_preview_dimension_for, which
    # factors in self.zoom() now too). Fired from wheelEvent/reset_view
    # only when self._zoom actually changes, not on every wheel tick that
    # happens to be clamped at MIN_ZOOM/MAX_ZOOM already.
    zoom_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._before = None
        self._after = None
        self._after_placeholder_text = "Load a LUT to see the result"
        self._wipe_fraction = 0.5
        self._dragging = False
        self._color_readout_text = ""
        # See set_wipe_enabled -- when False, paintEvent draws only
        # `_after` full-panel (whatever single source sample_image_panel.py
        # currently has selected), no divider/handle. `_before` is simply
        # ignored while this is False, not cleared -- re-enabling the wipe
        # doesn't lose whatever was on the left side.
        self._wipe_enabled = True
        # Zoom/pan (per Tom) -- zoom is a multiplier on top of the normal
        # "fit to panel" scale _image_rect already computes (1.0 = fit,
        # unchanged from before this existed), pan is a widget-space
        # pixel offset added to the centered position. Deliberately NOT
        # reset by set_before/set_after: those fire on every single live
        # stack edit (see sample_image_panel.py's refresh_preview), so
        # resetting here would mean zooming in to inspect a detail while
        # grading gets thrown away on the very next slider tick -- see
        # reset_view for the actual, explicit way to get back to "fit".
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self._panning = False
        self._pan_start_mouse = None
        self._pan_start_offset = None
        self.setMinimumHeight(220)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_before(self, pixmap):
        self._before = pixmap
        self.update()

    def set_after(self, pixmap):
        self._after = pixmap
        self.update()

    def set_after_placeholder(self, text):
        """Message shown in the "after" area when set_after(None) --
        lets callers explain WHY there's nothing there (no LUT loaded vs.
        an empty concatenate chain are different situations that call for
        different guidance)."""
        self._after_placeholder_text = text
        self.update()

    def clear(self):
        self._before = None
        self._after = None
        self.update()

    def set_color_readout_text(self, text):
        """The "Color: Unmanaged" / "Color: X -> Y" note (see
        SampleImagePanel._update_color_readout) -- drawn as a chip
        overlaid in the image's lower-right corner (per Tom) instead of
        its own row of app chrome above/below the image, using LABEL_BG/
        LABEL_COLOR (the same light-on-dark treatment the now-removed
        BEFORE/AFTER tags used) since it's sitting on top of arbitrary
        image content, not part of the app's own theme-following UI."""
        self._color_readout_text = text or ""
        self.update()

    def set_wipe_enabled(self, enabled):
        """Toggle between the divider-based before/after compare (True,
        the original/default behavior) and a plain single-image view
        (False) showing only whatever's currently set via set_after --
        see sample_image_panel.py's "Enable Wipe" entry in the "..."
        menu. `_before` is left as-is either way (see __init__), so
        flipping this back on doesn't require re-supplying the left
        side."""
        if self._wipe_enabled == enabled:
            return
        self._wipe_enabled = enabled
        self.update()

    def wipe_fraction(self):
        return self._wipe_fraction

    def zoom(self):
        """1.0 = fit (the original, only-ever behavior before zoom/pan
        existed) -- see SampleImagePanel._desired_preview_dimension_for,
        which factors this in when deciding how much source resolution
        the current view actually needs."""
        return self._zoom

    def set_wipe_fraction(self, fraction):
        self._wipe_fraction = max(0.0, min(1.0, fraction))
        self.update()
        self.wipe_changed.emit(self._wipe_fraction)

    def reset_wipe(self):
        self.set_wipe_fraction(0.5)

    def reset_view(self):
        """Resets zoom/pan back to "fit" -- the middle-mouse-button
        double-click gesture (per Tom). Deliberately a SEPARATE gesture
        from the existing left-button double-click above (reset_wipe) --
        zoom/pan and the wipe divider are two independent things a user
        might want to reset without disturbing the other, and middle vs.
        left button is exactly how this widget already keeps panning
        (middle-drag) from ever conflicting with the wipe drag
        (left-drag, see mousePressEvent)."""
        zoom_changed = self._zoom != 1.0
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self.update()
        if zoom_changed:
            self.zoom_changed.emit()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self.reset_view()
            return
        self.reset_wipe()

    def wheelEvent(self, event):
        """Scroll to zoom (per Tom), anchored on the cursor -- the image
        point currently under the cursor stays under it as the zoom
        level changes, the standard behavior in image viewers/DCC tools
        (zooming around the widget's center regardless of where the
        cursor actually is would feel wrong the moment you're not
        centered)."""
        old_rect = self._image_rect()
        if old_rect is None or old_rect.width() <= 0 or old_rect.height() <= 0:
            return
        cursor = event.position()
        # Cursor's position as a FRACTION of the current image rect
        # (0/1 = left/right edge) -- kept fixed across the zoom change,
        # rather than the absolute pixel position, since the rect itself
        # is about to change size. Extrapolates harmlessly (a fraction
        # outside [0,1]) if the cursor is over the letterboxed gutter
        # rather than the image itself.
        rel_x = (cursor.x() - old_rect.left()) / old_rect.width()
        rel_y = (cursor.y() - old_rect.top()) / old_rect.height()

        # angleDelta().y() is in eighths of a degree, 120 per traditional
        # "one wheel notch" -- normalizing to notches keeps the zoom step
        # consistent regardless of whatever units/resolution a given
        # mouse or trackpad actually reports.
        notches = event.angleDelta().y() / 120.0
        if notches == 0:
            return
        new_zoom = max(MIN_ZOOM, min(MAX_ZOOM, self._zoom * (ZOOM_STEP ** notches)))
        if new_zoom == self._zoom:
            return
        self._zoom = new_zoom

        # Where that same relative point landed after the zoom change,
        # BEFORE correcting pan -- the gap between this and the cursor's
        # actual (unchanged) position is exactly how far pan needs to
        # move to put it back under the cursor.
        new_rect = self._image_rect()
        now_x = new_rect.left() + rel_x * new_rect.width()
        now_y = new_rect.top() + rel_y * new_rect.height()
        self._pan += QPointF(cursor.x() - now_x, cursor.y() - now_y)
        self.update()
        self.zoom_changed.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.size() != event.oldSize():
            self.resized.emit()

    # ---- geometry -----------------------------------------------------

    def _image_rect(self):
        """Centered, aspect-preserved rect both pixmaps are drawn into,
        sized off whichever pixmap is available. self._zoom (1.0 = fit,
        matching the original always-fit behavior exactly) multiplies
        the base fit scale; self._pan then offsets the centered position
        by a widget-space pixel amount -- see wheelEvent/mousePressEvent
        for how each actually changes. When zoomed in, this legitimately
        returns a rect LARGER than the widget's own -- paintEvent's
        existing QPainter (clipped to this widget by Qt already) simply
        shows whatever portion of it falls within the panel, same as any
        other image viewer's zoomed-in view."""
        reference = self._after if self._after is not None else self._before
        if reference is None or reference.isNull():
            return None
        img_w, img_h = reference.width(), reference.height()
        if img_w == 0 or img_h == 0:
            return None
        widget_rect = QRectF(self.rect())
        fit_scale = min(widget_rect.width() / img_w, widget_rect.height() / img_h)
        scale = fit_scale * self._zoom
        draw_w, draw_h = img_w * scale, img_h * scale
        x = (widget_rect.width() - draw_w) / 2 + self._pan.x()
        y = (widget_rect.height() - draw_h) / 2 + self._pan.y()
        return QRectF(x, y, draw_w, draw_h)

    # ---- painting -----------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # ELEVENTH PASS -- explicit, unconditional fill of this widget's
        # ENTIRE rect first, before anything else gets drawn. Previously
        # this widget never painted its own background at all and relied
        # on transparency to let panelFrame's palette(base) fill show
        # through underneath -- fine as long as nothing else ever sits
        # between them and every platform's compositor treats an
        # un-erased widget region identically, neither of which this
        # widget should have to assume. Covers both the empty-state (no
        # image_rect at all, early-return below) and the letterboxed
        # gutter around a loaded image whose aspect ratio doesn't match
        # this widget's own -- both previously left unpainted. Same
        # palette(base) source of truth as style.py's QSS everywhere
        # else, not a hardcoded hex -- this is chrome, not image content
        # (unlike HANDLE_COLOR/LABEL_BG/PLACEHOLDER_BG below, which stay
        # hardcoded on purpose, see this module's docstring).
        painter.fillRect(self.rect(), self.palette().color(QPalette.ColorRole.Base))

        image_rect = self._image_rect()
        if image_rect is None:
            painter.setPen(EMPTY_TEXT_COLOR)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                              "Import an image and load a LUT to preview")
            painter.end()
            return

        if self._after is not None and not self._after.isNull():
            painter.drawPixmap(image_rect, self._after, QRectF(self._after.rect()))
        else:
            painter.fillRect(image_rect, PLACEHOLDER_BG)
            painter.setPen(EMPTY_TEXT_COLOR)
            painter.drawText(image_rect, Qt.AlignmentFlag.AlignCenter,
                              self._after_placeholder_text)

        if not self._wipe_enabled:
            # Single-image mode (see set_wipe_enabled) -- whatever's in
            # `_after` already got drawn full-panel above; no divider or
            # handle for a view that isn't comparing two things. The color
            # readout chip still applies either way -- it's a fact about
            # the image, not the wipe.
            self._draw_color_readout(painter, image_rect, self._color_readout_text)
            painter.end()
            return

        divider_x = image_rect.left() + image_rect.width() * self._wipe_fraction

        if self._before is not None and not self._before.isNull():
            before_clip = QRectF(
                image_rect.left(), image_rect.top(),
                max(0.0, divider_x - image_rect.left()), image_rect.height(),
            )
            painter.save()
            painter.setClipRect(before_clip)
            painter.drawPixmap(image_rect, self._before, QRectF(self._before.rect()))
            painter.restore()

        painter.setPen(QPen(HANDLE_COLOR, HANDLE_WIDTH))
        painter.drawLine(QPointF(divider_x, image_rect.top()),
                          QPointF(divider_x, image_rect.bottom()))

        center_y = image_rect.top() + image_rect.height() / 2
        painter.setPen(QPen(QColor(60, 60, 60), 1))
        painter.setBrush(HANDLE_COLOR)
        painter.drawEllipse(QPointF(divider_x, center_y), HANDLE_GRIP_RADIUS, HANDLE_GRIP_RADIUS)

        # No BEFORE/AFTER corner labels anymore (per Tom) -- with Left/
        # Right each independently pickable from three sources (Sample
        # Before, Sample After, or Reference -- see sample_image_panel.py),
        # a fixed "BEFORE"/"AFTER" tag would be wrong as often as not (e.g.
        # Reference on the left, or Sample Before on the right), and
        # labeling each side correctly would mean this widget needing to
        # know the source keys/display names sample_image_panel.py owns.
        # Simpler to just drop the overlay entirely rather than plumb that
        # through -- the "..." menu's Left/Right submenus already show
        # which source is checked if you need a reminder.
        self._draw_color_readout(painter, image_rect, self._color_readout_text)

        painter.end()

    @staticmethod
    def _draw_color_readout(painter, image_rect, text):
        """Bottom-right chip -- not bold and not tied to the wipe divider
        position (unlike the now-removed BEFORE/AFTER corner tags used to
        be), since this is a static fact about the image, not a before/
        after label, so it's always shown regardless of where the divider
        currently sits."""
        if not text:
            return
        font = QFont()
        font.setPointSize(9)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        pad = 6
        text_w = metrics.horizontalAdvance(text) + pad * 2
        text_h = metrics.height() + pad
        x = image_rect.right() - text_w - 8
        y = image_rect.bottom() - text_h - 8
        bg_rect = QRectF(x, y, text_w, text_h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(LABEL_BG)
        painter.drawRoundedRect(bg_rect, 4, 4)
        painter.setPen(LABEL_COLOR)
        painter.drawText(bg_rect, Qt.AlignmentFlag.AlignCenter, text)

    # ---- mouse interaction ----------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            # Panning (per Tom) -- middle button specifically, so it can
            # never conflict with the left-button wipe-divider drag right
            # below: both can be reached from the exact same cursor
            # position without either one stealing the other's gesture.
            self._panning = True
            self._pan_start_mouse = event.position()
            self._pan_start_offset = QPointF(self._pan)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if not self._wipe_enabled:
            return
        image_rect = self._image_rect()
        if image_rect is not None and image_rect.contains(event.position()):
            self._dragging = True
            self._update_wipe_from_x(event.position().x(), image_rect)

    def mouseMoveEvent(self, event):
        if self._panning:
            delta = event.position() - self._pan_start_mouse
            self._pan = self._pan_start_offset + delta
            self.update()
            return
        if not self._wipe_enabled:
            return
        image_rect = self._image_rect()
        if image_rect is None:
            return
        if self._dragging:
            self._update_wipe_from_x(event.position().x(), image_rect)
        divider_x = image_rect.left() + image_rect.width() * self._wipe_fraction
        near_handle = abs(event.position().x() - divider_x) < HANDLE_GRIP_RADIUS + 4
        self.setCursor(
            Qt.CursorShape.SizeHorCursor if (near_handle or self._dragging)
            else Qt.CursorShape.ArrowCursor
        )

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = False
            self._pan_start_mouse = None
            self._pan_start_offset = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        self._dragging = False

    def _update_wipe_from_x(self, x, image_rect):
        if image_rect.width() <= 0:
            return
        fraction = (x - image_rect.left()) / image_rect.width()
        self.set_wipe_fraction(fraction)
