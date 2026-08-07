"""
sample_image_panel.py -- import a sample image and preview it before/after
the current Stack (see stack_panel.py) is applied, via an interactive wipe
comparison (wipe_compare_widget.py). Also lets you export that graded
result as a real still image file (Export Still), independent of LUT
export (see main_window.py's Export dialog) -- though that dialog's own
"Export Still" checkbox (per Tom) now reuses this panel's
render_full_resolution_after/has_sample_image directly rather than
duplicating the reload/adapt/apply sequence, see those methods' docstrings
below.

The actual image loading and file export live in app_logic.py
(load_image_as_array / export_image_array_to_file) and are fully tested
there without Qt; the actual stack application is
stack.apply_stack_to_image_array, fully tested in test_stack.py. This
file is just the widget: QFileDialog for import/export, and feeding the
result into WipeCompareWidget.

PREVIEW PIXMAP PATH (2026-07-25, see CLEANUP_AND_PERF_OBSERVATIONS_
2026-07-25.md) -- _array_to_pixmap below builds each preview QPixmap
directly from the numpy array via QImage (Format_RGB888), NOT via
app_logic.array_to_png_bytes + QPixmap.loadFromData() like it used to.
That PNG round trip (a real zlib compress, then an immediate decompress,
purely to move pixels already sitting in memory into a QPixmap) ran on
every single stack edit including live slider drags -- real, avoidable
CPU work on the one path where preview latency actually matters.
array_to_png_bytes itself is untouched and still exactly right for its
other job (Export Still writes a real file to disk); only this panel's
in-memory preview hand-off changed.

Used to have a "Current LUT" vs "Concatenate Chain" mode toggle here --
now that the Stack (stack_panel.py) is the single source of truth for
every operation in the app (what used to be "the current LUT," Concatenate,
Retarget, Printer Lights, and ACES DRT all collapsed into one reorderable
stack), there's only one thing to preview: apply_stack_to_image_array
applies every enabled stage directly to the actual image pixels (no
intermediate grid baking -- exact, not an approximation of what the Stack
would eventually bake), so this reflects the stack -- including each
stage's opacity/enabled state -- as currently built, live, without needing
to commit anything first.

On-screen preview processing happens on a downsampled copy (see app_logic's
max_dimension) purely for responsiveness -- Export Still deliberately does
NOT reuse that downsampled array, it reloads the original file at full
resolution and grades THAT, so what gets saved is an actual deliverable,
not a 480px-wide preview thumbnail.

COLOR ADAPTATION (non-destructive): imported/default images carry two
separate arrays -- `_original_array` (exactly what was decoded off disk,
[0,1] raw code values, never touched again after load) and `_image_array`
(the "working" array everything else in this file actually uses -- before/
after preview, scopes, Export Still). `_image_array` is always DERIVED
from `_original_array` via _recompute_working_array()/
_apply_adaptation_to_array(), never mutated in place, so toggling Unmanaged
back on or picking a different From/To always starts fresh from the exact
original pixels -- no compounding rounding error across repeated
conversions, same non-destructive principle the Stack itself follows.
Unmanaged (the default for anything loaded via "Load Sample Image...", per
Tom) means _apply_adaptation_to_array is a no-op -- the working array IS
the original array. "Adapt" (the dialog opened via the "Adapt Sample
Image..." menu entry) uses colorspace.convert (colour.RGB_to_RGB under
the hood, same machinery the Stack's per-LUT-stage Retarget option
already uses) to reinterpret those code values as a declared source space
and convert them into a different scene-referred space before anything
downstream (preview, scopes, Export Still) ever sees them. The bundled
default startup image (see main_window.py) is loaded via
load_default_image with its declared source pre-filled (ACEScct) but
adaptation still starts OFF/Unmanaged, matching that same default -- it
just saves a step if you turn Adapt on.

HEADER UI (per Tom): a single "..." menu button holds "Load Sample
Image.../Adapt Sample Image.../Export Still.../Load Default Sample"
instead of four separate buttons -- same toolbar-space-saving direction
as the Stack panel's Grid Size/Domain popup. "Load Default Sample"
(restore_default_image) replaced the old destructive Clear -- there's
always a sensible image to fall back to instead of an empty panel, so
"clear to nothing" wasn't pulling its weight as its own action. The file
path itself is intentionally never displayed (also per Tom) -- load
failures surface via a QMessageBox instead of a persistent label; the one
thing still shown inline (status_label, non-modal, since it can fire on
every live edit) is a stack-apply failure message, which isn't about the
path at all.

Adapt's From/To pickers are built from colorspace.list_colourspace_choices,
not the bare colour.RGB_COLOURSPACES names -- see that function's
docstring for why (most camera-native "gamut" names have a specific log
curve baked in as their default cctf, but the bare name alone doesn't say
so; the picker labels gamut+curve together, e.g. "ARRI Wide Gamut 3 --
LogC3", while still resolving back to the exact colourspace object either
way, so both the primaries AND the log transform always run together).

PySide6 + stub libEGL.so.1 are installed now (see browser_panel.py's
docstring) -- menu_button no longer builds its own header row (see
_build_layout below); MainWindow now places it as PanelFrame's
corner_widget on the "Viewer" title row instead, confirmed headless by
walking the constructed widget tree. Actual pixel rendering/import/export
below is still unverified in this offscreen environment. Please run
main_window.py, import a real image, build a
stack with a couple of stages, confirm the preview updates live as you
add/reorder/toggle/adjust opacity on stages, then try Export Still and
confirm the saved file is full resolution and matches what the "after"
side of the wipe compare showed. Also confirm: the app launches with the
bundled default reference image already loaded and Unmanaged; the "..."
menu's Load Sample Image/Adapt Sample Image/Export Still/Load Default
Sample all work; Load Default Sample reloads the bundled image after
you've imported something else; opening Adapt Sample Image...,
checking the box, and picking From/To visibly changes the "before" side
(and therefore "after" too) with the label showing gamut+curve together;
unchecking Adapt reverts exactly to the original look with no drift;
Export Still reflects whatever adaptation is currently active; no file
path is ever shown anywhere in this panel.
"""
from pathlib import Path

import numpy as np
from PySide6.QtCore import Signal, QTimer
from PySide6.QtGui import QPixmap, QImage, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFileDialog,
    QMessageBox, QDialog, QComboBox, QCheckBox,
    QToolButton, QMenu,
)

from wipe_compare_widget import WipeCompareWidget
from app_logic import (
    load_image_as_array, export_image_array_to_file,
    default_sample_image_path, DEFAULT_SAMPLE_IMAGE_SOURCE_COLOURSPACE,
    get_image_native_size, resample_image_array,
)
from colorspace import convert as convert_colourspace, list_colourspace_choices
from stack import apply_stack_to_image_array, StageCache
from floating_dialog import make_floating_tool_window, show_floating

PREVIEW_MAX_DIMENSION = 480
# Hard ceiling on how much resolution a panel resize can ask for, regardless
# of how large the window gets -- keeps a maximized/ultrawide window from
# turning every stack edit into a multi-megapixel reprocess.
PREVIEW_MAX_DIMENSION_CEILING = 4096
# How long to wait after the panel stops resizing before reloading the
# source at the new target resolution. Splitter drags fire dozens of resize
# events a second; reloading+regrading on every one of them would make
# dragging feel sluggish, so this coalesces them into one reload after the
# user settles.
RESIZE_RELOAD_DEBOUNCE_MS = 150

# The three things either side of the wipe (or the single view, when the
# wipe's disabled -- see _render_wipe, which reuses the Right selection as
# the single-view source rather than tracking a fourth independent state)
# can show -- see this module's docstring on Sample vs Reference. "Sample
# Before"/"Sample After" are the pre-/post-stack states of the one Sample
# image this panel always had; "Reference" is the new second image the
# Stack never touches. Left/Right are picked from "Left"/"Right" tree
# submenus in the "..." menu (per Tom -- see populate_menu/
# _build_wipe_source_submenu), each built as one checkable QAction per
# entry in this list, so the actual dispatch logic (_pixmap_for_source)
# only ever has to compare against the stable key, never the display
# label.
WIPE_SOURCE_SAMPLE_BEFORE = "sample_before"
WIPE_SOURCE_SAMPLE_AFTER = "sample_after"
WIPE_SOURCE_REFERENCE = "reference"
WIPE_SOURCE_CHOICES = [
    ("Sample Before", WIPE_SOURCE_SAMPLE_BEFORE),
    ("Sample After", WIPE_SOURCE_SAMPLE_AFTER),
    ("Reference", WIPE_SOURCE_REFERENCE),
]


class SampleImagePanel(QWidget):
    """Emits after_image_changed(array_or_None) every time the previewed
    "after" result is (re)computed -- ScopesPanel subscribes to this so its
    histogram/waveform/vectorscope always reflect whatever's currently
    shown in the wipe compare, without duplicating any of the stack
    resolution logic that already lives here."""

    after_image_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_path = None
        self._original_array = None  # (H,W,3) float32, exactly as decoded -- never mutated after load
        self._image_array = None  # (H,W,3) float32, DERIVED working array -- see this module's docstring
        # Bumped only in _recompute_working_array, i.e. only when
        # _image_array is actually re-derived from a (possibly newly
        # loaded/resolution-upgraded) _original_array -- NOT on every
        # Stack edit. Passed to apply_stack_to_image_array as _preview_
        # cache's input_key, so a Stack edit reuses every already-computed
        # stage against this same working array instead of re-running the
        # whole stack on every single opacity/param tweak (see
        # StageCache's own docstring in stack.py).
        self._image_array_generation = 0
        self._preview_cache = StageCache()
        self._source_colourspace = None  # declared space of _original_array's code values, or None
        self._adapt_enabled = False  # False = Unmanaged (_image_array IS _original_array)
        self._adapt_target_colourspace = None  # space to convert into when _adapt_enabled
        self._stack_getter = None  # zero-arg callable -> the live stack.Stack
        self._current_after_array = None  # last successfully rendered "after" result, or None
        self._current_preview_dimension = PREVIEW_MAX_DIMENSION  # max_dimension actually used for _original_array
        self._native_size = None  # (w, h) of the source file on disk, or None

        # Reference image state -- deliberately a full parallel set of
        # fields, not a shared/parameterized structure with the Sample
        # fields above. See this module's docstring: Reference is the
        # Stack-never-touches-it second image, with its own independent
        # Adapt state (own dialog, own checkbox/From/To, defaults
        # Unmanaged same as Sample's does) and its own preview-resolution
        # tracking, so it can be loaded/adapted/resized without ever
        # affecting Sample.
        self._reference_current_path = None
        self._reference_original_array = None
        self._reference_image_array = None
        self._reference_source_colourspace = None
        self._reference_adapt_enabled = False
        self._reference_adapt_target_colourspace = None
        self._reference_current_preview_dimension = PREVIEW_MAX_DIMENSION
        self._reference_native_size = None

        # Wipe state -- moved out of on-panel widgets into the "..." menu
        # (per Tom, see populate_menu/_build_wipe_source_submenu). Plain
        # attributes now instead of combo boxes/a checkbox, since
        # populate_menu is called once per menu instance (the "..." corner
        # button's menu AND MainWindow's top-level "Viewer" menu-bar menu
        # -- see this app's "redundant, always-in-sync" menu convention),
        # so each call builds its OWN QActions reading/writing these same
        # attributes rather than one combo box only one of the two menus
        # could show. The three action lists below let a change made from
        # either menu instance push its new checked-state into every other
        # instance too (see _on_wipe_enabled_toggled/_on_wipe_left_selected/
        # _on_wipe_right_selected).
        self._wipe_enabled = True
        self._wipe_left_key = WIPE_SOURCE_SAMPLE_BEFORE
        self._wipe_right_key = WIPE_SOURCE_SAMPLE_AFTER
        self._wipe_enabled_actions = []  # every "Enable Wipe" QAction built so far
        self._wipe_left_actions = []  # [(QAction, key), ...] across every "Left" submenu built so far
        self._wipe_right_actions = []  # ditto for every "Right" submenu

        self._build_layout()
        self._build_adapt_dialog()
        self._build_reference_adapt_dialog()

        # See _on_compare_resized/_upgrade_preview_resolution_if_needed --
        # the panel starts at PREVIEW_MAX_DIMENSION (480px) purely for
        # responsiveness (see this module's docstring), but that's a fixed
        # cap: if the Viewer panel is bigger than that, drawPixmap has no
        # choice but to stretch the 480px image up to fill it, which is the
        # "downscale once then upscale separately" blur Tom flagged. This
        # timer coalesces resize events and reloads the source at a bigger
        # max_dimension (never smaller -- see the grow-only check) once the
        # panel settles at a size that actually needs more detail than
        # what's currently loaded.
        self._resize_reload_timer = QTimer(self)
        self._resize_reload_timer.setSingleShot(True)
        self._resize_reload_timer.timeout.connect(self._upgrade_preview_resolution_if_needed)
        self.compare.resized.connect(
            lambda: self._resize_reload_timer.start(RESIZE_RELOAD_DEBOUNCE_MS))

    def _build_layout(self):
        layout = QVBoxLayout(self)

        # Not added to this panel's own layout -- MainWindow hands this to
        # PanelFrame as `corner_widget` so it sits on the SAME row as the
        # "Viewer" title (see panel_frame.py) instead of its own header row
        # underneath it. Built here (not lazily) since MainWindow needs it
        # at construction time, right after building this panel.
        self.menu_button = QToolButton()
        self.menu_button.setObjectName("panelMenuButton")
        self.menu_button.setAutoRaise(True)
        self.menu_button.setText("☰")  # hamburger icon, per Tom (was "...")
        self.menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.menu_button)
        self.populate_menu(menu)
        self.menu_button.setMenu(menu)

        # No path label -- the file path is intentionally never displayed
        # here (per Tom); status_label below is only for a live stack-apply
        # failure message, unrelated to the loaded path.
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # No on-panel wipe controls anymore -- Enable Wipe and the Left/
        # Right source pickers all moved into the "..." menu (per Tom, see
        # populate_menu/_build_wipe_source_submenu), same "toolbar-space-
        # saving" direction as the header "..." menu itself already
        # follows for Load Sample Image/Adapt Sample Image/Export Still/
        # Load Default Sample above.
        #
        # No color_readout_label / wipe-usage hint row anymore (per Tom) --
        # the "Color: ..." note is now an overlay chip painted directly on
        # the image itself (see _update_color_readout below and
        # WipeCompareWidget.set_color_readout_text), and the "drag the
        # handle..." hint line is just gone, not moved.
        self.compare = WipeCompareWidget()
        layout.addWidget(self.compare, stretch=1)

    def populate_menu(self, menu):
        """Add this panel's actions to `menu` -- shared by the "..." corner
        button above and MainWindow's top-level "Viewer" menu-bar menu
        (per Tom, making the "..." menu redundant rather than the only way
        in). Called once per menu instance (twice total, at startup) --
        for the plain (non-checkable) actions that's harmless, since both
        copies just call the same method. The wipe controls below are
        checkable/exclusive though, so this method also registers each new
        QAction it creates into self._wipe_enabled_actions/
        _wipe_left_actions/_wipe_right_actions -- see _on_wipe_enabled_toggled/
        _on_wipe_left_selected/_on_wipe_right_selected for how a change
        made from either menu's copy gets mirrored into the other's."""
        menu.addAction("Load Sample Image...", self.import_image)
        menu.addAction("Adapt Sample Image...", self.show_adapt_dialog)
        menu.addAction("Export Still...", self.export_still)
        menu.addSeparator()
        # Replaces the old destructive Clear -- see this module's docstring.
        menu.addAction("Load Default Sample", self.restore_default_image)
        menu.addSeparator()
        # Reference -- see this module's docstring. No "Default"/Export
        # equivalent for Reference (there's no bundled default, and it's
        # never stack-graded content to export).
        menu.addAction("Load Reference Image...", self.import_reference_image)
        menu.addAction("Adapt Reference...", self.show_reference_adapt_dialog)
        menu.addSeparator()

        # Wipe controls (per Tom, moved out of the on-panel row into here).
        wipe_enabled_action = menu.addAction("Enable Wipe")
        wipe_enabled_action.setShortcut(QKeySequence("Ctrl+W"))
        wipe_enabled_action.setCheckable(True)
        wipe_enabled_action.setChecked(self._wipe_enabled)
        wipe_enabled_action.toggled.connect(self._on_wipe_enabled_toggled)
        self._wipe_enabled_actions.append(wipe_enabled_action)

        # "Left"/"Right" tree (sub)menus, one checkable entry per
        # WIPE_SOURCE_CHOICES option, radio-exclusive within each submenu
        # via QActionGroup. Right also doubles as the single-image source
        # when Enable Wipe is off -- see _render_wipe -- so there's no
        # separate "Show" menu to keep in sync with it.
        left_menu = menu.addMenu("Left Image")
        self._build_wipe_source_submenu(
            left_menu, self._wipe_left_actions, self._wipe_left_key,
            self._on_wipe_left_selected)
        right_menu = menu.addMenu("Right Image")
        self._build_wipe_source_submenu(
            right_menu, self._wipe_right_actions, self._wipe_right_key,
            self._on_wipe_right_selected)

    def _build_wipe_source_submenu(self, submenu, actions_list, current_key, on_selected):
        """Populate one "Left"/"Right" tree menu with a checkable, mutually
        exclusive (QActionGroup) entry per WIPE_SOURCE_CHOICES option, and
        record each new (action, key) pair into `actions_list` so a
        selection made here can be mirrored into every other menu
        instance's matching submenu (see _on_wipe_left_selected/
        _on_wipe_right_selected)."""
        group = QActionGroup(submenu)
        group.setExclusive(True)
        for label, key in WIPE_SOURCE_CHOICES:
            action = submenu.addAction(label)
            action.setCheckable(True)
            action.setChecked(key == current_key)
            group.addAction(action)
            action.triggered.connect(lambda _checked, k=key: on_selected(k))
            actions_list.append((action, key))

    def _build_adapt_dialog(self):
        dialog = QDialog(self)
        make_floating_tool_window(dialog)
        dialog.setWindowTitle("Adapt Sample Image")
        outer = QVBoxLayout(dialog)

        self.adapt_checkbox = QCheckBox("Adapt color space (unchecked = Unmanaged)")
        self.adapt_checkbox.toggled.connect(self._on_adapt_checkbox_toggled)
        outer.addWidget(self.adapt_checkbox)

        # Labeled by gamut+EOTF/log curve together (e.g. "ARRI Wide Gamut 3
        # -- LogC3"), not the bare gamut-only name -- see
        # colorspace.list_colourspace_choices' docstring. userData on each
        # item is the real colour-science name convert()/lookup_colourspace
        # expect; read it back via .currentData(), not .currentText().
        choices = list_colourspace_choices()
        from_row = QHBoxLayout()
        from_row.addWidget(QLabel("From:"))
        self.adapt_from_combo = QComboBox()
        for label, name in choices:
            self.adapt_from_combo.addItem(label, name)
        self.adapt_from_combo.currentIndexChanged.connect(self._on_adapt_combo_changed)
        from_row.addWidget(self.adapt_from_combo, stretch=1)
        outer.addLayout(from_row)

        to_row = QHBoxLayout()
        to_row.addWidget(QLabel("To:"))
        self.adapt_to_combo = QComboBox()
        for label, name in choices:
            self.adapt_to_combo.addItem(label, name)
        self.adapt_to_combo.currentIndexChanged.connect(self._on_adapt_combo_changed)
        to_row.addWidget(self.adapt_to_combo, stretch=1)
        outer.addLayout(to_row)

        self.adapt_from_combo.setEnabled(False)
        self.adapt_to_combo.setEnabled(False)

        # Live: checking the box or changing either combo applies
        # immediately (see _apply_adapt_state_live) -- nothing left to
        # commit, so this is just an acknowledge-and-close button, not a
        # separate confirm step.
        ok_button = QPushButton("OK")
        ok_button.clicked.connect(dialog.close)
        outer.addWidget(ok_button)

        self._adapt_dialog = dialog

    def _build_reference_adapt_dialog(self):
        """Mirrors _build_adapt_dialog exactly, for the Reference slot --
        see this module's docstring on why Reference gets its own fully
        independent Adapt dialog/state rather than sharing Sample's."""
        dialog = QDialog(self)
        make_floating_tool_window(dialog)
        dialog.setWindowTitle("Adapt Reference Image")
        outer = QVBoxLayout(dialog)

        self.ref_adapt_checkbox = QCheckBox("Adapt color space (unchecked = Unmanaged)")
        self.ref_adapt_checkbox.toggled.connect(self._on_ref_adapt_checkbox_toggled)
        outer.addWidget(self.ref_adapt_checkbox)

        choices = list_colourspace_choices()
        from_row = QHBoxLayout()
        from_row.addWidget(QLabel("From:"))
        self.ref_adapt_from_combo = QComboBox()
        for label, name in choices:
            self.ref_adapt_from_combo.addItem(label, name)
        self.ref_adapt_from_combo.currentIndexChanged.connect(self._on_ref_adapt_combo_changed)
        from_row.addWidget(self.ref_adapt_from_combo, stretch=1)
        outer.addLayout(from_row)

        to_row = QHBoxLayout()
        to_row.addWidget(QLabel("To:"))
        self.ref_adapt_to_combo = QComboBox()
        for label, name in choices:
            self.ref_adapt_to_combo.addItem(label, name)
        self.ref_adapt_to_combo.currentIndexChanged.connect(self._on_ref_adapt_combo_changed)
        to_row.addWidget(self.ref_adapt_to_combo, stretch=1)
        outer.addLayout(to_row)

        self.ref_adapt_from_combo.setEnabled(False)
        self.ref_adapt_to_combo.setEnabled(False)

        ok_button = QPushButton("OK")
        ok_button.clicked.connect(dialog.close)
        outer.addWidget(ok_button)

        self._reference_adapt_dialog = dialog

    def _on_adapt_checkbox_toggled(self, checked):
        self.adapt_from_combo.setEnabled(checked)
        self.adapt_to_combo.setEnabled(checked)
        self._apply_adapt_state_live()

    def _on_ref_adapt_checkbox_toggled(self, checked):
        self.ref_adapt_from_combo.setEnabled(checked)
        self.ref_adapt_to_combo.setEnabled(checked)
        self._apply_ref_adapt_state_live()

    def _on_ref_adapt_combo_changed(self, _index):
        self._apply_ref_adapt_state_live()

    def show_reference_adapt_dialog(self):
        if self._reference_original_array is None:
            QMessageBox.information(self, "No reference image", "Load a reference image first.")
            return
        self.ref_adapt_checkbox.blockSignals(True)
        self.ref_adapt_checkbox.setChecked(self._reference_adapt_enabled)
        self.ref_adapt_checkbox.blockSignals(False)
        self.ref_adapt_from_combo.setEnabled(self._reference_adapt_enabled)
        self.ref_adapt_to_combo.setEnabled(self._reference_adapt_enabled)
        if self._reference_source_colourspace:
            idx = self.ref_adapt_from_combo.findData(self._reference_source_colourspace)
            if idx >= 0:
                self.ref_adapt_from_combo.blockSignals(True)
                self.ref_adapt_from_combo.setCurrentIndex(idx)
                self.ref_adapt_from_combo.blockSignals(False)
        if self._reference_adapt_target_colourspace:
            idx = self.ref_adapt_to_combo.findData(self._reference_adapt_target_colourspace)
            if idx >= 0:
                self.ref_adapt_to_combo.blockSignals(True)
                self.ref_adapt_to_combo.setCurrentIndex(idx)
                self.ref_adapt_to_combo.blockSignals(False)
        show_floating(self._reference_adapt_dialog)

    def _apply_ref_adapt_state_live(self):
        self._reference_adapt_enabled = self.ref_adapt_checkbox.isChecked()
        self._reference_source_colourspace = self.ref_adapt_from_combo.currentData()
        self._reference_adapt_target_colourspace = self.ref_adapt_to_combo.currentData()
        try:
            self._recompute_reference_working_array()
        except Exception as e:  # noqa: BLE001 -- surface any conversion failure to the user
            QMessageBox.critical(self._reference_adapt_dialog, "Adapt failed", str(e))
            return
        self._render_wipe()

    def _on_adapt_combo_changed(self, _index):
        # Only meaningful while Adapt is checked (Unmanaged ignores
        # From/To entirely), but harmless either way -- always remember
        # whichever spaces are selected, see _apply_adapt_state_live.
        self._apply_adapt_state_live()

    def show_adapt_dialog(self):
        if self._original_array is None:
            QMessageBox.information(self, "No image", "Import a sample image first.")
            return
        # Blocked while pre-filling to whatever state is already active --
        # this is just syncing the dialog's controls to reality, not a new
        # user-driven change, so it shouldn't itself trigger another live
        # recompute (harmless if it did -- same state in, same state out --
        # but pointless work, and _on_adapt_checkbox_toggled would also
        # re-enable/disable the combos redundantly).
        self.adapt_checkbox.blockSignals(True)
        self.adapt_checkbox.setChecked(self._adapt_enabled)
        self.adapt_checkbox.blockSignals(False)
        self.adapt_from_combo.setEnabled(self._adapt_enabled)
        self.adapt_to_combo.setEnabled(self._adapt_enabled)
        if self._source_colourspace:
            idx = self.adapt_from_combo.findData(self._source_colourspace)
            if idx >= 0:
                self.adapt_from_combo.blockSignals(True)
                self.adapt_from_combo.setCurrentIndex(idx)
                self.adapt_from_combo.blockSignals(False)
        if self._adapt_target_colourspace:
            idx = self.adapt_to_combo.findData(self._adapt_target_colourspace)
            if idx >= 0:
                self.adapt_to_combo.blockSignals(True)
                self.adapt_to_combo.setCurrentIndex(idx)
                self.adapt_to_combo.blockSignals(False)
        show_floating(self._adapt_dialog)

    def _apply_adapt_state_live(self):
        """Checking/unchecking Adapt, or changing either combo while it's
        checked, applies immediately -- no separate commit step (per Tom:
        "the image can change as soon as" either happens). Always remembers
        whichever From/To are currently picked, even while Unmanaged, so
        toggling Adapt back on later doesn't lose the last selection."""
        self._adapt_enabled = self.adapt_checkbox.isChecked()
        self._source_colourspace = self.adapt_from_combo.currentData()
        self._adapt_target_colourspace = self.adapt_to_combo.currentData()
        try:
            self._recompute_working_array()
        except Exception as e:  # noqa: BLE001 -- surface any conversion failure to the user
            QMessageBox.critical(self._adapt_dialog, "Adapt failed", str(e))
            return
        self._recompute_sample_after()
        self._render_wipe()

    @staticmethod
    def _convert_if_enabled(array, adapt_enabled, source_colourspace, target_colourspace):
        """The actual conversion math behind both _apply_adaptation_to_array
        (Sample) and _recompute_reference_working_array (Reference) --
        shared so the two independent Adapt states can never drift into
        applying the conversion differently, even though which state's
        values get passed in is otherwise completely separate."""
        if not adapt_enabled or not source_colourspace or not target_colourspace:
            return array
        converted = convert_colourspace(array, source_colourspace, target_colourspace)
        return np.asarray(converted, dtype=np.float32)

    def _apply_adaptation_to_array(self, array):
        """Shared by _recompute_working_array (preview resolution) and
        export_still (full resolution) so both ever apply the exact same
        adaptation -- never mutates `array` itself, always converts fresh
        from whatever's handed in."""
        return self._convert_if_enabled(
            array, self._adapt_enabled, self._source_colourspace, self._adapt_target_colourspace)

    def _recompute_working_array(self):
        if self._original_array is None:
            self._image_array = None
            return
        self._image_array = self._apply_adaptation_to_array(self._original_array)
        self._image_array_generation += 1

    def _recompute_reference_working_array(self):
        if self._reference_original_array is None:
            self._reference_image_array = None
            return
        self._reference_image_array = self._convert_if_enabled(
            self._reference_original_array, self._reference_adapt_enabled,
            self._reference_source_colourspace, self._reference_adapt_target_colourspace)

    def _update_color_readout(self):
        # Overlay chip on the image itself now (see WipeCompareWidget.
        # set_color_readout_text) -- no separate QLabel/stylesheet to
        # juggle for the "adapted" vs "Unmanaged" look, the text itself
        # already says which; the overlay's own fixed light-on-dark chip
        # style stays legible over arbitrary image content either way.
        #
        # The chip describes whichever source is CURRENTLY showing in the
        # "right"/single slot (see _render_wipe -- Right doubles as the
        # single-image source when Enable Wipe is off, so this is always
        # just self._wipe_right_key regardless of wipe state) -- Sample
        # Before/After share Sample's Adapt state, Reference has its own,
        # so the chip has to ask the right one depending on what's
        # actually on screen right now rather than always assuming Sample.
        active_key = self._wipe_right_key
        if active_key == WIPE_SOURCE_REFERENCE:
            if self._reference_original_array is None:
                self.compare.set_color_readout_text("")
                return
            if self._reference_adapt_enabled and self._reference_source_colourspace and self._reference_adapt_target_colourspace:
                self.compare.set_color_readout_text(
                    f"Color: {self._reference_source_colourspace} → {self._reference_adapt_target_colourspace}")
            else:
                self.compare.set_color_readout_text("Color: Unmanaged")
            return

        if self._original_array is None:
            self.compare.set_color_readout_text("")
            return
        if self._adapt_enabled and self._source_colourspace and self._adapt_target_colourspace:
            self.compare.set_color_readout_text(
                f"Color: {self._source_colourspace} → {self._adapt_target_colourspace}")
        else:
            self.compare.set_color_readout_text("Color: Unmanaged")

    def import_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a sample image", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp);;"
            "All files (*)",
        )
        if path:
            self.load_image(path)

    def load_image(self, path):
        """Load, downsample, and preview an image, Unmanaged (no declared
        color space) -- matches "imported ref images are Unmanaged by
        default." Returns True on success. Safe to call directly without
        going through the file dialog. For a startup default image with a
        KNOWN declared color space, use load_default_image instead."""
        return self._load_image_core(path)

    def load_default_image(self, path, declared_source_colourspace=None):
        """Like load_image, but pre-fills the Adapt dialog's declared
        source color space (so opening it and checking Adapt is one less
        step) WITHOUT turning adaptation on -- still starts Unmanaged, same
        default as any other freshly loaded image, see this module's
        docstring. Used at startup and by restore_default_image() for the
        bundled default reference still."""
        ok = self._load_image_core(path)
        if ok and declared_source_colourspace:
            self._source_colourspace = declared_source_colourspace
        return ok

    def restore_default_image(self):
        """"Load Default Sample" menu action -- (re)loads the bundled
        default reference image fresh, same as what happens automatically
        on first launch. Replaces the old destructive Clear -- see this
        module's docstring."""
        path = default_sample_image_path()
        if not path.exists():
            QMessageBox.warning(
                self, "Default image missing",
                f"Couldn't find the bundled default reference image at:\n{path}")
            return
        self.load_default_image(
            str(path), declared_source_colourspace=DEFAULT_SAMPLE_IMAGE_SOURCE_COLOURSPACE)

    def _load_image_core(self, path):
        try:
            array = load_image_as_array(path, max_dimension=PREVIEW_MAX_DIMENSION)
        except Exception as e:  # noqa: BLE001 -- surface any decode failure to the user
            QMessageBox.critical(self, "Failed to load image", f"{path}\n\n{e}")
            self._current_path = None
            self._original_array = None
            self._image_array = None
            self._native_size = None
            self.status_label.setText("")
            self.compare.set_color_readout_text("")
            self.compare.clear()
            return False

        self._current_path = str(path)
        self._original_array = array
        self._current_preview_dimension = PREVIEW_MAX_DIMENSION
        try:
            self._native_size = get_image_native_size(path)
        except Exception:  # noqa: BLE001 -- resolution-tracking is best-effort, not critical
            self._native_size = None
        self._source_colourspace = None
        self._adapt_enabled = False
        self._adapt_target_colourspace = None
        self.status_label.setText("")
        self._recompute_working_array()
        self._recompute_sample_after()
        self._render_wipe()
        # In case the Viewer panel is already bigger than the 480px default
        # (e.g. the window was left maximized from last time), don't wait
        # for a resize event that may never come -- check right away.
        self._upgrade_preview_resolution_if_needed()
        return True

    def import_reference_image(self):
        """"Load Reference Image..." menu action -- see this module's
        docstring on Sample vs Reference. Unlike Sample, there's no
        bundled default and no separate load_reference_image/
        load_default_reference split -- Reference is always either
        empty, loaded from a real file here, or loaded from the Image
        Library (see load_reference_from_library_entry)."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Reference Image", "",
            "Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.exr);;All files (*)",
        )
        if path:
            self._load_reference_image_core(path)

    def _load_reference_image_core(self, path):
        """Mirrors _load_image_core, but for the Reference slot -- see this
        module's docstring on why Reference has its own fully independent
        state (own array, own Adapt dialog/state, own preview-resolution
        tracking) rather than reusing Sample's. Reference always starts
        Unmanaged on a fresh load (per Tom), same default Sample gets,
        even though the two Adapt states are otherwise unrelated."""
        try:
            array = load_image_as_array(path, max_dimension=PREVIEW_MAX_DIMENSION)
        except Exception as e:  # noqa: BLE001 -- surface any decode failure to the user
            QMessageBox.critical(self, "Failed to load reference image", f"{path}\n\n{e}")
            return False

        self._reference_current_path = str(path)
        self._reference_original_array = array
        self._reference_current_preview_dimension = PREVIEW_MAX_DIMENSION
        try:
            self._reference_native_size = get_image_native_size(path)
        except Exception:  # noqa: BLE001 -- resolution-tracking is best-effort, not critical
            self._reference_native_size = None
        self._reference_source_colourspace = None
        self._reference_adapt_enabled = False
        self._reference_adapt_target_colourspace = None
        self._recompute_reference_working_array()
        self._render_wipe()
        self._upgrade_preview_resolution_if_needed()
        return True

    def load_sample_from_library_entry(self, entry):
        """Wired to ImageLibraryPanel.load_as_sample_requested. Unlike a
        normal file-dialog Import, the full-resolution array is already
        decoded (see image_library.py) -- this just downsamples it to the
        current preview resolution via app_logic.resample_image_array
        instead of re-reading anything from disk. Pre-fills the declared
        source color space from the entry if it had one (same "save a
        step" convenience load_default_image gives Sample's bundled
        default), but Adapt itself still starts off, same as any other
        fresh load."""
        self._current_path = entry.source_path
        self._original_array = resample_image_array(entry.array, PREVIEW_MAX_DIMENSION)
        self._current_preview_dimension = PREVIEW_MAX_DIMENSION
        h, w = entry.array.shape[:2]
        self._native_size = (w, h)
        self._source_colourspace = entry.source_colourspace
        self._adapt_enabled = False
        self._adapt_target_colourspace = None
        self.status_label.setText("")
        self._recompute_working_array()
        self._recompute_sample_after()
        self._render_wipe()
        self._upgrade_preview_resolution_if_needed()

    def load_reference_from_library_entry(self, entry):
        """Wired to ImageLibraryPanel.load_as_reference_requested -- see
        load_sample_from_library_entry, identical idea for the Reference
        slot."""
        self._reference_current_path = entry.source_path
        self._reference_original_array = resample_image_array(entry.array, PREVIEW_MAX_DIMENSION)
        self._reference_current_preview_dimension = PREVIEW_MAX_DIMENSION
        h, w = entry.array.shape[:2]
        self._reference_native_size = (w, h)
        self._reference_source_colourspace = entry.source_colourspace
        self._reference_adapt_enabled = False
        self._reference_adapt_target_colourspace = None
        self._recompute_reference_working_array()
        self._render_wipe()
        self._upgrade_preview_resolution_if_needed()

    def _desired_preview_dimension_for(self, native_size):
        """How many pixels (on the longer side) the current panel size
        actually calls for, so drawPixmap in WipeCompareWidget is scaling
        DOWN from a higher-res source instead of stretching UP from a
        fixed-size one -- floored at the responsiveness-driven default,
        capped at both a hard ceiling and the source's own native
        resolution (no point loading more detail than the file has).
        Shared by Sample and Reference (each passes its own native_size)
        since the panel-size half of this calculation is identical for
        both -- they share one WipeCompareWidget."""
        w, h = self.compare.width(), self.compare.height()
        if w <= 0 or h <= 0:
            return PREVIEW_MAX_DIMENSION
        dpr = self.compare.devicePixelRatioF()
        target = int(round(max(w, h) * dpr))
        target = max(target, PREVIEW_MAX_DIMENSION)
        target = min(target, PREVIEW_MAX_DIMENSION_CEILING)
        if native_size is not None:
            target = min(target, max(native_size))
        return target

    def _upgrade_preview_resolution_if_needed(self):
        """Grow-only, for BOTH Sample and Reference -- see
        _upgrade_sample_resolution_if_needed/
        _upgrade_reference_resolution_if_needed. Renders once at the end
        regardless of which (if either) actually upgraded, same as before
        this panel had two independent images to track."""
        self._upgrade_sample_resolution_if_needed()
        self._upgrade_reference_resolution_if_needed()
        self._render_wipe()

    def _upgrade_sample_resolution_if_needed(self):
        if self._current_path is None:
            return
        desired = self._desired_preview_dimension_for(self._native_size)
        if desired <= self._current_preview_dimension:
            return
        try:
            array = load_image_as_array(self._current_path, max_dimension=desired)
        except Exception:
            # Best-effort background quality upgrade -- if it fails, keep
            # showing whatever's already loaded rather than interrupting
            # with a dialog for something the user didn't directly ask for.
            return
        self._original_array = array
        self._current_preview_dimension = desired
        self._recompute_working_array()
        self._recompute_sample_after()

    def _upgrade_reference_resolution_if_needed(self):
        if self._reference_current_path is None:
            return
        desired = self._desired_preview_dimension_for(self._reference_native_size)
        if desired <= self._reference_current_preview_dimension:
            return
        try:
            array = load_image_as_array(self._reference_current_path, max_dimension=desired)
        except Exception:
            return
        self._reference_original_array = array
        self._reference_current_preview_dimension = desired
        self._recompute_reference_working_array()

    def set_stack_getter(self, getter):
        """getter: a zero-arg callable returning StackPanel's live
        stack.Stack object. A callback rather than a stored reference so
        preview always reflects the stack (stages, order, opacity,
        enabled state, everything) exactly as it stands right now."""
        self._stack_getter = getter

    def refresh_preview(self):
        """Public trigger for 'something this panel doesn't own directly
        changed, re-render' -- used for StackPanel.stack_changed, since
        that fires on every stack mutation and this panel has no other
        way to know. Recomputes Sample After specifically (the only one
        of the three wipe sources that depends on the Stack) and always
        re-renders the wipe regardless of which source(s) are currently
        selected -- if Sample After isn't currently showing on either
        side, this is a cheap no-op render, not a wrong one."""
        self._recompute_sample_after()
        self._render_wipe()

    def _recompute_sample_after(self):
        """Computes (or clears) Sample's post-stack result and notifies
        anything listening (ScopesPanel via after_image_changed) --
        deliberately does NOT touch the wipe display itself (see
        _render_wipe, which reads self._current_after_array back out
        whenever "Sample After" is one of the currently-selected
        sources). Split out from the old _render_after specifically so
        Scopes can keep tracking "the graded result" even when the wipe
        itself is currently showing Reference on both sides."""
        if self._image_array is None:
            self._set_current_after_array(None)
            return
        stack = self._stack_getter() if self._stack_getter else None
        if stack is None or not stack.stages:
            self._set_current_after_array(None)
            return
        try:
            result = apply_stack_to_image_array(
                stack, self._image_array,
                cache=self._preview_cache, input_key=self._image_array_generation)
        except Exception as e:  # noqa: BLE001 -- surface any apply failure to the user
            self.status_label.setText(f"Failed to apply stack: {e}")
            self._set_current_after_array(None)
            return
        self.status_label.setText("")
        self._set_current_after_array(result)

    def _pixmap_for_source(self, key):
        """(QPixmap_or_None, placeholder_message) for one of
        WIPE_SOURCE_CHOICES' three keys -- shared by the per-side
        Left/Right combos (wipe on) and the single Show combo (wipe off)
        in _render_wipe, so all three ever compute a given source's image
        the exact same way."""
        if key == WIPE_SOURCE_SAMPLE_BEFORE:
            if self._image_array is None:
                return None, "Import a Sample image to preview"
            return self._array_to_pixmap(self._image_array), ""
        if key == WIPE_SOURCE_SAMPLE_AFTER:
            if self._image_array is None:
                return None, "Import a Sample image to preview"
            if self._current_after_array is None:
                return None, "Add stages to the Stack to preview"
            return self._array_to_pixmap(self._current_after_array), ""
        if key == WIPE_SOURCE_REFERENCE:
            if self._reference_image_array is None:
                return None, "Load a Reference image to preview"
            return self._array_to_pixmap(self._reference_image_array), ""
        return None, ""  # pragma: no cover -- the combos only ever offer the three keys above

    def _render_wipe(self):
        """Central place that pushes whatever's currently selected (Left/
        Right when the wipe's on, just Right when it's off -- see this
        module's docstring on why there's no separate "Show" state) into
        the WipeCompareWidget -- called after literally anything that
        could change what should be on screen: a stack edit
        (refresh_preview), either image's Adapt state changing, a new
        Sample/Reference image loading, a Left/Right menu selection
        changing, or Enable Wipe being toggled. Always recomputes both
        potentially-visible sources (even in single mode, only Right
        matters, but it's the same cheap dispatch either way) rather than
        trying to track which one specifically changed -- see
        _pixmap_for_source, which is O(1) array-to-pixmap work, not a
        re-decode."""
        wipe_enabled = self._wipe_enabled
        self.compare.set_wipe_enabled(wipe_enabled)
        if wipe_enabled:
            left_pixmap, _left_placeholder = self._pixmap_for_source(self._wipe_left_key)
            right_pixmap, right_placeholder = self._pixmap_for_source(self._wipe_right_key)
            self.compare.set_after_placeholder(right_placeholder)
            self.compare.set_before(left_pixmap)
            self.compare.set_after(right_pixmap)
        else:
            pixmap, placeholder = self._pixmap_for_source(self._wipe_right_key)
            self.compare.set_after_placeholder(placeholder)
            self.compare.set_before(None)
            self.compare.set_after(pixmap)
        self._update_color_readout()

    def _on_wipe_enabled_toggled(self, checked):
        """Wired to every "Enable Wipe" QAction built by populate_menu (one
        per menu instance) -- updates the shared state, then mirrors the
        new checked-state into every OTHER instance's action too (blocked,
        so this doesn't recurse back into this same handler), so the
        corner "..." menu and the top-level Viewer menu-bar entry never
        show a stale/contradictory checkmark for each other."""
        self._wipe_enabled = checked
        for action in self._wipe_enabled_actions:
            action.blockSignals(True)
            action.setChecked(checked)
            action.blockSignals(False)
        self._render_wipe()

    def _on_wipe_left_selected(self, key):
        """Wired to every "Left Image" submenu's QActionGroup (one per menu
        instance) -- same cross-instance sync idea as
        _on_wipe_enabled_toggled, just for a radio-style choice instead of
        a checkbox: only the action matching `key` ends up checked, in
        every submenu instance, not just the one that was clicked."""
        self._wipe_left_key = key
        for action, action_key in self._wipe_left_actions:
            action.blockSignals(True)
            action.setChecked(action_key == key)
            action.blockSignals(False)
        self._render_wipe()

    def _on_wipe_right_selected(self, key):
        """Mirrors _on_wipe_left_selected for the "Right Image" submenu."""
        self._wipe_right_key = key
        for action, action_key in self._wipe_right_actions:
            action.blockSignals(True)
            action.setChecked(action_key == key)
            action.blockSignals(False)
        self._render_wipe()

    def _set_current_after_array(self, array):
        """Single place that updates the "last after result" and notifies
        anything listening (ScopesPanel) -- so every return path in
        _recompute_sample_after (success, empty-stack, or apply failure)
        keeps scopes in sync with the current graded result, independent
        of whether "Sample After" is one of the sources actually showing
        in the wipe right now."""
        self._current_after_array = array
        self.after_image_changed.emit(array)

    def get_current_after_array(self):
        """The last successfully rendered 'after' array (post-stack, at
        preview resolution), or None if nothing's currently showing. For a
        panel that attaches after the fact (or wants a pull instead of
        subscribing to after_image_changed) to read the current state
        without waiting for the next change."""
        return self._current_after_array

    @staticmethod
    def _array_to_pixmap(image_array):
        """(H,W,3) float array in [0,1] -> QPixmap, for WipeCompareWidget.
        Direct buffer hand-off (uint8 RGB -> QImage -> QPixmap.fromImage),
        NOT a PNG encode/decode round trip -- see this module's own
        docstring, "PREVIEW PIXMAP PATH." Same rounding/clipping as
        app_logic._float_array_to_pil_image (`* 255.0 + 0.5` then clip,
        not a bare truncating cast) so this produces byte-identical pixel
        values to what array_to_png_bytes/Export Still would have shown,
        just without ever touching PNG for this in-memory-only path.

        np.ascontiguousarray is required, not just tidy -- QImage's
        Format_RGB888 constructor reads `bytes_per_line` raw bytes per
        row directly out of the buffer it's handed; a non-contiguous
        array (e.g. a transposed/sliced view elsewhere in this app) would
        silently read the wrong bytes into each row instead of raising.
        uint8_array is kept alive for this whole function via the local
        variable -- QPixmap.fromImage() converts to Qt's own device-
        dependent representation immediately, so by the time this
        function returns, the QPixmap no longer references uint8_array's
        buffer at all; nothing dangles once it falls out of scope."""
        uint8_array = np.ascontiguousarray(
            np.clip(image_array * 255.0 + 0.5, 0, 255).astype(np.uint8))
        height, width, _channels = uint8_array.shape
        bytes_per_line = 3 * width
        qimage = QImage(uint8_array.data, width, height, bytes_per_line,
                         QImage.Format.Format_RGB888)
        return QPixmap.fromImage(qimage)

    def has_sample_image(self):
        """Whether a sample image is currently loaded -- used by
        export_still() itself and by MainWindow's Export dialog "Export
        Still" checkbox (per Tom) to check the same precondition without
        reaching into self._current_path directly from outside this
        class."""
        return self._current_path is not None

    def render_full_resolution_after(self, stack):
        """Reload the sample image source at full resolution, apply the
        current adaptation, then `stack` -- the actual computation shared
        by export_still() (its own file-picker flow, below) and
        MainWindow's Export dialog "Export Still" checkbox (per Tom), so
        both produce an identically processed still without duplicating
        the reload/adapt/apply sequence. Reloads the source file at full
        resolution rather than reusing the (deliberately downsampled)
        preview array -- see this module's docstring. Raises whatever
        load_image_as_array/apply_stack_to_image_array itself raises on
        failure -- callers surface that however fits their own UI.
        Caller is responsible for the "a sample image is loaded"
        precondition (see has_sample_image) -- this assumes
        self._current_path is already set."""
        full_res = load_image_as_array(self._current_path, max_dimension=None)
        # Same adaptation currently active in the preview (Unmanaged or
        # From->To) -- reapplied fresh to the full-res reload, not
        # derived from the (deliberately downsampled) preview array.
        full_res = self._apply_adaptation_to_array(full_res)
        return apply_stack_to_image_array(stack, full_res)

    def export_still(self):
        """Save the currently previewed "after" result as a real still
        image file, at the ORIGINAL image's full resolution -- not the
        downsampled preview. Same source-of-truth as _render_after (the
        live stack, via get_stack()) so what gets saved always matches
        what's on screen, just at full quality instead of preview
        quality."""
        if not self.has_sample_image():
            QMessageBox.warning(self, "No image", "Import a sample image first.")
            return

        stack = self._stack_getter() if self._stack_getter else None
        if stack is None or not stack.stages:
            QMessageBox.warning(
                self, "Empty stack",
                "Add stages to the Stack first.")
            return

        default_name = Path(self._current_path).stem + "_graded.png"
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Export Still", default_name,
            "PNG (*.png);;TIFF (*.tif *.tiff);;JPEG (*.jpg *.jpeg)",
        )
        if not out_path:
            return

        try:
            result = self.render_full_resolution_after(stack)
        except Exception as e:  # noqa: BLE001 -- surface any decode/apply failure to the user
            QMessageBox.critical(self, "Export failed", f"Couldn't process the image: {e}")
            return

        try:
            export_image_array_to_file(result, out_path)
        except Exception as e:  # noqa: BLE001 -- surface any write failure to the user
            QMessageBox.critical(self, "Export failed", f"Couldn't save the file: {e}")
            return

        QMessageBox.information(
            self, "Exported",
            f"Saved {result.shape[1]}x{result.shape[0]} still to:\n{out_path}")

    def clear(self):
        """No longer exposed in the UI (see restore_default_image/the
        "Load Default Sample" menu action, which replaced the old Clear
        button) -- kept as a public method since clearing to nothing is still a
        reasonable thing for other code (e.g. a future session-load) to
        want, just not a standalone user-facing action anymore."""
        self._current_path = None
        self._original_array = None
        self._image_array = None
        self._source_colourspace = None
        self._adapt_enabled = False
        self._adapt_target_colourspace = None
        self.status_label.setText("")
        self.compare.set_color_readout_text("")
        self.compare.clear()
        self._set_current_after_array(None)

    @property
    def current_path(self):
        return self._current_path

    # ---- session save/load (see session_io.py) -----------------------

    def get_session_state(self):
        """Plain JSON-able dict describing the currently loaded Sample and
        Reference images + their adaptation state, or None if Sample isn't
        loaded (Reference-only, no Sample, isn't a state this app's
        startup flow produces) -- used by main_window.py's Save Session.
        Deliberately just paths + tags, never pixel data -- same "how to
        rebuild it, not the built thing" principle as LUT stage provenance
        in stack_panel.py. Reference's path (see image_library.py's
        docstring on the Image-Library-vs-LUT/CDL-Library difference)
        genuinely can go missing across a save/reload if its source file
        has since moved -- unlike Sample, there's no bundled default to
        gracefully fall back to, so a missing Reference on load just stays
        empty (with a warning), not a hard failure for the rest of the
        session. Wipe enable/side-selection state is deliberately NOT
        included here -- same "session is project state, not screen
        layout" call this module's docstring already makes for window
        size/panel visibility; it resets to Enable Wipe / Sample Before /
        Sample After on every session load."""
        if self._current_path is None:
            return None
        state = {
            "path": self._current_path,
            "source_colourspace": self._source_colourspace,
            "adapt_enabled": self._adapt_enabled,
            "adapt_target_colourspace": self._adapt_target_colourspace,
        }
        if self._reference_current_path is not None:
            state["reference"] = {
                "path": self._reference_current_path,
                "source_colourspace": self._reference_source_colourspace,
                "adapt_enabled": self._reference_adapt_enabled,
                "adapt_target_colourspace": self._reference_adapt_target_colourspace,
            }
        return state

    def load_session_state(self, state):
        """Restore a saved Sample (and, if present, Reference) state
        exactly -- unlike load_image/load_default_image, does NOT reset
        adaptation to Unmanaged; whatever was saved (including an active
        Adapt conversion) is what gets restored. Falls back to the
        bundled default image (with a warning) if the saved Sample path
        no longer exists, same "don't let a missing reference wreck the
        rest of the session" treatment session_io.py uses for LUT stages.
        A missing Reference path degrades more simply -- there's no
        bundled default for it, so it's just left empty, with a warning.
        No-ops on state=None (a session saved with no sample image
        loaded)."""
        if not state or not state.get("path"):
            return
        path = state["path"]
        if not Path(path).exists():
            QMessageBox.warning(
                self, "Sample image missing",
                f"Couldn't find the session's sample image at:\n{path}\n\n"
                "Falling back to the default reference image.")
            self.restore_default_image()
            return
        if not self._load_image_core(path):
            return
        self._source_colourspace = state.get("source_colourspace")
        self._adapt_enabled = bool(state.get("adapt_enabled", False))
        self._adapt_target_colourspace = state.get("adapt_target_colourspace")
        self._recompute_working_array()

        reference_state = state.get("reference")
        if reference_state and reference_state.get("path"):
            ref_path = reference_state["path"]
            if not Path(ref_path).exists():
                QMessageBox.warning(
                    self, "Reference image missing",
                    f"Couldn't find the session's reference image at:\n{ref_path}\n\n"
                    "Leaving Reference empty.")
            elif self._load_reference_image_core(ref_path):
                self._reference_source_colourspace = reference_state.get("source_colourspace")
                self._reference_adapt_enabled = bool(reference_state.get("adapt_enabled", False))
                self._reference_adapt_target_colourspace = reference_state.get("adapt_target_colourspace")
                self._recompute_reference_working_array()

        self._recompute_sample_after()
        self._render_wipe()
