"""
undo_history.py -- a simple linear undo/redo history for the working
Stack (stack.Stack), pointer-based over a list of snapshots. Qt-free,
fully testable without a display -- same convention as every other
backend module here.

SCOPE (per Tom, "basic" undo/redo for a first test release): tracks the
working Stack only -- not the LUT/CDL Library, Stack Memories, or the
sample image. Every real edit to the Stack (add/remove/reorder/enable/
opacity/param change, size/domain, recalling a Stack Memory, applying a
CDL/LUT Library entry, opening a session, New Session) already funnels
through StackPanel.stack_changed exactly once per change (see that
module's _emit_changed call sites) -- MainWindow listens for that signal
and pushes a snapshot here for every genuine edit, guarding against
recording its OWN restore calls as new history (see MainWindow's
_restoring_stack_history flag in main_window.py).

CHEAP BY CONSTRUCTION: Stack.copy() (see stack.py) never duplicates a
LUT's actual table -- Stage.copy() only shallow-copies each stage's
params dict, so a "lut" stage's embedded/loaded LUT object is SHARED by
reference across every snapshot, never re-baked or re-allocated. LUT
tables are also never mutated in place anywhere in this app (only
replaced wholesale). That's what makes keeping a long, uncapped history
cheap enough not to bother capping it for a first pass -- if a session
ever turns out to need thousands of edits, add a cap then, not
speculatively now.
"""


class UndoHistory:
    """Pointer into a list of Stack snapshots. push() truncates any redo
    tail (same as every standard undo/redo implementation -- making a new
    edit after undoing abandons the redone-away future, it doesn't try to
    merge it back in)."""

    def __init__(self, initial_state):
        self._history = [initial_state]
        self._pointer = 0

    def push(self, state):
        """Record a new state as the current point in history, discarding
        any redo tail beyond the current pointer."""
        del self._history[self._pointer + 1:]
        self._history.append(state)
        self._pointer += 1

    def undo(self):
        """Move the pointer back one step and return that state, or
        return None (pointer unchanged) if there's nothing to undo -- see
        can_undo."""
        if not self.can_undo:
            return None
        self._pointer -= 1
        return self._history[self._pointer]

    def redo(self):
        """Move the pointer forward one step and return that state, or
        return None (pointer unchanged) if there's nothing to redo -- see
        can_redo."""
        if not self.can_redo:
            return None
        self._pointer += 1
        return self._history[self._pointer]

    @property
    def can_undo(self):
        return self._pointer > 0

    @property
    def can_redo(self):
        return self._pointer < len(self._history) - 1

    def __len__(self):
        return len(self._history)
