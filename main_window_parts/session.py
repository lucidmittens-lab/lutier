"""main_window_parts/session.py -- sessions (.ltr via session_io: new, open,
save, save as) and project setup: the Project Settings form, canvas
settings and the sample image. A mixin: MainWindow inherits it, so these
methods share the window's own `self`.
"""
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QMessageBox, QSpinBox, QVBoxLayout, QWidget,
)
import colour_pipeline
import canvas as cv
import aces_drt
from spinbox_scrub import enable_drag_to_scrub
from PySide6.QtCore import QTimer
import session_io
import stack as st
from stack_memory import StackMemoryLibrary, GalleryStillLibrary
from cdl_library import CDLLibrary
from lut_library import LUTLibrary
from media_library import MediaPool
from undo_history import UndoHistory
from floating_dialog import make_floating_tool_window, show_floating


class _ProjectSettingsForm(QWidget):
    """The project fields -- resolution, bit depth, pixel aspect, edit
    rate, working color space -- shared by File > Project Settings and
    the New Project prompt. Width/height/PAR use enable_drag_to_scrub,
    matching every other numeric field in this app."""

    def __init__(self, parent=None):
        super().__init__(parent)
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        def row(label, widget):
            form.addRow(label, widget)

        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 33000)
        self.width_spin.setSuffix(" px")
        enable_drag_to_scrub(self.width_spin)
        row("Width", self.width_spin)

        self.height_spin = QSpinBox()
        self.height_spin.setRange(1, 33000)
        self.height_spin.setSuffix(" px")
        enable_drag_to_scrub(self.height_spin)
        row("Height", self.height_spin)

        self.bit_depth_combo = QComboBox()
        for depth in cv.BIT_DEPTHS:
            self.bit_depth_combo.addItem(cv.BIT_DEPTH_LABELS[depth], depth)
        row("Bit Depth", self.bit_depth_combo)

        self.par_spin = QDoubleSpinBox()
        self.par_spin.setRange(0.01, 10.0)
        self.par_spin.setDecimals(4)
        self.par_spin.setSingleStep(0.01)
        enable_drag_to_scrub(self.par_spin)
        row("Pixel Aspect Ratio", self.par_spin)

        # Editable -- FRAME_RATE_PRESETS back the dropdown, but any
        # positive rate can be typed in (odd rates are real, e.g. 47.952).
        self.frame_rate_combo = QComboBox()
        self.frame_rate_combo.setEditable(True)
        for rate in cv.FRAME_RATE_PRESETS:
            self.frame_rate_combo.addItem(str(rate))
        row("Edit Rate", self.frame_rate_combo)

        self.working_space_combo = QComboBox()
        if aces_drt.OCIO_AVAILABLE:
            self.working_space_combo.addItems(aces_drt.list_working_colourspaces())
        row("Working Color Space", self.working_space_combo)

        # Display transform (colour_pipeline.py): what Record views the
        # working-space image through. Off by default.
        self.display_drt_combo = QComboBox()
        self.display_drt_combo.addItem("None (view the working space)", "none")
        self.display_drt_combo.addItem("ACES Output Transform", "aces")
        row("Display Transform", self.display_drt_combo)
        self._displays = dict(colour_pipeline.available_displays())
        self.display_combo = QComboBox()
        self.display_combo.addItems(list(self._displays))
        row("Display", self.display_combo)
        self.view_combo = QComboBox()
        row("View", self.view_combo)
        self.display_combo.currentTextChanged.connect(self._refresh_views)
        self.display_drt_combo.currentIndexChanged.connect(self._refresh_display_enabled)
        self._refresh_views(self.display_combo.currentText())
        self._refresh_display_enabled()

    def _refresh_views(self, display):
        current = self.view_combo.currentText()
        self.view_combo.clear()
        self.view_combo.addItems(self._displays.get(display, []))
        idx = self.view_combo.findText(current)
        if idx >= 0:
            self.view_combo.setCurrentIndex(idx)

    def _refresh_display_enabled(self):
        on = self.display_drt_combo.currentData() != "none"
        self.display_combo.setEnabled(on)
        self.view_combo.setEnabled(on)

    def set_canvas(self, c):
        self.width_spin.setValue(c.width)
        self.height_spin.setValue(c.height)
        idx = self.bit_depth_combo.findData(c.bit_depth)
        if idx >= 0:
            self.bit_depth_combo.setCurrentIndex(idx)
        self.par_spin.setValue(c.pixel_aspect_ratio)
        self.frame_rate_combo.setCurrentText(str(c.frame_rate))
        idx = self.working_space_combo.findText(c.working_colourspace)
        if idx < 0:  # a session saved with a space this config lacks
            self.working_space_combo.addItem(c.working_colourspace)
            idx = self.working_space_combo.count() - 1
        self.working_space_combo.setCurrentIndex(idx)
        self.display_drt_combo.setCurrentIndex(max(0, self.display_drt_combo.findData(c.display_drt)))
        idx = self.display_combo.findText(c.display_name)
        if idx >= 0:
            self.display_combo.setCurrentIndex(idx)
        self._refresh_views(self.display_combo.currentText())
        idx = self.view_combo.findText(c.display_view)
        if idx >= 0:
            self.view_combo.setCurrentIndex(idx)
        self._refresh_display_enabled()

    def canvas_or_report_error(self, parent):
        """The CanvasSettings these fields describe, or None after
        showing what's wrong."""
        text = self.frame_rate_combo.currentText()
        try:
            frame_rate = float(text)
        except ValueError:
            QMessageBox.critical(parent, "Invalid edit rate", f"{text!r} isn't a valid frame rate.")
            return None
        try:
            return cv.CanvasSettings(
                width=self.width_spin.value(), height=self.height_spin.value(),
                bit_depth=self.bit_depth_combo.currentData(),
                pixel_aspect_ratio=self.par_spin.value(), frame_rate=frame_rate,
                working_colourspace=self.working_space_combo.currentText(),
                display_drt=self.display_drt_combo.currentData() or "none",
                display_name=self.display_combo.currentText() or cv.DEFAULT_DISPLAY_NAME,
                display_view=self.view_combo.currentText() or cv.DEFAULT_DISPLAY_VIEW)
        except ValueError as e:
            QMessageBox.critical(parent, "Invalid project settings", str(e))
            return None


class SessionMixin:
    def _on_sample_image_loaded(self, width, height):
        """The one auto-sync carve-out canvas.py's docstring documents:
        seed width/height from the FIRST still/video loaded this session,
        but only while the canvas is still untouched (`== DEFAULT_CANVAS`)
        -- after that, Canvas Settings is fully independent and this is a
        no-op for the rest of the session, even across further loads."""
        if not self._canvas_confirmed and self._canvas == cv.DEFAULT_CANVAS:
            self._canvas = cv.canvas_from_first_media(
                width, height, working_colourspace=self._canvas.working_colourspace)
            self._apply_canvas_to_viewer()

    def _apply_canvas_to_viewer(self):
        """Pushes self._canvas's own aspect ratio (width * pixel_aspect_
        ratio / height -- PAR folded in so an anamorphic canvas letterboxes
        correctly too, not just square-pixel ones) into the Viewer, so it
        letterboxes/pillarboxes to the CANVAS's shape rather than whatever
        image happens to be loaded (Tom, 2026-08-13: confirmed this is
        the desired visible effect of a Canvas Settings change). Called
        after every single place self._canvas is reassigned -- see this
        method's call sites (init, _on_sample_image_loaded, _new_session,
        _open_session, _apply_canvas_settings)."""
        c = self._canvas
        self.sample_image.set_canvas_aspect_ratio((c.width * c.pixel_aspect_ratio) / c.height)
        self.stack_panel.set_default_working_colourspace(c.working_colourspace)

    def _new_session(self):
        if (self.stack_panel.get_sequence_stack().stages
                or self.stack_memories_panel.get_library().memories
                or self.stack_memories_panel.get_gallery_library().stills
                or self.stack_memories_panel.get_cdl_library().entries
                or self.lut_library_panel.get_library().entries
                or not self.media_panel.get_pool().is_empty()):
            reply = QMessageBox.question(
                self, "New Session",
                "Discard the current stack, Galleries, "
                "LUT Library, Media Libraries, and Timeline and start a "
                "new session? Unsaved changes will be lost.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return False
        # Floating state was never part of "the session" (see
        # get_workspace_state) -- New Session doesn't touch workspace
        # layout otherwise, so this is the one place that needs its own
        # explicit call rather than inheriting it from load_workspace_state.
        self._dock_all_floating_panels()
        new_stack = st.Stack()
        self._restoring_stack_history = True
        try:
            # Force back to Sequence mode BEFORE resetting the Timeline
            # below -- if Shot mode were still pointed at a clip's Stack
            # here, set_stack() just below would silently update the
            # (about-to-be-discarded) sequence stack in the background
            # rather than visibly resetting the editor, leaving the Stack
            # panel showing a clip Stack object that's no longer part of
            # any Timeline. Wrapped in the same _restoring_stack_history
            # block as set_stack() just below -- both are wholesale
            # replaces, neither should create a spurious undo entry.
            self.stack_panel.notify_selected_clip_changed(None)
            self.stack_panel.set_stack(new_stack)
        finally:
            self._restoring_stack_history = False
        # Fresh history for this new, empty stack -- undo must not be able
        # to walk back into whatever the previous (now-discarded) session
        # had (per Tom: "reset on new/open session"). Cancels (not
        # flushes) any debounced push still pending from an edit right
        # before New Session was invoked -- it would only ever target the
        # history object about to be discarded here anyway.
        self._undo_debounce_timer.stop()
        self._undo_history = UndoHistory(new_stack.copy())
        self._update_undo_redo_actions()
        self.stack_memories_panel.load_library(StackMemoryLibrary())
        self.stack_memories_panel.load_gallery_library(GalleryStillLibrary())
        self.stack_memories_panel.load_cdl_library(CDLLibrary())
        self.lut_library_panel.load_library(LUTLibrary())
        self.media_panel.load_pool(MediaPool())
        self._canvas = cv.DEFAULT_CANVAS
        self._canvas_confirmed = False
        self._bake_settings = st.DEFAULT_BAKE_SETTINGS
        # stop(), not pause() -- same "about to be replaced wholesale,
        # don't bother re-parking" reasoning as _close_video_source's own
        # use of _playback_controller.stop() -- also resets position back
        # to clip 0 so a later Play doesn't try to resume into a clip
        # index that belonged to the just-discarded Timeline.
        self.sample_image._timeline_controller.stop()
        self.timeline_panel.load_timeline(None)
        self._apply_canvas_to_viewer()
        # Empty, like a fresh launch (no bundled test image) -- the menu
        # path follows up with the New Project prompt, see
        # _new_session_from_menu.
        self.sample_image.clear()
        self._session_path = None
        return True

    def _open_session(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Session", "",
            "LUTier Session (*.ltr *.lutier);;All files (*)",
        )
        if not path:
            return
        try:
            (new_stack, bake_settings, sample_image_state, memory_library, gallery_still_library,
             cdl_library, lut_library, media_pool, undo_history_state,
             loaded_canvas, loaded_timeline, warnings) = session_io.load_session(path)
        except Exception as e:  # noqa: BLE001 -- surface any read/parse failure to the user
            QMessageBox.critical(self, "Failed to open session", str(e))
            return

        self._restoring_stack_history = True
        try:
            # Same "force back to Sequence before the swap, wrapped
            # together" reasoning as _new_session -- the OLD session's
            # Timeline (and whatever clip Shot mode might be pointed at)
            # is about to be replaced wholesale by set_stack() just below.
            self.stack_panel.notify_selected_clip_changed(None)
            self.stack_panel.set_stack(new_stack)
        finally:
            self._restoring_stack_history = False
        # Reset undo history to this session's own -- either what it was
        # saved with (undo_history_state) or, for a file with none (no
        # history saved, or a pre-version-8 file), a fresh single-state
        # history for the stack just loaded. Either way, undo can no
        # longer walk back into whatever the PREVIOUS session had (per
        # Tom: "reset on new/open session but keep undo history inside of
        # project data if possible"). Cancels (not flushes) any debounced
        # push still pending from an edit right before Open Session was
        # invoked -- it would only ever target the history object about
        # to be replaced below anyway.
        self._undo_debounce_timer.stop()
        if undo_history_state is not None:
            states, pointer = undo_history_state
            self._undo_history = UndoHistory.from_states(states, pointer)
        else:
            self._undo_history = UndoHistory(new_stack.copy())
        self._update_undo_redo_actions()
        self.stack_memories_panel.load_library(memory_library)
        self.stack_memories_panel.load_gallery_library(gallery_still_library)
        self.stack_memories_panel.load_cdl_library(cdl_library)
        self.lut_library_panel.load_library(lut_library)
        self.media_panel.load_pool(media_pool)
        if sample_image_state is not None:
            self.sample_image.load_session_state(sample_image_state)
        else:
            # Saved with no Source loaded -- the norm since launch and New
            # Session start empty. Without this, the previous session's
            # image would stay in the Viewer under the new session.
            self.sample_image.clear()
        # Set AFTER load_session_state above, not before -- reloading the
        # sample image fires image_loaded too, and this session's own
        # saved canvas (whatever it is) must win outright over any
        # incidental reseed-from-image-dimensions side effect of that
        # reload (see _on_sample_image_loaded).
        self._canvas = loaded_canvas
        self._canvas_confirmed = True
        self._bake_settings = bake_settings
        # Same "stop, don't re-park, reset position" reasoning as
        # _new_session's own use of this call, just above.
        self.sample_image._timeline_controller.stop()
        self.timeline_panel.load_timeline(loaded_timeline)
        self._apply_canvas_to_viewer()
        # No workspace-layout step here anymore (see session_io.py's
        # VERSION 7 note) -- opening a session no longer touches panel
        # visibility/floating/splitter sizes/window size at all, only the
        # project data above. Current layout (whatever the remembered
        # workspace or a manually applied preset last set) is left exactly
        # as it was.
        self._session_path = path

        if warnings:
            QMessageBox.warning(
                self, "Session opened with warnings",
                "Loaded, but some stages couldn't be restored:\n\n" + "\n".join(warnings))

    def _save_session(self):
        if self._session_path is None:
            self._save_session_as()
            return
        self._write_session(self._session_path)

    def _save_session_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Session As", "session.ltr",
            "LUTier Session (*.ltr);;JSON (*.json)",
        )
        if not path:
            return
        self._write_session(path)

    def _write_session(self, path):
        # So the undo_history embedded in the saved file's last entry
        # always matches the stack actually being saved, rather than
        # potentially lagging by one still-pending debounced edit (see
        # _flush_pending_undo_push's own docstring).
        self._flush_pending_undo_push()
        try:
            warnings = session_io.save_session(
                path, self.stack_panel.get_sequence_stack(), self.sample_image.get_session_state(),
                stack_memory_library=self.stack_memories_panel.get_library(),
                gallery_still_library=self.stack_memories_panel.get_gallery_library(),
                cdl_library=self.stack_memories_panel.get_cdl_library(),
                lut_library=self.lut_library_panel.get_library(),
                media_pool=self.media_panel.get_pool(),
                undo_history=self._undo_history, canvas=self._canvas,
                loaded_timeline=self.timeline_panel.get_timeline(),
                bake_settings=self._bake_settings)
        except Exception as e:  # noqa: BLE001 -- surface any write failure to the user
            QMessageBox.critical(self, "Failed to save session", str(e))
            return
        self._session_path = path
        if warnings:
            # save_session's own docstring: only stack_memories/undo_history
            # entries can be dropped this way -- the current working stack
            # already saved fine, or this except block above would have
            # fired instead. Same "warn, don't silently lose it" convention
            # as the load path's own dialog just above.
            QMessageBox.warning(
                self, "Session saved with warnings",
                "Saved, but some old data couldn't be included:\n\n" + "\n".join(warnings))

    def _show_canvas_settings_dialog(self):
        """File > Project Settings... (was Canvas Settings, Tom 2026-08-13;
        renamed when the working color space joined it, 2026-10-05) --
        same lazy-built-cached-QDialog shape as _show_export_dialog. The
        fields are a _ProjectSettingsForm, shared with the New Project
        prompt (_prompt_project_setup); every open re-populates it FROM
        self._canvas, and Apply is the only thing that writes back."""
        if getattr(self, "_canvas_settings_dialog", None) is None:
            dialog = QDialog(self)
            make_floating_tool_window(dialog)
            dialog.setWindowTitle("Project Settings")
            outer = QVBoxLayout(dialog)
            self._project_settings_form = _ProjectSettingsForm()
            outer.addWidget(self._project_settings_form)

            button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
            apply_button = button_box.addButton(
                "Apply", QDialogButtonBox.ButtonRole.AcceptRole)
            apply_button.clicked.connect(self._apply_canvas_settings)
            button_box.rejected.connect(dialog.close)
            outer.addWidget(button_box)

            self._canvas_settings_dialog = dialog

        self._project_settings_form.set_canvas(self._canvas)
        show_floating(self._canvas_settings_dialog)

    def _apply_canvas_settings(self):
        """Project Settings' own "Apply" button. A bad entry (e.g. an
        unparseable frame rate) reports an error and leaves self._canvas
        untouched rather than half-applying the other fields."""
        canvas = self._project_settings_form.canvas_or_report_error(self)
        if canvas is None:
            return
        self._set_project_canvas(canvas)
        self._canvas_settings_dialog.close()

    def _set_project_canvas(self, canvas):
        """Every user-confirmed project canvas lands here (Project
        Settings Apply, the New Project prompt). Marks it confirmed so
        the first-loaded media no longer reseeds it -- see
        _on_sample_image_loaded."""
        self._canvas = canvas
        self._canvas_confirmed = True
        self._apply_canvas_to_viewer()
        self.sample_image.refresh_preview()  # display transform / working space may have changed

    def _prompt_project_setup(self):
        """The New Project prompt (Tom, 2026-10-05: "default to launching
        without the test image loaded and prompting for a project setup
        with the edit rate, resolution, and working color space"). Shown
        at launch (show_on_launch) and after File > New Session -- never
        from _new_session itself, which tests and other callers use as a
        plain reset. Cancel keeps the defaults; "Open Session..." skips
        setup in favour of a saved session, whose own canvas wins."""
        dialog = QDialog(self)
        dialog.setWindowTitle("New Project")
        outer = QVBoxLayout(dialog)
        form = _ProjectSettingsForm()
        form.set_canvas(self._canvas)
        outer.addWidget(form)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        create_button = button_box.addButton(
            "Create Project", QDialogButtonBox.ButtonRole.AcceptRole)
        open_button = button_box.addButton(
            "Open Session...", QDialogButtonBox.ButtonRole.ActionRole)
        create_button.setDefault(True)

        def create():
            canvas = form.canvas_or_report_error(dialog)
            if canvas is not None:
                self._set_project_canvas(canvas)
                dialog.accept()

        def open_session():
            dialog.reject()
            QTimer.singleShot(0, self._open_session)

        create_button.clicked.connect(create)
        open_button.clicked.connect(open_session)
        button_box.rejected.connect(dialog.reject)
        outer.addWidget(button_box)
        dialog.exec()

    def _new_session_from_menu(self):
        if self._new_session():
            self._prompt_project_setup()
