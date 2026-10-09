"""main_window_parts/menus.py -- the menu bar (File, Edit, Workspace, Help...)
and the small dialogs its actions open: the manual, accent colour and media
cache folder. A mixin: MainWindow inherits it, so these methods share the
window's own `self`.
"""
from PySide6.QtWidgets import (
    QApplication, QVBoxLayout, QFileDialog, QMessageBox, QDialog, QTextBrowser, QDialogButtonBox, QColorDialog,
)
from PySide6.QtGui import QKeySequence, QColor
from style import STYLESHEET, build_dark_palette, get_accent_color, set_accent_color
import media_cache
from help_text import USER_MANUAL_MARKDOWN
from floating_dialog import make_floating_tool_window, show_floating
from .constants import PANEL_TOGGLES


class MenuMixin:
    def _build_menu_bar(self):
        """Native File menu for session save/load -- see session_io.py for
        what a session actually captures (the Stack + sample image
        reference, every library, and -- since VERSION 6, per Tom -- the
        workspace layout too; see get_workspace_state/load_workspace_state
        below and session_io.py's docstring for the earlier "layout isn't
        project state" call this reversed). A menu bar rather than
        another inline button/"..." row since a session spans the whole
        window's state, not one panel -- File > Open/Save reads as the
        obvious place to look for it, same as literally every other
        desktop app, which an ad hoc button somewhere wouldn't.

        Workspace menu replaces the old on-screen toggle row entirely (per
        Tom) -- checkable QActions for each panel instead of a persistent
        row of buttons eating vertical space. Since the docking migration,
        each show/hide entry IS the panel's own QDockWidget.
        toggleViewAction() (Qt's own built-in mechanism), not a hand-rolled
        QAction main_window.py re-syncs itself -- Qt keeps it checked/
        unchecked correctly no matter how visibility actually changed
        (this menu, a drag, or the panel's own close button), which a
        hand-rolled action can't do for free.

        CATEGORY MENUS (per Tom): one top-level menu per panel that has a
        "..." corner menu -- LUT Library, Galleries,
        Viewer, Stack, Scopes, Graph -- each just calls that panel's own
        populate_menu(menu) (see each panel module's docstring) to add the
        EXACT same actions, wired to the exact same callback methods. This
        makes the "..." corner menus fully redundant (a second way to
        reach the same actions, per Tom's request) rather than replacing
        them -- both stay in sync automatically since neither one owns
        separate state, they just both populate from the same method.
        Graph is the newest addition here (per Tom's follow-up moving its
        3D/1D-view switch from an on-panel button pair into a "..." menu,
        see lut_viewer_3d.py's docstring) -- Grid density/Cage opacity stay
        inline in LutViewer3D's own layout, only view selection moved.
        Browser and Parameters still don't get a category menu here, since
        neither has a "..." menu of its own to mirror (Browser's actions
        are inline buttons; Parameters is just Stack's own inspector).

        KEYBOARD SHORTCUTS (per Tom): Cmd+N / Cmd+O / Cmd+S / Cmd+Shift+S /
        Cmd+E for New/Open/Save/Save As/Export (QKeySequence's "Ctrl+..."
        maps to Cmd on macOS automatically -- this is the portable way to
        write it, not a Mac-specific string), and Shift+1 through Shift+9
        for
        the NINE Workspace panel toggles, assigned by simple position in
        PANEL_TOGGLES (Browser=1, Media Libraries=2, LUT Library=3,
        Galleries=4, Viewer=5, Stack=6, Parameters=7,
        Scopes=8, Graph=9) -- set directly on each dock's own
        toggleViewAction() below. Extrapolating a new panel's shortcut is
        automatic (just its position in the list), not something to
        hand-assign each time a panel's added/reordered -- EXCEPT past
        position 9, where keyboards run out of single digits: a 10th panel
        would get Shift+0 rather than the nonsensical "Shift+10",
        following the ordinary keyboard-row convention of 0 coming right
        after 9 (used to be exercised for real, when CDL Library was its
        own 10th panel here -- folded into Galleries per Tom, see
        stack_memories_panel.py's own docstring, so all nine panels now
        fit within Shift+1..9 with no 0 needed; the code below still
        handles a >9th panel the same way if one's ever added again). If
        an 11th panel is ever added, this scheme has no more
        room and would need an actual redesign (e.g. Cmd+1..9,0 instead of
        Shift+), not just another special case.

        FLOATING (per Tom): Cmd+Shift+1 through Cmd+Shift+9 -- the exact
        same per-panel digit as its Shift+N
        toggle, with Cmd added -- pops that panel out of the main window
        into its own top-level window instead of showing/hiding it in
        place (see _float_panel/_dock_panel, both now thin wrappers over
        QDockWidget.setFloating()). A floated panel's window stays above
        the main window specifically (Qt.WindowType.Tool, confirmed
        empirically to be what QDockWidget's own native floating already
        sets -- see this module's docstring) but can go under OTHER
        applications, same scoping #39 established for this app's popup
        dialogs (floating_dialog.py) -- not "above literally everything"
        the way the pre-#39 WindowStaysOnTopHint mechanism was. Floating
        state IS captured now as part of the dock layout blob (see
        get_workspace_state/load_workspace_state) -- unlike before the
        docking migration, a remembered/preset workspace can restore a
        panel as floating, since that's just part of what
        QMainWindow.saveState() already captures; still never part of a
        saved SESSION (.ltr) though, same "layout isn't project data"
        split as always (see session_io.py)."""
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("&File")
        new_action = file_menu.addAction("New Session", self._new_session_from_menu)
        new_action.setShortcut(QKeySequence("Ctrl+N"))
        open_action = file_menu.addAction("Open Session...", self._open_session)
        open_action.setShortcut(QKeySequence("Ctrl+O"))
        file_menu.addSeparator()
        save_action = file_menu.addAction("Save Session", self._save_session)
        save_action.setShortcut(QKeySequence("Ctrl+S"))
        save_as_action = file_menu.addAction("Save Session As...", self._save_session_as)
        save_as_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        file_menu.addSeparator()
        # Project Settings... (was Canvas Settings, per Tom 2026-08-13) --
        # the project-level resolution/bit depth/pixel aspect/edit rate/
        # working color space, see canvas.py's docstring. Lives right
        # before Export since it's the OTHER file-level setting that
        # shapes what gets produced.
        file_menu.addAction("Project Settings...", self._show_canvas_settings_dialog)
        file_menu.addSeparator()
        # Export... (⌘E, per Tom) -- opens _show_export_dialog rather than
        # exporting straight away, same "menu entry, not an inline action"
        # shape as everything else in this menu; see that method's
        # docstring for why this replaced the old always-visible bottom
        # bar row.
        export_action = file_menu.addAction("Export...", self._show_export_dialog)
        export_action.setShortcut(QKeySequence("Ctrl+E"))
        # Export Video... (per Tom, 2026-08-13: "Video export") -- only
        # meaningful once a video is loaded in the Viewer (see
        # _show_export_video_dialog's own guard), but always visible in
        # the menu, same "menu entries don't disappear based on state"
        # convention every other action in this menu already follows.
        file_menu.addAction("Export Video...", self._show_export_video_dialog)

        # ---- Edit menu: Undo/Redo (per Tom) -- see undo_history.py's
        # docstring on scope (the working Stack only, for now) and
        # _on_stack_changed_for_history/_undo/_redo below for the actual
        # wiring. Actions stored on self so _update_undo_redo_actions can
        # enable/disable them as the history pointer moves.
        edit_menu = menu_bar.addMenu("&Edit")
        self.undo_action = edit_menu.addAction("Undo", self._undo)
        self.undo_action.setShortcut(QKeySequence("Ctrl+Z"))
        self.redo_action = edit_menu.addAction("Redo", self._redo)
        self.redo_action.setShortcut(QKeySequence("Ctrl+Shift+Z"))
        self._update_undo_redo_actions()

        # _panel_float_action_attr: frame_attr -> the Float action's attr
        # name, so _on_dock_top_level_changed can sync that checkbox when
        # a float/dock transition happens some OTHER way (a drag, or the
        # panel's own close button) -- built here since every dock already
        # exists (_build_layout ran first) and this is the one place that
        # already walks PANEL_TOGGLES to build the menu.
        self._panel_float_action_attr = {}

        workspace_menu = menu_bar.addMenu("&Workspace")

        # All nine show/hide toggles together first, THEN all nine Float
        # toggles together (per Tom -- this used to interleave each
        # panel's pair; two flat blocks read as two distinct KINDS of
        # control instead). Both loops walk PANEL_TOGGLES in the exact
        # same order, so a given row position means the same panel in
        # either block.
        for i, (action_attr, label, frame_attr, _default_visible) in enumerate(PANEL_TOGGLES, start=1):
            # Only matters once a 10th panel exists again -- see this
            # method's own docstring on Shift+0's fallback role.
            shortcut_digit = i if i <= 9 else 0
            # The dock's OWN toggleViewAction() -- Qt's built-in show/hide
            # action, kept correctly in sync with actual visibility no
            # matter how it changes (see this method's docstring). Just
            # needs its shortcut set and to be added to this menu; text
            # already matches the dock's windowTitle (== label).
            toggle_action = getattr(self, frame_attr).toggleViewAction()
            toggle_action.setShortcut(QKeySequence(f"Shift+{shortcut_digit}"))
            workspace_menu.addAction(toggle_action)
            setattr(self, action_attr, toggle_action)

        workspace_menu.addSeparator()

        for i, (_action_attr, label, frame_attr, _default_visible) in enumerate(PANEL_TOGGLES, start=1):
            shortcut_digit = i if i <= 9 else 0
            # Float (per Tom): Cmd+Shift+<same digit> -- pops the panel out
            # of the main window into its own always-on-top window (see
            # _float_panel). Every frame_attr in PANEL_TOGGLES ends in
            # "_frame" (see that list's own definition above), so this
            # derives a matching, readable attribute name
            # ("browser_frame" -> "browser_float_toggle") rather than
            # needing a second hand-maintained list kept in sync with the
            # first.
            float_action_attr = f"{frame_attr[:-len('_frame')]}_float_toggle"
            self._make_float_action(
                workspace_menu, float_action_attr, label, frame_attr,
                shortcut=f"Ctrl+Shift+{shortcut_digit}")
            self._panel_float_action_attr[frame_attr] = float_action_attr

        workspace_menu.addSeparator()
        reset_workspace_action = workspace_menu.addAction(
            "Reset to Default Workspace", self._reset_workspace_to_default)
        # No shortcut -- Tom asked for "Shift+W", but a bare Shift+letter
        # just types a capital letter, which a WindowShortcut-context
        # QAction shortcut would intercept and steal from any focused text
        # field. Flagged back to Tom; per his call, skipped rather than
        # reworked -- see RUNNING.md #47.

        workspace_menu.addSeparator()
        workspace_menu.addAction("Export Workspace Preset...", self._export_workspace_preset)
        workspace_menu.addAction("Import Workspace Preset...", self._import_workspace_preset)

        # Accent Color... (per Tom, "give users control over the accent
        # color," SIXTEENTH PASS) -- own separator, own group: this is a
        # THEME preference, not a panel-layout one (see theme_prefs.py's
        # own docstring on why it's persisted separately from workspace
        # state), so it reads as its own thing here too rather than
        # looking like a workspace preset option.
        workspace_menu.addSeparator()
        workspace_menu.addAction("Accent Color...", self._choose_accent_color)

        # Media Cache Folder... (Phase 1 of the Media Library/Stills
        # Gallery work, per Tom: "thumbnails can go in the session file,
        # but we should allow configuration of a sidecar gallery folder.
        # will need this for video cache eventually") -- own action
        # right alongside Accent Color, same "one app-level setting, no
        # Preferences dialog to hang it on" precedent (see media_cache.py's
        # own docstring).
        workspace_menu.addAction("Media Cache Folder...", self._choose_media_cache_folder)

        # ---- category menus -- see docstring above ------------------------
        media_library_menu = menu_bar.addMenu("Media Libraries")
        self.media_panel.populate_menu(media_library_menu)

        lut_library_menu = menu_bar.addMenu("LUT Library")
        self.lut_library_panel.populate_menu(lut_library_menu)

        stack_memories_menu = menu_bar.addMenu("Galleries")
        self.stack_memories_panel.populate_menu(stack_memories_menu)

        timeline_menu = menu_bar.addMenu("Timeline")
        self.timeline_panel.populate_menu(timeline_menu)

        viewer_menu = menu_bar.addMenu("Viewer")
        self.sample_image.populate_menu(viewer_menu)
        viewer_menu.addSeparator()
        # Match Frame (Avid/Resolve: F) -- the clip under Record's playhead
        # into the Source viewer on the same source frame.
        match_action = viewer_menu.addAction("Match Frame", self.match_frame)
        match_action.setShortcut(QKeySequence("F"))

        stack_menu = menu_bar.addMenu("Stack")
        self.stack_panel.populate_menu(stack_menu)

        scopes_menu = menu_bar.addMenu("Scopes")
        self.scopes_panel.populate_menu(scopes_menu)

        graph_menu = menu_bar.addMenu("Graph")
        self.viewer_3d.populate_menu(graph_menu)

        # ---- Help menu: Manual (per Tom) -- see _show_manual_dialog. Text
        # comes from help_text.USER_MANUAL_MARKDOWN, a plain Python string
        # constant, not a runtime read of docs/USER_MANUAL.md -- see that
        # module's docstring for why (same frozen-app resource-path class
        # of bug default_sample_image_path hit).
        help_menu = menu_bar.addMenu("&Help")
        help_menu.addAction("Manual", self._show_manual_dialog)

    def _show_manual_dialog(self):
        # Built lazily on first use rather than at __init__ time -- unlike
        # the per-panel dialogs (Adapt, Grid Size/Domain) this one has no
        # state to keep in sync with anything else, so there's no reason
        # to pay for building it before it's ever opened.
        if getattr(self, "_manual_dialog", None) is None:
            dialog = QDialog(self)
            make_floating_tool_window(dialog)
            dialog.setWindowTitle("LUTier Manual")
            dialog.resize(720, 640)
            outer = QVBoxLayout(dialog)

            browser = QTextBrowser()
            browser.setOpenExternalLinks(False)
            browser.setMarkdown(USER_MANUAL_MARKDOWN)
            outer.addWidget(browser, stretch=1)

            button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            button_box.rejected.connect(dialog.close)
            button_box.button(QDialogButtonBox.StandardButton.Close).clicked.connect(dialog.close)
            outer.addWidget(button_box)

            self._manual_dialog = dialog
        show_floating(self._manual_dialog)

    def _choose_accent_color(self):
        """Workspace menu "Accent Color..." (SIXTEENTH PASS, per Tom: "give
        users control over the accent color") -- opens a QColorDialog
        seeded with the CURRENT accent (get_accent_color(), not
        style.DEFAULT_ACCENT_HEX -- reopening the dialog should show what's
        actually applied right now, including a previously-picked color
        from an earlier session), and on OK: persists the choice
        (set_accent_color(), which both updates style.py's in-memory
        cache and writes theme_prefs.json -- see that function's
        docstring) and re-applies the palette/stylesheet to the live
        QApplication so the new color shows up immediately, with no
        restart required.

        Re-running app.setPalette(build_dark_palette())/
        app.setStyleSheet(STYLESHEET) against the singleton
        (QApplication.instance(), same instance __init__'s own
        installEventFilter(self) call above uses) is the same one-two
        order main() uses at startup, for the same reason documented
        there: build_dark_palette() now reads get_accent_color() itself
        (style.py SIXTEENTH PASS), so simply calling it again after
        set_accent_color() has updated the cache is enough to pick up
        the new color everywhere a `palette(...)` QSS reference or a
        QPalette role resolves it -- no per-widget update needed.

        QColorDialog returns an invalid QColor (isValid() False) if the
        user hits Cancel or closes the dialog -- checked for and treated
        as a no-op, same as every other file-picker in this file
        returning an empty path."""
        initial = QColor(get_accent_color())
        color = QColorDialog.getColor(initial, self, "Choose Accent Color")
        if not color.isValid():
            return
        set_accent_color(color.name())
        app = QApplication.instance()
        app.setPalette(build_dark_palette())
        app.setStyleSheet(STYLESHEET)

    def _choose_media_cache_folder(self):
        """Workspace menu "Media Cache Folder..." (Phase 1 of the Media
        Library/Stills Gallery work) -- offers a choice between picking a
        sidecar folder (media_cache.set_cache_dir persists it; future
        thumbnails/video proxies get written there instead of embedded in
        the session file) or clearing back to embed-in-session mode (the
        default), via a small custom-button QMessageBox rather than a new
        dialog class -- one setting doesn't warrant one, same reasoning
        as _choose_accent_color popping QColorDialog directly.

        Seeds the QFileDialog at the currently-configured folder (if any)
        so reopening this shows where it's already pointed, same
        "reflect current state, not a blank default" precedent as
        _choose_accent_color seeding QColorDialog from get_accent_color()."""
        current = media_cache.get_cache_dir()
        box = QMessageBox(self)
        box.setWindowTitle("Media Cache Folder")
        box.setText(
            f"Current: {current}" if current is not None
            else "Current: embedded in session file (no cache folder set)")
        choose_button = box.addButton("Choose Folder...", QMessageBox.ActionRole)
        clear_button = box.addButton("Clear (Embed in Session)", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is choose_button:
            start_dir = str(current) if current is not None else ""
            path = QFileDialog.getExistingDirectory(self, "Choose Media Cache Folder", start_dir)
            if path:
                media_cache.set_cache_dir(path)
        elif clicked is clear_button:
            media_cache.set_cache_dir(None)
