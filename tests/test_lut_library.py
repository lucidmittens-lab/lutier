"""Tests for lut_library.py -- run directly: python3 -m tests.test_lut_library"""
import numpy as np
import colour

from lut_library import LUTLibraryEntry, LUTLibrary


def _make_lut3d(gain=2.0, size=3, name="test_lut"):
    identity = colour.LUT3D(size=size).table
    return colour.LUT3D(table=identity * gain, size=size, name=name)


def test_add_entry_stores_the_lut_object_directly():
    library = LUTLibrary()
    lut = _make_lut3d()
    entry = LUTLibraryEntry("My LUT", lut, source_filename="grade.cube",
                             source_path="/some/path/grade.cube")
    library.add_entry(entry)
    assert library.entries == [entry]
    assert entry.lut is lut
    assert entry.source_filename == "grade.cube"
    assert entry.source_path == "/some/path/grade.cube"
    print("LUTLibrary.add_entry: stores the entry with its LUT and "
          "source provenance intact -- OK")


def test_entry_survives_source_file_going_away():
    """The whole point (per Tom, "can't go offline"): once ingested, an
    entry's LUT is the entry's own -- nothing about using it again reads
    back from source_path, so deleting/renaming the original file after
    the fact can't break it."""
    lut = _make_lut3d(gain=1.5)
    entry = LUTLibraryEntry("Imported", lut, source_path="/tmp/does_not_exist_at_all.cube")
    # No I/O happens anywhere in LUTLibraryEntry/LUTLibrary -- source_path
    # is never opened. Confirm the LUT itself is exactly what was passed.
    assert entry.lut.table.tolist() == lut.table.tolist()
    assert entry.source_path == "/tmp/does_not_exist_at_all.cube"
    print("LUTLibraryEntry: never reads from source_path -- an entry is "
          "self-contained once created, matching the 'ingested, not "
          "referenced' design -- OK")


def test_copy_is_independent_object_same_lut_reference():
    """Same convention Stage.copy() uses (see stack.py) -- a shallow copy
    of params is fine because LUT objects are treated as immutable
    (replaced, never mutated in place) everywhere in this app."""
    lut = _make_lut3d()
    entry = LUTLibraryEntry("Original", lut)
    copy = entry.copy()
    assert copy.id == entry.id
    assert copy.lut is entry.lut
    copy.name = "Renamed Copy"
    assert entry.name == "Original", "renaming the copy shouldn't affect the original"
    print("LUTLibraryEntry.copy: independent object, same id and lut "
          "reference, editing the copy's name doesn't affect the "
          "original -- OK")


def test_rename():
    library = LUTLibrary()
    entry = library.add_entry(LUTLibraryEntry("Old Name", _make_lut3d()))
    library.rename(entry.id, "New Name")
    assert library.find(entry.id).name == "New Name"
    print("LUTLibrary.rename: updates the entry's name in place -- OK")


def test_remove():
    library = LUTLibrary()
    entry_a = library.add_entry(LUTLibraryEntry("A", _make_lut3d()))
    library.add_entry(LUTLibraryEntry("B", _make_lut3d()))
    library.remove(entry_a.id)
    assert len(library.entries) == 1
    assert library.entries[0].name == "B"
    try:
        library.remove(entry_a.id)
        raise AssertionError("expected KeyError removing an already-removed entry")
    except KeyError:
        pass
    print("LUTLibrary.remove: removes the right entry, raises KeyError "
          "for an unknown/already-removed id -- OK")


def test_find_raises_for_unknown_id():
    library = LUTLibrary()
    try:
        library.find(999999)
        raise AssertionError("expected KeyError for an unknown id")
    except KeyError:
        pass
    print("LUTLibrary.find: raises KeyError for an unknown id -- OK")


def test_entries_get_unique_ids():
    library = LUTLibrary()
    a = library.add_entry(LUTLibraryEntry("A", _make_lut3d()))
    b = library.add_entry(LUTLibraryEntry("B", _make_lut3d()))
    assert a.id != b.id
    print("LUTLibraryEntry: distinct entries get distinct ids -- OK")


if __name__ == "__main__":
    test_add_entry_stores_the_lut_object_directly()
    test_entry_survives_source_file_going_away()
    test_copy_is_independent_object_same_lut_reference()
    test_rename()
    test_remove()
    test_find_raises_for_unknown_id()
    test_entries_get_unique_ids()
    print()
    print("All lut_library tests passed.")
