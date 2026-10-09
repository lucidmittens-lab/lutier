"""
media_probe.py -- header-only metadata for the Media Libraries table's
columns (Tom, 2026-10-05: "a more complete column list"). probe(path)
returns a plain JSON-able dict stored as MediaEntry.metadata and saved
with the session as-is, so adding a column later is a new key here plus
a column in media_panel.py, not a new MediaEntry field and session
version bump each time.

Header only, same contract as the rest of the Media Library (see
media_library.py's docstring): PyAV opens the container and reads stream
parameters without decoding a frame; OpenImageIO reads the image spec
without the raster. Every key is optional -- a source that doesn't carry
something (no embedded timecode, no camera tag, no audio) simply has no
key, and the table leaves that cell blank.

Keys:
  file_size (int bytes), file_modified (float epoch seconds),
  format (container/file format, e.g. "MXF", "OpenEXR"),
  codec (e.g. "ProRes 4444", "H.264 High", "ZIP"),
  bit_depth (int bits per component), is_float (bool),
  chroma ("4:4:4", "4:2:2", "4:2:0", "RGB", "Mono", ...),
  has_alpha (bool), start_timecode ("HH:MM:SS:FF" or ";FF" drop-frame),
  audio_channels (int, summed across audio streams), audio_sample_rate,
  camera (e.g. "ARRI ALEXA Mini LF"), pixel_aspect_ratio (float),
  recorded (ISO 8601 string, from the file's own creation/recording date
  tag or a still's EXIF DateTimeOriginal -- not the filesystem date),
  firmware (camera software version, e.g. "SUP 6.00.15"),
  reel and clip (e.g. "F003" / "C002": a reel_name tag, else parsed from
  ARRI/RED/Sony-style camera file names like F003C002_190925_MN99),
  audio_bit_depth (int), colour_primaries / colour_transfer /
  colour_matrix (e.g. "BT.709", "PQ": the stream's own colour tags, each
  only when declared -- most camera ProRes declares none or just the
  matrix).

No PySide6 import -- fully testable without a display, same rule as
every other backend module in this app.
"""
import os
import re

import av
import OpenImageIO as oiio

# ffmpeg codec name -> the name editors actually use. Anything not listed
# falls back to ffmpeg's own short name, upper-cased.
_CODEC_NAMES = {
    "prores": "ProRes", "h264": "H.264", "hevc": "H.265", "dnxhd": "DNxHD",
    "mjpeg": "Motion JPEG", "av1": "AV1", "vp9": "VP9", "vp8": "VP8",
    "ffv1": "FFV1", "png": "PNG", "rawvideo": "Uncompressed", "cfhd": "CineForm",
    "mpeg2video": "MPEG-2", "mpeg4": "MPEG-4", "jpeg2000": "JPEG 2000",
    "dpx": "DPX", "qtrle": "QuickTime Animation", "v210": "Uncompressed 10-bit",
}

# ffmpeg/OIIO format name -> display name.
_FORMAT_NAMES = {
    "mxf": "MXF", "mov,mp4,m4a,3gp,3g2,mj2": "QuickTime/MP4",
    "matroska,webm": "Matroska", "avi": "AVI",
    "openexr": "OpenEXR", "tiff": "TIFF", "png": "PNG", "jpeg": "JPEG",
    "dpx": "DPX", "bmp": "BMP",
}

# Container/stream metadata keys that carry the camera make and model,
# in the order they're tried (MXF, QuickTime, generic).
# Tags that carry the recording date, the camera firmware and the reel
# name, in the order they're tried.
_RECORDED_KEYS = ("creation_time", "com.apple.quicktime.creationdate", "modification_date", "date")
_FIRMWARE_KEYS = ("product_version", "com.apple.quicktime.software", "encoder_version")
_REEL_KEYS = ("reel_name", "reel", "com.apple.quicktime.reelname")
# Camera-original file names: ARRI "F003C002_190925_MN99" (reel F003, clip
# C002), RED "A001_C003_0101AB", Sony "A001C003_..." -- a reel letter +
# 3 digits, an optional "_", then a "C" + 3 digits clip number.
_CAMERA_FILENAME = re.compile(r"^([A-Z]\d{3})_?(C\d{3})(?=[_.]|$)", re.IGNORECASE)
# ffmpeg's numeric colour tags (AVColorPrimaries / AVColorTransfer-
# Characteristic / AVColorSpace) -> display names. PyAV hands these back as
# ints; 2 is "unspecified" in all three, and anything not listed is shown
# as its number.
_PRIMARIES = {1: "BT.709", 4: "BT.470M", 5: "BT.601 PAL", 6: "BT.601 NTSC", 9: "BT.2020",
              10: "XYZ", 11: "DCI-P3", 12: "P3-D65", 22: "EBU 3213"}
_TRANSFERS = {1: "BT.709", 4: "Gamma 2.2", 5: "Gamma 2.8", 6: "BT.601", 8: "Linear",
              13: "sRGB", 14: "BT.2020 10-bit", 15: "BT.2020 12-bit", 16: "PQ",
              17: "SMPTE 428", 18: "HLG"}
_MATRICES = {0: "RGB", 1: "BT.709", 5: "BT.601 PAL", 6: "BT.601 NTSC", 9: "BT.2020",
             10: "BT.2020 CL", 14: "ICtCp"}
_UNSPECIFIED_COLOUR = {2}

_CAMERA_KEY_PAIRS = (
    ("company_name", "product_name"),
    ("com.apple.quicktime.make", "com.apple.quicktime.model"),
    ("make", "model"),
)


def probe(path, is_video):
    """Metadata dict for `path` (see module docstring). `is_video` comes
    from the caller (MediaEntry.media_kind) rather than being sniffed --
    ffmpeg happily opens a TIFF or PNG as a one-frame "video", which
    would report it as such. Raises whatever the underlying reader
    raises if the file can't be opened at all; a missing individual
    field is never an error."""
    path = str(path)
    stat = os.stat(path)
    metadata = {"file_size": stat.st_size, "file_modified": stat.st_mtime}
    if is_video:
        container = av.open(path)
        try:
            if not container.streams.video:
                raise ValueError(f"{path} has no video stream")
            metadata.update(_probe_video(container))
        finally:
            container.close()
    else:
        metadata.update(_probe_still(path))
    return metadata


def _probe_video(container):
    stream = container.streams.video[0]
    cc = stream.codec_context
    metadata = {"format": _FORMAT_NAMES.get(container.format.name, container.format.name.upper())}

    codec = _CODEC_NAMES.get(cc.name, cc.name.upper())
    profile = cc.profile
    if profile and profile.lower() not in codec.lower():
        # DNxHR reports its whole name as the profile ("DNXHR HQX").
        codec = profile if profile.upper().startswith("DNX") else f"{codec} {profile}"
    metadata["codec"] = codec

    if cc.pix_fmt:
        metadata.update(_pixel_format_fields(cc.pix_fmt))

    sar = stream.sample_aspect_ratio
    if sar:
        metadata["pixel_aspect_ratio"] = float(sar)

    tags = [dict(container.metadata)] + [dict(s.metadata) for s in container.streams]
    timecode = next((t["timecode"] for t in tags if t.get("timecode")), None)
    if timecode:
        metadata["start_timecode"] = timecode
    camera = _camera_from_tags(tags)
    if camera:
        metadata["camera"] = camera
    for key, keys in (("recorded", _RECORDED_KEYS), ("firmware", _FIRMWARE_KEYS)):
        value = _first_tag(tags, keys)
        if value:
            metadata[key] = _iso_date(value) if key == "recorded" else value
    metadata.update(_reel_and_clip(_first_tag(tags, _REEL_KEYS), container.name))
    metadata.update(_colour_tags(cc))

    audio = container.streams.audio
    if audio:
        metadata["audio_channels"] = sum(s.codec_context.channels or 0 for s in audio)
        if audio[0].codec_context.sample_rate:
            metadata["audio_sample_rate"] = audio[0].codec_context.sample_rate
        audio_bits = _audio_bit_depth(audio[0].codec_context)
        if audio_bits:
            metadata["audio_bit_depth"] = audio_bits
    return metadata


def _first_tag(tags, keys):
    for key in keys:
        for t in tags:
            value = str(t.get(key, "")).strip()
            if value:
                return value
    return None


def _iso_date(value):
    """'2019-09-25T09:56:02.000000Z' -> '2019-09-25T09:56:02'; EXIF's
    '2019:09:25 09:56:02' -> the same. Anything unparseable is kept as
    given (it's still better than nothing in a details panel)."""
    match = re.match(r"^(\d{4})[-:](\d{2})[-:](\d{2})[T ](\d{2}):(\d{2}):(\d{2})", value)
    if not match:
        return value
    y, mo, d, h, mi, s = match.groups()
    return f"{y}-{mo}-{d}T{h}:{mi}:{s}"


def _reel_and_clip(reel_tag, path):
    """{"reel": ..., "clip": ...} from a reel tag, else from a camera-
    original file name (see _CAMERA_FILENAME); {} when neither says."""
    fields = {}
    match = _CAMERA_FILENAME.match(os.path.basename(str(path or "")))
    if reel_tag:
        fields["reel"] = reel_tag
    elif match:
        fields["reel"] = match.group(1).upper()
    if match:
        fields["clip"] = match.group(2).upper()
    return fields


def _colour_tags(cc):
    """{"colour_primaries": ..., "colour_transfer": ..., "colour_matrix": ...}
    for whichever of the stream's colour tags are declared."""
    fields = {}
    for key, attr, names in (("colour_primaries", "color_primaries", _PRIMARIES),
                             ("colour_transfer", "color_trc", _TRANSFERS),
                             ("colour_matrix", "colorspace", _MATRICES)):
        value = getattr(cc, attr, None)
        try:
            code = int(value)
        except (TypeError, ValueError):
            continue
        if code in _UNSPECIFIED_COLOUR or (code == 0 and key != "colour_matrix"):
            continue
        fields[key] = names.get(code, str(code))
    return fields


def _audio_bit_depth(cc):
    """24 for pcm_s24le, 16 for pcm_s16be, 32 for pcm_f32le...; None for
    compressed audio."""
    match = re.match(r"^pcm_[suf](\d+)", cc.name or "")
    return int(match.group(1)) if match else None


def _pixel_format_fields(pix_fmt):
    fmt = av.VideoFormat(pix_fmt)
    components = fmt.components
    fields = {
        "bit_depth": max(c.bits for c in components),
        "has_alpha": any(c.is_alpha for c in components),
    }
    colour = [c for c in components if not c.is_alpha]
    if len(colour) == 1:
        fields["chroma"] = "Mono"
    elif fmt.is_rgb:
        fields["chroma"] = "RGB"
    else:
        # chroma samples per 4 luma samples, horizontally and vertically
        w, h = fmt.chroma_width(4), fmt.chroma_height(4)
        fields["chroma"] = f"4:{w}:{w if h == 4 else 0}"
    return fields


def _camera_from_tags(tags):
    for make_key, model_key in _CAMERA_KEY_PAIRS:
        for t in tags:
            make, model = t.get(make_key, "").strip(), t.get(model_key, "").strip()
            if make or model:
                if model.lower().startswith(make.lower()):
                    return model
                return f"{make} {model}".strip()
    return None


def _probe_still(path):
    inp = oiio.ImageInput.open(path)
    if inp is None:
        raise IOError(f"Couldn't open {path}: {oiio.geterror()}")
    try:
        spec = inp.spec()
        format_name = inp.format_name()
    finally:
        inp.close()
    metadata = {"format": _FORMAT_NAMES.get(format_name, format_name.upper())}

    compression = spec.get_string_attribute("compression", "")
    if compression and compression.lower() != "none":
        metadata["codec"] = compression.upper()
    else:
        metadata["codec"] = "Uncompressed"

    basetype = spec.format.basetype
    metadata["is_float"] = basetype in (oiio.HALF, oiio.FLOAT, oiio.DOUBLE)
    bits = spec.get_int_attribute("oiio:BitsPerSample", 0)
    if bits <= 0:
        bits = {oiio.UINT8: 8, oiio.HALF: 16, oiio.FLOAT: 32, oiio.DOUBLE: 64}.get(basetype, 16)
    metadata["bit_depth"] = bits

    metadata["has_alpha"] = spec.alpha_channel >= 0
    colour_channels = spec.nchannels - (1 if metadata["has_alpha"] else 0)
    metadata["chroma"] = "Mono" if colour_channels == 1 else "RGB"

    par = spec.get_float_attribute("PixelAspectRatio", 0.0)
    if par > 0:
        metadata["pixel_aspect_ratio"] = par
    camera = _camera_from_tags([{"make": spec.get_string_attribute("Make", ""),
                                 "model": spec.get_string_attribute("Model", "")}])
    if camera:
        metadata["camera"] = camera
    recorded = (spec.get_string_attribute("Exif:DateTimeOriginal", "")
                or spec.get_string_attribute("DateTime", ""))
    if recorded:
        metadata["recorded"] = _iso_date(recorded)
    software = spec.get_string_attribute("Software", "")
    if software:
        metadata["firmware"] = software
    metadata.update(_reel_and_clip(None, path))
    return metadata
