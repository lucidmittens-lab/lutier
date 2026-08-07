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

Qt-free, fully testable without a display -- same convention as stack.py,
app_logic.py, and session_io.py.
"""
import itertools

_id_counter = itertools.count(1)


def next_memory_id():
    """Monotonically increasing int, unique for the life of the process --
    used as a memory's identity for GUI selection tracking, same reasoning
    as stack.next_stage_id()."""
    return next(_id_counter)


class StackMemory:
    """One saved snapshot: a name plus an OWNED copy of a stack.Stack --
    `stack` here should never be a reference shared with the actively-
    edited working stack, see this module's docstring."""

    def __init__(self, name, stack_snapshot, memory_id=None):
        self.id = memory_id if memory_id is not None else next_memory_id()
        self.name = name
        self.stack = stack_snapshot

    def copy(self):
        return StackMemory(self.name, self.stack.copy(), memory_id=self.id)

    def __repr__(self):
        return f"StackMemory(id={self.id}, name={self.name!r}, stages={len(self.stack.stages)})"


class StackMemoryLibrary:
    """An ordered list of StackMemory entries."""

    def __init__(self, memories=None):
        self.memories = list(memories) if memories is not None else []

    def save(self, name, stack):
        """Add a NEW memory capturing `stack`'s current state -- takes its
        own copy immediately, so later edits to `stack` don't retroactively
        change what was just saved."""
        memory = StackMemory(name, stack.copy())
        self.memories.append(memory)
        return memory

    def update(self, memory_id, stack):
        """Overwrite an EXISTING memory's snapshot with `stack`'s current
        state (same copy-on-write rule as save()) -- "update this memory
        with what I have now" rather than creating a new one."""
        memory = self.find(memory_id)
        memory.stack = stack.copy()

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
