"""
aces_drt.py -- Qt-free backend for applying a real ACES Output Transform
(what colorists mean by "DRT": the RRT+ODT chain, or ACES 2.0's unified
Output Transform) to identify a working/camera color space and render it
to a chosen display + view, via PyOpenColorIO and its BUILT-IN ACES
configs.

Deliberately NOT using colour-science's own ACES-adjacent math (the
ACEScct log encoding used elsewhere in this app, e.g. retarget.py) for
this -- that's just a transfer function, not the vetted RRT/ODT reference
pipeline colorists actually mean by "apply an ACES DRT." OpenColorIO's
built-in configs ARE that reference pipeline, maintained by the Academy,
and critically don't require downloading or shipping a separate .ocio
config file: OCIO >=2.3 bundles the ACES "CG" and "Studio" configs (both
ACES 1.3 and 2.0) internally, loaded via
Config.CreateFromBuiltinConfig(name) with no network access and no extra
files in this repo.

Same rule as app_logic.py/scopes.py: no PySide6 import here, fully
testable without a display. The eventual GUI layer's job is just picking
values from the combos this module provides lists for for and calling
bake_aces_output_transform_to_lut3d, which hands back a normal
colour.LUT3D -- from that point on it's just another LUT to the rest of
this app (3D viewer, sample image preview, Concatenate chain, every
exporter), no special-casing needed anywhere else.
"""
from functools import lru_cache

import numpy as np
import colour

try:
    import PyOpenColorIO as ocio
    OCIO_AVAILABLE = True
    OCIO_IMPORT_ERROR = None
except ImportError as e:  # pragma: no cover -- exercised by environments without opencolorio installed
    ocio = None
    OCIO_AVAILABLE = False
    OCIO_IMPORT_ERROR = str(e)


# Curated subset of OCIO's full builtin-config registry (which also
# includes plain "raw"/"default" non-ACES configs not relevant here).
# (config_name, human label). Ordered newest/most-recommended first within
# each of the two ACES versions; Studio configs include far more real
# camera working spaces (ARRI, RED, Sony, Canon, Blackmagic, Panasonic...)
# than the CG configs, which is almost certainly what "identify a working
# color space" means for real footage, so Studio is the default.
AVAILABLE_CONFIGS = [
    ("studio-config-v2.2.0_aces-v1.3_ocio-v2.4", "ACES 1.3 -- Studio (recommended)"),
    ("studio-config-v2.1.0_aces-v1.3_ocio-v2.3", "ACES 1.3 -- Studio (older OCIO)"),
    ("studio-config-v1.0.0_aces-v1.3_ocio-v2.1", "ACES 1.3 -- Studio (earliest)"),
    ("studio-config-v4.0.0_aces-v2.0_ocio-v2.5", "ACES 2.0 -- Studio"),
    ("cg-config-v2.2.0_aces-v1.3_ocio-v2.4", "ACES 1.3 -- CG (synthetic/render spaces only)"),
    ("cg-config-v4.0.0_aces-v2.0_ocio-v2.5", "ACES 2.0 -- CG (synthetic/render spaces only)"),
]
DEFAULT_CONFIG_NAME = AVAILABLE_CONFIGS[0][0]

_DISPLAY_SUFFIX = " - Display"

# Well-known, industry-standard (gamut-preserving) encoded-space -> linear
# sibling pairs, checked BEFORE the generic family/token-matching search in
# _find_native_linear_colourspace. Exists because that generic search can't
# always disambiguate on name tokens alone even though the real-world
# pairing is completely unambiguous -- e.g. 'ACEScct' and 'ACEScg' share no
# common word, yet ACEScg *is* ACEScct's one and only correct linear
# equivalent (same AP1 primaries) by definition, not by guesswork. Also
# covers the handful of camera families (ARRI, Sony, Blackmagic) where the
# family has more than one scene-linear member and token overlap ties.
_KNOWN_LINEAR_PAIRS = {
    "ACEScct": "ACEScg",
    "ACEScc": "ACEScg",
    "ARRI LogC3 (EI800)": "Linear ARRI Wide Gamut 3",
    "ARRI LogC4": "Linear ARRI Wide Gamut 4",
    "BMDFilm WideGamut Gen5": "Linear BMD WideGamut Gen5",
    "DaVinci Intermediate WideGamut": "Linear DaVinci WideGamut",
    "S-Log3 S-Gamut3": "Linear S-Gamut3",
    "S-Log3 S-Gamut3.Cine": "Linear S-Gamut3.Cine",
    "S-Log3 Venice S-Gamut3": "Linear Venice S-Gamut3",
    "S-Log3 Venice S-Gamut3.Cine": "Linear Venice S-Gamut3.Cine",
}


def _require_ocio():
    if not OCIO_AVAILABLE:
        raise RuntimeError(
            "PyOpenColorIO isn't installed, so ACES DRT features are "
            f"unavailable (import error: {OCIO_IMPORT_ERROR}). "
            "Install it with: pip3 install opencolorio"
        )


@lru_cache(maxsize=None)
def _get_config(config_name):
    """Cached ocio.Config for `config_name` -- CreateFromBuiltinConfig
    parses a real (if embedded) config file each time, so cache it rather
    than rebuilding on every combo-box repopulation or Apply click."""
    _require_ocio()
    return ocio.Config.CreateFromBuiltinConfig(config_name)


def list_available_configs():
    """[(config_name, human_label), ...] for the config-choice combo."""
    return list(AVAILABLE_CONFIGS)


def list_working_colourspaces(config_name=DEFAULT_CONFIG_NAME):
    """Scene-referred / camera working color spaces available in
    `config_name` -- i.e. everything EXCEPT the display-referred "- Display"
    color spaces (those are outputs, not something you'd identify footage
    as being shot in). This is what populates the "Working Color Space"
    combo (ARRI LogC3/4, RED Log3G10, Sony S-Log3, ACEScg, ACES2065-1,
    etc.)."""
    cfg = _get_config(config_name)
    names = list(cfg.getColorSpaceNames())
    return sorted(n for n in names if not n.endswith(_DISPLAY_SUFFIX))


def list_displays(config_name=DEFAULT_CONFIG_NAME):
    """Available displays (e.g. 'sRGB - Display', 'Rec.1886 Rec.709 -
    Display', 'ST2084-P3-D65 - Display') for `config_name`."""
    cfg = _get_config(config_name)
    return list(cfg.getDisplays())


def list_views(config_name=DEFAULT_CONFIG_NAME, display=None):
    """Available views (the actual Output Transform variants -- 'ACES 1.0
    - SDR Video', 'ACES 1.1 - HDR Video (1000 nits & Rec.2020 lim)', etc.)
    for `display` in `config_name`. Defaults to the config's default
    display if none given."""
    cfg = _get_config(config_name)
    if display is None:
        display = cfg.getDefaultDisplay()
    return list(cfg.getViews(display))


def get_default_display_and_view(config_name=DEFAULT_CONFIG_NAME):
    """(display, view) the config itself nominates as default -- a
    reasonable starting selection for the GUI combos."""
    cfg = _get_config(config_name)
    display = cfg.getDefaultDisplay()
    view = cfg.getDefaultView(display)
    return display, view


def _apply_ocio_processor(cpu_processor, array):
    """Apply an OCIO CPUProcessor to an arbitrary-shaped (..., 3) float32
    array. OCIO's PackedImageDesc requires a strict (width, height,
    channels) image layout, which a LUT identity grid's natural
    (size,size,size,3) shape isn't -- so this flattens to a single row of
    pixels, applies, and reshapes back, working identically for a real
    (H,W,3) image array or an (N,N,N,3) LUT grid. Returns a NEW array;
    does not mutate the input."""
    array = np.ascontiguousarray(np.asarray(array, dtype=np.float32))
    original_shape = array.shape
    flat = array.reshape(-1, 3).copy()
    n_pixels = flat.shape[0]
    desc = ocio.PackedImageDesc(flat, n_pixels, 1, 3)
    cpu_processor.apply(desc)
    return flat.reshape(original_shape)


@lru_cache(maxsize=None)
def _get_output_transform_processor(config_name, working_colourspace, display, view):
    """Cached: building an OCIO Processor/CPUProcessor from a Config is
    real work (constructs a transform graph), and this function's own
    arguments are already exactly its cache key -- all strings, and the
    result depends on nothing else. Uncached, this ran fresh on every
    single call to apply_aces_output_transform, including every live
    preview repaint while dragging any control on a Stack that has an
    ACES DRT stage (see stack.py's _apply_stage_to_array -- every stack
    mutation re-evaluates every stage), rebuilding an identical processor
    over and over for parameters that hadn't changed."""
    _require_ocio()
    cfg = _get_config(config_name)
    processor = cfg.getProcessor(
        working_colourspace, display, view, ocio.TRANSFORM_DIR_FORWARD
    )
    return processor.getDefaultCPUProcessor()


def apply_aces_output_transform(image_array, working_colourspace, display, view,
                                 config_name=DEFAULT_CONFIG_NAME):
    """Apply the real ACES Output Transform (RRT+ODT, or ACES 2.0's unified
    transform, whichever `config_name` provides) from `working_colourspace`
    to `display`/`view`, on an (H,W,3) float array.

    No clipping is applied -- an HDR view can legitimately produce values
    outside [0,1], same "don't silently discard legitimate range" rule as
    concatenate_luts/bake_process_list_to_lut3d elsewhere in this app.
    Callers that need a clipped [0,1] result for on-screen/8-bit preview
    (e.g. feeding array_to_png_bytes) should clip themselves, the same way
    apply_lut_to_image_array does for ordinary LUTs.
    """
    cpu = _get_output_transform_processor(config_name, working_colourspace, display, view)
    return _apply_ocio_processor(cpu, image_array)


@lru_cache(maxsize=None)
def _find_native_linear_colourspace(cfg, working_colourspace):
    """Cached: this does a linear scan of every color space in `cfg`
    (getColorSpaceNames(), then getFamily()/getEncoding() per candidate)
    to find the sibling -- real cost against the Studio config's large
    registry, and every call for the same (cfg, working_colourspace) pair
    gives the identical answer (the result depends on nothing else).
    `cfg` is hashable (default identity hash) and _get_config already
    caches by config_name, so the same cfg object -- and therefore the
    same cache entry -- is reused correctly across calls for the same
    config. Uncached, this ran on every single decode_to_linear/
    encode_from_linear call, i.e. every live preview repaint while
    dragging a Printer Lights R/G/B slider (see printer_lights.py's
    apply_printer_lights_in_working_space and stack.py's
    _apply_stage_to_array, which re-evaluates every stage on every stack
    mutation).

    Find the color space in `cfg` that shares `working_colourspace`'s own
    native primaries/gamut but is scene-linear (no encoding curve applied)
    -- e.g. 'Linear CinemaGamut D55' for 'CanonLog2 CinemaGamut D55'.

    This matters because decode_to_linear/encode_from_linear exist so
    printer_lights.py can apply a per-channel R/G/B gain in linear light --
    and that gain is only meaningful relative to a SPECIFIC set of
    primaries (that's the whole concept: "brighten the red channel" means
    the red primary of whatever gamut you're in). Converting through a
    DIFFERENT reference space (e.g. always normalizing to ACES2065-1, as
    an earlier version of this function did) applies a gamut matrix before
    and after the gain, which mixes channels together -- introducing
    crosstalk a diagonal R/G/B gain was never meant to have, and silently
    giving a different (wrong) answer than gaining in the native gamut.
    Staying within the same gamut, only stripping/reapplying the encoding
    curve, is the only version of this that's actually correct.

    Uses this config's family/encoding metadata to find the sibling: if
    `working_colourspace` is itself already scene-linear, it IS its own
    answer. Otherwise, look within the same family (e.g. "Input/Canon",
    "Input/Sony") for scene-linear members. If there's exactly one, that's
    the sibling. If there's more than one (e.g. ARRI's family has both
    "Linear ARRI Wide Gamut 3" and "...4"; Sony's has four Venice/S-Gamut3
    variants), disambiguate by shared name tokens with
    `working_colourspace` -- and if that's still tied/ambiguous, raise
    rather than silently guessing wrong, same "loud failure over silent
    wrongness" rule this app follows everywhere else (see
    printer_lights.py's module docstring).
    """
    cs = cfg.getColorSpace(working_colourspace)
    if cs.getEncoding() == "scene-linear":
        return working_colourspace

    known = _KNOWN_LINEAR_PAIRS.get(working_colourspace)
    if known is not None and known in cfg.getColorSpaceNames():
        return known

    family = cs.getFamily()
    all_names = [n for n in cfg.getColorSpaceNames() if not n.endswith(_DISPLAY_SUFFIX)]
    linear_siblings = [
        n for n in all_names
        if cfg.getColorSpace(n).getFamily() == family
        and cfg.getColorSpace(n).getEncoding() == "scene-linear"
    ]

    if len(linear_siblings) == 1:
        return linear_siblings[0]

    if len(linear_siblings) > 1:
        def tokens(name):
            return set(name.replace("(", " ").replace(")", " ").replace(".", " ").split())

        encoded_tokens = tokens(working_colourspace)
        scored = sorted(
            ((len(encoded_tokens & tokens(cand)), cand) for cand in linear_siblings),
            reverse=True,
        )
        top_score, top_candidate = scored[0]
        runner_up_score = scored[1][0] if len(scored) > 1 else -1
        if top_score > 0 and top_score > runner_up_score:
            return top_candidate
        raise RuntimeError(
            f"Can't unambiguously find a same-gamut linear color space for "
            f"{working_colourspace!r} -- family {family!r} has {len(linear_siblings)} "
            f"scene-linear candidates that tie or are all equally (un)related by "
            f"name: {linear_siblings}. Pick a more specific working color space "
            "name, or handle this pairing explicitly rather than guessing."
        )

    raise RuntimeError(
        f"No scene-linear color space found in family {family!r} "
        f"(needed to decode {working_colourspace!r} to linear in its own gamut)."
    )


@lru_cache(maxsize=None)
def _get_colourspace_conversion_processor(config_name, src_space, dst_space):
    """Cached for the same reason _get_output_transform_processor is:
    cfg.getProcessor(...).getDefaultCPUProcessor() builds a real transform
    graph, and (config_name, src_space, dst_space) is already this
    function's whole cache key -- decode_to_linear/encode_from_linear
    each call this once per invocation, previously rebuilding an
    identical processor from scratch every time (every live preview
    repaint while dragging a Printer Lights slider, same hot path
    _find_native_linear_colourspace's docstring describes)."""
    _require_ocio()
    cfg = _get_config(config_name)
    processor = cfg.getProcessor(ocio.ColorSpaceTransform(src=src_space, dst=dst_space))
    return processor.getDefaultCPUProcessor()


def decode_to_linear(image_array, working_colourspace, config_name=DEFAULT_CONFIG_NAME):
    """Convert `image_array` from encoded code values in
    `working_colourspace` to that SAME gamut's linear equivalent (see
    _find_native_linear_colourspace) -- i.e. just the encoding-curve half
    of what apply_aces_output_transform does, with no gamut conversion and
    no display/view rendering involved.

    Exists for printer_lights.py: applying a stop-based gain correctly
    requires linearizing first, and this config's working-space registry
    is the more complete, unambiguous one for that -- every name pairs a
    specific gamut with a specific encoding (e.g. 'CanonLog2 CinemaGamut
    D55' vs 'CanonLog3 CinemaGamut D55' vs 'Linear CinemaGamut D55' as
    three distinct entries), unlike colour-science's own registry where
    some gamut-named entries (e.g. plain 'Cinema Gamut') silently default
    to a linear transfer function even though real footage in that gamut
    is virtually always log-encoded.
    """
    _require_ocio()
    cfg = _get_config(config_name)
    linear_space = _find_native_linear_colourspace(cfg, working_colourspace)
    cpu = _get_colourspace_conversion_processor(config_name, working_colourspace, linear_space)
    return _apply_ocio_processor(cpu, image_array)


def encode_from_linear(image_array, working_colourspace, config_name=DEFAULT_CONFIG_NAME):
    """Inverse of decode_to_linear -- `image_array` is scene-linear values
    in `working_colourspace`'s own native gamut, converted to encoded code
    values in `working_colourspace`."""
    _require_ocio()
    cfg = _get_config(config_name)
    linear_space = _find_native_linear_colourspace(cfg, working_colourspace)
    cpu = _get_colourspace_conversion_processor(config_name, linear_space, working_colourspace)
    return _apply_ocio_processor(cpu, image_array)


def bake_aces_output_transform_to_lut3d(working_colourspace, display, view,
                                         config_name=DEFAULT_CONFIG_NAME,
                                         size=33, name=None):
    """Bake the ACES Output Transform for (working_colourspace, display,
    view) into a flat colour.LUT3D, by sampling it on an identity grid --
    same "trust the real transform, don't hand-roll interpolation" pattern
    as bake_retargeted_lut/bake_process_list_to_lut3d.

    Baking this into a colour.LUT3D is the whole integration strategy:
    from this point on, the DRT is just another LUT to the rest of the
    app -- it can be viewed in the 3D viewer, previewed against the sample
    image, dropped into a Concatenate chain alongside ordinary LUTs, and
    exported to any of the existing writers. No other module needs to
    know OCIO was involved.
    """
    identity_grid = colour.LUT3D(size=size).table  # (size,size,size,3), domain [0,1]
    baked_table = apply_aces_output_transform(
        identity_grid, working_colourspace, display, view, config_name=config_name
    )
    default_name = f"ACES_{working_colourspace}_to_{display}_{view}".replace(" ", "_")
    return colour.LUT3D(table=baked_table, size=size, name=name or default_name)
