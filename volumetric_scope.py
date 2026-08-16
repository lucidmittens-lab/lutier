"""
volumetric_scope.py -- Qt-free math for the "Volumetric" scope in
scopes_panel.py: samples the current preview image's own pixels and
converts them to OKLCh, positioned in cartesian space for a live 3D
point cloud (see volumetric_scope_widget.py for the Qt/pyqtgraph
rendering layer -- same "Qt-free math module + Qt widget wrapper" split
as color_warper.py/color_warper_wheel.py).

PERCEPTUALLY CORRECT means real OKLCh (see color_warper.py's own
docstring on why that needs linear Rec.709/sRGB input specifically) --
but scopes_panel.py's OTHER scopes have ZERO color-space awareness
(confirmed directly: they plot raw [0,1] numbers with no decode step,
and this app tracks no color space for that array anywhere -- canvas.py
has no such field, load_image_as_array explicitly does no color
management). So `working_colourspace` here is a USER-ASSERTED "treat
this array as X" choice (the Scopes panel's own Color Space dropdown for
this one scope), not something derived from app state -- it reuses
color_warper.py's exact OCIO-vs-colour-science auto-dispatch (same
REFERENCE_LINEAR_SPACE target) rather than reimplementing it, so this
scope's notion of "perceptually correct" is identical to WARPier's own.

DEFAULT_VOLUMETRIC_COLOURSPACE deliberately differs from color_warper.
DEFAULT_WORKING_COLOURSPACE (ACEScct, a log/ACES-workflow assumption) --
a raw loaded still with no declared color space is far more likely
already display-encoded, so this scope defaults to a plain sRGB-ish
space instead.

Same "no PySide6 import" rule as color_warper.py/scopes.py -- fully
testable without a display.
"""
import numpy as np

import aces_drt
import color_warper as cw
import colorspace

DEFAULT_MAX_POINTS = 6000

DEFAULT_VOLUMETRIC_COLOURSPACE = (
    "sRGB Encoded Rec.709 (sRGB)" if aces_drt.OCIO_AVAILABLE else "sRGB"
)


def sample_pixels_to_oklch(image_array, working_colourspace=None,
                            max_points=DEFAULT_MAX_POINTS, seed=0):
    """Random-subsamples `image_array` (any (...,3)+ array, extra
    channels beyond RGB ignored) down to at most `max_points` pixels,
    decodes them from `working_colourspace` (defaults to
    DEFAULT_VOLUMETRIC_COLOURSPACE) to OKLCh, and returns
    `(positions, colors)`:

    - `positions`: an (N,3) float64 array of cartesian coordinates ready
      for a 3D scatter plot -- x/y place each point on the hue/chroma
      wheel (same GREEN_HUE/HUE_ROTATE orientation WarpierWheel uses, for
      visual consistency between the two), z is OKLab lightness.
    - `colors`: an (N,3) float64 array of the ORIGINAL (pre-decode)
      sampled RGB values, clipped to [0,1] -- points are colored by what
      the image actually looks like on screen, not re-derived from OKLCh.

    A subsample (not every pixel) is deliberate: a live scope recomputed
    on nearly every edit tick needs a bounded per-tick cost regardless of
    how many pixels the source image has (sample_image_panel.py's own
    SCOPES_MAX_DIMENSION already caps the array's resolution before this
    function ever sees it, but max_points caps it further for the actual
    3D upload/render cost). `seed` is fixed (not re-randomized per call)
    so the same underlying image produces a stable-looking cloud across
    consecutive ticks rather than visibly resampling every frame.

    Empty input (`image_array` with zero pixels) returns two empty (0,3)
    arrays rather than raising."""
    if working_colourspace is None:
        working_colourspace = DEFAULT_VOLUMETRIC_COLOURSPACE
    image_array = np.asarray(image_array, dtype=np.float64)
    pixels = image_array.reshape(-1, image_array.shape[-1])[:, :3]
    n_pixels = pixels.shape[0]
    if n_pixels == 0:
        empty = np.zeros((0, 3), dtype=np.float64)
        return empty, empty

    if n_pixels > max_points:
        rng = np.random.default_rng(seed)
        indices = rng.choice(n_pixels, size=max_points, replace=False)
        pixels = pixels[indices]

    if aces_drt.OCIO_AVAILABLE:
        linear = aces_drt.convert_colourspace(pixels, working_colourspace, cw.REFERENCE_LINEAR_SPACE)
    else:
        linear = colorspace.convert(pixels, working_colourspace, cw.REFERENCE_LINEAR_SPACE)

    lab = cw.linear_srgb_to_oklab(linear)
    L, C, H = cw.oklab_to_oklch(lab)

    rad = np.radians(H + cw.HUE_ROTATE)
    x = np.cos(rad) * C
    y = np.sin(rad) * C
    positions = np.stack([x, y, L], axis=-1)

    colors = np.clip(pixels, 0.0, 1.0)
    return positions, colors
