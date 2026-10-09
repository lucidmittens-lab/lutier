"""main_window_parts/workspace.py -- workspace layout persistence (dock state,
saved separately from the session by workspace_io), reset to default, and
preset export/import. A mixin: MainWindow inherits it, so these methods
share the window's own `self`.
"""
import base64
from PySide6.QtWidgets import QFileDialog, QMessageBox
from PySide6.QtCore import QTimer, QByteArray
import workspace_io
from .constants import DEFAULT_WORKSPACE_STATE, PANEL_TOGGLES


class WorkspaceMixin:
    # ---- workspace layout persistence (per Tom -- see session_io.py's
    # docstring on why layout is separate from a saved SESSION) ----------

    def get_workspace_state(self):
        """Plain JSON-able dict capturing window size plus the ENTIRE dock
        layout (every panel's position, size, floating state, and
        geometry) via QMainWindow's own saveState() -- see this module's
        docstring, WORKSPACE PERSISTENCE, for why this replaced the old
        hand-rolled panels/floating/splitters dict once panels no longer
        have a fixed splitter to describe their position within. Called
        from closeEvent (auto-remember) and _export_workspace_preset;
        load_workspace_state below is the exact inverse, kept as a
        matched pair rather than inlining either direction elsewhere.

        NOT called by session save/load -- workspace has been separate
        from a saved .ltr session since session_io.SESSION_VERSION 7 (see
        that module's docstring).

        saveState() returns a QByteArray -- base64-encoded to a plain str
        so the result stays JSON-serializable, same as every other value
        workspace_io.py's json.dumps already needs to handle."""
        # normalGeometry() so un-maximizing after a relaunch goes back to
        # the user's own size, not a screen-sized "normal" window.
        size = self.normalGeometry().size() if self.isMaximized() else self.size()
        return {
            "window_size": [size.width(), size.height()],
            "maximized": self.isMaximized(),
            "dock_state_b64": base64.b64encode(bytes(self.saveState())).decode("ascii"),
            "media_table_columns": self.media_panel.get_header_state(),
        }

    def load_workspace_state(self, workspace):
        """Inverse of get_workspace_state. `workspace` is whatever the
        caller has on hand in that same dict shape -- workspace_io.
        load_remembered_workspace()'s result (see the startup call in
        __init__), an imported preset (see _import_workspace_preset), or
        None/empty (see _reset_workspace_to_default, which no longer goes
        through this method at all -- see that method's own docstring).
        An EMPTY/falsy dict, or one with no `dock_state_b64` key (e.g. a
        preset saved before this migration -- see workspace_io.py's
        WORKSPACE_FORMAT_VERSION bump), is a deliberate no-op here,
        leaving whatever's currently in place (the built-in default
        layout, on first launch) untouched, same tolerance this method
        has always had for a missing/malformed saved state.

        Window size is applied first, then the dock state -- restoreState()
        positions/sizes docks in terms of the window's OWN current size,
        so it needs to already be at its final saved size first, same
        ordering logic the pre-migration splitter-based version had for
        splitter sizes vs. window size.

        DEFERRED (per Tom's question, after #29-#32's fixes still didn't
        help -- "any chance this is related to the retain workspace
        between sessions feature?"): restoreState() runs via
        QTimer.singleShot(0, ...), not inline -- same one-tick deferral
        the pre-migration floating-restore loop used, and for the same
        reason: __init__ calls this method (with whatever workspace_io.
        load_remembered_workspace() returns) BEFORE main() ever calls
        window.show() or QApplication.exec() starts processing events.
        restoreState() can reconstruct a FLOATING dock widget (with a live
        QOpenGLWidget child, for the Graph panel) exactly like the old
        floating-restore loop could, so this migration keeps the same
        discipline rather than assuming saveState()/restoreState() are
        somehow immune to the timing risk #33 diagnosed."""
        # Absent key (no remembered workspace, or one saved before this
        # key existed) means maximized -- the default launch state.
        self._launch_maximized = bool((workspace or {}).get("maximized", True))
        self._using_default_dock_layout = not (workspace or {}).get("dock_state_b64")
        if self.isVisible():  # an imported preset, applied to a live window
            if self._launch_maximized:
                self.showMaximized()
            else:
                self.showNormal()
        self.media_panel.restore_header_state((workspace or {}).get("media_table_columns"))
        window_size = (workspace or {}).get("window_size")
        if window_size and len(window_size) == 2:
            try:
                self.resize(int(window_size[0]), int(window_size[1]))
            except (TypeError, ValueError):
                pass

        dock_state_b64 = (workspace or {}).get("dock_state_b64")
        if dock_state_b64:
            if self._launch_maximized and not self.isMaximized():
                # Restore only once the window is at its maximized size:
                # restoreState() at the 1200x800 construction size, then
                # maximizing, let Qt spread the extra width unevenly --
                # a layout saved at 16/60/24 came back ~23/47/30. The
                # timer is a fallback for a window manager that never
                # maximizes (show_on_launch's own retries notwithstanding).
                self._pending_dock_state_b64 = dock_state_b64
                QTimer.singleShot(1500, self._apply_pending_dock_state)
            else:
                QTimer.singleShot(0, lambda: self._apply_dock_state(dock_state_b64))

    def _apply_dock_state(self, dock_state_b64):
        """The actual restoreState() call load_workspace_state defers by
        one event-loop tick -- see that method's docstring (DEFERRED) for
        why. Split into its own method purely so it's a schedulable
        callback rather than inline code. Tolerates a malformed base64
        string (a hand-edited or corrupted preset file) the same way the
        rest of this method's caller tolerates a malformed dict -- skip
        rather than raise.

        RUNNING.md #60 -- Tom's own instinct, and he was right that this
        mattered (same remembered-workspace mechanism #29-#32's original
        crash chase already circled back to once before), though the
        deeper bug this call was actually masking turned out to be #59's
        coarse-area tracking itself (see _PanelDockWidget.closeEvent's
        docstring). Still correct and still needed regardless: whatever
        redock mechanism is in play, _build_layout's
        _snapshot_all_docked_state() call runs synchronously during
        __init__, which is BEFORE this deferred restoreState() ever runs
        -- so a snapshot taken only at that point describes the DEFAULT
        layout, not whatever Tom last actually had (this app is a "drag
        panels anywhere" tool Tom's been actively rearranging, so "still
        in the default spot" is the unusual case in practice, not the
        normal one). Nothing else tells _last_all_docked_state this
        restore happened, since restoreState() repositioning an
        already-docked widget is not a floating-state transition and
        never fires topLevelChanged (the only other thing that keeps
        _last_all_docked_state current -- see _on_dock_top_level_changed).
        Re-snapshotting here, right after the real layout is actually in
        place, is what makes the very next float-then-close on any panel
        restore to Tom's real remembered arrangement instead of the
        default that was superseded a tick earlier."""
        try:
            raw = base64.b64decode(dock_state_b64)
        except (ValueError, TypeError):
            return
        self.restoreState(QByteArray(raw))
        # A layout saved before the Source viewer existed doesn't mention
        # it (saveState stores each dock's objectName as UTF-16) -- put it
        # beside Record rather than wherever the default layout left it.
        if "source_viewer_frame".encode("utf-16-be") not in raw:
            self._place_source_viewer_beside_record()
        self._snapshot_all_docked_state()

    def _reset_workspace_to_default(self):
        """Workspace menu "Reset to Default Workspace" (per Tom) -- rebuilds
        the exact same default spatial arrangement _build_layout starts
        with (see _build_default_dock_layout), re-shows every panel, and
        resizes the window back to DEFAULT_WORKSPACE_STATE's size. Unlike
        before the docking migration, this does NOT go through
        load_workspace_state/restoreState() -- the default arrangement is
        deterministic CODE (the addDockWidget/splitDockWidget calls), not
        a serialized blob, so rebuilding it directly is both simpler and
        immune to whatever base64 blob happened to be captured for
        DEFAULT_WORKSPACE_STATE (there isn't one -- see that dict's own
        docstring, now just window_size).

        _dock_all_floating_panels() FIRST is required, not optional --
        confirmed empirically (in this sandbox) that neither
        addDockWidget() nor splitDockWidget() reliably redocks a widget
        that's ALREADY floating; both calls silently leave it floating
        rather than pulling it back in the way this method's first draft
        assumed. Explicitly docking everything before rebuilding the
        layout is what actually guarantees "reset" really means every
        panel ends up docked in its default spot, not still floating
        wherever it was."""
        self._dock_all_floating_panels()
        for _action_attr, _label, frame_attr, default_visible in PANEL_TOGGLES:
            getattr(self, frame_attr).setVisible(default_visible)
        self.resize(*DEFAULT_WORKSPACE_STATE["window_size"])
        self.showMaximized()
        self._build_default_dock_layout()
        self.media_panel.reset_columns()
        self._snapshot_all_docked_state()
        self._settling_default_layout = True
        QTimer.singleShot(1000, self._finish_launch_settling)

    def _export_workspace_preset(self):
        """Workspace menu "Export Workspace Preset..." (per Tom) -- saves
        the CURRENT arrangement (including floating state -- per Tom,
        "this needs to remember float states as well") to a user-chosen
        file via workspace_io.py, independent of the auto-remembered
        workspace (see that module's docstring on the two are separate
        uses of the same format)."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Workspace Preset", "workspace.lutlayout",
            "LUTier Workspace Preset (*.lutlayout);;JSON (*.json)",
        )
        if not path:
            return
        try:
            workspace_io.save_workspace_preset(path, self.get_workspace_state())
        except OSError as e:
            QMessageBox.critical(self, "Failed to save workspace preset", str(e))

    def _import_workspace_preset(self):
        """Inverse of _export_workspace_preset -- reads a preset file and
        applies it via load_workspace_state, same as Reset to Default or
        the startup remembered-workspace load."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Workspace Preset", "",
            "LUTier Workspace Preset (*.lutlayout *.json);;All files (*)",
        )
        if not path:
            return
        try:
            state = workspace_io.load_workspace_preset(path)
        except (OSError, ValueError) as e:
            QMessageBox.critical(self, "Failed to load workspace preset", str(e))
            return
        self.load_workspace_state(state)
