"""
exposure_contrast.py -- Qt-free backend for OCIO's ExposureContrastTransform:
exposure/contrast/gamma, OCIO's own real-time-editing-oriented exposure
tool -- see docs/archive/OCIO_TIER1_GRADING_OPERATORS_PLAN_2026-08-09.md, Stage 3.

Three ExposureContrastStyle variants exist (see AVAILABLE_STYLES), exposed
as a stage param (style dropdown), same pattern as grading_primary.py's
own style choice:

  - "linear" (EXPOSURE_CONTRAST_LINEAR) -- contrast pivots around a
    scene-linear mid-gray reference. DEFAULT here.
  - "log" (EXPOSURE_CONTRAST_LOGARITHMIC) -- contrast pivots around a
    log-encoded mid-gray reference (tuned by logExposureStep/logMidGray,
    see below).
  - "video" (EXPOSURE_CONTRAST_VIDEO) -- contrast pivots around a
    display-referred reference.

PIVOT/LOGEXPOSURESTEP/LOGMIDGRAY ARE DELIBERATELY NOT EXPOSED -- unlike
exposure/contrast/gamma (each independently `makeXDynamic()`-able, see
gpu_stack_renderer.py's own use of gpu_ocio_dynamic.py), these three are
NOT dynamic properties in OCIO's API (confirmed directly: only
DYNAMIC_PROPERTY_EXPOSURE/_CONTRAST/_GAMMA exist for this transform).
That means OCIO bakes them as literal GLSL CONSTANTS even in a
makeXDynamic()'d shader (e.g. `0.18` hardcoded into the pow() call) --
so unlike exposure/contrast/gamma, changing one of these three would
require a full shader recompile, not a cheap uniform push. Exposing them
as live sliders would silently reintroduce exactly the per-edit-tick
recompile cost this whole project has been eliminating. OCIO's own
defaults (pivot=0.18, the standard 18%-gray reference) are used
unconditionally -- a real, deliberate scope decision, not an oversight.

Uses OCIO's own ExposureContrastTransform (already a dependency via
cdl.py/aces_drt.py) rather than hand-rolling the exposure/contrast/gamma
math -- same "trust the vetted reference implementation" rule as
cdl.py/grading_primary.py. Reuses cdl._get_raw_config() (a bare config
with no colorspaces/displays registered) since this transform, like
CDLTransform/GradingPrimaryTransform, never references a named
colorspace.

Same rule as cdl.py/grading_primary.py: no PySide6 import here, fully
testable without a display.
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

IDENTITY_EXPOSURE = 0.0
IDENTITY_CONTRAST = 1.0
IDENTITY_GAMMA = 1.0

DEFAULT_STYLE = "linear"

# (style key, human label) -- key is what's stored in Stage.params/session
# files (stable across OCIO versions), not the OCIO enum name directly.
# "linear" listed first to match DEFAULT_STYLE above.
AVAILABLE_STYLES = [
    ("linear", "Linear (scene-referred mid-gray pivot)"),
    ("log", "Logarithmic (log-encoded mid-gray pivot)"),
    ("video", "Video (display-referred pivot)"),
]


def _require_ocio():
    if not OCIO_AVAILABLE:
        raise RuntimeError(
            "PyOpenColorIO isn't installed, so Exposure/Contrast features are "
            f"unavailable (import error: {OCIO_IMPORT_ERROR}). "
            "Install it with: pip3 install opencolorio"
        )


def list_available_styles():
    """[(style_key, human_label), ...] for the Style combo."""
    return list(AVAILABLE_STYLES)


def _style_to_ocio(style):
    _require_ocio()
    if style == "linear":
        return ocio.EXPOSURE_CONTRAST_LINEAR
    if style == "log":
        return ocio.EXPOSURE_CONTRAST_LOGARITHMIC
    if style == "video":
        return ocio.EXPOSURE_CONTRAST_VIDEO
    raise ValueError(f"Unknown Exposure/Contrast style: {style!r} (expected 'linear', 'log', or 'video')")


def build_exposure_contrast_transform(style=DEFAULT_STYLE, exposure=IDENTITY_EXPOSURE,
                                       contrast=IDENTITY_CONTRAST, gamma=IDENTITY_GAMMA):
    """An ocio.ExposureContrastTransform for the given style/values --
    pivot/logExposureStep/logMidGray are left at OCIO's own defaults, see
    module docstring on why those three are never user-facing here."""
    _require_ocio()
    transform = ocio.ExposureContrastTransform()
    transform.setStyle(_style_to_ocio(style))
    transform.setExposure(float(exposure))
    transform.setContrast(float(contrast))
    transform.setGamma(float(gamma))
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


def apply_exposure_contrast(image_array, style=DEFAULT_STYLE, exposure=IDENTITY_EXPOSURE,
                             contrast=IDENTITY_CONTRAST, gamma=IDENTITY_GAMMA):
    """Apply an OCIO Exposure/Contrast grade to an (H,W,3) float array (or
    any (...,3)-shaped array, e.g. a LUT identity grid) -- same "one
    function, used both for direct-pixel preview and for baking onto a
    grid" pattern as apply_cdl/apply_grading_primary.

    Identity kwargs (exposure=0, contrast=1, gamma=1 -- all defaults) are
    an exact no-op regardless of style: exposure=0 -> pow(2,0)=1x
    multiply, contrast=1 and gamma=1 -> contrast*gamma=1.0 -> the
    contrast branch is skipped entirely (confirmed directly against
    OCIO's own generated shader, see the plan doc's GLSL dump)."""
    cache_key = (style, float(exposure), float(contrast), float(gamma))

    def build_transform():
        return build_exposure_contrast_transform(
            style=style, exposure=exposure, contrast=contrast, gamma=gamma)

    return _apply_ocio_transform(cache_key, build_transform, image_array)
