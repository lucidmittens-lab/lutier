"""
video_scrub_slider.py -- VideoScrubSlider: a plain QSlider subclass that
additionally paints IN/OUT point markers over its own groove (Phase 4
transport upgrade follow-up, per Tom: "hard to tell where the marks are
based on the current progress bar"). Qt-only (no way around that for a
custom paintEvent), but deliberately kept to exactly one job -- drawing
marks over positions it's told about -- so sample_image_panel.py stays
the only place that decides WHAT those positions mean (in_point/
out_point on VideoPlaybackController); this widget has no idea what a
video or a playback controller even is, and does nothing at all if
set_in_out is never called (a plain QSlider, no visual change).
"""
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider

# Semi-transparent highlight fill for the active [in_point, out_point]
# range, plus a more opaque marker line at each bound -- plain constants
# rather than reaching into style.py's accent color machinery, since
# these need to read clearly against the slider's own groove regardless
# of whatever accent color happens to be chosen.
_RANGE_FILL = QColor(255, 200, 60, 70)
_MARK_COLOR = QColor(255, 200, 60, 220)
_MARK_WIDTH = 2


class VideoScrubSlider(QSlider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._in_point = None
        self._out_point = None

    def set_in_out(self, in_point, out_point):
        """`in_point`/`out_point` are frame indices (matching this
        slider's own minimum()/maximum() range) -- `out_point=None` is
        drawn at this slider's own maximum() (the clip's last frame),
        matching VideoPlaybackController's own "None means the end of
        the clip" convention (see that class's docstring). Passing
        (None, None) clears the markers back to a plain slider."""
        self._in_point = in_point
        self._out_point = out_point
        self.update()

    def _groove_x_for_value(self, value, groove_rect, opt):
        span = max(0, groove_rect.width() - 1)
        pos = QStyle.sliderPositionFromValue(
            self.minimum(), self.maximum(), value, span, opt.upsideDown)
        return groove_rect.x() + pos

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._in_point is None and self._out_point is None:
            return
        if self.maximum() <= self.minimum():
            return

        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        groove_rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderGroove, self)
        if groove_rect.width() <= 0:
            return

        in_point = self._in_point if self._in_point is not None else self.minimum()
        out_point = self._out_point if self._out_point is not None else self.maximum()
        in_x = self._groove_x_for_value(in_point, groove_rect, opt)
        out_x = self._groove_x_for_value(out_point, groove_rect, opt)

        painter = QPainter(self)
        fill_rect = groove_rect
        fill_rect.setLeft(min(in_x, out_x))
        fill_rect.setRight(max(in_x, out_x))
        painter.fillRect(fill_rect, _RANGE_FILL)
        for x in (in_x, out_x):
            painter.fillRect(int(x - _MARK_WIDTH / 2), groove_rect.top(),
                              _MARK_WIDTH, groove_rect.height(), _MARK_COLOR)
        painter.end()
