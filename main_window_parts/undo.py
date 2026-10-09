"""main_window_parts/undo.py -- debounced undo/redo of the Stack (see
undo_history.py and CLAUDE.md: stack_changed fires on every drag tick, so
pushes are coalesced). A mixin: MainWindow inherits it, so these methods
share the window's own `self`.
"""


# How long a stream of Stack edits (e.g. dragging a slider) has to go
# quiet before _on_stack_changed_for_history actually commits an undo
# step -- see that method's own docstring. Short enough that a genuine
# pause between two separate, distinct edits (click a different stage,
# adjust a different control) still reliably lands as two separate undo
# steps, long enough to comfortably coalesce every tick of one
# continuous slider drag into a single one.
_UNDO_DEBOUNCE_MS = 400


class UndoMixin:
    # ---- undo/redo (see undo_history.py for scope/design) ----------------

    def _on_stack_changed_for_history(self):
        """Schedules an undo step for every genuine Stack edit --
        connected to the same StackPanel.stack_changed signal
        _on_stack_changed listens to, fired by every mutation (add/
        remove/reorder/enable/opacity/param edit, size/domain, recalling
        a Stack Memory, applying a CDL/LUT Library entry -- see
        stack_panel.py's _emit_changed call sites). Skipped while
        _restoring_stack_history is set -- i.e. while THIS method's own
        _undo()/_redo(), or _new_session/_open_session resetting to a
        fresh history, is the one calling StackPanel.set_stack() --
        otherwise restoring/replacing the stack would immediately record
        itself as a new edit, corrupting the redo tail (or, for New/Open
        Session, pointlessly recording one entry into a history object
        about to be replaced anyway). Also skipped entirely while Shot
        mode is active (see stack_panel.py's is_shot_mode_active/
        _activate_shot_mode) -- this history only ever tracks the
        Sequence stack, and per-clip undo is an explicit, not-yet-built
        fast-follow (see the approved Timeline plan's own scope note), so
        a clip edit while Shot mode is on simply isn't undo-tracked at
        all, rather than corrupting the Sequence history with an entry
        that isn't even the Sequence stack.

        DEBOUNCED, not pushed immediately: stack_changed fires on every
        SINGLE TICK of a live drag (a Printer Lights/CDL slider, the
        fork-merge blend slider, ...), not once per gesture -- pushing on
        every tick used to mean one Undo only reverted a tiny fraction of
        a drag (the user had to press it dozens of times to get back to
        before the drag even started), and the history list grew by
        dozens-to-hundreds of entries for a single continuous edit.
        Restarting a short single-shot timer on every call, and only
        actually pushing when it fires (i.e. once the stream of edits has
        gone quiet for _UNDO_DEBOUNCE_MS), coalesces an entire drag
        gesture into exactly one undo step -- the same idea a text
        editor uses to group a burst of keystrokes into one undo, not
        one per character."""
        if self._restoring_stack_history or self.stack_panel.is_shot_mode_active():
            return
        self._undo_debounce_timer.start(_UNDO_DEBOUNCE_MS)

    def _commit_pending_undo_push(self):
        """The actual push, run once the debounce timer in
        _on_stack_changed_for_history actually fires. .copy() is
        required, not just cheap insurance: StackPanel.get_stack()
        returns its own live, still-mutable Stack object, so storing it
        directly (rather than a copy) would let later edits silently
        rewrite history that's already been recorded. Looking the stack
        up fresh here (rather than capturing it back when the timer was
        (re)started) is what makes the debounce correct: whichever edit
        last restarted the timer, this always records the CURRENT state
        at the moment the gesture actually went quiet, not some earlier
        mid-drag snapshot."""
        self._undo_history.push(self.stack_panel.get_stack().copy())
        self._update_undo_redo_actions()

    def _flush_pending_undo_push(self):
        """If a debounced push is still waiting to fire, commit it right
        now instead -- called before anything that itself depends on the
        undo history's top entry accurately reflecting the current live
        stack (undo/redo navigating relative to it; saving a session,
        whose embedded undo history should match what was actually
        saved). Without this, a still-pending coalesced edit could fire
        its OWN commit later, after an intervening undo/redo has already
        changed the live stack out from under it -- pushing the wrong
        state, at the wrong point in history, well after the fact."""
        if self._undo_debounce_timer.isActive():
            self._undo_debounce_timer.stop()
            self._commit_pending_undo_push()

    def _undo(self):
        self._flush_pending_undo_push()
        if not self._undo_history.can_undo:
            return
        state = self._undo_history.undo()
        self._restoring_stack_history = True
        try:
            self.stack_panel.set_stack(state.copy())
        finally:
            self._restoring_stack_history = False
        self._update_undo_redo_actions()

    def _redo(self):
        self._flush_pending_undo_push()
        if not self._undo_history.can_redo:
            return
        state = self._undo_history.redo()
        self._restoring_stack_history = True
        try:
            self.stack_panel.set_stack(state.copy())
        finally:
            self._restoring_stack_history = False
        self._update_undo_redo_actions()

    def _update_undo_redo_actions(self):
        # Force both off while Shot mode is active (see stack_panel.py's
        # is_shot_mode_active/_activate_shot_mode and
        # _on_stack_changed_for_history's own docstring) -- the history
        # underneath never changes during Shot mode (clip edits aren't
        # tracked at all), so Undo/Redo would otherwise silently act on
        # the SEQUENCE stack while the clip's own grade is what's on
        # screen -- same "must not be editable during an incompatible
        # mode" precedent as locking the Stack panel during video
        # playback (see the playback_started/playback_stopped wiring in
        # _build_layout).
        shot_mode = self.stack_panel.is_shot_mode_active()
        self.undo_action.setEnabled(not shot_mode and self._undo_history.can_undo)
        self.redo_action.setEnabled(not shot_mode and self._undo_history.can_redo)
