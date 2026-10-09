"""
theme_prefs.py -- persistence for the one user-choosable theme value this
app has: the accent color (per Tom, "give users control over the accent
color," 2026-07-25). Deliberately its own tiny module rather than folded
into style.py -- style.py stays Qt-AND-disk-free (it only ever computes
colors/QSS from values handed to it), same separation-of-concerns reason
workspace_io.py is split out from main_window.py: this is I/O, not
styling logic. Same "Qt-free backend module" convention as workspace_io.py/
session_io.py -- MainWindow/style.py are the only callers, nothing here
imports PySide6.

Same directory as workspace_io.DEFAULT_WORKSPACE_DIR (this app's one
per-machine preferences location), a sibling file rather than merged into
workspace.json -- accent color is a THEME preference, not a panel-layout
one; keeping them separate means a "Reset to Default Workspace" (panel
layout only, see main_window._reset_workspace_to_default) can never
accidentally also reset someone's chosen accent color, and vice versa.

Same tolerant-of-a-missing-or-bad-file convention as workspace_io's
load_remembered_workspace: a first-ever launch, a hand-edited-into-
garbage file, or a read error are all just "nothing saved yet" to the
caller, never a crash -- style.get_accent_color() falls back to
style.DEFAULT_ACCENT_HEX either way.

Every save merges into what's already in theme_prefs.json instead of
overwriting it, so a value added later (an Interface Scale setting was
built and parked on the `interface-scale` branch, 2026-10-09) can share
the file safely.
"""
import json

from atomic_write import atomic_open
from workspace_io import DEFAULT_WORKSPACE_DIR

THEME_PREFS_FORMAT = "lutier-theme-prefs"
THEME_PREFS_VERSION = 1
THEME_PREFS_PATH = DEFAULT_WORKSPACE_DIR / "theme_prefs.json"

def _load_payload():
    """theme_prefs.json as a dict, or {} if it's missing, unreadable or
    not a JSON object."""
    try:
        payload = json.loads(THEME_PREFS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_values(**values):
    """Merge `values` into theme_prefs.json (keeping every other saved
    value) and write it atomically. Best-effort: an OSError is swallowed,
    same as before -- callers act on a choice already shown on screen."""
    try:
        THEME_PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = _load_payload()
        payload.update({"format": THEME_PREFS_FORMAT, "version": THEME_PREFS_VERSION})
        payload.update(values)
        with atomic_open(THEME_PREFS_PATH, "w") as f:
            f.write(json.dumps(payload, indent=2))
    except OSError:
        pass


def save_accent_color(hex_color):
    """Best-effort, same reasoning as workspace_io.save_remembered_
    workspace -- called right after the user picks a new color (see
    main_window._choose_accent_color); a write failure (permissions, a
    read-only filesystem, disk full) should never raise into the middle
    of an otherwise-successful color change the user already sees applied
    on screen.

    Writes via atomic_write.atomic_open rather than a plain Path.write_
    text (a real, previously-shipped bug, found in code review): a plain
    write truncates the file to zero bytes before writing a single new
    byte, so a crash or power loss mid-write left a truncated,
    unparseable file -- load_accent_color's own (OSError, ValueError)
    guard means that never crashes the app, but it does mean the user's
    saved accent color silently reverts to default on the next launch,
    the exact class of bug atomic_write.py exists to prevent everywhere
    else a user's own choice gets persisted."""
    _save_values(accent_color=hex_color)


def load_accent_color():
    """The saved accent hex string, or None if there isn't one yet (first
    launch), the file's unreadable, or it doesn't parse as the expected
    shape -- style.get_accent_color() treats None as "use the built-in
    default," same no-op convention load_remembered_workspace's None
    already has for workspace state."""
    color = _load_payload().get("accent_color")
    return color if isinstance(color, str) and color else None
