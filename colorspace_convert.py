"""
colorspace_convert.py -- Qt-free backend for a PLAIN color space
conversion: reinterpret pixel values from one named OCIO color space to
another via ocio.ColorSpaceTransform, with NO rendering step involved --
not the RRT/ODT (or ACES 2.0 unified) Output Transform aces_drt.py applies.
Two distinct real workflows, both covered by the exact same mechanism:

  - SCENE-REFERRED to SCENE-REFERRED: re-express camera/working-space code
    values in a different scene-referred space's own encoding -- e.g. ARRI
    LogC3 (EI800) -> ACEScg, or ACEScct -> ACES2065-1. Pure encoding-curve
    and/or gamut math, the same category of operation as aces_drt.py's own
    decode_to_linear/encode_from_linear, just not restricted to a space and
    its own linear sibling -- any two scene-referred spaces the active
    config knows about.
  - DISPLAY-REFERRED to DISPLAY-REFERRED: reinterpret an already
    display-referred image's code values as if they were authored for a
    DIFFERENT display standard -- e.g. "sRGB - Display" code values read
    as "Rec.1886 Rec.709 - Display" code values. Useful for repurposing an
    already-graded master between display standards without re-rendering
    it through a DRT (which would require the ORIGINAL scene-referred
    source, not just the graded output).

Deliberately NOT offering scene-referred-to-display-referred (or the
reverse) here -- that conversion needs an actual rendering decision (which
RRT/ODT, which creative look), which is exactly what aces_drt.py's stage
kind is for. This module's list_scene_referred_colourspaces/
list_display_referred_colourspaces split lets the GUI layer keep the two
categories from being mixed in the same From/To pair (see
stack_panel.py's Category selector), even though OCIO's own
ColorSpaceTransform would technically allow it -- the RESTRICTION is a
deliberate UX choice here, not an OCIO limitation.

Reuses aces_drt.py's already-imported PyOpenColorIO module, its
OCIO_AVAILABLE/OCIO_IMPORT_ERROR flags, its cached builtin-config loader,
and its curated AVAILABLE_CONFIGS/DEFAULT_CONFIG_NAME list, rather than
re-importing OCIO or maintaining a second config registry independently --
unlike cdl.py (which is genuinely independent: CDLTransform has nothing to
do with named color spaces or configs at all), this module IS fundamentally
"another way to use the same config registry" aces_drt.py already curates,
so tight coupling to it is the right call here, matching
printer_lights.py's own precedent of reusing aces_drt.OCIO_AVAILABLE
directly rather than re-checking the import itself.

Same rule as every other backend module in this app: no PySide6 import
here, fully testable without a display.
"""
import aces_drt

OCIO_AVAILABLE = aces_drt.OCIO_AVAILABLE
OCIO_IMPORT_ERROR = aces_drt.OCIO_IMPORT_ERROR
DEFAULT_CONFIG_NAME = aces_drt.DEFAULT_CONFIG_NAME

# Matches aces_drt.py's own _DISPLAY_SUFFIX exactly -- OCIO's builtin ACES
# configs name every display-referred pseudo-color-space "<Display Name>
# - Display" (e.g. "sRGB - Display", "ST2084-P3-D65 - Display"), which is
# how aces_drt.list_working_colourspaces already excludes them from the
# scene-referred list; this module just also uses that same tag to build
# the COMPLEMENTARY (display-referred-only) list.
DISPLAY_SUFFIX = " - Display"


def _require_ocio():
    if not OCIO_AVAILABLE:
        raise RuntimeError(
            "PyOpenColorIO isn't installed, so color space conversion "
            f"features are unavailable (import error: {OCIO_IMPORT_ERROR}). "
            "Install it with: pip3 install opencolorio"
        )


def list_available_configs():
    """[(config_name, human_label), ...] -- same list aces_drt.py's ACES
    Config combo uses, kept identical on purpose (see module docstring:
    one config registry, shared across every OCIO-backed stage kind)."""
    return aces_drt.list_available_configs()


def list_scene_referred_colourspaces(config_name=DEFAULT_CONFIG_NAME):
    """Camera/working color spaces (ARRI LogC3/4, RED Log3G10, Sony
    S-Log3, ACEScg, ACES2065-1, ...) -- identical to
    aces_drt.list_working_colourspaces, re-exposed under this module's own
    name so callers reading colorspace_convert.py don't need to know that
    "scene-referred" and "working" are the same concept aces_drt.py just
    calls by a different name."""
    return aces_drt.list_working_colourspaces(config_name)


def list_display_referred_colourspaces(config_name=DEFAULT_CONFIG_NAME):
    """Display-referred pseudo-color-spaces (e.g. "sRGB - Display",
    "Rec.1886 Rec.709 - Display", "ST2084-P3-D65 - Display") -- the
    complementary set to list_scene_referred_colourspaces, both drawn from
    the same cfg.getColorSpaceNames() registry aces_drt.py itself reads
    from (via its cached _get_config -- reused here rather than
    re-parsing the same builtin config a second time)."""
    _require_ocio()
    cfg = aces_drt._get_config(config_name)
    names = list(cfg.getColorSpaceNames())
    return sorted(n for n in names if n.endswith(DISPLAY_SUFFIX))


def apply_colorspace_conversion(image_array, src, dst, config_name=DEFAULT_CONFIG_NAME):
    """Apply a plain ocio.ColorSpaceTransform(src -> dst) to an (H,W,3) (or
    any (...,3)-shaped, e.g. a LUT identity grid) float array -- no
    rendering, no RRT/ODT, just that one config's own declared conversion
    between two named color spaces (see module docstring for why this is a
    DIFFERENT operation from aces_drt.apply_aces_output_transform, not a
    simplified version of it).

    src == dst is an exact no-op (OCIO short-circuits identical color
    spaces internally). No clipping -- same "don't silently discard
    legitimate range" rule as aces_drt.py/printer_lights.py/cdl.py.

    Builds the processor via aces_drt._get_colourspace_conversion_
    processor (an lru_cache-wrapped helper, keyed on (config_name, src,
    dst)) rather than calling cfg.getProcessor(...).getDefaultCPU
    Processor() directly -- a real, previously-shipped performance bug
    (found in code review): building that processor graph from scratch
    is real, measurable work, and this function is on the hot path for
    every "colorspace" stage's own live-preview repaint AND every
    custom_drt.apply_custom_drt call (its own rendering-space conversion
    step) -- i.e. every frame while dragging any custom_drt slider. Every
    OTHER OCIO-processor-building call site in this app already reuses
    this exact cache (or ocio_processor_cache.py's own equivalent for
    parametrized transforms); this one was simply missed when it was
    introduced.
    """
    _require_ocio()
    cpu = aces_drt._get_colourspace_conversion_processor(config_name, src, dst)
    return aces_drt._apply_ocio_processor(cpu, image_array)
