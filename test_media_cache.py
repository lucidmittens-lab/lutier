"""Tests for media_cache.py -- run directly: python3 test_media_cache.py

Fully Qt-free. Redirects MEDIA_CACHE_PREFS_PATH to a scratch temp dir so
these tests never touch the real ~/Library/Application Support/LUTier.
"""
import shutil
import tempfile
from pathlib import Path

import media_cache as mc

_SCRATCH = Path(tempfile.mkdtemp(prefix="lutier-media-cache-test-"))


def _reset():
    mc.MEDIA_CACHE_PREFS_PATH = _SCRATCH / "prefs" / "media_cache_prefs.json"


def test_get_cache_dir_defaults_to_none_when_nothing_saved():
    _reset()
    assert mc.get_cache_dir() is None
    print("media_cache.get_cache_dir: defaults to None (embed-in-session) "
          "-- OK")


def test_set_and_get_cache_dir_round_trips():
    _reset()
    target = _SCRATCH / "my_cache"
    mc.set_cache_dir(target)
    assert mc.get_cache_dir() == target
    print("media_cache.set_cache_dir/get_cache_dir: round-trips a chosen "
          "folder -- OK")


def test_set_cache_dir_none_clears_it():
    _reset()
    mc.set_cache_dir(_SCRATCH / "my_cache")
    mc.set_cache_dir(None)
    assert mc.get_cache_dir() is None
    print("media_cache.set_cache_dir(None): clears back to embed-in-"
          "session mode -- OK")


def test_store_and_load_thumbnail_embedded_mode():
    _reset()
    png_bytes = b"\x89PNG-fake-bytes-for-testing"
    descriptor = mc.store_thumbnail("entry-1", png_bytes)
    assert descriptor["mode"] == "embedded"
    assert mc.load_thumbnail(descriptor) == png_bytes
    print("media_cache.store_thumbnail/load_thumbnail: embedded mode "
          "round-trips bytes -- OK")


def test_store_and_load_thumbnail_cached_mode():
    _reset()
    cache_dir = _SCRATCH / "cache_folder"
    mc.set_cache_dir(cache_dir)
    png_bytes = b"\x89PNG-other-fake-bytes"
    descriptor = mc.store_thumbnail("entry-2", png_bytes)
    assert descriptor["mode"] == "cached"
    assert "filename" in descriptor
    assert (cache_dir / descriptor["filename"]).exists()
    assert mc.load_thumbnail(descriptor) == png_bytes
    print("media_cache.store_thumbnail/load_thumbnail: cached mode "
          "writes to the configured folder and round-trips -- OK")


def test_switching_modes_does_not_corrupt_already_stored_descriptors():
    _reset()
    cache_dir = _SCRATCH / "cache_folder_2"
    mc.set_cache_dir(cache_dir)
    cached_descriptor = mc.store_thumbnail("entry-3", b"cached-bytes")

    mc.set_cache_dir(None)
    embedded_descriptor = mc.store_thumbnail("entry-4", b"embedded-bytes")

    # Both descriptors, from before/after the mode switch, still load
    # correctly -- switching modes only affects FUTURE store_thumbnail
    # calls, never already-persisted descriptors.
    mc.set_cache_dir(cache_dir)
    assert mc.load_thumbnail(cached_descriptor) == b"cached-bytes"
    mc.set_cache_dir(None)
    assert mc.load_thumbnail(embedded_descriptor) == b"embedded-bytes"
    print("media_cache: switching modes doesn't corrupt descriptors "
          "already stored under the other mode -- OK")


def test_load_thumbnail_returns_none_for_missing_cached_file():
    _reset()
    cache_dir = _SCRATCH / "cache_folder_3"
    mc.set_cache_dir(cache_dir)
    descriptor = mc.store_thumbnail("entry-5", b"will-be-deleted")
    (cache_dir / descriptor["filename"]).unlink()
    assert mc.load_thumbnail(descriptor) is None
    print("media_cache.load_thumbnail: returns None (not a crash) for a "
          "cached descriptor whose file is gone -- OK")


def test_load_thumbnail_returns_none_for_cached_descriptor_with_no_cache_dir_configured():
    _reset()
    cache_dir = _SCRATCH / "cache_folder_4"
    mc.set_cache_dir(cache_dir)
    descriptor = mc.store_thumbnail("entry-6", b"some-bytes")
    mc.set_cache_dir(None)
    assert mc.load_thumbnail(descriptor) is None
    print("media_cache.load_thumbnail: returns None for a cached "
          "descriptor when no cache dir is currently configured -- OK")


def test_load_thumbnail_tolerates_malformed_descriptors():
    _reset()
    assert mc.load_thumbnail(None) is None
    assert mc.load_thumbnail({}) is None
    assert mc.load_thumbnail({"mode": "nonsense"}) is None
    assert mc.load_thumbnail({"mode": "embedded"}) is None
    assert mc.load_thumbnail({"mode": "cached"}) is None
    print("media_cache.load_thumbnail: tolerates missing/malformed "
          "descriptors -- OK")


if __name__ == "__main__":
    try:
        test_get_cache_dir_defaults_to_none_when_nothing_saved()
        test_set_and_get_cache_dir_round_trips()
        test_set_cache_dir_none_clears_it()
        test_store_and_load_thumbnail_embedded_mode()
        test_store_and_load_thumbnail_cached_mode()
        test_switching_modes_does_not_corrupt_already_stored_descriptors()
        test_load_thumbnail_returns_none_for_missing_cached_file()
        test_load_thumbnail_returns_none_for_cached_descriptor_with_no_cache_dir_configured()
        test_load_thumbnail_tolerates_malformed_descriptors()
        print()
        print("All media_cache tests passed.")
    finally:
        shutil.rmtree(_SCRATCH, ignore_errors=True)
