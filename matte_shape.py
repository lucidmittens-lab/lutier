"""
matte_shape.py -- Qt-free backend for the "matte_shape" stage kind: a
classic "Power Window" garbage matte (an ellipse or rectangle placed
anywhere in frame, with a soft edge), producing a MATTE (see stack.py's
own docstring, "MATTE CHANNELS") without ever reading the image's own
RGB values at all -- pure geometry, the same "matte producer that isn't
about pixel color" shape matte_combine.py already established, just
reading a shape's own params + the image's RESOLUTION instead of other
mattes.

Naturally combines with matte_combine.py (Tom: "we should also think
about shapes, matte combination..." -- these two together give the
classic "restrict a qualifier's key to inside/outside a window" garbage-
matte workflow every real keyer needs): AND a shape with a keyer's own
qualifier matte to garbage-matte out a stray same-hue region elsewhere
in frame.

`center_x`/`center_y`/`width`/`height`: fractional [0,1], the SAME
image-relative convention `segmentation.py`'s own points already use
(x is a fraction of image WIDTH, y a fraction of image HEIGHT) --
`center_x`/`center_y` position the shape's own center, `width`/`height`
its own full extent along each axis. The in/out-of-shape test itself
runs in REAL PIXEL space (converting these fractions against the
image's actual (H, W) before comparing), not raw normalized units per
axis independently -- so a circle (width == height in real pixels, not
just in fraction terms) actually renders as a circle regardless of the
image's own aspect ratio, rather than stretching on a non-square frame.

`rotation`: degrees, clockwise, applied around the shape's own center
before the in/out test.

`softness`: same single shared "how hard vs. soft is the edge" knob
convention keyer.py's own qualifier already uses, reusing color_warper.
falloff_weight (see that function's own docstring) on the shape's own
excess-distance-past-its-edge term -- 0 is closer to a hard cutoff, 1
the softest.

`invert`: flips the final matte (1 - matte), same as every other matte-
producing kind's own invert.

Same "no PySide6 import" rule as every other backend module in this app.
"""
import numpy as np

import color_warper as cw

DEFAULT_SHAPE = "ellipse"
SHAPES = ("ellipse", "rectangle")
DEFAULT_CENTER_X = 0.5
DEFAULT_CENTER_Y = 0.5
# A freshly-added shape needs to exist as SOME concrete, visible geometry
# from the moment it's added -- unlike keyer/matte_combine, there's no
# "no points yet, so it's a no-op" state a shape can start at (a
# zero-size shape is just as much an arbitrary default as a non-zero
# one). 0.3 (30% of each axis) centered in frame gives Highlight mode
# something immediately visible to show.
DEFAULT_WIDTH = 0.3
DEFAULT_HEIGHT = 0.3
DEFAULT_ROTATION = 0.0
DEFAULT_SOFTNESS = 0.2
DEFAULT_INVERT = False


def _softness_to_dropoff(softness):
    """Same convention as keyer._softness_to_dropoff -- color_warper.
    falloff_weight's own `dropoff` runs the OPPOSITE direction from this
    module's `softness` (dropoff=1 is a HARDER edge, dropoff=0 the more
    gradual Gaussian tail), so softness=1 (softest) maps to dropoff=0
    and softness=0 (hardest) maps to dropoff=1."""
    return 1.0 - softness


def compute_shape_matte(shape_hw, params):
    """shape_hw: (H, W), the image's own resolution -- this stage kind
    never reads actual pixel VALUES, only the array's shape, so callers
    pass just the shape rather than a full image array. params: a plain
    dict, see the DEFAULT_* constants above for keys/defaults (every key
    optional, same params.get(key, DEFAULT_X) convention every other
    stage kind's own apply function already uses). Returns an (H,W)
    float array in [0,1]."""
    h, w = shape_hw
    shape = params.get("shape", DEFAULT_SHAPE)
    center_x = float(params.get("center_x", DEFAULT_CENTER_X))
    center_y = float(params.get("center_y", DEFAULT_CENTER_Y))
    width = max(1e-6, float(params.get("width", DEFAULT_WIDTH)))
    height = max(1e-6, float(params.get("height", DEFAULT_HEIGHT)))
    rotation = float(params.get("rotation", DEFAULT_ROTATION))
    softness = float(np.clip(params.get("softness", DEFAULT_SOFTNESS), 0.0, 1.0))
    invert = bool(params.get("invert", DEFAULT_INVERT))

    # Pixel-space center/half-extent -- converting BOTH axes against the
    # image's own real (H, W) is what keeps a circle circular (not
    # stretched) regardless of the image's aspect ratio, see this
    # module's own docstring.
    cx_px = center_x * w
    cy_px = center_y * h
    half_w_px = max(1e-6, width * w / 2.0)
    half_h_px = max(1e-6, height * h / 2.0)

    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    dx = xx - cx_px
    dy = yy - cy_px

    # Rotate the offset by -rotation (clockwise `rotation` on the shape
    # itself is equivalent to counter-rotating the sample point before
    # testing against an axis-aligned shape).
    theta = np.radians(-rotation)
    cos_t, sin_t = np.cos(theta), np.sin(theta)
    rx = dx * cos_t - dy * sin_t
    ry = dx * sin_t + dy * cos_t

    nx = rx / half_w_px
    ny = ry / half_h_px

    edge_width = 0.05 + 0.3 * softness
    dropoff = _softness_to_dropoff(softness)
    if shape == "ellipse":
        radius = np.sqrt(nx * nx + ny * ny)
        excess = np.maximum(0.0, radius - 1.0)
    elif shape == "rectangle":
        excess = np.maximum(0.0, np.maximum(np.abs(nx), np.abs(ny)) - 1.0)
    else:
        raise ValueError(f"Unknown matte shape: {shape!r} (expected one of {SHAPES})")

    matte = cw.falloff_weight(excess, wake=edge_width, dropoff=dropoff)
    if invert:
        matte = 1.0 - matte
    return np.clip(matte, 0.0, 1.0)
