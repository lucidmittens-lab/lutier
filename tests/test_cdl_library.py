"""Tests for cdl_library.py -- run directly: python3 -m tests.test_cdl_library"""
from cdl_library import CDLEntry, CDLLibrary


def test_save_creates_entry_with_given_values():
    library = CDLLibrary()
    entry = library.save(
        "Shot 010", (1.1, 1.0, 0.9), (0.02, 0.0, -0.01), (1.0, 1.05, 1.0), 1.1)
    assert len(library.entries) == 1
    assert library.entries[0] is entry
    assert entry.name == "Shot 010"
    assert entry.slope == (1.1, 1.0, 0.9)
    assert entry.offset == (0.02, 0.0, -0.01)
    assert entry.power == (1.0, 1.05, 1.0)
    assert entry.sat == 1.1
    assert entry.style == "asc"
    assert entry.sop_enabled is True and entry.sat_enabled is True
    assert entry.source_filename is None and entry.source_path is None
    print("CDLLibrary.save: creates an entry with the given values, no "
          "source provenance for a directly-saved entry -- OK")


def test_add_entry_records_source_provenance():
    library = CDLLibrary()
    entry = CDLEntry(
        "Imported Shot", (1.2, 1.2, 1.2), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0), 1.0,
        source_filename="grade.cc", source_path="/some/path/grade.cc")
    library.add_entry(entry)
    assert library.entries == [entry]
    assert entry.source_filename == "grade.cc"
    assert entry.source_path == "/some/path/grade.cc"
    print("CDLLibrary.add_entry: preserves source_filename/source_path "
          "for an imported entry -- OK")


def test_entries_are_independent_of_source_after_creation():
    """The whole point of "copied in, not referenced" (per Tom): once a
    CDLEntry exists, it has no live connection to wherever its values
    came from -- mutating the ORIGINAL tuples/values passed in after
    construction should never retroactively change the stored entry."""
    slope = [1.1, 1.0, 0.9]  # a mutable list, deliberately
    entry = CDLEntry("Test", slope, (0.0, 0.0, 0.0), (1.0, 1.0, 1.0), 1.0)
    assert entry.slope == (1.1, 1.0, 0.9)
    slope[0] = 99.0  # mutate the original list after the fact
    assert entry.slope == (1.1, 1.0, 0.9), \
        "CDLEntry should have copied slope into its own tuple, not aliased the list"
    print("CDLEntry: converts slope/offset/power to its own tuples at "
          "construction time -- mutating the caller's original list "
          "afterward doesn't affect the stored entry -- OK")


def test_copy_is_independent():
    entry = CDLEntry("Original", (1.0, 1.0, 1.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0), 1.0)
    copy = entry.copy()
    assert copy.id == entry.id
    assert copy.name == entry.name
    copy.name = "Renamed Copy"
    assert entry.name == "Original", "renaming the copy shouldn't affect the original"
    print("CDLEntry.copy: independent object, same id, editing the copy "
          "doesn't affect the original -- OK")


def test_rename():
    library = CDLLibrary()
    entry = library.save("Old Name", (1, 1, 1), (0, 0, 0), (1, 1, 1), 1.0)
    library.rename(entry.id, "New Name")
    assert library.find(entry.id).name == "New Name"
    print("CDLLibrary.rename: updates the entry's name in place -- OK")


def test_remove():
    library = CDLLibrary()
    entry = library.save("A", (1, 1, 1), (0, 0, 0), (1, 1, 1), 1.0)
    library.save("B", (1, 1, 1), (0, 0, 0), (1, 1, 1), 1.0)
    library.remove(entry.id)
    assert len(library.entries) == 1
    assert library.entries[0].name == "B"
    try:
        library.remove(entry.id)
        raise AssertionError("expected KeyError removing an already-removed entry")
    except KeyError:
        pass
    print("CDLLibrary.remove: removes the right entry, raises KeyError for "
          "an unknown/already-removed id -- OK")


def test_find_raises_for_unknown_id():
    library = CDLLibrary()
    try:
        library.find(999999)
        raise AssertionError("expected KeyError for an unknown id")
    except KeyError:
        pass
    print("CDLLibrary.find: raises KeyError for an unknown id -- OK")


def test_bypass_flags_round_trip_through_entry():
    library = CDLLibrary()
    entry = library.save(
        "Bypassed", (1, 1, 1), (0, 0, 0), (1, 1, 1), 1.0,
        style="no_clip", sop_enabled=False, sat_enabled=False)
    assert entry.style == "no_clip"
    assert entry.sop_enabled is False
    assert entry.sat_enabled is False
    print("CDLLibrary.save: style/sop_enabled/sat_enabled are stored "
          "exactly as given -- OK")


if __name__ == "__main__":
    test_save_creates_entry_with_given_values()
    test_add_entry_records_source_provenance()
    test_entries_are_independent_of_source_after_creation()
    test_copy_is_independent()
    test_rename()
    test_remove()
    test_find_raises_for_unknown_id()
    test_bypass_flags_round_trip_through_entry()
    print()
    print("All cdl_library tests passed.")
