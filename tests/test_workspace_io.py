"""Tests for workspace_io.py -- run directly: python3 -m tests.test_workspace_io

Fully Qt-free (see that module's docstring) -- MainWindow.get_workspace_
state()/load_workspace_state() build/consume the same plain dict shape
used throughout, but exercising THOSE requires a real QMainWindow (no
display in this sandbox otherwise -- see main_window.py's own docstring),
so these tests cover workspace_io's own half: the file I/O round-trips
correctly, tolerates the file not existing yet, and doesn't blow up on a
malformed file.
"""
import json
import os
import tempfile
from pathlib import Path

import workspace_io as wio

SAMPLE_STATE = {
    "window_size": [1400, 900],
    "panels": {"browser_frame": True, "scopes_frame": False},
    "floating": ["scopes_frame", "viewer_3d_frame"],
    "splitters": {"main_splitter": [260, 700, 340], "left_splitter": [300, 180]},
}


# ---- save_workspace_preset / load_workspace_preset ------------------------

def test_save_and_load_workspace_preset_round_trips_exactly():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "test.lutlayout")
        wio.save_workspace_preset(path, SAMPLE_STATE)
        loaded = wio.load_workspace_preset(path)
        assert loaded == SAMPLE_STATE
    print("save_workspace_preset/load_workspace_preset: an arbitrary "
          "workspace state dict (including floating) round-trips through "
          "a real file exactly -- OK")


def test_save_workspace_preset_writes_a_versioned_envelope():
    """Not just the bare state dict -- format/version/workspace, same
    "envelope" shape session_io.py's format/version/... top-level keys
    follow, so a future format change has somewhere to hang a migration
    off of (see this module's docstring)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "test.lutlayout")
        wio.save_workspace_preset(path, SAMPLE_STATE)
        with open(path) as f:
            raw = json.load(f)
        assert raw["format"] == wio.WORKSPACE_FORMAT
        assert raw["version"] == wio.WORKSPACE_FORMAT_VERSION
        assert raw["workspace"] == SAMPLE_STATE
    print("save_workspace_preset: writes a versioned {format, version, "
          "workspace} envelope, not just the bare state dict -- OK")


def test_load_workspace_preset_raises_on_missing_file():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "does_not_exist.lutlayout")
        try:
            wio.load_workspace_preset(path)
            raise AssertionError("expected an exception for a missing file")
        except OSError:
            pass
    print("load_workspace_preset: raises on a missing file rather than "
          "silently returning something -- OK")


def test_load_workspace_preset_raises_on_malformed_json():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "bad.lutlayout")
        with open(path, "w") as f:
            f.write("{not valid json")
        try:
            wio.load_workspace_preset(path)
            raise AssertionError("expected an exception for malformed JSON")
        except ValueError:
            pass
    print("load_workspace_preset: raises on malformed JSON rather than "
          "silently returning something -- OK")


def test_load_workspace_preset_accepts_a_bare_dict_with_no_envelope():
    """Defensive tolerance (see _unwrap's docstring) -- not something this
    module's own writer ever produces, but a plain dict with no "workspace"
    key is treated as being the state itself rather than raising."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "bare.lutlayout")
        with open(path, "w") as f:
            json.dump({"window_size": [800, 600]}, f)
        loaded = wio.load_workspace_preset(path)
        assert loaded == {"window_size": [800, 600]}
    print("load_workspace_preset: a bare dict with no envelope is treated "
          "as the state itself, not rejected -- OK")


# ---- save_remembered_workspace / load_remembered_workspace ----------------
# Both read/write DEFAULT_WORKSPACE_PATH -- redirected to a temp file for
# the duration of each test (restored in `finally`) rather than touching
# whatever's actually remembered on the machine running these tests.

def test_remembered_workspace_round_trips():
    original_dir, original_path = wio.DEFAULT_WORKSPACE_DIR, wio.DEFAULT_WORKSPACE_PATH
    with tempfile.TemporaryDirectory() as tmp_dir:
        wio.DEFAULT_WORKSPACE_DIR = Path(tmp_dir) / "nested" / "LUTier"
        wio.DEFAULT_WORKSPACE_PATH = wio.DEFAULT_WORKSPACE_DIR / "workspace.json"
        try:
            # mkdir(parents=True) inside save_remembered_workspace must
            # create "nested/LUTier" itself -- this directory doesn't
            # exist yet, same as a real first-ever launch.
            wio.save_remembered_workspace(SAMPLE_STATE)
            loaded = wio.load_remembered_workspace()
            assert loaded == SAMPLE_STATE
        finally:
            wio.DEFAULT_WORKSPACE_DIR, wio.DEFAULT_WORKSPACE_PATH = original_dir, original_path
    print("save_remembered_workspace/load_remembered_workspace: round-trip "
          "through the (auto-created) remembered-workspace path -- OK")


def test_load_remembered_workspace_returns_none_when_nothing_saved_yet():
    original_dir, original_path = wio.DEFAULT_WORKSPACE_DIR, wio.DEFAULT_WORKSPACE_PATH
    with tempfile.TemporaryDirectory() as tmp_dir:
        wio.DEFAULT_WORKSPACE_DIR = Path(tmp_dir) / "never_written"
        wio.DEFAULT_WORKSPACE_PATH = wio.DEFAULT_WORKSPACE_DIR / "workspace.json"
        try:
            assert wio.load_remembered_workspace() is None
        finally:
            wio.DEFAULT_WORKSPACE_DIR, wio.DEFAULT_WORKSPACE_PATH = original_dir, original_path
    print("load_remembered_workspace: returns None (not an exception) on "
          "a first-ever launch with nothing remembered yet -- OK")


def test_save_remembered_workspace_never_raises_on_an_unwritable_path():
    """Best-effort per this function's own docstring -- a write failure
    (here: DEFAULT_WORKSPACE_DIR pointed at a path that can't be created,
    a file standing where a directory needs to go) must never propagate,
    since closeEvent calling this should never block the app from
    actually quitting."""
    original_dir, original_path = wio.DEFAULT_WORKSPACE_DIR, wio.DEFAULT_WORKSPACE_PATH
    with tempfile.TemporaryDirectory() as tmp_dir:
        blocking_file = os.path.join(tmp_dir, "blocking_file")
        with open(blocking_file, "w") as f:
            f.write("not a directory")
        # Asking mkdir(parents=True) to create a directory AT a path
        # that's already an ordinary file -- guaranteed OSError.
        wio.DEFAULT_WORKSPACE_DIR = Path(blocking_file) / "LUTier"
        wio.DEFAULT_WORKSPACE_PATH = wio.DEFAULT_WORKSPACE_DIR / "workspace.json"
        try:
            wio.save_remembered_workspace(SAMPLE_STATE)  # must not raise
        finally:
            wio.DEFAULT_WORKSPACE_DIR, wio.DEFAULT_WORKSPACE_PATH = original_dir, original_path
    print("save_remembered_workspace: swallows a write failure rather "
          "than raising -- OK")


# ---- _default_workspace_dir platform dispatch ------------------------------

def test_default_workspace_dir_dispatches_per_platform(monkeypatch):
    """_default_workspace_dir() per-platform shape -- see that function's
    own docstring for why each OS gets a different, idiomatic base dir."""
    monkeypatch.setattr(wio.sys, "platform", "darwin")
    assert wio._default_workspace_dir() == (
        Path.home() / "Library" / "Application Support" / "LUTier")

    monkeypatch.setattr(wio.sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert wio._default_workspace_dir() == Path.home() / ".config" / "LUTier"

    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/custom-xdg-config")
    assert wio._default_workspace_dir() == Path("/tmp/custom-xdg-config") / "LUTier"

    monkeypatch.setattr(wio.sys, "platform", "win32")
    monkeypatch.delenv("APPDATA", raising=False)
    assert wio._default_workspace_dir() == Path.home() / "AppData" / "Roaming" / "LUTier"

    monkeypatch.setenv("APPDATA", "C:/custom/appdata")
    assert wio._default_workspace_dir() == Path("C:/custom/appdata") / "LUTier"
    print("_default_workspace_dir: darwin/linux/windows each resolve to "
          "their own OS-idiomatic base dir, honoring XDG_CONFIG_HOME/"
          "APPDATA when set -- OK")


if __name__ == "__main__":
    test_save_and_load_workspace_preset_round_trips_exactly()
    test_save_workspace_preset_writes_a_versioned_envelope()
    test_load_workspace_preset_raises_on_missing_file()
    test_load_workspace_preset_raises_on_malformed_json()
    test_load_workspace_preset_accepts_a_bare_dict_with_no_envelope()
    test_remembered_workspace_round_trips()
    test_load_remembered_workspace_returns_none_when_nothing_saved_yet()
    test_save_remembered_workspace_never_raises_on_an_unwritable_path()
    print()
    print("All workspace_io tests passed.")
