"""
grading_rgb_curve.py -- Qt-free backend for OCIO's GradingRGBCurveTransform:
four independent B-spline curves (red/green/blue/master) mapping input
value -> output value per channel, OCIO's own real-time-editing-oriented
curve-grading tool -- see OCIO_TIER1_GRADING_OPERATORS_PLAN_2026-08-09.md,
Stage 5.

Same three GradingStyle variants as grading_primary.py/grading_tone.py
(see AVAILABLE_STYLES), same "log" default for the same reason (matches
printer_lights.py's own ACEScct-default working-space convention).

EVERY FIELD HERE IS A REAL GPU UNIFORM under one single
DYNAMIC_PROPERTY_GRADING_RGBCURVE dynamic property (confirmed directly,
same validation this whole Tier 1 family got -- see
gpu_ocio_dynamic.py's own test_gpu_ocio_dynamic.py, which already
covers this kind bit-exactly, 0.0 max|delta| against
getDefaultCPUProcessor() -- the array/int-vector uniform types
(knots/coefs/knotsOffsets/coefsOffsets) this kind uses were exactly why
gpu_ocio_dynamic.push_dynamic_uniforms was built generically over EVERY
ocio.UniformDataType from the start, not just the scalar ones the other
three kinds needed).

CONTROL POINTS -- each channel is a flat, ascending-by-x sequence of
(x, y) pairs, minimum 2 points (OCIO's own real floor, confirmed by
probing: "GradingBSpline needs at least 4 values" for a 1-point/2-value
curve). Built via ocio.GradingBSplineCurve's flat-sequence constructor
overload, NOT by mutating an existing curve's getControlPoints() -- a
real, confirmed API gotcha (see grading_tone.py's own docstring, same
underlying OCIO curve type): mutating those returned items in place does
NOT persist; the whole curve must be rebuilt and reassigned.

Uses OCIO's own GradingRGBCurveTransform/GradingRGBCurve/
GradingBSplineCurve (already a dependency via cdl.py/grading_primary.py)
rather than hand-rolling spline math -- same "trust the vetted reference
implementation" rule as every other module in this family. Reuses
cdl._get_raw_config() (a bare config with no colorspaces/displays
registered) since this transform never references a named colorspace.

Same rule as cdl.py/grading_primary.py/grading_tone.py/
exposure_contrast.py: no PySide6 import here, fully testable without a
display.
"""
import numpy as np

try:
    import PyOpenColorIO as ocio
    OCIO_AVAILABLE = True
    OCIO_IMPORT_ERROR = None
except ImportError as e:  # pragma: no cover -- exercised by environments without opencolorio installed
    ocio = None
    OCIO_AVAILABLE = False
    OCIO_IMPORT_ERROR = str(e)

import cdl

RGB_CHANNELS = ("red", "green", "blue", "master")

# OCIO's own default control points for a freshly constructed
# GradingRGBCurve -- an exact identity curve out of the box (confirmed
# directly, not hand-transcribed): three points, straight line.
DEFAULT_CONTROL_POINTS = ((0.0, 0.0), (0.5, 0.5), (1.0, 1.0))

DEFAULT_STYLE = "log"

# (style key, human label) -- key is what's stored in Stage.params/session
# files (stable across OCIO versions), not the OCIO enum name directly.
# "log" listed first to match DEFAULT_STYLE above.
AVAILABLE_STYLES = [
    ("log", "Log (log-encoded working space)"),
    ("lin", "Linear (scene-linear)"),
    ("video", "Video (display-referred)"),
]

MIN_CONTROL_POINTS = 2


def _require_ocio():
    if not OCIO_AVAILABLE:
        raise RuntimeError(
            "PyOpenColorIO isn't installed, so Grading RGB Curve features are "
            f"unavailable (import error: {OCIO_IMPORT_ERROR}). "
            "Install it with: pip3 install opencolorio"
        )


def list_available_styles():
    """[(style_key, human_label), ...] for the Style combo."""
    return list(AVAILABLE_STYLES)


def _style_to_ocio(style):
    _require_ocio()
    if style == "log":
        return ocio.GRADING_LOG
    if style == "lin":
        return ocio.GRADING_LIN
    if style == "video":
        return ocio.GRADING_VIDEO
    raise ValueError(f"Unknown Grading RGB Curve style: {style!r} (expected 'log', 'lin', or 'video')")


def _normalize_control_points(points):
    """A real, ascending-by-x, at-least-MIN_CONTROL_POINTS-long point
    list -- sorted (never reordered incorrectly by a UI that lets a user
    drag one point past another), padded up to the minimum with the
    identity curve's own points if too few were given. Equal-x points
    are left as-is -- confirmed directly that OCIO tolerates them (no
    "ascending" gotcha the way grading_tone.py's gain bounds had)."""
    points = sorted((float(x), float(y)) for x, y in points)
    if len(points) < MIN_CONTROL_POINTS:
        points = list(DEFAULT_CONTROL_POINTS)
    return points


def _build_bspline_curve(points):
    points = _normalize_control_points(points)
    flat = [v for point in points for v in point]
    return ocio.GradingBSplineCurve(flat)


def build_grading_rgb_curve_value(style=DEFAULT_STYLE, red=None, green=None, blue=None, master=None):
    """An ocio.GradingRGBCurve struct for the given values -- each
    channel argument defaults to the identity curve (DEFAULT_CONTROL_
    POINTS) when None, not a shared/generic no-op computed some other
    way."""
    _require_ocio()
    value = ocio.GradingRGBCurve(_style_to_ocio(style))
    value.red = _build_bspline_curve(red if red is not None else DEFAULT_CONTROL_POINTS)
    value.green = _build_bspline_curve(green if green is not None else DEFAULT_CONTROL_POINTS)
    value.blue = _build_bspline_curve(blue if blue is not None else DEFAULT_CONTROL_POINTS)
    value.master = _build_bspline_curve(master if master is not None else DEFAULT_CONTROL_POINTS)
    return value


def build_grading_rgb_curve_transform(style=DEFAULT_STYLE, **kwargs):
    """An ocio.GradingRGBCurveTransform for the given style/values."""
    _require_ocio()
    value = build_grading_rgb_curve_value(style=style, **kwargs)
    transform = ocio.GradingRGBCurveTransform(_style_to_ocio(style))
    transform.setValue(value)
    return transform


def _apply_ocio_transform(transform, image_array):
    """Same flatten/PackedImageDesc/reshape dance as
    cdl._apply_ocio_transform -- duplicated locally rather than imported,
    matching this app's existing "independently self-contained OCIO
    consumer" convention (see cdl.py's own docstring)."""
    _require_ocio()
    cfg = cdl._get_raw_config()
    processor = cfg.getProcessor(transform)
    cpu = processor.getDefaultCPUProcessor()

    array = np.ascontiguousarray(np.asarray(image_array, dtype=np.float32))
    original_shape = array.shape
    flat = array.reshape(-1, 3).copy()
    n_pixels = flat.shape[0]
    desc = ocio.PackedImageDesc(flat, n_pixels, 1, 3)
    cpu.apply(desc)
    return flat.reshape(original_shape)


def apply_grading_rgb_curve(image_array, style=DEFAULT_STYLE, red=None, green=None, blue=None, master=None):
    """Apply an OCIO GradingRGBCurve grade to an (H,W,3) float array (or
    any (...,3)-shaped array, e.g. a LUT identity grid) -- same "one
    function, used both for direct-pixel preview and for baking onto a
    grid" pattern as apply_cdl/apply_grading_primary.

    Identity kwargs (every channel at None -> the identity curve) are an
    exact no-op regardless of style -- confirmed directly, same as every
    other Tier 1 kind."""
    transform = build_grading_rgb_curve_transform(style=style, red=red, green=green, blue=blue, master=master)
    return _apply_ocio_transform(transform, image_array)
