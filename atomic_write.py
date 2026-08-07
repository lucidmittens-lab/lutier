"""
atomic_write.py -- one helper, `atomic_open`, for every place this app
writes a file a user's work actually depends on (session_io.py's .ltr,
stack_memory_file_io.py's .ltm export, workspace_io.py's remembered/preset
workspace state). Plain `open(path, "w")` truncates the target to zero
bytes the instant it's opened, before a single byte of new content is
written -- if the app is killed, the machine loses power, or the disk
fills up anywhere during the write that follows, the previous good file is
already gone and the new one is left incomplete/corrupt. For a session
file that can run to tens of MB (embedded LUT tables), that write is not
instantaneous, so this isn't just a theoretical race.

`atomic_open` writes to a temp file in the SAME directory as the target
(so the final swap is a same-filesystem rename, not a cross-filesystem
copy) and only calls `os.replace()` -- atomic on both POSIX and Windows --
once the `with` block finishes without raising. On any failure before
that point, the temp file is removed and `path` is left completely
untouched.
"""
import os
import tempfile
from contextlib import contextmanager


@contextmanager
def atomic_open(path, mode="w", encoding="utf-8"):
    """Drop-in replacement for `open(path, mode)` in a `with` block, for
    text-mode writes that should be atomic. Binary mode ("wb") is also
    supported -- pass encoding=None in that case."""
    path = str(path)
    directory = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".lutier-tmp-", suffix=".part")
    try:
        with os.fdopen(fd, mode, encoding=encoding if "b" not in mode else None) as f:
            yield f
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
