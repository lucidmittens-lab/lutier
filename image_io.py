"""
image_io.py -- OpenImageIO-backed still image read/write, replacing the
PIL-only 8-bit-only path app_logic.py used before Canvas Settings' real
bit-depth support (Tom, 2026-08-13: "Yes -- real 10/12/16-bit + float
support"). Reads/writes 8/10/12/16-bit integer and 32-bit float
(EXR-class) images across TIFF/EXR/DPX/PNG/JPEG and everything else
OIIO's own plugin set covers, through ONE dependency that already pairs
naturally with this app's existing OpenColorIO dependency (both ASWF
projects) -- see the approved plan for why this was chosen over
splitting EXR/TIFF/DPX across three separate libraries.

INTERNAL REPRESENTATION stays float32 regardless of source bit depth,
matching every downstream stage in this app (custom_drt.py's whole design
is float-native/unbounded-HDR-capable, GPU textures are RGBA32F).

BIT DEPTH vs. CONTAINER SCALE -- these are NOT the same thing, and
conflating them was a real bug caught before this module ever shipped
(round-tripped a 10-bit DPX and got errors of ~0.06 in [0,1], nowhere
near the ~0.001 a real 10-bit quantization step should cause). Confirmed
directly against OIIO's own DPX plugin: a 10/12-bit sample living in a
16-bit (UINT16) container is stored SCALED TO THE FULL 16-BIT RANGE
(0-65535), NOT as a raw 0-1023/0-4095 value in the low bits -- the
oiio:BitsPerSample attribute is metadata about the TRUE PRECISION only
(how many distinct quantization levels are meaningfully present), it does
NOT change the numeric scale of the stored samples. So: reading ALWAYS
divides by the CONTAINER's own max (255 for UINT8, 65535 for UINT16,
unscaled for float) regardless of the detected true bit depth; writing
10/12-bit ALWAYS quantizes to that depth's own level count first (true
precision), then RE-EXPRESSES the result in the full UINT16 container
range before storing (see write_image) -- 16-bit is the trivial case
where "true depth" and "container" are already the same range.

Same rule as every other backend module in this app: no PySide6 import
here, fully testable without a display.
"""
import numpy as np
import OpenImageIO as oiio

import canvas as cv

# Passed to ImageInput.open as a config hint -- WITHOUT this, OIIO's own
# PNG (and some other) readers silently premultiply RGB by alpha on
# decode ("associated alpha"), confirmed directly: a source with
# alpha=128 (~0.5) came back with every RGB channel roughly HALVED from
# its true value. This app has never wanted that (matches PIL's old
# .convert("RGB"), which just drops alpha, never premultiplies) -- alpha
# itself is dropped anyway (see read_image_as_array), so the only
# question is whether RGB survives untouched, and this config makes it do
# so.
_READ_CONFIG = oiio.ImageSpec()
_READ_CONFIG.attribute("oiio:UnassociatedAlpha", 1)


def _detect_bit_depth(spec):
    """The real, per-sample bit depth of a decoded image -- NOT just its
    storage container width (see module docstring on why those differ
    for 10/12-bit DPX, confirmed directly: a DPX written at 10 bits
    round-trips oiio:BitsPerSample=10 even though its basetype is
    UINT16). Falls back to the container's own basetype only when the
    format doesn't report oiio:BitsPerSample at all (PNG/EXR/JPEG etc,
    confirmed directly they report -1/absent)."""
    bps = spec.get_int_attribute("oiio:BitsPerSample", 0)
    if bps in cv.BIT_DEPTHS:
        return bps
    basetype = spec.format.basetype
    if basetype in (oiio.HALF, oiio.FLOAT, oiio.DOUBLE):
        return "32f"
    if basetype == oiio.UINT8:
        return 8
    # Anything else (a UINT16 source with no explicit BitsPerSample, or
    # an exotic basetype this app doesn't specifically expect) -- 16 is
    # the safe, non-lossy assumption: every real 10/12-bit-CAPABLE format
    # (DPX, TIFF) DOES report BitsPerSample when it's actually 10/12, so
    # reaching this fallback means the source is genuinely 16-bit (or
    # something unusual enough that guessing lower would silently lose
    # real precision).
    return 16


def get_native_size(path):
    """(width, height) of the source file without decoding raster data --
    matches app_logic.get_image_native_size's own header-only-read
    contract (used by sample_image_panel's resize handling)."""
    inp = oiio.ImageInput.open(str(path))
    if inp is None:
        raise IOError(f"Couldn't open {path}: {oiio.geterror()}")
    try:
        spec = inp.spec()
        return spec.width, spec.height
    finally:
        inp.close()


def read_image_as_array(path):
    """Returns (array, bit_depth) -- array is (H,W,3) float32. `bit_depth`
    is the source's REAL precision (see _detect_bit_depth), reported for
    informational use -- but the numeric SCALE applied here is always the
    CONTAINER's own (255 for 8-bit, 65535 for any 10/12/16-bit UINT16
    source, regardless of which of those it actually is -- see module
    docstring on why those are different things). Float sources (EXR/HDR)
    pass through unscaled, unbounded -- matches this app's "raw numeric
    domain" convention (see app_logic.load_image_as_array's own
    docstring) and the fact every downstream stage is already
    float-native/HDR-capable. More than 3 channels (alpha, extra AOVs)
    are dropped; 1 or 2 channels (grayscale, or luminance+alpha)
    replicate channel 0 to all three -- matching PIL's old
    .convert("RGB") behavior for the same cases."""
    inp = oiio.ImageInput.open(str(path), _READ_CONFIG)
    if inp is None:
        raise IOError(f"Couldn't open {path}: {oiio.geterror()}")
    try:
        spec = inp.spec()
        bit_depth = _detect_bit_depth(spec)
        basetype = spec.format.basetype
        if basetype in (oiio.HALF, oiio.FLOAT, oiio.DOUBLE):
            read_type = oiio.FLOAT
        elif basetype == oiio.UINT8:
            read_type = oiio.UINT8
        else:
            read_type = oiio.UINT16
        pixels = np.asarray(inp.read_image(read_type))
    finally:
        inp.close()

    if pixels.ndim == 2:
        pixels = pixels[..., None]
    nchannels = pixels.shape[-1]
    if nchannels >= 3:
        pixels = pixels[..., :3]
    else:  # 1 (grayscale) or 2 (luminance+alpha) channels
        pixels = np.repeat(pixels[..., :1], 3, axis=-1)

    array = pixels.astype(np.float32)
    if read_type == oiio.UINT8:
        array = array / 255.0
    elif read_type == oiio.UINT16:
        array = array / 65535.0
    return array, bit_depth


def resize_array(array, target_w, target_h):
    """Lanczos resize (caller computes target_w/target_h, typically
    aspect-preserving -- see app_logic.load_image_as_array) via OIIO's
    own ImageBufAlgo, operating directly on the float32 array so no
    precision is lost through an intermediate 8-bit round trip (a real
    regression the old PIL-based downsample would have introduced for
    16-bit/float sources once those could actually be loaded)."""
    h, w = array.shape[:2]
    buf = oiio.ImageBuf(oiio.ImageSpec(w, h, 3, oiio.FLOAT))
    buf.set_pixels(oiio.ROI(0, w, 0, h, 0, 1, 0, 3), np.ascontiguousarray(array, dtype=np.float32))
    resized = oiio.ImageBufAlgo.resize(
        buf, filtername="lanczos3", roi=oiio.ROI(0, target_w, 0, target_h, 0, 1, 0, 3))
    if resized.has_error:
        raise RuntimeError(f"resize_array failed: {resized.geterror()}")
    return np.ascontiguousarray(resized.get_pixels(oiio.FLOAT), dtype=np.float32)


def quantize_to_bit_depth(array, bit_depth):
    """Returns (pixels, oiio_type) -- `pixels` is `array` ((...,3)
    float32) quantized to `bit_depth` (one of canvas.BIT_DEPTHS) as a
    real numpy array, `oiio_type` is the matching OIIO TypeDesc. Shared
    by write_image (below) and video_export.py's own per-frame encode
    quantization, so both stay byte-for-byte consistent rather than two
    independently-maintained copies of this logic drifting apart.

    Integer depths clip to [0,1] and quantize (round-to-nearest, matching
    the existing +0.5-then-truncate convention _float_array_to_pil_image
    already used) -- 10/12-bit quantize to THAT depth's own level count
    first, THEN re-express the result across the full UINT16 container
    range (see module docstring on why: OIIO's DPX plugin -- confirmed
    directly -- expects 10/12-bit samples pre-scaled to the full 16-bit
    range, not packed into the low N bits). "32f" passes the array
    through unclamped, preserving any HDR/negative range, matching this
    app's existing "don't silently discard legitimate HDR range" rule
    (see gpu_stack_renderer.py/casting_controller.py's own precedent)."""
    if bit_depth not in cv.BIT_DEPTHS:
        raise ValueError(f"Unknown bit_depth {bit_depth!r} (expected one of {cv.BIT_DEPTHS})")

    if bit_depth == "32f":
        return np.ascontiguousarray(array, dtype=np.float32), oiio.FLOAT
    if bit_depth == 8:
        pixels = np.ascontiguousarray(
            (np.clip(array, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8))
        return pixels, oiio.UINT8
    # 10, 12, 16 -- always a UINT16 container, see this function's own docstring
    depth_max = 2 ** bit_depth - 1
    quantized_level = np.round(np.clip(array, 0.0, 1.0) * depth_max)
    full_range = quantized_level / depth_max * 65535.0
    pixels = np.ascontiguousarray((full_range + 0.5).astype(np.uint16))
    return pixels, oiio.UINT16


def write_image(array, path, bit_depth):
    """Writes `array` ((H,W,3) float32) to `path`, format inferred from
    its extension (same convention app_logic.export_image_array_to_file
    already had). `bit_depth` (one of canvas.BIT_DEPTHS) comes from
    Canvas Settings, NOT the source's own -- see module and
    quantize_to_bit_depth's own docstrings for the actual quantization
    rules."""
    h, w = array.shape[:2]
    pixels, oiio_type = quantize_to_bit_depth(array, bit_depth)

    spec = oiio.ImageSpec(w, h, 3, oiio_type)
    if bit_depth in (10, 12):
        # DPX-style "true" bit depth in a wider container -- see module
        # docstring. Harmless to set on formats that ignore the attribute.
        spec.attribute("oiio:BitsPerSample", bit_depth)

    out = oiio.ImageOutput.create(str(path))
    if out is None:
        raise IOError(f"No OIIO plugin can write {path}: {oiio.geterror()}")
    try:
        if not out.open(str(path), spec):
            raise IOError(f"Couldn't open {path} for writing: {out.geterror()}")
        if not out.write_image(pixels):
            raise IOError(f"Failed writing {path}: {out.geterror()}")
    finally:
        out.close()
