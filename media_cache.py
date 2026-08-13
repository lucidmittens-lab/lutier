"""
media_cache.py -- persistence for the one new user-choosable preference
the Media Library / Stills Gallery work needs: an optional sidecar folder
to store generated thumbnails in, instead of embedding them directly in
the session file (per Tom: "thumbnails can go in the session file, but we
should allow configuration of a sidecar gallery folder. will need this
for video cache eventually"). Same tiny-sibling-module shape as
theme_prefs.py (its own file under workspace_io.DEFAULT_WORKSPACE_DIR,
not folded into workspace.json -- a cache-folder choice is neither THEME
nor panel-layout state). Qt-free, like every other backend module in
this app.

The forward-looking piece is the STORAGE DESCRIPTOR returned by
store_thumbnail/consumed by load_thumbnail: callers (session_io.py's
to/from functions) just serialize/deserialize a small dict, oblivious to
which mode produced it. That's deliberate -- a future store_video_proxy
can reuse the identical {"mode": ...} shape without touching any call
site that already handles thumbnail descriptors, and a session saved in
"cached" mode still loads fine on a machine with no cache dir configured
(load_thumbnail just returns None -- see its own docstring).
"""
import base64
import json
import uuid
from pathlib import Path

from atomic_write import atomic_open
from workspace_io import DEFAULT_WORKSPACE_DIR

MEDIA_CACHE_PREFS_FORMAT = "lutier-media-cache-prefs"
MEDIA_CACHE_PREFS_VERSION = 1
MEDIA_CACHE_PREFS_PATH = DEFAULT_WORKSPACE_DIR / "media_cache_prefs.json"


def get_cache_dir():
    """The configured cache folder as a Path, or None if the user hasn't
    set one (default -- see module docstring's "embed in session"
    default) or the prefs file is missing/corrupt/unreadable, same
    tolerant-of-a-missing-or-bad-file convention as theme_prefs.load_
    accent_color."""
    try:
        payload = json.loads(MEDIA_CACHE_PREFS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    raw = payload.get("cache_dir")
    return Path(raw) if isinstance(raw, str) and raw else None


def set_cache_dir(path_or_none):
    """Persist the chosen cache folder, or clear it (embed-in-session
    mode) when passed None -- matches Phase 1's planned "Clear (embed in
    session)" menu option. Best-effort, same reasoning as theme_prefs.
    save_accent_color: called from a menu action already reflected on
    screen, so a write failure (permissions, read-only filesystem)
    shouldn't raise into the middle of it."""
    try:
        MEDIA_CACHE_PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {"format": MEDIA_CACHE_PREFS_FORMAT, "version": MEDIA_CACHE_PREFS_VERSION,
                   "cache_dir": str(path_or_none) if path_or_none else None}
        with atomic_open(MEDIA_CACHE_PREFS_PATH, "w") as f:
            f.write(json.dumps(payload, indent=2))
    except OSError:
        pass


def store_thumbnail(entry_id, png_bytes):
    """Store `png_bytes` (from thumbnail.make_thumbnail_png_bytes) for
    `entry_id` (any caller-chosen stable id -- an ImageLibraryEntry/
    StackMemory/GalleryStill's own id is the expected use), returning a
    small descriptor dict callers persist verbatim in the session file:

      - no cache dir configured -> {"mode": "embedded",
        "data_b64": <base64 str>} -- the bytes travel INSIDE the session
        file, matching Tom's stated default.
      - a cache dir IS configured -> {"mode": "cached",
        "filename": <str>} -- bytes are written under that folder instead
        (a random, collision-proof filename, not entry_id itself, since
        entry_id isn't guaranteed filesystem-safe); the session file only
        ever stores the filename, never the bytes.

    A cache-dir write failure (folder deleted out from under the app,
    permissions) falls back to embedding instead of losing the thumbnail
    entirely -- a thumbnail is small enough that this fallback is cheap,
    and "no thumbnail" is a worse failure mode than "embedded when it
    didn't strictly need to be"."""
    cache_dir = get_cache_dir()
    if cache_dir is not None:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{uuid.uuid4().hex}.png"
            (cache_dir / filename).write_bytes(png_bytes)
            return {"mode": "cached", "filename": filename}
        except OSError:
            pass
    return {"mode": "embedded", "data_b64": base64.b64encode(png_bytes).decode("ascii")}


def load_thumbnail(descriptor):
    """Inverse of store_thumbnail. Returns None (never raises) for a
    missing/malformed descriptor, a "cached" descriptor whose file is
    gone (folder moved/cleared since the session was saved), or no cache
    dir currently configured -- callers (list-row icon population) treat
    None as "show no thumbnail," never a crash, same tolerant convention
    as every other loader in this module."""
    if not isinstance(descriptor, dict):
        return None
    mode = descriptor.get("mode")
    if mode == "embedded":
        data_b64 = descriptor.get("data_b64")
        if not isinstance(data_b64, str) or not data_b64:
            return None
        try:
            return base64.b64decode(data_b64)
        except (ValueError, TypeError):
            return None
    if mode == "cached":
        filename = descriptor.get("filename")
        cache_dir = get_cache_dir()
        if not isinstance(filename, str) or not filename or cache_dir is None:
            return None
        try:
            return (cache_dir / filename).read_bytes()
        except OSError:
            return None
    return None
