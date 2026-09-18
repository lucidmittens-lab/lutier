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

PRE-FILTER + MATTE BLUR + CLEANUP (per Tom: "pre filtering/blur for keys
as well as typical cleanup features (clean white/black)" -- then, once
these landed combined into one blur, clarified he meant TWO separate,
independent things, the same two DaVinci Resolve's own Qualifier splits
across the node tree vs. its own Matte Finesse panel): the standard
toolkit every real qualifier ships beyond the qualifier band itself,
applied in this order -- qualify -> pre-filter (before qualifying, see
below) -> matte blur -> clean black/white -> invert:
- `prefilter_blur` (pixel radius, >=0): if nonzero, blurs `image_array`
  itself (via scipy.ndimage.gaussian_filter, per-channel -- a 2D
  spatial blur, never mixing R/G/B) BEFORE decoding to OKLCh --
  the Nuke-style "pre-blur the source" technique, reducing noise/
  compression-artifact-driven SPECKLE in the resulting matte (isolated
  false-positive/false-negative pixels from grain, not the matte's own
  edges). 0 (the default) is an exact no-op -- skips the blur call
  entirely rather than relying on gaussian_filter(sigma=0) to be a
  no-op itself.
- `matte_blur` (pixel radius, >=0): if nonzero, blurs the ALREADY-
  COMPUTED matte itself (after the hue/chroma/lightness bands, before
  clip_black/white) -- Resolve's own Matte Finesse "Blur Radius"
  control, which softens/feathers the matte's own EDGES (as opposed to
  prefilter_blur's noise-reduction on the source). The two are
  independent and commonly used together: prefilter_blur cleans up
  noisy input before qualifying, matte_blur then softens whatever edge
  the qualifier itself produced. 0 (the default) is an exact no-op,
  same reasoning as prefilter_blur.
- `clip_black`/`clip_white` (each 0..1): a final linear remap of the
  computed matte -- AFTER matte_blur, BEFORE `invert` (so "invert"
  always flips whatever the cleaned key actually looks like, not the
  other way around; and clean black/white can re-harden whatever
  matte_blur just softened, exactly how Resolve's own Matte Finesse
  panel orders Blur Radius above Clean Black/Clean White) --
  everything at/below clip_black clamps to 0, at/above clip_white
  clamps to 1, linear in between. The standard "clean black/clean
  white" matte cleanup control (see _apply_clip's own docstring).
  Defaults (0.0/1.0) are an exact identity remap.

Same "no PySide6 import" rule as every other backend module in this app.
"""
import numpy as np
from scipy import ndimage

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
DEFAULT_PREFILTER_BLUR = 0.0
DEFAULT_MATTE_BLUR = 0.0
DEFAULT_CLIP_BLACK = 0.0
DEFAULT_CLIP_WHITE = 1.0


def _signed_angular_delta(h0, h1):
    """Shortest signed distance from h0 to h1 in degrees, wrapped to
    (-180, 180] -- same convention as color_warper._angular_delta, kept
    as its own tiny local copy rather than importing that module's
    private helper."""
    return ((h1 - h0 + 180.0) % 360.0) - 180.0


def _band_term(value, lo, hi, softness, edge_width):
    """1.0 for any `value` inside [lo, hi], falling off outside it via
    color_warper.falloff_weight's own super-Gaussian shape, at the given
    `edge_width` (in the SAME units as `value`/`lo`/`hi` -- chroma and
    lightness have different natural scales, so each caller picks its
    own edge_width rather than sharing one absolute number)."""
    distance_outside = np.maximum(0.0, lo - value) + np.maximum(0.0, value - hi)
    return cw.falloff_weight(distance_outside, wake=edge_width, dropoff=cw.softness_to_dropoff(softness))


def _apply_clip(matte, clip_black, clip_white):
    """Linear "clean black/clean white" matte cleanup: everything at or
    below clip_black clamps to 0, at or above clip_white clamps to 1,
    linear rescale between. clip_white <= clip_black (a degenerate/
    inverted range) falls back to a hard threshold at clip_black rather
    than dividing by a zero/negative span."""
    span = clip_white - clip_black
    if span <= 1e-6:
        return np.where(matte > clip_black, 1.0, 0.0)
    return np.clip((matte - clip_black) / span, 0.0, 1.0)


def compute_matte(image_array, params):
    """image_array: (H,W,3), ENCODED code values in
    params["working_colourspace"] -- decoded via color_warper.decode_
    working_colourspace_to_oklch before qualifying (see module
    docstring). params: a plain dict, see the DEFAULT_* constants above
    for keys and defaults (every key is optional, missing ones fall back
    to that default -- same `params.get(key, DEFAULT_X)` convention
    every other stage kind's own apply function already uses). Returns
    an (H,W) float array in [0,1]."""
    prefilter_blur = max(0.0, float(params.get("prefilter_blur", DEFAULT_PREFILTER_BLUR)))
    if prefilter_blur > 0.0:
        # Per-channel spatial blur only (sigma=0 on the channel axis) --
        # never mixes R/G/B, matching how a real pre-filter should
        # behave (denoise each channel's own spatial signal, don't
        # bleed color between channels).
        image_array = ndimage.gaussian_filter(image_array, sigma=(prefilter_blur, prefilter_blur, 0))

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
    matte_blur = max(0.0, float(params.get("matte_blur", DEFAULT_MATTE_BLUR)))
    clip_black = float(np.clip(params.get("clip_black", DEFAULT_CLIP_BLACK), 0.0, 1.0))
    clip_white = float(np.clip(params.get("clip_white", DEFAULT_CLIP_WHITE), 0.0, 1.0))

    hue_term = cw.falloff_weight(_signed_angular_delta(hue_center, H), wake=hue_width,
                                  dropoff=cw.softness_to_dropoff(softness))
    # Edge width scaled to each channel's own natural range (chroma_max
    # for chroma, the full [0,1] span for lightness) rather than one
    # shared absolute width -- keeps "how much of the range is soft
    # edge" comparable across two channels with very different scales.
    chroma_term = _band_term(C, chroma_min, chroma_max, softness,
                              edge_width=0.03 + 0.25 * softness * max(chroma_max, 1e-6))
    lightness_term = _band_term(L, lightness_min, lightness_max, softness,
                                 edge_width=0.03 + 0.25 * softness)

    matte = hue_term * chroma_term * lightness_term
    if matte_blur > 0.0:
        # A plain single-channel blur on the already-computed matte
        # itself (Resolve's own Matte Finesse "Blur Radius") -- softens
        # the qualifier's own EDGES, distinct from prefilter_blur's
        # noise-reduction on the source (see module docstring).
        matte = ndimage.gaussian_filter(matte, sigma=matte_blur)
    matte = _apply_clip(matte, clip_black, clip_white)
    if invert:
        matte = 1.0 - matte
    return np.clip(matte, 0.0, 1.0)
