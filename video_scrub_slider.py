"""
video_scrub_slider.py -- VideoScrubSlider: a plain QSlider subclass that
paints IN/OUT point markers and a simple playhead over its own groove
(Phase 4 transport upgrade follow-up, per Tom: "hard to tell where the
marks are based on the current progress bar" -- then, on seeing that
first pass, "the handle and highlight on the scrub is ugly ... instead
of a knob there should be a simple white playhead").

Qt-only (no way around that for a custom paintEvent), but deliberately
kept to exactly one job -- drawing marks/playhead over positions it's
told about -- so sample_image_panel.py stays the only place that
decides WHAT those positions mean (in_point/out_point/current value on
VideoPlaybackController); this widget has no idea what a video or a
playback controller even is.

STYLING: the default QSlider chrome (a solid accent-colored "elapsed"
fill up to the handle, plus a round accent-colored knob) is suppressed
via style.py's QSlider#videoScrubSlider QSS rules (flat single-color
groove, invisible handle) -- this class draws its OWN thin white
playhead line at the current value instead, replacing the knob rather
than just recoloring it. objectName is set here (not by callers) so the
QSS selector always matches regardless of who constructs this widget."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider

# Thin marker lines at in_point/out_point -- no fill between them (an
# earlier version filled the whole active range, which read as a "yellow
# bar across everything," per direct feedback -- removed in favor of
# just marking the two boundary positions).
_MARK_COLOR = QColor(255, 170, 60, 220)
_MARK_WIDTH = 2

# Simple white playhead line at the current value -- replaces the
# default round handle entirely, per direct feedback ("instead of a
# knob there should be a simple white playhead"). Drawn a couple of
# pixels taller than the groove itself so it stays visible/distinct from
# the marker lines even when it lands exactly on one.
_PLAYHEAD_COLOR = QColor(255, 255, 255, 235)
_PLAYHEAD_WIDTH = 2
_PLAYHEAD_OVERHANG = 2


class VideoScrubSlider(QSlider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setObjectName("videoScrubSlider")
        self._in_point = None
        self._out_point = None

    def _value_from_x(self, x):
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        groove_rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderGroove, self)
        span = max(1, groove_rect.width() - 1)
        pos = x - groove_rect.x()
        return QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), pos, span, opt.upsideDown)

    def mousePressEvent(self, event):
        """Jump straight to the clicked position (per direct feedback:
        "the scrub bar still only moves slightly when you click far from
        the playhead") -- default QSlider behavior on a groove click
        (not the handle) is a single page-step nudge TOWARD the click,
        not a jump TO it, which reads as "barely moves" for anything but
        a click right next to the current playhead. There's no real
        handle to preserve drag-from-the-handle behavior for anyway (see
        module docstring -- the playhead is hand-painted, not the actual
        QSlider handle, which stays invisible via style.py's own QSS),
        so every left-click here jumps directly, and mouseMoveEvent below
        keeps tracking the cursor the same way for a click-and-drag."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.setValue(self._value_from_x(event.position().x()))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.setValue(self._value_from_x(event.position().x()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def set_in_out(self, in_point, out_point):
        """`in_point`/`out_point` are frame indices (matching this
        slider's own minimum()/maximum() range) -- `out_point=None` is
        drawn at this slider's own maximum() (the clip's last frame),
        matching VideoPlaybackController's own "None means the end of
        the clip" convention (see that class's docstring). Passing
        (None, None) clears the markers back to a plain slider (still
        shows the playhead -- that's independent of in/out state)."""
        self._in_point = in_point
        self._out_point = out_point
        self.update()

    def _groove_x_for_value(self, value, groove_rect, opt):
        span = max(0, groove_rect.width() - 1)
        pos = QStyle.sliderPositionFromValue(
            self.minimum(), self.maximum(), value, span, opt.upsideDown)
        return groove_rect.x() + pos

    def paintEvent(self, event):
        super().paintEvent(event)  # flat groove, no visible handle -- see style.py's QSS
        if self.maximum() <= self.minimum():
            return

        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        groove_rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderGroove, self)
        if groove_rect.width() <= 0:
            return

        painter = QPainter(self)

        if self._in_point is not None or self._out_point is not None:
            in_point = self._in_point if self._in_point is not None else self.minimum()
            out_point = self._out_point if self._out_point is not None else self.maximum()
            for value in (in_point, out_point):
                x = self._groove_x_for_value(value, groove_rect, opt)
                painter.fillRect(int(x - _MARK_WIDTH / 2), groove_rect.top(),
                                  _MARK_WIDTH, groove_rect.height(), _MARK_COLOR)

        # Playhead drawn LAST so it always sits on top of the in/out marks.
        playhead_x = self._groove_x_for_value(self.value(), groove_rect, opt)
        painter.fillRect(
            int(playhead_x - _PLAYHEAD_WIDTH / 2), groove_rect.top() - _PLAYHEAD_OVERHANG,
            _PLAYHEAD_WIDTH, groove_rect.height() + 2 * _PLAYHEAD_OVERHANG, _PLAYHEAD_COLOR)

        painter.end()
