"""
lut_library.py -- named LUTs ingested into session memory ("LUT Library"),
each entry holding the FULL loaded LUT object (table + domain + name)
rather than a path reference. Mirrors cdl_library.py's "named, reusable
shelf" pattern -- see that module's docstring for the general shape --
but each entry is a whole LUT (colour.LUT1D/LUT3D) rather than a handful
of SOP/SAT floats.

WHY EMBEDDED, NOT REFERENCED (per Tom): a "lut" Stack stage normally
reloads its file from source_path every session open (see session_io.py's
docstring on LUT stage provenance) -- the right call there, since
re-reading the file is cheap and keeps a giant baked table out of the
.ltr JSON for the common case. But that also means a session moved to a
different machine, or just a folder that got reorganized, silently
breaks any stage whose source file isn't at that same path anymore.

Importing a LUT into this library instead reads the file ONCE, right
here, and keeps the loaded LUT object itself (not just its path) for the
rest of the session -- and, critically, session_io.py serializes a
library entry's table/domain directly into the .ltr file (see
lut_to_embedded_dict/lut_library_to_session_list there), and a Stack
stage added FROM this library (stack_panel.add_lut_stage_from_library_entry)
is tagged source_kind="embedded" so it gets the same table-in-the-file
treatment independent of what happens to the library entry afterward.
Either way, once a LUT has been ingested here, nothing about using it
again -- in this session or a reopened one, on this machine or another --
depends on the original file still existing at that path.

source_filename/source_path are recorded ONLY as on-screen provenance for
an entry imported from a real file, exactly like cdl_library.CDLEntry --
neither is ever read from again.

Qt-free, fully testable without a display -- same convention as
stack_memory.py/cdl_library.py.
"""
import id_counter

next_entry_id = id_counter.make_next_entry_id()


class LUTLibraryEntry:
    """One saved LUT: a name plus the actual loaded LUT object (anything
    with .table/.domain -- colour.LUT1D or colour.LUT3D, same "any
    LUT-like object" convention app_logic.py/stack.py already use).
    source_filename/source_path are optional, DISPLAY-ONLY provenance for
    an entry imported from a real file (see module docstring)."""

    def __init__(self, name, lut, source_filename=None, source_path=None, entry_id=None):
        self.id = entry_id if entry_id is not None else next_entry_id()
        self.name = name
        self.lut = lut
        self.source_filename = source_filename
        self.source_path = source_path

    def copy(self):
        return LUTLibraryEntry(
            self.name, self.lut,
            source_filename=self.source_filename, source_path=self.source_path,
            entry_id=self.id,
        )

    def __repr__(self):
        table = getattr(self.lut, "table", None)
        shape = tuple(table.shape) if table is not None else None
        return f"LUTLibraryEntry(id={self.id}, name={self.name!r}, table_shape={shape})"


class LUTLibrary:
    """An ordered list of LUTLibraryEntry entries."""

    def __init__(self, entries=None):
        self.entries = list(entries) if entries is not None else []

    def add_entry(self, entry):
        """Add an already-built LUTLibraryEntry directly -- used by
        lut_library_panel.py's import path (one entry per imported
        file)."""
        self.entries.append(entry)
        return entry

    def rename(self, entry_id, new_name):
        self.find(entry_id).name = new_name

    def remove(self, entry_id):
        before = len(self.entries)
        self.entries = [e for e in self.entries if e.id != entry_id]
        if len(self.entries) == before:
            raise KeyError(f"No LUT Library entry with id={entry_id}")

    def find(self, entry_id):
        for entry in self.entries:
            if entry.id == entry_id:
                return entry
        raise KeyError(f"No LUT Library entry with id={entry_id}")
