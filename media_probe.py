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
  camera (e.g. "ARRI ALEXA Mini LF"), pixel_aspect_ratio (float).

No PySide6 import -- fully testable without a display, same rule as
every other backend module in this app.
"""
import os

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

    audio = container.streams.audio
    if audio:
        metadata["audio_channels"] = sum(s.codec_context.channels or 0 for s in audio)
        if audio[0].codec_context.sample_rate:
            metadata["audio_sample_rate"] = audio[0].codec_context.sample_rate
    return metadata


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
    return metadata
