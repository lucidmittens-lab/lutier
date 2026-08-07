"""
cdl.py -- Qt-free backend for ASC CDL (Color Decision List) grading: SOP
(Slope/Offset/Power, per-channel) and SAT (Saturation), the same two-part
primary-grade formula colorists exchange as .cc/.cdl/.ccc files -- every
grading/color tool (Baselight, Resolve, Flame, Nuke...) implements this
identically because it's a written ASC standard, not a house convention.
(.cc/.cdl/.ccc import/export and a CDL library panel are a deliberate
follow-up, not in this pass -- see stack_panel.py's docstring.)

Uses OpenColorIO's own CDLTransform (already a dependency, via aces_drt.py)
rather than hand-rolling the SOP/SAT math -- same "trust the vetted
reference implementation" rule aces_drt.py follows for the RRT/ODT chain,
and it directly exposes the spec's two interchange styles:

  - CDLStyle.CDL_ASC -- clamps to [0,1] between the SOP and SAT stages.
    This is the ASC spec's own default and what every other CDL-reading
    tool assumes unless told otherwise, so it's what makes a stage here
    round-trip identically through a real .cc file read by a different
    application later. Opt in via style="asc" for a stage meant for that
    kind of interchange.
  - CDLStyle.CDL_NO_CLAMP -- skips that intermediate clamp, letting values
    run outside [0,1] the way this app's other stages (aces_drt.py,
    printer_lights.py) always do. Default here (style="no_clip" below):
    the intermediate clamp applies regardless of SOP/SAT parameter
    values, so an "asc"-style stage with identity params is NOT a true
    no-op once upstream values already exceed [0,1] (scene-linear/HDR
    signal, an ACES DRT output, another no_clip stage upstream, ...) --
    it silently discards that range with zero indication to the user.
    "no_clip" is the only style where a freshly added, untouched CDL
    stage is guaranteed to be a real no-op, matching every other stage
    kind's behavior in this app.

Independent SOP/SAT bypass toggles (sop_enabled/sat_enabled below) mirror
the two independent bypass switches every real CDL-aware tool exposes --
"check the shot on SOP alone" (or vice versa) is a normal grading-review
move, not a hypothetical. Implemented by neutralizing that half's
parameters (identity slope/offset/power, or sat=1.0) rather than building
two separate transforms, since a single CDLTransform already applies SOP
then SAT in the correct order -- neutralizing one half still composes
correctly with the other.

Same rule as aces_drt.py/printer_lights.py: no PySide6 import here, fully
testable without a display.
"""
from functools import lru_cache

import numpy as np

try:
    import PyOpenColorIO as ocio
    OCIO_AVAILABLE = True
    OCIO_IMPORT_ERROR = None
except ImportError as e:  # pragma: no cover -- exercised by environments without opencolorio installed
    ocio = None
    OCIO_AVAILABLE = False
    OCIO_IMPORT_ERROR = str(e)

IDENTITY_SLOPE = (1.0, 1.0, 1.0)
IDENTITY_OFFSET = (0.0, 0.0, 0.0)
IDENTITY_POWER = (1.0, 1.0, 1.0)
IDENTITY_SAT = 1.0

DEFAULT_STYLE = "no_clip"

# (style key, human label) -- key is what's stored in Stage.params/session
# files (stable across OCIO versions), not the OCIO enum name directly.
# "no_clip" listed first to match DEFAULT_STYLE above.
AVAILABLE_STYLES = [
    ("no_clip", "No Clip (extended range, matches this app's other stages)"),
    ("asc", "ASC (clamped -- spec default, matches other CDL tools)"),
]


def _require_ocio():
    if not OCIO_AVAILABLE:
        raise RuntimeError(
            "PyOpenColorIO isn't installed, so ASC CDL features are "
            f"unavailable (import error: {OCIO_IMPORT_ERROR}). "
            "Install it with: pip3 install opencolorio"
        )


def list_available_styles():
    """[(style_key, human_label), ...] for the Style combo."""
    return list(AVAILABLE_STYLES)


def _style_to_ocio(style):
    _require_ocio()
    if style == "asc":
        return ocio.CDLStyle.CDL_ASC
    if style == "no_clip":
        return ocio.CDLStyle.CDL_NO_CLAMP
    raise ValueError(f"Unknown CDL style: {style!r} (expected 'asc' or 'no_clip')")


@lru_cache(maxsize=1)
def _get_raw_config():
    """A bare ocio.Config with no color spaces/displays registered -- all a
    plain CDLTransform (no color-space conversion involved) needs to build
    a Processor from. Cached since CreateRaw() has no reason to be rebuilt
    per call, same "don't rebuild what doesn't change" rule as aces_drt.py's
    _get_config cache."""
    _require_ocio()
    return ocio.Config.CreateRaw()


def build_cdl_transform(slope=IDENTITY_SLOPE, offset=IDENTITY_OFFSET,
                         power=IDENTITY_POWER, sat=IDENTITY_SAT,
                         style=DEFAULT_STYLE, sop_enabled=True, sat_enabled=True):
    """An ocio.CDLTransform for the given SOP/SAT values -- `sop_enabled`/
    `sat_enabled` neutralize their half (see module docstring) rather than
    being passed to OCIO directly, since CDLTransform itself has no notion
    of "bypass," only parameter values."""
    _require_ocio()
    effective_slope = tuple(slope) if sop_enabled else IDENTITY_SLOPE
    effective_offset = tuple(offset) if sop_enabled else IDENTITY_OFFSET
    effective_power = tuple(power) if sop_enabled else IDENTITY_POWER
    effective_sat = float(sat) if sat_enabled else IDENTITY_SAT

    transform = ocio.CDLTransform(
        slope=effective_slope, offset=effective_offset, power=effective_power,
        sat=effective_sat,
    )
    transform.setStyle(_style_to_ocio(style))
    return transform


def _apply_ocio_transform(transform, image_array):
    """Apply a bare (non-color-space) OCIO transform to an arbitrary-shaped
    (...,3) float array -- same flatten/PackedImageDesc/reshape dance as
    aces_drt._apply_ocio_processor, duplicated locally rather than imported
    since it's a few lines and keeps this module's only real dependency on
    aces_drt.py at zero (both are independently self-contained OCIO
    consumers, matching printer_lights.py's own "OCIO_AVAILABLE" pattern of
    checking its own import rather than reaching into a sibling module for
    it)."""
    _require_ocio()
    cfg = _get_raw_config()
    processor = cfg.getProcessor(transform)
    cpu = processor.getDefaultCPUProcessor()

    array = np.ascontiguousarray(np.asarray(image_array, dtype=np.float32))
    original_shape = array.shape
    flat = array.reshape(-1, 3).copy()
    n_pixels = flat.shape[0]
    desc = ocio.PackedImageDesc(flat, n_pixels, 1, 3)
    cpu.apply(desc)
    return flat.reshape(original_shape)


def apply_cdl(image_array, slope=IDENTITY_SLOPE, offset=IDENTITY_OFFSET,
              power=IDENTITY_POWER, sat=IDENTITY_SAT, style=DEFAULT_STYLE,
              sop_enabled=True, sat_enabled=True):
    """Apply an ASC CDL SOP+SAT grade to an (H,W,3) float array (or any
    (...,3)-shaped array, e.g. a LUT identity grid -- same "one function,
    used both for direct-pixel preview and for baking onto a grid" pattern
    as apply_aces_output_transform/apply_printer_lights_in_working_space).

    Identity parameters (default slope=(1,1,1), offset=(0,0,0),
    power=(1,1,1), sat=1.0) are an exact no-op under the default
    style="no_clip" -- see test_cdl.py. They are NOT a guaranteed no-op
    under style="asc": that style's intermediate ASC clamp applies
    regardless of SOP/SAT parameter values, so identity params with
    style="asc" will still silently clip any input already outside
    [0,1] (see module docstring).
    """
    transform = build_cdl_transform(
        slope=slope, offset=offset, power=power, sat=sat, style=style,
        sop_enabled=sop_enabled, sat_enabled=sat_enabled,
    )
    return _apply_ocio_transform(transform, image_array)
