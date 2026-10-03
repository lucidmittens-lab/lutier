"""
media_library.py -- named references to still/video files on disk (the
"Media Libraries" panel, media_panel.py), each entry a metadata ROW, not
a decoded image. Mirrors lut_library.py's/cdl_library.py's "named,
reusable shelf" pattern -- see those modules' docstrings for the general
shape -- but unlike either of those (and unlike this module's own
predecessor, image_library.py), an entry never embeds pixel data: it
carries a path plus enough metadata (width/height, colourspace,
media_kind, video frame_count/frame_rate/duration, a cached thumbnail
descriptor) to populate media_panel.py's table without ever decoding the
file's raster data outside of generating that thumbnail once at import
time. Renamed from image_library.py -- the earlier module's own docstring
called this exact shape out as a possible future direction ("writing
pixel data to a sidecar file... not attempted here"); this goes further
and doesn't keep decoded pixels around at all, sidecar or otherwise.

PATH-REFERENCED, NOT EMBEDDED -- full stop, for both still and video
entries. Consequence, same as the old image_library.py's own "can't go
missing" caveat but now unconditional rather than a same-session-only
guarantee: moving/renaming/deleting the source file after import means
this entry can no longer be used (its next "Load as Source"/"Load as
Reference"/session-load will fail and warn, see media_panel.py/
session_io.py), whether that happens seconds later in the same session
or after a session reload. Every entry is imported from a real file (no
"New Identity" equivalent), so source_filename/source_path are always
populated for a real entry -- kept optional on the class itself anyway,
same defensive convention lut_library.py/cdl_library.py/the old
image_library.py already use.

width/height are probed HEADER-ONLY at import/session-load time (see
media_panel.py's import_from_path / session_io.py's
media_library_from_session_list) -- app_logic.get_image_native_size for a
still, video_source.VideoSource's own stream metadata for video -- never
a full raster decode. thumbnail is a media_cache.py storage descriptor,
generated once at import time from a single decode (the only raster
decode this module's data ever requires) via
thumbnail.make_thumbnail_png_bytes -- None for an entry created before
this field existed (old session load) or whose thumbnail generation
failed.

Qt-free, fully testable without a display -- same convention as
lut_library.py/cdl_library.py/stack_memory.py.
"""
import id_counter

MEDIA_KINDS = ("still", "video")

# The custom QMimeData format a dragged media_panel.py row carries (just
# the entry's own id, utf-8 encoded) -- shared here (a plain string, no
# Qt import needed) rather than duplicated/cross-imported between
# media_panel.py (the drag SOURCE) and timeline_track_widget.py (the drop
# TARGET), per Tom: "make it more similar to a typical NLE" (dragging a
# clip from your media bin onto the timeline).
MEDIA_ENTRY_DRAG_MIME_TYPE = "application/x-lutier-media-entry-id"

next_entry_id = id_counter.make_next_entry_id()


class MediaEntry:
    """One catalogued media file: a name, its on-disk path, and enough
    metadata to display/sort it in media_panel.py's table -- never a
    decoded pixel array (see module docstring). source_colourspace is the
    declared source color space if one was set at import time (None means
    Unmanaged, same meaning as SampleImagePanel's own
    _source_colourspace) -- ignored/None for a video entry, same asymmetry
    the old image_library.py already had. width/height are None only for
    an entry whose header probe failed at import (still catalogued, just
    without a Resolution column value) -- see media_panel.py's import
    error handling."""

    def __init__(self, name, source_path, source_colourspace=None,
                 source_filename=None, entry_id=None, media_kind="still",
                 width=None, height=None, frame_count=None, frame_rate=None,
                 duration=None, thumbnail=None):
        self.id = entry_id if entry_id is not None else next_entry_id()
        self.name = name
        self.source_path = source_path
        self.source_colourspace = source_colourspace
        self.source_filename = source_filename
        self.media_kind = media_kind
        self.width = width
        self.height = height
        self.frame_count = frame_count
        self.frame_rate = frame_rate
        self.duration = duration
        self.thumbnail = thumbnail

    @property
    def is_video(self):
        return self.media_kind == "video"

    def copy(self):
        return MediaEntry(
            self.name, self.source_path, source_colourspace=self.source_colourspace,
            source_filename=self.source_filename, entry_id=self.id,
            media_kind=self.media_kind, width=self.width, height=self.height,
            frame_count=self.frame_count, frame_rate=self.frame_rate,
            duration=self.duration, thumbnail=self.thumbnail,
        )

    def __repr__(self):
        size = f"{self.width}x{self.height}" if self.width and self.height else None
        return (
            f"MediaEntry(id={self.id}, name={self.name!r}, "
            f"size={size}, media_kind={self.media_kind!r}, "
            f"source_colourspace={self.source_colourspace!r})"
        )


class MediaLibrary:
    """An ordered list of MediaEntry entries."""

    def __init__(self, entries=None):
        self.entries = list(entries) if entries is not None else []

    def add_entry(self, entry):
        """Add an already-built MediaEntry directly -- used by
        media_panel.py's import path (one entry per imported file)."""
        self.entries.append(entry)
        return entry

    def rename(self, entry_id, new_name):
        self.find(entry_id).name = new_name

    def remove(self, entry_id):
        before = len(self.entries)
        self.entries = [e for e in self.entries if e.id != entry_id]
        if len(self.entries) == before:
            raise KeyError(f"No Media Library entry with id={entry_id}")

    def find(self, entry_id):
        for entry in self.entries:
            if entry.id == entry_id:
                return entry
        raise KeyError(f"No Media Library entry with id={entry_id}")
