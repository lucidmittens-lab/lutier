"""
image_library.py -- named reference images ingested into session memory
("Image Library"), each entry holding the FULL decoded pixel array (plus
its declared source color space, if any) rather than just a file path.
Mirrors lut_library.py's/cdl_library.py's "named, reusable shelf"
pattern -- see those modules' docstrings for the general shape -- but each
entry is a decoded (H,W,3) float32 array instead of a LUT table or a
handful of SOP/SAT floats.

WHY EMBEDDED (IN MEMORY), BUT PATH-REFERENCED ON DISK -- a deliberate split,
unlike LUT Library's fully-embedded approach: once imported, an entry's
pixel array lives in memory for the rest of the session exactly like a LUT
Library entry's table does -- moving/renaming/deleting the original file
afterward doesn't affect anything already using this entry (as either the
Sample or Reference image in sample_image_panel.py). But UNLIKE a LUT
table (a modest 33-97-per-axis grid), a decoded image can be tens of
millions of floats -- embedding that directly into the .ltr JSON the way
lut_to_embedded_dict does for LUTs would bloat a session file by orders of
magnitude for no real benefit. So session_io.py's image_library
serialization (see that module) writes only name/source_path/
source_colourspace and RE-DECODES the file fresh on session load, the same
"how to rebuild it, not the built thing" principle SampleImagePanel's own
get_session_state/load_session_state already uses for the Sample image
itself. Practical consequence, called out here rather than left as a
silent gap: an Image Library entry's "can't go missing" guarantee is a
same-session guarantee only -- across a session save/reload (or moving the
.ltr to another machine), an entry whose source file has since moved or
been deleted CAN'T be restored, same as any ordinary "lut" stage's
source_path (unlike LUT Library entries and CDL Library entries, which
really do survive that). If embedding-by-value turns out to matter more
than session-file size in practice, this would need a real design pass
(e.g. writing pixel data to a sidecar file next to the .ltr rather than
inline JSON) -- not attempted here.

Every entry is currently imported from a real file (no "New Identity
Image" equivalent to LUT's identity stages), so source_filename/
source_path are always populated for a library-imported entry -- but kept
optional on the class itself, same defensive convention lut_library.py/
cdl_library.py already use, in case that ever changes.

VIDEO ENTRIES (Media Library Phase 2, per Tom's "comprehensive media
library" request) -- media_kind distinguishes "still" (the original,
default behavior -- array is the full decoded image) from "video" (array
holds only the FIRST decoded frame, for thumbnail generation/preview
only, never the whole clip -- embedding every frame the way a still's
array embeds its one frame would be unbounded memory for a multi-second
clip). A video entry additionally carries frame_count/frame_rate/
duration (from video_source.VideoSource at import time) and is never
eligible for "Load as Reference" (see image_library_panel.py) -- Sample/
Reference have never supported video, that asymmetry is out of scope
here.

thumbnail is a media_cache.py storage descriptor (dict, see that
module), generated at import time via thumbnail.make_thumbnail_png_bytes
-- None for an entry created before this field existed (old session
load, see session_io.py), same "old file loads fine, just without the
new bit" tolerance every prior additive field in this app's session
formats already has.

Qt-free, fully testable without a display -- same convention as
lut_library.py/cdl_library.py/stack_memory.py.
"""
import id_counter

MEDIA_KINDS = ("still", "video")

next_entry_id = id_counter.make_next_entry_id()


class ImageLibraryEntry:
    """One saved reference image: a name, the decoded (H,W,3) float32 pixel
    array (domain [0,1], same "raw numeric domain" convention
    app_logic.load_image_as_array uses everywhere else), and its declared
    source color space if one was set at import/adapt time (None means
    Unmanaged, same meaning as SampleImagePanel's own
    _source_colourspace). source_filename/source_path are DISPLAY-ONLY
    provenance for an entry imported from a real file (see module
    docstring on why they're also load-bearing for session persistence,
    unlike LUT Library's identical-looking but purely-cosmetic fields)."""

    def __init__(self, name, array, source_colourspace=None,
                 source_filename=None, source_path=None, entry_id=None,
                 media_kind="still", frame_count=None, frame_rate=None,
                 duration=None, thumbnail=None):
        self.id = entry_id if entry_id is not None else next_entry_id()
        self.name = name
        self.array = array
        self.source_colourspace = source_colourspace
        self.source_filename = source_filename
        self.source_path = source_path
        self.media_kind = media_kind
        self.frame_count = frame_count
        self.frame_rate = frame_rate
        self.duration = duration
        self.thumbnail = thumbnail

    @property
    def is_video(self):
        return self.media_kind == "video"

    def copy(self):
        return ImageLibraryEntry(
            self.name, self.array, source_colourspace=self.source_colourspace,
            source_filename=self.source_filename, source_path=self.source_path,
            entry_id=self.id, media_kind=self.media_kind,
            frame_count=self.frame_count, frame_rate=self.frame_rate,
            duration=self.duration, thumbnail=self.thumbnail,
        )

    def __repr__(self):
        shape = tuple(self.array.shape) if self.array is not None else None
        return (
            f"ImageLibraryEntry(id={self.id}, name={self.name!r}, "
            f"shape={shape}, media_kind={self.media_kind!r}, "
            f"source_colourspace={self.source_colourspace!r})"
        )


class ImageLibrary:
    """An ordered list of ImageLibraryEntry entries."""

    def __init__(self, entries=None):
        self.entries = list(entries) if entries is not None else []

    def add_entry(self, entry):
        """Add an already-built ImageLibraryEntry directly -- used by
        image_library_panel.py's import path (one entry per imported
        file)."""
        self.entries.append(entry)
        return entry

    def rename(self, entry_id, new_name):
        self.find(entry_id).name = new_name

    def remove(self, entry_id):
        before = len(self.entries)
        self.entries = [e for e in self.entries if e.id != entry_id]
        if len(self.entries) == before:
            raise KeyError(f"No Image Library entry with id={entry_id}")

    def find(self, entry_id):
        for entry in self.entries:
            if entry.id == entry_id:
                return entry
        raise KeyError(f"No Image Library entry with id={entry_id}")
