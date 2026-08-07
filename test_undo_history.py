"""Tests for undo_history.py -- run directly: python3 test_undo_history.py

Uses plain strings/ints as "states" -- UndoHistory doesn't know or care
what it's storing (MainWindow hands it stack.Stack.copy() snapshots, but
nothing here depends on that), so plain values keep these tests focused
on the pointer/history logic itself.
"""
from undo_history import UndoHistory


def test_starts_with_one_state_nothing_to_undo_or_redo():
    history = UndoHistory("initial")
    assert len(history) == 1
    assert not history.can_undo
    assert not history.can_redo
    print("UndoHistory: starts with exactly the initial state, "
          "can_undo/can_redo both False -- OK")


def test_push_then_undo_returns_previous_state():
    history = UndoHistory("A")
    history.push("B")
    assert history.can_undo
    assert history.undo() == "A"
    assert not history.can_undo
    print("push then undo: returns the prior state, can_undo becomes "
          "False once back at the start -- OK")


def test_undo_then_redo_returns_to_the_same_state():
    history = UndoHistory("A")
    history.push("B")
    history.undo()
    assert history.can_redo
    assert history.redo() == "B"
    assert not history.can_redo
    print("undo then redo: returns to the state that was undone -- OK")


def test_undo_with_nothing_to_undo_returns_none_and_does_not_move():
    history = UndoHistory("A")
    assert history.undo() is None
    assert not history.can_undo
    assert not history.can_redo
    print("undo() at the very start: returns None, pointer unchanged -- OK")


def test_redo_with_nothing_to_redo_returns_none_and_does_not_move():
    history = UndoHistory("A")
    history.push("B")
    assert history.redo() is None
    print("redo() with no undone state pending: returns None -- OK")


def test_push_after_undo_discards_the_redo_tail():
    """The standard rule: making a new edit after undoing abandons
    whatever was undone -- it doesn't try to splice the old future back
    in. This is the one behavior worth pinning down with a test, since
    getting it wrong (keeping the old tail around) is the classic
    undo/redo bug."""
    history = UndoHistory("A")
    history.push("B")
    history.push("C")
    history.undo()  # back to B, "C" is now a pending redo
    assert history.can_redo
    history.push("D")  # a genuinely new edit from B
    assert not history.can_redo, "pushing a new state must discard the old redo tail"
    assert history.undo() == "B"
    print("push after undo: discards the abandoned redo tail (C), "
          "doesn't resurrect it later -- OK")


def test_multi_step_undo_redo_sequence():
    history = UndoHistory(0)
    for i in range(1, 6):
        history.push(i)
    # History: 0,1,2,3,4,5 -- pointer at 5.
    assert history.undo() == 4
    assert history.undo() == 3
    assert history.undo() == 2
    assert history.redo() == 3
    assert history.redo() == 4
    assert history.undo() == 3
    print("multi-step undo/redo sequence lands on the expected state at "
          "every step -- OK")


def test_len_reflects_total_history_including_redo_tail():
    history = UndoHistory("A")
    history.push("B")
    history.push("C")
    history.undo()
    assert len(history) == 3, "len() should count the full history, not just up to the pointer"
    print("__len__: counts the whole recorded history, including any "
          "not-yet-discarded redo tail -- OK")


if __name__ == "__main__":
    test_starts_with_one_state_nothing_to_undo_or_redo()
    test_push_then_undo_returns_previous_state()
    test_undo_then_redo_returns_to_the_same_state()
    test_undo_with_nothing_to_undo_returns_none_and_does_not_move()
    test_redo_with_nothing_to_redo_returns_none_and_does_not_move()
    test_push_after_undo_discards_the_redo_tail()
    test_multi_step_undo_redo_sequence()
    test_len_reflects_total_history_including_redo_tail()
    print()
    print("All undo_history tests passed.")
