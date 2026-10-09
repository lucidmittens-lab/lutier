"""
hidpi.py -- Qt-free helpers for fractional device pixel ratios.

With QT_SCALE_FACTOR set (or fractional OS scaling), the device pixel
ratio is often fractional (0.75, 1.25, 2 x 0.75 = 1.5).
Qt sizes a QOpenGLWidget's framebuffer as qRound(logical * dpr) per
dimension -- the PRODUCT rounded half away from zero, never the ratio
rounded -- so every GL-side size in this app must agree with that exactly,
or the viewer draws into a framebuffer one pixel off from the one it has
(a 1px strip, a squashed projection, or a half-pixel shift that softens
image edges).

Note Python's own round() rounds halves to EVEN (round(1200.5) == 1200),
unlike qRound (1201): qt_round below is the one to use.
"""
import math


def qt_round(value):
    """Qt's qRound: nearest integer, halves away from zero."""
    if value >= 0:
        return int(math.floor(value + 0.5))
    return -int(math.floor(-value + 0.5))


def physical_size(logical_w, logical_h, dpr):
    """The framebuffer size Qt gives a widget of logical_w x logical_h at
    device pixel ratio `dpr` (float, used as-is)."""
    return max(1, qt_round(logical_w * dpr)), max(1, qt_round(logical_h * dpr))


def snap_rect(left, top, width, height):
    """(left, top, width, height) moved to whole physical pixels -- the
    position rounded, the SIZE rounded once on its own -- so both edges
    land on pixel boundaries (no half-blended border) and a whole-pixel
    size is kept exactly (1:1 stays 1:1). Rounding the two edges
    separately instead made a 1920px image 1921px whenever both edges sat
    on .5 (found on the real display at QT_SCALE_FACTOR=0.75, 2026-10-09)."""
    return qt_round(left), qt_round(top), qt_round(width), qt_round(height)
