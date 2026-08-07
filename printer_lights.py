"""
printer_lights.py -- Qt-free backend for a "printer lights" style trim: a
per-channel R/G/B adjustment expressed in the traditional film-lab "points"
scale, rather than raw gain/gamma numbers, so the controls feel like the
familiar answer-print timing convention (a small delta, centered at 0 =
"no change") instead of an abstract multiplier.

Convention used here: 1 point = 0.025 log10 exposure units, the standard
textbook definition of a printer point -- which makes 12 points ≈ 1 stop
(12 * 0.025 = 0.30 ≈ log10(2) = 0.301). Sign convention: INCREASING a
channel's points BRIGHTENS that channel -- chosen for digital-tool
intuitiveness, not because it matches which direction actual lab printer
lights move density (that depends on negative-vs-print conventions this
app doesn't otherwise track).

COLOR SPACE AWARENESS: a "stop" is a well-defined, universal concept only
in SCENE-LINEAR light -- doubling linear light is exactly +1 stop,
regardless of how that value is later encoded. But this app's LUTs/images
carry values in whatever encoding the working color space uses (ACEScct's
log curve, a camera log like ARRI LogC, a display gamma like sRGB/Rec.709,
or already-linear spaces like ACEScg). Naively multiplying a LOG-encoded
code value by a gain does NOT produce a stop shift -- log encodings need
an ADDITIVE code-value shift for that (this is exactly the wildcard-search
class of bug: something that happens to look plausible for one input
shape and is simply wrong in general). The fix here is the same one a
real ACES-aware printer-lights tool uses: decode the working color
space's own transfer function to get back to linear, apply the gain
THERE, then re-encode with that same transfer function
(apply_printer_lights_in_colourspace). Reuses colorspace.py's
lookup_colourspace -- the exact same color space registry Retarget
already exposes -- specifically for its .cctf_decoding/.cctf_encoding
(the transfer function alone, no primaries/gamut conversion, since input
and output are the same space here). Verified against colour-science
directly: decoding ACEScct code 0.5, doubling the resulting linear value,
and re-encoding lands exactly 1/17.52 higher -- ACEScct's own documented
"code values per stop" constant -- confirming this round-trip is
mathematically the correct way to apply a stop-based control to a
log-encoded value, not just a plausible-looking one.

Same rule as app_logic.py/scopes.py/aces_drt.py: no PySide6 import here,
fully testable without a display. bake_printer_lights_on_lut is the
integration point -- it composes the trim ON TOP of an existing LUT and
hands back a normal colour.LUT3D, same "bake once, it's just a LUT from
here on" pattern as bake_retargeted_lut/aces_drt's baking functions, so it
plugs into the 3D viewer, Concatenate chain, and every exporter for free.

REGISTRY CHOICE: apply_printer_lights_in_colourspace (above) decodes via
colour-science's own colour.RGB_COLOURSPACES registry, which has a real
ambiguity for some gamut names -- e.g. its plain 'Cinema Gamut' entry
silently defaults to a LINEAR transfer function, even though real Canon
footage in that gamut is virtually always Log2/Log3-encoded, so picking it
for genuinely log-encoded footage would silently apply the WRONG stop
math with no error raised. aces_drt.py's OCIO-backed registry disambiguates
this correctly (distinct 'CanonLog2 CinemaGamut D55' / 'CanonLog3
CinemaGamut D55' / 'Linear CinemaGamut D55' entries). apply_printer_lights_in_working_space
below prefers that OCIO registry whenever it's available (PyOpenColorIO
installed), falling back to the colour-science path only when it isn't --
bake_printer_lights_on_lut and the GUI both go through this auto-dispatching
function rather than calling apply_printer_lights_in_colourspace directly,
so the safer registry is used automatically without either caller having to
know or care which backend is actually active.
"""
import numpy as np
import colour

from colorspace import lookup_colourspace
import aces_drt

LOG_INCREMENT_PER_POINT = 0.025
POINTS_PER_STOP = np.log10(2.0) / LOG_INCREMENT_PER_POINT  # ~12.04

DEFAULT_WORKING_COLOURSPACE = "ACEScct"


def point_gain(points):
    """Points -> linear multiplier: 10 ** (points * LOG_INCREMENT_PER_POINT).
    0 points -> gain of 1.0 (no change), positive points brighten, negative
    darken. Works with a scalar or a numpy array of points."""
    return 10.0 ** (np.asarray(points, dtype=np.float64) * LOG_INCREMENT_PER_POINT)


def apply_printer_lights(image_array, r_points=0.0, g_points=0.0, b_points=0.0):
    """Apply independent R/G/B printer-light gains to a SCENE-LINEAR
    (...,3) array -- this is the one domain where "gain = stops" is
    actually true. For anything encoded (log, gamma), use
    apply_printer_lights_in_colourspace instead, which decodes to linear,
    calls this, and re-encodes.

    No clipping -- same "don't silently discard legitimate range" rule as
    concatenate_luts/apply_aces_output_transform elsewhere in this app.
    All-zero points is an exact no-op (gain of 1.0 on every channel)."""
    image_array = np.asarray(image_array, dtype=np.float64)
    gains = np.array([point_gain(r_points), point_gain(g_points), point_gain(b_points)])
    return image_array * gains


def apply_printer_lights_in_colourspace(image_array, working_colourspace,
                                         r_points=0.0, g_points=0.0, b_points=0.0):
    """Apply printer-light points to `image_array`, treating its values as
    ENCODED code values in `working_colourspace` (e.g. ACEScct log, an
    ARRI/RED/Sony camera log, sRGB/Rec.709 gamma, or an already-linear
    space like ACEScg) -- decodes to linear via that space's own
    cctf_decoding, applies the gain in linear light where a stop is a
    well-defined doubling, then re-encodes via cctf_encoding. All-zero
    points is a decode-then-encode round trip, which is an exact no-op to
    within floating-point precision for every space checked (see
    test_printer_lights.py) -- not a coincidence, cctf_encoding/
    cctf_decoding are meant to be exact inverses of each other."""
    cs = lookup_colourspace(working_colourspace)
    image_array = np.asarray(image_array, dtype=np.float64)
    linear = cs.cctf_decoding(image_array)
    trimmed_linear = apply_printer_lights(linear, r_points, g_points, b_points)
    return cs.cctf_encoding(trimmed_linear)


def apply_printer_lights_in_working_space(image_array, working_colourspace,
                                           r_points=0.0, g_points=0.0, b_points=0.0):
    """Auto-dispatching version of apply_printer_lights_in_colourspace: uses
    aces_drt's OCIO-backed ACES config registry to decode/encode when
    PyOpenColorIO is installed (aces_drt.OCIO_AVAILABLE), since its
    working-space names are unambiguous gamut+encoding pairs (see this
    module's docstring for the Cinema-Gamut-style bug that registry avoids).
    Falls back to the plain colour-science path
    (apply_printer_lights_in_colourspace) when OCIO isn't installed.

    `working_colourspace` must be a name valid for whichever registry ends
    up active -- i.e. one of aces_drt.list_working_colourspaces() when OCIO
    is available, or one of app_logic.list_available_colourspaces() (the
    colour-science registry) when it isn't. The GUI combo populates from
    whichever list matches (see lut_controls_panel.py), so callers going
    through the normal UI never have to reconcile the two namespaces
    themselves.
    """
    if aces_drt.OCIO_AVAILABLE:
        # No explicit float32 cast here -- aces_drt.decode_to_linear/
        # encode_from_linear both route through _apply_ocio_processor,
        # whose OCIO PackedImageDesc requires float32 regardless of what's
        # passed in (see that function's own docstring), so casting here
        # first was redundant, not a precision choice this module makes:
        # staying in float64 as long as possible doesn't buy back any
        # precision OCIO's own API contract already gives up at its
        # boundary (see test_printer_lights.py's
        # test_bake_zero_points_reproduces_source_for_other_working_spaces
        # for the actual, unavoidable-here float32 error this produces for
        # a gamma-shaped working space).
        linear = aces_drt.decode_to_linear(image_array, working_colourspace)
        trimmed_linear = apply_printer_lights(linear, r_points, g_points, b_points)
        return aces_drt.encode_from_linear(trimmed_linear, working_colourspace)
    return apply_printer_lights_in_colourspace(
        image_array, working_colourspace, r_points, g_points, b_points)


def bake_printer_lights_on_lut(lut, r_points=0.0, g_points=0.0, b_points=0.0,
                                working_colourspace=DEFAULT_WORKING_COLOURSPACE,
                                size=None, name=None):
    """Compose a printer-lights trim ON TOP of `lut` (i.e. lut is applied
    first, the trim second -- the usual "final timing pass over an
    existing grade" role printer lights play), sampled onto an identity
    grid and baked into a new flat colour.LUT3D. Same size-defaulting rule
    as bake_retargeted_lut: `lut`'s own resolution if it's a LUT3D, else 33.

    `working_colourspace` tells the trim what encoding `lut`'s OUTPUT is
    in, so the points-to-gain math happens in the correct (linear) domain
    regardless of whether that output is log-encoded, gamma-encoded, or
    already linear -- see this module's docstring for why that matters.
    Defaults to ACEScct, a common ACES-workflow working space for grading
    adjustments like this one; pick whatever the current LUT's output
    actually represents for a correct result on anything else.

    All-zero points reproduces `lut` exactly to within floating-point
    round-trip precision (see test_printer_lights.py), which is what
    makes this safe to wire up as an always-present "Apply" action rather
    than something that has to be conditionally skipped when nothing's
    actually changed.
    """
    if size is None:
        table = getattr(lut, "table", None)
        size = table.shape[0] if table is not None and np.ndim(table) == 4 else 33

    identity_grid = colour.LUT3D(size=size).table
    base = np.asarray(lut.apply(identity_grid), dtype=np.float64)
    trimmed = apply_printer_lights_in_working_space(
        base, working_colourspace, r_points, g_points, b_points)
    return colour.LUT3D(
        table=trimmed, size=size,
        name=name or f"{getattr(lut, 'name', None) or 'lut'}_printerlights",
    )
