"""
id_counter.py -- the one shared "monotonically increasing int, unique for
the life of the process" id-generator shape used across this app's
in-memory library/collection classes (CDLEntry, LUTLibraryEntry,
MediaEntry, ...) for GUI selection tracking -- picking a row back
out of a QListWidget/QTableWidget by identity rather than by fragile
index, which shifts under reorder/delete/sort.

Previously reimplemented byte-for-byte in cdl_library.py, lut_library.py,
and media_library.py (each its own `itertools.count(1)` + a `next_entry_
id()` wrapper, only the cross-reference in each docstring actually
differing) -- consolidated here (code review) rather than left
triplicated. make_next_entry_id() returns a FRESH counter-backed
callable per call, so each caller still gets its own independent
per-registry counter (a CDLEntry and a LUTLibraryEntry can share the same
numeric id -- fine, since nothing ever compares ids ACROSS registries,
only within one list at a time), exactly preserving each module's
previous behavior.

stack.py's own next_stage_id() and stack_memory.py's next_memory_id()/
next_gallery_still_id() (the latter two deliberately SHARING one counter,
so a StackMemory and a GalleryStill can never collide on id even though
they're both rows in the same union list) are the same basic idea but
don't fit this simple one-counter-one-function shape -- left as their own
implementations rather than forced through here.

Qt-free, fully testable without a display -- same convention as every
other backend module in this app.
"""
import itertools


def make_next_entry_id():
    """Returns a fresh `next_entry_id()` callable, backed by its OWN new
    itertools.count(1) -- call this once per registry module (at import
    time, module-level, same as the itertools.count(1) it replaces) and
    keep the returned callable as that module's own `next_entry_id`."""
    counter = itertools.count(1)

    def next_entry_id():
        return next(counter)

    return next_entry_id
