"""
grading_primary.py -- Qt-free backend for OCIO's GradingPrimaryTransform:
pivot-based lift/gamma/gain-shaped primary grading, OCIO's own real-time-
editing-oriented grading tool -- see
docs/archive/OCIO_TIER1_GRADING_OPERATORS_PLAN_2026-08-09.md for why this was added:
cdl.py's role in this app is standard/interchange ASC CDL support
specifically, not the primary grading tool.

Three GradingStyle variants exist (see AVAILABLE_STYLES), exposed as a
stage param (style dropdown) -- same pattern as cdl.py's own "asc"/
"no_clip" choice, since which one fits depends on WHERE in the stack a
user places this stage, not something this app should decide for them:

  - "log" (GradingStyle.GRADING_LOG) -- brightness/contrast/gamma, tuned
    for a LOG-encoded working space (e.g. ACEScct). DEFAULT here, since
    printer_lights.py's own default working_colourspace is ACEScct and
    this app's stacks generally grade in a log-encoded space before any
    display transform.
  - "lin" (GradingStyle.GRADING_LIN) -- offset/exposure/contrast, tuned
    for scene-linear values.
  - "video" (GradingStyle.GRADING_VIDEO) -- lift/gamma/gain, the classic
    three-wheel color-wheel grade, tuned for display-referred/video
    signal.

All fields (brightness/contrast/gamma/offset/exposure/lift/gain/pivot/
saturation) are accepted regardless of style -- OCIO's own generated
shader/CPU processor simply never reads the fields a given style doesn't
use (confirmed directly, see the plan doc's GLSL dump for GRADING_LOG:
only brightness/contrast/gamma/pivot/saturation/clamp appear), so storing
every field in Stage.params uniformly (rather than a style-shaped subset)
is harmless and keeps switching styles non-destructive to whatever was
already dialed in on the other fields.

Uses OCIO's own GradingPrimaryTransform/GradingPrimary/GradingRGBM
(already a dependency via cdl.py/aces_drt.py) rather than hand-rolling
the lift/gamma/gain math -- same "trust the vetted reference
implementation" rule as cdl.py. Reuses cdl._get_raw_config() (a bare
config with no colorspaces/displays registered) since this transform,
like CDLTransform, never references a named colorspace.

Same rule as cdl.py/aces_drt.py/printer_lights.py: no PySide6 import
here, fully testable without a display.
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
import ocio_processor_cache

IDENTITY_ADD = (0.0, 0.0, 0.0, 0.0)  # brightness/offset/lift additive identity (r, g, b, master)
IDENTITY_MULT = (1.0, 1.0, 1.0, 1.0)  # contrast/gamma/gain multiplicative identity (r, g, b, master)
DEFAULT_PIVOT = -0.2
DEFAULT_SATURATION = 1.0

DEFAULT_STYLE = "log"

# (style key, human label) -- key is what's stored in Stage.params/session
# files (stable across OCIO versions), not the OCIO enum name directly.
# "log" listed first to match DEFAULT_STYLE above.
AVAILABLE_STYLES = [
    ("log", "Log (brightness/contrast/gamma -- log-encoded working space)"),
    ("lin", "Linear (offset/exposure/contrast -- scene-linear)"),
    ("video", "Video (lift/gamma/gain -- classic 3-wheel grade)"),
]


def _require_ocio():
    if not OCIO_AVAILABLE:
        raise RuntimeError(
            "PyOpenColorIO isn't installed, so Grading Primary features are "
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
    raise ValueError(f"Unknown Grading Primary style: {style!r} (expected 'log', 'lin', or 'video')")


def _rgbm(values):
    r, g, b, m = values
    return ocio.GradingRGBM(r, g, b, m)


# OCIO's own ocio.GradingPrimary.validate() requires contrast and gamma
# to stay strictly positive (confirmed via binary-search probing, same
# technique/reasoning as grading_tone.GAIN_BOUNDS -- exceeding this
# raises a real ocio.Exception from inside setGradingPrimary, the exact
# failure mode a live grading_tone slider drag actually hit in practice).
# brightness/offset/exposure/lift/gain/pivot/saturation have no such
# floor (probed effectively unbounded) -- only contrast/gamma need this.
_POSITIVE_FIELD_FLOOR = 0.01


def _clamp_positive(values):
    r, g, b, m = values
    clamp = lambda v: max(v, _POSITIVE_FIELD_FLOOR)
    return (clamp(r), clamp(g), clamp(b), clamp(m))


def build_grading_primary_value(style=DEFAULT_STYLE, brightness=IDENTITY_ADD, contrast=IDENTITY_MULT,
                                 gamma=IDENTITY_MULT, offset=IDENTITY_ADD, exposure=IDENTITY_ADD,
                                 lift=IDENTITY_ADD, gain=IDENTITY_MULT, pivot=DEFAULT_PIVOT,
                                 saturation=DEFAULT_SATURATION):
    """An ocio.GradingPrimary struct for the given values -- see module
    docstring on why every field is accepted regardless of style.
    contrast/gamma are clamped to stay strictly positive (see
    _POSITIVE_FIELD_FLOOR's own comment) -- every other field is passed
    through as given."""
    _require_ocio()
    value = ocio.GradingPrimary(_style_to_ocio(style))
    value.brightness = _rgbm(brightness)
    value.contrast = _rgbm(_clamp_positive(contrast))
    value.gamma = _rgbm(_clamp_positive(gamma))
    value.offset = _rgbm(offset)
    value.exposure = _rgbm(exposure)
    value.lift = _rgbm(lift)
    value.gain = _rgbm(gain)
    value.pivot = float(pivot)
    value.saturation = float(saturation)
    return value


def build_grading_primary_transform(style=DEFAULT_STYLE, **kwargs):
    """An ocio.GradingPrimaryTransform for the given style/values."""
    _require_ocio()
    value = build_grading_primary_value(style=style, **kwargs)
    transform = ocio.GradingPrimaryTransform(_style_to_ocio(style))
    transform.setValue(value)
    return transform


def _apply_ocio_transform(cache_key, build_transform, image_array):
    """Same flatten/PackedImageDesc/reshape dance as
    cdl._apply_ocio_transform -- duplicated locally rather than imported,
    matching this app's existing "independently self-contained OCIO
    consumer" convention (see cdl.py's own docstring). `build_transform`/
    `cache_key`: see cdl._apply_ocio_transform's own docstring."""
    _require_ocio()
    cfg = cdl._get_raw_config()
    cpu = ocio_processor_cache.get_cpu_processor(cfg, cache_key, build_transform)

    array = np.ascontiguousarray(np.asarray(image_array, dtype=np.float32))
    original_shape = array.shape
    flat = array.reshape(-1, 3).copy()
    n_pixels = flat.shape[0]
    desc = ocio.PackedImageDesc(flat, n_pixels, 1, 3)
    cpu.apply(desc)
    return flat.reshape(original_shape)


def apply_grading_primary(image_array, style=DEFAULT_STYLE, brightness=IDENTITY_ADD,
                           contrast=IDENTITY_MULT, gamma=IDENTITY_MULT, offset=IDENTITY_ADD,
                           exposure=IDENTITY_ADD, lift=IDENTITY_ADD, gain=IDENTITY_MULT,
                           pivot=DEFAULT_PIVOT, saturation=DEFAULT_SATURATION):
    """Apply an OCIO GradingPrimary grade to an (H,W,3) float array (or any
    (...,3)-shaped array, e.g. a LUT identity grid -- same "one function,
    used both for direct-pixel preview and for baking onto a grid" pattern
    as apply_cdl/apply_aces_output_transform.

    Identity kwargs (all defaults) are an exact no-op regardless of style
    -- unlike CDL's "asc" style, GradingPrimary has no intermediate clamp
    that applies unconditionally (clampBlack/clampWhite default to
    +/-inf, i.e. no clamp at all, confirmed directly)."""
    cache_key = (style, tuple(brightness), tuple(contrast), tuple(gamma), tuple(offset),
                 tuple(exposure), tuple(lift), tuple(gain), float(pivot), float(saturation))

    def build_transform():
        return build_grading_primary_transform(
            style=style, brightness=brightness, contrast=contrast, gamma=gamma, offset=offset,
            exposure=exposure, lift=lift, gain=gain, pivot=pivot, saturation=saturation,
        )

    return _apply_ocio_transform(cache_key, build_transform, image_array)
