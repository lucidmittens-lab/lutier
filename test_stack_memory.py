"""Tests for stack_memory.py -- run directly: python3 test_stack_memory.py

Fully Qt-free.
"""
import stack as st
from stack_memory import (
    StackMemory, StackMemoryLibrary, GalleryStill, GalleryStillLibrary,
)


def _make_stack():
    stack = st.Stack()
    stack.add_stage(st.make_cdl_stage(slope=(1.1, 1.0, 0.9)))
    return stack


def test_save_creates_a_memory_with_an_independent_stack_copy():
    library = StackMemoryLibrary()
    stack = _make_stack()
    memory = library.save("My Grade", stack)
    assert memory.stack is not stack
    stack.add_stage(st.make_cdl_stage(slope=(2.0, 2.0, 2.0)))
    assert len(memory.stack.stages) == 1, "editing the working stack after save must not affect the saved memory"
    print("StackMemoryLibrary.save: takes an independent copy, later edits "
          "to the working stack don't retroactively change it -- OK")


def test_save_with_no_thumbnail_defaults_to_none():
    library = StackMemoryLibrary()
    memory = library.save("Untitled", _make_stack())
    assert memory.thumbnail is None
    assert memory.full_still is None
    print("StackMemoryLibrary.save: thumbnail and full_still both default "
          "to None when not passed -- OK")


def test_save_stores_a_full_still_descriptor():
    full_still = {"mode": "embedded", "data_b64": "ZnVsbA=="}
    library = StackMemoryLibrary()
    memory = library.save("Graded", _make_stack(), full_still=full_still)
    assert memory.full_still == full_still
    print("StackMemoryLibrary.save: stores the full_still descriptor "
          "it's handed -- OK")


def test_update_replaces_full_still_when_given_one():
    old_still = {"mode": "embedded", "data_b64": "old"}
    new_still = {"mode": "embedded", "data_b64": "new"}
    library = StackMemoryLibrary()
    memory = library.save("Grade", _make_stack(), full_still=old_still)
    library.update(memory.id, _make_stack(), full_still=new_still)
    assert library.find(memory.id).full_still == new_still
    print("StackMemoryLibrary.update: replaces full_still when a new one "
          "is passed -- OK")


def test_update_without_a_full_still_keeps_the_existing_one():
    full_still = {"mode": "embedded", "data_b64": "keep-me"}
    library = StackMemoryLibrary()
    memory = library.save("Grade", _make_stack(), full_still=full_still)
    library.update(memory.id, _make_stack())
    assert library.find(memory.id).full_still == full_still
    print("StackMemoryLibrary.update: leaves the existing full_still "
          "alone when no new one is passed -- OK")


def test_memory_copy_preserves_full_still():
    full_still = {"mode": "cached", "filename": "full.png"}
    memory = StackMemory("Grade", _make_stack(), full_still=full_still)
    copy = memory.copy()
    assert copy.full_still == full_still
    print("StackMemory.copy: preserves the full_still descriptor -- OK")


def test_save_stores_a_thumbnail_descriptor():
    thumb = {"mode": "embedded", "data_b64": "Zm9v"}
    library = StackMemoryLibrary()
    memory = library.save("Graded", _make_stack(), thumbnail=thumb)
    assert memory.thumbnail == thumb
    print("StackMemoryLibrary.save: stores the thumbnail descriptor it's "
          "handed -- OK")


def test_update_replaces_thumbnail_when_given_one():
    old_thumb = {"mode": "embedded", "data_b64": "old"}
    new_thumb = {"mode": "embedded", "data_b64": "new"}
    library = StackMemoryLibrary()
    memory = library.save("Grade", _make_stack(), thumbnail=old_thumb)
    library.update(memory.id, _make_stack(), thumbnail=new_thumb)
    assert library.find(memory.id).thumbnail == new_thumb
    print("StackMemoryLibrary.update: replaces the thumbnail when a new "
          "one is passed -- OK")


def test_update_without_a_thumbnail_keeps_the_existing_one():
    thumb = {"mode": "embedded", "data_b64": "keep-me"}
    library = StackMemoryLibrary()
    memory = library.save("Grade", _make_stack(), thumbnail=thumb)
    library.update(memory.id, _make_stack())
    assert library.find(memory.id).thumbnail == thumb
    print("StackMemoryLibrary.update: leaves the existing thumbnail alone "
          "when no new one is passed -- OK")


def test_memory_copy_preserves_thumbnail():
    thumb = {"mode": "cached", "filename": "abc.png"}
    memory = StackMemory("Grade", _make_stack(), thumbnail=thumb)
    copy = memory.copy()
    assert copy.thumbnail == thumb
    assert copy.id == memory.id
    print("StackMemory.copy: preserves the thumbnail descriptor -- OK")


def test_memory_and_gallery_still_ids_never_collide():
    """Both classes share one id counter (see next_memory_id/
    next_gallery_still_id's own docstrings) -- a memory and a still
    created back to back must never get the same id, since they live as
    rows in the same union list (stack_memories_panel.py)."""
    memory_library = StackMemoryLibrary()
    still_library = GalleryStillLibrary()
    memory = memory_library.save("Grade", _make_stack())
    still = still_library.add_still(GalleryStill("Ref", None, "/some/path.png"))
    another_memory = memory_library.save("Grade 2", _make_stack())
    ids = [memory.id, still.id, another_memory.id]
    assert len(set(ids)) == 3
    print("StackMemory/GalleryStill: share one id counter, never collide "
          "-- OK")


def test_gallery_still_library_add_rename_remove_find():
    library = GalleryStillLibrary()
    still = library.add_still(GalleryStill("Ref Shot", None, "/tmp/ref.png",
                                            source_filename="ref.png"))
    assert library.find(still.id) is still

    library.rename(still.id, "Renamed Ref")
    assert library.find(still.id).name == "Renamed Ref"

    library.remove(still.id)
    assert len(library.stills) == 0
    try:
        library.find(still.id)
        raise AssertionError("expected KeyError for a removed still")
    except KeyError:
        pass
    print("GalleryStillLibrary: add_still/rename/remove/find behave like "
          "MediaLibrary's equivalent methods -- OK")


def test_gallery_still_copy_is_independent_with_same_id():
    still = GalleryStill("Original", {"mode": "embedded", "data_b64": "x"},
                          "/tmp/ref.png", source_filename="ref.png")
    copy = still.copy()
    assert copy.id == still.id
    assert copy.source_path == still.source_path
    copy.name = "Renamed Copy"
    assert still.name == "Original"
    print("GalleryStill.copy: independent object, same id, editing the "
          "copy's name doesn't affect the original -- OK")


if __name__ == "__main__":
    test_save_creates_a_memory_with_an_independent_stack_copy()
    test_save_with_no_thumbnail_defaults_to_none()
    test_save_stores_a_thumbnail_descriptor()
    test_save_stores_a_full_still_descriptor()
    test_update_replaces_thumbnail_when_given_one()
    test_update_without_a_thumbnail_keeps_the_existing_one()
    test_update_replaces_full_still_when_given_one()
    test_update_without_a_full_still_keeps_the_existing_one()
    test_memory_copy_preserves_thumbnail()
    test_memory_copy_preserves_full_still()
    test_memory_and_gallery_still_ids_never_collide()
    test_gallery_still_library_add_rename_remove_find()
    test_gallery_still_copy_is_independent_with_same_id()
    print()
    print("All stack_memory tests passed.")
