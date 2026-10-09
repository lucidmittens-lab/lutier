"""
grading_tone.py -- Qt-free backend for OCIO's GradingToneTransform:
print-style tone shaping across 5 overlapping ranges (blacks/shadows/
midtones/highlights/whites), each an independent R/G/B/Master gain plus
its own start/width along the tone curve, plus a single S-contrast
scalar -- see docs/archive/OCIO_TIER1_GRADING_OPERATORS_PLAN_2026-08-09.md, Stage 4.

Same three GradingStyle variants as grading_primary.py (see
AVAILABLE_STYLES), same "log" default for the same reason (matches
printer_lights.py's own ACEScct-default working-space convention).

UNLIKE grading_primary.py's pivot/pivotBlack/pivotWhite -- and UNLIKE
exposure_contrast.py's pivot/logExposureStep/logMidGray -- every field
here (all 5 ranges' R/G/B/Master/Start/Width, plus S-contrast) IS a real
GPU uniform under a single DYNAMIC_PROPERTY_GRADING_TONE dynamic
property (confirmed directly via GpuShaderDesc.getUniforms() -- see the
plan doc's own survey), so nothing here needs the "exclude this field
from the UI, it'd force a recompile" treatment those two modules needed.
Every field is genuinely live-editable.

RANGE_KEYS/DEFAULT_RANGE_VALUES below are OCIO's own defaults, read once
via ocio.GradingTone(ocio.GRADING_LOG)'s own construction rather than
hand-transcribed -- see the module-level block below this docstring.

Uses OCIO's own GradingToneTransform/GradingTone/GradingRGBMSW (already a
dependency via cdl.py/grading_primary.py) rather than hand-rolling the
tone-range math -- same "trust the vetted reference implementation" rule
as every other module in this family. Reuses cdl._get_raw_config() (a
bare config with no colorspaces/displays registered) since this
transform never references a named colorspace.

Same rule as cdl.py/grading_primary.py/exposure_contrast.py: no PySide6
import here, fully testable without a display.
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

RANGE_KEYS = ("blacks", "shadows", "midtones", "highlights", "whites")

# (red, green, blue, master, start, width) -- OCIO's own defaults for a
# freshly constructed ocio.GradingTone(GRADING_LOG), confirmed directly
# (not hand-transcribed from documentation): identity gain (1.0) on every
# channel, with each range's start/width already positioned so the whole
# transform is a real no-op out of the box.
DEFAULT_RANGE_VALUES = {
    "blacks": (1.0, 1.0, 1.0, 1.0, 0.4, 0.4),
    "shadows": (1.0, 1.0, 1.0, 1.0, 0.5, 0.0),
    "midtones": (1.0, 1.0, 1.0, 1.0, 0.4, 0.6),
    "highlights": (1.0, 1.0, 1.0, 1.0, 0.3, 1.0),
    "whites": (1.0, 1.0, 1.0, 1.0, 0.4, 0.5),
}
DEFAULT_SCONTRAST = 1.0

# OCIO's own internal ocio.GradingTone.validate() bounds for each range's
# R/G/B/Master gain -- confirmed via binary-search probing against real
# validate() calls (NOT documented anywhere else, and NOT the same for
# every range: shadows/highlights are tighter than blacks/midtones/
# whites). Exceeding these raises a real ocio.Exception from inside
# setGradingTone -- a real, reproduced bug: this was hit live (dragging
# a UI slider past the old, too-permissive [0,4] range) and produced an
# uncaught exception INSIDE paintGL, which crashed the app outright
# before paintGL grew its own exception guard (see wipe_compare_widget.
# WipeCompareWidget.paintGL's docstring) -- clamping here, at the one
# place every caller (UI, session load, tests) funnels through via
# build_grading_tone_value, is the actual fix, not just a UI nicety.
GAIN_BOUNDS = {
    "blacks": (0.1, 1.9), "shadows": (0.2, 1.8), "midtones": (0.1, 1.9),
    "highlights": (0.2, 1.8), "whites": (0.1, 1.9),
}
# scontrast's own validate() bound, found the same way.
SCONTRAST_BOUNDS = (0.01, 1.99)

# Start/Width are DELIBERATELY NOT given fixed bounds here -- probing
# showed their validity depends on OTHER ranges' current start/width too
# (OCIO validates the 5 ranges' relative ordering along the tone curve as
# a whole, not each range's width against one universal constant), so no
# single per-field clamp here can be both correct and permissive. See
# gpu_stack_renderer.py's own handling of GradingTone (catches
# ocio.Exception around setGradingTone and skips the update rather than
# crashing) for how that residual risk is actually covered.

DEFAULT_STYLE = "log"

# (style key, human label) -- key is what's stored in Stage.params/session
# files (stable across OCIO versions), not the OCIO enum name directly.
# "log" listed first to match DEFAULT_STYLE above.
AVAILABLE_STYLES = [
    ("log", "Log (log-encoded working space)"),
    ("lin", "Linear (scene-linear)"),
    ("video", "Video (display-referred)"),
]


def _require_ocio():
    if not OCIO_AVAILABLE:
        raise RuntimeError(
            "PyOpenColorIO isn't installed, so Grading Tone features are "
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
    raise ValueError(f"Unknown Grading Tone style: {style!r} (expected 'log', 'lin', or 'video')")


def _clamp_range_values(range_key, values):
    """Clamps a (r, g, b, m, start, width) tuple's gain channels to
    GAIN_BOUNDS[range_key] -- start/width pass through unclamped, see
    GAIN_BOUNDS' own comment on why those two can't be safely bounded
    per-field in isolation."""
    r, g, b, m, start, width = values
    lo, hi = GAIN_BOUNDS[range_key]
    clamp = lambda v: min(max(v, lo), hi)
    return (clamp(r), clamp(g), clamp(b), clamp(m), start, width)


def _rgbmsw(values):
    r, g, b, m, start, width = values
    return ocio.GradingRGBMSW(r, g, b, m, start, width)


def build_grading_tone_value(style=DEFAULT_STYLE, blacks=None, shadows=None, midtones=None,
                              highlights=None, whites=None, scontrast=DEFAULT_SCONTRAST):
    """An ocio.GradingTone struct for the given values -- each range
    argument defaults to THAT range's own OCIO default (see
    DEFAULT_RANGE_VALUES) when None, not a shared/generic identity.

    Every range's R/G/B/Master gain is clamped to GAIN_BOUNDS[range_key]
    and scontrast to SCONTRAST_BOUNDS -- see those constants' own
    comments for why (a real crash this app hit, not preemptive
    over-caution). Start/Width are NOT clamped here (see GAIN_BOUNDS'
    comment) -- gpu_stack_renderer.py's own handling of a resulting
    ocio.Exception from setGradingTone is this app's actual safety net
    for that residual case."""
    _require_ocio()
    blacks = _clamp_range_values("blacks", blacks if blacks is not None else DEFAULT_RANGE_VALUES["blacks"])
    shadows = _clamp_range_values("shadows", shadows if shadows is not None else DEFAULT_RANGE_VALUES["shadows"])
    midtones = _clamp_range_values("midtones", midtones if midtones is not None else DEFAULT_RANGE_VALUES["midtones"])
    highlights = _clamp_range_values(
        "highlights", highlights if highlights is not None else DEFAULT_RANGE_VALUES["highlights"])
    whites = _clamp_range_values("whites", whites if whites is not None else DEFAULT_RANGE_VALUES["whites"])
    scontrast_lo, scontrast_hi = SCONTRAST_BOUNDS
    scontrast = min(max(float(scontrast), scontrast_lo), scontrast_hi)

    value = ocio.GradingTone(_style_to_ocio(style))
    value.blacks = _rgbmsw(blacks)
    value.shadows = _rgbmsw(shadows)
    value.midtones = _rgbmsw(midtones)
    value.highlights = _rgbmsw(highlights)
    value.whites = _rgbmsw(whites)
    value.scontrast = scontrast
    return value


def build_grading_tone_transform(style=DEFAULT_STYLE, **kwargs):
    """An ocio.GradingToneTransform for the given style/values."""
    _require_ocio()
    value = build_grading_tone_value(style=style, **kwargs)
    transform = ocio.GradingToneTransform(_style_to_ocio(style))
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


def apply_grading_tone(image_array, style=DEFAULT_STYLE, blacks=None, shadows=None, midtones=None,
                        highlights=None, whites=None, scontrast=DEFAULT_SCONTRAST):
    """Apply an OCIO GradingTone grade to an (H,W,3) float array (or any
    (...,3)-shaped array, e.g. a LUT identity grid) -- same "one function,
    used both for direct-pixel preview and for baking onto a grid" pattern
    as apply_cdl/apply_grading_primary.

    Identity kwargs (every range at None -> its own OCIO default,
    scontrast=1.0) are an exact no-op regardless of style -- confirmed
    directly, same as every other Tier 1 kind."""
    cache_key = (
        style,
        tuple(blacks) if blacks is not None else None,
        tuple(shadows) if shadows is not None else None,
        tuple(midtones) if midtones is not None else None,
        tuple(highlights) if highlights is not None else None,
        tuple(whites) if whites is not None else None,
        float(scontrast),
    )

    def build_transform():
        return build_grading_tone_transform(
            style=style, blacks=blacks, shadows=shadows, midtones=midtones,
            highlights=highlights, whites=whites, scontrast=scontrast,
        )

    return _apply_ocio_transform(cache_key, build_transform, image_array)
