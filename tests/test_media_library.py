"""Tests for media_library.py -- run directly: python3 -m tests.test_media_library"""
from media_library import MediaEntry, MediaLibrary, MediaPool
import timeline as tl


def test_add_entry_stores_path_and_metadata_not_pixels():
    library = MediaLibrary()
    entry = MediaEntry("My Image", "/some/path/shot.png", source_colourspace="sRGB",
                        source_filename="shot.png", width=640, height=480)
    library.add_entry(entry)
    assert library.entries == [entry]
    assert not hasattr(entry, "array"), "a MediaEntry must never carry a decoded pixel array"
    assert entry.source_path == "/some/path/shot.png"
    assert entry.source_colourspace == "sRGB"
    assert entry.source_filename == "shot.png"
    assert entry.width == 640 and entry.height == 480
    print("MediaLibrary.add_entry: stores the entry's path and metadata, "
          "no pixel array -- OK")


def test_entry_never_reads_its_own_source_path():
    """Path-referenced, not embedded -- see media_library.py's docstring.
    Constructing/copying/renaming/removing an entry never opens its
    source_path, so a MediaEntry for a file that's since moved/been
    deleted stays perfectly usable AS A ROW until something actually
    tries to load its pixels (media_panel.py/sample_image_panel.py)."""
    entry = MediaEntry("Imported", "/tmp/does_not_exist_at_all.png",
                        width=100, height=100)
    assert entry.source_path == "/tmp/does_not_exist_at_all.png"
    entry.copy()
    print("MediaEntry: never reads from source_path just by existing -- OK")


def test_copy_is_independent_object_same_id():
    entry = MediaEntry("Original", "/tmp/a.png", width=10, height=10)
    copy = entry.copy()
    assert copy.id == entry.id
    assert copy is not entry
    copy.name = "Renamed Copy"
    assert entry.name == "Original", "renaming the copy shouldn't affect the original"
    print("MediaEntry.copy: independent object, same id, editing the "
          "copy's name doesn't affect the original -- OK")


def test_rename():
    library = MediaLibrary()
    entry = library.add_entry(MediaEntry("Old Name", "/tmp/a.png"))
    library.rename(entry.id, "New Name")
    assert library.find(entry.id).name == "New Name"
    print("MediaLibrary.rename: updates the entry's name in place -- OK")


def test_remove():
    library = MediaLibrary()
    entry_a = library.add_entry(MediaEntry("A", "/tmp/a.png"))
    library.add_entry(MediaEntry("B", "/tmp/b.png"))
    library.remove(entry_a.id)
    assert len(library.entries) == 1
    assert library.entries[0].name == "B"
    try:
        library.remove(entry_a.id)
        raise AssertionError("expected KeyError removing an already-removed entry")
    except KeyError:
        pass
    print("MediaLibrary.remove: removes the right entry, raises KeyError "
          "for an unknown/already-removed id -- OK")


def test_find_raises_for_unknown_id():
    library = MediaLibrary()
    try:
        library.find(999999)
        raise AssertionError("expected KeyError for an unknown id")
    except KeyError:
        pass
    print("MediaLibrary.find: raises KeyError for an unknown id -- OK")


def test_entries_get_unique_ids():
    library = MediaLibrary()
    a = library.add_entry(MediaEntry("A", "/tmp/a.png"))
    b = library.add_entry(MediaEntry("B", "/tmp/b.png"))
    assert a.id != b.id
    print("MediaEntry: distinct entries get distinct ids -- OK")


def test_default_source_colourspace_is_none_meaning_unmanaged():
    """None means Unmanaged -- same meaning as SampleImagePanel's own
    _source_colourspace (see media_library.py's docstring)."""
    entry = MediaEntry("Plain", "/tmp/a.png")
    assert entry.source_colourspace is None
    print("MediaEntry: source_colourspace defaults to None (Unmanaged) "
          "when not supplied -- OK")


def test_default_media_kind_is_still_with_no_video_metadata():
    entry = MediaEntry("Plain", "/tmp/a.png")
    assert entry.media_kind == "still"
    assert entry.is_video is False
    assert entry.frame_count is None
    assert entry.frame_rate is None
    assert entry.duration is None
    assert entry.thumbnail is None
    assert entry.width is None and entry.height is None
    print("MediaEntry: media_kind defaults to 'still' with no video "
          "metadata/thumbnail/resolution -- OK")


def test_video_entry_carries_metadata_and_thumbnail():
    thumb = {"mode": "embedded", "data_b64": "Zm9v"}
    entry = MediaEntry(
        "Clip", "/tmp/clip.mp4", media_kind="video", width=1920, height=1080,
        frame_count=120, frame_rate=24.0, duration=5.0, thumbnail=thumb)
    assert entry.is_video is True
    assert entry.width == 1920 and entry.height == 1080
    assert entry.frame_count == 120
    assert entry.frame_rate == 24.0
    assert entry.duration == 5.0
    assert entry.thumbnail == thumb
    print("MediaEntry: a video entry carries width/height/frame_count/"
          "frame_rate/duration/thumbnail and reports is_video -- OK")


def test_copy_preserves_media_kind_and_video_metadata():
    thumb = {"mode": "cached", "filename": "abc.png"}
    entry = MediaEntry(
        "Clip", "/tmp/clip.mp4", media_kind="video", width=1280, height=720,
        frame_count=10, frame_rate=30.0, duration=0.33, thumbnail=thumb)
    copy = entry.copy()
    assert copy.media_kind == "video"
    assert copy.width == 1280 and copy.height == 720
    assert copy.frame_count == 10
    assert copy.frame_rate == 30.0
    assert copy.duration == 0.33
    assert copy.thumbnail == thumb
    print("MediaEntry.copy: preserves media_kind, resolution, and video "
          "metadata/thumbnail -- OK")


def test_media_pool_finds_entries_and_timelines_across_libraries():
    pool = MediaPool()
    assert len(pool.libraries) == 1 and pool.is_empty()
    selects = pool.add_library("Selects")
    entry = selects.add_entry(MediaEntry("Shot", "/a.mov", media_kind="video"))
    timeline = selects.add_timeline(tl.Timeline(name="Cut"))
    assert pool.find_entry(entry.id) is entry
    assert pool.find_timeline(timeline.id) == (selects, timeline)
    assert not pool.is_empty()
    selects.remove_timeline(timeline.id)
    assert selects.timelines == []
    print("MediaPool: finds entries/timelines in any library -- OK")


def test_media_pool_keeps_at_least_one_library():
    pool = MediaPool()
    try:
        pool.remove_library(pool.libraries[0].id)
    except ValueError:
        pass
    else:
        raise AssertionError("removing the last library should raise")
    extra = pool.add_library("Extra")
    pool.remove_library(extra.id)
    assert [lib.name for lib in pool.libraries] == ["Media"]
    assert MediaPool.unique_name("Timeline", ["Timeline", "Timeline 2"]) == "Timeline 3"
    print("MediaPool: the last library can't be removed -- OK")


if __name__ == "__main__":
    test_add_entry_stores_path_and_metadata_not_pixels()
    test_entry_never_reads_its_own_source_path()
    test_copy_is_independent_object_same_id()
    test_rename()
    test_remove()
    test_find_raises_for_unknown_id()
    test_entries_get_unique_ids()
    test_default_source_colourspace_is_none_meaning_unmanaged()
    test_default_media_kind_is_still_with_no_video_metadata()
    test_video_entry_carries_metadata_and_thumbnail()
    test_copy_preserves_media_kind_and_video_metadata()
    test_media_pool_finds_entries_and_timelines_across_libraries()
    test_media_pool_keeps_at_least_one_library()
    print()
    print("All media_library tests passed.")
