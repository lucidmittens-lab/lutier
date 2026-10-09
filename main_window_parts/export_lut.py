"""main_window_parts/export_lut.py -- the Export LUT dialog and bake: format
choice (EXPORT_FORMATS), bake settings, the export source, writing the
file, and the optional still exported alongside. A mixin: MainWindow
inherits it, so these methods share the window's own `self`.
"""
from pathlib import Path
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout,
    QComboBox, QFileDialog, QMessageBox, QLabel, QCheckBox,
    QDialog, QDialogButtonBox, QGroupBox,
)
from PySide6.QtCore import Qt
from bake_settings_form import BakeSettingsForm, describe_bake_settings
from clf_adapter import colour_lut3d_to_clf_node, process_list_wrapping
from format_writers import write_3dl, write_truelight_cub
from app_logic import export_image_array_to_file
import stack as st
from floating_dialog import make_floating_tool_window, show_floating
import colour
import colour_clf_io as clf


# (label, file extension, writer key) -- a plain list rather than a dict
# keyed by extension, since the two .3dl dialects share an extension and
# need distinct writer keys to disambiguate.
EXPORT_FORMATS = [
    ("Cube (Resolve/Iridas)", ".cube", "native"),
    ("CLF (Common LUT Format)", ".clf", "clf"),
    ("Sony SPI3D", ".spi3d", "native"),
    ("Cinespace", ".csp", "native"),
    ("Autodesk 3D LUT -- Flame dialect", ".3dl", "3dl_flame"),
    ("Autodesk 3D LUT -- Lustre dialect", ".3dl", "3dl_lustre"),
    ("FilmLight Truelight/Baselight Cube", ".cub", "truelight_cub"),
]


class ExportLutMixin:
    def _show_export_dialog(self):
        """File > Export... (⌘E, per Tom) -- replaces the old always-
        visible bottom-bar row (format dropdown + "Export..." button) with
        a small dialog reachable from the menu, same "menu entry over a
        row that eats vertical space for the whole session" call already
        made for the Workspace panel toggles (see _build_menu_bar's
        docstring) and Stack Params' Grid Size/Domain controls (see
        stack_panel.py). Built lazily on first use, same pattern as
        _show_manual_dialog -- self.format_combo and the four checkboxes
        (export_still_checkbox/export_still_to_library_checkbox/
        export_add_to_library_checkbox/export_save_memory_checkbox) are
        the pieces of state that need to survive across opens (format
        choice, and whichever boxes were left checked), and they do,
        since closing this dialog doesn't destroy it, only hides it (see
        button_box.rejected below). export_still_to_library_checkbox is a
        SECONDARY checkbox, indented under and gated by Export Still
        itself -- see _on_export_still_toggled.

        export_specs_label (per Tom -- basic specs of the LUT about to be
        exported: grid size, domain, and the stage list) is refreshed on
        EVERY call, not just when the dialog is first built -- the Stack
        can change between one Export... and the next, so the widget tree
        is built once but its text always reflects the live Stack at the
        moment the dialog is (re)shown, same "build once, refresh every
        open" split _show_export_dialog already uses for format_combo's
        surrounding chrome vs. its own persisted selection.

        The dialog's own "Export..." button calls export_current_lut
        directly -- unchanged from what the old bottom-bar button called,
        plus now reading all four checkboxes at the end of a successful
        export (see that method) -- this method only owns getting the
        format-picker/specs/checkbox UI on screen, not the export logic
        itself."""
        if getattr(self, "_export_dialog", None) is None:
            dialog = QDialog(self)
            make_floating_tool_window(dialog)
            dialog.setWindowTitle("Export LUT")

            outer = QVBoxLayout(dialog)
            row = QHBoxLayout()
            row.addWidget(QLabel("Export current stack as:"))
            self.format_combo = QComboBox()
            for label, ext, writer_key in EXPORT_FORMATS:
                self.format_combo.addItem(label, (ext, writer_key))
            row.addWidget(self.format_combo, stretch=1)
            outer.addLayout(row)

            self.export_specs_label = QLabel()
            self.export_specs_label.setWordWrap(True)
            self.export_specs_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            # palette(highlight) rather than palette(mid) (per Tom -- the
            # mid-gray hint-label color used elsewhere was too low-contrast
            # to read comfortably in dark mode here). This is the same
            # palette(highlight) blue already used for stack_panel.py's
            # enabled-stage toggle/delete-button hover (see style.py's
            # QToolButton#stageEnabledToggle/#stageDeleteButton rules) --
            # an accent color Qt keeps legible against both light and
            # dark palettes, not a hardcoded hex that could fight either.
            self.export_specs_label.setStyleSheet("color: palette(highlight); font-size: 11px;")
            outer.addWidget(self.export_specs_label)

            lut_group = QGroupBox("LUT Grid / Input Domain")
            lut_group_layout = QVBoxLayout(lut_group)
            self._bake_settings_form = BakeSettingsForm(self.stack_panel.get_stack)
            self._bake_settings_form.settings_changed.connect(self._set_bake_settings)
            lut_group_layout.addWidget(self._bake_settings_form)
            # Full chain (colour_pipeline.py): bake the source's input
            # transform and the project display transform around the
            # grade -- a camera-log -> display "show LUT" for set/dailies.
            # Off: the grade only, working space in and out.
            self.export_full_chain_checkbox = QCheckBox(
                "Full chain: include input and display transforms (show LUT)")
            self.export_full_chain_checkbox.setToolTip(
                "Bakes Input -> grade -> Display, as Record shows it. Unchecked: the "
                "grade only, in the project working space.")
            self.export_full_chain_checkbox.toggled.connect(
                lambda _on: self.export_specs_label.setText(self._export_specs_text()))
            lut_group_layout.addWidget(self.export_full_chain_checkbox)
            outer.addWidget(lut_group)

            # Three top-level checkboxes (per Tom), in this order -- each
            # an independent optional side effect of the same Export
            # click, none of them prompting its own separate dialog (see
            # export_current_lut: still path/library name/memory name are
            # all derived automatically, same "one Export, no follow-up
            # prompts" shape the format/library checkbox already had).
            self.export_still_checkbox = QCheckBox("Export Still")
            self.export_still_checkbox.toggled.connect(self._on_export_still_toggled)
            outer.addWidget(self.export_still_checkbox)

            # export_still_to_library_checkbox is a SECONDARY checkbox
            # (per Tom) -- indented under, and only meaningful alongside,
            # Export Still (there's no still to import if that one's
            # unchecked), so it's disabled and force-unchecked whenever
            # Export Still itself is off -- see _on_export_still_toggled.
            still_sub_row = QHBoxLayout()
            still_sub_row.addSpacing(20)
            self.export_still_to_library_checkbox = QCheckBox("Import Still to Media Libraries")
            self.export_still_to_library_checkbox.setEnabled(False)
            still_sub_row.addWidget(self.export_still_to_library_checkbox)
            outer.addLayout(still_sub_row)

            self.export_add_to_library_checkbox = QCheckBox("Import to LUT Library")
            outer.addWidget(self.export_add_to_library_checkbox)

            self.export_save_memory_checkbox = QCheckBox("Grab Still")
            outer.addWidget(self.export_save_memory_checkbox)

            button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
            export_button = button_box.addButton(
                "Export...", QDialogButtonBox.ButtonRole.AcceptRole)
            export_button.clicked.connect(self.export_current_lut)
            button_box.rejected.connect(dialog.close)
            outer.addWidget(button_box)

            self._export_dialog = dialog
        self._bake_settings_form.set_settings(self._bake_settings)
        self.export_specs_label.setText(self._export_specs_text())
        show_floating(self._export_dialog)

    def _set_bake_settings(self, settings):
        """The Export dialog's LUT grid/domain fields (BakeSettingsForm)
        land here on every valid edit -- saved with the session, used
        only when a LUT is actually exported (_resolve_export_source)."""
        self._bake_settings = settings
        if getattr(self, "export_specs_label", None) is not None:
            self.export_specs_label.setText(self._export_specs_text())

    def _on_export_still_toggled(self, checked):
        """export_still_to_library_checkbox only makes sense alongside
        Export Still itself (see _show_export_dialog) -- disabled AND
        force-unchecked the moment Export Still is turned off, so it
        can't be left silently checked-but-inert (and, if Export Still is
        turned back on later, the user has to consciously re-check it
        rather than a stale checked state from a previous, unrelated
        export quietly carrying forward)."""
        self.export_still_to_library_checkbox.setEnabled(checked)
        if not checked:
            self.export_still_to_library_checkbox.setChecked(False)

    def _export_specs_text(self):
        """Basic-facts summary shown in the Export dialog (per Tom): grid
        size and domain (self._bake_settings, see describe_bake_settings)
        and the live Stack's stage list -- no bake needed just to preview
        what's about to be exported."""
        stack = self.stack_panel.get_stack()
        lines = [describe_bake_settings(self._bake_settings)]
        full_chain = getattr(self, "export_full_chain_checkbox", None)
        if full_chain is not None and full_chain.isChecked():
            lines.append(self.stack_panel.pipeline_label.text())

        if stack.stages:
            plural = "" if len(stack.stages) == 1 else "s"
            lines.append(f"Stack ({len(stack.stages)} stage{plural}):")
            for i, stage in enumerate(stack.stages, start=1):
                suffix = "" if stage.enabled else "  (disabled)"
                lines.append(f"  {i}. {stage.label}{suffix}")
        else:
            lines.append("Stack: empty")
        return "\n".join(lines)

    def _resolve_export_source(self):
        """Return (lut, default_filename_stem), or None if it warned the
        user about why it can't (empty stack) -- caller should just return
        in that case, the warning's already shown. Bakes the stack fresh
        every export (same get_baked_lut() call _on_stack_changed uses)
        rather than requiring any prior commit step -- exporting is a read
        of "what the stack currently bakes to," there's nothing separate
        to commit to first."""
        if not self.stack_panel.get_stack().stages:
            QMessageBox.warning(
                self, "Empty stack",
                "Add stages to the Stack first.")
            return None
        full_chain = getattr(self, "export_full_chain_checkbox", None)
        try:
            if full_chain is not None and full_chain.isChecked():
                lut = st.bake_stack_to_lut3d(self._composed_preview_stack(), self._bake_settings)
            else:
                lut = self.stack_panel.get_baked_lut(self._bake_settings)
        except Exception as e:  # noqa: BLE001 -- surface any bake failure to the user
            QMessageBox.critical(self, "Bake failed", str(e))
            return None
        return lut, "stack_export"

    def export_current_lut(self):
        """Called by the Export dialog's "Export..." button (see
        _show_export_dialog) -- unchanged from when the old bottom-bar
        button called it directly, except for two things at the end of a
        successful export: honoring export_add_to_library_checkbox (per
        Tom, see LUTLibraryPanel.import_lut_object's docstring for why
        this hands over lut_to_export -- the in-memory baked object --
        rather than re-reading the file just written), and closing the
        dialog (a cancelled file picker or a failed bake/write returns
        early above that, same as before, leaving the dialog open so the
        format choice/checkbox state isn't lost)."""
        resolved = self._resolve_export_source()
        if resolved is None:
            return
        lut_to_export, default_stem = resolved

        target_ext, writer_key = self.format_combo.currentData()
        default_name = default_stem + target_ext
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Export LUT", default_name, f"*{target_ext}")
        if not out_path:
            return

        try:
            if writer_key == "clf":
                node = colour_lut3d_to_clf_node(lut_to_export, node_id="lut")
                process_list = process_list_wrapping(
                    [node], list_id=lut_to_export.name or "export",
                    name=lut_to_export.name)
                with open(out_path, "w") as f:
                    f.write(clf.write_clf(process_list))
            elif writer_key == "3dl_flame":
                write_3dl(lut_to_export, out_path, variant="flame")
            elif writer_key == "3dl_lustre":
                write_3dl(lut_to_export, out_path, variant="lustre")
            elif writer_key == "truelight_cub":
                write_truelight_cub(lut_to_export, out_path)
            else:
                colour.write_LUT(lut_to_export, out_path)
        except Exception as e:  # noqa: BLE001 -- surface any writer failure to the user
            QMessageBox.critical(self, "Export failed", str(e))
            return

        # Three optional side effects (per Tom), each independent of the
        # other two -- none of them can undo the LUT file that's already
        # safely on disk at this point, so a problem with one (no sample
        # image loaded for Export Still, say) is reported alongside the
        # main success message rather than raised as its own blocking
        # error.
        extra_lines = []

        if self.export_still_checkbox.isChecked():
            extra_lines.append(self._export_still_alongside(out_path))

        if self.export_add_to_library_checkbox.isChecked():
            self.lut_library_panel.import_lut_object(
                Path(out_path).stem, lut_to_export,
                source_filename=Path(out_path).name, source_path=out_path)
            extra_lines.append("Added to the LUT Library.")

        if self.export_save_memory_checkbox.isChecked():
            self.stack_memories_panel.save_memory(
                Path(out_path).stem, self.stack_panel.get_stack())
            extra_lines.append(f'Grabbed still "{Path(out_path).stem}".')

        message = f"Saved to:\n{out_path}"
        if extra_lines:
            message += "\n\n" + "\n".join(extra_lines)
        QMessageBox.information(self, "Exported", message)
        if getattr(self, "_export_dialog", None) is not None:
            self._export_dialog.close()

    def _export_still_alongside(self, lut_out_path):
        """Export Still checkbox (per Tom) -- writes a full-resolution
        graded still next to the LUT file just exported, same basename
        with a .png extension (e.g. "shot.cube" -> "shot.png") so the two
        are obviously paired in a file listing, no second file-save
        dialog needed mid-Export. Returns a one-or-two-line status string
        for export_current_lut's summary message rather than its own
        QMessageBox -- a problem here (no sample image loaded, a decode/
        write failure) shouldn't look like the LUT export itself failed,
        since that part already succeeded.

        Reuses SampleImagePanel.render_full_resolution_after with the
        SAME stack export_current_lut already resolved/baked from (via
        self.stack_panel.get_stack(), not a re-bake) -- exact stack
        opacity/enabled state, matching what render_full_resolution_after
        already promises for export_still() itself.

        export_still_to_library_checkbox (per Tom, a SECONDARY checkbox --
        see _show_export_dialog/_on_export_still_toggled, only reachable
        at all when this one's checked) additionally hands the just-
        written still's PATH to MediaPanel.import_from_path -- matches how
        every other Media Libraries import already works (see
        media_library.py's docstring on why entries there are path-
        referenced, never embedded, unlike the LUT Library's
        import_lut_object above)."""
        if not self.sample_image.has_sample_image():
            return "Export Still skipped: no source image loaded."
        still_path = str(Path(lut_out_path).with_suffix(".png"))
        try:
            result = self.sample_image.render_full_resolution_after(
                self.stack_panel.get_stack())
            export_image_array_to_file(result, still_path)
        except Exception as e:  # noqa: BLE001 -- report, don't block the rest of Export
            return f"Export Still failed: {e}"

        lines = [f"Exported a still to:\n{still_path}"]
        if self.export_still_to_library_checkbox.isChecked():
            self.media_panel.import_from_path(still_path)
            lines.append("Imported the still into the Media Libraries.")
        return "\n".join(lines)
