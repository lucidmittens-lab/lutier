"""
cdl_library.py -- named, saved ASC CDL grades ("CDL Library") that live
alongside the working Stack for the life of a session, saved/restored as
part of a .ltr session file (see session_io.py's cdl_library_to_session_list
/cdl_library_from_session_list). Mirrors stack_memory.py's "named shelf of
reusable presets" pattern, but each entry is a single CDL's SOP/SAT values
rather than a whole Stack -- e.g. a shot's on-set CDL, or a look you want
to reuse across several shots' own CDL stages.

IMPORTED CDLs ARE COPIED IN, NOT REFERENCED (per Tom): unlike a "lut"
stage, which stores source_path and RELOADS the file each session open
(see session_io.py's docstring on LUT stage provenance -- the right call
there since a LUT's full table isn't something you'd want to embed
verbatim), a CDLEntry imported from a .cc/.cdl/.ccc file has its parsed
SOP/SAT VALUES copied in directly, right here, at import time (see
cdl_file_io.py for the actual file parsing, via OCIO's own
CDLTransform.CreateGroupFromFile). source_filename/source_path are
recorded ONLY as on-screen reference for the entry (so you can tell which
file it came from) -- neither is ever read from again, so a CDL Library
entry can never go missing or break just because the original file was
moved, renamed, or deleted, unlike a lut stage's source_path. A handful of
floats is cheap enough to just store outright; there's no size trade-off
here the way there would be for embedding a whole baked LUT table.

Qt-free, fully testable without a display -- same convention as
stack_memory.py.
"""
import itertools

_id_counter = itertools.count(1)


def next_entry_id():
    """Monotonically increasing int, unique for the life of the process --
    used as an entry's identity for GUI selection tracking, same reasoning
    as stack.next_stage_id()/stack_memory.next_memory_id()."""
    return next(_id_counter)


class CDLEntry:
    """One saved CDL grade: a name plus its own SOP (slope/offset/power)
    and SAT values, style, and bypass flags -- never a reference to a
    Stage or a file (see module docstring). source_filename/source_path
    are optional, DISPLAY-ONLY provenance for an entry that came from an
    imported file (both None for an entry saved directly from a CDL stage
    in the Stack)."""

    def __init__(self, name, slope, offset, power, sat, style="asc",
                 sop_enabled=True, sat_enabled=True,
                 source_filename=None, source_path=None, entry_id=None):
        self.id = entry_id if entry_id is not None else next_entry_id()
        self.name = name
        self.slope = tuple(slope)
        self.offset = tuple(offset)
        self.power = tuple(power)
        self.sat = float(sat)
        self.style = style
        self.sop_enabled = sop_enabled
        self.sat_enabled = sat_enabled
        self.source_filename = source_filename
        self.source_path = source_path

    def copy(self):
        return CDLEntry(
            self.name, self.slope, self.offset, self.power, self.sat,
            style=self.style, sop_enabled=self.sop_enabled, sat_enabled=self.sat_enabled,
            source_filename=self.source_filename, source_path=self.source_path,
            entry_id=self.id,
        )

    def __repr__(self):
        return (f"CDLEntry(id={self.id}, name={self.name!r}, slope={self.slope}, "
                f"offset={self.offset}, power={self.power}, sat={self.sat})")


class CDLLibrary:
    """An ordered list of CDLEntry entries."""

    def __init__(self, entries=None):
        self.entries = list(entries) if entries is not None else []

    def save(self, name, slope, offset, power, sat, style="asc",
             sop_enabled=True, sat_enabled=True,
             source_filename=None, source_path=None):
        """Add a NEW entry with these values directly (see module
        docstring -- there's no "stack" object here to copy-on-write from,
        every value is a plain immutable float/tuple already)."""
        entry = CDLEntry(
            name, slope, offset, power, sat, style=style,
            sop_enabled=sop_enabled, sat_enabled=sat_enabled,
            source_filename=source_filename, source_path=source_path,
        )
        self.entries.append(entry)
        return entry

    def add_entry(self, entry):
        """Add an already-built CDLEntry directly -- used by
        stack_memories_panel.py's CDL import path, which builds one
        CDLEntry per ColorCorrection found in an imported file (see
        cdl_file_io.py)."""
        self.entries.append(entry)
        return entry

    def rename(self, entry_id, new_name):
        self.find(entry_id).name = new_name

    def remove(self, entry_id):
        before = len(self.entries)
        self.entries = [e for e in self.entries if e.id != entry_id]
        if len(self.entries) == before:
            raise KeyError(f"No CDL entry with id={entry_id}")

    def find(self, entry_id):
        for entry in self.entries:
            if entry.id == entry_id:
                return entry
        raise KeyError(f"No CDL entry with id={entry_id}")
