"""
workspace_io.py -- persistence for LUTier's WORKSPACE state (window size,
panel visibility, panel floating, splitter sizes) -- deliberately separate
from session_io.py's .ltr format (per Tom): workspace is an app-level UI
preference ("how do I like my panels arranged right now"), not project
data, so as of session_io.SESSION_VERSION 7 it no longer travels with a
saved session (see that module's docstring on the reversal -- VERSION 6
briefly folded workspace into .ltr, VERSION 7 splits it back out here).
Qt-free, like every other backend module in this app -- MainWindow is the
only caller, but nothing here imports PySide6.

Same plain dict shape everywhere (window_size/panels/splitters/floating --
see main_window.MainWindow.get_workspace_state/load_workspace_state for
what each key means and how it's produced/applied), used two independent
ways:

  - the REMEMBERED workspace (per Tom, "remember between sessions") --
    written to a fixed, not-user-chosen path on every app quit (see
    DEFAULT_WORKSPACE_PATH/save_remembered_workspace) and read back on the
    next launch (load_remembered_workspace), so the app reopens looking
    the way it was left -- same intent VERSION 6's session-embedded
    workspace had, just tied to "this app on this machine" instead of
    "whichever project happens to be open."
  - named PRESETS (per Tom, "workspace preset export") -- an explicit
    Export/Import pair to a user-chosen file (see save_workspace_preset/
    load_workspace_preset), for saving or sharing a specific panel
    arrangement independent of whatever's currently remembered. Same file
    format as the remembered workspace, just written wherever the user
    picks instead of the fixed path -- floating state round-trips through
    presets exactly the same way it does through the remembered workspace,
    since both are literally the same dict shape read/written by the same
    two functions underneath.

Both are a thin, versioned JSON envelope around that one dict --
WORKSPACE_FORMAT_VERSION exists for the same forward-compatibility reason
SESSION_FORMAT/SESSION_VERSION do in session_io.py, but there's no
per-field reconstruction logic here that could partially fail the way a
Stack stage or a Library entry can, so there's nothing finer-grained to
version yet.

WORKSPACE_FORMAT_VERSION 1 -> 2 (docking migration, per Tom -- "do the
migration to new system"): the dict's own shape changed from
{window_size, panels, splitters, floating} to {window_size,
dock_state_b64} once panel position/size/floating all live in one
QMainWindow.saveState() blob instead of a hand-rolled splitter/visibility
dict -- see main_window.MainWindow.get_workspace_state/
load_workspace_state. This module's own functions don't interpret that
shape at all (see docstring above -- pure I/O, no per-field logic), so
the version bump is purely informational/for-humans here; the actual
tolerance for an old-format file living happily alongside new code is in
MainWindow.load_workspace_state, which treats a dict with no
`dock_state_b64` key as empty (falls back to the built-in default
layout) rather than attempting to apply panels/splitters keys that no
longer mean anything against a dock-based layout.
"""
import json
from pathlib import Path

from atomic_write import atomic_open

WORKSPACE_FORMAT = "lutier-workspace"
WORKSPACE_FORMAT_VERSION = 2

# Fixed, not-user-chosen -- the "remember between launches" file (per
# Tom), distinct from an exported PRESET (which the user explicitly names
# and picks a location for via Export/Import Workspace Preset...).
# ~/Library/Application Support is the macOS-conventional per-app
# writable-preferences location this app already targets for its packaged
# .app (see BUILD_ON_MAC.md) -- resolves to an equivalent, if not
# OS-idiomatic, path on any other platform this happens to run on, which
# is fine: this is a private file LUTier only ever reads/writes to
# itself, never a path shown to the user.
DEFAULT_WORKSPACE_DIR = Path.home() / "Library" / "Application Support" / "LUTier"
DEFAULT_WORKSPACE_PATH = DEFAULT_WORKSPACE_DIR / "workspace.json"


def _wrap(state):
    return {"format": WORKSPACE_FORMAT, "version": WORKSPACE_FORMAT_VERSION,
            "workspace": state}


def _unwrap(payload):
    """Tolerant of a missing/foreign envelope -- there's only ever been
    version 1 of this format so far, and a plain dict with no "workspace"
    key at all (shouldn't happen from this module's own writer, but cheap
    to accept defensively) is treated as being the state itself."""
    if isinstance(payload, dict) and "workspace" in payload:
        return payload.get("workspace") or {}
    return payload or {}


def save_workspace_preset(path, state):
    """Write `state` (MainWindow.get_workspace_state()'s dict shape,
    including "floating") to `path` as a named preset. Raises whatever
    the underlying file write raises -- callers (MainWindow's Export
    Workspace Preset... action, or save_remembered_workspace below)
    decide how to surface or swallow that, same "this module just does
    the I/O" convention as session_io.save_session."""
    with atomic_open(path, "w") as f:
        f.write(json.dumps(_wrap(state), indent=2))


def load_workspace_preset(path):
    """Inverse of save_workspace_preset -- returns the plain state dict.
    Raises FileNotFoundError/ValueError (a bad/non-JSON file) on failure,
    same "let the caller decide how to surface it" convention as
    session_io.load_session raising ValueError for a non-session file."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return _unwrap(payload)


def save_remembered_workspace(state):
    """Best-effort -- called from MainWindow.closeEvent on every quit, so
    a write failure (permissions, a read-only filesystem, disk full)
    should never block the app from actually closing; silently gives up
    rather than raising. Creates DEFAULT_WORKSPACE_DIR if this is the
    first time LUTier has ever quit on this machine."""
    try:
        DEFAULT_WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
        save_workspace_preset(DEFAULT_WORKSPACE_PATH, state)
    except OSError:
        pass


def load_remembered_workspace():
    """Returns the remembered state dict, or None if there isn't one yet
    (first-ever launch on this machine) or it can't be read/parsed.
    MainWindow treats None as "nothing to apply, leave the built-in
    DEFAULT_WORKSPACE_STATE _build_layout already set in place" -- same
    no-op convention load_workspace_state already has for an empty dict."""
    try:
        return load_workspace_preset(DEFAULT_WORKSPACE_PATH)
    except (OSError, ValueError):
        return None
