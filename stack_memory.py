"""
stack_memory.py -- named, saved snapshots of a Stack configuration ("Stack
Memories") that live alongside the working Stack for the life of a
session, and are saved/restored as part of a .ltr session file (see
session_io.py's memory_library_to_session_list/memory_library_from_
session_list). Distinct from a session itself: a session is "the one
stack you're actively editing plus its sample image," a memory library is
"a shelf of alternate stack configs you can flip between, tweak, and
resave without losing the others" -- e.g. a few different LMT+DRT combos
you want to A/B without rebuilding either from scratch each time.

NON-DESTRUCTIVE BY CONSTRUCTION: saving or recalling a memory always goes
through Stack.copy() (never hands out or stores the SAME Stack object
someone else might still be mutating) -- so editing the stack you just
recalled can never retroactively change the saved memory underneath you,
and continuing to edit your working stack after saving a memory can't
retroactively change what was saved either. Same principle stack.py
itself follows (Stage.copy(), Stack.copy()).

STILLS GALLERY (Phase 3, per Tom: "Stack library should not be part of
[the Media Library] but it SHOULD be enhanced to be a stills gallery ...
should be able to store stack memories w/ stills grabbed at the time of
save AND external stills imported for reference") -- two additions,
deliberately kept in this one module since they share a panel
(stack_memories_panel.py):

  - StackMemory now optionally carries a `thumbnail` (a media_cache.py
    storage descriptor, see that module) captured automatically at save
    time from the current graded preview -- see stack_memories_panel.py's
    own save flow for where that capture happens; this module never
    generates one itself, just stores/copies whatever it's handed, same
    "this module owns the data shape, not how it's produced" split
    media_library.py already follows for ITS thumbnail field.
  - GalleryStill is a NEW, separate class for externally-imported
    reference stills -- PATH-REFERENCED like media_library.MediaEntry
    (it has a real source file to re-decode from, unlike a StackMemory's
    capture-at-save-time grab, which doesn't), NOT folded into
    MediaLibrary (Tom's own explicit instruction: "we're not folding it
    into the media library").

Qt-free, fully testable without a display -- same convention as stack.py,
app_logic.py, and session_io.py.
"""
import itertools

_id_counter = itertools.count(1)


def next_memory_id():
    """Monotonically increasing int, unique for the life of the process --
    used as a memory's identity for GUI selection tracking, same reasoning
    as stack.next_stage_id(). Shares ITS OWN counter with
    next_gallery_still_id() (see that function) so a StackMemory and a
    GalleryStill can never collide on id even though they're both rows in
    the same union list (stack_memories_panel.py)."""
    return next(_id_counter)


def next_gallery_still_id():
    """Same counter as next_memory_id() -- see that function's docstring
    on why the two share one id space."""
    return next(_id_counter)


class StackMemory:
    """One saved snapshot: a name plus an OWNED copy of a stack.Stack --
    `stack` here should never be a reference shared with the actively-
    edited working stack, see this module's docstring. `thumbnail` (Phase
    3, see module docstring) is a media_cache.py storage descriptor
    captured from the graded preview at save time, or None for a memory
    saved before this field existed (old session load)."""

    def __init__(self, name, stack_snapshot, memory_id=None, thumbnail=None,
                 full_still=None):
        self.id = memory_id if memory_id is not None else next_memory_id()
        self.name = name
        self.stack = stack_snapshot
        self.thumbnail = thumbnail
        # `full_still` (Phase 3 follow-up, per Tom: "also capture full
        # canvas-res still") -- a SECOND media_cache.py storage descriptor,
        # same shape as `thumbnail` but holding the full canvas-resolution
        # graded PNG rather than a small preview icon. Captured by
        # stack_memories_panel.py alongside the thumbnail at save time (one
        # full-res render, downsized once for the thumbnail, kept as-is for
        # this) -- None for a memory saved before this field existed, or
        # for one saved with no Sample loaded (nothing to capture from).
        self.full_still = full_still

    def copy(self):
        return StackMemory(self.name, self.stack.copy(), memory_id=self.id,
                            thumbnail=self.thumbnail, full_still=self.full_still)

    def __repr__(self):
        return f"StackMemory(id={self.id}, name={self.name!r}, stages={len(self.stack.stages)})"


class GalleryStill:
    """One externally-imported reference still (Phase 3, see module
    docstring) -- PATH-REFERENCED, not embedded: source_path/
    source_filename are load-bearing for session persistence (re-decoded
    fresh on load), same "how to rebuild it, not the built thing"
    principle media_library.MediaEntry uses for the identical
    reason (see that module's docstring on the practical consequence:
    can't be reconstructed if the source file has since moved). Deliberately
    NOT a subclass of MediaEntry -- structurally similar, but a
    distinct type on purpose so stack_memories_panel.py's union list can
    tell a memory row from a still row with a plain isinstance check, and
    so the two never accidentally become interchangeable with the real
    Media Library (Tom's own explicit "not folding it in")."""

    def __init__(self, name, thumbnail, source_path, source_filename=None,
                 still_id=None):
        self.id = still_id if still_id is not None else next_gallery_still_id()
        self.name = name
        self.thumbnail = thumbnail
        self.source_path = source_path
        self.source_filename = source_filename

    def copy(self):
        return GalleryStill(
            self.name, self.thumbnail, self.source_path,
            source_filename=self.source_filename, still_id=self.id)

    def __repr__(self):
        return f"GalleryStill(id={self.id}, name={self.name!r}, source_path={self.source_path!r})"


class StackMemoryLibrary:
    """An ordered list of StackMemory entries."""

    def __init__(self, memories=None):
        self.memories = list(memories) if memories is not None else []

    def save(self, name, stack, thumbnail=None, full_still=None):
        """Add a NEW memory capturing `stack`'s current state -- takes its
        own copy immediately, so later edits to `stack` don't retroactively
        change what was just saved. `thumbnail`/`full_still` (Phase 3, see
        StackMemory's own docstring on the latter) are media_cache.py
        storage descriptors already generated by the caller (see
        stack_memories_panel.py's save flow) -- this method just stores
        them, same "don't generate it here" split the class docstring
        calls out."""
        memory = StackMemory(name, stack.copy(), thumbnail=thumbnail, full_still=full_still)
        self.memories.append(memory)
        return memory

    def update(self, memory_id, stack, thumbnail=None, full_still=None):
        """Overwrite an EXISTING memory's snapshot with `stack`'s current
        state (same copy-on-write rule as save()) -- "update this memory
        with what I have now" rather than creating a new one. Only
        replaces `thumbnail`/`full_still` when actually passed (not None)
        -- callers that don't recapture a preview on update keep the
        existing ones rather than losing them."""
        memory = self.find(memory_id)
        memory.stack = stack.copy()
        if thumbnail is not None:
            memory.thumbnail = thumbnail
        if full_still is not None:
            memory.full_still = full_still

    def rename(self, memory_id, new_name):
        self.find(memory_id).name = new_name

    def remove(self, memory_id):
        before = len(self.memories)
        self.memories = [m for m in self.memories if m.id != memory_id]
        if len(self.memories) == before:
            raise KeyError(f"No memory with id={memory_id}")

    def find(self, memory_id):
        for memory in self.memories:
            if memory.id == memory_id:
                return memory
        raise KeyError(f"No memory with id={memory_id}")

    def recall(self, memory_id):
        """Returns a COPY of the memory's stack, ready to hand to
        StackPanel.set_stack -- never the memory's own owned Stack object,
        so subsequent edits to the working stack can't mutate the saved
        memory."""
        return self.find(memory_id).stack.copy()


class GalleryStillLibrary:
    """An ordered list of GalleryStill entries (Phase 3) -- same shape as
    MediaLibrary, deliberately kept a separate, sibling collection rather
    than reusing that class (see GalleryStill's own docstring)."""

    def __init__(self, stills=None):
        self.stills = list(stills) if stills is not None else []

    def add_still(self, still):
        self.stills.append(still)
        return still

    def rename(self, still_id, new_name):
        self.find(still_id).name = new_name

    def remove(self, still_id):
        before = len(self.stills)
        self.stills = [s for s in self.stills if s.id != still_id]
        if len(self.stills) == before:
            raise KeyError(f"No gallery still with id={still_id}")

    def find(self, still_id):
        for still in self.stills:
            if still.id == still_id:
                return still
        raise KeyError(f"No gallery still with id={still_id}")
