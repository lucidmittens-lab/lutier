"""
colorspace.py -- color space lookup and conversion, built on colour-science's
verified RGB colourspace definitions and RGB_to_RGB conversion.

This module is the single place that knows how to turn a color space *name*
into a real colourspace definition. Kept deliberately thin and separate from
retarget.py so that a custom/proprietary color science registry (mentioned
as a "down the line" need) can be swapped in later without touching the
retargeting logic itself -- just point CUSTOM_COLOURSPACES at whatever
registry is needed and it participates in lookup_colourspace() the same way
built-in ACES spaces do.

GAMUT/EOTF PAIRING (list_colourspace_choices): colour-science's own
RGB_COLOURSPACES keys are misleading to pick from bare -- most camera-native
entries are named after their GAMUT only ("ARRI Wide Gamut 3", "S-Gamut3",
"V-Gamut", "REDWideGamutRGB", ...) even though each one is actually defined
with a specific, non-linear cctf_encoding/cctf_decoding pair baked in as its
DEFAULT (LogC3, S-Log3, V-Log, Log3G10, respectively -- verified by
inspecting colour.RGB_COLOURSPACES[name].cctf_encoding directly, not
assumed). So `convert()` below already applies BOTH the primaries matrix
(via RGB_to_RGB's chromatic adaptation) AND that space's own log/EOTF
encode-decode whenever apply_cctf_decoding/apply_cctf_encoding are True
(the default) -- nothing was ever gamut-only under the hood. The UI-facing
problem this module previously had was purely a LABELING one: a From/To
picker built straight from list_available_colourspaces() shows "ARRI Wide
Gamut 3" with no indication of which log curve comes along with it, so two
different pickers could show the identical bare name for spaces that behave
completely differently once the actual cctf runs. list_colourspace_choices()
fixes that by labeling every entry with its gamut AND its curve together
(e.g. "ARRI Wide Gamut 3 -- LogC3"), while still resolving back to the exact
same colour.RGB_Colourspace via lookup_colourspace() -- so picking any label
from that list is guaranteed to run both transforms, and it's obvious from
the label alone which curve that is.
"""
import re

import colour

# Populated later if/when custom color science needs to be plugged in.
# Keys are space names as strings, values are colour.RGB_Colourspace objects
# built the same way colour-science builds its own (primaries, whitepoint,
# and cctf_encoding/cctf_decoding transfer functions if the space is
# log/non-linear).
CUSTOM_COLOURSPACES: dict[str, "colour.RGB_Colourspace"] = {}


def lookup_colourspace(name: str) -> "colour.RGB_Colourspace":
    """Resolve a color space name to a colour.RGB_Colourspace, checking the
    custom registry first so local definitions can override or extend the
    built-in colour-science set."""
    if name in CUSTOM_COLOURSPACES:
        return CUSTOM_COLOURSPACES[name]
    if name in colour.RGB_COLOURSPACES:
        return colour.RGB_COLOURSPACES[name]
    raise KeyError(
        f"Unknown color space {name!r}. Known spaces: "
        f"{sorted(set(colour.RGB_COLOURSPACES.keys()) | set(CUSTOM_COLOURSPACES.keys()))}"
    )


def convert(values, from_space: str, to_space: str,
            chromatic_adaptation_transform: str = "CAT02",
            decode_input: bool = True, encode_output: bool = True):
    """Convert RGB code values from one color space's encoding to another's.

    decode_input/encode_output default to True, meaning: treat `values` as
    *encoded* code values in `from_space` (e.g. ACEScct log values, not
    scene-linear), decode them to scene-linear, convert primaries, then
    re-encode into `to_space`'s own transfer function. This is the common
    case for LUT domain conversion. Set either flag False if you're handing
    in/expecting already-linear values on that side instead.
    """
    return colour.RGB_to_RGB(
        values,
        lookup_colourspace(from_space),
        lookup_colourspace(to_space),
        chromatic_adaptation_transform=chromatic_adaptation_transform,
        apply_cctf_decoding=decode_input,
        apply_cctf_encoding=encode_output,
    )


# ---- gamut/EOTF-paired display labels for pickers -------------------------
#
# Curated short names for the cctf functions colour-science ships, so a
# label reads "LogC3" instead of "log_encoding_ARRILogC3". Anything not in
# here still gets a reasonable label via _cctf_short_name's regex fallback
# below (stripped of the common colour-science prefix), not silently
# dropped -- an unrecognized curve should look unfamiliar, not invisible.
_CCTF_LABELS = {
    "linear_function": "Linear",
    "log_encoding_ACEScct": "ACEScct",
    "log_decoding_ACEScct": "ACEScct",
    "log_encoding_ACEScc": "ACEScc",
    "log_decoding_ACEScc": "ACEScc",
    "log_encoding_ACESproxy": "ACESproxy",
    "log_decoding_ACESproxy": "ACESproxy",
    "log_encoding_ARRILogC3": "LogC3",
    "log_decoding_ARRILogC3": "LogC3",
    "log_encoding_ARRILogC4": "LogC4",
    "log_decoding_ARRILogC4": "LogC4",
    "log_encoding_SLog3": "S-Log3",
    "log_decoding_SLog3": "S-Log3",
    "log_encoding_SLog2": "S-Log2",
    "log_decoding_SLog2": "S-Log2",
    "log_encoding_VLog": "V-Log",
    "log_decoding_VLog": "V-Log",
    "log_encoding_Log3G10": "Log3G10",
    "log_decoding_Log3G10": "Log3G10",
    "log_encoding_Log3G12": "Log3G12",
    "log_decoding_Log3G12": "Log3G12",
    "log_encoding_CanonLog": "Canon-Log",
    "log_decoding_CanonLog": "Canon-Log",
    "log_encoding_CanonLog2": "Canon-Log2",
    "log_decoding_CanonLog2": "Canon-Log2",
    "log_encoding_CanonLog3": "Canon-Log3",
    "log_decoding_CanonLog3": "Canon-Log3",
    "oetf_DaVinciIntermediate": "DaVinci Intermediate",
    "oetf_inverse_DaVinciIntermediate": "DaVinci Intermediate",
    "oetf_BlackmagicFilmGeneration5": "Blackmagic Film Gen 5",
    "oetf_inverse_BlackmagicFilmGeneration5": "Blackmagic Film Gen 5",
    "oetf_sRGB": "sRGB",
    "eotf_inverse_sRGB": "sRGB",
    "oetf_BT709": "Rec. 709",
    "oetf_inverse_BT709": "Rec. 709",
    "eotf_ST2084": "ST 2084 (PQ)",
    "eotf_inverse_ST2084": "ST 2084 (PQ)",
    "log_encoding_REDLog": "REDLog",
    "log_decoding_REDLog": "REDLog",
    "log_encoding_REDLogFilm": "REDLogFilm",
    "log_decoding_REDLogFilm": "REDLogFilm",
}

_CCTF_PREFIX_RE = re.compile(
    r"^(log_encoding_|log_decoding_|oetf_inverse_|oetf_|eotf_inverse_|eotf_|"
    r"cctf_encoding_|cctf_decoding_|gamma_function)"
)


def _cctf_short_name(cctf_func):
    """Best-effort human-readable name for a colour-science cctf function --
    curated table first, then a regex-stripped fallback of the function's
    own __name__ so an unrecognized curve still shows SOMETHING meaningful
    (e.g. an unlisted "log_encoding_Foo" becomes "Foo") rather than being
    silently omitted from the label."""
    if cctf_func is None:
        return "Linear"
    raw_name = getattr(cctf_func, "__name__", "") or ""
    if raw_name in _CCTF_LABELS:
        return _CCTF_LABELS[raw_name]
    cleaned = _CCTF_PREFIX_RE.sub("", raw_name).strip("_")
    return cleaned or raw_name or "Unknown"


def list_colourspace_choices():
    """Sorted list of (display_label, colourspace_name) pairs for From/To
    pickers, labeled by GAMUT + EOTF/log curve together (e.g. "ARRI Wide
    Gamut 3 -- LogC3") instead of the bare gamut-only name
    list_available_colourspaces() returns -- see this module's docstring
    for why the bare name was ambiguous even though the underlying
    transform was always correct. `colourspace_name` is always a real key
    resolvable via lookup_colourspace()/convert(), so callers should build
    combo box items as addItem(label, userData=colourspace_name) and read
    back .currentData(), not parse the label."""
    from app_logic import list_available_colourspaces

    choices = []
    for name in list_available_colourspaces():
        cs = lookup_colourspace(name)
        curve_label = _cctf_short_name(getattr(cs, "cctf_encoding", None))
        if curve_label.lower() in name.lower():
            label = name
        else:
            label = f"{name} — {curve_label}"
        choices.append((label, name))
    return sorted(choices, key=lambda pair: pair[0].lower())
