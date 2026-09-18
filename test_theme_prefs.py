"""Tests for theme_prefs.py -- run directly: python3 test_theme_prefs.py

Previously a coverage gap (found in code review) -- also the target of a
real bug fix (save_accent_color now writes via atomic_write.atomic_open
rather than a plain Path.write_text, so a crash/power-loss mid-write
can't leave a truncated, unparseable file).
"""
import os
import tempfile
from pathlib import Path

import theme_prefs as tp

# ---- save_accent_color / load_accent_color ---------------------------------
# Both read/write THEME_PREFS_PATH -- redirected to a temp file for the
# duration of each test (restored in `finally`) rather than touching
# whatever's actually saved on the machine running these tests, same
# convention test_workspace_io.py's own remembered-workspace tests use.


def test_accent_color_round_trips():
    original_dir, original_path = tp.THEME_PREFS_PATH.parent, tp.THEME_PREFS_PATH
    with tempfile.TemporaryDirectory() as tmp_dir:
        tp.THEME_PREFS_PATH = Path(tmp_dir) / "nested" / "LUTier" / "theme_prefs.json"
        try:
            # mkdir(parents=True) inside save_accent_color must create
            # "nested/LUTier" itself -- this directory doesn't exist yet,
            # same as a real first-ever accent color pick.
            tp.save_accent_color("#3fae6a")
            assert tp.load_accent_color() == "#3fae6a"
        finally:
            tp.THEME_PREFS_PATH = original_path
    print("save_accent_color/load_accent_color: round-trip through the "
          "(auto-created) theme prefs path -- OK")


def test_load_accent_color_returns_none_when_nothing_saved_yet():
    original_path = tp.THEME_PREFS_PATH
    with tempfile.TemporaryDirectory() as tmp_dir:
        tp.THEME_PREFS_PATH = Path(tmp_dir) / "never_written" / "theme_prefs.json"
        try:
            assert tp.load_accent_color() is None
        finally:
            tp.THEME_PREFS_PATH = original_path
    print("load_accent_color: returns None (not an exception) when "
          "nothing's been saved yet -- OK")


def test_load_accent_color_returns_none_for_a_malformed_file():
    original_path = tp.THEME_PREFS_PATH
    with tempfile.TemporaryDirectory() as tmp_dir:
        tp.THEME_PREFS_PATH = Path(tmp_dir) / "theme_prefs.json"
        tp.THEME_PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            tp.THEME_PREFS_PATH.write_text("not valid json{{{", encoding="utf-8")
            assert tp.load_accent_color() is None
            tp.THEME_PREFS_PATH.write_text('{"accent_color": 12345}', encoding="utf-8")
            assert tp.load_accent_color() is None, "a non-string accent_color must not be returned"
            tp.THEME_PREFS_PATH.write_text('{"accent_color": ""}', encoding="utf-8")
            assert tp.load_accent_color() is None, "an empty accent_color must not be returned"
            tp.THEME_PREFS_PATH.write_text("[]", encoding="utf-8")
            assert tp.load_accent_color() is None, "a non-dict payload must not be returned"
        finally:
            tp.THEME_PREFS_PATH = original_path
    print("load_accent_color: returns None (not an exception) for a "
          "malformed/unparseable/wrongly-shaped file -- OK")


def test_save_accent_color_never_raises_on_an_unwritable_path():
    """Best-effort per this function's own docstring -- a write failure
    (here: a file standing where a directory needs to go) must never
    propagate, since a failed save should never interrupt an otherwise-
    successful color change the user already sees applied on screen."""
    original_path = tp.THEME_PREFS_PATH
    with tempfile.TemporaryDirectory() as tmp_dir:
        blocking_file = os.path.join(tmp_dir, "blocking_file")
        with open(blocking_file, "w") as f:
            f.write("not a directory")
        # Asking mkdir(parents=True) to create a directory AT a path
        # that's already an ordinary file -- guaranteed OSError.
        tp.THEME_PREFS_PATH = Path(blocking_file) / "LUTier" / "theme_prefs.json"
        try:
            tp.save_accent_color("#3fae6a")  # must not raise
        finally:
            tp.THEME_PREFS_PATH = original_path
    print("save_accent_color: never raises, even when the target path "
          "can't be created -- OK")


def test_save_accent_color_leaves_the_previous_file_intact_on_a_failed_write():
    """Regression guard for the atomic_write fix: a write that fails
    partway through must not truncate/corrupt the PREVIOUSLY saved file
    -- confirmed here by making the write itself fail (a read-only
    target file) after a real, successful prior save, and checking the
    old value survives."""
    original_path = tp.THEME_PREFS_PATH
    with tempfile.TemporaryDirectory() as tmp_dir:
        tp.THEME_PREFS_PATH = Path(tmp_dir) / "theme_prefs.json"
        try:
            tp.save_accent_color("#111111")
            assert tp.load_accent_color() == "#111111"

            # atomic_open writes to a temp file in the SAME directory,
            # then os.replace()s it over the target -- making the
            # DIRECTORY read-only blocks creating that temp file at all,
            # so the write fails before ever touching the real file.
            os.chmod(tmp_dir, 0o500)
            try:
                tp.save_accent_color("#222222")  # must not raise
            finally:
                os.chmod(tmp_dir, 0o700)  # restore so TemporaryDirectory can clean up

            assert tp.load_accent_color() == "#111111", (
                "a failed write must leave the previously saved color completely intact")
        finally:
            tp.THEME_PREFS_PATH = original_path
    print("save_accent_color: a failed write leaves the previously "
          "saved accent color completely intact, not truncated -- OK")


if __name__ == "__main__":
    test_accent_color_round_trips()
    test_load_accent_color_returns_none_when_nothing_saved_yet()
    test_load_accent_color_returns_none_for_a_malformed_file()
    test_save_accent_color_never_raises_on_an_unwritable_path()
    test_save_accent_color_leaves_the_previous_file_intact_on_a_failed_write()
    print()
    print("All theme_prefs tests passed.")
