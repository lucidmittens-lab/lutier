"""
stack_memory_file_io.py -- save/load a single Stack Memory to/from its own
.ltm file, independent of a whole .ltr session (see session_io.py). Lets a
named Stack snapshot be shared between sessions, handed to someone else,
or backed up on its own -- rather than exporting/importing the entire
working session just to move one saved memory -- same relationship
save_session/load_session has to the working Stack overall, just one
level down, for a single stack_memory.StackMemory entry.

Reuses session_io.py's stack_to_session_dict/stack_from_session_dict for
the actual Stack serialization -- a memory's stack is serialized
identically to the working stack (see session_io.py's docstring on
memory_library_to_session_list); this module only adds the memory's name
and its own small file-format wrapper around that.

Qt-free, fully testable without a display -- same convention as every
other backend module here (stack_memory.py, session_io.py, ...).
"""
import json

from stack_memory import StackMemory
import session_io

MEMORY_FORMAT = "lutier-stack-memory"
MEMORY_VERSION = 1


def export_memory(memory, path):
    """Write `memory` (a stack_memory.StackMemory) to its own .ltm file at
    `path`. Delegates to session_io.stack_to_session_dict for the stack
    itself -- a LUT stage in this memory keeps whatever provenance it
    already has (reload-by-path, regenerate-identity, or embedded-from-
    the-LUT-Library, see session_io.py's docstring), so a re-imported
    memory reconstructs its stages exactly the same way the original
    did."""
    data = {
        "format": MEMORY_FORMAT,
        "version": MEMORY_VERSION,
        "name": memory.name,
        "stack": session_io.stack_to_session_dict(memory.stack),
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def import_memory(path):
    """Inverse of export_memory. Returns (StackMemory, warnings) -- a
    per-stage warning list, same "one bad stage doesn't sink the whole
    import" rule stack_from_session_dict/load_session use. Raises
    ValueError if `path` isn't a recognized Stack Memory file at all
    (wrong format/not JSON) -- a hard failure, same as
    session_io.load_session's own format check. The returned StackMemory
    is a fresh entry (its own new id via stack_memory.next_memory_id(),
    not whatever id it may have had when exported) -- same "ids aren't
    stable across a save/reload boundary" rule every other library/memory
    entry in this app already follows."""
    with open(path, "r") as f:
        data = json.load(f)
    if data.get("format") != MEMORY_FORMAT:
        raise ValueError(f"Not a LUTier Stack Memory file: {path}")
    stack, warnings = session_io.stack_from_session_dict(data.get("stack", {}))
    name = data.get("name", "Memory")
    return StackMemory(name, stack), warnings
