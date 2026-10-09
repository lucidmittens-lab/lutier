"""
ycbcr_convert.py -- Qt-free, hardware-free RGB -> 10-bit 4:2:0 YCbCr
conversion for airplay_output_sink.py's CVPixelBuffer construction (see
that module's docstring for the destination pixel format,
kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange).

Same "Qt-free math module + thin backend wrapper" split as every other
non-UI module in this app (cdl.py/aces_drt.py/...) -- this file has no
PyObjC/AVFoundation dependency at all, so the actual color math is fully
testable without a real Mac video-output pipeline, same "trust the
vetted reference implementation, but verify the parts we hand-roll
ourselves" discipline this app follows for OCIO but can't lean on OCIO
for here (OCIO has no YCbCr-for-video-hardware-output concept -- this is
genuinely this app's own hand-rolled math, not a wrapper over someone
else's).

BT.709, VIDEO RANGE, 10-BIT -- the standard SDR broadcast/HD convention
(ITU-R BT.709 matrix, "video range" 10-bit levels: Y in [64, 940], Cb/Cr
in [64, 960], matching the classic 8-bit [16,235]/[16,240] video-range
convention scaled by 4x for 10-bit, per BT.709/BT.601's own N-bit
generalization -- black=16*2^(N-8), white=235*2^(N-8), chroma
center=128*2^(N-8)). Deliberately NOT attempting BT.2020/PQ HDR matrix
selection or HDR metadata (mastering display / content light level)
signaling here -- that's real additional scope (see docs/archive/
VIEWER_CASTING_PLAN_2026-08-11.md's own "not attempted" list), and would need a real
AirPlay HDR receiver to verify against, which this environment doesn't
have. This module's SDR path is what airplay_output_sink.py uses today;
an ACES DRT HDR Viewer output would still cast through this same BT.709
path (correct RANGE mapping into 10-bit code values, just not flagged as
HDR content to the receiver) rather than being rejected.

10-BIT-IN-16-BIT PACKING -- Apple's biplanar 10-bit pixel formats (and
the industry-standard v210/P010 family they mirror) store each 10-bit
sample "high-bit-justified" in a 16-bit little-endian word: the 10-bit
value occupies bits 6-15, with the low 6 bits zero (value16 = value10 <<
6). This is a well-established cross-vendor convention (P010, v210,
Apple's own planar/biplanar 10-bit formats all follow it), not a guess
specific to this module -- but NOT verified against a real AirPlay
receiver decoding it in this environment (no receiver hardware available
here -- see plan doc). pack_10bit_to_16bit/unpack_16bit_to_10bit below
are exact inverses of each other (tested), which confirms this module's
own pack/unpack round-trip is self-consistent; it does not by itself
prove the convention matches what a real receiver expects, only that the
documented convention is applied consistently.
"""
import numpy as np

# Video-range 10-bit levels (BT.709/BT.601's own N-bit generalization --
# see module docstring).
_Y_BLACK, _Y_WHITE = 64.0, 940.0
_C_LOW, _C_HIGH, _C_CENTER = 64.0, 960.0, 512.0

# BT.709 luma coefficients.
_KR, _KG, _KB = 0.2126, 0.7152, 0.0722
_CB_DENOM = 1.8556  # 2 * (1 - Kb)
_CR_DENOM = 1.5748  # 2 * (1 - Kr)


def rgb_to_bt709_ycbcr_float(rgb):
    """(..., 3) float RGB -> (Y', Cb, Cr) as three same-shaped float
    arrays, Y' in [0, 1], Cb/Cr in [-0.5, 0.5] -- the "analog" BT.709
    formula, before video-range digital scaling (see
    ycbcr_float_to_10bit_digital). Split out as its own function so
    test_ycbcr_convert.py can check the matrix math independently of the
    digital quantization step."""
    rgb = np.asarray(rgb, dtype=np.float64)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = _KR * r + _KG * g + _KB * b
    cb = (b - y) / _CB_DENOM
    cr = (r - y) / _CR_DENOM
    return y, cb, cr


def _luma_float_to_10bit_digital(y):
    """Y' in [0,1] -> 10-bit video-range digital luma (Y in [64,940]),
    rounded and clipped to that legal range (an out-of-gamut/HDR-
    headroom input can otherwise land outside it -- clipping here, not
    upstream, keeps rgb_to_bt709_ycbcr_float itself a pure, unclamped
    matrix transform some future caller might want raw)."""
    y_digital = np.clip(np.round(y * (_Y_WHITE - _Y_BLACK) + _Y_BLACK), _Y_BLACK, _Y_WHITE)
    return y_digital.astype(np.uint16)


def _chroma_float_to_10bit_digital(c):
    """Cb or Cr in [-0.5,0.5] -> 10-bit video-range digital chroma
    (in [64,960]), rounded and clipped -- see _luma_float_to_10bit_digital's
    own comment on why clipping happens at this stage."""
    c_digital = np.clip(np.round(c * (_C_HIGH - _C_LOW) + _C_CENTER), _C_LOW, _C_HIGH)
    return c_digital.astype(np.uint16)


def ycbcr_float_to_10bit_digital(y, cb, cr):
    """(Y' in [0,1], Cb/Cr in [-0.5,0.5]) -> 10-bit video-range digital
    code values (Y in [64,940], Cb/Cr in [64,960]) -- the combined,
    directly-testable entry point; rgb_to_yuv420p10_planes below calls
    the split per-channel helpers directly instead, since luma and
    chroma end up at different resolutions after 4:2:0 subsampling and
    have no reason to share one call."""
    return _luma_float_to_10bit_digital(y), _chroma_float_to_10bit_digital(cb), _chroma_float_to_10bit_digital(cr)


def _box_downsample_2x(plane):
    """(H, W) -> (ceil(H/2), ceil(W/2)), averaging each 2x2 block --
    standard chroma-siting-agnostic box filter for 4:2:0 subsampling.
    Odd H/W is handled by edge-replicating one extra row/column first
    (so every output sample still averages a real 2x2 block, never
    reads out of bounds) -- real-world source images/Viewer frames are
    not guaranteed to have even dimensions."""
    h, w = plane.shape
    if h % 2:
        plane = np.concatenate([plane, plane[-1:, :]], axis=0)
        h += 1
    if w % 2:
        plane = np.concatenate([plane, plane[:, -1:]], axis=1)
        w += 1
    return plane.reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))


def pack_10bit_to_16bit(values_10bit):
    """uint16 array already holding 10-bit values (0-1023) -> uint16
    array with each value left-shifted into bits 6-15 -- see module
    docstring's "10-BIT-IN-16-BIT PACKING" section."""
    values_10bit = np.asarray(values_10bit, dtype=np.uint16)
    return (values_10bit.astype(np.uint32) << 6).astype(np.uint16)


def unpack_16bit_to_10bit(values_16bit):
    """Inverse of pack_10bit_to_16bit -- shifts back down and masks to
    10 bits. Exists mainly so test_ycbcr_convert.py can round-trip-check
    the packing step in isolation."""
    values_16bit = np.asarray(values_16bit, dtype=np.uint16)
    return (values_16bit.astype(np.uint32) >> 6).astype(np.uint16) & 0x03FF


def rgb_to_yuv420p10_planes(rgb):
    """(H, W, 3) float RGB -> (y_plane, cbcr_plane), both uint16,
    16-bit-packed 10-bit values (see pack_10bit_to_16bit), ready to copy
    directly into a kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange
    CVPixelBuffer's two planes (see airplay_output_sink.py).

    y_plane: (H, W) -- full-resolution luma, one packed uint16 per pixel.
    cbcr_plane: (ceil(H/2), ceil(W/2), 2) -- half-resolution (4:2:0)
    chroma, Cb/Cr INTERLEAVED per sample (the "biplanar"/NV12-style
    layout every kCVPixelFormatType_420YpCbCr10Bi* format uses, as
    opposed to separate planar Cb/Cr planes)."""
    rgb = np.asarray(rgb, dtype=np.float64)
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError(f"rgb must be (H, W, 3), got {rgb.shape}")

    y, cb, cr = rgb_to_bt709_ycbcr_float(rgb)
    cb_sub = _box_downsample_2x(cb)
    cr_sub = _box_downsample_2x(cr)

    y_digital = _luma_float_to_10bit_digital(y)
    cb_digital = _chroma_float_to_10bit_digital(cb_sub)
    cr_digital = _chroma_float_to_10bit_digital(cr_sub)

    y_plane = pack_10bit_to_16bit(y_digital)
    cbcr_plane = np.empty((*cb_digital.shape, 2), dtype=np.uint16)
    cbcr_plane[..., 0] = pack_10bit_to_16bit(cb_digital)
    cbcr_plane[..., 1] = pack_10bit_to_16bit(cr_digital)
    return y_plane, cbcr_plane
