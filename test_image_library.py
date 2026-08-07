"""Tests for image_library.py -- run directly: python3 test_image_library.py"""
import numpy as np

from image_library import ImageLibraryEntry, ImageLibrary


def _make_array(fill=0.5, h=4, w=6):
    return np.full((h, w, 3), fill, dtype=np.float32)


def test_add_entry_stores_the_array_directly():
    library = ImageLibrary()
    array = _make_array()
    entry = ImageLibraryEntry("My Image", array, source_colourspace="sRGB",
                               source_filename="shot.png",
                               source_path="/some/path/shot.png")
    library.add_entry(entry)
    assert library.entries == [entry]
    assert entry.array is array
    assert entry.source_colourspace == "sRGB"
    assert entry.source_filename == "shot.png"
    assert entry.source_path == "/some/path/shot.png"
    print("ImageLibrary.add_entry: stores the entry with its array and "
          "source provenance intact -- OK")


def test_entry_survives_source_file_going_away():
    """Same "ingested, not referenced" guarantee as LUT Library, but
    scoped to same-session use only (see image_library.py's docstring):
    within a session, nothing about using an already-loaded entry reads
    back from source_path, so deleting/renaming the original file after
    the fact can't break it."""
    array = _make_array(fill=0.25)
    entry = ImageLibraryEntry("Imported", array,
                               source_path="/tmp/does_not_exist_at_all.png")
    # No I/O happens anywhere in ImageLibraryEntry/ImageLibrary -- source_path
    # is never opened. Confirm the array itself is exactly what was passed.
    assert entry.array is array
    assert entry.source_path == "/tmp/does_not_exist_at_all.png"
    print("ImageLibraryEntry: never reads from source_path -- an entry is "
          "self-contained for the life of the session, matching the "
          "'ingested, not referenced' design -- OK")


def test_copy_is_independent_object_same_array_reference():
    """Same convention LUTLibraryEntry.copy()/Stage.copy() use -- a
    shallow copy is fine because these arrays are treated as immutable
    (replaced, never mutated in place) everywhere in this app."""
    array = _make_array()
    entry = ImageLibraryEntry("Original", array)
    copy = entry.copy()
    assert copy.id == entry.id
    assert copy.array is entry.array
    copy.name = "Renamed Copy"
    assert entry.name == "Original", "renaming the copy shouldn't affect the original"
    print("ImageLibraryEntry.copy: independent object, same id and array "
          "reference, editing the copy's name doesn't affect the "
          "original -- OK")


def test_rename():
    library = ImageLibrary()
    entry = library.add_entry(ImageLibraryEntry("Old Name", _make_array()))
    library.rename(entry.id, "New Name")
    assert library.find(entry.id).name == "New Name"
    print("ImageLibrary.rename: updates the entry's name in place -- OK")


def test_remove():
    library = ImageLibrary()
    entry_a = library.add_entry(ImageLibraryEntry("A", _make_array()))
    library.add_entry(ImageLibraryEntry("B", _make_array()))
    library.remove(entry_a.id)
    assert len(library.entries) == 1
    assert library.entries[0].name == "B"
    try:
        library.remove(entry_a.id)
        raise AssertionError("expected KeyError removing an already-removed entry")
    except KeyError:
        pass
    print("ImageLibrary.remove: removes the right entry, raises KeyError "
          "for an unknown/already-removed id -- OK")


def test_find_raises_for_unknown_id():
    library = ImageLibrary()
    try:
        library.find(999999)
        raise AssertionError("expected KeyError for an unknown id")
    except KeyError:
        pass
    print("ImageLibrary.find: raises KeyError for an unknown id -- OK")


def test_entries_get_unique_ids():
    library = ImageLibrary()
    a = library.add_entry(ImageLibraryEntry("A", _make_array()))
    b = library.add_entry(ImageLibraryEntry("B", _make_array()))
    assert a.id != b.id
    print("ImageLibraryEntry: distinct entries get distinct ids -- OK")


def test_default_source_colourspace_is_none_meaning_unmanaged():
    """None means Unmanaged -- same meaning as SampleImagePanel's own
    _source_colourspace (see image_library.py's docstring)."""
    entry = ImageLibraryEntry("Plain", _make_array())
    assert entry.source_colourspace is None
    print("ImageLibraryEntry: source_colourspace defaults to None "
          "(Unmanaged) when not supplied -- OK")


if __name__ == "__main__":
    test_add_entry_stores_the_array_directly()
    test_entry_survives_source_file_going_away()
    test_copy_is_independent_object_same_array_reference()
    test_rename()
    test_remove()
    test_find_raises_for_unknown_id()
    test_entries_get_unique_ids()
    test_default_source_colourspace_is_none_meaning_unmanaged()
    print()
    print("All image_library tests passed.")
