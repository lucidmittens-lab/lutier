"""
keyer.py -- Qt-free backend for the "Keyer" stage kind: a simple
qualifier that produces a MATTE (an (H,W) float array in [0,1]), not a
graded image. This is the first stage kind to feed LUTier's matte-
channel system (see stack.py's own docstring, "MATTE CHANNELS") -- a
keyer never touches the running RGB image itself; downstream stages
optionally opt into gating their own opacity by a keyer's matte, via
stack_panel.py's per-row matte dropdown.

OKLCh, WORKING-COLOURSPACE-AWARE (per Tom: "would it be stronger if we
can do this in oklab oklch or whatever with a working color space
selection?" -- REPLACING an earlier v1 that ran plain HSL directly on
whatever values happened to be in the array, clipped to [0,1], with no
color-space awareness at all): on this app's own log/HDR working spaces
(WARPier itself defaults to ACEScct), that earlier approach was
genuinely wrong, not just less pretty -- hue relationships distort under
log encoding, and legitimate out-of-[0,1] values got silently clipped
instead of properly decoded. This module now reuses
color_warper.decode_working_colourspace_to_oklch (the exact same,
already-proven "decode working_colourspace -> linear -> gamut-convert to
REFERENCE_LINEAR_SPACE -> OKLCh" pipeline WARPier's own warp uses) to
get real, perceptually uniform L/C/H values before qualifying on them --
a keyer only ever needs this FORWARD half, never WARPier's own reverse
(OKLCh -> linear -> working_colourspace), since a keyer never grades the
image, only reads it to decide a matte value.

QUALIFIER SHAPE: three independent terms, multiplied together (a pixel
must satisfy hue AND chroma AND lightness to be fully keyed, the usual
qualifier AND-logic):
- hue: a center + width (degrees) super-Gaussian falloff, reusing
  color_warper.falloff_weight (the exact same "wake/dropoff" shape
  WARPier's own point falloff uses) around the angular distance from
  hue_center -- OKLCh hue, the SAME hue convention WarpierWheel's own
  spider-web already uses, so a value picked in one operator is
  meaningful relative to the other.
- chroma / lightness: each a [min, max] BAND -- full weight for any
  value inside the band, the same falloff_weight shape outside it, at an
  edge width derived from `softness` (kept to one shared knob rather than
  an independent width per channel, per Tom's own framing of this as "a
  simple keyer"). Chroma's own natural range is NOT [0,1] the way HSL
  saturation was -- see DEFAULT_CHROMA_MAX's own comment.

`softness` (0..1) is the one shared control for how hard vs. soft every
edge in the qualifier is -- 0 is closer to a hard cutoff, 1 the softest.
`invert` flips the final matte (1-matte) -- the standard "key everything
ELSE" toggle.

Same "no PySide6 import" rule as every other backend module in this app.
"""
import numpy as np

import color_warper as cw

DEFAULT_WORKING_COLOURSPACE = cw.DEFAULT_WORKING_COLOURSPACE
DEFAULT_HUE_CENTER = 0.0
DEFAULT_HUE_WIDTH = 30.0
DEFAULT_CHROMA_MIN = 0.0
# OKLCh chroma's own natural range runs roughly 0 (fully neutral) to
# ~0.32 for a saturated sRGB primary -- NOT [0,1] the way HSL saturation
# was, so "full range, no gating" needs its own ceiling rather than
# reusing 1.0. 0.4 gives comfortable headroom above ordinary sRGB gamut
# chroma (wider-gamut working spaces can still push past it -- the user
# can always raise this in the inspector).
DEFAULT_CHROMA_MAX = 0.4
DEFAULT_LIGHTNESS_MIN = 0.0
DEFAULT_LIGHTNESS_MAX = 1.0
DEFAULT_SOFTNESS = 0.3
DEFAULT_INVERT = False


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


def _band_term(value, lo, hi, softness, edge_width):
    """1.0 for any `value` inside [lo, hi], falling off outside it via
    color_warper.falloff_weight's own super-Gaussian shape, at the given
    `edge_width` (in the SAME units as `value`/`lo`/`hi` -- chroma and
    lightness have different natural scales, so each caller picks its
    own edge_width rather than sharing one absolute number)."""
    distance_outside = np.maximum(0.0, lo - value) + np.maximum(0.0, value - hi)
    return cw.falloff_weight(distance_outside, wake=edge_width, dropoff=_softness_to_dropoff(softness))


def compute_matte(image_array, params):
    """image_array: (H,W,3), ENCODED code values in
    params["working_colourspace"] -- decoded via color_warper.decode_
    working_colourspace_to_oklch before qualifying (see module
    docstring). params: a plain dict, see the DEFAULT_* constants above
    for keys and defaults (every key is optional, missing ones fall back
    to that default -- same `params.get(key, DEFAULT_X)` convention
    every other stage kind's own apply function already uses). Returns
    an (H,W) float array in [0,1]."""
    working_colourspace = params.get("working_colourspace", DEFAULT_WORKING_COLOURSPACE)
    L, C, H = cw.decode_working_colourspace_to_oklch(image_array, working_colourspace)

    hue_center = float(params.get("hue_center", DEFAULT_HUE_CENTER))
    hue_width = max(1e-3, float(params.get("hue_width", DEFAULT_HUE_WIDTH)))
    chroma_min = float(params.get("chroma_min", DEFAULT_CHROMA_MIN))
    chroma_max = float(params.get("chroma_max", DEFAULT_CHROMA_MAX))
    lightness_min = float(params.get("lightness_min", DEFAULT_LIGHTNESS_MIN))
    lightness_max = float(params.get("lightness_max", DEFAULT_LIGHTNESS_MAX))
    softness = float(np.clip(params.get("softness", DEFAULT_SOFTNESS), 0.0, 1.0))
    invert = bool(params.get("invert", DEFAULT_INVERT))

    hue_term = cw.falloff_weight(_signed_angular_delta(hue_center, H), wake=hue_width,
                                  dropoff=_softness_to_dropoff(softness))
    # Edge width scaled to each channel's own natural range (chroma_max
    # for chroma, the full [0,1] span for lightness) rather than one
    # shared absolute width -- keeps "how much of the range is soft
    # edge" comparable across two channels with very different scales.
    chroma_term = _band_term(C, chroma_min, chroma_max, softness,
                              edge_width=0.03 + 0.25 * softness * max(chroma_max, 1e-6))
    lightness_term = _band_term(L, lightness_min, lightness_max, softness,
                                 edge_width=0.03 + 0.25 * softness)

    matte = hue_term * chroma_term * lightness_term
    if invert:
        matte = 1.0 - matte
    return np.clip(matte, 0.0, 1.0)
