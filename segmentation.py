"""
segmentation.py -- Qt-free point-click "magic wand" region-growing
segmentation: GROUNDWORK for a new "segment_mask" stage kind in stack.py
(see that module's docstring, "MATTE CHANNELS", and
make_segment_mask_stage) -- a second matte-PRODUCING stage kind
alongside keyer.py's own HSL qualifier, this one driven by a small
number of user-clicked SEED POINTS on the image rather than a hue/sat/
lum range.

CLASSICAL CV HERE, NOT A NEURAL SEGMENT-ANYTHING MODEL YET (per Tom:
"start laying the groundwork for... segment anything which will need
the viewer interactibility" -- deliberately scoped down for THIS pass):
a real Segment-Anything-style model needs a runtime dependency this app
has never carried before -- PyTorch for the reference SAM checkpoint
(~2.4GB), or at minimum onnxruntime for a much lighter one (MobileSAM's
own ONNX export: ~9.66M params vs SAM's ~615M, reported CPU inference
well under a second on ordinary hardware, roughly ~40MB of weights,
confirmed via a 2026-08 search of MobileSAM's own docs/repo) -- plus a
real model-weights download. That's a genuine new-dependency and disk/
bandwidth decision, not something to make unilaterally while Tom's
away from the keyboard. This module's own compute_mask_from_points
gives a REAL, useful "click to select a region" tool TODAY with ZERO
new dependencies (scipy -- already a requirement -- is all this needs)
via straightforward seeded region growing (industry nickname: "magic
wand"/"paint bucket select"). The interface is intentionally narrow
(image_array, points) -> (H,W) mask so swapping in a real MobileSAM (or
similar) backend later is a drop-in replacement behind this same
function, not a redesign of anything that consumes it (stack.py's
make_segment_mask_stage, the eventual Viewer click-to-point wiring).

ALGORITHM: for each seed point (an image-relative (x_frac, y_frac) pair
-- the SAME [0,1] convention wipe_compare_widget.
widget_pos_to_image_fraction now produces from a Viewer click, see that
function's own docstring), sample the seed pixel's own color, compute
every OTHER pixel's Euclidean RGB distance from it, threshold at
`tolerance`, then keep only the CONNECTED COMPONENT
(scipy.ndimage.label) that actually touches the seed pixel itself -- a
plain global color threshold would also grab same-colored pixels
elsewhere in the frame that were never part of the region the user
actually clicked on; region growing (contiguous-only) is what makes
this "select THIS blob," not "select every pixel that happens to be
this color anywhere in the image." Multiple seed points union their own
regions (np.maximum, not addition, so overlapping seeds don't double up
past 1.0). Edge softness reuses color_warper.falloff_weight's own
super-Gaussian shape (the SAME falloff keyer.py's own qualifier already
uses) rather than inventing a second falloff formula for this app.

Deliberately does NOT do connectivity-8-vs-4 configuration, multi-scale
matching, or any of the refinements a real product version might want
-- narrowly scoped to prove the plumbing (stage kind, matte consumption,
eventual click wiring) end to end with a real working default, not to
be the final word on classical-CV selection quality.
"""
import numpy as np
from scipy import ndimage

import color_warper as cw

DEFAULT_TOLERANCE = 0.08
DEFAULT_FEATHER = 0.3
DEFAULT_INVERT = False



def _single_point_mask(image_array, point, tolerance, feather):
    """One seed point's own (H,W) contribution -- 0 outside the
    connected region entirely, color_warper.falloff_weight's own
    super-Gaussian value (1.0 at the seed's exact color, falling off as
    color distance approaches `tolerance`) inside it."""
    h, w = image_array.shape[:2]
    col = int(np.clip(point.get("x", 0.5), 0.0, 1.0) * (w - 1))
    row = int(np.clip(point.get("y", 0.5), 0.0, 1.0) * (h - 1))
    seed_color = image_array[row, col]

    distance = np.linalg.norm(image_array - seed_color, axis=-1)
    candidate = distance <= tolerance
    labeled, _ = ndimage.label(candidate)
    seed_label = labeled[row, col]
    if seed_label == 0:
        # The seed's own pixel didn't pass its own threshold -- can't
        # happen for finite, non-NaN color data (distance to itself is
        # always exactly 0 <= tolerance for any tolerance >= 0), but
        # degrade to "no region" rather than crashing on a downstream
        # index for a malformed/adversarial tolerance value.
        return np.zeros((h, w), dtype=np.float64)
    region = labeled == seed_label

    weight = cw.falloff_weight(
        distance, wake=max(tolerance, 1e-6), dropoff=cw.softness_to_dropoff(feather))
    return np.where(region, weight, 0.0)


def compute_mask_from_points(image_array, points, tolerance=DEFAULT_TOLERANCE,
                              feather=DEFAULT_FEATHER, invert=DEFAULT_INVERT):
    """image_array: (H,W,3) RGB, any working space, treated as-is (same
    "operate directly on the stack's own running RGB, no gamut
    conversion" simplification keyer.py's own docstring already makes).
    points: a list of {"x": frac, "y": frac} dicts, each in [0,1] image-
    relative coordinates. Returns an (H,W) float mask in [0,1]. An empty
    points list returns an all-zero mask -- "no seeds, nothing selected"
    -- same "opt-in, no-op until the user actually does something"
    convention color_warper's own empty-points identity establishes in
    its own way."""
    image_array = np.asarray(image_array, dtype=np.float64)
    h, w = image_array.shape[:2]
    if not points:
        return np.zeros((h, w), dtype=np.float64)

    combined = np.zeros((h, w), dtype=np.float64)
    for point in points:
        combined = np.maximum(combined, _single_point_mask(image_array, point, tolerance, feather))

    if invert:
        combined = 1.0 - combined
    return np.clip(combined, 0.0, 1.0)
