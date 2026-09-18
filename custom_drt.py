"""
custom_drt.py -- Qt-free backend for LUTier's own display rendering
transform, "CAMier" (Tom's name for it): an alternative to aces_drt.py's
ACES 1.3/2.0 Output Transform,
designed from scratch (Tom, 2026-08-12 through 2026-08-13) rather than
ported from ACES, OpenDRT, or AgX -- see that design conversation for why:
OpenDRT is GPLv3 (would force this whole app to GPL), and AgX's own
licensing turned out to be unverifiable (no LICENSE file anywhere along
its actual chain of custody, which is legally MORE restrictive than GPL
for reuse, not less). Every constant and formula below was derived and
tuned in this app's own design process, validated against LUTier's bundled
default sample image, not copied from any of those projects' source.

PIPELINE (four stages, each independently swappable in principle, fixed
together here for the MVP):

1. RENDERING SPACE -- Linear Rec.2020, genuinely unbounded (no hard clip).
   Real (non-imaginary) primaries, wide enough to hold any camera/display
   gamut without clipping, without AP1's ultra-wide-primary hue problems.
2. TONESCALE -- a hyperbolic compression (same family ACES2/OpenDRT use)
   for the bulk of the range, blended into a separate sigmoid-shaped
   taper above an adjustable percentile of its own output range (the
   "top 1%" by default) -- pure hyperbolic crushes extreme highlights
   into a very narrow band very quickly (2 stops headroom is aggressive
   by design, for punch), which reads as "clipped" even though it's not
   literally a hard clip; the sigmoid tail deliberately widens that top
   region back out. Applied to the pixel's own norm (max(R,G,B)) and the
   RGB ratios rescaled by the same factor -- hue-preserving by
   construction, confirmed visually against a per-channel alternative
   (per-channel independently compresses each channel toward its own
   ceiling at a different rate, which is exactly what causes the classic
   "bright reds go orange" complaint).
3. HIGHLIGHT DESATURATION -- a separate, deliberate blend toward each
   pixel's own achromatic equivalent as its tonescale output climbs
   toward white. Not optional/free from step 2: a hue-preserving
   tonescale keeps full saturation all the way to peak brightness, which
   looks unnaturally neon/plastic rather than the graceful roll-to-white
   real light sources (and every production DRT) exhibit. This is the
   dedicated fix for that, independent of gamut compression below.
4. GAMUT COMPRESSION -- Rec.2020 (from step 2/3's output, still using
   Rec.2020 primaries) down to the chosen MASTERING DISPLAY's own gamut
   (see _MASTERING_GAMUT_MATRIX below -- Rec.709 for "SDR", P3-D65 for
   both "HDR" and "Theatrical"). A per-pixel RADIAL compression along the
   ray from that pixel's own achromatic anchor (same brightness, zero
   saturation) through its raw mastering-gamut-converted value: untouched
   for the inner `identity_zone` fraction of the distance to the TRUE
   mastering gamut boundary along that exact ray, then a
   smoothstep-blended remap of the rest of that ray (all the way out to
   the TRUE Rec.2020 boundary) into the remaining outer fraction --
   calibrated so the farthest any Rec.2020-native color could ever be
   pushed lands EXACTLY on the mastering gamut's edge, never beyond it,
   while colors already safely inside are left completely alone.
   `fullness` (per-channel) optionally blends the compressed result
   further toward that same true edge -- capped there by construction,
   so it can never push a color back out of gamut, unlike a blind
   saturation boost. Red needs more fullness than green/blue in practice
   for a Rec.709 mastering gamut: converting a saturated Rec.2020 red
   into Rec.709 pushes ITS OWN channel over 1.0 (a ceiling that gets
   tighter as brightness climbs, since there's less room left below
   white), while green/blue are typically constrained by a channel going
   negative (a fixed proportion of the ray regardless of brightness) --
   confirmed by direct measurement across a brightness sweep, see this
   module's own tests.

MASTERING DISPLAY -> FINAL TARGET (Tom, 2026-08-13 "good hygiene" pass)
-- the final two steps, replacing an earlier, simpler
target_gamut/output_encoding pair of independent pickers with the real
two-stage model production HDR pipelines actually use:

  - MASTERING DISPLAY (`mastering_display`, one of MASTERING_DISPLAYS):
    fixes BOTH the gamut that step 4's radial compression targets AND
    that display's own reference EOTF, as a single bundled choice --
    "SDR" (Rec.709 primaries, gamma 2.4), "HDR" (P3-D65 primaries, PQ),
    "Theatrical" (P3-D65 primaries, gamma 2.6). Gamma 2.4/2.6 here mean a
    simple power law (`V = clip(L,0,1)**(1/gamma)`), not the fuller
    Rec.1886 EOTF with its black-level offset term -- Tom's explicit
    choice, simplicity over that extra term's real-world relevance for a
    synthetic mastering reference.
  - MASTERING DISPLAY BRIGHTNESS (`mastering_brightness`, nits, HDR
    only): ties the pipeline's own normalized [0,1] "black to this
    master's own peak white" tonescale output to a real absolute-nits
    value before PQ's own fixed (always-10,000-nits-referenced) formula
    ever runs -- see _pq_encode's own docstring. Independent of
    `headroom` (which shapes the tonescale's own highlight rolloff
    shape, not absolute brightness).
  - FINAL TARGET (`final_target`, a key into FINAL_TARGETS[mastering_display]):
    a direct conversion from the mastering render to the actual delivery
    space -- Tom: "Direct conversion from mastering display to target,
    no gamut mapping should be necessary" -- deliberately NOT another
    radial ray-box compression like step 4, just a primaries matrix (with
    white-point adaptation baked in where the two gamuts don't share a
    white point, e.g. Theatrical's D65-referenced P3-D65 mastering ->
    DCI-white-referenced DCI-P3/XYZ) applied directly to the
    already-in-gamut, still-LINEAR step-4 output, immediately followed by
    ONE EOTF encode (that Final Target's own, from FINAL_TARGETS) with no
    intermediate encode/decode round trip -- encoding then immediately
    decoding through the SAME curve is lossless, so there is nothing to
    gain from doing it, and every mastering display's own "passthrough"
    Final Target option (e.g. SDR's "Rec.709 (Gamma 2.4)") is exactly
    that display's own definition with an identity matrix, by
    construction. Same-family only for now (e.g. no HDR->SDR trim) --
    Tom, 2026-08-13: that would need real gamut compression (P3-D65 can
    hold colors Rec.709 can't), a genuinely different kind of step,
    deliberately out of scope here.

GPU: gpu_custom_drt_shader.py mirrors every formula below in GLSL,
verified bit-exact -- see that module's own docstring.

Same rule as every other backend module in this app: no PySide6 import
here, fully testable without a display.
"""
import numpy as np

import aces_drt
import colorspace_convert

DEFAULT_CONFIG_NAME = aces_drt.DEFAULT_CONFIG_NAME

# Settled defaults from the design/validation pass against the bundled
# sample image (see this module's docstring) -- NOT arbitrary starting
# points, these are what Tom actually approved.
DEFAULT_CONTRAST = 0.65
DEFAULT_HEADROOM = 1.0
DEFAULT_BLEND_PERCENTILE = 0.899
DEFAULT_TAIL_WIDTH = 2.0
DEFAULT_DESAT_START = 0.75
DEFAULT_DESAT_END = 1.0
DEFAULT_IDENTITY_ZONE = 0.83
DEFAULT_TRANSITION_SOFTNESS = 0.17

# Hard safety bounds enforced inside apply_custom_drt itself (see that
# function's own clamping, near the top) -- a real, previously-shipped
# bug (found in code review): only the live UI spinbox (stack_panel.py,
# range [0.5, 0.999] for blend_percentile) ever constrained these values;
# nothing in this module's own math did. A hand-edited or future-
# imported session with blend_percentile <= 0 makes _hyperbolic_inverse_
# stops evaluate a fractional power of a non-positive base (NaN), or
# >= 1.0 divide by exactly zero (inf) -- either way silently render the
# entire stage's output as garbage with no error surfaced anywhere.
# _BLEND_PERCENTILE_EPSILON keeps the clamp strictly inside the open
# interval (0, 1) that math needs, well below the UI's own already-more-
# conservative [0.5, 0.999] floor, so this never visibly affects normal
# UI usage -- it only catches a value the UI itself could never produce.
_BLEND_PERCENTILE_EPSILON = 1e-3
DEFAULT_FULLNESS_R = 0.10
DEFAULT_FULLNESS_G = 0.0
DEFAULT_FULLNESS_B = 0.04
DEFAULT_MASTERING_BRIGHTNESS = 100

_GREY = 0.18  # 18% grey anchor, same convention the tonescale-design widgets used

MASTERING_DISPLAYS = ("SDR", "HDR", "Theatrical")
DEFAULT_MASTERING_DISPLAY = "SDR"

# Rec.2020 -> mastering-display-gamut linear RGB matrices (each verified
# via colour.matrix_RGB_to_RGB(colour.RGB_COLOURSPACES['ITU-R BT.2020'],
# colour.RGB_COLOURSPACES[name]) at generation time). Rec.709 and P3-D65
# both share Rec.2020's own D65 whitepoint, so these are pure primaries
# changes, no chromatic adaptation. Every row sums to 1 (confirmed for
# each), so a grey input maps to the identical grey output regardless of
# target -- what lets the gamut-compression anchor in _radial_compress be
# reused unchanged across either of them. "HDR" and "Theatrical" share
# the same P3-D65 mastering gamut (they differ only in Final Target
# options and EOTF), per Tom, 2026-08-13.
_REC2020_TO_REC709 = np.array([
    [1.660491, -0.587641, -0.072850],
    [-0.124550, 1.132900, -0.008349],
    [-0.018151, -0.100579, 1.118730],
])
_REC2020_TO_P3D65 = np.array([
    [1.343578, -0.282180, -0.061399],
    [-0.065297, 1.075788, -0.010490],
    [0.002822, -0.019598, 1.016777],
])
_MASTERING_GAMUT_MATRIX = {
    "SDR": _REC2020_TO_REC709,
    "HDR": _REC2020_TO_P3D65,
    "Theatrical": _REC2020_TO_P3D65,
}

# Final Target matrices, each relative to its own mastering display's
# gamut (NOT Rec.2020 -- see module docstring's "MASTERING DISPLAY ->
# FINAL TARGET" section on why no second radial compression is needed
# here, just a direct primaries matrix). `None` means identity (the
# mastering gamut already IS that Final Target's own gamut). Each
# verified via colour.matrix_RGB_to_RGB (with automatic chromatic
# adaptation where the two whitepoints differ, confirmed by every row
# still summing to 1).
_P3D65_TO_REC2020 = np.array([
    [0.75383303, 0.19859737, 0.0475696],
    [0.04574385, 0.94177722, 0.01247893],
    [-0.00121034, 0.01760172, 0.98360862],
])
_P3D65_TO_XYZ = np.array([
    [0.48657095, 0.26566769, 0.19821729],
    [0.22897456, 0.69173852, 0.07928691],
    [-0.0, 0.04511338, 1.04394437],
])
# DCI whitepoint-adapted (P3-D65 is D65-referenced, DCI-P3 is DCI-white
# (~x=0.314, y=0.351) referenced -- these two bake in real chromatic
# adaptation, unlike the SDR/HDR passthrough matrices above).
_P3D65_TO_DCIP3 = np.array([
    [1.06775799, -0.06998189, 0.0022239],
    [0.00271134, 0.99714975, 0.0001389],
    [-0.0006784, -0.00956984, 1.01024825],
])
_P3D65_TO_XYZ_DCI_WHITE = np.array([
    [0.47596816, 0.24354197, 0.17507677],
    [0.22559615, 0.70421842, 0.07018543],
    [-0.00048796, 0.03824318, 0.91666073],
])

# eotf spec: ("gamma", exponent) | ("srgb", None) | ("pq", None) -- see
# _gamma_encode/_srgb_encode/_pq_encode. Each mastering display's own
# "passthrough" entry (identity matrix, that display's own reference
# EOTF) is listed first and is that display's DEFAULT_FINAL_TARGET.
FINAL_TARGETS = {
    "SDR": {
        "Rec.709 (Gamma 2.4)": {"matrix": None, "eotf": ("gamma", 2.4)},
        "sRGB": {"matrix": None, "eotf": ("srgb", None)},
    },
    "HDR": {
        "P3-D65": {"matrix": None, "eotf": ("pq", None)},
        "Rec.2020": {"matrix": _P3D65_TO_REC2020, "eotf": ("pq", None)},
        "XYZ": {"matrix": _P3D65_TO_XYZ, "eotf": ("pq", None)},
    },
    "Theatrical": {
        "DCI-P3": {"matrix": _P3D65_TO_DCIP3, "eotf": ("gamma", 2.6)},
        "XYZ": {"matrix": _P3D65_TO_XYZ_DCI_WHITE, "eotf": ("gamma", 2.6)},
    },
}

DEFAULT_FINAL_TARGET_FOR_DISPLAY = {
    "SDR": "Rec.709 (Gamma 2.4)",
    "HDR": "P3-D65",
    "Theatrical": "DCI-P3",
}

# A fixed picklist (Tom, 2026-08-13: "mastering brightness will be a
# fixed option"), not a free numeric slider -- these are the standard
# reference peaks real HDR mastering/delivery pipelines actually use
# (10000 = PQ's own theoretical ceiling; 1000/2000 = common HDR grading
# monitor peaks; 400/200/100 = SDR-adjacent/transitional references).
MASTERING_BRIGHTNESS_OPTIONS = (100, 200, 400, 1000, 2000, 10000)

# The real SMPTE/DCI theatrical reference projection level (~14 fL, ~48
# cd/m^2) -- Tom, 2026-08-13: shown for context ("more true
# contextually"), NOT wired into the render math. Theatrical's own EOTF
# is a plain gamma 2.6 power law (see FINAL_TARGETS above), which never
# consumes mastering_brightness at all -- only the "pq" eotf branch does
# (see apply_custom_drt's own dispatch) -- so this constant exists purely
# for the UI to display something real instead of a stale HDR-picklist
# leftover value when Theatrical is selected.
DEFAULT_THEATRICAL_REFERENCE_BRIGHTNESS = 48


def list_working_colourspaces(config_name=DEFAULT_CONFIG_NAME):
    """Same registry aces_drt.py's own working-colourspace combo uses --
    see that function's docstring."""
    return aces_drt.list_working_colourspaces(config_name)


def _to_stops(x):
    return np.log2(np.maximum(x, 1e-8) / _GREY)


def _hyperbolic_tonescale(stops, contrast, headroom):
    x = _GREY * np.power(2.0, stops)
    s = _GREY * np.power(2.0, headroom)
    p = 0.7 + contrast * 1.6
    v = x / (x + s)
    return np.power(np.maximum(v, 0.0), p)


def _hyperbolic_inverse_stops(y, contrast, headroom):
    """The stops value at which _hyperbolic_tonescale first reaches
    output `y` -- used to anchor the sigmoid tail exactly where the
    hyperbolic curve was cut off, so the blend is continuous in value."""
    s = _GREY * np.power(2.0, headroom)
    p = 0.7 + contrast * 1.6
    v = np.power(y, 1.0 / p)
    x = s * v / (1.0 - v)
    return np.log2(x / _GREY)


def _sigmoid_tail(stops, s0, y0, width):
    """Half-sigmoid taper anchored at (s0, y0), asymptoting to 1.0 as
    stops -> inf -- continuous with the hyperbolic curve at s0 by
    construction (t=0 there gives exactly y0)."""
    t = (stops - s0) / width
    return y0 + (1.0 - y0) * (2.0 / (1.0 + np.exp(-t)) - 1.0)


def _hybrid_tonescale(stops, contrast, headroom, blend_percentile, tail_width):
    hyp = _hyperbolic_tonescale(stops, contrast, headroom)
    s0 = _hyperbolic_inverse_stops(blend_percentile, contrast, headroom)
    tail = _sigmoid_tail(stops, s0, blend_percentile, tail_width)
    return np.where(hyp < blend_percentile, hyp, tail)


def _tonemap_and_desaturate(rgb, contrast, headroom, blend_percentile, tail_width,
                             desat_start, desat_end):
    norm = np.max(rgb, axis=-1, keepdims=True)
    norm_safe = np.maximum(norm, 1e-8)
    out_norm = _hybrid_tonescale(
        _to_stops(norm_safe), contrast, headroom, blend_percentile, tail_width)
    toned = rgb * (out_norm / norm_safe)

    # Highlight desaturation -- blend toward the achromatic equivalent AT
    # THE SAME output brightness as out_norm climbs from desat_start to
    # desat_end (pure white). See module docstring, stage 3.
    span = max(desat_end - desat_start, 1e-6)
    t = np.clip((out_norm - desat_start) / span, 0.0, 1.0)
    desat_amount = t * t * (3 - 2 * t)  # smoothstep
    grey = np.repeat(out_norm, 3, axis=-1)
    return toned * (1 - desat_amount) + grey * desat_amount


def _ray_box_t(anchor, direction):
    """Smallest positive t such that anchor + t*direction reaches the
    [0,1]^3 box boundary. Assumes anchor is inside the box. +inf for a
    direction that never exits (e.g. a perfectly neutral pixel, where
    direction is exactly zero in every channel) -- callers must not do
    arithmetic that combines two of these without guarding for that, see
    _radial_compress.

    `eps` is RELATIVE to the pixel's own brightness (anchor), not a fixed
    absolute value -- a real, reproduced bug (Tom, 2026-08-13): a fixed
    1e-9 absolute threshold is far tighter than the actual floating-point
    noise floor between two INDEPENDENT implementations of the same
    logical computation (this CPU path's numpy/colour-science ACEScct
    decode vs gpu_custom_drt_shader.py's OCIO-generated GLSL decode) --
    confirmed directly: a genuinely neutral input pixel decoded to
    [0.02722906, 0.02722905, 0.02722906], an 1e-8 absolute (1e-6
    relative) difference comfortably OUTSIDE a 1e-9 absolute epsilon, so
    it got treated as a real, non-neutral direction -- and dividing by a
    near-but-not-quite-zero direction is a near-singularity: CPU and GPU's
    own independent, differently-signed/-sized noise at that scale then
    amplifies into a wildly different (not just slightly different)
    result, since each side resolves a different, unstable "boundary
    crossing" for what should be the same neutral ray. 1e-4 RELATIVE
    (0.01% of the pixel's own brightness) sits comfortably above both
    float32's own ~1e-7 relative precision floor and the actual observed
    noise, while staying far below any real, creatively meaningful color
    difference -- so a pixel this close to neutral is now correctly
    treated as neutral on both sides, not as an unstable near-miss."""
    eps = np.maximum(np.abs(anchor), 1e-6) * 1e-4
    pos = direction > eps
    neg = direction < -eps
    t_pos = np.where(pos, (1.0 - anchor) / np.where(pos, direction, 1.0), np.inf)
    t_neg = np.where(neg, (0.0 - anchor) / np.where(neg, direction, -1.0), np.inf)
    return np.min(np.minimum(t_pos, t_neg), axis=-1)


def _radial_compress(rgb2020, target_matrix, identity_zone, transition_softness, fullness_rgb):
    N = np.max(rgb2020, axis=-1, keepdims=True)
    anchor = np.repeat(N, 3, axis=-1)
    dir2020 = rgb2020 - anchor
    # np.einsum, NOT rgb2020 @ target_matrix.T -- confirmed via a real
    # crash (Tom, 2026-08-13): `@`/matmul on a (H,W,3)-shaped live-preview
    # array dispatches into macOS's Accelerate BLAS, which segfaulted
    # deep inside libBLAS.dylib during a live slider drag (EXC_BAD_ACCESS,
    # SIGSEGV -- a crash report, not a Python exception, so no traceback
    # to catch). clf_adapter.py's apply_clf_node hit the identical crash
    # class for the same (3,3)-matrix-times-(...,3)-array shape and
    # already settled on einsum as the fix -- matching that convention
    # here rather than rediscovering it independently.
    rgb_target_raw = np.einsum("ij,...j->...i", target_matrix, rgb2020)
    dir_target = rgb_target_raw - anchor

    t2020 = _ray_box_t(anchor, dir2020)
    tTarget = _ray_box_t(anchor, dir_target)

    # A perfectly (or near-perfectly) neutral pixel never leaves the
    # gamut, so _ray_box_t correctly returns +inf -- but inf - inf
    # downstream is NaN, not 0, in IEEE float math. Swap in a finite
    # stand-in for the arithmetic, then force identity (tOut=1) for those
    # pixels at the end, which is the actually-correct answer regardless
    # of the stand-in's value.
    finite_target = np.isfinite(tTarget)
    tTarget_safe = np.where(finite_target, tTarget, 1.0e6)
    t2020_safe = np.where(np.isfinite(t2020), t2020, 1.0e6)

    split = tTarget_safe * identity_zone
    w = tTarget_safe * transition_softness
    lo = split - w / 2.0
    hi = split + w / 2.0
    denom = np.maximum(t2020_safe - split, 1e-6)
    compress_at_1 = split + (1.0 - split) / denom * (tTarget_safe - split)

    # Proper if/elif/else via nested np.where -- each pixel takes EXACTLY
    # one branch. A single flat `np.where(hi <= lo, 1.0, weight)` applied
    # as a separate line AFTER this (an earlier version of this function
    # had exactly that) is wrong: transition_softness=0 makes hi == lo
    # for every pixel, including ones already correctly routed to the
    # identity branch (1.0 <= lo), and that flat override clobbers them
    # back to full compression. Nesting means the smoothstep branch is
    # only ever reached when lo < 1 < hi, which already guarantees
    # hi > lo -- so no separate degenerate-span guard is needed here at
    # all, `span`'s own 1e-9 floor above is enough to keep the smoothstep
    # branch's division finite on the rare pixel that lands exactly on
    # its own lo/hi boundary.
    span = np.maximum(hi - lo, 1e-9)
    x = np.clip((1.0 - lo) / span, 0.0, 1.0)
    smoothstep = x * x * (3 - 2 * x)
    weight = np.where(1.0 <= lo, 0.0, np.where(1.0 >= hi, 1.0, smoothstep))
    t_out = (1 - weight) + compress_at_1 * weight
    t_out = np.where(finite_target, t_out, 1.0)

    fullness = np.asarray(fullness_rgb, dtype=np.float64).reshape((1,) * (rgb2020.ndim - 1) + (3,))
    t_out_per_channel = t_out[..., None] + fullness * (tTarget_safe[..., None] - t_out[..., None])
    t_out_per_channel = np.where(finite_target[..., None], t_out_per_channel, 1.0)
    return anchor + t_out_per_channel * dir_target


def _srgb_encode(v):
    v = np.clip(v, 0.0, 1.0)
    return np.where(v <= 0.0031308, v * 12.92, 1.055 * np.power(v, 1 / 2.4) - 0.055)


def _gamma_encode(v, gamma):
    """Simple power-law EOTF (Tom, 2026-08-13: "simple power law", not
    the fuller Rec.1886 EOTF with its black-level offset term) -- used
    for both SDR's plain "Rec.709 (Gamma 2.4)" Final Target and
    Theatrical's gamma 2.6 (same curve for both DCI-P3 and XYZ Final
    Targets, Tom's explicit choice over a separate SMPTE 428 XYZ-specific
    curve)."""
    return np.power(np.clip(v, 0.0, 1.0), 1.0 / gamma)


# SMPTE ST 2084 (PQ) OETF constants -- verified directly against the
# standard's own published reference points (100 nits -> code ~0.5081,
# 203 nits -> code ~0.5807, 10000 nits -> code 1.0 exactly) before this
# module ever shipped with them.
_PQ_M1 = 2610.0 / 16384.0
_PQ_M2 = 2523.0 / 4096.0 * 128.0
_PQ_C1 = 3424.0 / 4096.0
_PQ_C2 = 2413.0 / 4096.0 * 32.0
_PQ_C3 = 2392.0 / 4096.0 * 32.0


def _pq_encode(v, mastering_brightness):
    """ST 2084 PQ OETF -- an ABSOLUTE-luminance code: 1.0 always means
    exactly 10,000 nits, no matter what's being delivered, unlike sRGB
    gamma's purely relative 0-1. `v` (this pipeline's own normalized
    [0,1] "black to this master's peak white" tonescale output) is first
    scaled to real nits via `mastering_brightness`, THEN divided by
    10,000 to land in PQ's own fixed reference frame -- see this module's
    docstring on why mastering_brightness is what actually ties those two
    together, not `headroom`."""
    v = np.clip(v, 0.0, 1.0)
    absolute_nits = v * mastering_brightness
    L = absolute_nits / 10000.0
    Lm = np.power(L, _PQ_M1)
    return np.power((_PQ_C1 + _PQ_C2 * Lm) / (1.0 + _PQ_C3 * Lm), _PQ_M2)


def apply_custom_drt(image_array, working_colourspace,
                      contrast=DEFAULT_CONTRAST, headroom=DEFAULT_HEADROOM,
                      blend_percentile=DEFAULT_BLEND_PERCENTILE, tail_width=DEFAULT_TAIL_WIDTH,
                      desat_start=DEFAULT_DESAT_START, desat_end=DEFAULT_DESAT_END,
                      identity_zone=DEFAULT_IDENTITY_ZONE,
                      transition_softness=DEFAULT_TRANSITION_SOFTNESS,
                      fullness_r=DEFAULT_FULLNESS_R, fullness_g=DEFAULT_FULLNESS_G,
                      fullness_b=DEFAULT_FULLNESS_B,
                      mastering_display=DEFAULT_MASTERING_DISPLAY,
                      final_target=None,
                      mastering_brightness=DEFAULT_MASTERING_BRIGHTNESS,
                      config_name=DEFAULT_CONFIG_NAME):
    """Render `image_array` (any (...,3)-shaped float array, in
    `working_colourspace`'s own encoding) through LUTier's own DRT --
    see this module's docstring for the four rendering stages plus the
    "MASTERING DISPLAY -> FINAL TARGET" section for what
    `mastering_display`/`final_target`/`mastering_brightness` actually
    mean. `final_target=None` (the default) means that
    `mastering_display`'s own passthrough option, from
    DEFAULT_FINAL_TARGET_FOR_DISPLAY. `image_array` may be unbounded (HDR
    scene-linear values well over 1.0 are expected and normal going in);
    the result is always in [0,1] either way (gamma/sRGB relative, PQ
    absolute-luminance-coded)."""
    if mastering_display not in MASTERING_DISPLAYS:
        raise ValueError(
            f"Unknown mastering_display {mastering_display!r} "
            f"(expected one of {MASTERING_DISPLAYS})")
    if final_target is None:
        final_target = DEFAULT_FINAL_TARGET_FOR_DISPLAY[mastering_display]
    final_target_options = FINAL_TARGETS[mastering_display]
    if final_target not in final_target_options:
        raise ValueError(
            f"Unknown final_target {final_target!r} for mastering_display "
            f"{mastering_display!r} (expected one of {tuple(final_target_options)})")

    # Clamp every param the live UI spinboxes already constrain (see
    # _BLEND_PERCENTILE_EPSILON's own comment) -- this is the ONLY
    # enforcement point for a caller that skips the UI entirely (a
    # hand-edited or future-imported session file, a script, a test).
    # blend_percentile specifically MUST stay inside the open interval
    # (0, 1) or _hyperbolic_inverse_stops produces NaN/inf; the rest just
    # match the UI's own already-safe [0.0, 1.0] range for consistency.
    blend_percentile = min(max(float(blend_percentile), _BLEND_PERCENTILE_EPSILON),
                            1.0 - _BLEND_PERCENTILE_EPSILON)
    desat_start = min(max(float(desat_start), 0.0), 1.0)
    desat_end = min(max(float(desat_end), 0.0), 1.0)
    identity_zone = min(max(float(identity_zone), 0.0), 1.0)
    transition_softness = min(max(float(transition_softness), 0.0), 1.0)

    rgb2020 = colorspace_convert.apply_colorspace_conversion(
        np.asarray(image_array, dtype=np.float64), working_colourspace,
        "Linear Rec.2020", config_name=config_name)
    toned = _tonemap_and_desaturate(
        rgb2020, contrast, headroom, blend_percentile, tail_width, desat_start, desat_end)
    compressed = _radial_compress(
        toned, _MASTERING_GAMUT_MATRIX[mastering_display], identity_zone, transition_softness,
        (fullness_r, fullness_g, fullness_b))

    spec = final_target_options[final_target]
    if spec["matrix"] is not None:
        # Still-linear -> still-linear direct primaries matrix, no
        # decode/re-encode round trip -- see module docstring.
        final_linear = np.einsum("ij,...j->...i", spec["matrix"], compressed)
    else:
        final_linear = compressed

    eotf_kind, eotf_param = spec["eotf"]
    if eotf_kind == "pq":
        return _pq_encode(final_linear, mastering_brightness)
    if eotf_kind == "srgb":
        return _srgb_encode(final_linear)
    return _gamma_encode(final_linear, eotf_param)
