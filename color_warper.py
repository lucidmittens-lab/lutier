"""
color_warper.py -- Qt-free backend for "WARPier": a hue/chroma warp stage
equivalent to DaVinci Resolve's Color Warper, but built on OKLCh (a
perceptually uniform color space -- see Bjorn Ottosson's OKLab, the
matrices below are the published reference constants) instead of raw
HSL. Resolve's warper places a fixed set of primary/secondary control
points around a hue wheel and lets you drag each one; equal steps around
an HSL wheel do NOT correspond to equal perceived color differences
(HSL's "hue" spacing is famously uneven -- yellows/greens are crowded,
blues/magentas stretched), which is exactly the distortion a
perceptually uniform space avoids. WARPier also allows an arbitrary
number of custom control points (not just 3 fixed ones), and both a
hue-angle move (recolor) AND a chroma move (desaturate toward/away from
the white/neutral axis) at each point.

COLOR SPACE AWARENESS: OKLab's own conversion matrices are defined for
one specific fixed set of primaries -- linear Rec.709/sRGB -- so, unlike
printer_lights.py's diagonal R/G/B gain (deliberately kept in the
image's OWN native gamut to avoid channel crosstalk a diagonal gain was
never meant to have, see that module's docstring), WARPier's math
genuinely needs a real gamut conversion: whatever `working_colourspace`
the image is actually in, converted (primaries AND encoding) to
"Linear Rec.709 (sRGB)" before the warp, and back afterward.
aces_drt.convert_colourspace does that hop via OCIO's ACES Studio config
when available; colorspace.convert (colour.RGB_to_RGB) is the
colour-science fallback when it isn't -- same auto-dispatch convention
apply_printer_lights_in_working_space uses. "Linear Rec.709 (sRGB)" was
confirmed present, scene-linear, under this exact name in BOTH
registries, so one target name works for either backend.

Each control point is `{"h0", "c0", "h", "c"}`: h0/c0 is the point's own
ANCHOR position (which hue/chroma neighborhood it grabs), h/c is where
it's been dragged to. Both hue (degrees, 0-360) and chroma are ABSOLUTE
OKLCh values -- not a fraction of any display-only reference boundary --
so a point warps identically regardless of a pixel's own lightness.
Lightness (L) is left untouched by the warp in this version, matching
Resolve's own Color Warper scope (hue/saturation only).

WAKE/DROPOFF: how far a point's influence reaches into neighboring hues
(wake, degrees) and how hard vs. soft that falloff is (dropoff, 0..1) --
global for the whole stage. Falloff is a super-Gaussian,
weight(d) = exp(-0.5 * (d/wake)^(2n)), n = 1 + dropoff*7: dropoff=0 is a
plain Gaussian (n=1), dropoff=1 approaches a hard plateau that stays
near full strength across the whole wake radius then cuts off sharply
(n=8). A pixel's warped hue/chroma is the weighted blend of every
point's own hue-delta/chroma-scale, weighted by that point's falloff at
the pixel's angular distance from the point's own h0 -- ported directly
from the interactive concept mockup this operator's design was validated
against.

Same "no PySide6 import" rule as printer_lights.py/aces_drt.py/
scopes.py -- fully testable without a display.
"""
import numpy as np

import aces_drt
import colorspace

DEFAULT_WORKING_COLOURSPACE = "ACEScct"
REFERENCE_LINEAR_SPACE = "Linear Rec.709 (sRGB)"

MAX_POINTS = 12
DEFAULT_WAKE = 35.0
DEFAULT_DROPOFF = 0.0
# The chroma-axis counterpart to WAKE/DROPOFF above (see warp_hue_chroma's
# own docstring for why this axis exists at all) -- 0.5 is deliberately
# WIDER than chroma's own practical ceiling (~0.4 for a saturated sRGB
# primary, see keyer.DEFAULT_CHROMA_MAX's own comment), so this default is
# a genuine no-op: falloff_weight(any realistic chroma distance, wake=0.5,
# ...) stays ~1.0, meaning every existing session/test that never touches
# the new Chroma Wake/Drop-off controls keeps behaving EXACTLY as before
# this axis existed.
DEFAULT_CHROMA_WAKE = 0.5
DEFAULT_CHROMA_DROPOFF = 0.0

# Bounds on warp_hue_chroma's own per-point chroma_scale ratio (p["c"]/c0)
# -- a real, previously-shipped bug (found in code review, then confirmed
# by hand): with no ceiling, dragging a point that started near-neutral
# (c0 close to 0, easy to land on with an ordinary click near the wheel's
# own center) out to a normal chroma could produce a ratio in the
# hundreds, blowing out every nearby pixel's own chroma by that same
# factor. CHROMA_SCALE_FLOOR raises c0's own "treat as unscalable, fall
# back to an identity 1.0 scale" threshold from the previous 1e-6 (so
# small it barely guarded anything a real click could produce) up to a
# genuinely near-neutral chroma; MAX_CHROMA_SCALE is a second, independent
# ceiling on the ratio itself for any c0 that clears that floor. Neither
# bound is reachable by an ordinary drag -- pushing a point's own chroma
# to 4x its starting point, from a starting point that isn't near-
# neutral, is already an extreme, clearly-intentional move.
CHROMA_SCALE_FLOOR = 0.02
MAX_CHROMA_SCALE = 4.0

# Display-orientation convention shared by every OKLCh visualization in
# this app (WarpierWheel, the volumetric scope) -- picked so green (OKLCh
# green's own approximate hue angle) lands at screen-down and magenta
# near screen-up (per Tom). Kept here (Qt-free) rather than in
# color_warper_wheel.py so a Qt-free consumer (e.g. volumetric_scope.py)
# can share the exact same orientation without importing a Qt-heavy
# module just for two numbers.
GREEN_HUE = 142.5
HUE_ROTATE = 90.0 - GREEN_HUE

# Companions to GREEN_HUE -- the other five standard vectorscope target
# hues (red/blue/cyan/magenta/yellow), used by WarpierWheel's own
# Resolve-style vectorscope graticule overlay (six small target boxes,
# see color_warper_wheel.py's own docstring). Same derivation as
# GREEN_HUE: each canonical sRGB primary/secondary's own real OKLCh hue
# angle, rounded to one decimal -- these are NOT evenly spaced around the
# circle (real broadcast vectorscope targets aren't either), so they just
# land wherever their own real hue naturally falls under the wheel's
# plain uniform hue-to-screen rotation.
RED_HUE = 29.2
BLUE_HUE = 264.1
CYAN_HUE = 194.8
MAGENTA_HUE = 328.4
YELLOW_HUE = 109.8

# ---- OKLab <-> linear sRGB (Rec.709 primaries) -- Ottosson's published
# reference matrices, https://bottosson.github.io/posts/oklab/ ---------

_FWD_M1 = np.array([
    [0.4122214708, 0.5363325363, 0.0514459929],
    [0.2119034982, 0.6806995451, 0.1073969566],
    [0.0883024619, 0.2817188376, 0.6299787005],
])
_FWD_M2 = np.array([
    [0.2104542553, 0.7936177850, -0.0040720468],
    [1.9779984951, -2.4285922050, 0.4505937099],
    [0.0259040371, 0.7827717662, -0.8086757660],
])
_INV_M2 = np.array([
    [1.0, 0.3963377774, 0.2158037573],
    [1.0, -0.1055613458, -0.0638541728],
    [1.0, -0.0894841775, -1.2914855480],
])
_INV_M1 = np.array([
    [4.0767416621, -3.3077115913, 0.2309699292],
    [-1.2684380046, 2.6097574011, -0.3413193965],
    [-0.0041960863, -0.7034186147, 1.7076147010],
])


def linear_srgb_to_oklab(rgb):
    """(...,3) linear Rec.709/sRGB -> (...,3) OKLab (L,a,b)."""
    rgb = np.asarray(rgb, dtype=np.float64)
    lms = rgb @ _FWD_M1.T
    lms_ = np.cbrt(lms)
    return lms_ @ _FWD_M2.T


def oklab_to_linear_srgb(lab):
    """Inverse of linear_srgb_to_oklab -- (...,3) OKLab -> (...,3) linear
    Rec.709/sRGB. Round trip is exact to within ~1e-6 for in-gamut values
    (the forward/inverse matrices are independently published constants,
    not exact algebraic inverses of each other -- see test_color_warper.py)."""
    lab = np.asarray(lab, dtype=np.float64)
    lms_ = lab @ _INV_M2.T
    lms = lms_ ** 3
    return lms @ _INV_M1.T


def oklab_to_oklch(lab):
    """(...,3) OKLab -> (L, C, H) -- H in degrees, 0-360."""
    lab = np.asarray(lab, dtype=np.float64)
    L = lab[..., 0]
    a = lab[..., 1]
    b = lab[..., 2]
    C = np.hypot(a, b)
    H = np.degrees(np.arctan2(b, a)) % 360.0
    return L, C, H


def oklch_to_oklab(L, C, H):
    """Inverse of oklab_to_oklch."""
    L = np.asarray(L, dtype=np.float64)
    C = np.asarray(C, dtype=np.float64)
    H = np.asarray(H, dtype=np.float64)
    rad = np.radians(H)
    a = C * np.cos(rad)
    b = C * np.sin(rad)
    return np.stack(np.broadcast_arrays(L, a, b), axis=-1)


def is_in_gamut(l, c, h):
    """True if the given OKLCh triple decodes to displayable (0..1, with
    a small numeric tolerance) linear Rec.709/sRGB -- i.e. whether this
    is actually a color a real display in that gamut can show. `l`/`c`/`h`
    may be scalars or numpy arrays of matching shape (returns a scalar
    bool or an array of bools respectively). Shared by WarpierWheel's own
    gamut-shaped disc (color_warper_wheel.py) and
    warped_gamut_blob_points below -- both need "is this OKLCh point
    actually visible," just for different purposes (a 2D disc boundary
    vs. a 3D gamut-solid surface)."""
    rgb = oklab_to_linear_srgb(oklch_to_oklab(l, c, h))
    return np.all((rgb >= -1e-3) & (rgb <= 1 + 1e-3), axis=-1)


_CHROMA_STEP = 0.004
_CHROMA_CEILING = 0.4


def _grow_chroma_to_gamut_boundary(L_flat, H_flat):
    """Vectorized "grow chroma from 0 until it leaves gamut" search --
    same idea compute_gamut_boundary (color_warper_wheel.py) does one hue
    at a time, done for every (L,H) pair in the flat input arrays
    simultaneously. Returns C_flat, the MAXIMUM in-gamut chroma (see
    is_in_gamut) for each pair -- i.e. each point sits exactly on the
    surface of the visible Rec.709/sRGB gamut."""
    C_flat = np.zeros_like(L_flat)
    still_growing = np.ones_like(L_flat, dtype=bool)
    c = 0.0
    while c + _CHROMA_STEP < _CHROMA_CEILING and np.any(still_growing):
        candidate = c + _CHROMA_STEP
        in_gamut = is_in_gamut(L_flat, np.full_like(L_flat, candidate), H_flat)
        advance = still_growing & in_gamut
        C_flat = np.where(advance, candidate, C_flat)
        still_growing = still_growing & in_gamut
        c = candidate
    return C_flat


def _spherical_grid_lh(hue_steps, theta_steps):
    """Shared by warped_gamut_blob_points/warped_idealized_sphere_points
    -- the base (L, H) flat arrays for a hue_steps x theta_steps
    spherical grid (theta = pi*(1-L), so L=1 -> theta=0 -> north pole,
    L=0 -> theta=pi -> south pole)."""
    theta = np.linspace(0.0, np.pi, theta_steps)
    L_levels = 1.0 - theta / np.pi
    hues = np.linspace(0.0, 360.0, hue_steps, endpoint=False)
    L_grid, H_grid = np.meshgrid(L_levels, hues, indexing="ij")
    return L_grid.ravel(), H_grid.ravel()


def _warp_and_embed_spherically(L_flat, H_flat, C_flat, points, wake, dropoff,
                                 chroma_wake=DEFAULT_CHROMA_WAKE, chroma_dropoff=DEFAULT_CHROMA_DROPOFF):
    """Shared by warped_gamut_blob_points/warped_idealized_sphere_points
    -- applies warp_hue_chroma to the given (L, H, C) grid, then embeds
    SPHERICALLY: hue is azimuth, lightness is polar angle, and chroma is
    the radius. That embedding is what puts the achromatic point at the
    shape's own geometric CENTER (position -> (0,0,0) as chroma -> 0,
    regardless of hue or lightness) instead of spread along a straight
    black-to-white axis the way an ordinary x=cos(H)*C, y=sin(H)*C, z=L
    cylinder would -- per Tom's own framing: "the most regular 3
    dimension shape... with white at the center and the volumes from 0
    chroma to 100 chroma at the border." Returns `(positions, colors)`."""
    H2, C2 = warp_hue_chroma(H_flat, C_flat, points, wake=wake, dropoff=dropoff,
                              chroma_wake=chroma_wake, chroma_dropoff=chroma_dropoff)

    theta2 = np.pi * (1.0 - L_flat)
    rad = np.radians(H2 + HUE_ROTATE)
    sin_t = np.sin(theta2)
    x = C2 * sin_t * np.cos(rad)
    y = C2 * sin_t * np.sin(rad)
    z = C2 * np.cos(theta2)
    positions = np.stack([x, y, z], axis=-1)

    linear = oklab_to_linear_srgb(oklch_to_oklab(L_flat, C2, H2))
    colors = np.clip(
        colorspace.convert(linear, REFERENCE_LINEAR_SPACE, "sRGB",
                            decode_input=False, encode_output=True),
        0.0, 1.0)
    return positions, colors


def warped_gamut_blob_points(points, wake=DEFAULT_WAKE, dropoff=DEFAULT_DROPOFF,
                              chroma_wake=DEFAULT_CHROMA_WAKE, chroma_dropoff=DEFAULT_CHROMA_DROPOFF,
                              hue_steps=26, theta_steps=16):
    """The "gamut-following blob" -- a smooth hue_steps x theta_steps grid
    tracing the ACTUAL visible Rec.709/sRGB gamut boundary in OKLCh space,
    spherically embedded (see _warp_and_embed_spherically). Chroma is the
    REAL per-direction max in-gamut value (see _grow_chroma_to_gamut_
    boundary), not a fixed decorative radius -- pinched in around blue/
    violet, bulging around yellow/green, rather than uniformly saturated
    in every direction (see warped_idealized_sphere_points for that
    alternative, validated side by side in a concept mockup -- Tom picked
    this one first ("gamut blob please"), then later switched to the
    sphere for WARPier's own embedded cloud specifically, see
    color_warper_cloud_widget.py). NOT a measurement of any actual image,
    and independent of working colourspace (OKLab's own matrices are
    always anchored to Rec.709/sRGB primaries specifically).

    Reacts live to a WARPier stage's own current warp: `points`/`wake`/
    `dropoff` (the SAME values driving the real grade) are applied via
    warp_hue_chroma BEFORE the spherical embedding, so this shape visibly
    deforms with the operator's actual settings (per Tom: "that graph
    should react to what the warper is doing"). `points=[]` (identity)
    reproduces the plain unwarped blob exactly."""
    L_flat, H_flat = _spherical_grid_lh(hue_steps, theta_steps)
    C_flat = _grow_chroma_to_gamut_boundary(L_flat, H_flat)
    return _warp_and_embed_spherically(L_flat, H_flat, C_flat, points, wake, dropoff,
                                        chroma_wake, chroma_dropoff)


DEFAULT_IDEALIZED_SPHERE_CHROMA = 0.22


def warped_idealized_sphere_points(points, wake=DEFAULT_WAKE, dropoff=DEFAULT_DROPOFF,
                                    chroma_wake=DEFAULT_CHROMA_WAKE, chroma_dropoff=DEFAULT_CHROMA_DROPOFF,
                                    hue_steps=26, theta_steps=16,
                                    chroma=DEFAULT_IDEALIZED_SPHERE_CHROMA):
    """The "idealized uniform sphere" alternative to warped_gamut_blob_
    points -- same hue_steps x theta_steps spherical embedding (see
    _warp_and_embed_spherically), but chroma is a FIXED constant in every
    direction rather than the real per-hue/lightness gamut boundary --
    decorative, uniformly saturated everywhere, not colorimetrically
    accurate. Both options were validated side by side in a concept
    mockup; Tom picked this one for WARPier's own embedded cloud
    specifically ("replace the volume accurate blob with the sphere you
    showed me"), independent of the fixed default chroma=0.22 the mockup
    itself used (kept here for continuity).

    Reacts live to a WARPier stage's own current warp, same as
    warped_gamut_blob_points -- `points=[]` (identity) reproduces a
    plain, perfectly uniform sphere of radius `chroma` exactly."""
    L_flat, H_flat = _spherical_grid_lh(hue_steps, theta_steps)
    C_flat = np.full_like(L_flat, chroma)
    return _warp_and_embed_spherically(L_flat, H_flat, C_flat, points, wake, dropoff,
                                        chroma_wake, chroma_dropoff)


def points_are_identity(points):
    """True if every point's dragged (h,c) matches its own anchor
    (h0,c0) -- an exact no-op warp regardless of wake/dropoff, since
    every point's own hue-delta/chroma-scale contribution is zero/one.
    Vacuously true for an empty list (no points -- a freshly-added stage
    starts here, see stack.make_color_warper_stage), same "starts at
    identity" convention as CDL/Grading Primary/Tone."""
    return all(
        abs(p["h"] - p["h0"]) < 1e-9 and abs(p["c"] - p["c0"]) < 1e-9
        for p in points
    )


def _angular_delta(h0, h1):
    """Signed shortest angular distance h1 - h0, wrapped to (-180, 180]."""
    return ((h1 - h0 + 180.0) % 360.0) - 180.0


def falloff_weight(angular_distance, wake=DEFAULT_WAKE, dropoff=DEFAULT_DROPOFF):
    """Super-Gaussian falloff -- see this module's own docstring. Works
    on a scalar or a numpy array of (non-negative or signed, only the
    magnitude matters) angular distances."""
    n = 1.0 + dropoff * 7.0
    sigma = max(wake, 1e-6)
    return np.exp(-0.5 * np.power(np.abs(angular_distance) / sigma, 2.0 * n))


def softness_to_dropoff(softness):
    """This function's own `dropoff` runs the OPPOSITE direction from
    every caller's own "softness"/"feather" knob -- dropoff=1 is a
    HARDER edge (a flatter core, a much sharper cutoff beyond the wake
    radius), dropoff=0 the more gradual plain-Gaussian tail. A caller's
    softness=1 (softest/most gradual edge) should map to dropoff=0, and
    softness=0 (hardest edge) to dropoff=1 -- the inverted, clamped-to-
    [0,1] relationship every matte-producing stage kind in this app
    wants for its own single shared "how hard vs. soft is the edge"
    control.

    Consolidated here (code review) after being reimplemented
    byte-for-byte three times -- keyer.py's own _softness_to_dropoff,
    matte_shape.py's own _softness_to_dropoff, and segmentation.py's own
    _feather_to_dropoff (that one alone already clipping its input, the
    one real difference between the three copies -- every caller already
    clips its own softness/feather to [0,1] before reaching this point
    anyway, so clipping here too is a safe, behavior-preserving no-op for
    all of them, and a defensive floor for any future caller that
    doesn't)."""
    return 1.0 - float(np.clip(softness, 0.0, 1.0))


def warp_hue_chroma(H, C, points, wake=DEFAULT_WAKE, dropoff=DEFAULT_DROPOFF,
                     chroma_wake=DEFAULT_CHROMA_WAKE, chroma_dropoff=DEFAULT_CHROMA_DROPOFF):
    """Vectorized version of the concept mockup's warped() -- H/C are
    numpy arrays of any matching shape (typically an image's (...,) hue/
    chroma planes), `points` a list of {"h0","c0","h","c"} dicts. Returns
    (H_out, C_out), each the same shape as the input. No points ->
    returns H, C unchanged.

    A point's own weight is the PRODUCT of two independent falloff_weight
    calls -- one over angular hue distance (wake/dropoff, as always), one
    over CHROMA distance (chroma_wake/chroma_dropoff, new) between the
    point's own anchor chroma (c0) and a given pixel's C. Per Tom, directly
    confirmed: without this second axis, a point dropped anywhere along a
    hue's own radial spoke (near center or out at the rim, doesn't matter)
    pulls the ENTIRE spoke -- moving a point that's only halfway out still
    warps fully-saturated pixels at that same hue by its own full delta,
    since the old falloff only ever measured hue angle, never how far a
    pixel's OWN chroma sits from where the point was actually dropped.
    chroma_wake's own default (see DEFAULT_CHROMA_WAKE's own comment) is
    wide enough that this second axis is a no-op unless a caller actually
    narrows it -- reusing falloff_weight verbatim (it's already a generic
    "distance -> weight" function, doesn't care whether the distance is
    degrees or chroma units).

    Blends an IMPLICIT identity contribution (hue_delta=0, chroma_scale=1)
    with weight `max(0, 1 - weight_sum)` alongside the real points' own
    weighted contributions, rather than a plain weighted average of just
    the real points. Without this, a single active point degenerates: a
    weighted average of ONE term always equals that term regardless of
    its own weight (`w*x/w == x` for any `w>0`), so a lone point's full
    hue_delta gets applied almost everywhere on the circle -- not just
    near it -- completely ignoring wake/dropoff (confirmed directly:
    a point with wake=20 was still shifting a hue 55 degrees away by its
    own FULL delta). Multiple points used to mask this in practice (each
    additional point's own competing weight diluted the lone-point
    degeneracy), which is why WarpierWheel's old seeded default points
    (three identity anchors) happened to keep the wheel/mesh from
    "spinning" as a rigid whole -- but that was incidental, not a real
    fix, and broke the moment a stage had exactly one real point (per
    Tom). The identity floor here fixes the actual math: at a point's own
    anchor (weight=1) behavior is unchanged (identity floor drops to 0),
    and multiple overlapping/well-distributed points are also unchanged
    (weight_sum already >=1 there) -- only the "sparse coverage" case
    (few or one point, most of the circle far from any of them) gets the
    correction, tapering smoothly to a true no-op instead of inheriting
    whatever the nearest point's own raw delta happens to be."""
    H = np.asarray(H, dtype=np.float64)
    C = np.asarray(C, dtype=np.float64)
    if not points:
        return H, C

    weight_sum = np.zeros_like(H)
    hue_delta_sum = np.zeros_like(H)
    chroma_scale_sum = np.zeros_like(H)
    for p in points:
        h0 = p["h0"]
        c0 = p["c0"]
        dd = _angular_delta(h0, H)
        hue_w = falloff_weight(dd, wake, dropoff)
        chroma_w = falloff_weight(C - c0, chroma_wake, chroma_dropoff)
        w = hue_w * chroma_w
        hue_delta = _angular_delta(h0, p["h"])
        chroma_scale = min(p["c"] / c0, MAX_CHROMA_SCALE) if c0 > CHROMA_SCALE_FLOOR else 1.0
        weight_sum += w
        hue_delta_sum += w * hue_delta
        chroma_scale_sum += w * chroma_scale

    identity_weight = np.maximum(0.0, 1.0 - weight_sum)
    total_weight = weight_sum + identity_weight
    # total_weight == max(weight_sum, 1.0) > 0 always, so no has_influence
    # guard/division-by-zero special case is needed anymore -- identity_weight
    # alone (contributing 0 hue_delta, chroma_scale=1) already produces the
    # exact right no-op result when weight_sum is 0.
    hue_delta_out = hue_delta_sum / total_weight
    chroma_scale_out = (chroma_scale_sum + identity_weight) / total_weight
    H_out = (H + hue_delta_out) % 360.0
    C_out = np.maximum(0.0, C * chroma_scale_out)
    return H_out, C_out


def decode_working_colourspace_to_oklch(image_array, working_colourspace, config_name=None):
    """`image_array`'s values, treated as ENCODED code values in
    `working_colourspace` -> (L, C, H) OKLCh -- the FORWARD half of
    apply_color_warper_in_working_space's own round trip (decode
    (primaries AND encoding) to REFERENCE_LINEAR_SPACE, then to OKLCh),
    pulled out into its own function since keyer.py's own qualifier (see
    that module's docstring) needs exactly this same forward conversion
    but, unlike WARPier's own warp, never needs the REVERSE -- a keyer
    only ever READS color to decide a matte value, it never grades the
    image itself, so there's nothing to convert back. Same OCIO-vs-
    colour-science auto-dispatch every working-colourspace-aware
    operator in this app already uses (see apply_printer_lights_in_
    working_space)."""
    image_array = np.asarray(image_array, dtype=np.float64)
    config_name = config_name or aces_drt.DEFAULT_CONFIG_NAME
    if aces_drt.OCIO_AVAILABLE:
        linear = aces_drt.convert_colourspace(
            image_array, working_colourspace, REFERENCE_LINEAR_SPACE, config_name=config_name)
    else:
        linear = colorspace.convert(image_array, working_colourspace, REFERENCE_LINEAR_SPACE)
    return oklab_to_oklch(linear_srgb_to_oklab(linear))


def apply_color_warper_in_working_space(image_array, working_colourspace, points,
                                         wake=DEFAULT_WAKE, dropoff=DEFAULT_DROPOFF,
                                         chroma_wake=DEFAULT_CHROMA_WAKE, chroma_dropoff=DEFAULT_CHROMA_DROPOFF,
                                         config_name=None):
    """Apply WARPier's warp to `image_array`, treating its values as
    ENCODED code values in `working_colourspace` -- converts (primaries
    AND encoding) to REFERENCE_LINEAR_SPACE, does the OKLCh warp there,
    converts back. No points, or every point at its own identity
    (points_are_identity), is an exact no-op -- skips the round trip
    entirely rather than relying on the warp math alone to cancel out,
    since the color space round trip itself isn't bit-exact (see
    test_color_warper.py)."""
    image_array = np.asarray(image_array, dtype=np.float64)
    if not points or points_are_identity(points):
        return image_array

    config_name = config_name or aces_drt.DEFAULT_CONFIG_NAME
    L, C, H = decode_working_colourspace_to_oklch(image_array, working_colourspace, config_name)
    H, C = warp_hue_chroma(H, C, points, wake, dropoff, chroma_wake, chroma_dropoff)
    warped_linear = oklab_to_linear_srgb(oklch_to_oklab(L, C, H))

    if aces_drt.OCIO_AVAILABLE:
        return aces_drt.convert_colourspace(
            warped_linear, REFERENCE_LINEAR_SPACE, working_colourspace, config_name=config_name)
    return colorspace.convert(warped_linear, REFERENCE_LINEAR_SPACE, working_colourspace)
