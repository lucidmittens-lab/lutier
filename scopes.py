"""
scopes.py -- Qt-free backend for the Scopes panel: histogram, waveform, and
vectorscope data computed from an (H,W,3) float image array in domain
[0,1] (the same convention as the rest of the app -- see
apply_lut_to_image_array in app_logic.py, which is what actually produces
the arrays this module reads).

Kept free of any PySide6/pyqtgraph import, same rule as app_logic.py, so it
can be fully tested here without a display. The GUI layer (scopes_panel.py)
just calls these functions and hands the arrays to pyqtgraph ImageItem/
PlotDataItem -- no scope math lives in the widget.

All three scopes operate on whatever image array they're given -- typically
SampleImagePanel's current "after" preview (post current-LUT or post-chain),
so what you see in the scopes always matches what you see in the wipe
compare, at the same preview resolution.

Also home to the Waveform's three optional graticule overlays (per Tom,
see scopes_panel.py's "Waveform Graticule" tree menu, one active at a
time): pq_encode/pq_graticule_stops (SMPTE ST 2084 nit levels -- a real
nonlinear curve), and the two purely linear ones, mv_graticule_stops
(the conventional 0-700mV waveform-monitor scale) and
percent_graticule_stops (this domain's own [0,1] re-expressed as 0-100%).
None of these take an image array at all -- they're just "where would
this reference level land on the given scale," independent of whatever's
actually loaded. Whether a given scale is a meaningful read for the
CURRENT image (e.g. whether it's actually PQ-encoded) is something only
the person looking at it knows -- this app has no color management
tagging on arbitrary image arrays to check that against (see
load_image_as_array's docstring), which is exactly why all three
graticules are opt-in rather than always shown.
"""
import numpy as np

# Rec.709 luma coefficients -- used for the luma trace in histogram/
# waveform and as the Y in the Y'CbCr-style projection the vectorscope
# plots chroma in. This app already treats image arrays as plain [0,1]
# numbers with no color management applied (see load_image_as_array's
# docstring) so this is a fixed, documented choice, not a color-managed
# "correct" luma for whatever space the pixels actually happen to be in.
LUMA_COEFFICIENTS_REC709 = (0.2126, 0.7152, 0.0722)


def compute_luma(image_array, coefficients=LUMA_COEFFICIENTS_REC709):
    """(H,W,3) -> (H,W) luma plane using the given RGB coefficients."""
    image_array = np.asarray(image_array, dtype=np.float64)
    cr, cg, cb = coefficients
    return image_array[..., 0] * cr + image_array[..., 1] * cg + image_array[..., 2] * cb


def compute_histogram(image_array, bins=256, value_range=(0.0, 1.0)):
    """Per-channel + luma histogram of `image_array`.

    Returns a dict with 'r', 'g', 'b', 'luma' (each a (bins,) count array)
    and 'bin_edges' ((bins+1,) array, shared by all four since they use the
    same bins/value_range). Counts are raw pixel counts, not normalized --
    the GUI layer decides whether to scale for display (e.g. log or
    per-column max-normalize), keeping this function a plain, testable
    "count what's there."
    """
    image_array = np.asarray(image_array, dtype=np.float64)
    result = {}
    bin_edges = None
    for i, channel in enumerate(("r", "g", "b")):
        counts, bin_edges = np.histogram(image_array[..., i], bins=bins, range=value_range)
        result[channel] = counts
    luma = compute_luma(image_array)
    counts, bin_edges = np.histogram(luma, bins=bins, range=value_range)
    result["luma"] = counts
    result["bin_edges"] = bin_edges
    return result


def compute_waveform(image_array, levels=256, value_range=(0.0, 1.0)):
    """Per-channel + luma waveform ("parade") of `image_array`.

    For each column of the image and each channel, bins that column's pixel
    values into `levels` buckets across value_range -- the classic waveform
    monitor read of "for this column, how many pixels sit at each
    brightness level," stacked left-to-right across all columns.

    Returns a dict with 'r', 'g', 'b', 'luma', each a (levels, width) count
    array suitable for handing straight to an image/heatmap widget (row 0 =
    value_range[0], row levels-1 = value_range[1], so the GUI should
    display it Y-flipped if it wants "up = brighter" like a real scope).
    """
    image_array = np.asarray(image_array, dtype=np.float64)
    height, width, _ = image_array.shape
    lo, hi = value_range
    span = hi - lo if hi != lo else 1.0

    def _column_histograms(channel_plane):
        # channel_plane: (H, W). Bin every pixel into [0, levels), then
        # one single vectorized 2D histogram -- fold the column index into
        # the bin index (bin*width + column gives every (level, column)
        # cell its own unique flat index) so ONE np.bincount over the
        # whole flattened array replaces what used to be a separate
        # np.bincount call per column (~480 calls per channel per render,
        # ~1920 total across r/g/b/luma, on every live stack edit while
        # Waveform is the active scope -- real per-call Python/numpy
        # dispatch overhead multiplied by ~2000). Confirmed bit-for-bit
        # identical output to the old per-column-loop version.
        width = channel_plane.shape[1]
        bin_idx = np.clip(
            ((channel_plane - lo) / span * levels).astype(np.int64), 0, levels - 1
        )
        col_idx = np.arange(width)[None, :]
        combined = bin_idx * width + col_idx
        return np.bincount(combined.ravel(), minlength=levels * width).reshape(levels, width)

    result = {
        "r": _column_histograms(image_array[..., 0]),
        "g": _column_histograms(image_array[..., 1]),
        "b": _column_histograms(image_array[..., 2]),
        "luma": _column_histograms(compute_luma(image_array)),
    }
    return result


def compute_chroma_plane(image_array, coefficients=LUMA_COEFFICIENTS_REC709):
    """(H,W,3) -> (Cb, Cr) planes, each (H,W), via a Y'CbCr-style
    projection using `coefficients` for Y. Scaled so that pure-saturated
    R/G/B/C/M/Y at value 1.0 (against a Y computed from the same
    coefficients) land at a Cb/Cr magnitude of roughly 0.5 -- i.e. the same
    normalization convention real vectorscopes use, just not tied to any
    particular broadcast standard's exact constants beyond the luma
    coefficients themselves."""
    image_array = np.asarray(image_array, dtype=np.float64)
    y = compute_luma(image_array, coefficients)
    b = image_array[..., 2]
    r = image_array[..., 0]
    cr, cg, cb_coef = coefficients
    # Max possible |B - Y| is (1 - cb_coef) at B=1,R=G=0 (or cb_coef at
    # R=G=1,B=0) -- normalize by the larger of the two so the plane's
    # extremes land at magnitude 0.5, matching a conventional vectorscope
    # graticule's outer ring representing 100% saturation.
    cb_norm = max(1.0 - cb_coef, cb_coef) * 2.0
    cr_norm = max(1.0 - cr, cr) * 2.0
    cb = (b - y) / cb_norm
    cr_plane = (r - y) / cr_norm
    return cb, cr_plane


def compute_vectorscope(image_array, bins=256, chroma_range=(-0.5, 0.5)):
    """2D (Cb, Cr) density histogram of `image_array`, shape (bins, bins),
    for a vectorscope-style display. `chroma_range` applies to both axes.

    Returns a dict with 'density' (the (bins,bins) count array, axis 0 =
    Cb, axis 1 = Cr) and 'chroma_range' (echoed back, so the GUI knows what
    axis range the density array corresponds to without recomputing it)."""
    cb, cr = compute_chroma_plane(image_array)
    density, _, _ = np.histogram2d(
        cb.ravel(), cr.ravel(), bins=bins, range=[chroma_range, chroma_range]
    )
    return {"density": density, "chroma_range": chroma_range}


# ---- PQ (SMPTE ST 2084) graticule -----------------------------------------
#
# Standard PQ OETF constants (SMPTE ST 2084 / ITU-R BT.2100), the same
# fixed constants any PQ implementation uses -- not this app's own tuning.
PQ_M1 = 2610.0 / 16384.0
PQ_M2 = 2523.0 / 4096.0 * 128.0
PQ_C1 = 3424.0 / 4096.0
PQ_C2 = 2413.0 / 4096.0 * 32.0
PQ_C3 = 2392.0 / 4096.0 * 32.0
# PQ's defining peak -- 10,000 cd/m^2 always maps to code value 1.0.
PQ_PEAK_NITS = 10000.0

# Reference nit stops shown on the Waveform's optional PQ Graticule -- the
# same handful of levels HDR grading monitors/scopes conventionally mark:
# 100 nits (a common "SDR-ish" reference), 203 nits (ITU-R BT.2408's SDR
# reference white level inside a PQ container), then decade-ish stops up
# to PQ's own 10,000-nit peak.
PQ_GRATICULE_NITS = (100, 203, 1000, 4000, 10000)


def pq_encode(nits):
    """SMPTE ST 2084 (PQ) OETF -- absolute luminance in cd/m^2 (`nits`,
    scalar or array) -> normalized code value in [0,1]. 0 nits -> 0.0,
    PQ_PEAK_NITS (10,000 nits) -> 1.0, monotonically increasing in
    between. Values outside [0, PQ_PEAK_NITS] are clamped first -- PQ
    itself isn't defined beyond its peak.

    Pure math, no notion of what's actually stored in any particular
    image array -- see this module's docstring on why the graticule this
    feeds is opt-in rather than assumed."""
    nits_array = np.asarray(nits, dtype=np.float64)
    y = np.clip(nits_array, 0.0, PQ_PEAK_NITS) / PQ_PEAK_NITS
    y_m1 = y ** PQ_M1
    code_value = ((PQ_C1 + PQ_C2 * y_m1) / (1.0 + PQ_C3 * y_m1)) ** PQ_M2
    return code_value


def pq_graticule_stops():
    """[(nits, code_value), ...] for PQ_GRATICULE_NITS, each code_value in
    [0,1] via pq_encode -- see scopes_panel.py's Waveform PQ graticule,
    which draws one horizontal reference line per entry."""
    return [(nits, float(pq_encode(nits))) for nits in PQ_GRATICULE_NITS]


# ---- mV graticule ----------------------------------------------------------
#
# The conventional analog waveform-monitor scale: 0% signal = 0mV, 100% =
# 700mV -- the same 0-700mV convention NTSC/PAL/component waveform
# monitors have always used (100 IRE = 700mV), still the standard "mV"
# graticule most waveform tools show even for a fully digital signal path
# with no actual voltage involved. Unlike PQ, this is a PLAIN LINEAR
# scale of this app's own [0,1] code-value domain -- no curve, just a
# unit conversion.
MV_FULL_SCALE = 700.0
MV_GRATICULE_MILLIVOLTS = (0, 100, 200, 300, 400, 500, 600, 700)


def mv_graticule_stops():
    """[(millivolts, code_value), ...] for MV_GRATICULE_MILLIVOLTS, each
    code_value = millivolts / MV_FULL_SCALE -- see scopes_panel.py's
    Waveform mV graticule."""
    return [(mv, mv / MV_FULL_SCALE) for mv in MV_GRATICULE_MILLIVOLTS]


# ---- % graticule ------------------------------------------------------------
#
# The simplest graticule -- this app's waveform domain [0,1] already IS a
# 0-100% scale, so no conversion is needed at all beyond the display label.
PERCENT_GRATICULE_VALUES = (0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100)


def percent_graticule_stops():
    """[(percent, code_value), ...] for PERCENT_GRATICULE_VALUES, each
    code_value = percent / 100 -- see scopes_panel.py's Waveform %
    graticule."""
    return [(pct, pct / 100.0) for pct in PERCENT_GRATICULE_VALUES]
