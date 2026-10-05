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

LIBRARIES AND TIMELINES (Tom, 2026-10-05: "media library should have a
side panel to select the target library" / "the timeline should be a
media library element that can be loaded or unloaded"): a session holds
a MediaPool of named MediaLibrary bins, each with its own media entries
AND timelines (timeline.Timeline). MediaPanel's side panel picks the
current library, which is where imports, drops and New Timeline land.
A timeline stays in its library whether or not it's loaded; the
Timeline panel edits the loaded one in place (the same object), so
there's nothing to sync back. Entry and timeline ids are process-unique
counters, so MediaPool.find_entry/find_timeline can search every library.

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
next_library_id = id_counter.make_next_entry_id()

DEFAULT_LIBRARY_NAME = "Media"


class MediaEntry:
    """One catalogued media file: a name, its on-disk path, and enough
    metadata to display/sort it in media_panel.py's table -- never a
    decoded pixel array (see module docstring). source_colourspace is the
    source's INPUT color space (colour_pipeline.py): what it's converted
    from into the project working space, for stills and video alike --
    suggested from camera metadata at import, None = already in the
    working space. width/height are None only for
    an entry whose header probe failed at import (still catalogued, just
    without a Resolution column value) -- see media_panel.py's import
    error handling."""

    def __init__(self, name, source_path, source_colourspace=None,
                 source_filename=None, entry_id=None, media_kind="still",
                 width=None, height=None, frame_count=None, frame_rate=None,
                 duration=None, thumbnail=None, metadata=None):
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
        # media_probe.probe()'s header metadata (codec, timecode, camera,
        # ...) backing the table's extra columns -- see that module.
        self.metadata = dict(metadata) if metadata else {}

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
            metadata=self.metadata,
        )

    def __repr__(self):
        size = f"{self.width}x{self.height}" if self.width and self.height else None
        return (
            f"MediaEntry(id={self.id}, name={self.name!r}, "
            f"size={size}, media_kind={self.media_kind!r}, "
            f"source_colourspace={self.source_colourspace!r})"
        )


class MediaLibrary:
    """One named bin: an ordered list of MediaEntry entries plus the
    timelines that live in it (see module docstring, LIBRARIES AND
    TIMELINES)."""

    def __init__(self, entries=None, name=DEFAULT_LIBRARY_NAME, timelines=None, library_id=None):
        self.id = library_id if library_id is not None else next_library_id()
        self.name = name
        self.entries = list(entries) if entries is not None else []
        self.timelines = list(timelines) if timelines is not None else []

    def is_empty(self):
        return not self.entries and not self.timelines

    def add_timeline(self, timeline):
        self.timelines.append(timeline)
        return timeline

    def remove_timeline(self, timeline_id):
        before = len(self.timelines)
        self.timelines = [t for t in self.timelines if t.id != timeline_id]
        if len(self.timelines) == before:
            raise KeyError(f"No timeline with id={timeline_id} in library {self.name!r}")

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


class MediaPool:
    """Every MediaLibrary in a session, in side-panel order. Always holds
    at least one library -- removing the last one isn't allowed, so
    there's always somewhere for an import or drop to land."""

    def __init__(self, libraries=None):
        self.libraries = list(libraries) if libraries else [MediaLibrary()]

    def add_library(self, name):
        library = MediaLibrary(name=name)
        self.libraries.append(library)
        return library

    def remove_library(self, library_id):
        if len(self.libraries) <= 1:
            raise ValueError("A session needs at least one library.")
        before = len(self.libraries)
        self.libraries = [lib for lib in self.libraries if lib.id != library_id]
        if len(self.libraries) == before:
            raise KeyError(f"No library with id={library_id}")

    def find_library(self, library_id):
        for library in self.libraries:
            if library.id == library_id:
                return library
        raise KeyError(f"No library with id={library_id}")

    def find_entry(self, entry_id):
        for library in self.libraries:
            for entry in library.entries:
                if entry.id == entry_id:
                    return entry
        raise KeyError(f"No Media Library entry with id={entry_id}")

    def find_timeline(self, timeline_id):
        """(library, timeline) for `timeline_id`."""
        for library in self.libraries:
            for timeline in library.timelines:
                if timeline.id == timeline_id:
                    return library, timeline
        raise KeyError(f"No timeline with id={timeline_id}")

    def is_empty(self):
        return all(library.is_empty() for library in self.libraries)

    @staticmethod
    def unique_name(base, existing):
        """`base`, or "base 2", "base 3", ... -- first one not in `existing`."""
        if base not in existing:
            return base
        n = 2
        while f"{base} {n}" in existing:
            n += 1
        return f"{base} {n}"
