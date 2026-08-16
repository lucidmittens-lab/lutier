"""
keyer.py -- Qt-free backend for the "Keyer" stage kind: a simple HSL
qualifier that produces a MATTE (an (H,W) float array in [0,1]), not a
graded image. This is the first stage kind to feed LUTier's new matte-
channel system (see stack.py's own docstring, "MATTE CHANNELS") -- a
keyer never touches the running RGB image itself; downstream stages
optionally opt into gating their own opacity by a keyer's matte, via
stack_panel.py's per-row matte dropdown.

QUALIFIER SHAPE: three independent terms, multiplied together (a pixel
must satisfy hue AND saturation AND luminance to be fully keyed, the
usual qualifier AND-logic):
- hue: a center + width (degrees) super-Gaussian falloff, reusing
  color_warper.falloff_weight (the exact same "wake/dropoff" shape
  WARPier's own point falloff uses) around the angular distance from
  hue_center.
- saturation / luminance: each a [min, max] BAND -- full weight for any
  value inside the band, the same falloff_weight shape outside it, at an
  edge width derived from `softness` (kept to one shared knob rather than
  an independent width per channel, per Tom's own framing of this as "a
  simple keyer").

`softness` (0..1) is the one shared control for how hard vs. soft every
edge in the qualifier is -- 0 is closer to a hard cutoff, 1 the softest.
`invert` flips the final matte (1-matte) -- the standard "key everything
ELSE" toggle.

No working-colourspace-aware gamut conversion here for v1 -- operates
directly on the stack's own running RGB, same simplification
grading_primary.py's own math already makes. Input is clipped to [0,1]
before HSL conversion, treating any over-range (linear/log working-space)
values as clipped-white for qualifier purposes -- a documented v1
simplification, not a precision requirement this operator needs to meet.

Same "no PySide6 import" rule as every other backend module in this app.
"""
import numpy as np

import color_warper as cw

DEFAULT_HUE_CENTER = 0.0
DEFAULT_HUE_WIDTH = 30.0
DEFAULT_SAT_MIN = 0.0
DEFAULT_SAT_MAX = 1.0
DEFAULT_LUM_MIN = 0.0
DEFAULT_LUM_MAX = 1.0
DEFAULT_SOFTNESS = 0.3
DEFAULT_INVERT = False


def _rgb_to_hsl(rgb):
    """(...,3) RGB in [0,1] -> (h, s, l), h in degrees [0,360), s/l in
    [0,1] -- a direct vectorization of the stdlib colorsys.rgb_to_hls
    algorithm (same formula, just applied to a whole array at once
    instead of one pixel at a time), so its well-known edge-case
    behavior (grays -> h=0,s=0) carries over unchanged."""
    rgb = np.clip(np.asarray(rgb, dtype=np.float64), 0.0, 1.0)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    maxc = np.max(rgb, axis=-1)
    minc = np.min(rgb, axis=-1)
    delta = maxc - minc
    nonzero = delta > 1e-9

    l = (maxc + minc) / 2.0

    denom = np.where(l <= 0.5, maxc + minc, 2.0 - maxc - minc)
    denom_safe = np.where(denom > 1e-9, denom, 1.0)
    s = np.where(nonzero, delta / denom_safe, 0.0)

    delta_safe = np.where(nonzero, delta, 1.0)
    rc = (maxc - r) / delta_safe
    gc = (maxc - g) / delta_safe
    bc = (maxc - b) / delta_safe
    h_raw = np.select([r == maxc, g == maxc, b == maxc], [bc - gc, 2.0 + rc - bc, 4.0 + gc - rc],
                       default=0.0)
    h = np.where(nonzero, ((h_raw / 6.0) % 1.0) * 360.0, 0.0)
    return h, s, l


def _signed_angular_delta(h0, h1):
    """Shortest signed distance from h0 to h1 in degrees, wrapped to
    (-180, 180] -- same convention as color_warper._angular_delta, kept
    as its own tiny local copy rather than importing that module's
    private helper."""
    return ((h1 - h0 + 180.0) % 360.0) - 180.0


def _softness_to_dropoff(softness):
    """color_warper.falloff_weight's own `dropoff` runs the OPPOSITE
    direction from this module's `softness` -- dropoff=1 is a harder
    plateau (flat near the center, a SHARPER cutoff beyond the wake
    radius) and dropoff=0 a plain Gaussian (the more gradual tail).
    softness=1 should read as the softest/most gradual edge, so it maps
    to dropoff=0, and softness=0 (hardest edge) maps to dropoff=1."""
    return 1.0 - softness


def _band_term(value, lo, hi, softness):
    """1.0 for any `value` inside [lo, hi], falling off outside it via
    color_warper.falloff_weight's own super-Gaussian shape. `softness`
    (0..1) sets both how wide the falloff zone outside the band is
    (scaled into this channel's own [0,1] units -- 0.03 to 0.28 of the
    channel's full range) AND that shape's own gradualness (see
    _softness_to_dropoff) -- harder edges at softness=0, softer at
    softness=1."""
    edge_width = 0.03 + 0.25 * softness
    distance_outside = np.maximum(0.0, lo - value) + np.maximum(0.0, value - hi)
    return cw.falloff_weight(distance_outside, wake=edge_width, dropoff=_softness_to_dropoff(softness))


def compute_matte(image_array, params):
    """image_array: (H,W,3) RGB (any working space, treated as [0,1]
    display-referred for qualifier purposes -- see module docstring).
    params: a plain dict, see the DEFAULT_* constants above for keys and
    defaults (every key is optional, missing ones fall back to that
    default -- same `params.get(key, DEFAULT_X)` convention every other
    stage kind's own apply function already uses). Returns an (H,W)
    float array in [0,1]."""
    h, s, l = _rgb_to_hsl(image_array)

    hue_center = float(params.get("hue_center", DEFAULT_HUE_CENTER))
    hue_width = max(1e-3, float(params.get("hue_width", DEFAULT_HUE_WIDTH)))
    sat_min = float(params.get("sat_min", DEFAULT_SAT_MIN))
    sat_max = float(params.get("sat_max", DEFAULT_SAT_MAX))
    lum_min = float(params.get("lum_min", DEFAULT_LUM_MIN))
    lum_max = float(params.get("lum_max", DEFAULT_LUM_MAX))
    softness = float(np.clip(params.get("softness", DEFAULT_SOFTNESS), 0.0, 1.0))
    invert = bool(params.get("invert", DEFAULT_INVERT))

    hue_term = cw.falloff_weight(_signed_angular_delta(hue_center, h), wake=hue_width,
                                  dropoff=_softness_to_dropoff(softness))
    sat_term = _band_term(s, sat_min, sat_max, softness)
    lum_term = _band_term(l, lum_min, lum_max, softness)

    matte = hue_term * sat_term * lum_term
    if invert:
        matte = 1.0 - matte
    return np.clip(matte, 0.0, 1.0)
